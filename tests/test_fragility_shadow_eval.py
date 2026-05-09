"""Tests for Phase 9 fragility/survivability shadow evaluation reporting."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.quality_summary import write_quality_summary_outputs
from courtvision.reporting.fragility_shadow_eval import (
    SHADOW_EVAL_REPORT_VERSION,
    build_shadow_eval_report,
    shadow_eval_path_for_date,
    write_shadow_eval_report,
)


def _fixture_history(n_hit: int = 60, n_miss: int = 40) -> pd.DataFrame:
    rows = []
    for i in range(n_hit):
        rows.append({
            "prediction_date": "2026-05-01",
            "result_status": "hit",
            "fragility_bucket": "LOW" if i % 3 != 0 else "HIGH",
            "survivability_bucket": "HIGH" if i % 3 != 0 else "LOW",
            "confidence": 0.72 if i % 3 != 0 else 0.51,
            "quality_score": 74.0 if i % 3 != 0 else 48.0,
            "edge": 1.5 if i % 3 != 0 else -0.5,
            "market_type": "player_points",
            "selection": "over" if i % 2 == 0 else "under",
        })
    for i in range(n_miss):
        rows.append({
            "prediction_date": "2026-05-01",
            "result_status": "miss",
            "fragility_bucket": "HIGH" if i % 3 != 0 else "LOW",
            "survivability_bucket": "LOW" if i % 3 != 0 else "HIGH",
            "confidence": 0.52 if i % 3 != 0 else 0.68,
            "quality_score": 49.0 if i % 3 != 0 else 71.0,
            "edge": -0.8 if i % 3 != 0 else 1.2,
            "market_type": "player_rebounds" if i % 2 == 0 else "player_points",
            "selection": "under",
        })
    return pd.DataFrame(rows)


# ── report structure ──────────────────────────────────────────────────────────

def test_report_generates_from_fixture_history() -> None:
    df = _fixture_history()
    report = build_shadow_eval_report(df, "2026-05-01")
    assert report["report_version"] == SHADOW_EVAL_REPORT_VERSION
    assert "diagnostic_coverage" in report
    assert "bucket_outcome_analysis" in report
    assert "baseline_comparison" in report
    assert "predictive_lift" in report
    assert "conflict_analysis" in report
    assert "readiness_verdict" in report


def test_report_bucket_outcome_keys() -> None:
    df = _fixture_history()
    report = build_shadow_eval_report(df, "2026-05-01")
    boa = report["bucket_outcome_analysis"]
    assert "by_fragility_bucket" in boa
    assert "by_survivability_bucket" in boa
    assert boa["by_fragility_bucket"]


def test_report_baseline_comparison_keys() -> None:
    df = _fixture_history()
    report = build_shadow_eval_report(df, "2026-05-01")
    bc = report["baseline_comparison"]
    assert "global_baseline_hit_rate" in bc
    assert "by_confidence_bucket" in bc
    assert "by_quality_bucket" in bc
    assert "by_edge_bucket" in bc
    assert "by_market_type" in bc
    assert "by_selection" in bc


# ── missing / empty data ──────────────────────────────────────────────────────

def test_empty_history_does_not_crash() -> None:
    report = build_shadow_eval_report(pd.DataFrame(), "2026-05-01")
    assert report["readiness_verdict"] == "not_ready_insufficient_sample"
    assert report["diagnostic_coverage"]["total_historical_rows"] == 0


def test_missing_diagnostics_do_not_crash() -> None:
    df = pd.DataFrame([
        {"result_status": "hit", "confidence": 0.7, "quality_score": 72.0},
        {"result_status": "miss", "confidence": 0.5, "quality_score": 48.0},
    ])
    report = build_shadow_eval_report(df, "2026-05-01")
    assert "readiness_verdict" in report
    cov = report["diagnostic_coverage"]
    assert cov["rows_with_fragility_diagnostics"] == 0
    assert cov["graded_rows_with_diagnostics"] == 0
    assert cov["diagnostics_coverage_pct"] == 0.0


def test_no_graded_rows_does_not_crash() -> None:
    df = pd.DataFrame([
        {"result_status": "pending", "fragility_bucket": "HIGH"},
        {"result_status": None, "fragility_bucket": "LOW"},
    ])
    report = build_shadow_eval_report(df, "2026-05-01")
    assert report["readiness_verdict"] == "not_ready_insufficient_sample"
    assert report["diagnostic_coverage"]["total_graded_rows"] == 0


# ── coverage calculations ─────────────────────────────────────────────────────

def test_coverage_calculations_correct() -> None:
    df = pd.DataFrame([
        {"result_status": "hit", "fragility_bucket": "HIGH", "survivability_bucket": "LOW"},
        {"result_status": "miss", "fragility_bucket": "LOW", "survivability_bucket": "HIGH"},
        {"result_status": "hit"},          # graded but no fragility diagnostics
        {"result_status": None},           # not graded
    ])
    report = build_shadow_eval_report(df, "2026-05-01")
    cov = report["diagnostic_coverage"]
    assert cov["total_historical_rows"] == 4
    assert cov["total_graded_rows"] == 3
    assert cov["rows_with_fragility_diagnostics"] == 2
    assert cov["rows_with_survivability_diagnostics"] == 2
    assert cov["graded_rows_with_diagnostics"] == 2
    assert cov["diagnostics_coverage_pct"] == pytest.approx(66.67, abs=0.1)


def test_coverage_full_diagnostics() -> None:
    df = pd.DataFrame([
        {"result_status": "hit", "fragility_bucket": "LOW", "survivability_bucket": "HIGH"},
        {"result_status": "miss", "fragility_bucket": "HIGH", "survivability_bucket": "LOW"},
    ])
    report = build_shadow_eval_report(df, "2026-05-01")
    cov = report["diagnostic_coverage"]
    assert cov["total_graded_rows"] == 2
    assert cov["graded_rows_with_diagnostics"] == 2
    assert cov["diagnostics_coverage_pct"] == 100.0


# ── readiness verdict ─────────────────────────────────────────────────────────

def test_readiness_not_ready_for_small_sample() -> None:
    df = _fixture_history(n_hit=5, n_miss=5)
    report = build_shadow_eval_report(df, "2026-05-01")
    assert report["readiness_verdict"] == "not_ready_insufficient_sample"


def test_readiness_promising_with_directional_lift() -> None:
    # 100–299 graded rows with diagnostics, strong directional lift
    rows = []
    for i in range(100):
        rows.append({
            "result_status": "hit" if i < 60 else "miss",
            "fragility_bucket": "LOW" if i < 60 else "HIGH",
            "survivability_bucket": "HIGH" if i < 60 else "LOW",
        })
    report = build_shadow_eval_report(pd.DataFrame(rows), "2026-05-01")
    assert report["readiness_verdict"] == "promising_needs_more_sample"


def test_readiness_reject_no_lift() -> None:
    # 150 rows with diagnostics but ~50 % hit rate for every bucket (no lift)
    rows = []
    for i in range(150):
        rows.append({
            "result_status": "hit" if i % 2 == 0 else "miss",
            "fragility_bucket": "HIGH" if i % 3 == 0 else "LOW",
            "survivability_bucket": "HIGH" if i % 3 == 0 else "LOW",
        })
    report = build_shadow_eval_report(pd.DataFrame(rows), "2026-05-01")
    assert report["readiness_verdict"] == "reject_signal_no_lift"


def test_readiness_ready_for_shadow_policy_300_plus() -> None:
    # 300+ rows with strong, consistent lift in both fragility and survivability
    rows = []
    for i in range(300):
        rows.append({
            "result_status": "hit" if i < 200 else "miss",
            "fragility_bucket": "LOW" if i < 200 else "HIGH",
            "survivability_bucket": "HIGH" if i < 200 else "LOW",
        })
    report = build_shadow_eval_report(pd.DataFrame(rows), "2026-05-01")
    assert report["readiness_verdict"] == "ready_for_shadow_policy_design"


# ── conflict analysis ─────────────────────────────────────────────────────────

def test_conflict_analysis_catches_high_confidence_high_fragility() -> None:
    df = pd.DataFrame([
        {
            "result_status": "hit",
            "fragility_bucket": "HIGH",
            "survivability_bucket": "LOW",
            "confidence": 0.82,
            "quality_score": 75.0,
            "edge": 1.5,
        },
        {
            "result_status": "miss",
            "fragility_bucket": "HIGH",
            "survivability_bucket": "LOW",
            "confidence": 0.78,
            "quality_score": 72.0,
            "edge": 1.2,
        },
        {
            "result_status": "hit",
            "fragility_bucket": "LOW",
            "survivability_bucket": "HIGH",
            "confidence": 0.65,
            "quality_score": 68.0,
            "edge": 0.5,
        },
    ])
    report = build_shadow_eval_report(df, "2026-05-01")
    ca = report["conflict_analysis"]
    assert ca["high_confidence_high_fragility_rows"] == 2
    assert ca["high_confidence_high_fragility_hits"] == 1
    assert ca["high_confidence_high_fragility_misses"] == 1
    assert ca["high_quality_high_fragility_rows"] == 2
    assert ca["strong_edge_high_fragility_rows"] == 2
    assert ca["high_fragility_win_rows"] == 1
    assert ca["high_survivability_miss_rows"] == 0


def test_conflict_analysis_high_survivability_miss() -> None:
    df = pd.DataFrame([
        {
            "result_status": "miss",
            "fragility_bucket": "LOW",
            "survivability_bucket": "HIGH",
            "confidence": 0.75,
            "quality_score": 80.0,
            "edge": 1.8,
        },
    ])
    report = build_shadow_eval_report(df, "2026-05-01")
    ca = report["conflict_analysis"]
    assert ca["high_survivability_miss_rows"] == 1
    assert ca["high_fragility_win_rows"] == 0


def test_conflict_analysis_no_crash_without_optional_fields() -> None:
    df = pd.DataFrame([
        {"result_status": "hit", "fragility_bucket": "HIGH", "survivability_bucket": "LOW"},
        {"result_status": "miss", "fragility_bucket": "LOW", "survivability_bucket": "HIGH"},
    ])
    report = build_shadow_eval_report(df, "2026-05-01")
    ca = report["conflict_analysis"]
    assert "high_confidence_high_fragility_rows" not in ca
    assert "high_quality_high_fragility_rows" not in ca
    assert "high_fragility_win_rows" in ca
    assert "high_survivability_miss_rows" in ca


# ── no decision fields mutated ────────────────────────────────────────────────

def test_no_decision_fields_change() -> None:
    df = _fixture_history()
    before_conf = df["confidence"].copy()
    before_qual = df["quality_score"].copy()
    before_edge = df["edge"].copy()
    before_frag = df["fragility_bucket"].copy()
    build_shadow_eval_report(df, "2026-05-01")
    pd.testing.assert_series_equal(df["confidence"], before_conf)
    pd.testing.assert_series_equal(df["quality_score"], before_qual)
    pd.testing.assert_series_equal(df["edge"], before_edge)
    pd.testing.assert_series_equal(df["fragility_bucket"], before_frag)


# ── file output ───────────────────────────────────────────────────────────────

def test_write_shadow_eval_report_creates_json_and_txt(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    df = _fixture_history()
    json_path, payload = write_shadow_eval_report(df, "2026-05-01", runtime_root)
    assert json_path.exists()
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert "readiness_verdict" in loaded
    assert loaded["report_version"] == SHADOW_EVAL_REPORT_VERSION
    txt_path = runtime_root / "operator" / "fragility_survivability_shadow_eval_2026-05-01.txt"
    assert txt_path.exists()
    txt_content = txt_path.read_text(encoding="utf-8")
    assert "readiness_verdict" in txt_content
    assert "diagnostics" in txt_content.lower()


def test_write_shadow_eval_creates_parent_dirs(tmp_path: Path) -> None:
    runtime_root = tmp_path / "deep" / "nested" / "runtime"
    df = _fixture_history(n_hit=2, n_miss=2)
    json_path, _ = write_shadow_eval_report(df, "2026-05-09", runtime_root)
    assert json_path.exists()


def test_shadow_eval_path_for_date() -> None:
    p = shadow_eval_path_for_date("2026-05-01", "outputs/runtime")
    assert p.name == "fragility_survivability_shadow_eval_2026-05-01.json"
    assert "diagnostics" in p.parts


def test_write_shadow_eval_empty_df_does_not_crash(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    json_path, payload = write_shadow_eval_report(pd.DataFrame(), "2026-05-01", runtime_root)
    assert json_path.exists()
    assert payload["readiness_verdict"] == "not_ready_insufficient_sample"


def test_report_is_deterministic() -> None:
    df = _fixture_history()
    r1 = build_shadow_eval_report(df, "2026-05-01")
    r2 = build_shadow_eval_report(df, "2026-05-01")
    assert r1["readiness_verdict"] == r2["readiness_verdict"]
    assert r1["diagnostic_coverage"] == r2["diagnostic_coverage"]
    assert r1["conflict_analysis"] == r2["conflict_analysis"]


# ── quality_summary metadata integration ──────────────────────────────────────

def test_quality_summary_includes_shadow_eval_metadata(tmp_path: Path) -> None:
    """quality_summary payload must carry shadow_eval path/verdict/coverage."""
    runtime_root = tmp_path / "outputs" / "runtime"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    history = tmp_path / "outputs" / "history"
    for d in (operator, diagnostics, history):
        d.mkdir(parents=True, exist_ok=True)

    date = "2026-05-09"
    # minimum required artifact stubs
    pd.DataFrame([{"prediction_date": date}]).to_csv(
        operator / f"elite_board_{date}.csv", index=False
    )
    pd.DataFrame([{"prediction_date": date}]).to_csv(
        operator / f"full_market_board_{date}.csv", index=False
    )
    pd.DataFrame([]).to_csv(operator / f"kelly_stakes_{date}.csv", index=False)
    (runtime_root / "research").mkdir(parents=True, exist_ok=True)
    (runtime_root / "research" / f"player_predictions_{date}.csv").write_text(
        "", encoding="utf-8"
    )
    (runtime_root / "research" / f"model_metrics_{date}.json").write_text(
        "{}", encoding="utf-8"
    )
    (operator / f"elite_pipeline_audit_summary_{date}.json").write_text(
        "{}", encoding="utf-8"
    )
    (diagnostics / f"board_diagnostics_{date}.json").write_text("{}", encoding="utf-8")

    # seed minimal history with fragility diagnostics
    pd.DataFrame([
        {
            "prediction_date": date,
            "result_status": "hit",
            "fragility_bucket": "LOW",
            "survivability_bucket": "HIGH",
            "confidence": 0.72,
            "quality_score": 74.0,
        },
        {
            "prediction_date": date,
            "result_status": "miss",
            "fragility_bucket": "HIGH",
            "survivability_bucket": "LOW",
            "confidence": 0.51,
            "quality_score": 48.0,
        },
    ]).to_csv(history / "market_shadow_history.csv", index=False)

    _txt, _json, payload = write_quality_summary_outputs(
        prediction_date=date,
        runtime_root=runtime_root,
        out_dir=tmp_path / "outputs",
    )

    # verify shadow_eval metadata is present in the payload
    se = payload.get("fragility_survivability_shadow_eval", {})
    assert se, "fragility_survivability_shadow_eval missing from quality_summary payload"
    assert "path" in se
    assert "readiness_verdict" in se
    assert "graded_rows_with_diagnostics" in se
    assert "diagnostics_coverage_pct" in se
    assert se["note"] == "diagnostics_non_operational"

    # verify the JSON report file was created
    report_path = Path(se["path"])
    assert report_path.exists(), f"shadow eval JSON not written: {report_path}"
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert "readiness_verdict" in loaded
    assert loaded["diagnostic_coverage"]["graded_rows_with_diagnostics"] == 2

    # verify no decision fields appear in the shadow eval report
    assert "is_elite" not in loaded
    assert "kelly_eligible" not in loaded
    assert "stake_amount" not in loaded
