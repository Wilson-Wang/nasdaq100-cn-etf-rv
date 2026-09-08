from __future__ import annotations

import signal
import socket
from datetime import datetime, timezone
from typing import Callable, TypeVar

import pandas as pd

from .config import ETF


T = TypeVar("T")


class SourceTimeoutError(TimeoutError):
    """Raised when an external market-data call exceeds its time budget."""


def run_with_timeout(func: Callable[..., T], *args, seconds: int = 30) -> T:
    """Run a blocking source call with a hard timeout on Unix runners."""
    if not hasattr(signal, "SIGALRM"):
        return func(*args)

    previous_handler = signal.getsignal(signal.SIGALRM)

    def _handle_timeout(_signum, _frame):
        raise SourceTimeoutError(f"source call exceeded {seconds}s")

    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return func(*args)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


PRICE_COLUMNS = [
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "preclose",
    "volume",
    "amount",
    "turnover",
    "pct_change",
    "trade_status",
    "is_tradable",
    "source",
    "source_priority",
    "ingested_at_utc",
]

NAV_COLUMNS = [
    "symbol",
    "nav_date",
    "unit_nav",
    "accumulated_nav",
    "daily_growth_pct",
    "subscription_status",
    "redemption_status",
    "source",
    "source_priority",
    "ingested_at_utc",
]

SNAPSHOT_COLUMNS = [
    "symbol",
    "name",
    "last",
    "iopv",
    "discount_rate_pct",
    "volume",
    "amount",
    "turnover_pct",
    "bid1",
    "ask1",
    "data_date",
    "updated_at",
    "source",
    "source_priority",
    "ingested_at_utc",
]


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _to_numeric(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def _add_tradability(df: pd.DataFrame) -> pd.DataFrame:
    """Mark rows that are usable for executable pair-return research."""
    close = pd.to_numeric(df.get("close"), errors="coerce")
    volume = pd.to_numeric(df.get("volume"), errors="coerce")
    tradable = close.gt(0) & volume.gt(0)

    if "trade_status" in df.columns:
        status = pd.to_numeric(df["trade_status"], errors="coerce")
        tradable &= status.isna() | status.gt(0)

    df["is_tradable"] = tradable.astype("boolean")
    return df


def _price_coverage(frame: pd.DataFrame, start_date: str, end_date: str) -> dict[str, object]:
    """Summarize whether a source appears to cover the requested date window."""
    if frame.empty or "date" not in frame.columns:
        return {
            "rows": 0,
            "min_date": None,
            "max_date": None,
            "business_day_ratio": 0.0,
            "needs_supplement": True,
        }

    dates = pd.to_datetime(frame["date"], errors="coerce").dropna().drop_duplicates()
    if dates.empty:
        return {
            "rows": 0,
            "min_date": None,
            "max_date": None,
            "business_day_ratio": 0.0,
            "needs_supplement": True,
        }

    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    expected_business_days = max(len(pd.bdate_range(start, end)), 1)
    observed = len(dates)
    min_date = dates.min()
    max_date = dates.max()

    business_day_ratio = observed / expected_business_days
    start_gap_days = max((min_date - start).days, 0)
    end_gap_days = max((end - max_date).days, 0)

    needs_supplement = (
        business_day_ratio < 0.80
        or start_gap_days > 21
        or end_gap_days > 7
    )
    return {
        "rows": observed,
        "min_date": min_date.strftime("%Y-%m-%d"),
        "max_date": max_date.strftime("%Y-%m-%d"),
        "business_day_ratio": round(float(business_day_ratio), 4),
        "needs_supplement": bool(needs_supplement),
    }


def _merge_price_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Merge source frames by date while preserving source priority."""
    usable = [frame for frame in frames if frame is not None and not frame.empty]
    if not usable:
        return _empty(PRICE_COLUMNS)

    combined = pd.concat(usable, ignore_index=True, sort=False)
    combined["source_priority"] = pd.to_numeric(
        combined["source_priority"], errors="coerce"
    ).fillna(9999)
    combined["_ingested_sort"] = pd.to_datetime(
        combined["ingested_at_utc"], errors="coerce", utc=True
    )
    combined = combined.sort_values(
        ["symbol", "date", "source_priority", "_ingested_sort"],
        ascending=[True, True, True, False],
        kind="stable",
    )
    combined = combined.drop_duplicates(["symbol", "date"], keep="first")
    combined = combined.drop(columns=["_ingested_sort"]).reset_index(drop=True)
    return combined.reindex(columns=PRICE_COLUMNS)


def fetch_prices_baostock(etf: ETF, start_date: str, end_date: str) -> pd.DataFrame:
    import baostock as bs

    previous_socket_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(10)
    logged_in = False
    try:
        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"baostock login failed: {login.error_code} {login.error_msg}")
        logged_in = True

        fields = (
            "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,"
            "tradestatus,pctChg"
        )
        rs = bs.query_history_k_data_plus(
            etf.baostock_code,
            fields,
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="3",
        )
        if rs.error_code != "0":
            raise RuntimeError(f"baostock query failed: {rs.error_code} {rs.error_msg}")
        rows: list[list[str]] = []
        while rs.next():
            rows.append(rs.get_row_data())
    finally:
        if logged_in:
            try:
                bs.logout()
            except Exception:
                pass
        socket.setdefaulttimeout(previous_socket_timeout)

    if not rows:
        return _empty(PRICE_COLUMNS)

    df = pd.DataFrame(rows, columns=rs.fields).rename(
        columns={
            "turn": "turnover",
            "tradestatus": "trade_status",
            "pctChg": "pct_change",
        }
    )
    df["symbol"] = etf.symbol
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = _to_numeric(
        df,
        [
            "open",
            "high",
            "low",
            "close",
            "preclose",
            "volume",
            "amount",
            "turnover",
            "pct_change",
            "trade_status",
        ],
    )
    df = _add_tradability(df)
    df["source"] = "baostock"
    df["source_priority"] = 10
    df["ingested_at_utc"] = _now_utc()
    return df.reindex(columns=PRICE_COLUMNS)


def fetch_prices_akshare_em(etf: ETF, start_date: str, end_date: str) -> pd.DataFrame:
    import akshare as ak

    df = ak.fund_etf_hist_em(
        symbol=etf.symbol,
        period="daily",
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
        adjust="",
    )
    if df is None or df.empty:
        return _empty(PRICE_COLUMNS)

    df = df.rename(
        columns={
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "换手率": "turnover",
            "涨跌幅": "pct_change",
        }
    )
    df["symbol"] = etf.symbol
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["preclose"] = pd.NA
    df["trade_status"] = pd.NA
    df = _to_numeric(
        df,
        ["open", "high", "low", "close", "volume", "amount", "turnover", "pct_change"],
    )
    df = _add_tradability(df)
    df["source"] = "akshare:eastmoney:fund_etf_hist_em"
    df["source_priority"] = 20
    df["ingested_at_utc"] = _now_utc()
    return df.reindex(columns=PRICE_COLUMNS)


def fetch_prices_akshare_sina(etf: ETF, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch full-history ETF daily bars from Sina through AKShare.

    Sina is intentionally a lower-priority supplement. It is useful when
    Baostock begins too recently and the Eastmoney endpoint is unavailable.
    """
    import akshare as ak

    df = ak.fund_etf_hist_sina(symbol=f"{etf.exchange}{etf.symbol}")
    if df is None or df.empty:
        return _empty(PRICE_COLUMNS)

    df = df.rename(
        columns={
            "date": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        }
    )
    df["symbol"] = etf.symbol
    parsed_date = pd.to_datetime(df["date"], errors="coerce")
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    df = df.loc[parsed_date.between(start, end)].copy()
    if df.empty:
        return _empty(PRICE_COLUMNS)

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["preclose"] = pd.NA
    df["amount"] = pd.NA
    df["turnover"] = pd.NA
    df["pct_change"] = pd.NA
    df["trade_status"] = pd.NA
    df = _to_numeric(df, ["open", "high", "low", "close", "volume"])
    df = _add_tradability(df)
    df["source"] = "akshare:sina:fund_etf_hist_sina"
    df["source_priority"] = 30
    df["ingested_at_utc"] = _now_utc()
    return df.reindex(columns=PRICE_COLUMNS)


def fetch_prices_with_fallback(
    etf: ETF, start_date: str, end_date: str
) -> tuple[pd.DataFrame, list[str]]:
    """Fetch prices and supplement partial coverage source by source.

    A non-empty primary result is not sufficient if it covers only part of the
    requested history. Baostock is preferred, Eastmoney is the first
    supplement, and Sina is a second independent supplement. Coverage is
    recomputed after each source so unnecessary requests are avoided.
    """
    errors: list[str] = []
    frames: list[pd.DataFrame] = []

    try:
        primary = run_with_timeout(
            fetch_prices_baostock, etf, start_date, end_date, seconds=30
        )
        if primary.empty:
            errors.append(f"{etf.symbol} prices baostock: empty result")
        else:
            frames.append(primary)
    except Exception as exc:
        primary = _empty(PRICE_COLUMNS)
        errors.append(f"{etf.symbol} prices baostock: {type(exc).__name__}: {exc}")

    merged = _merge_price_frames(frames)
    coverage = _price_coverage(merged, start_date, end_date)
    if bool(coverage["needs_supplement"]):
        try:
            fallback = run_with_timeout(
                fetch_prices_akshare_em, etf, start_date, end_date, seconds=30
            )
            if fallback.empty:
                errors.append(f"{etf.symbol} prices akshare:eastmoney: empty result")
            else:
                frames.append(fallback)
        except Exception as exc:
            errors.append(
                f"{etf.symbol} prices akshare:eastmoney: {type(exc).__name__}: {exc}"
            )

    merged = _merge_price_frames(frames)
    coverage = _price_coverage(merged, start_date, end_date)
    if bool(coverage["needs_supplement"]):
        try:
            fallback = run_with_timeout(
                fetch_prices_akshare_sina, etf, start_date, end_date, seconds=30
            )
            if fallback.empty:
                errors.append(f"{etf.symbol} prices akshare:sina: empty result")
            else:
                frames.append(fallback)
        except Exception as exc:
            errors.append(
                f"{etf.symbol} prices akshare:sina: {type(exc).__name__}: {exc}"
            )

    return _merge_price_frames(frames), errors


def fetch_nav_akshare_em(etf: ETF, start_date: str, end_date: str) -> pd.DataFrame:
    import akshare as ak

    df = ak.fund_etf_fund_info_em(
        fund=etf.symbol,
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
    )
    if df is None or df.empty:
        return _empty(NAV_COLUMNS)

    df = df.rename(
        columns={
            "净值日期": "nav_date",
            "单位净值": "unit_nav",
            "累计净值": "accumulated_nav",
            "日增长率": "daily_growth_pct",
            "申购状态": "subscription_status",
            "赎回状态": "redemption_status",
        }
    )
    df["symbol"] = etf.symbol
    df["nav_date"] = pd.to_datetime(df["nav_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = _to_numeric(df, ["unit_nav", "accumulated_nav", "daily_growth_pct"])
    df["source"] = "akshare:eastmoney:fund_etf_fund_info_em"
    df["source_priority"] = 10
    df["ingested_at_utc"] = _now_utc()
    return df.reindex(columns=NAV_COLUMNS)


def fetch_snapshot_akshare_em(symbols: set[str]) -> pd.DataFrame:
    import akshare as ak

    df = ak.fund_etf_spot_em()
    if df is None or df.empty:
        return _empty(SNAPSHOT_COLUMNS)

    df["代码"] = df["代码"].astype(str).str.zfill(6)
    df = df[df["代码"].isin(symbols)].copy()
    if df.empty:
        return _empty(SNAPSHOT_COLUMNS)

    df = df.rename(
        columns={
            "代码": "symbol",
            "名称": "name",
            "最新价": "last",
            "IOPV实时估值": "iopv",
            "基金折价率": "discount_rate_pct",
            "成交量": "volume",
            "成交额": "amount",
            "换手率": "turnover_pct",
            "买一": "bid1",
            "卖一": "ask1",
            "数据日期": "data_date",
            "更新时间": "updated_at",
        }
    )
    df = _to_numeric(
        df,
        ["last", "iopv", "discount_rate_pct", "volume", "amount", "turnover_pct", "bid1", "ask1"],
    )
    df["data_date"] = pd.to_datetime(df["data_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["updated_at"] = df["updated_at"].astype(str)
    df["source"] = "akshare:eastmoney:fund_etf_spot_em"
    df["source_priority"] = 10
    df["ingested_at_utc"] = _now_utc()
    return df.reindex(columns=SNAPSHOT_COLUMNS)
