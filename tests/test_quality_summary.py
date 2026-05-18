from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.quality_summary import (
    QUALITY_HISTORY_COLUMNS,
    QUALITY_HISTORY_CSV_NAME,
    QUALITY_HISTORY_JSONL_NAME,
    build_quality_summary,
    update_quality_history_from_summary,
    write_quality_summary_outputs,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _read_history_csv(runtime_root: Path) -> pd.DataFrame:
    return pd.read_csv(
        runtime_root / "operator" / QUALITY_HISTORY_CSV_NAME,
        keep_default_na=False,
    )


def _history_summary_payload(
    prediction_date: str,
    *,
    generated_at: str = "2026-05-01T00:00:00+00:00",
    elite_count: int = 3,
    kelly_rows: int = 3,
    total_stake: float = 22.0,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "run_identity": {
            "prediction_date": prediction_date,
            "generated_at": generated_at,
            "output_directory": "outputs",
            "commit_hash": "abc1234",
            "run_data_mode": "fixture",
        },
        "slate_provider_counts": {
            "games_count": 2,
            "players_or_baselines_count": 8,
            "players_or_baselines_count_legacy_definition": "Legacy alias for player_predictions_rows.",
            "raw_odds_rows_count": 12,
            "normalized_odds_rows_count": 10,
            "live_odds_count": 9,
            "synthetic_or_fallback_odds_count": 1,
        },
        "player_baseline_coverage": {
            "player_predictions_rows": 8,
            "baseline_rows": 64,
            "unique_baseline_players": 64,
            "active_team_baseline_players": 32,
            "odds_unique_players": 5,
            "odds_baseline_matched_players": 5,
            "full_market_unique_players": 5,
            "elite_unique_players": elite_count,
            "kelly_unique_players": kelly_rows,
            "players_or_baselines_count": 8,
            "players_or_baselines_count_legacy_definition": (
                "Legacy alias for player_predictions_rows; not the model baseline universe."
            ),
        },
        "candidate_funnel": {
            "raw_candidates_count": 7,
            "rejected_candidates_count": 4,
            "full_market_board_count": 5,
            "elite_board_count": elite_count,
            "sgp_board_count": 1,
            "kelly_rows_count": kelly_rows,
        },
        "kelly_safety_summary": {
            "total_rows": kelly_rows,
            "kelly_eligible_count": 2,
            "skipped_count": 1,
            "total_stake": total_stake,
            "total_expected_value": 6.44,
            "context_high_caution_over_skip_count": 1,
            "medium_neutral_over_dampened_count": 1,
        },
        "risk_exposure_summary": {
            "max_player_exposure": 12.0,
            "max_team_exposure": 12.0,
            "max_game_exposure": 10.0,
        },
        "market_coverage": {
            "rows": [
                {
                    "market": "player_points",
                    "normalized_odds_rows": 4,
                    "raw_candidates_count": 3,
                    "full_market_board_count": 3,
                    "elite_board_count": elite_count,
                    "rejected_count": 0,
                    "kelly_rows_count": kelly_rows,
                    "kelly_eligible_count": 2,
                    "skipped_count": 1,
                    "high_caution_over_skip_count": 1,
                    "medium_neutral_over_dampened_count": 1,
                },
                {
                    "market": "player_rebounds",
                    "normalized_odds_rows": 3,
                    "raw_candidates_count": 1,
                    "full_market_board_count": 1,
                    "elite_board_count": 0,
                    "rejected_count": 1,
                    "kelly_rows_count": 0,
                    "kelly_eligible_count": 0,
                    "skipped_count": 0,
                    "high_caution_over_skip_count": 0,
                    "medium_neutral_over_dampened_count": 0,
                },
            ],
            "coverage_warnings": ["Low live candidate coverage: fixture."],
        },
        "run_health": {
            "status": "DEGRADED_LOW_COVERAGE",
            "reason": "Provider/candidate coverage warnings are present.",
            "flags": ["provider_or_candidate_coverage_low"],
            "recommendation": "Provider/candidate coverage is thin; treat picks cautiously.",
        },
        "run_health_status": "DEGRADED_LOW_COVERAGE",
        "run_health_reason": "Provider/candidate coverage warnings are present.",
        "run_health_flags": ["provider_or_candidate_coverage_low"],
        "run_health_recommendation": "Provider/candidate coverage is thin; treat picks cautiously.",
        "date_isolation_check": {"status": "ok"},
        "warnings": warnings or [],
    }


def _write_quality_summary_json(runtime_root: Path, prediction_date: str, payload: dict) -> None:
    _write_json(runtime_root / "operator" / f"quality_summary_{prediction_date}.json", payload)


def _seed_player_baselines(
    runtime_root: Path,
    *,
    teams: tuple[str, ...] = ("BOS", "LAL", "DEN", "MIN"),
    players_per_team: int = 16,
) -> None:
    rows = []
    player_id = 10_000
    known_players = [
        ("Normal Eligible Under", "BOS"),
        ("High Caution Over", "LAL"),
        ("Medium Neutral Over", "DEN"),
        ("Rebounds Over", "DEN"),
        ("Rebounds Under", "MIN"),
        ("Assists Under", "DEN"),
    ]
    for name, team in known_players:
        rows.append(
            {
                "player_id": player_id,
                "player_name": name,
                "team_abbr": team,
                "games": 20,
                "min_avg": 28.0,
                "pts_avg": 12.0,
                "reb_avg": 4.0,
                "ast_avg": 3.0,
            }
        )
        player_id += 1
    for team in teams:
        for index in range(players_per_team):
            rows.append(
                {
                    "player_id": player_id,
                    "player_name": f"{team} Baseline {index}",
                    "team_abbr": team,
                    "games": 20,
                    "min_avg": 28.0,
                    "pts_avg": 12.0,
                    "reb_avg": 4.0,
                    "ast_avg": 3.0,
                }
            )
            player_id += 1
    _write_csv(runtime_root.parent / "model" / "player_baselines.csv", rows)


def _seed_quality_artifacts(runtime_root: Path, prediction_date: str) -> None:
    _seed_player_baselines(runtime_root)
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


def _seed_market_coverage_artifacts(runtime_root: Path, prediction_date: str) -> None:
    _seed_player_baselines(runtime_root)
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    research = runtime_root / "research"

    full_rows = [
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
        {
            "prediction_date": prediction_date,
            "player_name": "Rebounds Over",
            "team": "DEN",
            "game_id": "game-3",
            "market_type": "player_rebounds",
            "selection": "over",
            "line": 10.5,
            "odds": 120,
            "confidence": 0.75,
            "side_edge_pct": 0.20,
            "is_live_market": True,
            "line_source": "fixture_live_market",
        },
        {
            "prediction_date": prediction_date,
            "player_name": "Rebounds Under",
            "team": "MIN",
            "game_id": "game-3",
            "market_type": "player_rebounds",
            "selection": "under",
            "line": 12.5,
            "odds": -120,
            "confidence": 0.75,
            "side_edge_pct": 0.10,
            "is_live_market": True,
            "line_source": "fixture_live_market",
        },
        {
            "prediction_date": prediction_date,
            "player_name": "Assists Under",
            "team": "DEN",
            "game_id": "game-3",
            "market_type": "player_assists",
            "selection": "under",
            "line": 10.5,
            "odds": -150,
            "confidence": 0.75,
            "side_edge_pct": 0.10,
            "is_live_market": True,
            "line_source": "fixture_live_market",
        },
    ]
    elite_rows = full_rows[:3]
    _write_csv(operator / f"elite_board_{prediction_date}.csv", elite_rows)
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", full_rows)
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [])
    _write_csv(
        operator / f"kelly_stakes_{prediction_date}.csv",
        [
            {
                "prediction_date": prediction_date,
                "player_name": "Normal Eligible Under",
                "market_type": "player_points",
                "selection": "under",
                "stake_amount": 12.0,
                "expected_value": 1.44,
                "kelly_eligible": True,
                "skip_reason": "",
                "stake_dampener_reason": "",
                "stake_dampener_factor": 1.0,
            },
            {
                "prediction_date": prediction_date,
                "player_name": "High Caution Over",
                "market_type": "player_points",
                "selection": "over",
                "stake_amount": 0.0,
                "expected_value": 0.0,
                "kelly_eligible": False,
                "skip_reason": "context_high_caution_over",
                "stake_dampener_reason": "",
                "stake_dampener_factor": 1.0,
            },
            {
                "prediction_date": prediction_date,
                "player_name": "Medium Neutral Over",
                "market_type": "player_points",
                "selection": "over",
                "stake_amount": 10.0,
                "expected_value": 5.0,
                "kelly_eligible": True,
                "skip_reason": "",
                "stake_dampener_reason": "medium_neutral_over_dampener",
                "stake_dampener_factor": 0.5,
            },
        ],
    )
    player_prediction_rows = [
        {**full_rows[index % len(full_rows)], "player_prediction_row": index}
        for index in range(18)
    ]
    _write_csv(research / f"player_predictions_{prediction_date}.csv", player_prediction_rows)
    _write_json(
        research / f"model_metrics_{prediction_date}.json",
        {
            "prediction_summary": {
                "prediction_date": prediction_date,
                "games_count": 3,
                "odds_count": 8,
                "candidate_count": 6,
                "full_market_count": 6,
                "elite_count": 3,
                "pipeline_mode": "fixture_operator_smoke",
                "market_quality_status": "live",
            }
        },
    )
    _write_json(
        diagnostics / f"board_diagnostics_{prediction_date}.json",
        {"board_counts": {"elite": 3, "full_market": 6, "qualified_pool": 6}},
    )
    _write_json(
        operator / f"elite_pipeline_audit_summary_{prediction_date}.json",
        {
            "totals": {"total_candidates": 6, "passed_to_elite": 3, "total_rejections": 3},
            "rows": [
                {
                    "market": "player_assists",
                    "selection_side": "under",
                    "rejection_reason": "market_filtered_by_elite_policy",
                    "count": 1,
                },
                {
                    "market": "player_rebounds",
                    "selection_side": "over",
                    "rejection_reason": "market_filtered_by_elite_policy",
                    "count": 1,
                },
                {
                    "market": "player_rebounds",
                    "selection_side": "under",
                    "rejection_reason": "market_filtered_by_elite_policy",
                    "count": 1,
                },
                {"market": "player_points", "rejection_reason": "passed_to_elite", "count": 3},
            ],
        },
    )
    _write_json(
        diagnostics / f"market_availability_audit_{prediction_date}.json",
        {
            "counts": [
                {
                    "market": "player_points",
                    "count_after_normalization": 4,
                    "count_after_candidate_generation": 3,
                },
                {
                    "market": "player_rebounds",
                    "count_after_normalization": 3,
                    "count_after_candidate_generation": 2,
                },
                {
                    "market": "player_assists",
                    "count_after_normalization": 1,
                    "count_after_candidate_generation": 1,
                },
                {
                    "market": "player_3pt_made",
                    "count_after_normalization": 2,
                    "count_after_candidate_generation": 0,
                },
            ]
        },
    )


def _seed_run_health_artifacts(
    runtime_root: Path,
    prediction_date: str,
    *,
    games_count: int = 1,
    full_market_count: int = 10,
    elite_count: int = 2,
    kelly_eligible_count: int = 2,
    kelly_rows_count: int | None = None,
    context_gate_rejections: int = 0,
    high_caution_over_skips: int = 0,
) -> None:
    _seed_player_baselines(runtime_root, teams=("BOS", "NYK"), players_per_team=20)
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    research = runtime_root / "research"
    kelly_rows_count = kelly_eligible_count if kelly_rows_count is None else kelly_rows_count

    full_rows: list[dict] = []
    for index in range(full_market_count):
        gated = index < context_gate_rejections
        full_rows.append(
            {
                "prediction_date": prediction_date,
                "player_name": f"Health Player {index}",
                "player_id": 10_000 + index,
                "team": "BOS",
                "game_id": "game-health",
                "market_type": "player_points",
                "selection": "over" if gated else "under",
                "line": 10.5 + index,
                "odds": -110,
                "confidence": 0.75,
                "quality_score": 80 - index,
                "edge": 0.12,
                "context_caution_level": "high" if gated else "low",
                "context_pick_alignment": "conflicted" if gated else "aligned",
                "final_elite_rejection_reason": (
                    "elite_reject_context_high_caution_over" if gated else ""
                ),
                "is_live_market": True,
                "line_source": "fixture_live_market",
            }
        )
    elite_rows = [
        {**row, "final_elite_rejection_reason": ""}
        for row in full_rows[context_gate_rejections : context_gate_rejections + elite_count]
    ]

    kelly_rows: list[dict] = []
    for index in range(kelly_rows_count):
        skipped_for_context = index < high_caution_over_skips
        eligible = index < kelly_eligible_count and not skipped_for_context
        kelly_rows.append(
            {
                "prediction_date": prediction_date,
                "player_name": f"Health Kelly {index}",
                "market_type": "player_points",
                "selection": "over" if skipped_for_context else "under",
                "stake_amount": 8.0 if eligible else 0.0,
                "expected_value": 1.25 if eligible else 0.0,
                "kelly_eligible": eligible,
                "eligible": eligible,
                "skip_reason": "context_high_caution_over" if skipped_for_context else ("" if eligible else "no_positive_edge"),
                "context_caution_level": "high" if skipped_for_context else "low",
                "context_pick_alignment": "conflicted" if skipped_for_context else "aligned",
            }
        )

    _write_csv(operator / f"full_market_board_{prediction_date}.csv", full_rows)
    _write_csv(operator / f"elite_board_{prediction_date}.csv", elite_rows)
    _write_csv(operator / f"kelly_stakes_{prediction_date}.csv", kelly_rows)
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [{"prediction_date": prediction_date, "leg_count": 2}])
    _write_csv(research / f"player_predictions_{prediction_date}.csv", full_rows)
    _write_json(
        research / f"model_metrics_{prediction_date}.json",
        {
            "prediction_summary": {
                "prediction_date": prediction_date,
                "games_count": games_count,
                "odds_count": full_market_count,
                "candidate_count": full_market_count,
                "full_market_count": full_market_count,
                "elite_count": elite_count,
                "pipeline_mode": "fixture_operator_smoke",
                "market_quality_status": "live",
            }
        },
    )
    _write_json(
        diagnostics / f"board_diagnostics_{prediction_date}.json",
        {
            "board_counts": {
                "elite": elite_count,
                "full_market": full_market_count,
                "qualified_pool": full_market_count,
            }
        },
    )
    _write_json(
        operator / f"elite_pipeline_audit_summary_{prediction_date}.json",
        {
            "totals": {
                "total_candidates": full_market_count,
                "passed_to_elite": elite_count,
                "total_rejections": max(full_market_count - elite_count, 0),
            },
            "rows": [],
        },
    )
    _write_json(
        diagnostics / f"market_availability_audit_{prediction_date}.json",
        {
            "counts": [
                {
                    "market": "player_points",
                    "count_before_normalization": full_market_count,
                    "count_after_normalization": full_market_count,
                    "count_after_candidate_generation": full_market_count,
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
    assert payload["slate_provider_counts"]["players_or_baselines_count"] == 3
    assert payload["player_baseline_coverage"]["player_predictions_rows"] == 3
    assert payload["player_baseline_coverage"]["baseline_rows"] == 70
    assert payload["player_baseline_coverage"]["unique_baseline_players"] == 70
    assert payload["player_baseline_coverage"]["active_team_baseline_players"] == 53
    assert payload["player_baseline_coverage"]["odds_unique_players"] == 3
    assert payload["player_baseline_coverage"]["odds_baseline_matched_players"] == 3
    assert payload["player_baseline_coverage"]["full_market_unique_players"] == 3
    assert payload["player_baseline_coverage"]["elite_unique_players"] == 3
    assert payload["player_baseline_coverage"]["kelly_unique_players"] == 3
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

    history = _read_history_csv(runtime_root)
    assert list(history.columns) == list(QUALITY_HISTORY_COLUMNS)
    assert len(history) == 1
    history_row = history.iloc[0]
    assert history_row["prediction_date"] == prediction_date
    assert int(history_row["player_predictions_rows"]) == 3
    assert int(history_row["baseline_rows"]) == 70
    assert int(history_row["unique_baseline_players"]) == 70
    assert int(history_row["active_team_baseline_players"]) == 53
    assert int(history_row["odds_unique_players"]) == 3
    assert int(history_row["odds_baseline_matched_players"]) == 3
    assert int(history_row["full_market_unique_players"]) == 3
    assert int(history_row["elite_unique_players"]) == 3
    assert int(history_row["kelly_unique_players"]) == 3
    assert int(history_row["elite_board_count"]) == 3
    assert int(history_row["medium_neutral_over_dampened_count"]) == 1
    assert history_row["run_health_status"] == "DEGRADED_CONTEXT_BLOCKED"
    assert (runtime_root / "operator" / QUALITY_HISTORY_JSONL_NAME).exists()


def test_quality_summary_run_health_healthy(tmp_path: Path) -> None:
    prediction_date = "2026-05-02"
    runtime_root = tmp_path / "runtime"
    _seed_run_health_artifacts(runtime_root, prediction_date)

    text, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-03T00:00:00+00:00",
    )

    assert payload["run_health_status"] == "HEALTHY"
    assert payload["run_health"]["recommendation"] == "Run is bet-ready within configured staking limits."
    assert payload["run_health_flags"] == []
    assert "Run Health" in text
    assert "- status: HEALTHY" in text


def test_quality_summary_run_health_healthy_low_volume(tmp_path: Path) -> None:
    prediction_date = "2026-05-02"
    runtime_root = tmp_path / "runtime"
    _seed_run_health_artifacts(
        runtime_root,
        prediction_date,
        elite_count=1,
        kelly_eligible_count=1,
        kelly_rows_count=1,
    )

    _, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-03T00:00:00+00:00",
    )

    assert payload["run_health_status"] == "HEALTHY_LOW_VOLUME"
    assert "kelly_eligible_low_volume" in payload["run_health_flags"]


def test_quality_summary_run_health_degraded_low_coverage(tmp_path: Path) -> None:
    prediction_date = "2026-05-02"
    runtime_root = tmp_path / "runtime"
    _seed_run_health_artifacts(
        runtime_root,
        prediction_date,
        full_market_count=2,
        elite_count=2,
        kelly_eligible_count=2,
        kelly_rows_count=2,
    )

    _, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-03T00:00:00+00:00",
    )

    assert payload["run_health_status"] == "DEGRADED_LOW_COVERAGE"
    assert "provider_or_candidate_coverage_low" in payload["run_health_flags"]


def test_quality_summary_active_board_coverage_uses_odds_denominator(tmp_path: Path) -> None:
    prediction_date = "2026-05-02"
    runtime_root = tmp_path / "runtime"
    _seed_run_health_artifacts(
        runtime_root,
        prediction_date,
        games_count=2,
        full_market_count=28,
        elite_count=2,
        kelly_eligible_count=2,
        kelly_rows_count=2,
    )

    _, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-03T00:00:00+00:00",
    )

    coverage = payload["player_baseline_coverage"]
    assert coverage["baseline_universe_player_count"] > coverage["odds_unique_players"]
    assert coverage["odds_unique_players"] == 28
    assert coverage["odds_baseline_matched_players"] == 28
    assert coverage["active_board_match_rate"] == 1.0
    warnings = payload["market_coverage"]["coverage_warnings"]
    assert not any("Low active-board baseline match" in warning for warning in warnings)
    assert "provider_or_candidate_coverage_low" not in payload["run_health_flags"]


def test_quality_summary_run_health_context_gated_when_final_elite_is_clean(tmp_path: Path) -> None:
    prediction_date = "2026-05-02"
    runtime_root = tmp_path / "runtime"
    _seed_run_health_artifacts(
        runtime_root,
        prediction_date,
        full_market_count=10,
        elite_count=2,
        kelly_eligible_count=2,
        kelly_rows_count=2,
        context_gate_rejections=6,
    )

    _, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-03T00:00:00+00:00",
    )

    assert payload["run_health_status"] == "HEALTHY_CONTEXT_GATED"
    assert "valid_context_safety_blocking" in payload["run_health_flags"]
    assert "elite_context_gate_rejection_rate_high" not in payload["run_health_flags"]
    assert payload["elite_context_safety_gate"]["elite_high_caution_conflicted_over_count"] == 0


def test_quality_summary_run_health_no_bet(tmp_path: Path) -> None:
    prediction_date = "2026-05-02"
    runtime_root = tmp_path / "runtime"
    _seed_run_health_artifacts(
        runtime_root,
        prediction_date,
        elite_count=2,
        kelly_eligible_count=0,
        kelly_rows_count=2,
    )

    _, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-03T00:00:00+00:00",
    )

    assert payload["run_health_status"] == "NO_BET"
    assert "kelly_rows_exist_no_eligible" in payload["run_health_flags"]
    assert payload["run_health_recommendation"] == "No stakeable picks. Do not force action."


def test_quality_summary_run_health_error_or_incomplete_for_missing_required_artifact(tmp_path: Path) -> None:
    prediction_date = "2026-05-02"
    runtime_root = tmp_path / "runtime"
    _write_csv(runtime_root / "operator" / f"full_market_board_{prediction_date}.csv", [])
    _write_csv(runtime_root / "operator" / f"kelly_stakes_{prediction_date}.csv", [])

    _, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-03T00:00:00+00:00",
    )

    assert payload["run_health_status"] == "ERROR_OR_INCOMPLETE"
    assert "required_artifact_missing_or_unreadable" in payload["run_health_flags"]


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


def test_quality_summary_includes_market_coverage_for_points_rebounds_assists(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _seed_market_coverage_artifacts(runtime_root, prediction_date)

    text, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-01T00:00:00+00:00",
    )

    rows = {row["market"]: row for row in payload["market_coverage"]["rows"]}
    assert set(rows) == {"player_3pt_made", "player_assists", "player_points", "player_rebounds"}
    assert rows["player_points"]["normalized_odds_rows"] == 4
    assert rows["player_points"]["raw_candidates_count"] == 3
    assert rows["player_points"]["full_market_board_count"] == 3
    assert rows["player_points"]["elite_board_count"] == 3
    assert rows["player_points"]["kelly_rows_count"] == 3
    assert rows["player_points"]["kelly_eligible_count"] == 2
    assert rows["player_points"]["high_caution_over_skip_count"] == 1
    assert rows["player_points"]["medium_neutral_over_dampened_count"] == 1
    assert rows["player_rebounds"]["rejected_count"] == 2
    assert rows["player_assists"]["rejected_count"] == 1
    assert "Market Coverage" in text
    assert "player_rebounds" in text


def test_quality_summary_rejection_reasons_by_market(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _seed_market_coverage_artifacts(runtime_root, prediction_date)

    _, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-01T00:00:00+00:00",
    )

    rows = {
        (row["market"], row["rejection_reason"]): row
        for row in payload["market_coverage"]["rejection_reasons_by_market"]
    }
    assert rows[("player_rebounds", "market_filtered_by_elite_policy")]["count"] == 2
    assert rows[("player_rebounds", "market_filtered_by_elite_policy")]["percentage"] == 100.0
    assert rows[("player_assists", "market_filtered_by_elite_policy")]["count"] == 1
    assert rows[("player_assists", "market_filtered_by_elite_policy")]["percentage"] == 100.0


def test_quality_summary_surfaces_unsupported_active_market_drops(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _seed_market_coverage_artifacts(runtime_root, prediction_date)
    _write_json(
        runtime_root / "diagnostics" / f"board_diagnostics_{prediction_date}.json",
        {
            "board_counts": {"elite": 3, "full_market": 6, "qualified_pool": 12},
            "unsupported_active_operator_markets": {
                "rejection_reason": "unsupported_active_operator_market",
                "total_rows_dropped": 6,
                "counts_by_market_type": {
                    "player_blocks": 3,
                    "player_steals": 3,
                },
            },
        },
    )

    text, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-01T00:00:00+00:00",
    )

    assert payload["unsupported_active_operator_markets"] == {
        "rejection_reason": "unsupported_active_operator_market",
        "total_rows_dropped": 6,
        "counts_by_market_type": {
            "player_blocks": 3,
            "player_steals": 3,
        },
    }
    assert payload["candidate_funnel"]["unsupported_active_operator_market_drop_count"] == 6
    assert payload["candidate_funnel"]["unsupported_active_operator_market_drop_counts_by_market_type"] == {
        "player_blocks": 3,
        "player_steals": 3,
    }
    assert (
        "- unsupported active markets dropped: 6 (player_blocks=3, player_steals=3); "
        "reason=unsupported_active_operator_market"
    ) in text


def test_quality_summary_surfaces_identity_quarantine_counts(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _seed_market_coverage_artifacts(runtime_root, prediction_date)
    _write_json(
        runtime_root / "diagnostics" / f"board_diagnostics_{prediction_date}.json",
        {
            "board_counts": {"elite": 1, "full_market": 2, "qualified_pool": 3},
            "identity_quarantine": {
                "rejection_reason": "identity_quarantine",
                "total_rows_dropped": 1,
                "counts_by_reason": {"stale_team_identity": 1},
            },
        },
    )

    text, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-01T00:00:00+00:00",
    )

    assert payload["identity_quarantine"] == {
        "rejection_reason": "identity_quarantine",
        "total_rows_dropped": 1,
        "counts_by_reason": {"stale_team_identity": 1},
    }
    assert payload["candidate_funnel"]["identity_quarantine_count"] == 1
    assert payload["candidate_funnel"]["identity_quarantine_reason_counts"] == {"stale_team_identity": 1}
    assert "- identity_quarantine_count: 1" in text
    assert "- identity quarantined: 1 (stale_team_identity=1); reason=identity_quarantine" in text


def test_quality_summary_zero_unsupported_active_market_drops_stays_quiet(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _seed_market_coverage_artifacts(runtime_root, prediction_date)

    text, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-01T00:00:00+00:00",
    )

    assert payload["unsupported_active_operator_markets"]["total_rows_dropped"] == 0
    assert payload["candidate_funnel"]["unsupported_active_operator_market_drop_count"] == 0
    assert "unsupported active markets dropped" not in text


def test_quality_summary_low_live_candidate_warning(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _seed_market_coverage_artifacts(runtime_root, prediction_date)

    _, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-01T00:00:00+00:00",
    )

    warnings = payload["market_coverage"]["coverage_warnings"]
    assert any("Low live candidate coverage" in warning for warning in warnings)
    assert any("Low provider player coverage" in warning for warning in warnings)
    assert any("Low full-market player coverage" in warning for warning in warnings)


def test_quality_summary_low_kelly_eligible_warning(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _seed_market_coverage_artifacts(runtime_root, prediction_date)
    _write_csv(
        runtime_root / "operator" / f"kelly_stakes_{prediction_date}.csv",
        [
            {
                "prediction_date": prediction_date,
                "player_name": "Only Eligible",
                "market_type": "player_points",
                "kelly_eligible": True,
                "skip_reason": "",
                "stake_amount": 10.0,
                "expected_value": 1.0,
            }
        ],
    )

    _, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-01T00:00:00+00:00",
    )

    warnings = payload["market_coverage"]["coverage_warnings"]
    assert any("Low Kelly eligible coverage" in warning for warning in warnings)


def test_quality_summary_separates_player_prediction_rows_from_healthy_baselines(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _seed_market_coverage_artifacts(runtime_root, prediction_date)
    _write_csv(
        runtime_root / "research" / f"player_predictions_{prediction_date}.csv",
        [{"prediction_date": prediction_date, "player_name": "Only Player", "market_type": "player_points"}],
    )

    _, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-01T00:00:00+00:00",
    )

    coverage = payload["player_baseline_coverage"]
    assert coverage["player_predictions_rows"] == 1
    assert coverage["baseline_rows"] == 70
    assert coverage["unique_baseline_players"] == 70
    assert coverage["active_team_baseline_players"] == 70
    warnings = payload["market_coverage"]["coverage_warnings"]
    assert not any("Low active-team baseline coverage" in warning for warning in warnings)
    assert not any("Low player/baseline coverage" in warning for warning in warnings)


def test_quality_summary_empty_sgp_warning_is_clean(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _seed_market_coverage_artifacts(runtime_root, prediction_date)

    text, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-01T00:00:00+00:00",
    )

    assert any("SGP board artifact is empty" in warning for warning in payload["warnings"])
    assert "No columns to parse from file" not in text
    assert all("No columns to parse from file" not in warning for warning in payload["warnings"])


def test_quality_summary_missing_baseline_file_warns_without_crashing(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _seed_market_coverage_artifacts(runtime_root, prediction_date)
    baseline_path = runtime_root.parent / "model" / "player_baselines.csv"
    baseline_path.unlink()

    text, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-01T00:00:00+00:00",
    )

    coverage = payload["player_baseline_coverage"]
    assert coverage["baseline_rows"] == 0
    assert coverage["unique_baseline_players"] == 0
    assert coverage["active_team_baseline_players"] is None
    assert coverage["odds_baseline_matched_players"] == 0
    assert "Missing optional baseline artifact" in text
    assert any("Missing optional baseline artifact" in warning for warning in payload["warnings"])


def test_quality_history_includes_compact_market_coverage_fields(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _seed_market_coverage_artifacts(runtime_root, prediction_date)

    write_quality_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-01T00:00:00+00:00",
    )

    history = _read_history_csv(runtime_root)
    row = history.iloc[0]
    assert int(row["player_predictions_rows"]) == 18
    assert int(row["baseline_rows"]) == 70
    assert int(row["unique_baseline_players"]) == 70
    assert int(row["active_team_baseline_players"]) == 70
    assert int(row["odds_unique_players"]) == 6
    assert int(row["odds_baseline_matched_players"]) == 6
    assert int(row["full_market_unique_players"]) == 6
    assert int(row["elite_unique_players"]) == 3
    assert int(row["kelly_unique_players"]) == 3
    assert int(row["markets_seen"]) == 4
    assert int(row["points_normalized_odds_rows"]) == 4
    assert int(row["points_full_market_board_count"]) == 3
    assert int(row["points_elite_board_count"]) == 3
    assert int(row["points_kelly_eligible_count"]) == 2
    assert int(row["non_points_rejected_count"]) == 3
    assert int(row["low_coverage_warning_count"]) >= 1


def test_quality_history_creates_csv_from_one_summary_json(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _write_quality_summary_json(
        runtime_root,
        prediction_date,
        _history_summary_payload(prediction_date),
    )

    history_csv, history_jsonl, row = update_quality_history_from_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    assert history_csv.exists()
    assert history_jsonl is not None
    assert history_jsonl.exists()
    assert row["prediction_date"] == prediction_date
    history = _read_history_csv(runtime_root)
    assert list(history.columns) == list(QUALITY_HISTORY_COLUMNS)
    assert len(history) == 1
    assert int(history.iloc[0]["kelly_eligible_count"]) == 2
    assert int(history.iloc[0]["player_predictions_rows"]) == 8
    assert int(history.iloc[0]["baseline_rows"]) == 64
    assert int(history.iloc[0]["unique_baseline_players"]) == 64
    assert int(history.iloc[0]["active_team_baseline_players"]) == 32
    assert int(history.iloc[0]["odds_unique_players"]) == 5
    assert int(history.iloc[0]["odds_baseline_matched_players"]) == 5
    assert int(history.iloc[0]["full_market_unique_players"]) == 5
    assert int(history.iloc[0]["elite_unique_players"]) == 3
    assert int(history.iloc[0]["kelly_unique_players"]) == 3
    assert int(history.iloc[0]["markets_seen"]) == 2
    assert int(history.iloc[0]["points_normalized_odds_rows"]) == 4
    assert int(history.iloc[0]["points_elite_board_count"]) == 3
    assert int(history.iloc[0]["points_kelly_eligible_count"]) == 2
    assert int(history.iloc[0]["non_points_rejected_count"]) == 1
    assert int(history.iloc[0]["low_coverage_warning_count"]) == 1
    assert history.iloc[0]["run_health_status"] == "DEGRADED_LOW_COVERAGE"
    assert history.iloc[0]["run_health_flags"] == "provider_or_candidate_coverage_low"
    assert "Provider/candidate coverage" in history.iloc[0]["run_health_recommendation"]


def test_quality_history_updates_same_date_instead_of_duplicating(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _write_quality_summary_json(
        runtime_root,
        prediction_date,
        _history_summary_payload(prediction_date, elite_count=3, total_stake=22.0),
    )
    update_quality_history_from_summary(prediction_date=prediction_date, runtime_root=runtime_root)

    _write_quality_summary_json(
        runtime_root,
        prediction_date,
        _history_summary_payload(
            prediction_date,
            generated_at="2026-05-01T01:00:00+00:00",
            elite_count=6,
            total_stake=44.0,
        ),
    )
    update_quality_history_from_summary(prediction_date=prediction_date, runtime_root=runtime_root)

    history = _read_history_csv(runtime_root)
    assert len(history) == 1
    assert int(history.iloc[0]["elite_board_count"]) == 6
    assert float(history.iloc[0]["total_stake"]) == 44.0
    assert history.iloc[0]["run_health_status"] == "DEGRADED_LOW_COVERAGE"
    jsonl_rows = [
        json.loads(line)
        for line in (runtime_root / "operator" / QUALITY_HISTORY_JSONL_NAME)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(jsonl_rows) == 1
    assert jsonl_rows[0]["elite_board_count"] == 6
    assert jsonl_rows[0]["run_health_status"] == "DEGRADED_LOW_COVERAGE"


def test_quality_history_adds_second_date_in_sorted_order(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    later_date = "2026-05-01"
    earlier_date = "2026-04-30"
    _write_quality_summary_json(
        runtime_root,
        later_date,
        _history_summary_payload(later_date, elite_count=4),
    )
    update_quality_history_from_summary(prediction_date=later_date, runtime_root=runtime_root)
    _write_quality_summary_json(
        runtime_root,
        earlier_date,
        _history_summary_payload(earlier_date, elite_count=3),
    )
    update_quality_history_from_summary(prediction_date=earlier_date, runtime_root=runtime_root)

    history = _read_history_csv(runtime_root)
    assert history["prediction_date"].tolist() == [earlier_date, later_date]
    assert history["elite_board_count"].astype(int).tolist() == [3, 4]


def test_quality_history_missing_optional_fields_default_to_blanks_and_zeroes(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    _write_quality_summary_json(runtime_root, prediction_date, {"run_identity": {"prediction_date": prediction_date}})

    update_quality_history_from_summary(prediction_date=prediction_date, runtime_root=runtime_root)

    row = _read_history_csv(runtime_root).iloc[0]
    assert row["prediction_date"] == prediction_date
    assert row["generated_at"] == ""
    assert row["data_mode"] == ""
    assert row["date_isolation_status"] == ""
    assert int(row["games_count"]) == 0
    assert int(row["players_count"]) == 0
    assert int(row["player_predictions_rows"]) == 0
    assert int(row["baseline_rows"]) == 0
    assert int(row["unique_baseline_players"]) == 0
    assert int(row["active_team_baseline_players"]) == 0
    assert int(row["odds_unique_players"]) == 0
    assert int(row["odds_baseline_matched_players"]) == 0
    assert int(row["full_market_unique_players"]) == 0
    assert int(row["elite_unique_players"]) == 0
    assert int(row["kelly_unique_players"]) == 0
    assert int(row["kelly_rows_count"]) == 0
    assert int(row["markets_seen"]) == 0
    assert int(row["points_normalized_odds_rows"]) == 0
    assert int(row["non_points_rejected_count"]) == 0
    assert int(row["low_coverage_warning_count"]) == 0
    assert row["run_health_status"] == ""
    assert row["run_health_reason"] == ""
    assert row["run_health_flags"] == ""
    assert row["run_health_recommendation"] == ""
    assert int(row["warnings_count"]) == 0


def test_quality_history_update_does_not_modify_prediction_artifacts(tmp_path: Path) -> None:
    prediction_date = "2026-04-30"
    runtime_root = tmp_path / "runtime"
    elite_path = runtime_root / "operator" / f"elite_board_{prediction_date}.csv"
    _write_csv(
        elite_path,
        [{"prediction_date": prediction_date, "player_name": "Sentinel Player"}],
    )
    original_bytes = elite_path.read_bytes()
    original_mtime_ns = elite_path.stat().st_mtime_ns
    _write_quality_summary_json(
        runtime_root,
        prediction_date,
        _history_summary_payload(prediction_date),
    )

    update_quality_history_from_summary(prediction_date=prediction_date, runtime_root=runtime_root)

    assert elite_path.read_bytes() == original_bytes
    assert elite_path.stat().st_mtime_ns == original_mtime_ns
    assert (runtime_root / "operator" / QUALITY_HISTORY_CSV_NAME).exists()


def test_quality_summary_reports_elite_context_safety_gate(tmp_path: Path) -> None:
    prediction_date = "2026-05-02"
    runtime_root = tmp_path / "runtime"
    operator = runtime_root / "operator"

    _write_csv(
        operator / f"elite_board_{prediction_date}.csv",
        [
            {
                "prediction_date": prediction_date,
                "player_name": "Safer Under",
                "market_type": "player_points",
                "selection": "under",
                "context_caution_level": "low",
                "context_pick_alignment": "aligned",
                "final_elite_rejection_reason": "",
            }
        ],
    )
    _write_csv(
        operator / f"full_market_board_{prediction_date}.csv",
        [
            {
                "prediction_date": prediction_date,
                "player_name": "Blocked Over",
                "market_type": "player_points",
                "selection": "over",
                "context_caution_level": "high",
                "context_pick_alignment": "conflicted",
                "context_conflict_cause": "defense_driven",
                "final_elite_rejection_reason": "elite_reject_context_high_caution_over",
            },
            {
                "prediction_date": prediction_date,
                "player_name": "Safer Under",
                "market_type": "player_points",
                "selection": "under",
                "context_caution_level": "low",
                "context_pick_alignment": "aligned",
                "final_elite_rejection_reason": "",
            },
        ],
    )
    _write_csv(
        operator / f"kelly_stakes_{prediction_date}.csv",
        [
            {
                "prediction_date": prediction_date,
                "player_name": "Safer Under",
                "market_type": "player_points",
                "selection": "under",
                "stake_amount": 8.0,
                "expected_value": 1.2,
                "kelly_eligible": True,
                "skip_reason": "",
            }
        ],
    )

    text, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-03T00:00:00+00:00",
    )

    gate = payload["elite_context_safety_gate"]
    assert gate["rejected_from_final_elite_count"] == 1
    assert gate["elite_high_caution_conflicted_over_count"] == 0
    assert gate["final_elite_context_clean"] is True
    assert gate["full_market_context_conflict_cause_counts"]["defense_driven"] == 1
    assert gate["status"] == "ok"
    assert any("Elite context safety gate excluded 1" in warning for warning in payload["warnings"])
    assert "Elite Context Safety Gate" in text
    assert "elite_reject_context_high_caution_over" in text


# ── Phase P0: history_root canonicalisation ───────────────────────────────────

def test_write_quality_summary_uses_explicit_history_root(tmp_path: Path) -> None:
    """history_root parameter is used instead of the old runtime_root.parent/history derivation."""
    prediction_date = "2026-05-11"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "my_history"
    history_root.mkdir(parents=True, exist_ok=True)

    # Write a marker row to the caller-supplied history_root.
    pd.DataFrame([{
        "prediction_date": prediction_date,
        "result_status": "hit",
        "fragility_bucket": "LOW",
        "survivability_bucket": "HIGH",
        "confidence": 0.70,
        "market_type": "player_points",
        "selection": "under",
        "shadow_roi": 0.05,
    }]).to_csv(history_root / "market_shadow_history.csv", index=False)

    # Minimal operator artifacts so write_quality_summary_outputs doesn't error.
    operator = runtime_root / "operator"
    operator.mkdir(parents=True, exist_ok=True)
    (runtime_root / "diagnostics").mkdir(parents=True, exist_ok=True)
    (runtime_root / "research").mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"prediction_date": prediction_date}]).to_csv(
        operator / f"elite_board_{prediction_date}.csv", index=False
    )
    pd.DataFrame([{"prediction_date": prediction_date}]).to_csv(
        operator / f"full_market_board_{prediction_date}.csv", index=False
    )
    pd.DataFrame([]).to_csv(operator / f"kelly_stakes_{prediction_date}.csv", index=False)
    (runtime_root / "research" / f"player_predictions_{prediction_date}.csv").write_text("", encoding="utf-8")
    (runtime_root / "research" / f"model_metrics_{prediction_date}.json").write_text("{}", encoding="utf-8")
    (operator / f"elite_pipeline_audit_summary_{prediction_date}.json").write_text("{}", encoding="utf-8")
    (runtime_root / "diagnostics" / f"board_diagnostics_{prediction_date}.json").write_text("{}", encoding="utf-8")

    _txt, _json, payload = write_quality_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        history_root=history_root,
    )

    # The outcome_validation block read from history_root and saw the 1 row.
    fov = payload.get("fragility_outcome_validation", {})
    assert fov.get("total_graded_rows", 0) >= 1, (
        "fragility_outcome_validation saw 0 rows — history_root parameter not respected"
    )


def test_write_quality_summary_default_history_root_is_data_history(tmp_path: Path) -> None:
    """When history_root is not supplied the function resolves to data/history, not outputs/history."""
    import inspect
    from courtvision.reporting import quality_summary as qs_mod

    src = inspect.getsource(qs_mod.write_quality_summary_outputs)
    # The canonical path must appear in the source; the old derived path must not.
    assert "data/history" in src, "default history_root must be data/history"
    assert 'runtime_root.parent / "history"' not in src, (
        "old outputs/history derivation must be removed"
    )
