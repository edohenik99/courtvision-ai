from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


DIAGNOSTIC_COLUMNS: tuple[str, ...] = (
    "same_opponent_recent_games",
    "same_opponent_last_actual_points",
    "same_opponent_last_line",
    "same_opponent_last_selection",
    "same_opponent_last_result_status",
    "same_opponent_under_warning",
    "same_opponent_warning_reason",
    "manual_review_required",
    "manual_review_reason",
)
PLAYER_POINTS_MARKET_ALIASES = {"player_points", "points"}
UNDER_WARNING_REASON = "last_same_opponent_actual_exceeded_current_under_line"
MANUAL_REVIEW_REASON = "same_opponent_under_warning"
FINAL_STATUSES = {"hit", "miss", "push"}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


def _safe_float(value: Any) -> float | None:
    text = _safe_text(value)
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_text(value).lower() in {"true", "1", "yes", "y"}


def _normalize_market_type(value: Any) -> str:
    text = _safe_text(value).lower().replace(" ", "_")
    if text == "points":
        return "player_points"
    return text


def _row_market_type(row: pd.Series) -> str:
    for column in ("market_type", "market", "prop_type", "raw_prop_type"):
        if column in row.index:
            market = _normalize_market_type(row.get(column))
            if market:
                return market
    return ""


def _player_name(row: pd.Series) -> str:
    return (_safe_text(row.get("player_name")) or _safe_text(row.get("entity_name"))).lower()


def _player_id(row: pd.Series) -> str:
    return _safe_text(row.get("player_id")).lower()


def _opponent(row: pd.Series) -> str:
    return _safe_text(row.get("opponent")).upper()


def _selection(row: pd.Series) -> str:
    return (_safe_text(row.get("selection")) or _safe_text(row.get("side"))).lower()


def _line_value(row: pd.Series) -> float | None:
    for column in ("sportsbook_line", "line", "line_value"):
        if column in row.index:
            value = _safe_float(row.get(column))
            if value is not None:
                return value
    return None


def _status_from_row(row: pd.Series) -> str:
    for column in ("result_status", "graded_result", "result"):
        if column in row.index:
            text = _safe_text(row.get(column)).lower()
            if text in {"hit", "win", "won", "true", "1"}:
                return "hit"
            if text in {"miss", "loss", "lost", "false", "0"}:
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


def _read_history_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _append_reason(existing: Any, reason: str) -> str:
    text = _safe_text(existing)
    if not text:
        return reason
    reasons = [item.strip() for item in text.split(";") if item.strip()]
    if reason not in reasons:
        reasons.append(reason)
    return ";".join(reasons)


@dataclass(frozen=True)
class SameOpponentRecord:
    prediction_date: str
    player_id: str
    player_name: str
    opponent: str
    actual_points: float
    line: float | str
    selection: str
    result_status: str
    source_priority: int


class SameOpponentRematchLookup:
    def __init__(self, records: list[SameOpponentRecord]) -> None:
        self.records = sorted(
            records,
            key=lambda row: (row.prediction_date, -row.source_priority),
        )

    @classmethod
    def from_history(cls, history_root: str | Path = "data/history") -> "SameOpponentRematchLookup":
        history_root_path = Path(history_root)
        sources = [
            (history_root_path / "pick_history.csv", 0),
            (history_root_path / "market_shadow_history.csv", 1),
        ]
        records: list[SameOpponentRecord] = []
        seen: set[tuple[str, str, str, str]] = set()
        for path, priority in sources:
            df = _read_history_csv(path)
            if df.empty:
                continue
            source_records: list[SameOpponentRecord] = []
            for _, row in df.iterrows():
                market = _row_market_type(row)
                if market not in PLAYER_POINTS_MARKET_ALIASES:
                    continue
                prediction_date = _safe_text(row.get("prediction_date"))
                actual = _safe_float(row.get("actual_value"))
                result_status = _status_from_row(row)
                player_name = _player_name(row)
                opponent = _opponent(row)
                if (
                    not prediction_date
                    or not player_name
                    or not opponent
                    or actual is None
                    or result_status not in FINAL_STATUSES
                ):
                    continue
                line = _line_value(row)
                source_records.append(
                    SameOpponentRecord(
                        prediction_date=prediction_date,
                        player_id=_player_id(row),
                        player_name=player_name,
                        opponent=opponent,
                        actual_points=float(actual),
                        line=line if line is not None else "",
                        selection=_selection(row),
                        result_status=result_status,
                        source_priority=priority,
                    )
                )
            source_records.sort(key=lambda item: (item.prediction_date, item.source_priority))
            for record in source_records:
                identity = record.player_id or record.player_name
                key = (record.prediction_date, identity, record.player_name, record.opponent)
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)
        return cls(records)

    def prior_same_opponent_points(self, row: pd.Series, *, prediction_date: str) -> list[SameOpponentRecord]:
        player_id = _player_id(row)
        player_name = _player_name(row)
        opponent = _opponent(row)
        if not player_name or not opponent:
            return []
        current_date = _safe_text(row.get("prediction_date")) or prediction_date
        matches: list[SameOpponentRecord] = []
        for record in self.records:
            if record.prediction_date >= current_date:
                continue
            if record.opponent != opponent:
                continue
            if player_id and record.player_id and record.player_id == player_id:
                matches.append(record)
            elif record.player_name == player_name:
                matches.append(record)
        return matches


def annotate_same_opponent_rematches(
    df: pd.DataFrame,
    *,
    prediction_date: str,
    history_root: str | Path = "data/history",
    lookup: SameOpponentRematchLookup | None = None,
) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame(columns=list(DIAGNOSTIC_COLUMNS))

    working = df.copy()
    for column in DIAGNOSTIC_COLUMNS:
        if column not in working.columns:
            working[column] = False if column in {"same_opponent_under_warning", "manual_review_required"} else ""
        working[column] = working[column].astype("object")
    if "same_opponent_recent_games" in working.columns:
        working["same_opponent_recent_games"] = 0
    if working.empty:
        return working

    lookup = lookup or SameOpponentRematchLookup.from_history(history_root)
    for idx, row in working.iterrows():
        working.at[idx, "same_opponent_recent_games"] = 0
        working.at[idx, "same_opponent_last_actual_points"] = ""
        working.at[idx, "same_opponent_last_line"] = ""
        working.at[idx, "same_opponent_last_selection"] = ""
        working.at[idx, "same_opponent_last_result_status"] = ""
        working.at[idx, "same_opponent_under_warning"] = False
        working.at[idx, "same_opponent_warning_reason"] = ""

        if _row_market_type(row) != "player_points":
            working.at[idx, "manual_review_required"] = _truthy(row.get("manual_review_required"))
            continue

        prior = lookup.prior_same_opponent_points(row, prediction_date=prediction_date)
        if not prior:
            working.at[idx, "manual_review_required"] = _truthy(row.get("manual_review_required"))
            continue
        last = prior[-1]
        working.at[idx, "same_opponent_recent_games"] = int(len(prior))
        working.at[idx, "same_opponent_last_actual_points"] = last.actual_points
        working.at[idx, "same_opponent_last_line"] = last.line
        working.at[idx, "same_opponent_last_selection"] = last.selection
        working.at[idx, "same_opponent_last_result_status"] = last.result_status

        current_line = _line_value(row)
        warning = bool(_selection(row) == "under" and current_line is not None and last.actual_points > current_line)
        existing_manual_review = _truthy(row.get("manual_review_required"))
        working.at[idx, "same_opponent_under_warning"] = warning
        if warning:
            working.at[idx, "same_opponent_warning_reason"] = UNDER_WARNING_REASON
            working.at[idx, "manual_review_required"] = True
            working.at[idx, "manual_review_reason"] = _append_reason(row.get("manual_review_reason"), MANUAL_REVIEW_REASON)
        else:
            working.at[idx, "manual_review_required"] = existing_manual_review
    return working


def manual_review_summary(df: pd.DataFrame) -> dict[str, int]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return {
            "same_opponent_under_warning_count": 0,
            "manual_review_required_count": 0,
        }
    same_warning = (
        df["same_opponent_under_warning"].map(_truthy)
        if "same_opponent_under_warning" in df.columns
        else pd.Series(False, index=df.index)
    )
    manual_review = (
        df["manual_review_required"].map(_truthy)
        if "manual_review_required" in df.columns
        else pd.Series(False, index=df.index)
    )
    return {
        "same_opponent_under_warning_count": int(same_warning.sum()),
        "manual_review_required_count": int(manual_review.sum()),
    }


def annotate_operator_board_files(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
) -> dict[str, Any]:
    runtime_root_path = Path(runtime_root)
    operator_dir = runtime_root_path / "operator"
    lookup = SameOpponentRematchLookup.from_history(history_root)
    results: dict[str, Any] = {"prediction_date": prediction_date, "boards": {}}
    for board_name in ("full_market_board", "elite_board"):
        path = operator_dir / f"{board_name}_{prediction_date}.csv"
        if not path.exists() or path.stat().st_size == 0:
            results["boards"][board_name] = {"path": str(path), "exists": False, **manual_review_summary(pd.DataFrame())}
            continue
        try:
            df = pd.read_csv(path, keep_default_na=False, low_memory=False)
        except pd.errors.EmptyDataError:
            results["boards"][board_name] = {"path": str(path), "exists": False, **manual_review_summary(pd.DataFrame())}
            continue
        annotated = annotate_same_opponent_rematches(
            df,
            prediction_date=prediction_date,
            lookup=lookup,
        )
        annotated.to_csv(path, index=False)
        results["boards"][board_name] = {
            "path": str(path),
            "exists": True,
            "rows": int(len(annotated)),
            **manual_review_summary(annotated),
        }
    return results


__all__ = [
    "DIAGNOSTIC_COLUMNS",
    "MANUAL_REVIEW_REASON",
    "SameOpponentRematchLookup",
    "UNDER_WARNING_REASON",
    "annotate_operator_board_files",
    "annotate_same_opponent_rematches",
    "manual_review_summary",
]
