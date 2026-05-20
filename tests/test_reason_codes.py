from __future__ import annotations

from datetime import datetime, timedelta
import importlib

from courtvision import reason_codes as reasons


def _fresh_points_row(**overrides: object) -> dict[str, object]:
    now = datetime.now()
    row: dict[str, object] = {
        "market_type": "player_points",
        "selection": "over",
        "sportsbook_line": 20.5,
        "line": 20.5,
        "odds": -110,
        "edge": 1.0,
        "edge_pct": 0.05,
        "confidence": 0.80,
        "game_status": "scheduled",
        "game_date": (now + timedelta(hours=3)).isoformat(),
        "game_datetime": (now + timedelta(hours=3)).isoformat(),
        "odds_updated_at": (now - timedelta(minutes=5)).isoformat(),
    }
    row.update(overrides)
    return row


def test_shared_reason_code_values_are_stable() -> None:
    expected = {
        "GAME_STATUS_REASON_FINAL": "game_final",
        "GAME_STATUS_REASON_IN_PROGRESS": "game_in_progress",
        "GAME_STATUS_REASON_POSTPONED": "game_postponed",
        "GAME_STATUS_REASON_LOCKED": "game_locked",
        "GAME_STATUS_REASON_UNKNOWN": "game_status_unknown",
        "ODDS_STALE_REASON": "odds_stale",
        "REJECT_NEGATIVE_EDGE_DIRECTION": "reject_negative_edge_direction",
        "ELITE_REJECT_CONTEXT_HIGH_CAUTION_OVER": "elite_reject_context_high_caution_over",
        "ELITE_REJECT_GAME_FINAL": "elite_reject_game_final",
        "ELITE_REJECT_ODDS_STALE": "elite_reject_odds_stale",
        "KELLY_SKIP_CONTEXT_HIGH_CAUTION_OVER": "context_high_caution_over",
        "KELLY_SKIP_POINTS_ONLY_MARKET_LOCK": "kelly_points_only_market_lock",
        "KELLY_SKIP_MISSING_OR_INVALID_ODDS": "missing_or_invalid_odds",
        "KELLY_SKIP_NON_POSITIVE_DECIMAL_ODDS": "non_positive_decimal_odds",
        "KELLY_SKIP_MISSING_CONFIDENCE": "missing_confidence",
        "KELLY_SKIP_NON_POSITIVE_EDGE": "non_positive_edge",
        "KELLY_SKIP_RETURNED_ZERO": "kelly_returned_zero",
        "EDGE_CONTAINMENT_HOLD_SKIP_REASON": "edge_containment_hold_for_review",
        "MEDIUM_NEUTRAL_OVER_DAMPENER_REASON": "medium_neutral_over_dampener",
        "UNSUPPORTED_ACTIVE_OPERATOR_MARKET_REASON": "unsupported_active_operator_market",
        "DUPLICATE_BETTING_IDENTITY_REASON": "duplicate_betting_identity",
        "UNSUPPORTED_MILESTONE_MARKET_REASON": "unsupported_milestone_market",
        "SELECTION_NOT_LIVE_MARKET_ELIGIBLE_REASON": "selection_not_live_market_eligible",
        "SELECTION_LIVE_GATE_MISSING_QUALIFICATION_REASON": (
            "selection_live_gate_missing_qualification_reason"
        ),
        "SELECTION_LIVE_GATE_FILTERED_REASON": "selection_live_gate_filtered",
        "SELECTION_NOT_SELECTED_BY_BOARD_SELECTOR_REASON": (
            "selection_not_selected_by_board_selector"
        ),
        "PLAYER_POINTS_STRONG_OVER_CALIBRATION_GUARD_REASON": (
            "player_points_strong_over_calibration_guard"
        ),
    }

    for name, value in expected.items():
        assert getattr(reasons, name) == value


def test_existing_modules_reexport_same_reason_values() -> None:
    from courtvision import runtime_audit
    from courtvision import runtime_selection
    from courtvision.selection import operator_boards
    from scripts import run_kelly_stakes

    assert runtime_audit.REJECT_NEGATIVE_EDGE_DIRECTION == reasons.REJECT_NEGATIVE_EDGE_DIRECTION
    assert runtime_audit.KELLY_SKIP_ODDS_STALE == reasons.KELLY_SKIP_ODDS_STALE
    assert (
        runtime_selection.PLAYER_POINTS_STRONG_OVER_CALIBRATION_GUARD_REASON
        == reasons.PLAYER_POINTS_STRONG_OVER_CALIBRATION_GUARD_REASON
    )
    assert (
        operator_boards.UNSUPPORTED_ACTIVE_OPERATOR_MARKET_REASON
        == reasons.UNSUPPORTED_ACTIVE_OPERATOR_MARKET_REASON
    )
    assert (
        operator_boards.DUPLICATE_BETTING_IDENTITY_REASON
        == reasons.DUPLICATE_BETTING_IDENTITY_REASON
    )
    assert (
        run_kelly_stakes.EDGE_CONTAINMENT_HOLD_SKIP_REASON
        == reasons.EDGE_CONTAINMENT_HOLD_SKIP_REASON
    )


def test_elite_rejection_reasons_still_emit_same_strings() -> None:
    from courtvision.runtime_audit import get_elite_rejection_reason

    assert (
        get_elite_rejection_reason(_fresh_points_row(game_status="final"))
        == reasons.ELITE_REJECT_GAME_FINAL
    )
    assert (
        get_elite_rejection_reason(_fresh_points_row(selection="over", edge=-0.10, edge_pct=-0.10))
        == reasons.REJECT_NEGATIVE_EDGE_DIRECTION
    )
    assert (
        get_elite_rejection_reason(
            _fresh_points_row(
                selection="over",
                edge=1.0,
                edge_pct=0.05,
                context_caution_level="high",
                context_pick_alignment="conflicted",
            )
        )
        == reasons.ELITE_REJECT_CONTEXT_HIGH_CAUTION_OVER
    )


def test_projected_and_actual_kelly_skip_reasons_still_emit_same_strings() -> None:
    from courtvision.runtime_audit import projected_kelly_skip_reason
    from scripts.run_kelly_stakes import _build_stake_row

    assert (
        projected_kelly_skip_reason(_fresh_points_row(game_status="final"))
        == reasons.KELLY_SKIP_GAME_FINAL
    )
    assert (
        projected_kelly_skip_reason(_fresh_points_row(context_caution_level="high"))
        == reasons.KELLY_SKIP_CONTEXT_HIGH_CAUTION_OVER
    )

    stake = _build_stake_row(
        {
            "player_name": "Reason Code Player",
            "market_type": "player_rebounds",
            "selection": "over",
            "line": "8.5",
            "odds": "-110",
            "confidence": "0.80",
            "edge_pct": "0.10",
            "side_edge_pct": "0.10",
        },
        "side_edge_pct",
        1000.0,
    )
    assert stake.skip_reason == reasons.KELLY_SKIP_POINTS_ONLY_MARKET_LOCK

    milestone = _build_stake_row(
        {
            "player_name": "Milestone Player",
            "market_type": "player_points",
            "raw_market_type": "milestone",
            "selection": "over",
            "line": "20.5",
            "odds": "-110",
            "confidence": "0.80",
            "edge_pct": "0.10",
            "side_edge_pct": "0.10",
        },
        "side_edge_pct",
        1000.0,
    )
    assert milestone.skip_reason == reasons.UNSUPPORTED_MILESTONE_MARKET_REASON


def test_reason_code_imports_do_not_introduce_cycles() -> None:
    for module_name in (
        "courtvision.reason_codes",
        "courtvision.runtime_gates",
        "courtvision.runtime_audit",
        "courtvision.runtime_selection",
        "courtvision.selection.operator_boards",
        "courtvision.pipeline.predict_pipeline",
        "scripts.run_kelly_stakes",
    ):
        importlib.import_module(module_name)


def test_runtime_selection_reexports_shared_runtime_gate_helpers() -> None:
    from courtvision import runtime_gates
    from courtvision import runtime_selection

    assert (
        runtime_selection.game_status_ineligibility_reason
        is runtime_gates.game_status_ineligibility_reason
    )
    assert runtime_selection.is_game_bettable is runtime_gates.is_game_bettable
    assert (
        runtime_selection.odds_stale_ineligibility_reason
        is runtime_gates.odds_stale_ineligibility_reason
    )
    assert runtime_selection.is_odds_fresh is runtime_gates.is_odds_fresh
    assert (
        runtime_selection.DEFAULT_GAME_LOCK_BUFFER_MINUTES
        == runtime_gates.DEFAULT_GAME_LOCK_BUFFER_MINUTES
    )
    assert (
        runtime_selection.DEFAULT_ODDS_STALE_MINUTES
        == runtime_gates.DEFAULT_ODDS_STALE_MINUTES
    )
