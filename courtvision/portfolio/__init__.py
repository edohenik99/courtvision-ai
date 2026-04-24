"""Phase 10: Correlation and Portfolio Optimization.

Optimize groups of plays together instead of evaluating each play independently.

Components:
- correlation: Detect correlations between plays (same player, game, stat dependencies)
- risk_control: Portfolio exposure limits per game/player/stat
- covariance: Model how outcomes move together
- sgp_builder: SGP-aware logic for intentional correlation
- optimizer: Portfolio scoring (return, risk, drawdown)
- hooks: Integration with board builder

Phase 10 adds portfolio-level optimization after individual play validation.
"""

from __future__ import annotations

from courtvision.portfolio.correlation import CorrelationDetector, CorrelationMatrix
from courtvision.portfolio.covariance import CovarianceModel
from courtvision.portfolio.optimizer import PortfolioOptimizer, PortfolioResult
from courtvision.portfolio.risk_control import ExposureLimits, RiskController
from courtvision.portfolio.sgp_builder import SGPBuilder, SGPParlay
from courtvision.portfolio.hooks import PortfolioHooks

__all__ = [
    # Correlation
    "CorrelationDetector",
    "CorrelationMatrix",
    # Risk Control
    "ExposureLimits",
    "RiskController",
    # Covariance
    "CovarianceModel",
    # SGP
    "SGPBuilder",
    "SGPParlay",
    # Optimization
    "PortfolioOptimizer",
    "PortfolioResult",
    # Integration
    "PortfolioHooks",
]
