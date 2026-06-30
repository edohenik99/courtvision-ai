"""Reviewed one-shot execution of frozen MLB HR test metrics.

The executor has one narrow authority: after the immutable approval receipt,
frozen prediction artifact, identical pipeline, and exact test population all
pass the existing write-free preflight, it may open the matching test labels,
calculate the five frozen research metrics, and publish one terminal immutable
result.  It has no training, prediction generation, fetching, production, EV,
Kelly, Elite, staking, betting, wager-sizing, or bankroll path.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Final, Mapping, Sequence

from courtvision.sports.mlb.training.hr_evaluation_contract import (
    ALLOWED_EVALUATION_METRIC_NAMES,
    PREDICTION_PROBABILITY_FIELDS,
)
from courtvision.sports.mlb.training.hr_one_shot_test_evaluator import (
    MLBHROneShotTestEvaluationPlan,
    MLBHROneShotTestEvaluatorError,
    plan_one_shot_frozen_mlb_hr_test_evaluation,
)
from courtvision.sports.mlb.training.hr_prediction_artifact import (
    DEFAULT_MODEL_SPECIFICATION_PATH,
    MLBHRFrozenPredictionArtifact,
    MLBHRFrozenPredictionArtifactError,
    load_frozen_prediction_artifact,
)
from courtvision.sports.mlb.training.hr_label_custody import (
    MLBHRLabelCustodyError,
    MLBHRLabelOpeningAuthorization,
    open_mlb_hr_label_custody_split,
)
from courtvision.sports.mlb.training.hr_test_result_artifact import (
    TEST_RESULT_ARTIFACT_FILENAME,
    MLBHRTestResultArtifactError,
    MLBHRTestResultWriteResult,
    validate_mlb_hr_test_result_staging_dir,
    write_mlb_hr_test_result_artifact,
)
from courtvision.sports.mlb.training import hr_validation_metrics
from courtvision.sports.mlb.training.hr_validation_metrics import (
    MLBHRValidationMetricError,
)


ONE_SHOT_TEST_EXECUTOR_VERSION: Final = "mlb-hr-one-shot-test-executor-v1"
ONE_SHOT_RESULT_STAGING_SUFFIX: Final = ".one_shot_test_result"


class MLBHROneShotTestExecutionError(RuntimeError):
    """Raised after an authorized test attempt terminates unsuccessfully."""

    def __init__(
        self,
        message: str,
        *,
        attempt_status: str,
        attempt_consumed: bool,
        artifact_path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.attempt_status = attempt_status
        self.attempt_consumed = attempt_consumed
        self.artifact_path = artifact_path.resolve() if artifact_path else None


class _MetricAttemptError(Exception):
    def __init__(
        self,
        *,
        status: str,
        metric_results: Mapping[str, object],
        cause: Exception,
    ) -> None:
        super().__init__(str(cause))
        self.status = status
        self.metric_results = dict(metric_results)
        self.cause = cause


def one_shot_test_result_staging_dir(
    test_access_approval_receipt_path: str | Path,
) -> Path:
    """Return the sole receipt-bound result directory for this local attempt."""

    receipt_path = Path(test_access_approval_receipt_path).expanduser().resolve()
    return receipt_path.parent / f"{receipt_path.stem}{ONE_SHOT_RESULT_STAGING_SUFFIX}"


def _open_test_metric_inputs(
    *,
    plan: MLBHROneShotTestEvaluationPlan,
    artifact: MLBHRFrozenPredictionArtifact,
) -> tuple[tuple[int, ...], tuple[date, ...], Mapping[str, tuple[float, ...]]]:
    """Open only approved test labels after the label-sealed preflight passes."""

    if artifact.split_id != "test" or not plan.population_coverage.exact_identity_match:
        raise MLBHROneShotTestExecutionError(
            "test labels cannot open without exact frozen test coverage",
            attempt_status="failed",
            attempt_consumed=True,
        )
    authorization = MLBHRLabelOpeningAuthorization(
        split="test",
        reason="approved_one_shot_test_handoff",
        expected_row_ids=tuple(row.row_id for row in artifact.rows),
        frozen_prediction_artifact_sha256=artifact.artifact_sha256,
        approval_receipt_sha256=plan.test_access_approval_receipt_sha256,
    )
    try:
        opened = open_mlb_hr_label_custody_split(
            feature_pack_path=plan.feature_pack_path,
            label_custody_path=plan.label_custody_path,
            temporal_split_plan_path=plan.temporal_split_plan_path,
            authorization=authorization,
        )
    except MLBHRLabelCustodyError as exc:
        raise MLBHROneShotTestExecutionError(
            f"approved test label custody failed: {exc}",
            attempt_status="failed",
            attempt_consumed=True,
        ) from exc
    labels_by_row_id = {row.row_id: row for row in opened}

    labels: list[int] = []
    game_dates: list[date] = []
    probability_series: dict[str, list[float]] = {
        field_name: [] for field_name in PREDICTION_PROBABILITY_FIELDS
    }
    for prediction_row in artifact.rows:
        label_row = labels_by_row_id.get(prediction_row.row_id)
        if (
            label_row is None
            or label_row.game_date != prediction_row.game_date
            or label_row.game_id != prediction_row.game_id
            or label_row.player_id != prediction_row.player_id
        ):
            raise MLBHROneShotTestExecutionError(
                "test label population changed after exact coverage validation",
                attempt_status="failed",
                attempt_consumed=True,
            )
        labels.append(int(label_row.is_home_run))
        game_dates.append(prediction_row.game_date)
        probability_series["raw_home_run_probability"].append(
            prediction_row.raw_home_run_probability
        )
        probability_series["calibrated_home_run_probability"].append(
            prediction_row.calibrated_home_run_probability
        )

    if len(labels) != plan.population_coverage.expected_rows:
        raise MLBHROneShotTestExecutionError(
            "test label population changed after exact coverage validation",
            attempt_status="failed",
            attempt_consumed=True,
        )
    return (
        tuple(labels),
        tuple(game_dates),
        {name: tuple(values) for name, values in probability_series.items()},
    )


def _empty_metric_results() -> dict[str, object]:
    return {name: None for name in ALLOWED_EVALUATION_METRIC_NAMES}


def _calculate_allowed_metrics(
    *,
    labels: Sequence[int],
    probability_series: Mapping[str, Sequence[float]],
) -> dict[str, object]:
    """Calculate only the frozen metric names for both frozen probabilities."""

    results = _empty_metric_results()
    completed_metric_count = 0
    for metric_name in ALLOWED_EVALUATION_METRIC_NAMES:
        metric_function = getattr(hr_validation_metrics, metric_name)
        series_results: dict[str, float] = {}
        for series_name in PREDICTION_PROBABILITY_FIELDS:
            try:
                series_results[series_name] = metric_function(
                    labels,
                    probability_series[series_name],
                )
            except MLBHRValidationMetricError as exc:
                raise _MetricAttemptError(
                    status="inconclusive",
                    metric_results=results,
                    cause=exc,
                ) from exc
            except Exception as exc:
                raise _MetricAttemptError(
                    status="partial" if completed_metric_count else "failed",
                    metric_results=results,
                    cause=exc,
                ) from exc
        results[metric_name] = series_results
        completed_metric_count += 1
    return results


def _write_attempt(
    *,
    plan: MLBHROneShotTestEvaluationPlan,
    output_staging_dir: Path,
    attempt_status: str,
    metric_results: Mapping[str, object],
    model_specification_path: str | Path,
) -> MLBHRTestResultWriteResult:
    return write_mlb_hr_test_result_artifact(
        feature_pack_path=plan.feature_pack_path,
        temporal_split_plan_path=plan.temporal_split_plan_path,
        fitted_preprocessing_artifact_path=plan.fitted_preprocessing_artifact_path,
        test_prediction_artifact_path=plan.test_prediction_artifact_path,
        test_access_approval_receipt_path=(
            plan.test_access_approval_receipt_path
        ),
        output_staging_dir=output_staging_dir,
        attempt_status=attempt_status,
        metric_results=metric_results,
        model_specification_path=model_specification_path,
    )


def _capture_failed_attempt(
    *,
    plan: MLBHROneShotTestEvaluationPlan,
    output_staging_dir: Path,
    attempt_status: str,
    metric_results: Mapping[str, object],
    cause: Exception,
    model_specification_path: str | Path,
) -> MLBHRTestResultWriteResult:
    try:
        return _write_attempt(
            plan=plan,
            output_staging_dir=output_staging_dir,
            attempt_status=attempt_status,
            metric_results=metric_results,
            model_specification_path=model_specification_path,
        )
    except Exception as write_exc:
        artifact_path = output_staging_dir / TEST_RESULT_ARTIFACT_FILENAME
        raise MLBHROneShotTestExecutionError(
            "the authorized one-shot attempt was consumed, but its terminal "
            f"result could not be published: {write_exc}",
            attempt_status=attempt_status,
            attempt_consumed=True,
            artifact_path=artifact_path if artifact_path.is_file() else None,
        ) from cause


def execute_one_shot_frozen_mlb_hr_test_evaluation(
    *,
    feature_pack_path: str | Path,
    label_custody_path: str | Path | None = None,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
    test_prediction_artifact_path: str | Path,
    test_access_approval_receipt_path: str | Path,
    model_specification_path: str | Path = DEFAULT_MODEL_SPECIFICATION_PATH,
) -> MLBHRTestResultWriteResult:
    """Consume the approved attempt and write its sole terminal test result."""

    # This entire preflight is label-sealed and write-free.  Any approval,
    # prediction, pipeline, coverage, or research-gate failure exits here.
    plan = plan_one_shot_frozen_mlb_hr_test_evaluation(
        feature_pack_path=feature_pack_path,
        label_custody_path=label_custody_path,
        temporal_split_plan_path=temporal_split_plan_path,
        fitted_preprocessing_artifact_path=fitted_preprocessing_artifact_path,
        test_prediction_artifact_path=test_prediction_artifact_path,
        test_access_approval_receipt_path=test_access_approval_receipt_path,
        model_specification_path=model_specification_path,
    )
    try:
        artifact = load_frozen_prediction_artifact(
            plan.test_prediction_artifact_path,
            feature_pack_path=plan.feature_pack_path,
            temporal_split_plan_path=plan.temporal_split_plan_path,
            fitted_preprocessing_artifact_path=(
                plan.fitted_preprocessing_artifact_path
            ),
            model_specification_path=model_specification_path,
        )
    except MLBHRFrozenPredictionArtifactError as exc:
        raise MLBHROneShotTestEvaluatorError(
            f"frozen test prediction artifact gate failed: {exc}"
        ) from exc
    if artifact.artifact_sha256 != plan.test_prediction_artifact_sha256:
        raise MLBHROneShotTestEvaluatorError(
            "test prediction artifact changed after approved preflight"
        )

    output_staging_dir = one_shot_test_result_staging_dir(
        plan.test_access_approval_receipt_path
    )
    validate_mlb_hr_test_result_staging_dir(output_staging_dir)
    if output_staging_dir.exists():
        raise MLBHRTestResultArtifactError(
            "one-shot test-result attempt already exists and cannot be "
            f"overwritten: {output_staging_dir}"
        )
    try:
        # Atomic directory creation is the local one-shot claim.  It happens
        # after every label-sealed gate and immediately before label access.
        output_staging_dir.mkdir()
    except FileExistsError as exc:
        raise MLBHRTestResultArtifactError(
            "one-shot test-result attempt already exists and cannot be "
            f"overwritten: {output_staging_dir}"
        ) from exc
    except OSError as exc:
        raise MLBHRTestResultArtifactError(
            f"could not claim one-shot test-result attempt: {output_staging_dir}: {exc}"
        ) from exc

    try:
        labels, _game_dates, probability_series = _open_test_metric_inputs(
            plan=plan,
            artifact=artifact,
        )
        metric_results = _calculate_allowed_metrics(
            labels=labels,
            probability_series=probability_series,
        )
    except _MetricAttemptError as exc:
        result = _capture_failed_attempt(
            plan=plan,
            output_staging_dir=output_staging_dir,
            attempt_status=exc.status,
            metric_results=exc.metric_results,
            cause=exc.cause,
            model_specification_path=model_specification_path,
        )
        if exc.status == "inconclusive":
            return result
        raise MLBHROneShotTestExecutionError(
            f"one-shot test metric computation {exc.status}: {exc.cause}",
            attempt_status=exc.status,
            attempt_consumed=True,
            artifact_path=result.artifact_path,
        ) from exc.cause
    except Exception as exc:
        result = _capture_failed_attempt(
            plan=plan,
            output_staging_dir=output_staging_dir,
            attempt_status="failed",
            metric_results=_empty_metric_results(),
            cause=exc,
            model_specification_path=model_specification_path,
        )
        raise MLBHROneShotTestExecutionError(
            f"one-shot test label handoff failed: {exc}",
            attempt_status="failed",
            attempt_consumed=True,
            artifact_path=result.artifact_path,
        ) from exc

    try:
        return _write_attempt(
            plan=plan,
            output_staging_dir=output_staging_dir,
            attempt_status="complete",
            metric_results=metric_results,
            model_specification_path=model_specification_path,
        )
    except Exception as exc:
        result = _capture_failed_attempt(
            plan=plan,
            output_staging_dir=output_staging_dir,
            attempt_status="failed",
            metric_results=_empty_metric_results(),
            cause=exc,
            model_specification_path=model_specification_path,
        )
        raise MLBHROneShotTestExecutionError(
            f"one-shot test result publication failed: {exc}",
            attempt_status="failed",
            attempt_consumed=True,
            artifact_path=result.artifact_path,
        ) from exc


__all__ = [
    "MLBHROneShotTestExecutionError",
    "ONE_SHOT_RESULT_STAGING_SUFFIX",
    "ONE_SHOT_TEST_EXECUTOR_VERSION",
    "execute_one_shot_frozen_mlb_hr_test_evaluation",
    "one_shot_test_result_staging_dir",
]
