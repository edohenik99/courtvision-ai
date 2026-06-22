"""Typed adapter contracts for MLB home run prop data sources.

The contracts in this module are deliberately provider-neutral.  They describe
the data the HR prop pipeline needs without selecting an API, credential
strategy, or sportsbook priority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True, slots=True)
class OddsQuote:
    """One sportsbook quote for a player prop market."""

    player: str
    market: str
    line: float
    odds: int | str
    sportsbook: str
    game_id: str
    timestamp: datetime | str


@dataclass(frozen=True, slots=True)
class HitterStats:
    """Hitter and Statcast-style inputs required by the HR engine."""

    player: str
    recent_plate_appearances: int
    recent_batted_ball_events: Sequence[object]
    hard_hit_rate: float
    barrel_rate: float
    pull_rate: float
    pull_barrel_rate: float
    fly_ball_rate: float
    max_exit_velocity: float
    average_exit_velocity: float
    recent_home_runs: int
    hitter_vs_pitch_type: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class PitcherStats:
    """Pitcher inputs required for an HR matchup assessment."""

    pitcher: str
    pitcher_pitch_mix: Mapping[str, float]
    pitcher_hr_allowed_rate: float


@dataclass(frozen=True, slots=True)
class WeatherConditions:
    """Weather inputs for one venue and game time."""

    venue: str
    game_time: datetime | str
    temperature: float
    wind_speed: float
    wind_direction: str
    precipitation_risk: float
    roof_status: str | None = None


@dataclass(frozen=True, slots=True)
class BallparkFactors:
    """Venue-level HR context, including future adjustment placeholders."""

    venue: str
    hr_factor: float
    handedness_adjustment: Mapping[str, float] | None = None
    park_dimensions: Mapping[str, float] | None = None


@runtime_checkable
class OddsProvider(Protocol):
    """Contract for retrieving sportsbook HR prop quotes."""

    def get_odds(self, report_date: date) -> Sequence[OddsQuote]:
        """Return available odds for the requested MLB slate."""


@runtime_checkable
class HitterStatsProvider(Protocol):
    """Contract for retrieving hitter and batted-ball statistics."""

    def get_hitter_stats(self, player: str, *, game_id: str | None = None) -> HitterStats:
        """Return current HR-model inputs for a hitter."""


@runtime_checkable
class PitcherStatsProvider(Protocol):
    """Contract for retrieving opposing-pitcher statistics."""

    def get_pitcher_stats(self, pitcher: str, *, game_id: str | None = None) -> PitcherStats:
        """Return current HR-model inputs for a pitcher."""


@runtime_checkable
class WeatherProvider(Protocol):
    """Contract for retrieving game-time weather conditions."""

    def get_weather(
        self,
        venue: str,
        game_time: datetime | str,
        *,
        game_id: str | None = None,
    ) -> WeatherConditions:
        """Return weather conditions for a scheduled game."""


@runtime_checkable
class BallparkProvider(Protocol):
    """Contract for retrieving venue-level home run factors."""

    def get_ballpark(self, venue: str) -> BallparkFactors:
        """Return HR factors and available dimensions for a venue."""


__all__ = [
    "BallparkFactors",
    "BallparkProvider",
    "HitterStats",
    "HitterStatsProvider",
    "OddsProvider",
    "OddsQuote",
    "PitcherStats",
    "PitcherStatsProvider",
    "WeatherConditions",
    "WeatherProvider",
]
