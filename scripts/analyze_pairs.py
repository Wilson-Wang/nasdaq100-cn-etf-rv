from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from etf_dataset.pair_engine import PairEngineConfig, analyze_universe


def main() -> None:
    data = Path("data")
    prices = pd.read_csv(data / "etf_prices.csv", dtype={"symbol": "string"})
    nav = pd.read_csv(data / "etf_nav.csv", dtype={"symbol": "string"})
    manifest = json.loads((data / "manifest.json").read_text())
    execution_path = data / "execution_readiness.json"
    execution = json.loads(execution_path.read_text()) if execution_path.exists() else {}
    # Pair Engine v1 is an EOD model with earliest execution at t+1. Reuse the
    # engine's execution-ready field with the stricter semantic supplied by the
    # EOD report: same-day activity is required now and a live book must be
    # re-checked at actual next-session entry.
    if execution.get("next_session_eligible_symbols") is not None:
        execution = dict(execution)
        execution["execution_ready_symbols"] = execution["next_session_eligible_symbols"]
    symbols = [str(value) for value in manifest.get("universe", [])]

    summary, event_tables = analyze_universe(
        prices,
        nav,
        symbols,
        manifest,
        execution,
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
    print(f"analyzed_pairs={len(summary)} signals={counts}")


if __name__ == "__main__":
    main()
