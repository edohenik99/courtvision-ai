from __future__ import annotations

from statistics import mean, pstdev

from courtvision.models import PlayerGameStats


def compute_exposure_score(rows: list[PlayerGameStats]) -> tuple[float, str, list[str]]:
    recent = rows[:10]
    minutes = [row.minutes for row in recent if row.minutes > 0]
    if not minutes:
        return 0.2, "Avoid", ["no valid minutes sample"]

    minute_mean = mean(minutes)
    minute_std = pstdev(minutes) if len(minutes) > 1 else 0.0
    stability = max(0.0, min(1.0, 1.0 - (minute_std / max(minute_mean, 1.0))))
    workload = max(0.0, min(1.0, minute_mean / 36.0))
    exposure = round((0.65 * stability) + (0.35 * workload), 3)

    reasons: list[str] = [f"minute stability={stability:.2f}", f"workload={workload:.2f}"]
    if exposure >= 0.78:
        return exposure, "Strong", reasons
    if exposure >= 0.58:
        return exposure, "Medium", reasons
    return exposure, "Avoid", reasons
