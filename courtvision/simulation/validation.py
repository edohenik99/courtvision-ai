"""Board validation layer for high-risk play rejection.

Filters out plays that:
- Rely on narrow outcome ranges
- Have high variance risk
- Collapse under slight assumption changes

Phase 9: Scenario Simulation and Forward Validation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from courtvision.simulation.ev_calculation import EVResult
from courtvision.simulation.robustness import RobustnessReport
from courtvision.simulation.simulation_engine import SimulationSummary


class RejectionReason(str, Enum):
    """Reasons for rejecting a play."""

    NARROW_OUTCOMES = "narrow_outcomes"
    HIGH_VARIANCE = "high_variance"
    LOW_HIT_PROBABILITY = "low_hit_probability"
    NEGATIVE_EV = "negative_ev"
    LOW_CONFIDENCE = "low_confidence"
    NOT_ROBUST = "not_robust"
    EXTREME_SENSITIVITY = "extreme_sensitivity"


@dataclass
class ValidationResult:
    """Result of board validation for a single play."""

    player_name: str
    stat_type: str
    line_value: float

    # Validation status
    approved: bool
    rejection_reasons: list[RejectionReason] = field(default_factory=list)

    # Scores
    ev_score: float = 0.0
    robustness_score: float = 0.0
    variance_score: float = 0.0
    confidence_score: float = 0.0
    overall_score: float = 0.0

    # Details
    simulation_summary: SimulationSummary | None = None
    ev_result: EVResult | None = None
    robustness_report: RobustnessReport | None = None

    # Recommendation
    recommendation: str = ""  # "strong_play", "play", "marginal", "avoid"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "play": {
                "player": self.player_name,
                "stat": self.stat_type,
                "line": self.line_value,
            },
            "status": {
                "approved": self.approved,
                "rejection_reasons": [r.value for r in self.rejection_reasons],
            },
            "scores": {
                "ev": round(self.ev_score, 3),
                "robustness": round(self.robustness_score, 3),
                "variance": round(self.variance_score, 3),
                "confidence": round(self.confidence_score, 3),
                "overall": round(self.overall_score, 3),
            },
            "recommendation": self.recommendation,
        }


@dataclass
class BoardValidationSummary:
    """Summary of board validation results."""

    total_plays: int
    approved_count: int
    rejected_count: int

    approved_plays: list[ValidationResult] = field(default_factory=list)
    rejected_plays: list[ValidationResult] = field(default_factory=list)

    # Statistics
    avg_ev_score: float = 0.0
    avg_robustness: float = 0.0
    avg_overall_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "summary": {
                "total": self.total_plays,
                "approved": self.approved_count,
                "rejected": self.rejected_count,
                "approval_rate": round(self.approved_count / self.total_plays, 3) if self.total_plays > 0 else 0,
            },
            "statistics": {
                "avg_ev": round(self.avg_ev_score, 3),
                "avg_robustness": round(self.avg_robustness, 3),
                "avg_overall": round(self.avg_overall_score, 3),
            },
            "approved_plays": [p.to_dict() for p in self.approved_plays],
            "rejected_plays": [p.to_dict() for p in self.rejected_plays],
        }


class BoardValidator:
    """Validate and filter board plays based on simulation results.

    Applies multiple validation criteria:
    - EV must be positive
    - Hit probability must be above threshold
    - Must pass robustness tests
    - Variance must be acceptable
    - Confidence must be sufficient
    """

    # Validation thresholds
    MIN_EV = 0.03              # 3% minimum expected value
    MIN_HIT_PROBABILITY = 0.52  # 52% minimum hit rate
    MIN_ROBUSTNESS_SCORE = 0.6  # 60% of scenarios must remain viable
    MAX_CV = 0.30              # 30% max coefficient of variation
    MIN_CONFIDENCE = 0.50      # 50% minimum confidence

    # Scoring weights
    EV_WEIGHT = 0.35
    ROBUSTNESS_WEIGHT = 0.30
    CONFIDENCE_WEIGHT = 0.20
    VARIANCE_WEIGHT = 0.15

    def __init__(
        self,
        min_ev: float = MIN_EV,
        min_hit_prob: float = MIN_HIT_PROBABILITY,
        min_robustness: float = MIN_ROBUSTNESS_SCORE,
        max_cv: float = MAX_CV,
        min_confidence: float = MIN_CONFIDENCE,
    ) -> None:
        """Initialize board validator.

        Args:
            min_ev: Minimum EV threshold
            min_hit_prob: Minimum hit probability
            min_robustness: Minimum robustness score
            max_cv: Maximum coefficient of variation
            min_confidence: Minimum confidence score
        """
        self.min_ev = min_ev
        self.min_hit_prob = min_hit_prob
        self.min_robustness = min_robustness
        self.max_cv = max_cv
        self.min_confidence = min_confidence

    def validate_play(
        self,
        player_name: str,
        stat_type: str,
        line_value: float,
        simulation_summary: SimulationSummary,
        ev_result: EVResult | None = None,
        robustness_report: RobustnessReport | None = None,
    ) -> ValidationResult:
        """Validate a single play.

        Args:
            player_name: Player name
            stat_type: Stat type
            line_value: Betting line
            simulation_summary: Simulation results
            ev_result: EV calculation (optional)
            robustness_report: Robustness test results (optional)

        Returns:
            ValidationResult with approval status
        """
        rejection_reasons: list[RejectionReason] = []

        # Check 1: Hit probability
        hit_prob = simulation_summary.hit_probability
        if hit_prob < self.min_hit_prob:
            rejection_reasons.append(RejectionReason.LOW_HIT_PROBABILITY)

        # Check 2: Variance
        cv = simulation_summary.std_deviation / simulation_summary.mean_outcome if simulation_summary.mean_outcome > 0 else 0
        if cv > self.max_cv:
            rejection_reasons.append(RejectionReason.HIGH_VARIANCE)

        # Check 3: Narrow outcomes (tight distribution around line)
        # Check if 90% of outcomes fall within +/- 10% of line
        line_range = line_value * 0.10
        outcomes_near_line = sum(
            1 for o in simulation_summary.all_outcomes
            if abs(o - line_value) < line_range
        )
        narrow_outcome_pct = outcomes_near_line / len(simulation_summary.all_outcomes) if simulation_summary.all_outcomes else 0

        if narrow_outcome_pct > 0.70:  # 70%+ outcomes very close to line
            rejection_reasons.append(RejectionReason.NARROW_OUTCOMES)

        # Check 4: EV
        ev_score = 0.0
        if ev_result:
            if ev_result.expected_value < self.min_ev:
                rejection_reasons.append(RejectionReason.NEGATIVE_EV)
            ev_score = min(1.0, max(0, ev_result.expected_value) / 0.15)  # Normalize to 0-1

        # Check 5: Robustness
        robustness_score = 0.0
        if robustness_report:
            survival_rate = (
                robustness_report.scenarios_survived / robustness_report.total_scenarios
                if robustness_report.total_scenarios > 0 else 0
            )
            if survival_rate < self.min_robustness:
                rejection_reasons.append(RejectionReason.NOT_ROBUST)

            # Check extreme sensitivity
            if robustness_report.max_hit_prob_drop > 0.20:  # >20% drop in any scenario
                rejection_reasons.append(RejectionReason.EXTREME_SENSITIVITY)

            robustness_score = survival_rate

        # Check 6: Confidence
        confidence_score = 0.0
        if ev_result:
            if ev_result.confidence_score < self.min_confidence:
                rejection_reasons.append(RejectionReason.LOW_CONFIDENCE)
            confidence_score = ev_result.confidence_score

        # Calculate variance score (inverse of CV, normalized)
        variance_score = max(0, 1 - cv / self.max_cv)

        # Calculate overall score (weighted average)
        overall_score = (
            self.EV_WEIGHT * ev_score +
            self.ROBUSTNESS_WEIGHT * robustness_score +
            self.CONFIDENCE_WEIGHT * confidence_score +
            self.VARIANCE_WEIGHT * variance_score
        )

        # Determine recommendation
        if overall_score >= 0.85 and len(rejection_reasons) == 0:
            recommendation = "strong_play"
        elif overall_score >= 0.70 and len(rejection_reasons) <= 1:
            recommendation = "play"
        elif overall_score >= 0.50:
            recommendation = "marginal"
        else:
            recommendation = "avoid"

        return ValidationResult(
            player_name=player_name,
            stat_type=stat_type,
            line_value=line_value,
            approved=len(rejection_reasons) == 0,
            rejection_reasons=rejection_reasons,
            ev_score=ev_score,
            robustness_score=robustness_score,
            variance_score=variance_score,
            confidence_score=confidence_score,
            overall_score=overall_score,
            simulation_summary=simulation_summary,
            ev_result=ev_result,
            robustness_report=robustness_report,
            recommendation=recommendation,
        )

    def validate_board(
        self,
        plays: list[dict],
    ) -> BoardValidationSummary:
        """Validate a full board of plays.

        Args:
            plays: List of play dicts with keys:
                - player_name, stat_type, line_value
                - simulation_summary (required)
                - ev_result (optional)
                - robustness_report (optional)

        Returns:
            BoardValidationSummary
        """
        results: list[ValidationResult] = []

        for play in plays:
            result = self.validate_play(
                player_name=play["player_name"],
                stat_type=play["stat_type"],
                line_value=play["line_value"],
                simulation_summary=play["simulation_summary"],
                ev_result=play.get("ev_result"),
                robustness_report=play.get("robustness_report"),
            )
            results.append(result)

        approved = [r for r in results if r.approved]
        rejected = [r for r in results if not r.approved]

        # Calculate statistics
        if results:
            avg_ev = sum(r.ev_score for r in results) / len(results)
            avg_robustness = sum(r.robustness_score for r in results) / len(results)
            avg_overall = sum(r.overall_score for r in results) / len(results)
        else:
            avg_ev = avg_robustness = avg_overall = 0.0

        return BoardValidationSummary(
            total_plays=len(results),
            approved_count=len(approved),
            rejected_count=len(rejected),
            approved_plays=approved,
            rejected_plays=rejected,
            avg_ev_score=avg_ev,
            avg_robustness=avg_robustness,
            avg_overall_score=avg_overall,
        )

    def filter_board(
        self,
        plays: list[dict],
        min_overall_score: float = 0.70,
    ) -> list[ValidationResult]:
        """Filter board to only high-quality plays.

        Args:
            plays: List of play dicts
            min_overall_score: Minimum overall score to include

        Returns:
            List of approved ValidationResults
        """
        summary = self.validate_board(plays)

        # Filter by overall score
        filtered = [
            r for r in summary.approved_plays
            if r.overall_score >= min_overall_score
        ]

        # Sort by overall score (descending)
        filtered.sort(key=lambda r: r.overall_score, reverse=True)

        return filtered

    def get_rejection_analysis(
        self,
        summary: BoardValidationSummary,
    ) -> dict[str, Any]:
        """Analyze why plays were rejected.

        Args:
            summary: Board validation summary

        Returns:
            Rejection analysis
        """
        if not summary.rejected_plays:
            return {"message": "No plays were rejected"}

        # Count reasons
        reason_counts: dict[str, int] = {}
        for play in summary.rejected_plays:
            for reason in play.rejection_reasons:
                reason_counts[reason.value] = reason_counts.get(reason.value, 0) + 1

        # Identify most common rejection reason
        most_common = max(reason_counts.items(), key=lambda x: x[1]) if reason_counts else ("none", 0)

        # Calculate impact
        total_rejections = sum(reason_counts.values())

        return {
            "total_rejected_plays": len(summary.rejected_plays),
            "total_rejection_reasons": total_rejections,
            "reason_breakdown": {
                reason: {
                    "count": count,
                    "percentage": round(count / len(summary.rejected_plays) * 100, 1),
                }
                for reason, count in reason_counts.items()
            },
            "most_common_reason": most_common[0],
            "recommendations": self._generate_recommendations(reason_counts),
        }

    def _generate_recommendations(
        self,
        reason_counts: dict[str, int],
    ) -> list[str]:
        """Generate recommendations based on rejection patterns."""
        recommendations = []

        if reason_counts.get(RejectionReason.HIGH_VARIANCE.value, 0) > 5:
            recommendations.append(
                "High variance plays dominating rejects - consider tightening variance filters or requiring higher EV"
            )

        if reason_counts.get(RejectionReason.NEGATIVE_EV.value, 0) > 5:
            recommendations.append(
                "Many negative EV plays - review odds selection or improve projection accuracy"
            )

        if reason_counts.get(RejectionReason.NOT_ROBUST.value, 0) > 5:
            recommendations.append(
                "Many non-robust plays - plays collapse under assumption changes, require stronger edges"
            )

        if reason_counts.get(RejectionReason.NARROW_OUTCOMES.value, 0) > 3:
            recommendations.append(
                "Narrow outcome range detected - market lines may be very accurate for these plays"
            )

        if not recommendations:
            recommendations.append("Rejection distribution is balanced - no systematic issues detected")

        return recommendations
