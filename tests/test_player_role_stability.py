from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.context.player_role_stability import (
    calculate_player_role_stability_row,
    apply_player_role_stability,
)
from courtvision.reporting.player_role_stability import (
    DIAGNOSTIC_ONLY_NOTE,
    build_player_role_stability_report,
    render_player_role_stability_report,
    write_player_role_stability_report,
)


DATE = "2026-05-25"


def _mock_row(
    *,
    minutes_avg: float | None = 30.0,
    minutes_recent: float | None = 30.0,
    minutes_projection: float | None = 30.0,
    minutes_cv_recent: float | None = None,
    manual_minutes_delta: float | None = None,
    injury_role_pressure: float | bool | None = None,
    starter_or_rotation_status: str | None = "starter",
    role_data_quality: str | None = "high",
) -> dict:
    return {
        "prediction_date": DATE,
        "game_id": "game-1",
        "player_id": "player-1",
        "player_name": "Fixture Player",
        "team": "BOS",
        "opponent": "MIA",
        "market_type": "player_points",
        "selection": "over",
        "minutes_avg": minutes_avg,
        "minutes_recent": minutes_recent,
        "minutes_projection": minutes_projection,
        "minutes_cv_recent": minutes_cv_recent,
        "manual_minutes_delta": manual_minutes_delta,
        "injury_role_pressure": injury_role_pressure,
        "starter_or_rotation_status": starter_or_rotation_status,
        "role_data_quality": role_data_quality,
    }


def test_deterministic_score_and_bucket_mapping() -> None:
    # Baseline stable row (no deductions, score=100)
    stable_row = _mock_row()
    res = calculate_player_role_stability_row(stable_row)
    assert res["role_stability_score"] == 100.0
    assert res["role_stability_bucket"] == "stable"
    assert "stable_role_metrics" in res["role_stability_reasons"]

    # Mild deduction (recent delta=4.0 -> -10, score=90)
    mild_row = _mock_row(minutes_recent=34.0)
    res = calculate_player_role_stability_row(mild_row)
    assert res["role_stability_score"] == 90.0
    assert res["role_stability_bucket"] == "stable"
    assert "mild_recent_avg_delta" in res["role_stability_reasons"]

    # Mostly stable mapping (recent delta=6.0 -> -20, score=80)
    mostly_row = _mock_row(minutes_recent=36.0)
    res = calculate_player_role_stability_row(mostly_row)
    assert res["role_stability_score"] == 80.0
    assert res["role_stability_bucket"] == "stable"
    assert "moderate_recent_avg_delta" in res["role_stability_reasons"]

    # Mixed row (projection delta=6.0 -> -20, CV=0.30 -> -20, score=60)
    mixed_row = _mock_row(minutes_projection=36.0, minutes_cv_recent=0.30)
    res = calculate_player_role_stability_row(mixed_row)
    assert res["role_stability_score"] == 60.0
    assert res["role_stability_bucket"] == "mostly_stable"
    assert "moderate_projection_avg_delta" in res["role_stability_reasons"]
    assert "moderate_recent_minutes_cv" in res["role_stability_reasons"]

    # Volatile row (recent delta=9.0 -> -30, CV=0.45 -> -30, score=40)
    volatile_row = _mock_row(minutes_recent=39.0, minutes_cv_recent=0.45)
    res = calculate_player_role_stability_row(volatile_row)
    assert res["role_stability_score"] == 40.0
    assert res["role_stability_bucket"] == "mixed"

    # Highly volatile row (recent delta=9.0 -> -30, proj delta=9.0 -> -30, CV=0.45 -> -30, score=10)
    highly_row = _mock_row(minutes_recent=39.0, minutes_projection=39.0, minutes_cv_recent=0.45)
    res = calculate_player_role_stability_row(highly_row)
    assert res["role_stability_score"] == 10.0
    assert res["role_stability_bucket"] == "highly_volatile"


def test_missing_data_becomes_unknown_not_stable() -> None:
    # Missing minutes_avg
    missing_avg = _mock_row(minutes_avg=None)
    res = calculate_player_role_stability_row(missing_avg)
    assert res["role_stability_score"] is None
    assert res["role_stability_bucket"] == "unknown"
    assert "missing_critical_minutes_data" in res["role_stability_reasons"]

    # Missing minutes_recent
    missing_recent = _mock_row(minutes_recent=None)
    res = calculate_player_role_stability_row(missing_recent)
    assert res["role_stability_score"] is None
    assert res["role_stability_bucket"] == "unknown"
    assert "missing_critical_minutes_data" in res["role_stability_reasons"]


def test_high_minutes_volatility_becomes_volatile_or_highly_volatile() -> None:
    # High recent delta + High projection delta + CV
    row = _mock_row(minutes_recent=39.0, minutes_projection=39.0, minutes_cv_recent=0.50)
    res = calculate_player_role_stability_row(row)
    assert res["role_stability_score"] == 10.0
    assert res["role_stability_bucket"] == "highly_volatile"
    assert "high_recent_avg_delta" in res["role_stability_reasons"]
    assert "high_projection_avg_delta" in res["role_stability_reasons"]
    assert "high_recent_minutes_cv" in res["role_stability_reasons"]


def test_manual_context_reasons_safely() -> None:
    # Delta delta=6.0 -> -20 penalty
    row = _mock_row(manual_minutes_delta=6.0)
    res = calculate_player_role_stability_row(row)
    assert res["role_stability_score"] == 80.0
    assert "large_manual_minutes_adjustment" in res["role_stability_reasons"]

    # Delta delta=3.0 -> -10 penalty
    row2 = _mock_row(manual_minutes_delta=-3.0)
    res2 = calculate_player_role_stability_row(row2)
    assert res2["role_stability_score"] == 90.0
    assert "moderate_manual_minutes_adjustment" in res2["role_stability_reasons"]


def test_injury_role_pressure_reasons_safely() -> None:
    # Boolean True -> -20 penalty
    row = _mock_row(injury_role_pressure=True)
    res = calculate_player_role_stability_row(row)
    assert res["role_stability_score"] == 80.0
    assert "injury_role_pressure_detected" in res["role_stability_reasons"]

    # Float 1.0 -> -20 penalty
    row2 = _mock_row(injury_role_pressure=1.0)
    res2 = calculate_player_role_stability_row(row2)
    assert res2["role_stability_score"] == 80.0
    assert "injury_role_pressure_detected" in res2["role_stability_reasons"]


def test_report_renders_with_missing_optional_columns() -> None:
    # Construct a sparse DataFrame with missing optional columns entirely
    sparse_row = {
        "prediction_date": DATE,
        "player_name": "Sparse Player",
        "team": "BOS",
        "market_type": "player_points",
        "selection": "over",
        "minutes_avg": 30.0,
        "minutes_recent": 28.0,
    }
    df = pd.DataFrame([sparse_row])
    
    # Enrich and build
    payload = build_player_role_stability_report(DATE, full_market_df=df)
    assert payload["summary"]["total_rows_evaluated"] == 1
    assert payload["summary"]["stable_count"] == 1
    assert len(payload["rows"]) == 1
    
    row_data = payload["rows"][0]
    assert row_data["minutes_projection"] is None
    assert row_data["minutes_cv_recent"] is None
    assert row_data["manual_minutes_delta"] is None
    assert row_data["injury_role_pressure"] is None
    assert row_data["starter_or_rotation_status"] == "unknown"
    assert row_data["role_data_quality"] == "medium"
    assert row_data["role_stability_score"] == 100.0  # recent delta=2.0 -> no penalty, proj is missing

    # Render report text
    rendered = render_player_role_stability_report(payload)
    assert "Player Role Stability - Shadow Only" in rendered
    assert DIAGNOSTIC_ONLY_NOTE in rendered
    assert "Sparse Player" in rendered


def test_no_side_effects_on_production_boards(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    operator.mkdir(parents=True, exist_ok=True)
    history_root.mkdir(parents=True, exist_ok=True)

    # Write mock operator files (sentinels to prove report-only behavior)
    elite_path = operator / f"elite_board_{DATE}.csv"
    kelly_path = operator / f"kelly_stakes_{DATE}.csv"
    elite_path.write_text("player_name\nElite Sentinel\n", encoding="utf-8")
    kelly_path.write_text("player_name,stake_amount\nKelly Sentinel,15.5\n", encoding="utf-8")

    elite_before = elite_path.read_bytes()
    kelly_before = kelly_path.read_bytes()

    # Generate the Player Role Stability report
    df = pd.DataFrame([_mock_row()])
    json_path, txt_path, payload = write_player_role_stability_report(
        prediction_date=DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        full_market_df=df,
    )

    # Assert paths exist under temporary output directory
    assert json_path.exists()
    assert txt_path.exists()

    # Assert correct contents and notes
    assert DIAGNOSTIC_ONLY_NOTE in txt_path.read_text(encoding="utf-8")
    assert payload["summary"]["note"] == DIAGNOSTIC_ONLY_NOTE

    # Confirm original operator boards are completely untouched
    assert elite_path.read_bytes() == elite_before
    assert kelly_path.read_bytes() == kelly_before
