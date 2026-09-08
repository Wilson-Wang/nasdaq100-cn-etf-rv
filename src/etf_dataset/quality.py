from __future__ import annotations

from pathlib import Path

import pandas as pd


PRICE_FRESH_BDAYS = 0
NAV_FRESH_BDAYS = 2
SNAPSHOT_FRESH_BDAYS = 0


def _read(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"symbol": "string"})


def _normalize_bool(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
        .map({"true": True, "false": False, "1": True, "0": False})
        .fillna(False)
        .astype(bool)
    )


def _business_day_lag(last_date: str | None, as_of_date: str) -> int | None:
    if not last_date:
        return None
    last = pd.Timestamp(last_date).normalize()
    as_of = pd.Timestamp(as_of_date).normalize()
    if last >= as_of:
        return 0
    start = last + pd.Timedelta(days=1)
    return int(len(pd.bdate_range(start, as_of)))


def _latest_date(group: pd.DataFrame, column: str) -> str | None:
    if group.empty or column not in group.columns:
        return None
    dates = pd.to_datetime(group[column], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.max().strftime("%Y-%m-%d")


def _latest_pit_verified(group: pd.DataFrame) -> bool:
    if group.empty or "pit_verified" not in group.columns:
        return False
    dates = pd.to_datetime(group.get("nav_date"), errors="coerce")
    if dates.notna().any():
        latest_index = dates.idxmax()
        value = pd.Series([group.loc[latest_index, "pit_verified"]])
    else:
        value = group["pit_verified"].tail(1)
    return bool(_normalize_bool(value).iloc[0]) if not value.empty else False


def _usable_price_count(group: pd.DataFrame) -> int:
    if group.empty:
        return 0
    if "is_tradable" in group.columns:
        return int(_normalize_bool(group["is_tradable"]).sum())
    return int(len(group))


def _source_degraded(symbol: str, failures: list[str]) -> bool:
    symbol_prefix = f"{symbol} "
    return any(message.startswith(symbol_prefix) for message in failures)


def build_quality_report(
    prices_path: str | Path,
    nav_path: str | Path,
    snapshot_path: str | Path,
    universe_symbols: list[str],
    as_of_date: str,
    failures: list[str],
) -> dict:
    """Build signal-readiness separately from structural file validation.

    Freshness uses weekday lag as a deterministic conservative precheck. It is
    explicitly a heuristic until an exchange trading calendar is wired in.
    A source failure is recorded as degraded, but becomes critical only when
    the resulting current input is stale or absent.
    """
    prices = _read(prices_path)
    nav = _read(nav_path)
    snapshot = _read(snapshot_path)

    global_snapshot_failure = any(message.startswith("snapshot ") for message in failures)
    by_symbol: dict[str, dict] = {}

    for symbol in universe_symbols:
        price_group = prices[prices["symbol"] == symbol] if "symbol" in prices.columns else pd.DataFrame()
        nav_group = nav[nav["symbol"] == symbol] if "symbol" in nav.columns else pd.DataFrame()
        snapshot_group = (
            snapshot[snapshot["symbol"] == symbol]
            if "symbol" in snapshot.columns
            else pd.DataFrame()
        )

        price_last = _latest_date(price_group, "date")
        nav_last = _latest_date(nav_group, "nav_date")
        snapshot_last = _latest_date(snapshot_group, "data_date")

        price_lag = _business_day_lag(price_last, as_of_date)
        nav_lag = _business_day_lag(nav_last, as_of_date)
        snapshot_lag = _business_day_lag(snapshot_last, as_of_date)
        usable_prices = _usable_price_count(price_group)
        pit_verified = _latest_pit_verified(nav_group)
        source_degraded = _source_degraded(symbol, failures) or global_snapshot_failure

        stale_price = price_lag is None or price_lag > PRICE_FRESH_BDAYS
        stale_nav = nav_lag is None or nav_lag > NAV_FRESH_BDAYS
        stale_snapshot = snapshot_lag is None or snapshot_lag > SNAPSHOT_FRESH_BDAYS
        critical_source_failure = source_degraded and (stale_price or stale_nav or stale_snapshot)

        states: list[str] = []
        if usable_prices < 60:
            states.append("INSUFFICIENT_DATA")
        elif usable_prices < 120:
            states.append("SHORT_WINDOW_ONLY")
        elif usable_prices < 250:
            states.append("LIMITED_RESEARCH_DEPTH")

        if stale_price:
            states.append("STALE_PRICE")
        if stale_nav:
            states.append("STALE_NAV")
        if stale_snapshot:
            states.append("STALE_SNAPSHOT")
        if not pit_verified:
            states.append("PIT_UNVERIFIED")
        if source_degraded:
            states.append("SOURCE_DEGRADED")
        if critical_source_failure:
            states.append("CRITICAL_SOURCE_FAILURE")

        formal_signal_ready = (
            usable_prices >= 120
            and not stale_price
            and not stale_nav
            and not stale_snapshot
            and pit_verified
            and not critical_source_failure
        )

        by_symbol[symbol] = {
            "usable_price_observations": usable_prices,
            "price_last_date": price_last,
            "price_lag_business_days": price_lag,
            "nav_last_date": nav_last,
            "nav_lag_business_days": nav_lag,
            "snapshot_last_date": snapshot_last,
            "snapshot_lag_business_days": snapshot_lag,
            "pit_verified": pit_verified,
            "source_degraded": source_degraded,
            "critical_source_failure": critical_source_failure,
            "states": states or ["FRESH"],
            "formal_signal_ready": formal_signal_ready,
        }

    return {
        "as_of_date": as_of_date,
        "freshness_method": "weekday_heuristic",
        "thresholds_business_days": {
            "price": PRICE_FRESH_BDAYS,
            "nav": NAV_FRESH_BDAYS,
            "snapshot": SNAPSHOT_FRESH_BDAYS,
        },
        "formal_signal_ready_symbols": [
            symbol for symbol, item in by_symbol.items() if item["formal_signal_ready"]
        ],
        "by_symbol": by_symbol,
    }
