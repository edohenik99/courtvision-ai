from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.context.meta_label_promotion import (
    DIAGNOSTIC_ONLY_NOTE,
    calculate_meta_label_rules_score_row,
    apply_meta_label_promotion,
)
from courtvision.reporting.meta_label_promotion import (
    build_meta_label_promotion_report,
    render_meta_label_promotion_report,
    write_meta_label_promotion_report,
)


DATE = "2026-05-25"


def _mock_row(
    *,
    prediction_date: str = DATE,
    player_id: str = "player-1",
    player_name: str = "Fixture Player",
    game_id: str = "game-1",
    market_type: str = "player_points",
    selection: str = "over",
    line: float = 20.5,
    odds: float = -110,
    edge: float = 2.0,
    confidence: float = 0.70,
    quality_score: float = 70.0,
    role_stability_bucket: str = "stable",
    context_pick_alignment: str = "aligned",
    context_caution_level: str = "low",
    same_opponent_under_warning: bool = False,
    player_points_realism_dampened: bool = False,
    blocked_by_elite_points_risk_guard: bool = False,
    fragility_score: float = 20.0,
    fragility_bucket: str = "LOW",
    survivability_score: float = 68.0,
    survivability_bucket: str = "HIGH",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_id": player_id,
        "player_name": player_name,
        "game_id": game_id,
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "odds": odds,
        "edge": edge,
        "confidence": confidence,
        "quality_score": quality_score,
        "role_stability_bucket": role_stability_bucket,
        "context_pick_alignment": context_pick_alignment,
        "context_caution_level": context_caution_level,
        "same_opponent_under_warning": same_opponent_under_warning,
        "player_points_realism_dampened": player_points_realism_dampened,
        "blocked_by_elite_points_risk_guard": blocked_by_elite_points_risk_guard,
        "fragility_score": fragility_score,
        "fragility_bucket": fragility_bucket,
        "survivability_score": survivability_score,
        "survivability_bucket": survivability_bucket,
    }


def test_deterministic_score_mappings() -> None:
    # 1. Base stable, high-quality row (rewards added, score=100)
    base_row = _mock_row()
    res = calculate_meta_label_rules_score_row(base_row)
    assert res["meta_label_rules_score"] == 100.0
    assert res["meta_label_bucket"] == "shadow_strong_review_candidate"
    assert "good_quality_score" in res["reason_codes"]
    assert "good_confidence" in res["reason_codes"]

    # 2. Extreme penalties (extreme caution, conflicted alignment, volatile role)
    volatile_row = _mock_row(
        context_caution_level="extreme",
        context_pick_alignment="conflicted",
        role_stability_bucket="highly_volatile",
        same_opponent_under_warning=True,
    )
    res = calculate_meta_label_rules_score_row(volatile_row)
    assert res["meta_label_rules_score"] < 35.0
    assert res["meta_label_bucket"] == "shadow_avoid_review"
    assert "extreme_caution" in res["reason_codes"]
    assert "context_conflicted" in res["reason_codes"]
    assert "role_highly_volatile" in res["reason_codes"]
    assert "same_opponent_warning" in res["reason_codes"]


def test_bucket_and_status_mappings() -> None:
    # Test distinct score thresholds map to exact buckets and statuses
    # Score 90
    row_90 = _mock_row()
    res = calculate_meta_label_rules_score_row(row_90)
    assert res["meta_label_bucket"] == "shadow_strong_review_candidate"
    assert res["meta_label_status"] == "review_candidate"

    # Score 70: lower edge and confidence, higher caution
    row_70 = _mock_row(
        confidence=0.60,
        quality_score=60.0,
        context_caution_level="medium",
        role_stability_bucket="mostly_stable",
        context_pick_alignment="",
        fragility_bucket="MEDIUM",
        fragility_score=50.0,
        survivability_bucket="MEDIUM",
        survivability_score=50.0,
    )
    res = calculate_meta_label_rules_score_row(row_70)
    assert 50.0 <= res["meta_label_rules_score"] <= 79.0

    # Avoid review score
    avoid_row = _mock_row(
        confidence=0.50,
        context_caution_level="extreme",
        role_stability_bucket="highly_volatile",
        blocked_by_elite_points_risk_guard=True,
    )
    res = calculate_meta_label_rules_score_row(avoid_row)
    assert res["meta_label_rules_score"] <= 34.0
    assert res["meta_label_bucket"] == "shadow_avoid_review"
    assert res["meta_label_status"] == "avoid_review"


def test_leakage_fields_ignored_even_if_present() -> None:
    base_row = _mock_row()
    res_base = calculate_meta_label_rules_score_row(base_row)

    leakage_row = _mock_row()
    leakage_row.update(
        {
            "actual_value": 45,
            "result_status": "hit",
            "hit": True,
            "miss": False,
            "push": False,
            "shadow_roi": 1.25,
            "closing_line_observed": 22.5,
            "clv_grade": "positive",
            "clv_line_points": 2.0,
            "grading_skip_reason": "void",
        }
    )
    res_leakage = calculate_meta_label_rules_score_row(leakage_row)

    assert res_base["meta_label_rules_score"] == res_leakage["meta_label_rules_score"]
    assert res_base["reason_codes"] == res_leakage["reason_codes"]


def test_unknown_role_reasons_and_warning() -> None:
    row = _mock_row(role_stability_bucket="")
    res = calculate_meta_label_rules_score_row(row)
    assert res["role_stability_bucket"] == "unknown"
    assert "missing_role_stability_coverage" in res["missing_feature_warnings"]
    assert "role_stability_unknown" in res["reason_codes"]


def test_calibration_payload_observations() -> None:
    row = _mock_row()

    # Test match with good calibration
    cal_good = {
        "rows": [
            {
                "bucket_dimension": "market_type",
                "market_type": "player_points",
                "selection": "over",
                "calibration_gap": -0.01,
                "graded_n": 15,
            }
        ]
    }
    res_good = calculate_meta_label_rules_score_row(row, cal_payload=cal_good)
    assert "good_calibration" in res_good["reason_codes"]

    # Test match with poor calibration
    cal_poor = {
        "rows": [
            {
                "bucket_dimension": "market_type",
                "market_type": "player_points",
                "selection": "over",
                "calibration_gap": -0.22,
                "graded_n": 10,
            }
        ]
    }
    res_poor = calculate_meta_label_rules_score_row(row, cal_payload=cal_poor)
    assert "poor_calibration_observation" in res_poor["reason_codes"]


def test_report_renders_json_txt_csv() -> None:
    df = pd.DataFrame([_mock_row()])
    role_payload = {
        "rows": [
            {
                "player_name": "Fixture Player",
                "player_id": "player-1",
                "role_stability_bucket": "stable",
                "role_stability_score": 100.0,
                "role_stability_coverage": 1.0,
            }
        ]
    }
    cal_payload = {"rows": []}

    payload = build_meta_label_promotion_report(
        DATE, full_market_df=df, role_payload=role_payload, cal_payload=cal_payload
    )

    assert payload["summary"]["total_rows_evaluated"] == 1
    assert payload["summary"]["shadow_strong_review_candidate_count"] == 1
    assert len(payload["rows"]) == 1

    rendered = render_meta_label_promotion_report(payload)
    assert "Meta-Label Promotion - Shadow Only" in rendered
    assert DIAGNOSTIC_ONLY_NOTE in rendered
    assert "Fixture Player" in rendered


def test_no_side_effects_on_production_boards(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    operator.mkdir(parents=True, exist_ok=True)
    history_root.mkdir(parents=True, exist_ok=True)

    # Setup core prediction board mocks (sentinels to prove report-only)
    elite_path = operator / f"elite_board_{DATE}.csv"
    kelly_path = operator / f"kelly_stakes_{DATE}.csv"
    full_path = operator / f"full_market_board_{DATE}.csv"

    elite_sentinel = "player_name,market_type\nElite Player,player_points\n"
    kelly_sentinel = "player_name,stake_amount\nKelly Player,10.0\n"
    full_sentinel = "prediction_date,player_id,player_name,game_id,market_type,selection,line,odds,edge,confidence,quality_score\n2026-05-25,player-1,Fixture Player,game-1,player_points,over,20.5,-110,2.0,0.70,70.0\n"

    elite_path.write_text(elite_sentinel, encoding="utf-8")
    kelly_path.write_text(kelly_sentinel, encoding="utf-8")
    full_path.write_text(full_sentinel, encoding="utf-8")

    # Generate reports
    write_meta_label_promotion_report(
        prediction_date=DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    # Assert active files were NOT modified or overwritten
    assert elite_path.read_text(encoding="utf-8") == elite_sentinel
    assert kelly_path.read_text(encoding="utf-8") == kelly_sentinel
    assert full_path.read_text(encoding="utf-8") == full_sentinel
