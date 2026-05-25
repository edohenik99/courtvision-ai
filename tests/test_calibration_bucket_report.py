from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.calibration_bucket_report import (
    DIAGNOSTIC_ONLY_NOTE,
    build_calibration_bucket_report,
    render_calibration_bucket_report,
    write_calibration_bucket_report,
)


DATE = "2026-05-25"


def _row(
    idx: int,
    *,
    market_type: str = "player_points",
    selection: str = "over",
    result_status: str = "hit",
    confidence: float = 0.8,
    edge: float = 2.5,
    odds: int = -110,
    quality_score: float = 85.0,
    clv_line_points: float | None = 0.5,
    clv_grade: str | None = "positive",
) -> dict:
    row = {
        "prediction_date": DATE,
        "player_name": f"Fixture Player {idx}",
        "player_id": f"player-{idx}",
        "game_id": f"game-{idx % 3}",
        "market_type": market_type,
        "selection": selection,
        "line": 20.5 + idx,
        "result_status": result_status,
        "confidence": confidence,
        "edge": edge,
        "odds": odds,
        "quality_score": quality_score,
        "close_coverage_status": "observed" if clv_line_points is not None else "missing",
        "movement_toward_pick": clv_line_points is not None and clv_line_points > 0,
        "context_pick_alignment": "aligned",
        "context_caution_level": "low",
        "same_opponent_under_warning": False,
        "high_caution_over": False,
    }
    if clv_line_points is not None:
        row["clv_line_points"] = clv_line_points
    if clv_grade is not None:
        row["clv_grade"] = clv_grade
    return row


def _find_row(payload: dict, *, dimension: str, bucket: str, market: str = "player_points", side: str = "over") -> dict:
    matches = [
        row for row in payload["rows"]
        if row["bucket_dimension"] == dimension
        and row["bucket_value"] == bucket
        and row["market_type"] == market
        and row["selection"] == side
    ]
    assert matches, f"missing row dimension={dimension} bucket={bucket} market={market} side={side}"
    return matches[0]


def test_bucket_grouping_works_by_market_side_confidence_edge_and_clv() -> None:
    shadow = pd.DataFrame(
        [
            _row(1, result_status="hit", confidence=0.82, edge=2.5, clv_grade="positive"),
            _row(2, result_status="miss", confidence=0.64, edge=-2.0, clv_line_points=-0.5, clv_grade="negative"),
            _row(3, market_type="player_rebounds", selection="under", result_status="hit", confidence=0.75, edge=3.5),
        ]
    )

    payload = build_calibration_bucket_report(prediction_date=DATE, shadow_history_df=shadow)

    assert _find_row(payload, dimension="market_type", bucket="player_points")["n"] == 2
    assert _find_row(payload, dimension="selection", bucket="over")["n"] == 2
    assert _find_row(payload, dimension="confidence_bucket", bucket="0.80+")["hits"] == 1
    assert _find_row(payload, dimension="edge_bucket", bucket="negative_1_to_3")["misses"] == 1
    assert _find_row(payload, dimension="abs_edge_bucket", bucket="2-3")["n"] == 2
    assert _find_row(payload, dimension="clv_grade", bucket="positive")["hits"] == 1
    assert _find_row(payload, dimension="clv_grade", bucket="negative")["misses"] == 1


def test_pending_void_unsupported_and_pushes_are_excluded_from_hit_rate() -> None:
    statuses = ["hit", "miss", "push", "pending", "void", "unsupported"]
    shadow = pd.DataFrame([_row(idx, result_status=status) for idx, status in enumerate(statuses, start=1)])

    payload = build_calibration_bucket_report(prediction_date=DATE, shadow_history_df=shadow)
    row = _find_row(payload, dimension="market_type", bucket="player_points")

    assert row["hits"] == 1
    assert row["misses"] == 1
    assert row["pushes"] == 1
    assert row["pending"] == 1
    assert row["void"] == 1
    assert row["unsupported"] == 1
    assert row["graded_n"] == 2
    assert row["hit_rate"] == 0.5


def test_brier_score_calculation_is_correct() -> None:
    shadow = pd.DataFrame(
        [
            _row(1, result_status="hit", confidence=0.8),
            _row(2, result_status="miss", confidence=0.6),
            _row(3, result_status="push", confidence=0.95),
        ]
    )

    payload = build_calibration_bucket_report(prediction_date=DATE, shadow_history_df=shadow)
    row = _find_row(payload, dimension="market_type", bucket="player_points")

    assert row["graded_n"] == 2
    assert row["brier_score"] == 0.2
    assert row["avg_confidence"] == 0.7
    assert row["calibration_gap"] == -0.2


def test_small_sample_statuses_are_correct() -> None:
    rows: list[dict] = []
    rows.extend(_row(idx, market_type="tiny_market") for idx in range(1, 10))
    rows.extend(_row(idx, market_type="small_market") for idx in range(10, 20))
    rows.extend(_row(idx, market_type="developing_market") for idx in range(20, 50))
    rows.extend(_row(idx, market_type="mature_market") for idx in range(50, 150))

    payload = build_calibration_bucket_report(prediction_date=DATE, shadow_history_df=pd.DataFrame(rows))

    assert _find_row(payload, dimension="market_type", bucket="tiny_market", market="tiny_market")["sample_status"] == "tiny_sample"
    assert _find_row(payload, dimension="market_type", bucket="small_market", market="small_market")["sample_status"] == "small_sample"
    assert _find_row(payload, dimension="market_type", bucket="developing_market", market="developing_market")["sample_status"] == "developing_sample"
    assert _find_row(payload, dimension="market_type", bucket="mature_market", market="mature_market")["sample_status"] == "mature_sample"


def test_missing_clv_fields_do_not_crash_report() -> None:
    shadow = pd.DataFrame(
        [
            {
                "prediction_date": DATE,
                "player_name": "No CLV",
                "market_type": "player_points",
                "selection": "over",
                "line": 20.5,
                "result_status": "hit",
                "confidence": 0.8,
                "edge": 2.0,
                "odds": -110,
                "quality_score": 80,
            }
        ]
    )

    payload = build_calibration_bucket_report(prediction_date=DATE, shadow_history_df=shadow)
    row = _find_row(payload, dimension="clv_grade", bucket="missing")

    assert row["n"] == 1
    assert row["avg_clv_line_points"] is None
    assert row["positive_clv_rate"] is None
    assert row["coverage_status"] == "missing_clv"


def test_report_outputs_render_correctly_and_do_not_alter_operator_boards(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    shadow = pd.DataFrame([_row(1), _row(2, result_status="miss", confidence=0.6)])
    history_root.mkdir(parents=True, exist_ok=True)
    shadow.to_csv(history_root / "market_shadow_history.csv", index=False)
    pd.DataFrame([]).to_csv(history_root / "pick_history.csv", index=False)

    elite_path = operator / f"elite_board_{DATE}.csv"
    kelly_path = operator / f"kelly_stakes_{DATE}.csv"
    elite_path.parent.mkdir(parents=True, exist_ok=True)
    elite_path.write_text("player_name\nElite Sentinel\n", encoding="utf-8")
    kelly_path.write_text("player_name,stake_amount\nKelly Sentinel,10\n", encoding="utf-8")
    elite_before = elite_path.read_bytes()
    kelly_before = kelly_path.read_bytes()

    json_path, txt_path, payload = write_calibration_bucket_report(
        prediction_date=DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert json_path == runtime_root / "diagnostics" / f"calibration_bucket_report_{DATE}.json"
    assert txt_path == runtime_root / "operator" / f"calibration_bucket_report_{DATE}.txt"
    assert json_path.exists()
    assert txt_path.exists()
    assert DIAGNOSTIC_ONLY_NOTE in txt_path.read_text(encoding="utf-8")
    assert DIAGNOSTIC_ONLY_NOTE in render_calibration_bucket_report(payload)
    assert json.loads(json_path.read_text(encoding="utf-8"))["prediction_date"] == DATE
    assert elite_path.read_bytes() == elite_before
    assert kelly_path.read_bytes() == kelly_before
