from __future__ import annotations

from statistics import mean

from courtvision.models import PlayerGameStats, Projection


STAT_FIELDS = ["points", "rebounds", "assists", "threes", "steals", "blocks"]


def weighted_recent_average(rows: list[PlayerGameStats], field_name: str) -> float:
    recent5 = rows[:5]
    recent10 = rows[:10]
    all_games = rows

    def safe_mean(items: list[PlayerGameStats]) -> float:
        if not items:
            return 0.0
        return mean(float(getattr(item, field_name, 0.0) or 0.0) for item in items)

    return round((0.50 * safe_mean(recent5)) + (0.35 * safe_mean(recent10)) + (0.15 * safe_mean(all_games)), 2)


def project_from_context(
    *,
    player_id: int,
    player_name: str,
    team_abbreviation: str,
    opponent_abbreviation: str,
    rows: list[PlayerGameStats],
    expected_minutes: float,
    usage_boost: float,
    injury_boost: float,
    matchup_boost: float,
    exposure_score: float,
    confidence_tier: str,
    reasons: list[str],
) -> Projection:
    minute_factor = expected_minutes / max(_mean_minutes(rows), 1.0)
    multiplier = (1.0 + usage_boost + matchup_boost) * minute_factor

    projected: dict[str, float] = {}
    for field_name in STAT_FIELDS:
        base = weighted_recent_average(rows, field_name)
        projected[field_name] = round(max(0.0, base * multiplier), 2)

    return Projection(
        player_id=player_id,
        player_name=player_name,
        team_abbreviation=team_abbreviation,
        opponent_abbreviation=opponent_abbreviation,
        points=projected["points"],
        rebounds=projected["rebounds"],
        assists=projected["assists"],
        threes=projected["threes"],
        steals=projected["steals"],
        blocks=projected["blocks"],
        expected_minutes=round(expected_minutes, 2),
        usage_boost=round(usage_boost, 3),
        injury_boost=round(injury_boost, 3),
        matchup_boost=round(matchup_boost, 3),
        exposure_score=round(exposure_score, 3),
        confidence_tier=confidence_tier,
        reasons=reasons,
    )


def _mean_minutes(rows: list[PlayerGameStats]) -> float:
    non_zero = [row.minutes for row in rows[:10] if row.minutes > 0]
    if not non_zero:
        return 1.0
    return mean(non_zero)
