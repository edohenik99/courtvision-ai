from __future__ import annotations

import json
from pathlib import Path

import pytest

from courtvision.sports.mlb.training.hr_evaluation_contract import (
    ALLOWED_EVALUATION_METRIC_NAMES,
    PREDICTION_PROBABILITY_FIELDS,
)
from courtvision.sports.mlb.training.hr_one_shot_test_evaluator import (
    MLBHROneShotTestEvaluatorError,
)
from courtvision.sports.mlb.training.hr_one_shot_test_executor import (
    MLBHROneShotTestExecutionError,
    execute_one_shot_frozen_mlb_hr_test_evaluation,
    one_shot_test_result_staging_dir,
)
import courtvision.sports.mlb.training.hr_one_shot_test_executor as executor
from courtvision.sports.mlb.training.hr_test_result_artifact import (
    TEST_RESULT_ARTIFACT_FILENAME,
    MLBHRTestResultArtifactError,
    load_mlb_hr_test_result_artifact,
)
from courtvision.sports.mlb.training import hr_validation_metrics
from tests.test_mlb_hr_one_shot_test_evaluator import (
    _build_approved_case,
    _rewrite_receipt,
)
from tests.test_mlb_hr_test_evaluation_access import (
    _rehash_prediction,
    _sha256,
    _write_json,
)


def _execute(paths: dict[str, Path]):
    return execute_one_shot_frozen_mlb_hr_test_evaluation(
        feature_pack_path=paths["feature_pack"],
        temporal_split_plan_path=paths["split_plan"],
        fitted_preprocessing_artifact_path=paths["preprocessing"],
        test_prediction_artifact_path=paths["test_prediction"],
        test_access_approval_receipt_path=paths["approval_receipt"],
    )


def _load_result(paths: dict[str, Path], artifact_path: Path):
    return load_mlb_hr_test_result_artifact(
        artifact_path,
        feature_pack_path=paths["feature_pack"],
        temporal_split_plan_path=paths["split_plan"],
        fitted_preprocessing_artifact_path=paths["preprocessing"],
        test_prediction_artifact_path=paths["test_prediction"],
        test_access_approval_receipt_path=paths["approval_receipt"],
    )


def _unexpected_label_access(*args: object, **kwargs: object) -> None:
    raise AssertionError("test labels opened before all sealed gates passed")


def test_valid_execution_writes_complete_result(tmp_path: Path) -> None:
    paths = _build_approved_case(tmp_path)

    result = _execute(paths)
    loaded = _load_result(paths, result.artifact_path)

    assert result.output_dir == one_shot_test_result_staging_dir(
        paths["approval_receipt"]
    )
    assert result.artifact_path.name == TEST_RESULT_ARTIFACT_FILENAME
    assert loaded.attempt_status == "complete"
    assert loaded.attempt_consumed
    assert loaded.no_rerun
    assert loaded.allowed_metrics == ALLOWED_EVALUATION_METRIC_NAMES
    assert tuple(loaded.metric_results) == ALLOWED_EVALUATION_METRIC_NAMES
    for metric_result in loaded.metric_results.values():
        assert isinstance(metric_result, dict)
        assert tuple(metric_result) == PREDICTION_PROBABILITY_FIELDS
        assert all(isinstance(value, float) for value in metric_result.values())
    assert loaded.research_only
    assert loaded.approval_status == "not_approved"
    assert not loaded.operational_use_enabled


def test_missing_approval_fails_before_label_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _build_approved_case(tmp_path)
    paths["approval_receipt"] = tmp_path / "missing_approval_receipt.json"
    monkeypatch.setattr(executor, "_open_test_metric_inputs", _unexpected_label_access)

    with pytest.raises(
        MLBHROneShotTestEvaluatorError,
        match="approval receipt must be an existing local file",
    ):
        _execute(paths)

    assert not one_shot_test_result_staging_dir(
        paths["approval_receipt"]
    ).exists()


def test_coverage_mismatch_fails_before_label_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _build_approved_case(tmp_path)
    prediction = json.loads(paths["test_prediction"].read_text(encoding="utf-8"))
    prediction["rows"].pop()
    _rehash_prediction(prediction)
    _write_json(paths["test_prediction"], prediction)
    _rewrite_receipt(paths)
    monkeypatch.setattr(executor, "_open_test_metric_inputs", _unexpected_label_access)

    with pytest.raises(
        MLBHROneShotTestEvaluatorError,
        match="missing test predictions=1",
    ):
        _execute(paths)

    assert not one_shot_test_result_staging_dir(
        paths["approval_receipt"]
    ).exists()


def test_metric_failure_consumes_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _build_approved_case(tmp_path)

    def metric_failure(*args: object, **kwargs: object) -> float:
        raise RuntimeError("simulated reviewed metric failure")

    monkeypatch.setattr(hr_validation_metrics, "log_loss", metric_failure)

    with pytest.raises(
        MLBHROneShotTestExecutionError,
        match="metric computation failed",
    ) as captured:
        _execute(paths)

    error = captured.value
    assert error.attempt_consumed
    assert error.attempt_status == "failed"
    assert error.artifact_path is not None
    loaded = _load_result(paths, error.artifact_path)
    assert loaded.attempt_status == "failed"
    assert loaded.attempt_consumed
    assert loaded.metric_results == {
        name: None for name in ALLOWED_EVALUATION_METRIC_NAMES
    }


def test_partial_metric_failure_is_written_as_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _build_approved_case(tmp_path)

    def metric_failure(*args: object, **kwargs: object) -> float:
        raise RuntimeError("failure after the first frozen metric")

    monkeypatch.setattr(hr_validation_metrics, "brier_score", metric_failure)

    with pytest.raises(MLBHROneShotTestExecutionError) as captured:
        _execute(paths)

    error = captured.value
    assert error.attempt_status == "partial"
    assert error.attempt_consumed
    assert error.artifact_path is not None
    loaded = _load_result(paths, error.artifact_path)
    assert loaded.attempt_status == "partial"
    assert loaded.metric_results["log_loss"] is not None
    assert loaded.metric_results["brier_score"] is None


def test_inconclusive_metric_attempt_is_written_as_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _build_approved_case(tmp_path)

    def inconclusive_metric(*args: object, **kwargs: object) -> float:
        raise hr_validation_metrics.MLBHRValidationMetricError(
            "metric is not estimable"
        )

    monkeypatch.setattr(hr_validation_metrics, "roc_auc", inconclusive_metric)

    result = _execute(paths)

    assert result.artifact.attempt_status == "inconclusive"
    assert result.artifact.attempt_consumed
    assert result.artifact.metric_results["log_loss"] is not None
    assert result.artifact.metric_results["brier_score"] is not None
    assert result.artifact.metric_results["roc_auc"] is None


def test_overwrite_fails_before_second_label_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _build_approved_case(tmp_path)
    first = _execute(paths)
    before = first.artifact_path.read_bytes()
    monkeypatch.setattr(executor, "_open_test_metric_inputs", _unexpected_label_access)

    with pytest.raises(
        MLBHRTestResultArtifactError,
        match="already exists and cannot be overwritten",
    ):
        _execute(paths)

    assert first.artifact_path.read_bytes() == before


@pytest.mark.parametrize(
    "forbidden_field",
    ("ev_enabled", "kelly_eligible", "betting_enabled"),
)
def test_forbidden_ev_kelly_or_betting_fields_fail_before_label_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    forbidden_field: str,
) -> None:
    paths = _build_approved_case(tmp_path)
    feature_pack = json.loads(paths["feature_pack"].read_text(encoding="utf-8"))
    feature_pack[forbidden_field] = True
    _write_json(paths["feature_pack"], feature_pack)
    custody = json.loads(paths["label_custody"].read_text(encoding="utf-8"))
    custody["feature_pack_sha256"] = _sha256(paths["feature_pack"])
    _write_json(paths["label_custody"], custody)
    prediction = json.loads(paths["test_prediction"].read_text(encoding="utf-8"))
    prediction["feature_pack_sha256"] = _sha256(paths["feature_pack"])
    _rehash_prediction(prediction)
    _write_json(paths["test_prediction"], prediction)
    _rewrite_receipt(paths)
    monkeypatch.setattr(executor, "_open_test_metric_inputs", _unexpected_label_access)

    with pytest.raises(
        MLBHROneShotTestEvaluatorError,
        match=rf"feature pack\.{forbidden_field} must remain false",
    ):
        _execute(paths)

    assert not one_shot_test_result_staging_dir(
        paths["approval_receipt"]
    ).exists()
