from __future__ import annotations

import pandas as pd


def add_snapshot_derived_fields(frame: pd.DataFrame) -> pd.DataFrame:
    """Add deterministic execution/fair-value fields to ETF snapshots.

    Derived fields are intentionally mechanical. They do not turn IOPV into a
    guaranteed fair value; downstream analysis still applies freshness and
    multi-anchor consistency gates.
    """
    if frame.empty:
        return frame.copy()

    df = frame.copy()
    for column in ("last", "iopv", "bid1", "ask1"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    bid = df.get("bid1", pd.Series(index=df.index, dtype="float64"))
    ask = df.get("ask1", pd.Series(index=df.index, dtype="float64"))
    last = df.get("last", pd.Series(index=df.index, dtype="float64"))
    iopv = df.get("iopv", pd.Series(index=df.index, dtype="float64"))

    valid_quote = bid.gt(0) & ask.gt(0) & ask.ge(bid)
    mid = (bid + ask) / 2
    df["mid"] = mid.where(valid_quote)

    valid_mid = df["mid"].gt(0)
    df["bid_ask_spread_pct"] = (
        (ask - bid) / df["mid"] * 100
    ).where(valid_quote & valid_mid)

    valid_last_iopv = last.gt(0) & iopv.gt(0)
    df["last_iopv_premium_pct"] = (
        (last / iopv - 1) * 100
    ).where(valid_last_iopv)

    valid_mid_iopv = valid_mid & iopv.gt(0)
    df["mid_iopv_premium_pct"] = (
        (df["mid"] / iopv - 1) * 100
    ).where(valid_mid_iopv)

    return df
