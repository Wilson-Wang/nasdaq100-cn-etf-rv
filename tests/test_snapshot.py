from __future__ import annotations

import pandas as pd
import pytest

from etf_dataset.snapshot import add_snapshot_derived_fields


def test_add_snapshot_derived_fields_uses_executable_mid_and_iopv():
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

    result = add_snapshot_derived_fields(frame).iloc[0]

    assert result["mid"] == pytest.approx(1.02)
    assert result["bid_ask_spread_pct"] == pytest.approx((0.02 / 1.02) * 100)
    assert result["last_iopv_premium_pct"] == pytest.approx(2.0)
    assert result["mid_iopv_premium_pct"] == pytest.approx(2.0)


def test_add_snapshot_derived_fields_rejects_crossed_or_invalid_quotes():
    frame = pd.DataFrame(
        [
            {
                "symbol": "513100",
                "last": 1.0,
                "iopv": 0.0,
                "bid1": 1.02,
                "ask1": 1.01,
            }
        ]
    )

    result = add_snapshot_derived_fields(frame).iloc[0]

    assert pd.isna(result["mid"])
    assert pd.isna(result["bid_ask_spread_pct"])
    assert pd.isna(result["last_iopv_premium_pct"])
    assert pd.isna(result["mid_iopv_premium_pct"])
