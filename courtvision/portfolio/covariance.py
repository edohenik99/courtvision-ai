"""Covariance modeling for portfolio outcome correlations.

Estimates how outcomes move together and adjusts portfolio variance.

Phase 10: Correlation and Portfolio Optimization
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class PlayVariance:
    """Variance parameters for a single play."""

    play_id: str
    mean: float  # Expected value
    variance: float  # Variance of outcome
    std_dev: float  # Standard deviation
    hit_probability: float  # Probability of hitting
    expected_payout: float  # Expected payout if hit


@dataclass
class CovarianceEstimate:
    """Covariance estimate between two plays."""

    play_1_id: str
    play_2_id: str
    covariance: float
    correlation: float
    joint_hit_probability: float  # P(both hit)
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "play_1": self.play_1_id,
            "play_2": self.play_2_id,
            "covariance": round(self.covariance, 4),
            "correlation": round(self.correlation, 3),
            "joint_hit_prob": round(self.joint_hit_probability, 3),
            "explanation": self.explanation,
        }


class CovarianceModel:
    """Model covariance between portfolio plays.

    Estimates how outcomes move together using:
    - Historical correlation data
    - Structural correlations (same player/game)
    - Statistical relationships
    """

    def __init__(self) -> None:
        """Initialize covariance model."""
        self.play_vars: dict[str, PlayVariance] = {}
        self.covariances: dict[tuple[str, str], CovarianceEstimate] = {}

    def add_play_variance(
        self,
        play_id: str,
        mean: float,
        variance: float,
        hit_probability: float,
        expected_payout: float = 1.0,
    ) -> None:
        """Add variance information for a play.

        Args:
            play_id: Play identifier
            mean: Expected outcome
            variance: Variance of outcome
            hit_probability: Probability of hitting line
            expected_payout: Expected payout if hit
        """
        self.play_vars[play_id] = PlayVariance(
            play_id=play_id,
            mean=mean,
            variance=variance,
            std_dev=np.sqrt(variance),
            hit_probability=hit_probability,
            expected_payout=expected_payout,
        )

    def estimate_covariance(
        self,
        play_1_id: str,
        play_2_id: str,
        correlation: float,
    ) -> CovarianceEstimate:
        """Estimate covariance between two plays.

        Args:
            play_1_id: First play ID
            play_2_id: Second play ID
            correlation: Correlation coefficient (-1 to 1)

        Returns:
            CovarianceEstimate
        """
        var_1 = self.play_vars.get(play_1_id)
        var_2 = self.play_vars.get(play_2_id)

        if not var_1 or not var_2:
            return CovarianceEstimate(
                play_1_id=play_1_id,
                play_2_id=play_2_id,
                covariance=0.0,
                correlation=0.0,
                joint_hit_probability=0.0,
                explanation="Variance data not available",
            )

        # Covariance = correlation * std_1 * std_2
        covariance = correlation * var_1.std_dev * var_2.std_dev

        # Estimate joint hit probability
        # For positively correlated plays: P(A and B) > P(A) * P(B)
        # For negatively correlated plays: P(A and B) < P(A) * P(B)
        independent_prob = var_1.hit_probability * var_2.hit_probability

        if correlation > 0.5:
            # Strong positive correlation - significantly higher joint prob
            joint_prob = independent_prob + (1 - independent_prob) * correlation * 0.5
        elif correlation > 0:
            # Moderate positive correlation
            joint_prob = independent_prob * (1 + correlation)
        elif correlation < -0.5:
            # Strong negative correlation - significantly lower joint prob
            joint_prob = independent_prob * (1 + correlation)  # correlation is negative
        elif correlation < 0:
            # Moderate negative correlation
            joint_prob = independent_prob * (1 + correlation * 0.5)
        else:
            joint_prob = independent_prob

        # Clamp to valid range
        joint_prob = max(0.0, min(1.0, joint_prob))

        estimate = CovarianceEstimate(
            play_1_id=play_1_id,
            play_2_id=play_2_id,
            covariance=covariance,
            correlation=correlation,
            joint_hit_probability=joint_prob,
            explanation=f"Covariance based on correlation {correlation:.2f}",
        )

        # Store estimate
        key = tuple(sorted([play_1_id, play_2_id]))
        self.covariances[key] = estimate

        return estimate

    def build_covariance_matrix(
        self,
        play_ids: list[str],
        correlation_matrix: np.ndarray | None = None,
    ) -> np.ndarray:
        """Build full covariance matrix.

        Args:
            play_ids: List of play IDs
            correlation_matrix: Optional pre-computed correlation matrix

        Returns:
            Covariance matrix (n x n)
        """
        n = len(play_ids)
        cov_matrix = np.zeros((n, n))

        for i, play_1_id in enumerate(play_ids):
            for j, play_2_id in enumerate(play_ids):
                if i == j:
                    # Diagonal: variance of play
                    var = self.play_vars.get(play_1_id)
                    cov_matrix[i, j] = var.variance if var else 1.0
                else:
                    # Off-diagonal: covariance
                    if correlation_matrix is not None:
                        corr = correlation_matrix[i, j]
                    else:
                        key = tuple(sorted([play_1_id, play_2_id]))
                        est = self.covariances.get(key)
                        corr = est.correlation if est else 0.0

                    var_1 = self.play_vars.get(play_1_id)
                    var_2 = self.play_vars.get(play_2_id)

                    if var_1 and var_2:
                        cov_matrix[i, j] = corr * var_1.std_dev * var_2.std_dev
                    else:
                        cov_matrix[i, j] = 0.0

        return cov_matrix

    def calculate_portfolio_variance(
        self,
        play_ids: list[str],
        weights: np.ndarray | None = None,
        correlation_matrix: np.ndarray | None = None,
    ) -> dict[str, Any]:
        """Calculate portfolio variance with covariance.

        Args:
            play_ids: List of play IDs
            weights: Portfolio weights (defaults to equal weight)
            correlation_matrix: Optional correlation matrix

        Returns:
            Portfolio variance metrics
        """
        n = len(play_ids)

        if n == 0:
            return {"error": "No plays in portfolio"}

        if n == 1:
            var = self.play_vars.get(play_ids[0])
            return {
                "portfolio_variance": var.variance if var else 0,
                "portfolio_std": var.std_dev if var else 0,
                "diversification_ratio": 1.0,
            }

        # Default equal weights
        if weights is None:
            weights = np.ones(n) / n

        # Build covariance matrix
        cov_matrix = self.build_covariance_matrix(play_ids, correlation_matrix)

        # Portfolio variance = w^T * Cov * w
        portfolio_variance = weights.T @ cov_matrix @ weights
        portfolio_std = np.sqrt(portfolio_variance)

        # Calculate variance if uncorrelated (for comparison)
        individual_variances = np.array([
            self.play_vars.get(pid).variance if pid in self.play_vars else 0
            for pid in play_ids
        ])
        uncorrelated_variance = np.sum((weights ** 2) * individual_variances)

        # Diversification ratio
        # > 1: Correlations reduce diversification benefit
        # < 1: Negative correlations improve diversification
        diversification_ratio = (
            portfolio_variance / uncorrelated_variance
            if uncorrelated_variance > 0 else 1.0
        )

        return {
            "portfolio_variance": round(portfolio_variance, 4),
            "portfolio_std": round(portfolio_std, 4),
            "uncorrelated_variance": round(uncorrelated_variance, 4),
            "diversification_ratio": round(diversification_ratio, 3),
            "diversification_benefit": round(1 - diversification_ratio, 3),
            "interpretation": (
                "high_diversification" if diversification_ratio < 0.8
                else "moderate_diversification" if diversification_ratio < 1.0
                else "low_diversification"
            ),
        }

    def estimate_joint_outcomes(
        self,
        play_ids: list[str],
    ) -> dict[str, Any]:
        """Estimate joint outcome probabilities.

        Args:
            play_ids: List of play IDs

        Returns:
            Joint outcome probabilities
        """
        n = len(play_ids)
        if n == 0:
            return {"error": "No plays provided"}

        # Get individual hit probabilities
        hit_probs = [
            self.play_vars.get(pid).hit_probability if pid in self.play_vars else 0.5
            for pid in play_ids
        ]

        # Calculate independent probability (all hit)
        independent_all_hit = np.prod(hit_probs)

        # Adjust for correlations
        # If positive correlations: higher joint prob
        # If negative correlations: lower joint prob
        total_correlation = 0.0
        count = 0

        for i, play_1_id in enumerate(play_ids):
            for play_2_id in play_ids[i + 1:]:
                key = tuple(sorted([play_1_id, play_2_id]))
                est = self.covariances.get(key)
                if est:
                    total_correlation += est.correlation
                    count += 1

        avg_correlation = total_correlation / count if count > 0 else 0.0

        # Adjust joint probability
        if avg_correlation > 0:
            joint_all_hit = independent_all_hit * (1 + avg_correlation * 0.5)
        else:
            joint_all_hit = independent_all_hit * (1 + avg_correlation * 0.3)

        joint_all_hit = max(0.0, min(1.0, joint_all_hit))

        # Expected number of hits
        expected_hits = sum(hit_probs)

        # Variance of total hits
        # Var(sum X_i) = sum Var(X_i) + 2 * sum sum Cov(X_i, X_j)
        hit_variances = [p * (1 - p) for p in hit_probs]  # Bernoulli variance
        total_variance = sum(hit_variances)

        for i, play_1_id in enumerate(play_ids):
            for play_2_id in play_ids[i + 1:]:
                key = tuple(sorted([play_1_id, play_2_id]))
                est = self.covariances.get(key)
                if est:
                    cov = est.covariance
                    # Scale covariance to hit probability scale
                    scaled_cov = cov * hit_probs[i] * (1 - hit_probs[i]) * 0.1
                    total_variance += 2 * scaled_cov

        return {
            "independent_all_hit_prob": round(independent_all_hit, 4),
            "adjusted_all_hit_prob": round(joint_all_hit, 4),
            "expected_hits": round(expected_hits, 2),
            "variance_hits": round(total_variance, 4),
            "std_hits": round(np.sqrt(total_variance), 4),
            "avg_correlation": round(avg_correlation, 3),
        }

    def get_risk_contribution(
        self,
        play_id: str,
        portfolio_ids: list[str],
    ) -> dict[str, Any]:
        """Calculate marginal risk contribution of a play.

        Args:
            play_id: Play to analyze
            portfolio_ids: Current portfolio plays

        Returns:
            Risk contribution metrics
        """
        if play_id not in portfolio_ids:
            return {"error": "Play not in portfolio"}

        # Portfolio with play
        with_play_var = self.calculate_portfolio_variance(portfolio_ids)

        # Portfolio without play
        without_ids = [pid for pid in portfolio_ids if pid != play_id]
        without_play_var = (
            self.calculate_portfolio_variance(without_ids)
            if without_ids else {"portfolio_variance": 0}
        )

        marginal_risk = (
            with_play_var["portfolio_variance"] - without_play_var["portfolio_variance"]
        )

        risk_contribution_pct = (
            marginal_risk / with_play_var["portfolio_variance"]
            if with_play_var["portfolio_variance"] > 0 else 0
        )

        return {
            "play_id": play_id,
            "marginal_risk": round(marginal_risk, 4),
            "risk_contribution_pct": round(risk_contribution_pct, 3),
            "interpretation": (
                "high" if risk_contribution_pct > 0.2
                else "moderate" if risk_contribution_pct > 0.1
                else "low"
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        """Export covariance model."""
        return {
            "plays": {
                pid: {
                    "mean": round(pv.mean, 2),
                    "variance": round(pv.variance, 4),
                    "std_dev": round(pv.std_dev, 4),
                    "hit_prob": round(pv.hit_probability, 3),
                }
                for pid, pv in self.play_vars.items()
            },
            "covariances": [
                cov.to_dict() for cov in self.covariances.values()
            ],
        }
