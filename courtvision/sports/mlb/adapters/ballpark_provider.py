"""Placeholder adapter for future MLB ballpark factor data."""

from __future__ import annotations

from courtvision.core.provider_registry import get_provider as get_provider_registration
from courtvision.sports.mlb.adapters.base import BallparkFactors
from courtvision.sports.mlb.research_context import MLBBallparkContext


class MLBBallparkProvider:
    """Provider shell for HR factors, handedness adjustments, and dimensions."""

    _registration = get_provider_registration("mlb_ballpark_placeholder")
    provider_name = _registration.name
    source_type = _registration.source_type
    supported_modes = _registration.supported_modes
    requires_credentials = _registration.requires_credentials
    required_env_vars = _registration.required_environment_variables
    capabilities = _registration.capabilities
    production_safe = _registration.production_safe
    can_be_used_for_production = _registration.can_be_used_for_production

    def get_ballpark(self, venue: str) -> BallparkFactors:
        raise NotImplementedError("Live MLB ballpark factors are not configured")

    def get_ballpark_context(
        self, venue_name: str
    ) -> MLBBallparkContext | None:
        """Return explicit missing data; no ballpark source is configured."""

        return None


__all__ = ["MLBBallparkProvider"]
