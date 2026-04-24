"""
SportsDataIO NBA Data Provider Client

Provides NBA data ingestion with normalized interface matching BalldontlieClient.
Falls back gracefully when credentials are missing or endpoints fail.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable
from typing import Any

import requests

from courtvision.models import Game, Injury, MarketProp, PlayerGameStats, PlayerInfo, Team

logger = logging.getLogger("courtvision.sportsdataio")

# Environment variable names
SPORTSDATAIO_API_KEY_ENV_VAR = "SPORTSDATAIO_API_KEY"
SPORTSDATAIO_BASE_URL_ENV_VAR = "SPORTSDATAIO_BASE_URL"

# Default base URL for SportsDataIO NBA API
DEFAULT_BASE_URL = "https://api.sportsdata.io/v3/nba"


class SportsDataIOError(Exception):
    """Base exception for SportsDataIO client errors."""

    pass


class SportsDataIOAuthError(SportsDataIOError):
    """Authentication error (401)."""

    pass


class SportsDataIORateLimitError(SportsDataIOError):
    """Rate limit exceeded (429)."""

    pass


class SportsDataIOClient:
    """
    SportsDataIO NBA API client with normalized interface.

    Provides same methods as BalldontlieClient for seamless provider swapping.
    All methods return data normalized to internal schema expected by pipeline.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        """
        Initialize SportsDataIO client.

        Args:
            api_key: API key (defaults to SPORTSDATAIO_API_KEY env var)
            base_url: API base URL (defaults to SPORTSDATAIO_BASE_URL or production URL)
            timeout_seconds: Request timeout in seconds
        """
        self.api_key = self._resolve_api_key(api_key)
        self.base_url = (base_url or os.getenv(SPORTSDATAIO_BASE_URL_ENV_VAR, DEFAULT_BASE_URL)).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

        if self.api_key:
            self.session.headers.update({
                "Accept": "application/json",
                "Ocp-Apim-Subscription-Key": self.api_key,  # SportsDataIO uses this header
            })

        self._provider_status: dict[str, Any] = {
            "provider": "sportsdataio",
            "has_credentials": bool(self.api_key),
            "base_url": self.base_url,
        }

    def _resolve_api_key(self, provided: str | None) -> str:
        """Resolve API key from provided value or environment."""
        if provided:
            return provided.strip()
        return os.getenv(SPORTSDATAIO_API_KEY_ENV_VAR, "").strip()

    def is_configured(self) -> bool:
        """Check if client has valid credentials."""
        return bool(self.api_key)

    def get_provider_status(self) -> dict[str, Any]:
        """Get current provider status for diagnostics."""
        return dict(self._provider_status)

    def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any] | list[Any]:
        """
        Make authenticated request to SportsDataIO API.

        Args:
            endpoint: API endpoint path (without base URL)
            params: Query parameters
            max_retries: Maximum retry attempts

        Returns:
            Parsed JSON response

        Raises:
            SportsDataIOAuthError: If authentication fails
            SportsDataIORateLimitError: If rate limited
            SportsDataIOError: For other API errors
        """
        if not self.api_key:
            raise SportsDataIOAuthError("SportsDataIO API key not configured")

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        request_params = dict(params or {})

        # SportsDataIO uses query param auth as fallback
        if "key" not in request_params:
            request_params["key"] = self.api_key

        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                response = self.session.get(
                    url,
                    params=request_params,
                    timeout=self.timeout_seconds,
                )

                if response.status_code == 401:
                    raise SportsDataIOAuthError(
                        f"Authentication failed for {endpoint}. "
                        f"Check SPORTSDATAIO_API_KEY environment variable."
                    )

                if response.status_code == 429:
                    if attempt < max_retries - 1:
                        wait_time = 2.0 * (attempt + 1)
                        logger.warning(
                            "SportsDataIO rate limited, waiting %.1f seconds (attempt %d/%d)",
                            wait_time,
                            attempt + 1,
                            max_retries,
                        )
                        time.sleep(wait_time)
                        continue
                    raise SportsDataIORateLimitError(f"Rate limit exceeded for {endpoint}")

                response.raise_for_status()

                # SportsDataIO returns arrays for lists, objects for single items
                payload = response.json()
                return payload if isinstance(payload, (dict, list)) else {}

            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    wait_time = 1.0 * (attempt + 1)
                    logger.warning(
                        "SportsDataIO request failed, retrying in %.1f seconds: %s",
                        wait_time,
                        exc,
                    )
                    time.sleep(wait_time)
                    continue

        if last_error is not None:
            raise SportsDataIOError(f"Request failed after {max_retries} attempts: {last_error}")

        return {}

    # -------------------------------------------------------------------------
    # Normalized interface matching BalldontlieClient
    # -------------------------------------------------------------------------

    def get_games_by_date(self, target_date: str) -> list[Game]:
        """
        Fetch games for a specific date.

        Args:
            target_date: Date in YYYY-MM-DD format

        Returns:
            List of Game objects normalized to internal schema
        """
        try:
            # SportsDataIO uses different date format: YYYY-MM-DD is fine
            # Endpoint: /scores/json/GamesByDate/{date}
            data = self._request(f"scores/json/GamesByDate/{target_date}")

            if not isinstance(data, list):
                logger.warning("SportsDataIO games response not a list: %s", type(data))
                return []

            games: list[Game] = []
            for row in data:
                if not isinstance(row, dict):
                    continue

                # Normalize SportsDataIO response to Game model
                game_id = row.get("GameID") or row.get("gameId") or 0
                home_team_id = row.get("HomeTeamID") or row.get("homeTeamId") or 0
                away_team_id = row.get("AwayTeamID") or row.get("awayTeamId") or 0

                home_team_name = row.get("HomeTeam", "Unknown")
                away_team_name = row.get("AwayTeam", "Unknown")

                # Map team names to abbreviations (SportsDataIO provides names)
                home_abbr = self._normalize_team_abbr(home_team_name)
                away_abbr = self._normalize_team_abbr(away_team_name)

                games.append(
                    Game(
                        id=int(game_id),
                        date=target_date,
                        home_team=Team(
                            id=int(home_team_id),
                            abbreviation=home_abbr,
                            full_name=str(home_team_name),
                        ),
                        visitor_team=Team(
                            id=int(away_team_id),
                            abbreviation=away_abbr,
                            full_name=str(away_team_name),
                        ),
                        home_team_score=row.get("HomeTeamScore") or row.get("homeScore"),
                        visitor_team_score=row.get("AwayTeamScore") or row.get("awayScore"),
                        status=str(row.get("Status", "Scheduled") or "Scheduled"),
                    )
                )

            logger.info("SportsDataIO fetched %d games for %s", len(games), target_date)
            return games

        except SportsDataIOAuthError:
            logger.error("SportsDataIO authentication failed for games fetch")
            raise  # Re-auth errors should propagate for fallback logic
        except Exception as exc:
            logger.warning("SportsDataIO games fetch failed: %s", exc)
            raise SportsDataIOError(f"Failed to fetch games: {exc}") from exc

    def get_active_players_for_team_ids(self, team_ids: set[int]) -> list[PlayerInfo]:
        """
        Fetch active players for given team IDs.

        Args:
            team_ids: Set of team IDs to filter by

        Returns:
            List of PlayerInfo objects
        """
        try:
            # Endpoint: /scores/json/Players
            data = self._request("scores/json/Players")

            if not isinstance(data, list):
                logger.warning("SportsDataIO players response not a list: %s", type(data))
                return []

            players: list[PlayerInfo] = []
            for row in data:
                if not isinstance(row, dict):
                    continue

                team_id = row.get("TeamID") or row.get("teamId") or 0
                if int(team_id) not in team_ids:
                    continue

                first_name = str(row.get("FirstName", "") or row.get("firstName", "")).strip()
                last_name = str(row.get("LastName", "") or row.get("lastName", "")).strip()

                players.append(
                    PlayerInfo(
                        id=int(row.get("PlayerID") or row.get("playerId") or 0),
                        first_name=first_name,
                        last_name=last_name,
                        full_name=f"{first_name} {last_name}".strip(),
                        team_id=int(team_id),
                        team_abbreviation=self._normalize_team_abbr(
                            row.get("Team", row.get("team", "UNK"))
                        ),
                        position=str(row.get("Position", "") or ""),
                    )
                )

            logger.info("SportsDataIO fetched %d active players", len(players))
            return players

        except Exception as exc:
            logger.warning("SportsDataIO players fetch failed: %s", exc)
            raise SportsDataIOError(f"Failed to fetch players: {exc}") from exc

    def get_stats_for_player_ids(
        self, player_ids: Iterable[int], seasons: list[int]
    ) -> list[PlayerGameStats]:
        """
        Fetch season stats for given player IDs.

        Args:
            player_ids: Iterable of player IDs
            seasons: List of seasons (uses first season)

        Returns:
            List of PlayerGameStats objects
        """
        try:
            season = seasons[0] if seasons else 2025
            results: list[PlayerGameStats] = []

            # SportsDataIO endpoint for player game stats by season
            # We need to fetch game logs for each player
            for player_id in player_ids:
                try:
                    # Endpoint: /stats/json/PlayerGameStatsBySeason/{season}/{player_id}
                    data = self._request(f"stats/json/PlayerGameStatsBySeason/{season}/{player_id}")

                    if not isinstance(data, list):
                        continue

                    for row in data:
                        if not isinstance(row, dict):
                            continue

                        results.append(
                            PlayerGameStats(
                                player_id=int(player_id),
                                player_name=self._extract_player_name(row),
                                team_id=int(row.get("TeamID") or row.get("teamId") or 0),
                                team_abbreviation=self._normalize_team_abbr(
                                    row.get("Team", row.get("team", "UNK"))
                                ),
                                game_id=int(row.get("GameID") or row.get("gameId") or 0),
                                game_date=str(row.get("GameDate", "") or row.get("date", "")),
                                minutes=float(row.get("Minutes") or row.get("minutes") or 0),
                                points=float(row.get("Points") or row.get("points") or 0),
                                rebounds=float(row.get("Rebounds") or row.get("rebounds") or 0),
                                assists=float(row.get("Assists") or row.get("assists") or 0),
                                threes=float(row.get("ThreePointersMade") or row.get("fg3m") or 0),
                                steals=float(row.get("Steals") or row.get("steals") or 0),
                                blocks=float(row.get("BlockedShots") or row.get("blocks") or 0),
                            )
                        )

                except Exception as exc:
                    logger.warning("Failed to fetch stats for player %s: %s", player_id, exc)
                    continue

            logger.info("SportsDataIO fetched %d stat rows", len(results))
            return results

        except Exception as exc:
            logger.warning("SportsDataIO stats fetch failed: %s", exc)
            raise SportsDataIOError(f"Failed to fetch stats: {exc}") from exc

    def get_stats_for_player_ids_on_date(
        self,
        player_ids: Iterable[int],
        target_date: str,
        season: int | None = None,
    ) -> list[PlayerGameStats]:
        """
        Fetch stats for players on a specific date.

        Args:
            player_ids: Iterable of player IDs
            target_date: Date in YYYY-MM-DD format
            season: Season year (optional)

        Returns:
            List of PlayerGameStats objects for that date
        """
        try:
            results: list[PlayerGameStats] = []
            season_val = season or 2025

            # Fetch games for the date to get game IDs
            games_data = self._request(f"scores/json/GamesByDate/{target_date}")
            if not isinstance(games_data, list):
                return []

            game_ids = {g.get("GameID") or g.get("gameId") for g in games_data if isinstance(g, dict)}

            # Fetch box scores for each game
            for game_id in game_ids:
                if not game_id:
                    continue

                try:
                    # Endpoint: /stats/json/BoxScore/{game_id}
                    box_score = self._request(f"stats/json/BoxScore/{game_id}")

                    if not isinstance(box_score, dict):
                        continue

                    # Extract player stats from box score
                    player_stats = box_score.get("PlayerGames", []) or box_score.get("playerGames", [])

                    for row in player_stats:
                        if not isinstance(row, dict):
                            continue

                        player_id = row.get("PlayerID") or row.get("playerId") or 0
                        if int(player_id) not in set(player_ids):
                            continue

                        results.append(
                            PlayerGameStats(
                                player_id=int(player_id),
                                player_name=self._extract_player_name(row),
                                team_id=int(row.get("TeamID") or row.get("teamId") or 0),
                                team_abbreviation=self._normalize_team_abbr(
                                    row.get("Team", row.get("team", "UNK"))
                                ),
                                game_id=int(game_id),
                                game_date=target_date,
                                minutes=float(row.get("Minutes") or row.get("minutes") or 0),
                                points=float(row.get("Points") or row.get("points") or 0),
                                rebounds=float(row.get("Rebounds") or row.get("rebounds") or 0),
                                assists=float(row.get("Assists") or row.get("assists") or 0),
                                threes=float(row.get("ThreePointersMade") or row.get("fg3m") or 0),
                                steals=float(row.get("Steals") or row.get("steals") or 0),
                                blocks=float(row.get("BlockedShots") or row.get("blocks") or 0),
                            )
                        )

                except Exception as exc:
                    logger.warning("Failed to fetch box score for game %s: %s", game_id, exc)
                    continue

            logger.info("SportsDataIO fetched %d stat rows for date %s", len(results), target_date)
            return results

        except Exception as exc:
            logger.warning("SportsDataIO date-specific stats fetch failed: %s", exc)
            raise SportsDataIOError(f"Failed to fetch date stats: {exc}") from exc

    def get_team_injuries(self) -> list[Injury]:
        """
        Fetch current injury reports.

        Returns:
            List of Injury objects
        """
        try:
            # Endpoint: /scores/json/InjuredPlayers
            data = self._request("scores/json/InjuredPlayers")

            if not isinstance(data, list):
                logger.warning("SportsDataIO injuries response not a list: %s", type(data))
                return []

            injuries: list[Injury] = []
            for row in data:
                if not isinstance(row, dict):
                    continue

                first_name = str(row.get("FirstName", "") or row.get("firstName", "")).strip()
                last_name = str(row.get("LastName", "") or row.get("lastName", "")).strip()

                injuries.append(
                    Injury(
                        player_id=int(row.get("PlayerID") or row.get("playerId") or 0),
                        player_name=f"{first_name} {last_name}".strip(),
                        team_id=int(row.get("TeamID") or row.get("teamId") or 0),
                        team_abbreviation=self._normalize_team_abbr(
                            row.get("Team", row.get("team", "UNK"))
                        ),
                        status=str(row.get("Status", "Unknown") or row.get("injuryStatus", "Unknown")),
                        description=str(row.get("Description", "") or row.get("injuryDescription", "")),
                    )
                )

            logger.info("SportsDataIO fetched %d injuries", len(injuries))
            return injuries

        except Exception as exc:
            logger.warning("SportsDataIO injuries fetch failed: %s", exc)
            # Return empty list for injuries (non-critical)
            return []

    def get_player_props_for_game(
        self,
        game_id: int,
        vendors: list[str] | None = None,
        prop_types: list[str] | None = None,
    ) -> list[MarketProp]:
        """
        Fetch player props (betting odds) for a specific game.

        Note: SportsDataIO has limited betting odds coverage.
        This method attempts to fetch available odds data.

        Args:
            game_id: Game ID
            vendors: List of betting vendors (optional)
            prop_types: List of prop types to filter (optional)

        Returns:
            List of MarketProp objects (may be empty if odds unavailable)
        """
        try:
            # SportsDataIO betting endpoint (if available in subscription)
            # This is a best-effort implementation
            props: list[MarketProp] = []

            # Attempt to fetch game odds
            # Endpoint: /odds/json/GameOddsByGameID/{game_id}
            try:
                odds_data = self._request(f"odds/json/GameOddsByGameID/{game_id}")

                if isinstance(odds_data, dict):
                    # Parse player props if available
                    player_props = odds_data.get("PlayerProps", []) or odds_data.get("playerProps", [])

                    for row in player_props:
                        if not isinstance(row, dict):
                            continue

                        prop_type = self._normalize_prop_type(
                            str(row.get("PropType", "") or row.get("propType", ""))
                        )

                        if prop_types and prop_type not in prop_types:
                            continue

                        vendor = str(row.get("Sportsbook", "") or row.get("sportsbook", "unknown")).lower()
                        if vendors and vendor not in [v.lower() for v in vendors]:
                            continue

                        props.append(
                            MarketProp(
                                id=int(row.get("PlayerPropID") or row.get("id") or 0),
                                game_id=int(game_id),
                                player_id=int(row.get("PlayerID") or row.get("playerId") or 0),
                                player_name=str(row.get("PlayerName", "") or row.get("playerName", "")),
                                vendor=vendor,
                                prop_type=prop_type,
                                line_value=float(row.get("Line", 0) or row.get("line", 0)),
                                market_type="over_under",
                                over_odds=self._safe_int(row.get("OverOdds") or row.get("overOdds")),
                                under_odds=self._safe_int(row.get("UnderOdds") or row.get("underOdds")),
                                updated_at=str(row.get("Updated", "") or row.get("updated", "")),
                            )
                        )

            except SportsDataIOError as exc:
                logger.warning("SportsDataIO odds not available for game %s: %s", game_id, exc)
                # Odds are optional, return empty list

            logger.info("SportsDataIO fetched %d props for game %s", len(props), game_id)
            return props

        except Exception as exc:
            logger.warning("SportsDataIO player props fetch failed: %s", exc)
            # Return empty list - odds are optional
            return []

    # -------------------------------------------------------------------------
    # Helper methods
    # -------------------------------------------------------------------------

    def _normalize_team_abbr(self, team_name: str | None) -> str:
        """Normalize team name to abbreviation."""
        if not team_name:
            return "UNK"

        # SportsDataIO team name to abbreviation mapping
        name_to_abbr = {
            "Atlanta Hawks": "ATL",
            "Boston Celtics": "BOS",
            "Brooklyn Nets": "BKN",
            "Charlotte Hornets": "CHA",
            "Chicago Bulls": "CHI",
            "Cleveland Cavaliers": "CLE",
            "Dallas Mavericks": "DAL",
            "Denver Nuggets": "DEN",
            "Detroit Pistons": "DET",
            "Golden State Warriors": "GSW",
            "Houston Rockets": "HOU",
            "Indiana Pacers": "IND",
            "LA Clippers": "LAC",
            "Los Angeles Clippers": "LAC",
            "Los Angeles Lakers": "LAL",
            "Memphis Grizzlies": "MEM",
            "Miami Heat": "MIA",
            "Milwaukee Bucks": "MIL",
            "Minnesota Timberwolves": "MIN",
            "New Orleans Pelicans": "NOP",
            "New York Knicks": "NYK",
            "Oklahoma City Thunder": "OKC",
            "Orlando Magic": "ORL",
            "Philadelphia 76ers": "PHI",
            "Phoenix Suns": "PHX",
            "Portland Trail Blazers": "POR",
            "Sacramento Kings": "SAC",
            "San Antonio Spurs": "SAS",
            "Toronto Raptors": "TOR",
            "Utah Jazz": "UTA",
            "Washington Wizards": "WAS",
        }

        # Direct lookup
        abbr = name_to_abbr.get(str(team_name).strip())
        if abbr:
            return abbr

        # If already an abbreviation (2-4 chars uppercase), return as-is
        cleaned = str(team_name).strip().upper()
        if 2 <= len(cleaned) <= 4 and cleaned.isalpha():
            return cleaned

        return "UNK"

    def _extract_player_name(self, row: dict[str, Any]) -> str:
        """Extract player name from response row."""
        first = str(row.get("FirstName", "") or row.get("firstName", "")).strip()
        last = str(row.get("LastName", "") or row.get("lastName", "")).strip()

        if first and last:
            return f"{first} {last}".strip()

        # Fallback to Name field if present
        name = str(row.get("Name", "") or row.get("name", "")).strip()
        if name:
            return name

        # Fallback to PlayerName
        player_name = str(row.get("PlayerName", "") or row.get("playerName", "")).strip()
        if player_name:
            return player_name

        return "Unknown"

    def _normalize_prop_type(self, prop_type: str) -> str:
        """Normalize prop type to internal schema."""
        prop_type_lower = prop_type.lower().strip()

        mappings = {
            "points": "points",
            "pts": "points",
            "rebounds": "rebounds",
            "reb": "rebounds",
            "assists": "assists",
            "ast": "assists",
            "threes": "threes",
            "three pointers": "threes",
            "3pt": "threes",
            "steals": "steals",
            "stl": "steals",
            "blocks": "blocks",
            "blk": "blocks",
        }

        return mappings.get(prop_type_lower, prop_type_lower)

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        """Safely convert value to int, returning None if invalid."""
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None


__all__ = [
    "SportsDataIOClient",
    "SportsDataIOError",
    "SportsDataIOAuthError",
    "SportsDataIORateLimitError",
    "SPORTSDATAIO_API_KEY_ENV_VAR",
    "SPORTSDATAIO_BASE_URL_ENV_VAR",
]
