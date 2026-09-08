# Execution readiness

Model-data readiness and execution readiness are intentionally separate.

`manifest.json -> quality.formal_signal_ready_symbols` answers whether the historical/PIT inputs are sufficient to compute a formal relative-value model. It does **not** prove that an ETF currently has an executable order book.

The scheduled workflow also writes `data/execution_readiness.json` and embeds the same object in `manifest.json -> execution`.

A symbol is `execution_ready=true` only when the latest snapshot for the requested as-of date has:

- a positive last price;
- positive observed volume or amount;
- a valid positive best bid/ask with `ask1 >= bid1`.

If any of these are absent, the symbol remains eligible for research and ranking but must not be promoted to a formal `TRADE` / `STRONG TRADE` signal. It should be capped at `WATCH` until execution inputs become available.

Important states include:

- `NO_CURRENT_DAY_SNAPSHOT`
- `NO_CURRENT_PRICE`
- `NO_TRADING_ACTIVITY`
- `NO_ACTIVE_BOOK`
- `EXECUTION_READY`

Missing bid/ask values are never filled from an older trading day merely to make a signal look executable. This is especially important around temporary halts, risk-warning halts and other periods when an ETF can still have a reference price/IOPV but no active secondary-market book.

For downstream pair signals, both ETF legs must be execution-ready at the signal/execution decision point. Model readiness can still be used to compute observation priority when one or both legs are execution-blocked.
