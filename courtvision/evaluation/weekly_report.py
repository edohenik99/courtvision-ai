"""Weekly evaluation report generator.

At end of each 7-day block, generates:
- confidence calibration by bucket
- EV realization by bucket
- CLV consistency
- hit rate by stat type
- hit rate by market regime
- portfolio drawdown / volatility
- top miss categories
- most reliable/harmful signals
- threshold changes over time
- instability/overfitting detection

Uses `courtvision/evaluation/` as source of truth.

OPERATIONS + VALIDATION mode - Weekly measurement discipline only.

Task 3: Create weekly evaluation report
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from courtvision.evaluation.report_builder import BucketMetrics, ReportBuilder, RollingWindow
from courtvision.shadow_run.artifact import ShadowRunArtifact


@dataclass
class WeeklyReport:
    """Weekly evaluation report artifact."""

    # Identification
    report_id: str
    week_start: str
    week_end: str
    generated_at: str

    # Sample size
    total_picks: int
    sufficient_data: bool

    # Confidence calibration
    confidence_calibration: dict[str, dict[str, Any]]
    calibration_score: float

    # EV realization
    ev_by_bucket: dict[str, dict[str, Any]]

    # CLV consistency
    avg_clv: float
    clv_hit_correlation: float | None

    # Hit rate by dimensions
    hit_rate_by_stat_type: dict[str, float]
    hit_rate_by_regime: dict[str, float]

    # Portfolio health
    portfolio_drawdown: float
    portfolio_volatility: float
    max_consecutive_losses: int

    # Signal analysis
    top_miss_categories: list[tuple[str, int]]
    reliable_signals: list[dict[str, Any]]
    harmful_signals: list[dict[str, Any]]

    # Threshold tracking
    threshold_history: list[dict[str, Any]]
    threshold_stability_score: float

    # Instability detection
    instability_flags: list[str]
    overfitting_warnings: list[str]

    # Overall classification
    system_status: str  # insufficient_data, early_signal, promising, validated, unstable

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "metadata": {
                "report_id": self.report_id,
                "week_start": self.week_start,
                "week_end": self.week_end,
                "generated_at": self.generated_at,
                "version": "1.0",
            },
            "sample_size": {
                "total_picks": self.total_picks,
                "sufficient_data": self.sufficient_data,
                "minimum_required": 30,
            },
            "confidence_calibration": self.confidence_calibration,
            "calibration_score": round(self.calibration_score, 3),
            "ev_realization": self.ev_by_bucket,
            "clv_consistency": {
                "avg_clv": round(self.avg_clv, 4),
                "clv_hit_correlation": round(self.clv_hit_correlation, 3) if self.clv_hit_correlation else None,
            },
            "hit_rate_by_stat_type": self.hit_rate_by_stat_type,
            "hit_rate_by_regime": self.hit_rate_by_regime,
            "portfolio_health": {
                "drawdown": round(self.portfolio_drawdown, 2),
                "volatility": round(self.portfolio_volatility, 3),
                "max_consecutive_losses": self.max_consecutive_losses,
            },
            "signal_analysis": {
                "top_miss_categories": [
                    {"category": cat, "count": count}
                    for cat, count in self.top_miss_categories
                ],
                "reliable_signals": self.reliable_signals,
                "harmful_signals": self.harmful_signals,
            },
            "threshold_stability": {
                "history": self.threshold_history,
                "stability_score": round(self.threshold_stability_score, 3),
            },
            "health_checks": {
                "instability_flags": self.instability_flags,
                "overfitting_warnings": self.overfitting_warnings,
            },
            "system_status": self.system_status,
        }

    def to_markdown(self) -> str:
        """Generate human-readable markdown report."""
        lines = [
            f"# Weekly Evaluation Report: {self.week_start} to {self.week_end}",
            "",
            f"**Report ID**: {self.report_id}  ",
            f"**Generated**: {self.generated_at}  ",
            f"**Total Picks**: {self.total_picks}  ",
            f"**System Status**: {self.system_status.upper()}",
            "",
        ]

        if not self.sufficient_data:
            lines.extend([
                "## ⚠️ INSUFFICIENT DATA",
                "",
                f"Minimum {30} picks required for reliable analysis. Current: {self.total_picks}",
                "",
            ])

        lines.extend([
            "## Confidence Calibration",
            f"**Overall Score**: {self.calibration_score:.3f}",
            "",
        ])

        for bucket, data in self.confidence_calibration.items():
            lines.append(
                f"- {bucket}: {data['hit_rate']:.1%} hit rate "
                f"(n={data['count']}, predicted ~{data['predicted']:.0%})"
            )

        lines.extend([
            "",
            "## EV Realization by Bucket",
            "",
        ])

        for bucket, data in self.ev_by_bucket.items():
            lines.append(
                f"- {bucket}: {data['hit_rate']:.1%} hit rate, {data['avg_ev']:.2%} avg EV "
                f"(n={data['count']})"
            )

        lines.extend([
            "",
            "## CLV Consistency",
            f"- Average CLV: {self.avg_clv:.2%}",
        ])

        if self.clv_hit_correlation:
            lines.append(f"- CLV-to-Hit Correlation: {self.clv_hit_correlation:.3f}")

        lines.extend([
            "",
            "## Hit Rate by Stat Type",
        ])

        for stat_type, hit_rate in self.hit_rate_by_stat_type.items():
            lines.append(f"- {stat_type}: {hit_rate:.1%}")

        lines.extend([
            "",
            "## Hit Rate by Market Regime",
        ])

        for regime, hit_rate in self.hit_rate_by_regime.items():
            lines.append(f"- {regime}: {hit_rate:.1%}")

        lines.extend([
            "",
            "## Portfolio Health",
            f"- Drawdown: {self.portfolio_drawdown:.2f} units",
            f"- Volatility: {self.portfolio_volatility:.3f}",
            f"- Max Consecutive Losses: {self.max_consecutive_losses}",
            "",
            "## Signal Analysis",
        ])

        if self.top_miss_categories:
            lines.append("### Top Miss Categories")
            for cat, count in self.top_miss_categories:
                lines.append(f"- {cat}: {count} misses")
            lines.append("")

        if self.reliable_signals:
            lines.append("### Most Reliable Signals")
            for sig in self.reliable_signals[:3]:
                lines.append(f"- {sig['name']}: {sig['hit_rate']:.1%} hit rate (n={sig['count']})")
            lines.append("")

        if self.harmful_signals:
            lines.append("### Most Harmful Signals")
            for sig in self.harmful_signals[:3]:
                lines.append(f"- {sig['name']}: {sig['hit_rate']:.1%} hit rate (n={sig['count']})")
            lines.append("")

        if self.threshold_history:
            lines.extend([
                "## Threshold Stability",
                f"**Stability Score**: {self.threshold_stability_score:.3f}",
                "",
                "### Threshold History",
            ])
            for entry in self.threshold_history:
                lines.append(f"- {entry['date']}: edge={entry['edge']:.3f}, conf={entry['confidence']:.3f}")
            lines.append("")

        if self.instability_flags:
            lines.extend([
                "## ⚠️ Instability Flags",
                "",
            ])
            for flag in self.instability_flags:
                lines.append(f"- {flag}")
            lines.append("")

        if self.overfitting_warnings:
            lines.extend([
                "## ⚠️ Overfitting Warnings",
                "",
            ])
            for warning in self.overfitting_warnings:
                lines.append(f"- {warning}")
            lines.append("")

        lines.extend([
            "---",
            "*Generated by CourtVision Weekly Evaluation System*",
        ])

        return "\n".join(lines)

    def save(self, json_path: str, md_path: str | None = None) -> None:
        """Save report to JSON and optional Markdown."""
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

        if md_path:
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(self.to_markdown())


class WeeklyReportGenerator:
    """Generate weekly evaluation report from shadow run artifacts."""

    def __init__(
        self,
        artifacts: list[ShadowRunArtifact],
        week_start: str,
        week_end: str,
    ) -> None:
        """Initialize with week's shadow run artifacts.

        Args:
            artifacts: List of artifacts from the week
            week_start: Start date (YYYY-MM-DD)
            week_end: End date (YYYY-MM-DD)
        """
        self.artifacts = artifacts
        self.week_start = week_start
        self.week_end = week_end

    def generate(self) -> WeeklyReport:
        """Generate weekly report."""
        # Build pick history from all artifacts
        builder = ReportBuilder(RollingWindow(window_size=100, min_samples=30))

        for artifact in self.artifacts:
            for entry in artifact.entries:
                if entry.recommended:  # Only include recommended plays
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
                        hit=entry.hit if entry.hit is not None else False,
                        clv=entry.clv if entry.clv is not None else 0.0,
                    )

        # Get evaluation report
        eval_report = builder.build_report()

        if eval_report is None:
            # Not enough data
            return self._generate_insufficient_data_report()

        # Build confidence calibration by bucket
        conf_calibration = self._build_confidence_calibration(eval_report)

        # Build EV by bucket
        ev_by_bucket = self._build_ev_by_bucket(eval_report)

        # Calculate CLV metrics
        avg_clv, clv_correlation = self._calculate_clv_metrics(builder.pick_history)

        # Build hit rate by stat type
        hit_by_stat = self._build_hit_rate_by_stat(builder.pick_history)

        # Build hit rate by regime (from artifact context)
        hit_by_regime = self._build_hit_rate_by_regime()

        # Calculate portfolio health
        drawdown, volatility, max_losses = self._calculate_portfolio_health(
            builder.pick_history
        )

        # Get signal analysis
        top_misses = eval_report.top_miss_categories
        reliable = eval_report.reliable_signals
        harmful = eval_report.harmful_signals

        # Threshold history and stability
        threshold_history = self._extract_threshold_history()
        stability_score = self._calculate_threshold_stability(threshold_history)

        # Detect instability
        instabilities = self._detect_instabilities(
            eval_report, threshold_history, builder.pick_history
        )

        # Detect overfitting
        overfitting = self._detect_overfitting(eval_report, builder.pick_history)

        # Classify system status
        status = self._classify_system_status(
            total_picks=eval_report.total_picks,
            calibration_score=eval_report.calibration_score,
            avg_clv=avg_clv,
            instabilities=instabilities,
            overfitting=overfitting,
        )

        return WeeklyReport(
            report_id=f"weekly_{self.week_start}_{self.week_end}",
            week_start=self.week_start,
            week_end=self.week_end,
            generated_at=datetime.now().isoformat(),
            total_picks=eval_report.total_picks,
            sufficient_data=eval_report.total_picks >= 30,
            confidence_calibration=conf_calibration,
            calibration_score=eval_report.calibration_score,
            ev_by_bucket=ev_by_bucket,
            avg_clv=avg_clv,
            clv_hit_correlation=clv_correlation,
            hit_rate_by_stat_type=hit_by_stat,
            hit_rate_by_regime=hit_by_regime,
            portfolio_drawdown=drawdown,
            portfolio_volatility=volatility,
            max_consecutive_losses=max_losses,
            top_miss_categories=top_misses,
            reliable_signals=reliable,
            harmful_signals=harmful,
            threshold_history=threshold_history,
            threshold_stability_score=stability_score,
            instability_flags=instabilities,
            overfitting_warnings=overfitting,
            system_status=status,
        )

    def _generate_insufficient_data_report(self) -> WeeklyReport:
        """Generate report when insufficient data."""
        return WeeklyReport(
            report_id=f"weekly_{self.week_start}_{self.week_end}",
            week_start=self.week_start,
            week_end=self.week_end,
            generated_at=datetime.now().isoformat(),
            total_picks=0,
            sufficient_data=False,
            confidence_calibration={},
            calibration_score=0.0,
            ev_by_bucket={},
            avg_clv=0.0,
            clv_hit_correlation=None,
            hit_rate_by_stat_type={},
            hit_rate_by_regime={},
            portfolio_drawdown=0.0,
            portfolio_volatility=0.0,
            max_consecutive_losses=0,
            top_miss_categories=[],
            reliable_signals=[],
            harmful_signals=[],
            threshold_history=[],
            threshold_stability_score=1.0,
            instability_flags=[],
            overfitting_warnings=[],
            system_status="insufficient_data",
        )

    def _build_confidence_calibration(
        self, report: Any
    ) -> dict[str, dict[str, Any]]:
        """Build confidence calibration by bucket."""
        result = {}
        for bucket in report.confidence_buckets:
            if bucket.count >= 5:  # Minimum samples
                result[bucket.bucket_name] = {
                    "count": bucket.count,
                    "hit_rate": bucket.hit_rate,
                    "predicted": bucket.avg_confidence,
                    "calibration_error": abs(bucket.hit_rate - bucket.avg_confidence),
                }
        return result

    def _build_ev_by_bucket(self, report: Any) -> dict[str, dict[str, Any]]:
        """Build EV realization by bucket."""
        result = {}
        for bucket in report.edge_buckets:
            if bucket.count >= 5:
                result[bucket.bucket_name] = {
                    "count": bucket.count,
                    "hit_rate": bucket.hit_rate,
                    "avg_ev": bucket.avg_ev,
                    "avg_clv": bucket.avg_clv,
                }
        return result

    def _calculate_clv_metrics(
        self, picks: list[dict]
    ) -> tuple[float, float | None]:
        """Calculate CLV metrics and correlation with hits."""
        if not picks:
            return 0.0, None

        clvs = [p.get("clv", 0) for p in picks]
        avg_clv = sum(clvs) / len(clvs) if clvs else 0.0

        # Calculate correlation with hits
        hits = [1 if p.get("hit") else 0 for p in picks]
        if len(clvs) == len(hits) and len(clvs) > 5:
            try:
                import numpy as np
                correlation = np.corrcoef(clvs, hits)[0, 1]
                return avg_clv, correlation
            except:
                pass

        return avg_clv, None

    def _build_hit_rate_by_stat(self, picks: list[dict]) -> dict[str, float]:
        """Build hit rate by stat type."""
        stats: dict[str, list[bool]] = {}
        for pick in picks:
            stat = pick.get("stat_type", "unknown")
            if stat not in stats:
                stats[stat] = []
            stats[stat].append(pick.get("hit", False))

        return {
            stat: sum(hits) / len(hits) if hits else 0.0
            for stat, hits in stats.items()
        }

    def _build_hit_rate_by_regime(self) -> dict[str, float]:
        """Build hit rate by market regime from artifacts."""
        regime_hits: dict[str, list[bool]] = {}

        for artifact in self.artifacts:
            for entry in artifact.entries:
                if entry.recommended and entry.hit is not None:
                    regime = entry.market_regime or "unknown"
                    if regime not in regime_hits:
                        regime_hits[regime] = []
                    regime_hits[regime].append(entry.hit)

        return {
            regime: sum(hits) / len(hits) if hits else 0.0
            for regime, hits in regime_hits.items()
        }

    def _calculate_portfolio_health(
        self, picks: list[dict]
    ) -> tuple[float, float, int]:
        """Calculate portfolio drawdown, volatility, max consecutive losses."""
        if not picks:
            return 0.0, 0.0, 0

        # Simplified P&L: +0.91 for hit, -1.0 for miss
        pnl = [0.91 if p.get("hit") else -1.0 for p in picks]

        # Cumulative for drawdown
        cumulative = []
        running = 0.0
        for p in pnl:
            running += p
            cumulative.append(running)

        # Drawdown
        peak = 0.0
        max_drawdown = 0.0
        for val in cumulative:
            if val > peak:
                peak = val
            drawdown = peak - val
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        # Volatility
        if len(pnl) > 1:
            mean = sum(pnl) / len(pnl)
            variance = sum((p - mean) ** 2 for p in pnl) / len(pnl)
            volatility = variance ** 0.5
        else:
            volatility = 0.0

        # Max consecutive losses
        max_losses = 0
        current_losses = 0
        for p in pnl:
            if p < 0:
                current_losses += 1
                max_losses = max(max_losses, current_losses)
            else:
                current_losses = 0

        return max_drawdown, volatility, max_losses

    def _extract_threshold_history(self) -> list[dict[str, Any]]:
        """Extract threshold changes from artifacts."""
        history = []
        seen_dates = set()

        for artifact in sorted(self.artifacts, key=lambda a: a.prediction_date):
            date = artifact.prediction_date
            if date in seen_dates:
                continue
            seen_dates.add(date)

            # Get thresholds from first entry
            for entry in artifact.entries:
                if entry.thresholds_used:
                    history.append({
                        "date": date,
                        "edge": entry.thresholds_used.get("edge", 0.05),
                        "confidence": entry.thresholds_used.get("confidence", 0.65),
                        "ev": entry.thresholds_used.get("ev", 0.03),
                    })
                    break

        return history

    def _calculate_threshold_stability(
        self, history: list[dict]
    ) -> float:
        """Calculate threshold stability score (0-1)."""
        if len(history) < 2:
            return 1.0  # Stable if no changes

        # Calculate max drift in each threshold
        edges = [h["edge"] for h in history]
        confs = [h["confidence"] for h in history]

        edge_drift = max(edges) - min(edges)
        conf_drift = max(confs) - min(confs)

        # Score: 1.0 = no drift, 0.0 = drift > 0.05
        max_drift = max(edge_drift, conf_drift)
        stability = max(0.0, 1.0 - (max_drift / 0.05))

        return round(stability, 3)

    def _detect_instabilities(
        self,
        report: Any,
        threshold_history: list[dict],
        picks: list[dict],
    ) -> list[str]:
        """Detect signs of system instability."""
        flags = []

        # Calibration score too low
        if report.calibration_score < 0.4:
            flags.append(f"Low calibration score: {report.calibration_score:.3f}")

        # Excessive threshold drift
        if len(threshold_history) >= 2:
            edges = [h["edge"] for h in threshold_history]
            if max(edges) - min(edges) > 0.03:
                flags.append("Excessive threshold drift detected")

        # Sudden drop in hit rate
        if len(picks) >= 20:
            first_half = picks[:len(picks)//2]
            second_half = picks[len(picks)//2:]
            first_hit_rate = sum(p.get("hit", False) for p in first_half) / len(first_half)
            second_hit_rate = sum(p.get("hit", False) for p in second_half) / len(second_half)
            if first_hit_rate - second_hit_rate > 0.15:
                flags.append(f"Hit rate dropped from {first_hit_rate:.1%} to {second_hit_rate:.1%}")

        return flags

    def _detect_overfitting(
        self, report: Any, picks: list[dict]
    ) -> list[str]:
        """Detect signs of overfitting."""
        warnings = []

        # Perfect calibration is suspicious with small samples
        if report.calibration_score > 0.95 and report.total_picks < 100:
            warnings.append("Suspiciously perfect calibration with small sample")

        # Extreme bucket performance differences
        for bucket in report.confidence_buckets:
            if bucket.count >= 10:
                if bucket.hit_rate > 0.9 or bucket.hit_rate < 0.3:
                    warnings.append(f"Extreme hit rate in {bucket.bucket_name}: {bucket.hit_rate:.1%}")

        # CLV inconsistency
        positive_clv_hits = []
        negative_clv_hits = []
        for pick in picks:
            clv = pick.get("clv", 0)
            hit = pick.get("hit", False)
            if clv > 0.01:
                positive_clv_hits.append(hit)
            elif clv < -0.01:
                negative_clv_hits.append(hit)

        if positive_clv_hits and negative_clv_hits:
            pos_rate = sum(positive_clv_hits) / len(positive_clv_hits)
            neg_rate = sum(negative_clv_hits) / len(negative_clv_hits)
            if neg_rate > pos_rate:
                warnings.append("Negative CLV plays outperforming positive CLV")

        return warnings

    def _classify_system_status(
        self,
        total_picks: int,
        calibration_score: float,
        avg_clv: float,
        instabilities: list[str],
        overfitting: list[str],
    ) -> str:
        """Classify overall system status."""
        if total_picks < 30:
            return "insufficient_data"

        if instabilities:
            return "unstable"

        if overfitting:
            return "early_signal"  # Possible overfitting, needs more data

        if calibration_score > 0.7 and avg_clv > 0.01:
            return "validated"

        if calibration_score > 0.5 and avg_clv > 0:
            return "promising"

        return "early_signal"


def generate_weekly_report(
    artifacts: list[ShadowRunArtifact],
    week_start: str,
    week_end: str,
    output_dir: str = "./weekly_reports",
) -> WeeklyReport:
    """Convenience function to generate and save weekly report.

    Args:
        artifacts: List of shadow run artifacts from the week
        week_start: Start date (YYYY-MM-DD)
        week_end: End date (YYYY-MM-DD)
        output_dir: Directory for output files

    Returns:
        Generated WeeklyReport
    """
    import os

    generator = WeeklyReportGenerator(artifacts, week_start, week_end)
    report = generator.generate()

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Generate file paths
    json_path = os.path.join(output_dir, f"weekly_report_{week_start}_{week_end}.json")
    md_path = os.path.join(output_dir, f"weekly_report_{week_start}_{week_end}.md")

    # Save
    report.save(json_path, md_path)

    return report
