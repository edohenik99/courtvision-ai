from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.write_learning_artifacts import (
    FINAL_STATUS_BLOCKED_BY_UNSAFE_PROPOSALS,
    FINAL_STATUS_DRY_RUN,
    FINAL_STATUS_FAILED,
    FINAL_STATUS_READY,
    FINAL_STATUS_READY_WITH_WARNINGS,
    orchestrate_learning_artifacts,
)

PREDICTION_DATE = "2026-05-30"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_text(path: Path, text: str = "report\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _arg(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def _write_outputs_for_command(
    command: list[str],
    *,
    warnings_by_script: dict[str, list[str]] | None = None,
    active_proposal_count: int = 0,
    production_effect_count: int = 0,
) -> None:
    script = Path(command[1]).name
    runtime_root = Path(_arg(command, "--runtime-root"))
    prediction_date = _arg(command, "--prediction-date")
    warnings = (warnings_by_script or {}).get(script, [])

    if script == "write_learning_brain_report.py":
        _write_text(runtime_root / "operator" / f"learning_brain_report_{prediction_date}.txt")
        _write_json(
            runtime_root / "diagnostics" / f"learning_brain_report_{prediction_date}.json",
            {
                "status": "LEARNING_HEALTHY",
                "prediction_date": prediction_date,
                "data_quality_warnings": warnings,
            },
        )
    elif script == "write_shadow_rule_proposals.py":
        _write_text(runtime_root / "operator" / f"shadow_rule_proposals_{prediction_date}.txt")
        _write_json(
            runtime_root / "diagnostics" / f"shadow_rule_proposals_{prediction_date}.json",
            {
                "status": "SHADOW_RULE_PROPOSALS_READY",
                "prediction_date": prediction_date,
                "proposals": [],
                "active_proposal_count": 0,
                "production_effect_count": 0,
                "data_quality_warnings": warnings,
            },
        )
    elif script == "write_learning_integration_snapshot.py":
        _write_text(runtime_root / "operator" / f"learning_integration_snapshot_{prediction_date}.txt")
        _write_json(
            runtime_root / "diagnostics" / f"learning_integration_snapshot_{prediction_date}.json",
            {
                "status": "LEARNING_INTEGRATION_READY",
                "prediction_date": prediction_date,
                "learning_brain_status": "LEARNING_HEALTHY",
                "shadow_rule_proposal_status": "SHADOW_RULE_PROPOSALS_READY",
                "total_proposals": 0,
                "active_proposal_count": active_proposal_count,
                "production_effect_count": production_effect_count,
                "data_quality_warnings": warnings,
            },
        )


def _runner_factory(
    *,
    fail_script: str | None = None,
    warnings_by_script: dict[str, list[str]] | None = None,
    active_proposal_count: int = 0,
    production_effect_count: int = 0,
) -> tuple[Any, list[list[str]]]:
    calls: list[list[str]] = []

    def _runner(command: list[str], **kwargs) -> subprocess.CompletedProcess:  # noqa: ANN003
        calls.append(command)
        script = Path(command[1]).name
        if script == fail_script:
            return subprocess.CompletedProcess(command, 2, stdout="", stderr=f"{script} failed")
        _write_outputs_for_command(
            command,
            warnings_by_script=warnings_by_script,
            active_proposal_count=active_proposal_count,
            production_effect_count=production_effect_count,
        )
        return subprocess.CompletedProcess(command, 0, stdout=f"{script} ok\n", stderr="")

    return _runner, calls


def _run_success(tmp_path: Path) -> tuple[int, dict[str, Any]]:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    runner, _calls = _runner_factory()
    return orchestrate_learning_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        runner=runner,
        printer=lambda _line: None,
    )


def test_dry_run_prints_all_three_commands_and_does_not_execute(tmp_path: Path) -> None:
    runner_called = False

    def _runner(command: list[str], **kwargs) -> subprocess.CompletedProcess:  # noqa: ANN003
        nonlocal runner_called
        runner_called = True
        return subprocess.CompletedProcess(command, 0)

    printed: list[str] = []
    exit_code, summary = orchestrate_learning_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        dry_run=True,
        runner=_runner,
        printer=printed.append,
    )

    output = "\n".join(printed)
    assert exit_code == 0
    assert summary["final_status"] == FINAL_STATUS_DRY_RUN
    assert not runner_called
    assert "write_learning_brain_report.py" in output
    assert "write_shadow_rule_proposals.py" in output
    assert "write_learning_integration_snapshot.py" in output


def test_successful_three_step_run_returns_ready(tmp_path: Path) -> None:
    runner, calls = _runner_factory()

    exit_code, summary = orchestrate_learning_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        runner=runner,
        printer=lambda _line: None,
    )

    assert exit_code == 0
    assert summary["final_status"] == FINAL_STATUS_READY
    assert [Path(call[1]).name for call in calls] == [
        "write_learning_brain_report.py",
        "write_shadow_rule_proposals.py",
        "write_learning_integration_snapshot.py",
    ]


def test_missing_optional_artifacts_create_warning_not_crash(tmp_path: Path) -> None:
    runner, _calls = _runner_factory(
        warnings_by_script={
            "write_learning_integration_snapshot.py": [
                "missing optional text report: operator_card_text",
            ]
        }
    )

    exit_code, summary = orchestrate_learning_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        runner=runner,
        printer=lambda _line: None,
    )

    assert exit_code == 0
    assert summary["final_status"] == FINAL_STATUS_READY_WITH_WARNINGS
    assert any("missing optional text report" in warning for warning in summary["warnings"])


def test_failed_step_returns_ready_with_warnings_by_default(tmp_path: Path) -> None:
    runner, _calls = _runner_factory(fail_script="write_shadow_rule_proposals.py")

    exit_code, summary = orchestrate_learning_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        runner=runner,
        printer=lambda _line: None,
    )

    assert exit_code == 0
    assert summary["final_status"] == FINAL_STATUS_READY_WITH_WARNINGS
    assert summary["failed_step_count"] == 1


def test_failed_step_exits_nonzero_in_strict_mode(tmp_path: Path) -> None:
    runner, _calls = _runner_factory(fail_script="write_shadow_rule_proposals.py")

    exit_code, summary = orchestrate_learning_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        strict=True,
        runner=runner,
        printer=lambda _line: None,
    )

    assert exit_code == 1
    assert summary["final_status"] == FINAL_STATUS_FAILED
    assert summary["failed_step_count"] == 1


@pytest.mark.parametrize(
    ("active_count", "production_count"),
    [
        (1, 0),
        (0, 1),
    ],
)
def test_unsafe_proposal_count_blocks_final_status(
    tmp_path: Path,
    active_count: int,
    production_count: int,
) -> None:
    runner, _calls = _runner_factory(
        active_proposal_count=active_count,
        production_effect_count=production_count,
    )

    exit_code, summary = orchestrate_learning_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        runner=runner,
        printer=lambda _line: None,
    )

    assert exit_code == 1
    assert summary["final_status"] == FINAL_STATUS_BLOCKED_BY_UNSAFE_PROPOSALS


def test_summary_json_contains_applied_changes_false(tmp_path: Path) -> None:
    _exit_code, summary = _run_success(tmp_path)
    persisted = json.loads(Path(summary["summary_artifact_paths"]["json"]).read_text(encoding="utf-8"))
    assert persisted["applied_changes"] is False


def test_summary_json_contains_live_rules_modified_false(tmp_path: Path) -> None:
    _exit_code, summary = _run_success(tmp_path)
    persisted = json.loads(Path(summary["summary_artifact_paths"]["json"]).read_text(encoding="utf-8"))
    assert persisted["live_rules_modified"] is False


def test_summary_json_contains_elite_logic_modified_false(tmp_path: Path) -> None:
    _exit_code, summary = _run_success(tmp_path)
    persisted = json.loads(Path(summary["summary_artifact_paths"]["json"]).read_text(encoding="utf-8"))
    assert persisted["elite_logic_modified"] is False


def test_summary_json_contains_kelly_logic_modified_false(tmp_path: Path) -> None:
    _exit_code, summary = _run_success(tmp_path)
    persisted = json.loads(Path(summary["summary_artifact_paths"]["json"]).read_text(encoding="utf-8"))
    assert persisted["kelly_logic_modified"] is False


def test_summary_json_contains_final_decision_modified_false(tmp_path: Path) -> None:
    _exit_code, summary = _run_success(tmp_path)
    persisted = json.loads(Path(summary["summary_artifact_paths"]["json"]).read_text(encoding="utf-8"))
    assert persisted["final_decision_modified"] is False


def test_summary_json_contains_pick_history_modified_false(tmp_path: Path) -> None:
    _exit_code, summary = _run_success(tmp_path)
    persisted = json.loads(Path(summary["summary_artifact_paths"]["json"]).read_text(encoding="utf-8"))
    assert persisted["pick_history_modified"] is False


def test_summary_json_contains_generated_real_money_recommendations_false(tmp_path: Path) -> None:
    _exit_code, summary = _run_success(tmp_path)
    persisted = json.loads(Path(summary["summary_artifact_paths"]["json"]).read_text(encoding="utf-8"))
    assert persisted["generated_real_money_recommendations"] is False


def test_no_histories_are_modified(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    pick_history = history_root / "pick_history.csv"
    shadow_history = history_root / "shadow_candidate_lane_history.csv"
    pick_history.parent.mkdir(parents=True, exist_ok=True)
    pick_history.write_text("prediction_date,player_name\n2026-05-01,Player One\n", encoding="utf-8")
    shadow_history.write_text("prediction_date,player_name\n2026-05-01,Player Two\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in (pick_history, shadow_history)}
    runner, _calls = _runner_factory()

    orchestrate_learning_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        runner=runner,
        printer=lambda _line: None,
    )

    after = {path: path.read_bytes() for path in (pick_history, shadow_history)}
    assert after == before


def test_runtime_outputs_are_written_only_under_runtime_root(tmp_path: Path) -> None:
    runtime_root = tmp_path / "custom_runtime"
    other_runtime = tmp_path / "outputs" / "runtime"
    runner, _calls = _runner_factory()

    exit_code, summary = orchestrate_learning_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=tmp_path / "history",
        runner=runner,
        printer=lambda _line: None,
    )

    assert exit_code == 0
    assert not other_runtime.exists()
    assert Path(summary["summary_artifact_paths"]["txt"]).is_relative_to(runtime_root)
    assert Path(summary["summary_artifact_paths"]["json"]).is_relative_to(runtime_root)
    written_files = {path.relative_to(runtime_root).as_posix() for path in runtime_root.rglob("*") if path.is_file()}
    assert written_files == {
        f"operator/learning_brain_report_{PREDICTION_DATE}.txt",
        f"diagnostics/learning_brain_report_{PREDICTION_DATE}.json",
        f"operator/shadow_rule_proposals_{PREDICTION_DATE}.txt",
        f"diagnostics/shadow_rule_proposals_{PREDICTION_DATE}.json",
        f"operator/learning_integration_snapshot_{PREDICTION_DATE}.txt",
        f"diagnostics/learning_integration_snapshot_{PREDICTION_DATE}.json",
        f"operator/learning_artifacts_summary_{PREDICTION_DATE}.txt",
        f"diagnostics/learning_artifacts_summary_{PREDICTION_DATE}.json",
    }


def test_generated_runtime_artifacts_are_not_committed_or_tracked() -> None:
    result = subprocess.run(
        ["git", "ls-files", "outputs/runtime"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == ""
