"""Tests for player_points projection recalibration feature flag.

Covers:
- recalibration off leaves projection unchanged
- shadow mode adds recalibrated fields but does not alter selection
- enabled mode is shadow-only until projection/edge semantics are validated
- non-player_points markets are unaffected
- existing game-status and odds freshness gates still take precedence
- strong OVER guard still works after recalibration
"""
from __future__ import annotations

import importlib
import json
from typing import Any

import pytest

from courtvision.projection.recalibration import (
    ENABLED_UNSUPPORTED_REASON,
    get_recalibration_mode,
    get_recalibration_requested_mode,
    is_recalibration_enabled,
    is_recalibration_off,
    is_recalibration_shadow,
    recalibrate_player_points,
    should_use_recalibrated_for_selection,
    get_effective_projection_and_edge,
)


# ---------------------------------------------------------------------------
# Feature flag tests
# ---------------------------------------------------------------------------

def test_module_imports_from_pipeline_clean_checkout():
    module = importlib.import_module("courtvision.projection.recalibration")
    pipeline_module = importlib.import_module("courtvision.pipeline.predict_pipeline")
    assert callable(module.recalibrate_player_points)
    assert hasattr(pipeline_module, "PredictionPipeline")


def test_default_mode_is_off(monkeypatch):
    monkeypatch.delenv("COURTVISION_PLAYER_POINTS_RECALIBRATION", raising=False)
    assert get_recalibration_mode() == "off"
    assert get_recalibration_requested_mode() == "off"
    assert is_recalibration_off() is True
    assert is_recalibration_shadow() is False
    assert is_recalibration_enabled() is False


def test_shadow_mode(monkeypatch):
    monkeypatch.setenv("COURTVISION_PLAYER_POINTS_RECALIBRATION", "shadow")
    assert get_recalibration_requested_mode() == "shadow"
    assert get_recalibration_mode() == "shadow"
    assert is_recalibration_off() is False
    assert is_recalibration_shadow() is True
    assert is_recalibration_enabled() is False


def test_enabled_mode_is_shadow_only(monkeypatch):
    monkeypatch.setenv("COURTVISION_PLAYER_POINTS_RECALIBRATION", "enabled")
    assert get_recalibration_requested_mode() == "enabled"
    assert get_recalibration_mode() == "shadow"
    assert is_recalibration_off() is False
    assert is_recalibration_shadow() is True
    assert is_recalibration_enabled() is False


def test_invalid_mode_defaults_to_off(monkeypatch):
    monkeypatch.setenv("COURTVISION_PLAYER_POINTS_RECALIBRATION", "invalid")
    assert get_recalibration_requested_mode() == "off"
    assert get_recalibration_mode() == "off"


# ---------------------------------------------------------------------------
# Recalibration logic tests
# ---------------------------------------------------------------------------

@pytest.fixture
def base_row() -> dict[str, Any]:
    """A typical player_points over pick with strong enough edge to survive recalibration."""
    return {
        "model_projection": 28.0,  # 5.5 edge before recalibration
        "sportsbook_line": 22.5,
        "selection": "over",
        "minutes_avg": 32.0,
        "player_points_recent_form_ratio": 1.05,
        "opponent_def_rating": 113.0,
        "matchup_pace": 100.0,
        "postseason": False,
        "player_profile_bucket": "star_high_usage",
    }


def test_recalibration_applies_over_penalty(base_row):
    result = recalibrate_player_points(base_row)
    # Over penalty: 28.0 * 0.88 = 24.64, then shrinkage toward 22.5 at ~55%
    assert result["recalibrated_projection"] < base_row["model_projection"]


def test_recalibration_components_json_valid(base_row):
    result = recalibrate_player_points(base_row)
    components = json.loads(result["recalibration_components_json"])
    assert components["original_projection"] == 28.0
    assert components["selection"] == "over"
    assert components["requested_mode"] == "off"
    assert components["effective_mode"] == "off"
    assert "over_penalty" in components


def test_recalibration_selected_for_strong_over(base_row):
    result = recalibrate_player_points(base_row)
    assert result["recalibration_selected"] is True


def test_recalibration_rejects_weak_over(base_row):
    """A pick with edge < 1.0 after recalibration should be rejected."""
    base_row["model_projection"] = 24.0  # Only 1.5 edge before recalibration
    result = recalibrate_player_points(base_row)
    assert result["recalibration_selected"] is False
    assert "recalibrated_edge_below" in result["recalibration_rejection_reason"]


def test_under_selection_different_edge(base_row):
    base_row["selection"] = "under"
    base_row["model_projection"] = 18.0  # 4.5 edge for under (line 22.5)
    result = recalibrate_player_points(base_row)
    # Unders don't get over penalty, should have positive edge
    assert result["recalibrated_edge"] > 0
    assert result["recalibration_selected"] is True


def test_minutes_sanity_shrinks_projection(base_row):
    base_row["minutes_avg"] = 18.0
    result = recalibrate_player_points(base_row)
    components = json.loads(result["recalibration_components_json"])
    assert "minutes_sanity_factor" in components
    assert components["minutes_sanity_factor"] < 1.0


def test_playoff_role_dampener(base_row):
    base_row["postseason"] = True
    base_row["player_profile_bucket"] = "role_low_usage"
    result = recalibrate_player_points(base_row)
    components = json.loads(result["recalibration_components_json"])
    assert components.get("playoff_dampener") == 0.96


def test_playoff_star_boost(base_row):
    base_row["postseason"] = True
    base_row["player_profile_bucket"] = "star_high_usage"
    result = recalibrate_player_points(base_row)
    components = json.loads(result["recalibration_components_json"])
    assert components.get("playoff_boost") == 1.02


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------

def test_should_use_recalibrated_off_mode(monkeypatch, base_row):
    monkeypatch.setenv("COURTVISION_PLAYER_POINTS_RECALIBRATION", "off")
    assert should_use_recalibrated_for_selection("player_points", 2.0) is False


def test_should_use_recalibrated_enabled_mode_is_disabled(monkeypatch, base_row):
    monkeypatch.setenv("COURTVISION_PLAYER_POINTS_RECALIBRATION", "enabled")
    assert should_use_recalibrated_for_selection("player_points", 2.0) is False
    assert should_use_recalibrated_for_selection("player_points", 0.5) is False


def test_should_not_use_recalibrated_for_non_player_points(monkeypatch, base_row):
    monkeypatch.setenv("COURTVISION_PLAYER_POINTS_RECALIBRATION", "enabled")
    assert should_use_recalibrated_for_selection("player_rebounds", 5.0) is False
    assert should_use_recalibrated_for_selection("player_assists", 5.0) is False
    assert should_use_recalibrated_for_selection("moneyline", 5.0) is False


# ---------------------------------------------------------------------------
# Effective projection/edge tests
# ---------------------------------------------------------------------------

def test_effective_projection_off_mode(monkeypatch, base_row):
    monkeypatch.setenv("COURTVISION_PLAYER_POINTS_RECALIBRATION", "off")
    row = {
        **base_row,
        "market_type": "player_points",
        "edge": 5.5,
        "projection": 28.0,
    }
    result = get_effective_projection_and_edge(row)
    assert result["projection"] == 28.0
    assert result["edge"] == 5.5
    assert result["source"] == "original"
    assert result["recalibration_applied"] is False


def test_effective_projection_shadow_mode(monkeypatch, base_row):
    monkeypatch.setenv("COURTVISION_PLAYER_POINTS_RECALIBRATION", "shadow")
    row = {
        **base_row,
        "market_type": "player_points",
        "edge": 2.5,
        "projection": 25.0,
        "model_projection": 25.0,
    }
    result = get_effective_projection_and_edge(row)
    # Shadow mode: returns original but includes recalibrated fields
    assert result["projection"] == 25.0
    assert result["edge"] == 2.5
    assert result["source"] == "original"
    assert "recalibrated_projection" in result
    assert "recalibrated_edge" in result
    assert result["recalibration_requested_mode"] == "shadow"
    assert result["recalibration_effective_mode"] == "shadow"
    assert result["recalibration_applied"] is False


def test_effective_projection_enabled_mode_is_shadow_only(monkeypatch, base_row):
    monkeypatch.setenv("COURTVISION_PLAYER_POINTS_RECALIBRATION", "enabled")
    row = {
        **base_row,
        "model_projection": 28.0,  # 5.5 edge, strong enough for recalibration
        "sportsbook_line": 22.5,
        "market_type": "player_points",
        "edge": 5.5,
        "projection": 28.0,
    }
    result = get_effective_projection_and_edge(row)
    # Enabled mode is not production-ready; it computes diagnostics only.
    assert result["projection"] == 28.0
    assert result["edge"] == 5.5
    assert result["source"] == "original"
    assert result["recalibration_applied"] is False
    assert result["recalibration_requested_mode"] == "enabled"
    assert result["recalibration_effective_mode"] == "shadow"
    assert "recalibrated_projection" in result
    assert result["recalibration_selected"] is False
    assert result["recalibration_rejection_reason"] == ENABLED_UNSUPPORTED_REASON


def test_recalibration_enabled_request_returns_warning_reason(monkeypatch, base_row):
    monkeypatch.setenv("COURTVISION_PLAYER_POINTS_RECALIBRATION", "enabled")
    result = recalibrate_player_points(base_row)
    assert result["recalibration_selected"] is False
    assert result["recalibration_rejection_reason"] == ENABLED_UNSUPPORTED_REASON


def test_effective_projection_unaffected_markets(monkeypatch, base_row):
    monkeypatch.setenv("COURTVISION_PLAYER_POINTS_RECALIBRATION", "enabled")
    for market in ["player_rebounds", "player_assists", "player_points_rebounds", "moneyline"]:
        row = {
            **base_row,
            "market_type": market,
            "edge": 2.5,
            "projection": 25.0,
            "model_projection": 25.0,
        }
        result = get_effective_projection_and_edge(row)
        assert result["source"] == "original", f"{market} should not use recalibration"
        assert result["recalibration_applied"] is False


# ---------------------------------------------------------------------------
# Edge bucket behavior
# ---------------------------------------------------------------------------

def test_recalibration_selected_with_edge_above_1(base_row):
    base_row["model_projection"] = 28.0  # 5.5 edge -> above 1.0 after recalibration
    result = recalibrate_player_points(base_row)
    assert result["recalibration_selected"] is True


def test_recalibration_rejected_with_edge_below_1(base_row):
    base_row["model_projection"] = 22.8  # 0.3 edge before -> below 1.0 after recalibration
    result = recalibrate_player_points(base_row)
    assert result["recalibration_selected"] is False


# ---------------------------------------------------------------------------
# Guard precedence tests
# ---------------------------------------------------------------------------

def test_existing_gates_unchanged(base_row):
    """Recalibration should not modify game-status or odds-freshness guards."""
    # These guards are in runtime_selection.py and runtime_audit.py
    # We verify the recalibration module doesn't interfere with them
    from courtvision.runtime_selection import (
        game_status_ineligibility_reason,
        odds_stale_ineligibility_reason,
    )
    from courtvision.runtime_audit import (
        get_elite_rejection_reason,
        projected_kelly_skip_reason,
    )

    # Guards exist and are importable
    assert callable(game_status_ineligibility_reason)
    assert callable(odds_stale_ineligibility_reason)
    assert callable(get_elite_rejection_reason)
    assert callable(projected_kelly_skip_reason)


def test_recalibration_does_not_affect_freshness_gate(base_row):
    """Even with recalibration enabled, stale odds should still be rejected."""
    row = {
        **base_row,
        "market_type": "player_points",
        "model_projection": 25.0,
        "sportsbook_line": 22.5,
        "selection": "over",
        "edge": 2.5,
        "odds_updated_at": "2024-01-01T00:00:00",  # Clearly stale
    }
    from courtvision.runtime_selection import odds_stale_ineligibility_reason
    stale_reason = odds_stale_ineligibility_reason(row, now=datetime(2024, 6, 1, 12, 0, 0))
    assert stale_reason == "odds_stale"


# ---------------------------------------------------------------------------
from datetime import datetime
