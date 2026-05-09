from __future__ import annotations

import pandas as pd

from courtvision.diagnostics.fragility import FRAGILITY_COLUMNS, add_fragility_survivability_diagnostics


def test_fragility_empty_df_safe() -> None:
    df = pd.DataFrame()
    out = add_fragility_survivability_diagnostics(df)
    for col in FRAGILITY_COLUMNS:
        assert col in out.columns


def test_fragility_high_for_risky_points_over() -> None:
    df = pd.DataFrame([
        {
            "market_type": "player_points",
            "selection": "over",
            "confidence": 0.42,
            "quality_score": 48,
            "minutes_avg": 20,
            "context_caution_level": "high",
            "context_pick_alignment": "conflicted",
            "player_points_line_band": "low",
            "market_trust_weight": 0.6,
            "same_opponent_under_warning": True,
            "blowout_risk": 0.8,
            "injury_impact_score": 0.7,
            "injury_confidence_delta": -0.2,
            "player_points_realism_dampened": True,
        }
    ])
    out = add_fragility_survivability_diagnostics(df)
    assert out.loc[0, "fragility_score"] >= 67
    assert out.loc[0, "fragility_bucket"] == "HIGH"


def test_survivability_higher_for_stable_under_profile() -> None:
    df = pd.DataFrame([
        {
            "market_type": "player_rebounds",
            "selection": "under",
            "confidence": 0.82,
            "quality_score": 84,
            "minutes_avg": 33,
            "context_caution_level": "low",
            "context_pick_alignment": "aligned",
            "market_trust_weight": 1.1,
            "blowout_risk": 0.2,
            "expected_competitiveness": 0.7,
            "side_edge": 0.3,
            "injury_impact_score": 0.1,
        }
    ])
    out = add_fragility_survivability_diagnostics(df)
    assert out.loc[0, "survivability_score"] >= 67
    assert out.loc[0, "survivability_bucket"] in {"HIGH", "MEDIUM"}
    assert out.loc[0, "fragility_score"] < out.loc[0, "survivability_score"]


def test_fragility_does_not_mutate_decision_fields() -> None:
    base = pd.DataFrame([
        {
            "market_type": "player_points",
            "selection": "under",
            "quality_score": 71.0,
            "confidence": 0.73,
            "edge": -1.2,
            "side_edge": 0.2,
            "selection_score": 66.0,
            "is_elite": True,
            "kelly_eligible": False,
            "stake_amount": 0.0,
        }
    ])
    out = add_fragility_survivability_diagnostics(base)
    for col in ["quality_score", "confidence", "edge", "side_edge", "selection_score", "is_elite", "kelly_eligible", "stake_amount"]:
        assert out.loc[0, col] == base.loc[0, col]
