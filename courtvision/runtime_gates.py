"""Shared runtime ineligibility gates.

These helpers own the row-level game-status and odds-freshness checks used by
elite admission diagnostics. Keep reason strings and default thresholds stable.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Mapping

from courtvision.reason_codes import (
    GAME_STATUS_REASON_FINAL,
    GAME_STATUS_REASON_IN_PROGRESS,
    GAME_STATUS_REASON_LOCKED,
    GAME_STATUS_REASON_POSTPONED,
    GAME_STATUS_REASON_UNKNOWN,
    ODDS_STALE_REASON,
)

#: Default lock buffer before game start when betting is disabled
DEFAULT_GAME_LOCK_BUFFER_MINUTES: int = 10

#: Game statuses that indicate game is not yet started/bettable
GAME_STATUS_SCHEDULED: set[str] = {
    "scheduled", "pregame", "pre", "not_started", "pending", "upcoming", "open",
    # Note: "unknown" is NOT included - requires datetime validation in betting mode
}

#: Game statuses that indicate game is in progress (not bettable)
GAME_STATUS_IN_PROGRESS: set[str] = {
    "in_progress", "live", "1st", "2nd", "3rd", "4th", "ot", "halftime",
    "q1", "q2", "q3", "q4", "quarter_1", "quarter_2", "quarter_3", "quarter_4",
}

#: Game statuses that indicate game is complete (not bettable)
GAME_STATUS_FINAL: set[str] = {
    "final", "completed", "done", "finished", "ft", "over", "ended"
}

#: Game statuses that indicate game is postponed/cancelled (not bettable)
GAME_STATUS_CANCELLED: set[str] = {
    "postponed", "cancelled", "canceled", "suspended", "delayed", "abandoned"
}


def _parse_game_datetime(dt_raw: Any) -> datetime | None:
    """Parse game datetime from various formats."""
    if dt_raw is None:
        return None
    if isinstance(dt_raw, datetime):
        return dt_raw
    dt_str = str(dt_raw).strip()
    if not dt_str or dt_str.lower() in ("none", "null", "", "na", "n/a"):
        return None
    # Remove 'Z' and handle timezone
    dt_str = dt_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        # Try without timezone
        try:
            return datetime.fromisoformat(dt_str.split("+")[0].split(".")[0])
        except ValueError:
            return None


def _is_before_lock_buffer(
    game_dt: datetime,
    now: Any,
    lock_buffer_minutes: int,
) -> bool:
    """Check if current time is before game start minus lock buffer."""
    if now is None:
        return True  # Assume bettable if no reference time
    if isinstance(now, datetime):
        now_dt = now
    else:
        now_dt = datetime.now(game_dt.tzinfo) if game_dt.tzinfo else datetime.now()
    lock_time = game_dt - timedelta(minutes=lock_buffer_minutes)
    return now_dt < lock_time


def game_status_ineligibility_reason(
    row: Mapping[str, Any],
    now: Any | None = None,
    lock_buffer_minutes: int = DEFAULT_GAME_LOCK_BUFFER_MINUTES,
) -> str:
    """Return the game status ineligibility reason, or '' if bettable.

    A game is actionable only if:
    - status is scheduled/pregame (not started) OR unknown with valid future datetime
    - current time is before scheduled start minus lock buffer
    - game is not Final/completed
    - game is not in progress
    - game is not postponed/cancelled

    Betting mode (COURTVISION_MODE=betting):
    - scheduled/pregame: allowed only if before game start minus lock buffer
    - unknown: allowed only if valid future datetime outside lock buffer
    - unknown + missing datetime: blocked with 'game_status_unknown'
    - unknown + past datetime: blocked with 'game_locked'
    - final/live/postponed: always blocked

    Research mode (COURTVISION_MODE=research):
    - All checks bypassed

    Args:
        row: Candidate row with game status fields
        now: Current datetime (defaults to datetime.now())
        lock_buffer_minutes: Minutes before game start to lock betting

    Returns:
        Empty string if game is bettable, otherwise a reason code:
        - "game_final": Game is complete
        - "game_in_progress": Game is live/in progress
        - "game_postponed": Game is postponed or cancelled
        - "game_locked": Current time is within lock buffer of game start
        - "game_status_unknown": Cannot determine game status
    """
    # Check for COURTVISION_MODE - research mode bypasses game status checks
    mode = os.environ.get("COURTVISION_MODE", "betting").strip().lower()
    if mode == "research":
        return ""

    # Get game status from row
    status = str(row.get("game_status", row.get("status", ""))).strip().lower()

    # Check if game is already complete
    if status in GAME_STATUS_FINAL:
        return GAME_STATUS_REASON_FINAL

    # Check if game is in progress
    if status in GAME_STATUS_IN_PROGRESS:
        return GAME_STATUS_REASON_IN_PROGRESS

    # Check if game is postponed/cancelled
    if status in GAME_STATUS_CANCELLED:
        return GAME_STATUS_REASON_POSTPONED

    # Parse game datetime for lock buffer checks
    game_datetime_raw = row.get("game_datetime") or row.get("game_date") or row.get("datetime")
    game_dt = _parse_game_datetime(game_datetime_raw)

    # Handle "unknown" status (from normalize_games_schema default)
    # In betting mode, unknown requires datetime validation
    if status == "unknown" or not status:
        if game_dt is None:
            # Unknown status and no datetime - cannot determine if bettable
            return GAME_STATUS_REASON_UNKNOWN
        # Have datetime - check if game is in the future outside lock buffer
        if not _is_before_lock_buffer(game_dt, now, lock_buffer_minutes):
            # Game has started or is within lock buffer
            return GAME_STATUS_REASON_LOCKED
        # Game is in the future outside lock buffer - treat as bettable
        return ""

    # For known scheduled/pregame statuses, also check datetime
    if status in GAME_STATUS_SCHEDULED:
        if game_dt is not None:
            # Have datetime - verify game hasn't started
            if not _is_before_lock_buffer(game_dt, now, lock_buffer_minutes):
                return GAME_STATUS_REASON_LOCKED
        # No datetime but status is scheduled - allow (trust the status)
        return ""

    # Truly unrecognized status - be conservative and block
    # Check if it's numeric (quarter/period indicator)
    if status.isdigit() or status in ("q1", "q2", "q3", "q4", "ot", "halftime"):
        return GAME_STATUS_REASON_IN_PROGRESS

    return GAME_STATUS_REASON_UNKNOWN


def is_game_bettable(
    row: Mapping[str, Any],
    now: Any | None = None,
    lock_buffer_minutes: int = DEFAULT_GAME_LOCK_BUFFER_MINUTES,
) -> bool:
    """Return True if the game's candidates can be bet on.

    Convenience wrapper around game_status_ineligibility_reason.
    """
    return game_status_ineligibility_reason(row, now, lock_buffer_minutes) == ""


#: Default max age in minutes for odds to be considered fresh
DEFAULT_ODDS_STALE_MINUTES: int = 30


def odds_stale_ineligibility_reason(
    row: Mapping[str, Any],
    now: Any | None = None,
    stale_threshold_minutes: int = DEFAULT_ODDS_STALE_MINUTES,
) -> str:
    """Return the odds stale ineligibility reason, or '' if odds are fresh.

    Rules:
    - If updated_at is missing or empty, odds are stale (cannot verify freshness)
    - If updated_at is older than stale_threshold_minutes from now, odds are stale
    - If updated_at cannot be parsed, odds are stale (conservative)

    In research mode, always returns "" (all passes).

    Args:
        row: Candidate row with odds_updated_at field
        now: Current datetime (defaults to datetime.now())
        stale_threshold_minutes: Max age of odds in minutes before considered stale

    Returns:
        Empty string if odds are fresh, otherwise "odds_stale".
    """
    # Research mode bypass
    mode = os.environ.get("COURTVISION_MODE", "betting").strip().lower()
    if mode == "research":
        return ""

    updated_at_raw = row.get("odds_updated_at", "")
    if not updated_at_raw:
        return ODDS_STALE_REASON

    updated_at_str = str(updated_at_raw).strip()
    if not updated_at_str:
        return ODDS_STALE_REASON

    # Try to parse the datetime
    updated_at = _parse_game_datetime(updated_at_str)
    if updated_at is None:
        return ODDS_STALE_REASON

    if isinstance(now, datetime):
        now_dt = now
    else:
        now_dt = datetime.now(updated_at.tzinfo) if updated_at.tzinfo else datetime.now()

    age = now_dt - updated_at
    if age > timedelta(minutes=stale_threshold_minutes):
        return ODDS_STALE_REASON

    return ""


def is_odds_fresh(
    row: Mapping[str, Any],
    now: Any | None = None,
    stale_threshold_minutes: int = DEFAULT_ODDS_STALE_MINUTES,
) -> bool:
    """Return True if the odds on this candidate are fresh enough to bet.

    Convenience wrapper around odds_stale_ineligibility_reason.
    """
    return odds_stale_ineligibility_reason(row, now, stale_threshold_minutes) == ""


__all__ = [
    "DEFAULT_GAME_LOCK_BUFFER_MINUTES",
    "DEFAULT_ODDS_STALE_MINUTES",
    "GAME_STATUS_CANCELLED",
    "GAME_STATUS_FINAL",
    "GAME_STATUS_IN_PROGRESS",
    "GAME_STATUS_SCHEDULED",
    "_is_before_lock_buffer",
    "_parse_game_datetime",
    "game_status_ineligibility_reason",
    "is_game_bettable",
    "is_odds_fresh",
    "odds_stale_ineligibility_reason",
]
