from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

import courtvision_ai
from courtvision.pipeline.runner import save_prediction_boards
from scripts.history_tracking import persist_daily_picks


def _board_row(prediction_date: str, player_name: str = "Date Guard Player") -> dict[str, object]:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "entity_name": player_name,
        "team": "BOS",
        "opponent": "NYK",
        "market_type": "player_points",
        "selection": "over",
        "sportsbook_line": 10.5,
        "line": 10.5,
        "odds": -110,
        "model_projection": 12.0,
        "edge": 1.5,
        "confidence": 0.7,
        "quality_score": 80.0,
        "qualification_reason": "test",
    }


def _write_sentinel(path: Path, text: str) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    old_time = 1_700_000_000
    os.utime(path, (old_time, old_time))
    return path.stat().st_mtime_ns, path.read_text(encoding="utf-8")


def test_prediction_writer_does_not_touch_prior_date_boards(tmp_path: Path) -> None:
    date_a = "2026-04-29"
    date_b = "2026-04-30"
    operator_dir = tmp_path / "runtime" / "operator"
    prior_files = [
        operator_dir / f"elite_board_{date_a}.csv",
        operator_dir / f"full_market_board_{date_a}.csv",
        operator_dir / f"sgp_board_{date_a}.csv",
    ]
    before = {
        path: _write_sentinel(path, f"sentinel,{path.name}\n")
        for path in prior_files
    }

    elite_df = pd.DataFrame([_board_row(date_b)])
    paths = courtvision_ai._write_cli_outputs(
        out_dir=tmp_path,
        prediction_date=date_b,
        fit_metrics=None,
        prediction_outputs={
            "selected_props": elite_df,
            "elite_props": elite_df,
            "qualified_pool_props": elite_df,
            "full_market_props": elite_df,
            "sgp_props": pd.DataFrame(),
            "summary": {"prediction_date": date_b},
            "grading_results": pd.DataFrame(),
        },
        verbose_outputs=False,
    )

    assert paths["elite_board"].name == f"elite_board_{date_b}.csv"
    assert paths["full_market_board"].name == f"full_market_board_{date_b}.csv"
    assert paths["sgp_board"].name == f"sgp_board_{date_b}.csv"
    for path, (mtime_ns, content) in before.items():
        assert path.stat().st_mtime_ns == mtime_ns
        assert path.read_text(encoding="utf-8") == content


def test_prediction_artifact_date_guard_rejects_mismatched_path(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match=r"requested_prediction_date=2026-04-30 .*artifact_date=2026-04-29"):
        courtvision_ai._guard_prediction_artifact_date(
            requested_prediction_date="2026-04-30",
            output_path=tmp_path / "runtime" / "operator" / "elite_board_2026-04-29.csv",
            caller="test",
        )


def test_prediction_artifact_date_guard_rejects_mismatched_date_directory(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match=r"requested_prediction_date=2026-04-30 .*artifact_date=2026-04-29"):
        courtvision_ai._guard_prediction_artifact_date(
            requested_prediction_date="2026-04-30",
            output_path=tmp_path / "boards" / "2026-04-29" / "elite.csv",
            caller="test",
        )


def test_compat_board_writer_does_not_touch_prior_date_directory(tmp_path: Path) -> None:
    date_a = "2026-04-29"
    date_b = "2026-04-30"
    prior_path = tmp_path / "boards" / date_a / "elite.csv"
    mtime_ns, content = _write_sentinel(prior_path, "sentinel,elite.csv\n")

    elite_df = pd.DataFrame([_board_row(date_b)])
    save_prediction_boards(
        date_b,
        {
            "elite_props": elite_df,
            "full_market_props": elite_df,
            "stat_only_props": elite_df,
        },
        tmp_path,
    )

    assert prior_path.stat().st_mtime_ns == mtime_ns
    assert prior_path.read_text(encoding="utf-8") == content
    assert (tmp_path / "boards" / date_b / "elite.csv").exists()


def test_post_run_tracking_reads_operator_board_without_rewriting_it(tmp_path: Path) -> None:
    date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    elite_path = runtime_root / "operator" / f"elite_board_{date}.csv"
    elite_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([_board_row(date)]).to_csv(elite_path, index=False)
    mtime_ns = elite_path.stat().st_mtime_ns
    content = elite_path.read_text(encoding="utf-8")

    result = persist_daily_picks(
        prediction_date=date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert result["appended_rows"] == 1
    assert elite_path.stat().st_mtime_ns == mtime_ns
    assert elite_path.read_text(encoding="utf-8") == content
    assert (runtime_root / "history" / f"picks_{date}.csv").exists()
