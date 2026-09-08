from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd


FACTOR_COLUMNS = [
    "factor_name",
    "factor_date",
    "value",
    "open",
    "high",
    "low",
    "volume",
    "available_at",
    "availability_method",
    "availability_verified",
    "pit_usable",
    "source",
    "source_priority",
    "ingested_at_utc",
]

CONSERVATIVE_AVAILABILITY_METHOD = "next_calendar_day_08_cn"


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=FACTOR_COLUMNS)


def _to_numeric(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def conservative_available_at(factor_date: object) -> str | None:
    """Return a deliberately late timestamp that avoids same-day daily-bar lookahead.

    The upstream daily endpoints do not expose publication timestamps. For both
    the US index close and USD/CNH daily close, treating the observation as
    available at 08:00 Asia/Shanghai on the next calendar day is conservative
    for China-market EOD research. The timestamp is a model rule, not a source-
    verified publication time.
    """
    date_value = pd.to_datetime(factor_date, errors="coerce")
    if pd.isna(date_value):
        return None
    next_day = date_value.normalize() + timedelta(days=1)
    return next_day.tz_localize("Asia/Shanghai").replace(hour=8).isoformat()


def _attach_availability(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["available_at"] = frame["factor_date"].map(conservative_available_at)
    frame["availability_method"] = CONSERVATIVE_AVAILABILITY_METHOD
    frame["availability_verified"] = False
    frame["pit_usable"] = frame["available_at"].notna()
    return frame


def fetch_ndx_sina(start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch Nasdaq-100 daily closes from Sina via AKShare."""
    import akshare as ak

    df = ak.index_us_stock_sina(symbol=".NDX")
    if df is None or df.empty:
        return _empty()

    df = df.rename(columns={"date": "factor_date", "close": "value"})
    dates = pd.to_datetime(df["factor_date"], errors="coerce")
    mask = dates.between(pd.Timestamp(start_date), pd.Timestamp(end_date))
    df = df.loc[mask].copy()
    if df.empty:
        return _empty()

    df["factor_name"] = "NDX"
    df["factor_date"] = pd.to_datetime(df["factor_date"], errors="coerce").dt.strftime(
        "%Y-%m-%d"
    )
    df = _to_numeric(df, ["value", "open", "high", "low", "volume"])
    df = _attach_availability(df)
    df["source"] = "akshare:sina:index_us_stock_sina"
    df["source_priority"] = 10
    df["ingested_at_utc"] = _now_utc()
    return df.reindex(columns=FACTOR_COLUMNS)


def fetch_usdcnh_em(start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch USD/CNH daily closes from Eastmoney via AKShare."""
    import akshare as ak

    df = ak.forex_hist_em(symbol="USDCNH")
    if df is None or df.empty:
        return _empty()

    df = df.rename(
        columns={
            "日期": "factor_date",
            "最新价": "value",
            "今开": "open",
            "最高": "high",
            "最低": "low",
        }
    )
    dates = pd.to_datetime(df["factor_date"], errors="coerce")
    mask = dates.between(pd.Timestamp(start_date), pd.Timestamp(end_date))
    df = df.loc[mask].copy()
    if df.empty:
        return _empty()

    df["factor_name"] = "USDCNH"
    df["factor_date"] = pd.to_datetime(df["factor_date"], errors="coerce").dt.strftime(
        "%Y-%m-%d"
    )
    df["volume"] = pd.NA
    df = _to_numeric(df, ["value", "open", "high", "low", "volume"])
    df = _attach_availability(df)
    df["source"] = "akshare:eastmoney:forex_hist_em"
    df["source_priority"] = 10
    df["ingested_at_utc"] = _now_utc()
    return df.reindex(columns=FACTOR_COLUMNS)


def factor_summary(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return {"rows": 0, "factors": {}, "pit_usable_rows": 0}

    frame = pd.read_csv(path)
    usable = (
        frame.get("pit_usable", pd.Series(False, index=frame.index))
        .astype("string")
        .str.lower()
        .map({"true": True, "1": True, "false": False, "0": False})
        .fillna(False)
    )
    by_factor: dict[str, dict] = {}
    for name, group in frame.groupby("factor_name", sort=True):
        dates = pd.to_datetime(group["factor_date"], errors="coerce").dropna()
        by_factor[str(name)] = {
            "rows": int(len(group)),
            "min_date": dates.min().strftime("%Y-%m-%d") if not dates.empty else None,
            "max_date": dates.max().strftime("%Y-%m-%d") if not dates.empty else None,
            "sources": group["source"].value_counts(dropna=False).astype(int).to_dict(),
        }
    return {
        "rows": int(len(frame)),
        "factors": by_factor,
        "pit_usable_rows": int(usable.sum()),
        "availability_method": CONSERVATIVE_AVAILABILITY_METHOD,
        "availability_verified": False,
    }


def validate_factor_inputs(path: str | Path) -> list[str]:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return ["factor inputs file missing or empty"]

    frame = pd.read_csv(path)
    required = {
        "factor_name",
        "factor_date",
        "value",
        "available_at",
        "availability_method",
        "pit_usable",
        "source",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"factor inputs missing required columns: {sorted(missing)}")

    duplicated = frame.duplicated(["factor_name", "factor_date"], keep=False)
    if duplicated.any():
        raise ValueError("factor inputs contain duplicate factor/date rows")

    values = pd.to_numeric(frame["value"], errors="coerce")
    if values.isna().any() or values.le(0).any():
        raise ValueError("factor inputs contain non-positive or invalid values")

    available = pd.to_datetime(frame["available_at"], errors="coerce", utc=True)
    if available.isna().any():
        raise ValueError("factor inputs contain invalid available_at timestamps")

    warnings: list[str] = []
    present = set(frame["factor_name"].astype(str))
    for required_factor in ("NDX", "USDCNH"):
        if required_factor not in present:
            warnings.append(f"factor input missing: {required_factor}")
    return warnings
