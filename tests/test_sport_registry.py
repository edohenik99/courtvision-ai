from __future__ import annotations

import pytest

from courtvision.core.sport_registry import (
    SPORT_REGISTRY,
    CapabilityNotSupportedError,
    SportCapability,
    SportCode,
    SportMode,
    SportPlugin,
    SportRegistry,
    get_plugin,
    get_registered_sports,
    get_sport,
    is_betting_approved,
    is_kelly_allowed,
    require_capability,
    supports_capability,
    supports_mode,
)


def _reserved_plugin(sport: SportCode = SportCode.WNBA) -> SportPlugin:
    return SportPlugin(
        sport_code=sport,
        plugin_name=f"{sport.value.lower()}_test_reserved",
        supported_markets=(),
    )


def test_default_registry_contains_all_typed_sports() -> None:
    assert SPORT_REGISTRY.keys() == ("NBA", "WNBA", "MLB", "NFL", "NHL")
    assert get_registered_sports() == (
        SportCode.NBA,
        SportCode.WNBA,
        SportCode.MLB,
        SportCode.NFL,
        SportCode.NHL,
    )
    assert get_plugin("mlb") is get_sport(SportCode.MLB)


def test_nba_is_the_only_production_capable_sport() -> None:
    nba = get_plugin(SportCode.NBA)

    assert nba.plugin_name == "nba_legacy_runtime"
    assert supports_mode("NBA", SportMode.PRODUCTION)
    assert supports_mode("NBA", SportMode.RESEARCH)
    assert supports_capability("NBA", SportCapability.SCHEDULE)
    assert supports_capability("NBA", SportCapability.ODDS)
    assert supports_capability("NBA", SportCapability.PROJECTIONS)
    assert supports_capability("NBA", SportCapability.HISTORICAL_TRAINING)
    assert not supports_capability("NBA", SportCapability.BACKTESTING)
    assert is_betting_approved("NBA")
    assert is_kelly_allowed("NBA")

    for sport in (SportCode.MLB, SportCode.WNBA, SportCode.NFL, SportCode.NHL):
        assert not supports_mode(sport, SportMode.PRODUCTION)
        assert not is_betting_approved(sport)
        assert not is_kelly_allowed(sport)


def test_mlb_is_research_sample_only_with_research_odds_capability() -> None:
    mlb = get_plugin("MLB")

    assert mlb.supported_modes == frozenset({SportMode.RESEARCH, SportMode.SAMPLE})
    assert mlb.capabilities == frozenset(
        {SportCapability.ODDS, SportCapability.RESEARCH_WATCHLIST}
    )
    assert mlb.supports_market("total_bases")
    assert not supports_capability("MLB", SportCapability.SCHEDULE)
    assert not supports_capability("MLB", SportCapability.HISTORICAL_TRAINING)
    assert not supports_capability("MLB", SportCapability.BACKTESTING)
    assert not supports_capability("MLB", SportCapability.BETTING_APPROVAL)
    assert not supports_capability("MLB", SportCapability.KELLY_SIZING)


@pytest.mark.parametrize("sport", [SportCode.WNBA, SportCode.NFL, SportCode.NHL])
def test_placeholder_sports_are_reserved_without_executable_capabilities(
    sport: SportCode,
) -> None:
    plugin = get_plugin(sport)

    assert plugin.reserved is True
    assert plugin.supported_modes == frozenset()
    assert plugin.capabilities == frozenset()


def test_registering_a_sport_does_not_imply_betting_or_kelly_approval() -> None:
    plugin = _reserved_plugin()
    registry = SportRegistry([plugin])

    assert registry.get(SportCode.WNBA) is plugin
    assert not plugin.supports_capability(SportCapability.BETTING_APPROVAL)
    assert not plugin.supports_capability(SportCapability.KELLY_SIZING)


def test_missing_capabilities_fail_closed_with_clear_error() -> None:
    with pytest.raises(
        CapabilityNotSupportedError,
        match="MLB.*does not support capability 'kelly_sizing'",
    ):
        require_capability("MLB", SportCapability.KELLY_SIZING)


def test_require_capability_returns_plugin_when_explicitly_registered() -> None:
    assert require_capability("NBA", SportCapability.PROJECTIONS) is get_plugin("NBA")


def test_registry_rejects_duplicate_sports() -> None:
    plugin = _reserved_plugin()
    registry = SportRegistry([plugin])

    with pytest.raises(ValueError, match="already registered"):
        registry.register(plugin)


def test_research_only_plugin_cannot_declare_production_capabilities() -> None:
    with pytest.raises(ValueError, match="require production mode"):
        SportPlugin(
            sport_code=SportCode.NBA,
            plugin_name="unsafe_research_plugin",
            supported_markets=("points",),
            supported_modes=frozenset({SportMode.RESEARCH}),
            capabilities=frozenset({SportCapability.BETTING_APPROVAL}),
        )


def test_sample_plugin_cannot_also_expose_production_mode() -> None:
    with pytest.raises(ValueError, match="Sample plugins"):
        SportPlugin(
            sport_code=SportCode.NBA,
            plugin_name="unsafe_sample_plugin",
            supported_markets=("points",),
            supported_modes=frozenset({SportMode.SAMPLE, SportMode.PRODUCTION}),
        )


def test_mlb_cannot_be_constructed_as_a_production_plugin() -> None:
    with pytest.raises(ValueError, match="MLB.*production"):
        SportPlugin(
            sport_code=SportCode.MLB,
            plugin_name="unsafe_mlb_plugin",
            supported_markets=("home_runs",),
            supported_modes=frozenset({SportMode.PRODUCTION}),
            capabilities=frozenset(
                {SportCapability.BETTING_APPROVAL, SportCapability.KELLY_SIZING}
            ),
        )


def test_non_nba_sports_cannot_be_constructed_as_production_plugins() -> None:
    with pytest.raises(ValueError, match="WNBA is not approved for production mode"):
        SportPlugin(
            sport_code=SportCode.WNBA,
            plugin_name="unsafe_wnba_plugin",
            supported_markets=("points",),
            supported_modes=frozenset({SportMode.PRODUCTION}),
        )


def test_backward_compatible_registry_properties_remain_available() -> None:
    nba = get_sport("nba")

    assert nba.sport_name == "NBA"
    assert nba.supported_prop_markets is nba.supported_markets
    assert nba.supported_props is nba.supported_markets
    assert nba.to_dict()["sport_name"] == "NBA"
