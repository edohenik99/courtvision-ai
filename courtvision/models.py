from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Team:
    id: int
    abbreviation: str
    full_name: str


@dataclass(slots=True)
class Game:
    id: int
    date: str
    home_team: Team
    visitor_team: Team
    home_team_score: int | None = None
    visitor_team_score: int | None = None
    status: str = ""


@dataclass(slots=True)
class Injury:
    player_id: int
    player_name: str
    team_id: int
    team_abbreviation: str
    status: str
    description: str = ""


@dataclass(slots=True)
class PlayerInfo:
    id: int
    first_name: str
    last_name: str
    full_name: str
    team_id: int
    team_abbreviation: str
    position: str = ""


@dataclass(slots=True)
class PlayerGameStats:
    player_id: int
    player_name: str
    team_id: int
    team_abbreviation: str
    game_id: int
    game_date: str
    minutes: float
    points: float
    rebounds: float
    assists: float
    threes: float
    steals: float
    blocks: float


@dataclass(slots=True)
class Projection:
    player_id: int
    player_name: str
    team_abbreviation: str
    opponent_abbreviation: str
    points: float
    rebounds: float
    assists: float
    threes: float
    steals: float
    blocks: float
    expected_minutes: float
    usage_boost: float
    injury_boost: float
    matchup_boost: float
    exposure_score: float
    confidence_tier: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "player_name": self.player_name,
            "team_abbreviation": self.team_abbreviation,
            "opponent_abbreviation": self.opponent_abbreviation,
            "points": self.points,
            "rebounds": self.rebounds,
            "assists": self.assists,
            "threes": self.threes,
            "steals": self.steals,
            "blocks": self.blocks,
            "expected_minutes": self.expected_minutes,
            "usage_boost": self.usage_boost,
            "injury_boost": self.injury_boost,
            "matchup_boost": self.matchup_boost,
            "exposure_score": self.exposure_score,
            "confidence_tier": self.confidence_tier,
            "reasons": "; ".join(self.reasons),
        }


@dataclass(slots=True)
class PlayerProjection:
    player_id: int
    player_name: str
    team_id: int
    team_abbreviation: str
    game_id: int
    opponent_abbreviation: str
    expected_minutes: float
    exposure_score: float
    confidence: str
    stat_projections: dict[str, float]
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MarketProp:
    id: int
    game_id: int
    player_id: int
    player_name: str
    vendor: str
    prop_type: str
    line_value: float
    market_type: str
    over_odds: int | None
    under_odds: int | None
    updated_at: str = ""


@dataclass(slots=True)
class RankedPlay:
    rank: int
    game_id: int
    player_id: int
    player_name: str
    team_abbreviation: str
    opponent_abbreviation: str
    vendor: str
    prop_type: str
    side: str
    line_value: float
    projection: float
    edge: float
    confidence: str
    exposure_score: float
    fair_probability: float
    offered_odds: int | None
    kelly_fraction: float
    score: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "game_id": self.game_id,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "team_abbreviation": self.team_abbreviation,
            "opponent_abbreviation": self.opponent_abbreviation,
            "vendor": self.vendor,
            "prop_type": self.prop_type,
            "side": self.side,
            "line_value": self.line_value,
            "projection": self.projection,
            "edge": self.edge,
            "confidence": self.confidence,
            "exposure_score": self.exposure_score,
            "fair_probability": self.fair_probability,
            "offered_odds": self.offered_odds,
            "kelly_fraction": self.kelly_fraction,
            "score": self.score,
            "notes": "; ".join(self.notes),
        }


@dataclass(slots=True)
class GradedPick:
    prediction_date: str
    game_id: int
    player_id: int
    player_name: str
    team_abbreviation: str
    opponent_abbreviation: str
    vendor: str
    prop_type: str
    side: str
    line_value: float
    projection: float
    actual_value: float | None
    result: str
    edge: float
    confidence: str
    exposure_score: float
    fair_probability: float
    offered_odds: int | None
    kelly_fraction: float
    score: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "prediction_date": self.prediction_date,
            "game_id": self.game_id,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "team_abbreviation": self.team_abbreviation,
            "opponent_abbreviation": self.opponent_abbreviation,
            "vendor": self.vendor,
            "prop_type": self.prop_type,
            "side": self.side,
            "line_value": self.line_value,
            "projection": self.projection,
            "actual_value": self.actual_value,
            "result": self.result,
            "edge": self.edge,
            "confidence": self.confidence,
            "exposure_score": self.exposure_score,
            "fair_probability": self.fair_probability,
            "offered_odds": self.offered_odds,
            "kelly_fraction": self.kelly_fraction,
            "score": self.score,
            "notes": "; ".join(self.notes),
        }


GradedProp = GradedPick


__all__ = [
    "Game",
    "GradedProp",
    "GradedPick",
    "Injury",
    "MarketProp",
    "PlayerGameStats",
    "PlayerInfo",
    "PlayerProjection",
    "Projection",
    "RankedPlay",
    "Team",
]
