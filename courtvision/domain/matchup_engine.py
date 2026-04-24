from __future__ import annotations

from collections import defaultdict
from statistics import mean

from courtvision.models import PlayerGameStats


def build_team_allowance(stats: list[PlayerGameStats]) -> dict[int, dict[str, float]]:
    # Placeholder allowance model based on team-level player box volume.
    # This is intentionally simple for the MVP rebuild.
    buckets: dict[int, dict[str, list[float]]] = defaultdict(lambda: {
        "points": [],
        "rebounds": [],
        "assists": [],
        "threes": [],
        "steals": [],
        "blocks": [],
    })
    for row in stats:
        buckets[row.team_id]["points"].append(row.points)
        buckets[row.team_id]["rebounds"].append(row.rebounds)
        buckets[row.team_id]["assists"].append(row.assists)
        buckets[row.team_id]["threes"].append(row.threes)
        buckets[row.team_id]["steals"].append(row.steals)
        buckets[row.team_id]["blocks"].append(row.blocks)

    output: dict[int, dict[str, float]] = {}
    for team_id, metrics in buckets.items():
        output[team_id] = {metric: mean(values) if values else 0.0 for metric, values in metrics.items()}
    return output


def compute_matchup_boost(opponent_team_id: int, team_allowance: dict[int, dict[str, float]]) -> tuple[float, list[str]]:
    baseline = team_allowance.get(opponent_team_id, {})
    if not baseline:
        return 0.0, ["no matchup sample available"]

    score = 0.0
    reasons: list[str] = []
    if baseline.get("points", 0.0) >= 16:
        score += 0.03
        reasons.append("opponent allows above-baseline scoring volume")
    if baseline.get("assists", 0.0) >= 4.0:
        score += 0.01
        reasons.append("assist environment is playable")
    if baseline.get("rebounds", 0.0) >= 5.5:
        score += 0.01
        reasons.append("rebound environment is playable")
    return score, reasons
