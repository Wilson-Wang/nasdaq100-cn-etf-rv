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
        ["open", "high", "low", "close", "preclose", "volume", "amount", "turnover", "pct_change"],
    )
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
    df["source"] = "akshare:eastmoney:fund_etf_hist_em"
    df["source_priority"] = 20
    df["ingested_at_utc"] = _now_utc()
    return df.reindex(columns=PRICE_COLUMNS)


def fetch_prices_with_fallback(etf: ETF, start_date: str, end_date: str) -> tuple[pd.DataFrame, list[str]]:
    errors: list[str] = []
    sources: list[tuple[str, Callable[[ETF, str, str], pd.DataFrame]]] = [
        ("baostock", fetch_prices_baostock),
        ("akshare:eastmoney", fetch_prices_akshare_em),
    ]
    for source_name, fetcher in sources:
        try:
            frame = run_with_timeout(fetcher, etf, start_date, end_date, seconds=30)
            if not frame.empty:
                return frame, errors
            errors.append(f"{etf.symbol} prices {source_name}: empty result")
        except Exception as exc:  # endpoint failures are recorded, not hidden
            errors.append(f"{etf.symbol} prices {source_name}: {type(exc).__name__}: {exc}")
    return _empty(PRICE_COLUMNS), errors


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
