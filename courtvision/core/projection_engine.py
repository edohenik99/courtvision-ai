"""Small sport-neutral projection contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    sport: str
    market: str
    projection: float
    model_name: str
    data_quality: float
    is_placeholder: bool = False
    context: Mapping[str, object] = field(default_factory=dict)

    def edge_percent(self, line: float) -> float:
        if line == 0:
            return 0.0
        return round(((self.projection - line) / abs(line)) * 100.0, 2)


def weighted_recent_projection(values: list[float]) -> float:
    """NBA-compatible 5/10/season blend used by placeholder sport models."""

    if not values:
        return 0.0

    def average(sample: list[float]) -> float:
        return sum(sample) / len(sample) if sample else 0.0

    recent_first = list(reversed(values))
    projection = (
        0.50 * average(recent_first[:5])
        + 0.35 * average(recent_first[:10])
        + 0.15 * average(recent_first)
    )
    return round(projection, 2)
