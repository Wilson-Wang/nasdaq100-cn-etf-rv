import math

import numpy as np
import pandas as pd

from etf_dataset.pair_engine import (
    PairEngineConfig,
    add_pair_features,
    backtest_crossing_events,
    benjamini_hochberg,
    build_pair_spread,
    classify_latest_signal,
)


def _prices(symbol: str, dates: pd.DatetimeIndex, offset: float = 0.0) -> pd.DataFrame:
    close = 1.0 + offset + np.linspace(0, 0.05, len(dates))
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates.strftime("%Y-%m-%d"),
            "open": close,
            "close": close,
            "volume": 1000,
            "amount": 100000,
            "is_tradable": True,
        }
    )


def test_pair_spread_uses_only_common_nav_available_by_signal_cutoff():
    dates = pd.bdate_range("2026-01-05", periods=5)
    prices = pd.concat([_prices("A", dates), _prices("B", dates, 0.1)], ignore_index=True)
    nav = pd.DataFrame(
        {
            "symbol": ["A", "B", "A", "B"],
            "nav_date": ["2026-01-05", "2026-01-05", "2026-01-06", "2026-01-06"],
            "unit_nav": [1.0, 1.1, 1.01, 1.11],
            "available_at": [
                "2026-01-07T23:59:59+08:00",
                "2026-01-07T23:59:59+08:00",
                "2026-01-08T23:59:59+08:00",
                "2026-01-08T23:59:59+08:00",
            ],
            "pit_usable": True,
        }
    )
    spread = build_pair_spread(prices, nav, "A", "B")
    assert spread.iloc[0]["date"] == pd.Timestamp("2026-01-07")
    assert spread.iloc[0]["common_nav_date"] == "2026-01-05"
    assert spread.loc[spread["date"].eq(pd.Timestamp("2026-01-08")), "common_nav_date"].item() == "2026-01-06"


def test_robust_z_uses_only_prior_window():
    n = 125
    dates = pd.bdate_range("2025-01-02", periods=n)
    history = np.sin(np.arange(n) / 5) * 0.01
    history[-1] = 0.10
    frame = pd.DataFrame(
        {
            "date": dates,
            "d": history,
            "symbol_i": "A",
            "symbol_j": "B",
            "open_i": 1.0,
            "open_j": 1.0,
            "close_i": 1.0,
            "close_j": 1.0,
        }
    )
    features = add_pair_features(frame)
    latest = features.iloc[-1]
    expected_center = float(pd.Series(history[-61:-1]).median())
    assert math.isclose(latest["median60"], expected_center, rel_tol=0, abs_tol=1e-12)
    assert latest["z"] > 5


def test_crossing_event_uses_t_plus_one_open_and_non_overlapping_window():
    dates = pd.bdate_range("2026-01-05", periods=10)
    frame = pd.DataFrame(
        {
            "date": dates,
            "symbol_i": "A",
            "symbol_j": "B",
            "d": [0, 0, 0.03, 0.04, 0, 0, 0, 0, 0, 0],
            "mu": [0] * 10,
            "z": [0, 1, 2.1, 2.2, 0.5, 0.2, 2.5, 2.7, 0.2, 0.1],
            "half_life": [5] * 10,
            "adf_p": [0.02] * 10,
            "expected_convergence_5d": [0.02] * 10,
            "open_i": [1.0] * 10,
            "close_i": [1.0] * 10,
            "open_j": [1.0] * 10,
            "close_j": [1.0, 1.0, 1.0, 1.01, 1.02, 1.03, 1.04, 1.05, 1.06, 1.07],
        }
    )
    events, metrics = backtest_crossing_events(frame, PairEngineConfig(assumed_rotation_cost=0.0))
    assert len(events) == 1
    assert events.iloc[0]["entry_date"] == dates[3]
    assert events.iloc[0]["exit_date"] == dates[7]
    assert metrics["signal_count"] == 1


def test_bh_q_values_are_monotone_and_bounded():
    q = benjamini_hochberg([0.01, 0.04, 0.03, None])
    assert q[0] <= q[2] <= q[1] <= 1
    assert q[3] is None


def test_execution_gate_blocks_formal_trade():
    latest = pd.Series(
        {
            "symbol_i": "A",
            "symbol_j": "B",
            "d": 0.03,
            "mu": 0.0,
            "z": 2.5,
            "half_life": 5.0,
            "adf_p": 0.01,
            "stability_ratio": 1.0,
            "expected_convergence_5d": 0.02,
            "regime": "NORMAL",
        }
    )
    quality = {
        "A": {"formal_signal_ready": True, "primary_market": {"creation_state": "OPEN", "redemption_state": "OPEN"}},
        "B": {"formal_signal_ready": True, "primary_market": {"creation_state": "OPEN", "redemption_state": "OPEN"}},
    }
    bt = {"adjusted_win_rate_5d": 0.70, "signal_count": 30, "median_mae_5d": -0.002}
    result = classify_latest_signal(latest, bt, quality, {"A"}, 80.0)
    assert result["signal"] == "WATCH"
    assert result["gates"]["execution_readiness"] is False
