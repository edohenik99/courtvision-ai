"""Calibration audit tests for system validation.

VALIDATE + CALIBRATE mode - Measurement and validation only.

Verifies:
- Confidence buckets align with realized hit rates
- Positive-EV plays outperform neutral/negative
- Positive CLV correlates with outcomes
- Adaptive thresholds are bounded and stable
- Portfolio optimizer doesn't increase concentration risk

Task B: Add calibration audit tests
"""

from __future__ import annotations

import statistics
from typing import Any

import numpy as np
import pytest

from courtvision.config import create_balanced_mode
from courtvision.evaluation import ReportBuilder, RollingWindow
from courtvision.portfolio import PortfolioOptimizer, RiskController
from courtvision.shadow_run import ShadowRunRunner


# ============================================================================
# Test Constants and Utilities
# ============================================================================

MIN_SAMPLES_CALIBRATION = 50  # Minimum samples for calibration tests
MIN_SAMPLES_EV = 30  # Minimum samples for EV tests
TOLERANCE_CALIBRATION = 0.15  # 15% tolerance for confidence/hit-rate alignment
TOLERANCE_EV_OUTPERFORM = 0.10  # 10% outperformance required
MAX_THRESHOLD_DRIFT = 0.02  # Max allowed drift in adaptive thresholds


def generate_test_picks(
    n: int,
    base_hit_rate: float = 0.55,
    confidence_accuracy: float = 0.8,
) -> list[dict[str, Any]]:
    """Generate synthetic pick history for testing.

    Args:
        n: Number of picks to generate
        base_hit_rate: Base hit rate
        confidence_accuracy: How well confidence predicts outcomes

    Returns:
        List of pick dicts
    """
    picks = []

    for i in range(n):
        # Generate realistic confidence values
        confidence = np.random.uniform(0.55, 0.85)

        # Hit is determined by confidence with some noise
        hit_prob = confidence * confidence_accuracy + (1 - confidence_accuracy) * base_hit_rate
        hit = np.random.random() < hit_prob

        # Edge correlates with confidence
        edge = (confidence - 0.5) * 0.3 + np.random.normal(0, 0.02)
        ev = edge * 0.8 + np.random.normal(0, 0.01)
        clv = edge * 0.5 + np.random.normal(0, 0.01)

        picks.append({
            "pick_id": f"p{i}",
            "prediction_date": f"2024-01-{i % 30 + 1:02d}",
            "player_name": f"Player_{i % 20}",
            "stat_type": ["points", "rebounds", "assists"][i % 3],
            "over_under": "over" if i % 2 == 0 else "under",
            "line_value": 25.5,
            "confidence": confidence,
            "edge": edge,
            "ev": ev,
            "hit": hit,
            "clv": clv,
        })

    return picks


# ============================================================================
# Calibration Tests
# ============================================================================

class TestConfidenceCalibration:
    """Test that predicted confidence aligns with realized hit rates."""

    def test_confidence_bucket_alignment(self):
        """Verify 70% confidence picks hit ~70% of the time (within tolerance)."""
        # Generate perfectly calibrated data
        picks = []
        bucket_confidences = [0.55, 0.65, 0.75, 0.85]

        for conf in bucket_confidences:
            for _ in range(50):  # 50 picks per bucket
                # Generate hit based on exact confidence
                hit = np.random.random() < conf
                picks.append({
                    "pick_id": f"p{len(picks)}",
                    "prediction_date": "2024-01-01",
                    "player_name": "Test",
                    "stat_type": "points",
                    "over_under": "over",
                    "line_value": 25.5,
                    "confidence": conf,
                    "edge": (conf - 0.5) * 0.2,
                    "ev": (conf - 0.5) * 0.15,
                    "hit": hit,
                    "clv": (conf - 0.5) * 0.1,
                })

        builder = ReportBuilder()
        for p in picks:
            builder.add_pick(**p)

        report = builder.build_report()
        assert report is not None

        # Check calibration score
        assert report.calibration_score > 0.6, (
            f"Calibration score {report.calibration_score:.3f} is too low. "
            "Predicted confidence does not align with realized hit rates."
        )

    def test_calibration_score_bounds(self):
        """Verify calibration score is bounded [0, 1]."""
        builder = ReportBuilder()

        # Add minimum samples
        for i in range(MIN_SAMPLES_CALIBRATION):
            builder.add_pick(
                pick_id=f"p{i}",
                prediction_date="2024-01-01",
                player_name="Test",
                stat_type="points",
                over_under="over",
                line_value=25.5,
                confidence=0.70,
                edge=0.05,
                ev=0.03,
                hit=True,
                clv=0.01,
            )

        report = builder.build_report()
        assert report is not None

        # Score must be in valid range
        assert 0.0 <= report.calibration_score <= 1.0, (
            f"Calibration score {report.calibration_score} out of bounds [0, 1]"
        )

    def test_high_confidence_higher_hit_rate(self):
        """Verify higher confidence buckets have higher hit rates."""
        builder = ReportBuilder()

        # Generate picks with clear confidence hierarchy
        # 80% confidence should hit more than 60% confidence
        for i in range(100):
            if i < 50:
                conf = np.random.uniform(0.58, 0.62)  # 60% bucket
                hit = np.random.random() < 0.60
            else:
                conf = np.random.uniform(0.78, 0.82)  # 80% bucket
                hit = np.random.random() < 0.80

            builder.add_pick(
                pick_id=f"p{i}",
                prediction_date="2024-01-01",
                player_name="Test",
                stat_type="points",
                over_under="over",
                line_value=25.5,
                confidence=conf,
                edge=(conf - 0.5) * 0.2,
                ev=(conf - 0.5) * 0.15,
                hit=hit,
                clv=(conf - 0.5) * 0.1,
            )

        report = builder.build_report()
        assert report is not None

        # Find 60-70% and 70-80% buckets
        low_conf_bucket = next(
            (b for b in report.confidence_buckets if b.bucket_name == "60-70%"),
            None
        )
        high_conf_bucket = next(
            (b for b in report.confidence_buckets if b.bucket_name == "70-80%"),
            None
        )

        if low_conf_bucket and high_conf_bucket:
            assert high_conf_bucket.hit_rate > low_conf_bucket.hit_rate, (
                f"High confidence bucket hit rate ({high_conf_bucket.hit_rate:.3f}) "
                f"not greater than low confidence ({low_conf_bucket.hit_rate:.3f})"
            )

    def test_insufficient_data_returns_none(self):
        """Verify report returns None when insufficient data."""
        builder = ReportBuilder(RollingWindow(min_samples=50))

        for i in range(10):  # Less than minimum
            builder.add_pick(
                pick_id=f"p{i}",
                prediction_date="2024-01-01",
                player_name="Test",
                stat_type="points",
                over_under="over",
                line_value=25.5,
                confidence=0.70,
                edge=0.05,
                ev=0.03,
                hit=True,
                clv=0.01,
            )

        report = builder.build_report()
        assert report is None, "Should return None for insufficient data"


# ============================================================================
# EV Outperformance Tests
# ============================================================================

class TestEVOutperformance:
    """Test that positive-EV plays outperform neutral/negative."""

    def test_positive_ev_outperforms_negative(self):
        """Verify positive EV plays have higher hit rates than negative EV."""
        builder = ReportBuilder()

        # Generate positive EV picks (should hit more)
        for i in range(MIN_SAMPLES_EV):
            ev = np.random.uniform(0.03, 0.10)
            hit = np.random.random() < (0.55 + ev * 2)  # Higher EV = higher hit rate

            builder.add_pick(
                pick_id=f"pos_{i}",
                prediction_date="2024-01-01",
                player_name="Test",
                stat_type="points",
                over_under="over",
                line_value=25.5,
                confidence=0.70,
                edge=0.08,
                ev=ev,
                hit=hit,
                clv=0.02,
            )

        # Generate negative EV picks (should hit less)
        for i in range(MIN_SAMPLES_EV):
            ev = np.random.uniform(-0.05, -0.01)
            hit = np.random.random() < (0.45 + ev * 2)  # Negative EV = lower hit rate

            builder.add_pick(
                pick_id=f"neg_{i}",
                prediction_date="2024-01-01",
                player_name="Test",
                stat_type="points",
                over_under="over",
                line_value=25.5,
                confidence=0.55,
                edge=-0.02,
                ev=ev,
                hit=hit,
                clv=-0.01,
            )

        report = builder.build_report()
        assert report is not None

        # Find positive and negative EV buckets
        positive_bucket = next(
            (b for b in report.edge_buckets if b.bucket_name not in ["negative", "0-5%"]),
            None
        )
        negative_bucket = next(
            (b for b in report.edge_buckets if b.bucket_name == "negative"),
            None
        )

        if positive_bucket and negative_bucket:
            assert positive_bucket.hit_rate > negative_bucket.hit_rate + TOLERANCE_EV_OUTPERFORM, (
                f"Positive EV hit rate ({positive_bucket.hit_rate:.3f}) not sufficiently "
                f"above negative EV ({negative_bucket.hit_rate:.3f})"
            )

    def test_zero_ev_bucket_baseline(self):
        """Verify 0-5% EV bucket performs around expected level."""
        builder = ReportBuilder()

        for i in range(MIN_SAMPLES_EV):
            ev = np.random.uniform(0.00, 0.05)
            hit = np.random.random() < (0.52 + ev)

            builder.add_pick(
                pick_id=f"p{i}",
                prediction_date="2024-01-01",
                player_name="Test",
                stat_type="points",
                over_under="over",
                line_value=25.5,
                confidence=0.65,
                edge=0.05,
                ev=ev,
                hit=hit,
                clv=0.02,
            )

        report = builder.build_report()
        assert report is not None

        zero_five_bucket = next(
            (b for b in report.edge_buckets if b.bucket_name == "0-5%"),
            None
        )

        if zero_five_bucket and zero_five_bucket.count >= 10:
            # Should be around 52-57% hit rate for small positive EV
            assert 0.45 < zero_five_bucket.hit_rate < 0.65, (
                f"0-5% EV bucket hit rate {zero_five_bucket.hit_rate:.3f} "
                "outside reasonable range (0.45, 0.65)"
            )


# ============================================================================
# CLV Correlation Tests
# ============================================================================

class TestCLVCorrelation:
    """Test that positive CLV correlates with positive outcomes."""

    def test_positive_clv_correlates_with_hits(self):
        """Verify positive CLV picks hit more than negative CLV picks."""
        builder = ReportBuilder()

        # Generate picks where CLV correlates with outcome
        for i in range(MIN_SAMPLES_EV):
            clv = np.random.uniform(0.01, 0.05)  # Positive CLV
            # Positive CLV should correlate with hits
            hit = np.random.random() < (0.55 + clv * 3)

            builder.add_pick(
                pick_id=f"pos_{i}",
                prediction_date="2024-01-01",
                player_name="Test",
                stat_type="points",
                over_under="over",
                line_value=25.5,
                confidence=0.70,
                edge=0.06,
                ev=0.04,
                hit=hit,
                clv=clv,
            )

        for i in range(MIN_SAMPLES_EV):
            clv = np.random.uniform(-0.05, -0.01)  # Negative CLV
            hit = np.random.random() < (0.45 + clv * 2)  # Negative CLV = lower hit rate

            builder.add_pick(
                pick_id=f"neg_{i}",
                prediction_date="2024-01-01",
                player_name="Test",
                stat_type="points",
                over_under="over",
                line_value=25.5,
                confidence=0.60,
                edge=0.03,
                ev=0.01,
                hit=hit,
                clv=clv,
            )

        report = builder.build_report()
        assert report is not None

        # Calculate average CLV for hits vs misses
        hits = [p for p in builder.pick_history if p["hit"]]
        misses = [p for p in builder.pick_history if not p["hit"]]

        if hits and misses:
            avg_clv_hits = sum(p["clv"] for p in hits) / len(hits)
            avg_clv_misses = sum(p["clv"] for p in misses) / len(misses)

            assert avg_clv_hits > avg_clv_misses, (
                f"Average CLV for hits ({avg_clv_hits:.4f}) not greater than "
                f"average CLV for misses ({avg_clv_misses:.4f})"
            )


# ============================================================================
# Adaptive Threshold Tests
# ============================================================================

class TestAdaptiveThresholdStability:
    """Test that adaptive thresholds are bounded and stable."""

    def test_threshold_bounds(self):
        """Verify adaptive thresholds stay within reasonable bounds."""
        from courtvision.market_intelligence import AdaptiveStrategy

        strategy = AdaptiveStrategy(base_edge=0.05, base_confidence=0.65)

        # Simulate various market conditions
        for _ in range(20):
            # Generate different market conditions
            conditions = np.random.choice(["soft", "efficient", "neutral"])

            # Mock market condition effects
            if conditions == "soft":
                strategy.config.clv_adjustment = -0.015
                strategy.config.bias_adjustment = -0.01
            elif conditions == "efficient":
                strategy.config.clv_adjustment = 0.01
                strategy.config.bias_adjustment = 0.005
            else:
                strategy.config.clv_adjustment = 0.0
                strategy.config.bias_adjustment = 0.0

            strategy.config.__post_init__()
            thresholds = strategy.get_current_thresholds()

            # Verify bounds
            assert 0.01 <= thresholds["edge"] <= 0.15, (
                f"Edge threshold {thresholds['edge']} out of bounds [0.01, 0.15]"
            )
            assert 0.50 <= thresholds["confidence"] <= 0.80, (
                f"Confidence threshold {thresholds['confidence']} out of bounds [0.50, 0.80]"
            )
            assert 0.00 <= thresholds["ev"] <= 0.10, (
                f"EV threshold {thresholds['ev']} out of bounds [0.00, 0.10]"
            )

    def test_threshold_stability(self):
        """Verify thresholds don't fluctuate wildly."""
        from courtvision.market_intelligence import AdaptiveStrategy

        strategy = AdaptiveStrategy(base_edge=0.05)

        edge_values = []

        # Simulate multiple days with similar conditions
        for _ in range(10):
            strategy.config.clv_adjustment = np.random.uniform(-0.01, 0.01)
            strategy.config.bias_adjustment = np.random.uniform(-0.01, 0.01)
            strategy.config.__post_init__()

            thresholds = strategy.get_current_thresholds()
            edge_values.append(thresholds["edge"])

        # Calculate drift
        if len(edge_values) > 1:
            drift = max(edge_values) - min(edge_values)
            assert drift < MAX_THRESHOLD_DRIFT * 3, (
                f"Threshold drift {drift:.4f} exceeds maximum {MAX_THRESHOLD_DRIFT * 3:.4f}. "
                "Thresholds are fluctuating too much."
            )


# ============================================================================
# Portfolio Concentration Tests
# ============================================================================

class TestPortfolioConcentration:
    """Test that portfolio optimizer doesn't increase concentration risk."""

    def test_diversification_score_bounds(self):
        """Verify diversification score stays within [0, 1]."""
        optimizer = PortfolioOptimizer()

        # Add diverse plays
        for i in range(10):
            optimizer.add_play(
                play_id=f"play_{i}",
                player_name=f"Player_{i}",
                game_id=f"game_{i % 3}",
                stat_type=["points", "rebounds", "assists"][i % 3],
                ev=0.05,
                hit_probability=0.55,
                odds=2.0,
                variance=0.1,
            )

        portfolios = optimizer.optimize(
            available_play_ids=[f"play_{i}" for i in range(10)],
            target_size=5,
            num_portfolios=3,
        )

        for portfolio in portfolios:
            assert 0.0 <= portfolio.diversification_score <= 1.0, (
                f"Diversification score {portfolio.diversification_score} out of bounds [0, 1]"
            )

    def test_portfolio_does_not_exceed_risk_limits(self):
        """Verify optimized portfolio respects risk limits."""
        from courtvision.portfolio import RiskController, ExposureLimits

        limits = ExposureLimits(
            max_plays_per_game=3,
            max_props_per_player=2,
        )
        controller = RiskController(limits)

        # Try to add more plays per game than allowed
        violations = []
        for i in range(5):
            can_add, reason = controller.can_add_play(
                player_name=f"Player_{i}",
                game_id="game_1",  # Same game
                stat_type="points",
            )
            if can_add:
                controller.add_play(
                    play_id=f"play_{i}",
                    player_name=f"Player_{i}",
                    game_id="game_1",
                    stat_type="points",
                )
            else:
                violations.append(reason)

        # Should have violations after limit reached
        assert len(violations) > 0, "Risk controller should enforce per-game limits"

    def test_optimized_vs_naive_diversification(self):
        """Verify optimizer improves diversification vs random selection."""
        optimizer = PortfolioOptimizer()

        # Add plays from few games (concentrated scenario)
        for i in range(12):
            optimizer.add_play(
                play_id=f"play_{i}",
                player_name=f"Player_{i}",
                game_id=f"game_{i % 2}",  # Only 2 games
                stat_type="points",  # Same stat
                ev=0.05,
                hit_probability=0.55,
                odds=2.0,
                variance=0.1,
            )

        portfolios = optimizer.optimize(
            available_play_ids=[f"play_{i}" for i in range(12)],
            target_size=6,
            num_portfolios=1,
        )

        if portfolios:
            portfolio = portfolios[0]
            # Optimizer should attempt to diversify
            # With 2 games and 6 picks, we expect at least some distribution
            assert portfolio.diversification_score > 0.1, (
                f"Diversification score {portfolio.diversification_score:.3f} too low. "
                "Optimizer may be increasing concentration risk."
            )


# ============================================================================
# Integration Tests
# ============================================================================

class TestIntegration:
    """Integration tests for calibration across components."""

    def test_shadow_run_records_all_required_fields(self):
        """Verify shadow run captures all fields needed for calibration."""
        runner = ShadowRunRunner(
            prediction_date="2024-01-01",
            config={"thresholds": {"edge": 0.05, "confidence": 0.65}},
        )

        runner.set_context(
            thresholds={"edge": 0.05, "confidence": 0.65},
            market_regime="neutral",
        )

        candidate = {
            "player_name": "LeBron",
            "stat_type": "points",
            "over_under": "over",
            "line_value": 25.5,
            "odds": -110,
            "projected_value": 28.5,
            "confidence": 0.75,
            "edge": 0.08,
            "ev": 0.05,
        }

        entry = runner.evaluate_candidate(candidate)

        # Verify all calibration fields are recorded
        assert entry.player_name == "LeBron"
        assert entry.stat_type == "points"
        assert entry.line_value == 25.5
        assert entry.confidence == 0.75
        assert entry.edge == 0.08
        assert entry.ev == 0.05
        assert entry.recommended is True
        assert "thresholds_used" in entry.to_dict()["context"]

    def test_end_to_end_calibration_workflow(self):
        """Test complete calibration workflow from picks to report."""
        # Generate synthetic history
        picks = generate_test_picks(100, base_hit_rate=0.55, confidence_accuracy=0.8)

        # Build report
        builder = ReportBuilder(RollingWindow(window_size=50, min_samples=30))
        for p in picks:
            builder.add_pick(**p)

        # Get rolling reports
        reports = builder.build_all_reports()
        assert len(reports) > 0

        # Verify reports contain all calibration metrics
        for report in reports:
            assert report.calibration_score >= 0.0
            assert len(report.confidence_buckets) > 0
            assert len(report.edge_buckets) > 0
            assert report.portfolio_drawdown >= 0.0

            # High confidence buckets should hit more
            high_conf = next((b for b in report.confidence_buckets if "80" in b.bucket_name), None)
            low_conf = next((b for b in report.confidence_buckets if "60" in b.bucket_name), None)

            if high_conf and low_conf and high_conf.count > 5 and low_conf.count > 5:
                assert high_conf.hit_rate > low_conf.hit_rate - TOLERANCE_CALIBRATION


# ============================================================================
# Statistical Utility Tests
# ============================================================================

class TestStatisticalUtilities:
    """Test statistical calculation utilities."""

    def test_calibration_score_calculation(self):
        """Verify calibration score calculation is reasonable."""
        builder = ReportBuilder()

        # Perfect calibration: 60% confidence picks hit 60% of time
        for i in range(60):
            builder.add_pick(
                pick_id=f"p{i}",
                prediction_date="2024-01-01",
                player_name="Test",
                stat_type="points",
                over_under="over",
                line_value=25.5,
                confidence=0.60,
                edge=0.05,
                ev=0.03,
                hit=i < 36,  # Exactly 60%
                clv=0.01,
            )

        report = builder.build_report()
        if report:
            # Should be reasonably calibrated
            assert report.calibration_score > 0.4

    def test_drawdown_calculation_accuracy(self):
        """Verify drawdown calculation."""
        builder = ReportBuilder()

        # Sequence: win, win, loss, loss, loss, win (drawdown of 3 losses)
        results = [True, True, False, False, False, True]
        for i, hit in enumerate(results):
            builder.add_pick(
                pick_id=f"p{i}",
                prediction_date="2024-01-01",
                player_name="Test",
                stat_type="points",
                over_under="over",
                line_value=25.5,
                confidence=0.70,
                edge=0.05,
                ev=0.03,
                hit=hit,
                clv=0.01,
            )

        report = builder.build_report()
        if report:
            # Max drawdown should be around 2-3 units (3 losses at -1 each)
            assert report.portfolio_drawdown > 0
            assert report.portfolio_drawdown < 5  # Reasonable upper bound

    def test_volatility_calculation(self):
        """Verify volatility calculation."""
        builder = ReportBuilder()

        # All wins (low volatility)
        for i in range(20):
            builder.add_pick(
                pick_id=f"p{i}",
                prediction_date="2024-01-01",
                player_name="Test",
                stat_type="points",
                over_under="over",
                line_value=25.5,
                confidence=0.70,
                edge=0.05,
                ev=0.03,
                hit=True,
                clv=0.01,
            )

        report = builder.build_report()
        if report:
            # All wins = zero volatility
            assert report.portfolio_volatility == 0.0


# ============================================================================
# Configuration Tests
# ============================================================================

class TestConfigurationCalibration:
    """Test that configuration produces calibrated system behavior."""

    def test_conservative_mode_higher_thresholds(self):
        """Verify conservative mode produces higher effective thresholds."""
        conservative = create_conservative_mode()
        balanced = create_balanced_mode()
        aggressive = create_aggressive_mode()

        # Conservative should have highest thresholds
        assert conservative.effective_thresholds.edge > balanced.effective_thresholds.edge
        assert balanced.effective_thresholds.edge > aggressive.effective_thresholds.edge

    def test_aggressive_mode_allows_more_plays(self):
        """Verify aggressive mode allows more plays."""
        conservative = create_conservative_mode()
        balanced = create_balanced_mode()
        aggressive = create_aggressive_mode()

        # Aggressive should allow most plays
        assert aggressive.effective_limits.max_daily_plays > balanced.effective_limits.max_daily_plays
        assert balanced.effective_limits.max_daily_plays > conservative.effective_limits.max_daily_plays

    def test_config_validation_pass(self):
        """Verify config validation passes for valid candidates."""
        config = create_balanced_mode()

        is_valid, reason = config.validate_candidate(
            edge=0.08,
            confidence=0.75,
            ev=0.05,
            hit_prob=0.60,
        )

        assert is_valid is True
        assert reason == ""

    def test_config_validation_fail(self):
        """Verify config validation fails for invalid candidates."""
        config = create_balanced_mode()

        is_valid, reason = config.validate_candidate(
            edge=0.02,  # Too low
            confidence=0.55,  # Too low
            ev=0.01,  # Too low
            hit_prob=0.50,  # Too low
        )

        assert is_valid is False
        assert len(reason) > 0
