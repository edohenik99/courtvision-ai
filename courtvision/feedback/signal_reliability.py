"""Signal reliability scoring system.

Determines which signals consistently help vs hurt prediction accuracy.
Tracks performance impact of individual signals to enable weight adjustments.

Phase 8: Causal Attribution and Mistake Analysis
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SignalPerformance:
    """Performance metrics for a single signal."""

    signal_name: str
    total_appearances: int = 0
    hits_when_present: int = 0
    misses_when_present: int = 0
    hit_rate: float = 0.0
    avg_confidence_with: float = 0.0
    avg_confidence_without: float = 0.0
    confidence_lift: float = 0.0  # How much signal boosts confidence
    reliability_score: float = 0.0  # -1 (harmful) to +1 (helpful)

    def compute_hit_rate(self) -> float:
        """Compute hit rate when signal is present."""
        decisions = self.hits_when_present + self.misses_when_present
        return self.hits_when_present / decisions if decisions > 0 else 0.0

    def compute_reliability(self, baseline_hit_rate: float = 0.55) -> float:
        """Compute reliability score relative to baseline.

        Positive = signal improves outcomes
        Negative = signal hurts outcomes
        """
        hit_rate = self.compute_hit_rate()
        if self.total_appearances < 10:
            return 0.0  # Insufficient data

        # Normalize to -1 to +1 scale
        relative_performance = hit_rate - baseline_hit_rate
        # Scale: 0.10 improvement = +1.0 reliability
        return max(-1.0, min(1.0, relative_performance / 0.10))


@dataclass
class SignalImpact:
    """Impact record for a single signal occurrence."""

    signal_name: str
    pick_id: str
    was_present: bool
    result: str  # "hit" or "miss"
    confidence: float
    timestamp: str


class SignalReliabilityTracker:
    """Track reliability of prediction signals.

    Monitors which signals consistently produce wins vs losses,
    enabling dynamic weight adjustments based on historical performance.
    """

    # Signal definitions
    SIGNALS = [
        "high_edge",           # Edge > 5%
        "elite_confidence",    # Confidence > 0.80
        "injury_boost",        # Positive injury context
        "strong_projection",   # Projection significantly above baseline
        "good_recent_form",    # Recent performance above average
        "low_volatility",      # Stable injury situation
        "favorable_matchup",   # Good matchup context
        "rest_advantage",      # Better rest than opponent
        "home_court",          # Home game
        "minutes_confidence",  # High confidence in minutes projection
    ]

    def __init__(self) -> None:
        """Initialize signal reliability tracker."""
        self._signal_performance: dict[str, SignalPerformance] = {
            signal: SignalPerformance(signal_name=signal)
            for signal in self.SIGNALS
        }
        self._impact_history: list[SignalImpact] = []
        self._pick_signals: dict[str, list[str]] = {}  # pick_id -> signals present

    def record_signal_presence(
        self,
        pick_id: str,
        signals: list[str],
        result: str,
        confidence: float,
        timestamp: str,
    ) -> None:
        """Record which signals were present for a pick and the outcome.

        Args:
            pick_id: Unique pick identifier
            signals: List of signal names that were present
            result: "hit" or "miss"
            confidence: Final confidence of the pick
            timestamp: Prediction date
        """
        self._pick_signals[pick_id] = signals

        # Record impact for each known signal
        for signal_name in self.SIGNALS:
            was_present = signal_name in signals

            perf = self._signal_performance[signal_name]
            perf.total_appearances += 1 if was_present else 0

            if was_present:
                if result == "hit":
                    perf.hits_when_present += 1
                else:
                    perf.misses_when_present += 1
                perf.avg_confidence_with = (
                    (perf.avg_confidence_with * (perf.total_appearances - 1) + confidence)
                    / perf.total_appearances
                )

            # Record impact
            impact = SignalImpact(
                signal_name=signal_name,
                pick_id=pick_id,
                was_present=was_present,
                result=result,
                confidence=confidence,
                timestamp=timestamp,
            )
            self._impact_history.append(impact)

        # Update derived metrics
        self._update_signal_metrics()

    def _update_signal_metrics(self) -> None:
        """Update derived metrics for all signals."""
        for perf in self._signal_performance.values():
            perf.hit_rate = perf.compute_hit_rate()
            perf.reliability_score = perf.compute_reliability()

    def get_signal_reliability(self, signal_name: str) -> SignalPerformance | None:
        """Get reliability metrics for a specific signal."""
        return self._signal_performance.get(signal_name)

    def get_all_reliabilities(self) -> dict[str, SignalPerformance]:
        """Get reliability metrics for all signals."""
        return self._signal_performance.copy()

    def get_reliable_signals(
        self,
        min_samples: int = 20,
        min_reliability: float = 0.2,
    ) -> list[str]:
        """Get list of reliable (helpful) signals.

        Args:
            min_samples: Minimum appearances required
            min_reliability: Minimum reliability score (0-1)

        Returns:
            List of signal names that meet criteria
        """
        reliable = []
        for name, perf in self._signal_performance.items():
            if perf.total_appearances >= min_samples and perf.reliability_score >= min_reliability:
                reliable.append(name)
        return reliable

    def get_harmful_signals(
        self,
        min_samples: int = 20,
        max_reliability: float = -0.2,
    ) -> list[str]:
        """Get list of harmful signals.

        Args:
            min_samples: Minimum appearances required
            max_reliability: Maximum reliability score (negative)

        Returns:
            List of signal names that are harmful
        """
        harmful = []
        for name, perf in self._signal_performance.items():
            if perf.total_appearances >= min_samples and perf.reliability_score <= max_reliability:
                harmful.append(name)
        return harmful

    def analyze_signal_combinations(
        self,
        signal_names: list[str],
        min_samples: int = 10,
    ) -> dict[str, Any]:
        """Analyze performance when multiple signals appear together.

        Args:
            signal_names: List of signals to analyze together
            min_samples: Minimum samples for reliable analysis

        Returns:
            Performance metrics for the signal combination
        """
        matching_picks = []

        for pick_id, signals in self._pick_signals.items():
            if all(s in signals for s in signal_names):
                # Find the result for this pick
                for impact in self._impact_history:
                    if impact.pick_id == pick_id and impact.signal_name == signal_names[0]:
                        matching_picks.append(impact)
                        break

        if len(matching_picks) < min_samples:
            return {
                "signals": signal_names,
                "error": f"Insufficient samples: {len(matching_picks)} < {min_samples}",
            }

        hits = sum(1 for p in matching_picks if p.result == "hit")
        misses = sum(1 for p in matching_picks if p.result == "miss")
        hit_rate = hits / (hits + misses) if (hits + misses) > 0 else 0.0

        return {
            "signals": signal_names,
            "total_appearances": len(matching_picks),
            "hits": hits,
            "misses": misses,
            "hit_rate": hit_rate,
            "avg_confidence": sum(p.confidence for p in matching_picks) / len(matching_picks),
            "signal_strength": "strong" if hit_rate > 0.65 else "moderate" if hit_rate > 0.55 else "weak",
        }

    def get_weight_adjustment_recommendations(
        self,
        min_samples: int = 30,
    ) -> dict[str, Any]:
        """Generate recommendations for signal weight adjustments.

        Args:
            min_samples: Minimum samples before making recommendations

        Returns:
            Recommendations for increasing/decreasing signal weights
        """
        recommendations = {
            "increase_weight": [],
            "decrease_weight": [],
            "maintain": [],
            "insufficient_data": [],
        }

        for name, perf in self._signal_performance.items():
            if perf.total_appearances < min_samples:
                recommendations["insufficient_data"].append({
                    "signal": name,
                    "samples": perf.total_appearances,
                })
                continue

            if perf.reliability_score > 0.3:
                recommendations["increase_weight"].append({
                    "signal": name,
                    "reliability": perf.reliability_score,
                    "hit_rate": perf.hit_rate,
                    "reason": f"Strong positive reliability ({perf.reliability_score:.2f})",
                })
            elif perf.reliability_score < -0.3:
                recommendations["decrease_weight"].append({
                    "signal": name,
                    "reliability": perf.reliability_score,
                    "hit_rate": perf.hit_rate,
                    "reason": f"Negative reliability ({perf.reliability_score:.2f}) - signal may be misleading",
                })
            else:
                recommendations["maintain"].append({
                    "signal": name,
                    "reliability": perf.reliability_score,
                    "hit_rate": perf.hit_rate,
                    "reason": "Neutral impact - no change needed",
                })

        return recommendations

    def compute_signal_weights(
        self,
        base_weight: float = 1.0,
        min_samples: int = 30,
    ) -> dict[str, float]:
        """Compute dynamic weights for signals based on reliability.

        Args:
            base_weight: Starting weight for all signals
            min_samples: Minimum samples before adjusting

        Returns:
            Dictionary of signal names to weights
        """
        weights = {}

        for name, perf in self._signal_performance.items():
            if perf.total_appearances < min_samples:
                weights[name] = base_weight
                continue

            # Adjust weight based on reliability score
            # Reliability: -1 to +1, Weight: 0.5 to 1.5
            weight_multiplier = 1.0 + (perf.reliability_score * 0.5)
            weights[name] = base_weight * weight_multiplier

        return weights

    def export_reliability_report(self) -> dict[str, Any]:
        """Export complete signal reliability report."""
        return {
            "summary": {
                "total_signals_tracked": len(self.SIGNALS),
                "total_impacts_recorded": len(self._impact_history),
                "reliable_signals": len(self.get_reliable_signals()),
                "harmful_signals": len(self.get_harmful_signals()),
            },
            "signal_performance": {
                name: {
                    "appearances": perf.total_appearances,
                    "hit_rate": perf.hit_rate,
                    "reliability": perf.reliability_score,
                    "assessment": (
                        "reliable" if perf.reliability_score > 0.2
                        else "harmful" if perf.reliability_score < -0.2
                        else "neutral"
                    ),
                }
                for name, perf in self._signal_performance.items()
            },
            "recommendations": self.get_weight_adjustment_recommendations(),
            "suggested_weights": self.compute_signal_weights(),
        }
