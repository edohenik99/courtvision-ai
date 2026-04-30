"""
Provider Manager with Priority-Based Fallback

Manages multiple data providers (SportsDataIO, BallDontLie) with:
- Priority-based provider selection
- Per-domain fallback (games, players, stats, injuries, odds)
- Transparent logging of provider usage
- Graceful degradation when providers fail
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from courtvision.clients.balldontlie_client import BalldontlieClient
from courtvision.clients.sportsdataio_client import (
    SportsDataIOClient,
    SportsDataIOError,
    SPORTSDATAIO_API_KEY_ENV_VAR,
)
from courtvision.config import Settings
from courtvision.models import Game, Injury, MarketProp, PlayerGameStats, PlayerInfo

logger = logging.getLogger("courtvision.providers")


class ProviderStatus(str, Enum):
    """Provider status labels for diagnostics."""

    SPORTSDATAIO_PRIMARY = "sportsdataio_primary"
    SPORTSDATAIO_PARTIAL_BDL_FALLBACK = "sportsdataio_partial_bdl_fallback"
    BALLDONTLIE_FALLBACK = "balldontlie_fallback"
    FAILED_NO_PROVIDER = "failed_no_provider"


class DataDomain(str, Enum):
    """Data domains for per-domain fallback."""

    GAMES = "games"
    PLAYERS = "players"
    STATS = "stats"
    INJURIES = "injuries"
    ODDS = "odds"


@dataclass
class ProviderResult:
    """Result from a provider operation with metadata."""

    data: Any
    provider_used: str
    fallback_used: bool = False
    failure_reason: str | None = None
    partial_data: bool = False


@dataclass
class ProviderRunStatus:
    """Status of a complete pipeline run with provider provenance."""

    provider_attempted: str = ""
    provider_used: str = ""
    provider_fallback_used: bool = False
    failure_reason: str | None = None
    partial_provider_merge_used: bool = False
    domain_status: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_log_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "provider_attempted": self.provider_attempted,
            "provider_used": self.provider_used,
            "provider_fallback_used": self.provider_fallback_used,
            "failure_reason": self.failure_reason,
            "partial_provider_merge_used": self.partial_provider_merge_used,
            "domain_status": self.domain_status,
        }


class ProviderManager:
    """
    Manages multiple data providers with priority-based fallback.

    Provider priority (configurable via NBA_PROVIDER_PRIORITY env var):
    1. SportsDataIO (primary)
    2. BallDontLie (fallback)

    Usage:
        manager = ProviderManager(settings)
        games = manager.get_games_by_date("2025-04-20")
        status = manager.get_run_status()
    """

    DEFAULT_PROVIDER_PRIORITY = ["sportsdataio", "balldontlie"]

    def __init__(
        self,
        settings: Settings | None = None,
        provider_priority: list[str] | None = None,
    ) -> None:
        """
        Initialize provider manager.

        Args:
            settings: Balldontlie settings (optional)
            provider_priority: Ordered list of provider names (defaults to env or DEFAULT)
        """
        self.settings = settings or Settings.from_env()
        self.provider_priority = self._resolve_priority(provider_priority)

        # Initialize clients. BallDontLie may be absent in SportsDataIO-only
        # environments, so keep the manager constructible and skip the fallback
        # provider at fetch time if credentials are not available.
        self.sportsdataio = SportsDataIOClient()
        self.balldontlie: BalldontlieClient | None
        try:
            self.balldontlie = BalldontlieClient(self.settings)
        except RuntimeError as exc:
            logger.warning("Provider balldontlie not configured: %s", exc)
            self.balldontlie = None

        # Run status tracking
        self._run_status = ProviderRunStatus()
        self._domain_results: dict[str, ProviderResult] = {}

        logger.info(
            "ProviderManager initialized with priority: %s",
            self.provider_priority,
        )

    def _resolve_priority(self, explicit: list[str] | None) -> list[str]:
        """Resolve provider priority from explicit, env, or default."""
        if explicit:
            return explicit

        env_priority = os.getenv("NBA_PROVIDER_PRIORITY", "").strip()
        if env_priority:
            return [p.strip().lower() for p in env_priority.split(",") if p.strip()]

        return list(self.DEFAULT_PROVIDER_PRIORITY)

    def get_run_status(self) -> ProviderRunStatus:
        """Get current run status for diagnostics."""
        return self._run_status

    def _try_provider(
        self,
        domain: DataDomain,
        provider_name: str,
        operation: str,
        *args: Any,
        **kwargs: Any,
    ) -> ProviderResult | None:
        """
        Try a single provider for an operation.

        Args:
            domain: Data domain being fetched
            provider_name: Name of provider to try
            operation: Method name to call
            *args, **kwargs: Arguments for the operation

        Returns:
            ProviderResult on success, None on failure
        """
        client = self._get_client(provider_name)
        if not client:
            logger.debug("Provider %s not available (no client)", provider_name)
            return None

        # Check if provider is configured
        if hasattr(client, "is_configured") and not client.is_configured():
            logger.debug("Provider %s not configured, skipping", provider_name)
            return None

        try:
            method = getattr(client, operation)
            data = method(*args, **kwargs)

            # Validate we got meaningful data
            if data is None:
                logger.warning("Provider %s returned None for %s", provider_name, operation)
                return None

            # For list operations, check if list is non-empty
            if isinstance(data, list) and len(data) == 0:
                logger.warning("Provider %s returned empty list for %s", provider_name, operation)
                # Still accept empty lists as valid (no data available)

            logger.info(
                "Provider %s succeeded for %s.%s (returned %s items)",
                provider_name,
                domain.value,
                operation,
                len(data) if hasattr(data, "__len__") else "?",
            )

            return ProviderResult(
                data=data,
                provider_used=provider_name,
                fallback_used=False,
            )

        except SportsDataIOError as exc:
            logger.warning(
                "Provider %s failed for %s.%s: %s",
                provider_name,
                domain.value,
                operation,
                exc,
            )
            return None

        except Exception as exc:
            logger.warning(
                "Provider %s unexpected error for %s.%s: %s",
                provider_name,
                domain.value,
                operation,
                exc,
                exc_info=True,
            )
            return None

    def _get_client(self, provider_name: str) -> Any:
        """Get client instance by provider name."""
        provider_lower = provider_name.lower()

        if provider_lower == "sportsdataio":
            return self.sportsdataio
        elif provider_lower in ("balldontlie", "bdl"):
            return self.balldontlie

        logger.error("Unknown provider: %s", provider_name)
        return None

    def _fetch_with_fallback(
        self,
        domain: DataDomain,
        operation: str,
        *args: Any,
        **kwargs: Any,
    ) -> ProviderResult:
        """
        Fetch data with priority-based fallback across providers.

        Args:
            domain: Data domain
            operation: Method name to call on providers
            *args, **kwargs: Arguments for the operation

        Returns:
            ProviderResult with data and metadata

        Raises:
            RuntimeError: If no provider succeeds
        """
        last_error: str | None = None

        primary_provider = self.provider_priority[0] if self.provider_priority else ""

        for provider_name in self.provider_priority:
            result = self._try_provider(domain, provider_name, operation, *args, **kwargs)

            if result is not None:
                fallback_used = provider_name != primary_provider
                result.fallback_used = fallback_used
                # Track domain status
                self._run_status.domain_status[domain.value] = {
                    "provider_used": provider_name,
                    "fallback_used": fallback_used,
                    "success": True,
                }
                self._domain_results[domain.value] = result
                return result

            last_error = f"{provider_name}_failed"

        # All providers failed
        error_msg = f"All providers failed for {domain.value}.{operation}"
        logger.error(error_msg)

        self._run_status.domain_status[domain.value] = {
            "provider_used": "none",
            "fallback_used": False,
            "success": False,
            "error": last_error,
        }

        raise RuntimeError(error_msg)

    # -------------------------------------------------------------------------
    # Public API matching BalldontlieClient interface
    # -------------------------------------------------------------------------

    def get_games_by_date(self, target_date: str) -> list[Game]:
        """
        Fetch games by date with fallback.

        Args:
            target_date: Date in YYYY-MM-DD format

        Returns:
            List of Game objects
        """
        self._run_status.provider_attempted = self.provider_priority[0]

        try:
            result = self._fetch_with_fallback(
                DataDomain.GAMES,
                "get_games_by_date",
                target_date,
            )

            self._run_status.provider_used = result.provider_used
            self._run_status.provider_fallback_used = result.fallback_used

            return result.data

        except RuntimeError as exc:
            self._run_status.failure_reason = str(exc)
            self._run_status.provider_used = "none"
            raise

    def get_active_players_for_team_ids(self, team_ids: set[int]) -> list[PlayerInfo]:
        """
        Fetch active players for team IDs with fallback.

        Args:
            team_ids: Set of team IDs

        Returns:
            List of PlayerInfo objects
        """
        result = self._fetch_with_fallback(
            DataDomain.PLAYERS,
            "get_active_players_for_team_ids",
            team_ids,
        )
        return result.data

    def get_stats_for_player_ids(
        self, player_ids: Iterable[int], seasons: list[int]
    ) -> list[PlayerGameStats]:
        """
        Fetch player stats with fallback.

        Args:
            player_ids: Iterable of player IDs
            seasons: List of season years

        Returns:
            List of PlayerGameStats objects
        """
        result = self._fetch_with_fallback(
            DataDomain.STATS,
            "get_stats_for_player_ids",
            player_ids,
            seasons,
        )
        return result.data

    def get_stats_for_player_ids_on_date(
        self,
        player_ids: Iterable[int],
        target_date: str,
        season: int | None = None,
    ) -> list[PlayerGameStats]:
        """
        Fetch player stats for specific date with fallback.

        Args:
            player_ids: Iterable of player IDs
            target_date: Date in YYYY-MM-DD format
            season: Season year (optional)

        Returns:
            List of PlayerGameStats objects
        """
        result = self._fetch_with_fallback(
            DataDomain.STATS,
            "get_stats_for_player_ids_on_date",
            player_ids,
            target_date,
            season=season,
        )
        return result.data

    def get_team_injuries(self) -> list[Injury]:
        """
        Fetch team injuries with fallback.

        Returns:
            List of Injury objects
        """
        try:
            result = self._fetch_with_fallback(
                DataDomain.INJURIES,
                "get_team_injuries",
            )
            return result.data
        except RuntimeError:
            # Injuries are optional - return empty list on failure
            logger.warning("Injury fetch failed for all providers, returning empty list")
            return []

    def get_player_props_for_game(
        self,
        game_id: int,
        vendors: list[str] | None = None,
        prop_types: list[str] | None = None,
    ) -> list[MarketProp]:
        """
        Fetch player props (odds) with fallback.

        Args:
            game_id: Game ID
            vendors: List of betting vendors
            prop_types: List of prop types

        Returns:
            List of MarketProp objects (may be empty)
        """
        try:
            result = self._fetch_with_fallback(
                DataDomain.ODDS,
                "get_player_props_for_game",
                game_id,
                vendors=vendors,
                prop_types=prop_types,
            )
            return result.data
        except RuntimeError:
            # Odds are optional - return empty list on failure
            logger.warning("Odds fetch failed for all providers for game %s, returning empty list", game_id)
            return []

    def log_provider_summary(self) -> None:
        """Log summary of provider usage for the run."""
        status = self._run_status

        # Determine overall status label
        if status.failure_reason:
            overall_status = ProviderStatus.FAILED_NO_PROVIDER
        elif status.provider_fallback_used:
            if status.partial_provider_merge_used:
                overall_status = ProviderStatus.SPORTSDATAIO_PARTIAL_BDL_FALLBACK
            else:
                overall_status = ProviderStatus.BALLDONTLIE_FALLBACK
        else:
            overall_status = ProviderStatus.SPORTSDATAIO_PRIMARY

        logger.info(
            "Provider run summary: status=%s, attempted=%s, used=%s, fallback=%s, domains=%s",
            overall_status.value,
            status.provider_attempted,
            status.provider_used,
            status.provider_fallback_used,
            list(status.domain_status.keys()),
        )

        # Log per-domain status
        for domain, info in status.domain_status.items():
            logger.info(
                "  Domain %s: provider=%s, success=%s",
                domain,
                info.get("provider_used", "unknown"),
                info.get("success", False),
            )


__all__ = [
    "ProviderManager",
    "ProviderResult",
    "ProviderRunStatus",
    "ProviderStatus",
    "DataDomain",
]
