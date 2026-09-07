from __future__ import annotations

import pandas as pd

from etf_dataset import sources
from etf_dataset.config import ETF


def _frame(symbol: str, dates: list[str], source: str, priority: int) -> pd.DataFrame:
    rows = []
    for date in dates:
        rows.append(
            {
                "symbol": symbol,
                "date": date,
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.0,
                "preclose": 1.0,
                "volume": 1000.0,
                "amount": 1000.0,
                "turnover": 1.0,
                "pct_change": 0.0,
                "trade_status": 1.0,
                "is_tradable": True,
                "source": source,
                "source_priority": priority,
                "ingested_at_utc": "2026-09-07T00:00:00+00:00",
            }
        )
    return pd.DataFrame(rows).reindex(columns=sources.PRICE_COLUMNS)


def test_partial_primary_is_supplemented_and_primary_wins_overlap(monkeypatch):
    etf = ETF("513100", "sh", "test", "NDX")
    primary_dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
    fallback_dates = [
        "2025-12-29",
        "2025-12-30",
        "2025-12-31",
        "2026-01-02",
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
    ]

    monkeypatch.setattr(
        sources,
        "fetch_prices_baostock",
        lambda *_args, **_kwargs: _frame(etf.symbol, primary_dates, "baostock", 10),
    )
    monkeypatch.setattr(
        sources,
        "fetch_prices_akshare_em",
        lambda *_args, **_kwargs: _frame(
            etf.symbol, fallback_dates, "akshare:eastmoney:fund_etf_hist_em", 20
        ),
    )

    frame, errors = sources.fetch_prices_with_fallback(
        etf, "2025-12-01", "2026-01-07"
    )

    assert not errors
    assert set(frame["date"]) == set(fallback_dates)
    overlap = frame.loc[frame["date"] == "2026-01-05"].iloc[0]
    assert overlap["source"] == "baostock"
    assert overlap["source_priority"] == 10


def test_complete_primary_does_not_call_fallback(monkeypatch):
    etf = ETF("513100", "sh", "test", "NDX")
    dates = pd.bdate_range("2026-01-01", "2026-01-30").strftime("%Y-%m-%d").tolist()

    monkeypatch.setattr(
        sources,
        "fetch_prices_baostock",
        lambda *_args, **_kwargs: _frame(etf.symbol, dates, "baostock", 10),
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("fallback should not be called for complete coverage")

    monkeypatch.setattr(sources, "fetch_prices_akshare_em", fail_if_called)

    frame, errors = sources.fetch_prices_with_fallback(
        etf, "2026-01-01", "2026-01-30"
    )

    assert not errors
    assert len(frame) == len(dates)
    assert set(frame["source"]) == {"baostock"}


def test_tradability_requires_positive_volume_and_price():
    frame = pd.DataFrame(
        {
            "close": [1.0, 1.0, 0.0],
            "volume": [100.0, 0.0, 100.0],
            "trade_status": [1.0, 1.0, 1.0],
        }
    )

    out = sources._add_tradability(frame)

    assert out["is_tradable"].tolist() == [True, False, False]
