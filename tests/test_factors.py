from __future__ import annotations

import pandas as pd

from etf_dataset.factors import (
    FACTOR_COLUMNS,
    CONSERVATIVE_AVAILABILITY_METHOD,
    conservative_available_at,
    factor_summary,
    validate_factor_inputs,
)


def test_conservative_available_at_is_next_day_08_cn():
    assert conservative_available_at("2026-09-07") == "2026-09-08T08:00:00+08:00"


def test_factor_summary_and_validation(tmp_path):
    frame = pd.DataFrame(
        [
            {
                "factor_name": "NDX",
                "factor_date": "2026-09-07",
                "value": 25000.0,
                "open": 24900.0,
                "high": 25100.0,
                "low": 24800.0,
                "volume": 1.0,
                "available_at": conservative_available_at("2026-09-07"),
                "availability_method": CONSERVATIVE_AVAILABILITY_METHOD,
                "availability_verified": False,
                "pit_usable": True,
                "source": "akshare:sina:index_us_stock_sina",
                "source_priority": 10,
                "ingested_at_utc": "2026-09-08T00:00:00+00:00",
            },
            {
                "factor_name": "USDCNH",
                "factor_date": "2026-09-07",
                "value": 7.1,
                "open": 7.09,
                "high": 7.11,
                "low": 7.08,
                "volume": pd.NA,
                "available_at": conservative_available_at("2026-09-07"),
                "availability_method": CONSERVATIVE_AVAILABILITY_METHOD,
                "availability_verified": False,
                "pit_usable": True,
                "source": "akshare:eastmoney:forex_hist_em",
                "source_priority": 10,
                "ingested_at_utc": "2026-09-08T00:00:00+00:00",
            },
        ]
    ).reindex(columns=FACTOR_COLUMNS)
    path = tmp_path / "factor_inputs.csv"
    frame.to_csv(path, index=False)

    assert validate_factor_inputs(path) == []
    summary = factor_summary(path)
    assert summary["rows"] == 2
    assert summary["pit_usable_rows"] == 2
    assert summary["availability_verified"] is False
    assert set(summary["factors"]) == {"NDX", "USDCNH"}


def test_validation_warns_when_one_factor_missing(tmp_path):
    frame = pd.DataFrame(
        [
            {
                "factor_name": "NDX",
                "factor_date": "2026-09-07",
                "value": 25000.0,
                "available_at": conservative_available_at("2026-09-07"),
                "availability_method": CONSERVATIVE_AVAILABILITY_METHOD,
                "pit_usable": True,
                "source": "akshare:sina:index_us_stock_sina",
                "source_priority": 10,
                "ingested_at_utc": "2026-09-08T00:00:00+00:00",
            }
        ]
    )
    path = tmp_path / "factor_inputs.csv"
    frame.to_csv(path, index=False)

    assert validate_factor_inputs(path) == ["factor input missing: USDCNH or USDCNY"]
