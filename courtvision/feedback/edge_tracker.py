"""Edge reliability tracking by edge size buckets.

Tracks hit rates by edge size ranges and adjusts edge weighting dynamically.
Identifies which edge ranges are most reliable for predictions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EdgeBucketPerformance:
    """Performance metrics for an edge size bucket."""

    bucket_label: str
    edge_min: float
    edge_max: float
    total_picks: int = 0
    hits: int = 0
    misses: int = 0
    hit_rate: float = 0.0
    avg_confidence: float = 0.0
    reliability_score: float = 0.0  # Composite score for ranking


@dataclass
class EdgeWeightAdjustment:
    """Edge weight adjustment recommendation with diagnostics."""

    bucket_label: str
    current_weight: float
    recommended_weight: float
    hit_rate: float
    sample_size: int
    days_of_data: int = 0
    adjustment_confidence: float = 0.0
    applied: bool = False
    skipped_reason: str = ""
    reason: str = ""


class EdgeReliabilityTracker:
    """Track and adjust edge reliability by size buckets with stability safeguards.

    Maintains hit rate statistics for different edge ranges:
    - 0-1 edge
    - 1-2 edge
    - 2-3 edge
    - 3-5 edge
    - 5-10 edge
    - 10+ edge

    Adjusts edge weighting dynamically based on performance.

    Safeguards:
    - Minimum 30 picks or 7 days required
    - EMA smoothing for gradual adjustments
    - Adjustment capped at ±15%
    - Daily cooldown to prevent overfitting
    """

    # Default buckets (edge ranges)
    DEFAULT_BUCKETS = [
        (0.0, 1.0),
        (1.0, 2.0),
        (2.0, 3.0),
        (3.0, 5.0),
        (5.0, 10.0),
        (10.0, float("inf")),
    ]

    # Minimum samples for reliable statistics (30 picks or 7 days)
    MIN_SAMPLES = 30
    MIN_DAYS = 7

    # Minimum hit rate to consider bucket viable
    MIN_HIT_RATE = 0.50

    # Adjustment caps (±15%)
    MAX_WEIGHT = 1.15
    MIN_WEIGHT = 0.85
    MAX_ADJUSTMENT = 0.15

    # EMA smoothing (30% new, 70% historical)
    EMA_ALPHA = 0.3

    # Daily cooldown
    COOLDOWN_HOURS = 24

    def __init__(
        self,
        performance_store: Any,
        buckets: list[tuple[float, float]] | None = None,
    ) -> None:
        """Initialize edge tracker.

        Args:
            performance_store: PerformanceStore instance
            buckets: Custom edge buckets (default if None)
        """
        self.store = performance_store
        self.buckets = buckets or self.DEFAULT_BUCKETS
        self._weight_multipliers: dict[str, float] = {}
        self._initialize_weights()
        # EMA tracking
        self._ema_values: dict[str, float] = {}
        self._initialize_ema()
        # Cooldown tracking
        self._last_update: datetime | None = None
        # Adjustment history
        self._adjustment_history: list[EdgeWeightAdjustment] = []

    def _initialize_weights(self) -> None:
        """Initialize weight multipliers for each bucket."""
        for low, high in self.buckets:
            label = self._bucket_label(low, high)
            self._weight_multipliers[label] = 1.0

    def _initialize_ema(self) -> None:
        """Initialize EMA tracking values."""
        for low, high in self.buckets:
            label = self._bucket_label(low, high)
            self._ema_values[label] = 1.0

    def _check_cooldown(self) -> tuple[bool, str]:
        """Check if cooldown period has passed."""
        if self._last_update is None:
            return True, "First update"

        elapsed = datetime.now() - self._last_update
        if elapsed < timedelta(hours=self.COOLDOWN_HOURS):
            hours_remaining = (timedelta(hours=self.COOLDOWN_HOURS) - elapsed).total_seconds() / 3600
            return False, f"Cooldown active: {hours_remaining:.1f} hours remaining"

        return True, "Cooldown clear"

    def _check_sample_threshold(self, window_days: int) -> tuple[bool, int]:
        """Check if minimum sample threshold is met.

        Returns:
            (sufficient, days_count)
        """
        window = self.store.get_window_metrics(window_days)
        days_count = window.total_picks
        sufficient = days_count >= self.MIN_DAYS
        return sufficient, days_count

    def _apply_ema(self, bucket: str, raw_value: float) -> float:
        """Apply exponential moving average smoothing."""
        current_ema = self._ema_values.get(bucket, 1.0)
        new_ema = self.EMA_ALPHA * raw_value + (1 - self.EMA_ALPHA) * current_ema
        self._ema_values[bucket] = new_ema
        return new_ema

    def _clamp_weight(self, value: float) -> float:
        """Clamp weight to ±15% range."""
        return max(self.MIN_WEIGHT, min(self.MAX_WEIGHT, value))

    def analyze_reliability(
        self,
        window_days: int = 30,
    ) -> dict[str, EdgeBucketPerformance]:
        """Analyze reliability for each edge bucket.

        Returns dict mapping bucket labels to performance metrics.
        """
        edge_reliability = self.store.get_edge_reliability(window_days)
        results = {}

        for low, high in self.buckets:
            label = self._bucket_label(low, high)
            data = edge_reliability.get(label, {"total": 0, "hits": 0, "misses": 0, "hit_rate": 0.0})

            # Compute reliability score
            # Weighs hit rate and sample size
            reliability = self._compute_reliability_score(data)

            results[label] = EdgeBucketPerformance(
                bucket_label=label,
                edge_min=low,
                edge_max=high,
                total_picks=data["total"],
                hits=data["hits"],
                misses=data["misses"],
                hit_rate=data["hit_rate"],
                reliability_score=reliability,
            )

        return results

    def update_weights(self, window_days: int = 30) -> list[EdgeWeightAdjustment]:
        """Update edge weight multipliers with stability safeguards.

        Safeguards:
        1. Cooldown check (max 1/day)
        2. Sample threshold (30 picks or 7 days)
        3. EMA smoothing
        4. Adjustment caps (±15%)

        Returns list of adjustments with diagnostics.
        """
        # Check cooldown
        can_update, cooldown_reason = self._check_cooldown()
        if not can_update:
            return [EdgeWeightAdjustment(
                bucket_label="all",
                current_weight=1.0,
                recommended_weight=1.0,
                hit_rate=0.0,
                sample_size=0,
                applied=False,
                skipped_reason=cooldown_reason,
                reason=cooldown_reason,
            )]

        # Check sample threshold
        sufficient, days_count = self._check_sample_threshold(window_days)
        if not sufficient:
            return [EdgeWeightAdjustment(
                bucket_label="all",
                current_weight=1.0,
                recommended_weight=1.0,
                hit_rate=0.0,
                sample_size=0,
                days_of_data=days_count,
                applied=False,
                skipped_reason=f"Need {self.MIN_DAYS} days of data, got {days_count}",
                reason="Insufficient data for weight update",
            )]

        reliability = self.analyze_reliability(window_days)
        adjustments = []

        for label, perf in reliability.items():
            # Skip buckets with insufficient data
            if perf.total_picks < self.MIN_SAMPLES:
                adjustments.append(EdgeWeightAdjustment(
                    bucket_label=label,
                    current_weight=self._weight_multipliers[label],
                    recommended_weight=self._weight_multipliers[label],
                    hit_rate=perf.hit_rate,
                    sample_size=perf.total_picks,
                    days_of_data=days_count,
                    applied=False,
                    skipped_reason=f"Need {self.MIN_SAMPLES} picks, got {perf.total_picks}",
                    reason="Insufficient samples in bucket",
                ))
                continue

            current_weight = self._weight_multipliers[label]
            raw_weight = self._compute_weight_adjustment(perf)

            # Apply EMA smoothing
            smoothed_weight = self._apply_ema(label, raw_weight)

            # Clamp to bounds
            new_weight = self._clamp_weight(smoothed_weight)

            if abs(new_weight - current_weight) > 0.01:  # Significant change
                self._weight_multipliers[label] = new_weight

                # Calculate adjustment confidence
                adj_confidence = min(0.95, perf.total_picks / 100)

                adjustment = EdgeWeightAdjustment(
                    bucket_label=label,
                    current_weight=current_weight,
                    recommended_weight=new_weight,
                    hit_rate=perf.hit_rate,
                    sample_size=perf.total_picks,
                    days_of_data=days_count,
                    adjustment_confidence=adj_confidence,
                    applied=True,
                    reason=self._generate_reason(perf, new_weight),
                )
                adjustments.append(adjustment)
                self._adjustment_history.append(adjustment)

        # Update cooldown if any adjustments applied
        if any(a.applied for a in adjustments):
            self._last_update = datetime.now()

        return adjustments

    def apply_edge_weight(self, edge: float, bucket_label: str | None = None) -> float:
        """Apply weight multiplier to edge value.

        Args:
            edge: Raw edge value
            bucket_label: Edge bucket (auto-detected if None)

        Returns:
            Weighted edge value
        """
        if bucket_label is None:
            bucket_label = self._edge_to_bucket(edge)

        multiplier = self._weight_multipliers.get(bucket_label, 1.0)
        return edge * multiplier

    def get_recommended_minimum_edge(self, target_hit_rate: float = 0.60) -> float:
        """Get recommended minimum edge threshold.

        Finds the smallest edge range that achieves target hit rate.

        Args:
            target_hit_rate: Minimum acceptable hit rate

        Returns:
            Recommended minimum edge value
        """
        # Analyze all windows for stability
        for window in [7, 14, 30]:
            reliability = self.analyze_reliability(window)

            # Find buckets meeting target
            viable_buckets = [
                (label, perf) for label, perf in reliability.items()
                if perf.hit_rate >= target_hit_rate
                and perf.total_picks >= self.MIN_SAMPLES
            ]

            if viable_buckets:
                # Return the minimum edge of the lowest viable bucket
                min_edge = min(perf.edge_min for _, perf in viable_buckets)
                return min_edge

        # Default if no viable buckets found
        return 2.0

    def get_edge_tier_rankings(self) -> list[tuple[str, float, int]]:
        """Get edge buckets ranked by reliability score.

        Returns list of (bucket_label, reliability_score, sample_size)
        """
        reliability = self.analyze_reliability(30)  # Use 30-day window

        ranked = sorted(
            [(label, perf.reliability_score, perf.total_picks)
             for label, perf in reliability.items()],
            key=lambda x: x[1],
            reverse=True,
        )

        return ranked

    def get_weight_status(self) -> dict[str, Any]:
        """Get current weight multiplier status."""
        return {
            "weight_multipliers": self._weight_multipliers.copy(),
            "tier_rankings": self.get_edge_tier_rankings(),
            "recommended_min_edge": self.get_recommended_minimum_edge(),
        }

    def reset_weights(self) -> None:
        """Reset all weight multipliers to 1.0."""
        for label in self._weight_multipliers:
            self._weight_multipliers[label] = 1.0
            self._ema_values[label] = 1.0
        self._last_update = None
        self._adjustment_history.clear()

    def get_adjustment_diagnostics(self, bucket: str | None = None) -> dict[str, Any]:
        """Get diagnostic information about edge weight adjustments."""
        history = self._adjustment_history
        if bucket:
            history = [h for h in history if h.bucket_label == bucket]

        if not history:
            return {
                "total_adjustments": 0,
                "last_adjustment": None,
                "current_weight": self._weight_multipliers.get(bucket, 1.0) if bucket else self._weight_multipliers,
            }

        recent = history[-10:]
        return {
            "total_adjustments": len(history),
            "recent_adjustments": [
                {
                    "bucket": h.bucket_label,
                    "old_weight": h.current_weight,
                    "new_weight": h.recommended_weight,
                    "sample_size": h.sample_size,
                    "confidence": h.adjustment_confidence,
                    "applied": h.applied,
                }
                for h in recent
            ],
            "average_sample_size": sum(h.sample_size for h in history) / len(history),
            "average_confidence": sum(h.adjustment_confidence for h in history) / len(history),
            "current_weight": self._weight_multipliers.get(bucket, 1.0) if bucket else self._weight_multipliers,
            "last_update": self._last_update.isoformat() if self._last_update else None,
        }

    def _bucket_label(self, low: float, high: float) -> str:
        """Generate label for a bucket."""
        if high == float("inf"):
            return f"{low:.1f}+"
        return f"{low:.1f}-{high:.1f}"

    def _edge_to_bucket(self, edge: float) -> str:
        """Map edge value to bucket label."""
        for low, high in self.buckets:
            if low <= edge < high:
                return self._bucket_label(low, high)
        return self._bucket_label(10.0, float("inf"))

    def _compute_reliability_score(self, data: dict[str, Any]) -> float:
        """Compute composite reliability score.

        Weighs:
        - Hit rate (primary)
        - Sample size confidence (secondary)
        """
        hit_rate = data["hit_rate"]
        samples = data["total"]

        # Penalize buckets with few samples
        sample_confidence = min(samples / 30, 1.0)  # Max confidence at 30+ samples

        # Reliability score combines hit rate and sample confidence
        return hit_rate * sample_confidence

    def _compute_weight_adjustment(self, perf: EdgeBucketPerformance) -> float:
        """Compute new weight multiplier for a bucket."""
        if perf.total_picks < self.MIN_SAMPLES:
            return 1.0

        hit_rate = perf.hit_rate

        # Adjust weights based on hit rate performance
        if hit_rate < 0.45:  # Poor performance
            # Reduce weight for this edge range
            return max(0.7, 0.9 - (0.45 - hit_rate))
        elif hit_rate < self.MIN_HIT_RATE:
            # Moderate reduction
            return 0.9
        elif hit_rate > 0.70:  # Strong performance
            # Increase weight for this edge range
            return min(1.3, 1.1 + (hit_rate - 0.70))
        elif hit_rate > 0.60:  # Good performance
            # Slight increase
            return 1.05

        # Neutral performance - keep weight
        return 1.0

    def _generate_reason(self, perf: EdgeBucketPerformance, new_weight: float) -> str:
        """Generate human-readable reason for weight adjustment."""
        if new_weight < 0.9:
            return f"Poor hit rate ({perf.hit_rate:.1%}) in {perf.bucket_label} edge range - reducing weight"
        elif new_weight < 1.0:
            return f"Below-average hit rate ({perf.hit_rate:.1%}) - slight weight reduction"
        elif new_weight > 1.1:
            return f"Strong hit rate ({perf.hit_rate:.1%}) in {perf.bucket_label} edge range - increasing weight"
        elif new_weight > 1.0:
            return f"Good hit rate ({perf.hit_rate:.1%}) - slight weight increase"

        return f"Neutral hit rate ({perf.hit_rate:.1%}) - maintaining weight"
