"""Weather and combined environment scoring for MLB HR props."""

from __future__ import annotations

from dataclasses import dataclass

from courtvision.sports.mlb.ballpark_factors import score_ballpark_factor
from courtvision.sports.mlb.hr_features import clamp_score


@dataclass(frozen=True, slots=True)
class EnvironmentScore:
    score: float
    park_score: float
    wind_score: float
    temperature_score: float
    wind_effect: str


def classify_wind_direction(wind_direction: str | None) -> str:
    text = (wind_direction or "").strip().lower()
    if any(token in text for token in ("blowing out", "out to", "toward outfield", "to center", "to left", "to right")):
        return "out"
    if any(token in text for token in ("blowing in", "in from", "toward home", "from center", "from left", "from right")):
        return "in"
    return "neutral"


def score_wind(wind_direction: str | None, wind_speed: float | None) -> float:
    speed = min(max(float(wind_speed or 0.0), 0.0), 20.0)
    effect = classify_wind_direction(wind_direction)
    if effect == "out":
        return round(50.0 + (speed / 20.0) * 50.0, 2)
    if effect == "in":
        return round(50.0 - (speed / 20.0) * 50.0, 2)
    return 50.0


def score_temperature(temperature: float | None) -> float:
    if temperature is None:
        return 50.0
    return round(clamp_score(((float(temperature) - 50.0) / 40.0) * 100.0), 2)


def score_environment(
    *,
    ballpark_hr_factor: float | None,
    wind_direction: str | None,
    wind_speed: float | None,
    temperature: float | None,
) -> EnvironmentScore:
    """Blend park (55%), wind (30%), and temperature (15%)."""

    park_score = score_ballpark_factor(ballpark_hr_factor)
    wind_score = score_wind(wind_direction, wind_speed)
    temperature_score = score_temperature(temperature)
    score = park_score * 0.55 + wind_score * 0.30 + temperature_score * 0.15
    return EnvironmentScore(
        score=round(clamp_score(score), 2),
        park_score=park_score,
        wind_score=wind_score,
        temperature_score=temperature_score,
        wind_effect=classify_wind_direction(wind_direction),
    )


def calculate_environment_score(**kwargs: object) -> float:
    return score_environment(**kwargs).score  # type: ignore[arg-type]


__all__ = [
    "EnvironmentScore",
    "calculate_environment_score",
    "classify_wind_direction",
    "score_environment",
    "score_temperature",
    "score_wind",
]
