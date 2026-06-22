from __future__ import annotations

from datetime import date

import pytest

from courtvision.core.provider_registry import (
    ProviderCapability,
    ProviderCapabilityNotSupportedError,
    ProviderMode,
    ProviderSourceType,
    get_provider,
    require_provider_capability,
)
from courtvision.sports.mlb.adapters.ballpark_provider import (
    MLBBallparkProvider as MLBBallparkProviderStub,
)
from courtvision.sports.mlb.adapters.sample_provider import SampleHRProvider
from courtvision.sports.mlb.adapters.stats_provider import MLBStatsProvider
from courtvision.sports.mlb.adapters.weather_provider import (
    MLBWeatherProvider as MLBWeatherProviderStub,
)
from courtvision.sports.mlb.providers import (
    MLBBallparkProvider,
    MLBHitterFeatureProvider,
    MLBLineupProvider,
    MLBPitcherFeatureProvider,
    MLBProbablePitcherProvider,
    MLBProviderContract,
    MLBResearchContextProvider,
    MLBScheduleProvider,
    MLBWeatherProvider,
)
from courtvision.sports.mlb.research_context import (
    MLBHRResearchContext,
    build_sample_mlb_hr_contexts,
)


RUN_DATE = date(2026, 6, 19)
PROVIDER_PROTOCOLS = (
    MLBScheduleProvider,
    MLBLineupProvider,
    MLBProbablePitcherProvider,
    MLBHitterFeatureProvider,
    MLBPitcherFeatureProvider,
    MLBWeatherProvider,
    MLBBallparkProvider,
    MLBResearchContextProvider,
)
REQUIRED_METADATA = (
    "provider_name",
    "source_type",
    "supported_modes",
    "requires_credentials",
    "required_env_vars",
    "capabilities",
)


def test_every_provider_protocol_exposes_phase_1c_metadata() -> None:
    for protocol in PROVIDER_PROTOCOLS:
        assert MLBProviderContract in protocol.__mro__
        for name in REQUIRED_METADATA:
            assert hasattr(protocol, name), f"{protocol.__name__}.{name}"


def test_protocol_methods_return_phase_2b_context_contracts() -> None:
    expected_methods = {
        MLBScheduleProvider: ("get_games",),
        MLBLineupProvider: ("get_lineups", "get_lineup_for_game"),
        MLBProbablePitcherProvider: (
            "get_probable_pitchers",
            "get_probable_pitcher_for_game",
        ),
        MLBHitterFeatureProvider: ("get_hitter_features",),
        MLBPitcherFeatureProvider: ("get_pitcher_features",),
        MLBWeatherProvider: ("get_weather_for_game",),
        MLBBallparkProvider: ("get_ballpark_context",),
        MLBResearchContextProvider: ("get_hr_research_contexts",),
    }

    for protocol, method_names in expected_methods.items():
        for method_name in method_names:
            assert callable(getattr(protocol, method_name))


def test_sample_context_provider_is_keyless_deterministic_and_registry_aligned() -> None:
    provider = SampleHRProvider()
    registration = get_provider("mlb_sample")

    assert isinstance(provider, MLBResearchContextProvider)
    assert provider.provider_name == registration.name
    assert provider.source_type is ProviderSourceType.SAMPLE
    assert provider.supported_modes == registration.supported_modes
    assert provider.capabilities == registration.capabilities
    assert provider.requires_credentials is False
    assert provider.required_env_vars == ()
    assert provider.production_safe is False
    assert provider.can_be_used_for_production is False

    first = provider.get_hr_research_contexts(RUN_DATE)
    second = provider.get_hr_research_contexts(RUN_DATE)
    assert first == second == list(build_sample_mlb_hr_contexts(RUN_DATE))
    assert all(isinstance(context, MLBHRResearchContext) for context in first)
    assert all(context.mode == "research" for context in first)


def test_existing_placeholders_conform_and_return_explicit_missing_data() -> None:
    stats = MLBStatsProvider()
    weather = MLBWeatherProviderStub()
    ballpark = MLBBallparkProviderStub()
    sample_game = build_sample_mlb_hr_contexts(RUN_DATE)[0].game
    assert sample_game is not None

    assert isinstance(stats, MLBHitterFeatureProvider)
    assert isinstance(stats, MLBPitcherFeatureProvider)
    assert isinstance(weather, MLBWeatherProvider)
    assert isinstance(ballpark, MLBBallparkProvider)
    assert stats.get_hitter_features("missing", RUN_DATE, "recent_30_pa") is None
    assert stats.get_pitcher_features("missing", RUN_DATE, "recent_30_pa") is None
    assert weather.get_weather_for_game(sample_game) is None
    assert ballpark.get_ballpark_context("Unknown Park") is None

    for provider in (stats, weather, ballpark):
        registration = get_provider(provider.provider_name)
        assert registration.placeholder
        assert provider.supported_modes == frozenset()
        assert provider.capabilities == frozenset()
        assert provider.requires_credentials is False
        assert provider.production_safe is False
        assert provider.can_be_used_for_production is False


def test_unsupported_contract_capability_fails_closed() -> None:
    with pytest.raises(
        ProviderCapabilityNotSupportedError, match="capability 'schedule'"
    ):
        require_provider_capability(
            "mlb_sample",
            "MLB",
            ProviderCapability.SCHEDULE,
            ProviderMode.SAMPLE,
        )


def test_provider_contracts_expose_no_betting_or_sizing_surface() -> None:
    forbidden = {
        "eligible_for_betting",
        "kelly_eligible",
        "stake",
        "unit_size",
        "ev",
        "fair_probability",
    }

    for protocol in (MLBProviderContract, *PROVIDER_PROTOCOLS):
        assert forbidden.isdisjoint(dir(protocol))
