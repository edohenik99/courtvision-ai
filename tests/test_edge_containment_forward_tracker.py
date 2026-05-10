"""
Tests for Phase 12F: Edge Containment Forward Review Outcome Tracker.

Proves:
- Flagged rows are correctly loaded and filtered from review_flags CSVs
- Join to shadow history works on normalised keys
- Outcome metrics are computed correctly (push excluded from hit rate)
- Readiness classification covers all 6 states
- Unmatched/pending/void rows are accounted for separately
- No live logic changed: board unchanged, Kelly unchanged
- quality_summary.py includes edge_containment_forward_tracker key
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.edge_containment_forward_tracker import (
    TRACKER_POLICY_NAME,
    _classify_readiness,
    _compute_outcome_metrics,
    _filter_flagged,
    _join_flags_to_history,
    _normalise_join_keys,
    build_edge_containment_forward_tracker,
    forward_tracker_json_path_for_date,
    forward_tracker_txt_path_for_date,
    write_edge_containment_forward_tracker,
)

DATE = "2026-05-10"
_COMBO = "player_points_assists"
_NON_COMBO = "player_points"


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _flag_row(
    player_name: str = "Alpha",
    market_type: str = _COMBO,
    selection: str = "over",
    line: float = 20.5,
    edge: float = 5.5,
    confidence: float = 0.75,
    context_pick_alignment: str = "conflicted",
    is_elite: bool = False,
    flagged: bool = True,
    prediction_date: str = DATE,
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "edge": edge,
        "confidence": confidence,
        "context_pick_alignment": context_pick_alignment,
        "is_elite": is_elite,
        "edge_containment_review_required": flagged,
        "edge_containment_policy": TRACKER_POLICY_NAME if flagged else "",
        "edge_containment_reason": "high_edge_combo_over_historical_inflation" if flagged else "",
        "edge_containment_recommended_action": "REVIEW_BEFORE_BET" if flagged else "OK_TO_CONSIDER",
        "edge_containment_verdict": "READY_FOR_FORWARD_SHADOW" if flagged else "",
        "edge_containment_note": "review_only_no_live_suppression" if flagged else "",
    }


def _history_row(
    player_name: str = "Alpha",
    market_type: str = _COMBO,
    selection: str = "over",
    line: float = 20.5,
    edge: float = 5.5,
    confidence: float = 0.75,
    result_status: str = "hit",
    actual_value: float = 22.0,
    model_projection: float = 21.5,
    shadow_roi: float = 0.909,
    prediction_date: str = DATE,
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "edge": edge,
        "confidence": confidence,
        "model_projection": model_projection,
        "result_status": result_status,
        "actual_value": actual_value,
        "shadow_roi": shadow_roi,
        "hit": result_status == "hit",
        "miss": result_status == "miss",
        "push": result_status == "push",
    }


# ---------------------------------------------------------------------------
# Tests: _filter_flagged
# ---------------------------------------------------------------------------

class TestFilterFlagged:
    def test_keeps_truthy_rows(self):
        df = pd.DataFrame([
            _flag_row(flagged=True),
            _flag_row(flagged=False),
        ])
        result = _filter_flagged(df)
        assert len(result) == 1
        assert result.iloc[0]["edge_containment_review_required"] == True

    def test_empty_df_returns_empty(self):
        result = _filter_flagged(pd.DataFrame())
        assert result.empty

    def test_no_flag_column_returns_empty(self):
        df = pd.DataFrame([{"player_name": "X"}])
        result = _filter_flagged(df)
        assert result.empty

    def test_string_true_treated_as_truthy(self):
        df = pd.DataFrame([{"edge_containment_review_required": "True", "player_name": "X"}])
        result = _filter_flagged(df)
        assert len(result) == 1

    def test_all_false_returns_empty(self):
        df = pd.DataFrame([_flag_row(flagged=False), _flag_row(flagged=False)])
        result = _filter_flagged(df)
        assert result.empty


# ---------------------------------------------------------------------------
# Tests: _normalise_join_keys
# ---------------------------------------------------------------------------

class TestNormaliseJoinKeys:
    def test_lowercases_player_name(self):
        df = pd.DataFrame([{"player_name": "LeBron James", "line": 20.5}])
        result = _normalise_join_keys(df)
        assert result.iloc[0]["player_name"] == "lebron james"

    def test_strips_whitespace_from_selection(self):
        df = pd.DataFrame([{"selection": "  Over  ", "line": 10.0}])
        result = _normalise_join_keys(df)
        assert result.iloc[0]["selection"] == "over"

    def test_rounds_line_to_two_decimals(self):
        df = pd.DataFrame([{"line": 20.499999}])
        result = _normalise_join_keys(df)
        assert result.iloc[0]["line"] == round(20.499999, 2)

    def test_does_not_mutate_input(self):
        df = pd.DataFrame([{"player_name": "Alpha", "selection": "OVER", "line": 10.0}])
        original_selection = df.iloc[0]["selection"]
        _normalise_join_keys(df)
        assert df.iloc[0]["selection"] == original_selection

    def test_handles_missing_columns_gracefully(self):
        df = pd.DataFrame([{"some_other_col": "x"}])
        result = _normalise_join_keys(df)
        assert "some_other_col" in result.columns


# ---------------------------------------------------------------------------
# Tests: _join_flags_to_history
# ---------------------------------------------------------------------------

class TestJoinFlagsToHistory:
    def test_matched_row_gets_result_status(self):
        flagged = pd.DataFrame([_flag_row()])
        history = pd.DataFrame([_history_row(result_status="hit")])
        joined = _join_flags_to_history(flagged, history)
        assert len(joined) == 1
        assert str(joined.iloc[0]["result_status"]).strip().lower() == "hit"

    def test_unmatched_row_gets_nan_result_status(self):
        flagged = pd.DataFrame([_flag_row(player_name="Alpha")])
        history = pd.DataFrame([_history_row(player_name="Beta")])
        joined = _join_flags_to_history(flagged, history)
        assert len(joined) == 1
        rs = joined.iloc[0].get("result_status", None)
        assert rs is None or str(rs) in {"nan", "NaN", "None", ""} or pd.isna(rs)

    def test_empty_flagged_returns_empty(self):
        flagged = pd.DataFrame()
        history = pd.DataFrame([_history_row()])
        result = _join_flags_to_history(flagged, history)
        assert result.empty

    def test_all_flagged_rows_preserved(self):
        flagged = pd.DataFrame([
            _flag_row(player_name="Alpha"),
            _flag_row(player_name="Gamma"),  # not in history
        ])
        history = pd.DataFrame([_history_row(player_name="Alpha", result_status="miss")])
        joined = _join_flags_to_history(flagged, history)
        assert len(joined) == 2  # left join preserves both

    def test_case_insensitive_join(self):
        flagged = pd.DataFrame([_flag_row(player_name="Alpha", selection="over")])
        history = pd.DataFrame([_history_row(player_name="ALPHA", selection="OVER", result_status="hit")])
        joined = _join_flags_to_history(flagged, history)
        assert len(joined) == 1
        assert str(joined.iloc[0]["result_status"]).strip().lower() == "hit"

    def test_line_float_precision_join(self):
        flagged = pd.DataFrame([_flag_row(line=20.5000001)])
        history = pd.DataFrame([_history_row(line=20.5, result_status="hit")])
        joined = _join_flags_to_history(flagged, history)
        # After rounding to 2 decimals both become 20.5
        rs = joined.iloc[0].get("result_status", "")
        assert str(rs).strip().lower() == "hit"

    def test_does_not_duplicate_columns(self):
        flagged = pd.DataFrame([_flag_row(edge=5.5)])
        history = pd.DataFrame([_history_row(edge=5.5)])
        joined = _join_flags_to_history(flagged, history)
        # No _x/_y duplicate columns for join keys
        for col in joined.columns:
            assert not col.endswith("_x"), f"unexpected duplicate column: {col}"
            assert not col.endswith("_y"), f"unexpected duplicate column: {col}"


# ---------------------------------------------------------------------------
# Tests: _compute_outcome_metrics
# ---------------------------------------------------------------------------

class TestComputeOutcomeMetrics:
    def test_empty_df_returns_zero_counts(self):
        result = _compute_outcome_metrics(pd.DataFrame())
        assert result["n_rows"] == 0
        assert result["n_graded"] == 0
        assert result["hit_rate"] is None
        assert result["avg_roi"] is None

    def test_hit_rate_excludes_push(self):
        # 2 hits, 1 miss, 1 push → hit_rate = 2/3 (not 2/4)
        df = pd.DataFrame([
            {**_history_row(result_status="hit"), **_flag_row()},
            {**_history_row(result_status="hit"), **_flag_row()},
            {**_history_row(result_status="miss"), **_flag_row()},
            {**_history_row(result_status="push"), **_flag_row()},
        ])
        result = _compute_outcome_metrics(df)
        assert result["n_hit"] == 2
        assert result["n_miss"] == 1
        assert result["n_push"] == 1
        assert abs(result["hit_rate"] - 2 / 3) < 0.0001

    def test_hit_rate_all_hit(self):
        df = pd.DataFrame([
            {**_history_row(result_status="hit"), **_flag_row()},
            {**_history_row(result_status="hit"), **_flag_row()},
        ])
        result = _compute_outcome_metrics(df)
        assert result["hit_rate"] == 1.0

    def test_hit_rate_all_miss(self):
        df = pd.DataFrame([
            {**_history_row(result_status="miss", shadow_roi=-1.0), **_flag_row()},
        ])
        result = _compute_outcome_metrics(df)
        assert result["hit_rate"] == 0.0
        assert result["avg_roi"] == pytest.approx(-1.0)

    def test_avg_roi_computed_from_graded_rows(self):
        df = pd.DataFrame([
            {**_history_row(result_status="hit",  shadow_roi=0.9), **_flag_row()},
            {**_history_row(result_status="miss", shadow_roi=-1.0), **_flag_row()},
        ])
        result = _compute_outcome_metrics(df)
        assert result["avg_roi"] == pytest.approx((0.9 + -1.0) / 2, abs=0.0001)

    def test_avg_projection_error(self):
        df = pd.DataFrame([{
            **_history_row(result_status="hit", model_projection=21.0, actual_value=23.0),
            **_flag_row(),
        }])
        result = _compute_outcome_metrics(df)
        assert result["avg_projection_error"] == pytest.approx(2.0)

    def test_pending_rows_excluded_from_graded_count(self):
        df = pd.DataFrame([
            {**_history_row(result_status="pending"), **_flag_row()},
        ])
        result = _compute_outcome_metrics(df)
        assert result["n_graded"] == 0
        assert result["hit_rate"] is None

    def test_void_rows_excluded_from_graded_count(self):
        df = pd.DataFrame([
            {**_history_row(result_status="void"), **_flag_row()},
        ])
        result = _compute_outcome_metrics(df)
        assert result["n_graded"] == 0

    def test_avg_edge_computed(self):
        df = pd.DataFrame([_flag_row(edge=5.5), _flag_row(edge=6.5)])
        result = _compute_outcome_metrics(df)
        assert result["avg_edge"] == pytest.approx(6.0)

    def test_avg_confidence_computed(self):
        df = pd.DataFrame([_flag_row(confidence=0.75), _flag_row(confidence=0.85)])
        result = _compute_outcome_metrics(df)
        assert result["avg_confidence"] == pytest.approx(0.80)


# ---------------------------------------------------------------------------
# Tests: _classify_readiness
# ---------------------------------------------------------------------------

class TestClassifyReadiness:
    def test_insufficient_when_below_10(self):
        assert _classify_readiness(9, 0.30, -0.5, None) == "INSUFFICIENT_FORWARD_SAMPLE"

    def test_tracking_when_10_to_29(self):
        assert _classify_readiness(15, 0.45, -0.05, None) == "TRACKING"

    def test_tracking_when_no_signal(self):
        assert _classify_readiness(30, 0.55, 0.10, 0.50) == "TRACKING"

    def test_warning_signal_low_hit_rate(self):
        assert _classify_readiness(30, 0.44, -0.05, 0.50) == "WARNING_SIGNAL"

    def test_strong_warning_signal_very_low_hit_rate(self):
        assert _classify_readiness(30, 0.34, -0.30, 0.50) == "STRONG_WARNING_SIGNAL"

    def test_ready_for_hold_with_50_graded(self):
        assert _classify_readiness(50, 0.39, -0.15, 0.50) == "READY_FOR_HOLD_REVIEW_CONSIDERATION"

    def test_ready_for_suppression_with_100_graded(self):
        assert _classify_readiness(100, 0.30, -0.25, 0.50) == "READY_FOR_SUPPRESSION_REVIEW_LATER"

    def test_tracking_when_hit_rate_is_none(self):
        assert _classify_readiness(30, None, None, None) == "TRACKING"

    def test_insufficient_at_zero(self):
        assert _classify_readiness(0, None, None, None) == "INSUFFICIENT_FORWARD_SAMPLE"


# ---------------------------------------------------------------------------
# Tests: build_edge_containment_forward_tracker
# ---------------------------------------------------------------------------

class TestBuildEdgeContainmentForwardTracker:

    def _write_flags_csv(self, path: Path, rows: list[dict]) -> None:
        pd.DataFrame(rows).to_csv(path, index=False)

    def _write_history_csv(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(path, index=False)

    def test_no_flags_csv_returns_empty_payload(self, tmp_path: Path):
        operator_dir = tmp_path / "operator"
        operator_dir.mkdir()
        history_csv = tmp_path / "market_shadow_history.csv"
        self._write_history_csv(history_csv, [_history_row()])
        result = build_edge_containment_forward_tracker(
            DATE, operator_dir=operator_dir, history_csv=history_csv
        )
        assert result["total_review_flags"] == 0
        assert result["readiness"] == "INSUFFICIENT_FORWARD_SAMPLE"

    def test_history_not_found_returns_empty_payload(self, tmp_path: Path):
        operator_dir = tmp_path / "operator"
        operator_dir.mkdir()
        self._write_flags_csv(
            operator_dir / f"edge_containment_review_flags_{DATE}.csv",
            [_flag_row()]
        )
        result = build_edge_containment_forward_tracker(
            DATE, operator_dir=operator_dir, history_csv=tmp_path / "nonexistent.csv"
        )
        assert result["total_review_flags"] == 0
        assert "market_shadow_history_not_found" in result["note"]

    def test_counts_all_flagged_rows_across_dates(self, tmp_path: Path):
        operator_dir = tmp_path / "operator"
        operator_dir.mkdir()
        history_csv = tmp_path / "shadow.csv"
        self._write_flags_csv(
            operator_dir / f"edge_containment_review_flags_2026-05-08.csv",
            [_flag_row(prediction_date="2026-05-08"), _flag_row(prediction_date="2026-05-08")]
        )
        self._write_flags_csv(
            operator_dir / f"edge_containment_review_flags_{DATE}.csv",
            [_flag_row(prediction_date=DATE), _flag_row(flagged=False, prediction_date=DATE)]
        )
        # Write a non-empty history CSV (empty CSV would raise EmptyDataError on read)
        self._write_history_csv(history_csv, [_history_row(player_name="NoMatch")])
        result = build_edge_containment_forward_tracker(
            DATE, operator_dir=operator_dir, history_csv=history_csv
        )
        assert result["total_review_flags"] == 3  # 2 + 1 flagged = 3

    def test_unmatched_count_correct(self, tmp_path: Path):
        operator_dir = tmp_path / "operator"
        operator_dir.mkdir()
        history_csv = tmp_path / "shadow.csv"
        self._write_flags_csv(
            operator_dir / f"edge_containment_review_flags_{DATE}.csv",
            [_flag_row(player_name="Alpha"), _flag_row(player_name="Beta")]
        )
        # History only has Alpha
        self._write_history_csv(history_csv, [_history_row(player_name="Alpha")])
        result = build_edge_containment_forward_tracker(
            DATE, operator_dir=operator_dir, history_csv=history_csv
        )
        assert result["total_review_flags"] == 2
        assert result["unmatched_review_flags"] >= 1

    def test_graded_count_hit_miss_push(self, tmp_path: Path):
        operator_dir = tmp_path / "operator"
        operator_dir.mkdir()
        history_csv = tmp_path / "shadow.csv"
        self._write_flags_csv(
            operator_dir / f"edge_containment_review_flags_{DATE}.csv",
            [
                _flag_row(player_name="A"),
                _flag_row(player_name="B"),
                _flag_row(player_name="C"),
            ]
        )
        self._write_history_csv(history_csv, [
            _history_row(player_name="A", result_status="hit"),
            _history_row(player_name="B", result_status="miss"),
            _history_row(player_name="C", result_status="push"),
        ])
        result = build_edge_containment_forward_tracker(
            DATE, operator_dir=operator_dir, history_csv=history_csv
        )
        assert result["graded_review_flags"] == 3
        assert result["n_hit"] == 1
        assert result["n_miss"] == 1
        assert result["n_push"] == 1

    def test_hit_rate_excludes_push_from_denominator(self, tmp_path: Path):
        operator_dir = tmp_path / "operator"
        operator_dir.mkdir()
        history_csv = tmp_path / "shadow.csv"
        self._write_flags_csv(
            operator_dir / f"edge_containment_review_flags_{DATE}.csv",
            [_flag_row(player_name="A"), _flag_row(player_name="B"), _flag_row(player_name="C")]
        )
        self._write_history_csv(history_csv, [
            _history_row(player_name="A", result_status="hit"),
            _history_row(player_name="B", result_status="miss"),
            _history_row(player_name="C", result_status="push"),
        ])
        result = build_edge_containment_forward_tracker(
            DATE, operator_dir=operator_dir, history_csv=history_csv
        )
        # hit=1, miss=1, push excluded → hit_rate = 1/2 = 0.5
        assert result["flagged_hit_rate"] == pytest.approx(0.5)

    def test_pending_rows_in_pending_count(self, tmp_path: Path):
        operator_dir = tmp_path / "operator"
        operator_dir.mkdir()
        history_csv = tmp_path / "shadow.csv"
        self._write_flags_csv(
            operator_dir / f"edge_containment_review_flags_{DATE}.csv",
            [_flag_row(player_name="A")]
        )
        self._write_history_csv(history_csv, [
            _history_row(player_name="A", result_status="pending")
        ])
        result = build_edge_containment_forward_tracker(
            DATE, operator_dir=operator_dir, history_csv=history_csv
        )
        assert result["pending_review_flags"] == 1
        assert result["graded_review_flags"] == 0

    def test_readiness_insufficient_when_no_graded(self, tmp_path: Path):
        operator_dir = tmp_path / "operator"
        operator_dir.mkdir()
        history_csv = tmp_path / "shadow.csv"
        self._write_flags_csv(
            operator_dir / f"edge_containment_review_flags_{DATE}.csv",
            [_flag_row()]
        )
        self._write_history_csv(history_csv, [])
        result = build_edge_containment_forward_tracker(
            DATE, operator_dir=operator_dir, history_csv=history_csv
        )
        assert result["readiness"] == "INSUFFICIENT_FORWARD_SAMPLE"

    def test_policy_name_in_payload(self, tmp_path: Path):
        operator_dir = tmp_path / "operator"
        operator_dir.mkdir()
        history_csv = tmp_path / "shadow.csv"
        self._write_flags_csv(
            operator_dir / f"edge_containment_review_flags_{DATE}.csv",
            [_flag_row()]
        )
        self._write_history_csv(history_csv, [])
        result = build_edge_containment_forward_tracker(
            DATE, operator_dir=operator_dir, history_csv=history_csv
        )
        assert result["policy_name"] == TRACKER_POLICY_NAME

    def test_note_is_forward_tracking_only(self, tmp_path: Path):
        operator_dir = tmp_path / "operator"
        operator_dir.mkdir()
        history_csv = tmp_path / "shadow.csv"
        self._write_flags_csv(
            operator_dir / f"edge_containment_review_flags_{DATE}.csv",
            [_flag_row()]
        )
        self._write_history_csv(history_csv, [])
        result = build_edge_containment_forward_tracker(
            DATE, operator_dir=operator_dir, history_csv=history_csv
        )
        assert "forward_tracking_only_no_live_logic_changed" in result["note"]

    def test_comparison_groups_present(self, tmp_path: Path):
        operator_dir = tmp_path / "operator"
        operator_dir.mkdir()
        history_csv = tmp_path / "shadow.csv"
        self._write_flags_csv(
            operator_dir / f"edge_containment_review_flags_{DATE}.csv",
            [_flag_row()]
        )
        self._write_history_csv(history_csv, [_history_row(result_status="hit")])
        result = build_edge_containment_forward_tracker(
            DATE, operator_dir=operator_dir, history_csv=history_csv
        )
        assert "comparison_groups" in result
        assert isinstance(result["comparison_groups"], dict)

    def test_breakdowns_present(self, tmp_path: Path):
        operator_dir = tmp_path / "operator"
        operator_dir.mkdir()
        history_csv = tmp_path / "shadow.csv"
        self._write_flags_csv(
            operator_dir / f"edge_containment_review_flags_{DATE}.csv",
            [_flag_row()]
        )
        self._write_history_csv(history_csv, [_history_row(result_status="hit")])
        result = build_edge_containment_forward_tracker(
            DATE, operator_dir=operator_dir, history_csv=history_csv
        )
        bd = result["breakdowns"]
        assert "by_market_type" in bd
        assert "by_context_alignment" in bd
        assert "by_edge_bucket" in bd
        assert "by_confidence_bucket" in bd
        assert "rolling" in bd


# ---------------------------------------------------------------------------
# Tests: write_edge_containment_forward_tracker
# ---------------------------------------------------------------------------

class TestWriteEdgeContainmentForwardTracker:

    def _prepare(self, tmp_path: Path):
        runtime_root = tmp_path / "outputs" / "runtime"
        operator_dir = runtime_root / "operator"
        operator_dir.mkdir(parents=True)
        history_csv = tmp_path / "data" / "history" / "market_shadow_history.csv"
        history_csv.parent.mkdir(parents=True)
        pd.DataFrame([_flag_row()]).to_csv(
            operator_dir / f"edge_containment_review_flags_{DATE}.csv", index=False
        )
        pd.DataFrame([_history_row(result_status="hit")]).to_csv(history_csv, index=False)
        return runtime_root, history_csv

    def test_returns_json_and_txt_paths(self, tmp_path: Path):
        runtime_root, history_csv = self._prepare(tmp_path)
        json_path, txt_path, _ = write_edge_containment_forward_tracker(
            DATE, runtime_root=runtime_root, history_csv=history_csv
        )
        assert isinstance(json_path, Path)
        assert isinstance(txt_path, Path)

    def test_json_artifact_written(self, tmp_path: Path):
        runtime_root, history_csv = self._prepare(tmp_path)
        json_path, _, _ = write_edge_containment_forward_tracker(
            DATE, runtime_root=runtime_root, history_csv=history_csv
        )
        assert json_path.exists()
        import json
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert "readiness" in data

    def test_txt_artifact_written(self, tmp_path: Path):
        runtime_root, history_csv = self._prepare(tmp_path)
        _, txt_path, _ = write_edge_containment_forward_tracker(
            DATE, runtime_root=runtime_root, history_csv=history_csv
        )
        assert txt_path.exists()
        text = txt_path.read_text(encoding="utf-8")
        assert "EDGE CONTAINMENT FORWARD REVIEW TRACKER" in text

    def test_txt_contains_readiness(self, tmp_path: Path):
        runtime_root, history_csv = self._prepare(tmp_path)
        _, txt_path, _ = write_edge_containment_forward_tracker(
            DATE, runtime_root=runtime_root, history_csv=history_csv
        )
        text = txt_path.read_text(encoding="utf-8")
        assert "readiness" in text.lower()

    def test_json_in_diagnostics_subdir(self, tmp_path: Path):
        runtime_root, history_csv = self._prepare(tmp_path)
        json_path, _, _ = write_edge_containment_forward_tracker(
            DATE, runtime_root=runtime_root, history_csv=history_csv
        )
        assert "diagnostics" in str(json_path)

    def test_txt_in_operator_subdir(self, tmp_path: Path):
        runtime_root, history_csv = self._prepare(tmp_path)
        _, txt_path, _ = write_edge_containment_forward_tracker(
            DATE, runtime_root=runtime_root, history_csv=history_csv
        )
        assert "operator" in str(txt_path)

    def test_payload_no_flags_df_key(self, tmp_path: Path):
        """Payload must not contain a large DataFrame key (only serialisable values)."""
        runtime_root, history_csv = self._prepare(tmp_path)
        _, _, payload = write_edge_containment_forward_tracker(
            DATE, runtime_root=runtime_root, history_csv=history_csv
        )
        assert "flags_df" not in payload

    def test_no_input_mutation(self, tmp_path: Path):
        runtime_root, history_csv = self._prepare(tmp_path)
        flags_path = runtime_root / "operator" / f"edge_containment_review_flags_{DATE}.csv"
        rows_before = len(pd.read_csv(flags_path))
        write_edge_containment_forward_tracker(
            DATE, runtime_root=runtime_root, history_csv=history_csv
        )
        assert len(pd.read_csv(flags_path)) == rows_before


# ---------------------------------------------------------------------------
# Tests: path helpers
# ---------------------------------------------------------------------------

class TestPathHelpers:
    def test_json_path_contains_date(self):
        p = forward_tracker_json_path_for_date(DATE)
        assert DATE in str(p)
        assert "edge_containment_forward_tracker" in str(p)

    def test_txt_path_contains_date(self):
        p = forward_tracker_txt_path_for_date(DATE)
        assert DATE in str(p)
        assert "edge_containment_forward_tracker" in str(p)

    def test_json_path_in_diagnostics(self):
        p = forward_tracker_json_path_for_date(DATE)
        assert "diagnostics" in str(p)

    def test_txt_path_in_operator(self):
        p = forward_tracker_txt_path_for_date(DATE)
        assert "operator" in str(p)

    def test_custom_runtime_root(self):
        p = forward_tracker_json_path_for_date(DATE, runtime_root="/custom/root")
        # Use forward-slash normalisation for cross-platform path comparison
        assert "custom" in str(p) and "root" in str(p)


# ---------------------------------------------------------------------------
# Tests: quality_summary integration
# ---------------------------------------------------------------------------

class TestQualitySummaryIntegration:
    def _setup(self, tmp_path: Path) -> tuple[Path, Path, str]:
        runtime_root = tmp_path / "outputs" / "runtime"
        operator = runtime_root / "operator"
        diagnostics = runtime_root / "diagnostics"
        research = runtime_root / "research"
        history = tmp_path / "data" / "history"
        for d in (operator, diagnostics, research, history):
            d.mkdir(parents=True, exist_ok=True)
        date = DATE
        _COMBO_MARKET = "player_points_assists"
        board_rows = [
            {
                "prediction_date": date, "player_name": "Alpha",
                "market_type": _COMBO_MARKET, "selection": "over",
                "line": 20.5, "edge": 5.5, "confidence": 0.75,
                "context_pick_alignment": "conflicted", "is_elite": True,
            },
            {
                "prediction_date": date, "player_name": "Beta",
                "market_type": "player_points", "selection": "over",
                "line": 22.5, "edge": 2.1, "confidence": 0.70,
                "context_pick_alignment": "aligned", "is_elite": False,
            },
        ]
        pd.DataFrame([{"prediction_date": date}]).to_csv(
            operator / f"elite_board_{date}.csv", index=False
        )
        pd.DataFrame(board_rows).to_csv(
            operator / f"full_market_board_{date}.csv", index=False
        )
        pd.DataFrame([]).to_csv(operator / f"kelly_stakes_{date}.csv", index=False)
        (research / f"player_predictions_{date}.csv").write_text("", encoding="utf-8")
        (research / f"model_metrics_{date}.json").write_text("{}", encoding="utf-8")
        (operator / f"elite_pipeline_audit_summary_{date}.json").write_text("{}", encoding="utf-8")
        (diagnostics / f"board_diagnostics_{date}.json").write_text("{}", encoding="utf-8")
        return runtime_root, history, date

    def test_key_present_in_payload(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        assert "edge_containment_forward_tracker" in qs

    def test_payload_has_required_keys(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        eft = qs["edge_containment_forward_tracker"]
        for key in (
            "json_path", "txt_path", "total_review_flags",
            "graded_review_flags", "pending_review_flags",
            "readiness", "recommendation", "note",
        ):
            assert key in eft, f"missing key: {key}"

    def test_note_is_forward_tracking_only(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        note = qs["edge_containment_forward_tracker"]["note"]
        assert "forward_tracking_only_no_live_logic_changed" in note

    def test_no_crash_without_shadow_history(self, tmp_path: Path):
        """quality_summary must not crash when shadow history is absent."""
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        assert "edge_containment_forward_tracker" in qs

    def test_readiness_insufficient_when_no_graded(self, tmp_path: Path):
        """With no graded history, readiness must be INSUFFICIENT_FORWARD_SAMPLE."""
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        eft = qs["edge_containment_forward_tracker"]
        assert eft["readiness"] == "INSUFFICIENT_FORWARD_SAMPLE"

    def test_quality_summary_txt_contains_tracker_section(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        text_path, _, _ = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        text = text_path.read_text(encoding="utf-8")
        assert "EDGE CONTAINMENT FORWARD TRACKER" in text
        assert "forward_tracking_only_no_live_logic_changed" in text

    def test_elite_board_row_count_unchanged(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        elite_path = runtime_root / "operator" / f"elite_board_{date}.csv"
        rows_before = len(pd.read_csv(elite_path))
        write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        assert len(pd.read_csv(elite_path)) == rows_before

    def test_full_market_board_row_count_unchanged(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        board_path = runtime_root / "operator" / f"full_market_board_{date}.csv"
        rows_before = len(pd.read_csv(board_path))
        write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        assert len(pd.read_csv(board_path)) == rows_before

    def test_kelly_stakes_unchanged(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        kelly_path = runtime_root / "operator" / f"kelly_stakes_{date}.csv"
        content_before = kelly_path.read_text(encoding="utf-8")
        write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        assert kelly_path.read_text(encoding="utf-8") == content_before
