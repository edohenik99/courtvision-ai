from __future__ import annotations

import pandas as pd

from courtvision.data.candidates import score_player_markets
from courtvision.pipeline.predict_pipeline import PredictionConfig, PredictionPipeline


def test_points_only_mode_preserves_behavior() -> None:
    cfg = PredictionConfig(prediction_date="2026-04-24", elite_market_mode="points_only")
    pipeline = PredictionPipeline(cfg)
    assert pipeline._elite_allowed_markets == {"player_points"}


def test_player_props_mode_allows_non_points_props() -> None:
    cfg = PredictionConfig(prediction_date="2026-04-24", elite_market_mode="player_props")
    pipeline = PredictionPipeline(cfg)
    assert "player_rebounds" in pipeline._elite_allowed_markets
    assert "player_assists" in pipeline._elite_allowed_markets
    assert "player_points" in pipeline._elite_allowed_markets
    assert "moneyline" not in pipeline._elite_allowed_markets


def test_candidate_projection_support_for_non_points_markets() -> None:
    cfg = PredictionConfig(prediction_date="2026-04-24", elite_market_mode="player_props")
    pipeline = PredictionPipeline(cfg)
    row = pd.Series(
        {
            "pts_avg": 20.0,
            "reb_avg": 8.0,
            "ast_avg": 6.0,
            "threes_avg": 2.8,
            "stl_avg": 1.2,
            "blk_avg": 0.9,
        }
    )
    assert pipeline._compute_projection(row, "player_rebounds") == 8.0
    assert pipeline._compute_projection(row, "player_assists") == 6.0
    assert pipeline._compute_projection(row, "player_3pt_made") == 2.8
    assert pipeline._compute_projection(row, "player_steals") == 1.2
    assert pipeline._compute_projection(row, "player_blocks") == 0.9


def test_unsupported_market_rejected_with_explicit_reason() -> None:
    players = pd.DataFrame([{"player_name": "A Star", "team_abbr": "BOS", "min_avg": 30.0}])
    odds = pd.DataFrame([{"player_name": "A Star", "team_abbr": "BOS", "market_type": "player_rebounds", "line": 10.5, "odds": -110}])

    def build_candidate_row(**_: object):
        return None

    def score_candidate_fn(**_: object):
        return None

    def reject_candidate_fn(player_row, market, reason, team, projection_support_status=""):
        return {
            "player_name": player_row.get("player_name"),
            "market_type": market,
            "rejection_reason": reason,
            "team": team,
            "projection_support_status": projection_support_status,
        }

    accepted, rejected = score_player_markets(
        players_df=players,
        odds_df=odds,
        is_player_inactive=lambda _: False,
        build_candidate_row=build_candidate_row,
        score_candidate_fn=score_candidate_fn,
        reject_candidate_fn=reject_candidate_fn,
        allow_partial_fill=False,
    )
    assert accepted == []
    assert rejected
    assert rejected[0]["rejection_reason"] == "unsupported_projection_market"

