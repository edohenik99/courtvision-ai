from __future__ import annotations

from pathlib import Path

import pandas as pd

from courtvision.reporting.paper_kelly_performance import summarize_paper_kelly_history
from scripts.repair_pending_grades import repair_pending_grades
from scripts.write_daily_summary import write_daily_summary_outputs


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _feedback_row(
    player_name: str,
    *,
    prediction_date: str = "2026-05-05",
    team: str = "OKC",
    opponent: str = "LAL",
    market_type: str = "player_points",
    selection: str = "under",
    line: float = 16.5,
    actual_value: float = 14.0,
) -> dict:
    result = "push"
    if actual_value != line:
        result = "win" if (selection == "over" and actual_value > line) or (selection == "under" and actual_value < line) else "loss"
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "entity_name": player_name,
        "team": team,
        "team_abbr": team,
        "opponent": opponent,
        "market_type": market_type,
        "selection": selection,
        "sportsbook_line": line,
        "line": line,
        "actual_value": actual_value,
        "result": result,
        "graded_result": result,
    }


def _shadow_row(
    player_name: str,
    *,
    prediction_date: str = "2026-05-05",
    team: str = "OKC",
    opponent: str = "LAL",
    market_type: str = "player_points",
    selection: str = "under",
    line: float = 16.5,
    result_status: str = "pending",
    actual_value: str = "",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "player_id": "",
        "team_abbr": team,
        "opponent": opponent,
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "model_projection": 14.0,
        "edge": -2.5,
        "confidence": 0.75,
        "quality_score": 70.0,
        "selection_score": 60.0,
        "odds": -110,
        "line_source": "live_market",
        "context_pick_alignment": "aligned",
        "context_caution_level": "low",
        "context_conflict_cause": "",
        "kelly_projected_skip_reason": "",
        "final_elite_rejection_reason": "",
        "result_status": result_status,
        "actual_value": actual_value,
        "hit": False,
        "miss": False,
        "push": False,
        "shadow_roi": "",
        "calibration_eligible": False,
        "calibration_exclusion_reason": "no_graded_results",
    }


def _pick_row(player_name: str, *, result_status: str = "miss", actual_value: str = "") -> dict:
    return {
        "prediction_date": "2026-05-05",
        "run_timestamp": "2026-05-05T12:00:00+00:00",
        "player_name": player_name,
        "player_id": "",
        "team": "OKC",
        "opponent": "LAL",
        "game_id": "",
        "market": "player_points",
        "selection": "under",
        "line": 16.5,
        "projection": 14.0,
        "edge": -2.5,
        "abs_edge": 2.5,
        "odds": -110,
        "confidence": 0.75,
        "quality_score": 70.0,
        "qualification_reason": "test",
        "provider_used": "test",
        "result_status": result_status,
        "actual_value": actual_value,
        "grading_skip_reason": "",
    }


def _paper_row(player_name: str, *, result_status: str = "pending") -> dict:
    return {
        "prediction_date": "2026-05-05",
        "paper_bucket": "repair_test",
        "player_name": player_name,
        "team_abbr": "OKC",
        "opponent": "LAL",
        "market_type": "player_points",
        "selection": "under",
        "line": 16.5,
        "odds": -110,
        "edge": -2.5,
        "directional_edge": 2.5,
        "confidence": 0.75,
        "quality_score": 70.0,
        "context_pick_alignment": "aligned",
        "context_caution_level": "low",
        "simulated_fraction": 0.01,
        "simulated_stake": 10.0,
        "pre_cap_simulated_stake": 10.0,
        "cap_adjustment_reason": "none",
        "player_exposure_after_cap": 10.0,
        "team_exposure_after_cap": 10.0,
        "game_exposure_after_cap": 10.0,
        "side_exposure_after_cap": 10.0,
        "bucket_exposure_after_cap": 10.0,
        "simulated_ev": 1.0,
        "real_kelly_eligible": False,
        "simulation_only": True,
        "reason_not_real_kelly": "test",
        "result_status": result_status,
        "actual_value": "",
        "paper_profit": "",
        "paper_roi": "",
        "grading_skip_reason": "market_shadow_history_result_pending",
    }


def test_stale_pending_shadow_rows_are_repaired(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    _write_csv(runtime_root / "history" / "result_feedback.csv", [_feedback_row("Ajay Mitchell", actual_value=14.0)])
    _write_csv(history_root / "market_shadow_history.csv", [_shadow_row("Ajay Mitchell", result_status="pending")])

    repair_pending_grades(start_date="2026-05-05", end_date="2026-05-05", runtime_root=runtime_root, history_root=history_root)

    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False)
    row = shadow.iloc[0]
    assert row["result_status"] == "hit"
    assert float(row["actual_value"]) == 14.0
    assert str(row["hit"]).lower() == "true"
    assert float(row["shadow_roi"]) == 0.909091


def test_final_rows_missing_actual_value_are_repaired(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    _write_csv(runtime_root / "history" / "result_feedback.csv", [_feedback_row("Ajay Mitchell", actual_value=18.0)])
    _write_csv(history_root / "pick_history.csv", [_pick_row("Ajay Mitchell", result_status="miss", actual_value="")])
    _write_csv(
        history_root / "market_shadow_history.csv",
        [_shadow_row("Ajay Mitchell", result_status="miss", actual_value="")],
    )

    repair_pending_grades(start_date="2026-05-05", end_date="2026-05-05", runtime_root=runtime_root, history_root=history_root)

    pick = pd.read_csv(history_root / "pick_history.csv", keep_default_na=False).iloc[0]
    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False).iloc[0]
    assert pick["result_status"] == "miss"
    assert float(pick["actual_value"]) == 18.0
    assert shadow["result_status"] == "miss"
    assert float(shadow["actual_value"]) == 18.0
    assert str(shadow["miss"]).lower() == "true"


def test_fixture_rows_are_removed_and_do_not_affect_readiness_or_paper_reports(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    _write_csv(runtime_root / "history" / "result_feedback.csv", [_feedback_row("Real Player", actual_value=14.0)])
    _write_csv(
        history_root / "market_shadow_history.csv",
        [
            _shadow_row("Points Star", prediction_date="2024-01-15"),
            _shadow_row("High Edge"),
            _shadow_row("Real Player"),
        ],
    )
    _write_csv(history_root / "paper_kelly_history.csv", [_paper_row("High Edge"), _paper_row("Real Player")])

    result = repair_pending_grades(
        start_date="2026-05-05",
        end_date="2026-05-05",
        runtime_root=runtime_root,
        history_root=history_root,
    )

    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False)
    paper = pd.read_csv(history_root / "paper_kelly_history.csv", keep_default_na=False)
    readiness = pd.read_csv(history_root / "market_readiness_summary.csv", keep_default_na=False)
    assert set(shadow["player_name"]) == {"Real Player"}
    assert set(paper["player_name"]) == {"Real Player"}
    assert int(readiness["total"].sum()) == 1
    assert result["paper_kelly_summary"]["total"] == 1


def test_paper_void_rows_are_not_reported_as_pending() -> None:
    history = pd.DataFrame(
        [
            _paper_row("Void Row", result_status="void"),
            _paper_row("Pending Row", result_status="pending"),
        ]
    )

    summary = summarize_paper_kelly_history(history)

    assert summary["total"] == 2
    assert summary["graded_total"] == 0
    assert summary["pending"] == 1


def test_daily_summary_does_not_reset_repaired_shadow_rows(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-05"
    row = _shadow_row("Ajay Mitchell", prediction_date=date)
    missing_row = _shadow_row("Missing Actual", prediction_date=date, line=12.5)
    _write_csv(runtime_root / "history" / "result_feedback.csv", [_feedback_row("Ajay Mitchell", prediction_date=date, actual_value=14.0)])
    _write_csv(history_root / "market_shadow_history.csv", [row, missing_row])
    _write_csv(runtime_root / "operator" / f"full_market_board_{date}.csv", [row, missing_row])
    _write_csv(runtime_root / "operator" / f"elite_board_{date}.csv", [])
    _write_csv(runtime_root / "operator" / f"kelly_stakes_{date}.csv", [])

    repair_pending_grades(start_date=date, end_date=date, runtime_root=runtime_root, history_root=history_root)
    write_daily_summary_outputs(prediction_date=date, runtime_root=runtime_root, history_root=history_root)

    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False)
    by_player = shadow.set_index("player_name")
    assert by_player.loc["Ajay Mitchell", "result_status"] == "hit"
    assert float(by_player.loc["Ajay Mitchell", "actual_value"]) == 14.0
    assert by_player.loc["Missing Actual", "result_status"] == "void"
    assert by_player.loc["Missing Actual", "grading_skip_reason"] == "player_stat_match_missing"


def test_same_opponent_repeated_under_warning_is_flagged(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    _write_csv(
        runtime_root / "history" / "result_feedback.csv",
        [
            _feedback_row("Ajay Mitchell", prediction_date="2026-05-01", line=15.5, actual_value=18.0),
            _feedback_row("Ajay Mitchell", prediction_date="2026-05-05", line=16.5, actual_value=14.0),
        ],
    )
    _write_csv(history_root / "market_shadow_history.csv", [_shadow_row("Ajay Mitchell", line=16.5)])

    repair_pending_grades(start_date="2026-05-05", end_date="2026-05-05", runtime_root=runtime_root, history_root=history_root)

    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False)
    row = shadow.iloc[0]
    assert int(row["same_opponent_recent_games"]) == 1
    assert float(row["same_opponent_last_actual_points"]) == 18.0
    assert float(row["same_opponent_last_line"]) == 15.5
    assert row["same_opponent_last_selection"] == "under"
    assert str(row["same_opponent_under_warning"]).lower() == "true"
