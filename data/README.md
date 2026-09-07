# Dataset schema

Generated files are committed by the update workflow. CSV is the canonical, human-diffable representation; Parquet is a derived mirror for fast local analytics.

## `etf_prices`

Primary key: `symbol, date`.

Important fields: `open`, `high`, `low`, `close`, `preclose`, `volume`, `amount`, `turnover`, `pct_change`, `trade_status`, `is_tradable`, `source`, `source_priority`, `ingested_at_utc`.

Prices are stored **unadjusted** because cross-ETF premium/discount work must preserve the actually traded market price on each date.

`is_tradable` is a model-safety field. The current adapters mark a row tradable only when price and volume are positive and, when the source exposes it, `trade_status` is active. Pair models should count an observation only when **both ETF legs are tradable**. Suspended/zero-volume observations must not be used to shrink MAD, estimate half-life, or calculate executable historical P&L.

### Coverage-aware source supplementation

Historical price sourcing is not a first-non-empty fallback chain. Baostock remains the preferred source, but the pipeline checks whether its returned dates materially cover the requested window. When coverage is clearly partial, AKShare/Eastmoney is queried as a supplement. Overlapping dates retain the lower `source_priority`; the secondary source fills historical holes instead of overwriting preferred rows.

The coverage check is deliberately tolerant of mainland public holidays and recently listed products. Downstream models must still use actual aligned observation counts rather than the requested lookback length.

## `etf_nav`

Primary key: `symbol, nav_date`.

Fields: `unit_nav`, `accumulated_nav`, `daily_growth_pct`, `subscription_status`, `redemption_status`, plus provenance fields.

Downstream models must align each trading date to the NAV that was actually available at that point in time. Do not forward-fill a future published NAV backward into an earlier signal. Target extensions are `published_at`, `available_at`, `availability_source`, and `pit_verified`.

## `etf_snapshot`

Primary key: `symbol, data_date`.

Fields include `last`, `iopv`, `discount_rate_pct`, market activity, best bid/ask when the source exposes them, and provenance timestamps.

IOPV is a current-time anchor, not a substitute for historical official NAV. The v2.1 skill treats official NAV, IOPV, and model fair value as separate valuation anchors.

## Planned v2.x tables

- `etf_pcf`: creation/redemption status, limits, cash substitution parameters and availability time.
- `etf_metadata`: AUM, fees, inception date, tracking error and other low-frequency product quality inputs.
- `etf_events`: point-in-time regime events, including primary-market status and market-maker changes.
- `source_runs`: per-run source success/failure and coverage diagnostics.

## Source priority

Lower is preferred during duplicate resolution. A higher-priority historical row is not silently overwritten by a later fallback-source download.
