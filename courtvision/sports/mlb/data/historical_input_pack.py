"""Read-only preflight for aligned real MLB HR historical input packs.

The pack contract deliberately sits in front of the existing local-file
builder.  It verifies immutable source bytes and cross-source identities but
does not write files, fetch data, build datasets, or change research gates.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, timedelta
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Final, Mapping, Sequence

from courtvision.sports.mlb.data.ballpark_factors import (
    REQUIRED_BALLPARK_COLUMNS,
    BallparkFactorIngestionResult,
    ingest_local_ballpark_factors_csv,
    normalize_venue_name,
)
from courtvision.sports.mlb.data.odds_snapshot_ingestion import (
    HOME_RUN_MARKET_TYPE,
    REQUIRED_ODDS_COLUMNS,
    MLBOddsSnapshotIngestionResult,
    ingest_local_odds_snapshot_csv,
    normalize_player_name,
)
from courtvision.sports.mlb.data.retrosheet_ingestion import (
    REQUIRED_RETROSHEET_EVENT_COLUMNS,
    REQUIRED_RETROSHEET_GAME_COLUMNS,
    RetrosheetIngestionResult,
    ingest_local_retrosheet_csvs,
)
from courtvision.sports.mlb.data.statcast_ingestion import (
    REQUIRED_STATCAST_COLUMNS,
    StatcastIngestionResult,
    ingest_local_statcast_csv,
)
from courtvision.sports.mlb.data.weather_ingestion import (
    REQUIRED_WEATHER_COLUMNS,
    WeatherIngestionResult,
    ingest_local_weather_csv,
)
from courtvision.sports.mlb.data_manifest import verify_source_manifest_file


HISTORICAL_INPUT_PACK_VERSION: Final = "mlb-hr-historical-input-pack-v1"
HISTORICAL_INPUT_PACK_MODE: Final = "historical_input_pack"
INPUT_PACK_MANIFEST_FILENAME: Final = "input_pack_manifest.json"

PACK_SOURCE_FILES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "statcast": "statcast.csv",
        "retrosheet_games": "retrosheet_games.csv",
        "retrosheet_events": "retrosheet_events.csv",
        "weather": "weather.csv",
        "ballpark_factors": "ballpark_factors.csv",
        "odds_snapshot": "hr_odds_snapshot.csv",
    }
)

# These are intentionally small enough for a narrow first real pack and for
# realistic unit fixtures, while still rejecting empty or one-row placeholders.
MINIMUM_REAL_ROW_THRESHOLDS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "statcast": 2,
        "retrosheet_games": 1,
        "retrosheet_events": 2,
        "weather": 1,
        "ballpark_factors": 1,
        "odds_snapshot": 1,
        "labeled_batter_games": 2,
    }
)

REQUIRED_PACK_COLUMNS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "statcast": REQUIRED_STATCAST_COLUMNS | {"game_pk"},
        "retrosheet_games": REQUIRED_RETROSHEET_GAME_COLUMNS
        | {"game_number", "venue_name", "home_score", "away_score"},
        "retrosheet_events": REQUIRED_RETROSHEET_EVENT_COLUMNS
        | {"event_text", "rbi"},
        "weather": REQUIRED_WEATHER_COLUMNS
        | {"game_id", "event_start_time", "as_of_date"},
        "ballpark_factors": REQUIRED_BALLPARK_COLUMNS
        | {"team", "as_of_date"},
        "odds_snapshot": REQUIRED_ODDS_COLUMNS
        | {
            "game_id",
            "player_id",
            "player_name",
            "team",
            "opponent",
            "sportsbook",
            "odds_collected_at",
            "event_start_time",
            "home_team",
            "away_team",
            "provider",
            "source_type",
        },
    }
)

_PROHIBITED_REAL_TOKEN = re.compile(
    r"(?:^|\s)(sample|fixture|mock|test|synthetic|dummy|fake|example|placeholder)(?:$|\s)",
    re.IGNORECASE,
)
_SYNTHETIC_ID = re.compile(
    r"^(?:[bp]\d+|player[-_]?\d+|pitcher[-_]?\d+|batter[-_]?\d+)$",
    re.IGNORECASE,
)
_TEAM_CODE = re.compile(r"^[A-Z]{2,3}$")
_SYNTHETIC_TEAM_CODE = re.compile(r"^(?:EX[A-Z]|TST|AAA|BBB|XXX)$")


class HistoricalInputPackError(ValueError):
    """Raised when a proposed real input pack fails preflight."""


@dataclass(frozen=True, slots=True)
class HistoricalInputPackPaths:
    """Resolved paths for one contract-shaped pack."""

    root: Path
    manifest: Path
    statcast: Path
    retrosheet_games: Path
    retrosheet_events: Path
    weather: Path
    ballpark_factors: Path
    odds_snapshot: Path

    def source_map(self) -> dict[str, Path]:
        """Return builder-facing source paths keyed by contract source name."""

        return {
            source_name: getattr(self, source_name)
            for source_name in PACK_SOURCE_FILES
        }


@dataclass(frozen=True, slots=True)
class HistoricalInputPackValidationResult:
    """Read-only preflight result with stable diagnostics."""

    is_valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    paths: HistoricalInputPackPaths
    row_counts: Mapping[str, int] = field(default_factory=dict)
    date_range_start: date | None = None
    date_range_end: date | None = None

    def raise_for_errors(self) -> None:
        if self.errors:
            raise HistoricalInputPackError("; ".join(self.errors))


def historical_input_pack_paths(
    pack_dir: str | Path,
) -> HistoricalInputPackPaths:
    """Resolve the fixed contract paths without creating any directories."""

    root = Path(pack_dir).expanduser().resolve()
    return HistoricalInputPackPaths(
        root=root,
        manifest=root / INPUT_PACK_MANIFEST_FILENAME,
        **{
            source_name: root / filename
            for source_name, filename in PACK_SOURCE_FILES.items()
        },
    )


def _read_manifest(path: Path, errors: list[str]) -> Mapping[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"could not read input pack manifest {path}: {exc}")
        return None
    if not isinstance(payload, Mapping):
        errors.append("input pack manifest JSON must be an object")
        return None
    return payload


def _validate_headers(
    source_name: str,
    path: Path,
    errors: list[str],
) -> None:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = set(reader.fieldnames or ())
    except (OSError, UnicodeError, csv.Error) as exc:
        errors.append(f"could not read {source_name} CSV header: {exc}")
        return
    missing = sorted(REQUIRED_PACK_COLUMNS[source_name] - headers)
    if missing:
        errors.append(
            f"{source_name} CSV is missing pack-required columns: "
            + ", ".join(missing)
        )


def _manifest_source_entries(
    payload: Mapping[str, object] | None,
    paths: HistoricalInputPackPaths,
    errors: list[str],
) -> dict[str, Mapping[str, object]]:
    if payload is None:
        return {}
    if payload.get("manifest_version") != HISTORICAL_INPUT_PACK_VERSION:
        errors.append(
            f"manifest_version must be {HISTORICAL_INPUT_PACK_VERSION!r}"
        )
    if payload.get("mode") != HISTORICAL_INPUT_PACK_MODE:
        errors.append(f"manifest mode must be {HISTORICAL_INPUT_PACK_MODE!r}")
    if payload.get("source_classification") != "real":
        errors.append("input pack source_classification must be 'real'")

    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list):
        errors.append("input pack manifest sources must be a list")
        return {}

    entries: dict[str, Mapping[str, object]] = {}
    for index, raw_entry in enumerate(raw_sources):
        if not isinstance(raw_entry, Mapping):
            errors.append(f"manifest sources[{index}] must be an object")
            continue
        source_name = str(raw_entry.get("source_name") or "").strip()
        if source_name in entries:
            errors.append(f"duplicate manifest source entry: {source_name}")
            continue
        entries[source_name] = raw_entry

    expected_names = set(PACK_SOURCE_FILES)
    missing = sorted(expected_names - set(entries))
    unexpected = sorted(set(entries) - expected_names)
    if missing:
        errors.append("manifest is missing required sources: " + ", ".join(missing))
    if unexpected:
        errors.append("manifest has unexpected sources: " + ", ".join(unexpected))

    for source_name in sorted(expected_names & set(entries)):
        entry = entries[source_name]
        expected_path = getattr(paths, source_name).resolve()
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        declared = Path(raw_path)
        if declared.is_absolute():
            errors.append(
                f"manifest source {source_name} path must be relative for a portable pack"
            )
            continue
        resolved = (paths.root / declared).resolve()
        if not resolved.is_relative_to(paths.root):
            errors.append(f"manifest source {source_name} path escapes the pack root")
        elif resolved != expected_path:
            errors.append(
                f"manifest source {source_name} must point to "
                f"{PACK_SOURCE_FILES[source_name]!r}"
            )
        if entry.get("source_classification") != "real":
            errors.append(
                f"manifest source {source_name} source_classification must be 'real'"
            )
        if entry.get("source_type") != "local_file":
            errors.append(f"manifest source {source_name} source_type must be 'local_file'")
        if _prohibited_token(entry.get("provider_label")):
            errors.append(
                f"manifest source {source_name} uses sample/fixture provider provenance"
            )
        if entry.get("required_or_optional") != "required":
            errors.append(f"manifest source {source_name} must be marked required")
        if entry.get("loaded_successfully") is not True:
            errors.append(
                f"manifest source {source_name} loaded_successfully must be true"
            )
    return entries


def _prohibited_token(value: object) -> bool:
    text = "" if value is None else str(value).strip()
    normalized = re.sub(r"[^\w]+", " ", text).strip()
    return bool(normalized and _PROHIBITED_REAL_TOKEN.search(normalized))


def _check_real_identity(
    *,
    label: str,
    identity_id: object,
    identity_name: object,
    errors: list[str],
) -> None:
    id_text = str(identity_id or "").strip()
    name_text = str(identity_name or "").strip()
    if _SYNTHETIC_ID.fullmatch(id_text) or _prohibited_token(name_text):
        errors.append(
            f"{label} uses a sample/fixture/synthetic identity: "
            f"id={id_text or '<missing>'} name={name_text or '<missing>'}"
        )
    if not id_text.isdigit() or int(id_text) <= 0:
        errors.append(f"{label} id must be a positive canonical MLBAM id: {id_text!r}")
    if normalize_player_name(name_text) is None:
        errors.append(f"{label} name must not be empty")


def _check_team(value: object, label: str, errors: list[str]) -> None:
    text = str(value or "").strip()
    if not _TEAM_CODE.fullmatch(text):
        errors.append(f"{label} must be an uppercase 2-3 letter team code: {text!r}")
    elif _SYNTHETIC_TEAM_CODE.fullmatch(text):
        errors.append(f"{label} uses a sample/fixture/synthetic team code: {text!r}")


def _date_range(rows: Sequence[object]) -> tuple[date, date] | None:
    dates = [getattr(row, "game_date", None) for row in rows]
    valid_dates = [value for value in dates if isinstance(value, date)]
    if not valid_dates:
        return None
    return min(valid_dates), max(valid_dates)


def _parse_manifest_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _validate_manifest_observations(
    *,
    payload: Mapping[str, object] | None,
    entries: Mapping[str, Mapping[str, object]],
    row_counts: Mapping[str, int],
    ranges: Mapping[str, tuple[date, date]],
    pack_range: tuple[date, date] | None,
    errors: list[str],
) -> None:
    if payload is None or pack_range is None:
        return
    declared_pack_range = (
        _parse_manifest_date(payload.get("dataset_date_range_start")),
        _parse_manifest_date(payload.get("dataset_date_range_end")),
    )
    if declared_pack_range != pack_range:
        errors.append(
            "manifest date range does not match Retrosheet game coverage: "
            f"declared={declared_pack_range}, actual={pack_range}"
        )

    for source_name, actual_count in row_counts.items():
        entry = entries.get(source_name)
        if entry is None:
            continue
        if entry.get("parsed_row_count") != actual_count:
            errors.append(
                f"manifest source {source_name} parsed_row_count does not match "
                f"the CSV: declared={entry.get('parsed_row_count')!r}, actual={actual_count}"
            )
        actual_range = ranges.get(source_name, pack_range)
        declared_range = (
            _parse_manifest_date(entry.get("date_range_start")),
            _parse_manifest_date(entry.get("date_range_end")),
        )
        if declared_range != actual_range:
            errors.append(
                f"manifest source {source_name} date range does not match the CSV/pack: "
                f"declared={declared_range}, actual={actual_range}"
            )


def _validate_alignment(
    *,
    statcast: StatcastIngestionResult,
    retrosheet: RetrosheetIngestionResult,
    weather: WeatherIngestionResult,
    ballpark: BallparkFactorIngestionResult,
    odds: MLBOddsSnapshotIngestionResult,
    errors: list[str],
) -> None:
    games: dict[tuple[str, date], object] = {}
    for row in retrosheet.games:
        key = (row.game_id, row.game_date)
        if key in games:
            errors.append(f"duplicate Retrosheet game identity: {key}")
        games[key] = row
        if not row.game_id.isdigit():
            errors.append(
                f"Retrosheet game_id must be the canonical numeric Statcast game_pk: {row.game_id!r}"
            )
        _check_team(row.home_team, f"game {row.game_id} home_team", errors)
        _check_team(row.away_team, f"game {row.game_id} away_team", errors)
        if row.home_team == row.away_team:
            errors.append(f"game {row.game_id} home_team and away_team must differ")
        if row.game_status != "completed":
            errors.append(f"game {row.game_id} must be completed for historical labels")
        if not row.venue_name:
            errors.append(f"game {row.game_id} is missing venue_name")
        elif _prohibited_token(row.venue_name):
            errors.append(f"game {row.game_id} uses a sample/fixture venue")

    game_keys = set(games)
    statcast_games: set[tuple[str, date]] = set()
    statcast_players: dict[tuple[str, date, str], str] = {}
    for row in statcast.rows:
        game_key = (row.game_id, row.game_date)
        statcast_games.add(game_key)
        _check_real_identity(
            label="Statcast batter",
            identity_id=row.player_id,
            identity_name=row.player_name,
            errors=errors,
        )
        _check_real_identity(
            label="Statcast pitcher",
            identity_id=row.pitcher_id,
            identity_name="known pitcher",
            errors=errors,
        )
        game = games.get(game_key)
        if game is None:
            errors.append(f"Statcast row does not match a Retrosheet game: {game_key}")
        else:
            if (row.home_team, row.away_team) != (
                getattr(game, "home_team"),
                getattr(game, "away_team"),
            ):
                errors.append(
                    f"Statcast teams do not match Retrosheet game {row.game_id}"
                )
        player_key = (*game_key, str(row.player_id))
        name_key = normalize_player_name(row.player_name) or ""
        previous = statcast_players.setdefault(player_key, name_key)
        if previous != name_key:
            errors.append(f"conflicting Statcast names for player identity {player_key}")

    label_games: set[tuple[str, date]] = set()
    label_players: dict[tuple[str, date, str], str] = {}
    label_teams: dict[tuple[str, date, str], tuple[str, str]] = {}
    for row in retrosheet.events:
        game_key = (row.game_id, row.game_date)
        label_games.add(game_key)
        _check_real_identity(
            label="Retrosheet batter",
            identity_id=row.batter_id,
            identity_name=row.batter_name,
            errors=errors,
        )
        _check_real_identity(
            label="Retrosheet pitcher",
            identity_id=row.pitcher_id,
            identity_name=row.pitcher_name,
            errors=errors,
        )
        game = games.get(game_key)
        if game is None:
            errors.append(f"Retrosheet event does not match a game row: {game_key}")
        else:
            teams = {getattr(game, "home_team"), getattr(game, "away_team")}
            if {row.batting_team, row.fielding_team} != teams:
                errors.append(
                    f"Retrosheet event teams do not match game {row.game_id}"
                )
            if row.batting_team == row.fielding_team:
                errors.append(
                    f"Retrosheet event batting_team and fielding_team must differ: {row.game_id}"
                )
        player_key = (*game_key, row.batter_id)
        name_key = normalize_player_name(row.batter_name) or ""
        previous = label_players.setdefault(player_key, name_key)
        if previous != name_key:
            errors.append(f"conflicting Retrosheet names for player identity {player_key}")
        team_context = (row.batting_team, row.fielding_team)
        previous_teams = label_teams.setdefault(player_key, team_context)
        if previous_teams != team_context:
            errors.append(f"conflicting team context for player identity {player_key}")

    if statcast_games != game_keys:
        errors.append(
            "Statcast game coverage must exactly match Retrosheet games: "
            f"missing={sorted(game_keys - statcast_games)}, "
            f"unexpected={sorted(statcast_games - game_keys)}"
        )
    if label_games != game_keys:
        errors.append(
            "Retrosheet label game coverage must exactly match game rows: "
            f"missing={sorted(game_keys - label_games)}, "
            f"unexpected={sorted(label_games - game_keys)}"
        )
    if set(statcast_players) != set(label_players):
        errors.append(
            "Statcast batter-game identities must exactly match Retrosheet labels: "
            f"missing_labels={sorted(set(statcast_players) - set(label_players))}, "
            f"labels_without_statcast={sorted(set(label_players) - set(statcast_players))}"
        )
    for player_key in set(statcast_players) & set(label_players):
        if statcast_players[player_key] != label_players[player_key]:
            errors.append(
                f"player name mismatch for canonical batter identity {player_key}: "
                f"Statcast={statcast_players[player_key]!r}, "
                f"Retrosheet={label_players[player_key]!r}"
            )

    weather_by_game: dict[tuple[str, date], list[object]] = {}
    for row in weather.rows:
        if not row.game_id:
            errors.append("weather row is missing game_id")
            continue
        key = (row.game_id, row.game_date)
        weather_by_game.setdefault(key, []).append(row)
        if _prohibited_token(row.source) or row.source_type == "sample":
            errors.append(f"weather row for {key} uses sample/fixture provenance")
    if set(weather_by_game) != game_keys:
        errors.append(
            "weather game coverage must exactly match Retrosheet games: "
            f"missing={sorted(game_keys - set(weather_by_game))}, "
            f"unexpected={sorted(set(weather_by_game) - game_keys)}"
        )
    event_starts: dict[tuple[str, date], object] = {}
    for game_key, game in games.items():
        matches = weather_by_game.get(game_key, [])
        if len(matches) != 1:
            errors.append(
                f"game {game_key} requires exactly one weather row; found {len(matches)}"
            )
            continue
        row = matches[0]
        venue = getattr(game, "venue_name", None)
        if not venue or normalize_venue_name(row.venue_name) != normalize_venue_name(venue):
            errors.append(f"weather venue does not match Retrosheet game {game_key}")
        if row.event_start_time is None:
            errors.append(f"weather row for game {game_key} is missing event_start_time")
        else:
            event_starts[game_key] = row.event_start_time
        if row.temperature is None or row.wind_speed is None or not row.wind_direction:
            errors.append(f"weather row for game {game_key} is incomplete")

    used_venues = {
        normalize_venue_name(row.venue_name)
        for row in retrosheet.games
        if row.venue_name
    }
    ballparks = {normalize_venue_name(row.venue_name): row for row in ballpark.rows}
    if set(ballparks) != used_venues:
        errors.append(
            "ballpark venue coverage must exactly match Retrosheet venues: "
            f"missing={sorted(used_venues - set(ballparks))}, "
            f"unexpected={sorted(set(ballparks) - used_venues)}"
        )
    for venue_key, row in ballparks.items():
        if (
            _prohibited_token(row.source)
            or _prohibited_token(row.data_version)
            or row.source_type == "sample"
        ):
            errors.append(f"ballpark row {venue_key!r} uses sample/fixture provenance")
        if row.park_factor_hr is None:
            errors.append(f"ballpark row {venue_key!r} is missing park_factor_hr")

    seen_odds: set[tuple[str, date, str, str]] = set()
    for row in odds.rows:
        if not row.game_id:
            errors.append("odds row is missing game_id")
            continue
        game_key = (row.game_id, row.game_date)
        game = games.get(game_key)
        if game is None:
            errors.append(f"odds row does not match a Retrosheet game: {game_key}")
            continue
        _check_real_identity(
            label="odds player",
            identity_id=row.player_id,
            identity_name=row.player_name,
            errors=errors,
        )
        player_id = str(row.player_id or "").strip()
        player_key = (*game_key, player_id)
        if player_key not in label_players:
            errors.append(f"odds player does not match a labeled batter-game: {player_key}")
        elif normalize_player_name(row.player_name) != label_players[player_key]:
            errors.append(f"odds player name does not match canonical identity {player_key}")
        expected_teams = label_teams.get(player_key)
        if expected_teams and (row.team, row.opponent) != expected_teams:
            errors.append(f"odds team/opponent do not match labeled player {player_key}")
        if (row.home_team, row.away_team) != (
            getattr(game, "home_team"),
            getattr(game, "away_team"),
        ):
            errors.append(f"odds home/away teams do not match game {game_key}")
        if row.market_type != HOME_RUN_MARKET_TYPE:
            errors.append(f"odds row is not an MLB HR market: {player_key}")
        if (
            _prohibited_token(row.sportsbook)
            or _prohibited_token(row.provider)
            or _prohibited_token(row.source_type)
        ):
            errors.append(f"odds row for {player_key} uses sample/fixture provenance")
        event_start = event_starts.get(game_key)
        if row.event_start_time is None:
            errors.append(f"odds row for {player_key} is missing event_start_time")
        elif event_start is not None and row.event_start_time != event_start:
            errors.append(f"odds event_start_time does not match weather for {game_key}")
        if row.event_start_time is not None:
            age = row.event_start_time - row.odds_collected_at
            if not timedelta(0) < age <= timedelta(hours=24):
                errors.append(f"odds row is not a verified pregame snapshot: {player_key}")
        duplicate_key = (
            row.game_id,
            row.game_date,
            player_id,
            row.sportsbook.casefold(),
        )
        if duplicate_key in seen_odds:
            errors.append(f"duplicate odds snapshot identity: {duplicate_key}")
        seen_odds.add(duplicate_key)


def preflight_historical_input_pack(
    pack_dir: str | Path,
    *,
    minimum_row_thresholds: Mapping[str, int] | None = None,
) -> HistoricalInputPackValidationResult:
    """Validate one proposed real pack without writing or repairing anything."""

    paths = historical_input_pack_paths(pack_dir)
    errors: list[str] = []
    warnings: list[str] = []
    row_counts: dict[str, int] = {}
    ranges: dict[str, tuple[date, date]] = {}

    if not paths.root.is_dir():
        errors.append(f"historical input pack directory does not exist: {paths.root}")
    required_paths = {INPUT_PACK_MANIFEST_FILENAME: paths.manifest}
    required_paths.update(
        {filename: getattr(paths, source) for source, filename in PACK_SOURCE_FILES.items()}
    )
    for filename, path in required_paths.items():
        if not path.is_file():
            errors.append(f"required input pack file is missing: {filename}")

    payload: Mapping[str, object] | None = None
    entries: dict[str, Mapping[str, object]] = {}
    if paths.manifest.is_file():
        verification = verify_source_manifest_file(paths.manifest)
        errors.extend(f"source manifest: {error}" for error in verification.errors)
        payload = _read_manifest(paths.manifest, errors)
        entries = _manifest_source_entries(payload, paths, errors)

    for source_name, path in paths.source_map().items():
        if path.is_file():
            _validate_headers(source_name, path, errors)

    statcast: StatcastIngestionResult | None = None
    retrosheet: RetrosheetIngestionResult | None = None
    weather: WeatherIngestionResult | None = None
    ballpark: BallparkFactorIngestionResult | None = None
    odds: MLBOddsSnapshotIngestionResult | None = None

    if paths.statcast.is_file():
        try:
            statcast = ingest_local_statcast_csv(paths.statcast)
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"statcast parse failed: {exc}")
    if paths.retrosheet_games.is_file() and paths.retrosheet_events.is_file():
        try:
            retrosheet = ingest_local_retrosheet_csvs(
                games_csv=paths.retrosheet_games,
                events_csv=paths.retrosheet_events,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"Retrosheet parse failed: {exc}")
    if paths.weather.is_file():
        try:
            weather = ingest_local_weather_csv(paths.weather)
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"weather parse failed: {exc}")
    if paths.ballpark_factors.is_file():
        try:
            ballpark = ingest_local_ballpark_factors_csv(paths.ballpark_factors)
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"ballpark parse failed: {exc}")
    if paths.odds_snapshot.is_file():
        try:
            odds = ingest_local_odds_snapshot_csv(paths.odds_snapshot)
            if odds.rejected_row_count:
                errors.append(
                    "odds CSV contains rejected rows; every real-pack row must parse: "
                    f"{odds.rejected_row_count}"
                )
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"odds parse failed: {exc}")

    if statcast is not None:
        row_counts["statcast"] = len(statcast.rows)
        if value := _date_range(statcast.rows):
            ranges["statcast"] = value
    if retrosheet is not None:
        row_counts["retrosheet_games"] = len(retrosheet.games)
        row_counts["retrosheet_events"] = len(retrosheet.events)
        if value := _date_range(retrosheet.games):
            ranges["retrosheet_games"] = value
        if value := _date_range(retrosheet.events):
            ranges["retrosheet_events"] = value
    if weather is not None:
        row_counts["weather"] = len(weather.rows)
        if value := _date_range(weather.rows):
            ranges["weather"] = value
    if ballpark is not None:
        row_counts["ballpark_factors"] = len(ballpark.rows)
    if odds is not None:
        row_counts["odds_snapshot"] = len(odds.rows)
        if value := _date_range(odds.rows):
            ranges["odds_snapshot"] = value

    pack_range = ranges.get("retrosheet_games")
    if pack_range is not None:
        ranges["ballpark_factors"] = pack_range
    _validate_manifest_observations(
        payload=payload,
        entries=entries,
        row_counts=row_counts,
        ranges=ranges,
        pack_range=pack_range,
        errors=errors,
    )

    thresholds = dict(MINIMUM_REAL_ROW_THRESHOLDS)
    if minimum_row_thresholds is not None:
        unknown = set(minimum_row_thresholds) - set(thresholds)
        if unknown:
            errors.append("unknown row thresholds: " + ", ".join(sorted(unknown)))
        for source_name, value in minimum_row_thresholds.items():
            if source_name in thresholds:
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    errors.append(f"minimum threshold for {source_name} must be positive")
                else:
                    thresholds[source_name] = value
    for source_name, actual in row_counts.items():
        minimum = thresholds[source_name]
        if actual < minimum:
            errors.append(
                f"{source_name} minimum real row threshold not met: "
                f"required={minimum}, actual={actual}"
            )

    if all(value is not None for value in (statcast, retrosheet, weather, ballpark, odds)):
        assert statcast is not None
        assert retrosheet is not None
        assert weather is not None
        assert ballpark is not None
        assert odds is not None
        labeled_count = len(
            {
                (row.game_id, row.game_date, row.batter_id)
                for row in retrosheet.events
            }
        )
        row_counts["labeled_batter_games"] = labeled_count
        if labeled_count < thresholds["labeled_batter_games"]:
            errors.append(
                "labeled_batter_games minimum real row threshold not met: "
                f"required={thresholds['labeled_batter_games']}, actual={labeled_count}"
            )
        _validate_alignment(
            statcast=statcast,
            retrosheet=retrosheet,
            weather=weather,
            ballpark=ballpark,
            odds=odds,
            errors=errors,
        )

    unique_errors = tuple(dict.fromkeys(errors))
    return HistoricalInputPackValidationResult(
        is_valid=not unique_errors,
        errors=unique_errors,
        warnings=tuple(dict.fromkeys(warnings)),
        paths=paths,
        row_counts=MappingProxyType(dict(row_counts)),
        date_range_start=pack_range[0] if pack_range else None,
        date_range_end=pack_range[1] if pack_range else None,
    )


__all__ = [
    "HISTORICAL_INPUT_PACK_MODE",
    "HISTORICAL_INPUT_PACK_VERSION",
    "INPUT_PACK_MANIFEST_FILENAME",
    "MINIMUM_REAL_ROW_THRESHOLDS",
    "PACK_SOURCE_FILES",
    "REQUIRED_PACK_COLUMNS",
    "HistoricalInputPackError",
    "HistoricalInputPackPaths",
    "HistoricalInputPackValidationResult",
    "historical_input_pack_paths",
    "preflight_historical_input_pack",
]
