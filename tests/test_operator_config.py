"""Tests for operator configuration.

VALIDATE + CALIBRATE mode - Configuration and control only.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from courtvision.config import (
    ModePreset,
    OperatorConfig,
    create_aggressive_mode,
    create_balanced_mode,
    create_conservative_mode,
    create_shadow_mode,
    load_config,
    save_config,
)


def test_mode_preset_values():
    """Test mode preset enum values."""
    assert ModePreset.CONSERVATIVE.value == "conservative"
    assert ModePreset.BALANCED.value == "balanced"
    assert ModePreset.AGGRESSIVE.value == "aggressive"


def test_threshold_config_defaults():
    """Test threshold config default values."""
    from courtvision.config.operator_config import ThresholdConfig

    config = ThresholdConfig()
    assert config.edge == 0.05
    assert config.confidence == 0.65
    assert config.ev == 0.03
    assert config.min_hit_probability == 0.52


def test_threshold_config_conservative_modifier():
    """Test conservative mode threshold adjustment."""
    from courtvision.config.operator_config import ThresholdConfig

    base = ThresholdConfig(edge=0.05, confidence=0.65, ev=0.03)
    conservative = base.apply_mode_modifier(ModePreset.CONSERVATIVE)

    assert conservative.edge > base.edge  # Higher threshold
    assert conservative.confidence > base.confidence
    assert conservative.ev > base.ev
    assert conservative.min_hit_probability > base.min_hit_probability


def test_threshold_config_aggressive_modifier():
    """Test aggressive mode threshold adjustment."""
    from courtvision.config.operator_config import ThresholdConfig

    base = ThresholdConfig(edge=0.05, confidence=0.65, ev=0.03)
    aggressive = base.apply_mode_modifier(ModePreset.AGGRESSIVE)

    assert aggressive.edge < base.edge  # Lower threshold
    assert aggressive.confidence < base.confidence
    assert aggressive.ev < base.ev
    assert aggressive.min_hit_probability < base.min_hit_probability


def test_limit_config_conservative_modifier():
    """Test conservative mode limit adjustment."""
    from courtvision.config.operator_config import LimitConfig

    base = LimitConfig(max_daily_plays=10, max_plays_per_game=3)
    conservative = base.apply_mode_modifier(ModePreset.CONSERVATIVE)

    assert conservative.max_daily_plays < base.max_daily_plays
    assert conservative.max_plays_per_game <= base.max_plays_per_game
    assert conservative.max_plays_per_player == 1


def test_limit_config_aggressive_modifier():
    """Test aggressive mode limit adjustment."""
    from courtvision.config.operator_config import LimitConfig

    base = LimitConfig(max_daily_plays=10, max_plays_per_game=3)
    aggressive = base.apply_mode_modifier(ModePreset.AGGRESSIVE)

    assert aggressive.max_daily_plays > base.max_daily_plays
    assert aggressive.max_plays_per_game > base.max_plays_per_game
    assert aggressive.max_plays_per_player > base.max_plays_per_player


def test_operator_config_defaults():
    """Test operator config default values."""
    config = OperatorConfig()

    assert config.mode == ModePreset.BALANCED
    assert config.should_gate_with_simulation() is True
    assert config.should_use_adaptive_thresholds() is True
    assert config.should_use_sgp_builder() is False
    assert config.should_apply_feedback() is True
    assert config.should_optimize_portfolio() is True
    assert config.is_shadow_run_mode() is False


def test_operator_config_effective_thresholds():
    """Test effective thresholds computation."""
    config = OperatorConfig(
        mode=ModePreset.BALANCED,
        base_thresholds__edge=0.05,
    )

    effective = config.get_effective_thresholds()
    assert "edge" in effective
    assert "confidence" in effective
    assert "ev" in effective
    assert "min_hit_probability" in effective


def test_operator_config_effective_limits():
    """Test effective limits computation."""
    config = OperatorConfig(mode=ModePreset.BALANCED)

    effective = config.get_effective_limits()
    assert "max_daily_plays" in effective
    assert "max_portfolio_exposure" in effective
    assert "max_plays_per_game" in effective
    assert "max_plays_per_player" in effective


def test_operator_config_set_mode():
    """Test changing mode and recomputing."""
    config = OperatorConfig(mode=ModePreset.BALANCED)
    base_edge = config.effective_thresholds.edge

    config.set_mode(ModePreset.CONSERVATIVE)
    assert config.mode == ModePreset.CONSERVATIVE
    assert config.effective_thresholds.edge > base_edge

    config.set_mode(ModePreset.AGGRESSIVE)
    assert config.mode == ModePreset.AGGRESSIVE
    assert config.effective_thresholds.edge < base_edge


def test_operator_config_validate_candidate_pass():
    """Test candidate validation - passing."""
    config = OperatorConfig(mode=ModePreset.BALANCED)

    # Candidate that meets all thresholds
    is_valid, reason = config.validate_candidate(
        edge=0.08,
        confidence=0.75,
        ev=0.05,
        hit_prob=0.60,
    )

    assert is_valid is True
    assert reason == ""


def test_operator_config_validate_candidate_fail_edge():
    """Test candidate validation - failing edge."""
    config = OperatorConfig(mode=ModePreset.BALANCED)

    is_valid, reason = config.validate_candidate(
        edge=0.02,  # Below threshold
        confidence=0.75,
        ev=0.05,
        hit_prob=0.60,
    )

    assert is_valid is False
    assert "edge" in reason


def test_operator_config_validate_candidate_fail_confidence():
    """Test candidate validation - failing confidence."""
    config = OperatorConfig(mode=ModePreset.BALANCED)

    is_valid, reason = config.validate_candidate(
        edge=0.08,
        confidence=0.55,  # Below threshold
        ev=0.05,
        hit_prob=0.60,
    )

    assert is_valid is False
    assert "confidence" in reason


def test_operator_config_validate_candidate_multiple_failures():
    """Test candidate validation - multiple failures."""
    config = OperatorConfig(mode=ModePreset.BALANCED)

    is_valid, reason = config.validate_candidate(
        edge=0.02,  # Below
        confidence=0.55,  # Below
        ev=0.01,  # Below
        hit_prob=0.60,
    )

    assert is_valid is False
    assert "edge" in reason
    assert "confidence" in reason
    assert "ev" in reason


def test_operator_config_check_limits_pass():
    """Test limit checking - passing."""
    config = OperatorConfig(mode=ModePreset.BALANCED)

    can_add, reason = config.check_limits(
        current_daily_plays=5,
        current_exposure=0.5,
        plays_in_game=1,
        plays_on_player=1,
    )

    assert can_add is True
    assert reason == ""


def test_operator_config_check_limits_daily():
    """Test limit checking - daily limit."""
    config = OperatorConfig(mode=ModePreset.BALANCED)
    max_daily = config.effective_limits.max_daily_plays

    can_add, reason = config.check_limits(
        current_daily_plays=max_daily,
        current_exposure=0.5,
        plays_in_game=1,
        plays_on_player=1,
    )

    assert can_add is False
    assert "daily" in reason.lower()


def test_operator_config_check_limits_game():
    """Test limit checking - per-game limit."""
    config = OperatorConfig(mode=ModePreset.BALANCED)
    max_per_game = config.effective_limits.max_plays_per_game

    can_add, reason = config.check_limits(
        current_daily_plays=1,
        current_exposure=0.1,
        plays_in_game=max_per_game,
        plays_on_player=1,
    )

    assert can_add is False
    assert "game" in reason.lower()


def test_operator_config_check_limits_player():
    """Test limit checking - per-player limit."""
    config = OperatorConfig(mode=ModePreset.BALANCED)
    max_per_player = config.effective_limits.max_plays_per_player

    can_add, reason = config.check_limits(
        current_daily_plays=1,
        current_exposure=0.1,
        plays_in_game=1,
        plays_on_player=max_per_player,
    )

    assert can_add is False
    assert "player" in reason.lower()


def test_create_conservative_mode():
    """Test conservative mode factory."""
    config = create_conservative_mode(max_daily_plays=6, edge_threshold=0.08)

    assert config.mode == ModePreset.CONSERVATIVE
    assert config.effective_limits.max_daily_plays < 6  # Mode modifier applied
    assert config.effective_thresholds.edge > 0.08  # Mode modifier applied
    assert config.should_use_adaptive_thresholds() is False
    assert config.should_use_sgp_builder() is False


def test_create_balanced_mode():
    """Test balanced mode factory."""
    config = create_balanced_mode(max_daily_plays=12, edge_threshold=0.06)

    assert config.mode == ModePreset.BALANCED
    assert config.effective_limits.max_daily_plays == 12
    assert config.effective_thresholds.edge == 0.06
    assert config.should_use_adaptive_thresholds() is True
    assert config.should_use_sgp_builder() is False


def test_create_aggressive_mode():
    """Test aggressive mode factory."""
    config = create_aggressive_mode(max_daily_plays=20, edge_threshold=0.04)

    assert config.mode == ModePreset.AGGRESSIVE
    assert config.effective_limits.max_daily_plays > 20  # Mode modifier applied
    assert config.effective_thresholds.edge < 0.04  # Mode modifier applied
    assert config.should_gate_with_simulation() is False
    assert config.should_use_sgp_builder() is True


def test_create_shadow_mode():
    """Test shadow mode factory."""
    config = create_shadow_mode()

    assert config.is_shadow_run_mode() is True
    assert config.mode == ModePreset.BALANCED


def test_operator_config_serialization():
    """Test config serialization to dict."""
    config = create_balanced_mode()

    data = config.to_dict()
    assert data["mode"] == "balanced"
    assert "base_thresholds" in data
    assert "base_limits" in data
    assert "features" in data
    assert "effective_thresholds" in data
    assert "effective_limits" in data


def test_operator_config_deserialization():
    """Test config deserialization from dict."""
    original = create_balanced_mode()
    data = original.to_dict()

    restored = OperatorConfig.from_dict(data)
    assert restored.mode == original.mode
    assert restored.base_thresholds.edge == original.base_thresholds.edge
    assert restored.base_limits.max_daily_plays == original.base_limits.max_daily_plays
    assert restored.features.simulation_gate == original.features.simulation_gate


def test_operator_config_save_load():
    """Test config save and load."""
    config = create_balanced_mode()

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        temp_path = f.name

    try:
        save_config(config, temp_path)
        assert os.path.exists(temp_path)

        loaded = load_config(temp_path)
        assert loaded.mode == config.mode
        assert loaded.base_thresholds.edge == config.base_thresholds.edge
        assert loaded.features.simulation_gate == config.features.simulation_gate
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def test_operator_config_modes_comparison():
    """Test that different modes produce different effective values."""
    conservative = create_conservative_mode()
    balanced = create_balanced_mode()
    aggressive = create_aggressive_mode()

    # Conservative should have highest thresholds
    assert conservative.effective_thresholds.edge > balanced.effective_thresholds.edge
    assert balanced.effective_thresholds.edge > aggressive.effective_thresholds.edge

    # Conservative should have lowest limits
    assert conservative.effective_limits.max_daily_plays < balanced.effective_limits.max_daily_plays
    assert balanced.effective_limits.max_daily_plays < aggressive.effective_limits.max_daily_plays


def test_feature_toggles():
    """Test feature toggle methods."""
    from courtvision.config.operator_config import FeatureToggles

    toggles = FeatureToggles(
        simulation_gate=True,
        market_adaptive_thresholds=False,
        sgp_builder=True,
        feedback_adjustments=False,
        portfolio_optimization=True,
        shadow_run_mode=True,
    )

    assert toggles.simulation_gate is True
    assert toggles.market_adaptive_thresholds is False
    assert toggles.sgp_builder is True
    assert toggles.feedback_adjustments is False
    assert toggles.portfolio_optimization is True
    assert toggles.shadow_run_mode is True
