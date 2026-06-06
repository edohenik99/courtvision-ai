"""Phase 5F research-only manual review board.

This entrypoint filters and ranks validated Phase 5E edge rows for human
research review. It does not create picks, MarketProp rows, Elite rows, Kelly
inputs, or operator betting boards.
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


MANUAL_REVIEW_OK = "MANUAL_REVIEW_OK"
MANUAL_REVIEW_NO_EDGE_BOARD = "MANUAL_REVIEW_NO_EDGE_BOARD"
MANUAL_REVIEW_NO_VALIDATED_ROWS = "MANUAL_REVIEW_NO_VALIDATED_ROWS"
MANUAL_REVIEW_SCHEMA_INVALID = "MANUAL_REVIEW_SCHEMA_INVALID"

DEFAULT_OUTPUT_DIR = Path("outputs/runtime/research")
DEFAULT_DIAGNOSTICS_DIR = Path("outputs/runtime/diagnostics")
DEFAULT_TOP_N = 50
DEFAULT_DEDUPE_MODE = "best_per_player_market"
DEFAULT_MIN_RANK_SCORE = 0.0

DEDUPE_MODES = (
    "best_per_player_market",
    "best_per_player_market_side",
    "none",
)
BETTING_APPROVAL_STATUS = "research_only_not_betting_approved"
MANUAL_REVIEW_STATUS = "research_only_pending_review"
SUMMARY_NOTICE = (
    "Research-only manual review board. Not betting-approved. No picks created."
)

SOURCE_COLUMNS = [
    "player_name",
    "market_type",
    "side",
    "line",
    "projection_value",
    "side_adjusted_edge",
    "edge_bucket",
    "directional_edge_bucket",
    "sportsbook",
    "american_odds",
    "projection_source_type",
    "projection_quality_flag",
    "research_rank_score",
    "research_rank_reason",
    "game_date",
    "home_team",
    "away_team",
    "commence_time_local",
    "betting_approval_status",
    "eligible_for_betting",
]
REQUIRED_COLUMNS = ["passes_research_validation", *SOURCE_COLUMNS]
REVIEW_FLAG_COLUMNS = [
    "needs_manual_projection_review",
    "needs_line_shop_review",
    "needs_injury_review",
    "needs_minutes_review",
    "manual_review_status",
]
MANUAL_REVIEW_COLUMNS = ["review_rank", *SOURCE_COLUMNS, *REVIEW_FLAG_COLUMNS]


@dataclass(slots=True)
class ManualReviewResult:
    status: str
    output_path: Path
    summary_path: Path
    diagnostics_path: Path
    diagnostics: dict[str, Any]


def run_manual_review_board(
    *,
    target_date: str,
    edge_board: str | Path | None = None,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    diagnostics_dir: str | Path = DEFAULT_DIAGNOSTICS_DIR,
    top_n: int = DEFAULT_TOP_N,
    dedupe_mode: str = DEFAULT_DEDUPE_MODE,
    min_rank_score: float = DEFAULT_MIN_RANK_SCORE,
) -> ManualReviewResult:
    """Build one research-only manual review board."""
    target_date_text = _validate_date(target_date)
    top_n_value = _non_negative_int(top_n, "--top-n")
    min_rank_score_value = _number(min_rank_score, "--min-rank-score")
    dedupe_mode_value = str(dedupe_mode).strip()
    if dedupe_mode_value not in DEDUPE_MODES:
        allowed = ", ".join(DEDUPE_MODES)
        raise ValueError(f"--dedupe-mode must be one of: {allowed}")

    output_dir_path = Path(output_dir)
    diagnostics_dir_path = Path(diagnostics_dir)
    edge_board_path = Path(edge_board) if edge_board else (
        output_dir_path / f"edge_validation_board_{target_date_text}.csv"
    )
    output_path = output_dir_path / f"manual_review_board_{target_date_text}.csv"
    summary_path = output_dir_path / f"manual_review_summary_{target_date_text}.txt"
    diagnostics_path = diagnostics_dir_path / f"manual_review_{target_date_text}.json"

    output_dir_path.mkdir(parents=True, exist_ok=True)
    diagnostics_dir_path.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    input_row_count = 0
    research_validated_input_count = 0
    schema_missing_columns: list[str] = []

    if not edge_board_path.exists():
        status = MANUAL_REVIEW_NO_EDGE_BOARD
        board = _empty_manual_review_frame()
        warnings.append(f"Edge validation board not found: {edge_board_path}")
    else:
        source_df, read_error = _read_csv(edge_board_path)
        input_row_count = int(len(source_df.index))
        schema_missing_columns = [
            column for column in REQUIRED_COLUMNS if column not in source_df.columns
        ]

        if read_error:
            status = MANUAL_REVIEW_SCHEMA_INVALID
            board = _empty_manual_review_frame()
            warnings.append(read_error)
        elif schema_missing_columns:
            status = MANUAL_REVIEW_SCHEMA_INVALID
            board = _empty_manual_review_frame()
            warnings.append(
                "Edge validation board is missing required columns: "
                + ", ".join(schema_missing_columns)
            )
        else:
            validated_df = source_df[
                source_df["passes_research_validation"].map(_truthy)
            ].copy()
            research_validated_input_count = int(len(validated_df.index))
            if research_validated_input_count == 0:
                status = MANUAL_REVIEW_NO_VALIDATED_ROWS
                board = _empty_manual_review_frame()
                warnings.append(
                    "Edge validation board contains no research-validated rows."
                )
            else:
                status = MANUAL_REVIEW_OK
                board = _build_manual_review_board(
                    validated_df,
                    dedupe_mode=dedupe_mode_value,
                    top_n=top_n_value,
                    min_rank_score=min_rank_score_value,
                    warnings=warnings,
                )

    diagnostics = _diagnostics_payload(
        target_date=target_date_text,
        status=status,
        edge_board_path=edge_board_path,
        board=board,
        input_row_count=input_row_count,
        research_validated_input_count=research_validated_input_count,
        dedupe_mode=dedupe_mode_value,
        top_n=top_n_value,
        min_rank_score=min_rank_score_value,
        schema_missing_columns=schema_missing_columns,
        warnings=warnings,
        output_path=output_path,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
    )
    _write_outputs(output_path, summary_path, diagnostics_path, board, diagnostics)
    return ManualReviewResult(
        status, output_path, summary_path, diagnostics_path, diagnostics
    )


def _build_manual_review_board(
    validated_df: pd.DataFrame,
    *,
    dedupe_mode: str,
    top_n: int,
    min_rank_score: float,
    warnings: list[str],
) -> pd.DataFrame:
    board = validated_df.copy()
    board["_source_position"] = range(len(board.index))
    board["_rank_score"] = pd.to_numeric(
        board["research_rank_score"], errors="coerce"
    )
    board["_directional_edge"] = pd.to_numeric(
        board["side_adjusted_edge"], errors="coerce"
    )

    invalid_rank_count = int(board["_rank_score"].isna().sum())
    if invalid_rank_count:
        warnings.append(
            f"Excluded {invalid_rank_count} validated rows with invalid research_rank_score."
        )
        board = board[board["_rank_score"].notna()].copy()

    below_minimum_count = int((board["_rank_score"] < min_rank_score).sum())
    if below_minimum_count:
        warnings.append(
            f"Excluded {below_minimum_count} validated rows below min rank score "
            f"{min_rank_score:g}."
        )
        board = board[board["_rank_score"] >= min_rank_score].copy()

    board = board.sort_values(
        by=["_rank_score", "_directional_edge", "_source_position"],
        ascending=[False, False, True],
        kind="mergesort",
        na_position="last",
    )

    dedupe_columns: list[str] = []
    if dedupe_mode == "best_per_player_market":
        dedupe_columns = ["player_name", "market_type"]
    elif dedupe_mode == "best_per_player_market_side":
        dedupe_columns = ["player_name", "market_type", "side"]
    if dedupe_columns:
        board = board.drop_duplicates(subset=dedupe_columns, keep="first")

    board = board.head(top_n).copy()
    board["review_rank"] = range(1, len(board.index) + 1)
    board["betting_approval_status"] = BETTING_APPROVAL_STATUS
    board["eligible_for_betting"] = False
    board["needs_manual_projection_review"] = True
    board["needs_line_shop_review"] = True
    board["needs_injury_review"] = True
    board["needs_minutes_review"] = True
    board["manual_review_status"] = MANUAL_REVIEW_STATUS
    return board.loc[:, MANUAL_REVIEW_COLUMNS].reset_index(drop=True)


def _diagnostics_payload(
    *,
    target_date: str,
    status: str,
    edge_board_path: Path,
    board: pd.DataFrame,
    input_row_count: int,
    research_validated_input_count: int,
    dedupe_mode: str,
    top_n: int,
    min_rank_score: float,
    schema_missing_columns: list[str],
    warnings: list[str],
    output_path: Path,
    summary_path: Path,
    diagnostics_path: Path,
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": target_date,
        "status": status,
        "edge_board_path": str(edge_board_path),
        "input_row_count": int(input_row_count),
        "research_validated_input_count": int(research_validated_input_count),
        "output_row_count": int(len(board.index)),
        "dedupe_mode": dedupe_mode,
        "top_n": top_n,
        "min_rank_score": min_rank_score,
        "unique_player_count": _unique_count(board, "player_name"),
        "unique_market_count": _unique_count(board, "market_type"),
        "unique_sportsbook_count": _unique_count(board, "sportsbook"),
        "market_counts": _value_counts(board, "market_type"),
        "side_counts": _value_counts(board, "side"),
        "projection_source_type_counts": _value_counts(
            board, "projection_source_type"
        ),
        "edge_bucket_counts": _value_counts(board, "edge_bucket"),
        "eligible_for_betting_any_true": _eligible_any_true(board),
        "market_prop_rows_created": 0,
        "elite_rows_created": 0,
        "kelly_called": False,
        "operator_betting_boards_written": [],
        "schema_missing_required_columns": schema_missing_columns,
        "warnings": warnings,
        "artifacts": {
            "manual_review_board_csv": str(output_path),
            "manual_review_summary_txt": str(summary_path),
            "manual_review_diagnostics_json": str(diagnostics_path),
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
        f"Manual Review Board - {diagnostics['date']}",
        SUMMARY_NOTICE,
        f"status: {diagnostics['status']}",
        f"edge_board: {diagnostics['edge_board_path']}",
        f"input_row_count: {diagnostics['input_row_count']}",
        (
            "research_validated_input_count: "
            f"{diagnostics['research_validated_input_count']}"
        ),
        f"output_row_count: {diagnostics['output_row_count']}",
        f"dedupe_mode: {diagnostics['dedupe_mode']}",
        f"top_n: {diagnostics['top_n']}",
        f"min_rank_score: {diagnostics['min_rank_score']}",
        f"unique_player_count: {diagnostics['unique_player_count']}",
        f"unique_market_count: {diagnostics['unique_market_count']}",
        f"unique_sportsbook_count: {diagnostics['unique_sportsbook_count']}",
        f"market_counts: {_counts_inline(diagnostics['market_counts'])}",
        f"side_counts: {_counts_inline(diagnostics['side_counts'])}",
        (
            "projection_source_type_counts: "
            f"{_counts_inline(diagnostics['projection_source_type_counts'])}"
        ),
        f"edge_bucket_counts: {_counts_inline(diagnostics['edge_bucket_counts'])}",
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


def _empty_manual_review_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=MANUAL_REVIEW_COLUMNS)


def _read_csv(path: Path) -> tuple[pd.DataFrame, str]:
    try:
        return pd.read_csv(path), ""
    except pd.errors.EmptyDataError:
        return pd.DataFrame(), f"Edge validation board is empty: {path}"
    except Exception as exc:
        return (
            pd.DataFrame(),
            f"Could not read edge validation board {path}: "
            f"{type(exc).__name__}: {exc}",
        )


def _unique_count(df: pd.DataFrame, column: str) -> int:
    if column not in df.columns:
        return 0
    return len({_clean_text(value) for value in df[column].tolist()} - {""})


def _value_counts(df: pd.DataFrame, column: str) -> dict[str, int]:
    if column not in df.columns:
        return {}
    counts: dict[str, int] = {}
    for raw_value in df[column].tolist():
        value = _clean_text(raw_value)
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


def _eligible_any_true(df: pd.DataFrame) -> bool:
    if "eligible_for_betting" not in df.columns or df.empty:
        return False
    return any(_truthy(value) for value in df["eligible_for_betting"].tolist())


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return bool(value) and not pd.isna(value)
        except TypeError:
            return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


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


def _number(value: Any, argument_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{argument_name} must be a number") from exc


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
        description="Build a CourtVision research-only manual review board."
    )
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")
    parser.add_argument(
        "--edge-board",
        default=None,
        help="Edge validation CSV. Defaults to output-dir/date naming.",
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
        "--top-n",
        type=int,
        default=DEFAULT_TOP_N,
        help="Maximum manual review rows to write.",
    )
    parser.add_argument(
        "--dedupe-mode",
        choices=DEDUPE_MODES,
        default=DEFAULT_DEDUPE_MODE,
        help="Manual review dedupe strategy.",
    )
    parser.add_argument(
        "--min-rank-score",
        type=float,
        default=DEFAULT_MIN_RANK_SCORE,
        help="Minimum research rank score to include.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = run_manual_review_board(
            target_date=args.date,
            edge_board=args.edge_board,
            output_dir=args.output_dir,
            diagnostics_dir=args.diagnostics_dir,
            top_n=args.top_n,
            dedupe_mode=args.dedupe_mode,
            min_rank_score=args.min_rank_score,
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
