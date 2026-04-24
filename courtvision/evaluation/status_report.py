"""Prove-it status command/report for system calibration validation.

Answers:
- is the system calibrated yet?
- is CLV positive yet?
- are adaptive thresholds helping?
- is portfolio optimization reducing concentration risk?
- is there enough sample size to trust results yet?

Classifies system as:
- insufficient_data
- early_signal
- promising
- validated
- unstable

OPERATIONS + VALIDATION mode - Measurement discipline only.

Task 5: Add "prove it" status command/report
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from courtvision.evaluation.report_builder import ReportBuilder, RollingWindow
from courtvision.evaluation.weekly_report import WeeklyReport
from courtvision.shadow_run.artifact import ShadowRunArtifact


@dataclass
class CalibrationStatus:
    """Status of a single calibration dimension."""

    dimension: str
    sufficient_data: bool
    meets_target: bool
    status: str  # "pending", "met", "exceeded", "degraded"
    value: float | None
    target: float
    minimum_samples: int
    actual_samples: int
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProveItReport:
    """Complete prove-it status report."""

    generated_at: str
    system_status: str  # insufficient_data, early_signal, promising, validated, unstable
    overall_confidence: str  # low, medium, high

    # Individual calibration dimensions
    confidence_calibration: CalibrationStatus
    clv_status: CalibrationStatus
    adaptive_thresholds: CalibrationStatus
    portfolio_optimization: CalibrationStatus
    sample_size: CalibrationStatus

    # Aggregated metrics
    total_picks: int
    weeks_of_data: int
    calibration_score: float
    avg_clv: float

    # Evidence summary
    evidence_for: list[str]
    evidence_against: list[str]
    next_milestones: list[str]

    # Recommendations
    recommendations: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "metadata": {
                "generated_at": self.generated_at,
                "version": "1.0",
            },
            "system_status": self.system_status,
            "overall_confidence": self.overall_confidence,
            "calibration_status": {
                "confidence_calibration": self._status_to_dict(self.confidence_calibration),
                "clv": self._status_to_dict(self.clv_status),
                "adaptive_thresholds": self._status_to_dict(self.adaptive_thresholds),
                "portfolio_optimization": self._status_to_dict(self.portfolio_optimization),
                "sample_size": self._status_to_dict(self.sample_size),
            },
            "aggregated_metrics": {
                "total_picks": self.total_picks,
                "weeks_of_data": self.weeks_of_data,
                "calibration_score": round(self.calibration_score, 3),
                "avg_clv": round(self.avg_clv, 4),
            },
            "evidence": {
                "for": self.evidence_for,
                "against": self.evidence_against,
            },
            "next_milestones": self.next_milestones,
            "recommendations": self.recommendations,
        }

    def _status_to_dict(self, status: CalibrationStatus) -> dict[str, Any]:
        """Convert CalibrationStatus to dict."""
        return {
            "dimension": status.dimension,
            "sufficient_data": status.sufficient_data,
            "meets_target": status.meets_target,
            "status": status.status,
            "value": round(status.value, 4) if status.value else None,
            "target": status.target,
            "samples": {
                "minimum": status.minimum_samples,
                "actual": status.actual_samples,
            },
            "details": status.details,
        }

    def to_markdown(self) -> str:
        """Generate human-readable markdown report."""
        lines = [
            "# Prove-It Status Report",
            "",
            f"**Generated**: {self.generated_at}",
            f"**System Status**: {self.system_status.upper()}",
            f"**Overall Confidence**: {self.overall_confidence.upper()}",
            "",
            "## Aggregated Metrics",
            f"- Total picks: {self.total_picks}",
            f"- Weeks of data: {self.weeks_of_data}",
            f"- Calibration score: {self.calibration_score:.3f}",
            f"- Average CLV: {self.avg_clv:.2%}",
            "",
            "## Calibration Dimensions",
            "",
        ]

        # Confidence calibration
        cc = self.confidence_calibration
        lines.extend([
            f"### 1. Confidence Calibration",
            f"**Status**: {cc.status.upper()}",
            f"- Current score: {cc.value:.3f} (target: {cc.target:.3f})",
            f"- Samples: {cc.actual_samples} (need {cc.minimum_samples})",
            f"- Sufficient data: {'Yes' if cc.sufficient_data else 'No'}",
            "",
        ])

        # CLV status
        clv = self.clv_status
        lines.extend([
            f"### 2. CLV Status",
            f"**Status**: {clv.status.upper()}",
            f"- Current: {clv.value:.2%} (target: {clv.target:.2%})",
            f"- Samples: {clv.actual_samples} (need {clv.minimum_samples})",
            f"- Sufficient data: {'Yes' if clv.sufficient_data else 'No'}",
            "",
        ])

        # Adaptive thresholds
        at = self.adaptive_thresholds
        lines.extend([
            f"### 3. Adaptive Thresholds",
            f"**Status**: {at.status.upper()}",
            f"- Effectiveness: {at.value:.3f} (target: {at.target:.3f})",
            f"- Samples: {at.actual_samples} (need {at.minimum_samples})",
            f"- Sufficient data: {'Yes' if at.sufficient_data else 'No'}",
            "",
        ])

        # Portfolio optimization
        po = self.portfolio_optimization
        lines.extend([
            f"### 4. Portfolio Optimization",
            f"**Status**: {po.status.upper()}",
            f"- Concentration risk: {po.value:.3f} (target: {po.target:.3f})",
            f"- Samples: {po.actual_samples} (need {po.minimum_samples})",
            f"- Sufficient data: {'Yes' if po.sufficient_data else 'No'}",
            "",
        ])

        # Sample size
        ss = self.sample_size
        lines.extend([
            f"### 5. Sample Size",
            f"**Status**: {ss.status.upper()}",
            f"- Total picks: {ss.actual_samples} (need {ss.minimum_samples})",
            f"- Sufficient: {'Yes' if ss.sufficient_data else 'No'}",
            "",
        ])

        # Evidence
        lines.extend([
            "## Evidence Summary",
            "",
            "### Supporting Evidence",
        ])
        for item in self.evidence_for:
            lines.append(f"- ✓ {item}")

        lines.extend([
            "",
            "### Contradicting Evidence",
        ])
        for item in self.evidence_against:
            lines.append(f"- ✗ {item}")

        # Next milestones
        lines.extend([
            "",
            "## Next Milestones",
        ])
        for milestone in self.next_milestones:
            lines.append(f"- ☐ {milestone}")

        # Recommendations
        lines.extend([
            "",
            "## Recommendations",
        ])
        for rec in self.recommendations:
            lines.append(f"- {rec}")

        lines.extend([
            "",
            "---",
            "*Generated by CourtVision Prove-It System*",
        ])

        return "\n".join(lines)

    def save(self, json_path: str, md_path: str | None = None) -> None:
        """Save report to JSON and optional Markdown."""
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

        if md_path:
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(self.to_markdown())


class ProveItCommand:
    """Command to generate prove-it status report.

    Usage:
        cmd = ProveItCommand()
        report = cmd.run(artifacts, weekly_reports)

        # Or as CLI:
        # python -m courtvision.evaluation.status_report --artifacts-dir ./shadow_runs
    """

    def __init__(self) -> None:
        """Initialize command."""
        pass

    def run(
        self,
        artifacts: list[ShadowRunArtifact],
        weekly_reports: list[WeeklyReport] | None = None,
    ) -> ProveItReport:
        """Generate prove-it report from accumulated data.

        Args:
            artifacts: All shadow run artifacts
            weekly_reports: Weekly evaluation reports (optional)

        Returns:
            ProveItReport with system status
        """
        # Build comprehensive pick history
        builder = ReportBuilder(RollingWindow(window_size=500, min_samples=30))

        for artifact in artifacts:
            for entry in artifact.entries:
                if entry.recommended and entry.hit is not None:
                    builder.add_pick(
                        pick_id=entry.entry_id,
                        prediction_date=entry.prediction_date,
                        player_name=entry.player_name,
                        stat_type=entry.stat_type,
                        over_under=entry.over_under,
                        line_value=entry.line_value,
                        confidence=entry.confidence,
                        edge=entry.edge,
                        ev=entry.ev,
                        hit=entry.hit,
                        clv=entry.clv if entry.clv else 0.0,
                    )

        eval_report = builder.build_report()

        # Calculate weeks of data
        unique_dates = set()
        for artifact in artifacts:
            unique_dates.add(artifact.prediction_date)
        weeks_of_data = len(unique_dates) / 7  # Approximate

        # Use weekly reports if available, otherwise derive from artifacts
        latest_weekly = weekly_reports[-1] if weekly_reports else None

        total_picks = eval_report.total_picks if eval_report else 0
        calibration_score = eval_report.calibration_score if eval_report else 0.0
        avg_clv = self._calculate_avg_clv(builder.pick_history)

        # Build individual calibration statuses
        confidence_status = self._assess_confidence_calibration(
            eval_report, total_picks
        )
        clv_status = self._assess_clv_status(builder.pick_history, total_picks)
        adaptive_status = self._assess_adaptive_thresholds(artifacts, total_picks)
        portfolio_status = self._assess_portfolio_optimization(
            latest_weekly, total_picks
        )
        sample_status = self._assess_sample_size(total_picks)

        # Classify overall system status
        system_status = self._classify_system(
            confidence_status,
            clv_status,
            adaptive_status,
            portfolio_status,
            sample_status,
            latest_weekly,
        )

        # Assess overall confidence
        overall_confidence = self._assess_overall_confidence(
            confidence_status,
            clv_status,
            adaptive_status,
            portfolio_status,
            sample_status,
        )

        # Gather evidence
        evidence_for, evidence_against = self._gather_evidence(
            confidence_status,
            clv_status,
            adaptive_status,
            portfolio_status,
            sample_status,
            latest_weekly,
        )

        # Next milestones
        milestones = self._determine_milestones(
            confidence_status,
            clv_status,
            adaptive_status,
            portfolio_status,
            sample_status,
        )

        # Recommendations
        recommendations = self._generate_recommendations(
            system_status,
            confidence_status,
            clv_status,
            adaptive_status,
            portfolio_status,
            sample_status,
        )

        return ProveItReport(
            generated_at=datetime.now().isoformat(),
            system_status=system_status,
            overall_confidence=overall_confidence,
            confidence_calibration=confidence_status,
            clv_status=clv_status,
            adaptive_thresholds=adaptive_status,
            portfolio_optimization=portfolio_status,
            sample_size=sample_status,
            total_picks=total_picks,
            weeks_of_data=round(weeks_of_data, 1),
            calibration_score=calibration_score,
            avg_clv=avg_clv,
            evidence_for=evidence_for,
            evidence_against=evidence_against,
            next_milestones=milestones,
            recommendations=recommendations,
        )

    def _calculate_avg_clv(self, picks: list[dict]) -> float:
        """Calculate average CLV from pick history."""
        if not picks:
            return 0.0
        clvs = [p.get("clv", 0) for p in picks]
        return sum(clvs) / len(clvs)

    def _assess_confidence_calibration(
        self, eval_report: Any, total_picks: int
    ) -> CalibrationStatus:
        """Assess confidence calibration status."""
        min_samples = 100

        if not eval_report or total_picks < min_samples:
            return CalibrationStatus(
                dimension="confidence_calibration",
                sufficient_data=False,
                meets_target=False,
                status="pending",
                value=None,
                target=0.7,
                minimum_samples=min_samples,
                actual_samples=total_picks,
                details={"reason": "insufficient_samples"},
            )

        score = eval_report.calibration_score
        meets_target = score >= 0.7

        status = "met" if meets_target else "pending"
        if score > 0.8:
            status = "exceeded"

        return CalibrationStatus(
            dimension="confidence_calibration",
            sufficient_data=True,
            meets_target=meets_target,
            status=status,
            value=score,
            target=0.7,
            minimum_samples=min_samples,
            actual_samples=total_picks,
            details={"buckets_analyzed": len(eval_report.confidence_buckets)},
        )

    def _assess_clv_status(
        self, picks: list[dict], total_picks: int
    ) -> CalibrationStatus:
        """Assess CLV status."""
        min_samples = 50

        if total_picks < min_samples:
            return CalibrationStatus(
                dimension="clv",
                sufficient_data=False,
                meets_target=False,
                status="pending",
                value=None,
                target=0.01,
                minimum_samples=min_samples,
                actual_samples=total_picks,
                details={"reason": "insufficient_samples"},
            )

        avg_clv = self._calculate_avg_clv(picks)
        meets_target = avg_clv >= 0.01

        status = "met" if meets_target else "pending"
        if avg_clv > 0.02:
            status = "exceeded"

        # Calculate correlation
        correlation = None
        if total_picks >= 30:
            try:
                import numpy as np
                clvs = [p.get("clv", 0) for p in picks]
                hits = [1 if p.get("hit") else 0 for p in picks]
                if len(clvs) == len(hits) and len(clvs) > 5:
                    correlation = np.corrcoef(clvs, hits)[0, 1]
            except:
                pass

        return CalibrationStatus(
            dimension="clv",
            sufficient_data=True,
            meets_target=meets_target,
            status=status,
            value=avg_clv,
            target=0.01,
            minimum_samples=min_samples,
            actual_samples=total_picks,
            details={"correlation_with_hits": correlation},
        )

    def _assess_adaptive_thresholds(
        self, artifacts: list[ShadowRunArtifact], total_picks: int
    ) -> CalibrationStatus:
        """Assess adaptive thresholds effectiveness."""
        min_samples = 100

        if total_picks < min_samples:
            return CalibrationStatus(
                dimension="adaptive_thresholds",
                sufficient_data=False,
                meets_target=False,
                status="pending",
                value=None,
                target=0.5,
                minimum_samples=min_samples,
                actual_samples=total_picks,
                details={"reason": "insufficient_samples"},
            )

        # Extract threshold history
        threshold_changes = []
        for artifact in artifacts:
            for entry in artifact.entries:
                if entry.thresholds_used:
                    threshold_changes.append(entry.thresholds_used.get("edge", 0.05))
                    break

        if len(threshold_changes) < 2:
            return CalibrationStatus(
                dimension="adaptive_thresholds",
                sufficient_data=False,
                meets_target=False,
                status="pending",
                value=None,
                target=0.5,
                minimum_samples=5,
                actual_samples=len(threshold_changes),
                details={"reason": "insufficient_threshold_history"},
            )

        # Calculate stability (inverse of volatility)
        if len(threshold_changes) > 1:
            mean = sum(threshold_changes) / len(threshold_changes)
            variance = sum((t - mean) ** 2 for t in threshold_changes) / len(threshold_changes)
            volatility = variance ** 0.5
            stability = max(0, 1 - (volatility / 0.05))
        else:
            stability = 1.0

        meets_target = stability >= 0.5
        status = "met" if meets_target else "degraded"

        return CalibrationStatus(
            dimension="adaptive_thresholds",
            sufficient_data=True,
            meets_target=meets_target,
            status=status,
            value=stability,
            target=0.5,
            minimum_samples=min_samples,
            actual_samples=total_picks,
            details={
                "threshold_changes": len(threshold_changes),
                "volatility": volatility if len(threshold_changes) > 1 else 0,
            },
        )

    def _assess_portfolio_optimization(
        self, weekly_report: WeeklyReport | None, total_picks: int
    ) -> CalibrationStatus:
        """Assess portfolio optimization effectiveness."""
        min_samples = 50

        if not weekly_report or total_picks < min_samples:
            return CalibrationStatus(
                dimension="portfolio_optimization",
                sufficient_data=False,
                meets_target=False,
                status="pending",
                value=None,
                target=0.5,
                minimum_samples=min_samples,
                actual_samples=total_picks,
                details={"reason": "insufficient_data"},
            )

        # Use drawdown as proxy for concentration risk
        drawdown = weekly_report.portfolio_drawdown
        max_drawdown = 5.0  # 5 units

        # Lower drawdown = better optimization
        score = max(0, 1 - (drawdown / max_drawdown))
        meets_target = score >= 0.5

        status = "met" if meets_target else "pending"

        return CalibrationStatus(
            dimension="portfolio_optimization",
            sufficient_data=True,
            meets_target=meets_target,
            status=status,
            value=score,
            target=0.5,
            minimum_samples=min_samples,
            actual_samples=total_picks,
            details={
                "drawdown": drawdown,
                "volatility": weekly_report.portfolio_volatility,
            },
        )

    def _assess_sample_size(self, total_picks: int) -> CalibrationStatus:
        """Assess overall sample size sufficiency."""
        min_samples = 200  # For full validation

        sufficient = total_picks >= min_samples
        meets_target = total_picks >= min_samples

        if total_picks < 50:
            status = "pending"
        elif total_picks < 200:
            status = "early_signal"
        else:
            status = "met"

        return CalibrationStatus(
            dimension="sample_size",
            sufficient_data=sufficient,
            meets_target=meets_target,
            status=status,
            value=float(total_picks),
            target=float(min_samples),
            minimum_samples=min_samples,
            actual_samples=total_picks,
            details={"weeks_needed": max(0, (min_samples - total_picks) // 30)},
        )

    def _classify_system(
        self,
        confidence: CalibrationStatus,
        clv: CalibrationStatus,
        adaptive: CalibrationStatus,
        portfolio: CalibrationStatus,
        sample: CalibrationStatus,
        weekly: WeeklyReport | None,
    ) -> str:
        """Classify overall system status."""
        # Check for instability first
        if weekly and weekly.instability_flags:
            return "unstable"

        if weekly and weekly.overfitting_warnings and len(weekly.overfitting_warnings) > 1:
            return "unstable"

        # Check sample size
        if not sample.sufficient_data:
            return "insufficient_data"

        # Check for degradation
        if adaptive.status == "degraded":
            return "early_signal"

        # Count met targets
        met_count = sum([
            confidence.meets_target,
            clv.meets_target,
            adaptive.meets_target,
            portfolio.meets_target,
        ])

        if met_count >= 4 and sample.meets_target:
            return "validated"

        if met_count >= 2:
            return "promising"

        return "early_signal"

    def _assess_overall_confidence(
        self,
        confidence: CalibrationStatus,
        clv: CalibrationStatus,
        adaptive: CalibrationStatus,
        portfolio: CalibrationStatus,
        sample: CalibrationStatus,
    ) -> str:
        """Assess overall confidence level."""
        scores = [
            1 if confidence.sufficient_data and confidence.meets_target else 0,
            1 if clv.sufficient_data and clv.meets_target else 0,
            1 if adaptive.sufficient_data and adaptive.meets_target else 0,
            1 if portfolio.sufficient_data and portfolio.meets_target else 0,
            1 if sample.sufficient_data else 0,
        ]

        total = sum(scores)

        if total >= 4:
            return "high"
        elif total >= 2:
            return "medium"
        else:
            return "low"

    def _gather_evidence(
        self,
        confidence: CalibrationStatus,
        clv: CalibrationStatus,
        adaptive: CalibrationStatus,
        portfolio: CalibrationStatus,
        sample: CalibrationStatus,
        weekly: WeeklyReport | None,
    ) -> tuple[list[str], list[str]]:
        """Gather evidence for and against system validity."""
        evidence_for = []
        evidence_against = []

        # Confidence calibration
        if confidence.meets_target:
            evidence_for.append(f"Confidence calibration score {confidence.value:.3f} meets target")
        else:
            evidence_against.append(f"Confidence calibration below target ({confidence.value:.3f} < {confidence.target:.3f})")

        # CLV
        if clv.meets_target:
            evidence_for.append(f"Positive CLV ({clv.value:.2%}) indicates market edge")
        else:
            evidence_against.append(f"CLV below target ({clv.value:.2%} < {clv.target:.2%})")

        # Adaptive thresholds
        if adaptive.meets_target:
            evidence_for.append("Adaptive thresholds are stable and helping")
        else:
            evidence_against.append("Adaptive thresholds showing instability")

        # Portfolio
        if portfolio.meets_target:
            evidence_for.append("Portfolio optimization reducing concentration risk")
        else:
            evidence_against.append("Portfolio drawdown higher than expected")

        # Sample size
        if sample.sufficient_data:
            evidence_for.append(f"Sufficient sample size ({sample.actual_samples} picks)")
        else:
            evidence_against.append(f"Insufficient samples ({sample.actual_samples} < {sample.minimum_samples})")

        # Weekly report flags
        if weekly:
            if weekly.calibration_score > 0.6:
                evidence_for.append("Weekly calibration score healthy")
            if weekly.avg_clv > 0:
                evidence_for.append("Weekly CLV positive")

        return evidence_for, evidence_against

    def _determine_milestones(
        self,
        confidence: CalibrationStatus,
        clv: CalibrationStatus,
        adaptive: CalibrationStatus,
        portfolio: CalibrationStatus,
        sample: CalibrationStatus,
    ) -> list[str]:
        """Determine next milestones to reach."""
        milestones = []

        if not sample.sufficient_data:
            needed = sample.minimum_samples - sample.actual_samples
            milestones.append(f"Accumulate {needed} more picks (currently {sample.actual_samples})")

        if not confidence.sufficient_data:
            milestones.append("Reach 100 picks in high-confidence bucket")

        if not confidence.meets_target:
            milestones.append(f"Improve calibration score to {confidence.target:.3f}")

        if not clv.meets_target:
            milestones.append(f"Achieve consistent CLV above {clv.target:.2%}")

        if not adaptive.meets_target:
            milestones.append("Stabilize adaptive threshold adjustments")

        if not portfolio.meets_target:
            milestones.append("Reduce portfolio drawdown below 3 units")

        if not milestones:
            milestones.append("Maintain current performance for 4 more weeks")
            milestones.append("Consider gradual increase in position sizing")

        return milestones

    def _generate_recommendations(
        self,
        system_status: str,
        confidence: CalibrationStatus,
        clv: CalibrationStatus,
        adaptive: CalibrationStatus,
        portfolio: CalibrationStatus,
        sample: CalibrationStatus,
    ) -> list[str]:
        """Generate actionable recommendations."""
        recommendations = []

        if system_status == "insufficient_data":
            recommendations.append("Continue shadow run mode for 2-4 more weeks")
            recommendations.append("Do not increase exposure until sample size requirement met")

        elif system_status == "unstable":
            recommendations.append("SWITCH TO CONSERVATIVE MODE IMMEDIATELY")
            recommendations.append("Disable adaptive thresholds until stability returns")
            recommendations.append("Review recent threshold adjustments for errors")

        elif system_status == "early_signal":
            recommendations.append("Maintain current shadow run mode")
            recommendations.append("Focus on accumulating clean samples")
            recommendations.append("Review miss classifications weekly")

        elif system_status == "promising":
            recommendations.append("Continue shadow mode, system showing positive signs")
            if not confidence.meets_target:
                recommendations.append("Investigate confidence calibration - may need threshold tweaks")
            if not clv.meets_target:
                recommendations.append("Monitor line timing - may be entering too early/late")

        elif system_status == "validated":
            recommendations.append("System validated - consider gradual live deployment")
            recommendations.append("Maintain 50% position size for first 2 weeks live")
            recommendations.append("Continue shadow run in parallel for validation")

        # Mode-specific advice
        if adaptive.status == "degraded":
            recommendations.append("Temporarily disable market-adaptive thresholds")

        if portfolio.status == "pending":
            recommendations.append("Review portfolio concentration - may need stricter limits")

        return recommendations


def prove_it(
    artifacts: list[ShadowRunArtifact],
    weekly_reports: list[WeeklyReport] | None = None,
    output_dir: str = "./prove_it_reports",
) -> ProveItReport:
    """Convenience function to generate and save prove-it report.

    Args:
        artifacts: All shadow run artifacts
        weekly_reports: Weekly evaluation reports (optional)
        output_dir: Directory for output files

    Returns:
        Generated ProveItReport
    """
    import os

    cmd = ProveItCommand()
    report = cmd.run(artifacts, weekly_reports)

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Generate file paths with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(output_dir, f"prove_it_report_{timestamp}.json")
    md_path = os.path.join(output_dir, f"prove_it_report_{timestamp}.md")

    # Save
    report.save(json_path, md_path)

    return report
