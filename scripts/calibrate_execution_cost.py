from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


MIN_LIVE_OBSERVATIONS = 20


def main() -> None:
    data = Path("data")
    path = data / "execution_history.csv"
    result: dict[str, object] = {
        "status": "INSUFFICIENT_HISTORY",
        "minimum_live_observations_per_symbol": MIN_LIVE_OBSERVATIONS,
        "model_v1_cost_unchanged": 0.0015,
        "symbol_costs": {},
        "portfolio_research_cost": None,
        "semantics": "challenger_calibration_only_does_not_mutate_frozen_pair-engine-v1.0.0",
    }
    if path.exists() and path.stat().st_size:
        frame = pd.read_csv(path, dtype={"symbol": "string"})
        frame["bid_ask_spread_pct"] = pd.to_numeric(
            frame.get("bid_ask_spread_pct"), errors="coerce"
        )
        live = frame.loc[
            frame.get("observation_quality", "").astype("string").eq("LIVE_BOOK")
            & frame["bid_ask_spread_pct"].gt(0)
        ].copy()
        estimates = []
        for symbol, group in live.groupby("symbol"):
            values = group["bid_ask_spread_pct"].dropna()
            count = int(len(values))
            item = {
                "live_observations": count,
                "median_quoted_spread": float(values.median()) if count else None,
                "p75_quoted_spread": float(values.quantile(0.75)) if count else None,
                "ready": count >= MIN_LIVE_OBSERVATIONS,
            }
            if item["ready"]:
                # A rotation has two ETF legs. Using the sum of two p75 half-spreads
                # is a conservative quoted-spread baseline before additional impact.
                item["estimated_two_leg_switch_cost"] = float(values.quantile(0.75))
                estimates.append(item["estimated_two_leg_switch_cost"])
            result["symbol_costs"][str(symbol)] = item
        if result["symbol_costs"] and all(
            bool(item.get("ready")) for item in result["symbol_costs"].values()
        ):
            result["status"] = "EMPIRICAL_BASELINE_READY"
            result["portfolio_research_cost"] = float(pd.Series(estimates).median()) if estimates else None

    (data / "execution_cost_model.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        f"execution_cost_model status={result['status']} "
        f"symbols={len(result['symbol_costs'])}"
    )


if __name__ == "__main__":
    main()
