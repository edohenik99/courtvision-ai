from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.dashboard import load_dashboard_data
from scripts.history_tracking import (
    grade_completed_picks,
    persist_daily_picks,
    update_performance_summaries,
)


def _write_elite_board(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_audit(path: Path, max_team: int = 2, max_game: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "summary": {
                    "provider_used": "test_provider",
                    "board_analytics": {
                        "max_team_exposure": max_team,
                        "max_game_exposure": max_game,
                    },
                },
                "totals": {"total_candidates": 2, "total_rejections": 0},
            }
        ),
        encoding="utf-8",
    )


def test_pick_history_append_works(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-04-23"
    _write_elite_board(
        runtime_root / "operator" / f"elite_board_{date}.csv",
        [
            {
                "prediction_date": date,
                "player_name": "Alpha Star",
                "team": "BOS",
                "opponent": "NYK",
                "game_id": 101,
                "market_type": "player_points",
                "selection": "over",
                "sportsbook_line": 24.5,
                "model_projection": 27.0,
                "edge": 2.5,
                "odds": -110,
                "confidence": 0.7,
                "quality_score": 80.0,
                "qualification_reason": "pass",
            }
        ],
    )
    _write_audit(runtime_root / "operator" / f"elite_pipeline_audit_summary_{date}.json")

    result = persist_daily_picks(prediction_date=date, runtime_root=runtime_root, history_root=history_root)
    assert result["appended_rows"] == 1
    assert (runtime_root / "history" / f"picks_{date}.csv").exists()

    history = pd.read_csv(history_root / "pick_history.csv")
    assert len(history) == 1
    assert history.iloc[0]["result_status"] == "pending"
    assert history.iloc[0]["provider_used"] == "test_provider"


def test_pending_picks_do_not_crash_grading(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    history_root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "prediction_date": "2026-04-24",
                "run_timestamp": "2026-04-24T12:00:00+00:00",
                "player_name": "No Result",
                "team": "BOS",
                "opponent": "NYK",
                "game_id": "",
                "market": "points",
                "selection": "over",
                "line": 20.5,
                "projection": 23.0,
                "edge": 2.5,
                "abs_edge": 2.5,
                "odds": "-110",
                "confidence": "0.7",
                "quality_score": 70.0,
                "qualification_reason": "pass",
                "provider_used": "test",
                "result_status": "pending",
            }
        ]
    ).to_csv(history_root / "pick_history.csv", index=False)

    result = grade_completed_picks(history_root=history_root, runtime_root=runtime_root)
    assert result["updated_rows"] == 0
    updated = pd.read_csv(history_root / "pick_history.csv")
    assert updated.iloc[0]["result_status"] == "pending"


def test_over_under_grading_logic_works(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    history_root.mkdir(parents=True, exist_ok=True)
    date = "2026-04-25"
    pd.DataFrame(
        [
            {
                "prediction_date": date,
                "run_timestamp": "2026-04-25T12:00:00+00:00",
                "player_name": "Over Guy",
                "team": "BOS",
                "opponent": "NYK",
                "game_id": "",
                "market": "points",
                "selection": "over",
                "line": 20.5,
                "projection": 23.0,
                "edge": 2.5,
                "abs_edge": 2.5,
                "odds": "-110",
                "confidence": "0.7",
                "quality_score": 70.0,
                "qualification_reason": "pass",
                "provider_used": "test",
                "result_status": "pending",
            },
            {
                "prediction_date": date,
                "run_timestamp": "2026-04-25T12:00:00+00:00",
                "player_name": "Under Guy",
                "team": "MIA",
                "opponent": "LAL",
                "game_id": "",
                "market": "points",
                "selection": "under",
                "line": 12.5,
                "projection": 10.0,
                "edge": -2.5,
                "abs_edge": 2.5,
                "odds": "-110",
                "confidence": "0.7",
                "quality_score": 70.0,
                "qualification_reason": "pass",
                "provider_used": "test",
                "result_status": "pending",
            },
        ]
    ).to_csv(history_root / "pick_history.csv", index=False)

    actual_df = pd.DataFrame(
        [
            {
                "entity_name": "Over Guy",
                "selection": "over",
                "market_type": "points",
                "sportsbook_line": 20.5,
                "graded_result": "win",
            },
            {
                "entity_name": "Under Guy",
                "selection": "under",
                "market_type": "points",
                "sportsbook_line": 12.5,
                "graded_result": "loss",
            },
        ]
    )

    import scripts.history_tracking as history_tracking

    original_loader = history_tracking._load_actual_results_for_date
    history_tracking._load_actual_results_for_date = lambda *_args, **_kwargs: actual_df.copy()
    try:
        grade_completed_picks(history_root=history_root, runtime_root=runtime_root)
    finally:
        history_tracking._load_actual_results_for_date = original_loader
    updated = pd.read_csv(history_root / "pick_history.csv")
    assert sorted(updated["result_status"].tolist()) == ["hit", "miss"]


def test_performance_summary_updates_correctly(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    history_root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "prediction_date": "2026-04-26",
                "run_timestamp": "2026-04-26T12:00:00+00:00",
                "player_name": "A",
                "team": "BOS",
                "opponent": "NYK",
                "game_id": "",
                "market": "points",
                "selection": "over",
                "line": 10.5,
                "projection": 12.0,
                "edge": 1.5,
                "abs_edge": 1.5,
                "odds": "-110",
                "confidence": "0.7",
                "quality_score": 70.0,
                "qualification_reason": "pass",
                "provider_used": "test",
                "result_status": "hit",
            },
            {
                "prediction_date": "2026-04-26",
                "run_timestamp": "2026-04-26T12:00:00+00:00",
                "player_name": "B",
                "team": "BOS",
                "opponent": "NYK",
                "game_id": "",
                "market": "points",
                "selection": "under",
                "line": 8.5,
                "projection": 7.0,
                "edge": -1.5,
                "abs_edge": 1.5,
                "odds": "-110",
                "confidence": "0.7",
                "quality_score": 70.0,
                "qualification_reason": "pass",
                "provider_used": "test",
                "result_status": "miss",
            },
        ]
    ).to_csv(history_root / "pick_history.csv", index=False)
    _write_audit(runtime_root / "operator" / "elite_pipeline_audit_summary_2026-04-26.json", max_team=2, max_game=4)

    update_performance_summaries(history_root=history_root, runtime_root=runtime_root)
    perf = pd.read_csv(history_root / "performance_summary.csv")
    assert len(perf) == 1
    assert perf.iloc[0]["total_picks"] == 2
    assert perf.iloc[0]["hits"] == 1
    assert perf.iloc[0]["misses"] == 1
    assert perf.iloc[0]["hit_rate"] == 0.5
    assert perf.iloc[0]["max_team_exposure"] == 2
    assert perf.iloc[0]["max_game_exposure"] == 4


def test_dashboard_loader_handles_empty_files(tmp_path: Path) -> None:
    history_root = tmp_path / "data" / "history"
    history_root.mkdir(parents=True, exist_ok=True)
    data = load_dashboard_data(history_root=history_root)
    assert set(data.keys()) == {"pick_history", "performance_summary", "by_side", "by_edge", "by_qualification"}
    for value in data.values():
        assert isinstance(value, pd.DataFrame)

