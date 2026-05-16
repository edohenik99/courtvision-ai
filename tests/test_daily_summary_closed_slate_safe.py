from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pandas as pd

from courtvision.reporting.same_opponent_rematch import annotate_operator_board_files
from scripts import write_daily_summary as daily_summary


def _write_minimal_board(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-16",
                "player_name": "Test Player",
                "team_abbr": "AAA",
                "opponent": "BBB",
                "market_type": "player_points",
                "selection": "under",
                "line": 10.5,
            }
        ]
    ).to_csv(path, index=False)


def test_annotate_operator_board_files_read_only_does_not_mutate_boards(tmp_path: Path) -> None:
    prediction_date = "2026-05-16"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    full_market_path = runtime_root / "operator" / f"full_market_board_{prediction_date}.csv"
    elite_path = runtime_root / "operator" / f"elite_board_{prediction_date}.csv"

    _write_minimal_board(full_market_path)
    _write_minimal_board(elite_path)
    full_before = full_market_path.read_text(encoding="utf-8")
    elite_before = elite_path.read_text(encoding="utf-8")

    result = annotate_operator_board_files(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        write=False,
    )

    assert full_market_path.read_text(encoding="utf-8") == full_before
    assert elite_path.read_text(encoding="utf-8") == elite_before
    assert result["boards"]["full_market_board"]["write_enabled"] is False
    assert result["boards"]["full_market_board"]["skipped_write"] is True
    assert result["boards"]["elite_board"]["write_enabled"] is False
    assert result["boards"]["elite_board"]["skipped_write"] is True


def test_annotate_operator_board_files_default_writes_annotations(tmp_path: Path) -> None:
    prediction_date = "2026-05-16"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    full_market_path = runtime_root / "operator" / f"full_market_board_{prediction_date}.csv"
    elite_path = runtime_root / "operator" / f"elite_board_{prediction_date}.csv"

    _write_minimal_board(full_market_path)
    _write_minimal_board(elite_path)

    result = annotate_operator_board_files(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    full_after = pd.read_csv(full_market_path, keep_default_na=False)
    elite_after = pd.read_csv(elite_path, keep_default_na=False)
    assert "same_opponent_recent_games" in full_after.columns
    assert "same_opponent_recent_games" in elite_after.columns
    assert result["boards"]["full_market_board"]["write_enabled"] is True
    assert result["boards"]["full_market_board"]["skipped_write"] is False


def test_write_daily_summary_main_closed_slate_safe_passes_read_only_flags(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_write_daily_summary_outputs(**kwargs):
        captured.update(kwargs)
        metadata = defaultdict(int)
        metadata["run_health_status"] = "TEST"
        return tmp_path / "daily_summary_2026-05-16.txt", metadata

    monkeypatch.setattr(daily_summary, "write_daily_summary_outputs", fake_write_daily_summary_outputs)

    rc = daily_summary.main(["--prediction-date", "2026-05-16", "--closed-slate-safe"])

    assert rc == 0
    assert captured["write_board_annotations"] is False
    assert captured["persist_shadow_history"] is False
    assert captured["persist_paper_kelly_history"] is False


def test_write_daily_summary_main_explicit_skip_flags(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_write_daily_summary_outputs(**kwargs):
        captured.update(kwargs)
        metadata = defaultdict(int)
        metadata["run_health_status"] = "TEST"
        return tmp_path / "daily_summary_2026-05-16.txt", metadata

    monkeypatch.setattr(daily_summary, "write_daily_summary_outputs", fake_write_daily_summary_outputs)

    rc = daily_summary.main(
        [
            "--prediction-date",
            "2026-05-16",
            "--no-board-annotation-write",
            "--skip-market-shadow-history",
            "--skip-paper-kelly-history",
        ]
    )

    assert rc == 0
    assert captured["write_board_annotations"] is False
    assert captured["persist_shadow_history"] is False
