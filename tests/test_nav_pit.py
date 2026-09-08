from __future__ import annotations

import pandas as pd

from etf_dataset.nav_pit import (
    PENDING_AVAILABILITY_METHOD,
    QDII_NAV_AVAILABILITY_METHOD,
    enrich_nav_pit,
)


def test_qdii_nav_becomes_usable_only_after_two_observed_exchange_days():
    nav = pd.DataFrame(
        [
            {
                "symbol": "513100",
                "nav_date": "2026-09-04",
                "unit_nav": 1.0,
                "pit_verified": False,
            }
        ]
    )
    exchange_days = [
        pd.Timestamp("2026-09-04"),
        pd.Timestamp("2026-09-07"),
        pd.Timestamp("2026-09-08"),
    ]

    out = enrich_nav_pit(nav, exchange_days)
    row = out.iloc[0]

    assert row["available_at"] == "2026-09-08T23:59:59+08:00"
    assert row["availability_source"] == QDII_NAV_AVAILABILITY_METHOD
    assert bool(row["availability_verified"]) is False
    assert bool(row["pit_verified"]) is False
    assert bool(row["pit_usable"]) is True


def test_latest_nav_stays_pending_when_second_following_session_not_observed():
    nav = pd.DataFrame(
        [
            {
                "symbol": "513100",
                "nav_date": "2026-09-04",
                "unit_nav": 1.0,
                "pit_verified": False,
            }
        ]
    )
    exchange_days = [pd.Timestamp("2026-09-04"), pd.Timestamp("2026-09-07")]

    out = enrich_nav_pit(nav, exchange_days)
    row = out.iloc[0]

    assert pd.isna(row["available_at"])
    assert row["availability_source"] == PENDING_AVAILABILITY_METHOD
    assert bool(row["pit_usable"]) is False


def test_verified_source_availability_is_preserved():
    nav = pd.DataFrame(
        [
            {
                "symbol": "513100",
                "nav_date": "2026-09-04",
                "unit_nav": 1.0,
                "available_at": "2026-09-05T01:00:00+08:00",
                "availability_source": "first_seen",
                "availability_verified": True,
                "pit_verified": True,
                "pit_usable": True,
            }
        ]
    )
    exchange_days = [
        pd.Timestamp("2026-09-04"),
        pd.Timestamp("2026-09-07"),
        pd.Timestamp("2026-09-08"),
    ]

    out = enrich_nav_pit(nav, exchange_days)
    row = out.iloc[0]

    assert row["available_at"] == "2026-09-05T01:00:00+08:00"
    assert row["availability_source"] == "first_seen"
    assert bool(row["pit_verified"]) is True
    assert bool(row["pit_usable"]) is True
