from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


TRUE_STRINGS = {"true", "1", "yes", "y"}
GRADED_STATUSES = {"hit", "miss"}
REQUIRED_ARTIFACT_KEYS = (
    "elite_board",
    "full_market_board",
    "quality_summary_json",
    "board_diagnostics",
)


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _safe_int(value: Any, default: int = 0) -> int:
    number = _safe_float(value)
    return default if number is None else int(number)


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_text(value).lower() in TRUE_STRINGS


def _format_num(value: Any, digits: int = 2, *, trim: bool = False) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    text = f"{number:.{digits}f}"
    return text.rstrip("0").rstrip(".") if trim else text


def _format_rate(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    if abs(number) <= 1:
        number *= 100
    return f"{number:.1f}%"


def _read_csv(path: Path, warnings: list[str], *, required: bool = False) -> pd.DataFrame:
    if not path.exists():
        if required:
            warnings.append(f"Missing required CSV: {path}")
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    except Exception as exc:
        warnings.append(f"Could not read CSV {path}: {exc}")
        return pd.DataFrame()


def _read_json(path: Path, warnings: list[str], *, required: bool = False) -> dict[str, Any]:
    if not path.exists():
        if required:
            warnings.append(f"Missing required JSON: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(f"Could not read JSON {path}: {exc}")
        return {}


def _artifact_paths(runtime_root: Path, prediction_date: str) -> dict[str, Path]:
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    return {
        "elite_board": operator / f"elite_board_{prediction_date}.csv",
        "full_market_board": operator / f"full_market_board_{prediction_date}.csv",
        "sgp_board": operator / f"sgp_board_{prediction_date}.csv",
        "kelly_stakes": operator / f"kelly_stakes_{prediction_date}.csv",
        "daily_summary": operator / f"daily_summary_{prediction_date}.txt",
        "quality_summary": operator / f"quality_summary_{prediction_date}.txt",
        "quality_summary_json": operator / f"quality_summary_{prediction_date}.json",
        "operator_card": operator / f"operator_card_{prediction_date}.txt",
        "board_diagnostics": diagnostics / f"board_diagnostics_{prediction_date}.json",
        "market_shadow_report": operator / f"market_shadow_report_{prediction_date}.txt",
        "market_shadow_grading": diagnostics / f"market_shadow_grading_{prediction_date}.json",
        "high_caution_over_watchlist": operator / f"high_caution_over_watchlist_{prediction_date}.csv",
        "combo_under_watchlist": operator / f"combo_under_watchlist_{prediction_date}.csv",
        "same_opponent_under_warnings": operator / f"same_opponent_under_warnings_{prediction_date}.csv",
    }


def _count_bool_column(df: pd.DataFrame, *columns: str) -> int:
    if df.empty:
        return 0
    for column in columns:
        if column in df.columns:
            return int(df[column].map(_is_truthy).sum())
    return 0


def _quality_count(payload: dict[str, Any], key_path: tuple[str, ...], default: int) -> int:
    node: Any = payload
    for key in key_path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return _safe_int(node, default)


def _market_counts(df: pd.DataFrame) -> Counter[str]:
    if df.empty or "market_type" not in df.columns:
        return Counter()
    return Counter(_safe_text(value) or "unknown" for value in df["market_type"])


def _line_value(row: pd.Series) -> Any:
    for column in ("sportsbook_line", "line"):
        if column in row.index and _safe_text(row.get(column)):
            return row.get(column)
    return None


def _edge_value(row: pd.Series) -> Any:
    for column in ("directional_edge", "edge"):
        if column in row.index and _safe_text(row.get(column)):
            return row.get(column)
    return None


def _row_has_any(row: pd.Series, *columns: str) -> bool:
    return any(column in row.index and _is_truthy(row.get(column)) for column in columns)


def _action_value(row: pd.Series) -> str:
    if "recommended_action" in row.index:
        value = _safe_text(row.get("recommended_action"))
        if value and value.lower() not in {"n/a", "na"}:
            return value

    if _row_has_any(row, "review_before_bet"):
        return "REVIEW_BEFORE_BET"
    if _row_has_any(row, "manual_review_required"):
        return "MANUAL_REVIEW_REQUIRED"
    if _row_has_any(row, "same_opponent_under_warning"):
        return "REVIEW_SAME_OPPONENT_WARNING"
    if _row_has_any(
        row,
        "kelly_manual_review_required",
        "kelly_review_required",
        "review_policy_hold",
    ):
        return "KELLY_REVIEW_REQUIRED"

    operator_action = _safe_text(row.get("operator_action") if "operator_action" in row.index else "")
    review_status = _safe_text(row.get("review_status") if "review_status" in row.index else "")
    stake_policy = _safe_text(row.get("stake_policy") if "stake_policy" in row.index else "")
    if (
        operator_action == "DO_NOT_BET_UNTIL_REVIEWED"
        or review_status == "REVIEW_REQUIRED"
        or stake_policy == "HOLD"
    ):
        return "KELLY_REVIEW_REQUIRED"

    return "CLEAR"


def _bucket_value(row: pd.Series) -> str:
    for column in ("bucket", "watchlist_bucket", "context_caution_level", "fragility_bucket", "quality_band"):
        if column in row.index:
            value = _safe_text(row.get(column))
            if value:
                return value
    return "n/a"


def _sort_candidates(df: pd.DataFrame, limit: int) -> pd.DataFrame:
    if df.empty:
        return df
    working = df.copy()
    sort_columns: list[str] = []
    for column in ("quality_score", "confidence"):
        if column in working.columns:
            working[column] = pd.to_numeric(working[column], errors="coerce")
            sort_columns.append(column)
    if "edge" in working.columns:
        working["_abs_edge_for_display"] = pd.to_numeric(working["edge"], errors="coerce").abs()
        sort_columns.append("_abs_edge_for_display")
    if sort_columns:
        working = working.sort_values(sort_columns, ascending=[False] * len(sort_columns), na_position="last")
    return working.head(limit)


def _clip(text: Any, width: int) -> str:
    value = _safe_text(text) or "n/a"
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."


def _render_table(rows: list[dict[str, str]], columns: list[tuple[str, int]]) -> list[str]:
    if not rows:
        return []
    headers = {key: key for key, _width in columns}
    widths = {
        key: min(max(width, len(headers[key]), *(len(_safe_text(row.get(key))) for row in rows)), width)
        for key, width in columns
    }
    header = " ".join(headers[key].ljust(widths[key]) for key, _width in columns)
    separator = " ".join("-" * widths[key] for key, _width in columns)
    lines = [header, separator]
    for row in rows:
        lines.append(" ".join(_clip(row.get(key), widths[key]).ljust(widths[key]) for key, _width in columns))
    return lines


def _pick_rows(df: pd.DataFrame, *, limit: int, include_bucket: bool) -> list[dict[str, str]]:
    display = _sort_candidates(df, limit)
    rows: list[dict[str, str]] = []
    for _idx, row in display.iterrows():
        item = {
            "player": _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name")) or "Unknown",
            "market": _safe_text(row.get("market_type")) or "unknown",
            "sel": _safe_text(row.get("selection")) or "n/a",
            "line": _format_num(_line_value(row), 1, trim=True),
            "odds": _safe_text(row.get("odds") if "odds" in row.index else row.get("american_odds")) or "n/a",
            "edge": _format_num(_edge_value(row), 3),
            "conf": _format_num(row.get("confidence"), 3),
            "qual": _format_num(row.get("quality_score"), 2),
            "action": _action_value(row),
        }
        if include_bucket:
            item["bucket"] = _bucket_value(row)
        rows.append(item)
    return rows


def _render_pick_table(df: pd.DataFrame, *, limit: int, include_bucket: bool = False) -> list[str]:
    columns = [
        ("player", 22),
        ("market", 32),
        ("sel", 5),
        ("line", 6),
        ("odds", 6),
        ("edge", 8),
        ("conf", 6),
        ("qual", 7),
    ]
    if include_bucket:
        columns.append(("bucket", 12))
    columns.append(("action", 28))
    return _render_table(_pick_rows(df, limit=limit, include_bucket=include_bucket), columns)


def _review_match_key(row: pd.Series) -> tuple[str, str, str, str, str]:
    return (
        _safe_text(row.get("prediction_date")),
        _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name")),
        _safe_text(row.get("market_type")),
        _safe_text(row.get("selection")).lower(),
        _format_num(_line_value(row), 3, trim=True),
    )


def _with_kelly_review_fields(board_df: pd.DataFrame, kelly_df: pd.DataFrame) -> pd.DataFrame:
    if board_df.empty or kelly_df.empty:
        return board_df

    review_columns = (
        "recommended_action",
        "review_before_bet",
        "manual_review_required",
        "same_opponent_under_warning",
        "kelly_manual_review_required",
        "review_policy_hold",
        "operator_action",
        "operator_note",
        "review_status",
        "stake_policy",
    )
    lookup: dict[tuple[str, str, str, str, str], pd.Series] = {}
    for _idx, row in kelly_df.iterrows():
        lookup[_review_match_key(row)] = row

    rows: list[dict[str, Any]] = []
    for _idx, row in board_df.iterrows():
        item = row.to_dict()
        source = lookup.get(_review_match_key(row))
        if source is not None:
            for column in review_columns:
                if column not in source.index:
                    continue
                source_value = source.get(column)
                if column in item and _safe_text(item.get(column)):
                    if _is_truthy(item.get(column)) or not _is_truthy(source_value):
                        continue
                if _safe_text(source_value):
                    item[column] = source_value
        rows.append(item)
    return pd.DataFrame(rows, columns=list(dict.fromkeys([*board_df.columns, *review_columns])))


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    if count == 1:
        return singular
    return plural or f"{singular}s"


def _example_lines(df: pd.DataFrame, *, limit: int = 3) -> list[str]:
    if df.empty:
        return []
    rows = _sort_candidates(df, limit)
    lines: list[str] = []
    for _idx, row in rows.iterrows():
        player = _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name")) or "Unknown"
        market = _safe_text(row.get("market_type")) or "unknown"
        selection = _safe_text(row.get("selection")) or "n/a"
        line = _format_num(_line_value(row), 1, trim=True)
        edge = _format_num(_edge_value(row), 3)
        confidence = _format_num(row.get("confidence"), 3)
        reason = ""
        for column in (
            "manual_review_reason",
            "same_opponent_warning_reason",
            "final_elite_rejection_reason",
            "kelly_projected_skip_reason",
            "skip_reason",
            "operator_note",
        ):
            if column in row.index and _safe_text(row.get(column)):
                reason = _safe_text(row.get(column))
                break
        suffix = f", reason={reason}" if reason else ""
        lines.append(f"  - {player}: {market} {selection} {line} (edge={edge}, conf={confidence}{suffix})")
    return lines


def _filter_truthy(df: pd.DataFrame, column: str) -> pd.DataFrame:
    if df.empty or column not in df.columns:
        return pd.DataFrame(columns=list(df.columns))
    return df[df[column].map(_is_truthy)].copy()


def _matchups(df: pd.DataFrame, limit: int = 8) -> list[str]:
    if df.empty:
        return []
    labels: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for _idx, row in df.iterrows():
        team = _safe_text(row.get("team_abbr")) or _safe_text(row.get("team"))
        opponent = _safe_text(row.get("opponent"))
        if not team or not opponent:
            continue
        game_id = _safe_text(row.get("game_id"))
        home_away = _safe_text(row.get("home_away")).lower()
        if home_away == "away":
            label = f"{team} @ {opponent}"
        elif home_away == "home":
            label = f"{opponent} @ {team}"
        else:
            label = f"{team} vs {opponent}"
        key = (game_id, *sorted((team, opponent)))
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
        if len(labels) >= limit:
            break
    return labels


def _provider_status(quality_payload: dict[str, Any], full_market_df: pd.DataFrame) -> tuple[str, bool]:
    slate = quality_payload.get("slate_provider_counts", {})
    if not isinstance(slate, dict):
        slate = {}
    games = _safe_int(slate.get("games_count"), 0)
    normalized = _safe_int(slate.get("normalized_odds_rows_count"), len(full_market_df))
    live = _safe_int(slate.get("live_odds_count"), _count_bool_column(full_market_df, "is_live_market"))
    synthetic = _safe_int(slate.get("synthetic_or_fallback_odds_count"), 0)

    if normalized <= 0 and games > 0:
        return "unsafe_no_odds", True
    if live > 0:
        return f"live ({live} live odds, {synthetic} fallback/synthetic)", False
    if normalized > 0:
        return f"available_non_live ({normalized} normalized odds, {synthetic} fallback/synthetic)", False
    return "unknown", False


def _context_status(path: Path, label: str, payload: dict[str, Any]) -> str:
    if not path.exists():
        return f"{label}: missing"
    if label == "injury":
        normalized = _safe_int(payload.get("normalized_rows"), 0)
        matched = _safe_int(payload.get("candidate_player_matches"), 0)
        return f"injury: available ({normalized} rows, {matched} candidate matches)"
    if label == "game":
        rows = _safe_int(payload.get("rows"), _safe_int(payload.get("total_candidates"), 0))
        suppressed = _safe_int(payload.get("game_context_suppressed_count"), 0)
        stale = _safe_int(payload.get("stale_team_not_in_game_count"), 0)
        return f"game: available ({rows} rows, suppressed={suppressed}, stale_team={stale})"
    return f"{label}: available"


def _history_hit_rates(path: Path, prediction_date: str) -> dict[str, Any]:
    if not path.exists():
        return {"all_time": None, "last_7": None, "graded_count": 0, "source": str(path)}
    try:
        df = pd.read_csv(path, keep_default_na=False)
    except Exception:
        return {"all_time": None, "last_7": None, "graded_count": 0, "source": str(path)}
    if df.empty or "result_status" not in df.columns:
        return {"all_time": None, "last_7": None, "graded_count": 0, "source": str(path)}

    statuses = df["result_status"].map(lambda value: _safe_text(value).lower())
    graded = df[statuses.isin(GRADED_STATUSES)].copy()
    if graded.empty:
        return {"all_time": None, "last_7": None, "graded_count": 0, "source": str(path)}

    def hit_rate(frame: pd.DataFrame) -> float | None:
        if frame.empty:
            return None
        hits = int((frame["result_status"].map(lambda value: _safe_text(value).lower()) == "hit").sum())
        losses = int((frame["result_status"].map(lambda value: _safe_text(value).lower()) == "miss").sum())
        total = hits + losses
        return None if total <= 0 else hits / total

    all_time = hit_rate(graded)
    last_7 = None
    if "prediction_date" in graded.columns:
        dated = graded.copy()
        dated["_date_sort"] = pd.to_datetime(dated["prediction_date"], errors="coerce")
        current_date = pd.to_datetime(prediction_date, errors="coerce")
        if not pd.isna(current_date):
            dated = dated[(dated["_date_sort"].isna()) | (dated["_date_sort"] <= current_date)]
        dates = [value for value in sorted(dated["_date_sort"].dropna().unique())]
        if dates:
            last_dates = set(dates[-7:])
            last_7 = hit_rate(dated[dated["_date_sort"].isin(last_dates)])
    return {
        "all_time": all_time,
        "last_7": last_7,
        "graded_count": int(len(graded)),
        "source": str(path),
    }


def _final_decision(
    *,
    elite_count: int,
    manual_review_count: int,
    review_before_bet_count: int,
    kelly_hold_count: int,
    missing_required: list[str],
    provider_unsafe: bool,
    quality_payload: dict[str, Any],
) -> str:
    run_health = _safe_text(quality_payload.get("run_health_status")).upper()
    date_check = quality_payload.get("date_isolation_check", {})
    date_check_status = _safe_text(date_check.get("status") if isinstance(date_check, dict) else "").lower()
    output_validation_failed = bool(date_check_status and date_check_status != "ok")

    if (
        missing_required
        or provider_unsafe
        or output_validation_failed
        or run_health == "ERROR_OR_INCOMPLETE"
        or run_health.startswith("DEGRADED")
    ):
        return "DEGRADED"
    if elite_count <= 0:
        return "NO BET"
    if manual_review_count > 0 or review_before_bet_count > 0 or kelly_hold_count > 0:
        return "REVIEW REQUIRED"
    return "BETTABLE"


def _files_written_lines(paths: dict[str, Path]) -> list[str]:
    keys = (
        "elite_board",
        "full_market_board",
        "daily_summary",
        "quality_summary",
        "operator_card",
        "board_diagnostics",
        "market_shadow_report",
    )
    lines: list[str] = []
    for key in keys:
        path = paths[key]
        status = "ok" if key == "operator_card" or path.exists() else "missing"
        lines.append(f"- {key}: {path} [{status}]")
    return lines


def build_operator_card(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
) -> tuple[str, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)
    paths = _artifact_paths(runtime_root, prediction_date)
    warnings: list[str] = []

    elite_df = _read_csv(paths["elite_board"], warnings, required=True)
    full_market_df = _read_csv(paths["full_market_board"], warnings, required=True)
    sgp_df = _read_csv(paths["sgp_board"], warnings)
    kelly_df = _read_csv(paths["kelly_stakes"], warnings)
    quality_payload = _read_json(paths["quality_summary_json"], warnings, required=True)
    board_diagnostics = _read_json(paths["board_diagnostics"], warnings, required=True)
    market_shadow_payload = _read_json(paths["market_shadow_grading"], warnings)
    injury_payload = _read_json(runtime_root / "diagnostics" / f"injury_context_diagnostics_{prediction_date}.json", warnings)
    game_payload = _read_json(runtime_root / "diagnostics" / f"game_context_{prediction_date}.json", warnings)
    high_caution_df = _read_csv(paths["high_caution_over_watchlist"], warnings)
    combo_under_df = _read_csv(paths["combo_under_watchlist"], warnings)
    same_opponent_file_df = _read_csv(paths["same_opponent_under_warnings"], warnings)

    missing_required = [
        str(paths[key])
        for key in REQUIRED_ARTIFACT_KEYS
        if not paths[key].exists()
    ]

    funnel = quality_payload.get("candidate_funnel", {})
    if not isinstance(funnel, dict):
        funnel = {}
    kelly_summary = quality_payload.get("kelly_safety_summary", {})
    if not isinstance(kelly_summary, dict):
        kelly_summary = {}

    elite_count = _quality_count(quality_payload, ("candidate_funnel", "elite_board_count"), len(elite_df))
    full_market_count = _quality_count(quality_payload, ("candidate_funnel", "full_market_board_count"), len(full_market_df))
    sgp_count = _quality_count(quality_payload, ("candidate_funnel", "sgp_board_count"), len(sgp_df))
    if elite_count <= 0:
        kelly_rows_count = _quality_count(quality_payload, ("kelly_safety_summary", "total_rows"), 0)
    else:
        kelly_rows_count = _quality_count(quality_payload, ("kelly_safety_summary", "total_rows"), len(kelly_df))
    kelly_eligible_count = _quality_count(
        quality_payload,
        ("kelly_safety_summary", "kelly_eligible_count"),
        _count_bool_column(kelly_df, "kelly_eligible", "eligible") if elite_count > 0 else 0,
    )
    manual_review_count = _quality_count(
        quality_payload,
        ("manual_review_required_count",),
        _count_bool_column(full_market_df, "manual_review_required") + _count_bool_column(elite_df, "manual_review_required"),
    )
    review_before_bet_count = _quality_count(
        quality_payload,
        ("kelly_safety_summary", "review_before_bet_count"),
        _count_bool_column(kelly_df, "review_before_bet"),
    )
    high_caution_count = _quality_count(
        quality_payload,
        ("high_caution_over_watchlist", "row_count"),
        len(high_caution_df),
    )
    combo_under_count = len(combo_under_df)
    same_opponent_warning_count = _quality_count(
        quality_payload,
        ("same_opponent_under_warning_count",),
        _count_bool_column(full_market_df, "same_opponent_under_warning"),
    )
    kelly_hold_count = _quality_count(
        quality_payload,
        ("kelly_safety_summary", "review_policy_hold_count"),
        0,
    )
    if not kelly_df.empty and "operator_action" in kelly_df.columns:
        kelly_hold_count = max(
            kelly_hold_count,
            int(kelly_df["operator_action"].map(lambda value: _safe_text(value) == "DO_NOT_BET_UNTIL_REVIEWED").sum()),
        )

    elite_display_df = _with_kelly_review_fields(elite_df, kelly_df)
    full_market_display_df = _with_kelly_review_fields(full_market_df, kelly_df)
    elite_manual_review_count = _count_bool_column(elite_display_df, "manual_review_required")

    provider_status, provider_unsafe = _provider_status(quality_payload, full_market_df)
    final_decision = _final_decision(
        elite_count=elite_count,
        manual_review_count=manual_review_count,
        review_before_bet_count=review_before_bet_count,
        kelly_hold_count=kelly_hold_count,
        missing_required=missing_required,
        provider_unsafe=provider_unsafe,
        quality_payload=quality_payload,
    )

    slate = quality_payload.get("slate_provider_counts", {})
    if not isinstance(slate, dict):
        slate = {}
    games_count = _safe_int(slate.get("games_count"), 0)
    if games_count <= 0 and "game_id" in full_market_df.columns:
        games_count = int(full_market_df["game_id"].map(_safe_text).replace("", pd.NA).dropna().nunique())
    odds_count = _safe_int(slate.get("normalized_odds_rows_count"), len(full_market_df))
    stale_odds_count = slate.get("stale_odds_count", "n/a")
    run_health_status = _safe_text(quality_payload.get("run_health_status")) or "UNKNOWN"
    run_health_reason = _safe_text(quality_payload.get("run_health_reason")) or "n/a"
    provider_breakdown = slate.get("provider_breakdown", {})
    line_sources = provider_breakdown.get("line_source", {}) if isinstance(provider_breakdown, dict) else {}
    line_source_text = ", ".join(f"{key}={value}" for key, value in sorted(line_sources.items())) if line_sources else "n/a"
    matchup_list = _matchups(full_market_df)

    market_shadow_totals = market_shadow_payload.get("totals", {}) if isinstance(market_shadow_payload, dict) else {}
    if not isinstance(market_shadow_totals, dict):
        market_shadow_totals = {}
    history_rates = _history_hit_rates(history_root / "pick_history.csv", prediction_date)
    shadow_history_rates = _history_hit_rates(history_root / "market_shadow_history.csv", prediction_date)
    all_time_rate = history_rates["all_time"] if history_rates["all_time"] is not None else shadow_history_rates["all_time"]
    last_7_rate = history_rates["last_7"] if history_rates["last_7"] is not None else shadow_history_rates["last_7"]
    graded_count = _safe_int(market_shadow_totals.get("graded_picks"), 0)
    pending_count = _safe_int(market_shadow_totals.get("pending_picks"), 0)
    market_shadow_rows = _safe_int(market_shadow_totals.get("total_picks"), len(full_market_df))
    kelly_performance = market_shadow_payload.get("kelly_decision_performance", {}) if isinstance(market_shadow_payload, dict) else {}
    kelly_performance_status = (
        _safe_text(kelly_performance.get("status")) if isinstance(kelly_performance, dict) else ""
    ) or "n/a"

    board_counts = board_diagnostics.get("board_counts", {}) if isinstance(board_diagnostics, dict) else {}
    board_count_note = ""
    if isinstance(board_counts, dict) and board_counts:
        board_count_note = (
            f"diagnostics qualified_pool={_safe_int(board_counts.get('qualified_pool'), 0)}, "
            f"rejected={_safe_int(board_counts.get('rejected'), 0)}"
        )

    full_manual_df = _filter_truthy(full_market_display_df, "manual_review_required")
    full_same_opponent_df = _filter_truthy(full_market_display_df, "same_opponent_under_warning")
    review_before_bet_df = _filter_truthy(kelly_df, "review_before_bet")
    if same_opponent_file_df.empty:
        same_opponent_examples_df = full_same_opponent_df
    else:
        same_opponent_examples_df = same_opponent_file_df

    lines: list[str] = []
    lines.append("=" * 40)
    lines.append(f"COURTVISION DAILY CARD - {prediction_date}")
    lines.append("=" * 40)
    lines.append(f"prediction_date: {prediction_date}")
    lines.append(f"run_health: {run_health_status} - {run_health_reason}")
    lines.append(f"final_decision: {final_decision}")
    if warnings:
        lines.append(f"report_warnings: {len(warnings)}")
    lines.append("")

    lines.append("Slate Summary")
    lines.append("-" * 40)
    lines.append(f"- games count: {games_count}")
    lines.append(f"- matchups: {', '.join(matchup_list) if matchup_list else 'n/a'}")
    lines.append(f"- provider/live status: {provider_status}")
    lines.append(f"- provider line sources: {line_source_text}")
    lines.append(f"- odds count: {odds_count}")
    lines.append(f"- stale odds count: {_safe_text(stale_odds_count) or 'n/a'}")
    lines.append(f"- {_context_status(runtime_root / 'diagnostics' / f'injury_context_diagnostics_{prediction_date}.json', 'injury', injury_payload)}")
    lines.append(f"- {_context_status(runtime_root / 'diagnostics' / f'game_context_{prediction_date}.json', 'game', game_payload)}")
    lines.append("")

    lines.append("Board Summary")
    lines.append("-" * 40)
    lines.append(f"- elite picks count: {elite_count}")
    lines.append(f"- full market candidates count: {full_market_count}")
    lines.append(f"- SGP candidates count: {sgp_count}")
    lines.append(f"- Kelly rows count: {kelly_rows_count}")
    lines.append(f"- Kelly eligible count: {kelly_eligible_count}")
    lines.append(f"- manual review count: {manual_review_count}")
    lines.append(f"- review_before_bet count: {review_before_bet_count}")
    lines.append(f"- high caution OVER count: {high_caution_count}")
    lines.append(f"- combo UNDER watchlist count: {combo_under_count}")
    lines.append(f"- same-opponent warning count: {same_opponent_warning_count}")
    if board_count_note:
        lines.append(f"- {board_count_note}")
    lines.append("")

    lines.append("Market Breakdown")
    lines.append("-" * 40)
    market_counts = _market_counts(full_market_df)
    if market_counts:
        for market, count in sorted(market_counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"{market}: {count}")
    else:
        lines.append("n/a")
    lines.append("")

    lines.append("Elite Picks")
    lines.append("-" * 40)
    if elite_count > 0 and not elite_display_df.empty:
        lines.extend(_render_pick_table(elite_display_df, limit=25))
    else:
        lines.append("NO ELITE PICKS - all candidates were filtered by safety, quality, context, or exposure gates.")
    lines.append("")

    if elite_count <= 0:
        lines.append("Top Candidate Preview")
        lines.append("-" * 40)
        if full_market_df.empty:
            lines.append("n/a")
        else:
            lines.extend(_render_pick_table(full_market_display_df, limit=10, include_bucket=True))
        lines.append("")

    lines.append("Watchlists")
    lines.append("-" * 40)
    lines.append(f"- high caution OVER: {high_caution_count}")
    lines.extend(_example_lines(high_caution_df, limit=3))
    lines.append(f"- combo UNDER watchlist: {combo_under_count}")
    lines.extend(_example_lines(combo_under_df, limit=3))
    lines.append(f"- same-opponent UNDER warnings: {same_opponent_warning_count}")
    lines.extend(_example_lines(same_opponent_examples_df, limit=3))
    lines.append(f"- manual review required: {manual_review_count}")
    lines.extend(_example_lines(full_manual_df, limit=3))
    lines.append(f"- review_before_bet: {review_before_bet_count}")
    lines.extend(_example_lines(review_before_bet_df, limit=3))
    lines.append("")

    if final_decision == "REVIEW REQUIRED":
        lines.append("Why Review Required?")
        lines.append("-" * 40)
        review_lines: list[str] = []
        if elite_manual_review_count > 0:
            review_lines.append(
                f"- {elite_manual_review_count} elite {_plural(elite_manual_review_count, 'candidate')} "
                f"{'requires' if elite_manual_review_count == 1 else 'require'} manual review."
            )
        elif manual_review_count > 0:
            review_lines.append(
                f"- {manual_review_count} {_plural(manual_review_count, 'candidate')} "
                f"{'requires' if manual_review_count == 1 else 'require'} manual review."
            )
        if review_before_bet_count > 0:
            review_lines.append(
                f"- {review_before_bet_count} {_plural(review_before_bet_count, 'candidate')} "
                f"{'is' if review_before_bet_count == 1 else 'are'} marked review_before_bet."
            )
        if same_opponent_warning_count > 0:
            review_lines.append(
                f"- {same_opponent_warning_count} same-opponent UNDER "
                f"{_plural(same_opponent_warning_count, 'warning')} "
                f"{'is' if same_opponent_warning_count == 1 else 'are'} present."
            )
        if kelly_rows_count > 0 and (
            manual_review_count > 0 or review_before_bet_count > 0 or kelly_hold_count > 0
        ):
            review_lines.append(
                "- Kelly exists, but stake should not be treated as clean until review is complete."
            )
        if not review_lines:
            review_lines.append("- Review flags are present on the board or Kelly artifact.")
        lines.extend(review_lines)
        lines.append("")

    lines.append("Grading Snapshot")
    lines.append("-" * 40)
    lines.append(f"- market shadow rows: {market_shadow_rows}")
    lines.append(f"- graded rows: {graded_count}")
    lines.append(f"- pending grading: {pending_count}")
    lines.append(f"- market shadow hit rate: {_format_rate(market_shadow_totals.get('hit_rate'))}")
    lines.append(f"- all-time hit rate: {_format_rate(all_time_rate)}")
    lines.append(f"- last 7 slates hit rate: {_format_rate(last_7_rate)}")
    lines.append(f"- Kelly performance status: {kelly_performance_status}")
    lines.append("")

    lines.append("Final Decision")
    lines.append("-" * 40)
    if final_decision == "REVIEW REQUIRED":
        lines.append("REVIEW REQUIRED — elite candidates exist, but review flags are present.")
    else:
        lines.append(final_decision)
    lines.append("")

    lines.append("Files Written")
    lines.append("-" * 40)
    lines.extend(_files_written_lines(paths))
    if missing_required:
        lines.append("")
        lines.append("Missing Required Artifacts")
        lines.append("-" * 40)
        lines.extend(f"- {path}" for path in missing_required)

    card_text = "\n".join(lines)
    payload = {
        "prediction_date": prediction_date,
        "final_decision": final_decision,
        "elite_count": elite_count,
        "full_market_count": full_market_count,
        "sgp_count": sgp_count,
        "kelly_rows_count": kelly_rows_count,
        "kelly_eligible_count": kelly_eligible_count,
        "manual_review_count": manual_review_count,
        "review_before_bet_count": review_before_bet_count,
        "high_caution_over_count": high_caution_count,
        "combo_under_watchlist_count": combo_under_count,
        "same_opponent_warning_count": same_opponent_warning_count,
        "provider_status": provider_status,
        "missing_required": missing_required,
        "warnings": warnings,
    }
    return card_text, payload


def write_operator_card_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
) -> tuple[Path, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    paths = _artifact_paths(runtime_root, prediction_date)
    paths["operator_card"].parent.mkdir(parents=True, exist_ok=True)
    text, payload = build_operator_card(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    paths["operator_card"].write_text(text + "\n", encoding="utf-8")
    return paths["operator_card"], payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the CourtVision daily operator card.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    args = parser.parse_args(argv)

    output_path, payload = write_operator_card_outputs(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
    )
    print(f"operator_card_txt={output_path}")
    print(f"operator_card_decision={payload['final_decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
