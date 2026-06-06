"""Phase 6F research-only odds improvement tracker.

This entrypoint compares quality-gated research rows with the full market
board. It does not create picks, MarketProp rows, Elite rows, Kelly inputs,
or operator betting boards.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ODDS_IMPROVEMENT_TRACKER_OK = "ODDS_IMPROVEMENT_TRACKER_OK"
ODDS_IMPROVEMENT_TRACKER_INPUT_MISSING = (
    "ODDS_IMPROVEMENT_TRACKER_INPUT_MISSING"
)
ODDS_IMPROVEMENT_TRACKER_SCHEMA_INVALID = (
    "ODDS_IMPROVEMENT_TRACKER_SCHEMA_INVALID"
)
ODDS_IMPROVEMENT_TRACKER_NO_OUTPUT_ROWS = (
    "ODDS_IMPROVEMENT_TRACKER_NO_OUTPUT_ROWS"
)

DEFAULT_OUTPUT_DIR = Path("outputs/runtime/research")
DEFAULT_DIAGNOSTICS_DIR = Path("outputs/runtime/diagnostics")

FAIR_PRICE_NOW = "fair_price_now"
BETTER_LINE_NOW = "better_line_now"
MONITOR_FOR_PRICE_DROP = "monitor_for_price_drop"
LOW_PRIORITY_MONITOR = "low_priority_monitor"
NO_IMPROVEMENT_AVAILABLE = "no_improvement_available"

FAVORABLE_OR_FAIR = "favorable_or_fair"
HEAVY_JUICE = "heavy_juice"
MISSING_ODDS = "missing_odds"
MIN_RESEARCH_EDGE = 1.5

BETTING_APPROVAL_STATUS = "research_only_not_betting_approved"
SUMMARY_NOTICE = (
    "Research-only odds improvement tracker. Not betting-approved. "
    "No picks created."
)

QUALITY_REQUIRED_COLUMNS = [
    "player_name",
    "market_type",
    "side",
    "line",
    "projection_value",
    "side_adjusted_edge",
    "american_odds",
    "research_category",
    "projection_confidence_tier",
    "odds_quality_flag",
    "line_quality_flag",
    "minutes_quality_flag",
]
MARKET_REQUIRED_COLUMNS = [
    "player_name",
    "market_type",
    "side",
    "line",
    "american_odds",
    "sportsbook",
]
TRACKING_COLUMNS = [
    "best_available_sportsbook",
    "best_available_line",
    "best_available_american_odds",
    "best_available_odds_quality_flag",
    "best_available_side_adjusted_edge",
    "same_line_fair_price_available",
    "better_line_available",
    "better_price_available",
    "improvement_category",
    "improvement_note",
    "eligible_for_betting",
    "betting_approval_status",
]


@dataclass(slots=True)
class OddsImprovementTrackerResult:
    status: str
    output_path: Path
    summary_path: Path
    diagnostics_path: Path
    diagnostics: dict[str, Any]


def run_odds_improvement_tracker(
    *,
    target_date: str,
    quality_board: str | Path | None = None,
    market_board: str | Path | None = None,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    diagnostics_dir: str | Path = DEFAULT_DIAGNOSTICS_DIR,
) -> OddsImprovementTrackerResult:
    """Compare research-board rows with currently available market offers."""
    target_date_text = _validate_date(target_date)
    output_dir_path = Path(output_dir)
    diagnostics_dir_path = Path(diagnostics_dir)
    quality_board_path = Path(quality_board) if quality_board else (
        output_dir_path
        / f"quality_gated_research_board_{target_date_text}.csv"
    )
    market_board_path = Path(market_board) if market_board else (
        output_dir_path / f"market_validation_board_{target_date_text}.csv"
    )
    output_path = (
        output_dir_path
        / f"odds_improvement_tracker_{target_date_text}.csv"
    )
    summary_path = (
        output_dir_path
        / f"odds_improvement_tracker_summary_{target_date_text}.txt"
    )
    diagnostics_path = (
        diagnostics_dir_path
        / f"odds_improvement_tracker_{target_date_text}.json"
    )

    output_dir_path.mkdir(parents=True, exist_ok=True)
    diagnostics_dir_path.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    quality_df = pd.DataFrame()
    market_df = pd.DataFrame()
    missing_inputs = [
        str(path)
        for path in (quality_board_path, market_board_path)
        if not path.exists()
    ]
    quality_read_error = ""
    market_read_error = ""

    if quality_board_path.exists():
        quality_df, quality_read_error = _read_csv(
            quality_board_path,
            "quality-gated research board",
        )
    if market_board_path.exists():
        market_df, market_read_error = _read_csv(
            market_board_path,
            "market validation board",
        )

    schema_missing_columns = {
        "quality_board": _missing_columns(
            quality_df,
            QUALITY_REQUIRED_COLUMNS,
        ),
        "market_board": _missing_columns(
            market_df,
            MARKET_REQUIRED_COLUMNS,
        ),
    }
    warnings.extend(
        error
        for error in (quality_read_error, market_read_error)
        if error
    )
    warnings.extend(f"Input not found: {path}" for path in missing_inputs)
    for label, columns in schema_missing_columns.items():
        if columns:
            warnings.append(
                f"{label} is missing required columns: "
                + ", ".join(columns)
            )

    if missing_inputs:
        status = ODDS_IMPROVEMENT_TRACKER_INPUT_MISSING
        tracker = _empty_tracker(quality_df.columns.tolist())
    elif (
        quality_read_error
        or market_read_error
        or any(schema_missing_columns.values())
    ):
        status = ODDS_IMPROVEMENT_TRACKER_SCHEMA_INVALID
        tracker = _empty_tracker(quality_df.columns.tolist())
    else:
        tracker = _build_tracker(quality_df, market_df)
        status = (
            ODDS_IMPROVEMENT_TRACKER_OK
            if not tracker.empty
            else ODDS_IMPROVEMENT_TRACKER_NO_OUTPUT_ROWS
        )

    diagnostics = _diagnostics_payload(
        target_date=target_date_text,
        status=status,
        quality_board_path=quality_board_path,
        market_board_path=market_board_path,
        quality_df=quality_df,
        market_df=market_df,
        tracker=tracker,
        schema_missing_columns=schema_missing_columns,
        warnings=warnings,
        output_path=output_path,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
    )
    _write_outputs(
        output_path,
        summary_path,
        diagnostics_path,
        tracker,
        diagnostics,
    )
    return OddsImprovementTrackerResult(
        status=status,
        output_path=output_path,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
        diagnostics=diagnostics,
    )


def _build_tracker(
    quality_df: pd.DataFrame,
    market_df: pd.DataFrame,
) -> pd.DataFrame:
    if quality_df.empty:
        return _empty_tracker(quality_df.columns.tolist())

    market_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for position, (_, row) in enumerate(market_df.iterrows()):
        key = _match_key(row)
        market_groups.setdefault(key, []).append(
            {
                "sportsbook": _clean_text(row.get("sportsbook")),
                "line": _coerce_number(row.get("line")),
                "american_odds": _coerce_number(
                    row.get("american_odds")
                ),
                "position": position,
            }
        )

    output_rows: list[dict[str, Any]] = []
    source_columns = quality_df.columns.tolist()
    for _, row in quality_df.iterrows():
        source = row.to_dict()
        projection = _coerce_number(row.get("projection_value"))
        source_line = _coerce_number(row.get("line"))
        source_edge = _coerce_number(row.get("side_adjusted_edge"))
        source_odds = _coerce_number(row.get("american_odds"))
        side = _clean_text(row.get("side")).lower()
        candidates = _prepare_candidates(
            market_groups.get(_match_key(row), []),
            side=side,
            projection=projection,
        )

        fair_candidates = [
            candidate
            for candidate in candidates
            if candidate["odds_quality_flag"] == FAVORABLE_OR_FAIR
            and _edge_meets_threshold(candidate["side_adjusted_edge"])
        ]
        better_line_candidates = [
            candidate
            for candidate in candidates
            if _line_improves(
                side=side,
                source_line=source_line,
                available_line=candidate["line"],
            )
            and _edge_improves(
                source_edge,
                candidate["side_adjusted_edge"],
            )
            and _edge_meets_threshold(candidate["side_adjusted_edge"])
        ]
        same_line_fair_price_available = any(
            _same_number(candidate["line"], source_line)
            and candidate["odds_quality_flag"] == FAVORABLE_OR_FAIR
            and _edge_meets_threshold(candidate["side_adjusted_edge"])
            for candidate in candidates
        )
        better_price_available = any(
            _same_number(candidate["line"], source_line)
            and source_odds is not None
            and candidate["american_odds"] is not None
            and candidate["american_odds"] > source_odds
            for candidate in candidates
        )

        low_priority = _is_low_priority(row, source_edge)
        all_prices_heavy = _all_available_prices_heavy(candidates)
        if low_priority:
            category = LOW_PRIORITY_MONITOR
            best = _best_candidate(candidates)
            note = (
                "Low-confidence, unstable-minutes, or weak-edge research "
                "row. Monitor only."
            )
        elif fair_candidates:
            category = FAIR_PRICE_NOW
            best = _best_candidate(fair_candidates)
            note = _available_note(
                "Fair-or-favorable price now",
                best,
            )
        elif better_line_candidates:
            category = BETTER_LINE_NOW
            best = _best_candidate(better_line_candidates)
            note = _available_note("Better line now", best)
        elif _edge_meets_threshold(source_edge) and all_prices_heavy:
            category = MONITOR_FOR_PRICE_DROP
            best = _best_candidate(candidates)
            note = (
                "Research edge remains, but all available prices are heavy "
                "juice. Monitor for a price drop."
            )
        else:
            category = NO_IMPROVEMENT_AVAILABLE
            best = _best_candidate(candidates)
            note = (
                "No matching market offers were found."
                if not candidates
                else "No better fair price or qualifying line is available."
            )

        source.update(
            {
                "best_available_sportsbook": (
                    best["sportsbook"] if best else pd.NA
                ),
                "best_available_line": (
                    best["line"] if best else pd.NA
                ),
                "best_available_american_odds": (
                    best["american_odds"] if best else pd.NA
                ),
                "best_available_odds_quality_flag": (
                    best["odds_quality_flag"] if best else MISSING_ODDS
                ),
                "best_available_side_adjusted_edge": (
                    best["side_adjusted_edge"] if best else pd.NA
                ),
                "same_line_fair_price_available": bool(
                    same_line_fair_price_available
                ),
                "better_line_available": bool(better_line_candidates),
                "better_price_available": bool(better_price_available),
                "improvement_category": category,
                "improvement_note": note,
                "eligible_for_betting": False,
                "betting_approval_status": BETTING_APPROVAL_STATUS,
            }
        )
        output_rows.append(source)

    output_columns = [
        *[
            column
            for column in source_columns
            if column not in TRACKING_COLUMNS
        ],
        *TRACKING_COLUMNS,
    ]
    return pd.DataFrame(output_rows).loc[:, output_columns]


def _prepare_candidates(
    raw_candidates: list[dict[str, Any]],
    *,
    side: str,
    projection: float | None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for candidate in raw_candidates:
        line = candidate["line"]
        if line is None:
            continue
        odds = candidate["american_odds"]
        candidates.append(
            {
                **candidate,
                "odds_quality_flag": _odds_quality_flag(odds),
                "side_adjusted_edge": _side_adjusted_edge(
                    side=side,
                    projection=projection,
                    available_line=line,
                ),
            }
        )
    return candidates


def _best_candidate(
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda candidate: (
            _sort_number(candidate["side_adjusted_edge"]),
            _sort_number(candidate["american_odds"]),
            -candidate["position"],
        ),
    )


def _side_adjusted_edge(
    *,
    side: str,
    projection: float | None,
    available_line: float | None,
) -> float | None:
    if projection is None or available_line is None:
        return None
    if side == "over":
        return projection - available_line
    if side == "under":
        return available_line - projection
    return None


def _line_improves(
    *,
    side: str,
    source_line: float | None,
    available_line: float | None,
) -> bool:
    if source_line is None or available_line is None:
        return False
    if side == "over":
        return available_line < source_line
    if side == "under":
        return available_line > source_line
    return False


def _edge_improves(
    source_edge: float | None,
    available_edge: float | None,
) -> bool:
    return (
        source_edge is not None
        and available_edge is not None
        and available_edge > source_edge
    )


def _edge_meets_threshold(edge: float | None) -> bool:
    return edge is not None and edge >= MIN_RESEARCH_EDGE


def _is_low_priority(row: pd.Series, source_edge: float | None) -> bool:
    category = _clean_text(row.get("research_category")).lower()
    confidence = _clean_text(
        row.get("projection_confidence_tier")
    ).lower()
    minutes = _clean_text(row.get("minutes_quality_flag")).lower()
    line_quality = _clean_text(row.get("line_quality_flag")).lower()
    return (
        category == "low_confidence_review"
        or confidence in {"low_research_confidence", "manual_review_only"}
        or minutes != "stable_minutes"
        or line_quality in {"weak_edge_line", "missing_edge"}
        or not _edge_meets_threshold(source_edge)
    )


def _all_available_prices_heavy(
    candidates: list[dict[str, Any]],
) -> bool:
    priced_candidates = [
        candidate
        for candidate in candidates
        if candidate["american_odds"] is not None
    ]
    return bool(priced_candidates) and all(
        candidate["odds_quality_flag"] == HEAVY_JUICE
        for candidate in priced_candidates
    )


def _odds_quality_flag(american_odds: float | None) -> str:
    if american_odds is None:
        return MISSING_ODDS
    return (
        FAVORABLE_OR_FAIR
        if american_odds >= -150
        else HEAVY_JUICE
    )


def _available_note(prefix: str, candidate: dict[str, Any]) -> str:
    odds = candidate["american_odds"]
    odds_text = "missing odds" if odds is None else f"{odds:g}"
    edge = candidate["side_adjusted_edge"]
    edge_text = "missing" if edge is None else f"{edge:.2f}"
    return (
        f"{prefix}: {candidate['sportsbook']} line "
        f"{candidate['line']:g} at {odds_text}, side-adjusted edge "
        f"{edge_text}. Research review only."
    )


def _diagnostics_payload(
    *,
    target_date: str,
    status: str,
    quality_board_path: Path,
    market_board_path: Path,
    quality_df: pd.DataFrame,
    market_df: pd.DataFrame,
    tracker: pd.DataFrame,
    schema_missing_columns: dict[str, list[str]],
    warnings: list[str],
    output_path: Path,
    summary_path: Path,
    diagnostics_path: Path,
) -> dict[str, Any]:
    category_counts = _value_counts(tracker, "improvement_category")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": target_date,
        "target_date": target_date,
        "status": status,
        "quality_board_path": str(quality_board_path),
        "market_board_path": str(market_board_path),
        "input_quality_row_count": int(len(quality_df.index)),
        "input_market_row_count": int(len(market_df.index)),
        "output_row_count": int(len(tracker.index)),
        "improvement_category_counts": category_counts,
        "fair_price_now_count": category_counts.get(FAIR_PRICE_NOW, 0),
        "better_line_now_count": category_counts.get(BETTER_LINE_NOW, 0),
        "monitor_for_price_drop_count": category_counts.get(
            MONITOR_FOR_PRICE_DROP,
            0,
        ),
        "low_priority_monitor_count": category_counts.get(
            LOW_PRIORITY_MONITOR,
            0,
        ),
        "no_improvement_available_count": category_counts.get(
            NO_IMPROVEMENT_AVAILABLE,
            0,
        ),
        "eligible_for_betting_any_true": _eligible_any_true(tracker),
        "market_prop_rows_created": 0,
        "elite_rows_created": 0,
        "kelly_called": False,
        "operator_betting_boards_written": [],
        "schema_missing_required_columns": schema_missing_columns,
        "warnings": warnings,
        "artifacts": {
            "odds_improvement_tracker_csv": str(output_path),
            "odds_improvement_tracker_summary_txt": str(summary_path),
            "odds_improvement_tracker_diagnostics_json": str(
                diagnostics_path
            ),
        },
    }


def _write_outputs(
    output_path: Path,
    summary_path: Path,
    diagnostics_path: Path,
    tracker: pd.DataFrame,
    diagnostics: dict[str, Any],
) -> None:
    tracker.to_csv(output_path, index=False)
    _write_summary(summary_path, diagnostics)
    diagnostics_path.write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_summary(path: Path, diagnostics: dict[str, Any]) -> None:
    lines = [
        f"Odds Improvement Tracker - {diagnostics['date']}",
        SUMMARY_NOTICE,
        f"status: {diagnostics['status']}",
        f"quality_board: {diagnostics['quality_board_path']}",
        f"market_board: {diagnostics['market_board_path']}",
        (
            "input_quality_row_count: "
            f"{diagnostics['input_quality_row_count']}"
        ),
        (
            "input_market_row_count: "
            f"{diagnostics['input_market_row_count']}"
        ),
        f"output_row_count: {diagnostics['output_row_count']}",
        (
            "improvement_category_counts: "
            f"{_counts_inline(diagnostics['improvement_category_counts'])}"
        ),
        f"fair_price_now_count: {diagnostics['fair_price_now_count']}",
        f"better_line_now_count: {diagnostics['better_line_now_count']}",
        (
            "monitor_for_price_drop_count: "
            f"{diagnostics['monitor_for_price_drop_count']}"
        ),
        (
            "low_priority_monitor_count: "
            f"{diagnostics['low_priority_monitor_count']}"
        ),
        (
            "no_improvement_available_count: "
            f"{diagnostics['no_improvement_available_count']}"
        ),
        (
            "eligible_for_betting_any_true: "
            f"{diagnostics['eligible_for_betting_any_true']}"
        ),
        "market_prop_rows_created: 0",
        "elite_rows_created: 0",
        "kelly_called: False",
        "operator_betting_boards_written: 0",
    ]
    if diagnostics["warnings"]:
        lines.extend(
            [
                "warnings:",
                *[
                    f"  {warning}"
                    for warning in diagnostics["warnings"]
                ],
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _empty_tracker(
    source_columns: list[str] | None = None,
) -> pd.DataFrame:
    columns = [
        *[
            column
            for column in (source_columns or QUALITY_REQUIRED_COLUMNS)
            if column not in TRACKING_COLUMNS
        ],
        *TRACKING_COLUMNS,
    ]
    return pd.DataFrame(columns=list(dict.fromkeys(columns)))


def _read_csv(
    path: Path,
    source_label: str,
) -> tuple[pd.DataFrame, str]:
    try:
        return pd.read_csv(path, low_memory=False), ""
    except pd.errors.EmptyDataError:
        return pd.DataFrame(), f"{source_label} is empty: {path}"
    except Exception as exc:
        return (
            pd.DataFrame(),
            f"Could not read {source_label} {path}: "
            f"{type(exc).__name__}: {exc}",
        )


def _missing_columns(
    df: pd.DataFrame,
    required: list[str],
) -> list[str]:
    return [column for column in required if column not in df.columns]


def _match_key(row: pd.Series) -> tuple[str, str, str]:
    return (
        _clean_text(row.get("player_name")).casefold(),
        _clean_text(row.get("market_type")).casefold(),
        _clean_text(row.get("side")).casefold(),
    )


def _value_counts(
    df: pd.DataFrame,
    column: str,
) -> dict[str, int]:
    if column not in df.columns:
        return {}
    counts: dict[str, int] = {}
    for value in df[column].tolist():
        text = _clean_text(value)
        if text:
            counts[text] = counts.get(text, 0) + 1
    return counts


def _eligible_any_true(df: pd.DataFrame) -> bool:
    if "eligible_for_betting" not in df.columns or df.empty:
        return False
    return any(
        _truthy(value)
        for value in df["eligible_for_betting"].tolist()
    )


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if value is False or _is_missing(value):
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _coerce_number(value: Any) -> float | None:
    if _is_missing(value):
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(parsed) else parsed


def _same_number(
    left: float | None,
    right: float | None,
) -> bool:
    return (
        left is not None
        and right is not None
        and abs(left - right) < 1e-9
    )


def _sort_number(value: float | None) -> float:
    return float("-inf") if value is None else value


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(value, str) and not value.strip()


def _clean_text(value: Any) -> str:
    return "" if _is_missing(value) else str(value).strip()


def _counts_inline(counts: dict[str, int]) -> str:
    return (
        ", ".join(f"{key}={value}" for key, value in counts.items())
        if counts
        else "none"
    )


def _validate_date(value: str) -> str:
    text = str(value).strip()
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("--date must be in YYYY-MM-DD format") from exc
    return text


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the CourtVision research-only odds improvement tracker."
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Target date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--quality-board",
        default=None,
        help="Quality-gated research board CSV. Defaults to output-dir/date.",
    )
    parser.add_argument(
        "--market-board",
        default=None,
        help="Full market validation board CSV. Defaults to output-dir/date.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Research output directory. Defaults to outputs/runtime/research.",
    )
    parser.add_argument(
        "--diagnostics-dir",
        default=str(DEFAULT_DIAGNOSTICS_DIR),
        help="Diagnostics directory. Defaults to outputs/runtime/diagnostics.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = run_odds_improvement_tracker(
            target_date=args.date,
            quality_board=args.quality_board,
            market_board=args.market_board,
            output_dir=args.output_dir,
            diagnostics_dir=args.diagnostics_dir,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"status: {result.status}")
    print(f"tracker: {result.output_path}")
    print(f"summary: {result.summary_path}")
    print(f"diagnostics: {result.diagnostics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
