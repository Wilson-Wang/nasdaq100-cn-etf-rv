from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from etf_dataset.config import load_universe
from etf_dataset.factors import fetch_ndx_sina, fetch_usdcnh_em, fetch_usdcny_safe
from etf_dataset.nav_pit import enrich_nav_pit, observed_exchange_days
from etf_dataset.sources import fetch_nav_akshare_em, fetch_prices_with_fallback, run_with_timeout
from etf_dataset.storage import upsert_csv, write_parquet_mirror


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=1100)
    args = parser.parse_args()

    root = Path(".")
    data = root / "data"
    end = date.today()
    start = end - timedelta(days=args.lookback_days)
    start_s = start.isoformat()
    end_s = end.isoformat()
    universe = load_universe(root / "config" / "etfs.json")

    price_frames: list[pd.DataFrame] = []
    nav_frames: list[pd.DataFrame] = []
    errors: list[str] = []
    for idx, etf in enumerate(universe, start=1):
        print(f"[{idx}/{len(universe)}] {etf.symbol} deep price history", flush=True)
        prices, price_errors = fetch_prices_with_fallback(etf, start_s, end_s)
        errors.extend(price_errors)
        if not prices.empty:
            price_frames.append(prices)

        print(f"[{idx}/{len(universe)}] {etf.symbol} deep NAV history", flush=True)
        try:
            nav = run_with_timeout(fetch_nav_akshare_em, etf, start_s, end_s, seconds=45)
            if not nav.empty:
                nav_frames.append(nav)
        except Exception as exc:
            errors.append(f"{etf.symbol} NAV deep backfill: {type(exc).__name__}: {exc}")

    prices_in = pd.concat(price_frames, ignore_index=True) if price_frames else pd.DataFrame()
    price_path = data / "etf_prices.csv"
    prices = upsert_csv(price_path, prices_in, ["symbol", "date"])
    write_parquet_mirror(price_path)

    nav_in = pd.concat(nav_frames, ignore_index=True) if nav_frames else pd.DataFrame()
    nav_path = data / "etf_nav.csv"
    exchange_days = observed_exchange_days(price_path, prices_in)
    nav_in = enrich_nav_pit(nav_in, exchange_days)
    nav = upsert_csv(nav_path, nav_in, ["symbol", "nav_date"])
    write_parquet_mirror(nav_path)

    factor_frames: list[pd.DataFrame] = []
    try:
        ndx = run_with_timeout(fetch_ndx_sina, start_s, end_s, seconds=45)
        if not ndx.empty:
            factor_frames.append(ndx)
    except Exception as exc:
        errors.append(f"NDX deep backfill: {type(exc).__name__}: {exc}")
    try:
        fx = run_with_timeout(fetch_usdcnh_em, start_s, end_s, seconds=30)
        if not fx.empty:
            factor_frames.append(fx)
    except Exception as exc:
        errors.append(f"USDCNH deep backfill: {type(exc).__name__}: {exc}")
        try:
            fx = run_with_timeout(fetch_usdcny_safe, start_s, end_s, seconds=45)
            if not fx.empty:
                factor_frames.append(fx)
        except Exception as fallback_exc:
            errors.append(
                f"USDCNY deep backfill: {type(fallback_exc).__name__}: {fallback_exc}"
            )
    factors_path = data / "factor_inputs.csv"
    factors_in = pd.concat(factor_frames, ignore_index=True) if factor_frames else pd.DataFrame()
    factors = upsert_csv(factors_path, factors_in, ["factor_name", "factor_date"])
    write_parquet_mirror(factors_path)

    depth = (
        prices.assign(symbol=prices["symbol"].astype("string"))
        .groupby("symbol")["date"]
        .count()
        .sort_values()
    )
    print(
        f"deep backfill prices={len(prices)} nav={len(nav)} factors={len(factors)} "
        f"min_symbol_rows={int(depth.min()) if not depth.empty else 0} errors={len(errors)}"
    )
    for error in errors:
        print(f"warning: {error}")


if __name__ == "__main__":
    main()
