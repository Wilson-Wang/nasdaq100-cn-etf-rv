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


def _metadata_for_scoring(metadata: pd.DataFrame, nav: pd.DataFrame) -> pd.DataFrame:
    """Add a transparent shares×NAV AUM proxy without altering raw metadata."""
    if metadata.empty:
        return metadata.copy()
    enriched = metadata.copy()
    enriched["aum_cny_reported"] = pd.to_numeric(enriched.get("aum_cny"), errors="coerce")
    enriched["shares"] = pd.to_numeric(enriched.get("shares"), errors="coerce")
    if nav.empty:
        enriched["aum_cny_proxy"] = pd.NA
        enriched["aum_method"] = "MISSING"
        return enriched

    n = nav.copy()
    n["nav_date"] = pd.to_datetime(n["nav_date"], errors="coerce")
    n["unit_nav"] = pd.to_numeric(n["unit_nav"], errors="coerce")
    latest_nav = (
        n.dropna(subset=["nav_date", "unit_nav"])
        .sort_values("nav_date")
        .drop_duplicates("symbol", keep="last")
        .set_index(n.dropna(subset=["nav_date", "unit_nav"]).sort_values("nav_date").drop_duplicates("symbol", keep="last")["symbol"].astype("string"))["unit_nav"]
    )
    enriched["latest_unit_nav_for_aum"] = enriched["symbol"].astype("string").map(latest_nav)
    enriched["aum_cny_proxy"] = enriched["shares"] * pd.to_numeric(
        enriched["latest_unit_nav_for_aum"], errors="coerce"
    )
    reported = enriched["aum_cny_reported"].notna() & enriched["aum_cny_reported"].gt(0)
    proxy = enriched["aum_cny_proxy"].notna() & enriched["aum_cny_proxy"].gt(0)
    enriched["aum_cny"] = enriched["aum_cny_reported"].where(reported, enriched["aum_cny_proxy"])
    enriched["aum_method"] = "MISSING"
    enriched.loc[proxy & ~reported, "aum_method"] = "PROXY_SHARES_TIMES_LATEST_NAV"
    enriched.loc[reported, "aum_method"] = "REPORTED"
    return enriched


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
    metadata_scoring = _metadata_for_scoring(metadata, nav)
    pqs = build_product_quality_scores(metadata_scoring, prices, tracking, as_of)
    pqs_path = data / "product_quality.csv"
    pqs.to_csv(pqs_path, index=False)
    if not pqs.empty:
        pqs.to_parquet(pqs_path.with_suffix(".parquet"), index=False)

    proxy_count = int((pqs.get("aum_method", pd.Series(dtype="string")) == "PROXY_SHARES_TIMES_LATEST_NAV").sum()) if not pqs.empty else 0
    manifest["metadata_status"] = {
        "as_of_date": as_of,
        "rows": int(len(metadata)),
        "symbols": int(metadata["symbol"].nunique()) if not metadata.empty else 0,
        "current_snapshot_symbols": int(incoming["symbol"].nunique()) if not incoming.empty else 0,
        "pqs_rows": int(len(pqs)),
        "pqs_complete": int((pqs["pqs_status"] == "COMPLETE").sum()) if not pqs.empty else 0,
        "aum_proxy_symbols": proxy_count,
        "aum_proxy_method": "shares_times_latest_unit_nav_when_reported_aum_missing",
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
        f"pqs_complete={manifest['metadata_status']['pqs_complete']} "
        f"aum_proxy={proxy_count} failures={len(failures)}"
    )


if __name__ == "__main__":
    main()
