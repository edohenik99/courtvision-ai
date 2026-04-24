"""Market bias detection for over/under and stat-type inefficiencies.

Phase 11: Market Adaptation and Opponent Modeling
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MarketBias:
    """Detected market bias."""

    bias_type: str  # "over", "under", "stat_type"
    entity: str  # stat type or market category

    # Bias metrics
    sample_size: int
    hit_rate: float
    expected_hit_rate: float  # Assuming efficient market = 0.5 for -110 odds

    # Bias strength
    bias_score: float  # How strong the bias is
    edge_opportunity: float  # Estimated edge from bias

    # Confidence
    confidence: float  # 0-1 confidence in bias detection
    statistical_significance: float  # p-value approximation

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "bias": {
                "type": self.bias_type,
                "entity": self.entity,
            },
            "sample": {
                "size": self.sample_size,
                "hit_rate": round(self.hit_rate, 3),
                "expected_hit_rate": round(self.expected_hit_rate, 3),
            },
            "metrics": {
                "bias_score": round(self.bias_score, 3),
                "edge_opportunity": round(self.edge_opportunity, 3),
                "confidence": round(self.confidence, 3),
                "significance": round(self.statistical_significance, 3),
            },
        }


@dataclass
class BiasReport:
    """Complete bias detection report."""

    total_picks_analyzed: int
    detected_biases: list[MarketBias] = field(default_factory=list)

    # Over/under bias
    over_hit_rate: float = 0.0
    under_hit_rate: float = 0.0
    over_under_sample: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "summary": {
                "total_picks": self.total_picks_analyzed,
                "biases_detected": len(self.detected_biases),
            },
            "over_under": {
                "over_hit_rate": round(self.over_hit_rate, 3),
                "under_hit_rate": round(self.under_hit_rate, 3),
                "sample": self.over_under_sample,
                "bias": "over" if self.over_hit_rate > self.under_hit_rate else "under"
                if abs(self.over_hit_rate - self.under_hit_rate) > 0.05 else "neutral",
            },
            "biases": [b.to_dict() for b in self.detected_biases],
        }


class BiasDetector:
    """Detect market biases in over/under and stat types.

    Identifies systematic inefficiencies in market pricing.
    """

    # Minimum sample size for bias detection
    MIN_SAMPLE = 30

    # Bias thresholds
    SIGNIFICANT_BIAS = 0.03  # 3% deviation from expected
    STRONG_BIAS = 0.05  # 5% deviation

    def __init__(self) -> None:
        """Initialize bias detector."""
        self.results: list[dict] = []
        self.by_stat: dict[str, list[dict]] = {}
        self.by_over_under: dict[str, list[dict]] = {
            "over": [],
            "under": [],
        }

    def add_result(
        self,
        stat_type: str,
        over_under: str,  # "over" or "under"
        hit: bool,
        line_value: float,
        actual_value: float,
    ) -> None:
        """Add a pick result for bias analysis.

        Args:
            stat_type: Stat type
            over_under: "over" or "under"
            hit: Whether the pick hit
            line_value: Betting line
            actual_value: Actual result
        """
        result = {
            "stat_type": stat_type,
            "over_under": over_under,
            "hit": hit,
            "line_value": line_value,
            "actual_value": actual_value,
        }

        self.results.append(result)

        if stat_type not in self.by_stat:
            self.by_stat[stat_type] = []
        self.by_stat[stat_type].append(result)

        if over_under in self.by_over_under:
            self.by_over_under[over_under].append(result)

    def detect_over_under_bias(self) -> dict[str, Any] | None:
        """Detect over/under market bias."""
        over_results = self.by_over_under["over"]
        under_results = self.by_over_under["under"]

        if len(over_results) < self.MIN_SAMPLE or len(under_results) < self.MIN_SAMPLE:
            return None

        over_hit_rate = sum(1 for r in over_results if r["hit"]) / len(over_results)
        under_hit_rate = sum(1 for r in under_results if r["hit"]) / len(under_results)

        # Expected hit rate at -110 odds (implied prob ~52.4%, after vig ~50%)
        expected_rate = 0.50

        over_bias = over_hit_rate - expected_rate
        under_bias = under_hit_rate - expected_rate

        # Determine if bias is significant
        bias_detected = abs(over_bias) > self.SIGNIFICANT_BIAS or abs(under_bias) > self.SIGNIFICANT_BIAS

        if not bias_detected:
            return {
                "bias_detected": False,
                "over_hit_rate": round(over_hit_rate, 3),
                "under_hit_rate": round(under_hit_rate, 3),
                "message": "No significant over/under bias detected",
            }

        # Create bias objects
        biases = []

        if abs(over_bias) > self.SIGNIFICANT_BIAS:
            bias = MarketBias(
                bias_type="over",
                entity="market_wide",
                sample_size=len(over_results),
                hit_rate=over_hit_rate,
                expected_hit_rate=expected_rate,
                bias_score=abs(over_bias),
                edge_opportunity=over_bias,
                confidence=min(1.0, len(over_results) / 100),
                statistical_significance=self._calculate_significance(over_hit_rate, expected_rate, len(over_results)),
            )
            biases.append(bias)

        if abs(under_bias) > self.SIGNIFICANT_BIAS:
            bias = MarketBias(
                bias_type="under",
                entity="market_wide",
                sample_size=len(under_results),
                hit_rate=under_hit_rate,
                expected_hit_rate=expected_rate,
                bias_score=abs(under_bias),
                edge_opportunity=under_bias,
                confidence=min(1.0, len(under_results) / 100),
                statistical_significance=self._calculate_significance(under_hit_rate, expected_rate, len(under_results)),
            )
            biases.append(bias)

        return {
            "bias_detected": True,
            "over_hit_rate": round(over_hit_rate, 3),
            "under_hit_rate": round(under_hit_rate, 3),
            "over_bias": round(over_bias, 3),
            "under_bias": round(under_bias, 3),
            "biases": [b.to_dict() for b in biases],
            "recommendation": self._generate_ou_recommendation(over_bias, under_bias),
        }

    def detect_stat_type_biases(self) -> list[MarketBias]:
        """Detect biases in specific stat types."""
        biases = []

        for stat_type, results in self.by_stat.items():
            if len(results) < self.MIN_SAMPLE:
                continue

            hit_rate = sum(1 for r in results if r["hit"]) / len(results)
            expected_rate = 0.50

            bias_score = hit_rate - expected_rate

            if abs(bias_score) > self.SIGNIFICANT_BIAS:
                bias = MarketBias(
                    bias_type="stat_type",
                    entity=stat_type,
                    sample_size=len(results),
                    hit_rate=hit_rate,
                    expected_hit_rate=expected_rate,
                    bias_score=abs(bias_score),
                    edge_opportunity=bias_score,
                    confidence=min(1.0, len(results) / 100),
                    statistical_significance=self._calculate_significance(hit_rate, expected_rate, len(results)),
                )
                biases.append(bias)

        # Sort by bias strength
        biases.sort(key=lambda b: abs(b.bias_score), reverse=True)

        return biases

    def _calculate_significance(
        self,
        observed_rate: float,
        expected_rate: float,
        sample_size: int,
    ) -> float:
        """Calculate approximate statistical significance."""
        if sample_size < 2:
            return 1.0

        # Standard error
        se = (expected_rate * (1 - expected_rate) / sample_size) ** 0.5
        if se == 0:
            return 1.0

        # Z-score
        z = (observed_rate - expected_rate) / se

        # Approximate p-value (two-tailed)
        # Using rough normal approximation
        p_value = 2 * (1 - self._normal_cdf(abs(z)))

        return max(0.001, min(1.0, p_value))

    def _normal_cdf(self, x: float) -> float:
        """Approximate normal CDF."""
        import math
        # Abramowitz and Stegun approximation
        b1 = 0.319381530
        b2 = -0.356563782
        b3 = 1.781477937
        b4 = -1.821255978
        b5 = 1.330274429
        p = 0.2316419
        c = 0.39894228

        if x >= 0.0:
            t = 1.0 / (1.0 + p * x)
            return 1.0 - c * math.exp(-x * x / 2.0) * t * (t * (t * (t * (t * b5 + b4) + b3) + b2) + b1)
        else:
            t = 1.0 / (1.0 - p * x)
            return c * math.exp(-x * x / 2.0) * t * (t * (t * (t * (t * b5 + b4) + b3) + b2) + b1)

    def _generate_ou_recommendation(self, over_bias: float, under_bias: float) -> str:
        """Generate recommendation based on over/under bias."""
        if over_bias > self.STRONG_BIAS:
            return "Strong over bias detected - consider increasing over exposure"
        elif over_bias > self.SIGNIFICANT_BIAS:
            return "Moderate over bias - slight preference for overs"
        elif under_bias > self.STRONG_BIAS:
            return "Strong under bias detected - consider increasing under exposure"
        elif under_bias > self.SIGNIFICANT_BIAS:
            return "Moderate under bias - slight preference for unders"
        else:
            return "No actionable over/under bias"

    def generate_full_report(self) -> BiasReport:
        """Generate complete bias detection report."""
        ou_analysis = self.detect_over_under_bias()
        stat_biases = self.detect_stat_type_biases()

        all_biases = stat_biases.copy()

        if ou_analysis and ou_analysis.get("biases"):
            from dataclasses import asdict
            for bias_dict in ou_analysis["biases"]:
                # Reconstruct MarketBias from dict
                bias = MarketBias(
                    bias_type=bias_dict["bias"]["type"],
                    entity=bias_dict["bias"]["entity"],
                    sample_size=bias_dict["sample"]["size"],
                    hit_rate=bias_dict["sample"]["hit_rate"],
                    expected_hit_rate=bias_dict["sample"]["expected_hit_rate"],
                    bias_score=bias_dict["metrics"]["bias_score"],
                    edge_opportunity=bias_dict["metrics"]["edge_opportunity"],
                    confidence=bias_dict["metrics"]["confidence"],
                    statistical_significance=bias_dict["metrics"]["significance"],
                )
                all_biases.append(bias)

        return BiasReport(
            total_picks_analyzed=len(self.results),
            detected_biases=all_biases,
            over_hit_rate=ou_analysis["over_hit_rate"] if ou_analysis else 0.0,
            under_hit_rate=ou_analysis["under_hit_rate"] if ou_analysis else 0.0,
            over_under_sample=(len(self.by_over_under["over"]) + len(self.by_over_under["under"])),
        )

    def get_adjusted_thresholds(
        self,
        base_threshold: float = 0.05,
    ) -> dict[str, float]:
        """Get adjusted edge thresholds based on detected biases."""
        report = self.generate_full_report()

        adjustments = {}

        # Adjust for over/under bias
        if report.over_hit_rate > report.under_hit_rate + 0.05:
            # Over market is softer - require less edge for overs
            adjustments["over_edge"] = base_threshold * 0.9
            adjustments["under_edge"] = base_threshold * 1.1
        elif report.under_hit_rate > report.over_hit_rate + 0.05:
            # Under market is softer
            adjustments["over_edge"] = base_threshold * 1.1
            adjustments["under_edge"] = base_threshold * 0.9
        else:
            adjustments["over_edge"] = base_threshold
            adjustments["under_edge"] = base_threshold

        # Adjust for stat type biases
        for bias in report.detected_biases:
            if bias.bias_type == "stat_type":
                if bias.edge_opportunity > 0:
                    # This stat type hits more - lower threshold
                    adjustments[f"{bias.entity}_edge"] = base_threshold * 0.85
                else:
                    # This stat type hits less - raise threshold
                    adjustments[f"{bias.entity}_edge"] = base_threshold * 1.15

        return adjustments

    def export_bias_report(self) -> dict[str, Any]:
        """Export complete bias report."""
        report = self.generate_full_report()
        adjustments = self.get_adjusted_thresholds()

        return {
            "report": report.to_dict(),
            "threshold_adjustments": {k: round(v, 3) for k, v in adjustments.items()},
            "recommendations": [
                bias.entity for bias in report.detected_biases
                if abs(bias.edge_opportunity) > 0.04
            ],
        }
