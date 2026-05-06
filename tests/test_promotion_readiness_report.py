from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.promotion_readiness import (
    OBSERVATION_ONLY_NOTE,
    REPORT_COLUMNS,
    build_promotion_readiness_report,
    write_promotion_readiness_report,
)
from scripts.write_daily_summary import write_daily_summary_outputs


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _shadow_row(
    *,
    market_type: str = "player_points_rebounds_assists",
    selection: str = "under",
    alignment: str = "aligned",
    caution: str = "low",
    result_status: str = "hit",
    shadow_roi: float = 0.9,
    prediction_date: str = "2026-05-06",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": "Sample Player",
        "player_id": "p1",
        "team_abbr": "BOS",
        "opponent": "PHI",
        "market_type": market_type,
        "selection": selection,
        "line": 36.5,
        "model_projection": 33.8,
        "edge": -2.7,
        "confidence": 0.68,
        "quality_score": 82.0,
        "selection_score": 76.0,
        "odds": -110,
        "line_source": "fixture_live_market",
        "context_pick_alignment": alignment,
        "context_caution_level": caution,
        "context_conflict_cause": "",
        "kelly_projected_skip_reason": "kelly_points_only_market_lock",
        "final_elite_rejection_reason": "market_not_elite_eligible",
        "result_status": result_status,
        "actual_value": "",
        "hit": result_status == "hit",
        "miss": result_status == "miss",
        "push": result_status == "push",
        "shadow_roi": shadow_roi,
        "calibration_eligible": False,
        "calibration_exclusion_reason": "",
    }


def _rows_for_bucket(
    *,
    hits: int,
    misses: int,
    market_type: str = "player_points_rebounds_assists",
    selection: str = "under",
    alignment: str = "aligned",
    caution: str = "low",
    prediction_date: str = "2026-05-06",
) -> list[dict]:
    rows = [
        _shadow_row(
            market_type=market_type,
            selection=selection,
            alignment=alignment,
            caution=caution,
            result_status="hit",
            shadow_roi=0.9,
            prediction_date=prediction_date,
        )
        for _ in range(hits)
    ]
    rows.extend(
        _shadow_row(
            market_type=market_type,
            selection=selection,
            alignment=alignment,
            caution=caution,
            result_status="miss",
            shadow_roi=-1.0,
            prediction_date=prediction_date,
        )
        for _ in range(misses)
    )
    return rows


def _report_row(report: pd.DataFrame, market_type: str, selection: str) -> pd.Series:
    match = report[(report["market_type"] == market_type) & (report["selection"] == selection)]
    assert len(match) == 1
    return match.iloc[0]


def test_promotion_readiness_report_is_generated(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _write_csv(
        history_root / "market_shadow_history.csv",
        _rows_for_bucket(hits=14, misses=6, prediction_date="2026-05-05"),
    )

    text_path, csv_path, report = write_promotion_readiness_report(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert text_path == runtime_root / "operator" / f"promotion_readiness_report_{prediction_date}.txt"
    assert csv_path == runtime_root / "operator" / f"promotion_readiness_report_{prediction_date}.csv"
    assert text_path.exists()
    assert csv_path.exists()
    assert list(pd.read_csv(csv_path).columns) == list(REPORT_COLUMNS)
    assert len(report) == 1
    assert "Promotion Readiness Report" in text_path.read_text(encoding="utf-8")


def test_insufficient_sample_buckets_are_blocked() -> None:
    report = build_promotion_readiness_report(pd.DataFrame(_rows_for_bucket(hits=10, misses=9)))
    row = report.iloc[0]
    assert row["graded_total"] == 19
    assert row["promotion_status"] == "blocked_insufficient_sample"
    assert row["promotion_reason"] == "graded_total_lt_20"


def test_promising_combo_under_aligned_low_bucket_becomes_near_candidate() -> None:
    report = build_promotion_readiness_report(pd.DataFrame(_rows_for_bucket(hits=14, misses=6)))
    row = report.iloc[0]
    assert row["graded_total"] == 20
    assert row["hit_rate"] == 0.7
    assert row["promotion_status"] == "near_candidate"
    assert row["promotion_reason"] == "combo_under_aligned_low_with_minimum_sample"


def test_combo_over_remains_blocked_even_with_high_hit_rate() -> None:
    report = build_promotion_readiness_report(
        pd.DataFrame(
            _rows_for_bucket(
                hits=25,
                misses=5,
                market_type="player_points_rebounds_assists",
                selection="over",
                alignment="aligned",
                caution="low",
            )
        )
    )
    row = report.iloc[0]
    assert row["graded_total"] == 30
    assert row["hit_rate"] == 0.8333
    assert row["promotion_status"] == "blocked_combo_over_review_required"
    assert row["promotion_reason"] == "combo_over_requires_explicit_review"


def test_daily_summary_includes_promotion_readiness_observation_section(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=["prediction_date", "player_name"])
    _write_csv(
        operator / f"kelly_stakes_{prediction_date}.csv",
        [],
        columns=["eligible", "stake_amount", "expected_value"],
    )
    _write_csv(
        operator / f"full_market_board_{prediction_date}.csv",
        [
            _shadow_row(),
            _shadow_row(market_type="player_points", selection="under"),
        ],
    )
    _write_csv(
        history_root / "market_shadow_history.csv",
        _rows_for_bucket(hits=14, misses=6, prediction_date="2026-05-05"),
    )
    diagnostics.mkdir(parents=True, exist_ok=True)
    (diagnostics / f"market_shadow_grading_{prediction_date}.json").write_text(
        json.dumps({"kelly_decision_performance": {"by_kelly_eligible": {"true": {}, "false": {}}}}),
        encoding="utf-8",
    )

    output_path, metadata = write_daily_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert "Promotion Readiness — Observation Only" in text
    assert OBSERVATION_ONLY_NOTE in text
    assert "near_candidate" in text
    assert metadata["promotion_readiness_report_count"] >= 1
    assert Path(metadata["promotion_readiness_report_path"]).exists()
    assert Path(metadata["promotion_readiness_report_csv_path"]).exists()


def test_promotion_readiness_does_not_modify_kelly_output_or_candidate_scoring(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    kelly_path = operator / f"kelly_stakes_{prediction_date}.csv"
    candidate_scoring_path = Path("courtvision/scoring/candidate_scoring.py")
    _write_csv(history_root / "market_shadow_history.csv", _rows_for_bucket(hits=14, misses=6))
    _write_csv(
        kelly_path,
        [{"player_name": "Kelly Sentinel", "market_type": "player_points", "eligible": True, "stake_amount": 10.0}],
    )
    kelly_before = kelly_path.read_bytes()
    candidate_scoring_before = candidate_scoring_path.read_bytes()

    write_promotion_readiness_report(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert kelly_path.read_bytes() == kelly_before
    assert candidate_scoring_path.read_bytes() == candidate_scoring_before
    kelly = pd.read_csv(kelly_path)
    assert set(kelly["market_type"]) == {"player_points"}
