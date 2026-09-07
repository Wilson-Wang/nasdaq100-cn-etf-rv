# Integration with Nasdaq-100 ETF Relative-Value Skill v2.0

This repository is the canonical data layer for the downstream `nasdaq100-cn-etf-relative-value` skill.

The skill is **dataset-first** and **multi-source**. It must not assume that all inputs come from Eastmoney, and it must preserve source provenance and point-in-time availability.

## 1. Current tables

The current repository provides:

- `data/etf_prices.csv` / `.parquet`
- `data/etf_nav.csv` / `.parquet`
- `data/etf_snapshot.csv` / `.parquet`
- `data/manifest.json`

The analysis must read `manifest.json` before running models and must use the **actual table date ranges and aligned pair observation counts**, not the requested lookback range, to determine whether 60/120/250-day model requirements are satisfied.

## 2. Point-in-time requirements

The skill must never use information that was unavailable at the signal time.

For NAV, the target schema should eventually distinguish:

- `nav_date`: date the NAV belongs to
- `published_at`: publication time when known
- `available_at`: first time the value can reasonably be used by the model
- `pit_verified`: whether the historical availability time has been verified

Until historical `available_at` is available, strict NAV-premium backtests must be labeled `PIT_UNVERIFIED` unless a conservative availability rule can be established.

For each pair `(i, j)` at signal time `t`, the premium-spread model should use the latest **common NAV date** for which both NAV values were available by the signal cutoff. Do not mix asynchronously available NAVs and call the result a precise same-time premium spread.

## 3. No-lookahead model contract

For a signal on day `t`:

- 60-day median/MAD must use observations ending at `t-1`.
- 120-day AR(1), ADF and half-life must use observations ending at `t-1`.
- Regime information must include only events published by the signal cutoff.
- Walk-forward backtests must refit using only information available at each historical signal date.

A day must not be included in the historical window used to score itself.

## 4. Historical event definition

Do not count every consecutive day with `abs(Robust Z) >= 2` as an independent event.

Default event logic:

1. New entry when `abs(Z_t) >= 2` and the previous valid day was below 2.
2. Do not open another event while the current evaluation window is active.
3. Require a reset below `abs(Z) < 1` before another threshold crossing is counted.

This avoids inflating signal count and historical win rate.

## 5. Expected convergence vs realized return

The AR(1) model may estimate:

`expected_d_5 = mu + phi^5 * (d_t - mu)`

and:

`expected_convergence_5d = abs(d_t - expected_d_5)`

This is a **model forecast of spread convergence**, not realized investment return.

Historical performance must use executable ETF returns. For a signal that rotates from ETF `i` into ETF `j`:

`realized_relative_return = Return(j) - Return(i)`

and:

`realized_net_alpha = realized_relative_return - realized_cost`

For EOD signals, the default strict backtest should assume the signal is known only after the close and should enter no earlier than the next tradable session.

## 6. Pair-score reproducibility

The downstream skill defines deterministic component mappings for:

- Mispricing (`M`)
- Mean Reversion (`R`)
- Expected Edge (`E`)
- Historical (`H`)
- Liquidity (`L`)
- Regime (`G`)

with:

`PairScore = 0.20*M + 0.25*R + 0.25*E + 0.15*H + 0.10*L + 0.05*G`

The data layer should expose enough raw inputs for these components. Analysis code must not improvise alternative scoring formulas from run to run.

## 7. Formal signal minimums

The downstream skill requires, at minimum:

- `abs(Robust Z) >= 2.0`
- `0 < Half-Life < 12`
- stationarity gate passed
- `Net Expected Convergence 5d >= 0.8%`
- `Adjusted Win Rate >= 65%`
- `PairScore >= 75`
- no disabling Regime shift
- at least 120 aligned pair observations for the full model
- PIT and freshness gates passed
- no critical source failure affecting the pair

Strong signals require `PairScore >= 85` plus all other hard gates.

Missing inputs must never be compensated for by lowering thresholds.

## 8. Data-quality state

The skill needs to distinguish:

- `FRESH`
- `STALE_NAV`
- `STALE_PRICE`
- `PIT_UNVERIFIED`
- `SOURCE_DEGRADED`
- `INSUFFICIENT_DATA`

A successful structural validation is not the same as fresh, signal-ready data.

If the latest fetch fails for a symbol but cached historical rows remain, the manifest must keep the fetch failure explicit. The skill may continue only if the failed refresh does not affect the current signal inputs; otherwise the pair is downgraded to at most `WATCH`.

## 9. Target dataset extensions

### NAV fields

Add when feasible:

- `published_at`
- `available_at`
- `availability_source`
- `pit_verified`

### `etf_metadata`

Target fields:

- `symbol`
- `effective_date`
- `aum`
- `shares`
- `management_fee`
- `custodian_fee`
- `total_fee`
- `tracking_error`
- `inception_date`
- `tracking_index`
- `source`
- `available_at`

### `etf_events`

Target fields:

- `symbol`
- `event_type`
- `published_at`
- `effective_at`
- `title`
- `severity`
- `source`
- `source_url`
- `ingested_at_utc`

### `source_runs`

Target fields:

- `run_id`
- `dataset`
- `source`
- `started_at`
- `finished_at`
- `success`
- `rows_fetched`
- `min_date`
- `max_date`
- `error_type`
- `error_message`

## 10. Product ranking separation

The downstream skill separates:

- **Product Quality Score (PQS)**: fees, liquidity, AUM, tracking error, structural stability.
- **Tactical Value Score (TVS)**: current relative value, expected convergence, liquidity and Regime.

The data layer should support both, but pair-trade ranking and long-term product-quality ranking remain separate outputs.

## 11. Current known limitation

The current requested lookback may be longer than the actual available price history. The skill must use the actual `min_date`, `max_date` and aligned pair count from the dataset. It must never infer 250-day coverage merely because the pipeline was asked to fetch 450 calendar days.
