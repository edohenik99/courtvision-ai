"""CourtVision Power Rating — game results store and current-ratings builder.

Reads completed game results from data/history/game_results.csv and derives
current team Power Ratings for use in diagnostics.  No API calls; all data
must already be present in the results file.

The game_results.csv schema:
    date          — YYYY-MM-DD
    home_team_id  — team abbreviation (e.g. OKC)
    away_team_id  — team abbreviation (e.g. LAL)
    home_score    — numeric final score
    away_score    — numeric final score
    game_id       — optional identifier
    home_team_name — optional full name
    away_team_name — optional full name

Abbreviations are used as team IDs throughout so that ratings keys match the
team_abbr column on full_market and elite board DataFrames.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from courtvision.ratings.power_rating import build_team_power_rating_history

GAME_RESULTS_FILENAME = "game_results.csv"
_DEFAULT_GAME_RESULTS_PATH = Path("data/history") / GAME_RESULTS_FILENAME

GAME_RESULTS_COLUMNS: tuple[str, ...] = (
    "date",
    "home_team_id",
    "away_team_id",
    "home_score",
    "away_score",
    "game_id",
    "home_team_name",
    "away_team_name",
)

_REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {"date", "home_team_id", "away_team_id", "home_score", "away_score"}
)


def load_game_results(path: str | Path | None = None) -> pd.DataFrame:
    """Read completed game results from the game results store.

    Returns an empty DataFrame (with GAME_RESULTS_COLUMNS) when the file is
    absent, unreadable, or missing required columns.  Never raises.

    Args:
        path: Path to the CSV file.  Defaults to data/history/game_results.csv.

    Returns:
        DataFrame with at least the five required columns, or empty on failure.
    """
    p = Path(path) if path is not None else _DEFAULT_GAME_RESULTS_PATH
    try:
        if not p.exists():
            return pd.DataFrame(columns=list(GAME_RESULTS_COLUMNS))
        df = pd.read_csv(p, dtype=str)
        if not _REQUIRED_COLUMNS.issubset(set(df.columns)):
            return pd.DataFrame(columns=list(GAME_RESULTS_COLUMNS))
        return df
    except Exception:
        return pd.DataFrame(columns=list(GAME_RESULTS_COLUMNS))


def build_current_power_ratings(games_df: pd.DataFrame) -> dict[str, float]:
    """Build current team Power Ratings from a completed-games DataFrame.

    Processes games chronologically via build_team_power_rating_history and
    returns the most recent rating for each team.

    Args:
        games_df: DataFrame with required columns date, home_team_id,
            away_team_id, home_score, away_score.

    Returns:
        dict[team_id → power_rating] or {} when games_df is empty/invalid.
    """
    if games_df is None or games_df.empty:
        return {}
    history = build_team_power_rating_history(games_df)
    if history.empty:
        return {}
    latest = (
        history
        .sort_values("games_played", ascending=True)
        .drop_duplicates(subset=["team_id"], keep="last")
        .set_index("team_id")["power_rating"]
    )
    return {str(tid): float(r) for tid, r in latest.items()}


def get_latest_team_power_ratings(
    path: str | Path | None = None,
) -> dict[str, float]:
    """Load game results from disk and return current team Power Ratings.

    Combines load_game_results + build_current_power_ratings with a safe
    outer fallback.  Returns {} on any error so callers always get a valid
    dict and can supply safe defaults downstream.

    Args:
        path: Path to the game results CSV.  Defaults to
            data/history/game_results.csv.

    Returns:
        dict[team_id → power_rating], or {} when data are unavailable.
    """
    try:
        games_df = load_game_results(path)
        return build_current_power_ratings(games_df)
    except Exception:
        return {}
