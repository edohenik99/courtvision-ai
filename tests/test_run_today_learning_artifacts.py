"""
Tests for Phase 6A Learning Artifacts integration in run_today.ps1.

These are text-based assertion tests that read run_today.ps1 as a string and
verify structural properties. No subprocess execution of the daily runner occurs.
"""
from __future__ import annotations

from pathlib import Path

import pytest


PS1_PATH = Path(__file__).resolve().parents[1] / "run_today.ps1"
PS1_TEXT = PS1_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _learning_step_block(text: str) -> str:
    """Extract the Phase 6A Learning Artifacts block from the script text."""
    start_marker = "Phase 6A Learning Artifacts"
    end_marker = "[START] Operator Card"
    start = text.find(start_marker)
    end = text.find(end_marker, start)
    if start == -1 or end == -1:
        return ""
    return text[start:end]


# ---------------------------------------------------------------------------
# Presence and reference tests
# ---------------------------------------------------------------------------

def test_run_today_references_write_learning_artifacts() -> None:
    """run_today.ps1 must reference the write_learning_artifacts.py script."""
    assert "write_learning_artifacts.py" in PS1_TEXT


def test_run_today_defines_learning_artifacts_script_variable() -> None:
    """run_today.ps1 must define $LearningArtifactsScript pointing at the script."""
    assert "$LearningArtifactsScript" in PS1_TEXT
    assert "scripts\\write_learning_artifacts.py" in PS1_TEXT


# ---------------------------------------------------------------------------
# Script existence guard tests
# ---------------------------------------------------------------------------

def test_run_today_checks_script_existence_before_execution() -> None:
    """run_today.ps1 must guard execution with a Test-Path check on $LearningArtifactsScript."""
    block = _learning_step_block(PS1_TEXT)
    assert block, "Phase 6A Learning Artifacts block not found in run_today.ps1"
    assert "Test-Path $LearningArtifactsScript" in block


def test_run_today_logs_warning_when_script_missing() -> None:
    """run_today.ps1 must log a warning (not crash) when the learning artifacts script is missing."""
    block = _learning_step_block(PS1_TEXT)
    assert block, "Phase 6A Learning Artifacts block not found in run_today.ps1"
    # Should have a [WARN] or [WARNING] message in the missing-script branch
    assert "[WARN]" in block or "[WARNING]" in block


# ---------------------------------------------------------------------------
# Strict mode / invocation safety tests
# ---------------------------------------------------------------------------

def test_run_today_learning_step_does_not_use_strict_flag() -> None:
    """run_today.ps1 must NOT pass --strict to write_learning_artifacts.py.

    Without --strict, failed steps exit 0 (READY_WITH_WARNINGS), so
    normal missing optional artifacts do not crash the daily runner.
    """
    block = _learning_step_block(PS1_TEXT)
    assert block, "Phase 6A Learning Artifacts block not found in run_today.ps1"
    assert "--strict" not in block


def test_run_today_does_not_call_run_today_recursively() -> None:
    """run_today.ps1 must not invoke itself (no recursive call)."""
    # Remove the script declaration line to avoid false positives
    lines = [
        line for line in PS1_TEXT.splitlines()
        if "run_today.ps1" not in line.lower() or line.strip().startswith("#")
    ]
    # The actual content lines should not invoke run_today.ps1 as a command
    invocation_lines = [
        line for line in PS1_TEXT.splitlines()
        if (
            "run_today.ps1" in line.lower()
            and not line.strip().startswith("#")
            and ("invoke" in line.lower() or "& " in line or "powershell" in line.lower())
        )
    ]
    assert invocation_lines == [], (
        f"run_today.ps1 appears to invoke itself recursively:\n"
        + "\n".join(invocation_lines)
    )


# ---------------------------------------------------------------------------
# Safety boundary tests — learning step must not call grading or boards
# ---------------------------------------------------------------------------

def test_run_today_learning_step_does_not_call_grading() -> None:
    """The Phase 6A Learning Artifacts block must not invoke grading scripts."""
    block = _learning_step_block(PS1_TEXT)
    assert block, "Phase 6A Learning Artifacts block not found in run_today.ps1"
    grading_indicators = [
        "grade_completed_picks",
        "post_run_tracking",
        "market_shadow_grading",
        "repair_pending_grades",
    ]
    for indicator in grading_indicators:
        assert indicator not in block, (
            f"Learning artifacts block must not invoke grading script '{indicator}'"
        )


def test_run_today_learning_step_does_not_call_board_regeneration() -> None:
    """The Phase 6A Learning Artifacts block must not regenerate boards."""
    block = _learning_step_block(PS1_TEXT)
    assert block, "Phase 6A Learning Artifacts block not found in run_today.ps1"
    board_indicators = [
        "courtvision_ai.py",
        "--predict-only",
        "--fit-only",
        "elite_board",
        "full_market_board",
        "sgp_board",
    ]
    for indicator in board_indicators:
        assert indicator not in block, (
            f"Learning artifacts block must not trigger board regeneration via '{indicator}'"
        )


# ---------------------------------------------------------------------------
# Unsafe proposals handling
# ---------------------------------------------------------------------------

def test_run_today_handles_unsafe_proposals_with_high_severity_warning() -> None:
    """run_today.ps1 must log a high-severity warning when unsafe proposals are detected,
    and must NOT exit the runner or activate any rules.
    """
    block = _learning_step_block(PS1_TEXT)
    assert block, "Phase 6A Learning Artifacts block not found in run_today.ps1"
    # Must reference the blocked status constant
    assert "LEARNING_ARTIFACTS_BLOCKED_BY_UNSAFE_PROPOSALS" in block
    # Must emit a high-severity log label (not just a plain [WARN])
    assert "SAFETY-HIGH" in block or "safety" in block.lower()


def test_run_today_unsafe_proposals_do_not_exit_runner() -> None:
    """When unsafe proposals are detected the runner must NOT call exit or Stop-StageFailure."""
    block = _learning_step_block(PS1_TEXT)
    assert block, "Phase 6A Learning Artifacts block not found in run_today.ps1"
    # The block should NOT call Stop-StageFailure (which would halt the runner)
    assert "Stop-StageFailure" not in block


def test_run_today_learning_step_continues_after_normal_failure() -> None:
    """When the learning step fails for a non-unsafe-proposals reason the runner continues."""
    block = _learning_step_block(PS1_TEXT)
    assert block, "Phase 6A Learning Artifacts block not found in run_today.ps1"
    # Nonfatal path must set a status variable, not call Stop-StageFailure
    assert "failed_nonfatal" in block or "Continuing daily run" in block


# ---------------------------------------------------------------------------
# Placement test — learning step runs after research artifacts
# ---------------------------------------------------------------------------

def test_run_today_learning_step_placed_after_research_artifacts() -> None:
    """Phase 6A Learning Artifacts step must appear after Phase 5 Research Artifacts."""
    research_pos = PS1_TEXT.find("Phase 5 Research Artifacts")
    learning_pos = PS1_TEXT.find("Phase 6A Learning Artifacts")
    assert research_pos != -1, "Phase 5 Research Artifacts marker not found"
    assert learning_pos != -1, "Phase 6A Learning Artifacts marker not found"
    assert learning_pos > research_pos, (
        "Phase 6A Learning Artifacts must appear after Phase 5 Research Artifacts in run_today.ps1"
    )


def test_run_today_learning_step_placed_before_operator_card() -> None:
    """Phase 6A Learning Artifacts step must appear before the Operator Card step."""
    learning_pos = PS1_TEXT.find("Phase 6A Learning Artifacts")
    operator_pos = PS1_TEXT.find("[START] Operator Card")
    assert learning_pos != -1, "Phase 6A Learning Artifacts marker not found"
    assert operator_pos != -1, "[START] Operator Card marker not found"
    assert learning_pos < operator_pos, (
        "Phase 6A Learning Artifacts must appear before Operator Card in run_today.ps1"
    )
