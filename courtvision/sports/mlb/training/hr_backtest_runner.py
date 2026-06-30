"""Sealed, read-only planning shell for future MLB HR research backtests.

This module validates the complete backtest input contract and returns an
in-memory execution plan.  It deliberately has no transformer, estimator,
prediction, metric-computation, fetching, wagering, or artifact-writing code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Final, Mapping

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
    assert_model_visible_feature_pack_label_free,
    resolve_label_custody_path,
    validate_mlb_hr_label_custody,
)


BACKTEST_RUNNER_CONTRACT_VERSION: Final = (
    "mlb-hr-sealed-research-backtest-runner-v1"
)
EVALUATION_LABEL_COLUMNS: Final = ("is_home_run",)
LABEL_ACCESS_SCOPE: Final = "evaluation_planning_only"


class MLBHRBacktestRunnerContractError(ValueError):
    """Raised when a sealed dry-run execution plan must fail closed."""


@dataclass(frozen=True, slots=True)
class MLBHRBacktestMetricDefinition:
    """One predeclared metric; the dry-run shell never computes it."""

    name: str
    role: str
    definition: str
    direction: str
    status: str = "planned_not_computed"


BACKTEST_METRIC_DEFINITIONS: Final = (
    MLBHRBacktestMetricDefinition(
        name="log_loss",
        role="primary",
        definition=(
            "mean negative Bernoulli log likelihood over independent "
            "player-games"
        ),
        direction="lower_is_better",
    ),
    MLBHRBacktestMetricDefinition(
        name="brier_score",
        role="secondary_calibration",
        definition="mean squared error between HR probability and binary outcome",
        direction="lower_is_better",
    ),
    MLBHRBacktestMetricDefinition(
        name="roc_auc",
        role="secondary_discrimination",
        definition=(
            "probability that a randomly selected HR-positive player-game "
            "ranks above a randomly selected HR-negative player-game"
        ),
        direction="higher_is_better",
    ),
    MLBHRBacktestMetricDefinition(
        name="average_precision",
        role="secondary_discrimination",
        definition=(
            "positive-class precision averaged across recall increments"
        ),
        direction="higher_is_better",
    ),
    MLBHRBacktestMetricDefinition(
        name="calibration_intercept",
        role="diagnostic_only",
        definition=(
            "intercept from outcome on prediction log-odds with slope fixed at one"
        ),
        direction="closer_to_zero_is_better",
    ),
    MLBHRBacktestMetricDefinition(
        name="calibration_slope",
        role="diagnostic_only",
        definition="slope from outcome on prediction log-odds",
        direction="closer_to_one_is_better",
    ),
)


@dataclass(frozen=True, slots=True)
class MLBHRBacktestWindowExecutionPlan:
    """Read-only boundary declaration for one validated temporal window."""

    name: str
    date_start: date
    date_end: date
    player_game_rows: int
    positive_labels: int | None
    negative_labels: int | None
    preprocessing_boundary: str
    current_action: str = "validate_only"
    model_action: str = "prohibited"
    prediction_action: str = "prohibited"
    metric_action: str = "planned_not_computed"

    def __post_init__(self) -> None:
        expected_boundaries = {
            "train": "train_fitted_artifact_validate_only",
            "validation": "transform_only_no_refit_if_separately_approved",
            "test": "transform_only_no_refit_if_separately_approved",
        }
        if (
            self.name not in expected_boundaries
            or self.preprocessing_boundary != expected_boundaries.get(self.name)
            or self.date_start > self.date_end
            or self.player_game_rows <= 0
            or self.positive_labels is not None
            or self.negative_labels is not None
            or self.current_action != "validate_only"
            or self.model_action != "prohibited"
            or self.prediction_action != "prohibited"
            or self.metric_action != "planned_not_computed"
        ):
            raise MLBHRBacktestRunnerContractError(
                "a window plan cannot relax the validation-only boundary"
            )


@dataclass(frozen=True, slots=True)
class MLBHRBacktestExecutionPlan:
    """Immutable in-memory plan proving that the safety shell passed."""

    feature_pack_path: Path
    label_custody_path: Path
    temporal_split_plan_path: Path
    fitted_preprocessing_artifact_path: Path
    feature_pack_sha256: str
    label_custody_sha256: str
    feature_row_identity_sha256: str
    temporal_split_plan_sha256: str
    fitted_preprocessing_artifact_sha256: str
    windows: tuple[MLBHRBacktestWindowExecutionPlan, ...]
    metric_definitions: tuple[MLBHRBacktestMetricDefinition, ...] = field(
        default_factory=lambda: BACKTEST_METRIC_DEFINITIONS
    )
    contract_version: str = BACKTEST_RUNNER_CONTRACT_VERSION
    status: str = "BACKTEST_EXECUTION_PLAN_ONLY"
    feature_firewall_valid: bool = True
    temporal_split_valid: bool = True
    preprocessing_artifact_valid: bool = True
    window_population_valid: bool = True
    label_access_valid: bool = True
    label_columns: tuple[str, ...] = EVALUATION_LABEL_COLUMNS
    label_access_scope: str = LABEL_ACCESS_SCOPE
    preprocessing_fit_split: str = "train"
    validation_preprocessing_boundary: str = "transform_only_no_refit"
    test_preprocessing_boundary: str = "transform_only_no_refit"
    research_only: bool = True
    approval_status: str = "not_approved"
    execution_authorized: bool = False
    model_training_enabled: bool = False
    preprocessing_transform_enabled: bool = False
    predictions_enabled: bool = False
    metric_computation_enabled: bool = False
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
        object.__setattr__(self, "windows", tuple(self.windows))
        object.__setattr__(self, "metric_definitions", tuple(self.metric_definitions))
        object.__setattr__(self, "label_columns", tuple(self.label_columns))
        required_gates = (
            self.feature_firewall_valid,
            self.temporal_split_valid,
            self.preprocessing_artifact_valid,
            self.window_population_valid,
            self.label_access_valid,
        )
        if not all(required_gates):
            raise MLBHRBacktestRunnerContractError(
                "an execution plan requires every label-sealed structural gate to pass"
            )
        if (
            self.contract_version != BACKTEST_RUNNER_CONTRACT_VERSION
            or self.status != "BACKTEST_EXECUTION_PLAN_ONLY"
            or self.metric_definitions != BACKTEST_METRIC_DEFINITIONS
            or tuple(window.name for window in self.windows)
            != ("train", "validation", "test")
            or self.label_columns != EVALUATION_LABEL_COLUMNS
            or self.label_access_scope != LABEL_ACCESS_SCOPE
            or self.preprocessing_fit_split != "train"
            or self.validation_preprocessing_boundary != "transform_only_no_refit"
            or self.test_preprocessing_boundary != "transform_only_no_refit"
            or not self.research_only
            or self.approval_status != "not_approved"
        ):
            raise MLBHRBacktestRunnerContractError(
                "the sealed backtest planning boundary cannot be relaxed"
            )
        enabled_actions = (
            self.execution_authorized,
            self.model_training_enabled,
            self.preprocessing_transform_enabled,
            self.predictions_enabled,
            self.metric_computation_enabled,
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
        if any(enabled_actions):
            raise MLBHRBacktestRunnerContractError(
                "a dry-run plan cannot execute, write, or enable wagering"
            )


def _file_sha256(path: Path, label: str) -> str:
    if not path.is_file():
        raise MLBHRBacktestRunnerContractError(
            f"{label} must be an existing local file: {path}"
        )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise MLBHRBacktestRunnerContractError(
            f"could not hash {label} {path}: {exc}"
        ) from exc
    return digest.hexdigest()


def _read_feature_payload(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MLBHRBacktestRunnerContractError(
            f"could not read feature pack for label-access planning: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise MLBHRBacktestRunnerContractError(
            "feature pack must contain a JSON object"
        )
    return payload


def _validate_evaluation_label_access(
    path: Path, label_custody_path: Path
) -> None:
    """Validate physical custody binding without opening a label value."""

    payload = _read_feature_payload(path)
    try:
        assert_model_visible_feature_pack_label_free(payload)
        validate_mlb_hr_label_custody(
            feature_pack_path=path,
            label_custody_path=label_custody_path,
        )
    except MLBHRLabelCustodyError as exc:
        raise MLBHRBacktestRunnerContractError(str(exc)) from exc
    raw_names = payload.get("feature_names")
    if not isinstance(raw_names, list):
        raise MLBHRBacktestRunnerContractError(
            "feature-pack feature_names must be a list"
        )
    leaked_names = [
        name
        for name in raw_names
        if isinstance(name, str)
        and classify_mlb_hr_research_field(name)
        is MLBHRFeatureFieldClass.LABEL_OUTCOME
    ]
    if leaked_names:
        raise MLBHRBacktestRunnerContractError(
            "evaluation labels cannot be declared as features: "
            + ", ".join(sorted(set(leaked_names)))
        )

    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise MLBHRBacktestRunnerContractError(
            "feature-pack rows must be a non-empty list for evaluation planning"
        )
    for index, raw_row in enumerate(raw_rows):
        if not isinstance(raw_row, Mapping):
            raise MLBHRBacktestRunnerContractError(
                f"feature-pack rows[{index}] must be an object"
            )
        unsupported_labels = [
            str(name)
            for name in raw_row
            if classify_mlb_hr_research_field(str(name))
            is MLBHRFeatureFieldClass.LABEL_OUTCOME
        ]
        if unsupported_labels:
            raise MLBHRBacktestRunnerContractError(
                f"feature-pack rows[{index}] contains unsupported evaluation "
                "label columns: "
                + ", ".join(sorted(set(unsupported_labels)))
            )
        values = raw_row.get("feature_values")
        if not isinstance(values, Mapping):
            raise MLBHRBacktestRunnerContractError(
                f"feature-pack rows[{index}].feature_values must be an object"
            )
        leaked_values = [
            name
            for name in values
            if classify_mlb_hr_research_field(str(name))
            is MLBHRFeatureFieldClass.LABEL_OUTCOME
        ]
        if leaked_values:
            raise MLBHRBacktestRunnerContractError(
                f"feature-pack rows[{index}] exposes evaluation labels in "
                "feature_values: " + ", ".join(sorted(set(leaked_values)))
            )


def _window_plans(
    feature_payload: Mapping[str, object], split_plan: object
) -> tuple[MLBHRBacktestWindowExecutionPlan, ...]:
    raw_rows = feature_payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise MLBHRBacktestRunnerContractError(
            "feature-pack rows must be a non-empty list"
        )
    planned: list[MLBHRBacktestWindowExecutionPlan] = []
    for name in ("train", "validation", "test"):
        window = getattr(split_plan, name)
        window_dates = frozenset(window.game_dates)
        identities: set[tuple[date, str, str]] = set()
        for index, raw_row in enumerate(raw_rows):
            if not isinstance(raw_row, Mapping):
                raise MLBHRBacktestRunnerContractError(
                    f"feature-pack rows[{index}] must be an object"
                )
            try:
                game_date = date.fromisoformat(str(raw_row.get("game_date")))
            except ValueError as exc:
                raise MLBHRBacktestRunnerContractError(
                    f"feature-pack rows[{index}].game_date must be ISO-8601"
                ) from exc
            if game_date not in window_dates:
                continue
            game_id = raw_row.get("game_id")
            player_id = raw_row.get("player_id")
            if not isinstance(game_id, str) or not isinstance(player_id, str):
                raise MLBHRBacktestRunnerContractError(
                    f"feature-pack rows[{index}] has invalid player-game identity"
                )
            identities.add((game_date, game_id, player_id))
        if not identities:
            raise MLBHRBacktestRunnerContractError(
                f"{name} window has no feature rows"
            )
        boundary = (
            "train_fitted_artifact_validate_only"
            if name == "train"
            else "transform_only_no_refit_if_separately_approved"
        )
        observed_dates = tuple(identity[0] for identity in identities)
        planned.append(
            MLBHRBacktestWindowExecutionPlan(
                name=name,
                date_start=min(observed_dates),
                date_end=max(observed_dates),
                player_game_rows=len(identities),
                positive_labels=None,
                negative_labels=None,
                preprocessing_boundary=boundary,
            )
        )
    return tuple(planned)


def plan_sealed_mlb_hr_research_backtest(
    *,
    feature_pack_path: str | Path,
    label_custody_path: str | Path | None = None,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
) -> MLBHRBacktestExecutionPlan:
    """Validate all gates and return a non-executable, write-free plan."""

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

    try:
        preprocessing_plan = plan_mlb_hr_preprocessing(
            feature_pack_path=feature_source,
            temporal_split_plan_path=split_source,
        )
    except MLBHRPreprocessingPlanningError as exc:
        raise MLBHRBacktestRunnerContractError(
            f"feature-firewall or temporal-split gate failed: {exc}"
        ) from exc

    try:
        artifact = load_fitted_preprocessing_artifact(
            preprocessing_source,
            feature_pack_path=feature_source,
            temporal_split_plan_path=split_source,
        )
    except MLBHRFittedPreprocessingArtifactError as exc:
        raise MLBHRBacktestRunnerContractError(
            f"fitted preprocessing artifact gate failed: {exc}"
        ) from exc
    if (
        preprocessing_plan.fit_split != "train"
        or not preprocessing_plan.validation_transform_only
        or not preprocessing_plan.test_transform_only
        or artifact.train_date_start != preprocessing_plan.split_plan.train.start
        or artifact.train_date_end != preprocessing_plan.split_plan.train.end
    ):
        raise MLBHRBacktestRunnerContractError(
            "fitted preprocessing artifact violates the train-only/transform-only "
            "boundary"
        )

    _validate_evaluation_label_access(feature_source, custody_source)
    feature_payload = _read_feature_payload(feature_source)

    final_hashes = tuple(_file_sha256(path, label) for path, label in sources)
    if final_hashes != initial_hashes:
        raise MLBHRBacktestRunnerContractError(
            "an input artifact changed during dry-run validation"
        )

    return MLBHRBacktestExecutionPlan(
        feature_pack_path=feature_source,
        label_custody_path=custody_source,
        temporal_split_plan_path=split_source,
        fitted_preprocessing_artifact_path=preprocessing_source,
        feature_pack_sha256=initial_hashes[0],
        label_custody_sha256=initial_hashes[1],
        feature_row_identity_sha256=(
            validate_mlb_hr_label_custody(
                feature_pack_path=feature_source,
                label_custody_path=custody_source,
            ).row_identity_sha256
        ),
        temporal_split_plan_sha256=initial_hashes[2],
        fitted_preprocessing_artifact_sha256=initial_hashes[3],
        windows=_window_plans(feature_payload, preprocessing_plan.split_plan),
    )


__all__ = [
    "BACKTEST_METRIC_DEFINITIONS",
    "BACKTEST_RUNNER_CONTRACT_VERSION",
    "EVALUATION_LABEL_COLUMNS",
    "LABEL_ACCESS_SCOPE",
    "MLBHRBacktestExecutionPlan",
    "MLBHRBacktestMetricDefinition",
    "MLBHRBacktestRunnerContractError",
    "MLBHRBacktestWindowExecutionPlan",
    "plan_sealed_mlb_hr_research_backtest",
]
