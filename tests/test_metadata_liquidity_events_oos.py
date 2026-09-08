from __future__ import annotations

import json

import pandas as pd
import pytest

from etf_dataset.events import build_event_table, derive_pcf_events
from etf_dataset.liquidity import build_liquidity_scores
from etf_dataset.metadata import (
    _cny_amount,
    _inception,
    _pct,
    _shares,
    build_product_quality_scores,
)
from etf_dataset.oos import canonical_model_hash, register_frozen_model


def test_metadata_parsers_and_missing_weight_pqs():
    assert _pct("0.50%") == 0.5
    assert _cny_amount("12.3亿元") == 1.23e9
    assert _shares("4.5亿份") == 4.5e8
    assert _inception("2023年03月15日 / 2.1亿份") == "2023-03-15"

    metadata = pd.DataFrame(
        [
            {
                "symbol": "A",
                "effective_date": "2026-09-08",
                "inception_date": "2023-01-01",
                "aum_cny": 1e9,
                "management_fee_pct": 0.5,
                "custodian_fee_pct": 0.1,
                "sales_service_fee_pct": pd.NA,
            },
            {
                "symbol": "B",
                "effective_date": "2026-09-08",
                "inception_date": "2024-01-01",
                "aum_cny": 5e8,
                "management_fee_pct": 0.8,
                "custodian_fee_pct": 0.2,
                "sales_service_fee_pct": pd.NA,
            },
        ]
    )
    prices = pd.DataFrame(
        {
            "symbol": ["A"] * 20 + ["B"] * 20,
            "date": pd.bdate_range("2026-08-10", periods=20).tolist() * 2,
            "amount": [2e8] * 20 + [1e8] * 20,
        }
    )
    scores = build_product_quality_scores(metadata, prices, {"A": 0.02, "B": None}, "2026-09-08")
    a = scores.loc[scores["symbol"].eq("A")].iloc[0]
    b = scores.loc[scores["symbol"].eq("B")].iloc[0]
    assert a["pqs"] > b["pqs"]
    assert a["pqs_coverage"] == pytest.approx(1.0)
    assert b["pqs_coverage"] == pytest.approx(0.8)


def test_liquidity_reweights_missing_spread_without_imputation():
    dates = pd.bdate_range("2026-08-10", periods=20)
    prices = pd.DataFrame(
        {
            "symbol": ["A"] * 20 + ["B"] * 20,
            "date": dates.tolist() * 2,
            "amount": [2e8] * 20 + [1e8] * 20,
        }
    )
    snapshot = pd.DataFrame(
        [
            {
                "symbol": "A",
                "data_date": "2026-09-08",
                "updated_at": "2026-09-08 10:00:00+08:00",
                "turnover_pct": 2.0,
                "bid_ask_spread_pct": 0.04,
            },
            {
                "symbol": "B",
                "data_date": "2026-09-08",
                "updated_at": "2026-09-08 10:00:00+08:00",
                "turnover_pct": 1.0,
                "bid_ask_spread_pct": pd.NA,
            },
        ]
    )
    scores = build_liquidity_scores(prices, snapshot, ["A", "B"]).set_index("symbol")
    assert scores.loc["B", "liquidity_coverage"] == pytest.approx(0.85)
    assert pd.notna(scores.loc["B", "liquidity_score"])


def test_pcf_change_generates_point_in_time_event():
    pcf = pd.DataFrame(
        [
            {
                "symbol": "A",
                "date": "2026-09-08",
                "creation_allowed": True,
                "redemption_allowed": True,
                "available_at": "2026-09-08T08:30:00+08:00",
                "source": "exchange",
                "document_url": pd.NA,
            },
            {
                "symbol": "A",
                "date": "2026-09-09",
                "creation_allowed": False,
                "redemption_allowed": True,
                "available_at": "2026-09-09T08:30:00+08:00",
                "source": "exchange",
                "document_url": pd.NA,
            },
        ]
    )
    events = derive_pcf_events(pcf)
    assert len(events) == 1
    assert events.iloc[0]["event_type"] == "PRIMARY_MARKET_STATUS_CHANGE"
    assert events.iloc[0]["severity"] == "HIGH"
    assert pd.isna(events.iloc[0]["source_url"])
    assert len(build_event_table(pcf, pd.DataFrame())) == 1


def test_frozen_model_registry_rejects_same_version_mutation(tmp_path):
    path = tmp_path / "registry.json"
    spec = {"model_version": "v1", "parameters": {"z": 2.0}}
    registered = register_frozen_model(spec, path)
    assert registered["model_hash"] == canonical_model_hash(spec)
    with pytest.raises(ValueError):
        register_frozen_model({"model_version": "v1", "parameters": {"z": 1.9}}, path)
    loaded = json.loads(path.read_text())
    assert loaded["models"]["v1"]["model_hash"] == canonical_model_hash(spec)
