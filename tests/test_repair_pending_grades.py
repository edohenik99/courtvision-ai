from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.paper_kelly_performance import summarize_paper_kelly_history
from scripts import repair_pending_grades as repair_module
from scripts.repair_pending_grades import repair_all_completed_grades, repair_pending_grades
from scripts.write_daily_summary import write_daily_summary_outputs


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _feedback_row(
    player_name: str,
    *,
    prediction_date: str = "2026-05-05",
    player_id: str = "",
    team: str = "OKC",
    opponent: str = "LAL",
    game_id: str = "",
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
        "player_id": player_id,
        "team": team,
        "team_abbr": team,
        "opponent": opponent,
        "game_id": game_id,
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
    player_id: str = "",
    team: str = "OKC",
    opponent: str = "LAL",
    game_id: str = "",
    market_type: str = "player_points",
    selection: str = "under",
    line: float = 16.5,
    result_status: str = "pending",
    actual_value: str = "",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "player_id": player_id,
        "team_abbr": team,
        "opponent": opponent,
        "game_id": game_id,
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


def _pick_row(
    player_name: str,
    *,
    prediction_date: str = "2026-05-05",
    result_status: str = "miss",
    actual_value: str = "",
) -> dict:
    return {
        "prediction_date": prediction_date,
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


def _paper_row(
    player_name: str,
    *,
    prediction_date: str = "2026-05-05",
    player_id: str = "",
    game_id: str = "",
    result_status: str = "pending",
    actual_value: str = "",
    grading_skip_reason: str = "market_shadow_history_result_pending",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "paper_bucket": "repair_test",
        "player_id": player_id,
        "player_name": player_name,
        "team_abbr": "OKC",
        "opponent": "LAL",
        "game_id": game_id,
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
        "actual_value": actual_value,
        "paper_profit": "",
        "paper_roi": "",
        "grading_skip_reason": grading_skip_reason,
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


def test_missing_date_actual_feedback_does_not_terminal_void_shadow_or_paper(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    _write_csv(
        runtime_root / "history" / "result_feedback.csv",
        [_feedback_row("Prior Player", prediction_date="2026-05-04", actual_value=14.0)],
    )
    _write_csv(history_root / "market_shadow_history.csv", [_shadow_row("Ajay Mitchell", prediction_date="2026-05-05")])
    _write_csv(history_root / "paper_kelly_history.csv", [_paper_row("Ajay Mitchell", prediction_date="2026-05-05")])

    result = repair_pending_grades(
        start_date="2026-05-05",
        end_date="2026-05-05",
        runtime_root=runtime_root,
        history_root=history_root,
    )

    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False).iloc[0]
    paper = pd.read_csv(history_root / "paper_kelly_history.csv", keep_default_na=False).iloc[0]
    assert shadow["result_status"] == "pending"
    assert paper["result_status"] == "pending"
    assert shadow["grading_skip_reason"] == "date_actual_feedback_missing"
    assert paper["grading_skip_reason"] == "date_actual_feedback_missing"
    assert result["histories"]["market_shadow_history"]["voided_rows"] == 0
    assert result["histories"]["paper_kelly_history"]["voided_rows"] == 0
    assert result["summary"]["stale_pending"] == 2


def test_date_actual_feedback_grades_shadow_and_paper_hit_miss(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    _write_csv(runtime_root / "history" / "result_feedback.csv", [_feedback_row("Ajay Mitchell", actual_value=18.0)])
    _write_csv(history_root / "market_shadow_history.csv", [_shadow_row("Ajay Mitchell", result_status="pending")])
    _write_csv(history_root / "paper_kelly_history.csv", [_paper_row("Ajay Mitchell", result_status="pending")])

    repair_pending_grades(start_date="2026-05-05", end_date="2026-05-05", runtime_root=runtime_root, history_root=history_root)

    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False).iloc[0]
    paper = pd.read_csv(history_root / "paper_kelly_history.csv", keep_default_na=False).iloc[0]
    assert shadow["result_status"] == "miss"
    assert float(shadow["actual_value"]) == 18.0
    assert str(shadow["miss"]).lower() == "true"
    assert paper["result_status"] == "miss"
    assert float(paper["actual_value"]) == 18.0
    assert float(paper["paper_profit"]) == -10.0
    assert float(paper["paper_roi"]) == -1.0


def test_terminal_player_stat_match_missing_shadow_and_paper_can_be_regraded(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    _write_csv(runtime_root / "history" / "result_feedback.csv", [_feedback_row("Ajay Mitchell", actual_value=14.0)])
    _write_csv(
        history_root / "pick_history.csv",
        [_pick_row("Ajay Mitchell", result_status="void", actual_value="") | {"grading_skip_reason": "player_stat_match_missing"}],
    )
    _write_csv(
        history_root / "market_shadow_history.csv",
        [_shadow_row("Ajay Mitchell", result_status="void", actual_value="") | {"grading_skip_reason": "player_stat_match_missing"}],
    )
    _write_csv(
        history_root / "paper_kelly_history.csv",
        [
            _paper_row(
                "Ajay Mitchell",
                result_status="void",
                actual_value="",
                grading_skip_reason="player_stat_match_missing",
            )
        ],
    )

    result = repair_pending_grades(
        start_date="2026-05-05",
        end_date="2026-05-05",
        runtime_root=runtime_root,
        history_root=history_root,
        regrade_terminal_player_stat_missing=True,
    )

    pick = pd.read_csv(history_root / "pick_history.csv", keep_default_na=False).iloc[0]
    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False).iloc[0]
    paper = pd.read_csv(history_root / "paper_kelly_history.csv", keep_default_na=False).iloc[0]
    assert pick["result_status"] == "void"
    assert pick["grading_skip_reason"] == "player_stat_match_missing"
    assert shadow["result_status"] == "hit"
    assert float(shadow["actual_value"]) == 14.0
    assert paper["result_status"] == "hit"
    assert float(paper["actual_value"]) == 14.0
    assert result["histories"]["pick_history"]["repaired_rows"] == 0
    assert result["histories"]["market_shadow_history"]["pending_taxonomy"]["terminal_player_stat_match_missing_regraded"] == 1
    assert result["histories"]["paper_kelly_history"]["pending_taxonomy"]["terminal_player_stat_match_missing_regraded"] == 1


def test_actual_lookup_prefers_player_id_game_id_when_available(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    _write_csv(
        runtime_root / "history" / "result_feedback.csv",
        [
            _feedback_row("Same Name", player_id="20", game_id="200", actual_value=20.0),
            _feedback_row("Same Name", player_id="10", game_id="100", actual_value=14.0),
        ],
    )
    _write_csv(
        history_root / "market_shadow_history.csv",
        [_shadow_row("Same Name", player_id="20", game_id="200", result_status="pending")],
    )

    repair_pending_grades(start_date="2026-05-05", end_date="2026-05-05", runtime_root=runtime_root, history_root=history_root)

    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False).iloc[0]
    assert shadow["result_status"] == "miss"
    assert float(shadow["actual_value"]) == 20.0


def test_direct_combo_actual_feedback_grades_combo_shadow_rows(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    _write_csv(
        runtime_root / "history" / "result_feedback.csv",
        [
            _feedback_row(
                "Combo Player",
                market_type="player_points_rebounds",
                selection="under",
                line=30.5,
                actual_value=28.0,
            )
        ],
    )
    _write_csv(
        history_root / "market_shadow_history.csv",
        [
            _shadow_row(
                "Combo Player",
                market_type="player_points_rebounds",
                selection="under",
                line=30.5,
                result_status="pending",
            )
        ],
    )

    repair_pending_grades(start_date="2026-05-05", end_date="2026-05-05", runtime_root=runtime_root, history_root=history_root)

    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False).iloc[0]
    assert shadow["result_status"] == "hit"
    assert float(shadow["actual_value"]) == 28.0


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
    current_date = repair_module._today_iso()
    history = pd.DataFrame(
        [
            _paper_row("Void Row", result_status="void"),
            _paper_row("Pending Row", result_status="pending"),
            _paper_row(
                "Open Row",
                prediction_date=current_date,
                result_status="pending",
                grading_skip_reason="game_not_final",
            ),
        ]
    )

    summary = summarize_paper_kelly_history(history)

    assert summary["total"] == 3
    assert summary["graded_total"] == 0
    assert summary["pending"] == 2
    assert summary["open_pending"] == 1
    assert summary["stale_pending"] == 1


def test_all_completed_repairs_old_stale_pending_rows(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    old_date = "2026-05-04"
    _write_csv(
        runtime_root / "history" / "result_feedback.csv",
        [_feedback_row("Backlog Player", prediction_date=old_date, actual_value=14.0)],
    )
    _write_csv(history_root / "pick_history.csv", [_pick_row("Backlog Player", prediction_date=old_date, result_status="pending")])
    _write_csv(history_root / "market_shadow_history.csv", [_shadow_row("Backlog Player", prediction_date=old_date)])
    _write_csv(history_root / "paper_kelly_history.csv", [_paper_row("Backlog Player", prediction_date=old_date)])

    exit_code = repair_module.main(
        [
            "--all-completed",
            "--through-date",
            "2026-05-06",
            "--history-root",
            str(history_root),
            "--runtime-root",
            str(runtime_root),
        ]
    )

    assert exit_code == 0
    for filename in ("pick_history.csv", "market_shadow_history.csv", "paper_kelly_history.csv"):
        history = pd.read_csv(history_root / filename, keep_default_na=False)
        row = history.iloc[0]
        assert row["result_status"] == "hit"
        assert float(row["actual_value"]) == 14.0
    audit_files = list((runtime_root / "diagnostics").glob("pending_repair_audit_*.json"))
    report_files = list((runtime_root / "operator").glob("pending_repair_report_*.txt"))
    assert len(audit_files) == 1
    assert len(report_files) == 1
    audit = json.loads(audit_files[0].read_text(encoding="utf-8"))
    assert audit["mode"] == "all_completed"
    assert audit["summary"]["stale_pending"] == 0
    assert audit["summary"]["repaired_rows"] == 3


def test_current_date_game_not_final_rows_remain_pending_open_game(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    current_date = repair_module._today_iso()
    _write_csv(history_root / "market_shadow_history.csv", [_shadow_row("Open Slate Player", prediction_date=current_date)])

    result = repair_all_completed_grades(
        history_root=history_root,
        runtime_root=runtime_root,
        include_current_date=True,
    )

    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False)
    row = shadow.iloc[0]
    assert row["result_status"] == "pending"
    assert row["grading_skip_reason"] == "game_not_final"
    assert result["summary"]["total_pending"] == 1
    assert result["summary"]["open_game_pending"] == 1
    assert result["summary"]["stale_pending"] == 0


def test_current_provider_missing_finality_rows_remain_pending_open_game(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    current_date = repair_module._today_iso()
    row = {
        **_shadow_row("Provider Gap Player", prediction_date=current_date),
        "grading_skip_reason": "provider_missing_finality",
    }
    _write_csv(history_root / "market_shadow_history.csv", [row])

    result = repair_all_completed_grades(
        history_root=history_root,
        runtime_root=runtime_root,
        include_current_date=True,
    )

    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False)
    row = shadow.iloc[0]
    assert row["result_status"] == "pending"
    assert row["grading_skip_reason"] == "provider_missing_finality"
    assert result["summary"]["total_pending"] == 1
    assert result["summary"]["open_game_pending"] == 1
    assert result["summary"]["stale_pending"] == 0


def test_repair_terminal_unsupported_market_remains_unsupported(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    current_date = repair_module._today_iso()
    _write_csv(
        history_root / "market_shadow_history.csv",
        [
            _shadow_row(
                "Unsupported Repair Player",
                prediction_date=current_date,
                market_type="player_threes",
            )
        ],
    )

    result = repair_all_completed_grades(
        history_root=history_root,
        runtime_root=runtime_root,
        include_current_date=True,
    )

    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False)
    row = shadow.iloc[0]
    assert row["result_status"] == "unsupported"
    assert row["grading_skip_reason"] == "unsupported_market"
    assert result["summary"]["unsupported_rows"] == 1
    assert result["summary"]["open_game_pending"] == 0


def test_old_provider_missing_finality_rows_defer_when_date_actuals_are_absent(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    old_date = "2026-05-04"
    row = {
        **_shadow_row("Old Provider Gap", prediction_date=old_date),
        "grading_skip_reason": "provider_missing_finality",
    }
    _write_csv(history_root / "market_shadow_history.csv", [row])

    result = repair_all_completed_grades(
        history_root=history_root,
        runtime_root=runtime_root,
        through_date="2026-05-06",
    )

    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False)
    row = shadow.iloc[0]
    assert row["result_status"] == "pending"
    assert row["grading_skip_reason"] == "date_actual_feedback_missing"
    assert result["summary"]["open_game_pending"] == 0
    assert result["summary"]["stale_pending"] == 1
    assert result["summary"]["voided_rows"] == 0


def test_completed_rows_do_not_stay_plain_pending_without_reason(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    old_date = "2026-05-04"
    _write_csv(history_root / "market_shadow_history.csv", [_shadow_row("Missing Result", prediction_date=old_date)])

    result = repair_all_completed_grades(
        history_root=history_root,
        runtime_root=runtime_root,
        through_date="2026-05-06",
    )

    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False)
    row = shadow.iloc[0]
    assert row["result_status"] == "pending"
    assert row["actual_value"] == ""
    assert row["grading_skip_reason"] == "date_actual_feedback_missing"
    assert result["summary"]["stale_pending"] == 1
    assert result["summary"]["voided_rows"] == 0


def test_all_completed_hit_miss_rows_cannot_have_blank_actual_value(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    old_date = "2026-05-04"
    _write_csv(
        runtime_root / "history" / "result_feedback.csv",
        [_feedback_row("Actual Repair", prediction_date=old_date, actual_value=18.0)],
    )
    _write_csv(
        history_root / "market_shadow_history.csv",
        [_shadow_row("Actual Repair", prediction_date=old_date, result_status="miss", actual_value="")],
    )

    result = repair_all_completed_grades(
        history_root=history_root,
        runtime_root=runtime_root,
        through_date="2026-05-06",
    )

    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False)
    row = shadow.iloc[0]
    assert row["result_status"] == "miss"
    assert float(row["actual_value"]) == 18.0
    assert result["summary"]["final_missing_actual_rows"] == 0


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


def test_repair_history_df_handles_arrow_string_columns() -> None:
    base = pd.DataFrame([_shadow_row("Ajay Mitchell")])
    base["grading_skip_reason"] = ""
    base["same_opponent_recent_games"] = ""
    base["same_opponent_under_warning"] = ""
    base = base.astype(
        {
            "result_status": "string[pyarrow]",
            "actual_value": "string[pyarrow]",
            "grading_skip_reason": "string[pyarrow]",
            "same_opponent_recent_games": "string[pyarrow]",
            "same_opponent_under_warning": "string[pyarrow]",
        }
    )
    feedback = pd.DataFrame([_feedback_row("Ajay Mitchell", actual_value=14.0)])
    lookup = repair_module.ActualLookup(feedback)

    repaired, _summary = repair_module._repair_history_df(
        base,
        start_date="2026-05-05",
        end_date="2026-05-05",
        lookup=lookup,
        source_name="market_shadow_history",
    )
    row = repaired.iloc[0]
    assert row["result_status"] == "hit"
    assert row["actual_value"] == "14.0"
    assert row["same_opponent_recent_games"] in {"", "0"}


def test_all_completed_terminal_regrade_only_affects_scoped_date(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    
    # 1. Write actual feedback for all three test players
    _write_csv(
        runtime_root / "history" / "result_feedback.csv",
        [
            # Ajay Mitchell: May 23 (target date) terminal player_stat_match_missing
            _feedback_row("Ajay Mitchell", prediction_date="2026-05-23", actual_value=14.0),
            # Backlog Player: May 22 (prior date) terminal player_stat_match_missing
            _feedback_row("Backlog Player", prediction_date="2026-05-22", actual_value=14.0),
            # Stale Pending Player: May 22 (prior date) stale pending
            _feedback_row("Stale Pending Player", prediction_date="2026-05-22", actual_value=14.0),
        ],
    )
    
    # 2. Write market shadow history rows
    _write_csv(
        history_root / "market_shadow_history.csv",
        [
            # Ajay Mitchell: May 23 terminal player_stat_match_missing
            _shadow_row("Ajay Mitchell", prediction_date="2026-05-23", result_status="void", actual_value="")
            | {"grading_skip_reason": "player_stat_match_missing"},
            # Backlog Player: May 22 terminal player_stat_match_missing
            _shadow_row("Backlog Player", prediction_date="2026-05-22", result_status="void", actual_value="")
            | {"grading_skip_reason": "player_stat_match_missing"},
            # Stale Pending Player: May 22 stale pending row
            _shadow_row("Stale Pending Player", prediction_date="2026-05-22", result_status="pending", actual_value=""),
        ],
    )
    
    # 3. Invoke all_completed repair with both regrade flags
    result = repair_all_completed_grades(
        history_root=history_root,
        runtime_root=runtime_root,
        through_date="2026-05-23",
        regrade_terminal_player_stat_missing=True,
        terminal_regrade_date="2026-05-23",
    )
    
    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False).set_index("player_name")
    
    # Prove Ajay Mitchell (scoped date) is regraded to hit
    assert shadow.loc["Ajay Mitchell", "result_status"] == "hit"
    assert float(shadow.loc["Ajay Mitchell", "actual_value"]) == 14.0
    
    # Prove Backlog Player (prior date) remains void and untouched
    assert shadow.loc["Backlog Player", "result_status"] == "void"
    assert shadow.loc["Backlog Player", "grading_skip_reason"] == "player_stat_match_missing"
    
    # Prove Stale Pending Player (prior date) is normally repaired to hit
    assert shadow.loc["Stale Pending Player", "result_status"] == "hit"
    assert float(shadow.loc["Stale Pending Player", "actual_value"]) == 14.0
