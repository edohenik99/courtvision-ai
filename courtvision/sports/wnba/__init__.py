"""WNBA configuration and placeholder projection model."""

from courtvision.core.sport_registry import get_sport
from courtvision.sports.wnba.projection import WNBAProjectionModel

SPORT = get_sport("WNBA")
SUPPORTED_PROP_MARKETS = SPORT.supported_prop_markets

__all__ = ["SPORT", "SUPPORTED_PROP_MARKETS", "WNBAProjectionModel"]
