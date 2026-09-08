from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .audit import source_run_summary
from .config import load_universe
from .factors import factor_summary, validate_factor_inputs
from .pcf import pcf_summary, validate_pcf
from .quality import build_quality_report
from .storage import table_summary, write_manifest, write_parquet_mirror
from .validation import validate_nav, validate_prices, validate_snapshot


CANONICAL_TABLES = (
    ("prices", "etf_prices.csv", "date"),
    ("nav", "etf_nav.csv", "nav_date"),
    ("snapshot", "etf_snapshot.csv", "data_date"),
)


def _load_existing_manifest(path: Path) -> dict:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _default_as_of(existing: dict, prices_path: Path) -> str:
    requested = existing.get("requested_range", {}).get("end")
    if requested:
        return str(requested)
    summary = table_summary(prices_path, "date")
    if summary.get("max_date"):
        return str(summary["max_date"])
    return datetime.now(timezone.utc).date().isoformat()


def rebuild_manifest_from_persisted_data(
    root: str | Path,
    *,
    as_of_date: str | None = None,
    write_parquet: bool = True,
) -> dict:
    """Rebuild manifest coverage/quality from canonical files without network I/O.

    The latest source-run identity and failures are intentionally inherited from
    the prior manifest: an offline rebuild is a metadata revision, not a new
    source fetch. Physical coverage, checksums, validation and quality are always
    recalculated from the files that are actually persisted on disk.
    """
    root = Path(root)
    data_dir = root / "data"
    manifest_path = data_dir / "manifest.json"
    existing = _load_existing_manifest(manifest_path)

    universe = load_universe(root / "config" / "etfs.json")
    universe_symbols = [etf.symbol for etf in universe]
    prices_path = data_dir / "etf_prices.csv"
    nav_path = data_dir / "etf_nav.csv"
    pcf_path = data_dir / "etf_pcf.csv"
    snapshot_path = data_dir / "etf_snapshot.csv"
    factors_path = data_dir / "factor_inputs.csv"
    source_runs_path = data_dir / "source_runs.csv"

    effective_as_of = as_of_date or _default_as_of(existing, prices_path)
    # Preserve the observation timestamp of the source run when available so an
    # offline metadata rebuild does not pretend that no-new-data freshness has
    # been checked at rebuild time.
    quality_observed_at = existing.get("generated_at_utc")
    rebuilt_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    failures = list(existing.get("failures", []))
    source_run_id = str(existing.get("run_id") or "")

    parquet_paths: list[str] = []
    if write_parquet:
        for filename in (
            "etf_prices.csv",
            "etf_nav.csv",
            "etf_pcf.csv",
            "etf_snapshot.csv",
            "factor_inputs.csv",
            "source_runs.csv",
        ):
            mirror = write_parquet_mirror(data_dir / filename)
            if mirror:
                parquet_paths.append(str(mirror.relative_to(root)))
    else:
        parquet_paths = list(existing.get("parquet_mirrors", []))

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
        "source_runs": source_run_summary(source_runs_path, source_run_id),
    }
    quality = build_quality_report(
        prices_path=prices_path,
        nav_path=nav_path,
        snapshot_path=snapshot_path,
        universe_symbols=universe_symbols,
        as_of_date=effective_as_of,
        failures=failures,
        observed_at_utc=quality_observed_at,
        pcf_path=pcf_path,
    )

    manifest = dict(existing)
    manifest.update(
        {
            "generated_at_utc": rebuilt_at,
            "universe": universe_symbols,
            "tables": tables,
            "quality": quality,
            "parquet_mirrors": parquet_paths,
            "warnings": warnings,
            "failures": failures,
            "manifest_revision": {
                "mode": "REBUILT_FROM_PERSISTED_DATA",
                "rebuilt_at_utc": rebuilt_at,
                "source_run_id": source_run_id or None,
                "quality_reference_observed_at_utc": quality_observed_at,
                "as_of_date": effective_as_of,
            },
        }
    )
    manifest.setdefault("requested_range", {"start": None, "end": effective_as_of})
    write_manifest(manifest_path, manifest)
    return manifest
