from __future__ import annotations

from pathlib import Path

import pandas as pd

from courtvision.reporting.shadow_candidate_lane_performance import (
    COMBO_OVER_WEAK_POSITIVE_RESEARCH,
    UNDER_ALIGNED_RESEARCH,
    grade_shadow_candidate_lane_history,
    persist_daily_shadow_candidate_lane,
    write_shadow_candidate_lane_performance_outputs,
)


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _shadow_row(
    player: str,
    *,
    prediction_date: str = "2026-05-29",
    source_artifact_date: str | None = None,
    lane: str = UNDER_ALIGNED_RESEARCH,
    player_id: str | None = None,
    market_type: str = "player_points",
    selection: str = "under",
    line: float = 20.5,
    odds: int = -110,
    edge: float = -2.0,
    confidence: float = 0.74,
    team: str = "OKC",
    opponent: str = "SAS",
    game_id: str = "game-1",
) -> dict:
    return {
        "rank": 1,
        "prediction_date": prediction_date,
        "source_artifact_date": source_artifact_date or prediction_date,
        "source_board": "full_market_board",
        "research_lane": lane,
        "rank_score": 612.0,
        "player_id": player_id or player.lower().replace(" ", "-"),
        "player_name": player,
        "team_abbr": team,
        "opponent": opponent,
        "game_id": game_id,
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "odds": odds,
        "model_projection": line + edge,
        "edge": edge,
        "confidence": confidence,
        "quality_score": 61.0,
        "selection_score": 50.0,
        "context_pick_alignment": "aligned",
        "context_caution_level": "low",
        "context_edge_label": "aligned",
        "source_rejection_reason": "full_market_board",
        "historical_bucket_key": "bucket",
        "historical_recommendation": "NEED_MORE_DATA",
        "historical_graded_rows": 15,
        "historical_hit_rate": 0.6,
        "historical_roi": 0.1,
        "historical_clv_coverage_rate": 0.0,
        "promotion_status": "SHADOW_ONLY_DO_NOT_PROMOTE",
        "real_money_eligible": False,
        "kelly_eligible": False,
        "elite_eligible": False,
        "shadow_only": True,
    }


def _actual_row(
    player: str,
    *,
    prediction_date: str = "2026-05-29",
    market_type: str = "player_points",
    selection: str = "under",
    line: float = 20.5,
    actual_value: float = 18.0,
    result: str = "win",
    team: str = "OKC",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "entity_name": player,
        "player_name": player,
        "player_id": "",
        "team": team,
        "team_abbr": team,
        "market_type": market_type,
        "selection": selection,
        "sportsbook_line": line,
        "actual_value": actual_value,
        "result": result,
        "graded_result": result,
    }


def test_persist_shadow_lane_history_is_separate_deduped_and_pick_history_untouched(tmp_path: Path) -> None:
    prediction_date = "2026-05-29"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    row = _shadow_row("Under Candidate", prediction_date=prediction_date)
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{prediction_date}.csv", [row, row.copy()])
    pick_history = history_root / "pick_history.csv"
    _write_csv(pick_history, [{"prediction_date": "2026-05-28", "player_name": "Elite", "result_status": "hit"}])
    pick_before = pick_history.read_bytes()

    result = persist_daily_shadow_candidate_lane(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    rerun = persist_daily_shadow_candidate_lane(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    history_path = history_root / "shadow_candidate_lane_history.csv"
    history = pd.read_csv(history_path, keep_default_na=False)
    assert result["incoming_rows"] == 2
    assert result["persisted_rows"] == 1
    assert rerun["persisted_rows"] == 1
    assert len(history) == 1
    assert history.iloc[0]["player"] == "Under Candidate"
    assert history.iloc[0]["result_status"] == "pending"
    assert history.iloc[0]["grading_status"] == "open_game_pending"
    assert history.iloc[0]["grading_reason"] == "game_not_final"
    assert str(history.iloc[0]["real_money_eligible"]).lower() == "false"
    assert str(history.iloc[0]["kelly_eligible"]).lower() == "false"
    assert str(history.iloc[0]["elite_eligible"]).lower() == "false"
    assert str(history.iloc[0]["shadow_only"]).lower() == "true"
    assert pick_history.read_bytes() == pick_before


def test_grade_shadow_lane_hit_miss_push_and_report_counts(tmp_path: Path) -> None:
    prediction_date = "2026-05-29"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    rows = [
        _shadow_row("Hit Under", prediction_date=prediction_date, selection="under", line=20.5, odds=-110),
        _shadow_row("Miss Over", prediction_date=prediction_date, selection="over", line=10.5, odds=-110),
        _shadow_row("Push Over", prediction_date=prediction_date, selection="over", line=12.0, odds=-110),
    ]
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{prediction_date}.csv", rows)
    _write_csv(
        runtime_root / "history" / "result_feedback.csv",
        [
            _actual_row("Hit Under", prediction_date=prediction_date, selection="under", line=20.5, actual_value=18.0, result="win"),
            _actual_row("Miss Over", prediction_date=prediction_date, selection="over", line=10.5, actual_value=9.0, result="loss"),
            _actual_row("Push Over", prediction_date=prediction_date, selection="over", line=12.0, actual_value=12.0, result="push"),
        ],
    )

    txt_path, csv_path, json_path, payload = write_shadow_candidate_lane_performance_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert txt_path.exists()
    assert csv_path.exists()
    assert json_path.exists()
    overall = payload["overall"]
    assert overall["total_rows"] == 3
    assert overall["graded_rows"] == 3
    assert overall["pending_rows"] == 0
    assert overall["hits"] == 1
    assert overall["misses"] == 1
    assert overall["pushes"] == 1
    assert overall["hit_rate"] == 0.5
    assert overall["flat_profit_loss"] == -0.090909
    assert overall["flat_roi"] == -0.030303
    report_csv = pd.read_csv(csv_path, keep_default_na=False)
    overall_csv = report_csv[report_csv["dimension"] == "overall"].iloc[0]
    assert int(overall_csv["total_rows"]) == 3
    assert int(overall_csv["graded_rows"]) == 3


def test_combo_shadow_lane_grades_from_component_actuals(tmp_path: Path) -> None:
    prediction_date = "2026-05-29"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    combo = _shadow_row(
        "Combo Player",
        prediction_date=prediction_date,
        lane=COMBO_OVER_WEAK_POSITIVE_RESEARCH,
        market_type="player_points_rebounds_assists",
        selection="over",
        line=30.5,
        odds=120,
    )
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{prediction_date}.csv", [combo])
    _write_csv(
        runtime_root / "history" / "result_feedback.csv",
        [
            _actual_row("Combo Player", prediction_date=prediction_date, market_type="player_points", actual_value=18.0),
            _actual_row("Combo Player", prediction_date=prediction_date, market_type="player_rebounds", actual_value=8.0),
            _actual_row("Combo Player", prediction_date=prediction_date, market_type="player_assists", actual_value=6.0),
        ],
    )

    write_shadow_candidate_lane_performance_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    history = pd.read_csv(history_root / "shadow_candidate_lane_history.csv", keep_default_na=False)
    assert history.iloc[0]["result_status"] == "hit"
    assert float(history.iloc[0]["actual_value"]) == 32.0
    assert float(history.iloc[0]["flat_profit_loss"]) == 1.2


def test_open_shadow_lane_rows_remain_pending_game_not_final(tmp_path: Path) -> None:
    prediction_date = "2026-05-29"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _write_csv(
        runtime_root / "operator" / f"shadow_candidate_lane_{prediction_date}.csv",
        [_shadow_row("Pending Player", prediction_date=prediction_date)],
    )

    persist_daily_shadow_candidate_lane(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    result = grade_shadow_candidate_lane_history(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    history = pd.read_csv(history_root / "shadow_candidate_lane_history.csv", keep_default_na=False)
    assert result["updated_rows"] == 0
    assert result["pending_rows"] == 1
    assert history.iloc[0]["result_status"] == "pending"
    assert history.iloc[0]["grading_status"] == "open_game_pending"
    assert history.iloc[0]["grading_reason"] == "game_not_final"


def test_missing_shadow_lane_artifacts_do_not_crash_report(tmp_path: Path) -> None:
    prediction_date = "2026-05-29"
    txt_path, csv_path, json_path, payload = write_shadow_candidate_lane_performance_outputs(
        prediction_date=prediction_date,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
    )

    assert txt_path.exists()
    assert csv_path.exists()
    assert json_path.exists()
    assert payload["persist_result"]["incoming_rows"] == 0
    assert payload["overall"]["total_rows"] == 0
    assert payload["overall"]["graded_rows"] == 0
    assert payload["all_rows_real_money_eligible_false"] is True


def test_date_integrity_mismatch_skips_persistence(tmp_path: Path) -> None:
    prediction_date = "2026-05-29"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    row = _shadow_row("Mismatch Candidate", prediction_date=prediction_date, source_artifact_date="2026-05-28")
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{prediction_date}.csv", [row])
    
    result = persist_daily_shadow_candidate_lane(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    
    assert result["source_date_mismatch"] is True
    assert result["history_persistence_status"] == "skipped_source_date_mismatch"
    assert result["persisted_rows"] == 0
    
    history_path = history_root / "shadow_candidate_lane_history.csv"
    assert not history_path.exists()


def test_date_integrity_override_persists(tmp_path: Path) -> None:
    prediction_date = "2026-05-29"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    row = _shadow_row("Override Candidate", prediction_date=prediction_date, source_artifact_date="2026-05-28")
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{prediction_date}.csv", [row])
    
    result = persist_daily_shadow_candidate_lane(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        override_date_integrity=True,
    )
    
    assert result["source_date_mismatch"] is True
    assert result["history_persistence_status"] == "persisted_with_override"
    assert result["persisted_rows"] == 1
    
    history_path = history_root / "shadow_candidate_lane_history.csv"
    assert history_path.exists()


def test_date_integrity_missing_source_date_skips(tmp_path: Path) -> None:
    prediction_date = "2026-05-29"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    row = _shadow_row("Missing Candidate", prediction_date=prediction_date)
    del row["source_artifact_date"]
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{prediction_date}.csv", [row])
    
    result = persist_daily_shadow_candidate_lane(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    
    assert result["source_date_mismatch"] is True
    assert result["history_persistence_status"] == "skipped_source_date_mismatch"
    assert result["persisted_rows"] == 0
    
    history_path = history_root / "shadow_candidate_lane_history.csv"
    assert not history_path.exists()


def test_date_integrity_report_written_even_when_skipped(tmp_path: Path) -> None:
    prediction_date = "2026-05-29"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    
    row = _shadow_row("Report Candidate", prediction_date=prediction_date, source_artifact_date="2026-05-28")
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{prediction_date}.csv", [row])
    
    txt_path, csv_path, json_path, payload = write_shadow_candidate_lane_performance_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        grade_pending=False,
    )
    
    assert txt_path.exists()
    assert csv_path.exists()
    assert json_path.exists()
    
    assert payload["source_date_mismatch"] is True
    assert payload["history_persistence_status"] == "skipped_source_date_mismatch"
    assert payload["warning"] == "SOURCE_DATE_MISMATCH"
    assert payload["report_date"] == prediction_date
    assert payload["prediction_date"] == prediction_date
    assert payload["source_artifact_date"] == "2026-05-28"
    
    txt_content = txt_path.read_text(encoding="utf-8")
    assert "!!! WARNING: SOURCE_DATE_MISMATCH !!!" in txt_content
    assert f"Report Date ({prediction_date}) does not match Source Artifact Date (2026-05-28)" in txt_content


def test_date_integrity_pick_history_untouched(tmp_path: Path) -> None:
    prediction_date = "2026-05-29"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    
    pick_history = history_root / "pick_history.csv"
    pick_rows = [{"prediction_date": "2026-05-28", "player_name": "Elite Player", "result_status": "hit"}]
    _write_csv(pick_history, pick_rows)
    pick_before = pick_history.read_bytes()
    
    row = _shadow_row("Mismatched Candidate", prediction_date=prediction_date, source_artifact_date="2026-05-28")
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{prediction_date}.csv", [row])
    
    write_shadow_candidate_lane_performance_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    
    assert pick_history.read_bytes() == pick_before
