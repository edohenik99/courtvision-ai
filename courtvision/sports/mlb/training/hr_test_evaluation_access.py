"""Read-only approval audit before frozen MLB HR test-label review.

This module verifies existing, hash-bound research artifacts only.  It does
not open labels, calculate metrics, train or refit a model, generate
predictions, write an artifact, or authorize production or wagering use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Final, Mapping, Sequence

from courtvision.sports.mlb.training.hr_prediction_artifact import (
    DEFAULT_MODEL_SPECIFICATION_PATH,
    MLBHRFrozenPredictionArtifact,
    MLBHRFrozenPredictionArtifactError,
    load_frozen_prediction_artifact,
)
from courtvision.sports.mlb.training.hr_validation_promotion import (
    PROMOTE_TO_TEST_REVIEW,
    VALIDATION_ACCEPTANCE_POLICY_VERSION,
    MLBHRValidationPromotionAuditError,
    audit_mlb_hr_validation_promotion,
    pipeline_sha256,
    validation_result_sha256,
)


FROZEN_TEST_EVALUATION_ACCESS_POLICY_VERSION: Final = (
    "mlb-hr-frozen-test-evaluation-access-v1"
)
VALIDATION_PROMOTION_AUDIT_RESULT_SCHEMA_VERSION: Final = (
    "mlb-hr-validation-promotion-audit-result-v1"
)
APPROVE_TEST_LABEL_ACCESS_REVIEW: Final = "APPROVE_TEST_LABEL_ACCESS_REVIEW"
DENY_TEST_LABEL_ACCESS: Final = "DENY_TEST_LABEL_ACCESS"

_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_PROMOTION_RESULT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "acceptance_policy_version",
        "verdict",
        "failures",
        "research_only",
        "approval_status",
        "immutable",
        "feature_pack_sha256",
        "temporal_split_plan_sha256",
        "fitted_preprocessing_artifact_sha256",
        "validation_prediction_file_sha256",
        "validation_prediction_artifact_sha256",
        "validation_result_file_sha256",
        "validation_result_sha256",
        "model_specification_sha256",
        "code_version_sha256",
        "pipeline_sha256",
        "test_labels_sealed",
        "test_labels_opened",
        "test_metrics_computed",
        "writes_performed",
        "test_label_access_authorized",
        "test_evaluation_authorized",
        "production_approved",
        "operational_use_enabled",
        "eligible_for_betting",
        "ev_enabled",
        "kelly_eligible",
        "elite_enabled",
        "staking_enabled",
        "audit_result_sha256",
    }
)
_PROMOTION_RESULT_HASH_FIELDS: Final = (
    "feature_pack_sha256",
    "temporal_split_plan_sha256",
    "fitted_preprocessing_artifact_sha256",
    "validation_prediction_file_sha256",
    "validation_prediction_artifact_sha256",
    "validation_result_file_sha256",
    "validation_result_sha256",
    "model_specification_sha256",
    "code_version_sha256",
    "pipeline_sha256",
    "audit_result_sha256",
)
_DISABLED_GATE_FIELDS: Final = frozenset(
    {
        "production_approved",
        "production_enabled",
        "operational_use_enabled",
        "model_training_enabled",
        "prediction_generation_enabled",
        "predictions_enabled",
        "backtesting_enabled",
        "evaluation_enabled",
        "metric_computation_enabled",
        "final_metrics_calculated",
        "live_fetching_enabled",
        "eligible_for_backtest",
        "eligible_for_betting",
        "betting_enabled",
        "wagering_enabled",
        "wager_sizing_enabled",
        "bankroll_enabled",
        "bankroll_management_enabled",
        "ev_enabled",
        "kelly_enabled",
        "kelly_eligible",
        "elite_enabled",
        "staking_enabled",
    }
)


class MLBHRTestEvaluationAccessAuditError(ValueError):
    """Raised when test-access evidence cannot be read or canonicalized."""


@dataclass(frozen=True, slots=True)
class MLBHRTestEvaluationAccessDecision:
    """One write-free technical-review decision with labels still sealed."""

    verdict: str
    failures: tuple[str, ...]
    pipeline_sha256: str | None
    validation_result_sha256: str | None
    test_prediction_artifact_sha256: str | None
    expected_test_rows: int | None
    predicted_test_rows: int | None
    matched_test_rows: int | None
    test_predictions_frozen: bool
    test_labels_sealed: bool
    writes_performed: bool = False
    labels_accessed: bool = False
    test_metrics_calculated: bool = False
    test_label_access_authorized: bool = False
    test_evaluation_authorized: bool = False
    production_approved: bool = False
    operational_use_enabled: bool = False
    eligible_for_betting: bool = False
    ev_enabled: bool = False
    kelly_eligible: bool = False
    elite_enabled: bool = False
    staking_enabled: bool = False
    research_only: bool = True
    approval_status: str = "not_approved"

    def __post_init__(self) -> None:
        object.__setattr__(self, "failures", tuple(self.failures))
        prohibited = (
            self.writes_performed,
            self.labels_accessed,
            self.test_metrics_calculated,
            self.test_label_access_authorized,
            self.test_evaluation_authorized,
            self.production_approved,
            self.operational_use_enabled,
            self.eligible_for_betting,
            self.ev_enabled,
            self.kelly_eligible,
            self.elite_enabled,
            self.staking_enabled,
        )
        approved = self.verdict == APPROVE_TEST_LABEL_ACCESS_REVIEW
        if (
            self.verdict
            not in {APPROVE_TEST_LABEL_ACCESS_REVIEW, DENY_TEST_LABEL_ACCESS}
            or (approved and self.failures)
            or (not approved and not self.failures)
            or (approved and not self.test_predictions_frozen)
            or (approved and not self.test_labels_sealed)
            or any(prohibited)
            or not self.research_only
            or self.approval_status != "not_approved"
        ):
            raise MLBHRTestEvaluationAccessAuditError(
                "invalid frozen test-evaluation access decision"
            )


def _file_sha256(path: Path, label: str) -> str:
    if not path.is_file():
        raise MLBHRTestEvaluationAccessAuditError(
            f"{label} must be an existing local file: {path}"
        )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise MLBHRTestEvaluationAccessAuditError(
            f"could not hash {label} {path}: {exc}"
        ) from exc
    return digest.hexdigest()


def _reject_nonstandard_number(value: str) -> object:
    raise ValueError(f"non-standard JSON number is prohibited: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is prohibited: {key}")
        result[key] = value
    return result


def _read_json_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_nonstandard_number,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise MLBHRTestEvaluationAccessAuditError(
            f"could not read {label} {path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise MLBHRTestEvaluationAccessAuditError(
            f"{label} must contain a JSON object"
        )
    return payload


def _canonical_sha256(payload: Mapping[str, object], *, label: str) -> str:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MLBHRTestEvaluationAccessAuditError(
            f"{label} cannot be canonicalized: {exc}"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def validation_promotion_audit_result_sha256(
    payload: Mapping[str, object],
) -> str:
    """Hash a promotion-audit result without its self-hash field."""

    content = dict(payload)
    content.pop("audit_result_sha256", None)
    return _canonical_sha256(content, label="validation promotion audit result")


def _require_sha256(
    value: object, location: str, failures: list[str]
) -> str | None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        failures.append(f"{location} must be lowercase SHA-256")
        return None
    return value


def _validate_research_gates(
    value: object, *, location: str, failures: list[str]
) -> None:
    if isinstance(value, Mapping):
        if value.get("research_only", True) is not True:
            failures.append(f"{location}.research_only must be true")
        if "approval_status" in value and value.get("approval_status") != "not_approved":
            failures.append(f"{location}.approval_status must remain 'not_approved'")
        if "mode" in value and value.get("mode") != "historical_research":
            failures.append(f"{location}.mode must remain 'historical_research'")
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key in _DISABLED_GATE_FIELDS and child is not False:
                failures.append(f"{child_location} must remain false")
            _validate_research_gates(
                child, location=child_location, failures=failures
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_research_gates(
                child, location=f"{location}[{index}]", failures=failures
            )


def _validate_promotion_result(
    payload: Mapping[str, object],
    *,
    expected_hashes: Mapping[str, str],
    failures: list[str],
) -> None:
    keys = set(payload)
    missing = sorted(_PROMOTION_RESULT_FIELDS - keys)
    extras = sorted(keys - _PROMOTION_RESULT_FIELDS)
    if missing:
        failures.append(
            "validation promotion audit result missing fields: "
            + ", ".join(missing)
        )
    if extras:
        failures.append(
            "validation promotion audit result unsupported fields: "
            + ", ".join(extras)
        )

    expected_values: Mapping[str, object] = {
        "schema_version": VALIDATION_PROMOTION_AUDIT_RESULT_SCHEMA_VERSION,
        "acceptance_policy_version": VALIDATION_ACCEPTANCE_POLICY_VERSION,
        "verdict": PROMOTE_TO_TEST_REVIEW,
        "failures": [],
        "research_only": True,
        "approval_status": "not_approved",
        "immutable": True,
        "test_labels_sealed": True,
        "test_labels_opened": False,
        "test_metrics_computed": False,
        "writes_performed": False,
        "test_label_access_authorized": False,
        "test_evaluation_authorized": False,
        "production_approved": False,
        "operational_use_enabled": False,
        "eligible_for_betting": False,
        "ev_enabled": False,
        "kelly_eligible": False,
        "elite_enabled": False,
        "staking_enabled": False,
    }
    for field_name, expected in expected_values.items():
        if payload.get(field_name) != expected:
            failures.append(
                f"validation promotion audit result {field_name} must be "
                f"{expected!r}"
            )

    for field_name in _PROMOTION_RESULT_HASH_FIELDS:
        _require_sha256(
            payload.get(field_name),
            f"validation promotion audit result {field_name}",
            failures,
        )
    for field_name, expected in expected_hashes.items():
        observed = payload.get(field_name)
        if isinstance(observed, str) and observed != expected:
            failures.append(
                f"validation promotion audit result {field_name} does not "
                "match the reviewed evidence"
            )
    try:
        expected_self_hash = validation_promotion_audit_result_sha256(payload)
    except MLBHRTestEvaluationAccessAuditError as exc:
        failures.append(str(exc))
    else:
        if payload.get("audit_result_sha256") != expected_self_hash:
            failures.append(
                "validation promotion audit result audit_result_sha256 does "
                "not match its content"
            )


def _split_dates(
    split_payload: Mapping[str, object], split_id: str
) -> frozenset[date]:
    raw_window = split_payload.get(split_id)
    if not isinstance(raw_window, Mapping):
        raise MLBHRTestEvaluationAccessAuditError(
            f"temporal split plan has no {split_id!r} window"
        )
    raw_dates = raw_window.get("game_dates")
    if not isinstance(raw_dates, list) or not raw_dates:
        raise MLBHRTestEvaluationAccessAuditError(
            f"temporal split plan {split_id}.game_dates must be non-empty"
        )
    parsed: list[date] = []
    for index, raw_date in enumerate(raw_dates):
        if not isinstance(raw_date, str):
            raise MLBHRTestEvaluationAccessAuditError(
                f"temporal split plan {split_id}.game_dates[{index}] is invalid"
            )
        try:
            parsed.append(date.fromisoformat(raw_date))
        except ValueError as exc:
            raise MLBHRTestEvaluationAccessAuditError(
                f"temporal split plan {split_id}.game_dates[{index}] is invalid"
            ) from exc
    if parsed != sorted(set(parsed)):
        raise MLBHRTestEvaluationAccessAuditError(
            f"temporal split plan {split_id}.game_dates must be unique and ordered"
        )
    return frozenset(parsed)


def _required_text(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MLBHRTestEvaluationAccessAuditError(
            f"{location} must be non-empty text"
        )
    return value


def _feature_population_identities(
    feature_payload: Mapping[str, object], split_dates: frozenset[date]
) -> tuple[tuple[str, str, str, str], ...]:
    """Read only identity fields; never retrieve or summarize label values."""

    raw_rows = feature_payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise MLBHRTestEvaluationAccessAuditError(
            "feature-pack rows must be a non-empty list"
        )
    identities: list[tuple[str, str, str, str]] = []
    identity_set: set[tuple[str, str, str, str]] = set()
    row_ids: set[str] = set()
    for index, raw_row in enumerate(raw_rows):
        location = f"feature-pack rows[{index}]"
        if not isinstance(raw_row, Mapping):
            raise MLBHRTestEvaluationAccessAuditError(
                f"{location} must be an object"
            )
        raw_date = raw_row.get("game_date")
        if not isinstance(raw_date, str):
            raise MLBHRTestEvaluationAccessAuditError(
                f"{location}.game_date must be an ISO-8601 date"
            )
        try:
            game_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise MLBHRTestEvaluationAccessAuditError(
                f"{location}.game_date must be an ISO-8601 date"
            ) from exc
        if game_date not in split_dates:
            continue
        row_id = _required_text(raw_row.get("row_id"), f"{location}.row_id")
        identity = (
            row_id,
            raw_date,
            _required_text(raw_row.get("game_id"), f"{location}.game_id"),
            _required_text(raw_row.get("player_id"), f"{location}.player_id"),
        )
        if row_id in row_ids or identity in identity_set:
            raise MLBHRTestEvaluationAccessAuditError(
                f"{location} duplicates a test-population identity"
            )
        row_ids.add(row_id)
        identity_set.add(identity)
        identities.append(identity)
    if not identities:
        raise MLBHRTestEvaluationAccessAuditError(
            "feature pack has no rows in the test split"
        )
    return tuple(identities)


def _format_identity_sample(
    identities: Sequence[tuple[str, str, str, str]],
) -> str:
    sample = ", ".join(identity[0] for identity in identities[:5])
    return sample + ("..." if len(identities) > 5 else "")


def _validate_test_population(
    *,
    feature_payload: Mapping[str, object],
    split_payload: Mapping[str, object],
    test_artifact: MLBHRFrozenPredictionArtifact,
    failures: list[str],
) -> tuple[int, int, int]:
    dates = _split_dates(split_payload, "test")
    expected = _feature_population_identities(feature_payload, dates)
    predicted = tuple(
        (
            row.row_id,
            row.game_date.isoformat(),
            row.game_id,
            row.player_id,
        )
        for row in test_artifact.rows
    )
    expected_set = set(expected)
    predicted_set = set(predicted)
    missing = sorted(expected_set - predicted_set)
    extra = sorted(predicted_set - expected_set)
    if missing:
        failures.append(
            f"test prediction population has {len(missing)} missing rows "
            f"({_format_identity_sample(missing)})"
        )
    if extra:
        failures.append(
            f"test prediction population has {len(extra)} extra rows "
            f"({_format_identity_sample(extra)})"
        )
    return len(expected), len(predicted), len(expected_set & predicted_set)


def _decision(
    failures: list[str],
    *,
    pipeline_hash: str | None,
    result_hash: str | None,
    test_artifact_hash: str | None,
    expected_rows: int | None,
    predicted_rows: int | None,
    matched_rows: int | None,
    test_predictions_frozen: bool,
    test_labels_sealed: bool,
) -> MLBHRTestEvaluationAccessDecision:
    unique_failures = tuple(dict.fromkeys(failures))
    return MLBHRTestEvaluationAccessDecision(
        verdict=(
            APPROVE_TEST_LABEL_ACCESS_REVIEW
            if not unique_failures
            else DENY_TEST_LABEL_ACCESS
        ),
        failures=unique_failures,
        pipeline_sha256=pipeline_hash,
        validation_result_sha256=result_hash,
        test_prediction_artifact_sha256=test_artifact_hash,
        expected_test_rows=expected_rows,
        predicted_test_rows=predicted_rows,
        matched_test_rows=matched_rows,
        test_predictions_frozen=test_predictions_frozen,
        test_labels_sealed=test_labels_sealed,
    )


def audit_mlb_hr_test_evaluation_access(
    *,
    feature_pack_path: str | Path,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
    validation_prediction_artifact_path: str | Path,
    validation_results_path: str | Path,
    validation_promotion_audit_result_path: str | Path,
    test_prediction_artifact_path: str | Path,
    model_specification_path: str | Path = DEFAULT_MODEL_SPECIFICATION_PATH,
) -> MLBHRTestEvaluationAccessDecision:
    """Audit the final technical gate while test labels remain sealed.

    A positive verdict approves only a human access review for these exact
    bytes.  It does not itself authorize or perform label access.
    """

    sources = (
        (Path(feature_pack_path).expanduser().resolve(), "feature pack"),
        (
            Path(temporal_split_plan_path).expanduser().resolve(),
            "temporal split plan",
        ),
        (
            Path(fitted_preprocessing_artifact_path).expanduser().resolve(),
            "fitted preprocessing artifact",
        ),
        (
            Path(validation_prediction_artifact_path).expanduser().resolve(),
            "validation prediction artifact",
        ),
        (
            Path(validation_results_path).expanduser().resolve(),
            "validation results",
        ),
        (
            Path(validation_promotion_audit_result_path).expanduser().resolve(),
            "validation promotion audit result",
        ),
        (
            Path(test_prediction_artifact_path).expanduser().resolve(),
            "test prediction artifact",
        ),
        (
            Path(model_specification_path).expanduser().resolve(),
            "model specification",
        ),
    )
    failures: list[str] = []
    try:
        initial_hashes = tuple(_file_sha256(path, label) for path, label in sources)
        payloads = tuple(
            _read_json_object(path, label) for path, label in sources[:-1]
        )
    except MLBHRTestEvaluationAccessAuditError as exc:
        return _decision(
            [str(exc)],
            pipeline_hash=None,
            result_hash=None,
            test_artifact_hash=None,
            expected_rows=None,
            predicted_rows=None,
            matched_rows=None,
            test_predictions_frozen=False,
            test_labels_sealed=False,
        )

    (
        feature_payload,
        split_payload,
        preprocessing_payload,
        validation_prediction_payload,
        validation_results_payload,
        promotion_result_payload,
        test_prediction_payload,
    ) = payloads
    for payload, (_, label) in zip(payloads, sources, strict=False):
        _validate_research_gates(payload, location=label, failures=failures)

    validation_decision = audit_mlb_hr_validation_promotion(
        feature_pack_path=sources[0][0],
        temporal_split_plan_path=sources[1][0],
        fitted_preprocessing_artifact_path=sources[2][0],
        prediction_artifact_path=sources[3][0],
        validation_results=sources[4][0],
        model_specification_path=sources[7][0],
    )
    if validation_decision.verdict != PROMOTE_TO_TEST_REVIEW:
        failures.append(
            "validation promotion audit did not return PROMOTE_TO_TEST_REVIEW"
        )
        failures.extend(
            f"validation promotion: {failure}"
            for failure in validation_decision.failures
        )

    validation_artifact: MLBHRFrozenPredictionArtifact | None = None
    test_artifact: MLBHRFrozenPredictionArtifact | None = None
    try:
        validation_artifact = load_frozen_prediction_artifact(
            sources[3][0],
            feature_pack_path=sources[0][0],
            temporal_split_plan_path=sources[1][0],
            fitted_preprocessing_artifact_path=sources[2][0],
            model_specification_path=sources[7][0],
        )
        test_artifact = load_frozen_prediction_artifact(
            sources[6][0],
            feature_pack_path=sources[0][0],
            temporal_split_plan_path=sources[1][0],
            fitted_preprocessing_artifact_path=sources[2][0],
            model_specification_path=sources[7][0],
        )
    except MLBHRFrozenPredictionArtifactError as exc:
        failures.append(str(exc))

    expected_rows: int | None = None
    predicted_rows: int | None = None
    matched_rows: int | None = None
    validation_pipeline_hash = validation_decision.pipeline_sha256
    test_pipeline_hash: str | None = None
    test_artifact_hash: str | None = None
    test_predictions_frozen = False
    if validation_artifact is not None:
        if validation_artifact.split_id != "validation":
            failures.append("validation prediction artifact must be validation-only")
        independently_computed = pipeline_sha256(validation_artifact)
        if (
            validation_pipeline_hash is not None
            and validation_pipeline_hash != independently_computed
        ):
            failures.append(
                "validation promotion pipeline hash changed during access audit"
            )
        validation_pipeline_hash = independently_computed
    if test_artifact is not None:
        test_artifact_hash = test_artifact.artifact_sha256
        if test_artifact.split_id != "test":
            failures.append("test prediction artifact must be test-only")
        test_predictions_frozen = (
            test_artifact.split_id == "test"
            and test_artifact.research_only
            and test_artifact.evaluation_data_sealed
            and test_artifact.immutable
            and not test_artifact.production_approved
            and not test_artifact.operational_use_enabled
        )
        if not test_predictions_frozen:
            failures.append(
                "test predictions were not frozen while evaluation data was sealed"
            )
        test_pipeline_hash = pipeline_sha256(test_artifact)
        if (
            validation_pipeline_hash is not None
            and test_pipeline_hash != validation_pipeline_hash
        ):
            failures.append(
                "test pipeline_sha256 does not exactly match the accepted "
                "validation pipeline"
            )
        try:
            expected_rows, predicted_rows, matched_rows = _validate_test_population(
                feature_payload=feature_payload,
                split_payload=split_payload,
                test_artifact=test_artifact,
                failures=failures,
            )
        except MLBHRTestEvaluationAccessAuditError as exc:
            failures.append(str(exc))

    result_hash: str | None = None
    try:
        result_hash = validation_result_sha256(validation_results_payload)
    except MLBHRValidationPromotionAuditError as exc:
        failures.append(str(exc))
    expected_receipt_hashes: dict[str, str] = {
        "feature_pack_sha256": initial_hashes[0],
        "temporal_split_plan_sha256": initial_hashes[1],
        "fitted_preprocessing_artifact_sha256": initial_hashes[2],
        "validation_prediction_file_sha256": initial_hashes[3],
        "validation_result_file_sha256": initial_hashes[4],
    }
    if validation_artifact is not None:
        expected_receipt_hashes.update(
            {
                "validation_prediction_artifact_sha256": (
                    validation_artifact.artifact_sha256
                ),
                "model_specification_sha256": (
                    validation_artifact.model_specification_sha256
                ),
                "code_version_sha256": validation_artifact.code_version_sha256,
            }
        )
    if result_hash is not None:
        expected_receipt_hashes["validation_result_sha256"] = result_hash
    if validation_pipeline_hash is not None:
        expected_receipt_hashes["pipeline_sha256"] = validation_pipeline_hash
    _validate_promotion_result(
        promotion_result_payload,
        expected_hashes=expected_receipt_hashes,
        failures=failures,
    )

    test_labels_sealed = (
        validation_decision.test_labels_sealed
        and promotion_result_payload.get("test_labels_sealed") is True
        and promotion_result_payload.get("test_labels_opened") is False
        and promotion_result_payload.get("test_metrics_computed") is False
    )
    if not test_labels_sealed:
        failures.append("test labels must remain sealed and unevaluated")

    try:
        final_hashes = tuple(_file_sha256(path, label) for path, label in sources)
        if final_hashes != initial_hashes:
            failures.append("an input artifact changed during the test-access audit")
    except MLBHRTestEvaluationAccessAuditError as exc:
        failures.append(str(exc))

    return _decision(
        failures,
        pipeline_hash=(
            validation_pipeline_hash
            if validation_pipeline_hash == test_pipeline_hash
            else None
        ),
        result_hash=result_hash,
        test_artifact_hash=test_artifact_hash,
        expected_rows=expected_rows,
        predicted_rows=predicted_rows,
        matched_rows=matched_rows,
        test_predictions_frozen=test_predictions_frozen,
        test_labels_sealed=test_labels_sealed,
    )


__all__ = [
    "APPROVE_TEST_LABEL_ACCESS_REVIEW",
    "DENY_TEST_LABEL_ACCESS",
    "FROZEN_TEST_EVALUATION_ACCESS_POLICY_VERSION",
    "MLBHRTestEvaluationAccessAuditError",
    "MLBHRTestEvaluationAccessDecision",
    "VALIDATION_PROMOTION_AUDIT_RESULT_SCHEMA_VERSION",
    "audit_mlb_hr_test_evaluation_access",
    "validation_promotion_audit_result_sha256",
]
