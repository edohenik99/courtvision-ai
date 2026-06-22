from __future__ import annotations

from dataclasses import fields, replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from courtvision.sports.mlb.data.ballpark_factors import (
    ingest_local_ballpark_factors_csv,
)
from courtvision.sports.mlb.data.retrosheet_ingestion import (
    ingest_local_retrosheet_csvs,
)
from courtvision.sports.mlb.data.statcast_ingestion import ingest_local_statcast_csv
from courtvision.sports.mlb.data.weather_ingestion import ingest_local_weather_csv
from courtvision.sports.mlb.training.hr_dataset_builder import (
    MLBHRDatasetBuildError,
    build_fixture_hr_batter_game_dataset,
    build_hr_batter_game_rows_from_sources,
    validate_hr_dataset_rows,
    write_hr_dataset_csv,
    write_hr_dataset_metadata_json,
)
from courtvision.sports.mlb.training.hr_dataset_schema import (
    FORBIDDEN_DECISION_FIELD_NAMES,
    OUTCOME_LABEL_FIELD_NAMES,
    PREGAME_FEATURE_FIELD_NAMES,
    MLBHRBatterGameRow,
    MLBHRDatasetSchemaError,
    assert_feature_as_of_before_game,
    dataset_row_id,
    row_feature_dict,
    row_label_dict,
    validate_batter_game_row,
    validate_dataset_metadata,
)


FIXTURES = Path(__file__).parent / "fixtures" / "mlb"
GENERATED_AT = datetime(2026, 6, 21, 16, 0, tzinfo=timezone.utc)
MANIFEST_IDS = {
    "statcast": "statcast-fixture-manifest",
    "retrosheet": "retrosheet-fixture-manifest",
    "weather": "weather-fixture-manifest",
    "ballpark": "ballpark-fixture-manifest",
}


@pytest.fixture(scope="module")
def parsed_sources():
    statcast = ingest_local_statcast_csv(
        FIXTURES / "statcast_sample.csv",
        collected_at=GENERATED_AT,
    )
    retrosheet = ingest_local_retrosheet_csvs(
        games_csv=FIXTURES / "retrosheet_games_sample.csv",
        events_csv=FIXTURES / "retrosheet_events_sample.csv",
        collected_at=GENERATED_AT,
    )
    weather = ingest_local_weather_csv(FIXTURES / "weather_sample.csv")
    ballpark = ingest_local_ballpark_factors_csv(
        FIXTURES / "ballpark_factors_sample.csv"
    )
    return statcast, retrosheet, weather, ballpark


def _aligned_result(parsed_sources, *, include_statcast: bool = False):
    statcast, retrosheet, weather, ballpark = parsed_sources
    game = retrosheet.games[0]
    aligned_weather = replace(
        weather.rows[0],
        game_id=game.game_id,
        game_date=game.game_date,
        venue_name=game.venue_name or "Rogers Centre",
        event_start_time=datetime(2025, 4, 1, 23, 5, tzinfo=timezone.utc),
    )
    aligned_ballpark = replace(
        ballpark.rows[0], venue_name=game.venue_name or "Rogers Centre"
    )
    aligned_statcast = replace(
        statcast.rows[0],
        game_id=game.game_id,
        game_date=game.game_date,
        player_id=retrosheet.events[0].batter_id,  # type: ignore[arg-type]
        player_name=retrosheet.events[0].batter_name,
        home_team=game.home_team,
        away_team=game.away_team,
        inning_half="Bot",
    )
    return build_hr_batter_game_rows_from_sources(
        statcast_rows=(aligned_statcast,) if include_statcast else (),
        retrosheet_game_rows=(game,),
        retrosheet_event_rows=retrosheet.events,
        weather_rows=(aligned_weather,),
        ballpark_rows=(aligned_ballpark,),
        source_manifest_ids=MANIFEST_IDS,
        generated_at=GENERATED_AT,
    )


def test_fixture_builder_loads_existing_local_outputs_without_writing() -> None:
    before = tuple(sorted(path.name for path in FIXTURES.iterdir()))

    result = build_fixture_hr_batter_game_dataset(
        FIXTURES, generated_at=GENERATED_AT
    )

    assert result.row_count == 4
    assert {(row.game_id, str(row.player_id)) for row in result.rows} == {
        ("20250401TORBOS-1", "b001"),
        ("20250401TORBOS-1", "b002"),
        ("777001", "592450"),
        ("777002", "682998"),
    }
    assert result.source_manifest_ids
    assert tuple(sorted(path.name for path in FIXTURES.iterdir())) == before


def test_fixture_builder_preserves_joined_and_missing_context_cases() -> None:
    result = build_fixture_hr_batter_game_dataset(
        FIXTURES, generated_at=GENERATED_AT
    )

    weather_rows = [row for row in result.rows if row.weather_temperature is not None]
    ballpark_rows = [row for row in result.rows if row.park_factor_hr is not None]
    complete_context_rows = [
        row
        for row in result.rows
        if row.weather_temperature is not None and row.park_factor_hr is not None
    ]
    incomplete_rows = [
        row
        for row in result.rows
        if row.weather_temperature is None or row.park_factor_hr is None
    ]

    assert weather_rows
    assert all(row.weather_wind_speed is not None for row in weather_rows)
    assert ballpark_rows
    assert complete_context_rows
    assert any(
        row.label_source == "retrosheet" and row.venue_name == "Rogers Centre"
        for row in complete_context_rows
    )
    assert incomplete_rows
    assert all(row.warnings for row in incomplete_rows)
    assert any(
        "weather context missing" in row.warnings for row in incomplete_rows
    )
    assert any(
        "ballpark context missing" in row.warnings for row in incomplete_rows
    )
    assert all(row.approval_status == "not_approved" for row in result.rows)
    assert all(row.eligible_for_betting is False for row in result.rows)
    assert all(row.kelly_eligible is False for row in result.rows)
    assert result.approval_status == "not_approved"
    assert result.eligible_for_betting is False
    assert result.kelly_eligible is False


def test_builder_creates_correct_hr_and_non_hr_labels(parsed_sources) -> None:
    result = _aligned_result(parsed_sources)
    by_player = {str(row.player_id): row for row in result.rows}

    assert by_player["b001"].hit_hr_today is True
    assert by_player["b001"].home_run_count == 1
    assert by_player["b002"].hit_hr_today is False
    assert by_player["b002"].home_run_count == 0
    assert all(row.label_available for row in result.rows)
    assert all(row.label_source == "retrosheet" for row in result.rows)


def test_completed_labeled_rows_can_be_training_and_backtest_eligible(
    parsed_sources,
) -> None:
    result = _aligned_result(parsed_sources)

    assert result.eligible_for_training_count == 2
    assert result.eligible_for_backtest_count == 2
    assert result.leakage_check_status == "passed"
    assert all(row.game_completed is True for row in result.rows)
    assert all(row.eligible_for_training for row in result.rows)
    assert all(row.eligible_for_backtest for row in result.rows)


def test_non_completed_and_unknown_statuses_fail_closed(parsed_sources) -> None:
    _, retrosheet, weather, ballpark = parsed_sources
    template_event = retrosheet.events[0]
    events = []
    weather_rows = []
    ballpark_rows = {}
    for index, game in enumerate(retrosheet.games):
        events.append(
            replace(
                template_event,
                game_id=game.game_id,
                game_date=game.game_date,
                batter_id=f"status-batter-{index}",
                batting_team=game.home_team,
                fielding_team=game.away_team,
            )
        )
        weather_rows.append(
            replace(
                weather.rows[0],
                game_id=game.game_id,
                game_date=game.game_date,
                venue_name=game.venue_name or f"Status Park {index}",
                event_start_time=datetime(
                    game.game_date.year,
                    game.game_date.month,
                    game.game_date.day,
                    23,
                    tzinfo=timezone.utc,
                ),
            )
        )
        venue_name = game.venue_name or f"Status Park {index}"
        ballpark_rows.setdefault(
            venue_name,
            replace(ballpark.rows[0], venue_name=venue_name),
        )

    result = build_hr_batter_game_rows_from_sources(
        retrosheet_game_rows=retrosheet.games,
        retrosheet_event_rows=tuple(events),
        weather_rows=tuple(weather_rows),
        ballpark_rows=tuple(ballpark_rows.values()),
        source_manifest_ids=MANIFEST_IDS,
        generated_at=GENERATED_AT,
    )
    by_status = {
        game.game_status: next(row for row in result.rows if row.game_id == game.game_id)
        for game in retrosheet.games
    }

    assert by_status["completed"].eligible_for_training is True
    assert by_status["postponed"].game_completed is False
    assert by_status["suspended"].game_completed is False
    assert by_status["unknown"].game_completed is None
    assert all(
        not by_status[status].eligible_for_training
        for status in ("postponed", "suspended", "unknown")
    )


def test_same_game_statcast_fields_are_not_pregame_features(parsed_sources) -> None:
    result = _aligned_result(parsed_sources, include_statcast=True)
    row = next(row for row in result.rows if str(row.player_id) == "b001")

    assert row.label_source == "retrosheet+statcast"
    assert row.home_run_count == 1
    assert row.hitter_avg_exit_velocity is None
    assert row.hitter_max_exit_velocity is None
    assert row.hitter_recent_barrel_rate is None
    assert row.batter_hand is None
    assert row.pitcher_hand is None
    assert row.primary_pitch_matchup_score is None


def test_label_namespace_remains_separate_from_features(parsed_sources) -> None:
    row = _aligned_result(parsed_sources).rows[0]

    assert set(row_feature_dict(row)).isdisjoint(OUTCOME_LABEL_FIELD_NAMES)
    assert set(row_label_dict(row)) == set(OUTCOME_LABEL_FIELD_NAMES)
    assert set(PREGAME_FEATURE_FIELD_NAMES).isdisjoint(OUTCOME_LABEL_FIELD_NAMES)


def test_feature_cutoff_passes_and_an_after_game_cutoff_fails(parsed_sources) -> None:
    row = _aligned_result(parsed_sources).rows[0]

    assert_feature_as_of_before_game(row)
    invalid = replace(row, feature_as_of="2025-04-02T00:00:00+00:00")
    assert not validate_batter_game_row(invalid).is_valid
    with pytest.raises(MLBHRDatasetSchemaError, match="must be before"):
        assert_feature_as_of_before_game(invalid)


def test_missing_weather_warns_without_fabricating_values(parsed_sources) -> None:
    _, retrosheet, _, ballpark = parsed_sources
    game = retrosheet.games[0]
    aligned_ballpark = replace(ballpark.rows[0], venue_name=game.venue_name)

    result = build_hr_batter_game_rows_from_sources(
        retrosheet_game_rows=(game,),
        retrosheet_event_rows=(retrosheet.events[0],),
        ballpark_rows=(aligned_ballpark,),
        source_manifest_ids=MANIFEST_IDS,
        generated_at=GENERATED_AT,
    )
    row = result.rows[0]

    assert row.weather_temperature is None
    assert row.weather_wind_speed is None
    assert row.weather_source_type is None
    assert any("weather context missing" in warning for warning in row.warnings)
    assert row.eligible_for_training is False


def test_missing_ballpark_warns_without_fabricating_values(parsed_sources) -> None:
    statcast, retrosheet, weather, _ = parsed_sources
    del statcast
    game = retrosheet.games[0]
    aligned_weather = replace(
        weather.rows[0],
        game_id=game.game_id,
        game_date=game.game_date,
        venue_name=game.venue_name,
        event_start_time=datetime(2025, 4, 1, 23, tzinfo=timezone.utc),
    )

    result = build_hr_batter_game_rows_from_sources(
        retrosheet_game_rows=(game,),
        retrosheet_event_rows=(retrosheet.events[0],),
        weather_rows=(aligned_weather,),
        source_manifest_ids=MANIFEST_IDS,
        generated_at=GENERATED_AT,
    )
    row = result.rows[0]

    assert row.park_factor_hr is None
    assert row.park_factor_lhb is None
    assert row.ballpark_source_type is None
    assert any("ballpark context missing" in warning for warning in row.warnings)


@pytest.mark.parametrize(("field_name", "value"), [("game_id", ""), ("batter_id", "")])
def test_missing_required_opportunity_identity_records_skipped_row(
    parsed_sources, field_name: str, value: str
) -> None:
    _, retrosheet, _, _ = parsed_sources
    invalid_event = replace(retrosheet.events[0], **{field_name: value})

    result = build_hr_batter_game_rows_from_sources(
        retrosheet_game_rows=(retrosheet.games[0],),
        retrosheet_event_rows=(invalid_event,),
        source_manifest_ids=MANIFEST_IDS,
        generated_at=GENERATED_AT,
    )

    assert result.rows == ()
    assert result.row_count == 0
    assert len(result.skipped_rows) == 1
    assert ("game_id" if field_name == "game_id" else "player_id") in result.skipped_rows[0]


def test_row_ids_and_dataset_metadata_are_deterministic_and_valid(parsed_sources) -> None:
    first = _aligned_result(parsed_sources)
    second = _aligned_result(parsed_sources)

    assert [row.row_id for row in first.rows] == [row.row_id for row in second.rows]
    assert first.metadata.dataset_id == second.metadata.dataset_id
    assert first.rows[0].row_id == dataset_row_id(
        first.rows[0].game_id, first.rows[0].player_id
    )
    assert validate_dataset_metadata(first.metadata).is_valid
    assert validate_hr_dataset_rows(first.rows).is_valid


def test_result_and_rows_remain_default_deny_and_decision_free(parsed_sources) -> None:
    result = _aligned_result(parsed_sources)
    row_names = {item.name for item in fields(MLBHRBatterGameRow)}

    assert result.eligible_for_betting is False
    assert result.kelly_eligible is False
    assert result.approval_status == "not_approved"
    assert result.metadata.eligible_for_betting is False
    assert result.metadata.kelly_eligible is False
    assert result.metadata.approval_status == "not_approved"
    assert all(row.eligible_for_betting is False for row in result.rows)
    assert all(row.kelly_eligible is False for row in result.rows)
    assert all(row.approval_status == "not_approved" for row in result.rows)
    assert row_names.isdisjoint(FORBIDDEN_DECISION_FIELD_NAMES)


def test_optional_writers_require_explicit_paths_and_write_to_pytest_tmp(
    parsed_sources, tmp_path: Path
) -> None:
    result = _aligned_result(parsed_sources)
    csv_path = tmp_path / "fixture_rows.csv"
    metadata_path = tmp_path / "fixture_metadata.json"

    assert write_hr_dataset_csv(result, csv_path) == csv_path
    assert write_hr_dataset_metadata_json(result, metadata_path) == metadata_path
    assert csv_path.read_text(encoding="utf-8").startswith("sport,league,")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["row_count"] == result.row_count
    assert metadata["approval_status"] == "not_approved"
    with pytest.raises(MLBHRDatasetBuildError, match="already exists"):
        write_hr_dataset_csv(result, csv_path)
