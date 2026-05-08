"""CourtVision Power Rating — power-rating-based team strength ratings.

Produces team strength signals used as context by game-level scoring,
dampening, blowout-risk detection, and competitiveness analysis.
Does not generate betting picks.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

DEFAULT_RATING: float = 1500.0
RATING_SCALE: float = 400.0


def expected_score(rating_a: float, rating_b: float) -> float:
    """Return expected win probability for team A.

    Uses the CourtVision Power Rating win probability formula. Equal ratings return 0.50.

    Args:
        rating_a: Power rating for team A.
        rating_b: Power rating for team B.

    Returns:
        Win probability for team A in (0.0, 1.0).
    """
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / RATING_SCALE))


def update_power_rating(
    team_rating: float,
    opponent_rating: float,
    team_score: float,
    opponent_score: float,
    k: float = 20.0,
    margin_scaling: bool = True,
) -> float:
    """Return updated power rating after a game result.

    Args:
        team_rating: Current rating for the team.
        opponent_rating: Current rating for the opponent.
        team_score: Points scored by the team.
        opponent_score: Points scored by the opponent.
        k: K-factor — controls maximum rating movement per game.
        margin_scaling: If True, scale K by log(margin + 1) so large wins
            move ratings more than narrow wins.

    Returns:
        Updated rating for the team, rounded to 4 decimal places.
    """
    exp = expected_score(team_rating, opponent_rating)

    if team_score > opponent_score:
        actual = 1.0
    elif team_score < opponent_score:
        actual = 0.0
    else:
        actual = 0.5

    effective_k = k
    if margin_scaling:
        margin = abs(team_score - opponent_score)
        effective_k = k * math.log(margin + 1.0)

    return round(team_rating + effective_k * (actual - exp), 4)


def regress_to_mean(
    rating: float,
    mean: float = 1500.0,
    regression_factor: float = 0.25,
) -> float:
    """Move rating toward the league-average baseline.

    Used for offseason or new-season normalization.

    Args:
        rating: Current power rating.
        mean: League average target (default 1500).
        regression_factor: Fraction of the gap to close; 0.25 closes 25%.

    Returns:
        Regressed rating, rounded to 4 decimal places.
    """
    return round(rating + regression_factor * (mean - rating), 4)


def build_team_power_rating_history(games_df: pd.DataFrame) -> pd.DataFrame:
    """Process game results chronologically to build per-team rating history.

    Required input columns: date, home_team_id, away_team_id,
    home_score, away_score.
    Optional: home_team_name, away_team_name, game_id.

    Args:
        games_df: DataFrame with one row per game.

    Returns:
        DataFrame with columns: date, team_id, team_name, power_rating,
        games_played, last_game_id, power_rating_change.
        Returns an empty DataFrame on missing/invalid input.
    """
    _OUTPUT_COLUMNS = [
        "date",
        "team_id",
        "team_name",
        "power_rating",
        "games_played",
        "last_game_id",
        "power_rating_change",
    ]
    empty = pd.DataFrame(columns=_OUTPUT_COLUMNS)

    if games_df is None or games_df.empty:
        return empty

    required = {"date", "home_team_id", "away_team_id", "home_score", "away_score"}
    if required - set(games_df.columns):
        return empty

    has_names = (
        "home_team_name" in games_df.columns
        and "away_team_name" in games_df.columns
    )
    has_game_id = "game_id" in games_df.columns

    df = games_df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    ratings: dict[str, float] = {}
    games_played: dict[str, int] = {}
    team_names: dict[str, str] = {}
    history: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        home_id = str(row.get("home_team_id") or "").strip()
        away_id = str(row.get("away_team_id") or "").strip()
        if not home_id or not away_id:
            continue

        try:
            home_score = float(row["home_score"])
            away_score = float(row["away_score"])
        except (TypeError, ValueError):
            continue
        if math.isnan(home_score) or math.isnan(away_score):
            continue

        if home_id not in ratings:
            ratings[home_id] = DEFAULT_RATING
            games_played[home_id] = 0
        if away_id not in ratings:
            ratings[away_id] = DEFAULT_RATING
            games_played[away_id] = 0

        if has_names:
            hn = str(row.get("home_team_name") or "").strip()
            an = str(row.get("away_team_name") or "").strip()
            if hn:
                team_names[home_id] = hn
            if an:
                team_names[away_id] = an

        game_id = str(row.get("game_id") or "").strip() if has_game_id else ""

        prior_home = ratings[home_id]
        prior_away = ratings[away_id]

        new_home = update_power_rating(prior_home, prior_away, home_score, away_score)
        new_away = update_power_rating(prior_away, prior_home, away_score, home_score)

        ratings[home_id] = new_home
        ratings[away_id] = new_away
        games_played[home_id] += 1
        games_played[away_id] += 1

        game_date = row["date"].date() if hasattr(row["date"], "date") else row["date"]

        history.append({
            "date": game_date,
            "team_id": home_id,
            "team_name": team_names.get(home_id, ""),
            "power_rating": new_home,
            "games_played": games_played[home_id],
            "last_game_id": game_id,
            "power_rating_change": round(new_home - prior_home, 4),
        })
        history.append({
            "date": game_date,
            "team_id": away_id,
            "team_name": team_names.get(away_id, ""),
            "power_rating": new_away,
            "games_played": games_played[away_id],
            "last_game_id": game_id,
            "power_rating_change": round(new_away - prior_away, 4),
        })

    if not history:
        return empty

    return pd.DataFrame(history, columns=_OUTPUT_COLUMNS)
