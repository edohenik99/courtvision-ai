"""Operator configuration for system control.

Config-driven control surface for:
- Mode presets (conservative/balanced/aggressive)
- Daily limits
- Feature toggles
- Threshold adjustments

VALIDATE + CALIBRATE mode - Configuration and control only.

Task D: Add operator controls config layer
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ModePreset(str, Enum):
    """Mode preset options."""

    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


@dataclass
class ThresholdConfig:
    """Threshold configuration."""

    edge: float = 0.05
    confidence: float = 0.65
    ev: float = 0.03
    min_hit_probability: float = 0.52

    def apply_mode_modifier(self, mode: ModePreset) -> ThresholdConfig:
        """Create modified thresholds based on mode."""
        if mode == ModePreset.CONSERVATIVE:
            return ThresholdConfig(
                edge=self.edge * 1.3,
                confidence=min(0.95, self.confidence * 1.1),
                ev=self.ev * 1.5,
                min_hit_probability=min(0.60, self.min_hit_probability * 1.1),
            )
        elif mode == ModePreset.AGGRESSIVE:
            return ThresholdConfig(
                edge=self.edge * 0.7,
                confidence=self.confidence * 0.9,
                ev=self.ev * 0.5,
                min_hit_probability=self.min_hit_probability * 0.95,
            )
        else:  # balanced
            return self


@dataclass
class LimitConfig:
    """Limit configuration."""

    max_daily_plays: int = 10
    max_portfolio_exposure: float = 1.0  # 100% of unit size
    max_plays_per_game: int = 3
    max_plays_per_player: int = 2

    def apply_mode_modifier(self, mode: ModePreset) -> LimitConfig:
        """Create modified limits based on mode."""
        if mode == ModePreset.CONSERVATIVE:
            return LimitConfig(
                max_daily_plays=int(self.max_daily_plays * 0.6),
                max_portfolio_exposure=self.max_portfolio_exposure * 0.7,
                max_plays_per_game=max(1, self.max_plays_per_game - 1),
                max_plays_per_player=1,
            )
        elif mode == ModePreset.AGGRESSIVE:
            return LimitConfig(
                max_daily_plays=int(self.max_daily_plays * 1.5),
                max_portfolio_exposure=self.max_portfolio_exposure * 1.3,
                max_plays_per_game=self.max_plays_per_game + 1,
                max_plays_per_player=self.max_plays_per_player + 1,
            )
        else:  # balanced
            return self


@dataclass
class FeatureToggles:
    """Feature toggle configuration."""

    simulation_gate: bool = True
    market_adaptive_thresholds: bool = True
    sgp_builder: bool = False
    feedback_adjustments: bool = True
    portfolio_optimization: bool = True
    shadow_run_mode: bool = False


@dataclass(init=False)
class OperatorConfig:
    """Complete operator configuration.

    Config-driven control surface - not hardcoded.
    """

    # Mode
    mode: ModePreset = ModePreset.BALANCED

    # Base configurations (before mode modifiers)
    base_thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    base_limits: LimitConfig = field(default_factory=LimitConfig)
    features: FeatureToggles = field(default_factory=FeatureToggles)

    # Derived effective values (computed)
    effective_thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    effective_limits: LimitConfig = field(default_factory=LimitConfig)

    def __init__(
        self,
        mode: ModePreset = ModePreset.BALANCED,
        base_thresholds: ThresholdConfig | None = None,
        base_limits: LimitConfig | None = None,
        features: FeatureToggles | None = None,
        effective_thresholds: ThresholdConfig | None = None,
        effective_limits: LimitConfig | None = None,
        **flat_overrides: Any,
    ) -> None:
        """Support both nested config objects and flat override kwargs."""
        self.mode = mode if isinstance(mode, ModePreset) else ModePreset(str(mode))
        self.base_thresholds = base_thresholds or ThresholdConfig()
        self.base_limits = base_limits or LimitConfig()
        self.features = features or FeatureToggles()
        self.effective_thresholds = effective_thresholds or ThresholdConfig()
        self.effective_limits = effective_limits or LimitConfig()

        self._apply_flat_overrides(flat_overrides)
        self.__post_init__()

    def __post_init__(self) -> None:
        """Compute effective values after mode modifiers."""
        self._recompute_effective()

    def _apply_flat_overrides(self, overrides: dict[str, Any]) -> None:
        if not overrides:
            return

        prefix_map = {
            "base_thresholds__": self.base_thresholds,
            "base_limits__": self.base_limits,
            "features__": self.features,
            "effective_thresholds__": self.effective_thresholds,
            "effective_limits__": self.effective_limits,
        }

        for key, value in overrides.items():
            matched = False
            for prefix, target in prefix_map.items():
                if key.startswith(prefix):
                    setattr(target, key[len(prefix):], value)
                    matched = True
                    break
            if not matched:
                raise TypeError(
                    f"OperatorConfig.__init__() got an unexpected keyword argument '{key}'"
                )

    def _recompute_effective(self) -> None:
        """Recompute effective thresholds and limits."""
        self.effective_thresholds = self.base_thresholds.apply_mode_modifier(self.mode)
        self.effective_limits = self.base_limits.apply_mode_modifier(self.mode)

    def set_mode(self, mode: ModePreset) -> None:
        """Set mode and recompute effective values."""
        self.mode = mode
        self._recompute_effective()

    def get_effective_thresholds(self) -> dict[str, float]:
        """Get effective thresholds as dict."""
        return {
            "edge": self.effective_thresholds.edge,
            "confidence": self.effective_thresholds.confidence,
            "ev": self.effective_thresholds.ev,
            "min_hit_probability": self.effective_thresholds.min_hit_probability,
        }

    def get_effective_limits(self) -> dict[str, int | float]:
        """Get effective limits as dict."""
        return {
            "max_daily_plays": self.effective_limits.max_daily_plays,
            "max_portfolio_exposure": self.effective_limits.max_portfolio_exposure,
            "max_plays_per_game": self.effective_limits.max_plays_per_game,
            "max_plays_per_player": self.effective_limits.max_plays_per_player,
        }

    def should_gate_with_simulation(self) -> bool:
        """Check if simulation gating is enabled."""
        return self.features.simulation_gate

    def should_use_adaptive_thresholds(self) -> bool:
        """Check if market-adaptive thresholds are enabled."""
        return self.features.market_adaptive_thresholds

    def should_use_sgp_builder(self) -> bool:
        """Check if SGP builder is enabled."""
        return self.features.sgp_builder

    def should_apply_feedback(self) -> bool:
        """Check if feedback adjustments are enabled."""
        return self.features.feedback_adjustments

    def should_optimize_portfolio(self) -> bool:
        """Check if portfolio optimization is enabled."""
        return self.features.portfolio_optimization

    def is_shadow_run_mode(self) -> bool:
        """Check if running in shadow mode."""
        return self.features.shadow_run_mode

    def validate_candidate(
        self,
        edge: float,
        confidence: float,
        ev: float,
        hit_prob: float,
    ) -> tuple[bool, str]:
        """Validate candidate against effective thresholds.

        Returns:
            (is_valid, rejection_reason)
        """
        reasons = []

        if edge < self.effective_thresholds.edge:
            reasons.append(
                f"edge {edge:.3f} < {self.effective_thresholds.edge:.3f}"
            )

        if confidence < self.effective_thresholds.confidence:
            reasons.append(
                f"confidence {confidence:.3f} < {self.effective_thresholds.confidence:.3f}"
            )

        if ev < self.effective_thresholds.ev:
            reasons.append(
                f"ev {ev:.3f} < {self.effective_thresholds.ev:.3f}"
            )

        if hit_prob < self.effective_thresholds.min_hit_probability:
            reasons.append(
                f"hit_prob {hit_prob:.3f} < {self.effective_thresholds.min_hit_probability:.3f}"
            )

        if reasons:
            return False, "; ".join(reasons)

        return True, ""

    def check_limits(
        self,
        current_daily_plays: int,
        current_exposure: float,
        plays_in_game: int,
        plays_on_player: int,
    ) -> tuple[bool, str]:
        """Check if adding another play would exceed limits.

        Returns:
            (can_add, limit_reason)
        """
        if current_daily_plays >= self.effective_limits.max_daily_plays:
            return False, f"daily limit {self.effective_limits.max_daily_plays} reached"

        if current_exposure >= self.effective_limits.max_portfolio_exposure:
            return False, f"exposure limit {self.effective_limits.max_portfolio_exposure} reached"

        if plays_in_game >= self.effective_limits.max_plays_per_game:
            return False, f"per-game limit {self.effective_limits.max_plays_per_game} reached"

        if plays_on_player >= self.effective_limits.max_plays_per_player:
            return False, f"per-player limit {self.effective_limits.max_plays_per_player} reached"

        return True, ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "mode": self.mode.value,
            "base_thresholds": asdict(self.base_thresholds),
            "base_limits": asdict(self.base_limits),
            "features": asdict(self.features),
            "effective_thresholds": asdict(self.effective_thresholds),
            "effective_limits": asdict(self.effective_limits),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OperatorConfig:
        """Create from dictionary."""
        config = cls(
            mode=ModePreset(data.get("mode", "balanced")),
            base_thresholds=ThresholdConfig(**data.get("base_thresholds", {})),
            base_limits=LimitConfig(**data.get("base_limits", {})),
            features=FeatureToggles(**data.get("features", {})),
        )
        return config

    def save(self, filepath: str) -> None:
        """Save configuration to JSON file."""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, filepath: str) -> OperatorConfig:
        """Load configuration from JSON file."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


def create_conservative_mode(
    max_daily_plays: int = 5,
    edge_threshold: float = 0.07,
) -> OperatorConfig:
    """Factory for conservative mode configuration.

    Args:
        max_daily_plays: Maximum plays per day
        edge_threshold: Minimum edge threshold

    Returns:
        Conservative OperatorConfig
    """
    config = OperatorConfig(
        mode=ModePreset.CONSERVATIVE,
        base_thresholds=ThresholdConfig(
            edge=edge_threshold,
            confidence=0.70,
            ev=0.04,
            min_hit_probability=0.55,
        ),
        base_limits=LimitConfig(
            max_daily_plays=max_daily_plays,
            max_portfolio_exposure=0.7,
            max_plays_per_game=2,
            max_plays_per_player=1,
        ),
        features=FeatureToggles(
            simulation_gate=True,
            market_adaptive_thresholds=False,  # Fixed thresholds
            sgp_builder=False,
            feedback_adjustments=True,
            portfolio_optimization=True,
            shadow_run_mode=False,
        ),
    )
    return config


def create_balanced_mode(
    max_daily_plays: int = 10,
    edge_threshold: float = 0.05,
) -> OperatorConfig:
    """Factory for balanced mode configuration.

    Args:
        max_daily_plays: Maximum plays per day
        edge_threshold: Minimum edge threshold

    Returns:
        Balanced OperatorConfig
    """
    config = OperatorConfig(
        mode=ModePreset.BALANCED,
        base_thresholds=ThresholdConfig(
            edge=edge_threshold,
            confidence=0.65,
            ev=0.03,
            min_hit_probability=0.52,
        ),
        base_limits=LimitConfig(
            max_daily_plays=max_daily_plays,
            max_portfolio_exposure=1.0,
            max_plays_per_game=3,
            max_plays_per_player=2,
        ),
        features=FeatureToggles(
            simulation_gate=True,
            market_adaptive_thresholds=True,
            sgp_builder=False,
            feedback_adjustments=True,
            portfolio_optimization=True,
            shadow_run_mode=False,
        ),
    )
    return config


def create_aggressive_mode(
    max_daily_plays: int = 15,
    edge_threshold: float = 0.03,
) -> OperatorConfig:
    """Factory for aggressive mode configuration.

    Args:
        max_daily_plays: Maximum plays per day
        edge_threshold: Minimum edge threshold

    Returns:
        Aggressive OperatorConfig
    """
    config = OperatorConfig(
        mode=ModePreset.AGGRESSIVE,
        base_thresholds=ThresholdConfig(
            edge=edge_threshold,
            confidence=0.60,
            ev=0.015,
            min_hit_probability=0.50,
        ),
        base_limits=LimitConfig(
            max_daily_plays=max_daily_plays,
            max_portfolio_exposure=1.3,
            max_plays_per_game=4,
            max_plays_per_player=3,
        ),
        features=FeatureToggles(
            simulation_gate=False,  # Bypass simulation
            market_adaptive_thresholds=True,
            sgp_builder=True,
            feedback_adjustments=True,
            portfolio_optimization=True,
            shadow_run_mode=False,
        ),
    )
    return config


def create_shadow_mode() -> OperatorConfig:
    """Factory for shadow run mode configuration.

    Returns:
        Shadow run OperatorConfig
    """
    config = create_balanced_mode()
    config.features.shadow_run_mode = True
    config._recompute_effective()
    return config


def load_config(filepath: str) -> OperatorConfig:
    """Load configuration from file.

    Args:
        filepath: Path to config JSON file

    Returns:
        Loaded OperatorConfig
    """
    return OperatorConfig.load(filepath)


def save_config(config: OperatorConfig, filepath: str) -> None:
    """Save configuration to file.

    Args:
        config: Configuration to save
        filepath: Path to save to
    """
    config.save(filepath)
