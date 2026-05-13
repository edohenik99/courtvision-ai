from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.actual_minutes_source_audit import (
    build_actual_minutes_source_audit,
    calculate_join_coverage,
    detect_minutes_like_columns,
    join_key_availability,
    provider_client_candidates_summary,
    select_readiness_verdict,
    write_actual_minutes_source_audit,
)
from courtvision.reporting.quality_summary import write_quality_summary_outputs


def _none_pattern(tmp_path: Path, suffix: str = "csv") -> str:
    return str(tmp_path / f"none_*.{suffix}")


def _history_rows() -> list[dict]:
    return [
        {
            "prediction_date": "2026-05-11",
            "player_id": 101,
            "player_name": "Player A",
            "game_id": 9001,
            "market_type": "player_points",
            "selection": "over",
            "line": 7.5,
            "result_status": "miss",
            "actual_value": 6,
        },
        {
            "prediction_date": "2026-05-11",
            "player_id": 102,
            "player_name": "Player B",
            "game_id": 9002,
            "market_type": "player_points",
            "selection": "under",
            "line": 25.5,
            "result_status": "hit",
            "actual_value": 20,
        },
    ]


def _build_payload(
    tmp_path: Path,
    *,
    market_shadow: pd.DataFrame | None = None,
    pick_history: pd.DataFrame | None = None,
    player_predictions_glob: str | None = None,
) -> dict:
    return build_actual_minutes_source_audit(
        "2026-05-11",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=market_shadow if market_shadow is not None else pd.DataFrame(_history_rows()),
        pick_history=pick_history if pick_history is not None else pd.DataFrame(_history_rows()),
        player_baselines=pd.DataFrame(),
        grading_glob=_none_pattern(tmp_path),
        player_predictions_glob=player_predictions_glob or _none_pattern(tmp_path),
        full_market_glob=_none_pattern(tmp_path),
        minutes_availability_glob=_none_pattern(tmp_path, "json"),
    )


def test_minutes_like_column_detection() -> None:
    assert detect_minutes_like_columns(["player_id", "minutes", "min_avg", "actual_minutes"]) == [
        "actual_minutes",
        "min_avg",
        "minutes",
    ]


def test_join_key_coverage_calculation() -> None:
    coverage = join_key_availability(pd.DataFrame(_history_rows()))
    assert coverage["rows"] == 2
    assert coverage["player_id_coverage"] == 1.0
    assert coverage["game_id_coverage"] == 1.0
    assert coverage["prediction_date_coverage"] == 1.0
    assert coverage["strict_player_id_game_id_date_coverage"] == 1.0


def test_actual_minutes_candidate_source_detection(tmp_path: Path) -> None:
    stats_path = tmp_path / "runtime" / "research" / "player_predictions_2026-05-11.csv"
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "player_id": 101,
                "player_name": "Player A",
                "game_id": 9001,
                "game_date": "2026-05-11",
                "minutes": 22.5,
                "pts": 6,
            }
        ]
    ).to_csv(stats_path, index=False)

    payload = _build_payload(
        tmp_path,
        player_predictions_glob=str(stats_path),
    )

    assert payload["local_actual_minutes_found"] is True
    assert payload["actual_minutes_rows_found"] == 1
    assert any(row["is_actual_minutes_candidate"] for row in payload["candidate_source_files"])


def test_join_coverage_calculation() -> None:
    history = pd.DataFrame(_history_rows())
    actual = pd.DataFrame(
        [
            {
                "player_id_key": "101",
                "game_id_key": "9001",
                "date_key": "2026-05-11",
                "actual_minutes": 22,
            }
        ]
    )

    coverage = calculate_join_coverage(history, actual)
    assert coverage["rows"] == 2
    assert coverage["rows_with_strict_join_key"] == 2
    assert coverage["joined_rows"] == 1
    assert coverage["join_rate_all_rows"] == 0.5
    assert coverage["player_points_join_rate"] == 0.5
    assert coverage["low_line_over_join_rate"] == 1.0


def test_missing_source_fallback(tmp_path: Path) -> None:
    payload = _build_payload(tmp_path)
    assert payload["local_actual_minutes_found"] is False
    assert payload["actual_minutes_rows_found"] == 0
    assert payload["recommended_source"] == "existing provider client function"
    assert payload["readiness_verdict"] == "PROVIDER_CLIENT_EXISTS_FETCH_NOT_VALIDATED"


def test_readiness_verdict_selection() -> None:
    provider = provider_client_candidates_summary()
    assert (
        select_readiness_verdict(
            local_actual_minutes_found=False,
            actual_minutes_rows_found=0,
            market_shadow_join_coverage={},
            pick_history_join_coverage={},
            provider_client_candidates=provider,
            missing_critical_fields=[],
        )
        == "PROVIDER_CLIENT_EXISTS_FETCH_NOT_VALIDATED"
    )
    assert (
        select_readiness_verdict(
            local_actual_minutes_found=True,
            actual_minutes_rows_found=2,
            market_shadow_join_coverage={"join_rate_keyed_rows": 0.5},
            pick_history_join_coverage={"join_rate_keyed_rows": 0.5},
            provider_client_candidates=provider,
            missing_critical_fields=[],
        )
        == "READY_FOR_PHASE_15C_MINUTES_ERROR_SHADOW"
    )
    assert (
        select_readiness_verdict(
            local_actual_minutes_found=True,
            actual_minutes_rows_found=2,
            market_shadow_join_coverage={"join_rate_keyed_rows": 0.0},
            pick_history_join_coverage={"join_rate_keyed_rows": 0.0},
            provider_client_candidates=provider,
            missing_critical_fields=["market_shadow_game_id"],
        )
        == "LOCAL_ACTUAL_MINUTES_FOUND_JOIN_KEYS_MISSING"
    )


def test_provider_client_candidate_summary() -> None:
    candidates = provider_client_candidates_summary()
    providers = {row["provider"] for row in candidates}
    assert {"balldontlie", "sportsdataio", "provider_manager"}.issubset(providers)
    assert all(row["returns_minutes"] for row in candidates)
    assert all(row["requires_api_call"] for row in candidates)
    assert all(row["mutates_history"] is False for row in candidates)


def test_json_artifact_writing(tmp_path: Path) -> None:
    json_path, _, payload = write_actual_minutes_source_audit(
        "2026-05-11",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(_history_rows()),
        pick_history=pd.DataFrame(_history_rows()),
        player_baselines=pd.DataFrame(),
        grading_glob=_none_pattern(tmp_path),
        player_predictions_glob=_none_pattern(tmp_path),
        full_market_glob=_none_pattern(tmp_path),
        minutes_availability_glob=_none_pattern(tmp_path, "json"),
    )
    assert json_path.exists()
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["note"] == "audit_only_no_history_mutation"
    assert saved["readiness_verdict"] == payload["readiness_verdict"]


def test_txt_artifact_writing(tmp_path: Path) -> None:
    _, txt_path, _ = write_actual_minutes_source_audit(
        "2026-05-11",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(_history_rows()),
        pick_history=pd.DataFrame(_history_rows()),
        player_baselines=pd.DataFrame(),
        grading_glob=_none_pattern(tmp_path),
        player_predictions_glob=_none_pattern(tmp_path),
        full_market_glob=_none_pattern(tmp_path),
        minutes_availability_glob=_none_pattern(tmp_path, "json"),
    )
    text = txt_path.read_text(encoding="utf-8")
    assert "ACTUAL MINUTES SOURCE AUDIT" in text
    assert "Do actual_minutes exist locally?" in text
    assert "Can we join actual_minutes to market_shadow_history?" in text


def test_quality_summary_integration(tmp_path: Path) -> None:
    prediction_date = "2026-05-11"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    research = runtime_root / "research"
    diagnostics = runtime_root / "diagnostics"
    model = tmp_path / "model"
    for directory in (operator, research, diagnostics, history_root, model):
        directory.mkdir(parents=True, exist_ok=True)

    row = {
        "prediction_date": prediction_date,
        "player_id": 101,
        "player_name": "Player A",
        "game_id": 9001,
        "market_type": "player_points",
        "selection": "over",
        "line": 7.5,
        "result_status": "miss",
        "actual_value": 6,
        "confidence": 0.7,
        "quality_score": 0.8,
        "selection_score": 0.8,
        "is_live_market": True,
        "line_source": "fixture_live_market",
    }
    pd.DataFrame([row]).to_csv(operator / f"elite_board_{prediction_date}.csv", index=False)
    pd.DataFrame([row]).to_csv(operator / f"full_market_board_{prediction_date}.csv", index=False)
    pd.DataFrame([row]).to_csv(research / f"player_predictions_{prediction_date}.csv", index=False)
    pd.DataFrame([row]).to_csv(history_root / "market_shadow_history.csv", index=False)
    pd.DataFrame([row]).to_csv(history_root / "pick_history.csv", index=False)
    pd.DataFrame([]).to_csv(operator / f"kelly_stakes_{prediction_date}.csv", index=False)
    pd.DataFrame([]).to_csv(operator / f"sgp_board_{prediction_date}.csv", index=False)
    pd.DataFrame([{"player_id": 101, "player_name": "Player A", "team_abbr": "BOS", "min_avg": 20}]).to_csv(
        model / "player_baselines.csv",
        index=False,
    )
    (research / f"model_metrics_{prediction_date}.json").write_text("{}", encoding="utf-8")
    (diagnostics / f"board_diagnostics_{prediction_date}.json").write_text("{}", encoding="utf-8")
    (operator / f"elite_pipeline_audit_summary_{prediction_date}.json").write_text("{}", encoding="utf-8")

    text_path, json_path, payload = write_quality_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        history_root=history_root,
    )

    assert "actual_minutes_source_audit" in payload
    audit = payload["actual_minutes_source_audit"]
    assert audit["note"] == "audit_only_no_history_mutation"
    assert Path(audit["json_path"]).exists()
    assert "ACTUAL MINUTES SOURCE AUDIT" in text_path.read_text(encoding="utf-8")
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert "actual_minutes_source_audit" in saved


def test_no_source_mutation(tmp_path: Path) -> None:
    market_shadow = pd.DataFrame(_history_rows())
    pick_history = pd.DataFrame(_history_rows())
    before_shadow = market_shadow.copy(deep=True)
    before_pick = pick_history.copy(deep=True)

    _build_payload(tmp_path, market_shadow=market_shadow, pick_history=pick_history)

    pd.testing.assert_frame_equal(market_shadow, before_shadow)
    pd.testing.assert_frame_equal(pick_history, before_pick)
