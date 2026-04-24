"""Tests for Phase 6 scoring and decision quality refinements.

Validates:
1. Confidence calibration (edge dampening, market penalty)
2. Anti-double-count protection
3. Board diversity controls
4. Injury volatility confidence penalty
"""

import pandas as pd
import pytest

from courtvision.scoring.confidence import compute_confidence
from courtvision.injuries.volatility import compute_volatility_confidence_penalty
from courtvision.selection.operator_boards import (
    compute_board_diversity_metrics,
    apply_diversity_penalty,
)


class TestConfidenceCalibration:
    """Test confidence calibration improvements."""

    def test_edge_dampening_for_very_large_edges(self):
        """Very large edges (>10) should get reduced boost + dampening."""
        row = {"confidence": 0.70}
        result = compute_confidence(
            row=row,
            edge_abs=12.0,
            market_type="player_points",
            minutes_projection=30.0,
        )
        assert result["edge_boost"] == 0.02
        assert result["edge_dampening"] == 0.03
        assert result["adjusted_confidence"] < 0.70 + 0.02  # Dampening applied

    def test_normal_edge_boost_for_moderate_edges(self):
        """Normal edges (6-10) should get full boost without dampening."""
        row = {"confidence": 0.70}
        result = compute_confidence(
            row=row,
            edge_abs=7.0,
            market_type="player_points",
            minutes_projection=30.0,
        )
        assert result["edge_boost"] == 0.04
        assert result["edge_dampening"] == 0.0

    def test_market_quality_penalty_applied(self):
        """Poor market quality should reduce confidence."""
        row = {"confidence": 0.70}
        result_good = compute_confidence(
            row=row,
            edge_abs=5.0,
            market_type="player_points",
            minutes_projection=30.0,
            market_quality_score=0.9,
        )
        result_poor = compute_confidence(
            row=row,
            edge_abs=5.0,
            market_type="player_points",
            minutes_projection=30.0,
            market_quality_score=0.5,
        )
        assert result_poor["market_penalty"] > 0
        assert result_poor["adjusted_confidence"] < result_good["adjusted_confidence"]

    def test_confidence_capped_at_96_percent(self):
        """Maximum confidence should be capped at 0.96."""
        row = {"confidence": 0.95}
        result = compute_confidence(
            row=row,
            edge_abs=15.0,
            market_type="player_points",
            minutes_projection=30.0,
        )
        assert result["adjusted_confidence"] <= 0.96


class TestAntiDoubleCountProtection:
    """Test anti-double-count protection in confidence computation."""

    def test_anti_double_count_cap_when_all_signals_positive(self):
        """When all positive signals present, boost should be capped."""
        row = {"confidence": 0.65}
        result = compute_confidence(
            row=row,
            edge_abs=7.0,  # Has edge_boost
            market_type="player_points",
            minutes_projection=30.0,  # Has minutes boost
            recent_form_ratio=1.20,  # Strong recent form
            injury_status="healthy",  # No injury
        )
        # With 3 positive signals and edge boost, should get penalty
        assert result["anti_double_count_adj"] == -0.02
        assert "anti_double_count_cap_applied" in result["diagnostics"]["adjustments"]

    def test_no_anti_double_count_penalty_with_few_signals(self):
        """With fewer positive signals, no cap applied."""
        row = {"confidence": 0.65}
        result = compute_confidence(
            row=row,
            edge_abs=7.0,
            market_type="player_points",
            minutes_projection=25.0,  # Below 28 threshold
            recent_form_ratio=1.20,
            injury_status="healthy",
        )
        assert result["anti_double_count_adj"] == 0.0
        assert "anti_double_count_cap_applied" not in result["diagnostics"]["adjustments"]

    def test_minutes_floor_not_applied_with_high_penalty(self):
        """Minutes floor should not apply if market penalty is high."""
        row = {"confidence": 0.50}
        result = compute_confidence(
            row=row,
            edge_abs=5.0,
            market_type="player_points",
            minutes_projection=30.0,
            market_quality_score=0.3,  # High penalty
        )
        # With market_penalty >= 0.10, minutes floor should not apply
        assert result["market_penalty"] > 0.10
        assert "minutes_floor_28plus" not in result["diagnostics"]["adjustments"]


class TestDiagnosticsExposure:
    """Test diagnostic tracking in confidence computation."""

    def test_diagnostics_include_all_adjustments(self):
        """Diagnostics should track all adjustments applied."""
        row = {"confidence": 0.70}
        result = compute_confidence(
            row=row,
            edge_abs=12.0,  # Triggers large edge dampening
            market_type="player_points",
            minutes_projection=30.0,
            market_quality_score=0.5,  # Triggers market penalty
            recent_form_ratio=1.25,  # Triggers anti-double-count
            injury_status="healthy",
        )
        adjustments = result["diagnostics"]["adjustments"]
        assert "large_edge_dampening_applied" in adjustments
        assert any("market_quality_penalty" in adj for adj in adjustments)
        assert "anti_double_count_cap_applied" in adjustments
        assert "minutes_floor_28plus" in adjustments

    def test_final_confidence_in_diagnostics(self):
        """Diagnostics should include final confidence value."""
        row = {"confidence": 0.70}
        result = compute_confidence(
            row=row,
            edge_abs=5.0,
            market_type="player_points",
            minutes_projection=30.0,
        )
        assert "final_confidence" in result["diagnostics"]
        assert result["diagnostics"]["final_confidence"] == result["adjusted_confidence"]


class TestBoardDiversityMetrics:
    """Test board diversity computation."""

    def test_empty_board_returns_zero_metrics(self):
        """Empty board should return zero/empty metrics."""
        metrics = compute_board_diversity_metrics(pd.DataFrame())
        assert metrics["points_bias_ratio"] == 0.0
        assert metrics["max_player_clustering"] == 0
        assert metrics["max_game_exposure"] == 0

    def test_points_bias_ratio_computed_correctly(self):
        """Points bias ratio should be computed correctly."""
        board = pd.DataFrame({
            "market_type": ["player_points", "player_points", "player_assists", "player_rebounds"],
            "player_name": ["A", "B", "C", "D"],
            "game_id": [1, 2, 3, 4],
        })
        metrics = compute_board_diversity_metrics(board)
        assert metrics["points_bias_ratio"] == 0.5  # 2 out of 4

    def test_player_clustering_detected(self):
        """Max player clustering should detect multiple picks for same player."""
        board = pd.DataFrame({
            "market_type": ["player_points", "player_assists", "player_rebounds"],
            "player_name": ["LeBron", "LeBron", "LeBron"],
            "game_id": [1, 1, 1],
        })
        metrics = compute_board_diversity_metrics(board)
        assert metrics["max_player_clustering"] == 3

    def test_game_exposure_detected(self):
        """Max game exposure should detect multiple picks from same game."""
        board = pd.DataFrame({
            "market_type": ["player_points", "player_assists", "player_rebounds", "player_steals"],
            "player_name": ["A", "B", "C", "D"],
            "game_id": [1, 1, 1, 1],
        })
        metrics = compute_board_diversity_metrics(board)
        assert metrics["max_game_exposure"] == 4


class TestDiversityPenalty:
    """Test diversity-based confidence penalties."""

    def test_points_bias_penalty_applied(self):
        """Points market bias should apply penalty."""
        row = pd.Series({
            "market_type": "player_points",
            "player_name": "LeBron",
            "game_id": 1,
        })
        metrics = {"points_bias_ratio": 0.6}  # Above 0.5 threshold
        penalty, reasons = apply_diversity_penalty(row, metrics, max_points_ratio=0.5)
        assert penalty == 0.03
        assert any("points_bias" in r for r in reasons)

    def test_player_clustering_penalty_applied(self):
        """Player clustering should apply penalty."""
        row = pd.Series({
            "market_type": "player_points",
            "player_name": "LeBron",
            "game_id": 1,
        })
        metrics = {
            "points_bias_ratio": 0.3,
            "player_counts": {"LeBron": 3},
        }
        penalty, reasons = apply_diversity_penalty(row, metrics, max_per_player=2)
        assert penalty == 0.04
        assert any("player_clustering" in r for r in reasons)

    def test_game_exposure_penalty_applied(self):
        """Game overexposure should apply penalty."""
        row = pd.Series({
            "market_type": "player_points",
            "player_name": "LeBron",
            "game_id": 1,
        })
        metrics = {
            "points_bias_ratio": 0.3,
            "player_counts": {},
            "game_counts": {1: 4},
        }
        penalty, reasons = apply_diversity_penalty(row, metrics, max_per_game=3)
        assert penalty == 0.02
        assert any("game_exposure" in r for r in reasons)

    def test_multiple_penalties_stacked(self):
        """Multiple diversity issues should stack penalties (with cap)."""
        row = pd.Series({
            "market_type": "player_points",
            "player_name": "LeBron",
            "game_id": 1,
        })
        metrics = {
            "points_bias_ratio": 0.6,
            "player_counts": {"LeBron": 3},
            "game_counts": {1: 4},
        }
        penalty, reasons = apply_diversity_penalty(row, metrics)
        assert penalty > 0.03  # At least points penalty
        assert penalty <= 0.15  # But capped at 15%
        assert len(reasons) == 3  # All three penalties

    def test_no_penalty_for_diverse_board(self):
        """Diverse board should not trigger penalties."""
        row = pd.Series({
            "market_type": "player_assists",
            "player_name": "LeBron",
            "game_id": 1,
        })
        metrics = {
            "points_bias_ratio": 0.2,
            "player_counts": {"LeBron": 1},
            "game_counts": {1: 1},
        }
        penalty, reasons = apply_diversity_penalty(row, metrics)
        assert penalty == 0.0
        assert len(reasons) == 0


class TestInjuryVolatilityPenalty:
    """Test injury volatility confidence penalty."""

    def test_recent_form_well_below_baseline(self):
        """Recent form well below baseline should trigger penalty."""
        player_row = {"games_last_10": 5, "ppg_last_10": 15.0}
        result = compute_volatility_confidence_penalty(
            player_row, baseline_projection=25.0, injury_impact=0.1
        )
        assert result["recent_form_ratio"] < 0.75
        assert result["penalty"] >= 0.06
        assert any("recent_form_well_below_baseline" in r for r in result["reasons"])

    def test_recent_form_below_baseline(self):
        """Recent form moderately below baseline should trigger smaller penalty."""
        player_row = {"games_last_10": 8, "ppg_last_10": 20.0}
        result = compute_volatility_confidence_penalty(
            player_row, baseline_projection=25.0, injury_impact=0.0
        )
        assert 0.75 <= result["recent_form_ratio"] < 0.85
        assert result["penalty"] >= 0.03
        assert any("recent_form_below_baseline" in r for r in result["reasons"])

    def test_high_injury_impact_penalty(self):
        """High injury impact should trigger significant penalty."""
        player_row = {"games_last_10": 10, "ppg_last_10": 24.0}
        result = compute_volatility_confidence_penalty(
            player_row, baseline_projection=25.0, injury_impact=0.5
        )
        assert result["penalty"] >= 0.08
        assert any("high_injury_impact" in r for r in result["reasons"])

    def test_moderate_injury_impact_penalty(self):
        """Moderate injury impact should trigger smaller penalty."""
        player_row = {"games_last_10": 10, "ppg_last_10": 24.0}
        result = compute_volatility_confidence_penalty(
            player_row, baseline_projection=25.0, injury_impact=0.3
        )
        assert result["penalty"] >= 0.04
        assert any("moderate_injury_impact" in r for r in result["reasons"])

    def test_weak_independent_support_penalty(self):
        """Weak independent support should trigger penalty."""
        player_row = {"games_last_10": 2, "ppg_last_10": 1.0}  # Very weak stats
        result = compute_volatility_confidence_penalty(
            player_row, baseline_projection=25.0, injury_impact=0.0
        )
        assert result["independent_support"] < 0.02
        assert result["penalty"] >= 0.04
        assert any("weak_independent_support" in r for r in result["reasons"])

    def test_penalty_capped_at_20_percent(self):
        """Total penalty should be capped at 20%."""
        player_row = {"games_last_10": 1, "ppg_last_10": 5.0}  # Very weak
        result = compute_volatility_confidence_penalty(
            player_row, baseline_projection=30.0, injury_impact=0.8  # High impact
        )
        assert result["penalty"] <= 0.20

    def test_combined_volatility_score(self):
        """Volatility score should be computed and capped at 1.0."""
        player_row = {"games_last_10": 5, "ppg_last_10": 10.0}
        result = compute_volatility_confidence_penalty(
            player_row, baseline_projection=25.0, injury_impact=0.3
        )
        assert "volatility_score" in result
        assert 0 <= result["volatility_score"] <= 1.0
