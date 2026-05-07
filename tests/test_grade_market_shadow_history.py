from __future__ import annotations

from pathlib import Path

import pandas as pd

import scripts.grade_market_shadow_history as subject
from scripts.grade_market_shadow_history import grade_market_shadow_history, main as grade_shadow_main
from scripts.market_shadow_grading import write_market_shadow_outputs
from scripts.write_daily_summary import write_daily_summary_outputs


def _shadow_row(
    player_name: str,
    *,
    prediction_date: str = "2026-05-06",
    market_type: str = "player_points_rebounds",
    selection: str = "over",
    line: float = 30.5,
    odds: int = -110,
    result_status: str = "pending",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "player_id": f"id-{player_name}",
        "team_abbr": "BOS",
        "opponent": "NYK",
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "model_projection": line + 1.0,
        "edge": 1.0,
        "confidence": 0.7,
        "quality_score": 75.0,
        "selection_score": 70.0,
        "odds": odds,
        "line_source": "live_market",
        "context_pick_alignment": "aligned",
        "context_caution_level": "low",
        "context_conflict_cause": "",
        "kelly_projected_skip_reason": "kelly_points_only_market_lock",
        "final_elite_rejection_reason": "",
        "result_status": result_status,
        "actual_value": "",
        "hit": "",
        "miss": "",
        "push": "",
        "shadow_roi": "",
        "calibration_eligible": "",
        "calibration_exclusion_reason": "",
    }


def _actual_row(
    player_name: str,
    market_type: str,
    actual_value: float,
    *,
    prediction_date: str = "2026-05-06",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "entity_name": player_name,
        "market_type": market_type,
        "selection": "over",
        "sportsbook_line": 1.5,
        "actual_value": actual_value,
        "result": "win",
        "graded_result": "win",
    }


def _write_shadow_history(history_root: Path, rows: list[dict]) -> Path:
    history_root.mkdir(parents=True, exist_ok=True)
    path = history_root / "market_shadow_history.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _write_result_feedback(runtime_root: Path, rows: list[dict]) -> Path:
    path = runtime_root / "history" / "result_feedback.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _write_operator_csv(runtime_root: Path, filename: str, rows: list[dict]) -> Path:
    path = runtime_root / "operator" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_combo_shadow_grading_updates_combo_rows_only(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"
    _write_shadow_history(
        history_root,
        [
            _shadow_row("Combo Star", prediction_date=date, market_type="player_points_rebounds", line=30.5),
            _shadow_row("Points Only", prediction_date=date, market_type="player_points", line=20.5),
        ],
    )
    _write_result_feedback(
        runtime_root,
        [
            _actual_row("Combo Star", "player_points", 22.0, prediction_date=date),
            _actual_row("Combo Star", "player_rebounds", 10.0, prediction_date=date),
        ],
    )

    result = grade_market_shadow_history(runtime_root=runtime_root, history_root=history_root, dates=[date])

    assert result["updated_rows"] == 1
    assert result["updated_by_market_type"] == {"player_points_rebounds": 1}
    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False)
    combo = shadow[shadow["player_name"] == "Combo Star"].iloc[0]
    assert float(combo["actual_value"]) == 32.0
    assert combo["result_status"] == "hit"
    assert str(combo["hit"]).lower() == "true"
    assert float(combo["shadow_roi"]) == 0.909091
    assert str(combo["calibration_eligible"]).lower() == "true"
    points = shadow[shadow["player_name"] == "Points Only"].iloc[0]
    assert points["result_status"] == "pending"
    assert points["actual_value"] == ""
    readiness = pd.read_csv(history_root / "market_readiness_summary.csv", keep_default_na=False)
    combo_readiness = readiness[readiness["market_type"] == "player_points_rebounds"].iloc[0]
    assert int(combo_readiness["total"]) == 1
    assert int(combo_readiness["graded_total"]) == 1
    assert int(combo_readiness["hits"]) == 1


def test_combo_shadow_market_type_accepts_raw_prop_type_aliases() -> None:
    assert subject._market_type(pd.Series({"market_type": "points_rebounds"})) == "player_points_rebounds"
    assert subject._market_type(pd.Series({"market_type": "", "raw_prop_type": "points_assists"})) == "player_points_assists"


def test_daily_summary_preserves_graded_combo_shadow_rows_and_report_counts(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"
    full_market_rows = [
        _shadow_row("Combo Star", prediction_date=date, market_type="player_points_rebounds", line=30.5),
        _shadow_row("Assist Shadow", prediction_date=date, market_type="player_assists", line=5.5),
    ]
    _write_operator_csv(runtime_root, f"full_market_board_{date}.csv", full_market_rows)
    _write_operator_csv(runtime_root, f"elite_board_{date}.csv", [_shadow_row("Elite Points", prediction_date=date)])
    _write_operator_csv(
        runtime_root,
        f"kelly_stakes_{date}.csv",
        [
            {
                "player_name": "Elite Points",
                "market_type": "player_points",
                "eligible": True,
                "stake_amount": 8.0,
                "expected_value": 1.0,
            }
        ],
    )
    _write_shadow_history(history_root, full_market_rows)
    _write_result_feedback(
        runtime_root,
        [
            _actual_row("Combo Star", "player_points", 22.0, prediction_date=date),
            _actual_row("Combo Star", "player_rebounds", 10.0, prediction_date=date),
        ],
    )

    grade_result = grade_market_shadow_history(runtime_root=runtime_root, history_root=history_root, dates=[date])
    diagnostics_path, report_path, payload = write_market_shadow_outputs(
        prediction_date=date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert grade_result["updated_rows"] == 1
    assert payload["totals"]["graded_picks"] == 1
    assert "Totals: total=2 graded=1 pending=1" in report_path.read_text(encoding="utf-8")
    assert diagnostics_path.exists()

    write_daily_summary_outputs(prediction_date=date, runtime_root=runtime_root, history_root=history_root)
    shadow_after_summary = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False)
    same_date = shadow_after_summary[shadow_after_summary["prediction_date"].astype(str) == date]
    combo = same_date[same_date["player_name"] == "Combo Star"].iloc[0]
    assert len(same_date) == 2
    assert same_date["player_name"].tolist().count("Combo Star") == 1
    assert combo["result_status"] == "hit"
    assert float(combo["actual_value"]) == 32.0
    assert str(combo["hit"]).lower() == "true"
    assert int(same_date["result_status"].astype(str).eq("pending").sum()) == 1

    after_first_summary = (history_root / "market_shadow_history.csv").read_bytes()
    write_daily_summary_outputs(prediction_date=date, runtime_root=runtime_root, history_root=history_root)
    assert (history_root / "market_shadow_history.csv").read_bytes() == after_first_summary


def test_combo_shadow_grading_dry_run_does_not_write(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"
    shadow_path = _write_shadow_history(
        history_root,
        [_shadow_row("Combo Star", prediction_date=date, market_type="player_points_assists", line=27.5)],
    )
    _write_result_feedback(
        runtime_root,
        [
            _actual_row("Combo Star", "player_points", 22.0, prediction_date=date),
            _actual_row("Combo Star", "player_assists", 7.0, prediction_date=date),
        ],
    )
    before = shadow_path.read_bytes()

    result = grade_market_shadow_history(runtime_root=runtime_root, history_root=history_root, dates=[date], dry_run=True)

    assert result["updated_rows"] == 0
    assert result["would_update_rows"] == 1
    assert shadow_path.read_bytes() == before
    assert not (history_root / "market_readiness_summary.csv").exists()


def test_combo_shadow_grading_requires_all_component_stats(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"
    _write_shadow_history(
        history_root,
        [_shadow_row("Combo Star", prediction_date=date, market_type="player_points_assists", line=27.5)],
    )
    _write_result_feedback(
        runtime_root,
        [_actual_row("Combo Star", "player_points", 22.0, prediction_date=date)],
    )

    result = grade_market_shadow_history(runtime_root=runtime_root, history_root=history_root, dates=[date])

    assert result["updated_rows"] == 0
    assert result["skip_reasons"] == {"missing_component_stats": 1}
    assert result["combo_component_diagnostics"]["by_date"][date]["component_stats_available"] == 0
    assert result["combo_component_diagnostics"]["by_date"][date]["missing_component_stats"] == 1
    assert result["combo_component_diagnostics"]["top_missing_players_dates"][0]["player_name"] == "Combo Star"
    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False)
    assert shadow.iloc[0]["result_status"] == "pending"
    assert shadow.iloc[0]["actual_value"] == ""


def test_combo_shadow_grading_replace_existing_regenerates_combo_result(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"
    row = _shadow_row(
        "Combo Star",
        prediction_date=date,
        market_type="player_rebounds_assists",
        selection="under",
        line=16.5,
        result_status="miss",
    )
    row["actual_value"] = 20.0
    row["miss"] = True
    _write_shadow_history(history_root, [row])
    _write_result_feedback(
        runtime_root,
        [
            _actual_row("Combo Star", "player_rebounds", 8.0, prediction_date=date),
            _actual_row("Combo Star", "player_assists", 6.0, prediction_date=date),
        ],
    )

    skipped = grade_market_shadow_history(runtime_root=runtime_root, history_root=history_root, dates=[date])
    replaced = grade_market_shadow_history(
        runtime_root=runtime_root,
        history_root=history_root,
        dates=[date],
        replace_existing=True,
    )

    assert skipped["updated_rows"] == 0
    assert skipped["skipped_already_graded"] == 1
    assert replaced["updated_rows"] == 1
    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False)
    assert float(shadow.iloc[0]["actual_value"]) == 14.0
    assert shadow.iloc[0]["result_status"] == "hit"
    assert str(shadow.iloc[0]["hit"]).lower() == "true"


def test_grade_market_shadow_history_cli_dry_run(tmp_path: Path, capsys) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"
    shadow_path = _write_shadow_history(
        history_root,
        [_shadow_row("Combo Star", prediction_date=date, market_type="player_points_rebounds_assists", line=34.5)],
    )
    _write_result_feedback(
        runtime_root,
        [
            _actual_row("Combo Star", "player_points", 22.0, prediction_date=date),
            _actual_row("Combo Star", "player_rebounds", 10.0, prediction_date=date),
            _actual_row("Combo Star", "player_assists", 5.0, prediction_date=date),
        ],
    )
    before = shadow_path.read_bytes()

    code = grade_shadow_main(
        [
            "--runtime-root",
            str(runtime_root),
            "--history-root",
            str(history_root),
            "--prediction-date",
            date,
            "--dry-run",
        ]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "would_update_rows=1" in out
    assert "combo_by_date=2026-05-06,combo_rows=1,component_stats_available=1,missing_component_stats=0" in out
    assert "stat_source_used=local_artifacts,1" in out
    assert "top_missing_player_date=none" in out
    assert "dry_run=true" in out
    assert "scope=combo_shadow_only" in out
    assert shadow_path.read_bytes() == before


def test_combo_shadow_grading_auto_provider_fills_missing_components(tmp_path: Path, monkeypatch) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"
    _write_shadow_history(
        history_root,
        [_shadow_row("Combo Star", prediction_date=date, market_type="player_points_assists", line=27.5)],
    )

    def fake_provider_lookup(*, runtime_root: Path, prediction_dates) -> subject.ComponentActualLookup:
        assert list(prediction_dates) == [date]
        lookup = subject.ComponentActualLookup(provider_rows_by_date={date: 1})
        for market_type, actual_value in [("player_points", 22.0), ("player_assists", 7.0)]:
            subject._add_component_actual(
                lookup,
                prediction_date=date,
                player_name="Combo Star",
                market_type=market_type,
                actual_value=actual_value,
                source=subject.STAT_SOURCE_PROVIDER,
                team_abbr="BOS",
            )
        lookup.source_rows[subject.STAT_SOURCE_PROVIDER] = 2
        return lookup

    monkeypatch.setattr(subject, "_provider_component_actual_lookup", fake_provider_lookup)

    result = grade_market_shadow_history(
        runtime_root=runtime_root,
        history_root=history_root,
        dates=[date],
        dry_run=True,
        stat_source="auto",
    )

    assert result["would_update_rows"] == 1
    assert result["provider_rows_by_date"] == {date: 1}
    assert result["combo_component_diagnostics"]["by_date"][date]["component_stats_available"] == 1
    assert result["combo_component_diagnostics"]["stat_source_used"] == {"provider_api": 1}


def test_combo_shadow_grading_does_not_modify_elite_or_kelly_outputs(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"
    _write_shadow_history(
        history_root,
        [_shadow_row("Combo Star", prediction_date=date, market_type="player_points_rebounds", line=30.5)],
    )
    _write_result_feedback(
        runtime_root,
        [
            _actual_row("Combo Star", "player_points", 22.0, prediction_date=date),
            _actual_row("Combo Star", "player_rebounds", 10.0, prediction_date=date),
        ],
    )
    operator = runtime_root / "operator"
    operator.mkdir(parents=True, exist_ok=True)
    elite_path = operator / f"elite_board_{date}.csv"
    kelly_path = operator / f"kelly_stakes_{date}.csv"
    pd.DataFrame([{"player_name": "Elite Sentinel", "market_type": "player_points"}]).to_csv(elite_path, index=False)
    pd.DataFrame([{"player_name": "Kelly Sentinel", "market_type": "player_points", "stake_amount": 10.0}]).to_csv(
        kelly_path,
        index=False,
    )
    elite_before = elite_path.read_bytes()
    kelly_before = kelly_path.read_bytes()

    grade_market_shadow_history(runtime_root=runtime_root, history_root=history_root, dates=[date])

    assert elite_path.read_bytes() == elite_before
    assert kelly_path.read_bytes() == kelly_before


def test_candidate_scoring_py_is_untouched_by_combo_shadow_grading() -> None:
    path = Path(__file__).parent.parent / "courtvision" / "scoring" / "candidate_scoring.py"
    content = path.read_text(encoding="utf-8")
    assert "grade_market_shadow_history" not in content
    assert "COMBO_MARKET_COMPONENTS" not in content
