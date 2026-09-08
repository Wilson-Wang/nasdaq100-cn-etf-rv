from __future__ import annotations

import pandas as pd

from etf_dataset.quality import build_quality_report


AFTER_CLOSE_UTC = "2026-09-08T08:30:00+00:00"  # 16:30 Asia/Shanghai
BEFORE_OPEN_UTC = "2026-09-08T00:30:00+00:00"  # 08:30 Asia/Shanghai


def _write_frames(
    tmp_path,
    *,
    pit_verified: bool = True,
    price_end: str = "2026-09-08",
    snapshot_date: str = "2026-09-08",
    subscription_status: str | None = None,
    redemption_status: str | None = None,
):
    price_dates = pd.bdate_range(end=price_end, periods=125)
    prices = pd.DataFrame(
        {
            "symbol": ["513100"] * len(price_dates),
            "date": price_dates.strftime("%Y-%m-%d"),
            "close": [1.0] * len(price_dates),
            "volume": [1000] * len(price_dates),
            "is_tradable": [True] * len(price_dates),
            "source": ["baostock"] * len(price_dates),
        }
    )
    nav = pd.DataFrame(
        [
            {
                "symbol": "513100",
                "nav_date": "2026-09-04",
                "unit_nav": 1.0,
                "pit_verified": pit_verified,
                "subscription_status": subscription_status,
                "redemption_status": redemption_status,
                "source": "eastmoney",
            }
        ]
    )
    snapshot = pd.DataFrame(
        [
            {
                "symbol": "513100",
                "data_date": snapshot_date,
                "last": 1.0,
                "source": "eastmoney",
            }
        ]
    )

    prices_path = tmp_path / "prices.csv"
    nav_path = tmp_path / "nav.csv"
    snapshot_path = tmp_path / "snapshot.csv"
    prices.to_csv(prices_path, index=False)
    nav.to_csv(nav_path, index=False)
    snapshot.to_csv(snapshot_path, index=False)
    return prices_path, nav_path, snapshot_path


def _write_pcf(
    tmp_path,
    *,
    date: str = "2026-09-08",
    creation_allowed: bool = True,
    redemption_allowed: bool = True,
    pit_verified: bool = True,
):
    path = tmp_path / "pcf.csv"
    pd.DataFrame(
        [
            {
                "symbol": "513100",
                "date": date,
                "creation_allowed": creation_allowed,
                "redemption_allowed": redemption_allowed,
                "creation_unit": 1_000_000,
                "creation_limit": 5_000_000,
                "redemption_limit": 4_000_000,
                "net_creation_limit": 3_000_000,
                "net_redemption_limit": 2_000_000,
                "cash_substitution_limit_pct": 50.0,
                "max_creation_cash_premium_pct": 2.0,
                "max_redemption_cash_discount_pct": 1.0,
                "available_at": "2026-09-08T08:30:00+08:00",
                "availability_method": "sse_official_08:30_cn",
                "pit_verified": pit_verified,
                "source": "sse:official_pcf",
            }
        ]
    ).to_csv(path, index=False)
    return path


def _report(paths, *, observed_at=AFTER_CLOSE_UTC, failures=None, pcf_path=None):
    prices_path, nav_path, snapshot_path = paths
    return build_quality_report(
        prices_path,
        nav_path,
        snapshot_path,
        ["513100"],
        "2026-09-08",
        failures or [],
        observed_at_utc=observed_at,
        pcf_path=pcf_path,
    )


def test_quality_report_separates_research_depth_from_signal_readiness(tmp_path):
    report = _report(_write_frames(tmp_path, pit_verified=True))

    item = report["by_symbol"]["513100"]
    assert report["model_as_of_date"] == "2026-09-08"
    assert item["usable_price_observations"] == 125
    assert "LIMITED_RESEARCH_DEPTH" in item["states"]
    assert item["price_lag_business_days"] == 0
    assert item["nav_lag_business_days"] == 2
    assert item["formal_signal_ready"] is True
    assert "PCF_MISSING" in item["states"]
    assert report["formal_signal_ready_symbols"] == ["513100"]


def test_preclose_refresh_uses_previous_completed_weekday(tmp_path):
    paths = _write_frames(
        tmp_path,
        pit_verified=True,
        price_end="2026-09-07",
        snapshot_date="2026-09-07",
    )
    report = _report(paths, observed_at=BEFORE_OPEN_UTC)

    item = report["by_symbol"]["513100"]
    assert report["as_of_date"] == "2026-09-08"
    assert report["model_as_of_date"] == "2026-09-07"
    assert item["price_last_date"] == "2026-09-07"
    assert item["price_lag_business_days"] == 0
    assert item["snapshot_last_date"] == "2026-09-07"
    assert item["snapshot_lag_business_days"] == 0
    assert "STALE_PRICE" not in item["states"]
    assert "STALE_SNAPSHOT" not in item["states"]


def test_preclose_quality_excludes_future_same_day_rows(tmp_path):
    paths = _write_frames(tmp_path, pit_verified=True)
    report = _report(paths, observed_at=BEFORE_OPEN_UTC)

    item = report["by_symbol"]["513100"]
    assert report["model_as_of_date"] == "2026-09-07"
    assert item["price_last_date"] == "2026-09-07"
    assert item["snapshot_last_date"] is None
    assert "STALE_SNAPSHOT" in item["states"]


def test_quality_report_keeps_noncritical_source_degradation_separate(tmp_path):
    report = _report(
        _write_frames(tmp_path, pit_verified=False),
        failures=["513100 NAV eastmoney: ReadTimeout"],
    )

    item = report["by_symbol"]["513100"]
    assert "PIT_UNVERIFIED" in item["states"]
    assert "SOURCE_DEGRADED" in item["states"]
    assert "CRITICAL_SOURCE_FAILURE" not in item["states"]
    assert item["source_degraded"] is True
    assert item["critical_source_failure"] is False
    assert item["formal_signal_ready"] is False


def test_quality_report_marks_degraded_source_critical_when_current_input_is_stale(tmp_path):
    report = _report(
        _write_frames(
            tmp_path,
            pit_verified=True,
            snapshot_date="2026-09-07",
        ),
        failures=["snapshot akshare:eastmoney: ReadTimeout"],
    )

    item = report["by_symbol"]["513100"]
    assert "STALE_SNAPSHOT" in item["states"]
    assert "SOURCE_DEGRADED" in item["states"]
    assert "CRITICAL_SOURCE_FAILURE" in item["states"]
    assert item["critical_source_failure"] is True
    assert item["formal_signal_ready"] is False


def test_nav_status_fallback_promotes_only_explicit_restrictions(tmp_path):
    report = _report(
        _write_frames(
            tmp_path,
            subscription_status="暂停申购",
            redemption_status="场内卖出",
        )
    )

    item = report["by_symbol"]["513100"]
    assert item["primary_market"]["creation_state"] == "RESTRICTED"
    assert item["primary_market"]["redemption_state"] == "UNKNOWN"
    assert item["primary_market"]["confidence"] == "LOW"
    assert "CREATION_RESTRICTED_BY_NAV_STATUS" in item["states"]
    assert "REDEMPTION_RESTRICTED_BY_NAV_STATUS" not in item["states"]


def test_secondary_market_labels_are_not_misclassified_as_primary_market_open(tmp_path):
    report = _report(
        _write_frames(
            tmp_path,
            subscription_status="场内买入",
            redemption_status="场内卖出",
        )
    )

    item = report["by_symbol"]["513100"]
    assert item["primary_market"]["creation_state"] == "UNKNOWN"
    assert item["primary_market"]["redemption_state"] == "UNKNOWN"
    assert "PRIMARY_MARKET_STATUS_LOW_CONFIDENCE" in item["states"]


def test_current_verified_official_pcf_overrides_nav_fallback(tmp_path):
    paths = _write_frames(
        tmp_path,
        subscription_status="暂停申购",
        redemption_status="暂停赎回",
    )
    pcf_path = _write_pcf(
        tmp_path,
        creation_allowed=True,
        redemption_allowed=False,
    )
    report = _report(paths, pcf_path=pcf_path)

    item = report["by_symbol"]["513100"]
    assert item["pcf_current_verified"] is True
    assert item["pcf_lag_business_days"] == 0
    assert item["primary_market"]["source"] == "official_pcf"
    assert item["primary_market"]["confidence"] == "HIGH"
    assert item["primary_market"]["creation_state"] == "OPEN"
    assert item["primary_market"]["redemption_state"] == "RESTRICTED"
    assert "REDEMPTION_RESTRICTED_BY_PCF" in item["states"]
    assert "CREATION_RESTRICTED_BY_NAV_STATUS" not in item["states"]
    assert "PCF_MISSING" not in item["states"]


def test_stale_pcf_does_not_override_nav_fallback(tmp_path):
    paths = _write_frames(tmp_path, subscription_status="暂停申购")
    pcf_path = _write_pcf(tmp_path, date="2026-09-07")
    report = _report(paths, pcf_path=pcf_path)

    item = report["by_symbol"]["513100"]
    assert item["pcf_current_verified"] is False
    assert "STALE_PCF" in item["states"]
    assert item["primary_market"]["source"] == "nav_status_fallback"
    assert item["primary_market"]["creation_state"] == "RESTRICTED"
