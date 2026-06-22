"""Dedicated CourtVision MLB home run research-watchlist engine.

This unvalidated research module is isolated from production selection,
grading, and staking paths. Its scores are ranking signals, not probabilities.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from courtvision.sports.mlb.ballpark_factors import park_factor_reason
from courtvision.sports.mlb.hr_features import PowerFormScore, clamp_score, score_power_form
from courtvision.sports.mlb.pitch_matchup import PitchMatchupScore, score_pitch_matchup
from courtvision.sports.mlb.research_safety import (
    MLB_BETTING_APPROVAL_STATUS,
    MLB_NO_BETTING_REASON,
    MLB_RESEARCH_MODE,
    mlb_research_safety_fields,
)
from courtvision.sports.mlb.weather_factor import EnvironmentScore, score_environment


@dataclass(frozen=True, slots=True)
class HRPropInput:
    player: str
    team: str
    opponent: str
    pitcher: str
    sportsbook: str
    odds: int | str
    line: float
    game_time: datetime | str
    venue: str
    handedness: str
    recent_plate_appearances: int
    recent_batted_ball_events: Sequence[object] | int | float
    hard_hit_rate: float
    barrel_rate: float
    pull_rate: float
    pull_barrel_rate: float
    fly_ball_rate: float
    max_exit_velocity: float
    average_exit_velocity: float
    recent_home_runs: int
    pitcher_pitch_mix: Mapping[str, float]
    hitter_vs_pitch_type: Mapping[str, float]
    pitcher_hr_allowed_rate: float
    ballpark_hr_factor: float
    wind_direction: str
    wind_speed: float
    temperature: float
    data_quality: str = "Unvalidated research input"
    sport: str = field(default="MLB", init=False)
    mode: str = field(default=MLB_RESEARCH_MODE, init=False)
    eligible_for_betting: bool = field(default=False, init=False)
    kelly_eligible: bool = field(default=False, init=False)
    betting_approval_status: str = field(
        default=MLB_BETTING_APPROVAL_STATUS, init=False
    )
    no_betting_reason: str = field(default=MLB_NO_BETTING_REASON, init=False)

    def to_dict(self) -> dict[str, object]:
        payload = {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name
            not in {
                "sport",
                "mode",
                "eligible_for_betting",
                "kelly_eligible",
                "betting_approval_status",
                "no_betting_reason",
            }
        }
        payload["sport"] = "MLB"
        payload.update(mlb_research_safety_fields())
        return payload


class ResearchLabel(StrEnum):
    RESEARCH_WATCHLIST = "Research Watchlist"
    CANDIDATE = "Candidate"
    NOT_SELECTED = "Not Selected"


@dataclass(frozen=True, slots=True)
class HRPropAssessment:
    player: str
    team: str
    opponent: str
    market: str
    odds: str
    sportsbook: str
    pitcher: str
    venue: str
    game_time: datetime | str
    research_score: int
    research_label: ResearchLabel
    data_quality: str
    key_reasons: tuple[str, ...]
    component_scores: dict[str, float]
    sport: str = field(default="MLB", init=False)
    mode: str = field(default=MLB_RESEARCH_MODE, init=False)
    eligible_for_betting: bool = field(default=False, init=False)
    kelly_eligible: bool = field(default=False, init=False)
    betting_approval_status: str = field(
        default=MLB_BETTING_APPROVAL_STATUS, init=False
    )
    no_betting_reason: str = field(default=MLB_NO_BETTING_REASON, init=False)

    @property
    def matchup(self) -> str:
        return f"{self.team} vs {self.opponent} — {self.player} vs {self.pitcher}"

    @property
    def power_form_score(self) -> float:
        return self.component_scores["power_form"]

    @property
    def pitch_matchup_score(self) -> float:
        return self.component_scores["pitch_matchup"]

    @property
    def environment_score(self) -> float:
        return self.component_scores["environment"]

    @property
    def market_score(self) -> float:
        return self.component_scores["market"]

    def to_dict(self) -> dict[str, object]:
        payload = {
            "player": self.player,
            "team": self.team,
            "opponent": self.opponent,
            "sport": self.sport,
            "market": self.market,
            "odds": self.odds,
            "sportsbook": self.sportsbook,
            "pitcher": self.pitcher,
            "matchup": self.matchup,
            "venue": self.venue,
            "game_time": self.game_time.isoformat() if isinstance(self.game_time, datetime) else self.game_time,
            "research_score": self.research_score,
            "research_label": self.research_label.value,
            "data_quality": self.data_quality,
            "key_reasons": list(self.key_reasons),
            "power_form_score": self.power_form_score,
            "pitch_matchup_score": self.pitch_matchup_score,
            "environment_score": self.environment_score,
            "market_score": self.market_score,
            "component_scores": self.component_scores,
        }
        payload.update(mlb_research_safety_fields())
        return payload


def parse_american_odds(odds: int | str) -> int:
    if isinstance(odds, str):
        text = odds.strip().replace("−", "-")
        if not text:
            raise ValueError("American odds cannot be empty")
        value = int(text)
    else:
        value = int(odds)
    if value == 0:
        raise ValueError("American odds cannot be zero")
    return value


def format_american_odds(odds: int | str) -> str:
    value = parse_american_odds(odds)
    return f"+{value}" if value > 0 else str(value)


def implied_probability(odds: int | str) -> float:
    value = parse_american_odds(odds)
    if value > 0:
        return 100.0 / (value + 100.0)
    return abs(value) / (abs(value) + 100.0)


def score_market(
    *, odds: int | str, power_form_score: float, pitch_matchup_score: float, environment_score: float
) -> float:
    """Return an uncalibrated market-context research score from 0 to 100."""

    predictive_score = (
        power_form_score * 0.412
        + pitch_matchup_score * 0.353
        + environment_score * 0.235
    )
    # These are uncalibrated ranking points only. They deliberately are not
    # exposed as a probability, value estimate, or promotion gate.
    uncalibrated_power_points = 4.0 + 28.0 * (clamp_score(predictive_score) / 100.0)
    market_price_points = implied_probability(odds) * 100.0
    research_score_gap = uncalibrated_power_points - market_price_points
    odds_value = clamp_score(50.0 + research_score_gap * 4.0)
    price_attractiveness = clamp_score(((40.0 - market_price_points) / 35.0) * 100.0)
    confidence_vs_price = clamp_score(
        predictive_score
        * min(max(uncalibrated_power_points / market_price_points, 0.0), 1.25)
    )
    market_score = (
        odds_value * 0.55
        + price_attractiveness * 0.15
        + confidence_vs_price * 0.30
    )
    return round(clamp_score(market_score), 2)


def research_label_for_score(score: float) -> ResearchLabel:
    score = clamp_score(float(score))
    if score >= 85:
        return ResearchLabel.RESEARCH_WATCHLIST
    if score >= 65:
        return ResearchLabel.CANDIDATE
    return ResearchLabel.NOT_SELECTED


def _key_reasons(
    prop: HRPropInput,
    power: PowerFormScore,
    pitch: PitchMatchupScore,
    environment: EnvironmentScore,
    market_score: float,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if power.components["recent_barrels"] >= 65 or power.components["barrel_rate"] >= 65:
        reasons.append("Elevated recent barrel profile")
    if pitch.pitch_type_score >= 65:
        reasons.append("Positive pitch-type matchup")
    if environment.wind_effect == "out" and prop.wind_speed >= 7:
        reasons.append("Wind blowing out")
    park_reason = park_factor_reason(prop.ballpark_hr_factor)
    if park_reason == "Favorable HR park factor":
        reasons.append(park_reason)
    if power.components["hard_hit_rate"] >= 70:
        reasons.append("Elevated hard-hit rate")
    if power.components["max_exit_velocity"] >= 70:
        reasons.append("Impact-level max exit velocity")
    if pitch.pitcher_hr_score >= 65:
        reasons.append("Pitcher allows elevated HR contact")
    if prop.temperature >= 80:
        reasons.append("Warm hitting conditions")
    if market_score >= 65:
        reasons.append("Price context supports the research ranking")
    if not reasons:
        reasons.append("No notable HR research signal identified")
    return tuple(reasons[:5])


class HRPropEngine:
    """Stateless facade for evaluating one or more MLB HR candidates."""

    weights = {
        "power_form": 0.35,
        "pitch_matchup": 0.30,
        "environment": 0.20,
        "market": 0.15,
    }

    def score(self, prop: HRPropInput) -> HRPropAssessment:
        power = score_power_form(
            recent_plate_appearances=prop.recent_plate_appearances,
            recent_batted_ball_events=prop.recent_batted_ball_events,
            hard_hit_rate=prop.hard_hit_rate,
            barrel_rate=prop.barrel_rate,
            pull_rate=prop.pull_rate,
            pull_barrel_rate=prop.pull_barrel_rate,
            fly_ball_rate=prop.fly_ball_rate,
            max_exit_velocity=prop.max_exit_velocity,
            recent_home_runs=prop.recent_home_runs,
        )
        pitch = score_pitch_matchup(
            pitcher_pitch_mix=prop.pitcher_pitch_mix,
            hitter_vs_pitch_type=prop.hitter_vs_pitch_type,
            pitcher_hr_allowed_rate=prop.pitcher_hr_allowed_rate,
            handedness=prop.handedness,
        )
        environment = score_environment(
            ballpark_hr_factor=prop.ballpark_hr_factor,
            wind_direction=prop.wind_direction,
            wind_speed=prop.wind_speed,
            temperature=prop.temperature,
        )
        market = score_market(
            odds=prop.odds,
            power_form_score=power.score,
            pitch_matchup_score=pitch.score,
            environment_score=environment.score,
        )
        components = {
            "power_form": power.score,
            "pitch_matchup": pitch.score,
            "environment": environment.score,
            "market": market,
        }
        research_score = int(
            round(sum(components[name] * weight for name, weight in self.weights.items()))
        )
        return HRPropAssessment(
            player=prop.player,
            team=prop.team,
            opponent=prop.opponent,
            market=f"Over {prop.line:g} Home Runs",
            odds=format_american_odds(prop.odds),
            sportsbook=prop.sportsbook,
            pitcher=prop.pitcher,
            venue=prop.venue,
            game_time=prop.game_time,
            research_score=research_score,
            research_label=research_label_for_score(research_score),
            data_quality=prop.data_quality,
            key_reasons=_key_reasons(prop, power, pitch, environment, market),
            component_scores=components,
        )

    def rank(self, props: Sequence[HRPropInput]) -> list[HRPropAssessment]:
        return sorted(
            (self.score(prop) for prop in props),
            key=lambda assessment: assessment.research_score,
            reverse=True,
        )


def score_hr_prop(prop: HRPropInput) -> HRPropAssessment:
    return HRPropEngine().score(prop)


__all__ = [
    "HRPropAssessment",
    "HRPropEngine",
    "HRPropInput",
    "ResearchLabel",
    "format_american_odds",
    "implied_probability",
    "parse_american_odds",
    "research_label_for_score",
    "score_hr_prop",
    "score_market",
]
