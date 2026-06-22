"""MLB configuration and placeholder projection model."""

from courtvision.core.sport_registry import get_sport
from courtvision.sports.mlb.projection import MLBProjectionFeatures, MLBProjectionModel

SPORT = get_sport("MLB")
SUPPORTED_PROP_MARKETS = SPORT.supported_prop_markets

__all__ = ["MLBProjectionFeatures", "MLBProjectionModel", "SPORT", "SUPPORTED_PROP_MARKETS"]
