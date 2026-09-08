from __future__ import annotations

import itertools
from typing import Any

import numpy as np
import pandas as pd

from .pair_engine import (
    PairEngineConfig,
    _robust_center_scale,
    _safe_float,
    _stability_ratio,
    backtest_crossing_events,
    benjamini_hochberg,
    build_pair_spread,
    fit_ar1,
    stationarity_stats,
)


def add_sparse_pair_features(
    frame: pd.DataFrame,
    config: PairEngineConfig = PairEngineConfig(),
) -> pd.DataFrame:
    """Compute cheap daily features and expensive tests only where decision-relevant.

    AR(1) is inexpensive and is fit walk-forward once 120 prior observations are
    available. ADF/KPSS/stability are run only on threshold-crossing candidates
    and the latest row. This preserves event/current decision diagnostics while
    avoiding thousands of redundant stationarity tests per daily refresh.
    """
    if frame.empty:
        return frame.copy()
    out = frame.copy().reset_index(drop=True)
    d = pd.to_numeric(out["d"], errors="coerce")
    records: list[dict[str, Any]] = []
    previous_abs_z: float | None = None

    for idx, current in enumerate(d):
        z_history = d.iloc[max(0, idx - config.z_window):idx]
        center = scale = z = None
        scale_method = "NONE"
        if len(z_history.dropna()) >= config.z_window:
            center, scale, scale_method = _robust_center_scale(z_history, config.min_scale)
            if scale is not None and center is not None and pd.notna(current):
                z = float((current - center) / scale)

        history = d.iloc[:idx].dropna()
        ar = {"alpha": None, "phi": None, "mu": None, "half_life": None}
        expected = regime_b = None
        if len(history) >= config.ar_window:
            ar_history = history.tail(config.ar_window)
            ar = fit_ar1(ar_history)
            if ar["mu"] is not None and ar["phi"] is not None and pd.notna(current):
                expected_d = ar["mu"] + (ar["phi"] ** config.horizon) * (current - ar["mu"])
                expected = float(abs(current - expected_d))
            recent20 = history.tail(20)
            recent120 = history.tail(config.ar_window)
            med120 = float(recent120.median())
            mad120 = float((recent120 - med120).abs().median())
            denom = 1.4826 * mad120
            if len(recent20) == 20 and denom >= config.min_scale:
                regime_b = float(abs(float(recent20.median()) - med120) / denom)

        abs_z = abs(z) if z is not None else None
        crossing = bool(
            abs_z is not None
            and previous_abs_z is not None
            and previous_abs_z < config.entry_z
            and abs_z >= config.entry_z
        )
        decision_row = idx == len(out) - 1 or crossing
        adf_p = kpss_p = stability = None
        stability_windows = 0
        if decision_row and len(history) >= config.ar_window:
            ar_history = history.tail(config.ar_window)
            adf_p, kpss_p = stationarity_stats(ar_history)
            stability, stability_windows = _stability_ratio(
                history, config.ar_window, config.max_half_life
            )

        if regime_b is None:
            regime = "UNKNOWN"
        elif regime_b < 1:
            regime = "NORMAL"
        elif regime_b < 2:
            regime = "CAUTION"
        elif regime_b <= 2.5:
            regime = "WATCH_ONLY"
        else:
            regime = "DISABLED"

        records.append(
            {
                "median60": center,
                "robust_scale": scale,
                "scale_method": scale_method,
                "z": z,
                "alpha": ar["alpha"],
                "phi": ar["phi"],
                "mu": ar["mu"],
                "half_life": ar["half_life"],
                "adf_p": adf_p,
                "kpss_p": kpss_p,
                "stability_ratio": stability,
                "stability_windows": stability_windows,
                "expected_convergence_5d": expected,
                "regime_b": regime_b,
                "regime": regime,
            }
        )
        if abs_z is not None:
            previous_abs_z = abs_z
    return pd.concat([out, pd.DataFrame(records)], axis=1)


def _direction(latest: pd.Series) -> tuple[str, str] | None:
    d = _safe_float(latest.get("d"))
    mu = _safe_float(latest.get("mu"))
    if d is None or mu is None or d == mu:
        return None
    if d > mu:
        return str(latest["symbol_j"]), str(latest["symbol_i"])
    return str(latest["symbol_i"]), str(latest["symbol_j"])


def _primary_market_ok(
    direction: tuple[str, str] | None,
    model_quality: dict[str, Any],
) -> bool:
    if direction is None:
        return False
    rotate_in, rotate_out = direction
    out_pm = model_quality.get(rotate_out, {}).get("primary_market", {})
    in_pm = model_quality.get(rotate_in, {}).get("primary_market", {})
    if out_pm.get("creation_state") == "RESTRICTED":
        return False
    if in_pm.get("redemption_state") == "RESTRICTED":
        return False
    return True


def _pair_score(
    latest: pd.Series,
    backtest: dict[str, float | int | None],
    liquidity_score: float | None,
    config: PairEngineConfig,
) -> tuple[float, float]:
    z = abs(_safe_float(latest.get("z")) or 0.0)
    m_score = float(np.clip((z - 1.5) / 1.5 * 100, 0, 100))
    hl = _safe_float(latest.get("half_life"))
    hl_score = float(np.clip((12 - hl) / 10 * 100, 0, 100)) if hl is not None else 0.0
    adf_p = _safe_float(latest.get("adf_p"))
    adf_score = (
        100.0
        if adf_p is not None and adf_p <= 0.05
        else 60.0
        if adf_p is not None and adf_p <= 0.10
        else 0.0
    )
    stability = _safe_float(latest.get("stability_ratio"))
    r_score = (
        0.625 * hl_score + 0.375 * adf_score
        if stability is None
        else 0.50 * hl_score + 0.30 * adf_score + 0.20 * stability * 100
    )
    expected = _safe_float(latest.get("expected_convergence_5d")) or 0.0
    net_expected = expected - config.assumed_rotation_cost
    e_score = float(np.clip(net_expected / 0.012 * 100, 0, 100))
    adjusted = float(backtest.get("adjusted_win_rate_5d") or 0.5)
    win_score = float(np.clip((adjusted - 0.50) / 0.25 * 100, 0, 100))
    sample_score = min(float(backtest.get("signal_count") or 0) / 30, 1.0) * 100
    median_mae = backtest.get("median_mae_5d")
    if median_mae is None:
        h_score = 0.75 * win_score + 0.25 * sample_score
    else:
        mae_score = float(
            np.clip(1 - abs(float(median_mae)) / max(expected, 0.001), 0, 1) * 100
        )
        h_score = 0.60 * win_score + 0.20 * sample_score + 0.20 * mae_score
    g_score = {"NORMAL": 100, "CAUTION": 70, "WATCH_ONLY": 40, "DISABLED": 0}.get(
        str(latest.get("regime") or "UNKNOWN"), 30
    )

    components = [(m_score, 0.20), (r_score, 0.25), (e_score, 0.25), (h_score, 0.15), (g_score, 0.05)]
    if liquidity_score is not None:
        components.append((float(liquidity_score), 0.10))
    numerator = sum(value * weight for value, weight in components)
    denominator = sum(weight for _, weight in components)
    return float(np.clip(numerator / denominator, 0, 100)), denominator


def analyze_universe_v1(
    prices: pd.DataFrame,
    nav: pd.DataFrame,
    symbols: list[str],
    manifest: dict[str, Any],
    execution_report: dict[str, Any],
    liquidity_scores: dict[str, float | None],
    config: PairEngineConfig = PairEngineConfig(),
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    rows: list[dict[str, Any]] = []
    event_tables: dict[str, pd.DataFrame] = {}
    execution_ready = set(execution_report.get("execution_ready_symbols", []))
    model_quality = manifest.get("quality", {}).get("by_symbol", {})

    for symbol_i, symbol_j in itertools.combinations(symbols, 2):
        spread = build_pair_spread(prices, nav, symbol_i, symbol_j)
        features = add_sparse_pair_features(spread, config)
        if features.empty:
            continue
        events, backtest = backtest_crossing_events(features, config)
        latest = features.iloc[-1]
        direction = _direction(latest)
        pair_key = f"{symbol_i}-{symbol_j}"
        event_tables[pair_key] = events

        li = liquidity_scores.get(symbol_i)
        lj = liquidity_scores.get(symbol_j)
        pair_liquidity = min(li, lj) if li is not None and lj is not None else None
        score, score_coverage = _pair_score(latest, backtest, pair_liquidity, config)
        z = _safe_float(latest.get("z"))
        hl = _safe_float(latest.get("half_life"))
        adf_p = _safe_float(latest.get("adf_p"))
        stability = _safe_float(latest.get("stability_ratio"))
        expected = _safe_float(latest.get("expected_convergence_5d"))
        net_expected = None if expected is None else expected - config.assumed_rotation_cost
        adjusted = float(backtest.get("adjusted_win_rate_5d") or 0.5)
        model_ready = all(
            model_quality.get(symbol, {}).get("formal_signal_ready") is True
            for symbol in (symbol_i, symbol_j)
        )
        execution_ok = symbol_i in execution_ready and symbol_j in execution_ready
        stationarity_ok = bool(
            adf_p is not None
            and (
                adf_p <= 0.05
                or (adf_p <= 0.10 and stability is not None and stability >= 0.75)
            )
        )
        regime = str(latest.get("regime") or "UNKNOWN")
        regime_ok = regime not in {"WATCH_ONLY", "DISABLED", "UNKNOWN"} and _primary_market_ok(
            direction, model_quality
        )
        gates = {
            "aligned_history": len(features) >= config.min_formal_observations,
            "z": z is not None and abs(z) >= config.entry_z,
            "half_life": hl is not None and 0 < hl < config.max_half_life,
            "stationarity": stationarity_ok,
            "expected_edge": net_expected is not None
            and net_expected >= config.min_net_expected_convergence,
            "historical_win_rate": adjusted >= config.min_adjusted_win_rate,
            "model_readiness": model_ready,
            "execution_readiness": execution_ok,
            "regime": regime_ok,
        }
        if all(gates.values()):
            signal = (
                "STRONG TRADE"
                if score >= 85
                and (abs(z or 0) >= 2.5 or (net_expected or 0) >= 0.012)
                else "TRADE"
            )
        elif not model_ready or z is None or hl is None or len(features) < config.min_formal_observations:
            signal = "INSUFFICIENT_DATA"
        elif not execution_ok or not regime_ok or abs(z) >= 1.5:
            signal = "WATCH"
        else:
            signal = "NO TRADE"

        rows.append(
            {
                "pair": pair_key,
                "symbol_i": symbol_i,
                "symbol_j": symbol_j,
                "date": latest["date"],
                "common_nav_date": latest.get("common_nav_date"),
                "aligned_observations": len(features),
                "d": latest.get("d"),
                "median60": latest.get("median60"),
                "z": z,
                "half_life": hl,
                "adf_p": adf_p,
                "kpss_p": latest.get("kpss_p"),
                "stability_ratio": stability,
                "expected_convergence_5d": expected,
                "assumed_rotation_cost": config.assumed_rotation_cost,
                "cost_method": "ASSUMED_COST",
                "net_expected_convergence_5d": net_expected,
                "signal_count": backtest["signal_count"],
                "adjusted_win_rate_5d": adjusted,
                "median_realized_net_alpha_5d": backtest["median_realized_net_alpha_5d"],
                "p25_realized_net_alpha_5d": backtest["p25_realized_net_alpha_5d"],
                "regime": regime,
                "pair_liquidity_score": pair_liquidity,
                "pair_score_coverage": score_coverage,
                "pair_score": score,
                "rotate_in": direction[0] if direction else None,
                "rotate_out": direction[1] if direction else None,
                "signal": signal,
                "gates": gates,
            }
        )

    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary, event_tables

    summary["adf_fdr_q"] = benjamini_hochberg(
        [_safe_float(value) for value in summary["adf_p"]]
    )
    # Multiple-testing control is an additional formal gate, never a way to
    # manufacture a signal. Current formal candidates require q <= 10%.
    for idx, row in summary.iterrows():
        q = _safe_float(row.get("adf_fdr_q"))
        gates = dict(row["gates"])
        gates["adf_fdr"] = q is not None and q <= 0.10
        summary.at[idx, "gates"] = gates
        if row["signal"] in {"TRADE", "STRONG TRADE"} and not gates["adf_fdr"]:
            summary.at[idx, "signal"] = "WATCH"
    summary = summary.sort_values(["pair_score", "z"], ascending=[False, False]).reset_index(
        drop=True
    )
    return summary, event_tables
