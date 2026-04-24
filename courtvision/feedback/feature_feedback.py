"""Feature-level performance feedback system.

Tracks performance impact of individual features:
- Recent form indicators
- Minutes projection accuracy
- Injury boost effectiveness
- Market quality detection

Phase 8: Causal Attribution and Mistake Analysis
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FeatureImpact:
    """Impact record for a single feature."""

    feature_name: str
    pick_id: str
    feature_value: float
    result: str  # "hit" or "miss"
    confidence: float
    timestamp: str


@dataclass
class FeaturePerformance:
    """Performance metrics for a feature."""

    feature_name: str
    total_applications: int = 0
    hits: int = 0
    misses: int = 0
    hit_rate: float = 0.0
    avg_feature_value_on_hit: float = 0.0
    avg_feature_value_on_miss: float = 0.0
    correlation_with_success: float = 0.0  # -1 to +1

    # Value buckets for analysis
    value_distribution_hits: dict[str, int] = field(default_factory=dict)
    value_distribution_misses: dict[str, int] = field(default_factory=dict)

    def compute_hit_rate(self) -> float:
        """Compute hit rate for this feature."""
        decisions = self.hits + self.misses
        return self.hits / decisions if decisions > 0 else 0.0


class FeaturePerformanceTracker:
    """Track performance of individual features.

    Monitors how specific features (recent form, minutes projection,
    injury boosts, market quality) correlate with success/failure.
    """

    # Features to track
    FEATURES = [
        "recent_form_ratio",
        "minutes_projection_confidence",
        "injury_boost_magnitude",
        "market_quality_score",
        "projection_edge_size",
        "confidence_base",
        "penalty_total_impact",
        "volatility_penalty",
        "usage_rate",
        "rest_days",
        "matchup_favorability",
    ]

    # Bucket thresholds for value distributions
    BUCKET_THRESHOLDS = {
        "recent_form_ratio": [0.8, 0.9, 1.0, 1.1, 1.2],
        "minutes_projection_confidence": [0.5, 0.6, 0.7, 0.8, 0.9],
        "injury_boost_magnitude": [0.0, 0.05, 0.10, 0.15, 0.20],
        "market_quality_score": [-0.2, -0.1, 0.0, 0.1, 0.2],
        "projection_edge_size": [0.02, 0.05, 0.10, 0.15, 0.20],
    }

    def __init__(self) -> None:
        """Initialize feature performance tracker."""
        self._feature_performance: dict[str, FeaturePerformance] = {
            feature: FeaturePerformance(feature_name=feature)
            for feature in self.FEATURES
        }
        self._impact_history: list[FeatureImpact] = []

    def record_feature_impact(
        self,
        feature_name: str,
        pick_id: str,
        feature_value: float,
        result: str,
        confidence: float,
        timestamp: str,
    ) -> None:
        """Record feature value and outcome for a pick.

        Args:
            feature_name: Name of the feature
            pick_id: Unique pick identifier
            feature_value: Value of the feature at prediction time
            result: "hit" or "miss"
            confidence: Pick confidence
            timestamp: Prediction date
        """
        if feature_name not in self._feature_performance:
            return  # Unknown feature

        perf = self._feature_performance[feature_name]
        perf.total_applications += 1

        if result == "hit":
            perf.hits += 1
            perf.avg_feature_value_on_hit = (
                (perf.avg_feature_value_on_hit * (perf.hits - 1) + feature_value)
                / perf.hits
            )
        else:
            perf.misses += 1
            perf.avg_feature_value_on_miss = (
                (perf.avg_feature_value_on_miss * (perf.misses - 1) + feature_value)
                / perf.misses
            )

        # Update value distribution
        bucket = self._get_bucket(feature_name, feature_value)
        if result == "hit":
            perf.value_distribution_hits[bucket] = perf.value_distribution_hits.get(bucket, 0) + 1
        else:
            perf.value_distribution_misses[bucket] = perf.value_distribution_misses.get(bucket, 0) + 1

        # Record impact
        impact = FeatureImpact(
            feature_name=feature_name,
            pick_id=pick_id,
            feature_value=feature_value,
            result=result,
            confidence=confidence,
            timestamp=timestamp,
        )
        self._impact_history.append(impact)

    def _get_bucket(self, feature_name: str, value: float) -> str:
        """Get value bucket for a feature."""
        thresholds = self.BUCKET_THRESHOLDS.get(feature_name, [0.0, 0.5, 1.0])

        for i, threshold in enumerate(thresholds):
            if value < threshold:
                return f"bucket_{i}"

        return f"bucket_{len(thresholds)}"

    def get_feature_performance(self, feature_name: str) -> FeaturePerformance | None:
        """Get performance metrics for a specific feature."""
        return self._feature_performance.get(feature_name)

    def get_all_performances(self) -> dict[str, FeaturePerformance]:
        """Get performance metrics for all features."""
        return self._feature_performance.copy()

    def analyze_feature_correlation(
        self,
        feature_name: str,
        min_samples: int = 20,
    ) -> dict[str, Any]:
        """Analyze correlation between feature value and success.

        Args:
            feature_name: Feature to analyze
            min_samples: Minimum samples required

        Returns:
            Correlation analysis
        """
        perf = self._feature_performance.get(feature_name)
        if not perf or perf.total_applications < min_samples:
            return {
                "feature": feature_name,
                "error": f"Insufficient samples: {perf.total_applications if perf else 0} < {min_samples}",
            }

        # Compute correlation
        hit_avg = perf.avg_feature_value_on_hit
        miss_avg = perf.avg_feature_value_on_miss

        # Simple correlation: positive = higher values correlate with hits
        correlation = 0.0
        if hit_avg != 0 or miss_avg != 0:
            correlation = (hit_avg - miss_avg) / max(abs(hit_avg), abs(miss_avg), 0.001)

        return {
            "feature": feature_name,
            "total_samples": perf.total_applications,
            "hit_rate": perf.compute_hit_rate(),
            "avg_value_on_hit": hit_avg,
            "avg_value_on_miss": miss_avg,
            "correlation": correlation,
            "interpretation": (
                "positive" if correlation > 0.1
                else "negative" if correlation < -0.1
                else "neutral"
            ),
        }

    def get_optimal_feature_ranges(
        self,
        feature_name: str,
        min_bucket_samples: int = 10,
    ) -> dict[str, Any]:
        """Identify value ranges where feature performs best.

        Args:
            feature_name: Feature to analyze
            min_bucket_samples: Minimum samples per bucket

        Returns:
            Optimal value ranges for the feature
        """
        perf = self._feature_performance.get(feature_name)
        if not perf:
            return {"feature": feature_name, "error": "Feature not found"}

        bucket_performance = {}

        # Get all buckets
        all_buckets = set(perf.value_distribution_hits.keys()) | set(perf.value_distribution_misses.keys())

        for bucket in all_buckets:
            hits = perf.value_distribution_hits.get(bucket, 0)
            misses = perf.value_distribution_misses.get(bucket, 0)
            total = hits + misses

            if total < min_bucket_samples:
                continue

            hit_rate = hits / total
            bucket_performance[bucket] = {
                "hits": hits,
                "misses": misses,
                "total": total,
                "hit_rate": hit_rate,
            }

        if not bucket_performance:
            return {
                "feature": feature_name,
                "error": f"Insufficient samples in any bucket (min {min_bucket_samples})",
            }

        # Find best performing bucket
        best_bucket = max(bucket_performance.items(), key=lambda x: x[1]["hit_rate"])

        return {
            "feature": feature_name,
            "bucket_performance": bucket_performance,
            "optimal_bucket": best_bucket[0],
            "optimal_hit_rate": best_bucket[1]["hit_rate"],
            "thresholds": self.BUCKET_THRESHOLDS.get(feature_name, []),
        }

    def compare_features(
        self,
        feature_names: list[str],
        min_samples: int = 20,
    ) -> dict[str, Any]:
        """Compare performance of multiple features.

        Args:
            feature_names: Features to compare
            min_samples: Minimum samples required

        Returns:
            Feature comparison
        """
        results = []

        for name in feature_names:
            perf = self._feature_performance.get(name)
            if not perf or perf.total_applications < min_samples:
                continue

            correlation = self.analyze_feature_correlation(name, min_samples)

            results.append({
                "feature": name,
                "total_applications": perf.total_applications,
                "hit_rate": perf.compute_hit_rate(),
                "correlation": correlation.get("correlation", 0),
                "reliability": abs(correlation.get("correlation", 0)),
            })

        # Sort by reliability
        results.sort(key=lambda x: x["reliability"], reverse=True)

        return {
            "features_compared": len(results),
            "ranking": results,
            "most_predictive": results[0]["feature"] if results else None,
            "least_predictive": results[-1]["feature"] if results else None,
        }

    def get_feature_adjustment_recommendations(
        self,
        min_samples: int = 30,
    ) -> dict[str, Any]:
        """Generate recommendations for feature weight adjustments.

        Args:
            min_samples: Minimum samples before making recommendations

        Returns:
            Recommendations for feature weight adjustments
        """
        recommendations = {
            "increase_weight": [],
            "decrease_weight": [],
            "refine_usage": [],
            "insufficient_data": [],
        }

        for name, perf in self._feature_performance.items():
            if perf.total_applications < min_samples:
                recommendations["insufficient_data"].append({
                    "feature": name,
                    "samples": perf.total_applications,
                })
                continue

            correlation = self.analyze_feature_correlation(name, min_samples)
            corr_value = correlation.get("correlation", 0)

            if corr_value > 0.2:
                recommendations["increase_weight"].append({
                    "feature": name,
                    "correlation": corr_value,
                    "hit_rate": perf.compute_hit_rate(),
                    "reason": f"Strong positive correlation ({corr_value:.2f})",
                })
            elif corr_value < -0.2:
                recommendations["decrease_weight"].append({
                    "feature": name,
                    "correlation": corr_value,
                    "hit_rate": perf.compute_hit_rate(),
                    "reason": f"Negative correlation ({corr_value:.2f}) - feature may be misleading",
                })
            else:
                # Check if feature has optimal ranges
                optimal = self.get_optimal_feature_ranges(name, min_bucket_samples=10)
                if "optimal_bucket" in optimal:
                    recommendations["refine_usage"].append({
                        "feature": name,
                        "reason": f"Feature has optimal range: use selectively based on value buckets",
                        "optimal_bucket": optimal["optimal_bucket"],
                        "optimal_hit_rate": optimal["optimal_hit_rate"],
                    })

        return recommendations

    def export_feature_report(self) -> dict[str, Any]:
        """Export complete feature performance report."""
        return {
            "summary": {
                "total_features_tracked": len(self.FEATURES),
                "total_impacts_recorded": len(self._impact_history),
            },
            "feature_performances": {
                name: {
                    "applications": perf.total_applications,
                    "hit_rate": perf.compute_hit_rate(),
                    "avg_value_hit": perf.avg_feature_value_on_hit,
                    "avg_value_miss": perf.avg_feature_value_on_miss,
                }
                for name, perf in self._feature_performance.items()
            },
            "correlations": {
                name: self.analyze_feature_correlation(name)
                for name in self.FEATURES
            },
            "optimal_ranges": {
                name: self.get_optimal_feature_ranges(name)
                for name in self.FEATURES
            },
            "recommendations": self.get_feature_adjustment_recommendations(),
            "feature_ranking": self.compare_features(self.FEATURES),
        }
