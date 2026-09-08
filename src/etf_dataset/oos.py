from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


def canonical_model_hash(spec: dict) -> str:
    payload = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_frozen_model(spec: dict, registry_path: str | Path) -> dict:
    """Register a model version once and reject silent parameter mutation."""
    path = Path(registry_path)
    registry = json.loads(path.read_text()) if path.exists() else {"models": {}}
    version = str(spec["model_version"])
    model_hash = canonical_model_hash(spec)
    existing = registry.setdefault("models", {}).get(version)
    if existing is not None and existing.get("model_hash") != model_hash:
        raise ValueError(
            f"frozen model {version} changed: {existing.get('model_hash')} != {model_hash}"
        )
    registry["models"][version] = {
        "model_hash": model_hash,
        "created_date": spec.get("created_date"),
        "training_end_date": spec.get("training_end_date"),
        "oos_start_date": spec.get("oos_start_date"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n")
    return registry["models"][version]


def append_oos_states(
    pair_analysis: pd.DataFrame,
    spec: dict,
    manifest_path: str | Path,
    output_path: str | Path,
) -> pd.DataFrame:
    """Persist the complete daily pair cross-section to prevent cherry-picking."""
    path = Path(output_path)
    if pair_analysis.empty:
        return pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame()
    frame = pair_analysis.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date", "pair"])
    oos_start = pd.Timestamp(spec["oos_start_date"])
    frame = frame.loc[frame["date"].ge(oos_start)].copy()
    if frame.empty:
        return pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame()

    keep = [
        "date",
        "pair",
        "symbol_i",
        "symbol_j",
        "common_nav_date",
        "aligned_observations",
        "z",
        "half_life",
        "adf_p",
        "kpss_p",
        "stability_ratio",
        "expected_convergence_5d",
        "net_expected_convergence_5d",
        "signal_count",
        "adjusted_win_rate_5d",
        "regime",
        "pair_score",
        "rotate_in",
        "rotate_out",
        "signal",
        "adf_fdr_q",
    ]
    current = frame[[column for column in keep if column in frame.columns]].copy()
    current = current.rename(columns={"date": "signal_date"})
    current["signal_date"] = current["signal_date"].dt.strftime("%Y-%m-%d")
    current.insert(0, "model_version", str(spec["model_version"]))
    current.insert(1, "model_hash", canonical_model_hash(spec))
    current.insert(2, "manifest_sha256", file_sha256(manifest_path))
    current["evaluation_status"] = "PENDING"
    current["realized_relative_return_5d"] = pd.NA
    current["realized_net_alpha_5d"] = pd.NA
    current["entry_date"] = pd.NA
    current["exit_date"] = pd.NA

    if path.exists() and path.stat().st_size > 0:
        existing = pd.read_csv(path, dtype={"model_version": "string", "pair": "string"})
        combined = pd.concat([existing, current], ignore_index=True, sort=False)
    else:
        combined = current
    combined = combined.drop_duplicates(
        ["model_version", "signal_date", "pair"], keep="first"
    ).sort_values(["signal_date", "pair"])
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(path, index=False)
    return combined.reset_index(drop=True)


def _prepared_prices(prices: pd.DataFrame, symbol: str) -> pd.DataFrame:
    group = prices.loc[prices["symbol"].astype("string").eq(symbol)].copy()
    group["date"] = pd.to_datetime(group["date"], errors="coerce")
    group["open"] = pd.to_numeric(group["open"], errors="coerce")
    group["close"] = pd.to_numeric(group["close"], errors="coerce")
    if "is_tradable" in group.columns:
        tradable = (
            group["is_tradable"].astype("string").str.lower().map(
                {"true": True, "1": True, "false": False, "0": False}
            ).fillna(False)
        )
        group = group.loc[tradable].copy()
    group = group.loc[group["open"].gt(0) & group["close"].gt(0)].copy()
    return group.sort_values("date").drop_duplicates("date", keep="last")


def evaluate_matured_oos(
    states: pd.DataFrame,
    prices: pd.DataFrame,
    spec: dict,
) -> pd.DataFrame:
    """Evaluate only frozen formal signals after five future aligned sessions exist."""
    if states.empty:
        return states
    out = states.copy()
    horizon = int(spec.get("parameters", {}).get("horizon", 5))
    cost = float(spec.get("parameters", {}).get("assumed_rotation_cost", 0.0015))
    formal = {"TRADE", "STRONG TRADE"}

    for idx, row in out.iterrows():
        if str(row.get("evaluation_status")) == "COMPLETE":
            continue
        if str(row.get("signal")) not in formal:
            out.at[idx, "evaluation_status"] = "NOT_FORMAL_SIGNAL"
            continue
        rotate_in = row.get("rotate_in")
        rotate_out = row.get("rotate_out")
        if pd.isna(rotate_in) or pd.isna(rotate_out):
            out.at[idx, "evaluation_status"] = "INVALID_DIRECTION"
            continue
        signal_date = pd.Timestamp(row["signal_date"])
        px_in = _prepared_prices(prices, str(rotate_in)).rename(
            columns={"open": "open_in", "close": "close_in"}
        )
        px_out = _prepared_prices(prices, str(rotate_out)).rename(
            columns={"open": "open_out", "close": "close_out"}
        )
        aligned = px_in[["date", "open_in", "close_in"]].merge(
            px_out[["date", "open_out", "close_out"]], on="date", how="inner"
        )
        future = aligned.loc[aligned["date"].gt(signal_date)].reset_index(drop=True)
        if len(future) < horizon:
            out.at[idx, "evaluation_status"] = "PENDING"
            continue
        entry = future.iloc[0]
        exit_row = future.iloc[horizon - 1]
        ret_in = float(exit_row["close_in"] / entry["open_in"] - 1)
        ret_out = float(exit_row["close_out"] / entry["open_out"] - 1)
        relative = ret_in - ret_out
        out.at[idx, "entry_date"] = entry["date"].strftime("%Y-%m-%d")
        out.at[idx, "exit_date"] = exit_row["date"].strftime("%Y-%m-%d")
        out.at[idx, "realized_relative_return_5d"] = relative
        out.at[idx, "realized_net_alpha_5d"] = relative - cost
        out.at[idx, "evaluation_status"] = "COMPLETE"
    return out
