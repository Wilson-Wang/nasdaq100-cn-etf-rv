from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from etf_dataset.liquidity import build_liquidity_scores, liquidity_score_map
from etf_dataset.pair_engine import PairEngineConfig
from etf_dataset.strategy_engine import analyze_universe_v1


def main() -> None:
    data = Path("data")
    prices = pd.read_csv(data / "etf_prices.csv", dtype={"symbol": "string"})
    nav = pd.read_csv(data / "etf_nav.csv", dtype={"symbol": "string"})
    snapshot = pd.read_csv(data / "etf_snapshot.csv", dtype={"symbol": "string"})
    manifest = json.loads((data / "manifest.json").read_text())
    execution_path = data / "execution_readiness.json"
    execution = json.loads(execution_path.read_text()) if execution_path.exists() else {}
    # Pair Engine v1 is an EOD model with earliest execution at t+1. Same-day
    # activity is required as a precheck, while a live book must be re-checked
    # when the next-session order is actually sent.
    if execution.get("next_session_eligible_symbols") is not None:
        execution = dict(execution)
        execution["execution_ready_symbols"] = execution["next_session_eligible_symbols"]
    symbols = [str(value) for value in manifest.get("universe", [])]

    liquidity = build_liquidity_scores(prices, snapshot, symbols)
    liquidity.to_csv(data / "liquidity_scores.csv", index=False)
    liquidity.to_parquet(data / "liquidity_scores.parquet", index=False)

    summary, event_tables = analyze_universe_v1(
        prices,
        nav,
        symbols,
        manifest,
        execution,
        liquidity_score_map(liquidity),
        PairEngineConfig(),
    )
    summary_out = summary.copy()
    if "gates" in summary_out.columns:
        summary_out["gates"] = summary_out["gates"].map(
            lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
        )
    summary_out.to_csv(data / "pair_analysis.csv", index=False)
    summary_out.to_parquet(data / "pair_analysis.parquet", index=False)

    events = []
    for pair, frame in event_tables.items():
        if frame.empty:
            continue
        item = frame.copy()
        item.insert(0, "pair", pair)
        events.append(item)
    event_frame = pd.concat(events, ignore_index=True) if events else pd.DataFrame()
    event_frame.to_csv(data / "pair_events.csv", index=False)
    if not event_frame.empty:
        event_frame.to_parquet(data / "pair_events.parquet", index=False)

    counts = summary["signal"].value_counts().to_dict() if not summary.empty else {}
    manifest["pair_engine_status"] = {
        "engine": "pair-engine-v1.0.0",
        "pairs": int(len(summary)),
        "signal_counts": {str(key): int(value) for key, value in counts.items()},
        "liquidity_method": "20d_amount_plus_snapshot_turnover_plus_spread_reweighted",
        "cost_method": "ASSUMED_COST",
        "multiple_testing": "Benjamini-Hochberg ADF q<=0.10 additional formal gate",
        "statistical_compute": "ADF/KPSS/stability on current and crossing rows only",
    }
    (data / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"analyzed_pairs={len(summary)} signals={counts}")


if __name__ == "__main__":
    main()
