"""Phase 6D research-only projection quality review.

This entrypoint reviews manual-board rows against their market/projection join
context. It does not create picks, MarketProp rows, Elite rows, Kelly inputs,
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


PROJECTION_QUALITY_REVIEW_OK = "PROJECTION_QUALITY_REVIEW_OK"
PROJECTION_QUALITY_REVIEW_INPUT_MISSING = (
    "PROJECTION_QUALITY_REVIEW_INPUT_MISSING"
)
PROJECTION_QUALITY_REVIEW_SCHEMA_INVALID = (
    "PROJECTION_QUALITY_REVIEW_SCHEMA_INVALID"
)
PROJECTION_QUALITY_REVIEW_NO_OUTPUT_ROWS = (
    "PROJECTION_QUALITY_REVIEW_NO_OUTPUT_ROWS"
)

DEFAULT_OUTPUT_DIR = Path("outputs/runtime/research")
DEFAULT_DIAGNOSTICS_DIR = Path("outputs/runtime/diagnostics")

HIGH_RESEARCH_CONFIDENCE = "high_research_confidence"
MEDIUM_RESEARCH_CONFIDENCE = "medium_research_confidence"
LOW_RESEARCH_CONFIDENCE = "low_research_confidence"
MANUAL_REVIEW_ONLY = "manual_review_only"

MIN_STABLE_MINUTES_FACTOR = 0.90
MAX_STABLE_MINUTES_FACTOR = 1.10
MIN_PHASE_6C_MINUTES_FACTOR = 0.85
MAX_PHASE_6C_MINUTES_FACTOR = 1.15

ACCEPTABLE_PROJECTION_QUALITY_FLAGS = {
    "acceptable",
    "model_projection",
    "projection_available",
    "research_projection_only",
}

MATCH_COLUMNS = [
    "player_name",
    "market_type",
    "side",
    "line",
    "sportsbook",
]
JOIN_REQUIRED_COLUMNS = [
    *MATCH_COLUMNS,
    "projection_value",
    "side_adjusted_edge",
    "american_odds",
    "projection_quality_flag",
]
MANUAL_REQUIRED_COLUMNS = [
    *MATCH_COLUMNS,
    "projection_value",
    "side_adjusted_edge",
    "american_odds",
    "projection_quality_flag",
]
QUALITY_REVIEW_COLUMNS = [
    "projection_quality_review",
    "projection_confidence_tier",
    "review_warning_flags",
    "odds_quality_flag",
    "line_quality_flag",
    "minutes_quality_flag",
]
FALLBACK_OUTPUT_COLUMNS = [
    "player_name",
    "market_type",
    "side",
    "line",
    "projection_value",
    "side_adjusted_edge",
    "sportsbook",
    "american_odds",
    "projection_source_type",
    "projection_quality_flag",
    "eligible_for_betting",
    *QUALITY_REVIEW_COLUMNS,
]


@dataclass(slots=True)
class ProjectionQualityReviewResult:
    status: str
    output_path: Path
    summary_path: Path
    diagnostics_path: Path
    diagnostics: dict[str, Any]


def run_projection_quality_review(
    *,
    target_date: str,
    join_board: str | Path | None = None,
    manual_board: str | Path | None = None,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    diagnostics_dir: str | Path = DEFAULT_DIAGNOSTICS_DIR,
) -> ProjectionQualityReviewResult:
    """Build one research-only projection quality review board."""
    target_date_text = _validate_date(target_date)
    output_dir_path = Path(output_dir)
    diagnostics_dir_path = Path(diagnostics_dir)
    join_board_path = Path(join_board) if join_board else (
        output_dir_path / f"market_projection_join_{target_date_text}.csv"
    )
    manual_board_path = Path(manual_board) if manual_board else (
        output_dir_path / f"manual_review_board_{target_date_text}.csv"
    )
    output_path = (
        output_dir_path / f"projection_quality_review_{target_date_text}.csv"
    )
    summary_path = output_dir_path / (
        f"projection_quality_review_summary_{target_date_text}.txt"
    )
    diagnostics_path = diagnostics_dir_path / (
        f"projection_quality_review_{target_date_text}.json"
    )

    output_dir_path.mkdir(parents=True, exist_ok=True)
    diagnostics_dir_path.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    join_df = pd.DataFrame()
    manual_df = pd.DataFrame()
    missing_inputs = [
        str(path)
        for path in (join_board_path, manual_board_path)
        if not path.exists()
    ]
    join_read_error = ""
    manual_read_error = ""

    if join_board_path.exists():
        join_df, join_read_error = _read_csv(join_board_path, "join board")
    if manual_board_path.exists():
        manual_df, manual_read_error = _read_csv(
            manual_board_path,
            "manual review board",
        )

    schema_missing_columns = {
        "join_board": _missing_columns(join_df, JOIN_REQUIRED_COLUMNS),
        "manual_board": _missing_columns(manual_df, MANUAL_REQUIRED_COLUMNS),
    }
    if join_read_error:
        warnings.append(join_read_error)
    if manual_read_error:
        warnings.append(manual_read_error)
    if missing_inputs:
        warnings.extend(f"Input not found: {path}" for path in missing_inputs)
    if schema_missing_columns["join_board"]:
        warnings.append(
            "Join board is missing required columns: "
            + ", ".join(schema_missing_columns["join_board"])
        )
    if schema_missing_columns["manual_board"]:
        warnings.append(
            "Manual review board is missing required columns: "
            + ", ".join(schema_missing_columns["manual_board"])
        )

    source_eligible_for_betting_any_true = (
        _eligible_any_true(join_df) or _eligible_any_true(manual_df)
    )
    if source_eligible_for_betting_any_true:
        warnings.append(
            "Input had eligible_for_betting truthy values; review output was "
            "forced false."
        )

    has_schema_issue = bool(
        join_read_error
        or manual_read_error
        or schema_missing_columns["join_board"]
        or schema_missing_columns["manual_board"]
    )
    if missing_inputs:
        status = PROJECTION_QUALITY_REVIEW_INPUT_MISSING
        board = _empty_review_frame()
    else:
        board, build_warnings = _build_review_board(
            join_df,
            manual_df,
            has_schema_issue=has_schema_issue,
        )
        warnings.extend(build_warnings)
        if has_schema_issue:
            status = PROJECTION_QUALITY_REVIEW_SCHEMA_INVALID
        elif board.empty:
            status = PROJECTION_QUALITY_REVIEW_NO_OUTPUT_ROWS
        else:
            status = PROJECTION_QUALITY_REVIEW_OK

    diagnostics = _diagnostics_payload(
        target_date=target_date_text,
        status=status,
        join_board_path=join_board_path,
        manual_board_path=manual_board_path,
        join_df=join_df,
        manual_df=manual_df,
        board=board,
        schema_missing_columns=schema_missing_columns,
        source_eligible_for_betting_any_true=(
            source_eligible_for_betting_any_true
        ),
        warnings=warnings,
        output_path=output_path,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
    )
    _write_outputs(output_path, summary_path, diagnostics_path, board, diagnostics)
    return ProjectionQualityReviewResult(
        status=status,
        output_path=output_path,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
        diagnostics=diagnostics,
    )


def _build_review_board(
    join_df: pd.DataFrame,
    manual_df: pd.DataFrame,
    *,
    has_schema_issue: bool,
) -> tuple[pd.DataFrame, list[str]]:
    if manual_df.empty:
        return _empty_review_frame(manual_df.columns.tolist()), []

    board = manual_df.copy()
    for column in QUALITY_REVIEW_COLUMNS:
        board[column] = pd.NA
    board["eligible_for_betting"] = False

    join_lookup = _join_row_lookup(join_df)
    unmatched_count = 0
    duplicate_match_count = 0

    for index, row in board.iterrows():
        matches = join_lookup.get(_match_key(row), [])
        join_row = matches[0] if matches else None
        if not matches:
            unmatched_count += 1
        elif len(matches) > 1:
            duplicate_match_count += 1

        minutes_factor = _minutes_factor(row, join_row)
        projection_value = _coerce_number(row.get("projection_value"))
        american_odds = _coerce_number(row.get("american_odds"))
        market_line = _first_number(row, ("line", "market_line"))
        side_adjusted_edge = _coerce_number(row.get("side_adjusted_edge"))
        projection_quality_flag = _clean_text(
            row.get("projection_quality_flag")
        ).lower()

        odds_quality_flag = _odds_quality_flag(american_odds)
        line_quality_flag = _line_quality_flag(side_adjusted_edge)
        minutes_quality_flag = _minutes_quality_flag(minutes_factor)
        row_schema_issues = _row_schema_issues(
            row,
            join_row=join_row,
            projection_value=projection_value,
            american_odds=american_odds,
            market_line=market_line,
            side_adjusted_edge=side_adjusted_edge,
            projection_quality_flag=projection_quality_flag,
            has_schema_issue=has_schema_issue,
        )
        confidence_tier = _confidence_tier(
            projection_value=projection_value,
            american_odds=american_odds,
            market_line=market_line,
            side_adjusted_edge=side_adjusted_edge,
            projection_quality_flag=projection_quality_flag,
            minutes_factor=minutes_factor,
            schema_issues=row_schema_issues,
        )
        warning_flags = _review_warning_flags(
            projection_quality_flag=projection_quality_flag,
            odds_quality_flag=odds_quality_flag,
            line_quality_flag=line_quality_flag,
            minutes_quality_flag=minutes_quality_flag,
            schema_issues=row_schema_issues,
        )

        board.at[index, "projection_quality_review"] = (
            _projection_quality_review(confidence_tier)
        )
        board.at[index, "projection_confidence_tier"] = confidence_tier
        board.at[index, "review_warning_flags"] = warning_flags
        board.at[index, "odds_quality_flag"] = odds_quality_flag
        board.at[index, "line_quality_flag"] = line_quality_flag
        board.at[index, "minutes_quality_flag"] = minutes_quality_flag

    warnings: list[str] = []
    if unmatched_count:
        warnings.append(
            f"{unmatched_count} manual review rows had no matching join-board row."
        )
    if duplicate_match_count:
        warnings.append(
            f"{duplicate_match_count} manual review rows matched multiple join rows; "
            "the first source-order match was used."
        )
    return board.reset_index(drop=True), warnings


def _join_row_lookup(join_df: pd.DataFrame) -> dict[tuple[str, ...], list[pd.Series]]:
    lookup: dict[tuple[str, ...], list[pd.Series]] = {}
    for _, row in join_df.iterrows():
        lookup.setdefault(_match_key(row), []).append(row)
    return lookup


def _match_key(row: pd.Series) -> tuple[str, ...]:
    return (
        _clean_text(row.get("player_name")).lower(),
        _clean_text(row.get("market_type")).lower(),
        _clean_text(row.get("side")).lower(),
        _number_key(row.get("line")),
        _clean_text(row.get("sportsbook")).lower(),
    )


def _number_key(value: Any) -> str:
    number = _coerce_number(value)
    return f"{number:.12g}" if number is not None else ""


def _minutes_factor(
    manual_row: pd.Series,
    join_row: pd.Series | None,
) -> float | None:
    for row in (manual_row, join_row):
        if row is None:
            continue
        direct = _first_number(
            row,
            ("minutes_factor", "projection_minutes_factor"),
        )
        if direct is not None:
            return direct

    for row in (join_row, manual_row):
        if row is None:
            continue
        for recent_column, average_column in (
            ("projection_min_recent", "projection_min_avg"),
            ("min_recent", "min_avg"),
        ):
            recent = _coerce_number(row.get(recent_column))
            average = _coerce_number(row.get(average_column))
            if recent is None or average is None or average <= 0:
                continue
            return min(
                MAX_PHASE_6C_MINUTES_FACTOR,
                max(MIN_PHASE_6C_MINUTES_FACTOR, recent / average),
            )
    return None


def _odds_quality_flag(american_odds: float | None) -> str:
    if american_odds is None:
        return "missing_odds"
    if american_odds < -150:
        return "heavy_juice"
    return "favorable_or_fair"


def _line_quality_flag(side_adjusted_edge: float | None) -> str:
    if side_adjusted_edge is None:
        return "missing_edge"
    if side_adjusted_edge >= 3.0:
        return "strong_edge_line"
    if side_adjusted_edge >= 1.5:
        return "review_edge_line"
    return "weak_edge_line"


def _minutes_quality_flag(minutes_factor: float | None) -> str:
    if minutes_factor is None:
        return "missing_minutes"
    if MIN_STABLE_MINUTES_FACTOR <= minutes_factor <= MAX_STABLE_MINUTES_FACTOR:
        return "stable_minutes"
    return "minutes_shift_review"


def _row_schema_issues(
    row: pd.Series,
    *,
    join_row: pd.Series | None,
    projection_value: float | None,
    american_odds: float | None,
    market_line: float | None,
    side_adjusted_edge: float | None,
    projection_quality_flag: str,
    has_schema_issue: bool,
) -> list[str]:
    issues: list[str] = []
    if has_schema_issue:
        issues.append("input_schema_issue")
    if join_row is None:
        issues.append("join_row_not_found")
    if projection_value is None:
        issues.append("missing_projection")
    if american_odds is None:
        issues.append("missing_odds")
    if market_line is None:
        issues.append("missing_line")
    if side_adjusted_edge is None:
        issues.append("missing_side_adjusted_edge")
    if not projection_quality_flag:
        issues.append("missing_projection_quality_flag")
    for column in ("player_name", "market_type", "side", "sportsbook"):
        if not _clean_text(row.get(column)):
            issues.append(f"missing_{column}")
    return list(dict.fromkeys(issues))


def _confidence_tier(
    *,
    projection_value: float | None,
    american_odds: float | None,
    market_line: float | None,
    side_adjusted_edge: float | None,
    projection_quality_flag: str,
    minutes_factor: float | None,
    schema_issues: list[str],
) -> str:
    if (
        schema_issues
        or projection_value is None
        or american_odds is None
        or market_line is None
        or side_adjusted_edge is None
    ):
        return MANUAL_REVIEW_ONLY

    stable_minutes = (
        minutes_factor is not None
        and MIN_STABLE_MINUTES_FACTOR
        <= minutes_factor
        <= MAX_STABLE_MINUTES_FACTOR
    )
    if (
        projection_quality_flag == "research_projection_only"
        and stable_minutes
        and side_adjusted_edge >= 3.0
    ):
        return HIGH_RESEARCH_CONFIDENCE
    if (
        projection_quality_flag == "low_minutes_context"
        or side_adjusted_edge < 1.5
    ):
        return LOW_RESEARCH_CONFIDENCE
    if (
        projection_quality_flag in ACCEPTABLE_PROJECTION_QUALITY_FLAGS
        and side_adjusted_edge >= 1.5
    ):
        return MEDIUM_RESEARCH_CONFIDENCE
    return LOW_RESEARCH_CONFIDENCE


def _projection_quality_review(confidence_tier: str) -> str:
    if confidence_tier == MANUAL_REVIEW_ONLY:
        return "manual_quality_review_required"
    if confidence_tier == LOW_RESEARCH_CONFIDENCE:
        return "research_quality_warning"
    return "research_quality_review_complete"


def _review_warning_flags(
    *,
    projection_quality_flag: str,
    odds_quality_flag: str,
    line_quality_flag: str,
    minutes_quality_flag: str,
    schema_issues: list[str],
) -> str:
    flags = list(schema_issues)
    if projection_quality_flag == "low_minutes_context":
        flags.append("low_minutes_context")
    elif projection_quality_flag not in ACCEPTABLE_PROJECTION_QUALITY_FLAGS:
        flags.append(
            f"projection_quality_{projection_quality_flag or 'missing'}"
        )
    if odds_quality_flag != "favorable_or_fair":
        flags.append(odds_quality_flag)
    if line_quality_flag != "strong_edge_line":
        flags.append(line_quality_flag)
    if minutes_quality_flag != "stable_minutes":
        flags.append(minutes_quality_flag)
    unique_flags = list(dict.fromkeys(flags))
    return "|".join(unique_flags) if unique_flags else "none"


def _diagnostics_payload(
    *,
    target_date: str,
    status: str,
    join_board_path: Path,
    manual_board_path: Path,
    join_df: pd.DataFrame,
    manual_df: pd.DataFrame,
    board: pd.DataFrame,
    schema_missing_columns: dict[str, list[str]],
    source_eligible_for_betting_any_true: bool,
    warnings: list[str],
    output_path: Path,
    summary_path: Path,
    diagnostics_path: Path,
) -> dict[str, Any]:
    confidence_tier_counts = _value_counts(
        board,
        "projection_confidence_tier",
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": target_date,
        "target_date": target_date,
        "status": status,
        "join_board_path": str(join_board_path),
        "manual_board_path": str(manual_board_path),
        "input_join_row_count": int(len(join_df.index)),
        "input_manual_row_count": int(len(manual_df.index)),
        "output_row_count": int(len(board.index)),
        "confidence_tier_counts": confidence_tier_counts,
        "odds_quality_flag_counts": _value_counts(board, "odds_quality_flag"),
        "line_quality_flag_counts": _value_counts(board, "line_quality_flag"),
        "minutes_quality_flag_counts": _value_counts(
            board,
            "minutes_quality_flag",
        ),
        "projection_quality_flag_counts": _value_counts(
            board,
            "projection_quality_flag",
        ),
        "high_research_confidence_count": confidence_tier_counts.get(
            HIGH_RESEARCH_CONFIDENCE,
            0,
        ),
        "medium_research_confidence_count": confidence_tier_counts.get(
            MEDIUM_RESEARCH_CONFIDENCE,
            0,
        ),
        "low_research_confidence_count": confidence_tier_counts.get(
            LOW_RESEARCH_CONFIDENCE,
            0,
        ),
        "manual_review_only_count": confidence_tier_counts.get(
            MANUAL_REVIEW_ONLY,
            0,
        ),
        "source_eligible_for_betting_any_true": bool(
            source_eligible_for_betting_any_true
        ),
        "eligible_for_betting_any_true": _eligible_any_true(board),
        "market_prop_rows_created": 0,
        "elite_rows_created": 0,
        "kelly_called": False,
        "operator_betting_boards_written": [],
        "schema_missing_required_columns": schema_missing_columns,
        "warnings": warnings,
        "artifacts": {
            "projection_quality_review_csv": str(output_path),
            "projection_quality_review_summary_txt": str(summary_path),
            "projection_quality_review_diagnostics_json": str(
                diagnostics_path
            ),
        },
    }


def _write_outputs(
    output_path: Path,
    summary_path: Path,
    diagnostics_path: Path,
    board: pd.DataFrame,
    diagnostics: dict[str, Any],
) -> None:
    board.to_csv(output_path, index=False)
    _write_summary(summary_path, diagnostics)
    diagnostics_path.write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_summary(path: Path, diagnostics: dict[str, Any]) -> None:
    lines = [
        f"Projection Quality Review - {diagnostics['date']}",
        "Research-only projection review. Not betting-approved. No picks created.",
        f"status: {diagnostics['status']}",
        f"join_board: {diagnostics['join_board_path']}",
        f"manual_board: {diagnostics['manual_board_path']}",
        f"input_join_row_count: {diagnostics['input_join_row_count']}",
        f"input_manual_row_count: {diagnostics['input_manual_row_count']}",
        f"output_row_count: {diagnostics['output_row_count']}",
        (
            "confidence_tier_counts: "
            f"{_counts_inline(diagnostics['confidence_tier_counts'])}"
        ),
        (
            "odds_quality_flag_counts: "
            f"{_counts_inline(diagnostics['odds_quality_flag_counts'])}"
        ),
        (
            "line_quality_flag_counts: "
            f"{_counts_inline(diagnostics['line_quality_flag_counts'])}"
        ),
        (
            "minutes_quality_flag_counts: "
            f"{_counts_inline(diagnostics['minutes_quality_flag_counts'])}"
        ),
        (
            "projection_quality_flag_counts: "
            f"{_counts_inline(diagnostics['projection_quality_flag_counts'])}"
        ),
        (
            "high_research_confidence_count: "
            f"{diagnostics['high_research_confidence_count']}"
        ),
        (
            "medium_research_confidence_count: "
            f"{diagnostics['medium_research_confidence_count']}"
        ),
        (
            "low_research_confidence_count: "
            f"{diagnostics['low_research_confidence_count']}"
        ),
        f"manual_review_only_count: {diagnostics['manual_review_only_count']}",
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
            ["warnings:", *[f"  {warning}" for warning in diagnostics["warnings"]]]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _empty_review_frame(
    source_columns: list[str] | None = None,
) -> pd.DataFrame:
    columns = [
        *(source_columns or FALLBACK_OUTPUT_COLUMNS),
        *QUALITY_REVIEW_COLUMNS,
    ]
    return pd.DataFrame(columns=list(dict.fromkeys(columns)))


def _read_csv(path: Path, source_label: str) -> tuple[pd.DataFrame, str]:
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


def _missing_columns(df: pd.DataFrame, required: list[str]) -> list[str]:
    return [column for column in required if column not in df.columns]


def _first_number(row: pd.Series, columns: tuple[str, ...]) -> float | None:
    for column in columns:
        value = _coerce_number(row.get(column))
        if value is not None:
            return value
    return None


def _value_counts(df: pd.DataFrame, column: str) -> dict[str, int]:
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
    return any(_truthy(value) for value in df["eligible_for_betting"].tolist())


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
        description="Run CourtVision research-only projection quality review."
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Target date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--join-board",
        default=None,
        help="Market/projection join CSV. Defaults to output-dir/date naming.",
    )
    parser.add_argument(
        "--manual-board",
        default=None,
        help="Manual review board CSV. Defaults to output-dir/date naming.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Research output directory. Defaults to outputs/runtime/research.",
    )
    parser.add_argument(
        "--diagnostics-dir",
        default=str(DEFAULT_DIAGNOSTICS_DIR),
        help="Diagnostics output directory. Defaults to outputs/runtime/diagnostics.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = run_projection_quality_review(
            target_date=args.date,
            join_board=args.join_board,
            manual_board=args.manual_board,
            output_dir=args.output_dir,
            diagnostics_dir=args.diagnostics_dir,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"status: {result.status}")
    print(f"review: {result.output_path}")
    print(f"summary: {result.summary_path}")
    print(f"diagnostics: {result.diagnostics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
