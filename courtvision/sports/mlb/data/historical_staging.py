"""Isolated staging builder for candidate MLB HR historical input packs.

The builder consumes caller-supplied local files only. It validates the
Retrosheet-to-MLBAM crosswalk before doing any other work, derives the two
pack-facing Retrosheet CSVs, and runs the historical-pack preflight before
finalizing files in a caller-selected staging directory.

Nothing in this module enables betting, production approval, EV, Kelly, Elite,
or runtime promotion.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import shutil
from types import MappingProxyType
from typing import Final, Mapping, Sequence
from uuid import uuid4

from courtvision.sports.mlb.data.crosswalk_validation import (
    MLB_HR_CROSSWALK_VERSION,
    REQUIRED_CROSSWALK_COLUMNS,
    MLBHRCrosswalkValidationResult,
    validate_mlb_hr_crosswalk_csv,
)
from courtvision.sports.mlb.data.historical_input_pack import (
    HISTORICAL_INPUT_PACK_MODE,
    HISTORICAL_INPUT_PACK_VERSION,
    INPUT_PACK_MANIFEST_FILENAME,
    PACK_SOURCE_FILES,
    REQUIRED_PACK_COLUMNS,
    HistoricalInputPackValidationResult,
    historical_input_pack_paths,
    preflight_historical_input_pack,
)
from courtvision.sports.mlb.data.odds_snapshot_ingestion import (
    normalize_player_name,
)
from courtvision.sports.mlb.data_manifest import compute_file_sha256


HISTORICAL_STAGING_VERSION: Final = "mlb-hr-historical-staging-v1"

REQUIRED_RETROSHEET_LABEL_COLUMNS: Final = frozenset(
    {
        "retrosheet_game_id",
        "game_date",
        "game_number",
        "retrosheet_home_team_id",
        "retrosheet_away_team_id",
        "venue_name",
        "home_score",
        "away_score",
        "game_status",
        "retrosheet_batting_team_id",
        "retrosheet_fielding_team_id",
        "retrosheet_batter_id",
        "batter_name",
        "pitcher_id",
        "pitcher_name",
        "inning",
        "event_type",
        "event_text",
        "is_home_run",
        "rbi",
        "source_type",
    }
)

_RETROSHEET_GAME_FIELDS: Final = (
    "game_id",
    "game_date",
    "home_team",
    "away_team",
    "game_number",
    "venue_name",
    "home_score",
    "away_score",
    "game_status",
    "source_type",
)
_RETROSHEET_EVENT_FIELDS: Final = (
    "game_id",
    "game_date",
    "inning",
    "batting_team",
    "fielding_team",
    "batter_id",
    "batter_name",
    "pitcher_id",
    "pitcher_name",
    "event_type",
    "event_text",
    "is_home_run",
    "rbi",
    "source_type",
)
_WEATHER_FIELDS: Final = (
    "game_id",
    "game_date",
    "event_start_time",
    "venue_name",
    "latitude",
    "longitude",
    "temperature",
    "wind_speed",
    "wind_direction",
    "wind_out_to_field",
    "humidity",
    "precipitation",
    "roof_status",
    "source_name",
    "source_type",
    "collected_at",
    "as_of_date",
)
_ODDS_FIELDS: Final = (
    "game_date",
    "game_id",
    "player_id",
    "player_name",
    "team",
    "opponent",
    "market_type",
    "sportsbook",
    "american_odds",
    "decimal_odds",
    "odds_collected_at",
    "event_start_time",
    "home_team",
    "away_team",
    "provider",
    "source_type",
    "market_label",
    "selection_name",
)

_FORBIDDEN_STAGING_PARTS: Final = frozenset(
    {
        "cache",
        "caches",
        "history",
        "manual",
        "manualdata",
        "output",
        "outputs",
        "pytestcache",
        "pycache",
        "runtime",
        "testoutputs",
    }
)
_POSITIVE_MLBAM_ID = re.compile(r"^[1-9]\d{5,9}$")
_PROHIBITED_IDENTITY = re.compile(
    r"(?:^|[^a-z0-9])"
    r"(?:sample|fixture|mock|test|synthetic|dummy|fake|example|placeholder)"
    r"(?:$|[^a-z0-9])",
    re.IGNORECASE,
)


class HistoricalStagingBuildError(ValueError):
    """Raised when local sources cannot produce a preflight-valid pack."""


@dataclass(frozen=True, slots=True)
class HistoricalStagingSourcePaths:
    """Resolved caller-supplied source paths for one staging build."""

    statcast: Path
    retrosheet_labels: Path
    crosswalk: Path
    weather: Path
    ballpark: Path
    odds_context: Path

    def as_mapping(self) -> Mapping[str, Path]:
        return MappingProxyType(
            {
                "statcast": self.statcast,
                "retrosheet_labels": self.retrosheet_labels,
                "crosswalk": self.crosswalk,
                "weather": self.weather,
                "ballpark": self.ballpark,
                "odds_context": self.odds_context,
            }
        )


@dataclass(frozen=True, slots=True)
class HistoricalStagingBuildResult:
    """Successful candidate-pack build evidence."""

    output_dir: Path
    source_paths: HistoricalStagingSourcePaths
    crosswalk_validation: MLBHRCrosswalkValidationResult
    preflight: HistoricalInputPackValidationResult


@dataclass(frozen=True, slots=True)
class _CSVTable:
    path: Path
    fieldnames: tuple[str, ...]
    rows: tuple[Mapping[str, str], ...]


@dataclass(frozen=True, slots=True)
class _GameContext:
    retrosheet_game_id: str
    mlbam_game_id: str
    game_date: str
    game_number: str
    retrosheet_home_team_id: str
    retrosheet_away_team_id: str
    home_team: str
    away_team: str


@dataclass(frozen=True, slots=True)
class _PlayerContext:
    game: _GameContext
    retrosheet_batter_id: str
    mlbam_batter_id: str
    batter_name: str
    retrosheet_batting_team_id: str
    retrosheet_fielding_team_id: str
    batting_team: str
    fielding_team: str


def _text(row: Mapping[str, object], field_name: str) -> str:
    value = row.get(field_name)
    return "" if value is None else str(value).strip()


def _read_csv(
    path: str | Path,
    *,
    label: str,
    required_columns: frozenset[str] = frozenset(),
) -> _CSVTable:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise HistoricalStagingBuildError(f"{label} CSV does not exist: {source}")
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = tuple(reader.fieldnames or ())
            if not headers:
                raise HistoricalStagingBuildError(
                    f"{label} CSV is missing a header row"
                )
            if len(headers) != len(set(headers)):
                raise HistoricalStagingBuildError(
                    f"{label} CSV contains duplicate column names"
                )
            missing = sorted(required_columns - set(headers))
            if missing:
                raise HistoricalStagingBuildError(
                    f"{label} CSV is missing required columns: " + ", ".join(missing)
                )
            rows: list[Mapping[str, str]] = []
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise HistoricalStagingBuildError(
                        f"row {row_number}: {label} CSV has extra values"
                    )
                rows.append(MappingProxyType(dict(row)))
    except HistoricalStagingBuildError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise HistoricalStagingBuildError(
            f"could not read {label} CSV {source}: {exc}"
        ) from exc
    if not rows:
        raise HistoricalStagingBuildError(f"{label} CSV contains no data rows")
    return _CSVTable(source, headers, tuple(rows))


def _crosswalk_contexts(
    table: _CSVTable,
) -> tuple[
    dict[str, _GameContext],
    dict[str, _GameContext],
    dict[tuple[str, str], _PlayerContext],
    dict[tuple[str, str], _PlayerContext],
]:
    games_by_retrosheet: dict[str, _GameContext] = {}
    games_by_mlbam: dict[str, _GameContext] = {}
    players_by_retrosheet: dict[tuple[str, str], _PlayerContext] = {}
    players_by_mlbam: dict[tuple[str, str], _PlayerContext] = {}
    for row in table.rows:
        game = _GameContext(
            retrosheet_game_id=_text(row, "retrosheet_game_id"),
            mlbam_game_id=_text(row, "mlbam_game_id"),
            game_date=_text(row, "game_date"),
            game_number=_text(row, "game_number"),
            retrosheet_home_team_id=_text(row, "retrosheet_home_team_id"),
            retrosheet_away_team_id=_text(row, "retrosheet_away_team_id"),
            home_team=_text(row, "home_team"),
            away_team=_text(row, "away_team"),
        )
        games_by_retrosheet.setdefault(game.retrosheet_game_id, game)
        games_by_mlbam.setdefault(game.mlbam_game_id, game)
        player = _PlayerContext(
            game=game,
            retrosheet_batter_id=_text(row, "retrosheet_batter_id"),
            mlbam_batter_id=_text(row, "mlbam_batter_id"),
            batter_name=_text(row, "batter_name"),
            retrosheet_batting_team_id=_text(row, "retrosheet_batting_team_id"),
            retrosheet_fielding_team_id=_text(row, "retrosheet_fielding_team_id"),
            batting_team=_text(row, "batting_team"),
            fielding_team=_text(row, "fielding_team"),
        )
        if not player.retrosheet_batter_id:
            raise HistoricalStagingBuildError(
                "staging requires a Retrosheet batter id for every crosswalk row"
            )
        players_by_retrosheet[
            (game.retrosheet_game_id, player.retrosheet_batter_id)
        ] = player
        players_by_mlbam[(game.mlbam_game_id, player.mlbam_batter_id)] = player
    return (
        games_by_retrosheet,
        games_by_mlbam,
        players_by_retrosheet,
        players_by_mlbam,
    )


def _require_equal(
    actual: str,
    expected: str,
    *,
    row_number: int,
    label: str,
) -> None:
    if actual != expected:
        raise HistoricalStagingBuildError(
            f"row {row_number}: {label} does not match the validated crosswalk: "
            f"found={actual!r}, expected={expected!r}"
        )


def _transform_retrosheet_labels(
    labels: _CSVTable,
    players_by_retrosheet: Mapping[tuple[str, str], _PlayerContext],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    games: dict[str, dict[str, str]] = {}
    events: list[dict[str, str]] = []
    seen_players: set[tuple[str, str]] = set()

    for row_number, row in enumerate(labels.rows, start=2):
        native_key = (
            _text(row, "retrosheet_game_id"),
            _text(row, "retrosheet_batter_id"),
        )
        context = players_by_retrosheet.get(native_key)
        if context is None:
            raise HistoricalStagingBuildError(
                f"row {row_number}: Retrosheet label has no exact validated "
                f"crosswalk row: {native_key}"
            )
        if native_key in seen_players:
            raise HistoricalStagingBuildError(
                f"row {row_number}: duplicate Retrosheet batter-game label: {native_key}"
            )
        seen_players.add(native_key)

        game = context.game
        for field_name, expected in (
            ("game_date", game.game_date),
            ("game_number", game.game_number),
            ("retrosheet_home_team_id", game.retrosheet_home_team_id),
            ("retrosheet_away_team_id", game.retrosheet_away_team_id),
            (
                "retrosheet_batting_team_id",
                context.retrosheet_batting_team_id,
            ),
            (
                "retrosheet_fielding_team_id",
                context.retrosheet_fielding_team_id,
            ),
        ):
            _require_equal(
                _text(row, field_name),
                expected,
                row_number=row_number,
                label=field_name,
            )
        if normalize_player_name(_text(row, "batter_name")) != normalize_player_name(
            context.batter_name
        ):
            raise HistoricalStagingBuildError(
                f"row {row_number}: batter_name does not match the validated crosswalk"
            )

        pitcher_id = _text(row, "pitcher_id")
        pitcher_name = _text(row, "pitcher_name")
        if not _POSITIVE_MLBAM_ID.fullmatch(pitcher_id):
            raise HistoricalStagingBuildError(
                f"row {row_number}: pitcher_id must be a positive 6-10 digit MLBAM id"
            )
        if not pitcher_name or _PROHIBITED_IDENTITY.search(pitcher_name):
            raise HistoricalStagingBuildError(
                f"row {row_number}: pitcher_name is missing or synthetic"
            )

        game_row = {
            "game_id": game.mlbam_game_id,
            "game_date": game.game_date,
            "home_team": game.home_team,
            "away_team": game.away_team,
            "game_number": game.game_number,
            "venue_name": _text(row, "venue_name"),
            "home_score": _text(row, "home_score"),
            "away_score": _text(row, "away_score"),
            "game_status": _text(row, "game_status").lower(),
            "source_type": _text(row, "source_type").lower(),
        }
        previous_game = games.setdefault(game.retrosheet_game_id, game_row)
        if previous_game != game_row:
            raise HistoricalStagingBuildError(
                f"row {row_number}: conflicting Retrosheet game context for "
                f"{game.retrosheet_game_id}"
            )

        events.append(
            {
                "game_id": game.mlbam_game_id,
                "game_date": game.game_date,
                "inning": _text(row, "inning"),
                "batting_team": context.batting_team,
                "fielding_team": context.fielding_team,
                "batter_id": context.mlbam_batter_id,
                "batter_name": context.batter_name,
                "pitcher_id": pitcher_id,
                "pitcher_name": pitcher_name,
                "event_type": _text(row, "event_type"),
                "event_text": _text(row, "event_text"),
                "is_home_run": _text(row, "is_home_run"),
                "rbi": _text(row, "rbi"),
                "source_type": _text(row, "source_type").lower(),
            }
        )

    expected_players = set(players_by_retrosheet)
    if seen_players != expected_players:
        raise HistoricalStagingBuildError(
            "Retrosheet label coverage must exactly match the validated crosswalk: "
            f"missing={sorted(expected_players - seen_players)}, "
            f"unexpected={sorted(seen_players - expected_players)}"
        )
    return list(games.values()), events


def _validate_statcast_context(
    table: _CSVTable,
    *,
    players_by_mlbam: Mapping[tuple[str, str], _PlayerContext],
    event_rows: Sequence[Mapping[str, object]],
) -> None:
    seen_players: set[tuple[str, str]] = set()
    pitchers_by_player: dict[tuple[str, str], set[str]] = {}
    for row_number, row in enumerate(table.rows, start=2):
        key = (_text(row, "game_pk"), _text(row, "batter"))
        context = players_by_mlbam.get(key)
        if context is None:
            raise HistoricalStagingBuildError(
                f"row {row_number}: Statcast batter-game does not match the "
                f"validated crosswalk: {key}"
            )
        seen_players.add(key)
        game = context.game
        for field_name, expected in (
            ("game_date", game.game_date),
            ("home_team", game.home_team),
            ("away_team", game.away_team),
        ):
            _require_equal(
                _text(row, field_name),
                expected,
                row_number=row_number,
                label=f"Statcast {field_name}",
            )
        if normalize_player_name(_text(row, "player_name")) != normalize_player_name(
            context.batter_name
        ):
            raise HistoricalStagingBuildError(
                f"row {row_number}: Statcast player_name does not match crosswalk"
            )
        pitcher_id = _text(row, "pitcher")
        if not _POSITIVE_MLBAM_ID.fullmatch(pitcher_id):
            raise HistoricalStagingBuildError(
                f"row {row_number}: Statcast pitcher must be a positive 6-10 digit "
                "MLBAM id"
            )
        pitchers_by_player.setdefault(key, set()).add(pitcher_id)

    expected_players = set(players_by_mlbam)
    if seen_players != expected_players:
        raise HistoricalStagingBuildError(
            "Statcast batter-game coverage must exactly match the validated "
            f"crosswalk: missing={sorted(expected_players - seen_players)}, "
            f"unexpected={sorted(seen_players - expected_players)}"
        )
    for event in event_rows:
        key = (_text(event, "game_id"), _text(event, "batter_id"))
        pitcher_id = _text(event, "pitcher_id")
        if pitcher_id not in pitchers_by_player.get(key, set()):
            raise HistoricalStagingBuildError(
                "Retrosheet label pitcher does not match any Statcast pitcher for "
                f"the batter-game: game={key[0]!r}, batter={key[1]!r}, "
                f"pitcher={pitcher_id!r}"
            )


def _resolve_game_context(
    row: Mapping[str, object],
    *,
    row_number: int,
    label: str,
    games_by_retrosheet: Mapping[str, _GameContext],
    games_by_mlbam: Mapping[str, _GameContext],
) -> _GameContext:
    retrosheet_id = _text(row, "retrosheet_game_id")
    mlbam_id = _text(row, "game_id")
    by_native = games_by_retrosheet.get(retrosheet_id) if retrosheet_id else None
    by_mlbam = games_by_mlbam.get(mlbam_id) if mlbam_id else None
    if retrosheet_id and by_native is None:
        raise HistoricalStagingBuildError(
            f"row {row_number}: {label} Retrosheet game does not match crosswalk: "
            f"{retrosheet_id!r}"
        )
    if mlbam_id and by_mlbam is None:
        raise HistoricalStagingBuildError(
            f"row {row_number}: {label} game does not match crosswalk: {mlbam_id!r}"
        )
    context = by_native or by_mlbam
    if context is None:
        raise HistoricalStagingBuildError(
            f"row {row_number}: {label} requires game_id or retrosheet_game_id"
        )
    if by_native is not None and by_mlbam is not None and by_native != by_mlbam:
        raise HistoricalStagingBuildError(
            f"row {row_number}: {label} native and canonical game ids conflict"
        )
    _require_equal(
        _text(row, "game_date"),
        context.game_date,
        row_number=row_number,
        label=f"{label} game_date",
    )
    return context


def _transform_weather(
    table: _CSVTable,
    *,
    games_by_retrosheet: Mapping[str, _GameContext],
    games_by_mlbam: Mapping[str, _GameContext],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row_number, row in enumerate(table.rows, start=2):
        game = _resolve_game_context(
            row,
            row_number=row_number,
            label="weather",
            games_by_retrosheet=games_by_retrosheet,
            games_by_mlbam=games_by_mlbam,
        )
        transformed = dict(row)
        transformed["game_id"] = game.mlbam_game_id
        rows.append(transformed)
    return rows


def _transform_odds(
    table: _CSVTable,
    *,
    games_by_retrosheet: Mapping[str, _GameContext],
    games_by_mlbam: Mapping[str, _GameContext],
    players_by_retrosheet: Mapping[tuple[str, str], _PlayerContext],
    players_by_mlbam: Mapping[tuple[str, str], _PlayerContext],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row_number, row in enumerate(table.rows, start=2):
        game = _resolve_game_context(
            row,
            row_number=row_number,
            label="odds",
            games_by_retrosheet=games_by_retrosheet,
            games_by_mlbam=games_by_mlbam,
        )
        native_player = _text(row, "retrosheet_batter_id")
        canonical_player = _text(row, "player_id")
        by_native = (
            players_by_retrosheet.get(
                (game.retrosheet_game_id, native_player)
            )
            if native_player
            else None
        )
        by_mlbam = (
            players_by_mlbam.get((game.mlbam_game_id, canonical_player))
            if canonical_player
            else None
        )
        if native_player and by_native is None:
            raise HistoricalStagingBuildError(
                f"row {row_number}: odds player does not match crosswalk: "
                f"{native_player!r}"
            )
        if canonical_player and by_mlbam is None:
            raise HistoricalStagingBuildError(
                f"row {row_number}: odds player does not match crosswalk: "
                f"{canonical_player!r}"
            )
        context = by_native or by_mlbam
        if context is None:
            raise HistoricalStagingBuildError(
                f"row {row_number}: odds requires player_id or retrosheet_batter_id"
            )
        if by_native is not None and by_mlbam is not None and by_native != by_mlbam:
            raise HistoricalStagingBuildError(
                f"row {row_number}: odds native and canonical player ids conflict"
            )
        if normalize_player_name(_text(row, "player_name")) != normalize_player_name(
            context.batter_name
        ):
            raise HistoricalStagingBuildError(
                f"row {row_number}: odds player name does not match crosswalk"
            )
        for field_name, expected in (
            ("team", context.batting_team),
            ("opponent", context.fielding_team),
            ("home_team", game.home_team),
            ("away_team", game.away_team),
        ):
            _require_equal(
                _text(row, field_name),
                expected,
                row_number=row_number,
                label=f"odds {field_name}",
            )
        transformed = dict(row)
        transformed["game_id"] = game.mlbam_game_id
        transformed["player_id"] = context.mlbam_batter_id
        transformed["player_name"] = context.batter_name
        rows.append(transformed)
    return rows


def _ordered_fields(
    preferred: Sequence[str], source_fields: Sequence[str]
) -> tuple[str, ...]:
    ordered = list(dict.fromkeys((*preferred, *source_fields)))
    return tuple(ordered)


def _write_csv(
    path: Path,
    *,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _normalized_path_part(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _validate_output_directory(path: str | Path) -> tuple[Path, bool]:
    output_dir = Path(path).expanduser().resolve()
    forbidden = [
        part
        for part in output_dir.parts
        if _normalized_path_part(part) in _FORBIDDEN_STAGING_PARTS
    ]
    if forbidden:
        raise HistoricalStagingBuildError(
            "staging output cannot be inside manual-data, output, history, "
            f"runtime, or cache folders: {output_dir}"
        )
    if output_dir.exists():
        if not output_dir.is_dir():
            raise HistoricalStagingBuildError(
                f"output staging path is not a directory: {output_dir}"
            )
        if any(output_dir.iterdir()):
            raise HistoricalStagingBuildError(
                f"output staging directory must be empty: {output_dir}"
            )
        return output_dir, False
    if not output_dir.parent.is_dir():
        raise HistoricalStagingBuildError(
            f"output staging parent directory does not exist: {output_dir.parent}"
        )
    return output_dir, True


def _date_range(rows: Sequence[Mapping[str, object]]) -> tuple[str, str]:
    values: list[date] = []
    for row in rows:
        raw = _text(row, "game_date")
        try:
            values.append(date.fromisoformat(raw))
        except ValueError as exc:
            raise HistoricalStagingBuildError(
                f"game_date must be an ISO date before staging: {raw!r}"
            ) from exc
    if not values:
        raise HistoricalStagingBuildError("cannot derive a date range from zero rows")
    return min(values).isoformat(), max(values).isoformat()


def _provider_label(
    rows: Sequence[Mapping[str, object]],
    field_name: str,
    fallback: str,
) -> str:
    values = sorted({_text(row, field_name) for row in rows if _text(row, field_name)})
    return "+".join(values) if values else fallback


def _input_record(name: str, table: _CSVTable) -> dict[str, object]:
    return {
        "input_name": name,
        "path": str(table.path),
        "sha256": compute_file_sha256(table.path),
        "byte_size": table.path.stat().st_size,
        "row_count": len(table.rows),
        "source_classification": "real",
    }


def _manifest_source_entry(
    *,
    source_name: str,
    path: Path,
    provider_label: str,
    row_count: int,
    date_range: tuple[str, str],
    created_at: str,
    source_tables: Sequence[_CSVTable],
) -> dict[str, object]:
    return {
        "source_name": source_name,
        "provider_label": provider_label,
        "source_type": "local_file",
        "source_classification": "real",
        "path": PACK_SOURCE_FILES[source_name],
        "sha256": compute_file_sha256(path),
        "byte_size": path.stat().st_size,
        "parsed_row_count": row_count,
        "created_at": created_at,
        "date_range_start": date_range[0],
        "date_range_end": date_range[1],
        "required_or_optional": "required",
        "loaded_successfully": True,
        "source_paths": [str(table.path) for table in source_tables],
        "source_hashes": [compute_file_sha256(table.path) for table in source_tables],
        "transformation_classification": "research_only_candidate",
    }


def _write_manifest(
    *,
    temporary_dir: Path,
    created_at: str,
    pack_range: tuple[str, str],
    output_rows: Mapping[str, Sequence[Mapping[str, object]]],
    providers: Mapping[str, str],
    source_tables: Mapping[str, Sequence[_CSVTable]],
    input_tables: Mapping[str, _CSVTable],
    crosswalk_validation: MLBHRCrosswalkValidationResult,
) -> None:
    sources = []
    for source_name in PACK_SOURCE_FILES:
        source_range = (
            pack_range
            if source_name == "ballpark_factors"
            else _date_range(output_rows[source_name])
        )
        sources.append(
            _manifest_source_entry(
                source_name=source_name,
                path=temporary_dir / PACK_SOURCE_FILES[source_name],
                provider_label=providers[source_name],
                row_count=len(output_rows[source_name]),
                date_range=source_range,
                created_at=created_at,
                source_tables=source_tables[source_name],
            )
        )
    crosswalk = input_tables["crosswalk"]
    payload = {
        "manifest_version": HISTORICAL_INPUT_PACK_VERSION,
        "mode": HISTORICAL_INPUT_PACK_MODE,
        "staging_version": HISTORICAL_STAGING_VERSION,
        "created_at": created_at,
        "source_classification": "real",
        "transformation_classification": "research_only_candidate",
        "dataset_date_range_start": pack_range[0],
        "dataset_date_range_end": pack_range[1],
        "approval_status": "not_approved",
        "eligible_for_betting": False,
        "kelly_eligible": False,
        "crosswalk": {
            "contract_version": MLB_HR_CROSSWALK_VERSION,
            "path": str(crosswalk.path),
            "sha256": compute_file_sha256(crosswalk.path),
            "byte_size": crosswalk.path.stat().st_size,
            "row_count": crosswalk_validation.row_count,
            "provider_label": "retrosheet_mlbam_verified_crosswalk",
            "source_classification": "real",
            "validation_status": "valid",
            "warnings": list(crosswalk_validation.warnings),
        },
        "input_sources": [
            _input_record(name, table) for name, table in input_tables.items()
        ],
        "sources": sources,
    }
    manifest_path = temporary_dir / INPUT_PACK_MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_historical_input_pack_staging(
    *,
    statcast_csv: str | Path,
    retrosheet_labels_csv: str | Path,
    crosswalk_csv: str | Path,
    weather_csv: str | Path,
    ballpark_csv: str | Path,
    odds_context_csv: str | Path,
    output_staging_dir: str | Path,
) -> HistoricalStagingBuildResult:
    """Build one isolated, research-only candidate historical input pack."""

    # This must remain the first validation operation. In particular, no output
    # directory is checked or created before the crosswalk has passed.
    crosswalk_validation = validate_mlb_hr_crosswalk_csv(crosswalk_csv)
    if not crosswalk_validation.is_valid:
        raise HistoricalStagingBuildError(
            "crosswalk validation failed before staging: "
            + "; ".join(crosswalk_validation.errors)
        )

    crosswalk = _read_csv(
        crosswalk_csv,
        label="crosswalk",
        required_columns=REQUIRED_CROSSWALK_COLUMNS,
    )
    labels = _read_csv(
        retrosheet_labels_csv,
        label="Retrosheet/game labels",
        required_columns=REQUIRED_RETROSHEET_LABEL_COLUMNS,
    )
    statcast = _read_csv(
        statcast_csv,
        label="Statcast",
        required_columns=REQUIRED_PACK_COLUMNS["statcast"],
    )
    weather_required = REQUIRED_PACK_COLUMNS["weather"] - {"game_id"}
    weather = _read_csv(
        weather_csv,
        label="weather",
        required_columns=weather_required,
    )
    ballpark = _read_csv(
        ballpark_csv,
        label="ballpark",
        required_columns=REQUIRED_PACK_COLUMNS["ballpark_factors"],
    )
    odds_required = REQUIRED_PACK_COLUMNS["odds_snapshot"] - {
        "game_id",
        "player_id",
    }
    odds = _read_csv(
        odds_context_csv,
        label="odds/context",
        required_columns=odds_required,
    )
    output_dir, create_output_dir = _validate_output_directory(output_staging_dir)

    (
        games_by_retrosheet,
        games_by_mlbam,
        players_by_retrosheet,
        players_by_mlbam,
    ) = _crosswalk_contexts(crosswalk)
    game_rows, event_rows = _transform_retrosheet_labels(
        labels,
        players_by_retrosheet,
    )
    _validate_statcast_context(
        statcast,
        players_by_mlbam=players_by_mlbam,
        event_rows=event_rows,
    )
    weather_rows = _transform_weather(
        weather,
        games_by_retrosheet=games_by_retrosheet,
        games_by_mlbam=games_by_mlbam,
    )
    odds_rows = _transform_odds(
        odds,
        games_by_retrosheet=games_by_retrosheet,
        games_by_mlbam=games_by_mlbam,
        players_by_retrosheet=players_by_retrosheet,
        players_by_mlbam=players_by_mlbam,
    )

    source_paths = HistoricalStagingSourcePaths(
        statcast=statcast.path,
        retrosheet_labels=labels.path,
        crosswalk=crosswalk.path,
        weather=weather.path,
        ballpark=ballpark.path,
        odds_context=odds.path,
    )
    output_rows: dict[str, Sequence[Mapping[str, object]]] = {
        "statcast": statcast.rows,
        "retrosheet_games": game_rows,
        "retrosheet_events": event_rows,
        "weather": weather_rows,
        "ballpark_factors": ballpark.rows,
        "odds_snapshot": odds_rows,
    }
    pack_range = _date_range(game_rows)
    providers = {
        "statcast": "baseball_savant_statcast",
        "retrosheet_games": "retrosheet_game_labels",
        "retrosheet_events": "retrosheet_event_labels",
        "weather": _provider_label(
            weather.rows, "source_name", "local_historical_weather"
        ),
        "ballpark_factors": _provider_label(
            ballpark.rows, "source_name", "local_ballpark_factors"
        ),
        "odds_snapshot": _provider_label(
            odds.rows, "provider", "local_historical_odds"
        ),
    }
    source_tables: dict[str, Sequence[_CSVTable]] = {
        "statcast": (statcast,),
        "retrosheet_games": (labels, crosswalk),
        "retrosheet_events": (labels, crosswalk),
        "weather": (weather, crosswalk),
        "ballpark_factors": (ballpark,),
        "odds_snapshot": (odds, crosswalk),
    }
    input_tables = {
        "statcast": statcast,
        "retrosheet_labels": labels,
        "crosswalk": crosswalk,
        "weather": weather,
        "ballpark": ballpark,
        "odds_context": odds,
    }

    temporary_dir: Path | None = None
    finalized: list[Path] = []
    created_output_dir = False
    succeeded = False
    try:
        if create_output_dir:
            output_dir.mkdir()
            created_output_dir = True
        temporary_dir = output_dir / f".courtvision-staging-{uuid4().hex}"
        temporary_dir.mkdir()
        temporary_paths = historical_input_pack_paths(temporary_dir)

        shutil.copyfile(statcast.path, temporary_paths.statcast)
        _write_csv(
            temporary_paths.retrosheet_games,
            fieldnames=_RETROSHEET_GAME_FIELDS,
            rows=game_rows,
        )
        _write_csv(
            temporary_paths.retrosheet_events,
            fieldnames=_RETROSHEET_EVENT_FIELDS,
            rows=event_rows,
        )
        _write_csv(
            temporary_paths.weather,
            fieldnames=_ordered_fields(_WEATHER_FIELDS, weather.fieldnames),
            rows=weather_rows,
        )
        shutil.copyfile(ballpark.path, temporary_paths.ballpark_factors)
        _write_csv(
            temporary_paths.odds_snapshot,
            fieldnames=_ordered_fields(_ODDS_FIELDS, odds.fieldnames),
            rows=odds_rows,
        )
        created_at = datetime.now(timezone.utc).isoformat()
        _write_manifest(
            temporary_dir=temporary_dir,
            created_at=created_at,
            pack_range=pack_range,
            output_rows=output_rows,
            providers=providers,
            source_tables=source_tables,
            input_tables=input_tables,
            crosswalk_validation=crosswalk_validation,
        )

        temporary_preflight = preflight_historical_input_pack(temporary_dir)
        if not temporary_preflight.is_valid:
            raise HistoricalStagingBuildError(
                "input-pack preflight failed before finalizing: "
                + "; ".join(temporary_preflight.errors)
            )

        for filename in (*PACK_SOURCE_FILES.values(), INPUT_PACK_MANIFEST_FILENAME):
            source = temporary_dir / filename
            destination = output_dir / filename
            if destination.exists():
                raise HistoricalStagingBuildError(
                    f"refusing to overwrite staging file: {destination}"
                )
            source.rename(destination)
            finalized.append(destination)

        final_preflight = preflight_historical_input_pack(output_dir)
        if not final_preflight.is_valid:
            raise HistoricalStagingBuildError(
                "finalized input-pack verification failed: "
                + "; ".join(final_preflight.errors)
            )
        succeeded = True
        return HistoricalStagingBuildResult(
            output_dir=output_dir,
            source_paths=source_paths,
            crosswalk_validation=crosswalk_validation,
            preflight=final_preflight,
        )
    finally:
        if not succeeded:
            for path in reversed(finalized):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        if temporary_dir is not None and temporary_dir.exists():
            shutil.rmtree(temporary_dir)
        if not succeeded and created_output_dir and output_dir.exists():
            try:
                output_dir.rmdir()
            except OSError:
                pass


__all__ = [
    "HISTORICAL_STAGING_VERSION",
    "REQUIRED_RETROSHEET_LABEL_COLUMNS",
    "HistoricalStagingBuildError",
    "HistoricalStagingBuildResult",
    "HistoricalStagingSourcePaths",
    "build_historical_input_pack_staging",
]
