from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from courtvision.clients.api_nba_client import (
    API_NBA_KEY_ENV,
    API_SPORTS_KEY_ENV,
    ApiNbaClient,
    resolve_api_nba_key,
)
from courtvision.models import Game, MarketProp, PlayerGameStats, PlayerInfo, Team


class ResponseStub:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self._payload


def _ok(rows: list[dict[str, Any]]) -> ResponseStub:
    return ResponseStub(200, {"errors": [], "results": len(rows), "response": rows})


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ApiNbaClient:
    monkeypatch.setenv(API_NBA_KEY_ENV, "api_nba_key")
    monkeypatch.delenv(API_SPORTS_KEY_ENV, raising=False)
    return ApiNbaClient(
        runtime_root=tmp_path / "outputs" / "runtime",
        manual_schedule_dir=tmp_path / "data" / "manual_schedule",
        use_cache=False,
    )


def _game_row(target_date: str, game_id: int = 10403) -> dict[str, Any]:
    return {
        "id": game_id,
        "date": {"start": f"{target_date}T00:00:00+00:00"},
        "teams": {
            "home": {"id": 1, "name": "Oklahoma City Thunder", "code": "OKC"},
            "visitors": {"id": 2, "name": "Indiana Pacers", "code": "IND"},
        },
        "scores": {"home": {"points": 111}, "visitors": {"points": 108}},
        "status": {"long": "Finished", "short": "FT"},
    }


def _stats_row(game_id: int = 10403) -> dict[str, Any]:
    return {
        "player": {"id": 265, "firstname": "Jane", "lastname": "Doe"},
        "team": {"id": 1, "code": "OKC"},
        "game": {"id": game_id},
        "min": "32:30",
        "points": 22,
        "totReb": 8,
        "assists": 6,
        "tpm": 3,
        "steals": 2,
        "blocks": 1,
    }


def test_api_key_loading_prefers_api_nba_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_NBA_KEY_ENV, "primary_key")
    monkeypatch.setenv(API_SPORTS_KEY_ENV, "fallback_key")

    key, source = resolve_api_nba_key()

    assert key == "primary_key"
    assert source == API_NBA_KEY_ENV


def test_api_key_loading_falls_back_to_api_sports_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_NBA_KEY_ENV, raising=False)
    monkeypatch.setenv(API_SPORTS_KEY_ENV, "fallback_key")

    key, source = resolve_api_nba_key()

    assert key == "fallback_key"
    assert source == API_SPORTS_KEY_ENV


@pytest.mark.parametrize(
    ("status_code", "provider_status"),
    [(401, "unauthorized"), (403, "forbidden"), (404, "not_found")],
)
def test_http_401_403_404_are_non_fatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    provider_status: str,
) -> None:
    client = _client(tmp_path, monkeypatch)
    client.session.get = MagicMock(return_value=ResponseStub(status_code, {"errors": {}, "response": []}))

    assert client.get_teams() == []
    assert client.get_provider_status()["provider_status"] == provider_status


def test_game_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target_date = "2026-04-12"
    client = _client(tmp_path, monkeypatch)
    client.session.get = MagicMock(return_value=_ok([_game_row(target_date)]))

    games = client.get_games_by_date(target_date)

    assert len(games) == 1
    game = games[0]
    assert isinstance(game, Game)
    assert game.id == 10403
    assert game.date == target_date
    assert game.home_team.abbreviation == "OKC"
    assert game.visitor_team.full_name == "Indiana Pacers"
    assert game.home_team_score == 111
    assert game.visitor_team_score == 108
    assert game.status == "Finished"
    assert game.source == "api_nba"
    assert game.mode == "research"
    assert game.eligible_for_betting is False


def test_team_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.session.get = MagicMock(
        return_value=_ok([{"id": 1, "name": "Oklahoma City Thunder", "code": "OKC"}])
    )

    teams = client.get_teams()

    assert len(teams) == 1
    team = teams[0]
    assert isinstance(team, Team)
    assert team.id == 1
    assert team.abbreviation == "OKC"
    assert team.full_name == "Oklahoma City Thunder"
    assert team.source == "api_nba"
    assert team.eligible_for_betting is False


def test_player_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.session.get = MagicMock(
        return_value=_ok(
            [
                {
                    "id": 265,
                    "firstname": "Jane",
                    "lastname": "Doe",
                    "leagues": {"standard": {"pos": "G"}},
                }
            ]
        )
    )

    players = client.get_players(2025)

    assert len(players) == 1
    player = players[0]
    assert isinstance(player, PlayerInfo)
    assert player.id == 265
    assert player.first_name == "Jane"
    assert player.last_name == "Doe"
    assert player.full_name == "Jane Doe"
    assert player.team_id == 0
    assert player.team_abbreviation == "UNK"
    assert player.position == "G"
    assert player.mode == "research"
    assert player.eligible_for_betting is False


def test_player_game_stats_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.session.get = MagicMock(return_value=_ok([_stats_row()]))

    stats = client.get_player_stats_for_game(10403, game_date="2026-04-12")

    assert len(stats) == 1
    row = stats[0]
    assert isinstance(row, PlayerGameStats)
    assert row.player_id == 265
    assert row.player_name == "Jane Doe"
    assert row.team_id == 1
    assert row.team_abbreviation == "OKC"
    assert row.game_id == 10403
    assert row.game_date == "2026-04-12"
    assert row.minutes == 32.5
    assert row.points == 22.0
    assert row.rebounds == 8.0
    assert row.assists == 6.0
    assert row.threes == 3.0
    assert row.steals == 2.0
    assert row.blocks == 1.0
    assert row.source == "api_nba"
    assert row.eligible_for_betting is False


def test_manual_schedule_fallback_compatible_with_date_stats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_date = "2026-06-03"
    manual_dir = tmp_path / "data" / "manual_schedule"
    manual_dir.mkdir(parents=True)
    (manual_dir / f"manual_games_{target_date}.csv").write_text(
        "\n".join(
            [
                "game_date,game_id,home_team,away_team,home_team_abbr,away_team_abbr,source",
                f"{target_date},10403,Oklahoma City Thunder,Indiana Pacers,OKC,IND,manual_schedule",
            ]
        ),
        encoding="utf-8",
    )

    client = ApiNbaClient(
        api_key="api_nba_key",
        runtime_root=tmp_path / "outputs" / "runtime",
        manual_schedule_dir=manual_dir,
        use_cache=False,
    )
    client.session.get = MagicMock(side_effect=[_ok([]), _ok([]), _ok([_stats_row(10403)])])

    games = client.get_games_by_date(target_date)
    stats = client.get_player_stats_for_date(target_date, season=2025)

    assert games[0].source == "manual_schedule"
    assert games[0].mode == "research"
    assert games[0].eligible_for_betting is False
    assert len(stats) == 1
    assert stats[0].game_id == 10403
    assert stats[0].game_date == target_date


def test_api_nba_client_never_returns_marketprop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)

    props = client.get_player_props_for_game(10403)

    assert props == []
    assert all(not isinstance(item, MarketProp) for item in props)


def test_cache_writes_under_runtime_cache_api_nba(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = ApiNbaClient(
        api_key="api_nba_key",
        runtime_root=tmp_path / "outputs" / "runtime",
        manual_schedule_dir=tmp_path / "data" / "manual_schedule",
        use_cache=True,
    )
    client.session.get = MagicMock(return_value=_ok([{"id": 1, "name": "Thunder", "code": "OKC"}]))

    teams = client.get_teams()

    assert len(teams) == 1
    cache_files = list((tmp_path / "outputs" / "runtime" / "cache" / "api_nba").glob("*.json"))
    assert len(cache_files) == 1
