from __future__ import annotations

import csv
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPORT_VERSION = "1.0"
HISTORY_FILENAME = "shadow_candidate_lane_history.csv"
REPORT_FILE_PREFIX = "shadow_candidate_lane_performance"
PAPER_ONLY_DISCLAIMER = "Shadow candidate lane is paper-only and not a betting input."

UNDER_ALIGNED_RESEARCH = "UNDER_ALIGNED_RESEARCH"
COMBO_OVER_WEAK_POSITIVE_RESEARCH = "COMBO_OVER_WEAK_POSITIVE_RESEARCH"
NEAR_ELITE_RESEARCH = "NEAR_ELITE_RESEARCH"
INCUBATOR_RESEARCH = "INCUBATOR_RESEARCH"
TRACKED_LANES: tuple[str, ...] = (
    UNDER_ALIGNED_RESEARCH,
    COMBO_OVER_WEAK_POSITIVE_RESEARCH,
    NEAR_ELITE_RESEARCH,
    INCUBATOR_RESEARCH,
)

PENDING_RESULT = "pending"
OPEN_GAME_STATUS = "open_game_pending"
GAME_NOT_FINAL_REASON = "game_not_final"
GRADED_RESULTS = {"hit", "miss", "push"}
FINAL_RESULTS = {"hit", "miss", "push", "void", "unsupported"}
SUPPORTED_SELECTIONS = {"over", "under", "milestone"}
SUPPORTED_MARKETS = {
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_3pt_made",
    "player_steals",
    "player_blocks",
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
}
COMBO_MARKET_COMPONENTS: dict[str, tuple[str, ...]] = {
    "player_points_rebounds": ("player_points", "player_rebounds"),
    "player_points_assists": ("player_points", "player_assists"),
    "player_rebounds_assists": ("player_rebounds", "player_assists"),
    "player_points_rebounds_assists": ("player_points", "player_rebounds", "player_assists"),
}
MARKET_ALIASES = {
    "points": "player_points",
    "pts": "player_points",
    "rebounds": "player_rebounds",
    "reb": "player_rebounds",
    "assists": "player_assists",
    "ast": "player_assists",
    "threes": "player_3pt_made",
    "3pt_made": "player_3pt_made",
    "three_pointers_made": "player_3pt_made",
    "player_threes": "player_3pt_made",
    "steals": "player_steals",
    "stl": "player_steals",
    "blocks": "player_blocks",
    "blk": "player_blocks",
    "points_rebounds": "player_points_rebounds",
    "points_assists": "player_points_assists",
    "rebounds_assists": "player_rebounds_assists",
    "points_rebounds_assists": "player_points_rebounds_assists",
}
STAT_COLUMNS: dict[str, tuple[str, ...]] = {
    "player_points": ("pts", "points", "actual_points"),
    "player_rebounds": ("reb", "rebounds", "actual_rebounds"),
    "player_assists": ("ast", "assists", "actual_assists"),
    "player_3pt_made": ("fg3m", "threes", "three_pointers_made", "actual_3pt_made"),
    "player_steals": ("stl", "steals", "actual_steals"),
    "player_blocks": ("blk", "blocks", "actual_blocks"),
}

HISTORY_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "source_artifact_date",
    "source_board",
    "lane",
    "rank",
    "rank_score",
    "player",
    "player_id",
    "team",
    "opponent",
    "market_type",
    "selection",
    "line",
    "odds",
    "source_game_id",
    "game_id",
    "model_projection",
    "edge",
    "confidence",
    "quality_score",
    "selection_score",
    "context_pick_alignment",
    "context_edge_label",
    "context_caution_level",
    "source_rejection_reason",
    "confidence_bucket",
    "edge_bucket",
    "historical_bucket_key",
    "historical_recommendation",
    "historical_graded_rows",
    "historical_hit_rate",
    "historical_roi",
    "historical_clv_coverage_rate",
    "promotion_status",
    "real_money_eligible",
    "kelly_eligible",
    "elite_eligible",
    "shadow_only",
    "result_status",
    "actual_value",
    "hit",
    "miss",
    "push",
    "flat_profit_loss",
    "graded_at_utc",
    "grading_status",
    "grading_reason",
)

GRADE_PRESERVED_COLUMNS: tuple[str, ...] = (
    "result_status",
    "actual_value",
    "hit",
    "miss",
    "push",
    "flat_profit_loss",
    "graded_at_utc",
    "grading_status",
    "grading_reason",
)

SEGMENT_DIMENSIONS: tuple[str, ...] = (
    "lane",
    "market_type",
    "selection",
    "context_caution_level",
    "context_edge_label",
    "source_rejection_reason",
    "confidence_bucket",
    "edge_bucket",
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


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_lower(value, default="") in {"true", "1", "yes", "y"}


def _normalize_market(value: Any) -> str:
    text = _safe_lower(value, default="").replace(" ", "_")
    return MARKET_ALIASES.get(text, text)


def _line_token(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return _safe_lower(value, default="")
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _first_text(row: pd.Series | dict[str, Any], columns: tuple[str, ...], default: str = "") -> str:
    for column in columns:
        value = row.get(column) if column in row else None
        text = _safe_text(value)
        if text:
            return text
    return default


def _player(row: pd.Series | dict[str, Any]) -> str:
    return _first_text(row, ("player", "player_name", "entity_name"), default="Unknown")


def _team(row: pd.Series | dict[str, Any]) -> str:
    return _first_text(row, ("team", "team_abbr", "resolved_team_abbr", "canonical_team_abbr")).upper()


def _source_game_id(row: pd.Series | dict[str, Any]) -> str:
    return _first_text(row, ("source_game_id", "game_id", "id"))


def _lane(row: pd.Series | dict[str, Any]) -> str:
    return _first_text(row, ("lane", "research_lane"), default="unknown")


def _confidence_bucket(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "unknown"
    if number < 0.55:
        return "below_0.55"
    if number < 0.65:
        return "0.55-0.65"
    if number < 0.75:
        return "0.65-0.75"
    if number < 0.85:
        return "0.75-0.85"
    return "0.85+"


def _edge_bucket(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "unknown"
    abs_edge = abs(number)
    if abs_edge >= 5.0:
        return "5+"
    if abs_edge >= 3.0:
        return "3-5"
    if abs_edge >= 2.0:
        return "2-3"
    if abs_edge >= 1.0:
        return "1-2"
    return "<1"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except (OSError, ValueError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def _write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def history_path_for_root(history_root: str | Path = "data/history") -> Path:
    return Path(history_root) / HISTORY_FILENAME


def performance_paths_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path, Path]:
    runtime_root_path = Path(runtime_root)
    return (
        runtime_root_path / "operator" / f"{REPORT_FILE_PREFIX}_{prediction_date}.txt",
        runtime_root_path / "operator" / f"{REPORT_FILE_PREFIX}_{prediction_date}.csv",
        runtime_root_path / "diagnostics" / f"{REPORT_FILE_PREFIX}_{prediction_date}.json",
    )


def _dedupe_key(row: pd.Series | dict[str, Any]) -> tuple[str, str, str, str, str, str, str, str, str]:
    player_id = _safe_lower(row.get("player_id"), default="")
    identity = player_id or _safe_lower(_player(row), default="")
    return (
        _safe_text(row.get("prediction_date")),
        identity,
        _safe_lower(_team(row), default=""),
        _safe_lower(row.get("opponent"), default=""),
        _normalize_market(row.get("market_type")),
        _safe_lower(row.get("selection"), default=""),
        _line_token(row.get("line")),
        _safe_lower(_source_game_id(row), default=""),
        _lane(row),
    )


def _normalize_shadow_board_row(row: pd.Series, prediction_date: str) -> dict[str, Any]:
    confidence = _safe_float(row.get("confidence"))
    edge = _safe_float(row.get("edge"))
    context_edge_label = _first_text(row, ("context_edge_label", "context_pick_alignment"), default="unknown")
    return {
        "prediction_date": _safe_text(row.get("prediction_date"), default=prediction_date),
        "source_artifact_date": _safe_text(row.get("source_artifact_date")),
        "source_board": _safe_text(row.get("source_board")),
        "lane": _lane(row),
        "rank": _safe_text(row.get("rank")),
        "rank_score": _safe_float(row.get("rank_score")),
        "player": _player(row),
        "player_id": _safe_text(row.get("player_id")),
        "team": _team(row),
        "opponent": _safe_text(row.get("opponent")).upper(),
        "market_type": _normalize_market(row.get("market_type") or row.get("market")),
        "selection": _safe_lower(row.get("selection"), default="unknown"),
        "line": _safe_float(row.get("line")),
        "odds": _safe_float(row.get("odds")),
        "source_game_id": _source_game_id(row),
        "game_id": _safe_text(row.get("game_id") or row.get("source_game_id")),
        "model_projection": _safe_float(row.get("model_projection") or row.get("projection")),
        "edge": edge,
        "confidence": confidence,
        "quality_score": _safe_float(row.get("quality_score")),
        "selection_score": _safe_float(row.get("selection_score")),
        "context_pick_alignment": _safe_lower(row.get("context_pick_alignment"), default="unknown"),
        "context_edge_label": _safe_lower(context_edge_label, default="unknown"),
        "context_caution_level": _safe_lower(row.get("context_caution_level"), default="unknown"),
        "source_rejection_reason": _safe_lower(row.get("source_rejection_reason"), default="unknown"),
        "confidence_bucket": _safe_text(row.get("confidence_bucket")) or _confidence_bucket(confidence),
        "edge_bucket": _safe_text(row.get("edge_bucket")) or _edge_bucket(edge),
        "historical_bucket_key": _safe_text(row.get("historical_bucket_key")),
        "historical_recommendation": _safe_text(row.get("historical_recommendation")),
        "historical_graded_rows": _safe_text(row.get("historical_graded_rows")),
        "historical_hit_rate": _safe_text(row.get("historical_hit_rate")),
        "historical_roi": _safe_text(row.get("historical_roi")),
        "historical_clv_coverage_rate": _safe_text(row.get("historical_clv_coverage_rate")),
        "promotion_status": _safe_text(row.get("promotion_status"), default="SHADOW_ONLY_DO_NOT_PROMOTE"),
        "real_money_eligible": False,
        "kelly_eligible": False,
        "elite_eligible": False,
        "shadow_only": True,
        "result_status": PENDING_RESULT,
        "actual_value": "",
        "hit": False,
        "miss": False,
        "push": False,
        "flat_profit_loss": "",
        "graded_at_utc": "",
        "grading_status": OPEN_GAME_STATUS,
        "grading_reason": GAME_NOT_FINAL_REASON,
    }


def _read_history(path: Path) -> pd.DataFrame:
    df = _read_csv(path)
    if df.empty:
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    for column in HISTORY_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    ordered = list(HISTORY_COLUMNS) + [column for column in df.columns if column not in HISTORY_COLUMNS]
    return df[ordered]


def _preserve_existing_grades(incoming: pd.DataFrame, existing: pd.DataFrame) -> pd.DataFrame:
    if incoming.empty or existing.empty:
        return incoming

    graded_existing = existing[
        existing["result_status"].astype(str).str.strip().str.lower().isin(FINAL_RESULTS)
    ].copy()
    if graded_existing.empty:
        return incoming

    lookup: dict[tuple[str, str, str, str, str, str, str, str, str], pd.Series] = {}
    for _, row in graded_existing.iterrows():
        lookup[_dedupe_key(row)] = row

    preserved = incoming.copy()
    for column in GRADE_PRESERVED_COLUMNS:
        if column in preserved.columns:
            preserved[column] = preserved[column].astype("object")

    for idx, row in preserved.iterrows():
        existing_row = lookup.get(_dedupe_key(row))
        if existing_row is None:
            continue
        for column in GRADE_PRESERVED_COLUMNS:
            if column in existing_row.index and column in preserved.columns:
                preserved.at[idx, column] = existing_row.get(column)
    return preserved


def _with_dedupe_columns(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    keys = [_dedupe_key(row) for _, row in working.iterrows()]
    for position in range(9):
        working[f"_dedupe_{position}"] = [key[position] for key in keys]
    return working


def persist_daily_shadow_candidate_lane(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    dry_run: bool = False,
) -> dict[str, Any]:
    runtime_root_path = Path(runtime_root)
    history_path = history_path_for_root(history_root)
    board_path = runtime_root_path / "operator" / f"shadow_candidate_lane_{prediction_date}.csv"
    existing = _read_history(history_path)
    board_df = _read_csv(board_path)

    if board_df.empty:
        return {
            "shadow_candidate_lane_path": str(board_path),
            "shadow_candidate_lane_history_path": str(history_path),
            "incoming_rows": 0,
            "persisted_rows": 0,
            "total_rows": int(len(existing)),
            "dry_run": bool(dry_run),
            "note": "Shadow candidate lane CSV is missing or empty.",
        }

    normalized_rows = [_normalize_shadow_board_row(row, prediction_date) for _, row in board_df.iterrows()]
    incoming = pd.DataFrame(normalized_rows, columns=HISTORY_COLUMNS)
    incoming = _preserve_existing_grades(incoming, existing)
    combined = incoming if existing.empty else pd.concat([existing, incoming], ignore_index=True)
    combined = combined.reindex(columns=HISTORY_COLUMNS)
    working = _with_dedupe_columns(combined)
    dedupe_columns = [f"_dedupe_{position}" for position in range(9)]
    working = working.drop_duplicates(subset=dedupe_columns, keep="last")
    combined = (
        working.drop(columns=dedupe_columns)
        .sort_values(["prediction_date", "lane", "player", "market_type", "selection", "line"])
        .reset_index(drop=True)
    )

    if not dry_run:
        _write_csv(history_path, combined[list(HISTORY_COLUMNS)])

    same_date = combined[combined["prediction_date"].astype(str).eq(str(prediction_date))]
    return {
        "shadow_candidate_lane_path": str(board_path),
        "shadow_candidate_lane_history_path": str(history_path),
        "incoming_rows": int(len(incoming)),
        "persisted_rows": int(len(same_date)),
        "total_rows": int(len(combined)),
        "dry_run": bool(dry_run),
        "all_rows_real_money_eligible_false": _all_false(combined, "real_money_eligible"),
        "all_rows_kelly_eligible_false": _all_false(combined, "kelly_eligible"),
        "all_rows_elite_eligible_false": _all_false(combined, "elite_eligible"),
        "all_rows_shadow_only_true": _all_true(combined, "shadow_only"),
    }


def _all_false(df: pd.DataFrame, column: str) -> bool:
    if df.empty or column not in df.columns:
        return True
    return not df[column].map(_truthy).any()


def _all_true(df: pd.DataFrame, column: str) -> bool:
    if df.empty or column not in df.columns:
        return True
    return bool(df[column].map(_truthy).all())


def _actual_candidate_paths(runtime_root: Path, prediction_date: str) -> list[Path]:
    return [
        runtime_root / "history" / "result_feedback.csv",
        runtime_root / "research" / f"grading_results_{prediction_date}.csv",
        runtime_root / "history" / f"graded_picks_{prediction_date}.csv",
    ]


def _load_actual_results(runtime_root: Path, prediction_date: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    seen_paths: set[Path] = set()
    for path in _actual_candidate_paths(runtime_root, prediction_date):
        resolved = path.resolve()
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        frame = _read_csv(path)
        if frame.empty:
            continue
        if "prediction_date" in frame.columns:
            frame = frame[frame["prediction_date"].astype(str).eq(str(prediction_date))].copy()
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def _selection(row: pd.Series | dict[str, Any]) -> str:
    return _safe_lower(row.get("selection") or row.get("side"), default="")


def _row_market(row: pd.Series | dict[str, Any]) -> str:
    for column in ("market_type", "market", "prop_type", "raw_prop_type"):
        value = _normalize_market(row.get(column))
        if value:
            return value
    return ""


def _line_value(row: pd.Series | dict[str, Any]) -> float | None:
    for column in ("line", "sportsbook_line", "line_value"):
        if column in row:
            value = _safe_float(row.get(column))
            if value is not None:
                return value
    return None


def _actual_value(row: pd.Series | dict[str, Any]) -> float | None:
    for column in ("actual_value", "stat_value", "actual", "final_value"):
        if column in row:
            value = _safe_float(row.get(column))
            if value is not None:
                return value
    market = _row_market(row)
    for column in STAT_COLUMNS.get(market, ()):
        if column in row:
            value = _safe_float(row.get(column))
            if value is not None:
                return value
    return None


def _normalized_result(value: Any) -> str:
    text = _safe_lower(value, default="")
    if text in {"hit", "win", "won", "true", "1"}:
        return "hit"
    if text in {"miss", "loss", "lost", "false", "0"}:
        return "miss"
    if text == "push":
        return "push"
    if text in {"void", "canceled", "cancelled", "no_action"}:
        return "void"
    if text in {"unsupported", "unsupported_market", "unsupported_selection"}:
        return "unsupported"
    return PENDING_RESULT


def _direct_result(row: pd.Series | dict[str, Any]) -> str:
    for column in ("result_status", "graded_result", "result"):
        if column in row:
            result = _normalized_result(row.get(column))
            if result != PENDING_RESULT:
                return result
    if _truthy(row.get("hit")):
        return "hit"
    if _truthy(row.get("miss")):
        return "miss"
    if _truthy(row.get("push")):
        return "push"
    return PENDING_RESULT


def _line_result(selection: str, actual_value: float, line: float) -> str:
    if selection == "milestone":
        return "hit" if actual_value >= line else "miss"
    if abs(float(actual_value) - float(line)) < 1e-9:
        return "push"
    if selection == "over":
        return "hit" if actual_value > line else "miss"
    if selection == "under":
        return "hit" if actual_value < line else "miss"
    return PENDING_RESULT


def _matching_actual_rows(row: pd.Series, actual_df: pd.DataFrame) -> pd.DataFrame:
    if actual_df.empty:
        return pd.DataFrame()
    candidates = actual_df.copy()
    row_player_id = _safe_text(row.get("player_id")).lower()
    matched_identity = False
    if row_player_id and "player_id" in candidates.columns:
        by_id = candidates[candidates["player_id"].astype(str).str.strip().str.lower().eq(row_player_id)].copy()
        if not by_id.empty:
            candidates = by_id
            matched_identity = True
    if not matched_identity:
        player = _safe_lower(row.get("player"), default="")
        for column in ("player_name", "entity_name", "player"):
            if column in candidates.columns and player:
                by_name = candidates[candidates[column].astype(str).str.strip().str.lower().eq(player)].copy()
                if not by_name.empty:
                    candidates = by_name
                    break

    market = _row_market(row)
    if "market_type" in candidates.columns:
        candidates = candidates[candidates["market_type"].map(_normalize_market).eq(market)].copy()
    elif "market" in candidates.columns:
        candidates = candidates[candidates["market"].map(_normalize_market).eq(market)].copy()
    elif "prop_type" in candidates.columns:
        candidates = candidates[candidates["prop_type"].map(_normalize_market).eq(market)].copy()

    selection = _selection(row)
    if "selection" in candidates.columns:
        candidates = candidates[candidates["selection"].astype(str).str.strip().str.lower().eq(selection)].copy()
    elif "side" in candidates.columns:
        candidates = candidates[candidates["side"].astype(str).str.strip().str.lower().eq(selection)].copy()

    line = _line_value(row)
    if line is not None:
        for column in ("sportsbook_line", "line", "line_value"):
            if column in candidates.columns:
                numeric = pd.to_numeric(candidates[column], errors="coerce")
                exact = candidates[(numeric - float(line)).abs() < 1e-6].copy()
                if not exact.empty:
                    candidates = exact
                break
    return candidates


def _grade_from_direct_actual(row: pd.Series, actual_df: pd.DataFrame) -> tuple[str, float | None, str]:
    matches = _matching_actual_rows(row, actual_df)
    if matches.empty:
        return PENDING_RESULT, None, GAME_NOT_FINAL_REASON
    match = matches.iloc[0]
    direct = _direct_result(match)
    actual = _actual_value(match)
    if direct in FINAL_RESULTS:
        if actual is None and direct in GRADED_RESULTS:
            return "void", None, "actual_value_missing"
        return direct, actual, ""
    if actual is not None:
        line = _line_value(row)
        if line is None:
            return "void", actual, "missing_line"
        return _line_result(_selection(row), actual, float(line)), actual, ""
    return PENDING_RESULT, None, GAME_NOT_FINAL_REASON


def _component_key(
    prediction_date: str,
    player: str,
    market_type: str,
    team: str = "",
) -> tuple[str, str, str, str]:
    return (
        _safe_text(prediction_date),
        _safe_lower(player, default=""),
        _normalize_market(market_type),
        _safe_lower(team, default=""),
    )


def _component_actual_lookup(actual_df: pd.DataFrame) -> dict[tuple[str, str, str, str], float]:
    lookup: dict[tuple[str, str, str, str], float] = {}
    if actual_df.empty:
        return lookup
    for _, actual_row in actual_df.iterrows():
        prediction_date = _safe_text(actual_row.get("prediction_date"))
        player = _player(actual_row)
        team = _team(actual_row)
        market = _row_market(actual_row)
        if market not in STAT_COLUMNS:
            continue
        actual = _actual_value(actual_row)
        if actual is None:
            continue
        if team:
            lookup[_component_key(prediction_date, player, market, team)] = float(actual)
        lookup[_component_key(prediction_date, player, market)] = float(actual)
    return lookup


def _grade_from_components(
    row: pd.Series,
    component_lookup: dict[tuple[str, str, str, str], float],
) -> tuple[str, float | None, str]:
    market = _row_market(row)
    components = COMBO_MARKET_COMPONENTS.get(market, ())
    if not components:
        return PENDING_RESULT, None, "not_combo_market"
    prediction_date = _safe_text(row.get("prediction_date"))
    player = _safe_text(row.get("player"))
    team = _safe_text(row.get("team"))
    values: list[float] = []
    missing: list[str] = []
    for component_market in components:
        keys = []
        if team:
            keys.append(_component_key(prediction_date, player, component_market, team))
        keys.append(_component_key(prediction_date, player, component_market))
        value = next((component_lookup[key] for key in keys if key in component_lookup), None)
        if value is None:
            missing.append(component_market)
        else:
            values.append(float(value))
    if missing:
        return PENDING_RESULT, None, "missing_component_stats"
    line = _line_value(row)
    if line is None:
        return "void", None, "missing_line"
    actual = float(sum(values))
    return _line_result(_selection(row), actual, float(line)), actual, ""


def _flat_profit_loss(odds: Any, result_status: str) -> float | str:
    if result_status == "miss":
        return -1.0
    if result_status == "push":
        return 0.0
    if result_status != "hit":
        return ""
    american = _safe_float(odds)
    if american is None or american == 0:
        return ""
    if american > 0:
        return round(float(american) / 100.0, 6)
    return round(100.0 / abs(float(american)), 6)


def _set_grade(
    frame: pd.DataFrame,
    idx: Any,
    *,
    result_status: str,
    actual_value: float | None,
    grading_status: str,
    grading_reason: str,
    graded_at_utc: str = "",
) -> None:
    frame.at[idx, "result_status"] = result_status
    frame.at[idx, "actual_value"] = "" if actual_value is None else actual_value
    frame.at[idx, "hit"] = result_status == "hit"
    frame.at[idx, "miss"] = result_status == "miss"
    frame.at[idx, "push"] = result_status == "push"
    frame.at[idx, "flat_profit_loss"] = _flat_profit_loss(frame.at[idx, "odds"], result_status)
    frame.at[idx, "graded_at_utc"] = graded_at_utc
    frame.at[idx, "grading_status"] = grading_status
    frame.at[idx, "grading_reason"] = grading_reason


def grade_shadow_candidate_lane_history(
    *,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    prediction_date: str | None = None,
    dry_run: bool = False,
    replace_existing: bool = False,
) -> dict[str, Any]:
    runtime_root_path = Path(runtime_root)
    history_path = history_path_for_root(history_root)
    history_df = _read_history(history_path)
    if history_df.empty:
        return {
            "shadow_candidate_lane_history_path": str(history_path),
            "selected_dates": [] if prediction_date else [],
            "updated_rows": 0,
            "would_update_rows": 0,
            "pending_rows": 0,
            "graded_rows": 0,
            "dry_run": bool(dry_run),
            "skip_reasons": {},
            "note": "Shadow candidate lane history is missing or empty.",
        }

    selected_dates = (
        [str(prediction_date)]
        if prediction_date
        else sorted(history_df["prediction_date"].astype(str).dropna().unique().tolist())
    )
    selected_date_set = set(selected_dates)
    pending_mask = history_df["prediction_date"].astype(str).isin(selected_date_set)
    if not replace_existing:
        pending_mask &= ~history_df["result_status"].astype(str).str.lower().isin(FINAL_RESULTS)

    updated = history_df.copy()
    for column in GRADE_PRESERVED_COLUMNS:
        if column in updated.columns:
            updated[column] = updated[column].astype("object")

    updated_rows = 0
    would_update_rows = 0
    skip_reasons: Counter[str] = Counter()
    graded_at = datetime.now(timezone.utc).isoformat()

    for date in selected_dates:
        actual_df = _load_actual_results(runtime_root_path, date)
        component_lookup = _component_actual_lookup(actual_df)
        date_indices = updated.index[pending_mask & updated["prediction_date"].astype(str).eq(date)].tolist()
        for idx in date_indices:
            row = updated.loc[idx]
            market = _row_market(row)
            selection = _selection(row)
            if market not in SUPPORTED_MARKETS:
                result_status, actual_value, reason = "unsupported", None, "unsupported_market"
            elif selection not in SUPPORTED_SELECTIONS:
                result_status, actual_value, reason = "unsupported", None, "unsupported_selection"
            else:
                result_status, actual_value, reason = _grade_from_direct_actual(row, actual_df)
                if result_status == PENDING_RESULT and market in COMBO_MARKET_COMPONENTS:
                    result_status, actual_value, reason = _grade_from_components(row, component_lookup)

            if result_status in GRADED_RESULTS:
                would_update_rows += 1
                if not dry_run:
                    updated_rows += 1
                    _set_grade(
                        updated,
                        idx,
                        result_status=result_status,
                        actual_value=actual_value,
                        grading_status="graded",
                        grading_reason="",
                        graded_at_utc=graded_at,
                    )
            elif result_status in {"void", "unsupported"}:
                would_update_rows += 1
                if not dry_run:
                    updated_rows += 1
                    _set_grade(
                        updated,
                        idx,
                        result_status=result_status,
                        actual_value=actual_value,
                        grading_status=result_status,
                        grading_reason=reason or result_status,
                        graded_at_utc=graded_at,
                    )
                skip_reasons[reason or result_status] += 1
            else:
                skip_reasons[reason or GAME_NOT_FINAL_REASON] += 1
                if not dry_run:
                    _set_grade(
                        updated,
                        idx,
                        result_status=PENDING_RESULT,
                        actual_value=None,
                        grading_status=OPEN_GAME_STATUS,
                        grading_reason=GAME_NOT_FINAL_REASON,
                    )

    if not dry_run:
        _write_csv(history_path, updated[list(HISTORY_COLUMNS)])

    result_source = updated if not dry_run else history_df
    statuses = result_source["result_status"].astype(str).str.strip().str.lower()
    graded_rows = int(statuses.isin(GRADED_RESULTS).sum())
    pending_rows = int(statuses.eq(PENDING_RESULT).sum())
    return {
        "shadow_candidate_lane_history_path": str(history_path),
        "selected_dates": selected_dates,
        "updated_rows": int(updated_rows),
        "would_update_rows": int(would_update_rows if dry_run else 0),
        "pending_rows": pending_rows,
        "graded_rows": graded_rows,
        "dry_run": bool(dry_run),
        "skip_reasons": dict(sorted(skip_reasons.items())),
    }


def _sample_warning(graded_rows: int) -> str:
    if graded_rows < 20:
        return "<20 graded rows = no conclusion"
    if graded_rows < 50:
        return "20-49 graded rows = weak directional only"
    if graded_rows < 100:
        return "50-99 graded rows = moderate evidence"
    return "100+ graded rows = stronger evidence"


def _aggregate_segment(segment_df: pd.DataFrame, label: str) -> dict[str, Any]:
    if segment_df.empty:
        return {
            "segment": label,
            "total_rows": 0,
            "graded_rows": 0,
            "pending_rows": 0,
            "hits": 0,
            "misses": 0,
            "pushes": 0,
            "void_rows": 0,
            "unsupported_rows": 0,
            "hit_rate": None,
            "flat_profit_loss": 0.0,
            "flat_roi": None,
            "sample_warning": _sample_warning(0),
        }
    status = segment_df["result_status"].astype(str).str.strip().str.lower()
    hits = int(status.eq("hit").sum())
    misses = int(status.eq("miss").sum())
    pushes = int(status.eq("push").sum())
    void_rows = int(status.eq("void").sum())
    unsupported_rows = int(status.eq("unsupported").sum())
    pending_rows = int(status.eq(PENDING_RESULT).sum())
    graded_rows = hits + misses + pushes
    profit = 0.0
    for _, row in segment_df[status.isin(GRADED_RESULTS)].iterrows():
        pl = _safe_float(row.get("flat_profit_loss"))
        if pl is not None:
            profit += float(pl)
    hit_denominator = hits + misses
    return {
        "segment": label,
        "total_rows": int(len(segment_df)),
        "graded_rows": graded_rows,
        "pending_rows": pending_rows,
        "hits": hits,
        "misses": misses,
        "pushes": pushes,
        "void_rows": void_rows,
        "unsupported_rows": unsupported_rows,
        "hit_rate": round(hits / hit_denominator, 6) if hit_denominator else None,
        "flat_profit_loss": round(profit, 6),
        "flat_roi": round(profit / graded_rows, 6) if graded_rows else None,
        "sample_warning": _sample_warning(graded_rows),
    }


def _segment_rows(df: pd.DataFrame, dimension: str) -> list[dict[str, Any]]:
    if df.empty:
        return []
    working = df.copy()
    if dimension not in working.columns:
        working[dimension] = "unknown"
    working[dimension] = working[dimension].astype(str).str.strip().replace("", "unknown")
    rows = [_aggregate_segment(group, str(value)) for value, group in working.groupby(dimension, sort=True)]
    if dimension == "lane":
        existing = {row["segment"] for row in rows}
        for lane in TRACKED_LANES:
            if lane not in existing:
                rows.append(_aggregate_segment(pd.DataFrame(columns=df.columns), lane))
        rows.sort(key=lambda item: (TRACKED_LANES.index(item["segment"]) if item["segment"] in TRACKED_LANES else 99, item["segment"]))
    return rows


def build_shadow_candidate_lane_performance(df: pd.DataFrame) -> dict[str, Any]:
    history_df = df.copy()
    for column in HISTORY_COLUMNS:
        if column not in history_df.columns:
            history_df[column] = ""
    if not history_df.empty:
        history_df["result_status"] = history_df["result_status"].astype(str).str.strip().str.lower()
        for idx, row in history_df.iterrows():
            result_status = _safe_lower(row.get("result_status"), default=PENDING_RESULT)
            if result_status in GRADED_RESULTS and _safe_text(row.get("flat_profit_loss")) == "":
                history_df.at[idx, "flat_profit_loss"] = _flat_profit_loss(row.get("odds"), result_status)
    overall = _aggregate_segment(history_df, "Overall")
    by_dimension = {dimension: _segment_rows(history_df, dimension) for dimension in SEGMENT_DIMENSIONS}
    return {
        "report_name": REPORT_FILE_PREFIX,
        "report_version": REPORT_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "paper_only": True,
        "betting_logic_changed": False,
        "disclaimer": PAPER_ONLY_DISCLAIMER,
        "overall": overall,
        "by_dimension": by_dimension,
        "all_rows_real_money_eligible_false": _all_false(history_df, "real_money_eligible"),
        "all_rows_kelly_eligible_false": _all_false(history_df, "kelly_eligible"),
        "all_rows_elite_eligible_false": _all_false(history_df, "elite_eligible"),
        "all_rows_shadow_only_true": _all_true(history_df, "shadow_only"),
    }


def _format_pct(value: Any) -> str:
    number = _safe_float(value)
    return "n/a" if number is None else f"{number * 100:.1f}%"


def _metric_line(label: str, stats: dict[str, Any]) -> str:
    return (
        f"- {label}: total={int(stats.get('total_rows', 0) or 0)} "
        f"graded={int(stats.get('graded_rows', 0) or 0)} "
        f"pending={int(stats.get('pending_rows', 0) or 0)} "
        f"hits/misses/pushes="
        f"{int(stats.get('hits', 0) or 0)}/"
        f"{int(stats.get('misses', 0) or 0)}/"
        f"{int(stats.get('pushes', 0) or 0)} "
        f"hit_rate={_format_pct(stats.get('hit_rate'))} "
        f"flat_roi={_format_pct(stats.get('flat_roi'))} "
        f"sample='{stats.get('sample_warning')}'"
    )


def render_shadow_candidate_lane_performance_text(payload: dict[str, Any], history_path: Path) -> str:
    overall = payload.get("overall", {})
    lines = [
        f"CourtVision Shadow Candidate Lane Performance - {payload.get('generated_for_date', '')}",
        "=" * 78,
        PAPER_ONLY_DISCLAIMER,
        "This report does not affect Elite, Kelly, final_decision, staking, or bankroll.",
        f"History CSV: {history_path}",
        "",
        "Overall Performance",
        "-" * 78,
        _metric_line("Overall", overall),
        f"- flat profit/loss: {overall.get('flat_profit_loss', 0.0)} units",
        "",
        "Sample Warnings",
        "-" * 78,
        "- <20 graded rows = no conclusion",
        "- 20-49 graded rows = weak directional only",
        "- 50-99 graded rows = moderate evidence",
        "- 100+ graded rows = stronger evidence",
        "",
    ]
    by_dimension = payload.get("by_dimension", {})
    titles = {
        "lane": "Performance by Lane",
        "market_type": "Performance by Market Type",
        "selection": "Performance by Selection",
        "context_caution_level": "Performance by Context Caution Level",
        "context_edge_label": "Performance by Context Edge Label",
        "source_rejection_reason": "Performance by Source Rejection Reason",
        "confidence_bucket": "Performance by Confidence Bucket",
        "edge_bucket": "Performance by Edge Bucket",
    }
    for dimension in SEGMENT_DIMENSIONS:
        lines.extend([titles.get(dimension, f"Performance by {dimension}"), "-" * 78])
        rows = by_dimension.get(dimension, [])
        if rows:
            for item in rows:
                lines.append(_metric_line(str(item.get("segment", "unknown")), item))
        else:
            lines.append("- none")
        lines.append("")
    lines.extend(
        [
            "Guardrails",
            "-" * 78,
            f"- all rows real_money_eligible=False: {payload.get('all_rows_real_money_eligible_false')}",
            f"- all rows kelly_eligible=False: {payload.get('all_rows_kelly_eligible_false')}",
            f"- all rows elite_eligible=False: {payload.get('all_rows_elite_eligible_false')}",
            f"- all rows shadow_only=True: {payload.get('all_rows_shadow_only_true')}",
            "- pick_history.csv is not read or written by this report.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_performance_csv(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []

    def add_row(dimension: str, stats: dict[str, Any]) -> None:
        rows.append(
            {
                "dimension": dimension,
                "segment": stats.get("segment", ""),
                "total_rows": stats.get("total_rows", 0),
                "graded_rows": stats.get("graded_rows", 0),
                "pending_rows": stats.get("pending_rows", 0),
                "hits": stats.get("hits", 0),
                "misses": stats.get("misses", 0),
                "pushes": stats.get("pushes", 0),
                "void_rows": stats.get("void_rows", 0),
                "unsupported_rows": stats.get("unsupported_rows", 0),
                "hit_rate": stats.get("hit_rate"),
                "flat_profit_loss": stats.get("flat_profit_loss", 0.0),
                "flat_roi": stats.get("flat_roi"),
                "sample_warning": stats.get("sample_warning", ""),
            }
        )

    add_row("overall", payload.get("overall", {}))
    by_dimension = payload.get("by_dimension", {})
    for dimension in SEGMENT_DIMENSIONS:
        for item in by_dimension.get(dimension, []):
            add_row(dimension, item)

    fieldnames = [
        "dimension",
        "segment",
        "total_rows",
        "graded_rows",
        "pending_rows",
        "hits",
        "misses",
        "pushes",
        "void_rows",
        "unsupported_rows",
        "hit_rate",
        "flat_profit_loss",
        "flat_roi",
        "sample_warning",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_shadow_candidate_lane_performance_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    grade_pending: bool = True,
    dry_run: bool = False,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    history_path = history_path_for_root(history_root)
    persist_result = persist_daily_shadow_candidate_lane(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        dry_run=dry_run,
    )
    grade_result: dict[str, Any] = {
        "skipped": True,
        "reason": "grade_pending_disabled",
        "pending_rows": 0,
        "graded_rows": 0,
    }
    if grade_pending:
        grade_result = grade_shadow_candidate_lane_history(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
            history_root=history_root,
            dry_run=dry_run,
        )
    history_df = _read_history(history_path)
    payload = build_shadow_candidate_lane_performance(history_df)
    payload["generated_for_date"] = prediction_date
    payload["history_path"] = str(history_path)
    payload["persist_result"] = persist_result
    payload["grade_result"] = grade_result
    txt_path, csv_path, json_path = performance_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    if not dry_run:
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(render_shadow_candidate_lane_performance_text(payload, history_path), encoding="utf-8")
        _write_performance_csv(csv_path, payload)
        json_path.write_text(json.dumps(_json_ready(payload), indent=2), encoding="utf-8")
    return txt_path, csv_path, json_path, _json_ready(payload)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.DataFrame):
        return [_json_ready(row) for row in value.to_dict("records")]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, (int, str, bool)) or value is None:
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


__all__ = [
    "HISTORY_FILENAME",
    "REPORT_FILE_PREFIX",
    "TRACKED_LANES",
    "build_shadow_candidate_lane_performance",
    "grade_shadow_candidate_lane_history",
    "history_path_for_root",
    "performance_paths_for_date",
    "persist_daily_shadow_candidate_lane",
    "render_shadow_candidate_lane_performance_text",
    "write_shadow_candidate_lane_performance_outputs",
]
