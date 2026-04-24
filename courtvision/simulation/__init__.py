"""Phase 9: Scenario Simulation and Forward Validation.

Test predictions under different possible game outcomes before committing
to final board decisions.

Components:
- simulation_engine: Core Monte Carlo simulation for stat outcomes
- distributions: Outcome distribution modeling (mean, variance, skew, ceiling/floor)
- ev_calculation: Expected value based on odds + hit probability
- robustness: Sensitivity testing for minutes, injury, variance
- validation: Board validation layer for high-risk play rejection
- hooks: Integration with board builder

Phase 9 adds an optional validation layer after scoring to ensure only
robust, high-EV plays make it to final boards.
"""

from __future__ import annotations

from courtvision.simulation.distributions import OutcomeDistribution, StatDistributionModel
from courtvision.simulation.ev_calculation import EVCalculator, OddsConverter
from courtvision.simulation.robustness import RobustnessTester, SensitivityResult
from courtvision.simulation.simulation_engine import SimulationEngine, SimulationResult
from courtvision.simulation.validation import BoardValidator, ValidationResult
from courtvision.simulation.hooks import SimulationHooks

__all__ = [
    # Core simulation
    "SimulationEngine",
    "SimulationResult",
    "OutcomeDistribution",
    "StatDistributionModel",
    # EV calculation
    "EVCalculator",
    "OddsConverter",
    # Robustness
    "RobustnessTester",
    "SensitivityResult",
    # Validation
    "BoardValidator",
    "ValidationResult",
    # Integration
    "SimulationHooks",
]
