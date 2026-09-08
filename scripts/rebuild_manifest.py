from __future__ import annotations

import argparse
from pathlib import Path

from etf_dataset.manifest import rebuild_manifest_from_persisted_data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild data/manifest.json from persisted canonical files without network I/O"
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--as-of-date")
    parser.add_argument("--no-parquet", action="store_true")
    args = parser.parse_args()

    manifest = rebuild_manifest_from_persisted_data(
        args.root,
        as_of_date=args.as_of_date,
        write_parquet=not args.no_parquet,
    )
    prices = manifest["tables"]["prices"]
    nav = manifest["tables"]["nav"]
    quality = manifest["quality"]
    print(
        "manifest rebuilt "
        f"prices={prices.get('rows', 0)} nav={nav.get('rows', 0)} "
        f"formal_ready={quality.get('formal_signal_ready_symbols', 0)}"
    )


if __name__ == "__main__":
    main()
