"""Build a transparent research-only player stat projection source."""
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

from scripts.run_market_projection_join import normalize_player_name


STAT_PROJECTION_OK = "STAT_PROJECTION_OK"
STAT_PROJECTION_INPUT_MISSING = "STAT_PROJECTION_INPUT_MISSING"
STAT_PROJECTION_SCHEMA_INVALID = "STAT_PROJECTION_SCHEMA_INVALID"
STAT_PROJECTION_NO_OUTPUT_ROWS = "STAT_PROJECTION_NO_OUTPUT_ROWS"

DEFAULT_OUTPUT_DIR = Path("outputs/runtime/research")
DEFAULT_DIAGNOSTICS_DIR = Path("outputs/runtime/diagnostics")
DEFAULT_RECENT_WEIGHT = 0.65
DEFAULT_BASELINE_WEIGHT = 0.35
MIN_MINUTES_FACTOR = 0.85
MAX_MINUTES_FACTOR = 1.15
LOW_MINUTES_THRESHOLD = 20.0

OUTPUT_COLUMNS = [
    "player_id",
    "player_name",
    "team_abbr",
    "normalized_player_name",
    "projected_points",
    "projected_rebounds",
    "projected_assists",
    "projection_method",
    "projection_quality_flag",
    "minutes_factor",
    "min_avg",
    "min_recent",
    "pts_avg",
    "pts_recent",
    "reb_avg",
    "reb_recent",
    "ast_avg",
    "ast_recent",
    "eligible_for_betting",
]

INPUT_ALIASES = {
    "player_id": ("player_id", "athlete_id"),
    "player_name": ("player_name", "name", "player", "athlete_name"),
    "team_abbr": ("team_abbr", "team_abbreviation", "team"),
    "min_avg": ("min_avg", "minutes_avg", "average_minutes", "avg_minutes"),
    "min_recent": ("min_recent", "minutes_recent", "recent_minutes"),
    "pts_avg": ("pts_avg", "points_avg", "avg_points"),
    "pts_recent": ("pts_recent", "points_recent", "recent_points"),
    "reb_avg": ("reb_avg", "rebounds_avg", "avg_rebounds"),
    "reb_recent": ("reb_recent", "rebounds_recent", "recent_rebounds"),
    "ast_avg": ("ast_avg", "assists_avg", "avg_assists"),
    "ast_recent": ("ast_recent", "assists_recent", "recent_assists"),
}

STAT_CONTEXT_PAIRS = {
    "projected_points": ("pts_recent", "pts_avg"),
    "projected_rebounds": ("reb_recent", "reb_avg"),
    "projected_assists": ("ast_recent", "ast_avg"),
}


@dataclass(slots=True)
class StatProjectionSourceResult:
    status: str
    output_path: Path
    diagnostics_path: Path
    diagnostics: dict[str, Any]


def build_stat_projection_source(
    *,
    target_date: str,
    cleaned_context: str | Path | None = None,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    diagnostics_dir: str | Path = DEFAULT_DIAGNOSTICS_DIR,
) -> StatProjectionSourceResult:
    """Build one research-only projection row per cleaned player context row."""
    target_date_text = _validate_date(target_date)
    output_dir_path = Path(output_dir)
    diagnostics_dir_path = Path(diagnostics_dir)
    input_path = Path(cleaned_context) if cleaned_context else (
        output_dir_path / f"projection_context_clean_{target_date_text}.csv"
    )
    output_path = output_dir_path / f"stat_projection_source_{target_date_text}.csv"
    diagnostics_path = (
        diagnostics_dir_path / f"stat_projection_source_{target_date_text}.json"
    )

    output_dir_path.mkdir(parents=True, exist_ok=True)
    diagnostics_dir_path.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    source_df = pd.DataFrame()
    output_df = _empty_output_frame()

    if not input_path.exists():
        status = STAT_PROJECTION_INPUT_MISSING
        warnings.append(f"Cleaned projection context not found: {input_path}")
    else:
        source_df, read_error = _read_csv(input_path)
        columns = _column_lookup(source_df)
        name_column = _first_existing_column(columns, INPUT_ALIASES["player_name"])

        if read_error:
            status = STAT_PROJECTION_SCHEMA_INVALID
            warnings.append(f"Could not read cleaned projection context: {read_error}")
        elif name_column is None:
            status = STAT_PROJECTION_SCHEMA_INVALID
            warnings.append("Cleaned projection context is missing a player name column.")
        else:
            output_df, build_warnings = _build_rows(source_df, columns)
            warnings.extend(build_warnings)
            status = (
                STAT_PROJECTION_OK
                if not output_df.empty
                else STAT_PROJECTION_NO_OUTPUT_ROWS
            )

    diagnostics = _diagnostics_payload(
        target_date=target_date_text,
        status=status,
        input_path=input_path,
        output_path=output_path,
        diagnostics_path=diagnostics_path,
        source_df=source_df,
        output_df=output_df,
        warnings=warnings,
    )
    output_df.to_csv(output_path, index=False)
    diagnostics_path.write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return StatProjectionSourceResult(
        status=status,
        output_path=output_path,
        diagnostics_path=diagnostics_path,
        diagnostics=diagnostics,
    )


def _build_rows(
    source_df: pd.DataFrame,
    columns: dict[str, str],
) -> tuple[pd.DataFrame, list[str]]:
    rows: list[dict[str, Any]] = []
    blank_name_count = 0
    source_eligible_true_count = 0

    for _, source_row in source_df.iterrows():
        player_name = _clean_text(
            _source_value(source_row, columns, INPUT_ALIASES["player_name"])
        )
        normalized_name = normalize_player_name(player_name)
        if not normalized_name:
            blank_name_count += 1
            continue

        if _truthy(source_row.get(columns.get("eligible_for_betting", ""))):
            source_eligible_true_count += 1

        context = {
            key: _coerce_number(_source_value(source_row, columns, aliases))
            for key, aliases in INPUT_ALIASES.items()
            if key not in {"player_id", "player_name", "team_abbr"}
        }
        min_avg = context["min_avg"]
        min_recent = context["min_recent"]
        minutes_factor = _minutes_factor(min_recent=min_recent, min_avg=min_avg)
        projections = {
            output_column: _blended_projection(
                recent=context[recent_column],
                baseline=context[baseline_column],
                minutes_factor=minutes_factor,
            )
            for output_column, (
                recent_column,
                baseline_column,
            ) in STAT_CONTEXT_PAIRS.items()
        }
        projected_count = sum(value is not None for value in projections.values())
        missing_minutes_context = minutes_factor is None
        low_minutes_context = _is_low_minutes_context(
            min_recent=min_recent,
            min_avg=min_avg,
        )

        if projected_count == 0:
            projection_method = "insufficient_data"
        elif missing_minutes_context:
            projection_method = "blended_recent_baseline_no_minutes_adjustment"
        else:
            projection_method = "blended_recent_baseline_minutes_adjusted"

        if projected_count < len(STAT_CONTEXT_PAIRS):
            quality_flag = "missing_stat_context"
        elif missing_minutes_context:
            quality_flag = "missing_minutes_context"
        elif low_minutes_context:
            quality_flag = "low_minutes_context"
        else:
            quality_flag = "research_projection_only"

        rows.append(
            {
                "player_id": _source_value(
                    source_row,
                    columns,
                    INPUT_ALIASES["player_id"],
                ),
                "player_name": player_name,
                "team_abbr": _clean_text(
                    _source_value(
                        source_row,
                        columns,
                        INPUT_ALIASES["team_abbr"],
                    )
                ).upper(),
                "normalized_player_name": normalized_name,
                **{
                    column: value if value is not None else pd.NA
                    for column, value in projections.items()
                },
                "projection_method": projection_method,
                "projection_quality_flag": quality_flag,
                "minutes_factor": (
                    minutes_factor if minutes_factor is not None else pd.NA
                ),
                **{
                    column: value if value is not None else pd.NA
                    for column, value in context.items()
                },
                "eligible_for_betting": False,
            }
        )

    warnings: list[str] = []
    if blank_name_count:
        warnings.append(
            f"Skipped {blank_name_count} rows with blank normalized player names."
        )
    if source_eligible_true_count:
        warnings.append(
            "Ignored truthy eligible_for_betting values from "
            f"{source_eligible_true_count} source rows."
        )
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS), warnings


def _blended_projection(
    *,
    recent: float | None,
    baseline: float | None,
    minutes_factor: float | None,
) -> float | None:
    if recent is None or baseline is None:
        return None
    blended = (
        DEFAULT_RECENT_WEIGHT * recent
        + DEFAULT_BASELINE_WEIGHT * baseline
    )
    return blended * minutes_factor if minutes_factor is not None else blended


def _minutes_factor(
    *,
    min_recent: float | None,
    min_avg: float | None,
) -> float | None:
    if min_recent is None or min_avg is None or min_avg <= 0:
        return None
    return min(
        MAX_MINUTES_FACTOR,
        max(MIN_MINUTES_FACTOR, min_recent / min_avg),
    )


def _is_low_minutes_context(
    *,
    min_recent: float | None,
    min_avg: float | None,
) -> bool:
    if min_recent is None or min_avg is None or min_avg <= 0:
        return False
    return min_recent < LOW_MINUTES_THRESHOLD or min_avg < LOW_MINUTES_THRESHOLD


def _diagnostics_payload(
    *,
    target_date: str,
    status: str,
    input_path: Path,
    output_path: Path,
    diagnostics_path: Path,
    source_df: pd.DataFrame,
    output_df: pd.DataFrame,
    warnings: list[str],
) -> dict[str, Any]:
    missing_minutes_count = 0
    low_minutes_count = 0
    for _, row in output_df.iterrows():
        min_avg = _coerce_number(row.get("min_avg"))
        min_recent = _coerce_number(row.get("min_recent"))
        if _minutes_factor(min_recent=min_recent, min_avg=min_avg) is None:
            missing_minutes_count += 1
        elif _is_low_minutes_context(min_recent=min_recent, min_avg=min_avg):
            low_minutes_count += 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": target_date,
        "target_date": target_date,
        "status": status,
        "input_path": str(input_path),
        "output_path": str(output_path),
        "input_row_count": int(len(source_df.index)),
        "output_row_count": int(len(output_df.index)),
        "projected_points_available_count": _numeric_value_count(
            output_df,
            "projected_points",
        ),
        "projected_rebounds_available_count": _numeric_value_count(
            output_df,
            "projected_rebounds",
        ),
        "projected_assists_available_count": _numeric_value_count(
            output_df,
            "projected_assists",
        ),
        "missing_minutes_context_count": missing_minutes_count,
        "low_minutes_context_count": low_minutes_count,
        "insufficient_data_count": _value_count(
            output_df,
            "projection_method",
            "insufficient_data",
        ),
        "projection_method_counts": _value_counts(
            output_df,
            "projection_method",
        ),
        "projection_quality_flag_counts": _value_counts(
            output_df,
            "projection_quality_flag",
        ),
        "eligible_for_betting_any_true": _eligible_any_true(output_df),
        "market_prop_rows_created": 0,
        "elite_rows_created": 0,
        "kelly_called": False,
        "operator_betting_boards_written": [],
        "warnings": warnings,
        "artifacts": {
            "stat_projection_source_csv": str(output_path),
            "stat_projection_source_diagnostics_json": str(diagnostics_path),
        },
        "projection_config": {
            "recent_weight": DEFAULT_RECENT_WEIGHT,
            "baseline_weight": DEFAULT_BASELINE_WEIGHT,
            "minutes_factor_min": MIN_MINUTES_FACTOR,
            "minutes_factor_max": MAX_MINUTES_FACTOR,
            "low_minutes_threshold": LOW_MINUTES_THRESHOLD,
        },
    }


def _empty_output_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def _read_csv(path: Path) -> tuple[pd.DataFrame, str | None]:
    try:
        return pd.read_csv(path, low_memory=False), None
    except pd.errors.EmptyDataError:
        return pd.DataFrame(), "cleaned context CSV is empty"
    except Exception as exc:
        return pd.DataFrame(), f"{type(exc).__name__}: {exc}"


def _column_lookup(df: pd.DataFrame) -> dict[str, str]:
    return {str(column).strip().lower(): str(column) for column in df.columns}


def _first_existing_column(
    columns: dict[str, str],
    aliases: tuple[str, ...],
) -> str | None:
    for alias in aliases:
        actual = columns.get(alias.lower())
        if actual:
            return actual
    return None


def _source_value(
    row: pd.Series,
    columns: dict[str, str],
    aliases: tuple[str, ...],
) -> Any:
    column = _first_existing_column(columns, aliases)
    return row.get(column) if column is not None else pd.NA


def _numeric_value_count(df: pd.DataFrame, column: str) -> int:
    if column not in df.columns:
        return 0
    return int(
        sum(_coerce_number(value) is not None for value in df[column].tolist())
    )


def _value_count(df: pd.DataFrame, column: str, expected: str) -> int:
    if column not in df.columns:
        return 0
    return int(
        sum(_clean_text(value) == expected for value in df[column].tolist())
    )


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
    if "eligible_for_betting" not in df.columns:
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
    return isinstance(value, str) and value.strip() == ""


def _clean_text(value: Any) -> str:
    return "" if _is_missing(value) else str(value).strip()


def _validate_date(value: str) -> str:
    text = str(value).strip()
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("--date must be in YYYY-MM-DD format") from exc
    return text


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build CourtVision research-only blended stat projections."
    )
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")
    parser.add_argument(
        "--cleaned-context",
        default=None,
        help="Cleaned projection context CSV. Defaults to output-dir/date naming.",
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
        result = build_stat_projection_source(
            target_date=args.date,
            cleaned_context=args.cleaned_context,
            output_dir=args.output_dir,
            diagnostics_dir=args.diagnostics_dir,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"status: {result.status}")
    print(f"projection_source: {result.output_path}")
    print(f"diagnostics: {result.diagnostics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
