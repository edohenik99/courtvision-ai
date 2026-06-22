"""Pitch-type matchup scoring for MLB home run prop research."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from courtvision.sports.mlb.hr_features import clamp_score, normalize_rate


@dataclass(frozen=True, slots=True)
class PitchMatchupScore:
    score: float
    pitch_type_score: float
    pitcher_hr_score: float
    handedness_score: float
    covered_pitch_mix: float


def _rating(value: float | int) -> float:
    rating = float(value)
    if rating > 1.0:
        rating /= 100.0
    return min(max(rating, 0.0), 1.0)


def _handedness_placeholder(handedness: str | None) -> float:
    """Small, explicit placeholder until pitcher-side splits are supplied."""

    text = (handedness or "").strip().lower()
    if any(token in text for token in ("opposite", "platoon advantage", "favorable")):
        return 60.0
    if any(token in text for token in ("same side", "same-side", "unfavorable")):
        return 45.0
    return 50.0


def score_pitch_matchup(
    *,
    pitcher_pitch_mix: Mapping[str, float],
    hitter_vs_pitch_type: Mapping[str, float],
    pitcher_hr_allowed_rate: float,
    handedness: str | None = None,
) -> PitchMatchupScore:
    """Blend pitch-mix coverage, hitter pitch strengths, and HR allowance."""

    normalized_mix = {
        str(pitch).strip().lower(): normalize_rate(share)
        for pitch, share in pitcher_pitch_mix.items()
        if float(share) > 0
    }
    total_mix = sum(normalized_mix.values())
    strengths = {
        str(pitch).strip().lower(): _rating(value)
        for pitch, value in hitter_vs_pitch_type.items()
    }

    if total_mix:
        matched_weight = sum(
            share for pitch, share in normalized_mix.items() if pitch in strengths
        )
        weighted_strength = sum(
            share * strengths.get(pitch, 0.50)
            for pitch, share in normalized_mix.items()
        ) / total_mix
        covered_pitch_mix = matched_weight / total_mix
    else:
        weighted_strength = 0.50
        covered_pitch_mix = 0.0

    pitch_type_score = weighted_strength * 100.0
    hr_rate = normalize_rate(pitcher_hr_allowed_rate)
    pitcher_hr_score = clamp_score(((hr_rate - 0.015) / (0.065 - 0.015)) * 100.0)
    handedness_score = _handedness_placeholder(handedness)
    score = (
        pitch_type_score * 0.65
        + pitcher_hr_score * 0.30
        + handedness_score * 0.05
    )
    return PitchMatchupScore(
        score=round(clamp_score(score), 2),
        pitch_type_score=round(pitch_type_score, 2),
        pitcher_hr_score=round(pitcher_hr_score, 2),
        handedness_score=handedness_score,
        covered_pitch_mix=round(covered_pitch_mix, 3),
    )


def calculate_pitch_matchup_score(**kwargs: object) -> float:
    return score_pitch_matchup(**kwargs).score  # type: ignore[arg-type]


__all__ = [
    "PitchMatchupScore",
    "calculate_pitch_matchup_score",
    "score_pitch_matchup",
]
