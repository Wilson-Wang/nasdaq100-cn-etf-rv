from __future__ import annotations

import json
from pathlib import Path

from etf_dataset.health import build_source_health, read_source_runs


def main() -> None:
    data = Path("data")
    manifest = json.loads((data / "manifest.json").read_text())
    source_runs = read_source_runs(data / "source_runs.csv")
    health = build_source_health(source_runs, manifest)
    (data / "research_health.json").write_text(
        json.dumps(health, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        f"research_health={health['status']} alerts={len(health['alerts'])} "
        f"counts={health['alert_counts']}"
    )


if __name__ == "__main__":
    main()
