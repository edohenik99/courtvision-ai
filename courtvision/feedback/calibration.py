"""Confidence calibration feedback system with stability safeguards.

Dynamically adjusts confidence thresholds based on historical performance.
If high-confidence picks underperform, reduces future confidence.
If low-confidence picks outperform, adjusts thresholds upward.

Includes safeguards:
- Minimum sample thresholds (30 picks or 7 days)
- EMA smoothing for gradual adjustments
- Adjustment caps (±10%)
- Daily cooldown to prevent overfitting
- Fallback to defaults when data insufficient
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


@dataclass
class CalibrationAdjustment:
    """Single calibration adjustment recommendation with diagnostics."""

    target_bucket: str
    adjustment: float  # Positive = increase confidence, negative = decrease
    reason: str
    confidence_multiplier: float = 1.0
    # Stability diagnostics
    sample_size: int = 0
    days_of_data: int = 0
    adjustment_confidence: float = 0.0  # 0-1 confidence in this adjustment
    applied: bool = False  # Whether adjustment was actually applied
    skipped_reason: str = ""  # If not applied, why


class ConfidenceCalibrator:
    """Dynamic confidence calibration with stability safeguards.

    Tracks hit rates by confidence bucket and adjusts:
    - Confidence multipliers for future predictions
    - Thresholds for confidence band assignments
    - Penalty strength based on calibration gaps

    Safeguards:
    - Minimum 30 picks or 7 days of data required
    - EMA smoothing prevents sudden jumps
    - Adjustment capped at ±10%
    - Max 1 update per day cooldown
    """

    # Target hit rates for each confidence bucket
    TARGET_HIT_RATES = {
        "low": 0.55,
        "mid": 0.62,
        "high": 0.70,
        "elite": 0.78,
    }

    # Maximum adjustment per iteration (10% cap)
    MAX_ADJUSTMENT = 0.10
    MAX_MULTIPLIER = 1.10  # +10% cap
    MIN_MULTIPLIER = 0.90  # -10% cap

    # Adjustment speed factor (lower = more conservative)
    ADJUSTMENT_SPEED = 0.5

    # EMA smoothing alpha (0.3 = 30% new, 70% historical)
    EMA_ALPHA = 0.3

    # Minimum sample thresholds
    MIN_SAMPLES = 30
    MIN_DAYS = 7

    # Daily cooldown
    COOLDOWN_HOURS = 24

    def __init__(self, performance_store: Any) -> None:
        """Initialize with performance store."""
        self.store = performance_store
        self._confidence_multipliers: dict[str, float] = {
            "low": 1.0,
            "mid": 1.0,
            "high": 1.0,
            "elite": 1.0,
        }
        self._threshold_adjustments: dict[str, float] = {
            "low_to_mid": 0.0,
            "mid_to_high": 0.0,
            "high_to_elite": 0.0,
        }
        # EMA tracking for smooth transitions
        self._ema_values: dict[str, float] = {
            "low": 1.0,
            "mid": 1.0,
            "high": 1.0,
            "elite": 1.0,
        }
        # Cooldown tracking
        self._last_update: datetime | None = None
        # Adjustment history for diagnostics
        self._adjustment_history: list[CalibrationAdjustment] = []

    def _check_cooldown(self) -> tuple[bool, str]:
        """Check if cooldown period has passed.

        Returns:
            (can_update, reason)
        """
        if self._last_update is None:
            return True, "First update"

        elapsed = datetime.now() - self._last_update
        if elapsed < timedelta(hours=self.COOLDOWN_HOURS):
            hours_remaining = (timedelta(hours=self.COOLDOWN_HOURS) - elapsed).total_seconds() / 3600
            return False, f"Cooldown active: {hours_remaining:.1f} hours remaining"

        return True, "Cooldown clear"

    def _check_sample_threshold(self, bucket: str, window_days: int) -> tuple[bool, int, int]:
        """Check if sample threshold is met.

        Returns:
            (sufficient, sample_count, days_count)
        """
        calibration = self.store.get_confidence_calibration(window_days)
        data = calibration.get(bucket, {"total": 0})
        sample_count = data.get("total", 0)

        # Count actual days with data
        all_windows = self.store.get_all_windows()
        max_days = max(all_windows.keys()) if all_windows else window_days
        days_data = self.store.get_window_metrics(max_days)
        days_count = days_data.total_picks

        sufficient = sample_count >= self.MIN_SAMPLES or days_count >= self.MIN_DAYS
        return sufficient, sample_count, days_count

    def _apply_ema(self, bucket: str, raw_value: float) -> float:
        """Apply exponential moving average smoothing.

        Formula: EMA_t = alpha * raw + (1-alpha) * EMA_{t-1}
        """
        current_ema = self._ema_values[bucket]
        new_ema = self.EMA_ALPHA * raw_value + (1 - self.EMA_ALPHA) * current_ema
        self._ema_values[bucket] = new_ema
        return new_ema

    def _clamp_multiplier(self, value: float) -> float:
        """Clamp multiplier to ±10% range (0.9 to 1.1)."""
        return max(self.MIN_MULTIPLIER, min(self.MAX_MULTIPLIER, value))

    def update_calibration(self, window_days: int = 30) -> list[CalibrationAdjustment]:
        """Update calibration with stability safeguards.

        Safeguards applied:
        1. Cooldown check (max 1/day)
        2. Sample threshold (30 picks or 7 days)
        3. Gap threshold (only adjust if >10% deviation)
        4. EMA smoothing (gradual transitions)
        5. Adjustment caps (±10% total range)

        Returns list of adjustments with diagnostics.
        """
        # Check cooldown
        can_update, cooldown_reason = self._check_cooldown()
        if not can_update:
            return [CalibrationAdjustment(
                target_bucket="all",
                adjustment=0.0,
                reason=cooldown_reason,
                confidence_multiplier=1.0,
                applied=False,
                skipped_reason=cooldown_reason,
            )]

        calibration = self.store.get_confidence_calibration(window_days)
        adjustments = []

        for bucket, data in calibration.items():
            # Check sample threshold
            sufficient, sample_count, days_count = self._check_sample_threshold(bucket, window_days)

            if not sufficient:
                adjustments.append(CalibrationAdjustment(
                    target_bucket=bucket,
                    adjustment=0.0,
                    reason="Insufficient data for adjustment",
                    confidence_multiplier=self._confidence_multipliers[bucket],
                    sample_size=sample_count,
                    days_of_data=days_count,
                    adjustment_confidence=0.0,
                    applied=False,
                    skipped_reason=f"Need {self.MIN_SAMPLES} picks or {self.MIN_DAYS} days, got {sample_count}/{days_count}",
                ))
                continue

            target = self.TARGET_HIT_RATES.get(bucket, 0.60)
            actual = data["hit_rate"]
            gap = actual - target

            # Only adjust if significant gap (>10%)
            if abs(gap) < 0.10:
                adjustments.append(CalibrationAdjustment(
                    target_bucket=bucket,
                    adjustment=0.0,
                    reason=f"Gap {gap:.1%} within tolerance (±10%)",
                    confidence_multiplier=self._confidence_multipliers[bucket],
                    sample_size=sample_count,
                    days_of_data=days_count,
                    adjustment_confidence=0.0,
                    applied=False,
                    skipped_reason="Gap within tolerance",
                ))
                continue

            # Calculate raw adjustment (capped per-iteration)
            raw_adjustment = max(min(gap * self.ADJUSTMENT_SPEED, self.MAX_ADJUSTMENT), -self.MAX_ADJUSTMENT)

            # Calculate new multiplier before EMA
            raw_new_multiplier = self._confidence_multipliers[bucket] + raw_adjustment

            # Apply EMA smoothing
            smoothed_multiplier = self._apply_ema(bucket, raw_new_multiplier)

            # Clamp to total range
            final_multiplier = self._clamp_multiplier(smoothed_multiplier)

            # Calculate actual adjustment applied
            actual_adjustment = final_multiplier - self._confidence_multipliers[bucket]

            # Update stored multiplier
            self._confidence_multipliers[bucket] = final_multiplier

            # Calculate adjustment confidence based on sample size
            # More samples = higher confidence, capped at 0.95
            adjustment_confidence = min(0.95, sample_count / 100)

            adjustment = CalibrationAdjustment(
                target_bucket=bucket,
                adjustment=actual_adjustment,
                reason=f"{'Underperforming' if gap < 0 else 'Overperforming'} by {abs(gap):.1%} vs target {target:.1%}",
                confidence_multiplier=final_multiplier,
                sample_size=sample_count,
                days_of_data=days_count,
                adjustment_confidence=adjustment_confidence,
                applied=True,
            )
            adjustments.append(adjustment)
            self._adjustment_history.append(adjustment)

        # Update last update time if any adjustments applied
        if any(a.applied for a in adjustments):
            self._last_update = datetime.now()

        return adjustments

    def apply_calibration(self, base_confidence: float, bucket: str | None = None) -> float:
        """Apply calibration multiplier to confidence value.

        Args:
            base_confidence: Raw confidence value
            bucket: Confidence bucket (auto-detected if None)

        Returns:
            Calibrated confidence value
        """
        if bucket is None:
            bucket = self._confidence_to_bucket(base_confidence)

        multiplier = self._confidence_multipliers.get(bucket, 1.0)
        calibrated = base_confidence * multiplier

        # Clamp to valid range
        return max(0.0, min(0.99, calibrated))

    def get_calibration_status(self) -> dict[str, Any]:
        """Get current calibration status."""
        return {
            "confidence_multipliers": self._confidence_multipliers.copy(),
            "threshold_adjustments": self._threshold_adjustments.copy(),
            "interpretation": self._generate_interpretation(),
        }

    def reset_calibration(self) -> None:
        """Reset all calibration adjustments to defaults."""
        for bucket in self._confidence_multipliers:
            self._confidence_multipliers[bucket] = 1.0
            self._ema_values[bucket] = 1.0
        for threshold in self._threshold_adjustments:
            self._threshold_adjustments[threshold] = 0.0
        self._last_update = None
        self._adjustment_history.clear()

    def _confidence_to_bucket(self, confidence: float) -> str:
        """Map confidence value to bucket."""
        if confidence >= 0.80:
            return "elite"
        elif confidence >= 0.70:
            return "high"
        elif confidence >= 0.60:
            return "mid"
        return "low"

    def _clamp_multipliers(self) -> None:
        """Keep multipliers within ±10% bounds (0.9 to 1.1)."""
        for bucket in self._confidence_multipliers:
            self._confidence_multipliers[bucket] = self._clamp_multiplier(
                self._confidence_multipliers[bucket]
            )

    def get_adjustment_diagnostics(self, bucket: str | None = None) -> dict[str, Any]:
        """Get diagnostic information about adjustments.

        Args:
            bucket: Specific bucket or None for all

        Returns:
            Dict with adjustment history, sample sizes, confidence levels
        """
        history = self._adjustment_history
        if bucket:
            history = [h for h in history if h.target_bucket == bucket]

        if not history:
            return {
                "total_adjustments": 0,
                "last_adjustment": None,
                "average_confidence": 0.0,
                "current_multiplier": self._confidence_multipliers.get(bucket, 1.0) if bucket else self._confidence_multipliers,
            }

        recent = history[-10:]  # Last 10 adjustments
        return {
            "total_adjustments": len(history),
            "recent_adjustments": [
                {
                    "bucket": h.target_bucket,
                    "adjustment": h.adjustment,
                    "multiplier": h.confidence_multiplier,
                    "sample_size": h.sample_size,
                    "confidence": h.adjustment_confidence,
                    "applied": h.applied,
                }
                for h in recent
            ],
            "average_sample_size": sum(h.sample_size for h in history) / len(history),
            "average_confidence": sum(h.adjustment_confidence for h in history) / len(history),
            "current_multiplier": self._confidence_multipliers.get(bucket, 1.0) if bucket else self._confidence_multipliers,
            "last_update": self._last_update.isoformat() if self._last_update else None,
        }

    def _generate_interpretation(self) -> str:
        """Generate human-readable interpretation of calibration status."""
        parts = []

        for bucket, multiplier in self._confidence_multipliers.items():
            if multiplier < 0.95:
                parts.append(f"{bucket}: confidence reduced by {(1-multiplier):.0%} due to underperformance")
            elif multiplier > 1.05:
                parts.append(f"{bucket}: confidence increased by {(multiplier-1):.0%} due to overperformance")

        if not parts:
            return "Calibration neutral - all buckets performing as expected"

        return "; ".join(parts)

    def get_recommended_thresholds(self) -> dict[str, float]:
        """Get recommended confidence thresholds based on calibration."""
        # Base thresholds
        base = {
            "low": 0.55,
            "mid": 0.60,
            "high": 0.70,
            "elite": 0.80,
        }

        # Adjust based on multipliers
        # If multiplier is low (underperforming), raise the threshold
        recommended = {}
        for bucket, threshold in base.items():
            multiplier = self._confidence_multipliers.get(bucket, 1.0)
            if multiplier < 0.95:
                # Underperforming - raise threshold to be more selective
                recommended[bucket] = threshold / multiplier
            elif multiplier > 1.05:
                # Overperforming - lower threshold to capture more value
                recommended[bucket] = threshold / multiplier
            else:
                recommended[bucket] = threshold

        return recommended
