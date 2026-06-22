"""NFL configuration and placeholder projection model."""

from courtvision.core.sport_registry import get_sport
from courtvision.sports.nfl.projection import NFLProjectionFeatures, NFLProjectionModel

SPORT = get_sport("NFL")
SUPPORTED_PROP_MARKETS = SPORT.supported_prop_markets

__all__ = ["NFLProjectionFeatures", "NFLProjectionModel", "SPORT", "SUPPORTED_PROP_MARKETS"]
