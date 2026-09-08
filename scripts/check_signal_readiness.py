from __future__ import annotations

import argparse
import json
from pathlib import Path

from etf_dataset.config import load_universe
from etf_dataset.quality import build_quality_report


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check per-ETF strict signal readiness")
    parser.add_argument("--root", type=Path, default=_root())
    parser.add_argument("--as-of")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.root
    data_dir = root / "data"
    manifest_path = data_dir / "manifest.json"
    if not manifest_path.exists():
        print("No manifest.json found.")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    as_of = args.as_of or manifest.get("requested_range", {}).get("end")
    if not as_of:
        print("Unable to determine as-of date.")
        return 1

    universe = load_universe(root / "config" / "etfs.json")
    symbols = [item.symbol for item in universe]
    report = build_quality_report(
        data_dir / "etf_prices.csv",
        data_dir / "etf_nav.csv",
        data_dir / "etf_snapshot.csv",
        symbols,
        as_of,
        list(manifest.get("failures", [])),
    )

    rows = []
    for symbol, item in report["by_symbol"].items():
        rows.append(
            {
                "symbol": symbol,
                "states": "|".join(item["states"]),
                "formal_signal_ready": item["formal_signal_ready"],
                "usable_price_observations": item["usable_price_observations"],
                "price_last_date": item["price_last_date"],
                "nav_last_date": item["nav_last_date"],
                "snapshot_last_date": item["snapshot_last_date"],
                "pit_verified": item["pit_verified"],
                "source_degraded": item["source_degraded"],
                "critical_source_failure": item["critical_source_failure"],
            }
        )

    if not rows:
        print("No dataset rows available.")
        return 1

    import pandas as pd

    frame = pd.DataFrame(rows)
    print(frame.to_string(index=False))
    ready_count = int(frame["formal_signal_ready"].sum())
    print(f"\nformal_signal_ready={ready_count}/{len(frame)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
