"""Frozen, read-only evaluation for MLB HR research predictions.

The dry-run planner validates existing local artifacts in a fixed order.  The
explicit validation mode then opens validation labels and computes in-memory
metrics and paired date-block intervals.  Neither path trains or runs a model,
generates predictions, opens test labels, fetches data, writes artifacts, or
enables wagering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Final, Mapping, Sequence

from courtvision.sports.mlb.training.hr_backtest_runner import (
    MLBHRBacktestExecutionPlan,
    MLBHRBacktestRunnerContractError,
    plan_sealed_mlb_hr_research_backtest,
)
from courtvision.sports.mlb.training.hr_label_handoff import (
    EVALUATION_ONLY,
    MLBHRLabelAccessRequest,
    MLBHRLabelHandoffError,
    MLBHRLabelHandoffReport,
    validate_mlb_hr_label_handoff,
)
from courtvision.sports.mlb.training.hr_label_custody import (
    MLBHRLabelCustodyError,
    MLBHRLabelOpeningAuthorization,
    open_mlb_hr_label_custody_split,
    resolve_label_custody_path,
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
    METRIC_NAMES,
    MLBHRPairedGameDateBootstrapResult,
    MLBHRValidationMetricError,
    paired_game_date_bootstrap,
)


EVALUATOR_CONTRACT_VERSION: Final = "mlb-hr-frozen-research-evaluator-v1"
VALIDATION_EVALUATOR_VERSION: Final = "mlb-hr-validation-metrics-v1"
SUPPORTED_EVALUATION_SPLITS: Final = frozenset({"validation"})
LABEL_OPENING_SEQUENCE: Final = (
    "frozen_prediction_artifact_validated_labels_sealed",
    "prediction_population_coverage_validated_labels_sealed",
    "runner_gates_validated_after_prediction_freeze",
    "evaluation_only_label_handoff_validated",
    "evaluation_plan_returned_metrics_not_computed",
)
MISSING_PREDICTION_POLICY: Final = (
    "exact_split_population_required_no_drop_no_impute_no_regeneration"
)
IMMUTABLE_EVALUATION_ARTIFACT_POLICY: Final = (
    "future_create_once_atomic_no_overwrite_hash_bound_research_staging_only"
)
BOOTSTRAP_SEED: Final = DEFAULT_BOOTSTRAP_SEED
BOOTSTRAP_REPLICATES: Final = DEFAULT_BOOTSTRAP_REPLICATES
BOOTSTRAP_MINIMUM_SUCCESSFUL_REPLICATES: Final = (
    DEFAULT_BOOTSTRAP_MINIMUM_SUCCESSFUL_REPLICATES
)
BOOTSTRAP_CONFIDENCE_LEVEL: Final = DEFAULT_BOOTSTRAP_CONFIDENCE_LEVEL
SEGMENT_MINIMUM_ROWS: Final = 200
SEGMENT_MINIMUM_POSITIVES: Final = 20
SEGMENT_MINIMUM_NEGATIVES: Final = 20
PREDICTION_PROBABILITY_FIELDS: Final = (
    "raw_home_run_probability",
    "calibrated_home_run_probability",
)

_SHA256_LENGTH: Final = 64
_RUNNER_FALSE_GATES: Final = (
    "execution_authorized",
    "model_training_enabled",
    "preprocessing_transform_enabled",
    "predictions_enabled",
    "metric_computation_enabled",
    "live_fetching_enabled",
    "backtesting_enabled",
    "eligible_for_betting",
    "ev_enabled",
    "kelly_eligible",
    "elite_enabled",
    "staking_enabled",
    "production_approved",
    "artifacts_written",
)
_HANDOFF_FALSE_GATES: Final = (
    "label_values_exposed",
    "model_training_enabled",
    "predictions_enabled",
    "live_fetching_enabled",
    "backtesting_enabled",
    "eligible_for_betting",
    "ev_enabled",
    "kelly_eligible",
    "elite_enabled",
    "staking_enabled",
    "production_approved",
    "artifacts_written",
)


class MLBHREvaluationContractError(ValueError):
    """Raised when the frozen evaluator planning boundary must fail closed."""


@dataclass(frozen=True, slots=True)
class MLBHREvaluationMetricDefinition:
    """One allowed metric in the frozen evaluator contract."""

    name: str
    definition: str
    direction: str
    status: str = "planned_not_computed"


ALLOWED_EVALUATION_METRICS: Final = (
    MLBHREvaluationMetricDefinition(
        name="log_loss",
        definition=(
            "mean binary negative log likelihood; probabilities clipped only "
            "for numerical evaluation to [1e-15, 1-1e-15]"
        ),
        direction="lower_is_better",
    ),
    MLBHREvaluationMetricDefinition(
        name="brier_score",
        definition="mean squared error between probability and binary label",
        direction="lower_is_better",
    ),
    MLBHREvaluationMetricDefinition(
        name="roc_auc",
        definition="rank-based area under the receiver-operating curve",
        direction="higher_is_better",
    ),
    MLBHREvaluationMetricDefinition(
        name="pr_auc",
        definition=(
            "positive-class average precision using step-wise recall increments"
        ),
        direction="higher_is_better",
    ),
    MLBHREvaluationMetricDefinition(
        name="calibration_error",
        definition=(
            "expected absolute calibration error in ten fixed equal-width "
            "probability bins, weighted by row count; empty bins omitted"
        ),
        direction="lower_is_better",
    ),
)
ALLOWED_EVALUATION_METRIC_NAMES: Final = tuple(
    metric.name for metric in ALLOWED_EVALUATION_METRICS
)
if ALLOWED_EVALUATION_METRIC_NAMES != METRIC_NAMES:
    raise RuntimeError("evaluation metric definitions do not match implementation")


@dataclass(frozen=True, slots=True)
class MLBHREvaluationBaselineDefinition:
    """One predeclared non-wagering comparison."""

    name: str
    population: str
    source: str
    status: str = "required_not_computed"


REQUIRED_BASELINE_COMPARISONS: Final = (
    MLBHREvaluationBaselineDefinition(
        name="train_prevalence_constant",
        population="identical_full_evaluation_population",
        source="sealed_train_label_handoff_aggregate",
    ),
    MLBHREvaluationBaselineDefinition(
        name="raw_implied_probability",
        population="identical_predeclared_market_covered_paired_subset",
        source="feature_pack_implied_probability",
    ),
    MLBHREvaluationBaselineDefinition(
        name="no_market_logistic_ablation",
        population="identical_full_evaluation_population",
        source="separately_frozen_prediction_artifact_required",
    ),
)


@dataclass(frozen=True, slots=True)
class MLBHRBootstrapPolicy:
    """Predeclared uncertainty policy for a future evaluator implementation."""

    unit: str = "game_date_block"
    method: str = "paired_percentile_bootstrap"
    confidence_level: float = BOOTSTRAP_CONFIDENCE_LEVEL
    replicates: int = BOOTSTRAP_REPLICATES
    minimum_successful_replicates: int = BOOTSTRAP_MINIMUM_SUCCESSFUL_REPLICATES
    seed: int = BOOTSTRAP_SEED
    applies_to: str = "every_metric_and_every_paired_baseline_difference"
    failure_result: str = "inconclusive"

    def __post_init__(self) -> None:
        if (
            self.unit != "game_date_block"
            or self.method != "paired_percentile_bootstrap"
            or self.confidence_level != BOOTSTRAP_CONFIDENCE_LEVEL
            or self.replicates != BOOTSTRAP_REPLICATES
            or self.minimum_successful_replicates
            != BOOTSTRAP_MINIMUM_SUCCESSFUL_REPLICATES
            or self.seed != BOOTSTRAP_SEED
            or self.applies_to
            != "every_metric_and_every_paired_baseline_difference"
            or self.failure_result != "inconclusive"
        ):
            raise MLBHREvaluationContractError(
                "the frozen bootstrap policy cannot be relaxed"
            )


@dataclass(frozen=True, slots=True)
class MLBHRSegmentationPolicy:
    """Frozen diagnostic segmentation rules; never a selection gate."""

    primary_population: str = "overall_exact_evaluation_population"
    diagnostic_segments: tuple[str, ...] = (
        "market_coverage",
        "batter_hand_if_present",
        "pitcher_hand_if_present",
        "platoon_side_if_present",
    )
    minimum_rows: int = SEGMENT_MINIMUM_ROWS
    minimum_positives: int = SEGMENT_MINIMUM_POSITIVES
    minimum_negatives: int = SEGMENT_MINIMUM_NEGATIVES
    underpowered_action: str = "suppress_metrics_report_counts_only"
    post_label_segment_creation_allowed: bool = False
    promotion_or_wagering_use_allowed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "diagnostic_segments", tuple(self.diagnostic_segments)
        )
        if (
            self.primary_population != "overall_exact_evaluation_population"
            or self.diagnostic_segments
            != (
                "market_coverage",
                "batter_hand_if_present",
                "pitcher_hand_if_present",
                "platoon_side_if_present",
            )
            or self.minimum_rows != SEGMENT_MINIMUM_ROWS
            or self.minimum_positives != SEGMENT_MINIMUM_POSITIVES
            or self.minimum_negatives != SEGMENT_MINIMUM_NEGATIVES
            or self.underpowered_action
            != "suppress_metrics_report_counts_only"
            or self.post_label_segment_creation_allowed
            or self.promotion_or_wagering_use_allowed
        ):
            raise MLBHREvaluationContractError(
                "the frozen segmentation policy cannot be relaxed"
            )


@dataclass(frozen=True, slots=True)
class MLBHREvaluationArtifactPolicy:
    """Future artifact requirements; this module has no writer."""

    policy: str = IMMUTABLE_EVALUATION_ARTIFACT_POLICY
    bind_all_input_hashes: bool = True
    bind_contract_and_code_versions: bool = True
    canonical_content_sha256_required: bool = True
    isolated_research_staging_required: bool = True
    operational_paths_prohibited: bool = True
    append_allowed: bool = False
    overwrite_allowed: bool = False
    mutation_after_label_open_allowed: bool = False
    writer_implemented: bool = False

    def __post_init__(self) -> None:
        required = (
            self.bind_all_input_hashes,
            self.bind_contract_and_code_versions,
            self.canonical_content_sha256_required,
            self.isolated_research_staging_required,
            self.operational_paths_prohibited,
        )
        prohibited = (
            self.append_allowed,
            self.overwrite_allowed,
            self.mutation_after_label_open_allowed,
            self.writer_implemented,
        )
        if (
            self.policy != IMMUTABLE_EVALUATION_ARTIFACT_POLICY
            or not all(required)
            or any(prohibited)
        ):
            raise MLBHREvaluationContractError(
                "the immutable evaluation artifact policy cannot be relaxed"
            )


@dataclass(frozen=True, slots=True)
class MLBHRPredictionPopulationCoverage:
    """Exact identity-set coverage established before label handoff."""

    feature_pack_path: Path
    temporal_split_plan_path: Path
    prediction_artifact_path: Path
    prediction_artifact_sha256: str
    split_id: str
    window_id: str
    expected_rows: int
    predicted_rows: int
    matched_rows: int
    missing_rows: int = 0
    extra_rows: int = 0
    duplicate_feature_rows: int = 0
    exact_identity_match: bool = True
    labels_accessed: bool = False
    missing_prediction_policy: str = MISSING_PREDICTION_POLICY

    def __post_init__(self) -> None:
        for name in (
            "feature_pack_path",
            "temporal_split_plan_path",
            "prediction_artifact_path",
        ):
            object.__setattr__(self, name, getattr(self, name).resolve())
        if (
            self.split_id not in SUPPORTED_EVALUATION_SPLITS
            or self.expected_rows <= 0
            or self.predicted_rows != self.expected_rows
            or self.matched_rows != self.expected_rows
            or self.missing_rows != 0
            or self.extra_rows != 0
            or self.duplicate_feature_rows != 0
            or not self.exact_identity_match
            or self.labels_accessed
            or self.missing_prediction_policy != MISSING_PREDICTION_POLICY
            or len(self.prediction_artifact_sha256) != _SHA256_LENGTH
        ):
            raise MLBHREvaluationContractError(
                "prediction coverage must be an exact label-sealed identity match"
            )


@dataclass(frozen=True, slots=True)
class MLBHREvaluationPlan:
    """Immutable, in-memory plan proving that evaluator prerequisites passed."""

    feature_pack_path: Path
    label_custody_path: Path
    temporal_split_plan_path: Path
    fitted_preprocessing_artifact_path: Path
    prediction_artifact_path: Path
    feature_pack_sha256: str
    label_custody_sha256: str
    temporal_split_plan_sha256: str
    fitted_preprocessing_artifact_sha256: str
    prediction_artifact_sha256: str
    split_id: str
    window_id: str
    population_coverage: MLBHRPredictionPopulationCoverage
    runner_status: str
    label_handoff_status: str
    metric_definitions: tuple[MLBHREvaluationMetricDefinition, ...] = field(
        default_factory=lambda: ALLOWED_EVALUATION_METRICS
    )
    baseline_comparisons: tuple[MLBHREvaluationBaselineDefinition, ...] = field(
        default_factory=lambda: REQUIRED_BASELINE_COMPARISONS
    )
    bootstrap_policy: MLBHRBootstrapPolicy = field(
        default_factory=MLBHRBootstrapPolicy
    )
    segmentation_policy: MLBHRSegmentationPolicy = field(
        default_factory=MLBHRSegmentationPolicy
    )
    evaluation_artifact_policy: MLBHREvaluationArtifactPolicy = field(
        default_factory=MLBHREvaluationArtifactPolicy
    )
    label_opening_sequence: tuple[str, ...] = LABEL_OPENING_SEQUENCE
    contract_version: str = EVALUATOR_CONTRACT_VERSION
    status: str = "EVALUATION_PLAN_ONLY"
    research_only: bool = True
    approval_status: str = "not_approved"
    prediction_artifact_valid: bool = True
    population_coverage_valid: bool = True
    runner_gates_valid: bool = True
    label_handoff_valid: bool = True
    labels_opened_after_prediction_validation: bool = True
    label_values_exposed: bool = False
    model_training_enabled: bool = False
    prediction_generation_enabled: bool = False
    final_metrics_calculated: bool = False
    metric_computation_enabled: bool = False
    live_fetching_enabled: bool = False
    operational_use_enabled: bool = False
    eligible_for_betting: bool = False
    ev_enabled: bool = False
    kelly_eligible: bool = False
    elite_enabled: bool = False
    staking_enabled: bool = False
    production_approved: bool = False
    evaluation_artifact_writing_enabled: bool = False
    artifacts_written: bool = False

    def __post_init__(self) -> None:
        for name in (
            "feature_pack_path",
            "label_custody_path",
            "temporal_split_plan_path",
            "fitted_preprocessing_artifact_path",
            "prediction_artifact_path",
        ):
            object.__setattr__(self, name, getattr(self, name).resolve())
        for name in (
            "metric_definitions",
            "baseline_comparisons",
            "label_opening_sequence",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        required_true = (
            self.research_only,
            self.prediction_artifact_valid,
            self.population_coverage_valid,
            self.runner_gates_valid,
            self.label_handoff_valid,
            self.labels_opened_after_prediction_validation,
        )
        prohibited = (
            self.label_values_exposed,
            self.model_training_enabled,
            self.prediction_generation_enabled,
            self.final_metrics_calculated,
            self.metric_computation_enabled,
            self.live_fetching_enabled,
            self.operational_use_enabled,
            self.eligible_for_betting,
            self.ev_enabled,
            self.kelly_eligible,
            self.elite_enabled,
            self.staking_enabled,
            self.production_approved,
            self.evaluation_artifact_writing_enabled,
            self.artifacts_written,
        )
        if (
            self.contract_version != EVALUATOR_CONTRACT_VERSION
            or self.status != "EVALUATION_PLAN_ONLY"
            or self.approval_status != "not_approved"
            or self.split_id not in SUPPORTED_EVALUATION_SPLITS
            or self.window_id != self.population_coverage.window_id
            or self.prediction_artifact_sha256
            != self.population_coverage.prediction_artifact_sha256
            or self.runner_status != "BACKTEST_EXECUTION_PLAN_ONLY"
            or self.label_handoff_status != "LABEL_HANDOFF_PLAN_ONLY"
            or self.metric_definitions != ALLOWED_EVALUATION_METRICS
            or self.baseline_comparisons != REQUIRED_BASELINE_COMPARISONS
            or self.bootstrap_policy != MLBHRBootstrapPolicy()
            or self.segmentation_policy != MLBHRSegmentationPolicy()
            or self.evaluation_artifact_policy != MLBHREvaluationArtifactPolicy()
            or self.label_opening_sequence != LABEL_OPENING_SEQUENCE
            or not all(required_true)
            or any(prohibited)
        ):
            raise MLBHREvaluationContractError(
                "the research-only evaluation plan boundary cannot be relaxed"
            )


@dataclass(frozen=True, slots=True)
class MLBHRValidationEvaluationResult:
    """Write-free validation metrics produced after every frozen gate passes."""

    plan: MLBHREvaluationPlan
    bootstrap: MLBHRPairedGameDateBootstrapResult
    contract_version: str = VALIDATION_EVALUATOR_VERSION
    status: str = "VALIDATION_METRICS_COMPUTED_IN_MEMORY"
    split_id: str = "validation"
    research_only: bool = True
    approval_status: str = "not_approved"
    validation_labels_opened: bool = True
    test_labels_opened: bool = False
    test_labels_sealed: bool = True
    validation_metrics_calculated: bool = True
    validation_metric_computation_only: bool = True
    model_training_enabled: bool = False
    prediction_generation_enabled: bool = False
    live_fetching_enabled: bool = False
    operational_use_enabled: bool = False
    eligible_for_betting: bool = False
    ev_enabled: bool = False
    kelly_eligible: bool = False
    elite_enabled: bool = False
    staking_enabled: bool = False
    production_approved: bool = False
    artifacts_written: bool = False

    def __post_init__(self) -> None:
        required = (
            self.research_only,
            self.validation_labels_opened,
            self.test_labels_sealed,
            self.validation_metrics_calculated,
            self.validation_metric_computation_only,
        )
        prohibited = (
            self.test_labels_opened,
            self.model_training_enabled,
            self.prediction_generation_enabled,
            self.live_fetching_enabled,
            self.operational_use_enabled,
            self.eligible_for_betting,
            self.ev_enabled,
            self.kelly_eligible,
            self.elite_enabled,
            self.staking_enabled,
            self.production_approved,
            self.artifacts_written,
        )
        expected_results = {
            (probability_field, metric_name)
            for probability_field in PREDICTION_PROBABILITY_FIELDS
            for metric_name in ALLOWED_EVALUATION_METRIC_NAMES
        }
        observed_results = {
            (interval.series_name, interval.metric_name)
            for interval in self.bootstrap.intervals
        }
        if (
            self.contract_version != VALIDATION_EVALUATOR_VERSION
            or self.status != "VALIDATION_METRICS_COMPUTED_IN_MEMORY"
            or self.split_id != "validation"
            or self.plan.split_id != self.split_id
            or self.approval_status != "not_approved"
            or observed_results != expected_results
            or len(self.bootstrap.intervals) != len(expected_results)
            or self.bootstrap.seed != BOOTSTRAP_SEED
            or self.bootstrap.requested_replicates != BOOTSTRAP_REPLICATES
            or self.bootstrap.minimum_successful_replicates
            != BOOTSTRAP_MINIMUM_SUCCESSFUL_REPLICATES
            or self.bootstrap.confidence_level != BOOTSTRAP_CONFIDENCE_LEVEL
            or not all(required)
            or any(prohibited)
        ):
            raise MLBHREvaluationContractError(
                "the validation-only evaluation boundary cannot be relaxed"
            )


def _file_sha256(path: Path, label: str) -> str:
    if not path.is_file():
        raise MLBHREvaluationContractError(
            f"{label} must be an existing local file: {path}"
        )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise MLBHREvaluationContractError(
            f"could not hash {label} {path}: {exc}"
        ) from exc
    return digest.hexdigest()


def _read_json_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MLBHREvaluationContractError(
            f"could not read {label} {path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise MLBHREvaluationContractError(f"{label} must contain a JSON object")
    return payload


def _split_dates(payload: Mapping[str, object], split_id: str) -> frozenset[date]:
    raw_window = payload.get(split_id)
    if not isinstance(raw_window, Mapping):
        raise MLBHREvaluationContractError(
            f"temporal split plan has no {split_id!r} window"
        )
    raw_dates = raw_window.get("game_dates")
    if not isinstance(raw_dates, list) or not raw_dates:
        raise MLBHREvaluationContractError(
            f"temporal split plan {split_id}.game_dates must be non-empty"
        )
    parsed: list[date] = []
    for index, raw_date in enumerate(raw_dates):
        if not isinstance(raw_date, str):
            raise MLBHREvaluationContractError(
                f"temporal split plan {split_id}.game_dates[{index}] is invalid"
            )
        try:
            parsed.append(date.fromisoformat(raw_date))
        except ValueError as exc:
            raise MLBHREvaluationContractError(
                f"temporal split plan {split_id}.game_dates[{index}] is invalid"
            ) from exc
    if parsed != sorted(set(parsed)):
        raise MLBHREvaluationContractError(
            f"temporal split plan {split_id}.game_dates must be unique and ordered"
        )
    return frozenset(parsed)


def _required_text(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MLBHREvaluationContractError(f"{location} must be non-empty text")
    return value


def _feature_population_identities(
    feature_payload: Mapping[str, object],
    split_dates: frozenset[date],
) -> tuple[tuple[str, str, str, str], ...]:
    raw_rows = feature_payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise MLBHREvaluationContractError(
            "feature-pack rows must be a non-empty list"
        )
    identities: list[tuple[str, str, str, str]] = []
    row_ids: set[str] = set()
    for index, raw_row in enumerate(raw_rows):
        location = f"feature-pack rows[{index}]"
        if not isinstance(raw_row, Mapping):
            raise MLBHREvaluationContractError(f"{location} must be an object")
        raw_date = raw_row.get("game_date")
        if not isinstance(raw_date, str):
            raise MLBHREvaluationContractError(
                f"{location}.game_date must be an ISO-8601 date"
            )
        try:
            game_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise MLBHREvaluationContractError(
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
        if row_id in row_ids or identity in identities:
            raise MLBHREvaluationContractError(
                f"{location} duplicates an evaluation population identity"
            )
        row_ids.add(row_id)
        identities.append(identity)
    if not identities:
        raise MLBHREvaluationContractError(
            "feature pack has no rows in the prediction artifact split"
        )
    return tuple(identities)


def _format_identity_sample(
    identities: Sequence[tuple[str, str, str, str]],
) -> str:
    sample = ", ".join(identity[0] for identity in identities[:5])
    suffix = "..." if len(identities) > 5 else ""
    return sample + suffix


def validate_mlb_hr_prediction_population_coverage(
    *,
    frozen_prediction_artifact: MLBHRFrozenPredictionArtifact,
    feature_pack_path: str | Path,
    temporal_split_plan_path: str | Path,
) -> MLBHRPredictionPopulationCoverage:
    """Require an exact prediction for every feature row in the frozen split.

    The function reads row identities only.  It deliberately never retrieves,
    compares, returns, or summarizes the ``is_home_run`` label.
    """

    if not isinstance(frozen_prediction_artifact, MLBHRFrozenPredictionArtifact):
        raise MLBHREvaluationContractError(
            "population coverage requires a validated frozen prediction artifact"
        )
    if frozen_prediction_artifact.split_id not in SUPPORTED_EVALUATION_SPLITS:
        raise MLBHREvaluationContractError(
            "frozen evaluator v1 permits validation planning only; test labels "
            "remain sealed pending validation-to-test promotion proof"
        )
    feature_source = Path(feature_pack_path).expanduser().resolve()
    split_source = Path(temporal_split_plan_path).expanduser().resolve()
    if _file_sha256(feature_source, "feature pack") != (
        frozen_prediction_artifact.feature_pack_sha256
    ):
        raise MLBHREvaluationContractError(
            "population coverage feature-pack SHA-256 does not match predictions"
        )
    if _file_sha256(split_source, "temporal split plan") != (
        frozen_prediction_artifact.temporal_split_plan_sha256
    ):
        raise MLBHREvaluationContractError(
            "population coverage split-plan SHA-256 does not match predictions"
        )

    split_payload = _read_json_object(split_source, "temporal split plan")
    feature_payload = _read_json_object(feature_source, "feature pack")
    dates = _split_dates(split_payload, frozen_prediction_artifact.split_id)
    expected = _feature_population_identities(feature_payload, dates)
    predicted = tuple(
        (
            row.row_id,
            row.game_date.isoformat(),
            row.game_id,
            row.player_id,
        )
        for row in frozen_prediction_artifact.rows
    )
    expected_set = set(expected)
    predicted_set = set(predicted)
    missing = sorted(expected_set - predicted_set)
    extra = sorted(predicted_set - expected_set)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(
                f"missing predictions={len(missing)} "
                f"({_format_identity_sample(missing)})"
            )
        if extra:
            details.append(
                f"extra predictions={len(extra)} "
                f"({_format_identity_sample(extra)})"
            )
        raise MLBHREvaluationContractError(
            "prediction population coverage failed: " + "; ".join(details)
        )

    return MLBHRPredictionPopulationCoverage(
        feature_pack_path=feature_source,
        temporal_split_plan_path=split_source,
        prediction_artifact_path=frozen_prediction_artifact.path,
        prediction_artifact_sha256=frozen_prediction_artifact.artifact_sha256,
        split_id=frozen_prediction_artifact.split_id,
        window_id=frozen_prediction_artifact.window_id,
        expected_rows=len(expected),
        predicted_rows=len(predicted),
        matched_rows=len(expected_set & predicted_set),
    )


def _validate_runner_gates(plan: MLBHRBacktestExecutionPlan) -> None:
    required_true = (
        "feature_firewall_valid",
        "temporal_split_valid",
        "preprocessing_artifact_valid",
        "window_population_valid",
        "label_access_valid",
        "research_only",
    )
    failed = [name for name in required_true if getattr(plan, name, None) is not True]
    enabled = [
        name for name in _RUNNER_FALSE_GATES if getattr(plan, name, None) is not False
    ]
    if (
        getattr(plan, "status", None) != "BACKTEST_EXECUTION_PLAN_ONLY"
        or getattr(plan, "approval_status", None) != "not_approved"
        or failed
        or enabled
    ):
        details = failed + enabled
        raise MLBHREvaluationContractError(
            "sealed runner gates are not valid"
            + (": " + ", ".join(details) if details else "")
        )


def _validate_handoff_gates(
    report: MLBHRLabelHandoffReport,
    *,
    split_id: str,
    expected_rows: int,
) -> None:
    enabled = [
        name
        for name in _HANDOFF_FALSE_GATES
        if getattr(report, name, None) is not False
    ]
    expected_phase = f"{split_id}_evaluation_after_predictions_frozen"
    phase_valid = any(
        getattr(phase, "name", None) == expected_phase
        and getattr(phase, split_id, None) == EVALUATION_ONLY
        and getattr(phase, "predictions_frozen", None) is True
        for phase in getattr(report, "phases", ())
    )
    selected_distribution = next(
        (
            distribution
            for distribution in getattr(report, "distributions", ())
            if getattr(distribution, "split", None) == split_id
        ),
        None,
    )
    opened_splits = getattr(
        report,
        "label_value_splits",
        tuple(
            getattr(distribution, "split", None)
            for distribution in getattr(report, "distributions", ())
        ),
    )
    if (
        getattr(report, "status", None) != "LABEL_HANDOFF_PLAN_ONLY"
        or getattr(report, "approval_status", None) != "not_approved"
        or getattr(report, "research_only", None) is not True
        or getattr(report, "requested_access_count", None) != 1
        or not phase_valid
        or selected_distribution is None
        or getattr(selected_distribution, "row_count", None) != expected_rows
        or tuple(opened_splits) != (split_id,)
        or enabled
    ):
        raise MLBHREvaluationContractError(
            "evaluation-only label handoff gates are not valid"
        )


def _require_matching_input_hashes(
    artifact: MLBHRFrozenPredictionArtifact,
    runner_plan: MLBHRBacktestExecutionPlan,
    handoff_report: MLBHRLabelHandoffReport,
) -> None:
    expected = (
        artifact.feature_pack_sha256,
        artifact.temporal_split_plan_sha256,
        artifact.fitted_preprocessing_artifact_sha256,
    )
    for label, report in (
        ("runner", runner_plan),
        ("label handoff", handoff_report),
    ):
        observed = (
            getattr(report, "feature_pack_sha256", None),
            getattr(report, "temporal_split_plan_sha256", None),
            getattr(report, "fitted_preprocessing_artifact_sha256", None),
        )
        if observed != expected:
            raise MLBHREvaluationContractError(
                f"{label} input hashes do not match frozen predictions"
            )
    if (
        runner_plan.label_custody_sha256
        != handoff_report.label_custody_sha256
    ):
        raise MLBHREvaluationContractError(
            "runner and label handoff do not bind the same custody artifact"
        )


def validate_mlb_hr_evaluation_label_opening(
    *,
    frozen_prediction_artifact: MLBHRFrozenPredictionArtifact | None,
    population_coverage: MLBHRPredictionPopulationCoverage | None,
    feature_pack_path: str | Path,
    label_custody_path: str | Path | None = None,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
) -> tuple[MLBHRBacktestExecutionPlan, MLBHRLabelHandoffReport]:
    """Open only aggregate evaluation handoff after both freeze gates pass."""

    if not isinstance(frozen_prediction_artifact, MLBHRFrozenPredictionArtifact):
        raise MLBHREvaluationContractError(
            "labels cannot open before frozen prediction validation"
        )
    if not isinstance(population_coverage, MLBHRPredictionPopulationCoverage):
        raise MLBHREvaluationContractError(
            "labels cannot open before prediction population coverage validation"
        )
    if (
        population_coverage.prediction_artifact_sha256
        != frozen_prediction_artifact.artifact_sha256
        or population_coverage.split_id != frozen_prediction_artifact.split_id
        or population_coverage.window_id != frozen_prediction_artifact.window_id
        or not population_coverage.exact_identity_match
        or population_coverage.labels_accessed
    ):
        raise MLBHREvaluationContractError(
            "labels cannot open without matching frozen prediction coverage proof"
        )

    try:
        runner_plan = plan_sealed_mlb_hr_research_backtest(
            feature_pack_path=feature_pack_path,
            label_custody_path=label_custody_path,
            temporal_split_plan_path=temporal_split_plan_path,
            fitted_preprocessing_artifact_path=fitted_preprocessing_artifact_path,
        )
    except MLBHRBacktestRunnerContractError as exc:
        raise MLBHREvaluationContractError(f"sealed runner gate failed: {exc}") from exc
    _validate_runner_gates(runner_plan)

    access = MLBHRLabelAccessRequest(
        phase=(
            f"{frozen_prediction_artifact.split_id}_evaluation_after_"
            "predictions_frozen"
        ),
        split=frozen_prediction_artifact.split_id,
        purpose="evaluation",
        predictions_frozen=True,
    )
    authorization = MLBHRLabelOpeningAuthorization(
        split=frozen_prediction_artifact.split_id,
        reason="frozen_prediction_validation",
        expected_row_ids=tuple(
            row.row_id for row in frozen_prediction_artifact.rows
        ),
        frozen_prediction_artifact_sha256=(
            frozen_prediction_artifact.artifact_sha256
        ),
    )
    try:
        handoff_report = validate_mlb_hr_label_handoff(
            feature_pack_path=feature_pack_path,
            label_custody_path=label_custody_path,
            temporal_split_plan_path=temporal_split_plan_path,
            fitted_preprocessing_artifact_path=fitted_preprocessing_artifact_path,
            access_requests=(access,),
            distribution_splits=(frozen_prediction_artifact.split_id,),
            opening_authorizations=(authorization,),
        )
    except MLBHRLabelHandoffError as exc:
        raise MLBHREvaluationContractError(
            f"evaluation label handoff failed: {exc}"
        ) from exc
    _validate_handoff_gates(
        handoff_report,
        split_id=frozen_prediction_artifact.split_id,
        expected_rows=population_coverage.expected_rows,
    )
    _require_matching_input_hashes(
        frozen_prediction_artifact, runner_plan, handoff_report
    )
    return runner_plan, handoff_report


def _prepare_frozen_mlb_hr_research_evaluation(
    *,
    feature_pack_path: str | Path,
    label_custody_path: str | Path | None = None,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
    prediction_artifact_path: str | Path,
    model_specification_path: str | Path = DEFAULT_MODEL_SPECIFICATION_PATH,
) -> tuple[MLBHREvaluationPlan, MLBHRFrozenPredictionArtifact]:
    """Validate all prerequisites and retain the validated artifact in memory."""

    feature_source = Path(feature_pack_path).expanduser().resolve()
    custody_source = resolve_label_custody_path(
        feature_source, label_custody_path
    )
    split_source = Path(temporal_split_plan_path).expanduser().resolve()
    preprocessing_source = (
        Path(fitted_preprocessing_artifact_path).expanduser().resolve()
    )
    prediction_source = Path(prediction_artifact_path).expanduser().resolve()
    sources = (
        (feature_source, "feature pack"),
        (custody_source, "label-custody artifact"),
        (split_source, "temporal split plan"),
        (preprocessing_source, "fitted preprocessing artifact"),
        (prediction_source, "prediction artifact"),
    )
    initial_hashes = tuple(_file_sha256(path, label) for path, label in sources)

    try:
        artifact = load_frozen_prediction_artifact(
            prediction_source,
            feature_pack_path=feature_source,
            temporal_split_plan_path=split_source,
            fitted_preprocessing_artifact_path=preprocessing_source,
            model_specification_path=model_specification_path,
        )
    except MLBHRFrozenPredictionArtifactError as exc:
        raise MLBHREvaluationContractError(
            f"frozen prediction artifact gate failed: {exc}"
        ) from exc

    coverage = validate_mlb_hr_prediction_population_coverage(
        frozen_prediction_artifact=artifact,
        feature_pack_path=feature_source,
        temporal_split_plan_path=split_source,
    )
    runner_plan, handoff_report = validate_mlb_hr_evaluation_label_opening(
        frozen_prediction_artifact=artifact,
        population_coverage=coverage,
        feature_pack_path=feature_source,
        label_custody_path=custody_source,
        temporal_split_plan_path=split_source,
        fitted_preprocessing_artifact_path=preprocessing_source,
    )

    final_hashes = tuple(_file_sha256(path, label) for path, label in sources)
    if final_hashes != initial_hashes:
        raise MLBHREvaluationContractError(
            "an evaluator input artifact changed during dry-run validation"
        )
    if artifact.artifact_sha256 != coverage.prediction_artifact_sha256:
        raise MLBHREvaluationContractError(
            "prediction population proof is not bound to the frozen artifact"
        )

    plan = MLBHREvaluationPlan(
        feature_pack_path=feature_source,
        label_custody_path=custody_source,
        temporal_split_plan_path=split_source,
        fitted_preprocessing_artifact_path=preprocessing_source,
        prediction_artifact_path=prediction_source,
        feature_pack_sha256=initial_hashes[0],
        label_custody_sha256=initial_hashes[1],
        temporal_split_plan_sha256=initial_hashes[2],
        fitted_preprocessing_artifact_sha256=initial_hashes[3],
        prediction_artifact_sha256=artifact.artifact_sha256,
        split_id=artifact.split_id,
        window_id=artifact.window_id,
        population_coverage=coverage,
        runner_status=runner_plan.status,
        label_handoff_status=handoff_report.status,
    )
    return plan, artifact


def plan_frozen_mlb_hr_research_evaluation(
    *,
    feature_pack_path: str | Path,
    label_custody_path: str | Path | None = None,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
    prediction_artifact_path: str | Path,
    model_specification_path: str | Path = DEFAULT_MODEL_SPECIFICATION_PATH,
) -> MLBHREvaluationPlan:
    """Validate the frozen contract and return a write-free evaluation plan."""

    plan, _ = _prepare_frozen_mlb_hr_research_evaluation(
        feature_pack_path=feature_pack_path,
        label_custody_path=label_custody_path,
        temporal_split_plan_path=temporal_split_plan_path,
        fitted_preprocessing_artifact_path=fitted_preprocessing_artifact_path,
        prediction_artifact_path=prediction_artifact_path,
        model_specification_path=model_specification_path,
    )
    return plan


def _validation_metric_inputs(
    *,
    feature_pack_path: Path,
    label_custody_path: Path,
    temporal_split_plan_path: Path,
    artifact: MLBHRFrozenPredictionArtifact,
) -> tuple[tuple[int, ...], tuple[date, ...], Mapping[str, tuple[float, ...]]]:
    """Read only validation labels, after artifact and handoff gates passed."""

    if artifact.split_id != "validation":
        raise MLBHREvaluationContractError(
            "metric input access is restricted to validation labels"
        )
    authorization = MLBHRLabelOpeningAuthorization(
        split="validation",
        reason="frozen_prediction_validation",
        expected_row_ids=tuple(row.row_id for row in artifact.rows),
        frozen_prediction_artifact_sha256=artifact.artifact_sha256,
    )
    try:
        opened = open_mlb_hr_label_custody_split(
            feature_pack_path=feature_pack_path,
            label_custody_path=label_custody_path,
            temporal_split_plan_path=temporal_split_plan_path,
            authorization=authorization,
        )
    except MLBHRLabelCustodyError as exc:
        raise MLBHREvaluationContractError(
            f"validation label custody failed: {exc}"
        ) from exc
    labels_by_row_id = {row.row_id: row for row in opened}

    labels: list[int] = []
    game_dates: list[date] = []
    raw_probabilities: list[float] = []
    calibrated_probabilities: list[float] = []
    for prediction_row in artifact.rows:
        label_row = labels_by_row_id.get(prediction_row.row_id)
        if (
            label_row is None
            or label_row.game_date != prediction_row.game_date
            or label_row.game_id != prediction_row.game_id
            or label_row.player_id != prediction_row.player_id
        ):
            raise MLBHREvaluationContractError(
                "validation label row is missing after population coverage passed"
            )
        labels.append(int(label_row.is_home_run))
        game_dates.append(prediction_row.game_date)
        raw_probabilities.append(prediction_row.raw_home_run_probability)
        calibrated_probabilities.append(
            prediction_row.calibrated_home_run_probability
        )

    return (
        tuple(labels),
        tuple(game_dates),
        {
            "raw_home_run_probability": tuple(raw_probabilities),
            "calibrated_home_run_probability": tuple(calibrated_probabilities),
        },
    )


def evaluate_frozen_mlb_hr_validation(
    *,
    feature_pack_path: str | Path,
    label_custody_path: str | Path | None = None,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
    prediction_artifact_path: str | Path,
    model_specification_path: str | Path = DEFAULT_MODEL_SPECIFICATION_PATH,
) -> MLBHRValidationEvaluationResult:
    """Compute validation-only metrics in memory after every frozen gate passes."""

    plan, artifact = _prepare_frozen_mlb_hr_research_evaluation(
        feature_pack_path=feature_pack_path,
        label_custody_path=label_custody_path,
        temporal_split_plan_path=temporal_split_plan_path,
        fitted_preprocessing_artifact_path=fitted_preprocessing_artifact_path,
        prediction_artifact_path=prediction_artifact_path,
        model_specification_path=model_specification_path,
    )
    prediction_file_sha256 = _file_sha256(
        plan.prediction_artifact_path, "prediction artifact"
    )
    labels, game_dates, probability_series = _validation_metric_inputs(
        feature_pack_path=plan.feature_pack_path,
        label_custody_path=plan.label_custody_path,
        temporal_split_plan_path=plan.temporal_split_plan_path,
        artifact=artifact,
    )
    if len(labels) != plan.population_coverage.expected_rows:
        raise MLBHREvaluationContractError(
            "validation label population changed after coverage validation"
        )
    try:
        bootstrap = paired_game_date_bootstrap(
            labels=labels,
            game_dates=game_dates,
            probability_series=probability_series,
            replicates=BOOTSTRAP_REPLICATES,
            minimum_successful_replicates=(
                BOOTSTRAP_MINIMUM_SUCCESSFUL_REPLICATES
            ),
            confidence_level=BOOTSTRAP_CONFIDENCE_LEVEL,
            seed=BOOTSTRAP_SEED,
        )
    except MLBHRValidationMetricError as exc:
        raise MLBHREvaluationContractError(
            f"validation metric computation failed: {exc}"
        ) from exc

    final_hashes = (
        _file_sha256(plan.feature_pack_path, "feature pack"),
        _file_sha256(plan.label_custody_path, "label-custody artifact"),
        _file_sha256(plan.temporal_split_plan_path, "temporal split plan"),
        _file_sha256(
            plan.fitted_preprocessing_artifact_path,
            "fitted preprocessing artifact",
        ),
        _file_sha256(plan.prediction_artifact_path, "prediction artifact"),
    )
    if final_hashes != (
        plan.feature_pack_sha256,
        plan.label_custody_sha256,
        plan.temporal_split_plan_sha256,
        plan.fitted_preprocessing_artifact_sha256,
        prediction_file_sha256,
    ):
        raise MLBHREvaluationContractError(
            "an evaluator input artifact changed during validation metrics"
        )
    return MLBHRValidationEvaluationResult(plan=plan, bootstrap=bootstrap)


__all__ = [
    "ALLOWED_EVALUATION_METRICS",
    "ALLOWED_EVALUATION_METRIC_NAMES",
    "BOOTSTRAP_REPLICATES",
    "BOOTSTRAP_SEED",
    "EVALUATOR_CONTRACT_VERSION",
    "IMMUTABLE_EVALUATION_ARTIFACT_POLICY",
    "LABEL_OPENING_SEQUENCE",
    "MISSING_PREDICTION_POLICY",
    "MLBHRBootstrapPolicy",
    "MLBHREvaluationArtifactPolicy",
    "MLBHREvaluationBaselineDefinition",
    "MLBHREvaluationContractError",
    "MLBHREvaluationMetricDefinition",
    "MLBHREvaluationPlan",
    "MLBHRValidationEvaluationResult",
    "MLBHRPredictionPopulationCoverage",
    "MLBHRSegmentationPolicy",
    "REQUIRED_BASELINE_COMPARISONS",
    "SUPPORTED_EVALUATION_SPLITS",
    "VALIDATION_EVALUATOR_VERSION",
    "evaluate_frozen_mlb_hr_validation",
    "plan_frozen_mlb_hr_research_evaluation",
    "validate_mlb_hr_evaluation_label_opening",
    "validate_mlb_hr_prediction_population_coverage",
]
