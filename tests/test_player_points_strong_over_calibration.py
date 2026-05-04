"""Tests for the player_points strong-OVER calibration guard.

Historical calibration showed player_points OVER picks at edge >= +3 hit only
41.6% (n=127). The guard blocks them from Elite and Kelly while keeping them
visible in Full Market for diagnostics.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

# Import courtvision_ai first to prime the module graph (avoids a pre-existing
# circular import between runtime_audit and runtime_selection if runtime_audit
# is imported before courtvision_ai loads).
import courtvision_ai  # noqa: F401  (import side effects)

from courtvision.runtime_audit import (
    BoardAuditPolicy,
    ELITE_REJECT_PLAYER_POINTS_STRONG_OVER_CALIBRATION,
    KELLY_PROJECTED_SKIP_PLAYER_POINTS_STRONG_OVER_CALIBRATION,
    get_elite_rejection_reason,
    projected_kelly_skip_reason,
)
from courtvision.runtime_selection import (
    BoardVolumeConfig,
    BoardVolumePolicy,
    PLAYER_POINTS_STRONG_OVER_CALIBRATION_GUARD_REASON,
    passes_player_points_strong_over_calibration,
    player_points_strong_over_calibration_reason,
)
from courtvision.runtime_scoring import BoardScoringConfig, BoardScoringPolicy
from courtvision.scoring.candidate_scoring import CandidateScoringPolicy


# Future datetime to pass the game-status gate so tests target the guard under test.
_FUTURE_DATE = (datetime.now() + timedelta(hours=2)).isoformat()
#: Recent timestamp for fresh odds in tests
_FRESH_ODDS_TIME = (datetime.now() - timedelta(minutes=5)).isoformat()


def _candidate(
    *,
    market_type: str = "player_points",
    selection: str = "over",
    edge: float = 4.0,
    line: float = 20.0,
    confidence: float = 0.80,
    quality_score: float = 100.0,
    minutes_avg: float = 34.0,
    qualification_gate_mode: str = "core_pass",
) -> dict[str, object]:
    return {
        "market_type": market_type,
        "entity_name": "Test Player",
        "player_name": "Test Player",
        "team": "BOS",
        "team_abbr": "BOS",
        "opponent": "PHI",
        "selection": selection,
        "sportsbook_line": line,
        "line": line,
        "model_projection": line + edge if selection == "over" else line - abs(edge),
        "projection": line + edge if selection == "over" else line - abs(edge),
        "edge": edge if selection == "over" else -abs(edge),
        "edge_abs": abs(edge),
        "confidence": confidence,
        "quality_score": quality_score,
        "selection_score": quality_score,
        "minutes_avg": minutes_avg,
        "minutes_recent": minutes_avg,
        "is_live_market": True,
        "synthetic_line": False,
        "line_source": "live_market",
        "qualification_gate_mode": qualification_gate_mode,
        "odds": -110,
        # Include valid future game status so the game-status gate does not block
        # before the strong-over calibration guard can be tested.
        "game_status": "scheduled",
        "game_date": _FUTURE_DATE,
        "game_datetime": _FUTURE_DATE,
        "postseason": "false",
        # Include fresh odds timestamp so odds-stale gate doesn't block
        "odds_updated_at": _FRESH_ODDS_TIME,
    }


# ---- guard reason -----------------------------------------------------------

def test_strong_over_above_threshold_is_blocked() -> None:
    row = _candidate(selection="over", edge=4.0)
    assert (
        player_points_strong_over_calibration_reason(row)
        == PLAYER_POINTS_STRONG_OVER_CALIBRATION_GUARD_REASON
    )
    assert passes_player_points_strong_over_calibration(row) is False


def test_strong_over_at_exact_threshold_is_blocked() -> None:
    row = _candidate(selection="over", edge=3.0)
    assert (
        player_points_strong_over_calibration_reason(row)
        == PLAYER_POINTS_STRONG_OVER_CALIBRATION_GUARD_REASON
    )


def test_mild_over_below_threshold_is_not_blocked() -> None:
    row = _candidate(selection="over", edge=2.99)
    assert player_points_strong_over_calibration_reason(row) == ""
    assert passes_player_points_strong_over_calibration(row) is True


def test_strong_under_is_not_blocked() -> None:
    row = _candidate(selection="under", edge=-4.0)
    assert player_points_strong_over_calibration_reason(row) == ""
    assert passes_player_points_strong_over_calibration(row) is True


def test_non_player_points_market_is_not_blocked() -> None:
    for market in ("player_rebounds", "player_assists", "player_points_assists"):
        row = _candidate(market_type=market, selection="over", edge=5.0)
        assert player_points_strong_over_calibration_reason(row) == ""
        assert passes_player_points_strong_over_calibration(row) is True


# ---- elite admission --------------------------------------------------------

def test_strong_over_blocked_from_runtime_scoring_elite() -> None:
    row = _candidate(selection="over", edge=4.0)
    policy = BoardScoringPolicy()
    assert policy.is_elite_candidate(row) is False


def test_strong_over_blocked_from_candidate_scoring_elite() -> None:
    row = _candidate(selection="over", edge=4.0)
    policy = CandidateScoringPolicy()
    assert policy.is_elite_candidate(row) is False


def test_strong_over_blocked_from_elite_backfill() -> None:
    row = _candidate(selection="over", edge=4.0)
    policy = BoardVolumePolicy(BoardVolumeConfig())
    assert policy.is_elite_backfill_candidate(row) is False


def test_strong_over_remains_full_market_backfill_eligible() -> None:
    """Strong-OVER calibration guard must NOT block full-market admission."""
    row = _candidate(selection="over", edge=4.0)
    policy = BoardVolumePolicy(BoardVolumeConfig())
    # Full-market backfill check should not consult the strong-over guard.
    # If all other gates pass, full-market should accept.
    assert policy.is_full_market_backfill_candidate(row) is True


def test_mild_over_remains_elite_eligible() -> None:
    """Mild over (edge < 3) should still be allowed through the strong-over guard."""
    row = _candidate(selection="over", edge=2.5, quality_score=110.0, confidence=0.82, minutes_avg=34.0)
    policy = BoardScoringPolicy()
    # Strong-over guard should not fire; whether other gates pass depends on config,
    # but at minimum the strong-over guard should not be the blocker.
    assert passes_player_points_strong_over_calibration(row) is True


def test_strong_under_remains_elite_eligible() -> None:
    row = _candidate(selection="under", edge=-4.0, quality_score=110.0, confidence=0.82, minutes_avg=34.0)
    policy = BoardScoringPolicy()
    # Strong-under is allowed by this guard.
    assert passes_player_points_strong_over_calibration(row) is True


# ---- Kelly skip reason ------------------------------------------------------

def test_strong_over_sets_kelly_skip_reason() -> None:
    row = _candidate(selection="over", edge=4.0)
    assert (
        projected_kelly_skip_reason(row)
        == KELLY_PROJECTED_SKIP_PLAYER_POINTS_STRONG_OVER_CALIBRATION
    )


def test_strong_under_does_not_set_strong_over_kelly_skip() -> None:
    row = _candidate(selection="under", edge=-4.0)
    skip = projected_kelly_skip_reason(row)
    assert skip != KELLY_PROJECTED_SKIP_PLAYER_POINTS_STRONG_OVER_CALIBRATION


# ---- elite rejection reason -------------------------------------------------

def test_strong_over_elite_rejection_reason() -> None:
    row = _candidate(selection="over", edge=4.0)
    assert get_elite_rejection_reason(row) == ELITE_REJECT_PLAYER_POINTS_STRONG_OVER_CALIBRATION


# ---- row annotation + diagnostics ------------------------------------------

def test_apply_row_audit_annotates_strong_over_block() -> None:
    row = _candidate(selection="over", edge=4.5)
    audited = BoardAuditPolicy().apply_row_audit(row)
    assert audited["blocked_by_player_points_strong_over_calibration"] is True
    assert (
        audited["player_points_strong_over_calibration_reason"]
        == PLAYER_POINTS_STRONG_OVER_CALIBRATION_GUARD_REASON
    )


def test_apply_row_audit_does_not_annotate_under() -> None:
    row = _candidate(selection="under", edge=-4.0)
    audited = BoardAuditPolicy().apply_row_audit(row)
    assert audited["blocked_by_player_points_strong_over_calibration"] is False
    assert audited["player_points_strong_over_calibration_reason"] == ""


def test_diagnostics_counts_strong_over_guards() -> None:
    qualified = pd.DataFrame(
        [
            _candidate(selection="over", edge=4.5),         # blocked, edge_3_to_6
            _candidate(selection="over", edge=7.0),         # blocked, edge_6_plus
            _candidate(selection="over", edge=2.0),         # not blocked
            _candidate(selection="under", edge=-4.0),       # not blocked
            _candidate(market_type="player_rebounds", edge=5.0),  # not blocked
        ]
    )
    policy = BoardAuditPolicy()
    audited = policy.apply_dataframe_audit(qualified)
    diagnostics = policy.build_diagnostics(
        prediction_date="2026-05-03",
        qualified_pool_df=audited,
        elite_df=pd.DataFrame(),
        full_market_df=pd.DataFrame(),
        rejected_df=pd.DataFrame(),
    )
    payload = diagnostics["player_points_strong_over_guard"]
    assert payload["player_points_strong_over_guard_count"] == 2
    assert payload["player_points_strong_over_guard_by_edge_bucket"] == {
        "edge_3_to_6": 1,
        "edge_6_plus": 1,
    }
    assert payload["player_points_strong_over_guard_by_player"] == {"Test Player": 2}


# ---- backfill / replacement bypass guard ------------------------------------

def _to_dataframe(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _make_cvi():
    """Create a minimal CourtVisionAI instance for testing."""
    from courtvision_ai import CourtVisionAI
    cvi = CourtVisionAI()
    cvi.ELITE_BOARD_LIMIT = 3
    cvi.ELITE_MAX_PROPS_PER_PLAYER = 2
    cvi.ELITE_TEAM_CAP = 3
    cvi.ELITE_GAME_CAP = 3
    return cvi


def test_strong_over_blocked_from_elite_replacement_backfill() -> None:
    """Strong OVER must not re-enter Elite via replacement backfill."""
    cvi = _make_cvi()
    # Create a survivor that WILL be rejected by context gate (high caution + conflicted)
    # so that backfill logic runs
    survivor = _candidate(
        selection="over",
        edge=2.0,
        quality_score=110.0,
        confidence=0.82,
    )
    survivor["context_caution_level"] = "high"
    survivor["context_pick_alignment"] = "conflicted"
    survivor["context_suppressed"] = False
    survivor["final_elite_rejection_reason"] = ""
    survivor["elite_rejection_reason"] = ""
    survivor["selection_rejection_reason"] = ""

    strong_over = _candidate(
        selection="over",
        edge=4.5,
        quality_score=100.0,
    )
    strong_over["context_caution_level"] = "low"
    strong_over["context_pick_alignment"] = "aligned"
    strong_over["context_suppressed"] = False
    strong_over["final_elite_rejection_reason"] = ""
    strong_over["elite_rejection_reason"] = ""
    strong_over["selection_rejection_reason"] = ""

    elite_df = _to_dataframe([survivor])
    candidate_df = _to_dataframe([strong_over])

    combined, _, summary = cvi._apply_elite_context_safety_gate(
        elite_df, candidate_df, target_size=2
    )

    # The strong OVER should NOT be in the final combined elite
    assert len(combined) == 0
    assert summary["strong_over_guard_blocked_count"] == 1
    assert summary["replacement_added_count"] == 0


def test_mild_over_can_replace_backfill() -> None:
    """Mild OVER (edge < 3) should be allowed through replacement backfill."""
    cvi = _make_cvi()
    # Create a survivor that WILL be rejected by context gate (high caution + conflicted)
    # so that backfill logic runs
    survivor = _candidate(
        selection="over",
        edge=2.0,
        quality_score=110.0,
        confidence=0.82,
    )
    survivor["context_caution_level"] = "high"
    survivor["context_pick_alignment"] = "conflicted"
    survivor["context_suppressed"] = False
    survivor["final_elite_rejection_reason"] = ""
    survivor["elite_rejection_reason"] = ""
    survivor["selection_rejection_reason"] = ""

    mild_over = _candidate(
        selection="over",
        edge=2.5,
        quality_score=105.0,
        confidence=0.80,
    )
    mild_over["context_caution_level"] = "low"
    mild_over["context_pick_alignment"] = "aligned"
    mild_over["context_suppressed"] = False
    mild_over["final_elite_rejection_reason"] = ""
    mild_over["elite_rejection_reason"] = ""
    mild_over["selection_rejection_reason"] = ""

    elite_df = _to_dataframe([survivor])
    candidate_df = _to_dataframe([mild_over])

    combined, _, summary = cvi._apply_elite_context_safety_gate(
        elite_df, candidate_df, target_size=2
    )

    # The mild OVER should be allowed as replacement
    assert len(combined) == 1
    assert summary["strong_over_guard_blocked_count"] == 0
    assert summary["replacement_added_count"] == 1


def test_under_and_non_pp_unaffected_in_backfill() -> None:
    """UNDER and non-player_points markets should not be blocked by this guard."""
    cvi = _make_cvi()
    # Create a survivor that WILL be rejected by context gate (high caution + conflicted)
    # so that backfill logic runs
    survivor = _candidate(
        selection="over",
        edge=2.0,
        quality_score=110.0,
        confidence=0.82,
    )
    survivor["context_caution_level"] = "high"
    survivor["context_pick_alignment"] = "conflicted"
    survivor["context_suppressed"] = False
    survivor["final_elite_rejection_reason"] = ""
    survivor["elite_rejection_reason"] = ""
    survivor["selection_rejection_reason"] = ""

    # Non-player_points candidate (filtered out by replacement pool market_type filter)
    other_market = _candidate(
        market_type="player_rebounds",
        selection="over",
        edge=5.0,
        quality_score=105.0,
    )
    other_market["context_caution_level"] = "low"
    other_market["context_pick_alignment"] = "aligned"
    other_market["context_suppressed"] = False
    other_market["final_elite_rejection_reason"] = ""
    other_market["elite_rejection_reason"] = ""
    other_market["selection_rejection_reason"] = ""

    # The replacement_pool in _apply_elite_context_safety_gate filters to
    # player_points only, so non-player_points will not be in replacement_pool.
    # This test verifies the guard does not affect them.
    elite_df = _to_dataframe([survivor])
    candidate_df = _to_dataframe([other_market])

    combined, _, summary = cvi._apply_elite_context_safety_gate(
        elite_df, candidate_df, target_size=2
    )

    # Non-player_points not in replacement pool, so combined is empty
    assert len(combined) == 0
    assert summary["strong_over_guard_blocked_count"] == 0
    assert summary["replacement_added_count"] == 0
