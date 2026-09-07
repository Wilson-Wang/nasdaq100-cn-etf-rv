from __future__ import annotations

from pathlib import Path

import pandas as pd


class ValidationError(ValueError):
    pass


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"symbol": "string"})


def validate_prices(path: str | Path) -> list[str]:
    df = _load(Path(path))
    warnings: list[str] = []
    if df.empty:
        warnings.append("price dataset is empty")
        return warnings

    required = {"symbol", "date", "open", "high", "low", "close", "source"}
    missing = required - set(df.columns)
    if missing:
        raise ValidationError(f"prices missing columns: {sorted(missing)}")
    if df.duplicated(["symbol", "date"]).any():
        raise ValidationError("prices contain duplicate symbol/date keys")

    numeric = df[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    if (numeric < 0).any().any():
        raise ValidationError("prices contain negative OHLC values")
    bad_high = numeric["high"] < numeric[["open", "low", "close"]].max(axis=1)
    bad_low = numeric["low"] > numeric[["open", "high", "close"]].min(axis=1)
    if bad_high.any() or bad_low.any():
        raise ValidationError("prices contain inconsistent OHLC ranges")

    counts = df.groupby("symbol")["date"].nunique()
    short = counts[counts < 120]
    if not short.empty:
        warnings.append(
            "fewer than 120 price observations: "
            + ", ".join(f"{k}={v}" for k, v in short.items())
        )
    return warnings


def validate_nav(path: str | Path) -> list[str]:
    df = _load(Path(path))
    warnings: list[str] = []
    if df.empty:
        warnings.append("NAV dataset is empty")
        return warnings
    required = {"symbol", "nav_date", "unit_nav", "source"}
    missing = required - set(df.columns)
    if missing:
        raise ValidationError(f"NAV missing columns: {sorted(missing)}")
    if df.duplicated(["symbol", "nav_date"]).any():
        raise ValidationError("NAV contains duplicate symbol/nav_date keys")
    nav = pd.to_numeric(df["unit_nav"], errors="coerce")
    if (nav.dropna() <= 0).any():
        raise ValidationError("NAV contains non-positive unit_nav")
    return warnings


def validate_snapshot(path: str | Path) -> list[str]:
    df = _load(Path(path))
    if df.empty:
        return ["snapshot dataset is empty"]
    if df.duplicated(["symbol", "data_date"]).any():
        raise ValidationError("snapshot contains duplicate symbol/data_date keys")
    return []
