from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import refresh_closed_slate_reports as wrapper


def _seed_required_artifacts(
    tmp_path: Path,
    prediction_date: str,
    *,
    missing: set[str] | None = None,
) -> tuple[Path, dict[str, Path]]:
    runtime_root = tmp_path / "outputs" / "runtime"
    paths = wrapper.required_artifact_paths(prediction_date, runtime_root=runtime_root)
    missing = missing or set()
    for name, path in paths.items():
        if name in missing:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".csv":
            path.write_text("prediction_date\n", encoding="utf-8")
        else:
            path.write_text("{}\n", encoding="utf-8")
    return runtime_root, paths


def test_dry_run_prints_expected_commands_and_does_not_execute_subprocesses(
    tmp_path: Path,
) -> None:
    prediction_date = "2026-05-21"
    runtime_root, _ = _seed_required_artifacts(tmp_path, prediction_date)
    lines: list[str] = []

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        raise AssertionError("dry-run should not execute subprocesses")

    exit_code = wrapper.refresh_closed_slate_reports(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        dry_run=True,
        runner=runner,
        printer=lines.append,
    )

    output = "\n".join(lines)
    assert exit_code == 0
    assert (
        "would_run py -3.13 scripts/prefill_actual_feedback.py --prediction-date 2026-05-21"
    ) in output
    assert (
        "would_run py -3.13 scripts/repair_pending_grades.py --all-completed "
        "--through-date 2026-05-21 --regrade-terminal-player-stat-missing "
        "--terminal-regrade-date 2026-05-21 --include-current-date"
    ) in output
    assert (
        "would_run py -3.13 scripts/write_daily_summary.py --prediction-date "
        "2026-05-21 --closed-slate-safe --no-board-annotation-write"
    ) in output
    assert "no prediction boards regenerated: yes" in output
    assert "no Kelly stakes regenerated: yes" in output


def test_missing_required_artifact_blocks_before_running_commands(
    tmp_path: Path,
) -> None:
    prediction_date = "2026-05-21"
    runtime_root, _ = _seed_required_artifacts(
        tmp_path,
        prediction_date,
        missing={"board_diagnostics"},
    )
    lines: list[str] = []

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        raise AssertionError("missing artifacts should block subprocesses")

    exit_code = wrapper.refresh_closed_slate_reports(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        runner=runner,
        printer=lines.append,
    )

    output = "\n".join(lines)
    assert exit_code == 1
    assert "Missing required prediction-date artifacts" in output
    assert "board_diagnostics" in output


def test_command_sequence_includes_closed_slate_safe_no_annotation_flags() -> None:
    commands = wrapper.build_refresh_commands(prediction_date="2026-05-21")
    by_label = {command.label: command.command for command in commands}

    assert by_label["prefill_actual_feedback"][2] == "scripts/prefill_actual_feedback.py"
    assert by_label["repair_pending_grades"].index("scripts/repair_pending_grades.py") == 2
    assert "--regrade-terminal-player-stat-missing" in by_label["repair_pending_grades"]
    assert "--closed-slate-safe" in by_label["daily_summary"]
    assert "--no-board-annotation-write" in by_label["daily_summary"]
    assert "--closed-slate-safe" in by_label["quality_summary"]
    assert "--no-board-annotation-write" in by_label["quality_summary"]
    assert by_label["operator_card"][-1] == "--force"


def test_wrapper_never_calls_forbidden_pipeline_commands() -> None:
    commands = wrapper.build_refresh_commands(prediction_date="2026-05-21")
    command_text = "\n".join(
        wrapper._format_command(command.command) for command in commands
    ).lower()

    assert "run_today.ps1" not in command_text
    assert "run_today.bat" not in command_text
    assert "courtvision_ai.py" not in command_text
    assert "scripts/run_kelly_stakes.py" not in command_text


def test_subprocess_nonzero_stops_workflow(tmp_path: Path) -> None:
    prediction_date = "2026-05-21"
    runtime_root, _ = _seed_required_artifacts(tmp_path, prediction_date)
    calls: list[tuple[str, ...]] = []
    return_codes = [0, 7, 0, 0, 0, 0]

    def runner(
        command: tuple[str, ...],
        cwd: Path,
    ) -> subprocess.CompletedProcess:
        calls.append(command)
        return subprocess.CompletedProcess(command, return_codes.pop(0))

    exit_code = wrapper.refresh_closed_slate_reports(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        runner=runner,
        printer=lambda _: None,
    )

    assert exit_code == 7
    assert len(calls) == 2
    assert calls[0][2] == "scripts/prefill_actual_feedback.py"
    assert calls[1][2] == "scripts/repair_pending_grades.py"


def test_wrapper_itself_does_not_write_prediction_board_or_kelly_outputs(
    tmp_path: Path,
) -> None:
    prediction_date = "2026-05-21"
    runtime_root, paths = _seed_required_artifacts(tmp_path, prediction_date)
    kelly_path = runtime_root / "operator" / f"kelly_stakes_{prediction_date}.csv"
    kelly_path.write_text("protected\n", encoding="utf-8")
    before = {
        paths["elite_board"]: paths["elite_board"].read_bytes(),
        paths["full_market_board"]: paths["full_market_board"].read_bytes(),
        kelly_path: kelly_path.read_bytes(),
    }
    calls: list[tuple[str, ...]] = []

    def runner(
        command: tuple[str, ...],
        cwd: Path,
    ) -> subprocess.CompletedProcess:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    exit_code = wrapper.refresh_closed_slate_reports(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        runner=runner,
        printer=lambda _: None,
    )

    after = {path: path.read_bytes() for path in before}
    command_text = "\n".join(wrapper._format_command(command) for command in calls)
    assert exit_code == 0
    assert after == before
    assert "kelly_stakes" not in command_text
    assert "elite_board" not in command_text
    assert "full_market_board" not in command_text
