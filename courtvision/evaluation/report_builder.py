"""Evaluation report builder for rolling window analysis.

VALIDATE + CALIBRATE mode - Measurement and validation only.

Task A: Build evaluation dashboard/report layer
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class RollingWindow:
    """Configuration for rolling analysis window."""

    window_size: int = 30  # Number of picks
    min_samples: int = 10  # Minimum samples for analysis

    def has_enough_samples(self, n: int) -> bool:
        """Check if sample count meets minimum."""
        return n >= self.min_samples


@dataclass
class BucketMetrics:
    """Metrics for a single bucket (confidence, edge, etc.)."""

    bucket_name: str
    count: int
    hits: int
    hit_rate: float
    avg_ev: float
    avg_clv: float
    profit: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "bucket": self.bucket_name,
            "count": self.count,
            "hits": self.hits,
            "hit_rate": round(self.hit_rate, 3) if self.count > 0 else None,
            "avg_ev": round(self.avg_ev, 3) if self.count > 0 else None,
            "avg_clv": round(self.avg_clv, 3) if self.count > 0 else None,
            "profit": round(self.profit, 2),
        }


@dataclass
class SignalReliability:
    """Reliability metrics for a signal."""

    signal_name: str
    picks_count: int
    hit_rate: float
    avg_contribution: float  # How much signal contributes when correct
    harm_when_wrong: float  # How much signal hurts when wrong

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "signal": self.signal_name,
            "picks": self.picks_count,
            "hit_rate": round(self.hit_rate, 3) if self.picks_count > 0 else None,
            "avg_contribution": round(self.avg_contribution, 3),
            "harm_when_wrong": round(self.harm_when_wrong, 3),
        }


@dataclass
class EvaluationReport:
    """Complete evaluation report for a rolling window."""

    window_start: str
    window_end: str
    total_picks: int

    # By confidence bucket
    confidence_buckets: list[BucketMetrics] = field(default_factory=list)

    # By edge bucket
    edge_buckets: list[BucketMetrics] = field(default_factory=list)

    # By stat type
    stat_type_metrics: dict[str, BucketMetrics] = field(default_factory=dict)

    # By market regime
    regime_metrics: dict[str, BucketMetrics] = field(default_factory=dict)

    # Portfolio metrics
    portfolio_drawdown: float = 0.0
    portfolio_volatility: float = 0.0

    # Quality metrics
    top_rejection_reasons: list[tuple[str, int]] = field(default_factory=list)
    top_miss_categories: list[tuple[str, int]] = field(default_factory=list)

    # Signal analysis
    reliable_signals: list[SignalReliability] = field(default_factory=list)
    harmful_signals: list[SignalReliability] = field(default_factory=list)

    # Calibration score (how well predicted confidence matches hit rate)
    calibration_score: float = 0.0  # 0-1, higher = better calibrated

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "window": {
                "start": self.window_start,
                "end": self.window_end,
                "total_picks": self.total_picks,
            },
            "by_confidence": [b.to_dict() for b in self.confidence_buckets],
            "by_edge": [b.to_dict() for b in self.edge_buckets],
            "by_stat_type": {
                k: v.to_dict() for k, v in self.stat_type_metrics.items()
            },
            "by_market_regime": {
                k: v.to_dict() for k, v in self.regime_metrics.items()
            },
            "portfolio": {
                "drawdown": round(self.portfolio_drawdown, 3),
                "volatility": round(self.portfolio_volatility, 3),
            },
            "quality": {
                "top_rejections": self.top_rejection_reasons[:5],
                "top_misses": self.top_miss_categories[:5],
            },
            "signals": {
                "reliable": [s.to_dict() for s in self.reliable_signals[:5]],
                "harmful": [s.to_dict() for s in self.harmful_signals[:5]],
            },
            "calibration": {
                "score": round(self.calibration_score, 3),
                "interpretation": self._interpret_calibration(),
            },
        }

    def _interpret_calibration(self) -> str:
        """Interpret calibration score."""
        if self.calibration_score > 0.8:
            return "well_calibrated"
        elif self.calibration_score > 0.6:
            return "moderately_calibrated"
        elif self.calibration_score > 0.4:
            return "poorly_calibrated"
        else:
            return "miscalibrated"


class ReportBuilder:
    """Build evaluation reports from pick history.

    Rolling window analysis of:
    - Confidence calibration
    - Edge realization
    - CLV consistency
    - Signal reliability
    - Market regime performance
    """

    # Confidence bucket boundaries
    CONFIDENCE_BUCKETS = [
        (0.50, 0.60, "50-60%"),
        (0.60, 0.70, "60-70%"),
        (0.70, 0.80, "70-80%"),
        (0.80, 0.90, "80-90%"),
        (0.90, 1.00, "90-100%"),
    ]

    # Edge bucket boundaries
    EDGE_BUCKETS = [
        (-1.0, 0.0, "negative"),
        (0.0, 0.05, "0-5%"),
        (0.05, 0.10, "5-10%"),
        (0.10, 0.20, "10-20%"),
        (0.20, 1.0, "20%+"),
    ]

    def __init__(self, window: RollingWindow | None = None) -> None:
        """Initialize report builder.

        Args:
            window: Rolling window configuration
        """
        self.window = window or RollingWindow(min_samples=1)
        self.pick_history: list[dict] = []

    def add_pick(
        self,
        pick_id: str,
        prediction_date: str,
        player_name: str,
        stat_type: str,
        over_under: str,
        line_value: float,
        confidence: float,
        edge: float,
        ev: float,
        hit: bool,
        clv: float = 0.0,
        rejection_reason: str = "",
        miss_category: str = "",
        market_regime: str = "neutral",
        signals: dict[str, float] | None = None,
    ) -> None:
        """Add a pick to history.

        Args:
            pick_id: Unique pick identifier
            prediction_date: Date of prediction
            player_name: Player name
            stat_type: Stat type
            over_under: "over" or "under"
            line_value: Betting line
            confidence: Predicted confidence (0-1)
            edge: Predicted edge
            ev: Expected value
            hit: Whether pick hit
            clv: Closing line value
            rejection_reason: If rejected, why
            miss_category: If missed, classification
            market_regime: Market condition
            signals: Dict of signal contributions
        """
        pick = {
            "pick_id": pick_id,
            "prediction_date": prediction_date,
            "player_name": player_name,
            "stat_type": stat_type,
            "over_under": over_under,
            "line_value": line_value,
            "confidence": confidence,
            "edge": edge,
            "ev": ev,
            "hit": hit,
            "clv": clv,
            "rejection_reason": rejection_reason,
            "miss_category": miss_category,
            "market_regime": market_regime,
            "signals": signals or {},
        }
        self.pick_history.append(pick)

    def build_report(
        self,
        window_end: int | None = None,
    ) -> EvaluationReport | None:
        """Build evaluation report for rolling window.

        Args:
            window_end: End index for window (default: latest)

        Returns:
            EvaluationReport or None if insufficient data
        """
        if not self.pick_history:
            return None

        if window_end is None:
            window_end = len(self.pick_history)

        window_start = max(0, window_end - self.window.window_size)
        window_picks = self.pick_history[window_start:window_end]

        if not self.window.has_enough_samples(len(window_picks)):
            return None

        # Get date range
        dates = [p["prediction_date"] for p in window_picks]
        start_date = min(dates)
        end_date = max(dates)

        report = EvaluationReport(
            window_start=start_date,
            window_end=end_date,
            total_picks=len(window_picks),
        )

        # Build bucket metrics
        report.confidence_buckets = self._build_confidence_buckets(window_picks)
        report.edge_buckets = self._build_edge_buckets(window_picks)
        report.stat_type_metrics = self._build_stat_type_metrics(window_picks)
        report.regime_metrics = self._build_regime_metrics(window_picks)

        # Portfolio metrics
        report.portfolio_drawdown = self._calculate_drawdown(window_picks)
        report.portfolio_volatility = self._calculate_volatility(window_picks)

        # Quality metrics
        report.top_rejection_reasons = self._count_rejections(window_picks)
        report.top_miss_categories = self._count_misses(window_picks)

        # Signal analysis
        reliable, harmful = self._analyze_signals(window_picks)
        report.reliable_signals = reliable
        report.harmful_signals = harmful

        # Calibration
        report.calibration_score = self._calculate_calibration(window_picks)

        return report

    def build_all_reports(self) -> list[EvaluationReport]:
        """Build reports for all rolling windows."""
        reports = []

        for end_idx in range(
            self.window.min_samples,
            len(self.pick_history) + 1,
            max(1, self.window.window_size // 3),  # Overlap windows
        ):
            report = self.build_report(window_end=end_idx)
            if report:
                reports.append(report)

        return reports

    def _build_confidence_buckets(
        self,
        picks: list[dict],
    ) -> list[BucketMetrics]:
        """Build metrics by confidence bucket."""
        buckets = []

        for low, high, name in self.CONFIDENCE_BUCKETS:
            bucket_picks = [
                p for p in picks
                if low <= p["confidence"] < high
            ]

            metrics = self._compute_bucket_metrics(name, bucket_picks)
            buckets.append(metrics)

        return buckets

    def _build_edge_buckets(
        self,
        picks: list[dict],
    ) -> list[BucketMetrics]:
        """Build metrics by edge bucket."""
        buckets = []

        for low, high, name in self.EDGE_BUCKETS:
            bucket_picks = [
                p for p in picks
                if low <= p["edge"] < high
            ]

            metrics = self._compute_bucket_metrics(name, bucket_picks)
            buckets.append(metrics)

        return buckets

    def _build_stat_type_metrics(
        self,
        picks: list[dict],
    ) -> dict[str, BucketMetrics]:
        """Build metrics by stat type."""
        by_stat: dict[str, list[dict]] = {}
        for p in picks:
            stat = p["stat_type"]
            if stat not in by_stat:
                by_stat[stat] = []
            by_stat[stat].append(p)

        return {
            stat: self._compute_bucket_metrics(stat, stat_picks)
            for stat, stat_picks in by_stat.items()
            if len(stat_picks) >= 5  # Min samples per stat
        }

    def _build_regime_metrics(
        self,
        picks: list[dict],
    ) -> dict[str, BucketMetrics]:
        """Build metrics by market regime."""
        by_regime: dict[str, list[dict]] = {}
        for p in picks:
            regime = p.get("market_regime", "neutral")
            if regime not in by_regime:
                by_regime[regime] = []
            by_regime[regime].append(p)

        return {
            regime: self._compute_bucket_metrics(regime, regime_picks)
            for regime, regime_picks in by_regime.items()
            if len(regime_picks) >= 5
        }

    def _compute_bucket_metrics(
        self,
        name: str,
        picks: list[dict],
    ) -> BucketMetrics:
        """Compute metrics for a bucket of picks."""
        count = len(picks)
        if count == 0:
            return BucketMetrics(
                bucket_name=name,
                count=0,
                hits=0,
                hit_rate=0.0,
                avg_ev=0.0,
                avg_clv=0.0,
                profit=0.0,
            )

        hits = sum(1 for p in picks if p["hit"])
        hit_rate = hits / count

        avg_ev = sum(p["ev"] for p in picks) / count
        avg_clv = sum(p["clv"] for p in picks) / count

        # Simplified profit calculation
        profit = sum(0.91 if p["hit"] else -1.0 for p in picks)

        return BucketMetrics(
            bucket_name=name,
            count=count,
            hits=hits,
            hit_rate=hit_rate,
            avg_ev=avg_ev,
            avg_clv=avg_clv,
            profit=profit,
        )

    def _calculate_drawdown(self, picks: list[dict]) -> float:
        """Calculate maximum drawdown from picks."""
        if not picks:
            return 0.0

        # Calculate running P&L
        pnl = [0.91 if p["hit"] else -1.0 for p in picks]
        cumulative = np.cumsum(pnl)

        # Calculate drawdown
        peak = cumulative[0]
        max_dd = 0.0

        for val in cumulative:
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd

        return max_dd

    def _calculate_volatility(self, picks: list[dict]) -> float:
        """Calculate portfolio volatility (std dev of P&L)."""
        if len(picks) < 2:
            return 0.0

        pnl = [0.91 if p["hit"] else -1.0 for p in picks]
        return float(np.std(pnl))

    def _count_rejections(
        self,
        picks: list[dict],
    ) -> list[tuple[str, int]]:
        """Count rejection reasons."""
        reasons: dict[str, int] = {}
        for p in picks:
            reason = p.get("rejection_reason", "")
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1

        return sorted(reasons.items(), key=lambda x: x[1], reverse=True)

    def _count_misses(
        self,
        picks: list[dict],
    ) -> list[tuple[str, int]]:
        """Count miss categories."""
        categories: dict[str, int] = {}
        for p in picks:
            if not p["hit"]:
                cat = p.get("miss_category", "unknown")
                if cat:
                    categories[cat] = categories.get(cat, 0) + 1

        return sorted(categories.items(), key=lambda x: x[1], reverse=True)

    def _analyze_signals(
        self,
        picks: list[dict],
    ) -> tuple[list[SignalReliability], list[SignalReliability]]:
        """Analyze signal reliability."""
        # Aggregate signal performance
        signal_stats: dict[str, dict] = {}

        for p in picks:
            hit = p["hit"]
            for signal, contribution in p.get("signals", {}).items():
                if signal not in signal_stats:
                    signal_stats[signal] = {
                        "picks": 0,
                        "hits": 0,
                        "contributions": [],
                        "harms": [],
                    }

                signal_stats[signal]["picks"] += 1
                if hit:
                    signal_stats[signal]["hits"] += 1
                    signal_stats[signal]["contributions"].append(contribution)
                else:
                    signal_stats[signal]["harms"].append(contribution)

        reliable = []
        harmful = []

        for signal, stats in signal_stats.items():
            if stats["picks"] < 5:  # Min samples
                continue

            hit_rate = stats["hits"] / stats["picks"]
            avg_contribution = (
                sum(stats["contributions"]) / len(stats["contributions"])
                if stats["contributions"] else 0
            )
            harm_when_wrong = (
                sum(stats["harms"]) / len(stats["harms"])
                if stats["harms"] else 0
            )

            reliability = SignalReliability(
                signal_name=signal,
                picks_count=stats["picks"],
                hit_rate=hit_rate,
                avg_contribution=avg_contribution,
                harm_when_wrong=harm_when_wrong,
            )

            if hit_rate > 0.55 and avg_contribution > 0:
                reliable.append(reliability)
            elif hit_rate < 0.45 or harm_when_wrong > 0.1:
                harmful.append(reliability)

        # Sort by hit rate
        reliable.sort(key=lambda s: s.hit_rate, reverse=True)
        harmful.sort(key=lambda s: s.harm_when_wrong, reverse=True)

        return reliable, harmful

    def _calculate_calibration(self, picks: list[dict]) -> float:
        """Calculate calibration score (predicted vs realized confidence).

        Returns score 0-1 where 1 = perfect calibration.
        """
        if len(picks) < 10:
            return 0.0

        # Group by predicted confidence buckets
        bucket_errors = []

        for low, high, _ in self.CONFIDENCE_BUCKETS:
            bucket_picks = [
                p for p in picks
                if low <= p["confidence"] < high
            ]

            if len(bucket_picks) < 3:
                continue

            predicted = (low + high) / 2  # Midpoint
            realized = sum(1 for p in bucket_picks if p["hit"]) / len(bucket_picks)
            error = abs(predicted - realized)
            bucket_errors.append(error)

        if not bucket_errors:
            return 0.0

        # Average error (0 = perfect, 0.5 = random, 1 = inverted)
        avg_error = sum(bucket_errors) / len(bucket_errors)

        # Convert to score (1 - 2*error, clamped to 0-1)
        score = max(0.0, 1.0 - 2.0 * avg_error)

        return score
