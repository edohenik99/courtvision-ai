"""Phase 15B actual-minutes source/backfill audit.

Diagnostic-only audit for determining whether CourtVision can obtain, join, and
eventually backfill actual player minutes from completed games.

This module does not fetch external APIs and does not mutate history.
"""
from __future__ import annotations

import glob
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


DEFAULT_RUNTIME_ROOT = "outputs/runtime"

READINESS_VERDICTS = {
    "NO_LOCAL_ACTUAL_MINUTES_SOURCE_FOUND",
    "LOCAL_ACTUAL_MINUTES_FOUND_JOIN_KEYS_MISSING",
    "LOCAL_ACTUAL_MINUTES_JOIN_READY",
    "PROVIDER_CLIENT_EXISTS_FETCH_NOT_VALIDATED",
    "PROVIDER_BACKFILL_REQUIRES_APPROVAL",
    "READY_FOR_PHASE_15C_MINUTES_ERROR_SHADOW",
    "READY_FOR_PHASE_15D_LOW_LINE_OVER_GUARD_REVIEW",
}

MINUTES_LIKE_RE = re.compile(r"(^|_)(actual_)?min(ute)?s?($|_)|minutes|manual_minutes_limit", re.I)
PROJECTED_MINUTES_COLUMNS = {
    "minutes_avg",
    "min_avg",
    "average_minutes",
    "avg_minutes",
    "minutes_recent",
    "min_recent",
    "recent_minutes",
    "minutes_bucket",
    "manual_minutes_limit",
    "projected_minutes",
    "minutes_projected",
    "expected_minutes",
    "projected_min",
    "minutes_error",
}
ACTUAL_MINUTES_COLUMNS = (
    "actual_minutes",
    "minutes_actual",
    "actual_min",
    "actual_min_played",
    "minutes",
    "min",
    "mins",
    "minutes_played",
    "player_minutes",
)
PLAYER_ID_COLUMNS = ("player_id", "PlayerID", "playerId")
PLAYER_NAME_COLUMNS = ("player_name", "entity_name", "name", "PlayerName", "Name")
GAME_ID_COLUMNS = ("game_id", "GameID", "gameId")
DATE_COLUMNS = ("prediction_date", "game_date", "date", "GameDate")
MARKET_COLUMNS = ("market_type", "market", "prop_type")
RESULT_COLUMNS = ("result_status", "result", "graded_result")
ACTUAL_STAT_COLUMNS = (
    "actual_value",
    "actual_points",
    "points",
    "pts",
    "Points",
    "reb",
    "rebounds",
    "Rebounds",
    "ast",
    "assists",
    "Assists",
    "stl",
    "steals",
    "blk",
    "blocks",
    "fg3m",
    "turnover",
)


def actual_minutes_audit_json_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"actual_minutes_source_audit_{date}.json"


def actual_minutes_audit_txt_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "operator" / f"actual_minutes_source_audit_{date}.txt"


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "null", "<na>"} else text


def _to_float(value: Any) -> float | None:
    text = _safe_text(value).replace(",", "")
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        try:
            return float(parts[0]) + float(parts[1]) / 60.0
        except (IndexError, TypeError, ValueError):
            return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _has_value(value: Any) -> bool:
    return bool(_safe_text(value))


def _numeric_has_value(value: Any) -> bool:
    return _to_float(value) is not None


def _coverage(frame: pd.DataFrame, columns: tuple[str, ...], *, numeric: bool = False) -> float:
    column = _first_existing_column(frame, columns)
    if frame.empty or not column:
        return 0.0
    mapper = _numeric_has_value if numeric else _has_value
    return round(float(frame[column].map(mapper).mean()), 4)


def _first_existing_column(frame: pd.DataFrame, columns: tuple[str, ...]) -> str | None:
    lower_lookup = {str(col).lower(): str(col) for col in frame.columns}
    for column in columns:
        if column in frame.columns:
            return column
        lowered = column.lower()
        if lowered in lower_lookup:
            return lower_lookup[lowered]
    return None


def detect_minutes_like_columns(columns: list[str] | tuple[str, ...]) -> list[str]:
    """Return columns that look related to minutes."""
    return sorted({str(col) for col in columns if MINUTES_LIKE_RE.search(str(col))})


def _actual_minutes_column(frame: pd.DataFrame) -> str | None:
    for column in ACTUAL_MINUTES_COLUMNS:
        existing = _first_existing_column(frame, (column,))
        if existing:
            lowered = existing.lower()
            if lowered not in PROJECTED_MINUTES_COLUMNS:
                return existing
    return None


def _read_csv(source: str | Path | pd.DataFrame | None) -> pd.DataFrame:
    if source is None:
        return pd.DataFrame()
    if isinstance(source, pd.DataFrame):
        return source.copy(deep=True)
    path = Path(source)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _read_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _csv_rows_count(path: Path) -> int:
    if not path.exists() or path.stat().st_size == 0:
        return 0
    try:
        return max(sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore")) - 1, 0)
    except Exception:
        return 0


def _source_scan_row(path: str | Path, frame: pd.DataFrame | None = None) -> dict[str, Any]:
    p = Path(path)
    df = _read_csv(frame if frame is not None else p)
    columns = [str(col) for col in df.columns]
    minutes_cols = detect_minutes_like_columns(columns)
    actual_col = _actual_minutes_column(df)
    actual_stat_cols = [col for col in ACTUAL_STAT_COLUMNS if col in df.columns]
    player_id_col = _first_existing_column(df, PLAYER_ID_COLUMNS)
    player_name_col = _first_existing_column(df, PLAYER_NAME_COLUMNS)
    game_id_col = _first_existing_column(df, GAME_ID_COLUMNS)
    date_col = _first_existing_column(df, DATE_COLUMNS)
    is_candidate = bool(
        actual_col
        and _coverage(df, (actual_col,), numeric=True) > 0
        and (player_id_col or player_name_col)
        and date_col
    )
    return {
        "path": str(p),
        "rows": int(len(df)) if frame is not None else _csv_rows_count(p),
        "minutes_like_columns": minutes_cols,
        "actual_minutes_column": actual_col,
        "actual_minutes_available_rate": _coverage(df, (actual_col,), numeric=True) if actual_col else 0.0,
        "player_id_availability": _coverage(df, (player_id_col,)) if player_id_col else 0.0,
        "player_name_availability": _coverage(df, (player_name_col,)) if player_name_col else 0.0,
        "game_id_availability": _coverage(df, (game_id_col,)) if game_id_col else 0.0,
        "date_availability": _coverage(df, (date_col,)) if date_col else 0.0,
        "actual_stat_columns": actual_stat_cols,
        "actual_stat_availability": max((_coverage(df, (col,), numeric=True) for col in actual_stat_cols), default=0.0),
        "is_actual_minutes_candidate": is_candidate,
    }


def join_key_availability(frame: pd.DataFrame) -> dict[str, Any]:
    """Return key coverage for a history-like frame."""
    rows = int(len(frame))
    out = {
        "rows": rows,
        "player_id_coverage": _coverage(frame, PLAYER_ID_COLUMNS),
        "player_name_coverage": _coverage(frame, PLAYER_NAME_COLUMNS),
        "game_id_coverage": _coverage(frame, GAME_ID_COLUMNS),
        "prediction_date_coverage": _coverage(frame, ("prediction_date",)),
        "game_date_coverage": _coverage(frame, ("game_date", "date")),
        "market_type_coverage": _coverage(frame, MARKET_COLUMNS),
        "result_status_coverage": _coverage(frame, RESULT_COLUMNS),
        "strict_player_id_game_id_date_coverage": 0.0,
    }
    if rows:
        player_id_col = _first_existing_column(frame, PLAYER_ID_COLUMNS)
        game_id_col = _first_existing_column(frame, GAME_ID_COLUMNS)
        date_col = _first_existing_column(frame, ("prediction_date", "game_date", "date"))
        if player_id_col and game_id_col and date_col:
            keyed = (
                frame[player_id_col].map(_has_value)
                & frame[game_id_col].map(_has_value)
                & frame[date_col].map(_has_value)
            )
            out["strict_player_id_game_id_date_coverage"] = round(float(keyed.mean()), 4)
    return out


def _normalize_date(value: Any) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return text[:10]
    return parsed.strftime("%Y-%m-%d")


def _id_key(value: Any) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    try:
        return str(int(float(text)))
    except (TypeError, ValueError):
        return text


def _line_float(value: Any) -> float | None:
    return _to_float(value)


def _normalize_history_frame(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy(deep=True)
    out = pd.DataFrame(index=df.index)
    for name, cols in {
        "player_id": PLAYER_ID_COLUMNS,
        "player_name": PLAYER_NAME_COLUMNS,
        "game_id": GAME_ID_COLUMNS,
        "prediction_date": ("prediction_date", "game_date", "date"),
        "market_type": MARKET_COLUMNS,
        "selection": ("selection", "side", "pick_side"),
        "line": ("line", "sportsbook_line", "line_value"),
        "result_status": RESULT_COLUMNS,
    }.items():
        col = _first_existing_column(df, cols)
        out[name] = df[col] if col else ""
    out["player_id_key"] = out["player_id"].map(_id_key)
    out["game_id_key"] = out["game_id"].map(_id_key)
    out["date_key"] = out["prediction_date"].map(_normalize_date)
    out["market_type"] = out["market_type"].map(lambda value: _safe_text(value).lower())
    out["selection"] = out["selection"].map(lambda value: _safe_text(value).lower())
    out["line_num"] = out["line"].map(_line_float)
    return out


def _normalize_actual_minutes_source(frame: pd.DataFrame, source_path: str) -> pd.DataFrame:
    df = frame.copy(deep=True)
    actual_col = _actual_minutes_column(df)
    if not actual_col:
        return pd.DataFrame()
    out = pd.DataFrame(index=df.index)
    out["source_path"] = source_path
    out["actual_minutes"] = df[actual_col].map(_to_float)
    for name, cols in {
        "player_id": PLAYER_ID_COLUMNS,
        "player_name": PLAYER_NAME_COLUMNS,
        "game_id": GAME_ID_COLUMNS,
        "game_date": ("game_date", "prediction_date", "date", "GameDate"),
        "points": ("points", "pts", "Points", "actual_points", "actual_value"),
    }.items():
        col = _first_existing_column(df, cols)
        out[name] = df[col] if col else ""
    out["player_id_key"] = out["player_id"].map(_id_key)
    out["game_id_key"] = out["game_id"].map(_id_key)
    out["date_key"] = out["game_date"].map(_normalize_date)
    out = out[out["actual_minutes"].map(lambda value: value is not None)].copy()
    return out.reset_index(drop=True)


def _strict_join_key(row: Mapping[str, Any]) -> str:
    player_id = _safe_text(row.get("player_id_key"))
    game_id = _safe_text(row.get("game_id_key"))
    date = _safe_text(row.get("date_key"))
    if not (player_id and game_id and date):
        return ""
    return f"{player_id}|{game_id}|{date}"


def calculate_join_coverage(
    history_frame: pd.DataFrame,
    actual_minutes_rows: pd.DataFrame,
) -> dict[str, Any]:
    """Calculate strict player_id + game_id + date join coverage."""
    hist = _normalize_history_frame(history_frame)
    actual = actual_minutes_rows.copy(deep=True)
    if not actual.empty:
        actual["_strict_key"] = actual.apply(_strict_join_key, axis=1)
        actual_keys = {key for key in actual["_strict_key"].tolist() if key}
    else:
        actual_keys = set()
    if not hist.empty:
        hist["_strict_key"] = hist.apply(_strict_join_key, axis=1)
        keyed_mask = hist["_strict_key"].map(bool)
        joined_mask = hist["_strict_key"].isin(actual_keys) & keyed_mask
    else:
        keyed_mask = pd.Series([], dtype=bool)
        joined_mask = pd.Series([], dtype=bool)
    player_points = hist["market_type"].eq("player_points") if "market_type" in hist.columns else pd.Series([], dtype=bool)
    low_line_over = (
        player_points
        & hist["selection"].eq("over")
        & hist["line_num"].map(lambda value: value is not None and value < 15.0)
    ) if not hist.empty else pd.Series([], dtype=bool)

    by_date: dict[str, Any] = {}
    if not hist.empty and "date_key" in hist.columns:
        for date, group in hist.groupby("date_key", dropna=False):
            if not _safe_text(date):
                continue
            group_keys = group["_strict_key"]
            group_keyed = group_keys.map(bool)
            group_joined = group_keys.isin(actual_keys) & group_keyed
            by_date[_safe_text(date)] = {
                "rows": int(len(group)),
                "rows_with_strict_join_key": int(group_keyed.sum()),
                "joined_rows": int(group_joined.sum()),
                "join_rate_all_rows": round(float(group_joined.mean()), 4) if len(group) else 0.0,
                "join_rate_keyed_rows": round(float(group_joined.sum() / max(1, group_keyed.sum())), 4),
            }
    total_rows = int(len(hist))
    keyed_rows = int(keyed_mask.sum()) if total_rows else 0
    joined_rows = int(joined_mask.sum()) if total_rows else 0
    pp_rows = int(player_points.sum()) if total_rows else 0
    pp_joined = int((joined_mask & player_points).sum()) if total_rows else 0
    low_rows = int(low_line_over.sum()) if total_rows else 0
    low_joined = int((joined_mask & low_line_over).sum()) if total_rows else 0
    return {
        "rows": total_rows,
        "rows_with_strict_join_key": keyed_rows,
        "joined_rows": joined_rows,
        "join_rate_all_rows": round(joined_rows / max(1, total_rows), 4),
        "join_rate_keyed_rows": round(joined_rows / max(1, keyed_rows), 4),
        "player_points_rows": pp_rows,
        "player_points_joined_rows": pp_joined,
        "player_points_join_rate": round(pp_joined / max(1, pp_rows), 4),
        "low_line_over_rows": low_rows,
        "low_line_over_joined_rows": low_joined,
        "low_line_over_join_rate": round(low_joined / max(1, low_rows), 4),
        "coverage_by_prediction_date": by_date,
    }


def provider_client_candidates_summary() -> list[dict[str, Any]]:
    """Static audit of provider/client code paths; performs no API calls."""
    return [
        {
            "provider": "balldontlie",
            "class": "BalldontlieClient",
            "functions": ["get_stats_for_player_ids", "get_stats_for_player_ids_on_date"],
            "returns_minutes": True,
            "returns_player_id": True,
            "returns_game_id": True,
            "returns_game_date": True,
            "returns_points": True,
            "requires_api_call": True,
            "already_used_by_grading": True,
            "mutates_history": False,
            "safe_for_diagnostics_only": True,
            "evidence": "courtvision/clients/balldontlie_client.py builds PlayerGameStats(minutes=...) from /stats rows",
        },
        {
            "provider": "sportsdataio",
            "class": "SportsDataIOClient",
            "functions": ["get_stats_for_player_ids", "get_stats_for_player_ids_on_date"],
            "returns_minutes": True,
            "returns_player_id": True,
            "returns_game_id": True,
            "returns_game_date": True,
            "returns_points": True,
            "requires_api_call": True,
            "already_used_by_grading": True,
            "mutates_history": False,
            "safe_for_diagnostics_only": True,
            "evidence": "courtvision/clients/sportsdataio_client.py reads Minutes from PlayerGameStatsBySeason and BoxScore",
        },
        {
            "provider": "provider_manager",
            "class": "ProviderManager",
            "functions": ["get_stats_for_player_ids", "get_stats_for_player_ids_on_date"],
            "returns_minutes": True,
            "returns_player_id": True,
            "returns_game_id": True,
            "returns_game_date": True,
            "returns_points": True,
            "requires_api_call": True,
            "already_used_by_grading": True,
            "mutates_history": False,
            "safe_for_diagnostics_only": True,
            "evidence": "courtvision/clients/provider_manager.py wraps stats fetches with provider fallback",
        },
    ]


def _recommended_source(
    *,
    local_actual_minutes_found: bool,
    provider_candidates: list[dict[str, Any]],
) -> str:
    if local_actual_minutes_found:
        return "existing local artifact"
    if any(candidate.get("safe_for_diagnostics_only") and candidate.get("returns_minutes") for candidate in provider_candidates):
        return "existing provider client function"
    return "insufficient evidence"


def _missing_critical_fields(
    market_shadow_keys: Mapping[str, Any],
    pick_history_keys: Mapping[str, Any],
    local_actual_minutes_found: bool,
) -> list[str]:
    missing: list[str] = []
    if not local_actual_minutes_found:
        missing.append("local_actual_minutes")
    if market_shadow_keys.get("player_id_coverage", 0) <= 0:
        missing.append("market_shadow_player_id")
    if market_shadow_keys.get("game_id_coverage", 0) <= 0:
        missing.append("market_shadow_game_id")
    if market_shadow_keys.get("prediction_date_coverage", 0) <= 0:
        missing.append("market_shadow_prediction_date")
    if pick_history_keys.get("player_id_coverage", 0) <= 0:
        missing.append("pick_history_player_id")
    if pick_history_keys.get("game_id_coverage", 0) <= 0:
        missing.append("pick_history_game_id")
    if pick_history_keys.get("prediction_date_coverage", 0) <= 0:
        missing.append("pick_history_prediction_date")
    return missing


def select_readiness_verdict(
    *,
    local_actual_minutes_found: bool,
    actual_minutes_rows_found: int,
    market_shadow_join_coverage: Mapping[str, Any],
    pick_history_join_coverage: Mapping[str, Any],
    provider_client_candidates: list[Mapping[str, Any]],
    missing_critical_fields: list[str],
) -> str:
    if local_actual_minutes_found and actual_minutes_rows_found > 0:
        market_join = float(market_shadow_join_coverage.get("join_rate_keyed_rows") or 0)
        pick_join = float(pick_history_join_coverage.get("join_rate_keyed_rows") or 0)
        if "market_shadow_game_id" in missing_critical_fields or "pick_history_game_id" in missing_critical_fields:
            return "LOCAL_ACTUAL_MINUTES_FOUND_JOIN_KEYS_MISSING"
        if market_join > 0 and pick_join > 0:
            return "READY_FOR_PHASE_15C_MINUTES_ERROR_SHADOW"
        return "LOCAL_ACTUAL_MINUTES_JOIN_READY"
    if any(candidate.get("returns_minutes") and candidate.get("requires_api_call") for candidate in provider_client_candidates):
        return "PROVIDER_CLIENT_EXISTS_FETCH_NOT_VALIDATED"
    return "NO_LOCAL_ACTUAL_MINUTES_SOURCE_FOUND"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _resolve_history_path(runtime_root: Path, filename: str, explicit: Any) -> Any:
    if explicit is not None:
        return explicit
    if runtime_root.as_posix().replace("\\", "/").endswith("outputs/runtime"):
        return Path("data/history") / filename
    return runtime_root.parent / "history" / filename


def _collect_source_paths(
    *,
    runtime_root: Path,
    market_shadow_history: Any,
    pick_history: Any,
    player_baselines: Any,
    grading_glob: str | Path | None,
    player_predictions_glob: str | Path | None,
    full_market_glob: str | Path | None,
    minutes_availability_glob: str | Path | None,
) -> tuple[list[tuple[str, str, pd.DataFrame]], list[Path]]:
    operator_dir = runtime_root / "operator"
    research_dir = runtime_root / "research"
    diagnostics_dir = runtime_root / "diagnostics"
    model_dir = runtime_root.parent / "model"
    player_baselines = player_baselines if player_baselines is not None else model_dir / "player_baselines.csv"
    grading_glob = grading_glob or research_dir / "grading_results_*.csv"
    player_predictions_glob = player_predictions_glob or research_dir / "player_predictions_*.csv"
    full_market_glob = full_market_glob or operator_dir / "full_market_board_*.csv"
    minutes_availability_glob = minutes_availability_glob or diagnostics_dir / "minutes_availability_audit_*.json"
    csv_sources: list[tuple[str, str, pd.DataFrame]] = []
    for label, source in [
        ("market_shadow_history", market_shadow_history),
        ("pick_history", pick_history),
        ("player_baselines", player_baselines),
    ]:
        frame = _read_csv(source)
        if not frame.empty:
            csv_sources.append((label, str(source), frame))
    for label, pattern in [
        ("grading_results", grading_glob),
        ("player_predictions", player_predictions_glob),
        ("full_market_board", full_market_glob),
    ]:
        for path_text in sorted(glob.glob(str(pattern))):
            frame = _read_csv(path_text)
            if not frame.empty:
                csv_sources.append((label, path_text, frame))
    json_paths = [Path(path) for path in sorted(glob.glob(str(minutes_availability_glob)))]
    return csv_sources, json_paths


def build_actual_minutes_source_audit(
    prediction_date: str,
    *,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    market_shadow_history: str | Path | pd.DataFrame | None = None,
    pick_history: str | Path | pd.DataFrame | None = None,
    player_baselines: str | Path | pd.DataFrame | None = None,
    grading_glob: str | Path | None = None,
    player_predictions_glob: str | Path | None = None,
    full_market_glob: str | Path | None = None,
    minutes_availability_glob: str | Path | None = None,
) -> dict[str, Any]:
    """Build the actual-minutes source audit payload."""
    runtime_root = Path(runtime_root)
    market_shadow_history = _resolve_history_path(runtime_root, "market_shadow_history.csv", market_shadow_history)
    pick_history = _resolve_history_path(runtime_root, "pick_history.csv", pick_history)

    market_shadow_df = _read_csv(market_shadow_history)
    pick_history_df = _read_csv(pick_history)
    csv_sources, json_paths = _collect_source_paths(
        runtime_root=runtime_root,
        market_shadow_history=market_shadow_history,
        pick_history=pick_history,
        player_baselines=player_baselines,
        grading_glob=grading_glob,
        player_predictions_glob=player_predictions_glob,
        full_market_glob=full_market_glob,
        minutes_availability_glob=minutes_availability_glob,
    )
    candidate_source_files: list[dict[str, Any]] = []
    actual_frames: list[pd.DataFrame] = []
    for _label, source_file, frame in csv_sources:
        scan = _source_scan_row(source_file, frame)
        candidate_source_files.append(scan)
        if scan["is_actual_minutes_candidate"]:
            normalized = _normalize_actual_minutes_source(frame, source_file)
            if not normalized.empty:
                actual_frames.append(normalized)
    for path in json_paths:
        payload = _read_json(path)
        candidate_source_files.append(
            {
                "path": str(path),
                "rows": int(payload.get("total_rows_scanned") or 0) if payload else 0,
                "minutes_like_columns": ["actual_minutes_available_rate"] if "actual_minutes_available_rate" in payload else [],
                "actual_minutes_column": None,
                "actual_minutes_available_rate": float(payload.get("actual_minutes_available_rate") or 0) if payload else 0.0,
                "player_id_availability": None,
                "player_name_availability": None,
                "game_id_availability": None,
                "date_availability": None,
                "actual_stat_columns": [],
                "actual_stat_availability": None,
                "is_actual_minutes_candidate": False,
                "json_only": True,
            }
        )

    actual_minutes_rows = (
        pd.concat(actual_frames, ignore_index=True).drop_duplicates()
        if actual_frames
        else pd.DataFrame()
    )
    local_actual_minutes_found = not actual_minutes_rows.empty
    provider_candidates = provider_client_candidates_summary()
    market_keys = join_key_availability(market_shadow_df)
    pick_keys = join_key_availability(pick_history_df)
    market_join = calculate_join_coverage(market_shadow_df, actual_minutes_rows)
    pick_join = calculate_join_coverage(pick_history_df, actual_minutes_rows)
    missing = _missing_critical_fields(market_keys, pick_keys, local_actual_minutes_found)
    recommended = _recommended_source(
        local_actual_minutes_found=local_actual_minutes_found,
        provider_candidates=provider_candidates,
    )
    verdict = select_readiness_verdict(
        local_actual_minutes_found=local_actual_minutes_found,
        actual_minutes_rows_found=int(len(actual_minutes_rows)),
        market_shadow_join_coverage=market_join,
        pick_history_join_coverage=pick_join,
        provider_client_candidates=provider_candidates,
        missing_critical_fields=missing,
    )

    payload = {
        "prediction_date": prediction_date,
        "note": "audit_only_no_history_mutation",
        "local_actual_minutes_found": bool(local_actual_minutes_found),
        "candidate_source_files": candidate_source_files,
        "provider_client_candidates": provider_candidates,
        "join_key_availability": {
            "market_shadow_history": market_keys,
            "pick_history": pick_keys,
        },
        "market_shadow_join_coverage": market_join,
        "pick_history_join_coverage": pick_join,
        "actual_minutes_rows_found": int(len(actual_minutes_rows)),
        "actual_minutes_unique_players": int(actual_minutes_rows["player_id_key"].nunique()) if not actual_minutes_rows.empty else 0,
        "actual_minutes_unique_games": int(actual_minutes_rows["game_id_key"].nunique()) if not actual_minutes_rows.empty else 0,
        "player_id_coverage": {
            "market_shadow_history": market_keys["player_id_coverage"],
            "pick_history": pick_keys["player_id_coverage"],
            "actual_minutes_sources": _coverage(actual_minutes_rows, ("player_id",)) if not actual_minutes_rows.empty else 0.0,
        },
        "game_id_coverage": {
            "market_shadow_history": market_keys["game_id_coverage"],
            "pick_history": pick_keys["game_id_coverage"],
            "actual_minutes_sources": _coverage(actual_minutes_rows, ("game_id",)) if not actual_minutes_rows.empty else 0.0,
        },
        "date_coverage": {
            "market_shadow_history": market_keys["prediction_date_coverage"],
            "pick_history": pick_keys["prediction_date_coverage"],
            "actual_minutes_sources": _coverage(actual_minutes_rows, ("game_date",)) if not actual_minutes_rows.empty else 0.0,
        },
        "missing_critical_fields": missing,
        "recommended_source": recommended,
        "readiness_verdict": verdict,
        "history_backfill_safety": {
            "history_mutated": False,
            "safe_to_write_history_now": False,
            "recommendation": "write diagnostic artifact only; history backfill requires explicit approval",
        },
    }
    return _json_safe(payload)


def _format_txt(payload: Mapping[str, Any], prediction_date: str) -> str:
    sep = "=" * 78
    sep2 = "-" * 78
    local_found = bool(payload.get("local_actual_minutes_found"))
    provider_candidates = payload.get("provider_client_candidates", [])
    provider_support = any(
        isinstance(candidate, Mapping) and candidate.get("returns_minutes")
        for candidate in provider_candidates
    )
    market_join = payload.get("market_shadow_join_coverage", {}) if isinstance(payload.get("market_shadow_join_coverage"), dict) else {}
    pick_join = payload.get("pick_history_join_coverage", {}) if isinstance(payload.get("pick_history_join_coverage"), dict) else {}
    missing = payload.get("missing_critical_fields", [])
    next_step = "provider diagnostics fetch design, with explicit approval before any API call"
    if local_found and (market_join.get("join_rate_keyed_rows") or 0) > 0:
        next_step = "Phase 15C -- minutes-error shadow audit"

    lines = [
        f"{sep}\n",
        "ACTUAL MINUTES SOURCE AUDIT (Phase 15B -- AUDIT ONLY)\n",
        f"date: {prediction_date}    note: {payload.get('note', '')}\n",
        f"{sep}\n\n",
        "OVERVIEW\n",
        f"{sep2}\n",
        f"  local_actual_minutes_found : {local_found}\n",
        f"  actual_minutes_rows_found  : {payload.get('actual_minutes_rows_found', 0)}\n",
        f"  recommended_source         : {payload.get('recommended_source')}\n",
        f"  readiness_verdict          : {payload.get('readiness_verdict')}\n\n",
        "DIAGNOSTIC QUESTIONS\n",
        f"{sep2}\n",
        f"  Q: Do actual_minutes exist locally?\n     {'Yes' if local_found else 'No numeric actual_minutes source was found locally.'}\n",
        "  Q: Which files have minutes-like columns?\n",
    ]
    for row in payload.get("candidate_source_files", [])[:12]:
        if isinstance(row, Mapping) and row.get("minutes_like_columns"):
            lines.append(f"     {row.get('path')}: {', '.join(row.get('minutes_like_columns') or [])}\n")
    lines.extend(
        [
            "  Q: Can we join actual_minutes to market_shadow_history?\n",
            f"     joined_rows={market_join.get('joined_rows', 0)} join_rate_keyed={market_join.get('join_rate_keyed_rows', 0)}\n",
            "  Q: Can we join actual_minutes to pick_history?\n",
            f"     joined_rows={pick_join.get('joined_rows', 0)} join_rate_keyed={pick_join.get('join_rate_keyed_rows', 0)}\n",
            "  Q: Does current provider/client code already support fetching minutes?\n",
            f"     {'Yes' if provider_support else 'No'}; no provider call was made by this audit.\n",
            "  Q: Would backfilling actual_minutes mutate canonical history?\n",
            "     Yes. This phase writes diagnostics only; any history backfill requires explicit approval.\n",
            "  Q: What is the safest next step?\n",
            f"     {next_step}\n",
            "  Q: Is CourtVision ready for a minutes-error shadow audit?\n",
            f"     {payload.get('readiness_verdict')}\n",
            "  Q: What fields are missing?\n",
            f"     {', '.join(missing) if missing else 'none'}\n\n",
            "JOIN KEY AVAILABILITY\n",
            f"{sep2}\n",
        ]
    )
    join_keys = payload.get("join_key_availability", {}) if isinstance(payload.get("join_key_availability"), dict) else {}
    for label, row in join_keys.items():
        if isinstance(row, Mapping):
            lines.append(
                f"  {label}: rows={row.get('rows')} player_id={row.get('player_id_coverage')} "
                f"game_id={row.get('game_id_coverage')} date={row.get('prediction_date_coverage')} "
                f"strict_key={row.get('strict_player_id_game_id_date_coverage')}\n"
            )
    lines.extend(
        [
            "\nPROVIDER CLIENT CANDIDATES\n",
            f"{sep2}\n",
        ]
    )
    for row in provider_candidates:
        if isinstance(row, Mapping):
            lines.append(
                f"  {row.get('provider')}: functions={', '.join(row.get('functions') or [])}; "
                f"returns_minutes={row.get('returns_minutes')}; requires_api_call={row.get('requires_api_call')}; "
                f"mutates_history={row.get('mutates_history')}\n"
            )
    lines.append(f"\n{sep}\n")
    return "".join(lines)


def write_actual_minutes_source_audit(
    prediction_date: str,
    *,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    market_shadow_history: str | Path | pd.DataFrame | None = None,
    pick_history: str | Path | pd.DataFrame | None = None,
    player_baselines: str | Path | pd.DataFrame | None = None,
    grading_glob: str | Path | None = None,
    player_predictions_glob: str | Path | None = None,
    full_market_glob: str | Path | None = None,
    minutes_availability_glob: str | Path | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    payload = build_actual_minutes_source_audit(
        prediction_date,
        runtime_root=runtime_root,
        market_shadow_history=market_shadow_history,
        pick_history=pick_history,
        player_baselines=player_baselines,
        grading_glob=grading_glob,
        player_predictions_glob=player_predictions_glob,
        full_market_glob=full_market_glob,
        minutes_availability_glob=minutes_availability_glob,
    )
    json_path = actual_minutes_audit_json_path_for_date(prediction_date, runtime_root)
    txt_path = actual_minutes_audit_txt_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    txt_path.write_text(_format_txt(payload, prediction_date), encoding="utf-8")
    return json_path, txt_path, payload


__all__ = [
    "actual_minutes_audit_json_path_for_date",
    "actual_minutes_audit_txt_path_for_date",
    "build_actual_minutes_source_audit",
    "calculate_join_coverage",
    "detect_minutes_like_columns",
    "join_key_availability",
    "provider_client_candidates_summary",
    "select_readiness_verdict",
    "write_actual_minutes_source_audit",
]
