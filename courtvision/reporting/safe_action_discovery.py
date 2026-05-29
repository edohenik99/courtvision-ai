from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPORT_FILE_PREFIX = "safe_action_discovery_report"
REPORT_VERSION = "1.0"

MIN_CONCLUSION_SAMPLE = 20
WEAK_SAMPLE_MAX = 49
MODERATE_SAMPLE_MAX = 99
MIN_ACCEPTABLE_HIT_RATE = 0.53
MIN_PROMISING_HIT_RATE = 0.55
MIN_CLV_COVERAGE_FOR_THRESHOLD_REVIEW = 0.50
MIN_FEATURE_COVERAGE_FOR_THRESHOLD_REVIEW = 0.70

KEEP_BLOCKED = "KEEP_BLOCKED"
SHADOW_ONLY = "SHADOW_ONLY"
NEED_MORE_DATA = "NEED_MORE_DATA"
FUTURE_THRESHOLD_REVIEW = "FUTURE_THRESHOLD_REVIEW"
DO_NOT_PROMOTE = "DO_NOT_PROMOTE"

GRADED_STATUSES = {"hit", "miss", "push"}
PENDING_STATUSES = {"", "pending", "open_game_pending", "ungraded", "void", "unsupported"}

HISTORY_SOURCES: tuple[tuple[str, str, str | None], ...] = (
    ("pick_history.csv", "pick_history", None),
    ("incubator_history.csv", "incubator_history", None),
    ("market_shadow_history.csv", "market_shadow_history", "shadow_roi"),
    ("paper_kelly_history.csv", "paper_kelly_history", "paper_roi"),
)

ARTIFACT_SOURCES: tuple[tuple[str, str], ...] = (
    ("full_market_board_*.csv", "full_market_board"),
    ("near_elite_review_*.csv", "near_elite_review"),
    ("incubator_board_*.csv", "incubator_board"),
)

SOURCE_REASON_GROUP_COLS: tuple[str, ...] = (
    "history_source",
    "market_type",
    "selection",
    "context_caution_level",
    "context_edge_label",
    "source_rejection_reason",
)

FULL_GROUP_COLS: tuple[str, ...] = (
    *SOURCE_REASON_GROUP_COLS,
    "confidence_bucket",
    "edge_bucket",
    "quality_bucket",
    "odds_bucket",
)

DIMENSION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("market_type", ("history_source", "market_type")),
    ("selection", ("history_source", "selection")),
    ("context_caution_level", ("history_source", "context_caution_level")),
    ("context_edge_label", ("history_source", "context_edge_label")),
    ("source_rejection_reason", ("history_source", "source_rejection_reason")),
    ("confidence_bucket", ("history_source", "confidence_bucket")),
    ("edge_bucket", ("history_source", "edge_bucket")),
    ("quality_bucket", ("history_source", "quality_bucket")),
    ("odds_bucket", ("history_source", "odds_bucket")),
)

MATRIX_COLUMNS: tuple[str, ...] = (
    "recommendation",
    "bucket_scope",
    "bucket_key",
    "history_source",
    "market_type",
    "selection",
    "context_caution_level",
    "context_edge_label",
    "source_rejection_reason",
    "confidence_bucket",
    "edge_bucket",
    "quality_bucket",
    "odds_bucket",
    "total_rows",
    "graded_rows",
    "pending_rows",
    "hits",
    "misses",
    "pushes",
    "hit_rate",
    "roi",
    "profit_loss",
    "avg_odds",
    "avg_edge",
    "avg_confidence",
    "avg_quality_score",
    "clv_available_rows",
    "clv_coverage_rate",
    "positive_clv_rate",
    "feature_complete_rows",
    "feature_complete_rate",
    "evidence_level",
    "classification",
    "recommendation_reason",
)


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "<na>", "nat"}:
        return default
    return text


def _safe_lower(value: Any, default: str = "unknown") -> str:
    text = _safe_text(value, default=default).lower()
    return text if text else default


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_text(value).lower() in {"true", "1", "yes", "y"}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except (OSError, ValueError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _date_from_artifact_name(path: Path) -> str:
    match = re.search(r"_(\d{4}-\d{2}-\d{2})\.(?:csv|json|txt)$", path.name)
    return match.group(1) if match else ""


def _pct(numerator: int | float, denominator: int | float) -> float | None:
    return round(float(numerator) / float(denominator), 4) if denominator else None


def _odds_profit_factor(odds: Any) -> float:
    value = _safe_float(odds)
    if value is None or abs(value) < 1.0:
        return 100.0 / 110.0
    if value > 0:
        return value / 100.0
    return 100.0 / abs(value)


def _flat_roi_for_result(row: pd.Series) -> float | None:
    status = _safe_lower(row.get("result_status"), default="")
    if status == "hit":
        return _odds_profit_factor(row.get("odds") or row.get("entry_odds"))
    if status == "miss":
        return -1.0
    if status == "push":
        return 0.0
    return None


def _confidence_bucket(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "unknown"
    if number >= 0.85:
        return "0.85+"
    if number >= 0.75:
        return "0.75-0.85"
    if number >= 0.70:
        return "0.70-0.75"
    if number >= 0.60:
        return "0.60-0.70"
    return "<0.60"


def _edge_bucket(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "unknown"
    number = abs(number)
    if number >= 5.0:
        return "5+"
    if number >= 3.0:
        return "3-5"
    if number >= 2.0:
        return "2-3"
    if number >= 1.0:
        return "1-2"
    return "<1"


def _quality_bucket(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "unknown"
    if number >= 80.0:
        return "80+"
    if number >= 70.0:
        return "70-80"
    if number >= 60.0:
        return "60-70"
    if number >= 50.0:
        return "50-60"
    return "<50"


def _odds_bucket(value: Any, fallback: Any = None) -> str:
    existing = _safe_lower(fallback, default="")
    if existing:
        return existing
    odds = _safe_float(value)
    if odds is None:
        return "unknown"
    if odds <= -150:
        return "heavy_fav"
    if odds < -115:
        return "fav"
    if odds <= 115:
        return "near_even"
    if odds <= 150:
        return "plus_small"
    return "plus_large"


def _first_text(row: pd.Series, names: tuple[str, ...], default: str = "") -> str:
    for name in names:
        value = _safe_text(row.get(name))
        if value:
            return value
    return default


def _source_rejection_reason(row: pd.Series, source_name: str) -> str:
    reason = _first_text(
        row,
        (
            "source_rejection_reason",
            "final_elite_rejection_reason",
            "elite_rejection_reason",
            "selection_rejection_reason",
            "kelly_projected_skip_reason",
            "rejection_reason",
            "reason_not_real_kelly",
            "paper_bucket",
            "skip_reason",
            "qualification_reason",
        ),
        default="",
    )
    if not reason and source_name == "paper_kelly_history":
        reason = "paper_kelly"
    if not reason and source_name in {"near_elite_review", "incubator_board"}:
        reason = source_name
    return reason or "unknown"


def _context_edge_label(row: pd.Series) -> str:
    value = _first_text(row, ("context_edge_label", "context_pick_alignment", "context_alignment"), default="")
    return value or "unknown"


def _line_clv(row: pd.Series) -> float | None:
    for column in ("clv_line_points", "clv"):
        value = _safe_float(row.get(column))
        if value is not None:
            return value
    entry = _safe_float(row.get("entry_line") or row.get("line"))
    close = _safe_float(row.get("closing_line_observed") or row.get("closing_line"))
    if entry is None or close is None:
        return None
    selection = _safe_lower(row.get("selection"), default="")
    if selection == "under":
        return entry - close
    return close - entry


def _positive_clv(row: pd.Series, clv_value: float | None) -> bool | None:
    movement = _safe_text(row.get("movement_toward_pick")).lower()
    if movement in {"true", "1", "yes", "toward_pick", "for_pick", "positive"}:
        return True
    if movement in {"false", "0", "no", "against_pick", "negative"}:
        return False
    grade = _safe_text(row.get("clv_grade")).lower()
    if "positive" in grade or grade in {"win", "good"}:
        return True
    if "negative" in grade or grade in {"loss", "bad"}:
        return False
    if clv_value is None:
        return None
    return clv_value > 0


def _feature_complete(row: pd.Series) -> bool:
    has_context = _safe_lower(row.get("context_caution_level"), default="unknown") != "unknown"
    has_alignment = _context_edge_label(row) != "unknown"
    has_fragility = _safe_float(row.get("fragility_score")) is not None or bool(_safe_text(row.get("fragility_bucket")))
    has_survivability = _safe_float(row.get("survivability_score")) is not None or bool(
        _safe_text(row.get("survivability_bucket"))
    )
    return has_context and has_alignment and has_fragility and has_survivability


def _normalize_rows(
    df: pd.DataFrame,
    *,
    history_source: str,
    roi_column: str | None = None,
    default_result_status: str = "",
    artifact_path: Path | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(df, pd.DataFrame) or df.empty:
        return rows
    for _, row in df.iterrows():
        status = _safe_lower(row.get("result_status"), default=default_result_status).lower()
        if not status:
            status = default_result_status
        status = status if status in GRADED_STATUSES else ("pending" if status in PENDING_STATUSES else status)
        roi = _safe_float(row.get(roi_column)) if roi_column else None
        if roi is None:
            roi = _flat_roi_for_result(row)
        clv = _line_clv(row)
        market_type = _safe_lower(row.get("market_type") or row.get("market"), default="unknown")
        selection = _safe_lower(row.get("selection"), default="unknown")
        edge = _safe_float(row.get("directional_edge")) if history_source == "paper_kelly_history" else None
        if edge is None:
            edge = _safe_float(row.get("edge") or row.get("side_edge") or row.get("abs_edge"))
        quality_value = row.get("quality_score") if "quality_score" in row.index else row.get("quality")
        odds_value = row.get("odds") if _safe_text(row.get("odds")) else row.get("entry_odds")
        rows.append(
            {
                "prediction_date": _safe_text(row.get("prediction_date")),
                "history_source": history_source,
                "source_path": str(artifact_path or ""),
                "player_name": _safe_text(row.get("player_name") or row.get("player") or row.get("entity_name")),
                "market_type": market_type,
                "selection": selection,
                "context_caution_level": _safe_lower(row.get("context_caution_level"), default="unknown"),
                "context_edge_label": _safe_lower(_context_edge_label(row), default="unknown"),
                "source_rejection_reason": _safe_lower(_source_rejection_reason(row, history_source), default="unknown"),
                "confidence_bucket": _confidence_bucket(row.get("confidence")),
                "edge_bucket": _edge_bucket(edge),
                "quality_bucket": _quality_bucket(quality_value),
                "odds_bucket": _odds_bucket(odds_value, row.get("odds_bucket")),
                "result_status": status,
                "roi": roi,
                "odds": _safe_float(odds_value),
                "edge": edge,
                "confidence": _safe_float(row.get("confidence")),
                "quality_score": _safe_float(quality_value),
                "clv": clv,
                "positive_clv": _positive_clv(row, clv),
                "feature_complete": _feature_complete(row),
                "real_money_eligible": _safe_bool(row.get("kelly_eligible"))
                or _safe_bool(row.get("real_kelly_eligible"))
                or _safe_bool(row.get("real_money_eligible")),
                "review_lane": _safe_lower(row.get("review_lane"), default=""),
            }
        )
    return rows


def _history_rows(
    *,
    history_root: Path,
    through_date: str | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for filename, source_name, roi_column in HISTORY_SOURCES:
        path = history_root / filename
        df = _read_csv(path)
        if df.empty:
            continue
        if through_date and "prediction_date" in df.columns:
            df = df[df["prediction_date"].astype(str) <= str(through_date)].copy()
        rows.extend(_normalize_rows(df, history_source=source_name, roi_column=roi_column, artifact_path=path))
    return rows


def _artifact_rows(
    *,
    runtime_root: Path,
    through_date: str | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    operator_dir = runtime_root / "operator"
    for pattern, source_name in ARTIFACT_SOURCES:
        for path in sorted(operator_dir.glob(pattern)):
            artifact_date = _date_from_artifact_name(path)
            if through_date and artifact_date and artifact_date > through_date:
                continue
            df = _read_csv(path)
            if df.empty:
                continue
            rows.extend(
                _normalize_rows(
                    df,
                    history_source=source_name,
                    default_result_status="pending",
                    artifact_path=path,
                )
            )
    return rows


def load_discovery_frame(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    include_artifacts: bool = True,
) -> pd.DataFrame:
    runtime_root_path = Path(runtime_root)
    history_root_path = Path(history_root)
    rows = _history_rows(history_root=history_root_path, through_date=prediction_date)
    if include_artifacts:
        rows.extend(_artifact_rows(runtime_root=runtime_root_path, through_date=prediction_date))
    if not rows:
        return pd.DataFrame(columns=["history_source", "result_status", *FULL_GROUP_COLS])
    return pd.DataFrame(rows)


def _evidence_level(graded_rows: int) -> str:
    if graded_rows < MIN_CONCLUSION_SAMPLE:
        return "no_conclusion_lt_20"
    if graded_rows <= WEAK_SAMPLE_MAX:
        return "weak_directional_20_49"
    if graded_rows <= MODERATE_SAMPLE_MAX:
        return "moderate_50_99"
    return "stronger_100_plus"


def _is_high_caution_over(row: dict[str, Any]) -> bool:
    return (
        _safe_lower(row.get("selection"), default="") == "over"
        and _safe_lower(row.get("context_caution_level"), default="") == "high"
    )


def _is_discovery_source(row: dict[str, Any]) -> bool:
    source = _safe_lower(row.get("history_source"), default="")
    if source in {"market_shadow_history", "paper_kelly_history", "incubator_history"}:
        return True
    reason = _safe_lower(row.get("source_rejection_reason"), default="unknown")
    return reason not in {"unknown", "paper_kelly"} and source != "pick_history"


def _classify_bucket(row: dict[str, Any]) -> tuple[str, str, str]:
    graded = int(row.get("graded_rows") or 0)
    hit_rate = row.get("hit_rate")
    roi = row.get("roi")
    clv_coverage = row.get("clv_coverage_rate") or 0.0
    feature_coverage = row.get("feature_complete_rate") or 0.0
    real_money = bool(row.get("real_money_eligible"))
    high_caution_over = _is_high_caution_over(row)

    if row.get("bucket_scope") == "policy_guardrail":
        return DO_NOT_PROMOTE, "policy_guardrail", "Reporting-only audit; no real-money promotion is recommended."
    if real_money and _safe_lower(row.get("history_source"), default="") == "pick_history":
        return DO_NOT_PROMOTE, "observed_real_money_history", "Historical pick bucket, not a blocked-bucket promotion target."
    if graded < MIN_CONCLUSION_SAMPLE:
        return NEED_MORE_DATA, "unproven_small_sample", "Fewer than 20 graded rows; no conclusion."
    if roi is not None and roi < 0:
        return KEEP_BLOCKED, "unsafe_negative_roi", "Negative ROI with enough graded rows."
    if hit_rate is not None and hit_rate < MIN_ACCEPTABLE_HIT_RATE:
        return KEEP_BLOCKED, "unsafe_low_hit_rate", "Hit rate is below the acceptable review floor."
    if high_caution_over:
        return (
            SHADOW_ONLY,
            "promising_but_gate_blocked" if (roi or 0.0) > 0 else "blocked_high_caution_over",
            "High-caution OVER remains blocked; positive signals stay shadow-only.",
        )
    if hit_rate is not None and roi is not None and hit_rate >= MIN_PROMISING_HIT_RATE and roi > 0:
        if (
            graded >= 50
            and clv_coverage >= MIN_CLV_COVERAGE_FOR_THRESHOLD_REVIEW
            and feature_coverage >= MIN_FEATURE_COVERAGE_FOR_THRESHOLD_REVIEW
        ):
            return (
                FUTURE_THRESHOLD_REVIEW,
                "promising_with_moderate_evidence",
                "Positive ROI and acceptable hit rate with enough CLV/feature coverage for future review.",
            )
        return (
            SHADOW_ONLY,
            "promising_shadow_only",
            "Positive ROI and acceptable hit rate, but sample/CLV/feature coverage is not enough for threshold review.",
        )
    if graded <= WEAK_SAMPLE_MAX:
        return NEED_MORE_DATA, "weak_mixed_signal", "20-49 graded rows provide weak directional signal only."
    return NEED_MORE_DATA, "mixed_or_incomplete_signal", "No unsafe result, but evidence is not strong enough for review."


def _mean_numeric(group: pd.DataFrame, column: str) -> float | None:
    values = pd.to_numeric(group.get(column, pd.Series(dtype=float)), errors="coerce").dropna()
    if values.empty:
        return None
    return round(float(values.mean()), 4)


def _aggregate_groups(
    df: pd.DataFrame,
    group_cols: tuple[str, ...],
    *,
    bucket_scope: str,
    include_pending: bool = True,
) -> list[dict[str, Any]]:
    if df.empty:
        return []
    working = df.copy()
    if not include_pending:
        working = working[working["result_status"].isin(GRADED_STATUSES)].copy()
    if working.empty:
        return []
    for column in group_cols:
        if column not in working.columns:
            working[column] = "unknown"
        working[column] = working[column].fillna("").astype(str).replace("", "unknown")

    rows: list[dict[str, Any]] = []
    for group_values, group in working.groupby(list(group_cols), sort=True, dropna=False):
        if not isinstance(group_values, tuple):
            group_values = (group_values,)
        group_map = dict(zip(group_cols, group_values))
        statuses = group["result_status"].fillna("").astype(str).str.lower()
        graded_mask = statuses.isin(GRADED_STATUSES)
        graded = group.loc[graded_mask].copy()
        hits = int((graded["result_status"] == "hit").sum()) if not graded.empty else 0
        misses = int((graded["result_status"] == "miss").sum()) if not graded.empty else 0
        pushes = int((graded["result_status"] == "push").sum()) if not graded.empty else 0
        graded_rows = hits + misses + pushes
        roi_values = pd.to_numeric(graded.get("roi", pd.Series(dtype=float)), errors="coerce").dropna()
        clv_values = pd.to_numeric(graded.get("clv", pd.Series(dtype=float)), errors="coerce").dropna()
        positive_clv_values = graded.get("positive_clv", pd.Series(dtype=object)).dropna()
        feature_complete_rows = int(group.get("feature_complete", pd.Series(dtype=bool)).fillna(False).astype(bool).sum())

        row: dict[str, Any] = {
            "bucket_scope": bucket_scope,
            **{column: group_map.get(column, "all") for column in FULL_GROUP_COLS},
            "total_rows": int(len(group)),
            "graded_rows": int(graded_rows),
            "pending_rows": int(len(group) - graded_rows),
            "hits": hits,
            "misses": misses,
            "pushes": pushes,
            "hit_rate": round(hits / (hits + misses), 4) if (hits + misses) else None,
            "roi": round(float(roi_values.mean()), 4) if len(roi_values) else None,
            "profit_loss": round(float(roi_values.sum()), 4) if len(roi_values) else None,
            "avg_odds": _mean_numeric(group, "odds"),
            "avg_edge": _mean_numeric(group, "edge"),
            "avg_confidence": _mean_numeric(group, "confidence"),
            "avg_quality_score": _mean_numeric(group, "quality_score"),
            "clv_available_rows": int(len(clv_values)),
            "clv_coverage_rate": _pct(len(clv_values), graded_rows),
            "positive_clv_rate": _pct(
                int(positive_clv_values.astype(bool).sum()),
                int(len(positive_clv_values)),
            ),
            "feature_complete_rows": feature_complete_rows,
            "feature_complete_rate": _pct(feature_complete_rows, len(group)),
            "real_money_eligible": bool(group.get("real_money_eligible", pd.Series(dtype=bool)).fillna(False).astype(bool).any()),
            "evidence_level": _evidence_level(graded_rows),
        }
        row["bucket_key"] = _bucket_key(row, group_cols)
        action, classification, reason = _classify_bucket(row)
        row["recommendation"] = action
        row["classification"] = classification
        row["recommendation_reason"] = reason
        rows.append(row)
    return rows


def _bucket_key(row: dict[str, Any], group_cols: tuple[str, ...]) -> str:
    return " | ".join(f"{column}={row.get(column, 'all')}" for column in group_cols)


def _sort_best_by_hit_rate(row: dict[str, Any]) -> tuple[int, float, float, int]:
    hit_rate = row.get("hit_rate")
    roi = row.get("roi")
    return (
        -(float(hit_rate) if hit_rate is not None else -1.0),
        -(float(roi) if roi is not None else -99.0),
        -int(row.get("graded_rows") or 0),
        -int(row.get("hits") or 0),
    )


def _sort_best_by_roi(row: dict[str, Any]) -> tuple[int, float, float]:
    roi = row.get("roi")
    hit_rate = row.get("hit_rate")
    return (
        -(float(roi) if roi is not None else -99.0),
        -(float(hit_rate) if hit_rate is not None else -1.0),
        -int(row.get("graded_rows") or 0),
    )


def _sort_worst_by_roi(row: dict[str, Any]) -> tuple[int, float, float]:
    roi = row.get("roi")
    hit_rate = row.get("hit_rate")
    return (
        float(roi) if roi is not None else 99.0,
        float(hit_rate) if hit_rate is not None else 99.0,
        -int(row.get("graded_rows") or 0),
    )


def _history_only(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["history_source"].isin({source for _filename, source, _roi in HISTORY_SOURCES})].copy()


def _blocked_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    reason = df["source_rejection_reason"].fillna("").astype(str).str.lower()
    source = df["history_source"].fillna("").astype(str).str.lower()
    blocked = reason.ne("unknown") & ~reason.isin({"paper_kelly"})
    blocked = blocked | source.isin({"market_shadow_history", "paper_kelly_history", "incubator_history"})
    return df[blocked].copy()


def _near_elite_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    source = df["history_source"].fillna("").astype(str).str.lower()
    lane = df.get("review_lane", pd.Series("", index=df.index)).fillna("").astype(str).str.lower()
    return df[(source == "near_elite_review") | (lane == "near_elite")].copy()


def _incubator_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    source = df["history_source"].fillna("").astype(str).str.lower()
    return df[source.isin({"incubator_history", "incubator_board"})].copy()


def _high_caution_over_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    selection = df["selection"].fillna("").astype(str).str.lower()
    caution = df["context_caution_level"].fillna("").astype(str).str.lower()
    return df[(selection == "over") & (caution == "high")].copy()


def _low_caution_or_context_aligned_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    selection = df["selection"].fillna("").astype(str).str.lower()
    caution = df["context_caution_level"].fillna("").astype(str).str.lower()
    label = df["context_edge_label"].fillna("").astype(str).str.lower()
    aligned = label.eq("aligned")
    aligned = aligned | ((selection == "over") & label.isin({"supports_over", "over_aligned"}))
    aligned = aligned | ((selection == "under") & label.isin({"supports_under", "under_aligned"}))
    return df[(caution == "low") | aligned].copy()


def _recommendation_rank(row: dict[str, Any]) -> tuple[int, int, float, float]:
    action_rank = {
        FUTURE_THRESHOLD_REVIEW: 0,
        SHADOW_ONLY: 1,
        NEED_MORE_DATA: 2,
        KEEP_BLOCKED: 3,
        DO_NOT_PROMOTE: 4,
    }.get(str(row.get("recommendation")), 9)
    roi = row.get("roi")
    hit_rate = row.get("hit_rate")
    return (
        action_rank,
        -int(row.get("graded_rows") or 0),
        -(float(roi) if roi is not None else -99.0),
        -(float(hit_rate) if hit_rate is not None else -1.0),
    )


def _top_rows(rows: list[dict[str, Any]], *, limit: int = 12, min_graded: int = MIN_CONCLUSION_SAMPLE) -> list[dict[str, Any]]:
    return [row for row in rows if int(row.get("graded_rows") or 0) >= min_graded][:limit]


def _sample_size_warnings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    warnings = []
    for row in rows:
        graded = int(row.get("graded_rows") or 0)
        if graded < MIN_CONCLUSION_SAMPLE:
            warnings.append(
                {
                    "bucket_key": row["bucket_key"],
                    "history_source": row.get("history_source", "all"),
                    "graded_rows": graded,
                    "warning": "no_conclusion_lt_20",
                }
            )
        elif graded <= WEAK_SAMPLE_MAX:
            warnings.append(
                {
                    "bucket_key": row["bucket_key"],
                    "history_source": row.get("history_source", "all"),
                    "graded_rows": graded,
                    "warning": "weak_directional_signal_only",
                }
            )
    warnings.sort(key=lambda item: (item["graded_rows"], item["bucket_key"]))
    return warnings[:25]


def _clv_warnings(rows: list[dict[str, Any]], *, feature_payload: dict[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for row in rows:
        graded = int(row.get("graded_rows") or 0)
        if graded < MIN_CONCLUSION_SAMPLE:
            continue
        coverage = row.get("clv_coverage_rate")
        if coverage is None or float(coverage) < MIN_CLV_COVERAGE_FOR_THRESHOLD_REVIEW:
            warnings.append(
                {
                    "bucket_key": row["bucket_key"],
                    "history_source": row.get("history_source", "all"),
                    "graded_rows": graded,
                    "clv_coverage_rate": coverage,
                    "warning": "insufficient_clv_coverage_for_threshold_review",
                }
            )
    readiness = feature_payload.get("readiness") if isinstance(feature_payload.get("readiness"), dict) else {}
    verdict = _safe_text(readiness.get("verdict"))
    if verdict and verdict != "READY":
        warnings.insert(
            0,
            {
                "bucket_key": "feature_completeness_tracker",
                "history_source": "supporting_artifact",
                "graded_rows": readiness.get("minimum_required_graded_rows", ""),
                "clv_coverage_rate": None,
                "warning": f"feature_completeness_verdict={verdict}",
            },
        )
    return warnings[:25]


def _latest_artifact_payload(
    *,
    runtime_root: Path,
    prediction_date: str,
    stem: str,
    subdir: str = "diagnostics",
) -> dict[str, Any]:
    directory = runtime_root / subdir
    candidates = []
    for path in directory.glob(f"{stem}_*.json"):
        artifact_date = _date_from_artifact_name(path)
        if artifact_date and artifact_date <= prediction_date:
            candidates.append((artifact_date, path))
    if not candidates:
        return {"path": "", "payload": {}}
    candidates.sort(key=lambda item: item[0])
    latest_date, latest_path = candidates[-1]
    return {"date": latest_date, "path": str(latest_path), "payload": _read_json(latest_path)}


def _supporting_artifacts(runtime_root: Path, prediction_date: str) -> dict[str, Any]:
    artifacts = {
        "no_bet_funnel_report": _latest_artifact_payload(
            runtime_root=runtime_root,
            prediction_date=prediction_date,
            stem="no_bet_funnel_report",
        ),
        "calibration_bucket_report": _latest_artifact_payload(
            runtime_root=runtime_root,
            prediction_date=prediction_date,
            stem="calibration_bucket_report",
        ),
        "meta_label_promotion_shadow": _latest_artifact_payload(
            runtime_root=runtime_root,
            prediction_date=prediction_date,
            stem="meta_label_promotion_shadow",
        ),
        "feature_completeness_tracker": _latest_artifact_payload(
            runtime_root=runtime_root,
            prediction_date=prediction_date,
            stem="feature_completeness_tracker",
        ),
    }
    summary: dict[str, Any] = {}
    for name, item in artifacts.items():
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        summary[name] = {
            "date": item.get("date", ""),
            "path": item.get("path", ""),
            "summary": payload.get("summary", {}),
            "readiness": payload.get("readiness", {}),
        }
        if name == "no_bet_funnel_report":
            summary[name]["aggregate"] = payload.get("aggregate", {})
            summary[name]["high_caution_over_analysis"] = payload.get("high_caution_over_analysis", {})
            summary[name]["no_bet_streak"] = payload.get("no_bet_streak", {})
    return summary


def _source_inventory(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    rows = []
    for source, group in df.groupby("history_source", sort=True):
        graded = int(group["result_status"].isin(GRADED_STATUSES).sum())
        rows.append(
            {
                "history_source": source,
                "total_rows": int(len(group)),
                "graded_rows": graded,
                "pending_rows": int(len(group) - graded),
            }
        )
    return rows


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, pd.DataFrame):
        return [_json_ready(row) for row in value.to_dict("records")]
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (int, str, bool)) or value is None:
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _matrix_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=MATRIX_COLUMNS)
    return pd.DataFrame(rows).reindex(columns=MATRIX_COLUMNS)


def build_safe_action_discovery_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    generated_at_utc: str | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    runtime_root_path = Path(runtime_root)
    history_root_path = Path(history_root)
    df = load_discovery_frame(
        prediction_date=prediction_date,
        runtime_root=runtime_root_path,
        history_root=history_root_path,
        include_artifacts=True,
    )
    history_df = _history_only(df)
    blocked_df = _blocked_rows(history_df)
    near_elite_df = _near_elite_rows(df)
    incubator_df = _incubator_rows(df)
    high_caution_over_df = _high_caution_over_rows(history_df)
    aligned_df = _low_caution_or_context_aligned_rows(history_df)

    source_reason_rows = _aggregate_groups(
        history_df,
        SOURCE_REASON_GROUP_COLS,
        bucket_scope="source_reason",
        include_pending=True,
    )
    full_dimension_rows = _aggregate_groups(
        history_df,
        FULL_GROUP_COLS,
        bucket_scope="full_dimension",
        include_pending=True,
    )
    blocked_rows = _aggregate_groups(
        blocked_df,
        SOURCE_REASON_GROUP_COLS,
        bucket_scope="blocked_bucket",
        include_pending=True,
    )
    near_elite_rows = _aggregate_groups(
        near_elite_df,
        SOURCE_REASON_GROUP_COLS,
        bucket_scope="near_elite_bucket",
        include_pending=True,
    )
    incubator_rows = _aggregate_groups(
        incubator_df,
        SOURCE_REASON_GROUP_COLS,
        bucket_scope="incubator_bucket",
        include_pending=True,
    )
    selection_rows = _aggregate_groups(
        history_df,
        ("selection",),
        bucket_scope="selection",
        include_pending=True,
    )
    high_caution_over_rows = _aggregate_groups(
        high_caution_over_df,
        SOURCE_REASON_GROUP_COLS,
        bucket_scope="high_caution_over",
        include_pending=True,
    )
    aligned_rows = _aggregate_groups(
        aligned_df,
        SOURCE_REASON_GROUP_COLS,
        bucket_scope="low_caution_or_context_aligned",
        include_pending=True,
    )

    performance_by_dimension = {
        dimension: _aggregate_groups(history_df, group_cols, bucket_scope=f"by_{dimension}", include_pending=True)
        for dimension, group_cols in DIMENSION_GROUPS
    }

    source_reason_eligible = _top_rows(
        source_reason_rows,
        limit=len(source_reason_rows),
        min_graded=MIN_CONCLUSION_SAMPLE,
    )
    best_by_hit_rate = sorted(source_reason_eligible, key=_sort_best_by_hit_rate)[:12]
    best_by_roi = sorted(source_reason_eligible, key=_sort_best_by_roi)[:12]
    worst_by_roi = sorted(
        _top_rows(source_reason_rows, limit=len(source_reason_rows), min_graded=MIN_CONCLUSION_SAMPLE),
        key=_sort_worst_by_roi,
    )[:12]
    worst_by_hit_rate = sorted(
        _top_rows(source_reason_rows, limit=len(source_reason_rows), min_graded=MIN_CONCLUSION_SAMPLE),
        key=lambda row: (
            float(row.get("hit_rate")) if row.get("hit_rate") is not None else 99.0,
            float(row.get("roi")) if row.get("roi") is not None else 99.0,
            -int(row.get("graded_rows") or 0),
        ),
    )[:12]

    promising_candidates = [
        row
        for row in sorted(blocked_rows, key=_recommendation_rank)
        if row["recommendation"] in {SHADOW_ONLY, FUTURE_THRESHOLD_REVIEW}
        and _is_discovery_source(row)
    ][:12]
    remain_blocked = [
        row
        for row in sorted(blocked_rows, key=lambda item: (-int(item.get("graded_rows") or 0), float(item.get("roi") or 99.0)))
        if row["recommendation"] == KEEP_BLOCKED
    ][:12]
    need_more_samples = [
        row
        for row in sorted(blocked_rows, key=lambda item: (-int(item.get("graded_rows") or 0), item["bucket_key"]))
        if row["recommendation"] == NEED_MORE_DATA
    ][:12]

    supporting = _supporting_artifacts(runtime_root_path, prediction_date)
    feature_payload = supporting.get("feature_completeness_tracker", {})
    feature_payload = feature_payload if isinstance(feature_payload, dict) else {}
    feature_payload = {"readiness": feature_payload.get("readiness", {})}

    policy_row = {
        "recommendation": DO_NOT_PROMOTE,
        "bucket_scope": "policy_guardrail",
        "bucket_key": "all_discovery_buckets",
        "history_source": "all",
        "market_type": "all",
        "selection": "all",
        "context_caution_level": "all",
        "context_edge_label": "all",
        "source_rejection_reason": "all",
        "confidence_bucket": "all",
        "edge_bucket": "all",
        "quality_bucket": "all",
        "odds_bucket": "all",
        "total_rows": int(len(df)),
        "graded_rows": int(df["result_status"].isin(GRADED_STATUSES).sum()) if not df.empty else 0,
        "pending_rows": int((~df["result_status"].isin(GRADED_STATUSES)).sum()) if not df.empty else 0,
        "hits": 0,
        "misses": 0,
        "pushes": 0,
        "hit_rate": None,
        "roi": None,
        "profit_loss": None,
        "avg_odds": None,
        "avg_edge": None,
        "avg_confidence": None,
        "avg_quality_score": None,
        "clv_available_rows": 0,
        "clv_coverage_rate": None,
        "positive_clv_rate": None,
        "feature_complete_rows": 0,
        "feature_complete_rate": None,
        "evidence_level": "policy",
        "classification": "policy_guardrail",
        "recommendation_reason": "Reporting-only audit; no real-money promotion is recommended.",
    }

    matrix_rows = [
        policy_row,
        *source_reason_rows,
        *full_dimension_rows,
        *blocked_rows,
        *near_elite_rows,
        *incubator_rows,
        *high_caution_over_rows,
        *aligned_rows,
    ]
    matrix_rows.sort(key=_recommendation_rank)
    matrix_df = _matrix_dataframe(matrix_rows)

    recommendation_counts = Counter(row["recommendation"] for row in matrix_rows)
    graded_total = int(history_df["result_status"].isin(GRADED_STATUSES).sum()) if not history_df.empty else 0
    high_caution_summary = _aggregate_groups(
        high_caution_over_df,
        ("selection", "context_caution_level"),
        bucket_scope="high_caution_over_summary",
        include_pending=True,
    )

    payload = {
        "report_name": REPORT_FILE_PREFIX,
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "betting_logic_changed": False,
        "real_money_promotion_recommended": False,
        "minimum_sample_rules": {
            "lt_20": "no conclusion",
            "20_49": "weak directional signal only",
            "50_99": "moderate evidence",
            "100_plus": "stronger evidence",
            "promotion_policy": "No real-money promotion is recommended by this report.",
        },
        "source_inventory": _source_inventory(df),
        "summary": {
            "total_rows_loaded": int(len(df)),
            "historical_rows_loaded": int(len(history_df)),
            "historical_graded_rows": graded_total,
            "blocked_historical_rows": int(len(blocked_df)),
            "near_elite_artifact_rows": int(len(near_elite_df)),
            "incubator_rows": int(len(incubator_df)),
            "high_caution_over_historical_rows": int(len(high_caution_over_df)),
            "recommendation_counts": dict(sorted(recommendation_counts.items())),
        },
        "best_historical_buckets_by_hit_rate": best_by_hit_rate,
        "best_historical_buckets_by_roi": best_by_roi,
        "worst_historical_buckets_by_hit_rate": worst_by_hit_rate,
        "worst_historical_buckets_by_roi": worst_by_roi,
        "blocked_bucket_performance": sorted(blocked_rows, key=_recommendation_rank)[:25],
        "near_elite_bucket_performance": sorted(near_elite_rows, key=_recommendation_rank)[:25],
        "incubator_bucket_performance": sorted(incubator_rows, key=_recommendation_rank)[:25],
        "under_vs_over_comparison": sorted(selection_rows, key=lambda row: str(row.get("selection"))),
        "high_caution_over_performance": sorted(high_caution_over_rows, key=_recommendation_rank)[:25],
        "high_caution_over_summary": high_caution_summary,
        "low_caution_or_context_aligned_performance": sorted(aligned_rows, key=_recommendation_rank)[:25],
        "sample_size_warnings": _sample_size_warnings(source_reason_rows),
        "clv_availability_warnings": _clv_warnings(source_reason_rows, feature_payload=feature_payload),
        "potential_safe_action_discovery_candidates": promising_candidates,
        "buckets_that_should_remain_blocked": remain_blocked,
        "buckets_that_need_more_samples": need_more_samples,
        "recommendation_matrix": matrix_df.to_dict("records"),
        "performance_by_dimension": performance_by_dimension,
        "supporting_artifacts": supporting,
        "guardrails": [
            "Reporting-only audit.",
            "No Elite logic changed.",
            "No Kelly logic changed.",
            "No final_decision logic changed.",
            "No staking or bankroll logic changed.",
            "No high-caution OVER gate loosened.",
            "No Incubator promotion.",
            "No blocked rows moved into pick_history.",
            "No closed-slate boards regenerated.",
        ],
        "source_paths": {
            "runtime_root": str(runtime_root_path),
            "history_root": str(history_root_path),
        },
    }
    return _json_ready(payload), matrix_df


def _fmt_pct(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.1f}%"


def _fmt_roi(value: Any) -> str:
    return _fmt_pct(value)


def _bucket_line(row: dict[str, Any]) -> str:
    return (
        f"- {row.get('history_source', 'all')}: {row.get('market_type', 'all')}/"
        f"{row.get('selection', 'all')}/{row.get('context_caution_level', 'all')}/"
        f"{row.get('context_edge_label', 'all')} reason={row.get('source_rejection_reason', 'all')} "
        f"n={row.get('graded_rows', 0)} hit={_fmt_pct(row.get('hit_rate'))} "
        f"roi={_fmt_roi(row.get('roi'))} action={row.get('recommendation', '')}"
    )


def _append_rows(lines: list[str], rows: list[dict[str, Any]], *, empty: str = "- none") -> None:
    if not rows:
        lines.append(empty)
        return
    for row in rows:
        lines.append(_bucket_line(row))


def render_safe_action_discovery_text(payload: dict[str, Any], csv_path: Path) -> str:
    summary = payload["summary"]
    supporting = payload.get("supporting_artifacts", {})
    no_bet = supporting.get("no_bet_funnel_report", {}) if isinstance(supporting, dict) else {}
    no_bet_aggregate = no_bet.get("aggregate", {}) if isinstance(no_bet.get("aggregate"), dict) else {}
    lines: list[str] = [
        f"CourtVision Safe Action Discovery Audit - {payload['prediction_date']}",
        "=" * 78,
        "Reporting-only diagnostic. No Elite, Kelly, final_decision, bankroll, staking, or gate logic changed.",
        f"CSV recommendation matrix: {csv_path}",
        "",
        "1. Executive Summary",
        "-" * 78,
        f"- historical graded rows audited: {summary['historical_graded_rows']}",
        f"- blocked historical rows loaded: {summary['blocked_historical_rows']}",
        f"- near-elite artifact rows loaded: {summary['near_elite_artifact_rows']}",
        f"- incubator rows loaded: {summary['incubator_rows']}",
        f"- high-caution OVER historical rows loaded: {summary['high_caution_over_historical_rows']}",
        f"- recommendation counts: {summary['recommendation_counts']}",
        f"- Phase 5A full-market candidates: {no_bet_aggregate.get('total_full_market_candidates', 'n/a')}",
        f"- Phase 5A high-caution OVER blocks: {no_bet_aggregate.get('total_high_caution_over_blocks', 'n/a')}",
        "- real-money promotion recommended: False",
        "",
        "2. Best Historical Buckets By Hit Rate And ROI",
        "-" * 78,
        "By hit rate:",
    ]
    _append_rows(lines, payload["best_historical_buckets_by_hit_rate"], empty="- none with >=20 graded rows")
    lines.append("By ROI:")
    _append_rows(lines, payload["best_historical_buckets_by_roi"], empty="- none with >=20 graded rows")

    lines.extend(["", "3. Worst Historical Buckets By Hit Rate And ROI", "-" * 78, "By hit rate:"])
    _append_rows(lines, payload["worst_historical_buckets_by_hit_rate"], empty="- none with >=20 graded rows")
    lines.append("By ROI:")
    _append_rows(lines, payload["worst_historical_buckets_by_roi"], empty="- none with >=20 graded rows")

    lines.extend(["", "4. Blocked Bucket Performance", "-" * 78])
    _append_rows(lines, payload["blocked_bucket_performance"], empty="- no blocked history found")

    lines.extend(["", "5. Near-Elite Bucket Performance", "-" * 78])
    if payload["near_elite_bucket_performance"]:
        _append_rows(lines, payload["near_elite_bucket_performance"])
    else:
        lines.append("- no near-elite review artifacts found")
    lines.append("- near-elite rows are review-only and not staking inputs.")

    lines.extend(["", "6. Incubator Bucket Performance", "-" * 78])
    _append_rows(lines, payload["incubator_bucket_performance"], empty="- no incubator rows found")
    lines.append("- incubator rows remain paper-only.")

    lines.extend(["", "7. UNDER vs OVER Comparison", "-" * 78])
    _append_rows(lines, payload["under_vs_over_comparison"], empty="- no graded selection history found")

    lines.extend(["", "8. High-Caution OVER Performance", "-" * 78])
    _append_rows(lines, payload["high_caution_over_performance"], empty="- no high-caution OVER history found")
    lines.append("- high-caution OVER gates remain strict; positive signals are shadow-only.")

    lines.extend(["", "9. Low-Caution Or Context-Aligned Bucket Performance", "-" * 78])
    _append_rows(lines, payload["low_caution_or_context_aligned_performance"], empty="- no matching history found")

    lines.extend(["", "10. Sample-Size Warnings", "-" * 78])
    warnings = payload["sample_size_warnings"]
    if warnings:
        for warning in warnings[:12]:
            lines.append(
                f"- {warning['history_source']}: {warning['bucket_key']} "
                f"graded={warning['graded_rows']} warning={warning['warning']}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "11. CLV Availability Warnings", "-" * 78])
    clv_warnings = payload["clv_availability_warnings"]
    if clv_warnings:
        for warning in clv_warnings[:12]:
            lines.append(
                f"- {warning['history_source']}: {warning['bucket_key']} "
                f"graded={warning['graded_rows']} clv_coverage={_fmt_pct(warning['clv_coverage_rate'])} "
                f"warning={warning['warning']}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "12. Potential Safe-Action Discovery Candidates", "-" * 78])
    _append_rows(lines, payload["potential_safe_action_discovery_candidates"], empty="- none")
    lines.append("- candidates are shadow-only; this report recommends no promotion.")

    lines.extend(["", "13. Buckets That Should Remain Blocked", "-" * 78])
    _append_rows(lines, payload["buckets_that_should_remain_blocked"], empty="- none")

    lines.extend(["", "14. Buckets That Need More Samples", "-" * 78])
    _append_rows(lines, payload["buckets_that_need_more_samples"], empty="- none")

    lines.extend(["", "15. Recommendation Matrix", "-" * 78])
    for action in (KEEP_BLOCKED, SHADOW_ONLY, NEED_MORE_DATA, FUTURE_THRESHOLD_REVIEW, DO_NOT_PROMOTE):
        lines.append(f"- {action}: {summary['recommendation_counts'].get(action, 0)} row(s)")

    lines.extend(["", "Guardrails", "-" * 78])
    lines.extend(f"- {item}" for item in payload["guardrails"])
    return "\n".join(lines) + "\n"


def report_paths_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path, Path]:
    runtime_root_path = Path(runtime_root)
    stem = f"{REPORT_FILE_PREFIX}_{prediction_date}"
    return (
        runtime_root_path / "operator" / f"{stem}.txt",
        runtime_root_path / "diagnostics" / f"{stem}.json",
        runtime_root_path / "operator" / f"{stem}.csv",
    )


def write_safe_action_discovery_report_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
) -> tuple[Path, Path, Path, dict[str, Any]]:
    text_path, json_path, csv_path = report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    payload, matrix_df = build_safe_action_discovery_report(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    text = render_safe_action_discovery_text(payload, csv_path)

    text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    text_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    matrix_df.to_csv(csv_path, index=False)
    return text_path, json_path, csv_path, payload


__all__ = [
    "DO_NOT_PROMOTE",
    "FUTURE_THRESHOLD_REVIEW",
    "KEEP_BLOCKED",
    "NEED_MORE_DATA",
    "REPORT_FILE_PREFIX",
    "SHADOW_ONLY",
    "build_safe_action_discovery_report",
    "load_discovery_frame",
    "render_safe_action_discovery_text",
    "report_paths_for_date",
    "write_safe_action_discovery_report_outputs",
]
