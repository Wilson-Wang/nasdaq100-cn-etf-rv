from __future__ import annotations

import math
import re
from datetime import datetime, timezone

import pandas as pd

from .sources import run_with_timeout


METADATA_COLUMNS = [
    "symbol",
    "effective_date",
    "name",
    "fund_type",
    "inception_date",
    "aum_cny",
    "shares",
    "fund_manager_company",
    "custodian",
    "management_fee_pct",
    "custodian_fee_pct",
    "sales_service_fee_pct",
    "benchmark",
    "tracking_index",
    "source",
    "available_at",
    "ingested_at_utc",
]


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _pct(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().replace("%", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def _cny_amount(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).replace(",", "").strip()
    match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*(万|亿)?元", text)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2)
    if unit == "亿":
        amount *= 1e8
    elif unit == "万":
        amount *= 1e4
    return amount


def _shares(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).replace(",", "").strip()
    match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*(万|亿)?份", text)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2)
    if unit == "亿":
        amount *= 1e8
    elif unit == "万":
        amount *= 1e4
    return amount


def _inception(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    match = re.search(r"(20\d{2})年(\d{2})月(\d{2})日", str(value))
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _first(row: pd.Series, *keys: str) -> object:
    for key in keys:
        if key in row and pd.notna(row[key]):
            return row[key]
    return None


def fetch_fund_overview(symbol: str, as_of_date: str) -> pd.DataFrame:
    """Fetch current fund-file metadata from Eastmoney through AKShare."""
    import akshare as ak

    raw = run_with_timeout(ak.fund_overview_em, symbol, seconds=30)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=METADATA_COLUMNS)
    row = raw.iloc[0]
    now = _now_utc()
    inception_raw = _first(row, "成立日期/规模", "成立日期")
    aum_raw = _first(row, "资产规模")
    shares_raw = _first(row, "份额规模")
    record = {
        "symbol": symbol,
        "effective_date": as_of_date,
        "name": _first(row, "基金全称", "基金简称"),
        "fund_type": _first(row, "基金类型"),
        "inception_date": _inception(inception_raw),
        "aum_cny": _cny_amount(aum_raw),
        "shares": _shares(shares_raw),
        "fund_manager_company": _first(row, "基金管理人"),
        "custodian": _first(row, "基金托管人"),
        "management_fee_pct": _pct(_first(row, "管理费率")),
        "custodian_fee_pct": _pct(_first(row, "托管费率")),
        "sales_service_fee_pct": _pct(_first(row, "销售服务费率")),
        "benchmark": _first(row, "业绩比较基准"),
        "tracking_index": _first(row, "跟踪标的"),
        "source": "akshare:eastmoney:fund_overview_em",
        "available_at": now,
        "ingested_at_utc": now,
    }
    return pd.DataFrame([record], columns=METADATA_COLUMNS)


def tracking_error_proxy(nav: pd.DataFrame, factors: pd.DataFrame, window: int = 120) -> dict[str, float | None]:
    """Annualized NAV-vs-NDX×FX return-difference volatility, explicitly a proxy."""
    if nav.empty or factors.empty:
        return {}
    f = factors.copy()
    f["factor_date"] = pd.to_datetime(f["factor_date"], errors="coerce")
    f["value"] = pd.to_numeric(f["value"], errors="coerce")
    preferred_fx = "USDCNH" if (f["factor_name"] == "USDCNH").any() else "USDCNY"
    pivot = f.loc[f["factor_name"].isin(["NDX", preferred_fx])].pivot_table(
        index="factor_date", columns="factor_name", values="value", aggfunc="last"
    )
    if "NDX" not in pivot.columns or preferred_fx not in pivot.columns:
        return {}
    pivot = pivot.dropna().sort_index()
    pivot["benchmark"] = pivot["NDX"] * pivot[preferred_fx]
    benchmark = pivot[["benchmark"]].reset_index().sort_values("factor_date")

    result: dict[str, float | None] = {}
    for symbol, group in nav.groupby(nav["symbol"].astype("string")):
        g = group.copy()
        g["nav_date"] = pd.to_datetime(g["nav_date"], errors="coerce")
        g["unit_nav"] = pd.to_numeric(g["unit_nav"], errors="coerce")
        g = g.dropna(subset=["nav_date", "unit_nav"]).sort_values("nav_date")
        merged = pd.merge_asof(
            g[["nav_date", "unit_nav"]],
            benchmark,
            left_on="nav_date",
            right_on="factor_date",
            direction="backward",
        ).dropna()
        if len(merged) < 30:
            result[str(symbol)] = None
            continue
        sample = merged.tail(window + 1).copy()
        nav_ret = sample["unit_nav"].pct_change()
        bench_ret = sample["benchmark"].pct_change()
        diff = (nav_ret - bench_ret).dropna()
        result[str(symbol)] = float(diff.std(ddof=1) * math.sqrt(252)) if len(diff) >= 20 else None
    return result


def _percentile(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    rank = numeric.rank(pct=True, method="average") * 100
    return rank if higher_is_better else 100 - rank + (100 / max(int(numeric.notna().sum()), 1))


def build_product_quality_scores(
    metadata: pd.DataFrame,
    prices: pd.DataFrame,
    tracking_error: dict[str, float | None],
    as_of_date: str,
) -> pd.DataFrame:
    """Compute reproducible PQS with missing-component weight re-normalization."""
    if metadata.empty:
        return pd.DataFrame()
    latest = metadata.copy()
    latest["effective_date"] = pd.to_datetime(latest["effective_date"], errors="coerce")
    latest = latest.sort_values("effective_date").drop_duplicates("symbol", keep="last")
    latest["symbol"] = latest["symbol"].astype("string")
    latest["total_fee_pct"] = (
        pd.to_numeric(latest["management_fee_pct"], errors="coerce")
        + pd.to_numeric(latest["custodian_fee_pct"], errors="coerce")
        + pd.to_numeric(latest["sales_service_fee_pct"], errors="coerce").fillna(0)
    )

    px = prices.copy()
    px["date"] = pd.to_datetime(px["date"], errors="coerce")
    px["amount"] = pd.to_numeric(px.get("amount"), errors="coerce")
    med_amount = (
        px.sort_values("date")
        .groupby(px["symbol"].astype("string"), group_keys=False)
        .tail(20)
        .groupby("symbol")["amount"]
        .median()
    )
    latest["median_amount_20d"] = latest["symbol"].map(med_amount)
    latest["tracking_error_proxy"] = latest["symbol"].map(tracking_error)
    inception = pd.to_datetime(latest["inception_date"], errors="coerce")
    latest["age_days"] = (pd.Timestamp(as_of_date) - inception).dt.days

    latest["fee_score"] = _percentile(latest["total_fee_pct"], higher_is_better=False)
    latest["liquidity_score"] = _percentile(latest["median_amount_20d"], higher_is_better=True)
    latest["aum_score"] = _percentile(latest["aum_cny"], higher_is_better=True)
    latest["tracking_score"] = _percentile(latest["tracking_error_proxy"], higher_is_better=False)
    latest["stability_score"] = _percentile(latest["age_days"], higher_is_better=True)

    components = [
        ("fee_score", 0.25),
        ("liquidity_score", 0.25),
        ("aum_score", 0.20),
        ("tracking_score", 0.20),
        ("stability_score", 0.10),
    ]
    pqs: list[float | None] = []
    coverage: list[float] = []
    for _, row in latest.iterrows():
        numerator = 0.0
        denominator = 0.0
        for column, weight in components:
            value = row[column]
            if pd.notna(value):
                numerator += float(value) * weight
                denominator += weight
        coverage.append(denominator)
        pqs.append(numerator / denominator if denominator else None)
    latest["pqs_coverage"] = coverage
    latest["pqs"] = pqs
    latest["pqs_status"] = latest["pqs_coverage"].map(
        lambda value: "COMPLETE" if value >= 0.70 else "INCOMPLETE"
    )
    return latest.sort_values("pqs", ascending=False, na_position="last").reset_index(drop=True)
