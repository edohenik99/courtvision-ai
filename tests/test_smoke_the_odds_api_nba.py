from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from courtvision.models import MarketProp


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "smoke_the_odds_api_nba.py"


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("smoke_the_odds_api_nba", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


smoke = load_module()


class ResponseStub:
    def __init__(
        self,
        status_code: int,
        payload: Any,
        headers: dict[str, str] | None = None,
        text: str | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(payload)

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def event_row(
    event_id: str,
    commence_time: str,
    *,
    home_team: str = "Oklahoma City Thunder",
    away_team: str = "Indiana Pacers",
) -> dict[str, Any]:
    return {
        "id": event_id,
        "sport_key": "basketball_nba",
        "commence_time": commence_time,
        "home_team": home_team,
        "away_team": away_team,
    }


def odds_body(
    event_id: str = "evt_target",
    *,
    market_key: str = "player_points",
    bookmaker_key: str = "draftkings",
    bookmaker_title: str = "DraftKings",
) -> dict[str, Any]:
    return {
        "id": event_id,
        "sport_key": "basketball_nba",
        "commence_time": "2026-06-04T23:30:00Z",
        "home_team": "Oklahoma City Thunder",
        "away_team": "Indiana Pacers",
        "bookmakers": [
            {
                "key": bookmaker_key,
                "title": bookmaker_title,
                "last_update": "2026-06-04T14:00:00Z",
                "markets": [
                    {
                        "key": market_key,
                        "last_update": "2026-06-04T14:01:00Z",
                        "outcomes": [
                            {
                                "name": "Over",
                                "description": "Shai Gilgeous-Alexander",
                                "price": -110,
                                "point": 31.5,
                            },
                            {
                                "name": "Under",
                                "description": "Shai Gilgeous-Alexander",
                                "price": -120,
                                "point": 31.5,
                            },
                        ],
                    }
                ],
            }
        ],
    }


def test_mask_key_shows_preview_without_leaking_full_key() -> None:
    raw = "abcdefghijklmnopqrstuvwxyz123456"
    masked = smoke._mask_key(raw)

    assert masked == "abcd...3456"
    assert raw not in masked
    assert smoke._mask_key("12345678") == "********"
    assert smoke._mask_key("") == "<empty>"


@patch("requests.Session.get")
def test_missing_key_is_non_fatal_and_writes_diagnostic(
    mock_get: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    output = tmp_path / "outputs" / "runtime" / "diagnostics" / "the_odds_api_smoke_2026-06-04.json"

    code = smoke.main(["--date", "2026-06-04", "--output", str(output)])

    captured = capsys.readouterr()
    assert code == 0
    assert output.exists()
    assert mock_get.call_count == 0
    assert "key=<empty>" in captured.out
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["api_access_works"] is False
    assert payload["provider_status"] == "missing_credentials"
    assert payload["courtvision_verdict"]["looks_usable_for_courtvision"] is False


@patch("requests.Session.get")
def test_event_filtering_by_target_date_and_low_volume_event_probe(mock_get: MagicMock) -> None:
    target_date = "2026-06-04"
    mock_get.side_effect = [
        ResponseStub(
            200,
            [
                event_row("evt_other", "2026-06-03T23:30:00Z"),
                event_row("evt_target", "2026-06-04T23:30:00Z"),
                event_row("evt_late", "2026-06-04T23:59:00Z"),
            ],
        ),
        ResponseStub(200, odds_body("evt_target")),
    ]

    payload = smoke.run_smoke_test("secretkeyvalue123456", target_date, max_events=1)

    assert payload["events_available"] == 3
    assert payload["target_date_events_count"] == 2
    assert payload["probed_event_count"] == 1
    assert mock_get.call_count == 2
    events_call, odds_call = mock_get.call_args_list
    assert events_call.args[0].endswith("/v4/sports/basketball_nba/events")
    assert events_call.kwargs["params"] == {"apiKey": "secretkeyvalue123456", "dateFormat": "iso"}
    assert odds_call.args[0].endswith("/v4/sports/basketball_nba/events/evt_target/odds")
    assert odds_call.kwargs["params"]["markets"] == "player_points,player_rebounds,player_assists"


@patch("requests.Session.get")
def test_local_sports_date_matches_toronto_boundary_event(mock_get: MagicMock) -> None:
    target_date = "2026-06-05"
    event = event_row(
        "2852e944f8ef0bc3eba79f883b9101ba",
        "2026-06-06T00:40:00Z",
        home_team="San Antonio Spurs",
        away_team="New York Knicks",
    )
    mock_get.side_effect = [
        ResponseStub(200, [event]),
        ResponseStub(200, odds_body(event["id"])),
    ]

    payload = smoke.run_smoke_test(
        "secretkeyvalue123456",
        target_date,
        timezone_name="America/Toronto",
    )

    assert payload["target_date_events_count"] == 1
    assert payload["probed_event_count"] == 1
    diagnostic = payload["event_debug_diagnostics"][0]
    assert diagnostic["commence_date_utc"] == "2026-06-06"
    assert diagnostic["commence_date_local"] == "2026-06-05"
    assert diagnostic["target_date_match_utc"] is False
    assert diagnostic["target_date_match_local"] is True


def test_utc_date_matching_alone_would_miss_toronto_boundary_event() -> None:
    event = event_row(
        "2852e944f8ef0bc3eba79f883b9101ba",
        "2026-06-06T00:40:00Z",
        home_team="San Antonio Spurs",
        away_team="New York Knicks",
    )

    diagnostic = smoke._event_debug_diagnostic(
        event,
        "2026-06-05",
        smoke._resolve_timezone("America/Toronto"),
    )

    assert diagnostic == {
        "event_id": "2852e944f8ef0bc3eba79f883b9101ba",
        "home_team": "San Antonio Spurs",
        "away_team": "New York Knicks",
        "commence_time_utc": "2026-06-06T00:40:00Z",
        "commence_date_utc": "2026-06-06",
        "commence_time_local": "2026-06-05T20:40:00-04:00",
        "commence_date_local": "2026-06-05",
        "target_date_match_utc": False,
        "target_date_match_local": True,
    }


@patch("requests.Session.get")
def test_event_debug_diagnostics_are_written(
    mock_get: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_date = "2026-06-05"
    output = tmp_path / "outputs" / "runtime" / "diagnostics" / f"the_odds_api_smoke_{target_date}.json"
    monkeypatch.setenv("THE_ODDS_API_KEY", "secretkeyvalue123456")
    mock_get.side_effect = [
        ResponseStub(
            200,
            [
                event_row(
                    "2852e944f8ef0bc3eba79f883b9101ba",
                    "2026-06-06T00:40:00Z",
                    home_team="San Antonio Spurs",
                    away_team="New York Knicks",
                )
            ],
        ),
        ResponseStub(200, odds_body("2852e944f8ef0bc3eba79f883b9101ba")),
    ]

    code = smoke.main(
        [
            "--date",
            target_date,
            "--timezone",
            "America/Toronto",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["timezone"] == "America/Toronto"
    assert payload["event_debug_diagnostics"] == [
        {
            "event_id": "2852e944f8ef0bc3eba79f883b9101ba",
            "home_team": "San Antonio Spurs",
            "away_team": "New York Knicks",
            "commence_time_utc": "2026-06-06T00:40:00Z",
            "commence_date_utc": "2026-06-06",
            "commence_time_local": "2026-06-05T20:40:00-04:00",
            "commence_date_local": "2026-06-05",
            "target_date_match_utc": False,
            "target_date_match_local": True,
        }
    ]


@patch("requests.Session.get")
def test_commence_window_params_are_built_from_local_date(mock_get: MagicMock) -> None:
    mock_get.side_effect = [ResponseStub(200, [])]

    payload = smoke.run_smoke_test(
        "secretkeyvalue123456",
        "2026-06-05",
        timezone_name="America/Toronto",
        use_commence_window=True,
    )

    expected_window = {
        "commenceTimeFrom": "2026-06-05T04:00:00Z",
        "commenceTimeTo": "2026-06-06T04:00:00Z",
    }
    assert smoke._build_commence_window_params("2026-06-05", "America/Toronto") == expected_window
    assert payload["commence_window_utc"] == expected_window
    events_call = mock_get.call_args_list[0]
    assert events_call.kwargs["params"] == {
        "apiKey": "secretkeyvalue123456",
        "dateFormat": "iso",
        **expected_window,
    }


@patch("requests.Session.get")
def test_player_prop_detection(mock_get: MagicMock) -> None:
    mock_get.side_effect = [
        ResponseStub(200, [event_row("evt_target", "2026-06-04T23:30:00Z")]),
        ResponseStub(200, odds_body("evt_target", market_key="player_rebounds")),
    ]

    payload = smoke.run_smoke_test(
        "secretkeyvalue123456",
        "2026-06-04",
        markets="player_rebounds",
    )

    assert payload["api_access_works"] is True
    assert payload["player_props_available"] is True
    assert payload["market_keys_seen"] == ["player_rebounds"]
    assert payload["courtvision_verdict"]["looks_usable_for_courtvision"] is True


@patch("requests.Session.get")
def test_bookmaker_extraction(mock_get: MagicMock) -> None:
    body = odds_body("evt_target")
    body["bookmakers"].append(
        {
            "key": "fanduel",
            "title": "FanDuel",
            "last_update": "2026-06-04T14:02:00Z",
            "markets": [],
        }
    )
    mock_get.side_effect = [
        ResponseStub(200, [event_row("evt_target", "2026-06-04T23:30:00Z")]),
        ResponseStub(200, body),
    ]

    payload = smoke.run_smoke_test("secretkeyvalue123456", "2026-06-04")

    assert payload["bookmaker_count"] == 2
    assert payload["sample_bookmakers"] == [
        {"key": "draftkings", "title": "DraftKings"},
        {"key": "fanduel", "title": "FanDuel"},
    ]


@patch("requests.Session.get")
def test_normalization_preview_shape(mock_get: MagicMock) -> None:
    mock_get.side_effect = [
        ResponseStub(200, [event_row("evt_target", "2026-06-04T23:30:00Z")]),
        ResponseStub(200, odds_body("evt_target")),
    ]

    payload = smoke.run_smoke_test("secretkeyvalue123456", "2026-06-04")

    rows = payload["sample_normalized_rows"]
    assert len(rows) == 2
    assert list(rows[0].keys()) == list(smoke.NORMALIZED_ROW_FIELDS)
    assert rows[0] == {
        "provider": "the_odds_api",
        "provider_event_id": "evt_target",
        "home_team": "Oklahoma City Thunder",
        "away_team": "Indiana Pacers",
        "commence_time": "2026-06-04T23:30:00Z",
        "player_name": "Shai Gilgeous-Alexander",
        "market_type": "player_points",
        "side": "over",
        "line": 31.5,
        "american_odds": -110,
        "sportsbook": "DraftKings",
        "updated_at": "2026-06-04T14:01:00Z",
        "eligible_for_betting": False,
    }
    assert rows[1]["side"] == "under"
    assert rows[1]["eligible_for_betting"] is False


@patch("requests.Session.get")
def test_usage_headers_captured(mock_get: MagicMock) -> None:
    mock_get.side_effect = [
        ResponseStub(
            200,
            [event_row("evt_target", "2026-06-04T23:30:00Z")],
            headers={
                "X-Requests-Remaining": "499",
                "X-Requests-Used": "1",
                "X-Requests-Last": "0",
            },
        ),
        ResponseStub(
            200,
            odds_body("evt_target"),
            headers={
                "x-requests-remaining": "498",
                "x-requests-used": "2",
                "x-requests-last": "1",
            },
        ),
    ]

    payload = smoke.run_smoke_test("secretkeyvalue123456", "2026-06-04")

    assert payload["usage_headers"]["events"] == {
        "x-requests-remaining": "499",
        "x-requests-used": "1",
        "x-requests-last": "0",
    }
    assert payload["usage_headers"]["event_odds"] == [
        {
            "event_id": "evt_target",
            "headers": {
                "x-requests-remaining": "498",
                "x-requests-used": "2",
                "x-requests-last": "1",
            },
        }
    ]


@patch("requests.Session.get")
def test_no_betting_artifacts_written(
    mock_get: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_date = "2026-06-04"
    runtime_root = tmp_path / "outputs" / "runtime"
    output = runtime_root / "diagnostics" / f"the_odds_api_smoke_{target_date}.json"
    monkeypatch.setenv("THE_ODDS_API_KEY", "secretkeyvalue123456")
    mock_get.side_effect = [
        ResponseStub(200, [event_row("evt_target", "2026-06-04T23:30:00Z")]),
        ResponseStub(200, odds_body("evt_target")),
    ]

    code = smoke.main(["--date", target_date, "--output", str(output)])

    assert code == 0
    assert output.exists()
    assert not (runtime_root / "operator").exists()
    assert not (runtime_root / "history").exists()
    assert not (runtime_root / "research").exists()
    assert not (runtime_root / "model").exists()


@patch("requests.Session.get")
def test_no_marketprop_rows_created(mock_get: MagicMock) -> None:
    mock_get.side_effect = [
        ResponseStub(200, [event_row("evt_target", "2026-06-04T23:30:00Z")]),
        ResponseStub(200, odds_body("evt_target")),
    ]

    payload = smoke.run_smoke_test("secretkeyvalue123456", "2026-06-04")

    assert not hasattr(smoke, "MarketProp")
    assert all(not isinstance(row, MarketProp) for row in payload["sample_normalized_rows"])
