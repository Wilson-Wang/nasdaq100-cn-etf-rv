from __future__ import annotations

import pandas as pd

from etf_dataset.quality import build_quality_report


def _write_frames(tmp_path, *, pit_verified: bool = True):
    price_dates = pd.bdate_range(end="2026-09-07", periods=125)
    prices = pd.DataFrame(
        {
            "symbol": ["513100"] * len(price_dates),
            "date": price_dates.strftime("%Y-%m-%d"),
            "close": [1.0] * len(price_dates),
            "volume": [1000] * len(price_dates),
            "is_tradable": [True] * len(price_dates),
            "source": ["baostock"] * len(price_dates),
        }
    )
    nav = pd.DataFrame(
        [
            {
                "symbol": "513100",
                "nav_date": "2026-09-04",
                "unit_nav": 1.0,
                "pit_verified": pit_verified,
                "source": "eastmoney",
            }
        ]
    )
    snapshot = pd.DataFrame(
        [
            {
                "symbol": "513100",
                "data_date": "2026-09-08",
                "last": 1.0,
                "source": "eastmoney",
            }
        ]
    )

    prices_path = tmp_path / "prices.csv"
    nav_path = tmp_path / "nav.csv"
    snapshot_path = tmp_path / "snapshot.csv"
    prices.to_csv(prices_path, index=False)
    nav.to_csv(nav_path, index=False)
    snapshot.to_csv(snapshot_path, index=False)
    return prices_path, nav_path, snapshot_path


def test_quality_report_separates_research_depth_from_signal_readiness(tmp_path):
    prices_path, nav_path, snapshot_path = _write_frames(tmp_path, pit_verified=True)

    report = build_quality_report(
        prices_path,
        nav_path,
        snapshot_path,
        ["513100"],
        "2026-09-08",
        [],
    )

    item = report["by_symbol"]["513100"]
    assert item["usable_price_observations"] == 125
    assert "LIMITED_RESEARCH_DEPTH" in item["states"]
    assert item["price_lag_business_days"] == 1
    assert item["nav_lag_business_days"] == 2
    assert item["formal_signal_ready"] is True
    assert report["formal_signal_ready_symbols"] == ["513100"]


def test_quality_report_blocks_unverified_pit_and_source_failure(tmp_path):
    prices_path, nav_path, snapshot_path = _write_frames(tmp_path, pit_verified=False)

    report = build_quality_report(
        prices_path,
        nav_path,
        snapshot_path,
        ["513100"],
        "2026-09-08",
        ["513100 NAV eastmoney: ReadTimeout"],
    )

    item = report["by_symbol"]["513100"]
    assert "PIT_UNVERIFIED" in item["states"]
    assert "SOURCE_DEGRADED" in item["states"]
    assert item["formal_signal_ready"] is False
    assert report["formal_signal_ready_symbols"] == []
