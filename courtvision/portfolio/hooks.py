"""Integration hooks for Phase 10 portfolio optimization.

Provides integration with board builder for:
- Correlation detection after play selection
- Portfolio optimization before final board
- Risk control during board construction
- SGP building as optional layer

Phase 10: Correlation and Portfolio Optimization
"""

from __future__ import annotations

from typing import Any

from courtvision.portfolio.correlation import CorrelationDetector, CorrelationMatrix, PlayIdentity
from courtvision.portfolio.covariance import CovarianceModel
from courtvision.portfolio.optimizer import PortfolioOptimizer, PortfolioResult
from courtvision.portfolio.risk_control import ExposureLimits, RiskController, RiskViolation
from courtvision.portfolio.sgp_builder import SGPBuilder, SGPParlay


class PortfolioHooks:
    """Hooks for integrating portfolio optimization into board building.

    Usage:
        hooks = PortfolioHooks()

        # After selecting plays:
        hooks.analyze_correlations(selected_plays)

        # Before final board:
        optimal_portfolio = hooks.optimize_portfolio(selected_plays)

        # Or with SGPs:
        result = hooks.build_portfolio_with_sgps(selected_plays)
    """

    def __init__(
        self,
        enable_correlation: bool = True,
        enable_risk_control: bool = True,
        enable_optimization: bool = True,
        enable_sgp: bool = False,
        risk_limits: ExposureLimits | None = None,
    ) -> None:
        """Initialize portfolio hooks.

        Args:
            enable_correlation: Enable correlation detection
            enable_risk_control: Enable exposure limits
            enable_optimization: Enable portfolio optimization
            enable_sgp: Enable SGP building
            risk_limits: Risk limits configuration
        """
        self.enable_correlation = enable_correlation
        self.enable_risk_control = enable_risk_control
        self.enable_optimization = enable_optimization
        self.enable_sgp = enable_sgp

        # Initialize components
        self.correlation_detector = CorrelationDetector() if enable_correlation else None
        self.risk_controller = RiskController(risk_limits) if enable_risk_control else None
        self.optimizer = PortfolioOptimizer(risk_controller=risk_controller) if enable_optimization else None
        self.sgp_builder = SGPBuilder() if enable_sgp else None
        self.covariance_model = CovarianceModel()

        self._last_correlation_matrix: CorrelationMatrix | None = None
        self._last_portfolio_result: PortfolioResult | None = None

    def analyze_correlations(
        self,
        plays: list[dict],
    ) -> dict[str, Any]:
        """Analyze correlations between selected plays.

        Args:
            plays: List of play dicts with keys:
                - play_id, player_name, game_id, stat_type
                - line_value, projection

        Returns:
            Correlation analysis
        """
        if not self.enable_correlation or not self.correlation_detector:
            return {"enabled": False}

        # Add plays to detector
        for play in plays:
            identity = PlayIdentity(
                play_id=play["play_id"],
                player_name=play["player_name"],
                player_id=play.get("player_id"),
                stat_type=play["stat_type"],
                game_id=play["game_id"],
                team=play.get("team", ""),
                opponent=play.get("opponent", ""),
                line_value=play.get("line_value", 0),
                projection=play.get("projection", 0),
            )
            self.correlation_detector.add_play(identity)

        # Build correlation matrix
        play_ids = [p["play_id"] for p in plays]
        matrix = self.correlation_detector.build_correlation_matrix(play_ids)
        self._last_correlation_matrix = matrix

        # Find correlated groups
        groups = self.correlation_detector.find_correlated_groups(threshold=0.5)

        # Risk concentration
        concentration = self.correlation_detector.get_risk_concentration()

        return {
            "enabled": True,
            "plays_analyzed": len(plays),
            "matrix": matrix.to_dict(),
            "correlated_groups": groups,
            "high_correlations": [c.to_dict() for c in matrix.get_high_correlations(0.4)],
            "risk_concentration": concentration,
            "variance_multiplier": matrix.get_portfolio_variance_multiplier(),
        }

    def check_risk_limits(
        self,
        plays: list[dict],
    ) -> dict[str, Any]:
        """Check if plays satisfy risk limits.

        Args:
            plays: List of play dicts

        Returns:
            Risk status
        """
        if not self.enable_risk_control or not self.risk_controller:
            return {"enabled": False}

        self.risk_controller.reset()

        for play in plays:
            self.risk_controller.add_play(
                play_id=play["play_id"],
                player_name=play["player_name"],
                game_id=play["game_id"],
                stat_type=play["stat_type"],
                bet_size=play.get("bet_size", 1.0),
            )

        return self.risk_controller.get_status()

    def optimize_portfolio(
        self,
        plays: list[dict],
        target_size: int = 10,
        num_portfolios: int = 3,
    ) -> list[PortfolioResult]:
        """Optimize portfolio from available plays.

        Args:
            plays: List of play dicts with EV and variance data
            target_size: Target portfolio size
            num_portfolios: Number of portfolios to return

        Returns:
            List of optimized portfolios
        """
        if not self.enable_optimization or not self.optimizer:
            return []

        # Add plays to optimizer
        for play in plays:
            self.optimizer.add_play(
                play_id=play["play_id"],
                player_name=play["player_name"],
                game_id=play["game_id"],
                stat_type=play["stat_type"],
                ev=play.get("ev", 0),
                hit_probability=play.get("hit_probability", 0.5),
                odds=play.get("odds", 2.0),
                variance=play.get("variance", 0.1),
            )

        # Get correlation matrix
        correlation_matrix = None
        if self._last_correlation_matrix:
            play_ids = [p["play_id"] for p in plays]
            correlation_matrix = self._last_correlation_matrix

        # Optimize
        portfolios = self.optimizer.optimize(
            available_play_ids=[p["play_id"] for p in plays],
            correlation_matrix=correlation_matrix,
            target_size=target_size,
            num_portfolios=num_portfolios,
        )

        if portfolios:
            self._last_portfolio_result = portfolios[0]

        return portfolios

    def build_portfolio_with_sgps(
        self,
        plays: list[dict],
        target_size: int = 10,
    ) -> dict[str, Any]:
        """Build optimized portfolio including SGPs.

        Args:
            plays: List of play dicts
            target_size: Target total positions (singles + SGPs)

        Returns:
            Portfolio with SGPs included
        """
        result = {
            "singles": [],
            "sgps": [],
            "total_plays": 0,
            "analysis": {},
        }

        # First, analyze correlations
        if self.enable_correlation:
            correlation_analysis = self.analyze_correlations(plays)
            result["analysis"]["correlations"] = correlation_analysis

        # Find SGP opportunities
        if self.enable_sgp and self.sgp_builder:
            correlation_matrix = self._last_correlation_matrix

            sgps = self.sgp_builder.find_best_sgps(
                plays=plays,
                correlation_matrix=correlation_matrix,
                max_sgps=3,
                min_ev=-0.05,
            )
            result["sgps"] = [sgp.to_dict() for sgp in sgps]

            # Remove SGP legs from available singles
            sgp_legs = set()
            for sgp in sgps:
                sgp_legs.update(sgp.legs)

            available_for_singles = [p for p in plays if p["play_id"] not in sgp_legs]
        else:
            available_for_singles = plays

        # Optimize singles portfolio
        if self.enable_optimization:
            portfolios = self.optimize_portfolio(
                available_for_singles,
                target_size=target_size - len(result["sgps"]),
                num_portfolios=1,
            )

            if portfolios:
                best = portfolios[0]
                result["singles"] = best.play_ids
                result["portfolio_metrics"] = best.to_dict()

        result["total_plays"] = len(result["singles"]) + len(result["sgps"])

        return result

    def get_recommended_portfolio(
        self,
        plays: list[dict],
        target_size: int = 10,
    ) -> dict[str, Any]:
        """Get the recommended portfolio (main entry point).

        Args:
            plays: List of validated plays
            target_size: Target portfolio size

        Returns:
            Complete portfolio recommendation
        """
        # Step 1: Check risk limits
        risk_status = self.check_risk_limits(plays)

        # Step 2: Analyze correlations
        correlation_analysis = self.analyze_correlations(plays)

        # Step 3: Optimize
        portfolios = self.optimize_portfolio(plays, target_size=target_size)

        if not portfolios:
            return {
                "error": "No valid portfolios found",
                "risk_status": risk_status,
                "correlation_analysis": correlation_analysis,
            }

        best = portfolios[0]

        return {
            "recommended_portfolio": best.to_dict(),
            "alternatives": [p.to_dict() for p in portfolios[1:3]],
            "risk_status": risk_status,
            "correlation_analysis": correlation_analysis,
            "summary": {
                "plays": len(best.play_ids),
                "expected_return": round(best.total_expected_return, 3),
                "risk_score": round(best.risk_score, 3),
                "diversification": round(best.diversification_score, 3),
                "overall_score": round(best.overall_score, 3),
            },
        }

    def get_optimal_bet_sizes(
        self,
        portfolio: PortfolioResult | None = None,
        bankroll: float = 100.0,
    ) -> dict[str, float]:
        """Get optimal bet sizes for portfolio.

        Args:
            portfolio: Portfolio result (uses last if None)
            bankroll: Total bankroll

        Returns:
            Bet sizes by play_id
        """
        if portfolio is None:
            portfolio = self._last_portfolio_result

        if portfolio is None or not self.optimizer:
            return {}

        return self.optimizer.get_optimal_bet_sizes(
            portfolio_ids=portfolio.play_ids,
            bankroll=bankroll,
        )

    def get_portfolio_summary(self) -> dict[str, Any]:
        """Get summary of current portfolio state."""
        return {
            "enabled_modules": {
                "correlation": self.enable_correlation,
                "risk_control": self.enable_risk_control,
                "optimization": self.enable_optimization,
                "sgp": self.enable_sgp,
            },
            "last_correlation_matrix": (
                self._last_correlation_matrix.to_dict() if self._last_correlation_matrix else None
            ),
            "risk_controller_status": (
                self.risk_controller.get_status() if self.risk_controller else None
            ),
        }

    def validate_against_correlation(
        self,
        new_play: dict,
        existing_plays: list[dict],
        max_correlation: float = 0.6,
    ) -> dict[str, Any]:
        """Validate if new play creates problematic correlations.

        Args:
            new_play: New play to validate
            existing_plays: Current portfolio plays
            max_correlation: Maximum allowed correlation

        Returns:
            Validation result
        """
        if not self.enable_correlation or not self.correlation_detector:
            return {"enabled": False, "valid": True}

        # Add all plays
        all_plays = existing_plays + [new_play]
        self.analyze_correlations(all_plays)

        # Check correlations with new play
        if not self._last_correlation_matrix:
            return {"valid": True}

        high_correlations = []
        for existing in existing_plays:
            corr = self._last_correlation_matrix.get_correlation(
                new_play["play_id"],
                existing["play_id"],
            )
            if abs(corr) > max_correlation:
                details = self._last_correlation_matrix.get_correlation_details(
                    new_play["play_id"],
                    existing["play_id"],
                )
                high_correlations.append({
                    "play": existing["play_id"],
                    "correlation": round(corr, 3),
                    "explanation": details.explanation if details else "",
                })

        return {
            "valid": len(high_correlations) == 0,
            "new_play": new_play["play_id"],
            "high_correlations": high_correlations,
            "recommendation": (
                "Proceed" if len(high_correlations) == 0
                else "Caution - high correlation detected"
            ),
        }


def create_portfolio_aware_board_builder(
    risk_limits: ExposureLimits | None = None,
    enable_sgp: bool = False,
) -> PortfolioHooks:
    """Factory function to create portfolio-aware board builder.

    Args:
        risk_limits: Risk limits configuration
        enable_sgp: Enable SGP building

    Returns:
        Configured PortfolioHooks instance
    """
    return PortfolioHooks(
        enable_correlation=True,
        enable_risk_control=True,
        enable_optimization=True,
        enable_sgp=enable_sgp,
        risk_limits=risk_limits,
    )
