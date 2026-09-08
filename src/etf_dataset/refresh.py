from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pandas as pd


DEFAULT_OVERLAP_DAYS = 14


def read_existing(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"symbol": "string", "factor_name": "string"})


def _bool_series(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
        .map({"true": True, "false": False, "1": True, "0": False})
        .fillna(False)
        .astype(bool)
    )


def plan_incremental_start(
    frame: pd.DataFrame,
    *,
    date_column: str,
    requested_start: str,
    filter_column: str | None = None,
    filter_value: str | None = None,
    minimum_observations: int = 120,
    require_requested_start_coverage: bool = True,
    tradable_column: str | None = None,
    overlap_days: int = DEFAULT_OVERLAP_DAYS,
) -> tuple[str, str]:
    """Return `(fetch_start, mode)` for a safe incremental refresh.

    Full-history fetches continue until the stored dataset has enough usable
    observations and, when requested, reaches close to the requested start.
    Once those gates pass, a calendar-day overlap is re-fetched so late source
    corrections can still replace changed business values.
    """
    if frame.empty or date_column not in frame.columns:
        return requested_start, "FULL_NO_EXISTING_DATA"

    group = frame.copy()
    if filter_column is not None:
        if filter_column not in group.columns:
            return requested_start, "FULL_FILTER_COLUMN_MISSING"
        group = group[group[filter_column].astype("string") == str(filter_value)].copy()
    if group.empty:
        return requested_start, "FULL_NO_MATCHING_DATA"

    dates = pd.to_datetime(group[date_column], errors="coerce")
    valid = dates.notna()
    group = group.loc[valid].copy()
    dates = dates.loc[valid]
    if group.empty:
        return requested_start, "FULL_NO_VALID_DATES"

    if tradable_column and tradable_column in group.columns:
        usable_observations = int(_bool_series(group[tradable_column]).sum())
    else:
        usable_observations = int(len(group))
    if usable_observations < minimum_observations:
        return requested_start, f"FULL_DEPTH_{usable_observations}_LT_{minimum_observations}"

    minimum_date = dates.min().normalize()
    requested = pd.Timestamp(requested_start).normalize()
    if require_requested_start_coverage and minimum_date > requested + timedelta(days=31):
        return requested_start, "FULL_START_COVERAGE_GAP"

    latest_date = dates.max().normalize()
    incremental = max(requested, latest_date - timedelta(days=overlap_days))
    return incremental.strftime("%Y-%m-%d"), "INCREMENTAL"
