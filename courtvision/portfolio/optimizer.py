"""Portfolio optimizer with return/risk/drawdown scoring.

Evaluates portfolios on:
- Expected return (EV-weighted)
- Risk (variance with covariance)
- Drawdown potential

Phase 10: Correlation and Portfolio Optimization
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from courtvision.portfolio.correlation import CorrelationMatrix
from courtvision.portfolio.covariance import CovarianceModel
from courtvision.portfolio.risk_control import RiskController


@dataclass
class PortfolioResult:
    """Result of portfolio optimization."""

    portfolio_id: str
    play_ids: list[str]

    # Return metrics
    total_expected_return: float
    avg_ev: float
    expected_hits: float

    # Risk metrics
    portfolio_variance: float
    portfolio_std: float
    risk_score: float  # 0-1 (0 = low risk)
    sharpe_ratio: float  # Return / Risk

    # Drawdown metrics
    max_drawdown_prob: float  # Probability of major loss
    tail_risk_score: float  # Extreme loss potential

    # Diversification
    diversification_score: float  # 0-1 (1 = well diversified)
    num_games: int
    num_players: int

    # Constraints
    satisfies_constraints: bool
    constraint_violations: list[str] = field(default_factory=list)

    # Ranking
    overall_score: float = 0.0  # Composite score for ranking

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "portfolio_id": self.portfolio_id,
            "plays": {
                "count": len(self.play_ids),
                "ids": self.play_ids,
            },
            "return": {
                "total_expected": round(self.total_expected_return, 3),
                "avg_ev": round(self.avg_ev, 3),
                "expected_hits": round(self.expected_hits, 2),
            },
            "risk": {
                "variance": round(self.portfolio_variance, 4),
                "std": round(self.portfolio_std, 4),
                "score": round(self.risk_score, 3),
                "sharpe": round(self.sharpe_ratio, 3),
            },
            "drawdown": {
                "max_dd_prob": round(self.max_drawdown_prob, 3),
                "tail_risk": round(self.tail_risk_score, 3),
            },
            "diversification": {
                "score": round(self.diversification_score, 3),
                "games": self.num_games,
                "players": self.num_players,
            },
            "constraints": {
                "satisfied": self.satisfies_constraints,
                "violations": self.constraint_violations if not self.satisfies_constraints else [],
            },
            "overall_score": round(self.overall_score, 3),
        }


@dataclass
class PlayMetrics:
    """Metrics for a single play."""

    play_id: str
    player_name: str
    game_id: str
    stat_type: str

    # Expected value
    ev: float
    hit_probability: float
    odds: float

    # Risk
    variance: float
    std_dev: float

    # Payout
    expected_payout: float


class PortfolioOptimizer:
    """Optimize portfolio of plays.

    Evaluates multiple portfolio compositions and selects
    the best balance of return, risk, and diversification.
    """

    # Risk tolerance levels
    RISK_CONSERVATIVE = 0.3
    RISK_MODERATE = 0.5
    RISK_AGGRESSIVE = 0.7

    def __init__(
        self,
        risk_tolerance: float = RISK_MODERATE,
        risk_controller: RiskController | None = None,
        covariance_model: CovarianceModel | None = None,
    ) -> None:
        """Initialize portfolio optimizer.

        Args:
            risk_tolerance: Risk tolerance level (0-1)
            risk_controller: Risk controller for constraint checking
            covariance_model: Covariance model for variance calculation
        """
        self.risk_tolerance = risk_tolerance
        self.risk_controller = risk_controller or RiskController()
        self.covariance_model = covariance_model or CovarianceModel()

        self.play_metrics: dict[str, PlayMetrics] = {}

    def add_play(
        self,
        play_id: str,
        player_name: str,
        game_id: str,
        stat_type: str,
        ev: float,
        hit_probability: float,
        odds: float,
        variance: float,
    ) -> None:
        """Add a play to the optimization pool.

        Args:
            play_id: Play identifier
            player_name: Player name
            game_id: Game ID
            stat_type: Stat type
            ev: Expected value
            hit_probability: Probability of hitting
            odds: Decimal odds
            variance: Variance of outcome
        """
        self.play_metrics[play_id] = PlayMetrics(
            play_id=play_id,
            player_name=player_name,
            game_id=game_id,
            stat_type=stat_type,
            ev=ev,
            hit_probability=hit_probability,
            odds=odds,
            variance=variance,
            std_dev=np.sqrt(variance),
            expected_payout=ev + 1,  # Simplified
        )

    def evaluate_portfolio(
        self,
        play_ids: list[str],
        correlation_matrix: CorrelationMatrix | None = None,
    ) -> PortfolioResult:
        """Evaluate a portfolio composition.

        Args:
            play_ids: List of play IDs in portfolio
            correlation_matrix: Correlation matrix for covariance calculation

        Returns:
            PortfolioResult with all metrics
        """
        if not play_ids:
            return PortfolioResult(
                portfolio_id="empty",
                play_ids=[],
                total_expected_return=0.0,
                avg_ev=0.0,
                expected_hits=0.0,
                portfolio_variance=0.0,
                portfolio_std=0.0,
                risk_score=0.0,
                sharpe_ratio=0.0,
                max_drawdown_prob=0.0,
                tail_risk_score=0.0,
                diversification_score=0.0,
                num_games=0,
                num_players=0,
                satisfies_constraints=True,
                overall_score=0.0,
            )

        # Get metrics for plays
        plays = [self.play_metrics[pid] for pid in play_ids if pid in self.play_metrics]

        if not plays:
            return PortfolioResult(
                portfolio_id="invalid",
                play_ids=play_ids,
                total_expected_return=0.0,
                avg_ev=0.0,
                expected_hits=0.0,
                portfolio_variance=0.0,
                portfolio_std=0.0,
                risk_score=0.0,
                sharpe_ratio=0.0,
                max_drawdown_prob=0.0,
                tail_risk_score=0.0,
                diversification_score=0.0,
                num_games=0,
                num_players=0,
                satisfies_constraints=False,
                constraint_violations=["No valid play metrics found"],
                overall_score=0.0,
            )

        # Calculate return metrics
        total_ev = sum(p.ev for p in plays)
        avg_ev = total_ev / len(plays)
        expected_hits = sum(p.hit_probability for p in plays)

        # Calculate portfolio variance with covariance
        weights = np.ones(len(plays)) / len(plays)  # Equal weights

        # Add plays to covariance model
        for p in plays:
            self.covariance_model.add_play_variance(
                play_id=p.play_id,
                mean=p.hit_probability,
                variance=p.variance,
                hit_probability=p.hit_probability,
                expected_payout=p.expected_payout,
            )

        # Build covariance matrix
        cov_result = self.covariance_model.calculate_portfolio_variance(
            play_ids=[p.play_id for p in plays],
            weights=weights,
            correlation_matrix=correlation_matrix.matrix if correlation_matrix else None,
        )

        portfolio_variance = cov_result["portfolio_variance"]
        portfolio_std = cov_result["portfolio_std"]

        # Risk score (0-1, higher = more risk)
        risk_score = min(1.0, portfolio_std / 0.5)  # Normalize to 0-1

        # Sharpe ratio (simplified, assuming risk-free rate = 0)
        sharpe_ratio = avg_ev / portfolio_std if portfolio_std > 0 else 0

        # Drawdown metrics
        # Estimate probability of significant loss (>20% of plays miss)
        # This is a simplified estimate
        miss_probabilities = [1 - p.hit_probability for p in plays]
        expected_misses = sum(miss_probabilities)

        # Probability of >50% misses (using normal approximation)
        variance_misses = sum(p * (1 - p) for p in miss_probabilities)
        std_misses = np.sqrt(variance_misses)

        # P(misses > 0.5 * n)
        threshold_misses = 0.5 * len(plays)
        if std_misses > 0:
            z_score = (threshold_misses - expected_misses) / std_misses
            # Approximate normal CDF
            max_drawdown_prob = 1 - 0.5 * (1 + np.sign(z_score) * np.sqrt(1 - np.exp(-2 * z_score ** 2 / np.pi)))
        else:
            max_drawdown_prob = 1.0 if expected_misses > threshold_misses else 0.0

        # Tail risk - probability of extreme loss (>80% misses)
        extreme_threshold = 0.8 * len(plays)
        if std_misses > 0:
            z_extreme = (extreme_threshold - expected_misses) / std_misses
            tail_risk_score = 1 - 0.5 * (1 + np.sign(z_extreme) * np.sqrt(1 - np.exp(-2 * z_extreme ** 2 / np.pi)))
        else:
            tail_risk_score = 1.0 if expected_misses > extreme_threshold else 0.0

        # Diversification metrics
        unique_games = len(set(p.game_id for p in plays))
        unique_players = len(set(p.player_name for p in plays))
        unique_stats = len(set(p.stat_type for p in plays))

        # Diversification score
        game_diversification = min(1.0, unique_games / max(3, len(plays) * 0.5))
        player_diversification = min(1.0, unique_players / len(plays))
        stat_diversification = min(1.0, unique_stats / 3)  # 3+ stat types = good

        diversification_score = (game_diversification + player_diversification + stat_diversification) / 3

        # Check constraints
        violations = []
        self.risk_controller.reset()

        for p in plays:
            can_add, reason = self.risk_controller.can_add_play(
                player_name=p.player_name,
                game_id=p.game_id,
                stat_type=p.stat_type,
            )
            if can_add:
                self.risk_controller.add_play(
                    play_id=p.play_id,
                    player_name=p.player_name,
                    game_id=p.game_id,
                    stat_type=p.stat_type,
                )
            else:
                violations.append(f"{p.play_id}: {reason}")

        satisfies_constraints = len(violations) == 0

        # Calculate overall score (composite)
        # Weights: Return 40%, Risk 30%, Diversification 30%
        return_score = min(1.0, max(0, avg_ev + 0.1) / 0.2)  # Normalize EV to 0-1
        risk_score_inv = 1 - risk_score  # Lower risk = higher score

        overall_score = (
            0.40 * return_score +
            0.30 * risk_score_inv +
            0.30 * diversification_score
        )

        return PortfolioResult(
            portfolio_id=f"portfolio_{len(play_ids)}_{hash(tuple(play_ids)) % 10000}",
            play_ids=play_ids,
            total_expected_return=total_ev,
            avg_ev=avg_ev,
            expected_hits=expected_hits,
            portfolio_variance=portfolio_variance,
            portfolio_std=portfolio_std,
            risk_score=risk_score,
            sharpe_ratio=sharpe_ratio,
            max_drawdown_prob=max_drawdown_prob,
            tail_risk_score=tail_risk_score,
            diversification_score=diversification_score,
            num_games=unique_games,
            num_players=unique_players,
            satisfies_constraints=satisfies_constraints,
            constraint_violations=violations,
            overall_score=overall_score,
        )

    def optimize(
        self,
        available_play_ids: list[str],
        correlation_matrix: CorrelationMatrix | None = None,
        target_size: int = 10,
        num_portfolios: int = 5,
    ) -> list[PortfolioResult]:
        """Find optimal portfolio compositions.

        Args:
            available_play_ids: Pool of available plays
            correlation_matrix: Correlation matrix
            target_size: Target number of plays in portfolio
            num_portfolios: Number of portfolios to return

        Returns:
            List of top portfolios ranked by overall score
        """
        if not available_play_ids:
            return []

        # Greedy selection with diversification
        portfolios: list[PortfolioResult] = []

        # Try different starting points
        for start_idx in range(min(len(available_play_ids), 5)):
            selected = []
            remaining = available_play_ids.copy()

            # Start with a different play each time
            first_play = remaining.pop(start_idx)
            selected.append(first_play)

            # Greedily add plays that maximize score while maintaining diversification
            while len(selected) < target_size and remaining:
                best_addition = None
                best_score = -float("inf")

                for play_id in remaining:
                    trial_portfolio = selected + [play_id]
                    result = self.evaluate_portfolio(trial_portfolio, correlation_matrix)

                    if result.satisfies_constraints and result.overall_score > best_score:
                        best_score = result.overall_score
                        best_addition = play_id

                if best_addition:
                    selected.append(best_addition)
                    remaining.remove(best_addition)
                else:
                    break

            if len(selected) >= target_size // 2:  # At least half target
                result = self.evaluate_portfolio(selected, correlation_matrix)
                portfolios.append(result)

        # Rank by overall score
        portfolios.sort(key=lambda p: p.overall_score, reverse=True)

        return portfolios[:num_portfolios]

    def get_optimal_bet_sizes(
        self,
        portfolio_ids: list[str],
        bankroll: float = 100.0,
        kelly_fraction: float = 0.25,
    ) -> dict[str, float]:
        """Calculate optimal bet sizes for portfolio.

        Args:
            portfolio_ids: Plays in portfolio
            bankroll: Total bankroll
            kelly_fraction: Fraction of Kelly to use

        Returns:
            Dict mapping play_id to bet size
        """
        plays = [self.play_metrics[pid] for pid in portfolio_ids if pid in self.play_metrics]

        if not plays:
            return {}

        # Simple proportional sizing based on EV
        total_ev = sum(max(0, p.ev) for p in plays)

        if total_ev == 0:
            # Equal weight if no positive EV
            equal_size = (bankroll * 0.02) / len(plays)  # 2% of bankroll total
            return {p.play_id: equal_size for p in plays}

        # Proportional to EV
        bet_sizes = {}
        for p in plays:
            if p.ev > 0:
                proportion = p.ev / total_ev
                bet_size = bankroll * kelly_fraction * proportion
                bet_sizes[p.play_id] = round(bet_size, 2)

        return bet_sizes

    def compare_portfolios(
        self,
        portfolios: list[PortfolioResult],
    ) -> dict[str, Any]:
        """Compare multiple portfolios."""
        if not portfolios:
            return {"error": "No portfolios to compare"}

        return {
            "count": len(portfolios),
            "best_overall": max(portfolios, key=lambda p: p.overall_score).portfolio_id,
            "best_return": max(portfolios, key=lambda p: p.avg_ev).portfolio_id,
            "best_sharpe": max(portfolios, key=lambda p: p.sharpe_ratio).portfolio_id,
            "lowest_risk": min(portfolios, key=lambda p: p.risk_score).portfolio_id,
            "best_diversified": max(portfolios, key=lambda p: p.diversification_score).portfolio_id,
            "comparison": [
                {
                    "id": p.portfolio_id,
                    "plays": len(p.play_ids),
                    "ev": round(p.avg_ev, 3),
                    "risk": round(p.risk_score, 3),
                    "sharpe": round(p.sharpe_ratio, 3),
                    "diversification": round(p.diversification_score, 3),
                    "score": round(p.overall_score, 3),
                }
                for p in portfolios
            ],
        }
