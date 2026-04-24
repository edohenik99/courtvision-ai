"""Miss classification system for understanding prediction failures.

Classifies why picks missed into actionable categories:
- projection_error: Model projection was wrong
- role_change: Player role changed unexpectedly
- injury_misread: Injury context misinterpreted
- market_trap: Market was correct, model was wrong
- variance_noise: Statistical noise/unpredictable variance
- situational: Game situation caused unexpected outcome

Phase 8: Causal Attribution and Mistake Analysis
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from courtvision.feedback.attribution import PickAttribution


class MissCategory(str, Enum):
    """Categories of prediction misses."""

    PROJECTION_ERROR = "projection_error"
    ROLE_CHANGE = "role_change"
    INJURY_MISREAD = "injury_misread"
    MARKET_TRAP = "market_trap"
    VARIANCE_NOISE = "variance_noise"
    SITUATIONAL = "situational"
    UNKNOWN = "unknown"


@dataclass
class MissClassification:
    """Classification of why a pick missed."""

    pick_id: str
    category: MissCategory
    confidence: float  # 0-1 confidence in classification
    evidence: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    actionable: bool = False  # Can we act on this insight?
    recommended_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "pick_id": self.pick_id,
            "category": self.category.value,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "explanation": self.explanation,
            "actionable": self.actionable,
            "recommended_action": self.recommended_action,
        }


class MissClassifier:
    """Classify prediction misses into actionable categories.

    Analyzes the gap between prediction and outcome to identify
    root causes and recommend corrective actions.
    """

    # Thresholds for classification
    PROJECTION_ERROR_THRESHOLD = 0.20  # 20% projection miss
    ROLE_CHANGE_MINUTES_SHIFT = 8  # 8+ min change suggests role shift
    INJURY_IMPACT_THRESHOLD = 0.15  # 15% variance from injury expectation
    MARKET_TRAP_THRESHOLD = 0.10  # Market within 10% of actual
    VARIANCE_THRESHOLD = 0.05  # Within 5% = likely noise

    def __init__(self) -> None:
        """Initialize miss classifier."""
        self._classifications: dict[str, MissClassification] = {}
        self._category_counts: dict[MissCategory, int] = {
            cat: 0 for cat in MissCategory
        }

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
    ) -> MissClassification:
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
            MissClassification with category and evidence
        """
        evidence: dict[str, Any] = {}

        # Calculate key deltas
        projection_delta = (actual_value - projection) / projection if projection else 0
        market_delta = (actual_value - line) / line if line else 0
        minutes_delta = (
            (actual_minutes - expected_minutes) / expected_minutes
            if expected_minutes and actual_minutes else 0
        )

        # Classification logic
        category = MissCategory.UNKNOWN
        confidence = 0.5
        explanation = ""
        actionable = False
        recommended_action = ""

        # Check for projection error (model was significantly wrong)
        if abs(projection_delta) > self.PROJECTION_ERROR_THRESHOLD:
            evidence["projection_delta"] = projection_delta
            evidence["market_delta"] = market_delta

            # Was market closer to actual than projection?
            if abs(market_delta) < abs(projection_delta) * 0.5:
                category = MissCategory.MARKET_TRAP
                confidence = 0.8
                explanation = f"Market was closer to actual ({market_delta:.1%}) than projection ({projection_delta:.1%})"
                actionable = True
                recommended_action = "Review market efficiency signals; consider market-implied adjustments"
            else:
                category = MissCategory.PROJECTION_ERROR
                confidence = 0.7
                explanation = f"Model projection missed by {abs(projection_delta):.1%}"
                actionable = True
                recommended_action = "Review projection inputs: minutes, usage, matchup"

        # Check for role change (minutes shift)
        elif actual_minutes and expected_minutes:
            if abs(minutes_delta) > 0.20:  # 20%+ minutes change
                evidence["minutes_delta"] = minutes_delta
                evidence["expected_minutes"] = expected_minutes
                evidence["actual_minutes"] = actual_minutes

                category = MissCategory.ROLE_CHANGE
                confidence = 0.75
                explanation = f"Minutes shifted {minutes_delta:+.1%} ({expected_minutes:.1f} -> {actual_minutes:.1f})"
                actionable = True
                recommended_action = "Monitor lineup changes, rotation shifts, or coaching decisions"

        # Check for injury misread
        elif attribution.injury_boost > 0 or attribution.injury_volatility_penalty > 0:
            injury_expected_impact = attribution.injury_boost + attribution.injury_volatility_penalty

            # Did actual go opposite of injury expectation?
            if (injury_expected_impact > 0 and projection_delta < -self.INJURY_IMPACT_THRESHOLD) or \
               (injury_expected_impact < 0 and projection_delta > self.INJURY_IMPACT_THRESHOLD):
                evidence["injury_expected_impact"] = injury_expected_impact
                evidence["actual_projection_delta"] = projection_delta

                category = MissCategory.INJURY_MISREAD
                confidence = 0.7
                explanation = f"Injury context suggested {injury_expected_impact:+.1%} but actual was {projection_delta:+.1%}"
                actionable = True
                recommended_action = "Review injury context logic; check for rotation adjustments"

        # Check for situational factors
        elif game_context:
            blowout_margin = game_context.get("final_margin", 0)
            was_blowout = abs(blowout_margin) > 20
            rest_days = game_context.get("rest_days", 1)
            back_to_back = rest_days < 2

            if was_blowout or back_to_back:
                evidence["blowout"] = was_blowout
                evidence["blowout_margin"] = blowout_margin
                evidence["back_to_back"] = back_to_back

                category = MissCategory.SITUATIONAL
                confidence = 0.6
                explanation = "Game situation affected outcome"
                if was_blowout:
                    explanation += f" (blowout by {abs(blowout_margin)} points)"
                if back_to_back:
                    explanation += " (back-to-back)"
                actionable = True
                recommended_action = "Factor game situation into projections"

        # Check for variance/noise (close to line, small miss)
        elif abs(market_delta) < self.VARIANCE_THRESHOLD:
            category = MissCategory.VARIANCE_NOISE
            confidence = 0.6
            explanation = f"Outcome within {self.VARIANCE_THRESHOLD:.1%} of line - statistical noise"
            actionable = False
            recommended_action = "No action - within normal variance"

        # Default if no clear pattern
        if category == MissCategory.UNKNOWN:
            explanation = "Unable to determine clear cause"
            actionable = False

        classification = MissClassification(
            pick_id=pick_id,
            category=category,
            confidence=confidence,
            evidence=evidence,
            explanation=explanation,
            actionable=actionable,
            recommended_action=recommended_action,
        )

        # Store classification
        self._classifications[pick_id] = classification
        self._category_counts[category] += 1

        return classification

    def analyze_miss_patterns(
        self,
        min_samples: int = 5,
    ) -> dict[str, Any]:
        """Analyze patterns in miss classifications.

        Returns distribution of miss types and actionable insights.
        """
        total = len(self._classifications)

        if total < min_samples:
            return {
                "error": f"Insufficient samples: {total} < {min_samples}",
                "total_misses": total,
            }

        # Calculate distribution
        distribution = {
            cat.value: {
                "count": count,
                "percentage": count / total * 100 if total > 0 else 0,
            }
            for cat, count in self._category_counts.items()
            if count > 0
        }

        # Identify dominant patterns
        sorted_categories = sorted(
            self._category_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        # Extract actionable insights
        actionable_misses = [
            c for c in self._classifications.values() if c.actionable
        ]

        recommendations: dict[str, list[str]] = {}
        for miss in actionable_misses:
            cat = miss.category.value
            if cat not in recommendations:
                recommendations[cat] = []
            if miss.recommended_action not in recommendations[cat]:
                recommendations[cat].append(miss.recommended_action)

        return {
            "total_misses": total,
            "distribution": distribution,
            "dominant_pattern": sorted_categories[0][0].value if sorted_categories else None,
            "actionable_misses": len(actionable_misses),
            "actionable_percentage": len(actionable_misses) / total * 100 if total > 0 else 0,
            "recommendations": recommendations,
        }

    def get_projection_error_rate(self) -> float:
        """Get percentage of misses due to projection errors."""
        total = len(self._classifications)
        if total == 0:
            return 0.0
        projection_errors = self._category_counts[MissCategory.PROJECTION_ERROR]
        return projection_errors / total

    def get_market_trap_rate(self) -> float:
        """Get percentage of misses that were market traps."""
        total = len(self._classifications)
        if total == 0:
            return 0.0
        market_traps = self._category_counts[MissCategory.MARKET_TRAP]
        return market_traps / total

    def get_classifications_by_category(
        self,
        category: MissCategory,
    ) -> list[MissClassification]:
        """Get all classifications for a specific category."""
        return [
            c for c in self._classifications.values()
            if c.category == category
        ]

    def export_classification_report(self) -> dict[str, Any]:
        """Export complete classification report."""
        return {
            "summary": self.analyze_miss_patterns(),
            "projection_error_rate": self.get_projection_error_rate(),
            "market_trap_rate": self.get_market_trap_rate(),
            "all_classifications": [
                c.to_dict() for c in self._classifications.values()
            ],
        }
