from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.write_operator_card import write_operator_card_outputs


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_operator_card_includes_clv_market_movement_shadow_section(tmp_path: Path) -> None:
    prediction_date = "2026-05-24"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    row = {
        "prediction_date": prediction_date,
        "player_name": "Fixture Player",
        "player_id": "player-1",
        "team_abbr": "BOS",
        "opponent": "NYK",
        "game_id": "game-1",
        "market_type": "player_points",
        "selection": "over",
        "line": 20.5,
        "sportsbook_line": 20.5,
        "odds": -110,
        "edge": 2.0,
        "confidence": 0.72,
        "quality_score": 80.0,
    }

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [row])
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [row])
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        {
            "run_health_status": "HEALTHY",
            "run_health_reason": "fixture",
            "slate_provider_counts": {
                "games_count": 1,
                "normalized_odds_rows_count": 1,
                "stale_odds_count": 0,
                "provider_breakdown": {"line_source": {"fixture": 1}},
            },
            "candidate_funnel": {
                "elite_board_count": 1,
                "full_market_board_count": 1,
                "sgp_board_count": 0,
            },
            "kelly_safety_summary": {
                "total_rows": 0,
                "kelly_eligible_count": 0,
                "review_before_bet_count": 0,
                "review_policy_hold_count": 0,
            },
            "manual_review_required_count": 0,
            "same_opponent_under_warning_count": 0,
            "date_isolation_check": {"status": "ok"},
        },
    )
    _write_json(diagnostics / f"board_diagnostics_{prediction_date}.json", {"board_counts": {}})
    _write_json(
        diagnostics / f"market_shadow_grading_{prediction_date}.json",
        {
            "totals": {"total_picks": 1, "graded_picks": 0, "pending_picks": 1},
            "kelly_decision_performance": {"status": "insufficient_sample"},
        },
    )
    _write_json(
        diagnostics / f"clv_market_movement_{prediction_date}.json",
        {
            "summary": {
                "total_rows": 4,
                "close_coverage_count": 3,
                "positive_clv_count": 2,
                "positive_clv_rate": 2 / 3,
                "movement_toward_pick_count": 2,
                "movement_away_from_pick_count": 1,
                "missing_close_line_count": 1,
            }
        },
    )
    _write_json(
        diagnostics / f"calibration_bucket_report_{prediction_date}.json",
        {
            "summary": {
                "total_graded_rows_used": 42,
                "worst_overconfident_bucket_label": (
                    "confidence_bucket=0.80+ market=player_points side=over "
                    "graded_n=12 gap=-0.2500"
                ),
                "best_calibrated_bucket_label": (
                    "market_type=player_points market=player_points side=over "
                    "graded_n=30 gap=0.0100"
                ),
                "tiny_small_sample_warning_count": 7,
                "readiness": "review_only",
            }
        },
    )
    _write_json(
        diagnostics / f"player_role_stability_{prediction_date}.json",
        {
            "summary": {
                "total_rows_evaluated": 50,
                "stable_count": 40,
                "mostly_stable_count": 5,
                "mixed_count": 3,
                "volatile_count": 1,
                "highly_volatile_count": 1,
                "unknown_count": 0,
                "top_volatile_examples": [
                    {
                        "player_name": "Volatile Player",
                        "team": "BOS",
                        "role_stability_score": 15.0,
                        "role_stability_bucket": "highly_volatile",
                        "role_stability_reasons": ["high_recent_avg_delta"],
                    }
                ],
            }
        },
    )
    _write_csv(
        history_root / "pick_history.csv",
        [{"prediction_date": "2026-05-01", "result_status": "hit"}],
    )

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert "CLV / Market Movement - Shadow Only" in text
    assert "- close coverage count: 3 / 4" in text
    assert "- positive CLV count/rate: 2 / 66.7%" in text
    assert "- movement toward pick count: 2" in text
    assert "- movement away from pick count: 1" in text
    assert "- missing close-line count: 1" in text
    assert "- CLV is diagnostic only and is not an Elite/Kelly input." in text
    assert payload["clv_market_movement"]["positive_clv_count"] == 2
    assert "Calibration Health - Shadow Only" in text
    assert "- total graded rows used: 42" in text
    assert "- worst overconfident bucket: confidence_bucket=0.80+ market=player_points side=over graded_n=12 gap=-0.2500" in text
    assert "- best calibrated bucket: market_type=player_points market=player_points side=over graded_n=30 gap=0.0100" in text
    assert "- tiny/small sample warning count: 7" in text
    assert "- Calibration Bucket Report is diagnostic only and is not an Elite/Kelly input." in text
    assert payload["calibration_bucket_report"]["total_graded_rows_used"] == 42
    
    assert "Player Role Stability - Shadow Only" in text
    assert "- total rows evaluated: 50" in text
    assert "- stable count: 40" in text
    assert "- volatile count: 1" in text
    assert "- Volatile Player (BOS): score=15.0 bucket=highly_volatile reasons=[high_recent_avg_delta]" in text
    assert "- Player Role Stability is diagnostic only and is not an Elite/Kelly input." in text
    assert payload["player_role_stability_report"]["total_rows_evaluated"] == 50
    assert payload["final_decision"] == "BETTABLE"
