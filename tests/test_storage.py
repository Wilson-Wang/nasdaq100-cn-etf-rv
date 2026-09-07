from __future__ import annotations

import pandas as pd

from etf_dataset.storage import table_summary, upsert_csv


def test_upsert_prefers_higher_priority_source(tmp_path):
    path = tmp_path / "prices.csv"
    old = pd.DataFrame(
        [
            {
                "symbol": "513100",
                "date": "2026-08-20",
                "close": 1.0,
                "source": "baostock",
                "source_priority": 10,
                "ingested_at_utc": "2026-08-20T10:00:00+00:00",
            }
        ]
    )
    fallback = pd.DataFrame(
        [
            {
                "symbol": "513100",
                "date": "2026-08-20",
                "close": 9.0,
                "source": "eastmoney",
                "source_priority": 20,
                "ingested_at_utc": "2026-08-21T10:00:00+00:00",
            }
        ]
    )
    upsert_csv(path, old, ["symbol", "date"])
    result = upsert_csv(path, fallback, ["symbol", "date"])
    assert len(result) == 1
    assert float(result.iloc[0]["close"]) == 1.0


def test_upsert_keeps_latest_same_priority(tmp_path):
    path = tmp_path / "snapshot.csv"
    first = pd.DataFrame(
        [
            {
                "symbol": "159941",
                "data_date": "2026-08-20",
                "last": 1.0,
                "source": "same",
                "source_priority": 10,
                "ingested_at_utc": "2026-08-20T08:00:00+00:00",
            }
        ]
    )
    second = first.copy()
    second.loc[0, "last"] = 1.1
    second.loc[0, "ingested_at_utc"] = "2026-08-20T09:00:00+00:00"
    upsert_csv(path, first, ["symbol", "data_date"])
    result = upsert_csv(path, second, ["symbol", "data_date"])
    assert float(result.iloc[0]["last"]) == 1.1


def test_table_summary_exposes_per_symbol_usable_coverage(tmp_path):
    path = tmp_path / "prices.csv"
    dates = pd.bdate_range("2026-01-01", periods=125).strftime("%Y-%m-%d").tolist()
    rows = []
    for symbol in ("159941", "513100"):
        for index, date in enumerate(dates):
            rows.append(
                {
                    "symbol": symbol,
                    "date": date,
                    "close": 1.0,
                    "is_tradable": not (symbol == "513100" and index < 10),
                    "source": "baostock",
                }
            )
    pd.DataFrame(rows).to_csv(path, index=False)

    summary = table_summary(path, "date")

    assert summary["rows"] == 250
    assert summary["by_symbol"]["159941"]["tradable_rows"] == 125
    assert summary["by_symbol"]["159941"]["precheck_120"] is True
    assert summary["by_symbol"]["513100"]["tradable_rows"] == 115
    assert summary["by_symbol"]["513100"]["precheck_120"] is False
    assert summary["by_symbol"]["513100"]["precheck_250"] is False
