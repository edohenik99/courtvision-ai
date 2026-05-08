"""CourtVision Power Rating — matchup-level game strength context.

Generates team strength context features for game-level analysis: blowout-risk,
competitiveness, and win-probability signals derived from CourtVision Power Ratings.
Does not generate picks. Used by scoring, dampening, and diagnostics layers.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from courtvision.ratings.power_rating import DEFAULT_RATING, expected_score

DEFAULT_HOME_COURT_ADVANTAGE: float = 50.0

_BLOWOUT_HIGH: float = 120.0
_BLOWOUT_MEDIUM: float = 60.0
_COMP_HIGH: float = 60.0
_COMP_MEDIUM: float = 120.0

POWER_RATING_CONTEXT_COLUMNS: tuple[str, ...] = (
    "home_team_power_rating",
    "away_team_power_rating",
    "power_rating_diff",
    "home_adjusted_power_rating_diff",
    "home_win_probability",
    "away_win_probability",
    "expected_competitiveness",
    "blowout_risk",
    "team_power_context_applied",
    "team_power_context_missing_reason",
)


def _blowout_risk(rating_diff: float) -> str:
    d = abs(rating_diff)
    if d >= _BLOWOUT_HIGH:
        return "HIGH"
    if d >= _BLOWOUT_MEDIUM:
        return "MEDIUM"
    return "LOW"


def _competitiveness(rating_diff: float) -> str:
    d = abs(rating_diff)
    if d < _COMP_HIGH:
        return "HIGH"
    if d < _COMP_MEDIUM:
        return "MEDIUM"
    return "LOW"


def _safe_default(missing_reason: str) -> dict[str, Any]:
    return {
        "home_team_power_rating": DEFAULT_RATING,
        "away_team_power_rating": DEFAULT_RATING,
        "power_rating_diff": 0.0,
        "home_adjusted_power_rating_diff": DEFAULT_HOME_COURT_ADVANTAGE,
        "home_win_probability": 0.5,
        "away_win_probability": 0.5,
        "expected_competitiveness": "UNKNOWN",
        "blowout_risk": "UNKNOWN",
        "team_power_context_applied": False,
        "team_power_context_missing_reason": missing_reason,
    }


def get_matchup_context(
    home_team_id: str,
    away_team_id: str,
    ratings: dict[str, float],
    home_court_advantage: float = DEFAULT_HOME_COURT_ADVANTAGE,
) -> dict[str, Any]:
    """Generate CourtVision Power Rating context for a single matchup.

    Args:
        home_team_id: Identifier for the home team.
        away_team_id: Identifier for the away team.
        ratings: Mapping from team_id to power rating float.
        home_court_advantage: Rating-point bonus added to the home team
            when computing win probability (default 50 ≈ 3.5 pts spread).

    Returns:
        Dict with all POWER_RATING_CONTEXT_COLUMNS fields.
        Returns safe defaults when ratings are missing; never raises.
    """
    if not ratings:
        return _safe_default("team_power_context_missing=no_ratings_provided")

    missing = []
    if home_team_id not in ratings:
        missing.append(f"home_team={home_team_id!r}")
    if away_team_id not in ratings:
        missing.append(f"away_team={away_team_id!r}")
    if missing:
        return _safe_default(
            "team_power_context_missing=rating_not_found; " + "; ".join(missing)
        )

    home_r = float(ratings[home_team_id])
    away_r = float(ratings[away_team_id])

    raw_diff = home_r - away_r
    adj_diff = raw_diff + home_court_advantage

    home_win_prob = round(expected_score(home_r + home_court_advantage, away_r), 6)
    away_win_prob = round(1.0 - home_win_prob, 6)

    return {
        "home_team_power_rating": round(home_r, 4),
        "away_team_power_rating": round(away_r, 4),
        "power_rating_diff": round(raw_diff, 4),
        "home_adjusted_power_rating_diff": round(adj_diff, 4),
        "home_win_probability": home_win_prob,
        "away_win_probability": away_win_prob,
        "expected_competitiveness": _competitiveness(raw_diff),
        "blowout_risk": _blowout_risk(adj_diff),
        "team_power_context_applied": True,
        "team_power_context_missing_reason": "",
    }


def apply_power_rating_context_to_df(
    df: pd.DataFrame,
    ratings: dict[str, float] | None = None,
    home_court_advantage: float = DEFAULT_HOME_COURT_ADVANTAGE,
    team_col: str = "team_abbr",
    opponent_col: str = "opponent",
    home_away_col: str = "home_away",
) -> pd.DataFrame:
    """Attach POWER_RATING_CONTEXT_COLUMNS to a board DataFrame in-place.

    Derives home/away team IDs from (team_col, opponent_col, home_away_col),
    computes matchup context once per unique game, and broadcasts to all rows
    of that game.  Safe defaults fill every column when ratings are empty or
    the required columns are absent.  Never raises.

    Args:
        df: Board DataFrame to enrich.
        ratings: team_id → power rating. Pass None or {} for all safe defaults.
        home_court_advantage: Rating-point bonus for the home team.
        team_col: Column name for the focal team's abbreviation.
        opponent_col: Column name for the opponent's abbreviation.
        home_away_col: Column name for the "home"/"away" indicator.

    Returns:
        df with all POWER_RATING_CONTEXT_COLUMNS added.
    """
    if ratings is None:
        ratings = {}

    _safe = _safe_default("team_power_context_missing=no_ratings_provided")
    for col in POWER_RATING_CONTEXT_COLUMNS:
        df[col] = _safe[col]

    if df.empty or not {team_col, opponent_col, home_away_col}.issubset(df.columns):
        return df

    team_s = df[team_col].fillna("").astype(str).str.strip()
    opp_s = df[opponent_col].fillna("").astype(str).str.strip()
    ha_s = df[home_away_col].fillna("").astype(str).str.strip().str.lower()

    is_home = ha_s == "home"
    is_away = ha_s == "away"
    valid = is_home | is_away

    home_ids = team_s.where(is_home, opp_s).where(valid, "")
    away_ids = opp_s.where(is_home, team_s).where(valid, "")

    game_keys: list[tuple[str, str]] = list(zip(home_ids, away_ids))
    unique_keys: set[tuple[str, str]] = set(game_keys)

    game_contexts: dict[tuple[str, str], dict[str, Any]] = {}
    for h, a in unique_keys:
        if h and a:
            game_contexts[(h, a)] = get_matchup_context(h, a, ratings, home_court_advantage)
        else:
            game_contexts[(h, a)] = _safe_default(
                "team_power_context_missing=missing_team_id_or_home_away"
            )

    for col in POWER_RATING_CONTEXT_COLUMNS:
        df[col] = [game_contexts[k][col] for k in game_keys]

    return df


def get_matchup_context_batch(
    matchups: list[dict[str, str]],
    ratings: dict[str, float],
    home_court_advantage: float = DEFAULT_HOME_COURT_ADVANTAGE,
) -> list[dict[str, Any]]:
    """Generate context for a list of matchups.

    Each matchup dict must contain home_team_id and away_team_id keys.
    All other keys in the matchup dict are preserved in the output.

    Args:
        matchups: List of matchup dicts.
        ratings: Mapping from team_id to power rating.
        home_court_advantage: Rating-point bonus for home team.

    Returns:
        List of dicts — original matchup keys merged with context fields.
    """
    results = []
    for m in matchups:
        home_id = str(m.get("home_team_id") or "").strip()
        away_id = str(m.get("away_team_id") or "").strip()
        if not home_id or not away_id:
            ctx = _safe_default("team_power_context_missing=missing_team_id_in_matchup")
        else:
            ctx = get_matchup_context(home_id, away_id, ratings, home_court_advantage)
        results.append({**m, **ctx})
    return results
