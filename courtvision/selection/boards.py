from __future__ import annotations

from collections import defaultdict

from courtvision.models import Projection


def build_full_board(projections: list[Projection]) -> list[Projection]:
    ordered = sorted(
        projections,
        key=lambda item: (item.confidence_tier == "Strong", item.exposure_score, item.expected_minutes, item.points + item.rebounds + item.assists),
        reverse=True,
    )
    limited: list[Projection] = []
    per_team = defaultdict(int)
    for projection in ordered:
        if projection.confidence_tier == "Avoid":
            continue
        if per_team[projection.team_abbreviation] >= 5:
            continue
        limited.append(projection)
        per_team[projection.team_abbreviation] += 1
    return limited


def build_elite_board(projections: list[Projection], limit: int = 20) -> list[Projection]:
    ordered = sorted(
        [item for item in projections if item.confidence_tier == "Strong" and item.exposure_score >= 0.72],
        key=lambda item: (item.exposure_score, item.expected_minutes, item.points + item.assists + item.rebounds),
        reverse=True,
    )
    return ordered[:limit]
