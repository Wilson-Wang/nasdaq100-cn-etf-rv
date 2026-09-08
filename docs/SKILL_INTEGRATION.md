# Integration with Nasdaq-100 ETF Relative-Value Skill v2.1.x

This repository is the canonical data layer for the downstream `nasdaq100-cn-etf-relative-value` skill.

The integration contract is **dataset-first**, **multi-source**, **point-in-time**, **coverage-aware**, and now explicitly separates **model readiness** from **execution readiness**.

## 1. Canonical data products

Current persisted outputs are:

- `data/etf_prices.csv` / `.parquet`
- `data/etf_nav.csv` / `.parquet`
- `data/etf_pcf.csv` / `.parquet`
- `data/etf_snapshot.csv` / `.parquet`
- `data/factor_inputs.csv` / `.parquet`
- `data/source_runs.csv` / `.parquet`
- `data/execution_readiness.json`
- `data/manifest.json`

The Skill must read `manifest.json` before running models. Requested lookback length is never evidence of actual coverage; use per-symbol and aligned-pair observations.

## 2. Two distinct readiness gates

### 2.1 Model readiness

`manifest.json -> quality.formal_signal_ready_symbols` means that the historical/PIT inputs are sufficient to run the formal relative-value model.

It does **not** mean that an ETF currently has an executable order book.

Current model-readiness checks include:

- at least 120 usable historical price observations;
- current price/NAV/snapshot freshness;
- NAV point-in-time usability by the information cutoff;
- no critical source failure;
- current primary-market state when official PCF is available.

The current dataset has 250+ usable observations for all 12 ETFs, but pair eligibility still requires the pair's own aligned, tradable history.

### 2.2 Execution readiness

`manifest.json -> execution` and `data/execution_readiness.json` are the execution gate.

A symbol is `execution_ready=true` only when the current-day snapshot has:

- a positive last price;
- positive observed volume or amount;
- a valid best bid/ask with `bid1 > 0` and `ask1 >= bid1`.

Missing bid/ask values must **never** be filled from an older trading day merely to manufacture a trade signal.

For a formal pair signal, **both ETF legs must be execution-ready**. If the model passes but either leg is execution-blocked, cap the result at `WATCH` until execution inputs recover.

Typical execution states are:

- `NO_CURRENT_DAY_SNAPSHOT`
- `NO_CURRENT_PRICE`
- `NO_TRADING_ACTIVITY`
- `NO_ACTIVE_BOOK`
- `EXECUTION_READY`

## 3. Historical price contract

Historical price sourcing is coverage-aware rather than first-non-empty:

1. Baostock — preferred source;
2. AKShare/Eastmoney — supplementation;
3. AKShare/Sina — independent supplementation when earlier sources under-cover or fail.

Overlapping dates retain the lower `source_priority`; fallback sources fill holes rather than overwrite preferred rows.

Each row includes `is_tradable`. Robust Z, AR/half-life estimation and executable P&L backtests may use a date only when **both pair legs are tradable**.

A symbol reaching 250 usable observations switches to overlapping incremental refresh; incomplete history remains in full-backfill mode.

## 4. NAV point-in-time contract

Historical NAV has two separate concepts:

- `pit_verified=true`: exact/source-backed availability timing has been proven;
- `pit_usable=true`: the observation is safe for no-lookahead use by a conservative availability bound, even though the exact publication time is unverified.

Current historical QDII NAV generally remains:

- `pit_verified=false`
- `availability_verified=false`
- `pit_usable=true` once its conservative bound has passed.

The conservative bound uses the earliest safe value between observed first ingestion and the QDII regulatory T+2 mainland-exchange-workday disclosure bound. Observed ETF trading sessions are used rather than generic weekdays where possible.

This state must be reported as `PIT_CONSERVATIVE`, not silently promoted to exact PIT.

For pair `(i, j)` at information cutoff `t`, use only NAV values whose `available_at <= t`, and use the latest common eligible NAV date when the NAV-premium pair model requires synchronized anchors.

## 5. Information cutoff vs EOD model date

Do not treat all daily inputs as if they share the same cutoff.

- Daily market-price models use the latest completed mainland trading session (`model_as_of_date`).
- PCF and other information published during the current morning may already be usable before the current trading day has completed.
- NAV/PCF eligibility is determined by `available_at <= information_cutoff_utc`.

This prevents both lookahead and the opposite error of discarding information that was genuinely known before trading.

## 6. Three-anchor fair-value framework

The Skill distinguishes:

1. **Official NAV** — disclosure / historical anchor;
2. **IOPV** — intraday ETF reference when valid for the same time slice;
3. **Model Fair Value** — optional estimate using a known NAV base, Nasdaq-100 factor and aligned FX factor.

Snapshot-derived fields include, when valid:

- `mid`
- `bid_ask_spread_pct`
- `last_iopv_premium_pct`
- `mid_iopv_premium_pct`

Crossed or missing quotes remain missing.

Factor inputs currently support:

- `NDX` from Sina via AKShare;
- `USDCNH` from Eastmoney when available;
- official SAFE/BOC `USDCNY` as fallback when `USDCNH` fails.

If the FX source falls back from CNH to CNY, use that fallback **consistently throughout the same Model Fair Value calculation** and surface the degradation. Do not mix CNH and CNY within one comparison merely to maximize data availability.

If reliable anchors materially disagree on direction, cap the pair at `WATCH`.

## 7. Official PCF / primary-market regime

`etf_pcf` is a live point-in-time table, not a planned extension.

Current collection uses official exchange sources for both SSE and SZSE ETFs and persists:

- creation/redemption permission;
- creation unit;
- creation/redemption and net limits where disclosed;
- cash-substitution parameters;
- component-level premium/discount diagnostics;
- `available_at` and provenance.

SSE uses the exchange's 08:30 China-time disclosure timing. SZSE uses a conservative pre-open availability bound while retaining that the exact timestamp is not source-verified.

PCF history is accumulated prospectively. Never fabricate historical PCF rows to make old backtests look complete.

When current official PCF is unavailable, NAV-page subscription/redemption text may be used only as a **low-confidence fallback**. Only explicit restriction language such as `暂停申购` or `暂停赎回` may be promoted to `RESTRICTED`; labels such as `场内买入` / `场内卖出` are secondary-market descriptions and remain `UNKNOWN`.

Regime interpretation is directional:

- creation restriction weakens high-premium compression trades;
- redemption restriction weakens discount-repair trades.

## 8. No-lookahead model contract

For a signal on day `t`:

- 60-day median/MAD estimation ends at `t-1`;
- 120-day AR(1), ADF and half-life estimation ends at `t-1`;
- walk-forward backtests refit using only information available at each historical cutoff;
- EOD model signals enter no earlier than the next tradable session unless another execution convention is explicitly modeled;
- event/PCF/regime information must satisfy its own `available_at` cutoff.

## 9. Historical event and realized-return contract

Do not count every consecutive `abs(Robust Z) >= 2` day as an independent event.

A new event requires a threshold crossing and reset below the configured reset level before another event is counted.

AR(1) expected convergence is a forecast, not realized investment return. Historical strategy evaluation must use executable ETF returns:

`realized_relative_return = Return(rotation_in) - Return(rotation_out)`

`realized_net_alpha = realized_relative_return - applicable_cost`

Distinguish `rotation_cost` from a complete `round_trip_cost`.

## 10. Hard gates before ranking

PairScore ranks eligible candidates; it must not substitute for eligibility.

A formal `TRADE` candidate must pass at least:

- `abs(Robust Z) >= 2.0`;
- `0 < Half-Life < 12`;
- stationarity/stability gate;
- `Net Expected Convergence 5d >= 0.8%`;
- historical performance gate;
- directionally compatible regime;
- at least 120 aligned tradable pair observations;
- walk-forward / PIT / freshness gates;
- multi-anchor consistency when applicable;
- **execution readiness for both legs at the actual execution decision point**.

`STRONG TRADE` may additionally require `PairScore >= 85` and all hard gates. Do not lower thresholds to manufacture a signal.

## 11. Source-run audit

Every logical source call is recorded in `source_runs` with:

- run id;
- resource/symbol;
- requested range;
- duration;
- status (`SUCCESS`, `DEGRADED`, `FAILED`);
- row count and observed min/max dates;
- selected source(s);
- errors.

A degraded primary source is not automatically critical when a fallback produced usable current data. The Skill should distinguish `SOURCE_DEGRADED` from `CRITICAL_SOURCE_FAILURE`.

## 12. Remaining extensions

Still useful but not required for the current data model:

- `etf_metadata`: AUM, fees, shares, inception date, tracking error and effective dates;
- `etf_events`: point-in-time premium warnings, temporary halts/resumptions, market-maker additions/removals and other structural events;
- an official exchange holiday calendar to replace the remaining weekday freshness heuristic;
- a repository-native pair analysis engine implementing Robust Z, AR(1), stationarity, walk-forward event backtests and realized rotation P&L.

The downstream Skill must never interpret missing execution data, conservative PIT timing, source fallback, or prospective-only PCF history as if those limitations did not exist.
