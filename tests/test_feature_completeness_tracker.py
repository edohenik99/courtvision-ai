from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.feature_completeness_tracker import (
    DIAGNOSTIC_ONLY_NOTE,
    FORWARD_FEATURE_START_DATE,
    build_feature_completeness_report,
    render_feature_completeness_report,
    write_feature_completeness_report,
)

DATE = "2026-05-25"


def _mock_market_row(
    *,
    player_id: str = "player-1",
    player_name: str = "Fixture Player",
    team: str = "BOS",
    market_type: str = "player_points",
    selection: str = "over",
    line: float = 20.5,
    context_pick_alignment: str = "aligned",
    context_caution_level: str = "low",
    fragility_bucket: str = "low",
    survivability_bucket: str = "high",
    role_stability_bucket: str = "stable",
    meta_label_rules_score: float | None = 0.85,
) -> dict:
    return {
        "prediction_date": DATE,
        "player_id": player_id,
        "player_name": player_name,
        "team": team,
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "sportsbook_line": line,
        "context_pick_alignment": context_pick_alignment,
        "context_caution_level": context_caution_level,
        "fragility_bucket": fragility_bucket,
        "survivability_bucket": survivability_bucket,
        "role_stability_bucket": role_stability_bucket,
        "meta_label_rules_score": meta_label_rules_score,
    }


def test_current_date_coverage_calculations() -> None:
    # 1. Complete row
    row1 = _mock_market_row()
    # 2. Row with missing context alignment
    row2 = _mock_market_row(player_id="player-2", context_pick_alignment="")
    # 3. Row with invalid role stability
    row3 = _mock_market_row(player_id="player-3", role_stability_bucket="unknown")
    # 4. Row with missing meta_label_rules_score
    row4 = _mock_market_row(player_id="player-4", meta_label_rules_score=None)

    df = pd.DataFrame([row1, row2, row3, row4])
    report = build_feature_completeness_report(DATE, full_market_df=df)
    current = report["current_coverage"]

    assert current["full_market_rows"] == 4
    assert current["rows_with_context_alignment"] == 3
    assert current["rows_with_context_caution"] == 4
    assert current["rows_with_fragility"] == 4
    assert current["rows_with_survivability"] == 4
    assert current["rows_with_role_stability"] == 3  # stable is valid, unknown is invalid
    assert current["rows_with_meta_label_rules_score"] == 3
    assert current["feature_complete_current_rows"] == 1  # only row1 is complete


def test_historical_coverage_and_verdict_transitions(tmp_path: Path) -> None:
    # Write some mock historical data
    # Forward starting date is FORWARD_FEATURE_START_DATE ("2026-05-24")
    hist_rows = []
    
    # Construct a list of 10 forward rows
    for i in range(10):
        hist_rows.append({
            "prediction_date": FORWARD_FEATURE_START_DATE,
            "player_id": f"player-{i}",
            "player_name": f"Player {i}",
            "team": "BOS",
            "market_type": "player_points",
            "selection": "over",
            "line": 20.5,
            "result_status": "hit",
            "context_pick_alignment": "aligned",
            "context_caution_level": "low",
            "fragility_bucket": "low",
            "survivability_bucket": "high",
            "role_stability_bucket": "stable" if i < 9 else "unknown", # 10% missing rate
            "meta_label_rules_score": 0.8,
        })
        
    hist_df = pd.DataFrame(hist_rows)
    
    # Mock promotion shadow CSV file
    runtime_root = tmp_path / "runtime"
    operator_dir = runtime_root / "operator"
    operator_dir.mkdir(parents=True, exist_ok=True)
    
    promo_csv_path = operator_dir / f"meta_label_promotion_shadow_{FORWARD_FEATURE_START_DATE}.csv"
    promo_rows = []
    for i in range(10):
        promo_rows.append({
            "player_id": f"player-{i}",
            "player_name": f"Player {i}",
            "team": "BOS",
            "market_type": "player_points",
            "selection": "over",
            "line": 20.5,
            "meta_label_rules_score": 0.8,
        })
    pd.DataFrame(promo_rows).to_csv(promo_csv_path, index=False)

    report = build_feature_completeness_report(
        DATE,
        shadow_history_df=hist_df,
        runtime_root=runtime_root,
    )
    hist = report["historical_coverage"]
    readiness = report["readiness"]

    assert hist["completed_slate_count"] == 1
    assert hist["graded_hit_miss_rows"] == 10
    assert hist["feature_complete_graded_rows"] == 9
    assert hist["role_stability_missing_rate"] == 0.10
    assert hist["fragility_missing_rate"] == 0.0
    assert hist["survivability_missing_rate"] == 0.0
    assert hist["context_missing_rate"] == 0.0
    assert hist["meta_label_rules_score_missing_rate"] == 0.0

    # Verdict with 10% missing role stability should be NEED_FEATURE_BACKFILL_REVIEW
    assert readiness["verdict"] == "NEED_FEATURE_BACKFILL_REVIEW"


def test_verdict_sample_small(tmp_path: Path) -> None:
    hist_rows = []
    for i in range(5):
        hist_rows.append({
            "prediction_date": FORWARD_FEATURE_START_DATE,
            "player_id": f"player-{i}",
            "player_name": f"Player {i}",
            "team": "BOS",
            "market_type": "player_points",
            "selection": "over",
            "line": 20.5,
            "result_status": "hit",
            "context_pick_alignment": "aligned",
            "context_caution_level": "low",
            "fragility_bucket": "low",
            "survivability_bucket": "high",
            "role_stability_bucket": "stable",
            "meta_label_rules_score": 0.8,
        })
        
    hist_df = pd.DataFrame(hist_rows)
    runtime_root = tmp_path / "runtime"
    operator_dir = runtime_root / "operator"
    operator_dir.mkdir(parents=True, exist_ok=True)
    
    promo_csv_path = operator_dir / f"meta_label_promotion_shadow_{FORWARD_FEATURE_START_DATE}.csv"
    promo_rows = []
    for i in range(5):
        promo_rows.append({
            "player_id": f"player-{i}",
            "player_name": f"Player {i}",
            "team": "BOS",
            "market_type": "player_points",
            "selection": "over",
            "line": 20.5,
            "meta_label_rules_score": 0.8,
        })
    pd.DataFrame(promo_rows).to_csv(promo_csv_path, index=False)

    report = build_feature_completeness_report(
        DATE,
        shadow_history_df=hist_df,
        runtime_root=runtime_root,
    )
    readiness = report["readiness"]
    # All missing rates < 10%, but graded count < 1000 -> FEATURE_COLLECTION_HEALTHY_BUT_SAMPLE_SMALL
    assert readiness["verdict"] == "FEATURE_COLLECTION_HEALTHY_BUT_SAMPLE_SMALL"


def test_no_leakage_and_rendering() -> None:
    row = _mock_market_row()
    # verify post-game fields are completely absent from mock inputs to avoid leakage
    assert "actual_value" not in row
    assert "result_status" not in row

    report = build_feature_completeness_report(DATE, full_market_df=pd.DataFrame([row]))
    rendered = render_feature_completeness_report(report)
    
    assert "Feature Completeness Tracker - Shadow Only" in rendered
    assert DIAGNOSTIC_ONLY_NOTE in rendered


def test_writes_artifacts_correctly(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    runtime_root.mkdir(parents=True, exist_ok=True)
    history_root.mkdir(parents=True, exist_ok=True)

    # Write sentinel mock shadow history
    shadow_history_path = history_root / "market_shadow_history.csv"
    shadow_history_df = pd.DataFrame([{
        "prediction_date": FORWARD_FEATURE_START_DATE,
        "player_id": "player-1",
        "player_name": "Player 1",
        "team": "BOS",
        "market_type": "player_points",
        "selection": "over",
        "line": 20.5,
        "result_status": "hit",
        "context_pick_alignment": "aligned",
        "context_caution_level": "low",
        "fragility_bucket": "low",
        "survivability_bucket": "high",
        "role_stability_bucket": "stable",
    }])
    shadow_history_df.to_csv(shadow_history_path, index=False)

    # Write sentinel mock full market board
    fm_path = runtime_root / "operator" / f"full_market_board_{DATE}.csv"
    fm_path.parent.mkdir(parents=True, exist_ok=True)
    full_market_df = pd.DataFrame([_mock_market_row()])
    full_market_df.to_csv(fm_path, index=False)

    # Write sentinel mock promotion shadow CSV
    promo_csv_path = runtime_root / "operator" / f"meta_label_promotion_shadow_{FORWARD_FEATURE_START_DATE}.csv"
    promo_rows = [{
        "player_id": "player-1",
        "player_name": "Player 1",
        "team": "BOS",
        "market_type": "player_points",
        "selection": "over",
        "line": 20.5,
        "meta_label_rules_score": 0.8,
    }]
    pd.DataFrame(promo_rows).to_csv(promo_csv_path, index=False)

    json_path, txt_path, csv_path, payload = write_feature_completeness_report(
        prediction_date=DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert json_path.exists()
    assert txt_path.exists()
    assert csv_path.exists()

    # Verify JSON content
    content = json.loads(json_path.read_text(encoding="utf-8"))
    assert content["report_version"] == "1.0"
    assert content["prediction_date"] == DATE

    # Verify CSV headers
    csv_df = pd.read_csv(csv_path)
    assert set(csv_df.columns) == {"metric_group", "metric_name", "value", "target_threshold", "status"}


def test_non_mutation_properties(tmp_path: Path) -> None:
    # verify that calling write_feature_completeness_report does not mutate input arrays or history files
    history_root = tmp_path / "history"
    runtime_root = tmp_path / "runtime"
    history_root.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)

    shadow_history_path = history_root / "market_shadow_history.csv"
    shadow_history_df = pd.DataFrame([{
        "prediction_date": FORWARD_FEATURE_START_DATE,
        "player_id": "player-1",
        "player_name": "Player 1",
        "team": "BOS",
        "market_type": "player_points",
        "selection": "over",
        "line": 20.5,
        "result_status": "hit",
        "context_pick_alignment": "aligned",
        "context_caution_level": "low",
        "fragility_bucket": "low",
        "survivability_bucket": "high",
        "role_stability_bucket": "stable",
    }])
    shadow_history_df.to_csv(shadow_history_path, index=False)

    history_before_bytes = shadow_history_path.read_bytes()

    write_feature_completeness_report(
        prediction_date=DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        shadow_history_df=shadow_history_df.copy(),
    )

    assert shadow_history_path.read_bytes() == history_before_bytes


def test_missing_columns_safety_no_crash(tmp_path: Path) -> None:
    # Proves missing expected columns in full_market_df and shadow_history_df are handled safely.
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    runtime_root.mkdir(parents=True, exist_ok=True)
    history_root.mkdir(parents=True, exist_ok=True)

    # Empty DataFrame with no columns
    empty_df = pd.DataFrame()

    # Should not crash and successfully return default payload/write files
    json_p, txt_p, csv_p, payload = write_feature_completeness_report(
        prediction_date=DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        shadow_history_df=empty_df,
        full_market_df=empty_df,
    )
    assert json_p.exists()
    assert payload["historical_coverage"]["graded_hit_miss_rows"] == 0
    assert payload["readiness"]["estimated_additional_slates_needed"] == "n/a"
    assert payload["readiness"]["projection_reason"] == "insufficient_forward_sample"


def test_zero_sample_projection_is_na(tmp_path: Path) -> None:
    # Proves that if forward graded rows is 0, estimated additional slates is "n/a" and reason is "insufficient_forward_sample"
    report = build_feature_completeness_report(
        prediction_date=DATE,
        shadow_history_df=pd.DataFrame(),
        full_market_df=pd.DataFrame(),
    )
    readiness = report["readiness"]
    assert readiness["estimated_additional_slates_needed"] == "n/a"
    assert readiness["projection_reason"] == "insufficient_forward_sample"
    assert readiness["verdict"] == "WAIT_MORE_FORWARD_DATA"

    # Verify rendering
    text = render_feature_completeness_report(report)
    assert "- estimated additional slates needed: n/a" in text
    assert "- projection_reason: insufficient_forward_sample" in text


def test_operator_card_and_daily_summary_render_na_safely(tmp_path: Path) -> None:
    # Verify that scripts can safely render "n/a" estimated slates
    from scripts.write_operator_card import write_operator_card_outputs
    from scripts.write_daily_summary import write_daily_summary_outputs

    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    research = runtime_root / "research"
    model = tmp_path / "model"

    operator.mkdir(parents=True, exist_ok=True)
    diagnostics.mkdir(parents=True, exist_ok=True)
    research.mkdir(parents=True, exist_ok=True)
    model.mkdir(parents=True, exist_ok=True)

    prediction_date = "2026-05-24"

    # Pre-populate required dummy files
    pd.DataFrame().to_csv(operator / f"elite_board_{prediction_date}.csv", index=False)
    pd.DataFrame().to_csv(operator / f"kelly_stakes_{prediction_date}.csv", index=False)
    pd.DataFrame().to_csv(operator / f"full_market_board_{prediction_date}.csv", index=False)
    pd.DataFrame().to_csv(operator / f"sgp_board_{prediction_date}.csv", index=False)
    pd.DataFrame().to_csv(research / f"player_predictions_{prediction_date}.csv", index=False)
    pd.DataFrame().to_csv(model / "player_baselines.csv", index=False)
    
    with open(research / f"model_metrics_{prediction_date}.json", "w") as f:
        json.dump({}, f)
    with open(diagnostics / f"board_diagnostics_{prediction_date}.json", "w") as f:
        json.dump({}, f)
    with open(operator / f"elite_pipeline_audit_summary_{prediction_date}.json", "w") as f:
        json.dump({}, f)
    with open(diagnostics / f"market_availability_audit_{prediction_date}.json", "w") as f:
        json.dump({}, f)
    with open(diagnostics / f"market_shadow_grading_{prediction_date}.json", "w") as f:
        json.dump({}, f)
    with open(diagnostics / f"completion_state_audit_{prediction_date}.json", "w") as f:
        json.dump({}, f)

    # Write a mock feature completeness with "n/a"
    with open(diagnostics / f"feature_completeness_tracker_{prediction_date}.json", "w") as f:
        json.dump({
            "historical_coverage": {
                "completed_slate_count": 0,
                "graded_hit_miss_rows": 0,
                "feature_complete_graded_rows": 0,
            },
            "readiness": {
                "verdict": "WAIT_MORE_FORWARD_DATA",
                "estimated_additional_slates_needed": "n/a",
            }
        }, f)

    # Calling write_operator_card_outputs should not crash and render safely
    out_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    assert out_path.exists()
    assert payload["feature_completeness_tracker"]["estimated_additional_slates_needed"] == "n/a"

    # Calling write_daily_summary_outputs should not crash and render safely
    sum_txt_path, sum_payload = write_daily_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    assert sum_txt_path.exists()
    assert sum_payload["feature_completeness_tracker"]["estimated_additional_slates_needed"] == "n/a"
