"""Dynamic adjustment hooks for scoring system integration.

Provides non-invasive hooks to integrate feedback loop adjustments
into the existing scoring pipeline without changing its structure.
"""

from __future__ import annotations

from typing import Any, Callable

from courtvision.feedback.calibration import ConfidenceCalibrator
from courtvision.feedback.edge_tracker import EdgeReliabilityTracker
from courtvision.feedback.penalty_tuner import PenaltyTuner
from courtvision.feedback.performance_store import PerformanceStore


class ScoringAdjustmentHooks:
    """Hooks to integrate feedback adjustments into scoring.

    Provides a layer that sits on top of the current scoring system,
    applying dynamic adjustments without modifying existing code paths.

    Usage:
        hooks = ScoringAdjustmentHooks(performance_store)

        # In scoring code:
        confidence = hooks.apply_confidence_calibration(raw_confidence, bucket)
        edge = hooks.apply_edge_weight(raw_edge)
        penalty = hooks.apply_penalty_strength(penalty_type, base_penalty)
    """

    def __init__(
        self,
        performance_store: PerformanceStore,
        enable_calibration: bool = True,
        enable_edge_weights: bool = True,
        enable_penalty_tuning: bool = True,
    ) -> None:
        """Initialize hooks with feedback components.

        Args:
            performance_store: Performance store with historical data
            enable_calibration: Enable confidence calibration adjustments
            enable_edge_weights: Enable edge weight adjustments
            enable_penalty_tuning: Enable penalty strength adjustments
        """
        self.store = performance_store
        self.enable_calibration = enable_calibration
        self.enable_edge_weights = enable_edge_weights
        self.enable_penalty_tuning = enable_penalty_tuning

        # Initialize feedback components
        self.calibrator = ConfidenceCalibrator(performance_store) if enable_calibration else None
        self.edge_tracker = EdgeReliabilityTracker(performance_store) if enable_edge_weights else None
        self.penalty_tuner = PenaltyTuner(performance_store) if enable_penalty_tuning else None

        # Cache window for batch updates
        self._last_update: str | None = None

    def apply_confidence_calibration(
        self,
        base_confidence: float,
        bucket: str | None = None,
    ) -> float:
        """Apply confidence calibration adjustment.

        Args:
            base_confidence: Raw confidence value from scoring
            bucket: Confidence bucket (auto-detected if None)

        Returns:
            Calibrated confidence value
        """
        if not self.enable_calibration or self.calibrator is None:
            return base_confidence

        return self.calibrator.apply_calibration(base_confidence, bucket)

    def apply_edge_weight(
        self,
        base_edge: float,
        bucket_label: str | None = None,
    ) -> float:
        """Apply edge reliability weight adjustment.

        Args:
            base_edge: Raw edge value from scoring
            bucket_label: Edge bucket (auto-detected if None)

        Returns:
            Weighted edge value
        """
        if not self.enable_edge_weights or self.edge_tracker is None:
            return base_edge

        return self.edge_tracker.apply_edge_weight(base_edge, bucket_label)

    def apply_penalty_strength(
        self,
        penalty_type: str,
        base_penalty: float,
    ) -> float:
        """Apply penalty strength multiplier.

        Args:
            penalty_type: Type of penalty being applied
            base_penalty: Base penalty amount from scoring logic

        Returns:
            Adjusted penalty amount
        """
        if not self.enable_penalty_tuning or self.penalty_tuner is None:
            return base_penalty

        return self.penalty_tuner.apply_penalty_strength(penalty_type, base_penalty)

    def record_penalty_outcome(
        self,
        penalty_type: str,
        penalty_strength: float,
        original_confidence: float,
        penalized_confidence: float,
        result: str,
    ) -> None:
        """Record penalty outcome for future tuning.

        Should be called after grading to track penalty effectiveness.

        Args:
            penalty_type: Type of penalty applied
            penalty_strength: Amount of confidence reduced
            original_confidence: Confidence before penalty
            penalized_confidence: Confidence after penalty
            result: "hit" or "miss"
        """
        if not self.enable_penalty_tuning or self.penalty_tuner is None:
            return

        self.penalty_tuner.record_penalty_application(
            penalty_type=penalty_type,
            penalty_strength=penalty_strength,
            original_confidence=original_confidence,
            penalized_confidence=penalized_confidence,
            result=result,
        )

    def update_all(self, window_days: int = 30) -> dict[str, Any]:
        """Update all feedback components with latest data.

        Args:
            window_days: Window size for analysis

        Returns:
            Dict of all adjustments made
        """
        adjustments = {}

        if self.enable_calibration and self.calibrator is not None:
            adjustments["confidence"] = [
                {
                    "bucket": a.target_bucket,
                    "adjustment": a.adjustment,
                    "multiplier": a.confidence_multiplier,
                }
                for a in self.calibrator.update_calibration(window_days)
            ]

        if self.enable_edge_weights and self.edge_tracker is not None:
            adjustments["edge"] = [
                {
                    "bucket": a.bucket_label,
                    "old_weight": a.current_weight,
                    "new_weight": a.recommended_weight,
                }
                for a in self.edge_tracker.update_weights(window_days)
            ]

        if self.enable_penalty_tuning and self.penalty_tuner is not None:
            adjustments["penalty"] = [
                {
                    "type": a.penalty_type,
                    "old_strength": a.current_strength,
                    "new_strength": a.recommended_strength,
                }
                for a in self.penalty_tuner.update_penalty_strengths(window_days)
            ]

        return adjustments

    def get_status(self) -> dict[str, Any]:
        """Get current status of all feedback components."""
        status = {
            "enabled": {
                "calibration": self.enable_calibration,
                "edge_weights": self.enable_edge_weights,
                "penalty_tuning": self.enable_penalty_tuning,
            },
        }

        if self.calibrator:
            status["calibration"] = self.calibrator.get_calibration_status()

        if self.edge_tracker:
            status["edge_weights"] = self.edge_tracker.get_weight_status()

        if self.penalty_tuner:
            status["penalties"] = self.penalty_tuner.get_penalty_status()

        return status

    def reset_all(self) -> None:
        """Reset all feedback components to defaults."""
        if self.calibrator:
            self.calibrator.reset_calibration()

        if self.edge_tracker:
            self.edge_tracker.reset_weights()

        if self.penalty_tuner:
            self.penalty_tuner.reset_penalties()


def create_feedback_aware_scoring(
    performance_store: PerformanceStore,
    **hook_kwargs: Any,
) -> tuple[Callable[[float, str | None], float], Callable[[float, str | None], float], Callable[[str, float], float]]:
    """Create feedback-aware scoring functions.

    Returns three functions that wrap the hooks for easy integration:
    1. calibrate_confidence(base_confidence, bucket=None)
    2. weight_edge(base_edge, bucket_label=None)
    3. tune_penalty(penalty_type, base_penalty)

    Example:
        calibrate, weight_edge, tune = create_feedback_aware_scoring(store)

        # In scoring code:
        confidence = calibrate(raw_confidence, "high")
        weighted_edge = weight_edge(raw_edge)
        adjusted_penalty = tune("market_quality", 0.05)
    """
    hooks = ScoringAdjustmentHooks(performance_store, **hook_kwargs)

    def calibrate_confidence(base: float, bucket: str | None = None) -> float:
        return hooks.apply_confidence_calibration(base, bucket)

    def weight_edge(base: float, bucket_label: str | None = None) -> float:
        return hooks.apply_edge_weight(base, bucket_label)

    def tune_penalty(penalty_type: str, base: float) -> float:
        return hooks.apply_penalty_strength(penalty_type, base)

    return calibrate_confidence, weight_edge, tune_penalty
