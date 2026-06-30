"""Immutable one-shot MLB HR test-result artifact contract.

This module validates and serializes results supplied by a future, separately
approved executor.  It does not open labels, calculate a metric, train or run
a model, generate a prediction, fetch data, or enable an operational or
wagering path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Final, Mapping
from uuid import uuid4

from courtvision.sports.mlb.training.hr_evaluation_contract import (
    ALLOWED_EVALUATION_METRIC_NAMES,
)
from courtvision.sports.mlb.training.hr_one_shot_test_evaluator import (
    MLBHROneShotTestEvaluationPlan,
    MLBHROneShotTestEvaluatorError,
    plan_one_shot_frozen_mlb_hr_test_evaluation,
)
from courtvision.sports.mlb.training.hr_prediction_artifact import (
    DEFAULT_MODEL_SPECIFICATION_PATH,
    IMMUTABLE_WRITE_POLICY,
)


TEST_RESULT_ARTIFACT_SCHEMA_VERSION: Final = (
    "mlb-hr-one-shot-test-result-artifact-v1"
)
TEST_RESULT_ARTIFACT_TYPE: Final = "mlb_hr_one_shot_test_result"
TEST_RESULT_ARTIFACT_FILENAME: Final = "mlb_hr_one_shot_test_result.json"
TEST_RESULT_ATTEMPT_STATUSES: Final = frozenset(
    {"complete", "inconclusive", "partial", "failed"}
)

_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_ROOT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "artifact_type",
        "mode",
        "split_id",
        "approval_receipt_sha256",
        "feature_pack_sha256",
        "temporal_split_plan_sha256",
        "fitted_preprocessing_artifact_sha256",
        "prediction_artifact_sha256",
        "pipeline_sha256",
        "allowed_metrics",
        "metric_results",
        "timestamp",
        "attempt_number",
        "attempt_status",
        "attempt_consumed",
        "no_rerun",
        "no_cherry_pick",
        "report_all_frozen_metrics",
        "research_only",
        "approval_status",
        "operational_use_enabled",
        "immutable",
        "write_policy",
        "artifact_sha256",
    }
)
_HASH_FIELDS: Final = (
    "approval_receipt_sha256",
    "feature_pack_sha256",
    "temporal_split_plan_sha256",
    "fitted_preprocessing_artifact_sha256",
    "prediction_artifact_sha256",
    "pipeline_sha256",
    "artifact_sha256",
)
_FORBIDDEN_PATH_COMPONENTS: Final = frozenset(
    {
        "bankroll",
        "bankrolls",
        "betting",
        "cache",
        "caches",
        "dashboard",
        "dashboards",
        "history",
        "histories",
        "kelly",
        "manual",
        "manualdata",
        "model",
        "models",
        "operational",
        "output",
        "outputs",
        "production",
        "pytestcache",
        "pycache",
        "runtime",
        "runtimeoutputs",
        "testoutputs",
        "wagering",
    }
)
_FORBIDDEN_FIELD_TOKENS: Final = frozenset(
    {
        "bankroll",
        "bankrolls",
        "bet",
        "bets",
        "betting",
        "edge",
        "elite",
        "ev",
        "kelly",
        "odds",
        "payout",
        "production",
        "profit",
        "roi",
        "sportsbook",
        "stake",
        "stakes",
        "staking",
        "wager",
        "wagers",
        "wagering",
    }
)


class MLBHRTestResultArtifactError(ValueError):
    """Raised when a one-shot test-result artifact must fail closed."""


@dataclass(frozen=True, slots=True)
class MLBHRTestResultArtifact:
    """Validated immutable view of one terminal one-shot result."""

    path: Path
    approval_receipt_sha256: str
    feature_pack_sha256: str
    temporal_split_plan_sha256: str
    fitted_preprocessing_artifact_sha256: str
    prediction_artifact_sha256: str
    pipeline_sha256: str
    allowed_metrics: tuple[str, ...]
    metric_results: Mapping[str, object]
    timestamp: datetime
    attempt_status: str
    artifact_sha256: str
    schema_version: str = TEST_RESULT_ARTIFACT_SCHEMA_VERSION
    artifact_type: str = TEST_RESULT_ARTIFACT_TYPE
    mode: str = "historical_research"
    split_id: str = "test"
    attempt_number: int = 1
    attempt_consumed: bool = True
    no_rerun: bool = True
    no_cherry_pick: bool = True
    report_all_frozen_metrics: bool = True
    research_only: bool = True
    approval_status: str = "not_approved"
    operational_use_enabled: bool = False
    immutable: bool = True
    write_policy: str = IMMUTABLE_WRITE_POLICY

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", self.path.resolve())
        object.__setattr__(self, "allowed_metrics", tuple(self.allowed_metrics))
        object.__setattr__(self, "metric_results", dict(self.metric_results))


@dataclass(frozen=True, slots=True)
class MLBHRTestResultWriteResult:
    """The one artifact created inside an isolated research staging folder."""

    output_dir: Path
    artifact_path: Path
    artifact: MLBHRTestResultArtifact

    def __post_init__(self) -> None:
        object.__setattr__(self, "output_dir", self.output_dir.resolve())
        object.__setattr__(self, "artifact_path", self.artifact_path.resolve())


def _canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MLBHRTestResultArtifactError(
            f"test-result artifact cannot be canonicalized: {exc}"
        ) from exc


def test_result_artifact_sha256(payload: Mapping[str, object]) -> str:
    """Hash canonical artifact content without its self-hash field."""

    content = dict(payload)
    content.pop("artifact_sha256", None)
    return hashlib.sha256(_canonical_json_bytes(content)).hexdigest()


def _reject_nonstandard_number(value: str) -> object:
    raise ValueError(f"non-standard JSON number is prohibited: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is prohibited: {key}")
        result[key] = value
    return result


def _read_json_object(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_nonstandard_number,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise MLBHRTestResultArtifactError(
            f"could not read test-result artifact {path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise MLBHRTestResultArtifactError(
            "test-result artifact must contain a JSON object"
        )
    return payload


def _field_tokens(field_name: str) -> frozenset[str]:
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", field_name)
    return frozenset(
        token
        for token in re.split(r"[^a-z0-9]+", snake.casefold())
        if token
    )


def _forbidden_field_reason(field_name: str) -> str | None:
    tokens = _field_tokens(field_name)
    if "production" in tokens:
        return "production"
    if "kelly" in tokens:
        return "Kelly"
    if "ev" in tokens or {"expected", "value"}.issubset(tokens):
        return "EV"
    if tokens & (_FORBIDDEN_FIELD_TOKENS - {"ev", "kelly", "production"}):
        return "betting/wagering"
    return None


def _reject_forbidden_fields(value: object, *, location: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            reason = _forbidden_field_reason(key)
            if reason is not None:
                raise MLBHRTestResultArtifactError(
                    f"{location}.{key} is a prohibited {reason} field"
                )
            _reject_forbidden_fields(child, location=f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_fields(child, location=f"{location}[{index}]")


def _normalized_path_components(path: Path) -> frozenset[str]:
    return frozenset(
        re.sub(r"[^a-z0-9]+", "", part.casefold()) for part in path.parts
    )


def _reject_forbidden_result_path(path: Path) -> None:
    forbidden = sorted(
        _normalized_path_components(path) & _FORBIDDEN_PATH_COMPONENTS
    )
    if forbidden:
        raise MLBHRTestResultArtifactError(
            "test-result artifacts cannot use operational, manual, or cache "
            f"paths ({', '.join(forbidden)}): {path}"
        )


def _validate_output_directory(path: str | Path) -> tuple[Path, bool]:
    output_dir = Path(path).expanduser().resolve()
    _reject_forbidden_result_path(output_dir)
    output_path = output_dir / TEST_RESULT_ARTIFACT_FILENAME
    if output_path.exists():
        raise MLBHRTestResultArtifactError(
            f"test-result artifact already exists and cannot be overwritten: "
            f"{output_path}"
        )
    if output_dir.exists():
        if not output_dir.is_dir():
            raise MLBHRTestResultArtifactError(
                f"test-result staging path is not a directory: {output_dir}"
            )
        if any(output_dir.iterdir()):
            raise MLBHRTestResultArtifactError(
                "test-result staging directory must be isolated and empty: "
                f"{output_dir}"
            )
        return output_dir, False
    if not output_dir.parent.is_dir():
        raise MLBHRTestResultArtifactError(
            "test-result staging parent directory does not exist: "
            f"{output_dir.parent}"
        )
    return output_dir, True


def validate_mlb_hr_test_result_staging_dir(path: str | Path) -> Path:
    """Validate a prospective isolated result directory without creating it."""

    output_dir, _ = _validate_output_directory(path)
    return output_dir


def _require_exact_schema(payload: Mapping[str, object]) -> None:
    missing = sorted(_ROOT_FIELDS - payload.keys())
    extras = sorted(payload.keys() - _ROOT_FIELDS)
    if missing:
        raise MLBHRTestResultArtifactError(
            "test-result artifact is missing required fields: "
            + ", ".join(missing)
        )
    if extras:
        raise MLBHRTestResultArtifactError(
            "test-result artifact contains unsupported fields: "
            + ", ".join(str(value) for value in extras)
        )


def _require_sha256(payload: Mapping[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise MLBHRTestResultArtifactError(
            f"test-result artifact {field_name} must be lowercase SHA-256"
        )
    return value


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MLBHRTestResultArtifactError(
            "test-result timestamp must be an ISO-8601 timezone-aware datetime"
        )
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise MLBHRTestResultArtifactError(
            "test-result timestamp must be an ISO-8601 timezone-aware datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MLBHRTestResultArtifactError(
            "test-result timestamp must be timezone-aware"
        )
    return parsed


def _copy_metric_results(value: object, *, attempt_status: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise MLBHRTestResultArtifactError(
            "test-result metric_results must be a JSON object"
        )
    observed = frozenset(str(name) for name in value)
    expected = frozenset(ALLOWED_EVALUATION_METRIC_NAMES)
    missing = sorted(expected - observed)
    extras = sorted(observed - expected)
    if missing or extras:
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extras:
            details.append("forbidden=" + ",".join(extras))
        raise MLBHRTestResultArtifactError(
            "test-result metric_results must contain allowed metrics only "
            "(" + "; ".join(details) + ")"
        )
    copied = {name: value[name] for name in ALLOWED_EVALUATION_METRIC_NAMES}
    _reject_forbidden_fields(copied, location="test-result metric_results")
    _canonical_json_bytes(copied)
    for metric_name, result in copied.items():
        if isinstance(result, float) and not math.isfinite(result):
            raise MLBHRTestResultArtifactError(
                f"test-result metric {metric_name} must be finite or null"
            )
    present = sum(result is not None for result in copied.values())
    if attempt_status == "complete" and present != len(copied):
        raise MLBHRTestResultArtifactError(
            "complete test-result artifacts require every frozen metric"
        )
    if attempt_status == "partial" and present not in range(1, len(copied)):
        raise MLBHRTestResultArtifactError(
            "partial test-result artifacts require some but not all metrics"
        )
    if attempt_status == "failed" and present:
        raise MLBHRTestResultArtifactError(
            "failed test-result artifacts cannot contain computed metrics; "
            "use partial when any metric result exists"
        )
    return copied


def _validate_fixed_contract(payload: Mapping[str, object]) -> tuple[datetime, str]:
    fixed_values: Mapping[str, object] = {
        "schema_version": TEST_RESULT_ARTIFACT_SCHEMA_VERSION,
        "artifact_type": TEST_RESULT_ARTIFACT_TYPE,
        "mode": "historical_research",
        "split_id": "test",
        "allowed_metrics": list(ALLOWED_EVALUATION_METRIC_NAMES),
        "attempt_number": 1,
        "attempt_consumed": True,
        "no_rerun": True,
        "no_cherry_pick": True,
        "report_all_frozen_metrics": True,
        "research_only": True,
        "approval_status": "not_approved",
        "operational_use_enabled": False,
        "immutable": True,
        "write_policy": IMMUTABLE_WRITE_POLICY,
    }
    invalid = [
        name for name, expected in fixed_values.items() if payload.get(name) != expected
    ]
    if invalid:
        raise MLBHRTestResultArtifactError(
            "test-result artifact cannot relax its one-shot research contract: "
            + ", ".join(invalid)
        )
    attempt_status = payload.get("attempt_status")
    if attempt_status not in TEST_RESULT_ATTEMPT_STATUSES:
        raise MLBHRTestResultArtifactError(
            "test-result attempt_status must be complete, inconclusive, "
            "partial, or failed"
        )
    return _parse_timestamp(payload.get("timestamp")), str(attempt_status)


def _validated_plan(
    *,
    feature_pack_path: str | Path,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
    test_prediction_artifact_path: str | Path,
    test_access_approval_receipt_path: str | Path,
    model_specification_path: str | Path,
) -> MLBHROneShotTestEvaluationPlan:
    try:
        return plan_one_shot_frozen_mlb_hr_test_evaluation(
            feature_pack_path=feature_pack_path,
            temporal_split_plan_path=temporal_split_plan_path,
            fitted_preprocessing_artifact_path=(
                fitted_preprocessing_artifact_path
            ),
            test_prediction_artifact_path=test_prediction_artifact_path,
            test_access_approval_receipt_path=(
                test_access_approval_receipt_path
            ),
            model_specification_path=model_specification_path,
        )
    except MLBHROneShotTestEvaluatorError as exc:
        raise MLBHRTestResultArtifactError(
            f"test-result input contract failed: {exc}"
        ) from exc


def _validate_hash_bindings(
    payload: Mapping[str, object],
    plan: MLBHROneShotTestEvaluationPlan,
) -> dict[str, str]:
    hashes = {name: _require_sha256(payload, name) for name in _HASH_FIELDS}
    expected = {
        "approval_receipt_sha256": plan.test_access_approval_receipt_sha256,
        "feature_pack_sha256": plan.feature_pack_sha256,
        "temporal_split_plan_sha256": plan.temporal_split_plan_sha256,
        "fitted_preprocessing_artifact_sha256": (
            plan.fitted_preprocessing_artifact_sha256
        ),
        "prediction_artifact_sha256": plan.test_prediction_artifact_sha256,
        "pipeline_sha256": plan.test_pipeline_sha256,
    }
    mismatches = [
        name
        for name, expected_hash in expected.items()
        if hashes[name] != expected_hash
    ]
    if mismatches:
        raise MLBHRTestResultArtifactError(
            "test-result artifact input hash mismatch: " + ", ".join(mismatches)
        )
    if hashes["artifact_sha256"] != test_result_artifact_sha256(payload):
        raise MLBHRTestResultArtifactError(
            "test-result artifact content SHA-256 does not match"
        )
    return hashes


def _artifact_from_payload(
    *,
    path: Path,
    payload: Mapping[str, object],
    plan: MLBHROneShotTestEvaluationPlan,
) -> MLBHRTestResultArtifact:
    _reject_forbidden_fields(payload, location="test-result artifact")
    _require_exact_schema(payload)
    timestamp, attempt_status = _validate_fixed_contract(payload)
    metric_results = _copy_metric_results(
        payload.get("metric_results"), attempt_status=attempt_status
    )
    hashes = _validate_hash_bindings(payload, plan)
    return MLBHRTestResultArtifact(
        path=path,
        approval_receipt_sha256=hashes["approval_receipt_sha256"],
        feature_pack_sha256=hashes["feature_pack_sha256"],
        temporal_split_plan_sha256=hashes["temporal_split_plan_sha256"],
        fitted_preprocessing_artifact_sha256=(
            hashes["fitted_preprocessing_artifact_sha256"]
        ),
        prediction_artifact_sha256=hashes["prediction_artifact_sha256"],
        pipeline_sha256=hashes["pipeline_sha256"],
        allowed_metrics=tuple(str(value) for value in payload["allowed_metrics"]),
        metric_results=metric_results,
        timestamp=timestamp,
        attempt_status=attempt_status,
        artifact_sha256=hashes["artifact_sha256"],
    )


def _payload_from_plan(
    *,
    plan: MLBHROneShotTestEvaluationPlan,
    attempt_status: str,
    metric_results: Mapping[str, object],
) -> dict[str, object]:
    if attempt_status not in TEST_RESULT_ATTEMPT_STATUSES:
        raise MLBHRTestResultArtifactError(
            "test-result attempt_status must be complete, inconclusive, "
            "partial, or failed"
        )
    copied_results = _copy_metric_results(
        metric_results, attempt_status=attempt_status
    )
    payload: dict[str, object] = {
        "schema_version": TEST_RESULT_ARTIFACT_SCHEMA_VERSION,
        "artifact_type": TEST_RESULT_ARTIFACT_TYPE,
        "mode": "historical_research",
        "split_id": "test",
        "approval_receipt_sha256": plan.test_access_approval_receipt_sha256,
        "feature_pack_sha256": plan.feature_pack_sha256,
        "temporal_split_plan_sha256": plan.temporal_split_plan_sha256,
        "fitted_preprocessing_artifact_sha256": (
            plan.fitted_preprocessing_artifact_sha256
        ),
        "prediction_artifact_sha256": plan.test_prediction_artifact_sha256,
        "pipeline_sha256": plan.test_pipeline_sha256,
        "allowed_metrics": list(ALLOWED_EVALUATION_METRIC_NAMES),
        "metric_results": copied_results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "attempt_number": 1,
        "attempt_status": attempt_status,
        "attempt_consumed": True,
        "no_rerun": True,
        "no_cherry_pick": True,
        "report_all_frozen_metrics": True,
        "research_only": True,
        "approval_status": "not_approved",
        "operational_use_enabled": False,
        "immutable": True,
        "write_policy": IMMUTABLE_WRITE_POLICY,
        "artifact_sha256": "pending",
    }
    payload["artifact_sha256"] = test_result_artifact_sha256(payload)
    return payload


def _publish_atomic_no_overwrite(path: Path, payload: Mapping[str, object]) -> None:
    temporary_path = path.parent / (
        f".courtvision-one-shot-test-result-{uuid4().hex}.json"
    )
    encoded = json.dumps(payload, indent=2, allow_nan=False) + "\n"
    fd: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        fd = os.open(temporary_path, flags, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(encoded.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError as exc:
            raise MLBHRTestResultArtifactError(
                f"test-result artifact already exists and cannot be overwritten: "
                f"{path}"
            ) from exc
        except OSError as exc:
            raise MLBHRTestResultArtifactError(
                f"could not atomically publish test-result artifact {path}: {exc}"
            ) from exc
    except OSError as exc:
        raise MLBHRTestResultArtifactError(
            f"could not create test-result artifact {path}: {exc}"
        ) from exc
    finally:
        if fd is not None:
            os.close(fd)
        if temporary_path.exists():
            temporary_path.unlink()


def load_mlb_hr_test_result_artifact(
    artifact_path: str | Path,
    *,
    feature_pack_path: str | Path,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
    test_prediction_artifact_path: str | Path,
    test_access_approval_receipt_path: str | Path,
    model_specification_path: str | Path = DEFAULT_MODEL_SPECIFICATION_PATH,
) -> MLBHRTestResultArtifact:
    """Load and hash-validate one existing result without calculating metrics."""

    source = Path(artifact_path).expanduser().resolve()
    _reject_forbidden_result_path(source)
    if source.name != TEST_RESULT_ARTIFACT_FILENAME or not source.is_file():
        raise MLBHRTestResultArtifactError(
            "test-result artifact must be the existing canonical staging file: "
            f"{source}"
        )
    plan = _validated_plan(
        feature_pack_path=feature_pack_path,
        temporal_split_plan_path=temporal_split_plan_path,
        fitted_preprocessing_artifact_path=fitted_preprocessing_artifact_path,
        test_prediction_artifact_path=test_prediction_artifact_path,
        test_access_approval_receipt_path=test_access_approval_receipt_path,
        model_specification_path=model_specification_path,
    )
    payload = _read_json_object(source)
    return _artifact_from_payload(path=source, payload=payload, plan=plan)


def write_mlb_hr_test_result_artifact(
    *,
    feature_pack_path: str | Path,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
    test_prediction_artifact_path: str | Path,
    test_access_approval_receipt_path: str | Path,
    output_staging_dir: str | Path,
    attempt_status: str,
    metric_results: Mapping[str, object],
    model_specification_path: str | Path = DEFAULT_MODEL_SPECIFICATION_PATH,
) -> MLBHRTestResultWriteResult:
    """Create exactly one supplied-result artifact; never calculate metrics."""

    output_dir, create_output_dir = _validate_output_directory(
        output_staging_dir
    )
    plan = _validated_plan(
        feature_pack_path=feature_pack_path,
        temporal_split_plan_path=temporal_split_plan_path,
        fitted_preprocessing_artifact_path=fitted_preprocessing_artifact_path,
        test_prediction_artifact_path=test_prediction_artifact_path,
        test_access_approval_receipt_path=test_access_approval_receipt_path,
        model_specification_path=model_specification_path,
    )
    payload = _payload_from_plan(
        plan=plan,
        attempt_status=attempt_status,
        metric_results=metric_results,
    )
    output_path = output_dir / TEST_RESULT_ARTIFACT_FILENAME
    _artifact_from_payload(path=output_path, payload=payload, plan=plan)

    created_output_dir = False
    try:
        if create_output_dir:
            output_dir.mkdir()
            created_output_dir = True
        if any(output_dir.iterdir()):
            raise MLBHRTestResultArtifactError(
                "test-result staging directory must remain isolated and empty: "
                f"{output_dir}"
            )
        _publish_atomic_no_overwrite(output_path, payload)
    except Exception:
        if created_output_dir and output_dir.exists():
            try:
                output_dir.rmdir()
            except OSError:
                pass
        raise

    artifact = load_mlb_hr_test_result_artifact(
        output_path,
        feature_pack_path=feature_pack_path,
        temporal_split_plan_path=temporal_split_plan_path,
        fitted_preprocessing_artifact_path=fitted_preprocessing_artifact_path,
        test_prediction_artifact_path=test_prediction_artifact_path,
        test_access_approval_receipt_path=test_access_approval_receipt_path,
        model_specification_path=model_specification_path,
    )
    return MLBHRTestResultWriteResult(
        output_dir=output_dir,
        artifact_path=output_path,
        artifact=artifact,
    )


__all__ = [
    "MLBHRTestResultArtifact",
    "MLBHRTestResultArtifactError",
    "MLBHRTestResultWriteResult",
    "TEST_RESULT_ARTIFACT_FILENAME",
    "TEST_RESULT_ARTIFACT_SCHEMA_VERSION",
    "TEST_RESULT_ARTIFACT_TYPE",
    "TEST_RESULT_ATTEMPT_STATUSES",
    "load_mlb_hr_test_result_artifact",
    "test_result_artifact_sha256",
    "validate_mlb_hr_test_result_staging_dir",
    "write_mlb_hr_test_result_artifact",
]
