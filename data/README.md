# Dataset schema

Generated files are committed by the update workflow. CSV is the canonical, human-diffable representation; Parquet is a derived mirror for fast local analytics.

## `etf_prices`

Primary key: `symbol, date`.

Important fields: `open`, `high`, `low`, `close`, `preclose`, `volume`, `amount`, `turnover`, `pct_change`, `trade_status`, `is_tradable`, `source`, `source_priority`, `ingested_at_utc`.

Prices are stored **unadjusted** because cross-ETF premium/discount work must preserve the actually traded market price on each date.

`is_tradable` is a model-safety field. The current adapters mark a row tradable only when price and volume are positive and, when the source exposes it, `trade_status` is active. Pair models should count an observation only when **both ETF legs are tradable**. Suspended/zero-volume observations must not be used to shrink MAD, estimate half-life, or calculate executable historical P&L.

### Coverage-aware source supplementation

Historical price sourcing is not a first-non-empty fallback chain. Source order is Baostock, AKShare/Eastmoney, then AKShare/Sina. Coverage is recomputed after each source; another source is queried only while the requested history remains materially incomplete. Overlapping dates retain the lower `source_priority`, so supplements fill holes without overwriting preferred rows.

The coverage check is deliberately tolerant of mainland public holidays and recently listed products. Downstream models must still use actual aligned observation counts rather than the requested lookback length.

## `etf_nav`

Primary key: `symbol, nav_date`.

Fields: `unit_nav`, `accumulated_nav`, `daily_growth_pct`, `subscription_status`, `redemption_status`, plus provenance fields.

Downstream models must align each trading date to the NAV that was actually available at that point in time. Do not forward-fill a future published NAV backward into an earlier signal. Current historical NAV rows persist `pit_verified=false` until publication/availability time can be substantiated.

## `etf_pcf`

Primary key: `symbol, date`.

This table is collected prospectively from the official Shanghai and Shenzhen exchange PCF disclosures. It includes creation/redemption permissions, creation unit, creation/redemption and net limits, cash components, cash-substitution limit, IOPV publication flag, component count, aggregate cash-premium/discount diagnostics, and provenance/availability fields.

For SSE, `available_at` uses the exchange's explicit 08:30 China-time PCF disclosure time and is marked `availability_verified=true`. For SZSE, the exchange rule guarantees publication before market open but does not provide an exact timestamp in the source used by this pipeline; the dataset therefore uses a conservative 09:15 China-time bound, marks `availability_verified=false`, and records the rule in `availability_method`.

The collector builds PCF history **forward from the date it is deployed**. It does not fabricate historical PCF rows or backfill a current file into earlier dates.

## `etf_snapshot`

Primary key: `symbol, data_date`.

Fields include `last`, `iopv`, `discount_rate_pct`, market activity, best bid/ask when the source exposes them, and provenance timestamps. Derived execution/valuation fields include `mid`, `bid_ask_spread_pct`, `last_iopv_premium_pct`, and `mid_iopv_premium_pct` when their raw inputs are valid.

IOPV is a current-time anchor, not a substitute for historical official NAV.

## `factor_inputs`

Primary key: `factor_name, factor_date`.

Current factors are Nasdaq-100 (`NDX`) and USD/CNH (`USDCNH`), used as raw inputs for the model-fair-value anchor. Because the daily endpoints do not expose publication timestamps, the pipeline assigns a deliberately conservative next-calendar-day 08:00 China-time `available_at`. This is identified as a model availability rule with `availability_verified=false`; it must not be presented as a source-published timestamp.

## Planned v2.x tables

- `etf_metadata`: AUM, fees, inception date, tracking error and other low-frequency product quality inputs.
- `etf_events`: point-in-time regime events, including primary-market status and market-maker changes.
- `source_runs`: per-run source success/failure and coverage diagnostics.

## Source priority

Lower is preferred during duplicate resolution. A preferred-source historical row is not silently overwritten by a later fallback-source download.
