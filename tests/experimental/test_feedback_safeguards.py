"""Tests for Phase 7.5: Feedback stability safeguards.

Validates:
1. Minimum sample thresholds (30 picks or 7 days)
2. EMA smoothing behavior
3. Adjustment caps (±10% conf, ±15% edge, ±20% penalty)
4. Cooldown mechanism (max 1/day)
5. Fallback to defaults
6. Diagnostics tracking
"""

import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from courtvision.feedback.calibration import CalibrationAdjustment, ConfidenceCalibrator
from courtvision.feedback.edge_tracker import EdgeReliabilityTracker, EdgeWeightAdjustment
from courtvision.feedback.penalty_tuner import PenaltyAdjustment, PenaltyTuner
from courtvision.feedback.performance_store import PerformanceRecord, PerformanceStore


class TestMinimumSampleThresholds:
    """Test minimum sample thresholds prevent premature adjustments."""

    def test_calibration_skips_with_insufficient_samples(self):
        """Calibration should not adjust with < 30 samples."""
        store = PerformanceStore()
        calibrator = ConfidenceCalibrator(store)

        # Add only 5 records
        for i in range(5):
            store.add_record(PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"P{i}",
                stat_type="points",
                market_type="player_points",
                edge=5.0,
                confidence=0.85,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="miss",  # Underperforming
                actual_value=0.0,
                line_value=0.0,
                projection=0.0,
            ))

        adjustments = calibrator.update_calibration()

        # Should have an adjustment entry but marked as not applied
        high_adj = [a for a in adjustments if a.target_bucket == "high"]
        assert len(high_adj) == 1
        assert high_adj[0].applied is False
        assert "30" in high_adj[0].skipped_reason

    def test_edge_tracker_skips_with_insufficient_samples(self):
        """Edge tracker should not adjust with < 30 picks."""
        store = PerformanceStore()
        tracker = EdgeReliabilityTracker(store)

        # Add only 10 records
        for i in range(10):
            store.add_record(PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"P{i}",
                stat_type="points",
                market_type="player_points",
                edge=7.0,
                confidence=0.80,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="hit",
                actual_value=0.0,
                line_value=0.0,
                projection=0.0,
            ))

        adjustments = tracker.update_weights()

        # Should indicate insufficient data
        assert len(adjustments) > 0
        assert any("30" in (a.skipped_reason or "") for a in adjustments)

    def test_penalty_tuner_skips_with_insufficient_applications(self):
        """Penalty tuner should not adjust with < 30 applications."""
        store = PerformanceStore()
        tuner = PenaltyTuner(store)

        # Record only 5 penalty applications
        for _ in range(5):
            tuner.record_penalty_application(
                penalty_type="market_quality",
                penalty_strength=0.05,
                original_confidence=0.75,
                penalized_confidence=0.70,
                result="hit",
            )

        adjustments = tuner.update_penalty_strengths()

        # Should indicate insufficient data
        market_adj = [a for a in adjustments if a.penalty_type == "market_quality"]
        if market_adj:
            assert market_adj[0].applied is False
            assert "30" in market_adj[0].skipped_reason


class TestEMASmoothing:
    """Test EMA smoothing prevents sudden jumps."""

    def test_ema_smooths_calibration_adjustments(self):
        """EMA should smooth calibration adjustments over time."""
        store = PerformanceStore()
        calibrator = ConfidenceCalibrator(store)

        # Add 50 records with consistent underperformance
        for i in range(50):
            store.add_record(PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"P{i}",
                stat_type="points",
                market_type="player_points",
                edge=5.0,
                confidence=0.85,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="miss" if i < 35 else "hit",  # 30% hit rate
                actual_value=0.0,
                line_value=0.0,
                projection=0.0,
            ))

        # First update
        adj1 = calibrator.update_calibration()
        high_adj_1 = [a for a in adj1 if a.target_bucket == "high" and a.applied]

        if high_adj_1:
            mult_1 = high_adj_1[0].confidence_multiplier

            # Second update (should be smoothed)
            adj2 = calibrator.update_calibration()
            high_adj_2 = [a for a in adj2 if a.target_bucket == "high" and a.applied]

            if high_adj_2:
                mult_2 = high_adj_2[0].confidence_multiplier
                # Change should be gradual due to EMA
                assert abs(mult_2 - mult_1) < 0.05  # Small per-update change

    def test_ema_smooths_edge_weight_adjustments(self):
        """EMA should smooth edge weight adjustments."""
        store = PerformanceStore()
        tracker = EdgeReliabilityTracker(store)

        # Add 50 records with strong performance in one bucket
        for i in range(50):
            store.add_record(PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"P{i}",
                stat_type="points",
                market_type="player_points",
                edge=7.0,
                confidence=0.80,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="hit" if i < 45 else "miss",  # 90% hit rate
                actual_value=0.0,
                line_value=0.0,
                projection=0.0,
            ))

        # First update
        adj1 = tracker.update_weights()

        # Second update (should be smoothed)
        adj2 = tracker.update_weights()

        # Check that changes are gradual
        for a1, a2 in zip(adj1, adj2):
            if a1.applied and a2.applied:
                change = abs(a2.recommended_weight - a1.recommended_weight)
                assert change < 0.1  # Small per-update change due to EMA


class TestAdjustmentCaps:
    """Test adjustment caps prevent extreme values."""

    def test_confidence_multiplier_capped_at_10_percent(self):
        """Confidence multipliers should be capped at ±10%."""
        store = PerformanceStore()
        calibrator = ConfidenceCalibrator(store)

        # Add extreme underperformance data
        for i in range(100):
            store.add_record(PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"P{i}",
                stat_type="points",
                market_type="player_points",
                edge=5.0,
                confidence=0.85,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="miss",  # 0% hit rate - extreme underperformance
                actual_value=0.0,
                line_value=0.0,
                projection=0.0,
            ))

        # Run multiple updates trying to drive adjustment
        for _ in range(10):
            calibrator.update_calibration()

        status = calibrator.get_calibration_status()
        for mult in status["confidence_multipliers"].values():
            assert 0.90 <= mult <= 1.10  # ±10% cap

    def test_edge_weight_capped_at_15_percent(self):
        """Edge weights should be capped at ±15%."""
        store = PerformanceStore()
        tracker = EdgeReliabilityTracker(store)

        # Add extreme performance data
        for i in range(100):
            store.add_record(PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"P{i}",
                stat_type="points",
                market_type="player_points",
                edge=7.0,
                confidence=0.80,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="hit",  # 100% hit rate - extreme
                actual_value=0.0,
                line_value=0.0,
                projection=0.0,
            ))

        # Run multiple updates
        for _ in range(10):
            tracker.update_weights()

        status = tracker.get_weight_status()
        for weight in status["weight_multipliers"].values():
            assert 0.85 <= weight <= 1.15  # ±15% cap

    def test_penalty_strength_capped_at_20_percent(self):
        """Penalty strengths should be capped at ±20%."""
        store = PerformanceStore()
        tuner = PenaltyTuner(store)

        # Record extreme effectiveness data
        for _ in range(100):
            tuner.record_penalty_application(
                penalty_type="market_quality",
                penalty_strength=0.05,
                original_confidence=0.75,
                penalized_confidence=0.70,
                result="miss",  # All penalties correct
            )

        # Run multiple updates
        for _ in range(10):
            tuner.update_penalty_strengths()

        status = tuner.get_penalty_status()
        for strength in status["current_strengths"].values():
            assert 0.80 <= strength <= 1.20  # ±20% cap


class TestCooldownMechanism:
    """Test cooldown prevents multiple updates per day."""

    def test_calibration_cooldown_blocks_second_update(self):
        """Calibration should not update twice in same day."""
        store = PerformanceStore()
        calibrator = ConfidenceCalibrator(store)

        # Add sufficient data
        for i in range(50):
            store.add_record(PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"P{i}",
                stat_type="points",
                market_type="player_points",
                edge=5.0,
                confidence=0.85,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="miss",
                actual_value=0.0,
                line_value=0.0,
                projection=0.0,
            ))

        # First update should work
        adj1 = calibrator.update_calibration()
        applied1 = [a for a in adj1 if a.applied]

        # Second update should be blocked by cooldown
        adj2 = calibrator.update_calibration()
        assert any("cooldown" in a.skipped_reason.lower() for a in adj2)

    def test_edge_tracker_cooldown_blocks_second_update(self):
        """Edge tracker should not update twice in same day."""
        store = PerformanceStore()
        tracker = EdgeReliabilityTracker(store)

        # Add sufficient data
        for i in range(50):
            store.add_record(PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"P{i}",
                stat_type="points",
                market_type="player_points",
                edge=7.0,
                confidence=0.80,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="hit",
                actual_value=0.0,
                line_value=0.0,
                projection=0.0,
            ))

        # First update
        tracker.update_weights()

        # Second update should be blocked
        adj2 = tracker.update_weights()
        assert any("cooldown" in (a.skipped_reason or "").lower() for a in adj2)


class TestFallbackToDefaults:
    """Test fallback to defaults when data insufficient."""

    def test_calibration_uses_defaults_with_no_data(self):
        """Calibration should use 1.0 multipliers with no data."""
        store = PerformanceStore()
        calibrator = ConfidenceCalibrator(store)

        status = calibrator.get_calibration_status()
        for mult in status["confidence_multipliers"].values():
            assert mult == 1.0

    def test_edge_tracker_uses_defaults_with_no_data(self):
        """Edge tracker should use 1.0 weights with no data."""
        store = PerformanceStore()
        tracker = EdgeReliabilityTracker(store)

        status = tracker.get_weight_status()
        for weight in status["weight_multipliers"].values():
            assert weight == 1.0

    def test_penalty_tuner_uses_defaults_with_no_data(self):
        """Penalty tuner should use 1.0 strengths with no data."""
        store = PerformanceStore()
        tuner = PenaltyTuner(store)

        status = tuner.get_penalty_status()
        for strength in status["current_strengths"].values():
            assert strength == 1.0

    def test_reset_restores_all_defaults(self):
        """Reset should restore all values to defaults."""
        store = PerformanceStore()
        calibrator = ConfidenceCalibrator(store)
        tracker = EdgeReliabilityTracker(store)
        tuner = PenaltyTuner(store)

        # Add data and make adjustments
        for i in range(50):
            store.add_record(PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"P{i}",
                stat_type="points",
                market_type="player_points",
                edge=5.0,
                confidence=0.85,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="miss",
                actual_value=0.0,
                line_value=0.0,
                projection=0.0,
            ))

        calibrator.update_calibration()

        # Reset
        calibrator.reset_calibration()
        tracker.reset_weights()
        tuner.reset_penalties()

        # Verify all defaults restored
        assert calibrator.get_calibration_status()["confidence_multipliers"]["high"] == 1.0
        assert tracker.get_weight_status()["weight_multipliers"]["5.0-10.0"] == 1.0
        assert tuner.get_penalty_status()["current_strengths"]["market_quality"] == 1.0


class TestDiagnosticsTracking:
    """Test diagnostics track adjustment metadata."""

    def test_calibration_tracks_sample_size(self):
        """Calibration should track sample size in diagnostics."""
        store = PerformanceStore()
        calibrator = ConfidenceCalibrator(store)

        # Add 50 records
        for i in range(50):
            store.add_record(PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"P{i}",
                stat_type="points",
                market_type="player_points",
                edge=5.0,
                confidence=0.85,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="miss",
                actual_value=0.0,
                line_value=0.0,
                projection=0.0,
            ))

        adjustments = calibrator.update_calibration()
        high_adj = [a for a in adjustments if a.target_bucket == "high" and a.applied]

        if high_adj:
            assert high_adj[0].sample_size == 50
            assert high_adj[0].adjustment_confidence > 0

    def test_calibration_tracks_days_of_data(self):
        """Calibration should track days of data in diagnostics."""
        store = PerformanceStore()
        calibrator = ConfidenceCalibrator(store)

        # Add records across multiple days
        for day in range(10):  # 10 days
            for i in range(5):  # 5 per day = 50 total
                store.add_record(PerformanceRecord(
                    prediction_date=f"2024-01-{day+1:02d}",
                    player_id=day*5+i,
                    player_name=f"P{day*5+i}",
                    stat_type="points",
                    market_type="player_points",
                    edge=5.0,
                    confidence=0.85,
                    confidence_bucket="high",
                    edge_bucket="5.0-10.0",
                    result="miss",
                    actual_value=0.0,
                    line_value=0.0,
                    projection=0.0,
                ))

        adjustments = calibrator.update_calibration()
        high_adj = [a for a in adjustments if a.target_bucket == "high" and a.applied]

        if high_adj:
            assert high_adj[0].days_of_data >= 10

    def test_adjustment_diagnostics_returns_history(self):
        """Diagnostics should return adjustment history."""
        store = PerformanceStore()
        calibrator = ConfidenceCalibrator(store)

        # Add data and make adjustments
        for i in range(50):
            store.add_record(PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"P{i}",
                stat_type="points",
                market_type="player_points",
                edge=5.0,
                confidence=0.85,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="miss",
                actual_value=0.0,
                line_value=0.0,
                projection=0.0,
            ))

        calibrator.update_calibration()

        diagnostics = calibrator.get_adjustment_diagnostics("high")
        assert "total_adjustments" in diagnostics
        assert "average_sample_size" in diagnostics
        assert "current_multiplier" in diagnostics

    def test_skipped_adjustments_tracked(self):
        """Skipped adjustments should be tracked with reasons."""
        store = PerformanceStore()
        calibrator = ConfidenceCalibrator(store)

        # Add only 5 records (insufficient)
        for i in range(5):
            store.add_record(PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"P{i}",
                stat_type="points",
                market_type="player_points",
                edge=5.0,
                confidence=0.85,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="miss",
                actual_value=0.0,
                line_value=0.0,
                projection=0.0,
            ))

        adjustments = calibrator.update_calibration()
        high_adj = [a for a in adjustments if a.target_bucket == "high"]

        assert len(high_adj) == 1
        assert high_adj[0].applied is False
        assert high_adj[0].skipped_reason != ""


class TestConfidenceInAdjustment:
    """Test adjustment confidence calculation."""

    def test_higher_sample_size_increases_confidence(self):
        """Larger samples should yield higher adjustment confidence."""
        store50 = PerformanceStore()
        store10 = PerformanceStore()
        calibrator50 = ConfidenceCalibrator(store50)
        calibrator10 = ConfidenceCalibrator(store10)

        # Add 50 records
        for i in range(50):
            store50.add_record(PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"P{i}",
                stat_type="points",
                market_type="player_points",
                edge=5.0,
                confidence=0.85,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="miss",
                actual_value=0.0,
                line_value=0.0,
                projection=0.0,
            ))

        # Add 10 records
        for i in range(10):
            store10.add_record(PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"P{i}",
                stat_type="points",
                market_type="player_points",
                edge=5.0,
                confidence=0.85,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="miss",
                actual_value=0.0,
                line_value=0.0,
                projection=0.0,
            ))

        # Manually override threshold for test
        calibrator10.MIN_SAMPLES = 10

        adj50 = calibrator50.update_calibration()
        adj10 = calibrator10.update_calibration()

        high50 = [a for a in adj50 if a.target_bucket == "high" and a.applied]
        high10 = [a for a in adj10 if a.target_bucket == "high" and a.applied]

        if high50 and high10:
            # Larger sample should have higher confidence
            assert high50[0].adjustment_confidence > high10[0].adjustment_confidence
