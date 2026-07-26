from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.history_tracking import MARKET_SHADOW_HISTORY_COLUMNS, update_market_readiness_summary


FINAL_RESULTS = {"hit", "miss", "push"}
SUPPORTED_SELECTIONS = {"over", "under"}
COMBO_MARKET_COMPONENTS = {
    "player_points_assists": ("player_points", "player_assists"),
    "player_points_rebounds": ("player_points", "player_rebounds"),
    "player_rebounds_assists": ("player_rebounds", "player_assists"),
    "player_points_rebounds_assists": ("player_points", "player_rebounds", "player_assists"),
}
COMPONENT_MARKET_STAT_COLUMNS = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
}
STAT_SOURCE_LOCAL = "local_artifacts"
STAT_SOURCE_PROVIDER = "provider_api"
STAT_SOURCE_NONE = "none"
STAT_SOURCE_MODES = {"local", "auto", "provider"}
MARKET_TYPE_ALIASES = {
    "points": "player_points",
    "rebounds": "player_rebounds",
    "assists": "player_assists",
    "points_rebounds": "player_points_rebounds",
    "points_assists": "player_points_assists",
    "rebounds_assists": "player_rebounds_assists",
    "points_rebounds_assists": "player_points_rebounds_assists",
    "threes": "player_3pt_made",
    "3pt_made": "player_3pt_made",
    "three_pointers_made": "player_3pt_made",
}


@dataclass
class ComponentActualLookup:
    values: dict[tuple[str, str, str, str], float] = field(default_factory=dict)
    sources: dict[tuple[str, str, str, str], str] = field(default_factory=dict)
    conflicts: set[tuple[str, str, str, str]] = field(default_factory=set)
    source_rows: Counter[str] = field(default_factory=Counter)
    provider_rows_by_date: dict[str, int] = field(default_factory=dict)
    provider_errors_by_date: dict[str, str] = field(default_factory=dict)


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _safe_float(value: Any) -> float | None:
    text = _safe_text(value)
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _read_shadow_history(path: Path) -> pd.DataFrame:
    df = _read_csv(path)
    if df.empty:
        return pd.DataFrame(columns=MARKET_SHADOW_HISTORY_COLUMNS)
    for column in MARKET_SHADOW_HISTORY_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    ordered = MARKET_SHADOW_HISTORY_COLUMNS + [column for column in df.columns if column not in MARKET_SHADOW_HISTORY_COLUMNS]
    return df[ordered]


def _write_shadow_history(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = MARKET_SHADOW_HISTORY_COLUMNS + [column for column in df.columns if column not in MARKET_SHADOW_HISTORY_COLUMNS]
    df[ordered].to_csv(path, index=False)


def _set_cell_value(frame: pd.DataFrame, idx: Any, column: str, value: Any) -> None:
    if column not in frame.columns:
        frame.at[idx, column] = value
        return
    if pd.api.types.is_string_dtype(frame[column].dtype):
        frame.at[idx, column] = "" if value is None else str(value)
        return
    frame.at[idx, column] = value


def _normalize_market_type(value: Any) -> str:
    text = _safe_text(value).lower()
    if text == "player_threes":
        text = "threes"
    return MARKET_TYPE_ALIASES.get(text, text)


def _player_name(row: pd.Series) -> str:
    return (_safe_text(row.get("player_name")) or _safe_text(row.get("entity_name"))).lower()


def _display_player_name(row: pd.Series) -> str:
    return _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name"))


def _team_abbr(row: pd.Series) -> str:
    return (_safe_text(row.get("team_abbr")) or _safe_text(row.get("team"))).upper()


def _market_type(row: pd.Series) -> str:
    for column in ("market_type", "market", "prop_type", "raw_prop_type"):
        market_type = _normalize_market_type(row.get(column))
        if market_type:
            return market_type
    return ""


def _prediction_date(row: pd.Series) -> str:
    return _safe_text(row.get("prediction_date"))


def _selection(row: pd.Series) -> str:
    return (_safe_text(row.get("selection")) or _safe_text(row.get("side"))).lower()


def _line_value(row: pd.Series) -> float | None:
    for column in ("line", "sportsbook_line", "line_value"):
        if column in row.index:
            value = _safe_float(row.get(column))
            if value is not None:
                return value
    return None


def _actual_value(row: pd.Series) -> float | None:
    for column in ("actual_value", "stat_value", "actual"):
        if column in row.index:
            value = _safe_float(row.get(column))
            if value is not None:
                return value
    return None


def _result_status(row: pd.Series) -> str:
    for column in ("result_status", "graded_result", "result"):
        if column not in row.index:
            continue
        text = _safe_text(row.get(column)).lower()
        if text in {"hit", "win", "won"}:
            return "hit"
        if text in {"miss", "loss", "lost"}:
            return "miss"
        if text == "push":
            return "push"
    if _truthy(row.get("hit")):
        return "hit"
    if _truthy(row.get("miss")):
        return "miss"
    if _truthy(row.get("push")):
        return "push"
    return "pending"


def _truthy(value: Any) -> bool:
    return _safe_text(value).lower() in {"true", "1", "yes", "y"}


def _american_shadow_roi(odds: Any, result_status: str) -> float | str:
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
        return round(american / 100.0, 6)
    return round(100.0 / abs(american), 6)


def _calibration_fields(result_status: str, selection: str) -> tuple[bool, str]:
    reasons: list[str] = []
    if selection not in SUPPORTED_SELECTIONS:
        reasons.append(f"unsupported_selection_{selection or 'blank'}")
    if result_status == "pending":
        reasons.append("no_graded_results")
    elif result_status == "push":
        reasons.append("push_excluded_from_hit_rate")
    elif result_status not in {"hit", "miss"}:
        reasons.append("ungraded_result")
    return not reasons, ";".join(reasons)


def _candidate_actual_paths(runtime_root: Path, history_root: Path, prediction_date: str) -> list[Path]:
    return [
        runtime_root / "history" / "result_feedback.csv",
        runtime_root / "research" / f"grading_results_{prediction_date}.csv",
        runtime_root / "history" / f"graded_picks_{prediction_date}.csv",
        history_root / "pick_history.csv",
    ]


def _component_key(prediction_date: str, player_name: str, market_type: str, team_abbr: str = "") -> tuple[str, str, str, str]:
    return (
        _safe_text(prediction_date),
        _safe_text(player_name).lower(),
        _normalize_market_type(market_type),
        _safe_text(team_abbr).upper(),
    )


def _add_component_actual(
    lookup: ComponentActualLookup,
    *,
    prediction_date: str,
    player_name: str,
    market_type: str,
    actual_value: float,
    source: str,
    team_abbr: str = "",
) -> None:
    key = _component_key(prediction_date, player_name, market_type, team_abbr)
    if not key[0] or not key[1] or key[2] not in COMPONENT_MARKET_STAT_COLUMNS:
        return
    if key in lookup.values and abs(float(lookup.values[key]) - float(actual_value)) > 1e-9:
        lookup.conflicts.add(key)
        return
    lookup.values[key] = float(actual_value)
    lookup.sources[key] = source


def _merge_component_actuals(
    base: ComponentActualLookup,
    incoming: ComponentActualLookup,
    *,
    overwrite: bool = False,
) -> ComponentActualLookup:
    merged = ComponentActualLookup(
        values=dict(base.values),
        sources=dict(base.sources),
        conflicts=set(base.conflicts),
        source_rows=Counter(base.source_rows),
        provider_rows_by_date=dict(base.provider_rows_by_date),
        provider_errors_by_date=dict(base.provider_errors_by_date),
    )
    merged.source_rows.update(incoming.source_rows)
    merged.provider_rows_by_date.update(incoming.provider_rows_by_date)
    merged.provider_errors_by_date.update(incoming.provider_errors_by_date)
    for key in incoming.conflicts:
        merged.conflicts.add(key)
    for key, value in incoming.values.items():
        if key in merged.values and not overwrite:
            if abs(float(merged.values[key]) - float(value)) > 1e-9:
                merged.conflicts.add(key)
            continue
        if key in merged.values and abs(float(merged.values[key]) - float(value)) > 1e-9:
            merged.conflicts.add(key)
            if not overwrite:
                continue
        merged.values[key] = float(value)
        merged.sources[key] = incoming.sources.get(key, "")
    return merged


def _component_actual_lookup(
    *,
    runtime_root: Path,
    history_root: Path,
    prediction_dates: list[str],
) -> ComponentActualLookup:
    lookup = ComponentActualLookup()
    seen_paths: set[Path] = set()
    selected_dates = set(prediction_dates)

    for prediction_date in prediction_dates:
        for path in _candidate_actual_paths(runtime_root, history_root, prediction_date):
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            df = _read_csv(path)
            if df.empty:
                continue
            if "prediction_date" in df.columns:
                df = df[df["prediction_date"].astype(str).isin(selected_dates)].copy()
            source_count = 0
            for _, row in df.iterrows():
                row_date = _prediction_date(row) or prediction_date
                if row_date not in selected_dates:
                    continue
                market_type = _market_type(row)
                if market_type not in COMPONENT_MARKET_STAT_COLUMNS:
                    continue
                player_name = _player_name(row)
                actual_value = _actual_value(row)
                if not player_name or actual_value is None:
                    continue
                team_abbr = _team_abbr(row)
                if team_abbr:
                    _add_component_actual(
                        lookup,
                        prediction_date=row_date,
                        player_name=player_name,
                        market_type=market_type,
                        actual_value=float(actual_value),
                        source=STAT_SOURCE_LOCAL,
                        team_abbr=team_abbr,
                    )
                _add_component_actual(
                    lookup,
                    prediction_date=row_date,
                    player_name=player_name,
                    market_type=market_type,
                    actual_value=float(actual_value),
                    source=STAT_SOURCE_LOCAL,
                )
                source_count += 1
            if source_count:
                lookup.source_rows[STAT_SOURCE_LOCAL] += source_count
    return lookup


def _provider_component_actual_lookup(
    *,
    runtime_root: Path,
    prediction_dates: Iterable[str],
) -> ComponentActualLookup:
    lookup = ComponentActualLookup()
    dates = sorted({_safe_text(date) for date in prediction_dates if _safe_text(date)})
    if not dates:
        return lookup

    try:
        from courtvision.operations import (
            CourtVisionOperations,
            normalize_operation_stats,
            operations_client,
        )
    except Exception as exc:
        for date in dates:
            lookup.provider_errors_by_date[date] = f"provider_import_failed:{type(exc).__name__}"
        return lookup

    try:
        outputs_root = runtime_root.parent if runtime_root.name == "runtime" else runtime_root
        ai = CourtVisionOperations(out_dir=str(outputs_root))
        if hasattr(ai, "runtime_dir"):
            ai.runtime_dir = runtime_root
        client = operations_client(ai)
    except Exception as exc:
        for date in dates:
            lookup.provider_errors_by_date[date] = f"provider_init_failed:{type(exc).__name__}"
        return lookup

    for prediction_date in dates:
        try:
            raw_stats = client.get_stats(prediction_date, prediction_date)
            stats = normalize_operation_stats(ai, raw_stats)
        except Exception as exc:
            lookup.provider_errors_by_date[prediction_date] = f"provider_fetch_failed:{type(exc).__name__}"
            continue
        if not isinstance(stats, pd.DataFrame) or stats.empty:
            lookup.provider_rows_by_date[prediction_date] = 0
            continue
        lookup.provider_rows_by_date[prediction_date] = int(len(stats))
        source_count = 0
        for _, row in stats.iterrows():
            player_name = _player_name(row)
            if not player_name:
                continue
            team_abbr = _team_abbr(row)
            for market_type, stat_column in COMPONENT_MARKET_STAT_COLUMNS.items():
                if stat_column not in row.index:
                    continue
                actual_value = _safe_float(row.get(stat_column))
                if actual_value is None:
                    continue
                if team_abbr:
                    _add_component_actual(
                        lookup,
                        prediction_date=prediction_date,
                        player_name=player_name,
                        market_type=market_type,
                        actual_value=float(actual_value),
                        source=STAT_SOURCE_PROVIDER,
                        team_abbr=team_abbr,
                    )
                _add_component_actual(
                    lookup,
                    prediction_date=prediction_date,
                    player_name=player_name,
                    market_type=market_type,
                    actual_value=float(actual_value),
                    source=STAT_SOURCE_PROVIDER,
                )
                source_count += 1
        if source_count:
            lookup.source_rows[STAT_SOURCE_PROVIDER] += source_count
    return lookup


def _combo_component_diagnostics(
    shadow_df: pd.DataFrame,
    *,
    selected_dates: set[str],
    actual_lookup: ComponentActualLookup,
) -> dict[str, Any]:
    by_date: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "combo_rows": 0,
            "component_stats_available": 0,
            "missing_component_stats": 0,
            "component_actual_conflict": 0,
        }
    )
    stat_source_used: Counter[str] = Counter()
    missing_players: Counter[tuple[str, str, str]] = Counter()
    missing_component_counts: Counter[tuple[str, str, str, str]] = Counter()

    for _, row in shadow_df.iterrows():
        prediction_date = _prediction_date(row)
        if prediction_date not in selected_dates:
            continue
        market_type = _market_type(row)
        if market_type not in COMBO_MARKET_COMPONENTS:
            continue
        by_date[prediction_date]["combo_rows"] += 1
        status = _combo_component_status(row, actual_lookup)
        if status["available"]:
            by_date[prediction_date]["component_stats_available"] += 1
            stat_source_used[str(status["source"])] += 1
            continue
        if status["conflict_components"]:
            by_date[prediction_date]["component_actual_conflict"] += 1
        if status["missing_components"]:
            by_date[prediction_date]["missing_component_stats"] += 1
            missing_key = (
                str(status["prediction_date"]),
                str(status["player_display"]),
                str(status["team_abbr"]),
            )
            missing_players[missing_key] += 1
            for component_market in status["missing_components"]:
                missing_component_counts[missing_key + (str(component_market),)] += 1

    top_missing_players: list[dict[str, Any]] = []
    for (prediction_date, player_name, team_abbr), count in missing_players.most_common(10):
        component_counts = {
            component: component_count
            for (date, name, team, component), component_count in missing_component_counts.items()
            if date == prediction_date and name == player_name and team == team_abbr
        }
        top_missing_players.append(
            {
                "prediction_date": prediction_date,
                "player_name": player_name,
                "team_abbr": team_abbr,
                "missing_rows": int(count),
                "missing_components": dict(sorted(component_counts.items())),
            }
        )

    return {
        "by_date": {date: dict(values) for date, values in sorted(by_date.items())},
        "stat_source_used": dict(sorted(stat_source_used.items())),
        "top_missing_players_dates": top_missing_players,
    }


def _dates_missing_component_stats(diagnostics: dict[str, Any]) -> list[str]:
    by_date = diagnostics.get("by_date", {})
    if not isinstance(by_date, dict):
        return []
    return [
        str(date)
        for date, values in sorted(by_date.items())
        if isinstance(values, dict) and int(values.get("missing_component_stats", 0) or 0) > 0
    ]


def _lookup_component_actual(
    lookup: ComponentActualLookup,
    *,
    prediction_date: str,
    player_name: str,
    market_type: str,
    team_abbr: str = "",
) -> tuple[float | None, str, bool]:
    keys = []
    if team_abbr:
        keys.append(_component_key(prediction_date, player_name, market_type, team_abbr))
    keys.append(_component_key(prediction_date, player_name, market_type))
    for key in keys:
        if key in lookup.conflicts:
            return None, lookup.sources.get(key, ""), True
        if key in lookup.values:
            return lookup.values[key], lookup.sources.get(key, ""), False
    return None, "", False


def _combo_component_status(row: pd.Series, lookup: ComponentActualLookup) -> dict[str, Any]:
    prediction_date = _prediction_date(row)
    player_name = _player_name(row)
    player_display = _display_player_name(row)
    team_abbr = _team_abbr(row)
    market_type = _market_type(row)
    components = COMBO_MARKET_COMPONENTS.get(market_type, ())
    missing_components: list[str] = []
    conflict_components: list[str] = []
    sources: set[str] = set()
    values: list[float] = []
    for component_market in components:
        value, source, conflict = _lookup_component_actual(
            lookup,
            prediction_date=prediction_date,
            player_name=player_name,
            market_type=component_market,
            team_abbr=team_abbr,
        )
        if conflict:
            conflict_components.append(component_market)
            continue
        if value is None:
            missing_components.append(component_market)
            continue
        values.append(float(value))
        sources.add(source or STAT_SOURCE_NONE)
    source_label = "+".join(sorted(source for source in sources if source)) or STAT_SOURCE_NONE
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "player_display": player_display,
        "team_abbr": team_abbr,
        "market_type": market_type,
        "components": list(components),
        "values": values,
        "missing_components": missing_components,
        "conflict_components": conflict_components,
        "available": bool(components) and not missing_components and not conflict_components,
        "source": source_label,
    }


def _grade_combo_row(
    row: pd.Series,
    *,
    actual_lookup: ComponentActualLookup,
) -> tuple[str, float | None, str]:
    prediction_date = _prediction_date(row)
    player_name = _player_name(row)
    selection = _selection(row)
    line = _line_value(row)
    status = _combo_component_status(row, actual_lookup)
    market_type = str(status["market_type"])

    if market_type not in COMBO_MARKET_COMPONENTS:
        return "pending", None, "not_combo_market"
    if not prediction_date:
        return "pending", None, "missing_prediction_date"
    if not player_name:
        return "pending", None, "missing_player_name"
    if selection not in SUPPORTED_SELECTIONS:
        return "pending", None, "unsupported_selection"
    if line is None:
        return "pending", None, "missing_line"

    if status["conflict_components"]:
        return "pending", None, "component_actual_conflict"
    if status["missing_components"]:
        return "pending", None, "missing_component_stats"

    actual = float(sum(status["values"]))
    if abs(actual - line) < 1e-9:
        return "push", actual, ""
    if selection == "over":
        return ("hit" if actual > line else "miss"), actual, ""
    return ("hit" if actual < line else "miss"), actual, ""


def _selected_dates(df: pd.DataFrame, dates: list[str] | None, start_date: str | None, end_date: str | None) -> list[str]:
    if dates:
        selected = sorted({_safe_text(date) for date in dates if _safe_text(date)})
    elif "prediction_date" in df.columns:
        selected = sorted({_safe_text(value) for value in df["prediction_date"].tolist() if _safe_text(value)})
    else:
        selected = []
    if start_date:
        selected = [date for date in selected if date >= str(start_date)]
    if end_date:
        selected = [date for date in selected if date <= str(end_date)]
    return selected


def grade_market_shadow_history(
    *,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    dates: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    dry_run: bool = False,
    replace_existing: bool = False,
    stat_source: str = "local",
) -> dict[str, Any]:
    runtime_root_path = Path(runtime_root)
    history_root_path = Path(history_root)
    stat_source_mode = _safe_text(stat_source).lower() or "local"
    if stat_source_mode not in STAT_SOURCE_MODES:
        raise ValueError(f"Unsupported stat_source={stat_source!r}; expected one of {sorted(STAT_SOURCE_MODES)}")
    shadow_history_path = history_root_path / "market_shadow_history.csv"
    readiness_path = history_root_path / "market_readiness_summary.csv"
    shadow_df = _read_shadow_history(shadow_history_path)
    selected_dates = _selected_dates(shadow_df, dates, start_date, end_date)
    selected_date_set = set(selected_dates)

    if shadow_df.empty or not selected_dates:
        return {
            "dry_run": dry_run,
            "market_shadow_history_path": shadow_history_path,
            "market_readiness_summary_path": readiness_path,
            "selected_dates": selected_dates,
            "total_shadow_rows": int(len(shadow_df)),
            "combo_rows": 0,
            "updated_rows": 0,
            "would_update_rows": 0,
            "skipped_already_graded": 0,
            "skip_reasons": {},
            "updated_by_market_type": {},
            "stat_source_mode": stat_source_mode,
            "stat_source_candidate_rows": {},
            "provider_rows_by_date": {},
            "provider_errors_by_date": {},
            "combo_component_diagnostics": {"by_date": {}, "stat_source_used": {}, "top_missing_players_dates": []},
        }

    local_lookup = _component_actual_lookup(
        runtime_root=runtime_root_path,
        history_root=history_root_path,
        prediction_dates=selected_dates,
    )
    if stat_source_mode == "provider":
        actual_lookup = _provider_component_actual_lookup(
            runtime_root=runtime_root_path,
            prediction_dates=selected_dates,
        )
    else:
        actual_lookup = local_lookup

    local_diagnostics = _combo_component_diagnostics(
        shadow_df,
        selected_dates=selected_date_set,
        actual_lookup=local_lookup,
    )
    if stat_source_mode == "auto":
        missing_dates = _dates_missing_component_stats(local_diagnostics)
        if missing_dates:
            provider_lookup = _provider_component_actual_lookup(
                runtime_root=runtime_root_path,
                prediction_dates=missing_dates,
            )
            actual_lookup = _merge_component_actuals(actual_lookup, provider_lookup)

    combo_component_diagnostics = _combo_component_diagnostics(
        shadow_df,
        selected_dates=selected_date_set,
        actual_lookup=actual_lookup,
    )

    updated_df = shadow_df.copy()
    skip_reasons: Counter[str] = Counter()
    updated_by_market: Counter[str] = Counter()
    combo_rows = 0
    updated_rows = 0
    skipped_already_graded = 0

    for idx, row in shadow_df.iterrows():
        prediction_date = _prediction_date(row)
        if prediction_date not in selected_date_set:
            continue
        market_type = _market_type(row)
        if market_type not in COMBO_MARKET_COMPONENTS:
            continue
        combo_rows += 1
        if _result_status(row) in FINAL_RESULTS and not replace_existing:
            skipped_already_graded += 1
            continue

        result_status, actual_value, reason = _grade_combo_row(row, actual_lookup=actual_lookup)
        if result_status not in FINAL_RESULTS or actual_value is None:
            skip_reasons[reason or "ungraded"] += 1
            continue

        updated_rows += 1
        updated_by_market[market_type] += 1
        if dry_run:
            continue

        calibration_eligible, calibration_reason = _calibration_fields(result_status, _selection(row))
        _set_cell_value(updated_df, idx, "actual_value", actual_value)
        _set_cell_value(updated_df, idx, "result_status", result_status)
        _set_cell_value(updated_df, idx, "hit", result_status == "hit")
        _set_cell_value(updated_df, idx, "miss", result_status == "miss")
        _set_cell_value(updated_df, idx, "push", result_status == "push")
        _set_cell_value(updated_df, idx, "shadow_roi", _american_shadow_roi(row.get("odds"), result_status))
        _set_cell_value(updated_df, idx, "calibration_eligible", calibration_eligible)
        _set_cell_value(updated_df, idx, "calibration_exclusion_reason", calibration_reason)

    if not dry_run and updated_rows:
        _write_shadow_history(shadow_history_path, updated_df)
        update_market_readiness_summary(history_root=history_root_path)

    return {
        "dry_run": dry_run,
        "market_shadow_history_path": shadow_history_path,
        "market_readiness_summary_path": readiness_path,
        "selected_dates": selected_dates,
        "total_shadow_rows": int(len(shadow_df)),
        "combo_rows": int(combo_rows),
        "updated_rows": 0 if dry_run else int(updated_rows),
        "would_update_rows": int(updated_rows) if dry_run else 0,
        "skipped_already_graded": int(skipped_already_graded),
        "skip_reasons": dict(sorted(skip_reasons.items())),
        "updated_by_market_type": dict(sorted(updated_by_market.items())),
        "stat_source_mode": stat_source_mode,
        "stat_source_candidate_rows": dict(sorted(actual_lookup.source_rows.items())),
        "provider_rows_by_date": dict(sorted(actual_lookup.provider_rows_by_date.items())),
        "provider_errors_by_date": dict(sorted(actual_lookup.provider_errors_by_date.items())),
        "combo_component_diagnostics": combo_component_diagnostics,
    }


def _parse_dates(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    dates: list[str] = []
    for value in values:
        dates.extend(part.strip() for part in str(value).split(",") if part.strip())
    return dates


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grade combo markets in market_shadow_history.csv for shadow tracking only.")
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument(
        "--prediction-date",
        dest="dates",
        action="append",
        help="Prediction date to grade. May be repeated or comma-separated. Defaults to all shadow-history dates.",
    )
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--dry-run", action="store_true", help="Compute updates without writing market_shadow_history.csv.")
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Re-grade combo rows that already have hit/miss/push results.",
    )
    parser.add_argument(
        "--stat-source",
        choices=sorted(STAT_SOURCE_MODES),
        default="auto",
        help="Component stat source for combo grading. auto uses local artifacts first, then provider stats for missing dates.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = grade_market_shadow_history(
        runtime_root=args.runtime_root,
        history_root=args.history_root,
        dates=_parse_dates(args.dates),
        start_date=args.start_date,
        end_date=args.end_date,
        dry_run=args.dry_run,
        replace_existing=args.replace_existing,
        stat_source=args.stat_source,
    )
    print(f"market_shadow_history_csv={result['market_shadow_history_path']}")
    print(f"market_readiness_summary_csv={result['market_readiness_summary_path']}")
    print(f"selected_dates={','.join(result['selected_dates'])}")
    print(f"stat_source_mode={result['stat_source_mode']}")
    for source, count in result["stat_source_candidate_rows"].items():
        print(f"stat_source_candidate_rows={source},{count}")
    for date, count in result["provider_rows_by_date"].items():
        print(f"provider_stats_rows={date},{count}")
    for date, error in result["provider_errors_by_date"].items():
        print(f"provider_stats_error={date},{error}")
    print(f"combo_rows={result['combo_rows']}")
    print(f"updated_rows={result['updated_rows']}")
    print(f"would_update_rows={result['would_update_rows']}")
    print(f"skipped_already_graded={result['skipped_already_graded']}")
    for market_type, count in result["updated_by_market_type"].items():
        print(f"updated_by_market_type={market_type},{count}")
    for reason, count in result["skip_reasons"].items():
        print(f"skip_reason={reason},{count}")
    diagnostics = result.get("combo_component_diagnostics", {})
    by_date = diagnostics.get("by_date", {}) if isinstance(diagnostics, dict) else {}
    for date, values in by_date.items():
        print(
            "combo_by_date="
            f"{date},"
            f"combo_rows={int(values.get('combo_rows', 0) or 0)},"
            f"component_stats_available={int(values.get('component_stats_available', 0) or 0)},"
            f"missing_component_stats={int(values.get('missing_component_stats', 0) or 0)},"
            f"component_actual_conflict={int(values.get('component_actual_conflict', 0) or 0)}"
        )
    stat_source_used = diagnostics.get("stat_source_used", {}) if isinstance(diagnostics, dict) else {}
    for source, count in stat_source_used.items():
        print(f"stat_source_used={source},{count}")
    top_missing = diagnostics.get("top_missing_players_dates", []) if isinstance(diagnostics, dict) else []
    if top_missing:
        for item in top_missing:
            missing_components = item.get("missing_components", {})
            if isinstance(missing_components, dict):
                component_text = "|".join(f"{component}:{count}" for component, count in sorted(missing_components.items()))
            else:
                component_text = ""
            print(
                "top_missing_player_date="
                f"{item.get('prediction_date')},"
                f"{item.get('player_name')},"
                f"{item.get('team_abbr')},"
                f"missing_rows={item.get('missing_rows')},"
                f"missing_components={component_text}"
            )
    else:
        print("top_missing_player_date=none")
    if args.dry_run:
        print("dry_run=true")
    print("scope=combo_shadow_only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
