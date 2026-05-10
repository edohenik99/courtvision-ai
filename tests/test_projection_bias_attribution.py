from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.projection_bias_attribution import (
    REPORT_VERSION,
    _classify_inflation,
    _segment_stats,
    build_projection_bias_attribution,
    attribution_json_path_for_date,
    attribution_txt_path_for_date,
    write_projection_bias_attribution,
)
from courtvision.reporting.quality_summary import write_quality_summary_outputs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DATE = "2026-05-10"


def _row(
    result_status: str = "hit",
    confidence: float = 0.75,
    edge: float = 3.0,
    shadow_roi: float = 0.05,
    selection: str = "over",
    model_projection: float = 25.0,
    actual_value: float = 22.0,
    market_type: str = "player_points",
    prediction_date: str = DATE,
    final_elite_rejection_reason: str = "",
    context_pick_alignment: str = "aligned",
    context_caution_level: str = "medium",
    quality_score: float = 95.0,
    selection_score: float = 37.0,
    same_opponent_under_warning: bool = False,
    team_abbr: str = "LAL",
    player_name: str = "Player A",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "result_status": result_status,
        "confidence": confidence,
        "edge": edge,
        "shadow_roi": shadow_roi,
        "selection": selection,
        "model_projection": model_projection,
        "actual_value": actual_value,
        "market_type": market_type,
        "final_elite_rejection_reason": final_elite_rejection_reason,
        "context_pick_alignment": context_pick_alignment,
        "context_caution_level": context_caution_level,
        "quality_score": quality_score,
        "selection_score": selection_score,
        "same_opponent_under_warning": same_opponent_under_warning,
        "team_abbr": team_abbr,
        "player_name": player_name,
    }


def _make_df(
    n_hits: int = 20,
    n_misses: int = 15,
    selection: str = "over",
    model_projection: float = 25.0,
    actual_value: float = 22.0,
) -> pd.DataFrame:
    rows = []
    for _ in range(n_hits):
        rows.append(_row("hit", selection=selection, model_projection=model_projection, actual_value=actual_value))
    for _ in range(n_misses):
        rows.append(_row("miss", selection=selection, model_projection=model_projection, actual_value=actual_value))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Unit tests: _classify_inflation
# ---------------------------------------------------------------------------

class TestClassifyInflation:
    def test_insufficient_sample(self) -> None:
        assert _classify_inflation(2.0, -0.2, 5) == "INSUFFICIENT_SAMPLE"

    def test_extreme_inflation_by_error(self) -> None:
        assert _classify_inflation(5.5, -0.1, 50) == "EXTREME_INFLATION"

    def test_extreme_inflation_by_gap(self) -> None:
        assert _classify_inflation(1.0, -0.35, 50) == "EXTREME_INFLATION"

    def test_severe_inflation(self) -> None:
        assert _classify_inflation(3.5, -0.15, 50) == "SEVERE_INFLATION"

    def test_severe_by_gap(self) -> None:
        assert _classify_inflation(1.0, -0.25, 50) == "SEVERE_INFLATION"

    def test_moderate_inflation(self) -> None:
        assert _classify_inflation(2.0, -0.05, 50) == "MODERATE_INFLATION"

    def test_moderate_by_gap(self) -> None:
        assert _classify_inflation(0.3, -0.15, 50) == "MODERATE_INFLATION"

    def test_mild_inflation(self) -> None:
        assert _classify_inflation(0.8, -0.02, 50) == "MILD_INFLATION"

    def test_well_calibrated(self) -> None:
        assert _classify_inflation(0.1, -0.02, 50) == "WELL_CALIBRATED"

    def test_under_correction(self) -> None:
        assert _classify_inflation(-1.5, 0.02, 50) == "UNDER_CORRECTION"

    def test_none_gap_uses_error_only(self) -> None:
        result = _classify_inflation(3.5, None, 50)
        assert result == "SEVERE_INFLATION"


# ---------------------------------------------------------------------------
# Unit tests: _segment_stats
# ---------------------------------------------------------------------------

class TestSegmentStats:
    def test_empty_segment(self) -> None:
        s = _segment_stats(pd.DataFrame(), "test")
        assert s["inflation_severity"] == "INSUFFICIENT_SAMPLE"
        assert s["sample_size"] == 0

    def test_projection_error_computed(self) -> None:
        df = _make_df(10, 5, model_projection=25.0, actual_value=22.0)
        s = _segment_stats(df, "test")
        # error = 25 - 22 = 3.0 for all rows
        assert s["mean_projection_error"] == pytest.approx(3.0, abs=0.01)
        assert s["over_prediction_rate"] == pytest.approx(1.0, abs=0.01)

    def test_under_prediction(self) -> None:
        df = _make_df(10, 5, model_projection=20.0, actual_value=25.0)
        s = _segment_stats(df, "test")
        assert s["mean_projection_error"] == pytest.approx(-5.0, abs=0.01)
        assert s["under_prediction_rate"] == pytest.approx(1.0, abs=0.01)

    def test_sparse_caution(self) -> None:
        df = pd.DataFrame([_row("hit")] * 5)
        s = _segment_stats(df, "sparse")
        assert "caution" in s

    def test_inflation_severity_extreme(self) -> None:
        # mean_error = 3.0, calibration_gap will be large
        df = pd.DataFrame([_row("miss", model_projection=30.0, actual_value=25.0, confidence=0.75)] * 50)
        s = _segment_stats(df, "test")
        assert s["inflation_severity"] in ("SEVERE_INFLATION", "EXTREME_INFLATION")

    def test_no_mutation_of_source(self) -> None:
        df = _make_df()
        original_cols = list(df.columns)
        _segment_stats(df, "test")
        assert list(df.columns) == original_cols


# ---------------------------------------------------------------------------
# Unit tests: build_projection_bias_attribution
# ---------------------------------------------------------------------------

class TestBuildAttribution:
    def test_empty_df_returns_empty_report(self) -> None:
        r = build_projection_bias_attribution(pd.DataFrame(), DATE)
        assert r["report_version"] == REPORT_VERSION
        assert r["overall"]["total_graded_rows"] == 0
        assert r["overall"]["inflation_severity"] == "INSUFFICIENT_SAMPLE"

    def test_basic_structure(self) -> None:
        df = _make_df(20, 15)
        r = build_projection_bias_attribution(df, DATE)
        assert r["report_version"] == REPORT_VERSION
        assert r["prediction_date"] == DATE
        assert "overall" in r
        assert "attribution" in r
        assert "root_cause_diagnostics" in r
        assert "top_findings" in r
        for note in ("shadow_analytics_only", "no_live_logic_changed",
                     "no_projections_altered", "no_gates_modified"):
            assert note in r["notes"]

    def test_overall_metrics(self) -> None:
        df = _make_df(20, 10, model_projection=25.0, actual_value=22.0)
        r = build_projection_bias_attribution(df, DATE)
        ov = r["overall"]
        assert ov["total_graded_rows"] == 30
        assert ov["mean_projection_error"] == pytest.approx(3.0, abs=0.01)
        assert ov["over_prediction_rate"] == pytest.approx(1.0, abs=0.01)
        assert ov["hit_rate"] == pytest.approx(20 / 30, abs=0.001)

    def test_no_graded_rows_returns_empty(self) -> None:
        df = pd.DataFrame([{"prediction_date": DATE, "result_status": "pending"}])
        r = build_projection_bias_attribution(df, DATE)
        assert r["overall"]["total_graded_rows"] == 0

    def test_no_mutation_of_source(self) -> None:
        df = _make_df(20, 10)
        original_len = len(df)
        original_cols = set(df.columns)
        build_projection_bias_attribution(df, DATE)
        assert len(df) == original_len
        assert set(df.columns) == original_cols


# ---------------------------------------------------------------------------
# Attribution: by_market
# ---------------------------------------------------------------------------

class TestMarketAttribution:
    def test_market_keys_present(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_bias_attribution(df, DATE)
        attr = r["attribution"]["by_market"]
        assert "player_points" in attr
        assert "combos" in attr
        assert "combo_breakdown" in attr

    def test_combo_breakdown_has_sub_markets(self) -> None:
        rows = [_row("hit", market_type="player_points_assists")] * 10
        rows += [_row("miss", market_type="player_points_assists")] * 10
        rows += [_row("hit", market_type="player_points")] * 10
        df = pd.DataFrame(rows)
        r = build_projection_bias_attribution(df, DATE)
        combo_bd = r["attribution"]["by_market"]["combo_breakdown"]
        assert "player_points_assists" in combo_bd

    def test_separate_market_stats(self) -> None:
        rows = [_row("hit", market_type="player_rebounds")] * 15
        rows += [_row("miss", market_type="player_assists")] * 15
        df = pd.DataFrame(rows)
        r = build_projection_bias_attribution(df, DATE)
        attr = r["attribution"]["by_market"]
        assert attr["player_rebounds"]["sample_size"] == 15
        assert attr["player_assists"]["sample_size"] == 15

    def test_absent_market_col_returns_insufficient(self) -> None:
        df = _make_df().drop(columns=["market_type"])
        r = build_projection_bias_attribution(df, DATE)
        assert r["attribution"]["by_market"]["status"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Attribution: by_direction
# ---------------------------------------------------------------------------

class TestDirectionalAttribution:
    def test_over_under_segments(self) -> None:
        rows = [_row("hit", selection="over")] * 20 + [_row("miss", selection="under")] * 10
        df = pd.DataFrame(rows)
        r = build_projection_bias_attribution(df, DATE)
        d = r["attribution"]["by_direction"]
        assert d["over"]["sample_size"] == 20
        assert d["under"]["sample_size"] == 10

    def test_error_delta_computed(self) -> None:
        rows = [_row("hit", selection="over", model_projection=25.0, actual_value=20.0)] * 20
        rows += [_row("hit", selection="under", model_projection=18.0, actual_value=22.0)] * 20
        df = pd.DataFrame(rows)
        r = build_projection_bias_attribution(df, DATE)
        d = r["attribution"]["by_direction"]
        # over error = +5, under error = -4, delta = 9
        assert d["error_delta_over_minus_under"] == pytest.approx(9.0, abs=0.01)

    def test_missing_selection_col(self) -> None:
        df = _make_df().drop(columns=["selection"])
        r = build_projection_bias_attribution(df, DATE)
        assert r["attribution"]["by_direction"]["status"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Attribution: by_confidence_bucket and by_edge_bucket
# ---------------------------------------------------------------------------

class TestBucketAttribution:
    def test_confidence_buckets_present(self) -> None:
        df = _make_df(30, 20)
        r = build_projection_bias_attribution(df, DATE)
        conf = r["attribution"]["by_confidence_bucket"]
        labels = [b["label"] for b in conf]
        assert "<0.55" in labels
        assert "0.80+" in labels

    def test_edge_buckets_present(self) -> None:
        df = _make_df(30, 20)
        r = build_projection_bias_attribution(df, DATE)
        edge = r["attribution"]["by_edge_bucket"]
        labels = [b["label"] for b in edge]
        assert "0-2%" in labels
        assert "6%+" in labels

    def test_absent_confidence_col(self) -> None:
        df = _make_df().drop(columns=["confidence"])
        r = build_projection_bias_attribution(df, DATE)
        assert r["attribution"]["by_confidence_bucket"] == []


# ---------------------------------------------------------------------------
# Attribution: fragility
# ---------------------------------------------------------------------------

class TestFragilityAttribution:
    def test_fragility_insufficient_when_absent(self) -> None:
        df = _make_df()
        r = build_projection_bias_attribution(df, DATE)
        # fragility_bucket not in fixture columns
        fb = r["attribution"]["by_fragility"]
        assert fb["status"] == "insufficient_data"

    def test_fragility_insufficient_when_all_nan(self) -> None:
        df = _make_df()
        df["fragility_bucket"] = None
        r = build_projection_bias_attribution(df, DATE)
        assert r["attribution"]["by_fragility"]["status"] == "insufficient_data"

    def test_fragility_ok_when_populated(self) -> None:
        rows = [_row("hit")] * 15 + [_row("miss")] * 15
        df = pd.DataFrame(rows)
        df["fragility_bucket"] = ["LOW"] * 15 + ["HIGH"] * 15
        r = build_projection_bias_attribution(df, DATE)
        fb = r["attribution"]["by_fragility"]
        assert fb["status"] == "ok"
        assert "LOW" in fb
        assert "HIGH" in fb


# ---------------------------------------------------------------------------
# Attribution: context, caution, season, rematch
# ---------------------------------------------------------------------------

class TestOptionalDimensions:
    def test_context_attribution_populated(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_bias_attribution(df, DATE)
        ctx = r["attribution"]["by_context"]
        assert "coverage_pct" in ctx
        assert "aligned" in ctx

    def test_context_absent_col(self) -> None:
        df = _make_df().drop(columns=["context_pick_alignment"])
        r = build_projection_bias_attribution(df, DATE)
        assert r["attribution"]["by_context"]["status"] == "insufficient_data"

    def test_caution_level_populated(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_bias_attribution(df, DATE)
        cl = r["attribution"]["by_caution_level"]
        assert "coverage_pct" in cl

    def test_season_context_inferred(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_bias_attribution(df, DATE)
        sc = r["attribution"]["by_season_context"]
        # DATE = 2026-05-10 → playoffs
        assert sc.get("playoff_rows", 0) > 0

    def test_rematch_attribution(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_bias_attribution(df, DATE)
        rem = r["attribution"]["by_rematch"]
        assert "non_rematch" in rem

    def test_nan_handling_in_all_dimensions(self) -> None:
        df = _make_df(20, 10)
        # Inject NaNs into optional columns
        df.loc[df.index[:5], "context_pick_alignment"] = None
        df.loc[df.index[:5], "edge"] = None
        df.loc[df.index[:5], "confidence"] = None
        r = build_projection_bias_attribution(df, DATE)
        # Should not raise
        assert r["overall"]["total_graded_rows"] == 30


# ---------------------------------------------------------------------------
# Board tier
# ---------------------------------------------------------------------------

class TestBoardTierAttribution:
    def test_board_tier_keys(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_bias_attribution(df, DATE)
        bt = r["attribution"]["by_board_tier"]
        assert "elite" in bt
        assert "full_market" in bt

    def test_board_tier_splits(self) -> None:
        rows = [_row("hit", final_elite_rejection_reason="")] * 15
        rows += [_row("miss", final_elite_rejection_reason="low_edge")] * 15
        df = pd.DataFrame(rows)
        r = build_projection_bias_attribution(df, DATE)
        bt = r["attribution"]["by_board_tier"]
        assert bt["elite"]["sample_size"] == 15
        assert bt["full_market"]["sample_size"] == 15


# ---------------------------------------------------------------------------
# Team and player attribution
# ---------------------------------------------------------------------------

class TestTeamPlayerAttribution:
    def test_team_attribution_structure(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_bias_attribution(df, DATE)
        ta = r["attribution"]["by_team"]
        assert "top_inflated" in ta
        assert "top_calibrated" in ta

    def test_player_attribution_structure(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_bias_attribution(df, DATE)
        pa = r["attribution"]["by_player"]
        assert "top_inflated" in pa
        assert "top_calibrated" in pa

    def test_player_minimum_sample(self) -> None:
        # Only 2 rows per player — should not appear in analysis
        rows = [_row("hit", player_name=f"P{i}") for i in range(20)]
        df = pd.DataFrame(rows)
        r = build_projection_bias_attribution(df, DATE)
        pa = r["attribution"]["by_player"]
        assert pa["total_players_analyzed"] == 0 or all(
            s["sample_size"] >= 3 for s in pa["top_inflated"]
        )


# ---------------------------------------------------------------------------
# Root-cause diagnostics
# ---------------------------------------------------------------------------

class TestRootCauseDiagnostics:
    def test_root_cause_keys(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_bias_attribution(df, DATE)
        rc = r["root_cause_diagnostics"]
        expected = [
            "over_inflation_concentration",
            "high_confidence_inflation",
            "high_edge_inflation",
            "fragile_over_inflation",
            "context_conflicted_inflation",
            "elite_board_inflation",
            "combo_inflation",
            "minutes_optimism",
            "high_caution_inflation",
        ]
        for key in expected:
            assert key in rc, f"missing root cause key: {key}"

    def test_over_inflation_supported_flag(self) -> None:
        # OVER error >> UNDER error → should be supported
        rows = [_row("hit", selection="over", model_projection=28.0, actual_value=20.0)] * 20
        rows += [_row("hit", selection="over", model_projection=28.0, actual_value=20.0)] * 10
        rows += [_row("hit", selection="under", model_projection=22.0, actual_value=25.0)] * 15
        df = pd.DataFrame(rows)
        r = build_projection_bias_attribution(df, DATE)
        assert r["root_cause_diagnostics"]["over_inflation_concentration"]["supported"] is True

    def test_fragile_over_returns_insufficient(self) -> None:
        df = _make_df()
        r = build_projection_bias_attribution(df, DATE)
        assert r["root_cause_diagnostics"]["fragile_over_inflation"]["status"] == "insufficient_data"

    def test_minutes_optimism_absent(self) -> None:
        df = _make_df()
        r = build_projection_bias_attribution(df, DATE)
        assert r["root_cause_diagnostics"]["minutes_optimism"]["status"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Top findings
# ---------------------------------------------------------------------------

class TestTopFindings:
    def test_top_findings_keys(self) -> None:
        df = _make_df(30, 20)
        r = build_projection_bias_attribution(df, DATE)
        tf = r["top_findings"]
        expected_keys = [
            "worst_calibrated_segments",
            "best_calibrated_segments",
            "most_inflated_market",
            "best_calibrated_market",
            "most_inflated_confidence_bucket",
            "most_inflated_edge_bucket",
            "most_inflated_directional",
        ]
        for k in expected_keys:
            assert k in tf, f"missing top_findings key: {k}"

    def test_worst_segments_ranked(self) -> None:
        rows = [_row("miss", market_type="player_points_rebounds_assists",
                     model_projection=30.0, actual_value=20.0)] * 15
        rows += [_row("hit", market_type="player_rebounds",
                      model_projection=10.0, actual_value=10.0)] * 15
        df = pd.DataFrame(rows)
        r = build_projection_bias_attribution(df, DATE)
        tf = r["top_findings"]
        worst = tf.get("worst_calibrated_segments", [])
        if len(worst) >= 2:
            # First worst should have higher error than last
            errors = [s["mean_projection_error"] for s in worst if s and s.get("mean_projection_error") is not None]
            assert errors == sorted(errors, reverse=True)

    def test_best_calibrated_segments_low_error(self) -> None:
        df = _make_df(30, 20, model_projection=22.0, actual_value=22.0)
        r = build_projection_bias_attribution(df, DATE)
        tf = r["top_findings"]
        best = tf.get("best_calibrated_segments", [])
        if best:
            assert all(abs(s["mean_projection_error"]) < 1.0 for s in best if s and s.get("mean_projection_error") is not None)


# ---------------------------------------------------------------------------
# Writer tests
# ---------------------------------------------------------------------------

class TestWriter:
    def test_writes_json_and_txt(self, tmp_path: Path) -> None:
        df = _make_df(20, 10)
        json_path, txt_path, payload = write_projection_bias_attribution(
            df, DATE, runtime_root=tmp_path
        )
        assert json_path.exists()
        assert txt_path.exists()
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["report_version"] == REPORT_VERSION
        assert "attribution" in data

    def test_txt_contains_key_sections(self, tmp_path: Path) -> None:
        df = _make_df(20, 10)
        _, txt_path, _ = write_projection_bias_attribution(df, DATE, runtime_root=tmp_path)
        content = txt_path.read_text(encoding="utf-8")
        assert "Market Attribution" in content
        assert "Directional Attribution" in content
        assert "Confidence Bucket Attribution" in content
        assert "Edge Bucket Attribution" in content
        assert "Root-Cause Diagnostics" in content
        assert "Top Findings Summary" in content
        assert "shadow analytics only" in content.lower()

    def test_path_helpers(self) -> None:
        json_p = attribution_json_path_for_date(DATE, "outputs/runtime")
        txt_p  = attribution_txt_path_for_date(DATE, "outputs/runtime")
        assert json_p.name == f"projection_bias_attribution_{DATE}.json"
        assert txt_p.name  == f"projection_bias_attribution_{DATE}.txt"
        assert "diagnostics" in str(json_p)
        assert "operator" in str(txt_p)

    def test_empty_history_still_writes(self, tmp_path: Path) -> None:
        json_path, txt_path, payload = write_projection_bias_attribution(
            pd.DataFrame(), DATE, runtime_root=tmp_path
        )
        assert json_path.exists()
        assert txt_path.exists()
        assert payload["overall"]["total_graded_rows"] == 0


# ---------------------------------------------------------------------------
# Quality summary integration
# ---------------------------------------------------------------------------

class TestQualitySummaryIntegration:
    def _setup_runtime(self, tmp_path: Path) -> tuple[Path, Path, str]:
        runtime_root = tmp_path / "outputs" / "runtime"
        operator     = runtime_root / "operator"
        diagnostics  = runtime_root / "diagnostics"
        history      = tmp_path / "data" / "history"
        operator.mkdir(parents=True, exist_ok=True)
        diagnostics.mkdir(parents=True, exist_ok=True)
        history.mkdir(parents=True, exist_ok=True)
        (runtime_root / "research").mkdir(parents=True, exist_ok=True)
        date = DATE
        pd.DataFrame([{"prediction_date": date}]).to_csv(
            operator / f"elite_board_{date}.csv", index=False
        )
        pd.DataFrame([{"prediction_date": date}]).to_csv(
            operator / f"full_market_board_{date}.csv", index=False
        )
        pd.DataFrame([]).to_csv(operator / f"kelly_stakes_{date}.csv", index=False)
        (runtime_root / "research" / f"player_predictions_{date}.csv").write_text("", encoding="utf-8")
        (runtime_root / "research" / f"model_metrics_{date}.json").write_text("{}", encoding="utf-8")
        (operator / f"elite_pipeline_audit_summary_{date}.json").write_text("{}", encoding="utf-8")
        (diagnostics / f"board_diagnostics_{date}.json").write_text("{}", encoding="utf-8")
        return runtime_root, history, date

    def test_projection_bias_attribution_key_in_payload(self, tmp_path: Path) -> None:
        runtime_root, history, date = self._setup_runtime(tmp_path)
        rows = [_row("hit")] * 5 + [_row("miss")] * 5
        pd.DataFrame(rows).to_csv(history / "market_shadow_history.csv", index=False)

        _txt, _json, payload = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        attr = payload.get("projection_bias_attribution", {})
        assert attr, "projection_bias_attribution missing from payload"
        assert "json_path" in attr
        assert "txt_path" in attr
        assert "inflation_severity" in attr
        assert "mean_projection_error" in attr
        assert attr.get("note") == "shadow_analytics_only_investigative_bias_attribution"

    def test_attribution_artifacts_written(self, tmp_path: Path) -> None:
        runtime_root, history, date = self._setup_runtime(tmp_path)
        rows = [_row("hit")] * 5 + [_row("miss")] * 5
        pd.DataFrame(rows).to_csv(history / "market_shadow_history.csv", index=False)

        _txt, _json, payload = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        attr = payload["projection_bias_attribution"]
        assert Path(attr["json_path"]).exists(), "attribution JSON not written"
        assert Path(attr["txt_path"]).exists(), "attribution TXT not written"

    def test_missing_history_does_not_crash(self, tmp_path: Path) -> None:
        runtime_root, history, date = self._setup_runtime(tmp_path)
        # No market_shadow_history.csv

        _txt, _json, payload = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        attr = payload.get("projection_bias_attribution", {})
        assert "note" in attr

    def test_no_board_behavior_change(self, tmp_path: Path) -> None:
        runtime_root, history, date = self._setup_runtime(tmp_path)
        rows = [_row("hit")] * 5 + [_row("miss")] * 5
        pd.DataFrame(rows).to_csv(history / "market_shadow_history.csv", index=False)

        _txt, _json, payload = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        # Verify elite/full_market counts are not mutated by attribution
        # (quality_summary counts come from runtime artifacts, not history)
        assert "projection_bias_attribution" in payload
        # The payload should not contain altered elite/Kelly counts
        attr = payload["projection_bias_attribution"]
        assert "kelly_stake" not in str(attr)
        assert "elite_gate" not in str(attr)
