"""Approval-gated planning for one frozen MLB HR test evaluation.

The planner is intentionally write-free.  It validates an immutable approval
receipt and the exact frozen test inputs, proves full-population prediction
coverage, and returns policy metadata only.  It never retrieves label values,
calculates metrics, trains or runs a model, generates predictions, fetches
data, writes a result, or enables any operational or wagering path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Final, Mapping, Sequence

from courtvision.sports.mlb.training.hr_evaluation_contract import (
    ALLOWED_EVALUATION_METRIC_NAMES,
)
from courtvision.sports.mlb.training.hr_prediction_artifact import (
    DEFAULT_MODEL_SPECIFICATION_PATH,
    IMMUTABLE_WRITE_POLICY,
    MLBHRFrozenPredictionArtifact,
    MLBHRFrozenPredictionArtifactError,
    load_frozen_prediction_artifact,
)
from courtvision.sports.mlb.training.hr_label_custody import (
    MLBHRLabelCustodyError,
    resolve_label_custody_path,
    validate_mlb_hr_label_custody,
)
from courtvision.sports.mlb.training.hr_test_evaluation_access import (
    APPROVE_TEST_LABEL_ACCESS_REVIEW,
    FROZEN_TEST_EVALUATION_ACCESS_POLICY_VERSION,
)
from courtvision.sports.mlb.training.hr_validation_promotion import (
    pipeline_sha256,
)


ONE_SHOT_TEST_EVALUATOR_POLICY_VERSION: Final = (
    "mlb-hr-one-shot-frozen-test-evaluator-v1"
)
TEST_ACCESS_APPROVAL_RECEIPT_SCHEMA_VERSION: Final = (
    "mlb-hr-test-access-approval-receipt-v1"
)
ONE_SHOT_TEST_EVALUATION_APPROVAL_SCOPE: Final = (
    "one_shot_frozen_test_evaluation_only"
)
APPROVE_ONE_SHOT_TEST_LABEL_HANDOFF: Final = (
    "APPROVE_ONE_SHOT_TEST_LABEL_HANDOFF"
)
ONE_SHOT_TEST_EVALUATION_PLAN_ONLY: Final = (
    "ONE_SHOT_TEST_EVALUATION_PLAN_ONLY"
)
RESULT_ARTIFACT_WRITE_POLICY: Final = IMMUTABLE_WRITE_POLICY
MISSING_TEST_PREDICTION_POLICY: Final = (
    "exact_test_population_required_no_drop_no_impute_no_replacement"
)

_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "evaluator_policy_version",
        "access_policy_version",
        "access_audit_verdict",
        "approval_scope",
        "approval_id",
        "methodology_approver",
        "operator_approver",
        "approved_at",
        "test_access_approved",
        "label_handoff_approval",
        "one_shot_attempt_number",
        "one_shot_attempt_consumed",
        "feature_pack_sha256",
        "temporal_split_plan_sha256",
        "fitted_preprocessing_artifact_sha256",
        "test_prediction_file_sha256",
        "test_prediction_artifact_sha256",
        "accepted_validation_pipeline_sha256",
        "test_pipeline_sha256",
        "allowed_metrics",
        "test_labels_sealed",
        "test_labels_opened",
        "test_metrics_computed",
        "result_artifact_created",
        "no_rerun",
        "no_cherry_pick",
        "report_all_frozen_metrics",
        "result_artifact_write_policy",
        "research_only",
        "approval_status",
        "immutable",
        "write_policy",
        "production_approved",
        "operational_use_enabled",
        "eligible_for_betting",
        "betting_enabled",
        "ev_enabled",
        "kelly_eligible",
        "elite_enabled",
        "staking_enabled",
        "bankroll_enabled",
        "receipt_sha256",
    }
)
_RECEIPT_HASH_FIELDS: Final = (
    "feature_pack_sha256",
    "temporal_split_plan_sha256",
    "fitted_preprocessing_artifact_sha256",
    "test_prediction_file_sha256",
    "test_prediction_artifact_sha256",
    "accepted_validation_pipeline_sha256",
    "test_pipeline_sha256",
    "receipt_sha256",
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
        "test_evaluation_execution_enabled",
        "metric_computation_enabled",
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


class MLBHROneShotTestEvaluatorError(ValueError):
    """Raised when one-shot test-evaluator planning must fail closed."""


@dataclass(frozen=True, slots=True)
class MLBHRTestAccessApprovalReceipt:
    """Validated immutable approval for one exact label handoff."""

    path: Path
    file_sha256: str
    receipt_sha256: str
    approval_id: str
    methodology_approver: str
    operator_approver: str
    approved_at: datetime
    feature_pack_sha256: str
    temporal_split_plan_sha256: str
    fitted_preprocessing_artifact_sha256: str
    test_prediction_file_sha256: str
    test_prediction_artifact_sha256: str
    accepted_validation_pipeline_sha256: str
    test_pipeline_sha256: str
    allowed_metrics: tuple[str, ...]
    test_access_approved: bool = True
    label_handoff_approval: str = APPROVE_ONE_SHOT_TEST_LABEL_HANDOFF
    test_labels_sealed: bool = True
    test_labels_opened: bool = False
    test_metrics_computed: bool = False
    result_artifact_created: bool = False
    one_shot_attempt_number: int = 1
    one_shot_attempt_consumed: bool = False
    no_rerun: bool = True
    no_cherry_pick: bool = True
    report_all_frozen_metrics: bool = True
    research_only: bool = True
    approval_status: str = "not_approved"

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", self.path.resolve())
        object.__setattr__(self, "allowed_metrics", tuple(self.allowed_metrics))
        required = (
            self.test_access_approved,
            self.test_labels_sealed,
            self.no_rerun,
            self.no_cherry_pick,
            self.report_all_frozen_metrics,
            self.research_only,
        )
        prohibited = (
            self.test_labels_opened,
            self.test_metrics_computed,
            self.result_artifact_created,
            self.one_shot_attempt_consumed,
        )
        if (
            not all(required)
            or any(prohibited)
            or self.label_handoff_approval
            != APPROVE_ONE_SHOT_TEST_LABEL_HANDOFF
            or self.one_shot_attempt_number != 1
            or self.allowed_metrics != ALLOWED_EVALUATION_METRIC_NAMES
            or self.accepted_validation_pipeline_sha256
            != self.test_pipeline_sha256
            or self.approval_status != "not_approved"
        ):
            raise MLBHROneShotTestEvaluatorError(
                "test-access approval receipt does not authorize the frozen "
                "one-shot research handoff"
            )


@dataclass(frozen=True, slots=True)
class MLBHRTestPredictionPopulationCoverage:
    """Exact test identity coverage established without retrieving labels."""

    expected_rows: int
    predicted_rows: int
    matched_rows: int
    missing_rows: int = 0
    extra_rows: int = 0
    exact_identity_match: bool = True
    labels_accessed: bool = False
    policy: str = MISSING_TEST_PREDICTION_POLICY

    def __post_init__(self) -> None:
        if (
            self.expected_rows <= 0
            or self.predicted_rows != self.expected_rows
            or self.matched_rows != self.expected_rows
            or self.missing_rows
            or self.extra_rows
            or not self.exact_identity_match
            or self.labels_accessed
            or self.policy != MISSING_TEST_PREDICTION_POLICY
        ):
            raise MLBHROneShotTestEvaluatorError(
                "test prediction coverage must be an exact label-sealed match"
            )


@dataclass(frozen=True, slots=True)
class MLBHROneShotTestEvaluationPolicy:
    """Frozen rules that a future separately approved executor must obey."""

    allowed_metrics: tuple[str, ...] = ALLOWED_EVALUATION_METRIC_NAMES
    maximum_attempts: int = 1
    rerun_allowed: bool = False
    cherry_pick_allowed: bool = False
    report_all_frozen_metrics_required: bool = True
    failed_or_partial_attempt_consumes_one_shot: bool = True
    result_artifact_required: bool = True
    result_artifact_immutable: bool = True
    result_artifact_write_policy: str = RESULT_ARTIFACT_WRITE_POLICY
    result_artifact_append_allowed: bool = False
    result_artifact_overwrite_allowed: bool = False
    result_artifact_repair_allowed: bool = False
    result_writer_implemented: bool = True
    research_only: bool = True
    approval_status: str = "not_approved"

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_metrics", tuple(self.allowed_metrics))
        required = (
            self.report_all_frozen_metrics_required,
            self.failed_or_partial_attempt_consumes_one_shot,
            self.result_artifact_required,
            self.result_artifact_immutable,
            self.result_writer_implemented,
            self.research_only,
        )
        prohibited = (
            self.rerun_allowed,
            self.cherry_pick_allowed,
            self.result_artifact_append_allowed,
            self.result_artifact_overwrite_allowed,
            self.result_artifact_repair_allowed,
        )
        if (
            self.allowed_metrics != ALLOWED_EVALUATION_METRIC_NAMES
            or self.maximum_attempts != 1
            or not all(required)
            or any(prohibited)
            or self.result_artifact_write_policy != RESULT_ARTIFACT_WRITE_POLICY
            or self.approval_status != "not_approved"
        ):
            raise MLBHROneShotTestEvaluatorError(
                "the one-shot frozen test-evaluation policy cannot be relaxed"
            )


@dataclass(frozen=True, slots=True)
class MLBHROneShotTestEvaluationPlan:
    """Write-free plan proving that the final approval boundary passed."""

    feature_pack_path: Path
    label_custody_path: Path
    temporal_split_plan_path: Path
    fitted_preprocessing_artifact_path: Path
    test_prediction_artifact_path: Path
    test_access_approval_receipt_path: Path
    feature_pack_sha256: str
    label_custody_sha256: str
    temporal_split_plan_sha256: str
    fitted_preprocessing_artifact_sha256: str
    test_prediction_file_sha256: str
    test_prediction_artifact_sha256: str
    test_access_approval_receipt_file_sha256: str
    test_access_approval_receipt_sha256: str
    accepted_validation_pipeline_sha256: str
    test_pipeline_sha256: str
    window_id: str
    approval_id: str
    population_coverage: MLBHRTestPredictionPopulationCoverage
    policy: MLBHROneShotTestEvaluationPolicy = field(
        default_factory=MLBHROneShotTestEvaluationPolicy
    )
    contract_version: str = ONE_SHOT_TEST_EVALUATOR_POLICY_VERSION
    status: str = ONE_SHOT_TEST_EVALUATION_PLAN_ONLY
    split_id: str = "test"
    research_only: bool = True
    approval_status: str = "not_approved"
    test_access_approved: bool = True
    label_handoff_approved: bool = True
    test_labels_sealed: bool = True
    labels_accessed: bool = False
    test_metrics_calculated: bool = False
    metric_computation_enabled: bool = False
    model_training_enabled: bool = False
    prediction_generation_enabled: bool = False
    live_fetching_enabled: bool = False
    test_evaluation_execution_enabled: bool = False
    result_artifact_writing_enabled: bool = False
    production_approved: bool = False
    operational_use_enabled: bool = False
    eligible_for_betting: bool = False
    betting_enabled: bool = False
    ev_enabled: bool = False
    kelly_eligible: bool = False
    elite_enabled: bool = False
    staking_enabled: bool = False
    bankroll_enabled: bool = False
    writes_performed: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "feature_pack_path",
            "label_custody_path",
            "temporal_split_plan_path",
            "fitted_preprocessing_artifact_path",
            "test_prediction_artifact_path",
            "test_access_approval_receipt_path",
        ):
            object.__setattr__(self, field_name, getattr(self, field_name).resolve())
        required = (
            self.research_only,
            self.test_access_approved,
            self.label_handoff_approved,
            self.test_labels_sealed,
        )
        prohibited = (
            self.labels_accessed,
            self.test_metrics_calculated,
            self.metric_computation_enabled,
            self.model_training_enabled,
            self.prediction_generation_enabled,
            self.live_fetching_enabled,
            self.test_evaluation_execution_enabled,
            self.result_artifact_writing_enabled,
            self.production_approved,
            self.operational_use_enabled,
            self.eligible_for_betting,
            self.betting_enabled,
            self.ev_enabled,
            self.kelly_eligible,
            self.elite_enabled,
            self.staking_enabled,
            self.bankroll_enabled,
            self.writes_performed,
        )
        if (
            self.contract_version != ONE_SHOT_TEST_EVALUATOR_POLICY_VERSION
            or self.status != ONE_SHOT_TEST_EVALUATION_PLAN_ONLY
            or self.split_id != "test"
            or self.approval_status != "not_approved"
            or self.accepted_validation_pipeline_sha256
            != self.test_pipeline_sha256
            or not all(required)
            or any(prohibited)
        ):
            raise MLBHROneShotTestEvaluatorError(
                "the one-shot test-evaluation planning boundary cannot be relaxed"
            )


def _file_sha256(path: Path, label: str) -> str:
    if not path.is_file():
        raise MLBHROneShotTestEvaluatorError(
            f"{label} must be an existing local file: {path}"
        )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise MLBHROneShotTestEvaluatorError(
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
        raise MLBHROneShotTestEvaluatorError(
            f"could not read {label} {path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise MLBHROneShotTestEvaluatorError(
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
        raise MLBHROneShotTestEvaluatorError(
            f"{label} cannot be canonicalized: {exc}"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def test_access_approval_receipt_sha256(payload: Mapping[str, object]) -> str:
    """Hash canonical receipt content without its self-hash field."""

    content = dict(payload)
    content.pop("receipt_sha256", None)
    return _canonical_sha256(content, label="test-access approval receipt")


def _required_text(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MLBHROneShotTestEvaluatorError(f"{location} must be non-empty text")
    return value


def _required_sha256(value: object, location: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise MLBHROneShotTestEvaluatorError(
            f"{location} must be lowercase SHA-256"
        )
    return value


def _parse_approved_at(value: object) -> datetime:
    text = _required_text(value, "test-access approval receipt approved_at")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MLBHROneShotTestEvaluatorError(
            "test-access approval receipt approved_at must be an ISO-8601 "
            "timezone-aware datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MLBHROneShotTestEvaluatorError(
            "test-access approval receipt approved_at must be an ISO-8601 "
            "timezone-aware datetime"
        )
    return parsed


def _validate_research_gates(value: object, *, location: str) -> None:
    if isinstance(value, Mapping):
        if "research_only" in value and value.get("research_only") is not True:
            raise MLBHROneShotTestEvaluatorError(
                f"{location}.research_only must be true"
            )
        if (
            "approval_status" in value
            and value.get("approval_status") != "not_approved"
        ):
            raise MLBHROneShotTestEvaluatorError(
                f"{location}.approval_status must remain 'not_approved'"
            )
        if "mode" in value and value.get("mode") != "historical_research":
            raise MLBHROneShotTestEvaluatorError(
                f"{location}.mode must remain 'historical_research'"
            )
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key in _DISABLED_GATE_FIELDS and child is not False:
                raise MLBHROneShotTestEvaluatorError(
                    f"{child_location} must remain false"
                )
            _validate_research_gates(child, location=child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_research_gates(
                child,
                location=f"{location}[{index}]",
            )


def load_test_access_approval_receipt(
    path: str | Path,
) -> MLBHRTestAccessApprovalReceipt:
    """Validate an existing create-once approval receipt without writing."""

    source = Path(path).expanduser().resolve()
    file_hash = _file_sha256(source, "test-access approval receipt")
    payload = _read_json_object(source, "test-access approval receipt")
    missing = sorted(_RECEIPT_FIELDS - payload.keys())
    extras = sorted(payload.keys() - _RECEIPT_FIELDS)
    if missing:
        raise MLBHROneShotTestEvaluatorError(
            "test-access approval receipt missing fields: " + ", ".join(missing)
        )
    if extras:
        raise MLBHROneShotTestEvaluatorError(
            "test-access approval receipt unsupported fields: "
            + ", ".join(str(value) for value in extras)
        )

    expected_values: Mapping[str, object] = {
        "schema_version": TEST_ACCESS_APPROVAL_RECEIPT_SCHEMA_VERSION,
        "evaluator_policy_version": ONE_SHOT_TEST_EVALUATOR_POLICY_VERSION,
        "access_policy_version": FROZEN_TEST_EVALUATION_ACCESS_POLICY_VERSION,
        "access_audit_verdict": APPROVE_TEST_LABEL_ACCESS_REVIEW,
        "approval_scope": ONE_SHOT_TEST_EVALUATION_APPROVAL_SCOPE,
        "test_access_approved": True,
        "label_handoff_approval": APPROVE_ONE_SHOT_TEST_LABEL_HANDOFF,
        "one_shot_attempt_number": 1,
        "one_shot_attempt_consumed": False,
        "allowed_metrics": list(ALLOWED_EVALUATION_METRIC_NAMES),
        "test_labels_sealed": True,
        "test_labels_opened": False,
        "test_metrics_computed": False,
        "result_artifact_created": False,
        "no_rerun": True,
        "no_cherry_pick": True,
        "report_all_frozen_metrics": True,
        "result_artifact_write_policy": RESULT_ARTIFACT_WRITE_POLICY,
        "research_only": True,
        "approval_status": "not_approved",
        "immutable": True,
        "write_policy": IMMUTABLE_WRITE_POLICY,
        "production_approved": False,
        "operational_use_enabled": False,
        "eligible_for_betting": False,
        "betting_enabled": False,
        "ev_enabled": False,
        "kelly_eligible": False,
        "elite_enabled": False,
        "staking_enabled": False,
        "bankroll_enabled": False,
    }
    invalid = [
        field_name
        for field_name, expected in expected_values.items()
        if payload.get(field_name) != expected
    ]
    if invalid:
        raise MLBHROneShotTestEvaluatorError(
            "test-access approval receipt has invalid approval/policy fields: "
            + ", ".join(invalid)
        )
    _validate_research_gates(payload, location="test-access approval receipt")

    hashes = {
        field_name: _required_sha256(
            payload.get(field_name),
            f"test-access approval receipt {field_name}",
        )
        for field_name in _RECEIPT_HASH_FIELDS
    }
    expected_self_hash = test_access_approval_receipt_sha256(payload)
    if hashes["receipt_sha256"] != expected_self_hash:
        raise MLBHROneShotTestEvaluatorError(
            "test-access approval receipt receipt_sha256 does not match its "
            "content"
        )
    if (
        hashes["accepted_validation_pipeline_sha256"]
        != hashes["test_pipeline_sha256"]
    ):
        raise MLBHROneShotTestEvaluatorError(
            "test-access approval receipt does not bind identical validation "
            "and test pipeline hashes"
        )

    methodology_approver = _required_text(
        payload.get("methodology_approver"),
        "test-access approval receipt methodology_approver",
    )
    operator_approver = _required_text(
        payload.get("operator_approver"),
        "test-access approval receipt operator_approver",
    )
    if methodology_approver == operator_approver:
        raise MLBHROneShotTestEvaluatorError(
            "test-access approval receipt requires distinct methodology and "
            "operator approvers"
        )

    final_file_hash = _file_sha256(source, "test-access approval receipt")
    if final_file_hash != file_hash:
        raise MLBHROneShotTestEvaluatorError(
            "test-access approval receipt changed during validation"
        )
    return MLBHRTestAccessApprovalReceipt(
        path=source,
        file_sha256=file_hash,
        receipt_sha256=hashes["receipt_sha256"],
        approval_id=_required_text(
            payload.get("approval_id"),
            "test-access approval receipt approval_id",
        ),
        methodology_approver=methodology_approver,
        operator_approver=operator_approver,
        approved_at=_parse_approved_at(payload.get("approved_at")),
        feature_pack_sha256=hashes["feature_pack_sha256"],
        temporal_split_plan_sha256=hashes["temporal_split_plan_sha256"],
        fitted_preprocessing_artifact_sha256=(
            hashes["fitted_preprocessing_artifact_sha256"]
        ),
        test_prediction_file_sha256=hashes["test_prediction_file_sha256"],
        test_prediction_artifact_sha256=(
            hashes["test_prediction_artifact_sha256"]
        ),
        accepted_validation_pipeline_sha256=(
            hashes["accepted_validation_pipeline_sha256"]
        ),
        test_pipeline_sha256=hashes["test_pipeline_sha256"],
        allowed_metrics=tuple(str(value) for value in payload["allowed_metrics"]),
    )


def _split_dates(payload: Mapping[str, object]) -> frozenset[date]:
    raw_window = payload.get("test")
    if not isinstance(raw_window, Mapping):
        raise MLBHROneShotTestEvaluatorError(
            "temporal split plan has no 'test' window"
        )
    raw_dates = raw_window.get("game_dates")
    if not isinstance(raw_dates, list) or not raw_dates:
        raise MLBHROneShotTestEvaluatorError(
            "temporal split plan test.game_dates must be non-empty"
        )
    dates: list[date] = []
    for index, raw_date in enumerate(raw_dates):
        if not isinstance(raw_date, str):
            raise MLBHROneShotTestEvaluatorError(
                f"temporal split plan test.game_dates[{index}] is invalid"
            )
        try:
            dates.append(date.fromisoformat(raw_date))
        except ValueError as exc:
            raise MLBHROneShotTestEvaluatorError(
                f"temporal split plan test.game_dates[{index}] is invalid"
            ) from exc
    if dates != sorted(set(dates)):
        raise MLBHROneShotTestEvaluatorError(
            "temporal split plan test.game_dates must be unique and ordered"
        )
    return frozenset(dates)


def _feature_test_identities(
    payload: Mapping[str, object],
    test_dates: frozenset[date],
) -> tuple[tuple[str, str, str, str], ...]:
    """Select identity fields only; label keys and values are never accessed."""

    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise MLBHROneShotTestEvaluatorError(
            "feature-pack rows must be a non-empty list"
        )
    identities: list[tuple[str, str, str, str]] = []
    identity_set: set[tuple[str, str, str, str]] = set()
    row_ids: set[str] = set()
    for index, raw_row in enumerate(raw_rows):
        location = f"feature-pack rows[{index}]"
        if not isinstance(raw_row, Mapping):
            raise MLBHROneShotTestEvaluatorError(f"{location} must be an object")
        raw_date = raw_row.get("game_date")
        if not isinstance(raw_date, str):
            raise MLBHROneShotTestEvaluatorError(
                f"{location}.game_date must be an ISO-8601 date"
            )
        try:
            game_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise MLBHROneShotTestEvaluatorError(
                f"{location}.game_date must be an ISO-8601 date"
            ) from exc
        if game_date not in test_dates:
            continue
        row_id = _required_text(raw_row.get("row_id"), f"{location}.row_id")
        identity = (
            row_id,
            raw_date,
            _required_text(raw_row.get("game_id"), f"{location}.game_id"),
            _required_text(raw_row.get("player_id"), f"{location}.player_id"),
        )
        if row_id in row_ids or identity in identity_set:
            raise MLBHROneShotTestEvaluatorError(
                f"{location} duplicates a test-population identity"
            )
        row_ids.add(row_id)
        identity_set.add(identity)
        identities.append(identity)
    if not identities:
        raise MLBHROneShotTestEvaluatorError(
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
    artifact: MLBHRFrozenPredictionArtifact,
) -> MLBHRTestPredictionPopulationCoverage:
    expected = _feature_test_identities(feature_payload, _split_dates(split_payload))
    predicted = tuple(
        (
            row.row_id,
            row.game_date.isoformat(),
            row.game_id,
            row.player_id,
        )
        for row in artifact.rows
    )
    expected_set = set(expected)
    predicted_set = set(predicted)
    missing = sorted(expected_set - predicted_set)
    extra = sorted(predicted_set - expected_set)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(
                f"missing test predictions={len(missing)} "
                f"({_format_identity_sample(missing)})"
            )
        if extra:
            details.append(
                f"extra test predictions={len(extra)} "
                f"({_format_identity_sample(extra)})"
            )
        raise MLBHROneShotTestEvaluatorError(
            "test prediction population coverage failed: " + "; ".join(details)
        )
    return MLBHRTestPredictionPopulationCoverage(
        expected_rows=len(expected),
        predicted_rows=len(predicted),
        matched_rows=len(expected_set & predicted_set),
    )


def plan_one_shot_frozen_mlb_hr_test_evaluation(
    *,
    feature_pack_path: str | Path,
    label_custody_path: str | Path | None = None,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
    test_prediction_artifact_path: str | Path,
    test_access_approval_receipt_path: str | Path,
    model_specification_path: str | Path = DEFAULT_MODEL_SPECIFICATION_PATH,
) -> MLBHROneShotTestEvaluationPlan:
    """Return a write-free test plan only after exact approval verification."""

    feature_source = Path(feature_pack_path).expanduser().resolve()
    custody_source = resolve_label_custody_path(
        feature_source, label_custody_path
    )
    sources = (
        (feature_source, "feature pack"),
        (custody_source, "label-custody artifact"),
        (
            Path(temporal_split_plan_path).expanduser().resolve(),
            "temporal split plan",
        ),
        (
            Path(fitted_preprocessing_artifact_path).expanduser().resolve(),
            "fitted preprocessing artifact",
        ),
        (
            Path(test_prediction_artifact_path).expanduser().resolve(),
            "test prediction artifact",
        ),
        (
            Path(test_access_approval_receipt_path).expanduser().resolve(),
            "test-access approval receipt",
        ),
    )
    initial_hashes = tuple(_file_sha256(path, label) for path, label in sources)

    # Approval is validated before any feature-pack row is selected.  The
    # planner still never retrieves a label value after approval.
    receipt = load_test_access_approval_receipt(sources[5][0])
    expected_receipt_bindings = (
        receipt.feature_pack_sha256,
        receipt.temporal_split_plan_sha256,
        receipt.fitted_preprocessing_artifact_sha256,
        receipt.test_prediction_file_sha256,
        receipt.file_sha256,
    )
    observed_receipt_bindings = (
        initial_hashes[0],
        initial_hashes[2],
        initial_hashes[3],
        initial_hashes[4],
        initial_hashes[5],
    )
    if expected_receipt_bindings != observed_receipt_bindings:
        names = (
            "feature_pack_sha256",
            "temporal_split_plan_sha256",
            "fitted_preprocessing_artifact_sha256",
            "test_prediction_file_sha256",
            "test_access_approval_receipt_file_sha256",
        )
        mismatches = [
            name
            for name, expected, observed in zip(
                names,
                expected_receipt_bindings,
                observed_receipt_bindings,
                strict=True,
            )
            if expected != observed
        ]
        raise MLBHROneShotTestEvaluatorError(
            "test-access approval receipt input hash mismatch: "
            + ", ".join(mismatches)
        )

    try:
        validate_mlb_hr_label_custody(
            feature_pack_path=sources[0][0],
            label_custody_path=sources[1][0],
        )
    except MLBHRLabelCustodyError as exc:
        raise MLBHROneShotTestEvaluatorError(
            f"label-custody binding failed: {exc}"
        ) from exc

    try:
        artifact = load_frozen_prediction_artifact(
            sources[4][0],
            feature_pack_path=sources[0][0],
            temporal_split_plan_path=sources[2][0],
            fitted_preprocessing_artifact_path=sources[3][0],
            model_specification_path=model_specification_path,
        )
    except MLBHRFrozenPredictionArtifactError as exc:
        raise MLBHROneShotTestEvaluatorError(
            f"frozen test prediction artifact gate failed: {exc}"
        ) from exc
    if artifact.split_id != "test":
        raise MLBHROneShotTestEvaluatorError(
            "one-shot test evaluator requires a test-only prediction artifact"
        )
    if artifact.artifact_sha256 != receipt.test_prediction_artifact_sha256:
        raise MLBHROneShotTestEvaluatorError(
            "test prediction artifact hash does not match the approval receipt"
        )

    test_pipeline_hash = pipeline_sha256(artifact)
    if test_pipeline_hash != receipt.test_pipeline_sha256:
        raise MLBHROneShotTestEvaluatorError(
            "test pipeline hash does not match the approval receipt"
        )
    if test_pipeline_hash != receipt.accepted_validation_pipeline_sha256:
        raise MLBHROneShotTestEvaluatorError(
            "test pipeline hash changed from the accepted validation pipeline"
        )

    feature_payload = _read_json_object(sources[0][0], "feature pack")
    split_payload = _read_json_object(sources[2][0], "temporal split plan")
    preprocessing_payload = _read_json_object(
        sources[3][0], "fitted preprocessing artifact"
    )
    for payload, label in (
        (feature_payload, "feature pack"),
        (split_payload, "temporal split plan"),
        (preprocessing_payload, "fitted preprocessing artifact"),
    ):
        _validate_research_gates(payload, location=label)

    coverage = _validate_test_population(
        feature_payload=feature_payload,
        split_payload=split_payload,
        artifact=artifact,
    )
    final_hashes = tuple(_file_sha256(path, label) for path, label in sources)
    if final_hashes != initial_hashes:
        raise MLBHROneShotTestEvaluatorError(
            "an input changed during one-shot test-evaluator planning"
        )

    return MLBHROneShotTestEvaluationPlan(
        feature_pack_path=sources[0][0],
        label_custody_path=sources[1][0],
        temporal_split_plan_path=sources[2][0],
        fitted_preprocessing_artifact_path=sources[3][0],
        test_prediction_artifact_path=sources[4][0],
        test_access_approval_receipt_path=sources[5][0],
        feature_pack_sha256=initial_hashes[0],
        label_custody_sha256=initial_hashes[1],
        temporal_split_plan_sha256=initial_hashes[2],
        fitted_preprocessing_artifact_sha256=initial_hashes[3],
        test_prediction_file_sha256=initial_hashes[4],
        test_prediction_artifact_sha256=artifact.artifact_sha256,
        test_access_approval_receipt_file_sha256=initial_hashes[5],
        test_access_approval_receipt_sha256=receipt.receipt_sha256,
        accepted_validation_pipeline_sha256=(
            receipt.accepted_validation_pipeline_sha256
        ),
        test_pipeline_sha256=test_pipeline_hash,
        window_id=artifact.window_id,
        approval_id=receipt.approval_id,
        population_coverage=coverage,
    )


__all__ = [
    "APPROVE_ONE_SHOT_TEST_LABEL_HANDOFF",
    "MISSING_TEST_PREDICTION_POLICY",
    "MLBHROneShotTestEvaluationPlan",
    "MLBHROneShotTestEvaluationPolicy",
    "MLBHROneShotTestEvaluatorError",
    "MLBHRTestAccessApprovalReceipt",
    "MLBHRTestPredictionPopulationCoverage",
    "ONE_SHOT_TEST_EVALUATION_APPROVAL_SCOPE",
    "ONE_SHOT_TEST_EVALUATION_PLAN_ONLY",
    "ONE_SHOT_TEST_EVALUATOR_POLICY_VERSION",
    "RESULT_ARTIFACT_WRITE_POLICY",
    "TEST_ACCESS_APPROVAL_RECEIPT_SCHEMA_VERSION",
    "load_test_access_approval_receipt",
    "plan_one_shot_frozen_mlb_hr_test_evaluation",
    "test_access_approval_receipt_sha256",
]
