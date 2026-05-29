from __future__ import annotations

import json
import subprocess
from pathlib import Path
import pandas as pd
import pytest

from scripts.write_research_artifacts import orchestrate_research_artifacts


PREDICTION_DATE = "2026-05-30"


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


def _seed_operator_inputs(runtime_root: Path) -> None:
    operator = runtime_root / "operator"
    row = _market_row()
    _write_csv(operator / f"full_market_board_{PREDICTION_DATE}.csv", [row])


def test_orchestrator_required_board_missing(tmp_path: Path) -> None:
    # No full_market_board seeded
    exit_code = orchestrate_research_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
    )
    assert exit_code == 1


def test_orchestrator_executes_correct_order_and_arguments(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _seed_operator_inputs(runtime_root)

    commands_run = []

    def mock_runner(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        commands_run.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    exit_code = orchestrate_research_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        runner=mock_runner,
    )

    assert exit_code == 0
    assert len(commands_run) == 5

    # Verify command order
    assert "write_under_visibility_audit.py" in commands_run[0][1]
    assert "write_shadow_candidate_lane_report.py" in commands_run[1][1]
    assert "write_shadow_candidate_lane_performance.py" in commands_run[2][1]
    assert "write_daily_summary.py" in commands_run[3][1]
    assert "write_operator_card.py" in commands_run[4][1]

    # Verify all arguments propagated correctly
    for cmd in commands_run:
        assert "--prediction-date" in cmd
        assert PREDICTION_DATE in cmd
        assert "--runtime-root" in cmd
        assert str(runtime_root) in cmd
        assert "--history-root" in cmd
        assert str(history_root) in cmd

    # Verify --force on operator card
    assert "--force" in commands_run[4]


def test_orchestrator_dry_run_safety(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _seed_operator_inputs(runtime_root)

    runner_called = False

    def mock_runner(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        nonlocal runner_called
        runner_called = True
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    exit_code = orchestrate_research_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        runner=mock_runner,
        dry_run=True,
    )

    assert exit_code == 0
    assert not runner_called  # Under dry run, no file-writing subprocess is executed


def test_orchestrator_non_fatal_optional_failures(tmp_path: Path, capsys) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _seed_operator_inputs(runtime_root)

    def mock_runner(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        # Fail optional under visibility audit step, but succeed daily summary/operator card
        if "write_under_visibility_audit.py" in cmd[1]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="audit failed")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    exit_code = orchestrate_research_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        runner=mock_runner,
    )

    assert exit_code == 0  # Non-fatal optional failures still result in exit code 0
    captured = capsys.readouterr()
    assert "[WARN] Under visibility audit failed: audit failed" in captured.out


def test_orchestrator_core_failures_warnings(tmp_path: Path, capsys) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _seed_operator_inputs(runtime_root)

    def mock_runner(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        # Fail daily summary refresh core step
        if "write_daily_summary.py" in cmd[1]:
            return subprocess.CompletedProcess(args=cmd, returncode=2, stdout="", stderr="summary error")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    exit_code = orchestrate_research_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        runner=mock_runner,
    )

    assert exit_code == 0  # Non-fatal even for core failures inside the orchestrator
    captured = capsys.readouterr()
    assert "!!! WARNING: CORE SUMMARY/CARD REFRESH FAILED !!! Daily summary refresh failed: summary error" in captured.out


def test_orchestrator_no_pick_history_access(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _seed_operator_inputs(runtime_root)

    pick_history_path = history_root / "pick_history.csv"
    _write_csv(pick_history_path, [{"prediction_date": PREDICTION_DATE, "pick": "over"}])

    commands_run = []

    def mock_runner(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        commands_run.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    orchestrate_research_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        runner=mock_runner,
    )

    # Verify pick history was untouched/not accessed by orchestrator
    df = pd.read_csv(pick_history_path)
    assert len(df) == 1
    assert df.loc[0, "pick"] == "over"


def test_orchestrator_dry_run_missing_board(tmp_path: Path, capsys) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    # Do not seed the operator inputs! The board is missing.
    # Seed pick_history.csv to verify it is untouched.
    pick_history_path = history_root / "pick_history.csv"
    _write_csv(pick_history_path, [{"prediction_date": PREDICTION_DATE, "pick": "over"}])

    runner_called = False

    def mock_runner(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        nonlocal runner_called
        runner_called = True
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    exit_code = orchestrate_research_artifacts(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        runner=mock_runner,
        dry_run=True,
    )

    # 1. dry-run with missing full_market_board exits successfully
    assert exit_code == 0

    # 2. dry-run invokes no subprocesses
    assert not runner_called

    # 3. dry-run prints a warning and planned command order
    captured = capsys.readouterr()
    expected_warn = f"[DRY-RUN][WARN] Required source artifact is missing: {runtime_root / 'operator' / f'full_market_board_{PREDICTION_DATE}.csv'}"
    assert expected_warn in captured.out
    assert "Would run command:" in captured.out
    assert "write_under_visibility_audit.py" in captured.out
    assert "write_daily_summary.py" in captured.out
    assert "write_operator_card.py" in captured.out

    # 4. pick_history.csv untouched
    df = pd.read_csv(pick_history_path)
    assert len(df) == 1
    assert df.loc[0, "pick"] == "over"

    # 5. Confirm dry-run with missing full_market_board does not create any runtime output files/directories or report files
    # runtime_root should not exist or be empty
    assert not runtime_root.exists() or len(list(runtime_root.rglob("*"))) == 0
    # The only file in history_root should be pick_history.csv
    history_files = [f for f in history_root.rglob("*") if f.is_file()]
    assert len(history_files) == 1
    assert history_files[0] == pick_history_path

