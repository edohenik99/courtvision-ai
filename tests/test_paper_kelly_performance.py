from __future__ import annotations

from pathlib import Path

import pandas as pd

from courtvision.reporting.paper_kelly_performance import (
    HISTORY_COLUMNS,
    REPORT_COLUMNS,
    REPORT_TITLE,
    build_paper_kelly_performance_report,
    persist_paper_kelly_history,
    read_paper_kelly_history,
    write_paper_kelly_performance_report,
)


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _paper_row(
    player_name: str = "Paper Star",
    *,
    prediction_date: str = "2026-05-06",
    paper_bucket: str = "combo_under_watchlist",
    market_type: str = "player_points_rebounds",
    selection: str = "under",
    line: float = 30.5,
    edge: float = -2.5,
    directional_edge: float = 2.5,
    confidence: float = 0.75,
    stake: float = 0.002,
    pre_cap_stake: float | None = None,
    cap_reason: str = "none",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "paper_bucket": paper_bucket,
        "player_name": player_name,
        "team_abbr": "BOS",
        "opponent": "NYK",
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "odds": -110,
        "edge": edge,
        "directional_edge": directional_edge,
        "confidence": confidence,
        "quality_score": 82.0,
        "context_pick_alignment": "aligned",
        "context_caution_level": "low",
        "simulated_fraction": stake,
        "simulated_stake": stake,
        "pre_cap_simulated_stake": pre_cap_stake if pre_cap_stake is not None else stake,
        "cap_adjustment_reason": cap_reason,
        "player_exposure_after_cap": stake,
        "team_exposure_after_cap": stake,
        "game_exposure_after_cap": stake,
        "side_exposure_after_cap": stake,
        "bucket_exposure_after_cap": stake,
        "simulated_ev": 0.00012,
        "real_kelly_eligible": False,
        "simulation_only": True,
        "reason_not_real_kelly": "combo_market_not_real_kelly_eligible",
    }


def _shadow_result_row(
    player_name: str = "Paper Star",
    *,
    prediction_date: str = "2026-05-06",
    market_type: str = "player_points_rebounds",
    selection: str = "under",
    line: float = 30.5,
    result_status: str = "hit",
    actual_value: float = 28.0,
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "team_abbr": "BOS",
        "opponent": "NYK",
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "odds": -110,
        "result_status": result_status,
        "actual_value": actual_value,
    }


def _write_paper_source(runtime_root: Path, prediction_date: str, rows: list[dict]) -> Path:
    path = runtime_root / "operator" / f"paper_kelly_simulation_{prediction_date}.csv"
    _write_csv(path, rows)
    return path


def test_paper_kelly_history_created_and_same_date_persist_is_idempotent(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"
    other_date = "2026-05-05"

    _write_paper_source(runtime_root, date, [_paper_row("Old Current", prediction_date=date)])
    first = persist_paper_kelly_history(prediction_date=date, runtime_root=runtime_root, history_root=history_root)
    _write_paper_source(runtime_root, other_date, [_paper_row("Other Date", prediction_date=other_date)])
    persist_paper_kelly_history(prediction_date=other_date, runtime_root=runtime_root, history_root=history_root)

    _write_paper_source(
        runtime_root,
        date,
        [
            _paper_row("New Current A", prediction_date=date, line=28.5),
            _paper_row("New Current B", prediction_date=date, line=29.5),
        ],
    )
    second = persist_paper_kelly_history(prediction_date=date, runtime_root=runtime_root, history_root=history_root)
    third = persist_paper_kelly_history(prediction_date=date, runtime_root=runtime_root, history_root=history_root)

    history_path = history_root / "paper_kelly_history.csv"
    history = pd.read_csv(history_path, keep_default_na=False)
    same_date = history[history["prediction_date"].astype(str) == date]
    assert first["current_date_rows"] == 1
    assert second["replaced_rows"] == 1
    assert third["current_date_rows"] == 2
    assert history_path.exists()
    assert list(history.columns) == list(HISTORY_COLUMNS)
    assert len(same_date) == 2
    assert len(history[history["prediction_date"].astype(str) == other_date]) == 1
    assert "Old Current" not in set(history["player_name"])
    assert "Other Date" in set(history["player_name"])
    dedupe_key = ["prediction_date", "paper_bucket", "player_name", "market_type", "selection", "line"]
    assert not same_date.duplicated(subset=dedupe_key).any()


def test_paper_rows_grade_when_market_shadow_actuals_exist(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"
    _write_paper_source(runtime_root, date, [_paper_row(stake=0.002)])
    _write_csv(history_root / "market_shadow_history.csv", [_shadow_result_row()])

    persist_paper_kelly_history(prediction_date=date, runtime_root=runtime_root, history_root=history_root)

    history = pd.read_csv(history_root / "paper_kelly_history.csv", keep_default_na=False)
    row = history.iloc[0]
    assert row["result_status"] == "hit"
    assert float(row["actual_value"]) == 28.0
    assert float(row["paper_profit"]) == 0.001818
    assert float(row["paper_roi"]) == 0.909091
    assert row["grading_skip_reason"] == ""


def test_unresolved_paper_rows_remain_pending_with_reason(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"
    _write_paper_source(runtime_root, date, [_paper_row()])

    persist_paper_kelly_history(prediction_date=date, runtime_root=runtime_root, history_root=history_root)

    history = pd.read_csv(history_root / "paper_kelly_history.csv", keep_default_na=False)
    row = history.iloc[0]
    assert row["result_status"] == "pending"
    assert row["paper_profit"] == ""
    assert row["paper_roi"] == ""
    assert row["grading_skip_reason"] == "no_matching_shadow_or_result_data"


def test_pending_shadow_false_flags_do_not_grade_as_miss(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"
    _write_paper_source(runtime_root, date, [_paper_row()])
    pending_shadow = _shadow_result_row(result_status="pending")
    pending_shadow["actual_value"] = ""
    pending_shadow["hit"] = False
    pending_shadow["miss"] = False
    pending_shadow["push"] = False
    _write_csv(history_root / "market_shadow_history.csv", [pending_shadow])

    persist_paper_kelly_history(prediction_date=date, runtime_root=runtime_root, history_root=history_root)

    history = pd.read_csv(history_root / "paper_kelly_history.csv", keep_default_na=False)
    row = history.iloc[0]
    assert row["result_status"] == "pending"
    assert row["paper_profit"] == ""
    assert row["grading_skip_reason"] == "market_shadow_history_result_pending"


def test_grouped_paper_performance_report_is_generated(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"
    _write_paper_source(
        runtime_root,
        date,
        [
            _paper_row("Paper Hit", stake=0.002),
            _paper_row("Paper Pending", line=27.5, stake=0.0015),
        ],
    )
    _write_csv(
        history_root / "market_shadow_history.csv",
        [_shadow_result_row("Paper Hit", result_status="hit", actual_value=28.0)],
    )

    text_path, csv_path, report, persist_result = write_paper_kelly_performance_report(
        prediction_date=date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert persist_result["current_date_rows"] == 2
    assert text_path == runtime_root / "operator" / f"paper_kelly_performance_report_{date}.txt"
    assert csv_path == runtime_root / "operator" / f"paper_kelly_performance_report_{date}.csv"
    assert text_path.exists()
    assert csv_path.exists()
    assert REPORT_TITLE in text_path.read_text(encoding="utf-8")
    assert list(pd.read_csv(csv_path, keep_default_na=False).columns) == list(REPORT_COLUMNS)
    assert int(report["total"].sum()) == 2
    assert int(report["graded_total"].sum()) == 1
    assert int(report["pending"].sum()) == 1


def test_real_kelly_file_unchanged_by_paper_performance(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"
    operator = runtime_root / "operator"
    kelly_path = operator / f"kelly_stakes_{date}.csv"
    _write_csv(kelly_path, [{"player_name": "Kelly Sentinel", "stake_amount": 10.0, "eligible": True}])
    _write_paper_source(runtime_root, date, [_paper_row()])
    before = kelly_path.read_bytes()

    write_paper_kelly_performance_report(prediction_date=date, runtime_root=runtime_root, history_root=history_root)

    assert kelly_path.read_bytes() == before


def test_candidate_scoring_py_is_untouched_by_paper_performance() -> None:
    path = Path(__file__).parent.parent / "courtvision" / "scoring" / "candidate_scoring.py"
    content = path.read_text(encoding="utf-8")
    assert "paper_kelly_performance" not in content
    assert "paper_kelly_history" not in content


def test_paper_history_migrates_old_rows_without_cap_columns(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    old_date = "2026-05-01"
    new_date = "2026-05-06"

    old_columns = [
        "prediction_date", "paper_bucket", "player_name", "team_abbr", "opponent",
        "market_type", "selection", "line", "odds", "edge", "directional_edge",
        "confidence", "quality_score", "context_pick_alignment", "context_caution_level",
        "simulated_fraction", "simulated_stake", "simulated_ev", "real_kelly_eligible",
        "simulation_only", "reason_not_real_kelly", "result_status", "actual_value",
        "paper_profit", "paper_roi", "grading_skip_reason",
    ]
    old_row = {col: "" for col in old_columns}
    old_row.update({
        "prediction_date": old_date, "paper_bucket": "combo_under_watchlist",
        "player_name": "Old Player", "team_abbr": "BOS", "opponent": "NYK",
        "market_type": "player_points_rebounds", "selection": "under", "line": "28.5",
        "simulated_stake": "0.001", "result_status": "hit", "paper_profit": "0.0009",
    })
    old_history_path = history_root / "paper_kelly_history.csv"
    old_history_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([old_row], columns=old_columns).to_csv(old_history_path, index=False)

    _write_paper_source(runtime_root, new_date, [_paper_row("New Player", prediction_date=new_date)])
    persist_paper_kelly_history(prediction_date=new_date, runtime_root=runtime_root, history_root=history_root)

    history = pd.read_csv(old_history_path, keep_default_na=False)
    assert list(history.columns) == list(HISTORY_COLUMNS)
    old_rows = history[history["prediction_date"].astype(str) == old_date]
    assert len(old_rows) == 1
    assert old_rows.iloc[0]["pre_cap_simulated_stake"] == ""
    assert old_rows.iloc[0]["cap_adjustment_reason"] == ""
    assert old_rows.iloc[0]["player_exposure_after_cap"] == ""
    new_rows = history[history["prediction_date"].astype(str) == new_date]
    assert len(new_rows) == 1
    assert new_rows.iloc[0]["cap_adjustment_reason"] == "none"


def test_new_cap_columns_persist_in_history(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"

    capped_row = _paper_row(
        "Capped Player",
        prediction_date=date,
        stake=0.0015,
        pre_cap_stake=0.0025,
        cap_reason="player_exposure_cap player_selection_exposure_cap",
    )
    uncapped_row = _paper_row(
        "Uncapped Player",
        prediction_date=date,
        line=27.5,
        stake=0.002,
        pre_cap_stake=0.002,
        cap_reason="none",
    )
    _write_paper_source(runtime_root, date, [capped_row, uncapped_row])
    persist_paper_kelly_history(prediction_date=date, runtime_root=runtime_root, history_root=history_root)

    history = pd.read_csv(history_root / "paper_kelly_history.csv", keep_default_na=False)
    assert list(history.columns) == list(HISTORY_COLUMNS)
    capped = history[history["player_name"] == "Capped Player"].iloc[0]
    uncapped = history[history["player_name"] == "Uncapped Player"].iloc[0]
    assert float(capped["pre_cap_simulated_stake"]) == 0.0025
    assert float(capped["simulated_stake"]) == 0.0015
    assert capped["cap_adjustment_reason"] == "player_exposure_cap player_selection_exposure_cap"
    assert float(uncapped["pre_cap_simulated_stake"]) == 0.002
    assert uncapped["cap_adjustment_reason"] == "none"
    assert float(capped["player_exposure_after_cap"]) == 0.0015
    assert float(capped["team_exposure_after_cap"]) == 0.0015


def test_report_groups_by_cap_adjustment_reason(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-06"

    _write_paper_source(
        runtime_root,
        date,
        [
            _paper_row("Player A", prediction_date=date, line=28.5, cap_reason="player_exposure_cap"),
            _paper_row("Player B", prediction_date=date, line=29.5, cap_reason="none"),
            _paper_row("Player C", prediction_date=date, line=30.5, cap_reason="game_exposure_cap"),
        ],
    )
    persist_paper_kelly_history(prediction_date=date, runtime_root=runtime_root, history_root=history_root)
    history = read_paper_kelly_history(history_root / "paper_kelly_history.csv")
    report = build_paper_kelly_performance_report(history, through_date=date)

    assert list(report.columns) == list(REPORT_COLUMNS)
    assert "cap_adjustment_reason" in report.columns
    assert "pre_cap_exposure" in report.columns
    assert "post_cap_exposure" in report.columns
    assert "exposure_reduced" in report.columns
    cap_reasons_in_report = set(report["cap_adjustment_reason"].astype(str).str.lower())
    assert "player_exposure_cap" in cap_reasons_in_report
    # "none" is treated as null by _safe_text and normalizes to "unknown"
    assert "unknown" in cap_reasons_in_report
    assert "game_exposure_cap" in cap_reasons_in_report
    assert int(report["total"].sum()) == 3
    for _, row in report.iterrows():
        assert float(row["pre_cap_exposure"]) >= float(row["post_cap_exposure"]) - 1e-9
