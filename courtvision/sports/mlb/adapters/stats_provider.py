"""Placeholder adapters for future MLB and Statcast-style statistics."""

from __future__ import annotations

from datetime import date

from courtvision.core.provider_registry import get_provider as get_provider_registration
from courtvision.sports.mlb.adapters.base import HitterStats, PitcherStats
from courtvision.sports.mlb.research_context import (
    MLBHitterFeatureContext,
    MLBPitcherFeatureContext,
)


class MLBStatsProvider:
    """Provider shell for hitter batted-ball data and pitcher matchup data."""

    _registration = get_provider_registration("mlb_stats_placeholder")
    provider_name = _registration.name
    source_type = _registration.source_type
    supported_modes = _registration.supported_modes
    requires_credentials = _registration.requires_credentials
    required_env_vars = _registration.required_environment_variables
    capabilities = _registration.capabilities
    production_safe = _registration.production_safe
    can_be_used_for_production = _registration.can_be_used_for_production

    def get_hitter_stats(self, player: str, *, game_id: str | None = None) -> HitterStats:
        raise NotImplementedError("Live MLB hitter statistics are not configured")

    def get_pitcher_stats(self, pitcher: str, *, game_id: str | None = None) -> PitcherStats:
        raise NotImplementedError("Live MLB pitcher statistics are not configured")

    def get_hitter_features(
        self,
        player_id: str,
        as_of_date: date,
        window: str,
    ) -> MLBHitterFeatureContext | None:
        """Return explicit missing data; no live source is configured."""

        return None

    def get_pitcher_features(
        self,
        pitcher_id: str,
        as_of_date: date,
        window: str,
    ) -> MLBPitcherFeatureContext | None:
        """Return explicit missing data; no live source is configured."""

        return None


__all__ = ["MLBStatsProvider"]
