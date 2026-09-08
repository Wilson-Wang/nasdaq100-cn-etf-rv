from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd


def _bool_series(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
        .map({"true": True, "1": True, "false": False, "0": False})
        .fillna(False)
    )


def _robust_z(history: pd.Series, current: float) -> float | None:
    values = pd.to_numeric(history, errors="coerce").dropna()
    if len(values) < 60:
        return None
    values = values.tail(60)
    center = float(values.median())
    mad = float((values - center).abs().median())
    scale = 1.4826 * mad
    if not np.isfinite(scale) or scale < 0.0001:
        q25, q75 = values.quantile([0.25, 0.75])
        scale = float((q75 - q25) / 1.349)
    if not np.isfinite(scale) or scale < 0.0001:
        return None
    return float((current - center) / scale)


def _jump_flag(ratio: pd.Series) -> tuple[bool, float | None]:
    change = pd.to_numeric(ratio, errors="coerce").diff().dropna()
    if len(change) < 60:
        return False, None
    history = change.iloc[:-1].tail(120)
    current = float(change.iloc[-1])
    center = float(history.median())
    mad = float((history - center).abs().median())
    scale = 1.4826 * mad
    if not np.isfinite(scale) or scale < 0.0001:
        return False, None
    score = abs(current - center) / scale
    return bool(score >= 8.0), float(score)


def main() -> None:
    data = Path("data")
    prices = pd.read_csv(data / "etf_prices.csv", dtype={"symbol": "string"})
    manifest = json.loads((data / "manifest.json").read_text())
    symbols = [str(value) for value in manifest.get("universe", [])]
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    if "is_tradable" in prices.columns:
        prices = prices.loc[_bool_series(prices["is_tradable"])].copy()
    prices = prices.loc[prices["close"].gt(0)].copy()

    rows: list[dict[str, object]] = []
    for symbol_i, symbol_j in itertools.combinations(symbols, 2):
        i = (
            prices.loc[prices["symbol"].eq(symbol_i), ["date", "close"]]
            .drop_duplicates("date", keep="last")
            .rename(columns={"close": "close_i"})
        )
        j = (
            prices.loc[prices["symbol"].eq(symbol_j), ["date", "close"]]
            .drop_duplicates("date", keep="last")
            .rename(columns={"close": "close_j"})
        )
        aligned = i.merge(j, on="date", how="inner").sort_values("date")
        if aligned.empty:
            continue
        aligned["q"] = np.log(aligned["close_i"] / aligned["close_j"])
        latest = float(aligned.iloc[-1]["q"])
        z = _robust_z(aligned["q"].iloc[:-1], latest)
        jump, jump_score = _jump_flag(aligned["q"])
        rows.append(
            {
                "pair": f"{symbol_i}-{symbol_j}",
                "symbol_i": symbol_i,
                "symbol_j": symbol_j,
                "date": aligned.iloc[-1]["date"].strftime("%Y-%m-%d"),
                "aligned_observations": int(len(aligned)),
                "log_price_ratio": latest,
                "robust_z_60_tminus1": z,
                "latest_change_structural_jump_flag": jump,
                "latest_change_robust_magnitude": jump_score,
                "price_adjustment": "UNADJUSTED",
                "production_gate": False,
                "status": "DIAGNOSTIC_ONLY",
            }
        )

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(
            "robust_z_60_tminus1",
            key=lambda value: pd.to_numeric(value, errors="coerce").abs(),
            ascending=False,
            na_position="last",
        ).reset_index(drop=True)
    result.to_csv(data / "price_ratio_diagnostics.csv", index=False)
    if not result.empty:
        result.to_parquet(data / "price_ratio_diagnostics.parquet", index=False)
    status = {
        "status": "DIAGNOSTIC_ONLY",
        "production_gate": False,
        "pairs": int(len(result)),
        "structural_jump_flags": int(result["latest_change_structural_jump_flag"].sum())
        if not result.empty
        else 0,
        "limitation": (
            "Daily ETF prices are unadjusted. Price-ratio diagnostics must not be used as an "
            "independent trade model across dividends, splits, share conversions, or other "
            "corporate actions without verified adjustment/reset evidence."
        ),
    }
    (data / "price_ratio_diagnostic_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        f"price_ratio_diagnostics pairs={status['pairs']} "
        f"jump_flags={status['structural_jump_flags']} production_gate=false"
    )


if __name__ == "__main__":
    main()
