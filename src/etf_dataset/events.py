from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


EVENT_COLUMNS = [
    "symbol",
    "event_type",
    "published_at",
    "effective_at",
    "title",
    "severity",
    "source",
    "source_url",
    "ingested_at_utc",
]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _bool(value: object) -> bool | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def derive_pcf_events(pcf: pd.DataFrame) -> pd.DataFrame:
    if pcf.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    frame = pcf.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["symbol", "date"]).sort_values(["symbol", "date"])
    rows: list[dict[str, object]] = []
    now = _now()
    watched = [
        "creation_allowed",
        "redemption_allowed",
        "creation_limit",
        "redemption_limit",
        "net_creation_limit",
        "net_redemption_limit",
        "cash_substitution_limit_pct",
        "max_creation_cash_premium_pct",
        "max_redemption_cash_discount_pct",
    ]
    for symbol, group in frame.groupby(frame["symbol"].astype("string"), sort=True):
        previous: pd.Series | None = None
        for _, row in group.iterrows():
            if previous is None:
                previous = row
                continue
            changes: list[str] = []
            for column in watched:
                left = previous.get(column)
                right = row.get(column)
                left_missing = left is None or pd.isna(left)
                right_missing = right is None or pd.isna(right)
                if left_missing and right_missing:
                    continue
                if left_missing != right_missing or str(left) != str(right):
                    changes.append(column)
            if changes:
                creation_before = _bool(previous.get("creation_allowed"))
                creation_after = _bool(row.get("creation_allowed"))
                redemption_before = _bool(previous.get("redemption_allowed"))
                redemption_after = _bool(row.get("redemption_allowed"))
                severity = "MEDIUM"
                if creation_before != creation_after or redemption_before != redemption_after:
                    severity = "HIGH"
                title = "PCF changed: " + ", ".join(changes)
                rows.append(
                    {
                        "symbol": str(symbol),
                        "event_type": "PRIMARY_MARKET_STATUS_CHANGE",
                        "published_at": row.get("available_at"),
                        "effective_at": row["date"].strftime("%Y-%m-%d"),
                        "title": title,
                        "severity": severity,
                        "source": row.get("source"),
                        "source_url": row.get("document_url") or row.get("source_url"),
                        "ingested_at_utc": now,
                    }
                )
            previous = row
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def append_execution_history(
    report: dict,
    history_path: str | Path,
) -> pd.DataFrame:
    path = Path(history_path)
    current_rows: list[dict[str, object]] = []
    observed = _now()
    for symbol, item in report.get("by_symbol", {}).items():
        current_rows.append(
            {
                "symbol": symbol,
                "as_of_date": report.get("as_of_date"),
                "observed_at": item.get("quote_updated_at") or observed,
                "execution_ready": bool(item.get("execution_ready")),
                "next_session_eligible": bool(item.get("next_session_eligible")),
                "states": json.dumps(item.get("states", []), ensure_ascii=False),
            }
        )
    current = pd.DataFrame(current_rows)
    if path.exists() and path.stat().st_size > 0:
        existing = pd.read_csv(path, dtype={"symbol": "string"})
        combined = pd.concat([existing, current], ignore_index=True, sort=False)
    else:
        combined = current
    if not combined.empty:
        combined = combined.drop_duplicates(["symbol", "observed_at"], keep="last")
        combined = combined.sort_values(["symbol", "observed_at"]).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(path, index=False)
    return combined


def derive_execution_events(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    frame = history.copy()
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["symbol", "observed_at"]).sort_values(["symbol", "observed_at"])
    rows: list[dict[str, object]] = []
    now = _now()
    for symbol, group in frame.groupby(frame["symbol"].astype("string"), sort=True):
        prior: bool | None = None
        for _, row in group.iterrows():
            current = _bool(row.get("next_session_eligible"))
            if current is None:
                continue
            if prior is not None and current != prior:
                gained = current is True
                rows.append(
                    {
                        "symbol": str(symbol),
                        "event_type": (
                            "SECONDARY_MARKET_EXECUTION_ELIGIBILITY_GAINED"
                            if gained
                            else "SECONDARY_MARKET_EXECUTION_ELIGIBILITY_LOST"
                        ),
                        "published_at": row["observed_at"].isoformat(),
                        "effective_at": row.get("as_of_date"),
                        "title": "Next-session execution eligibility " + ("gained" if gained else "lost"),
                        "severity": "MEDIUM" if gained else "HIGH",
                        "source": "derived:execution_readiness",
                        "source_url": None,
                        "ingested_at_utc": now,
                    }
                )
            prior = current
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def build_event_table(pcf: pd.DataFrame, execution_history: pd.DataFrame) -> pd.DataFrame:
    parts = [derive_pcf_events(pcf), derive_execution_events(execution_history)]
    nonempty = [part for part in parts if not part.empty]
    if not nonempty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    frame = pd.concat(nonempty, ignore_index=True, sort=False)
    return frame.drop_duplicates(
        ["symbol", "event_type", "published_at", "title"], keep="last"
    ).sort_values(["published_at", "symbol"]).reset_index(drop=True)
