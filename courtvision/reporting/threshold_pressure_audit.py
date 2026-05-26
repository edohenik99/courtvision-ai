"""Shadow-only threshold pressure and profitability audit.

This report studies rejected/watchlist buckets after odds and outcomes. It is
post-hoc reporting only and does not change prediction logic, Elite gates,
Kelly sizing, bankroll logic, or final decisions.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from courtvision.reporting.combo_under_watchlist import COMBO_UNDER_MARKETS
from courtvision.reporting.near_elite_review import MIN_CONFIDENCE, MIN_EDGE, MIN_QUALITY_SCORE
from courtvision.reporting.shadow_artifact_metadata import apply_shadow_report_metadata


REPORT_VERSION = "1.0"
REPORT_NAME = "threshold_pressure_audit"
GENERATED_BY = "courtvision.reporting.threshold_pressure_audit.write_threshold_pressure_audit"
DISCLAIMER = "Threshold Pressure Audit is shadow-only and is not an Elite/Kelly input."
READINESS_NOTE = "This report is not a model. It only identifies threshold pressure for future review."

CSV_FIELDS: tuple[str, ...] = (
    "breakdown",
    "bucket",
    "total_rows",
    "graded_rows",
    "hit_count",
    "miss_count",
    "push_count",
    "void_count",
    "pending_count",
    "hit_rate",
    "average_odds",
    "average_breakeven_probability",
    "average_edge",
    "average_confidence",
    "average_quality_score",
    "unit_profit",
    "roi_percentage",
    "sample_size_status",
    "pressure_status",
)

BREAKDOWN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("by_rejection_reason", "rejection_reason"),
    ("by_recommended_action", "recommended_action"),
    ("by_market_type", "market_type"),
    ("by_side", "side"),
    ("by_edge_bucket", "edge_bucket"),
    ("by_confidence_bucket", "confidence_bucket"),
    ("by_quality_score_bucket", "quality_score_bucket"),
    ("by_meta_label_bucket", "meta_label_bucket"),
    ("by_role_stability_bucket", "role_stability_bucket"),
    ("by_fragility_bucket", "fragility_bucket"),
    ("by_survivability_bucket", "survivability_bucket"),
)

MISSING_TEXT = {"", "nan", "none", "null", "<na>"}
VOID_STATUSES = {"void", "cancelled", "canceled"}
HIGH_CAUTION_REASONS = {"elite_reject_context_high_caution_over", "context_high_caution_over"}


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return default if text.lower() in MISSING_TEXT else text


def _safe_float(value: Any) -> float | None:
    text = _safe_text(value)
    if not text:
        return None
    try:
        number = float(text.replace(",", ""))
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_text(value).lower() in {"true", "1", "yes", "y"}


def american_breakeven_probability(odds: Any) -> float | None:
    number = _safe_float(odds)
    if number is None or abs(number) < 1e-9:
        return None
    if number < 0:
        return round(abs(number) / (abs(number) + 100.0), 6)
    return round(100.0 / (number + 100.0), 6)


def unit_profit_for_status(result_status: Any, odds: Any) -> float | None:
    status = _safe_text(result_status).lower() or "pending"
    number = _safe_float(odds)
    if status == "hit":
        if number is None or abs(number) < 1e-9:
            return None
        if number > 0:
            return round(number / 100.0, 6)
        return round(100.0 / abs(number), 6)
    if status == "miss":
        return -1.0
    if status == "push":
        return 0.0
    return None


american_odds_breakeven_probability = american_breakeven_probability
unit_profit_for_result = unit_profit_for_status


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _line_token(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return _safe_text(value).lower()
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _row_identity(row: pd.Series | dict[str, Any]) -> str:
    return (
        _safe_text(row.get("player_id")).lower()
        or _safe_text(row.get("entity_id")).lower()
        or _safe_text(row.get("player_name")).lower()
        or _safe_text(row.get("entity_name")).lower()
    )


def _line_value(row: pd.Series | dict[str, Any]) -> Any:
    for column in ("line", "sportsbook_line", "entry_line"):
        value = row.get(column)
        if _safe_text(value):
            return value
    return ""


def _row_key(row: pd.Series | dict[str, Any]) -> str:
    return "|".join(
        [
            _safe_text(row.get("prediction_date")),
            _row_identity(row),
            _safe_text(row.get("market_type")).lower(),
            _safe_text(row.get("selection")).lower(),
            _line_token(_line_value(row)),
        ]
    )


def _with_join_key(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    if working.empty:
        working["_join_key"] = pd.Series(dtype=str)
        return working
    for column in ("prediction_date", "player_id", "player_name", "market_type", "selection", "line"):
        if column not in working.columns:
            working[column] = ""
    working["_join_key"] = working.apply(_row_key, axis=1)
    return working


def _append_current_board_rows(history_df: pd.DataFrame, full_market_df: pd.DataFrame, prediction_date: str) -> pd.DataFrame:
    if full_market_df.empty:
        return history_df.copy()
    history = _with_join_key(history_df)
    board = full_market_df.copy()
    if "prediction_date" not in board.columns:
        board["prediction_date"] = prediction_date
    board = board[board["prediction_date"].fillna("").astype(str).eq(str(prediction_date))].copy()
    if board.empty:
        return history.drop(columns=["_join_key"], errors="ignore")
    if "result_status" not in board.columns:
        board["result_status"] = "pending"
    else:
        board["result_status"] = board["result_status"].map(lambda value: _safe_text(value, "pending") or "pending")
    board = _with_join_key(board)
    existing_keys = set(history["_join_key"].astype(str)) if "_join_key" in history.columns else set()
    missing = board[~board["_join_key"].astype(str).isin(existing_keys)].copy()
    if missing.empty:
        return history.drop(columns=["_join_key"], errors="ignore")
    missing["source_artifact"] = "full_market_board_current_date"
    combined = pd.concat([history, missing], ignore_index=True, sort=False)
    return combined.drop(columns=["_join_key"], errors="ignore")


def _merge_optional_columns(base_df: pd.DataFrame, optional_df: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    if base_df.empty or optional_df.empty:
        return base_df.copy()
    base = _with_join_key(base_df)
    optional = _with_join_key(optional_df)
    optional = optional.drop_duplicates(subset=["_join_key"], keep="last")
    for column in columns:
        if column not in optional.columns:
            continue
        values = optional.set_index("_join_key")[column]
        incoming = base["_join_key"].map(values)
        if column not in base.columns:
            base[column] = incoming
            continue
        missing_mask = base[column].map(_safe_text).eq("")
        base.loc[missing_mask, column] = incoming.loc[missing_mask]
    return base.drop(columns=["_join_key"], errors="ignore")


def _filter_history_through_date(df: pd.DataFrame, prediction_date: str) -> pd.DataFrame:
    if df.empty or "prediction_date" not in df.columns:
        return df.copy()
    dates = df["prediction_date"].fillna("").astype(str).str.strip()
    return df[dates.le(str(prediction_date))].copy()


def _edge_bucket(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "unknown"
    if number <= -5:
        return "<=-5"
    if number <= -3:
        return "-5_to_-3"
    if number <= -1:
        return "-3_to_-1"
    if number < 1:
        return "-1_to_1"
    if number < 3:
        return "1_to_3"
    if number < 5:
        return "3_to_5"
    return "5_plus"


def _confidence_bucket(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "unknown"
    if number > 1 and number <= 100:
        number /= 100.0
    if number < 0.55:
        return "<0.55"
    if number < 0.60:
        return "0.55_to_0.60"
    if number < 0.70:
        return "0.60_to_0.70"
    if number < 0.80:
        return "0.70_to_0.80"
    return "0.80_plus"


def _quality_bucket(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "unknown"
    if number < 40:
        return "<40"
    if number < 48:
        return "40_to_48"
    if number < 55:
        return "48_to_55"
    if number < 65:
        return "55_to_65"
    return "65_plus"


def _status_series(df: pd.DataFrame) -> pd.Series:
    if "result_status" not in df.columns:
        return pd.Series("pending", index=df.index)
    statuses = df["result_status"].fillna("").astype(str).str.strip().str.lower()
    return statuses.where(~statuses.isin(MISSING_TEXT), "pending")


def _odds_value(row: pd.Series | dict[str, Any]) -> Any:
    for column in ("odds", "entry_odds", "american_odds"):
        value = row.get(column)
        if _safe_text(value):
            return value
    return ""


def _is_high_caution_over_reject(row: pd.Series) -> bool:
    reason = _safe_text(row.get("final_elite_rejection_reason")).lower()
    skip_reason = _safe_text(row.get("kelly_projected_skip_reason")).lower()
    return reason in HIGH_CAUTION_REASONS or skip_reason in HIGH_CAUTION_REASONS


def _is_combo_under_watchlist(row: pd.Series) -> bool:
    market = _safe_text(row.get("market_type")).lower()
    selection = _safe_text(row.get("selection")).lower()
    alignment = _safe_text(row.get("context_pick_alignment")).lower()
    caution = _safe_text(row.get("context_caution_level")).lower()
    return market in COMBO_UNDER_MARKETS and selection == "under" and alignment == "aligned" and caution == "low"


def _source_rejection_reason(row: pd.Series) -> str:
    for column in (
        "final_elite_rejection_reason",
        "elite_rejection_reason",
        "selection_rejection_reason",
        "rejection_reason",
        "kelly_projected_skip_reason",
        "pre_rejection_reason",
    ):
        value = _safe_text(row.get(column))
        if value:
            return value
    return ""


def _is_near_elite_rejected_candidate(row: pd.Series) -> bool:
    if _safe_text(row.get("review_lane")).lower() == "near_elite":
        return True
    market = _safe_text(row.get("market_type")).lower()
    selection = _safe_text(row.get("selection")).lower()
    edge = _safe_float(row.get("edge"))
    confidence = _safe_float(row.get("confidence"))
    quality = _safe_float(row.get("quality_score"))
    return (
        market == "player_points"
        and selection == "over"
        and edge is not None
        and confidence is not None
        and quality is not None
        and edge >= MIN_EDGE
        and confidence >= MIN_CONFIDENCE
        and quality >= MIN_QUALITY_SCORE
        and bool(_source_rejection_reason(row))
    )


def _derive_rejection_reason(row: pd.Series) -> str:
    reason = _source_rejection_reason(row)
    if reason:
        return reason
    if _is_combo_under_watchlist(row):
        return "combo_under_watchlist"
    if _is_near_elite_rejected_candidate(row):
        return "near_elite_rejected_candidate"
    return "not_rejected_or_watchlisted"


def _derive_recommended_action(row: pd.Series) -> str:
    explicit = (
        _safe_text(row.get("recommended_action"))
        or _safe_text(row.get("operator_action"))
        or _safe_text(row.get("edge_containment_recommended_action"))
    )
    if explicit:
        return explicit.upper()
    if _is_high_caution_over_reject(row):
        return "WATCHLIST_ONLY"
    if _is_combo_under_watchlist(row):
        return "COMBO_WATCHLIST_ONLY"
    if _is_near_elite_rejected_candidate(row):
        return "NEAR_ELITE_REVIEW_ONLY"
    if _is_truthy(row.get("manual_review_required")) or _is_truthy(row.get("same_opponent_under_warning")):
        return "MANUAL_REVIEW_REQUIRED"
    if _source_rejection_reason(row):
        return "REJECTED"
    return "SHADOW_OBSERVED"


def _prepare_audit_frame(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    for column in (
        "prediction_date",
        "market_type",
        "selection",
        "edge",
        "confidence",
        "quality_score",
        "result_status",
    ):
        if column not in working.columns:
            working[column] = ""
    working["side"] = working["selection"].map(lambda value: _safe_text(value, "unknown").lower() or "unknown")
    working["is_high_caution_over_reject"] = working.apply(_is_high_caution_over_reject, axis=1)
    working["is_combo_under_watchlist"] = working.apply(_is_combo_under_watchlist, axis=1)
    working["is_near_elite_rejected_candidate"] = working.apply(_is_near_elite_rejected_candidate, axis=1)
    working["rejection_reason"] = working.apply(_derive_rejection_reason, axis=1)
    working["recommended_action"] = working.apply(_derive_recommended_action, axis=1)
    working["edge_bucket"] = working["edge"].map(_edge_bucket)
    working["confidence_bucket"] = working["confidence"].map(_confidence_bucket)
    working["quality_score_bucket"] = working["quality_score"].map(_quality_bucket)
    working["american_odds"] = working.apply(_odds_value, axis=1)
    working["breakeven_probability"] = working["american_odds"].map(american_breakeven_probability)
    working["unit_profit_row"] = working.apply(
        lambda row: unit_profit_for_status(row.get("result_status"), row.get("american_odds")),
        axis=1,
    )
    for column in ("meta_label_bucket", "role_stability_bucket", "fragility_bucket", "survivability_bucket"):
        if column not in working.columns:
            working[column] = "unknown"
        working[column] = working[column].map(lambda value: _safe_text(value, "unknown").lower() or "unknown")
    working["is_rejection_or_watchlist"] = (
        working["rejection_reason"].ne("not_rejected_or_watchlisted")
        | working["recommended_action"].ne("SHADOW_OBSERVED")
        | working["is_high_caution_over_reject"]
        | working["is_combo_under_watchlist"]
        | working["is_near_elite_rejected_candidate"]
    )
    return working


def _sample_status(graded_rows: int) -> str:
    if graded_rows < 10:
        return "insufficient"
    if graded_rows < 30:
        return "watch"
    return "usable"


def pressure_status_for_metrics(
    *,
    graded_rows: int,
    hit_rate: float | None,
    average_breakeven_probability: float | None,
    roi: float | None,
) -> str:
    if graded_rows < 30 or hit_rate is None or average_breakeven_probability is None or roi is None:
        return "too_early"
    hit_minus_breakeven = hit_rate - average_breakeven_probability
    if roi > 0.05 and hit_minus_breakeven >= 0.03:
        return "review_for_possible_relaxation"
    if roi < -0.05 and hit_minus_breakeven <= -0.03:
        return "keep_gate"
    return "monitor"


def _mean_numeric(df: pd.DataFrame, column: str) -> float | None:
    if column not in df.columns or df.empty:
        return None
    values = pd.to_numeric(df[column], errors="coerce").dropna()
    if values.empty:
        return None
    return round(float(values.mean()), 6)


def _metrics(df: pd.DataFrame) -> dict[str, Any]:
    statuses = _status_series(df)
    hit_count = int(statuses.eq("hit").sum())
    miss_count = int(statuses.eq("miss").sum())
    push_count = int(statuses.eq("push").sum())
    void_count = int(statuses.isin(VOID_STATUSES).sum())
    terminal_known = statuses.isin({"hit", "miss", "push", *VOID_STATUSES})
    pending_count = int((~terminal_known).sum())
    graded_rows = hit_count + miss_count
    roi_denominator = hit_count + miss_count + push_count
    hit_rate = round(hit_count / graded_rows, 6) if graded_rows else None
    unit_profit_values = pd.to_numeric(df.get("unit_profit_row", pd.Series(dtype=float)), errors="coerce").dropna()
    unit_profit = round(float(unit_profit_values.sum()), 6) if not unit_profit_values.empty else 0.0
    roi = round(unit_profit / roi_denominator, 6) if roi_denominator else None
    average_breakeven = _mean_numeric(df, "breakeven_probability")
    return {
        "total_rows": int(len(df)),
        "graded_rows": graded_rows,
        "hit_count": hit_count,
        "miss_count": miss_count,
        "push_count": push_count,
        "void_count": void_count,
        "pending_count": pending_count,
        "hit_rate": hit_rate,
        "average_odds": _mean_numeric(df, "american_odds"),
        "average_breakeven_probability": average_breakeven,
        "average_edge": _mean_numeric(df, "edge"),
        "average_confidence": _mean_numeric(df, "confidence"),
        "average_quality_score": _mean_numeric(df, "quality_score"),
        "unit_profit": unit_profit,
        "roi_percentage": round(roi * 100.0, 4) if roi is not None else None,
        "sample_size_status": _sample_status(graded_rows),
        "pressure_status": pressure_status_for_metrics(
            graded_rows=graded_rows,
            hit_rate=hit_rate,
            average_breakeven_probability=average_breakeven,
            roi=roi,
        ),
    }


def _breakdown(df: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    if df.empty or column not in df.columns:
        return []
    rows: list[dict[str, Any]] = []
    labels = df[column].map(lambda value: _safe_text(value, "unknown") or "unknown")
    for bucket, segment in df.assign(_bucket=labels).groupby("_bucket", sort=True, dropna=False):
        rows.append({"bucket": str(bucket), **_metrics(segment.drop(columns=["_bucket"], errors="ignore"))})
    return sorted(
        rows,
        key=lambda item: (
            str(item.get("breakdown", "")),
            str(item.get("bucket", "")),
        ),
    )


def _cohort_metrics(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    cohorts = {
        "high_caution_over_rejects": df[df["is_high_caution_over_reject"]].copy(),
        "near_elite_rejected_candidates": df[df["is_near_elite_rejected_candidate"]].copy(),
        "combo_under_watchlist_candidates": df[df["is_combo_under_watchlist"]].copy(),
        "meta_label_strong_review_candidates": df[df["meta_label_bucket"].eq("shadow_strong_review_candidate")].copy(),
        "role_stable_or_mostly_stable": df[df["role_stability_bucket"].isin({"stable", "mostly_stable"})].copy(),
        "fragile_rows": df[df["fragility_bucket"].isin({"high", "very_high"})].copy(),
        "all_rejected_or_watchlisted": df[df["is_rejection_or_watchlist"]].copy(),
    }
    return {name: _metrics(segment) for name, segment in cohorts.items()}


def _top_possible_false_reject_groups(breakdowns: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for breakdown, items in breakdowns.items():
        for item in items:
            if item.get("pressure_status") != "review_for_possible_relaxation":
                continue
            rows.append({"breakdown": breakdown, **item})
    return sorted(
        rows,
        key=lambda item: (
            -(float(item.get("roi_percentage") or 0.0)),
            -int(item.get("graded_rows", 0) or 0),
            -(float(item.get("hit_rate") or 0.0)),
        ),
    )[:12]


def _worst_rejected_watchlist_groups(breakdowns: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for breakdown, items in breakdowns.items():
        for item in items:
            if item.get("roi_percentage") is None:
                continue
            rows.append({"breakdown": breakdown, **item})
    return sorted(
        rows,
        key=lambda item: (
            float(item.get("roi_percentage") or 0.0),
            -int(item.get("graded_rows", 0) or 0),
        ),
    )[:12]


def _artifact_meta(path: Path, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = {"path": str(path), "exists": path.exists()}
    if payload:
        meta["generated_at_utc"] = payload.get("generated_at_utc") or payload.get("generated_at")
        meta["report_name"] = payload.get("report_name")
    return meta


def build_threshold_pressure_audit(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    shadow_history_df: pd.DataFrame | None = None,
    rejection_csv_df: pd.DataFrame | None = None,
    rejection_payload: dict[str, Any] | None = None,
    full_market_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)
    operator_dir = runtime_root / "operator"
    diagnostics_dir = runtime_root / "diagnostics"
    warnings: list[str] = []

    shadow_history_path = history_root / "market_shadow_history.csv"
    if shadow_history_df is None:
        shadow_history_df = _read_csv(shadow_history_path)
        if shadow_history_df.empty:
            warnings.append(f"market_shadow_history missing or empty: {shadow_history_path}")

    rejection_csv_path = operator_dir / f"rejection_outcome_audit_{prediction_date}.csv"
    if rejection_csv_df is None:
        rejection_csv_df = _read_csv(rejection_csv_path)
    rejection_json_path = diagnostics_dir / f"rejection_outcome_audit_{prediction_date}.json"
    if rejection_payload is None:
        rejection_payload = _read_json(rejection_json_path)

    full_market_path = operator_dir / f"full_market_board_{prediction_date}.csv"
    if full_market_df is None:
        full_market_df = _read_csv(full_market_path)

    working = _filter_history_through_date(shadow_history_df, prediction_date)
    working = _append_current_board_rows(working, full_market_df, prediction_date)
    working = _merge_optional_columns(
        working,
        full_market_df,
        (
            "final_elite_rejection_reason",
            "kelly_projected_skip_reason",
            "selection_rejection_reason",
            "rejection_reason",
            "recommended_action",
            "operator_action",
            "context_pick_alignment",
            "context_caution_level",
            "context_conflict_cause",
            "fragility_bucket",
            "survivability_bucket",
            "meta_label_bucket",
            "role_stability_bucket",
            "manual_review_required",
            "same_opponent_under_warning",
            "odds",
            "entry_odds",
        ),
    )
    audit_df = _prepare_audit_frame(working)
    pressure_df = audit_df[audit_df["is_rejection_or_watchlist"]].copy() if not audit_df.empty else audit_df
    breakdowns = {
        name: _breakdown(pressure_df, column)
        for name, column in BREAKDOWN_COLUMNS
        if column in pressure_df.columns
    }
    gate_pressure = _cohort_metrics(pressure_df)
    payload = {
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "scope": "threshold_pressure_audit_shadow",
        "disclaimer": DISCLAIMER,
        "phase4c_readiness_note": READINESS_NOTE,
        "notes": [
            "shadow_report_only",
            "no_prediction_logic_changed",
            "no_elite_gates_changed",
            "no_kelly_sizing_changed",
            "no_final_decision_changed",
            READINESS_NOTE,
            DISCLAIMER,
        ],
        "input_artifacts": {
            "market_shadow_history": _artifact_meta(shadow_history_path),
            "rejection_outcome_audit_csv": _artifact_meta(rejection_csv_path),
            "rejection_outcome_audit_json": _artifact_meta(rejection_json_path, payload=rejection_payload),
            "full_market_board": _artifact_meta(full_market_path),
        },
        "summary": {
            **_metrics(pressure_df),
            "audit_rows": int(len(audit_df)),
            "rejected_or_watchlist_rows": int(len(pressure_df)),
            "rejection_outcome_audit_csv_rows": int(len(rejection_csv_df)) if isinstance(rejection_csv_df, pd.DataFrame) else 0,
        },
        "gate_pressure_summary": gate_pressure,
        "top_possible_false_reject_groups": _top_possible_false_reject_groups(breakdowns),
        "worst_rejected_watchlist_groups": _worst_rejected_watchlist_groups(breakdowns),
        "breakdowns": breakdowns,
        "warnings": warnings,
    }
    return apply_shadow_report_metadata(
        payload,
        prediction_date=prediction_date,
        generated_by=GENERATED_BY,
        source_runtime_root=runtime_root,
        source_history_root=history_root,
        report_name=REPORT_NAME,
    )


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_rate(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_num(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "n/a"


def _metric_line(label: str, metrics: dict[str, Any]) -> str:
    return (
        f"- {label}: graded={metrics.get('graded_rows', 0)} "
        f"hit_rate={_fmt_rate(metrics.get('hit_rate'))} "
        f"roi={_fmt_pct(metrics.get('roi_percentage'))} "
        f"breakeven={_fmt_rate(metrics.get('average_breakeven_probability'))} "
        f"pressure={metrics.get('pressure_status', 'too_early')}"
    )


def _render_group_rows(title: str, rows: list[dict[str, Any]], *, limit: int = 10) -> list[str]:
    lines = [title, "-" * 72]
    if not rows:
        lines.append("n/a")
        return lines
    lines.append("group | bucket | graded | hit_rate | breakeven | profit | roi | pressure")
    for row in rows[:limit]:
        lines.append(
            " | ".join(
                [
                    str(row.get("breakdown", "")),
                    str(row.get("bucket", "unknown")),
                    str(row.get("graded_rows", 0)),
                    _fmt_rate(row.get("hit_rate")),
                    _fmt_rate(row.get("average_breakeven_probability")),
                    _fmt_num(row.get("unit_profit")),
                    _fmt_pct(row.get("roi_percentage")),
                    str(row.get("pressure_status", "too_early")),
                ]
            )
        )
    if len(rows) > limit:
        lines.append(f"... {len(rows) - limit} additional rows omitted")
    return lines


def render_threshold_pressure_audit(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    gate_pressure = (
        payload.get("gate_pressure_summary", {})
        if isinstance(payload.get("gate_pressure_summary"), dict)
        else {}
    )
    lines = [
        "Threshold Pressure & Profitability Audit - Shadow Only",
        f"prediction_date: {payload.get('prediction_date', '')}",
        "=" * 72,
        DISCLAIMER,
        READINESS_NOTE,
        "",
        "Summary",
        "-" * 72,
        f"- audit rows: {summary.get('audit_rows', 0)}",
        f"- graded rows: {summary.get('graded_rows', 0)}",
        f"- hit rate: {_fmt_rate(summary.get('hit_rate'))}",
        f"- average breakeven probability: {_fmt_rate(summary.get('average_breakeven_probability'))}",
        f"- unit profit: {_fmt_num(summary.get('unit_profit'))}",
        f"- ROI: {_fmt_pct(summary.get('roi_percentage'))}",
        f"- sample size status: {summary.get('sample_size_status', 'insufficient')}",
        f"- pressure status: {summary.get('pressure_status', 'too_early')}",
        "",
        "Gate Pressure Summary",
        "-" * 72,
    ]
    for key, label in (
        ("high_caution_over_rejects", "high-caution OVER rejects"),
        ("near_elite_rejected_candidates", "near-elite rejected candidates"),
        ("combo_under_watchlist_candidates", "combo UNDER watchlist candidates"),
        ("meta_label_strong_review_candidates", "meta-label strong review candidates"),
        ("role_stable_or_mostly_stable", "stable/mostly-stable role candidates"),
        ("fragile_rows", "fragile rows"),
    ):
        lines.append(_metric_line(label, gate_pressure.get(key, {})))

    lines.append("")
    lines.extend(_render_group_rows("Top Possible False-Reject Groups", payload.get("top_possible_false_reject_groups", [])))
    lines.append("")
    lines.extend(_render_group_rows("Worst Rejected/Watchlist Groups", payload.get("worst_rejected_watchlist_groups", [])))

    warnings = payload.get("warnings", [])
    if warnings:
        lines.extend(["", "Warnings", "-" * 72])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(["", DISCLAIMER])
    return "\n".join(lines) + "\n"


def threshold_pressure_audit_json_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"threshold_pressure_audit_{date}.json"


def threshold_pressure_audit_txt_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"threshold_pressure_audit_{date}.txt"


def threshold_pressure_audit_csv_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"threshold_pressure_audit_{date}.csv"


def _csv_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for breakdown, items in (payload.get("breakdowns", {}) or {}).items():
        if not isinstance(items, list):
            continue
        for item in items:
            row = {"breakdown": breakdown, "bucket": item.get("bucket", "unknown")}
            row.update({field: item.get(field) for field in CSV_FIELDS if field not in {"breakdown", "bucket"}})
            rows.append(row)
    for cohort, item in (payload.get("gate_pressure_summary", {}) or {}).items():
        if not isinstance(item, dict):
            continue
        row = {"breakdown": "cohort", "bucket": cohort}
        row.update({field: item.get(field) for field in CSV_FIELDS if field not in {"breakdown", "bucket"}})
        rows.append(row)
    return rows


def write_threshold_pressure_audit(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
) -> tuple[Path, Path, Path, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    payload = build_threshold_pressure_audit(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    txt_path = threshold_pressure_audit_txt_path_for_date(prediction_date, runtime_root)
    json_path = threshold_pressure_audit_json_path_for_date(prediction_date, runtime_root)
    csv_path = threshold_pressure_audit_csv_path_for_date(prediction_date, runtime_root)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(render_threshold_pressure_audit(payload), encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    pd.DataFrame(_csv_rows(payload), columns=list(CSV_FIELDS)).to_csv(csv_path, index=False)
    return txt_path, json_path, csv_path, payload


__all__ = [
    "CSV_FIELDS",
    "DISCLAIMER",
    "READINESS_NOTE",
    "REPORT_NAME",
    "REPORT_VERSION",
    "american_breakeven_probability",
    "american_odds_breakeven_probability",
    "build_threshold_pressure_audit",
    "pressure_status_for_metrics",
    "render_threshold_pressure_audit",
    "threshold_pressure_audit_csv_path_for_date",
    "threshold_pressure_audit_json_path_for_date",
    "threshold_pressure_audit_txt_path_for_date",
    "unit_profit_for_result",
    "unit_profit_for_status",
    "write_threshold_pressure_audit",
]
