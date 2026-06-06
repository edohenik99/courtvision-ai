"""Audit and clean projection context without creating betting artifacts."""
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


PROJECTION_AUDIT_OK = "PROJECTION_AUDIT_OK"
PROJECTION_AUDIT_SOURCE_MISSING = "PROJECTION_AUDIT_SOURCE_MISSING"
PROJECTION_AUDIT_SCHEMA_INVALID = "PROJECTION_AUDIT_SCHEMA_INVALID"
PROJECTION_AUDIT_DANGEROUS_DUPLICATES = "PROJECTION_AUDIT_DANGEROUS_DUPLICATES"

DEFAULT_PROJECTION_SOURCE = Path("outputs/model/player_baselines.csv")
DEFAULT_OUTPUT_DIR = Path("outputs/runtime/research")
DEFAULT_DIAGNOSTICS_DIR = Path("outputs/runtime/diagnostics")

NAME_COLUMNS = ("player_name", "name", "player", "athlete_name")
PLAYER_ID_COLUMNS = ("player_id", "athlete_id")
TEAM_COLUMNS = ("team_abbr", "team_abbreviation", "team")
DATE_COLUMNS = (
    "context_date",
    "game_date",
    "as_of_date",
    "snapshot_date",
    "updated_at",
    "created_at",
    "date",
)

STAT_COLUMN_ALIASES = {
    "points": (
        "points",
        "pts",
        "projected_points",
        "points_projection",
        "points_avg",
        "pts_avg",
        "avg_points",
        "points_recent",
        "pts_recent",
        "recent_points",
    ),
    "rebounds": (
        "rebounds",
        "reb",
        "totreb",
        "projected_rebounds",
        "rebounds_avg",
        "reb_avg",
        "avg_rebounds",
        "rebounds_recent",
        "reb_recent",
        "recent_rebounds",
    ),
    "assists": (
        "assists",
        "ast",
        "projected_assists",
        "assists_avg",
        "ast_avg",
        "avg_assists",
        "assists_recent",
        "ast_recent",
        "recent_assists",
    ),
    "recent_averages": (
        "recent_avg_value",
        "points_recent",
        "pts_recent",
        "recent_points",
        "rebounds_recent",
        "reb_recent",
        "recent_rebounds",
        "assists_recent",
        "ast_recent",
        "recent_assists",
    ),
    "baseline_values": (
        "baseline_value",
        "points_baseline",
        "pts_baseline",
        "points_avg",
        "pts_avg",
        "avg_points",
        "rebounds_baseline",
        "reb_baseline",
        "rebounds_avg",
        "reb_avg",
        "avg_rebounds",
        "assists_baseline",
        "ast_baseline",
        "assists_avg",
        "ast_avg",
        "avg_assists",
    ),
    "minutes_averages": (
        "minutes_avg",
        "average_minutes",
        "avg_minutes",
        "min_avg",
        "minutes_recent",
        "min_recent",
        "expected_minutes",
    ),
    "player_id": PLAYER_ID_COLUMNS,
    "team_abbr": TEAM_COLUMNS,
}

RECENT_MINUTES_COLUMNS = (
    *STAT_COLUMN_ALIASES["recent_averages"],
    *STAT_COLUMN_ALIASES["minutes_averages"],
)
SAMPLE_LIMIT = 10


@dataclass(slots=True)
class ProjectionSourceAuditResult:
    status: str
    audit_path: Path
    cleaned_context_path: Path
    diagnostics_path: Path
    diagnostics: dict[str, Any]


def run_projection_source_audit(
    *,
    target_date: str,
    projection_source: str | Path = DEFAULT_PROJECTION_SOURCE,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    diagnostics_dir: str | Path = DEFAULT_DIAGNOSTICS_DIR,
) -> ProjectionSourceAuditResult:
    """Audit one projection source and write a one-row-per-player preview."""
    target_date_text = _validate_date(target_date)
    source_path = Path(projection_source)
    output_dir_path = Path(output_dir)
    diagnostics_dir_path = Path(diagnostics_dir)
    audit_path = output_dir_path / f"projection_source_audit_{target_date_text}.txt"
    cleaned_path = output_dir_path / f"projection_context_clean_{target_date_text}.csv"
    diagnostics_path = (
        diagnostics_dir_path / f"projection_source_audit_{target_date_text}.json"
    )

    output_dir_path.mkdir(parents=True, exist_ok=True)
    diagnostics_dir_path.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    if not source_path.exists():
        status = PROJECTION_AUDIT_SOURCE_MISSING
        cleaned = _empty_cleaned_frame()
        diagnostics = _diagnostics_payload(
            target_date=target_date_text,
            status=status,
            source_path=source_path,
            source_df=pd.DataFrame(),
            cleaned=cleaned,
            duplicate_details=[],
            available_stat_columns={key: [] for key in STAT_COLUMN_ALIASES},
            missing_expected_columns=list(STAT_COLUMN_ALIASES),
            warnings=[f"Projection source not found: {source_path}"],
            name_column=None,
            player_id_column=None,
            team_column=None,
            date_column=None,
            audit_path=audit_path,
            cleaned_path=cleaned_path,
            diagnostics_path=diagnostics_path,
        )
        _write_outputs(audit_path, cleaned_path, diagnostics_path, cleaned, diagnostics)
        return ProjectionSourceAuditResult(
            status, audit_path, cleaned_path, diagnostics_path, diagnostics
        )

    source_df, read_error = _read_csv(source_path)
    column_lookup = _column_lookup(source_df)
    name_column = _first_existing_column(column_lookup, NAME_COLUMNS)
    player_id_column = _first_existing_column(column_lookup, PLAYER_ID_COLUMNS)
    team_column = _first_existing_column(column_lookup, TEAM_COLUMNS)
    date_column = _first_existing_column(column_lookup, DATE_COLUMNS)
    available_stat_columns = _available_stat_columns(source_df)
    missing_expected_columns = [
        category
        for category, columns in available_stat_columns.items()
        if not columns
    ]

    if read_error:
        warnings.append(f"Could not read projection source: {read_error}")
    if name_column is None:
        warnings.append("Projection source is missing a player name column.")

    if read_error or name_column is None:
        status = PROJECTION_AUDIT_SCHEMA_INVALID
        cleaned = _empty_cleaned_frame(source_df.columns)
        duplicate_details: list[dict[str, Any]] = []
    else:
        working = source_df.copy()
        working["_normalized_player_name"] = working[name_column].map(
            normalize_player_name
        )
        blank_name_count = int((working["_normalized_player_name"] == "").sum())
        if blank_name_count:
            warnings.append(
                f"Skipped {blank_name_count} rows with blank normalized player names."
            )
        working = working[working["_normalized_player_name"] != ""].copy()
        duplicate_details = _duplicate_details(
            working,
            name_column=name_column,
            player_id_column=player_id_column,
            team_column=team_column,
        )
        cleaned, dedupe_warnings = _clean_projection_context(
            working,
            source_columns=list(source_df.columns),
            date_column=date_column,
        )
        warnings.extend(dedupe_warnings)

        duplicate_count = len(duplicate_details)
        dangerous_count = sum(
            bool(detail["different_player_id"]) for detail in duplicate_details
        )
        team_review_count = sum(
            bool(detail["different_team_abbr"]) for detail in duplicate_details
        )
        if duplicate_count:
            warnings.append(
                f"Found {duplicate_count} duplicate normalized player names."
            )
        if team_review_count:
            warnings.append(
                f"{team_review_count} duplicate player groups span multiple teams and need review."
            )
        if dangerous_count:
            warnings.append(
                f"{dangerous_count} duplicate player groups have conflicting player IDs."
            )
        status = (
            PROJECTION_AUDIT_DANGEROUS_DUPLICATES
            if dangerous_count
            else PROJECTION_AUDIT_OK
        )

    diagnostics = _diagnostics_payload(
        target_date=target_date_text,
        status=status,
        source_path=source_path,
        source_df=source_df,
        cleaned=cleaned,
        duplicate_details=duplicate_details,
        available_stat_columns=available_stat_columns,
        missing_expected_columns=missing_expected_columns,
        warnings=warnings,
        name_column=name_column,
        player_id_column=player_id_column,
        team_column=team_column,
        date_column=date_column,
        audit_path=audit_path,
        cleaned_path=cleaned_path,
        diagnostics_path=diagnostics_path,
    )
    _write_outputs(audit_path, cleaned_path, diagnostics_path, cleaned, diagnostics)
    return ProjectionSourceAuditResult(
        status, audit_path, cleaned_path, diagnostics_path, diagnostics
    )


def _duplicate_details(
    df: pd.DataFrame,
    *,
    name_column: str,
    player_id_column: str | None,
    team_column: str | None,
) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for normalized_name, group in df.groupby(
        "_normalized_player_name", sort=False, dropna=False
    ):
        if len(group.index) < 2:
            continue
        player_ids = _ordered_identity_values(group, player_id_column)
        teams = _ordered_identity_values(group, team_column, uppercase=True)
        different_player_id = len(player_ids) > 1
        different_team_abbr = len(teams) > 1
        if different_player_id:
            classification = "dangerous_different_player_id"
        elif different_team_abbr:
            classification = "needs_review_different_team_abbr"
        else:
            classification = "likely_historical_rows"
        details.append(
            {
                "normalized_player_name": str(normalized_name),
                "duplicate_count": int(len(group.index)),
                "source_player_names": _ordered_text_values(group[name_column]),
                "player_ids": player_ids,
                "team_abbrs": teams,
                "different_player_id": different_player_id,
                "different_team_abbr": different_team_abbr,
                "classification": classification,
            }
        )
    return details


def _clean_projection_context(
    df: pd.DataFrame,
    *,
    source_columns: list[str],
    date_column: str | None,
) -> tuple[pd.DataFrame, list[str]]:
    output_columns = [
        *source_columns,
        "normalized_player_name",
        "duplicate_count",
        "dedupe_reason",
    ]
    if df.empty:
        return pd.DataFrame(columns=output_columns), []

    column_lookup = _column_lookup(df)
    preference_columns = [
        actual
        for alias in RECENT_MINUTES_COLUMNS
        if (actual := column_lookup.get(alias.lower())) is not None
    ]
    preference_columns = list(dict.fromkeys(preference_columns))
    selected_rows: list[dict[str, Any]] = []
    first_row_fallbacks = 0
    tied_preference_groups = 0

    for normalized_name, group in df.groupby(
        "_normalized_player_name", sort=False, dropna=False
    ):
        duplicate_count = int(len(group.index))
        selected_index = group.index[0]
        dedupe_reason = "unique_normalized_player"

        if duplicate_count > 1:
            selected_index, dedupe_reason, used_tie = _select_preferred_index(
                group,
                date_column=date_column,
                preference_columns=preference_columns,
            )
            if dedupe_reason == "first_row_fallback":
                first_row_fallbacks += 1
            if used_tie:
                tied_preference_groups += 1

        selected = group.loc[selected_index, source_columns].to_dict()
        selected["normalized_player_name"] = str(normalized_name)
        selected["duplicate_count"] = duplicate_count
        selected["dedupe_reason"] = dedupe_reason
        selected_rows.append(selected)

    warnings: list[str] = []
    if first_row_fallbacks:
        warnings.append(
            f"Used first-row fallback for {first_row_fallbacks} duplicate player groups."
        )
    if tied_preference_groups:
        warnings.append(
            f"Used source order to break equal dedupe preference for {tied_preference_groups} player groups."
        )
    return pd.DataFrame(selected_rows, columns=output_columns), warnings


def _select_preferred_index(
    group: pd.DataFrame,
    *,
    date_column: str | None,
    preference_columns: list[str],
) -> tuple[Any, str, bool]:
    candidates = group
    if date_column:
        parsed_dates = pd.to_datetime(group[date_column], errors="coerce", utc=True)
        if parsed_dates.notna().any():
            latest_date = parsed_dates.max()
            candidates = group.loc[parsed_dates[parsed_dates == latest_date].index]
            if len(candidates.index) == 1:
                return candidates.index[0], f"most_recent_context:{date_column}", False

    if preference_columns:
        completeness = candidates[preference_columns].apply(
            lambda row: sum(not _is_missing(value) for value in row),
            axis=1,
        )
        if int(completeness.max()) > 0:
            best = candidates.loc[completeness[completeness == completeness.max()].index]
            reason = "most_complete_recent_minutes_context"
            return best.index[0], reason, len(best.index) > 1

    return candidates.index[0], "first_row_fallback", len(candidates.index) > 1


def _diagnostics_payload(
    *,
    target_date: str,
    status: str,
    source_path: Path,
    source_df: pd.DataFrame,
    cleaned: pd.DataFrame,
    duplicate_details: list[dict[str, Any]],
    available_stat_columns: dict[str, list[str]],
    missing_expected_columns: list[str],
    warnings: list[str],
    name_column: str | None,
    player_id_column: str | None,
    team_column: str | None,
    date_column: str | None,
    audit_path: Path,
    cleaned_path: Path,
    diagnostics_path: Path,
) -> dict[str, Any]:
    dangerous_duplicates = [
        detail for detail in duplicate_details if detail["different_player_id"]
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": target_date,
        "target_date": target_date,
        "status": status,
        "source_path": str(source_path),
        "row_count": int(len(source_df.index)),
        "unique_normalized_player_count": int(len(cleaned.index)),
        "duplicate_normalized_player_count": int(len(duplicate_details)),
        "dangerous_duplicate_count": int(len(dangerous_duplicates)),
        "duplicate_players_sample": duplicate_details[:SAMPLE_LIMIT],
        "dangerous_duplicates_sample": dangerous_duplicates[:SAMPLE_LIMIT],
        "available_columns": [str(column) for column in source_df.columns],
        "available_stat_columns": available_stat_columns,
        "missing_expected_columns": missing_expected_columns,
        "detected_schema": {
            "player_name_column": name_column or "",
            "player_id_column": player_id_column or "",
            "team_abbr_column": team_column or "",
            "date_column": date_column or "",
        },
        "cleaned_context_row_count": int(len(cleaned.index)),
        "warnings": warnings,
        "eligible_for_betting_any_true": False,
        "market_prop_rows_created": 0,
        "elite_rows_created": 0,
        "kelly_called": False,
        "operator_betting_boards_written": [],
        "artifacts": {
            "projection_source_audit_txt": str(audit_path),
            "projection_context_clean_csv": str(cleaned_path),
            "projection_source_audit_json": str(diagnostics_path),
        },
    }


def _write_outputs(
    audit_path: Path,
    cleaned_path: Path,
    diagnostics_path: Path,
    cleaned: pd.DataFrame,
    diagnostics: dict[str, Any],
) -> None:
    cleaned.to_csv(cleaned_path, index=False)
    audit_path.write_text(_audit_text(diagnostics), encoding="utf-8")
    diagnostics_path.write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _audit_text(diagnostics: dict[str, Any]) -> str:
    lines = [
        f"Projection Source Audit - {diagnostics['date']}",
        f"status: {diagnostics['status']}",
        f"source_path: {diagnostics['source_path']}",
        f"row_count: {diagnostics['row_count']}",
        (
            "unique_normalized_player_count: "
            f"{diagnostics['unique_normalized_player_count']}"
        ),
        (
            "duplicate_normalized_player_count: "
            f"{diagnostics['duplicate_normalized_player_count']}"
        ),
        f"dangerous_duplicate_count: {diagnostics['dangerous_duplicate_count']}",
        f"cleaned_context_row_count: {diagnostics['cleaned_context_row_count']}",
        "",
        "Detected schema:",
    ]
    for key, value in diagnostics["detected_schema"].items():
        lines.append(f"  {key}: {value or 'not found'}")

    lines.extend(["", "Available stat columns:"])
    for category, columns in diagnostics["available_stat_columns"].items():
        lines.append(f"  {category}: {_list_inline(columns)}")
    lines.append(
        f"missing_expected_columns: {_list_inline(diagnostics['missing_expected_columns'])}"
    )

    lines.extend(["", "Duplicate player sample:"])
    if diagnostics["duplicate_players_sample"]:
        for detail in diagnostics["duplicate_players_sample"]:
            lines.append(
                "  "
                f"{detail['normalized_player_name']}: rows={detail['duplicate_count']}; "
                f"classification={detail['classification']}; "
                f"player_ids={_list_inline(detail['player_ids'])}; "
                f"teams={_list_inline(detail['team_abbrs'])}"
            )
    else:
        lines.append("  none")

    lines.extend(
        [
            "",
            "Research-only safety:",
            "  eligible_for_betting_any_true: False",
            "  market_prop_rows_created: 0",
            "  elite_rows_created: 0",
            "  kelly_called: False",
            "  operator_betting_boards_written: none",
        ]
    )
    if diagnostics["warnings"]:
        lines.extend(["", "Warnings:"])
        lines.extend(f"  {warning}" for warning in diagnostics["warnings"])
    return "\n".join(lines) + "\n"


def _available_stat_columns(df: pd.DataFrame) -> dict[str, list[str]]:
    column_lookup = _column_lookup(df)
    return {
        category: [
            actual
            for alias in aliases
            if (actual := column_lookup.get(alias.lower())) is not None
        ]
        for category, aliases in STAT_COLUMN_ALIASES.items()
    }


def _empty_cleaned_frame(source_columns: Any = ()) -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            *[str(column) for column in source_columns],
            "normalized_player_name",
            "duplicate_count",
            "dedupe_reason",
        ]
    )


def _read_csv(path: Path) -> tuple[pd.DataFrame, str | None]:
    try:
        return pd.read_csv(path, low_memory=False), None
    except pd.errors.EmptyDataError:
        return pd.DataFrame(), "source CSV is empty"
    except Exception as exc:
        return pd.DataFrame(), f"{type(exc).__name__}: {exc}"


def _column_lookup(df: pd.DataFrame) -> dict[str, str]:
    return {str(column).strip().lower(): str(column) for column in df.columns}


def _first_existing_column(
    column_lookup: dict[str, str],
    candidates: tuple[str, ...],
) -> str | None:
    for candidate in candidates:
        actual = column_lookup.get(candidate.lower())
        if actual:
            return actual
    return None


def _ordered_identity_values(
    df: pd.DataFrame,
    column: str | None,
    *,
    uppercase: bool = False,
) -> list[str]:
    if column is None:
        return []
    values: list[str] = []
    seen: set[str] = set()
    for value in df[column].tolist():
        text = _identity_text(value)
        if uppercase:
            text = text.upper()
        if text and text not in seen:
            values.append(text)
            seen.add(text)
    return values


def _ordered_text_values(series: pd.Series) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for value in series.tolist():
        text = _clean_text(value)
        if text and text not in seen:
            values.append(text)
            seen.add(text)
    return values


def _identity_text(value: Any) -> str:
    if _is_missing(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


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


def _list_inline(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def _validate_date(value: str) -> str:
    text = str(value).strip()
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("--date must be in YYYY-MM-DD format") from exc
    return text


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit and clean CourtVision projection source context."
    )
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")
    parser.add_argument(
        "--projection-source",
        default=str(DEFAULT_PROJECTION_SOURCE),
        help="Projection source CSV. Defaults to outputs/model/player_baselines.csv.",
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
        result = run_projection_source_audit(
            target_date=args.date,
            projection_source=args.projection_source,
            output_dir=args.output_dir,
            diagnostics_dir=args.diagnostics_dir,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"status: {result.status}")
    print(f"audit: {result.audit_path}")
    print(f"cleaned_context: {result.cleaned_context_path}")
    print(f"diagnostics: {result.diagnostics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
