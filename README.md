# Nasdaq-100 China ETF Dataset

A reproducible, database-free dataset pipeline for China-listed ETFs tracking the Nasdaq-100 index.

The repository is designed to support relative-value research across same-index ETFs: premium/discount history, pair spreads, robust z-scores, half-life estimates, and rotation research. It stores normalized source data in versioned files instead of requiring MySQL/PostgreSQL or a hosted database.

## Design goals

- **Database-free by default**: canonical CSV files plus Parquet mirrors; query locally with DuckDB.
- **Multi-source**: use Baostock for historical exchange prices first, with AKShare/Eastmoney as fallback; use AKShare fund interfaces for NAV and snapshots.
- **Provenance-first**: every row contains `source`, `source_priority`, and `ingested_at_utc`.
- **Failure-tolerant**: a failed endpoint does not fabricate values or erase existing history; failures are written to `data/manifest.json`.
- **Incremental**: repeated runs upsert by business key and keep stable historical rows.
- **Automation-ready**: GitHub Actions can refresh the dataset after China market close on weekdays.

## ETF universe

The initial universe is in `config/etfs.json` and currently contains 12 China-listed Nasdaq-100 ETFs:

`159941`, `159501`, `159513`, `159632`, `159659`, `159660`, `159696`, `513100`, `513110`, `513300`, `513390`, `513870`.

The configuration is explicit so universe changes are reviewable in Git history.

## Data outputs

After the first successful update, the pipeline creates:

| File | Grain | Purpose |
|---|---|---|
| `data/etf_prices.csv` | symbol + date | Daily OHLCV/amount history |
| `data/etf_prices.parquet` | symbol + date | Columnar mirror for analytics |
| `data/etf_nav.csv` | symbol + nav_date | Historical unit/accumulated NAV and subscription status |
| `data/etf_nav.parquet` | symbol + nav_date | Columnar mirror |
| `data/etf_snapshot.csv` | symbol + data_date | Latest market snapshot, IOPV/premium fields when available |
| `data/etf_snapshot.parquet` | symbol + data_date | Columnar mirror |
| `data/manifest.json` | one file | Row counts, date ranges, source mix, failures and checksums |

See `docs/SOURCES.md` and `data/README.md` for field definitions.

## Quick start

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python scripts/update_dataset.py --lookback-days 450
python scripts/validate_dataset.py
```

Query with DuckDB without running a database server:

```bash
python scripts/query_dataset.py \
  "select symbol, max(date) as last_date, count(*) as rows from prices group by 1 order by 1"
```

## Update behavior

Historical prices are fetched with this order:

1. **Baostock** — non-Eastmoney source, daily unadjusted bars.
2. **AKShare / Eastmoney** — fallback when Baostock fails or returns no rows.

Historical NAV currently uses AKShare's Eastmoney fund interface because NAV coverage is fund-specific and cannot safely be inferred from market prices. The dataset records the source explicitly rather than filling missing NAV from an unrelated field.

The source hierarchy is intentionally pluggable; additional adapters can be added without changing the storage schema.

## GitHub Actions

`.github/workflows/update-dataset.yml` runs on weekdays after China market close and can also be started manually. It commits dataset changes back to `main` only when files changed.

Scheduled workflows run from the default branch, and GitHub Actions supports scheduled workflow triggers. See GitHub documentation for operational details.

## Research boundary

This repository is a data layer. It does not place trades and does not label a pair as a formal trade signal merely because a single-day spread looks attractive. Downstream analysis should require adequate 60/120-day history and preserve point-in-time NAV alignment.

## License

MIT. Data retrieved from third-party public endpoints remains subject to the terms and rights of the underlying providers.
