from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from etf_dataset.metadata import (
    build_product_quality_scores,
    fetch_fund_overview,
    tracking_error_proxy,
)
from etf_dataset.storage import upsert_csv, write_parquet_mirror


def main() -> None:
    data = Path("data")
    manifest_path = data / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    as_of = str(manifest.get("quality", {}).get("as_of_date") or manifest["requested_range"]["end"])
    symbols = [str(value) for value in manifest.get("universe", [])]

    frames: list[pd.DataFrame] = []
    failures: list[str] = []
    for symbol in symbols:
        try:
            frame = fetch_fund_overview(symbol, as_of)
            if not frame.empty:
                frame["source_priority"] = 10
                frames.append(frame)
        except Exception as exc:
            failures.append(f"metadata {symbol}: {type(exc).__name__}: {exc}")

    incoming = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    metadata_path = data / "etf_metadata.csv"
    metadata = upsert_csv(metadata_path, incoming, ["symbol", "effective_date"])
    write_parquet_mirror(metadata_path)

    prices = pd.read_csv(data / "etf_prices.csv", dtype={"symbol": "string"})
    nav = pd.read_csv(data / "etf_nav.csv", dtype={"symbol": "string"})
    factors = pd.read_csv(data / "factor_inputs.csv") if (data / "factor_inputs.csv").exists() else pd.DataFrame()
    tracking = tracking_error_proxy(nav, factors)
    pqs = build_product_quality_scores(metadata, prices, tracking, as_of)
    pqs_path = data / "product_quality.csv"
    pqs.to_csv(pqs_path, index=False)
    if not pqs.empty:
        pqs.to_parquet(pqs_path.with_suffix(".parquet"), index=False)

    manifest["metadata_status"] = {
        "as_of_date": as_of,
        "rows": int(len(metadata)),
        "symbols": int(metadata["symbol"].nunique()) if not metadata.empty else 0,
        "current_snapshot_symbols": int(incoming["symbol"].nunique()) if not incoming.empty else 0,
        "pqs_rows": int(len(pqs)),
        "pqs_complete": int((pqs["pqs_status"] == "COMPLETE").sum()) if not pqs.empty else 0,
        "tracking_error_method": "NAV_vs_NDX_times_preferred_FX_annualized_proxy",
        "metadata_semantics": "current_observation_snapshot_not_historical_effective_fee_record",
        "failures": failures,
    }
    if failures:
        warnings = list(manifest.get("warnings", []))
        warning = f"metadata refresh degraded for {len(failures)} symbols"
        if warning not in warnings:
            warnings.append(warning)
        manifest["warnings"] = warnings
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(
        f"metadata symbols={manifest['metadata_status']['current_snapshot_symbols']} "
        f"pqs_complete={manifest['metadata_status']['pqs_complete']} failures={len(failures)}"
    )


if __name__ == "__main__":
    main()
