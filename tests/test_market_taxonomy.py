from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from courtvision.core.market_taxonomy import (
    MarketApplicability,
    MarketFamily,
    MarketPeriod,
    MarketStatistic,
    ParticipantScope,
    StatisticDomain,
    resolve_market_taxonomy,
)
from courtvision.core.sport_registry import SPORT_REGISTRY, get_plugin
from courtvision.markets.prop_types import canonical_market_type_from_prop_type
from courtvision.runtime_markets import normalize_market_alias


@pytest.mark.parametrize("market_type,statistic", [
    ("player_points", MarketStatistic.POINTS),
    ("player_rebounds", MarketStatistic.REBOUNDS),
    ("player_assists", MarketStatistic.ASSISTS),
    ("player_3pt_made", MarketStatistic.THREES),
    ("player_steals", MarketStatistic.STEALS),
    ("player_blocks", MarketStatistic.BLOCKS),
    ("player_points_rebounds", MarketStatistic.POINTS_REBOUNDS),
    ("player_points_assists", MarketStatistic.POINTS_ASSISTS),
    ("player_rebounds_assists", MarketStatistic.REBOUNDS_ASSISTS),
    ("player_points_rebounds_assists", MarketStatistic.POINTS_REBOUNDS_ASSISTS),
])
def test_existing_canonical_nba_markets_resolve(market_type: str, statistic: MarketStatistic) -> None:
    before = tuple(plugin.to_dict() for plugin in SPORT_REGISTRY.all())
    assert normalize_market_alias(market_type) == market_type
    assert canonical_market_type_from_prop_type(market_type) == market_type
    market = resolve_market_taxonomy("NBA", market_type)
    assert market.market_type == market_type
    assert market.statistic is statistic
    assert market.participant_scope is ParticipantScope.PLAYER
    assert market.market_family is MarketFamily.PLAYER_PROP
    assert market.statistic_domain is StatisticDomain.DISCRETE_COUNT
    assert tuple(plugin.to_dict() for plugin in SPORT_REGISTRY.all()) == before


@pytest.mark.parametrize("alias,canonical", [
    ("player_threes", "player_3pt_made"),
    ("threes", "player_3pt_made"),
    ("pra", "player_points_rebounds_assists"),
    ("points_rebounds", "player_points_rebounds"),
    ("points_assists", "player_points_assists"),
    ("rebounds_assists", "player_rebounds_assists"),
])
def test_nba_aliases_are_normalized_before_taxonomy_resolution(alias: str, canonical: str) -> None:
    with pytest.raises(ValueError, match="Undefined market taxonomy"):
        resolve_market_taxonomy("NBA", alias)
    assert normalize_market_alias(alias) == canonical
    assert resolve_market_taxonomy("NBA", normalize_market_alias(alias)).market_type == canonical


def test_hr_semantics_are_normalized_deterministic_and_immutable() -> None:
    market = resolve_market_taxonomy(" mlb ", " Batter Home Runs ")

    assert market == resolve_market_taxonomy("MLB", "batter_home_runs")
    assert market.sport == "MLB"
    assert market.market_type == "batter_home_runs"
    assert market.market_family is MarketFamily.PLAYER_PROP
    assert market.participant_scope is ParticipantScope.BATTER
    assert market.statistic is MarketStatistic.HOME_RUNS
    assert market.statistic_domain is StatisticDomain.DISCRETE_COUNT
    assert market.period is MarketPeriod.FULL_GAME
    assert market.line_applicability is MarketApplicability.OPTIONAL
    assert market.side_applicability is MarketApplicability.OPTIONAL
    assert market.supports_alternate_thresholds is True
    assert not hasattr(market, "__dict__")
    with pytest.raises(FrozenInstanceError):
        market.sport = "NBA"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("market_type", "scope", "statistic"),
    [
        ("batter_hits", ParticipantScope.BATTER, MarketStatistic.HITS),
        ("batter_total_bases", ParticipantScope.BATTER, MarketStatistic.TOTAL_BASES),
        ("batter_runs", ParticipantScope.BATTER, MarketStatistic.RUNS),
        ("batter_rbis", ParticipantScope.BATTER, MarketStatistic.RBIS),
        ("batter_walks", ParticipantScope.BATTER, MarketStatistic.WALKS),
        ("batter_strikeouts", ParticipantScope.BATTER, MarketStatistic.STRIKEOUTS),
        ("batter_stolen_bases", ParticipantScope.BATTER, MarketStatistic.STOLEN_BASES),
        ("pitcher_strikeouts", ParticipantScope.PITCHER, MarketStatistic.STRIKEOUTS),
        ("pitcher_outs_recorded", ParticipantScope.PITCHER, MarketStatistic.OUTS_RECORDED),
        ("pitcher_hits_allowed", ParticipantScope.PITCHER, MarketStatistic.HITS_ALLOWED),
        ("pitcher_earned_runs", ParticipantScope.PITCHER, MarketStatistic.EARNED_RUNS),
        ("pitcher_walks_allowed", ParticipantScope.PITCHER, MarketStatistic.WALKS_ALLOWED),
    ],
)
def test_future_mlb_player_semantics(
    market_type: str, scope: ParticipantScope, statistic: MarketStatistic,
) -> None:
    market = resolve_market_taxonomy("MLB", market_type)

    assert market.participant_scope is scope
    assert market.statistic is statistic
    assert market.market_family is MarketFamily.PLAYER_PROP
    assert market.period is MarketPeriod.FULL_GAME
    assert market.statistic_domain is StatisticDomain.DISCRETE_COUNT


@pytest.mark.parametrize(
    ("market_type", "family", "scope", "statistic", "period"),
    [
        ("moneyline", MarketFamily.MONEYLINE, ParticipantScope.TEAM,
         MarketStatistic.GAME_RESULT, MarketPeriod.FULL_GAME),
        ("run_line", MarketFamily.SPREAD, ParticipantScope.TEAM,
         MarketStatistic.RUN_DIFFERENTIAL, MarketPeriod.FULL_GAME),
        ("game_total", MarketFamily.TOTAL, ParticipantScope.GAME,
         MarketStatistic.RUNS, MarketPeriod.FULL_GAME),
        ("team_total", MarketFamily.TOTAL, ParticipantScope.TEAM,
         MarketStatistic.RUNS, MarketPeriod.FULL_GAME),
        ("first_five_moneyline", MarketFamily.MONEYLINE, ParticipantScope.TEAM,
         MarketStatistic.GAME_RESULT, MarketPeriod.FIRST_FIVE),
        ("first_five_run_line", MarketFamily.SPREAD, ParticipantScope.TEAM,
         MarketStatistic.RUN_DIFFERENTIAL, MarketPeriod.FIRST_FIVE),
        ("first_five_total", MarketFamily.TOTAL, ParticipantScope.GAME,
         MarketStatistic.RUNS, MarketPeriod.FIRST_FIVE),
    ],
)
def test_future_mlb_game_semantics(
    market_type: str, family: MarketFamily, scope: ParticipantScope,
    statistic: MarketStatistic, period: MarketPeriod,
) -> None:
    market = resolve_market_taxonomy("MLB", market_type)

    assert market.market_family is family
    assert market.participant_scope is scope
    assert market.statistic is statistic
    assert market.period is period
    assert market.side_applicability is MarketApplicability.REQUIRED
    if family is MarketFamily.MONEYLINE:
        assert market.line_applicability is MarketApplicability.NOT_APPLICABLE
        assert market.statistic_domain is StatisticDomain.OUTCOME
        assert market.supports_alternate_thresholds is False
    else:
        assert market.line_applicability is MarketApplicability.REQUIRED
        assert market.supports_alternate_thresholds is True


@pytest.mark.parametrize(
    ("sport", "market_type", "statistic", "domain"),
    [
        ("NBA", "player_points", MarketStatistic.POINTS, StatisticDomain.DISCRETE_COUNT),
        ("NBA", "player_rebounds", MarketStatistic.REBOUNDS, StatisticDomain.DISCRETE_COUNT),
        ("NFL", "player_passing_yards", MarketStatistic.PASSING_YARDS,
         StatisticDomain.DISCRETE_INTEGER),
        ("NFL", "player_receptions", MarketStatistic.RECEPTIONS,
         StatisticDomain.DISCRETE_COUNT),
    ],
)
def test_nba_and_nfl_specialist_targets(
    sport: str, market_type: str, statistic: MarketStatistic, domain: StatisticDomain,
) -> None:
    market = resolve_market_taxonomy(sport, market_type)

    assert market.participant_scope is ParticipantScope.PLAYER
    assert market.statistic is statistic
    assert market.statistic_domain is domain
    assert market.period is MarketPeriod.FULL_GAME


@pytest.mark.parametrize(
    ("sport", "market_type"),
    [("NBA", "batter_home_runs"), ("MLB", "player_points"),
     ("NFL", "first_five_total"), ("UNKNOWN", "moneyline"),
     ("MLB", "mystery_market"), ("", "batter_home_runs"),
     ("MLB", ""), ("MLB", "???"), (None, "moneyline")],
)
def test_unknown_and_cross_sport_markets_fail_closed(sport: str, market_type: str) -> None:
    with pytest.raises(ValueError):
        resolve_market_taxonomy(sport, market_type)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("sport", "NBA"),
        ("market_type", "batter_hits"),
        ("market_family", MarketFamily.TOTAL),
        ("participant_scope", ParticipantScope.PITCHER),
        ("statistic", MarketStatistic.STRIKEOUTS),
        ("period", MarketPeriod.FIRST_FIVE),
        ("line_applicability", MarketApplicability.REQUIRED),
        ("side_applicability", MarketApplicability.NOT_APPLICABLE),
        ("supports_alternate_thresholds", False),
        ("supports_alternate_thresholds", 1),
        ("statistic_domain", StatisticDomain.CONTINUOUS),
        ("market_family", "unknown"),
    ],
)
def test_inconsistent_direct_construction_fails_closed(field_name: str, value: object) -> None:
    market = resolve_market_taxonomy("MLB", "batter_home_runs")
    with pytest.raises(ValueError):
        replace(market, **{field_name: value})


def test_definitions_and_alternate_capability_do_not_enable_operations() -> None:
    before = tuple(plugin.to_dict() for plugin in SPORT_REGISTRY.all())
    for sport, market_type in (
        ("MLB", "pitcher_outs_recorded"),
        ("MLB", "first_five_total"),
        ("NFL", "player_passing_yards"),
    ):
        market = resolve_market_taxonomy(sport, market_type)
        assert market.supports_alternate_thresholds is True
        assert not get_plugin(sport).supports_market(market_type)
        assert not hasattr(market, "eligible_for_betting")
        assert not hasattr(market, "promote")
    assert tuple(plugin.to_dict() for plugin in SPORT_REGISTRY.all()) == before
    assert get_plugin("NFL").reserved is True
