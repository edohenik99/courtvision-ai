"""Fixture-backed MLB home-run batter-game dataset construction.

This module joins already-normalized Phase 3C--3F rows into the Phase 4A
schema.  It is intentionally historical/research-only: it performs no network
access, rolling-feature computation, model training, scoring, or wagering work.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Final, Mapping, Sequence

from courtvision.sports.mlb.data.ballpark_factors import (
    MLBBallparkFactorRow,
    ingest_local_ballpark_factors_csv,
    normalize_venue_name,
)
from courtvision.sports.mlb.data.retrosheet_ingestion import (
    RetrosheetEventRow,
    RetrosheetGameRow,
    ingest_local_retrosheet_csvs,
)
from courtvision.sports.mlb.data.odds_snapshot_ingestion import (
    HOME_RUN_MARKET_TYPE,
    MLBOddsSnapshotRow,
    normalize_player_name,
)
from courtvision.sports.mlb.data.statcast_ingestion import (
    MLBStatcastEventRow,
    ingest_local_statcast_csv,
)
from courtvision.sports.mlb.data.weather_ingestion import (
    MLBWeatherObservationRow,
    ingest_local_weather_csv,
)
from courtvision.sports.mlb.data_manifest import MLBSourceManifest
from courtvision.sports.mlb.training.hr_dataset_schema import (
    MLBHRBatterGameRow,
    MLBHRDatasetMetadata,
    MLBHRDatasetSchemaError,
    MLBHRDatasetValidationResult,
    ROW_FIELD_NAMES,
    assert_feature_as_of_before_game,
    dataset_row_id,
    metadata_to_json,
    rows_to_csv_dicts,
    validate_batter_game_row,
    validate_dataset_metadata,
)


FIXTURE_DATASET_VERSION: Final = "phase4b-fixture-v1"
FIXTURE_GENERATED_BY: Final = "courtvision.phase4b.fixture_hr_dataset_builder"
_MANIFEST_SOURCE_KEYS: Final = (
    "statcast",
    "retrosheet",
    "weather",
    "ballpark",
    "odds",
)
ODDS_FRESHNESS_WINDOW: Final = timedelta(hours=24)


class MLBHRDatasetBuildError(ValueError):
    """Raised when deterministic fixture construction cannot proceed safely."""


@dataclass(frozen=True, slots=True)
class MLBHRDatasetBuildResult:
    """Rows, metadata, and fail-closed Phase 4B build diagnostics."""

    rows: tuple[MLBHRBatterGameRow, ...]
    metadata: MLBHRDatasetMetadata
    row_count: int
    date_range_start: date
    date_range_end: date
    source_manifest_ids: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    skipped_rows: tuple[str, ...] = field(default_factory=tuple)
    missing_context_summary: Mapping[str, int] = field(default_factory=dict)
    odds_pairing_summary: Mapping[str, int] = field(default_factory=dict)
    leakage_check_status: str = "not_checked"
    eligible_for_training_count: int = 0
    eligible_for_backtest_count: int = 0
    eligible_for_betting: bool = False
    kelly_eligible: bool = False
    approval_status: str = "not_approved"

    def __post_init__(self) -> None:
        rows = tuple(self.rows)
        manifest_ids = tuple(self.source_manifest_ids)
        warnings = tuple(self.warnings)
        skipped_rows = tuple(self.skipped_rows)
        summary = dict(self.missing_context_summary)
        odds_summary = dict(self.odds_pairing_summary)
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "source_manifest_ids", manifest_ids)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "skipped_rows", skipped_rows)
        object.__setattr__(
            self, "missing_context_summary", MappingProxyType(summary)
        )
        object.__setattr__(
            self, "odds_pairing_summary", MappingProxyType(odds_summary)
        )
        if self.row_count != len(rows):
            raise MLBHRDatasetBuildError("row_count must match rows")
        if self.metadata.row_count != self.row_count:
            raise MLBHRDatasetBuildError("metadata row_count must match rows")
        if self.date_range_start > self.date_range_end:
            raise MLBHRDatasetBuildError(
                "date_range_start must not be after date_range_end"
            )
        if self.eligible_for_betting is not False:
            raise MLBHRDatasetBuildError("eligible_for_betting must be false")
        if self.kelly_eligible is not False:
            raise MLBHRDatasetBuildError("kelly_eligible must be false")
        if self.approval_status != "not_approved":
            raise MLBHRDatasetBuildError("approval_status must be 'not_approved'")


@dataclass(slots=True)
class _Opportunity:
    statcast_rows: list[MLBStatcastEventRow] = field(default_factory=list)
    retrosheet_rows: list[RetrosheetEventRow] = field(default_factory=list)


def _clean_text(value: object) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _stable_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _manifest_identifier(manifest: MLBSourceManifest) -> str | None:
    """Return the stable identifier available from a Phase 3B manifest."""

    source_name = _clean_text(manifest.source_name)
    checksum = _clean_text(manifest.checksum)
    if source_name and checksum:
        return f"{source_name}:{checksum}"
    return source_name


def _source_manifest_map(
    source_manifest_ids: Mapping[str, str] | None,
) -> dict[str, str]:
    if source_manifest_ids is None:
        return {}
    normalized: dict[str, str] = {}
    for source_key, manifest_id in source_manifest_ids.items():
        key = str(source_key).strip().lower()
        value = _clean_text(manifest_id)
        if key not in _MANIFEST_SOURCE_KEYS:
            raise MLBHRDatasetBuildError(
                f"unsupported source manifest key: {source_key!r}"
            )
        if value is None:
            raise MLBHRDatasetBuildError(
                f"source manifest ID for {source_key!r} must not be empty"
            )
        normalized[key] = value
    return normalized


def _opportunity_key(
    row: object,
    *,
    source_name: str,
    row_number: int,
    skipped_rows: list[str],
) -> tuple[str, date, str] | None:
    game_id = _clean_text(getattr(row, "game_id", None))
    game_date = _as_date(getattr(row, "game_date", None))
    player_value = getattr(
        row,
        "batter_id" if source_name == "retrosheet" else "player_id",
        None,
    )
    player_id = _clean_text(player_value)
    missing = tuple(
        field_name
        for field_name, value in (
            ("game_id", game_id),
            ("game_date", game_date),
            ("player_id", player_id),
        )
        if value is None
    )
    if missing:
        skipped_rows.append(
            f"{source_name} row {row_number} skipped: missing " + ", ".join(missing)
        )
        return None
    return game_id, game_date, player_id  # type: ignore[return-value]


def _game_index(
    rows: Sequence[RetrosheetGameRow], warnings: list[str]
) -> dict[tuple[str, date], RetrosheetGameRow]:
    index: dict[tuple[str, date], RetrosheetGameRow] = {}
    for row_number, row in enumerate(rows, start=1):
        game_id = _clean_text(getattr(row, "game_id", None))
        game_date = _as_date(getattr(row, "game_date", None))
        if game_id is None or game_date is None:
            warnings.append(
                f"retrosheet game row {row_number} ignored: missing game_id or game_date"
            )
            continue
        key = (game_id, game_date)
        if key in index:
            raise MLBHRDatasetBuildError(
                f"duplicate Retrosheet game identity: {game_id} on {game_date}"
            )
        index[key] = row
    return index


def _weather_indexes(
    rows: Sequence[MLBWeatherObservationRow],
) -> tuple[
    dict[tuple[str, date], list[MLBWeatherObservationRow]],
    dict[tuple[str, date], list[MLBWeatherObservationRow]],
]:
    by_game: dict[tuple[str, date], list[MLBWeatherObservationRow]] = {}
    by_venue: dict[tuple[str, date], list[MLBWeatherObservationRow]] = {}
    for row in rows:
        row_date = _as_date(getattr(row, "game_date", None))
        if row_date is None:
            continue
        game_id = _clean_text(getattr(row, "game_id", None))
        if game_id:
            by_game.setdefault((game_id, row_date), []).append(row)
        venue = _clean_text(getattr(row, "venue_name", None))
        if venue:
            by_venue.setdefault((normalize_venue_name(venue), row_date), []).append(row)
    return by_game, by_venue


def _select_weather(
    *,
    game_id: str,
    game_date: date,
    venue_name: str | None,
    by_game: Mapping[tuple[str, date], Sequence[MLBWeatherObservationRow]],
    by_venue: Mapping[tuple[str, date], Sequence[MLBWeatherObservationRow]],
    warnings: list[str],
) -> MLBWeatherObservationRow | None:
    exact = tuple(by_game.get((game_id, game_date), ()))
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        warnings.append("weather context is ambiguous for game_id and date")
        return None
    if venue_name:
        fallback = tuple(
            by_venue.get((normalize_venue_name(venue_name), game_date), ())
        )
        if len(fallback) == 1:
            warnings.append("weather matched by normalized venue_name and game_date")
            return fallback[0]
        if len(fallback) > 1:
            warnings.append("weather context is ambiguous for venue_name and date")
            return None
    warnings.append("weather context missing")
    return None


def _ballpark_index(
    rows: Sequence[MLBBallparkFactorRow],
) -> dict[str, MLBBallparkFactorRow]:
    index: dict[str, MLBBallparkFactorRow] = {}
    for row in rows:
        venue = _clean_text(getattr(row, "venue_name", None))
        if venue is None:
            continue
        key = normalize_venue_name(venue)
        if key in index:
            raise MLBHRDatasetBuildError(
                f"duplicate normalized ballpark venue: {venue!r}"
            )
        index[key] = row
    return index


def _team_key(value: object) -> str | None:
    text = _clean_text(value)
    return text.casefold() if text is not None else None


def _odds_team_context_is_safe(
    odds: MLBOddsSnapshotRow,
    *,
    team: str | None,
    opponent: str | None,
) -> bool:
    row_team = _team_key(team)
    row_opponent = _team_key(opponent)
    odds_team = _team_key(odds.team)
    odds_opponent = _team_key(odds.opponent)
    return not (
        (odds_team is not None and row_team is not None and odds_team != row_team)
        or (
            odds_opponent is not None
            and row_opponent is not None
            and odds_opponent != row_opponent
        )
    )


def _odds_match_priority(
    odds: MLBOddsSnapshotRow,
    *,
    game_id: str,
    game_date: date,
    player_id: str,
    player_name: str,
    team: str | None,
    opponent: str | None,
) -> int | None:
    if odds.market_type != HOME_RUN_MARKET_TYPE or odds.game_date != game_date:
        return None
    odds_player_id = _clean_text(odds.player_id)
    if odds.game_id == game_id and odds_player_id == player_id:
        return 1
    team_matches = (
        _team_key(odds.team) == _team_key(team)
        and _team_key(odds.opponent) == _team_key(opponent)
    )
    if odds_player_id == player_id and team_matches:
        return 2
    if (
        odds_player_id is None
        and normalize_player_name(odds.player_name) == normalize_player_name(player_name)
        and team_matches
    ):
        return 3
    return None


def _odds_freshness(
    odds: MLBOddsSnapshotRow, event_start: datetime | None
) -> bool | None:
    if event_start is None:
        return None
    try:
        age = event_start - odds.odds_collected_at
    except TypeError:
        return None
    return timedelta(0) < age <= ODDS_FRESHNESS_WINDOW


def _statcast_team_context(
    rows: Sequence[MLBStatcastEventRow],
) -> tuple[str | None, str | None, str | None, bool | None]:
    if not rows:
        return None, None, None, None
    row = rows[0]
    home_team = _clean_text(row.home_team)
    away_team = _clean_text(row.away_team)
    inning_half = (_clean_text(row.inning_half) or "").lower()
    if inning_half in {"top", "t"}:
        return away_team, home_team, home_team, False
    if inning_half in {"bottom", "bot", "b"}:
        return home_team, away_team, away_team, True
    return None, None, None, None


def _feature_cutoff(
    game_date: date, event_start_time: datetime | None
) -> datetime | None:
    if event_start_time is None:
        return None
    cutoff = datetime.combine(
        game_date,
        time.min,
        tzinfo=event_start_time.tzinfo,
    )
    if cutoff >= event_start_time:
        return event_start_time - timedelta(microseconds=1)
    return cutoff


def _latest_datetime(values: Sequence[datetime]) -> datetime | None:
    if not values:
        return None
    aware = [value for value in values if value.tzinfo is not None]
    naive = [value for value in values if value.tzinfo is None]
    candidates = aware or naive
    return max(candidates)


def _dataset_id(
    rows: Sequence[MLBHRBatterGameRow], source_manifest_ids: Sequence[str]
) -> str:
    canonical = "|".join(
        (*sorted(row.row_id for row in rows), *sorted(source_manifest_ids))
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"mlb-hr-phase4b-fixture-{digest}"


def _row_manifest_fields(
    *,
    has_statcast: bool,
    has_retrosheet: bool,
    has_weather: bool,
    has_ballpark: bool,
    has_odds: bool,
    manifest_ids: Mapping[str, str],
) -> tuple[tuple[str, ...], dict[str, str | None]]:
    used = {
        "statcast": has_statcast,
        "retrosheet": has_retrosheet,
        "weather": has_weather,
        "ballpark": has_ballpark,
        "odds": has_odds,
    }
    source_ids = tuple(
        manifest_ids[key]
        for key in _MANIFEST_SOURCE_KEYS
        if used[key] and key in manifest_ids
    )
    fields = {
        f"{key}_manifest_id": manifest_ids.get(key) if used[key] else None
        for key in _MANIFEST_SOURCE_KEYS
    }
    return source_ids, fields


def validate_hr_dataset_rows(
    rows: Sequence[MLBHRBatterGameRow],
) -> MLBHRDatasetValidationResult:
    """Validate all rows plus row-ID uniqueness and leakage assertions."""

    errors: list[str] = []
    warnings: list[str] = []
    seen_row_ids: set[str] = set()
    for row_number, row in enumerate(rows, start=1):
        result = validate_batter_game_row(row)
        errors.extend(f"row {row_number}: {error}" for error in result.errors)
        warnings.extend(f"row {row_number}: {warning}" for warning in result.warnings)
        if row.row_id in seen_row_ids:
            errors.append(f"row {row_number}: duplicate row_id {row.row_id}")
        seen_row_ids.add(row.row_id)
        try:
            assert_feature_as_of_before_game(row)
        except MLBHRDatasetSchemaError as exc:
            errors.append(f"row {row_number}: {exc}")
    return MLBHRDatasetValidationResult(
        not errors,
        tuple(errors),
        _stable_unique(warnings),
    )


def build_hr_batter_game_rows_from_sources(
    *,
    statcast_rows: Sequence[MLBStatcastEventRow] = (),
    retrosheet_game_rows: Sequence[RetrosheetGameRow] = (),
    retrosheet_event_rows: Sequence[RetrosheetEventRow] = (),
    weather_rows: Sequence[MLBWeatherObservationRow] = (),
    ballpark_rows: Sequence[MLBBallparkFactorRow] = (),
    odds_rows: Sequence[MLBOddsSnapshotRow] = (),
    source_manifest_ids: Mapping[str, str] | None = None,
    generated_at: datetime | None = None,
    dataset_version: str = FIXTURE_DATASET_VERSION,
    generated_by: str = FIXTURE_GENERATED_BY,
) -> MLBHRDatasetBuildResult:
    """Join parsed fixture sources into default-deny batter-game rows."""

    if generated_at is None:
        generated_at = datetime.now(timezone.utc)
    if not isinstance(generated_at, datetime):
        raise MLBHRDatasetBuildError("generated_at must be a datetime")
    if _clean_text(dataset_version) is None:
        raise MLBHRDatasetBuildError("dataset_version must not be empty")
    if _clean_text(generated_by) is None:
        raise MLBHRDatasetBuildError("generated_by must not be empty")

    manifest_ids = _source_manifest_map(source_manifest_ids)
    build_warnings: list[str] = []
    skipped_rows: list[str] = []
    opportunities: dict[tuple[str, date, str], _Opportunity] = {}

    for row_number, row in enumerate(retrosheet_event_rows, start=1):
        key = _opportunity_key(
            row,
            source_name="retrosheet",
            row_number=row_number,
            skipped_rows=skipped_rows,
        )
        if key is not None:
            opportunities.setdefault(key, _Opportunity()).retrosheet_rows.append(row)
    for row_number, row in enumerate(statcast_rows, start=1):
        key = _opportunity_key(
            row,
            source_name="statcast",
            row_number=row_number,
            skipped_rows=skipped_rows,
        )
        if key is not None:
            opportunities.setdefault(key, _Opportunity()).statcast_rows.append(row)

    games = _game_index(retrosheet_game_rows, build_warnings)
    weather_by_game, weather_by_venue = _weather_indexes(weather_rows)
    ballparks = _ballpark_index(ballpark_rows)
    odds_pairing_enabled = bool(odds_rows) or "odds" in manifest_ids
    matched_odds_ids: set[int] = set()
    team_opponent_mismatch_ids: set[int] = set()
    duplicate_odds_matches = 0
    stale_odds = 0
    odds_timestamp_after_event_start_ids = {
        id(row)
        for row in odds_rows
        if row.event_start_time is not None
        and row.odds_collected_at >= row.event_start_time
    }
    rows: list[MLBHRBatterGameRow] = []

    for (game_id, game_date, player_id), opportunity in sorted(
        opportunities.items(), key=lambda item: item[0]
    ):
        row_warnings: list[str] = []
        game = games.get((game_id, game_date))
        retro = opportunity.retrosheet_rows
        statcast = opportunity.statcast_rows

        player_name = _clean_text(
            retro[0].batter_name if retro else statcast[0].player_name
        )
        if player_name is None:
            skipped_rows.append(
                f"batter-game {game_id}/{player_id} skipped: missing player_name"
            )
            continue

        stat_team, stat_opponent, _, stat_is_home = _statcast_team_context(statcast)
        team = _clean_text(retro[0].batting_team) if retro else stat_team
        opponent = _clean_text(retro[0].fielding_team) if retro else stat_opponent
        home_team = _clean_text(game.home_team) if game else None
        away_team = _clean_text(game.away_team) if game else None
        if statcast:
            home_team = home_team or _clean_text(statcast[0].home_team)
            away_team = away_team or _clean_text(statcast[0].away_team)
        is_home_team = (
            team == home_team if team and home_team else stat_is_home
        )

        venue_name = _clean_text(game.venue_name) if game else None
        weather = _select_weather(
            game_id=game_id,
            game_date=game_date,
            venue_name=venue_name,
            by_game=weather_by_game,
            by_venue=weather_by_venue,
            warnings=row_warnings,
        )
        if venue_name is None and weather is not None:
            venue_name = _clean_text(weather.venue_name)
            row_warnings.append("venue_name sourced from exact-game weather context")

        ballpark = ballparks.get(normalize_venue_name(venue_name)) if venue_name else None
        if ballpark is None:
            row_warnings.append("ballpark context missing")

        event_start = weather.event_start_time if weather is not None else None
        feature_as_of = _feature_cutoff(game_date, event_start)
        if event_start is None:
            row_warnings.append(
                "event_start_time missing; pregame cutoff cannot be verified"
            )
            leakage_status = "not_checked"
        else:
            leakage_status = "passed"

        odds: MLBOddsSnapshotRow | None = None
        if odds_pairing_enabled:
            candidates_by_priority: dict[int, list[MLBOddsSnapshotRow]] = {}
            for odds_candidate in odds_rows:
                priority = _odds_match_priority(
                    odds_candidate,
                    game_id=game_id,
                    game_date=game_date,
                    player_id=player_id,
                    player_name=player_name,
                    team=team,
                    opponent=opponent,
                )
                if priority is None:
                    continue
                if not _odds_team_context_is_safe(
                    odds_candidate, team=team, opponent=opponent
                ):
                    team_opponent_mismatch_ids.add(id(odds_candidate))
                    continue
                candidates_by_priority.setdefault(priority, []).append(odds_candidate)
            if candidates_by_priority:
                best_priority = min(candidates_by_priority)
                best = candidates_by_priority[best_priority]
                if len(best) == 1:
                    odds = best[0]
                    matched_odds_ids.add(id(odds))
                    if best_priority > 1:
                        row_warnings.append(
                            "odds matched by an unambiguous fallback identity"
                        )
                else:
                    duplicate_odds_matches += 1
                    row_warnings.append(
                        "odds context is ambiguous; duplicate matches were not attached"
                    )
            if odds is None and not any(
                "duplicate matches" in warning for warning in row_warnings
            ):
                row_warnings.append("odds market reference missing")

        odds_is_fresh = _odds_freshness(odds, event_start) if odds else None
        if odds is not None and odds_is_fresh is False:
            stale_odds += 1
            try:
                timestamp_after_start = odds.odds_collected_at >= event_start  # type: ignore[operator]
            except TypeError:
                timestamp_after_start = False
            if timestamp_after_start:
                odds_timestamp_after_event_start_ids.add(id(odds))
                row_warnings.append(
                    "odds timestamp is not pregame; market reference marked stale"
                )
            else:
                row_warnings.append(
                    "odds market reference is older than the 24-hour freshness window"
                )

        retro_hr_count = sum(1 for event in retro if event.is_home_run)
        statcast_hr_count = sum(1 for event in statcast if event.is_home_run)
        home_run_count = max(retro_hr_count, statcast_hr_count)
        if retro and statcast and retro_hr_count != statcast_hr_count:
            row_warnings.append(
                "Retrosheet and Statcast HR counts differ; maximum used without summing"
            )
        label_source = (
            "retrosheet+statcast"
            if retro and statcast
            else "retrosheet"
            if retro
            else "statcast"
        )
        label_times = [
            event.collected_at for event in (*retro, *statcast)
            if isinstance(event.collected_at, datetime)
        ]
        label_as_of = _latest_datetime(label_times)

        status = _clean_text(game.game_status) if game else "unknown"
        game_completed = True if status == "completed" else False if status in {
            "postponed",
            "suspended",
        } else None
        if game is None:
            row_warnings.append("Retrosheet game context missing; game status unknown")
        elif status != "completed":
            row_warnings.append(
                f"game status {status or 'unknown'} is not training eligible"
            )

        source_ids, manifest_fields = _row_manifest_fields(
            has_statcast=bool(statcast),
            has_retrosheet=bool(retro) or game is not None,
            has_weather=weather is not None,
            has_ballpark=ballpark is not None,
            has_odds=odds is not None,
            manifest_ids=manifest_ids,
        )
        if not source_ids:
            row_warnings.append("source manifest IDs unavailable")

        eligible = bool(
            game_completed is True
            and leakage_status == "passed"
            and home_run_count >= 0
            and player_name
        )
        data_quality = (
            "complete_fixture_context"
            if weather is not None and ballpark is not None and leakage_status == "passed"
            else "incomplete_fixture_context"
        )
        row = MLBHRBatterGameRow(
            dataset_version=dataset_version,
            row_id=dataset_row_id(game_id, player_id),
            game_id=game_id,
            game_date=game_date,
            event_start_time=event_start,
            season=game_date.year,
            game_number=game.game_number if game else None,
            player_id=player_id,
            player_name=player_name,
            team=team,
            opponent=opponent,
            home_team=home_team,
            away_team=away_team,
            is_home_team=is_home_team,
            venue_name=venue_name,
            weather_temperature=weather.temperature if weather else None,
            weather_wind_speed=weather.wind_speed if weather else None,
            weather_wind_direction=weather.wind_direction if weather else None,
            weather_wind_out_to_field=(
                weather.wind_out_to_field if weather else None
            ),
            weather_humidity=weather.humidity if weather else None,
            roof_status=weather.roof_status if weather else None,
            park_factor_hr=ballpark.park_factor_hr if ballpark else None,
            park_factor_lhb=(ballpark.handedness_factor_lhb if ballpark else None),
            park_factor_rhb=(ballpark.handedness_factor_rhb if ballpark else None),
            altitude=ballpark.altitude if ballpark else None,
            sportsbook=odds.sportsbook if odds else None,
            odds_provider=odds.provider if odds else None,
            hr_market_available=odds is not None,
            american_odds=odds.american_odds if odds else None,
            decimal_odds=odds.decimal_odds if odds else None,
            implied_probability=odds.implied_probability if odds else None,
            odds_collected_at=odds.odds_collected_at if odds else None,
            odds_as_of=odds.odds_collected_at if odds else None,
            odds_is_fresh_for_pregame=odds_is_fresh,
            hit_hr_today=home_run_count > 0,
            home_run_count=home_run_count,
            plate_appearances=len(retro) if retro else None,
            game_completed=game_completed,
            label_source=label_source,
            label_available=True,
            label_as_of=label_as_of,
            feature_as_of=feature_as_of,
            source_manifest_ids=source_ids,
            statcast_manifest_id=manifest_fields["statcast_manifest_id"],
            retrosheet_manifest_id=manifest_fields["retrosheet_manifest_id"],
            weather_manifest_id=manifest_fields["weather_manifest_id"],
            ballpark_manifest_id=manifest_fields["ballpark_manifest_id"],
            odds_manifest_id=manifest_fields["odds_manifest_id"],
            weather_source_type=weather.source_type if weather else None,
            ballpark_source_type=ballpark.source_type if ballpark else None,
            data_quality=data_quality,
            warnings=_stable_unique(row_warnings),
            leakage_check_status=leakage_status,
            eligible_for_training=eligible,
            eligible_for_backtest=eligible,
        )
        validation = validate_batter_game_row(row)
        if not validation.is_valid:
            skipped_rows.append(
                f"batter-game {game_id}/{player_id} skipped: "
                + "; ".join(validation.errors)
            )
            continue
        rows.append(row)

    all_source_dates = [
        parsed
        for source_row in (
            *statcast_rows,
            *retrosheet_game_rows,
            *retrosheet_event_rows,
            *weather_rows,
            *odds_rows,
        )
        if (parsed := _as_date(getattr(source_row, "game_date", None))) is not None
    ]
    output_dates = [row.game_date for row in rows if isinstance(row.game_date, date)]
    date_candidates = output_dates or all_source_dates
    if not date_candidates:
        raise MLBHRDatasetBuildError(
            "at least one valid game_date is required to build dataset metadata"
        )
    start = min(date_candidates)
    end = max(date_candidates)
    ordered_rows = tuple(
        sorted(
            rows,
            key=lambda row: (row.game_date, row.game_id, str(row.player_id)),
        )
    )
    all_manifest_ids = tuple(
        manifest_ids[key] for key in _MANIFEST_SOURCE_KEYS if key in manifest_ids
    )

    missing_context_summary = {
        "weather": sum(row.weather_source_type is None for row in ordered_rows),
        "ballpark": sum(row.ballpark_source_type is None for row in ordered_rows),
        "event_start_time": sum(row.event_start_time is None for row in ordered_rows),
        "venue_name": sum(row.venue_name is None for row in ordered_rows),
        "team": sum(row.team is None for row in ordered_rows),
        "opponent": sum(row.opponent is None for row in ordered_rows),
        "odds": sum(row.american_odds is None for row in ordered_rows),
    }
    odds_pairing_summary = {
        "odds_snapshot_rows": len(odds_rows),
        "odds_attached_rows": sum(
            row.american_odds is not None for row in ordered_rows
        ),
        "unmatched_odds_rows": sum(
            id(row) not in matched_odds_ids for row in odds_rows
        ),
        "rows_missing_odds": sum(
            row.american_odds is None for row in ordered_rows
        ),
        "duplicate_odds_matches": duplicate_odds_matches,
        "stale_odds": stale_odds,
        "odds_timestamp_after_event_start_time": len(
            odds_timestamp_after_event_start_ids
        ),
        "missing_player_id_in_odds": sum(row.player_id is None for row in odds_rows),
        "missing_game_id_in_odds": sum(row.game_id is None for row in odds_rows),
        "market_type_mismatch": sum(
            row.market_type != HOME_RUN_MARKET_TYPE for row in odds_rows
        ),
        "team_opponent_mismatch": len(team_opponent_mismatch_ids),
    }
    if skipped_rows:
        build_warnings.append(f"{len(skipped_rows)} source or batter-game rows skipped")
    for row in ordered_rows:
        build_warnings.extend(
            f"{row.game_id}/{row.player_id}: {warning}" for warning in row.warnings
        )
    metadata = MLBHRDatasetMetadata(
        dataset_id=_dataset_id(ordered_rows, all_manifest_ids),
        generated_at=generated_at,
        date_range_start=start,
        date_range_end=end,
        row_count=len(ordered_rows),
        generated_by=generated_by,
        source_manifest_ids=all_manifest_ids,
        mode="historical",
        warnings=_stable_unique(build_warnings),
    )
    metadata_validation = validate_dataset_metadata(metadata)
    if not metadata_validation.is_valid:
        raise MLBHRDatasetBuildError("; ".join(metadata_validation.errors))
    row_validation = validate_hr_dataset_rows(ordered_rows)
    if not row_validation.is_valid:
        raise MLBHRDatasetBuildError("; ".join(row_validation.errors))

    statuses = {row.leakage_check_status for row in ordered_rows}
    leakage_status = (
        "passed"
        if statuses == {"passed"}
        else "failed"
        if "failed" in statuses
        else "not_checked"
    )
    return MLBHRDatasetBuildResult(
        rows=ordered_rows,
        metadata=metadata,
        row_count=len(ordered_rows),
        date_range_start=start,
        date_range_end=end,
        source_manifest_ids=all_manifest_ids,
        warnings=metadata.warnings,
        skipped_rows=tuple(skipped_rows),
        missing_context_summary=missing_context_summary,
        odds_pairing_summary=odds_pairing_summary,
        leakage_check_status=leakage_status,
        eligible_for_training_count=sum(
            row.eligible_for_training for row in ordered_rows
        ),
        eligible_for_backtest_count=sum(
            row.eligible_for_backtest for row in ordered_rows
        ),
    )


def build_fixture_hr_batter_game_dataset(
    fixture_dir: str | Path,
    *,
    generated_at: datetime | None = None,
) -> MLBHRDatasetBuildResult:
    """Load the five existing local fixtures and build without writing files."""

    root = Path(fixture_dir).expanduser().resolve()
    paths = {
        "statcast": root / "statcast_sample.csv",
        "retrosheet_games": root / "retrosheet_games_sample.csv",
        "retrosheet_events": root / "retrosheet_events_sample.csv",
        "weather": root / "weather_sample.csv",
        "ballpark": root / "ballpark_factors_sample.csv",
    }
    missing = tuple(str(path) for path in paths.values() if not path.is_file())
    if missing:
        raise MLBHRDatasetBuildError(
            "required fixture files are missing: " + ", ".join(missing)
        )

    statcast = ingest_local_statcast_csv(paths["statcast"])
    retrosheet = ingest_local_retrosheet_csvs(
        games_csv=paths["retrosheet_games"],
        events_csv=paths["retrosheet_events"],
    )
    weather = ingest_local_weather_csv(paths["weather"])
    ballpark = ingest_local_ballpark_factors_csv(paths["ballpark"])
    manifests = {
        "statcast": _manifest_identifier(statcast.manifest),
        "retrosheet": _manifest_identifier(retrosheet.manifest),
        "weather": _manifest_identifier(weather.manifest),
        "ballpark": _manifest_identifier(ballpark.manifest),
    }
    return build_hr_batter_game_rows_from_sources(
        statcast_rows=statcast.rows,
        retrosheet_game_rows=retrosheet.games,
        retrosheet_event_rows=retrosheet.events,
        weather_rows=weather.rows,
        ballpark_rows=ballpark.rows,
        odds_rows=(),
        source_manifest_ids={key: value for key, value in manifests.items() if value},
        generated_at=generated_at,
    )


def _writable_path(path: str | Path, *, overwrite: bool) -> Path:
    destination = Path(path).expanduser().resolve()
    if not destination.parent.is_dir():
        raise MLBHRDatasetBuildError(
            f"output parent directory does not exist: {destination.parent}"
        )
    if destination.exists() and not overwrite:
        raise MLBHRDatasetBuildError(
            f"output already exists; pass overwrite=True: {destination}"
        )
    return destination


def write_hr_dataset_csv(
    result: MLBHRDatasetBuildResult,
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write validated rows only to an explicitly supplied file path."""

    validation = validate_hr_dataset_rows(result.rows)
    validation.raise_for_errors()
    destination = _writable_path(path, overwrite=overwrite)
    payloads = rows_to_csv_dicts(result.rows)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(ROW_FIELD_NAMES),
        )
        writer.writeheader()
        if payloads:
            writer.writerows(payloads)
    return destination


def write_hr_dataset_metadata_json(
    result: MLBHRDatasetBuildResult,
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write validated metadata only to an explicitly supplied file path."""

    destination = _writable_path(path, overwrite=overwrite)
    destination.write_text(
        metadata_to_json(result.metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


__all__ = [
    "FIXTURE_DATASET_VERSION",
    "MLBHRDatasetBuildError",
    "MLBHRDatasetBuildResult",
    "build_fixture_hr_batter_game_dataset",
    "build_hr_batter_game_rows_from_sources",
    "validate_hr_dataset_rows",
    "write_hr_dataset_csv",
    "write_hr_dataset_metadata_json",
]
