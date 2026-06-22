from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from courtvision.core.provider_registry import (
    PROVIDER_REGISTRY,
    ProviderCapability,
    ProviderCapabilityNotSupportedError,
    ProviderMode,
    ProviderRegistration,
    ProviderRegistry,
    ProviderSourceType,
    get_provider,
    get_registered_providers,
    provider_can_run,
    provider_missing_credentials,
    provider_requires_credentials,
    provider_supports_mode,
    providers_for_capability,
    providers_for_sport,
    require_provider_capability,
)
from courtvision.core.sport_registry import SportCode


def _research_provider(name: str = "test_provider") -> ProviderRegistration:
    return ProviderRegistration(
        name=name,
        supported_sports=frozenset({SportCode.NBA}),
        supported_modes=frozenset({ProviderMode.RESEARCH}),
        capabilities=frozenset({ProviderCapability.PLAYER_STATS}),
        source_type=ProviderSourceType.MANUAL,
        can_be_used_for_research=True,
    )


def test_registry_is_empty_unless_explicitly_populated() -> None:
    registry = ProviderRegistry()

    assert registry.keys() == ()
    assert registry.all() == ()
    assert PROVIDER_REGISTRY.keys() == get_registered_providers()


def test_registration_is_immutable_and_duplicate_registration_fails() -> None:
    provider = _research_provider()
    registry = ProviderRegistry((provider,))

    with pytest.raises(FrozenInstanceError):
        provider.name = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="already registered"):
        registry.register(provider)


def test_unknown_provider_lookup_fails_clearly() -> None:
    with pytest.raises(KeyError, match="not registered"):
        get_provider("does_not_exist")


def test_sample_provider_requires_no_credentials() -> None:
    provider = get_provider("mlb_sample")

    assert provider.source_type is ProviderSourceType.SAMPLE
    assert not provider_requires_credentials(provider.name)
    assert provider_missing_credentials(provider.name, {}) == ()
    assert provider_can_run(
        provider.name,
        "MLB",
        "research_watchlist",
        "sample",
        {},
    )


def test_sample_provider_cannot_be_constructed_with_credentials() -> None:
    with pytest.raises(ValueError, match="must not require credentials"):
        ProviderRegistration(
            name="unsafe_sample",
            supported_sports=frozenset({SportCode.MLB}),
            supported_modes=frozenset({ProviderMode.SAMPLE}),
            capabilities=frozenset({ProviderCapability.ODDS}),
            required_environment_variables=("SECRET",),
            source_type=ProviderSourceType.SAMPLE,
            can_be_used_for_sample=True,
        )


def test_live_provider_with_missing_credentials_cannot_run() -> None:
    assert provider_missing_credentials("balldontlie", {}) == (
        "BALLDONTLIE_API_KEY",
    )
    assert not provider_can_run(
        "balldontlie", "NBA", "player_stats", "research", {}
    )
    assert provider_can_run(
        "balldontlie",
        "NBA",
        "player_stats",
        "research",
        {"BALLDONTLIE_API_KEY": "configured"},
    )


def test_api_nba_accepts_either_existing_key_name() -> None:
    assert provider_missing_credentials("api_nba", {}) == (
        "API_NBA_KEY or API_SPORTS_KEY",
    )
    assert provider_missing_credentials(
        "api_nba", {"API_SPORTS_KEY": "fallback-key"}
    ) == ()


def test_provider_capability_cannot_override_sport_approval() -> None:
    provider = ProviderRegistration(
        name="wnba_read_only_description",
        supported_sports=frozenset({SportCode.WNBA}),
        supported_modes=frozenset({ProviderMode.RESEARCH}),
        capabilities=frozenset({ProviderCapability.PLAYER_STATS}),
        source_type=ProviderSourceType.MANUAL,
        can_be_used_for_research=True,
    )
    registry = ProviderRegistry((provider,))

    with pytest.raises(ProviderCapabilityNotSupportedError, match="Sport 'WNBA'"):
        registry.require_capability(
            provider.name, "WNBA", "player_stats", "research"
        )


def test_mlb_sample_provider_is_research_and_sample_only() -> None:
    provider = get_provider("mlb_sample")

    assert provider.supported_modes == frozenset(
        {ProviderMode.RESEARCH, ProviderMode.SAMPLE}
    )
    assert not provider.production_safe
    assert not provider.can_be_used_for_production
    assert not provider_supports_mode(provider.name, ProviderMode.PRODUCTION)


def test_mlb_the_odds_api_is_not_production_approved() -> None:
    provider = get_provider("the_odds_api_mlb")

    assert provider.source_type is ProviderSourceType.LIVE
    assert provider.capabilities == frozenset(
        {ProviderCapability.ODDS, ProviderCapability.PLAYER_PROPS}
    )
    assert not provider.production_safe
    assert not provider_can_run(
        provider.name,
        "MLB",
        "odds",
        "production",
        {"COURTVISION_ODDS_API_KEY": "configured"},
    )


def test_existing_nba_providers_are_described_without_runtime_wiring() -> None:
    nba_names = {provider.name for provider in providers_for_sport("nba")}

    assert {
        "balldontlie",
        "sportsdataio",
        "api_nba",
        "the_odds_api_nba",
        "manual_schedule",
    }.issubset(nba_names)
    assert {provider.name for provider in providers_for_capability("NBA", "odds")} >= {
        "balldontlie",
        "sportsdataio",
        "the_odds_api_nba",
    }


def test_future_provider_candidates_are_not_registered() -> None:
    registered = set(get_registered_providers())

    assert registered.isdisjoint(
        {
            "sportsgameodds",
            "opticodds",
            "retrosheet",
            "baseball_savant",
            "lahman",
            "open_meteo",
        }
    )


def test_current_mlb_stubs_are_inert_placeholders() -> None:
    for name in (
        "mlb_stats_placeholder",
        "mlb_weather_placeholder",
        "mlb_ballpark_placeholder",
    ):
        provider = get_provider(name)
        assert provider.placeholder
        assert provider.supported_modes == frozenset()
        assert provider.capabilities == frozenset()


def test_require_provider_capability_fails_closed() -> None:
    with pytest.raises(ProviderCapabilityNotSupportedError, match="capability 'weather'"):
        require_provider_capability(
            "the_odds_api_mlb", "MLB", "weather", "research"
        )
    with pytest.raises(ProviderCapabilityNotSupportedError, match="mode 'production'"):
        require_provider_capability(
            "the_odds_api_mlb", "MLB", "odds", "production"
        )


def test_non_live_sources_cannot_expose_production() -> None:
    with pytest.raises(ValueError, match="cannot expose production"):
        ProviderRegistration(
            name="manual_production",
            supported_sports=frozenset({SportCode.NBA}),
            supported_modes=frozenset({ProviderMode.PRODUCTION}),
            capabilities=frozenset({ProviderCapability.SCHEDULE}),
            source_type=ProviderSourceType.MANUAL,
            production_safe=True,
            can_be_used_for_production=True,
        )
