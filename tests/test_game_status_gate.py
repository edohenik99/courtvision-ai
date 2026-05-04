"""Tests for the game-status / slate-lock gate.

Prevents completed or already-started games from producing actionable picks
in Elite and Kelly boards while keeping them visible in Full Market diagnostics.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from unittest import mock

import pandas as pd
import pytest

from courtvision.runtime_selection import (
    GAME_STATUS_CANCELLED,
    GAME_STATUS_FINAL,
    GAME_STATUS_IN_PROGRESS,
    GAME_STATUS_SCHEDULED,
    DEFAULT_GAME_LOCK_BUFFER_MINUTES,
    game_status_ineligibility_reason,
    is_game_bettable,
)
from courtvision.runtime_audit import (
    ELITE_REJECT_GAME_FINAL,
    ELITE_REJECT_GAME_IN_PROGRESS,
    ELITE_REJECT_GAME_LOCKED,
    ELITE_REJECT_GAME_NOT_BETTABLE,
    ELITE_REJECT_GAME_POSTPONED,
    KELLY_SKIP_GAME_FINAL,
    KELLY_SKIP_GAME_IN_PROGRESS,
    KELLY_SKIP_GAME_LOCKED,
    KELLY_SKIP_GAME_NOT_BETTABLE,
    KELLY_SKIP_GAME_POSTPONED,
    get_elite_rejection_reason,
    projected_kelly_skip_reason,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Recent timestamp for fresh odds in tests
_FRESH_ODDS_TIME = (datetime.now() - timedelta(minutes=5)).isoformat()


def _candidate(
    *,
    game_status: str = "scheduled",
    game_datetime: str | datetime | None = None,
    postseason: str = "false",
    player_name: str = "Test Player",
    market_type: str = "player_points",
    selection: str = "over",
    edge: float = 2.0,
    quality_score: float = 100.0,
    confidence: float = 0.80,
    line: float = 20.5,
    odds_updated_at: str | None = None,
) -> dict[str, object]:
    return {
        "player_name": player_name,
        "market_type": market_type,
        "selection": selection,
        "edge": edge,
        "edge_pct": edge,
        "quality_score": quality_score,
        "confidence": confidence,
        "sportsbook_line": line,
        "line": line,
        "game_status": game_status,
        "game_date": game_datetime or "",
        "game_datetime": game_datetime or "",
        "postseason": postseason,
        # Include fresh odds timestamp so game-status tests don't trigger odds-stale rejection
        "odds_updated_at": odds_updated_at or _FRESH_ODDS_TIME,
    }


# ---------------------------------------------------------------------------
# game_status_ineligibility_reason core tests
# ---------------------------------------------------------------------------

def test_scheduled_game_is_bettable() -> None:
    row = _candidate(game_status="scheduled")
    assert game_status_ineligibility_reason(row) == ""
    assert is_game_bettable(row) is True


def test_pregame_status_is_bettable() -> None:
    row = _candidate(game_status="pregame")
    assert game_status_ineligibility_reason(row) == ""
    assert is_game_bettable(row) is True


def test_final_game_is_not_bettable() -> None:
    row = _candidate(game_status="final")
    assert game_status_ineligibility_reason(row) == "game_final"
    assert is_game_bettable(row) is False


def test_completed_game_is_not_bettable() -> None:
    row = _candidate(game_status="completed")
    assert game_status_ineligibility_reason(row) == "game_final"
    assert is_game_bettable(row) is False


def test_in_progress_game_is_not_bettable() -> None:
    row = _candidate(game_status="in_progress")
    assert game_status_ineligibility_reason(row) == "game_in_progress"
    assert is_game_bettable(row) is False


def test_live_game_is_not_bettable() -> None:
    row = _candidate(game_status="live")
    assert game_status_ineligibility_reason(row) == "game_in_progress"
    assert is_game_bettable(row) is False


def test_halftime_status_is_not_bettable() -> None:
    row = _candidate(game_status="halftime")
    assert game_status_ineligibility_reason(row) == "game_in_progress"
    assert is_game_bettable(row) is False


def test_q1_status_is_not_bettable() -> None:
    row = _candidate(game_status="q1")
    assert game_status_ineligibility_reason(row) == "game_in_progress"
    assert is_game_bettable(row) is False


def test_1st_status_is_not_bettable() -> None:
    row = _candidate(game_status="1st")
    assert game_status_ineligibility_reason(row) == "game_in_progress"
    assert is_game_bettable(row) is False


def test_postponed_game_is_not_bettable() -> None:
    row = _candidate(game_status="postponed")
    assert game_status_ineligibility_reason(row) == "game_postponed"
    assert is_game_bettable(row) is False


def test_cancelled_game_is_not_bettable() -> None:
    row = _candidate(game_status="cancelled")
    assert game_status_ineligibility_reason(row) == "game_postponed"
    assert is_game_bettable(row) is False


def test_unknown_status_with_no_datetime_blocks() -> None:
    # Unknown status + no datetime = cannot determine if bettable -> block
    row = _candidate(game_status="unknown", game_datetime=None)
    assert game_status_ineligibility_reason(row) == "game_status_unknown"
    assert is_game_bettable(row) is False


def test_unknown_status_with_empty_datetime_blocks() -> None:
    row = _candidate(game_status="unknown", game_datetime="")
    assert game_status_ineligibility_reason(row) == "game_status_unknown"
    assert is_game_bettable(row) is False


def test_unknown_status_with_past_datetime_blocks() -> None:
    # Unknown status + past datetime = game already started -> block
    past = datetime.now() - timedelta(hours=2)
    row = _candidate(game_status="unknown", game_datetime=past.isoformat())
    assert game_status_ineligibility_reason(row, now=datetime.now()) == "game_locked"
    assert is_game_bettable(row, now=datetime.now()) is False


def test_unknown_status_with_future_datetime_outside_buffer_is_allowed() -> None:
    # Unknown status + future datetime outside lock buffer = allow
    future = datetime.now() + timedelta(hours=2)
    row = _candidate(game_status="unknown", game_datetime=future.isoformat())
    assert game_status_ineligibility_reason(row, now=datetime.now()) == ""
    assert is_game_bettable(row, now=datetime.now()) is True


def test_unknown_status_with_future_datetime_inside_buffer_blocks() -> None:
    # Unknown status + future datetime inside lock buffer = block
    future = datetime.now() + timedelta(minutes=5)
    row = _candidate(game_status="unknown", game_datetime=future.isoformat())
    assert game_status_ineligibility_reason(row, now=datetime.now()) == "game_locked"
    assert is_game_bettable(row, now=datetime.now()) is False


def test_numeric_status_treated_as_in_progress() -> None:
    row = _candidate(game_status="3")
    assert game_status_ineligibility_reason(row) == "game_in_progress"
    assert is_game_bettable(row) is False


# ---------------------------------------------------------------------------
# Game datetime / lock buffer tests
# ---------------------------------------------------------------------------

def test_game_before_lock_buffer_is_bettable() -> None:
    future = datetime.now() + timedelta(hours=2)
    row = _candidate(game_status="scheduled", game_datetime=future.isoformat())
    assert game_status_ineligibility_reason(row, now=datetime.now()) == ""
    assert is_game_bettable(row, now=datetime.now()) is True


def test_game_inside_lock_buffer_is_locked() -> None:
    future = datetime.now() + timedelta(minutes=5)
    row = _candidate(game_status="scheduled", game_datetime=future.isoformat())
    assert game_status_ineligibility_reason(row, now=datetime.now()) == "game_locked"
    assert is_game_bettable(row, now=datetime.now()) is False


def test_past_game_datetime_is_locked() -> None:
    past = datetime.now() - timedelta(minutes=30)
    row = _candidate(game_status="scheduled", game_datetime=past.isoformat())
    assert game_status_ineligibility_reason(row, now=datetime.now()) == "game_locked"
    assert is_game_bettable(row, now=datetime.now()) is False


def test_custom_lock_buffer() -> None:
    future = datetime.now() + timedelta(minutes=25)
    row = _candidate(game_status="scheduled", game_datetime=future.isoformat())
    # Default 10-min buffer: should be bettable (25 > 10)
    assert is_game_bettable(row, now=datetime.now()) is True
    # With 30-min buffer: should be locked (25 < 30)
    assert is_game_bettable(row, now=datetime.now(), lock_buffer_minutes=30) is False


def test_datetime_parsing_with_z_suffix() -> None:
    utc_str = (datetime.now().replace(tzinfo=None) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = _candidate(game_status="scheduled", game_datetime=utc_str)
    assert game_status_ineligibility_reason(row) == ""


# ---------------------------------------------------------------------------
# COURTVISION_MODE research bypass
# ---------------------------------------------------------------------------

def test_research_mode_bypasses_all_checks() -> None:
    with mock.patch.dict(os.environ, {"COURTVISION_MODE": "research"}):
        for status in ("final", "in_progress", "postponed", "unknown", ""):
            row = _candidate(game_status=status)
            assert game_status_ineligibility_reason(row) == ""
            assert is_game_bettable(row) is True


def test_betting_mode_enforces_checks() -> None:
    with mock.patch.dict(os.environ, {"COURTVISION_MODE": "betting"}):
        row = _candidate(game_status="final")
        assert game_status_ineligibility_reason(row) == "game_final"
        assert is_game_bettable(row) is False


def test_default_mode_is_betting() -> None:
    # No COURTVISION_MODE set -> defaults to betting
    with mock.patch.dict(os.environ, {}, clear=True):
        row = _candidate(game_status="final")
        assert game_status_ineligibility_reason(row) == "game_final"


# ---------------------------------------------------------------------------
# Elite rejection reason integration
# ---------------------------------------------------------------------------

def test_final_game_elite_rejection_reason() -> None:
    row = _candidate(game_status="final")
    reason = get_elite_rejection_reason(row)
    assert reason == ELITE_REJECT_GAME_FINAL


def test_in_progress_game_elite_rejection_reason() -> None:
    row = _candidate(game_status="in_progress")
    reason = get_elite_rejection_reason(row)
    assert reason == ELITE_REJECT_GAME_IN_PROGRESS


def test_postponed_game_elite_rejection_reason() -> None:
    row = _candidate(game_status="postponed")
    reason = get_elite_rejection_reason(row)
    assert reason == ELITE_REJECT_GAME_POSTPONED


def test_locked_game_elite_rejection_reason() -> None:
    past = datetime.now() - timedelta(minutes=30)
    row = _candidate(game_status="scheduled", game_datetime=past.isoformat())
    reason = get_elite_rejection_reason(row, now=datetime.now())
    assert reason == ELITE_REJECT_GAME_LOCKED


def test_unknown_game_status_elite_rejection_reason() -> None:
    row = _candidate(game_status="weird_status")
    reason = get_elite_rejection_reason(row)
    assert reason == ELITE_REJECT_GAME_NOT_BETTABLE


def test_scheduled_game_no_elite_rejection() -> None:
    row = _candidate(game_status="scheduled")
    reason = get_elite_rejection_reason(row)
    assert reason is None  # Passes game-status gate, may fail other gates


# ---------------------------------------------------------------------------
# Kelly skip reason integration
# ---------------------------------------------------------------------------

def test_final_game_kelly_skip_reason() -> None:
    row = _candidate(game_status="final")
    skip = projected_kelly_skip_reason(row)
    assert skip == KELLY_SKIP_GAME_FINAL


def test_in_progress_game_kelly_skip_reason() -> None:
    row = _candidate(game_status="in_progress")
    skip = projected_kelly_skip_reason(row)
    assert skip == KELLY_SKIP_GAME_IN_PROGRESS


def test_postponed_game_kelly_skip_reason() -> None:
    row = _candidate(game_status="postponed")
    skip = projected_kelly_skip_reason(row)
    assert skip == KELLY_SKIP_GAME_POSTPONED


def test_locked_game_kelly_skip_reason() -> None:
    past = datetime.now() - timedelta(minutes=30)
    row = _candidate(game_status="scheduled", game_datetime=past.isoformat())
    skip = projected_kelly_skip_reason(row, now=datetime.now())
    assert skip == KELLY_SKIP_GAME_LOCKED


def test_scheduled_game_no_kelly_skip() -> None:
    row = _candidate(game_status="scheduled")
    skip = projected_kelly_skip_reason(row)
    assert skip == ""  # Not skipped by game-status gate


# ---------------------------------------------------------------------------
# Status set constants tests
# ---------------------------------------------------------------------------

def test_status_set_membership() -> None:
    assert "scheduled" in GAME_STATUS_SCHEDULED
    assert "pregame" in GAME_STATUS_SCHEDULED
    assert "final" in GAME_STATUS_FINAL
    assert "completed" in GAME_STATUS_FINAL
    assert "in_progress" in GAME_STATUS_IN_PROGRESS
    assert "live" in GAME_STATUS_IN_PROGRESS
    assert "postponed" in GAME_STATUS_CANCELLED
    assert "cancelled" in GAME_STATUS_CANCELLED


# ---------------------------------------------------------------------------
# DataFrame-level integration (pipeline-like)
# ---------------------------------------------------------------------------

def test_dataframe_filters_non_bettable_games() -> None:
    future = datetime.now() + timedelta(hours=2)
    rows = [
        _candidate(game_status="scheduled", player_name="Player A"),
        _candidate(game_status="final", player_name="Player B"),
        _candidate(game_status="in_progress", player_name="Player C"),
        _candidate(game_status="postponed", player_name="Player D"),
        _candidate(game_status="unknown", game_datetime=future.isoformat(), player_name="Player E"),
        _candidate(game_status="unknown", game_datetime=None, player_name="Player F"),
    ]
    df = pd.DataFrame(rows)
    bettable_mask = df.apply(lambda r: is_game_bettable(r.to_dict(), now=datetime.now()), axis=1)
    bettable = df[bettable_mask]
    non_bettable = df[~bettable_mask]

    assert len(bettable) == 2
    assert set(bettable["player_name"].tolist()) == {"Player A", "Player E"}
    assert len(non_bettable) == 4


def test_dataframe_rejection_reasons() -> None:
    rows = [
        _candidate(game_status="scheduled", player_name="Player A"),
        _candidate(game_status="final", player_name="Player B"),
        _candidate(game_status="in_progress", player_name="Player C"),
    ]
    df = pd.DataFrame(rows)
    df["elite_rejection_reason"] = df.apply(
        lambda r: get_elite_rejection_reason(r.to_dict()) or "", axis=1
    )
    df["kelly_skip_reason"] = df.apply(
        lambda r: projected_kelly_skip_reason(r.to_dict()), axis=1
    )

    assert df.loc[df["player_name"] == "Player A", "elite_rejection_reason"].iloc[0] == ""
    assert df.loc[df["player_name"] == "Player B", "elite_rejection_reason"].iloc[0] == ELITE_REJECT_GAME_FINAL
    assert df.loc[df["player_name"] == "Player C", "elite_rejection_reason"].iloc[0] == ELITE_REJECT_GAME_IN_PROGRESS

    assert df.loc[df["player_name"] == "Player A", "kelly_skip_reason"].iloc[0] == ""
    assert df.loc[df["player_name"] == "Player B", "kelly_skip_reason"].iloc[0] == KELLY_SKIP_GAME_FINAL
    assert df.loc[df["player_name"] == "Player C", "kelly_skip_reason"].iloc[0] == KELLY_SKIP_GAME_IN_PROGRESS
