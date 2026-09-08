from __future__ import annotations

import math
import statistics

import pandas as pd


def _bool_series(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
        .map({"true": True, "false": False, "1": True, "0": False})
        .fillna(False)
        .astype(bool)
    )


def add_snapshot_valuation_fields(snapshot: pd.DataFrame) -> pd.DataFrame:
    """Derive executable mid/IOPV diagnostics from a current ETF snapshot."""
    if snapshot.empty:
        return snapshot.copy()

    out = snapshot.copy()
    for column in ("last", "iopv", "bid1", "ask1"):
        if column not in out.columns:
            out[column] = pd.NA
        out[column] = pd.to_numeric(out[column], errors="coerce")

    valid_book = (
        out["bid1"].gt(0)
        & out["ask1"].gt(0)
        & out["ask1"].ge(out["bid1"])
    )
    quoted_mid = (out["bid1"] + out["ask1"]) / 2
    out["mid"] = quoted_mid.where(valid_book, out["last"])
    out["spread_bps"] = (
        (out["ask1"] - out["bid1"]) / quoted_mid * 10_000
    ).where(valid_book & quoted_mid.gt(0))

    valid_iopv = out["iopv"].gt(0)
    out["iopv_premium_pct"] = (
        (out["last"] / out["iopv"] - 1) * 100
    ).where(valid_iopv & out["last"].gt(0))
    out["mid_iopv_premium_pct"] = (
        (out["mid"] / out["iopv"] - 1) * 100
    ).where(valid_iopv & out["mid"].gt(0))
    out["current_anchor_status"] = "IOPV_MISSING"
    out.loc[valid_iopv, "current_anchor_status"] = "IOPV_AVAILABLE"
    return out


def build_official_nav_premium_history(
    prices: pd.DataFrame,
    nav: pd.DataFrame,
) -> pd.DataFrame:
    """Build same-date official-NAV premium history for diagnostics.

    This function intentionally does not claim point-in-time validity. Historical
    rows are marked PIT_VERIFIED only when the NAV input explicitly says so.
    """
    if prices.empty or nav.empty:
        return pd.DataFrame()

    px = prices.copy()
    nv = nav.copy()
    px["symbol"] = px["symbol"].astype("string")
    nv["symbol"] = nv["symbol"].astype("string")
    px["date"] = pd.to_datetime(px["date"], errors="coerce")
    nv["nav_date"] = pd.to_datetime(nv["nav_date"], errors="coerce")
    px["close"] = pd.to_numeric(px["close"], errors="coerce")
    nv["unit_nav"] = pd.to_numeric(nv["unit_nav"], errors="coerce")

    if "is_tradable" in px.columns:
        px = px.loc[_bool_series(px["is_tradable"])].copy()
    elif "volume" in px.columns:
        volume = pd.to_numeric(px["volume"], errors="coerce")
        px = px.loc[volume.gt(0)].copy()

    merged = px.merge(
        nv,
        left_on=["symbol", "date"],
        right_on=["symbol", "nav_date"],
        how="inner",
        suffixes=("_price", "_nav"),
    )
    merged = merged.loc[merged["close"].gt(0) & merged["unit_nav"].gt(0)].copy()
    if merged.empty:
        return merged

    merged["official_premium_pct"] = (merged["close"] / merged["unit_nav"] - 1) * 100
    merged["log_official_premium"] = (
        merged["close"] / merged["unit_nav"]
    ).map(math.log)

    if "pit_verified" in merged.columns:
        verified = _bool_series(merged["pit_verified"])
    else:
        verified = pd.Series(False, index=merged.index, dtype=bool)
    merged["pit_status"] = "PIT_UNVERIFIED"
    merged.loc[verified, "pit_status"] = "PIT_VERIFIED"
    return merged.sort_values(["date", "symbol"]).reset_index(drop=True)


def _rolling_prior_median(
    series: pd.Series,
    window: int,
    min_periods: int,
) -> pd.Series:
    return series.shift(1).rolling(window=window, min_periods=min_periods).median()


def _window_mad(values) -> float:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        return math.nan
    center = statistics.median(clean)
    return float(statistics.median(abs(value - center) for value in clean))


def _rolling_prior_mad(
    series: pd.Series,
    window: int,
    min_periods: int,
) -> pd.Series:
    return series.shift(1).rolling(
        window=window,
        min_periods=min_periods,
    ).apply(_window_mad, raw=True)


def compute_common_factor_residuals(
    premium_history: pd.DataFrame,
    *,
    baseline_window: int = 120,
    baseline_min_periods: int = 60,
    z_window: int = 60,
    z_min_periods: int = 60,
) -> pd.DataFrame:
    """Experimental no-lookahead common-premium-factor residual model.

    Cross-sectional common premium is removed first. Each ETF then gets a
    trailing structural-alpha estimate using only prior observations. The final
    idiosyncratic residual is suitable for cheap/expensive candidate generation,
    not as a replacement for the formal pair gate.
    """
    required = {"symbol", "date", "log_official_premium"}
    missing = required - set(premium_history.columns)
    if missing:
        raise ValueError(f"premium history missing columns: {sorted(missing)}")

    frame = premium_history.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["log_official_premium"] = pd.to_numeric(
        frame["log_official_premium"], errors="coerce"
    )
    frame = frame.dropna(subset=["symbol", "date", "log_official_premium"])
    frame = frame.sort_values(["symbol", "date"]).reset_index(drop=True)

    factor = frame.groupby("date")["log_official_premium"].median()
    factor.name = "common_premium_factor"
    frame = frame.join(factor, on="date")
    cross_section_size = frame.groupby("date")["symbol"].nunique()
    frame = frame.join(cross_section_size.rename("cross_section_size"), on="date")
    frame["raw_residual"] = (
        frame["log_official_premium"] - frame["common_premium_factor"]
    )

    frame["structural_alpha"] = frame.groupby("symbol", group_keys=False)[
        "raw_residual"
    ].transform(
        lambda series: _rolling_prior_median(
            series,
            baseline_window,
            baseline_min_periods,
        )
    )
    frame["idiosyncratic_residual"] = frame["raw_residual"] - frame["structural_alpha"]

    frame["residual_center"] = frame.groupby("symbol", group_keys=False)[
        "idiosyncratic_residual"
    ].transform(
        lambda series: _rolling_prior_median(series, z_window, z_min_periods)
    )
    frame["residual_mad"] = frame.groupby("symbol", group_keys=False)[
        "idiosyncratic_residual"
    ].transform(
        lambda series: _rolling_prior_mad(series, z_window, z_min_periods)
    )

    scale = 1.4826 * frame["residual_mad"]
    valid_scale = scale.gt(0.0001)
    frame["idiosyncratic_robust_z"] = (
        (frame["idiosyncratic_residual"] - frame["residual_center"]) / scale
    ).where(valid_scale)

    return frame.sort_values(["date", "symbol"]).reset_index(drop=True)


def latest_factor_ranking(factor_frame: pd.DataFrame) -> pd.DataFrame:
    """Return latest cross-sectional cheap-to-expensive residual ranking."""
    if factor_frame.empty:
        return factor_frame.copy()
    latest_date = pd.to_datetime(factor_frame["date"], errors="coerce").max()
    latest = factor_frame.loc[
        pd.to_datetime(factor_frame["date"], errors="coerce").eq(latest_date)
    ].copy()
    latest = latest.dropna(subset=["idiosyncratic_residual"])
    return latest.sort_values("idiosyncratic_residual").reset_index(drop=True)


def latest_factor_candidates(
    factor_frame: pd.DataFrame,
    *,
    n_each: int = 3,
) -> pd.DataFrame:
    """Generate experimental rotate-out/rotate-in candidates from residual tails."""
    ranking = latest_factor_ranking(factor_frame)
    if ranking.empty:
        return pd.DataFrame()

    n = min(max(n_each, 1), len(ranking) // 2)
    if n == 0:
        return pd.DataFrame()

    cheap = ranking.head(n)
    expensive = ranking.tail(n).sort_values("idiosyncratic_residual", ascending=False)
    rows: list[dict[str, object]] = []
    for _, out_row in expensive.iterrows():
        for _, in_row in cheap.iterrows():
            if out_row["symbol"] == in_row["symbol"]:
                continue
            rows.append(
                {
                    "date": out_row["date"],
                    "rotate_out": out_row["symbol"],
                    "rotate_in": in_row["symbol"],
                    "out_residual": out_row["idiosyncratic_residual"],
                    "in_residual": in_row["idiosyncratic_residual"],
                    "residual_gap": out_row["idiosyncratic_residual"]
                    - in_row["idiosyncratic_residual"],
                    "out_z": out_row.get("idiosyncratic_robust_z"),
                    "in_z": in_row.get("idiosyncratic_robust_z"),
                    "model_status": "EXPERIMENTAL_CANDIDATE_ONLY",
                }
            )
    return pd.DataFrame(rows).sort_values("residual_gap", ascending=False).reset_index(drop=True)
