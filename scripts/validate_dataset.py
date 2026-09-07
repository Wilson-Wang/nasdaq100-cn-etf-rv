from __future__ import annotations

from pathlib import Path

from etf_dataset.validation import validate_nav, validate_prices, validate_snapshot


ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    warnings = []
    warnings.extend(validate_prices(ROOT / "data" / "etf_prices.csv"))
    warnings.extend(validate_nav(ROOT / "data" / "etf_nav.csv"))
    warnings.extend(validate_snapshot(ROOT / "data" / "etf_snapshot.csv"))
    for warning in warnings:
        print(f"WARNING: {warning}")
    print("validation passed")
