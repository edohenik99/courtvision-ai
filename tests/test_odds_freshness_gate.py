"""Tests for the odds freshness gate.

Prevents stale or unverifiable odds from producing actionable picks in Elite
and Kelly boards while keeping them visible in Full Market diagnostics.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest import mock

import pandas as pd
import pytest

from courtvision.runtime_selection import (
    DEFAULT_ODDS_STALE_MINUTES,
    is_odds_fresh,
    odds_stale_ineligibility_reason,
)
from courtvision.runtime_audit import (
    ELITE_REJECT_ODDS_STALE,
    KELLY_SKIP_ODDS_STALE,
    get_elite_rejection_reason,
    projected_kelly_skip_reason,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FRESH_TIME = (datetime.now() - timedelta(minutes=5)).isoformat()
_STALE_TIME = (datetime.now() - timedelta(minutes=45)).isoformat()
_SENTINEL = object()


def _candidate(
    *,
    odds_updated_at: str | None | object = _SENTINEL,
    player_name: str = "Test Player",
    game_status: str = "scheduled",
    game_datetime: str | None = None,
) -> dict[str, object]:
    future = (datetime.now() + timedelta(hours=2)).isoformat()
    _oa = _FRESH_TIME if odds_updated_at is _SENTINEL else odds_updated_at  # type: ignore[assignment]
    return {
        "player_name": player_name,
        "market_type": "player_points",
        "selection": "over",
        "edge": 2.0,
        "edge_pct": 2.0,
        "quality_score": 100.0,
        "confidence": 0.80,
        "sportsbook_line": 20.5,
        "line": 20.5,
        "odds": -110,
        "game_status": game_status,
        "game_date": game_datetime or future,
        "game_datetime": game_datetime or future,
        "postseason": "false",
        "odds_updated_at": _oa,
    }


# ---------------------------------------------------------------------------
# odds_stale_ineligibility_reason core tests
# ---------------------------------------------------------------------------


def test_fresh_odds_pass() -> None:
    row = _candidate(odds_updated_at=_FRESH_TIME)
    assert odds_stale_ineligibility_reason(row) == ""
    assert is_odds_fresh(row) is True


def test_stale_odds_blocked() -> None:
    row = _candidate(odds_updated_at=_STALE_TIME)
    assert odds_stale_ineligibility_reason(row) == "odds_stale"
    assert is_odds_fresh(row) is False


def test_missing_updated_at_blocked() -> None:
    row = _candidate(odds_updated_at="")
    assert odds_stale_ineligibility_reason(row) == "odds_stale"
    assert is_odds_fresh(row) is False


def test_none_updated_at_blocked() -> None:
    row = _candidate(odds_updated_at=None)
    assert odds_stale_ineligibility_reason(row) == "odds_stale"
    assert is_odds_fresh(row) is False


def test_unparseable_updated_at_blocked() -> None:
    row = _candidate(odds_updated_at="not-a-date")
    assert odds_stale_ineligibility_reason(row) == "odds_stale"
    assert is_odds_fresh(row) is False


def test_custom_stale_threshold() -> None:
    # 15 minutes old with 10-minute threshold → stale
    _15min = (datetime.now() - timedelta(minutes=15)).isoformat()
    row = _candidate(odds_updated_at=_15min)
    assert odds_stale_ineligibility_reason(row, stale_threshold_minutes=10) == "odds_stale"
    # Same 15 minutes old with 20-minute threshold → fresh
    assert odds_stale_ineligibility_reason(row, stale_threshold_minutes=20) == ""


def test_datetime_object_accepted() -> None:
    dt = datetime.now() - timedelta(minutes=5)
    row = _candidate(odds_updated_at=dt)
    assert odds_stale_ineligibility_reason(row) == ""


def test_stale_datetime_object_blocked() -> None:
    dt = datetime.now() - timedelta(minutes=45)
    row = _candidate(odds_updated_at=dt)
    assert odds_stale_ineligibility_reason(row) == "odds_stale"


def test_now_parameter_prevents_stale() -> None:
    _10min = (datetime.now() - timedelta(minutes=10)).isoformat()
    row = _candidate(odds_updated_at=_10min)
    # With reference time 60 minutes ago, the odds are "fresh" relative to then
    now = datetime.now() - timedelta(minutes=60)
    assert odds_stale_ineligibility_reason(row, now=now) == ""


# ---------------------------------------------------------------------------
# Betting vs research mode
# ---------------------------------------------------------------------------


def test_research_mode_bypasses_odds_stale() -> None:
    with mock.patch.dict("os.environ", {"COURTVISION_MODE": "research"}):
        for updated_at in (_STALE_TIME, "", "not-a-date", None):
            row = _candidate(odds_updated_at=updated_at)
            assert odds_stale_ineligibility_reason(row) == ""
            assert is_odds_fresh(row) is True


def test_betting_mode_enforces_odds_stale() -> None:
    with mock.patch.dict("os.environ", {"COURTVISION_MODE": "betting"}):
        row = _candidate(odds_updated_at=_STALE_TIME)
        assert odds_stale_ineligibility_reason(row) == "odds_stale"
        assert is_odds_fresh(row) is False


def test_default_mode_is_betting() -> None:
    # Ensure default (no env var) is betting mode
    with mock.patch.dict("os.environ", {}, clear=True):
        row = _candidate(odds_updated_at=_STALE_TIME)
        assert odds_stale_ineligibility_reason(row) == "odds_stale"


# ---------------------------------------------------------------------------
# Elite rejection reason mapping
# ---------------------------------------------------------------------------


def test_stale_odds_elite_rejection_reason() -> None:
    row = _candidate(odds_updated_at=_STALE_TIME)
    assert get_elite_rejection_reason(row) == ELITE_REJECT_ODDS_STALE


def test_missing_updated_at_elite_rejection_reason() -> None:
    row = _candidate(odds_updated_at="")
    assert get_elite_rejection_reason(row) == ELITE_REJECT_ODDS_STALE


def test_unparseable_updated_at_elite_rejection_reason() -> None:
    row = _candidate(odds_updated_at="garbage")
    assert get_elite_rejection_reason(row) == ELITE_REJECT_ODDS_STALE


def test_fresh_odds_no_elite_rejection() -> None:
    row = _candidate(odds_updated_at=_FRESH_TIME)
    # Fresh odds should not trigger the stale gate; game status is scheduled
    # so the only remaining gate that might block is the directional edge
    # check which requires specific edge/selection values.
    reason = get_elite_rejection_reason(row)
    assert reason != ELITE_REJECT_ODDS_STALE


# ---------------------------------------------------------------------------
# Kelly skip reason mapping
# ---------------------------------------------------------------------------


def test_stale_odds_kelly_skip_reason() -> None:
    row = _candidate(odds_updated_at=_STALE_TIME)
    assert projected_kelly_skip_reason(row) == KELLY_SKIP_ODDS_STALE


def test_missing_updated_at_kelly_skip_reason() -> None:
    row = _candidate(odds_updated_at="")
    assert projected_kelly_skip_reason(row) == KELLY_SKIP_ODDS_STALE


def test_fresh_odds_no_kelly_skip() -> None:
    row = _candidate(odds_updated_at=_FRESH_TIME)
    skip = projected_kelly_skip_reason(row)
    assert skip != KELLY_SKIP_ODDS_STALE


# ---------------------------------------------------------------------------
# DataFrame-level integration
# ---------------------------------------------------------------------------


def test_dataframe_filters_stale_odds() -> None:
    rows = [
        _candidate(odds_updated_at=_FRESH_TIME, player_name="Fresh"),
        _candidate(odds_updated_at=_STALE_TIME, player_name="Stale"),
        _candidate(odds_updated_at="", player_name="Missing"),
    ]
    df = pd.DataFrame(rows)
    fresh_mask = df.apply(lambda r: is_odds_fresh(r.to_dict()), axis=1)
    fresh = df[fresh_mask]
    stale = df[~fresh_mask]

    assert len(fresh) == 1
    assert fresh.iloc[0]["player_name"] == "Fresh"
    assert len(stale) == 2


def test_dataframe_elite_rejection_reasons() -> None:
    rows = [
        _candidate(odds_updated_at=_FRESH_TIME, player_name="Fresh"),
        _candidate(odds_updated_at=_STALE_TIME, player_name="Stale"),
    ]
    df = pd.DataFrame(rows)
    df["elite_rejection_reason"] = df.apply(
        lambda r: get_elite_rejection_reason(r.to_dict()), axis=1,
    )
    assert df.loc[df["player_name"] == "Fresh", "elite_rejection_reason"].iloc[0] != ELITE_REJECT_ODDS_STALE
    assert df.loc[df["player_name"] == "Stale", "elite_rejection_reason"].iloc[0] == ELITE_REJECT_ODDS_STALE


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


def test_default_stale_minutes_is_reasonable() -> None:
    assert DEFAULT_ODDS_STALE_MINUTES == 30


# ---------------------------------------------------------------------------
# Edge case: odds_updated_at present but game_status already blocks
# ---------------------------------------------------------------------------


def test_game_status_gate_takes_precedence() -> None:
    # Even with fresh odds, a final game should be blocked by game_status first
    row = _candidate(odds_updated_at=_FRESH_TIME, game_status="final")
    elite_reason = get_elite_rejection_reason(row)
    assert "game_final" in elite_reason
    kelly_skip = projected_kelly_skip_reason(row)
    assert "game_final" in kelly_skip
