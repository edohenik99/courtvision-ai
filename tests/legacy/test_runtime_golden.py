from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

import courtvision_ai
from courtvision.calibration.grading_summary import (
    summarize_elite_filter_replay,
    summarize_graded_props,
    summarize_player_points_calibration,
    summarize_player_points_uplift_audit,
)
from courtvision.config import Settings
from courtvision.grading import PickGrader as ExportedPickGrader
from courtvision.grading.grade_props import PickGrader
from courtvision.models import GradedProp, PlayerGameStats
from courtvision.runtime_markets import filter_player_markets
from courtvision.runtime_selection import PlayerSelectionPolicy, elite_points_risk_guard_reason


def _stub_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.NullHandler())
    return logger


@pytest.fixture
def workspace_tmp() -> Path:
    root = Path("tests_artifacts") / f"runtime_golden_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def ai(workspace_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> courtvision_ai.CourtVisionAI:
    monkeypatch.setattr(courtvision_ai, "_get_logger", lambda _: _stub_logger(f"courtvision-test-{workspace_tmp.name}"))
    return courtvision_ai.CourtVisionAI(out_dir=str(workspace_tmp / "outputs"))


def test_board_generation_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    selected_df = pd.DataFrame(
        [
            {
                "prediction_date": "2026-04-10",
                "market_type": "player_points",
                "entity_name": "Alpha Star",
                "team": "BOS",
                "opponent": "NYK",
                "selection": "OVER",
                "sportsbook_line": 24.5,
                "model_projection": 27.0,
                "edge": 2.5,
                "edge_abs": 2.5,
                "confidence": 0.78,
                "odds": -115,
                "minutes_avg": 35.0,
                "minutes_recent": 36.0,
            },
            {
                "prediction_date": "2026-04-10",
                "market_type": "moneyline",
                "entity_name": "LA Clippers",
                "team": "LAC",
                "opponent": "PHX",
                "selection": "ML",
                "sportsbook_line": 0.0,
                "model_projection": 0.58,
                "edge": 0.08,
                "edge_abs": 0.08,
                "confidence": 0.75,
                "odds": 110,
            },
            {
                "prediction_date": "2026-04-10",
                "market_type": "moneyline",
                "entity_name": "Charlotte Hornets",
                "team": "CHA",
                "opponent": "MIL",
                "selection": "ML",
                "sportsbook_line": 0.0,
                "model_projection": 0.34,
                "edge": 0.18,
                "edge_abs": 0.18,
                "confidence": 0.73,
                "odds": 650,
            },
            {
                "prediction_date": "2026-04-10",
                "market_type": "player_blocks",
                "entity_name": "Bench Blocker",
                "team": "ORL",
                "opponent": "MIA",
                "selection": "OVER",
                "sportsbook_line": 1.5,
                "model_projection": 2.4,
                "edge": 0.9,
                "edge_abs": 0.9,
                "confidence": 0.74,
                "odds": 105,
                "minutes_avg": 18.0,
                "minutes_recent": 17.0,
            },
        ]
    )

    prepared = ai._prepare_selected_board(selected_df)
    elite = ai._select_elite_board(prepared)

    elite_entities = elite["entity_name"].tolist()
    assert "Alpha Star" in elite_entities
    assert "LA Clippers" in elite_entities
    assert "Charlotte Hornets" not in elite_entities
    assert "Bench Blocker" not in elite_entities
    assert elite.iloc[0]["entity_name"] == "Alpha Star"
    assert "LA Clippers" in elite["entity_name"].head(2).tolist()


def test_moneyline_qualification_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    longshot = ai._qualify_or_reject(
        market_type="moneyline",
        entity_name="Charlotte Hornets",
        team="CHA",
        opponent="MIL",
        sportsbook_line=0.0,
        model_projection=0.34,
        edge=0.18,
        confidence=0.73,
        selection="ML",
        prediction_date="2026-04-10",
        odds=650,
    )
    sane_plus_money = ai._qualify_or_reject(
        market_type="moneyline",
        entity_name="LA Clippers",
        team="LAC",
        opponent="PHX",
        sportsbook_line=0.0,
        model_projection=0.58,
        edge=0.08,
        confidence=0.75,
        selection="ML",
        prediction_date="2026-04-10",
        odds=110,
    )

    assert longshot["qualified"] is False
    assert longshot["row"]["rejection_reason"] == "quality_below_threshold"
    assert longshot["row"]["qualification_reason"] == ""
    assert longshot["row"]["odds_bucket"] == "plus_big"
    assert sane_plus_money["qualified"] is True
    assert sane_plus_money["row"]["recommendation"] == "qualified"
    assert sane_plus_money["row"]["qualification_reason"] == "moneyline_core_pass"
    assert sane_plus_money["row"]["minutes_bucket"] == "not_applicable"
    assert sane_plus_money["row"]["odds_bucket"] == "near_even"
    assert sane_plus_money["row"]["market_trust_weight"] >= 0.9


def test_asymmetric_qualification_gate_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    strong_edge = ai._qualify_or_reject(
        market_type="player_points",
        entity_name="Alpha Star",
        team="BOS",
        opponent="NYK",
        sportsbook_line=24.5,
        model_projection=27.4,
        edge=2.9,
        confidence=0.59,
        selection="OVER",
        prediction_date="2026-04-10",
        odds=-115,
    )
    strong_confidence = ai._qualify_or_reject(
        market_type="player_points",
        entity_name="Beta Star",
        team="PHX",
        opponent="LAL",
        sportsbook_line=23.5,
        model_projection=25.18,
        edge=1.68,
        confidence=0.68,
        selection="OVER",
        prediction_date="2026-04-10",
        odds=-110,
    )
    weak_both = ai._qualify_or_reject(
        market_type="player_points",
        entity_name="Gamma Star",
        team="DAL",
        opponent="DEN",
        sportsbook_line=24.5,
        model_projection=25.8,
        edge=1.3,
        confidence=0.57,
        selection="OVER",
        prediction_date="2026-04-10",
        odds=-108,
    )

    assert strong_edge["qualified"] is True
    assert strong_edge["row"]["qualification_gate_mode"] == "strong_edge_override"
    assert strong_edge["row"]["strong_edge_override_passed"] is True
    assert strong_edge["row"]["confidence_threshold_passed"] is False

    assert strong_confidence["qualified"] is True
    assert strong_confidence["row"]["qualification_gate_mode"] == "strong_confidence_override"
    assert strong_confidence["row"]["strong_confidence_override_passed"] is True
    assert strong_confidence["row"]["edge_threshold_passed"] is False

    assert weak_both["qualified"] is False
    assert weak_both["row"]["qualification_gate_mode"] == "rejected"
    assert weak_both["row"]["rejection_reason"] == "edge_and_confidence_below_threshold"


def test_core_pass_does_not_mark_override_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    core_pass = ai._qualify_or_reject(
        market_type="player_points",
        entity_name="Alpha Star",
        team="BOS",
        opponent="NYK",
        sportsbook_line=24.5,
        model_projection=28.2,
        edge=3.7,
        confidence=0.78,
        selection="OVER",
        prediction_date="2026-04-10",
        odds=-115,
        extra_fields={
            "minutes_avg": 35.0,
            "minutes_recent": 36.0,
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
        },
    )

    assert core_pass["qualified"] is True
    assert core_pass["row"]["qualification_gate_mode"] == "core_pass"
    assert core_pass["row"]["strong_edge_override_passed"] is False
    assert core_pass["row"]["strong_confidence_override_passed"] is False
    assert core_pass["row"]["live_quality_rescue_passed"] is False


def test_live_quality_rescue_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    rescued = ai._qualify_or_reject(
        market_type="player_points",
        entity_name="Amen Thompson",
        team="HOU",
        opponent="MIN",
        sportsbook_line=20.5,
        model_projection=22.34,
        edge=1.84,
        confidence=0.58,
        selection="OVER",
        prediction_date="2026-04-10",
        odds=-118,
        extra_fields={
            "minutes_avg": 37.7,
            "minutes_recent": 36.4,
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
        },
    )

    assert rescued["qualified"] is True
    assert rescued["row"]["qualification_gate_mode"] == "live_quality_rescue"
    assert rescued["row"]["live_quality_rescue_passed"] is True
    assert rescued["row"]["qualification_reason"] == "live_quality_rescue_pass"
    assert rescued["row"]["rejection_reason"] == ""


def test_elite_gate_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    low_minute_prop = ai._apply_scoring_metadata(
        {
            "prediction_date": "2026-04-10",
            "market_type": "player_blocks",
            "entity_name": "Bench Blocker",
            "team": "ORL",
            "opponent": "MIA",
            "selection": "OVER",
            "sportsbook_line": 1.5,
            "edge": 0.9,
            "edge_abs": 0.9,
            "confidence": 0.74,
            "odds": 105,
            "minutes_avg": 18.0,
            "minutes_recent": 17.0,
        }
    )
    core_play = ai._apply_scoring_metadata(
        {
            "prediction_date": "2026-04-10",
            "market_type": "player_points",
            "entity_name": "Alpha Star",
            "team": "BOS",
            "opponent": "NYK",
            "selection": "OVER",
            "sportsbook_line": 25.5,
            "edge": 2.5,
            "edge_abs": 2.5,
            "confidence": 0.77,
            "odds": -115,
            "minutes_avg": 35.0,
            "minutes_recent": 36.0,
        }
    )
    synthetic_play = ai._apply_scoring_metadata(
        {
            "prediction_date": "2026-04-10",
            "market_type": "player_points",
            "entity_name": "Synthetic Star",
            "team": "BOS",
            "opponent": "NYK",
            "selection": "OVER",
            "sportsbook_line": 24.5,
            "edge": 3.6,
            "edge_abs": 3.6,
            "confidence": 0.78,
            "odds": None,
            "minutes_avg": 35.0,
            "minutes_recent": 35.5,
            "synthetic_line": True,
            "is_live_market": False,
        }
    )

    assert ai._is_elite_candidate(low_minute_prop) is False
    assert ai._is_elite_candidate(core_play) is True
    assert ai._is_elite_candidate(synthetic_play) is False


def test_elite_points_risk_guard_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    injury_role_over = ai._apply_scoring_metadata(
        {
            "prediction_date": "2026-04-10",
            "market_type": "player_points",
            "entity_name": "Inflated Role Scorer",
            "team": "POR",
            "opponent": "LAC",
            "selection": "OVER",
            "sportsbook_line": 13.5,
            "edge": 7.4,
            "edge_abs": 7.4,
            "confidence": 0.6865,
            "odds": -113,
            "minutes_avg": 33.5,
            "minutes_recent": 35.0,
            "selection_team_injury_impact": 0.49,
            "team_injury_impact": 0.49,
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
            "qualification_gate_mode": "core_pass",
        }
    )
    weak_role_under = ai._apply_scoring_metadata(
        {
            "prediction_date": "2026-04-10",
            "market_type": "player_points",
            "entity_name": "Role Under",
            "team": "UTA",
            "opponent": "MEM",
            "selection": "UNDER",
            "sportsbook_line": 13.5,
            "edge": -5.4,
            "edge_abs": 5.4,
            "confidence": 0.735,
            "odds": -117,
            "minutes_avg": 32.8,
            "minutes_recent": 29.2,
            "selection_team_injury_impact": 0.29,
            "team_injury_impact": 0.29,
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
            "qualification_gate_mode": "core_pass",
        }
    )
    weak_secondary_under = ai._apply_scoring_metadata(
        {
            "prediction_date": "2026-04-10",
            "market_type": "player_points",
            "entity_name": "Secondary Under",
            "team": "ATL",
            "opponent": "CLE",
            "selection": "UNDER",
            "sportsbook_line": 26.5,
            "edge": -8.4,
            "edge_abs": 8.4,
            "confidence": 0.703,
            "odds": -120,
            "minutes_avg": 29.9,
            "minutes_recent": 31.2,
            "selection_opponent_injury_impact": 0.19,
            "opponent_injury_impact": 0.19,
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
            "qualification_gate_mode": "core_pass",
        }
    )
    stable_star_under = ai._apply_scoring_metadata(
        {
            "prediction_date": "2026-04-10",
            "market_type": "player_points",
            "entity_name": "Stable Star Under",
            "team": "CLE",
            "opponent": "ATL",
            "selection": "UNDER",
            "sportsbook_line": 25.5,
            "edge": -4.3,
            "edge_abs": 4.3,
            "confidence": 0.714,
            "odds": -130,
            "minutes_avg": 34.6,
            "minutes_recent": 34.4,
            "selection_team_injury_impact": 0.19,
            "team_injury_impact": 0.19,
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
            "qualification_gate_mode": "core_pass",
        }
    )
    stable_low_line_over = ai._apply_scoring_metadata(
        {
            "prediction_date": "2026-04-10",
            "market_type": "player_points",
            "entity_name": "Stable Low Line Over",
            "team": "ATL",
            "opponent": "CLE",
            "selection": "OVER",
            "sportsbook_line": 16.5,
            "edge": 2.8,
            "edge_abs": 2.8,
            "confidence": 0.722,
            "odds": -102,
            "minutes_avg": 34.0,
            "minutes_recent": 36.0,
            "selection_opponent_injury_impact": 0.19,
            "opponent_injury_impact": 0.19,
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
            "qualification_gate_mode": "core_pass",
        }
    )

    assert ai._is_elite_candidate(injury_role_over) is False
    assert ai._is_elite_candidate(weak_role_under) is False
    assert ai._is_elite_candidate(weak_secondary_under) is False
    assert ai._is_elite_candidate(stable_star_under) is True
    assert ai._is_elite_candidate(stable_low_line_over) is True
    assert elite_points_risk_guard_reason(injury_role_over) == "injury_driven_low_line_over"
    assert elite_points_risk_guard_reason(weak_role_under) == "weak_role_under"
    assert elite_points_risk_guard_reason(weak_secondary_under) == "weak_secondary_under"
    assert elite_points_risk_guard_reason(stable_star_under) == ""
    assert ai.board_volume.is_elite_backfill_candidate(injury_role_over) is False
    assert ai.board_volume.is_elite_backfill_candidate(weak_role_under) is False
    assert ai.board_volume.is_elite_backfill_candidate(stable_star_under) is True


def test_player_points_confidence_multiplier_deemphasizes_minutes_only_golden(
    ai: courtvision_ai.CourtVisionAI,
) -> None:
    weak_points = {
        "prediction_date": "2026-04-10",
        "market_type": "player_points",
        "entity_name": "Inflated Role Scorer",
        "team": "POR",
        "opponent": "LAC",
        "selection": "OVER",
        "sportsbook_line": 13.5,
        "model_projection": 20.9,
        "edge": 7.4,
        "edge_abs": 7.4,
        "confidence": 0.6865,
        "odds": -113,
        "minutes_avg": 33.5,
        "minutes_recent": 35.0,
        "recent_avg": 10.8,
        "season_avg": 9.7,
        "selection_team_injury_impact": 0.49,
        "team_injury_impact": 0.49,
    }
    stable_star_points = {
        "prediction_date": "2026-04-10",
        "market_type": "player_points",
        "entity_name": "Stable Star",
        "team": "CLE",
        "opponent": "ATL",
        "selection": "UNDER",
        "sportsbook_line": 25.5,
        "model_projection": 21.2,
        "edge": -4.3,
        "edge_abs": 4.3,
        "confidence": 0.714,
        "odds": -130,
        "minutes_avg": 34.6,
        "minutes_recent": 34.4,
        "recent_avg": 24.7,
        "season_avg": 25.1,
        "selection_team_injury_impact": 0.19,
        "team_injury_impact": 0.19,
    }
    stable_rebounds = {
        "prediction_date": "2026-04-10",
        "market_type": "player_rebounds",
        "entity_name": "Stable Big",
        "team": "BOS",
        "opponent": "NYK",
        "selection": "OVER",
        "sportsbook_line": 8.5,
        "model_projection": 10.4,
        "edge": 1.9,
        "edge_abs": 1.9,
        "confidence": 0.6865,
        "odds": -118,
        "minutes_avg": 33.5,
        "minutes_recent": 35.0,
    }

    weak_multiplier = ai.board_scoring.historical_confidence_multiplier(weak_points)
    star_multiplier = ai.board_scoring.historical_confidence_multiplier(stable_star_points)
    rebounds_multiplier = ai.board_scoring.historical_confidence_multiplier(stable_rebounds)

    assert weak_multiplier < star_multiplier
    assert weak_multiplier < rebounds_multiplier


def test_borderline_points_downgrade_without_blocking_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    borderline_secondary_under = ai._apply_scoring_metadata(
        {
            "prediction_date": "2026-04-10",
            "market_type": "player_points",
            "entity_name": "Borderline Secondary Under",
            "team": "MIA",
            "opponent": "ORL",
            "selection": "UNDER",
            "sportsbook_line": 28.5,
            "model_projection": 24.9,
            "edge": -3.6,
            "edge_abs": 3.6,
            "confidence": 0.74,
            "odds": -112,
            "minutes_avg": 30.2,
            "minutes_recent": 30.8,
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
            "qualification_gate_mode": "core_pass",
        }
    )
    stable_rebounds = ai._apply_scoring_metadata(
        {
            "prediction_date": "2026-04-10",
            "market_type": "player_rebounds",
            "entity_name": "Stable Big",
            "team": "MIA",
            "opponent": "ORL",
            "selection": "OVER",
            "sportsbook_line": 8.5,
            "model_projection": 10.4,
            "edge": 1.9,
            "edge_abs": 1.9,
            "confidence": 0.68,
            "odds": -118,
            "minutes_avg": 33.2,
            "minutes_recent": 34.0,
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
            "qualification_gate_mode": "core_pass",
        }
    )

    assert ai._is_elite_candidate(borderline_secondary_under) is True
    assert borderline_secondary_under["elite_points_ranking_penalty"] > 0.0
    assert borderline_secondary_under["elite_points_ranking_reason"] == "borderline_secondary_under"
    assert borderline_secondary_under["elite_rank_score"] < borderline_secondary_under["quality_score"]
    assert stable_rebounds["elite_points_ranking_penalty"] == 0.0

    full_market = ai._select_top_per_market(
        pd.DataFrame([borderline_secondary_under, stable_rebounds]),
        per_market_limit=20,
    )
    assert "Borderline Secondary Under" in full_market["entity_name"].tolist()


def test_elite_backfill_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    prepared = ai._prepare_selected_board(
        pd.DataFrame(
            [
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Alpha Star",
                    "team": "BOS",
                    "opponent": "NYK",
                    "selection": "OVER",
                    "sportsbook_line": 24.5,
                    "model_projection": 27.0,
                    "edge": 2.5,
                    "edge_abs": 2.5,
                    "confidence": 0.78,
                    "odds": -115,
                    "minutes_avg": 35.0,
                    "minutes_recent": 36.0,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                    "player_points_realism_dampened": True,
                    "player_points_realism_dampener_reason": "fragile_mid_line_injury_over",
                    "injury_projection_delta": 1.2,
                    "injury_confidence_delta": 0.02,
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Beta Star",
                    "team": "MIA",
                    "opponent": "ORL",
                    "selection": "UNDER",
                    "sportsbook_line": 26.5,
                    "model_projection": 22.8,
                    "edge": -3.7,
                    "edge_abs": 3.7,
                    "confidence": 0.76,
                    "odds": -112,
                    "minutes_avg": 34.0,
                    "minutes_recent": 34.6,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Gamma Star",
                    "team": "DEN",
                    "opponent": "OKC",
                    "selection": "OVER",
                    "sportsbook_line": 23.5,
                    "model_projection": 26.8,
                    "edge": 3.3,
                    "edge_abs": 3.3,
                    "confidence": 0.74,
                    "odds": -110,
                    "minutes_avg": 35.6,
                    "minutes_recent": 36.2,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_3pt_made",
                    "entity_name": "Delta Shooter",
                    "team": "PHX",
                    "opponent": "LAL",
                    "selection": "OVER",
                    "sportsbook_line": 2.5,
                    "model_projection": 4.0,
                    "edge": 1.5,
                    "edge_abs": 1.5,
                    "confidence": 0.68,
                    "odds": -138,
                    "minutes_avg": 31.0,
                    "minutes_recent": 31.4,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Epsilon Wing",
                    "team": "TOR",
                    "opponent": "NYK",
                    "selection": "OVER",
                    "sportsbook_line": 17.5,
                    "model_projection": 20.0,
                    "edge": 2.5,
                    "edge_abs": 2.5,
                    "confidence": 0.60,
                    "odds": -112,
                    "minutes_avg": 32.5,
                    "minutes_recent": 33.0,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "live_quality_rescue",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_assists",
                    "entity_name": "Zeta Creator",
                    "team": "ATL",
                    "opponent": "CLE",
                    "selection": "OVER",
                    "sportsbook_line": 5.5,
                    "model_projection": 7.6,
                    "edge": 2.1,
                    "edge_abs": 2.1,
                    "confidence": 0.60,
                    "odds": -118,
                    "minutes_avg": 33.0,
                    "minutes_recent": 33.4,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                },
            ]
        )
    )

    elite = ai._select_elite_board(prepared)

    assert len(elite) == 5
    assert "Epsilon Wing" in elite["entity_name"].tolist()
    assert "Zeta Creator" in elite["entity_name"].tolist()


def test_elite_player_cap_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    prepared = ai._prepare_selected_board(
        pd.DataFrame(
            [
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Alpha Star",
                    "team": "BOS",
                    "opponent": "NYK",
                    "selection": "OVER",
                    "sportsbook_line": 24.5,
                    "model_projection": 28.2,
                    "edge": 3.7,
                    "edge_abs": 3.7,
                    "confidence": 0.78,
                    "odds": -115,
                    "minutes_avg": 35.0,
                    "minutes_recent": 36.0,
                    "is_live_market": True,
                    "synthetic_line": False,
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_3pt_made",
                    "entity_name": "Alpha Star",
                    "team": "BOS",
                    "opponent": "NYK",
                    "selection": "OVER",
                    "sportsbook_line": 2.5,
                    "model_projection": 4.0,
                    "edge": 1.5,
                    "edge_abs": 1.5,
                    "confidence": 0.76,
                    "odds": -110,
                    "minutes_avg": 35.0,
                    "minutes_recent": 36.0,
                    "is_live_market": True,
                    "synthetic_line": False,
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Beta Star",
                    "team": "MIA",
                    "opponent": "ORL",
                    "selection": "UNDER",
                    "sportsbook_line": 26.5,
                    "model_projection": 22.9,
                    "edge": -3.6,
                    "edge_abs": 3.6,
                    "confidence": 0.77,
                    "odds": -112,
                    "minutes_avg": 34.0,
                    "minutes_recent": 34.5,
                    "is_live_market": True,
                    "synthetic_line": False,
                },
            ]
        )
    )

    elite = ai._select_elite_board(prepared)
    alpha_rows = elite[elite["entity_name"] == "Alpha Star"]

    assert len(alpha_rows) == 1


def test_full_market_backfill_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    prepared = ai._prepare_selected_board(
        pd.DataFrame(
            [
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Alpha Star",
                    "team": "BOS",
                    "opponent": "NYK",
                    "selection": "OVER",
                    "sportsbook_line": 24.5,
                    "model_projection": 28.2,
                    "edge": 3.7,
                    "edge_abs": 3.7,
                    "confidence": 0.78,
                    "odds": -115,
                    "minutes_avg": 35.0,
                    "minutes_recent": 36.0,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                    "selection_team_injury_impact": 0.22,
                    "team_injury_impact": 0.22,
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_rebounds",
                    "entity_name": "Beta Big",
                    "team": "BOS",
                    "opponent": "NYK",
                    "selection": "OVER",
                    "sportsbook_line": 8.5,
                    "model_projection": 10.2,
                    "edge": 1.7,
                    "edge_abs": 1.7,
                    "confidence": 0.64,
                    "odds": -118,
                    "minutes_avg": 31.0,
                    "minutes_recent": 31.6,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_assists",
                    "entity_name": "Gamma Guard",
                    "team": "BOS",
                    "opponent": "NYK",
                    "selection": "OVER",
                    "sportsbook_line": 5.5,
                    "model_projection": 7.1,
                    "edge": 1.6,
                    "edge_abs": 1.6,
                    "confidence": 0.63,
                    "odds": -110,
                    "minutes_avg": 30.0,
                    "minutes_recent": 30.4,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_3pt_made",
                    "entity_name": "Delta Shooter",
                    "team": "NYK",
                    "opponent": "BOS",
                    "selection": "OVER",
                    "sportsbook_line": 2.5,
                    "model_projection": 4.0,
                    "edge": 1.5,
                    "edge_abs": 1.5,
                    "confidence": 0.68,
                    "odds": -138,
                    "minutes_avg": 31.0,
                    "minutes_recent": 31.4,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Epsilon Wing",
                    "team": "NYK",
                    "opponent": "BOS",
                    "selection": "UNDER",
                    "sportsbook_line": 19.5,
                    "model_projection": 17.3,
                    "edge": -2.2,
                    "edge_abs": 2.2,
                    "confidence": 0.61,
                    "odds": -112,
                    "minutes_avg": 32.5,
                    "minutes_recent": 33.0,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "live_quality_rescue",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_rebounds",
                    "entity_name": "Zeta Big",
                    "team": "NYK",
                    "opponent": "BOS",
                    "selection": "UNDER",
                    "sportsbook_line": 9.5,
                    "model_projection": 7.8,
                    "edge": -1.7,
                    "edge_abs": 1.7,
                    "confidence": 0.62,
                    "odds": -119,
                    "minutes_avg": 29.5,
                    "minutes_recent": 29.9,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                },
            ]
        )
    )

    full_market = ai._select_top_per_market(prepared, per_market_limit=20)

    assert len(full_market) == 6


def test_final_operator_board_trace_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    prepared = ai._prepare_selected_board(
        pd.DataFrame(
            [
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Alpha Star",
                    "team": "BOS",
                    "opponent": "NYK",
                    "selection": "OVER",
                    "sportsbook_line": 24.5,
                    "model_projection": 28.2,
                    "edge": 3.7,
                    "edge_abs": 3.7,
                    "confidence": 0.78,
                    "odds": -115,
                    "minutes_avg": 35.0,
                    "minutes_recent": 36.0,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                    "selection_team_injury_impact": 0.22,
                    "team_injury_impact": 0.22,
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Beta Star",
                    "team": "MIA",
                    "opponent": "ORL",
                    "selection": "UNDER",
                    "sportsbook_line": 26.5,
                    "model_projection": 22.8,
                    "edge": -3.7,
                    "edge_abs": 3.7,
                    "confidence": 0.76,
                    "odds": -112,
                    "minutes_avg": 34.0,
                    "minutes_recent": 34.6,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Gamma Star",
                    "team": "DEN",
                    "opponent": "OKC",
                    "selection": "OVER",
                    "sportsbook_line": 23.5,
                    "model_projection": 26.8,
                    "edge": 3.3,
                    "edge_abs": 3.3,
                    "confidence": 0.74,
                    "odds": -110,
                    "minutes_avg": 35.6,
                    "minutes_recent": 36.2,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Rescue Wing",
                    "team": "TOR",
                    "opponent": "NYK",
                    "selection": "OVER",
                    "sportsbook_line": 17.5,
                    "model_projection": 20.1,
                    "edge": 2.6,
                    "edge_abs": 2.6,
                    "confidence": 0.60,
                    "odds": -112,
                    "minutes_avg": 32.5,
                    "minutes_recent": 33.0,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "live_quality_rescue",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_assists",
                    "entity_name": "Rescue Creator",
                    "team": "ATL",
                    "opponent": "CLE",
                    "selection": "OVER",
                    "sportsbook_line": 5.5,
                    "model_projection": 7.7,
                    "edge": 2.2,
                    "edge_abs": 2.2,
                    "confidence": 0.60,
                    "odds": -118,
                    "minutes_avg": 33.0,
                    "minutes_recent": 33.4,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "live_quality_rescue",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Partial Fill",
                    "team": "LAC",
                    "opponent": "POR",
                    "selection": "OVER",
                    "sportsbook_line": 18.5,
                    "model_projection": 21.0,
                    "edge": 2.5,
                    "edge_abs": 2.5,
                    "confidence": 0.63,
                    "odds": -110,
                    "minutes_avg": 32.0,
                    "minutes_recent": 32.4,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "partial_market_fill",
                    "qualification_gate_mode": "live_quality_rescue",
                },
            ]
        )
    )

    elite, full_market, trace = ai._build_final_operator_boards(prepared, per_market_limit=20)

    assert "Partial Fill" not in elite["entity_name"].tolist()
    assert "Partial Fill" not in full_market["entity_name"].tolist()
    assert trace["elite"]["input_live_candidates"]["count"] == 5
    assert trace["elite"]["candidate_count_before_final_board_build"] == 5
    assert trace["elite"]["core_pass_candidate_count"] == 3
    assert trace["elite"]["live_quality_rescue_candidate_count"] == 2
    assert trace["elite"]["backfill_added_count"] >= 1
    assert "final_selection_source_lane" in elite.columns
    assert set(elite["final_selection_source_lane"].astype(str).tolist()).issubset({"core_pass", "live_quality_rescue_pass"})
    assert any(
        item["key"] == "live_quality_rescue"
        for item in trace["elite"]["backfill_added_by_qualification_gate_mode"]
    )
    assert any(
        item["key"] == "live_quality_rescue_pass"
        for item in trace["elite"]["final_selected_by_source_lane"]
    )
    admission_rows = pd.DataFrame(trace["elite"]["player_points_elite_admission_rows"])
    assert not admission_rows.empty
    assert "final_exclusion_stage" in admission_rows.columns
    assert "selection_score" in admission_rows.columns
    assert "rank_position_overall" in admission_rows.columns
    assert set(admission_rows["market"].astype(str).tolist()) == {"player_points"}
    assert elite[elite["market_type"].astype(str) != "player_points"].shape[0] >= 1


def test_under_bias_and_realism_penalty_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    over_row = ai._apply_scoring_metadata(
        {
            "prediction_date": "2026-04-10",
            "market_type": "player_points",
            "entity_name": "Alpha Star",
            "team": "BOS",
            "opponent": "NYK",
            "selection": "OVER",
            "sportsbook_line": 24.5,
            "edge": 3.5,
            "edge_abs": 3.5,
            "confidence": 0.72,
            "odds": -115,
            "minutes_avg": 35.0,
            "minutes_recent": 35.5,
        }
    )
    under_row = ai._apply_scoring_metadata(
        {
            "prediction_date": "2026-04-10",
            "market_type": "player_points",
            "entity_name": "Alpha Star",
            "team": "BOS",
            "opponent": "NYK",
            "selection": "UNDER",
            "sportsbook_line": 24.5,
            "edge": -3.5,
            "edge_abs": 3.5,
            "confidence": 0.72,
            "odds": -115,
            "minutes_avg": 35.0,
            "minutes_recent": 35.5,
        }
    )
    unstable_spike = ai._apply_scoring_metadata(
        {
            "prediction_date": "2026-04-10",
            "market_type": "player_points",
            "entity_name": "Spike Player",
            "team": "BOS",
            "opponent": "NYK",
            "selection": "OVER",
            "sportsbook_line": 18.5,
            "edge": 7.2,
            "edge_abs": 7.2,
            "confidence": 0.58,
            "odds": -110,
            "minutes_avg": 29.0,
            "minutes_recent": 30.0,
        }
    )

    assert under_row["under_bias_multiplier"] == 0.95
    assert under_row["quality_score"] < over_row["quality_score"]
    assert unstable_spike["projection_realism_penalty"] > 0.0


def test_player_points_admission_audit_does_not_force_elite_points_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    prepared = ai._prepare_selected_board(
        pd.DataFrame(
            [
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Blocked Role Under",
                    "team": "UTA",
                    "opponent": "MEM",
                    "selection": "UNDER",
                    "sportsbook_line": 13.5,
                    "model_projection": 8.1,
                    "edge": -5.4,
                    "edge_abs": 5.4,
                    "confidence": 0.70,
                    "odds": -116,
                    "minutes_avg": 31.0,
                    "minutes_recent": 30.4,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_assists",
                    "entity_name": "Assist Hub",
                    "team": "BOS",
                    "opponent": "NYK",
                    "selection": "OVER",
                    "sportsbook_line": 6.5,
                    "model_projection": 8.8,
                    "edge": 2.3,
                    "edge_abs": 2.3,
                    "confidence": 0.74,
                    "odds": -114,
                    "minutes_avg": 35.0,
                    "minutes_recent": 35.2,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                },
            ]
        )
    )

    elite_df, _, trace = ai._build_final_operator_boards(prepared)
    admission_rows = pd.DataFrame(trace["elite"]["player_points_elite_admission_rows"])

    assert elite_df["market_type"].tolist() == ["player_assists"]
    assert len(elite_df) == 1
    assert admission_rows["final_exclusion_stage"].tolist() == ["failed_hard_guard"]


def test_market_name_matching_golden() -> None:
    odds = pd.DataFrame(
        [
            {
                "player_id": None,
                "player_name": "Nikola Jokic Jr.",
                "team": "DEN",
                "market_type": "player_points",
                "line": 28.5,
            }
        ]
    )

    matched = filter_player_markets(
        game_odds=odds,
        player_name="Nikola Jokic",
        team_abbr="DEN",
    )

    assert len(matched) == 1
    assert matched.iloc[0]["player_name"] == "Nikola Jokic Jr."


def test_live_market_only_legacy_blank_flags_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    rows = pd.DataFrame(
        [
            {
                "prediction_date": "2026-04-09",
                "market_type": "player_points",
                "entity_name": "Legacy Star",
                "team": "BOS",
                "opponent": "NYK",
                "selection": "UNDER",
                "sportsbook_line": 29.5,
                "quality_score": 98.0,
                "is_live_market": "",
                "synthetic_line": "",
                "line_source": "",
            }
        ]
    )

    filtered = ai._live_market_only(rows)

    assert len(filtered) == 1
    assert filtered.iloc[0]["entity_name"] == "Legacy Star"


def test_minutes_gate_soft_penalty_golden() -> None:
    policy = PlayerSelectionPolicy()
    result = policy.evaluate_minutes_gate(
        minutes_avg=19.8,
        minutes_recent=18.9,
        min_threshold=22.0,
        threshold_overrides={"edge_multiplier": 1.0, "confidence_delta": 0.0},
    )

    assert result["mode"] == "soft_penalty"
    assert result["minutes_shortfall"] > 0.0
    assert result["threshold_overrides"]["edge_multiplier"] > 1.0
    assert result["threshold_overrides"]["confidence_delta"] > 0.0


def test_runtime_client_401_message_golden() -> None:
    client = courtvision_ai.BallDontLieClient(api_key="test-key")

    class FakeResponse:
        status_code = 401
        url = "https://api.balldontlie.io/v2/odds?dates%5B%5D=2026-04-10"
        text = '{"message":"unauthorized"}'

    client.session.get = lambda *args, **kwargs: FakeResponse()  # type: ignore[assignment]

    with pytest.raises(RuntimeError) as exc:
        client._get("https://api.balldontlie.io/v2/odds", {"dates[]": "2026-04-10"})

    message = str(exc.value)
    assert "status=401" in message
    assert "url=https://api.balldontlie.io/v2/odds" in message
    assert "has_auth=True" in message
    assert "unauthorized" in message


def test_get_odds_player_prop_trace_and_shape_golden(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = courtvision_ai.BallDontLieClient(api_key="test-key")
    monkeypatch.setattr(client, "_build_player_prop_identity_lookup", lambda *args, **kwargs: {})
    normalize_calls: list[dict[str, object]] = []
    original_normalize = client._normalize_player_prop_row

    def wrapped_normalize(market: object, *args: object, **kwargs: object) -> dict[str, object] | None:
        if isinstance(market, dict):
            normalize_calls.append(dict(market))
        return original_normalize(market, *args, **kwargs)

    monkeypatch.setattr(client, "_normalize_player_prop_row", wrapped_normalize)

    def fake_get(url: str, params: dict[str, object] | None = None) -> dict[str, object]:
        if url == f"{courtvision_ai.NBA_V2}/odds":
            return {
                "data": [
                    {
                        "game_id": 77,
                        "vendor": "fanduel",
                        "moneyline_home_odds": -135,
                        "moneyline_away_odds": 114,
                    }
                ]
            }
        if url == f"{courtvision_ai.NBA_V2}/odds/player_props":
            assert params == {"game_id": 77}
            return {
                "data": [
                    {
                        "game_id": 77,
                        "vendor": "fanduel",
                        "prop_type": "points",
                        "player_id": 12,
                        "player": {"id": 12, "first_name": "Alpha", "last_name": "Star"},
                        "team": {"abbreviation": "BOS"},
                        "line_value": 24.5,
                        "market": {
                            "type": "over_under",
                            "over_odds": -118,
                            "under_odds": -104,
                            "odds": -118,
                        },
                    }
                ]
            }
        raise AssertionError(f"unexpected url={url} params={params}")

    monkeypatch.setattr(client, "_get", fake_get)

    with caplog.at_level(logging.INFO, logger="courtvision_ai"):
        odds = client.get_odds("2026-04-12", game_ids=[77])

    assert len(normalize_calls) == 1
    assert len(odds) == 3
    assert "player_name" in odds.columns
    player_props = odds[odds["raw_market_name"] == "player_points"].copy()
    assert len(player_props) == 1
    assert player_props.iloc[0]["player_name"] == "Alpha Star"

    messages = [record.getMessage() for record in caplog.records if record.name == "courtvision_ai"]
    assert any("get_odds_player_props_raw game_id=77 raw_rows=1" in message for message in messages)
    assert any("get_odds_player_props_normalized game_id=77 normalize_called=1 normalized_rows=1" in message for message in messages)
    assert any("get_odds_final_frame rows=3" in message for message in messages)
    assert any("get_odds_stage_counts" in message for message in messages)


def test_get_odds_player_prop_failure_logs_actionable_details_golden(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = courtvision_ai.BallDontLieClient(api_key="test-key")
    monkeypatch.setattr(client, "_build_player_prop_identity_lookup", lambda *args, **kwargs: {})

    def fake_get(url: str, params: dict[str, object] | None = None) -> dict[str, object]:
        if url == f"{courtvision_ai.NBA_V2}/odds":
            return {
                "data": [
                    {
                        "game_id": 88,
                        "vendor": "fanduel",
                        "moneyline_home_odds": -120,
                        "moneyline_away_odds": 102,
                    }
                ]
            }
        if url == f"{courtvision_ai.NBA_V2}/odds/player_props":
            assert params == {"game_id": 88}
            return {
                "data": [
                    {
                        "game_id": 88,
                        "vendor": "fanduel",
                        "prop_type": "points",
                        "player_id": 44,
                        "player": {},
                        "team": {"abbreviation": "BOS"},
                        "line_value": 13.5,
                        "market": {
                            "type": "over_under",
                            "over_odds": -110,
                            "under_odds": -110,
                            "odds": -110,
                        },
                    }
                ]
            }
        raise AssertionError(f"unexpected url={url} params={params}")

    monkeypatch.setattr(client, "_get", fake_get)

    with caplog.at_level(logging.INFO, logger="courtvision_ai"):
        with pytest.raises(AssertionError) as exc:
            client.get_odds("2026-04-12", game_ids=[88])

    message = str(exc.value)
    assert "null_count=1" in message
    assert "total_prop_rows=1" in message
    assert "normalized_rows_had_names=False" in message
    assert "stage_hint=normalize_player_prop_row returned blank player_name values" in message

    messages = [record.getMessage() for record in caplog.records if record.name == "courtvision_ai"]
    assert any("get_odds_player_props_raw_sample game_id=88 index=0" in msg for msg in messages)
    assert any("get_odds_player_props_normalized_samples game_id=88 normalized_named=0" in msg for msg in messages)
    assert any("get_odds_player_name_failure_counts raw_game_odds_rows=1 raw_player_prop_rows=1 normalized_player_prop_rows=1" in msg for msg in messages)
    assert any("get_odds_player_name_failure_raw_player_prop_samples=" in msg for msg in messages)
    assert any("get_odds_player_name_failure_normalized_player_prop_samples=" in msg for msg in messages)
    assert any("get_odds_player_name_failure_final_player_prop_samples=" in msg for msg in messages)


def test_get_odds_player_id_lookup_recovers_names_golden(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = courtvision_ai.BallDontLieClient(api_key="test-key")
    monkeypatch.setattr(
        client,
        "_build_player_prop_identity_lookup",
        lambda *args, **kwargs: {
            12: {"player_name": "Alpha Star", "team_abbr": "BOS"},
        },
    )

    def fake_get(url: str, params: dict[str, object] | None = None) -> dict[str, object]:
        if url == f"{courtvision_ai.NBA_V2}/odds":
            return {
                "data": [
                    {
                        "game_id": 77,
                        "vendor": "fanduel",
                        "moneyline_home_odds": -135,
                        "moneyline_away_odds": 114,
                    }
                ]
            }
        if url == f"{courtvision_ai.NBA_V2}/odds/player_props":
            assert params == {"game_id": 77}
            return {
                "data": [
                    {
                        "game_id": 77,
                        "vendor": "fanduel",
                        "prop_type": "points",
                        "player_id": 12,
                        "line_value": 24.5,
                        "market": {
                            "type": "over_under",
                            "over_odds": -118,
                            "under_odds": -104,
                            "odds": -118,
                        },
                    }
                ]
            }
        raise AssertionError(f"unexpected url={url} params={params}")

    monkeypatch.setattr(client, "_get", fake_get)

    with caplog.at_level(logging.INFO, logger="courtvision_ai"):
        odds = client.get_odds("2026-04-12", game_ids=[77])

    player_props = odds[odds["raw_market_name"] == "player_points"].copy()
    assert len(player_props) == 1
    assert player_props.iloc[0]["player_name"] == "Alpha Star"
    assert player_props.iloc[0]["team"] == "BOS"

    messages = [record.getMessage() for record in caplog.records if record.name == "courtvision_ai"]
    assert any("normalized_named=1" in message for message in messages)


def test_env_key_debug_details_golden(monkeypatch: pytest.MonkeyPatch) -> None:
    original_audit = dict(courtvision_ai._ENV_SOURCE_AUDIT)
    monkeypatch.setenv("BALLDONTLIE_API_KEY", "process-key-1234")
    courtvision_ai._ENV_SOURCE_AUDIT["BALLDONTLIE_API_KEY"] = {
        "source": "process_env",
        "dotenv_present": True,
        "dotenv_matches_process": False,
        "dotenv_path": "C:\\dev\\Sport_Project1\\.env",
        "dotenv_fingerprint": "len=15 last4=9999",
    }

    try:
        details = courtvision_ai._env_key_debug_details("BALLDONTLIE_API_KEY")
    finally:
        courtvision_ai._ENV_SOURCE_AUDIT.clear()
        courtvision_ai._ENV_SOURCE_AUDIT.update(original_audit)

    assert courtvision_ai._safe_key_fingerprint("process-key-1234") == "len=16 last4=1234"
    assert details["source"] == "process_env_dotenv_mismatch"
    assert details["fingerprint"] == "len=16 last4=1234"
    assert details["dotenv_ignored"] is True
    assert details["dotenv_fingerprint"] == "len=15 last4=9999"


def test_grading_contract_golden(workspace_tmp: Path) -> None:
    output_dir = workspace_tmp / "outputs"
    history_dir = output_dir / "runtime" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    picks_path = history_dir / "picks_2026-04-10.csv"

    with picks_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "prediction_date",
                "rank",
                "game_id",
                "player_id",
                "player_name",
                "team_abbreviation",
                "opponent_abbreviation",
                "vendor",
                "prop_type",
                "side",
                "line_value",
                "projection",
                "edge",
                "confidence",
                "exposure_score",
                "fair_probability",
                "offered_odds",
                "kelly_fraction",
                "score",
                "notes",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "prediction_date": "2026-04-10",
                "rank": "1",
                "game_id": "99",
                "player_id": "7",
                "player_name": "Alpha Star",
                "team_abbreviation": "BOS",
                "opponent_abbreviation": "NYK",
                "vendor": "test-book",
                "prop_type": "points",
                "side": "over",
                "line_value": "25.5",
                "projection": "28.0",
                "edge": "2.5",
                "confidence": "Strong",
                "exposure_score": "0.82",
                "fair_probability": "0.6100",
                "offered_odds": "-115",
                "kelly_fraction": "0.0210",
                "score": "77.2000",
                "notes": "golden-case",
            }
        )

    grader = PickGrader(settings=Settings(api_key="test"), output_dir=str(output_dir))

    class FakeClient:
        def get_stats_for_player_ids_on_date(
            self,
            player_ids: list[int],
            prediction_date: str,
            season: int,
        ) -> list[PlayerGameStats]:
            assert player_ids == [7]
            assert prediction_date == "2026-04-10"
            assert season == 2025
            return [
                PlayerGameStats(
                    player_id=7,
                    player_name="Alpha Star",
                    team_id=1,
                    team_abbreviation="BOS",
                    game_id=99,
                    game_date="2026-04-10",
                    minutes=36.0,
                    points=28.0,
                    rebounds=8.0,
                    assists=6.0,
                    threes=4.0,
                    steals=1.0,
                    blocks=1.0,
                )
            ]

    grader.client = FakeClient()
    graded, summary = grader.grade_date("2026-04-10")

    assert len(graded) == 1
    assert isinstance(graded[0], GradedProp)
    assert graded[0].result == "win"
    assert summary[0]["bucket"] == "overall"
    assert summary[0]["total"] == "1"


def test_grading_package_export_golden() -> None:
    assert ExportedPickGrader is PickGrader


def test_grading_rebuilds_pick_history_from_operator_board_golden(workspace_tmp: Path) -> None:
    output_dir = workspace_tmp / "outputs"
    operator_dir = output_dir / "runtime" / "operator"
    history_dir = output_dir / "runtime" / "history"
    model_dir = output_dir / "model"
    operator_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)

    with (operator_dir / "elite_board_2026-04-10.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "prediction_date",
                "market_type",
                "entity_name",
                "team",
                "opponent",
                "selection",
                "sportsbook_line",
                "model_projection",
                "edge",
                "edge_abs",
                "confidence",
                "odds",
                "bookmaker",
                "quality_score",
                "confidence_band",
                "qualification_reason",
                "selection_injury_notes",
                "is_live_market",
                "line_source",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "prediction_date": "2026-04-10",
                "market_type": "player_points",
                "entity_name": "Alpha Star",
                "team": "BOS",
                "opponent": "NYK",
                "selection": "OVER",
                "sportsbook_line": "25.5",
                "model_projection": "28.0",
                "edge": "2.5",
                "edge_abs": "2.5",
                "confidence": "0.66",
                "odds": "-115",
                "bookmaker": "test-book",
                "quality_score": "77.2",
                "confidence_band": "high",
                "qualification_reason": "player_stable_minutes_pass",
                "selection_injury_notes": "opp_relief:0.12",
                "is_live_market": "True",
                "line_source": "live_market",
            }
        )

    with (model_dir / "player_baselines.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["player_id", "player_name", "team_abbr", "player_key"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "player_id": "7",
                "player_name": "Alpha Star",
                "team_abbr": "BOS",
                "player_key": "alpha star__BOS",
            }
        )

    grader = PickGrader(settings=Settings(api_key="test"), output_dir=str(output_dir))

    class FakeClient:
        def get_stats_for_player_ids_on_date(
            self,
            player_ids: list[int],
            prediction_date: str,
            season: int,
        ) -> list[PlayerGameStats]:
            assert player_ids == [7]
            assert prediction_date == "2026-04-10"
            assert season == 2025
            return [
                PlayerGameStats(
                    player_id=7,
                    player_name="Alpha Star",
                    team_id=1,
                    team_abbreviation="BOS",
                    game_id=99,
                    game_date="2026-04-10",
                    minutes=36.0,
                    points=28.0,
                    rebounds=8.0,
                    assists=6.0,
                    threes=4.0,
                    steals=1.0,
                    blocks=1.0,
                )
            ]

    grader.client = FakeClient()
    graded, _summary = grader.grade_date("2026-04-10")

    rebuilt_rows = list(csv.DictReader((history_dir / "picks_2026-04-10.csv").open("r", encoding="utf-8")))

    assert len(rebuilt_rows) == 1
    assert rebuilt_rows[0]["player_id"] == "7"
    assert rebuilt_rows[0]["prop_type"] == "points"
    assert graded[0].result == "win"


def test_injury_boost_cap_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    adjusted_projection, adjusted_confidence, injury_payload = ai._apply_player_injury_context(
        player_row={"player_name": "Alpha Star", "min_avg": 34.0},
        team_abbr="BOS",
        opp_abbr="LAL",
        market_type="player_points",
        projection=20.0,
        confidence=0.70,
        injury_context={
            "players": {},
            "teams": {
                "BOS": {
                    "impact_score": 0.75,
                    "usage_boost": 0.45,
                    "rebound_boost": 0.20,
                    "defensive_event_boost": 0.15,
                },
                "LAL": {
                    "impact_score": 0.55,
                    "defense_penalty": 0.18,
                    "rebound_boost": 0.10,
                    "rim_penalty": 0.08,
                },
            },
        },
    )

    assert adjusted_projection <= 24.0 + 1e-9
    assert adjusted_confidence > 0.70
    assert "injury_boost_capped" in injury_payload["injury_notes"]


def test_player_points_injury_double_count_protection_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    adjusted_projection, adjusted_confidence, injury_payload = ai._apply_player_injury_context(
        player_row={
            "player_name": "Role Wing",
            "min_avg": 27.0,
            "pts_avg": 12.8,
            "pts_recent": 10.9,
        },
        team_abbr="POR",
        opp_abbr="LAC",
        market_type="player_points",
        projection=13.4,
        confidence=0.62,
        injury_context={
            "players": {},
            "teams": {
                "POR": {
                    "impact_score": 0.49,
                    "usage_boost": 0.18,
                },
                "LAC": {
                    "impact_score": 0.18,
                    "defense_penalty": 0.12,
                },
            },
        },
    )

    assert adjusted_projection > 13.4
    assert adjusted_confidence < 0.65
    assert injury_payload["injury_projection_delta"] > 0.0
    assert injury_payload["injury_confidence_delta"] <= ai.PLAYER_POINTS_MAX_CONFIDENCE_INJURY_UPLIFT
    assert injury_payload["player_points_confidence_uplift_dampened"] is True
    assert injury_payload["player_points_confidence_uplift_reason"] == "projection_injury_uplift_already_applied"


def test_player_points_realism_dampener_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    projection, confidence, payload = ai._apply_player_points_realism_dampener(
        player_row={
            "player_name": "Role Wing",
            "min_avg": 28.0,
            "min_recent": 27.4,
        },
        sportsbook_line=13.5,
        selection="Over",
        projection=15.6,
        confidence=0.66,
        injury_payload={
            "injury_impact_score": 0.32,
            "team_injury_impact": 0.32,
            "opponent_injury_impact": 0.10,
            "injury_projection_delta": 1.8,
            "injury_confidence_delta": 0.03,
            "player_points_recent_form_ratio": 0.84,
        },
        is_live_market=True,
    )

    assert projection < 15.6
    assert confidence < 0.66
    assert payload["player_points_realism_dampened"] is True
    assert payload["player_points_realism_dampener_reason"] == "fragile_low_line_injury_over"


def test_board_diagnostics_outputs_golden(workspace_tmp: Path, ai: courtvision_ai.CourtVisionAI) -> None:
    out_dir = workspace_tmp / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    qualified_pool_df = ai._prepare_selected_board(
        pd.DataFrame(
            [
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Alpha Star",
                    "team": "BOS",
                    "opponent": "NYK",
                    "selection": "OVER",
                    "sportsbook_line": 24.5,
                    "model_projection": 28.2,
                    "edge": 3.7,
                    "edge_abs": 3.7,
                    "confidence": 0.78,
                    "odds": -115,
                    "minutes_avg": 35.0,
                    "minutes_recent": 36.0,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_assists",
                    "entity_name": "Rescue Creator",
                    "team": "ATL",
                    "opponent": "CLE",
                    "selection": "OVER",
                    "sportsbook_line": 5.5,
                    "model_projection": 7.6,
                    "edge": 2.1,
                    "edge_abs": 2.1,
                    "confidence": 0.60,
                    "odds": -118,
                    "minutes_avg": 33.0,
                    "minutes_recent": 33.4,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "live_quality_rescue",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Partial Fill",
                    "team": "LAC",
                    "opponent": "POR",
                    "selection": "OVER",
                    "sportsbook_line": 18.5,
                    "model_projection": 21.1,
                    "edge": 2.6,
                    "edge_abs": 2.6,
                    "confidence": 0.61,
                    "odds": -112,
                    "minutes_avg": 31.0,
                    "minutes_recent": 31.6,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "partial_market_fill",
                    "qualification_gate_mode": "live_quality_rescue",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Blocked Role Under",
                    "team": "UTA",
                    "opponent": "MEM",
                    "selection": "UNDER",
                    "sportsbook_line": 13.5,
                    "model_projection": 8.2,
                    "edge": -5.3,
                    "edge_abs": 5.3,
                    "confidence": 0.71,
                    "odds": -117,
                    "minutes_avg": 32.8,
                    "minutes_recent": 29.2,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                    "selection_team_injury_impact": 0.29,
                    "team_injury_impact": 0.29,
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Borderline Secondary Under",
                    "team": "MIA",
                    "opponent": "BOS",
                    "selection": "UNDER",
                    "sportsbook_line": 28.5,
                    "model_projection": 25.1,
                    "edge": -3.4,
                    "edge_abs": 3.4,
                    "confidence": 0.74,
                    "odds": -112,
                    "minutes_avg": 30.2,
                    "minutes_recent": 30.8,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                },
            ]
        )
    )
    elite_df, full_market_df, final_board_construction = ai._build_final_operator_boards(qualified_pool_df)
    rejected_df = pd.DataFrame(
        [
            ai._qualify_or_reject(
                market_type="moneyline",
                entity_name="Charlotte Hornets",
                team="CHA",
                opponent="MIL",
                sportsbook_line=0.0,
                model_projection=0.34,
                edge=0.18,
                confidence=0.73,
                selection="ML",
                prediction_date="2026-04-10",
                odds=650,
            )["row"],
            ai._qualify_or_reject(
                market_type="player_points",
                entity_name="Delta Wing",
                team="MIA",
                opponent="BOS",
                sportsbook_line=20.5,
                model_projection=18.9,
                edge=-1.6,
                confidence=0.57,
                selection="UNDER",
                prediction_date="2026-04-10",
                odds=-112,
            )["row"]
        ]
    )
    board_diagnostics = ai._build_board_diagnostics(
        prediction_date="2026-04-10",
        qualified_pool_df=qualified_pool_df,
        elite_df=elite_df,
        full_market_df=full_market_df,
        rejected_df=rejected_df,
        final_board_construction=final_board_construction,
    )
    grading_results = pd.DataFrame(
        [
            {
                "market_type": "player_points",
                "quality_band": "high",
                "confidence_band": "high",
                "odds_bucket": "near_even",
                "minutes_bucket": "28_34",
                "graded_result": "win",
                "is_win": 1,
                "is_push": 0,
                "is_loss": 0,
                "hit": 1,
                "model_projection": 28.2,
                "actual_value": 30.0,
            }
        ]
    )

    paths = courtvision_ai._write_cli_outputs(
        out_dir=out_dir,
        prediction_date="2026-04-10",
        fit_metrics=None,
        prediction_outputs={
            "selected_props": elite_df,
            "elite_props": elite_df,
            "qualified_pool_props": qualified_pool_df,
            "full_market_props": full_market_df,
            "rejected_props": rejected_df,
            "grading_results": grading_results,
            "board_diagnostics": board_diagnostics,
            "final_board_construction": final_board_construction,
            "summary": {"prediction_date": "2026-04-10", "final_board_construction": final_board_construction},
        },
        verbose_outputs=False,
    )

    diagnostics_json = json.loads(paths["board_diagnostics_json"].read_text(encoding="utf-8"))
    elite_board_csv = pd.read_csv(paths["elite_board"])
    points_admission_csv = pd.read_csv(paths["player_points_elite_admission_csv"])
    points_admission_json = json.loads(paths["player_points_elite_admission_json"].read_text(encoding="utf-8"))
    grading_summary_json = json.loads(paths["grading_summary_json"].read_text(encoding="utf-8"))
    points_calibration_json = json.loads(paths["player_points_calibration_json"].read_text(encoding="utf-8"))
    report_text = paths["top_plays_report"].read_text(encoding="utf-8")

    assert paths["elite_board"].parent.name == "operator"
    assert paths["elite_board"].parent.parent.name == "runtime"
    assert paths["full_market_board"].parent.name == "operator"
    assert paths["sgp_board"].parent.name == "operator"
    assert paths["top_plays_report"].parent.name == "operator"
    assert paths["board_diagnostics_json"].parent.name == "diagnostics"
    assert paths["player_points_elite_admission_csv"].parent.name == "diagnostics"
    assert paths["player_points_elite_admission_json"].parent.name == "diagnostics"
    assert paths["grading_summary_json"].parent.name == "diagnostics"
    assert paths["player_points_calibration_json"].parent.name == "diagnostics"
    assert paths["player_predictions"].parent.name == "research"
    assert paths["grading_results"].parent.name == "research"
    assert "board_diagnostics_csv" not in paths
    assert "grading_summary_csv" not in paths
    assert "stat_only_board" not in paths
    assert "strike_board" not in paths
    assert "predictive_lines_board" not in paths
    assert "near_miss_board" not in paths
    assert "team_board" not in paths
    assert diagnostics_json["board_counts"]["elite"] == len(elite_df)
    assert any(item["key"] == "quality_below_threshold" for item in diagnostics_json["rejected_by_reason"])
    assert any(item["key"] == "live_quality_rescue_pass" for item in diagnostics_json["qualified_by_reason"])
    assert "count_by_side" in diagnostics_json["qualified_pool"]
    assert any(item["key"] == "over" for item in diagnostics_json["qualified_pool"]["count_by_side"])
    assert any(item["key"] == "under" for item in diagnostics_json["qualification_rate_by_side"])
    assert "count_by_qualification_gate_mode" in diagnostics_json["qualified_pool"]
    assert any(item["key"] == "live_quality_rescue" for item in diagnostics_json["qualified_pool"]["count_by_qualification_gate_mode"])
    assert "count_by_final_selection_source_lane" in diagnostics_json["elite_board"]
    assert "count_by_player_profile_bucket" in diagnostics_json["qualified_pool"]
    assert "count_by_player_points_line_band" in diagnostics_json["qualified_pool"]
    assert "count_by_injury_influence_bucket" in diagnostics_json["qualified_pool"]
    assert "count_by_blocked_by_elite_points_risk_guard" in diagnostics_json["qualified_pool"]
    assert "count_by_elite_points_risk_guard_reason" in diagnostics_json["qualified_pool"]
    assert "count_by_elite_points_ranking_reason" in diagnostics_json["qualified_pool"]
    assert "count_by_player_points_elite_outcome" in diagnostics_json["qualified_pool"]
    assert "count_by_player_points_realism_dampener_reason" in diagnostics_json["qualified_pool"]
    assert "avg_injury_projection_delta_by_side" in diagnostics_json["qualified_pool"]
    assert "avg_injury_confidence_delta_by_side" in diagnostics_json["qualified_pool"]
    assert "player_points_guard" in diagnostics_json
    assert "player_points_realism" in diagnostics_json
    assert "player_points_elite_admission" in diagnostics_json
    assert any(
        item["key"] == "weak_role_under"
        for item in diagnostics_json["player_points_guard"]["blocked_from_elite_candidate_pool"]["count_by_elite_points_risk_guard_reason"]
    )
    assert any(
        item["key"] == "borderline_secondary_under"
        for item in diagnostics_json["player_points_guard"]["qualified_pool"]["count_by_elite_points_ranking_reason"]
    )
    assert "final_board_construction" in diagnostics_json
    assert diagnostics_json["final_board_construction"]["elite"]["input_live_candidates"]["count"] >= 2
    assert diagnostics_json["final_board_construction"]["elite"]["candidate_count_before_final_board_build"] >= 2
    assert diagnostics_json["final_board_construction"]["elite"]["live_quality_rescue_candidate_count"] >= 1
    assert diagnostics_json["final_board_construction"]["elite"]["backfill_added_count"] >= 0
    assert diagnostics_json["player_points_elite_admission"]["player_points_post_realism_count"] >= 1
    assert diagnostics_json["player_points_elite_admission"]["player_points_elite_guard_pass_count"] >= 1
    assert any(
        item["key"] == "failed_hard_guard"
        for item in diagnostics_json["player_points_elite_admission"]["exclusion_buckets"]
    )
    assert "qualification_reason" in elite_board_csv.columns
    assert "market_trust_weight" in elite_board_csv.columns
    assert "qualification_gate_mode" in elite_board_csv.columns
    assert "final_selection_source_lane" in elite_board_csv.columns
    assert "blocked_by_elite_points_risk_guard" in elite_board_csv.columns
    assert "elite_points_risk_guard_reason" in elite_board_csv.columns
    assert "player_points_elite_outcome" in elite_board_csv.columns
    assert set(points_admission_csv["market"].astype(str).tolist()) == {"player_points"}
    assert "final_exclusion_stage" in points_admission_csv.columns
    assert "lost_to_non_points_candidate" in points_admission_csv.columns
    assert "lost_to_same_player_exposure" in points_admission_csv.columns
    assert "lost_to_board_cap" in points_admission_csv.columns
    assert "lost_to_rescue_priority" in points_admission_csv.columns
    assert len(points_admission_json["rows"]) == len(points_admission_csv)
    assert not any(str(value).lower() == "nan" for value in points_admission_csv["elite_guard_fail_reason"].fillna("").tolist())
    assert "Final Board Construction" in report_text
    assert "rescue=" in report_text
    assert "elite:" in report_text
    assert grading_summary_json["overall"]["n"] == 1
    assert "player_points_calibration" in points_calibration_json
    assert "overall_points" in points_calibration_json["player_points_calibration"]
    assert "player_points_uplift_audit" in points_calibration_json


def test_verbose_outputs_include_optional_lane_golden(workspace_tmp: Path, ai: courtvision_ai.CourtVisionAI) -> None:
    out_dir = workspace_tmp / "outputs_verbose"
    out_dir.mkdir(parents=True, exist_ok=True)

    qualified_pool_df = ai._prepare_selected_board(
        pd.DataFrame(
            [
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Alpha Star",
                    "team": "BOS",
                    "opponent": "NYK",
                    "selection": "OVER",
                    "sportsbook_line": 24.5,
                    "model_projection": 28.2,
                    "edge": 3.7,
                    "edge_abs": 3.7,
                    "confidence": 0.78,
                    "odds": -115,
                    "minutes_avg": 35.0,
                    "minutes_recent": 36.0,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_assists",
                    "entity_name": "Rescue Creator",
                    "team": "ATL",
                    "opponent": "CLE",
                    "selection": "OVER",
                    "sportsbook_line": 5.5,
                    "model_projection": 7.6,
                    "edge": 2.1,
                    "edge_abs": 2.1,
                    "confidence": 0.60,
                    "odds": -118,
                    "minutes_avg": 33.0,
                    "minutes_recent": 33.4,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "live_quality_rescue",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Borderline Secondary Under",
                    "team": "MIA",
                    "opponent": "BOS",
                    "selection": "UNDER",
                    "sportsbook_line": 28.5,
                    "model_projection": 25.1,
                    "edge": -3.4,
                    "edge_abs": 3.4,
                    "confidence": 0.74,
                    "odds": -112,
                    "minutes_avg": 30.2,
                    "minutes_recent": 30.8,
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                    "qualification_gate_mode": "core_pass",
                }
            ]
        )
    )
    elite_df, full_market_df, final_board_construction = ai._build_final_operator_boards(qualified_pool_df)

    paths = courtvision_ai._write_cli_outputs(
        out_dir=out_dir,
        prediction_date="2026-04-10",
        fit_metrics=None,
        prediction_outputs={
            "selected_props": elite_df,
            "elite_props": elite_df,
            "qualified_pool_props": qualified_pool_df,
            "full_market_props": full_market_df,
            "stat_only_props": qualified_pool_df,
            "team_board_props": pd.DataFrame(),
            "strike_props": qualified_pool_df,
            "predictive_lines_props": qualified_pool_df,
            "sgp_props": pd.DataFrame(),
            "near_miss_props": qualified_pool_df,
            "rejected_props": pd.DataFrame(),
            "final_board_construction": final_board_construction,
            "grading_results": pd.DataFrame(
                [
                    {
                        "market_type": "player_points",
                        "quality_band": "high",
                        "confidence_band": "high",
                        "odds_bucket": "near_even",
                        "minutes_bucket": "28_34",
                        "graded_result": "win",
                        "is_win": 1,
                        "is_push": 0,
                        "is_loss": 0,
                        "hit": 1,
                        "model_projection": 28.2,
                        "actual_value": 30.0,
                    }
                ]
            ),
            "summary": {"prediction_date": "2026-04-10", "final_board_construction": final_board_construction},
        },
        verbose_outputs=True,
    )

    diagnostics_csv = pd.read_csv(paths["board_diagnostics_csv"])
    grading_summary_csv = pd.read_csv(paths["grading_summary_csv"])

    assert paths["board_diagnostics_csv"].parent.name == "optional"
    assert paths["grading_summary_csv"].parent.name == "optional"
    assert paths["stat_only_board"].parent.name == "optional"
    assert paths["strike_board"].parent.name == "optional"
    assert paths["predictive_lines_board"].parent.name == "optional"
    assert paths["near_miss_board"].parent.name == "optional"
    assert "qualified_by_reason" in diagnostics_csv["section"].tolist()
    assert "final_board_construction" in diagnostics_csv["section"].tolist()
    assert "backfill_added_by_qualification_gate_mode" in diagnostics_csv["section"].tolist()
    assert "final_selected_by_source_lane" in diagnostics_csv["section"].tolist()
    assert "count_by_player_profile_bucket" in diagnostics_csv["section"].tolist()
    assert "count_by_blocked_by_elite_points_risk_guard" in diagnostics_csv["section"].tolist()
    assert "count_by_player_points_line_band" in diagnostics_csv["section"].tolist()
    assert "count_by_elite_points_ranking_reason" in diagnostics_csv["section"].tolist()
    assert "count_by_player_points_elite_outcome" in diagnostics_csv["section"].tolist()
    assert "player_points_elite_admission" in diagnostics_csv["section"].tolist()
    assert "player_points_elite_admission.exclusion_buckets" in diagnostics_csv["section"].tolist()
    assert "by_prop_type" in grading_summary_csv["section"].tolist()


def test_sgp_live_only_and_report_label_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    candidate_frame = ai._sgp_candidate_frame(
        pd.DataFrame(
            [
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_points",
                    "entity_name": "Alpha Star",
                    "team": "BOS",
                    "opponent": "NYK",
                    "selection": "OVER",
                    "sportsbook_line": 24.5,
                    "edge": 3.7,
                    "edge_abs": 3.7,
                    "confidence": 0.78,
                    "quality_score": 110.0,
                    "bet_label": "Alpha Star OVER 24.5 POINTS",
                    "is_live_market": True,
                    "synthetic_line": False,
                    "line_source": "live_market",
                },
                {
                    "prediction_date": "2026-04-10",
                    "market_type": "player_blocks",
                    "entity_name": "Synthetic Blocker",
                    "team": "BOS",
                    "opponent": "NYK",
                    "selection": "OVER",
                    "sportsbook_line": 0.0,
                    "edge": 0.2,
                    "edge_abs": 0.2,
                    "confidence": 0.56,
                    "quality_score": 35.0,
                    "bet_label": "Synthetic Blocker OVER 0.0 BLOCKS",
                    "is_live_market": False,
                    "synthetic_line": True,
                    "line_source": "predictive_market_fill",
                },
            ]
        ),
        label="full_market",
    )

    assert candidate_frame["entity_name"].tolist() == ["Alpha Star"]

    report = courtvision_ai._build_report_text(
        prediction_date="2026-04-10",
        fit_metrics=None,
        summary={},
        elite_df=pd.DataFrame(),
        full_market_df=pd.DataFrame(),
        all_stats_df=pd.DataFrame(),
        team_board_df=pd.DataFrame(),
        strike_df=pd.DataFrame(),
        predictive_lines_df=pd.DataFrame(),
        sgp_df=pd.DataFrame(
            [
                {
                    "sgp_label": "Alpha Star OVER 24.5 POINTS + Beta Star UNDER 8.5 REBOUNDS",
                    "combined_hit_probability": 0.41,
                    "combined_confidence": 0.68,
                    "estimated_american_odds": 144,
                    "leg_count": 2,
                    "sgp_quality_score": 142.5,
                }
            ]
        ),
        grading_df=pd.DataFrame(),
        near_miss_df=pd.DataFrame(),
    )

    assert "SGP Board" in report
    assert "Unknown" not in report
    assert "hit_prob=0.41" in report


def test_dead_markets_filtered_from_generation_golden(ai: courtvision_ai.CourtVisionAI) -> None:
    games = pd.DataFrame(
        [
            {
                "home_team_abbr": "BOS",
                "visitor_team_abbr": "NYK",
            }
        ]
    )
    player_baselines = pd.DataFrame(
        [
            {
                "player_name": "Alpha Star",
                "team_abbr": "BOS",
                "min_avg": 33.0,
                "min_recent": 34.0,
                "pts_avg": 24.0,
                "pts_recent": 26.0,
                "pts_std": 4.5,
                "reb_avg": 7.0,
                "reb_recent": 7.5,
                "reb_std": 2.2,
                "ast_avg": 6.0,
                "ast_recent": 6.5,
                "ast_std": 2.0,
                "fg3m_avg": 2.7,
                "fg3m_recent": 3.1,
                "fg3m_std": 1.0,
            }
        ]
    )

    predictive_rows = ai._build_missing_player_market_rows(
        prediction_date="2026-04-10",
        games=games,
        player_baselines=player_baselines,
        team_lookup={},
        league_context={},
        live_supported_markets=[],
        injury_context=None,
    )

    market_types = set(predictive_rows["market_type"].astype(str).tolist())
    assert market_types
    assert market_types.issubset(set(courtvision_ai.PRIMARY_PLAYER_MARKETS))
    assert "player_steals" not in market_types
    assert "player_blocks" not in market_types


def test_grading_summary_buckets_basic() -> None:
    rows = [
        {
            "prop_type": "player_points",
            "side": "under",
            "quality_band": "high",
            "confidence_band": "high",
            "odds_bucket": "near_even",
            "minutes_bucket": "28_34",
            "player_profile_bucket": "starter_secondary",
            "player_points_line_band": "20_to_26_5",
            "injury_influence_bucket": "low",
            "final_selection_source_lane": "core_pass",
            "blocked_by_elite_points_risk_guard": "False",
            "elite_points_risk_guard_reason": "",
            "elite_points_ranking_reason": "borderline_secondary_under",
            "result": "win",
        },
        {
            "prop_type": "player_points",
            "side": "over",
            "quality_band": "high",
            "confidence_band": "high",
            "odds_bucket": "near_even",
            "minutes_bucket": "28_34",
            "player_profile_bucket": "role_low_usage",
            "player_points_line_band": "lte_14_5",
            "injury_influence_bucket": "high",
            "final_selection_source_lane": "live_quality_rescue_pass",
            "blocked_by_elite_points_risk_guard": "True",
            "elite_points_risk_guard_reason": "injury_driven_low_line_over",
            "elite_points_ranking_reason": "injury_influenced_points_over",
            "result": "loss",
        },
    ]

    summary = summarize_graded_props(rows)
    pts = summary["by_prop_type"]["player_points"]
    joint = summary["joint_quality_confidence"]["high|high"]
    by_side = summary["by_side"]["under"]
    by_lane = summary["by_final_selection_source_lane"]["live_quality_rescue_pass"]
    by_profile = summary["by_player_profile_bucket"]["role_low_usage"]
    prop_side = summary["joint_prop_side"]["player_points|over"]
    blocked = summary["by_blocked_by_elite_points_risk_guard"]["True"]
    guard_reason = summary["by_elite_points_risk_guard_reason"]["injury_driven_low_line_over"]
    ranking_reason = summary["by_elite_points_ranking_reason"]["injury_influenced_points_over"]
    prop_side_profile = summary["joint_prop_side_profile"]["player_points|over|role_low_usage"]
    line_band_side = summary["joint_line_band_side"]["lte_14_5|over"]
    injury_bucket = summary["by_injury_influence_bucket"]["high"]

    assert summary["overall"]["n"] == 2
    assert pts["n"] == 2
    assert 0.0 < pts["win_rate"] < 1.0
    assert joint["wins"] == 1
    assert by_side["wins"] == 1
    assert by_lane["losses"] == 1
    assert by_profile["losses"] == 1
    assert prop_side["losses"] == 1
    assert blocked["losses"] == 1
    assert guard_reason["losses"] == 1
    assert ranking_reason["losses"] == 1
    assert prop_side_profile["losses"] == 1
    assert line_band_side["losses"] == 1
    assert injury_bucket["losses"] == 1


def test_elite_filter_replay_summary_golden() -> None:
    graded_rows = [
        {
            "player_name": "Alpha Star",
            "team_abbreviation": "BOS",
            "prop_type": "points",
            "side": "over",
            "line_value": 24.5,
            "result": "win",
            "player_profile_bucket": "star_high_usage",
            "final_selection_source_lane": "core_pass",
        },
        {
            "player_name": "Role Wing",
            "team_abbreviation": "POR",
            "prop_type": "points",
            "side": "under",
            "line_value": 13.5,
            "result": "loss",
            "player_profile_bucket": "role_low_usage",
            "final_selection_source_lane": "core_pass",
        },
    ]
    replay_elite_rows = [
        {
            "entity_name": "Alpha Star",
            "team": "BOS",
            "market_type": "player_points",
            "selection": "OVER",
            "sportsbook_line": 24.5,
        }
    ]

    summary = summarize_elite_filter_replay(graded_rows, replay_elite_rows)

    assert summary["counts"]["kept_elite"] == 1
    assert summary["counts"]["filtered_out"] == 1
    assert summary["kept_elite"]["overall"]["wins"] == 1
    assert summary["filtered_out"]["overall"]["losses"] == 1


def test_player_points_calibration_summary_golden() -> None:
    graded_rows = [
        {
            "player_name": "Alpha Star",
            "team_abbreviation": "BOS",
            "prop_type": "points",
            "side": "over",
            "line_value": 24.5,
            "result": "win",
            "player_profile_bucket": "star_high_usage",
            "player_points_line_band": "20_to_26_5",
            "injury_influence_bucket": "low",
            "final_selection_source_lane": "core_pass",
        },
        {
            "player_name": "Role Wing",
            "team_abbreviation": "POR",
            "prop_type": "points",
            "side": "over",
            "line_value": 13.5,
            "result": "loss",
            "player_profile_bucket": "role_low_usage",
            "player_points_line_band": "lte_14_5",
            "injury_influence_bucket": "high",
            "final_selection_source_lane": "core_pass",
        },
    ]
    replay_elite_rows = [
        {
            "entity_name": "Alpha Star",
            "team": "BOS",
            "market_type": "player_points",
            "selection": "OVER",
            "sportsbook_line": 24.5,
        }
    ]

    summary = summarize_player_points_calibration(graded_rows, replay_elite_rows)

    assert summary["overall_points"]["overall"]["n"] == 2
    assert summary["kept_points"]["overall"]["wins"] == 1
    assert summary["filtered_points"]["overall"]["losses"] == 1
    assert summary["filtered_points"]["by_player_points_line_band"]["lte_14_5"]["losses"] == 1


def test_player_points_uplift_audit_summary_golden() -> None:
    graded_rows = [
        {
            "player_name": "Alpha Star",
            "team_abbreviation": "BOS",
            "prop_type": "points",
            "side": "over",
            "line_value": 24.5,
            "result": "win",
            "player_points_line_band": "20_to_26_5",
            "injury_influence_bucket": "low",
            "injury_projection_delta": 1.4,
            "injury_confidence_delta": 0.02,
        },
        {
            "player_name": "Role Wing",
            "team_abbreviation": "POR",
            "prop_type": "points",
            "side": "over",
            "line_value": 13.5,
            "result": "loss",
            "player_points_line_band": "lte_14_5",
            "injury_influence_bucket": "high",
            "injury_projection_delta": 2.1,
            "injury_confidence_delta": 0.03,
        },
    ]
    replay_elite_rows = [
        {
            "entity_name": "Alpha Star",
            "team": "BOS",
            "market_type": "player_points",
            "selection": "OVER",
            "sportsbook_line": 24.5,
        }
    ]

    summary = summarize_player_points_uplift_audit(graded_rows, replay_elite_rows)

    assert summary["overall"]["overall"]["n"] == 2
    assert summary["overall"]["overall"]["avg_projection_delta"] > 1.0
    assert summary["kept_points"]["overall"]["wins"] == 1
    assert summary["filtered_points"]["overall"]["losses"] == 1
    assert summary["filtered_points"]["by_injury_influence_bucket"]["high"]["avg_confidence_delta"] == 0.03
