"""Outcome distribution modeling for player stats.

Models the statistical distribution of player outcomes including:
- Mean (expected value)
- Variance (spread)
- Skew (asymmetry)
- Ceiling/floor (bounded outcomes)

Phase 9: Scenario Simulation and Forward Validation
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class OutcomeDistribution:
    """Statistical distribution parameters for a player stat."""

    # Central tendency
    mean: float
    median: float | None = None
    mode: float | None = None

    # Spread
    std_dev: float = 0.0
    variance: float = 0.0
    iqr: float = 0.0  # Interquartile range

    # Shape
    skewness: float = 0.0  # Negative = left skew, Positive = right skew
    kurtosis: float = 0.0  # Tail heaviness

    # Bounds
    floor: float | None = None  # Minimum possible value
    ceiling: float | None = None  # Maximum possible value

    # Percentiles
    p10: float | None = None
    p90: float | None = None

    def __post_init__(self) -> None:
        """Compute derived statistics."""
        if self.variance == 0.0 and self.std_dev > 0:
            self.variance = self.std_dev ** 2
        if self.std_dev == 0.0 and self.variance > 0:
            self.std_dev = np.sqrt(self.variance)

    @property
    def coefficient_of_variation(self) -> float:
        """Coefficient of variation (std/mean)."""
        return self.std_dev / self.mean if self.mean > 0 else 0.0

    @property
    def is_high_variance(self) -> bool:
        """Check if this is a high variance distribution."""
        return self.coefficient_of_variation > 0.20

    @property
    def is_skewed(self) -> bool:
        """Check if distribution is significantly skewed."""
        return abs(self.skewness) > 0.5

    @property
    def is_bounded(self) -> bool:
        """Check if distribution has bounds."""
        return self.floor is not None or self.ceiling is not None

    def get_confidence_interval(self, level: float = 0.90) -> tuple[float, float]:
        """Get confidence interval for the mean.

        Args:
            level: Confidence level (0.90 = 90%)

        Returns:
            (lower_bound, upper_bound)
        """
        z_score = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}.get(level, 1.645)
        margin = z_score * self.std_dev
        return (self.mean - margin, self.mean + margin)

    def probability_above(self, threshold: float) -> float:
        """Estimate probability of outcome above threshold using normal approximation.

        Args:
            threshold: Value to compare against

        Returns:
            Probability (0-1)
        """
        if self.std_dev == 0:
            return 1.0 if self.mean > threshold else 0.0

        z_score = (threshold - self.mean) / self.std_dev
        # Approximate normal CDF
        return 1.0 - 0.5 * (1 + np.sign(z_score) * np.sqrt(1 - np.exp(-2 * z_score ** 2 / np.pi)))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "central_tendency": {
                "mean": round(self.mean, 2),
                "median": round(self.median, 2) if self.median else None,
                "mode": round(self.mode, 2) if self.mode else None,
            },
            "spread": {
                "std_dev": round(self.std_dev, 2),
                "variance": round(self.variance, 2),
                "iqr": round(self.iqr, 2),
                "cv": round(self.coefficient_of_variation, 3),
            },
            "shape": {
                "skewness": round(self.skewness, 3),
                "kurtosis": round(self.kurtosis, 3),
                "is_skewed": self.is_skewed,
            },
            "bounds": {
                "floor": self.floor,
                "ceiling": self.ceiling,
                "is_bounded": self.is_bounded,
            },
            "percentiles": {
                "p10": self.p10,
                "p90": self.p90,
            },
        }


class StatDistributionModel:
    """Build distribution models for player statistics.

    Estimates mean, variance, skew, and bounds based on:
    - Player historical volatility
    - Stat type characteristics
    - Matchup factors
    - Game context
    """

    # Default variance by stat type (coefficient of variation)
    STAT_TYPE_CV = {
        "points": 0.18,
        "rebounds": 0.22,
        "assists": 0.25,
        "threes": 0.35,
        "steals": 0.40,
        "blocks": 0.45,
        "turnovers": 0.30,
        "pra": 0.15,  # Combined stats more stable
    }

    # Skew tendencies by stat type
    STAT_TYPE_SKEW = {
        "points": 0.1,      # Slightly right-skewed (blowup games)
        "rebounds": 0.0,    # More symmetric
        "assists": 0.2,     # Right-skewed (high assist games)
        "threes": 0.3,      # Highly right-skewed (3pt variance)
        "steals": 0.4,      # Very right-skewed
        "blocks": 0.4,      # Very right-skewed
        "turnovers": 0.2,   # Right-skewed
        "pra": 0.1,         # Slightly right-skewed
    }

    def __init__(self) -> None:
        """Initialize distribution model builder."""
        self.models: dict[str, OutcomeDistribution] = {}

    def build_distribution(
        self,
        player_name: str,
        stat_type: str,
        projection: float,
        historical_std: float | None = None,
        historical_games: int = 0,
        matchup_factor: float = 1.0,
        rest_days: int = 1,
        home_game: bool = True,
    ) -> OutcomeDistribution:
        """Build distribution model for a player stat.

        Args:
            player_name: Player name
            stat_type: Type of statistic
            projection: Mean projection
            historical_std: Historical standard deviation (if known)
            historical_games: Number of games in history
            matchup_factor: Matchup adjustment (1.0 = neutral)
            rest_days: Days of rest (1 = normal)
            home_game: Playing at home

        Returns:
            OutcomeDistribution with estimated parameters
        """
        # Get base variance from stat type
        base_cv = self.STAT_TYPE_CV.get(stat_type, 0.20)
        base_skew = self.STAT_TYPE_SKEW.get(stat_type, 0.0)

        # Adjust variance based on historical data if available
        if historical_std is not None and historical_games >= 10:
            historical_cv = historical_std / projection if projection > 0 else base_cv
            # Blend historical with base (more weight to historical with more games)
            weight = min(historical_games / 50, 0.8)  # Max 80% weight
            cv = historical_cv * weight + base_cv * (1 - weight)
        else:
            cv = base_cv

        # Adjust for rest (more rest = slightly less variance)
        if rest_days >= 2:
            cv *= 0.95

        # Adjust for home/road (home slightly less variance)
        if not home_game:
            cv *= 1.05

        # Compute standard deviation
        std_dev = projection * cv

        # Estimate bounds based on stat type
        floor, ceiling = self._estimate_bounds(stat_type, projection, std_dev)

        # Estimate percentiles
        z_10 = -1.28  # 10th percentile
        z_90 = 1.28   # 90th percentile
        p10 = projection + z_10 * std_dev
        p90 = projection + z_90 * std_dev

        # Apply bounds
        if floor is not None:
            p10 = max(p10, floor)
        if ceiling is not None:
            p90 = min(p90, ceiling)

        # Estimate median (adjust for skew)
        if base_skew > 0.3:
            median = projection * 0.95  # Right skew pulls median left
        elif base_skew < -0.3:
            median = projection * 1.05  # Left skew pulls median right
        else:
            median = projection

        distribution = OutcomeDistribution(
            mean=projection,
            median=median,
            std_dev=std_dev,
            variance=std_dev ** 2,
            skewness=base_skew,
            floor=floor,
            ceiling=ceiling,
            p10=p10,
            p90=p90,
        )

        # Store model
        key = f"{player_name}_{stat_type}"
        self.models[key] = distribution

        return distribution

    def _estimate_bounds(
        self,
        stat_type: str,
        projection: float,
        std_dev: float,
    ) -> tuple[float | None, float | None]:
        """Estimate floor and ceiling for a stat.

        Args:
            stat_type: Type of statistic
            projection: Mean projection
            std_dev: Standard deviation

        Returns:
            (floor, ceiling) tuple
        """
        # Most stats can't be negative
        floor = 0.0

        # Estimate ceiling based on stat type and projection
        # Ceiling is typically 2-3 standard deviations above mean
        ceiling_multiplier = {
            "points": 2.5,
            "rebounds": 2.8,
            "assists": 3.0,
            "threes": 4.0,
            "steals": 4.0,
            "blocks": 4.0,
            "turnovers": 3.5,
            "pra": 2.2,
        }.get(stat_type, 3.0)

        ceiling = projection + ceiling_multiplier * std_dev

        # Hard ceilings for counting stats
        hard_ceilings = {
            "steals": 8.0,
            "blocks": 8.0,
            "threes": 12.0,
        }

        if stat_type in hard_ceilings:
            ceiling = min(ceiling, hard_ceilings[stat_type])

        return (floor, ceiling)

    def compare_distributions(
        self,
        dist1: OutcomeDistribution,
        dist2: OutcomeDistribution,
    ) -> dict[str, Any]:
        """Compare two distributions.

        Args:
            dist1: First distribution
            dist2: Second distribution

        Returns:
            Comparison metrics
        """
        return {
            "mean_difference": dist1.mean - dist2.mean,
            "variance_ratio": dist1.variance / dist2.variance if dist2.variance > 0 else 1.0,
            "risk_comparison": (
                "higher" if dist1.coefficient_of_variation > dist2.coefficient_of_variation
                else "lower" if dist1.coefficient_of_variation < dist2.coefficient_of_variation
                else "equal"
            ),
            "skew_comparison": (
                "more_right" if dist1.skewness > dist2.skewness
                else "more_left" if dist1.skewness < dist2.skewness
                else "similar"
            ),
        }

    def get_volatility_rating(self, distribution: OutcomeDistribution) -> str:
        """Get human-readable volatility rating.

        Args:
            distribution: Distribution to rate

        Returns:
            Volatility rating string
        """
        cv = distribution.coefficient_of_variation

        if cv < 0.10:
            return "very_low"
        elif cv < 0.15:
            return "low"
        elif cv < 0.20:
            return "moderate"
        elif cv < 0.30:
            return "high"
        else:
            return "very_high"
