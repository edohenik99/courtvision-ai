"""Phase 15E low-line OVER minutes guard outcome validation.

Review-only diagnostics that measure whether Phase 15D minutes-basis buckets
have historically underperformed after grading. This module writes separate
artifacts only; it does not change prediction, grading, Kelly, suppression, or
history state.
"""
from __future__ import annotations

import glob
import json
import math
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from courtvision.reporting.low_line_over_minutes_guard_review import (
    BORDERLINE_MINUTES_BASIS_THRESHOLD,
    BUCKETS,
    WEAK_MINUTES_BASIS_THRESHOLD,
    low_line_over_minutes_guard_review_csv_path_for_date,
)


DEFAULT_RUNTIME_ROOT = "outputs/runtime"
TERMINAL_STATUSES = frozenset({"hit", "miss", "push", "void"})
HIT_RATE_STATUSES = frozenset({"hit", "miss"})
MIN_BUCKET_SAMPLE = 30
MATERIAL_HIT_RATE_GAP = 0.05
MISSING_RISK_HIT_RATE = 0.45

PLAYER_ID_COLUMNS = ("player_id", "PlayerID", "playerId")
PLAYER_NAME_COLUMNS = ("player_name", "entity_name", "name", "PlayerName", "Name")
DATE_COLUMNS = ("prediction_date", "game_date", "date", "GameDate")
MARKET_COLUMNS = ("market_type", "market", "prop_type")
SELECTION_COLUMNS = ("selection", "side", "pick_side")
LINE_COLUMNS = ("line", "sportsbook_line", "line_value", "market_line")
RESULT_COLUMNS = ("result_status", "result", "graded_result")
MINUTES_BASIS_COLUMNS = ("minutes_basis",)
EDGE_COLUMNS = ("edge", "side_edge", "edge_pct", "side_edge_pct", "dir_edge")
CONFIDENCE_COLUMNS = ("confidence", "base_confidence", "final_confidence")
QUALITY_COLUMNS = ("quality_score", "quality", "selection_score")
ROI_COLUMNS = ("shadow_roi", "roi", "unit_roi")
PROFIT_COLUMNS = ("profit_loss", "profit", "pnl", "paper_profit")
STAKE_COLUMNS = ("stake_amount", "stake", "simulated_stake", "paper_stake")
ODDS_COLUMNS = ("odds", "american_odds", "offered_odds")
BUCKET_COLUMNS = ("minutes_guard_review_bucket", "minutes_bucket")
SOURCE_COLUMNS = ("source_type", "source_file")

CSV_COLUMNS = [
    "prediction_date",
    "player_name",
    "player_id",
    "market_type",
    "selection",
    "line",
    "minutes_guard_review_bucket",
    "minutes_basis",
    "edge",
    "confidence",
    "quality_score",
    "result_status",
    "terminal_result",
    "hit_rate_eligible",
    "row_roi",
    "odds",
    "profit_loss",
    "stake_amount",
    "context_pick_alignment",
    "context_caution_level",
    "matched_history",
    "source_type",
    "source_file",
]


def low_line_over_minutes_guard_outcome_json_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"low_line_over_minutes_guard_outcome_{date}.json"


def low_line_over_minutes_guard_outcome_txt_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "operator" / f"low_line_over_minutes_guard_outcome_{date}.txt"


def low_line_over_minutes_guard_outcome_csv_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "operator" / f"low_line_over_minutes_guard_outcome_{date}.csv"


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
    if text in {"push", "void"}:
        return text
    if text in {"pending", "open", "open_game", "ungraded"}:
        return "pending"
    return text


def _line_key(value: Any) -> str:
    number = _to_float(value)
    return "" if number is None else f"{number:.4f}"


def _bucket_for_minutes_basis(value: Any) -> str:
    basis = _to_float(value)
    if basis is None:
        return "missing_minutes_basis"
    if basis < WEAK_MINUTES_BASIS_THRESHOLD:
        return "weak_minutes_basis"
    if basis < BORDERLINE_MINUTES_BASIS_THRESHOLD:
        return "borderline_minutes_basis"
    return "stable_minutes_basis"


def _normalize_bucket(value: Any, minutes_basis: Any) -> str:
    bucket = _safe_text(value)
    if bucket in BUCKETS:
        return bucket
    return _bucket_for_minutes_basis(minutes_basis)


def _unit_return_from_odds(result_status: str, odds: Any) -> float | None:
    if result_status == "push":
        return 0.0
    if result_status == "void":
        return None
    price = _to_float(odds)
    if price is None or result_status not in {"hit", "miss"}:
        return None
    if result_status == "miss":
        return -1.0
    if price > 0:
        return round(price / 100.0, 6)
    if price < 0:
        return round(100.0 / abs(price), 6)
    return None


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
    out["market_type"] = _coalesce_text(df, MARKET_COLUMNS).map(_normalize_market)
    out["selection"] = _coalesce_text(df, SELECTION_COLUMNS).map(_normalize_selection)
    out["line"] = _coalesce_numeric(df, LINE_COLUMNS)
    out["line_key"] = out["line"].map(_line_key)
    out["minutes_basis"] = _coalesce_numeric(df, MINUTES_BASIS_COLUMNS)
    out["minutes_guard_review_bucket"] = [
        _normalize_bucket(bucket, basis)
        for bucket, basis in zip(_coalesce_text(df, BUCKET_COLUMNS), out["minutes_basis"])
    ]
    out["edge"] = _coalesce_numeric(df, EDGE_COLUMNS)
    out["confidence"] = _coalesce_numeric(df, CONFIDENCE_COLUMNS)
    out["quality_score"] = _coalesce_numeric(df, QUALITY_COLUMNS)
    out["result_status"] = _coalesce_text(df, RESULT_COLUMNS).map(_normalize_result)
    out["shadow_roi"] = _coalesce_numeric(df, ROI_COLUMNS)
    out["profit_loss"] = _coalesce_numeric(df, PROFIT_COLUMNS)
    out["stake_amount"] = _coalesce_numeric(df, STAKE_COLUMNS)
    out["odds"] = _coalesce_numeric(df, ODDS_COLUMNS)
    out["context_pick_alignment"] = _coalesce_text(df, ("context_pick_alignment",))
    out["context_caution_level"] = _coalesce_text(df, ("context_caution_level",))
    return out.reset_index(drop=True)


def _low_line_over_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    working = frame.copy(deep=True)
    working = working[working["market_type"].eq("player_points")]
    working = working[working["selection"].eq("over")]
    working = working[
        working["line"].map(lambda value: (_to_float(value) is not None) and (_to_float(value) < 15.0))
    ].copy()
    return working.reset_index(drop=True)


def _identity_keys(row: Mapping[str, Any], *, use_id: bool) -> tuple[str, str, str, str, str] | None:
    prediction_date = _safe_text(row.get("prediction_date"))
    market_type = _safe_text(row.get("market_type"))
    selection = _safe_text(row.get("selection"))
    line_key = _line_key(row.get("line"))
    player = _safe_text(row.get("player_id_key" if use_id else "player_name_key"))
    if not prediction_date or not market_type or not selection or not line_key or not player:
        return None
    return prediction_date, player, market_type, selection, line_key


def _unique_history_lookup(history: pd.DataFrame, *, use_id: bool) -> tuple[dict[tuple[str, str, str, str, str], pd.Series], int]:
    lookup: dict[tuple[str, str, str, str, str], pd.Series] = {}
    ambiguous: set[tuple[str, str, str, str, str]] = set()
    if history.empty:
        return lookup, 0
    for _, row in history.iterrows():
        key = _identity_keys(row, use_id=use_id)
        if key is None:
            continue
        if key in lookup:
            ambiguous.add(key)
        else:
            lookup[key] = row
    for key in ambiguous:
        lookup.pop(key, None)
    return lookup, len(ambiguous)


def _first_present(current: Any, candidate: Any) -> Any:
    if _to_float(current) is not None or _safe_text(current):
        return current
    return candidate


def _overlay_history(review_df: pd.DataFrame, history_df: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    if review_df.empty:
        return review_df.copy(), 0, 0
    if history_df.empty:
        out = review_df.copy(deep=True)
        out["matched_history"] = False
        return out, 0, 0

    id_lookup, ambiguous_id = _unique_history_lookup(history_df, use_id=True)
    name_lookup, ambiguous_name = _unique_history_lookup(history_df, use_id=False)
    working = review_df.copy(deep=True)
    working["matched_history"] = False
    matched = 0
    overlay_columns = [
        "result_status",
        "shadow_roi",
        "profit_loss",
        "stake_amount",
        "odds",
        "edge",
        "confidence",
        "quality_score",
        "context_pick_alignment",
        "context_caution_level",
    ]

    for idx, row in working.iterrows():
        match = None
        id_key = _identity_keys(row, use_id=True)
        if id_key is not None:
            match = id_lookup.get(id_key)
        if match is None:
            name_key = _identity_keys(row, use_id=False)
            if name_key is not None:
                match = name_lookup.get(name_key)
        if match is None:
            continue
        matched += 1
        working.at[idx, "matched_history"] = True
        for column in overlay_columns:
            if column == "result_status":
                if _safe_text(match.get(column)):
                    working.at[idx, column] = match.get(column)
            else:
                working.at[idx, column] = _first_present(working.at[idx, column], match.get(column))
    return working, matched, ambiguous_id + ambiguous_name


def _review_inputs(
    prediction_date: str,
    runtime_root: Path,
    guard_review_csv: str | Path | pd.DataFrame | None,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    source_files: list[dict[str, Any]] = []
    if isinstance(guard_review_csv, pd.DataFrame):
        frame = _read_csv(guard_review_csv)
        if not frame.empty:
            source_files.append({"source_type": "guard_review", "source_file": "<dataframe>", "rows": int(len(frame))})
            return _source_frame(frame, source_type="guard_review", source_file="<dataframe>"), source_files
        return pd.DataFrame(), source_files

    paths: list[Path] = []
    if guard_review_csv is not None:
        text = str(guard_review_csv)
        if any(char in text for char in "*?[]"):
            paths = [Path(path) for path in sorted(glob.glob(text))]
        else:
            paths = [Path(guard_review_csv)]
    else:
        paths = [low_line_over_minutes_guard_review_csv_path_for_date(prediction_date, runtime_root)]

    frames: list[pd.DataFrame] = []
    for path in paths:
        frame = _read_csv(path)
        if frame.empty:
            continue
        frames.append(_source_frame(frame, source_type="guard_review", source_file=str(path)))
        source_files.append({"source_type": "guard_review", "source_file": str(path), "rows": int(len(frame))})
    if not frames:
        return pd.DataFrame(), source_files
    return pd.concat(frames, ignore_index=True), source_files


def _avg(frame: pd.DataFrame, column: str) -> float | None:
    if frame.empty or column not in frame.columns:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return None
    return round(float(values.mean()), 4)


def _row_roi(row: Mapping[str, Any]) -> float | None:
    explicit = _to_float(row.get("shadow_roi"))
    if explicit is not None:
        return explicit
    profit = _to_float(row.get("profit_loss"))
    stake = _to_float(row.get("stake_amount"))
    if profit is not None and stake is not None and stake > 0:
        return round(profit / stake, 6)
    return _unit_return_from_odds(_safe_text(row.get("result_status")), row.get("odds"))


def _prepare_outcome_rows(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame(columns=CSV_COLUMNS)
    working = rows.copy(deep=True)
    working["result_status"] = working["result_status"].map(_normalize_result)
    working["minutes_guard_review_bucket"] = [
        _normalize_bucket(bucket, basis)
        for bucket, basis in zip(working["minutes_guard_review_bucket"], working["minutes_basis"])
    ]
    working["terminal_result"] = working["result_status"].isin(TERMINAL_STATUSES)
    working["hit_rate_eligible"] = working["result_status"].isin(HIT_RATE_STATUSES)
    working["row_roi"] = working.apply(_row_roi, axis=1)
    for column in CSV_COLUMNS:
        if column not in working.columns:
            working[column] = pd.NA
    return working.reindex(columns=CSV_COLUMNS).reset_index(drop=True)


def _roi(frame: pd.DataFrame) -> float | None:
    if frame.empty or "row_roi" not in frame.columns:
        return None
    eligible = frame[frame["result_status"].isin({"hit", "miss", "push"})]
    values = pd.to_numeric(eligible["row_roi"], errors="coerce").dropna()
    if values.empty:
        return None
    return round(float(values.mean()), 4)


def _bucket_metrics(frame: pd.DataFrame, bucket: str) -> dict[str, Any]:
    group = frame[frame["minutes_guard_review_bucket"].eq(bucket)] if not frame.empty else pd.DataFrame()
    status = group["result_status"].map(_normalize_result) if not group.empty else pd.Series(dtype="object")
    hits = int(status.eq("hit").sum())
    misses = int(status.eq("miss").sum())
    pushes = int(status.eq("push").sum())
    voids = int(status.eq("void").sum())
    graded = hits + misses + pushes + voids
    pending = int(len(group) - graded)
    denom = hits + misses
    if len(group) <= 0:
        sample_status = "no_rows"
    elif graded < MIN_BUCKET_SAMPLE:
        sample_status = "insufficient_sample"
    else:
        sample_status = "ready_sample"
    return {
        "total_rows": int(len(group)),
        "graded_rows": graded,
        "hits": hits,
        "misses": misses,
        "pushes": pushes,
        "voids": voids,
        "pending_rows_excluded": pending,
        "hit_rate": round(hits / denom, 4) if denom else None,
        "roi": _roi(group),
        "avg_edge": _avg(group, "edge"),
        "avg_confidence": _avg(group, "confidence"),
        "avg_quality": _avg(group, "quality_score"),
        "avg_minutes_basis": _avg(group, "minutes_basis"),
        "sample_status": sample_status,
    }


def _bucket_performance(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    return {bucket: _bucket_metrics(frame, bucket) for bucket in BUCKETS}


def _confidence_bucket(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return "missing_confidence"
    if number < 0.65:
        return "confidence_lt_0_65"
    if number < 0.75:
        return "confidence_0_65_to_0_75"
    return "confidence_gte_0_75"


def _edge_bucket(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return "missing_edge"
    if number < 0:
        return "edge_lt_0"
    if number < 1:
        return "edge_0_to_1"
    if number < 3:
        return "edge_1_to_3"
    return "edge_gte_3"


def _group_breakdown(frame: pd.DataFrame, column: str) -> dict[str, dict[str, Any]]:
    if frame.empty or column not in frame.columns:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, group in frame.groupby(column, sort=True, dropna=False):
        label = _safe_text(key) or "missing"
        out[label] = {
            "total_rows": int(len(group)),
            "bucket_performance": _bucket_performance(group),
        }
    return out


def _optional_breakdowns(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "by_confidence_bucket": {},
            "by_edge_bucket": {},
            "by_context_pick_alignment": {},
            "by_context_caution_level": {},
        }
    working = frame.copy(deep=True)
    working["_confidence_bucket"] = working["confidence"].map(_confidence_bucket)
    working["_edge_bucket"] = working["edge"].map(_edge_bucket)
    return {
        "by_confidence_bucket": _group_breakdown(working, "_confidence_bucket"),
        "by_edge_bucket": _group_breakdown(working, "_edge_bucket"),
        "by_context_pick_alignment": _group_breakdown(working, "context_pick_alignment"),
        "by_context_caution_level": _group_breakdown(working, "context_caution_level"),
    }


def compare_weak_vs_stable(bucket_performance: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    weak = bucket_performance.get("weak_minutes_basis", {})
    stable = bucket_performance.get("stable_minutes_basis", {})
    weak_hr = weak.get("hit_rate")
    stable_hr = stable.get("hit_rate")
    weak_roi = weak.get("roi")
    stable_roi = stable.get("roi")
    hit_delta = (
        round(float(weak_hr) - float(stable_hr), 4)
        if weak_hr is not None and stable_hr is not None
        else None
    )
    roi_delta = (
        round(float(weak_roi) - float(stable_roi), 4)
        if weak_roi is not None and stable_roi is not None
        else None
    )
    weak_under = False
    if hit_delta is not None and hit_delta <= -MATERIAL_HIT_RATE_GAP:
        weak_under = True
    if roi_delta is not None and roi_delta <= -MATERIAL_HIT_RATE_GAP:
        weak_under = True
    return {
        "weak_hit_rate_minus_stable_hit_rate": hit_delta,
        "weak_roi_minus_stable_roi": roi_delta,
        "weak_underperformance_signal": weak_under,
    }


def select_readiness_verdict(
    *,
    weak_graded_rows: int,
    stable_graded_rows: int,
    weak_underperformance_signal: bool,
    missing_graded_rows: int,
    missing_hit_rate: float | None,
    missing_roi: float | None,
) -> str:
    if weak_graded_rows < MIN_BUCKET_SAMPLE or stable_graded_rows < MIN_BUCKET_SAMPLE:
        return "INSUFFICIENT_SAMPLE"
    if weak_underperformance_signal:
        return "REVIEW_READY_WEAK_BUCKET_UNDERPERFORMS"
    if (
        missing_graded_rows >= MIN_BUCKET_SAMPLE
        and (
            (missing_hit_rate is not None and missing_hit_rate < MISSING_RISK_HIT_RATE)
            or (missing_roi is not None and missing_roi < 0)
        )
    ):
        return "REVIEW_READY_MISSING_MINUTES_RISK"
    return "REVIEW_READY_NO_CLEAR_BUCKET_EDGE"


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


def build_low_line_over_minutes_guard_outcome(
    prediction_date: str,
    *,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    market_shadow_history: str | Path | pd.DataFrame | None = None,
    paper_kelly_history: str | Path | pd.DataFrame | None = None,
    guard_review_csv: str | Path | pd.DataFrame | None = None,
) -> dict[str, Any]:
    runtime_root = Path(runtime_root)
    market_shadow_history = _resolve_history_path(runtime_root, "market_shadow_history.csv", market_shadow_history)
    paper_kelly_history = _resolve_history_path(runtime_root, "paper_kelly_history.csv", paper_kelly_history)

    source_files: list[dict[str, Any]] = []
    shadow_raw = _read_csv(market_shadow_history)
    shadow = _source_frame(shadow_raw, source_type="market_shadow_history", source_file=str(market_shadow_history)) if not shadow_raw.empty else pd.DataFrame()
    if not shadow_raw.empty:
        source_files.append({"source_type": "market_shadow_history", "source_file": str(market_shadow_history), "rows": int(len(shadow_raw))})
    paper_raw = _read_csv(paper_kelly_history)
    paper = _source_frame(paper_raw, source_type="paper_kelly_history", source_file=str(paper_kelly_history)) if not paper_raw.empty else pd.DataFrame()
    if not paper_raw.empty:
        source_files.append({"source_type": "paper_kelly_history", "source_file": str(paper_kelly_history), "rows": int(len(paper_raw))})

    review_rows, review_sources = _review_inputs(prediction_date, runtime_root, guard_review_csv)
    source_files.extend(review_sources)
    history_rows = _low_line_over_rows(pd.concat([frame for frame in (shadow, paper) if not frame.empty], ignore_index=True)) if not shadow.empty or not paper.empty else pd.DataFrame()

    if not review_rows.empty:
        base_rows = _low_line_over_rows(review_rows)
        joined, matched_history_count, ambiguous_history_match_count = _overlay_history(base_rows, history_rows)
    else:
        joined = history_rows.copy(deep=True)
        if not joined.empty:
            joined["matched_history"] = True
        matched_history_count = int(len(joined))
        ambiguous_history_match_count = 0

    outcome_df = _prepare_outcome_rows(joined)
    bucket_performance = _bucket_performance(outcome_df)
    comparison = compare_weak_vs_stable(bucket_performance)
    missing = bucket_performance["missing_minutes_basis"]
    weak = bucket_performance["weak_minutes_basis"]
    stable = bucket_performance["stable_minutes_basis"]
    readiness = select_readiness_verdict(
        weak_graded_rows=int(weak.get("graded_rows", 0)),
        stable_graded_rows=int(stable.get("graded_rows", 0)),
        weak_underperformance_signal=bool(comparison.get("weak_underperformance_signal")),
        missing_graded_rows=int(missing.get("graded_rows", 0)),
        missing_hit_rate=missing.get("hit_rate"),
        missing_roi=missing.get("roi"),
    )

    payload = {
        "prediction_date": prediction_date,
        "note": "review_only_no_prediction_grading_kelly_history_or_suppression_change",
        "thresholds": {
            "weak_minutes_basis_lt": WEAK_MINUTES_BASIS_THRESHOLD,
            "borderline_minutes_basis_gte": WEAK_MINUTES_BASIS_THRESHOLD,
            "borderline_minutes_basis_lt": BORDERLINE_MINUTES_BASIS_THRESHOLD,
            "stable_minutes_basis_gte": BORDERLINE_MINUTES_BASIS_THRESHOLD,
            "minimum_bucket_sample": MIN_BUCKET_SAMPLE,
            "material_hit_rate_gap": MATERIAL_HIT_RATE_GAP,
        },
        "total_low_line_over_rows": int(len(outcome_df)),
        "bucket_performance": bucket_performance,
        "comparison": comparison,
        "optional_breakdowns": _optional_breakdowns(outcome_df),
        "readiness_verdict": readiness,
        "matched_history_count": matched_history_count,
        "ambiguous_history_match_count": ambiguous_history_match_count,
        "pending_rows_excluded_from_metrics": int((~outcome_df["terminal_result"].map(bool)).sum()) if not outcome_df.empty else 0,
        "history_mutated": False,
        "live_picks_suppressed": False,
        "outcome_df": outcome_df,
        "source_files_scanned": source_files,
    }
    serializable = _json_safe({key: value for key, value in payload.items() if key != "outcome_df"})
    serializable["outcome_df"] = outcome_df
    return serializable


def _format_pct(value: Any) -> str:
    number = _to_float(value)
    return "n/a" if number is None else f"{number * 100:.1f}%"


def _format_num(value: Any) -> str:
    number = _to_float(value)
    return "n/a" if number is None else f"{number:.4f}"


def _format_txt(payload: Mapping[str, Any], prediction_date: str) -> str:
    sep = "=" * 78
    sep2 = "-" * 78
    buckets = payload.get("bucket_performance", {}) if isinstance(payload.get("bucket_performance"), dict) else {}
    comparison = payload.get("comparison", {}) if isinstance(payload.get("comparison"), Mapping) else {}
    lines = [
        f"{sep}\n",
        "LOW-LINE OVER MINUTES GUARD OUTCOME VALIDATION (Phase 15E -- REVIEW ONLY)\n",
        f"date: {prediction_date}    note: {payload.get('note', '')}\n",
        f"{sep}\n\n",
        "OVERVIEW\n",
        f"{sep2}\n",
        f"  total_low_line_over_rows        : {payload.get('total_low_line_over_rows', 0)}\n",
        f"  matched_history_count           : {payload.get('matched_history_count', 0)}\n",
        f"  pending_rows_excluded           : {payload.get('pending_rows_excluded_from_metrics', 0)}\n",
        f"  readiness_verdict               : {payload.get('readiness_verdict')}\n\n",
        "BUCKET OUTCOMES\n",
        f"{sep2}\n",
    ]
    for bucket in BUCKETS:
        row = buckets.get(bucket, {}) if isinstance(buckets.get(bucket, {}), Mapping) else {}
        lines.append(
            f"  {bucket}: total={row.get('total_rows', 0)} graded={row.get('graded_rows', 0)} "
            f"hits={row.get('hits', 0)} misses={row.get('misses', 0)} "
            f"pushes={row.get('pushes', 0)} voids={row.get('voids', 0)} "
            f"pending={row.get('pending_rows_excluded', 0)} "
            f"hit_rate={_format_pct(row.get('hit_rate'))} roi={_format_pct(row.get('roi'))} "
            f"avg_basis={_format_num(row.get('avg_minutes_basis'))}\n"
        )
    lines.extend(
        [
            "\nWEAK VS STABLE\n",
            f"{sep2}\n",
            f"  weak_hit_rate_minus_stable_hit_rate : {_format_num(comparison.get('weak_hit_rate_minus_stable_hit_rate'))}\n",
            f"  weak_roi_minus_stable_roi           : {_format_num(comparison.get('weak_roi_minus_stable_roi'))}\n",
            f"  weak_underperformance_signal        : {comparison.get('weak_underperformance_signal')}\n",
            "\nNOTE: REVIEW ONLY; no prediction/grading/Kelly/history changes and no picks suppressed.\n",
            f"{sep}\n",
        ]
    )
    return "".join(lines)


def write_low_line_over_minutes_guard_outcome(
    prediction_date: str,
    *,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    market_shadow_history: str | Path | pd.DataFrame | None = None,
    paper_kelly_history: str | Path | pd.DataFrame | None = None,
    guard_review_csv: str | Path | pd.DataFrame | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    payload = build_low_line_over_minutes_guard_outcome(
        prediction_date,
        runtime_root=runtime_root,
        market_shadow_history=market_shadow_history,
        paper_kelly_history=paper_kelly_history,
        guard_review_csv=guard_review_csv,
    )
    json_path = low_line_over_minutes_guard_outcome_json_path_for_date(prediction_date, runtime_root)
    txt_path = low_line_over_minutes_guard_outcome_txt_path_for_date(prediction_date, runtime_root)
    csv_path = low_line_over_minutes_guard_outcome_csv_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    outcome_df = payload.get("outcome_df")
    csv_df = outcome_df if isinstance(outcome_df, pd.DataFrame) else pd.DataFrame(columns=CSV_COLUMNS)
    csv_df.reindex(columns=CSV_COLUMNS).to_csv(csv_path, index=False)

    serializable = {key: value for key, value in payload.items() if key != "outcome_df"}
    json_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    txt_path.write_text(_format_txt(serializable, prediction_date), encoding="utf-8")
    return json_path, txt_path, csv_path, serializable


__all__ = [
    "HIT_RATE_STATUSES",
    "MATERIAL_HIT_RATE_GAP",
    "MIN_BUCKET_SAMPLE",
    "TERMINAL_STATUSES",
    "build_low_line_over_minutes_guard_outcome",
    "compare_weak_vs_stable",
    "low_line_over_minutes_guard_outcome_csv_path_for_date",
    "low_line_over_minutes_guard_outcome_json_path_for_date",
    "low_line_over_minutes_guard_outcome_txt_path_for_date",
    "select_readiness_verdict",
    "write_low_line_over_minutes_guard_outcome",
]
