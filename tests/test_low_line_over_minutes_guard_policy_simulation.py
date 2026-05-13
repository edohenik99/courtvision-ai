from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.low_line_over_minutes_guard_policy_simulation import (
    build_low_line_over_minutes_guard_policy_simulation,
    select_policy_risk_verdict,
    write_low_line_over_minutes_guard_policy_simulation,
)
from courtvision.reporting.quality_summary import write_quality_summary_outputs


def _bucket_for_basis(minutes_basis: float | None) -> str:
    if minutes_basis is None:
        return "missing_minutes_basis"
    if minutes_basis < 28:
        return "weak_minutes_basis"
    if minutes_basis < 30:
        return "borderline_minutes_basis"
    return "stable_minutes_basis"


def _row(
    player_id: int,
    *,
    minutes_basis: float | None,
    result_status: str,
    shadow_roi: float | None = None,
    line: float = 12.5,
) -> dict:
    row = {
        "prediction_date": "2026-05-13",
        "player_id": player_id,
        "player_name": f"Player {player_id}",
        "market_type": "player_points",
        "selection": "over",
        "line": line,
        "minutes_guard_review_bucket": _bucket_for_basis(minutes_basis),
        "minutes_basis": minutes_basis,
        "edge": 1.4,
        "confidence": 0.72,
        "quality_score": 56.0,
        "result_status": result_status,
        "row_roi": shadow_roi,
    }
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


def test_policy_simulation_calculates_suppressed_and_kept_rows(tmp_path: Path) -> None:
    payload = build_low_line_over_minutes_guard_policy_simulation(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(),
        paper_kelly_history=pd.DataFrame(),
        outcome_csv=pd.DataFrame(_bulk_rows()),
    )

    weak_policy = payload["policy_results"]["suppress_weak_minutes_basis"]
    assert weak_policy["total_candidates"] == 70
    assert weak_policy["suppressed_rows"] == 35
    assert weak_policy["kept_rows"] == 35
    assert weak_policy["suppressed_graded_rows"] == 35
    assert weak_policy["kept_graded_rows"] == 35
    assert weak_policy["volume_reduction_pct"] == 0.5


def test_pending_rows_are_excluded_from_graded_metrics(tmp_path: Path) -> None:
    rows = [
        _row(1, minutes_basis=27.0, result_status="miss", shadow_roi=-1.0),
        _row(2, minutes_basis=27.0, result_status="pending"),
        _row(3, minutes_basis=31.0, result_status="hit", shadow_roi=0.9091),
    ]

    payload = build_low_line_over_minutes_guard_policy_simulation(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(),
        paper_kelly_history=pd.DataFrame(),
        outcome_csv=pd.DataFrame(rows),
    )

    weak_policy = payload["policy_results"]["suppress_weak_minutes_basis"]
    assert weak_policy["suppressed_rows"] == 2
    assert weak_policy["suppressed_graded_rows"] == 1
    assert weak_policy["suppressed_misses"] == 1
    assert weak_policy["saved_losers"] == 1


def test_voids_do_not_inflate_hit_rate_denominator(tmp_path: Path) -> None:
    rows = [
        _row(1, minutes_basis=27.0, result_status="hit", shadow_roi=0.9091),
        _row(2, minutes_basis=27.0, result_status="void"),
        _row(3, minutes_basis=27.0, result_status="void"),
        _row(4, minutes_basis=31.0, result_status="miss", shadow_roi=-1.0),
    ]

    payload = build_low_line_over_minutes_guard_policy_simulation(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(),
        paper_kelly_history=pd.DataFrame(),
        outcome_csv=pd.DataFrame(rows),
    )

    weak_policy = payload["policy_results"]["suppress_weak_minutes_basis"]
    assert weak_policy["suppressed_graded_rows"] == 3
    assert weak_policy["suppressed_voids"] == 2
    assert weak_policy["suppressed_hit_rate"] == 1.0


def test_saved_losers_missed_winners_and_hit_rate_delta(tmp_path: Path) -> None:
    payload = build_low_line_over_minutes_guard_policy_simulation(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(),
        paper_kelly_history=pd.DataFrame(),
        outcome_csv=pd.DataFrame(_bulk_rows()),
    )

    weak_policy = payload["policy_results"]["suppress_weak_minutes_basis"]
    assert weak_policy["saved_losers"] == 25
    assert weak_policy["missed_winners"] == 10
    assert weak_policy["net_saved_result_count"] == 15
    assert weak_policy["baseline_hit_rate"] == 0.5
    assert weak_policy["kept_hit_rate"] == 0.7143
    assert weak_policy["hit_rate_delta"] == 0.2143
    assert weak_policy["risk_verdict"] == "POLICY_SIM_REVIEW_READY"


def test_insufficient_sample_verdict() -> None:
    assert (
        select_policy_risk_verdict(
            policy_name="suppress_weak_minutes_basis",
            suppressed_graded_rows=29,
            suppressed_hits=4,
            suppressed_misses=25,
            hit_rate_delta=0.1,
            roi_delta=0.1,
        )
        == "INSUFFICIENT_SAMPLE"
    )


def test_review_ready_verdict() -> None:
    assert (
        select_policy_risk_verdict(
            policy_name="suppress_weak_minutes_basis",
            suppressed_graded_rows=35,
            suppressed_hits=10,
            suppressed_misses=25,
            hit_rate_delta=0.2143,
            roi_delta=0.2,
        )
        == "POLICY_SIM_REVIEW_READY"
    )


def test_writer_outputs_artifacts_without_mutating_history(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    history_root.mkdir(parents=True)
    history_path = history_root / "market_shadow_history.csv"
    paper_path = history_root / "paper_kelly_history.csv"
    pd.DataFrame(_bulk_rows()).to_csv(history_path, index=False)
    pd.DataFrame([]).to_csv(paper_path, index=False)
    before_history = history_path.read_bytes()
    before_paper = paper_path.read_bytes()

    json_path, txt_path, csv_path, payload = write_low_line_over_minutes_guard_policy_simulation(
        "2026-05-13",
        runtime_root=runtime_root,
        market_shadow_history=history_path,
        paper_kelly_history=paper_path,
        outcome_csv=pd.DataFrame(_bulk_rows()),
    )

    assert history_path.read_bytes() == before_history
    assert paper_path.read_bytes() == before_paper
    assert json_path.exists()
    assert txt_path.exists()
    assert csv_path.exists()
    assert payload["history_mutated"] is False
    assert payload["live_picks_suppressed"] is False
    assert "LOW-LINE OVER MINUTES GUARD POLICY SIMULATION" in txt_path.read_text(encoding="utf-8")
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["readiness_verdict"] == "POLICY_SIM_REVIEW_READY"


def test_quality_summary_section_renders_and_history_is_not_mutated(tmp_path: Path) -> None:
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
    first_row = rows[0] | {"team_abbr": "BOS", "selection_score": 56, "is_live_market": True}
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
    assert "low_line_over_minutes_guard_policy_simulation" in payload
    sim = payload["low_line_over_minutes_guard_policy_simulation"]
    assert sim["note"] == "simulation_only_no_prediction_grading_kelly_history_or_suppression_change"
    assert sim["best_policy_name"] == "suppress_weak_minutes_basis"
    assert sim["saved_losers"] == 25
    assert sim["missed_winners"] == 10
    assert Path(sim["json_path"]).exists()
    assert Path(sim["csv_path"]).exists()
    text = text_path.read_text(encoding="utf-8")
    assert "LOW-LINE OVER MINUTES GUARD POLICY SIMULATION (Phase 15F -- SIMULATION ONLY)" in text
    assert "NOTE: SIMULATION ONLY; no prediction/grading/Kelly/history changes and no picks suppressed." in text
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert "low_line_over_minutes_guard_policy_simulation" in saved
