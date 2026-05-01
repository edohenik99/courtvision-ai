from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.quality_summary import (
    build_quality_summary,
    write_quality_summary_outputs,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _seed_quality_artifacts(runtime_root: Path, prediction_date: str) -> None:
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    research = runtime_root / "research"

    board_rows = [
        {
            "prediction_date": prediction_date,
            "player_name": "Normal Eligible Under",
            "team": "BOS",
            "game_id": "game-1",
            "market_type": "player_points",
            "selection": "under",
            "line": 21.5,
            "odds": -110,
            "confidence": 0.80,
            "side_edge_pct": 0.12,
            "context_caution_level": "low",
            "context_pick_alignment": "aligned",
            "is_live_market": True,
            "line_source": "fixture_live_market",
        },
        {
            "prediction_date": prediction_date,
            "player_name": "High Caution Over",
            "team": "LAL",
            "game_id": "game-2",
            "market_type": "player_points",
            "selection": "over",
            "line": 17.5,
            "odds": -110,
            "confidence": 0.85,
            "side_edge_pct": 0.20,
            "context_caution_level": "high",
            "context_pick_alignment": "conflicted",
            "is_live_market": True,
            "line_source": "fixture_live_market",
        },
        {
            "prediction_date": prediction_date,
            "player_name": "Medium Neutral Over",
            "team": "DEN",
            "game_id": "game-3",
            "market_type": "player_points",
            "selection": "over",
            "line": 8.5,
            "odds": -110,
            "confidence": 1.00,
            "side_edge_pct": 0.50,
            "context_caution_level": "medium",
            "context_pick_alignment": "neutral",
            "is_live_market": True,
            "line_source": "fixture_live_market",
        },
    ]
    _write_csv(operator / f"elite_board_{prediction_date}.csv", board_rows)
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", board_rows)
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [])
    _write_csv(
        operator / f"kelly_stakes_{prediction_date}.csv",
        [
            {
                "prediction_date": prediction_date,
                "player_name": "Normal Eligible Under",
                "market_type": "player_points",
                "selection": "under",
                "line": 21.5,
                "american_odds": -110,
                "side_edge_pct": 0.12,
                "confidence": 0.80,
                "stake_amount": 12.0,
                "stake_fraction": 0.012,
                "expected_value": 1.44,
                "kelly_eligible": True,
                "eligible": True,
                "skip_reason": "",
                "context_caution_level": "low",
                "context_pick_alignment": "aligned",
                "stake_dampener_reason": "",
                "stake_dampener_factor": 1.0,
            },
            {
                "prediction_date": prediction_date,
                "player_name": "High Caution Over",
                "market_type": "player_points",
                "selection": "over",
                "line": 17.5,
                "american_odds": -110,
                "side_edge_pct": 0.20,
                "confidence": 0.85,
                "stake_amount": 0.0,
                "stake_fraction": 0.0,
                "expected_value": 0.0,
                "kelly_eligible": False,
                "eligible": False,
                "skip_reason": "context_high_caution_over",
                "context_caution_level": "high",
                "context_pick_alignment": "conflicted",
                "stake_dampener_reason": "",
                "stake_dampener_factor": 1.0,
            },
            {
                "prediction_date": prediction_date,
                "player_name": "Medium Neutral Over",
                "market_type": "player_points",
                "selection": "over",
                "line": 8.5,
                "american_odds": -110,
                "side_edge_pct": 0.50,
                "confidence": 1.00,
                "stake_amount": 10.0,
                "stake_fraction": 0.01,
                "expected_value": 5.0,
                "kelly_eligible": True,
                "eligible": True,
                "skip_reason": "",
                "context_caution_level": "medium",
                "context_pick_alignment": "neutral",
                "stake_dampener_reason": "medium_neutral_over_dampener",
                "stake_dampener_factor": 0.5,
            },
        ],
    )
    _write_csv(research / f"player_predictions_{prediction_date}.csv", board_rows)
    _write_json(
        research / f"model_metrics_{prediction_date}.json",
        {
            "prediction_summary": {
                "prediction_date": prediction_date,
                "games_count": 2,
                "odds_count": 6,
                "candidate_count": 5,
                "full_market_count": 3,
                "elite_count": 3,
                "pipeline_mode": "fixture_operator_smoke",
                "market_quality_status": "live",
            }
        },
    )
    _write_json(
        diagnostics / f"board_diagnostics_{prediction_date}.json",
        {"board_counts": {"elite": 3, "full_market": 3, "qualified_pool": 5}},
    )
    _write_json(
        operator / f"elite_pipeline_audit_summary_{prediction_date}.json",
        {
            "totals": {"total_candidates": 5, "passed_to_elite": 3, "total_rejections": 2},
            "rows": [
                {"rejection_reason": "market_filtered_by_elite_policy", "count": 1},
                {"rejection_reason": "market_gate_minutes_lt_24", "count": 1},
                {"rejection_reason": "passed_to_elite", "count": 3},
            ],
        },
    )
    _write_json(
        diagnostics / f"market_availability_audit_{prediction_date}.json",
        {
            "counts": [
                {
                    "market": "player_points",
                    "count_before_normalization": 6,
                    "count_after_normalization": 6,
                }
            ]
        },
    )


def test_quality_summary_generation_from_fixture_artifacts(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _seed_quality_artifacts(runtime_root, prediction_date)
    _write_json(
        runtime_root / "operator" / f"quality_summary_{prediction_date}.json",
        {"candidate_funnel": {"elite_board_count": 2, "kelly_rows_count": 2}},
    )

    text_path, json_path, payload = write_quality_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-01T00:00:00+00:00",
    )

    assert text_path.exists()
    assert json_path.exists()
    assert "Quality Summary - 2026-04-30" in text_path.read_text(encoding="utf-8")
    assert payload["run_identity"]["run_data_mode"] == "fixture"
    assert payload["slate_provider_counts"]["games_count"] == 2
    assert payload["slate_provider_counts"]["raw_odds_rows_count"] == 6
    assert payload["slate_provider_counts"]["normalized_odds_rows_count"] == 6
    assert payload["candidate_funnel"]["raw_candidates_count"] == 5
    assert payload["candidate_funnel"]["elite_board_count"] == 3
    assert payload["candidate_funnel"]["kelly_rows_count"] == 3
    assert payload["top_rejection_reasons"][0]["reason"] == "market_filtered_by_elite_policy"
    assert payload["kelly_safety_summary"]["context_high_caution_over_skip_count"] == 1
    assert payload["kelly_safety_summary"]["medium_neutral_over_dampened_count"] == 1
    assert payload["kelly_safety_summary"]["total_stake_reduction_from_dampeners"] == 10.0
    assert payload["risk_exposure_summary"]["max_team_exposure"] == 12.0
    assert payload["board_movement_summary"]["comparison_available"] is True
    assert payload["board_movement_summary"]["elite_row_count_diff"] == 1
    assert payload["date_isolation_check"]["status"] == "ok"


def test_quality_summary_missing_optional_artifacts_warns_without_crashing(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _write_csv(
        runtime_root / "operator" / f"elite_board_{prediction_date}.csv",
        [{"prediction_date": prediction_date, "player_name": "Only Row", "market_type": "player_points"}],
    )

    text, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-01T00:00:00+00:00",
    )

    assert "Missing optional artifact" in text
    assert payload["candidate_funnel"]["elite_board_count"] == 1
    assert payload["kelly_safety_summary"]["total_rows"] == 0
    assert payload["warnings"]


def test_quality_summary_flags_mismatched_artifact_dates(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _seed_quality_artifacts(runtime_root, prediction_date)
    bad_path = runtime_root / "operator" / "elite_board_2026-04-29.csv"

    _, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        extra_prediction_artifact_paths=[bad_path],
        generated_at="2026-05-01T00:00:00+00:00",
    )

    isolation = payload["date_isolation_check"]
    assert isolation["status"] == "warning"
    assert isolation["mismatched_artifacts"] == [
        {"artifact_date": "2026-04-29", "path": str(bad_path)}
    ]
    assert any("Prediction artifact date mismatch" in warning for warning in payload["warnings"])
