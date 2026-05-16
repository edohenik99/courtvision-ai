from __future__ import annotations

from pathlib import Path

from scripts import nightly_grade_and_refresh as nightly


def test_nightly_grade_and_refresh_passes_dry_run(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    captured: dict[str, object] = {}

    def fake_grade_completed_picks(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "updated_rows": 3,
            "pending_rows": 2,
            "unsupported_rows": 0,
            "void_rows": 0,
            "skip_reasons": {},
            "dry_run": bool(kwargs.get("dry_run")),
        }

    monkeypatch.setattr(nightly, "grade_completed_picks", fake_grade_completed_picks)

    history_root = tmp_path / "history"
    runtime_root = tmp_path / "runtime"

    rc = nightly.main(
        [
            "--history-root",
            str(history_root),
            "--runtime-root",
            str(runtime_root),
            "--dry-run",
        ]
    )

    output = capsys.readouterr().out
    assert rc == 0
    assert captured["history_root"] == history_root
    assert captured["runtime_root"] == runtime_root
    assert captured["dry_run"] is True
    assert "dry_run=true" in output
    assert "newly_graded=3" in output
    assert "still_pending=2" in output


def test_nightly_grade_and_refresh_defaults_to_write_mode(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    captured: dict[str, object] = {}

    def fake_grade_completed_picks(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "updated_rows": 0,
            "pending_rows": 0,
            "unsupported_rows": 0,
            "void_rows": 0,
            "skip_reasons": {},
            "dry_run": bool(kwargs.get("dry_run")),
        }

    monkeypatch.setattr(nightly, "grade_completed_picks", fake_grade_completed_picks)

    history_root = tmp_path / "history"
    runtime_root = tmp_path / "runtime"

    rc = nightly.main(
        [
            "--history-root",
            str(history_root),
            "--runtime-root",
            str(runtime_root),
        ]
    )

    output = capsys.readouterr().out
    assert rc == 0
    assert captured["history_root"] == history_root
    assert captured["runtime_root"] == runtime_root
    assert captured["dry_run"] is False
    assert "dry_run=false" in output
