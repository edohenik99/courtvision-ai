from __future__ import annotations

from pathlib import Path

import pytest

from courtvision.reporting import quality_summary
from scripts import write_quality_summary as quality_summary_script


def test_quality_summary_outputs_can_disable_board_annotation_writes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_annotate_operator_board_files(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        raise RuntimeError("stop after annotation")

    monkeypatch.setattr(
        quality_summary,
        "annotate_operator_board_files",
        fake_annotate_operator_board_files,
    )

    with pytest.raises(RuntimeError, match="stop after annotation"):
        quality_summary.write_quality_summary_outputs(
            prediction_date="2026-05-15",
            runtime_root=tmp_path / "runtime",
            out_dir=tmp_path / "outputs",
            history_root=tmp_path / "history",
            write_board_annotations=False,
        )

    assert captured["prediction_date"] == "2026-05-15"
    assert captured["runtime_root"] == tmp_path / "runtime"
    assert captured["write"] is False


def test_quality_summary_outputs_default_keeps_board_annotation_writes_enabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_annotate_operator_board_files(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        raise RuntimeError("stop after annotation")

    monkeypatch.setattr(
        quality_summary,
        "annotate_operator_board_files",
        fake_annotate_operator_board_files,
    )

    with pytest.raises(RuntimeError, match="stop after annotation"):
        quality_summary.write_quality_summary_outputs(
            prediction_date="2026-05-15",
            runtime_root=tmp_path / "runtime",
            out_dir=tmp_path / "outputs",
            history_root=tmp_path / "history",
        )

    assert captured["write"] is True


def _minimal_payload() -> dict:
    return {
        "candidate_funnel": {
            "elite_board_count": 0,
            "full_market_board_count": 0,
        },
        "kelly_safety_summary": {
            "total_rows": 0,
            "kelly_eligible_count": 0,
            "context_high_caution_over_skip_count": 0,
            "medium_neutral_over_dampened_count": 0,
            "manual_review_required_count": 0,
            "review_before_bet_count": 0,
        },
        "same_opponent_under_warning_count": 0,
        "manual_review_required_count": 0,
    }


def test_quality_summary_main_closed_slate_safe_disables_board_annotation_write(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_write_quality_summary_outputs(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return (
            tmp_path / "quality_summary_2026-05-15.txt",
            tmp_path / "quality_summary_2026-05-15.json",
            _minimal_payload(),
        )

    monkeypatch.setattr(
        quality_summary_script,
        "write_quality_summary_outputs",
        fake_write_quality_summary_outputs,
    )

    rc = quality_summary_script.main(
        [
            "--prediction-date",
            "2026-05-15",
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--closed-slate-safe",
        ]
    )

    assert rc == 0
    assert captured["write_board_annotations"] is False


def test_quality_summary_main_no_board_annotation_write_flag(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_write_quality_summary_outputs(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return (
            tmp_path / "quality_summary_2026-05-15.txt",
            tmp_path / "quality_summary_2026-05-15.json",
            _minimal_payload(),
        )

    monkeypatch.setattr(
        quality_summary_script,
        "write_quality_summary_outputs",
        fake_write_quality_summary_outputs,
    )

    rc = quality_summary_script.main(
        [
            "--prediction-date",
            "2026-05-15",
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--no-board-annotation-write",
        ]
    )

    assert rc == 0
    assert captured["write_board_annotations"] is False
