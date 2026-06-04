from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from courtvision.models import MarketProp
from courtvision.providers import the_odds_api_provider as provider


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


def market_body(market_key: str = "player_points") -> dict[str, Any]:
    return {
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


def odds_body(
    event_id: str = "evt_target",
    *,
    commence_time: str = "2026-06-04T23:30:00Z",
    market_key: str = "player_points",
    extra_bookmaker: bool = False,
) -> dict[str, Any]:
    bookmakers = [
        {
            "key": "draftkings",
            "title": "DraftKings",
            "last_update": "2026-06-04T14:00:00Z",
            "markets": [market_body(market_key)],
        }
    ]
    if extra_bookmaker:
        bookmakers.append(
            {
                "key": "fanduel",
                "title": "FanDuel",
                "last_update": "2026-06-04T14:02:00Z",
                "markets": [],
            }
        )
    return {
        "id": event_id,
        "sport_key": "basketball_nba",
        "commence_time": commence_time,
        "home_team": "Oklahoma City Thunder",
        "away_team": "Indiana Pacers",
        "bookmakers": bookmakers,
    }


@patch("requests.Session.get")
def test_get_events_for_date_uses_local_toronto_date(
    mock_get: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("THE_ODDS_API_KEY", "secretkeyvalue123456")
    mock_get.return_value = ResponseStub(
        200,
        [
            event_row("evt_previous_local_day", "2026-06-05T03:30:00Z"),
            event_row("evt_target", "2026-06-06T00:40:00Z"),
        ],
    )

    events = provider.get_events_for_date(
        "2026-06-05",
        runtime_root=tmp_path / "outputs" / "runtime",
    )

    assert [event["id"] for event in events] == ["evt_target"]


@patch("requests.Session.get")
def test_player_props_for_date_normalizes_rows_and_toronto_time(
    mock_get: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_date = "2026-06-05"
    runtime_root = tmp_path / "outputs" / "runtime"
    monkeypatch.setenv("THE_ODDS_API_KEY", "secretkeyvalue123456")
    mock_get.side_effect = [
        ResponseStub(200, [event_row("evt_target", "2026-06-06T00:40:00Z")]),
        ResponseStub(200, odds_body("evt_target", commence_time="2026-06-06T00:40:00Z")),
    ]

    df = provider.get_player_props_for_date(
        target_date,
        ["player_points"],
        runtime_root=runtime_root,
    )

    assert list(df.columns) == list(provider.NORMALIZED_COLUMNS)
    assert len(df.index) == 2
    row = df.iloc[0].to_dict()
    assert row == {
        "provider": "the_odds_api",
        "provider_event_id": "evt_target",
        "home_team": "Oklahoma City Thunder",
        "away_team": "Indiana Pacers",
        "commence_time_utc": "2026-06-06T00:40:00Z",
        "commence_time_local": "2026-06-05T20:40:00-04:00",
        "game_date": "2026-06-05",
        "player_name": "Shai Gilgeous-Alexander",
        "market_type": "player_points",
        "side": "over",
        "line": 31.5,
        "american_odds": -110,
        "sportsbook": "DraftKings",
        "updated_at": "2026-06-04T14:01:00Z",
        "source": "the_odds_api:event_odds",
        "eligible_for_betting": False,
    }
    assert df.iloc[1]["side"] == "under"
    assert df["eligible_for_betting"].tolist() == [False, False]


@patch("requests.Session.get")
def test_sparse_market_coverage_is_non_fatal_and_diagnosed(
    mock_get: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_date = "2026-06-05"
    runtime_root = tmp_path / "outputs" / "runtime"
    monkeypatch.setenv("THE_ODDS_API_KEY", "secretkeyvalue123456")
    mock_get.side_effect = [
        ResponseStub(
            200,
            [event_row("evt_target", "2026-06-06T00:40:00Z")],
            headers={
                "X-Requests-Remaining": "499",
                "X-Requests-Used": "1",
                "X-Requests-Last": "0",
            },
        ),
        ResponseStub(
            200,
            odds_body("evt_target", commence_time="2026-06-06T00:40:00Z", market_key="player_assists"),
            headers={
                "x-requests-remaining": "498",
                "x-requests-used": "2",
                "x-requests-last": "1",
            },
        ),
    ]

    df = provider.get_player_props_for_date(
        target_date,
        ["player_points", "player_rebounds", "player_assists"],
        runtime_root=runtime_root,
    )

    diagnostics_path = runtime_root / "diagnostics" / f"the_odds_api_provider_{target_date}.json"
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    assert diagnostics["provider_status"] == "ok"
    assert diagnostics["prop_row_count"] == len(df.index) == 2
    assert diagnostics["requested_markets"] == [
        "player_points",
        "player_rebounds",
        "player_assists",
    ]
    assert diagnostics["market_keys_seen"] == ["player_assists"]
    assert diagnostics["missing_requested_markets"] == ["player_points", "player_rebounds"]
    assert diagnostics["usage_headers"]["events"] == {
        "x-requests-remaining": "499",
        "x-requests-used": "1",
        "x-requests-last": "0",
    }
    assert diagnostics["usage_headers"]["event_odds"] == [
        {
            "event_id": "evt_target",
            "headers": {
                "x-requests-remaining": "498",
                "x-requests-used": "2",
                "x-requests-last": "1",
            },
        }
    ]
    assert diagnostics["eligible_for_betting_any_true"] is False


@patch("requests.Session.get")
def test_bookmaker_extraction_and_cache_files(
    mock_get: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_date = "2026-06-04"
    runtime_root = tmp_path / "outputs" / "runtime"
    monkeypatch.setenv("THE_ODDS_API_KEY", "secretkeyvalue123456")
    mock_get.side_effect = [
        ResponseStub(200, [event_row("evt_target", "2026-06-04T23:30:00Z")]),
        ResponseStub(200, odds_body("evt_target", extra_bookmaker=True)),
    ]

    provider.get_player_props_for_date(
        target_date,
        "player_points",
        runtime_root=runtime_root,
    )

    diagnostics = json.loads(
        (runtime_root / "diagnostics" / f"the_odds_api_provider_{target_date}.json").read_text(
            encoding="utf-8"
        )
    )
    assert diagnostics["bookmaker_count"] == 2
    cache_files = list((runtime_root / "cache" / "the_odds_api").glob("*.json"))
    assert len(cache_files) == 2
    assert all("secretkeyvalue123456" not in path.read_text(encoding="utf-8") for path in cache_files)


@patch("requests.Session.get")
def test_missing_key_is_non_fatal_and_writes_diagnostics(
    mock_get: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_date = "2026-06-05"
    runtime_root = tmp_path / "outputs" / "runtime"
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)

    df = provider.get_player_props_for_date(
        target_date,
        ["player_points"],
        runtime_root=runtime_root,
    )

    diagnostics = json.loads(
        (runtime_root / "diagnostics" / f"the_odds_api_provider_{target_date}.json").read_text(
            encoding="utf-8"
        )
    )
    assert df.empty
    assert mock_get.call_count == 0
    assert diagnostics["provider_status"] == "missing_credentials"
    assert diagnostics["warnings"] == ["THE_ODDS_API_KEY_missing"]


@patch("requests.Session.get")
def test_no_marketprop_rows_or_betting_artifacts_are_created(
    mock_get: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_date = "2026-06-04"
    runtime_root = tmp_path / "outputs" / "runtime"
    monkeypatch.setenv("THE_ODDS_API_KEY", "secretkeyvalue123456")
    mock_get.side_effect = [
        ResponseStub(200, [event_row("evt_target", "2026-06-04T23:30:00Z")]),
        ResponseStub(200, odds_body("evt_target")),
    ]

    df = provider.get_player_props_for_date(
        target_date,
        ["player_points"],
        runtime_root=runtime_root,
    )

    assert not hasattr(provider, "MarketProp")
    assert all(not isinstance(row, MarketProp) for row in df.to_dict("records"))
    assert not (runtime_root / "operator").exists()
    assert not (runtime_root / "history").exists()
    assert not (runtime_root / "research").exists()
    assert not (runtime_root / "model").exists()
    assert not (runtime_root / "operator" / f"elite_board_{target_date}.csv").exists()
    assert not (runtime_root / "operator" / f"kelly_stakes_{target_date}.csv").exists()
    assert not (runtime_root / "operator" / f"full_market_board_{target_date}.csv").exists()
