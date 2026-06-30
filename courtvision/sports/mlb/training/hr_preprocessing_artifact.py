"""Immutable fitted-preprocessing artifacts for MLB HR research.

This module serializes only train-fitted preprocessing parameters produced by
the sealed planner.  It cannot train a model, transform rows, make a
prediction, run a backtest, fetch data, or enable a production/wagering gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Final, Mapping
from uuid import uuid4

from courtvision.sports.mlb.training.hr_preprocessing_plan import (
    MISSING_CATEGORY_TOKEN,
    MLBHRPreprocessingPlan,
    MLBHRPreprocessingPlanningError,
    PREPROCESSING_PLAN_SCHEMA_VERSION,
    PREPROCESSING_POLICY_VERSION,
    RARE_CATEGORY_MIN_TRAIN_COUNT,
    RARE_CATEGORY_TOKEN,
    TEMPORAL_SPLIT_ARTIFACT_VERSION,
    UNKNOWN_CATEGORY_TOKEN,
    plan_mlb_hr_preprocessing,
)


FITTED_PREPROCESSING_ARTIFACT_SCHEMA_VERSION: Final = (
    "mlb-hr-fitted-preprocessing-artifact-v1"
)
FITTED_PREPROCESSING_ARTIFACT_FILENAME: Final = (
    "mlb_hr_fitted_preprocessing.json"
)
FITTED_PREPROCESSING_CODE_VERSION: Final = (
    "courtvision-0.1.0:mlb-hr-fitted-preprocessing-v1"
)

_GATE_NAMES: Final = (
    "model_training_enabled",
    "backtesting_enabled",
    "predictions_enabled",
    "live_fetching_enabled",
    "betting_enabled",
    "eligible_for_betting",
    "ev_enabled",
    "kelly_eligible",
    "elite_enabled",
    "staking_enabled",
    "production_enabled",
    "production_approved",
)
_REQUIRED_FIELDS: Final = frozenset(
    {
        "schema_version",
        "policy_version",
        "plan_schema_version",
        "mode",
        "feature_pack_sha256",
        "split_plan_sha256",
        "split_source_kind",
        "split_hash_kind",
        "fit_split",
        "validation_transform_only",
        "test_transform_only",
        "feature_firewall_valid",
        "temporal_split_valid",
        "train_date_range",
        "train_row_count",
        "numeric_medians",
        "missing_indicators",
        "categorical_vocabularies",
        "rare_category_mappings",
        "category_policy",
        "created_at",
        "code_version",
        "research_only",
        "approval_status",
        "artifact_sha256",
        *_GATE_NAMES,
    }
)
_FORBIDDEN_PATH_COMPONENTS: Final = frozenset(
    {
        "cache",
        "caches",
        "dashboard",
        "dashboards",
        "histories",
        "history",
        "manual",
        "manualdata",
        "model",
        "models",
        "output",
        "outputs",
        "production",
        "pytestcache",
        "pycache",
        "runtime",
        "runtimeoutputs",
        "testoutputs",
    }
)
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


class MLBHRFittedPreprocessingArtifactError(ValueError):
    """Raised when a fitted preprocessing artifact is unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class MLBHRFittedPreprocessingArtifact:
    """Validated research-only fitted preprocessing parameters."""

    path: Path
    feature_pack_sha256: str
    split_plan_sha256: str
    split_source_kind: str
    split_hash_kind: str
    train_date_start: date
    train_date_end: date
    train_row_count: int
    numeric_medians: Mapping[str, float]
    missing_indicators: Mapping[str, bool]
    categorical_vocabularies: Mapping[str, tuple[str, ...]]
    rare_category_mappings: Mapping[str, Mapping[str, str]]
    category_policy: Mapping[str, object]
    created_at: datetime
    code_version: str
    artifact_sha256: str
    schema_version: str = FITTED_PREPROCESSING_ARTIFACT_SCHEMA_VERSION
    policy_version: str = PREPROCESSING_POLICY_VERSION
    plan_schema_version: str = PREPROCESSING_PLAN_SCHEMA_VERSION
    mode: str = "historical_research"
    research_only: bool = True
    approval_status: str = "not_approved"
    model_training_enabled: bool = False
    backtesting_enabled: bool = False
    predictions_enabled: bool = False
    live_fetching_enabled: bool = False
    betting_enabled: bool = False
    eligible_for_betting: bool = False
    ev_enabled: bool = False
    kelly_eligible: bool = False
    elite_enabled: bool = False
    staking_enabled: bool = False
    production_enabled: bool = False
    production_approved: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", self.path.resolve())


@dataclass(frozen=True, slots=True)
class MLBHRFittedPreprocessingWriteResult:
    """The single artifact created in an isolated staging directory."""

    output_dir: Path
    artifact_path: Path
    artifact: MLBHRFittedPreprocessingArtifact

    def __post_init__(self) -> None:
        object.__setattr__(self, "output_dir", self.output_dir.resolve())
        object.__setattr__(self, "artifact_path", self.artifact_path.resolve())


def _canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _payload_sha256(payload: Mapping[str, object]) -> str:
    content = dict(payload)
    content.pop("artifact_sha256", None)
    return hashlib.sha256(_canonical_json_bytes(content)).hexdigest()


def _file_sha256(path: str | Path, label: str) -> str:
    source = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    try:
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise MLBHRFittedPreprocessingArtifactError(
            f"could not hash {label} {source}: {exc}"
        ) from exc
    return digest.hexdigest()


def _canonical_split_payload(plan: MLBHRPreprocessingPlan) -> dict[str, object]:
    split_plan = plan.split_plan
    return {
        "schema_version": TEMPORAL_SPLIT_ARTIFACT_VERSION,
        "split_method": split_plan.split_method,
        "readiness_verdict": split_plan.readiness_verdict,
        "approval_status": split_plan.approval_status,
        "train": [value.isoformat() for value in split_plan.train.game_dates],
        "validation": [
            value.isoformat() for value in split_plan.validation.game_dates
        ],
        "test": [value.isoformat() for value in split_plan.test.game_dates],
    }


def _split_hash(plan: MLBHRPreprocessingPlan) -> tuple[str, str]:
    if plan.split_source_kind == "temporal_split_plan":
        return (
            _file_sha256(plan.split_source_path, "temporal split plan"),
            "source_file_sha256",
        )
    if plan.split_source_kind == "staged_pack":
        return (
            hashlib.sha256(
                _canonical_json_bytes(_canonical_split_payload(plan))
            ).hexdigest(),
            "canonical_derived_plan_sha256",
        )
    raise MLBHRFittedPreprocessingArtifactError(
        f"unsupported split source kind: {plan.split_source_kind}"
    )


def _category_policy() -> dict[str, object]:
    return {
        "normalization": "strip_whitespace_empty_as_missing",
        "missing_category_policy": "map_to_missing_token",
        "missing_token": MISSING_CATEGORY_TOKEN,
        "rare_category_policy": "map_train_count_below_threshold_to_rare_token",
        "rare_min_train_count": RARE_CATEGORY_MIN_TRAIN_COUNT,
        "rare_token": RARE_CATEGORY_TOKEN,
        "unknown_category_policy": "map_non_train_category_to_unknown_token",
        "unknown_token": UNKNOWN_CATEGORY_TOKEN,
        "encoding": "train_vocabulary_one_hot",
    }


def _payload_from_plan(
    plan: MLBHRPreprocessingPlan,
    *,
    created_at: str,
) -> dict[str, object]:
    split_plan_sha256, split_hash_kind = _split_hash(plan)
    numeric_medians = {
        summary.column: summary.train_median for summary in plan.numeric_summaries
    }
    missing_indicators = {
        summary.column: summary.missing_indicator
        for summary in plan.numeric_summaries
    }
    categorical_vocabularies = {
        summary.column: [
            *summary.retained_train_categories,
            summary.missing_token,
            summary.rare_token,
            summary.unknown_token,
        ]
        for summary in plan.categorical_summaries
    }
    rare_category_mappings = {
        summary.column: {
            category: summary.rare_token
            for category in summary.rare_train_categories
        }
        for summary in plan.categorical_summaries
    }
    payload: dict[str, object] = {
        "schema_version": FITTED_PREPROCESSING_ARTIFACT_SCHEMA_VERSION,
        "policy_version": plan.policy_version,
        "plan_schema_version": plan.schema_version,
        "mode": "historical_research",
        "feature_pack_sha256": plan.feature_pack_sha256,
        "split_plan_sha256": split_plan_sha256,
        "split_source_kind": plan.split_source_kind,
        "split_hash_kind": split_hash_kind,
        "fit_split": plan.fit_split,
        "validation_transform_only": plan.validation_transform_only,
        "test_transform_only": plan.test_transform_only,
        "feature_firewall_valid": plan.feature_firewall_valid,
        "temporal_split_valid": plan.temporal_split_valid,
        "train_date_range": {
            "start": plan.split_plan.train.start.isoformat(),
            "end": plan.split_plan.train.end.isoformat(),
        },
        "train_row_count": plan.train_row_count,
        "numeric_medians": numeric_medians,
        "missing_indicators": missing_indicators,
        "categorical_vocabularies": categorical_vocabularies,
        "rare_category_mappings": rare_category_mappings,
        "category_policy": _category_policy(),
        "created_at": created_at,
        "code_version": FITTED_PREPROCESSING_CODE_VERSION,
        "research_only": True,
        "approval_status": "not_approved",
        **{name: False for name in _GATE_NAMES},
    }
    payload["artifact_sha256"] = _payload_sha256(payload)
    return payload


def _read_json_object(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MLBHRFittedPreprocessingArtifactError(
            f"could not read fitted preprocessing artifact {path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing artifact must contain a JSON object"
        )
    return payload


def _require_exact_schema(payload: Mapping[str, object]) -> None:
    missing = sorted(_REQUIRED_FIELDS - payload.keys())
    if missing:
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing artifact is missing required field(s): "
            + ", ".join(missing)
        )
    unknown = sorted(payload.keys() - _REQUIRED_FIELDS)
    if unknown:
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing artifact contains unsupported field(s): "
            + ", ".join(unknown)
        )


def _require_safety_contract(payload: Mapping[str, object]) -> None:
    versions = {
        "schema_version": FITTED_PREPROCESSING_ARTIFACT_SCHEMA_VERSION,
        "policy_version": PREPROCESSING_POLICY_VERSION,
        "plan_schema_version": PREPROCESSING_PLAN_SCHEMA_VERSION,
    }
    for field_name, expected in versions.items():
        if payload.get(field_name) != expected:
            raise MLBHRFittedPreprocessingArtifactError(
                f"unsupported fitted preprocessing {field_name}"
            )
    required_values = {
        "mode": "historical_research",
        "fit_split": "train",
        "validation_transform_only": True,
        "test_transform_only": True,
        "feature_firewall_valid": True,
        "temporal_split_valid": True,
        "research_only": True,
        "approval_status": "not_approved",
        "code_version": FITTED_PREPROCESSING_CODE_VERSION,
    }
    for field_name, expected in required_values.items():
        if payload.get(field_name) != expected:
            raise MLBHRFittedPreprocessingArtifactError(
                f"fitted preprocessing artifact requires {field_name}={expected!r}"
            )
    enabled = [name for name in _GATE_NAMES if payload.get(name) is not False]
    if enabled:
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing artifact must explicitly disable gates: "
            + ", ".join(enabled)
        )


def _require_sha256(payload: Mapping[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise MLBHRFittedPreprocessingArtifactError(
            f"fitted preprocessing {field_name} must be a lowercase SHA-256"
        )
    return value


def _parse_created_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing created_at must be ISO-8601 text"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing created_at must be valid ISO-8601 text"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing created_at must be timezone-aware"
        )
    return parsed


def _parse_train_range(value: object) -> tuple[date, date]:
    if not isinstance(value, Mapping) or set(value) != {"start", "end"}:
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing train_date_range requires start and end"
        )
    try:
        start = date.fromisoformat(str(value["start"]))
        end = date.fromisoformat(str(value["end"]))
    except ValueError as exc:
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing train_date_range must contain ISO dates"
        ) from exc
    if start > end:
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing train_date_range is reversed"
        )
    return start, end


def _parse_numeric_medians(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing numeric_medians must be an object"
        )
    parsed: dict[str, float] = {}
    for column, raw_value in value.items():
        if (
            not isinstance(column, str)
            or not column
            or isinstance(raw_value, bool)
            or not isinstance(raw_value, (int, float))
            or not math.isfinite(float(raw_value))
        ):
            raise MLBHRFittedPreprocessingArtifactError(
                "fitted preprocessing numeric_medians contains an invalid value"
            )
        parsed[column] = float(raw_value)
    return parsed


def _parse_missing_indicators(value: object) -> dict[str, bool]:
    if not isinstance(value, Mapping) or any(
        not isinstance(column, str) or enabled is not True
        for column, enabled in value.items()
    ):
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing missing_indicators must enable each numeric column"
        )
    return {str(column): True for column in value}


def _parse_vocabularies(value: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing categorical_vocabularies must be an object"
        )
    parsed: dict[str, tuple[str, ...]] = {}
    for column, raw_vocabulary in value.items():
        if not isinstance(column, str) or not isinstance(raw_vocabulary, list):
            raise MLBHRFittedPreprocessingArtifactError(
                "fitted preprocessing categorical_vocabularies is malformed"
            )
        vocabulary = tuple(raw_vocabulary)
        if (
            not vocabulary
            or any(not isinstance(item, str) or not item for item in vocabulary)
            or len(vocabulary) != len(set(vocabulary))
            or not {MISSING_CATEGORY_TOKEN, RARE_CATEGORY_TOKEN, UNKNOWN_CATEGORY_TOKEN}
            <= set(vocabulary)
        ):
            raise MLBHRFittedPreprocessingArtifactError(
                f"invalid categorical vocabulary for {column}"
            )
        parsed[column] = vocabulary
    return parsed


def _parse_rare_mappings(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping):
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing rare_category_mappings must be an object"
        )
    parsed: dict[str, dict[str, str]] = {}
    for column, raw_mapping in value.items():
        if not isinstance(column, str) or not isinstance(raw_mapping, Mapping):
            raise MLBHRFittedPreprocessingArtifactError(
                "fitted preprocessing rare_category_mappings is malformed"
            )
        if any(
            not isinstance(category, str) or token != RARE_CATEGORY_TOKEN
            for category, token in raw_mapping.items()
        ):
            raise MLBHRFittedPreprocessingArtifactError(
                f"invalid rare-category mapping for {column}"
            )
        parsed[column] = dict(raw_mapping)
    return parsed


def _require_expected_plan_fields(
    payload: Mapping[str, object],
    plan: MLBHRPreprocessingPlan,
) -> None:
    expected = _payload_from_plan(plan, created_at=str(payload["created_at"]))
    for field_name in (
        "split_source_kind",
        "split_hash_kind",
        "train_date_range",
        "train_row_count",
        "numeric_medians",
        "missing_indicators",
        "categorical_vocabularies",
        "rare_category_mappings",
        "category_policy",
    ):
        if payload.get(field_name) != expected[field_name]:
            raise MLBHRFittedPreprocessingArtifactError(
                f"fitted preprocessing {field_name} does not match the sealed plan"
            )


def load_fitted_preprocessing_artifact(
    artifact_path: str | Path,
    *,
    feature_pack_path: str | Path,
    temporal_split_plan_path: str | Path | None = None,
    staged_pack_path: str | Path | None = None,
) -> MLBHRFittedPreprocessingArtifact:
    """Load and bind an artifact to the exact feature pack and split plan."""

    source = Path(artifact_path).expanduser().resolve()
    payload = _read_json_object(source)
    _require_exact_schema(payload)
    _require_safety_contract(payload)

    feature_hash = _require_sha256(payload, "feature_pack_sha256")
    split_hash = _require_sha256(payload, "split_plan_sha256")
    artifact_hash = _require_sha256(payload, "artifact_sha256")
    if feature_hash != _file_sha256(feature_pack_path, "feature pack"):
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing feature-pack SHA-256 does not match"
        )
    if temporal_split_plan_path is not None and split_hash != _file_sha256(
        temporal_split_plan_path, "temporal split plan"
    ):
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing split-plan SHA-256 does not match"
        )
    if artifact_hash != _payload_sha256(payload):
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing artifact content SHA-256 does not match"
        )

    created_at = _parse_created_at(payload["created_at"])
    train_start, train_end = _parse_train_range(payload["train_date_range"])
    train_row_count = payload["train_row_count"]
    if (
        isinstance(train_row_count, bool)
        or not isinstance(train_row_count, int)
        or train_row_count <= 0
    ):
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing train_row_count must be a positive integer"
        )
    numeric_medians = _parse_numeric_medians(payload["numeric_medians"])
    missing_indicators = _parse_missing_indicators(payload["missing_indicators"])
    categorical_vocabularies = _parse_vocabularies(
        payload["categorical_vocabularies"]
    )
    rare_category_mappings = _parse_rare_mappings(
        payload["rare_category_mappings"]
    )
    category_policy = payload["category_policy"]
    if (
        not isinstance(category_policy, Mapping)
        or dict(category_policy) != _category_policy()
    ):
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing category_policy is unsupported"
        )

    supplied_sources = sum(
        value is not None for value in (temporal_split_plan_path, staged_pack_path)
    )
    if supplied_sources != 1:
        raise MLBHRFittedPreprocessingArtifactError(
            "provide exactly one temporal_split_plan_path or staged_pack_path"
        )
    try:
        plan = plan_mlb_hr_preprocessing(
            feature_pack_path=feature_pack_path,
            temporal_split_plan_path=temporal_split_plan_path,
            staged_pack_path=staged_pack_path,
        )
    except MLBHRPreprocessingPlanningError as exc:
        raise MLBHRFittedPreprocessingArtifactError(
            f"sealed preprocessing plan validation failed: {exc}"
        ) from exc
    expected_split_hash, _ = _split_hash(plan)
    if split_hash != expected_split_hash:
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing split-plan SHA-256 does not match"
        )
    _require_expected_plan_fields(payload, plan)

    return MLBHRFittedPreprocessingArtifact(
        path=source,
        feature_pack_sha256=feature_hash,
        split_plan_sha256=split_hash,
        split_source_kind=str(payload["split_source_kind"]),
        split_hash_kind=str(payload["split_hash_kind"]),
        train_date_start=train_start,
        train_date_end=train_end,
        train_row_count=train_row_count,
        numeric_medians=numeric_medians,
        missing_indicators=missing_indicators,
        categorical_vocabularies=categorical_vocabularies,
        rare_category_mappings=rare_category_mappings,
        category_policy=dict(category_policy),
        created_at=created_at,
        code_version=str(payload["code_version"]),
        artifact_sha256=artifact_hash,
    )


def _validate_output_directory(path: str | Path) -> tuple[Path, bool]:
    output_dir = Path(path).expanduser().resolve()
    components = {
        re.sub(r"[^a-z0-9]+", "", part.casefold()) for part in output_dir.parts
    }
    forbidden = sorted(components & _FORBIDDEN_PATH_COMPONENTS)
    if forbidden:
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing staging output cannot be inside operational "
            f"folders ({', '.join(forbidden)}): {output_dir}"
        )
    if output_dir.exists():
        if not output_dir.is_dir():
            raise MLBHRFittedPreprocessingArtifactError(
                f"output staging path is not a directory: {output_dir}"
            )
        if any(output_dir.iterdir()):
            raise MLBHRFittedPreprocessingArtifactError(
                f"output staging directory must be empty: {output_dir}"
            )
        return output_dir, False
    if not output_dir.parent.is_dir():
        raise MLBHRFittedPreprocessingArtifactError(
            f"output staging parent directory does not exist: {output_dir.parent}"
        )
    return output_dir, True


def write_fitted_preprocessing_artifact(
    *,
    feature_pack_path: str | Path,
    output_staging_dir: str | Path,
    temporal_split_plan_path: str | Path | None = None,
    staged_pack_path: str | Path | None = None,
) -> MLBHRFittedPreprocessingWriteResult:
    """Write one sealed parameter artifact to an explicit isolated directory."""

    output_dir, create_output_dir = _validate_output_directory(output_staging_dir)
    try:
        plan = plan_mlb_hr_preprocessing(
            feature_pack_path=feature_pack_path,
            temporal_split_plan_path=temporal_split_plan_path,
            staged_pack_path=staged_pack_path,
        )
    except MLBHRPreprocessingPlanningError as exc:
        raise MLBHRFittedPreprocessingArtifactError(str(exc)) from exc

    if plan.split_source_kind == "staged_pack" and output_dir.is_relative_to(
        plan.split_source_path
    ):
        raise MLBHRFittedPreprocessingArtifactError(
            "fitted preprocessing output must be isolated from the staged input pack"
        )

    payload = _payload_from_plan(
        plan,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    output_path = output_dir / FITTED_PREPROCESSING_ARTIFACT_FILENAME
    temporary_path: Path | None = None
    created_output_dir = False
    succeeded = False
    try:
        if create_output_dir:
            output_dir.mkdir()
            created_output_dir = True
        temporary_path = output_dir / (
            f".courtvision-fitted-preprocessing-{uuid4().hex}.json"
        )
        temporary_path.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary_path.replace(output_path)
        temporary_path = None
        artifact = load_fitted_preprocessing_artifact(
            output_path,
            feature_pack_path=feature_pack_path,
            temporal_split_plan_path=temporal_split_plan_path,
            staged_pack_path=staged_pack_path,
        )
        succeeded = True
        return MLBHRFittedPreprocessingWriteResult(
            output_dir=output_dir,
            artifact_path=output_path,
            artifact=artifact,
        )
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
        if not succeeded and output_path.exists():
            output_path.unlink()
        if not succeeded and created_output_dir and output_dir.exists():
            try:
                output_dir.rmdir()
            except OSError:
                pass


__all__ = [
    "FITTED_PREPROCESSING_ARTIFACT_FILENAME",
    "FITTED_PREPROCESSING_ARTIFACT_SCHEMA_VERSION",
    "FITTED_PREPROCESSING_CODE_VERSION",
    "MLBHRFittedPreprocessingArtifact",
    "MLBHRFittedPreprocessingArtifactError",
    "MLBHRFittedPreprocessingWriteResult",
    "load_fitted_preprocessing_artifact",
    "write_fitted_preprocessing_artifact",
]
