import pandas as pd

from etf_dataset.reporting import build_product_ranking, choose_report_mode


def test_full_delta_mode_changes_on_signal_state():
    current = {"pair_engine": "v1", "signals": {"A-B": "NO TRADE"}, "regimes": {"A-B": "NORMAL"}, "model_ready_symbols": ["A", "B"], "health_status": "OK"}
    previous = dict(current)
    mode, reasons = choose_report_mode(current, previous)
    assert mode == "DELTA"
    assert reasons == []

    changed = dict(current)
    changed["signals"] = {"A-B": "TRADE"}
    mode, reasons = choose_report_mode(changed, previous)
    assert mode == "FULL"
    assert "signal_state_changed" in reasons
    assert "formal_signal_present" in reasons


def test_product_ranking_keeps_pqs_and_tvs_separate():
    pqs = pd.DataFrame(
        [
            {"symbol": "A", "effective_date": "2026-09-08", "name": "A fund", "pqs": 80.0, "pqs_coverage": 1.0},
            {"symbol": "B", "effective_date": "2026-09-08", "name": "B fund", "pqs": 60.0, "pqs_coverage": 0.5},
        ]
    )
    pairs = pd.DataFrame(
        [{"rotate_in": "B", "rotate_out": "A", "pair_score": 80.0, "pair_liquidity_score": 80.0, "z": 2.0, "net_expected_convergence_5d": 0.01}]
    )
    result = build_product_ranking(pqs, pairs, ["A", "B"]).set_index("symbol")
    assert result.loc["B", "tvs"] > result.loc["A", "tvs"]
    assert pd.isna(result.loc["B", "overall"])
    assert result.loc["B", "overall_status"] == "PQS_INCOMPLETE"
