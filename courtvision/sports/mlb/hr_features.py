"""Power-form feature scoring for MLB home run prop research."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


def clamp_score(value: float) -> float:
    """Clamp a numeric score to CourtVision's 0-100 scale."""

    return min(max(float(value), 0.0), 100.0)


def normalize_rate(value: float | int | None) -> float:
    """Accept decimal or percentage-form rates and return a 0-1 value."""

    if value is None:
        return 0.0
    rate = float(value)
    if rate > 1.0:
        rate /= 100.0
    return min(max(rate, 0.0), 1.0)


def _scaled(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return clamp_score(((value - low) / (high - low)) * 100.0)


def recent_barrel_count(events: Sequence[object] | int | float | None) -> int:
    """Count explicitly marked barrels in a recent batted-ball sample.

    Feed adapters may provide an already-computed count, mappings with a
    ``barrel``/``is_barrel`` flag, or objects exposing either attribute.
    """

    if events is None:
        return 0
    if isinstance(events, (int, float)):
        return max(int(events), 0)

    count = 0
    for event in events:
        if isinstance(event, str):
            is_barrel = event.strip().lower() == "barrel"
        elif isinstance(event, Mapping):
            is_barrel = bool(event.get("is_barrel", event.get("barrel", False)))
        else:
            is_barrel = bool(
                getattr(event, "is_barrel", getattr(event, "barrel", False))
            )
        count += int(is_barrel)
    return count


@dataclass(frozen=True, slots=True)
class PowerFormScore:
    score: float
    recent_barrels: int
    components: dict[str, float]


def score_power_form(
    *,
    recent_plate_appearances: int,
    recent_batted_ball_events: Sequence[object] | int | float | None,
    hard_hit_rate: float,
    barrel_rate: float,
    pull_rate: float,
    pull_barrel_rate: float,
    fly_ball_rate: float,
    max_exit_velocity: float,
    recent_home_runs: int,
) -> PowerFormScore:
    """Score current hitter power indicators from 0 to 100."""

    plate_appearances = max(int(recent_plate_appearances), 1)
    barrel_count = recent_barrel_count(recent_batted_ball_events)
    if isinstance(recent_batted_ball_events, Sequence) and not isinstance(
        recent_batted_ball_events, (str, bytes)
    ):
        batted_ball_count = max(len(recent_batted_ball_events), 1)
    else:
        # A count-only feed has no denominator; plate appearances are the most
        # conservative available proxy for the recent opportunity sample.
        batted_ball_count = plate_appearances

    raw_components = {
        "recent_home_runs": clamp_score((max(recent_home_runs, 0) / plate_appearances) / 0.10 * 100.0),
        "recent_barrels": clamp_score((barrel_count / batted_ball_count) / 0.20 * 100.0),
        "hard_hit_rate": clamp_score(normalize_rate(hard_hit_rate) / 0.55 * 100.0),
        "barrel_rate": clamp_score(normalize_rate(barrel_rate) / 0.20 * 100.0),
        "fly_ball_rate": clamp_score(normalize_rate(fly_ball_rate) / 0.50 * 100.0),
        "pull_rate": clamp_score(normalize_rate(pull_rate) / 0.50 * 100.0),
        "pull_barrel_rate": clamp_score(normalize_rate(pull_barrel_rate) / 0.12 * 100.0),
        "max_exit_velocity": _scaled(float(max_exit_velocity), 95.0, 115.0),
    }
    weights = {
        "recent_home_runs": 0.20,
        "recent_barrels": 0.15,
        "hard_hit_rate": 0.15,
        "barrel_rate": 0.15,
        "fly_ball_rate": 0.10,
        "pull_rate": 0.08,
        "pull_barrel_rate": 0.10,
        "max_exit_velocity": 0.07,
    }
    score = sum(raw_components[name] * weight for name, weight in weights.items())
    return PowerFormScore(
        score=round(clamp_score(score), 2),
        recent_barrels=barrel_count,
        components={name: round(value, 2) for name, value in raw_components.items()},
    )


def calculate_power_form_score(**kwargs: object) -> float:
    """Convenience facade returning only the numeric power score."""

    return score_power_form(**kwargs).score  # type: ignore[arg-type]


__all__ = [
    "PowerFormScore",
    "calculate_power_form_score",
    "clamp_score",
    "normalize_rate",
    "recent_barrel_count",
    "score_power_form",
]
