from __future__ import annotations

from pathlib import Path


RUNBOOK = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "EVIDENCE_AUTOMATION_RUNBOOK.md"
).read_text(encoding="utf-8")
RUNBOOK_LOWER = RUNBOOK.lower()


def test_runbook_references_daily_automation_script() -> None:
    assert "tools/run_courtvision_evidence_daily.ps1" in RUNBOOK


def test_runbook_references_grading_automation_script() -> None:
    assert "tools/run_courtvision_evidence_grading.ps1" in RUNBOOK


def test_runbook_includes_dry_run_commands() -> None:
    assert "-DryRunEvidenceExport" in RUNBOOK
    assert "-DryRun" in RUNBOOK


def test_runbook_includes_real_run_commands() -> None:
    assert "-SkipCourtVisionRun" in RUNBOOK
    assert "apply the same inputs" in RUNBOOK_LOWER


def test_runbook_warns_not_to_commit_generated_evidence() -> None:
    assert "do not commit generated evidence" in RUNBOOK_LOWER


def test_runbook_warns_evidence_does_not_prove_profitability() -> None:
    assert "does not prove profitability" in RUNBOOK_LOWER
