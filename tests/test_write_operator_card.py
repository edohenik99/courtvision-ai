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
    _write_json(
        diagnostics / f"meta_label_promotion_shadow_{prediction_date}.json",
        {
            "summary": {
                "total_rows_evaluated": 12,
                "shadow_strong_review_candidate_count": 2,
                "shadow_watch_candidate_count": 3,
                "shadow_neutral_count": 4,
                "shadow_weak_count": 2,
                "shadow_avoid_review_count": 1,
                "top_strong_candidates": [
                    {
                        "player_name": "Elite Candidate",
                        "team": "BOS",
                        "market_type": "player_points",
                        "selection": "over",
                        "meta_label_rules_score": 95.0,
                        "meta_label_bucket": "shadow_strong_review_candidate",
                        "reason_codes": ["high_quality_score", "strong_confidence"],
                    }
                ],
            }
        },
    )
    _write_csv(
        history_root / "pick_history.csv",
        [{"prediction_date": "2026-05-01", "result_status": "hit"}],
    )
    _write_json(
        diagnostics / f"meta_label_rules_performance_{prediction_date}.json",
        {
            "data_readiness": {
                "verdict": "WAIT_MORE_DATA",
                "graded_hit_miss_rows": 10,
                "completed_slate_count": 2,
                "minimum_sample_threshold_status": "insufficient",
                "missing_role_stability_rate": 0.5,
                "missing_fragility_rate": 0.2,
            }
        },
    )
    _write_json(
        diagnostics / f"feature_completeness_tracker_{prediction_date}.json",
        {
            "historical_coverage": {
                "completed_slate_count": 5,
                "graded_hit_miss_rows": 150,
                "feature_complete_graded_rows": 120,
            },
            "readiness": {
                "verdict": "FEATURE_COLLECTION_HEALTHY_BUT_SAMPLE_SMALL",
                "estimated_additional_slates_needed": 25,
            }
        },
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

    assert "Meta-Label Promotion - Shadow Only" in text
    assert "- total rows evaluated: 12" in text
    assert "- shadow strong review candidate count: 2" in text
    assert "- Elite Candidate (BOS): score=95.0 bucket=shadow_strong_review_candidate reasons=[high_quality_score; strong_confidence]" in text
    assert "- Meta-Label Promotion is shadow-only and is not an Elite/Kelly input." in text
    assert payload["meta_label_promotion_report"]["total_rows_evaluated"] == 12

    assert "Feature Completeness Tracker - Shadow Only" in text
    assert "- completed slate count: 5" in text
    assert "- graded hit/miss rows: 150" in text
    assert "- feature-complete graded rows: 120" in text
    assert "- estimated additional slates needed: 25" in text
    assert "- Phase 4C readiness verdict: FEATURE_COLLECTION_HEALTHY_BUT_SAMPLE_SMALL" in text
    assert "- Feature Completeness Tracker is shadow-only and is not an Elite/Kelly input." in text
    assert payload["feature_completeness_tracker"]["completed_slate_count"] == 5

    assert payload["final_decision"] == "BETTABLE"


def test_operator_card_grading_snapshot_zero_on_complete_closed_slate(tmp_path: Path) -> None:
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
            "totals": {"total_picks": 53, "graded_picks": 0, "pending_picks": 53},
            "kelly_decision_performance": {"status": "insufficient_sample"},
        },
    )
    _write_json(
        diagnostics / f"completion_state_audit_{prediction_date}.json",
        {
            "report_agreement_status": "COMPLETE",
            "agreement_issues": [],
            "real_pick_pending_count": 0,
            "shadow_pending_count": 0,
            "paper_pending_count": 0,
            "daily_summary_pending_grading": 0,
        },
    )
    _write_csv(
        history_root / "pick_history.csv",
        [],
        columns=["prediction_date", "result_status"],
    )
    _write_json(
        diagnostics / f"meta_label_rules_performance_{prediction_date}.json",
        {
            "data_readiness": {
                "verdict": "WAIT_MORE_DATA",
                "graded_hit_miss_rows": 10,
                "completed_slate_count": 2,
                "minimum_sample_threshold_status": "insufficient",
                "missing_role_stability_rate": 0.5,
                "missing_fragility_rate": 0.2,
            }
        },
    )
    _write_json(
        diagnostics / f"feature_completeness_tracker_{prediction_date}.json",
        {
            "historical_coverage": {
                "completed_slate_count": 5,
                "graded_hit_miss_rows": 150,
                "feature_complete_graded_rows": 120,
            },
            "readiness": {
                "verdict": "FEATURE_COLLECTION_HEALTHY_BUT_SAMPLE_SMALL",
                "estimated_additional_slates_needed": 25,
            }
        },
    )

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert "Grading Snapshot" in text
    assert "- market shadow rows: 53" in text
    assert "- graded rows: 0" in text
    assert "- pending grading: 0" in text
