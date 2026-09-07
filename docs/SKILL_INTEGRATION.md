# Integration with Nasdaq-100 ETF Relative-Value Skill v2.1

This repository is the canonical data layer for the downstream `nasdaq100-cn-etf-relative-value` skill.

The skill is **dataset-first**, **multi-source**, **point-in-time**, and **coverage-aware**. It must not assume that a non-empty source response covers the requested history, and it must preserve source provenance and availability time.

## 1. Current tables

The repository currently provides:

- `data/etf_prices.csv` / `.parquet`
- `data/etf_nav.csv` / `.parquet`
- `data/etf_snapshot.csv` / `.parquet`
- `data/manifest.json`

The analysis must read `manifest.json` before running models and use actual table date ranges and aligned pair observation counts, not requested lookback length.

## 2. Historical price coverage

Baostock is the preferred daily-price source. AKShare/Eastmoney is a supplement when the primary source fails, returns empty, or materially under-covers the requested interval.

A first-non-empty fallback policy is not acceptable because a truncated primary response can silently leave a large historical hole. When multiple sources overlap, lower `source_priority` wins and the secondary source only fills missing dates.

Each price row now carries `is_tradable`. Pair-model observations should be valid only when both ETF legs are tradable; suspended, zero-volume or invalid-price rows must be excluded from Robust Z history, AR/half-life estimation and executable P&L backtests.

## 3. Point-in-time requirements

The skill must never use information that was unavailable at the signal time.

For NAV, the target schema should distinguish:

- `nav_date`
- `published_at`
- `available_at`
- `pit_verified`

Until historical `available_at` is available, strict NAV-premium backtests must be labeled `PIT_UNVERIFIED` unless a conservative availability rule can be established.

For each pair `(i, j)` at signal time `t`, the NAV-premium model should use the latest **common NAV date** for which both values were available by the signal cutoff.

## 4. Three-anchor fair-value framework

Skill v2.1 distinguishes three valuation anchors instead of treating official NAV as the only fair-value estimate:

1. **Official NAV** — disclosure and long-history anchor.
2. **IOPV** — intraday primary/secondary-market reference when available at the same time slice.
3. **Model Fair Value** — optional point-in-time estimate using a known NAV base, a Nasdaq-100/index proxy factor and an aligned FX factor.

These anchors must not be mixed across the two ETF legs inside one pair spread. If multiple reliable anchors materially disagree on direction, the pair is downgraded to at most `WATCH`.

Target fair-value inputs must preserve source and `available_at` for both the index proxy and FX series.

## 5. No-lookahead model contract

For a signal on day `t`:

- 60-day median/MAD ends at `t-1`.
- 120-day AR(1), ADF and half-life end at `t-1`.
- Regime information includes only events/PCF states known by the signal cutoff.
- Walk-forward backtests refit using only information available at each historical signal date.
- EOD signals enter no earlier than the next tradable session unless a different execution convention is explicitly modeled.

## 6. Primary-market regime and PCF

The next dataset extension should add `etf_pcf` with fields such as:

- `symbol`, `date`
- `creation_allowed`, `redemption_allowed`
- `net_creation_limit`, `net_redemption_limit`
- `creation_unit`
- `estimated_cash_component`
- `cash_substitution_limit`
- `creation_cash_premium`, `redemption_cash_discount`
- `source`, `available_at`

Regime interpretation is directional:

- creation restrictions weaken high-premium compression trades;
- redemption restrictions weaken discount-repair trades;
- both restricted or stale/unknown PCF state can cap a pair at `WATCH` or disable it.

`etf_events` should also support market-maker additions/removals and primary-market status changes.

## 7. Historical event definition and realized return

Do not count consecutive `abs(Robust Z) >= 2` days as independent events. A new event requires a threshold crossing and reset below the configured reset level before another event can be counted.

AR(1) expected convergence is a forecast, not realized investment return. Historical performance must use executable ETF returns:

`realized_relative_return = Return(rotation_in) - Return(rotation_out)`

`realized_net_alpha = realized_relative_return - applicable_cost`

Cost models must distinguish `rotation_cost` from a complete `round_trip_cost`.

## 8. Hard gates vs pair ranking

Skill v2.1 separates **eligibility** from **ranking**.

A formal `TRADE` must pass hard gates for:

- `abs(Robust Z) >= 2.0`
- `0 < Half-Life < 12`
- stationarity/stability gate
- `Net Expected Convergence 5d >= 0.8%`
- historical win-rate gate
- directionally compatible Regime
- at least 120 aligned and tradable pair observations
- walk-forward backtest
- PIT/freshness/source-quality gates
- multi-anchor consistency when multiple reliable anchors are available

Only after eligibility passes is `PairScore` used to rank candidates. This avoids treating correlated components such as Z, mean-reversion speed and expected convergence as independent pieces of evidence. `STRONG TRADE` may still require `PairScore >= 85` plus all hard gates.

## 9. Statistical robustness roadmap

For sufficiently long history, downstream research should add:

- ADF + KPSS joint stationarity diagnostics;
- structural-break diagnostics such as Zivot-Andrews or equivalent;
- half-life bootstrap intervals / `P(HL < 12)`;
- 120-day vs 250-day parameter consistency;
- genuine out-of-sample tracking from a frozen model version.

Each frozen model should record `model_version`, `parameter_hash`, `training_end_date`, `oos_start_date`, and the manifest/checksum used to generate each live signal.

## 10. Target dataset extensions

### NAV
`published_at`, `available_at`, `availability_source`, `pit_verified`.

### PCF
Daily creation/redemption status, limits, substitution parameters, source and availability time.

### Fair-value inputs
Point-in-time Nasdaq-100/index proxy and USD/CNY or USD/CNH series with source and `available_at`.

### Metadata
AUM, shares, fees, inception date, tracking error, tracking index and effective/available dates.

### Events
Point-in-time event records including `PRIMARY_MARKET_STATUS_CHANGE`, `MARKET_MAKER_ADDED`, and `MARKET_MAKER_REMOVED`.

### Source Runs
Per-run source, timing, success, row count, min/max date and error diagnostics.

## 11. Current known limitation

The current requested lookback can be materially longer than actual available price history. Skill v2.1 never infers coverage from request parameters. The new price-source logic attempts to supplement materially partial Baostock history, but successful extension of history still depends on the secondary source actually returning older rows. Validation and downstream models must continue to use actual aligned observations.
