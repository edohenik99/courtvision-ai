"""Data adapter contracts and provider shells for MLB HR prop research."""

from courtvision.sports.mlb.adapters.base import (
    BallparkFactors,
    BallparkProvider,
    HitterStats,
    HitterStatsProvider,
    OddsProvider,
    OddsQuote,
    PitcherStats,
    PitcherStatsProvider,
    WeatherConditions,
    WeatherProvider,
)
from courtvision.sports.mlb.adapters.ballpark_provider import MLBBallparkProvider
from courtvision.sports.mlb.adapters.odds_provider import (
    SUPPORTED_SPORTSBOOKS,
    SportsbookOddsProvider,
)
from courtvision.sports.mlb.adapters.odds_api_provider import (
    HROddsCandidate,
    NormalizedOddsCandidate,
    OddsAPIConfigurationError,
    OddsAPIProvider,
    OddsAPIProviderError,
    OddsAPIRequestError,
)
from courtvision.sports.mlb.adapters.provider_factory import (
    SUPPORTED_PROVIDERS,
    UnsupportedProviderError,
    create_provider,
    get_hr_provider,
    get_provider,
)
from courtvision.sports.mlb.adapters.sample_provider import SampleHRProvider, SampleProvider
from courtvision.sports.mlb.adapters.stats_provider import MLBStatsProvider
from courtvision.sports.mlb.adapters.weather_provider import MLBWeatherProvider

__all__ = [
    "BallparkFactors",
    "BallparkProvider",
    "HitterStats",
    "HitterStatsProvider",
    "MLBBallparkProvider",
    "MLBStatsProvider",
    "MLBWeatherProvider",
    "HROddsCandidate",
    "NormalizedOddsCandidate",
    "OddsProvider",
    "OddsQuote",
    "OddsAPIConfigurationError",
    "OddsAPIProvider",
    "OddsAPIProviderError",
    "OddsAPIRequestError",
    "PitcherStats",
    "PitcherStatsProvider",
    "SUPPORTED_PROVIDERS",
    "SUPPORTED_SPORTSBOOKS",
    "SampleHRProvider",
    "SampleProvider",
    "SportsbookOddsProvider",
    "UnsupportedProviderError",
    "WeatherConditions",
    "WeatherProvider",
    "create_provider",
    "get_hr_provider",
    "get_provider",
]
