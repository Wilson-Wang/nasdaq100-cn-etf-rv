from __future__ import annotations

import pandas as pd


def _rank_score(values: pd.Series, *, higher_is_better: bool) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    n = int(numeric.notna().sum())
    if n == 0:
        return pd.Series(index=values.index, dtype=float)
    ranks = numeric.rank(pct=True, method="average") * 100
    if higher_is_better:
        return ranks
    return 100 - ranks + (100 / n)


def build_liquidity_scores(
    prices: pd.DataFrame,
    snapshot: pd.DataFrame,
    symbols: list[str],
) -> pd.DataFrame:
    """Build ETF liquidity scores with missing-component weight re-normalization.

    Amount uses 20-session median turnover value, turnover uses the latest
    snapshot turnover percentage, and spread uses the latest valid bid/ask
    spread. Missing spread/turnover never receives a synthetic perfect score.
    """
    base = pd.DataFrame({"symbol": pd.Series(symbols, dtype="string")})

    px = prices.copy()
    if not px.empty:
        px["symbol"] = px["symbol"].astype("string")
        px["date"] = pd.to_datetime(px["date"], errors="coerce")
        px["amount"] = pd.to_numeric(px.get("amount"), errors="coerce")
        px = px.dropna(subset=["date"]).sort_values(["symbol", "date"])
        latest20 = px.groupby("symbol", group_keys=False).tail(20)
        med_amount = latest20.groupby("symbol")["amount"].median()
        base["median_amount_20d"] = base["symbol"].map(med_amount)
    else:
        base["median_amount_20d"] = pd.NA

    snap = snapshot.copy()
    if not snap.empty:
        snap["symbol"] = snap["symbol"].astype("string")
        snap["data_date"] = pd.to_datetime(snap["data_date"], errors="coerce")
        snap = snap.sort_values(["symbol", "data_date", "updated_at"]).drop_duplicates(
            "symbol", keep="last"
        )
        turnover = pd.to_numeric(snap.get("turnover_pct"), errors="coerce")
        spread = pd.to_numeric(snap.get("bid_ask_spread_pct"), errors="coerce")
        turnover_map = pd.Series(turnover.to_numpy(), index=snap["symbol"])
        spread_map = pd.Series(spread.to_numpy(), index=snap["symbol"])
        base["turnover_pct"] = base["symbol"].map(turnover_map)
        base["bid_ask_spread_pct"] = base["symbol"].map(spread_map)
    else:
        base["turnover_pct"] = pd.NA
        base["bid_ask_spread_pct"] = pd.NA

    base["amount_score"] = _rank_score(base["median_amount_20d"], higher_is_better=True)
    base["turnover_score"] = _rank_score(base["turnover_pct"], higher_is_better=True)
    base["spread_score"] = _rank_score(base["bid_ask_spread_pct"], higher_is_better=False)

    components = [("amount_score", 0.60), ("turnover_score", 0.25), ("spread_score", 0.15)]
    scores: list[float | None] = []
    coverage: list[float] = []
    for _, row in base.iterrows():
        numerator = 0.0
        denominator = 0.0
        for column, weight in components:
            value = row[column]
            if pd.notna(value):
                numerator += float(value) * weight
                denominator += weight
        scores.append(numerator / denominator if denominator else None)
        coverage.append(denominator)
    base["liquidity_score"] = scores
    base["liquidity_coverage"] = coverage
    return base


def liquidity_score_map(frame: pd.DataFrame) -> dict[str, float | None]:
    if frame.empty:
        return {}
    return {
        str(row["symbol"]): (float(row["liquidity_score"]) if pd.notna(row["liquidity_score"]) else None)
        for _, row in frame.iterrows()
    }
