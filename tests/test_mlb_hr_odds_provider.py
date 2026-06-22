from __future__ import annotations

from datetime import date

import pytest

from courtvision.sports.mlb.adapters.odds_api_provider import (
    ODDS_API_CONFIGURATION_MESSAGE,
    HROddsCandidate,
    OddsAPIConfigurationError,
    OddsAPIProvider,
)
from courtvision.sports.mlb.adapters.base import OddsProvider
from courtvision.sports.mlb.adapters.provider_factory import get_hr_provider
from courtvision.sports.mlb.adapters.sample_provider import SampleHRProvider
from courtvision.sports.mlb.hr_report import main


def test_provider_factory_returns_sample_provider_by_default() -> None:
    assert isinstance(get_hr_provider(), SampleHRProvider)
    assert isinstance(get_hr_provider("sample"), SampleHRProvider)


def test_provider_factory_returns_odds_api_provider(monkeypatch) -> None:
    monkeypatch.delenv("COURTVISION_ODDS_API_KEY", raising=False)

    provider = get_hr_provider("odds_api")

    assert isinstance(provider, OddsAPIProvider)
    assert isinstance(provider, OddsProvider)
    assert provider.region == "us"
    assert provider.markets == "batter_home_runs"


def test_odds_api_provider_reads_environment_configuration(monkeypatch) -> None:
    monkeypatch.setenv("COURTVISION_ODDS_API_KEY", "configured-key")
    monkeypatch.setenv("COURTVISION_ODDS_REGION", "ca")
    monkeypatch.setenv("COURTVISION_ODDS_MARKETS", "batter_home_runs,alternate_batter_home_runs")

    provider = OddsAPIProvider()

    assert provider.is_configured is True
    assert provider.region == "ca"
    assert provider.market_keys == (
        "batter_home_runs",
        "alternate_batter_home_runs",
    )


def test_missing_api_key_fails_before_network(monkeypatch) -> None:
    monkeypatch.delenv("COURTVISION_ODDS_API_KEY", raising=False)
    network_calls: list[str] = []
    provider = OddsAPIProvider(http_get=lambda url: network_calls.append(url))

    with pytest.raises(OddsAPIConfigurationError, match="not configured") as exc_info:
        provider.get_hr_candidates(date(2026, 6, 19))

    assert str(exc_info.value) == ODDS_API_CONFIGURATION_MESSAGE
    assert network_calls == []


def test_odds_api_candidate_normalization_from_mocked_payload() -> None:
    payload = [
        {
            "id": "mlb-game-1",
            "commence_time": "2026-06-20T00:10:00Z",
            "home_team": "New York Yankees",
            "away_team": "Boston Red Sox",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "last_update": "2026-06-19T16:00:00Z",
                    "markets": [
                        {
                            "key": "batter_home_runs",
                            "outcomes": [
                                {
                                    "name": "Over",
                                    "description": "Aaron Judge",
                                    "team": "New York Yankees",
                                    "price": 330,
                                    "point": 0.5,
                                },
                                {
                                    "name": "Under",
                                    "description": "Aaron Judge",
                                    "team": "New York Yankees",
                                    "price": -500,
                                    "point": 0.5,
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    provider = OddsAPIProvider(api_key="test-key")

    candidates = provider.normalize_payload(payload)

    assert candidates == [
        HROddsCandidate(
            player="Aaron Judge",
            team="New York Yankees",
            opponent="Boston Red Sox",
            pitcher="TBD",
            sportsbook="DraftKings",
            odds=330,
            line=0.5,
            market="batter_home_runs",
            game_id="mlb-game-1",
            commence_time="2026-06-20T00:10:00Z",
            timestamp="2026-06-19T16:00:00Z",
        )
    ]
    serialized = candidates[0].to_dict()
    assert serialized["mode"] == "research"
    assert serialized["eligible_for_betting"] is False
    assert serialized["kelly_eligible"] is False
    assert serialized["betting_approval_status"] == "research_only_not_betting_approved"


def test_cli_sample_mode_still_works(monkeypatch, capsys) -> None:
    monkeypatch.delenv("COURTVISION_ODDS_API_KEY", raising=False)

    assert main(["--date", "2026-06-19", "--provider", "sample"]) == 0

    captured = capsys.readouterr()
    assert "Research Watchlist" in captured.out
    assert "Example Player" in captured.out
    assert captured.err == ""


def test_cli_odds_api_missing_key_exits_cleanly(monkeypatch, capsys) -> None:
    monkeypatch.delenv("COURTVISION_ODDS_API_KEY", raising=False)

    assert main(["--date", "today", "--provider", "odds_api"]) != 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == ODDS_API_CONFIGURATION_MESSAGE
