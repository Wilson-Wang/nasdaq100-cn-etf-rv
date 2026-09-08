from __future__ import annotations

import pandas as pd

from etf_dataset.execution import build_execution_readiness


def test_execution_readiness_requires_current_activity_and_book(tmp_path):
    path = tmp_path / "snapshot.csv"
    pd.DataFrame(
        [
            {
                "symbol": "159513",
                "data_date": "2026-09-08",
                "updated_at": "2026-09-08 09:44:00+08:00",
                "last": 1.82,
                "volume": 100000,
                "amount": 18000000,
                "bid1": 1.819,
                "ask1": 1.82,
            },
            {
                "symbol": "159941",
                "data_date": "2026-09-08",
                "updated_at": "2026-09-08 09:43:00+08:00",
                "last": 1.668,
                "volume": pd.NA,
                "amount": pd.NA,
                "bid1": pd.NA,
                "ask1": pd.NA,
            },
        ]
    ).to_csv(path, index=False)

    report = build_execution_readiness(path, ["159513", "159941"], "2026-09-08")
    assert report["execution_ready_symbols"] == ["159513"]
    assert report["execution_blocked_symbols"] == ["159941"]
    assert report["next_session_eligible_symbols"] == ["159513"]
    assert report["by_symbol"]["159513"]["execution_ready"] is True
    assert report["by_symbol"]["159513"]["next_session_eligible"] is True
    assert report["by_symbol"]["159941"]["execution_ready"] is False
    assert report["by_symbol"]["159941"]["next_session_eligible"] is False
    assert "NO_TRADING_ACTIVITY" in report["by_symbol"]["159941"]["states"]
    assert "NO_ACTIVE_BOOK" in report["by_symbol"]["159941"]["states"]


def test_eod_symbol_can_be_next_session_eligible_without_live_book(tmp_path):
    path = tmp_path / "snapshot.csv"
    pd.DataFrame(
        [
            {
                "symbol": "513300",
                "data_date": "2026-09-08",
                "updated_at": "2026-09-08 15:31:00+08:00",
                "last": 2.71,
                "volume": 200000,
                "amount": 54000000,
                "bid1": pd.NA,
                "ask1": pd.NA,
            }
        ]
    ).to_csv(path, index=False)

    report = build_execution_readiness(path, ["513300"], "2026-09-08")
    item = report["by_symbol"]["513300"]
    assert item["execution_ready"] is False
    assert item["next_session_eligible"] is True
    assert report["next_session_eligible_symbols"] == ["513300"]
    assert "NO_ACTIVE_BOOK" in item["states"]
    assert "NEXT_SESSION_ELIGIBLE" in item["states"]


def test_old_snapshot_is_not_execution_ready(tmp_path):
    path = tmp_path / "snapshot.csv"
    pd.DataFrame(
        [
            {
                "symbol": "513100",
                "data_date": "2026-09-07",
                "updated_at": "2026-09-07 15:34:00+08:00",
                "last": 2.236,
                "volume": 100000,
                "amount": 20000000,
                "bid1": 2.235,
                "ask1": 2.236,
            }
        ]
    ).to_csv(path, index=False)

    report = build_execution_readiness(path, ["513100"], "2026-09-08")
    item = report["by_symbol"]["513100"]
    assert item["execution_ready"] is False
    assert item["next_session_eligible"] is False
    assert "NO_CURRENT_DAY_SNAPSHOT" in item["states"]
