"""Typed, research-only provider contracts for MLB context acquisition.

These protocols define the Phase 2B context shapes that future acquisition
adapters must return.  They do not select providers, perform I/O, or authorize
production use.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from courtvision.core.provider_registry import (
    ProviderCapability,
    ProviderMode,
    ProviderSourceType,
)
from courtvision.sports.mlb.research_context import (
    MLBBallparkContext,
    MLBGameContext,
    MLBHitterFeatureContext,
    MLBHRResearchContext,
    MLBLineupContext,
    MLBPitcherFeatureContext,
    MLBProbablePitcherContext,
    MLBWeatherContext,
)


@runtime_checkable
class MLBProviderContract(Protocol):
    """Metadata shared by every MLB context provider contract.

    Metadata uses the Phase 1C enums so implementations can be compared with
    the capability registry without translation.  MLB implementations remain
    research-safe unless separately registered and approved; Phase 1C rejects
    production mode for MLB in all cases.
    """

    @property
    def provider_name(self) -> str:
        """Return the matching Phase 1C provider registration name."""

    @property
    def source_type(self) -> ProviderSourceType:
        """Return the declared origin of the provider's data."""

    @property
    def supported_modes(self) -> frozenset[ProviderMode]:
        """Return only modes explicitly declared by the provider registry."""

    @property
    def requires_credentials(self) -> bool:
        """Return whether any credential is required before provider use."""

    @property
    def required_env_vars(self) -> tuple[str, ...]:
        """Return environment variable names required by the provider."""

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        """Return only capabilities explicitly declared by Phase 1C."""

    @property
    def production_safe(self) -> bool:
        """Remain false for all Phase 2D MLB provider implementations."""

    @property
    def can_be_used_for_production(self) -> bool:
        """Remain false for all Phase 2D MLB provider implementations."""


@runtime_checkable
class MLBScheduleProvider(MLBProviderContract, Protocol):
    """Provide schedule contexts, or an empty list when none are available."""

    def get_games(self, report_date: date) -> list[MLBGameContext]:
        """Return known games for ``report_date`` without fabricating data."""


@runtime_checkable
class MLBLineupProvider(MLBProviderContract, Protocol):
    """Provide lineup contexts with explicit empty/missing behavior."""

    def get_lineups(self, report_date: date) -> list[MLBLineupContext]:
        """Return known lineups, or an empty list when none are available."""

    def get_lineup_for_game(self, game_id: str) -> MLBLineupContext | None:
        """Return one known lineup or ``None`` when it is unavailable."""


@runtime_checkable
class MLBProbablePitcherProvider(MLBProviderContract, Protocol):
    """Provide probable-pitcher contexts without assuming confirmation."""

    def get_probable_pitchers(
        self, report_date: date
    ) -> list[MLBProbablePitcherContext]:
        """Return known probable pitchers, or an explicit empty list."""

    def get_probable_pitcher_for_game(
        self, game_id: str, team: str
    ) -> MLBProbablePitcherContext | None:
        """Return one known probable pitcher or ``None`` when unavailable."""


@runtime_checkable
class MLBHitterFeatureProvider(MLBProviderContract, Protocol):
    """Provide one Phase 2B hitter feature context when available."""

    def get_hitter_features(
        self,
        player_id: str,
        as_of_date: date,
        window: str,
    ) -> MLBHitterFeatureContext | None:
        """Return known features or ``None``; never synthesize live data."""


@runtime_checkable
class MLBPitcherFeatureProvider(MLBProviderContract, Protocol):
    """Provide one Phase 2B pitcher feature context when available."""

    def get_pitcher_features(
        self,
        pitcher_id: str,
        as_of_date: date,
        window: str,
    ) -> MLBPitcherFeatureContext | None:
        """Return known features or ``None``; never synthesize live data."""


@runtime_checkable
class MLBWeatherProvider(MLBProviderContract, Protocol):
    """Provide weather context for a known Phase 2B game."""

    def get_weather_for_game(self, game: MLBGameContext) -> MLBWeatherContext | None:
        """Return known weather or ``None`` when it is unavailable."""


@runtime_checkable
class MLBBallparkProvider(MLBProviderContract, Protocol):
    """Provide ballpark context by venue name."""

    def get_ballpark_context(self, venue_name: str) -> MLBBallparkContext | None:
        """Return known ballpark context or ``None`` when unavailable."""


@runtime_checkable
class MLBResearchContextProvider(MLBProviderContract, Protocol):
    """Optionally provide complete Phase 2B HR research contexts."""

    def get_hr_research_contexts(
        self, report_date: date
    ) -> list[MLBHRResearchContext]:
        """Return known research contexts, or an explicit empty list."""


__all__ = [
    "MLBBallparkProvider",
    "MLBHitterFeatureProvider",
    "MLBLineupProvider",
    "MLBPitcherFeatureProvider",
    "MLBProbablePitcherProvider",
    "MLBProviderContract",
    "MLBResearchContextProvider",
    "MLBScheduleProvider",
    "MLBWeatherProvider",
]
