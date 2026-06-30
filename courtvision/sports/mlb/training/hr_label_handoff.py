"""Sealed label handoff validation for future MLB HR research.

The validator reads existing local research artifacts, verifies the binary
target and split-level distribution, and returns aggregate-only access rules.
It never returns row-level labels and contains no fitting, prediction,
evaluation, fetching, wagering, approval, or artifact-writing operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Final, Mapping, Sequence

from courtvision.sports.mlb.training.hr_feature_allowlist import (
    MLBHRFeatureFieldClass,
    classify_mlb_hr_research_field,
)
from courtvision.sports.mlb.training.hr_preprocessing_artifact import (
    MLBHRFittedPreprocessingArtifactError,
    load_fitted_preprocessing_artifact,
)
from courtvision.sports.mlb.training.hr_preprocessing_plan import (
    MLBHRPreprocessingPlanningError,
    plan_mlb_hr_preprocessing,
)
from courtvision.sports.mlb.training.hr_label_custody import (
    MLBHRLabelCustodyError,
    MLBHRLabelOpeningAuthorization,
    assert_model_visible_feature_pack_label_free,
    open_mlb_hr_label_custody_split,
    resolve_label_custody_path,
    validate_mlb_hr_label_custody,
)


LABEL_HANDOFF_CONTRACT_VERSION: Final = "mlb-hr-sealed-label-handoff-v1"
LABEL_COLUMN: Final = "is_home_run"
SPLIT_NAMES: Final = ("train", "validation", "test")
SEALED: Final = "sealed"
FITTING_ONLY: Final = "fitting_only"
EVALUATION_ONLY: Final = "evaluation_only"

_RESEARCH_MODE: Final = "historical_research"
_NOT_APPROVED: Final = "not_approved"
_PROHIBITED_GATE_NAMES: Final = (
    "execution_authorized",
    "model_training_enabled",
    "preprocessing_transform_enabled",
    "backtesting_enabled",
    "predictions_enabled",
    "metric_computation_enabled",
    "live_fetching_enabled",
    "betting_enabled",
    "eligible_for_training",
    "eligible_for_backtest",
    "eligible_for_betting",
    "ev_enabled",
    "kelly_eligible",
    "elite_enabled",
    "staking_enabled",
    "production_enabled",
    "production_approved",
    "artifacts_written",
)


class MLBHRLabelHandoffError(ValueError):
    """Raised when the sealed label handoff must fail closed."""


@dataclass(frozen=True, slots=True)
class MLBHRLabelDistribution:
    """Aggregate label distribution for one temporal split."""

    split: str
    row_count: int
    positive_count: int
    negative_count: int
    positive_rate: float

    def __post_init__(self) -> None:
        if (
            self.split not in SPLIT_NAMES
            or self.row_count <= 0
            or self.positive_count < 0
            or self.negative_count < 0
            or self.positive_count + self.negative_count != self.row_count
            or self.positive_rate != self.positive_count / self.row_count
        ):
            raise MLBHRLabelHandoffError("invalid split label distribution")


@dataclass(frozen=True, slots=True)
class MLBHRLabelHandoffPhase:
    """Which split labels a future consumer could access in one phase."""

    name: str
    train: str = SEALED
    validation: str = SEALED
    test: str = SEALED
    predictions_frozen: bool = False
    approval_required: bool = False


LABEL_HANDOFF_PHASES: Final = (
    MLBHRLabelHandoffPhase(name="feature_preparation"),
    MLBHRLabelHandoffPhase(name="baseline_fit", train=FITTING_ONLY),
    MLBHRLabelHandoffPhase(
        name="train_only_calibration_fit",
        train=FITTING_ONLY,
    ),
    MLBHRLabelHandoffPhase(name="validation_prediction"),
    MLBHRLabelHandoffPhase(
        name="validation_evaluation_after_predictions_frozen",
        validation=EVALUATION_ONLY,
        predictions_frozen=True,
    ),
    MLBHRLabelHandoffPhase(name="test_prediction"),
    MLBHRLabelHandoffPhase(
        name="test_evaluation_after_predictions_frozen",
        test=EVALUATION_ONLY,
        predictions_frozen=True,
        approval_required=True,
    ),
)


@dataclass(frozen=True, slots=True)
class MLBHRLabelAccessRequest:
    """A proposed future label access checked against the sealed contract."""

    phase: str
    split: str
    purpose: str
    predictions_frozen: bool = False


_ALLOWED_ACCESS_REQUESTS: Final = frozenset(
    {
        ("baseline_fit", "train", "fitting", False),
        ("train_only_calibration_fit", "train", "fitting", False),
        (
            "validation_evaluation_after_predictions_frozen",
            "validation",
            "evaluation",
            True,
        ),
        (
            "test_evaluation_after_predictions_frozen",
            "test",
            "evaluation",
            True,
        ),
    }
)


@dataclass(frozen=True, slots=True)
class MLBHRLabelHandoffReport:
    """Aggregate-only proof that the sealed label contract passed."""

    feature_pack_path: Path
    label_custody_path: Path
    temporal_split_plan_path: Path
    fitted_preprocessing_artifact_path: Path
    feature_pack_sha256: str
    label_custody_sha256: str
    feature_row_identity_sha256: str
    temporal_split_plan_sha256: str
    fitted_preprocessing_artifact_sha256: str
    distributions: tuple[MLBHRLabelDistribution, ...]
    label_value_splits: tuple[str, ...] = ()
    phases: tuple[MLBHRLabelHandoffPhase, ...] = field(
        default_factory=lambda: LABEL_HANDOFF_PHASES
    )
    requested_access_count: int = 0
    contract_version: str = LABEL_HANDOFF_CONTRACT_VERSION
    status: str = "LABEL_HANDOFF_PLAN_ONLY"
    label_column: str = LABEL_COLUMN
    research_only: bool = True
    approval_status: str = _NOT_APPROVED
    label_values_exposed: bool = False
    model_training_enabled: bool = False
    predictions_enabled: bool = False
    live_fetching_enabled: bool = False
    backtesting_enabled: bool = False
    eligible_for_betting: bool = False
    ev_enabled: bool = False
    kelly_eligible: bool = False
    elite_enabled: bool = False
    staking_enabled: bool = False
    production_approved: bool = False
    artifacts_written: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "feature_pack_path",
            "label_custody_path",
            "temporal_split_plan_path",
            "fitted_preprocessing_artifact_path",
        ):
            object.__setattr__(self, field_name, getattr(self, field_name).resolve())
        object.__setattr__(self, "distributions", tuple(self.distributions))
        object.__setattr__(self, "label_value_splits", tuple(self.label_value_splits))
        object.__setattr__(self, "phases", tuple(self.phases))
        distribution_splits = tuple(item.split for item in self.distributions)
        if (
            self.contract_version != LABEL_HANDOFF_CONTRACT_VERSION
            or self.status != "LABEL_HANDOFF_PLAN_ONLY"
            or self.label_column != LABEL_COLUMN
            or distribution_splits != self.label_value_splits
            or tuple(name for name in SPLIT_NAMES if name in distribution_splits)
            != distribution_splits
            or len(set(distribution_splits)) != len(distribution_splits)
            or self.phases != LABEL_HANDOFF_PHASES
            or self.requested_access_count < 0
            or not self.research_only
            or self.approval_status != _NOT_APPROVED
        ):
            raise MLBHRLabelHandoffError(
                "the sealed label handoff boundary cannot be relaxed"
            )
        prohibited = (
            self.label_values_exposed,
            self.model_training_enabled,
            self.predictions_enabled,
            self.live_fetching_enabled,
            self.backtesting_enabled,
            self.eligible_for_betting,
            self.ev_enabled,
            self.kelly_eligible,
            self.elite_enabled,
            self.staking_enabled,
            self.production_approved,
            self.artifacts_written,
        )
        if any(prohibited):
            raise MLBHRLabelHandoffError(
                "label handoff validation cannot execute, expose labels, write, "
                "or enable wagering"
            )


def _file_sha256(path: Path, label: str) -> str:
    if not path.is_file():
        raise MLBHRLabelHandoffError(
            f"{label} must be an existing local file: {path}"
        )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise MLBHRLabelHandoffError(f"could not hash {label} {path}: {exc}") from exc
    return digest.hexdigest()


def _read_json_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MLBHRLabelHandoffError(f"could not read {label} {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise MLBHRLabelHandoffError(f"{label} must contain a JSON object")
    return payload


def _validate_research_only_payload(
    payload: Mapping[str, object],
    label: str,
) -> None:
    if payload.get("mode") != _RESEARCH_MODE:
        raise MLBHRLabelHandoffError(
            f"{label} mode must remain {_RESEARCH_MODE!r}"
        )
    if payload.get("approval_status") != _NOT_APPROVED:
        raise MLBHRLabelHandoffError(
            f"{label} approval_status must remain {_NOT_APPROVED!r}"
        )
    if "research_only" in payload and payload.get("research_only") is not True:
        raise MLBHRLabelHandoffError(f"{label} research_only must be true")
    enabled = [
        name
        for name in _PROHIBITED_GATE_NAMES
        if name in payload and payload.get(name) is not False
    ]
    if enabled:
        raise MLBHRLabelHandoffError(
            f"{label} must not enable non-research gates: "
            + ", ".join(enabled)
        )


def _feature_rows(
    payload: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    try:
        assert_model_visible_feature_pack_label_free(payload)
    except MLBHRLabelCustodyError as exc:
        raise MLBHRLabelHandoffError(str(exc)) from exc
    raw_names = payload.get("feature_names")
    if not isinstance(raw_names, list) or any(
        not isinstance(name, str) for name in raw_names
    ):
        raise MLBHRLabelHandoffError(
            "feature-pack feature_names must be a list of strings"
        )
    leaked_names = [
        name
        for name in raw_names
        if classify_mlb_hr_research_field(name)
        is MLBHRFeatureFieldClass.LABEL_OUTCOME
    ]
    if leaked_names:
        raise MLBHRLabelHandoffError(
            "label leakage in feature_names: "
            + ", ".join(sorted(set(leaked_names)))
        )

    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise MLBHRLabelHandoffError("feature-pack rows must be a non-empty list")

    rows: list[Mapping[str, object]] = []
    for index, raw_row in enumerate(raw_rows):
        prefix = f"feature-pack rows[{index}]"
        if not isinstance(raw_row, Mapping):
            raise MLBHRLabelHandoffError(f"{prefix} must be an object")
        values = raw_row.get("feature_values")
        if not isinstance(values, Mapping):
            raise MLBHRLabelHandoffError(f"{prefix}.feature_values must be an object")
        leaked_values = [
            str(name)
            for name in values
            if classify_mlb_hr_research_field(str(name))
            is MLBHRFeatureFieldClass.LABEL_OUTCOME
        ]
        if leaked_values:
            raise MLBHRLabelHandoffError(
                f"{prefix} contains label leakage in feature_values: "
                + ", ".join(sorted(set(leaked_values)))
            )
        rows.append(raw_row)
    return tuple(rows)


def _label_distributions(
    opened_by_split: Mapping[str, Sequence[object]],
    distribution_splits: tuple[str, ...],
) -> tuple[MLBHRLabelDistribution, ...]:
    distributions: list[MLBHRLabelDistribution] = []
    for name in distribution_splits:
        split_labels = [
            bool(getattr(item, LABEL_COLUMN)) for item in opened_by_split[name]
        ]
        if not split_labels:
            raise MLBHRLabelHandoffError(f"{name} split has no labels")
        positive_count = sum(split_labels)
        row_count = len(split_labels)
        distributions.append(
            MLBHRLabelDistribution(
                split=name,
                row_count=row_count,
                positive_count=positive_count,
                negative_count=row_count - positive_count,
                positive_rate=positive_count / row_count,
            )
        )
    return tuple(distributions)


def _validated_distribution_splits(
    distribution_splits: Sequence[str],
) -> tuple[str, ...]:
    normalized = tuple(distribution_splits)
    if (
        any(name not in SPLIT_NAMES for name in normalized)
        or len(set(normalized)) != len(normalized)
        or tuple(name for name in SPLIT_NAMES if name in normalized) != normalized
    ):
        raise MLBHRLabelHandoffError(
            "distribution_splits must be an ordered subset of "
            "train, validation, test"
        )
    return normalized


def _validate_access_requests(
    requests: Sequence[MLBHRLabelAccessRequest],
) -> None:
    for index, request in enumerate(requests):
        if not isinstance(request, MLBHRLabelAccessRequest):
            raise MLBHRLabelHandoffError(
                f"access_requests[{index}] has invalid type"
            )
        proposed = (
            request.phase,
            request.split,
            request.purpose,
            request.predictions_frozen,
        )
        if proposed not in _ALLOWED_ACCESS_REQUESTS:
            raise MLBHRLabelHandoffError(
                f"label access request rejected for split={request.split!r}, "
                f"phase={request.phase!r}, purpose={request.purpose!r}; train "
                "labels are fitting-only and validation/test labels are "
                "evaluation-only after predictions are frozen"
            )


def validate_mlb_hr_label_handoff(
    *,
    feature_pack_path: str | Path,
    label_custody_path: str | Path | None = None,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
    access_requests: Sequence[MLBHRLabelAccessRequest] = (),
    distribution_splits: Sequence[str] = (),
    opening_authorizations: Sequence[MLBHRLabelOpeningAuthorization] = (),
) -> MLBHRLabelHandoffReport:
    """Validate a sealed, aggregate-only label handoff without execution.

    With no ``distribution_splits`` this validates binding only and never opens
    a label value.  Every requested split requires a matching authorization.
    """

    feature_source = Path(feature_pack_path).expanduser().resolve()
    custody_source = resolve_label_custody_path(
        feature_source, label_custody_path
    )
    split_source = Path(temporal_split_plan_path).expanduser().resolve()
    preprocessing_source = (
        Path(fitted_preprocessing_artifact_path).expanduser().resolve()
    )
    sources = (
        (feature_source, "feature pack"),
        (custody_source, "label-custody artifact"),
        (split_source, "temporal split plan"),
        (preprocessing_source, "fitted preprocessing artifact"),
    )
    initial_hashes = tuple(_file_sha256(path, label) for path, label in sources)
    payloads = tuple(_read_json_object(path, label) for path, label in sources)
    for payload, (_, label) in zip(
        (payloads[0], payloads[2], payloads[3]),
        (sources[0], sources[2], sources[3]),
        strict=True,
    ):
        _validate_research_only_payload(payload, label)

    try:
        custody_binding = validate_mlb_hr_label_custody(
            feature_pack_path=feature_source,
            label_custody_path=custody_source,
        )
    except MLBHRLabelCustodyError as exc:
        raise MLBHRLabelHandoffError(f"label-custody binding failed: {exc}") from exc

    selected_distribution_splits = _validated_distribution_splits(
        distribution_splits
    )
    _feature_rows(payloads[0])
    _validate_access_requests(access_requests)

    authorizations = {item.split: item for item in opening_authorizations}
    if len(authorizations) != len(tuple(opening_authorizations)):
        raise MLBHRLabelHandoffError(
            "opening_authorizations cannot repeat a split"
        )
    if set(authorizations) != set(selected_distribution_splits):
        raise MLBHRLabelHandoffError(
            "each opened label split requires exactly one matching authorization"
        )
    requested_splits = {request.split for request in access_requests}
    if set(selected_distribution_splits) - requested_splits:
        raise MLBHRLabelHandoffError(
            "each opened label split requires a matching access request"
        )

    try:
        preprocessing_plan = plan_mlb_hr_preprocessing(
            feature_pack_path=feature_source,
            temporal_split_plan_path=split_source,
        )
        artifact = load_fitted_preprocessing_artifact(
            preprocessing_source,
            feature_pack_path=feature_source,
            temporal_split_plan_path=split_source,
        )
    except (
        MLBHRPreprocessingPlanningError,
        MLBHRFittedPreprocessingArtifactError,
    ) as exc:
        raise MLBHRLabelHandoffError(
            f"sealed research input contract failed: {exc}"
        ) from exc
    if (
        artifact.train_date_start != preprocessing_plan.split_plan.train.start
        or artifact.train_date_end != preprocessing_plan.split_plan.train.end
    ):
        raise MLBHRLabelHandoffError(
            "fitted preprocessing artifact does not match the train split"
        )

    opened_by_split: dict[str, Sequence[object]] = {}
    for split_name in selected_distribution_splits:
        try:
            opened_by_split[split_name] = open_mlb_hr_label_custody_split(
                feature_pack_path=feature_source,
                label_custody_path=custody_source,
                temporal_split_plan_path=split_source,
                authorization=authorizations[split_name],
            )
        except MLBHRLabelCustodyError as exc:
            raise MLBHRLabelHandoffError(
                f"{split_name} label opening failed: {exc}"
            ) from exc
    distributions = _label_distributions(
        opened_by_split, selected_distribution_splits
    )
    final_hashes = tuple(_file_sha256(path, label) for path, label in sources)
    if final_hashes != initial_hashes:
        raise MLBHRLabelHandoffError(
            "an input artifact changed during label handoff validation"
        )

    return MLBHRLabelHandoffReport(
        feature_pack_path=feature_source,
        label_custody_path=custody_source,
        temporal_split_plan_path=split_source,
        fitted_preprocessing_artifact_path=preprocessing_source,
        feature_pack_sha256=initial_hashes[0],
        label_custody_sha256=initial_hashes[1],
        feature_row_identity_sha256=custody_binding.row_identity_sha256,
        temporal_split_plan_sha256=initial_hashes[2],
        fitted_preprocessing_artifact_sha256=initial_hashes[3],
        distributions=distributions,
        label_value_splits=selected_distribution_splits,
        requested_access_count=len(access_requests),
    )


__all__ = [
    "EVALUATION_ONLY",
    "FITTING_ONLY",
    "LABEL_COLUMN",
    "LABEL_HANDOFF_CONTRACT_VERSION",
    "LABEL_HANDOFF_PHASES",
    "MLBHRLabelAccessRequest",
    "MLBHRLabelDistribution",
    "MLBHRLabelHandoffError",
    "MLBHRLabelHandoffPhase",
    "MLBHRLabelHandoffReport",
    "SEALED",
    "SPLIT_NAMES",
    "validate_mlb_hr_label_handoff",
]
