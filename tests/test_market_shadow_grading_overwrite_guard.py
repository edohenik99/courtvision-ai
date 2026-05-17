from __future__ import annotations

from pathlib import Path

import pytest

from scripts import market_shadow_grading as shadow


def test_market_shadow_outputs_no_force_raises_on_existing_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    diagnostics_path = runtime_root / "diagnostics" / f"market_shadow_grading_{prediction_date}.json"
    report_path = runtime_root / "operator" / f"market_shadow_report_{prediction_date}.txt"
    diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostics_path.write_text('{"existing": true}\n', encoding="utf-8")

    def fail_build_market_shadow_grading(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("build_market_shadow_grading should not run when guard blocks")

    monkeypatch.setattr(shadow, "build_market_shadow_grading", fail_build_market_shadow_grading)

    with pytest.raises(RuntimeError, match=r"\[ARTIFACT_OVERWRITE_GUARD\]"):
        shadow.write_market_shadow_outputs(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
            history_root=tmp_path / "history",
        )

    assert diagnostics_path.read_text(encoding="utf-8") == '{"existing": true}\n'
    assert not report_path.exists()


def test_market_shadow_outputs_no_force_raises_on_existing_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    diagnostics_path = runtime_root / "diagnostics" / f"market_shadow_grading_{prediction_date}.json"
    report_path = runtime_root / "operator" / f"market_shadow_report_{prediction_date}.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("existing report\n", encoding="utf-8")

    def fail_build_market_shadow_grading(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("build_market_shadow_grading should not run when guard blocks")

    monkeypatch.setattr(shadow, "build_market_shadow_grading", fail_build_market_shadow_grading)

    with pytest.raises(RuntimeError, match=r"\[ARTIFACT_OVERWRITE_GUARD\]"):
        shadow.write_market_shadow_outputs(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
            history_root=tmp_path / "history",
        )

    assert not diagnostics_path.exists()
    assert report_path.read_text(encoding="utf-8") == "existing report\n"


def test_market_shadow_outputs_force_allows_existing_artifact_overwrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    diagnostics_path = runtime_root / "diagnostics" / f"market_shadow_grading_{prediction_date}.json"
    report_path = runtime_root / "operator" / f"market_shadow_report_{prediction_date}.txt"
    diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostics_path.write_text('{"existing": true}\n', encoding="utf-8")
    report_path.write_text("existing report\n", encoding="utf-8")

    def fake_build_market_shadow_grading(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "prediction_date": prediction_date,
            "scope": "full_market_shadow_grading",
            "totals": {"total_picks": 0, "graded_picks": 0, "pending_picks": 0},
            "markets": [],
            "context_alignment_performance": {"status": "insufficient_sample", "by_alignment": {}},
            "kelly_decision_performance": {},
        }

    monkeypatch.setattr(shadow, "build_market_shadow_grading", fake_build_market_shadow_grading)

    written_diagnostics, written_report, payload = shadow.write_market_shadow_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=tmp_path / "history",
        update_grading_summary=False,
        force=True,
    )

    assert written_diagnostics == diagnostics_path
    assert written_report == report_path
    assert payload["grading_summary_update_enabled"] is False
    assert '"prediction_date": "2026-05-06"' in diagnostics_path.read_text(encoding="utf-8")
    assert "Market Shadow Report - 2026-05-06" in report_path.read_text(encoding="utf-8")


def test_market_shadow_cli_passes_force(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def fake_write_market_shadow_outputs(**kwargs: object) -> tuple[Path, Path, dict[str, object]]:
        captured.update(kwargs)
        return (
            tmp_path / "market_shadow.json",
            tmp_path / "market_shadow.txt",
            {
                "totals": {"total_picks": 1, "graded_picks": 0, "pending_picks": 1},
                "context_alignment_performance": {"by_alignment": {}},
                "kelly_decision_performance": {},
            },
        )

    monkeypatch.setattr(shadow, "write_market_shadow_outputs", fake_write_market_shadow_outputs)

    rc = shadow.main(
        [
            "--prediction-date",
            "2026-05-06",
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--history-root",
            str(tmp_path / "history"),
            "--closed-slate-safe",
            "--force",
        ]
    )

    output = capsys.readouterr().out
    assert rc == 0
    assert captured["prediction_date"] == "2026-05-06"
    assert captured["runtime_root"] == str(tmp_path / "runtime")
    assert captured["history_root"] == str(tmp_path / "history")
    assert captured["update_grading_summary"] is False
    assert captured["force"] is True
    assert "market_shadow_grading_json=" in output
    assert "market_shadow_report_txt=" in output
