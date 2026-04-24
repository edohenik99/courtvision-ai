"""Tests for Phase 8: Causal Attribution and Mistake Analysis.

Validates:
1. Attribution tracking (pick-level contributions)
2. Miss classification (projection error, role change, etc.)
3. Signal reliability scoring
4. Feature-level performance feedback
5. Integration hooks
"""

import pytest

from courtvision.feedback.attribution import AttributionTracker, PickAttribution
from courtvision.feedback.miss_classifier import MissClassifier, MissCategory
from courtvision.feedback.signal_reliability import SignalReliabilityTracker
from courtvision.feedback.feature_feedback import FeaturePerformanceTracker
from courtvision.feedback.attribution_hooks import AttributionHooks, IntegratedFeedbackSystem
from courtvision.feedback.performance_store import PerformanceStore


class TestAttributionTracking:
    """Test pick-level attribution tracking."""

    def test_attribution_creation(self):
        """Test creating pick attribution."""
        attr = PickAttribution(
            pick_id="test_001",
            prediction_date="2024-01-01",
            player_id=1,
            player_name="Test Player",
            stat_type="points",
            market_type="player_points",
            projection_value=25.5,
            line_value=24.5,
            projection_edge=0.041,
            base_confidence=0.72,
            final_confidence=0.65,
            confidence_bucket="high",
            edge_raw=0.041,
            edge_final=0.041,
            edge_bucket="0.0-0.05",
            penalty_impacts={"market_quality": -0.05, "injury_volatility": -0.02},
            total_penalty_impact=-0.07,
            injury_boost=0.10,
            injury_volatility_penalty=-0.02,
            injury_context_score=0.08,
            signals_present=["high_edge", "injury_boost"],
        )

        assert attr.pick_id == "test_001"
        assert attr.final_confidence == 0.65
        assert attr.total_penalty_impact == -0.07

    def test_attribution_tracker_records(self):
        """Test attribution tracker records attributions."""
        tracker = AttributionTracker()

        attr = PickAttribution(
            pick_id="test_001",
            prediction_date="2024-01-01",
            player_id=1,
            player_name="Test Player",
            stat_type="points",
            market_type="player_points",
            projection_value=25.5,
            line_value=24.5,
            projection_edge=0.041,
            base_confidence=0.72,
            final_confidence=0.65,
            confidence_bucket="high",
            edge_raw=0.041,
            edge_final=0.041,
            edge_bucket="0.0-0.05",
        )

        tracker.record_attribution(attr)

        assert len(tracker._attributions) == 1
        assert tracker.get_attribution("test_001") is not None

    def test_attribution_indexing(self):
        """Test attribution indexing by player, date, market."""
        tracker = AttributionTracker()

        for i in range(5):
            attr = PickAttribution(
                pick_id=f"test_{i:03d}",
                prediction_date="2024-01-01",
                player_id=i % 2,  # 2 unique players
                player_name=f"Player {i % 2}",
                stat_type="points",
                market_type="player_points" if i < 3 else "player_rebounds",
                projection_value=25.5,
                line_value=24.5,
                projection_edge=0.041,
                base_confidence=0.72,
                final_confidence=0.65,
                confidence_bucket="high",
                edge_raw=0.041,
                edge_final=0.041,
                edge_bucket="0.0-0.05",
            )
            tracker.record_attribution(attr)

        # Check player indexing
        player_0_attrs = tracker.get_player_attributions(0)
        assert len(player_0_attrs) == 3  # IDs 0, 2, 4

        # Check date indexing
        date_attrs = tracker.get_date_attributions("2024-01-01")
        assert len(date_attrs) == 5

    def test_signal_strength_computation(self):
        """Test computing signal strength from attribution."""
        attr = PickAttribution(
            pick_id="test_001",
            prediction_date="2024-01-01",
            player_id=1,
            player_name="Test Player",
            stat_type="points",
            market_type="player_points",
            projection_value=25.5,
            line_value=24.5,
            projection_edge=0.041,
            base_confidence=0.72,
            final_confidence=0.65,
            confidence_bucket="high",
            edge_raw=0.041,
            edge_final=0.041,
            edge_bucket="0.0-0.05",
            penalty_impacts={"market_quality": -0.05},
            total_penalty_impact=-0.05,
            injury_boost=0.10,
            injury_volatility_penalty=-0.02,
            recent_form_ratio=1.15,
        )

        strengths = attr.compute_signal_strength()

        assert "projection" in strengths
        assert "penalty_severity" in strengths
        assert "injury_net" in strengths
        assert "recent_form" in strengths


class TestMissClassification:
    """Test miss classification system."""

    def test_projection_error_classification(self):
        """Test classifying projection errors."""
        classifier = MissClassifier()

        attr = PickAttribution(
            pick_id="test_001",
            prediction_date="2024-01-01",
            player_id=1,
            player_name="Test Player",
            stat_type="points",
            market_type="player_points",
            projection_value=25.5,
            line_value=24.5,
            projection_edge=0.041,
            base_confidence=0.72,
            final_confidence=0.65,
            confidence_bucket="high",
            edge_raw=0.041,
            edge_final=0.041,
            edge_bucket="0.0-0.05",
        )

        # Model projected 25.5, actual was 18.0 (30% miss)
        # Market line was 24.5, actual was 18.0 (27% miss)
        # Both significantly wrong = projection error
        classification = classifier.classify_miss(
            pick_id="test_001",
            attribution=attr,
            actual_value=18.0,
            projection=25.5,
            line=24.5,
        )

        assert classification.category == MissCategory.PROJECTION_ERROR
        assert classification.actionable is True

    def test_market_trap_classification(self):
        """Test classifying market traps."""
        classifier = MissClassifier()

        attr = PickAttribution(
            pick_id="test_001",
            prediction_date="2024-01-01",
            player_id=1,
            player_name="Test Player",
            stat_type="points",
            market_type="player_points",
            projection_value=28.0,  # Model very high
            line_value=24.5,
            projection_edge=0.14,
            base_confidence=0.75,
            final_confidence=0.70,
            confidence_bucket="high",
            edge_raw=0.14,
            edge_final=0.14,
            edge_bucket="0.10-0.15",
        )

        # Model projected 28, actual was 24.5 (12% miss)
        # Market line was 24.5, actual was 24.5 (0% miss)
        # Market was right, model was wrong = market trap
        classification = classifier.classify_miss(
            pick_id="test_001",
            attribution=attr,
            actual_value=24.5,  # Exactly at line
            projection=28.0,
            line=24.5,
        )

        assert classification.category == MissCategory.MARKET_TRAP

    def test_role_change_classification(self):
        """Test classifying role changes."""
        classifier = MissClassifier()

        attr = PickAttribution(
            pick_id="test_001",
            prediction_date="2024-01-01",
            player_id=1,
            player_name="Test Player",
            stat_type="points",
            market_type="player_points",
            projection_value=25.5,
            line_value=24.5,
            projection_edge=0.041,
            base_confidence=0.72,
            final_confidence=0.65,
            confidence_bucket="high",
            edge_raw=0.041,
            edge_final=0.041,
            edge_bucket="0.0-0.05",
        )

        # Minutes dropped from 32 to 18 (44% decrease)
        classification = classifier.classify_miss(
            pick_id="test_001",
            attribution=attr,
            actual_value=15.0,
            projection=25.5,
            line=24.5,
            actual_minutes=18.0,
            expected_minutes=32.0,
        )

        assert classification.category == MissCategory.ROLE_CHANGE

    def test_variance_noise_classification(self):
        """Test classifying variance/noise."""
        classifier = MissClassifier()

        attr = PickAttribution(
            pick_id="test_001",
            prediction_date="2024-01-01",
            player_id=1,
            player_name="Test Player",
            stat_type="points",
            market_type="player_points",
            projection_value=25.0,
            line_value=24.5,
            projection_edge=0.02,
            base_confidence=0.65,
            final_confidence=0.62,
            confidence_bucket="mid",
            edge_raw=0.02,
            edge_final=0.02,
            edge_bucket="0.0-0.05",
        )

        # Actual was 25.8, line was 24.5 (4% difference - within noise)
        classification = classifier.classify_miss(
            pick_id="test_001",
            attribution=attr,
            actual_value=25.8,  # Slightly under line
            projection=25.0,
            line=24.5,
        )

        assert classification.category == MissCategory.VARIANCE_NOISE
        assert classification.actionable is False

    def test_miss_pattern_analysis(self):
        """Test analyzing miss patterns."""
        classifier = MissClassifier()

        # Create several classifications
        for i in range(10):
            attr = PickAttribution(
                pick_id=f"test_{i:03d}",
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"Player {i}",
                stat_type="points",
                market_type="player_points",
                projection_value=25.5,
                line_value=24.5,
                projection_edge=0.041,
                base_confidence=0.72,
                final_confidence=0.65,
                confidence_bucket="high",
                edge_raw=0.041,
                edge_final=0.041,
                edge_bucket="0.0-0.05",
            )

            # Alternate between projection errors and market traps
            if i % 2 == 0:
                classifier.classify_miss(
                    pick_id=f"test_{i:03d}",
                    attribution=attr,
                    actual_value=18.0,
                    projection=25.5,
                    line=24.5,
                )
            else:
                classifier.classify_miss(
                    pick_id=f"test_{i:03d}",
                    attribution=attr,
                    actual_value=24.5,
                    projection=28.0,
                    line=24.5,
                )

        analysis = classifier.analyze_miss_patterns()

        assert analysis["total_misses"] == 10
        assert "distribution" in analysis
        assert "projection_error" in str(analysis["distribution"])


class TestSignalReliability:
    """Test signal reliability scoring."""

    def test_signal_recording(self):
        """Test recording signal presence and outcomes."""
        tracker = SignalReliabilityTracker()

        tracker.record_signal_presence(
            pick_id="test_001",
            signals=["high_edge", "injury_boost"],
            result="hit",
            confidence=0.75,
            timestamp="2024-01-01",
        )

        assert len(tracker._impact_history) == len(tracker.SIGNALS)

    def test_signal_reliability_computation(self):
        """Test computing signal reliability scores."""
        tracker = SignalReliabilityTracker()

        # Record 50 hits with "high_edge" signal
        for i in range(50):
            tracker.record_signal_presence(
                pick_id=f"hit_{i:03d}",
                signals=["high_edge"],
                result="hit",
                confidence=0.75,
                timestamp="2024-01-01",
            )

        # Record 20 misses without "high_edge" signal
        for i in range(20):
            tracker.record_signal_presence(
                pick_id=f"miss_{i:03d}",
                signals=[],  # No high_edge
                result="miss",
                confidence=0.55,
                timestamp="2024-01-01",
            )

        # Update metrics
        tracker._update_signal_metrics()

        high_edge_perf = tracker.get_signal_reliability("high_edge")
        assert high_edge_perf.total_appearances == 50
        assert high_edge_perf.hit_rate == 1.0  # All hits

    def test_reliable_signals_filter(self):
        """Test filtering reliable signals."""
        tracker = SignalReliabilityTracker()

        # Create a reliable signal (high hit rate)
        for i in range(50):
            tracker.record_signal_presence(
                pick_id=f"test_{i:03d}",
                signals=["high_edge", "elite_confidence"],
                result="hit" if i < 40 else "miss",  # 80% hit rate
                confidence=0.75,
                timestamp="2024-01-01",
            )

        reliable = tracker.get_reliable_signals(min_samples=30, min_reliability=0.2)

        # Should identify the signals as reliable
        assert "high_edge" in reliable or "elite_confidence" in reliable

    def test_weight_recommendations(self):
        """Test generating weight adjustment recommendations."""
        tracker = SignalReliabilityTracker()

        # Create signals with different performance
        for i in range(100):
            signals = []
            if i < 50:
                signals.append("reliable_signal")
            if i >= 40:
                signals.append("unreliable_signal")

            # reliable_signal: 40 hits / 10 misses when present
            # unreliable_signal: 10 hits / 50 misses when present
            result = "hit" if (i < 40 or (i >= 50 and i < 60)) else "miss"

            tracker.record_signal_presence(
                pick_id=f"test_{i:03d}",
                signals=signals,
                result=result,
                confidence=0.65,
                timestamp="2024-01-01",
            )

        recs = tracker.get_weight_adjustment_recommendations(min_samples=20)

        assert "increase_weight" in recs
        assert "decrease_weight" in recs


class TestFeatureFeedback:
    """Test feature-level performance feedback."""

    def test_feature_impact_recording(self):
        """Test recording feature impacts."""
        tracker = FeaturePerformanceTracker()

        tracker.record_feature_impact(
            feature_name="recent_form_ratio",
            pick_id="test_001",
            feature_value=1.15,
            result="hit",
            confidence=0.72,
            timestamp="2024-01-01",
        )

        perf = tracker.get_feature_performance("recent_form_ratio")
        assert perf.total_applications == 1
        assert perf.hits == 1

    def test_feature_correlation_analysis(self):
        """Test analyzing feature correlations with success."""
        tracker = FeaturePerformanceTracker()

        # Record features where higher values correlate with hits
        for i in range(50):
            value = 1.2 if i < 35 else 0.9  # Higher values more likely to hit
            result = "hit" if i < 35 else "miss"

            tracker.record_feature_impact(
                feature_name="recent_form_ratio",
                pick_id=f"test_{i:03d}",
                feature_value=value,
                result=result,
                confidence=0.70,
                timestamp="2024-01-01",
            )

        correlation = tracker.analyze_feature_correlation("recent_form_ratio")

        assert correlation["feature"] == "recent_form_ratio"
        assert correlation["interpretation"] == "positive"
        assert correlation["correlation"] > 0.1

    def test_optimal_feature_ranges(self):
        """Test identifying optimal feature value ranges."""
        tracker = FeaturePerformanceTracker()

        # Record features in different ranges
        for i in range(60):
            # Bucket 4 (high values) should perform best
            if i < 20:
                value = 0.85  # bucket_0
            elif i < 30:
                value = 0.95  # bucket_2
            elif i < 40:
                value = 1.15  # bucket_4 - should have high hit rate
            else:
                value = 1.05  # bucket_3

            # Make bucket_4 (high values) perform best
            result = "hit" if (i >= 30 and i < 40) or i < 15 else "miss"

            tracker.record_feature_impact(
                feature_name="recent_form_ratio",
                pick_id=f"test_{i:03d}",
                feature_value=value,
                result=result,
                confidence=0.70,
                timestamp="2024-01-01",
            )

        optimal = tracker.get_optimal_feature_ranges("recent_form_ratio")

        assert "bucket_performance" in optimal
        assert "optimal_bucket" in optimal


class TestAttributionHooks:
    """Test Phase 8 attribution hooks integration."""

    def test_hooks_initialization(self):
        """Test initializing attribution hooks."""
        hooks = AttributionHooks()

        assert hooks.enable_attribution is True
        assert hooks.attribution_tracker is not None
        assert hooks.miss_classifier is not None
        assert hooks.signal_tracker is not None
        assert hooks.feature_tracker is not None

    def test_complete_outcome_recording(self):
        """Test recording complete pick outcome."""
        hooks = AttributionHooks()

        attr = PickAttribution(
            pick_id="test_001",
            prediction_date="2024-01-01",
            player_id=1,
            player_name="Test Player",
            stat_type="points",
            market_type="player_points",
            projection_value=25.5,
            line_value=24.5,
            projection_edge=0.041,
            base_confidence=0.72,
            final_confidence=0.65,
            confidence_bucket="high",
            edge_raw=0.041,
            edge_final=0.041,
            edge_bucket="0.0-0.05",
            signals_present=["high_edge", "injury_boost"],
        )

        hooks.record_attribution(attr)
        hooks.update_signal_reliability(
            pick_id="test_001",
            signals=["high_edge", "injury_boost"],
            result="hit",
            confidence=0.65,
            timestamp="2024-01-01",
        )
        hooks.update_feature_performance(
            pick_id="test_001",
            features={"recent_form_ratio": 1.15, "projection_edge_size": 0.041},
            result="hit",
            confidence=0.65,
            timestamp="2024-01-01",
        )

        assert len(hooks.attribution_tracker._attributions) == 1
        assert len(hooks.signal_tracker._impact_history) > 0
        assert len(hooks.feature_tracker._impact_history) > 0

    def test_get_complete_analysis(self):
        """Test getting complete Phase 8 analysis."""
        hooks = AttributionHooks()

        # Add some data
        for i in range(30):
            attr = PickAttribution(
                pick_id=f"test_{i:03d}",
                prediction_date="2024-01-01",
                player_id=i,
                player_name=f"Player {i}",
                stat_type="points",
                market_type="player_points",
                projection_value=25.5,
                line_value=24.5,
                projection_edge=0.041,
                base_confidence=0.72,
                final_confidence=0.65,
                confidence_bucket="high",
                edge_raw=0.041,
                edge_final=0.041,
                edge_bucket="0.0-0.05",
                signals_present=["high_edge"],
            )
            hooks.record_attribution(attr)

        analysis = hooks.get_complete_analysis()

        assert "attribution" in analysis
        assert "signal_reliability" in analysis
        assert "feature_performance" in analysis


class TestIntegratedFeedbackSystem:
    """Test integrated Phase 7 + Phase 8 system."""

    def test_integrated_system_initialization(self):
        """Test initializing integrated feedback system."""
        store = PerformanceStore()
        system = IntegratedFeedbackSystem(store, enable_phase7=True, enable_phase8=True)

        assert system.phase7 is not None
        assert system.phase8 is not None

    def test_apply_all_adjustments(self):
        """Test applying all Phase 7 adjustments."""
        store = PerformanceStore()
        system = IntegratedFeedbackSystem(store)

        adjustments = system.apply_all_adjustments(
            base_confidence=0.70,
            base_edge=0.05,
            base_penalty=0.05,
            penalty_type="market_quality",
            bucket="high",
        )

        assert "confidence" in adjustments
        assert "edge" in adjustments
        assert "penalty" in adjustments

    def test_complete_report(self):
        """Test generating complete feedback report."""
        store = PerformanceStore()
        system = IntegratedFeedbackSystem(store, enable_phase7=True, enable_phase8=True)

        report = system.get_complete_report()

        assert "phase7_status" in report
        assert "phase8_analysis" in report
