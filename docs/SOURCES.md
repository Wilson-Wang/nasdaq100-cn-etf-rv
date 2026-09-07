# Data sources and fallback policy

## Historical exchange prices

### 1. Baostock (`source_priority=10`)

Default historical price source. Baostock is independent of Eastmoney and exposes security master records where ETF is a distinct security type, plus historical K-line queries. The pipeline requests daily, unadjusted OHLCV/amount data.

### 2. AKShare / Eastmoney (`source_priority=20`)

Fallback through `fund_etf_hist_em` when Baostock fails or returns no rows.

## Historical NAV

### AKShare / Eastmoney (`source_priority=10`)

`fund_etf_fund_info_em` provides historical unit NAV, accumulated NAV, daily growth rate, subscription status and redemption status.

NAV is not inferred from IOPV, market price, or an unrelated provider when the historical fund NAV endpoint is unavailable. Missing input remains missing and is recorded in the manifest.

## Latest market snapshot

### AKShare / Eastmoney (`source_priority=10`)

`fund_etf_spot_em` provides market last price, IOPV estimate, discount rate, volume/amount, turnover and best bid/ask fields when available.

## Why not a database?

The universe is small and daily frequency is modest. Versioned CSV plus Parquet provides:

- transparent diffs and source provenance;
- no service credentials or database maintenance;
- direct use from Python, DuckDB, Polars, pandas or Arrow;
- reproducible point-in-time snapshots through Git history.

If dataset size later becomes materially larger, the file layout can be partitioned by year/symbol without changing the source adapters.
