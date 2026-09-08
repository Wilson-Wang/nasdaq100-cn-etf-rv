from __future__ import annotations

import math

import pandas as pd

from etf_dataset.analysis import (
    add_snapshot_valuation_fields,
    build_official_nav_premium_history,
    compute_common_factor_residuals,
    latest_factor_candidates,
)


def test_snapshot_valuation_fields_use_mid_and_iopv():
    frame = pd.DataFrame(
        [
            {
                "symbol": "513100",
                "last": 1.02,
                "iopv": 1.00,
                "bid1": 1.01,
                "ask1": 1.03,
            }
        ]
    )

    out = add_snapshot_valuation_fields(frame).iloc[0]

    assert abs(float(out["mid"]) - 1.02) < 1e-12
    assert abs(float(out["spread_bps"]) - (0.02 / 1.02 * 10_000)) < 1e-9
    assert abs(float(out["iopv_premium_pct"]) - 2.0) < 1e-9
    assert abs(float(out["mid_iopv_premium_pct"]) - 2.0) < 1e-9
    assert out["current_anchor_status"] == "IOPV_AVAILABLE"


def test_official_nav_history_is_explicitly_pit_unverified_without_flag():
    prices = pd.DataFrame(
        [
            {
                "symbol": "513100",
                "date": "2026-09-01",
                "close": 1.02,
                "volume": 1000,
                "is_tradable": True,
            }
        ]
    )
    nav = pd.DataFrame(
        [
            {
                "symbol": "513100",
                "nav_date": "2026-09-01",
                "unit_nav": 1.00,
            }
        ]
    )

    out = build_official_nav_premium_history(prices, nav)

    assert len(out) == 1
    assert out.iloc[0]["pit_status"] == "PIT_UNVERIFIED"
    assert abs(float(out.iloc[0]["official_premium_pct"]) - 2.0) < 1e-9


def test_factor_baseline_excludes_current_observation():
    dates = pd.bdate_range("2026-01-01", periods=8)
    rows = []
    for symbol, structural in (("A", 0.02), ("B", -0.02)):
        for index, date in enumerate(dates):
            shock = 0.0
            if symbol == "A" and index == len(dates) - 1:
                shock = 0.50
            rows.append(
                {
                    "symbol": symbol,
                    "date": date,
                    "log_official_premium": structural + shock,
                }
            )

    premium = pd.DataFrame(rows)
    factor = compute_common_factor_residuals(
        premium,
        baseline_window=5,
        baseline_min_periods=3,
        z_window=3,
        z_min_periods=3,
    )
    latest_a = factor.loc[
        (factor["symbol"] == "A") & (factor["date"] == dates[-1])
    ].iloc[0]

    assert abs(float(latest_a["structural_alpha"]) - 0.02) < 1e-12
    assert float(latest_a["idiosyncratic_residual"]) > 0.20


def test_factor_candidates_rotate_from_expensive_to_cheap():
    date = pd.Timestamp("2026-09-01")
    factor = pd.DataFrame(
        [
            {
                "symbol": "cheap",
                "date": date,
                "idiosyncratic_residual": -0.03,
                "idiosyncratic_robust_z": -2.5,
            },
            {
                "symbol": "middle",
                "date": date,
                "idiosyncratic_residual": 0.00,
                "idiosyncratic_robust_z": 0.0,
            },
            {
                "symbol": "expensive",
                "date": date,
                "idiosyncratic_residual": 0.04,
                "idiosyncratic_robust_z": 3.0,
            },
            {
                "symbol": "expensive2",
                "date": date,
                "idiosyncratic_residual": 0.02,
                "idiosyncratic_robust_z": 2.0,
            },
        ]
    )

    out = latest_factor_candidates(factor, n_each=1)

    assert len(out) == 1
    assert out.iloc[0]["rotate_out"] == "expensive"
    assert out.iloc[0]["rotate_in"] == "cheap"
    assert math.isclose(float(out.iloc[0]["residual_gap"]), 0.07)
    assert out.iloc[0]["model_status"] == "EXPERIMENTAL_CANDIDATE_ONLY"
