"""Feedback loop and self-correction system for CourtVisionAI.

This package enables the model to learn from past predictions and adjust
confidence, edge weighting, and penalties dynamically based on performance.

Phase 7: Performance Feedback (What happened)
- performance_store: Rolling window performance tracking
- grading_feedback: Prediction vs actual comparison
- calibration: Confidence calibration feedback
- edge_tracker: Edge reliability tracking by size buckets
- penalty_tuner: Penalty strength auto-adjustment

Phase 8: Causal Attribution (Why it happened)
- attribution: Pick-level contribution tracking
- miss_classifier: Classification of prediction failures
- signal_reliability: Signal effectiveness scoring
- feature_feedback: Feature-level performance tracking
- attribution_hooks: Integrated Phase 7 + Phase 8 system
"""

from __future__ import annotations

from courtvision.feedback.attribution import AttributionTracker, PickAttribution
from courtvision.feedback.attribution_hooks import AttributionHooks, IntegratedFeedbackSystem
from courtvision.feedback.calibration import ConfidenceCalibrator
from courtvision.feedback.edge_tracker import EdgeReliabilityTracker
from courtvision.feedback.feature_feedback import FeaturePerformanceTracker
from courtvision.feedback.grading_feedback import GradingFeedbackAnalyzer
from courtvision.feedback.miss_classifier import MissCategory, MissClassifier
from courtvision.feedback.penalty_tuner import PenaltyTuner
from courtvision.feedback.performance_store import PerformanceStore
from courtvision.feedback.signal_reliability import SignalReliabilityTracker

__all__ = [
    # Phase 7: Performance Feedback
    "PerformanceStore",
    "GradingFeedbackAnalyzer",
    "ConfidenceCalibrator",
    "EdgeReliabilityTracker",
    "PenaltyTuner",
    # Phase 8: Causal Attribution
    "AttributionTracker",
    "PickAttribution",
    "MissClassifier",
    "MissCategory",
    "SignalReliabilityTracker",
    "FeaturePerformanceTracker",
    "AttributionHooks",
    "IntegratedFeedbackSystem",
]
