from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from scripts.run_research_mode import (
    RESEARCH_NO_GAMES,
    RESEARCH_NO_PLAYER_STATS,
    RESEARCH_OK,
    RESEARCH_PROVIDER_UNAVAILABLE,
    RESEARCH_SCHEDULE_ONLY_API_GAME_ID_MISSING,
    STAT_PROJECTION_COLUMNS,
    run_research_mode,
)


def _api_nba_body(target_date: str, game_id: int = 10403) -> dict[str, Any]:
    return {
        "response": [
            {
                "id": game_id,
                "date": {"start": f"{target_date}T00:00:00+00:00"},
                "teams": {
                    "home": {"name": "Oklahoma City Thunder", "code": "OKC"},
                    "visitors": {"name": "Indiana Pacers", "code": "IND"},
                },
            }
        ]
    }


def _stat(game_id: int = 10403, eligible_for_betting: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        game_date="2026-04-12",
        game_id=game_id,
        player_id=265,
        player_name="Jane Doe",
        team_id=1,
        team_abbreviation="OKC",
        minutes=32.5,
        points=22.0,
        rebounds=8.0,
        assists=6.0,
        threes=3.0,
        steals=2.0,
        blocks=1.0,
        eligible_for_betting=eligible_for_betting,
    )


def _write_manual_schedule(manual_dir: Path, target_date: str, game_id: str) -> None:
    manual_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "game_date": target_date,
                "game_id": game_id,
                "home_team": "Oklahoma City Thunder",
                "away_team": "Indiana Pacers",
                "home_team_abbr": "OKC",
                "away_team_abbr": "IND",
                "source": "manual_schedule",
            }
        ]
    ).to_csv(manual_dir / f"manual_games_{target_date}.csv", index=False)


class FakeApiNbaClient:
    def __init__(
        self,
        *,
        games_body: dict[str, Any],
        games_provider_status: str = "ok",
        stats_by_game: dict[int, list[Any]] | None = None,
        stats_provider_status: str = "ok",
    ) -> None:
        self.games_body = games_body
        self.games_provider_status = games_provider_status
        self.stats_by_game = stats_by_game or {}
        self.stats_provider_status = stats_provider_status
        self.player_stats_calls: list[tuple[int, str | None]] = []
        self._provider_status = {"provider": "api_nba", "provider_status": "unrequested"}

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        assert endpoint == "games"
        assert "date" in params
        self._provider_status = {
            "provider": "api_nba",
            "endpoint": endpoint,
            "provider_status": self.games_provider_status,
        }
        return self.games_body

    def get_provider_status(self) -> dict[str, Any]:
        return dict(self._provider_status)

    def get_player_stats_for_game(self, game_id: int, game_date: str | None = None) -> list[Any]:
        self.player_stats_calls.append((game_id, game_date))
        self._provider_status = {
            "provider": "api_nba",
            "endpoint": "players/statistics",
            "provider_status": self.stats_provider_status,
        }
        return list(self.stats_by_game.get(game_id, []))


def _run(
    tmp_path: Path,
    target_date: str,
    client: FakeApiNbaClient,
    *,
    manual_dir: Path | None = None,
) -> tuple[Any, Path]:
    output_dir = tmp_path / "outputs" / "runtime" / "research"
    result = run_research_mode(
        target_date=target_date,
        season=2025,
        output_dir=output_dir,
        manual_schedule_dir=manual_dir or tmp_path / "data" / "manual_schedule",
        client_factory=lambda **_: client,
    )
    return result, output_dir


def test_regular_season_api_nba_stats_output(tmp_path: Path) -> None:
    target_date = "2026-04-12"
    client = FakeApiNbaClient(
        games_body=_api_nba_body(target_date),
        stats_by_game={10403: [_stat(10403)]},
    )

    result, output_dir = _run(tmp_path, target_date, client)

    assert result.status == RESEARCH_OK
    assert client.player_stats_calls == [(10403, target_date)]

    csv_path = output_dir / f"stat_projection_source_{target_date}.csv"
    rows = pd.read_csv(csv_path)
    assert rows.columns.tolist() == STAT_PROJECTION_COLUMNS
    assert len(rows) == 1
    row = rows.iloc[0].to_dict()
    assert row["game_date"] == target_date
    assert row["game_id"] == 10403
    assert row["player_id"] == 265
    assert row["player_name"] == "Jane Doe"
    assert row["team_abbreviation"] == "OKC"
    assert row["points"] == 22.0
    assert row["source"] == "api_nba"
    assert row["mode"] == "research"
    assert row["eligible_for_betting"] is False

    summary = (output_dir / f"research_mode_summary_{target_date}.txt").read_text(encoding="utf-8")
    assert "status: RESEARCH_OK" in summary
    assert "player_stats_row_count: 1" in summary

    diagnostics = json.loads(
        (tmp_path / "outputs" / "runtime" / "diagnostics" / f"research_mode_{target_date}.json").read_text(
            encoding="utf-8"
        )
    )
    assert diagnostics["status"] == RESEARCH_OK
    assert diagnostics["market_prop_rows_created"] == 0
    assert diagnostics["elite_rows_created"] == 0
    assert diagnostics["kelly_called"] is False
    assert diagnostics["operator_artifacts_written"] == []


def test_manual_schedule_fake_game_id_does_not_call_player_stats_endpoint(tmp_path: Path) -> None:
    target_date = "2026-06-03"
    manual_dir = tmp_path / "data" / "manual_schedule"
    _write_manual_schedule(manual_dir, target_date, "manual_finals_001")
    client = FakeApiNbaClient(games_body={"response": []})

    result, output_dir = _run(tmp_path, target_date, client, manual_dir=manual_dir)

    assert result.status == RESEARCH_SCHEDULE_ONLY_API_GAME_ID_MISSING
    assert client.player_stats_calls == []

    rows = pd.read_csv(output_dir / f"stat_projection_source_{target_date}.csv")
    assert rows.columns.tolist() == STAT_PROJECTION_COLUMNS
    assert rows.empty
    assert result.diagnostics["skipped_non_numeric_game_ids"] == ["manual_finals_001"]


def test_eligible_for_betting_is_always_false_even_if_source_object_is_true(tmp_path: Path) -> None:
    target_date = "2026-04-12"
    client = FakeApiNbaClient(
        games_body=_api_nba_body(target_date),
        stats_by_game={10403: [_stat(10403, eligible_for_betting=True)]},
    )

    _result, output_dir = _run(tmp_path, target_date, client)

    rows = pd.read_csv(output_dir / f"stat_projection_source_{target_date}.csv")
    assert rows["eligible_for_betting"].tolist() == [False]


def test_no_marketprop_kelly_elite_or_operator_files_written(tmp_path: Path) -> None:
    target_date = "2026-06-03"
    runtime_root = tmp_path / "outputs" / "runtime"
    operator_dir = runtime_root / "operator"
    operator_dir.mkdir(parents=True)
    sentinel = operator_dir / f"kelly_stakes_{target_date}.csv"
    sentinel.write_text("player_name,stake\nExisting,10\n", encoding="utf-8")

    client = FakeApiNbaClient(games_body={"response": []})
    result = run_research_mode(
        target_date=target_date,
        season=2025,
        output_dir=runtime_root / "research",
        manual_schedule_dir=tmp_path / "data" / "manual_schedule",
        client_factory=lambda **_: client,
    )

    assert result.status == RESEARCH_NO_GAMES
    assert result.diagnostics["market_prop_rows_created"] == 0
    assert result.diagnostics["elite_rows_created"] == 0
    assert result.diagnostics["kelly_called"] is False
    assert result.diagnostics["operator_artifacts_written"] == []
    assert sentinel.read_text(encoding="utf-8") == "player_name,stake\nExisting,10\n"
    assert not (operator_dir / f"elite_board_{target_date}.csv").exists()
    assert not (operator_dir / f"full_market_board_{target_date}.csv").exists()


def test_status_no_games_when_schedule_missing_and_provider_ok(tmp_path: Path) -> None:
    target_date = "2026-04-13"
    client = FakeApiNbaClient(games_body={"response": []})

    result, _output_dir = _run(tmp_path, target_date, client)

    assert result.status == RESEARCH_NO_GAMES


def test_status_provider_unavailable_when_api_nba_access_fails(tmp_path: Path) -> None:
    target_date = "2026-04-13"
    client = FakeApiNbaClient(games_body={}, games_provider_status="missing_credentials")

    result, _output_dir = _run(tmp_path, target_date, client)

    assert result.status == RESEARCH_PROVIDER_UNAVAILABLE


def test_status_no_player_stats_when_games_exist_but_stats_empty(tmp_path: Path) -> None:
    target_date = "2026-04-12"
    client = FakeApiNbaClient(games_body=_api_nba_body(target_date), stats_by_game={10403: []})

    result, _output_dir = _run(tmp_path, target_date, client)

    assert result.status == RESEARCH_NO_PLAYER_STATS
