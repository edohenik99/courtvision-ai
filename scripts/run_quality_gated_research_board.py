"""Phase 6E research-only quality-gated review board.

This entrypoint organizes projection quality review rows for final human
research. It does not create picks, MarketProp rows, Elite rows, Kelly inputs,
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


QUALITY_GATED_RESEARCH_OK = "QUALITY_GATED_RESEARCH_OK"
QUALITY_GATED_RESEARCH_INPUT_MISSING = (
    "QUALITY_GATED_RESEARCH_INPUT_MISSING"
)
QUALITY_GATED_RESEARCH_SCHEMA_INVALID = (
    "QUALITY_GATED_RESEARCH_SCHEMA_INVALID"
)
QUALITY_GATED_RESEARCH_NO_OUTPUT_ROWS = (
    "QUALITY_GATED_RESEARCH_NO_OUTPUT_ROWS"
)

DEFAULT_OUTPUT_DIR = Path("outputs/runtime/research")
DEFAULT_DIAGNOSTICS_DIR = Path("outputs/runtime/diagnostics")
DEFAULT_TOP_N = 25

RESEARCH_WATCHLIST = "research_watchlist"
PRICE_SENSITIVE_WATCHLIST = "price_sensitive_watchlist"
LOW_CONFIDENCE_REVIEW = "low_confidence_review"
EXCLUDED_RESEARCH_ONLY = "excluded_research_only"

HIGH_RESEARCH_CONFIDENCE = "high_research_confidence"
MEDIUM_RESEARCH_CONFIDENCE = "medium_research_confidence"
LOW_RESEARCH_CONFIDENCE = "low_research_confidence"
MANUAL_REVIEW_ONLY = "manual_review_only"

FAVORABLE_OR_FAIR = "favorable_or_fair"
HEAVY_JUICE = "heavy_juice"
STABLE_MINUTES = "stable_minutes"
STRONG_EDGE_LINE = "strong_edge_line"
REVIEW_EDGE_LINE = "review_edge_line"
WEAK_EDGE_LINE = "weak_edge_line"

BETTING_APPROVAL_STATUS = "research_only_not_betting_approved"
SUMMARY_NOTICE = (
    "Research-only quality-gated board. Not betting-approved. No picks created."
)

REQUIRED_COLUMNS = [
    "projection_value",
    "side_adjusted_edge",
    "american_odds",
    "review_rank",
    "projection_confidence_tier",
    "review_warning_flags",
    "odds_quality_flag",
    "line_quality_flag",
    "minutes_quality_flag",
    "eligible_for_betting",
]
ADDED_COLUMNS = [
    "research_board_rank",
    "research_category",
    "price_warning",
    "final_research_note",
    "betting_approval_status",
    "eligible_for_betting",
]

CATEGORY_PRIORITY = {
    RESEARCH_WATCHLIST: 0,
    PRICE_SENSITIVE_WATCHLIST: 1,
    LOW_CONFIDENCE_REVIEW: 2,
    EXCLUDED_RESEARCH_ONLY: 3,
}
CONFIDENCE_PRIORITY = {
    HIGH_RESEARCH_CONFIDENCE: 0,
    MEDIUM_RESEARCH_CONFIDENCE: 1,
    LOW_RESEARCH_CONFIDENCE: 2,
    MANUAL_REVIEW_ONLY: 3,
}
ODDS_PRIORITY = {
    FAVORABLE_OR_FAIR: 0,
    HEAVY_JUICE: 1,
    "missing_odds": 2,
}
SCHEMA_WARNING_FLAGS = {
    "input_schema_issue",
    "join_row_not_found",
    "missing_line",
    "missing_side_adjusted_edge",
    "missing_projection_quality_flag",
    "missing_player_name",
    "missing_market_type",
    "missing_side",
    "missing_sportsbook",
}


@dataclass(slots=True)
class QualityGatedResearchResult:
    status: str
    output_path: Path
    summary_path: Path
    diagnostics_path: Path
    diagnostics: dict[str, Any]


def run_quality_gated_research_board(
    *,
    target_date: str,
    quality_review: str | Path | None = None,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    diagnostics_dir: str | Path = DEFAULT_DIAGNOSTICS_DIR,
    include_heavy_juice_review: bool = False,
    top_n: int = DEFAULT_TOP_N,
) -> QualityGatedResearchResult:
    """Build one final research-only quality-gated board."""
    target_date_text = _validate_date(target_date)
    top_n_value = _non_negative_int(top_n, "--top-n")
    output_dir_path = Path(output_dir)
    diagnostics_dir_path = Path(diagnostics_dir)
    quality_review_path = Path(quality_review) if quality_review else (
        output_dir_path / f"projection_quality_review_{target_date_text}.csv"
    )
    output_path = output_dir_path / (
        f"quality_gated_research_board_{target_date_text}.csv"
    )
    summary_path = output_dir_path / (
        f"quality_gated_research_summary_{target_date_text}.txt"
    )
    diagnostics_path = diagnostics_dir_path / (
        f"quality_gated_research_{target_date_text}.json"
    )

    output_dir_path.mkdir(parents=True, exist_ok=True)
    diagnostics_dir_path.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    source_df = pd.DataFrame()
    schema_missing_columns: list[str] = []
    source_eligible_for_betting_any_true = False

    if not quality_review_path.exists():
        status = QUALITY_GATED_RESEARCH_INPUT_MISSING
        board = _empty_board()
        warnings.append(
            f"Projection quality review not found: {quality_review_path}"
        )
    else:
        source_df, read_error = _read_csv(quality_review_path)
        schema_missing_columns = _missing_columns(source_df, REQUIRED_COLUMNS)
        source_eligible_for_betting_any_true = _eligible_any_true(source_df)
        if source_eligible_for_betting_any_true:
            warnings.append(
                "Input had eligible_for_betting truthy values; board output "
                "was forced false."
            )

        if read_error:
            status = QUALITY_GATED_RESEARCH_SCHEMA_INVALID
            board = _empty_board()
            warnings.append(read_error)
        else:
            if schema_missing_columns:
                warnings.append(
                    "Projection quality review is missing required columns: "
                    + ", ".join(schema_missing_columns)
                )
            board = _build_board(
                source_df,
                include_heavy_juice_review=include_heavy_juice_review,
                top_n=top_n_value,
                has_schema_issue=bool(schema_missing_columns),
            )
            if schema_missing_columns:
                status = QUALITY_GATED_RESEARCH_SCHEMA_INVALID
            elif board.empty:
                status = QUALITY_GATED_RESEARCH_NO_OUTPUT_ROWS
            else:
                status = QUALITY_GATED_RESEARCH_OK

    diagnostics = _diagnostics_payload(
        target_date=target_date_text,
        status=status,
        quality_review_path=quality_review_path,
        source_df=source_df,
        board=board,
        include_heavy_juice_review=include_heavy_juice_review,
        top_n=top_n_value,
        source_eligible_for_betting_any_true=(
            source_eligible_for_betting_any_true
        ),
        schema_missing_columns=schema_missing_columns,
        warnings=warnings,
        output_path=output_path,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
    )
    _write_outputs(output_path, summary_path, diagnostics_path, board, diagnostics)
    return QualityGatedResearchResult(
        status=status,
        output_path=output_path,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
        diagnostics=diagnostics,
    )


def _build_board(
    source_df: pd.DataFrame,
    *,
    include_heavy_juice_review: bool,
    top_n: int,
    has_schema_issue: bool,
) -> pd.DataFrame:
    if source_df.empty:
        return _empty_board(source_df.columns.tolist())

    board = source_df.copy()
    source_columns = board.columns.tolist()
    for column in REQUIRED_COLUMNS:
        if column not in board.columns:
            board[column] = pd.NA

    board["_source_position"] = range(len(board.index))
    categories: list[str] = []
    price_warnings: list[str] = []
    final_notes: list[str] = []

    for _, row in board.iterrows():
        category, exclusion_reasons = _research_category(
            row,
            include_heavy_juice_review=include_heavy_juice_review,
            has_schema_issue=has_schema_issue,
        )
        categories.append(category)
        price_warnings.append(_price_warning(row))
        final_notes.append(_final_research_note(category, exclusion_reasons))

    board["research_category"] = categories
    board["price_warning"] = price_warnings
    board["final_research_note"] = final_notes
    board["betting_approval_status"] = BETTING_APPROVAL_STATUS
    board["eligible_for_betting"] = False

    board["_category_priority"] = board["research_category"].map(
        CATEGORY_PRIORITY
    ).fillna(len(CATEGORY_PRIORITY))
    board["_confidence_priority"] = board[
        "projection_confidence_tier"
    ].map(lambda value: CONFIDENCE_PRIORITY.get(
        _clean_text(value).lower(),
        len(CONFIDENCE_PRIORITY),
    ))
    board["_edge_rank"] = pd.to_numeric(
        board["side_adjusted_edge"],
        errors="coerce",
    )
    board["_odds_priority"] = board["odds_quality_flag"].map(
        lambda value: ODDS_PRIORITY.get(
            _clean_text(value).lower(),
            len(ODDS_PRIORITY),
        )
    )
    board["_review_rank"] = pd.to_numeric(
        board["review_rank"],
        errors="coerce",
    )
    board = board.sort_values(
        by=[
            "_category_priority",
            "_confidence_priority",
            "_edge_rank",
            "_odds_priority",
            "_review_rank",
            "_source_position",
        ],
        ascending=[True, True, False, True, True, True],
        kind="mergesort",
        na_position="last",
    ).head(top_n).copy()
    board["research_board_rank"] = range(1, len(board.index) + 1)

    output_columns = [
        "research_board_rank",
        *[
            column
            for column in source_columns
            if column not in ADDED_COLUMNS
        ],
        "research_category",
        "price_warning",
        "final_research_note",
        "betting_approval_status",
        "eligible_for_betting",
    ]
    return board.loc[:, output_columns].reset_index(drop=True)


def _research_category(
    row: pd.Series,
    *,
    include_heavy_juice_review: bool,
    has_schema_issue: bool,
) -> tuple[str, list[str]]:
    confidence = _clean_text(row.get("projection_confidence_tier")).lower()
    odds_quality = _clean_text(row.get("odds_quality_flag")).lower()
    line_quality = _clean_text(row.get("line_quality_flag")).lower()
    minutes_quality = _clean_text(row.get("minutes_quality_flag")).lower()
    exclusion_reasons = _exclusion_reasons(
        row,
        has_schema_issue=has_schema_issue,
    )
    if exclusion_reasons:
        return EXCLUDED_RESEARCH_ONLY, exclusion_reasons

    if (
        confidence == LOW_RESEARCH_CONFIDENCE
        or minutes_quality != STABLE_MINUTES
    ):
        return LOW_CONFIDENCE_REVIEW, []

    if (
        confidence == HIGH_RESEARCH_CONFIDENCE
        and odds_quality == FAVORABLE_OR_FAIR
        and line_quality in {STRONG_EDGE_LINE, REVIEW_EDGE_LINE}
        and minutes_quality == STABLE_MINUTES
    ):
        return RESEARCH_WATCHLIST, []

    if (
        include_heavy_juice_review
        and confidence in {
            HIGH_RESEARCH_CONFIDENCE,
            MEDIUM_RESEARCH_CONFIDENCE,
        }
        and odds_quality == HEAVY_JUICE
        and line_quality != WEAK_EDGE_LINE
    ):
        return PRICE_SENSITIVE_WATCHLIST, []

    if odds_quality == HEAVY_JUICE and not include_heavy_juice_review:
        exclusion_reasons.append("heavy_juice_review_not_enabled")
    else:
        exclusion_reasons.append("quality_gate_not_met")
    return EXCLUDED_RESEARCH_ONLY, exclusion_reasons


def _exclusion_reasons(
    row: pd.Series,
    *,
    has_schema_issue: bool,
) -> list[str]:
    confidence = _clean_text(row.get("projection_confidence_tier")).lower()
    odds_quality = _clean_text(row.get("odds_quality_flag")).lower()
    line_quality = _clean_text(row.get("line_quality_flag")).lower()
    warning_flags = {
        flag.strip().lower()
        for flag in _clean_text(row.get("review_warning_flags")).split("|")
        if flag.strip() and flag.strip().lower() != "none"
    }
    reasons: list[str] = []

    if (
        has_schema_issue
        or warning_flags.intersection(SCHEMA_WARNING_FLAGS)
        or any("schema_issue" in flag for flag in warning_flags)
    ):
        reasons.append("schema_issue")
    if _coerce_number(row.get("projection_value")) is None or (
        "missing_projection" in warning_flags
    ):
        reasons.append("missing_projection")
    if (
        _coerce_number(row.get("american_odds")) is None
        or odds_quality == "missing_odds"
        or "missing_odds" in warning_flags
    ):
        reasons.append("missing_odds")
    if (
        _coerce_number(row.get("side_adjusted_edge")) is None
        or line_quality == "missing_edge"
    ):
        reasons.append("missing_edge")
    elif line_quality == WEAK_EDGE_LINE:
        reasons.append(WEAK_EDGE_LINE)
    if confidence == MANUAL_REVIEW_ONLY:
        reasons.append(MANUAL_REVIEW_ONLY)
    return list(dict.fromkeys(reasons))


def _price_warning(row: pd.Series) -> str:
    odds_quality = _clean_text(row.get("odds_quality_flag")).lower()
    if odds_quality == HEAVY_JUICE:
        return "heavy_juice_price_warning"
    if (
        odds_quality == "missing_odds"
        or _coerce_number(row.get("american_odds")) is None
    ):
        return "missing_odds_price_warning"
    return "none"


def _final_research_note(
    category: str,
    exclusion_reasons: list[str],
) -> str:
    if category == RESEARCH_WATCHLIST:
        return (
            "High-confidence projection with acceptable price, line, and "
            "stable minutes. Research review only."
        )
    if category == PRICE_SENSITIVE_WATCHLIST:
        return (
            "Projection merits research review, but heavy juice makes the "
            "price unattractive. Research review only."
        )
    if category == LOW_CONFIDENCE_REVIEW:
        return (
            "Projection or minutes context needs lower-confidence research "
            "review. Not betting-approved."
        )
    reasons = "|".join(exclusion_reasons) if exclusion_reasons else "excluded"
    return (
        f"Excluded from research watchlists: {reasons}. "
        "Not betting-approved."
    )


def _diagnostics_payload(
    *,
    target_date: str,
    status: str,
    quality_review_path: Path,
    source_df: pd.DataFrame,
    board: pd.DataFrame,
    include_heavy_juice_review: bool,
    top_n: int,
    source_eligible_for_betting_any_true: bool,
    schema_missing_columns: list[str],
    warnings: list[str],
    output_path: Path,
    summary_path: Path,
    diagnostics_path: Path,
) -> dict[str, Any]:
    category_counts = _value_counts(board, "research_category")
    confidence_tier_counts = _value_counts(
        board,
        "projection_confidence_tier",
    )
    odds_quality_flag_counts = _value_counts(board, "odds_quality_flag")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": target_date,
        "target_date": target_date,
        "status": status,
        "quality_review_path": str(quality_review_path),
        "input_row_count": int(len(source_df.index)),
        "output_row_count": int(len(board.index)),
        "top_n": top_n,
        "include_heavy_juice_review": bool(include_heavy_juice_review),
        "category_counts": category_counts,
        "confidence_tier_counts": confidence_tier_counts,
        "odds_quality_flag_counts": odds_quality_flag_counts,
        "line_quality_flag_counts": _value_counts(
            board,
            "line_quality_flag",
        ),
        "minutes_quality_flag_counts": _value_counts(
            board,
            "minutes_quality_flag",
        ),
        "heavy_juice_count": odds_quality_flag_counts.get(HEAVY_JUICE, 0),
        "favorable_or_fair_count": odds_quality_flag_counts.get(
            FAVORABLE_OR_FAIR,
            0,
        ),
        "research_watchlist_count": category_counts.get(
            RESEARCH_WATCHLIST,
            0,
        ),
        "price_sensitive_watchlist_count": category_counts.get(
            PRICE_SENSITIVE_WATCHLIST,
            0,
        ),
        "low_confidence_review_count": category_counts.get(
            LOW_CONFIDENCE_REVIEW,
            0,
        ),
        "excluded_research_only_count": category_counts.get(
            EXCLUDED_RESEARCH_ONLY,
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
            "quality_gated_research_board_csv": str(output_path),
            "quality_gated_research_summary_txt": str(summary_path),
            "quality_gated_research_diagnostics_json": str(
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
        f"Quality-Gated Research Board - {diagnostics['date']}",
        SUMMARY_NOTICE,
        f"status: {diagnostics['status']}",
        f"quality_review: {diagnostics['quality_review_path']}",
        f"input_row_count: {diagnostics['input_row_count']}",
        f"output_row_count: {diagnostics['output_row_count']}",
        f"top_n: {diagnostics['top_n']}",
        (
            "include_heavy_juice_review: "
            f"{diagnostics['include_heavy_juice_review']}"
        ),
        (
            "category_counts: "
            f"{_counts_inline(diagnostics['category_counts'])}"
        ),
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
        f"heavy_juice_count: {diagnostics['heavy_juice_count']}",
        f"favorable_or_fair_count: {diagnostics['favorable_or_fair_count']}",
        (
            "research_watchlist_count: "
            f"{diagnostics['research_watchlist_count']}"
        ),
        (
            "price_sensitive_watchlist_count: "
            f"{diagnostics['price_sensitive_watchlist_count']}"
        ),
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
    ]
    if diagnostics["warnings"]:
        lines.extend(
            ["warnings:", *[f"  {warning}" for warning in diagnostics["warnings"]]]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _empty_board(source_columns: list[str] | None = None) -> pd.DataFrame:
    columns = [
        "research_board_rank",
        *(source_columns or REQUIRED_COLUMNS),
        "research_category",
        "price_warning",
        "final_research_note",
        "betting_approval_status",
        "eligible_for_betting",
    ]
    return pd.DataFrame(columns=list(dict.fromkeys(columns)))


def _read_csv(path: Path) -> tuple[pd.DataFrame, str]:
    try:
        return pd.read_csv(path, low_memory=False), ""
    except pd.errors.EmptyDataError:
        return pd.DataFrame(), f"Projection quality review is empty: {path}"
    except Exception as exc:
        return (
            pd.DataFrame(),
            f"Could not read projection quality review {path}: "
            f"{type(exc).__name__}: {exc}",
        )


def _missing_columns(df: pd.DataFrame, required: list[str]) -> list[str]:
    return [column for column in required if column not in df.columns]


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
        description="Build a CourtVision quality-gated research-only board."
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Target date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--quality-review",
        default=None,
        help="Projection quality review CSV. Defaults to output-dir/date naming.",
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
    parser.add_argument(
        "--include-heavy-juice-review",
        action="store_true",
        help="Admit qualified heavy-juice rows to the price-sensitive watchlist.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=DEFAULT_TOP_N,
        help="Maximum ranked research rows to write.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = run_quality_gated_research_board(
            target_date=args.date,
            quality_review=args.quality_review,
            output_dir=args.output_dir,
            diagnostics_dir=args.diagnostics_dir,
            include_heavy_juice_review=args.include_heavy_juice_review,
            top_n=args.top_n,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"status: {result.status}")
    print(f"board: {result.output_path}")
    print(f"summary: {result.summary_path}")
    print(f"diagnostics: {result.diagnostics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
