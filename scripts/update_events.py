from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from etf_dataset.events import append_execution_history, build_event_table


def main() -> None:
    data = Path("data")
    execution_path = data / "execution_readiness.json"
    if not execution_path.exists():
        raise SystemExit("execution_readiness.json is required before event update")

    execution = json.loads(execution_path.read_text())
    history = append_execution_history(execution, data / "execution_status_history.csv")
    history.to_parquet(data / "execution_status_history.parquet", index=False)

    pcf_path = data / "etf_pcf.csv"
    pcf = pd.read_csv(pcf_path, dtype={"symbol": "string"}) if pcf_path.exists() else pd.DataFrame()
    events = build_event_table(pcf, history)
    events.to_csv(data / "etf_events.csv", index=False)
    if not events.empty:
        events.to_parquet(data / "etf_events.parquet", index=False)

    manifest_path = data / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["events_status"] = {
        "rows": int(len(events)),
        "pcf_history_start": (
            str(pd.to_datetime(pcf["date"], errors="coerce").min().date())
            if not pcf.empty and pd.to_datetime(pcf["date"], errors="coerce").notna().any()
            else None
        ),
        "execution_history_rows": int(len(history)),
        "coverage": [
            "PRIMARY_MARKET_STATUS_CHANGE from prospectively collected official PCF",
            "SECONDARY_MARKET_EXECUTION_ELIGIBILITY gained/lost from prospective snapshots",
        ],
        "limitations": [
            "no fabricated pre-deployment PCF history",
            "market-maker and free-text fund-announcement events are not yet source-normalized",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"events={len(events)} execution_history={len(history)}")


if __name__ == "__main__":
    main()
