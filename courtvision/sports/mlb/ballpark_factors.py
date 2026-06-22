"""Ballpark factor helpers for MLB home run prop research."""

from __future__ import annotations

from courtvision.sports.mlb.hr_features import clamp_score


NEUTRAL_HR_FACTOR = 1.0


def _normalize_park_factor(ballpark_hr_factor: float | None) -> float:
    factor = NEUTRAL_HR_FACTOR if ballpark_hr_factor is None else float(ballpark_hr_factor)
    # Common feeds express park indices as either 1.10 or 110.
    return factor / 100.0 if factor > 10.0 else factor


def score_ballpark_factor(ballpark_hr_factor: float | None) -> float:
    """Map a multiplicative park HR factor to a 0-100 favorability score."""

    factor = _normalize_park_factor(ballpark_hr_factor)
    return round(clamp_score(((factor - 0.75) / 0.50) * 100.0), 2)


def park_factor_reason(ballpark_hr_factor: float | None) -> str | None:
    factor = _normalize_park_factor(ballpark_hr_factor)
    if factor >= 1.08:
        return "Favorable HR park factor"
    if factor <= 0.92:
        return "Suppressive HR park factor"
    return None


__all__ = ["NEUTRAL_HR_FACTOR", "park_factor_reason", "score_ballpark_factor"]
