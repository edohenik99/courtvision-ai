"""
Tests for Phase 12D: Edge Containment Shadow Validation.

Shadow-validation layer: no live logic is modified, no rows are removed.
Tests cover mask building, policy validation, verdict logic, P&L decomposition,
forward tracking, no-mutation guarantees, missing-column safety, and
quality_summary integration.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.edge_containment_shadow_validation import (
    REPORT_VERSION,
    _COMBO_MARKETS,
    _POLICY_SPECS,
    _build_mask,
    _forward_track_policy,
    _policy_verdict,
    _risk_reduction_score,
    _validate_policy,
    build_edge_containment_shadow_validation,
    containment_json_path_for_date,
    containment_txt_path_for_date,
    write_edge_containment_shadow_validation,
)

DATE = "2026-05-10"


# ---------------------------------------------------------------------------
# Fixtures / row factories
# ---------------------------------------------------------------------------

def _row(
    result_status: str = "hit",
    edge: float = 2.0,
    shadow_roi: float = 0.05,
    model_projection: float = 22.0,
    actual_value: float = 22.0,
    selection: str = "over",
    market_type: str = "player_points",
    context_pick_alignment: str = "aligned",
    player_name: str = "Player A",
    game_id: str = "GAME_001",
    confidence: float = 0.70,
) -> dict:
    return {
        "result_status": result_status,
        "edge": edge,
        "shadow_roi": shadow_roi,
        "model_projection": model_projection,
        "actual_value": actual_value,
        "selection": selection,
        "market_type": market_type,
        "context_pick_alignment": context_pick_alignment,
        "player_name": player_name,
        "game_id": game_id,
        "confidence": confidence,
    }


def _make_df(n_hits: int = 20, n_misses: int = 15, **kwargs) -> pd.DataFrame:
    rows = [_row("hit",  **kwargs)] * n_hits
    rows += [_row("miss", **kwargs)] * n_misses
    return pd.DataFrame(rows)


def _make_graded(n: int = 40, hit_rate: float = 0.50, **row_kwargs) -> pd.DataFrame:
    n_hits = int(n * hit_rate)
    n_miss = n - n_hits
    rows = [_row("hit",  **row_kwargs)] * n_hits
    rows += [_row("miss", shadow_roi=-0.10, **row_kwargs)] * n_miss
    return pd.DataFrame(rows)


def _spec(name: str) -> dict:
    return next(s for s in _POLICY_SPECS if s["name"] == name)


# ---------------------------------------------------------------------------
# Tests: _risk_reduction_score
# ---------------------------------------------------------------------------

class TestRiskReductionScore:
    def test_flagged_worse_than_baseline(self):
        # flagged roi = -0.50, baseline roi = -0.10 → ((-0.10 - (-0.50)) * 100) = 40
        assert _risk_reduction_score(-0.50, -0.10) == pytest.approx(40.0)

    def test_flagged_better_than_baseline_clamps_to_zero(self):
        # flagged roi = -0.05, baseline roi = -0.20 → negative → clamped to 0
        assert _risk_reduction_score(-0.05, -0.20) == 0.0

    def test_capped_at_100(self):
        # extreme difference → cap at 100
        assert _risk_reduction_score(-2.00, 0.00) == pytest.approx(100.0)

    def test_none_inputs_return_zero(self):
        assert _risk_reduction_score(None, -0.10) == 0.0
        assert _risk_reduction_score(-0.50, None) == 0.0
        assert _risk_reduction_score(None, None) == 0.0


# ---------------------------------------------------------------------------
# Tests: _policy_verdict
# ---------------------------------------------------------------------------

class TestPolicyVerdict:
    def test_insufficient_sample_below_20(self):
        assert _policy_verdict(19, 0.20, -0.60, 0.50, -0.10) == "INSUFFICIENT_SAMPLE"

    def test_insufficient_sample_on_none_metrics(self):
        assert _policy_verdict(25, None, -0.30, 0.50, -0.10) == "INSUFFICIENT_SAMPLE"
        assert _policy_verdict(25, 0.35, None, 0.50, -0.10) == "INSUFFICIENT_SAMPLE"

    def test_reject_policy_when_flagged_beats_baseline_on_both(self):
        # flagged hit_rate >= baseline AND flagged roi >= baseline → reject
        assert _policy_verdict(25, 0.55, -0.05, 0.50, -0.10) == "REJECT_POLICY"

    def test_ready_for_live_review_later(self):
        # n >= 50, hr < 0.40, roi < -0.30
        assert _policy_verdict(50, 0.39, -0.31, 0.50, -0.10) == "READY_FOR_LIVE_REVIEW_LATER"

    def test_ready_for_forward_shadow(self):
        # n >= 20, hr < 0.35, roi < -0.35
        assert _policy_verdict(20, 0.34, -0.36, 0.50, -0.10) == "READY_FOR_FORWARD_SHADOW"

    def test_promising_low_hit_rate(self):
        # n >= 20, hr < 0.43
        assert _policy_verdict(25, 0.42, -0.05, 0.50, -0.10) == "PROMISING"

    def test_promising_low_roi(self):
        # n >= 20, roi < -0.30
        assert _policy_verdict(25, 0.48, -0.31, 0.50, -0.10) == "PROMISING"

    def test_monitor(self):
        # n >= 20, hr in [0.43, 0.50), roi in [-0.30, -0.10)
        assert _policy_verdict(25, 0.45, -0.20, 0.50, -0.10) == "MONITOR"

    def test_reject_at_floor_of_monitor(self):
        # hr >= 0.50, roi >= -0.10 → reject
        assert _policy_verdict(25, 0.50, -0.09, 0.50, -0.10) == "REJECT_POLICY"

    def test_ready_for_live_review_at_exactly_50(self):
        assert _policy_verdict(50, 0.35, -0.50, 0.50, -0.10) == "READY_FOR_LIVE_REVIEW_LATER"


# ---------------------------------------------------------------------------
# Tests: _build_mask
# ---------------------------------------------------------------------------

class TestBuildMask:
    def test_edge_floor(self):
        df = pd.DataFrame([_row(edge=3.0), _row(edge=5.0), _row(edge=1.0)])
        spec = {"name": "t", "edge_min": 4.0, "edge_max": None, "selection": None,
                "combo": None, "context": None}
        mask = _build_mask(df, spec)
        assert list(mask) == [False, True, False]

    def test_edge_ceiling(self):
        # edge_max is exclusive: 3.0 < 5.0 = True, 6.0 < 5.0 = False, 5.0 < 5.0 = False
        df = pd.DataFrame([_row(edge=3.0), _row(edge=6.0), _row(edge=5.0)])
        spec = {"name": "t", "edge_min": None, "edge_max": 5.0, "selection": None,
                "combo": None, "context": None}
        mask = _build_mask(df, spec)
        assert list(mask) == [True, False, False]

    def test_selection_filter(self):
        df = pd.DataFrame([
            _row(edge=5.0, selection="over"),
            _row(edge=5.0, selection="under"),
        ])
        spec = {"name": "t", "edge_min": 4.0, "edge_max": None, "selection": "over",
                "combo": None, "context": None}
        mask = _build_mask(df, spec)
        assert list(mask) == [True, False]

    def test_combo_filter_true(self):
        combo_market = list(_COMBO_MARKETS)[0]
        df = pd.DataFrame([
            _row(edge=5.0, market_type=combo_market),
            _row(edge=5.0, market_type="player_points"),
        ])
        spec = {"name": "t", "edge_min": 4.0, "edge_max": None, "selection": None,
                "combo": True, "context": None}
        mask = _build_mask(df, spec)
        assert list(mask) == [True, False]

    def test_combo_filter_false(self):
        combo_market = list(_COMBO_MARKETS)[0]
        df = pd.DataFrame([
            _row(edge=5.0, market_type=combo_market),
            _row(edge=5.0, market_type="player_points"),
        ])
        spec = {"name": "t", "edge_min": 4.0, "edge_max": None, "selection": None,
                "combo": False, "context": None}
        mask = _build_mask(df, spec)
        assert list(mask) == [False, True]

    def test_context_filter_plain(self):
        df = pd.DataFrame([
            _row(edge=5.0, context_pick_alignment="conflicted"),
            _row(edge=5.0, context_pick_alignment="aligned"),
        ])
        spec = {"name": "t", "edge_min": 4.0, "edge_max": None, "selection": None,
                "combo": None, "context": "conflicted"}
        mask = _build_mask(df, spec)
        assert list(mask) == [True, False]

    def test_context_filter_negated(self):
        # "!aligned" means NOT aligned → matches everything except "aligned"
        df = pd.DataFrame([
            _row(edge=6.0, context_pick_alignment="aligned"),
            _row(edge=6.0, context_pick_alignment="conflicted"),
            _row(edge=6.0, context_pick_alignment="neutral"),
        ])
        spec = {"name": "t", "edge_min": 6.0, "edge_max": None, "selection": None,
                "combo": None, "context": "!aligned"}
        mask = _build_mask(df, spec)
        assert list(mask) == [False, True, True]

    def test_missing_edge_column_returns_all_false(self):
        df = pd.DataFrame([{"result_status": "hit", "selection": "over"}])
        spec = {"name": "t", "edge_min": 4.0, "edge_max": None, "selection": None,
                "combo": None, "context": None}
        mask = _build_mask(df, spec)
        assert list(mask) == [False]

    def test_missing_selection_column_returns_all_false(self):
        df = pd.DataFrame([{"edge": 5.0, "result_status": "hit"}])
        spec = {"name": "t", "edge_min": 4.0, "edge_max": None, "selection": "over",
                "combo": None, "context": None}
        mask = _build_mask(df, spec)
        assert list(mask) == [False]

    def test_missing_context_column_returns_all_false(self):
        df = pd.DataFrame([{"edge": 5.0, "result_status": "hit"}])
        spec = {"name": "t", "edge_min": 4.0, "edge_max": None, "selection": None,
                "combo": None, "context": "conflicted"}
        mask = _build_mask(df, spec)
        assert list(mask) == [False]

    def test_combined_edge_selection_context(self):
        df = pd.DataFrame([
            _row(edge=5.0, selection="over", context_pick_alignment="conflicted"),
            _row(edge=5.0, selection="over", context_pick_alignment="aligned"),
            _row(edge=5.0, selection="under", context_pick_alignment="conflicted"),
            _row(edge=1.0, selection="over", context_pick_alignment="conflicted"),
        ])
        spec = {"name": "t", "edge_min": 4.0, "edge_max": None, "selection": "over",
                "combo": None, "context": "conflicted"}
        mask = _build_mask(df, spec)
        assert list(mask) == [True, False, False, False]


# ---------------------------------------------------------------------------
# Tests: _validate_policy
# ---------------------------------------------------------------------------

class TestValidatePolicy:
    def _make_graded_with_roi(self, n_hit=15, n_miss=25, hit_roi=0.10, miss_roi=-0.10) -> pd.DataFrame:
        rows = [_row("hit", edge=5.0, shadow_roi=hit_roi)] * n_hit
        rows += [_row("miss", edge=5.0, shadow_roi=miss_roi)] * n_miss
        return pd.DataFrame(rows)

    def test_rows_flagged_count(self):
        df = self._make_graded_with_roi()
        spec = _spec("cap_effective_edge_at_4_percent")
        result = _validate_policy(df, spec, 0.50, -0.10, 1.0)
        assert result["rows_flagged"] == 40

    def test_hit_rate_flagged(self):
        df = self._make_graded_with_roi(n_hit=20, n_miss=20)
        spec = _spec("cap_effective_edge_at_4_percent")
        result = _validate_policy(df, spec, 0.50, -0.10, 1.0)
        assert result["hit_rate_flagged"] == pytest.approx(0.50)

    def test_avoided_loss_is_abs_sum_of_negative_roi(self):
        # 10 misses at -0.20, 10 hits at +0.10
        rows = [_row("hit", edge=5.0, shadow_roi=0.10)] * 10
        rows += [_row("miss", edge=5.0, shadow_roi=-0.20)] * 10
        df = pd.DataFrame(rows)
        spec = _spec("cap_effective_edge_at_4_percent")
        result = _validate_policy(df, spec, 0.50, -0.10, 1.0)
        assert result["avoided_loss"] == pytest.approx(2.0)     # 10 * 0.20
        assert result["lost_profit"]  == pytest.approx(1.0)     # 10 * 0.10
        assert result["net_effect"]   == pytest.approx(1.0)     # 2.0 - 1.0

    def test_delta_hit_rate_and_delta_roi(self):
        df = self._make_graded_with_roi(n_hit=15, n_miss=25)
        spec = _spec("cap_effective_edge_at_4_percent")
        baseline_hr  = 0.50
        baseline_roi = -0.05
        result = _validate_policy(df, spec, baseline_hr, baseline_roi, 1.0)
        assert result["delta_hit_rate"]  is not None
        assert result["delta_roi"]       is not None
        # flagged hit_rate = 15/40 = 0.375; delta = 0.375 - 0.50 = -0.125
        assert result["delta_hit_rate"] == pytest.approx(-0.125, abs=1e-4)

    def test_insufficient_sample_verdict_when_few_flagged(self):
        # Only 5 rows match spec
        df = pd.DataFrame(
            [_row("hit", edge=7.0)] * 3 + [_row("miss", edge=7.0)] * 2
            + [_row("hit", edge=1.0)] * 30
        )
        spec = _spec("manual_review_for_6plus_edge_any_direction")
        result = _validate_policy(df, spec, 0.50, -0.10, 1.0)
        assert result["verdict"] == "INSUFFICIENT_SAMPLE"
        assert result["sample_warning"] is not None

    def test_promising_verdict(self):
        # 25 rows flagged, hit_rate = 0.40, roi = -0.30
        rows  = [_row("hit",  edge=5.0, shadow_roi=0.10)] * 10
        rows += [_row("miss", edge=5.0, shadow_roi=-0.20)] * 15
        df = pd.DataFrame(rows)
        spec = _spec("cap_effective_edge_at_4_percent")
        result = _validate_policy(df, spec, 0.55, -0.05, 1.0)
        assert result["verdict"] in ("PROMISING", "READY_FOR_FORWARD_SHADOW", "READY_FOR_LIVE_REVIEW_LATER")

    def test_policy_name_and_note_in_result(self):
        df = _make_df(n_hits=20, n_misses=20, edge=5.0)
        spec = _spec("cap_effective_edge_at_4_percent")
        result = _validate_policy(df, spec, 0.50, -0.10, 1.0)
        assert result["policy_name"] == "cap_effective_edge_at_4_percent"
        assert result["note"] == "shadow_validation_only_no_live_logic_changed"

    def test_missing_shadow_roi_column_yields_zero_pnl(self):
        rows = [{"result_status": "hit", "edge": 5.0}] * 10
        rows += [{"result_status": "miss", "edge": 5.0}] * 10
        df = pd.DataFrame(rows)
        spec = _spec("cap_effective_edge_at_4_percent")
        result = _validate_policy(df, spec, 0.50, -0.10, 1.0)
        assert result["avoided_loss"] == 0.0
        assert result["lost_profit"]  == 0.0
        assert result["net_effect"]   == 0.0

    def test_risk_reduction_score_present(self):
        df = _make_df(n_hits=10, n_misses=30, edge=5.0, shadow_roi=-0.15)
        spec = _spec("cap_effective_edge_at_4_percent")
        result = _validate_policy(df, spec, 0.50, -0.05, 1.0)
        assert "risk_reduction_score" in result
        assert isinstance(result["risk_reduction_score"], float)


# ---------------------------------------------------------------------------
# Tests: _forward_track_policy
# ---------------------------------------------------------------------------

class TestForwardTrackPolicy:
    def _make_board(self) -> pd.DataFrame:
        rows = [
            _row(edge=5.0, selection="over", market_type="player_points",
                 player_name="Player A", game_id="G1"),
            _row(edge=5.0, selection="over", market_type="player_points",
                 player_name="Player B", game_id="G2"),
            _row(edge=1.0, selection="over", market_type="player_points",
                 player_name="Player C", game_id="G3"),
        ]
        return pd.DataFrame(rows)

    def test_rows_flagged_count(self):
        spec = _spec("cap_effective_edge_at_4_percent")
        board = self._make_board()
        result = _forward_track_policy(board, spec)
        assert result["current_rows_flagged"] == 2

    def test_players_and_games_listed(self):
        spec = _spec("cap_effective_edge_at_4_percent")
        board = self._make_board()
        result = _forward_track_policy(board, spec)
        assert "Player A" in result["current_players_flagged"]
        assert "Player B" in result["current_players_flagged"]
        assert "G1" in result["current_games_flagged"]

    def test_expected_risk_high_when_5_or_more(self):
        rows = [_row(edge=5.0)] * 6
        board = pd.DataFrame(rows)
        spec = _spec("cap_effective_edge_at_4_percent")
        result = _forward_track_policy(board, spec)
        assert result["current_expected_risk"] == "HIGH"

    def test_expected_risk_moderate_when_2_to_4(self):
        rows = [_row(edge=5.0)] * 3
        board = pd.DataFrame(rows)
        spec = _spec("cap_effective_edge_at_4_percent")
        result = _forward_track_policy(board, spec)
        assert result["current_expected_risk"] == "MODERATE"

    def test_expected_risk_low_when_1(self):
        rows = [_row(edge=5.0)] * 1
        board = pd.DataFrame(rows)
        spec = _spec("cap_effective_edge_at_4_percent")
        result = _forward_track_policy(board, spec)
        assert result["current_expected_risk"] == "LOW"

    def test_empty_board_returns_zero_flagged(self):
        result = _forward_track_policy(pd.DataFrame(), _spec("cap_effective_edge_at_4_percent"))
        assert result["current_rows_flagged"] == 0
        assert result.get("note") == "board_not_available"

    def test_does_not_mutate_board(self):
        board = self._make_board()
        original_len = len(board)
        original_cols = set(board.columns)
        _forward_track_policy(board, _spec("cap_effective_edge_at_4_percent"))
        assert len(board) == original_len
        assert set(board.columns) == original_cols

    def test_shadow_tracking_note_present(self):
        board = self._make_board()
        result = _forward_track_policy(board, _spec("cap_effective_edge_at_4_percent"))
        assert "TRACKING_ONLY" in result.get("shadow_tracking_note", "")


# ---------------------------------------------------------------------------
# Tests: build_edge_containment_shadow_validation
# ---------------------------------------------------------------------------

class TestBuildEdgeContainmentShadowValidation:
    def test_empty_df_returns_empty_report(self):
        result = build_edge_containment_shadow_validation(pd.DataFrame(), DATE)
        assert result["baseline"]["total_graded_rows"] == 0
        assert result["historical_validation"] == {}
        assert result["policy_rankings"] == []

    def test_no_graded_rows_returns_empty_report(self):
        df = pd.DataFrame([_row("pending"), _row("ungraded")])
        result = build_edge_containment_shadow_validation(df, DATE)
        assert result["baseline"]["total_graded_rows"] == 0

    def test_report_version_present(self):
        df = _make_df(n_hits=20, n_misses=20)
        result = build_edge_containment_shadow_validation(df, DATE)
        assert result["report_version"] == REPORT_VERSION

    def test_baseline_keys(self):
        df = _make_df(n_hits=20, n_misses=20)
        result = build_edge_containment_shadow_validation(df, DATE)
        bl = result["baseline"]
        assert "total_graded_rows" in bl
        assert "hit_rate" in bl
        assert "roi" in bl
        assert bl["total_graded_rows"] == 40

    def test_all_policy_specs_have_historical_validation_entry(self):
        df = _make_df(n_hits=20, n_misses=20)
        result = build_edge_containment_shadow_validation(df, DATE)
        for spec in _POLICY_SPECS:
            assert spec["name"] in result["historical_validation"]

    def test_policy_rankings_length_matches_spec_count(self):
        df = _make_df(n_hits=20, n_misses=20)
        result = build_edge_containment_shadow_validation(df, DATE)
        assert len(result["policy_rankings"]) == len(_POLICY_SPECS)

    def test_policy_rankings_have_required_keys(self):
        df = _make_df(n_hits=20, n_misses=20)
        result = build_edge_containment_shadow_validation(df, DATE)
        for row in result["policy_rankings"]:
            assert "rank" in row
            assert "policy_name" in row
            assert "verdict" in row
            assert "n_flagged" in row

    def test_executive_summary_keys(self):
        df = _make_df(n_hits=20, n_misses=20)
        result = build_edge_containment_shadow_validation(df, DATE)
        es = result["executive_summary"]
        assert "total_policies_evaluated" in es
        assert "top_policy" in es
        assert "top_policy_verdict" in es
        assert es["total_policies_evaluated"] == len(_POLICY_SPECS)

    def test_notes_contain_shadow_only(self):
        df = _make_df(n_hits=20, n_misses=20)
        result = build_edge_containment_shadow_validation(df, DATE)
        notes = result.get("notes", [])
        assert any("shadow" in n for n in notes)
        assert any("no_live_logic_changed" in n for n in notes)
        assert any("no_board_rows_removed" in n for n in notes)

    def test_forward_tracking_present_with_board(self):
        df = _make_df(n_hits=20, n_misses=20)
        board = _make_df(n_hits=5, n_misses=0, edge=5.0)
        result = build_edge_containment_shadow_validation(df, DATE, board_df=board)
        ft = result["forward_tracking"]
        assert "cap_effective_edge_at_4_percent" in ft

    def test_forward_tracking_empty_when_no_board(self):
        df = _make_df(n_hits=20, n_misses=20)
        result = build_edge_containment_shadow_validation(df, DATE, board_df=None)
        ft = result["forward_tracking"]
        # Should still have all policies, but flagged = 0 and note = board_not_provided
        for spec in _POLICY_SPECS:
            assert spec["name"] in ft
            assert ft[spec["name"]]["current_rows_flagged"] == 0
            assert ft[spec["name"]].get("note") == "board_not_provided"

    def test_does_not_mutate_input_df(self):
        df = _make_df(n_hits=20, n_misses=20)
        original_len = len(df)
        original_cols = set(df.columns)
        build_edge_containment_shadow_validation(df, DATE)
        assert len(df) == original_len
        assert set(df.columns) == original_cols

    def test_does_not_mutate_board_df(self):
        df = _make_df(n_hits=20, n_misses=20)
        board = _make_df(n_hits=5, n_misses=0, edge=5.0)
        original_board_len = len(board)
        build_edge_containment_shadow_validation(df, DATE, board_df=board)
        assert len(board) == original_board_len

    def test_total_current_flagged_in_executive_summary(self):
        df = _make_df(n_hits=20, n_misses=20)
        board = pd.DataFrame([_row(edge=5.0)] * 3)
        result = build_edge_containment_shadow_validation(df, DATE, board_df=board)
        es = result["executive_summary"]
        assert "total_current_slate_rows_flagged" in es


# ---------------------------------------------------------------------------
# Tests: missing column safety
# ---------------------------------------------------------------------------

class TestMissingColumnSafety:
    def test_no_edge_column_all_masks_false(self):
        rows = [{"result_status": "hit", "shadow_roi": 0.05}] * 30
        rows += [{"result_status": "miss", "shadow_roi": -0.05}] * 10
        df = pd.DataFrame(rows)
        # Should not raise — all policies require edge >= 4 or 6, so all masks are False
        result = build_edge_containment_shadow_validation(df, DATE)
        for spec in _POLICY_SPECS:
            entry = result["historical_validation"][spec["name"]]
            assert entry["rows_flagged"] == 0
            assert entry["verdict"] == "INSUFFICIENT_SAMPLE"

    def test_no_context_column_positive_match_policies_get_zero_flagged(self):
        # Policies that require context == "conflicted" or "aligned" get 0 matches
        # when context_pick_alignment is absent (empty string != "conflicted").
        # Note: require_context_aligned_for_6plus_edge uses "!aligned" (negation),
        # so it correctly flags rows when context is absent — excluded here.
        rows = [{"result_status": "hit", "edge": 7.0, "shadow_roi": -0.30}] * 25
        rows += [{"result_status": "miss", "edge": 7.0, "shadow_roi": -0.30}] * 25
        df = pd.DataFrame(rows)
        result = build_edge_containment_shadow_validation(df, DATE)
        positive_context_policies = [
            "suppress_high_edge_context_conflicted_over",
            "suppress_conflicted_6plus_any_direction",
        ]
        for name in positive_context_policies:
            entry = result["historical_validation"][name]
            assert entry["rows_flagged"] == 0

    def test_nan_edge_rows_excluded_from_flags(self):
        rows = [_row("hit", edge=5.0)] * 10 + [_row("miss", edge=5.0)] * 10
        df = pd.DataFrame(rows)
        df.loc[0, "edge"] = float("nan")
        spec = _spec("cap_effective_edge_at_4_percent")
        result = _validate_policy(df, spec, 0.50, -0.10, 1.0)
        # Row 0 has NaN edge → excluded from flagged
        assert result["rows_flagged"] == 19

    def test_no_market_type_combo_filter_returns_zero(self):
        rows = [{"result_status": "hit", "edge": 5.0, "shadow_roi": 0.05}] * 25
        rows += [{"result_status": "miss", "edge": 5.0, "shadow_roi": -0.10}] * 25
        df = pd.DataFrame(rows)
        combo_specs = [s for s in _POLICY_SPECS if s.get("combo")]
        for spec in combo_specs:
            result = _validate_policy(df, spec, 0.50, -0.10, 1.0)
            assert result["rows_flagged"] == 0


# ---------------------------------------------------------------------------
# Tests: specific policy behaviours
# ---------------------------------------------------------------------------

class TestSpecificPolicies:
    def test_cap_effective_edge_4pct_flags_all_high_edge(self):
        # 30 rows at edge=5.0 (all flagged), 10 rows at edge=2.0 (none flagged)
        rows = [_row("hit", edge=5.0)] * 15 + [_row("miss", edge=5.0)] * 15
        rows += [_row("hit", edge=2.0)] * 10
        df = pd.DataFrame(rows)
        spec = _spec("cap_effective_edge_at_4_percent")
        result = _validate_policy(df, spec, 0.50, -0.10, 1.0)
        assert result["rows_flagged"] == 30

    def test_suppress_high_edge_combo_over_requires_combo_and_over(self):
        combo_mkt = list(_COMBO_MARKETS)[0]
        rows = [
            _row("hit",  edge=5.0, selection="over",  market_type=combo_mkt),    # flagged
            _row("miss", edge=5.0, selection="under", market_type=combo_mkt),    # not over
            _row("miss", edge=5.0, selection="over",  market_type="player_points"),  # not combo
        ]
        df = pd.DataFrame(rows)
        spec = _spec("suppress_high_edge_combo_over")
        mask = _build_mask(df, spec)
        assert list(mask) == [True, False, False]

    def test_require_context_aligned_negation(self):
        rows = [
            _row("hit",  edge=7.0, context_pick_alignment="aligned"),     # aligned → NOT flagged
            _row("miss", edge=7.0, context_pick_alignment="conflicted"),  # not aligned → flagged
            _row("miss", edge=7.0, context_pick_alignment="neutral"),     # not aligned → flagged
        ]
        df = pd.DataFrame(rows)
        spec = _spec("require_context_aligned_for_6plus_edge")
        mask = _build_mask(df, spec)
        assert list(mask) == [False, True, True]

    def test_suppress_6plus_combo_any_direction(self):
        combo_mkt = list(_COMBO_MARKETS)[0]
        rows = [
            _row("miss", edge=7.0, selection="over",  market_type=combo_mkt),   # flagged
            _row("miss", edge=7.0, selection="under", market_type=combo_mkt),   # flagged
            _row("miss", edge=7.0, selection="over",  market_type="player_points"),  # not combo
            _row("miss", edge=3.0, selection="over",  market_type=combo_mkt),   # edge too low
        ]
        df = pd.DataFrame(rows)
        spec = _spec("suppress_6plus_combo_any_direction")
        mask = _build_mask(df, spec)
        assert list(mask) == [True, True, False, False]


# ---------------------------------------------------------------------------
# Tests: write_edge_containment_shadow_validation
# ---------------------------------------------------------------------------

class TestWriteEdgeContainmentShadowValidation:
    def test_writes_json_and_txt(self, tmp_path):
        df = _make_df(n_hits=20, n_misses=20)
        json_path, txt_path, payload = write_edge_containment_shadow_validation(
            df, DATE, runtime_root=str(tmp_path)
        )
        assert json_path.exists()
        assert txt_path.exists()

    def test_json_is_valid(self, tmp_path):
        df = _make_df(n_hits=20, n_misses=20)
        json_path, _, _ = write_edge_containment_shadow_validation(
            df, DATE, runtime_root=str(tmp_path)
        )
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "baseline" in data
        assert "historical_validation" in data

    def test_txt_has_key_sections(self, tmp_path):
        df = _make_df(n_hits=20, n_misses=20)
        _, txt_path, _ = write_edge_containment_shadow_validation(
            df, DATE, runtime_root=str(tmp_path)
        )
        text = txt_path.read_text(encoding="utf-8")
        assert "EXECUTIVE SUMMARY" in text
        assert "HISTORICAL POLICY VALIDATION" in text
        assert "POLICY DETAILS" in text
        assert "CURRENT SLATE FORWARD-TRACKING FLAGS" in text
        assert "CAUTIONS" in text
        assert "No live betting behavior changed" in text

    def test_empty_df_still_writes_files(self, tmp_path):
        json_path, txt_path, payload = write_edge_containment_shadow_validation(
            pd.DataFrame(), DATE, runtime_root=str(tmp_path)
        )
        assert json_path.exists()
        assert txt_path.exists()

    def test_returns_payload_dict(self, tmp_path):
        df = _make_df(n_hits=20, n_misses=20)
        _, _, payload = write_edge_containment_shadow_validation(
            df, DATE, runtime_root=str(tmp_path)
        )
        assert isinstance(payload, dict)
        assert payload["prediction_date"] == DATE

    def test_json_path_in_diagnostics_subdir(self, tmp_path):
        json_path, _, _ = write_edge_containment_shadow_validation(
            pd.DataFrame(), DATE, runtime_root=str(tmp_path)
        )
        assert "diagnostics" in str(json_path)

    def test_txt_path_in_operator_subdir(self, tmp_path):
        _, txt_path, _ = write_edge_containment_shadow_validation(
            pd.DataFrame(), DATE, runtime_root=str(tmp_path)
        )
        assert "operator" in str(txt_path)

    def test_board_df_forwarded_to_forward_tracking(self, tmp_path):
        df = _make_df(n_hits=20, n_misses=20)
        board = pd.DataFrame([_row(edge=5.0, player_name="BoardPlayer")] * 3)
        json_path, _, payload = write_edge_containment_shadow_validation(
            df, DATE, runtime_root=str(tmp_path), board_df=board
        )
        ft = payload.get("forward_tracking", {})
        cap_spec = ft.get("cap_effective_edge_at_4_percent", {})
        assert cap_spec.get("current_rows_flagged", 0) >= 3


# ---------------------------------------------------------------------------
# Tests: path helpers
# ---------------------------------------------------------------------------

class TestPathHelpers:
    def test_json_path_contains_date_and_filename(self):
        p = containment_json_path_for_date("2026-05-10", "outputs/runtime")
        assert "2026-05-10" in str(p)
        assert "edge_containment_shadow_validation" in str(p)
        assert str(p).endswith(".json")

    def test_txt_path_contains_date_and_filename(self):
        p = containment_txt_path_for_date("2026-05-10", "outputs/runtime")
        assert "2026-05-10" in str(p)
        assert "edge_containment_shadow_validation" in str(p)
        assert str(p).endswith(".txt")

    def test_json_path_uses_diagnostics_subdir(self):
        p = containment_json_path_for_date("2026-05-10", "outputs/runtime")
        assert "diagnostics" in str(p)

    def test_txt_path_uses_operator_subdir(self):
        p = containment_txt_path_for_date("2026-05-10", "outputs/runtime")
        assert "operator" in str(p)


# ---------------------------------------------------------------------------
# Tests: quality_summary integration
# ---------------------------------------------------------------------------

class TestQualitySummaryIntegration:
    def _setup(self, tmp_path: Path) -> tuple[Path, Path, str]:
        runtime_root = tmp_path / "outputs" / "runtime"
        operator     = runtime_root / "operator"
        diagnostics  = runtime_root / "diagnostics"
        research     = runtime_root / "research"
        history      = tmp_path / "data" / "history"
        for d in (operator, diagnostics, research, history):
            d.mkdir(parents=True, exist_ok=True)
        date = DATE
        pd.DataFrame([{"prediction_date": date}]).to_csv(
            operator / f"elite_board_{date}.csv", index=False
        )
        pd.DataFrame([{"prediction_date": date}]).to_csv(
            operator / f"full_market_board_{date}.csv", index=False
        )
        pd.DataFrame([]).to_csv(operator / f"kelly_stakes_{date}.csv", index=False)
        (research / f"player_predictions_{date}.csv").write_text("", encoding="utf-8")
        (research / f"model_metrics_{date}.json").write_text("{}", encoding="utf-8")
        (operator / f"elite_pipeline_audit_summary_{date}.json").write_text("{}", encoding="utf-8")
        (diagnostics / f"board_diagnostics_{date}.json").write_text("{}", encoding="utf-8")
        return runtime_root, history, date

    def test_key_present_in_payload(self, tmp_path: Path):
        """quality_summary payload must have edge_containment_shadow_validation key."""
        from courtvision.reporting.quality_summary import write_quality_summary_outputs

        runtime_root, history, date = self._setup(tmp_path)
        _make_df(n_hits=20, n_misses=20).to_csv(
            history / "market_shadow_history.csv", index=False
        )
        _, _, qs_payload = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        assert "edge_containment_shadow_validation" in qs_payload

    def test_payload_key_has_required_fields(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs

        runtime_root, history, date = self._setup(tmp_path)
        _make_df(n_hits=20, n_misses=20).to_csv(
            history / "market_shadow_history.csv", index=False
        )
        _, _, qs_payload = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        ecv = qs_payload["edge_containment_shadow_validation"]
        assert "json_path" in ecv
        assert "txt_path" in ecv
        assert "policy_count" in ecv
        assert "note" in ecv
        assert ecv["note"] == "shadow_validation_only_no_live_logic_changed"

    def test_missing_history_does_not_crash(self, tmp_path: Path):
        """If history CSV is absent, quality_summary must not crash."""
        from courtvision.reporting.quality_summary import write_quality_summary_outputs

        runtime_root, history, date = self._setup(tmp_path)
        # Do NOT write any history files — empty_history dir has no CSVs
        _, _, qs_payload = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        assert "edge_containment_shadow_validation" in qs_payload
