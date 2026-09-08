from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .config import load_universe
from .factors import (
    factor_summary,
    fetch_ndx_sina,
    fetch_usdcnh_em,
    validate_factor_inputs,
)
from .pcf import fetch_official_pcf, pcf_summary, validate_pcf
from .quality import build_quality_report
from .refresh import DEFAULT_OVERLAP_DAYS, plan_incremental_start, read_existing
from .snapshot import add_snapshot_derived_fields
from .sources import (
    fetch_nav_akshare_em,
    fetch_prices_with_fallback,
    fetch_snapshot_akshare_em,
    run_with_timeout,
)
from .storage import table_summary, upsert_csv, write_manifest, write_parquet_mirror
from .validation import validate_nav, validate_prices, validate_snapshot


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _add_unverified_nav_pit_fields(nav: pd.DataFrame) -> pd.DataFrame:
    """Persist explicit PIT-unknown state until a source proves availability time."""
    nav = nav.copy()
    defaults = {
        "published_at": pd.NA,
        "available_at": pd.NA,
        "availability_source": "unverified",
        "pit_verified": False,
    }
    for column, value in defaults.items():
        if column not in nav.columns:
            nav[column] = value
    return nav


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
    prices_path = data_dir / "etf_prices.csv"
    nav_path = data_dir / "etf_nav.csv"
    pcf_path = data_dir / "etf_pcf.csv"
    snapshot_path = data_dir / "etf_snapshot.csv"
    factors_path = data_dir / "factor_inputs.csv"

    existing_prices = read_existing(prices_path)
    existing_nav = read_existing(nav_path)
    existing_factors = read_existing(factors_path)

    failures: list[str] = []
    refresh_plan: dict[str, object] = {
        "overlap_days": DEFAULT_OVERLAP_DAYS,
        "prices": {},
        "nav": {},
        "factors": {},
    }

    price_frames: list[pd.DataFrame] = []
    nav_frames: list[pd.DataFrame] = []
    pcf_frames: list[pd.DataFrame] = []

    for index, etf in enumerate(universe, start=1):
        price_start, price_mode = plan_incremental_start(
            existing_prices,
            date_column="date",
            requested_start=start_date,
            filter_column="symbol",
            filter_value=etf.symbol,
            minimum_observations=250,
            require_requested_start_coverage=False,
            tradable_column="is_tradable",
        )
        refresh_plan["prices"][etf.symbol] = {
            "start": price_start,
            "mode": price_mode,
        }
        print(
            f"[{index}/{len(universe)}] {etf.symbol} prices {price_mode} from {price_start}",
            flush=True,
        )
        prices, price_errors = fetch_prices_with_fallback(etf, price_start, end_date)
        failures.extend(price_errors)
        if not prices.empty:
            price_frames.append(prices)

        nav_start, nav_mode = plan_incremental_start(
            existing_nav,
            date_column="nav_date",
            requested_start=start_date,
            filter_column="symbol",
            filter_value=etf.symbol,
            minimum_observations=120,
            require_requested_start_coverage=True,
        )
        refresh_plan["nav"][etf.symbol] = {"start": nav_start, "mode": nav_mode}
        print(
            f"[{index}/{len(universe)}] {etf.symbol} NAV {nav_mode} from {nav_start}",
            flush=True,
        )
        try:
            nav = run_with_timeout(
                fetch_nav_akshare_em,
                etf,
                nav_start,
                end_date,
                seconds=30,
            )
            if nav.empty:
                failures.append(f"{etf.symbol} NAV akshare:eastmoney: empty result")
            else:
                nav_frames.append(_add_unverified_nav_pit_fields(nav))
        except Exception as exc:
            failures.append(f"{etf.symbol} NAV akshare:eastmoney: {type(exc).__name__}: {exc}")

        print(f"[{index}/{len(universe)}] {etf.symbol} official PCF", flush=True)
        try:
            pcf = run_with_timeout(fetch_official_pcf, etf, end_date, seconds=30)
            if pcf.empty:
                failures.append(f"{etf.symbol} PCF official: empty result")
            else:
                pcf_frames.append(pcf)
        except Exception as exc:
            failures.append(f"{etf.symbol} PCF official: {type(exc).__name__}: {exc}")

    prices_in = pd.concat(price_frames, ignore_index=True) if price_frames else pd.DataFrame()
    nav_in = pd.concat(nav_frames, ignore_index=True) if nav_frames else pd.DataFrame()
    pcf_in = pd.concat(pcf_frames, ignore_index=True) if pcf_frames else pd.DataFrame()

    symbols = {etf.symbol for etf in universe}
    print("fetching latest ETF snapshot", flush=True)
    try:
        snapshot_in = run_with_timeout(fetch_snapshot_akshare_em, symbols, seconds=45)
        snapshot_in = add_snapshot_derived_fields(snapshot_in)
        if snapshot_in.empty:
            failures.append("snapshot akshare:eastmoney: empty result")
    except Exception as exc:
        snapshot_in = pd.DataFrame()
        failures.append(f"snapshot akshare:eastmoney: {type(exc).__name__}: {exc}")

    factor_frames: list[pd.DataFrame] = []
    print("fetching fair-value factor inputs", flush=True)
    for factor_name, source_name, fetcher in (
        ("NDX", "akshare:sina:index_us_stock_sina", fetch_ndx_sina),
        ("USDCNH", "akshare:eastmoney:forex_hist_em", fetch_usdcnh_em),
    ):
        factor_start, factor_mode = plan_incremental_start(
            existing_factors,
            date_column="factor_date",
            requested_start=start_date,
            filter_column="factor_name",
            filter_value=factor_name,
            minimum_observations=120,
            require_requested_start_coverage=True,
        )
        refresh_plan["factors"][factor_name] = {
            "start": factor_start,
            "mode": factor_mode,
        }
        try:
            factor = run_with_timeout(fetcher, factor_start, end_date, seconds=30)
            if factor.empty:
                failures.append(f"factor {factor_name} {source_name}: empty result")
            else:
                factor_frames.append(factor)
        except Exception as exc:
            failures.append(
                f"factor {factor_name} {source_name}: {type(exc).__name__}: {exc}"
            )
    factors_in = pd.concat(factor_frames, ignore_index=True) if factor_frames else pd.DataFrame()

    upsert_csv(prices_path, prices_in, ["symbol", "date"])
    upsert_csv(nav_path, nav_in, ["symbol", "nav_date"])
    if not pcf_in.empty or pcf_path.exists():
        upsert_csv(pcf_path, pcf_in, ["symbol", "date"])
    if not snapshot_in.empty or snapshot_path.exists():
        upsert_csv(snapshot_path, snapshot_in, ["symbol", "data_date"])
    if not factors_in.empty or factors_path.exists():
        upsert_csv(factors_path, factors_in, ["factor_name", "factor_date"])

    parquet_paths: list[str] = []
    if write_parquet:
        for path in (prices_path, nav_path, pcf_path, snapshot_path, factors_path):
            mirror = write_parquet_mirror(path)
            if mirror:
                parquet_paths.append(str(mirror.relative_to(root)))

    warnings: list[str] = []
    warnings.extend(validate_prices(prices_path))
    warnings.extend(validate_nav(nav_path))
    warnings.extend(validate_pcf(pcf_path))
    warnings.extend(validate_snapshot(snapshot_path))
    warnings.extend(validate_factor_inputs(factors_path))

    tables = {
        "prices": table_summary(prices_path, "date"),
        "nav": table_summary(nav_path, "nav_date"),
        "pcf": pcf_summary(pcf_path),
        "snapshot": table_summary(snapshot_path, "data_date"),
        "factor_inputs": factor_summary(factors_path),
    }
    universe_symbols = [etf.symbol for etf in universe]
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    quality = build_quality_report(
        prices_path=prices_path,
        nav_path=nav_path,
        snapshot_path=snapshot_path,
        universe_symbols=universe_symbols,
        as_of_date=end_date,
        failures=failures,
        observed_at_utc=generated_at,
        pcf_path=pcf_path,
    )

    manifest = {
        "generated_at_utc": generated_at,
        "requested_range": {"start": start_date, "end": end_date},
        "refresh_plan": refresh_plan,
        "universe": universe_symbols,
        "tables": tables,
        "quality": quality,
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
        f"pcf={manifest['tables']['pcf']['rows']} "
        f"snapshot={manifest['tables']['snapshot']['rows']} "
        f"factors={manifest['tables']['factor_inputs']['rows']} "
        f"formal_ready={len(manifest['quality']['formal_signal_ready_symbols'])} "
        f"failures={len(manifest['failures'])}"
    )
    return 0
