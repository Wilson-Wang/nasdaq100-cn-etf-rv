from __future__ import annotations

from datetime import time
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

CHINA_TZ = ZoneInfo("Asia/Shanghai")
DAILY_READY = time(15, 30)
PCF_READY = time(9, 15)


def _sessions(start: str, end: str) -> pd.DatetimeIndex:
    calendar = xcals.get_calendar("XSHG")
    sessions = calendar.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    if sessions.tz is not None:
        sessions = sessions.tz_localize(None)
    return sessions.normalize()


def _latest_session_on_or_before(value: str, sessions: pd.DatetimeIndex) -> pd.Timestamp:
    target = pd.Timestamp(value).normalize()
    eligible = sessions[sessions <= target]
    if len(eligible) == 0:
        raise ValueError(f"no exchange session on or before {value}")
    return eligible[-1]


def _previous_session(value: pd.Timestamp, sessions: pd.DatetimeIndex) -> pd.Timestamp:
    eligible = sessions[sessions < value]
    if len(eligible) == 0:
        raise ValueError(f"no previous exchange session before {value}")
    return eligible[-1]


def effective_sessions(
    as_of_date: str,
    observed_at_utc: str,
) -> tuple[str, str]:
    """Return completed-EOD session and current-PCF session using the exchange calendar."""
    observed = pd.Timestamp(observed_at_utc)
    if observed.tzinfo is None:
        observed = observed.tz_localize("UTC")
    observed_cn = observed.tz_convert(CHINA_TZ)
    start = (pd.Timestamp(as_of_date) - pd.Timedelta(days=40)).strftime("%Y-%m-%d")
    end = (pd.Timestamp(as_of_date) + pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    sessions = _sessions(start, end)
    requested = pd.Timestamp(as_of_date).normalize()
    current_session = _latest_session_on_or_before(as_of_date, sessions)

    eod_session = current_session
    if (
        requested == current_session
        and observed_cn.date() == requested.date()
        and observed_cn.time().replace(tzinfo=None) < DAILY_READY
    ):
        eod_session = _previous_session(current_session, sessions)

    pcf_session = current_session
    if (
        requested == current_session
        and observed_cn.date() == requested.date()
        and observed_cn.time().replace(tzinfo=None) < PCF_READY
    ):
        pcf_session = _previous_session(current_session, sessions)

    return eod_session.strftime("%Y-%m-%d"), pcf_session.strftime("%Y-%m-%d")


def session_lag(last_date: str | None, expected_date: str) -> int | None:
    if not last_date:
        return None
    last = pd.Timestamp(last_date).normalize()
    expected = pd.Timestamp(expected_date).normalize()
    if last >= expected:
        return 0
    start = (last - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    end = (expected + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    sessions = _sessions(start, end)
    return int(((sessions > last) & (sessions <= expected)).sum())


def reconcile_quality_with_exchange_calendar(manifest: dict[str, Any]) -> dict[str, Any]:
    """Replace weekday freshness heuristics with actual XSHG exchange sessions."""
    quality = manifest.get("quality")
    if not isinstance(quality, dict):
        return manifest
    as_of_date = str(quality.get("as_of_date") or manifest.get("requested_range", {}).get("end"))
    observed = str(
        quality.get("observed_at_utc")
        or manifest.get("generated_at_utc")
        or pd.Timestamp.now(tz="UTC").isoformat()
    )
    model_session, pcf_session = effective_sessions(as_of_date, observed)
    by_symbol = quality.get("by_symbol", {})

    for item in by_symbol.values():
        price_lag = session_lag(item.get("price_last_date"), model_session)
        nav_lag = session_lag(item.get("raw_nav_last_date") or item.get("nav_last_date"), model_session)
        pit_nav_lag = session_lag(item.get("pit_nav_last_date"), model_session)
        snapshot_lag = session_lag(item.get("snapshot_last_date"), model_session)
        pcf_lag = session_lag(item.get("pcf_last_available_date"), pcf_session)

        item["price_lag_business_days"] = price_lag
        item["nav_lag_business_days"] = nav_lag
        item["raw_nav_lag_business_days"] = nav_lag
        item["pit_nav_lag_business_days"] = pit_nav_lag
        item["snapshot_lag_business_days"] = snapshot_lag
        item["pcf_lag_business_days"] = pcf_lag

        stale_price = price_lag is None or price_lag > 0
        stale_nav = nav_lag is None or nav_lag > 2
        stale_snapshot = snapshot_lag is None or snapshot_lag > 0
        source_degraded = bool(item.get("source_degraded"))
        critical = source_degraded and (stale_price or stale_nav or stale_snapshot)
        item["critical_source_failure"] = critical

        mutable_states = {
            "STALE_PRICE",
            "STALE_NAV",
            "STALE_SNAPSHOT",
            "STALE_PCF",
            "CRITICAL_SOURCE_FAILURE",
            "FRESH",
        }
        states = [state for state in item.get("states", []) if state not in mutable_states]
        if stale_price:
            states.append("STALE_PRICE")
        if stale_nav:
            states.append("STALE_NAV")
        if stale_snapshot:
            states.append("STALE_SNAPSHOT")
        if pcf_lag is None or pcf_lag > 0:
            states.append("STALE_PCF")
        if critical:
            states.append("CRITICAL_SOURCE_FAILURE")
        item["states"] = list(dict.fromkeys(states)) or ["FRESH"]
        item["formal_signal_ready"] = bool(
            int(item.get("usable_price_observations") or 0) >= 120
            and not stale_price
            and not stale_nav
            and not stale_snapshot
            and bool(item.get("pit_usable"))
            and not critical
        )

    quality["model_as_of_date"] = model_session
    quality["pcf_as_of_session"] = pcf_session
    quality["freshness_method"] = "exchange_calendars_XSHG_with_market_phase"
    quality["formal_signal_ready_symbols"] = [
        symbol for symbol, item in by_symbol.items() if item.get("formal_signal_ready")
    ]
    manifest["quality"] = quality
    return manifest
