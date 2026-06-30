from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import pytest

from courtvision.sports.mlb.training.hr_evaluation_contract import (
    ALLOWED_EVALUATION_METRIC_NAMES,
)
import courtvision.sports.mlb.training.hr_evaluation_contract as evaluation_contract
from courtvision.sports.mlb.training.hr_prediction_artifact import (
    IMMUTABLE_WRITE_POLICY,
)
from courtvision.sports.mlb.training.hr_test_result_artifact import (
    TEST_RESULT_ARTIFACT_FILENAME,
    TEST_RESULT_ARTIFACT_SCHEMA_VERSION,
    MLBHRTestResultArtifactError,
    load_mlb_hr_test_result_artifact,
    test_result_artifact_sha256 as _result_artifact_sha256,
    write_mlb_hr_test_result_artifact,
)
import courtvision.sports.mlb.training.hr_validation_metrics as validation_metrics
from tests.test_mlb_hr_one_shot_test_evaluator import _build_approved_case
from tests.test_mlb_hr_test_evaluation_access import _write_json


def _empty_metric_results() -> dict[str, object]:
    return {name: None for name in ALLOWED_EVALUATION_METRIC_NAMES}


def _write(
    paths: Mapping[str, Path],
    output_staging_dir: Path,
    *,
    metric_results: Mapping[str, object] | None = None,
):
    return write_mlb_hr_test_result_artifact(
        feature_pack_path=paths["feature_pack"],
        temporal_split_plan_path=paths["split_plan"],
        fitted_preprocessing_artifact_path=paths["preprocessing"],
        test_prediction_artifact_path=paths["test_prediction"],
        test_access_approval_receipt_path=paths["approval_receipt"],
        output_staging_dir=output_staging_dir,
        attempt_status="failed",
        metric_results=metric_results or _empty_metric_results(),
    )


def _load(paths: Mapping[str, Path], artifact_path: Path):
    return load_mlb_hr_test_result_artifact(
        artifact_path,
        feature_pack_path=paths["feature_pack"],
        temporal_split_plan_path=paths["split_plan"],
        fitted_preprocessing_artifact_path=paths["preprocessing"],
        test_prediction_artifact_path=paths["test_prediction"],
        test_access_approval_receipt_path=paths["approval_receipt"],
    )


def test_valid_result_artifact_writes_and_loads_once(tmp_path: Path) -> None:
    paths = _build_approved_case(tmp_path)
    staging = tmp_path / "approved_research_staging"

    result = _write(paths, staging)
    loaded = _load(paths, result.artifact_path)

    assert result.artifact_path == staging / TEST_RESULT_ARTIFACT_FILENAME
    assert loaded.schema_version == TEST_RESULT_ARTIFACT_SCHEMA_VERSION
    assert loaded.attempt_status == "failed"
    assert loaded.attempt_consumed
    assert loaded.no_rerun
    assert loaded.allowed_metrics == ALLOWED_EVALUATION_METRIC_NAMES
    assert loaded.metric_results == _empty_metric_results()
    assert loaded.research_only
    assert loaded.approval_status == "not_approved"
    assert not loaded.operational_use_enabled
    assert loaded.immutable
    assert loaded.write_policy == IMMUTABLE_WRITE_POLICY
    assert [path.name for path in staging.iterdir()] == [
        TEST_RESULT_ARTIFACT_FILENAME
    ]


def test_result_artifact_overwrite_fails(tmp_path: Path) -> None:
    paths = _build_approved_case(tmp_path)
    staging = tmp_path / "approved_research_staging"
    first = _write(paths, staging)
    before = first.artifact_path.read_bytes()

    with pytest.raises(
        MLBHRTestResultArtifactError,
        match="already exists and cannot be overwritten",
    ):
        _write(paths, staging)

    assert first.artifact_path.read_bytes() == before


def test_result_artifact_hash_mismatch_fails(tmp_path: Path) -> None:
    paths = _build_approved_case(tmp_path)
    artifact_path = _write(
        paths, tmp_path / "approved_research_staging"
    ).artifact_path
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["feature_pack_sha256"] = "0" * 64
    payload["artifact_sha256"] = _result_artifact_sha256(payload)
    _write_json(artifact_path, payload)

    with pytest.raises(
        MLBHRTestResultArtifactError,
        match="input hash mismatch: feature_pack_sha256",
    ):
        _load(paths, artifact_path)


def test_result_artifact_forbidden_production_gate_fails(
    tmp_path: Path,
) -> None:
    paths = _build_approved_case(tmp_path)
    artifact_path = _write(
        paths, tmp_path / "approved_research_staging"
    ).artifact_path
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["production_approved"] = False
    payload["artifact_sha256"] = _result_artifact_sha256(payload)
    _write_json(artifact_path, payload)

    with pytest.raises(
        MLBHRTestResultArtifactError,
        match="prohibited production field",
    ):
        _load(paths, artifact_path)


@pytest.mark.parametrize("forbidden_name", ("ev", "kelly", "betting"))
def test_result_writer_rejects_forbidden_metric_fields(
    tmp_path: Path,
    forbidden_name: str,
) -> None:
    paths = _build_approved_case(tmp_path)
    metric_results = _empty_metric_results()
    metric_results["log_loss"] = {forbidden_name: False}

    with pytest.raises(
        MLBHRTestResultArtifactError,
        match=rf"prohibited (EV|Kelly|betting/wagering) field",
    ):
        _write(
            paths,
            tmp_path / f"approved_{forbidden_name}_staging",
            metric_results=metric_results,
        )


def test_result_writer_rejects_metric_outside_frozen_allowlist(
    tmp_path: Path,
) -> None:
    paths = _build_approved_case(tmp_path)
    metric_results = _empty_metric_results()
    metric_results["roi"] = None

    with pytest.raises(
        MLBHRTestResultArtifactError,
        match="allowed metrics only.*forbidden=roi",
    ):
        _write(
            paths,
            tmp_path / "approved_research_staging",
            metric_results=metric_results,
        )


@pytest.mark.parametrize("folder", ("outputs", "manual", "cache"))
def test_result_artifact_operational_manual_or_cache_path_fails(
    tmp_path: Path,
    folder: str,
) -> None:
    paths = _build_approved_case(tmp_path)
    prohibited_parent = tmp_path / folder
    prohibited_parent.mkdir()

    with pytest.raises(
        MLBHRTestResultArtifactError,
        match="cannot use operational, manual, or cache paths",
    ):
        _write(paths, prohibited_parent / "research_staging")


def test_result_writer_does_not_execute_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _build_approved_case(tmp_path)

    def unexpected_metric_execution(*args: object, **kwargs: object) -> None:
        raise AssertionError("metric execution is outside the result writer")

    for name in (
        "log_loss",
        "brier_score",
        "roc_auc",
        "pr_auc",
        "calibration_error",
        "compute_binary_metrics",
        "paired_game_date_bootstrap",
    ):
        monkeypatch.setattr(validation_metrics, name, unexpected_metric_execution)
    monkeypatch.setattr(
        evaluation_contract,
        "evaluate_frozen_mlb_hr_validation",
        unexpected_metric_execution,
    )

    result = _write(paths, tmp_path / "approved_research_staging")

    assert result.artifact.metric_results == _empty_metric_results()
