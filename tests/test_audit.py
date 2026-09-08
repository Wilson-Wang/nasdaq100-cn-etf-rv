from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from etf_dataset.audit import build_source_run_record, new_run_id


def test_source_run_record_marks_partial_success_degraded():
    started = datetime(2026, 9, 8, 0, 0, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 9, 8, 0, 0, 2, tzinfo=timezone.utc)
    frame = pd.DataFrame(
        {
            "date": ["2026-09-07", "2026-09-08"],
            "source": ["baostock", "akshare:sina:fund_etf_hist_sina"],
        }
    )

    record = build_source_run_record(
        run_id="run1",
        resource="prices",
        symbol="513100",
        requested_start="2026-08-01",
        requested_end="2026-09-08",
        started_at=started,
        finished_at=finished,
        frame=frame,
        date_column="date",
        errors=["Eastmoney unavailable"],
    )

    assert record["status"] == "DEGRADED"
    assert record["row_count"] == 2
    assert record["min_date"] == "2026-09-07"
    assert record["max_date"] == "2026-09-08"
    assert record["sources"] == "akshare:sina:fund_etf_hist_sina;baostock"
    assert record["duration_seconds"] == 2.0


def test_source_run_record_marks_empty_fetch_failed():
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    record = build_source_run_record(
        run_id="run2",
        resource="pcf",
        symbol="159941",
        requested_start="2026-09-08",
        requested_end="2026-09-08",
        started_at=now,
        finished_at=now,
        frame=pd.DataFrame(),
        date_column="date",
        errors=["empty result"],
    )
    assert record["status"] == "FAILED"
    assert record["row_count"] == 0


def test_run_id_is_deterministic_for_supplied_time():
    value = datetime(2026, 9, 8, 1, 2, 3, 456789, tzinfo=timezone.utc)
    assert new_run_id(value) == "20260908T010203456789Z"
