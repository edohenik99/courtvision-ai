"""Core simulation engine for stat outcome distributions.

Runs Monte Carlo simulations to estimate probability distributions
of player stat outcomes.

Phase 9: Scenario Simulation and Forward Validation
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

import numpy as np


@dataclass
class SimulationResult:
    """Results from a single simulation run."""

    simulated_value: float
    hit_line: bool
    margin_vs_line: float


@dataclass
class SimulationSummary:
    """Summary statistics from multiple simulations."""

    # Input parameters
    player_name: str
    stat_type: str
    line_value: float
    projection: float
    num_simulations: int

    # Hit probability
    hit_probability: float
    hit_count: int
    miss_count: int

    # Distribution statistics
    mean_outcome: float
    std_deviation: float
    min_outcome: float
    max_outcome: float
    median_outcome: float

    # Percentiles
    p10: float
    p25: float
    p75: float
    p90: float

    # Margins
    avg_margin_vs_line: float
    max_positive_margin: float
    max_negative_margin: float

    # Outcomes list (for histogram)
    all_outcomes: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "player_name": self.player_name,
            "stat_type": self.stat_type,
            "line_value": self.line_value,
            "projection": self.projection,
            "num_simulations": self.num_simulations,
            "hit_probability": round(self.hit_probability, 3),
            "statistics": {
                "mean": round(self.mean_outcome, 2),
                "std": round(self.std_deviation, 2),
                "min": round(self.min_outcome, 2),
                "max": round(self.max_outcome, 2),
                "median": round(self.median_outcome, 2),
                "p10": round(self.p10, 2),
                "p25": round(self.p25, 2),
                "p75": round(self.p75, 2),
                "p90": round(self.p90, 2),
            },
            "margins": {
                "avg": round(self.avg_margin_vs_line, 2),
                "max_positive": round(self.max_positive_margin, 2),
                "max_negative": round(self.max_negative_margin, 2),
            },
        }


class SimulationEngine:
    """Monte Carlo simulation engine for player stat outcomes.

    Runs 1000+ simulations per candidate to estimate probability
    distributions and hit probabilities.
    """

    DEFAULT_SIMULATIONS = 2000
    RANDOM_SEED = 42  # For reproducibility

    def __init__(self, num_simulations: int = DEFAULT_SIMULATIONS, seed: int | None = RANDOM_SEED) -> None:
        """Initialize simulation engine.

        Args:
            num_simulations: Number of Monte Carlo runs (default 2000)
            seed: Random seed for reproducibility (None for non-deterministic)
        """
        self.num_simulations = num_simulations
        self.seed = seed
        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)

    def simulate_player_stat(
        self,
        player_name: str,
        stat_type: str,
        line_value: float,
        projection: float,
        std_dev: float,
        skew: float = 0.0,
        floor: float | None = None,
        ceiling: float | None = None,
        distribution_type: str = "normal",
    ) -> SimulationSummary:
        """Run Monte Carlo simulation for a player stat.

        Args:
            player_name: Player name
            stat_type: Type of stat (points, rebounds, etc.)
            line_value: Betting line
            projection: Mean projection
            std_dev: Standard deviation of outcomes
            skew: Skewness of distribution (0 = symmetric)
            floor: Minimum possible value (None for no floor)
            ceiling: Maximum possible value (None for no ceiling)
            distribution_type: "normal", "skew_normal", "truncated_normal"

        Returns:
            SimulationSummary with statistics
        """
        outcomes = self._generate_outcomes(
            projection=projection,
            std_dev=std_dev,
            skew=skew,
            floor=floor,
            ceiling=ceiling,
            distribution_type=distribution_type,
            n=self.num_simulations,
        )

        # Calculate results
        results = [
            SimulationResult(
                simulated_value=v,
                hit_line=v > line_value,
                margin_vs_line=v - line_value,
            )
            for v in outcomes
        ]

        hit_count = sum(1 for r in results if r.hit_line)
        miss_count = len(results) - hit_count
        hit_probability = hit_count / len(results) if results else 0.0

        # Calculate statistics
        sorted_outcomes = sorted(outcomes)
        n = len(sorted_outcomes)

        return SimulationSummary(
            player_name=player_name,
            stat_type=stat_type,
            line_value=line_value,
            projection=projection,
            num_simulations=self.num_simulations,
            hit_probability=hit_probability,
            hit_count=hit_count,
            miss_count=miss_count,
            mean_outcome=np.mean(outcomes),
            std_deviation=np.std(outcomes),
            min_outcome=min(outcomes),
            max_outcome=max(outcomes),
            median_outcome=np.median(outcomes),
            p10=sorted_outcomes[int(n * 0.10)],
            p25=sorted_outcomes[int(n * 0.25)],
            p75=sorted_outcomes[int(n * 0.75)],
            p90=sorted_outcomes[int(n * 0.90)],
            avg_margin_vs_line=np.mean([r.margin_vs_line for r in results]),
            max_positive_margin=max([r.margin_vs_line for r in results]),
            max_negative_margin=min([r.margin_vs_line for r in results]),
            all_outcomes=outcomes,
        )

    def _generate_outcomes(
        self,
        projection: float,
        std_dev: float,
        skew: float,
        floor: float | None,
        ceiling: float | None,
        distribution_type: str,
        n: int,
    ) -> list[float]:
        """Generate random outcomes from specified distribution."""
        if distribution_type == "normal":
            outcomes = np.random.normal(projection, std_dev, n)

        elif distribution_type == "skew_normal":
            # Approximate skew normal using gamma-like transformation
            if skew != 0:
                # Use a simple skew approximation
                normal_samples = np.random.normal(0, 1, n)
                skewed = normal_samples + skew * (normal_samples ** 2 - 1)
                outcomes = projection + std_dev * (skewed - np.mean(skewed)) / np.std(skewed)
            else:
                outcomes = np.random.normal(projection, std_dev, n)

        elif distribution_type == "truncated_normal":
            # Rejection sampling for truncated normal
            outcomes = []
            attempts = 0
            max_attempts = n * 10

            while len(outcomes) < n and attempts < max_attempts:
                sample = np.random.normal(projection, std_dev)
                valid = True

                if floor is not None and sample < floor:
                    valid = False
                if ceiling is not None and sample > ceiling:
                    valid = False

                if valid:
                    outcomes.append(sample)
                attempts += 1

            # If we didn't get enough samples, fill with boundary values
            while len(outcomes) < n:
                if floor is not None:
                    outcomes.append(floor)
                elif ceiling is not None:
                    outcomes.append(ceiling)
                else:
                    outcomes.append(projection)

            outcomes = np.array(outcomes)

        else:
            outcomes = np.random.normal(projection, std_dev, n)

        # Apply floor/ceiling if not already handled
        if distribution_type != "truncated_normal":
            if floor is not None:
                outcomes = np.maximum(outcomes, floor)
            if ceiling is not None:
                outcomes = np.minimum(outcomes, ceiling)

        return outcomes.tolist()

    def batch_simulate(
        self,
        candidates: list[dict],
    ) -> list[SimulationSummary]:
        """Run simulations for multiple candidates.

        Args:
            candidates: List of dicts with keys:
                - player_name, stat_type, line_value, projection
                - std_dev, skew (optional), floor (optional), ceiling (optional)

        Returns:
            List of SimulationSummary objects
        """
        results = []
        for candidate in candidates:
            summary = self.simulate_player_stat(
                player_name=candidate["player_name"],
                stat_type=candidate["stat_type"],
                line_value=candidate["line_value"],
                projection=candidate["projection"],
                std_dev=candidate.get("std_dev", projection * 0.15),
                skew=candidate.get("skew", 0.0),
                floor=candidate.get("floor"),
                ceiling=candidate.get("ceiling"),
                distribution_type=candidate.get("distribution_type", "normal"),
            )
            results.append(summary)
        return results

    def get_hit_probability(
        self,
        projection: float,
        line_value: float,
        std_dev: float,
        skew: float = 0.0,
        floor: float | None = None,
        ceiling: float | None = None,
    ) -> float:
        """Quick hit probability estimate.

        Args:
            projection: Mean projection
            line_value: Betting line
            std_dev: Standard deviation
            skew: Skewness
            floor: Minimum value
            ceiling: Maximum value

        Returns:
            Hit probability (0-1)
        """
        summary = self.simulate_player_stat(
            player_name="quick",
            stat_type="quick",
            line_value=line_value,
            projection=projection,
            std_dev=std_dev,
            skew=skew,
            floor=floor,
            ceiling=ceiling,
        )
        return summary.hit_probability

    def estimate_variance_risk(
        self,
        projection: float,
        std_dev: float,
        line_value: float,
    ) -> dict:
        """Estimate risk metrics based on variance.

        Args:
            projection: Mean projection
            std_dev: Standard deviation
            line_value: Betting line

        Returns:
            Risk metrics dict
        """
        summary = self.simulate_player_stat(
            player_name="risk",
            stat_type="risk",
            line_value=line_value,
            projection=projection,
            std_dev=std_dev,
        )

        # Calculate coefficient of variation
        cv = std_dev / projection if projection > 0 else 0

        # Calculate probability of extreme outcomes
        extreme_low = sum(1 for o in summary.all_outcomes if o < line_value * 0.5) / len(summary.all_outcomes)
        extreme_high = sum(1 for o in summary.all_outcomes if o > line_value * 1.5) / len(summary.all_outcomes)

        return {
            "coefficient_of_variation": round(cv, 3),
            "std_deviation": round(std_dev, 2),
            "extreme_low_probability": round(extreme_low, 3),
            "extreme_high_probability": round(extreme_high, 3),
            "variance_risk_score": round((cv + extreme_low + extreme_high) / 3, 3),
        }
