"""Read-only sealed preprocessing planning for MLB HR research features.

The planner validates an existing feature pack and temporal split, then
computes train-only preprocessing diagnostics in memory.  It does not fit or
persist an executable transformer, train a model, score rows, run a backtest,
or enable any wagering or production gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Final, Mapping, Sequence

from courtvision.sports.mlb.data.historical_backtest_readiness import (
    HistoricalBacktestReadinessVerdict,
)
from courtvision.sports.mlb.data.historical_feature_pack import (
    HistoricalFeaturePackBuildError,
    feature_pack_from_payload,
)
from courtvision.sports.mlb.data.historical_temporal_backtest import (
    MIN_TEST_UNIQUE_DATES,
    MIN_TRAIN_UNIQUE_DATES,
    MIN_VALIDATION_UNIQUE_DATES,
    TemporalBacktestPlanningError,
    TemporalDateWindow,
    TemporalSplitPlan,
    dry_run_historical_research_backtest,
)
from courtvision.sports.mlb.training.hr_feature_allowlist import (
    ALLOWED_MARKET_FEATURES,
    ALLOWED_MLB_HR_RESEARCH_FEATURES,
    MLBHRResearchFeaturePack,
    validate_mlb_hr_feature_pack,
)


PREPROCESSING_POLICY_VERSION: Final = "mlb-hr-sealed-preprocessing-v1"
PREPROCESSING_PLAN_SCHEMA_VERSION: Final = "mlb-hr-preprocessing-plan-v1"
TEMPORAL_SPLIT_ARTIFACT_VERSION: Final = (
    "mlb-hr-research-temporal-split-plan-v1"
)
RARE_CATEGORY_MIN_TRAIN_COUNT: Final = 5
MISSING_CATEGORY_TOKEN: Final = "__MISSING__"
RARE_CATEGORY_TOKEN: Final = "__RARE__"
UNKNOWN_CATEGORY_TOKEN: Final = "__UNKNOWN__"

LINEAGE_FEATURE_NAMES: Final = frozenset(
    {
        "odds_collected_at",
        "odds_as_of",
    }
)
LINEAGE_METADATA_COLUMNS: Final = (
    "row_id",
    "game_id",
    "game_date",
    "player_id",
    "player_name",
    "feature_cutoff_at",
    "odds_collected_at",
    "event_start_time",
    "feature_availability",
)
CATEGORICAL_FEATURE_NAMES: Final = frozenset(
    {
        "batter_hand",
        "pitcher_hand",
        "platoon_side",
        "weather_wind_direction",
        "weather_wind_out_to_field",
        "roof_status",
        "pitcher_pitch_mix_json",
        "sportsbook",
        "odds_provider",
    }
)
BINARY_NUMERIC_FEATURE_NAMES: Final = frozenset(
    {
        "hr_market_available",
        "odds_is_fresh_for_pregame",
    }
)
NUMERIC_FEATURE_NAMES: Final = frozenset(
    ALLOWED_MLB_HR_RESEARCH_FEATURES
    - CATEGORICAL_FEATURE_NAMES
    - LINEAGE_FEATURE_NAMES
)
_RESERVED_CATEGORY_TOKENS: Final = frozenset(
    {
        MISSING_CATEGORY_TOKEN,
        RARE_CATEGORY_TOKEN,
        UNKNOWN_CATEGORY_TOKEN,
    }
)
_DISABLED_GATE_NAMES: Final = (
    "model_training_enabled",
    "backtesting_enabled",
    "predictions_enabled",
    "eligible_for_betting",
    "ev_enabled",
    "kelly_eligible",
    "elite_enabled",
    "staking_enabled",
)

if (
    NUMERIC_FEATURE_NAMES
    | CATEGORICAL_FEATURE_NAMES
    | LINEAGE_FEATURE_NAMES
) != ALLOWED_MLB_HR_RESEARCH_FEATURES:
    raise RuntimeError("preprocessing feature classes do not cover the allowlist")
if (
    NUMERIC_FEATURE_NAMES & CATEGORICAL_FEATURE_NAMES
    or NUMERIC_FEATURE_NAMES & LINEAGE_FEATURE_NAMES
    or CATEGORICAL_FEATURE_NAMES & LINEAGE_FEATURE_NAMES
):
    raise RuntimeError("preprocessing feature classes must not overlap")


class MLBHRPreprocessingPlanningError(ValueError):
    """Raised when a sealed preprocessing plan cannot be produced safely."""


@dataclass(frozen=True, slots=True)
class NumericTrainSummary:
    """Train-only missingness and median for one numeric feature."""

    column: str
    train_row_count: int
    train_missing_count: int
    train_nonmissing_count: int
    train_missing_rate: float
    train_median: float
    imputation: str = "train_median"
    missing_indicator: bool = True


@dataclass(frozen=True, slots=True)
class CategoricalTrainSummary:
    """Train vocabulary and transform-only category diagnostics."""

    column: str
    train_row_count: int
    train_missing_count: int
    train_nonmissing_count: int
    train_missing_rate: float
    retained_train_categories: tuple[str, ...] = field(default_factory=tuple)
    rare_train_categories: tuple[str, ...] = field(default_factory=tuple)
    validation_only_categories: tuple[str, ...] = field(default_factory=tuple)
    test_only_categories: tuple[str, ...] = field(default_factory=tuple)
    missing_token: str = MISSING_CATEGORY_TOKEN
    rare_token: str = RARE_CATEGORY_TOKEN
    unknown_token: str = UNKNOWN_CATEGORY_TOKEN
    encoding: str = "train_vocabulary_one_hot"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "retained_train_categories",
            tuple(self.retained_train_categories),
        )
        object.__setattr__(
            self,
            "rare_train_categories",
            tuple(self.rare_train_categories),
        )
        object.__setattr__(
            self,
            "validation_only_categories",
            tuple(self.validation_only_categories),
        )
        object.__setattr__(
            self,
            "test_only_categories",
            tuple(self.test_only_categories),
        )


@dataclass(frozen=True, slots=True)
class MLBHRPreprocessingPlan:
    """Immutable dry-run plan; never an executable fitted transformer."""

    feature_pack_path: Path
    split_source_path: Path
    split_source_kind: str
    feature_pack_sha256: str
    split_plan: TemporalSplitPlan
    numeric_columns: tuple[str, ...]
    categorical_columns: tuple[str, ...]
    market_columns: tuple[str, ...]
    lineage_columns: tuple[str, ...]
    train_row_count: int
    validation_row_count: int
    test_row_count: int
    numeric_summaries: tuple[NumericTrainSummary, ...]
    categorical_summaries: tuple[CategoricalTrainSummary, ...]
    policy_version: str = PREPROCESSING_POLICY_VERSION
    schema_version: str = PREPROCESSING_PLAN_SCHEMA_VERSION
    fit_split: str = "train"
    validation_transform_only: bool = True
    test_transform_only: bool = True
    feature_firewall_valid: bool = True
    temporal_split_valid: bool = True
    approval_status: str = "not_approved"
    model_training_enabled: bool = False
    backtesting_enabled: bool = False
    predictions_enabled: bool = False
    eligible_for_betting: bool = False
    ev_enabled: bool = False
    kelly_eligible: bool = False
    elite_enabled: bool = False
    staking_enabled: bool = False
    artifacts_written: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "feature_pack_path", self.feature_pack_path.resolve()
        )
        object.__setattr__(
            self, "split_source_path", self.split_source_path.resolve()
        )
        for name in (
            "numeric_columns",
            "categorical_columns",
            "market_columns",
            "lineage_columns",
            "numeric_summaries",
            "categorical_summaries",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.fit_split != "train":
            raise MLBHRPreprocessingPlanningError(
                "preprocessing may be fitted on the train split only"
            )
        if not self.validation_transform_only or not self.test_transform_only:
            raise MLBHRPreprocessingPlanningError(
                "validation and test must remain transform-only"
            )
        if not self.feature_firewall_valid or not self.temporal_split_valid:
            raise MLBHRPreprocessingPlanningError(
                "a preprocessing plan requires valid feature and temporal gates"
            )
        if self.approval_status != "not_approved":
            raise MLBHRPreprocessingPlanningError(
                "preprocessing planning cannot grant production approval"
            )
        if any(getattr(self, name) for name in _DISABLED_GATE_NAMES) or (
            self.artifacts_written
        ):
            raise MLBHRPreprocessingPlanningError(
                "preprocessing planning cannot enable execution or write artifacts"
            )


@dataclass(frozen=True, slots=True)
class _LoadedFeatureArtifact:
    path: Path
    sha256: str
    payload: Mapping[str, object]
    feature_pack: MLBHRResearchFeaturePack


def _read_json_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MLBHRPreprocessingPlanningError(
            f"could not read {label} {path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise MLBHRPreprocessingPlanningError(f"{label} must contain a JSON object")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise MLBHRPreprocessingPlanningError(
            f"could not hash feature pack {path}: {exc}"
        ) from exc
    return digest.hexdigest()


def _load_feature_artifact(path: str | Path) -> _LoadedFeatureArtifact:
    source = Path(path).expanduser().resolve()
    payload = _read_json_object(source, "feature-pack artifact")
    try:
        feature_pack = feature_pack_from_payload(payload)
    except HistoricalFeaturePackBuildError as exc:
        raise MLBHRPreprocessingPlanningError(
            f"feature-pack validation failed: {exc}"
        ) from exc
    firewall = validate_mlb_hr_feature_pack(feature_pack)
    if not firewall.is_valid:
        raise MLBHRPreprocessingPlanningError(
            "feature firewall rejected pack: " + "; ".join(firewall.errors)
        )
    return _LoadedFeatureArtifact(
        path=source,
        sha256=_sha256(source),
        payload=payload,
        feature_pack=feature_pack,
    )


def _require_text(payload: Mapping[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise MLBHRPreprocessingPlanningError(
            f"temporal split plan {field_name} must be non-empty text"
        )
    return value.strip()


def _parse_plan_date(value: object, field_name: str) -> date:
    if not isinstance(value, str):
        raise MLBHRPreprocessingPlanningError(
            f"temporal split plan {field_name} must contain ISO-8601 dates"
        )
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise MLBHRPreprocessingPlanningError(
            f"temporal split plan {field_name} has invalid date: {value!r}"
        ) from exc


def _load_window(payload: Mapping[str, object], name: str) -> TemporalDateWindow:
    raw_window = payload.get(name)
    if not isinstance(raw_window, Mapping):
        raise MLBHRPreprocessingPlanningError(
            f"temporal split plan {name} must be an object"
        )
    raw_dates = raw_window.get("game_dates")
    if not isinstance(raw_dates, list):
        raise MLBHRPreprocessingPlanningError(
            f"temporal split plan {name}.game_dates must be a list"
        )
    parsed = tuple(
        _parse_plan_date(value, f"{name}.game_dates[{index}]")
        for index, value in enumerate(raw_dates)
    )
    if len(parsed) != len(set(parsed)):
        raise MLBHRPreprocessingPlanningError(
            f"temporal split plan {name}.game_dates contains duplicates"
        )
    try:
        return TemporalDateWindow(name=name, game_dates=parsed)
    except TemporalBacktestPlanningError as exc:
        raise MLBHRPreprocessingPlanningError(str(exc)) from exc


def _require_disabled_temporal_gates(payload: Mapping[str, object]) -> None:
    invalid = [name for name in _DISABLED_GATE_NAMES if payload.get(name) is not False]
    if invalid:
        raise MLBHRPreprocessingPlanningError(
            "temporal split plan must explicitly disable gates: "
            + ", ".join(invalid)
        )


def _load_temporal_split_artifact(
    path: str | Path,
    *,
    feature_pack_sha256: str,
) -> TemporalSplitPlan:
    source = Path(path).expanduser().resolve()
    payload = _read_json_object(source, "temporal split plan")
    if payload.get("schema_version") != TEMPORAL_SPLIT_ARTIFACT_VERSION:
        raise MLBHRPreprocessingPlanningError(
            "unsupported temporal split plan schema_version"
        )
    if payload.get("mode") != "historical_research":
        raise MLBHRPreprocessingPlanningError(
            "temporal split plan mode must be 'historical_research'"
        )
    if payload.get("feature_pack_sha256") != feature_pack_sha256:
        raise MLBHRPreprocessingPlanningError(
            "temporal split plan feature_pack_sha256 does not match the feature pack"
        )
    required_verdict = (
        HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
    )
    if payload.get("readiness_verdict") != required_verdict:
        raise MLBHRPreprocessingPlanningError(
            f"temporal split plan requires {required_verdict}"
        )
    if payload.get("approval_status") != "not_approved":
        raise MLBHRPreprocessingPlanningError(
            "temporal split plan approval_status must remain not_approved"
        )
    _require_disabled_temporal_gates(payload)
    split_method = _require_text(payload, "split_method")
    if split_method != "whole_unique_game_dates_60_20_20":
        raise MLBHRPreprocessingPlanningError(
            "temporal split plan must use whole_unique_game_dates_60_20_20"
        )

    try:
        plan = TemporalSplitPlan(
            pack_dir=Path(_require_text(payload, "pack_dir")).expanduser(),
            train=_load_window(payload, "train"),
            validation=_load_window(payload, "validation"),
            test=_load_window(payload, "test"),
            split_method=split_method,
            readiness_verdict=required_verdict,
            approval_status="not_approved",
        )
    except TemporalBacktestPlanningError as exc:
        raise MLBHRPreprocessingPlanningError(str(exc)) from exc

    minimums = (
        ("train", plan.train.unique_date_count, MIN_TRAIN_UNIQUE_DATES),
        (
            "validation",
            plan.validation.unique_date_count,
            MIN_VALIDATION_UNIQUE_DATES,
        ),
        ("test", plan.test.unique_date_count, MIN_TEST_UNIQUE_DATES),
    )
    below_floor = [
        f"{name}={actual} (minimum {minimum})"
        for name, actual, minimum in minimums
        if actual < minimum
    ]
    if below_floor:
        raise MLBHRPreprocessingPlanningError(
            "temporal split plan does not meet unique-date floors: "
            + ", ".join(below_floor)
        )
    total_dates = sum(actual for _, actual, _ in minimums)
    expected_train = total_dates * 3 // 5
    expected_validation = total_dates // 5
    expected_test = total_dates - expected_train - expected_validation
    actual_counts = (
        plan.train.unique_date_count,
        plan.validation.unique_date_count,
        plan.test.unique_date_count,
    )
    expected_counts = (expected_train, expected_validation, expected_test)
    if actual_counts != expected_counts:
        raise MLBHRPreprocessingPlanningError(
            "temporal split plan does not match the declared 60/20/20 method: "
            f"found {actual_counts}, expected {expected_counts}"
        )
    return plan


def _plan_from_staged_pack(
    path: str | Path,
    *,
    feature_pack: MLBHRResearchFeaturePack,
) -> TemporalSplitPlan:
    result = dry_run_historical_research_backtest(path, feature_pack=feature_pack)
    if not result.feature_firewall_checked or not result.feature_firewall_valid:
        details = "; ".join(result.feature_firewall_errors)
        suffix = f": {details}" if details else ""
        raise MLBHRPreprocessingPlanningError(
            "staged-pack dry run did not pass the feature firewall" + suffix
        )
    if result.split_plan is None:
        details = "; ".join(result.refusal_reasons)
        suffix = f": {details}" if details else ""
        raise MLBHRPreprocessingPlanningError(
            "staged-pack dry run did not produce a temporal split" + suffix
        )
    return result.split_plan


def _artifact_rows(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        raise MLBHRPreprocessingPlanningError("feature-pack rows must be a list")
    rows: list[Mapping[str, object]] = []
    for index, row in enumerate(raw_rows):
        if not isinstance(row, Mapping):
            raise MLBHRPreprocessingPlanningError(
                f"feature-pack rows[{index}] must be an object"
            )
        rows.append(row)
    return tuple(rows)


def _row_date(row: Mapping[str, object], row_index: int) -> date:
    value = row.get("game_date")
    if not isinstance(value, str):
        raise MLBHRPreprocessingPlanningError(
            f"feature-pack rows[{row_index}].game_date must be an ISO-8601 date"
        )
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise MLBHRPreprocessingPlanningError(
            f"feature-pack rows[{row_index}].game_date is invalid: {value!r}"
        ) from exc


def _partition_rows(
    rows: Sequence[Mapping[str, object]],
    split_plan: TemporalSplitPlan,
) -> dict[str, tuple[Mapping[str, object], ...]]:
    date_to_split: dict[date, str] = {}
    for window in (split_plan.train, split_plan.validation, split_plan.test):
        for game_date in window.game_dates:
            if game_date in date_to_split:
                raise MLBHRPreprocessingPlanningError(
                    f"game date appears in multiple temporal splits: {game_date}"
                )
            date_to_split[game_date] = window.name

    partitioned: dict[str, list[Mapping[str, object]]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    for row_index, row in enumerate(rows):
        game_date = _row_date(row, row_index)
        split_name = date_to_split.get(game_date)
        if split_name is None:
            raise MLBHRPreprocessingPlanningError(
                f"feature-pack row date is outside the temporal split: {game_date}"
            )
        partitioned[split_name].append(row)
    empty = [name for name, split_rows in partitioned.items() if not split_rows]
    if empty:
        raise MLBHRPreprocessingPlanningError(
            "feature pack has no rows in split(s): " + ", ".join(empty)
        )
    return {name: tuple(split_rows) for name, split_rows in partitioned.items()}


def _feature_value(
    row: Mapping[str, object],
    column: str,
) -> object:
    values = row.get("feature_values")
    if not isinstance(values, Mapping):
        raise MLBHRPreprocessingPlanningError(
            "feature-pack row feature_values must be an object"
        )
    return values.get(column)


def _numeric_value(value: object, column: str) -> float | None:
    if value is None:
        return None
    if column in BINARY_NUMERIC_FEATURE_NAMES:
        if not isinstance(value, bool):
            raise MLBHRPreprocessingPlanningError(
                f"binary numeric feature {column} must contain booleans or null"
            )
        return float(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MLBHRPreprocessingPlanningError(
            f"numeric feature {column} must contain JSON numbers or null"
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        raise MLBHRPreprocessingPlanningError(
            f"numeric feature {column} contains a non-finite value"
        )
    return numeric


def _category_value(value: object, column: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MLBHRPreprocessingPlanningError(
            f"categorical feature {column} must contain strings or null"
        )
    normalized = value.strip()
    if not normalized:
        return None
    if normalized in _RESERVED_CATEGORY_TOKENS:
        raise MLBHRPreprocessingPlanningError(
            f"categorical feature {column} collides with reserved token {normalized}"
        )
    return normalized


def _numeric_summary(
    column: str,
    train_rows: Sequence[Mapping[str, object]],
) -> NumericTrainSummary:
    values = [
        _numeric_value(_feature_value(row, column), column) for row in train_rows
    ]
    observed = [value for value in values if value is not None]
    missing_count = len(values) - len(observed)
    if not observed:
        raise MLBHRPreprocessingPlanningError(
            f"numeric feature {column} is entirely missing in train; median undefined"
        )
    return NumericTrainSummary(
        column=column,
        train_row_count=len(values),
        train_missing_count=missing_count,
        train_nonmissing_count=len(observed),
        train_missing_rate=missing_count / len(values),
        train_median=float(median(observed)),
    )


def _validate_numeric_transform_values(
    columns: Sequence[str],
    partitions: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    for split_name in ("validation", "test"):
        for column in columns:
            for row in partitions[split_name]:
                _numeric_value(_feature_value(row, column), column)


def _category_set(
    rows: Sequence[Mapping[str, object]],
    column: str,
) -> set[str]:
    return {
        value
        for row in rows
        if (value := _category_value(_feature_value(row, column), column))
        is not None
    }


def _categorical_summary(
    column: str,
    partitions: Mapping[str, Sequence[Mapping[str, object]]],
) -> CategoricalTrainSummary:
    train_values = [
        _category_value(_feature_value(row, column), column)
        for row in partitions["train"]
    ]
    counts: dict[str, int] = {}
    for value in train_values:
        if value is not None:
            counts[value] = counts.get(value, 0) + 1
    missing_count = sum(value is None for value in train_values)
    train_categories = set(counts)
    validation_categories = _category_set(partitions["validation"], column)
    test_categories = _category_set(partitions["test"], column)
    retained = tuple(
        sorted(
            category
            for category, count in counts.items()
            if count >= RARE_CATEGORY_MIN_TRAIN_COUNT
        )
    )
    rare = tuple(
        sorted(
            category
            for category, count in counts.items()
            if count < RARE_CATEGORY_MIN_TRAIN_COUNT
        )
    )
    return CategoricalTrainSummary(
        column=column,
        train_row_count=len(train_values),
        train_missing_count=missing_count,
        train_nonmissing_count=len(train_values) - missing_count,
        train_missing_rate=missing_count / len(train_values),
        retained_train_categories=retained,
        rare_train_categories=rare,
        validation_only_categories=tuple(
            sorted(validation_categories - train_categories)
        ),
        test_only_categories=tuple(sorted(test_categories - train_categories)),
    )


def _validate_lineage_feature_values(
    rows: Sequence[Mapping[str, object]],
    lineage_features: Sequence[str],
) -> None:
    for row_index, row in enumerate(rows):
        cutoff_raw = row.get("feature_cutoff_at") or row.get("odds_collected_at")
        start_raw = row.get("event_start_time")
        try:
            cutoff = datetime.fromisoformat(
                str(cutoff_raw).strip().replace("Z", "+00:00")
            )
            start = datetime.fromisoformat(
                str(start_raw).strip().replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise MLBHRPreprocessingPlanningError(
                f"feature-pack rows[{row_index}] has invalid lineage timestamp"
            ) from exc
        if cutoff.tzinfo is None or start.tzinfo is None:
            raise MLBHRPreprocessingPlanningError(
                f"feature-pack rows[{row_index}] lineage timestamps must be aware"
            )
        market_available = _feature_value(row, "hr_market_available")
        for column in lineage_features:
            value = _feature_value(row, column)
            if value is None and market_available is False:
                continue
            if not isinstance(value, str):
                raise MLBHRPreprocessingPlanningError(
                    f"lineage feature {column} must contain aware datetime text; "
                    "null is allowed only when hr_market_available is false"
                )
            try:
                parsed = datetime.fromisoformat(
                    value.strip().replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise MLBHRPreprocessingPlanningError(
                    f"lineage feature {column} contains an invalid datetime"
                ) from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise MLBHRPreprocessingPlanningError(
                    f"lineage feature {column} must be timezone-aware"
                )
            if parsed > cutoff or parsed >= start:
                raise MLBHRPreprocessingPlanningError(
                    f"lineage feature {column} is after the pregame cutoff"
                )


def plan_mlb_hr_preprocessing(
    *,
    feature_pack_path: str | Path,
    temporal_split_plan_path: str | Path | None = None,
    staged_pack_path: str | Path | None = None,
    fit_split: str = "train",
) -> MLBHRPreprocessingPlan:
    """Create an in-memory, train-only preprocessing plan after all gates pass."""

    if fit_split != "train":
        raise MLBHRPreprocessingPlanningError(
            "preprocessing may be fitted on the train split only; validation and "
            "test are transform-only"
        )
    supplied_sources = sum(
        value is not None for value in (temporal_split_plan_path, staged_pack_path)
    )
    if supplied_sources != 1:
        raise MLBHRPreprocessingPlanningError(
            "provide exactly one temporal_split_plan_path or staged_pack_path"
        )

    artifact = _load_feature_artifact(feature_pack_path)
    if temporal_split_plan_path is not None:
        split_source = Path(temporal_split_plan_path).expanduser().resolve()
        split_source_kind = "temporal_split_plan"
        split_plan = _load_temporal_split_artifact(
            split_source,
            feature_pack_sha256=artifact.sha256,
        )
    else:
        assert staged_pack_path is not None
        split_source = Path(staged_pack_path).expanduser().resolve()
        split_source_kind = "staged_pack"
        split_plan = _plan_from_staged_pack(
            split_source,
            feature_pack=artifact.feature_pack,
        )

    rows = _artifact_rows(artifact.payload)
    partitions = _partition_rows(rows, split_plan)
    names = artifact.feature_pack.feature_names
    numeric_columns = tuple(name for name in names if name in NUMERIC_FEATURE_NAMES)
    categorical_columns = tuple(
        name for name in names if name in CATEGORICAL_FEATURE_NAMES
    )
    market_columns = tuple(name for name in names if name in ALLOWED_MARKET_FEATURES)
    lineage_features = tuple(name for name in names if name in LINEAGE_FEATURE_NAMES)
    lineage_columns = tuple(
        dict.fromkeys((*LINEAGE_METADATA_COLUMNS, *lineage_features))
    )

    _validate_lineage_feature_values(rows, lineage_features)
    numeric_summaries = tuple(
        _numeric_summary(column, partitions["train"]) for column in numeric_columns
    )
    _validate_numeric_transform_values(numeric_columns, partitions)
    categorical_summaries = tuple(
        _categorical_summary(column, partitions) for column in categorical_columns
    )
    return MLBHRPreprocessingPlan(
        feature_pack_path=artifact.path,
        split_source_path=split_source,
        split_source_kind=split_source_kind,
        feature_pack_sha256=artifact.sha256,
        split_plan=split_plan,
        numeric_columns=numeric_columns,
        categorical_columns=categorical_columns,
        market_columns=market_columns,
        lineage_columns=lineage_columns,
        train_row_count=len(partitions["train"]),
        validation_row_count=len(partitions["validation"]),
        test_row_count=len(partitions["test"]),
        numeric_summaries=numeric_summaries,
        categorical_summaries=categorical_summaries,
    )


__all__ = [
    "BINARY_NUMERIC_FEATURE_NAMES",
    "CATEGORICAL_FEATURE_NAMES",
    "LINEAGE_FEATURE_NAMES",
    "LINEAGE_METADATA_COLUMNS",
    "MISSING_CATEGORY_TOKEN",
    "MLBHRPreprocessingPlan",
    "MLBHRPreprocessingPlanningError",
    "NUMERIC_FEATURE_NAMES",
    "PREPROCESSING_PLAN_SCHEMA_VERSION",
    "PREPROCESSING_POLICY_VERSION",
    "RARE_CATEGORY_MIN_TRAIN_COUNT",
    "RARE_CATEGORY_TOKEN",
    "TEMPORAL_SPLIT_ARTIFACT_VERSION",
    "UNKNOWN_CATEGORY_TOKEN",
    "CategoricalTrainSummary",
    "NumericTrainSummary",
    "plan_mlb_hr_preprocessing",
]
