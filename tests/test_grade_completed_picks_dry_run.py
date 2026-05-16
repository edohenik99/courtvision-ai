from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scripts import grade_completed_picks as grade_completed_picks_cli
from scripts import history_tracking


def _pending_pick_row(prediction_date: str = "2026-05-06") -> dict[str, object]:
    row: dict[str, object] = {column: "" for column in history_tracking.PICK_HISTORY_COLUMNS}
    row.update(
        {
            "prediction_date": prediction_date,
            "player_name": "Dry Run Player",
            "team": "AAA",
            "opponent": "BBB",
            "market": "player_points",
            "selection": "over",
            "line": 10.5,
            "result_status": "pending",
        }
    )
    return row


def test_grade_completed_picks_dry_run_does_not_write_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    history_root = tmp_path / "history"
    runtime_root = tmp_path / "runtime"
    history_root.mkdir(parents=True, exist_ok=True)
    pick_history_path = history_root / "pick_history.csv"

    pd.DataFrame(
        [_pending_pick_row()],
        columns=list(history_tracking.PICK_HISTORY_COLUMNS),
    ).to_csv(pick_history_path, index=False)
    before = pick_history_path.read_bytes()

    def fail_write_csv(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("_write_csv should not be called in dry-run mode")

    def fail_update_performance_summaries(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("update_performance_summaries should not be called in dry-run mode")

    monkeypatch.setattr(history_tracking, "_write_csv", fail_write_csv)
    monkeypatch.setattr(
        history_tracking,
        "update_performance_summaries",
        fail_update_performance_summaries,
    )
    monkeypatch.setattr(
        history_tracking,
        "_load_actual_results_for_date",
        lambda *_args, **_kwargs: pd.DataFrame(),
    )
    monkeypatch.setattr(
        history_tracking,
        "_load_player_stats_for_date",
        lambda *_args, **_kwargs: pd.DataFrame(),
    )
    monkeypatch.setattr(
        history_tracking,
        "_load_games_for_date",
        lambda *_args, **_kwargs: pd.DataFrame(),
    )

    result = history_tracking.grade_completed_picks(
        history_root=history_root,
        runtime_root=runtime_root,
        prediction_date="2026-05-06",
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert pick_history_path.read_bytes() == before
    assert not (runtime_root / "history").exists()


def test_grade_completed_picks_dry_run_empty_history_skips_performance_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    history_root = tmp_path / "history"
    runtime_root = tmp_path / "runtime"

    def fail_update_performance_summaries(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("update_performance_summaries should not be called in dry-run mode")

    monkeypatch.setattr(
        history_tracking,
        "update_performance_summaries",
        fail_update_performance_summaries,
    )

    result = history_tracking.grade_completed_picks(
        history_root=history_root,
        runtime_root=runtime_root,
        dry_run=True,
    )

    assert result == {
        "updated_rows": 0,
        "pending_rows": 0,
        "unsupported_rows": 0,
        "void_rows": 0,
        "skip_reasons": {},
        "dry_run": True,
    }


def test_grade_completed_picks_cli_passes_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def fake_grade_completed_picks(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "updated_rows": 2,
            "pending_rows": 1,
            "unsupported_rows": 0,
            "void_rows": 0,
            "skip_reasons": {"game_not_final": 1},
            "dry_run": bool(kwargs.get("dry_run")),
        }

    monkeypatch.setattr(
        grade_completed_picks_cli,
        "grade_completed_picks",
        fake_grade_completed_picks,
    )

    rc = grade_completed_picks_cli.main(
        [
            "--prediction-date",
            "2026-05-06",
            "--history-root",
            str(tmp_path / "history"),
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--dry-run",
        ]
    )

    output = capsys.readouterr().out
    assert rc == 0
    assert captured["prediction_date"] == "2026-05-06"
    assert captured["history_root"] == str(tmp_path / "history")
    assert captured["runtime_root"] == str(tmp_path / "runtime")
    assert captured["dry_run"] is True
    assert "dry_run=true" in output
    assert "graded_updates=2" in output
    assert "pending_remaining=1" in output
    assert "skip_reason=game_not_final,1" in output
