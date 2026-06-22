from __future__ import annotations

from datetime import date

from courtvision.sports.mlb.adapters import (
    BallparkProvider,
    HitterStatsProvider,
    MLBBallparkProvider,
    MLBStatsProvider,
    MLBWeatherProvider,
    OddsProvider,
    PitcherStatsProvider,
    SampleHRProvider,
    SportsbookOddsProvider,
    WeatherProvider,
)
from courtvision.sports.mlb.hr_prop_engine import HRPropInput
from courtvision.sports.mlb.hr_report import main


def test_sample_provider_returns_hr_candidates() -> None:
    candidates = SampleHRProvider().get_hr_candidates(date(2026, 6, 19))

    assert len(candidates) == 3
    assert all(isinstance(candidate, HRPropInput) for candidate in candidates)
    assert {candidate.sportsbook for candidate in candidates} == {
        "BetMGM",
        "DraftKings",
        "FanDuel",
    }
    assert all(candidate.mode == "research" for candidate in candidates)
    assert all(candidate.eligible_for_betting is False for candidate in candidates)
    assert all(candidate.kelly_eligible is False for candidate in candidates)
    assert all(
        candidate.betting_approval_status == "research_only_not_betting_approved"
        for candidate in candidates
    )


def test_adapter_contracts_exist_and_provider_shells_conform() -> None:
    odds = SportsbookOddsProvider()
    stats = MLBStatsProvider()

    assert isinstance(odds, OddsProvider)
    assert isinstance(stats, HitterStatsProvider)
    assert isinstance(stats, PitcherStatsProvider)
    assert isinstance(MLBWeatherProvider(), WeatherProvider)
    assert isinstance(MLBBallparkProvider(), BallparkProvider)
    assert "Pinnacle" in odds.supported_sportsbooks


def test_cli_default_provider_works(capsys) -> None:
    assert main(["--date", "2026-06-19"]) == 0

    captured = capsys.readouterr()
    assert "Research Watchlist" in captured.out
    assert "Example Player" in captured.out
    assert captured.err == ""


def test_cli_sample_provider_works(capsys) -> None:
    assert main(["--date", "2026-06-19", "--provider", "sample"]) == 0

    captured = capsys.readouterr()
    assert "Research Watchlist" in captured.out
    assert "Sample Slugger" in captured.out
    assert captured.err == ""


def test_cli_unsupported_provider_fails_cleanly(capsys) -> None:
    assert main(["--date", "2026-06-19", "--provider", "live"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Unsupported MLB HR provider 'live'" in captured.err
    assert "Supported providers: sample" in captured.err


def test_sample_provider_needs_no_external_keys(monkeypatch, capsys) -> None:
    for name in (
        "THE_ODDS_API_KEY",
        "SPORTSDATAIO_API_KEY",
        "MLB_API_KEY",
        "WEATHER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    assert SampleHRProvider.requires_external_keys is False
    assert main(["--date", "2026-06-19", "--provider", "sample"]) == 0
    assert "Research Watchlist" in capsys.readouterr().out
