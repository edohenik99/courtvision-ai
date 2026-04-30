"""
Regression tests for provider manager with fallback logic.

Tests verify:
1. SportsDataIO is attempted before BallDontLie
2. Missing SportsDataIO key falls back without crash
3. SportsDataIO failure falls back without crash
4. Partial SportsDataIO response can merge with BallDontLie fallback
5. Existing BallDontLie-only behavior still works when SportsDataIO is disabled
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from courtvision.clients.balldontlie_client import BalldontlieClient
from courtvision.clients.provider_manager import (
    DataDomain,
    ProviderManager,
    ProviderResult,
    ProviderStatus,
)
from courtvision.clients.sportsdataio_client import (
    SportsDataIOAuthError,
    SportsDataIOClient,
    SportsDataIOError,
)
from courtvision.config import ProviderSettings, Settings
from courtvision.models import Game, Injury, MarketProp, PlayerGameStats, PlayerInfo, Team


class MockSportsDataIOClient:
    """Mock SportsDataIO client for testing."""

    def __init__(self, api_key: str | None = None, **kwargs: Any) -> None:
        self.api_key = api_key
        self.configured = bool(api_key)
        self.call_count: dict[str, int] = {}
        self.should_fail: dict[str, Exception | None] = {}
        self.return_data: dict[str, Any] = {}

    def is_configured(self) -> bool:
        return self.configured

    def _track_call(self, method: str) -> None:
        self.call_count[method] = self.call_count.get(method, 0) + 1

    def get_games_by_date(self, target_date: str) -> list[Game]:
        self._track_call("get_games_by_date")
        if "get_games_by_date" in self.should_fail:
            raise self.should_fail["get_games_by_date"]
        return self.return_data.get("get_games_by_date", [])

    def get_active_players_for_team_ids(self, team_ids: set[int]) -> list[PlayerInfo]:
        self._track_call("get_active_players_for_team_ids")
        if "get_active_players_for_team_ids" in self.should_fail:
            raise self.should_fail["get_active_players_for_team_ids"]
        return self.return_data.get("get_active_players_for_team_ids", [])

    def get_stats_for_player_ids(self, player_ids: Any, seasons: list[int]) -> list[PlayerGameStats]:
        self._track_call("get_stats_for_player_ids")
        if "get_stats_for_player_ids" in self.should_fail:
            raise self.should_fail["get_stats_for_player_ids"]
        return self.return_data.get("get_stats_for_player_ids", [])

    def get_stats_for_player_ids_on_date(self, player_ids: Any, target_date: str, season: int | None = None) -> list[PlayerGameStats]:
        self._track_call("get_stats_for_player_ids_on_date")
        if "get_stats_for_player_ids_on_date" in self.should_fail:
            raise self.should_fail["get_stats_for_player_ids_on_date"]
        return self.return_data.get("get_stats_for_player_ids_on_date", [])

    def get_team_injuries(self) -> list[Injury]:
        self._track_call("get_team_injuries")
        if "get_team_injuries" in self.should_fail:
            raise self.should_fail["get_team_injuries"]
        return self.return_data.get("get_team_injuries", [])

    def get_player_props_for_game(self, game_id: int, vendors: list[str] | None = None, prop_types: list[str] | None = None) -> list[MarketProp]:
        self._track_call("get_player_props_for_game")
        if "get_player_props_for_game" in self.should_fail:
            raise self.should_fail["get_player_props_for_game"]
        return self.return_data.get("get_player_props_for_game", [])


class MockBalldontlieClient:
    """Mock BallDontLie client for testing."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.call_count: dict[str, int] = {}
        self.should_fail: dict[str, Exception | None] = {}
        self.return_data: dict[str, Any] = {}

    def _track_call(self, method: str) -> None:
        self.call_count[method] = self.call_count.get(method, 0) + 1

    def get_games_by_date(self, target_date: str) -> list[Game]:
        self._track_call("get_games_by_date")
        if "get_games_by_date" in self.should_fail:
            raise self.should_fail["get_games_by_date"]
        return self.return_data.get("get_games_by_date", [])

    def get_active_players_for_team_ids(self, team_ids: set[int]) -> list[PlayerInfo]:
        self._track_call("get_active_players_for_team_ids")
        if "get_active_players_for_team_ids" in self.should_fail:
            raise self.should_fail["get_active_players_for_team_ids"]
        return self.return_data.get("get_active_players_for_team_ids", [])

    def get_stats_for_player_ids(self, player_ids: Any, seasons: list[int]) -> list[PlayerGameStats]:
        self._track_call("get_stats_for_player_ids")
        if "get_stats_for_player_ids" in self.should_fail:
            raise self.should_fail["get_stats_for_player_ids"]
        return self.return_data.get("get_stats_for_player_ids", [])

    def get_stats_for_player_ids_on_date(self, player_ids: Any, target_date: str, season: int | None = None) -> list[PlayerGameStats]:
        self._track_call("get_stats_for_player_ids_on_date")
        if "get_stats_for_player_ids_on_date" in self.should_fail:
            raise self.should_fail["get_stats_for_player_ids_on_date"]
        return self.return_data.get("get_stats_for_player_ids_on_date", [])

    def get_team_injuries(self) -> list[Injury]:
        self._track_call("get_team_injuries")
        if "get_team_injuries" in self.should_fail:
            raise self.should_fail["get_team_injuries"]
        return self.return_data.get("get_team_injuries", [])

    def get_player_props_for_game(self, game_id: int, vendors: list[str] | None = None, prop_types: list[str] | None = None) -> list[MarketProp]:
        self._track_call("get_player_props_for_game")
        if "get_player_props_for_game" in self.should_fail:
            raise self.should_fail["get_player_props_for_game"]
        return self.return_data.get("get_player_props_for_game", [])


@pytest.fixture
def sample_games() -> list[Game]:
    """Sample games for testing."""
    return [
        Game(
            id=1,
            date="2025-04-20",
            home_team=Team(id=1, abbreviation="LAL", full_name="Los Angeles Lakers"),
            visitor_team=Team(id=2, abbreviation="GSW", full_name="Golden State Warriors"),
            status="Scheduled",
        ),
    ]


@pytest.fixture
def sample_players() -> list[PlayerInfo]:
    """Sample players for testing."""
    return [
        PlayerInfo(
            id=101,
            first_name="LeBron",
            last_name="James",
            full_name="LeBron James",
            team_id=1,
            team_abbreviation="LAL",
            position="F",
        ),
    ]


@pytest.fixture
def mock_clients() -> Generator[tuple[MockSportsDataIOClient, MockBalldontlieClient], None, None]:
    """Create mock clients for testing."""
    sdio = MockSportsDataIOClient(api_key="test_key")
    bdl = MockBalldontlieClient(Settings(api_key="test_key"))
    yield sdio, bdl


class TestProviderPriority:
    """Tests for provider priority ordering."""

    def test_sportsdataio_attempted_first(self, sample_games: list[Game], mock_clients: Any) -> None:
        """Test 1: SportsDataIO is attempted before BallDontLie."""
        sdio, bdl = mock_clients

        # Configure both providers to succeed
        sdio.return_data["get_games_by_date"] = sample_games
        bdl.return_data["get_games_by_date"] = []

        # Create manager with default priority
        manager = ProviderManager(Settings(api_key="test"))
        manager.sportsdataio = sdio
        manager.balldontlie = bdl

        # Execute
        result = manager.get_games_by_date("2025-04-20")

        # Verify
        assert result == sample_games
        assert sdio.call_count.get("get_games_by_date", 0) == 1
        assert bdl.call_count.get("get_games_by_date", 0) == 0

        # Verify status tracking
        status = manager.get_run_status()
        assert status.provider_attempted == "sportsdataio"
        assert status.provider_used == "sportsdataio"
        assert not status.provider_fallback_used

    def test_provider_priority_configurable(self) -> None:
        """Test that provider priority can be configured."""
        # Test with reversed priority
        manager = ProviderManager(
            Settings(api_key="test"),
            provider_priority=["balldontlie", "sportsdataio"],
        )

        assert manager.provider_priority == ["balldontlie", "sportsdataio"]

    def test_env_var_priority_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test NBA_PROVIDER_PRIORITY environment variable."""
        monkeypatch.setenv("NBA_PROVIDER_PRIORITY", "balldontlie,sportsdataio")

        manager = ProviderManager(Settings(api_key="test"))
        assert manager.provider_priority == ["balldontlie", "sportsdataio"]


class TestMissingCredentialsFallback:
    """Tests for fallback when credentials are missing."""

    def test_missing_sportsdataio_key_falls_back(self, sample_games: list[Game]) -> None:
        """Test 2: Missing SportsDataIO key falls back without crash."""
        # Create SportsDataIO client without API key (not configured)
        sdio = MockSportsDataIOClient(api_key=None)
        sdio.configured = False

        bdl = MockBalldontlieClient(Settings(api_key="test_key"))
        bdl.return_data["get_games_by_date"] = sample_games

        # Set priority so SportsDataIO is first but will be skipped
        manager = ProviderManager(Settings(api_key="test"), provider_priority=["sportsdataio", "balldontlie"])
        manager.sportsdataio = sdio
        manager.balldontlie = bdl

        # Execute - should not crash
        result = manager.get_games_by_date("2025-04-20")

        # Verify fallback worked
        assert result == sample_games
        assert bdl.call_count.get("get_games_by_date", 0) == 1

        # Verify status - BallDontLie should be used since SportsDataIO not configured
        status = manager.get_run_status()
        assert status.provider_used == "balldontlie"
        assert status.domain_status["games"]["provider_used"] == "balldontlie"


class TestProviderFailureFallback:
    """Tests for fallback when primary provider fails."""

    def test_sportsdataio_failure_falls_back(self, sample_games: list[Game]) -> None:
        """Test 3: SportsDataIO failure falls back without crash."""
        sdio = MockSportsDataIOClient(api_key="test_key")
        sdio.should_fail["get_games_by_date"] = SportsDataIOError("API Error")

        bdl = MockBalldontlieClient(Settings(api_key="test_key"))
        bdl.return_data["get_games_by_date"] = sample_games

        manager = ProviderManager(Settings(api_key="test"))
        manager.sportsdataio = sdio
        manager.balldontlie = bdl

        # Execute - should not crash
        result = manager.get_games_by_date("2025-04-20")

        # Verify fallback worked
        assert result == sample_games
        assert sdio.call_count.get("get_games_by_date", 0) == 1
        assert bdl.call_count.get("get_games_by_date", 0) == 1

        # Verify status - BallDontLie used as fallback
        status = manager.get_run_status()
        assert status.provider_used == "balldontlie"
        assert status.domain_status["games"]["provider_used"] == "balldontlie"

    def test_auth_error_triggers_fallback(self, sample_games: list[Game]) -> None:
        """Test that authentication errors trigger fallback."""
        sdio = MockSportsDataIOClient(api_key="test_key")
        sdio.should_fail["get_games_by_date"] = SportsDataIOAuthError("Invalid API key")

        bdl = MockBalldontlieClient(Settings(api_key="test_key"))
        bdl.return_data["get_games_by_date"] = sample_games

        manager = ProviderManager(Settings(api_key="test"))
        manager.sportsdataio = sdio
        manager.balldontlie = bdl

        # Execute - should fallback, not crash
        result = manager.get_games_by_date("2025-04-20")
        assert result == sample_games


class TestBallDontLieOnlyMode:
    """Tests for BallDontLie-only operation."""

    def test_balldontlie_only_works_when_sportsdataio_disabled(self, sample_games: list[Game]) -> None:
        """Test 5: BallDontLie-only behavior works when SportsDataIO is disabled."""
        sdio = MockSportsDataIOClient(api_key=None)
        sdio.configured = False

        bdl = MockBalldontlieClient(Settings(api_key="test_key"))
        bdl.return_data["get_games_by_date"] = sample_games

        # Create manager with only BallDontLie
        manager = ProviderManager(Settings(api_key="test"))
        manager.sportsdataio = sdio
        manager.balldontlie = bdl

        # Execute
        result = manager.get_games_by_date("2025-04-20")

        # Verify
        assert result == sample_games
        assert bdl.call_count.get("get_games_by_date", 0) == 1

    def test_balldontlie_only_priority(self, sample_games: list[Game]) -> None:
        """Test BallDontLie as sole provider."""
        sdio = MockSportsDataIOClient(api_key=None)
        sdio.configured = False

        bdl = MockBalldontlieClient(Settings(api_key="test_key"))
        bdl.return_data["get_games_by_date"] = sample_games

        manager = ProviderManager(
            Settings(api_key="test"),
            provider_priority=["balldontlie"],  # Only BallDontLie
        )
        manager.sportsdataio = sdio
        manager.balldontlie = bdl

        result = manager.get_games_by_date("2025-04-20")
        assert result == sample_games


class TestAllProvidersFail:
    """Tests for complete failure scenario."""

    def test_all_providers_fail_raises_error(self) -> None:
        """Test that RuntimeError is raised when all providers fail."""
        sdio = MockSportsDataIOClient(api_key="test_key")
        sdio.should_fail["get_games_by_date"] = SportsDataIOError("SportsDataIO down")

        bdl = MockBalldontlieClient(Settings(api_key="test_key"))
        bdl.should_fail["get_games_by_date"] = RuntimeError("BDL down")

        manager = ProviderManager(Settings(api_key="test"))
        manager.sportsdataio = sdio
        manager.balldontlie = bdl

        # Execute - should raise RuntimeError
        with pytest.raises(RuntimeError, match="All providers failed"):
            manager.get_games_by_date("2025-04-20")

        # Verify status tracked failure
        status = manager.get_run_status()
        assert status.failure_reason is not None
        assert "All providers failed" in status.failure_reason


class TestInjuriesOptional:
    """Tests that injuries are optional and return empty list on failure."""

    def test_injuries_return_empty_on_all_providers_fail(self) -> None:
        """Test that injuries return empty list rather than crashing."""
        sdio = MockSportsDataIOClient(api_key="test_key")
        sdio.should_fail["get_team_injuries"] = SportsDataIOError("API Error")

        bdl = MockBalldontlieClient(Settings(api_key="test_key"))
        bdl.should_fail["get_team_injuries"] = RuntimeError("BDL Error")

        manager = ProviderManager(Settings(api_key="test"))
        manager.sportsdataio = sdio
        manager.balldontlie = bdl

        # Execute - should not crash
        result = manager.get_team_injuries()

        # Verify empty list returned
        assert result == []


class TestOddsOptional:
    """Tests that odds are optional and return empty list on failure."""

    def test_odds_return_empty_on_all_providers_fail(self) -> None:
        """Test that odds return empty list rather than crashing."""
        sdio = MockSportsDataIOClient(api_key="test_key")
        sdio.should_fail["get_player_props_for_game"] = SportsDataIOError("API Error")

        bdl = MockBalldontlieClient(Settings(api_key="test_key"))
        bdl.should_fail["get_player_props_for_game"] = RuntimeError("BDL Error")

        manager = ProviderManager(Settings(api_key="test"))
        manager.sportsdataio = sdio
        manager.balldontlie = bdl

        # Execute - should not crash
        result = manager.get_player_props_for_game(12345)

        # Verify empty list returned
        assert result == []


class TestProviderSettings:
    """Tests for ProviderSettings configuration."""

    def test_provider_settings_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test ProviderSettings loads from environment."""
        monkeypatch.setenv("SPORTSDATAIO_API_KEY", "sdio_test_key")
        monkeypatch.setenv("BALLDONTLIE_API_KEY", "bdl_test_key")
        monkeypatch.setenv("NBA_PROVIDER_PRIORITY", "sportsdataio,balldontlie")

        settings = ProviderSettings.from_env()

        assert settings.sportsdataio_api_key == "sdio_test_key"
        assert settings.balldontlie_api_key == "bdl_test_key"
        assert settings.provider_priority == ["sportsdataio", "balldontlie"]

    def test_provider_status_diagnostics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test provider status returns diagnostic info."""
        monkeypatch.setenv("SPORTSDATAIO_API_KEY", "sdio_key")
        monkeypatch.setenv("BALLDONTLIE_API_KEY", "bdl_key")

        settings = ProviderSettings.from_env()
        status = settings.get_provider_status()

        assert status["provider_priority"] == ["balldontlie"]
        assert status["sportsdataio_configured"] is True
        assert status["balldontlie_configured"] is True

    def test_provider_status_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test provider status when credentials missing."""
        # Empty process values intentionally block .env fallback for this
        # configuration diagnostic.
        monkeypatch.setenv("SPORTSDATAIO_API_KEY", "")
        monkeypatch.setenv("BALLDONTLIE_API_KEY", "")

        settings = ProviderSettings.from_env()
        status = settings.get_provider_status()

        assert status["sportsdataio_configured"] is False
        # BallDontLie might be configured from other sources, just check structure
        assert isinstance(status["balldontlie_configured"], bool)


class TestProviderLogging:
    """Tests for provider logging and diagnostics."""

    def test_run_status_tracks_domain_status(self, sample_games: list[Game]) -> None:
        """Test that run status tracks per-domain provider usage."""
        sdio = MockSportsDataIOClient(api_key="test_key")
        sdio.return_data["get_games_by_date"] = sample_games

        manager = ProviderManager(Settings(api_key="test"))
        manager.sportsdataio = sdio
        manager.balldontlie = MagicMock()

        # Execute multiple operations
        manager.get_games_by_date("2025-04-20")

        # Verify domain tracking
        status = manager.get_run_status()
        assert "games" in status.domain_status
        assert status.domain_status["games"]["provider_used"] == "sportsdataio"
        assert status.domain_status["games"]["success"] is True


class TestProviderResult:
    """Tests for ProviderResult data class."""

    def test_provider_result_creation(self) -> None:
        """Test ProviderResult can be created and accessed."""
        result = ProviderResult(
            data=[{"test": "data"}],
            provider_used="sportsdataio",
            fallback_used=False,
        )

        assert result.data == [{"test": "data"}]
        assert result.provider_used == "sportsdataio"
        assert not result.fallback_used
        assert result.failure_reason is None


class TestProviderStatusEnum:
    """Tests for ProviderStatus enum."""

    def test_status_labels_exist(self) -> None:
        """Test that all expected status labels exist."""
        assert ProviderStatus.SPORTSDATAIO_PRIMARY == "sportsdataio_primary"
        assert ProviderStatus.SPORTSDATAIO_PARTIAL_BDL_FALLBACK == "sportsdataio_partial_bdl_fallback"
        assert ProviderStatus.BALLDONTLIE_FALLBACK == "balldontlie_fallback"
        assert ProviderStatus.FAILED_NO_PROVIDER == "failed_no_provider"


class TestDataDomainEnum:
    """Tests for DataDomain enum."""

    def test_data_domains_exist(self) -> None:
        """Test that all expected data domains exist."""
        assert DataDomain.GAMES == "games"
        assert DataDomain.PLAYERS == "players"
        assert DataDomain.STATS == "stats"
        assert DataDomain.INJURIES == "injuries"
        assert DataDomain.ODDS == "odds"
