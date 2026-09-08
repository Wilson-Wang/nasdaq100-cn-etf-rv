from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


PRICE_FRESH_BDAYS = 0
NAV_FRESH_BDAYS = 2
SNAPSHOT_FRESH_BDAYS = 0
PCF_FRESH_BDAYS = 0
CHINA_TZ = ZoneInfo("Asia/Shanghai")
DAILY_DATA_READY_TIME_CN = time(15, 30)


def _read(path: str | Path | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame()
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


def _scalar_bool(value: object) -> bool | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    return None


def _observed_timestamp(observed_at_utc: str | None) -> pd.Timestamp:
    observed = pd.Timestamp(observed_at_utc or datetime.now(timezone.utc).isoformat())
    if observed.tzinfo is None:
        observed = observed.tz_localize("UTC")
    return observed.tz_convert("UTC")


def _information_cutoff(as_of_date: str, observed_at_utc: str | None) -> pd.Timestamp:
    """Return the latest timestamp information may use without lookahead."""
    requested_eod_cn = (
        pd.Timestamp(as_of_date)
        .tz_localize(CHINA_TZ)
        .replace(hour=23, minute=59, second=59)
        .tz_convert("UTC")
    )
    return min(requested_eod_cn, _observed_timestamp(observed_at_utc))


def _previous_weekday(value: str) -> str:
    day = pd.Timestamp(value).normalize()
    previous = day - pd.offsets.BDay(1)
    return previous.strftime("%Y-%m-%d")


def _effective_market_date(as_of_date: str, observed_at_utc: str | None) -> str:
    """Return the latest weekday whose daily market data should be complete."""
    requested = pd.Timestamp(as_of_date).date()
    observed_cn = _observed_timestamp(observed_at_utc).tz_convert(CHINA_TZ)

    effective = requested
    while effective.weekday() >= 5:
        effective = (pd.Timestamp(effective) - pd.offsets.BDay(1)).date()

    if (
        requested == observed_cn.date()
        and requested.weekday() < 5
        and observed_cn.time().replace(tzinfo=None) < DAILY_DATA_READY_TIME_CN
    ):
        effective = pd.Timestamp(_previous_weekday(as_of_date)).date()

    return effective.isoformat()


def _business_day_lag(last_date: str | None, as_of_date: str) -> int | None:
    if not last_date:
        return None
    last = pd.Timestamp(last_date).normalize()
    as_of = pd.Timestamp(as_of_date).normalize()
    if last >= as_of:
        return 0
    start = last + pd.Timedelta(days=1)
    return int(len(pd.bdate_range(start, as_of)))


def _on_or_before(group: pd.DataFrame, column: str, cutoff_date: str) -> pd.DataFrame:
    if group.empty or column not in group.columns:
        return group.copy()
    dates = pd.to_datetime(group[column], errors="coerce")
    cutoff = pd.Timestamp(cutoff_date).normalize()
    return group.loc[dates.notna() & dates.le(cutoff)].copy()


def _available_by(group: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Return rows whose PIT availability is established by the cutoff.

    Legacy exact-PIT fixtures may contain `pit_verified=true` without an
    `available_at` column. Those rows remain accepted for backward compatibility;
    production conservative-PIT rows must carry an explicit timestamp.
    """
    if group.empty:
        return group.copy()
    if "available_at" not in group.columns:
        if "pit_verified" not in group.columns:
            return group.iloc[0:0].copy()
        return group.loc[_normalize_bool(group["pit_verified"])].copy()

    available = pd.to_datetime(group["available_at"], errors="coerce", utc=True)
    usable = (
        _normalize_bool(group["pit_usable"])
        if "pit_usable" in group.columns
        else available.notna()
    )
    return group.loc[usable & available.notna() & available.le(cutoff)].copy()


def _latest_date(group: pd.DataFrame, column: str) -> str | None:
    if group.empty or column not in group.columns:
        return None
    dates = pd.to_datetime(group[column], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.max().strftime("%Y-%m-%d")


def _latest_row(group: pd.DataFrame, date_column: str) -> pd.Series | None:
    if group.empty or date_column not in group.columns:
        return None
    dates = pd.to_datetime(group[date_column], errors="coerce")
    if not dates.notna().any():
        return None
    return group.loc[dates.idxmax()]


def _latest_exact_pit_verified(group: pd.DataFrame, date_column: str) -> bool:
    row = _latest_row(group, date_column)
    if row is None:
        return False
    return bool(_scalar_bool(row.get("pit_verified")))


def _usable_price_count(group: pd.DataFrame) -> int:
    if group.empty:
        return 0
    if "is_tradable" in group.columns:
        return int(_normalize_bool(group["is_tradable"]).sum())
    return int(len(group))


def _source_degraded(symbol: str, failures: list[str]) -> bool:
    symbol_prefix = f"{symbol} "
    return any(message.startswith(symbol_prefix) for message in failures)


def _restriction_state(value: object, restriction_terms: tuple[str, ...]) -> str:
    if value is None or pd.isna(value):
        return "UNKNOWN"
    text = str(value).strip()
    if not text:
        return "UNKNOWN"
    if any(term in text for term in restriction_terms):
        return "RESTRICTED"
    return "UNKNOWN"


def _nav_primary_market_fallback(group: pd.DataFrame) -> dict:
    row = _latest_row(group, "nav_date")
    if row is None:
        return {
            "source": "nav_status_fallback",
            "as_of_date": None,
            "creation_state": "UNKNOWN",
            "redemption_state": "UNKNOWN",
            "subscription_status_raw": None,
            "redemption_status_raw": None,
            "confidence": "LOW",
        }

    subscription_raw = row.get("subscription_status")
    redemption_raw = row.get("redemption_status")
    creation_state = _restriction_state(
        subscription_raw,
        ("暂停申购", "限制申购", "暂停大额申购", "限额申购"),
    )
    redemption_state = _restriction_state(
        redemption_raw,
        ("暂停赎回", "限制赎回", "暂停大额赎回", "限额赎回"),
    )
    return {
        "source": "nav_status_fallback",
        "as_of_date": _latest_date(group, "nav_date"),
        "creation_state": creation_state,
        "redemption_state": redemption_state,
        "subscription_status_raw": None if pd.isna(subscription_raw) else str(subscription_raw),
        "redemption_status_raw": None if pd.isna(redemption_raw) else str(redemption_raw),
        "confidence": "LOW",
    }


def _pcf_primary_market(group: pd.DataFrame) -> dict | None:
    row = _latest_row(group, "date")
    if row is None or not bool(_scalar_bool(row.get("pit_verified"))):
        return None

    creation_allowed = _scalar_bool(row.get("creation_allowed"))
    redemption_allowed = _scalar_bool(row.get("redemption_allowed"))

    def state(value: bool | None) -> str:
        if value is True:
            return "OPEN"
        if value is False:
            return "RESTRICTED"
        return "UNKNOWN"

    def clean(value: object) -> object:
        return None if value is None or pd.isna(value) else value

    return {
        "source": "official_pcf",
        "source_detail": clean(row.get("source")),
        "as_of_date": clean(row.get("date")),
        "creation_state": state(creation_allowed),
        "redemption_state": state(redemption_allowed),
        "creation_allowed": creation_allowed,
        "redemption_allowed": redemption_allowed,
        "creation_unit": clean(row.get("creation_unit")),
        "creation_limit": clean(row.get("creation_limit")),
        "redemption_limit": clean(row.get("redemption_limit")),
        "net_creation_limit": clean(row.get("net_creation_limit")),
        "net_redemption_limit": clean(row.get("net_redemption_limit")),
        "cash_substitution_limit_pct": clean(row.get("cash_substitution_limit_pct")),
        "max_creation_cash_premium_pct": clean(row.get("max_creation_cash_premium_pct")),
        "max_redemption_cash_discount_pct": clean(row.get("max_redemption_cash_discount_pct")),
        "available_at": clean(row.get("available_at")),
        "availability_method": clean(row.get("availability_method")),
        "confidence": "HIGH",
    }


def build_quality_report(
    prices_path: str | Path,
    nav_path: str | Path,
    snapshot_path: str | Path,
    universe_symbols: list[str],
    as_of_date: str,
    failures: list[str],
    observed_at_utc: str | None = None,
    pcf_path: str | Path | None = None,
) -> dict:
    """Build point-in-time signal readiness separately from file validity."""
    prices = _read(prices_path)
    nav = _read(nav_path)
    snapshot = _read(snapshot_path)
    pcf = _read(pcf_path)
    model_as_of_date = _effective_market_date(as_of_date, observed_at_utc)
    information_cutoff = _information_cutoff(as_of_date, observed_at_utc)

    global_snapshot_failure = any(message.startswith("snapshot ") for message in failures)
    by_symbol: dict[str, dict] = {}

    for symbol in universe_symbols:
        price_all = (
            prices[prices["symbol"] == symbol]
            if "symbol" in prices.columns
            else pd.DataFrame()
        )
        nav_all = nav[nav["symbol"] == symbol] if "symbol" in nav.columns else pd.DataFrame()
        snapshot_all = (
            snapshot[snapshot["symbol"] == symbol]
            if "symbol" in snapshot.columns
            else pd.DataFrame()
        )
        pcf_all = pcf[pcf["symbol"] == symbol] if "symbol" in pcf.columns else pd.DataFrame()

        price_group = _on_or_before(price_all, "date", model_as_of_date)
        nav_group = _on_or_before(nav_all, "nav_date", model_as_of_date)
        snapshot_group = _on_or_before(snapshot_all, "data_date", model_as_of_date)
        pcf_dated = _on_or_before(pcf_all, "date", as_of_date)
        pcf_available = _available_by(pcf_dated, information_cutoff)
        nav_available = _available_by(nav_group, information_cutoff)

        price_last = _latest_date(price_group, "date")
        raw_nav_last = _latest_date(nav_group, "nav_date")
        pit_nav_last = _latest_date(nav_available, "nav_date")
        snapshot_last = _latest_date(snapshot_group, "data_date")
        pcf_last_observed = _latest_date(pcf_dated, "date")
        pcf_last_available = _latest_date(pcf_available, "date")

        price_lag = _business_day_lag(price_last, model_as_of_date)
        raw_nav_lag = _business_day_lag(raw_nav_last, model_as_of_date)
        pit_nav_lag = _business_day_lag(pit_nav_last, model_as_of_date)
        snapshot_lag = _business_day_lag(snapshot_last, model_as_of_date)
        pcf_lag = _business_day_lag(pcf_last_available, as_of_date)
        usable_prices = _usable_price_count(price_group)
        pit_usable = pit_nav_last is not None
        pit_verified = _latest_exact_pit_verified(nav_available, "nav_date")
        source_degraded = _source_degraded(symbol, failures) or global_snapshot_failure

        current_pcf = (
            _pcf_primary_market(pcf_available)
            if pcf_lag is not None and pcf_lag <= PCF_FRESH_BDAYS
            else None
        )
        # NAV-page status is only a low-confidence fallback. It is intentionally
        # separate from strict NAV PIT eligibility and must never prove a formal signal.
        primary_market = current_pcf or _nav_primary_market_fallback(nav_group)

        stale_price = price_lag is None or price_lag > PRICE_FRESH_BDAYS
        # NAV freshness and NAV point-in-time usability are separate concepts.
        # A recent NAV can be fresh yet still unavailable for strict historical use.
        stale_nav = raw_nav_lag is None or raw_nav_lag > NAV_FRESH_BDAYS
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
        if not pit_usable:
            states.append("PIT_UNAVAILABLE")
        elif not pit_verified:
            states.append("PIT_CONSERVATIVE")

        if pcf_last_observed is None:
            states.append("PCF_MISSING")
        elif pcf_last_available is None:
            states.append("PCF_NOT_YET_AVAILABLE")
        elif pcf_lag is None or pcf_lag > PCF_FRESH_BDAYS:
            states.append("STALE_PCF")

        if source_degraded:
            states.append("SOURCE_DEGRADED")
        if critical_source_failure:
            states.append("CRITICAL_SOURCE_FAILURE")

        if current_pcf is not None:
            if primary_market["creation_state"] == "RESTRICTED":
                states.append("CREATION_RESTRICTED_BY_PCF")
            if primary_market["redemption_state"] == "RESTRICTED":
                states.append("REDEMPTION_RESTRICTED_BY_PCF")
        else:
            if primary_market["creation_state"] == "RESTRICTED":
                states.append("CREATION_RESTRICTED_BY_NAV_STATUS")
            if primary_market["redemption_state"] == "RESTRICTED":
                states.append("REDEMPTION_RESTRICTED_BY_NAV_STATUS")
            if (
                primary_market["creation_state"] == "UNKNOWN"
                and primary_market["redemption_state"] == "UNKNOWN"
            ):
                states.append("PRIMARY_MARKET_STATUS_LOW_CONFIDENCE")

        formal_signal_ready = (
            usable_prices >= 120
            and not stale_price
            and not stale_nav
            and not stale_snapshot
            and pit_usable
            and not critical_source_failure
        )

        by_symbol[symbol] = {
            "usable_price_observations": usable_prices,
            "price_last_date": price_last,
            "price_lag_business_days": price_lag,
            # Backward-compatible aliases retain raw NAV freshness semantics.
            "nav_last_date": raw_nav_last,
            "nav_lag_business_days": raw_nav_lag,
            "raw_nav_last_date": raw_nav_last,
            "raw_nav_lag_business_days": raw_nav_lag,
            "pit_nav_last_date": pit_nav_last,
            "pit_nav_lag_business_days": pit_nav_lag,
            "snapshot_last_date": snapshot_last,
            "snapshot_lag_business_days": snapshot_lag,
            "pcf_last_observed_date": pcf_last_observed,
            "pcf_last_available_date": pcf_last_available,
            "pcf_lag_business_days": pcf_lag,
            "pcf_current_verified": current_pcf is not None,
            "pit_usable": pit_usable,
            "pit_verified": pit_verified,
            "source_degraded": source_degraded,
            "critical_source_failure": critical_source_failure,
            "primary_market": primary_market,
            "states": states or ["FRESH"],
            "formal_signal_ready": formal_signal_ready,
        }

    return {
        "as_of_date": as_of_date,
        "model_as_of_date": model_as_of_date,
        "information_cutoff_utc": information_cutoff.isoformat(),
        "observed_at_utc": observed_at_utc,
        "freshness_method": "weekday_phase_heuristic_with_availability_cutoff",
        "daily_data_ready_time_cn": DAILY_DATA_READY_TIME_CN.strftime("%H:%M"),
        "thresholds_business_days": {
            "price": PRICE_FRESH_BDAYS,
            "nav": NAV_FRESH_BDAYS,
            "nav_pit_usable": NAV_FRESH_BDAYS,
            "snapshot": SNAPSHOT_FRESH_BDAYS,
            "pcf": PCF_FRESH_BDAYS,
        },
        "nav_pit_method": "verified_timestamp_or_conservative_available_by_bound",
        "primary_market_method": "available_official_pcf_then_low_confidence_nav_status",
        "formal_signal_ready_symbols": [
            symbol for symbol, item in by_symbol.items() if item["formal_signal_ready"]
        ],
        "by_symbol": by_symbol,
    }
