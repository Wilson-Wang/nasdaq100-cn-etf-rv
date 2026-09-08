from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from etf_dataset.config import load_universe
from etf_dataset.sources import fetch_snapshot_akshare_em, run_with_timeout
from etf_dataset.storage import upsert_csv, write_parquet_mirror


def main() -> None:
    root = Path(".")
    data = root / "data"
    universe = load_universe(root / "config" / "etfs.json")
    symbols = {etf.symbol for etf in universe}
    snapshot = run_with_timeout(fetch_snapshot_akshare_em, symbols, seconds=45)
    if snapshot.empty:
        raise RuntimeError("execution observation snapshot is empty")

    frame = snapshot.copy()
    bid = pd.to_numeric(frame.get("bid1"), errors="coerce")
    ask = pd.to_numeric(frame.get("ask1"), errors="coerce")
    last = pd.to_numeric(frame.get("last"), errors="coerce")
    volume = pd.to_numeric(frame.get("volume"), errors="coerce")
    amount = pd.to_numeric(frame.get("amount"), errors="coerce")
    mid = (bid + ask) / 2.0
    valid_book = bid.gt(0) & ask.gt(0) & ask.ge(bid) & mid.gt(0)
    frame["mid"] = mid.where(valid_book)
    frame["bid_ask_spread_pct"] = ((ask - bid) / mid).where(valid_book)
    frame["active_book"] = valid_book
    frame["active_trading"] = last.gt(0) & (volume.gt(0) | amount.gt(0))
    frame["captured_at_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    # Full switch cost is not inferred from one spread observation. This is a
    # one-leg quoted spread sample for future empirical calibration.
    frame["quoted_half_spread_pct"] = (frame["bid_ask_spread_pct"] / 2.0).where(valid_book)
    frame["observation_quality"] = np.where(
        valid_book & frame["active_trading"], "LIVE_BOOK", "NO_LIVE_BOOK"
    )

    keep = [
        "symbol",
        "data_date",
        "updated_at",
        "captured_at_utc",
        "last",
        "bid1",
        "ask1",
        "mid",
        "bid_ask_spread_pct",
        "quoted_half_spread_pct",
        "volume",
        "amount",
        "turnover_pct",
        "active_book",
        "active_trading",
        "observation_quality",
        "source",
        "source_priority",
        "ingested_at_utc",
    ]
    frame = frame[[column for column in keep if column in frame.columns]].copy()
    path = data / "execution_history.csv"
    combined = upsert_csv(path, frame, ["symbol", "data_date", "captured_at_utc"])
    write_parquet_mirror(path)
    live = int((frame["observation_quality"] == "LIVE_BOOK").sum())
    print(f"execution_history added={len(frame)} total={len(combined)} live_book={live}/{len(frame)}")


if __name__ == "__main__":
    main()
