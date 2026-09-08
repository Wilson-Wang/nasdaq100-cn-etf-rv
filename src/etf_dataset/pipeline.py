from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from .audit import (
    build_source_run_record,
    new_run_id,
    records_frame,
    source_run_summary,
    utc_now,
)
from .config import load_universe
from .factors import (
    FX_FACTOR_PREFERENCE,
    factor_summary,
    fetch_ndx_sina,
    fetch_usdcnh_em,
    fetch_usdcny_safe,
    validate_factor_inputs,
)
from .nav_pit import enrich_nav_file, enrich_nav_pit, observed_exchange_days
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


def _call_with_retries(
    func: Callable[..., pd.DataFrame],
    *args,
    attempts: int = 3,
    timeout_seconds: int = 30,
) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return run_with_timeout(func, *args, seconds=timeout_seconds)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2**attempt))
    assert last_error is not None
    raise last_error


def _preferred_existing_fx(existing_factors: pd.DataFrame) -> str:
    if existing_factors.empty or "factor_name" not in existing_factors.columns:
        return FX_FACTOR_PREFERENCE[0]
    present = set(existing_factors["factor_name"].dropna().astype(str))
    return next((name for name in FX_FACTOR_PREFERENCE if name in present), FX_FACTOR_PREFERENCE[0])


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

    run_id = new_run_id()
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    prices_path = data_dir / "etf_prices.csv"
    nav_path = data_dir / "etf_nav.csv"
    pcf_path = data_dir / "etf_pcf.csv"
    snapshot_path = data_dir / "etf_snapshot.csv"
    factors_path = data_dir / "factor_inputs.csv"
    source_runs_path = data_dir / "source_runs.csv"

    existing_prices = read_existing(prices_path)
    existing_nav = read_existing(nav_path)
    existing_factors = read_existing(factors_path)

    failures: list[str] = []
    source_records: list[dict[str, object]] = []
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
        refresh_plan["prices"][etf.symbol] = {"start": price_start, "mode": price_mode}
        print(
            f"[{index}/{len(universe)}] {etf.symbol} prices {price_mode} from {price_start}",
            flush=True,
        )
        started = utc_now()
        prices, price_errors = fetch_prices_with_fallback(etf, price_start, end_date)
        finished = utc_now()
        failures.extend(price_errors)
        source_records.append(
            build_source_run_record(
                run_id=run_id,
                resource="prices",
                symbol=etf.symbol,
                requested_start=price_start,
                requested_end=end_date,
                started_at=started,
                finished_at=finished,
                frame=prices,
                date_column="date",
                errors=price_errors,
            )
        )
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
        started = utc_now()
        nav_errors: list[str] = []
        nav = pd.DataFrame()
        try:
            nav = run_with_timeout(fetch_nav_akshare_em, etf, nav_start, end_date, seconds=30)
            if nav.empty:
                nav_errors.append(f"{etf.symbol} NAV akshare:eastmoney: empty result")
            else:
                nav_frames.append(nav)
        except Exception as exc:
            nav_errors.append(
                f"{etf.symbol} NAV akshare:eastmoney: {type(exc).__name__}: {exc}"
            )
        finished = utc_now()
        failures.extend(nav_errors)
        source_records.append(
            build_source_run_record(
                run_id=run_id,
                resource="nav",
                symbol=etf.symbol,
                requested_start=nav_start,
                requested_end=end_date,
                started_at=started,
                finished_at=finished,
                frame=nav,
                date_column="nav_date",
                errors=nav_errors,
            )
        )

        print(f"[{index}/{len(universe)}] {etf.symbol} official PCF", flush=True)
        started = utc_now()
        pcf_errors: list[str] = []
        pcf = pd.DataFrame()
        try:
            pcf = _call_with_retries(fetch_official_pcf, etf, end_date, attempts=3)
            if pcf.empty:
                pcf_errors.append(f"{etf.symbol} PCF official: empty result")
            else:
                pcf_frames.append(pcf)
        except Exception as exc:
            pcf_errors.append(f"{etf.symbol} PCF official: {type(exc).__name__}: {exc}")
        finished = utc_now()
        failures.extend(pcf_errors)
        source_records.append(
            build_source_run_record(
                run_id=run_id,
                resource="pcf",
                symbol=etf.symbol,
                requested_start=end_date,
                requested_end=end_date,
                started_at=started,
                finished_at=finished,
                frame=pcf,
                date_column="date",
                errors=pcf_errors,
            )
        )

    prices_in = pd.concat(price_frames, ignore_index=True) if price_frames else pd.DataFrame()
    nav_in = pd.concat(nav_frames, ignore_index=True) if nav_frames else pd.DataFrame()
    pcf_in = pd.concat(pcf_frames, ignore_index=True) if pcf_frames else pd.DataFrame()

    # Use actual observed ETF sessions as the mainland workday calendar for the
    # QDII T+2 conservative NAV availability bound.
    exchange_days = observed_exchange_days(prices_path, prices_in)
    nav_in = enrich_nav_pit(nav_in, exchange_days)

    symbols = {etf.symbol for etf in universe}
    print("fetching latest ETF snapshot", flush=True)
    started = utc_now()
    snapshot_errors: list[str] = []
    try:
        snapshot_in = run_with_timeout(fetch_snapshot_akshare_em, symbols, seconds=45)
        snapshot_in = add_snapshot_derived_fields(snapshot_in)
        if snapshot_in.empty:
            snapshot_errors.append("snapshot akshare:eastmoney: empty result")
    except Exception as exc:
        snapshot_in = pd.DataFrame()
        snapshot_errors.append(f"snapshot akshare:eastmoney: {type(exc).__name__}: {exc}")
    finished = utc_now()
    failures.extend(snapshot_errors)
    source_records.append(
        build_source_run_record(
            run_id=run_id,
            resource="snapshot",
            symbol="*",
            requested_start=end_date,
            requested_end=end_date,
            started_at=started,
            finished_at=finished,
            frame=snapshot_in,
            date_column="data_date",
            errors=snapshot_errors,
        )
    )

    factor_frames: list[pd.DataFrame] = []
    print("fetching fair-value factor inputs", flush=True)

    ndx_start, ndx_mode = plan_incremental_start(
        existing_factors,
        date_column="factor_date",
        requested_start=start_date,
        filter_column="factor_name",
        filter_value="NDX",
        minimum_observations=120,
        require_requested_start_coverage=True,
    )
    refresh_plan["factors"]["NDX"] = {"start": ndx_start, "mode": ndx_mode}
    started = utc_now()
    ndx_errors: list[str] = []
    ndx = pd.DataFrame()
    try:
        ndx = run_with_timeout(fetch_ndx_sina, ndx_start, end_date, seconds=30)
        if ndx.empty:
            ndx_errors.append("factor NDX akshare:sina:index_us_stock_sina: empty result")
        else:
            factor_frames.append(ndx)
    except Exception as exc:
        ndx_errors.append(
            f"factor NDX akshare:sina:index_us_stock_sina: {type(exc).__name__}: {exc}"
        )
    finished = utc_now()
    failures.extend(ndx_errors)
    source_records.append(
        build_source_run_record(
            run_id=run_id,
            resource="factor:NDX",
            symbol="*",
            requested_start=ndx_start,
            requested_end=end_date,
            started_at=started,
            finished_at=finished,
            frame=ndx,
            date_column="factor_date",
            errors=ndx_errors,
        )
    )

    existing_fx_name = _preferred_existing_fx(existing_factors)
    fx_start, fx_mode = plan_incremental_start(
        existing_factors,
        date_column="factor_date",
        requested_start=start_date,
        filter_column="factor_name",
        filter_value=existing_fx_name,
        minimum_observations=120,
        require_requested_start_coverage=True,
    )
    refresh_plan["factors"]["FX"] = {
        "start": fx_start,
        "mode": fx_mode,
        "existing_preference": existing_fx_name,
        "source_preference": list(FX_FACTOR_PREFERENCE),
    }
    started = utc_now()
    fx_errors: list[str] = []
    fx = pd.DataFrame()
    try:
        fx = run_with_timeout(fetch_usdcnh_em, fx_start, end_date, seconds=30)
        if fx.empty:
            fx_errors.append("factor USDCNH akshare:eastmoney:forex_hist_em: empty result")
    except Exception as exc:
        fx_errors.append(
            f"factor USDCNH akshare:eastmoney:forex_hist_em: {type(exc).__name__}: {exc}"
        )
        fx = pd.DataFrame()

    if fx.empty:
        try:
            fx = run_with_timeout(fetch_usdcny_safe, fx_start, end_date, seconds=30)
            if fx.empty:
                fx_errors.append("factor USDCNY akshare:safe:currency_boc_safe: empty result")
        except Exception as exc:
            fx_errors.append(
                f"factor USDCNY akshare:safe:currency_boc_safe: {type(exc).__name__}: {exc}"
            )
            fx = pd.DataFrame()
    if not fx.empty:
        factor_frames.append(fx)
    finished = utc_now()
    failures.extend(fx_errors)
    source_records.append(
        build_source_run_record(
            run_id=run_id,
            resource="factor:FX",
            symbol="*",
            requested_start=fx_start,
            requested_end=end_date,
            started_at=started,
            finished_at=finished,
            frame=fx,
            date_column="factor_date",
            errors=fx_errors,
        )
    )

    factors_in = pd.concat(factor_frames, ignore_index=True) if factor_frames else pd.DataFrame()
    source_runs_in = records_frame(source_records)

    upsert_csv(prices_path, prices_in, ["symbol", "date"])
    upsert_csv(nav_path, nav_in, ["symbol", "nav_date"])
    # One-time enrichment of previously stored history; subsequent unchanged
    # refreshes retain business values and ingestion provenance.
    enrich_nav_file(nav_path, exchange_days)
    if not pcf_in.empty or pcf_path.exists():
        upsert_csv(pcf_path, pcf_in, ["symbol", "date"])
    if not snapshot_in.empty or snapshot_path.exists():
        upsert_csv(snapshot_path, snapshot_in, ["symbol", "data_date"])
    if not factors_in.empty or factors_path.exists():
        upsert_csv(factors_path, factors_in, ["factor_name", "factor_date"])
    upsert_csv(source_runs_path, source_runs_in, ["run_id", "resource", "symbol"])

    parquet_paths: list[str] = []
    if write_parquet:
        for path in (
            prices_path,
            nav_path,
            pcf_path,
            snapshot_path,
            factors_path,
            source_runs_path,
        ):
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
        "source_runs": source_run_summary(source_runs_path, run_id),
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
        "run_id": run_id,
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
        f"source_runs={manifest['tables']['source_runs']['current_run_rows']} "
        f"formal_ready={len(manifest['quality']['formal_signal_ready_symbols'])} "
        f"failures={len(manifest['failures'])}"
    )
    return 0
