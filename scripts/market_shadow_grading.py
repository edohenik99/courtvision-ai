from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.kelly_performance import build_kelly_decision_performance, kelly_perf_log_lines


RESULT_HIT = "hit"
RESULT_MISS = "miss"
RESULT_PUSH = "push"
RESULT_PENDING = "pending"
CONTEXT_ALIGNMENT_VALUES = ("aligned", "conflicted", "neutral", "insufficient_data")
TRACKED_ALIGNMENT_VALUES = ("aligned", "conflicted", "neutral")
TRACKED_SELECTION_SIDES = ("over", "under")


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _line_value(row: pd.Series) -> float | None:
    for col in ("sportsbook_line", "line", "line_value"):
        if col in row.index:
            value = _safe_float(row.get(col))
            if value is not None:
                return value
    return None


def _player_name(row: pd.Series) -> str:
    return _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name"))


def _market_type(row: pd.Series) -> str:
    return _safe_text(row.get("market_type")) or _safe_text(row.get("market"))


def _identity(row: pd.Series) -> tuple[str, str, str, str]:
    line = _line_value(row)
    line_key = "" if line is None else f"{line:.4f}"
    return (
        _player_name(row).lower(),
        _market_type(row).lower(),
        _safe_text(row.get("selection") or row.get("side")).lower(),
        line_key,
    )


def _normalize_result(value: Any) -> str:
    text = _safe_text(value).lower()
    if text in {"hit", "win", "won", "true", "1"}:
        return RESULT_HIT
    if text in {"miss", "loss", "lost", "false", "0"}:
        return RESULT_MISS
    if text == "push":
        return RESULT_PUSH
    return RESULT_PENDING


def _direct_result(row: pd.Series) -> str:
    for col in ("result_status", "graded_result", "result"):
        if col in row.index:
            result = _normalize_result(row.get(col))
            if result != RESULT_PENDING:
                return result
    if "hit" in row.index:
        hit_value = row.get("hit")
        if _safe_text(hit_value) != "":
            return _normalize_result(hit_value)
    return RESULT_PENDING


def _graded_lookup(runtime_root: Path, history_root: Path, prediction_date: str) -> dict[tuple[str, str, str, str], str]:
    sources = [
        runtime_root / "history" / f"graded_picks_{prediction_date}.csv",
        runtime_root / "research" / f"grading_results_{prediction_date}.csv",
        history_root / "pick_history.csv",
        history_root / "market_shadow_history.csv",
    ]
    lookup: dict[tuple[str, str, str, str], str] = {}
    for path in sources:
        df = _read_csv(path)
        if df.empty:
            continue
        if "prediction_date" in df.columns:
            df = df[df["prediction_date"].astype(str) == str(prediction_date)].copy()
        for _, row in df.iterrows():
            result = _direct_result(row)
            if result == RESULT_PENDING:
                continue
            key = _identity(row)
            if key[0] and key[1]:
                lookup[key] = result
    return lookup


def _american_profit_for_unit_stake(odds: Any) -> float | None:
    american = _safe_float(odds)
    if american is None or american == 0:
        return None
    if american > 0:
        return american / 100.0
    return 100.0 / abs(american)


def _roi_for_group(group: pd.DataFrame) -> float | None:
    profit = 0.0
    graded_bets = 0
    for _, row in group.iterrows():
        result = _safe_text(row.get("_shadow_result"))
        if result not in {RESULT_HIT, RESULT_MISS}:
            continue
        win_profit = _american_profit_for_unit_stake(row.get("odds"))
        if win_profit is None:
            continue
        graded_bets += 1
        profit += win_profit if result == RESULT_HIT else -1.0
    if graded_bets == 0:
        return None
    return round(profit / graded_bets, 6)


def _numeric_avg(group: pd.DataFrame, col: str) -> float | None:
    if col not in group.columns:
        return None
    values = pd.to_numeric(group[col], errors="coerce").dropna()
    if values.empty:
        return None
    return round(float(values.mean()), 6)


def _alignment_counts(df: pd.DataFrame) -> dict[str, int]:
    counts = {"aligned": 0, "conflicted": 0, "neutral": 0, "insufficient_data": 0}
    if df.empty or "context_pick_alignment" not in df.columns:
        return counts
    series = df["context_pick_alignment"].fillna("insufficient_data").astype(str).str.strip().str.lower()
    raw_counts = series.value_counts().to_dict()
    for key in counts:
        counts[key] = int(raw_counts.get(key, 0) or 0)
    return counts


def _alignment_value(value: Any) -> str:
    text = _safe_text(value).lower()
    return text if text in CONTEXT_ALIGNMENT_VALUES else "insufficient_data"


def _selection_side(value: Any) -> str:
    text = _safe_text(value).lower()
    if text in TRACKED_SELECTION_SIDES:
        return text
    return "unknown"


def _performance_for_group(group: pd.DataFrame) -> dict[str, Any]:
    if group.empty or "_shadow_result" not in group.columns:
        return {
            "total_picks": 0,
            "graded_picks": 0,
            "pending_picks": 0,
            "hits": 0,
            "misses": 0,
            "pushes": 0,
            "hit_rate": None,
            "roi": None,
            "status": "insufficient_sample",
        }

    hits = int(group["_shadow_result"].eq(RESULT_HIT).sum())
    misses = int(group["_shadow_result"].eq(RESULT_MISS).sum())
    pushes = int(group["_shadow_result"].eq(RESULT_PUSH).sum())
    pending = int(group["_shadow_result"].eq(RESULT_PENDING).sum())
    hit_denominator = hits + misses
    return {
        "total_picks": int(len(group)),
        "graded_picks": int(hits + misses + pushes),
        "pending_picks": pending,
        "hits": hits,
        "misses": misses,
        "pushes": pushes,
        "hit_rate": round(hits / hit_denominator, 6) if hit_denominator else None,
        "roi": _roi_for_group(group),
        "status": "ok" if hit_denominator else "insufficient_sample",
    }


def _context_alignment_performance(working: pd.DataFrame) -> dict[str, Any]:
    if working.empty:
        return {
            "status": "insufficient_sample",
            "by_alignment": {key: _performance_for_group(pd.DataFrame()) for key in CONTEXT_ALIGNMENT_VALUES},
            "by_alignment_and_selection_side": {
                key: {side: _performance_for_group(pd.DataFrame()) for side in TRACKED_SELECTION_SIDES}
                for key in TRACKED_ALIGNMENT_VALUES
            },
            "by_alignment_and_market_type": {key: {} for key in TRACKED_ALIGNMENT_VALUES},
        }

    scoped = working.copy()
    if "context_pick_alignment" not in scoped.columns:
        scoped["context_pick_alignment"] = "insufficient_data"
    if "selection" not in scoped.columns and "side" in scoped.columns:
        scoped["selection"] = scoped["side"]
    scoped["_context_pick_alignment"] = scoped["context_pick_alignment"].map(_alignment_value)
    scoped["_selection_side"] = scoped.get("selection", pd.Series("", index=scoped.index)).map(_selection_side)

    by_alignment = {
        key: _performance_for_group(scoped[scoped["_context_pick_alignment"].eq(key)])
        for key in CONTEXT_ALIGNMENT_VALUES
    }
    by_alignment_and_selection_side = {
        alignment: {
            side: _performance_for_group(
                scoped[
                    scoped["_context_pick_alignment"].eq(alignment)
                    & scoped["_selection_side"].eq(side)
                ]
            )
            for side in TRACKED_SELECTION_SIDES
        }
        for alignment in TRACKED_ALIGNMENT_VALUES
    }

    by_alignment_and_market_type: dict[str, dict[str, Any]] = {key: {} for key in TRACKED_ALIGNMENT_VALUES}
    market_series = scoped["market_type"] if "market_type" in scoped.columns else pd.Series("unknown", index=scoped.index)
    scoped["_market_type"] = market_series.fillna("unknown").astype(str).str.strip().replace("", "unknown")
    for alignment in TRACKED_ALIGNMENT_VALUES:
        alignment_df = scoped[scoped["_context_pick_alignment"].eq(alignment)]
        for market_type, group in alignment_df.groupby("_market_type", sort=True):
            by_alignment_and_market_type[alignment][str(market_type)] = _performance_for_group(group)

    graded_hit_miss = sum(
        int(item.get("hits", 0) or 0) + int(item.get("misses", 0) or 0)
        for item in by_alignment.values()
    )
    return {
        "status": "ok" if graded_hit_miss else "insufficient_sample",
        "by_alignment": by_alignment,
        "by_alignment_and_selection_side": by_alignment_and_selection_side,
        "by_alignment_and_market_type": by_alignment_and_market_type,
    }


def build_market_shadow_grading(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
) -> dict[str, Any]:
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)
    full_market_path = runtime_root / "operator" / f"full_market_board_{prediction_date}.csv"
    full_market_df = _read_csv(full_market_path)

    working = pd.DataFrame()
    if full_market_df.empty:
        rows: list[dict[str, Any]] = []
    else:
        lookup = _graded_lookup(runtime_root, history_root, prediction_date)
        working = full_market_df.copy()
        results: list[str] = []
        for _, row in working.iterrows():
            direct = _direct_result(row)
            if direct != RESULT_PENDING:
                results.append(direct)
                continue
            results.append(lookup.get(_identity(row), RESULT_PENDING))
        working["_shadow_result"] = results

        rows = []
        for market_type, group in working.groupby("market_type", sort=True):
            hits = int(group["_shadow_result"].eq(RESULT_HIT).sum())
            misses = int(group["_shadow_result"].eq(RESULT_MISS).sum())
            pushes = int(group["_shadow_result"].eq(RESULT_PUSH).sum())
            pending = int(group["_shadow_result"].eq(RESULT_PENDING).sum())
            graded_picks = hits + misses + pushes
            hit_denominator = hits + misses
            rows.append(
                {
                    "market_type": str(market_type),
                    "total_picks": int(len(group)),
                    "graded_picks": graded_picks,
                    "pending_picks": pending,
                    "hit_rate": round(hits / hit_denominator, 6) if hit_denominator else None,
                    "avg_edge": _numeric_avg(group, "edge"),
                    "avg_confidence": _numeric_avg(group, "confidence"),
                    "avg_quality_score": _numeric_avg(group, "quality_score"),
                    "roi": _roi_for_group(group),
                    "context_alignment": _alignment_counts(group),
                    "hits": hits,
                    "misses": misses,
                    "pushes": pushes,
                }
            )

    totals = {
        "total_picks": int(sum(row["total_picks"] for row in rows)),
        "graded_picks": int(sum(row["graded_picks"] for row in rows)),
        "pending_picks": int(sum(row["pending_picks"] for row in rows)),
    }
    hit_rows = sum(int(row.get("hits", 0)) for row in rows)
    miss_rows = sum(int(row.get("misses", 0)) for row in rows)
    totals["hit_rate"] = round(hit_rows / (hit_rows + miss_rows), 6) if (hit_rows + miss_rows) else None
    totals["context_alignment"] = {
        key: int(sum((row.get("context_alignment") or {}).get(key, 0) for row in rows))
        for key in CONTEXT_ALIGNMENT_VALUES
    }
    context_alignment_performance = _context_alignment_performance(working)
    kelly_decision_performance = build_kelly_decision_performance(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=runtime_root.parent if runtime_root.name == "runtime" else runtime_root,
    )

    return {
        "prediction_date": prediction_date,
        "source": str(full_market_path),
        "scope": "full_market_shadow_grading",
        "elite_locked_to": ["player_points"],
        "kelly_locked_to": ["player_points"],
        "totals": totals,
        "markets": rows,
        "context_alignment_performance": context_alignment_performance,
        "kelly_decision_performance": kelly_decision_performance,
    }


def render_market_shadow_report(payload: dict[str, Any]) -> str:
    lines = [
        f"Market Shadow Report - {payload.get('prediction_date')}",
        "=" * 72,
        "Scope: full-market board diagnostics only. Elite and Kelly remain player_points only.",
        "",
    ]
    totals = payload.get("totals", {})
    lines.append(
        "Totals: "
        f"total={totals.get('total_picks', 0)} "
        f"graded={totals.get('graded_picks', 0)} "
        f"pending={totals.get('pending_picks', 0)} "
        f"hit_rate={_format_pct(totals.get('hit_rate'))}"
    )
    lines.append("")
    alignment = totals.get("context_alignment", {}) if isinstance(totals, dict) else {}
    lines.append(
        "Context alignment: "
        f"aligned={int(alignment.get('aligned', 0) or 0)} "
        f"conflicted={int(alignment.get('conflicted', 0) or 0)} "
        f"neutral={int(alignment.get('neutral', 0) or 0)} "
        f"insufficient_data={int(alignment.get('insufficient_data', 0) or 0)}"
    )
    lines.append("")
    performance = payload.get("context_alignment_performance", {})
    by_alignment = performance.get("by_alignment", {}) if isinstance(performance, dict) else {}
    lines.append("Context Alignment Performance")
    if isinstance(performance, dict) and performance.get("status") == "insufficient_sample":
        lines.append("No resolved graded hit/miss sample yet; context alignment performance is pending.")
    for alignment_key in TRACKED_ALIGNMENT_VALUES:
        lines.append(_performance_line(alignment_key, by_alignment.get(alignment_key, {})))
    lines.append("")

    kelly_perf = payload.get("kelly_decision_performance", {})
    lines.append("Kelly Decision Performance")
    if isinstance(kelly_perf, dict):
        by_eligible = kelly_perf.get("by_kelly_eligible", {})
        if isinstance(by_eligible, dict):
            lines.append(_performance_line("kelly_eligible=true", by_eligible.get("true", {})))
            lines.append(_performance_line("kelly_eligible=false", by_eligible.get("false", {})))
        by_skip = kelly_perf.get("by_skip_reason", {})
        if isinstance(by_skip, dict):
            for reason, item in sorted(by_skip.items()):
                lines.append(_performance_line(f"skip_reason={reason}", item))
    else:
        lines.append("- insufficient_sample")
    lines.append("")

    by_side = (
        performance.get("by_alignment_and_selection_side", {})
        if isinstance(performance, dict)
        else {}
    )
    lines.append("Context Alignment Performance by Selection Side")
    for alignment_key in TRACKED_ALIGNMENT_VALUES:
        side_payload = by_side.get(alignment_key, {}) if isinstance(by_side, dict) else {}
        for side in TRACKED_SELECTION_SIDES:
            item = side_payload.get(side, {}) if isinstance(side_payload, dict) else {}
            if int(item.get("total_picks", 0) or 0) > 0:
                lines.append(_performance_line(f"{alignment_key}/{side}", item))
    lines.append("")

    by_market = (
        performance.get("by_alignment_and_market_type", {})
        if isinstance(performance, dict)
        else {}
    )
    lines.append("Context Alignment Performance by Market Type")
    any_market_rows = False
    for alignment_key in TRACKED_ALIGNMENT_VALUES:
        market_payload = by_market.get(alignment_key, {}) if isinstance(by_market, dict) else {}
        for market_type, item in sorted(market_payload.items()):
            if int(item.get("total_picks", 0) or 0) > 0:
                any_market_rows = True
                lines.append(_performance_line(f"{alignment_key}/{market_type}", item))
    if not any_market_rows:
        lines.append("- n/a")
    lines.append("")

    header = (
        f"{'Market':34} {'Total':>5} {'Graded':>6} {'Pending':>7} "
        f"{'Hit%':>7} {'AvgEdge':>8} {'AvgConf':>8} {'AvgQual':>8} {'ROI':>8} {'Align':>7} {'Conflict':>8}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for row in payload.get("markets", []):
        lines.append(
            f"{str(row.get('market_type', '')):34} "
            f"{int(row.get('total_picks', 0)):5d} "
            f"{int(row.get('graded_picks', 0)):6d} "
            f"{int(row.get('pending_picks', 0)):7d} "
            f"{_format_pct(row.get('hit_rate')):>7} "
            f"{_format_num(row.get('avg_edge')):>8} "
            f"{_format_num(row.get('avg_confidence')):>8} "
            f"{_format_num(row.get('avg_quality_score')):>8} "
            f"{_format_pct(row.get('roi')):>8} "
            f"{int((row.get('context_alignment') or {}).get('aligned', 0)):7d} "
            f"{int((row.get('context_alignment') or {}).get('conflicted', 0)):8d}"
        )
    lines.append("")
    return "\n".join(lines)


def _performance_line(label: str, item: Any) -> str:
    payload = item if isinstance(item, dict) else {}
    total = payload.get("total_picks", payload.get("count", 0))
    graded = payload.get("graded_picks", payload.get("graded_count", 0))
    pending = payload.get("pending_picks", payload.get("pending_count", 0))
    return (
        f"- {label}: "
        f"total={int(total or 0)} "
        f"graded={int(graded or 0)} "
        f"pending={int(pending or 0)} "
        f"hit_rate={_format_pct(payload.get('hit_rate'))} "
        f"roi={_format_pct(payload.get('roi'))} "
        f"status={_safe_text(payload.get('status')) or 'insufficient_sample'}"
    )


def _format_num(value: Any) -> str:
    number = _safe_float(value)
    return "n/a" if number is None else f"{number:.3f}"


def _format_pct(value: Any) -> str:
    number = _safe_float(value)
    return "n/a" if number is None else f"{number * 100:.1f}%"


def write_market_shadow_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
) -> tuple[Path, Path, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    payload = build_market_shadow_grading(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    diagnostics_path = runtime_root / "diagnostics" / f"market_shadow_grading_{prediction_date}.json"
    report_path = runtime_root / "operator" / f"market_shadow_report_{prediction_date}.txt"
    diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostics_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    report_path.write_text(render_market_shadow_report(payload), encoding="utf-8")
    grading_summary_path = runtime_root / "diagnostics" / f"grading_summary_{prediction_date}.json"
    if grading_summary_path.exists():
        try:
            grading_summary = json.loads(grading_summary_path.read_text(encoding="utf-8"))
        except Exception:
            grading_summary = {}
        if isinstance(grading_summary, dict):
            grading_summary["kelly_decision_performance"] = payload.get("kelly_decision_performance", {})
            grading_summary_path.write_text(json.dumps(grading_summary, indent=2, default=str), encoding="utf-8")
    return diagnostics_path, report_path, payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write full-market shadow grading by market type.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    args = parser.parse_args(argv)
    diagnostics_path, report_path, payload = write_market_shadow_outputs(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
    )
    totals = payload.get("totals", {})
    print(f"market_shadow_grading_json={diagnostics_path}")
    print(f"market_shadow_report_txt={report_path}")
    print(
        "market_shadow_totals "
        f"total={totals.get('total_picks', 0)} "
        f"graded={totals.get('graded_picks', 0)} "
        f"pending={totals.get('pending_picks', 0)}"
    )
    performance = payload.get("context_alignment_performance", {})
    by_alignment = performance.get("by_alignment", {}) if isinstance(performance, dict) else {}
    for alignment_key in TRACKED_ALIGNMENT_VALUES:
        item = by_alignment.get(alignment_key, {}) if isinstance(by_alignment, dict) else {}
        print(
            f"[CONTEXT] graded_alignment_{alignment_key}="
            f"{int(item.get('graded_picks', 0) or 0)} "
            f"hit_rate={_format_pct(item.get('hit_rate'))} "
            f"roi={_format_pct(item.get('roi'))}"
        )
    for line in kelly_perf_log_lines(payload.get("kelly_decision_performance", {})):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
