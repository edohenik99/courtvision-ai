from __future__ import annotations

import pandas as pd

from courtvision.context import game_context
from courtvision.runtime_gates import (
    IDENTITY_GAME_NOT_BETTABLE_REASON,
    IDENTITY_OUTSIDE_TEAM_REASON,
    IDENTITY_QUARANTINE_REASONS,
    IDENTITY_QUARANTINE_REJECTION_REASON,
    IDENTITY_STALE_TEAM_REASON,
    identity_gate_status,
    identity_quarantine_reason,
    is_identity_quarantined,
)


def test_game_context_reexports_identity_reason_values() -> None:
    assert game_context.IDENTITY_OUTSIDE_TEAM_REASON == IDENTITY_OUTSIDE_TEAM_REASON
    assert game_context.IDENTITY_STALE_TEAM_REASON == IDENTITY_STALE_TEAM_REASON
    assert game_context.IDENTITY_GAME_NOT_BETTABLE_REASON == IDENTITY_GAME_NOT_BETTABLE_REASON
    assert game_context.IDENTITY_QUARANTINE_REASONS == IDENTITY_QUARANTINE_REASONS
    assert (
        game_context.IDENTITY_QUARANTINE_REJECTION_REASON
        == IDENTITY_QUARANTINE_REJECTION_REASON
    )


def test_identity_quarantine_reason_detects_explicit_reasons() -> None:
    for reason in (
        IDENTITY_OUTSIDE_TEAM_REASON,
        IDENTITY_STALE_TEAM_REASON,
        IDENTITY_GAME_NOT_BETTABLE_REASON,
    ):
        row = {"identity_quarantine_reason": reason}
        assert identity_quarantine_reason(row) == reason
        assert is_identity_quarantined(row) == reason
        assert game_context.is_identity_quarantined(row) == reason


def test_identity_quarantine_reason_detects_inferred_outside_team() -> None:
    row = {
        "team_abbr": "SAC",
        "game_home_team_abbr": "CLE",
        "game_away_team_abbr": "DET",
    }

    assert identity_quarantine_reason(row) == IDENTITY_OUTSIDE_TEAM_REASON
    assert identity_gate_status(row) == {
        "quarantined": True,
        "reason": IDENTITY_OUTSIDE_TEAM_REASON,
    }


def test_identity_quarantine_reason_detects_inferred_stale_team() -> None:
    row = {
        "team_abbr": "CLE",
        "game_home_team_abbr": "CLE",
        "game_away_team_abbr": "DET",
        "provider_team_abbr": "LAC",
    }

    assert identity_quarantine_reason(row) == IDENTITY_STALE_TEAM_REASON
    assert game_context.identity_quarantine_reason(row) == IDENTITY_STALE_TEAM_REASON


def test_identity_quarantine_reason_preserves_selection_rejection_fallback() -> None:
    assert (
        identity_quarantine_reason(
            {"selection_rejection_reason": IDENTITY_QUARANTINE_REJECTION_REASON}
        )
        == IDENTITY_GAME_NOT_BETTABLE_REASON
    )
    assert (
        identity_quarantine_reason(
            {
                "selection_rejection_reason": IDENTITY_QUARANTINE_REJECTION_REASON,
                "identity_quarantine_reason": "custom_identity_reason",
            }
        )
        == "custom_identity_reason"
    )


def test_identity_quarantine_reason_missing_and_null_columns_pass() -> None:
    row = pd.Series(
        {
            "identity_quarantine_reason": pd.NA,
            "selection_rejection_reason": "",
            "candidate_team_not_in_game": pd.NA,
            "context_conflict_cause": pd.NA,
        }
    )

    assert identity_quarantine_reason({}) is None
    assert identity_quarantine_reason(row) is None
    assert identity_gate_status(row) == {"quarantined": False, "reason": ""}
