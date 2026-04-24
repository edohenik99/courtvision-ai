from __future__ import annotations

from collections import defaultdict

from courtvision.models import Injury

OUT_STATUSES = {"out", "doubtful", "inactive", "day-to-day"}


def summarize_injuries(injuries: list[Injury]) -> dict[int, dict]:
    summary: dict[int, dict] = defaultdict(lambda: {"count": 0, "names": [], "descriptions": []})
    for injury in injuries:
        status = injury.status.strip().lower()
        if status not in OUT_STATUSES:
            continue
        summary[injury.team_id]["count"] += 1
        summary[injury.team_id]["names"].append(injury.player_name.strip())
        if injury.description:
            summary[injury.team_id]["descriptions"].append(injury.description.strip())
    return dict(summary)


def compute_injury_boost(team_injury_count: int, recent_minutes: float) -> tuple[float, float, list[str]]:
    usage_boost = min(0.12, team_injury_count * 0.02)
    minute_boost = min(3.0, team_injury_count * 0.5)
    reasons: list[str] = []
    if team_injury_count > 0:
        reasons.append(f"{team_injury_count} key teammate injuries on team context")
    if recent_minutes >= 30:
        usage_boost += 0.02
        reasons.append("already carrying stable starter minutes")
    return usage_boost, minute_boost, reasons
