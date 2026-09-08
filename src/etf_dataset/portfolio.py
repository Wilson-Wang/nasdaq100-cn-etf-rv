from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PortfolioConfig:
    max_pairs: int = 3
    max_pair_weight: float = 0.40
    max_symbol_weight: float = 0.40
    min_pair_score: float = 75.0
    min_net_edge: float = 0.008


def _safe_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    value = float(value)
    return value if np.isfinite(value) else None


def build_portfolio_plan(
    pair_analysis: pd.DataFrame,
    config: PortfolioConfig = PortfolioConfig(),
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build a conservative research portfolio from formal pair signals.

    This is a portfolio research layer, not an execution instruction. It prevents
    the same ETF from appearing in multiple simultaneous rotation pairs and caps
    any single selected pair. Only already-formal TRADE/STRONG TRADE rows are
    eligible; this layer never upgrades a WATCH/NO TRADE signal.
    """
    columns = [
        "rank",
        "pair",
        "rotate_in",
        "rotate_out",
        "signal",
        "pair_score",
        "net_expected_convergence_5d",
        "pair_liquidity_score",
        "raw_strength",
        "allocation_weight",
        "selection_reason",
    ]
    if pair_analysis.empty:
        return pd.DataFrame(columns=columns), {
            "status": "NO_FORMAL_SIGNALS",
            "eligible_pairs": 0,
            "selected_pairs": 0,
            "conflict_rejections": 0,
        }

    frame = pair_analysis.copy()
    formal = frame["signal"].astype("string").isin(["TRADE", "STRONG TRADE"])
    frame = frame.loc[formal].copy()
    if frame.empty:
        return pd.DataFrame(columns=columns), {
            "status": "NO_FORMAL_SIGNALS",
            "eligible_pairs": 0,
            "selected_pairs": 0,
            "conflict_rejections": 0,
        }

    frame["pair_score"] = pd.to_numeric(frame["pair_score"], errors="coerce")
    frame["net_expected_convergence_5d"] = pd.to_numeric(
        frame["net_expected_convergence_5d"], errors="coerce"
    )
    frame["pair_liquidity_score"] = pd.to_numeric(
        frame.get("pair_liquidity_score"), errors="coerce"
    )
    frame = frame.loc[
        frame["pair_score"].ge(config.min_pair_score)
        & frame["net_expected_convergence_5d"].ge(config.min_net_edge)
        & frame["rotate_in"].notna()
        & frame["rotate_out"].notna()
    ].copy()
    eligible_count = int(len(frame))
    if frame.empty:
        return pd.DataFrame(columns=columns), {
            "status": "NO_PORTFOLIO_ELIGIBLE_SIGNALS",
            "eligible_pairs": 0,
            "selected_pairs": 0,
            "conflict_rejections": 0,
        }

    # Edge dominates. Pair score and liquidity only modulate ranking. Missing
    # liquidity is penalized rather than imputed as a favorable neutral score.
    liq = frame["pair_liquidity_score"].fillna(0).clip(0, 100) / 100.0
    score = frame["pair_score"].clip(0, 100) / 100.0
    edge = frame["net_expected_convergence_5d"].clip(lower=0)
    frame["raw_strength"] = edge * score * (0.5 + 0.5 * liq)
    frame = frame.sort_values(
        ["raw_strength", "pair_score"], ascending=[False, False]
    ).reset_index(drop=True)

    selected_rows: list[pd.Series] = []
    used_symbols: set[str] = set()
    conflict_rejections = 0
    for _, row in frame.iterrows():
        symbol_in = str(row["rotate_in"])
        symbol_out = str(row["rotate_out"])
        if symbol_in in used_symbols or symbol_out in used_symbols:
            conflict_rejections += 1
            continue
        selected_rows.append(row)
        used_symbols.update([symbol_in, symbol_out])
        if len(selected_rows) >= config.max_pairs:
            break

    if not selected_rows:
        return pd.DataFrame(columns=columns), {
            "status": "ALL_SIGNALS_CONFLICTED",
            "eligible_pairs": eligible_count,
            "selected_pairs": 0,
            "conflict_rejections": conflict_rejections,
        }

    selected = pd.DataFrame(selected_rows).copy().reset_index(drop=True)
    strengths = selected["raw_strength"].clip(lower=0)
    if float(strengths.sum()) <= 0:
        weights = np.repeat(1.0 / len(selected), len(selected))
    else:
        weights = (strengths / strengths.sum()).to_numpy(dtype=float)

    # Cap and renormalize iteratively. With the default three-pair limit and a
    # 40% cap this produces a diversified research allocation when possible.
    capped = np.minimum(weights, config.max_pair_weight)
    if capped.sum() > 0:
        capped = capped / capped.sum()
    if len(capped) > 1 and capped.max() > config.max_pair_weight:
        excess = capped - np.minimum(capped, config.max_pair_weight)
        capped = np.minimum(capped, config.max_pair_weight)
        room = np.maximum(config.max_pair_weight - capped, 0)
        if room.sum() > 0 and excess.sum() > 0:
            capped += room / room.sum() * excess.sum()
    selected["allocation_weight"] = capped
    selected["rank"] = np.arange(1, len(selected) + 1)
    selected["selection_reason"] = "formal_signal_no_symbol_conflict"

    # The no-shared-symbol rule already enforces the symbol cap for one rotation
    # leg. Keep the cap in metadata so future portfolio extensions remain bound.
    output = selected[[column for column in columns if column in selected.columns]].copy()
    return output, {
        "status": "READY",
        "eligible_pairs": eligible_count,
        "selected_pairs": int(len(output)),
        "conflict_rejections": conflict_rejections,
        "max_pairs": config.max_pairs,
        "max_pair_weight": config.max_pair_weight,
        "max_symbol_weight": config.max_symbol_weight,
        "semantics": "research_rotation_allocation_not_trade_execution",
    }
