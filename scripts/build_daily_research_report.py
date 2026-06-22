"""Phase 6G readable daily research-only report bundle.

This entrypoint summarizes the quality-gated research board, odds improvement
tracker, and projection quality review. It does not create picks, MarketProp
rows, Elite rows, Kelly inputs, or operator betting boards.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd


DAILY_RESEARCH_REPORT_OK = "DAILY_RESEARCH_REPORT_OK"
DAILY_RESEARCH_REPORT_INPUT_MISSING = (
    "DAILY_RESEARCH_REPORT_INPUT_MISSING"
)
DAILY_RESEARCH_REPORT_SCHEMA_INVALID = (
    "DAILY_RESEARCH_REPORT_SCHEMA_INVALID"
)
DAILY_RESEARCH_REPORT_NO_OUTPUT_ROWS = (
    "DAILY_RESEARCH_REPORT_NO_OUTPUT_ROWS"
)

DEFAULT_OUTPUT_DIR = Path("outputs/runtime/research")
DEFAULT_DIAGNOSTICS_DIR = Path("outputs/runtime/diagnostics")
DEFAULT_TOP_N = 15

RESEARCH_WATCHLIST = "research_watchlist"
PRICE_SENSITIVE_WATCHLIST = "price_sensitive_watchlist"
LOW_CONFIDENCE_REVIEW = "low_confidence_review"
EXCLUDED_RESEARCH_ONLY = "excluded_research_only"
FAIR_PRICE_NOW = "fair_price_now"
LOW_PRIORITY_MONITOR = "low_priority_monitor"
HEAVY_JUICE = "heavy_juice"

HIGH_RESEARCH_CONFIDENCE = "high_research_confidence"
MEDIUM_RESEARCH_CONFIDENCE = "medium_research_confidence"
LOW_RESEARCH_CONFIDENCE = "low_research_confidence"

BETTING_APPROVAL_STATUS = "research_only_not_betting_approved"
SAFETY_STATUS = "RESEARCH_ONLY_SAFE"
REPORT_NOTICE = (
    "Research-only daily report. These are not betting-approved picks. "
    "No picks were created."
)
SECTION_NOTICE = (
    "**Research-only notice:** These rows are not betting-approved picks. "
    "`eligible_for_betting=False`."
)
REPORT_SECTIONS = [
    "Executive Summary",
    "Research Watchlist",
    "Fair Price Review",
    "Price Sensitive Watchlist",
    "Low Confidence / Monitor Only",
    "Excluded Research Only",
    "Safety Checklist",
]

QUALITY_REQUIRED_COLUMNS = [
    "player_name",
    "market_type",
    "side",
    "line",
    "projection_value",
    "side_adjusted_edge",
    "sportsbook",
    "american_odds",
    "projection_confidence_tier",
    "odds_quality_flag",
    "research_category",
    "eligible_for_betting",
]
ODDS_REQUIRED_COLUMNS = [
    "player_name",
    "market_type",
    "side",
    "line",
    "projection_value",
    "side_adjusted_edge",
    "research_category",
    "projection_confidence_tier",
    "improvement_category",
    "best_available_sportsbook",
    "best_available_line",
    "best_available_american_odds",
    "best_available_side_adjusted_edge",
    "improvement_note",
    "eligible_for_betting",
]
PROJECTION_REQUIRED_COLUMNS = [
    "player_name",
    "market_type",
    "side",
    "line",
    "projection_confidence_tier",
    "eligible_for_betting",
]


@dataclass(slots=True)
class DailyResearchReportResult:
    status: str
    report_path: Path
    summary_path: Path
    diagnostics_path: Path
    diagnostics: dict[str, Any]


def build_daily_research_report(
    *,
    target_date: str,
    quality_board: str | Path | None = None,
    odds_tracker: str | Path | None = None,
    projection_review: str | Path | None = None,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    diagnostics_dir: str | Path = DEFAULT_DIAGNOSTICS_DIR,
    top_n: int = DEFAULT_TOP_N,
) -> DailyResearchReportResult:
    """Build the readable Phase 6G research-only report bundle."""
    target_date_text = _validate_date(target_date)
    top_n_value = _non_negative_int(top_n, "--top-n")
    output_dir_path = Path(output_dir)
    diagnostics_dir_path = Path(diagnostics_dir)
    quality_board_path = Path(quality_board) if quality_board else (
        output_dir_path
        / f"quality_gated_research_board_{target_date_text}.csv"
    )
    odds_tracker_path = Path(odds_tracker) if odds_tracker else (
        output_dir_path
        / f"odds_improvement_tracker_{target_date_text}.csv"
    )
    projection_review_path = (
        Path(projection_review)
        if projection_review
        else output_dir_path
        / f"projection_quality_review_{target_date_text}.csv"
    )
    report_path = (
        output_dir_path / f"daily_research_report_{target_date_text}.md"
    )
    summary_path = (
        output_dir_path
        / f"daily_research_report_summary_{target_date_text}.txt"
    )
    diagnostics_path = (
        diagnostics_dir_path
        / f"daily_research_report_{target_date_text}.json"
    )

    output_dir_path.mkdir(parents=True, exist_ok=True)
    diagnostics_dir_path.mkdir(parents=True, exist_ok=True)

    input_specs = {
        "quality_board": (
            quality_board_path,
            "quality-gated research board",
            QUALITY_REQUIRED_COLUMNS,
        ),
        "odds_tracker": (
            odds_tracker_path,
            "odds improvement tracker",
            ODDS_REQUIRED_COLUMNS,
        ),
        "projection_review": (
            projection_review_path,
            "projection quality review",
            PROJECTION_REQUIRED_COLUMNS,
        ),
    }
    frames: dict[str, pd.DataFrame] = {}
    read_errors: dict[str, str] = {}
    missing_inputs: list[str] = []
    schema_missing_columns: dict[str, list[str]] = {}
    warnings: list[str] = []

    for key, (path, label, required_columns) in input_specs.items():
        if not path.exists():
            frames[key] = pd.DataFrame()
            missing_inputs.append(str(path))
            schema_missing_columns[key] = []
            continue
        frame, read_error = _read_csv(path, label)
        frames[key] = frame
        if read_error:
            read_errors[key] = read_error
            warnings.append(read_error)
        missing_columns = _missing_columns(frame, required_columns)
        schema_missing_columns[key] = missing_columns
        if missing_columns:
            warnings.append(
                f"{label} is missing required columns: "
                + ", ".join(missing_columns)
            )

    warnings.extend(f"Input not found: {path}" for path in missing_inputs)
    source_eligible_for_betting_any_true = any(
        _eligible_any_true(frame) for frame in frames.values()
    )
    if source_eligible_for_betting_any_true:
        warnings.append(
            "Input had eligible_for_betting truthy values; report rendering "
            "was forced false."
        )

    quality_df = frames["quality_board"]
    odds_df = frames["odds_tracker"]
    projection_df = frames["projection_review"]
    if missing_inputs:
        status = DAILY_RESEARCH_REPORT_INPUT_MISSING
    elif read_errors or any(schema_missing_columns.values()):
        status = DAILY_RESEARCH_REPORT_SCHEMA_INVALID
    elif quality_df.empty:
        status = DAILY_RESEARCH_REPORT_NO_OUTPUT_ROWS
    else:
        status = DAILY_RESEARCH_REPORT_OK

    diagnostics = _diagnostics_payload(
        target_date=target_date_text,
        status=status,
        quality_board_path=quality_board_path,
        odds_tracker_path=odds_tracker_path,
        projection_review_path=projection_review_path,
        quality_df=quality_df,
        odds_df=odds_df,
        projection_df=projection_df,
        top_n=top_n_value,
        source_eligible_for_betting_any_true=(
            source_eligible_for_betting_any_true
        ),
        schema_missing_columns=schema_missing_columns,
        warnings=warnings,
        report_path=report_path,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
    )
    _write_outputs(
        report_path=report_path,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
        quality_df=quality_df,
        odds_df=odds_df,
        diagnostics=diagnostics,
        top_n=top_n_value,
    )
    return DailyResearchReportResult(
        status=status,
        report_path=report_path,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
        diagnostics=diagnostics,
    )


def _diagnostics_payload(
    *,
    target_date: str,
    status: str,
    quality_board_path: Path,
    odds_tracker_path: Path,
    projection_review_path: Path,
    quality_df: pd.DataFrame,
    odds_df: pd.DataFrame,
    projection_df: pd.DataFrame,
    top_n: int,
    source_eligible_for_betting_any_true: bool,
    schema_missing_columns: dict[str, list[str]],
    warnings: list[str],
    report_path: Path,
    summary_path: Path,
    diagnostics_path: Path,
) -> dict[str, Any]:
    quality_categories = _value_counts(quality_df, "research_category")
    improvement_categories = _value_counts(
        odds_df,
        "improvement_category",
    )
    confidence_tiers = _value_counts(
        projection_df,
        "projection_confidence_tier",
    )
    odds_quality_flags = _value_counts(quality_df, "odds_quality_flag")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": target_date,
        "target_date": target_date,
        "status": status,
        "safety_status": SAFETY_STATUS,
        "quality_board_path": str(quality_board_path),
        "odds_tracker_path": str(odds_tracker_path),
        "projection_review_path": str(projection_review_path),
        "quality_board_row_count": int(len(quality_df.index)),
        "odds_tracker_row_count": int(len(odds_df.index)),
        "projection_review_row_count": int(len(projection_df.index)),
        "total_research_rows": int(len(quality_df.index)),
        "top_n": top_n,
        "report_sections_created": REPORT_SECTIONS,
        "high_research_confidence_count": confidence_tiers.get(
            HIGH_RESEARCH_CONFIDENCE,
            0,
        ),
        "medium_research_confidence_count": confidence_tiers.get(
            MEDIUM_RESEARCH_CONFIDENCE,
            0,
        ),
        "low_research_confidence_count": confidence_tiers.get(
            LOW_RESEARCH_CONFIDENCE,
            0,
        ),
        "research_watchlist_count": quality_categories.get(
            RESEARCH_WATCHLIST,
            0,
        ),
        "fair_price_now_count": improvement_categories.get(
            FAIR_PRICE_NOW,
            0,
        ),
        "price_sensitive_watchlist_count": quality_categories.get(
            PRICE_SENSITIVE_WATCHLIST,
            0,
        ),
        "heavy_juice_count": odds_quality_flags.get(HEAVY_JUICE, 0),
        "low_priority_count": improvement_categories.get(
            LOW_PRIORITY_MONITOR,
            0,
        ),
        "low_confidence_review_count": quality_categories.get(
            LOW_CONFIDENCE_REVIEW,
            0,
        ),
        "excluded_research_only_count": quality_categories.get(
            EXCLUDED_RESEARCH_ONLY,
            0,
        ),
        "source_eligible_for_betting_any_true": bool(
            source_eligible_for_betting_any_true
        ),
        "eligible_for_betting_any_true": False,
        "market_prop_rows_created": 0,
        "elite_rows_created": 0,
        "kelly_called": False,
        "operator_betting_boards_written": [],
        "schema_missing_required_columns": schema_missing_columns,
        "warnings": warnings,
        "artifacts": {
            "daily_research_report_markdown": str(report_path),
            "daily_research_report_summary_txt": str(summary_path),
            "daily_research_report_diagnostics_json": str(
                diagnostics_path
            ),
        },
    }


def _write_outputs(
    *,
    report_path: Path,
    summary_path: Path,
    diagnostics_path: Path,
    quality_df: pd.DataFrame,
    odds_df: pd.DataFrame,
    diagnostics: dict[str, Any],
    top_n: int,
) -> None:
    report_path.write_text(
        _render_report(
            quality_df=quality_df,
            odds_df=odds_df,
            diagnostics=diagnostics,
            top_n=top_n,
        ),
        encoding="utf-8",
    )
    summary_path.write_text(
        _render_summary(diagnostics),
        encoding="utf-8",
    )
    diagnostics_path.write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _render_report(
    *,
    quality_df: pd.DataFrame,
    odds_df: pd.DataFrame,
    diagnostics: dict[str, Any],
    top_n: int,
) -> str:
    watchlist_rows = _filter_rows(
        quality_df,
        "research_category",
        {RESEARCH_WATCHLIST},
    )
    fair_price_rows = _filter_rows(
        odds_df,
        "improvement_category",
        {FAIR_PRICE_NOW},
    )
    price_sensitive_rows = _filter_rows(
        quality_df,
        "research_category",
        {PRICE_SENSITIVE_WATCHLIST},
    )
    low_confidence_rows = _filter_rows(
        odds_df,
        "improvement_category",
        {LOW_PRIORITY_MONITOR},
    )
    if low_confidence_rows.empty:
        low_confidence_rows = _filter_rows(
            quality_df,
            "research_category",
            {LOW_CONFIDENCE_REVIEW},
        )
    excluded_rows = _filter_rows(
        quality_df,
        "research_category",
        {EXCLUDED_RESEARCH_ONLY},
    )

    lines = [
        f"# CourtVision Daily Research Report - {diagnostics['date']}",
        "",
        f"> {REPORT_NOTICE}",
        "",
        "## Executive Summary",
        "",
        SECTION_NOTICE,
        "",
        f"- Slate date: `{diagnostics['date']}`",
        f"- Status: `{diagnostics['status']}`",
        f"- Total research rows: {diagnostics['total_research_rows']}",
        (
            "- Confidence counts: "
            f"high={diagnostics['high_research_confidence_count']}, "
            f"medium={diagnostics['medium_research_confidence_count']}, "
            f"low={diagnostics['low_research_confidence_count']}"
        ),
        (
            "- Research watchlist count: "
            f"{diagnostics['research_watchlist_count']}"
        ),
        (
            "- Price sensitive watchlist count: "
            f"{diagnostics['price_sensitive_watchlist_count']}"
        ),
        f"- Fair price now count: {diagnostics['fair_price_now_count']}",
        f"- Heavy juice count: {diagnostics['heavy_juice_count']}",
        f"- Low priority count: {diagnostics['low_priority_count']}",
        f"- Safety status: `{diagnostics['safety_status']}`",
        "",
    ]
    lines.extend(
        _render_quality_section(
            "Research Watchlist",
            watchlist_rows,
            top_n=top_n,
        )
    )
    lines.extend(
        _render_fair_price_section(
            fair_price_rows,
            top_n=top_n,
        )
    )
    lines.extend(
        _render_quality_section(
            "Price Sensitive Watchlist",
            price_sensitive_rows,
            top_n=top_n,
        )
    )
    lines.extend(
        _render_monitor_section(
            low_confidence_rows,
            top_n=top_n,
        )
    )
    lines.extend(
        _render_quality_section(
            "Excluded Research Only",
            excluded_rows,
            top_n=top_n,
        )
    )
    lines.extend(
        [
            "## Safety Checklist",
            "",
            SECTION_NOTICE,
            "",
            "- Betting mode invoked: No",
            "- Official picks created: 0",
            "- `eligible_for_betting`: False for every rendered row",
            "- MarketProp rows created: 0",
            "- Elite rows created: 0",
            "- Kelly called: False",
            "- Operator betting boards written: 0",
            (
                "- Betting approval status: "
                f"`{BETTING_APPROVAL_STATUS}`"
            ),
            f"- Safety status: `{diagnostics['safety_status']}`",
            "",
        ]
    )
    if diagnostics["warnings"]:
        lines.extend(
            [
                "### Input Warnings",
                "",
                *[
                    f"- {_markdown_text(warning)}"
                    for warning in diagnostics["warnings"]
                ],
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _render_quality_section(
    title: str,
    frame: pd.DataFrame,
    *,
    top_n: int,
) -> list[str]:
    columns = [
        ("Rank", lambda row: _first_value(
            row,
            ("research_board_rank", "review_rank"),
        )),
        ("Player", lambda row: row.get("player_name")),
        ("Market", lambda row: row.get("market_type")),
        ("Side", lambda row: row.get("side")),
        ("Line", lambda row: _format_number(row.get("line"))),
        (
            "Projection",
            lambda row: _format_number(row.get("projection_value"), 2),
        ),
        (
            "Edge",
            lambda row: _format_number(
                row.get("side_adjusted_edge"),
                2,
            ),
        ),
        ("Sportsbook", lambda row: row.get("sportsbook")),
        ("Odds", lambda row: _format_number(row.get("american_odds"))),
        (
            "Confidence",
            lambda row: row.get("projection_confidence_tier"),
        ),
        (
            "Research Note",
            lambda row: _first_value(
                row,
                ("final_research_note", "review_warning_flags"),
            ),
        ),
        ("Eligible for Betting", lambda row: "False"),
    ]
    return _render_section(title, frame, columns=columns, top_n=top_n)


def _render_fair_price_section(
    frame: pd.DataFrame,
    *,
    top_n: int,
) -> list[str]:
    columns = [
        ("Rank", lambda row: _first_value(
            row,
            ("research_board_rank", "review_rank"),
        )),
        ("Player", lambda row: row.get("player_name")),
        ("Market", lambda row: row.get("market_type")),
        ("Side", lambda row: row.get("side")),
        ("Source Line", lambda row: _format_number(row.get("line"))),
        (
            "Best Sportsbook",
            lambda row: row.get("best_available_sportsbook"),
        ),
        (
            "Best Line",
            lambda row: _format_number(row.get("best_available_line")),
        ),
        (
            "Best Odds",
            lambda row: _format_number(
                row.get("best_available_american_odds"),
            ),
        ),
        (
            "Best Edge",
            lambda row: _format_number(
                row.get("best_available_side_adjusted_edge"),
                2,
            ),
        ),
        ("Research Note", lambda row: row.get("improvement_note")),
        ("Eligible for Betting", lambda row: "False"),
    ]
    return _render_section(
        "Fair Price Review",
        frame,
        columns=columns,
        top_n=top_n,
    )


def _render_monitor_section(
    frame: pd.DataFrame,
    *,
    top_n: int,
) -> list[str]:
    columns = [
        ("Rank", lambda row: _first_value(
            row,
            ("research_board_rank", "review_rank"),
        )),
        ("Player", lambda row: row.get("player_name")),
        ("Market", lambda row: row.get("market_type")),
        ("Side", lambda row: row.get("side")),
        ("Line", lambda row: _format_number(row.get("line"))),
        (
            "Confidence",
            lambda row: row.get("projection_confidence_tier"),
        ),
        (
            "Monitor Status",
            lambda row: _first_value(
                row,
                ("improvement_category", "research_category"),
            ),
        ),
        (
            "Research Note",
            lambda row: _first_value(
                row,
                ("improvement_note", "final_research_note"),
            ),
        ),
        ("Eligible for Betting", lambda row: "False"),
    ]
    return _render_section(
        "Low Confidence / Monitor Only",
        frame,
        columns=columns,
        top_n=top_n,
    )


def _render_section(
    title: str,
    frame: pd.DataFrame,
    *,
    columns: list[tuple[str, Callable[[pd.Series], Any]]],
    top_n: int,
) -> list[str]:
    lines = [f"## {title}", "", SECTION_NOTICE, ""]
    if frame.empty or top_n == 0:
        lines.extend(
            [
                (
                    "No rows displayed in this section. This does not imply "
                    "betting approval."
                ),
                "",
            ]
        )
        return lines

    display_frame = frame.head(top_n)
    headers = [header for header, _ in columns]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for _, row in display_frame.iterrows():
        values = [
            _markdown_text(getter(row))
            for _, getter in columns
        ]
        lines.append("| " + " | ".join(values) + " |")
    if len(frame.index) > len(display_frame.index):
        lines.extend(
            [
                "",
                (
                    f"_Showing {len(display_frame.index)} of "
                    f"{len(frame.index)} research-only rows._"
                ),
            ]
        )
    lines.append("")
    return lines


def _render_summary(diagnostics: dict[str, Any]) -> str:
    lines = [
        f"Daily Research Report - {diagnostics['date']}",
        REPORT_NOTICE,
        f"status: {diagnostics['status']}",
        f"safety_status: {diagnostics['safety_status']}",
        (
            "quality_board_row_count: "
            f"{diagnostics['quality_board_row_count']}"
        ),
        (
            "odds_tracker_row_count: "
            f"{diagnostics['odds_tracker_row_count']}"
        ),
        (
            "projection_review_row_count: "
            f"{diagnostics['projection_review_row_count']}"
        ),
        f"total_research_rows: {diagnostics['total_research_rows']}",
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
        (
            "research_watchlist_count: "
            f"{diagnostics['research_watchlist_count']}"
        ),
        f"fair_price_now_count: {diagnostics['fair_price_now_count']}",
        (
            "price_sensitive_watchlist_count: "
            f"{diagnostics['price_sensitive_watchlist_count']}"
        ),
        f"heavy_juice_count: {diagnostics['heavy_juice_count']}",
        f"low_priority_count: {diagnostics['low_priority_count']}",
        (
            "low_confidence_review_count: "
            f"{diagnostics['low_confidence_review_count']}"
        ),
        (
            "excluded_research_only_count: "
            f"{diagnostics['excluded_research_only_count']}"
        ),
        (
            "eligible_for_betting_any_true: "
            f"{diagnostics['eligible_for_betting_any_true']}"
        ),
        "market_prop_rows_created: 0",
        "elite_rows_created: 0",
        "kelly_called: False",
        "operator_betting_boards_written: 0",
        (
            "report_sections_created: "
            + ", ".join(diagnostics["report_sections_created"])
        ),
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
    return "\n".join(lines) + "\n"


def _filter_rows(
    frame: pd.DataFrame,
    column: str,
    values: set[str],
) -> pd.DataFrame:
    if frame.empty or column not in frame.columns:
        return frame.iloc[0:0].copy()
    normalized = frame[column].map(lambda value: _clean_text(value).lower())
    return frame.loc[normalized.isin(values)].copy()


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
    frame: pd.DataFrame,
    required: list[str],
) -> list[str]:
    return [column for column in required if column not in frame.columns]


def _value_counts(
    frame: pd.DataFrame,
    column: str,
) -> dict[str, int]:
    if column not in frame.columns:
        return {}
    counts: dict[str, int] = {}
    for value in frame[column].tolist():
        text = _clean_text(value)
        if text:
            counts[text] = counts.get(text, 0) + 1
    return counts


def _eligible_any_true(frame: pd.DataFrame) -> bool:
    if "eligible_for_betting" not in frame.columns or frame.empty:
        return False
    return any(_truthy(value) for value in frame["eligible_for_betting"])


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if value is False or _is_missing(value):
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _first_value(
    row: pd.Series,
    columns: tuple[str, ...],
) -> Any:
    for column in columns:
        value = row.get(column)
        if not _is_missing(value):
            return value
    return "n/a"


def _format_number(value: Any, decimal_places: int | None = None) -> str:
    if _is_missing(value):
        return "n/a"
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return _clean_text(value) or "n/a"
    if pd.isna(number):
        return "n/a"
    if decimal_places is not None:
        return f"{number:.{decimal_places}f}"
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _markdown_text(value: Any) -> str:
    text = _clean_text(value) or "n/a"
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


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


def _validate_date(value: str) -> str:
    text = str(value).strip()
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("--date must be in YYYY-MM-DD format") from exc
    return text


def _non_negative_int(value: Any, argument_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{argument_name} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{argument_name} must be non-negative")
    return parsed


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the CourtVision research-only daily report bundle."
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
        "--odds-tracker",
        default=None,
        help="Odds improvement tracker CSV. Defaults to output-dir/date.",
    )
    parser.add_argument(
        "--projection-review",
        default=None,
        help="Projection quality review CSV. Defaults to output-dir/date.",
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
    parser.add_argument(
        "--top-n",
        type=int,
        default=DEFAULT_TOP_N,
        help="Maximum rows displayed in each report section.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = build_daily_research_report(
            target_date=args.date,
            quality_board=args.quality_board,
            odds_tracker=args.odds_tracker,
            projection_review=args.projection_review,
            output_dir=args.output_dir,
            diagnostics_dir=args.diagnostics_dir,
            top_n=args.top_n,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"status: {result.status}")
    print(f"report: {result.report_path}")
    print(f"summary: {result.summary_path}")
    print(f"diagnostics: {result.diagnostics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
