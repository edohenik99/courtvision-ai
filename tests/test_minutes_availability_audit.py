from __future__ import annotations

import json
import warnings
from pathlib import Path

import pandas as pd

from courtvision.reporting.minutes_availability_audit import (
    build_minutes_availability_audit,
    write_minutes_availability_audit,
    _is_low_line_over,
    _line_bucket,
    _minutes_bucket,
    _select_readiness_verdict,
    _volatility_bucket,
)
from courtvision.reporting.quality_summary import write_quality_summary_outputs


def _none_pattern(tmp_path: Path, suffix: str = "csv") -> str:
    return str(tmp_path / f"none_*.{suffix}")


def _build_with_history(tmp_path: Path, rows: list[dict]) -> dict:
    return build_minutes_availability_audit(
        "2026-05-11",
        runtime_root=tmp_path / "runtime",
        history_csv=pd.DataFrame(rows),
        pick_history_csv=pd.DataFrame(),
        full_market_glob=_none_pattern(tmp_path),
        elite_board_glob=_none_pattern(tmp_path),
        player_predictions_glob=_none_pattern(tmp_path),
        grading_glob=_none_pattern(tmp_path),
        player_baselines_csv=pd.DataFrame(),
        inflation_audit_glob=_none_pattern(tmp_path, "json"),
        board_diagnostics_json_glob=_none_pattern(tmp_path, "json"),
        board_diagnostics_csv_glob=_none_pattern(tmp_path),
    )


def test_field_availability_calculation(tmp_path: Path) -> None:
    payload = _build_with_history(
        tmp_path,
        [
            {
                "prediction_date": "2026-05-11",
                "player_name": "Player A",
                "market_type": "player_points",
                "selection": "over",
                "line": 12.5,
                "projected_minutes": 30,
                "minutes_recent": 28,
                "minutes_avg": 26,
                "actual_minutes": 24,
                "minutes_bucket": "24_28",
                "model_projection": 18,
                "actual_value": 10,
                "result_status": "miss",
                "context_caution_level": "low",
            }
        ],
    )

    assert payload["projected_minutes_available_rate"] == 1.0
    assert payload["recent_minutes_available_rate"] == 1.0
    assert payload["average_minutes_available_rate"] == 1.0
    assert payload["actual_minutes_available_rate"] == 1.0
    merged = payload["minutes_field_availability"]["merged"]["fields"]
    assert merged["projected_minutes"]["available_rate"] == 1.0
    assert merged["context"]["available_rate"] == 1.0


def test_minutes_bucket_assignment() -> None:
    assert _minutes_bucket(14.9) == "under_15"
    assert _minutes_bucket(15) == "15_20"
    assert _minutes_bucket(20) == "20_24"
    assert _minutes_bucket(24) == "24_28"
    assert _minutes_bucket(28) == "28_32"
    assert _minutes_bucket(32) == "32_plus"
    assert _minutes_bucket(None) == "unknown"


def test_line_bucket_assignment() -> None:
    assert _line_bucket(8.0) == "below_8.5"
    assert _line_bucket(8.5) == "8.5_14.5"
    assert _line_bucket(14.5) == "8.5_14.5"
    assert _line_bucket(15.0) == "15_20.5"
    assert _line_bucket(21.0) == "21_plus"
    assert _line_bucket(None) == "unknown"


def test_low_line_over_filtering() -> None:
    assert _is_low_line_over({"selection": "over", "line": 14.5}) is True
    assert _is_low_line_over({"selection": "under", "line": 14.5}) is False
    assert _is_low_line_over({"selection": "over", "line": 15.0}) is False


def test_minutes_volatility_bucket_assignment() -> None:
    assert _volatility_bucket(2.9) == "0_3"
    assert _volatility_bucket(-5.9) == "3_6"
    assert _volatility_bucket(9.9) == "6_10"
    assert _volatility_bucket(10) == "10_plus"
    assert _volatility_bucket(None) == "unknown"


def test_actual_minutes_missing_fallback(tmp_path: Path) -> None:
    payload = _build_with_history(
        tmp_path,
        [
            {
                "prediction_date": "2026-05-11",
                "player_name": "Player A",
                "market_type": "player_points",
                "selection": "over",
                "line": 7.5,
                "minutes_recent": 18,
                "minutes_avg": 19,
                "model_projection": 12,
                "actual_value": 6,
                "result_status": "miss",
            }
        ],
    )

    assert payload["actual_minutes_available_rate"] == 0.0
    assert payload["minutes_error_available_rate"] == 0.0
    assert payload["readiness_verdict"] == "PROJECTED_MINUTES_AVAILABLE_ACTUAL_MISSING"
    assert "actual_minutes" in payload["missing_critical_fields"]
    assert "actual minutes are unavailable" in payload["actual_minutes_summary"]["message"]


def test_actual_minutes_present_minutes_error_calculation(tmp_path: Path) -> None:
    payload = _build_with_history(
        tmp_path,
        [
            {
                "prediction_date": "2026-05-11",
                "player_name": "Player A",
                "market_type": "player_points",
                "selection": "over",
                "line": 7.5,
                "projected_minutes": 31,
                "minutes_recent": 21,
                "minutes_avg": 22,
                "actual_minutes": 20,
                "model_projection": 12,
                "actual_value": 6,
                "result_status": "miss",
            }
        ],
    )

    actual = payload["actual_minutes_summary"]
    assert payload["actual_minutes_available_rate"] == 1.0
    assert payload["minutes_error_available_rate"] == 1.0
    assert actual["avg_minutes_error"] == 11.0
    assert actual["overprojected_minutes_rate"] == 1.0
    assert actual["player_points_miss_rate_minutes_error_gt_5"] == 1.0
    assert actual["player_points_miss_rate_minutes_error_gt_10"] == 1.0


def test_sparse_minutes_sources_do_not_emit_future_warnings(tmp_path: Path) -> None:
    prediction_date = "2026-05-11"
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        payload = build_minutes_availability_audit(
            prediction_date,
            runtime_root=tmp_path / "runtime",
            history_csv=pd.DataFrame(
                [
                    {
                        "prediction_date": prediction_date,
                        "player_name": "Projected Player",
                        "market_type": "player_points",
                        "selection": "over",
                        "line": 12.5,
                        "projected_minutes": 30,
                        "result_status": "miss",
                    }
                ]
            ),
            pick_history_csv=pd.DataFrame(
                [
                    {
                        "prediction_date": prediction_date,
                        "player_name": "Sparse Player",
                        "market": "player_points",
                        "selection": "under",
                        "line": 20.5,
                        "result_status": "hit",
                    }
                ]
            ),
            full_market_glob=_none_pattern(tmp_path),
            elite_board_glob=_none_pattern(tmp_path),
            player_predictions_glob=_none_pattern(tmp_path),
            grading_glob=_none_pattern(tmp_path),
            player_baselines_csv=pd.DataFrame([{"player_name": "Baseline Only", "min_avg": 22}]),
            inflation_audit_glob=_none_pattern(tmp_path, "json"),
            board_diagnostics_json_glob=_none_pattern(tmp_path, "json"),
            board_diagnostics_csv_glob=_none_pattern(tmp_path),
        )

    assert payload["total_rows_scanned"] == 3
    assert payload["player_points_rows"] == 2
    assert payload["projected_minutes_available_rate"] == 0.5
    assert payload["actual_minutes_available_rate"] == 0.0
    assert payload["readiness_verdict"] == "PROJECTED_MINUTES_AVAILABLE_ACTUAL_MISSING"


def test_readiness_verdict_selection() -> None:
    assert (
        _select_readiness_verdict(
            player_points_rows=0,
            projected_rate=0,
            recent_rate=0,
            average_rate=0,
            actual_rate=0,
            minutes_error_rate=0,
            low_line_over_misses=0,
            low_line_minutes_summary={},
        )
        == "MINUTES_FIELD_COVERAGE_INSUFFICIENT"
    )
    assert (
        _select_readiness_verdict(
            player_points_rows=3,
            projected_rate=0,
            recent_rate=1,
            average_rate=1,
            actual_rate=0,
            minutes_error_rate=0,
            low_line_over_misses=1,
            low_line_minutes_summary={},
        )
        == "PROJECTED_MINUTES_AVAILABLE_ACTUAL_MISSING"
    )
    assert (
        _select_readiness_verdict(
            player_points_rows=5,
            projected_rate=1,
            recent_rate=1,
            average_rate=1,
            actual_rate=1,
            minutes_error_rate=1,
            low_line_over_misses=3,
            low_line_minutes_summary={"20_24": {"low_line_over_miss_count": 2}},
        )
        == "LOW_MINUTE_OVER_RISK_CONFIRMED"
    )


def test_json_artifact_writing(tmp_path: Path) -> None:
    json_path, _, payload = write_minutes_availability_audit(
        "2026-05-11",
        runtime_root=tmp_path / "runtime",
        history_csv=pd.DataFrame(
            [
                {
                    "prediction_date": "2026-05-11",
                    "player_name": "Player A",
                    "market_type": "player_points",
                    "selection": "over",
                    "line": 12.5,
                    "minutes_avg": 26,
                    "result_status": "miss",
                }
            ]
        ),
        pick_history_csv=pd.DataFrame(),
        full_market_glob=_none_pattern(tmp_path),
        elite_board_glob=_none_pattern(tmp_path),
        player_predictions_glob=_none_pattern(tmp_path),
        grading_glob=_none_pattern(tmp_path),
        player_baselines_csv=pd.DataFrame(),
        inflation_audit_glob=_none_pattern(tmp_path, "json"),
        board_diagnostics_json_glob=_none_pattern(tmp_path, "json"),
        board_diagnostics_csv_glob=_none_pattern(tmp_path),
    )

    assert json_path.exists()
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["note"] == "audit_only_no_live_logic_change"
    assert saved["player_points_rows"] == payload["player_points_rows"]


def test_txt_artifact_writing(tmp_path: Path) -> None:
    _, txt_path, _ = write_minutes_availability_audit(
        "2026-05-11",
        runtime_root=tmp_path / "runtime",
        history_csv=pd.DataFrame(
            [
                {
                    "prediction_date": "2026-05-11",
                    "player_name": "Player A",
                    "market_type": "player_points",
                    "selection": "over",
                    "line": 12.5,
                    "minutes_avg": 26,
                    "result_status": "miss",
                }
            ]
        ),
        pick_history_csv=pd.DataFrame(),
        full_market_glob=_none_pattern(tmp_path),
        elite_board_glob=_none_pattern(tmp_path),
        player_predictions_glob=_none_pattern(tmp_path),
        grading_glob=_none_pattern(tmp_path),
        player_baselines_csv=pd.DataFrame(),
        inflation_audit_glob=_none_pattern(tmp_path, "json"),
        board_diagnostics_json_glob=_none_pattern(tmp_path, "json"),
        board_diagnostics_csv_glob=_none_pattern(tmp_path),
    )

    text = txt_path.read_text(encoding="utf-8")
    assert "MINUTES AVAILABILITY AUDIT" in text
    assert "Do we have actual_minutes?" in text
    assert "Is a live minutes guard justified now?" in text


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
        "player_name": "Player A",
        "market_type": "player_points",
        "selection": "over",
        "line": 7.5,
        "minutes_recent": 19,
        "minutes_avg": 20,
        "model_projection": 12,
        "actual_value": 6,
        "result_status": "miss",
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
    pd.DataFrame([{"player_name": "Player A", "team_abbr": "BOS", "min_avg": 20, "min_recent": 19}]).to_csv(
        model / "player_baselines.csv",
        index=False,
    )
    (research / f"model_metrics_{prediction_date}.json").write_text("{}", encoding="utf-8")
    (diagnostics / f"board_diagnostics_{prediction_date}.json").write_text("{}", encoding="utf-8")
    (operator / f"elite_pipeline_audit_summary_{prediction_date}.json").write_text("{}", encoding="utf-8")

    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        text_path, json_path, payload = write_quality_summary_outputs(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
            out_dir=tmp_path,
            history_root=history_root,
        )

    assert "minutes_availability_audit" in payload
    assert payload["minutes_availability_audit"]["note"] == "audit_only_no_live_logic_change"
    assert Path(payload["minutes_availability_audit"]["json_path"]).exists()
    assert "MINUTES AVAILABILITY AUDIT" in text_path.read_text(encoding="utf-8")
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert "minutes_availability_audit" in saved


def test_no_source_mutation(tmp_path: Path) -> None:
    source = pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-11",
                "player_name": "Player A",
                "market_type": "player_points",
                "selection": "over",
                "line": 12.5,
                "minutes_avg": 26,
                "result_status": "miss",
            }
        ]
    )
    before = source.copy(deep=True)

    _build_with_history(tmp_path, source.to_dict("records"))
    pd.testing.assert_frame_equal(source, before)
