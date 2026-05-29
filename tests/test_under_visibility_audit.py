from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.under_visibility_audit import (
    build_under_visibility_audit,
    render_under_visibility_audit_text,
    report_paths_for_date,
    write_under_visibility_audit_outputs,
)


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_builds_visibility_audit_with_mock_slate(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    prediction_date = "2026-05-30"

    # Set up mock files
    # 1. Market availability
    market_avail = {
        "raw_provider_markets": {"provider1": 10, "provider2": 5},
        "normalized_markets": {"market1": 8},
        "counts": [],
    }
    _write_json(
        runtime_root / "diagnostics" / f"market_availability_audit_{prediction_date}.json",
        market_avail,
    )

    # 2. Player predictions (contains rejected)
    preds = [
        {
            "player_name": "Player A",
            "market_type": "player_points",
            "selection": "under",
            "line": 15.5,
            "model_projection": 13.2,
            "edge": -2.3,
            "confidence": 0.72,
            "quality_score": 62.0,
            "rejection_reason": "market_gate_confidence_lt_0.60",
        },
        {
            "player_name": "Player B",
            "market_type": "player_rebounds",
            "selection": "under",
            "line": 8.5,
            "model_projection": 7.0,
            "edge": -1.5,
            "confidence": 0.55,
            "quality_score": 45.0,
            "rejection_reason": "market_gate_minutes_lt_24",
        },
        {
            "player_name": "Player C",
            "market_type": "player_points",
            "selection": None,  # early gate rejection
            "rejection_reason": "reject_negative_edge_direction",
        },
    ]
    _write_csv(
        runtime_root / "research" / f"player_predictions_{prediction_date}.csv",
        preds,
    )

    # 3. Full market board (contains accepted)
    full_market = [
        {
            "player_name": "Player D",
            "market_type": "player_points",
            "selection": "under",
            "line": 20.5,
            "model_projection": 17.5,
            "edge": -3.0,
            "confidence": 0.75,
            "quality_score": 68.0,
            "same_opponent_under_warning": "True",
            "final_elite_rejection_reason": "none",
        },
        {
            "player_name": "Player E",
            "market_type": "player_rebounds",
            "selection": "over",
            "line": 6.5,
            "model_projection": 7.8,
            "edge": 1.3,
            "confidence": 0.76,
            "quality_score": 70.0,
            "same_opponent_under_warning": "False",
            "final_elite_rejection_reason": "none",
        },
    ]
    _write_csv(
        runtime_root / "operator" / f"full_market_board_{prediction_date}.csv",
        full_market,
    )

    # 4. Empty review / incubator / elite lists
    _write_csv(runtime_root / "operator" / f"near_elite_review_{prediction_date}.csv", [])
    _write_csv(runtime_root / "operator" / f"incubator_board_{prediction_date}.csv", [])
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{prediction_date}.csv", [])
    _write_csv(runtime_root / "operator" / f"elite_board_{prediction_date}.csv", [])

    # 5. History shadow candidates with mixed type shadow_roi
    history = [
        {"selection": "under", "result_status": "hit", "shadow_roi": "0.909091"},
        {"selection": "under", "result_status": "miss", "shadow_roi": -1.0},
        {"selection": "over", "result_status": "miss", "shadow_roi": "-1.0"},
        {"selection": "over", "result_status": "hit", "shadow_roi": 0.8},
    ]
    _write_csv(history_root / "market_shadow_history.csv", history)

    # Run build
    payload, df = build_under_visibility_audit(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    # Assertions on payload structure and values
    assert payload["prediction_date"] == prediction_date
    assert payload["read_only"] is True
    assert payload["betting_logic_changed"] is False

    funnel = payload["funnel_stages"]
    assert funnel["raw_odds"]["total"] == 30
    assert funnel["full_market"]["under"] == 1
    assert funnel["full_market"]["over"] == 1

    # check rejection counts
    rejections = payload["rejection_reasons"]
    assert rejections["same opponent warning"] == 1
    assert rejections["low confidence"] == 1  # Player A: confidence=0.72, rejected by market_gate_confidence_lt_0.60
    assert rejections["negative edge direction"] == 1  # Player C is 0.5 negative edge, rounded to 1

    # check shadow performance averages (mixed strings & floats parsed without TypeError)
    hist_comp = payload["historical_comparison"]
    assert hist_comp["under"]["count"] == 2
    assert hist_comp["over"]["count"] == 2
    assert pytest.approx(hist_comp["under"]["roi"]) == -0.0454545
    assert pytest.approx(hist_comp["over"]["roi"]) == -0.1

    # Verify rendering text report
    csv_path, text_path, json_path = report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    text_report = render_under_visibility_audit_text(payload, csv_path)
    assert "UNDER Candidate Visibility Audit" in text_report
    assert "Raw Odds Feeds" in text_report
    assert "Player D" in text_report


def test_handles_empty_or_missing_files_gracefully(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    prediction_date = "2026-05-31"

    # All files missing
    payload, df = build_under_visibility_audit(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert payload["prediction_date"] == prediction_date
    assert payload["funnel_stages"]["raw_odds"]["total"] == 0
    assert payload["historical_comparison"]["under"]["count"] == 0
    assert payload["current_slate_candidates"] == []
    assert df.empty


def test_write_audit_outputs_persists_correctly(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    prediction_date = "2026-05-30"

    # Create directories
    (runtime_root / "operator").mkdir(parents=True, exist_ok=True)
    (runtime_root / "diagnostics").mkdir(parents=True, exist_ok=True)
    (runtime_root / "research").mkdir(parents=True, exist_ok=True)
    history_root.mkdir(parents=True, exist_ok=True)

    csv_path, text_path, json_path, payload = write_under_visibility_audit_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert csv_path.exists()
    assert text_path.exists()
    assert json_path.exists()

    # Verify no betting-facing flags or components were written
    content = json.loads(json_path.read_text(encoding="utf-8"))
    assert content["read_only"] is True
    assert content["betting_logic_changed"] is False
