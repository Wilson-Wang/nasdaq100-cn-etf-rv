from __future__ import annotations

import argparse
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Query local ETF dataset with DuckDB")
    parser.add_argument(
        "sql",
        help="SQL to execute; views: prices, nav, pcf, snapshot, factors, source_runs",
    )
    args = parser.parse_args()

    con = duckdb.connect(database=":memory:")
    for view, filename in {
        "prices": "etf_prices.parquet",
        "nav": "etf_nav.parquet",
        "pcf": "etf_pcf.parquet",
        "snapshot": "etf_snapshot.parquet",
        "factors": "factor_inputs.parquet",
        "source_runs": "source_runs.parquet",
    }.items():
        path = ROOT / "data" / filename
        if path.exists():
            escaped = str(path).replace("'", "''")
            con.execute(f"create view {view} as select * from read_parquet('{escaped}')")
    result = con.execute(args.sql).df()
    print(result.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
