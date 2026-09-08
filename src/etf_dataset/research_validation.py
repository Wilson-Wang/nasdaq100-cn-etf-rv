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
    pairs = pd.read_csv(pair_path, dtype={"pair": "string", "symbol_i": "string", "symbol_j": "string"})
    if pairs["pair"].duplicated().any():
        errors.append("pair_analysis contains duplicate pair rows")
    if len(pairs) > 66:
        errors.append(f"pair_analysis has impossible pair count {len(pairs)} > 66")

    manifest_path = data / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    universe = [str(value) for value in manifest.get("universe", [])]
    if universe:
        expected_pairs = len(universe) * (len(universe) - 1) // 2
        ready = manifest.get("quality", {}).get("formal_signal_ready_symbols", [])
        if len(ready) == len(universe) and len(pairs) != expected_pairs:
            errors.append(
                f"all symbols model-ready but pair_analysis has {len(pairs)} rows, expected {expected_pairs}"
            )

    score = pd.to_numeric(pairs.get("pair_score"), errors="coerce")
    if score.notna().any() and ((score < 0) | (score > 100)).any():
        errors.append("pair_score outside [0,100]")
    observations = pd.to_numeric(pairs.get("aligned_observations"), errors="coerce")
    formal_mask = pairs["signal"].isin(FORMAL_SIGNALS)
    if formal_mask.any() and observations.loc[formal_mask].lt(120).any():
        errors.append("formal pair signal with fewer than 120 aligned observations")

    # A pair cannot have more aligned observations than either leg has unique
    # tradable price dates. This catches accidental many-to-many/cartesian joins.
    prices_path = data / "etf_prices.csv"
    if prices_path.exists() and prices_path.stat().st_size:
        prices = pd.read_csv(prices_path, dtype={"symbol": "string"})
        prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
        if "is_tradable" in prices.columns:
            tradable = prices["is_tradable"].astype("string").str.lower().map(
                {"true": True, "1": True, "false": False, "0": False}
            ).fillna(False)
            prices = prices.loc[tradable].copy()
        unique_dates = prices.groupby("symbol")["date"].nunique().to_dict()
        for _, row in pairs.iterrows():
            upper = min(
                int(unique_dates.get(str(row["symbol_i"]), 0)),
                int(unique_dates.get(str(row["symbol_j"]), 0)),
            )
            aligned = pd.to_numeric(pd.Series([row.get("aligned_observations")]), errors="coerce").iloc[0]
            if pd.notna(aligned) and int(aligned) > upper:
                errors.append(
                    f"pair {row['pair']} aligned observations {int(aligned)} exceed price-date upper bound {upper}"
                )

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

    for required in (
        "etf_events.csv",
        "product_quality.csv",
        "product_value_ranking.csv",
        "factor_residual_status.json",
        "portfolio_status.json",
        "research_health.json",
        "execution_cost_model.json",
        "daily_report.md",
        "report_state.json",
    ):
        if not (data / required).exists():
            errors.append(f"{required} missing")

    portfolio_path = data / "portfolio_plan.csv"
    if not portfolio_path.exists():
        errors.append("portfolio_plan.csv missing")
    elif portfolio_path.stat().st_size:
        portfolio = pd.read_csv(portfolio_path, dtype={"rotate_in": "string", "rotate_out": "string"})
        if not portfolio.empty:
            if not portfolio["signal"].isin(FORMAL_SIGNALS).all():
                errors.append("portfolio plan contains non-formal pair signals")
            weight = pd.to_numeric(portfolio["allocation_weight"], errors="coerce")
            if weight.gt(0.4000001).any():
                errors.append("portfolio pair allocation exceeds 40% cap")
            legs = pd.concat([portfolio["rotate_in"], portfolio["rotate_out"]], ignore_index=True)
            if legs.duplicated().any():
                errors.append("portfolio plan contains shared ETF across selected pairs")
            if weight.sum() > 1.0000001:
                errors.append("portfolio total allocation exceeds 100%")

    return errors
