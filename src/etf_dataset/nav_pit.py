from __future__ import annotations

from bisect import bisect_right
from pathlib import Path

import pandas as pd


QDII_NAV_AVAILABILITY_METHOD = "qdii_regulatory_tplus2_exchange_workdays_eod"
FIRST_SEEN_METHOD = "first_seen_ingestion_bound"
COMBINED_AVAILABILITY_METHOD = "earliest_safe_bound:first_seen_or_qdii_tplus2"
PENDING_AVAILABILITY_METHOD = "pending_qdii_tplus2_exchange_workdays"


def observed_exchange_days(
    prices_path: str | Path,
    extra_prices: pd.DataFrame | None = None,
) -> list[pd.Timestamp]:
    """Build an observed mainland exchange-session calendar from ETF prices."""
    frames: list[pd.DataFrame] = []
    path = Path(prices_path)
    if path.exists() and path.stat().st_size:
        frames.append(pd.read_csv(path, dtype={"symbol": "string"}))
    if extra_prices is not None and not extra_prices.empty:
        frames.append(extra_prices)
    if not frames:
        return []

    combined = pd.concat(frames, ignore_index=True, sort=False)
    if "date" not in combined.columns:
        return []
    dates = pd.to_datetime(combined["date"], errors="coerce").dropna().dt.normalize()
    return sorted(pd.Timestamp(value) for value in dates.unique())


def _regulatory_available_at(
    nav_date: object,
    exchange_days: list[pd.Timestamp],
) -> pd.Timestamp | None:
    value = pd.to_datetime(nav_date, errors="coerce")
    if pd.isna(value):
        return None
    normalized = pd.Timestamp(value).normalize()
    position = bisect_right(exchange_days, normalized)
    second_following_index = position + 1
    if second_following_index >= len(exchange_days):
        return None
    deadline_day = exchange_days[second_following_index]
    return deadline_day.tz_localize("Asia/Shanghai").replace(hour=23, minute=59, second=59)


def _first_seen_available_at(row: pd.Series) -> pd.Timestamp | None:
    value = pd.to_datetime(row.get("ingested_at_utc"), errors="coerce", utc=True)
    if pd.isna(value):
        return None
    nav_date = pd.to_datetime(row.get("nav_date"), errors="coerce")
    if pd.isna(nav_date):
        return None
    # A successful fetch proves only that the value was available by fetch time.
    # It remains an upper bound, not an exact publication timestamp.
    return pd.Timestamp(value).tz_convert("Asia/Shanghai")


def _safe_availability_bound(
    row: pd.Series,
    exchange_days: list[pd.Timestamp],
) -> tuple[pd.Timestamp | None, str]:
    regulatory = _regulatory_available_at(row.get("nav_date"), exchange_days)
    first_seen = _first_seen_available_at(row)
    candidates = [value for value in (regulatory, first_seen) if value is not None]
    if not candidates:
        return None, PENDING_AVAILABILITY_METHOD

    chosen = min(candidates)
    if regulatory is not None and first_seen is not None:
        return chosen, COMBINED_AVAILABILITY_METHOD
    if first_seen is not None:
        return chosen, FIRST_SEEN_METHOD
    return chosen, QDII_NAV_AVAILABILITY_METHOD


def enrich_nav_pit(
    nav: pd.DataFrame,
    exchange_days: list[pd.Timestamp],
) -> pd.DataFrame:
    """Attach a conservative, non-fabricated availability bound to NAV rows.

    Exact historical publication timestamps remain unverified. A row becomes
    point-in-time usable once either (a) the dataset actually observed it, or
    (b) the QDII regulatory T+2 exchange-workday disclosure deadline is known.
    The earlier of those two safe upper bounds is recorded. `pit_verified`
    remains false unless a future source supplies an exact publication time.
    """
    if nav.empty:
        return nav.copy()

    result = nav.copy()
    defaults: dict[str, object] = {
        "published_at": pd.NA,
        "available_at": pd.NA,
        "availability_source": PENDING_AVAILABILITY_METHOD,
        "availability_verified": False,
        "pit_verified": False,
        "pit_usable": False,
    }
    for column, value in defaults.items():
        if column not in result.columns:
            result[column] = value

    verified = (
        result["pit_verified"]
        .astype("string")
        .str.strip()
        .str.lower()
        .map({"true": True, "1": True, "false": False, "0": False})
        .fillna(False)
    )

    for index, row in result.loc[~verified].iterrows():
        available_at, method = _safe_availability_bound(row, exchange_days)
        if available_at is None:
            result.at[index, "available_at"] = pd.NA
            result.at[index, "availability_source"] = method
            result.at[index, "availability_verified"] = False
            result.at[index, "pit_verified"] = False
            result.at[index, "pit_usable"] = False
            continue

        result.at[index, "available_at"] = available_at.isoformat()
        result.at[index, "availability_source"] = method
        result.at[index, "availability_verified"] = False
        result.at[index, "pit_verified"] = False
        result.at[index, "pit_usable"] = True

    result.loc[verified, "pit_usable"] = True
    return result


def enrich_nav_file(
    nav_path: str | Path,
    exchange_days: list[pd.Timestamp],
) -> pd.DataFrame:
    path = Path(nav_path)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    frame = pd.read_csv(path, dtype={"symbol": "string"})
    enriched = enrich_nav_pit(frame, exchange_days)
    enriched.to_csv(path, index=False)
    return enriched
