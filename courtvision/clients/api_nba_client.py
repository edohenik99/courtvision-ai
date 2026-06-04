"""Thin API-NBA stats client for CourtVision Research Mode.

API-NBA is stats-only in this repo. This client intentionally does not create
MarketProp rows, betting lines, Kelly inputs, or Elite-board candidates.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from courtvision.models import Game, PlayerGameStats, PlayerInfo, Team
from courtvision.providers.research_schedule_resolver import (
    DEFAULT_MANUAL_SCHEDULE_DIR,
    DEFAULT_RUNTIME_ROOT,
    SOURCE_API_NBA,
    SOURCE_MANUAL_SCHEDULE,
    resolve_research_schedule,
)

LOG = logging.getLogger("courtvision.api_nba")

API_NBA_KEY_ENV = "API_NBA_KEY"
API_SPORTS_KEY_ENV = "API_SPORTS_KEY"
DEFAULT_BASE_URL = "https://v2.nba.api-sports.io"
DEFAULT_TIMEOUT_SECONDS = 30
RESEARCH_MODE = "research"
ELIGIBLE_FOR_BETTING = False


class ApiNbaError(Exception):
    """Base exception for API-NBA client internals."""


@dataclass(slots=True)
class ResearchTeam(Team):
    source: str = SOURCE_API_NBA
    mode: str = RESEARCH_MODE
    eligible_for_betting: bool = ELIGIBLE_FOR_BETTING


@dataclass(slots=True)
class ResearchGame(Game):
    source: str = SOURCE_API_NBA
    mode: str = RESEARCH_MODE
    eligible_for_betting: bool = ELIGIBLE_FOR_BETTING


@dataclass(slots=True)
class ResearchPlayerInfo(PlayerInfo):
    source: str = SOURCE_API_NBA
    mode: str = RESEARCH_MODE
    eligible_for_betting: bool = ELIGIBLE_FOR_BETTING


@dataclass(slots=True)
class ResearchPlayerGameStats(PlayerGameStats):
    source: str = SOURCE_API_NBA
    mode: str = RESEARCH_MODE
    eligible_for_betting: bool = ELIGIBLE_FOR_BETTING


class ApiNbaClient:
    """Stats-only API-NBA client for isolated Research Mode usage."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
        manual_schedule_dir: str | Path = DEFAULT_MANUAL_SCHEDULE_DIR,
        use_cache: bool = True,
    ) -> None:
        self.api_key, self.api_key_source = resolve_api_nba_key(api_key)
        self.base_url = str(base_url).rstrip("/")
        self.timeout_seconds = int(timeout_seconds)
        self.runtime_root = Path(runtime_root)
        self.cache_dir = self.runtime_root / "cache" / "api_nba"
        self.manual_schedule_dir = Path(manual_schedule_dir)
        self.use_cache = bool(use_cache)
        self.session = requests.Session()
        self.session.headers.clear()
        self.session.headers.update({"Accept": "application/json"})
        if self.api_key:
            self.session.headers.update({"x-apisports-key": self.api_key})
        self._last_request_status: dict[str, Any] = {
            "provider": SOURCE_API_NBA,
            "has_credentials": bool(self.api_key),
            "provider_status": "unrequested",
        }

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def get_provider_status(self) -> dict[str, Any]:
        return dict(self._last_request_status)

    def research_metadata(self) -> dict[str, Any]:
        return {
            "source": SOURCE_API_NBA,
            "mode": RESEARCH_MODE,
            "eligible_for_betting": ELIGIBLE_FOR_BETTING,
        }

    def get_games_by_date(self, target_date: str) -> list[Game]:
        target_date_text = str(target_date)
        body = self._request("games", {"date": target_date_text})
        schedule_result = resolve_research_schedule(
            target_date_text,
            body,
            manual_schedule_dir=self.manual_schedule_dir,
            runtime_root=self.runtime_root,
        )

        if schedule_result.selected_source == SOURCE_API_NBA:
            games = [
                self._map_game(row, target_date_text)
                for row in _response_items(body)
                if _game_date(row) == target_date_text
            ]
            if games:
                return games

        if schedule_result.selected_source == SOURCE_MANUAL_SCHEDULE:
            return self._games_from_schedule_rows(schedule_result.schedule.to_dict("records"))

        return []

    def get_teams(self) -> list[Team]:
        body = self._request("teams", {"league": "standard"})
        return [self._map_team(row) for row in _response_items(body)]

    def get_players(self, season: int) -> list[PlayerInfo]:
        body = self._request("players", {"season": int(season)})
        return [self._map_player(row) for row in _response_items(body)]

    def get_player_stats_for_game(
        self,
        game_id: int,
        game_date: str | None = None,
    ) -> list[PlayerGameStats]:
        body = self._request("players/statistics", {"game": int(game_id)})
        return [
            self._map_player_game_stats(row, fallback_game_id=int(game_id), fallback_game_date=game_date)
            for row in _response_items(body)
        ]

    def get_player_stats_for_date(
        self,
        target_date: str,
        season: int | None = None,
    ) -> list[PlayerGameStats]:
        del season  # API-NBA player stats are fetched by game id for date-level safety.
        target_date_text = str(target_date)
        results: list[PlayerGameStats] = []
        for game in self.get_games_by_date(target_date_text):
            game_id = _safe_int(getattr(game, "id", 0), default=0)
            if game_id <= 0:
                LOG.warning("Skipping API-NBA stats fetch for nonnumeric game_id=%s", game.id)
                continue
            results.extend(self.get_player_stats_for_game(game_id, game_date=target_date_text))
        return results

    def get_player_props_for_game(
        self,
        game_id: int,
        vendors: list[str] | None = None,
        prop_types: list[str] | None = None,
    ) -> list[Any]:
        del game_id, vendors, prop_types
        return []

    def _request(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_params = dict(params or {})
        status_key = f"{endpoint}?{json.dumps(request_params, sort_keys=True, default=str)}"

        if not self.api_key:
            self._last_request_status = {
                "provider": SOURCE_API_NBA,
                "endpoint": endpoint,
                "provider_status": "missing_credentials",
                "has_credentials": False,
            }
            return {}

        cache_path = self._cache_path(endpoint, request_params)
        if self.use_cache:
            cached = self._read_cache(cache_path)
            if cached is not None:
                self._last_request_status = {
                    "provider": SOURCE_API_NBA,
                    "endpoint": endpoint,
                    "provider_status": "ok",
                    "has_credentials": True,
                    "cache_hit": True,
                    "cache_key": status_key,
                }
                return cached

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            response = self.session.get(url, params=request_params, timeout=self.timeout_seconds)
        except requests.exceptions.Timeout:
            self._set_request_status(endpoint, "timeout", -1, cache_hit=False)
            return {}
        except requests.RequestException as exc:
            self._set_request_status(endpoint, f"request_error: {exc}", -1, cache_hit=False)
            return {}

        http_status = int(getattr(response, "status_code", -1) or -1)
        body, parse_error = _parse_response_json(response)
        provider_status = _provider_status(http_status, body, parse_error)
        self._set_request_status(endpoint, provider_status, http_status, cache_hit=False)

        if provider_status != "ok":
            return {}

        if self.use_cache:
            self._write_cache(cache_path, body)

        return body

    def _set_request_status(
        self,
        endpoint: str,
        provider_status: str,
        http_status: int,
        *,
        cache_hit: bool,
    ) -> None:
        self._last_request_status = {
            "provider": SOURCE_API_NBA,
            "endpoint": endpoint,
            "provider_status": provider_status,
            "http_status": http_status,
            "has_credentials": bool(self.api_key),
            "cache_hit": cache_hit,
        }

    def _cache_path(self, endpoint: str, params: dict[str, Any]) -> Path:
        payload = json.dumps(
            {"endpoint": endpoint.lstrip("/"), "params": params},
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        safe_endpoint = endpoint.strip("/").replace("/", "_") or "root"
        return self.cache_dir / f"{safe_endpoint}_{digest}.json"

    @staticmethod
    def _read_cache(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _write_cache(path: Path, payload: dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        except Exception as exc:
            LOG.warning("API-NBA cache write failed path=%s error=%s", path, exc)

    def _map_game(self, row: dict[str, Any], target_date: str) -> ResearchGame:
        home = _path_value(row, "teams.home")
        visitors = _path_value(row, "teams.visitors")
        status = _first_text(_path_value(row, "status.long"), _path_value(row, "status.short"), row.get("status"))
        return ResearchGame(
            id=_safe_int(row.get("id"), default=0),
            date=_game_date(row) or target_date,
            home_team=self._map_team(home if isinstance(home, dict) else {}),
            visitor_team=self._map_team(visitors if isinstance(visitors, dict) else {}),
            home_team_score=_safe_optional_int(_path_value(row, "scores.home.points")),
            visitor_team_score=_safe_optional_int(_path_value(row, "scores.visitors.points")),
            status=status,
            source=SOURCE_API_NBA,
        )

    def _games_from_schedule_rows(self, rows: list[dict[str, Any]]) -> list[Game]:
        games: list[Game] = []
        for row in rows:
            source = _first_text(row.get("source")) or SOURCE_MANUAL_SCHEDULE
            games.append(
                ResearchGame(
                    id=_safe_int(row.get("game_id"), default=0),
                    date=_first_text(row.get("game_date")),
                    home_team=ResearchTeam(
                        id=0,
                        abbreviation=_first_text(row.get("home_team_abbr")) or "UNK",
                        full_name=_first_text(row.get("home_team")) or "Unknown",
                        source=source,
                    ),
                    visitor_team=ResearchTeam(
                        id=0,
                        abbreviation=_first_text(row.get("away_team_abbr")) or "UNK",
                        full_name=_first_text(row.get("away_team")) or "Unknown",
                        source=source,
                    ),
                    status="research_manual_schedule",
                    source=source,
                )
            )
        return games

    @staticmethod
    def _map_team(row: dict[str, Any]) -> ResearchTeam:
        return ResearchTeam(
            id=_safe_int(row.get("id"), default=0),
            abbreviation=_first_text(row.get("code"), row.get("abbreviation")) or "UNK",
            full_name=_first_text(row.get("name"), row.get("full_name")) or "Unknown",
            source=SOURCE_API_NBA,
        )

    @staticmethod
    def _map_player(row: dict[str, Any]) -> ResearchPlayerInfo:
        first_name = _first_text(row.get("firstname"), row.get("first_name"))
        last_name = _first_text(row.get("lastname"), row.get("last_name"))
        full_name = _first_text(row.get("name"), row.get("full_name"), f"{first_name} {last_name}".strip())
        team = row.get("team") if isinstance(row.get("team"), dict) else {}
        standard = _path_value(row, "leagues.standard")
        if not isinstance(standard, dict):
            standard = {}
        return ResearchPlayerInfo(
            id=_safe_int(row.get("id"), default=0),
            first_name=first_name,
            last_name=last_name,
            full_name=full_name,
            team_id=_safe_int(_first_value(team.get("id"), _path_value(standard, "team.id")), default=0),
            team_abbreviation=_first_text(team.get("code"), team.get("abbreviation")) or "UNK",
            position=_first_text(standard.get("pos"), row.get("position")),
            source=SOURCE_API_NBA,
        )

    @staticmethod
    def _map_player_game_stats(
        row: dict[str, Any],
        *,
        fallback_game_id: int,
        fallback_game_date: str | None,
    ) -> ResearchPlayerGameStats:
        player = row.get("player") if isinstance(row.get("player"), dict) else {}
        team = row.get("team") if isinstance(row.get("team"), dict) else {}
        game = row.get("game") if isinstance(row.get("game"), dict) else {}
        first_name = _first_text(player.get("firstname"), player.get("first_name"))
        last_name = _first_text(player.get("lastname"), player.get("last_name"))
        player_name = _first_text(player.get("name"), f"{first_name} {last_name}".strip(), row.get("player_name"))

        return ResearchPlayerGameStats(
            player_id=_safe_int(player.get("id"), default=0),
            player_name=player_name or "Unknown",
            team_id=_safe_int(team.get("id"), default=0),
            team_abbreviation=_first_text(team.get("code"), team.get("abbreviation")) or "UNK",
            game_id=_safe_int(game.get("id"), default=fallback_game_id),
            game_date=_first_text(game.get("date"), fallback_game_date),
            minutes=_parse_minutes(_first_value(row.get("min"), row.get("minutes"))),
            points=_safe_float(row.get("points")),
            rebounds=_safe_float(_first_value(row.get("totReb"), row.get("rebounds"), row.get("reb"))),
            assists=_safe_float(row.get("assists")),
            threes=_safe_float(_first_value(row.get("tpm"), row.get("fg3m"), row.get("threes"))),
            steals=_safe_float(row.get("steals")),
            blocks=_safe_float(row.get("blocks")),
            source=SOURCE_API_NBA,
        )


def resolve_api_nba_key(provided: str | None = None) -> tuple[str, str]:
    cleaned = _clean_key(provided)
    if cleaned:
        return cleaned, "provided"

    for env_var in (API_NBA_KEY_ENV, API_SPORTS_KEY_ENV):
        value = _clean_key(os.getenv(env_var))
        if value:
            return value, env_var
    return "", "<not found>"


def _provider_status(
    http_status: int,
    body: dict[str, Any] | None = None,
    error: str | None = None,
) -> str:
    if http_status == 200:
        if error:
            return "malformed_response"
        if not isinstance(body, dict):
            return "empty_response"
        errors = body.get("errors")
        if errors not in (None, "", [], {}):
            return "provider_error"
        return "ok"
    return {
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        408: "timeout",
        429: "rate_limited",
        500: "server_error",
        502: "bad_gateway",
        503: "unavailable",
        504: "gateway_timeout",
        -1: "connection_error",
    }.get(http_status, f"http_{http_status}")


def _parse_response_json(response: requests.Response) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = response.json()
    except Exception as exc:
        return None, f"json_parse_error: {exc}"
    if isinstance(payload, dict):
        return payload, None
    return None, f"unexpected_json_type: {type(payload).__name__}"


def _response_items(body: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    response = body.get("response")
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if isinstance(response, dict):
        return [response]
    return []


def _game_date(row: dict[str, Any]) -> str:
    return _first_text(_path_value(row, "date.start"), _path_value(row, "game.date"), row.get("date"))[:10]


def _path_value(item: dict[str, Any], path: str) -> Any:
    current: Any = item
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _first_text(*values: Any) -> str:
    value = _first_value(*values)
    return "" if value is None else str(value).strip()


def _clean_key(value: str | None) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] in {"'", '"'} and text[-1] == text[0]:
        text = text[1:-1].strip()
    return text


def _safe_int(value: Any, *, default: int) -> int:
    if value is None or value == "":
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_minutes(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if ":" in text:
        minutes_text, seconds_text = text.split(":", 1)
        try:
            return float(minutes_text) + float(seconds_text) / 60.0
        except ValueError:
            return 0.0
    return _safe_float(text)


__all__ = [
    "API_NBA_KEY_ENV",
    "API_SPORTS_KEY_ENV",
    "ApiNbaClient",
    "ApiNbaError",
    "DEFAULT_BASE_URL",
    "ELIGIBLE_FOR_BETTING",
    "RESEARCH_MODE",
    "ResearchGame",
    "ResearchPlayerGameStats",
    "ResearchPlayerInfo",
    "ResearchTeam",
    "resolve_api_nba_key",
]

