from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.low_line_over_minutes_guard_outcome import (
    build_low_line_over_minutes_guard_outcome,
    select_readiness_verdict,
    write_low_line_over_minutes_guard_outcome,
)
from courtvision.reporting.quality_summary import write_quality_summary_outputs


def _row(
    player_id: int,
    *,
    minutes_basis: float | None,
    result_status: str,
    shadow_roi: float | None = None,
    line: float = 12.5,
    edge: float = 1.2,
    confidence: float = 0.7,
    quality_score: float = 55.0,
) -> dict:
    row = {
        "prediction_date": "2026-05-13",
        "player_id": player_id,
        "player_name": f"Player {player_id}",
        "market_type": "player_points",
        "selection": "over",
        "line": line,
        "edge": edge,
        "confidence": confidence,
        "quality_score": quality_score,
        "result_status": result_status,
        "shadow_roi": shadow_roi,
        "context_pick_alignment": "aligned",
        "context_caution_level": "low",
    }
    if minutes_basis is not None:
        row["minutes_basis"] = minutes_basis
    return row


def _bulk_rows() -> list[dict]:
    rows: list[dict] = []
    next_id = 1000
    for _ in range(10):
        rows.append(_row(next_id, minutes_basis=27.0, result_status="hit", shadow_roi=0.9091))
        next_id += 1
    for _ in range(25):
        rows.append(_row(next_id, minutes_basis=27.0, result_status="miss", shadow_roi=-1.0))
        next_id += 1
    for _ in range(25):
        rows.append(_row(next_id, minutes_basis=31.0, result_status="hit", shadow_roi=0.9091))
        next_id += 1
    for _ in range(10):
        rows.append(_row(next_id, minutes_basis=31.0, result_status="miss", shadow_roi=-1.0))
        next_id += 1
    return rows


def test_bucket_performance_calculated_correctly(tmp_path: Path) -> None:
    rows = [
        _row(1, minutes_basis=27.0, result_status="hit", shadow_roi=0.9091),
        _row(2, minutes_basis=27.0, result_status="miss", shadow_roi=-1.0),
        _row(3, minutes_basis=27.0, result_status="push", shadow_roi=0.0),
        _row(4, minutes_basis=27.0, result_status="void"),
        _row(5, minutes_basis=27.0, result_status="pending"),
        _row(6, minutes_basis=29.0, result_status="hit", shadow_roi=0.9091),
        _row(7, minutes_basis=31.0, result_status="miss", shadow_roi=-1.0),
        _row(8, minutes_basis=None, result_status="miss", shadow_roi=-1.0),
        _row(9, minutes_basis=24.0, result_status="hit", line=16.5, shadow_roi=0.9091),
    ]

    payload = build_low_line_over_minutes_guard_outcome(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(rows),
        paper_kelly_history=pd.DataFrame(),
        guard_review_csv=pd.DataFrame(),
    )

    weak = payload["bucket_performance"]["weak_minutes_basis"]
    assert weak["total_rows"] == 5
    assert weak["graded_rows"] == 4
    assert weak["hits"] == 1
    assert weak["misses"] == 1
    assert weak["pushes"] == 1
    assert weak["voids"] == 1
    assert weak["pending_rows_excluded"] == 1
    assert weak["hit_rate"] == 0.5
    assert weak["roi"] == -0.0303
    assert payload["bucket_performance"]["borderline_minutes_basis"]["hits"] == 1
    assert payload["bucket_performance"]["stable_minutes_basis"]["misses"] == 1
    assert payload["bucket_performance"]["missing_minutes_basis"]["misses"] == 1


def test_pending_open_rows_excluded_from_graded_metrics(tmp_path: Path) -> None:
    rows = [
        _row(1, minutes_basis=27.0, result_status="open"),
        _row(2, minutes_basis=27.0, result_status="pending"),
        _row(3, minutes_basis=27.0, result_status="hit", shadow_roi=0.9091),
    ]

    payload = build_low_line_over_minutes_guard_outcome(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(rows),
        paper_kelly_history=pd.DataFrame(),
        guard_review_csv=pd.DataFrame(),
    )

    weak = payload["bucket_performance"]["weak_minutes_basis"]
    assert weak["total_rows"] == 3
    assert weak["graded_rows"] == 1
    assert weak["pending_rows_excluded"] == 2
    assert weak["hit_rate"] == 1.0
    assert payload["pending_rows_excluded_from_metrics"] == 2


def test_voids_do_not_inflate_hit_rate_denominator(tmp_path: Path) -> None:
    rows = [
        _row(1, minutes_basis=31.0, result_status="hit", shadow_roi=0.9091),
        _row(2, minutes_basis=31.0, result_status="void"),
        _row(3, minutes_basis=31.0, result_status="void"),
    ]

    payload = build_low_line_over_minutes_guard_outcome(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(rows),
        paper_kelly_history=pd.DataFrame(),
        guard_review_csv=pd.DataFrame(),
    )

    stable = payload["bucket_performance"]["stable_minutes_basis"]
    assert stable["graded_rows"] == 3
    assert stable["voids"] == 2
    assert stable["hit_rate"] == 1.0


def test_weak_vs_stable_delta_and_underperformance_verdict(tmp_path: Path) -> None:
    payload = build_low_line_over_minutes_guard_outcome(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(_bulk_rows()),
        paper_kelly_history=pd.DataFrame(),
        guard_review_csv=pd.DataFrame(),
    )

    comparison = payload["comparison"]
    assert payload["bucket_performance"]["weak_minutes_basis"]["graded_rows"] == 35
    assert payload["bucket_performance"]["stable_minutes_basis"]["graded_rows"] == 35
    assert comparison["weak_hit_rate_minus_stable_hit_rate"] == -0.4286
    assert comparison["weak_underperformance_signal"] is True
    assert payload["readiness_verdict"] == "REVIEW_READY_WEAK_BUCKET_UNDERPERFORMS"


def test_insufficient_sample_verdict() -> None:
    assert (
        select_readiness_verdict(
            weak_graded_rows=29,
            stable_graded_rows=40,
            weak_underperformance_signal=True,
            missing_graded_rows=0,
            missing_hit_rate=None,
            missing_roi=None,
        )
        == "INSUFFICIENT_SAMPLE"
    )


def test_writer_outputs_json_text_and_csv_without_mutating_history(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    history_root.mkdir(parents=True)
    history_path = history_root / "market_shadow_history.csv"
    paper_path = history_root / "paper_kelly_history.csv"
    pd.DataFrame(_bulk_rows()).to_csv(history_path, index=False)
    pd.DataFrame([]).to_csv(paper_path, index=False)
    before_history = history_path.read_bytes()
    before_paper = paper_path.read_bytes()

    json_path, txt_path, csv_path, payload = write_low_line_over_minutes_guard_outcome(
        "2026-05-13",
        runtime_root=runtime_root,
        market_shadow_history=history_path,
        paper_kelly_history=paper_path,
        guard_review_csv=runtime_root / "operator" / "missing_review.csv",
    )

    assert history_path.read_bytes() == before_history
    assert paper_path.read_bytes() == before_paper
    assert json_path.exists()
    assert txt_path.exists()
    assert csv_path.exists()
    assert payload["history_mutated"] is False
    assert payload["live_picks_suppressed"] is False
    assert "LOW-LINE OVER MINUTES GUARD OUTCOME VALIDATION" in txt_path.read_text(encoding="utf-8")
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["readiness_verdict"] == "REVIEW_READY_WEAK_BUCKET_UNDERPERFORMS"


def test_quality_summary_integrates_phase_15e(tmp_path: Path) -> None:
    prediction_date = "2026-05-13"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    research = runtime_root / "research"
    diagnostics = runtime_root / "diagnostics"
    model = tmp_path / "model"
    for directory in (operator, research, diagnostics, history_root, model):
        directory.mkdir(parents=True, exist_ok=True)

    rows = _bulk_rows()
    first_row = rows[0] | {"team_abbr": "BOS", "selection_score": 55, "is_live_market": True}
    pd.DataFrame([first_row]).to_csv(operator / f"elite_board_{prediction_date}.csv", index=False)
    pd.DataFrame([first_row]).to_csv(operator / f"full_market_board_{prediction_date}.csv", index=False)
    pd.DataFrame([first_row]).to_csv(research / f"player_predictions_{prediction_date}.csv", index=False)
    pd.DataFrame(rows).to_csv(history_root / "market_shadow_history.csv", index=False)
    pd.DataFrame(rows).to_csv(history_root / "pick_history.csv", index=False)
    pd.DataFrame([]).to_csv(history_root / "paper_kelly_history.csv", index=False)
    pd.DataFrame([]).to_csv(operator / f"kelly_stakes_{prediction_date}.csv", index=False)
    pd.DataFrame([]).to_csv(operator / f"sgp_board_{prediction_date}.csv", index=False)
    pd.DataFrame([{"player_id": 1000, "player_name": "Player 1000", "team_abbr": "BOS", "min_avg": 27}]).to_csv(
        model / "player_baselines.csv",
        index=False,
    )
    (research / f"model_metrics_{prediction_date}.json").write_text("{}", encoding="utf-8")
    (diagnostics / f"board_diagnostics_{prediction_date}.json").write_text("{}", encoding="utf-8")
    (operator / f"elite_pipeline_audit_summary_{prediction_date}.json").write_text("{}", encoding="utf-8")

    shadow_before = (history_root / "market_shadow_history.csv").read_bytes()
    pick_before = (history_root / "pick_history.csv").read_bytes()
    paper_before = (history_root / "paper_kelly_history.csv").read_bytes()
    text_path, json_path, payload = write_quality_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        history_root=history_root,
    )

    assert (history_root / "market_shadow_history.csv").read_bytes() == shadow_before
    assert (history_root / "pick_history.csv").read_bytes() == pick_before
    assert (history_root / "paper_kelly_history.csv").read_bytes() == paper_before
    assert "low_line_over_minutes_guard_outcome" in payload
    outcome = payload["low_line_over_minutes_guard_outcome"]
    assert outcome["note"] == "review_only_no_prediction_grading_kelly_history_or_suppression_change"
    assert outcome["weak_graded_rows"] == 35
    assert outcome["stable_graded_rows"] == 35
    assert outcome["weak_hit_rate_minus_stable_hit_rate"] == -0.4286
    assert Path(outcome["json_path"]).exists()
    assert Path(outcome["csv_path"]).exists()
    text = text_path.read_text(encoding="utf-8")
    assert "LOW-LINE OVER MINUTES GUARD OUTCOME VALIDATION (Phase 15E -- REVIEW ONLY)" in text
    assert "NOTE: REVIEW ONLY; no prediction/grading/Kelly/history changes and no picks suppressed." in text
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert "low_line_over_minutes_guard_outcome" in saved
