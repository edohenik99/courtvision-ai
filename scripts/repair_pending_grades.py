from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.paper_kelly_performance import (
    HIT_MISS_STATUSES,
    read_paper_kelly_history,
    summarize_paper_kelly_history,
)
from scripts.history_tracking import update_market_readiness_summary, update_performance_summaries


SUPPORTED_MARKETS = {
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_blocks",
    "player_steals",
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
}
COMBO_MARKET_COMPONENTS = {
    "player_points_rebounds": ("player_points", "player_rebounds"),
    "player_points_assists": ("player_points", "player_assists"),
    "player_rebounds_assists": ("player_rebounds", "player_assists"),
    "player_points_rebounds_assists": ("player_points", "player_rebounds", "player_assists"),
}
MARKET_TYPE_ALIASES = {
    "points": "player_points",
    "rebounds": "player_rebounds",
    "assists": "player_assists",
    "blocks": "player_blocks",
    "steals": "player_steals",
    "points_rebounds": "player_points_rebounds",
    "points_assists": "player_points_assists",
    "rebounds_assists": "player_rebounds_assists",
    "points_rebounds_assists": "player_points_rebounds_assists",
}
FIXTURE_PLAYER_NAMES = {"points star", "rebound star", "high edge", "low edge"}
FINAL_STATUSES = {"hit", "miss", "push"}
PENDING_STATUS = "pending"
VOID_STATUS = "void"
UNSUPPORTED_STATUS = "unsupported"
GAME_NOT_FINAL_REASON = "game_not_final"
PROVIDER_MISSING_FINALITY_REASON = "provider_missing_finality"
OPEN_PENDING_REASONS = {GAME_NOT_FINAL_REASON, PROVIDER_MISSING_FINALITY_REASON}
OPEN_GAME_STATUSES = {"scheduled", "scheduled_future_iso_status", "not_started", "pre_game", "in_progress", "live"}
TERMINAL_GAME_STATUSES = {"final", "final_status", "completed", "complete", "closed", "post_game"}
DIRECT_RESULT_SOURCES = {
    "pick_history",
    "market_shadow_history",
    "paper_kelly_history",
}
SAME_OPPONENT_COLUMNS = [
    "same_opponent_recent_games",
    "same_opponent_last_actual_points",
    "same_opponent_last_line",
    "same_opponent_last_selection",
    "same_opponent_last_result_status",
    "same_opponent_under_warning",
]


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "<na>"}:
        return default
    return text


def _safe_float(value: Any) -> float | None:
    text = _safe_text(value)
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _normalize_market_type(value: Any) -> str:
    text = _safe_text(value).lower().replace(" ", "_")
    return MARKET_TYPE_ALIASES.get(text, text)


def _row_market_type(row: pd.Series) -> str:
    for column in ("market_type", "market", "prop_type", "raw_prop_type"):
        market = _normalize_market_type(row.get(column))
        if market:
            return market
    return ""


def _player_name(row: pd.Series) -> str:
    return (_safe_text(row.get("player_name")) or _safe_text(row.get("entity_name"))).lower()


def _display_player_name(row: pd.Series) -> str:
    return _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name"))


def _team_abbr(row: pd.Series) -> str:
    return (_safe_text(row.get("team_abbr")) or _safe_text(row.get("team"))).upper()


def _opponent(row: pd.Series) -> str:
    return _safe_text(row.get("opponent")).upper()


def _selection(row: pd.Series) -> str:
    return (_safe_text(row.get("selection")) or _safe_text(row.get("side"))).lower()


def _line_value(row: pd.Series) -> float | None:
    for column in ("line", "sportsbook_line", "line_value"):
        value = _safe_float(row.get(column))
        if value is not None:
            return value
    return None


def _line_key(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return _safe_text(value).lower()
    return f"{number:.4f}"


def _status_from_value(value: Any) -> str:
    text = _safe_text(value).lower()
    if text in {"hit", "win", "won", "true", "1"}:
        return "hit"
    if text in {"miss", "loss", "lost", "false", "0"}:
        return "miss"
    if text == "push":
        return "push"
    if text in {"void", "unsupported"}:
        return text
    return PENDING_STATUS


def _reason_tokens(value: Any) -> set[str]:
    return {reason for reason in _safe_text(value).lower().split(";") if reason}


def _row_status(row: pd.Series) -> str:
    for column in ("result_status", "graded_result", "result"):
        if column in row.index:
            status = _status_from_value(row.get(column))
            if status != PENDING_STATUS:
                return status
    if _safe_text(row.get("hit")).lower() in {"true", "1", "yes"}:
        return "hit"
    if _safe_text(row.get("miss")).lower() in {"true", "1", "yes"}:
        return "miss"
    if _safe_text(row.get("push")).lower() in {"true", "1", "yes"}:
        return "push"
    return PENDING_STATUS


def _line_result(selection: str, actual_value: float, line: float) -> str:
    if abs(actual_value - line) < 1e-9:
        return "push"
    if selection == "over":
        return "hit" if actual_value > line else "miss"
    if selection == "under":
        return "hit" if actual_value < line else "miss"
    return PENDING_STATUS


def _american_profit_for_unit_stake(odds: Any) -> float | None:
    american = _safe_float(odds)
    if american is None or american == 0:
        return None
    if american > 0:
        return american / 100.0
    return 100.0 / abs(american)


def _shadow_roi(odds: Any, result_status: str) -> float | str:
    if result_status == "miss":
        return -1.0
    if result_status == "push":
        return 0.0
    if result_status != "hit":
        return ""
    profit = _american_profit_for_unit_stake(odds)
    return round(float(profit), 6) if profit is not None else ""


def _paper_profit_and_roi(row: pd.Series, result_status: str) -> tuple[float | str, float | str]:
    stake = _safe_float(row.get("simulated_stake"))
    if stake is None or stake <= 0 or result_status not in FINAL_STATUSES:
        return "", ""
    if result_status == "push":
        return 0.0, 0.0
    if result_status == "miss":
        return round(-stake, 6), -1.0
    profit = _american_profit_for_unit_stake(row.get("odds"))
    if profit is None:
        return "", ""
    return round(stake * profit, 6), round(profit, 6)


def _calibration_fields(result_status: str, selection: str, reason: str = "") -> tuple[bool, str]:
    reasons: list[str] = []
    if selection not in {"over", "under"}:
        reasons.append(f"unsupported_selection_{selection or 'blank'}")
    if result_status == PENDING_STATUS:
        reasons.append(reason or "no_graded_results")
    elif result_status == "push":
        reasons.append("push_excluded_from_hit_rate")
    elif result_status not in {"hit", "miss"}:
        reasons.append(reason or "ungraded_result")
    return not reasons, ";".join(reasons)


def _is_fixture_row(row: pd.Series) -> bool:
    return _player_name(row) in FIXTURE_PLAYER_NAMES


def _date_in_range(value: Any, start_date: str, end_date: str) -> bool:
    date = _safe_text(value)
    return bool(date) and start_date <= date <= end_date


def _parse_iso_date(value: Any) -> date | None:
    text = _safe_text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _today_iso() -> str:
    return datetime.now().date().isoformat()


def _yesterday_iso() -> str:
    return (datetime.now().date() - timedelta(days=1)).isoformat()


def _game_not_final(row: pd.Series) -> bool:
    prediction_date = _safe_text(row.get("prediction_date"))
    status = _safe_text(row.get("game_status") or row.get("game_status_bucket")).lower()
    if status in TERMINAL_GAME_STATUSES:
        return False
    if status in OPEN_GAME_STATUSES:
        return True
    if prediction_date and prediction_date >= _today_iso():
        return True
    return False


def _actual_status_and_value(row: pd.Series) -> tuple[str, float | None]:
    actual = _safe_float(row.get("actual_value"))
    status = _row_status(row)
    return status, actual


class ActualLookup:
    def __init__(self, feedback: pd.DataFrame) -> None:
        self.feedback = feedback.copy()
        self.direct: dict[tuple[str, str, str, str, str], tuple[str, float]] = {}
        self.direct_no_line: dict[tuple[str, str, str], float] = {}
        self.components: dict[tuple[str, str, str], float] = {}
        self.same_opponent_points = self._build_same_opponent_points()
        self._build()

    def _build(self) -> None:
        if self.feedback.empty:
            return
        for _, row in self.feedback.iterrows():
            prediction_date = _safe_text(row.get("prediction_date"))
            player = _player_name(row)
            market = _row_market_type(row)
            selection = _selection(row)
            line = _line_value(row)
            status, actual = _actual_status_and_value(row)
            if not prediction_date or not player or market not in SUPPORTED_MARKETS or actual is None:
                continue
            if line is not None and selection:
                self.direct[(prediction_date, player, market, selection, _line_key(line))] = (status, actual)
            self.direct_no_line[(prediction_date, player, market)] = actual
            if market in {"player_points", "player_rebounds", "player_assists", "player_blocks", "player_steals"}:
                self.components[(prediction_date, player, market)] = actual

    def _build_same_opponent_points(self) -> pd.DataFrame:
        if self.feedback.empty:
            return pd.DataFrame()
        rows: list[dict[str, Any]] = []
        for _, row in self.feedback.iterrows():
            if _row_market_type(row) != "player_points":
                continue
            actual = _safe_float(row.get("actual_value"))
            if actual is None:
                continue
            rows.append(
                {
                    "prediction_date": _safe_text(row.get("prediction_date")),
                    "player_name": _player_name(row),
                    "opponent": _opponent(row),
                    "actual_value": actual,
                    "line": _line_value(row),
                    "selection": _selection(row),
                    "result_status": _row_status(row),
                }
            )
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).sort_values(["prediction_date"], kind="mergesort").reset_index(drop=True)

    def grade_row(self, row: pd.Series) -> tuple[str, float | None, str]:
        market = _row_market_type(row)
        selection = _selection(row)
        line = _line_value(row)
        prediction_date = _safe_text(row.get("prediction_date"))
        player = _player_name(row)

        if _is_fixture_row(row):
            return VOID_STATUS, None, "fixture_row"
        if market not in SUPPORTED_MARKETS:
            return UNSUPPORTED_STATUS, None, "unsupported_market"
        if selection not in {"over", "under"}:
            return UNSUPPORTED_STATUS, None, "unsupported_selection"
        if line is None:
            return VOID_STATUS, None, "missing_line"
        if not prediction_date or not player:
            return VOID_STATUS, None, "player_stat_match_missing"

        if market in COMBO_MARKET_COMPONENTS:
            values: list[float] = []
            missing: list[str] = []
            for component in COMBO_MARKET_COMPONENTS[market]:
                actual = self.components.get((prediction_date, player, component))
                if actual is None:
                    missing.append(component)
                else:
                    values.append(actual)
            if missing:
                if _game_not_final(row):
                    return PENDING_STATUS, None, "game_not_final"
                return VOID_STATUS, None, "player_stat_match_missing"
            actual_value = float(sum(values))
            return _line_result(selection, actual_value, line), actual_value, ""

        exact = self.direct.get((prediction_date, player, market, selection, _line_key(line)))
        if exact is not None and exact[1] is not None:
            actual_value = float(exact[1])
            return _line_result(selection, actual_value, line), actual_value, ""
        actual = self.direct_no_line.get((prediction_date, player, market))
        if actual is not None:
            actual_value = float(actual)
            return _line_result(selection, actual_value, line), actual_value, ""
        if _game_not_final(row):
            return PENDING_STATUS, None, "game_not_final"
        return VOID_STATUS, None, "player_stat_match_missing" if not self.feedback.empty else "provider_unavailable"

    def same_opponent_diagnostics(self, row: pd.Series) -> dict[str, Any]:
        empty = {
            "same_opponent_recent_games": 0,
            "same_opponent_last_actual_points": "",
            "same_opponent_last_line": "",
            "same_opponent_last_selection": "",
            "same_opponent_last_result_status": "",
            "same_opponent_under_warning": False,
        }
        if _row_market_type(row) != "player_points" or self.same_opponent_points.empty:
            return empty
        prediction_date = _safe_text(row.get("prediction_date"))
        player = _player_name(row)
        opponent = _opponent(row)
        line = _line_value(row)
        if not prediction_date or not player or not opponent:
            return empty
        prior = self.same_opponent_points[
            (self.same_opponent_points["prediction_date"].astype(str) < prediction_date)
            & self.same_opponent_points["player_name"].astype(str).eq(player)
            & self.same_opponent_points["opponent"].astype(str).eq(opponent)
        ].copy()
        if prior.empty:
            return empty
        last = prior.iloc[-1]
        actual = _safe_float(last.get("actual_value"))
        under_warning = bool(_selection(row) == "under" and actual is not None and line is not None and actual > line)
        return {
            "same_opponent_recent_games": int(len(prior)),
            "same_opponent_last_actual_points": actual if actual is not None else "",
            "same_opponent_last_line": last.get("line", ""),
            "same_opponent_last_selection": last.get("selection", ""),
            "same_opponent_last_result_status": last.get("result_status", ""),
            "same_opponent_under_warning": under_warning,
        }


def _ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    working = df.copy()
    for column in columns:
        if column not in working.columns:
            working[column] = ""
    return working


def _set_cell_value(frame: pd.DataFrame, idx: Any, column: str, value: Any) -> None:
    """Assign values safely across object/string/arrow-backed columns."""
    if column not in frame.columns:
        frame.at[idx, column] = value
        return
    dtype = frame[column].dtype
    if pd.api.types.is_string_dtype(dtype):
        frame.at[idx, column] = "" if value is None else str(value)
        return
    frame.at[idx, column] = value


def _needs_repair(row: pd.Series) -> bool:
    status = _row_status(row)
    actual_missing = _safe_float(row.get("actual_value")) is None
    return status == PENDING_STATUS or (status in FINAL_STATUSES and actual_missing)


def _final_missing_actual_count(df: pd.DataFrame) -> int:
    if df.empty or "result_status" not in df.columns or "actual_value" not in df.columns:
        return 0
    status = df["result_status"].map(_status_from_value)
    actual_missing = df["actual_value"].map(lambda value: _safe_float(value) is None)
    return int((status.isin(FINAL_STATUSES) & actual_missing).sum())


def _pending_breakdown(df: pd.DataFrame, *, today: str | None = None) -> dict[str, int]:
    if df.empty or "result_status" not in df.columns:
        return {"total_pending": 0, "open_game_pending": 0, "stale_pending": 0}
    today_text = today or _today_iso()
    pending = df["result_status"].map(_status_from_value).eq(PENDING_STATUS)
    dates = (
        df["prediction_date"].map(_safe_text)
        if "prediction_date" in df.columns
        else pd.Series("", index=df.index)
    )
    reasons = (
        df["grading_skip_reason"].map(lambda value: _safe_text(value).lower())
        if "grading_skip_reason" in df.columns
        else pd.Series("", index=df.index)
    )
    game_statuses = pd.Series("", index=df.index)
    for column in ("game_status", "game_status_bucket"):
        if column in df.columns:
            game_statuses = game_statuses.mask(game_statuses.eq(""), df[column].map(lambda value: _safe_text(value).lower()))

    open_game = pending & (
        reasons.isin(OPEN_PENDING_REASONS)
        | game_statuses.isin(OPEN_GAME_STATUSES)
        | (dates.ge(today_text) & ~game_statuses.isin(TERMINAL_GAME_STATUSES))
    )
    stale = pending & ~open_game
    return {
        "total_pending": int(pending.sum()),
        "open_game_pending": int(open_game.sum()),
        "stale_pending": int(stale.sum()),
    }


def _stale_pending_count(df: pd.DataFrame, *, start_date: str, end_date: str) -> int:
    if df.empty or "result_status" not in df.columns or "prediction_date" not in df.columns:
        return 0
    in_range = df["prediction_date"].map(lambda value: _date_in_range(value, start_date, end_date))
    pending = df["result_status"].map(_status_from_value).eq(PENDING_STATUS)
    return int((in_range & pending).sum())


def _status_counts(df: pd.DataFrame) -> dict[str, int]:
    if df.empty or "result_status" not in df.columns:
        return {}
    return {str(k): int(v) for k, v in df["result_status"].map(_status_from_value).value_counts().items()}


def _empty_history_summary() -> dict[str, Any]:
    return {
        "rows_before": 0,
        "rows_after": 0,
        "removed_fixture_rows": 0,
        "updated_rows": 0,
        "skip_reasons": {},
        "before_pending": 0,
        "after_pending": 0,
        "before_total_pending": 0,
        "before_open_game_pending": 0,
        "before_stale_pending": 0,
        "before_final_missing_actual": 0,
        "after_final_missing_actual": 0,
        "total_pending": 0,
        "open_game_pending": 0,
        "stale_pending": 0,
        "repaired_rows": 0,
        "voided_rows": 0,
        "unsupported_rows": 0,
        "final_missing_actual_rows": 0,
        "pending_taxonomy": {},
        "status_counts": {},
    }


def _repair_history_df(
    df: pd.DataFrame,
    *,
    start_date: str,
    end_date: str,
    lookup: ActualLookup,
    source_name: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if df.empty:
        return df.copy(), _empty_history_summary()

    working = _ensure_columns(df, ["result_status", "actual_value", "grading_skip_reason", *SAME_OPPONENT_COLUMNS])
    before_pending = _stale_pending_count(working, start_date=start_date, end_date=end_date)
    before_breakdown = _pending_breakdown(working)
    before_final_missing = _final_missing_actual_count(working)
    fixtures = working.apply(_is_fixture_row, axis=1)
    removed_fixture_rows = int(fixtures.sum())
    working = working.loc[~fixtures].copy()
    scoped = working["prediction_date"].map(lambda value: _date_in_range(value, start_date, end_date))

    updated = 0
    repaired_rows = 0
    voided_rows = 0
    unsupported_rows = 0
    skip_reasons: Counter[str] = Counter()
    pending_taxonomy: Counter[str] = Counter()
    if removed_fixture_rows:
        pending_taxonomy["fixture_terminal"] += removed_fixture_rows
    for idx, row in working.loc[scoped].iterrows():
        diagnostics = lookup.same_opponent_diagnostics(row)
        for column, value in diagnostics.items():
            _set_cell_value(working, idx, column, value)

        if not _needs_repair(row):
            continue

        old_status = _row_status(row)
        old_values = {
            column: _safe_text(working.at[idx, column])
            for column in (
                "result_status",
                "actual_value",
                "grading_skip_reason",
                "hit",
                "miss",
                "push",
                "shadow_roi",
                "calibration_eligible",
                "calibration_exclusion_reason",
                "paper_profit",
                "paper_roi",
            )
            if column in working.columns
        }
        result_status, actual_value, reason = lookup.grade_row(row)
        old_reason = _safe_text(row.get("grading_skip_reason")).lower()
        if (
            result_status == PENDING_STATUS
            and PROVIDER_MISSING_FINALITY_REASON in (_reason_tokens(old_reason) | _reason_tokens(reason))
            and _game_not_final(row)
        ):
            reason = PROVIDER_MISSING_FINALITY_REASON
        if result_status in FINAL_STATUSES and actual_value is None:
            result_status = VOID_STATUS
            reason = reason or "actual_stats_not_found"
        if result_status == PENDING_STATUS and reason not in OPEN_PENDING_REASONS:
            result_status = VOID_STATUS
            reason = reason or "actual_stats_not_found"

        _set_cell_value(working, idx, "result_status", result_status)
        _set_cell_value(working, idx, "actual_value", actual_value if actual_value is not None else "")
        _set_cell_value(working, idx, "grading_skip_reason", "" if result_status in FINAL_STATUSES else reason)
        if source_name == "market_shadow_history":
            _set_cell_value(working, idx, "hit", result_status == "hit")
            _set_cell_value(working, idx, "miss", result_status == "miss")
            _set_cell_value(working, idx, "push", result_status == "push")
            _set_cell_value(working, idx, "shadow_roi", _shadow_roi(row.get("odds"), result_status))
            eligible, calibration_reason = _calibration_fields(result_status, _selection(row), reason)
            _set_cell_value(working, idx, "calibration_eligible", eligible)
            _set_cell_value(working, idx, "calibration_exclusion_reason", calibration_reason)
        elif source_name == "paper_kelly_history":
            profit, roi = _paper_profit_and_roi(row, result_status)
            _set_cell_value(working, idx, "paper_profit", profit)
            _set_cell_value(working, idx, "paper_roi", roi)
        new_values = {
            column: _safe_text(working.at[idx, column])
            for column in old_values
        }
        if new_values != old_values:
            updated += 1
        if result_status in FINAL_STATUSES:
            repaired_rows += 1
            taxonomy_key = "stale_pending_repaired" if old_status == PENDING_STATUS else "final_missing_actual_repaired"
            pending_taxonomy[taxonomy_key] += 1
        elif result_status == VOID_STATUS:
            voided_rows += 1
            taxonomy_key = "stale_pending_voided" if old_status == PENDING_STATUS else "final_missing_actual_voided"
            pending_taxonomy[taxonomy_key] += 1
        elif result_status == UNSUPPORTED_STATUS:
            unsupported_rows += 1
            pending_taxonomy["unsupported_terminal"] += 1
        elif result_status == PENDING_STATUS and reason in OPEN_PENDING_REASONS:
            pending_taxonomy["pending_open_game"] += 1
        if reason:
            skip_reasons[reason] += 1

    after_pending = _stale_pending_count(working, start_date=start_date, end_date=end_date)
    after_final_missing = _final_missing_actual_count(working)
    after_breakdown = _pending_breakdown(working)
    return working, {
        "rows_before": int(len(df)),
        "rows_after": int(len(working)),
        "removed_fixture_rows": removed_fixture_rows,
        "updated_rows": int(updated),
        "skip_reasons": dict(sorted(skip_reasons.items())),
        "before_pending": before_pending,
        "after_pending": after_pending,
        "before_total_pending": before_breakdown["total_pending"],
        "before_open_game_pending": before_breakdown["open_game_pending"],
        "before_stale_pending": before_breakdown["stale_pending"],
        "before_final_missing_actual": before_final_missing,
        "after_final_missing_actual": after_final_missing,
        "total_pending": after_breakdown["total_pending"],
        "open_game_pending": after_breakdown["open_game_pending"],
        "stale_pending": after_breakdown["stale_pending"],
        "repaired_rows": int(repaired_rows),
        "voided_rows": int(voided_rows),
        "unsupported_rows": int(unsupported_rows),
        "final_missing_actual_rows": after_final_missing,
        "pending_taxonomy": dict(sorted(pending_taxonomy.items())),
        "status_counts": _status_counts(working),
    }


def _paper_summary_rows(history_df: pd.DataFrame) -> dict[str, Any]:
    summary = summarize_paper_kelly_history(history_df)
    return {
        "total": int(summary.get("total", 0) or 0),
        "graded_total": int(summary.get("graded_total", 0) or 0),
        "pending": int(summary.get("pending", 0) or 0),
        "open_pending": int(summary.get("open_pending", 0) or 0),
        "stale_pending": int(summary.get("stale_pending", 0) or 0),
        "hit_rate": summary.get("hit_rate", ""),
    }


def _history_targets(history_root_path: Path) -> list[tuple[str, Path, Any]]:
    return [
        ("pick_history", history_root_path / "pick_history.csv", _read_csv),
        ("market_shadow_history", history_root_path / "market_shadow_history.csv", _read_csv),
        ("paper_kelly_history", history_root_path / "paper_kelly_history.csv", read_paper_kelly_history),
    ]


def _available_history_dates(history_root_path: Path) -> list[str]:
    dates: set[str] = set()
    for _source_name, path, reader in _history_targets(history_root_path):
        df = reader(path)
        if df.empty or "prediction_date" not in df.columns:
            continue
        for value in df["prediction_date"]:
            date = _parse_iso_date(value)
            if date is not None:
                dates.add(date.isoformat())
    return sorted(dates)


def _global_end_date(*, through_date: str | None, include_current_date: bool) -> str:
    max_date = _today_iso() if include_current_date else _yesterday_iso()
    requested = _safe_text(through_date) or max_date
    requested_date = _parse_iso_date(requested)
    max_parsed = _parse_iso_date(max_date)
    if requested_date is None:
        raise ValueError(f"Invalid --through-date value: {through_date}")
    if max_parsed is not None and requested_date > max_parsed:
        return max_date
    return requested_date.isoformat()


def _aggregate_summary(histories: dict[str, Any]) -> dict[str, int]:
    summary_fields = [
        "total_pending",
        "open_game_pending",
        "stale_pending",
        "repaired_rows",
        "voided_rows",
        "unsupported_rows",
        "final_missing_actual_rows",
    ]
    before_fields = {
        "before_total_pending": "before_total_pending",
        "before_open_game_pending": "before_open_game_pending",
        "before_stale_pending": "before_stale_pending",
        "before_final_missing_actual_rows": "before_final_missing_actual",
    }
    aggregate = {field: 0 for field in summary_fields}
    aggregate.update({field: 0 for field in before_fields})
    for history_summary in histories.values():
        for field in summary_fields:
            aggregate[field] += int(history_summary.get(field, 0) or 0)
        for output_field, history_field in before_fields.items():
            aggregate[output_field] += int(history_summary.get(history_field, 0) or 0)
    return aggregate


def _render_pending_repair_report(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    lines = [
        f"Pending Repair Audit - {_today_iso()}",
        "=" * 72,
        f"mode: {result.get('mode', 'date_range')}",
        f"repair_range: {result.get('start_date')}..{result.get('end_date')}",
        f"dry_run: {str(result.get('dry_run', False)).lower()}",
        f"result_feedback_rows: {result.get('result_feedback_rows', 0)}",
        "",
        "Summary",
        "-" * 72,
        f"- total_pending: {summary.get('total_pending', 0)}",
        f"- open_game_pending: {summary.get('open_game_pending', 0)}",
        f"- stale_pending: {summary.get('stale_pending', 0)}",
        f"- repaired_rows: {summary.get('repaired_rows', 0)}",
        f"- voided_rows: {summary.get('voided_rows', 0)}",
        f"- unsupported_rows: {summary.get('unsupported_rows', 0)}",
        f"- final_missing_actual_rows: {summary.get('final_missing_actual_rows', 0)}",
        "",
        "Before",
        "-" * 72,
        f"- total_pending: {summary.get('before_total_pending', 0)}",
        f"- open_game_pending: {summary.get('before_open_game_pending', 0)}",
        f"- stale_pending: {summary.get('before_stale_pending', 0)}",
        f"- final_missing_actual_rows: {summary.get('before_final_missing_actual_rows', 0)}",
        "",
        "Histories",
        "-" * 72,
    ]
    for name, history_summary in result.get("histories", {}).items():
        lines.append(
            f"- {name}: rows_before={history_summary.get('rows_before', 0)}, "
            f"rows_after={history_summary.get('rows_after', 0)}, "
            f"updated_rows={history_summary.get('updated_rows', 0)}, "
            f"total_pending={history_summary.get('total_pending', 0)}, "
            f"open_game_pending={history_summary.get('open_game_pending', 0)}, "
            f"stale_pending={history_summary.get('stale_pending', 0)}, "
            f"final_missing_actual_rows={history_summary.get('final_missing_actual_rows', 0)}"
        )
        taxonomy = history_summary.get("pending_taxonomy", {})
        if taxonomy:
            taxonomy_text = ", ".join(f"{key}={value}" for key, value in sorted(taxonomy.items()))
            lines.append(f"  taxonomy: {taxonomy_text}")
        skip_reasons = history_summary.get("skip_reasons", {})
        if skip_reasons:
            reason_text = ", ".join(f"{key}={value}" for key, value in sorted(skip_reasons.items()))
            lines.append(f"  reasons: {reason_text}")
    return "\n".join(lines) + "\n"


def _write_audit_reports(result: dict[str, Any], runtime_root_path: Path) -> tuple[Path, Path]:
    run_date = _today_iso()
    diagnostics_path = runtime_root_path / "diagnostics" / f"pending_repair_audit_{run_date}.json"
    operator_path = runtime_root_path / "operator" / f"pending_repair_report_{run_date}.txt"
    diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
    operator_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**result, "audit_json_path": str(diagnostics_path), "report_text_path": str(operator_path)}
    diagnostics_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    operator_path.write_text(_render_pending_repair_report(payload), encoding="utf-8")
    return diagnostics_path, operator_path


def repair_pending_grades(
    *,
    start_date: str,
    end_date: str,
    history_root: str | Path = "data/history",
    runtime_root: str | Path = "outputs/runtime",
    dry_run: bool = False,
    mode: str = "date_range",
    through_date: str | None = None,
    include_current_date: bool = False,
    available_dates: list[str] | None = None,
) -> dict[str, Any]:
    history_root_path = Path(history_root)
    runtime_root_path = Path(runtime_root)
    feedback = _read_csv(runtime_root_path / "history" / "result_feedback.csv")
    lookup = ActualLookup(feedback)

    targets = _history_targets(history_root_path)
    results: dict[str, Any] = {
        "mode": mode,
        "start_date": start_date,
        "end_date": end_date,
        "dry_run": dry_run,
        "through_date": through_date,
        "include_current_date": include_current_date,
        "available_dates": available_dates or [],
        "result_feedback_rows": int(len(feedback)),
        "histories": {},
    }
    repaired_frames: dict[str, pd.DataFrame] = {}
    for source_name, path, reader in targets:
        df = reader(path)
        repaired, summary = _repair_history_df(
            df,
            start_date=start_date,
            end_date=end_date,
            lookup=lookup,
            source_name=source_name,
        )
        results["histories"][source_name] = {"path": str(path), **summary}
        repaired_frames[source_name] = repaired
        if not dry_run and path.exists():
            _write_csv(path, repaired)

    if not dry_run:
        update_performance_summaries(history_root=history_root_path, runtime_root=runtime_root_path)
        update_market_readiness_summary(history_root=history_root_path)
        paper_path = history_root_path / "paper_kelly_history.csv"
        if paper_path.exists():
            results["paper_kelly_summary"] = _paper_summary_rows(read_paper_kelly_history(paper_path))
    else:
        results["paper_kelly_summary"] = _paper_summary_rows(repaired_frames["paper_kelly_history"])
    results["summary"] = _aggregate_summary(results["histories"])
    results["audit_report_write_enabled"] = not dry_run
    results["audit_report_write_skipped"] = bool(dry_run)
    if not dry_run:
        audit_json_path, report_text_path = _write_audit_reports(results, runtime_root_path)
        results["audit_json_path"] = str(audit_json_path)
        results["report_text_path"] = str(report_text_path)
    return results


def repair_all_completed_grades(
    *,
    history_root: str | Path = "data/history",
    runtime_root: str | Path = "outputs/runtime",
    through_date: str | None = None,
    include_current_date: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    history_root_path = Path(history_root)
    available_dates = _available_history_dates(history_root_path)
    end_date = _global_end_date(through_date=through_date, include_current_date=include_current_date)
    eligible_dates = [date for date in available_dates if date <= end_date]
    start_date = min(eligible_dates) if eligible_dates else end_date
    return repair_pending_grades(
        start_date=start_date,
        end_date=end_date,
        history_root=history_root,
        runtime_root=runtime_root,
        dry_run=dry_run,
        mode="all_completed",
        through_date=through_date,
        include_current_date=include_current_date,
        available_dates=available_dates,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair stale pending and missing actual values in history artifacts.")
    parser.add_argument("--all-completed", action="store_true", help="Repair every completed historical date found in history CSVs.")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--through-date", help="Last date to include for --all-completed.")
    parser.add_argument("--include-current-date", action="store_true", help="Allow --all-completed to inspect today's rows.")
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.all_completed:
        if args.start_date or args.end_date:
            parser.error("--all-completed cannot be combined with --start-date or --end-date")
    else:
        if args.through_date or args.include_current_date:
            parser.error("--through-date and --include-current-date require --all-completed")
        if not args.start_date or not args.end_date:
            parser.error("--start-date and --end-date are required unless --all-completed is used")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.all_completed:
        result = repair_all_completed_grades(
            history_root=args.history_root,
            runtime_root=args.runtime_root,
            through_date=args.through_date,
            include_current_date=args.include_current_date,
            dry_run=args.dry_run,
        )
    else:
        result = repair_pending_grades(
            start_date=args.start_date,
            end_date=args.end_date,
            history_root=args.history_root,
            runtime_root=args.runtime_root,
            dry_run=args.dry_run,
        )
    print(f"mode={result.get('mode', 'date_range')}")
    print(f"repair_range={result['start_date']}..{result['end_date']}")
    print(f"dry_run={str(result['dry_run']).lower()}")
    print(f"result_feedback_rows={result['result_feedback_rows']}")
    for name, summary in result["histories"].items():
        print(
            f"{name} rows_before={summary['rows_before']} rows_after={summary['rows_after']} "
            f"removed_fixture_rows={summary['removed_fixture_rows']} updated_rows={summary['updated_rows']} "
            f"pending_before={summary['before_pending']} pending_after={summary['after_pending']} "
            f"total_pending={summary['total_pending']} open_game_pending={summary['open_game_pending']} "
            f"stale_pending={summary['stale_pending']} repaired_rows={summary['repaired_rows']} "
            f"voided_rows={summary['voided_rows']} unsupported_rows={summary['unsupported_rows']} "
            f"final_missing_actual_before={summary['before_final_missing_actual']} "
            f"final_missing_actual_after={summary['after_final_missing_actual']}"
        )
        for taxonomy, count in summary.get("pending_taxonomy", {}).items():
            print(f"{name} taxonomy={taxonomy},{count}")
        for reason, count in summary.get("skip_reasons", {}).items():
            print(f"{name} skip_reason={reason},{count}")
    if "summary" in result:
        summary = result["summary"]
        print(
            "pending_repair_summary "
            f"total_pending={summary['total_pending']} open_game_pending={summary['open_game_pending']} "
            f"stale_pending={summary['stale_pending']} repaired_rows={summary['repaired_rows']} "
            f"voided_rows={summary['voided_rows']} unsupported_rows={summary['unsupported_rows']} "
            f"final_missing_actual_rows={summary['final_missing_actual_rows']}"
        )
    if "paper_kelly_summary" in result:
        summary = result["paper_kelly_summary"]
        print(
            "paper_kelly_summary "
            f"total={summary['total']} graded={summary['graded_total']} "
            f"pending={summary['pending']} open_pending={summary['open_pending']} "
            f"stale_pending={summary['stale_pending']} hit_rate={summary['hit_rate']}"
        )
    if result.get("audit_json_path"):
        print(f"audit_json_path={result['audit_json_path']}")
    if result.get("report_text_path"):
        print(f"report_text_path={result['report_text_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
