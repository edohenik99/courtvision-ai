from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from courtvision.operations import (
    CourtVisionOperations,
    normalize_operation_stats,
    operations_client,
)

# Compatibility injection name retained for tests and callers. It points to the
# restricted non-prediction facade, not the CourtVision prediction runtime.
CourtVisionAI = CourtVisionOperations
from courtvision.calibration.buckets import abs_edge_bucket as _abs_edge_bucket
from courtvision.context.game_context import is_identity_quarantined
from courtvision.market_intelligence.market_snapshots import market_snapshot_key


PICK_HISTORY_COLUMNS = [
    "prediction_date",
    "run_timestamp",
    "player_name",
    "player_id",
    "team",
    "opponent",
    "game_id",
    "market",
    "selection",
    "line",
    "projection",
    "edge",
    "abs_edge",
    "odds",
    "confidence",
    "quality_score",
    "qualification_reason",
    "provider_used",
    "result_status",
    "actual_value",
    "grading_skip_reason",
    # Context / Kelly columns — populated from kelly_stakes when available
    "kelly_eligible",
    "skip_reason",
    "context_caution_level",
    "context_pick_alignment",
    "line_source",
    "fragility_score",
    "fragility_bucket",
    "fragility_reasons",
    "survivability_score",
    "survivability_bucket",
    "survivability_reasons",
]

MARKET_SHADOW_HISTORY_COLUMNS = [
    "prediction_date",
    "market_snapshot_key",
    "player_name",
    "player_id",
    "team",
    "team_abbr",
    "opponent",
    "game_id",
    "market_type",
    "selection",
    "line",
    "entry_line",
    "entry_odds",
    "opening_line_observed",
    "closing_line_observed",
    "close_source",
    "close_coverage_status",
    "line_move_points",
    "movement_toward_pick",
    "clv_line_points",
    "clv_odds_delta",
    "clv_grade",
    "clv_confidence",
    "model_projection",
    "edge",
    "confidence",
    "quality_score",
    "selection_score",
    "odds",
    "line_source",
    "context_pick_alignment",
    "context_caution_level",
    "context_conflict_cause",
    "kelly_projected_skip_reason",
    "final_elite_rejection_reason",
    "result_status",
    "actual_value",
    "hit",
    "miss",
    "push",
    "shadow_roi",
    "calibration_eligible",
    "calibration_exclusion_reason",
    "fragility_score",
    "fragility_bucket",
    "fragility_reasons",
    "survivability_score",
    "survivability_bucket",
    "survivability_reasons",
]
MARKET_SHADOW_PRESERVED_GRADED_COLUMNS = [
    "result_status",
    "actual_value",
    "hit",
    "miss",
    "push",
    "shadow_roi",
    "calibration_eligible",
    "calibration_exclusion_reason",
    "grading_skip_reason",
    "same_opponent_recent_games",
    "same_opponent_last_actual_points",
    "same_opponent_last_line",
    "same_opponent_last_selection",
    "same_opponent_last_result_status",
    "same_opponent_under_warning",
    "market_snapshot_key",
    "entry_line",
    "entry_odds",
    "opening_line_observed",
    "closing_line_observed",
    "close_source",
    "close_coverage_status",
    "line_move_points",
    "movement_toward_pick",
    "clv_line_points",
    "clv_odds_delta",
    "clv_grade",
    "clv_confidence",
]
MARKET_SHADOW_PRESERVED_RESULT_STATUSES = {"hit", "miss", "push", "void", "unsupported"}

PICK_HISTORY_PRESERVED_GRADED_COLUMNS = [
    "result_status",
    "actual_value",
    "grading_skip_reason",
    "fragility_score",
    "fragility_bucket",
    "fragility_reasons",
    "survivability_score",
    "survivability_bucket",
    "survivability_reasons",
    # preserve these if they exist in history
    "profit_loss",
    "graded_at",
    "grading_source",
]
_FRAGILITY_COLUMNS = [
    "fragility_score",
    "fragility_bucket",
    "fragility_reasons",
    "survivability_score",
    "survivability_bucket",
    "survivability_reasons",
]
PICK_HISTORY_GRADED_STATUSES = {"hit", "miss", "push", "void", "unsupported"}

MARKET_READINESS_COLUMNS = [
    "market_type",
    "selection",
    "edge_bucket",
    "confidence_bucket",
    "context_pick_alignment",
    "context_caution_level",
    "total",
    "graded_total",
    "hits",
    "misses",
    "pushes",
    "pending",
    "hit_rate",
    "sample_status",
    "calibration_eligible",
    "calibration_exclusion_reason",
]

CONTEXT_CROSS_SLATE_COLUMNS = [
    "dimension",
    "group_value",
    "total",
    "hits",
    "misses",
    "pushes",
    "pending",
    "graded_total",
    "hit_rate",
    "sample_status",
    "calibration_eligible",
    "calibration_exclusion_reason",
]

CALIBRATION_SELECTIONS = {"over", "under"}
LEGACY_METADATA_DIMENSIONS = {
    "kelly_eligible",
    "skip_reason",
    "context_caution_level",
    "context_pick_alignment",
}
BLANK_GROUP_VALUE = "(blank)"
PLAYER_POINTS_MARKET = "player_points"
FULL_MARKET_BOARD_PATTERN = re.compile(r"^full_market_board_(\d{4}-\d{2}-\d{2})\.csv$")
PENDING_RESULT_STATUS = "pending"
VOID_RESULT_STATUS = "void"
UNSUPPORTED_RESULT_STATUS = "unsupported"
GAME_NOT_FINAL_REASON = "game_not_final"
PROVIDER_MISSING_FINALITY_REASON = "provider_missing_finality"
UNSUPPORTED_GRADING_REASONS = {"unsupported_market", "unsupported_selection"}
MARKET_TYPE_ALIASES = {
    "points": "player_points",
    "rebounds": "player_rebounds",
    "assists": "player_assists",
    "points_rebounds": "player_points_rebounds",
    "points_assists": "player_points_assists",
    "rebounds_assists": "player_rebounds_assists",
    "points_rebounds_assists": "player_points_rebounds_assists",
    "steals": "player_steals",
    "blocks": "player_blocks",
    "threes": "player_3pt_made",
    "3pt_made": "player_3pt_made",
    "three_pointers_made": "player_3pt_made",
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _safe_non_null_text(value: Any, default: str = "") -> str:
    text = _safe_text(value, default=default)
    return default if text.lower() in {"nan", "none", "null", "<na>"} else text


def _optional_float(value: Any) -> float | None:
    text = _safe_non_null_text(value)
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return float(number)


def _first_present(row: pd.Series | dict[str, Any], *columns: str) -> Any:
    for column in columns:
        value = row.get(column) if column in row else None
        if _safe_non_null_text(value):
            return value
    return ""


def _normalize_market_type_value(value: Any) -> str:
    text = _safe_non_null_text(value).lower().replace(" ", "_")
    if not text:
        return ""
    return MARKET_TYPE_ALIASES.get(text, text)


def _market_shadow_market_type(row: pd.Series | dict[str, Any]) -> str:
    for column in ("market_type", "market", "prop_type", "raw_prop_type"):
        market_type = _normalize_market_type_value(row.get(column))
        if market_type:
            return market_type
    return ""


def _today_iso() -> str:
    return datetime.now().date().isoformat()


def _split_grading_reasons(value: Any) -> list[str]:
    return [reason for reason in _safe_text(value).split(";") if reason]


def _unresolved_result_status(skip_reason: str, prediction_date: str) -> str:
    reasons = set(_split_grading_reasons(skip_reason))
    if reasons & UNSUPPORTED_GRADING_REASONS:
        return UNSUPPORTED_RESULT_STATUS
    if reasons == {GAME_NOT_FINAL_REASON} and _safe_text(prediction_date) >= _today_iso():
        return PENDING_RESULT_STATUS
    if PROVIDER_MISSING_FINALITY_REASON in reasons and _safe_text(prediction_date) >= _today_iso():
        return PENDING_RESULT_STATUS
    return VOID_RESULT_STATUS


def _line_key(value: Any) -> str:
    """Normalize a sportsbook line value to a consistent 4-decimal string for dict joins."""
    if value is None:
        return "__none__"
    s = str(value).strip()
    if s in ("", "nan", "None"):
        return "__none__"
    try:
        return f"{float(s):.4f}"
    except (TypeError, ValueError):
        return s.lower()


def _market_shadow_match_key(
    row: pd.Series | dict[str, Any],
    *,
    include_player_id: bool,
) -> tuple[str, str, str, str, str, str, str, str]:
    player_id = _safe_text(row.get("player_id")).lower() if include_player_id else ""
    return (
        _safe_text(row.get("prediction_date")).lower(),
        player_id,
        _safe_text(row.get("player_name")).lower(),
        _safe_text(row.get("team_abbr")).upper(),
        _safe_text(row.get("opponent")).upper(),
        _normalize_market_type_value(row.get("market_type")),
        _safe_text(row.get("selection")).lower(),
        _line_key(row.get("line")),
    )


def _pick_history_match_key(row: pd.Series | dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        _safe_text(row.get("prediction_date")).strip(),
        _safe_text(row.get("player_name")).strip().lower(),
        _safe_text(row.get("market")).strip().lower(),
        _safe_text(row.get("selection")).strip().lower(),
        _line_key(row.get("line")),
    )


def _preserve_existing_pick_grades(
    incoming: pd.DataFrame,
    existing: pd.DataFrame,
) -> pd.DataFrame:
    """Restore graded fields from existing rows so pending re-runs don't overwrite graded results."""
    if incoming.empty or existing.empty:
        return incoming

    graded_existing = existing[
        existing["result_status"].astype(str).str.strip().str.lower().isin(PICK_HISTORY_GRADED_STATUSES)
    ].copy()
    if graded_existing.empty:
        return incoming

    graded_lookup: dict[tuple[str, str, str, str, str], pd.Series] = {}
    for _, row in graded_existing.iterrows():
        key = _pick_history_match_key(row)
        if key[0] and key[1]:
            graded_lookup[key] = row

    if not graded_lookup:
        return incoming

    preserved = incoming.copy()
    for col in PICK_HISTORY_PRESERVED_GRADED_COLUMNS:
        if col in graded_existing.columns and col not in preserved.columns:
            preserved[col] = ""
        if col in preserved.columns:
            preserved[col] = preserved[col].astype("object")

    for idx, row in preserved.iterrows():
        existing_row = graded_lookup.get(_pick_history_match_key(row))
        if existing_row is None:
            continue
        for col in PICK_HISTORY_PRESERVED_GRADED_COLUMNS:
            if col in preserved.columns and col in existing_row.index:
                preserved.at[idx, col] = existing_row.get(col)
    return preserved


def _preserve_existing_market_shadow_grades(
    incoming: pd.DataFrame,
    existing_same_date: pd.DataFrame,
) -> pd.DataFrame:
    if incoming.empty or existing_same_date.empty:
        return incoming

    graded_existing = existing_same_date[
        existing_same_date["result_status"].astype(str).str.strip().str.lower().isin(MARKET_SHADOW_PRESERVED_RESULT_STATUSES)
    ].copy()
    if graded_existing.empty:
        return incoming

    by_player_id: dict[tuple[str, str, str, str, str, str, str, str], pd.Series] = {}
    fallback_counts: Counter[tuple[str, str, str, str, str, str, str, str]] = Counter()
    fallback_rows: dict[tuple[str, str, str, str, str, str, str, str], pd.Series] = {}
    for _, row in graded_existing.iterrows():
        if _safe_text(row.get("player_id")):
            by_player_id[_market_shadow_match_key(row, include_player_id=True)] = row
        fallback_key = _market_shadow_match_key(row, include_player_id=False)
        fallback_counts[fallback_key] += 1
        fallback_rows[fallback_key] = row

    preserved = incoming.copy()
    for column in MARKET_SHADOW_PRESERVED_GRADED_COLUMNS:
        if column in graded_existing.columns and column not in preserved.columns:
            preserved[column] = ""
        if column in preserved.columns:
            preserved[column] = preserved[column].astype("object")
    for idx, row in preserved.iterrows():
        existing_row = None
        if _safe_text(row.get("player_id")):
            existing_row = by_player_id.get(_market_shadow_match_key(row, include_player_id=True))
        if existing_row is None:
            fallback_key = _market_shadow_match_key(row, include_player_id=False)
            if fallback_counts.get(fallback_key, 0) == 1:
                existing_row = fallback_rows.get(fallback_key)
        if existing_row is None:
            continue
        for column in MARKET_SHADOW_PRESERVED_GRADED_COLUMNS:
            if column in preserved.columns and column in existing_row.index:
                preserved.at[idx, column] = existing_row.get(column)
        # Preserve fragility fields only when the incoming row has no diagnostic data.
        for col in _FRAGILITY_COLUMNS:
            if col not in preserved.columns or col not in existing_row.index:
                continue
            incoming_val = _safe_text(preserved.at[idx, col])
            if not incoming_val or incoming_val.lower() in {"nan", "none"}:
                preserved.at[idx, col] = existing_row.get(col)
    return preserved


def _confidence_bucket(value: float) -> str:
    """Bucket a 0–1 float confidence; called on pd.to_numeric result (NaN-safe)."""
    if value != value:  # NaN check
        return "unknown"
    if value < 0.55:
        return "below_0.55"
    if value < 0.65:
        return "0.55-0.65"
    if value < 0.75:
        return "0.65-0.75"
    if value < 0.85:
        return "0.75-0.85"
    return "0.85+"


def _sample_status(graded_total: int) -> str:
    if graded_total <= 0:
        return "no_graded_results"
    if graded_total < 20:
        return "insufficient_sample"
    return "usable_sample"


def _market_readiness_sample_status(graded_total: int) -> str:
    if graded_total <= 0:
        return "no_graded_results"
    if graded_total < 30:
        return "insufficient_sample"
    return "usable_sample"


def _context_group_value(series: pd.Series) -> pd.Series:
    group_col = series.fillna("").astype(str).str.strip()
    group_col = group_col.replace({"nan": "", "none": "", "None": ""})
    return group_col.mask(group_col == "", BLANK_GROUP_VALUE)


def _context_calibration_reasons(dimension: str, group_value: str, graded_total: int) -> list[str]:
    reasons: list[str] = []
    group_norm = str(group_value or "").strip().lower()

    if dimension == "selection" and group_norm not in CALIBRATION_SELECTIONS:
        reason_value = group_norm.replace(" ", "_") or "blank"
        reasons.append(f"unsupported_selection_{reason_value}")

    if dimension in LEGACY_METADATA_DIMENSIONS and group_value == BLANK_GROUP_VALUE:
        reasons.append("legacy_missing_metadata")

    status = _sample_status(graded_total)
    if status != "usable_sample":
        reasons.append(status)

    return reasons


def _load_csv(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns or [])
    return pd.read_csv(path)


def _write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _read_csv_preserve_schema(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=columns)
    try:
        df = pd.read_csv(path, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=columns)
    for column in columns:
        if column not in df.columns:
            df[column] = ""
    ordered_columns = columns + [column for column in df.columns if column not in columns]
    return df[ordered_columns]


def _full_market_board_date(path: Path) -> str | None:
    match = FULL_MARKET_BOARD_PATTERN.match(path.name)
    return match.group(1) if match else None


def _full_market_row_count(path: Path) -> int:
    try:
        return int(len(pd.read_csv(path, low_memory=False)))
    except pd.errors.EmptyDataError:
        return 0


def _unique_sorted_dates(values: list[str]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _american_to_shadow_roi(odds: Any, result_status: str) -> float | str:
    status = _safe_text(result_status).lower()
    if status == "miss":
        return -1.0
    if status == "push":
        return 0.0
    if status != "hit":
        return ""
    odds_value = _safe_float(odds, default=float("nan"))
    if odds_value != odds_value:
        return ""
    if odds_value > 0:
        return round(float(odds_value) / 100.0, 6)
    if odds_value < 0:
        return round(100.0 / abs(float(odds_value)), 6)
    return ""


def _shadow_result_flags(row: pd.Series | dict[str, Any]) -> tuple[str, bool, bool, bool]:
    result_status = _safe_text(row.get("result_status"), default="pending").lower()
    hit = _truthy(row.get("hit")) or result_status == "hit"
    miss = _truthy(row.get("miss")) or result_status == "miss"
    push = _truthy(row.get("push")) or result_status == "push"
    if hit:
        result_status = "hit"
    elif miss:
        result_status = "miss"
    elif push:
        result_status = "push"
    elif result_status not in {"pending", "hit", "miss", "push"}:
        result_status = "pending"
    return result_status, hit, miss, push


def _row_calibration_fields(result_status: str, selection: str) -> tuple[bool, str]:
    reasons: list[str] = []
    selection_norm = _safe_text(selection).lower()
    status_norm = _safe_text(result_status).lower()
    if selection_norm not in CALIBRATION_SELECTIONS:
        reason_value = selection_norm.replace(" ", "_") or "blank"
        reasons.append(f"unsupported_selection_{reason_value}")
    if status_norm == "pending":
        reasons.append("no_graded_results")
    elif status_norm == "push":
        reasons.append("push_excluded_from_hit_rate")
    elif status_norm not in {"hit", "miss"}:
        reasons.append("ungraded_result")
    return not reasons, ";".join(reasons)


def _provider_from_audit_summary(audit_summary_path: Path) -> str:
    if not audit_summary_path.exists():
        return "unknown"
    try:
        payload = json.loads(audit_summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    summary = payload.get("summary") or {}
    return _safe_text(summary.get("provider_used"), default="unknown")


def _build_kelly_lookup(kelly_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Build a lookup dict from a kelly_stakes DataFrame for joining into pick_history.

    Key format: "{player.lower()}|{market.lower()}|{selection.lower()}|{line_key}"
    Returns context columns: kelly_eligible, skip_reason, context_caution_level, context_pick_alignment.
    """
    if kelly_df.empty:
        return {}
    name_col = "player_name" if "player_name" in kelly_df.columns else "entity_name"
    mkt_col = "market_type" if "market_type" in kelly_df.columns else "market"
    line_col = next((c for c in ("line", "sportsbook_line", "line_value") if c in kelly_df.columns), None)
    lookup: dict[str, dict[str, Any]] = {}
    for _, row in kelly_df.iterrows():
        player = _safe_text(row.get(name_col)).lower()
        market = _safe_text(row.get(mkt_col)).lower()
        selection = _safe_text(row.get("selection")).lower()
        line = _line_key(row.get(line_col) if line_col else None)
        key = f"{player}|{market}|{selection}|{line}"
        eligible_raw = row.get("kelly_eligible") if row.get("kelly_eligible") is not None else row.get("eligible")
        if isinstance(eligible_raw, bool):
            kelly_eligible = "True" if eligible_raw else "False"
        elif str(eligible_raw).strip().lower() in ("true", "1", "yes"):
            kelly_eligible = "True"
        elif str(eligible_raw).strip().lower() in ("false", "0", "no"):
            kelly_eligible = "False"
        else:
            kelly_eligible = ""
        lookup[key] = {
            "kelly_eligible": kelly_eligible,
            "skip_reason": _safe_text(row.get("skip_reason")),
            "context_caution_level": _safe_text(row.get("context_caution_level")),
            "context_pick_alignment": _safe_text(row.get("context_pick_alignment")),
        }
    return lookup


def migrate_pick_history_schema(
    history_root: str | Path = "data/history",
) -> dict[str, Any]:
    """Idempotent migration: add missing PICK_HISTORY_COLUMNS to an existing pick_history.csv.

    Safe to re-run: only adds absent columns with empty-string defaults.
    Never removes, renames, or overwrites existing data.
    Returns a summary dict with keys: status, added_columns, total_rows.
    """
    history_root_path = Path(history_root)
    pick_history_path = history_root_path / "pick_history.csv"
    if not pick_history_path.exists():
        return {"status": "no_file", "added_columns": [], "total_rows": 0}
    df = pd.read_csv(pick_history_path)
    added: list[str] = []
    for col in PICK_HISTORY_COLUMNS:
        if col not in df.columns:
            df[col] = ""
            added.append(col)
    if added:
        _write_csv(pick_history_path, df[PICK_HISTORY_COLUMNS])
    return {"status": "ok", "added_columns": added, "total_rows": len(df)}


def migrate_market_shadow_history_schema(
    history_root: str | Path = "data/history",
) -> dict[str, Any]:
    """Add missing market shadow columns without dropping existing rows or extras."""

    history_root_path = Path(history_root)
    shadow_history_path = history_root_path / "market_shadow_history.csv"
    if not shadow_history_path.exists():
        return {"status": "no_file", "added_columns": [], "total_rows": 0}

    try:
        existing_columns = list(pd.read_csv(shadow_history_path, nrows=0).columns)
    except pd.errors.EmptyDataError:
        existing_columns = []
    df = _read_csv_preserve_schema(shadow_history_path, MARKET_SHADOW_HISTORY_COLUMNS)
    added = [column for column in MARKET_SHADOW_HISTORY_COLUMNS if column not in existing_columns]
    if added:
        _write_csv(shadow_history_path, df)
    return {"status": "ok", "added_columns": added, "total_rows": len(df)}


def _passive_clv_market_fields(
    row: pd.Series,
    *,
    prediction_date: str,
    line_value: Any,
) -> dict[str, Any]:
    entry_line_raw = _first_present(row, "entry_line") or line_value
    entry_odds_raw = _first_present(row, "entry_odds") or _first_present(row, "odds", "american_odds")
    opening_line_raw = _first_present(row, "opening_line_observed", "opening_line", "open_line")
    closing_line_raw = _first_present(
        row,
        "closing_line_observed",
        "closing_line",
        "close_line",
        "market_close_line",
    )
    closing_odds_raw = _first_present(
        row,
        "closing_odds_observed",
        "closing_odds",
        "close_odds",
        "market_close_odds",
    )

    entry_line = _optional_float(entry_line_raw)
    entry_odds = _optional_float(entry_odds_raw)
    opening_line = _optional_float(opening_line_raw)
    closing_line = _optional_float(closing_line_raw)
    closing_odds = _optional_float(closing_odds_raw)
    close_source = _safe_non_null_text(
        _first_present(row, "close_source", "closing_line_source", "closing_source")
    )
    close_status = _safe_non_null_text(row.get("close_coverage_status"))
    if not close_status:
        close_status = "observed" if closing_line is not None else "missing"

    line_move_points = _optional_float(row.get("line_move_points"))
    if line_move_points is None and opening_line is not None and closing_line is not None:
        line_move_points = round(closing_line - opening_line, 4)

    selection = _safe_text(row.get("selection")).lower()
    movement_toward_pick: bool | str = _safe_non_null_text(row.get("movement_toward_pick"))
    if movement_toward_pick == "" and line_move_points is not None and abs(line_move_points) >= 1e-9:
        if selection == "over":
            movement_toward_pick = line_move_points > 0
        elif selection == "under":
            movement_toward_pick = line_move_points < 0

    clv_line_points = _optional_float(row.get("clv_line_points"))
    if clv_line_points is None and entry_line is not None and closing_line is not None:
        if selection == "over":
            clv_line_points = round(closing_line - entry_line, 4)
        elif selection == "under":
            clv_line_points = round(entry_line - closing_line, 4)

    clv_odds_delta = _optional_float(row.get("clv_odds_delta"))
    if clv_odds_delta is None and entry_odds is not None and closing_odds is not None:
        clv_odds_delta = round(closing_odds - entry_odds, 4)

    clv_grade = _safe_non_null_text(row.get("clv_grade"))
    if not clv_grade:
        if clv_line_points is None:
            clv_grade = "missing"
        elif clv_line_points > 0:
            clv_grade = "positive"
        elif clv_line_points < 0:
            clv_grade = "negative"
        else:
            clv_grade = "neutral"

    clv_confidence = _optional_float(row.get("clv_confidence"))
    if clv_confidence is None:
        if closing_line is None:
            clv_confidence = 0.0
        elif close_source:
            clv_confidence = 1.0
        else:
            clv_confidence = 0.75

    snapshot_key = _safe_non_null_text(row.get("market_snapshot_key"))
    if not snapshot_key:
        snapshot_key = market_snapshot_key(row, prediction_date=prediction_date)

    return {
        "market_snapshot_key": snapshot_key,
        "team": _safe_text(row.get("team")) or _safe_text(row.get("team_abbr")),
        "entry_line": entry_line if entry_line is not None else "",
        "entry_odds": entry_odds if entry_odds is not None else "",
        "opening_line_observed": opening_line if opening_line is not None else "",
        "closing_line_observed": closing_line if closing_line is not None else "",
        "close_source": close_source,
        "close_coverage_status": close_status,
        "line_move_points": line_move_points if line_move_points is not None else "",
        "movement_toward_pick": movement_toward_pick,
        "clv_line_points": clv_line_points if clv_line_points is not None else "",
        "clv_odds_delta": clv_odds_delta if clv_odds_delta is not None else "",
        "clv_grade": clv_grade,
        "clv_confidence": clv_confidence,
    }


def _normalize_market_shadow_rows(
    full_market_df: pd.DataFrame,
    *,
    prediction_date: str,
    result_status: str = "pending",
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if not isinstance(full_market_df, pd.DataFrame) or full_market_df.empty:
        return pd.DataFrame(columns=MARKET_SHADOW_HISTORY_COLUMNS)

    for _, row in full_market_df.iterrows():
        if is_identity_quarantined(row) is not None:
            continue
        status = _safe_text(row.get("result_status"), default=result_status).lower()
        if status not in {"pending", "hit", "miss", "push"}:
            status = result_status
        hit = status == "hit"
        miss = status == "miss"
        push = status == "push"
        calibration_eligible, calibration_reason = _row_calibration_fields(
            status,
            _safe_text(row.get("selection")).lower(),
        )
        line_value = row.get("line") if "line" in row.index else row.get("sportsbook_line")
        model_projection = (
            row.get("model_projection")
            if "model_projection" in row.index
            else row.get("projection")
        )
        shadow_roi = row.get("shadow_roi") if "shadow_roi" in row.index else ""
        if _safe_text(shadow_roi) == "":
            shadow_roi = _american_to_shadow_roi(row.get("odds"), status)
        passive_market_fields = _passive_clv_market_fields(
            row,
            prediction_date=prediction_date,
            line_value=line_value,
        )
        rows.append(
            {
                "prediction_date": _safe_text(row.get("prediction_date"), default=prediction_date),
                "market_snapshot_key": passive_market_fields["market_snapshot_key"],
                "player_name": _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name")),
                "player_id": _safe_text(row.get("player_id")),
                "team": passive_market_fields["team"],
                "team_abbr": _safe_text(row.get("team_abbr")) or _safe_text(row.get("team")),
                "opponent": _safe_text(row.get("opponent")),
                "game_id": _safe_text(row.get("game_id")),
                "market_type": _market_shadow_market_type(row),
                "selection": _safe_text(row.get("selection")).lower(),
                "line": line_value,
                "entry_line": passive_market_fields["entry_line"],
                "entry_odds": passive_market_fields["entry_odds"],
                "opening_line_observed": passive_market_fields["opening_line_observed"],
                "closing_line_observed": passive_market_fields["closing_line_observed"],
                "close_source": passive_market_fields["close_source"],
                "close_coverage_status": passive_market_fields["close_coverage_status"],
                "line_move_points": passive_market_fields["line_move_points"],
                "movement_toward_pick": passive_market_fields["movement_toward_pick"],
                "clv_line_points": passive_market_fields["clv_line_points"],
                "clv_odds_delta": passive_market_fields["clv_odds_delta"],
                "clv_grade": passive_market_fields["clv_grade"],
                "clv_confidence": passive_market_fields["clv_confidence"],
                "model_projection": model_projection,
                "edge": row.get("edge"),
                "confidence": row.get("confidence"),
                "quality_score": row.get("quality_score"),
                "selection_score": row.get("selection_score"),
                "odds": row.get("odds"),
                "line_source": _safe_text(row.get("line_source")),
                "context_pick_alignment": _safe_text(row.get("context_pick_alignment")),
                "context_caution_level": _safe_text(row.get("context_caution_level")),
                "context_conflict_cause": _safe_text(row.get("context_conflict_cause")),
                "kelly_projected_skip_reason": _safe_text(row.get("kelly_projected_skip_reason")),
                "final_elite_rejection_reason": _safe_text(row.get("final_elite_rejection_reason")),
                "result_status": status,
                "actual_value": row.get("actual_value") if "actual_value" in row.index else "",
                "hit": hit,
                "miss": miss,
                "push": push,
                "shadow_roi": shadow_roi,
                "calibration_eligible": calibration_eligible,
                "calibration_exclusion_reason": calibration_reason,
                "fragility_score": row.get("fragility_score") if "fragility_score" in row.index else "",
                "fragility_bucket": _safe_text(row.get("fragility_bucket")) if "fragility_bucket" in row.index else "",
                "fragility_reasons": _safe_text(row.get("fragility_reasons")) if "fragility_reasons" in row.index else "",
                "survivability_score": row.get("survivability_score") if "survivability_score" in row.index else "",
                "survivability_bucket": _safe_text(row.get("survivability_bucket")) if "survivability_bucket" in row.index else "",
                "survivability_reasons": _safe_text(row.get("survivability_reasons")) if "survivability_reasons" in row.index else "",
            }
        )

    return pd.DataFrame(rows, columns=MARKET_SHADOW_HISTORY_COLUMNS)


def build_market_readiness_summary(history_df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(history_df, pd.DataFrame) or history_df.empty:
        return pd.DataFrame(columns=MARKET_READINESS_COLUMNS)

    df = history_df.copy()
    for column in MARKET_SHADOW_HISTORY_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    df["edge_bucket"] = pd.to_numeric(df["edge"], errors="coerce").apply(
        lambda value: _abs_edge_bucket(value) if value == value else "unknown"
    )
    df["confidence_bucket"] = pd.to_numeric(df["confidence"], errors="coerce").apply(_confidence_bucket)

    group_columns = [
        "market_type",
        "selection",
        "edge_bucket",
        "confidence_bucket",
        "context_pick_alignment",
        "context_caution_level",
    ]
    for column in group_columns:
        df[column] = _context_group_value(df[column])

    rows: list[dict[str, Any]] = []
    for group_values, segment in df.groupby(group_columns, sort=True, dropna=False):
        statuses = segment.apply(_shadow_result_flags, axis=1)
        hits = int(sum(flag[1] for flag in statuses))
        misses = int(sum(flag[2] for flag in statuses))
        pushes = int(sum(flag[3] for flag in statuses))
        pending = int(len(segment) - hits - misses - pushes)
        graded_total = hits + misses
        sample_status = _market_readiness_sample_status(graded_total)
        calibration_eligible = sample_status == "usable_sample"
        rows.append(
            {
                "market_type": group_values[0],
                "selection": group_values[1],
                "edge_bucket": group_values[2],
                "confidence_bucket": group_values[3],
                "context_pick_alignment": group_values[4],
                "context_caution_level": group_values[5],
                "total": int(len(segment)),
                "graded_total": graded_total,
                "hits": hits,
                "misses": misses,
                "pushes": pushes,
                "pending": pending,
                "hit_rate": round(float(hits / graded_total), 4) if graded_total else "",
                "sample_status": sample_status,
                "calibration_eligible": calibration_eligible,
                "calibration_exclusion_reason": "" if calibration_eligible else sample_status,
            }
        )
    return pd.DataFrame(rows, columns=MARKET_READINESS_COLUMNS)


def update_market_readiness_summary(
    history_root: str | Path = "data/history",
) -> tuple[Path, pd.DataFrame]:
    history_root_path = Path(history_root)
    shadow_history_path = history_root_path / "market_shadow_history.csv"
    readiness_path = history_root_path / "market_readiness_summary.csv"
    history_df = _read_csv_preserve_schema(shadow_history_path, MARKET_SHADOW_HISTORY_COLUMNS)
    readiness = build_market_readiness_summary(history_df)
    _write_csv(readiness_path, readiness)
    return readiness_path, readiness


def discover_full_market_board_dates(runtime_root: str | Path = "outputs/runtime") -> dict[str, Path]:
    runtime_root_path = Path(runtime_root)
    operator_root = runtime_root_path / "operator"
    boards: dict[str, Path] = {}
    if not operator_root.exists():
        return boards
    for path in sorted(operator_root.glob("full_market_board_*.csv")):
        prediction_date = _full_market_board_date(path)
        if prediction_date:
            boards[prediction_date] = path
    return boards


def persist_market_shadow_history(
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    result_status: str = "pending",
    dry_run: bool = False,
) -> dict[str, Any]:
    runtime_root_path = Path(runtime_root)
    history_root_path = Path(history_root)
    full_market_path = runtime_root_path / "operator" / f"full_market_board_{prediction_date}.csv"
    shadow_history_path = history_root_path / "market_shadow_history.csv"

    if not full_market_path.exists():
        raise FileNotFoundError(f"Missing full market board CSV: {full_market_path}")

    try:
        full_market_df = pd.read_csv(full_market_path, low_memory=False)
    except pd.errors.EmptyDataError:
        full_market_df = pd.DataFrame(columns=MARKET_SHADOW_HISTORY_COLUMNS)

    incoming = _normalize_market_shadow_rows(
        full_market_df,
        prediction_date=prediction_date,
        result_status=result_status,
    )
    existing = _read_csv_preserve_schema(shadow_history_path, MARKET_SHADOW_HISTORY_COLUMNS)
    same_date_mask = (
        existing["prediction_date"].astype(str).eq(str(prediction_date))
        if "prediction_date" in existing.columns
        else pd.Series(False, index=existing.index)
    )
    replaced_rows = int(same_date_mask.sum())
    existing_same_date = existing.loc[same_date_mask].copy()
    incoming = _preserve_existing_market_shadow_grades(incoming, existing_same_date)
    preserved_existing = existing.loc[~same_date_mask].copy()

    if preserved_existing.empty:
        combined = incoming.copy()
    elif incoming.empty:
        combined = preserved_existing.copy()
    else:
        combined = pd.concat([preserved_existing, incoming], ignore_index=True)
    for column in MARKET_SHADOW_HISTORY_COLUMNS:
        if column not in combined.columns:
            combined[column] = ""
    ordered_columns = MARKET_SHADOW_HISTORY_COLUMNS + [
        column for column in combined.columns if column not in MARKET_SHADOW_HISTORY_COLUMNS
    ]
    combined = combined[ordered_columns].sort_values(
        ["prediction_date", "market_type", "player_name", "selection", "line"],
        kind="mergesort",
    ).reset_index(drop=True)
    if not dry_run:
        _write_csv(shadow_history_path, combined)
        readiness_path, readiness = update_market_readiness_summary(history_root=history_root_path)
    else:
        readiness_path = history_root_path / "market_readiness_summary.csv"
        readiness = build_market_readiness_summary(combined)
    current_date_rows = combined[combined["prediction_date"].astype(str) == str(prediction_date)].copy()
    current_market_counts = (
        current_date_rows["market_type"].fillna("unknown").astype(str).str.strip().replace("", "unknown").value_counts()
        if not current_date_rows.empty and "market_type" in current_date_rows.columns
        else pd.Series(dtype=int)
    )
    non_points_rows = (
        int(current_date_rows["market_type"].astype(str).str.strip().str.lower().ne(PLAYER_POINTS_MARKET).sum())
        if not current_date_rows.empty and "market_type" in current_date_rows.columns
        else 0
    )
    return {
        "market_shadow_history_path": shadow_history_path,
        "market_readiness_summary_path": readiness_path,
        "incoming_rows": int(len(incoming)),
        "replaced_rows": replaced_rows,
        "new_rows": max(int(len(incoming)) - replaced_rows, 0),
        "total_rows": int(len(combined)),
        "current_date_rows": int(len(current_date_rows)),
        "current_date_non_points_rows": non_points_rows,
        "current_date_market_counts": {str(k): int(v) for k, v in current_market_counts.items()},
        "readiness_rows": int(len(readiness)),
        "dry_run": bool(dry_run),
    }


def backfill_market_shadow_history(
    *,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    dates: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    replace_existing: bool = False,
    dry_run: bool = False,
    result_status: str = "pending",
) -> dict[str, Any]:
    """Backfill shadow history from historical full-market boards.

    Default behavior is conservative: only dates missing from
    market_shadow_history.csv are written. Existing dates with row counts that
    differ from their full-market board are reported as count mismatches and
    require replace_existing=True to refresh.
    """

    runtime_root_path = Path(runtime_root)
    history_root_path = Path(history_root)
    shadow_history_path = history_root_path / "market_shadow_history.csv"
    readiness_path = history_root_path / "market_readiness_summary.csv"
    boards = discover_full_market_board_dates(runtime_root_path)

    requested_dates = _unique_sorted_dates(dates or list(boards.keys()))
    missing_full_market_dates = [prediction_date for prediction_date in requested_dates if prediction_date not in boards]
    selected_dates = [prediction_date for prediction_date in requested_dates if prediction_date in boards]
    if start_date:
        selected_dates = [prediction_date for prediction_date in selected_dates if prediction_date >= str(start_date)]
    if end_date:
        selected_dates = [prediction_date for prediction_date in selected_dates if prediction_date <= str(end_date)]

    existing = _read_csv_preserve_schema(shadow_history_path, MARKET_SHADOW_HISTORY_COLUMNS)
    if not existing.empty and "prediction_date" in existing.columns:
        existing_date_series = existing["prediction_date"].astype(str).str.strip()
        existing_counts = {str(k): int(v) for k, v in existing_date_series.value_counts().items() if str(k).strip()}
    else:
        existing_counts = {}
    existing_dates = set(existing_counts)
    board_row_counts = {prediction_date: _full_market_row_count(boards[prediction_date]) for prediction_date in selected_dates}

    complete_existing_dates = [
        prediction_date
        for prediction_date in selected_dates
        if prediction_date in existing_counts and existing_counts[prediction_date] == board_row_counts[prediction_date]
    ]
    count_mismatch_details = [
        {
            "prediction_date": prediction_date,
            "existing_rows": existing_counts[prediction_date],
            "board_rows": board_row_counts[prediction_date],
        }
        for prediction_date in selected_dates
        if prediction_date in existing_counts and existing_counts[prediction_date] != board_row_counts[prediction_date]
    ]
    count_mismatch_dates = [str(detail["prediction_date"]) for detail in count_mismatch_details]
    missing_shadow_dates = [prediction_date for prediction_date in selected_dates if prediction_date not in existing_counts]

    skipped_existing_dates = [] if replace_existing else complete_existing_dates
    backfill_dates = selected_dates if replace_existing else missing_shadow_dates

    date_results: list[dict[str, Any]] = []
    total_incoming_rows = 0
    total_replaced_rows = 0
    total_current_date_rows = 0
    for prediction_date in backfill_dates:
        board_path = boards[prediction_date]
        board_rows = board_row_counts[prediction_date]
        if dry_run:
            date_results.append(
                {
                    "prediction_date": prediction_date,
                    "full_market_board_path": board_path,
                    "board_rows": board_rows,
                    "incoming_rows": board_rows,
                    "replaced_rows": int((existing["prediction_date"].astype(str) == prediction_date).sum())
                    if not existing.empty and "prediction_date" in existing.columns
                    else 0,
                    "current_date_rows": board_rows,
                    "dry_run": True,
                }
            )
            total_incoming_rows += board_rows
            total_current_date_rows += board_rows
            continue

        result = persist_market_shadow_history(
            prediction_date=prediction_date,
            runtime_root=runtime_root_path,
            history_root=history_root_path,
            result_status=result_status,
        )
        date_results.append(
            {
                "prediction_date": prediction_date,
                "full_market_board_path": board_path,
                "board_rows": board_rows,
                **result,
                "dry_run": False,
            }
        )
        total_incoming_rows += int(result["incoming_rows"])
        total_replaced_rows += int(result["replaced_rows"])
        total_current_date_rows += int(result["current_date_rows"])

    return {
        "runtime_root": runtime_root_path,
        "history_root": history_root_path,
        "market_shadow_history_path": shadow_history_path,
        "market_readiness_summary_path": readiness_path,
        "full_market_board_dates": sorted(boards),
        "selected_dates": selected_dates,
        "missing_full_market_dates": missing_full_market_dates,
        "existing_shadow_dates": sorted(existing_dates),
        "skipped_existing_dates": skipped_existing_dates,
        "complete_existing_dates": complete_existing_dates,
        "count_mismatch_dates": count_mismatch_dates,
        "count_mismatch_details": count_mismatch_details,
        "missing_shadow_dates": missing_shadow_dates,
        "backfilled_dates": [] if dry_run else backfill_dates,
        "would_backfill_dates": backfill_dates if dry_run else [],
        "replace_existing": replace_existing,
        "dry_run": dry_run,
        "dates_processed": int(len(backfill_dates)),
        "total_incoming_rows": int(total_incoming_rows),
        "total_replaced_rows": int(total_replaced_rows),
        "total_current_date_rows": int(total_current_date_rows),
        "date_results": date_results,
    }


def persist_daily_picks(
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    result_status: str = "pending",
    dry_run: bool = False,
) -> dict[str, Any]:
    runtime_root_path = Path(runtime_root)
    history_root_path = Path(history_root)
    operator_path = runtime_root_path / "operator" / f"elite_board_{prediction_date}.csv"
    picks_output_path = runtime_root_path / "history" / f"picks_{prediction_date}.csv"
    audit_summary_path = runtime_root_path / "operator" / f"elite_pipeline_audit_summary_{prediction_date}.json"
    pick_history_path = history_root_path / "pick_history.csv"

    if not operator_path.exists():
        raise FileNotFoundError(f"Missing elite board CSV: {operator_path}")

    elite_df = pd.read_csv(operator_path)
    if not dry_run:
        picks_output_path.parent.mkdir(parents=True, exist_ok=True)
        elite_df.to_csv(picks_output_path, index=False)

    # Load kelly_stakes for context / eligibility columns — best-effort, no hard failure.
    kelly_stakes_path = runtime_root_path / "operator" / f"kelly_stakes_{prediction_date}.csv"
    kelly_lookup: dict[str, dict[str, Any]] = {}
    if kelly_stakes_path.exists():
        try:
            kelly_lookup = _build_kelly_lookup(pd.read_csv(kelly_stakes_path))
        except Exception:
            kelly_lookup = {}

    provider_used = _provider_from_audit_summary(audit_summary_path)
    timestamp = datetime.now(timezone.utc).isoformat()

    normalized_rows: list[dict[str, Any]] = []
    for _, row in elite_df.iterrows():
        if is_identity_quarantined(row) is not None:
            continue
        edge = _safe_float(row.get("edge"))
        player = _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name"), default="unknown")
        market = _safe_text(row.get("market_type")) or _safe_text(row.get("market"))
        selection = _safe_text(row.get("selection")).lower()
        line_raw = row.get("sportsbook_line") if row.get("sportsbook_line") is not None else row.get("line_value")
        lookup_key = f"{player.lower()}|{market.lower()}|{selection}|{_line_key(line_raw)}"
        kelly_ctx = kelly_lookup.get(lookup_key, {})
        # context_caution_level / context_pick_alignment: prefer kelly_stakes (has all candidates),
        # fall back to the columns on the elite_board row itself.
        context_caution_level = kelly_ctx.get("context_caution_level") or _safe_text(row.get("context_caution_level"))
        context_pick_alignment = kelly_ctx.get("context_pick_alignment") or _safe_text(row.get("context_pick_alignment"))
        normalized_rows.append(
            {
                "prediction_date": _safe_text(row.get("prediction_date"), default=prediction_date),
                "run_timestamp": timestamp,
                "player_name": player,
                "player_id": _safe_text(row.get("player_id")),
                "team": _safe_text(row.get("team")) or _safe_text(row.get("team_abbr")),
                "opponent": _safe_text(row.get("opponent")),
                "game_id": _safe_text(row.get("game_id")),
                "market": market,
                "selection": selection,
                "line": _safe_float(line_raw),
                "projection": _safe_float(row.get("model_projection"), _safe_float(row.get("projection"))),
                "edge": edge,
                "abs_edge": abs(edge),
                "odds": _safe_text(row.get("odds")),
                "confidence": _safe_text(row.get("confidence")),
                "quality_score": _safe_float(row.get("quality_score")),
                "qualification_reason": _safe_text(row.get("qualification_reason")),
                "provider_used": provider_used,
                "result_status": result_status,
                "actual_value": "",
                "grading_skip_reason": "",
                "kelly_eligible": kelly_ctx.get("kelly_eligible", ""),
                "skip_reason": kelly_ctx.get("skip_reason", ""),
                "context_caution_level": context_caution_level,
                "context_pick_alignment": context_pick_alignment,
                "line_source": _safe_text(row.get("line_source")),
            }
        )

    existing = _load_csv(pick_history_path, columns=PICK_HISTORY_COLUMNS)
    incoming = pd.DataFrame(normalized_rows, columns=PICK_HISTORY_COLUMNS)
    # Restore graded fields from existing history so pending re-runs don't overwrite results.
    incoming = _preserve_existing_pick_grades(incoming, existing)
    combined = incoming.copy() if existing.empty else pd.concat([existing, incoming], ignore_index=True)
    dedupe_keys = ["prediction_date", "player_name", "market", "selection", "line"]
    combined = combined.drop_duplicates(subset=dedupe_keys, keep="last")
    combined = combined[PICK_HISTORY_COLUMNS].sort_values(["prediction_date", "player_name", "market"]).reset_index(drop=True)
    if not dry_run:
        _write_csv(pick_history_path, combined)
    return {
        "picks_output_path": picks_output_path,
        "pick_history_path": pick_history_path,
        "appended_rows": len(incoming),
        "total_rows": len(combined),
        "dry_run": bool(dry_run),
    }


def _load_actual_results_for_date(prediction_date: str, runtime_root: Path) -> pd.DataFrame:
    """
    Load real graded outcomes generated from provider-backed stats/games.

    This intentionally does NOT read prior graded runtime CSVs to avoid
    circular grading dependencies.
    """
    try:
        outputs_root = runtime_root.parent if runtime_root.name == "runtime" else runtime_root
        ai = CourtVisionAI(out_dir=str(outputs_root))
        graded_df = ai.auto_grade(prediction_date)
        if graded_df is None or graded_df.empty:
            print(
                f"[history_tracking] WARNING: auto_grade returned no results for {prediction_date}. "
                "Picks will remain pending. Possible causes: provider API unavailable, "
                "game not yet final, or date has no matching game data."
            )
            return pd.DataFrame()
        return graded_df.copy()
    except Exception as exc:
        print(
            f"[history_tracking] WARNING: auto_grade raised {type(exc).__name__}: {exc} "
            f"for {prediction_date}. Picks will remain pending."
        )
        return pd.DataFrame()


def _runtime_outputs_root(runtime_root: Path) -> Path:
    return runtime_root.parent if runtime_root.name == "runtime" else runtime_root


def _load_player_stats_for_date(prediction_date: str, runtime_root: Path) -> pd.DataFrame:
    try:
        ai = CourtVisionOperations(
            out_dir=str(_runtime_outputs_root(runtime_root))
        )
        client = operations_client(ai)
        raw_stats = client.get_stats(str(prediction_date), str(prediction_date))
        stats = normalize_operation_stats(ai, raw_stats)
        return stats.copy() if isinstance(stats, pd.DataFrame) else pd.DataFrame()
    except Exception as exc:
        print(
            f"[history_tracking] WARNING: player stats load raised {type(exc).__name__}: {exc} "
            f"for {prediction_date}. Stats fallback unavailable."
        )
        return pd.DataFrame()


def _load_games_for_date(prediction_date: str, runtime_root: Path) -> pd.DataFrame:
    try:
        ai = CourtVisionOperations(
            out_dir=str(_runtime_outputs_root(runtime_root))
        )
        client = operations_client(ai)
        raw_games = client.get_games(str(prediction_date))
        return raw_games.copy() if isinstance(raw_games, pd.DataFrame) else pd.DataFrame()
    except Exception as exc:
        print(
            f"[history_tracking] WARNING: games load raised {type(exc).__name__}: {exc} "
            f"for {prediction_date}. Game finality diagnostics unavailable."
        )
        return pd.DataFrame()


def _normalize_pick_market(value: Any) -> str:
    market = _safe_text(value).lower()
    aliases = {
        "points": "player_points",
        "pts": "player_points",
        "rebounds": "player_rebounds",
        "assists": "player_assists",
        "threes": "player_3pt_made",
        "player_threes": "player_3pt_made",
    }
    return aliases.get(market, market)


def _id_key(value: Any) -> str:
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = _safe_text(value)
    if not text:
        return ""
    try:
        return str(int(float(text)))
    except (TypeError, ValueError):
        return text


def _game_team_abbrs(game_row: pd.Series | dict[str, Any]) -> tuple[str, str]:
    home = game_row.get("home_team", {}) if isinstance(game_row.get("home_team"), dict) else {}
    visitor = game_row.get("visitor_team", {}) if isinstance(game_row.get("visitor_team"), dict) else {}
    home_abbr = (
        _safe_text(home.get("abbreviation") if isinstance(home, dict) else "")
        or _safe_text(game_row.get("home_team_abbr"))
        or _safe_text(game_row.get("home_team"))
    ).upper()
    visitor_abbr = (
        _safe_text(visitor.get("abbreviation") if isinstance(visitor, dict) else "")
        or _safe_text(game_row.get("visitor_team_abbr"))
        or _safe_text(game_row.get("away_team_abbr"))
        or _safe_text(game_row.get("away_team"))
    ).upper()
    return home_abbr, visitor_abbr


def _game_row_is_final(game_row: pd.Series | dict[str, Any]) -> bool:
    status = _safe_text(game_row.get("status")).lower()
    if any(token in status for token in ("final", "complete", "closed")):
        return True
    home_score = _safe_float(game_row.get("home_team_score"), default=float("nan"))
    visitor_score = _safe_float(game_row.get("visitor_team_score"), default=float("nan"))
    has_scores = home_score == home_score and visitor_score == visitor_score
    nonzero_scores = has_scores and (float(home_score) != 0.0 or float(visitor_score) != 0.0)
    return bool(nonzero_scores and status not in {"scheduled", "not started", "pre-game", "pregame"})


def _matching_game_rows(row: pd.Series, games_df: pd.DataFrame) -> list[pd.Series]:
    if games_df.empty:
        return []
    game_id = _id_key(row.get("game_id"))
    team = _safe_text(row.get("team")).upper()
    opponent = _safe_text(row.get("opponent")).upper()
    if opponent == "OPP":
        opponent = ""

    matches: list[pd.Series] = []
    for _, game in games_df.iterrows():
        game_ids = {_id_key(game.get("id")), _id_key(game.get("game_id"))}
        if game_id and game_id in game_ids:
            matches.append(game)
            continue
        home_abbr, visitor_abbr = _game_team_abbrs(game)
        teams = {home_abbr, visitor_abbr}
        if team and opponent and {team, opponent} == teams:
            matches.append(game)
        elif team and not opponent and team in teams:
            matches.append(game)
    return matches


def _game_final_status(row: pd.Series, games_df: pd.DataFrame) -> bool | None:
    matches = _matching_game_rows(row, games_df)
    if not matches:
        return None
    return any(_game_row_is_final(match) for match in matches)


def _date_is_current_or_future(value: Any) -> bool:
    prediction_date = _safe_text(value)
    return bool(prediction_date and prediction_date >= _today_iso())


def _line_result(selection: str, actual_value: float, line: float) -> str:
    if selection == "milestone":
        return "hit" if actual_value >= line else "miss"
    if abs(actual_value - line) < 1e-9:
        return "push"
    if selection == "over":
        return "hit" if actual_value > line else "miss"
    if selection == "under":
        return "hit" if actual_value < line else "miss"
    return "pending"


def _status_from_graded_result(value: Any) -> str:
    result = _safe_text(value).lower()
    if result in {"hit", "win", "won"}:
        return "hit"
    if result in {"miss", "loss", "lost"}:
        return "miss"
    if result == "push":
        return "push"
    return "pending"


def _pick_direct_actual(row: pd.Series, actual_df: pd.DataFrame) -> tuple[str, float | None]:
    if actual_df.empty:
        return "pending", None
    player = _safe_text(row.get("player_name")).lower()
    selection = _safe_text(row.get("selection")).lower()
    market = _normalize_pick_market(row.get("market"))
    line = _safe_float(row.get("line"))

    candidates = actual_df.copy()
    if "entity_name" in candidates.columns:
        candidates = candidates[candidates["entity_name"].astype(str).str.strip().str.lower() == player]
    elif "player_name" in candidates.columns:
        candidates = candidates[candidates["player_name"].astype(str).str.strip().str.lower() == player]
    if "selection" in candidates.columns:
        candidates = candidates[candidates["selection"].astype(str).str.lower() == selection]
    elif "side" in candidates.columns:
        candidates = candidates[candidates["side"].astype(str).str.lower() == selection]
    if "market_type" in candidates.columns:
        market_candidates = candidates["market_type"].map(_normalize_pick_market)
        candidates = candidates[market_candidates == market]
    elif "prop_type" in candidates.columns:
        candidates = candidates[candidates["prop_type"].map(_normalize_pick_market) == market]
    if line is not None and "sportsbook_line" in candidates.columns:
        candidates = candidates[(pd.to_numeric(candidates["sportsbook_line"], errors="coerce") - line).abs() < 1e-6]
    elif line is not None and "line_value" in candidates.columns:
        candidates = candidates[(candidates["line_value"].astype(float) - line).abs() < 1e-6]
    result_col = "result" if "result" in candidates.columns else ("graded_result" if "graded_result" in candidates.columns else "")
    if candidates.empty or not result_col:
        return "pending", None
    status = _status_from_graded_result(candidates.iloc[0].get(result_col))
    actual_value = _safe_float(candidates.iloc[0].get("actual_value"), default=float("nan"))
    actual = float(actual_value) if actual_value == actual_value else None
    return status, actual


def _map_actual_result(row: pd.Series, actual_df: pd.DataFrame) -> str:
    status, _actual = _pick_direct_actual(row, actual_df)
    return status


def _resolve_player_points_actual(row: pd.Series, stats_df: pd.DataFrame) -> tuple[float | None, str]:
    missing_game_id = not _id_key(row.get("game_id"))
    missing_player_id = not _id_key(row.get("player_id"))
    if stats_df.empty:
        reasons = []
        if missing_game_id:
            reasons.append("missing_game_id")
        if missing_player_id:
            reasons.append("missing_player_id")
        reasons.append("actual_stats_not_found")
        return None, ";".join(reasons)

    candidates = stats_df.copy()
    player_id = _id_key(row.get("player_id"))
    game_id = _id_key(row.get("game_id"))
    if player_id and "player_id" in candidates.columns:
        player_mask = candidates["player_id"].map(_id_key) == player_id
        candidates = candidates[player_mask].copy()
    if game_id and "game_id" in candidates.columns:
        game_mask = candidates["game_id"].map(_id_key) == game_id
        candidates = candidates[game_mask].copy()

    if candidates.empty or missing_game_id or missing_player_id:
        fallback = stats_df.copy()
        player = _safe_text(row.get("player_name")).lower()
        team = _safe_text(row.get("team")).upper()
        prediction_date = _safe_text(row.get("prediction_date"))
        if player and "player_name" in fallback.columns:
            fallback = fallback[fallback["player_name"].astype(str).str.strip().str.lower() == player].copy()
        if team and "team_abbr" in fallback.columns:
            fallback = fallback[fallback["team_abbr"].astype(str).str.strip().str.upper() == team].copy()
        if prediction_date and "game_date" in fallback.columns:
            game_dates = pd.to_datetime(fallback["game_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            fallback = fallback[game_dates == prediction_date].copy()
        candidates = fallback

    if candidates.empty:
        reasons = []
        if missing_game_id:
            reasons.append("missing_game_id")
        if missing_player_id:
            reasons.append("missing_player_id")
        reasons.append("player_stat_match_failed")
        return None, ";".join(reasons)
    if "pts" not in candidates.columns:
        return None, "actual_stats_not_found"
    actual = _safe_float(candidates.iloc[0].get("pts"), default=float("nan"))
    if actual != actual:
        return None, "actual_stats_not_found"
    return float(actual), ""


def _grade_pick_row(
    row: pd.Series,
    *,
    actual_df: pd.DataFrame,
    stats_df: pd.DataFrame,
    games_df: pd.DataFrame,
) -> tuple[str, float | None, str]:
    market = _normalize_pick_market(row.get("market"))
    selection = _safe_text(row.get("selection")).lower()
    line = _safe_float(row.get("line"), default=float("nan"))

    if market != "player_points":
        return "pending", None, "unsupported_market"
    if selection not in {"over", "under", "milestone"}:
        return "pending", None, "unsupported_selection"
    if line != line:
        return "pending", None, "actual_stats_not_found"

    final_status = _game_final_status(row, games_df)
    if final_status is False:
        return "pending", None, "game_not_final"

    direct_status, direct_actual = _pick_direct_actual(row, actual_df)
    if direct_status in {"hit", "miss", "push"}:
        return direct_status, direct_actual, ""

    actual_value, reason = _resolve_player_points_actual(row, stats_df)
    if actual_value is None:
        if final_status is None and _date_is_current_or_future(row.get("prediction_date")):
            return "pending", None, PROVIDER_MISSING_FINALITY_REASON
        return "pending", None, reason
    return _line_result(selection, actual_value, float(line)), actual_value, ""


def _write_context_cross_slate_summary(history_df: pd.DataFrame, history_root_path: Path) -> None:
    """Write a cross-slate hit-rate summary grouped by context and eligibility dimensions.

    Aggregates across ALL dates in pick_history. Output:
      {history_root}/performance_context_cross_slate.csv
    Adds sample/calibration metadata so legacy rows are visible but not
    mistaken for usable calibration segments.
    """
    df = history_df.copy()
    # Derive bucketed dimensions from numeric columns
    if "edge" in df.columns:
        df["edge_bucket"] = pd.to_numeric(df["edge"], errors="coerce").apply(
            lambda v: _abs_edge_bucket(v) if v == v else "unknown"
        )
    else:
        df["edge_bucket"] = "unknown"
    if "confidence" in df.columns:
        df["confidence_bucket"] = pd.to_numeric(df["confidence"], errors="coerce").apply(_confidence_bucket)
    else:
        df["confidence_bucket"] = "unknown"

    dim_map = {
        "kelly_eligible": "kelly_eligible",
        "skip_reason": "skip_reason",
        "context_caution_level": "context_caution_level",
        "context_pick_alignment": "context_pick_alignment",
        "market": "market",
        "selection": "selection",
        "edge_bucket": "edge_bucket",
        "confidence_bucket": "confidence_bucket",
    }

    rows: list[dict[str, Any]] = []
    for col, display_name in dim_map.items():
        if col not in df.columns:
            continue
        group_col = _context_group_value(df[col])
        for group_val, seg in df.assign(**{col: group_col}).groupby(col, sort=True):
            result_status = seg["result_status"].fillna("").astype(str).str.strip().str.lower()
            hits = int((result_status == "hit").sum())
            misses = int((result_status == "miss").sum())
            pushes = int((result_status == "push").sum())
            pending = int((result_status == "pending").sum())
            graded_total = hits + misses
            sample_status = _sample_status(graded_total)
            exclusion_reasons = _context_calibration_reasons(display_name, str(group_val), graded_total)
            rows.append({
                "dimension": display_name,
                "group_value": group_val,
                "total": int(len(seg)),
                "hits": hits,
                "misses": misses,
                "pushes": pushes,
                "pending": pending,
                "graded_total": graded_total,
                "hit_rate": round(float(hits / graded_total), 4) if graded_total else "",
                "sample_status": sample_status,
                "calibration_eligible": not exclusion_reasons,
                "calibration_exclusion_reason": ";".join(exclusion_reasons),
            })

    _write_csv(
        history_root_path / "performance_context_cross_slate.csv",
        pd.DataFrame(rows, columns=CONTEXT_CROSS_SLATE_COLUMNS),
    )


def update_performance_summaries(
    history_root: str | Path = "data/history",
    runtime_root: str | Path = "outputs/runtime",
) -> None:
    history_root_path = Path(history_root)
    runtime_root_path = Path(runtime_root)
    pick_history_path = history_root_path / "pick_history.csv"
    performance_daily_path = history_root_path / "performance_summary.csv"

    history_df = _load_csv(pick_history_path, columns=PICK_HISTORY_COLUMNS)
    if history_df.empty:
        _write_csv(performance_daily_path, pd.DataFrame(columns=[
            "date", "total_picks", "hits", "misses", "pushes", "pending", "hit_rate",
            "overs_count", "overs_hit_rate", "unders_count", "unders_hit_rate",
            "avg_edge", "avg_abs_edge", "max_team_exposure", "max_game_exposure",
        ]))
        per_date_cols = ["date", "group", "total", "hits", "misses", "pushes", "pending", "hit_rate"]
        for name in ("performance_by_market.csv", "performance_by_selection.csv", "performance_by_edge_bucket.csv", "performance_by_qualification_reason.csv"):
            _write_csv(history_root_path / name, pd.DataFrame(columns=per_date_cols))
        _write_csv(
            history_root_path / "performance_context_cross_slate.csv",
            pd.DataFrame(columns=CONTEXT_CROSS_SLATE_COLUMNS),
        )
        return

    history_df["prediction_date"] = history_df["prediction_date"].astype(str)
    history_df["selection"] = history_df["selection"].astype(str).str.lower()
    history_df["result_status"] = history_df["result_status"].fillna("").astype(str).str.strip().str.lower()
    history_df["edge"] = pd.to_numeric(history_df["edge"], errors="coerce").fillna(0.0)
    history_df["abs_edge"] = pd.to_numeric(history_df["abs_edge"], errors="coerce").fillna(history_df["edge"].abs())

    daily_rows: list[dict[str, Any]] = []
    for prediction_date, group in history_df.groupby("prediction_date"):
        hits = int((group["result_status"] == "hit").sum())
        misses = int((group["result_status"] == "miss").sum())
        pushes = int((group["result_status"] == "push").sum())
        pending = int((group["result_status"] == "pending").sum())
        graded_total = hits + misses
        hit_rate = float(hits / graded_total) if graded_total else 0.0

        overs = group[group["selection"] == "over"]
        unders = group[group["selection"] == "under"]
        overs_graded = int((overs["result_status"].isin(["hit", "miss"])).sum())
        unders_graded = int((unders["result_status"].isin(["hit", "miss"])).sum())
        overs_hit_rate = float((overs["result_status"] == "hit").sum() / overs_graded) if overs_graded else 0.0
        unders_hit_rate = float((unders["result_status"] == "hit").sum() / unders_graded) if unders_graded else 0.0

        audit_path = runtime_root_path / "operator" / f"elite_pipeline_audit_summary_{prediction_date}.json"
        max_team_exposure = 0
        max_game_exposure = 0
        if audit_path.exists():
            try:
                payload = json.loads(audit_path.read_text(encoding="utf-8"))
                summary = payload.get("summary") or {}
                ba = summary.get("board_analytics") or {}
                max_team_exposure = int(ba.get("max_team_exposure", summary.get("elite_max_team_exposure", 0)) or 0)
                max_game_exposure = int(ba.get("max_game_exposure", summary.get("elite_max_game_exposure", 0)) or 0)
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                pass

        daily_rows.append(
            {
                "date": prediction_date,
                "total_picks": int(len(group)),
                "hits": hits,
                "misses": misses,
                "pushes": pushes,
                "pending": pending,
                "hit_rate": round(hit_rate, 4),
                "overs_count": int(len(overs)),
                "overs_hit_rate": round(overs_hit_rate, 4),
                "unders_count": int(len(unders)),
                "unders_hit_rate": round(unders_hit_rate, 4),
                "avg_edge": round(float(group["edge"].mean()), 4) if not group.empty else 0.0,
                "avg_abs_edge": round(float(group["abs_edge"].mean()), 4) if not group.empty else 0.0,
                "max_team_exposure": max_team_exposure,
                "max_game_exposure": max_game_exposure,
            }
        )

    daily_df = pd.DataFrame(daily_rows).sort_values("date").reset_index(drop=True)
    _write_csv(performance_daily_path, daily_df)

    def grouped_summary(group_col: str, filename: str) -> None:
        rows: list[dict[str, Any]] = []
        for prediction_date, date_group in history_df.groupby("prediction_date"):
            for group_name, seg in date_group.groupby(group_col):
                hits = int((seg["result_status"] == "hit").sum())
                misses = int((seg["result_status"] == "miss").sum())
                pushes = int((seg["result_status"] == "push").sum())
                pending = int((seg["result_status"] == "pending").sum())
                graded_total = hits + misses
                rows.append(
                    {
                        "date": prediction_date,
                        "group": _safe_text(group_name, default="unknown"),
                        "total": int(len(seg)),
                        "hits": hits,
                        "misses": misses,
                        "pushes": pushes,
                        "pending": pending,
                        "hit_rate": round(float(hits / graded_total), 4) if graded_total else 0.0,
                    }
                )
        _write_csv(history_root_path / filename, pd.DataFrame(rows, columns=["date", "group", "total", "hits", "misses", "pushes", "pending", "hit_rate"]))

    history_df["edge_bucket"] = history_df["edge"].apply(_abs_edge_bucket)
    grouped_summary("market", "performance_by_market.csv")
    grouped_summary("selection", "performance_by_selection.csv")
    grouped_summary("edge_bucket", "performance_by_edge_bucket.csv")
    grouped_summary("qualification_reason", "performance_by_qualification_reason.csv")

    _write_context_cross_slate_summary(history_df, history_root_path)


def grade_completed_picks(
    history_root: str | Path = "data/history",
    runtime_root: str | Path = "outputs/runtime",
    prediction_date: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    history_root_path = Path(history_root)
    runtime_root_path = Path(runtime_root)
    # Store the input prediction date to avoid variable overshadowing in loops
    input_prediction_date = prediction_date
    
    # Grade incubator picks and write their reports first regardless of pick_history being empty
    try:
        from courtvision.reporting.incubator_performance import (
            grade_incubator_picks,
            write_incubator_performance_report,
        )
        grade_incubator_picks(
            history_root=history_root_path,
            runtime_root=runtime_root_path,
            prediction_date=input_prediction_date,
            dry_run=dry_run,
        )
        report_date = input_prediction_date or _today_iso()
        write_incubator_performance_report(
            prediction_date=report_date,
            runtime_root=runtime_root_path,
            history_root=history_root_path,
            dry_run=dry_run,
        )
    except Exception as exc:
        print(f"[history_tracking] WARNING: Incubator grading/reporting failed: {exc}")

    pick_history_path = history_root_path / "pick_history.csv"
    pick_history_df = _load_csv(pick_history_path, columns=PICK_HISTORY_COLUMNS)
    if pick_history_df.empty:
        if not dry_run:
            update_performance_summaries(history_root=history_root_path, runtime_root=runtime_root_path)
        return {
            "updated_rows": 0,
            "pending_rows": 0,
            "unsupported_rows": 0,
            "void_rows": 0,
            "skip_reasons": {},
            "dry_run": bool(dry_run),
        }

    pick_history_df = pick_history_df.reindex(columns=PICK_HISTORY_COLUMNS)
    for column in ("actual_value", "grading_skip_reason"):
        pick_history_df[column] = pick_history_df[column].astype("object").where(
            pick_history_df[column].notna(),
            "",
        )
    pending_mask = pick_history_df["result_status"].astype(str).str.lower() == "pending"
    if prediction_date:
        pending_mask &= pick_history_df["prediction_date"].astype(str) == str(prediction_date)
    updated = 0
    skip_reasons: Counter[str] = Counter()
    for prediction_date in pick_history_df.loc[pending_mask, "prediction_date"].astype(str).unique():
        actual_df = _load_actual_results_for_date(prediction_date, runtime_root=runtime_root_path)
        stats_df = _load_player_stats_for_date(prediction_date, runtime_root=runtime_root_path)
        games_df = _load_games_for_date(prediction_date, runtime_root=runtime_root_path)
        date_mask = (pick_history_df["prediction_date"].astype(str) == prediction_date) & pending_mask
        date_rows = pick_history_df[date_mask].copy()
        if date_rows.empty:
            continue
        if "actual_value" not in date_rows.columns:
            date_rows["actual_value"] = ""
        if "grading_skip_reason" not in date_rows.columns:
            date_rows["grading_skip_reason"] = ""

        for idx, row in date_rows.iterrows():
            result_status, actual_value, skip_reason = _grade_pick_row(
                row,
                actual_df=actual_df,
                stats_df=stats_df,
                games_df=games_df,
            )
            if result_status == PENDING_RESULT_STATUS:
                skip_reason = skip_reason or "actual_stats_not_found"
                result_status = _unresolved_result_status(skip_reason, prediction_date)
            date_rows.at[idx, "result_status"] = result_status
            if result_status != PENDING_RESULT_STATUS:
                updated += 1
                date_rows.at[idx, "actual_value"] = actual_value if actual_value is not None else ""
                date_rows.at[idx, "grading_skip_reason"] = "" if result_status in {"hit", "miss", "push"} else skip_reason
            else:
                date_rows.at[idx, "grading_skip_reason"] = skip_reason
            for reason in _split_grading_reasons(date_rows.at[idx, "grading_skip_reason"]):
                skip_reasons[reason] += 1
        pick_history_df.loc[date_rows.index, "result_status"] = date_rows["result_status"]
        pick_history_df.loc[date_rows.index, "actual_value"] = date_rows["actual_value"]
        pick_history_df.loc[date_rows.index, "grading_skip_reason"] = date_rows["grading_skip_reason"]

        runtime_graded_path = runtime_root_path / "history" / f"graded_picks_{prediction_date}.csv"
        if not dry_run:
            _write_csv(runtime_graded_path, date_rows)

    if not dry_run:
        _write_csv(pick_history_path, pick_history_df[PICK_HISTORY_COLUMNS])
        update_performance_summaries(history_root=history_root_path, runtime_root=runtime_root_path)

    pending_rows = int((pick_history_df["result_status"].astype(str).str.lower() == "pending").sum())
    unsupported_rows = int((pick_history_df["result_status"].astype(str).str.lower() == UNSUPPORTED_RESULT_STATUS).sum())
    void_rows = int((pick_history_df["result_status"].astype(str).str.lower() == VOID_RESULT_STATUS).sum())
    return {
        "updated_rows": updated,
        "pending_rows": pending_rows,
        "unsupported_rows": unsupported_rows,
        "void_rows": void_rows,
        "skip_reasons": dict(sorted(skip_reasons.items())),
        "dry_run": bool(dry_run),
    }
