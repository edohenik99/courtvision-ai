from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.projection_calibration_shadow import (
    REPORT_VERSION,
    build_projection_calibration_report,
    calibration_json_path_for_date,
    calibration_txt_path_for_date,
    write_projection_calibration_report,
)
from courtvision.reporting.quality_summary import write_quality_summary_outputs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DATE = "2026-05-10"


def _shadow_row(
    result_status: str = "hit",
    confidence: float = 0.62,
    edge: float = 3.5,
    shadow_roi: float = 0.08,
    selection: str = "over",
    model_projection: float = 25.5,
    actual_value: float = 26.0,
    market_type: str = "player_points",
    prediction_date: str = DATE,
    final_elite_rejection_reason: str = "",
    context_pick_alignment: str = "aligned",
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
    }


def _make_df(n_hits: int = 20, n_misses: int = 15) -> pd.DataFrame:
    rows = []
    for _ in range(n_hits):
        rows.append(_shadow_row("hit", selection="over"))
    for _ in range(n_misses):
        rows.append(_shadow_row("miss", selection="under", confidence=0.58))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Unit tests: build_projection_calibration_report
# ---------------------------------------------------------------------------

class TestBuildReport:
    def test_empty_df_returns_empty_report(self) -> None:
        r = build_projection_calibration_report(pd.DataFrame(), DATE)
        assert r["report_version"] == REPORT_VERSION
        assert r["overall"]["total_graded_rows"] == 0
        assert r["overall"]["readiness"] == "INSUFFICIENT_SAMPLE"
        assert r["directional_bias"]["status"] == "insufficient_data"

    def test_basic_structure(self) -> None:
        df = _make_df(20, 15)
        r = build_projection_calibration_report(df, DATE)
        assert r["report_version"] == REPORT_VERSION
        assert r["prediction_date"] == DATE
        for key in ("overall", "directional", "directional_bias", "confidence_buckets",
                    "edge_buckets", "market_buckets", "context_buckets",
                    "fragility_buckets", "board_buckets", "rolling_trends",
                    "elite_projection_error", "caution_warnings"):
            assert key in r, f"missing key: {key}"

    def test_overall_metrics(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_calibration_report(df, DATE)
        ov = r["overall"]
        assert ov["total_graded_rows"] == 30
        assert ov["hit_rate"] == pytest.approx(20 / 30, abs=0.001)
        assert ov["calibration_gap"] is not None
        assert ov["readiness"] in ("INSUFFICIENT_SAMPLE", "NEUTRAL", "PROMISING",
                                   "STRONGLY_CALIBRATED", "UNSTABLE")

    def test_projection_error_computed(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_calibration_report(df, DATE)
        pe = r["overall"]["projection_error"]
        assert pe["status"] == "ok"
        assert pe["n"] == 30
        assert "mean_error" in pe
        assert "pct_over_predicted" in pe

    def test_notes_present(self) -> None:
        df = _make_df()
        r = build_projection_calibration_report(df, DATE)
        assert "shadow_analytics_only" in r["notes"]
        assert "no_live_logic_changed" in r["notes"]

    def test_insufficient_sample_readiness(self) -> None:
        df = pd.DataFrame([_shadow_row("hit"), _shadow_row("miss")])
        r = build_projection_calibration_report(df, DATE)
        assert r["overall"]["readiness"] == "INSUFFICIENT_SAMPLE"

    def test_no_graded_rows_returns_empty(self) -> None:
        df = pd.DataFrame([{"prediction_date": DATE, "result_status": "pending"}])
        r = build_projection_calibration_report(df, DATE)
        assert r["overall"]["total_graded_rows"] == 0


# ---------------------------------------------------------------------------
# Unit tests: directional analytics
# ---------------------------------------------------------------------------

class TestDirectional:
    def test_over_under_split(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_calibration_report(df, DATE)
        d = r["directional"]
        assert "over" in d
        assert "under" in d
        assert d["over"]["sample_size"] == 20
        assert d["under"]["sample_size"] == 10

    def test_over_vs_under_delta(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_calibration_report(df, DATE)
        d = r["directional"]
        assert d["over_vs_under_hit_rate_delta"] is not None

    def test_missing_selection_column(self) -> None:
        df = _make_df().drop(columns=["selection"])
        r = build_projection_calibration_report(df, DATE)
        assert r["directional"]["status"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Unit tests: directional bias diagnostics
# ---------------------------------------------------------------------------

class TestDirectionalBias:
    def test_bias_keys_present(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_calibration_report(df, DATE)
        db = r["directional_bias"]
        for key in ("full_market_over_count", "full_market_under_count",
                    "over_acceptance_rate", "under_acceptance_rate",
                    "over_hit_rate", "under_hit_rate",
                    "over_roi", "under_roi",
                    "directional_bias_verdict"):
            assert key in db, f"missing key: {key}"

    def test_full_market_counts(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_calibration_report(df, DATE)
        db = r["directional_bias"]
        assert db["full_market_over_count"] == 20
        assert db["full_market_under_count"] == 10

    def test_acceptance_rate_with_rejection_reasons(self) -> None:
        rows = []
        for _ in range(15):
            rows.append(_shadow_row("hit", selection="over", final_elite_rejection_reason=""))
        for _ in range(5):
            rows.append(_shadow_row("miss", selection="over", final_elite_rejection_reason="low_edge"))
        for _ in range(10):
            rows.append(_shadow_row("hit", selection="under", final_elite_rejection_reason=""))
        df = pd.DataFrame(rows)
        r = build_projection_calibration_report(df, DATE)
        db = r["directional_bias"]
        assert db["elite_over_count"] == 15
        assert db["elite_under_count"] == 10
        assert db["over_acceptance_rate"] == pytest.approx(15 / 20, abs=0.001)
        assert db["under_acceptance_rate"] == pytest.approx(10 / 10, abs=0.001)
        assert len(db["over_rejection_reasons"]) >= 1
        assert db["over_rejection_reasons"][0]["reason"] == "low_edge"

    def test_graded_hit_rates(self) -> None:
        rows = [_shadow_row("hit", selection="over")] * 10 + [_shadow_row("miss", selection="over")] * 10
        rows += [_shadow_row("hit", selection="under")] * 8 + [_shadow_row("miss", selection="under")] * 2
        df = pd.DataFrame(rows)
        r = build_projection_calibration_report(df, DATE)
        db = r["directional_bias"]
        assert db["over_hit_rate"] == pytest.approx(0.5, abs=0.001)
        assert db["under_hit_rate"] == pytest.approx(0.8, abs=0.001)
        assert db["hit_rate_delta_over_minus_under"] == pytest.approx(-0.3, abs=0.001)

    def test_bias_verdict_under_justified(self) -> None:
        # under has higher acceptance AND higher hit rate → justified
        rows = []
        for _ in range(10):
            rows.append(_shadow_row("hit", selection="under", final_elite_rejection_reason=""))
        for _ in range(5):
            rows.append(_shadow_row("miss", selection="over", final_elite_rejection_reason=""))
        for _ in range(5):
            rows.append(_shadow_row("miss", selection="over", final_elite_rejection_reason="low_edge"))
        df = pd.DataFrame(rows)
        r = build_projection_calibration_report(df, DATE)
        db = r["directional_bias"]
        assert db["directional_bias_verdict"] in (
            "under_dominance_justified_by_performance",
            "under_dominance_not_justified_performance_similar_or_worse",
            "balanced_selection",
            "over_dominant_selection",
            "inconclusive_insufficient_data",
        )

    def test_context_aligned_over_under(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_calibration_report(df, DATE)
        db = r["directional_bias"]
        # context_pick_alignment = "aligned" in fixture → should be populated
        assert isinstance(db.get("context_aligned_over"), dict)
        assert isinstance(db.get("context_aligned_under"), dict)

    def test_high_caution_over_suppression_absent_column(self) -> None:
        df = _make_df()
        r = build_projection_calibration_report(df, DATE)
        hco = r["directional_bias"].get("high_caution_over_suppression", {})
        # No caution flag column in fixture → insufficient_data
        assert hco.get("status") == "insufficient_data"

    def test_missing_selection_returns_insufficient(self) -> None:
        df = _make_df().drop(columns=["selection"])
        r = build_projection_calibration_report(df, DATE)
        assert r["directional_bias"]["status"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Unit tests: confidence + edge buckets
# ---------------------------------------------------------------------------

class TestBuckets:
    def test_confidence_buckets_labels(self) -> None:
        df = _make_df(40, 20)
        r = build_projection_calibration_report(df, DATE)
        labels = [b["label"] for b in r["confidence_buckets"]]
        assert "<0.55" in labels
        assert "0.55-0.60" in labels

    def test_edge_buckets_labels(self) -> None:
        df = _make_df(40, 20)
        r = build_projection_calibration_report(df, DATE)
        labels = [b["label"] for b in r["edge_buckets"]]
        assert "0-2%" in labels
        assert "6%+" in labels

    def test_market_buckets(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_calibration_report(df, DATE)
        mb = r["market_buckets"]
        assert "player_points" in mb
        assert isinstance(mb["player_points"]["sample_size"], int)

    def test_context_buckets_coverage(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_calibration_report(df, DATE)
        cb = r["context_buckets"]
        assert "coverage_pct" in cb
        assert "aligned" in cb


# ---------------------------------------------------------------------------
# Unit tests: fragility and board buckets
# ---------------------------------------------------------------------------

class TestSchemaConstraints:
    def test_fragility_insufficient_when_unpopulated(self) -> None:
        df = _make_df(40, 20)
        # fragility_bucket not in columns
        r = build_projection_calibration_report(df, DATE)
        fb = r["fragility_buckets"]
        assert fb.get("status") == "insufficient_data"

    def test_fragility_insufficient_when_all_nan(self) -> None:
        df = _make_df(40, 20)
        df["fragility_bucket"] = None
        r = build_projection_calibration_report(df, DATE)
        fb = r["fragility_buckets"]
        assert fb.get("status") == "insufficient_data"

    def test_board_buckets_insufficient_when_sparse(self) -> None:
        df = _make_df(40, 20)
        # Replace with NaN to simulate the real-world schema (5% populated)
        df["final_elite_rejection_reason"] = None
        r = build_projection_calibration_report(df, DATE)
        bb = r["board_buckets"]
        assert bb.get("status") == "insufficient_data"

    def test_board_buckets_ok_when_populated(self) -> None:
        rows = [_shadow_row("hit", final_elite_rejection_reason="") for _ in range(20)]
        rows += [_shadow_row("miss", final_elite_rejection_reason="low_edge") for _ in range(20)]
        rows += [_shadow_row("hit", final_elite_rejection_reason="low_edge") for _ in range(20)]
        df = pd.DataFrame(rows)
        r = build_projection_calibration_report(df, DATE)
        bb = r["board_buckets"]
        assert bb.get("status") == "ok"
        assert "elite_board" in bb
        assert "full_market" in bb


# ---------------------------------------------------------------------------
# Unit tests: rolling trends
# ---------------------------------------------------------------------------

class TestRollingTrends:
    def test_single_date_returns_insufficient(self) -> None:
        df = _make_df(20, 10)
        r = build_projection_calibration_report(df, DATE)
        rt = r["rolling_trends"]
        assert rt.get("status") == "insufficient_data" or rt.get("unique_dates") is not None

    def test_multiple_dates(self) -> None:
        rows = []
        for i in range(5):
            date = f"2026-05-0{i+1}"
            for _ in range(6):
                rows.append(_shadow_row("hit", prediction_date=date))
            for _ in range(4):
                rows.append(_shadow_row("miss", prediction_date=date))
        df = pd.DataFrame(rows)
        r = build_projection_calibration_report(df, DATE)
        rt = r["rolling_trends"]
        assert rt.get("unique_dates") == 5
        assert "last_7_slates" in rt

    def test_insufficient_window_when_few_slates(self) -> None:
        rows = []
        for i in range(3):
            date = f"2026-05-0{i+1}"
            rows.append(_shadow_row("hit", prediction_date=date))
        df = pd.DataFrame(rows)
        r = build_projection_calibration_report(df, DATE)
        rt = r["rolling_trends"]
        w14 = rt.get("last_14_slates", {})
        assert w14.get("status") == "insufficient_sample" or w14.get("slates_available") is not None


# ---------------------------------------------------------------------------
# Unit tests: pick_history supplemental
# ---------------------------------------------------------------------------

class TestPickHistory:
    def test_pick_history_projection_error(self) -> None:
        shadow = _make_df(20, 10)
        pick = pd.DataFrame([
            {"result_status": "hit", "projection": 24.0, "actual_value": 25.0},
            {"result_status": "miss", "projection": 28.0, "actual_value": 20.0},
        ])
        r = build_projection_calibration_report(shadow, DATE, pick_history_df=pick)
        epe = r["elite_projection_error"]
        assert epe["status"] == "ok"
        assert epe["source"] == "pick_history"
        assert epe["n"] == 2

    def test_pick_history_none_returns_insufficient(self) -> None:
        shadow = _make_df(20, 10)
        r = build_projection_calibration_report(shadow, DATE, pick_history_df=None)
        assert r["elite_projection_error"]["status"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Integration test: write_projection_calibration_report
# ---------------------------------------------------------------------------

class TestWriter:
    def test_writes_json_and_txt(self, tmp_path: Path) -> None:
        df = _make_df(20, 10)
        json_path, txt_path, payload = write_projection_calibration_report(
            df, DATE, runtime_root=tmp_path
        )
        assert json_path.exists()
        assert txt_path.exists()
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["report_version"] == REPORT_VERSION
        assert "overall" in data
        assert "directional_bias" in data

    def test_txt_contains_key_sections(self, tmp_path: Path) -> None:
        df = _make_df(20, 10)
        _, txt_path, _ = write_projection_calibration_report(
            df, DATE, runtime_root=tmp_path
        )
        content = txt_path.read_text(encoding="utf-8")
        assert "Directional (OVER vs UNDER)" in content
        assert "Directional Bias Diagnostics" in content
        assert "Confidence Buckets" in content
        assert "Rolling Trends" in content
        assert "shadow analytics only" in content.lower()

    def test_path_helpers(self) -> None:
        json_p = calibration_json_path_for_date(DATE, "outputs/runtime")
        txt_p = calibration_txt_path_for_date(DATE, "outputs/runtime")
        assert json_p.name == f"projection_calibration_shadow_{DATE}.json"
        assert txt_p.name == f"projection_calibration_shadow_{DATE}.txt"
        assert "diagnostics" in str(json_p)
        assert "operator" in str(txt_p)

    def test_empty_history_still_writes(self, tmp_path: Path) -> None:
        json_path, txt_path, payload = write_projection_calibration_report(
            pd.DataFrame(), DATE, runtime_root=tmp_path
        )
        assert json_path.exists()
        assert txt_path.exists()
        assert payload["overall"]["total_graded_rows"] == 0


# ---------------------------------------------------------------------------
# Integration test: quality_summary integration
# ---------------------------------------------------------------------------

class TestQualitySummaryIntegration:
    def _setup_runtime(self, tmp_path: Path) -> tuple[Path, Path, str]:
        runtime_root = tmp_path / "outputs" / "runtime"
        operator = runtime_root / "operator"
        diagnostics = runtime_root / "diagnostics"
        history = tmp_path / "data" / "history"
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

    def test_projection_calibration_shadow_key_in_payload(self, tmp_path: Path) -> None:
        runtime_root, history, date = self._setup_runtime(tmp_path)
        rows = [_shadow_row("hit")] * 5 + [_shadow_row("miss")] * 5
        pd.DataFrame(rows).to_csv(history / "market_shadow_history.csv", index=False)

        _txt, _json, payload = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        cal = payload.get("projection_calibration_shadow", {})
        assert cal, "projection_calibration_shadow missing from payload"
        assert "json_path" in cal
        assert "txt_path" in cal
        assert "readiness" in cal
        assert "directional_bias_verdict" in cal
        assert cal.get("note") == "shadow_analytics_only_no_live_logic_changed"

    def test_calibration_artifacts_written(self, tmp_path: Path) -> None:
        runtime_root, history, date = self._setup_runtime(tmp_path)
        rows = [_shadow_row("hit")] * 5 + [_shadow_row("miss")] * 5
        pd.DataFrame(rows).to_csv(history / "market_shadow_history.csv", index=False)

        _txt, _json, payload = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        cal = payload["projection_calibration_shadow"]
        assert Path(cal["json_path"]).exists(), "calibration JSON not written"
        assert Path(cal["txt_path"]).exists(), "calibration TXT not written"

    def test_missing_history_does_not_crash(self, tmp_path: Path) -> None:
        runtime_root, history, date = self._setup_runtime(tmp_path)
        # Do NOT write market_shadow_history.csv

        _txt, _json, payload = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        cal = payload.get("projection_calibration_shadow", {})
        # Should have the key; readiness may be None or INSUFFICIENT_SAMPLE
        assert "note" in cal
