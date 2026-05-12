"""
Tests for Phase 13B: Player Points Inflation Source Audit.

Covers:
  - projection error calculation
  - over-projection rate calculation
  - selection breakdown (OVER vs UNDER)
  - edge bucket breakdown
  - line bucket breakdown
  - confidence bucket breakdown
  - context alignment breakdown with missing fields
  - player repeated failure detection
  - combo-linked points row matching
  - root cause classification with sufficient fields
  - INSUFFICIENT_FIELDS fallback
  - JSON artifact writing
  - TXT artifact writing
  - quality_summary integration
  - no source mutation
  - no live logic changes
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from courtvision.reporting.player_points_inflation_audit import (
    RC_DNP,
    RC_CONFLICT,
    RC_INJURY,
    RC_HIGH_EDGE,
    RC_USAGE,
    RC_PACE,
    RC_LOW_LINE,
    RC_MINUTES,
    RC_INSUFFICIENT,
    _audit_field_availability,
    _build_gr_lookup,
    _build_records,
    _classify_failure_mode,
    _combo_linked_summary,
    _conf_bucket,
    _ctx_align_bucket,
    _ctx_caution_bucket,
    _edge_bucket,
    _filter_graded,
    _filter_high_edge_combo_overs,
    _identify_combo_linked_keys,
    _line_bucket,
    _minutes_bucket,
    _norm_player,
    _over_projection_rate,
    build_player_points_inflation_audit,
    inflation_audit_json_path_for_date,
    inflation_audit_txt_path_for_date,
    write_player_points_inflation_audit,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pts_row(
    player: str = "Test Player",
    selection: str = "over",
    projection: float = 20.0,
    actual: float = 10.0,
    edge: float = 5.0,
    line: float = 15.0,
    confidence: float = 0.75,
    result: str = "miss",
    date: str = "2026-05-01",
    ctx_align: str | None = None,
    ctx_caution: str | None = None,
) -> dict[str, Any]:
    return {
        "prediction_date":     date,
        "player_name":         player,
        "market_type":         "player_points",
        "selection":           selection,
        "line":                line,
        "model_projection":    projection,
        "actual_value":        actual,
        "edge":                edge,
        "confidence":          confidence,
        "result_status":       result,
        "shadow_roi":          -1.0 if result == "miss" else 0.91,
        "context_pick_alignment":  ctx_align,
        "context_caution_level":   ctx_caution,
        "context_conflict_cause":  None,
        "fragility_score":         None,
    }


def _combo_row(
    player: str = "Test Player",
    market: str = "player_points_assists",
    edge: float = 5.0,
    result: str = "miss",
    date: str = "2026-05-01",
) -> dict[str, Any]:
    return {
        "prediction_date": date,
        "player_name":     player,
        "market_type":     market,
        "selection":       "over",
        "line":            18.0,
        "model_projection": 25.0,
        "actual_value":    10.0,
        "edge":            edge,
        "confidence":      0.75,
        "result_status":   result,
        "shadow_roi":      -1.0,
    }


def _make_sh(*rows: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


# ---------------------------------------------------------------------------
# TestProjectionErrorCalculation
# ---------------------------------------------------------------------------

class TestProjectionErrorCalculation:
    def test_error_is_projection_minus_actual(self):
        sh = _make_sh(_pts_row(projection=20.0, actual=10.0))
        payload = build_player_points_inflation_audit(
            "2026-05-01", history_csv=sh,
        )
        assert payload["avg_points_projection_error"] == pytest.approx(10.0)

    def test_negative_error_when_actual_exceeds_projection(self):
        sh = _make_sh(_pts_row(projection=15.0, actual=25.0, result="hit"))
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["avg_points_projection_error"] == pytest.approx(-10.0)

    def test_zero_error_when_projection_equals_actual(self):
        sh = _make_sh(_pts_row(projection=20.0, actual=20.0, result="push"))
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["avg_points_projection_error"] == pytest.approx(0.0)

    def test_error_none_when_actual_missing(self):
        sh = _make_sh(_pts_row(actual=float("nan")))
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        # No graded rows with valid error means None.
        assert payload["avg_points_projection_error"] is None

    def test_avg_over_multiple_rows(self):
        sh = _make_sh(
            _pts_row(projection=20.0, actual=10.0),         # error=10
            _pts_row(projection=18.0, actual=12.0, player="P2"),  # error=6
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["avg_points_projection_error"] == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# TestOverProjectionRate
# ---------------------------------------------------------------------------

class TestOverProjectionRate:
    def test_rate_1_when_all_over_projected(self):
        sh = _make_sh(
            _pts_row(projection=20.0, actual=10.0),
            _pts_row(projection=18.0, actual=8.0, player="P2"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["over_projection_rate"] == pytest.approx(1.0)

    def test_rate_0_when_all_under_projected(self):
        sh = _make_sh(
            _pts_row(projection=10.0, actual=20.0, result="hit"),
            _pts_row(projection=8.0, actual=18.0, result="hit", player="P2"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["over_projection_rate"] == pytest.approx(0.0)

    def test_rate_mixed(self):
        sh = _make_sh(
            _pts_row(projection=20.0, actual=10.0),          # over-projected
            _pts_row(projection=10.0, actual=20.0, result="hit", player="P2"),  # under-projected
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["over_projection_rate"] == pytest.approx(0.5)

    def test_standalone_function(self):
        rows = [{"projection_error": 5.0}, {"projection_error": -3.0}, {"projection_error": 2.0}]
        assert _over_projection_rate(rows) == pytest.approx(2 / 3, abs=0.01)


# ---------------------------------------------------------------------------
# TestSelectionBreakdown
# ---------------------------------------------------------------------------

class TestSelectionBreakdown:
    def test_over_and_under_separate(self):
        sh = _make_sh(
            _pts_row(selection="over", projection=20.0, actual=10.0),
            _pts_row(selection="under", projection=10.0, actual=18.0, result="hit", player="P2"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert "over" in payload["by_selection"]
        assert "under" in payload["by_selection"]

    def test_over_avg_error_correct(self):
        sh = _make_sh(
            _pts_row(selection="over", projection=20.0, actual=10.0),
            _pts_row(selection="over", projection=18.0, actual=8.0, player="P2"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["by_selection"]["over"]["avg_projection_error"] == pytest.approx(10.0)

    def test_under_hit_rate_correct(self):
        sh = _make_sh(
            _pts_row(selection="under", projection=10.0, actual=8.0, result="hit"),
            _pts_row(selection="under", projection=10.0, actual=15.0, result="miss", player="P2"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["by_selection"]["under"]["hit_rate"] == pytest.approx(0.5)

    def test_over_projection_rate_by_selection(self):
        sh = _make_sh(
            _pts_row(selection="over", projection=20.0, actual=10.0),  # over-projected
            _pts_row(selection="under", projection=8.0, actual=12.0, result="hit", player="P2"),  # under-projected
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["by_selection"]["over"]["over_projection_rate"] == pytest.approx(1.0)
        assert payload["by_selection"]["under"]["over_projection_rate"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# TestEdgeBucketBreakdown
# ---------------------------------------------------------------------------

class TestEdgeBucketBreakdown:
    def test_four_buckets_populated(self):
        sh = _make_sh(
            _pts_row(edge=1.0, player="P1"),
            _pts_row(edge=3.0, player="P2"),
            _pts_row(edge=5.0, player="P3"),
            _pts_row(edge=8.0, player="P4"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        bkts = set(payload["by_edge_bucket"].keys())
        assert "below_2" in bkts
        assert "2_to_4" in bkts
        assert "4_to_6" in bkts
        assert "6_plus" in bkts

    def test_edge_bucket_boundaries(self):
        assert _edge_bucket(0.0) == "below_2"
        assert _edge_bucket(2.0) == "2_to_4"
        assert _edge_bucket(4.0) == "4_to_6"
        assert _edge_bucket(6.0) == "6_plus"

    def test_high_edge_hit_rate_in_payload(self):
        sh = _make_sh(
            _pts_row(edge=7.0, result="miss"),
            _pts_row(edge=8.0, result="hit", actual=25.0, player="P2"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        bkt = payload["by_edge_bucket"].get("6_plus", {})
        assert bkt.get("hit_rate") == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# TestLineBucketBreakdown
# ---------------------------------------------------------------------------

class TestLineBucketBreakdown:
    def test_line_bucket_boundaries(self):
        assert _line_bucket(5.0) == "below_8.5"
        assert _line_bucket(8.5) == "8.5_to_14.5"
        assert _line_bucket(15.0) == "15_to_20.5"
        assert _line_bucket(21.0) == "21_plus"
        assert _line_bucket(None) == "unknown"

    def test_high_line_rows_in_21_plus_bucket(self):
        sh = _make_sh(
            _pts_row(line=23.5, projection=26.0, actual=10.0, player="Star Player"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        bkt = payload["by_line_bucket"].get("21_plus", {})
        assert bkt.get("graded_count") == 1
        assert bkt.get("avg_projection_error") == pytest.approx(16.0)

    def test_low_line_bucket(self):
        sh = _make_sh(_pts_row(line=6.5, result="miss"))
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert "below_8.5" in payload["by_line_bucket"]


# ---------------------------------------------------------------------------
# TestConfidenceBucketBreakdown
# ---------------------------------------------------------------------------

class TestConfidenceBucketBreakdown:
    def test_conf_bucket_boundaries(self):
        assert _conf_bucket(0.60) == "below_0.70"
        assert _conf_bucket(0.70) == "0.70_to_0.80"
        assert _conf_bucket(0.80) == "0.80_plus"
        assert _conf_bucket(None) == "unknown"

    def test_confidence_breakdown_present(self):
        sh = _make_sh(
            _pts_row(confidence=0.65),
            _pts_row(confidence=0.75, player="P2"),
            _pts_row(confidence=0.85, player="P3"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        bkts = set(payload["by_confidence_bucket"].keys())
        assert "below_0.70" in bkts or "0.70_to_0.80" in bkts or "0.80_plus" in bkts


# ---------------------------------------------------------------------------
# TestContextBreakdown
# ---------------------------------------------------------------------------

class TestContextBreakdown:
    def test_context_alignment_breakdown_with_conflicted(self):
        sh = _make_sh(
            _pts_row(ctx_align="conflicted", result="miss"),
            _pts_row(ctx_align="aligned", result="hit", actual=25.0, player="P2"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        ca = payload["by_context_alignment"]
        assert "conflicted" in ca or "aligned" in ca

    def test_unknown_alignment_when_missing(self):
        sh = _make_sh(_pts_row(ctx_align=None))
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert "unknown" in payload["by_context_alignment"]

    def test_ctx_align_bucket_values(self):
        assert _ctx_align_bucket("aligned") == "aligned"
        assert _ctx_align_bucket("conflicted") == "conflicted"
        assert _ctx_align_bucket("neutral") == "neutral"
        assert _ctx_align_bucket(None) == "unknown"
        assert _ctx_align_bucket("other_value") == "unknown"

    def test_ctx_caution_bucket_values(self):
        assert _ctx_caution_bucket("low") == "low"
        assert _ctx_caution_bucket("medium") == "medium"
        assert _ctx_caution_bucket("high") == "high"
        assert _ctx_caution_bucket(None) == "unknown"

    def test_conflicted_rows_have_lower_hit_rate(self):
        sh = _make_sh(
            _pts_row(ctx_align="conflicted", result="miss"),
            _pts_row(ctx_align="conflicted", result="miss", player="P2"),
            _pts_row(ctx_align="aligned", result="hit", actual=25.0, player="P3"),
            _pts_row(ctx_align="aligned", result="hit", actual=22.0, player="P4"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        ca = payload["by_context_alignment"]
        conflict_hr = ca.get("conflicted", {}).get("hit_rate", 1.0)
        aligned_hr  = ca.get("aligned", {}).get("hit_rate", 0.0)
        assert conflict_hr <= aligned_hr


# ---------------------------------------------------------------------------
# TestPlayerRepeatedFailures
# ---------------------------------------------------------------------------

class TestPlayerRepeatedFailures:
    def test_repeat_failure_count_correct(self):
        sh = _make_sh(
            _pts_row("Bad Player", result="miss", date="2026-05-01"),
            _pts_row("Bad Player", result="miss", date="2026-05-02"),
            _pts_row("Bad Player", result="hit", actual=25.0, date="2026-05-03"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        player_data = payload["by_player_top_failures"].get("Bad Player", {})
        assert player_data.get("repeat_failure_count") == 2

    def test_worst_single_error_tracked(self):
        sh = _make_sh(
            _pts_row("Star", projection=30.0, actual=5.0, result="miss"),   # err=25
            _pts_row("Star", projection=20.0, actual=14.0, result="miss", date="2026-05-02"),  # err=6
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        player_data = payload["by_player_top_failures"].get("Star", {})
        assert player_data.get("worst_single_error") == pytest.approx(25.0)

    def test_avg_projection_error_per_player(self):
        sh = _make_sh(
            _pts_row("P1", projection=20.0, actual=10.0, result="miss"),
            _pts_row("P1", projection=18.0, actual=8.0, result="miss", date="2026-05-02"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        p1 = payload["by_player_top_failures"].get("P1", {})
        assert p1.get("avg_projection_error") == pytest.approx(10.0)

    def test_top_failures_sorted_by_error(self):
        sh = _make_sh(
            _pts_row("HighErr", projection=30.0, actual=5.0, result="miss"),
            _pts_row("LowErr", projection=12.0, actual=10.0, result="miss"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        players = list(payload["by_player_top_failures"].keys())
        # HighErr should be first (higher avg error)
        assert players[0] == "HighErr"


# ---------------------------------------------------------------------------
# TestComboLinkedMatching
# ---------------------------------------------------------------------------

class TestComboLinkedMatching:
    def test_combo_linked_keys_identified(self):
        sh = _make_sh(
            _pts_row("Combo Player", result="miss"),
            _combo_row("Combo Player", edge=5.0, result="miss"),
        )
        keys = _identify_combo_linked_keys(sh)
        assert "2026-05-01|combo player" in keys

    def test_non_combo_player_not_linked(self):
        sh = _make_sh(
            _pts_row("Solo Player", result="miss"),
        )
        keys = _identify_combo_linked_keys(sh)
        assert len(keys) == 0

    def test_combo_keys_require_high_edge(self):
        sh = _make_sh(
            _pts_row("Low Edge Player"),
            _combo_row("Low Edge Player", edge=2.0, result="miss"),  # edge < 4
        )
        keys = _identify_combo_linked_keys(sh)
        assert len(keys) == 0

    def test_combo_linked_summary_counts_match(self):
        sh = _make_sh(
            _pts_row("PRA Player", result="miss", date="2026-05-01"),
            _combo_row("PRA Player", edge=5.0, result="miss"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        cs = payload["combo_linked_points_summary"]
        assert cs.get("total_linked_rows") >= 1

    def test_linked_combo_failure_count_in_player_breakdown(self):
        sh = _make_sh(
            _pts_row("Linked", result="miss"),
            _combo_row("Linked", edge=5.0, result="miss"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        player = payload["by_player_top_failures"].get("Linked", {})
        assert player.get("linked_combo_failure_count") >= 1


# ---------------------------------------------------------------------------
# TestRootCauseClassification
# ---------------------------------------------------------------------------

class TestRootCauseClassification:
    def test_dnp_when_actual_zero(self):
        rec = {"result_status": "miss", "actual_value": 0.0, "edge": 5.0,
               "line": 15.0, "context_pick_alignment": None, "context_caution_level": None,
               "injury_projection_delta": None, "form_ratio": None, "pace_context_signal": None,
               "minutes_avg": None}
        assert _classify_failure_mode(rec) == RC_DNP

    def test_dnp_when_actual_equals_threshold(self):
        rec = {"result_status": "miss", "actual_value": 2.0, "edge": 5.0,
               "line": 15.0, "context_pick_alignment": None, "context_caution_level": None,
               "injury_projection_delta": None, "form_ratio": None, "pace_context_signal": None,
               "minutes_avg": None}
        assert _classify_failure_mode(rec) == RC_DNP

    def test_conflict_when_context_conflicted(self):
        rec = {"result_status": "miss", "actual_value": 8.0, "edge": 3.0,
               "line": 12.0, "context_pick_alignment": "conflicted",
               "context_caution_level": "medium",
               "injury_projection_delta": None, "form_ratio": None,
               "pace_context_signal": None, "minutes_avg": None}
        assert _classify_failure_mode(rec) == RC_CONFLICT

    def test_injury_when_injection_delta_large(self):
        rec = {"result_status": "miss", "actual_value": 5.0, "edge": 3.0,
               "line": 12.0, "context_pick_alignment": None, "context_caution_level": None,
               "injury_projection_delta": 2.5, "form_ratio": None,
               "pace_context_signal": None, "minutes_avg": None}
        assert _classify_failure_mode(rec) == RC_INJURY

    def test_high_edge_false_positive(self):
        rec = {"result_status": "miss", "actual_value": 5.0, "edge": 7.0,
               "line": 12.0, "context_pick_alignment": None, "context_caution_level": None,
               "injury_projection_delta": None, "form_ratio": None,
               "pace_context_signal": None, "minutes_avg": None}
        assert _classify_failure_mode(rec) == RC_HIGH_EDGE

    def test_usage_inflation_when_form_high(self):
        rec = {"result_status": "miss", "actual_value": 5.0, "edge": 3.0,
               "line": 12.0, "context_pick_alignment": None, "context_caution_level": None,
               "injury_projection_delta": None, "form_ratio": 1.15,
               "pace_context_signal": None, "minutes_avg": None}
        assert _classify_failure_mode(rec) == RC_USAGE

    def test_pace_inflation_when_supports_over(self):
        rec = {"result_status": "miss", "actual_value": 8.0, "edge": 3.0,
               "line": 12.0, "context_pick_alignment": None, "context_caution_level": None,
               "injury_projection_delta": None, "form_ratio": None,
               "pace_context_signal": "supports_over", "minutes_avg": None}
        assert _classify_failure_mode(rec) == RC_PACE

    def test_low_line_when_line_below_8_5(self):
        rec = {"result_status": "miss", "actual_value": 5.0, "edge": 3.0,
               "line": 6.0, "context_pick_alignment": None, "context_caution_level": None,
               "injury_projection_delta": None, "form_ratio": None,
               "pace_context_signal": None, "minutes_avg": None}
        assert _classify_failure_mode(rec) == RC_LOW_LINE

    def test_insufficient_when_no_signals(self):
        rec = {"result_status": "miss", "actual_value": 8.0, "edge": 3.0,
               "line": 12.0, "context_pick_alignment": None, "context_caution_level": None,
               "injury_projection_delta": None, "form_ratio": None,
               "pace_context_signal": None, "minutes_avg": None}
        assert _classify_failure_mode(rec) == RC_INSUFFICIENT

    def test_not_a_failure_for_hit(self):
        rec = {"result_status": "hit", "actual_value": 25.0, "edge": 5.0,
               "line": 15.0, "context_pick_alignment": None, "context_caution_level": None,
               "injury_projection_delta": None, "form_ratio": None,
               "pace_context_signal": None, "minutes_avg": None}
        assert _classify_failure_mode(rec) == "NOT_A_FAILURE"

    def test_dnp_takes_priority_over_conflict(self):
        rec = {"result_status": "miss", "actual_value": 0.0, "edge": 3.0,
               "line": 12.0, "context_pick_alignment": "conflicted",
               "context_caution_level": "medium",
               "injury_projection_delta": None, "form_ratio": None,
               "pace_context_signal": None, "minutes_avg": None}
        assert _classify_failure_mode(rec) == RC_DNP


# ---------------------------------------------------------------------------
# TestRootCauseSummary
# ---------------------------------------------------------------------------

class TestRootCauseSummary:
    def test_root_cause_counts_in_payload(self):
        sh = _make_sh(
            _pts_row(actual=0.0, result="miss"),  # DNP
            _pts_row(actual=5.0, result="miss", player="P2"),  # INSUFFICIENT
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        rc = payload["root_cause_summary"]
        assert "root_cause_counts" in rc
        assert rc.get("total_failures_analyzed") >= 1

    def test_dominant_failure_mode_in_payload(self):
        sh = _make_sh(
            _pts_row(actual=0.0, result="miss"),
            _pts_row(actual=0.0, result="miss", player="P2"),
            _pts_row(actual=5.0, result="miss", player="P3"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["dominant_failure_mode"] == RC_DNP

    def test_pct_fields_sum_to_one_approx(self):
        sh = _make_sh(
            _pts_row(actual=0.0, result="miss"),
            _pts_row(actual=5.0, result="miss", player="P2"),
            _pts_row(ctx_align="conflicted", ctx_caution="medium", actual=8.0, result="miss", player="P3"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        rc = payload["root_cause_summary"]
        total_pct = sum(
            rc.get(k) or 0 for k in [
                "pct_dnp_or_low_minutes", "pct_context_conflict", "pct_injury_role",
                "pct_high_edge_false_positive", "pct_usage_form", "pct_pace_matchup",
                "pct_low_line_upside", "pct_minutes", "pct_insufficient",
            ]
        )
        assert total_pct == pytest.approx(1.0, abs=0.01)


# ---------------------------------------------------------------------------
# TestJsonArtifactWriting
# ---------------------------------------------------------------------------

class TestJsonArtifactWriting:
    def test_json_artifact_created(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_csv(hist, [_pts_row(result="miss")])
        jpath, _, _ = write_player_points_inflation_audit(
            "2026-05-01", runtime_root=tmp_path / "rt", history_csv=hist,
        )
        assert jpath.exists()

    def test_json_is_valid(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_csv(hist, [_pts_row()])
        jpath, _, _ = write_player_points_inflation_audit(
            "2026-05-01", runtime_root=tmp_path / "rt", history_csv=hist,
        )
        data = json.loads(jpath.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_json_required_keys(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_csv(hist, [_pts_row()])
        jpath, _, _ = write_player_points_inflation_audit(
            "2026-05-01", runtime_root=tmp_path / "rt", history_csv=hist,
        )
        data = json.loads(jpath.read_text(encoding="utf-8"))
        for k in [
            "prediction_date", "note", "total_player_points_rows",
            "graded_player_points_rows", "avg_points_projection_error",
            "over_projection_rate", "dominant_failure_mode",
            "root_cause_summary", "by_selection", "by_edge_bucket",
            "by_line_bucket", "by_confidence_bucket", "by_context_alignment",
            "by_player_top_failures", "combo_linked_points_summary",
            "field_availability",
        ]:
            assert k in data, f"Missing key: {k}"

    def test_json_note_is_audit_only(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_csv(hist, [_pts_row()])
        jpath, _, _ = write_player_points_inflation_audit(
            "2026-05-01", runtime_root=tmp_path / "rt", history_csv=hist,
        )
        data = json.loads(jpath.read_text(encoding="utf-8"))
        assert data["note"] == "audit_only_no_model_change"

    def test_json_path_in_diagnostics(self, tmp_path: Path):
        p = inflation_audit_json_path_for_date("2026-05-01", tmp_path)
        assert "diagnostics" in str(p)
        assert "2026-05-01" in str(p)


# ---------------------------------------------------------------------------
# TestTxtArtifactWriting
# ---------------------------------------------------------------------------

class TestTxtArtifactWriting:
    def test_txt_artifact_created(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_csv(hist, [_pts_row(result="miss")])
        _, tpath, _ = write_player_points_inflation_audit(
            "2026-05-01", runtime_root=tmp_path / "rt", history_csv=hist,
        )
        assert tpath.exists()

    def test_txt_contains_header(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_csv(hist, [_pts_row()])
        _, tpath, _ = write_player_points_inflation_audit(
            "2026-05-01", runtime_root=tmp_path / "rt", history_csv=hist,
        )
        assert "PLAYER POINTS INFLATION" in tpath.read_text(encoding="utf-8")

    def test_txt_answers_diagnostic_questions(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_csv(hist, [_pts_row(result="miss")])
        _, tpath, _ = write_player_points_inflation_audit(
            "2026-05-01", runtime_root=tmp_path / "rt", history_csv=hist,
        )
        content = tpath.read_text(encoding="utf-8")
        assert "Is inflation isolated to OVERs?" in content
        assert "concentrated in high-edge rows?" in content
        assert "concentrated in specific line ranges?" in content

    def test_txt_contains_root_cause_section(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_csv(hist, [_pts_row(actual=0.0, result="miss")])
        _, tpath, _ = write_player_points_inflation_audit(
            "2026-05-01", runtime_root=tmp_path / "rt", history_csv=hist,
        )
        assert "ROOT CAUSE" in tpath.read_text(encoding="utf-8")

    def test_txt_contains_selection_breakdown(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_csv(hist, [_pts_row(), _pts_row(selection="under", player="P2")])
        _, tpath, _ = write_player_points_inflation_audit(
            "2026-05-01", runtime_root=tmp_path / "rt", history_csv=hist,
        )
        assert "BY SELECTION" in tpath.read_text(encoding="utf-8")

    def test_txt_contains_field_availability(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_csv(hist, [_pts_row()])
        _, tpath, _ = write_player_points_inflation_audit(
            "2026-05-01", runtime_root=tmp_path / "rt", history_csv=hist,
        )
        assert "FIELD AVAILABILITY" in tpath.read_text(encoding="utf-8")

    def test_txt_path_in_operator_dir(self, tmp_path: Path):
        p = inflation_audit_txt_path_for_date("2026-05-01", tmp_path)
        assert "operator" in str(p)


# ---------------------------------------------------------------------------
# TestQualitySummaryIntegration
# ---------------------------------------------------------------------------

class TestQualitySummaryIntegration:
    def test_payload_has_note(self):
        sh = _make_sh(_pts_row(result="miss"))
        _, _, payload = write_player_points_inflation_audit.__wrapped__(
            "2026-05-01", history_csv=sh
        ) if hasattr(write_player_points_inflation_audit, "__wrapped__") else (None, None, build_player_points_inflation_audit("2026-05-01", history_csv=sh))
        assert payload["note"] == "audit_only_no_model_change"

    def test_json_readable(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_csv(hist, [_pts_row(result="miss"), _pts_row(result="hit", actual=25.0, player="P2")])
        jpath, _, _ = write_player_points_inflation_audit(
            "2026-05-01", runtime_root=tmp_path / "rt", history_csv=hist,
        )
        data = json.loads(jpath.read_text(encoding="utf-8"))
        assert data["prediction_date"] == "2026-05-01"

    def test_all_required_payload_keys_present(self):
        sh = _make_sh(_pts_row(result="miss"), _pts_row(result="hit", actual=25.0, player="P2"))
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        required = [
            "total_player_points_rows", "graded_player_points_rows",
            "avg_points_projection_error", "over_projection_rate",
            "dominant_failure_mode", "root_cause_summary",
            "by_selection", "by_edge_bucket", "by_line_bucket",
            "by_confidence_bucket", "by_context_alignment",
            "by_player_top_failures", "combo_linked_points_summary",
            "field_availability", "note",
        ]
        for k in required:
            assert k in payload, f"Missing key: {k}"


# ---------------------------------------------------------------------------
# TestNoSourceMutation
# ---------------------------------------------------------------------------

class TestNoSourceMutation:
    def test_shadow_history_not_mutated(self):
        sh = _make_sh(
            _pts_row(projection=20.0, actual=10.0, result="miss"),
            _pts_row(projection=15.0, actual=20.0, result="hit", player="P2"),
        )
        orig_cols   = list(sh.columns)
        orig_len    = len(sh)
        orig_projs  = sh["model_projection"].tolist()

        build_player_points_inflation_audit("2026-05-01", history_csv=sh)

        assert list(sh.columns) == orig_cols
        assert len(sh) == orig_len
        assert sh["model_projection"].tolist() == orig_projs

    def test_edge_values_not_changed(self):
        sh = _make_sh(
            _pts_row(edge=6.5, result="miss"),
            _pts_row(edge=3.2, result="hit", actual=22.0, player="P2"),
        )
        orig_edges = sh["edge"].tolist()
        build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert sh["edge"].tolist() == orig_edges

    def test_history_csv_not_overwritten(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_csv(hist, [_pts_row(result="miss")])
        mtime_before = hist.stat().st_mtime
        write_player_points_inflation_audit(
            "2026-05-01", runtime_root=tmp_path / "rt", history_csv=hist,
        )
        assert hist.stat().st_mtime == mtime_before


# ---------------------------------------------------------------------------
# TestNoLiveLogicChanged
# ---------------------------------------------------------------------------

class TestNoLiveLogicChanged:
    def test_module_does_not_import_kelly_or_pipeline(self):
        import importlib
        mod = importlib.import_module(
            "courtvision.reporting.player_points_inflation_audit"
        )
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for forbidden in ["run_kelly_stakes", "kelly_runner", "elite_gate", "model_formula"]:
            assert forbidden not in src

    def test_payload_note_confirms_audit_only(self):
        sh = _make_sh(_pts_row())
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["note"] == "audit_only_no_model_change"

    def test_no_writes_to_shadow_history(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_csv(hist, [_pts_row()])
        mtime_before = hist.stat().st_mtime
        write_player_points_inflation_audit(
            "2026-05-01", runtime_root=tmp_path / "rt", history_csv=hist,
        )
        assert hist.stat().st_mtime == mtime_before


# ---------------------------------------------------------------------------
# TestFieldAvailabilityAudit
# ---------------------------------------------------------------------------

class TestFieldAvailabilityAudit:
    def test_absent_fields_reported(self):
        sh = _make_sh(_pts_row())
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        fa = payload["field_availability"]
        # usage_rate is always absent
        assert "absent" in str(fa.get("usage_rate", "")).lower()

    def test_present_fields_reported(self):
        sh = _make_sh(_pts_row())
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        fa = payload["field_availability"]
        # edge and confidence are always present
        assert "available" in str(fa.get("edge_sh", "")).lower() or fa.get("edge_sh") is not None

    def test_field_availability_is_dict(self):
        sh = _make_sh(_pts_row())
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert isinstance(payload["field_availability"], dict)

    def test_field_group_status_reports_insufficient_minutes_actuals(self):
        sh = _make_sh(_pts_row())
        payload = build_player_points_inflation_audit(
            "2026-05-01",
            history_csv=sh,
            pick_history_csv=pd.DataFrame(),
            full_market_glob=None,
            elite_board_glob=None,
        )
        fa = payload["field_availability"]
        assert fa["actual_minutes"] == "absent_not_available_in_any_source"
        assert "field_group_status" in fa


# ---------------------------------------------------------------------------
# TestConservativeMatchingAndVerdict
# ---------------------------------------------------------------------------

class TestConservativeMatchingAndVerdict:
    def test_board_enrichment_uses_conservative_row_keys(self, tmp_path: Path):
        board = tmp_path / "full_market_board_2026-05-01.csv"
        _write_csv(board, [{
            **_pts_row("Matched Player", date="2026-05-01"),
            "player_id": "p-1",
            "game_id": "g-1",
            "minutes_avg": 34.0,
            "minutes_bucket": "high",
            "context_pick_alignment": "aligned",
            "injury_projection_delta": 2.2,
        }])
        sh = _make_sh({
            **_pts_row("Matched Player", date="2026-05-01"),
            "player_id": "p-1",
            "game_id": "g-1",
        })
        payload = build_player_points_inflation_audit(
            "2026-05-01",
            history_csv=sh,
            pick_history_csv=pd.DataFrame(),
            full_market_glob=str(tmp_path / "full_market_board_*.csv"),
            elite_board_glob=None,
        )
        assert payload["by_minutes_bucket"]["high"]["graded_count"] == 1
        assert payload["by_context_alignment"]["aligned"]["graded_count"] == 1

    def test_combo_linking_uses_failed_combo_overs_only(self):
        sh = _make_sh(
            _pts_row("Combo Hit Player", result="miss"),
            _combo_row("Combo Hit Player", edge=5.0, result="hit"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["combo_linked_points_summary"]["total_linked_rows"] == 0

    def test_readiness_verdict_present(self):
        sh = _make_sh(_pts_row(actual=0.0, result="miss"))
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["readiness_verdict"] == "DNP_LOW_MINUTES_FAILURE_CONFIRMED"


# ---------------------------------------------------------------------------
# TestEmptyDataHandling
# ---------------------------------------------------------------------------

class TestEmptyDataHandling:
    def test_empty_history_returns_payload(self):
        payload = build_player_points_inflation_audit(
            "2026-05-01", history_csv=pd.DataFrame(),
        )
        assert payload["total_player_points_rows"] == 0
        assert payload["note"] == "audit_only_no_model_change"

    def test_no_player_points_rows(self):
        sh = _make_sh({
            "prediction_date": "2026-05-01", "player_name": "X",
            "market_type": "player_points_assists", "selection": "over",
            "line": 18.0, "model_projection": 25.0, "actual_value": 10.0,
            "edge": 5.0, "confidence": 0.75, "result_status": "miss",
        })
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["total_player_points_rows"] == 0

    def test_pending_rows_excluded_from_graded(self):
        sh = _make_sh(
            _pts_row(result="pending"),
            _pts_row(result="miss", player="P2"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["graded_player_points_rows"] == 1

    def test_void_rows_excluded_from_graded(self):
        sh = _make_sh(
            _pts_row(result="void"),
            _pts_row(result="hit", actual=25.0, player="P2"),
        )
        payload = build_player_points_inflation_audit("2026-05-01", history_csv=sh)
        assert payload["graded_player_points_rows"] == 1
