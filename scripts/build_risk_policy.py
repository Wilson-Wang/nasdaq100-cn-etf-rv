from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    data = Path("data")
    policy = {
        "version": "risk-overlay-v1.0.0",
        "production_signal_model": "pair-engine-v1.0.0",
        "production_entry_policy": {
            "signal_timing": "EOD",
            "earliest_entry": "next_tradable_session_open",
            "require_live_book_at_entry": True,
            "require_both_legs_execution_ready_at_entry": True,
            "no_entry_if_model_readiness_fails": True,
            "no_entry_if_directional_regime_gate_fails": True,
        },
        "production_exit_policy": {
            "baseline": "fifth_future_aligned_session_close",
            "fixed_horizon_days": 5,
            "price_stop": None,
            "reason_price_stop_absent": (
                "No price stop is promoted without prospective/OOS evidence; adding one would "
                "silently mutate the frozen v1 backtest contract."
            ),
        },
        "emergency_research_overrides": {
            "critical_data_failure": "BLOCK_NEW_ENTRY_AND_FLAG_MANUAL_REVIEW",
            "regime_disabled_after_signal": "FLAG_EXIT_REVIEW",
            "creation_or_redemption_restriction_against_direction": "FLAG_EXIT_REVIEW",
            "trading_halt_or_no_tradable_quote": "NO_ASSUMED_FILL_FLAG_MANUAL_REVIEW",
        },
        "challenger_exit_rules": {
            "enabled_for_production": False,
            "candidate_mean_reversion_exit": "abs_robust_z_below_1",
            "candidate_structural_break_exit": "regime_disabled",
            "promotion_rule": "requires separate frozen version and prospective OOS comparison",
        },
        "semantics": "risk_research_policy_not_automatic_order_or_liquidation_instruction",
    }
    (data / "risk_policy.json").write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n"
    )
    print("risk_policy version=risk-overlay-v1.0.0 production_exit=fixed_5_session")


if __name__ == "__main__":
    main()
