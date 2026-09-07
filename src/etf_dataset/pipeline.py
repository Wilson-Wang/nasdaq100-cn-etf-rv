from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .config import load_universe
from .sources import fetch_nav_akshare_em, fetch_prices_with_fallback, fetch_snapshot_akshare_em
from .storage import table_summary, upsert_csv, write_manifest, write_parquet_mirror
from .validation import validate_nav, validate_prices, validate_snapshot


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def update_dataset(
    root: Path,
    start_date: str,
    end_date: str,
    selected_symbols: set[str] | None = None,
    write_parquet: bool = True,
) -> dict:
    universe = load_universe(root / "config" / "etfs.json")
    if selected_symbols:
        universe = [etf for etf in universe if etf.symbol in selected_symbols]

    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    price_frames: list[pd.DataFrame] = []
    nav_frames: list[pd.DataFrame] = []

    for etf in universe:
        prices, price_errors = fetch_prices_with_fallback(etf, start_date, end_date)
        failures.extend(price_errors)
        if not prices.empty:
            price_frames.append(prices)

        try:
            nav = fetch_nav_akshare_em(etf, start_date, end_date)
            if nav.empty:
                failures.append(f"{etf.symbol} NAV akshare:eastmoney: empty result")
            else:
                nav_frames.append(nav)
        except Exception as exc:
            failures.append(f"{etf.symbol} NAV akshare:eastmoney: {type(exc).__name__}: {exc}")

    prices_in = pd.concat(price_frames, ignore_index=True) if price_frames else pd.DataFrame()
    nav_in = pd.concat(nav_frames, ignore_index=True) if nav_frames else pd.DataFrame()

    symbols = {etf.symbol for etf in universe}
    try:
        snapshot_in = fetch_snapshot_akshare_em(symbols)
        if snapshot_in.empty:
            failures.append("snapshot akshare:eastmoney: empty result")
    except Exception as exc:
        snapshot_in = pd.DataFrame()
        failures.append(f"snapshot akshare:eastmoney: {type(exc).__name__}: {exc}")

    prices_path = data_dir / "etf_prices.csv"
    nav_path = data_dir / "etf_nav.csv"
    snapshot_path = data_dir / "etf_snapshot.csv"

    upsert_csv(prices_path, prices_in, ["symbol", "date"])
    upsert_csv(nav_path, nav_in, ["symbol", "nav_date"])
    if not snapshot_in.empty or snapshot_path.exists():
        upsert_csv(snapshot_path, snapshot_in, ["symbol", "data_date"])

    parquet_paths: list[str] = []
    if write_parquet:
        for path in (prices_path, nav_path, snapshot_path):
            mirror = write_parquet_mirror(path)
            if mirror:
                parquet_paths.append(str(mirror.relative_to(root)))

    warnings: list[str] = []
    warnings.extend(validate_prices(prices_path))
    warnings.extend(validate_nav(nav_path))
    warnings.extend(validate_snapshot(snapshot_path))

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "requested_range": {"start": start_date, "end": end_date},
        "universe": [etf.symbol for etf in universe],
        "tables": {
            "prices": table_summary(prices_path, "date"),
            "nav": table_summary(nav_path, "nav_date"),
            "snapshot": table_summary(snapshot_path, "data_date"),
        },
        "parquet_mirrors": parquet_paths,
        "warnings": warnings,
        "failures": failures,
    }
    write_manifest(data_dir / "manifest.json", manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update Nasdaq-100 China ETF dataset")
    parser.add_argument("--root", type=Path, default=_default_root())
    parser.add_argument("--start")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--lookback-days", type=int, default=450)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--no-parquet", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    end = date.fromisoformat(args.end)
    start = args.start or (end - timedelta(days=args.lookback_days)).isoformat()
    manifest = update_dataset(
        root=args.root,
        start_date=start,
        end_date=args.end,
        selected_symbols=set(args.symbols) if args.symbols else None,
        write_parquet=not args.no_parquet,
    )
    print(
        f"updated prices={manifest['tables']['prices']['rows']} "
        f"nav={manifest['tables']['nav']['rows']} "
        f"snapshot={manifest['tables']['snapshot']['rows']} "
        f"failures={len(manifest['failures'])}"
    )
    return 0
