# Integration with the Nasdaq-100 ETF relative-value skill

This repository supplies the historical data layer for the downstream ETF relative-value analysis.

The analysis should not substitute a one-day cross-sectional premium ranking for a historical pair model. At minimum:

1. Use at least 60 valid observations for short-window median/MAD and Robust Z.
2. Use at least 120 valid observations for AR(1), mean-reversion center and half-life estimates.
3. Preserve point-in-time NAV availability; never use future NAV to backfill an earlier signal.
4. Keep data-source failures explicit rather than lowering model thresholds to manufacture a trade signal.
5. Treat product-value ranking and pair-trade ranking as separate outputs.

The existing signal policy can consume these dataset files to calculate spread history, historical trigger events, expected 5-day convergence, costs, adjusted win rate and regime diagnostics.
