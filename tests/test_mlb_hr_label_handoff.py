from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from courtvision.sports.mlb.data.historical_backtest_readiness import (
    HistoricalBacktestReadinessVerdict,
)
from courtvision.sports.mlb.data.historical_feature_pack import (
    HISTORICAL_FEATURE_PACK_VERSION,
)
from courtvision.sports.mlb.training.hr_label_handoff import (
    EVALUATION_ONLY,
    FITTING_ONLY,
    MLBHRLabelAccessRequest,
    MLBHRLabelHandoffError,
    validate_mlb_hr_label_handoff,
)
from courtvision.sports.mlb.training.hr_label_custody import (
    LABEL_CUSTODY_FILENAME,
    MLBHRLabelOpeningAuthorization,
    build_label_custody_payload,
)
from courtvision.sports.mlb.training.hr_preprocessing_artifact import (
    write_fitted_preprocessing_artifact,
)
from courtvision.sports.mlb.training.hr_preprocessing_plan import (
    TEMPORAL_SPLIT_ARTIFACT_VERSION,
)
from courtvision.sports.mlb.training.hr_backtest_runner import (
    plan_sealed_mlb_hr_research_backtest,
)


FIRST_DATE = date(2024, 4, 1)
GAME_DATES = tuple(FIRST_DATE + timedelta(days=index) for index in range(30))
WINDOW_DATES = {
    "train": GAME_DATES[:18],
    "validation": GAME_DATES[18:24],
    "test": GAME_DATES[24:],
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    rows: list[dict[str, object]] = []
    labels: list[dict[str, object]] = []
    for split_name in ("train", "validation", "test"):
        for index, game_date in enumerate(WINDOW_DATES[split_name]):
            date_text = game_date.isoformat()
            cutoff = f"{date_text}T20:00:00+00:00"
            row = {
                    "row_id": f"{split_name}-{index:02d}",
                    "game_id": f"game-{split_name}-{index:02d}",
                    "game_date": date_text,
                    "player_id": f"player-{index:02d}",
                    "player_name": f"Player {index:02d}",
                    "odds_collected_at": cutoff,
                    "event_start_time": f"{date_text}T23:00:00+00:00",
                    "feature_values": {
                        "weather_temperature": 70.0 + index,
                        "hr_market_available": True,
                    },
                    "feature_availability": [
                        {
                            "feature_name": "weather_temperature",
                            "available_at": cutoff,
                            "source_latest_game_date": None,
                        },
                        {
                            "feature_name": "hr_market_available",
                            "available_at": cutoff,
                            "source_latest_game_date": None,
                        },
                    ],
                }
            rows.append(row)
            labels.append(
                {"row_id": row["row_id"], "is_home_run": index % 2 == 0}
            )

    feature_pack = tmp_path / "feature_pack.json"
    feature_pack.write_text(
        json.dumps(
            {
                "schema_version": HISTORICAL_FEATURE_PACK_VERSION,
                "mode": "historical_research",
                "readiness_verdict": (
                    HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
                ),
                "feature_names": ["weather_temperature", "hr_market_available"],
                "rows": rows,
                "feature_firewall_valid": True,
                "approval_status": "not_approved",
                "model_training_enabled": False,
                "backtesting_enabled": False,
                "predictions_enabled": False,
                "eligible_for_betting": False,
                "ev_enabled": False,
                "kelly_eligible": False,
                "elite_enabled": False,
                "staking_enabled": False,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    label_custody = tmp_path / LABEL_CUSTODY_FILENAME
    label_custody.write_text(
        json.dumps(
            build_label_custody_payload(
                feature_payload=json.loads(feature_pack.read_text(encoding="utf-8")),
                feature_pack_sha256=_sha256(feature_pack),
                labels=labels,
                created_at="2026-06-30T00:00:00+00:00",
            )
        ),
        encoding="utf-8",
    )

    temporal_plan = tmp_path / "temporal_plan.json"
    temporal_plan.write_text(
        json.dumps(
            {
                "schema_version": TEMPORAL_SPLIT_ARTIFACT_VERSION,
                "mode": "historical_research",
                "feature_pack_sha256": _sha256(feature_pack),
                "pack_dir": str(tmp_path / "source_pack"),
                "split_method": "whole_unique_game_dates_60_20_20",
                "readiness_verdict": (
                    HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
                ),
                "approval_status": "not_approved",
                "model_training_enabled": False,
                "backtesting_enabled": False,
                "predictions_enabled": False,
                "eligible_for_betting": False,
                "ev_enabled": False,
                "kelly_eligible": False,
                "elite_enabled": False,
                "staking_enabled": False,
                "train": {
                    "game_dates": [value.isoformat() for value in WINDOW_DATES["train"]]
                },
                "validation": {
                    "game_dates": [
                        value.isoformat() for value in WINDOW_DATES["validation"]
                    ]
                },
                "test": {
                    "game_dates": [value.isoformat() for value in WINDOW_DATES["test"]]
                },
            }
        ),
        encoding="utf-8",
    )
    fitted = write_fitted_preprocessing_artifact(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        output_staging_dir=tmp_path / "fitted_staging",
    ).artifact_path
    return feature_pack, label_custody, temporal_plan, fitted


def _validate(paths: tuple[Path, Path, Path, Path], **kwargs: object):
    feature_pack, label_custody, temporal_plan, fitted = paths
    return validate_mlb_hr_label_handoff(
        feature_pack_path=feature_pack,
        label_custody_path=label_custody,
        temporal_split_plan_path=temporal_plan,
        fitted_preprocessing_artifact_path=fitted,
        **kwargs,
    )


def test_valid_label_handoff_passes(tmp_path: Path) -> None:
    allowed_access = (
        MLBHRLabelAccessRequest(
            phase="baseline_fit",
            split="train",
            purpose="fitting",
        ),
        MLBHRLabelAccessRequest(
            phase="train_only_calibration_fit",
            split="train",
            purpose="fitting",
        ),
        MLBHRLabelAccessRequest(
            phase="validation_evaluation_after_predictions_frozen",
            split="validation",
            purpose="evaluation",
            predictions_frozen=True,
        ),
        MLBHRLabelAccessRequest(
            phase="test_evaluation_after_predictions_frozen",
            split="test",
            purpose="evaluation",
            predictions_frozen=True,
        ),
    )
    paths = _build_inputs(tmp_path)
    custody_payload = json.loads(paths[1].read_text(encoding="utf-8"))
    row_ids = [row["row_id"] for row in custody_payload["rows"]]
    authorizations = (
        MLBHRLabelOpeningAuthorization(
            split="train",
            reason="train_fitting",
            expected_row_ids=tuple(row_ids[:18]),
        ),
        MLBHRLabelOpeningAuthorization(
            split="validation",
            reason="frozen_prediction_validation",
            expected_row_ids=tuple(row_ids[18:24]),
            frozen_prediction_artifact_sha256="0" * 64,
        ),
        MLBHRLabelOpeningAuthorization(
            split="test",
            reason="approved_one_shot_test_handoff",
            expected_row_ids=tuple(row_ids[24:]),
            frozen_prediction_artifact_sha256="1" * 64,
            approval_receipt_sha256="2" * 64,
        ),
    )
    report = _validate(
        paths,
        access_requests=allowed_access,
        distribution_splits=("train", "validation", "test"),
        opening_authorizations=authorizations,
    )

    assert [
        (
            item.split,
            item.row_count,
            item.positive_count,
            item.negative_count,
        )
        for item in report.distributions
    ] == [
        ("train", 18, 9, 9),
        ("validation", 6, 3, 3),
        ("test", 6, 3, 3),
    ]
    phases = {phase.name: phase for phase in report.phases}
    assert phases["baseline_fit"].train == FITTING_ONLY
    assert (
        phases["validation_evaluation_after_predictions_frozen"].validation
        == EVALUATION_ONLY
    )
    assert phases["validation_prediction"].validation == "sealed"
    assert not report.label_values_exposed
    assert report.requested_access_count == 4
    assert not report.model_training_enabled
    assert not report.predictions_enabled


def test_runner_validates_custody_without_opening_window_labels(tmp_path: Path) -> None:
    feature_pack, label_custody, temporal_plan, fitted = _build_inputs(tmp_path)

    plan = plan_sealed_mlb_hr_research_backtest(
        feature_pack_path=feature_pack,
        label_custody_path=label_custody,
        temporal_split_plan_path=temporal_plan,
        fitted_preprocessing_artifact_path=fitted,
    )

    assert all(window.positive_labels is None for window in plan.windows)
    assert all(window.negative_labels is None for window in plan.windows)
    assert plan.label_access_scope == "evaluation_planning_only"


def test_missing_labels_fail(tmp_path: Path) -> None:
    paths = _build_inputs(tmp_path)
    payload = json.loads(paths[1].read_text(encoding="utf-8"))
    payload["rows"].pop(0)
    paths[1].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MLBHRLabelHandoffError, match="missing label rows=1"):
        _validate(paths)


def test_non_binary_labels_fail(tmp_path: Path) -> None:
    paths = _build_inputs(tmp_path)
    payload = json.loads(paths[1].read_text(encoding="utf-8"))
    payload["rows"][0]["is_home_run"] = 1
    paths[1].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MLBHRLabelHandoffError, match="boolean true/false"):
        _validate(
            paths,
            access_requests=(
                MLBHRLabelAccessRequest("baseline_fit", "train", "fitting"),
            ),
            distribution_splits=("train",),
            opening_authorizations=(
                MLBHRLabelOpeningAuthorization(
                    split="train",
                    reason="train_fitting",
                    expected_row_ids=tuple(
                        row["row_id"] for row in payload["rows"][:18]
                    ),
                ),
            ),
        )


@pytest.mark.parametrize(
    "access_request",
    (
        MLBHRLabelAccessRequest(
            phase="baseline_fit",
            split="validation",
            purpose="fitting",
        ),
        MLBHRLabelAccessRequest(
            phase="test_evaluation_after_predictions_frozen",
            split="test",
            purpose="evaluation",
            predictions_frozen=False,
        ),
    ),
)
def test_validation_or_test_label_misuse_fails(
    tmp_path: Path,
    access_request: MLBHRLabelAccessRequest,
) -> None:
    with pytest.raises(MLBHRLabelHandoffError, match="label access request rejected"):
        _validate(_build_inputs(tmp_path), access_requests=(access_request,))


def test_label_as_feature_fails(tmp_path: Path) -> None:
    paths = _build_inputs(tmp_path)
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    payload["feature_names"].append("is_home_run")
    paths[0].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MLBHRLabelHandoffError, match="contains outcome labels"):
        _validate(paths)


def test_non_research_gate_fails(tmp_path: Path) -> None:
    paths = _build_inputs(tmp_path)
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    payload["eligible_for_betting"] = True
    paths[0].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MLBHRLabelHandoffError, match="non-research gates"):
        _validate(paths)
