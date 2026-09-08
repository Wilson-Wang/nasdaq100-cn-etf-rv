from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin
from xml.etree import ElementTree as ET

import pandas as pd
import requests

from .config import ETF


SSE_DOWNLOAD_URL = "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do"
SSE_REFERER = "https://etf.sse.com.cn/fundlist/funddetail/"
SZSE_REPORT_URL = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_REFERER = "https://www.szse.cn/disclosure/fund/currency/index.html"
SZSE_DOCUMENT_ROOT = "https://reportdocs.static.szse.cn"

PCF_COLUMNS = [
    "symbol",
    "date",
    "previous_trading_day",
    "creation_allowed",
    "redemption_allowed",
    "creation_unit",
    "nav",
    "nav_per_creation_unit",
    "cash_component",
    "estimated_cash_component",
    "cash_substitution_limit_pct",
    "creation_limit",
    "redemption_limit",
    "net_creation_limit",
    "net_redemption_limit",
    "net_creation_limit_per_account",
    "net_redemption_limit_per_account",
    "publish_iopv",
    "component_count",
    "max_creation_cash_premium_pct",
    "max_redemption_cash_discount_pct",
    "available_at",
    "availability_method",
    "availability_verified",
    "pit_verified",
    "source",
    "source_url",
    "document_url",
    "source_priority",
    "ingested_at_utc",
]


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=PCF_COLUMNS)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(root: ET.Element, name: str) -> str | None:
    for element in root.iter():
        if _local_name(element.tag).lower() == name.lower():
            value = (element.text or "").strip()
            return value or None
    return None


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text or text in {"-", "--"} or "不设上限" in text:
        return None
    cleaned = text.replace(",", "").replace("￥", "").replace("%", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _percent_points(value: str | None) -> float | None:
    parsed = _number(value)
    if parsed is None:
        return None
    if value and "%" in value:
        return parsed
    if abs(parsed) <= 1:
        return parsed * 100
    return parsed


def _date_key(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) != 8:
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def _bool_flag(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if normalized in {"Y", "YES", "1", "TRUE", "是", "允许"}:
        return True
    if normalized in {"N", "NO", "0", "FALSE", "否", "不允许"}:
        return False
    return None


def _availability(trading_day: str, exchange: str) -> tuple[str, str, bool, bool]:
    day = pd.Timestamp(trading_day).normalize()
    if exchange == "sh":
        timestamp = day.tz_localize("Asia/Shanghai").replace(hour=8, minute=30)
        return timestamp.isoformat(), "sse_official_08:30_cn", True, True

    timestamp = day.tz_localize("Asia/Shanghai").replace(hour=9, minute=15)
    return (
        timestamp.isoformat(),
        "szse_before_open_rule_conservative_09:15_cn",
        False,
        True,
    )


def _component_rates(root: ET.Element, exchange: str) -> tuple[int, float | None, float | None]:
    creation_rates: list[float] = []
    redemption_rates: list[float] = []
    count = 0
    for element in root.iter():
        if _local_name(element.tag).lower() != "component":
            continue
        count += 1
        creation_tag = "CreationPremiumRate" if exchange == "sh" else "PremiumRatio"
        redemption_tag = "RedemptionDiscountRate" if exchange == "sh" else "DiscountRatio"
        creation = _percent_points(_text(element, creation_tag))
        redemption = _percent_points(_text(element, redemption_tag))
        if creation is not None:
            creation_rates.append(creation)
        if redemption is not None:
            redemption_rates.append(redemption)
    return (
        count,
        max(creation_rates) if creation_rates else None,
        max(redemption_rates) if redemption_rates else None,
    )


def parse_pcf_xml(
    xml_text: str,
    *,
    exchange: str,
    expected_symbol: str,
    source_url: str,
    document_url: str,
) -> pd.DataFrame:
    """Parse one official SSE/SZSE PCF XML document into a summary row."""
    root = ET.fromstring(xml_text)
    is_sse = exchange == "sh"
    fund_code = _text(root, "FundInstrumentID" if is_sse else "SecurityID")
    trading_day = _date_key(_text(root, "TradingDay"))
    if fund_code != expected_symbol:
        raise ValueError(f"PCF symbol mismatch: expected {expected_symbol}, got {fund_code}")
    if not trading_day:
        raise ValueError("PCF missing TradingDay")

    if is_sse:
        switch = _text(root, "CreationRedemptionSwitch")
        creation_allowed = switch in {"1", "2"} if switch is not None else None
        redemption_allowed = switch in {"1", "3"} if switch is not None else None
    else:
        creation_allowed = _bool_flag(_text(root, "Creation"))
        redemption_allowed = _bool_flag(_text(root, "Redemption"))

    component_count, max_creation_premium, max_redemption_discount = _component_rates(
        root, exchange
    )
    available_at, availability_method, availability_verified, pit_verified = _availability(
        trading_day, exchange
    )

    row = {
        "symbol": expected_symbol,
        "date": trading_day,
        "previous_trading_day": _date_key(_text(root, "PreTradingDay")),
        "creation_allowed": creation_allowed,
        "redemption_allowed": redemption_allowed,
        "creation_unit": _number(_text(root, "CreationRedemptionUnit")),
        "nav": _number(_text(root, "NAV")),
        "nav_per_creation_unit": _number(_text(root, "NAVperCU")),
        "cash_component": _number(_text(root, "PreCashComponent" if is_sse else "CashComponent")),
        "estimated_cash_component": _number(
            _text(root, "EstimatedCashComponent" if is_sse else "EstimateCashComponent")
        ),
        "cash_substitution_limit_pct": _percent_points(_text(root, "MaxCashRatio")),
        "creation_limit": _number(_text(root, "CreationLimit")),
        "redemption_limit": _number(_text(root, "RedemptionLimit")),
        "net_creation_limit": _number(_text(root, "NetCreationLimit")),
        "net_redemption_limit": _number(_text(root, "NetRedemptionLimit")),
        "net_creation_limit_per_account": _number(_text(root, "NetCreationLimitPerUser")),
        "net_redemption_limit_per_account": _number(_text(root, "NetRedemptionLimitPerUser")),
        "publish_iopv": _bool_flag(
            _text(root, "PublishIOPVFlag" if is_sse else "Publish")
        ),
        "component_count": component_count,
        "max_creation_cash_premium_pct": max_creation_premium,
        "max_redemption_cash_discount_pct": max_redemption_discount,
        "available_at": available_at,
        "availability_method": availability_method,
        "availability_verified": availability_verified,
        "pit_verified": pit_verified,
        "source": "sse:official_pcf" if is_sse else "szse:official_pcf",
        "source_url": source_url,
        "document_url": document_url,
        "source_priority": 5,
        "ingested_at_utc": _now_utc(),
    }
    return pd.DataFrame([row]).reindex(columns=PCF_COLUMNS)


def _request_text(url: str, *, referer: str, params: dict[str, str] | None = None) -> str:
    response = requests.get(
        url,
        params=params,
        headers={
            "Accept": "application/xml,text/xml,text/plain,application/json;q=0.9,*/*;q=0.8",
            "Referer": referer,
            "User-Agent": "Mozilla/5.0 (compatible; nasdaq100-cn-etf-rv/0.1)",
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.text


def fetch_sse_pcf(etf: ETF, as_of_date: str) -> pd.DataFrame:
    source_url = f"{SSE_REFERER}?fundCode={etf.symbol}"
    document_url = f"{SSE_DOWNLOAD_URL}?fundCode={etf.symbol}"
    xml_text = _request_text(document_url, referer=source_url)
    frame = parse_pcf_xml(
        xml_text,
        exchange="sh",
        expected_symbol=etf.symbol,
        source_url=source_url,
        document_url=document_url,
    )
    if pd.Timestamp(frame.iloc[0]["date"]) > pd.Timestamp(as_of_date):
        raise ValueError("SSE PCF trading day is after requested as-of date")
    return frame


def _find_jjdm(payload: object) -> str | None:
    if isinstance(payload, dict):
        value = payload.get("jjdm")
        if isinstance(value, str) and value:
            return value
        for nested in payload.values():
            found = _find_jjdm(nested)
            if found:
                return found
    elif isinstance(payload, list):
        for nested in payload:
            found = _find_jjdm(nested)
            if found:
                return found
    return None


def _extract_szse_document_url(payload: object, symbol: str) -> str | None:
    html = _find_jjdm(payload)
    if not html:
        return None
    match = re.search(r"path=([^&'\"]+).*?filename=([^&'\"]+)", html, flags=re.IGNORECASE)
    if not match:
        return None
    path = unquote(match.group(1))
    filename = unquote(match.group(2)).split(";", 1)[0]
    if symbol not in filename:
        return None
    if not filename.lower().endswith(".xml"):
        filename = f"{filename}.xml"
    return urljoin(SZSE_DOCUMENT_ROOT, f"{path}{filename}")


def fetch_szse_pcf(etf: ETF, as_of_date: str) -> pd.DataFrame:
    source_url = f"{SZSE_REFERER}?txtJCorDH={etf.symbol}"
    as_of = pd.Timestamp(as_of_date).normalize()
    for offset in range(7):
        day = (as_of - timedelta(days=offset)).strftime("%Y-%m-%d")
        params = {
            "SHOWTYPE": "JSON",
            "CATALOGID": "sgshqd",
            "TABKEY": "tab1",
            "txtJCorDH": etf.symbol,
            "txtStart": day,
            "txtEnd": day,
            "PAGENO": "1",
        }
        response = requests.get(
            SZSE_REPORT_URL,
            params=params,
            headers={
                "Accept": "application/json,text/plain,*/*",
                "Referer": source_url,
                "User-Agent": "Mozilla/5.0 (compatible; nasdaq100-cn-etf-rv/0.1)",
            },
            timeout=15,
        )
        response.raise_for_status()
        document_url = _extract_szse_document_url(response.json(), etf.symbol)
        if not document_url:
            continue
        xml_text = _request_text(document_url, referer=source_url)
        return parse_pcf_xml(
            xml_text,
            exchange="sz",
            expected_symbol=etf.symbol,
            source_url=source_url,
            document_url=document_url,
        )
    return _empty()


def fetch_official_pcf(etf: ETF, as_of_date: str) -> pd.DataFrame:
    if etf.exchange == "sh":
        return fetch_sse_pcf(etf, as_of_date)
    if etf.exchange == "sz":
        return fetch_szse_pcf(etf, as_of_date)
    raise ValueError(f"unsupported ETF exchange: {etf.exchange}")


def pcf_summary(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return {"rows": 0, "symbols": 0, "by_symbol": {}}
    frame = pd.read_csv(path, dtype={"symbol": "string"})
    by_symbol: dict[str, dict] = {}
    for symbol, group in frame.groupby("symbol", sort=True):
        dates = pd.to_datetime(group["date"], errors="coerce").dropna()
        latest = group.loc[pd.to_datetime(group["date"], errors="coerce").idxmax()]
        by_symbol[str(symbol)] = {
            "rows": int(len(group)),
            "min_date": dates.min().strftime("%Y-%m-%d") if not dates.empty else None,
            "max_date": dates.max().strftime("%Y-%m-%d") if not dates.empty else None,
            "creation_allowed": latest.get("creation_allowed"),
            "redemption_allowed": latest.get("redemption_allowed"),
            "pit_verified": latest.get("pit_verified"),
            "source": latest.get("source"),
        }
    return {
        "rows": int(len(frame)),
        "symbols": int(frame["symbol"].nunique()),
        "by_symbol": by_symbol,
    }


def validate_pcf(path: str | Path) -> list[str]:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return ["official PCF file missing or empty"]
    frame = pd.read_csv(path, dtype={"symbol": "string"})
    required = {
        "symbol",
        "date",
        "creation_allowed",
        "redemption_allowed",
        "available_at",
        "pit_verified",
        "source",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"PCF missing required columns: {sorted(missing)}")
    if frame.duplicated(["symbol", "date"], keep=False).any():
        raise ValueError("PCF contains duplicate symbol/date rows")
    available = pd.to_datetime(frame["available_at"], errors="coerce", utc=True)
    if available.isna().any():
        raise ValueError("PCF contains invalid available_at values")
    return []
