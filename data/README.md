# Dataset schema

Generated files are committed by the update workflow. CSV is the canonical, human-diffable representation; Parquet is a derived mirror for fast local analytics.

## `etf_prices`

Primary key: `symbol, date`.

Important fields: `open`, `high`, `low`, `close`, `preclose`, `volume`, `amount`, `turnover`, `pct_change`, `trade_status`, `source`, `source_priority`, `ingested_at_utc`.

Prices are stored **unadjusted** because cross-ETF premium/discount work must preserve the actually traded market price on each date.

## `etf_nav`

Primary key: `symbol, nav_date`.

Fields: `unit_nav`, `accumulated_nav`, `daily_growth_pct`, `subscription_status`, `redemption_status`, plus provenance fields.

Downstream models must align each trading date to the NAV that was actually available at that point in time. Do not forward-fill a future published NAV backward into an earlier signal.

## `etf_snapshot`

Primary key: `symbol, data_date`.

Fields include `last`, `iopv`, `discount_rate_pct`, market activity, best bid/ask when the source exposes them, and provenance timestamps.

## Source priority

Lower is preferred during duplicate resolution. A higher-priority historical row is not silently overwritten by a later fallback-source download.
