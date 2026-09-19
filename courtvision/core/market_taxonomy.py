"""Immutable market semantics, independent of operational market support.

Definitions describe analytical targets only. They do not register markets,
select providers, or authorize execution, betting, or candidate promotion.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from courtvision.core.odds import normalize_market_type


class MarketFamily(str, Enum):
    PLAYER_PROP = "player_prop"
    MONEYLINE = "moneyline"
    SPREAD = "spread"
    TOTAL = "total"


class ParticipantScope(str, Enum):
    BATTER = "batter"
    PITCHER = "pitcher"
    PLAYER = "player"
    TEAM = "team"
    GAME = "game"


class MarketStatistic(str, Enum):
    HOME_RUNS = "home_runs"
    HITS = "hits"
    TOTAL_BASES = "total_bases"
    RUNS = "runs"
    RBIS = "rbis"
    WALKS = "walks"
    STRIKEOUTS = "strikeouts"
    STOLEN_BASES = "stolen_bases"
    OUTS_RECORDED = "outs_recorded"
    HITS_ALLOWED = "hits_allowed"
    EARNED_RUNS = "earned_runs"
    WALKS_ALLOWED = "walks_allowed"
    RUN_DIFFERENTIAL = "run_differential"
    GAME_RESULT = "game_result"
    POINTS = "points"
    REBOUNDS = "rebounds"
    ASSISTS = "assists"
    POINTS_REBOUNDS = "points_rebounds"
    POINTS_ASSISTS = "points_assists"
    REBOUNDS_ASSISTS = "rebounds_assists"
    POINTS_REBOUNDS_ASSISTS = "points_rebounds_assists"
    THREES = "threes"
    STEALS = "steals"
    BLOCKS = "blocks"
    POINT_DIFFERENTIAL = "point_differential"
    PASSING_YARDS = "passing_yards"
    RUSHING_YARDS = "rushing_yards"
    RECEIVING_YARDS = "receiving_yards"
    RECEPTIONS = "receptions"
    PASSING_TOUCHDOWNS = "passing_touchdowns"


class StatisticDomain(str, Enum):
    DISCRETE_COUNT = "discrete_count"
    DISCRETE_INTEGER = "discrete_integer"
    CONTINUOUS = "continuous"
    OUTCOME = "outcome"


class MarketPeriod(str, Enum):
    FULL_GAME = "full_game"
    FIRST_FIVE = "first_five"


class MarketApplicability(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    NOT_APPLICABLE = "not_applicable"


_Semantics = tuple[
    MarketFamily,
    ParticipantScope,
    MarketStatistic,
    MarketPeriod,
    MarketApplicability,
    MarketApplicability,
    bool,
    StatisticDomain,
]


def _prop(
    scope: ParticipantScope,
    statistic: MarketStatistic,
    domain: StatisticDomain = StatisticDomain.DISCRETE_COUNT,
) -> _Semantics:
    # Milestone selections may express their threshold in a selection label.
    return (
        MarketFamily.PLAYER_PROP, scope, statistic, MarketPeriod.FULL_GAME,
        MarketApplicability.OPTIONAL, MarketApplicability.OPTIONAL, True, domain,
    )


def _game(
    family: MarketFamily,
    scope: ParticipantScope,
    statistic: MarketStatistic,
    period: MarketPeriod = MarketPeriod.FULL_GAME,
) -> _Semantics:
    moneyline = family is MarketFamily.MONEYLINE
    domain = (
        StatisticDomain.OUTCOME if moneyline else
        StatisticDomain.DISCRETE_INTEGER if family is MarketFamily.SPREAD else
        StatisticDomain.DISCRETE_COUNT
    )
    return (
        family, scope, statistic, period,
        MarketApplicability.NOT_APPLICABLE if moneyline else MarketApplicability.REQUIRED,
        MarketApplicability.REQUIRED, not moneyline, domain,
    )


# This read-only table is a semantic vocabulary, not the operational SportRegistry.
_DEFINITIONS: Mapping[tuple[str, str], _Semantics] = MappingProxyType({
    **{
        ("MLB", f"batter_{statistic.value}"): _prop(ParticipantScope.BATTER, statistic)
        for statistic in (
            MarketStatistic.HOME_RUNS, MarketStatistic.HITS, MarketStatistic.TOTAL_BASES,
            MarketStatistic.RUNS, MarketStatistic.RBIS, MarketStatistic.WALKS,
            MarketStatistic.STRIKEOUTS, MarketStatistic.STOLEN_BASES,
        )
    },
    **{
        ("MLB", f"pitcher_{statistic.value}"): _prop(ParticipantScope.PITCHER, statistic)
        for statistic in (
            MarketStatistic.STRIKEOUTS, MarketStatistic.OUTS_RECORDED,
            MarketStatistic.HITS_ALLOWED, MarketStatistic.EARNED_RUNS,
            MarketStatistic.WALKS_ALLOWED,
        )
    },
    **{
        ("NBA", f"player_{statistic.value}"): _prop(ParticipantScope.PLAYER, statistic)
        for statistic in (
            MarketStatistic.POINTS, MarketStatistic.REBOUNDS, MarketStatistic.ASSISTS,
            MarketStatistic.POINTS_REBOUNDS, MarketStatistic.POINTS_ASSISTS,
            MarketStatistic.REBOUNDS_ASSISTS, MarketStatistic.POINTS_REBOUNDS_ASSISTS,
            MarketStatistic.STEALS, MarketStatistic.BLOCKS,
        )
    },
    # NBA runtime normalization owns aliases; use its canonical market key here.
    ("NBA", "player_3pt_made"): _prop(ParticipantScope.PLAYER, MarketStatistic.THREES),
    **{
        ("NFL", f"player_{statistic.value}"): _prop(
            ParticipantScope.PLAYER, statistic, StatisticDomain.DISCRETE_INTEGER,
        )
        for statistic in (
            MarketStatistic.PASSING_YARDS, MarketStatistic.RUSHING_YARDS,
            MarketStatistic.RECEIVING_YARDS,
        )
    },
    **{
        ("NFL", f"player_{statistic.value}"): _prop(ParticipantScope.PLAYER, statistic)
        for statistic in (MarketStatistic.RECEPTIONS, MarketStatistic.PASSING_TOUCHDOWNS)
    },
    **{
        (sport, "moneyline"): _game(
            MarketFamily.MONEYLINE, ParticipantScope.TEAM, MarketStatistic.GAME_RESULT,
        )
        for sport in ("MLB", "NBA", "NFL")
    },
    **{
        (sport, market_type): _game(MarketFamily.TOTAL, scope, statistic)
        for sport, statistic in (
            ("MLB", MarketStatistic.RUNS),
            ("NBA", MarketStatistic.POINTS),
            ("NFL", MarketStatistic.POINTS),
        )
        for market_type, scope in (
            ("game_total", ParticipantScope.GAME), ("team_total", ParticipantScope.TEAM),
        )
    },
    ("MLB", "run_line"): _game(
        MarketFamily.SPREAD, ParticipantScope.TEAM, MarketStatistic.RUN_DIFFERENTIAL,
    ),
    **{
        (sport, "spread"): _game(
            MarketFamily.SPREAD, ParticipantScope.TEAM, MarketStatistic.POINT_DIFFERENTIAL,
        )
        for sport in ("NBA", "NFL")
    },
    ("MLB", "first_five_moneyline"): _game(
        MarketFamily.MONEYLINE, ParticipantScope.TEAM,
        MarketStatistic.GAME_RESULT, MarketPeriod.FIRST_FIVE,
    ),
    ("MLB", "first_five_run_line"): _game(
        MarketFamily.SPREAD, ParticipantScope.TEAM,
        MarketStatistic.RUN_DIFFERENTIAL, MarketPeriod.FIRST_FIVE,
    ),
    ("MLB", "first_five_total"): _game(
        MarketFamily.TOTAL, ParticipantScope.GAME,
        MarketStatistic.RUNS, MarketPeriod.FIRST_FIVE,
    ),
})


def _market_key(sport: str, market_type: str) -> tuple[str, str]:
    if not isinstance(sport, str) or not sport.strip():
        raise ValueError("sport must be a non-empty string")
    key = sport.strip().upper(), normalize_market_type(market_type)
    if key not in _DEFINITIONS:
        raise ValueError(f"Undefined market taxonomy: {key[0]}/{key[1]}")
    return key


@dataclass(frozen=True, slots=True)
class MarketTaxonomy:
    """Validated analytical definition; no operational support is implied."""

    sport: str
    market_type: str
    market_family: MarketFamily
    participant_scope: ParticipantScope
    statistic: MarketStatistic
    period: MarketPeriod
    line_applicability: MarketApplicability
    side_applicability: MarketApplicability
    supports_alternate_thresholds: bool
    statistic_domain: StatisticDomain

    def __post_init__(self) -> None:
        key = _market_key(self.sport, self.market_type)
        object.__setattr__(self, "sport", key[0])
        object.__setattr__(self, "market_type", key[1])
        for field_name, enum_type in (
            ("market_family", MarketFamily),
            ("participant_scope", ParticipantScope),
            ("statistic", MarketStatistic),
            ("period", MarketPeriod),
            ("line_applicability", MarketApplicability),
            ("side_applicability", MarketApplicability),
            ("statistic_domain", StatisticDomain),
        ):
            object.__setattr__(self, field_name, enum_type(getattr(self, field_name)))
        if not isinstance(self.supports_alternate_thresholds, bool):
            raise ValueError("supports_alternate_thresholds must be a boolean")
        actual = (
            self.market_family, self.participant_scope, self.statistic, self.period,
            self.line_applicability, self.side_applicability,
            self.supports_alternate_thresholds, self.statistic_domain,
        )
        if actual != _DEFINITIONS[key]:
            raise ValueError(f"Market semantics do not match defined market {key[0]}/{key[1]}")


def resolve_market_taxonomy(sport: str, market_type: str) -> MarketTaxonomy:
    """Resolve canonical markets without consulting or modifying runtime support.

    Sport/provider adapters must normalize aliases before taxonomy resolution.
    """

    key = _market_key(sport, market_type)
    return MarketTaxonomy(*key, *_DEFINITIONS[key])


__all__ = [
    "MarketApplicability", "MarketFamily", "MarketPeriod", "MarketStatistic",
    "MarketTaxonomy", "ParticipantScope", "StatisticDomain", "resolve_market_taxonomy",
]
