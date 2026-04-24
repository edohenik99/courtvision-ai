"""Grading feedback analyzer for prediction vs actual comparison.

Analyzes graded picks to extract insights for model improvement:
- Player-specific accuracy patterns
- Market type performance
- Confidence calibration issues
- Edge effectiveness
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PlayerAccuracyProfile:
    """Accuracy profile for a specific player."""

    player_id: int
    player_name: str
    total_picks: int = 0
    hits: int = 0
    misses: int = 0
    hit_rate: float = 0.0
    by_stat_type: dict[str, dict[str, Any]] = None  # type: ignore
    by_confidence: dict[str, dict[str, Any]] = None  # type: ignore

    def __post_init__(self):
        if self.by_stat_type is None:
            self.by_stat_type = {}
        if self.by_confidence is None:
            self.by_confidence = {}


@dataclass
class MarketTypeAnalysis:
    """Analysis for a specific market type."""

    market_type: str
    total_picks: int = 0
    hits: int = 0
    misses: int = 0
    hit_rate: float = 0.0
    avg_edge: float = 0.0
    avg_confidence: float = 0.0
    confidence_calibration: dict[str, dict[str, Any]] = None  # type: ignore

    def __post_init__(self):
        if self.confidence_calibration is None:
            self.confidence_calibration = {}


@dataclass
class ConfidenceCalibrationIssue:
    """Identified confidence calibration issue."""

    confidence_bucket: str
    expected_hit_rate: float
    actual_hit_rate: float
    deviation: float
    sample_size: int
    recommendation: str


class GradingFeedbackAnalyzer:
    """Analyze grading results to provide actionable feedback.

    Compares predictions vs actual results to identify:
    - Players the model over/under-values
    - Market types that are more/less predictable
    - Confidence calibration issues
    - Edge effectiveness patterns
    """

    # Expected hit rates by confidence bucket for calibration check
    EXPECTED_HIT_RATES = {
        "low": 0.55,    # 55% for low confidence
        "mid": 0.62,    # 62% for mid confidence
        "high": 0.70,   # 70% for high confidence
        "elite": 0.78,  # 78% for elite confidence
    }

    def __init__(self, performance_store: Any) -> None:
        """Initialize with a performance store instance."""
        self.store = performance_store

    def analyze_player_accuracy(
        self,
        player_id: int | None = None,
        window_days: int = 30,
        min_sample_size: int = 5,
    ) -> dict[int, PlayerAccuracyProfile] | PlayerAccuracyProfile | None:
        """Analyze accuracy for specific player(s).

        Args:
            player_id: Specific player to analyze, or None for all players
            window_days: Rolling window size
            min_sample_size: Minimum picks to include in analysis

        Returns:
            Player accuracy profile(s) or None if insufficient data
        """
        window = self.store.get_window_metrics(window_days)

        if player_id is not None:
            # Single player analysis
            player_data = window.by_player.get(player_id)
            if not player_data or player_data["total"] < min_sample_size:
                return None
            return self._build_player_profile(player_id, player_data, window)

        # All players analysis
        profiles = {}
        for pid, pdata in window.by_player.items():
            if pdata["total"] >= min_sample_size:
                profiles[pid] = self._build_player_profile(pid, pdata, window)

        return profiles

    def analyze_market_types(self, window_days: int = 30) -> dict[str, MarketTypeAnalysis]:
        """Analyze performance by market type."""
        window = self.store.get_window_metrics(window_days)

        analyses = {}
        for stat_type, data in window.by_stat_type.items():
            analysis = MarketTypeAnalysis(
                market_type=stat_type,
                total_picks=data["total"],
                hits=data["hits"],
                misses=data["misses"],
                hit_rate=data["hit_rate"],
            )

            # Add confidence calibration for this market type
            analysis.confidence_calibration = self._analyze_market_confidence(
                stat_type, window_days
            )

            analyses[stat_type] = analysis

        return analyses

    def identify_calibration_issues(
        self,
        window_days: int = 30,
        min_sample_size: int = 10,
        threshold: float = 0.10,
    ) -> list[ConfidenceCalibrationIssue]:
        """Identify confidence buckets that are miscalibrated.

        Args:
            window_days: Rolling window size
            min_sample_size: Minimum samples to flag an issue
            threshold: Deviation threshold to flag (e.g., 0.10 = 10%)

        Returns:
            List of identified calibration issues
        """
        calibration = self.store.get_confidence_calibration(window_days)
        issues = []

        for bucket, data in calibration.items():
            if data["total"] < min_sample_size:
                continue

            expected = self.EXPECTED_HIT_RATES.get(bucket, 0.60)
            actual = data["hit_rate"]
            deviation = actual - expected

            if abs(deviation) > threshold:
                if deviation > 0:
                    recommendation = f"Model is conservative in {bucket} bucket. Consider raising confidence threshold."
                else:
                    recommendation = f"Model is overconfident in {bucket} bucket. Consider lowering confidence threshold or adding penalties."

                issues.append(ConfidenceCalibrationIssue(
                    confidence_bucket=bucket,
                    expected_hit_rate=expected,
                    actual_hit_rate=actual,
                    deviation=deviation,
                    sample_size=data["total"],
                    recommendation=recommendation,
                ))

        return issues

    def get_edge_effectiveness_report(self, window_days: int = 30) -> dict[str, Any]:
        """Generate report on edge effectiveness by size bucket."""
        edge_reliability = self.store.get_edge_reliability(window_days)

        # Find most/least reliable edge ranges
        sorted_buckets = sorted(
            edge_reliability.items(),
            key=lambda x: x[1]["hit_rate"],
            reverse=True,
        )

        return {
            "edge_buckets": edge_reliability,
            "most_reliable": sorted_buckets[0] if sorted_buckets else None,
            "least_reliable": sorted_buckets[-1] if sorted_buckets else None,
            "recommended_edge_threshold": self._compute_recommended_edge(edge_reliability),
        }

    def generate_summary_report(self, window_days: int = 30) -> dict[str, Any]:
        """Generate comprehensive feedback summary."""
        window = self.store.get_window_metrics(window_days)
        calibration = self.store.get_confidence_calibration(window_days)
        edge_report = self.get_edge_effectiveness_report(window_days)
        issues = self.identify_calibration_issues(window_days)

        # Top performing players
        player_profiles = self.analyze_player_accuracy(window_days=window_days, min_sample_size=5)
        top_players = sorted(
            player_profiles.values() if isinstance(player_profiles, dict) else [],
            key=lambda p: p.hit_rate,
            reverse=True,
        )[:5] if isinstance(player_profiles, dict) else []

        # Worst performing players
        worst_players = sorted(
            player_profiles.values() if isinstance(player_profiles, dict) else [],
            key=lambda p: p.hit_rate,
        )[:5] if isinstance(player_profiles, dict) else []

        return {
            "window_days": window_days,
            "total_picks": window.total_picks,
            "overall_hit_rate": window.hit_rate,
            "avg_edge": window.avg_edge,
            "avg_confidence": window.avg_confidence,
            "confidence_calibration": calibration,
            "edge_effectiveness": edge_report,
            "calibration_issues": [
                {
                    "bucket": i.confidence_bucket,
                    "expected": i.expected_hit_rate,
                    "actual": i.actual_hit_rate,
                    "deviation": i.deviation,
                    "recommendation": i.recommendation,
                }
                for i in issues
            ],
            "top_performing_players": [
                {"name": p.player_name, "hit_rate": p.hit_rate, "picks": p.total_picks}
                for p in top_players
            ],
            "underperforming_players": [
                {"name": p.player_name, "hit_rate": p.hit_rate, "picks": p.total_picks}
                for p in worst_players
            ],
            "market_type_performance": self.analyze_market_types(window_days),
        }

    def _build_player_profile(
        self,
        player_id: int,
        player_data: dict[str, Any],
        window: Any,
    ) -> PlayerAccuracyProfile:
        """Build detailed player accuracy profile."""
        profile = PlayerAccuracyProfile(
            player_id=player_id,
            player_name=player_data.get("player_name", ""),
            total_picks=player_data["total"],
            hits=player_data["hits"],
            misses=player_data["misses"],
            hit_rate=player_data["hit_rate"],
        )

        # Add stat type breakdown (would need more detailed aggregation)
        # This is a placeholder - in production would query by player_id

        return profile

    def _analyze_market_confidence(self, stat_type: str, window_days: int) -> dict[str, dict[str, Any]]:
        """Analyze confidence calibration for a specific market type."""
        # Get all records and filter by stat type
        window = self.store.get_window_metrics(window_days)

        # Filter records for this stat type
        stat_records = [
            r for r in self.store.records
            if r.stat_type == stat_type and r.prediction_date in [
                d.prediction_date for d in self.store.records[-window.total_picks:]
            ]
        ]

        # Aggregate by confidence bucket
        buckets: dict[str, dict[str, Any]] = {
            b: {"total": 0, "hits": 0, "misses": 0} for b in self.store.CONFIDENCE_BUCKETS
        }

        for r in stat_records:
            if r.confidence_bucket in buckets:
                buckets[r.confidence_bucket]["total"] += 1
                if r.result == "hit":
                    buckets[r.confidence_bucket]["hits"] += 1
                elif r.result == "miss":
                    buckets[r.confidence_bucket]["misses"] += 1

        # Compute hit rates
        for b in buckets:
            total = buckets[b]["hits"] + buckets[b]["misses"]
            buckets[b]["hit_rate"] = buckets[b]["hits"] / total if total > 0 else 0.0

        return buckets

    def _compute_recommended_edge(self, edge_reliability: dict[str, dict[str, Any]]) -> float:
        """Compute recommended minimum edge based on reliability data."""
        # Find the edge bucket with the best hit rate that has sufficient sample size
        best_edge = 0.0
        best_hit_rate = 0.0

        for bucket, data in edge_reliability.items():
            if data["total"] < 10:  # Need sufficient samples
                continue

            if data["hit_rate"] > best_hit_rate:
                best_hit_rate = data["hit_rate"]
                # Parse bucket label to get lower bound
                if bucket == "10.0+":
                    best_edge = 10.0
                else:
                    best_edge = float(bucket.split("-")[0])

        return best_edge
