from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd


FINAL_RESULTS = {"win", "loss", "push"}
CAUTION_SCORE = {"insufficient_data": 0.0, "low": 1.0, "medium": 2.0, "high": 3.0}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


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


def _safe_read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _line_key(value: Any) -> str:
    parsed = _safe_float(value)
    if parsed is None:
        return _safe_text(value)
    return f"{parsed:.4f}"


def _identity_key(row: pd.Series) -> str:
    return "|".join(
        [
            _safe_text(row.get("prediction_date")).lower(),
            _safe_text(row.get("market_type") or row.get("market")).lower(),
            (_safe_text(row.get("entity_name")) or _safe_text(row.get("player_name"))).lower(),
            _safe_text(row.get("selection") or row.get("side")).lower(),
            _line_key(row.get("sportsbook_line") if "sportsbook_line" in row.index else row.get("line")),
        ]
    )


def _normalized_result(row: pd.Series) -> str:
    text = _safe_text(row.get("result") or row.get("graded_result")).lower()
    if text in FINAL_RESULTS:
        return text
    is_win = _safe_float(row.get("is_win"))
    is_push = _safe_float(row.get("is_push"))
    is_loss = _safe_float(row.get("is_loss"))
    if is_win == 1.0:
        return "win"
    if is_push == 1.0:
        return "push"
    if is_loss == 1.0:
        return "loss"
    hit = _safe_float(row.get("hit"))
    if hit == 1.0:
        return "win"
    if hit == 0.0:
        return "loss"
    return "pending"


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _american_profit_for_unit_stake(value: Any) -> float | None:
    american = _safe_float(value)
    if american is None or american == 0:
        return None
    if american > 0:
        return american / 100.0
    return 100.0 / abs(american)


def _result_lookup(out_dir: Path, runtime_root: Path, prediction_date: str, graded_df: pd.DataFrame | None) -> dict[str, str]:
    frames: list[pd.DataFrame] = []
    if isinstance(graded_df, pd.DataFrame) and not graded_df.empty:
        frames.append(graded_df.copy())
    frames.append(_safe_read_csv(runtime_root / "research" / f"grading_results_{prediction_date}.csv"))
    frames.append(_safe_read_csv(runtime_root / "history" / "result_feedback.csv"))

    lookup: dict[str, str] = {}
    for frame in frames:
        if frame.empty:
            continue
        scoped = frame.copy()
        if "prediction_date" in scoped.columns:
            scoped = scoped[scoped["prediction_date"].astype(str) == str(prediction_date)].copy()
        for _, row in scoped.iterrows():
            result = _normalized_result(row)
            if result not in FINAL_RESULTS:
                continue
            key = _identity_key(row)
            if key.count("|") == 4:
                lookup[key] = result
    return lookup


def _avg_numeric(group: pd.DataFrame, columns: list[str]) -> float | None:
    for column in columns:
        if column in group.columns:
            values = pd.to_numeric(group[column], errors="coerce").dropna()
            if not values.empty:
                return round(float(values.mean()), 6)
    return None


def _caution_value(value: Any) -> str:
    text = _safe_text(value).lower()
    return text if text in CAUTION_SCORE else "insufficient_data"


def _avg_caution(group: pd.DataFrame) -> tuple[float | None, str | None]:
    if group.empty or "context_caution_level" not in group.columns:
        return None, None
    scores = group["context_caution_level"].map(lambda value: CAUTION_SCORE[_caution_value(value)])
    scores = pd.to_numeric(scores, errors="coerce").dropna()
    if scores.empty:
        return None, None
    avg = round(float(scores.mean()), 6)
    nearest = min(CAUTION_SCORE, key=lambda key: abs(CAUTION_SCORE[key] - avg))
    return avg, nearest


def _performance_for_group(group: pd.DataFrame) -> dict[str, Any]:
    if group.empty:
        return {
            "count": 0,
            "graded_count": 0,
            "pending_count": 0,
            "wins": 0,
            "losses": 0,
            "pushes": 0,
            "hit_rate": None,
            "roi": None,
            "avg_edge": None,
            "avg_confidence": None,
            "avg_context_caution_score": None,
            "avg_context_caution_level": None,
            "status": "insufficient_sample",
        }
    wins = int(group["_kelly_perf_result"].eq("win").sum())
    losses = int(group["_kelly_perf_result"].eq("loss").sum())
    pushes = int(group["_kelly_perf_result"].eq("push").sum())
    pending = int(group["_kelly_perf_result"].eq("pending").sum())
    graded = wins + losses + pushes
    hit_denominator = wins + losses
    profit = 0.0
    roi_denominator = 0
    for _, row in group.iterrows():
        result = row.get("_kelly_perf_result")
        if result not in {"win", "loss"}:
            continue
        win_profit = _american_profit_for_unit_stake(row.get("american_odds") if "american_odds" in row.index else row.get("odds"))
        if win_profit is None:
            continue
        roi_denominator += 1
        profit += win_profit if result == "win" else -1.0
    caution_score, caution_level = _avg_caution(group)
    return {
        "count": int(len(group)),
        "graded_count": int(graded),
        "pending_count": int(pending),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": round(wins / hit_denominator, 6) if hit_denominator else None,
        "roi": round(profit / roi_denominator, 6) if roi_denominator else None,
        "avg_edge": _avg_numeric(group, ["side_edge_pct", "edge_pct", "edge"]),
        "avg_confidence": _avg_numeric(group, ["confidence"]),
        "avg_context_caution_score": caution_score,
        "avg_context_caution_level": caution_level,
        "status": "ok" if hit_denominator else "insufficient_sample",
    }


def _group_breakdown(df: pd.DataFrame, column: str) -> dict[str, dict[str, Any]]:
    if df.empty:
        return {}
    if column not in df.columns:
        scoped = df.assign(**{column: "unknown"})
    else:
        scoped = df.copy()
        scoped[column] = scoped[column].fillna("unknown").astype(str).str.strip().replace("", "unknown")
    return {str(key): _performance_for_group(group) for key, group in scoped.groupby(column, sort=True)}


def build_kelly_decision_performance(
    *,
    prediction_date: str,
    out_dir: str | Path = "outputs",
    runtime_root: str | Path | None = None,
    graded_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    out_dir = Path(out_dir)
    runtime_root = Path(runtime_root) if runtime_root is not None else out_dir / "runtime"
    kelly_path = runtime_root / "operator" / f"kelly_stakes_{prediction_date}.csv"
    kelly = _safe_read_csv(kelly_path)
    if kelly.empty:
        return {
            "status": "insufficient_sample",
            "source": str(kelly_path),
            "reason": "missing_or_empty_kelly_stakes",
            "overall": _performance_for_group(pd.DataFrame()),
            "by_kelly_eligible": {},
            "by_skip_reason": {},
            "by_selection_side": {},
            "by_context_caution_level": {},
            "by_context_pick_alignment": {},
        }

    working = kelly.copy()
    if "prediction_date" not in working.columns:
        working["prediction_date"] = prediction_date
    if "eligible" in working.columns:
        working["kelly_eligible"] = working["eligible"].map(_is_truthy)
    else:
        working["kelly_eligible"] = False
    if "skip_reason" not in working.columns:
        working["skip_reason"] = ""
    working["skip_reason"] = working["skip_reason"].fillna("").astype(str).str.strip()
    working.loc[working["skip_reason"].eq(""), "skip_reason"] = "eligible"
    if "selection" not in working.columns:
        working["selection"] = "unknown"
    working["selection_side"] = working["selection"].fillna("unknown").astype(str).str.strip().str.lower().replace("", "unknown")
    if "context_caution_level" not in working.columns:
        working["context_caution_level"] = "insufficient_data"
    working["context_caution_level"] = working["context_caution_level"].map(_caution_value)
    if "context_pick_alignment" not in working.columns:
        working["context_pick_alignment"] = "insufficient_data"
    working["context_pick_alignment"] = working["context_pick_alignment"].fillna("insufficient_data").astype(str).str.strip().str.lower().replace("", "insufficient_data")

    lookup = _result_lookup(out_dir, runtime_root, prediction_date, graded_df)
    working["_kelly_perf_result"] = [lookup.get(_identity_key(row), "pending") for _, row in working.iterrows()]

    overall = _performance_for_group(working)
    by_eligible_bool = {
        str(bool(key)).lower(): _performance_for_group(group)
        for key, group in working.groupby("kelly_eligible", sort=True)
    }
    by_eligible = {
        "true": by_eligible_bool.get("true", _performance_for_group(pd.DataFrame())),
        "false": by_eligible_bool.get("false", _performance_for_group(pd.DataFrame())),
    }
    return {
        "status": overall["status"],
        "source": str(kelly_path),
        "overall": overall,
        "by_kelly_eligible": by_eligible,
        "by_skip_reason": _group_breakdown(working, "skip_reason"),
        "by_selection_side": _group_breakdown(working, "selection_side"),
        "by_context_caution_level": _group_breakdown(working, "context_caution_level"),
        "by_context_pick_alignment": _group_breakdown(working, "context_pick_alignment"),
    }


def format_metric(value: Any) -> str:
    parsed = _safe_float(value)
    return "insufficient_sample" if parsed is None else f"{parsed:.6f}"


def kelly_perf_log_lines(payload: dict[str, Any]) -> list[str]:
    by_eligible = payload.get("by_kelly_eligible", {}) if isinstance(payload, dict) else {}
    eligible = by_eligible.get("true", {}) if isinstance(by_eligible, dict) else {}
    by_skip = payload.get("by_skip_reason", {}) if isinstance(payload, dict) else {}
    high_over = by_skip.get("context_high_caution_over", {}) if isinstance(by_skip, dict) else {}
    return [
        "[kelly_perf] eligible "
        f"graded={int(eligible.get('graded_count', 0) or 0)} "
        f"hit_rate={format_metric(eligible.get('hit_rate'))} "
        f"roi={format_metric(eligible.get('roi'))}",
        "[kelly_perf] skipped_context_high_caution_over "
        f"graded={int(high_over.get('graded_count', 0) or 0)} "
        f"hit_rate={format_metric(high_over.get('hit_rate'))} "
        f"roi={format_metric(high_over.get('roi'))}",
    ]
