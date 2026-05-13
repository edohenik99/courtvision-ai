from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.minutes_error_shadow_audit import (
    build_minutes_error_shadow_audit,
    select_readiness_verdict,
    write_minutes_error_shadow_audit,
)
from courtvision.reporting.quality_summary import write_quality_summary_outputs


def _none_pattern(tmp_path: Path) -> str:
    return str(tmp_path / "none_*.csv")


def _history_rows(*, include_minutes: bool = False) -> list[dict]:
    rows = [
        {
            "prediction_date": "2026-05-11",
            "player_id": 101,
            "player_name": "Player A",
            "game_id": 9001,
            "market": "player_points",
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
            "market": "player_points",
            "selection": "over",
            "line": 8.5,
            "result_status": "hit",
            "actual_value": 14,
        },
    ]
    if include_minutes:
        rows[0]["projected_minutes"] = 28
        rows[0]["actual_minutes"] = 20
        rows[1]["projected_minutes"] = 22
        rows[1]["actual_minutes"] = 23
    return rows


def _build_payload(
    tmp_path: Path,
    *,
    pick_history: pd.DataFrame | None = None,
    market_shadow: pd.DataFrame | None = None,
    player_baselines: pd.DataFrame | None = None,
) -> dict:
    return build_minutes_error_shadow_audit(
        "2026-05-11",
        runtime_root=tmp_path / "runtime",
        pick_history=pick_history if pick_history is not None else pd.DataFrame(_history_rows()),
        market_shadow_history=market_shadow if market_shadow is not None else pd.DataFrame(),
        player_baselines=player_baselines if player_baselines is not None else pd.DataFrame(),
        full_market_glob=_none_pattern(tmp_path),
    )


def test_audit_builds_with_no_minutes_fields(tmp_path: Path) -> None:
    payload = _build_payload(tmp_path)

    assert payload["total_player_points_rows"] == 2
    assert payload["low_line_over_rows"] == 2
    assert payload["low_line_over_misses"] == 1
    assert payload["minutes_fields_availability"]["minutes_basis"] == 0.0
    assert payload["rows_without_reliable_minutes_count"] == 2
    assert payload["readiness_verdict"] == "MINUTES_FIELDS_UNAVAILABLE"


def test_audit_builds_with_minutes_avg_only(tmp_path: Path) -> None:
    payload = _build_payload(
        tmp_path,
        player_baselines=pd.DataFrame(
            [
                {"player_id": 101, "player_name": "Player A", "min_avg": 18},
                {"player_id": 102, "player_name": "Player B", "min_avg": 24},
            ]
        ),
    )

    assert payload["minutes_fields_availability"]["minutes_avg"] == 1.0
    assert payload["minutes_fields_availability"]["minutes_basis"] == 1.0
    assert payload["avg_minutes_basis_by_result"]["miss"] == 18.0
    assert payload["avg_minutes_basis_by_result"]["hit"] == 24.0
    assert payload["rows_without_reliable_minutes_count"] == 0
    assert payload["readiness_verdict"] == "ACTUAL_MINUTES_UNAVAILABLE_MINUTES_BASIS_READY"


def test_audit_separates_low_line_over_hits_and_misses(tmp_path: Path) -> None:
    payload = _build_payload(tmp_path, pick_history=pd.DataFrame(_history_rows(include_minutes=True)))

    summary = payload["low_line_over_result_summary"]
    assert summary["miss"]["rows"] == 1
    assert summary["hit"]["rows"] == 1
    assert payload["avg_projected_minutes_by_result"]["miss"] == 28.0
    assert payload["avg_projected_minutes_by_result"]["hit"] == 22.0
    assert summary["miss"]["avg_minutes_shortfall"] == 8.0
    assert summary["hit"]["avg_minutes_shortfall"] == -1.0
    assert payload["minutes_shortfall_buckets"]["shortfall_6_10"]["misses"] == 1
    assert payload["readiness_verdict"] == "MINUTES_SHORTFALL_SIGNAL_PRESENT"


def test_readiness_verdict_selection() -> None:
    assert (
        select_readiness_verdict(
            total_player_points_rows=0,
            low_line_over_rows=0,
            minutes_basis_available_rate=0,
            actual_minutes_available_rate=0,
            low_line_over_misses=0,
            low_line_over_result_summary={},
        )
        == "NO_PLAYER_POINTS_HISTORY"
    )
    assert (
        select_readiness_verdict(
            total_player_points_rows=2,
            low_line_over_rows=0,
            minutes_basis_available_rate=1,
            actual_minutes_available_rate=0,
            low_line_over_misses=0,
            low_line_over_result_summary={},
        )
        == "LOW_LINE_OVER_SAMPLE_MISSING"
    )


def test_json_and_text_artifacts(tmp_path: Path) -> None:
    json_path, txt_path, payload = write_minutes_error_shadow_audit(
        "2026-05-11",
        runtime_root=tmp_path / "runtime",
        pick_history=pd.DataFrame(_history_rows(include_minutes=True)),
        market_shadow_history=pd.DataFrame(),
        player_baselines=pd.DataFrame(),
        full_market_glob=_none_pattern(tmp_path),
    )

    assert json_path.exists()
    assert txt_path.exists()
    assert payload["note"] == "audit_only_no_prediction_grading_kelly_or_history_change"
    assert "MINUTES-ERROR SHADOW AUDIT" in txt_path.read_text(encoding="utf-8")
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["readiness_verdict"] == payload["readiness_verdict"]


def test_audit_does_not_mutate_history_inputs(tmp_path: Path) -> None:
    pick_history = pd.DataFrame(_history_rows(include_minutes=True))
    market_shadow = pd.DataFrame(_history_rows())
    before_pick = pick_history.copy(deep=True)
    before_shadow = market_shadow.copy(deep=True)

    _build_payload(tmp_path, pick_history=pick_history, market_shadow=market_shadow)

    pd.testing.assert_frame_equal(pick_history, before_pick)
    pd.testing.assert_frame_equal(market_shadow, before_shadow)


def test_quality_summary_includes_phase_15c(tmp_path: Path) -> None:
    prediction_date = "2026-05-11"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    research = runtime_root / "research"
    diagnostics = runtime_root / "diagnostics"
    model = tmp_path / "model"
    for directory in (operator, research, diagnostics, history_root, model):
        directory.mkdir(parents=True, exist_ok=True)

    row = _history_rows(include_minutes=True)[0] | {
        "market_type": "player_points",
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

    pick_before = (history_root / "pick_history.csv").read_bytes()
    text_path, json_path, payload = write_quality_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        history_root=history_root,
    )

    assert (history_root / "pick_history.csv").read_bytes() == pick_before
    assert "minutes_error_shadow_audit" in payload
    audit = payload["minutes_error_shadow_audit"]
    assert audit["note"] == "audit_only_no_prediction_grading_kelly_or_history_change"
    assert Path(audit["json_path"]).exists()
    assert "MINUTES-ERROR SHADOW AUDIT (Phase 15C -- AUDIT ONLY)" in text_path.read_text(encoding="utf-8")
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert "minutes_error_shadow_audit" in saved
