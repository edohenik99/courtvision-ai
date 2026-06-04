from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "smoke_api_nba.py"


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("smoke_api_nba", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


smoke = load_module()


class ResponseStub:
    def __init__(self, status_code: int, payload: Any, text: str | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def game_row(target_date: str, game_id: int = 10403) -> dict[str, Any]:
    return {
        "id": game_id,
        "date": {"start": f"{target_date}T00:00:00+00:00"},
        "teams": {
            "home": {"id": 1, "name": "Oklahoma City Thunder"},
            "visitors": {"id": 2, "name": "Indiana Pacers"},
        },
        "scores": {"home": {"points": 101}, "visitors": {"points": 98}},
    }


def ok_body(rows: list[dict[str, Any]]) -> ResponseStub:
    return ResponseStub(200, {"errors": [], "results": len(rows), "response": rows})


def empty_ok() -> ResponseStub:
    return ok_body([])


def teams_ok() -> ResponseStub:
    return ok_body([{"id": 1, "name": "Atlanta Hawks"}])


def players_ok() -> ResponseStub:
    return ok_body([{"id": 265, "firstname": "Jane", "lastname": "Doe"}])


def player_stats_ok() -> ResponseStub:
    return ok_body(
        [
            {
                "player": {"id": 265, "firstname": "Jane", "lastname": "Doe"},
                "team": {"id": 1, "code": "ATL"},
                "game": {"id": 10403},
                "points": 22,
                "totReb": 8,
                "assists": 6,
            }
        ]
    )


def team_stats_ok() -> ResponseStub:
    return ok_body([{"points": 10550, "totReb": 4263, "assists": 2197}])


def finish_sequence(games_responses: list[ResponseStub]) -> list[ResponseStub]:
    assert len(games_responses) == 7
    return games_responses + [teams_ok(), players_ok(), player_stats_ok(), team_stats_ok()]


def test_mask_key_shows_preview_without_leaking_full_key() -> None:
    raw = "abcdefghijklmnopqrstuvwxyz123456"
    masked = smoke._mask_key(raw)

    assert masked == "abcd...3456"
    assert raw not in masked
    assert smoke._mask_key("12345678") == "********"
    assert smoke._mask_key("") == "<empty>"


def test_resolve_api_key_prefers_api_nba_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_NBA_KEY", "nba_key_value")
    monkeypatch.setenv("API_SPORTS_KEY", "sports_key_value")

    key, source = smoke.resolve_api_key()

    assert key == "nba_key_value"
    assert source == "API_NBA_KEY"


@pytest.mark.parametrize(
    ("http_status", "body", "error", "expected"),
    [
        (200, {"errors": [], "response": []}, None, "ok"),
        (200, {"errors": {"token": "bad"}, "response": []}, None, "provider_error"),
        (200, None, "json_parse_error: bad json", "malformed_response"),
        (401, None, None, "unauthorized"),
        (403, None, None, "forbidden"),
        (404, None, None, "not_found"),
        (429, None, None, "rate_limited"),
        (-1, None, "timeout", "connection_error"),
        (503, None, None, "unavailable"),
        (418, None, None, "http_418"),
    ],
)
def test_provider_status_mapping(
    http_status: int,
    body: dict[str, Any] | None,
    error: str | None,
    expected: str,
) -> None:
    assert smoke._provider_status(http_status, body, error) == expected


@patch("requests.Session.get")
def test_direct_date_lookup_finds_games(mock_get: MagicMock) -> None:
    target_date = "2026-06-03"
    mock_get.side_effect = finish_sequence(
        [ok_body([game_row(target_date, 10403)])] + [empty_ok()] * 6
    )

    payload = smoke.run_smoke_test("secretkeyvalue1234567890", target_date, "2025", timeout=5)

    assert len(payload["games_probe_attempts"]) == 7
    assert payload["verdict"]["games_available_direct"] == "yes"
    assert payload["verdict"]["games_available_fallback"] == "no"
    assert payload["verdict"]["games_available_any"] == "yes"
    assert payload["verdict"]["games_probe_attempt_count"] == "7"
    assert payload["games_probe_attempts"][0]["params"] == {"date": target_date}
    assert payload["games_probe_attempts"][0]["sample_game_dates"] == [target_date]
    assert payload["games_probe_attempts"][0]["sample_team_names"] == [
        "Oklahoma City Thunder vs Indiana Pacers"
    ]
    player_stats_call = mock_get.call_args_list[9]
    assert player_stats_call.kwargs["params"] == {"game": 10403}


@patch("requests.Session.get")
def test_direct_date_lookup_empty_but_season_fallback_finds_games(mock_get: MagicMock) -> None:
    target_date = "2026-06-03"
    other_date = "2026-04-12"
    mock_get.side_effect = finish_sequence(
        [empty_ok()] * 4
        + [ok_body([game_row(other_date, 900), game_row(target_date, 10404)])]
        + [empty_ok()] * 2
    )

    payload = smoke.run_smoke_test("secretkeyvalue1234567890", target_date, "2025", timeout=5)

    attempts = payload["games_probe_attempts"]
    assert attempts[4]["attempt"] == "season_only_local_filter"
    assert attempts[4]["object_count"] == 2
    assert attempts[4]["matched_target_date_count"] == 1
    assert payload["verdict"]["games_available_direct"] == "no"
    assert payload["verdict"]["games_available_fallback"] == "yes"
    assert payload["verdict"]["games_available_any"] == "yes"
    assert "season-only fallback" in payload["verdict"]["games_probe_note"]
    player_stats_call = mock_get.call_args_list[9]
    assert player_stats_call.kwargs["params"] == {"game": 10404}


@patch("requests.Session.get")
def test_all_games_probes_empty(mock_get: MagicMock) -> None:
    mock_get.side_effect = finish_sequence([empty_ok()] * 7)

    payload = smoke.run_smoke_test("secretkeyvalue1234567890", "2026-06-03", "2025", timeout=5)

    assert all(attempt["object_count"] == 0 for attempt in payload["games_probe_attempts"])
    assert payload["verdict"]["games_available_direct"] == "no"
    assert payload["verdict"]["games_available_fallback"] == "no"
    assert payload["verdict"]["games_available_any"] == "no"
    assert payload["verdict"]["games_probe_note"] == (
        "games endpoint was accessible, but no probe matched target-date games"
    )


@patch("requests.Session.get")
def test_regular_season_date_works_while_playoff_date_returns_empty(
    mock_get: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    playoff_date = "2026-06-03"
    regular_date = "2026-04-12"
    monkeypatch.setenv("API_NBA_KEY", "testkey_abcdef123456")

    control_games = [ok_body([game_row(regular_date, 777)])] + [empty_ok()] * 6
    target_games = [empty_ok()] * 7
    mock_get.side_effect = control_games + target_games + [
        teams_ok(),
        players_ok(),
        player_stats_ok(),
        team_stats_ok(),
    ]

    output = tmp_path / "api_nba_smoke.json"
    code = smoke.main(
        [
            "--date",
            playoff_date,
            "--season",
            "2025",
            "--regular-season-check-date",
            regular_date,
            "--output",
            str(output),
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert smoke.PLAYOFF_UNRELIABLE_MESSAGE in captured.out
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["regular_season_games_available"] == "yes"
    assert payload["verdict"]["games_available_any"] == "no"
    assert payload["verdict"]["games_probe_note"] == smoke.PLAYOFF_UNRELIABLE_MESSAGE


@patch("requests.Session.get")
def test_unauthorized_response_remains_non_fatal(mock_get: MagicMock) -> None:
    mock_get.return_value = ResponseStub(401, {"errors": {"token": "bad"}, "response": []})

    payload = smoke.run_smoke_test("badkeyvalue1234", "2026-06-03", "2025", timeout=1)

    assert len(payload["games_probe_attempts"]) == 7
    assert all(attempt["provider_status"] == "unauthorized" for attempt in payload["games_probe_attempts"])
    assert payload["verdict"]["api_access_works"] == "no"
    assert payload["verdict"]["games_available_any"] == "no"
    assert payload["verdict"]["usable_for_research_mode"] == "no"


@patch("requests.Session.get")
def test_main_writes_output_and_prints_required_verdict(
    mock_get: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target_date = "2026-06-03"
    monkeypatch.setenv("API_NBA_KEY", "testkey_abcdef123456")
    monkeypatch.delenv("API_SPORTS_KEY", raising=False)
    mock_get.side_effect = finish_sequence(
        [ok_body([game_row(target_date, 10403)])] + [empty_ok()] * 6
    )
    output = tmp_path / "api_nba_smoke.json"

    code = smoke.main(["--date", target_date, "--season", "2025", "--output", str(output)])

    captured = capsys.readouterr()
    assert code == 0
    assert output.exists()
    assert "testkey_abcdef123456" not in captured.out
    assert "API access works: yes" in captured.out
    assert "games_available_direct: yes" in captured.out
    assert "games_available_fallback: no" in captured.out
    assert "games_available_any: yes" in captured.out
    assert "games_probe_attempt_count: 7" in captured.out
    assert "player stats available: yes" in captured.out
    assert "team stats available: yes" in captured.out
    assert "usable for Research Mode: yes" in captured.out
    assert "usable for Betting Mode: no unless market lines/odds exist" in captured.out
