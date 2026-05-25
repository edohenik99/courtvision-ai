from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.meta_label_rules_performance import (
    DIAGNOSTIC_ONLY_NOTE,
    build_rules_performance_report,
    render_rules_performance_report,
    write_rules_performance_report,
    performance_json_path_for_date,
    performance_txt_path_for_date,
    performance_csv_path_for_date,
)
from courtvision.reporting.artifact_manifest import build_artifact_manifest
from scripts.write_daily_summary import build_daily_summary
from scripts.write_operator_card import build_operator_card


DATE = "2026-05-25"


def _mock_history_row(
    *,
    result_status: str = "hit",
    market_type: str = "player_points",
    selection: str = "over",
    edge: float = 10.0,
    confidence: float = 0.85,
    quality_score: float = 90.0,
    context_pick_alignment: str = "aligned",
    context_caution_level: str = "low",
    role_stability_bucket: str = "stable",
    fragility_bucket: str = "LOW",
) -> dict:
    return {
        "prediction_date": DATE,
        "player_id": "player-1",
        "player_name": "Fixture Player",
        "team": "BOS",
        "market_type": market_type,
        "selection": selection,
        "result_status": result_status,
        "edge": edge,
        "confidence": confidence,
        "quality_score": quality_score,
        "context_pick_alignment": context_pick_alignment,
        "context_caution_level": context_caution_level,
        "role_stability_bucket": role_stability_bucket,
        "fragility_bucket": fragility_bucket,
    }


def test_hit_rate_denominator_and_numerator() -> None:
    # 5 hits, 5 misses, 2 pushes, 2 voids, 2 pending (all strong candidates)
    rows = []
    for _ in range(5):
        rows.append(_mock_history_row(result_status="hit"))
    for _ in range(5):
        rows.append(_mock_history_row(result_status="miss"))
    for _ in range(2):
        rows.append(_mock_history_row(result_status="push"))
    for _ in range(2):
        rows.append(_mock_history_row(result_status="void"))
    for _ in range(2):
        rows.append(_mock_history_row(result_status="pending"))

    df = pd.DataFrame(rows)
    payload = build_rules_performance_report(DATE, shadow_history_df=df)

    # Graded rows should only be hits + misses = 10
    # Total rows = 16
    # Hit rate = 5 / 10 = 0.50
    band_80 = payload["score_bands"]["80-100"]
    assert band_80["total_rows"] == 16
    assert band_80["graded_rows"] == 10
    assert band_80["hits"] == 5
    assert band_80["misses"] == 5
    assert band_80["pushes"] == 2
    assert band_80["voids"] == 2
    assert band_80["pending"] == 2
    assert band_80["hit_rate"] == 0.50


def test_small_sample_flagging() -> None:
    # Graded rows < 30 -> insufficient
    rows_insufficient = []
    for _ in range(29):
        rows_insufficient.append(_mock_history_row(result_status="hit"))
    df_insufficient = pd.DataFrame(rows_insufficient)
    payload_insufficient = build_rules_performance_report(DATE, shadow_history_df=df_insufficient)
    assert payload_insufficient["score_bands"]["80-100"]["sample_status"] == "insufficient"

    # Graded rows >= 30 -> sufficient
    rows_sufficient = []
    for _ in range(30):
        rows_sufficient.append(_mock_history_row(result_status="hit"))
    df_sufficient = pd.DataFrame(rows_sufficient)
    payload_sufficient = build_rules_performance_report(DATE, shadow_history_df=df_sufficient)
    assert payload_sufficient["score_bands"]["80-100"]["sample_status"] == "sufficient"


def test_missing_features_count() -> None:
    # 10 rows total:
    # - 3 with role_stability_bucket = "unknown" (30% missing)
    # - 4 with fragility_bucket = "" (40% missing)
    rows = []
    for i in range(10):
        role = "unknown" if i < 3 else "stable"
        frag = "" if i < 4 else "LOW"
        rows.append(_mock_history_row(role_stability_bucket=role, fragility_bucket=frag))

    df = pd.DataFrame(rows)
    payload = build_rules_performance_report(DATE, shadow_history_df=df)

    readiness = payload["data_readiness"]
    assert readiness["missing_role_stability_rate"] == 0.30
    assert readiness["missing_fragility_rate"] == 0.40


def test_phase_4c_readiness_verdict() -> None:
    # Case 1: Graded < 1000 -> WAIT_MORE_DATA
    rows_low = [_mock_history_row()] * 500
    payload_low = build_rules_performance_report(DATE, shadow_history_df=pd.DataFrame(rows_low))
    assert payload_low["data_readiness"]["verdict"] == "WAIT_MORE_DATA"

    # Case 2: Graded >= 1000, but missing role stability >= 30% -> NEED_FEATURE_BACKFILL
    rows_missing_role = []
    for i in range(1000):
        role = "unknown" if i < 350 else "stable"
        rows_missing_role.append(_mock_history_row(role_stability_bucket=role))
    payload_missing_role = build_rules_performance_report(DATE, shadow_history_df=pd.DataFrame(rows_missing_role))
    assert payload_missing_role["data_readiness"]["verdict"] == "NEED_FEATURE_BACKFILL"

    # Case 3: Graded >= 1000, missing fragility >= 30% -> NEED_FEATURE_BACKFILL
    rows_missing_frag = []
    for i in range(1000):
        frag = "" if i < 350 else "LOW"
        rows_missing_frag.append(_mock_history_row(fragility_bucket=frag))
    payload_missing_frag = build_rules_performance_report(DATE, shadow_history_df=pd.DataFrame(rows_missing_frag))
    assert payload_missing_frag["data_readiness"]["verdict"] == "NEED_FEATURE_BACKFILL"

    # Case 4: Graded >= 1000, features complete, but strong bucket <= avoid bucket -> RULES_BASELINE_UNPROVEN
    # Let's mock a scenario:
    # Strong bucket has 500 rows, hit_rate = 45%
    # Avoid bucket has 500 rows, hit_rate = 55%
    rows_unproven = []
    # Strong bucket (rules score >= 80)
    for _ in range(225):
        rows_unproven.append(_mock_history_row(result_status="hit"))
    for _ in range(275):
        rows_unproven.append(_mock_history_row(result_status="miss"))
    # Avoid bucket (rules score < 35)
    for _ in range(275):
        rows_unproven.append(
            _mock_history_row(
                result_status="hit",
                edge=-5.0,
                confidence=0.20,
                quality_score=10.0,
                context_pick_alignment="conflicted",
                context_caution_level="extreme",
                role_stability_bucket="highly_volatile",
                fragility_bucket="HIGH",
            )
        )
    for _ in range(225):
        rows_unproven.append(
            _mock_history_row(
                result_status="miss",
                edge=-5.0,
                confidence=0.20,
                quality_score=10.0,
                context_pick_alignment="conflicted",
                context_caution_level="extreme",
                role_stability_bucket="highly_volatile",
                fragility_bucket="HIGH",
            )
        )

    payload_unproven = build_rules_performance_report(DATE, shadow_history_df=pd.DataFrame(rows_unproven))
    assert payload_unproven["data_readiness"]["verdict"] == "RULES_BASELINE_UNPROVEN"

    # Case 5: Graded >= 1000, features complete, strong bucket > avoid bucket -> READY_FOR_PHASE_4C
    rows_ready = []
    # Strong bucket (rules score >= 80)
    for _ in range(350):
        rows_ready.append(_mock_history_row(result_status="hit"))
    for _ in range(150):
        rows_ready.append(_mock_history_row(result_status="miss"))
    # Avoid bucket (rules score < 35)
    for _ in range(150):
        rows_ready.append(
            _mock_history_row(
                result_status="hit",
                edge=-5.0,
                confidence=0.20,
                quality_score=10.0,
                context_pick_alignment="conflicted",
                context_caution_level="extreme",
                role_stability_bucket="highly_volatile",
                fragility_bucket="HIGH",
            )
        )
    for _ in range(350):
        rows_ready.append(
            _mock_history_row(
                result_status="miss",
                edge=-5.0,
                confidence=0.20,
                quality_score=10.0,
                context_pick_alignment="conflicted",
                context_caution_level="extreme",
                role_stability_bucket="highly_volatile",
                fragility_bucket="HIGH",
            )
        )

    payload_ready = build_rules_performance_report(DATE, shadow_history_df=pd.DataFrame(rows_ready))
    assert payload_ready["data_readiness"]["verdict"] == "READY_FOR_PHASE_4C"


def test_report_writing_and_no_side_effects(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    runtime_root.mkdir(parents=True, exist_ok=True)
    history_root.mkdir(parents=True, exist_ok=True)

    # Sentinel operators to prove no mutation
    operator = runtime_root / "operator"
    operator.mkdir(parents=True, exist_ok=True)
    elite_path = operator / f"elite_board_{DATE}.csv"
    kelly_path = operator / f"kelly_stakes_{DATE}.csv"
    elite_path.write_text("player_name\nElite Sentinel\n", encoding="utf-8")
    kelly_path.write_text("player_name,stake_amount\nKelly Sentinel,15.5\n", encoding="utf-8")

    elite_before = elite_path.read_bytes()
    kelly_before = kelly_path.read_bytes()

    # Generate sparse shadow history CSV
    shadow_hist_path = history_root / "market_shadow_history.csv"
    df = pd.DataFrame([_mock_history_row()])
    df.to_csv(shadow_hist_path, index=False)

    json_path, txt_path, csv_path, payload = write_rules_performance_report(
        prediction_date=DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert json_path.exists()
    assert txt_path.exists()
    assert csv_path.exists()

    # Verify content disclaimer
    txt_content = txt_path.read_text(encoding="utf-8")
    assert "Meta-Label Rules Performance - Shadow Only" in txt_content
    assert DIAGNOSTIC_ONLY_NOTE in txt_content

    # Confirm original operator boards are completely untouched
    assert elite_path.read_bytes() == elite_before
    assert kelly_path.read_bytes() == kelly_before


def test_artifact_manifest_shadow_only(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)

    # Create dummy performance artifacts so they are reported as exists=True
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    operator.mkdir(parents=True, exist_ok=True)
    diagnostics.mkdir(parents=True, exist_ok=True)

    (operator / f"meta_label_rules_performance_{DATE}.txt").write_text("dummy", encoding="utf-8")
    (diagnostics / f"meta_label_rules_performance_{DATE}.json").write_text("{}", encoding="utf-8")
    (operator / f"meta_label_rules_performance_{DATE}.csv").write_text("dummy", encoding="utf-8")

    manifest = build_artifact_manifest(prediction_date=DATE, runtime_root=runtime_root)
    
    # Check that our artifacts are present in the manifest and have severity shadow_only
    rules_perf_txt = next(a for a in manifest["artifacts"] if a["name"] == "meta_label_rules_performance_txt")
    rules_perf_json = next(a for a in manifest["artifacts"] if a["name"] == "meta_label_rules_performance_json")
    rules_perf_csv = next(a for a in manifest["artifacts"] if a["name"] == "meta_label_rules_performance_csv")

    assert rules_perf_txt["exists"] is True
    assert rules_perf_txt["severity"] == "shadow_only"
    assert rules_perf_json["exists"] is True
    assert rules_perf_json["severity"] == "shadow_only"
    assert rules_perf_csv["exists"] is True
    assert rules_perf_csv["severity"] == "shadow_only"


def test_disclaimer_displays_everywhere(tmp_path: Path) -> None:
    # 1. Setup mock directories
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    operator.mkdir(parents=True, exist_ok=True)
    diagnostics.mkdir(parents=True, exist_ok=True)
    history_root.mkdir(parents=True, exist_ok=True)

    # 2. Write dummy required board files
    (operator / f"elite_board_{DATE}.csv").write_text("player_name\nSentinel\n", encoding="utf-8")
    (operator / f"full_market_board_{DATE}.csv").write_text("player_name\nSentinel\n", encoding="utf-8")
    (diagnostics / f"board_diagnostics_{DATE}.json").write_text("{}", encoding="utf-8")
    (diagnostics / f"completion_state_audit_{DATE}.json").write_text("{}", encoding="utf-8")
    (diagnostics / f"artifact_manifest_{DATE}.json").write_text("{}", encoding="utf-8")

    # Write meta-label rules performance payload
    payload = build_rules_performance_report(DATE, shadow_history_df=pd.DataFrame([_mock_history_row()]))
    (diagnostics / f"meta_label_rules_performance_{DATE}.json").write_text(json.dumps(payload), encoding="utf-8")

    # 3. Build Daily Summary and verify disclaimer exists
    summary_text, _ = build_daily_summary(prediction_date=DATE, runtime_root=runtime_root, history_root=history_root)
    assert "Meta-Label Rules Performance - Shadow Only" in summary_text
    assert DIAGNOSTIC_ONLY_NOTE in summary_text

    # 4. Build Operator Card and verify disclaimer exists
    card_text, _ = build_operator_card(prediction_date=DATE, runtime_root=runtime_root, history_root=history_root)
    assert "Meta-Label Rules Performance - Shadow Only" in card_text
    assert DIAGNOSTIC_ONLY_NOTE in card_text
