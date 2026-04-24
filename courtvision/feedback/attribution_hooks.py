"""Integration hooks for Phase 8 attribution and mistake analysis.

Provides non-invasive integration points for:
- Attribution tracking (pick-level contributions)
- Miss classification (why predictions failed)
- Signal reliability scoring
- Feature-level feedback

Phase 8: Causal Attribution and Mistake Analysis
"""

from __future__ import annotations

from typing import Any

from courtvision.feedback.attribution import AttributionTracker, PickAttribution
from courtvision.feedback.miss_classifier import MissClassifier, MissCategory
from courtvision.feedback.signal_reliability import SignalReliabilityTracker
from courtvision.feedback.feature_feedback import FeaturePerformanceTracker


class AttributionHooks:
    """Hooks for Phase 8 attribution and causal analysis.

    Integrates attribution tracking, miss classification, signal reliability,
    and feature feedback into a unified analysis layer.

    Usage:
        hooks = AttributionHooks()

        # During prediction:
        hooks.record_attribution(pick_id, attribution_data)

        # After grading:
        hooks.classify_miss(pick_id, actual_value, ...)
        hooks.update_signal_reliability(pick_id, signals, result)
        hooks.update_feature_performance(pick_id, features, result)
    """

    def __init__(
        self,
        enable_attribution: bool = True,
        enable_miss_classification: bool = True,
        enable_signal_reliability: bool = True,
        enable_feature_feedback: bool = True,
    ) -> None:
        """Initialize attribution hooks.

        Args:
            enable_attribution: Enable pick-level attribution tracking
            enable_miss_classification: Enable miss classification
            enable_signal_reliability: Enable signal reliability tracking
            enable_feature_feedback: Enable feature performance tracking
        """
        self.enable_attribution = enable_attribution
        self.enable_miss_classification = enable_miss_classification
        self.enable_signal_reliability = enable_signal_reliability
        self.enable_feature_feedback = enable_feature_feedback

        # Initialize trackers
        self.attribution_tracker = AttributionTracker() if enable_attribution else None
        self.miss_classifier = MissClassifier() if enable_miss_classification else None
        self.signal_tracker = SignalReliabilityTracker() if enable_signal_reliability else None
        self.feature_tracker = FeaturePerformanceTracker() if enable_feature_feedback else None

    def record_attribution(self, attribution: PickAttribution) -> None:
        """Record attribution for a pick.

        Args:
            attribution: Complete attribution breakdown for the pick
        """
        if self.attribution_tracker:
            self.attribution_tracker.record_attribution(attribution)

    def classify_miss(
        self,
        pick_id: str,
        attribution: PickAttribution,
        actual_value: float,
        projection: float,
        line: float,
        actual_minutes: float | None = None,
        expected_minutes: float | None = None,
        game_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Classify why a pick missed.

        Args:
            pick_id: Unique pick identifier
            attribution: Pick attribution data
            actual_value: Actual player performance
            projection: Model projection
            line: Betting line
            actual_minutes: Actual minutes played
            expected_minutes: Projected minutes
            game_context: Additional game context

        Returns:
            Classification result dict or None if not enabled
        """
        if not self.miss_classifier:
            return None

        classification = self.miss_classifier.classify_miss(
            pick_id=pick_id,
            attribution=attribution,
            actual_value=actual_value,
            projection=projection,
            line=line,
            actual_minutes=actual_minutes,
            expected_minutes=expected_minutes,
            game_context=game_context,
        )

        return classification.to_dict()

    def update_signal_reliability(
        self,
        pick_id: str,
        signals: list[str],
        result: str,
        confidence: float,
        timestamp: str,
    ) -> None:
        """Update signal reliability with pick outcome.

        Args:
            pick_id: Unique pick identifier
            signals: List of signals present for this pick
            result: "hit" or "miss"
            confidence: Final pick confidence
            timestamp: Prediction date
        """
        if self.signal_tracker:
            self.signal_tracker.record_signal_presence(
                pick_id=pick_id,
                signals=signals,
                result=result,
                confidence=confidence,
                timestamp=timestamp,
            )

    def update_feature_performance(
        self,
        pick_id: str,
        features: dict[str, float],
        result: str,
        confidence: float,
        timestamp: str,
    ) -> None:
        """Update feature performance with pick outcome.

        Args:
            pick_id: Unique pick identifier
            features: Dict of feature names to values
            result: "hit" or "miss"
            confidence: Final pick confidence
            timestamp: Prediction date
        """
        if not self.feature_tracker:
            return

        for feature_name, feature_value in features.items():
            self.feature_tracker.record_feature_impact(
                feature_name=feature_name,
                pick_id=pick_id,
                feature_value=feature_value,
                result=result,
                confidence=confidence,
                timestamp=timestamp,
            )

    def get_signal_weights(self, min_samples: int = 30) -> dict[str, float]:
        """Get dynamically adjusted signal weights.

        Args:
            min_samples: Minimum samples before adjusting weights

        Returns:
            Dictionary of signal names to weights
        """
        if not self.signal_tracker:
            return {}

        return self.signal_tracker.compute_signal_weights(min_samples=min_samples)

    def get_feature_recommendations(self) -> dict[str, Any]:
        """Get recommendations for feature weight adjustments.

        Returns:
            Recommendations for increasing/decreasing feature weights
        """
        if not self.feature_tracker:
            return {}

        return self.feature_tracker.get_feature_adjustment_recommendations()

    def get_miss_pattern_analysis(self) -> dict[str, Any]:
        """Get analysis of miss patterns.

        Returns:
            Distribution of miss types and actionable insights
        """
        if not self.miss_classifier:
            return {}

        return self.miss_classifier.analyze_miss_patterns()

    def get_attribution_report(self) -> dict[str, Any]:
        """Get complete attribution analysis report.

        Returns:
            Full attribution report with all analyses
        """
        if not self.attribution_tracker:
            return {}

        return self.attribution_tracker.export_attribution_report()

    def get_complete_analysis(self) -> dict[str, Any]:
        """Get complete Phase 8 analysis report.

        Returns:
            Combined report from all attribution components
        """
        return {
            "attribution": self.get_attribution_report() if self.attribution_tracker else None,
            "miss_patterns": self.get_miss_pattern_analysis() if self.miss_classifier else None,
            "signal_reliability": self.signal_tracker.export_reliability_report() if self.signal_tracker else None,
            "feature_performance": self.feature_tracker.export_feature_report() if self.feature_tracker else None,
            "recommendations": {
                "signal_weights": self.get_signal_weights(),
                "feature_adjustments": self.get_feature_recommendations(),
            },
        }

    def get_status(self) -> dict[str, Any]:
        """Get status of all attribution components."""
        return {
            "enabled": {
                "attribution": self.enable_attribution,
                "miss_classification": self.enable_miss_classification,
                "signal_reliability": self.enable_signal_reliability,
                "feature_feedback": self.enable_feature_feedback,
            },
            "attribution_count": len(self.attribution_tracker._attributions) if self.attribution_tracker else 0,
            "classification_count": len(self.miss_classifier._classifications) if self.miss_classifier else 0,
            "signal_impacts": len(self.signal_tracker._impact_history) if self.signal_tracker else 0,
            "feature_impacts": len(self.feature_tracker._impact_history) if self.feature_tracker else 0,
        }

    def reset_all(self) -> None:
        """Reset all attribution components."""
        if self.attribution_tracker:
            self.attribution_tracker = AttributionTracker()
        if self.miss_classifier:
            self.miss_classifier = MissClassifier()
        if self.signal_tracker:
            self.signal_tracker = SignalReliabilityTracker()
        if self.feature_tracker:
            self.feature_tracker = FeaturePerformanceTracker()


class IntegratedFeedbackSystem:
    """Integrated feedback system combining Phase 7 and Phase 8 components.

    Provides a unified interface for all feedback capabilities:
    - Phase 7: Calibration, edge tracking, penalty tuning (performance feedback)
    - Phase 8: Attribution, miss classification, signal/feature analysis (causal analysis)

    This is the main entry point for the complete feedback system.
    """

    def __init__(
        self,
        performance_store: Any,
        enable_phase7: bool = True,
        enable_phase8: bool = True,
    ) -> None:
        """Initialize integrated feedback system.

        Args:
            performance_store: Performance store for Phase 7 components
            enable_phase7: Enable Phase 7 feedback (calibration, edge, penalties)
            enable_phase8: Enable Phase 8 feedback (attribution, classification)
        """
        # Import here to avoid circular imports
        from courtvision.feedback.hooks import ScoringAdjustmentHooks

        self.phase7 = ScoringAdjustmentHooks(performance_store) if enable_phase7 else None
        self.phase8 = AttributionHooks() if enable_phase8 else None

    def apply_all_adjustments(
        self,
        base_confidence: float,
        base_edge: float,
        base_penalty: float,
        penalty_type: str,
        bucket: str | None = None,
    ) -> dict[str, float]:
        """Apply all Phase 7 feedback adjustments.

        Args:
            base_confidence: Raw confidence from scoring
            base_edge: Raw edge from scoring
            base_penalty: Base penalty amount
            penalty_type: Type of penalty
            bucket: Confidence bucket

        Returns:
            Adjusted values
        """
        if not self.phase7:
            return {
                "confidence": base_confidence,
                "edge": base_edge,
                "penalty": base_penalty,
            }

        return {
            "confidence": self.phase7.apply_confidence_calibration(base_confidence, bucket),
            "edge": self.phase7.apply_edge_weight(base_edge),
            "penalty": self.phase7.apply_penalty_strength(penalty_type, base_penalty),
        }

    def record_complete_outcome(
        self,
        pick_id: str,
        attribution: PickAttribution,
        actual_value: float,
        projection: float,
        line: float,
        result: str,
        signals: list[str],
        features: dict[str, float],
        timestamp: str,
        actual_minutes: float | None = None,
        expected_minutes: float | None = None,
        game_context: dict[str, Any] | None = None,
    ) -> None:
        """Record complete pick outcome for all feedback systems.

        This is the main method to call after grading a pick.

        Args:
            pick_id: Unique pick identifier
            attribution: Attribution data from prediction time
            actual_value: Actual player performance
            projection: Model projection
            line: Betting line
            result: "hit" or "miss"
            signals: Signals present for this pick
            features: Feature values for this pick
            timestamp: Prediction date
            actual_minutes: Actual minutes played
            expected_minutes: Projected minutes
            game_context: Additional game context
        """
        if self.phase8:
            # Record attribution
            self.phase8.record_attribution(attribution)

            # Classify miss if applicable
            if result == "miss":
                self.phase8.classify_miss(
                    pick_id=pick_id,
                    attribution=attribution,
                    actual_value=actual_value,
                    projection=projection,
                    line=line,
                    actual_minutes=actual_minutes,
                    expected_minutes=expected_minutes,
                    game_context=game_context,
                )

            # Update signal reliability
            self.phase8.update_signal_reliability(
                pick_id=pick_id,
                signals=signals,
                result=result,
                confidence=attribution.final_confidence,
                timestamp=timestamp,
            )

            # Update feature performance
            self.phase8.update_feature_performance(
                pick_id=pick_id,
                features=features,
                result=result,
                confidence=attribution.final_confidence,
                timestamp=timestamp,
            )

        # Record penalty outcome in Phase 7
        if self.phase7 and attribution.penalty_impacts:
            for penalty_type, impact in attribution.penalty_impacts.items():
                self.phase7.record_penalty_outcome(
                    penalty_type=penalty_type,
                    penalty_strength=abs(impact),
                    original_confidence=attribution.base_confidence,
                    penalized_confidence=attribution.final_confidence,
                    result=result,
                )

    def get_complete_report(self) -> dict[str, Any]:
        """Get complete feedback report from all phases."""
        return {
            "phase7_status": self.phase7.get_status() if self.phase7 else None,
            "phase7_adjustments": self.phase7.update_all() if self.phase7 else None,
            "phase8_analysis": self.phase8.get_complete_analysis() if self.phase8 else None,
            "phase8_status": self.phase8.get_status() if self.phase8 else None,
        }

    def reset_all(self) -> None:
        """Reset all feedback components."""
        if self.phase7:
            self.phase7.reset_all()
        if self.phase8:
            self.phase8.reset_all()
