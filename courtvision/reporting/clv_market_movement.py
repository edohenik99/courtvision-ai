"""CLV and market movement shadow report.

Report-only diagnostics for comparing entry lines to observed close lines.
Nothing in this module feeds prediction logic, Elite gates, Kelly sizing, or
board generation.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from courtvision.market_intelligence.market_snapshots import market_snapshot_key


REPORT_VERSION = "1.0"
DIAGNOSTIC_ONLY_NOTE = "CLV is diagnostic only and is not an Elite/Kelly input."

REPORT_COLUMNS: tuple[str, ...] = (
    "market_snapshot_key",
    "prediction_date",
    "game_id",
    "player_id",
    "player_name",
    "team",
    "opponent",
    "market_type",
    "selection",
    "entry_line",
    "entry_odds",
    "opening_line_observed",
    "closing_line_observed",
    "close_source",
    "close_coverage_status",
    "line_move_points",
    "movement_toward_pick",
    "clv_line_points",
    "clv_odds_delta",
    "clv_grade",
    "clv_confidence",
)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "none", "null", "<na>"}


def _safe_text(value: Any, default: str = "") -> str:
    if _is_missing(value):
        return default
    return str(value).strip()


def _safe_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _get_first(row: Mapping[str, Any] | pd.Series, *columns: str) -> Any:
    for column in columns:
        try:
            value = row.get(column)
        except AttributeError:
            value = None
        if not _is_missing(value):
            return value
    return None


def _entry_line(row: Mapping[str, Any] | pd.Series) -> float | None:
    return _safe_float(_get_first(row, "entry_line", "line", "sportsbook_line", "line_value"))


def _entry_odds(row: Mapping[str, Any] | pd.Series) -> float | None:
    return _safe_float(_get_first(row, "entry_odds", "odds", "american_odds"))


def _opening_line(row: Mapping[str, Any] | pd.Series) -> float | None:
    return _safe_float(_get_first(row, "opening_line_observed", "opening_line", "open_line"))


def _closing_line(row: Mapping[str, Any] | pd.Series) -> float | None:
    return _safe_float(
        _get_first(
            row,
            "closing_line_observed",
            "closing_line",
            "close_line",
            "market_close_line",
        )
    )


def _closing_odds(row: Mapping[str, Any] | pd.Series) -> float | None:
    return _safe_float(
        _get_first(row, "closing_odds_observed", "closing_odds", "close_odds", "market_close_odds")
    )


def _close_source(row: Mapping[str, Any] | pd.Series) -> str:
    return _safe_text(
        _get_first(row, "close_source", "closing_line_source", "closing_source", "source"),
        default="",
    )


def _pick_clv_points(selection: str, entry_line: float | None, closing_line: float | None) -> float | None:
    if entry_line is None or closing_line is None:
        return None
    selection_norm = selection.strip().lower()
    if selection_norm == "over":
        return round(closing_line - entry_line, 4)
    if selection_norm == "under":
        return round(entry_line - closing_line, 4)
    return None


def _movement_toward_pick(selection: str, line_move_points: float | None) -> bool | None:
    if line_move_points is None or abs(line_move_points) < 1e-9:
        return None
    selection_norm = selection.strip().lower()
    if selection_norm == "over":
        return line_move_points > 0
    if selection_norm == "under":
        return line_move_points < 0
    return None


def _clv_grade(clv_line_points: float | None) -> str:
    if clv_line_points is None:
        return "missing"
    if clv_line_points > 0:
        return "positive"
    if clv_line_points < 0:
        return "negative"
    return "neutral"


def _clv_confidence(closing_line: float | None, close_source: str) -> float:
    if closing_line is None:
        return 0.0
    if close_source:
        return 1.0
    return 0.75


def _round_or_none(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _close_lookup(close_snapshots_df: pd.DataFrame | None, prediction_date: str) -> dict[str, pd.Series]:
    if close_snapshots_df is None or close_snapshots_df.empty:
        return {}
    lookup: dict[str, pd.Series] = {}
    for _idx, row in close_snapshots_df.iterrows():
        key = market_snapshot_key(row, prediction_date=prediction_date)
        lookup[key] = row
    return lookup


def build_clv_market_movement_report(
    entry_df: pd.DataFrame,
    *,
    prediction_date: str,
    close_snapshots_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build report-only CLV and market movement diagnostics."""
    close_by_key = _close_lookup(close_snapshots_df, prediction_date)
    rows: list[dict[str, Any]] = []

    if entry_df is None or entry_df.empty:
        entry_df = pd.DataFrame()

    for _idx, entry_row in entry_df.iterrows():
        key = market_snapshot_key(entry_row, prediction_date=prediction_date)
        close_row = close_by_key.get(key)

        entry_line = _entry_line(entry_row)
        entry_odds = _entry_odds(entry_row)
        opening_line = _opening_line(close_row) if close_row is not None else _opening_line(entry_row)
        closing_line = _closing_line(close_row) if close_row is not None else _closing_line(entry_row)
        closing_odds = _closing_odds(close_row) if close_row is not None else _closing_odds(entry_row)
        close_source = _close_source(close_row) if close_row is not None else _close_source(entry_row)
        close_coverage_status = "observed" if closing_line is not None else "missing"

        line_move_points = (
            round(closing_line - opening_line, 4)
            if opening_line is not None and closing_line is not None
            else None
        )
        selection = _safe_text(_get_first(entry_row, "selection", "side")).lower()
        clv_line_points = _pick_clv_points(selection, entry_line, closing_line)
        clv_odds_delta = (
            round(closing_odds - entry_odds, 4)
            if closing_odds is not None and entry_odds is not None
            else None
        )
        movement_toward_pick = _movement_toward_pick(selection, line_move_points)

        rows.append(
            {
                "market_snapshot_key": key,
                "prediction_date": _safe_text(
                    _get_first(entry_row, "prediction_date"),
                    default=prediction_date,
                ),
                "game_id": _safe_text(_get_first(entry_row, "game_id", "event_id")),
                "player_id": _safe_text(_get_first(entry_row, "player_id", "entity_id")),
                "player_name": _safe_text(_get_first(entry_row, "player_name", "entity_name", "name")),
                "team": _safe_text(_get_first(entry_row, "team", "team_abbr")).upper(),
                "opponent": _safe_text(_get_first(entry_row, "opponent", "opponent_abbr")).upper(),
                "market_type": _safe_text(
                    _get_first(entry_row, "market_type", "market", "prop_type", "raw_prop_type")
                ),
                "selection": selection,
                "entry_line": _round_or_none(entry_line),
                "entry_odds": _round_or_none(entry_odds),
                "opening_line_observed": _round_or_none(opening_line),
                "closing_line_observed": _round_or_none(closing_line),
                "close_source": close_source,
                "close_coverage_status": close_coverage_status,
                "line_move_points": _round_or_none(line_move_points),
                "movement_toward_pick": movement_toward_pick,
                "clv_line_points": _round_or_none(clv_line_points),
                "clv_odds_delta": _round_or_none(clv_odds_delta),
                "clv_grade": _clv_grade(clv_line_points),
                "clv_confidence": _clv_confidence(closing_line, close_source),
            }
        )

    close_observed = [row for row in rows if row["close_coverage_status"] == "observed"]
    positive_clv = [row for row in close_observed if (row.get("clv_line_points") or 0) > 0]
    movement_toward = [
        row for row in close_observed
        if row.get("movement_toward_pick") is True
    ]
    movement_away = [
        row for row in close_observed
        if row.get("movement_toward_pick") is False
    ]
    total_rows = len(rows)
    close_count = len(close_observed)

    return {
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "scope": "clv_market_movement_shadow",
        "notes": [
            "shadow_report_only",
            "no_prediction_logic_changed",
            "no_elite_gates_changed",
            "no_kelly_sizing_changed",
            DIAGNOSTIC_ONLY_NOTE,
        ],
        "summary": {
            "total_rows": total_rows,
            "close_coverage_count": close_count,
            "close_coverage_rate": round(close_count / total_rows, 4) if total_rows else 0.0,
            "positive_clv_count": len(positive_clv),
            "positive_clv_rate": round(len(positive_clv) / close_count, 4) if close_count else 0.0,
            "movement_toward_pick_count": len(movement_toward),
            "movement_away_from_pick_count": len(movement_away),
            "missing_close_line_count": total_rows - close_count,
            "missing_close_line_rate": round((total_rows - close_count) / total_rows, 4) if total_rows else 0.0,
        },
        "rows": rows,
    }


def clv_market_movement_json_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"clv_market_movement_{date}.json"


def clv_market_movement_txt_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"clv_market_movement_{date}.txt"


def render_clv_market_movement_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    rows = payload.get("rows", [])
    if not isinstance(summary, dict):
        summary = {}
    if not isinstance(rows, list):
        rows = []

    lines = [
        "CLV / Market Movement - Shadow Only",
        f"prediction_date: {payload.get('prediction_date', '')}",
        "=" * 72,
        f"close coverage count: {summary.get('close_coverage_count', 0)} / {summary.get('total_rows', 0)}",
        (
            "positive CLV count/rate: "
            f"{summary.get('positive_clv_count', 0)} / {summary.get('positive_clv_rate', 0.0):.1%}"
        ),
        f"movement toward pick count: {summary.get('movement_toward_pick_count', 0)}",
        f"movement away from pick count: {summary.get('movement_away_from_pick_count', 0)}",
        f"missing close-line count: {summary.get('missing_close_line_count', 0)}",
        DIAGNOSTIC_ONLY_NOTE,
        "",
        "Rows",
        "-" * 72,
    ]
    if not rows:
        lines.append("n/a")
    else:
        lines.append(
            "player | market | selection | entry | close | clv_points | move | grade | source"
        )
        for row in rows[:50]:
            lines.append(
                " | ".join(
                    [
                        _safe_text(row.get("player_name"), "Unknown"),
                        _safe_text(row.get("market_type"), "unknown"),
                        _safe_text(row.get("selection"), "n/a"),
                        str(row.get("entry_line") if row.get("entry_line") is not None else "n/a"),
                        str(
                            row.get("closing_line_observed")
                            if row.get("closing_line_observed") is not None
                            else "missing"
                        ),
                        str(row.get("clv_line_points") if row.get("clv_line_points") is not None else "n/a"),
                        str(row.get("movement_toward_pick") if row.get("movement_toward_pick") is not None else "neutral"),
                        _safe_text(row.get("clv_grade"), "missing"),
                        _safe_text(row.get("close_source"), "n/a"),
                    ]
                )
            )
        if len(rows) > 50:
            lines.append(f"... {len(rows) - 50} additional rows omitted")
    return "\n".join(lines) + "\n"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def write_clv_market_movement_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    entry_df: pd.DataFrame | None = None,
    close_snapshots_df: pd.DataFrame | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Write JSON and operator TXT CLV/movement diagnostics.

    Defaults to reading the full-market board as the entry surface. Closing
    snapshots are optional; missing close lines are represented as missing
    coverage, not as failures.
    """
    runtime_root = Path(runtime_root)
    if entry_df is None:
        entry_df = _read_csv(runtime_root / "operator" / f"full_market_board_{prediction_date}.csv")

    payload = build_clv_market_movement_report(
        entry_df,
        prediction_date=prediction_date,
        close_snapshots_df=close_snapshots_df,
    )
    json_path = clv_market_movement_json_path_for_date(prediction_date, runtime_root)
    txt_path = clv_market_movement_txt_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    txt_path.write_text(render_clv_market_movement_report(payload), encoding="utf-8")
    return json_path, txt_path, payload


__all__ = [
    "DIAGNOSTIC_ONLY_NOTE",
    "REPORT_COLUMNS",
    "REPORT_VERSION",
    "build_clv_market_movement_report",
    "clv_market_movement_json_path_for_date",
    "clv_market_movement_txt_path_for_date",
    "render_clv_market_movement_report",
    "write_clv_market_movement_report",
]
