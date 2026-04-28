from __future__ import annotations

from pathlib import Path

import pandas as pd

from courtvision_ai import _build_elite_decision_report_text, _build_report_text
from courtvision.context.manual_player_context import (
    apply_manual_player_context,
    load_manual_player_context,
    write_manual_context_diagnostics,
)


def _candidate_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "prediction_date": "2024-01-15",
                "player_name": "LeBron James",
                "team_abbr": "LAL",
                "market_type": "player_points",
                "projection": 27.5,
                "confidence": 0.72,
                "quality_score": 82.0,
            }
        ]
    )


def test_manual_context_missing_file_is_clean_noop(tmp_path: Path) -> None:
    context, load_diag = load_manual_player_context("2024-01-15", config_dir=tmp_path)
    annotated, apply_diag = apply_manual_player_context(_candidate_frame(), context)

    assert load_diag["file_found"] is False
    assert load_diag["rows"] == 0
    assert apply_diag["candidate_matches"] == 0
    assert annotated.loc[0, "manual_status"] == ""
    assert pd.isna(annotated.loc[0, "manual_minutes_limit"])
    assert bool(annotated.loc[0, "manual_context_applied"]) is False


def test_manual_context_valid_row_matches_candidate(tmp_path: Path) -> None:
    (tmp_path / "manual_player_context_2024-01-15.csv").write_text(
        "\n".join(
            [
                "prediction_date,player_name,team,status,minutes_limit,projection_adjustment,confidence_adjustment,reason",
                "2024-01-15,LeBron James,LAL,questionable,30,-1.5,-0.05,minutes watch",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    context, load_diag = load_manual_player_context("2024-01-15", config_dir=tmp_path)
    annotated, apply_diag = apply_manual_player_context(_candidate_frame(), context)

    assert load_diag["file_found"] is True
    assert load_diag["rows"] == 1
    assert apply_diag["candidate_matches"] == 1
    assert annotated.loc[0, "projection"] == 27.5
    assert annotated.loc[0, "confidence"] == 0.72
    assert annotated.loc[0, "manual_status"] == "questionable"
    assert annotated.loc[0, "manual_minutes_limit"] == 30.0
    assert annotated.loc[0, "manual_projection_adjustment"] == -1.5
    assert annotated.loc[0, "manual_confidence_adjustment"] == -0.05
    assert annotated.loc[0, "manual_context_reason"] == "minutes watch"
    assert bool(annotated.loc[0, "manual_context_applied"]) is False


def test_manual_context_invalid_numeric_reports_warning_without_crash(tmp_path: Path) -> None:
    (tmp_path / "manual_player_context_2024-01-15.csv").write_text(
        "\n".join(
            [
                "prediction_date,player_name,team,status,minutes_limit,projection_adjustment,confidence_adjustment,reason",
                "2024-01-15,LeBron James,LAL,probable,not-a-number,bad,0.02,operator note",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    context, load_diag = load_manual_player_context("2024-01-15", config_dir=tmp_path)
    annotated, apply_diag = apply_manual_player_context(_candidate_frame(), context)
    output_path, payload = write_manual_context_diagnostics(
        prediction_date="2024-01-15",
        runtime_root=tmp_path / "runtime",
        load_diagnostics=load_diag,
        candidate_diagnostics=apply_diag,
        context_rows=context,
    )

    assert apply_diag["candidate_matches"] == 1
    assert pd.isna(annotated.loc[0, "manual_minutes_limit"])
    assert pd.isna(annotated.loc[0, "manual_projection_adjustment"])
    assert annotated.loc[0, "manual_confidence_adjustment"] == 0.02
    assert any("invalid numeric value for minutes_limit" in warning for warning in load_diag["warnings"])
    assert any("invalid numeric value for projection_adjustment" in warning for warning in load_diag["warnings"])
    assert output_path.name == "manual_context_2024-01-15.json"
    assert payload["passive_mode"] is True


def test_elite_reports_include_manual_context_fields() -> None:
    elite_df = pd.DataFrame(
        [
            {
                "player_name": "Jalen Green",
                "entity_name": "Jalen Green",
                "market_type": "player_points",
                "selection": "over",
                "sportsbook_line": 19.5,
                "model_projection": 22.9,
                "edge": 3.4,
                "confidence": 0.8,
                "manual_status": "active",
                "manual_minutes_limit": "",
                "manual_projection_adjustment": 0.0,
                "manual_confidence_adjustment": 0.0,
                "manual_context_reason": "manual test row only",
                "manual_context_applied": False,
                "pace_context_signal": "supports_over",
                "defense_context_signal": "neutral",
                "rest_context_signal": "supports_under",
                "playoff_context_signal": "supports_under",
                "overall_context_signal": "supports_under",
                "context_pick_alignment": "conflicted",
                "context_preview_applied": False,
            }
        ]
    )

    decision_report = _build_elite_decision_report_text("2026-04-27", elite_df)
    top_report = _build_report_text(
        prediction_date="2026-04-27",
        fit_metrics=None,
        summary={},
        elite_df=elite_df,
        full_market_df=pd.DataFrame(),
        all_stats_df=pd.DataFrame(),
        team_board_df=pd.DataFrame(),
        strike_df=pd.DataFrame(),
        predictive_lines_df=pd.DataFrame(),
        sgp_df=pd.DataFrame(),
        grading_df=pd.DataFrame(),
        near_miss_df=pd.DataFrame(),
    )

    for text in (decision_report, top_report):
        assert "manual_status=active" in text
        assert "manual_projection_adjustment=0.0" in text
        assert "manual_confidence_adjustment=0.0" in text
        assert "manual_context_reason=manual test row only" in text
        assert "manual_context_applied=False" in text
    assert "manual_context_mode=passive_diagnostic_only" in decision_report
    assert "pace_context_signal=supports_over" in decision_report
    assert "defense_context_signal=neutral" in decision_report
    assert "rest_context_signal=supports_under" in decision_report
    assert "playoff_context_signal=supports_under" in decision_report
    assert "overall_context_signal=supports_under" in decision_report
    assert "context_pick_alignment=conflicted" in decision_report
    assert "context_preview_applied=False" in decision_report
    assert "context_preview_mode=passive_diagnostic_only" in decision_report
