"""Phase 5E research-only edge validation board.

This entrypoint validates and ranks rows from the Phase 5D market/projection
join. It does not create picks, MarketProp rows, Elite rows, Kelly inputs, or
operator betting boards.
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


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


EDGE_VALIDATION_OK = "EDGE_VALIDATION_OK"
EDGE_VALIDATION_NO_JOIN_BOARD = "EDGE_VALIDATION_NO_JOIN_BOARD"
EDGE_VALIDATION_SCHEMA_INVALID = "EDGE_VALIDATION_SCHEMA_INVALID"
EDGE_VALIDATION_NO_RESEARCH_VALIDATED_ROWS = (
    "EDGE_VALIDATION_NO_RESEARCH_VALIDATED_ROWS"
)

DEFAULT_OUTPUT_DIR = Path("outputs/runtime/research")
DEFAULT_DIAGNOSTICS_DIR = Path("outputs/runtime/diagnostics")
DEFAULT_MIN_EDGE = 1.5
DEFAULT_ALLOWED_SOURCE_TYPES = "recent_avg_fallback,model_projection"
DEFAULT_MIN_MINUTES = 20.0
DEFAULT_MAX_EVENTS = 1

REQUIRED_COLUMNS = [
    "projection_value",
    "american_odds",
    "sportsbook",
    "player_name",
    "abs_edge",
    "side_adjusted_edge",
    "projection_source_type",
]
MARKET_LINE_COLUMNS = ["market_line", "line"]
MINUTES_CONTEXT_COLUMNS = ["projection_min_avg", "min_avg"]

VALIDATION_COLUMNS = [
    "has_projection_value",
    "has_market_line",
    "has_american_odds",
    "has_sportsbook",
    "has_player_name",
    "has_minutes_context",
    "passes_min_edge",
    "passes_directional_edge",
    "directional_edge_bucket",
    "passes_projection_source",
    "passes_minutes_context",
    "passes_basic_schema",
    "passes_research_validation",
    "research_rejection_reasons",
    "research_rank_score",
    "research_rank_reason",
    "edge_rank_within_market",
    "edge_rank_overall",
    "betting_approval_status",
]

BETTING_APPROVAL_STATUS = "research_only_not_betting_approved"


@dataclass(slots=True)
class EdgeValidationResult:
    status: str
    output_path: Path
    summary_path: Path
    diagnostics_path: Path
    diagnostics: dict[str, Any]


def run_edge_validation(
    *,
    target_date: str,
    join_board: str | Path | None = None,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    diagnostics_dir: str | Path = DEFAULT_DIAGNOSTICS_DIR,
    min_edge: float = DEFAULT_MIN_EDGE,
    allowed_source_types: str | list[str] | tuple[str, ...] = (
        DEFAULT_ALLOWED_SOURCE_TYPES
    ),
    min_minutes: float = DEFAULT_MIN_MINUTES,
    max_events: int = DEFAULT_MAX_EVENTS,
) -> EdgeValidationResult:
    """Run the research-only edge validation board for one date."""
    target_date_text = _validate_date(target_date)
    min_edge_value = _non_negative_float(min_edge, "--min-edge")
    min_minutes_value = _non_negative_float(min_minutes, "--min-minutes")
    max_events_value = _non_negative_int(max_events, "--max-events")
    allowed_sources = _csv_items(allowed_source_types)
    allowed_source_set = {source.lower() for source in allowed_sources}
    if not allowed_source_set:
        raise ValueError("--allowed-source-types must include at least one value")

    output_dir_path = Path(output_dir)
    diagnostics_dir_path = Path(diagnostics_dir)
    join_board_path = Path(join_board) if join_board else (
        output_dir_path / f"market_projection_join_{target_date_text}.csv"
    )
    output_path = output_dir_path / f"edge_validation_board_{target_date_text}.csv"
    summary_path = output_dir_path / f"edge_validation_summary_{target_date_text}.txt"
    diagnostics_path = diagnostics_dir_path / f"edge_validation_{target_date_text}.json"

    output_dir_path.mkdir(parents=True, exist_ok=True)
    diagnostics_dir_path.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    if not join_board_path.exists():
        board = _empty_validation_frame()
        status = EDGE_VALIDATION_NO_JOIN_BOARD
        warnings.append(f"Market projection join board not found: {join_board_path}")
        diagnostics = _diagnostics_payload(
            target_date=target_date_text,
            status=status,
            join_board_path=join_board_path,
            board=board,
            input_row_count=0,
            source_eligible_for_betting_any_true=False,
            schema_missing_columns=REQUIRED_COLUMNS + ["market_line or line"],
            allowed_source_types=allowed_sources,
            min_edge=min_edge_value,
            min_minutes=min_minutes_value,
            max_events=max_events_value,
            rejected_reason_counts={},
            warnings=warnings,
            output_path=output_path,
            summary_path=summary_path,
            diagnostics_path=diagnostics_path,
        )
        _write_outputs(output_path, summary_path, diagnostics_path, board, diagnostics)
        return EdgeValidationResult(
            status, output_path, summary_path, diagnostics_path, diagnostics
        )

    source_df = _read_csv(join_board_path, warnings)
    input_row_count = int(len(source_df.index))
    source_eligible_for_betting_any_true = _eligible_any_true(source_df)
    schema_missing_columns = _schema_missing_columns(source_df)

    limited_df = _limit_events(
        source_df,
        max_events=max_events_value,
        warnings=warnings,
    )
    board, rejected_reason_counts = _build_validation_board(
        limited_df,
        min_edge=min_edge_value,
        allowed_source_types=allowed_source_set,
        min_minutes=min_minutes_value,
    )

    if source_eligible_for_betting_any_true:
        warnings.append(
            "Join board had eligible_for_betting truthy values; validation output was forced false."
        )

    validated_count = _true_count(board, "passes_research_validation")
    if schema_missing_columns:
        status = EDGE_VALIDATION_SCHEMA_INVALID
    elif validated_count == 0:
        status = EDGE_VALIDATION_NO_RESEARCH_VALIDATED_ROWS
    else:
        status = EDGE_VALIDATION_OK

    diagnostics = _diagnostics_payload(
        target_date=target_date_text,
        status=status,
        join_board_path=join_board_path,
        board=board,
        input_row_count=input_row_count,
        source_eligible_for_betting_any_true=source_eligible_for_betting_any_true,
        schema_missing_columns=schema_missing_columns,
        allowed_source_types=allowed_sources,
        min_edge=min_edge_value,
        min_minutes=min_minutes_value,
        max_events=max_events_value,
        rejected_reason_counts=rejected_reason_counts,
        warnings=warnings,
        output_path=output_path,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
    )
    _write_outputs(output_path, summary_path, diagnostics_path, board, diagnostics)
    return EdgeValidationResult(
        status, output_path, summary_path, diagnostics_path, diagnostics
    )


def _build_validation_board(
    source_df: pd.DataFrame,
    *,
    min_edge: float,
    allowed_source_types: set[str],
    min_minutes: float,
) -> tuple[pd.DataFrame, dict[str, int]]:
    board = source_df.copy()
    for column in VALIDATION_COLUMNS:
        board[column] = pd.NA

    board["eligible_for_betting"] = False
    board["betting_approval_status"] = BETTING_APPROVAL_STATUS
    rejected_reason_counts: dict[str, int] = {}

    row_payloads: list[dict[str, Any]] = []
    for source_position, (_, row) in enumerate(board.iterrows()):
        projection_value = _coerce_number(row.get("projection_value"))
        market_line = _market_line(row)
        american_odds = _coerce_number(row.get("american_odds"))
        sportsbook = _clean_text(row.get("sportsbook"))
        player_name = _clean_text(row.get("player_name"))
        abs_edge = _coerce_number(row.get("abs_edge"))
        side_adjusted_edge = _coerce_number(row.get("side_adjusted_edge"))
        source_type = _clean_text(row.get("projection_source_type")).lower()
        minutes_value = _minutes_context_value(row)
        directional_edge_bucket = _directional_edge_bucket(side_adjusted_edge)

        has_projection_value = projection_value is not None
        has_market_line = market_line is not None
        has_american_odds = american_odds is not None
        has_sportsbook = bool(sportsbook)
        has_player_name = bool(player_name)
        has_minutes_context = minutes_value is not None
        passes_min_edge = abs_edge is not None and abs(abs_edge) >= min_edge
        passes_directional_edge = (
            side_adjusted_edge is not None and side_adjusted_edge >= min_edge
        )
        passes_projection_source = source_type in allowed_source_types
        passes_minutes_context = (
            minutes_value is None or minutes_value >= min_minutes
        )
        passes_basic_schema = all(
            [
                has_projection_value,
                has_market_line,
                has_american_odds,
                has_sportsbook,
                has_player_name,
            ]
        )
        passes_research_validation = all(
            [
                passes_basic_schema,
                passes_projection_source,
                passes_minutes_context,
                passes_directional_edge,
            ]
        )

        rejection_reasons = _rejection_reasons(
            has_projection_value=has_projection_value,
            has_market_line=has_market_line,
            has_american_odds=has_american_odds,
            has_sportsbook=has_sportsbook,
            has_player_name=has_player_name,
            abs_edge=abs_edge,
            passes_min_edge=passes_min_edge,
            side_adjusted_edge=side_adjusted_edge,
            passes_directional_edge=passes_directional_edge,
            source_type=source_type,
            passes_projection_source=passes_projection_source,
            has_minutes_context=has_minutes_context,
            passes_minutes_context=passes_minutes_context,
        )
        for reason in rejection_reasons:
            rejected_reason_counts[reason] = rejected_reason_counts.get(reason, 0) + 1

        rank_score, rank_reason = _research_rank(
            side_adjusted_edge=side_adjusted_edge,
            directional_edge_bucket=directional_edge_bucket,
            source_type=source_type,
            has_minutes_context=has_minutes_context,
            passes_minutes_context=passes_minutes_context,
        )
        row_payloads.append(
            {
                "source_position": source_position,
                "has_projection_value": has_projection_value,
                "has_market_line": has_market_line,
                "has_american_odds": has_american_odds,
                "has_sportsbook": has_sportsbook,
                "has_player_name": has_player_name,
                "has_minutes_context": has_minutes_context,
                "passes_min_edge": passes_min_edge,
                "passes_directional_edge": passes_directional_edge,
                "directional_edge_bucket": directional_edge_bucket,
                "passes_projection_source": passes_projection_source,
                "passes_minutes_context": passes_minutes_context,
                "passes_basic_schema": passes_basic_schema,
                "passes_research_validation": passes_research_validation,
                "research_rejection_reasons": (
                    "|".join(rejection_reasons) if rejection_reasons else "none"
                ),
                "research_rank_score": rank_score,
                "research_rank_reason": rank_reason,
                "_rank_directional_edge": (
                    side_adjusted_edge
                    if side_adjusted_edge is not None
                    else float("-inf")
                ),
            }
        )

    if board.empty:
        return board, rejected_reason_counts

    payload_df = pd.DataFrame(row_payloads, index=board.index)
    for column in VALIDATION_COLUMNS:
        if column in payload_df.columns:
            board[column] = payload_df[column]

    board["_source_position"] = payload_df["source_position"]
    board["_rank_directional_edge"] = payload_df["_rank_directional_edge"]
    board["_rank_market"] = (
        board["market_type"].map(_clean_text)
        if "market_type" in board.columns
        else "unavailable"
    )
    board = board.sort_values(
        by=[
            "passes_research_validation",
            "research_rank_score",
            "_rank_directional_edge",
            "_source_position",
        ],
        ascending=[False, False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    board["edge_rank_overall"] = range(1, len(board.index) + 1)
    board["edge_rank_within_market"] = (
        board.groupby("_rank_market", sort=False).cumcount() + 1
    )
    board = board.drop(
        columns=["_source_position", "_rank_directional_edge", "_rank_market"]
    )
    return board, dict(sorted(rejected_reason_counts.items()))


def _rejection_reasons(
    *,
    has_projection_value: bool,
    has_market_line: bool,
    has_american_odds: bool,
    has_sportsbook: bool,
    has_player_name: bool,
    abs_edge: float | None,
    passes_min_edge: bool,
    side_adjusted_edge: float | None,
    passes_directional_edge: bool,
    source_type: str,
    passes_projection_source: bool,
    has_minutes_context: bool,
    passes_minutes_context: bool,
) -> list[str]:
    reasons: list[str] = []
    if not has_projection_value:
        reasons.append("missing_projection_value")
    if not has_market_line:
        reasons.append("missing_market_line")
    if not has_american_odds:
        reasons.append("missing_american_odds")
    if not has_sportsbook:
        reasons.append("missing_sportsbook")
    if not has_player_name:
        reasons.append("missing_player_name")
    if not passes_min_edge:
        reasons.append("missing_abs_edge" if abs_edge is None else "edge_below_minimum")
    if not passes_directional_edge:
        reasons.append(
            "missing_side_adjusted_edge"
            if side_adjusted_edge is None
            else "directional_edge_below_minimum"
        )
    if not passes_projection_source:
        reasons.append(
            "missing_projection_source"
            if not source_type
            else "projection_source_not_allowed"
        )
    if has_minutes_context and not passes_minutes_context:
        reasons.append("minutes_below_minimum")
    return reasons


def _research_rank(
    *,
    side_adjusted_edge: float | None,
    directional_edge_bucket: str,
    source_type: str,
    has_minutes_context: bool,
    passes_minutes_context: bool,
) -> tuple[float, str]:
    positive_directional_edge = max(side_adjusted_edge or 0.0, 0.0)
    score = positive_directional_edge * 10
    reasons = [f"positive_directional_edge_x10={score:.2f}"]

    if directional_edge_bucket == "medium_edge":
        score += 3
        reasons.append("medium_edge=+3")
    elif directional_edge_bucket == "large_edge":
        score += 6
        reasons.append("large_edge=+6")

    if source_type == "model_projection":
        score += 2
        reasons.append("model_projection=+2")
    elif source_type == "recent_avg_fallback":
        score -= 3
        reasons.append("recent_avg_fallback=-3")

    if not has_minutes_context:
        score -= 5
        reasons.append("minutes_missing=-5")
    elif not passes_minutes_context:
        score -= 5
        reasons.append("minutes_below_threshold=-5")

    reasons.append(f"total={score:.2f}")
    return round(score, 6), "; ".join(reasons)


def _directional_edge_bucket(side_adjusted_edge: float | None) -> str:
    if side_adjusted_edge is None:
        return "unavailable"
    if side_adjusted_edge <= 0:
        return "no_positive_edge"
    if side_adjusted_edge < 0.5:
        return "tiny_edge"
    if side_adjusted_edge < 1.5:
        return "small_edge"
    if side_adjusted_edge < 3.0:
        return "medium_edge"
    return "large_edge"


def _limit_events(
    source_df: pd.DataFrame,
    *,
    max_events: int,
    warnings: list[str],
) -> pd.DataFrame:
    if source_df.empty:
        return source_df.copy()
    if max_events == 0:
        warnings.append("--max-events 0 excluded all join-board rows.")
        return source_df.iloc[0:0].copy()
    if "provider_event_id" not in source_df.columns:
        warnings.append(
            "Join board has no provider_event_id column; --max-events could not be applied."
        )
        return source_df.copy()

    event_keys: list[str] = []
    seen: set[str] = set()
    for value in source_df["provider_event_id"].tolist():
        key = _clean_text(value)
        if key and key not in seen:
            event_keys.append(key)
            seen.add(key)

    if len(event_keys) <= max_events:
        return source_df.copy()

    allowed = set(event_keys[:max_events])
    limited = source_df[
        source_df["provider_event_id"].map(_clean_text).isin(allowed)
    ].copy()
    warnings.append(
        f"Join board was limited from {len(event_keys)} events to {max_events} events."
    )
    return limited


def _diagnostics_payload(
    *,
    target_date: str,
    status: str,
    join_board_path: Path,
    board: pd.DataFrame,
    input_row_count: int,
    source_eligible_for_betting_any_true: bool,
    schema_missing_columns: list[str],
    allowed_source_types: list[str],
    min_edge: float,
    min_minutes: float,
    max_events: int,
    rejected_reason_counts: dict[str, int],
    warnings: list[str],
    output_path: Path,
    summary_path: Path,
    diagnostics_path: Path,
) -> dict[str, Any]:
    output_row_count = int(len(board.index))
    research_validated_count = _true_count(board, "passes_research_validation")
    eligible_for_betting_any_true = _eligible_any_true(board)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": target_date,
        "target_date": target_date,
        "status": status,
        "join_board_path": str(join_board_path),
        "input_row_count": int(input_row_count),
        "output_row_count": output_row_count,
        "research_validated_count": research_validated_count,
        "research_rejected_count": output_row_count - research_validated_count,
        "source_eligible_for_betting_any_true": bool(
            source_eligible_for_betting_any_true
        ),
        "eligible_for_betting_any_true": bool(eligible_for_betting_any_true),
        "schema_missing_required_columns": schema_missing_columns,
        "allowed_source_types": allowed_source_types,
        "min_edge": min_edge,
        "min_minutes": min_minutes,
        "max_events": max_events,
        "projection_source_type_counts": _value_counts(
            board, "projection_source_type"
        ),
        "edge_bucket_counts": _value_counts(board, "edge_bucket"),
        "directional_edge_bucket_counts": _value_counts(
            board, "directional_edge_bucket"
        ),
        "passes_min_edge_count": _true_count(board, "passes_min_edge"),
        "passes_directional_edge_count": _true_count(
            board, "passes_directional_edge"
        ),
        "passes_projection_source_count": _true_count(
            board, "passes_projection_source"
        ),
        "passes_minutes_context_count": _true_count(
            board, "passes_minutes_context"
        ),
        "passes_research_validation_count": research_validated_count,
        "rejected_reason_counts": rejected_reason_counts,
        "top_research_rows_sample": _top_research_rows_sample(board),
        "market_prop_rows_created": 0,
        "elite_rows_created": 0,
        "kelly_called": False,
        "operator_betting_boards_written": [],
        "warnings": warnings,
        "artifacts": {
            "edge_validation_board_csv": str(output_path),
            "edge_validation_summary_txt": str(summary_path),
            "edge_validation_diagnostics_json": str(diagnostics_path),
        },
    }


def _top_research_rows_sample(board: pd.DataFrame) -> list[dict[str, Any]]:
    if board.empty or "passes_research_validation" not in board.columns:
        return []
    sample_columns = [
        "player_name",
        "market_type",
        "side",
        "line",
        "american_odds",
        "sportsbook",
        "projection_value",
        "projection_source_type",
        "side_adjusted_edge",
        "abs_edge",
        "edge_bucket",
        "directional_edge_bucket",
        "research_rank_score",
        "edge_rank_overall",
        "betting_approval_status",
    ]
    available_columns = [column for column in sample_columns if column in board.columns]
    validated = board[board["passes_research_validation"].map(_truthy)]
    records = validated.loc[:, available_columns].head(10).to_dict(orient="records")
    return [_json_safe_record(record) for record in records]


def _json_safe_record(record: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in record.items():
        if _is_missing(value):
            safe[key] = None
        elif hasattr(value, "item"):
            safe[key] = value.item()
        else:
            safe[key] = value
    return safe


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
    top_rows = diagnostics["top_research_rows_sample"]
    lines = [
        f"Research-Only Edge Validation Board - {diagnostics['date']}",
        f"status: {diagnostics['status']}",
        f"join_board: {diagnostics['join_board_path']}",
        f"input_row_count: {diagnostics['input_row_count']}",
        f"output_row_count: {diagnostics['output_row_count']}",
        f"research_validated_count: {diagnostics['research_validated_count']}",
        f"research_rejected_count: {diagnostics['research_rejected_count']}",
        f"eligible_for_betting_any_true: {diagnostics['eligible_for_betting_any_true']}",
        f"projection_source_type_counts: {_counts_inline(diagnostics['projection_source_type_counts'])}",
        f"edge_bucket_counts: {_counts_inline(diagnostics['edge_bucket_counts'])}",
        f"directional_edge_bucket_counts: {_counts_inline(diagnostics['directional_edge_bucket_counts'])}",
        f"passes_min_edge_count: {diagnostics['passes_min_edge_count']}",
        f"passes_directional_edge_count: {diagnostics['passes_directional_edge_count']}",
        f"passes_projection_source_count: {diagnostics['passes_projection_source_count']}",
        f"passes_minutes_context_count: {diagnostics['passes_minutes_context_count']}",
        f"passes_research_validation_count: {diagnostics['passes_research_validation_count']}",
        f"rejected_reason_counts: {_counts_inline(diagnostics['rejected_reason_counts'])}",
        "market_prop_rows_created: 0",
        "elite_rows_created: 0",
        "kelly_called: False",
        "operator_betting_boards_written: 0",
        "",
        "top_research_rows_sample:",
    ]
    if top_rows:
        for row in top_rows:
            lines.append(
                "  "
                f"rank={row.get('edge_rank_overall')} "
                f"player={row.get('player_name')} "
                f"market={row.get('market_type')} "
                f"side={row.get('side')} "
                f"directional_edge={row.get('side_adjusted_edge')} "
                f"score={row.get('research_rank_score')}"
            )
    else:
        lines.append("  none")

    lines.extend(
        [
            "",
            "WARNING: Research-only validation. No row is betting-approved.",
            "No picks, MarketProp rows, Elite rows, Kelly calls, or operator betting boards were created.",
        ]
    )
    if diagnostics["warnings"]:
        lines.extend(
            ["warnings:", *[f"  {warning}" for warning in diagnostics["warnings"]]]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _empty_validation_frame() -> pd.DataFrame:
    columns = [
        "player_name",
        "market_type",
        "side",
        "line",
        "american_odds",
        "sportsbook",
        "eligible_for_betting",
        "projection_value",
        "projection_source_type",
        "side_adjusted_edge",
        "abs_edge",
        "edge_bucket",
        "directional_edge_bucket",
        *VALIDATION_COLUMNS,
    ]
    return pd.DataFrame(columns=list(dict.fromkeys(columns)))


def _schema_missing_columns(df: pd.DataFrame) -> list[str]:
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if not any(column in df.columns for column in MARKET_LINE_COLUMNS):
        missing.append("market_line or line")
    return missing


def _read_csv(path: Path, warnings: list[str]) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        warnings.append(f"Market projection join board is empty: {path}")
    except Exception as exc:
        warnings.append(
            f"Could not read market projection join board {path}: "
            f"{type(exc).__name__}: {exc}"
        )
    return pd.DataFrame()


def _market_line(row: pd.Series) -> float | None:
    for column in MARKET_LINE_COLUMNS:
        value = _coerce_number(row.get(column))
        if value is not None:
            return value
    return None


def _minutes_context_value(row: pd.Series) -> float | None:
    for column in MINUTES_CONTEXT_COLUMNS:
        value = _coerce_number(row.get(column))
        if value is not None:
            return value
    return None


def _true_count(df: pd.DataFrame, column: str) -> int:
    if column not in df.columns:
        return 0
    return int(sum(_truthy(value) for value in df[column].tolist()))


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
    return isinstance(value, str) and value.strip() == ""


def _clean_text(value: Any) -> str:
    return "" if _is_missing(value) else str(value).strip()


def _csv_items(
    value: str | list[str] | tuple[str, ...] | None,
) -> list[str]:
    if value is None:
        return []
    raw_items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    return [str(item).strip() for item in raw_items if str(item).strip()]


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


def _non_negative_float(value: Any, argument_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{argument_name} must be a number") from exc
    if parsed < 0:
        raise ValueError(f"{argument_name} must be non-negative")
    return parsed


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
        description="Run CourtVision research-only edge validation."
    )
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")
    parser.add_argument(
        "--join-board",
        default=None,
        help="Market projection join CSV. Defaults to output-dir/date naming.",
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
        "--min-edge",
        type=float,
        default=DEFAULT_MIN_EDGE,
        help="Minimum directional edge for research validation.",
    )
    parser.add_argument(
        "--allowed-source-types",
        default=DEFAULT_ALLOWED_SOURCE_TYPES,
        help="Comma-separated projection source types allowed for research validation.",
    )
    parser.add_argument(
        "--min-minutes",
        type=float,
        default=DEFAULT_MIN_MINUTES,
        help="Minimum minutes context when a minutes value is present.",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=DEFAULT_MAX_EVENTS,
        help="Maximum distinct provider events to include from the join board.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = run_edge_validation(
            target_date=args.date,
            join_board=args.join_board,
            output_dir=args.output_dir,
            diagnostics_dir=args.diagnostics_dir,
            min_edge=args.min_edge,
            allowed_source_types=args.allowed_source_types,
            min_minutes=args.min_minutes,
            max_events=args.max_events,
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
