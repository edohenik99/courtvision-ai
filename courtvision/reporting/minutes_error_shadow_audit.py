"""Phase 15C minutes-error shadow audit.

Diagnostic-only audit for measuring whether available minutes fields explain
historical low-line player_points OVER misses. This module reads local artifacts
and writes diagnostics only; it does not mutate prediction, grading, Kelly, or
history state.
"""
from __future__ import annotations

import glob
import json
import math
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


DEFAULT_RUNTIME_ROOT = "outputs/runtime"

READINESS_VERDICTS = {
    "NO_PLAYER_POINTS_HISTORY",
    "LOW_LINE_OVER_SAMPLE_MISSING",
    "MINUTES_FIELDS_UNAVAILABLE",
    "ACTUAL_MINUTES_UNAVAILABLE_MINUTES_BASIS_READY",
    "MINUTES_ERROR_SHADOW_READY",
    "MINUTES_SHORTFALL_SIGNAL_PRESENT",
}

PLAYER_ID_COLUMNS = ("player_id", "PlayerID", "playerId")
PLAYER_NAME_COLUMNS = ("player_name", "entity_name", "name", "PlayerName", "Name")
DATE_COLUMNS = ("prediction_date", "game_date", "date", "GameDate")
GAME_ID_COLUMNS = ("game_id", "GameID", "gameId")
MARKET_COLUMNS = ("market_type", "market", "prop_type")
SELECTION_COLUMNS = ("selection", "side", "pick_side")
LINE_COLUMNS = ("line", "sportsbook_line", "line_value", "market_line")
RESULT_COLUMNS = ("result_status", "result", "graded_result")
ACTUAL_VALUE_COLUMNS = ("actual_value", "actual_points", "points", "pts", "result_value")
PROJECTED_MINUTES_COLUMNS = ("projected_minutes", "minutes_projected", "expected_minutes", "projected_min")
MINUTES_RECENT_COLUMNS = ("minutes_recent", "min_recent", "recent_minutes")
MINUTES_AVG_COLUMNS = ("minutes_avg", "min_avg", "average_minutes", "avg_minutes")
MANUAL_MINUTES_LIMIT_COLUMNS = ("manual_minutes_limit", "minutes_limit")
ACTUAL_MINUTES_COLUMNS = (
    "actual_minutes",
    "minutes_actual",
    "actual_min",
    "actual_min_played",
    "minutes_played",
    "player_minutes",
    "minutes",
    "min",
    "mins",
)
MINUTES_FIELDS = ("projected_minutes", "minutes_recent", "minutes_avg", "manual_minutes_limit", "actual_minutes")
RELIABLE_MINUTES_FIELDS = ("projected_minutes", "minutes_recent", "minutes_avg", "manual_minutes_limit")
RESULTS = ("hit", "miss", "push")


def minutes_error_audit_json_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"minutes_error_shadow_audit_{date}.json"


def minutes_error_audit_txt_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "operator" / f"minutes_error_shadow_audit_{date}.txt"


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
        parsed = float(text)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


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


def _first_existing_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    lower_lookup = {str(column).lower(): str(column) for column in frame.columns}
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
        lowered = candidate.lower()
        if lowered in lower_lookup:
            return lower_lookup[lowered]
    return None


def _coalesce_text(frame: pd.DataFrame, candidates: tuple[str, ...]) -> pd.Series:
    out = pd.Series([""] * len(frame), index=frame.index, dtype="object")
    for candidate in candidates:
        column = _first_existing_column(frame, (candidate,))
        if not column:
            continue
        values = frame[column].map(_safe_text)
        fill_mask = out.eq("") & values.ne("")
        if fill_mask.any():
            out.loc[fill_mask] = values.loc[fill_mask]
    return out


def _coalesce_numeric(frame: pd.DataFrame, candidates: tuple[str, ...]) -> pd.Series:
    out = pd.Series([pd.NA] * len(frame), index=frame.index, dtype="object")
    for candidate in candidates:
        column = _first_existing_column(frame, (candidate,))
        if not column:
            continue
        values = frame[column].map(_to_float)
        fill_mask = out.isna() & values.notna()
        if fill_mask.any():
            out.loc[fill_mask] = values.loc[fill_mask]
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
        return text.lower()


def _name_key(value: Any) -> str:
    return " ".join(_safe_text(value).lower().split())


def _normalize_market(value: Any) -> str:
    text = _safe_text(value).lower()
    if text in {"points", "player_pts", "pts"}:
        return "player_points"
    return text


def _normalize_selection(value: Any) -> str:
    text = _safe_text(value).lower()
    if "over" in text:
        return "over"
    if "under" in text:
        return "under"
    return text


def _normalize_result(value: Any) -> str:
    text = _safe_text(value).lower()
    if text in {"hit", "win", "won", "true", "1"}:
        return "hit"
    if text in {"miss", "loss", "lost", "false", "0"}:
        return "miss"
    if text == "push":
        return "push"
    return text


def _line_key(value: Any) -> str:
    number = _to_float(value)
    return "" if number is None else f"{number:.4f}"


def _player_key(row: Mapping[str, Any]) -> str:
    player_id = _safe_text(row.get("player_id_key"))
    if player_id:
        return f"id:{player_id}"
    return f"name:{_safe_text(row.get('player_name_key'))}"


def _row_key(row: Mapping[str, Any]) -> str:
    return "|".join(
        [
            _safe_text(row.get("prediction_date")),
            _player_key(row),
            _safe_text(row.get("market_type")),
            _safe_text(row.get("selection")),
            _line_key(row.get("line")),
        ]
    )


def _source_frame(
    frame: pd.DataFrame,
    *,
    source_type: str,
    source_file: str,
) -> pd.DataFrame:
    df = frame.copy(deep=True)
    out = pd.DataFrame(index=df.index)
    out["source_type"] = source_type
    out["source_file"] = source_file
    out["prediction_date"] = _coalesce_text(df, DATE_COLUMNS).map(_normalize_date)
    out["player_id"] = _coalesce_text(df, PLAYER_ID_COLUMNS)
    out["player_id_key"] = out["player_id"].map(_id_key)
    out["player_name"] = _coalesce_text(df, PLAYER_NAME_COLUMNS)
    out["player_name_key"] = out["player_name"].map(_name_key)
    out["game_id"] = _coalesce_text(df, GAME_ID_COLUMNS)
    out["market_type"] = _coalesce_text(df, MARKET_COLUMNS).map(_normalize_market)
    out["selection"] = _coalesce_text(df, SELECTION_COLUMNS).map(_normalize_selection)
    out["line"] = _coalesce_numeric(df, LINE_COLUMNS)
    out["result_status"] = _coalesce_text(df, RESULT_COLUMNS).map(_normalize_result)
    out["actual_value"] = _coalesce_numeric(df, ACTUAL_VALUE_COLUMNS)
    out["projected_minutes"] = _coalesce_numeric(df, PROJECTED_MINUTES_COLUMNS)
    out["minutes_recent"] = _coalesce_numeric(df, MINUTES_RECENT_COLUMNS)
    out["minutes_avg"] = _coalesce_numeric(df, MINUTES_AVG_COLUMNS)
    out["manual_minutes_limit"] = _coalesce_numeric(df, MANUAL_MINUTES_LIMIT_COLUMNS)
    out["actual_minutes"] = _coalesce_numeric(df, ACTUAL_MINUTES_COLUMNS)
    return out.reset_index(drop=True)


def _resolve_history_path(runtime_root: Path, filename: str, explicit: Any) -> Any:
    if explicit is not None:
        return explicit
    if runtime_root.as_posix().replace("\\", "/").endswith("outputs/runtime"):
        return Path("data/history") / filename
    return runtime_root.parent / "history" / filename


def _first_present(values: pd.Series) -> Any:
    for value in values:
        if _to_float(value) is not None or _safe_text(value):
            return value
    return None


def _coalesce_history_rows(frames: list[pd.DataFrame]) -> pd.DataFrame:
    usable = [frame for frame in frames if not frame.empty]
    if not usable:
        return pd.DataFrame()
    combined = pd.concat(usable, ignore_index=True)
    combined = combined[combined["market_type"].eq("player_points")]
    combined = combined[combined["result_status"].isin(RESULTS)].copy()
    if combined.empty:
        return combined.reset_index(drop=True)
    combined["_audit_key"] = combined.apply(_row_key, axis=1)

    rows: list[dict[str, Any]] = []
    for key, group in combined.groupby("_audit_key", sort=False, dropna=False):
        merged: dict[str, Any] = {"_audit_key": key}
        for column in combined.columns:
            if column == "_audit_key":
                continue
            if column in {"source_type", "source_file"}:
                values = sorted({_safe_text(value) for value in group[column] if _safe_text(value)})
                merged[column] = ";".join(values)
            else:
                merged[column] = _first_present(group[column])
        rows.append(merged)
    return pd.DataFrame(rows).reset_index(drop=True)


def _exact_lookup(source: pd.DataFrame) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    if source.empty:
        return lookup
    for _, row in source.iterrows():
        key = _row_key(row)
        if not key or "||" in key:
            continue
        payload = lookup.setdefault(key, {})
        for field in MINUTES_FIELDS:
            if field in row.index and _to_float(payload.get(field)) is None and _to_float(row.get(field)) is not None:
                payload[field] = row.get(field)
    return lookup


def _baseline_lookup(source: pd.DataFrame) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    if source.empty:
        return lookup
    for _, row in source.iterrows():
        keys = []
        if _safe_text(row.get("player_id_key")):
            keys.append(f"id:{_safe_text(row.get('player_id_key'))}")
        if _safe_text(row.get("player_name_key")):
            keys.append(f"name:{_safe_text(row.get('player_name_key'))}")
        for key in keys:
            payload = lookup.setdefault(key, {})
            for field in ("minutes_recent", "minutes_avg", "projected_minutes"):
                if field in row.index and _to_float(payload.get(field)) is None and _to_float(row.get(field)) is not None:
                    payload[field] = row.get(field)
    return lookup


def _apply_minutes_sources(base: pd.DataFrame, sources: list[pd.DataFrame]) -> pd.DataFrame:
    if base.empty:
        return base.copy()
    working = base.copy(deep=True)
    exact: dict[str, dict[str, Any]] = {}
    baseline: dict[str, dict[str, Any]] = {}
    for source in sources:
        for key, payload in _exact_lookup(source).items():
            target = exact.setdefault(key, {})
            for field, value in payload.items():
                if _to_float(target.get(field)) is None:
                    target[field] = value
        for key, payload in _baseline_lookup(source).items():
            target = baseline.setdefault(key, {})
            for field, value in payload.items():
                if _to_float(target.get(field)) is None and _to_float(value) is not None:
                    target[field] = value

    for idx, row in working.iterrows():
        exact_payload = exact.get(_row_key(row), {})
        baseline_payload = baseline.get(_player_key(row), {})
        for field in MINUTES_FIELDS:
            if _to_float(working.at[idx, field]) is not None:
                continue
            if _to_float(exact_payload.get(field)) is not None:
                working.at[idx, field] = exact_payload[field]
            elif _to_float(baseline_payload.get(field)) is not None:
                working.at[idx, field] = baseline_payload[field]
    return working


def _minutes_basis(row: Mapping[str, Any]) -> tuple[float | None, str]:
    for field in ("projected_minutes", "manual_minutes_limit", "minutes_recent", "minutes_avg"):
        value = _to_float(row.get(field))
        if value is not None:
            return value, field
    return None, ""


def _has_reliable_minutes(row: Mapping[str, Any]) -> bool:
    return any(_to_float(row.get(field)) is not None for field in RELIABLE_MINUTES_FIELDS)


def _avg(values: pd.Series) -> float | None:
    numeric = values.map(_to_float).dropna()
    if numeric.empty:
        return None
    return round(float(numeric.mean()), 4)


def _availability(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return 0.0
    return round(float(frame[column].map(lambda value: _to_float(value) is not None).mean()), 4)


def _result_summary(frame: pd.DataFrame) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for result in RESULTS:
        group = frame[frame["result_status"].eq(result)] if not frame.empty else pd.DataFrame()
        summary[result] = {
            "rows": int(len(group)),
            "avg_projected_minutes": _avg(group["projected_minutes"]) if not group.empty else None,
            "avg_minutes_basis": _avg(group["minutes_basis"]) if not group.empty else None,
            "avg_actual_minutes": _avg(group["actual_minutes"]) if not group.empty else None,
            "avg_minutes_shortfall": _avg(group["minutes_shortfall"]) if not group.empty else None,
        }
    return summary


def _shortfall_bucket(value: Any) -> str:
    shortfall = _to_float(value)
    if shortfall is None:
        return "missing_actual_or_basis"
    if shortfall <= 0:
        return "no_shortfall_or_under"
    if shortfall < 3:
        return "shortfall_0_3"
    if shortfall < 6:
        return "shortfall_3_6"
    if shortfall < 10:
        return "shortfall_6_10"
    return "shortfall_10_plus"


def _shortfall_bucket_summary(frame: pd.DataFrame) -> dict[str, Any]:
    buckets = {
        "missing_actual_or_basis": {},
        "no_shortfall_or_under": {},
        "shortfall_0_3": {},
        "shortfall_3_6": {},
        "shortfall_6_10": {},
        "shortfall_10_plus": {},
    }
    if frame.empty:
        return {
            key: {"rows": 0, "hits": 0, "misses": 0, "pushes": 0, "miss_rate": None}
            for key in buckets
        }
    working = frame.copy()
    working["_shortfall_bucket"] = working["minutes_shortfall"].map(_shortfall_bucket)
    out: dict[str, Any] = {}
    for bucket in buckets:
        group = working[working["_shortfall_bucket"].eq(bucket)]
        hits = int(group["result_status"].eq("hit").sum())
        misses = int(group["result_status"].eq("miss").sum())
        pushes = int(group["result_status"].eq("push").sum())
        denominator = hits + misses
        out[bucket] = {
            "rows": int(len(group)),
            "hits": hits,
            "misses": misses,
            "pushes": pushes,
            "miss_rate": round(misses / denominator, 4) if denominator else None,
        }
    return out


def select_readiness_verdict(
    *,
    total_player_points_rows: int,
    low_line_over_rows: int,
    minutes_basis_available_rate: float,
    actual_minutes_available_rate: float,
    low_line_over_misses: int,
    low_line_over_result_summary: Mapping[str, Any],
) -> str:
    if total_player_points_rows <= 0:
        return "NO_PLAYER_POINTS_HISTORY"
    if low_line_over_rows <= 0:
        return "LOW_LINE_OVER_SAMPLE_MISSING"
    if minutes_basis_available_rate <= 0:
        return "MINUTES_FIELDS_UNAVAILABLE"
    if actual_minutes_available_rate <= 0:
        return "ACTUAL_MINUTES_UNAVAILABLE_MINUTES_BASIS_READY"
    miss_shortfall = _to_float((low_line_over_result_summary.get("miss") or {}).get("avg_minutes_shortfall"))
    hit_shortfall = _to_float((low_line_over_result_summary.get("hit") or {}).get("avg_minutes_shortfall")) or 0.0
    if low_line_over_misses > 0 and miss_shortfall is not None and miss_shortfall > max(0.0, hit_shortfall):
        return "MINUTES_SHORTFALL_SIGNAL_PRESENT"
    return "MINUTES_ERROR_SHADOW_READY"


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


def build_minutes_error_shadow_audit(
    prediction_date: str,
    *,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    pick_history: str | Path | pd.DataFrame | None = None,
    market_shadow_history: str | Path | pd.DataFrame | None = None,
    player_baselines: str | Path | pd.DataFrame | None = None,
    full_market_glob: str | Path | None = None,
) -> dict[str, Any]:
    """Build the Phase 15C minutes-error shadow audit payload."""
    runtime_root = Path(runtime_root)
    pick_history = _resolve_history_path(runtime_root, "pick_history.csv", pick_history)
    market_shadow_history = _resolve_history_path(runtime_root, "market_shadow_history.csv", market_shadow_history)
    player_baselines = player_baselines if player_baselines is not None else runtime_root.parent / "model" / "player_baselines.csv"
    full_market_glob = full_market_glob or runtime_root / "operator" / "full_market_board_*.csv"

    pick_df = _read_csv(pick_history)
    shadow_df = _read_csv(market_shadow_history)
    baseline_df = _read_csv(player_baselines)

    history_frames = [
        _source_frame(pick_df, source_type="pick_history", source_file=str(pick_history)),
        _source_frame(shadow_df, source_type="market_shadow_history", source_file=str(market_shadow_history)),
    ]
    base = _coalesce_history_rows(history_frames)

    source_frames: list[pd.DataFrame] = list(history_frames)
    if not baseline_df.empty:
        source_frames.append(_source_frame(baseline_df, source_type="player_baselines", source_file=str(player_baselines)))
    for path_text in sorted(glob.glob(str(full_market_glob))):
        frame = _read_csv(path_text)
        if not frame.empty:
            source_frames.append(_source_frame(frame, source_type="full_market_board", source_file=path_text))

    working = _apply_minutes_sources(base, source_frames)
    if not working.empty:
        basis_values: list[float | None] = []
        basis_sources: list[str] = []
        reliable_flags: list[bool] = []
        for _, row in working.iterrows():
            basis, source = _minutes_basis(row)
            basis_values.append(basis)
            basis_sources.append(source)
            reliable_flags.append(_has_reliable_minutes(row))
        working["minutes_basis"] = basis_values
        working["minutes_basis_source"] = basis_sources
        working["has_reliable_minutes"] = reliable_flags
        working["minutes_shortfall"] = [
            (_to_float(row.get("minutes_basis")) - _to_float(row.get("actual_minutes")))
            if _to_float(row.get("minutes_basis")) is not None and _to_float(row.get("actual_minutes")) is not None
            else None
            for _, row in working.iterrows()
        ]

    pp_rows = working[working["market_type"].eq("player_points")].copy() if not working.empty else pd.DataFrame()
    pp_over = pp_rows[pp_rows["selection"].eq("over")].copy() if not pp_rows.empty else pd.DataFrame()
    low_line_over = pp_over[pp_over["line"].map(lambda value: (_to_float(value) is not None) and (_to_float(value) < 15.0))].copy() if not pp_over.empty else pd.DataFrame()
    no_minutes = pp_rows[~pp_rows["has_reliable_minutes"].map(bool)].copy() if not pp_rows.empty else pd.DataFrame()
    low_summary = _result_summary(low_line_over)

    fields_availability = {
        "projected_minutes": _availability(pp_rows, "projected_minutes"),
        "minutes_recent": _availability(pp_rows, "minutes_recent"),
        "minutes_avg": _availability(pp_rows, "minutes_avg"),
        "manual_minutes_limit": _availability(pp_rows, "manual_minutes_limit"),
        "actual_minutes": _availability(pp_rows, "actual_minutes"),
        "actual_value": _availability(pp_rows, "actual_value"),
        "minutes_basis": _availability(pp_rows, "minutes_basis"),
    }
    low_line_basis_rate = _availability(low_line_over, "minutes_basis")
    low_line_actual_minutes_rate = _availability(low_line_over, "actual_minutes")
    low_line_over_misses = int(low_line_over["result_status"].eq("miss").sum()) if not low_line_over.empty else 0
    readiness = select_readiness_verdict(
        total_player_points_rows=int(len(pp_rows)),
        low_line_over_rows=int(len(low_line_over)),
        minutes_basis_available_rate=low_line_basis_rate,
        actual_minutes_available_rate=low_line_actual_minutes_rate,
        low_line_over_misses=low_line_over_misses,
        low_line_over_result_summary=low_summary,
    )

    no_minutes_sample = []
    for _, row in no_minutes.head(20).iterrows():
        no_minutes_sample.append(
            {
                "prediction_date": row.get("prediction_date"),
                "player_name": row.get("player_name"),
                "market_type": row.get("market_type"),
                "selection": row.get("selection"),
                "line": _to_float(row.get("line")),
                "result_status": row.get("result_status"),
            }
        )

    payload = {
        "prediction_date": prediction_date,
        "note": "audit_only_no_prediction_grading_kelly_or_history_change",
        "total_player_points_rows": int(len(pp_rows)),
        "player_points_over_rows": int(len(pp_over)),
        "low_line_over_rows": int(len(low_line_over)),
        "low_line_over_misses": low_line_over_misses,
        "minutes_fields_availability": fields_availability,
        "avg_projected_minutes_by_result": {
            result: low_summary[result]["avg_projected_minutes"] for result in RESULTS
        },
        "avg_minutes_basis_by_result": {
            result: low_summary[result]["avg_minutes_basis"] for result in RESULTS
        },
        "player_points_over_result_summary": _result_summary(pp_over),
        "low_line_over_result_summary": low_summary,
        "minutes_shortfall_buckets": _shortfall_bucket_summary(low_line_over),
        "rows_without_reliable_minutes_count": int(len(no_minutes)),
        "rows_without_reliable_minutes_sample": no_minutes_sample,
        "readiness_verdict": readiness,
        "history_mutated": False,
        "source_files_scanned": {
            "pick_history": str(pick_history),
            "market_shadow_history": str(market_shadow_history),
            "player_baselines": str(player_baselines),
            "full_market_glob": str(full_market_glob),
            "full_market_files": sorted(glob.glob(str(full_market_glob))),
        },
    }
    return _json_safe(payload)


def _format_pct(value: Any) -> str:
    number = _to_float(value)
    return "n/a" if number is None else f"{number * 100:.1f}%"


def _format_num(value: Any) -> str:
    number = _to_float(value)
    return "n/a" if number is None else f"{number:.3f}"


def _format_txt(payload: Mapping[str, Any], prediction_date: str) -> str:
    sep = "=" * 78
    sep2 = "-" * 78
    fields = payload.get("minutes_fields_availability", {}) if isinstance(payload.get("minutes_fields_availability"), dict) else {}
    low_summary = payload.get("low_line_over_result_summary", {}) if isinstance(payload.get("low_line_over_result_summary"), dict) else {}
    lines = [
        f"{sep}\n",
        "MINUTES-ERROR SHADOW AUDIT (Phase 15C -- AUDIT ONLY)\n",
        f"date: {prediction_date}    note: {payload.get('note', '')}\n",
        f"{sep}\n\n",
        "OVERVIEW\n",
        f"{sep2}\n",
        f"  total_player_points_rows       : {payload.get('total_player_points_rows', 0)}\n",
        f"  player_points_over_rows        : {payload.get('player_points_over_rows', 0)}\n",
        f"  low_line_over_rows             : {payload.get('low_line_over_rows', 0)}\n",
        f"  low_line_over_misses           : {payload.get('low_line_over_misses', 0)}\n",
        f"  rows_without_reliable_minutes  : {payload.get('rows_without_reliable_minutes_count', 0)}\n",
        f"  readiness_verdict              : {payload.get('readiness_verdict')}\n\n",
        "MINUTES FIELD AVAILABILITY\n",
        f"{sep2}\n",
    ]
    for field in ("projected_minutes", "minutes_recent", "minutes_avg", "manual_minutes_limit", "actual_minutes", "minutes_basis"):
        lines.append(f"  {field:28}: {_format_pct(fields.get(field))}\n")
    lines.extend(
        [
            "\nLOW-LINE OVER RESULT SUMMARY\n",
            f"{sep2}\n",
        ]
    )
    for result in RESULTS:
        row = low_summary.get(result, {}) if isinstance(low_summary.get(result, {}), Mapping) else {}
        lines.append(
            f"  {result}: rows={row.get('rows', 0)} "
            f"avg_projected_minutes={_format_num(row.get('avg_projected_minutes'))} "
            f"avg_minutes_basis={_format_num(row.get('avg_minutes_basis'))} "
            f"avg_actual_minutes={_format_num(row.get('avg_actual_minutes'))} "
            f"avg_minutes_shortfall={_format_num(row.get('avg_minutes_shortfall'))}\n"
        )
    lines.extend(
        [
            "\nDIAGNOSTIC QUESTIONS\n",
            f"{sep2}\n",
            "  Q: Are low-line player_points OVER misses correlated with minutes shortfall?\n",
            f"     readiness_verdict={payload.get('readiness_verdict')}\n",
            "  Q: Did this audit change predictions, grading, Kelly, or history?\n",
            "     No. Diagnostic artifacts only.\n",
            f"\n{sep}\n",
        ]
    )
    return "".join(lines)


def write_minutes_error_shadow_audit(
    prediction_date: str,
    *,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    pick_history: str | Path | pd.DataFrame | None = None,
    market_shadow_history: str | Path | pd.DataFrame | None = None,
    player_baselines: str | Path | pd.DataFrame | None = None,
    full_market_glob: str | Path | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    payload = build_minutes_error_shadow_audit(
        prediction_date,
        runtime_root=runtime_root,
        pick_history=pick_history,
        market_shadow_history=market_shadow_history,
        player_baselines=player_baselines,
        full_market_glob=full_market_glob,
    )
    json_path = minutes_error_audit_json_path_for_date(prediction_date, runtime_root)
    txt_path = minutes_error_audit_txt_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    txt_path.write_text(_format_txt(payload, prediction_date), encoding="utf-8")
    return json_path, txt_path, payload


__all__ = [
    "READINESS_VERDICTS",
    "build_minutes_error_shadow_audit",
    "minutes_error_audit_json_path_for_date",
    "minutes_error_audit_txt_path_for_date",
    "select_readiness_verdict",
    "write_minutes_error_shadow_audit",
]
