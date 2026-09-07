from __future__ import annotations

from pathlib import Path

import pandas as pd


class ValidationError(ValueError):
    pass


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"symbol": "string"})


def _normalize_bool(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
        .map({"true": True, "false": False, "1": True, "0": False})
        .fillna(False)
        .astype(bool)
    )


def _format_counts(counts: pd.Series) -> str:
    return ", ".join(f"{k}={int(v)}" for k, v in counts.items())


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

    if "is_tradable" in df.columns:
        tradable = _normalize_bool(df["is_tradable"])
        observation_counts = df.loc[tradable].groupby("symbol")["date"].nunique()
        all_symbols = pd.Index(df["symbol"].dropna().unique(), dtype="string")
        observation_counts = observation_counts.reindex(all_symbols, fill_value=0).sort_index()

        non_tradable = df.loc[~tradable].groupby("symbol")["date"].nunique()
        if not non_tradable.empty:
            warnings.append(
                "non-tradable price observations must be excluded from pair models: "
                + _format_counts(non_tradable)
            )
    else:
        observation_counts = df.groupby("symbol")["date"].nunique().sort_index()
        if "volume" in df.columns:
            volume = pd.to_numeric(df["volume"], errors="coerce")
            zero_volume = df.loc[volume.le(0)].groupby("symbol")["date"].nunique()
            if not zero_volume.empty:
                warnings.append(
                    "zero-volume price observations found; derive is_tradable before pair modeling: "
                    + _format_counts(zero_volume)
                )

    short_120 = observation_counts[observation_counts < 120]
    if not short_120.empty:
        warnings.append(
            "fewer than 120 usable price observations: " + _format_counts(short_120)
        )

    short_250 = observation_counts[observation_counts < 250]
    if not short_250.empty:
        warnings.append(
            "fewer than 250 usable price observations (research-depth warning): "
            + _format_counts(short_250)
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
