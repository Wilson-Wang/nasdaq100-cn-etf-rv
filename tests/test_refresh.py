from __future__ import annotations

import pandas as pd

from etf_dataset.refresh import plan_incremental_start


def test_prices_stay_full_until_250_tradable_observations():
    dates = pd.bdate_range("2026-01-05", periods=165)
    frame = pd.DataFrame(
        {
            "symbol": ["513100"] * len(dates),
            "date": dates.strftime("%Y-%m-%d"),
            "is_tradable": [True] * len(dates),
        }
    )

    start, mode = plan_incremental_start(
        frame,
        date_column="date",
        requested_start="2025-06-15",
        filter_column="symbol",
        filter_value="513100",
        minimum_observations=250,
        require_requested_start_coverage=False,
        tradable_column="is_tradable",
    )

    assert start == "2025-06-15"
    assert mode == "FULL_DEPTH_165_LT_250"


def test_prices_switch_to_overlapping_incremental_after_250_observations():
    dates = pd.bdate_range("2025-06-16", periods=302)
    frame = pd.DataFrame(
        {
            "symbol": ["513100"] * len(dates),
            "date": dates.strftime("%Y-%m-%d"),
            "is_tradable": [True] * len(dates),
        }
    )

    start, mode = plan_incremental_start(
        frame,
        date_column="date",
        requested_start="2025-06-15",
        filter_column="symbol",
        filter_value="513100",
        minimum_observations=250,
        require_requested_start_coverage=False,
        tradable_column="is_tradable",
    )

    expected = (dates.max() - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
    assert start == expected
    assert mode == "INCREMENTAL"


def test_nav_requires_requested_start_coverage_before_incremental():
    dates = pd.bdate_range("2026-01-05", periods=150)
    frame = pd.DataFrame(
        {
            "symbol": ["513100"] * len(dates),
            "nav_date": dates.strftime("%Y-%m-%d"),
        }
    )

    start, mode = plan_incremental_start(
        frame,
        date_column="nav_date",
        requested_start="2025-06-15",
        filter_column="symbol",
        filter_value="513100",
        minimum_observations=120,
        require_requested_start_coverage=True,
    )

    assert start == "2025-06-15"
    assert mode == "FULL_START_COVERAGE_GAP"


def test_nav_with_full_history_uses_incremental_overlap():
    dates = pd.bdate_range("2025-06-16", periods=301)
    frame = pd.DataFrame(
        {
            "symbol": ["513100"] * len(dates),
            "nav_date": dates.strftime("%Y-%m-%d"),
        }
    )

    start, mode = plan_incremental_start(
        frame,
        date_column="nav_date",
        requested_start="2025-06-15",
        filter_column="symbol",
        filter_value="513100",
        minimum_observations=120,
        require_requested_start_coverage=True,
    )

    expected = (dates.max() - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
    assert start == expected
    assert mode == "INCREMENTAL"
