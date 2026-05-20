"""Shared runtime ineligibility gates.

These helpers own the row-level game-status and odds-freshness checks used by
elite admission diagnostics. Keep reason strings and default thresholds stable.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Mapping

import pandas as pd

from courtvision.reason_codes import (
    GAME_STATUS_REASON_FINAL,
    GAME_STATUS_REASON_IN_PROGRESS,
    GAME_STATUS_REASON_LOCKED,
    GAME_STATUS_REASON_POSTPONED,
    GAME_STATUS_REASON_UNKNOWN,
    ODDS_STALE_REASON,
    SELECTION_LIVE_GATE_FILTERED_REASON,
    SELECTION_LIVE_GATE_MISSING_QUALIFICATION_REASON,
    SELECTION_NOT_LIVE_MARKET_ELIGIBLE_REASON,
)

IDENTITY_QUARANTINE_REJECTION_REASON = "identity_quarantine"
IDENTITY_OUTSIDE_TEAM_REASON = "outside_team_identity"
IDENTITY_STALE_TEAM_REASON = "stale_team_identity"
IDENTITY_GAME_NOT_BETTABLE_REASON = "game_not_bettable"
IDENTITY_QUARANTINE_REASONS: tuple[str, ...] = (
    IDENTITY_OUTSIDE_TEAM_REASON,
    IDENTITY_STALE_TEAM_REASON,
    IDENTITY_GAME_NOT_BETTABLE_REASON,
)
IDENTITY_SOURCE_TEAM_COLUMNS: tuple[str, ...] = (
    "provider_team_abbr",
    "odds_team_abbr",
    "baseline_team_abbr",
    "resolved_team_abbr",
    "identity_source_team_abbr",
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


def _identity_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>", "nat"} else text


def _identity_row_get(row: Mapping[str, Any] | pd.Series, key: str, default: Any = None) -> Any:
    if isinstance(row, pd.Series):
        return row.get(key, default)
    return row.get(key, default)


def _identity_truthy(value: Any) -> bool:
    return _identity_text(value).lower() in {"true", "1", "yes", "y", "on"}


def _identity_team_abbr(value: Any) -> str:
    return _identity_text(value).upper()


def _identity_first_team(row: Mapping[str, Any] | pd.Series, columns: tuple[str, ...]) -> str:
    for column in columns:
        value = _identity_team_abbr(_identity_row_get(row, column))
        if value:
            return value
    return ""


def _identity_candidate_team(row: Mapping[str, Any] | pd.Series) -> str:
    return _identity_first_team(row, ("team_abbr", "team", "team_abbreviation"))


def _identity_game_teams(row: Mapping[str, Any] | pd.Series) -> tuple[str, str]:
    home = _identity_first_team(
        row,
        (
            "game_home_team_abbr",
            "home_team_abbr",
            "home_team",
            "home",
        ),
    )
    away = _identity_first_team(
        row,
        (
            "game_away_team_abbr",
            "game_visitor_team_abbr",
            "visitor_team_abbr",
            "away_team_abbr",
            "away_team",
            "visitor_team",
            "away",
        ),
    )
    return home, away


def _has_identity_game_context(row: Mapping[str, Any] | pd.Series, home: str, away: str) -> bool:
    return bool(_identity_text(_identity_row_get(row, "game_id")) or (home and away))


def identity_quarantine_reason(row: Mapping[str, Any] | pd.Series) -> str | None:
    """Return a narrow identity-quarantine reason for stale/wrong-team evidence."""
    explicit_reason = _identity_text(_identity_row_get(row, "identity_quarantine_reason")).lower()
    if explicit_reason in IDENTITY_QUARANTINE_REASONS:
        return explicit_reason
    if (
        _identity_text(_identity_row_get(row, "selection_rejection_reason")).lower()
        == IDENTITY_QUARANTINE_REJECTION_REASON
    ):
        return explicit_reason or IDENTITY_GAME_NOT_BETTABLE_REASON
    if _identity_truthy(_identity_row_get(row, "candidate_team_not_in_game")):
        return IDENTITY_OUTSIDE_TEAM_REASON
    if _identity_text(_identity_row_get(row, "context_conflict_cause")).lower() == "stale_team_not_in_game":
        return IDENTITY_OUTSIDE_TEAM_REASON

    candidate = _identity_candidate_team(row)
    home, away = _identity_game_teams(row)
    game_teams = {team for team in (home, away) if team}
    if candidate and game_teams and _has_identity_game_context(row, home, away) and candidate not in game_teams:
        return IDENTITY_OUTSIDE_TEAM_REASON

    if candidate and candidate in game_teams:
        for column in IDENTITY_SOURCE_TEAM_COLUMNS:
            source_team = _identity_team_abbr(_identity_row_get(row, column))
            if source_team and source_team != candidate:
                return IDENTITY_STALE_TEAM_REASON

    return None


def is_identity_quarantined(row: Mapping[str, Any] | pd.Series) -> str | None:
    """Return a narrow identity-quarantine reason, or None when the row passes."""
    return identity_quarantine_reason(row)


def identity_gate_status(row: Mapping[str, Any] | pd.Series) -> dict[str, Any]:
    """Return a compact identity gate status without changing reason strings."""
    reason = identity_quarantine_reason(row)
    return {
        "quarantined": reason is not None,
        "reason": reason or "",
    }


def _operator_row_get(row: Mapping[str, Any] | pd.Series, key: str, default: Any = None) -> Any:
    if isinstance(row, pd.Series):
        return row.get(key, default)
    return row.get(key, default)


def _operator_bool(value: Any) -> bool:
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return bool(value)


def _operator_str(value: Any, default: str = "") -> str:
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    return str(value)


def operator_live_source_gate_status(row: Mapping[str, Any] | pd.Series) -> dict[str, Any]:
    """Return the current operator-board live/source gate status for one row."""
    qualification_reason = _operator_str(_operator_row_get(row, "qualification_reason", ""))
    line_source = _operator_str(_operator_row_get(row, "line_source", ""))
    diagnostic_live = _operator_bool(_operator_row_get(row, "is_live_market", False)) and not _operator_bool(
        _operator_row_get(row, "synthetic_line", False)
    )
    origin_live = (
        "live_market" in qualification_reason.lower()
        or "sportsbook" in qualification_reason.lower()
        or "live_market" in line_source.lower()
    )
    eligible = diagnostic_live and origin_live
    qualification_reason_missing = _operator_str(
        _operator_row_get(row, "qualification_reason", ""),
        default="",
    ).strip() == ""

    rejection_reason = ""
    if not eligible and not diagnostic_live:
        rejection_reason = SELECTION_NOT_LIVE_MARKET_ELIGIBLE_REASON
    elif diagnostic_live and not origin_live and qualification_reason_missing:
        rejection_reason = SELECTION_LIVE_GATE_MISSING_QUALIFICATION_REASON
    elif diagnostic_live and not origin_live:
        rejection_reason = SELECTION_LIVE_GATE_FILTERED_REASON

    return {
        "eligible": eligible,
        "diagnostic_live": diagnostic_live,
        "origin_live": origin_live,
        "qualification_reason_missing": qualification_reason_missing,
        "rejection_reason": rejection_reason,
    }


def is_operator_live_source_eligible(row: Mapping[str, Any] | pd.Series) -> bool:
    """Return True when a row passes the current operator-board live/source gate."""
    return bool(operator_live_source_gate_status(row)["eligible"])


def operator_live_source_rejection_reason(row: Mapping[str, Any] | pd.Series) -> str:
    """Return the current operator-board live/source rejection reason, or ''."""
    return str(operator_live_source_gate_status(row)["rejection_reason"])


def is_unsupported_milestone_market(row: Mapping[str, Any] | pd.Series) -> bool:
    """Return True when current operator-board milestone filtering rejects a row."""
    raw_market_type = _operator_str(_operator_row_get(row, "raw_market_type", "")).strip().lower()
    selection = _operator_str(_operator_row_get(row, "selection", "")).strip().lower()
    return raw_market_type == "milestone" or selection == "milestone"


__all__ = [
    "DEFAULT_GAME_LOCK_BUFFER_MINUTES",
    "DEFAULT_ODDS_STALE_MINUTES",
    "GAME_STATUS_CANCELLED",
    "GAME_STATUS_FINAL",
    "GAME_STATUS_IN_PROGRESS",
    "GAME_STATUS_SCHEDULED",
    "IDENTITY_GAME_NOT_BETTABLE_REASON",
    "IDENTITY_OUTSIDE_TEAM_REASON",
    "IDENTITY_QUARANTINE_REASONS",
    "IDENTITY_QUARANTINE_REJECTION_REASON",
    "IDENTITY_SOURCE_TEAM_COLUMNS",
    "IDENTITY_STALE_TEAM_REASON",
    "_is_before_lock_buffer",
    "_parse_game_datetime",
    "game_status_ineligibility_reason",
    "identity_gate_status",
    "identity_quarantine_reason",
    "is_operator_live_source_eligible",
    "is_unsupported_milestone_market",
    "is_game_bettable",
    "is_identity_quarantined",
    "is_odds_fresh",
    "operator_live_source_gate_status",
    "operator_live_source_rejection_reason",
    "odds_stale_ineligibility_reason",
]
