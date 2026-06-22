"""Placeholder adapter for future MLB game-time weather data."""

from __future__ import annotations

from datetime import datetime

from courtvision.core.provider_registry import get_provider as get_provider_registration
from courtvision.sports.mlb.adapters.base import WeatherConditions
from courtvision.sports.mlb.research_context import MLBGameContext, MLBWeatherContext


class MLBWeatherProvider:
    """Provider shell; no weather service or credentials are selected yet."""

    _registration = get_provider_registration("mlb_weather_placeholder")
    provider_name = _registration.name
    source_type = _registration.source_type
    supported_modes = _registration.supported_modes
    requires_credentials = _registration.requires_credentials
    required_env_vars = _registration.required_environment_variables
    capabilities = _registration.capabilities
    production_safe = _registration.production_safe
    can_be_used_for_production = _registration.can_be_used_for_production

    def get_weather(
        self,
        venue: str,
        game_time: datetime | str,
        *,
        game_id: str | None = None,
    ) -> WeatherConditions:
        raise NotImplementedError("Live MLB weather data is not configured")

    def get_weather_for_game(
        self, game: MLBGameContext
    ) -> MLBWeatherContext | None:
        """Return explicit missing data; no weather service is configured."""

        return None


__all__ = ["MLBWeatherProvider"]
