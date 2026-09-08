from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def _read(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"symbol": "string"})


def _positive(value: object) -> bool:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return bool(pd.notna(number) and float(number) > 0)


def _valid_book(row: pd.Series) -> bool:
    bid = pd.to_numeric(pd.Series([row.get("bid1")]), errors="coerce").iloc[0]
    ask = pd.to_numeric(pd.Series([row.get("ask1")]), errors="coerce").iloc[0]
    return bool(pd.notna(bid) and pd.notna(ask) and bid > 0 and ask >= bid)


def _latest_row_on_or_before(
    frame: pd.DataFrame,
    symbol: str,
    as_of_date: str,
) -> pd.Series | None:
    if frame.empty or not {"symbol", "data_date"}.issubset(frame.columns):
        return None
    group = frame.loc[frame["symbol"] == symbol].copy()
    if group.empty:
        return None
    dates = pd.to_datetime(group["data_date"], errors="coerce")
    cutoff = pd.Timestamp(as_of_date).normalize()
    group = group.loc[dates.notna() & dates.le(cutoff)].copy()
    if group.empty:
        return None
    dates = pd.to_datetime(group["data_date"], errors="coerce")
    return group.loc[dates.idxmax()]


def build_execution_readiness(
    snapshot_path: str | Path,
    symbols: list[str],
    as_of_date: str,
) -> dict:
    """Separate intraday executable quotes from next-session EOD eligibility.

    `execution_ready` requires a live same-day best book and is intended for an
    intraday decision. `next_session_eligible` only requires a same-day valid
    last price plus actual trading activity; it is the appropriate precheck for
    an EOD signal whose earliest fill is the next tradable session. The next-day
    order must still re-check a live bid/ask before execution.
    """
    snapshot = _read(snapshot_path)
    by_symbol: dict[str, dict] = {}

    for symbol in symbols:
        row = _latest_row_on_or_before(snapshot, symbol, as_of_date)
        if row is None:
            by_symbol[symbol] = {
                "snapshot_date": None,
                "quote_updated_at": None,
                "has_last": False,
                "has_activity": False,
                "has_valid_book": False,
                "execution_ready": False,
                "next_session_eligible": False,
                "states": ["NO_SNAPSHOT"],
            }
            continue

        snapshot_date = None if pd.isna(row.get("data_date")) else str(row.get("data_date"))
        quote_updated_at = None if pd.isna(row.get("updated_at")) else str(row.get("updated_at"))
        has_last = _positive(row.get("last"))
        has_volume = _positive(row.get("volume"))
        has_amount = _positive(row.get("amount"))
        has_activity = has_volume or has_amount
        has_valid_book = _valid_book(row)
        is_current_date = snapshot_date == as_of_date

        states: list[str] = []
        if not is_current_date:
            states.append("NO_CURRENT_DAY_SNAPSHOT")
        if not has_last:
            states.append("NO_CURRENT_PRICE")
        if not has_activity:
            states.append("NO_TRADING_ACTIVITY")
        if not has_valid_book:
            states.append("NO_ACTIVE_BOOK")

        execution_ready = bool(is_current_date and has_last and has_activity and has_valid_book)
        next_session_eligible = bool(is_current_date and has_last and has_activity)
        if execution_ready:
            states.append("EXECUTION_READY")
        if next_session_eligible:
            states.append("NEXT_SESSION_ELIGIBLE")

        by_symbol[symbol] = {
            "snapshot_date": snapshot_date,
            "quote_updated_at": quote_updated_at,
            "last": None if pd.isna(row.get("last")) else row.get("last"),
            "bid1": None if pd.isna(row.get("bid1")) else row.get("bid1"),
            "ask1": None if pd.isna(row.get("ask1")) else row.get("ask1"),
            "volume": None if pd.isna(row.get("volume")) else row.get("volume"),
            "amount": None if pd.isna(row.get("amount")) else row.get("amount"),
            "has_last": has_last,
            "has_activity": has_activity,
            "has_valid_book": has_valid_book,
            "execution_ready": execution_ready,
            "next_session_eligible": next_session_eligible,
            "states": states,
        }

    ready = [symbol for symbol, item in by_symbol.items() if item["execution_ready"]]
    intraday_blocked = [symbol for symbol in symbols if symbol not in ready]
    next_session = [
        symbol for symbol, item in by_symbol.items() if item["next_session_eligible"]
    ]
    next_session_blocked = [symbol for symbol in symbols if symbol not in next_session]
    return {
        "as_of_date": as_of_date,
        "method": "current_day_activity_with_separate_live_book_gate",
        "policy": (
            "intraday execution requires a live best book; EOD t+1 signals use "
            "next-session eligibility and must re-check a live book at entry"
        ),
        "execution_ready_symbols": ready,
        "execution_blocked_symbols": intraday_blocked,
        "next_session_eligible_symbols": next_session,
        "next_session_blocked_symbols": next_session_blocked,
        "by_symbol": by_symbol,
    }


def write_execution_readiness(
    snapshot_path: str | Path,
    output_path: str | Path,
    symbols: list[str],
    as_of_date: str,
) -> dict:
    report = build_execution_readiness(snapshot_path, symbols, as_of_date)
    Path(output_path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report
