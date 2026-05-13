from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.low_line_over_minutes_guard_review import (
    build_low_line_over_minutes_guard_review,
    select_readiness_verdict,
    write_low_line_over_minutes_guard_review,
)
from courtvision.reporting.quality_summary import write_quality_summary_outputs


def _none_pattern(tmp_path: Path) -> str:
    return str(tmp_path / "none_*.csv")


def _review_rows() -> list[dict]:
    return [
        {
            "prediction_date": "2026-05-12",
            "player_id": 101,
            "player_name": "Weak Player",
            "game_id": 9001,
            "market_type": "player_points",
            "selection": "over",
            "line": 12.5,
            "minutes_basis": 27.5,
            "minutes_recent": 27,
            "minutes_avg": 28,
            "edge": 1.2,
            "confidence": 0.71,
            "quality_score": 56,
            "result_status": "miss",
        },
        {
            "prediction_date": "2026-05-12",
            "player_id": 102,
            "player_name": "Borderline Player",
            "game_id": 9002,
            "market_type": "player_points",
            "selection": "over",
            "line": 14.5,
            "minutes_basis": 29.0,
            "minutes_recent": 29,
            "minutes_avg": 29.5,
            "edge": 0.8,
            "confidence": 0.68,
            "quality_score": 52,
            "result_status": "hit",
        },
        {
            "prediction_date": "2026-05-12",
            "player_id": 103,
            "player_name": "Stable Player",
            "game_id": 9003,
            "market_type": "player_points",
            "selection": "over",
            "line": 10.5,
            "minutes_basis": 31.0,
            "minutes_recent": 31,
            "minutes_avg": 30.5,
            "edge": 1.0,
            "confidence": 0.74,
            "quality_score": 58,
            "result_status": "hit",
        },
        {
            "prediction_date": "2026-05-12",
            "player_id": 104,
            "player_name": "Not Low Line",
            "game_id": 9004,
            "market_type": "player_points",
            "selection": "over",
            "line": 16.5,
            "minutes_basis": 24.0,
            "result_status": "miss",
        },
    ]


def _build_payload(tmp_path: Path, rows: list[dict] | None = None) -> dict:
    return build_low_line_over_minutes_guard_review(
        "2026-05-12",
        runtime_root=tmp_path / "runtime",
        pick_history=pd.DataFrame(rows if rows is not None else _review_rows()),
        market_shadow_history=pd.DataFrame(),
        player_baselines=pd.DataFrame(),
        full_market_glob=_none_pattern(tmp_path),
    )


def test_weak_minutes_rows_are_flagged(tmp_path: Path) -> None:
    payload = _build_payload(tmp_path)
    review_df = payload["review_df"]

    weak = review_df[review_df["player_name"].eq("Weak Player")].iloc[0]
    assert weak["minutes_guard_review_bucket"] == "weak_minutes_basis"
    assert bool(weak["minutes_guard_review_required"]) is True
    assert weak["minutes_guard_reason"] == "low_line_over_weak_minutes_basis"
    assert payload["weak_minutes_basis_count"] == 1
    assert payload["bucket_summary"]["weak_minutes_basis"]["misses"] == 1


def test_borderline_minutes_rows_are_flagged_separately(tmp_path: Path) -> None:
    payload = _build_payload(tmp_path)
    review_df = payload["review_df"]

    borderline = review_df[review_df["player_name"].eq("Borderline Player")].iloc[0]
    assert borderline["minutes_guard_review_bucket"] == "borderline_minutes_basis"
    assert bool(borderline["minutes_guard_review_required"]) is True
    assert borderline["minutes_guard_reason"] == "low_line_over_borderline_minutes_basis"
    assert payload["borderline_minutes_basis_count"] == 1
    assert payload["bucket_summary"]["borderline_minutes_basis"]["hits"] == 1


def test_stable_minutes_rows_are_not_marked_weak(tmp_path: Path) -> None:
    payload = _build_payload(tmp_path)
    review_df = payload["review_df"]

    stable = review_df[review_df["player_name"].eq("Stable Player")].iloc[0]
    assert stable["minutes_guard_review_bucket"] == "stable_minutes_basis"
    assert bool(stable["minutes_guard_review_required"]) is False
    assert stable["minutes_guard_reason"] == ""
    assert payload["stable_minutes_basis_count"] == 1
    assert all(row["player_name"] != "Stable Player" for row in payload["top_flagged_rows"])


def test_writer_outputs_json_text_and_csv(tmp_path: Path) -> None:
    json_path, txt_path, csv_path, payload = write_low_line_over_minutes_guard_review(
        "2026-05-12",
        runtime_root=tmp_path / "runtime",
        pick_history=pd.DataFrame(_review_rows()),
        market_shadow_history=pd.DataFrame(),
        player_baselines=pd.DataFrame(),
        full_market_glob=_none_pattern(tmp_path),
    )

    assert json_path.exists()
    assert txt_path.exists()
    assert csv_path.exists()
    assert payload["review_required_count"] == 2
    assert "LOW-LINE OVER MINUTES GUARD REVIEW" in txt_path.read_text(encoding="utf-8")
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["readiness_verdict"] == "REVIEW_READY_WEAK_MINUTES_PRESENT"
    csv_df = pd.read_csv(csv_path)
    assert set(csv_df["minutes_guard_review_bucket"]) == {
        "weak_minutes_basis",
        "borderline_minutes_basis",
        "stable_minutes_basis",
    }


def test_no_prediction_grading_kelly_or_history_mutation(tmp_path: Path) -> None:
    pick_history = pd.DataFrame(_review_rows())
    market_shadow = pd.DataFrame(_review_rows())
    before_pick = pick_history.copy(deep=True)
    before_shadow = market_shadow.copy(deep=True)

    _build_payload(tmp_path, rows=pick_history.to_dict("records"))
    build_low_line_over_minutes_guard_review(
        "2026-05-12",
        runtime_root=tmp_path / "runtime",
        pick_history=pick_history,
        market_shadow_history=market_shadow,
        player_baselines=pd.DataFrame(),
        full_market_glob=_none_pattern(tmp_path),
    )

    pd.testing.assert_frame_equal(pick_history, before_pick)
    pd.testing.assert_frame_equal(market_shadow, before_shadow)


def test_readiness_verdict_selection() -> None:
    assert (
        select_readiness_verdict(
            total_low_line_over_rows=0,
            weak_count=0,
            borderline_count=0,
            missing_minutes_basis_count=0,
        )
        == "NO_LOW_LINE_OVER_ROWS"
    )
    assert (
        select_readiness_verdict(
            total_low_line_over_rows=3,
            weak_count=0,
            borderline_count=2,
            missing_minutes_basis_count=0,
        )
        == "REVIEW_READY_BORDERLINE_MINUTES_PRESENT"
    )


def test_quality_summary_includes_phase_15d(tmp_path: Path) -> None:
    prediction_date = "2026-05-12"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    research = runtime_root / "research"
    diagnostics = runtime_root / "diagnostics"
    model = tmp_path / "model"
    for directory in (operator, research, diagnostics, history_root, model):
        directory.mkdir(parents=True, exist_ok=True)

    row = _review_rows()[0]
    pd.DataFrame([row]).to_csv(operator / f"elite_board_{prediction_date}.csv", index=False)
    pd.DataFrame([row]).to_csv(operator / f"full_market_board_{prediction_date}.csv", index=False)
    pd.DataFrame([row]).to_csv(research / f"player_predictions_{prediction_date}.csv", index=False)
    pd.DataFrame([row]).to_csv(history_root / "market_shadow_history.csv", index=False)
    pd.DataFrame([row]).to_csv(history_root / "pick_history.csv", index=False)
    pd.DataFrame([]).to_csv(operator / f"kelly_stakes_{prediction_date}.csv", index=False)
    pd.DataFrame([]).to_csv(operator / f"sgp_board_{prediction_date}.csv", index=False)
    pd.DataFrame([{"player_id": 101, "player_name": "Weak Player", "team_abbr": "BOS", "min_avg": 27}]).to_csv(
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
    assert "low_line_over_minutes_guard_review" in payload
    review = payload["low_line_over_minutes_guard_review"]
    assert review["note"] == "review_only_no_prediction_grading_kelly_or_history_change"
    assert review["weak_minutes_basis_count"] == 1
    assert Path(review["json_path"]).exists()
    assert Path(review["csv_path"]).exists()
    assert "LOW-LINE OVER MINUTES GUARD REVIEW (Phase 15D -- REVIEW ONLY)" in text_path.read_text(encoding="utf-8")
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert "low_line_over_minutes_guard_review" in saved
