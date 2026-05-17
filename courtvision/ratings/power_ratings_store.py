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

from datetime import date as _date_type
from pathlib import Path
from typing import Any

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

_FINAL_STATUSES: frozenset[str] = frozenset(
    {"final", "final/ot", "final ot", "complete", "completed", "closed"}
)


def _is_missing_value(val: Any) -> bool:
    if val is None:
        return True
    try:
        return bool(pd.isna(val))
    except (TypeError, ValueError):
        return False


def _safe_float(val: Any, default: float = 0.0) -> float:
    if _is_missing_value(val):
        return default
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    if _is_missing_value(f):
        return default
    return f


def _safe_str(val: Any) -> str:
    if _is_missing_value(val):
        return ""
    return str(val).strip()


def _score_value(val: Any) -> float | None:
    if _is_missing_value(val):
        return None
    try:
        score = float(val)
    except (TypeError, ValueError):
        return None
    if _is_missing_value(score) or score <= 0:
        return None
    return score


def _normalized_status(val: Any) -> str:
    return " ".join(_safe_str(val).lower().split())


def _extract_team_abbr(game_row: Any, side: str) -> str:
    """Extract uppercase team abbreviation for `side` ('home' or 'visitor').

    Handles BallDontLie rows where home_team / visitor_team may be a nested
    dict with an 'abbreviation' key, or flat columns like home_team_abbr.
    """
    nested_key = "home_team" if side == "home" else "visitor_team"
    abbr_col = f"{side}_team_abbr" if side == "home" else "visitor_team_abbr"

    nested = None
    if hasattr(game_row, "get"):
        nested = game_row.get(nested_key)
    elif hasattr(game_row, nested_key):
        nested = getattr(game_row, nested_key)

    if isinstance(nested, dict):
        abbr = _safe_str(nested.get("abbreviation") or nested.get("abbr") or "")
    else:
        abbr = ""

    if not abbr:
        for col in (abbr_col, nested_key):
            if hasattr(game_row, "get"):
                val = game_row.get(col, "")
            else:
                val = getattr(game_row, col, "")
            text = _safe_str(val)
            if text and not isinstance(val, dict):
                abbr = text
                break

    return _safe_str(abbr).upper()


def _is_final_game(game_row: Any) -> bool:
    """Return True if game_row represents a completed game with valid scores."""
    if hasattr(game_row, "get"):
        status = _normalized_status(game_row.get("status", ""))
        home_score = game_row.get("home_team_score", None)
        away_score = game_row.get("visitor_team_score", None)
    else:
        status = _normalized_status(getattr(game_row, "status", ""))
        home_score = getattr(game_row, "home_team_score", None)
        away_score = getattr(game_row, "visitor_team_score", None)

    if status not in _FINAL_STATUSES:
        return False

    if not _extract_team_abbr(game_row, "home") or not _extract_team_abbr(game_row, "visitor"):
        return False

    home_score_value = _score_value(home_score)
    away_score_value = _score_value(away_score)

    return (
        home_score_value is not None
        and away_score_value is not None
        and home_score_value > 0
        and away_score_value > 0
    )


def games_df_to_results(
    raw_games_df: pd.DataFrame,
    fallback_date: str = "",
) -> pd.DataFrame:
    """Convert a raw BallDontLie games DataFrame to GAME_RESULTS_COLUMNS format.

    Extracts only final/completed games with valid home and away teams and
    scores.  Rows with missing abbreviations or unparseable scores are silently
    skipped.  Never raises.

    Args:
        raw_games_df: DataFrame returned by BallDontLieClient.get_games().
        fallback_date: YYYY-MM-DD string used when the row's 'date' field is
            absent or empty.

    Returns:
        DataFrame with GAME_RESULTS_COLUMNS columns, possibly empty.
    """
    if raw_games_df is None or (hasattr(raw_games_df, "empty") and raw_games_df.empty):
        return pd.DataFrame(columns=list(GAME_RESULTS_COLUMNS))

    rows: list[dict[str, str]] = []
    try:
        for _, row in raw_games_df.iterrows():
            if not _is_final_game(row):
                continue

            home_abbr = _extract_team_abbr(row, "home")
            away_abbr = _extract_team_abbr(row, "visitor")
            if not home_abbr or not away_abbr:
                continue

            if hasattr(row, "get"):
                raw_home_score = row.get("home_team_score", "")
                raw_away_score = row.get("visitor_team_score", "")
                raw_date = row.get("date", fallback_date) or fallback_date
                raw_game_id = row.get("id", "") or ""
                raw_home_name = ""
                raw_away_name = ""
                home_team = row.get("home_team")
                vis_team = row.get("visitor_team")
                if isinstance(home_team, dict):
                    raw_home_name = home_team.get("full_name", "") or ""
                if isinstance(vis_team, dict):
                    raw_away_name = vis_team.get("full_name", "") or ""
            else:
                raw_home_score = getattr(row, "home_team_score", "")
                raw_away_score = getattr(row, "visitor_team_score", "")
                raw_date = getattr(row, "date", fallback_date) or fallback_date
                raw_game_id = getattr(row, "id", "") or ""
                raw_home_name = ""
                raw_away_name = ""

            home_score = _safe_float(raw_home_score)
            away_score = _safe_float(raw_away_score)
            if home_score == 0.0 and away_score == 0.0:
                continue

            date_str = _safe_str(raw_date)[:10]

            rows.append({
                "date": date_str,
                "home_team_id": home_abbr,
                "away_team_id": away_abbr,
                "home_score": str(int(home_score)),
                "away_score": str(int(away_score)),
                "game_id": _safe_str(raw_game_id),
                "home_team_name": _safe_str(raw_home_name),
                "away_team_name": _safe_str(raw_away_name),
            })
    except Exception:
        pass

    return pd.DataFrame(rows, columns=list(GAME_RESULTS_COLUMNS)) if rows else pd.DataFrame(columns=list(GAME_RESULTS_COLUMNS))


def append_game_results(
    new_df: pd.DataFrame,
    path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Append new game results to the game results CSV, deduplicating in place.

    Loads the existing CSV (creating it if absent), concatenates new_df,
    deduplicates (by game_id when non-empty, else by date+home+away), sorts
    chronologically, and writes back unless dry_run=True.  Never raises.

    Args:
        new_df: DataFrame in GAME_RESULTS_COLUMNS format (from games_df_to_results).
        path: Path to the CSV file.  Defaults to data/history/game_results.csv.

    Returns:
        dict with keys: fetched, accepted, appended, skipped_duplicates,
        total_rows, output_path.
    """
    p = Path(path) if path is not None else _DEFAULT_GAME_RESULTS_PATH
    fetched = len(new_df) if new_df is not None else 0

    try:
        existing = load_game_results(p)
        existing_rows = len(existing)

        if new_df is None or new_df.empty:
            return {
                "fetched": fetched,
                "accepted": 0,
                "appended": 0,
                "skipped_duplicates": 0,
                "total_rows": existing_rows,
                "output_path": str(p),
                "dry_run": bool(dry_run),
            }

        combined = pd.concat([existing, new_df], ignore_index=True)

        if "game_id" in combined.columns:
            combined["game_id"] = combined["game_id"].fillna("").astype(str).str.strip()

        has_game_id = (
            "game_id" in combined.columns
            and (combined["game_id"] != "").any()
        )

        if has_game_id:
            with_id = combined[combined["game_id"] != ""]
            without_id = combined[combined["game_id"] == ""]
            deduped_with = with_id.drop_duplicates(subset=["game_id"], keep="first")
            deduped_without = without_id.drop_duplicates(
                subset=["date", "home_team_id", "away_team_id"], keep="first"
            )
            deduped = pd.concat([deduped_with, deduped_without], ignore_index=True)
        else:
            deduped = combined.drop_duplicates(
                subset=["date", "home_team_id", "away_team_id"], keep="first"
            )

        deduped = deduped.sort_values("date", kind="stable").reset_index(drop=True)

        total_rows = len(deduped)
        appended = total_rows - existing_rows
        skipped = fetched - max(appended, 0)

        if not dry_run:
            p.parent.mkdir(parents=True, exist_ok=True)
            deduped.to_csv(p, index=False)

        return {
            "fetched": fetched,
            "accepted": fetched,
            "appended": max(appended, 0),
            "skipped_duplicates": max(skipped, 0),
            "total_rows": total_rows,
            "output_path": str(p),
            "dry_run": bool(dry_run),
        }
    except Exception:
        return {
            "fetched": fetched,
            "accepted": 0,
            "appended": 0,
            "skipped_duplicates": fetched,
            "total_rows": 0,
            "output_path": str(p),
            "dry_run": bool(dry_run),
        }


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


def build_current_power_ratings(
    games_df: pd.DataFrame,
    as_of_date: str | _date_type | None = None,
) -> dict[str, float]:
    """Build current team Power Ratings from a completed-games DataFrame.

    Processes games chronologically via build_team_power_rating_history and
    returns the most recent rating for each team.

    Args:
        games_df: DataFrame with required columns date, home_team_id,
            away_team_id, home_score, away_score.
        as_of_date: When provided, only games with date < as_of_date are used.
            Accepts YYYY-MM-DD strings or datetime.date objects.  Keeps
            pre-game-date ratings safe for pregame predictions.

    Returns:
        dict[team_id → power_rating] or {} when games_df is empty/invalid.
    """
    if games_df is None or games_df.empty:
        return {}
    if as_of_date is not None:
        as_of_str = str(as_of_date)[:10]
        games_df = games_df[games_df["date"].astype(str).str[:10] < as_of_str]
        if games_df.empty:
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
    as_of_date: str | _date_type | None = None,
) -> dict[str, float]:
    """Load game results from disk and return current team Power Ratings.

    Combines load_game_results + build_current_power_ratings with a safe
    outer fallback.  Returns {} on any error so callers always get a valid
    dict and can supply safe defaults downstream.

    Args:
        path: Path to the game results CSV.  Defaults to
            data/history/game_results.csv.
        as_of_date: When provided, only games with date < as_of_date are used.
            Pass the prediction date to get ratings that were available
            before that day's games were played.

    Returns:
        dict[team_id → power_rating], or {} when data are unavailable.
    """
    try:
        games_df = load_game_results(path)
        return build_current_power_ratings(games_df, as_of_date=as_of_date)
    except Exception:
        return {}
