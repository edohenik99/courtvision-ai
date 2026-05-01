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
            "raw_odds_rows_count": 12,
            "normalized_odds_rows_count": 10,
            "live_odds_count": 9,
            "synthetic_or_fallback_odds_count": 1,
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
        "date_isolation_check": {"status": "ok"},
        "warnings": warnings or [],
    }


def _write_quality_summary_json(runtime_root: Path, prediction_date: str, payload: dict) -> None:
    _write_json(runtime_root / "operator" / f"quality_summary_{prediction_date}.json", payload)


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

    history = _read_history_csv(runtime_root)
    assert list(history.columns) == list(QUALITY_HISTORY_COLUMNS)
    assert len(history) == 1
    history_row = history.iloc[0]
    assert history_row["prediction_date"] == prediction_date
    assert int(history_row["elite_board_count"]) == 3
    assert int(history_row["medium_neutral_over_dampened_count"]) == 1
    assert (runtime_root / "operator" / QUALITY_HISTORY_JSONL_NAME).exists()


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
    jsonl_rows = [
        json.loads(line)
        for line in (runtime_root / "operator" / QUALITY_HISTORY_JSONL_NAME)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(jsonl_rows) == 1
    assert jsonl_rows[0]["elite_board_count"] == 6


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
    assert int(row["kelly_rows_count"]) == 0
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
