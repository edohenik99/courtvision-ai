from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from courtvision.sports.mlb.ballpark_factors import score_ballpark_factor
from courtvision.sports.mlb.hr_features import score_power_form
from courtvision.sports.mlb.hr_prop_engine import (
    HRPropEngine,
    ResearchLabel,
    research_label_for_score,
)
from courtvision.sports.mlb.research_safety import MLB_NO_BETTING_REASON
from courtvision.sports.mlb.hr_report import main, sample_hr_props
from courtvision.sports.mlb.pitch_matchup import score_pitch_matchup
from courtvision.sports.mlb.weather_factor import score_environment


def test_hr_feature_scoring_rewards_power_form() -> None:
    strong = score_power_form(
        recent_plate_appearances=30,
        recent_batted_ball_events=[{"is_barrel": True}] * 3 + [{"is_barrel": False}] * 7,
        hard_hit_rate=0.56,
        barrel_rate=0.20,
        pull_rate=0.52,
        pull_barrel_rate=0.13,
        fly_ball_rate=0.49,
        max_exit_velocity=114,
        recent_home_runs=3,
    )
    weak = score_power_form(
        recent_plate_appearances=30,
        recent_batted_ball_events=[{"is_barrel": False}] * 10,
        hard_hit_rate=0.30,
        barrel_rate=0.05,
        pull_rate=0.32,
        pull_barrel_rate=0.02,
        fly_ball_rate=0.28,
        max_exit_velocity=99,
        recent_home_runs=0,
    )

    assert 0 <= weak.score < strong.score <= 100
    assert strong.recent_barrels == 3
    assert strong.score >= 85


def test_pitch_matchup_scoring_uses_pitch_mix_and_hr_rate() -> None:
    favorable = score_pitch_matchup(
        pitcher_pitch_mix={"fastball": 0.70, "slider": 0.30},
        hitter_vs_pitch_type={"fastball": 0.92, "slider": 0.80},
        pitcher_hr_allowed_rate=0.06,
        handedness="opposite",
    )
    difficult = score_pitch_matchup(
        pitcher_pitch_mix={"fastball": 0.70, "slider": 0.30},
        hitter_vs_pitch_type={"fastball": 0.35, "slider": 0.30},
        pitcher_hr_allowed_rate=0.02,
        handedness="same side",
    )

    assert favorable.score > difficult.score
    assert favorable.covered_pitch_mix == 1.0
    assert favorable.pitcher_hr_score > difficult.pitcher_hr_score


def test_weather_and_park_scoring_rewards_hr_conditions() -> None:
    favorable = score_environment(
        ballpark_hr_factor=1.15,
        wind_direction="blowing out to center",
        wind_speed=15,
        temperature=88,
    )
    unfavorable = score_environment(
        ballpark_hr_factor=0.85,
        wind_direction="blowing in from center",
        wind_speed=15,
        temperature=55,
    )

    assert score_ballpark_factor(1.15) > score_ballpark_factor(0.85)
    assert favorable.score > unfavorable.score
    assert favorable.wind_effect == "out"


def test_research_labels_never_use_production_recommendation_tiers() -> None:
    assert research_label_for_score(85) is ResearchLabel.RESEARCH_WATCHLIST
    assert research_label_for_score(75) is ResearchLabel.CANDIDATE
    assert research_label_for_score(65) is ResearchLabel.CANDIDATE
    assert research_label_for_score(64.99) is ResearchLabel.NOT_SELECTED


def test_engine_returns_rankable_hr_assessment() -> None:
    assessment = HRPropEngine().score(sample_hr_props(date(2026, 6, 19))[0])
    payload = assessment.to_dict()

    assert payload["sport"] == "MLB"
    assert payload["market"] == "Over 0.5 Home Runs"
    assert payload["odds"] == "+365"
    assert 0 <= payload["research_score"] <= 100
    assert payload["mode"] == "research"
    assert payload["eligible_for_betting"] is False
    assert payload["kelly_eligible"] is False
    assert payload["betting_approval_status"] == "research_only_not_betting_approved"
    assert payload["no_betting_reason"] == MLB_NO_BETTING_REASON
    assert "estimated_fair_probability" not in payload
    assert "implied_probability" not in payload
    assert not any("unit" in key or "stake" in key for key in payload)

    with pytest.raises(TypeError, match="eligible_for_betting"):
        replace(assessment, eligible_for_betting=True)


def test_cli_report_smoke(capsys) -> None:
    assert main(["--date", "2026-06-19"]) == 0
    output = capsys.readouterr().out

    assert "Research Watchlist" in output
    assert "Sample data" in output
    assert "Context not externally verified" in output
    assert "Research output only." in output
    assert "Research Score:" in output
    assert "Data Quality:" in output
    assert "Source: Sample Source A" in output
    assert "Source: Sample Source B" in output
    assert "Source: Sample Source C" in output
    assert "BetMGM" not in output
    assert "Matchup:" in output
    assert "Venue:" in output
    assert "Key reasons:" in output
    lowered = output.lower()
    for forbidden in (
        "bet",
        "elite",
        "strong",
        "wager",
        "unit",
        "kelly",
        "staking",
        "fair probability",
        "estimated fair",
        "betting edge",
        "bankroll",
        "recommendation",
        "production",
    ):
        assert forbidden not in lowered


def test_nba_regression_imports_still_resolve() -> None:
    from courtvision.projection.base_model import project_from_context as legacy
    from courtvision.sports.nba.projection import project_from_context as nba

    assert legacy is nba
