"""Penalty tuning feedback mechanism.

Measures whether penalties are too harsh or too soft and auto-adjusts
penalty strength over time based on prediction outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PenaltyEffectivenessMetrics:
    """Metrics for a specific penalty type."""

    penalty_type: str
    total_applied: int = 0
    hits_after_penalty: int = 0
    misses_after_penalty: int = 0
    avg_penalty_strength: float = 0.0
    effectiveness_score: float = 0.0  # How well the penalty predicted outcomes


@dataclass
class PenaltyAdjustment:
    """Penalty strength adjustment recommendation with diagnostics."""

    penalty_type: str
    current_strength: float
    recommended_strength: float
    effectiveness_score: float
    reason: str
    sample_size: int = 0
    adjustment_confidence: float = 0.0
    applied: bool = False
    skipped_reason: str = ""


class PenaltyTuner:
    """Auto-tune penalty strengths with stability safeguards.

    Tracks how often penalized picks still hit vs miss to determine if:
    - Penalties are too harsh (good picks being penalized too much)
    - Penalties are too soft (bad picks not being penalized enough)

    Safeguards:
    - Minimum 30 picks or 7 days required
    - EMA smoothing for gradual adjustments
    - Adjustment capped at ±20%
    - Daily cooldown to prevent overfitting
    """

    # Penalty types to track
    PENALTY_TYPES = [
        "market_quality",
        "injury_volatility",
        "recent_form",
        "diversity_points_bias",
        "diversity_player_cluster",
        "diversity_game_exposure",
        "anti_double_count",
        "large_edge_dampening",
    ]

    # Adjustment bounds (±20%)
    MIN_STRENGTH = 0.8
    MAX_STRENGTH = 1.2
    ADJUSTMENT_RATE = 0.05  # Slower than other components

    # Minimum sample thresholds
    MIN_SAMPLES = 30
    MIN_DAYS = 7

    # EMA smoothing (30% new, 70% historical)
    EMA_ALPHA = 0.3

    # Daily cooldown
    COOLDOWN_HOURS = 24

    def __init__(self, performance_store: Any) -> None:
        """Initialize penalty tuner."""
        self.store = performance_store
        self._penalty_strengths: dict[str, float] = {
            p: 1.0 for p in self.PENALTY_TYPES
        }
        self._ema_values: dict[str, float] = {
            p: 1.0 for p in self.PENALTY_TYPES
        }
        self._effectiveness_history: dict[str, list[float]] = {
            p: [] for p in self.PENALTY_TYPES
        }
        # Cooldown tracking
        self._last_update: datetime | None = None
        # Adjustment history
        self._adjustment_history: list[PenaltyAdjustment] = []

    def record_penalty_application(
        self,
        penalty_type: str,
        penalty_strength: float,
        original_confidence: float,
        penalized_confidence: float,
        result: str,  # "hit" or "miss"
    ) -> None:
        """Record application of a penalty for effectiveness tracking.

        Args:
            penalty_type: Type of penalty applied
            penalty_strength: Amount of confidence reduction
            original_confidence: Confidence before penalty
            penalized_confidence: Confidence after penalty
            result: Whether the pick hit or missed
        """
        # Compute effectiveness
        # If penalized pick still hit, penalty may be too harsh
        # If penalized pick missed, penalty was appropriate
        effectiveness = 1.0 if result == "miss" else -1.0

        # Weight by penalty strength
        weighted_effectiveness = effectiveness * penalty_strength

        self._effectiveness_history[penalty_type].append(weighted_effectiveness)

        # Keep history bounded
        if len(self._effectiveness_history[penalty_type]) > 100:
            self._effectiveness_history[penalty_type] = self._effectiveness_history[penalty_type][-50:]

    def analyze_penalty_effectiveness(
        self,
        window_days: int = 30,
    ) -> dict[str, PenaltyEffectivenessMetrics]:
        """Analyze effectiveness of each penalty type.

        Returns metrics for each penalty type.
        """
        metrics = {}

        for penalty_type in self.PENALTY_TYPES:
            history = self._effectiveness_history[penalty_type]

            if not history:
                metrics[penalty_type] = PenaltyEffectivenessMetrics(
                    penalty_type=penalty_type,
                    total_applied=0,
                )
                continue

            # Compute average effectiveness
            avg_effectiveness = sum(history) / len(history)

            # Count applications in recent window
            recent_count = min(len(history), 30)  # Last 30 applications

            metrics[penalty_type] = PenaltyEffectivenessMetrics(
                penalty_type=penalty_type,
                total_applied=len(history),
                avg_penalty_strength=sum(history) / len(history),
                effectiveness_score=avg_effectiveness,
            )

        return metrics

    def update_penalty_strengths(self, window_days: int = 30) -> list[PenaltyAdjustment]:
        """Update penalty strengths with stability safeguards.

        Safeguards:
        1. Cooldown check (max 1/day)
        2. Sample threshold (30 penalty applications)
        3. EMA smoothing
        4. Adjustment caps (±20%)

        Returns list of adjustments with diagnostics.
        """
        # Check cooldown
        can_update, cooldown_reason = self._check_cooldown()
        if not can_update:
            return [PenaltyAdjustment(
                penalty_type="all",
                current_strength=1.0,
                recommended_strength=1.0,
                effectiveness_score=0.0,
                reason=cooldown_reason,
                applied=False,
                skipped_reason=cooldown_reason,
            )]

        effectiveness = self.analyze_penalty_effectiveness(window_days)
        adjustments = []

        for penalty_type, metrics in effectiveness.items():
            # Check sample threshold
            sufficient, sample_count = self._check_sample_threshold(penalty_type)

            if not sufficient:
                adjustments.append(PenaltyAdjustment(
                    penalty_type=penalty_type,
                    current_strength=self._penalty_strengths[penalty_type],
                    recommended_strength=self._penalty_strengths[penalty_type],
                    effectiveness_score=metrics.effectiveness_score,
                    reason="Insufficient data for adjustment",
                    sample_size=sample_count,
                    applied=False,
                    skipped_reason=f"Need {self.MIN_SAMPLES} applications, got {sample_count}",
                ))
                continue

            current_strength = self._penalty_strengths[penalty_type]
            new_strength = self._compute_strength_adjustment(
                penalty_type, metrics, current_strength
            )

            if abs(new_strength - current_strength) > 0.01:
                self._penalty_strengths[penalty_type] = new_strength

                # Calculate adjustment confidence
                adj_confidence = min(0.95, sample_count / 100)

                adjustment = PenaltyAdjustment(
                    penalty_type=penalty_type,
                    current_strength=current_strength,
                    recommended_strength=new_strength,
                    effectiveness_score=metrics.effectiveness_score,
                    reason=self._generate_reason(metrics, new_strength),
                    sample_size=sample_count,
                    adjustment_confidence=adj_confidence,
                    applied=True,
                )
                adjustments.append(adjustment)
                self._adjustment_history.append(adjustment)

        # Update cooldown if any adjustments applied
        if any(a.applied for a in adjustments):
            self._last_update = datetime.now()

        return adjustments

    def apply_penalty_strength(
        self,
        penalty_type: str,
        base_penalty: float,
    ) -> float:
        """Apply tuned strength multiplier to a penalty.

        Args:
            penalty_type: Type of penalty
            base_penalty: Base penalty amount

        Returns:
            Adjusted penalty amount
        """
        strength = self._penalty_strengths.get(penalty_type, 1.0)
        return base_penalty * strength

    def get_penalty_status(self) -> dict[str, Any]:
        """Get current penalty tuning status."""
        effectiveness = self.analyze_penalty_effectiveness()

        return {
            "current_strengths": self._penalty_strengths.copy(),
            "effectiveness": {
                p: {
                    "score": e.effectiveness_score,
                    "total_applied": e.total_applied,
                }
                for p, e in effectiveness.items()
            },
            "interpretation": self._generate_interpretation(effectiveness),
        }

    def reset_penalties(self) -> None:
        """Reset all penalty strengths to default (1.0)."""
        for penalty_type in self._penalty_strengths:
            self._penalty_strengths[penalty_type] = 1.0
            self._ema_values[penalty_type] = 1.0
        for history in self._effectiveness_history.values():
            history.clear()
        self._last_update = None
        self._adjustment_history.clear()

    def get_adjustment_diagnostics(self, penalty_type: str | None = None) -> dict[str, Any]:
        """Get diagnostic information about penalty adjustments."""
        history = self._adjustment_history
        if penalty_type:
            history = [h for h in history if h.penalty_type == penalty_type]

        if not history:
            return {
                "total_adjustments": 0,
                "last_adjustment": None,
                "current_strength": self._penalty_strengths.get(penalty_type, 1.0) if penalty_type else self._penalty_strengths,
            }

        recent = history[-10:]
        return {
            "total_adjustments": len(history),
            "recent_adjustments": [
                {
                    "type": h.penalty_type,
                    "old_strength": h.current_strength,
                    "new_strength": h.recommended_strength,
                    "sample_size": h.sample_size,
                    "confidence": h.adjustment_confidence,
                    "applied": h.applied,
                }
                for h in recent
            ],
            "average_sample_size": sum(h.sample_size for h in history) / len(history),
            "average_confidence": sum(h.adjustment_confidence for h in history) / len(history),
            "current_strength": self._penalty_strengths.get(penalty_type, 1.0) if penalty_type else self._penalty_strengths,
            "last_update": self._last_update.isoformat() if self._last_update else None,
        }

    def _check_cooldown(self) -> tuple[bool, str]:
        """Check if cooldown period has passed."""
        if self._last_update is None:
            return True, "First update"

        elapsed = datetime.now() - self._last_update
        if elapsed < timedelta(hours=self.COOLDOWN_HOURS):
            hours_remaining = (timedelta(hours=self.COOLDOWN_HOURS) - elapsed).total_seconds() / 3600
            return False, f"Cooldown active: {hours_remaining:.1f} hours remaining"

        return True, "Cooldown clear"

    def _check_sample_threshold(self, penalty_type: str) -> tuple[bool, int]:
        """Check if sample threshold is met for a penalty type."""
        history = self._effectiveness_history.get(penalty_type, [])
        sample_count = len(history)
        sufficient = sample_count >= self.MIN_SAMPLES
        return sufficient, sample_count

    def _apply_ema(self, penalty_type: str, raw_value: float) -> float:
        """Apply exponential moving average smoothing."""
        current_ema = self._ema_values.get(penalty_type, 1.0)
        new_ema = self.EMA_ALPHA * raw_value + (1 - self.EMA_ALPHA) * current_ema
        self._ema_values[penalty_type] = new_ema
        return new_ema

    def _compute_strength_adjustment(
        self,
        penalty_type: str,
        metrics: PenaltyEffectivenessMetrics,
        current_strength: float,
    ) -> float:
        """Compute new strength for a penalty type with safeguards."""
        effectiveness = metrics.effectiveness_score

        # Effectiveness interpretation:
        # Positive = penalized picks mostly missed (penalty is appropriate or soft)
        # Negative = penalized picks mostly hit (penalty is too harsh)

        if effectiveness > 0.2:
            # Penalty is working well - increase slightly
            adjustment = self.ADJUSTMENT_RATE
        elif effectiveness > -0.1:
            # Penalty is roughly neutral - maintain
            adjustment = 0.0
        else:
            # Penalty is too harsh - reduce strength (faster reduction)
            adjustment = -self.ADJUSTMENT_RATE * 1.5

        # Apply EMA smoothing
        raw_new_strength = current_strength + adjustment
        smoothed_strength = self._apply_ema(penalty_type, raw_new_strength)

        # Clamp to bounds (±20%)
        return max(self.MIN_STRENGTH, min(self.MAX_STRENGTH, smoothed_strength))

    def _generate_reason(
        self,
        metrics: PenaltyEffectivenessMetrics,
        new_strength: float,
    ) -> str:
        """Generate human-readable reason for adjustment."""
        effectiveness = metrics.effectiveness_score

        if effectiveness > 0.2:
            return f"Penalty is effective ({effectiveness:.2f} score) - increasing strength"
        elif effectiveness > -0.1:
            return f"Penalty is neutral ({effectiveness:.2f} score) - maintaining strength"
        else:
            return f"Penalty may be too harsh ({effectiveness:.2f} score) - reducing strength"

    def _generate_interpretation(
        self,
        effectiveness: dict[str, PenaltyEffectivenessMetrics],
    ) -> str:
        """Generate overall interpretation of penalty tuning status."""
        parts = []

        for penalty_type, metrics in effectiveness.items():
            if metrics.total_applied < 5:
                continue

            strength = self._penalty_strengths[penalty_type]
            if strength < 0.9:
                parts.append(f"{penalty_type}: reduced to {strength:.1f}x (too harsh)")
            elif strength > 1.1:
                parts.append(f"{penalty_type}: increased to {strength:.1f}x (effective)")

        if not parts:
            return "All penalties at default strength - awaiting more data"

        return "; ".join(parts)

    def get_recommended_penalty_thresholds(self) -> dict[str, float]:
        """Get recommended thresholds for applying penalties."""
        base_thresholds = {
            "market_quality": 0.05,
            "injury_volatility": 0.10,
            "recent_form": 0.05,
            "diversity_points_bias": 0.03,
            "diversity_player_cluster": 0.04,
            "diversity_game_exposure": 0.02,
        }

        # Adjust thresholds based on strengths
        # If penalty is very strong, raise threshold to apply it less often
        recommended = {}
        for penalty_type, base in base_thresholds.items():
            strength = self._penalty_strengths.get(penalty_type, 1.0)
            if strength > 1.2:
                # Strong penalty - apply less frequently
                recommended[penalty_type] = base * 1.5
            elif strength < 0.8:
                # Weak penalty - apply more frequently
                recommended[penalty_type] = base * 0.7
            else:
                recommended[penalty_type] = base

        return recommended
