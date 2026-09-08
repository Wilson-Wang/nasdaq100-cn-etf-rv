from __future__ import annotations

import json
from pathlib import Path

from etf_dataset.execution import write_execution_readiness


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    data_dir = ROOT / "data"
    manifest_path = data_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    as_of_date = manifest["requested_range"]["end"]
    symbols = [str(symbol) for symbol in manifest["universe"]]

    report = write_execution_readiness(
        data_dir / "etf_snapshot.csv",
        data_dir / "execution_readiness.json",
        symbols,
        as_of_date,
    )
    manifest["execution"] = report
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"execution_ready={len(report['execution_ready_symbols'])} "
        f"blocked={len(report['execution_blocked_symbols'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
