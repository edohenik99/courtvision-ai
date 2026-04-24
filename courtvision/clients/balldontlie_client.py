from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

import requests
from balldontlie import BalldontlieAPI

from courtvision.balldontlie_auth import (
    BALLDONTLIE_API_KEY_ENV_VAR,
    build_missing_key_message,
    build_unauthorized_message,
    clean_api_key,
    env_key_debug_details,
    prepared_url as shared_prepared_url,
    resolve_api_key,
    response_text_preview as shared_response_text_preview,
)
from courtvision.config import Settings
from courtvision.models import Game, Injury, MarketProp, PlayerGameStats, PlayerInfo, Team


class BalldontlieClient:
    def __init__(self, settings: Settings) -> None:
        api_key, api_key_details = resolve_api_key(
            entrypoint="courtvision.clients.balldontlie_client",
            env_var_name=BALLDONTLIE_API_KEY_ENV_VAR,
            current_value=settings.api_key,
        )
        self.settings = Settings(
            api_key=api_key,
            base_url=settings.base_url,
            per_page=settings.per_page,
            request_timeout_seconds=settings.request_timeout_seconds,
        )
        self.api_key_details = dict(api_key_details)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": api_key,
                "Accept": "application/json",
            }
        )
        self.sdk = BalldontlieAPI(api_key=api_key)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.settings.api_key:
            raise RuntimeError(
                build_missing_key_message(
                    entrypoint="courtvision.clients.balldontlie_client",
                    details=env_key_debug_details(BALLDONTLIE_API_KEY_ENV_VAR, self.settings.api_key),
                )
            )

        url = f"{self.settings.base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(3):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.settings.request_timeout_seconds,
                )
                if response.status_code == 401:
                    raise RuntimeError(self._format_http_error(response=response, url=url, params=params))
                if response.status_code == 429 and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
                    continue

        if last_error is not None:
            raise last_error
        return {}

    def _get_absolute(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.settings.api_key:
            raise RuntimeError(
                build_missing_key_message(
                    entrypoint="courtvision.clients.balldontlie_client",
                    details=env_key_debug_details(BALLDONTLIE_API_KEY_ENV_VAR, self.settings.api_key),
                )
            )

        last_error: Exception | None = None

        for attempt in range(3):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.settings.request_timeout_seconds,
                )
                if response.status_code == 401:
                    raise RuntimeError(self._format_http_error(response=response, url=url, params=params))
                if response.status_code == 429 and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
                    continue

        if last_error is not None:
            raise last_error
        return {}

    def _format_http_error(
        self,
        response: requests.Response,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        prepared_url = self._prepared_url(url, params)
        response_url = str(getattr(response, "url", "") or prepared_url or url)
        has_auth = bool(self.session.headers.get("Authorization"))
        body_preview = self._response_text_preview(response)
        if response.status_code == 401:
            return build_unauthorized_message(
                entrypoint="courtvision.clients.balldontlie_client",
                env_var_name=BALLDONTLIE_API_KEY_ENV_VAR,
                masked_key_preview=self.api_key_details.get("masked_preview", "<empty>"),
                url=response_url,
                params=params,
                has_auth=has_auth,
                body_snippet=body_preview,
                details=self.api_key_details,
            )
        return (
            "BallDontLie request failed "
            f"status={response.status_code} "
            f"url={response_url} "
            f"params={params or {}} "
            f"has_auth={has_auth} "
            f"key={self.api_key_details.get('masked_preview', '<empty>')} "
            f"body={body_preview}"
        )

    @staticmethod
    def _prepared_url(url: str, params: dict[str, Any] | None = None) -> str:
        return shared_prepared_url(url, params)

    @staticmethod
    def _response_text_preview(response: requests.Response, limit: int = 300) -> str:
        return shared_response_text_preview(response, limit=limit)

    def _paginate(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        cursor: int | None = None

        while True:
            page_params = dict(params or {})
            page_params.setdefault("per_page", self.settings.per_page)
            if cursor is not None:
                page_params["cursor"] = cursor

            payload = self._get(path, page_params)
            rows = payload.get("data", [])
            if not isinstance(rows, list):
                break

            merged.extend(rows)
            meta = payload.get("meta", {})
            cursor = meta.get("next_cursor")
            if cursor in (None, "", 0):
                break

        return merged

    def get_games_by_date(self, target_date: str) -> list[Game]:
        rows = self._paginate("/games", {"dates[]": target_date})
        games: list[Game] = []

        for row in rows:
            home = row.get("home_team", {})
            away = row.get("visitor_team", {})
            games.append(
                Game(
                    id=int(row["id"]),
                    date=str(row.get("date", target_date)),
                    home_team=Team(
                        id=int(home["id"]),
                        abbreviation=str(home.get("abbreviation", "UNK")),
                        full_name=str(home.get("full_name", "Unknown")),
                    ),
                    visitor_team=Team(
                        id=int(away["id"]),
                        abbreviation=str(away.get("abbreviation", "UNK")),
                        full_name=str(away.get("full_name", "Unknown")),
                    ),
                    home_team_score=row.get("home_team_score"),
                    visitor_team_score=row.get("visitor_team_score"),
                    status=str(row.get("status", "")),
                )
            )

        return games

    def get_team_injuries(self) -> list[Injury]:
        try:
            response = self.sdk.nba.injuries.list()
        except Exception as exc:
            print(f"[WARNING] Injury fetch failed: {exc}")
            return []

        raw_rows = response.data if hasattr(response, "data") else response
        injuries: list[Injury] = []

        for row in raw_rows:
            player = getattr(row, "player", None)
            team = getattr(row, "team", None)

            if isinstance(row, dict):
                player = row.get("player", {})
                team = row.get("team", {})
                status = str(row.get("status", "Unknown"))
                description = str(row.get("description", ""))
            else:
                status = str(getattr(row, "status", "Unknown"))
                description = str(getattr(row, "description", ""))

            if isinstance(player, dict):
                player_id = int(player.get("id", 0) or 0)
                first_name = str(player.get("first_name", "")).strip()
                last_name = str(player.get("last_name", "")).strip()
            else:
                player_id = int(getattr(player, "id", 0) or 0)
                first_name = str(getattr(player, "first_name", "")).strip()
                last_name = str(getattr(player, "last_name", "")).strip()

            if isinstance(team, dict):
                team_id = int(team.get("id", 0) or 0)
                team_abbreviation = str(team.get("abbreviation", "UNK"))
            else:
                team_id = int(getattr(team, "id", 0) or 0)
                team_abbreviation = str(getattr(team, "abbreviation", "UNK"))

            injuries.append(
                Injury(
                    player_id=player_id,
                    player_name=f"{first_name} {last_name}".strip(),
                    team_id=team_id,
                    team_abbreviation=team_abbreviation,
                    status=status,
                    description=description,
                )
            )

        return injuries

    def get_active_players_for_team_ids(self, team_ids: set[int]) -> list[PlayerInfo]:
        players: list[PlayerInfo] = []
        cursor: int | None = None

        while True:
            kwargs: dict[str, Any] = {"per_page": 100}
            if cursor is not None:
                kwargs["cursor"] = cursor

            response = self.sdk.nba.players.list_active(**kwargs)
            rows = response.data if hasattr(response, "data") else response
            meta = getattr(response, "meta", None)

            for row in rows:
                team = getattr(row, "team", None)
                if isinstance(row, dict):
                    team = row.get("team", {})
                    player_team_id = int(team.get("id", 0) or 0)
                    if player_team_id not in team_ids:
                        continue
                    first_name = str(row.get("first_name", "")).strip()
                    last_name = str(row.get("last_name", "")).strip()
                    players.append(
                        PlayerInfo(
                            id=int(row.get("id", 0)),
                            first_name=first_name,
                            last_name=last_name,
                            full_name=f"{first_name} {last_name}".strip(),
                            team_id=player_team_id,
                            team_abbreviation=str(team.get("abbreviation", "UNK")),
                            position=str(row.get("position", "") or ""),
                        )
                    )
                else:
                    player_team_id = int(getattr(team, "id", 0) or 0)
                    if player_team_id not in team_ids:
                        continue
                    first_name = str(getattr(row, "first_name", "")).strip()
                    last_name = str(getattr(row, "last_name", "")).strip()
                    players.append(
                        PlayerInfo(
                            id=int(getattr(row, "id", 0) or 0),
                            first_name=first_name,
                            last_name=last_name,
                            full_name=f"{first_name} {last_name}".strip(),
                            team_id=player_team_id,
                            team_abbreviation=str(getattr(team, "abbreviation", "UNK")),
                            position=str(getattr(row, "position", "") or ""),
                        )
                    )

            next_cursor: int | None = None
            if meta is not None:
                if isinstance(meta, dict):
                    next_cursor = meta.get("next_cursor")
                else:
                    next_cursor = getattr(meta, "next_cursor", None)

            if next_cursor in (None, "", 0):
                break
            cursor = int(next_cursor)

        return players

    def get_stats_for_player_ids(self, player_ids: Iterable[int], seasons: list[int]) -> list[PlayerGameStats]:
        results: list[PlayerGameStats] = []
        unique_ids = sorted({int(pid) for pid in player_ids if pid})

        for player_id in unique_ids:
            rows = self._paginate(
                "/stats",
                {
                    "player_ids[]": player_id,
                    "seasons[]": seasons[0],
                },
            )

            for row in rows:
                player = row.get("player", {})
                team = row.get("team", {})
                game = row.get("game", {})
                results.append(
                    PlayerGameStats(
                        player_id=int(player.get("id", player_id)),
                        player_name=(
                            str(player.get("first_name", "")).strip()
                            + " "
                            + str(player.get("last_name", "")).strip()
                        ).strip(),
                        team_id=int(team.get("id", 0)),
                        team_abbreviation=str(team.get("abbreviation", "UNK")),
                        game_id=int(game.get("id", 0)),
                        game_date=str(game.get("date", "")),
                        minutes=_parse_minutes(row.get("min")),
                        points=float(row.get("pts", 0) or 0),
                        rebounds=float(row.get("reb", 0) or 0),
                        assists=float(row.get("ast", 0) or 0),
                        threes=float(row.get("fg3m", 0) or 0),
                        steals=float(row.get("stl", 0) or 0),
                        blocks=float(row.get("blk", 0) or 0),
                    )
                )

        return results

    def get_stats_for_player_ids_on_date(
        self,
        player_ids: Iterable[int],
        target_date: str,
        season: int | None = None,
    ) -> list[PlayerGameStats]:
        results: list[PlayerGameStats] = []
        unique_ids = sorted({int(pid) for pid in player_ids if pid})

        for player_id in unique_ids:
            params: dict[str, Any] = {
                "player_ids[]": player_id,
                "start_date": target_date,
                "end_date": target_date,
            }
            if season is not None:
                params["seasons[]"] = season

            rows = self._paginate("/stats", params)

            for row in rows:
                player = row.get("player", {})
                team = row.get("team", {})
                game = row.get("game", {})
                results.append(
                    PlayerGameStats(
                        player_id=int(player.get("id", player_id)),
                        player_name=(
                            str(player.get("first_name", "")).strip()
                            + " "
                            + str(player.get("last_name", "")).strip()
                        ).strip(),
                        team_id=int(team.get("id", 0)),
                        team_abbreviation=str(team.get("abbreviation", "UNK")),
                        game_id=int(game.get("id", 0)),
                        game_date=str(game.get("date", target_date)),
                        minutes=_parse_minutes(row.get("min")),
                        points=float(row.get("pts", 0) or 0),
                        rebounds=float(row.get("reb", 0) or 0),
                        assists=float(row.get("ast", 0) or 0),
                        threes=float(row.get("fg3m", 0) or 0),
                        steals=float(row.get("stl", 0) or 0),
                        blocks=float(row.get("blk", 0) or 0),
                    )
                )

        return results

    def get_player_props_for_game(
        self,
        game_id: int,
        vendors: list[str] | None = None,
        prop_types: list[str] | None = None,
    ) -> list[MarketProp]:
        params: dict[str, Any] = {"game_id": game_id}
        if vendors:
            params["vendors[]"] = vendors
        if prop_types:
            params["prop_type"] = prop_types[0] if len(prop_types) == 1 else None

        payload = self._get_absolute(
            "https://api.balldontlie.io/v2/odds/player_props",
            params={k: v for k, v in params.items() if v is not None},
        )
        rows = payload.get("data", [])
        if not isinstance(rows, list):
            return []

        props: list[MarketProp] = []
        allowed_prop_types = set(prop_types or ["points", "rebounds", "assists", "threes", "steals", "blocks"])

        for row in rows:
            prop_type = str(row.get("prop_type", "")).strip().lower()
            if prop_type not in allowed_prop_types:
                continue

            if isinstance(row, dict):
                player = row.get("player", {}) if isinstance(row.get("player"), dict) else {}
                first_name = str(player.get("first_name", "")).strip()
                last_name = str(player.get("last_name", "")).strip()
                player_name = (
                    str(row.get("player_name", "") or "").strip()
                    or f"{first_name} {last_name}".strip()
                    or str(player.get("full_name", "") or "").strip()
                    or str(player.get("name", "") or "").strip()
                )
                market = row.get("market", {}) if isinstance(row.get("market"), dict) else {}
                player_id = int(row.get("player_id") or player.get("id", 0) or 0)
                vendor = str(row.get("vendor", "") or "")
                line_value = float(row.get("line_value", 0) or 0)
                updated_at = str(row.get("updated_at", "") or "")
            else:
                player_name = str(getattr(row, "player_name", None) or "").strip()
                if not player_name:
                    player = getattr(row, "player", None)
                    if player and hasattr(player, "first_name") and hasattr(player, "last_name"):
                        player_name = f"{player.first_name} {player.last_name}".strip()
                market = getattr(row, "market", None)
                if not isinstance(market, dict):
                    market = {}
                player_id = int(getattr(row, "player_id", 0) or 0)
                vendor = str(getattr(row, "vendor", "") or "")
                line_value = float(getattr(row, "line_value", 0) or 0)
                updated_at = str(getattr(row, "updated_at", "") or "")

            market_type = str(market.get("type", "") or "over_under")

            props.append(
                MarketProp(
                    id=int(row.get("id") if isinstance(row, dict) else getattr(row, "id", 0)),
                    game_id=int(
                        row.get("game_id") if isinstance(row, dict) else getattr(row, "game_id", game_id)
                    ),
                    player_id=player_id,
                    player_name=player_name,
                    vendor=vendor,
                    prop_type=prop_type,
                    line_value=line_value,
                    market_type=market_type,
                    over_odds=_safe_int(market.get("over_odds")),
                    under_odds=_safe_int(market.get("under_odds")),
                    updated_at=updated_at,
                )
            )

        return props


def _parse_minutes(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return 0.0

    if ":" in text:
        minutes_text, seconds_text = text.split(":", 1)
        try:
            return float(minutes_text) + float(seconds_text) / 60.0
        except ValueError:
            return 0.0

    try:
        return float(text)
    except ValueError:
        return 0.0


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
