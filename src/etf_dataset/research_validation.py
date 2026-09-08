from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


FORMAL_SIGNALS = {"TRADE", "STRONG TRADE"}


def validate_research_outputs(data_dir: str | Path) -> list[str]:
    data = Path(data_dir)
    errors: list[str] = []

    pair_path = data / "pair_analysis.csv"
    if not pair_path.exists() or pair_path.stat().st_size == 0:
        errors.append("pair_analysis.csv missing or empty")
        return errors
    pairs = pd.read_csv(pair_path, dtype={"pair": "string"})
    if pairs["pair"].duplicated().any():
        errors.append("pair_analysis contains duplicate pair rows")
    if len(pairs) > 66:
        errors.append(f"pair_analysis has impossible pair count {len(pairs)} > 66")

    score = pd.to_numeric(pairs.get("pair_score"), errors="coerce")
    if score.notna().any() and ((score < 0) | (score > 100)).any():
        errors.append("pair_score outside [0,100]")
    observations = pd.to_numeric(pairs.get("aligned_observations"), errors="coerce")
    formal_mask = pairs["signal"].isin(FORMAL_SIGNALS)
    if formal_mask.any() and observations.loc[formal_mask].lt(120).any():
        errors.append("formal pair signal with fewer than 120 aligned observations")

    if "gates" in pairs.columns:
        for _, row in pairs.loc[formal_mask].iterrows():
            try:
                gates = json.loads(row["gates"])
            except (TypeError, json.JSONDecodeError):
                errors.append(f"formal pair {row['pair']} has invalid gate JSON")
                continue
            failed = [name for name, passed in gates.items() if passed is not True]
            if failed:
                errors.append(
                    f"formal pair {row['pair']} has failed gates: {','.join(sorted(failed))}"
                )

    liquidity_path = data / "liquidity_scores.csv"
    if not liquidity_path.exists() or liquidity_path.stat().st_size == 0:
        errors.append("liquidity_scores.csv missing or empty")
    else:
        liquidity = pd.read_csv(liquidity_path, dtype={"symbol": "string"})
        lscore = pd.to_numeric(liquidity.get("liquidity_score"), errors="coerce")
        if lscore.notna().any() and ((lscore < 0) | (lscore > 100)).any():
            errors.append("liquidity score outside [0,100]")

    registry = data / "model_registry.json"
    if not registry.exists():
        errors.append("model_registry.json missing")
    else:
        payload = json.loads(registry.read_text())
        if not payload.get("models"):
            errors.append("model registry has no frozen models")

    events_path = data / "etf_events.csv"
    if not events_path.exists():
        errors.append("etf_events.csv missing")

    metadata_path = data / "product_quality.csv"
    if not metadata_path.exists():
        errors.append("product_quality.csv missing")

    return errors
