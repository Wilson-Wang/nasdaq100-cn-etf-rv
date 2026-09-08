# Model v3 experiment: common premium factor + ETF residuals

This experiment complements the formal pair model. It does **not** replace the point-in-time, stationarity, regime, liquidity, cost, or walk-forward gates defined by the relative-value skill.

## Why add a common-factor layer?

Twelve Nasdaq-100 ETFs generate 66 unordered pairs, but those pair spreads are highly dependent because the products share the same underlying index and often share a market-wide premium regime.

The experimental model first decomposes each ETF's official-NAV log premium:

`x_i,t = log(price_i,t / nav_i,t)`

into:

`x_i,t = F_t + alpha_i,t + epsilon_i,t`

where:

- `F_t` is the cross-sectional median premium on date `t`;
- `alpha_i,t` is the ETF's trailing structural residual level, estimated using prior observations only;
- `epsilon_i,t` is the current idiosyncratic residual.

The current implementation is deliberately simple and robust:

1. `F_t = cross-sectional median(x_i,t)`.
2. `raw_residual_i,t = x_i,t - F_t`.
3. `alpha_i,t` is a trailing median of `raw_residual` using only observations through `t-1`.
4. `epsilon_i,t = raw_residual_i,t - alpha_i,t`.
5. A no-lookahead robust z-score is calculated on `epsilon` for diagnostics.

## Candidate generation

The most positive residuals are treated as relatively expensive candidates; the most negative residuals are treated as relatively cheap candidates.

The command:

```bash
python scripts/analyze_factor_residuals.py
```

prints a cheap-to-expensive ranking plus experimental rotate-out -> rotate-in candidate combinations.

Every candidate is labeled:

`EXPERIMENTAL_CANDIDATE_ONLY`

A factor candidate is never a `TRADE` by itself. It must still pass the formal pair model's hard gates.

## Point-in-time limitation

The current historical NAV dataset does not yet contain verified historical publication/availability timestamps. Therefore `build_official_nav_premium_history` marks rows `PIT_UNVERIFIED` unless `pit_verified` is explicitly supplied by the source data.

The factor model can currently be used for exploratory cross-sectional research, but not to manufacture a formal historical trading signal from unverified NAV timing.

## Current IOPV anchor

Use:

```bash
python scripts/current_valuation_anchors.py
```

to derive from the latest snapshot:

- executable mid price when valid bid/ask exist;
- bid/ask spread in basis points;
- last-price IOPV premium;
- mid-price IOPV premium;
- whether a usable IOPV anchor is available.

IOPV is a current-time anchor and must not be backfilled as historical NAV.

## Signal-readiness check

Use:

```bash
python scripts/check_signal_readiness.py
```

to obtain per-symbol states such as:

- `FRESH`
- `INSUFFICIENT_DATA`
- `STALE_PRICE`
- `STALE_NAV`
- `STALE_SNAPSHOT`
- `PIT_UNVERIFIED`
- `SOURCE_DEGRADED`

Structural validation and cached coverage are intentionally separated from formal signal readiness.

## Next implementation steps

1. Add verified NAV `published_at` / `available_at` history.
2. Add PCF tables and directional creation/redemption friction.
3. Add point-in-time Nasdaq proxy and FX inputs for a model fair-value anchor.
4. Persist `source_runs` so latest fetch success is separate from cached table coverage.
5. Compare the factor-residual candidate generator with the 66-pair model in frozen live OOS evaluation.
