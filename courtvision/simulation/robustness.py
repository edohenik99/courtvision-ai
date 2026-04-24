"""Robustness testing for sensitivity analysis.

Tests predictions under different assumptions:
- Minutes changes (what if player plays more/less?)
- Injury uncertainty (what if injury impact is different?)
- Variance spikes (what if game is high/low variance?)

Phase 9: Scenario Simulation and Forward Validation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from courtvision.simulation.simulation_engine import SimulationEngine, SimulationSummary


@dataclass
class SensitivityResult:
    """Result of a sensitivity test."""

    scenario_name: str
    parameter_changed: str
    original_value: float
    new_value: float

    # Impact on hit probability
    original_hit_prob: float
    new_hit_prob: float
    hit_prob_delta: float

    # Impact on EV
    original_ev: float
    new_ev: float
    ev_delta: float

    # Robustness assessment
    still_viable: bool  # Does play remain positive EV?
    robustness_score: float  # 0-1 (1 = very robust)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "scenario": self.scenario_name,
            "parameter": self.parameter_changed,
            "values": {
                "original": self.original_value,
                "new": self.new_value,
                "change_pct": round((self.new_value - self.original_value) / self.original_value * 100, 1) if self.original_value != 0 else 0,
            },
            "hit_probability": {
                "original": round(self.original_hit_prob, 3),
                "new": round(self.new_hit_prob, 3),
                "delta": round(self.hit_prob_delta, 3),
            },
            "ev": {
                "original": round(self.original_ev, 3),
                "new": round(self.new_ev, 3),
                "delta": round(self.ev_delta, 3),
            },
            "robustness": {
                "still_viable": self.still_viable,
                "score": round(self.robustness_score, 3),
            },
        }


@dataclass
class RobustnessReport:
    """Complete robustness analysis for a play."""

    player_name: str
    stat_type: str
    line_value: float

    # Baseline results
    baseline_hit_prob: float
    baseline_ev: float

    # Sensitivity tests
    sensitivity_tests: list[SensitivityResult] = field(default_factory=list)

    # Overall assessment
    robustness_rating: str = ""  # very_robust, robust, fragile, very_fragile
    passes_all_tests: bool = False

    # Summary statistics
    max_hit_prob_drop: float = 0.0
    max_ev_drop: float = 0.0
    scenarios_survived: int = 0
    total_scenarios: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "play": {
                "player": self.player_name,
                "stat": self.stat_type,
                "line": self.line_value,
            },
            "baseline": {
                "hit_prob": round(self.baseline_hit_prob, 3),
                "ev": round(self.baseline_ev, 3),
            },
            "robustness": {
                "rating": self.robustness_rating,
                "passes_all": self.passes_all_tests,
                "scenarios_survived": f"{self.scenarios_survived}/{self.total_scenarios}",
            },
            "sensitivity": {
                "max_hit_prob_drop": round(self.max_hit_prob_drop, 3),
                "max_ev_drop": round(self.max_ev_drop, 3),
                "tests": [t.to_dict() for t in self.sensitivity_tests],
            },
        }


class RobustnessTester:
    """Test robustness of predictions under different scenarios.

    Runs sensitivity analysis on key assumptions to identify
    which plays are robust vs fragile.
    """

    # Sensitivity test parameters
    MINUTES_SHIFTS = [-0.20, -0.10, 0.10, 0.20]  # +/- 10%, 20%
    INJURY_IMPACT_SHIFTS = [-0.05, 0.05]  # +/- 5%
    VARIANCE_MULTIPLIERS = [0.7, 1.3, 1.5]  # Lower/higher variance

    # Robustness thresholds
    ROBUSTNESS_VERY_HIGH = 0.90  # Maintains positive EV in 90%+ scenarios
    ROBUSTNESS_HIGH = 0.75       # Maintains positive EV in 75%+ scenarios
    ROBUSTNESS_MODERATE = 0.50   # Maintains positive EV in 50%+ scenarios

    def __init__(self, simulation_engine: SimulationEngine | None = None) -> None:
        """Initialize robustness tester.

        Args:
            simulation_engine: Simulation engine to use (creates default if None)
        """
        self.sim_engine = simulation_engine or SimulationEngine(num_simulations=1000)

    def test_minutes_sensitivity(
        self,
        player_name: str,
        stat_type: str,
        line_value: float,
        projection: float,
        std_dev: float,
        hit_probability: float,
        ev: float,
        base_minutes: float,
        minutes_per_stat: float = 0.8,  # How much stat scales with minutes
    ) -> list[SensitivityResult]:
        """Test sensitivity to minutes changes.

        Args:
            player_name: Player name
            stat_type: Stat type
            line_value: Betting line
            projection: Base projection
            std_dev: Standard deviation
            hit_probability: Baseline hit probability
            ev: Baseline EV
            base_minutes: Baseline minutes projection
            minutes_per_stat: Scaling factor (stat change per minute change)

        Returns:
            List of sensitivity results
        """
        results = []

        for shift in self.MINUTES_SHIFTS:
            new_minutes = base_minutes * (1 + shift)
            minutes_delta = new_minutes - base_minutes

            # Adjust projection based on minutes change
            stat_adjustment = minutes_delta * minutes_per_stat
            new_projection = projection + stat_adjustment

            # Run new simulation
            summary = self.sim_engine.simulate_player_stat(
                player_name=player_name,
                stat_type=stat_type,
                line_value=line_value,
                projection=new_projection,
                std_dev=std_dev,
            )

            new_hit_prob = summary.hit_probability
            # Simple EV estimate (using same odds)
            new_ev = ev * (new_hit_prob / hit_probability) if hit_probability > 0 else 0

            result = SensitivityResult(
                scenario_name=f"minutes_{shift:+.0%}",
                parameter_changed="minutes",
                original_value=base_minutes,
                new_value=new_minutes,
                original_hit_prob=hit_probability,
                new_hit_prob=new_hit_prob,
                hit_prob_delta=new_hit_prob - hit_probability,
                original_ev=ev,
                new_ev=new_ev,
                ev_delta=new_ev - ev,
                still_viable=new_ev > 0,
                robustness_score=1.0 if new_ev > 0 else max(0, new_ev + 0.1),
            )
            results.append(result)

        return results

    def test_injury_sensitivity(
        self,
        player_name: str,
        stat_type: str,
        line_value: float,
        projection: float,
        std_dev: float,
        hit_probability: float,
        ev: float,
        injury_boost: float,
    ) -> list[SensitivityResult]:
        """Test sensitivity to injury impact misestimation.

        Args:
            player_name: Player name
            stat_type: Stat type
            line_value: Betting line
            projection: Base projection
            std_dev: Standard deviation
            hit_probability: Baseline hit probability
            ev: Baseline EV
            injury_boost: Assumed injury boost (positive = boost, negative = penalty)

        Returns:
            List of sensitivity results
        """
        results = []

        for shift in self.INJURY_IMPACT_SHIFTS:
            adjusted_boost = injury_boost * (1 + shift)
            boost_delta = adjusted_boost - injury_boost

            # Adjust projection
            new_projection = projection + (boost_delta * projection)

            summary = self.sim_engine.simulate_player_stat(
                player_name=player_name,
                stat_type=stat_type,
                line_value=line_value,
                projection=new_projection,
                std_dev=std_dev,
            )

            new_hit_prob = summary.hit_probability
            new_ev = ev * (new_hit_prob / hit_probability) if hit_probability > 0 else 0

            result = SensitivityResult(
                scenario_name=f"injury_{shift:+.0%}",
                parameter_changed="injury_impact",
                original_value=injury_boost,
                new_value=adjusted_boost,
                original_hit_prob=hit_probability,
                new_hit_prob=new_hit_prob,
                hit_prob_delta=new_hit_prob - hit_probability,
                original_ev=ev,
                new_ev=new_ev,
                ev_delta=new_ev - ev,
                still_viable=new_ev > 0,
                robustness_score=1.0 if new_ev > 0 else max(0, new_ev + 0.1),
            )
            results.append(result)

        return results

    def test_variance_sensitivity(
        self,
        player_name: str,
        stat_type: str,
        line_value: float,
        projection: float,
        std_dev: float,
        hit_probability: float,
        ev: float,
    ) -> list[SensitivityResult]:
        """Test sensitivity to variance changes.

        Args:
            player_name: Player name
            stat_type: Stat type
            line_value: Betting line
            projection: Base projection
            std_dev: Standard deviation
            hit_probability: Baseline hit probability
            ev: Baseline EV

        Returns:
            List of sensitivity results
        """
        results = []

        for multiplier in self.VARIANCE_MULTIPLIERS:
            new_std_dev = std_dev * multiplier

            summary = self.sim_engine.simulate_player_stat(
                player_name=player_name,
                stat_type=stat_type,
                line_value=line_value,
                projection=projection,
                std_dev=new_std_dev,
            )

            new_hit_prob = summary.hit_probability
            new_ev = ev * (new_hit_prob / hit_probability) if hit_probability > 0 else 0

            result = SensitivityResult(
                scenario_name=f"variance_{multiplier:.1f}x",
                parameter_changed="variance",
                original_value=std_dev,
                new_value=new_std_dev,
                original_hit_prob=hit_probability,
                new_hit_prob=new_hit_prob,
                hit_prob_delta=new_hit_prob - hit_probability,
                original_ev=ev,
                new_ev=new_ev,
                ev_delta=new_ev - ev,
                still_viable=new_ev > 0,
                robustness_score=1.0 if new_ev > 0 else max(0, new_ev + 0.1),
            )
            results.append(result)

        return results

    def run_full_robustness_test(
        self,
        player_name: str,
        stat_type: str,
        line_value: float,
        projection: float,
        std_dev: float,
        hit_probability: float,
        ev: float,
        base_minutes: float,
        injury_boost: float = 0.0,
        minutes_per_stat: float = 0.8,
    ) -> RobustnessReport:
        """Run complete robustness test suite.

        Args:
            player_name: Player name
            stat_type: Stat type
            line_value: Betting line
            projection: Base projection
            std_dev: Standard deviation
            hit_probability: Baseline hit probability
            ev: Baseline EV
            base_minutes: Baseline minutes
            injury_boost: Assumed injury boost
            minutes_per_stat: Minutes scaling factor

        Returns:
            Complete robustness report
        """
        all_tests: list[SensitivityResult] = []

        # Minutes sensitivity
        all_tests.extend(self.test_minutes_sensitivity(
            player_name, stat_type, line_value, projection, std_dev,
            hit_probability, ev, base_minutes, minutes_per_stat,
        ))

        # Injury sensitivity
        all_tests.extend(self.test_injury_sensitivity(
            player_name, stat_type, line_value, projection, std_dev,
            hit_probability, ev, injury_boost,
        ))

        # Variance sensitivity
        all_tests.extend(self.test_variance_sensitivity(
            player_name, stat_type, line_value, projection, std_dev,
            hit_probability, ev,
        ))

        # Calculate summary statistics
        viable_count = sum(1 for t in all_tests if t.still_viable)
        total_count = len(all_tests)

        hit_prob_drops = [t.hit_prob_delta for t in all_tests if t.hit_prob_delta < 0]
        ev_drops = [t.ev_delta for t in all_tests if t.ev_delta < 0]

        max_hit_drop = abs(min(hit_prob_drops)) if hit_prob_drops else 0
        max_ev_drop = abs(min(ev_drops)) if ev_drops else 0

        # Determine robustness rating
        survival_rate = viable_count / total_count if total_count > 0 else 0

        if survival_rate >= self.ROBUSTNESS_VERY_HIGH:
            rating = "very_robust"
        elif survival_rate >= self.ROBUSTNESS_HIGH:
            rating = "robust"
        elif survival_rate >= self.ROBUSTNESS_MODERATE:
            rating = "moderate"
        elif survival_rate > 0:
            rating = "fragile"
        else:
            rating = "very_fragile"

        return RobustnessReport(
            player_name=player_name,
            stat_type=stat_type,
            line_value=line_value,
            baseline_hit_prob=hit_probability,
            baseline_ev=ev,
            sensitivity_tests=all_tests,
            robustness_rating=rating,
            passes_all_tests=viable_count == total_count,
            max_hit_prob_drop=max_hit_drop,
            max_ev_drop=max_ev_drop,
            scenarios_survived=viable_count,
            total_scenarios=total_count,
        )

    def batch_robustness_test(
        self,
        candidates: list[dict],
        min_robustness_score: float = 0.5,
    ) -> list[RobustnessReport]:
        """Run robustness tests on multiple candidates.

        Args:
            candidates: List of candidate dicts with required fields
            min_robustness_score: Minimum robustness to include

        Returns:
            List of robustness reports
        """
        reports = []

        for candidate in candidates:
            report = self.run_full_robustness_test(
                player_name=candidate["player_name"],
                stat_type=candidate["stat_type"],
                line_value=candidate["line_value"],
                projection=candidate["projection"],
                std_dev=candidate["std_dev"],
                hit_probability=candidate["hit_probability"],
                ev=candidate["ev"],
                base_minutes=candidate.get("base_minutes", 32),
                injury_boost=candidate.get("injury_boost", 0.0),
            )

            # Filter by robustness score
            survival_rate = report.scenarios_survived / report.total_scenarios if report.total_scenarios > 0 else 0
            if survival_rate >= min_robustness_score:
                reports.append(report)

        # Sort by robustness (most robust first)
        reports.sort(
            key=lambda r: r.scenarios_survived / r.total_scenarios if r.total_scenarios > 0 else 0,
            reverse=True,
        )

        return reports

    def get_fragile_plays(
        self,
        reports: list[RobustnessReport],
        max_survival_rate: float = 0.5,
    ) -> list[RobustnessReport]:
        """Identify plays that fail under stress tests.

        Args:
            reports: List of robustness reports
            max_survival_rate: Maximum survival rate to be considered fragile

        Returns:
            List of fragile plays
        """
        fragile = []
        for report in reports:
            survival_rate = report.scenarios_survived / report.total_scenarios if report.total_scenarios > 0 else 0
            if survival_rate <= max_survival_rate:
                fragile.append(report)
        return fragile
