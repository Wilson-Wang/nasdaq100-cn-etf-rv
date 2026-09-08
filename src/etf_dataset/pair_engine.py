from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller, kpss

CHINA_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class PairEngineConfig:
    z_window: int = 60
    ar_window: int = 120
    horizon: int = 5
    entry_z: float = 2.0
    reset_z: float = 1.0
    min_scale: float = 0.0001
    max_half_life: float = 12.0
    min_net_expected_convergence: float = 0.008
    min_adjusted_win_rate: float = 0.65
    assumed_rotation_cost: float = 0.0015
    min_formal_observations: int = 120
    preferred_observations: int = 250


def _bool_series(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
        .map({"true": True, "false": False, "1": True, "0": False})
        .fillna(False)
        .astype(bool)
    )


def _safe_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _prepare_prices(prices: pd.DataFrame, symbol: str) -> pd.DataFrame:
    frame = prices.loc[prices["symbol"].astype("string").eq(symbol)].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ("open", "close", "amount", "volume"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "is_tradable" in frame.columns:
        frame = frame.loc[_bool_series(frame["is_tradable"])].copy()
    elif "volume" in frame.columns:
        frame = frame.loc[frame["volume"].gt(0)].copy()
    frame = frame.loc[frame["open"].gt(0) & frame["close"].gt(0)].copy()
    return frame.sort_values("date").drop_duplicates("date", keep="last")


def _prepare_nav(nav: pd.DataFrame, symbol: str) -> pd.DataFrame:
    frame = nav.loc[nav["symbol"].astype("string").eq(symbol)].copy()
    frame["nav_date"] = pd.to_datetime(frame["nav_date"], errors="coerce")
    frame["unit_nav"] = pd.to_numeric(frame["unit_nav"], errors="coerce")
    frame["available_at"] = pd.to_datetime(frame.get("available_at"), errors="coerce", utc=True)
    if "pit_usable" in frame.columns:
        usable = _bool_series(frame["pit_usable"])
    else:
        usable = frame["available_at"].notna()
    frame = frame.loc[usable & frame["unit_nav"].gt(0) & frame["available_at"].notna()].copy()
    return frame.sort_values(["nav_date", "available_at"]).drop_duplicates("nav_date", keep="last")


def _availability_frontier(common_nav: pd.DataFrame) -> pd.DataFrame:
    """Keep only rows that advance the latest common NAV date as information arrives."""
    if common_nav.empty:
        return common_nav
    ordered = common_nav.sort_values(["pair_available_at", "nav_date"])
    keep: list[int] = []
    best_date: pd.Timestamp | None = None
    for idx, row in ordered.iterrows():
        nav_date = row["nav_date"]
        if pd.isna(nav_date):
            continue
        if best_date is None or nav_date > best_date:
            keep.append(idx)
            best_date = nav_date
    return ordered.loc[keep].sort_values("pair_available_at").reset_index(drop=True)


def build_pair_spread(
    prices: pd.DataFrame,
    nav: pd.DataFrame,
    symbol_i: str,
    symbol_j: str,
) -> pd.DataFrame:
    """Build a PIT-safe common-NAV pair spread using tradable same-day ETF prices."""
    px_i = _prepare_prices(prices, symbol_i).rename(
        columns={"open": "open_i", "close": "close_i", "amount": "amount_i"}
    )
    px_j = _prepare_prices(prices, symbol_j).rename(
        columns={"open": "open_j", "close": "close_j", "amount": "amount_j"}
    )
    price_cols_i = [column for column in ("date", "open_i", "close_i", "amount_i") if column in px_i]
    price_cols_j = [column for column in ("date", "open_j", "close_j", "amount_j") if column in px_j]
    aligned = px_i[price_cols_i].merge(px_j[price_cols_j], on="date", how="inner")
    if aligned.empty:
        return aligned

    nav_i = _prepare_nav(nav, symbol_i).rename(
        columns={"unit_nav": "nav_i", "available_at": "available_at_i"}
    )
    nav_j = _prepare_nav(nav, symbol_j).rename(
        columns={"unit_nav": "nav_j", "available_at": "available_at_j"}
    )
    common = nav_i[["nav_date", "nav_i", "available_at_i"]].merge(
        nav_j[["nav_date", "nav_j", "available_at_j"]], on="nav_date", how="inner"
    )
    if common.empty:
        return pd.DataFrame()
    common["pair_available_at"] = common[["available_at_i", "available_at_j"]].max(axis=1)
    frontier = _availability_frontier(common)

    signal_cutoff = (
        aligned["date"].dt.tz_localize(CHINA_TZ)
        + pd.Timedelta(hours=23, minutes=59, seconds=59)
    ).dt.tz_convert("UTC")
    aligned = aligned.assign(signal_cutoff=signal_cutoff).sort_values("signal_cutoff")
    merged = pd.merge_asof(
        aligned,
        frontier.sort_values("pair_available_at"),
        left_on="signal_cutoff",
        right_on="pair_available_at",
        direction="backward",
    )
    merged = merged.dropna(subset=["nav_i", "nav_j"]).copy()
    if merged.empty:
        return merged
    merged["x_i"] = np.log(merged["close_i"] / merged["nav_i"])
    merged["x_j"] = np.log(merged["close_j"] / merged["nav_j"])
    merged["d"] = merged["x_i"] - merged["x_j"]
    merged["symbol_i"] = symbol_i
    merged["symbol_j"] = symbol_j
    merged["common_nav_date"] = pd.to_datetime(merged["nav_date"]).dt.strftime("%Y-%m-%d")
    return merged.sort_values("date").reset_index(drop=True)


def _robust_center_scale(history: pd.Series, min_scale: float) -> tuple[float | None, float | None, str]:
    values = pd.to_numeric(history, errors="coerce").dropna()
    if values.empty:
        return None, None, "NONE"
    center = float(values.median())
    mad = float((values - center).abs().median())
    scale = 1.4826 * mad
    if math.isfinite(scale) and scale >= min_scale:
        return center, scale, "MAD"
    q25, q75 = values.quantile([0.25, 0.75])
    scale = float((q75 - q25) / 1.349)
    if math.isfinite(scale) and scale >= min_scale:
        return center, scale, "IQR"
    return center, None, "DEGENERATE"


def fit_ar1(values: pd.Series) -> dict[str, float | None]:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if len(clean) < 3:
        return {"alpha": None, "phi": None, "mu": None, "half_life": None}
    x = clean[:-1]
    y = clean[1:]
    design = np.column_stack([np.ones(len(x)), x])
    alpha, phi = np.linalg.lstsq(design, y, rcond=None)[0]
    mu = None
    half_life = None
    if 0 < phi < 1:
        mu = float(alpha / (1 - phi))
        half_life = float(-math.log(2) / math.log(phi))
    return {
        "alpha": float(alpha),
        "phi": float(phi),
        "mu": mu,
        "half_life": half_life,
    }


def stationarity_stats(values: pd.Series) -> tuple[float | None, float | None]:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if len(clean) < 20 or np.nanstd(clean) == 0:
        return None, None
    try:
        adf_p = float(adfuller(clean, autolag="AIC")[1])
    except Exception:
        adf_p = None
    try:
        kpss_p = float(kpss(clean, regression="c", nlags="auto")[1])
    except Exception:
        kpss_p = None
    return adf_p, kpss_p


def _stability_ratio(history: pd.Series, window: int, max_half_life: float) -> tuple[float | None, int]:
    assessable = 0
    valid = 0
    for offset in (0, 20, 40, 60):
        end = len(history) - offset
        start = end - window
        if start < 0:
            continue
        sample = history.iloc[start:end]
        ar = fit_ar1(sample)
        adf_p, _ = stationarity_stats(sample)
        assessable += 1
        hl = ar["half_life"]
        if hl is not None and 0 < hl < max_half_life and adf_p is not None and adf_p <= 0.10:
            valid += 1
    return ((valid / assessable) if assessable else None), assessable


def add_pair_features(frame: pd.DataFrame, config: PairEngineConfig = PairEngineConfig()) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy().reset_index(drop=True)
    records: list[dict[str, Any]] = []
    d = pd.to_numeric(out["d"], errors="coerce")
    for idx, current in enumerate(d):
        z_history = d.iloc[max(0, idx - config.z_window):idx]
        center = scale = z = None
        scale_method = "NONE"
        if len(z_history.dropna()) >= config.z_window:
            center, scale, scale_method = _robust_center_scale(z_history, config.min_scale)
            if scale is not None and center is not None and pd.notna(current):
                z = float((current - center) / scale)

        ar = {"alpha": None, "phi": None, "mu": None, "half_life": None}
        adf_p = kpss_p = stability = expected = regime_b = None
        stability_windows = 0
        history = d.iloc[:idx]
        if len(history.dropna()) >= config.ar_window:
            ar_history = history.dropna().tail(config.ar_window)
            ar = fit_ar1(ar_history)
            adf_p, kpss_p = stationarity_stats(ar_history)
            stability, stability_windows = _stability_ratio(
                history.dropna(), config.ar_window, config.max_half_life
            )
            if ar["mu"] is not None and ar["phi"] is not None and pd.notna(current):
                expected_d = ar["mu"] + (ar["phi"] ** config.horizon) * (current - ar["mu"])
                expected = float(abs(current - expected_d))
            recent20 = history.dropna().tail(20)
            recent120 = history.dropna().tail(config.ar_window)
            if len(recent20) == 20 and len(recent120) == config.ar_window:
                med120 = float(recent120.median())
                mad120 = float((recent120 - med120).abs().median())
                denom = 1.4826 * mad120
                if denom >= config.min_scale:
                    regime_b = float(abs(float(recent20.median()) - med120) / denom)

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
    return pd.concat([out, pd.DataFrame(records)], axis=1)


def _event_direction(row: pd.Series) -> tuple[str, str] | None:
    d = _safe_float(row.get("d"))
    mu = _safe_float(row.get("mu"))
    if d is None or mu is None or d == mu:
        return None
    if d > mu:
        return str(row["symbol_j"]), str(row["symbol_i"])
    return str(row["symbol_i"]), str(row["symbol_j"])


def backtest_crossing_events(
    features: pd.DataFrame,
    config: PairEngineConfig = PairEngineConfig(),
) -> tuple[pd.DataFrame, dict[str, float | int | None]]:
    if features.empty:
        return pd.DataFrame(), _empty_backtest_metrics()
    frame = features.reset_index(drop=True)
    events: list[dict[str, Any]] = []
    armed = True
    active_until = -1
    prev_abs: float | None = None

    for idx, row in frame.iterrows():
        z = _safe_float(row.get("z"))
        if z is None:
            continue
        abs_z = abs(z)
        if not armed and idx > active_until and abs_z < config.reset_z:
            armed = True
        crossing = prev_abs is not None and prev_abs < config.entry_z and abs_z >= config.entry_z
        if armed and crossing and idx > active_until:
            direction = _event_direction(row)
            exit_idx = idx + config.horizon
            entry_idx = idx + 1
            if direction is not None and exit_idx < len(frame):
                rotate_in, rotate_out = direction
                in_is_i = rotate_in == str(row["symbol_i"])
                entry_open_in = float(frame.loc[entry_idx, "open_i" if in_is_i else "open_j"])
                entry_open_out = float(frame.loc[entry_idx, "open_j" if in_is_i else "open_i"])
                path: list[float] = []
                for path_idx in range(entry_idx, exit_idx + 1):
                    close_in = float(frame.loc[path_idx, "close_i" if in_is_i else "close_j"])
                    close_out = float(frame.loc[path_idx, "close_j" if in_is_i else "close_i"])
                    path.append((close_in / entry_open_in - 1) - (close_out / entry_open_out - 1))
                realized_relative = path[-1]
                realized_net = realized_relative - config.assumed_rotation_cost
                events.append(
                    {
                        "signal_date": row["date"],
                        "entry_date": frame.loc[entry_idx, "date"],
                        "exit_date": frame.loc[exit_idx, "date"],
                        "rotate_in": rotate_in,
                        "rotate_out": rotate_out,
                        "z": z,
                        "half_life": row.get("half_life"),
                        "adf_p": row.get("adf_p"),
                        "expected_convergence_5d": row.get("expected_convergence_5d"),
                        "realized_relative_return_5d": realized_relative,
                        "realized_net_alpha_5d": realized_net,
                        "mae_5d": min(path),
                        "mfe_5d": max(path),
                        "cost": config.assumed_rotation_cost,
                    }
                )
                armed = False
                active_until = exit_idx
        prev_abs = abs_z

    event_frame = pd.DataFrame(events)
    return event_frame, summarize_backtest(event_frame)


def _empty_backtest_metrics() -> dict[str, float | int | None]:
    return {
        "signal_count": 0,
        "success_count_5d": 0,
        "raw_win_rate_5d": None,
        "bayes_win_rate_5d": 0.5,
        "adjusted_win_rate_5d": 0.5,
        "median_realized_net_alpha_5d": None,
        "p25_realized_net_alpha_5d": None,
        "median_mae_5d": None,
        "median_mfe_5d": None,
    }


def summarize_backtest(events: pd.DataFrame) -> dict[str, float | int | None]:
    if events.empty:
        return _empty_backtest_metrics()
    alpha = pd.to_numeric(events["realized_net_alpha_5d"], errors="coerce").dropna()
    n = int(len(alpha))
    success = int(alpha.gt(0).sum())
    raw = success / n if n else None
    bayes = (success + 2) / (n + 4)
    adjusted = 0.5 + (bayes - 0.5) * min(1.0, n / 30)
    return {
        "signal_count": n,
        "success_count_5d": success,
        "raw_win_rate_5d": raw,
        "bayes_win_rate_5d": bayes,
        "adjusted_win_rate_5d": adjusted,
        "median_realized_net_alpha_5d": float(alpha.median()) if n else None,
        "p25_realized_net_alpha_5d": float(alpha.quantile(0.25)) if n else None,
        "median_mae_5d": float(pd.to_numeric(events["mae_5d"]).median()) if n else None,
        "median_mfe_5d": float(pd.to_numeric(events["mfe_5d"]).median()) if n else None,
    }


def benjamini_hochberg(pvalues: list[float | None]) -> list[float | None]:
    valid = [(idx, p) for idx, p in enumerate(pvalues) if p is not None and math.isfinite(p)]
    result: list[float | None] = [None] * len(pvalues)
    if not valid:
        return result
    ranked = sorted(valid, key=lambda item: item[1])
    m = len(ranked)
    running = 1.0
    for rank in range(m, 0, -1):
        idx, p = ranked[rank - 1]
        running = min(running, p * m / rank)
        result[idx] = min(1.0, running)
    return result


def pair_score(
    latest: pd.Series,
    backtest: dict[str, float | int | None],
    liquidity_score: float,
    config: PairEngineConfig,
) -> float:
    z = abs(_safe_float(latest.get("z")) or 0.0)
    m_score = float(np.clip((z - 1.5) / 1.5 * 100, 0, 100))
    hl = _safe_float(latest.get("half_life"))
    hl_score = float(np.clip((12 - hl) / 10 * 100, 0, 100)) if hl is not None else 0.0
    adf_p = _safe_float(latest.get("adf_p"))
    adf_score = 100.0 if adf_p is not None and adf_p <= 0.05 else 60.0 if adf_p is not None and adf_p <= 0.10 else 0.0
    stability = _safe_float(latest.get("stability_ratio"))
    if stability is None:
        r_score = 0.625 * hl_score + 0.375 * adf_score
    else:
        r_score = 0.50 * hl_score + 0.30 * adf_score + 0.20 * stability * 100
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
        mae_score = float(np.clip(1 - abs(float(median_mae)) / max(expected, 0.001), 0, 1) * 100)
        h_score = 0.60 * win_score + 0.20 * sample_score + 0.20 * mae_score
    regime = str(latest.get("regime") or "UNKNOWN")
    g_score = {"NORMAL": 100, "CAUTION": 70, "WATCH_ONLY": 40, "DISABLED": 0}.get(regime, 30)
    score = 0.20 * m_score + 0.25 * r_score + 0.25 * e_score + 0.15 * h_score + 0.10 * liquidity_score + 0.05 * g_score
    return float(np.clip(score, 0, 100))


def _primary_market_blocks(direction: tuple[str, str] | None, quality: dict[str, Any]) -> bool:
    if direction is None:
        return True
    rotate_in, rotate_out = direction
    out_pm = quality.get(rotate_out, {}).get("primary_market", {})
    in_pm = quality.get(rotate_in, {}).get("primary_market", {})
    return out_pm.get("creation_state") == "RESTRICTED" or in_pm.get("redemption_state") == "RESTRICTED"


def classify_latest_signal(
    latest: pd.Series,
    backtest: dict[str, float | int | None],
    model_quality: dict[str, Any],
    execution_ready: set[str],
    liquidity_score: float,
    config: PairEngineConfig = PairEngineConfig(),
) -> dict[str, Any]:
    symbol_i = str(latest["symbol_i"])
    symbol_j = str(latest["symbol_j"])
    direction = _event_direction(latest)
    z = _safe_float(latest.get("z"))
    hl = _safe_float(latest.get("half_life"))
    adf_p = _safe_float(latest.get("adf_p"))
    stability = _safe_float(latest.get("stability_ratio"))
    expected = _safe_float(latest.get("expected_convergence_5d"))
    net_expected = None if expected is None else expected - config.assumed_rotation_cost
    adjusted = float(backtest.get("adjusted_win_rate_5d") or 0.5)
    regime = str(latest.get("regime") or "UNKNOWN")

    model_ready = all(
        model_quality.get(symbol, {}).get("formal_signal_ready") is True
        for symbol in (symbol_i, symbol_j)
    )
    execution_ok = symbol_i in execution_ready and symbol_j in execution_ready
    stationarity_ok = bool(
        adf_p is not None
        and (adf_p <= 0.05 or (adf_p <= 0.10 and stability is not None and stability >= 0.75))
    )
    regime_ok = regime not in {"WATCH_ONLY", "DISABLED", "UNKNOWN"} and not _primary_market_blocks(
        direction, model_quality
    )
    gates = {
        "z": z is not None and abs(z) >= config.entry_z,
        "half_life": hl is not None and 0 < hl < config.max_half_life,
        "stationarity": stationarity_ok,
        "expected_edge": net_expected is not None and net_expected >= config.min_net_expected_convergence,
        "historical_win_rate": adjusted >= config.min_adjusted_win_rate,
        "model_readiness": model_ready,
        "execution_readiness": execution_ok,
        "regime": regime_ok,
    }
    score = pair_score(latest, backtest, liquidity_score, config)
    if all(gates.values()):
        signal = "STRONG TRADE" if score >= 85 and (abs(z or 0) >= 2.5 or (net_expected or 0) >= 0.012) else "TRADE"
    elif not model_ready or z is None or hl is None:
        signal = "INSUFFICIENT_DATA"
    elif not execution_ok or not regime_ok or abs(z) >= 1.5:
        signal = "WATCH"
    else:
        signal = "NO TRADE"
    return {
        "signal": signal,
        "rotate_in": direction[0] if direction else None,
        "rotate_out": direction[1] if direction else None,
        "net_expected_convergence_5d": net_expected,
        "pair_score": score,
        "gates": gates,
    }


def analyze_universe(
    prices: pd.DataFrame,
    nav: pd.DataFrame,
    symbols: list[str],
    manifest: dict[str, Any],
    execution_report: dict[str, Any],
    config: PairEngineConfig = PairEngineConfig(),
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    rows: list[dict[str, Any]] = []
    event_tables: dict[str, pd.DataFrame] = {}
    execution_ready = set(execution_report.get("execution_ready_symbols", []))
    model_quality = manifest.get("quality", {}).get("by_symbol", {})

    for symbol_i, symbol_j in itertools.combinations(symbols, 2):
        spread = build_pair_spread(prices, nav, symbol_i, symbol_j)
        features = add_pair_features(spread, config)
        if features.empty:
            continue
        events, bt = backtest_crossing_events(features, config)
        latest = features.iloc[-1]
        pair_key = f"{symbol_i}-{symbol_j}"
        event_tables[pair_key] = events
        liquidity = 50.0
        result = classify_latest_signal(
            latest, bt, model_quality, execution_ready, liquidity, config
        )
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
                "z": latest.get("z"),
                "half_life": latest.get("half_life"),
                "adf_p": latest.get("adf_p"),
                "kpss_p": latest.get("kpss_p"),
                "stability_ratio": latest.get("stability_ratio"),
                "expected_convergence_5d": latest.get("expected_convergence_5d"),
                "net_expected_convergence_5d": result["net_expected_convergence_5d"],
                "signal_count": bt["signal_count"],
                "adjusted_win_rate_5d": bt["adjusted_win_rate_5d"],
                "median_realized_net_alpha_5d": bt["median_realized_net_alpha_5d"],
                "p25_realized_net_alpha_5d": bt["p25_realized_net_alpha_5d"],
                "regime": latest.get("regime"),
                "pair_score": result["pair_score"],
                "rotate_in": result["rotate_in"],
                "rotate_out": result["rotate_out"],
                "signal": result["signal"],
                "gates": result["gates"],
            }
        )

    summary = pd.DataFrame(rows)
    if not summary.empty:
        summary["adf_fdr_q"] = benjamini_hochberg(
            [_safe_float(value) for value in summary["adf_p"]]
        )
        summary = summary.sort_values(["pair_score", "z"], ascending=[False, False]).reset_index(drop=True)
    return summary, event_tables
