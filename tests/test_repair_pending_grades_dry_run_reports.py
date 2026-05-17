from __future__ import annotations

from pathlib import Path

from scripts import repair_pending_grades as repair


def test_repair_pending_grades_dry_run_does_not_write_audit_reports(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail_write_audit_reports(*_args: object, **_kwargs: object) -> tuple[Path, Path]:
        raise AssertionError("_write_audit_reports should not run during dry-run")

    monkeypatch.setattr(repair, "_write_audit_reports", fail_write_audit_reports)

    result = repair.repair_pending_grades(
        start_date="2026-05-01",
        end_date="2026-05-02",
        history_root=tmp_path / "history",
        runtime_root=tmp_path / "runtime",
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["audit_report_write_enabled"] is False
    assert result["audit_report_write_skipped"] is True
    assert "audit_json_path" not in result
    assert "report_text_path" not in result
    assert not (tmp_path / "runtime" / "diagnostics").exists()
    assert not (tmp_path / "runtime" / "operator").exists()


def test_repair_all_completed_dry_run_does_not_write_audit_reports(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail_write_audit_reports(*_args: object, **_kwargs: object) -> tuple[Path, Path]:
        raise AssertionError("_write_audit_reports should not run during all-completed dry-run")

    monkeypatch.setattr(repair, "_write_audit_reports", fail_write_audit_reports)

    result = repair.repair_all_completed_grades(
        history_root=tmp_path / "history",
        runtime_root=tmp_path / "runtime",
        through_date="2026-05-02",
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["audit_report_write_enabled"] is False
    assert result["audit_report_write_skipped"] is True
    assert "audit_json_path" not in result
    assert "report_text_path" not in result


def test_repair_pending_grades_write_mode_still_writes_audit_reports(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_write_audit_reports(result: dict[str, object], runtime_root_path: Path) -> tuple[Path, Path]:
        captured["result"] = result
        captured["runtime_root_path"] = runtime_root_path
        return (
            runtime_root_path / "diagnostics" / "pending_repair_audit_2026-05-02.json",
            runtime_root_path / "operator" / "pending_repair_report_2026-05-02.txt",
        )

    monkeypatch.setattr(repair, "_write_audit_reports", fake_write_audit_reports)

    runtime_root = tmp_path / "runtime"
    result = repair.repair_pending_grades(
        start_date="2026-05-01",
        end_date="2026-05-02",
        history_root=tmp_path / "history",
        runtime_root=runtime_root,
        dry_run=False,
    )

    assert result["dry_run"] is False
    assert result["audit_report_write_enabled"] is True
    assert result["audit_report_write_skipped"] is False
    assert result["audit_json_path"].endswith("pending_repair_audit_2026-05-02.json")
    assert result["report_text_path"].endswith("pending_repair_report_2026-05-02.txt")
    assert captured["runtime_root_path"] == runtime_root
