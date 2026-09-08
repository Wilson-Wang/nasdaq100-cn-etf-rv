from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def _bool_series(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
        .map({"true": True, "1": True, "false": False, "0": False})
        .fillna(False)
    )


def main() -> None:
    data = Path("data")
    manifest = json.loads((data / "manifest.json").read_text())
    symbols = [str(value) for value in manifest.get("universe", [])]
    prices = pd.read_csv(data / "etf_prices.csv", dtype={"symbol": "string"})
    pcf_path = data / "etf_pcf.csv"
    pcf = (
        pd.read_csv(pcf_path, dtype={"symbol": "string"})
        if pcf_path.exists() and pcf_path.stat().st_size
        else pd.DataFrame()
    )

    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    if "is_tradable" in prices.columns:
        prices = prices.loc[_bool_series(prices["is_tradable"])].copy()
    if not pcf.empty:
        pcf["date"] = pd.to_datetime(pcf["date"], errors="coerce")
        pcf["available_at"] = pd.to_datetime(
            pcf.get("available_at"), errors="coerce", utc=True
        )
        if "pit_verified" in pcf.columns:
            pcf = pcf.loc[_bool_series(pcf["pit_verified"])].copy()

    by_symbol: dict[str, dict[str, object]] = {}
    statuses: list[str] = []
    for symbol in symbols:
        px = prices.loc[prices["symbol"].astype("string").eq(symbol)].copy()
        pf = (
            pcf.loc[pcf["symbol"].astype("string").eq(symbol)].copy()
            if not pcf.empty
            else pd.DataFrame()
        )
        price_dates = set(px["date"].dropna().dt.normalize())
        pcf_dates = set(pf["date"].dropna().dt.normalize()) if not pf.empty else set()
        overlap = len(price_dates & pcf_dates)
        price_count = len(price_dates)
        ratio = overlap / price_count if price_count else 0.0
        price_min = min(price_dates) if price_dates else None
        pcf_min = min(pcf_dates) if pcf_dates else None
        pcf_max = max(pcf_dates) if pcf_dates else None

        if not pcf_dates:
            status = "MISSING"
        elif price_min is not None and pcf_min <= price_min and ratio >= 0.90:
            status = "FULL_OR_NEAR_FULL"
        elif ratio >= 0.50:
            status = "PARTIAL"
        else:
            status = "PROSPECTIVE_ONLY"
        statuses.append(status)
        by_symbol[symbol] = {
            "status": status,
            "tradable_price_dates": price_count,
            "pit_verified_pcf_dates": len(pcf_dates),
            "overlap_dates": overlap,
            "coverage_ratio": ratio,
            "price_min_date": price_min.strftime("%Y-%m-%d") if price_min is not None else None,
            "pcf_min_date": pcf_min.strftime("%Y-%m-%d") if pcf_min is not None else None,
            "pcf_max_date": pcf_max.strftime("%Y-%m-%d") if pcf_max is not None else None,
        }

    all_full = bool(statuses) and all(status == "FULL_OR_NEAR_FULL" for status in statuses)
    result = {
        "status": "FULL" if all_full else "INCOMPLETE_HISTORICAL_REGIME_COVERAGE",
        "all_symbols_full_or_near_full": all_full,
        "by_symbol": by_symbol,
        "historical_backtest_policy": (
            "PCF/event regime gates may be applied only on dates with PIT-verified event data; "
            "missing historical PCF must not be interpreted as NORMAL."
        ),
        "prospective_policy": (
            "current official PCF remains valid for current/future regime gating when available_at "
            "is before the signal information cutoff."
        ),
    }
    (data / "regime_history_coverage.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        f"regime_history_coverage status={result['status']} "
        f"full={sum(status == 'FULL_OR_NEAR_FULL' for status in statuses)}/{len(statuses)}"
    )


if __name__ == "__main__":
    main()
