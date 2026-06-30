"""Read-only acceptance audit for MLB HR validation promotion evidence.

The only positive verdict from this module is promotion to a human test
review.  It does not open labels, run an evaluator, train or refit anything,
generate predictions, write artifacts, or authorize test/production use.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Final, Mapping

from courtvision.sports.mlb.training.hr_evaluation_contract import (
    ALLOWED_EVALUATION_METRIC_NAMES,
    EVALUATOR_CONTRACT_VERSION,
    REQUIRED_BASELINE_COMPARISONS,
    VALIDATION_EVALUATOR_VERSION,
)
from courtvision.sports.mlb.training.hr_prediction_artifact import (
    DEFAULT_MODEL_SPECIFICATION_PATH,
    MLBHRFrozenPredictionArtifact,
    MLBHRFrozenPredictionArtifactError,
    load_frozen_prediction_artifact,
)
from courtvision.sports.mlb.training.hr_validation_metrics import (
    DEFAULT_BOOTSTRAP_CONFIDENCE_LEVEL,
    DEFAULT_BOOTSTRAP_MINIMUM_SUCCESSFUL_REPLICATES,
    DEFAULT_BOOTSTRAP_REPLICATES,
    DEFAULT_BOOTSTRAP_SEED,
)


VALIDATION_ACCEPTANCE_POLICY_VERSION: Final = (
    "mlb-hr-validation-acceptance-and-promotion-v1"
)
VALIDATION_PROMOTION_EVIDENCE_SCHEMA_VERSION: Final = (
    "mlb-hr-validation-promotion-evidence-v1"
)
PROMOTE_TO_TEST_REVIEW: Final = "PROMOTE_TO_TEST_REVIEW"
DO_NOT_PROMOTE: Final = "DO_NOT_PROMOTE"

MINIMUM_CALIBRATED_ROC_AUC: Final = 0.55
MINIMUM_CALIBRATED_ROC_AUC_LOWER_BOUND: Final = 0.50
MAXIMUM_CALIBRATED_CALIBRATION_ERROR: Final = 0.05
MAXIMUM_CALIBRATED_CALIBRATION_ERROR_UPPER_BOUND: Final = 0.075

_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_MODEL_SERIES: Final = (
    "raw_home_run_probability",
    "calibrated_home_run_probability",
)
_BASELINE_POPULATIONS: Final = {
    baseline.name: baseline.population for baseline in REQUIRED_BASELINE_COMPARISONS
}
_ROOT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "acceptance_policy_version",
        "evaluator_contract_version",
        "validation_evaluator_version",
        "split_id",
        "research_only",
        "approval_status",
        "production_approved",
        "operational_use_enabled",
        "feature_pack_sha256",
        "temporal_split_plan_sha256",
        "fitted_preprocessing_artifact_sha256",
        "prediction_artifact_sha256",
        "prediction_file_sha256",
        "model_specification_sha256",
        "code_version_sha256",
        "pipeline_sha256",
        "evaluation_attempt",
        "label_access",
        "bootstrap",
        "population",
        "calibration",
        "metrics",
        "baseline_comparisons",
        "validation_result_sha256",
    }
)
_EVALUATION_ATTEMPT_FIELDS: Final = frozenset(
    {
        "attempt_id",
        "attempt_number",
        "prediction_frozen_before_validation_labels",
        "metrics_predeclared",
        "baselines_predeclared",
        "all_required_results_reported",
        "rerun_after_validation_label_access",
        "prediction_regenerated_after_validation_labels",
        "post_label_model_or_metric_selection",
    }
)
_LABEL_ACCESS_FIELDS: Final = frozenset(
    {
        "validation_labels_opened",
        "validation_evaluation_only",
        "test_labels_opened",
        "test_labels_sealed",
        "test_metrics_computed",
    }
)
_BOOTSTRAP_FIELDS: Final = frozenset(
    {
        "unit",
        "method",
        "confidence_level",
        "requested_replicates",
        "minimum_successful_replicates",
        "seed",
        "deterministic",
    }
)
_POPULATION_FIELDS: Final = frozenset(
    {
        "row_count",
        "positive_count",
        "negative_count",
        "unique_game_date_count",
        "market_covered_count",
        "market_missing_count",
    }
)
_CALIBRATION_FIELDS: Final = frozenset(
    {
        "method",
        "fit_split",
        "train_time_ordered_out_of_fold_scores",
        "frozen_before_validation_labels",
        "validation_refit_performed",
        "test_refit_performed",
        "selection_using_validation_labels",
        "reliability_bin_policy_frozen_before_validation_labels",
    }
)
_INTERVAL_FIELDS: Final = frozenset(
    {
        "estimate",
        "lower_bound",
        "upper_bound",
        "confidence_level",
        "requested_replicates",
        "successful_replicates",
        "seed",
        "status",
    }
)
_BASELINE_FIELDS: Final = frozenset(
    {"population", "row_count", "source_sha256", "metrics"}
)
_BASELINE_INTERVAL_FIELDS: Final = frozenset(
    {
        "model_estimate",
        "baseline_estimate",
        "improvement_estimate",
        "lower_bound",
        "upper_bound",
        "confidence_level",
        "requested_replicates",
        "successful_replicates",
        "seed",
        "status",
    }
)
_SIGNIFICANT_IMPROVEMENT_REQUIREMENTS: Final = frozenset(
    {
        ("train_prevalence_constant", "log_loss"),
        ("train_prevalence_constant", "brier_score"),
        ("train_prevalence_constant", "pr_auc"),
        ("raw_implied_probability", "log_loss"),
        ("no_market_logistic_ablation", "log_loss"),
    }
)


class MLBHRValidationPromotionAuditError(ValueError):
    """Raised when promotion evidence cannot be read or canonicalized."""


@dataclass(frozen=True, slots=True)
class MLBHRValidationPromotionDecision:
    """One write-free, research-only promotion-audit verdict."""

    verdict: str
    failures: tuple[str, ...]
    pipeline_sha256: str | None
    validation_result_sha256: str | None
    test_labels_sealed: bool
    writes_performed: bool = False
    test_label_access_authorized: bool = False
    test_evaluation_authorized: bool = False
    production_approved: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "failures", tuple(self.failures))
        if (
            self.verdict not in {PROMOTE_TO_TEST_REVIEW, DO_NOT_PROMOTE}
            or (self.verdict == PROMOTE_TO_TEST_REVIEW and self.failures)
            or (self.verdict == DO_NOT_PROMOTE and not self.failures)
            or self.writes_performed
            or self.test_label_access_authorized
            or self.test_evaluation_authorized
            or self.production_approved
        ):
            raise MLBHRValidationPromotionAuditError(
                "invalid validation promotion decision"
            )


def _file_sha256(path: Path, label: str) -> str:
    if not path.is_file():
        raise MLBHRValidationPromotionAuditError(
            f"{label} must be an existing local file: {path}"
        )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise MLBHRValidationPromotionAuditError(
            f"could not hash {label} {path}: {exc}"
        ) from exc
    return digest.hexdigest()


def _canonical_sha256(payload: Mapping[str, object]) -> str:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MLBHRValidationPromotionAuditError(
            f"validation evidence cannot be canonicalized: {exc}"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def validation_result_sha256(payload: Mapping[str, object]) -> str:
    """Return the canonical evidence hash, excluding its self-hash field."""

    content = dict(payload)
    content.pop("validation_result_sha256", None)
    return _canonical_sha256(content)


def pipeline_sha256(artifact: MLBHRFrozenPredictionArtifact) -> str:
    """Fingerprint the components that must remain unchanged for test review.

    The split-specific prediction content is bound separately and intentionally
    excluded: a future test prediction has different rows, while the feature
    pack, split plan, preprocessing, model specification, and code must match.
    """

    return _canonical_sha256(
        {
            "feature_pack_sha256": artifact.feature_pack_sha256,
            "temporal_split_plan_sha256": artifact.temporal_split_plan_sha256,
            "fitted_preprocessing_artifact_sha256": (
                artifact.fitted_preprocessing_artifact_sha256
            ),
            "model_specification_id": artifact.model_specification_id,
            "model_specification_sha256": artifact.model_specification_sha256,
            "code_version": artifact.code_version,
            "code_version_sha256": artifact.code_version_sha256,
        }
    )


def _reject_nonstandard_number(value: str) -> object:
    raise ValueError(f"non-standard JSON number is prohibited: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is prohibited: {key}")
        result[key] = value
    return result


def _load_results(
    validation_results: Mapping[str, object] | str | Path,
) -> tuple[Mapping[str, object], Path | None, str | None]:
    if isinstance(validation_results, Mapping):
        try:
            snapshot = json.loads(
                json.dumps(
                    validation_results,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MLBHRValidationPromotionAuditError(
                f"in-memory validation metric results must be JSON-safe: {exc}"
            ) from exc
        if not isinstance(snapshot, Mapping):
            raise MLBHRValidationPromotionAuditError(
                "validation metric results must contain an object"
            )
        return snapshot, None, None
    source = Path(validation_results).expanduser().resolve()
    initial_hash = _file_sha256(source, "validation metric results")
    try:
        payload = json.loads(
            source.read_text(encoding="utf-8"),
            parse_constant=_reject_nonstandard_number,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise MLBHRValidationPromotionAuditError(
            f"could not read validation metric results {source}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise MLBHRValidationPromotionAuditError(
            "validation metric results must contain a JSON object"
        )
    return payload, source, initial_hash


def _mapping(
    value: object,
    *,
    fields: frozenset[str],
    location: str,
    failures: list[str],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        failures.append(f"{location} must be an object")
        return {}
    non_text = [key for key in value if not isinstance(key, str)]
    if non_text:
        failures.append(f"{location} keys must be text")
        return {}
    keys = set(value)
    missing = sorted(fields - keys)
    extras = sorted(keys - fields)
    if missing:
        failures.append(f"{location} missing fields: {', '.join(missing)}")
    if extras:
        failures.append(f"{location} unsupported fields: {', '.join(extras)}")
    return value


def _finite_number(
    value: object, location: str, failures: list[str]
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        failures.append(f"{location} must be a finite number")
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        failures.append(f"{location} must be a finite number")
        return None
    return parsed


def _positive_int(
    value: object, location: str, failures: list[str], *, allow_zero: bool = False
) -> int | None:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "non-negative" if allow_zero else "positive"
        failures.append(f"{location} must be a {qualifier} integer")
        return None
    return value


def _sha256_value(value: object, location: str, failures: list[str]) -> str | None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        failures.append(f"{location} must be lowercase SHA-256")
        return None
    return value


def _validate_interval(
    value: object,
    *,
    location: str,
    failures: list[str],
    estimate_field: str,
) -> tuple[float | None, float | None, float | None]:
    fields = (
        _INTERVAL_FIELDS
        if estimate_field == "estimate"
        else _BASELINE_INTERVAL_FIELDS
    )
    record = _mapping(value, fields=fields, location=location, failures=failures)
    estimate = _finite_number(
        record.get(estimate_field), f"{location}.{estimate_field}", failures
    )
    lower = _finite_number(
        record.get("lower_bound"), f"{location}.lower_bound", failures
    )
    upper = _finite_number(
        record.get("upper_bound"), f"{location}.upper_bound", failures
    )
    confidence = _finite_number(
        record.get("confidence_level"), f"{location}.confidence_level", failures
    )
    requested = _positive_int(
        record.get("requested_replicates"),
        f"{location}.requested_replicates",
        failures,
    )
    successful = _positive_int(
        record.get("successful_replicates"),
        f"{location}.successful_replicates",
        failures,
    )
    seed = record.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        failures.append(f"{location}.seed must be an integer")
    if record.get("status") != "estimated":
        failures.append(f"{location}.status must be estimated")
    if confidence != DEFAULT_BOOTSTRAP_CONFIDENCE_LEVEL:
        failures.append(f"{location}.confidence_level must be 0.95")
    if requested != DEFAULT_BOOTSTRAP_REPLICATES:
        failures.append(f"{location}.requested_replicates must be 2000")
    if successful is not None and (
        successful < DEFAULT_BOOTSTRAP_MINIMUM_SUCCESSFUL_REPLICATES
        or (requested is not None and successful > requested)
    ):
        failures.append(
            f"{location}.successful_replicates must be between 1900 and 2000"
        )
    if seed != DEFAULT_BOOTSTRAP_SEED:
        failures.append(f"{location}.seed must be 20260629")
    if lower is not None and upper is not None and lower > upper:
        failures.append(f"{location} confidence interval bounds are reversed")
    return estimate, lower, upper


def _validate_fixed_contracts(
    results: Mapping[str, object], failures: list[str]
) -> None:
    expected = {
        "schema_version": VALIDATION_PROMOTION_EVIDENCE_SCHEMA_VERSION,
        "acceptance_policy_version": VALIDATION_ACCEPTANCE_POLICY_VERSION,
        "evaluator_contract_version": EVALUATOR_CONTRACT_VERSION,
        "validation_evaluator_version": VALIDATION_EVALUATOR_VERSION,
        "split_id": "validation",
        "research_only": True,
        "approval_status": "not_approved",
        "production_approved": False,
        "operational_use_enabled": False,
    }
    for field_name, expected_value in expected.items():
        if results.get(field_name) != expected_value:
            failures.append(
                f"{field_name} must be {expected_value!r} for validation-only review"
            )


def _validate_attempt(results: Mapping[str, object], failures: list[str]) -> None:
    attempt = _mapping(
        results.get("evaluation_attempt"),
        fields=_EVALUATION_ATTEMPT_FIELDS,
        location="evaluation_attempt",
        failures=failures,
    )
    if not isinstance(attempt.get("attempt_id"), str) or not str(
        attempt.get("attempt_id", "")
    ).strip():
        failures.append("evaluation_attempt.attempt_id must be non-empty text")
    if attempt.get("attempt_number") != 1:
        failures.append("evaluation_attempt.attempt_number must be 1; reruns fail")
    for field_name in (
        "prediction_frozen_before_validation_labels",
        "metrics_predeclared",
        "baselines_predeclared",
        "all_required_results_reported",
    ):
        if attempt.get(field_name) is not True:
            failures.append(f"evaluation_attempt.{field_name} must be true")
    for field_name in (
        "rerun_after_validation_label_access",
        "prediction_regenerated_after_validation_labels",
        "post_label_model_or_metric_selection",
    ):
        if attempt.get(field_name) is not False:
            failures.append(f"evaluation_attempt.{field_name} must be false")


def _validate_label_access(
    results: Mapping[str, object], failures: list[str]
) -> bool:
    access = _mapping(
        results.get("label_access"),
        fields=_LABEL_ACCESS_FIELDS,
        location="label_access",
        failures=failures,
    )
    required_true = (
        "validation_labels_opened",
        "validation_evaluation_only",
        "test_labels_sealed",
    )
    required_false = ("test_labels_opened", "test_metrics_computed")
    for field_name in required_true:
        if access.get(field_name) is not True:
            failures.append(f"label_access.{field_name} must be true")
    for field_name in required_false:
        if access.get(field_name) is not False:
            failures.append(f"label_access.{field_name} must be false")
    return (
        access.get("test_labels_sealed") is True
        and access.get("test_labels_opened") is False
        and access.get("test_metrics_computed") is False
    )


def _validate_bootstrap(results: Mapping[str, object], failures: list[str]) -> None:
    bootstrap = _mapping(
        results.get("bootstrap"),
        fields=_BOOTSTRAP_FIELDS,
        location="bootstrap",
        failures=failures,
    )
    expected = {
        "unit": "game_date_block",
        "method": "paired_percentile_bootstrap",
        "confidence_level": DEFAULT_BOOTSTRAP_CONFIDENCE_LEVEL,
        "requested_replicates": DEFAULT_BOOTSTRAP_REPLICATES,
        "minimum_successful_replicates": (
            DEFAULT_BOOTSTRAP_MINIMUM_SUCCESSFUL_REPLICATES
        ),
        "seed": DEFAULT_BOOTSTRAP_SEED,
        "deterministic": True,
    }
    for field_name, expected_value in expected.items():
        if bootstrap.get(field_name) != expected_value:
            failures.append(f"bootstrap.{field_name} must be {expected_value!r}")


def _validate_population(
    results: Mapping[str, object], failures: list[str]
) -> tuple[int | None, int | None]:
    population = _mapping(
        results.get("population"),
        fields=_POPULATION_FIELDS,
        location="population",
        failures=failures,
    )
    row_count = _positive_int(
        population.get("row_count"), "population.row_count", failures
    )
    positive = _positive_int(
        population.get("positive_count"),
        "population.positive_count",
        failures,
    )
    negative = _positive_int(
        population.get("negative_count"),
        "population.negative_count",
        failures,
    )
    _positive_int(
        population.get("unique_game_date_count"),
        "population.unique_game_date_count",
        failures,
    )
    covered = _positive_int(
        population.get("market_covered_count"),
        "population.market_covered_count",
        failures,
    )
    missing = _positive_int(
        population.get("market_missing_count"),
        "population.market_missing_count",
        failures,
        allow_zero=True,
    )
    if (
        row_count is not None
        and positive is not None
        and negative is not None
        and positive + negative != row_count
    ):
        failures.append("population positive and negative counts must equal row_count")
    if (
        row_count is not None
        and covered is not None
        and missing is not None
        and covered + missing != row_count
    ):
        failures.append("population market coverage counts must equal row_count")
    return row_count, covered


def _validate_calibration(results: Mapping[str, object], failures: list[str]) -> None:
    calibration = _mapping(
        results.get("calibration"),
        fields=_CALIBRATION_FIELDS,
        location="calibration",
        failures=failures,
    )
    expected = {
        "method": "platt_sigmoid",
        "fit_split": "train",
        "train_time_ordered_out_of_fold_scores": True,
        "frozen_before_validation_labels": True,
        "validation_refit_performed": False,
        "test_refit_performed": False,
        "selection_using_validation_labels": False,
        "reliability_bin_policy_frozen_before_validation_labels": True,
    }
    for field_name, expected_value in expected.items():
        if calibration.get(field_name) != expected_value:
            failures.append(f"calibration.{field_name} must be {expected_value!r}")


def _validate_model_metrics(
    results: Mapping[str, object], failures: list[str]
) -> dict[str, dict[str, tuple[float | None, float | None, float | None]]]:
    metrics = _mapping(
        results.get("metrics"),
        fields=frozenset(_MODEL_SERIES),
        location="metrics",
        failures=failures,
    )
    parsed: dict[
        str, dict[str, tuple[float | None, float | None, float | None]]
    ] = {}
    metric_fields = frozenset(ALLOWED_EVALUATION_METRIC_NAMES)
    for series_name in _MODEL_SERIES:
        series = _mapping(
            metrics.get(series_name),
            fields=metric_fields,
            location=f"metrics.{series_name}",
            failures=failures,
        )
        parsed[series_name] = {}
        for metric_name in ALLOWED_EVALUATION_METRIC_NAMES:
            parsed[series_name][metric_name] = _validate_interval(
                series.get(metric_name),
                location=f"metrics.{series_name}.{metric_name}",
                failures=failures,
                estimate_field="estimate",
            )
    return parsed


def _validate_metric_thresholds(
    parsed: Mapping[
        str, Mapping[str, tuple[float | None, float | None, float | None]]
    ],
    failures: list[str],
) -> None:
    raw = parsed.get("raw_home_run_probability", {})
    calibrated = parsed.get("calibrated_home_run_probability", {})
    roc_estimate, roc_lower, _ = calibrated.get("roc_auc", (None, None, None))
    if roc_estimate is not None and roc_estimate < MINIMUM_CALIBRATED_ROC_AUC:
        failures.append("calibrated ROC-AUC must be at least 0.55")
    if roc_lower is not None and roc_lower <= MINIMUM_CALIBRATED_ROC_AUC_LOWER_BOUND:
        failures.append("calibrated ROC-AUC 95% lower bound must exceed 0.50")

    calibration_estimate, _, calibration_upper = calibrated.get(
        "calibration_error", (None, None, None)
    )
    if (
        calibration_estimate is not None
        and calibration_estimate > MAXIMUM_CALIBRATED_CALIBRATION_ERROR
    ):
        failures.append("calibrated calibration error must be at most 0.05")
    if (
        calibration_upper is not None
        and calibration_upper > MAXIMUM_CALIBRATED_CALIBRATION_ERROR_UPPER_BOUND
    ):
        failures.append(
            "calibrated calibration-error 95% upper bound must be at most 0.075"
        )

    for metric_name in ("log_loss", "brier_score", "calibration_error"):
        raw_estimate = raw.get(metric_name, (None, None, None))[0]
        calibrated_estimate = calibrated.get(metric_name, (None, None, None))[0]
        if (
            raw_estimate is not None
            and calibrated_estimate is not None
            and calibrated_estimate > raw_estimate
        ):
            failures.append(
                f"calibration may not worsen validation {metric_name} versus raw"
            )
    for metric_name in ("roc_auc", "pr_auc"):
        raw_estimate = raw.get(metric_name, (None, None, None))[0]
        calibrated_estimate = calibrated.get(metric_name, (None, None, None))[0]
        if (
            raw_estimate is not None
            and calibrated_estimate is not None
            and not math.isclose(
                raw_estimate, calibrated_estimate, rel_tol=0.0, abs_tol=1e-12
            )
        ):
            failures.append(
                f"Platt calibration must preserve validation {metric_name} ranking"
            )


def _validate_baselines(
    results: Mapping[str, object],
    *,
    row_count: int | None,
    market_covered_count: int | None,
    feature_pack_sha256: str,
    calibrated_metrics: Mapping[
        str, tuple[float | None, float | None, float | None]
    ],
    failures: list[str],
) -> None:
    baseline_names = frozenset(_BASELINE_POPULATIONS)
    comparisons = _mapping(
        results.get("baseline_comparisons"),
        fields=baseline_names,
        location="baseline_comparisons",
        failures=failures,
    )
    metric_fields = frozenset(ALLOWED_EVALUATION_METRIC_NAMES)
    for baseline_name, expected_population in _BASELINE_POPULATIONS.items():
        location = f"baseline_comparisons.{baseline_name}"
        comparison = _mapping(
            comparisons.get(baseline_name),
            fields=_BASELINE_FIELDS,
            location=location,
            failures=failures,
        )
        if comparison.get("population") != expected_population:
            failures.append(
                f"{location}.population must be {expected_population!r}"
            )
        comparison_rows = _positive_int(
            comparison.get("row_count"), f"{location}.row_count", failures
        )
        expected_rows = (
            market_covered_count
            if baseline_name == "raw_implied_probability"
            else row_count
        )
        if (
            comparison_rows is not None
            and expected_rows is not None
            and comparison_rows != expected_rows
        ):
            failures.append(f"{location}.row_count does not match its population")
        source_hash = _sha256_value(
            comparison.get("source_sha256"), f"{location}.source_sha256", failures
        )
        if (
            baseline_name == "raw_implied_probability"
            and source_hash is not None
            and source_hash != feature_pack_sha256
        ):
            failures.append(
                f"{location}.source_sha256 must match the frozen feature pack"
            )
        metric_results = _mapping(
            comparison.get("metrics"),
            fields=metric_fields,
            location=f"{location}.metrics",
            failures=failures,
        )
        for metric_name in ALLOWED_EVALUATION_METRIC_NAMES:
            metric_location = f"{location}.metrics.{metric_name}"
            metric_record = metric_results.get(metric_name)
            improvement, lower, _ = _validate_interval(
                metric_record,
                location=metric_location,
                failures=failures,
                estimate_field="improvement_estimate",
            )
            record = metric_record if isinstance(metric_record, Mapping) else {}
            model_estimate = _finite_number(
                record.get("model_estimate"),
                f"{metric_location}.model_estimate",
                failures,
            )
            baseline_estimate = _finite_number(
                record.get("baseline_estimate"),
                f"{metric_location}.baseline_estimate",
                failures,
            )
            if model_estimate is not None and baseline_estimate is not None:
                if metric_name in {"log_loss", "brier_score", "calibration_error"}:
                    expected_improvement = baseline_estimate - model_estimate
                else:
                    expected_improvement = model_estimate - baseline_estimate
                if improvement is not None and not math.isclose(
                    improvement,
                    expected_improvement,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                ):
                    failures.append(
                        f"{metric_location}.improvement_estimate does not match "
                        "the baseline/model estimates"
                    )
            if baseline_name != "raw_implied_probability":
                full_population_estimate = calibrated_metrics.get(
                    metric_name, (None, None, None)
                )[0]
                if (
                    model_estimate is not None
                    and full_population_estimate is not None
                    and not math.isclose(
                        model_estimate,
                        full_population_estimate,
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                ):
                    failures.append(
                        f"{metric_location}.model_estimate does not match the "
                        "full calibrated validation metric"
                    )
            if improvement is not None and improvement < 0.0:
                failures.append(
                    f"{metric_location}.improvement_estimate may not be negative"
                )
            if (
                (baseline_name, metric_name)
                in _SIGNIFICANT_IMPROVEMENT_REQUIREMENTS
                and lower is not None
                and lower <= 0.0
            ):
                failures.append(
                    f"{metric_location} 95% lower bound must exceed zero"
                )


def _decision(
    failures: list[str],
    *,
    pipeline_hash: str | None,
    result_hash: str | None,
    test_labels_sealed: bool,
) -> MLBHRValidationPromotionDecision:
    unique_failures = tuple(dict.fromkeys(failures))
    return MLBHRValidationPromotionDecision(
        verdict=PROMOTE_TO_TEST_REVIEW if not unique_failures else DO_NOT_PROMOTE,
        failures=unique_failures,
        pipeline_sha256=pipeline_hash,
        validation_result_sha256=result_hash,
        test_labels_sealed=test_labels_sealed,
    )


def audit_mlb_hr_validation_promotion(
    *,
    feature_pack_path: str | Path,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
    prediction_artifact_path: str | Path,
    validation_results: Mapping[str, object] | str | Path,
    model_specification_path: str | Path = DEFAULT_MODEL_SPECIFICATION_PATH,
) -> MLBHRValidationPromotionDecision:
    """Audit validation evidence without opening labels or writing anything."""

    feature_source = Path(feature_pack_path).expanduser().resolve()
    split_source = Path(temporal_split_plan_path).expanduser().resolve()
    preprocessing_source = (
        Path(fitted_preprocessing_artifact_path).expanduser().resolve()
    )
    prediction_source = Path(prediction_artifact_path).expanduser().resolve()
    model_specification_source = (
        Path(model_specification_path).expanduser().resolve()
    )
    sources = (
        (feature_source, "feature pack"),
        (split_source, "temporal split plan"),
        (preprocessing_source, "fitted preprocessing artifact"),
        (prediction_source, "prediction artifact"),
        (model_specification_source, "model specification"),
    )
    try:
        initial_hashes = tuple(_file_sha256(path, label) for path, label in sources)
        artifact = load_frozen_prediction_artifact(
            prediction_source,
            feature_pack_path=feature_source,
            temporal_split_plan_path=split_source,
            fitted_preprocessing_artifact_path=preprocessing_source,
            model_specification_path=model_specification_source,
        )
        results, results_source, results_file_hash = _load_results(validation_results)
    except (
        MLBHRValidationPromotionAuditError,
        MLBHRFrozenPredictionArtifactError,
    ) as exc:
        return _decision(
            [str(exc)],
            pipeline_hash=None,
            result_hash=None,
            test_labels_sealed=False,
        )

    failures: list[str] = []
    root = _mapping(
        results,
        fields=_ROOT_FIELDS,
        location="validation_results",
        failures=failures,
    )
    if artifact.split_id != "validation":
        failures.append("prediction artifact must be validation-only")
    _validate_fixed_contracts(root, failures)
    _validate_attempt(root, failures)
    test_labels_sealed = _validate_label_access(root, failures)
    _validate_bootstrap(root, failures)
    row_count, market_covered_count = _validate_population(root, failures)
    if row_count is not None and row_count != len(artifact.rows):
        failures.append(
            "population.row_count does not match the frozen validation predictions"
        )
    _validate_calibration(root, failures)

    evidence_hashes = {
        "feature_pack_sha256": initial_hashes[0],
        "temporal_split_plan_sha256": initial_hashes[1],
        "fitted_preprocessing_artifact_sha256": initial_hashes[2],
        "prediction_file_sha256": initial_hashes[3],
        "prediction_artifact_sha256": artifact.artifact_sha256,
        "model_specification_sha256": artifact.model_specification_sha256,
        "code_version_sha256": artifact.code_version_sha256,
    }
    for field_name, expected_hash in evidence_hashes.items():
        recorded_hash = _sha256_value(root.get(field_name), field_name, failures)
        if recorded_hash is not None and recorded_hash != expected_hash:
            failures.append(f"{field_name} does not match the frozen artifact")

    expected_pipeline_hash = pipeline_sha256(artifact)
    recorded_pipeline_hash = _sha256_value(
        root.get("pipeline_sha256"), "pipeline_sha256", failures
    )
    if (
        recorded_pipeline_hash is not None
        and recorded_pipeline_hash != expected_pipeline_hash
    ):
        failures.append("pipeline_sha256 does not match the unchanged pipeline")

    recorded_result_hash = _sha256_value(
        root.get("validation_result_sha256"),
        "validation_result_sha256",
        failures,
    )
    try:
        expected_result_hash = validation_result_sha256(root)
    except MLBHRValidationPromotionAuditError as exc:
        failures.append(str(exc))
        expected_result_hash = None
    if (
        recorded_result_hash is not None
        and expected_result_hash is not None
        and recorded_result_hash != expected_result_hash
    ):
        failures.append("validation_result_sha256 does not match the evidence")

    parsed_metrics = _validate_model_metrics(root, failures)
    _validate_metric_thresholds(parsed_metrics, failures)
    _validate_baselines(
        root,
        row_count=row_count,
        market_covered_count=market_covered_count,
        feature_pack_sha256=initial_hashes[0],
        calibrated_metrics=parsed_metrics.get(
            "calibrated_home_run_probability", {}
        ),
        failures=failures,
    )

    try:
        final_hashes = tuple(_file_sha256(path, label) for path, label in sources)
        if final_hashes != initial_hashes:
            failures.append("an input artifact changed during the promotion audit")
        if results_source is not None and results_file_hash is not None:
            final_results_hash = _file_sha256(
                results_source, "validation metric results"
            )
            if final_results_hash != results_file_hash:
                failures.append(
                    "validation metric results changed during the promotion audit"
                )
    except MLBHRValidationPromotionAuditError as exc:
        failures.append(str(exc))

    return _decision(
        failures,
        pipeline_hash=expected_pipeline_hash,
        result_hash=recorded_result_hash,
        test_labels_sealed=test_labels_sealed,
    )


__all__ = [
    "DO_NOT_PROMOTE",
    "MAXIMUM_CALIBRATED_CALIBRATION_ERROR",
    "MAXIMUM_CALIBRATED_CALIBRATION_ERROR_UPPER_BOUND",
    "MINIMUM_CALIBRATED_ROC_AUC",
    "MINIMUM_CALIBRATED_ROC_AUC_LOWER_BOUND",
    "MLBHRValidationPromotionAuditError",
    "MLBHRValidationPromotionDecision",
    "PROMOTE_TO_TEST_REVIEW",
    "VALIDATION_ACCEPTANCE_POLICY_VERSION",
    "VALIDATION_PROMOTION_EVIDENCE_SCHEMA_VERSION",
    "audit_mlb_hr_validation_promotion",
    "pipeline_sha256",
    "validation_result_sha256",
]
