import pandas as pd

from etf_dataset.portfolio import build_portfolio_plan


def test_portfolio_never_upgrades_watch_and_removes_symbol_conflicts():
    frame = pd.DataFrame(
        [
            {"pair": "A-B", "rotate_in": "A", "rotate_out": "B", "signal": "TRADE", "pair_score": 90, "net_expected_convergence_5d": 0.012, "pair_liquidity_score": 80},
            {"pair": "A-C", "rotate_in": "C", "rotate_out": "A", "signal": "STRONG TRADE", "pair_score": 88, "net_expected_convergence_5d": 0.011, "pair_liquidity_score": 90},
            {"pair": "D-E", "rotate_in": "D", "rotate_out": "E", "signal": "WATCH", "pair_score": 99, "net_expected_convergence_5d": 0.020, "pair_liquidity_score": 100},
        ]
    )
    plan, summary = build_portfolio_plan(frame)
    assert len(plan) == 1
    assert plan.iloc[0]["pair"] in {"A-B", "A-C"}
    assert summary["conflict_rejections"] == 1
    assert "D-E" not in set(plan["pair"])
    assert abs(float(plan["allocation_weight"].sum()) - 1.0) < 1e-12
