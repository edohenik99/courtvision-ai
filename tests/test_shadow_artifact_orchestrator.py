from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.artifact_manifest import (
    SEVERITY_SHADOW_ONLY,
    build_artifact_manifest,
)
from courtvision.reporting.quality_summary import write_quality_summary_outputs
from courtvision.reporting import shadow_artifact_orchestrator as orchestrator
from scripts import refresh_closed_slate_reports
from scripts.write_daily_summary import build_daily_summary
from scripts.write_operator_card import write_operator_card_outputs


PREDICTION_DATE = "2026-05-24"


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _market_row() -> dict:
    return {
        "prediction_date": PREDICTION_DATE,
        "player_name": "Fixture Player",
        "player_id": "player-1",
        "team": "BOS",
        "team_abbr": "BOS",
        "opponent": "NYK",
        "game_id": "game-1",
        "market_type": "player_points",
        "selection": "over",
        "line": 20.5,
        "sportsbook_line": 20.5,
        "odds": -110,
        "edge": 2.0,
        "confidence": 0.72,
        "quality_score": 80.0,
        "context_pick_alignment": "aligned",
        "context_caution_level": "low",
    }


def _seed_core_operator_inputs(runtime_root: Path, history_root: Path) -> None:
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    row = _market_row()
    _write_csv(operator / f"elite_board_{PREDICTION_DATE}.csv", [row])
    _write_csv(operator / f"full_market_board_{PREDICTION_DATE}.csv", [row])
    _write_csv(operator / f"sgp_board_{PREDICTION_DATE}.csv", [], columns=["prediction_date"])
    _write_json(diagnostics / f"board_diagnostics_{PREDICTION_DATE}.json", {"board_counts": {}})
    _write_json(operator / f"elite_pipeline_audit_summary_{PREDICTION_DATE}.json", {})
    _write_json(
        operator / f"quality_summary_{PREDICTION_DATE}.json",
        {
            "run_health_status": "HEALTHY",
            "run_health_reason": "fixture",
            "slate_provider_counts": {
                "games_count": 1,
                "normalized_odds_rows_count": 1,
                "stale_odds_count": 0,
                "provider_breakdown": {"line_source": {"fixture": 1}},
            },
            "candidate_funnel": {
                "elite_board_count": 1,
                "full_market_board_count": 1,
                "sgp_board_count": 0,
            },
            "kelly_safety_summary": {
                "total_rows": 0,
                "kelly_eligible_count": 0,
                "review_before_bet_count": 0,
                "review_policy_hold_count": 0,
            },
            "manual_review_required_count": 0,
            "same_opponent_under_warning_count": 0,
            "date_isolation_check": {"status": "ok"},
        },
    )
    _write_csv(
        history_root / "pick_history.csv",
        [],
        columns=["prediction_date", "result_status"],
    )
    _write_csv(
        history_root / "market_shadow_history.csv",
        [],
        columns=["prediction_date", "result_status"],
    )


def test_orchestrator_calls_all_writers_in_order_and_continues_after_failure(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls: list[str] = []

    def ok_writer(name: str, payload: dict):
        def _writer(**kwargs):  # noqa: ANN003
            calls.append(name)
            if name == "meta":
                assert kwargs["role_payload"] == {"summary": {"role": "ok"}}
                assert kwargs["cal_payload"] is None
            return (
                tmp_path / f"{name}.json",
                tmp_path / f"{name}.txt",
                payload,
            )

        return _writer

    def ok_csv_writer(name: str, payload: dict):
        def _writer(**kwargs):  # noqa: ANN003
            calls.append(name)
            return (
                tmp_path / f"{name}.json",
                tmp_path / f"{name}.txt",
                tmp_path / f"{name}.csv",
                payload,
            )

        return _writer

    def failing_calibration(**kwargs):  # noqa: ANN003
        calls.append("calibration")
        raise RuntimeError("calibration exploded")

    monkeypatch.setattr(
        orchestrator,
        "write_clv_market_movement_report",
        ok_writer("clv", {"summary": {"clv": "ok"}}),
    )
    monkeypatch.setattr(orchestrator, "write_calibration_bucket_report", failing_calibration)
    monkeypatch.setattr(
        orchestrator,
        "write_player_role_stability_report",
        ok_writer("role", {"summary": {"role": "ok"}}),
    )
    monkeypatch.setattr(
        orchestrator,
        "write_meta_label_promotion_report",
        ok_csv_writer("meta", {"summary": {"meta": "ok"}}),
    )
    monkeypatch.setattr(
        orchestrator,
        "write_rules_performance_report",
        ok_csv_writer("rules", {"data_readiness": {"rules": "ok"}}),
    )
    monkeypatch.setattr(
        orchestrator,
        "write_feature_completeness_report",
        ok_csv_writer("tracker", {"readiness": {"tracker": "ok"}}),
    )

    summary = orchestrator.write_shadow_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
    )

    assert calls == ["clv", "calibration", "role", "meta", "rules", "tracker"]
    statuses = {report["report_name"]: report["status"] for report in summary["reports"]}
    assert statuses == {
        "clv_market_movement": "written",
        "calibration_bucket_report": "failed",
        "player_role_stability": "written",
        "meta_label_promotion": "written",
        "meta_label_rules_performance": "written",
        "feature_completeness_tracker": "written",
    }
    assert summary["failed_count"] == 1
    captured = capsys.readouterr()
    assert "calibration_bucket_report failed: calibration exploded" in captured.err
    assert "Traceback" in captured.err


def test_closed_slate_refresh_runs_shadow_artifacts_before_daily_summary() -> None:
    commands = refresh_closed_slate_reports.build_refresh_commands(
        prediction_date=PREDICTION_DATE
    )
    labels = [command.label for command in commands]

    assert labels.index("shadow_artifacts") < labels.index("daily_summary")
    by_label = {command.label: command.command for command in commands}
    assert by_label["shadow_artifacts"][2] == "scripts/write_shadow_artifacts.py"
    assert "--closed-slate-safe" in by_label["shadow_artifacts"]


def test_daily_summary_missing_shadow_artifacts_are_unavailable_not_clean_zero(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _seed_core_operator_inputs(runtime_root, history_root)

    text, _payload = build_daily_summary(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert "Calibration Health - Shadow Only" in text
    assert "- status: unavailable/stale" in text
    assert f"calibration_bucket_report_{PREDICTION_DATE}.json" in text
    assert "- total graded rows used: 0" not in text


def test_operator_card_missing_shadow_artifacts_are_unavailable_not_clean_zero(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _seed_core_operator_inputs(runtime_root, history_root)

    output_path, payload = write_operator_card_outputs(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert "CLV / Market Movement - Shadow Only" in text
    assert "- status: unavailable/stale" in text
    assert f"clv_market_movement_{PREDICTION_DATE}.json" in text
    assert "- close coverage count: 0 / 0" not in text
    assert payload["final_decision"] == "BETTABLE"


def test_quality_summary_reads_existing_feature_completeness_artifact(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _seed_core_operator_inputs(runtime_root, history_root)
    _write_json(
        runtime_root / "diagnostics" / f"feature_completeness_tracker_{PREDICTION_DATE}.json",
        {
            "historical_coverage": {
                "completed_slate_count": 5,
                "graded_hit_miss_rows": 150,
                "feature_complete_graded_rows": 120,
            },
            "readiness": {
                "verdict": "FEATURE_COLLECTION_HEALTHY_BUT_SAMPLE_SMALL",
                "estimated_additional_slates_needed": 25,
            },
        },
    )

    text_path, _json_path, payload = write_quality_summary_outputs(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        write_board_annotations=False,
    )

    text = text_path.read_text(encoding="utf-8")
    assert "Feature Completeness Tracker - Shadow Only" in text
    feature_section = text.split("Feature Completeness Tracker - Shadow Only", 1)[1]
    feature_section = feature_section.split("\n\n", 1)[0]
    assert "- completed_slate_count: 5" in feature_section
    assert "- not available" not in feature_section
    assert payload["feature_completeness_tracker_shadow"]["status"] == "written"


def test_artifact_manifest_keeps_phase4b_shadow_artifacts_nonfatal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_csv(
        runtime_root / "operator" / f"elite_board_{PREDICTION_DATE}.csv",
        [],
        columns=["prediction_date"],
    )
    _write_csv(
        runtime_root / "operator" / f"full_market_board_{PREDICTION_DATE}.csv",
        [],
        columns=["prediction_date"],
    )
    _write_csv(
        runtime_root / "operator" / f"sgp_board_{PREDICTION_DATE}.csv",
        [],
        columns=["prediction_date"],
    )

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
    )
    by_name = {artifact["name"]: artifact for artifact in manifest["artifacts"]}

    assert by_name["feature_completeness_tracker_json"]["severity"] == SEVERITY_SHADOW_ONLY
    assert by_name["clv_market_movement_diagnostics"]["severity"] == SEVERITY_SHADOW_ONLY
    assert manifest["missing_by_severity"]["fatal"] == 0
