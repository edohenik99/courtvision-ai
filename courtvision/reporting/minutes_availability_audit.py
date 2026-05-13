"""Phase 15A minutes availability audit.

Diagnostic-only report for deciding whether CourtVision has enough minutes data
to explain player_points projection misses and low-line OVER inflation.

This module does not modify predictions, scoring, selection, Kelly, grading,
odds normalization, candidate generation, or history rows.
"""
from __future__ import annotations

import glob
import json
import math
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


DEFAULT_RUNTIME_ROOT = "outputs/runtime"
DEFAULT_HISTORY_CSV = "data/history/market_shadow_history.csv"
DEFAULT_PICK_HISTORY_CSV = "data/history/pick_history.csv"

READINESS_VERDICTS = {
    "ACTUAL_MINUTES_UNAVAILABLE",
    "PROJECTED_MINUTES_AVAILABLE_ACTUAL_MISSING",
    "MINUTES_ERROR_ANALYSIS_READY",
    "LOW_MINUTE_OVER_RISK_CONFIRMED",
    "MINUTES_FIELD_COVERAGE_INSUFFICIENT",
    "READY_FOR_PHASE_15B_USAGE_AUDIT",
    "READY_FOR_PHASE_15C_LOW_LINE_OVER_GUARD_REVIEW",
}

FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "projected_minutes": (
        "projected_minutes",
        "minutes_projected",
        "expected_minutes",
        "projected_min",
    ),
    "minutes_recent": ("minutes_recent", "min_recent", "recent_minutes"),
    "minutes_avg": ("minutes_avg", "min_avg", "average_minutes", "avg_minutes"),
    "minutes_bucket": ("minutes_bucket", "projected_minutes_bucket"),
    "actual_minutes": (
        "actual_minutes",
        "minutes_actual",
        "actual_min",
        "actual_min_played",
    ),
    "game_status": (
        "game_status",
        "game_status_bucket",
        "status",
        "result_status",
        "final_status",
        "game_final",
        "is_final",
    ),
    "player_points_projection": (
        "model_projection",
        "projection",
        "projected_value",
        "predicted_value",
        "player_points_projection",
        "recalibrated_projection",
        "baseline_projection",
    ),
    "actual_player_points": (
        "actual_value",
        "actual_points",
        "points",
        "pts",
        "result_value",
    ),
    "line": ("line", "sportsbook_line", "line_value", "market_line"),
    "selection": ("selection", "side", "pick_side"),
    "edge": ("edge", "side_edge", "edge_abs", "edge_pct", "side_edge_pct", "dir_edge"),
    "confidence": (
        "confidence",
        "quality_score",
        "selection_score",
        "base_confidence",
        "final_confidence",
    ),
    "context": (
        "context_pick_alignment",
        "context_caution_level",
        "context_conflict_cause",
        "pace_context_signal",
        "defense_context_signal",
        "overall_context_signal",
    ),
    "true_usage_rate": ("true_usage_rate", "usage_rate"),
    "projection_provenance": (
        "projection_source",
        "projection_method",
        "projection_provenance",
        "projection_components",
    ),
}


def minutes_audit_json_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"minutes_availability_audit_{date}.json"


def minutes_audit_txt_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "operator" / f"minutes_availability_audit_{date}.txt"


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
    try:
        parsed = float(text)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _round(value: Any, digits: int = 4) -> float | None:
    number = _to_float(value)
    return None if number is None else round(number, digits)


def _has_value(value: Any) -> bool:
    if _to_float(value) is not None:
        return True
    return bool(_safe_text(value))


def _value_rate(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return 0.0
    return round(float(frame[column].map(_has_value).mean()), 4)


def _numeric_rate(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return 0.0
    return round(float(frame[column].map(lambda value: _to_float(value) is not None).mean()), 4)


def _series_has_non_na(values: pd.Series) -> bool:
    return not values.empty and not bool(values.isna().all())


def _coalesce_series(series_list: list[pd.Series], index: pd.Index) -> pd.Series:
    out = pd.Series([pd.NA] * len(index), index=index, dtype="object")
    for values in series_list:
        aligned = values.reindex(index)
        if not _series_has_non_na(aligned):
            continue
        fill_mask = out.isna() & aligned.notna()
        if fill_mask.any():
            out.loc[fill_mask] = aligned.loc[fill_mask]
    return out


def _coalesce_numeric(df: pd.DataFrame, candidates: tuple[str, ...]) -> pd.Series:
    values_by_column: list[pd.Series] = []
    for column in candidates:
        if column not in df.columns:
            continue
        values = df[column].map(_to_float)
        if _series_has_non_na(values):
            values_by_column.append(values)
    return _coalesce_series(values_by_column, df.index)


def _coalesce_text(df: pd.DataFrame, candidates: tuple[str, ...]) -> pd.Series:
    out = pd.Series([""] * len(df), index=df.index, dtype="object")
    for column in candidates:
        if column not in df.columns:
            continue
        values = df[column].map(_safe_text)
        out = out.where(out.map(bool), values)
    return out


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


def _normalize_result_status(row: Mapping[str, Any]) -> str:
    status = _safe_text(row.get("result_status")).lower()
    if status in {"hit", "miss", "push"}:
        return status
    hit_value = row.get("hit")
    if isinstance(hit_value, bool):
        return "hit" if hit_value else "miss"
    text = _safe_text(hit_value).lower()
    if text in {"1", "true", "yes", "y"}:
        return "hit"
    if text in {"0", "false", "no", "n"}:
        return "miss"
    return status


def _minutes_bucket(value: Any) -> str:
    minutes = _to_float(value)
    if minutes is None:
        return "unknown"
    if minutes < 15:
        return "under_15"
    if minutes < 20:
        return "15_20"
    if minutes < 24:
        return "20_24"
    if minutes < 28:
        return "24_28"
    if minutes < 32:
        return "28_32"
    return "32_plus"


def _line_bucket(value: Any) -> str:
    line = _to_float(value)
    if line is None:
        return "unknown"
    if line < 8.5:
        return "below_8.5"
    if line <= 14.5:
        return "8.5_14.5"
    if line <= 20.5:
        return "15_20.5"
    return "21_plus"


def _volatility_bucket(value: Any) -> str:
    delta = _to_float(value)
    if delta is None:
        return "unknown"
    abs_delta = abs(delta)
    if abs_delta < 3:
        return "0_3"
    if abs_delta < 6:
        return "3_6"
    if abs_delta < 10:
        return "6_10"
    return "10_plus"


def _is_hit(status: Any) -> bool:
    return _safe_text(status).lower() == "hit"


def _is_miss(status: Any) -> bool:
    return _safe_text(status).lower() == "miss"


def _is_graded(status: Any) -> bool:
    return _safe_text(status).lower() in {"hit", "miss", "push"}


def _is_low_line_over(row: Mapping[str, Any]) -> bool:
    line = _to_float(row.get("line"))
    return _safe_text(row.get("selection")).lower() == "over" and line is not None and line < 15.0


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


def _read_csv_glob(pattern: str | Path | None) -> list[tuple[str, pd.DataFrame]]:
    if not pattern:
        return []
    frames: list[tuple[str, pd.DataFrame]] = []
    for path_text in sorted(glob.glob(str(pattern))):
        path = Path(path_text)
        frame = _read_csv(path)
        if not frame.empty:
            frames.append((str(path), frame))
    return frames


def _json_keys(value: Any, depth: int = 0) -> set[str]:
    if value is None or depth > 5:
        return set()
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_json_keys(child, depth + 1))
    elif isinstance(value, list):
        for child in value[:10]:
            keys.update(_json_keys(child, depth + 1))
    return keys


def _load_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_source_frame(
    frame: pd.DataFrame,
    *,
    source_type: str,
    source_file: str,
) -> pd.DataFrame:
    source = frame.copy(deep=True)
    out = pd.DataFrame(index=source.index)
    out["source_type"] = source_type
    out["source_file"] = source_file
    out["prediction_date"] = _coalesce_text(source, ("prediction_date", "date", "game_date"))
    out["player_id"] = _coalesce_text(source, ("player_id",))
    out["player_name"] = _coalesce_text(source, ("player_name", "entity_name", "name"))
    out["game_id"] = _coalesce_text(source, ("game_id",))
    out["market_type"] = _coalesce_text(source, ("market_type", "market", "prop_type")).map(_normalize_market)
    out["selection"] = _coalesce_text(source, FIELD_CANDIDATES["selection"]).map(_normalize_selection)
    out["line"] = _coalesce_numeric(source, FIELD_CANDIDATES["line"])
    out["projected_minutes"] = _coalesce_numeric(source, FIELD_CANDIDATES["projected_minutes"])
    out["minutes_recent"] = _coalesce_numeric(source, FIELD_CANDIDATES["minutes_recent"])
    out["minutes_avg"] = _coalesce_numeric(source, FIELD_CANDIDATES["minutes_avg"])
    out["actual_minutes"] = _coalesce_numeric(source, FIELD_CANDIDATES["actual_minutes"])
    out["minutes_bucket_raw"] = _coalesce_text(source, FIELD_CANDIDATES["minutes_bucket"])
    out["model_projection"] = _coalesce_numeric(source, FIELD_CANDIDATES["player_points_projection"])
    out["actual_value"] = _coalesce_numeric(source, FIELD_CANDIDATES["actual_player_points"])
    out["edge"] = _coalesce_numeric(source, FIELD_CANDIDATES["edge"])
    out["confidence"] = _coalesce_numeric(source, FIELD_CANDIDATES["confidence"])
    out["context_pick_alignment"] = _coalesce_text(source, ("context_pick_alignment",))
    out["context_caution_level"] = _coalesce_text(source, ("context_caution_level",))
    out["context_conflict_cause"] = _coalesce_text(source, ("context_conflict_cause",))
    out["pace_context_signal"] = _coalesce_text(source, ("pace_context_signal",))
    out["defense_context_signal"] = _coalesce_text(source, ("defense_context_signal",))
    out["overall_context_signal"] = _coalesce_text(source, ("overall_context_signal",))
    out["game_status"] = _coalesce_text(source, FIELD_CANDIDATES["game_status"])
    out["result_status"] = [
        _normalize_result_status(row)
        for row in source.to_dict("records")
    ]
    out["minutes_basis"] = _coalesce_series(
        [out["projected_minutes"], out["minutes_recent"], out["minutes_avg"]],
        out.index,
    )
    out["minutes_basis_source"] = ""
    out.loc[out["projected_minutes"].map(_to_float).notna(), "minutes_basis_source"] = "projected_minutes"
    out.loc[
        out["minutes_basis_source"].eq("") & out["minutes_recent"].map(_to_float).notna(),
        "minutes_basis_source",
    ] = "minutes_recent"
    out.loc[
        out["minutes_basis_source"].eq("") & out["minutes_avg"].map(_to_float).notna(),
        "minutes_basis_source",
    ] = "minutes_avg"
    out["minutes_bucket"] = out["minutes_basis"].map(_minutes_bucket)
    has_raw_bucket = out["minutes_bucket_raw"].map(bool)
    out.loc[has_raw_bucket, "minutes_bucket"] = out.loc[has_raw_bucket, "minutes_bucket_raw"]
    out["line_bucket"] = out["line"].map(_line_bucket)
    out["minutes_delta"] = out.apply(
        lambda row: (
            _to_float(row["minutes_recent"]) - _to_float(row["minutes_avg"])
            if _to_float(row["minutes_recent"]) is not None and _to_float(row["minutes_avg"]) is not None
            else None
        ),
        axis=1,
    )
    out["minutes_delta_abs_bucket"] = out["minutes_delta"].map(_volatility_bucket)
    out["projection_error"] = out.apply(
        lambda row: (
            _to_float(row["model_projection"]) - _to_float(row["actual_value"])
            if _to_float(row["model_projection"]) is not None and _to_float(row["actual_value"]) is not None
            else None
        ),
        axis=1,
    )
    out["minutes_error"] = out.apply(
        lambda row: (
            _to_float(row["projected_minutes"]) - _to_float(row["actual_minutes"])
            if _to_float(row["projected_minutes"]) is not None and _to_float(row["actual_minutes"]) is not None
            else None
        ),
        axis=1,
    )
    out["is_low_line_over"] = out.apply(_is_low_line_over, axis=1)
    return out.reset_index(drop=True)


def _row_key(row: Mapping[str, Any], fallback_index: int) -> str:
    date = _safe_text(row.get("prediction_date"))
    player_id = _safe_text(row.get("player_id"))
    player_name = _safe_text(row.get("player_name")).lower()
    player = f"id:{player_id}" if player_id else f"name:{player_name}"
    market = _safe_text(row.get("market_type")).lower()
    selection = _safe_text(row.get("selection")).lower()
    line = _to_float(row.get("line"))
    line_key = "" if line is None else f"{line:.3f}"
    game_id = _safe_text(row.get("game_id"))
    if not (date and player and market):
        return f"row:{fallback_index}:{_safe_text(row.get('source_type'))}:{_safe_text(row.get('source_file'))}"
    return "|".join([date, player, game_id, market, selection, line_key])


def _first_present(values: pd.Series) -> Any:
    for value in values:
        if _has_value(value):
            return value
    return None


def _coalesce_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    working = frame.copy()
    working["_audit_key"] = [
        _row_key(row, index)
        for index, row in enumerate(working.to_dict("records"))
    ]
    rows: list[dict[str, Any]] = []
    for key, group in working.groupby("_audit_key", sort=False, dropna=False):
        merged: dict[str, Any] = {"_audit_key": key}
        for column in working.columns:
            if column == "_audit_key":
                continue
            if column in {"source_type", "source_file"}:
                values = sorted({_safe_text(value) for value in group[column] if _safe_text(value)})
                merged[column] = ";".join(values)
            elif column == "is_low_line_over":
                merged[column] = bool(group[column].map(bool).any())
            else:
                merged[column] = _first_present(group[column])
        rows.append(merged)
    out = pd.DataFrame(rows)
    if "minutes_delta" in out.columns:
        out["minutes_delta_abs_bucket"] = out["minutes_delta"].map(_volatility_bucket)
    if "is_low_line_over" in out.columns:
        out["is_low_line_over"] = out.apply(_is_low_line_over, axis=1)
    return out.reset_index(drop=True)


def _is_all_na_frame(frame: pd.DataFrame) -> bool:
    return frame.empty or bool(frame.isna().to_numpy().all())


def _concat_normalized_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    usable = [frame for frame in frames if not _is_all_na_frame(frame)]
    if not usable:
        return pd.DataFrame()

    columns: list[str] = []
    seen: set[str] = set()
    trimmed: list[pd.DataFrame] = []
    for frame in usable:
        for column in frame.columns:
            if column not in seen:
                columns.append(column)
                seen.add(column)
        trimmed.append(frame.dropna(axis=1, how="all"))

    return pd.concat(trimmed, ignore_index=True).reindex(columns=columns)


def _field_source_columns(frame: pd.DataFrame, field: str) -> list[str]:
    return [column for column in FIELD_CANDIDATES[field] if column in frame.columns]


def _field_availability(
    sources: list[tuple[str, str, pd.DataFrame]],
    merged: pd.DataFrame,
    json_field_presence: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    by_source: dict[str, Any] = {}
    for label, source_file, frame in sources:
        normalized = _normalize_source_frame(frame, source_type=label, source_file=source_file)
        by_source.setdefault(label, {"files": 0, "rows": 0, "fields": {}})
        by_source[label]["files"] += 1
        by_source[label]["rows"] += int(len(normalized))
        for field in FIELD_CANDIDATES:
            entry = by_source[label]["fields"].setdefault(
                field,
                {"source_columns": set(), "available_count": 0, "available_rate": 0.0},
            )
            entry["source_columns"].update(_field_source_columns(frame, field))
            norm_col = field if field in normalized.columns else None
            if field == "player_points_projection":
                norm_col = "model_projection"
            elif field == "actual_player_points":
                norm_col = "actual_value"
            elif field == "selection":
                norm_col = "selection"
            elif field == "line":
                norm_col = "line"
            elif field == "edge":
                norm_col = "edge"
            elif field == "confidence":
                norm_col = "confidence"
            elif field == "context":
                context_cols = [
                    "context_pick_alignment",
                    "context_caution_level",
                    "context_conflict_cause",
                    "pace_context_signal",
                    "defense_context_signal",
                    "overall_context_signal",
                ]
                entry["available_count"] += int(
                    normalized[context_cols].apply(lambda row: any(_has_value(v) for v in row), axis=1).sum()
                )
                continue
            if norm_col and norm_col in normalized.columns:
                entry["available_count"] += int(normalized[norm_col].map(_has_value).sum())

    for label, payload in by_source.items():
        rows = max(1, int(payload["rows"]))
        for field, entry in payload["fields"].items():
            entry["source_columns"] = sorted(entry["source_columns"])
            entry["available_rate"] = round(entry["available_count"] / rows, 4)

    if json_field_presence:
        for label, entry in json_field_presence.items():
            by_source[label] = entry

    merged_fields: dict[str, Any] = {
        "projected_minutes": {
            "available_rate": _numeric_rate(merged, "projected_minutes"),
            "source_columns": list(FIELD_CANDIDATES["projected_minutes"]),
        },
        "minutes_recent": {
            "available_rate": _numeric_rate(merged, "minutes_recent"),
            "source_columns": list(FIELD_CANDIDATES["minutes_recent"]),
        },
        "minutes_avg": {
            "available_rate": _numeric_rate(merged, "minutes_avg"),
            "source_columns": list(FIELD_CANDIDATES["minutes_avg"]),
        },
        "actual_minutes": {
            "available_rate": _numeric_rate(merged, "actual_minutes"),
            "source_columns": list(FIELD_CANDIDATES["actual_minutes"]),
        },
        "minutes_bucket": {
            "available_rate": _value_rate(merged, "minutes_bucket"),
            "source_columns": list(FIELD_CANDIDATES["minutes_bucket"]),
        },
        "context": {
            "available_rate": 0.0,
            "source_columns": list(FIELD_CANDIDATES["context"]),
        },
    }
    context_cols = [
        "context_pick_alignment",
        "context_caution_level",
        "context_conflict_cause",
        "pace_context_signal",
        "defense_context_signal",
        "overall_context_signal",
    ]
    if not merged.empty and all(column in merged.columns for column in context_cols):
        merged_fields["context"]["available_rate"] = round(
            float(merged[context_cols].apply(lambda row: any(_has_value(v) for v in row), axis=1).mean()),
            4,
        )
    return {"by_source": by_source, "merged": {"rows": int(len(merged)), "fields": merged_fields}}


def _safe_mean(values: list[Any]) -> float | None:
    clean = [_to_float(value) for value in values]
    clean = [value for value in clean if value is not None]
    return round(sum(clean) / len(clean), 4) if clean else None


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator <= 0 else round(numerator / denominator, 4)


def _bucket_summary(frame: pd.DataFrame, bucket_col: str) -> dict[str, Any]:
    if frame.empty or bucket_col not in frame.columns:
        return {}
    summary: dict[str, Any] = {}
    for bucket, group in frame.groupby(bucket_col, dropna=False):
        status = group["result_status"].map(_safe_text).str.lower() if "result_status" in group.columns else pd.Series([], dtype=str)
        hits = int(status.eq("hit").sum()) if not status.empty else 0
        misses = int(status.eq("miss").sum()) if not status.empty else 0
        graded_hm = hits + misses
        graded_rows = int(status.isin(["hit", "miss", "push"]).sum()) if not status.empty else 0
        low_line = group[group["is_low_line_over"].map(bool)] if "is_low_line_over" in group.columns else group.head(0)
        low_line_status = low_line["result_status"].map(_safe_text).str.lower() if not low_line.empty else pd.Series([], dtype=str)
        low_line_misses = int(low_line_status.eq("miss").sum()) if not low_line_status.empty else 0
        low_line_hit_miss = int(low_line_status.isin(["hit", "miss"]).sum()) if not low_line_status.empty else 0
        summary[_safe_text(bucket) or "unknown"] = {
            "rows": int(len(group)),
            "graded_rows": graded_rows,
            "hit_rate": _rate(hits, graded_hm),
            "miss_rate": _rate(misses, graded_hm),
            "avg_projection_error": _safe_mean(group.get("projection_error", pd.Series(dtype=object)).tolist()),
            "low_line_over_count": int(len(low_line)),
            "low_line_over_miss_count": low_line_misses,
            "low_line_over_miss_rate": _rate(low_line_misses, low_line_hit_miss),
        }
    return summary


def _actual_minutes_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "actual_minutes_available": False,
            "minutes_error_available": False,
            "message": "actual minutes are unavailable",
        }
    actual_rate = _numeric_rate(frame, "actual_minutes")
    error_rate = _numeric_rate(frame, "minutes_error")
    actual_rows = frame[frame["actual_minutes"].map(lambda value: _to_float(value) is not None)] if "actual_minutes" in frame.columns else frame.head(0)
    error_rows = frame[frame["minutes_error"].map(lambda value: _to_float(value) is not None)] if "minutes_error" in frame.columns else frame.head(0)
    out = {
        "actual_minutes_available": actual_rate > 0,
        "actual_minutes_available_rate": actual_rate,
        "minutes_error_available": error_rate > 0,
        "minutes_error_available_rate": error_rate,
        "actual_minutes_rows": int(len(actual_rows)),
        "minutes_error_rows": int(len(error_rows)),
        "avg_minutes_error": _safe_mean(error_rows.get("minutes_error", pd.Series(dtype=object)).tolist()),
        "overprojected_minutes_rate": None,
        "player_points_miss_rate_minutes_error_gt_5": None,
        "player_points_miss_rate_minutes_error_gt_10": None,
    }
    if not error_rows.empty:
        errors = error_rows["minutes_error"].map(_to_float)
        out["overprojected_minutes_rate"] = round(float((errors > 0).mean()), 4)
        for threshold in (5, 10):
            subset = error_rows[errors > threshold]
            status = subset["result_status"].map(_safe_text).str.lower() if not subset.empty else pd.Series([], dtype=str)
            hit_miss = int(status.isin(["hit", "miss"]).sum()) if not status.empty else 0
            misses = int(status.eq("miss").sum()) if not status.empty else 0
            out[f"player_points_miss_rate_minutes_error_gt_{threshold}"] = _rate(misses, hit_miss)
    if actual_rate == 0:
        out["message"] = (
            "actual minutes are unavailable; a provider/player box-score stat source "
            "with final minutes is needed before minutes_error can be computed"
        )
    elif error_rate == 0:
        out["message"] = (
            "actual minutes exist but explicit projected_minutes are unavailable; "
            "minutes_error was not computed from average/recent minutes proxies"
        )
    else:
        out["message"] = "actual minutes and explicit projected minutes are available"
    return out


def _player_breakdown(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    pp_over = frame[
        frame["market_type"].map(_safe_text).str.lower().eq("player_points")
        & frame["selection"].map(_safe_text).str.lower().eq("over")
    ].copy()
    if pp_over.empty:
        return []
    misses = pp_over[pp_over["result_status"].map(_safe_text).str.lower().eq("miss")]
    names = misses["player_name"].map(_safe_text)
    top_names = names[names.ne("")].value_counts().head(10).index.tolist()
    rows: list[dict[str, Any]] = []
    for name in top_names:
        player_rows = pp_over[pp_over["player_name"].map(_safe_text).eq(name)]
        player_misses = player_rows[player_rows["result_status"].map(_safe_text).str.lower().eq("miss")]
        low_line = player_rows[player_rows["is_low_line_over"].map(bool)]
        low_line_misses = low_line[low_line["result_status"].map(_safe_text).str.lower().eq("miss")]
        rows.append(
            {
                "player_name": name,
                "sample_size": int(len(player_rows)),
                "miss_count": int(len(player_misses)),
                "low_line_over_count": int(len(low_line)),
                "low_line_over_miss_count": int(len(low_line_misses)),
                "avg_projected_minutes": _safe_mean(player_rows["projected_minutes"].tolist()),
                "avg_recent_minutes": _safe_mean(player_rows["minutes_recent"].tolist()),
                "avg_average_minutes": _safe_mean(player_rows["minutes_avg"].tolist()),
                "actual_minutes_available_rate": _numeric_rate(player_rows, "actual_minutes"),
                "avg_projection_error": _safe_mean(player_rows["projection_error"].tolist()),
            }
        )
    rows.sort(key=lambda row: (row["miss_count"], row["low_line_over_miss_count"]), reverse=True)
    return rows


def _json_field_presence(paths: list[str], label: str) -> dict[str, Any]:
    fields_present: dict[str, bool] = {field: False for field in FIELD_CANDIDATES}
    files = 0
    for path in paths:
        payload = _load_json(path)
        if not payload:
            continue
        files += 1
        keys = _json_keys(payload)
        for field, candidates in FIELD_CANDIDATES.items():
            if any(candidate in keys for candidate in candidates):
                fields_present[field] = True
    return {
        "files": files,
        "rows": 0,
        "fields": {
            field: {
                "source_columns": [candidate for candidate in FIELD_CANDIDATES[field] if fields_present[field]],
                "available_count": 0,
                "available_rate": None,
                "field_seen_in_json": seen,
            }
            for field, seen in fields_present.items()
        },
        "json_only": True,
        "label": label,
    }


def _latest_phase_13b_link(
    prediction_date: str,
    runtime_root: Path,
    inflation_audit_glob: str | Path | None,
) -> dict[str, Any]:
    target = runtime_root / "diagnostics" / f"player_points_inflation_audit_{prediction_date}.json"
    path: Path | None = target if target.exists() else None
    if path is None and inflation_audit_glob:
        matches = sorted(Path(p) for p in glob.glob(str(inflation_audit_glob)))
        if matches:
            path = matches[-1]
    if path is None:
        return {
            "source_json": None,
            "dominant_failure_mode": None,
            "readiness_verdict": None,
            "actual_minutes_status": "unavailable",
            "overlap_assessment": "blocked_by_missing_phase_13b_audit",
        }
    payload = _load_json(path)
    field_availability = payload.get("field_availability", {}) if isinstance(payload, dict) else {}
    actual_status = field_availability.get("actual_minutes", "unknown") if isinstance(field_availability, dict) else "unknown"
    return {
        "source_json": str(path),
        "dominant_failure_mode": payload.get("dominant_failure_mode"),
        "readiness_verdict": payload.get("readiness_verdict"),
        "actual_minutes_status": actual_status,
        "overlap_assessment": "supported_by_minutes_audit_if_low_line_minutes_summary_has_signal",
    }


def _missing_critical_fields(pp_rows: pd.DataFrame) -> list[str]:
    checks = {
        "projected_minutes": _numeric_rate(pp_rows, "projected_minutes"),
        "minutes_recent": _numeric_rate(pp_rows, "minutes_recent"),
        "minutes_avg": _numeric_rate(pp_rows, "minutes_avg"),
        "actual_minutes": _numeric_rate(pp_rows, "actual_minutes"),
        "true_usage_rate": 0.0,
        "row_level_projection_provenance": 0.0,
    }
    return [field for field, rate in checks.items() if rate <= 0]


def _low_minute_risk_confirmed(
    low_line_minutes_summary: Mapping[str, Any],
    low_line_over_misses: int,
) -> bool:
    if low_line_over_misses < 3:
        return False
    low_buckets = {"under_15", "15_20", "20_24"}
    low_bucket_misses = 0
    for bucket in low_buckets:
        row = low_line_minutes_summary.get(bucket, {})
        if isinstance(row, Mapping):
            low_bucket_misses += int(row.get("low_line_over_miss_count") or 0)
    return low_bucket_misses / max(1, low_line_over_misses) >= 0.5


def _select_readiness_verdict(
    *,
    player_points_rows: int,
    projected_rate: float,
    recent_rate: float,
    average_rate: float,
    actual_rate: float,
    minutes_error_rate: float,
    low_line_over_misses: int,
    low_line_minutes_summary: Mapping[str, Any],
) -> str:
    minutes_any_rate = max(projected_rate, recent_rate, average_rate)
    if player_points_rows <= 0 or minutes_any_rate <= 0:
        return "MINUTES_FIELD_COVERAGE_INSUFFICIENT"
    if actual_rate <= 0:
        return "PROJECTED_MINUTES_AVAILABLE_ACTUAL_MISSING"
    if minutes_error_rate <= 0:
        return "MINUTES_FIELD_COVERAGE_INSUFFICIENT"
    if _low_minute_risk_confirmed(low_line_minutes_summary, low_line_over_misses):
        return "LOW_MINUTE_OVER_RISK_CONFIRMED"
    if low_line_over_misses >= 3 and actual_rate >= 0.5:
        return "READY_FOR_PHASE_15C_LOW_LINE_OVER_GUARD_REVIEW"
    if actual_rate >= 0.5 and minutes_error_rate >= 0.5:
        return "READY_FOR_PHASE_15B_USAGE_AUDIT"
    return "MINUTES_ERROR_ANALYSIS_READY"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
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


def build_minutes_availability_audit(
    prediction_date: str,
    *,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    history_csv: str | Path | pd.DataFrame | None = None,
    pick_history_csv: str | Path | pd.DataFrame | None = None,
    full_market_glob: str | Path | None = None,
    elite_board_glob: str | Path | None = None,
    player_predictions_glob: str | Path | None = None,
    grading_glob: str | Path | None = None,
    player_baselines_csv: str | Path | pd.DataFrame | None = None,
    inflation_audit_glob: str | Path | None = None,
    board_diagnostics_json_glob: str | Path | None = None,
    board_diagnostics_csv_glob: str | Path | None = None,
) -> dict[str, Any]:
    """Build the diagnostic minutes availability payload."""
    runtime_root = Path(runtime_root)
    operator_dir = runtime_root / "operator"
    research_dir = runtime_root / "research"
    diagnostics_dir = runtime_root / "diagnostics"
    model_dir = runtime_root.parent / "model"

    history_csv = _resolve_history_path(runtime_root, "market_shadow_history.csv", history_csv)
    pick_history_csv = _resolve_history_path(runtime_root, "pick_history.csv", pick_history_csv)
    player_baselines_csv = player_baselines_csv if player_baselines_csv is not None else model_dir / "player_baselines.csv"
    full_market_glob = full_market_glob or operator_dir / "full_market_board_*.csv"
    elite_board_glob = elite_board_glob or operator_dir / "elite_board_*.csv"
    player_predictions_glob = player_predictions_glob or research_dir / "player_predictions_*.csv"
    grading_glob = grading_glob or research_dir / "grading_results_*.csv"
    inflation_audit_glob = inflation_audit_glob or diagnostics_dir / "player_points_inflation_audit_*.json"
    board_diagnostics_json_glob = board_diagnostics_json_glob or diagnostics_dir / "board_diagnostics_*.json"
    board_diagnostics_csv_glob = board_diagnostics_csv_glob or diagnostics_dir / "board_diagnostics_*.csv"

    source_frames: list[tuple[str, str, pd.DataFrame]] = []
    direct_sources = [
        ("market_shadow_history", str(history_csv), _read_csv(history_csv)),
        ("pick_history", str(pick_history_csv), _read_csv(pick_history_csv)),
        ("player_baselines", str(player_baselines_csv), _read_csv(player_baselines_csv)),
    ]
    for label, source_file, frame in direct_sources:
        if not frame.empty:
            source_frames.append((label, source_file, frame))
    for label, pattern in [
        ("full_market_board", full_market_glob),
        ("elite_board", elite_board_glob),
        ("player_predictions", player_predictions_glob),
        ("grading_results", grading_glob),
        ("board_diagnostics_csv", board_diagnostics_csv_glob),
    ]:
        for source_file, frame in _read_csv_glob(pattern):
            source_frames.append((label, source_file, frame))

    normalized_frames = [
        _normalize_source_frame(frame, source_type=label, source_file=source_file)
        for label, source_file, frame in source_frames
    ]
    combined = _concat_normalized_frames(normalized_frames)
    merged = _coalesce_rows(combined)

    pp_rows = merged[merged["market_type"].map(_safe_text).str.lower().eq("player_points")].copy() if not merged.empty else pd.DataFrame()
    graded_pp = pp_rows[pp_rows["result_status"].map(_is_graded)].copy() if not pp_rows.empty else pd.DataFrame()
    pp_over = pp_rows[pp_rows["selection"].map(_safe_text).str.lower().eq("over")].copy() if not pp_rows.empty else pd.DataFrame()
    low_line_over = pp_over[pp_over["is_low_line_over"].map(bool)].copy() if not pp_over.empty else pd.DataFrame()
    low_line_over_misses = int(low_line_over["result_status"].map(_is_miss).sum()) if not low_line_over.empty else 0

    projected_rate = _numeric_rate(pp_rows, "projected_minutes")
    recent_rate = _numeric_rate(pp_rows, "minutes_recent")
    average_rate = _numeric_rate(pp_rows, "minutes_avg")
    actual_rate = _numeric_rate(pp_rows, "actual_minutes")
    minutes_error_rate = _numeric_rate(pp_rows, "minutes_error")
    low_line_minutes_summary = _bucket_summary(low_line_over, "minutes_bucket")
    pp_over_minutes_summary = _bucket_summary(pp_over, "minutes_bucket")
    volatility_summary = _bucket_summary(pp_over, "minutes_delta_abs_bucket")
    actual_summary = _actual_minutes_summary(pp_rows)

    inflation_paths = sorted(glob.glob(str(inflation_audit_glob))) if inflation_audit_glob else []
    board_diag_json_paths = sorted(glob.glob(str(board_diagnostics_json_glob))) if board_diagnostics_json_glob else []
    json_presence = {
        "player_points_inflation_audit_json": _json_field_presence(inflation_paths, "player_points_inflation_audit_json"),
        "board_diagnostics_json": _json_field_presence(board_diag_json_paths, "board_diagnostics_json"),
    }
    minutes_field_availability = _field_availability(source_frames, merged, json_presence)
    missing_critical_fields = _missing_critical_fields(pp_rows)
    readiness_verdict = _select_readiness_verdict(
        player_points_rows=int(len(pp_rows)),
        projected_rate=projected_rate,
        recent_rate=recent_rate,
        average_rate=average_rate,
        actual_rate=actual_rate,
        minutes_error_rate=minutes_error_rate,
        low_line_over_misses=low_line_over_misses,
        low_line_minutes_summary=low_line_minutes_summary,
    )

    phase_13b_link = _latest_phase_13b_link(prediction_date, runtime_root, inflation_audit_glob)
    if actual_rate <= 0:
        phase_13b_link["overlap_assessment"] = "blocked_by_missing_actual_minutes"
    elif low_line_over_misses and _low_minute_risk_confirmed(low_line_minutes_summary, low_line_over_misses):
        phase_13b_link["overlap_assessment"] = "low_line_over_inflation_overlaps_low_minutes"
    elif low_line_over_misses:
        phase_13b_link["overlap_assessment"] = "low_line_over_misses_seen_but_minutes_concentration_not_confirmed"

    payload = {
        "prediction_date": prediction_date,
        "note": "audit_only_no_live_logic_change",
        "total_rows_scanned": int(len(merged)),
        "player_points_rows": int(len(pp_rows)),
        "graded_player_points_rows": int(len(graded_pp)),
        "projected_minutes_available_rate": projected_rate,
        "recent_minutes_available_rate": recent_rate,
        "average_minutes_available_rate": average_rate,
        "actual_minutes_available_rate": actual_rate,
        "minutes_error_available_rate": minutes_error_rate,
        "low_line_over_rows": int(len(low_line_over)),
        "low_line_over_misses": low_line_over_misses,
        "line_bucket_summary": _bucket_summary(pp_over, "line_bucket"),
        "low_line_minutes_summary": low_line_minutes_summary,
        "player_points_over_minutes_summary": pp_over_minutes_summary,
        "minutes_volatility_summary": volatility_summary,
        "actual_minutes_summary": actual_summary,
        "player_breakdown": _player_breakdown(pp_rows),
        "minutes_field_availability": minutes_field_availability,
        "missing_critical_fields": missing_critical_fields,
        "phase_13b_link": phase_13b_link,
        "readiness_verdict": readiness_verdict,
        "source_files_scanned": {
            "csv_sources": [
                {"source_type": label, "source_file": source_file, "rows": int(len(frame))}
                for label, source_file, frame in source_frames
            ],
            "player_points_inflation_audit_json": inflation_paths,
            "board_diagnostics_json": board_diag_json_paths,
        },
    }
    return _json_safe(payload)


def _format_txt(payload: Mapping[str, Any], prediction_date: str) -> str:
    sep = "=" * 78
    sep2 = "-" * 78
    actual_rate = payload.get("actual_minutes_available_rate", 0)
    projected_rate = payload.get("projected_minutes_available_rate", 0)
    recent_rate = payload.get("recent_minutes_available_rate", 0)
    average_rate = payload.get("average_minutes_available_rate", 0)
    verdict = _safe_text(payload.get("readiness_verdict"))
    missing = payload.get("missing_critical_fields", [])
    actual_available = bool(_to_float(actual_rate) and _to_float(actual_rate) > 0)
    low_line = payload.get("low_line_minutes_summary", {}) if isinstance(payload.get("low_line_minutes_summary"), dict) else {}
    volatility = payload.get("minutes_volatility_summary", {}) if isinstance(payload.get("minutes_volatility_summary"), dict) else {}
    low_minute_misses = sum(
        int((low_line.get(bucket, {}) or {}).get("low_line_over_miss_count") or 0)
        for bucket in ("under_15", "15_20", "20_24")
    )
    volatile_misses = sum(
        int((volatility.get(bucket, {}) or {}).get("low_line_over_miss_count") or 0)
        for bucket in ("6_10", "10_plus")
    )
    guard_answer = "No"
    if verdict == "READY_FOR_PHASE_15C_LOW_LINE_OVER_GUARD_REVIEW":
        guard_answer = "Review is justified; live guard still requires explicit approval"
    elif verdict == "LOW_MINUTE_OVER_RISK_CONFIRMED":
        guard_answer = "Risk is confirmed; continue guard review before any live change"
    next_phase = "data-source improvement for actual_minutes"
    if actual_available and verdict in {"LOW_MINUTE_OVER_RISK_CONFIRMED", "READY_FOR_PHASE_15C_LOW_LINE_OVER_GUARD_REVIEW"}:
        next_phase = "Phase 15C -- Low-Line OVER Realism Guard Review"
    elif actual_available:
        next_phase = "Phase 15B -- Usage Redistribution Shadow Audit"

    lines = [
        f"{sep}\n",
        "MINUTES AVAILABILITY AUDIT (Phase 15A -- AUDIT ONLY)\n",
        f"date: {prediction_date}    note: {payload.get('note', '')}\n",
        f"{sep}\n\n",
        "OVERVIEW\n",
        f"{sep2}\n",
        f"  total_rows_scanned             : {payload.get('total_rows_scanned', 0)}\n",
        f"  player_points_rows             : {payload.get('player_points_rows', 0)}\n",
        f"  graded_player_points_rows      : {payload.get('graded_player_points_rows', 0)}\n",
        f"  low_line_over_rows             : {payload.get('low_line_over_rows', 0)}\n",
        f"  low_line_over_misses           : {payload.get('low_line_over_misses', 0)}\n",
        f"  readiness_verdict              : {verdict}\n\n",
        "MINUTES FIELD COVERAGE\n",
        f"{sep2}\n",
        f"  projected_minutes_available_rate: {projected_rate}\n",
        f"  recent_minutes_available_rate   : {recent_rate}\n",
        f"  average_minutes_available_rate  : {average_rate}\n",
        f"  actual_minutes_available_rate   : {actual_rate}\n",
        f"  minutes_error_available_rate    : {payload.get('minutes_error_available_rate', 0)}\n\n",
        "DIAGNOSTIC QUESTIONS\n",
        f"{sep2}\n",
        f"  Q: Do we have actual_minutes?\n     {'Yes' if actual_available else 'No - actual minutes are unavailable as numeric row data.'}\n",
        "  Q: Do we have projected/recent/average minutes?\n"
        f"     projected={projected_rate} recent={recent_rate} average={average_rate}\n",
        "  Q: Are low-line OVER misses concentrated in low-minute players?\n"
        f"     low-minute low-line misses={low_minute_misses}; see low_line_minutes_summary.\n",
        "  Q: Are low-line OVER misses concentrated in volatile-minute players?\n"
        f"     volatile-minute low-line misses={volatile_misses}; see minutes_volatility_summary.\n",
        "  Q: Is a live minutes guard justified now?\n"
        f"     {guard_answer}\n",
        "  Q: What fields are missing?\n"
        f"     {', '.join(missing) if missing else 'none'}\n",
        "  Q: What is the next recommended phase?\n"
        f"     {next_phase}\n\n",
        "LOW-LINE OVER BY MINUTES BUCKET\n",
        f"{sep2}\n",
    ]
    for bucket, row in sorted(low_line.items()):
        if isinstance(row, Mapping):
            lines.append(
                f"  {bucket}: rows={row.get('rows')} graded={row.get('graded_rows')} "
                f"hit_rate={row.get('hit_rate')} miss_rate={row.get('miss_rate')} "
                f"low_line_misses={row.get('low_line_over_miss_count')}\n"
            )
    if not low_line:
        lines.append("  none\n")
    lines += [
        "\nMINUTES VOLATILITY BUCKETS\n",
        f"{sep2}\n",
    ]
    for bucket, row in sorted(volatility.items()):
        if isinstance(row, Mapping):
            lines.append(
                f"  {bucket}: rows={row.get('rows')} graded={row.get('graded_rows')} "
                f"hit_rate={row.get('hit_rate')} miss_rate={row.get('miss_rate')} "
                f"low_line_miss_rate={row.get('low_line_over_miss_rate')}\n"
            )
    if not volatility:
        lines.append("  none\n")
    phase = payload.get("phase_13b_link", {}) if isinstance(payload.get("phase_13b_link"), dict) else {}
    lines += [
        "\nPHASE 13B LINK\n",
        f"{sep2}\n",
        f"  source_json             : {phase.get('source_json')}\n",
        f"  dominant_failure_mode   : {phase.get('dominant_failure_mode')}\n",
        f"  readiness_verdict       : {phase.get('readiness_verdict')}\n",
        f"  overlap_assessment      : {phase.get('overlap_assessment')}\n",
        f"\n{sep}\n",
    ]
    return "".join(lines)


def write_minutes_availability_audit(
    prediction_date: str,
    *,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    history_csv: str | Path | pd.DataFrame | None = None,
    pick_history_csv: str | Path | pd.DataFrame | None = None,
    full_market_glob: str | Path | None = None,
    elite_board_glob: str | Path | None = None,
    player_predictions_glob: str | Path | None = None,
    grading_glob: str | Path | None = None,
    player_baselines_csv: str | Path | pd.DataFrame | None = None,
    inflation_audit_glob: str | Path | None = None,
    board_diagnostics_json_glob: str | Path | None = None,
    board_diagnostics_csv_glob: str | Path | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    payload = build_minutes_availability_audit(
        prediction_date,
        runtime_root=runtime_root,
        history_csv=history_csv,
        pick_history_csv=pick_history_csv,
        full_market_glob=full_market_glob,
        elite_board_glob=elite_board_glob,
        player_predictions_glob=player_predictions_glob,
        grading_glob=grading_glob,
        player_baselines_csv=player_baselines_csv,
        inflation_audit_glob=inflation_audit_glob,
        board_diagnostics_json_glob=board_diagnostics_json_glob,
        board_diagnostics_csv_glob=board_diagnostics_csv_glob,
    )
    json_path = minutes_audit_json_path_for_date(prediction_date, runtime_root)
    txt_path = minutes_audit_txt_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    txt_path.write_text(_format_txt(payload, prediction_date), encoding="utf-8")
    return json_path, txt_path, payload


__all__ = [
    "READINESS_VERDICTS",
    "build_minutes_availability_audit",
    "minutes_audit_json_path_for_date",
    "minutes_audit_txt_path_for_date",
    "write_minutes_availability_audit",
    "_field_availability",
    "_is_low_line_over",
    "_line_bucket",
    "_minutes_bucket",
    "_select_readiness_verdict",
    "_volatility_bucket",
]
