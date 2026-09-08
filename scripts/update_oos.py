from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from etf_dataset.oos import append_oos_states, evaluate_matured_oos, register_frozen_model


def main() -> None:
    root = Path(".")
    data = root / "data"
    spec = json.loads((root / "config" / "model_v1.json").read_text())
    registration = register_frozen_model(spec, data / "model_registry.json")

    pair_path = data / "pair_analysis.csv"
    if not pair_path.exists():
        raise SystemExit("pair_analysis.csv is required before OOS update")
    pairs = pd.read_csv(pair_path, dtype={"pair": "string", "symbol_i": "string", "symbol_j": "string"})
    states_path = data / "oos_pair_states.csv"
    states = append_oos_states(pairs, spec, data / "manifest.json", states_path)

    prices = pd.read_csv(data / "etf_prices.csv", dtype={"symbol": "string"})
    states = evaluate_matured_oos(states, prices, spec)
    states.to_csv(states_path, index=False)
    if not states.empty:
        states.to_parquet(states_path.with_suffix(".parquet"), index=False)

    manifest_path = data / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    formal = states[states["signal"].isin(["TRADE", "STRONG TRADE"])] if not states.empty else states
    complete = formal[formal["evaluation_status"] == "COMPLETE"] if not formal.empty else formal
    manifest["oos_status"] = {
        "model_version": spec["model_version"],
        "model_hash": registration["model_hash"],
        "training_end_date": spec["training_end_date"],
        "oos_start_date": spec["oos_start_date"],
        "recorded_pair_states": int(len(states)),
        "formal_signals": int(len(formal)),
        "completed_formal_signals": int(len(complete)),
        "frozen_model_guard": True,
        "selection_policy": "record full daily 66-pair cross-section, not only winners",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(
        f"oos model={spec['model_version']} states={len(states)} "
        f"formal={len(formal)} complete={len(complete)}"
    )


if __name__ == "__main__":
    main()
