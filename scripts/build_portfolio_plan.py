from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from etf_dataset.portfolio import PortfolioConfig, build_portfolio_plan


def main() -> None:
    data = Path("data")
    pair_path = data / "pair_analysis.csv"
    pair_analysis = (
        pd.read_csv(pair_path, dtype={"symbol_i": "string", "symbol_j": "string"})
        if pair_path.exists() and pair_path.stat().st_size
        else pd.DataFrame()
    )
    plan, summary = build_portfolio_plan(pair_analysis, PortfolioConfig())
    plan.to_csv(data / "portfolio_plan.csv", index=False)
    if not plan.empty:
        plan.to_parquet(data / "portfolio_plan.parquet", index=False)
    (data / "portfolio_status.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        f"portfolio status={summary['status']} eligible={summary['eligible_pairs']} "
        f"selected={summary['selected_pairs']} conflicts={summary['conflict_rejections']}"
    )


if __name__ == "__main__":
    main()
