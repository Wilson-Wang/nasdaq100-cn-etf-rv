# Integration with Nasdaq-100 ETF Relative-Value Skill v2.2

This repository is the canonical data and research layer for the downstream `nasdaq100-cn-etf-relative-value` Skill.

The contract is **dataset-first**, **multi-source**, **point-in-time**, **coverage-aware**, **gate-first**, and explicitly separates model research, execution eligibility, portfolio construction, challenger models, and prospective OOS evidence.

## 1. Canonical outputs

Base inputs:

- `data/etf_prices.csv` / `.parquet`
- `data/etf_nav.csv` / `.parquet`
- `data/etf_pcf.csv` / `.parquet`
- `data/etf_snapshot.csv` / `.parquet`
- `data/factor_inputs.csv` / `.parquet`
- `data/source_runs.csv` / `.parquet`
- `data/manifest.json`

Research outputs:

- `data/execution_readiness.json`
- `data/liquidity_scores.csv`
- `data/etf_metadata.csv`
- `data/product_quality.csv`
- `data/product_value_ranking.csv`
- `data/etf_events.csv`
- `data/pair_analysis.csv`
- `data/pair_events.csv`
- `data/model_registry.json`
- `data/oos_pair_states.csv`
- `data/portfolio_plan.csv`
- `data/portfolio_status.json`
- `data/history_depth.json`
- `data/regime_history_coverage.json`
- `data/research_health.json`
- `data/execution_cost_model.json`
- `data/factor_residual_ranking.csv`
- `data/factor_residual_candidates.csv`
- `data/factor_residual_status.json`
- `data/daily_report.md`
- `data/report_state.json`

Prospective execution observations are accumulated separately in `data/execution_history.csv` when the intraday capture workflow has live-book data.

The Skill must read `manifest.json`, `history_depth.json`, `regime_history_coverage.json`, and readiness outputs before interpreting model results. Requested lookback length is never evidence of actual coverage.

## 2. Model readiness vs execution readiness

`manifest.json -> quality.formal_signal_ready_symbols` means the historical/PIT inputs are sufficient to run the formal relative-value model. It does **not** mean the ETF has an immediately executable order book.

The EOD Pair Engine generates a signal after a completed mainland session and assumes entry no earlier than the next tradable session. Therefore two execution concepts are distinct:

- intraday `execution_ready`: requires a live valid bid/ask and current trading activity;
- EOD `next_session_eligible`: requires evidence the ETF traded in the signal session, with the live book re-checked at actual next-session execution.

Never fill a missing current book from an older day merely to manufacture an executable signal.

## 3. Historical coverage contract

Historical price sourcing is coverage-aware:

1. Baostock preferred;
2. AKShare/Eastmoney supplement;
3. AKShare/Sina independent supplement.

Overlaps retain the lower source priority. Pair model observations require both legs to be tradable.

`history_depth.json` reports, per symbol:

- unique tradable price observations;
- PIT-usable NAV observations;
- aligned upper bound;
- 120-day minimum status;
- 250-day research precheck;
- preferred 500-day depth.

Actual Pair alignment can be lower than the symbol-level upper bound. `pair_analysis.aligned_observations` must never exceed either leg's unique tradable price-date count; research validation enforces this to catch accidental many-to-many joins.

Deep-history backfill is a separate manual workflow. After backfill it rebuilds the manifest and every downstream research output so stored data and manifest coverage cannot silently diverge.

## 4. NAV point-in-time semantics

Historical NAV keeps two concepts separate:

- `pit_verified=true`: exact/source-backed availability timing has been proven;
- `pit_usable=true`: no-lookahead use is safe by a conservative availability bound even though exact publication time is not verified.

Historical QDII NAV without source publication timestamps remains `pit_verified=false`. The conservative availability policy uses first-seen evidence and the applicable QDII T+2 mainland trading-day disclosure bound. It must be reported as `PIT_CONSERVATIVE`, never as exact historical PIT.

For pair `(i,j)` at cutoff `t`, use only NAV with `available_at <= t`, and use the latest common eligible NAV date for the NAV-premium spread.

## 5. PCF and historical Regime coverage

Current PCF collection uses official SSE/SZSE sources and persists creation/redemption permissions, limits, creation unit, cash-substitution information, IOPV flag, availability metadata, and provenance.

Current official PCF may be used for current/future directional Regime gating when its `available_at` precedes the information cutoff.

Historical PCF is evidence-driven only. Missing old exchange files are **not** synthesized. `regime_history_coverage.json` explicitly reports each ETF as `FULL_OR_NEAR_FULL`, `PARTIAL`, `PROSPECTIVE_ONLY`, or `MISSING`.

Historical event/PCF Regime filters may be applied only on dates where PIT-verified historical event data actually exists. Missing historical PCF must never be interpreted as `NORMAL`.

Directional interpretation remains:

- creation restriction weakens high-premium compression;
- redemption restriction weakens discount-repair.

## 6. Three valuation anchors

The research stack distinguishes:

1. Official NAV;
2. IOPV when valid for the same time slice;
3. Model Fair Value using aligned Nasdaq-100 and FX factors.

Snapshot-derived fields retain missing/crossed quotes as missing.

Factor preference is NDX plus USDCNH when available; official SAFE/BOC USDCNY is the fallback. A fallback is surfaced and must be used consistently within one calculation. Anchor disagreement caps confidence rather than being averaged away.

## 7. Pair Engine v1.0.0

The frozen production research engine uses:

- 60-observation Robust Z estimated through `t-1`;
- MAD with IQR fallback;
- 120-observation AR(1) / Half-Life through `t-1`;
- ADF, KPSS diagnostics and rolling stability;
- crossing/reset event sampling;
- t+1 open entry and fifth future aligned-session close evaluation;
- realized relative return and net alpha after the configured rotation cost;
- direction-aware Regime and primary-market gates;
- liquidity-aware PairScore;
- Benjamini-Hochberg ADF FDR (`q <= 0.10`) as an additional formal gate.

Current frozen model version: `pair-engine-v1.0.0`. Its parameters must not be silently mutated. Any materially changed model must receive a new version and begin a new OOS record.

PairScore ranks candidates; it never substitutes for hard gates.

## 8. Formal signal gates

A formal `TRADE` requires all applicable hard gates, including:

- `abs(Robust Z) >= 2`;
- `0 < Half-Life < 12`;
- stationarity/stability rule;
- net expected convergence >= 0.8%;
- adjusted historical win-rate rule;
- at least 120 aligned usable observations;
- model/PIT/freshness readiness;
- directionally compatible Regime;
- EOD next-session eligibility / execution policy;
- BH/FDR gate.

`STRONG TRADE` is a stricter subset. Missing data never lowers thresholds. A challenger model may veto or annotate, but must not upgrade a failed production gate.

## 9. Product quality and tactical value

Product quality (`PQS`) is separate from tactical value (`TVS`).

PQS components are fees, liquidity, AUM, tracking quality and structural age/stability. When the metadata source omits reported AUM, the scoring layer may use `shares × latest unit NAV` as a transparent `PROXY_SHARES_TIMES_LATEST_NAV`; raw metadata remains unchanged and the proxy method must be visible.

Tracking error is currently an explicitly labelled NAV-vs-NDX×FX annualized proxy, not an official fund-company tracking-error statistic.

TVS aggregates directional pair evidence cross-sectionally. A combined `Overall = 60% PQS + 40% TVS` is emitted only when PQS coverage is at least 70%; otherwise Overall stays missing.

## 10. Portfolio research layer

Portfolio construction consumes only existing `TRADE` / `STRONG TRADE` rows and can never promote `WATCH` or `NO TRADE`.

Current conservative policy:

- at most three selected pairs;
- no ETF may appear in more than one simultaneous selected pair;
- at most 40% research allocation per pair;
- one pair therefore uses at most 40%, two at most 80%, with the remainder unallocated;
- ranking is based on net edge, PairScore and liquidity.

This is a research allocation, not an order-generation system.

## 11. OOS evidence

`model_registry.json` freezes the model hash. `oos_pair_states.csv` stores the complete daily 66-pair cross-section from the model's OOS start, not only later-profitable signals.

For `pair-engine-v1.0.0`:

- training end: 2026-09-08;
- OOS start: 2026-09-09.

Future OOS results cannot be backfilled or fabricated. Formal signals are evaluated only after the required future aligned sessions actually exist.

## 12. Execution-cost evidence

Production v1 retains its frozen assumed rotation-cost parameter. A separate weekday intraday capture workflow accumulates live bid/ask observations prospectively.

`execution_cost_model.json` is a challenger calibration. It remains `INSUFFICIENT_HISTORY` until enough live-book observations exist and may not silently modify the frozen v1 cost assumption.

Historical bid/ask data that was never observed must not be reconstructed from daily OHLC.

## 13. Challenger models

`common-factor-residual-challenger-v1` persists ETF residual rankings and candidate pairs. It is explicitly `production_gate=false`.

Challengers are used to test robustness, structural breaks and alternative cross-sectional explanations. They may support or challenge a production signal but cannot upgrade a failed production Pair gate.

## 14. Source health and validation

Every logical source call is recorded in `source_runs`. `research_health.json` detects repeated degradation, current data warnings and model-readiness blocks while distinguishing a successful fallback from a critical failure.

Research validation checks at least:

- pair cardinality and duplicate pairs;
- aligned-observation upper bounds;
- formal signals have all gates true;
- score ranges;
- portfolio concentration/conflicts;
- model registry existence;
- history-depth and Regime-coverage outputs;
- report/challenger/health outputs.

## 15. FULL / DELTA daily output

`daily_report.md` always retains the three semantic sections:

1. data status / process;
2. product quality + tactical ranking;
3. Pair / signal ranking.

A report is `FULL` on first run, model change, signal-state change, Regime change, readiness/health change, or whenever a formal signal exists. Ordinary unchanged days can be `DELTA`.

## 16. Non-negotiable limitations

The Skill must state, not hide, these evidence limits:

- conservative NAV availability is not exact historical publication timing;
- historical PCF/events remain incomplete until official evidence is actually retrieved;
- empirical execution cost and OOS performance only accumulate prospectively;
- AUM/tracking metrics labelled as proxies are not official reported statistics;
- no output is a risk-free-arbitrage claim or automatic trading instruction.
