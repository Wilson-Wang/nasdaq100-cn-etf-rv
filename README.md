# Nasdaq-100 China ETF Relative-Value Research

A reproducible, database-free data and research pipeline for China-listed ETFs tracking the Nasdaq-100 index.

The repository supports same-index relative-value research without predicting Nasdaq-100 direction and without describing the strategy as risk-free arbitrage. It stores normalized source data and research outputs in versioned CSV/Parquet/JSON files rather than requiring a database server.

## Design goals

- **Database-free**: canonical CSV plus Parquet mirrors; DuckDB can query locally.
- **Multi-source and coverage-aware**: preferred sources are supplemented when a returned range is incomplete.
- **Point-in-time aware**: NAV, PCF and model inputs carry availability semantics; conservative timing is never mislabeled as exact verification.
- **Gate-first**: data quality, Regime, execution and multiple-testing gates precede ranking.
- **Reproducible**: model versions/hashes, source calls, OOS states and report state are persisted.
- **Failure-tolerant**: endpoint failures remain visible and never create fabricated values.
- **Prospective evidence**: execution-cost and OOS evidence are accumulated forward rather than backfilled from unavailable information.

## ETF universe

`config/etfs.json` currently contains 12 China-listed Nasdaq-100 ETFs:

`159941`, `159501`, `159513`, `159632`, `159659`, `159660`, `159696`, `513100`, `513110`, `513300`, `513390`, `513870`.

Different-index products such as `159509` do not enter the same-index Pair model.

## Main outputs

### Base data

- `data/etf_prices.csv/.parquet`
- `data/etf_nav.csv/.parquet`
- `data/etf_pcf.csv/.parquet`
- `data/etf_snapshot.csv/.parquet`
- `data/factor_inputs.csv/.parquet`
- `data/source_runs.csv/.parquet`
- `data/manifest.json`

### Research

- `data/pair_analysis.csv/.parquet`
- `data/pair_events.csv`
- `data/liquidity_scores.csv`
- `data/etf_metadata.csv`
- `data/product_quality.csv`
- `data/product_value_ranking.csv`
- `data/etf_events.csv`
- `data/execution_readiness.json`
- `data/history_depth.json`
- `data/regime_history_coverage.json`
- `data/research_health.json`
- `data/portfolio_plan.csv`
- `data/portfolio_status.json`
- `data/model_registry.json`
- `data/oos_pair_states.csv`
- `data/execution_cost_model.json`
- `data/factor_residual_ranking.csv`
- `data/factor_residual_candidates.csv`
- `data/factor_residual_status.json`
- `data/daily_report.md`
- `data/report_state.json`

Prospective intraday observations are accumulated in `data/execution_history.csv/.parquet` when live books are available.

## Pair Engine v1.0.0

The frozen baseline implements:

- common PIT-safe NAV alignment;
- 60-observation Robust Z using history through `t-1`;
- 120-observation AR(1), Half-Life and stationarity diagnostics through `t-1`;
- crossing/reset event sampling;
- walk-forward realized relative-return backtesting;
- t+1 open entry and fifth future aligned-session close;
- assumed rotation cost;
- direction-aware primary-market/Regime gate;
- liquidity-aware PairScore;
- Benjamini-Hochberg ADF FDR as an additional formal gate.

`TRADE` / `STRONG TRADE` can be emitted only after hard gates pass. PairScore ranks eligible candidates; it cannot rescue a failed gate.

## Product and portfolio layers

Product Quality Score (PQS) is separated from Tactical Value Score (TVS). A reported AUM is preferred; when the metadata source omits it, the scoring layer can use `shares × latest NAV` as an explicitly labelled proxy without altering raw metadata.

The portfolio research layer consumes only formal Pair signals, selects at most three non-overlapping pairs, caps any selected pair at 40%, and leaves unused risk budget unallocated when too few independent opportunities exist. It is not an order-generation system.

## OOS and challenger models

`pair-engine-v1.0.0` has a frozen model hash. Its OOS period begins 2026-09-09 and the complete daily Pair cross-section is saved so unsuccessful opportunities cannot be silently discarded.

The common-factor residual model and empirical execution-cost calibration are challengers. They may annotate or challenge the baseline but do not silently change or upgrade the frozen production research signal.

## Historical evidence limits

The pipeline makes evidence gaps explicit:

- historical NAV can be `pit_usable=true` by a conservative disclosure bound while remaining `pit_verified=false` when exact publication timestamps are unavailable;
- historical PCF is never synthesized; `regime_history_coverage.json` reports actual PIT-verified coverage;
- historical bid/ask observations that were not collected cannot be reconstructed from daily OHLC;
- future OOS results cannot be backfilled.

## Quick start

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python scripts/update_dataset.py --lookback-days 450
python scripts/reconcile_exchange_calendar.py
python scripts/check_history_depth.py
python scripts/check_regime_history_coverage.py
python scripts/update_execution_readiness.py
python scripts/update_metadata.py
python scripts/update_events.py
python scripts/analyze_pairs.py
python scripts/analyze_factor_residuals.py
python scripts/build_portfolio_plan.py
python scripts/update_oos.py
python scripts/calibrate_execution_cost.py
python scripts/check_research_health.py
python scripts/generate_daily_report.py
python scripts/validate_dataset.py
python scripts/validate_research_outputs.py
```

For a one-time deeper history request, use the manual `Backfill ETF research history` workflow or:

```bash
python scripts/backfill_research_history.py --lookback-days 1100
```

The workflow version automatically rebuilds the manifest and all research outputs after the backfill.

## Automation

- `.github/workflows/update-dataset.yml`: full weekday EOD research refresh at 18:30 Asia/Shanghai and manual runs.
- `.github/workflows/capture-execution.yml`: weekday intraday live-book observation at 14:50 Asia/Shanghai plus manual runs.
- `.github/workflows/backfill-research-history.yml`: manual deep-history refresh and full downstream rebuild.

All dataset commits use fetch/rebase before push to reduce races with concurrent repository changes.

## Research boundary

The repository produces research signals and diagnostics only. It does not place trades, does not promise convergence, and does not characterize relative-value opportunities as risk-free arbitrage.

## License

MIT. Data retrieved from public third-party and exchange endpoints remains subject to the terms and rights of the underlying providers.
