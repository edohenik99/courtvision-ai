"""Phase 15G low-line OVER minutes guard missed-winner attribution.

Review-only diagnostics that compare weak_minutes_basis low-line player_points
OVER hits against weak_minutes_basis misses. This module writes separate
attribution artifacts only; it does not change prediction, grading, Kelly,
suppression, or history state.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from courtvision.reporting.low_line_over_minutes_guard_outcome import (
    DEFAULT_RUNTIME_ROOT,
    TERMINAL_STATUSES,
    _bucket_for_minutes_basis,
    _normalize_result,
    _read_csv,
    _safe_text,
    _to_float,
    low_line_over_minutes_guard_outcome_csv_path_for_date,
)
from courtvision.reporting.low_line_over_minutes_guard_policy_simulation import (
    low_line_over_minutes_guard_policy_simulation_csv_path_for_date,
)


MIN_ATTRIBUTION_SAMPLE = 30
NO_GENERALIZED_SIGNAL = "no_generalized_pre_pick_signal"
NO_IDENTITY_SIGNAL = "no_identity_signal"
SIGNAL_MIN_SUPPORT = 5
IDENTITY_FIELDS = frozenset({"player_name", "team", "opponent"})
NUMERIC_SIGNAL_THRESHOLDS = {
    "line": 1.0,
    "actual_value": 2.0,
    "edge": 0.5,
    "confidence": 0.05,
    "quality_score": 5.0,
    "minutes_basis": 1.0,
    "projected_minutes": 1.0,
    "minutes_recent": 1.0,
    "minutes_avg": 1.0,
    "manual_minutes_limit": 1.0,
    "actual_minutes": 2.0,
    "model_projection": 1.0,
    "usage_rate": 1.0,
    "projected_usage": 1.0,
}
OUTCOME_ONLY_NUMERIC_FIELDS = frozenset({"actual_value", "actual_minutes"})
OUTCOME_ONLY_TEXT_FIELDS = frozenset({"result_status", "result", "graded_result"})
CATEGORICAL_SIGNAL_DELTA = 0.2
CATEGORICAL_MIN_ROWS = 2

TEXT_COLUMNS = {
    "prediction_date": ("prediction_date", "game_date", "date"),
    "player_id": ("player_id", "PlayerID", "playerId"),
    "player_name": ("player_name", "entity_name", "name", "PlayerName", "Name"),
    "team": ("team_abbr", "team", "Team", "team_name"),
    "opponent": ("opponent", "opponent_abbr", "opp", "Opponent"),
    "market_type": ("market_type", "market", "prop_type"),
    "selection": ("selection", "side", "pick_side"),
    "result_status": ("result_status", "result", "graded_result"),
    "minutes_guard_review_bucket": ("minutes_guard_review_bucket", "minutes_bucket"),
    "context_pick_alignment": ("context_pick_alignment",),
    "context_caution_level": ("context_caution_level",),
    "defense_context_signal": ("defense_context_signal", "defense_signal", "defense_context"),
    "pace_context_signal": ("pace_context_signal", "pace_signal", "pace_context"),
    "playoff_context_signal": ("playoff_context_signal", "playoff_signal", "playoff_context"),
}

NUMERIC_COLUMNS = {
    "line": ("line", "sportsbook_line", "line_value", "market_line"),
    "actual_value": ("actual_value", "actual", "actual_points"),
    "edge": ("edge", "side_edge", "edge_pct", "side_edge_pct", "dir_edge"),
    "confidence": ("confidence", "base_confidence", "final_confidence"),
    "quality_score": ("quality_score", "quality", "selection_score"),
    "minutes_basis": ("minutes_basis",),
    "projected_minutes": ("projected_minutes", "minutes_projected", "expected_minutes", "projected_min"),
    "minutes_recent": ("minutes_recent", "min_recent", "recent_minutes"),
    "minutes_avg": ("minutes_avg", "min_avg", "average_minutes", "avg_minutes"),
    "manual_minutes_limit": ("manual_minutes_limit", "minutes_limit"),
    "actual_minutes": ("actual_minutes", "minutes_actual", "box_score_minutes"),
    "model_projection": ("model_projection", "projection", "projected_value"),
    "usage_rate": ("usage_rate", "usage", "avg_usage_rate"),
    "projected_usage": ("projected_usage", "usage_projection", "projected_usage_rate"),
    "odds": ("odds", "american_odds", "offered_odds"),
}
ACTIONABLE_NUMERIC_FIELDS = tuple(field for field in NUMERIC_COLUMNS if field not in OUTCOME_ONLY_NUMERIC_FIELDS)

CATEGORICAL_FIELDS = [
    "context_pick_alignment",
    "context_caution_level",
    "defense_context_signal",
    "pace_context_signal",
    "playoff_context_signal",
    "team",
    "opponent",
    "player_name",
    "odds_bucket",
    "line_bucket",
    "edge_bucket",
    "confidence_bucket",
    "quality_bucket",
    "minutes_bucket",
]
ACTIONABLE_CATEGORICAL_FIELDS = tuple(
    field
    for field in CATEGORICAL_FIELDS
    if field not in OUTCOME_ONLY_TEXT_FIELDS and field not in IDENTITY_FIELDS
)
IDENTITY_CATEGORICAL_FIELDS = tuple(field for field in CATEGORICAL_FIELDS if field in IDENTITY_FIELDS)

CSV_COLUMNS = [
    "attribution_group",
    "prediction_date",
    "player_name",
    "player_id",
    "team",
    "opponent",
    "market_type",
    "selection",
    "line",
    "actual_value",
    "edge",
    "confidence",
    "quality_score",
    "minutes_basis",
    "projected_minutes",
    "minutes_recent",
    "minutes_avg",
    "manual_minutes_limit",
    "actual_minutes",
    "model_projection",
    "usage_rate",
    "projected_usage",
    "odds",
    "odds_bucket",
    "line_bucket",
    "edge_bucket",
    "confidence_bucket",
    "quality_bucket",
    "minutes_bucket",
    "context_pick_alignment",
    "context_caution_level",
    "defense_context_signal",
    "pace_context_signal",
    "playoff_context_signal",
    "result_status",
    "source_type",
    "source_file",
]


def low_line_over_minutes_guard_missed_winner_attribution_json_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"low_line_over_minutes_guard_missed_winner_attribution_{date}.json"


def low_line_over_minutes_guard_missed_winner_attribution_txt_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "operator" / f"low_line_over_minutes_guard_missed_winner_attribution_{date}.txt"


def low_line_over_minutes_guard_missed_winner_attribution_csv_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "operator" / f"low_line_over_minutes_guard_missed_winner_attribution_{date}.csv"


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


def _line_key(value: Any) -> str:
    number = _to_float(value)
    return "" if number is None else f"{number:.4f}"


def _odds_bucket(value: Any) -> str:
    odds = _to_float(value)
    if odds is None:
        return "missing_odds"
    if odds <= -150:
        return "heavy_favorite"
    if odds < 0:
        return "favorite"
    if odds < 150:
        return "plus_money_under_150"
    return "plus_money_150_plus"


def _line_bucket(value: Any) -> str:
    line = _to_float(value)
    if line is None:
        return "missing_line"
    if line < 10:
        return "line_lt_10"
    if line < 12:
        return "line_10_to_12"
    return "line_12_to_15"


def _edge_bucket(value: Any) -> str:
    edge = _to_float(value)
    if edge is None:
        return "missing_edge"
    if edge < 1:
        return "edge_lt_1"
    if edge < 3:
        return "edge_1_to_3"
    return "edge_gte_3"


def _confidence_bucket(value: Any) -> str:
    confidence = _to_float(value)
    if confidence is None:
        return "missing_confidence"
    if confidence < 0.65:
        return "confidence_lt_0_65"
    if confidence < 0.75:
        return "confidence_0_65_to_0_75"
    return "confidence_gte_0_75"


def _quality_bucket(value: Any) -> str:
    quality = _to_float(value)
    if quality is None:
        return "missing_quality"
    if quality < 55:
        return "quality_lt_55"
    if quality < 65:
        return "quality_55_to_65"
    return "quality_gte_65"


def _minutes_bucket(value: Any) -> str:
    minutes = _to_float(value)
    if minutes is None:
        return "missing_minutes"
    if minutes < 26:
        return "minutes_lt_26"
    return "minutes_26_to_28"


def _source_frame(frame: pd.DataFrame, *, source_type: str, source_file: str) -> pd.DataFrame:
    df = frame.copy(deep=True)
    out = pd.DataFrame(index=df.index)
    out["source_type"] = source_type
    out["source_file"] = source_file
    for field, candidates in TEXT_COLUMNS.items():
        out[field] = _coalesce_text(df, candidates)
    for field, candidates in NUMERIC_COLUMNS.items():
        out[field] = _coalesce_numeric(df, candidates)
    out["prediction_date"] = out["prediction_date"].map(_normalize_date)
    out["player_id_key"] = out["player_id"].map(_id_key)
    out["player_name_key"] = out["player_name"].map(_name_key)
    out["market_type"] = out["market_type"].map(_normalize_market)
    out["selection"] = out["selection"].map(_normalize_selection)
    out["result_status"] = out["result_status"].map(_normalize_result)
    out["line_key"] = out["line"].map(_line_key)
    out["minutes_guard_review_bucket"] = [
        bucket if bucket in {"weak_minutes_basis", "borderline_minutes_basis", "stable_minutes_basis", "missing_minutes_basis"}
        else _bucket_for_minutes_basis(minutes_basis)
        for bucket, minutes_basis in zip(out["minutes_guard_review_bucket"].map(_safe_text), out["minutes_basis"])
    ]
    out["odds_bucket"] = out["odds"].map(_odds_bucket)
    out["line_bucket"] = out["line"].map(_line_bucket)
    out["edge_bucket"] = out["edge"].map(_edge_bucket)
    out["confidence_bucket"] = out["confidence"].map(_confidence_bucket)
    out["quality_bucket"] = out["quality_score"].map(_quality_bucket)
    out["minutes_bucket"] = out["minutes_basis"].map(_minutes_bucket)
    return out.reset_index(drop=True)


def _resolve_history_path(runtime_root: Path, filename: str, explicit: Any) -> Any:
    if explicit is not None:
        return explicit
    if runtime_root.as_posix().replace("\\", "/").endswith("outputs/runtime"):
        return Path("data/history") / filename
    return runtime_root.parent / "history" / filename


def _identity_key(row: Mapping[str, Any], *, use_id: bool) -> tuple[str, str, str, str, str] | None:
    prediction_date = _safe_text(row.get("prediction_date"))
    player = _safe_text(row.get("player_id_key" if use_id else "player_name_key"))
    market_type = _safe_text(row.get("market_type"))
    selection = _safe_text(row.get("selection"))
    line_key = _safe_text(row.get("line_key")) or _line_key(row.get("line"))
    if not prediction_date or not player or not market_type or not selection or not line_key:
        return None
    return prediction_date, player, market_type, selection, line_key


def _unique_lookup(frame: pd.DataFrame, *, use_id: bool) -> dict[tuple[str, str, str, str, str], pd.Series]:
    lookup: dict[tuple[str, str, str, str, str], pd.Series] = {}
    ambiguous: set[tuple[str, str, str, str, str]] = set()
    if frame.empty:
        return lookup
    for _, row in frame.iterrows():
        key = _identity_key(row, use_id=use_id)
        if key is None:
            continue
        if key in lookup:
            ambiguous.add(key)
        else:
            lookup[key] = row
    for key in ambiguous:
        lookup.pop(key, None)
    return lookup


def _first_present(current: Any, candidate: Any) -> Any:
    if _to_float(current) is not None or _safe_text(current):
        return current
    return candidate


def _overlay_history(outcome_rows: pd.DataFrame, history_rows: pd.DataFrame) -> pd.DataFrame:
    if outcome_rows.empty or history_rows.empty:
        return outcome_rows.copy(deep=True)
    id_lookup = _unique_lookup(history_rows, use_id=True)
    name_lookup = _unique_lookup(history_rows, use_id=False)
    working = outcome_rows.copy(deep=True)
    overlay_columns = [
        column
        for column in CSV_COLUMNS
        if column not in {"attribution_group", "source_type", "source_file"}
    ]
    for idx, row in working.iterrows():
        match = None
        id_key = _identity_key(row, use_id=True)
        if id_key is not None:
            match = id_lookup.get(id_key)
        if match is None:
            name_key = _identity_key(row, use_id=False)
            if name_key is not None:
                match = name_lookup.get(name_key)
        if match is None:
            continue
        for column in overlay_columns:
            if column in match.index:
                working.at[idx, column] = _first_present(working.at[idx, column], match.get(column))
    for bucket_column, source_column in [
        ("odds_bucket", "odds"),
        ("line_bucket", "line"),
        ("edge_bucket", "edge"),
        ("confidence_bucket", "confidence"),
        ("quality_bucket", "quality_score"),
        ("minutes_bucket", "minutes_basis"),
    ]:
        if bucket_column in working.columns:
            mapper = {
                "odds_bucket": _odds_bucket,
                "line_bucket": _line_bucket,
                "edge_bucket": _edge_bucket,
                "confidence_bucket": _confidence_bucket,
                "quality_bucket": _quality_bucket,
                "minutes_bucket": _minutes_bucket,
            }[bucket_column]
            working[bucket_column] = working[source_column].map(mapper)
    return working.reset_index(drop=True)


def _load_rows(
    prediction_date: str,
    *,
    runtime_root: Path,
    market_shadow_history: str | Path | pd.DataFrame | None,
    outcome_csv: str | Path | pd.DataFrame | None,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    source_files: list[dict[str, Any]] = []
    market_shadow_history = _resolve_history_path(runtime_root, "market_shadow_history.csv", market_shadow_history)
    outcome_csv = outcome_csv if outcome_csv is not None else low_line_over_minutes_guard_outcome_csv_path_for_date(prediction_date, runtime_root)

    outcome_raw = _read_csv(outcome_csv)
    history_raw = _read_csv(market_shadow_history)
    outcome_rows = _source_frame(
        outcome_raw,
        source_type="low_line_over_minutes_guard_outcome",
        source_file="<dataframe>" if isinstance(outcome_csv, pd.DataFrame) else str(outcome_csv),
    ) if not outcome_raw.empty else pd.DataFrame()
    history_rows = _source_frame(
        history_raw,
        source_type="market_shadow_history",
        source_file="<dataframe>" if isinstance(market_shadow_history, pd.DataFrame) else str(market_shadow_history),
    ) if not history_raw.empty else pd.DataFrame()
    if not outcome_raw.empty:
        source_files.append(
            {
                "source_type": "low_line_over_minutes_guard_outcome",
                "source_file": "<dataframe>" if isinstance(outcome_csv, pd.DataFrame) else str(outcome_csv),
                "rows": int(len(outcome_raw)),
            }
        )
    if not history_raw.empty:
        source_files.append(
            {
                "source_type": "market_shadow_history",
                "source_file": "<dataframe>" if isinstance(market_shadow_history, pd.DataFrame) else str(market_shadow_history),
                "rows": int(len(history_raw)),
            }
        )
    rows = _overlay_history(outcome_rows, history_rows) if not outcome_rows.empty else history_rows
    if rows.empty:
        return rows, source_files
    rows = rows[
        rows["market_type"].eq("player_points")
        & rows["selection"].eq("over")
        & rows["line"].map(lambda value: (_to_float(value) is not None) and (_to_float(value) < 15.0))
        & rows["minutes_guard_review_bucket"].eq("weak_minutes_basis")
    ].copy()
    return rows.reset_index(drop=True), source_files


def _avg(frame: pd.DataFrame, column: str) -> float | None:
    if frame.empty or column not in frame.columns:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return None
    return round(float(values.mean()), 4)


def _group_averages(frame: pd.DataFrame) -> dict[str, Any]:
    return {column: _avg(frame, column) for column in NUMERIC_COLUMNS}


def _numeric_comparison(winners: pd.DataFrame, losers: pd.DataFrame) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for column in NUMERIC_COLUMNS:
        winner_avg = _avg(winners, column)
        loser_avg = _avg(losers, column)
        out[column] = {
            "missed_winner_avg": winner_avg,
            "saved_loser_avg": loser_avg,
            "winner_minus_loser": round(float(winner_avg) - float(loser_avg), 4)
            if winner_avg is not None and loser_avg is not None
            else None,
            "missed_winner_count": int(pd.to_numeric(winners.get(column, pd.Series(dtype=float)), errors="coerce").notna().sum()),
            "saved_loser_count": int(pd.to_numeric(losers.get(column, pd.Series(dtype=float)), errors="coerce").notna().sum()),
        }
    return out


def _categorical_comparison(winners: pd.DataFrame, losers: pd.DataFrame) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    total_winners = len(winners)
    total_losers = len(losers)
    combined = pd.concat([winners.assign(_group="missed_winner"), losers.assign(_group="saved_loser")], ignore_index=True)
    for field in CATEGORICAL_FIELDS:
        if field not in combined.columns:
            continue
        field_out: dict[str, Any] = {}
        for value, group in combined.groupby(field, sort=True, dropna=False):
            label = _safe_text(value) or "missing"
            winner_count = int(group["_group"].eq("missed_winner").sum())
            loser_count = int(group["_group"].eq("saved_loser").sum())
            total = winner_count + loser_count
            field_out[label] = {
                "missed_winner_count": winner_count,
                "saved_loser_count": loser_count,
                "total_rows": total,
                "winner_rate_within_category": round(winner_count / total, 4) if total else None,
                "winner_share": round(winner_count / total_winners, 4) if total_winners else None,
                "loser_share": round(loser_count / total_losers, 4) if total_losers else None,
            }
        out[field] = field_out
    return out


def _support_payload(winner_count: int, loser_count: int) -> dict[str, Any]:
    total = int(winner_count + loser_count)
    return {
        "winner_count": int(winner_count),
        "loser_count": int(loser_count),
        "total_count": total,
        "winner_share": round(winner_count / total, 4) if total else None,
        "loser_share": round(loser_count / total, 4) if total else None,
        "sample_status": "supported" if total >= SIGNAL_MIN_SUPPORT else "low_support",
    }


def _numeric_signals(comparison: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    winner_signals: list[dict[str, Any]] = []
    loser_signals: list[dict[str, Any]] = []
    for field, values in comparison.items():
        diff = _to_float(values.get("winner_minus_loser"))
        threshold = NUMERIC_SIGNAL_THRESHOLDS.get(field)
        if diff is None or threshold is None or abs(diff) < threshold:
            continue
        winner_count = int(values.get("missed_winner_count", 0) or 0)
        loser_count = int(values.get("saved_loser_count", 0) or 0)
        support = _support_payload(winner_count, loser_count)
        strength = round(abs(diff) / threshold, 4)
        if diff > 0:
            winner_signals.append(
                {
                    "signal": f"missed_winners_higher_{field}",
                    "field": field,
                    "direction": "missed_winners_higher",
                    "difference": round(diff, 4),
                    "strength": strength,
                    **support,
                }
            )
            loser_signals.append(
                {
                    "signal": f"saved_losers_lower_{field}",
                    "field": field,
                    "direction": "saved_losers_lower",
                    "difference": round(diff, 4),
                    "strength": strength,
                    **support,
                }
            )
        else:
            winner_signals.append(
                {
                    "signal": f"missed_winners_lower_{field}",
                    "field": field,
                    "direction": "missed_winners_lower",
                    "difference": round(diff, 4),
                    "strength": strength,
                    **support,
                }
            )
            loser_signals.append(
                {
                    "signal": f"saved_losers_higher_{field}",
                    "field": field,
                    "direction": "saved_losers_higher",
                    "difference": round(diff, 4),
                    "strength": strength,
                    **support,
                }
            )
    return winner_signals, loser_signals


def _categorical_signals(
    comparison: Mapping[str, Mapping[str, Any]],
    *,
    overall_winner_rate: float | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if overall_winner_rate is None:
        return [], []
    winner_signals: list[dict[str, Any]] = []
    loser_signals: list[dict[str, Any]] = []
    for field, buckets in comparison.items():
        if not isinstance(buckets, Mapping):
            continue
        for label, values in buckets.items():
            if not isinstance(values, Mapping):
                continue
            total = int(values.get("total_rows", 0) or 0)
            rate = values.get("winner_rate_within_category")
            if total < CATEGORICAL_MIN_ROWS or rate is None:
                continue
            winner_count = int(values.get("missed_winner_count", 0) or 0)
            loser_count = int(values.get("saved_loser_count", 0) or 0)
            support = _support_payload(winner_count, loser_count)
            delta = round(float(rate) - overall_winner_rate, 4)
            strength = round(abs(delta) / CATEGORICAL_SIGNAL_DELTA, 4)
            if delta >= CATEGORICAL_SIGNAL_DELTA:
                winner_signals.append(
                    {
                        "signal": f"{field}:{label}_leans_missed_winner",
                        "field": field,
                        "category": str(label),
                        "winner_rate": rate,
                        "delta_vs_overall": delta,
                        "strength": strength,
                        **support,
                    }
                )
            elif delta <= -CATEGORICAL_SIGNAL_DELTA:
                loser_signals.append(
                    {
                        "signal": f"{field}:{label}_leans_saved_loser",
                        "field": field,
                        "category": str(label),
                        "winner_rate": rate,
                        "delta_vs_overall": delta,
                        "strength": strength,
                        **support,
                    }
                )
    return winner_signals, loser_signals


def _mapping_subset(mapping: Mapping[str, Any], fields: tuple[str, ...] | frozenset[str]) -> dict[str, Any]:
    allowed = set(fields)
    return {key: value for key, value in mapping.items() if key in allowed}


def _sort_signals(signals: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    return sorted(
        signals,
        key=lambda row: _to_float(row.get("strength")) or 0.0,
        reverse=True,
    )[:limit]


def _candidate_refinement_rules(
    numeric: Mapping[str, Mapping[str, Any]],
    categorical: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []

    def diff(field: str) -> float | None:
        row = numeric.get(field, {})
        return _to_float(row.get("winner_minus_loser")) if isinstance(row, Mapping) else None

    def winner_avg(field: str) -> float | None:
        row = numeric.get(field, {})
        return _to_float(row.get("missed_winner_avg")) if isinstance(row, Mapping) else None

    if (line_diff := diff("line")) is not None and line_diff <= -1.0 and winner_avg("line") is not None:
        rules.append(
            {
                "rule": f"review_weak_minutes_over_keep_candidate_when_line_below_{round(float(winner_avg('line')) + 0.5, 1)}",
                "reason": "missed_winners_had_materially_lower_lines_than_saved_losers",
            }
        )
    if (edge_diff := diff("edge")) is not None and edge_diff >= 0.5 and winner_avg("edge") is not None:
        rules.append(
            {
                "rule": f"review_weak_minutes_over_keep_candidate_when_edge_gte_{round(float(winner_avg('edge')), 1)}",
                "reason": "missed_winners_had_stronger_edge_than_saved_losers",
            }
        )
    if (conf_diff := diff("confidence")) is not None and conf_diff >= 0.05 and winner_avg("confidence") is not None:
        rules.append(
            {
                "rule": f"review_weak_minutes_over_keep_candidate_when_confidence_gte_{round(float(winner_avg('confidence')), 2)}",
                "reason": "missed_winners_had_stronger_confidence_than_saved_losers",
            }
        )
    if (quality_diff := diff("quality_score")) is not None and quality_diff >= 5.0 and winner_avg("quality_score") is not None:
        rules.append(
            {
                "rule": f"review_weak_minutes_over_keep_candidate_when_quality_gte_{round(float(winner_avg('quality_score')), 1)}",
                "reason": "missed_winners_had_stronger_quality_than_saved_losers",
            }
        )

    context = categorical.get("context_pick_alignment", {})
    if isinstance(context, Mapping):
        aligned = context.get("aligned")
        if isinstance(aligned, Mapping) and _to_float(aligned.get("winner_rate_within_category")) is not None:
            if float(aligned["winner_rate_within_category"]) >= 0.6 and int(aligned.get("total_rows", 0) or 0) >= CATEGORICAL_MIN_ROWS:
                rules.append(
                    {
                        "rule": "review_weak_minutes_over_keep_candidate_when_context_pick_alignment_aligned",
                        "reason": "aligned_context_leaned_toward_missed_winners",
                    }
                )
    caution = categorical.get("context_caution_level", {})
    if isinstance(caution, Mapping):
        for label in ("high", "high_caution", "caution_high"):
            bucket = caution.get(label)
            if isinstance(bucket, Mapping) and _to_float(bucket.get("winner_rate_within_category")) is not None:
                if float(bucket["winner_rate_within_category"]) <= 0.4 and int(bucket.get("total_rows", 0) or 0) >= CATEGORICAL_MIN_ROWS:
                    rules.append(
                        {
                            "rule": f"review_weak_minutes_over_tighten_candidate_when_context_caution_level_{label}",
                            "reason": "high_caution_context_leaned_toward_saved_losers",
                        }
                    )
                    break
    return rules


def select_readiness_verdict(
    *,
    missed_winner_count: int,
    saved_loser_count: int,
    strongest_winner_signals: list[dict[str, Any]],
    strongest_loser_signals: list[dict[str, Any]],
    candidate_refinement_rules: list[dict[str, Any]],
) -> str:
    if missed_winner_count + saved_loser_count < MIN_ATTRIBUTION_SAMPLE:
        return "INSUFFICIENT_SAMPLE"
    if candidate_refinement_rules:
        return "ATTRIBUTION_POLICY_REFINEMENT_CANDIDATE"
    if strongest_winner_signals or strongest_loser_signals:
        return "ATTRIBUTION_REVIEW_READY"
    return "ATTRIBUTION_MIXED_NO_CLEAR_RULE"


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


def build_low_line_over_minutes_guard_missed_winner_attribution(
    prediction_date: str,
    *,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    market_shadow_history: str | Path | pd.DataFrame | None = None,
    outcome_csv: str | Path | pd.DataFrame | None = None,
    policy_simulation_csv: str | Path | pd.DataFrame | None = None,
) -> dict[str, Any]:
    runtime_root = Path(runtime_root)
    rows, source_files = _load_rows(
        prediction_date,
        runtime_root=runtime_root,
        market_shadow_history=market_shadow_history,
        outcome_csv=outcome_csv,
    )
    if policy_simulation_csv is None:
        policy_simulation_csv = low_line_over_minutes_guard_policy_simulation_csv_path_for_date(prediction_date, runtime_root)
    policy_frame = _read_csv(policy_simulation_csv)
    if not policy_frame.empty:
        source_files.append(
            {
                "source_type": "low_line_over_minutes_guard_policy_simulation",
                "source_file": "<dataframe>" if isinstance(policy_simulation_csv, pd.DataFrame) else str(policy_simulation_csv),
                "rows": int(len(policy_frame)),
            }
        )

    terminal_rows = rows[rows["result_status"].isin(TERMINAL_STATUSES)].copy() if not rows.empty else rows
    missed_winners = terminal_rows[terminal_rows["result_status"].eq("hit")].copy() if not terminal_rows.empty else pd.DataFrame()
    saved_losers = terminal_rows[terminal_rows["result_status"].eq("miss")].copy() if not terminal_rows.empty else pd.DataFrame()
    comparison_df = pd.concat(
        [
            missed_winners.assign(attribution_group="missed_winner"),
            saved_losers.assign(attribution_group="saved_loser"),
        ],
        ignore_index=True,
    )
    pending_rows_excluded = int((~rows["result_status"].isin(TERMINAL_STATUSES)).sum()) if not rows.empty else 0
    voids_excluded_from_winner_loser = int(terminal_rows["result_status"].eq("void").sum()) if not terminal_rows.empty else 0
    pushes_excluded_from_winner_loser = int(terminal_rows["result_status"].eq("push").sum()) if not terminal_rows.empty else 0

    numeric = _numeric_comparison(missed_winners, saved_losers)
    categorical = _categorical_comparison(missed_winners, saved_losers)
    total_compare = len(missed_winners) + len(saved_losers)
    overall_winner_rate = round(len(missed_winners) / total_compare, 4) if total_compare else None
    actionable_numeric = _mapping_subset(numeric, ACTIONABLE_NUMERIC_FIELDS)
    post_outcome_numeric = _mapping_subset(numeric, OUTCOME_ONLY_NUMERIC_FIELDS)
    actionable_categorical = _mapping_subset(categorical, ACTIONABLE_CATEGORICAL_FIELDS)
    identity_categorical = _mapping_subset(categorical, IDENTITY_CATEGORICAL_FIELDS)

    numeric_winner_signals, numeric_loser_signals = _numeric_signals(actionable_numeric)
    categorical_winner_signals, categorical_loser_signals = _categorical_signals(
        actionable_categorical,
        overall_winner_rate=overall_winner_rate,
    )
    strongest_winner_signals = _sort_signals(numeric_winner_signals + categorical_winner_signals)
    strongest_loser_signals = _sort_signals(numeric_loser_signals + categorical_loser_signals)

    identity_winner_signals, identity_loser_signals = _categorical_signals(
        identity_categorical,
        overall_winner_rate=overall_winner_rate,
    )
    strongest_identity_winner_signals = _sort_signals(identity_winner_signals)
    strongest_identity_loser_signals = _sort_signals(identity_loser_signals)

    post_winner_signals, post_loser_signals = _numeric_signals(post_outcome_numeric)
    strongest_post_winner_diagnostics = _sort_signals(post_winner_signals)
    strongest_post_loser_diagnostics = _sort_signals(post_loser_signals)

    candidate_rules = _candidate_refinement_rules(actionable_numeric, actionable_categorical)
    readiness = select_readiness_verdict(
        missed_winner_count=int(len(missed_winners)),
        saved_loser_count=int(len(saved_losers)),
        strongest_winner_signals=strongest_winner_signals,
        strongest_loser_signals=strongest_loser_signals,
        candidate_refinement_rules=candidate_rules,
    )

    for column in CSV_COLUMNS:
        if column not in comparison_df.columns:
            comparison_df[column] = pd.NA
    comparison_df = comparison_df.reindex(columns=CSV_COLUMNS)
    payload = {
        "prediction_date": prediction_date,
        "note": "review_only_no_prediction_grading_kelly_history_or_suppression_change",
        "missed_winner_count": int(len(missed_winners)),
        "saved_loser_count": int(len(saved_losers)),
        "net_saved_result_count": int(len(saved_losers) - len(missed_winners)),
        "pending_rows_excluded": pending_rows_excluded,
        "voids_excluded_from_winner_loser": voids_excluded_from_winner_loser,
        "pushes_excluded_from_winner_loser": pushes_excluded_from_winner_loser,
        "metrics_by_group": {
            "missed_winners": _group_averages(missed_winners),
            "saved_losers": _group_averages(saved_losers),
        },
        "numeric_comparison": numeric,
        "categorical_comparison": categorical,
        "overall_winner_rate": overall_winner_rate,
        "post_outcome_diagnostics": {
            "numeric_comparison": post_outcome_numeric,
            "strongest_winner_diagnostics": strongest_post_winner_diagnostics,
            "strongest_loser_diagnostics": strongest_post_loser_diagnostics,
        },
        "actionable_pre_pick_signals": {
            "numeric_comparison": actionable_numeric,
            "categorical_comparison": actionable_categorical,
            "strongest_winner_signals": strongest_winner_signals,
            "strongest_loser_signals": strongest_loser_signals,
            "top_generalized_winner_signal": _top_signal(strongest_winner_signals, fallback=NO_GENERALIZED_SIGNAL),
            "top_generalized_loser_signal": _top_signal(strongest_loser_signals, fallback=NO_GENERALIZED_SIGNAL),
            "top_winner_signal": _top_signal(strongest_winner_signals, fallback=NO_GENERALIZED_SIGNAL),
            "top_loser_signal": _top_signal(strongest_loser_signals, fallback=NO_GENERALIZED_SIGNAL),
        },
        "identity_diagnostics": {
            "categorical_comparison": identity_categorical,
            "strongest_winner_signals": strongest_identity_winner_signals,
            "strongest_loser_signals": strongest_identity_loser_signals,
            "top_identity_winner_signal": _top_signal(strongest_identity_winner_signals, fallback=NO_IDENTITY_SIGNAL),
            "top_identity_loser_signal": _top_signal(strongest_identity_loser_signals, fallback=NO_IDENTITY_SIGNAL),
        },
        "strongest_winner_signals": strongest_winner_signals,
        "strongest_loser_signals": strongest_loser_signals,
        "candidate_refinement_rules": candidate_rules,
        "candidate_refinement_rule_count": len(candidate_rules),
        "readiness_verdict": readiness,
        "history_mutated": False,
        "live_picks_suppressed": False,
        "attribution_df": comparison_df,
        "source_files_scanned": source_files,
    }
    serializable = _json_safe({key: value for key, value in payload.items() if key != "attribution_df"})
    serializable["attribution_df"] = comparison_df
    return serializable


def _format_num(value: Any) -> str:
    number = _to_float(value)
    return "n/a" if number is None else f"{number:.4f}"


def _top_signal(signals: Any, *, fallback: str = "n/a") -> str:
    if isinstance(signals, list) and signals and isinstance(signals[0], Mapping):
        return _safe_text(signals[0].get("signal")) or fallback
    return fallback


def _format_txt(payload: Mapping[str, Any], prediction_date: str) -> str:
    sep = "=" * 78
    sep2 = "-" * 78
    rules = payload.get("candidate_refinement_rules", [])
    post = payload.get("post_outcome_diagnostics", {})
    post_winner = post.get("strongest_winner_diagnostics", []) if isinstance(post, Mapping) else []
    post_loser = post.get("strongest_loser_diagnostics", []) if isinstance(post, Mapping) else []
    identity = payload.get("identity_diagnostics", {})
    identity_winner = identity.get("strongest_winner_signals", []) if isinstance(identity, Mapping) else []
    identity_loser = identity.get("strongest_loser_signals", []) if isinstance(identity, Mapping) else []
    lines = [
        f"{sep}\n",
        "LOW-LINE OVER MINUTES GUARD MISSED WINNER ATTRIBUTION (Phase 15G -- REVIEW ONLY)\n",
        f"date: {prediction_date}    note: {payload.get('note', '')}\n",
        f"{sep}\n\n",
        "OVERVIEW\n",
        f"{sep2}\n",
        f"  missed_winner_count          : {payload.get('missed_winner_count', 0)}\n",
        f"  saved_loser_count            : {payload.get('saved_loser_count', 0)}\n",
        f"  net_saved_result_count       : {payload.get('net_saved_result_count', 0)}\n",
        f"  top_generalized_winner_signal: {_top_signal(payload.get('strongest_winner_signals'), fallback=NO_GENERALIZED_SIGNAL)}\n",
        f"  top_generalized_loser_signal : {_top_signal(payload.get('strongest_loser_signals'), fallback=NO_GENERALIZED_SIGNAL)}\n",
        f"  top_identity_winner_signal   : {_top_signal(identity_winner, fallback=NO_IDENTITY_SIGNAL)}\n",
        f"  top_identity_loser_signal    : {_top_signal(identity_loser, fallback=NO_IDENTITY_SIGNAL)}\n",
        f"  candidate_refinement_rules   : {payload.get('candidate_refinement_rule_count', 0)}\n",
        f"  readiness_verdict            : {payload.get('readiness_verdict')}\n\n",
        "POST-OUTCOME DIAGNOSTICS\n",
        f"{sep2}\n",
        f"  top_winner_diagnostic        : {_top_signal(post_winner)}\n",
        f"  top_loser_diagnostic         : {_top_signal(post_loser)}\n\n",
        "CANDIDATE REFINEMENT RULES\n",
        f"{sep2}\n",
    ]
    if isinstance(rules, list) and rules:
        for row in rules[:10]:
            if isinstance(row, Mapping):
                lines.append(f"  - {row.get('rule')} ({row.get('reason')})\n")
    else:
        lines.append("  none\n")
    lines.extend(
        [
            "\nNOTE: REVIEW ONLY; no prediction/grading/Kelly/history changes and no picks suppressed.\n",
            f"{sep}\n",
        ]
    )
    return "".join(lines)


def write_low_line_over_minutes_guard_missed_winner_attribution(
    prediction_date: str,
    *,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    market_shadow_history: str | Path | pd.DataFrame | None = None,
    outcome_csv: str | Path | pd.DataFrame | None = None,
    policy_simulation_csv: str | Path | pd.DataFrame | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    payload = build_low_line_over_minutes_guard_missed_winner_attribution(
        prediction_date,
        runtime_root=runtime_root,
        market_shadow_history=market_shadow_history,
        outcome_csv=outcome_csv,
        policy_simulation_csv=policy_simulation_csv,
    )
    json_path = low_line_over_minutes_guard_missed_winner_attribution_json_path_for_date(prediction_date, runtime_root)
    txt_path = low_line_over_minutes_guard_missed_winner_attribution_txt_path_for_date(prediction_date, runtime_root)
    csv_path = low_line_over_minutes_guard_missed_winner_attribution_csv_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    attribution_df = payload.get("attribution_df")
    csv_df = attribution_df if isinstance(attribution_df, pd.DataFrame) else pd.DataFrame(columns=CSV_COLUMNS)
    csv_df.reindex(columns=CSV_COLUMNS).to_csv(csv_path, index=False)

    serializable = {key: value for key, value in payload.items() if key != "attribution_df"}
    json_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    txt_path.write_text(_format_txt(serializable, prediction_date), encoding="utf-8")
    return json_path, txt_path, csv_path, serializable


__all__ = [
    "MIN_ATTRIBUTION_SAMPLE",
    "build_low_line_over_minutes_guard_missed_winner_attribution",
    "low_line_over_minutes_guard_missed_winner_attribution_csv_path_for_date",
    "low_line_over_minutes_guard_missed_winner_attribution_json_path_for_date",
    "low_line_over_minutes_guard_missed_winner_attribution_txt_path_for_date",
    "select_readiness_verdict",
    "write_low_line_over_minutes_guard_missed_winner_attribution",
]
