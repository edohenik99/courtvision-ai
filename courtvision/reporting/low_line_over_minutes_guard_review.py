"""Phase 15D low-line OVER minutes-basis guard review.

Review-only diagnostics for low-line player_points OVER rows with weak or
borderline minutes_basis. This module writes separate review artifacts only; it
does not suppress picks or mutate prediction, grading, Kelly, or history state.
"""
from __future__ import annotations

import glob
import json
import math
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


DEFAULT_RUNTIME_ROOT = "outputs/runtime"
WEAK_MINUTES_BASIS_THRESHOLD = 28.0
BORDERLINE_MINUTES_BASIS_THRESHOLD = 30.0

BUCKETS = (
    "missing_minutes_basis",
    "weak_minutes_basis",
    "borderline_minutes_basis",
    "stable_minutes_basis",
)
FLAGGED_BUCKETS = {"missing_minutes_basis", "weak_minutes_basis", "borderline_minutes_basis"}

PLAYER_ID_COLUMNS = ("player_id", "PlayerID", "playerId")
PLAYER_NAME_COLUMNS = ("player_name", "entity_name", "name", "PlayerName", "Name")
DATE_COLUMNS = ("prediction_date", "game_date", "date", "GameDate")
GAME_ID_COLUMNS = ("game_id", "GameID", "gameId")
MARKET_COLUMNS = ("market_type", "market", "prop_type")
SELECTION_COLUMNS = ("selection", "side", "pick_side")
LINE_COLUMNS = ("line", "sportsbook_line", "line_value", "market_line")
RESULT_COLUMNS = ("result_status", "result", "graded_result")
MINUTES_BASIS_COLUMNS = ("minutes_basis",)
PROJECTED_MINUTES_COLUMNS = ("projected_minutes", "minutes_projected", "expected_minutes", "projected_min")
MINUTES_RECENT_COLUMNS = ("minutes_recent", "min_recent", "recent_minutes")
MINUTES_AVG_COLUMNS = ("minutes_avg", "min_avg", "average_minutes", "avg_minutes")
MANUAL_MINUTES_LIMIT_COLUMNS = ("manual_minutes_limit", "minutes_limit")
EDGE_COLUMNS = ("edge", "side_edge", "edge_pct", "side_edge_pct", "dir_edge")
CONFIDENCE_COLUMNS = ("confidence", "base_confidence", "final_confidence")
QUALITY_COLUMNS = ("quality_score", "selection_score")
RESULTS = ("hit", "miss", "push")

CSV_COLUMNS = [
    "prediction_date",
    "player_name",
    "player_id",
    "game_id",
    "market_type",
    "selection",
    "line",
    "minutes_basis",
    "minutes_basis_source",
    "minutes_recent",
    "minutes_avg",
    "manual_minutes_limit",
    "edge",
    "confidence",
    "quality_score",
    "result_status",
    "minutes_guard_review_bucket",
    "minutes_guard_review_required",
    "minutes_guard_reason",
    "minutes_guard_recommended_action",
    "minutes_guard_note",
    "source_type",
    "source_file",
]


def low_line_over_minutes_guard_review_json_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"low_line_over_minutes_guard_review_{date}.json"


def low_line_over_minutes_guard_review_txt_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "operator" / f"low_line_over_minutes_guard_review_{date}.txt"


def low_line_over_minutes_guard_review_csv_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "operator" / f"low_line_over_minutes_guard_review_{date}.csv"


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
        pieces = text.split(":")
        try:
            return float(pieces[0]) + float(pieces[1]) / 60.0
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


def _resolve_history_path(runtime_root: Path, filename: str, explicit: Any) -> Any:
    if explicit is not None:
        return explicit
    if runtime_root.as_posix().replace("\\", "/").endswith("outputs/runtime"):
        return Path("data/history") / filename
    return runtime_root.parent / "history" / filename


def _source_frame(frame: pd.DataFrame, *, source_type: str, source_file: str) -> pd.DataFrame:
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
    out["explicit_minutes_basis"] = _coalesce_numeric(df, MINUTES_BASIS_COLUMNS)
    out["projected_minutes"] = _coalesce_numeric(df, PROJECTED_MINUTES_COLUMNS)
    out["minutes_recent"] = _coalesce_numeric(df, MINUTES_RECENT_COLUMNS)
    out["minutes_avg"] = _coalesce_numeric(df, MINUTES_AVG_COLUMNS)
    out["manual_minutes_limit"] = _coalesce_numeric(df, MANUAL_MINUTES_LIMIT_COLUMNS)
    out["edge"] = _coalesce_numeric(df, EDGE_COLUMNS)
    out["confidence"] = _coalesce_numeric(df, CONFIDENCE_COLUMNS)
    out["quality_score"] = _coalesce_numeric(df, QUALITY_COLUMNS)
    return out.reset_index(drop=True)


def _first_present(values: pd.Series) -> Any:
    for value in values:
        if _to_float(value) is not None or _safe_text(value):
            return value
    return None


def _coalesce_rows(frames: list[pd.DataFrame]) -> pd.DataFrame:
    usable = [frame for frame in frames if not frame.empty]
    if not usable:
        return pd.DataFrame()
    combined = pd.concat(usable, ignore_index=True)
    combined = combined[combined["market_type"].eq("player_points")]
    combined = combined[combined["selection"].eq("over")]
    combined = combined[combined["line"].map(lambda value: (_to_float(value) is not None) and (_to_float(value) < 15.0))].copy()
    if combined.empty:
        return combined.reset_index(drop=True)
    combined["_review_key"] = combined.apply(_row_key, axis=1)

    rows: list[dict[str, Any]] = []
    for key, group in combined.groupby("_review_key", sort=False, dropna=False):
        merged: dict[str, Any] = {"_review_key": key}
        for column in combined.columns:
            if column == "_review_key":
                continue
            if column in {"source_type", "source_file"}:
                values = sorted({_safe_text(value) for value in group[column] if _safe_text(value)})
                merged[column] = ";".join(values)
            else:
                merged[column] = _first_present(group[column])
        rows.append(merged)
    return pd.DataFrame(rows).reset_index(drop=True)


def _baseline_lookup(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    if frame.empty:
        return lookup
    for _, row in frame.iterrows():
        keys: list[str] = []
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


def _apply_baseline_minutes(rows: pd.DataFrame, baseline: pd.DataFrame) -> pd.DataFrame:
    if rows.empty or baseline.empty:
        return rows.copy()
    working = rows.copy(deep=True)
    lookup = _baseline_lookup(baseline)
    for idx, row in working.iterrows():
        payload = lookup.get(_player_key(row), {})
        for field in ("minutes_recent", "minutes_avg", "projected_minutes"):
            if _to_float(working.at[idx, field]) is None and _to_float(payload.get(field)) is not None:
                working.at[idx, field] = payload[field]
    return working


def _minutes_basis(row: Mapping[str, Any]) -> tuple[float | None, str]:
    for field in ("explicit_minutes_basis", "projected_minutes", "manual_minutes_limit", "minutes_recent", "minutes_avg"):
        value = _to_float(row.get(field))
        if value is not None:
            source = "minutes_basis" if field == "explicit_minutes_basis" else field
            return value, source
    return None, ""


def _bucket_for_minutes_basis(value: Any) -> str:
    basis = _to_float(value)
    if basis is None:
        return "missing_minutes_basis"
    if basis < WEAK_MINUTES_BASIS_THRESHOLD:
        return "weak_minutes_basis"
    if basis < BORDERLINE_MINUTES_BASIS_THRESHOLD:
        return "borderline_minutes_basis"
    return "stable_minutes_basis"


def _review_reason(bucket: str) -> str:
    return {
        "missing_minutes_basis": "missing_minutes_basis_for_low_line_over_review",
        "weak_minutes_basis": "low_line_over_weak_minutes_basis",
        "borderline_minutes_basis": "low_line_over_borderline_minutes_basis",
        "stable_minutes_basis": "",
    }.get(bucket, "")


def _avg(frame: pd.DataFrame, column: str) -> float | None:
    if frame.empty or column not in frame.columns:
        return None
    values = frame[column].map(_to_float).dropna()
    if values.empty:
        return None
    return round(float(values.mean()), 4)


def _bucket_summary(review_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for bucket in BUCKETS:
        group = review_df[review_df["minutes_guard_review_bucket"].eq(bucket)] if not review_df.empty else pd.DataFrame()
        hits = int(group["result_status"].eq("hit").sum()) if not group.empty else 0
        misses = int(group["result_status"].eq("miss").sum()) if not group.empty else 0
        pushes = int(group["result_status"].eq("push").sum()) if not group.empty else 0
        graded = hits + misses + pushes
        hit_miss = hits + misses
        out[bucket] = {
            "rows": int(len(group)),
            "graded_rows": graded,
            "hits": hits,
            "misses": misses,
            "pushes": pushes,
            "hit_rate": round(hits / hit_miss, 4) if hit_miss else None,
            "miss_rate": round(misses / hit_miss, 4) if hit_miss else None,
            "avg_edge": _avg(group, "edge"),
            "avg_confidence": _avg(group, "confidence"),
            "avg_quality_score": _avg(group, "quality_score"),
            "avg_minutes_basis": _avg(group, "minutes_basis"),
            "avg_minutes_recent": _avg(group, "minutes_recent"),
            "avg_minutes_avg": _avg(group, "minutes_avg"),
        }
    return out


def _top_flagged_rows(review_df: pd.DataFrame, limit: int = 20) -> list[dict[str, Any]]:
    if review_df.empty:
        return []
    priority = {"weak_minutes_basis": 0, "borderline_minutes_basis": 1, "missing_minutes_basis": 2}
    flagged = review_df[review_df["minutes_guard_review_required"].map(bool)].copy()
    if flagged.empty:
        return []
    flagged["_priority"] = flagged["minutes_guard_review_bucket"].map(lambda value: priority.get(_safe_text(value), 9))
    flagged["_edge_sort"] = flagged["edge"].map(lambda value: _to_float(value) if _to_float(value) is not None else -9999)
    flagged = flagged.sort_values(["_priority", "_edge_sort", "confidence"], ascending=[True, False, False], kind="mergesort")
    rows: list[dict[str, Any]] = []
    for _, row in flagged.head(limit).iterrows():
        rows.append(
            {
                "prediction_date": row.get("prediction_date"),
                "player_name": row.get("player_name"),
                "market_type": row.get("market_type"),
                "selection": row.get("selection"),
                "line": _to_float(row.get("line")),
                "minutes_basis": _to_float(row.get("minutes_basis")),
                "minutes_basis_source": row.get("minutes_basis_source"),
                "review_bucket": row.get("minutes_guard_review_bucket"),
                "edge": _to_float(row.get("edge")),
                "confidence": _to_float(row.get("confidence")),
                "quality_score": _to_float(row.get("quality_score")),
                "result_status": row.get("result_status"),
            }
        )
    return rows


def select_readiness_verdict(
    *,
    total_low_line_over_rows: int,
    weak_count: int,
    borderline_count: int,
    missing_minutes_basis_count: int,
) -> str:
    if total_low_line_over_rows <= 0:
        return "NO_LOW_LINE_OVER_ROWS"
    if missing_minutes_basis_count >= total_low_line_over_rows:
        return "MINUTES_BASIS_UNAVAILABLE"
    if weak_count > 0:
        return "REVIEW_READY_WEAK_MINUTES_PRESENT"
    if borderline_count > 0:
        return "REVIEW_READY_BORDERLINE_MINUTES_PRESENT"
    return "REVIEW_READY_STABLE_ONLY"


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


def build_low_line_over_minutes_guard_review(
    prediction_date: str,
    *,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    pick_history: str | Path | pd.DataFrame | None = None,
    market_shadow_history: str | Path | pd.DataFrame | None = None,
    player_baselines: str | Path | pd.DataFrame | None = None,
    full_market_glob: str | Path | None = None,
) -> dict[str, Any]:
    runtime_root = Path(runtime_root)
    pick_history = _resolve_history_path(runtime_root, "pick_history.csv", pick_history)
    market_shadow_history = _resolve_history_path(runtime_root, "market_shadow_history.csv", market_shadow_history)
    player_baselines = player_baselines if player_baselines is not None else runtime_root.parent / "model" / "player_baselines.csv"
    full_market_glob = full_market_glob or runtime_root / "operator" / "full_market_board_*.csv"

    source_frames: list[pd.DataFrame] = []
    source_files: list[dict[str, Any]] = []
    for label, source in [
        ("pick_history", pick_history),
        ("market_shadow_history", market_shadow_history),
    ]:
        frame = _read_csv(source)
        if not frame.empty:
            source_frames.append(_source_frame(frame, source_type=label, source_file=str(source)))
            source_files.append({"source_type": label, "source_file": str(source), "rows": int(len(frame))})
    for path_text in sorted(glob.glob(str(full_market_glob))):
        frame = _read_csv(path_text)
        if not frame.empty:
            source_frames.append(_source_frame(frame, source_type="full_market_board", source_file=path_text))
            source_files.append({"source_type": "full_market_board", "source_file": path_text, "rows": int(len(frame))})

    baseline_raw = _read_csv(player_baselines)
    baseline = (
        _source_frame(baseline_raw, source_type="player_baselines", source_file=str(player_baselines))
        if not baseline_raw.empty
        else pd.DataFrame()
    )
    if not baseline_raw.empty:
        source_files.append({"source_type": "player_baselines", "source_file": str(player_baselines), "rows": int(len(baseline_raw))})

    review_df = _apply_baseline_minutes(_coalesce_rows(source_frames), baseline)
    if not review_df.empty:
        bases: list[float | None] = []
        sources: list[str] = []
        for _, row in review_df.iterrows():
            basis, source = _minutes_basis(row)
            bases.append(basis)
            sources.append(source)
        review_df["minutes_basis"] = bases
        review_df["minutes_basis_source"] = sources
        review_df["minutes_guard_review_bucket"] = review_df["minutes_basis"].map(_bucket_for_minutes_basis)
        review_df["minutes_guard_review_required"] = review_df["minutes_guard_review_bucket"].isin(FLAGGED_BUCKETS)
        review_df["minutes_guard_reason"] = review_df["minutes_guard_review_bucket"].map(_review_reason)
        review_df["minutes_guard_recommended_action"] = review_df["minutes_guard_review_required"].map(
            lambda flagged: "REVIEW_BEFORE_BET" if flagged else "OK_TO_CONSIDER"
        )
        review_df["minutes_guard_note"] = "review_only_no_live_suppression"
    else:
        review_df = pd.DataFrame(columns=CSV_COLUMNS)

    bucket_counts = {
        bucket: int(review_df["minutes_guard_review_bucket"].eq(bucket).sum()) if not review_df.empty else 0
        for bucket in BUCKETS
    }
    weak_count = bucket_counts["weak_minutes_basis"]
    borderline_count = bucket_counts["borderline_minutes_basis"]
    stable_count = bucket_counts["stable_minutes_basis"]
    missing_count = bucket_counts["missing_minutes_basis"]
    readiness = select_readiness_verdict(
        total_low_line_over_rows=int(len(review_df)),
        weak_count=weak_count,
        borderline_count=borderline_count,
        missing_minutes_basis_count=missing_count,
    )

    csv_df = review_df.reindex(columns=CSV_COLUMNS).copy()
    payload = {
        "prediction_date": prediction_date,
        "note": "review_only_no_prediction_grading_kelly_or_history_change",
        "thresholds": {
            "weak_minutes_basis_lt": WEAK_MINUTES_BASIS_THRESHOLD,
            "borderline_minutes_basis_gte": WEAK_MINUTES_BASIS_THRESHOLD,
            "borderline_minutes_basis_lt": BORDERLINE_MINUTES_BASIS_THRESHOLD,
            "stable_minutes_basis_gte": BORDERLINE_MINUTES_BASIS_THRESHOLD,
        },
        "total_low_line_over_rows": int(len(review_df)),
        "weak_minutes_basis_count": weak_count,
        "borderline_minutes_basis_count": borderline_count,
        "stable_minutes_basis_count": stable_count,
        "missing_minutes_basis_count": missing_count,
        "review_required_count": int(review_df["minutes_guard_review_required"].map(bool).sum()) if not review_df.empty else 0,
        "bucket_summary": _bucket_summary(review_df),
        "top_flagged_rows": _top_flagged_rows(review_df),
        "readiness_verdict": readiness,
        "history_mutated": False,
        "live_picks_suppressed": False,
        "review_df": csv_df,
        "source_files_scanned": source_files,
    }
    serializable = _json_safe({key: value for key, value in payload.items() if key != "review_df"})
    serializable["review_df"] = csv_df
    return serializable


def _format_pct(value: Any) -> str:
    number = _to_float(value)
    return "n/a" if number is None else f"{number * 100:.1f}%"


def _format_num(value: Any) -> str:
    number = _to_float(value)
    return "n/a" if number is None else f"{number:.3f}"


def _format_txt(payload: Mapping[str, Any], prediction_date: str) -> str:
    sep = "=" * 78
    sep2 = "-" * 78
    summary = payload.get("bucket_summary", {}) if isinstance(payload.get("bucket_summary"), dict) else {}
    lines = [
        f"{sep}\n",
        "LOW-LINE OVER MINUTES GUARD REVIEW (Phase 15D -- REVIEW ONLY)\n",
        f"date: {prediction_date}    note: {payload.get('note', '')}\n",
        f"{sep}\n\n",
        "OVERVIEW\n",
        f"{sep2}\n",
        f"  total_low_line_over_rows      : {payload.get('total_low_line_over_rows', 0)}\n",
        f"  weak_minutes_basis_count      : {payload.get('weak_minutes_basis_count', 0)}\n",
        f"  borderline_minutes_basis_count: {payload.get('borderline_minutes_basis_count', 0)}\n",
        f"  stable_minutes_basis_count    : {payload.get('stable_minutes_basis_count', 0)}\n",
        f"  missing_minutes_basis_count   : {payload.get('missing_minutes_basis_count', 0)}\n",
        f"  review_required_count         : {payload.get('review_required_count', 0)}\n",
        f"  readiness_verdict             : {payload.get('readiness_verdict')}\n\n",
        "BUCKET PERFORMANCE\n",
        f"{sep2}\n",
    ]
    for bucket in BUCKETS:
        row = summary.get(bucket, {}) if isinstance(summary.get(bucket, {}), Mapping) else {}
        lines.append(
            f"  {bucket}: rows={row.get('rows', 0)} graded={row.get('graded_rows', 0)} "
            f"hit_rate={_format_pct(row.get('hit_rate'))} miss_rate={_format_pct(row.get('miss_rate'))} "
            f"avg_basis={_format_num(row.get('avg_minutes_basis'))} "
            f"avg_conf={_format_num(row.get('avg_confidence'))} "
            f"avg_quality={_format_num(row.get('avg_quality_score'))}\n"
        )
    lines.extend(
        [
            "\nTOP FLAGGED ROWS\n",
            f"{sep2}\n",
        ]
    )
    top_rows = payload.get("top_flagged_rows", []) if isinstance(payload.get("top_flagged_rows"), list) else []
    if not top_rows:
        lines.append("  none\n")
    for row in top_rows[:10]:
        if isinstance(row, Mapping):
            lines.append(
                f"  {row.get('player_name') or 'Unknown'}: line={_format_num(row.get('line'))} "
                f"basis={_format_num(row.get('minutes_basis'))} bucket={row.get('review_bucket')} "
                f"result={row.get('result_status') or 'pending'}\n"
            )
    lines.extend(
        [
            "\nDIAGNOSTIC QUESTIONS\n",
            f"{sep2}\n",
            "  Q: Are any low-line player_points OVER rows weak on minutes_basis?\n",
            f"     weak_minutes_basis_count={payload.get('weak_minutes_basis_count', 0)}\n",
            "  Q: Were live picks suppressed or removed?\n",
            "     No. REVIEW ONLY; no live logic changed.\n",
            f"\n{sep}\n",
        ]
    )
    return "".join(lines)


def write_low_line_over_minutes_guard_review(
    prediction_date: str,
    *,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    pick_history: str | Path | pd.DataFrame | None = None,
    market_shadow_history: str | Path | pd.DataFrame | None = None,
    player_baselines: str | Path | pd.DataFrame | None = None,
    full_market_glob: str | Path | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    payload = build_low_line_over_minutes_guard_review(
        prediction_date,
        runtime_root=runtime_root,
        pick_history=pick_history,
        market_shadow_history=market_shadow_history,
        player_baselines=player_baselines,
        full_market_glob=full_market_glob,
    )
    json_path = low_line_over_minutes_guard_review_json_path_for_date(prediction_date, runtime_root)
    txt_path = low_line_over_minutes_guard_review_txt_path_for_date(prediction_date, runtime_root)
    csv_path = low_line_over_minutes_guard_review_csv_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    review_df = payload.get("review_df")
    csv_df = review_df if isinstance(review_df, pd.DataFrame) else pd.DataFrame(columns=CSV_COLUMNS)
    csv_df.reindex(columns=CSV_COLUMNS).to_csv(csv_path, index=False)

    serializable = {key: value for key, value in payload.items() if key != "review_df"}
    json_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    txt_path.write_text(_format_txt(serializable, prediction_date), encoding="utf-8")
    return json_path, txt_path, csv_path, serializable


__all__ = [
    "BORDERLINE_MINUTES_BASIS_THRESHOLD",
    "WEAK_MINUTES_BASIS_THRESHOLD",
    "build_low_line_over_minutes_guard_review",
    "low_line_over_minutes_guard_review_csv_path_for_date",
    "low_line_over_minutes_guard_review_json_path_for_date",
    "low_line_over_minutes_guard_review_txt_path_for_date",
    "select_readiness_verdict",
    "write_low_line_over_minutes_guard_review",
]
