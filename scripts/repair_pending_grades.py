from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime
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


def _today_iso() -> str:
    return datetime.now().date().isoformat()


def _game_not_final(row: pd.Series) -> bool:
    prediction_date = _safe_text(row.get("prediction_date"))
    if prediction_date and prediction_date >= _today_iso():
        return True
    status = _safe_text(row.get("game_status") or row.get("game_status_bucket")).lower()
    return status in {"scheduled", "scheduled_future_iso_status", "not_started", "pre_game", "in_progress", "live"}


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


def _repair_history_df(
    df: pd.DataFrame,
    *,
    start_date: str,
    end_date: str,
    lookup: ActualLookup,
    source_name: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if df.empty:
        return df.copy(), {
            "rows_before": 0,
            "rows_after": 0,
            "removed_fixture_rows": 0,
            "updated_rows": 0,
            "skip_reasons": {},
            "before_pending": 0,
            "after_pending": 0,
            "before_final_missing_actual": 0,
            "after_final_missing_actual": 0,
        }

    working = _ensure_columns(df, ["result_status", "actual_value", "grading_skip_reason", *SAME_OPPONENT_COLUMNS])
    before_pending = _stale_pending_count(working, start_date=start_date, end_date=end_date)
    before_final_missing = _final_missing_actual_count(working)
    fixtures = working.apply(_is_fixture_row, axis=1)
    removed_fixture_rows = int(fixtures.sum())
    working = working.loc[~fixtures].copy()
    scoped = working["prediction_date"].map(lambda value: _date_in_range(value, start_date, end_date))

    updated = 0
    skip_reasons: Counter[str] = Counter()
    for idx, row in working.loc[scoped].iterrows():
        diagnostics = lookup.same_opponent_diagnostics(row)
        for column, value in diagnostics.items():
            working.at[idx, column] = value

        if not _needs_repair(row):
            continue

        result_status, actual_value, reason = lookup.grade_row(row)
        if result_status in FINAL_STATUSES and actual_value is None:
            result_status = VOID_STATUS
            reason = reason or "actual_stats_not_found"
        if result_status == PENDING_STATUS and reason != "game_not_final":
            result_status = VOID_STATUS
            reason = reason or "actual_stats_not_found"

        working.at[idx, "result_status"] = result_status
        working.at[idx, "actual_value"] = actual_value if actual_value is not None else ""
        working.at[idx, "grading_skip_reason"] = "" if result_status in FINAL_STATUSES else reason
        if source_name == "market_shadow_history":
            working.at[idx, "hit"] = result_status == "hit"
            working.at[idx, "miss"] = result_status == "miss"
            working.at[idx, "push"] = result_status == "push"
            working.at[idx, "shadow_roi"] = _shadow_roi(row.get("odds"), result_status)
            eligible, calibration_reason = _calibration_fields(result_status, _selection(row), reason)
            working.at[idx, "calibration_eligible"] = eligible
            working.at[idx, "calibration_exclusion_reason"] = calibration_reason
        elif source_name == "paper_kelly_history":
            profit, roi = _paper_profit_and_roi(row, result_status)
            working.at[idx, "paper_profit"] = profit
            working.at[idx, "paper_roi"] = roi
        updated += 1
        if reason:
            skip_reasons[reason] += 1

    after_pending = _stale_pending_count(working, start_date=start_date, end_date=end_date)
    after_final_missing = _final_missing_actual_count(working)
    return working, {
        "rows_before": int(len(df)),
        "rows_after": int(len(working)),
        "removed_fixture_rows": removed_fixture_rows,
        "updated_rows": int(updated),
        "skip_reasons": dict(sorted(skip_reasons.items())),
        "before_pending": before_pending,
        "after_pending": after_pending,
        "before_final_missing_actual": before_final_missing,
        "after_final_missing_actual": after_final_missing,
        "status_counts": _status_counts(working),
    }


def _paper_summary_rows(history_df: pd.DataFrame) -> dict[str, Any]:
    summary = summarize_paper_kelly_history(history_df)
    return {
        "total": int(summary.get("total", 0) or 0),
        "graded_total": int(summary.get("graded_total", 0) or 0),
        "pending": int(summary.get("pending", 0) or 0),
        "hit_rate": summary.get("hit_rate", ""),
    }


def repair_pending_grades(
    *,
    start_date: str,
    end_date: str,
    history_root: str | Path = "data/history",
    runtime_root: str | Path = "outputs/runtime",
    dry_run: bool = False,
) -> dict[str, Any]:
    history_root_path = Path(history_root)
    runtime_root_path = Path(runtime_root)
    feedback = _read_csv(runtime_root_path / "history" / "result_feedback.csv")
    lookup = ActualLookup(feedback)

    targets = [
        ("pick_history", history_root_path / "pick_history.csv", _read_csv),
        ("market_shadow_history", history_root_path / "market_shadow_history.csv", _read_csv),
        ("paper_kelly_history", history_root_path / "paper_kelly_history.csv", read_paper_kelly_history),
    ]
    results: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "dry_run": dry_run,
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
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair stale pending and missing actual values in history artifacts.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = repair_pending_grades(
        start_date=args.start_date,
        end_date=args.end_date,
        history_root=args.history_root,
        runtime_root=args.runtime_root,
        dry_run=args.dry_run,
    )
    print(f"repair_range={result['start_date']}..{result['end_date']}")
    print(f"dry_run={str(result['dry_run']).lower()}")
    print(f"result_feedback_rows={result['result_feedback_rows']}")
    for name, summary in result["histories"].items():
        print(
            f"{name} rows_before={summary['rows_before']} rows_after={summary['rows_after']} "
            f"removed_fixture_rows={summary['removed_fixture_rows']} updated_rows={summary['updated_rows']} "
            f"pending_before={summary['before_pending']} pending_after={summary['after_pending']} "
            f"final_missing_actual_before={summary['before_final_missing_actual']} "
            f"final_missing_actual_after={summary['after_final_missing_actual']}"
        )
        for reason, count in summary.get("skip_reasons", {}).items():
            print(f"{name} skip_reason={reason},{count}")
    if "paper_kelly_summary" in result:
        summary = result["paper_kelly_summary"]
        print(
            "paper_kelly_summary "
            f"total={summary['total']} graded={summary['graded_total']} "
            f"pending={summary['pending']} hit_rate={summary['hit_rate']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
