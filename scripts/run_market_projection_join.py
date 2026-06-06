"""Phase 6B research-only market/projection join and edge preview.

This entrypoint joins a Phase 5B market validation board to projection or
stat-context rows using normalized player names and game-team context, applies
research-only projection fallbacks, and calculates preview edges. It does not
create picks, MarketProp rows, Elite rows, Kelly inputs, or operator betting
boards.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


MARKET_PROJECTION_JOIN_OK = "MARKET_PROJECTION_JOIN_OK"
MARKET_PROJECTION_JOIN_NO_MARKET_BOARD = "MARKET_PROJECTION_JOIN_NO_MARKET_BOARD"
MARKET_PROJECTION_JOIN_NO_PROJECTION_SOURCE = "MARKET_PROJECTION_JOIN_NO_PROJECTION_SOURCE"
MARKET_PROJECTION_JOIN_SCHEMA_INVALID = "MARKET_PROJECTION_JOIN_SCHEMA_INVALID"
MARKET_PROJECTION_JOIN_PARTIAL_MATCH = "MARKET_PROJECTION_JOIN_PARTIAL_MATCH"
MARKET_PROJECTION_JOIN_NO_MATCHES = "MARKET_PROJECTION_JOIN_NO_MATCHES"

DEFAULT_OUTPUT_DIR = Path("outputs/runtime/research")
DEFAULT_DIAGNOSTICS_DIR = Path("outputs/runtime/diagnostics")

MARKET_REQUIRED_COLUMNS = ["player_name", "market_type", "side", "line"]
PROJECTION_NAME_COLUMNS = ["player_name", "name", "player", "athlete_name"]
PROJECTION_TEAM_COLUMNS = ["team_abbr", "team_abbreviation", "team"]

NBA_TEAM_NAME_TO_ABBR = {
    "atlanta hawks": "ATL",
    "boston celtics": "BOS",
    "brooklyn nets": "BKN",
    "charlotte hornets": "CHA",
    "chicago bulls": "CHI",
    "cleveland cavaliers": "CLE",
    "dallas mavericks": "DAL",
    "denver nuggets": "DEN",
    "detroit pistons": "DET",
    "golden state warriors": "GSW",
    "houston rockets": "HOU",
    "indiana pacers": "IND",
    "la clippers": "LAC",
    "los angeles clippers": "LAC",
    "los angeles lakers": "LAL",
    "memphis grizzlies": "MEM",
    "miami heat": "MIA",
    "milwaukee bucks": "MIL",
    "minnesota timberwolves": "MIN",
    "new orleans pelicans": "NOP",
    "new york knicks": "NYK",
    "oklahoma city thunder": "OKC",
    "orlando magic": "ORL",
    "philadelphia 76ers": "PHI",
    "phoenix suns": "PHX",
    "portland trail blazers": "POR",
    "sacramento kings": "SAC",
    "san antonio spurs": "SAS",
    "toronto raptors": "TOR",
    "utah jazz": "UTA",
    "washington wizards": "WAS",
}
NBA_TEAM_ABBRS = set(NBA_TEAM_NAME_TO_ABBR.values())

MARKET_PROJECTION_ALIASES = {
    "player_points": [
        "projection_value",
        "model_projection",
        "points",
        "pts",
        "projected_points",
        "points_projection",
    ],
    "player_rebounds": [
        "projection_value",
        "model_projection",
        "rebounds",
        "reb",
        "totReb",
        "projected_rebounds",
    ],
    "player_assists": [
        "projection_value",
        "model_projection",
        "assists",
        "ast",
        "projected_assists",
    ],
}

BASELINE_VALUE_ALIASES = {
    "player_points": [
        "baseline_value",
        "points_baseline",
        "pts_baseline",
        "points_avg",
        "pts_avg",
        "avg_points",
    ],
    "player_rebounds": [
        "baseline_value",
        "rebounds_baseline",
        "reb_baseline",
        "rebounds_avg",
        "reb_avg",
        "avg_rebounds",
    ],
    "player_assists": [
        "baseline_value",
        "assists_baseline",
        "ast_baseline",
        "assists_avg",
        "ast_avg",
        "avg_assists",
    ],
}

RECENT_VALUE_ALIASES = {
    "player_points": [
        "recent_avg_value",
        "points_recent",
        "pts_recent",
        "recent_points",
    ],
    "player_rebounds": [
        "recent_avg_value",
        "rebounds_recent",
        "reb_recent",
        "recent_rebounds",
    ],
    "player_assists": [
        "recent_avg_value",
        "assists_recent",
        "ast_recent",
        "recent_assists",
    ],
}

CONTEXT_COLUMNS = [
    "player_id",
    "team_id",
    "team_abbreviation",
    "team_abbr",
    "team",
    "game_id",
    "game_date",
    "minutes",
    "expected_minutes",
    "min",
    "min_avg",
    "min_recent",
    "usage_boost",
    "injury_boost",
    "matchup_boost",
    "exposure_score",
    "confidence",
    "confidence_tier",
    "mode",
    "source",
]

JOIN_EXTRA_COLUMNS = [
    "matched_projection_player_name",
    "projection_match_status",
    "market_line",
    "projection_value",
    "projection_source_type",
    "projection_quality_flag",
    "baseline_value",
    "recent_avg_value",
    "raw_edge",
    "side_adjusted_edge",
    "edge_direction",
    "abs_edge",
    "edge_bucket",
]


@dataclass(slots=True)
class MarketProjectionJoinResult:
    status: str
    output_path: Path
    summary_path: Path
    diagnostics_path: Path
    diagnostics: dict[str, Any]


@dataclass(slots=True)
class ProjectionJoinStats:
    matched_player_count: int
    unmatched_players: list[str]
    duplicate_normalized_player_warning_count: int
    team_aware_match_count: int
    name_only_match_count: int


def run_market_projection_join(
    *,
    target_date: str,
    market_board: str | Path | None = None,
    projection_source: str | Path | None = None,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    diagnostics_dir: str | Path = DEFAULT_DIAGNOSTICS_DIR,
) -> MarketProjectionJoinResult:
    """Run the research-only market/projection join preview for one date."""
    target_date_text = _validate_date(target_date)
    output_dir_path = Path(output_dir)
    diagnostics_dir_path = Path(diagnostics_dir)
    market_board_path = Path(market_board) if market_board else (
        output_dir_path / f"market_validation_board_{target_date_text}.csv"
    )

    output_path = output_dir_path / f"market_projection_join_{target_date_text}.csv"
    summary_path = output_dir_path / f"market_projection_join_summary_{target_date_text}.txt"
    diagnostics_path = diagnostics_dir_path / f"market_projection_join_{target_date_text}.json"

    output_dir_path.mkdir(parents=True, exist_ok=True)
    diagnostics_dir_path.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    if not market_board_path.exists():
        joined = _empty_join_frame()
        status = MARKET_PROJECTION_JOIN_NO_MARKET_BOARD
        diagnostics = _diagnostics_payload(
            target_date=target_date_text,
            status=status,
            market_board_path=market_board_path,
            projection_source_path=None,
            projection_source_available=False,
            projection_source_type="unavailable",
            joined=joined,
            unmatched_players=[],
            matched_player_count=0,
            duplicate_normalized_player_warning_count=0,
            team_aware_match_count=0,
            name_only_match_count=0,
            warnings=[f"Market board not found: {market_board_path}"],
            output_path=output_path,
            summary_path=summary_path,
            diagnostics_path=diagnostics_path,
            source_eligible_for_betting_any_true=False,
            schema_missing_columns=MARKET_REQUIRED_COLUMNS,
        )
        _write_outputs(output_path, summary_path, diagnostics_path, joined, diagnostics)
        return MarketProjectionJoinResult(status, output_path, summary_path, diagnostics_path, diagnostics)

    market_df = _read_csv(market_board_path, warnings=warnings, source_label="market board")
    schema_missing_columns = [
        column for column in MARKET_REQUIRED_COLUMNS if column not in market_df.columns
    ]
    source_eligible_for_betting_any_true = _eligible_any_true(market_df)

    (
        projection_path,
        projection_df,
        projection_available,
        projection_source_type,
    ) = _load_projection_source(
        target_date=target_date_text,
        output_dir=output_dir_path,
        projection_source=projection_source,
        warnings=warnings,
    )

    projection_schema_valid = True
    if projection_available:
        name_column = _first_existing_column(projection_df, PROJECTION_NAME_COLUMNS)
        if name_column is None:
            projection_schema_valid = False
            warnings.append("Projection source is missing a player name column.")

    joined, join_stats = _join_market_projection_rows(
        market_df=market_df,
        projection_df=projection_df,
        projection_available=projection_available and projection_schema_valid,
        warnings=warnings,
    )

    if source_eligible_for_betting_any_true:
        warnings.append(
            "Market board had eligible_for_betting truthy values; preview output was forced false."
        )

    if schema_missing_columns or not projection_schema_valid:
        status = MARKET_PROJECTION_JOIN_SCHEMA_INVALID
    elif not projection_available:
        status = MARKET_PROJECTION_JOIN_NO_PROJECTION_SOURCE
    elif join_stats.matched_player_count == 0 and _unique_market_player_count(market_df) > 0:
        status = MARKET_PROJECTION_JOIN_NO_MATCHES
    elif join_stats.unmatched_players:
        status = MARKET_PROJECTION_JOIN_PARTIAL_MATCH
    else:
        status = MARKET_PROJECTION_JOIN_OK

    diagnostics = _diagnostics_payload(
        target_date=target_date_text,
        status=status,
        market_board_path=market_board_path,
        projection_source_path=projection_path,
        projection_source_available=projection_available,
        projection_source_type=projection_source_type,
        joined=joined,
        unmatched_players=join_stats.unmatched_players,
        matched_player_count=join_stats.matched_player_count,
        duplicate_normalized_player_warning_count=(
            join_stats.duplicate_normalized_player_warning_count
        ),
        team_aware_match_count=join_stats.team_aware_match_count,
        name_only_match_count=join_stats.name_only_match_count,
        warnings=warnings,
        output_path=output_path,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
        source_eligible_for_betting_any_true=source_eligible_for_betting_any_true,
        schema_missing_columns=schema_missing_columns,
    )
    _write_outputs(output_path, summary_path, diagnostics_path, joined, diagnostics)

    return MarketProjectionJoinResult(status, output_path, summary_path, diagnostics_path, diagnostics)


def normalize_player_name(value: Any) -> str:
    """Normalize player names for deterministic research joins."""
    text = _clean_text(value).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = re.sub(r"[’‘`´]", "'", text)
    text = text.replace("'", "")
    text = re.sub(r"[-‐‑‒–—]", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _join_market_projection_rows(
    *,
    market_df: pd.DataFrame,
    projection_df: pd.DataFrame,
    projection_available: bool,
    warnings: list[str],
) -> tuple[pd.DataFrame, ProjectionJoinStats]:
    joined = market_df.copy()
    for column in JOIN_EXTRA_COLUMNS:
        joined[column] = pd.NA

    joined["eligible_for_betting"] = False
    joined["projection_source_type"] = "unavailable"
    joined["projection_quality_flag"] = "no_projection_context"
    joined["edge_direction"] = "unavailable"
    joined["edge_bucket"] = "unavailable"

    context_output_columns = _context_output_columns(projection_df)
    for column in context_output_columns:
        if column not in joined.columns:
            joined[column] = pd.NA

    if joined.empty:
        return joined, ProjectionJoinStats(0, [], 0, 0, 0)

    if not projection_available:
        joined["projection_match_status"] = "projection_source_unavailable"
        joined["market_line"] = joined["line"].map(_coerce_number) if "line" in joined.columns else pd.NA
        unmatched_players = _ordered_market_players(joined)
        return joined, ProjectionJoinStats(0, unmatched_players, 0, 0, 0)

    (
        projection_lookup,
        compact_lookup,
        duplicate_warning_count,
    ) = _projection_row_lookup(projection_df, warnings)
    projection_columns = _column_lookup(projection_df)
    matched_keys: set[str] = set()
    unmatched_by_key: dict[str, str] = {}
    team_aware_match_count = 0
    name_only_match_count = 0

    for index, market_row in joined.iterrows():
        market_name = market_row.get("player_name", "")
        market_key = normalize_player_name(market_name)
        projection_candidates = projection_lookup.get(market_key)
        if projection_candidates is None:
            projection_candidates = compact_lookup.get(_compact_name_key(market_key))

        market_type = _clean_text(market_row.get("market_type"))
        side = _clean_text(market_row.get("side")).lower()
        market_line = _coerce_number(market_row.get("line"))
        joined.at[index, "market_line"] = market_line if market_line is not None else pd.NA

        if not projection_candidates:
            joined.at[index, "projection_match_status"] = "unmatched"
            if market_key and market_key not in unmatched_by_key:
                unmatched_by_key[market_key] = _clean_text(market_name)
            continue

        projection_row, match_status = _select_projection_row(
            projection_candidates,
            game_team_abbrs=_market_game_team_abbrs(market_row),
        )
        matched_keys.add(market_key)
        joined.at[index, "projection_match_status"] = match_status
        if match_status == "team_aware_matched":
            team_aware_match_count += 1
        else:
            name_only_match_count += 1
        joined.at[index, "matched_projection_player_name"] = projection_row["player_name"]

        projection_value = _market_value(
            projection_row["row"],
            projection_columns,
            MARKET_PROJECTION_ALIASES.get(market_type, []),
        )
        baseline_value = _market_value(
            projection_row["row"],
            projection_columns,
            BASELINE_VALUE_ALIASES.get(market_type, []),
        )
        recent_avg_value = _market_value(
            projection_row["row"],
            projection_columns,
            RECENT_VALUE_ALIASES.get(market_type, []),
        )
        joined.at[index, "baseline_value"] = (
            baseline_value if baseline_value is not None else pd.NA
        )
        joined.at[index, "recent_avg_value"] = (
            recent_avg_value if recent_avg_value is not None else pd.NA
        )

        selected_projection, source_type, quality_flag = _select_projection_value(
            model_projection=projection_value,
            recent_avg_value=recent_avg_value,
            baseline_value=baseline_value,
        )
        joined.at[index, "projection_value"] = (
            selected_projection if selected_projection is not None else pd.NA
        )
        joined.at[index, "projection_source_type"] = source_type
        joined.at[index, "projection_quality_flag"] = quality_flag

        if selected_projection is not None and market_line is not None:
            raw_edge = selected_projection - market_line
            joined.at[index, "raw_edge"] = raw_edge
            if side == "over":
                joined.at[index, "side_adjusted_edge"] = raw_edge
                side_adjusted_edge = raw_edge
            elif side == "under":
                side_adjusted_edge = market_line - selected_projection
                joined.at[index, "side_adjusted_edge"] = side_adjusted_edge
            else:
                side_adjusted_edge = None

            if side_adjusted_edge is not None:
                abs_edge = abs(side_adjusted_edge)
                joined.at[index, "abs_edge"] = abs_edge
                joined.at[index, "edge_bucket"] = _edge_bucket(abs_edge)
                if side_adjusted_edge > 0:
                    joined.at[index, "edge_direction"] = f"{side}_edge"
                else:
                    joined.at[index, "edge_direction"] = "no_edge"

        for source_column, output_column in context_output_columns.items():
            value = projection_row["row"].get(source_column)
            joined.at[index, output_column] = value if not _is_missing(value) else pd.NA

    matched_player_count = len(matched_keys)
    unmatched_players = [
        player
        for key, player in unmatched_by_key.items()
        if key and key not in matched_keys
    ]
    return joined, ProjectionJoinStats(
        matched_player_count,
        unmatched_players,
        duplicate_warning_count,
        team_aware_match_count,
        name_only_match_count,
    )


def _select_projection_value(
    *,
    model_projection: float | None,
    recent_avg_value: float | None,
    baseline_value: float | None,
) -> tuple[float | None, str, str]:
    if model_projection is not None:
        return model_projection, "model_projection", "projection_available"
    if recent_avg_value is not None:
        return recent_avg_value, "recent_avg_fallback", "fallback_recent_average"
    if baseline_value is not None:
        return baseline_value, "baseline_fallback", "fallback_baseline_only"
    return None, "unavailable", "no_projection_context"


def _edge_bucket(abs_edge: float) -> str:
    if abs_edge < 0.5:
        return "tiny_edge"
    if abs_edge < 1.5:
        return "small_edge"
    if abs_edge < 3.0:
        return "medium_edge"
    return "large_edge"


def _projection_row_lookup(
    projection_df: pd.DataFrame,
    warnings: list[str],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
    int,
]:
    name_column = _first_existing_column(projection_df, PROJECTION_NAME_COLUMNS)
    if name_column is None or projection_df.empty:
        return {}, {}, 0

    team_column = _first_existing_column(projection_df, PROJECTION_TEAM_COLUMNS)
    lookup: dict[str, list[dict[str, Any]]] = {}
    for _, row in projection_df.iterrows():
        row_dict = row.to_dict()
        player_name = _clean_text(row_dict.get(name_column))
        normalized_name = normalize_player_name(player_name)
        if not normalized_name:
            continue
        team_abbr = _normalize_team_abbr(
            row_dict.get(team_column) if team_column is not None else None
        )
        lookup.setdefault(normalized_name, []).append(
            {
                "player_name": player_name,
                "team_abbr": team_abbr,
                "row": row_dict,
            }
        )

    duplicate_keys = {
        normalized_name
        for normalized_name, payloads in lookup.items()
        if len(payloads) > 1
    }
    if duplicate_keys:
        warnings.append(
            "Projection source had duplicate normalized player names; "
            f"team-aware selection applied for {len(duplicate_keys)} players."
        )

    compact_items: dict[str, list[str]] = {}
    for normalized_name in lookup:
        compact_items.setdefault(_compact_name_key(normalized_name), []).append(
            normalized_name
        )
    compact_lookup = {
        compact_name: lookup[normalized_names[0]]
        for compact_name, normalized_names in compact_items.items()
        if compact_name and len(normalized_names) == 1
    }
    return lookup, compact_lookup, len(duplicate_keys)


def _select_projection_row(
    projection_candidates: list[dict[str, Any]],
    *,
    game_team_abbrs: set[str],
) -> tuple[dict[str, Any], str]:
    if game_team_abbrs:
        for candidate in projection_candidates:
            if candidate["team_abbr"] in game_team_abbrs:
                return candidate, "team_aware_matched"
    return projection_candidates[0], "name_only_matched"


def _market_game_team_abbrs(market_row: pd.Series) -> set[str]:
    team_abbrs = {
        _normalize_team_abbr(market_row.get("home_team")),
        _normalize_team_abbr(market_row.get("away_team")),
    }
    return {team_abbr for team_abbr in team_abbrs if team_abbr}


def _normalize_team_abbr(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    upper_text = text.upper()
    if upper_text in NBA_TEAM_ABBRS:
        return upper_text
    return NBA_TEAM_NAME_TO_ABBR.get(normalize_player_name(text), "")


def _load_projection_source(
    *,
    target_date: str,
    output_dir: Path,
    projection_source: str | Path | None,
    warnings: list[str],
) -> tuple[Path | None, pd.DataFrame, bool, str]:
    if projection_source:
        path = Path(projection_source)
        if not path.exists():
            warnings.append(f"Projection source not found: {path}")
            return path, pd.DataFrame(), False, "unavailable"
        return (
            path,
            _read_csv(path, warnings=warnings, source_label="projection source"),
            True,
            "explicit_projection_source",
        )

    cleaned_projection_path = output_dir / f"projection_context_clean_{target_date}.csv"
    if cleaned_projection_path.exists():
        return (
            cleaned_projection_path,
            _read_csv(
                cleaned_projection_path,
                warnings=warnings,
                source_label="projection source",
            ),
            True,
            "cleaned_projection_context",
        )

    stat_projection_path = output_dir / f"stat_projection_source_{target_date}.csv"
    if stat_projection_path.exists():
        return (
            stat_projection_path,
            _read_csv(
                stat_projection_path,
                warnings=warnings,
                source_label="projection source",
            ),
            True,
            "stat_projection_source",
        )

    baseline_path = output_dir.parent.parent / "model" / "player_baselines.csv"
    if baseline_path.exists():
        return (
            baseline_path,
            _read_csv(
                baseline_path,
                warnings=warnings,
                source_label="projection source",
            ),
            True,
            "raw_player_baselines",
        )

    warnings.append("No projection source available.")
    return None, pd.DataFrame(), False, "unavailable"


def _diagnostics_payload(
    *,
    target_date: str,
    status: str,
    market_board_path: Path,
    projection_source_path: Path | None,
    projection_source_available: bool,
    projection_source_type: str,
    joined: pd.DataFrame,
    unmatched_players: list[str],
    matched_player_count: int,
    duplicate_normalized_player_warning_count: int,
    team_aware_match_count: int,
    name_only_match_count: int,
    warnings: list[str],
    output_path: Path,
    summary_path: Path,
    diagnostics_path: Path,
    source_eligible_for_betting_any_true: bool,
    schema_missing_columns: list[str],
) -> dict[str, Any]:
    projection_value_available_count = _numeric_value_count(joined, "projection_value")
    market_row_count = int(len(joined.index))
    markets_with_projection_values = _markets_with_projection_values(joined)
    markets_missing_projection_values = _markets_missing_projection_values(
        joined,
        markets_with_projection_values,
    )
    eligible_for_betting_any_true = _eligible_any_true(joined)
    edge_available_count = _numeric_value_count(joined, "side_adjusted_edge")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": target_date,
        "target_date": target_date,
        "status": status,
        "market_board_path": str(market_board_path),
        "schema_missing_required_columns": schema_missing_columns,
        "market_row_count": market_row_count,
        "joined_row_count": market_row_count,
        "matched_player_count": int(matched_player_count),
        "unmatched_player_count": len(unmatched_players),
        "unmatched_players": unmatched_players,
        "projection_source_path": str(projection_source_path) if projection_source_path else "",
        "projection_source_available": bool(projection_source_available),
        "projection_source_type": projection_source_type,
        "used_cleaned_projection_context": (
            projection_source_type == "cleaned_projection_context"
        ),
        "duplicate_normalized_player_warning_count": int(
            duplicate_normalized_player_warning_count
        ),
        "team_aware_match_count": int(team_aware_match_count),
        "name_only_match_count": int(name_only_match_count),
        "projection_value_available_count": projection_value_available_count,
        "projection_value_missing_count": market_row_count - projection_value_available_count,
        "projection_source_type_counts": _value_counts(joined, "projection_source_type"),
        "projection_quality_flag_counts": _value_counts(
            joined,
            "projection_quality_flag",
        ),
        "edge_available_count": edge_available_count,
        "edge_missing_count": market_row_count - edge_available_count,
        "edge_bucket_counts": _value_counts(joined, "edge_bucket"),
        "positive_side_adjusted_edge_count": _positive_numeric_value_count(
            joined,
            "side_adjusted_edge",
        ),
        "markets_with_projection_values": markets_with_projection_values,
        "markets_missing_projection_values": markets_missing_projection_values,
        "source_eligible_for_betting_any_true": bool(source_eligible_for_betting_any_true),
        "eligible_for_betting_any_true": bool(eligible_for_betting_any_true),
        "eligible_for_betting_all_join_rows_false": not eligible_for_betting_any_true,
        "betting_mode_integrated": False,
        "market_prop_rows_created": 0,
        "elite_rows_created": 0,
        "kelly_called": False,
        "operator_artifacts_written": [],
        "operator_betting_boards_written": [],
        "warnings": warnings,
        "artifacts": {
            "market_projection_join_csv": str(output_path),
            "market_projection_join_summary_txt": str(summary_path),
            "market_projection_join_diagnostics_json": str(diagnostics_path),
        },
    }


def _write_outputs(
    output_path: Path,
    summary_path: Path,
    diagnostics_path: Path,
    joined: pd.DataFrame,
    diagnostics: dict[str, Any],
) -> None:
    joined.to_csv(output_path, index=False)
    _write_summary(summary_path, diagnostics)
    diagnostics_path.write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_summary(path: Path, diagnostics: dict[str, Any]) -> None:
    lines = [
        f"Market Projection Edge Preview - {diagnostics['date']}",
        f"status: {diagnostics['status']}",
        f"market_board: {diagnostics['market_board_path']}",
        f"projection_source_path: {diagnostics['projection_source_path'] or 'none'}",
        f"projection_source_type: {diagnostics['projection_source_type']}",
        f"projection_source_available: {diagnostics['projection_source_available']}",
        f"used_cleaned_projection_context: {diagnostics['used_cleaned_projection_context']}",
        f"market_row_count: {diagnostics['market_row_count']}",
        f"joined_row_count: {diagnostics['joined_row_count']}",
        f"matched_player_count: {diagnostics['matched_player_count']}",
        f"team_aware_match_count: {diagnostics['team_aware_match_count']}",
        f"name_only_match_count: {diagnostics['name_only_match_count']}",
        f"unmatched_player_count: {diagnostics['unmatched_player_count']}",
        (
            "duplicate_normalized_player_warning_count: "
            f"{diagnostics['duplicate_normalized_player_warning_count']}"
        ),
        f"unmatched_players: {_list_inline(diagnostics['unmatched_players'])}",
        f"projection_value_available_count: {diagnostics['projection_value_available_count']}",
        f"projection_value_missing_count: {diagnostics['projection_value_missing_count']}",
        f"projection_source_type_counts: {_counts_inline(diagnostics['projection_source_type_counts'])}",
        f"projection_quality_flag_counts: {_counts_inline(diagnostics['projection_quality_flag_counts'])}",
        f"edge_available_count: {diagnostics['edge_available_count']}",
        f"edge_missing_count: {diagnostics['edge_missing_count']}",
        f"edge_bucket_counts: {_counts_inline(diagnostics['edge_bucket_counts'])}",
        f"positive_side_adjusted_edge_count: {diagnostics['positive_side_adjusted_edge_count']}",
        f"markets_with_projection_values: {_list_inline(diagnostics['markets_with_projection_values'])}",
        f"markets_missing_projection_values: {_list_inline(diagnostics['markets_missing_projection_values'])}",
        f"eligible_for_betting_any_true: {diagnostics['eligible_for_betting_any_true']}",
        "market_prop_rows_created: 0",
        "elite_rows_created: 0",
        "kelly_called: False",
        "operator_betting_boards_written: 0",
        "",
        "WARNING: Research-only edge preview. Fallback projections are not betting-approved.",
        "No picks, MarketProp rows, Elite rows, Kelly calls, or operator betting boards were created.",
    ]
    if diagnostics["warnings"]:
        lines.extend(["warnings:", *[f"  {warning}" for warning in diagnostics["warnings"]]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _empty_join_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=[*MARKET_REQUIRED_COLUMNS, "eligible_for_betting", *JOIN_EXTRA_COLUMNS])


def _read_csv(path: Path, *, warnings: list[str], source_label: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        warnings.append(f"{source_label} is empty: {path}")
    except Exception as exc:
        warnings.append(f"Could not read {source_label} {path}: {type(exc).__name__}: {exc}")
    return pd.DataFrame()


def _context_output_columns(projection_df: pd.DataFrame) -> dict[str, str]:
    columns = _column_lookup(projection_df)
    output_columns: dict[str, str] = {}
    for source_column in CONTEXT_COLUMNS:
        actual = columns.get(source_column.lower())
        if actual:
            output_columns[actual] = f"projection_{source_column}"
    return output_columns


def _market_value(
    row: dict[str, Any],
    columns: dict[str, str],
    aliases: list[str],
) -> float | None:
    for alias in aliases:
        actual_column = columns.get(alias.lower())
        if actual_column is None:
            continue
        value = _coerce_number(row.get(actual_column))
        if value is not None:
            return value
    return None


def _column_lookup(df: pd.DataFrame) -> dict[str, str]:
    return {str(column).strip().lower(): str(column) for column in df.columns}


def _first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    columns = _column_lookup(df)
    for candidate in candidates:
        actual = columns.get(candidate.lower())
        if actual:
            return actual
    return None


def _ordered_market_players(df: pd.DataFrame) -> list[str]:
    if "player_name" not in df.columns:
        return []
    players: list[str] = []
    seen: set[str] = set()
    for value in df["player_name"].tolist():
        player = _clean_text(value)
        normalized = normalize_player_name(player)
        if player and normalized not in seen:
            players.append(player)
            seen.add(normalized)
    return players


def _unique_market_player_count(df: pd.DataFrame) -> int:
    return len(_ordered_market_players(df))


def _numeric_value_count(df: pd.DataFrame, column: str) -> int:
    if column not in df.columns:
        return 0
    return int(sum(_coerce_number(value) is not None for value in df[column].tolist()))


def _positive_numeric_value_count(df: pd.DataFrame, column: str) -> int:
    if column not in df.columns:
        return 0
    return int(
        sum(
            value is not None and value > 0
            for value in (_coerce_number(raw_value) for raw_value in df[column].tolist())
        )
    )


def _value_counts(df: pd.DataFrame, column: str) -> dict[str, int]:
    if column not in df.columns:
        return {}
    counts: dict[str, int] = {}
    for raw_value in df[column].tolist():
        value = _clean_text(raw_value)
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


def _markets_with_projection_values(df: pd.DataFrame) -> list[str]:
    if "market_type" not in df.columns or "projection_value" not in df.columns:
        return []
    markets: list[str] = []
    seen: set[str] = set()
    for market in _ordered_non_missing_values(df, "market_type"):
        market_rows = df[df["market_type"].map(_clean_text) == market]
        if _numeric_value_count(market_rows, "projection_value") > 0 and market not in seen:
            markets.append(market)
            seen.add(market)
    return markets


def _markets_missing_projection_values(
    df: pd.DataFrame,
    markets_with_projection_values: list[str],
) -> list[str]:
    with_values = set(markets_with_projection_values)
    return [
        market
        for market in _ordered_non_missing_values(df, "market_type")
        if market not in with_values
    ]


def _ordered_non_missing_values(df: pd.DataFrame, column: str) -> list[str]:
    if column not in df.columns:
        return []
    values: list[str] = []
    seen: set[str] = set()
    for raw_value in df[column].tolist():
        value = _clean_text(raw_value)
        if value and value not in seen:
            values.append(value)
            seen.add(value)
    return values


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
    text = str(value).strip().lower()
    return text in {"true", "1", "yes", "y"}


def _coerce_number(value: Any) -> float | None:
    if _is_missing(value):
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    try:
        if pd.isna(parsed):
            return None
    except TypeError:
        pass
    return parsed


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _compact_name_key(normalized_name: str) -> str:
    return normalized_name.replace(" ", "")


def _list_inline(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def _counts_inline(counts: dict[str, int]) -> str:
    return ", ".join(f"{key}={value}" for key, value in counts.items()) if counts else "none"


def _validate_date(value: str) -> str:
    text = str(value).strip()
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("--date must be in YYYY-MM-DD format") from exc
    return text


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run CourtVision research-only market/projection join preview."
    )
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")
    parser.add_argument(
        "--market-board",
        default=None,
        help="Market validation board CSV. Defaults to output-dir/date naming.",
    )
    parser.add_argument(
        "--projection-source",
        default=None,
        help="Optional projection/stat context CSV. Defaults to research source, then model baselines.",
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
        result = run_market_projection_join(
            target_date=args.date,
            market_board=args.market_board,
            projection_source=args.projection_source,
            output_dir=args.output_dir,
            diagnostics_dir=args.diagnostics_dir,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"status: {result.status}")
    print(f"join: {result.output_path}")
    print(f"summary: {result.summary_path}")
    print(f"diagnostics: {result.diagnostics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
