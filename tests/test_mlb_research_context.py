from __future__ import annotations

from dataclasses import fields, replace
from datetime import date, datetime, timezone
import json

import pytest

from courtvision.sports.mlb.research_context import (
    MLBBallparkContext,
    MLBContextValidationError,
    MLBGameContext,
    MLBHitterFeatureContext,
    MLBHRResearchContext,
    MLBLineupContext,
    MLBPitcherFeatureContext,
    MLBPlayerLineupStatus,
    MLBProbablePitcherContext,
    MLBTeamContext,
    MLBWeatherContext,
    build_sample_mlb_hr_contexts,
    context_is_complete_for_production,
    context_is_complete_for_research,
    summarize_context_warnings,
    validate_game_context,
    validate_hr_research_context,
    validate_lineup_context,
    validate_probable_pitcher_context,
)


RUN_DATE = date(2026, 6, 19)
COLLECTED_AT = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)


def _sample() -> MLBHRResearchContext:
    return build_sample_mlb_hr_contexts(RUN_DATE)[0]


def test_sample_context_builder_is_deterministic_and_aligned_to_sample_slate() -> None:
    first = build_sample_mlb_hr_contexts(RUN_DATE)
    second = build_sample_mlb_hr_contexts(RUN_DATE)

    assert len(first) == 3
    assert [item.to_dict() for item in first] == [item.to_dict() for item in second]
    assert first[0].game is not None
    assert first[0].game.game_id == "mlb-sample-2026-06-19-001"
    assert first[0].game.home_team == "CHC"
    assert first[0].hitter_features is not None
    assert first[0].hitter_features.player_name == "Example Player"
    assert first[0].probable_pitcher is not None
    assert first[0].probable_pitcher.pitcher_name == "Example Pitcher"


def test_context_serialization_is_stable_json_compatible_and_keeps_sources_visible() -> None:
    context = _sample()
    payload = context.to_dict()

    assert json.dumps(payload, sort_keys=True) == json.dumps(
        context.to_dict(), sort_keys=True
    )
    assert payload["sport"] == "MLB"
    assert payload["mode"] == "research"
    assert payload["game"]["game_date"] == "2026-06-19"  # type: ignore[index]
    assert payload["game"]["source_type"] == "sample"  # type: ignore[index]
    assert payload["lineup_status"]["source_type"] == "sample"  # type: ignore[index]
    assert payload["pitcher_features"]["pitch_mix"]["four-seam"] == 0.52  # type: ignore[index]


@pytest.mark.parametrize("source_type", ["sample", "manual", "mock"])
def test_non_live_source_types_remain_explicit(source_type: str) -> None:
    game = replace(_sample().game, source_type=source_type)  # type: ignore[arg-type]

    assert game.source_type == source_type
    assert game.to_dict()["source_type"] == source_type


def test_unknown_lineup_status_is_never_treated_as_confirmed() -> None:
    lineup = MLBLineupContext(
        game_id="game-1",
        team="CHC",
        lineup_confirmed=False,
        batting_order=(
            MLBPlayerLineupStatus(
                player_id="player-1",
                player_name="Unknown Batter",
                bats="R",
                batting_order=None,
                status="unknown",
            ),
        ),
        collected_at=COLLECTED_AT,
        source_type="manual",
    )

    assert lineup.batting_order[0].is_confirmed is False
    assert lineup.is_player_confirmed("player-1") is False
    assert validate_lineup_context(lineup).is_valid


def test_unknown_pitcher_status_is_never_treated_as_confirmed() -> None:
    pitcher = MLBProbablePitcherContext(
        game_id="game-1",
        team="STL",
        pitcher_id="pitcher-1",
        pitcher_name="Unknown Pitcher",
        throws="R",
        probable_status="unknown",
        collected_at=COLLECTED_AT,
        source_type="mock",
    )

    assert pitcher.is_confirmed is False
    assert validate_probable_pitcher_context(pitcher).is_valid


def test_missing_required_game_fields_fail_validation() -> None:
    context = _sample()
    assert context.game is not None
    invalid = replace(context.game, game_id="", venue_name="")
    result = validate_game_context(invalid)

    assert result.is_valid is False
    assert "game.game_id is required" in result.errors
    assert "game.venue_name is required" in result.errors
    with pytest.raises(MLBContextValidationError):
        result.raise_for_errors()


def test_incomplete_hr_context_reports_missing_fields_and_warnings() -> None:
    incomplete = replace(_sample(), weather=None, ballpark=None)
    result = validate_hr_research_context(incomplete)

    assert incomplete.context_complete is False
    assert incomplete.missing_required_fields == ("weather", "ballpark")
    assert result.is_valid is False
    assert context_is_complete_for_research(incomplete) is False
    warning_summary = summarize_context_warnings(incomplete)
    assert "Missing or invalid required context: weather." in warning_summary
    assert "Missing or invalid required context: ballpark." in warning_summary


def test_complete_sample_context_passes_research_completeness_only() -> None:
    for context in build_sample_mlb_hr_contexts(RUN_DATE):
        assert context.context_complete is True
        assert context.missing_required_fields == ()
        assert validate_hr_research_context(context).is_valid
        assert context_is_complete_for_research(context) is True
        assert context_is_complete_for_production(context) is False


def test_unknown_required_identities_make_combined_context_incomplete() -> None:
    context = _sample()
    assert context.lineup_status is not None
    unknown_player = replace(
        context.lineup_status.batting_order[0], status="unknown"
    )
    lineup = replace(
        context.lineup_status,
        lineup_confirmed=False,
        batting_order=(unknown_player,),
    )
    incomplete_lineup = replace(context, lineup_status=lineup)
    assert "lineup_status.hitter_status" in incomplete_lineup.missing_required_fields

    assert context.probable_pitcher is not None
    probable = replace(context.probable_pitcher, probable_status="unknown")
    incomplete_pitcher = replace(context, probable_pitcher=probable)
    assert (
        "probable_pitcher.probable_status"
        in incomplete_pitcher.missing_required_fields
    )


def test_every_contract_is_immutable_research_only_and_has_no_forbidden_fields() -> None:
    context = _sample()
    assert context.game is not None
    assert context.lineup_status is not None
    assert context.probable_pitcher is not None
    assert context.hitter_features is not None
    assert context.pitcher_features is not None
    assert context.weather is not None
    assert context.ballpark is not None
    team = MLBTeamContext(
        game_id=context.game.game_id,
        team=context.game.home_team,
        opponent=context.game.away_team,
        is_home=True,
        source_type="sample",
        collected_at=COLLECTED_AT,
        data_quality="sample_data",
    )
    models = (
        team,
        context.game,
        context.lineup_status,
        context.lineup_status.batting_order[0],
        context.probable_pitcher,
        context.hitter_features,
        context.pitcher_features,
        context.weather,
        context.ballpark,
        context,
    )
    forbidden = {
        "stake",
        "stake_amount",
        "unit",
        "units",
        "unit_size",
        "ev",
        "expected_value",
        "fair_probability",
        "estimated_fair_probability",
    }

    for model in models:
        assert model.mode == "research"
        assert forbidden.isdisjoint(item.name for item in fields(model))

    with pytest.raises(Exception):
        context.game.game_id = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        context.pitcher_features.pitch_mix["new"] = 1.0  # type: ignore[index]


def test_individual_feature_weather_and_ballpark_contracts_serialize() -> None:
    hitter = MLBHitterFeatureContext(
        player_id="p1",
        player_name="Player",
        bats="L",
        sample_window="recent_30_pa",
        recent_hr_rate=0.1,
        barrel_rate=0.2,
        hard_hit_rate=0.5,
        fly_ball_rate=0.4,
        pull_rate=0.45,
        avg_exit_velocity=91.0,
        max_exit_velocity=110.0,
        source_type="manual",
        as_of_date=RUN_DATE,
        data_quality="manually_entered",
    )
    pitcher = MLBPitcherFeatureContext(
        pitcher_id="sp1",
        pitcher_name="Pitcher",
        throws="R",
        pitch_mix={"four-seam": 0.6, "slider": 0.4},
        hr_allowed_rate=0.04,
        barrel_allowed_rate=0.08,
        hard_hit_allowed_rate=0.38,
        fly_ball_allowed_rate=0.35,
        source_type="mock",
        as_of_date=RUN_DATE,
        data_quality="mock_data",
    )
    weather = MLBWeatherContext(
        game_id="game-1",
        venue_name="Test Park",
        temperature=75,
        wind_speed=8,
        wind_direction="out to center",
        wind_out_to_field="center",
        source_type="manual",
        collected_at=COLLECTED_AT,
        data_quality="manual_data",
    )
    ballpark = MLBBallparkContext(
        venue_name="Test Park",
        park_factor_hr=1.0,
        handedness_factor={"L": 1.01, "R": 0.99},
        dimensions={"LF": 330, "CF": 400, "RF": 330},
        source_type="mock",
        data_version="fixture-v1",
        data_quality="mock_data",
    )

    for model in (hitter, pitcher, weather, ballpark):
        json.dumps(model.to_dict(), sort_keys=True)


def test_sample_builder_requires_plain_date() -> None:
    with pytest.raises(TypeError, match="report_date must be a date"):
        build_sample_mlb_hr_contexts(COLLECTED_AT)  # type: ignore[arg-type]
