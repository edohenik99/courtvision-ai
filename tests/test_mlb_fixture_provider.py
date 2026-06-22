from __future__ import annotations

from datetime import date
import socket
import urllib.request

import pytest

from courtvision.core.provider_registry import (
    ProviderCapability,
    ProviderMode,
    ProviderSourceType,
    get_provider,
    get_registered_providers,
)
from courtvision.sports.mlb.providers import (
    MLBBallparkProvider,
    MLBFixtureContextProvider,
    MLBHitterFeatureProvider,
    MLBLineupProvider,
    MLBPitcherFeatureProvider,
    MLBProbablePitcherProvider,
    MLBResearchContextProvider,
    MLBScheduleProvider,
    MLBWeatherProvider,
    compose_hr_research_contexts,
)
from courtvision.sports.mlb.research_context import (
    context_is_complete_for_production,
    context_is_complete_for_research,
)


RUN_DATE = date(2026, 6, 19)
FIXTURE_PROTOCOLS = (
    MLBScheduleProvider,
    MLBLineupProvider,
    MLBProbablePitcherProvider,
    MLBHitterFeatureProvider,
    MLBPitcherFeatureProvider,
    MLBWeatherProvider,
    MLBBallparkProvider,
    MLBResearchContextProvider,
)


def _contexts_by_player(provider: MLBFixtureContextProvider):
    contexts = compose_hr_research_contexts(RUN_DATE, provider)
    return {
        context.hitter_features.player_id: context
        for context in contexts
        if context.hitter_features is not None
    }


def test_fixture_provider_metadata_is_keyless_research_sample_only() -> None:
    provider = MLBFixtureContextProvider()

    assert all(isinstance(provider, protocol) for protocol in FIXTURE_PROTOCOLS)
    assert provider.provider_name == "mlb_fixture"
    assert provider.source_type is ProviderSourceType.MOCK
    assert provider.supported_modes == frozenset(
        {ProviderMode.RESEARCH, ProviderMode.SAMPLE}
    )
    assert provider.requires_credentials is False
    assert provider.required_env_vars == ()
    assert provider.production_safe is False
    assert provider.can_be_used_for_production is False
    assert ProviderMode.PRODUCTION not in provider.supported_modes
    assert ProviderCapability.ODDS not in provider.capabilities
    assert ProviderCapability.PLAYER_PROPS not in provider.capabilities
    assert not hasattr(provider, "eligible_for_betting")
    assert not hasattr(provider, "kelly_eligible")


def test_fixture_provider_does_not_make_network_calls(monkeypatch) -> None:
    def fail_network(*_args, **_kwargs):
        raise AssertionError("fixture provider attempted network access")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)

    provider = MLBFixtureContextProvider()
    assert len(provider.get_hr_research_contexts(RUN_DATE)) == 4


def test_complete_and_incomplete_fixture_contexts_fail_closed_explicitly() -> None:
    provider = MLBFixtureContextProvider()
    contexts = provider.get_hr_research_contexts(RUN_DATE)

    assert len(contexts) == 4
    complete = [context for context in contexts if context.context_complete]
    incomplete = [context for context in contexts if not context.context_complete]
    assert len(complete) == 2
    assert len(incomplete) == 2
    assert all(context_is_complete_for_research(context) for context in complete)
    assert all(context.missing_required_fields for context in incomplete)
    assert all("weather" in context.missing_required_fields for context in incomplete)
    assert all(
        "probable_pitcher.probable_status" in context.missing_required_fields
        for context in incomplete
    )


def test_unknown_lineup_and_pitcher_statuses_are_not_confirmed() -> None:
    contexts = _contexts_by_player(MLBFixtureContextProvider())
    unknown_hitter = contexts["fixture-hitter-3"]
    unknown_pitcher = contexts["fixture-hitter-4"]

    assert unknown_hitter.lineup_status is not None
    assert unknown_hitter.lineup_status.lineup_confirmed is False
    assert not unknown_hitter.lineup_status.is_player_confirmed("fixture-hitter-3")
    assert "lineup_status.hitter_status" in unknown_hitter.missing_required_fields

    assert unknown_pitcher.probable_pitcher is not None
    assert unknown_pitcher.probable_pitcher.probable_status == "unknown"
    assert unknown_pitcher.probable_pitcher.is_confirmed is False
    assert (
        "probable_pitcher.probable_status"
        in unknown_pitcher.missing_required_fields
    )


def test_composition_joins_each_component_by_its_contract_key() -> None:
    contexts = _contexts_by_player(MLBFixtureContextProvider())
    complete = contexts["fixture-hitter-1"]
    incomplete = contexts["fixture-hitter-3"]

    assert complete.game is not None
    assert complete.lineup_status is not None
    assert complete.probable_pitcher is not None
    assert complete.hitter_features is not None
    assert complete.pitcher_features is not None
    assert complete.weather is not None
    assert complete.ballpark is not None
    assert complete.lineup_status.game_id == complete.game.game_id
    assert complete.probable_pitcher.game_id == complete.game.game_id
    assert complete.probable_pitcher.team == complete.game.away_team
    assert complete.hitter_features.player_id == "fixture-hitter-1"
    assert (
        complete.pitcher_features.pitcher_id
        == complete.probable_pitcher.pitcher_id
    )
    assert complete.weather.game_id == complete.game.game_id
    assert complete.ballpark.venue_name == complete.game.venue_name

    assert incomplete.game is not None
    assert incomplete.pitcher_features is not None
    assert incomplete.probable_pitcher is not None
    assert (
        incomplete.pitcher_features.pitcher_id
        == incomplete.probable_pitcher.pitcher_id
    )
    assert incomplete.ballpark is not None
    assert incomplete.ballpark.venue_name == incomplete.game.venue_name
    assert incomplete.weather is None


def test_fixture_composition_is_deterministic_and_never_production_complete() -> None:
    provider = MLBFixtureContextProvider()
    first = compose_hr_research_contexts(RUN_DATE, provider)
    second = compose_hr_research_contexts(RUN_DATE, provider)

    assert first == second
    assert provider.get_hr_research_contexts(RUN_DATE) == first
    assert all(context.mode == "research" for context in first)
    assert all(not context_is_complete_for_production(context) for context in first)
    assert compose_hr_research_contexts(date(2026, 6, 20), provider) == []


def test_fixture_metadata_does_not_mutate_or_override_provider_registry() -> None:
    before = get_registered_providers()
    provider = MLBFixtureContextProvider()
    after = get_registered_providers()

    assert provider.provider_name == "mlb_fixture"
    assert after == before
    assert "mlb_fixture" not in after
    with pytest.raises(KeyError, match="not registered"):
        get_provider(provider.provider_name)

