from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def _bool_series(series: pd.Series) -> pd.Series:
    return series.astype("string").str.lower().map(
        {"true": True, "1": True, "false": False, "0": False}
    ).fillna(False)


def main() -> None:
    data = Path("data")
    prices = pd.read_csv(data / "etf_prices.csv", dtype={"symbol": "string"})
    nav = pd.read_csv(data / "etf_nav.csv", dtype={"symbol": "string"})
    manifest = json.loads((data / "manifest.json").read_text())
    symbols = [str(value) for value in manifest.get("universe", [])]

    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    if "is_tradable" in prices.columns:
        prices = prices.loc[_bool_series(prices["is_tradable"])].copy()
    nav["nav_date"] = pd.to_datetime(nav["nav_date"], errors="coerce")
    if "pit_usable" in nav.columns:
        nav = nav.loc[_bool_series(nav["pit_usable"])].copy()

    by_symbol = {}
    for symbol in symbols:
        px = prices.loc[prices["symbol"].astype("string").eq(symbol)]
        nv = nav.loc[nav["symbol"].astype("string").eq(symbol)]
        px_count = int(px["date"].nunique())
        nav_count = int(nv["nav_date"].nunique())
        aligned_upper = min(px_count, nav_count)
        by_symbol[symbol] = {
            "tradable_price_observations": px_count,
            "pit_usable_nav_observations": nav_count,
            "aligned_upper_bound": aligned_upper,
            "price_min_date": px["date"].min().strftime("%Y-%m-%d") if not px.empty else None,
            "nav_min_date": nv["nav_date"].min().strftime("%Y-%m-%d") if not nv.empty else None,
            "precheck_120": aligned_upper >= 120,
            "precheck_250": aligned_upper >= 250,
            "preferred_500": aligned_upper >= 500,
        }
    preferred = sorted(symbol for symbol, item in by_symbol.items() if item["preferred_500"])
    result = {
        "symbols": len(symbols),
        "preferred_500_symbols": preferred,
        "preferred_500_count": len(preferred),
        "all_preferred_500": len(preferred) == len(symbols) and bool(symbols),
        "by_symbol": by_symbol,
        "semantics": "counts use unique tradable price dates and PIT-usable NAV dates; actual pair alignment may be lower",
    }
    (data / "history_depth.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        f"history_depth preferred_500={result['preferred_500_count']}/{result['symbols']}"
    )


if __name__ == "__main__":
    main()
