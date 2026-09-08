from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


SOURCE_RUN_COLUMNS = [
    "run_id",
    "resource",
    "symbol",
    "requested_start",
    "requested_end",
    "started_at_utc",
    "finished_at_utc",
    "duration_seconds",
    "status",
    "row_count",
    "min_date",
    "max_date",
    "sources",
    "error",
    "source_priority",
    "ingested_at_utc",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_run_id(now: datetime | None = None) -> str:
    value = now or utc_now()
    return value.strftime("%Y%m%dT%H%M%S%fZ")


def build_source_run_record(
    *,
    run_id: str,
    resource: str,
    symbol: str,
    requested_start: str | None,
    requested_end: str | None,
    started_at: datetime,
    finished_at: datetime,
    frame: pd.DataFrame,
    date_column: str | None,
    errors: list[str] | None = None,
) -> dict[str, object]:
    errors = errors or []
    rows = int(len(frame)) if frame is not None else 0
    if frame is not None and not frame.empty and date_column and date_column in frame.columns:
        dates = pd.to_datetime(frame[date_column], errors="coerce").dropna()
    else:
        dates = pd.Series(dtype="datetime64[ns]")

    if frame is not None and not frame.empty and "source" in frame.columns:
        sources = sorted(frame["source"].dropna().astype(str).unique().tolist())
    else:
        sources = []

    if rows > 0 and errors:
        status = "DEGRADED"
    elif rows > 0:
        status = "SUCCESS"
    else:
        status = "FAILED"

    finished_iso = finished_at.replace(microsecond=0).isoformat()
    return {
        "run_id": run_id,
        "resource": resource,
        "symbol": symbol,
        "requested_start": requested_start,
        "requested_end": requested_end,
        "started_at_utc": started_at.replace(microsecond=0).isoformat(),
        "finished_at_utc": finished_iso,
        "duration_seconds": round(max((finished_at - started_at).total_seconds(), 0.0), 3),
        "status": status,
        "row_count": rows,
        "min_date": dates.min().strftime("%Y-%m-%d") if not dates.empty else None,
        "max_date": dates.max().strftime("%Y-%m-%d") if not dates.empty else None,
        "sources": ";".join(sources),
        "error": " | ".join(errors) if errors else None,
        "source_priority": 0,
        "ingested_at_utc": finished_iso,
    }


def records_frame(records: list[dict[str, object]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=SOURCE_RUN_COLUMNS)
    return pd.DataFrame(records).reindex(columns=SOURCE_RUN_COLUMNS)


def source_run_summary(path: str | Path, run_id: str) -> dict:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return {"rows": 0, "current_run_rows": 0, "current_run_statuses": {}}
    frame = pd.read_csv(path, dtype={"symbol": "string", "run_id": "string"})
    current = frame[frame["run_id"] == run_id]
    return {
        "rows": int(len(frame)),
        "current_run_rows": int(len(current)),
        "current_run_statuses": current["status"].value_counts(dropna=False).astype(int).to_dict()
        if "status" in current.columns
        else {},
    }
