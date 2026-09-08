from __future__ import annotations

import json
from pathlib import Path

from etf_dataset.calendar_quality import reconcile_quality_with_exchange_calendar


def main() -> None:
    path = Path("data/manifest.json")
    manifest = json.loads(path.read_text())
    manifest = reconcile_quality_with_exchange_calendar(manifest)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    quality = manifest.get("quality", {})
    print(
        "exchange_calendar model_as_of_date="
        f"{quality.get('model_as_of_date')} ready="
        f"{len(quality.get('formal_signal_ready_symbols', []))}"
    )


if __name__ == "__main__":
    main()
