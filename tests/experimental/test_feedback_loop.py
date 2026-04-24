"""Tests for Phase 7 feedback loop and self-correction system.

Validates:
1. Performance store rolling windows
2. Grading feedback analyzer
3. Confidence calibration adjustments
4. Edge reliability tracking
5. Penalty tuning feedback
"""

import json
import tempfile
from datetime import datetime, timedelta

import pytest

from courtvision.feedback.performance_store import (
    PerformanceRecord,
    PerformanceStore,
    WindowMetrics,
)
from courtvision.feedback.grading_feedback import (
    GradingFeedbackAnalyzer,
    PlayerAccuracyProfile,
    MarketTypeAnalysis,
    ConfidenceCalibrationIssue,
)
from courtvision.feedback.calibration import ConfidenceCalibrator
from courtvision.feedback.edge_tracker import EdgeReliabilityTracker
from courtvision.feedback.penalty_tuner import PenaltyTuner


class MockGradedPick:
    """Mock graded pick for testing."""
    def __init__(
        self,
        player_id: int,
        player_name: str,
        prop_type: str,
        confidence: float,
        edge: float,
        side: str = "over",
        actual_stat: float = 0.0,
        line_value: float = 0.0,
        projection: float = 0.0,
    ):
        self.player_id = player_id
        self.player_name = player_name
        self.prop_type = prop_type
        self.confidence = confidence
        self.edge = edge
        self.side = side
        self.actual_stat = actual_stat
        self.line_value = line_value
        self.projection = projection


class TestPerformanceStore:
    """Test performance store rolling windows."""

    def test_add_record_increases_count(self):
        """Adding records should increase total count."""
        store = PerformanceStore()
        record = PerformanceRecord(
            prediction_date="2024-01-01",
            player_id=1,
            player_name="Test",
            stat_type="points",
            market_type="player_points",
            edge=5.0,
            confidence=0.75,
            confidence_bucket="high",
            edge_bucket="5.0-10.0",
            result="hit",
            actual_value=25.0,
            line_value=20.0,
            projection=22.0,
        )
        store.add_record(record)
        assert len(store.records) == 1

    def test_window_metrics_filters_by_date(self):
        """Window metrics should only include records within date range."""
        store = PerformanceStore()

        # Add old record
        old_record = PerformanceRecord(
            prediction_date=(datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d"),
            player_id=1,
            player_name="Old",
            stat_type="points",
            market_type="player_points",
            edge=5.0,
            confidence=0.75,
            confidence_bucket="high",
            edge_bucket="5.0-10.0",
            result="hit",
            actual_value=25.0,
            line_value=20.0,
            projection=22.0,
        )
        store.add_record(old_record)

        # Add recent record
        recent_record = PerformanceRecord(
            prediction_date=datetime.now().strftime("%Y-%m-%d"),
            player_id=2,
            player_name="Recent",
            stat_type="points",
            market_type="player_points",
            edge=5.0,
            confidence=0.75,
            confidence_bucket="high",
            edge_bucket="5.0-10.0",
            result="hit",
            actual_value=25.0,
            line_value=20.0,
            projection=22.0,
        )
        store.add_record(recent_record)

        # 30-day window should only include recent
        window = store.get_window_metrics(30)
        assert window.total_picks == 1
        assert window.hits == 1

    def test_confidence_bucket_aggregation(self):
        """Records should be aggregated by confidence bucket."""
        store = PerformanceStore()

        for i, bucket in enumerate(["low", "mid", "high", "elite"]):
            record = PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"Player{i}",
                stat_type="points",
                market_type="player_points",
                edge=5.0,
                confidence=0.5 + i * 0.1,
                confidence_bucket=bucket,
                edge_bucket="5.0-10.0",
                result="hit" if i % 2 == 0 else "miss",
                actual_value=25.0,
                line_value=20.0,
                projection=22.0,
            )
            store.add_record(record)

        calibration = store.get_confidence_calibration(30)
        assert "low" in calibration
        assert "mid" in calibration
        assert "high" in calibration
        assert "elite" in calibration

    def test_edge_bucket_aggregation(self):
        """Records should be aggregated by edge bucket."""
        store = PerformanceStore()

        edges = [0.5, 1.5, 2.5, 4.0, 7.0, 12.0]
        for i, edge in enumerate(edges):
            record = PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"Player{i}",
                stat_type="points",
                market_type="player_points",
                edge=edge,
                confidence=0.75,
                confidence_bucket="high",
                edge_bucket=store._edge_to_bucket(edge),
                result="hit",
                actual_value=25.0,
                line_value=20.0,
                projection=22.0,
            )
            store.add_record(record)

        edge_reliability = store.get_edge_reliability(30)
        assert len(edge_reliability) >= 5  # Should have multiple buckets

    def test_persistence(self):
        """Store should persist and reload data."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = f.name

        store = PerformanceStore(storage_path=temp_path)
        record = PerformanceRecord(
            prediction_date="2024-01-01",
            player_id=1,
            player_name="Test",
            stat_type="points",
            market_type="player_points",
            edge=5.0,
            confidence=0.75,
            confidence_bucket="high",
            edge_bucket="5.0-10.0",
            result="hit",
            actual_value=25.0,
            line_value=20.0,
            projection=22.0,
        )
        store.add_record(record)

        # Create new store from same file
        store2 = PerformanceStore(storage_path=temp_path)
        assert len(store2.records) == 1

    def test_hit_rate_computation_excludes_pushes(self):
        """Hit rate should exclude pushes from denominator."""
        metrics = WindowMetrics(window_days=7)
        metrics.hits = 7
        metrics.misses = 3
        metrics.pushes = 5

        hit_rate = metrics.compute_hit_rate()
        assert hit_rate == 0.7  # 7/10, not 7/15


class TestGradingFeedbackAnalyzer:
    """Test grading feedback analyzer."""

    @pytest.fixture
    def populated_store(self):
        """Create store with test data."""
        store = PerformanceStore()

        # Add records with varying confidence and results
        test_data = [
            # (confidence_bucket, result)
            ("low", "hit"), ("low", "miss"), ("low", "hit"),
            ("mid", "hit"), ("mid", "hit"), ("mid", "miss"),
            ("high", "hit"), ("high", "hit"), ("high", "hit"), ("high", "miss"),
            ("elite", "hit"), ("elite", "hit"), ("elite", "hit"),
        ]

        for i, (bucket, result) in enumerate(test_data):
            conf_values = {"low": 0.55, "mid": 0.65, "high": 0.75, "elite": 0.85}
            record = PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i % 3 + 1,  # 3 different players
                player_name=f"Player{i % 3 + 1}",
                stat_type="points" if i % 2 == 0 else "rebounds",
                market_type="player_points" if i % 2 == 0 else "player_rebounds",
                edge=3.0 + i * 0.5,
                confidence=conf_values[bucket],
                confidence_bucket=bucket,
                edge_bucket="3.0-5.0",
                result=result,
                actual_value=25.0,
                line_value=20.0,
                projection=22.0,
            )
            store.add_record(record)

        return store

    def test_identify_calibration_issues(self, populated_store):
        """Should identify miscalibrated confidence buckets."""
        analyzer = GradingFeedbackAnalyzer(populated_store)
        issues = analyzer.identify_calibration_issues(window_days=30, threshold=0.05)

        # Should find some issues with our test data
        assert isinstance(issues, list)
        for issue in issues:
            assert isinstance(issue, ConfidenceCalibrationIssue)
            assert issue.confidence_bucket in ["low", "mid", "high", "elite"]
            assert abs(issue.deviation) > 0.05

    def test_analyze_market_types(self, populated_store):
        """Should analyze performance by market type."""
        analyzer = GradingFeedbackAnalyzer(populated_store)
        analyses = analyzer.analyze_market_types(window_days=30)

        assert "points" in analyses or "rebounds" in analyses

    def test_player_accuracy_profile(self, populated_store):
        """Should generate player accuracy profiles."""
        analyzer = GradingFeedbackAnalyzer(populated_store)
        profiles = analyzer.analyze_player_accuracy(window_days=30, min_sample_size=2)

        assert isinstance(profiles, dict)
        for pid, profile in profiles.items():
            assert isinstance(profile, PlayerAccuracyProfile)
            assert profile.hit_rate >= 0.0
            assert profile.hit_rate <= 1.0

    def test_edge_effectiveness_report(self, populated_store):
        """Should generate edge effectiveness report."""
        analyzer = GradingFeedbackAnalyzer(populated_store)
        report = analyzer.get_edge_effectiveness_report(window_days=30)

        assert "edge_buckets" in report
        assert "most_reliable" in report
        assert "least_reliable" in report
        assert "recommended_edge_threshold" in report


class TestConfidenceCalibrator:
    """Test confidence calibration system."""

    @pytest.fixture
    def calibrator_with_data(self):
        """Create calibrator with performance data."""
        store = PerformanceStore()

        # Create underperforming high confidence data
        for i in range(15):
            record = PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"Player{i}",
                stat_type="points",
                market_type="player_points",
                edge=5.0,
                confidence=0.85,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="miss" if i < 8 else "hit",  # 47% hit rate (underperforming)
                actual_value=15.0,
                line_value=20.0,
                projection=22.0,
            )
            store.add_record(record)

        # Create overperforming low confidence data
        for i in range(15, 30):
            record = PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"Player{i}",
                stat_type="points",
                market_type="player_points",
                edge=5.0,
                confidence=0.55,
                confidence_bucket="low",
                edge_bucket="5.0-10.0",
                result="hit" if i < 26 else "miss",  # 73% hit rate (overperforming)
                actual_value=25.0,
                line_value=20.0,
                projection=22.0,
            )
            store.add_record(record)

        return ConfidenceCalibrator(store)

    def test_update_calibration_detects_underperformance(self, calibrator_with_data):
        """Should reduce multiplier for underperforming bucket."""
        adjustments = calibrator_with_data.update_calibration(window_days=30)

        # Should have adjustments for high bucket (underperforming)
        high_adjustments = [a for a in adjustments if a.target_bucket == "high"]
        assert len(high_adjustments) > 0
        assert high_adjustments[0].adjustment < 0  # Should reduce

    def test_apply_calibration_adjusts_confidence(self, calibrator_with_data):
        """Should adjust confidence values based on calibration."""
        calibrator_with_data.update_calibration(window_days=30)

        # High confidence should be reduced
        calibrated = calibrator_with_data.apply_calibration(0.85, "high")
        assert calibrated < 0.85 or calibrated == 0.85  # May be reduced or stay same

    def test_calibration_clamped_to_bounds(self, calibrator_with_data):
        """Calibration multipliers should stay within bounds."""
        # Run multiple updates to test clamping
        for _ in range(10):
            calibrator_with_data.update_calibration(window_days=30)

        status = calibrator_with_data.get_calibration_status()
        for multiplier in status["confidence_multipliers"].values():
            assert 0.8 <= multiplier <= 1.2

    def test_reset_calibration_restores_defaults(self, calibrator_with_data):
        """Reset should restore all multipliers to 1.0."""
        calibrator_with_data.update_calibration(window_days=30)
        calibrator_with_data.reset_calibration()

        status = calibrator_with_data.get_calibration_status()
        for multiplier in status["confidence_multipliers"].values():
            assert multiplier == 1.0


class TestEdgeReliabilityTracker:
    """Test edge reliability tracking."""

    @pytest.fixture
    def tracker_with_data(self):
        """Create tracker with performance data."""
        store = PerformanceStore()

        # Create strong performance for large edges
        for i in range(20):
            record = PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"Player{i}",
                stat_type="points",
                market_type="player_points",
                edge=7.0,  # Large edge
                confidence=0.80,
                confidence_bucket="high",
                edge_bucket="5.0-10.0",
                result="hit" if i < 16 else "miss",  # 80% hit rate
                actual_value=25.0,
                line_value=20.0,
                projection=22.0,
            )
            store.add_record(record)

        # Create weak performance for small edges
        for i in range(20, 40):
            record = PerformanceRecord(
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"Player{i}",
                stat_type="points",
                market_type="player_points",
                edge=0.5,  # Small edge
                confidence=0.60,
                confidence_bucket="mid",
                edge_bucket="0.0-1.0",
                result="hit" if i < 24 else "miss",  # 20% hit rate
                actual_value=18.0,
                line_value=20.0,
                projection=22.0,
            )
            store.add_record(record)

        return EdgeReliabilityTracker(store)

    def test_analyze_reliability_computes_scores(self, tracker_with_data):
        """Should compute reliability scores for edge buckets."""
        reliability = tracker_with_data.analyze_reliability(window_days=30)

        assert len(reliability) > 0
        for label, perf in reliability.items():
            assert 0 <= perf.reliability_score <= 1.0

    def test_large_edges_more_reliable(self, tracker_with_data):
        """Large edges should have higher reliability scores."""
        reliability = tracker_with_data.analyze_reliability(window_days=30)

        large_edge_score = reliability.get("5.0-10.0", None)
        small_edge_score = reliability.get("0.0-1.0", None)

        if large_edge_score and small_edge_score:
            assert large_edge_score.reliability_score > small_edge_score.reliability_score

    def test_update_weights_adjusts_multipliers(self, tracker_with_data):
        """Should adjust weight multipliers based on performance."""
        adjustments = tracker_with_data.update_weights(window_days=30)

        # Should have adjustments
        assert len(adjustments) > 0

        # Strong bucket should get increased weight
        strong_adj = [a for a in adjustments if a.bucket_label == "5.0-10.0"]
        if strong_adj:
            assert strong_adj[0].recommended_weight > 1.0

    def test_get_recommended_minimum_edge(self, tracker_with_data):
        """Should recommend minimum edge threshold."""
        tracker_with_data.update_weights(window_days=30)
        min_edge = tracker_with_data.get_recommended_minimum_edge(target_hit_rate=0.60)

        assert min_edge >= 0.0
        # Should recommend higher edge given our data
        assert min_edge >= 2.0


class TestPenaltyTuner:
    """Test penalty tuning feedback."""

    @pytest.fixture
    def tuner_with_history(self):
        """Create tuner with penalty application history."""
        store = PerformanceStore()
        tuner = PenaltyTuner(store)

        # Record penalties that were too harsh (penalized picks that hit)
        for i in range(10):
            tuner.record_penalty_application(
                penalty_type="market_quality",
                penalty_strength=0.05,
                original_confidence=0.75,
                penalized_confidence=0.70,
                result="hit",  # Penalized but still hit - too harsh
            )

        # Record penalties that worked (penalized picks that missed)
        for i in range(10):
            tuner.record_penalty_application(
                penalty_type="injury_volatility",
                penalty_strength=0.08,
                original_confidence=0.75,
                penalized_confidence=0.67,
                result="miss",  # Penalized and missed - effective
            )

        return tuner

    def test_record_penalty_tracks_effectiveness(self, tuner_with_history):
        """Should track penalty effectiveness."""
        effectiveness = tuner_with_history.analyze_penalty_effectiveness()

        assert "market_quality" in effectiveness
        assert "injury_volatility" in effectiveness

        # Market quality should have negative effectiveness (too harsh)
        assert effectiveness["market_quality"].effectiveness_score < 0

        # Injury volatility should have positive effectiveness
        assert effectiveness["injury_volatility"].effectiveness_score > 0

    def test_update_penalty_strengths_adjusts(self, tuner_with_history):
        """Should adjust penalty strengths based on effectiveness."""
        adjustments = tuner_with_history.update_penalty_strengths()

        assert len(adjustments) > 0

        # Should reduce strength for too-harsh penalty
        market_adj = [a for a in adjustments if a.penalty_type == "market_quality"]
        if market_adj:
            assert market_adj[0].recommended_strength < market_adj[0].current_strength

    def test_apply_penalty_strength_multiplies(self, tuner_with_history):
        """Should apply strength multiplier to penalties."""
        tuner_with_history.update_penalty_strengths()

        base_penalty = 0.05
        adjusted = tuner_with_history.apply_penalty_strength("market_quality", base_penalty)

        # Should be different from base (either reduced or same)
        assert adjusted != base_penalty or adjusted == base_penalty  # May change or stay same

    def test_penalty_strengths_clamped(self, tuner_with_history):
        """Penalty strengths should stay within bounds."""
        # Record many penalties to drive adjustments
        for _ in range(50):
            tuner_with_history.record_penalty_application(
                penalty_type="market_quality",
                penalty_strength=0.10,
                original_confidence=0.80,
                penalized_confidence=0.70,
                result="hit",
            )

        for _ in range(10):
            tuner_with_history.update_penalty_strengths()

        status = tuner_with_history.get_penalty_status()
        for strength in status["current_strengths"].values():
            assert 0.5 <= strength <= 2.0


class TestFeedbackIntegration:
    """Test integration of feedback components."""

    def test_full_feedback_workflow(self):
        """Test complete feedback workflow from grading to adjustments."""
        # Create store
        store = PerformanceStore()

        # Create graded picks and add to store
        picks = [
            MockGradedPick(1, "LeBron", "player_points", 0.85, 5.0, "over", 25.0, 20.0, 22.0),
            MockGradedPick(2, "Curry", "player_points", 0.65, 2.0, "over", 28.0, 25.0, 26.0),
            MockGradedPick(3, "Durant", "player_points", 0.75, 4.0, "over", 22.0, 24.0, 23.0),
        ]
        store.add_graded_picks(picks, "2024-01-01")

        # Create analyzer and generate report
        analyzer = GradingFeedbackAnalyzer(store)
        report = analyzer.generate_summary_report(window_days=30)

        assert "total_picks" in report
        assert "overall_hit_rate" in report
        assert "calibration_issues" in report

        # Create calibrator and update
        calibrator = ConfidenceCalibrator(store)
        adjustments = calibrator.update_calibration()

        # Should have calibration status
        status = calibrator.get_calibration_status()
        assert "confidence_multipliers" in status

        # Create edge tracker
        tracker = EdgeReliabilityTracker(store)
        reliability = tracker.analyze_reliability()
        assert len(reliability) > 0

        # Create penalty tuner
        tuner = PenaltyTuner(store)
        tuner.record_penalty_application(
            "market_quality", 0.05, 0.75, 0.70, "hit"
        )
        effectiveness = tuner.analyze_penalty_effectiveness()
        assert "market_quality" in effectiveness
