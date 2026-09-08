from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd


def _status_rank(status: str) -> int:
    return {"OK": 0, "WARN": 1, "ERROR": 2}.get(status, 1)


def build_source_health(
    source_runs: pd.DataFrame,
    manifest: dict[str, Any],
    *,
    consecutive_window: int = 3,
) -> dict[str, Any]:
    """Summarize source reliability without treating a successful fallback as failure."""
    alerts: list[dict[str, Any]] = []
    if not source_runs.empty:
        frame = source_runs.copy()
        frame["finished_at_utc"] = pd.to_datetime(
            frame.get("finished_at_utc"), errors="coerce", utc=True
        )
        frame = frame.sort_values("finished_at_utc")
        group_cols = [column for column in ("resource", "symbol") if column in frame.columns]
        if group_cols and "status" in frame.columns:
            for keys, group in frame.groupby(group_cols, dropna=False, sort=False):
                recent = group.tail(consecutive_window)
                statuses = recent["status"].astype("string").tolist()
                if len(recent) >= consecutive_window and all(
                    status in {"FAILED", "DEGRADED"} for status in statuses
                ):
                    if not isinstance(keys, tuple):
                        keys = (keys,)
                    identity = dict(zip(group_cols, keys))
                    alerts.append(
                        {
                            "severity": "WARN",
                            "code": "REPEATED_SOURCE_DEGRADATION",
                            **{key: None if pd.isna(value) else str(value) for key, value in identity.items()},
                            "recent_statuses": statuses,
                        }
                    )

    quality = manifest.get("quality", {})
    by_symbol = quality.get("by_symbol", {})
    blocked = [
        symbol
        for symbol, item in by_symbol.items()
        if item.get("formal_signal_ready") is not True
    ]
    if blocked:
        alerts.append(
            {
                "severity": "ERROR",
                "code": "MODEL_READINESS_BLOCKED",
                "symbols": sorted(blocked),
            }
        )

    failures = manifest.get("failures", [])
    if failures:
        alerts.append(
            {
                "severity": "WARN",
                "code": "CURRENT_RUN_FAILURES",
                "count": len(failures),
            }
        )

    warning_count = len(manifest.get("warnings", []))
    if warning_count:
        alerts.append(
            {
                "severity": "WARN",
                "code": "CURRENT_DATA_WARNINGS",
                "count": warning_count,
            }
        )

    overall = "OK"
    if alerts:
        overall = max(
            (str(item["severity"]) for item in alerts), key=_status_rank
        )
    counts = Counter(str(item["severity"]) for item in alerts)
    return {
        "status": overall,
        "alert_counts": dict(counts),
        "alerts": alerts,
        "policy": {
            "repeated_source_window": consecutive_window,
            "fallback_success_is_not_automatically_critical": True,
            "model_readiness_block_is_error": True,
        },
    }


def read_source_runs(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists() or not path.stat().st_size:
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"symbol": "string", "run_id": "string"})
