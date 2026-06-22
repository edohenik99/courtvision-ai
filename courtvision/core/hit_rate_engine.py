"""Reusable hit-rate calculations for player prop histories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

Direction = Literal["over", "under"]


@dataclass(frozen=True, slots=True)
class HitRateObservation:
    """One game, ordered oldest-to-newest when passed to the engine."""

    value: float
    is_home: bool
    opponent: str


@dataclass(frozen=True, slots=True)
class HitRateSummary:
    last_5_hit_rate: float | None
    last_10_hit_rate: float | None
    last_20_hit_rate: float | None
    season_hit_rate: float | None
    home_hit_rate: float | None
    away_hit_rate: float | None
    home_away_hit_rate: float | None
    opponent_adjusted_hit_rate: float | None
    line_specific_hit_rate: float | None
    sample_size: int

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "last_5_hit_rate": self.last_5_hit_rate,
            "last_10_hit_rate": self.last_10_hit_rate,
            "last_20_hit_rate": self.last_20_hit_rate,
            "season_hit_rate": self.season_hit_rate,
            "home_hit_rate": self.home_hit_rate,
            "away_hit_rate": self.away_hit_rate,
            "home_away_hit_rate": self.home_away_hit_rate,
            "opponent_adjusted_hit_rate": self.opponent_adjusted_hit_rate,
            "line_specific_hit_rate": self.line_specific_hit_rate,
            "sample_size": self.sample_size,
        }


class HitRateEngine:
    """Stateless engine facade for dependency-injected application code."""

    @staticmethod
    def calculate(
        observations: Iterable[HitRateObservation],
        *,
        line: float,
        direction: Direction = "over",
        upcoming_is_home: bool | None = None,
        opponent: str | None = None,
    ) -> HitRateSummary:
        return calculate_hit_rates(
            observations,
            line=line,
            direction=direction,
            upcoming_is_home=upcoming_is_home,
            opponent=opponent,
        )


def calculate_hit_rates(
    observations: Iterable[HitRateObservation],
    *,
    line: float,
    direction: Direction = "over",
    upcoming_is_home: bool | None = None,
    opponent: str | None = None,
) -> HitRateSummary:
    """Calculate standard windows at a proposed line.

    Inputs must be chronological (oldest to newest). Pushes are excluded from
    both wins and attempts so the result represents settled decisions only.
    """

    if direction not in {"over", "under"}:
        raise ValueError("direction must be 'over' or 'under'")

    rows = list(observations)

    def rate(sample: list[HitRateObservation]) -> float | None:
        settled = [row for row in sample if row.value != line]
        if not settled:
            return None
        hits = sum(
            row.value > line if direction == "over" else row.value < line
            for row in settled
        )
        return round((hits / len(settled)) * 100.0, 1)

    home_rows = [row for row in rows if row.is_home]
    away_rows = [row for row in rows if not row.is_home]
    venue_rows = home_rows if upcoming_is_home is True else away_rows if upcoming_is_home is False else []
    opponent_rows = (
        [row for row in rows if row.opponent.strip().upper() == opponent.strip().upper()]
        if opponent
        else []
    )

    return HitRateSummary(
        last_5_hit_rate=rate(rows[-5:]),
        last_10_hit_rate=rate(rows[-10:]),
        last_20_hit_rate=rate(rows[-20:]),
        season_hit_rate=rate(rows),
        home_hit_rate=rate(home_rows),
        away_hit_rate=rate(away_rows),
        home_away_hit_rate=rate(venue_rows) if upcoming_is_home is not None else None,
        opponent_adjusted_hit_rate=rate(opponent_rows) if opponent else None,
        line_specific_hit_rate=rate(rows),
        sample_size=len(rows),
    )
