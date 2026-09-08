from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"symbol": "string"})


def _values_equal(left: object, right: object) -> bool:
    if pd.isna(left) and pd.isna(right):
        return True
    try:
        return bool(left == right)
    except (TypeError, ValueError):
        return False


def _drop_unchanged_incoming(
    existing: pd.DataFrame,
    incoming: pd.DataFrame,
    key_columns: list[str],
) -> pd.DataFrame:
    """Drop refresh rows whose stored business values are already identical.

    `ingested_at_utc` is provenance for the observation that won the upsert. A
    repeated download of the exact same row should not replace it solely because
    the new fetch happened later; doing so creates large meaningless Git diffs.
    A changed value at the same source priority still replaces the stored row.
    """
    if existing.empty or incoming.empty:
        return incoming

    left = existing.copy()
    right = incoming.copy()
    for key in key_columns:
        if key in left.columns:
            left[key] = left[key].astype("string")
        if key in right.columns:
            right[key] = right[key].astype("string")

    left = left.drop_duplicates(key_columns, keep="first").set_index(key_columns, drop=False)
    compare_columns = sorted((set(left.columns) | set(right.columns)) - {"ingested_at_utc"})
    keep_indices: list[object] = []

    for index, row in right.iterrows():
        key = tuple(row.get(column) for column in key_columns)
        lookup_key: object = key[0] if len(key) == 1 else key
        if lookup_key not in left.index:
            keep_indices.append(index)
            continue

        stored = left.loc[lookup_key]
        if isinstance(stored, pd.DataFrame):
            stored = stored.iloc[0]

        same = True
        for column in compare_columns:
            if not _values_equal(stored.get(column, pd.NA), row.get(column, pd.NA)):
                same = False
                break
        if not same:
            keep_indices.append(index)

    return right.loc[keep_indices].copy()


def upsert_csv(path: str | Path, incoming: pd.DataFrame, key_columns: list[str]) -> pd.DataFrame:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_csv(path)

    if incoming.empty and existing.empty:
        return pd.DataFrame(columns=incoming.columns)
    if incoming.empty:
        return existing

    incoming = _drop_unchanged_incoming(existing, incoming, key_columns)
    if incoming.empty:
        return existing

    combined = pd.concat([existing, incoming], ignore_index=True, sort=False)
    for key in key_columns:
        combined[key] = combined[key].astype("string")

    combined["source_priority"] = pd.to_numeric(
        combined["source_priority"], errors="coerce"
    ).fillna(9999)
    combined["_ingested_sort"] = pd.to_datetime(
        combined["ingested_at_utc"], errors="coerce", utc=True
    )

    sort_columns = key_columns + ["source_priority", "_ingested_sort"]
    ascending = [True] * len(key_columns) + [True, False]
    combined = combined.sort_values(sort_columns, ascending=ascending, kind="stable")
    combined = combined.drop_duplicates(subset=key_columns, keep="first")
    combined = combined.drop(columns=["_ingested_sort"]).reset_index(drop=True)
    combined.to_csv(path, index=False)
    return combined


def write_parquet_mirror(csv_path: str | Path) -> Path | None:
    csv_path = Path(csv_path)
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return None
    frame = pd.read_csv(csv_path, dtype={"symbol": "string"})
    parquet_path = csv_path.with_suffix(".parquet")
    frame.to_parquet(parquet_path, index=False)
    return parquet_path


def sha256_file(path: str | Path) -> str:
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bool_series(series: pd.Series) -> pd.Series:
    """Normalize CSV boolean values without treating missing values as tradable."""
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
        .map({"true": True, "false": False, "1": True, "0": False})
        .fillna(False)
        .astype(bool)
    )


def _symbol_summary(df: pd.DataFrame, date_column: str) -> dict[str, dict]:
    if "symbol" not in df.columns:
        return {}

    summaries: dict[str, dict] = {}
    for symbol, group in df.groupby("symbol", sort=True, dropna=False):
        symbol_key = str(symbol)
        valid_dates = (
            pd.to_datetime(group[date_column], errors="coerce")
            if date_column in group.columns
            else pd.Series(dtype="datetime64[ns]")
        )
        valid_dates = valid_dates.dropna()

        if "is_tradable" in group.columns:
            tradable_rows = int(_bool_series(group["is_tradable"]).sum())
        else:
            tradable_rows = None

        observation_rows = tradable_rows if tradable_rows is not None else int(len(group))
        summaries[symbol_key] = {
            "rows": int(len(group)),
            "tradable_rows": tradable_rows,
            "min_date": valid_dates.min().strftime("%Y-%m-%d") if not valid_dates.empty else None,
            "max_date": valid_dates.max().strftime("%Y-%m-%d") if not valid_dates.empty else None,
            "sources": group["source"].value_counts(dropna=False).astype(int).to_dict()
            if "source" in group.columns
            else {},
            "precheck_120": observation_rows >= 120,
            "precheck_250": observation_rows >= 250,
        }
    return summaries


def table_summary(path: str | Path, date_column: str) -> dict:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return {
            "rows": 0,
            "symbols": 0,
            "min_date": None,
            "max_date": None,
            "sources": {},
            "by_symbol": {},
        }
    df = pd.read_csv(path, dtype={"symbol": "string"})
    return {
        "rows": int(len(df)),
        "symbols": int(df["symbol"].nunique()) if "symbol" in df.columns else 0,
        "min_date": str(df[date_column].min()) if date_column in df.columns and len(df) else None,
        "max_date": str(df[date_column].max()) if date_column in df.columns and len(df) else None,
        "sources": df["source"].value_counts(dropna=False).astype(int).to_dict()
        if "source" in df.columns
        else {},
        "by_symbol": _symbol_summary(df, date_column),
        "sha256": sha256_file(path),
    }


def write_manifest(path: str | Path, payload: dict) -> None:
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
