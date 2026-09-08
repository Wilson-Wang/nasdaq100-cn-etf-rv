from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from etf_dataset.reporting import (
    build_product_ranking,
    choose_report_mode,
    read_json,
    render_daily_report,
    snapshot_state,
)


def _read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.exists() or not path.stat().st_size:
        return pd.DataFrame()
    return pd.read_csv(path, **kwargs)


def main() -> None:
    data = Path("data")
    manifest = json.loads((data / "manifest.json").read_text())
    symbols = [str(value) for value in manifest.get("universe", [])]
    pair_analysis = _read_csv(data / "pair_analysis.csv", dtype={"symbol_i": "string", "symbol_j": "string"})
    product_quality = _read_csv(data / "product_quality.csv", dtype={"symbol": "string"})
    portfolio_status = read_json(data / "portfolio_status.json", {"status": "UNKNOWN", "selected_pairs": 0})
    health = read_json(data / "research_health.json", {"status": "UNKNOWN", "alerts": []})
    previous = read_json(data / "report_state.json", None)

    ranking = build_product_ranking(product_quality, pair_analysis, symbols)
    ranking.to_csv(data / "product_value_ranking.csv", index=False)
    if not ranking.empty:
        ranking.to_parquet(data / "product_value_ranking.parquet", index=False)

    current = snapshot_state(manifest, pair_analysis, health)
    mode, reasons = choose_report_mode(current, previous)
    report = render_daily_report(
        manifest,
        ranking,
        pair_analysis,
        portfolio_status,
        health,
        mode,
        reasons,
    )
    (data / "daily_report.md").write_text(report)
    current["report_mode"] = mode
    current["report_reasons"] = reasons
    (data / "report_state.json").write_text(
        json.dumps(current, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"daily_report mode={mode} reasons={reasons} products={len(ranking)} pairs={len(pair_analysis)}")


if __name__ == "__main__":
    main()
