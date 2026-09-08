from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from etf_dataset.analysis import (
    build_official_nav_premium_history,
    compute_common_factor_residuals,
    latest_factor_candidates,
    latest_factor_ranking,
)


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet" and path.exists():
        return pd.read_parquet(path)
    if path.exists():
        return pd.read_csv(path, dtype={"symbol": "string"})
    raise FileNotFoundError(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Experimental common-factor residual ranking for Nasdaq-100 China ETFs"
    )
    parser.add_argument("--root", type=Path, default=_root())
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--as-of")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    data_dir = args.root / "data"
    prices_path = data_dir / "etf_prices.parquet"
    nav_path = data_dir / "etf_nav.parquet"
    if not prices_path.exists():
        prices_path = data_dir / "etf_prices.csv"
    if not nav_path.exists():
        nav_path = data_dir / "etf_nav.csv"

    prices = _read(prices_path)
    nav = _read(nav_path)
    if args.as_of:
        as_of = pd.Timestamp(args.as_of)
        prices = prices.loc[pd.to_datetime(prices["date"], errors="coerce").le(as_of)]
        nav = nav.loc[pd.to_datetime(nav["nav_date"], errors="coerce").le(as_of)]

    premium = build_official_nav_premium_history(prices, nav)
    factors = compute_common_factor_residuals(premium)
    ranking = latest_factor_ranking(factors)
    candidates = latest_factor_candidates(factors, n_each=args.top)

    if ranking.empty:
        print("No factor ranking available; check aligned price/NAV history.")
        return 0

    latest_date = pd.Timestamp(ranking.iloc[0]["date"]).date().isoformat()
    print(f"factor-model date={latest_date} status=EXPERIMENTAL PIT may be unverified")
    display_columns = [
        "symbol",
        "official_premium_pct",
        "common_premium_factor",
        "structural_alpha",
        "idiosyncratic_residual",
        "idiosyncratic_robust_z",
        "pit_status",
    ]
    print("\nCheap -> expensive residual ranking")
    print(ranking[[column for column in display_columns if column in ranking.columns]].to_string(index=False))

    print("\nTop experimental rotate-out -> rotate-in candidates")
    if candidates.empty:
        print("none")
    else:
        print(candidates.head(max(args.top * args.top, 1)).to_string(index=False))
    print("\nThis module generates research candidates only; it does not bypass the formal pair/PIT/regime gates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
