from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from etf_dataset.analysis import add_snapshot_valuation_fields


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show current executable IOPV valuation anchors")
    parser.add_argument("--root", type=Path, default=_root())
    return parser


def main() -> int:
    args = build_parser().parse_args()
    data_dir = args.root / "data"
    path = data_dir / "etf_snapshot.parquet"
    if path.exists():
        snapshot = pd.read_parquet(path)
    else:
        path = data_dir / "etf_snapshot.csv"
        if not path.exists():
            print("No ETF snapshot dataset found.")
            return 1
        snapshot = pd.read_csv(path, dtype={"symbol": "string"})

    out = add_snapshot_valuation_fields(snapshot)
    columns = [
        "symbol",
        "name",
        "data_date",
        "updated_at",
        "last",
        "bid1",
        "ask1",
        "mid",
        "spread_bps",
        "iopv",
        "iopv_premium_pct",
        "mid_iopv_premium_pct",
        "current_anchor_status",
    ]
    print(out[[column for column in columns if column in out.columns]].to_string(index=False))
    print("\nIOPV is a current-time anchor only; this output must not be backfilled into historical NAV signals.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
