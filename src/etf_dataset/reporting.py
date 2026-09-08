from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


REPORT_SCHEMA_VERSION = "1.0.0"


def _float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    value = float(value)
    return value if np.isfinite(value) else None


def build_tactical_value(pair_analysis: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """Aggregate pair directions into an ETF-level tactical relative-value score."""
    accum: dict[str, float] = {symbol: 0.0 for symbol in symbols}
    weights: dict[str, float] = {symbol: 0.0 for symbol in symbols}
    if not pair_analysis.empty:
        for _, row in pair_analysis.iterrows():
            rotate_in = row.get("rotate_in")
            rotate_out = row.get("rotate_out")
            if pd.isna(rotate_in) or pd.isna(rotate_out):
                continue
            score = max(_float(row.get("pair_score")) or 0.0, 0.0) / 100.0
            liq = _float(row.get("pair_liquidity_score"))
            liq_factor = 1.0 if liq is None else max(min(liq / 100.0, 1.0), 0.0)
            z = abs(_float(row.get("z")) or 0.0)
            edge = max(_float(row.get("net_expected_convergence_5d")) or 0.0, 0.0)
            weight = score * (0.5 + 0.5 * liq_factor)
            magnitude = weight * max(z, 0.25) * max(edge, 0.0005)
            in_symbol = str(rotate_in)
            out_symbol = str(rotate_out)
            if in_symbol in accum:
                accum[in_symbol] += magnitude
                weights[in_symbol] += weight
            if out_symbol in accum:
                accum[out_symbol] -= magnitude
                weights[out_symbol] += weight

    rows = []
    for symbol in symbols:
        rv = accum[symbol] / weights[symbol] if weights[symbol] > 0 else 0.0
        rows.append({"symbol": symbol, "tactical_rv": rv})
    frame = pd.DataFrame(rows)
    if len(frame) > 1:
        frame["tvs"] = frame["tactical_rv"].rank(pct=True, method="average") * 100.0
    else:
        frame["tvs"] = 50.0
    return frame


def build_product_ranking(
    product_quality: pd.DataFrame,
    pair_analysis: pd.DataFrame,
    symbols: list[str],
) -> pd.DataFrame:
    tactical = build_tactical_value(pair_analysis, symbols)
    if product_quality.empty:
        result = tactical.copy()
        result["pqs"] = pd.NA
        result["pqs_coverage"] = 0.0
        result["overall"] = pd.NA
        result["overall_status"] = "PQS_INCOMPLETE"
        return result.sort_values("tvs", ascending=False).reset_index(drop=True)
    pqs = product_quality.copy()
    pqs["symbol"] = pqs["symbol"].astype("string")
    latest = pqs.sort_values("effective_date").drop_duplicates("symbol", keep="last")
    result = latest.merge(tactical, on="symbol", how="right")
    result["pqs"] = pd.to_numeric(result.get("pqs"), errors="coerce")
    result["pqs_coverage"] = pd.to_numeric(result.get("pqs_coverage"), errors="coerce").fillna(0)
    complete = result["pqs"].notna() & result["pqs_coverage"].ge(0.70)
    result["overall"] = np.where(complete, 0.60 * result["pqs"] + 0.40 * result["tvs"], np.nan)
    result["overall_status"] = np.where(complete, "COMPLETE", "PQS_INCOMPLETE")
    return result.sort_values(["overall", "tvs"], ascending=[False, False], na_position="last").reset_index(drop=True)


def snapshot_state(
    manifest: dict[str, Any],
    pair_analysis: pd.DataFrame,
    health: dict[str, Any],
) -> dict[str, Any]:
    signals = {}
    regimes = {}
    if not pair_analysis.empty:
        signals = {
            str(row["pair"]): str(row.get("signal"))
            for _, row in pair_analysis.iterrows()
        }
        regimes = {
            str(row["pair"]): str(row.get("regime"))
            for _, row in pair_analysis.iterrows()
        }
    quality = manifest.get("quality", {})
    model_ready = sorted(
        symbol
        for symbol, item in quality.get("by_symbol", {}).items()
        if item.get("formal_signal_ready") is True
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "dataset_run_id": manifest.get("run_id"),
        "model_as_of_date": quality.get("model_as_of_date") or quality.get("as_of_date"),
        "pair_engine": manifest.get("pair_engine_status", {}).get("engine"),
        "signals": signals,
        "regimes": regimes,
        "model_ready_symbols": model_ready,
        "health_status": health.get("status"),
        "failure_count": len(manifest.get("failures", [])),
    }


def choose_report_mode(current: dict[str, Any], previous: dict[str, Any] | None) -> tuple[str, list[str]]:
    if previous is None:
        return "FULL", ["first_report"]
    reasons: list[str] = []
    for key, reason in (
        ("pair_engine", "model_version_changed"),
        ("signals", "signal_state_changed"),
        ("regimes", "regime_changed"),
        ("model_ready_symbols", "data_readiness_changed"),
        ("health_status", "health_status_changed"),
    ):
        if current.get(key) != previous.get(key):
            reasons.append(reason)
    current_formal = any(value in {"TRADE", "STRONG TRADE"} for value in current.get("signals", {}).values())
    if current_formal:
        reasons.append("formal_signal_present")
    return ("FULL" if reasons else "DELTA"), reasons


def _pct(value: Any) -> str:
    number = _float(value)
    return "—" if number is None else f"{number * 100:.2f}%"


def _num(value: Any, digits: int = 2) -> str:
    number = _float(value)
    return "—" if number is None else f"{number:.{digits}f}"


def render_daily_report(
    manifest: dict[str, Any],
    product_ranking: pd.DataFrame,
    pair_analysis: pd.DataFrame,
    portfolio_status: dict[str, Any],
    health: dict[str, Any],
    mode: str,
    reasons: list[str],
) -> str:
    quality = manifest.get("quality", {})
    lines = [
        f"# Nasdaq-100 中国场内 ETF 相对价值日报 — {quality.get('model_as_of_date') or quality.get('as_of_date') or 'unknown'}",
        "",
        f"模式：**{mode}**" + (f"（{', '.join(reasons)}）" if reasons else ""),
        "",
        "## 第一部分：数据状态与研究过程",
        "",
        f"- Dataset run: `{manifest.get('run_id', 'unknown')}`；健康状态：**{health.get('status', 'UNKNOWN')}**。",
        f"- 模型日期：{quality.get('model_as_of_date') or quality.get('as_of_date') or '—'}；正式模型就绪 ETF：{len([1 for item in quality.get('by_symbol', {}).values() if item.get('formal_signal_ready') is True])}/{len(manifest.get('universe', []))}。",
        f"- 当前抓取失败：{len(manifest.get('failures', []))}；warnings：{len(manifest.get('warnings', []))}。",
        f"- Pair engine：`{manifest.get('pair_engine_status', {}).get('engine', 'unknown')}`；成本口径：{manifest.get('pair_engine_status', {}).get('cost_method', 'unknown')}。",
        "- 历史 NAV 若无精确发布时间，使用 conservative PIT；不得解释为精确 `pit_verified`。",
        "",
        "## 第二部分：产品价值排序",
        "",
        "| 排名 | ETF | 名称 | PQS | TVS | Overall | PQS覆盖 |",
        "|---:|---|---|---:|---:|---:|---:|",
    ]
    for idx, row in product_ranking.head(12).iterrows():
        lines.append(
            f"| {idx + 1} | {row.get('symbol', '—')} | {row.get('name', '—')} | "
            f"{_num(row.get('pqs'))} | {_num(row.get('tvs'))} | {_num(row.get('overall'))} | "
            f"{_pct(row.get('pqs_coverage'))} |"
        )

    lines.extend(
        [
            "",
            "PQS 是长期产品质量；TVS 是当日相对价值，不应互相替代。PQS覆盖不足70%时不生成伪 Overall。",
            "",
            "## 第三部分：Pair / 信号排名",
            "",
            "| 排名 | 换入 | 换出 | Z | HL | 净预期收敛5日 | 调整胜率 | PairScore | Regime | Signal |",
            "|---:|---|---|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    ranked = pair_analysis.sort_values("pair_score", ascending=False).head(10) if not pair_analysis.empty else pair_analysis
    for idx, (_, row) in enumerate(ranked.iterrows(), start=1):
        lines.append(
            f"| {idx} | {row.get('rotate_in', '—')} | {row.get('rotate_out', '—')} | "
            f"{_num(row.get('z'))} | {_num(row.get('half_life'))} | "
            f"{_pct(row.get('net_expected_convergence_5d'))} | {_pct(row.get('adjusted_win_rate_5d'))} | "
            f"{_num(row.get('pair_score'))} | {row.get('regime', '—')} | **{row.get('signal', '—')}** |"
        )
    formal_count = 0 if pair_analysis.empty else int(pair_analysis["signal"].isin(["TRADE", "STRONG TRADE"]).sum())
    lines.extend(
        [
            "",
            f"正式 Pair 信号：**{formal_count}**；portfolio 状态：**{portfolio_status.get('status', 'UNKNOWN')}**，选中 {portfolio_status.get('selected_pairs', 0)} 对。",
            "",
            "> 本报告是相对价值研究输出，不是无风险套利承诺，也不代表自动交易指令。",
        ]
    )
    return "\n".join(lines) + "\n"


def read_json(path: str | Path, default: Any) -> Any:
    path = Path(path)
    if not path.exists() or not path.stat().st_size:
        return default
    return json.loads(path.read_text())
