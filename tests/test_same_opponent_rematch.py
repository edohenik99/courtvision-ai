from __future__ import annotations

from pathlib import Path

import pandas as pd

import courtvision_ai
from courtvision.reporting.quality_summary import build_quality_summary
from courtvision.reporting.same_opponent_rematch import (
    DIAGNOSTIC_COLUMNS,
    UNDER_WARNING_REASON,
    annotate_same_opponent_rematches,
)
from scripts.write_daily_summary import write_daily_summary_outputs


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _history_row(
    player_name: str = "Ajay Mitchell",
    *,
    prediction_date: str = "2026-05-05",
    opponent: str = "LAL",
    selection: str = "under",
    line: float = 16.5,
    actual_value: float = 18.0,
    result_status: str = "miss",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "entity_name": player_name,
        "player_id": "",
        "team": "OKC",
        "team_abbr": "OKC",
        "opponent": opponent,
        "market": "player_points",
        "market_type": "player_points",
        "selection": selection,
        "line": line,
        "sportsbook_line": line,
        "actual_value": actual_value,
        "result_status": result_status,
    }


def _current_row(
    player_name: str = "Ajay Mitchell",
    *,
    prediction_date: str = "2026-05-07",
    opponent: str = "LAL",
    selection: str = "under",
    line: float = 16.5,
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "entity_name": player_name,
        "player_id": "",
        "team": "OKC",
        "team_abbr": "OKC",
        "opponent": opponent,
        "game_id": "okc-lal",
        "market_type": "player_points",
        "selection": selection,
        "sportsbook_line": line,
        "line": line,
        "odds": -110,
        "model_projection": 14.0,
        "edge": -2.5,
        "confidence": 0.75,
        "quality_score": 72.0,
        "context_caution_level": "low",
        "context_pick_alignment": "aligned",
    }


def _warning_board_row() -> dict:
    row = _current_row()
    row.update(
        {
            "same_opponent_recent_games": 1,
            "same_opponent_last_actual_points": 18.0,
            "same_opponent_last_line": 16.5,
            "same_opponent_last_selection": "under",
            "same_opponent_last_result_status": "miss",
            "same_opponent_under_warning": True,
            "same_opponent_warning_reason": UNDER_WARNING_REASON,
            "manual_review_required": True,
            "manual_review_reason": "same_opponent_under_warning",
        }
    )
    return row


def test_ajay_style_same_opponent_under_sets_warning_and_manual_review(tmp_path: Path) -> None:
    history_root = tmp_path / "data" / "history"
    _write_csv(history_root / "pick_history.csv", [_history_row(actual_value=18.0, result_status="miss")])
    current = pd.DataFrame([_current_row(line=16.5)])

    annotated = annotate_same_opponent_rematches(
        current,
        prediction_date="2026-05-07",
        history_root=history_root,
    )

    row = annotated.iloc[0]
    assert int(row["same_opponent_recent_games"]) == 1
    assert float(row["same_opponent_last_actual_points"]) == 18.0
    assert float(row["same_opponent_last_line"]) == 16.5
    assert row["same_opponent_last_selection"] == "under"
    assert row["same_opponent_last_result_status"] == "miss"
    assert bool(row["same_opponent_under_warning"]) is True
    assert row["same_opponent_warning_reason"] == UNDER_WARNING_REASON
    assert bool(row["manual_review_required"]) is True
    assert "same_opponent_under_warning" in row["manual_review_reason"]


def test_same_opponent_under_no_warning_when_last_actual_did_not_exceed_current_line(tmp_path: Path) -> None:
    history_root = tmp_path / "data" / "history"
    _write_csv(history_root / "market_shadow_history.csv", [_history_row(actual_value=14.0, result_status="hit")])
    current = pd.DataFrame([_current_row(line=16.5)])

    annotated = annotate_same_opponent_rematches(
        current,
        prediction_date="2026-05-07",
        history_root=history_root,
    )

    row = annotated.iloc[0]
    assert int(row["same_opponent_recent_games"]) == 1
    assert float(row["same_opponent_last_actual_points"]) == 14.0
    assert bool(row["same_opponent_under_warning"]) is False
    assert bool(row["manual_review_required"]) is False
    assert row["same_opponent_warning_reason"] == ""


def test_live_board_writer_adds_same_opponent_columns(tmp_path: Path) -> None:
    prediction_date = "2026-05-07"
    current = pd.DataFrame([_current_row("No History Rematch", prediction_date=prediction_date)])

    paths = courtvision_ai._write_cli_outputs(
        out_dir=tmp_path,
        prediction_date=prediction_date,
        fit_metrics=None,
        prediction_outputs={
            "selected_props": current,
            "elite_props": current,
            "qualified_pool_props": current,
            "full_market_props": current,
            "sgp_props": pd.DataFrame(),
            "summary": {"prediction_date": prediction_date},
            "grading_results": pd.DataFrame(),
        },
        verbose_outputs=False,
    )

    elite = pd.read_csv(paths["elite_board"], keep_default_na=False)
    full_market = pd.read_csv(paths["full_market_board"], keep_default_na=False)
    assert set(DIAGNOSTIC_COLUMNS).issubset(elite.columns)
    assert set(DIAGNOSTIC_COLUMNS).issubset(full_market.columns)
    assert str(elite.iloc[0]["same_opponent_under_warning"]).lower() == "false"
    assert str(full_market.iloc[0]["manual_review_required"]).lower() == "false"


def test_operator_reports_surface_same_opponent_manual_review(tmp_path: Path) -> None:
    prediction_date = "2026-05-07"
    row = _warning_board_row()
    elite_df = pd.DataFrame([row])

    decision_report = courtvision_ai._build_elite_decision_report_text(prediction_date, elite_df)
    top_report = courtvision_ai._build_report_text(
        prediction_date=prediction_date,
        fit_metrics=None,
        summary={
            "same_opponent_under_warning_count": 1,
            "manual_review_required_count": 1,
            "elite_same_opponent_under_warning_count": 1,
            "elite_manual_review_required_count": 1,
        },
        elite_df=elite_df,
        full_market_df=elite_df,
        all_stats_df=pd.DataFrame(),
        team_board_df=pd.DataFrame(),
        strike_df=pd.DataFrame(),
        predictive_lines_df=pd.DataFrame(),
        sgp_df=pd.DataFrame(),
        grading_df=pd.DataFrame(),
        near_miss_df=pd.DataFrame(),
    )

    for text in (decision_report, top_report):
        assert "manual_review_required=True" in text
        assert "same_opponent_under_warning=True" in text
        assert "same_opponent_warning_reason=last_same_opponent_actual_exceeded_current_under_line" in text
    assert "same_opponent_under_warning_count=1" in decision_report
    assert "manual_review_required_count: 1" in top_report


def test_daily_and_quality_summaries_surface_manual_review_counts(tmp_path: Path) -> None:
    prediction_date = "2026-05-07"
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    row = _current_row(prediction_date=prediction_date)
    _write_csv(history_root / "pick_history.csv", [_history_row(actual_value=18.0, result_status="miss")])

    _write_csv(runtime_root / "operator" / f"elite_board_{prediction_date}.csv", [row])
    _write_csv(runtime_root / "operator" / f"full_market_board_{prediction_date}.csv", [row])
    _write_csv(runtime_root / "operator" / f"sgp_board_{prediction_date}.csv", [])
    _write_csv(
        runtime_root / "operator" / f"kelly_stakes_{prediction_date}.csv",
        [
            {
                "prediction_date": prediction_date,
                "player_name": "Ajay Mitchell",
                "market_type": "player_points",
                "selection": "under",
                "line": 16.5,
                "stake_amount": 10.0,
                "expected_value": 1.2,
                "eligible": True,
                "kelly_eligible": True,
            }
        ],
    )
    _write_csv(runtime_root / "research" / f"player_predictions_{prediction_date}.csv", [row])

    daily_path, daily_metadata = write_daily_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    daily_text = daily_path.read_text(encoding="utf-8")
    assert daily_metadata["same_opponent_under_warning_count"] == 1
    assert daily_metadata["manual_review_required_count"] == 1
    assert "Manual Review Warnings" in daily_text
    assert "same_opponent_under_warning_count=1" in daily_text
    assert "manual_review_required: True" in daily_text
    annotated_board = pd.read_csv(runtime_root / "operator" / f"full_market_board_{prediction_date}.csv", keep_default_na=False)
    assert str(annotated_board.iloc[0]["same_opponent_under_warning"]).lower() == "true"
    assert str(annotated_board.iloc[0]["manual_review_required"]).lower() == "true"

    quality_text, quality_payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path / "outputs",
    )
    assert quality_payload["same_opponent_under_warning_count"] == 1
    assert quality_payload["manual_review_required_count"] == 1
    assert quality_payload["manual_review_summary"]["elite_manual_review_required_count"] == 1
    assert "Manual Review Warnings" in quality_text
    assert "full_market same_opponent_under_warning_count: 1" in quality_text
