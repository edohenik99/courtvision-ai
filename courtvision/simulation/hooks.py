"""Integration hooks for Phase 9 simulation system.

Provides optional validation layer that can be applied after scoring
to filter plays before final board construction.

Phase 9: Scenario Simulation and Forward Validation
"""

from __future__ import annotations

from typing import Any

from courtvision.simulation.distributions import StatDistributionModel
from courtvision.simulation.ev_calculation import EVCalculator, EVResult
from courtvision.simulation.robustness import RobustnessTester, RobustnessReport
from courtvision.simulation.simulation_engine import SimulationEngine, SimulationSummary
from courtvision.simulation.validation import BoardValidator, ValidationResult


class SimulationHooks:
    """Hooks for integrating simulation validation into board building.

    Provides optional validation layer that can be applied after scoring
    to ensure only robust, high-EV plays make it to final boards.

    Usage:
        hooks = SimulationHooks()

        # After scoring, before board building:
        validated_plays = hooks.validate_candidates(
            candidates=candidates,
            odds_map=odds_map,
            enable_robustness=True,
        )

        # Build board from validated plays only
        board = build_operator_boards(validated_plays, ...)
    """

    def __init__(
        self,
        num_simulations: int = 2000,
        enable_validation: bool = True,
        min_ev: float = 0.03,
        min_hit_prob: float = 0.52,
        min_robustness: float = 0.6,
    ) -> None:
        """Initialize simulation hooks.

        Args:
            num_simulations: Number of Monte Carlo simulations per candidate
            enable_validation: Enable validation layer
            min_ev: Minimum EV threshold
            min_hit_prob: Minimum hit probability
            min_robustness: Minimum robustness score
        """
        self.enable_validation = enable_validation

        # Initialize components
        self.sim_engine = SimulationEngine(num_simulations=num_simulations)
        self.dist_model = StatDistributionModel()
        self.ev_calc = EVCalculator()
        self.robustness_tester = RobustnessTester(self.sim_engine)
        self.validator = BoardValidator(
            min_ev=min_ev,
            min_hit_prob=min_hit_prob,
            min_robustness=min_robustness,
        )

    def simulate_candidate(
        self,
        player_name: str,
        stat_type: str,
        line_value: float,
        projection: float,
        std_dev: float | None = None,
        american_odds: int = -110,
        historical_std: float | None = None,
        historical_games: int = 0,
    ) -> dict[str, Any]:
        """Run full simulation analysis for a candidate.

        Args:
            player_name: Player name
            stat_type: Stat type
            line_value: Betting line
            projection: Model projection
            std_dev: Standard deviation (computed if None)
            american_odds: American odds for the play
            historical_std: Historical volatility (optional)
            historical_games: Games in history (optional)

        Returns:
            Complete simulation results
        """
        # Build distribution model
        if std_dev is None:
            distribution = self.dist_model.build_distribution(
                player_name=player_name,
                stat_type=stat_type,
                projection=projection,
                historical_std=historical_std,
                historical_games=historical_games,
            )
            std_dev = distribution.std_dev

        # Run simulation
        sim_summary = self.sim_engine.simulate_player_stat(
            player_name=player_name,
            stat_type=stat_type,
            line_value=line_value,
            projection=projection,
            std_dev=std_dev,
        )

        # Calculate EV
        ev_result = self.ev_calc.calculate_ev(
            player_name=player_name,
            stat_type=stat_type,
            line_value=line_value,
            projection=projection,
            edge=(projection - line_value) / line_value if line_value > 0 else 0,
            hit_probability=sim_summary.hit_probability,
            american_odds=american_odds,
        )

        return {
            "player_name": player_name,
            "stat_type": stat_type,
            "line_value": line_value,
            "projection": projection,
            "simulation": sim_summary,
            "ev": ev_result,
            "distribution": {
                "std_dev": std_dev,
                "cv": std_dev / projection if projection > 0 else 0,
            },
        }

    def validate_candidates(
        self,
        candidates: list[dict],
        enable_robustness: bool = True,
        enable_ev_filter: bool = True,
    ) -> list[ValidationResult]:
        """Validate a list of candidates using simulation.

        Args:
            candidates: List of candidate dicts with keys:
                - player_name, stat_type, line_value, projection
                - american_odds (optional, default -110)
                - std_dev (optional, computed if not provided)
                - historical_std, historical_games (optional)
                - base_minutes, injury_boost (optional, for robustness)
            enable_robustness: Run robustness tests
            enable_ev_filter: Filter by EV

        Returns:
            List of validated plays (only approved plays)
        """
        if not self.enable_validation:
            # Return all as approved if validation disabled
            return [
                ValidationResult(
                    player_name=c["player_name"],
                    stat_type=c["stat_type"],
                    line_value=c["line_value"],
                    approved=True,
                    recommendation="play",
                )
                for c in candidates
            ]

        validated_plays: list[ValidationResult] = []

        for candidate in candidates:
            # Run simulation
            sim_result = self.simulate_candidate(
                player_name=candidate["player_name"],
                stat_type=candidate["stat_type"],
                line_value=candidate["line_value"],
                projection=candidate["projection"],
                std_dev=candidate.get("std_dev"),
                american_odds=candidate.get("american_odds", -110),
                historical_std=candidate.get("historical_std"),
                historical_games=candidate.get("historical_games", 0),
            )

            # Run robustness test if enabled
            robustness_report = None
            if enable_robustness:
                robustness_report = self.robustness_tester.run_full_robustness_test(
                    player_name=candidate["player_name"],
                    stat_type=candidate["stat_type"],
                    line_value=candidate["line_value"],
                    projection=candidate["projection"],
                    std_dev=sim_result["distribution"]["std_dev"],
                    hit_probability=sim_result["simulation"].hit_probability,
                    ev=sim_result["ev"].expected_value,
                    base_minutes=candidate.get("base_minutes", 32),
                    injury_boost=candidate.get("injury_boost", 0.0),
                )

            # Validate
            validation = self.validator.validate_play(
                player_name=candidate["player_name"],
                stat_type=candidate["stat_type"],
                line_value=candidate["line_value"],
                simulation_summary=sim_result["simulation"],
                ev_result=sim_result["ev"] if enable_ev_filter else None,
                robustness_report=robustness_report,
            )

            validated_plays.append(validation)

        # Return only approved plays, sorted by overall score
        approved = [p for p in validated_plays if p.approved]
        approved.sort(key=lambda p: p.overall_score, reverse=True)

        return approved

    def batch_simulate_with_validation(
        self,
        candidates: list[dict],
    ) -> dict[str, Any]:
        """Run full simulation and validation pipeline on candidates.

        Args:
            candidates: List of candidate dicts

        Returns:
            Complete results with approved/rejected plays and summary
        """
        # Run validation
        validated = self.validate_candidates(
            candidates=candidates,
            enable_robustness=True,
            enable_ev_filter=True,
        )

        # Get rejected plays
        all_validations = []
        for candidate in candidates:
            sim_result = self.simulate_candidate(
                player_name=candidate["player_name"],
                stat_type=candidate["stat_type"],
                line_value=candidate["line_value"],
                projection=candidate["projection"],
                std_dev=candidate.get("std_dev"),
                american_odds=candidate.get("american_odds", -110),
            )

            validation = self.validator.validate_play(
                player_name=candidate["player_name"],
                stat_type=candidate["stat_type"],
                line_value=candidate["line_value"],
                simulation_summary=sim_result["simulation"],
                ev_result=sim_result["ev"],
            )
            all_validations.append(validation)

        approved = [v for v in all_validations if v.approved]
        rejected = [v for v in all_validations if not v.approved]

        # Build summary
        summary = {
            "total_candidates": len(candidates),
            "approved_count": len(approved),
            "rejected_count": len(rejected),
            "approval_rate": round(len(approved) / len(candidates), 3) if candidates else 0,
            "avg_scores": {
                "ev": round(sum(v.ev_score for v in all_validations) / len(all_validations), 3) if all_validations else 0,
                "robustness": round(sum(v.robustness_score for v in all_validations) / len(all_validations), 3) if all_validations else 0,
                "overall": round(sum(v.overall_score for v in all_validations) / len(all_validations), 3) if all_validations else 0,
            },
            "approved_plays": [p.to_dict() for p in approved],
            "rejected_plays": [p.to_dict() for p in rejected],
        }

        return summary

    def get_top_ev_plays(
        self,
        candidates: list[dict],
        top_n: int = 10,
        min_ev: float = 0.05,
    ) -> list[EVResult]:
        """Get top plays by EV.

        Args:
            candidates: List of candidate dicts
            top_n: Number of top plays to return
            min_ev: Minimum EV threshold

        Returns:
            List of top EV results
        """
        ev_results = []

        for candidate in candidates:
            sim_result = self.simulate_candidate(
                player_name=candidate["player_name"],
                stat_type=candidate["stat_type"],
                line_value=candidate["line_value"],
                projection=candidate["projection"],
                std_dev=candidate.get("std_dev"),
                american_odds=candidate.get("american_odds", -110),
            )
            ev_results.append(sim_result["ev"])

        return self.ev_calc.find_best_bets(ev_results, top_n=top_n, min_ev=min_ev)

    def get_robust_plays(
        self,
        candidates: list[dict],
        min_robustness_score: float = 0.75,
    ) -> list[RobustnessReport]:
        """Get plays that pass robustness tests.

        Args:
            candidates: List of candidate dicts
            min_robustness_score: Minimum robustness score

        Returns:
            List of robust plays
        """
        robust_reports = []

        for candidate in candidates:
            sim_result = self.simulate_candidate(
                player_name=candidate["player_name"],
                stat_type=candidate["stat_type"],
                line_value=candidate["line_value"],
                projection=candidate["projection"],
                std_dev=candidate.get("std_dev"),
                american_odds=candidate.get("american_odds", -110),
            )

            report = self.robustness_tester.run_full_robustness_test(
                player_name=candidate["player_name"],
                stat_type=candidate["stat_type"],
                line_value=candidate["line_value"],
                projection=candidate["projection"],
                std_dev=sim_result["distribution"]["std_dev"],
                hit_probability=sim_result["simulation"].hit_probability,
                ev=sim_result["ev"].expected_value,
                base_minutes=candidate.get("base_minutes", 32),
                injury_boost=candidate.get("injury_boost", 0.0),
            )

            survival_rate = report.scenarios_survived / report.total_scenarios if report.total_scenarios > 0 else 0
            if survival_rate >= min_robustness_score:
                robust_reports.append(report)

        # Sort by survival rate
        robust_reports.sort(
            key=lambda r: r.scenarios_survived / r.total_scenarios if r.total_scenarios > 0 else 0,
            reverse=True,
        )

        return robust_reports

    def get_high_confidence_plays(
        self,
        candidates: list[dict],
        min_confidence: float = 0.70,
    ) -> list[ValidationResult]:
        """Get plays with high confidence scores.

        Args:
            candidates: List of candidate dicts
            min_confidence: Minimum confidence score

        Returns:
            List of high confidence plays
        """
        high_confidence = []

        for candidate in candidates:
            sim_result = self.simulate_candidate(
                player_name=candidate["player_name"],
                stat_type=candidate["stat_type"],
                line_value=candidate["line_value"],
                projection=candidate["projection"],
                std_dev=candidate.get("std_dev"),
                american_odds=candidate.get("american_odds", -110),
            )

            validation = self.validator.validate_play(
                player_name=candidate["player_name"],
                stat_type=candidate["stat_type"],
                line_value=candidate["line_value"],
                simulation_summary=sim_result["simulation"],
                ev_result=sim_result["ev"],
            )

            if validation.confidence_score >= min_confidence and validation.approved:
                high_confidence.append(validation)

        # Sort by confidence
        high_confidence.sort(key=lambda p: p.confidence_score, reverse=True)

        return high_confidence

    def filter_by_variance(
        self,
        candidates: list[dict],
        max_cv: float = 0.25,
    ) -> list[dict]:
        """Filter candidates by coefficient of variation.

        Args:
            candidates: List of candidate dicts
            max_cv: Maximum coefficient of variation

        Returns:
            Filtered candidates
        """
        filtered = []

        for candidate in candidates:
            sim_result = self.simulate_candidate(
                player_name=candidate["player_name"],
                stat_type=candidate["stat_type"],
                line_value=candidate["line_value"],
                projection=candidate["projection"],
                std_dev=candidate.get("std_dev"),
            )

            cv = sim_result["distribution"]["cv"]
            if cv <= max_cv:
                filtered.append(candidate)

        return filtered

    def get_validation_config(self) -> dict[str, Any]:
        """Get current validation configuration."""
        return {
            "enabled": self.enable_validation,
            "simulations": self.sim_engine.num_simulations,
            "thresholds": {
                "min_ev": self.validator.min_ev,
                "min_hit_prob": self.validator.min_hit_prob,
                "min_robustness": self.validator.min_robustness,
                "max_cv": self.validator.max_cv,
                "min_confidence": self.validator.min_confidence,
            },
        }

    def update_thresholds(
        self,
        min_ev: float | None = None,
        min_hit_prob: float | None = None,
        min_robustness: float | None = None,
        max_cv: float | None = None,
        min_confidence: float | None = None,
    ) -> None:
        """Update validation thresholds."""
        if min_ev is not None:
            self.validator.min_ev = min_ev
        if min_hit_prob is not None:
            self.validator.min_hit_prob = min_hit_prob
        if min_robustness is not None:
            self.validator.min_robustness = min_robustness
        if max_cv is not None:
            self.validator.max_cv = max_cv
        if min_confidence is not None:
            self.validator.min_confidence = min_confidence


def create_simulation_enabled_board_builder(
    base_minutes: float = 32.0,
    num_simulations: int = 2000,
    min_ev: float = 0.03,
) -> SimulationHooks:
    """Factory function to create simulation-enabled board builder hooks.

    Args:
        base_minutes: Default minutes for robustness testing
        num_simulations: Number of Monte Carlo simulations
        min_ev: Minimum EV threshold

    Returns:
        Configured SimulationHooks instance
    """
    return SimulationHooks(
        num_simulations=num_simulations,
        enable_validation=True,
        min_ev=min_ev,
    )
