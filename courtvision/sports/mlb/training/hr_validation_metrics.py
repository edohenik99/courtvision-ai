"""Deterministic validation metrics for frozen MLB HR predictions.

This module is deliberately limited to binary research evaluation.  It has no
training, prediction, fetching, wagering, approval, or artifact-writing path.
Bootstrap samples are paired across every supplied probability series and are
drawn as whole game-date blocks with the frozen seed and iteration count.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
import random
from typing import Callable, Final, Mapping, Sequence


LOG_LOSS_CLIP: Final = 1e-15
CALIBRATION_BIN_COUNT: Final = 10
DEFAULT_BOOTSTRAP_SEED: Final = 20260629
DEFAULT_BOOTSTRAP_REPLICATES: Final = 2_000
DEFAULT_BOOTSTRAP_MINIMUM_SUCCESSFUL_REPLICATES: Final = 1_900
DEFAULT_BOOTSTRAP_CONFIDENCE_LEVEL: Final = 0.95
METRIC_NAMES: Final = (
    "log_loss",
    "brier_score",
    "roc_auc",
    "pr_auc",
    "calibration_error",
)


class MLBHRValidationMetricError(ValueError):
    """Raised when validation metric inputs or estimates are invalid."""


@dataclass(frozen=True, slots=True)
class MLBHRMetricEstimate:
    """One named point estimate."""

    name: str
    value: float

    def __post_init__(self) -> None:
        if self.name not in METRIC_NAMES or not math.isfinite(self.value):
            raise MLBHRValidationMetricError("invalid validation metric estimate")


@dataclass(frozen=True, slots=True)
class MLBHRBootstrapInterval:
    """One deterministic percentile interval from paired date-block draws."""

    series_name: str
    metric_name: str
    estimate: float
    lower_bound: float | None
    upper_bound: float | None
    confidence_level: float
    requested_replicates: int
    successful_replicates: int
    seed: int
    status: str

    def __post_init__(self) -> None:
        if (
            not self.series_name.strip()
            or self.metric_name not in METRIC_NAMES
            or not math.isfinite(self.estimate)
            or not 0.0 < self.confidence_level < 1.0
            or self.requested_replicates <= 0
            or not 0 <= self.successful_replicates <= self.requested_replicates
            or self.status not in {"estimated", "inconclusive"}
        ):
            raise MLBHRValidationMetricError("invalid bootstrap interval")
        if self.status == "estimated":
            if (
                self.lower_bound is None
                or self.upper_bound is None
                or not math.isfinite(self.lower_bound)
                or not math.isfinite(self.upper_bound)
                or self.lower_bound > self.upper_bound
            ):
                raise MLBHRValidationMetricError(
                    "estimated bootstrap interval requires finite ordered bounds"
                )
        elif self.lower_bound is not None or self.upper_bound is not None:
            raise MLBHRValidationMetricError(
                "inconclusive bootstrap interval cannot report bounds"
            )


@dataclass(frozen=True, slots=True)
class MLBHRPairedGameDateBootstrapResult:
    """All intervals produced from one shared sequence of date-block draws."""

    intervals: tuple[MLBHRBootstrapInterval, ...]
    row_count: int
    positive_count: int
    negative_count: int
    unique_game_date_count: int
    requested_replicates: int
    minimum_successful_replicates: int
    confidence_level: float
    seed: int
    unit: str = "game_date_block"
    method: str = "paired_percentile_bootstrap"
    deterministic: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "intervals", tuple(self.intervals))
        if (
            not self.intervals
            or self.row_count <= 0
            or self.positive_count < 0
            or self.negative_count < 0
            or self.positive_count + self.negative_count != self.row_count
            or self.unique_game_date_count <= 0
            or self.requested_replicates <= 0
            or not 0 < self.minimum_successful_replicates
            <= self.requested_replicates
            or not 0.0 < self.confidence_level < 1.0
            or self.unit != "game_date_block"
            or self.method != "paired_percentile_bootstrap"
            or not self.deterministic
        ):
            raise MLBHRValidationMetricError("invalid paired bootstrap result")


def _validated_labels(labels: Sequence[object]) -> tuple[int, ...]:
    if not labels:
        raise MLBHRValidationMetricError("labels must be non-empty")
    validated: list[int] = []
    for index, value in enumerate(labels):
        if type(value) not in (bool, int) or int(value) not in (0, 1):
            raise MLBHRValidationMetricError(
                f"labels[{index}] must be binary boolean or integer 0/1"
            )
        validated.append(int(value))
    return tuple(validated)


def _validated_probabilities(
    probabilities: Sequence[object],
    *,
    expected_length: int,
    location: str = "probabilities",
) -> tuple[float, ...]:
    if len(probabilities) != expected_length:
        raise MLBHRValidationMetricError(
            f"{location} length must match labels length"
        )
    validated: list[float] = []
    for index, value in enumerate(probabilities):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MLBHRValidationMetricError(
                f"{location}[{index}] must be a finite number in [0, 1]"
            )
        probability = float(value)
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise MLBHRValidationMetricError(
                f"{location}[{index}] must be a finite number in [0, 1]"
            )
        validated.append(probability)
    return tuple(validated)


def _log_loss(labels: Sequence[int], probabilities: Sequence[float]) -> float:
    total = 0.0
    for label, probability in zip(labels, probabilities, strict=True):
        clipped = min(max(probability, LOG_LOSS_CLIP), 1.0 - LOG_LOSS_CLIP)
        total -= label * math.log(clipped) + (1 - label) * math.log1p(-clipped)
    return total / len(labels)


def _brier_score(labels: Sequence[int], probabilities: Sequence[float]) -> float:
    return sum(
        (probability - label) ** 2
        for label, probability in zip(labels, probabilities, strict=True)
    ) / len(labels)


def _roc_auc(labels: Sequence[int], probabilities: Sequence[float]) -> float:
    positive_count = sum(labels)
    negative_count = len(labels) - positive_count
    if positive_count == 0 or negative_count == 0:
        raise MLBHRValidationMetricError(
            "ROC-AUC requires at least one positive and one negative label"
        )

    ordered = sorted(zip(probabilities, labels, strict=True), key=lambda item: item[0])
    rank_sum = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        average_rank = ((index + 1) + end) / 2.0
        rank_sum += average_rank * sum(label for _, label in ordered[index:end])
        index = end
    return (
        rank_sum - positive_count * (positive_count + 1) / 2.0
    ) / (positive_count * negative_count)


def _pr_auc(labels: Sequence[int], probabilities: Sequence[float]) -> float:
    positive_count = sum(labels)
    if positive_count == 0:
        raise MLBHRValidationMetricError("PR-AUC requires at least one positive label")

    ordered = sorted(
        zip(probabilities, labels, strict=True),
        key=lambda item: item[0],
        reverse=True,
    )
    cumulative_rows = 0
    cumulative_positives = 0
    average_precision = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        group = ordered[index:end]
        group_positives = sum(label for _, label in group)
        cumulative_rows += len(group)
        cumulative_positives += group_positives
        if group_positives:
            precision = cumulative_positives / cumulative_rows
            recall_increment = group_positives / positive_count
            average_precision += precision * recall_increment
        index = end
    return average_precision


def _calibration_error(
    labels: Sequence[int], probabilities: Sequence[float]
) -> float:
    bin_counts = [0] * CALIBRATION_BIN_COUNT
    bin_probability_sums = [0.0] * CALIBRATION_BIN_COUNT
    bin_label_sums = [0] * CALIBRATION_BIN_COUNT
    for label, probability in zip(labels, probabilities, strict=True):
        bin_index = min(int(probability * CALIBRATION_BIN_COUNT), 9)
        bin_counts[bin_index] += 1
        bin_probability_sums[bin_index] += probability
        bin_label_sums[bin_index] += label

    total = len(labels)
    return sum(
        (count / total)
        * abs(
            bin_label_sums[index] / count
            - bin_probability_sums[index] / count
        )
        for index, count in enumerate(bin_counts)
        if count
    )


_MetricFunction = Callable[[Sequence[int], Sequence[float]], float]
_METRIC_FUNCTIONS: Final[Mapping[str, _MetricFunction]] = {
    "log_loss": _log_loss,
    "brier_score": _brier_score,
    "roc_auc": _roc_auc,
    "pr_auc": _pr_auc,
    "calibration_error": _calibration_error,
}


def _compute_metric(
    name: str,
    labels: Sequence[int],
    probabilities: Sequence[float],
) -> float:
    try:
        function = _METRIC_FUNCTIONS[name]
    except KeyError as exc:
        raise MLBHRValidationMetricError(f"unsupported metric: {name}") from exc
    return function(labels, probabilities)


def log_loss(labels: Sequence[object], probabilities: Sequence[object]) -> float:
    validated_labels = _validated_labels(labels)
    validated_probabilities = _validated_probabilities(
        probabilities, expected_length=len(validated_labels)
    )
    return _log_loss(validated_labels, validated_probabilities)


def brier_score(labels: Sequence[object], probabilities: Sequence[object]) -> float:
    validated_labels = _validated_labels(labels)
    validated_probabilities = _validated_probabilities(
        probabilities, expected_length=len(validated_labels)
    )
    return _brier_score(validated_labels, validated_probabilities)


def roc_auc(labels: Sequence[object], probabilities: Sequence[object]) -> float:
    validated_labels = _validated_labels(labels)
    validated_probabilities = _validated_probabilities(
        probabilities, expected_length=len(validated_labels)
    )
    return _roc_auc(validated_labels, validated_probabilities)


def pr_auc(labels: Sequence[object], probabilities: Sequence[object]) -> float:
    validated_labels = _validated_labels(labels)
    validated_probabilities = _validated_probabilities(
        probabilities, expected_length=len(validated_labels)
    )
    return _pr_auc(validated_labels, validated_probabilities)


def calibration_error(
    labels: Sequence[object], probabilities: Sequence[object]
) -> float:
    validated_labels = _validated_labels(labels)
    validated_probabilities = _validated_probabilities(
        probabilities, expected_length=len(validated_labels)
    )
    return _calibration_error(validated_labels, validated_probabilities)


def compute_binary_metrics(
    labels: Sequence[object], probabilities: Sequence[object]
) -> tuple[MLBHRMetricEstimate, ...]:
    """Compute exactly the five frozen validation metrics."""

    validated_labels = _validated_labels(labels)
    validated_probabilities = _validated_probabilities(
        probabilities, expected_length=len(validated_labels)
    )
    return tuple(
        MLBHRMetricEstimate(
            name=name,
            value=_compute_metric(name, validated_labels, validated_probabilities),
        )
        for name in METRIC_NAMES
    )


def _normalized_game_dates(
    game_dates: Sequence[date | str], *, expected_length: int
) -> tuple[str, ...]:
    if len(game_dates) != expected_length:
        raise MLBHRValidationMetricError(
            "game_dates length must match labels length"
        )
    normalized: list[str] = []
    for index, value in enumerate(game_dates):
        if isinstance(value, date):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = date.fromisoformat(value)
            except ValueError as exc:
                raise MLBHRValidationMetricError(
                    f"game_dates[{index}] must be an ISO-8601 date"
                ) from exc
        else:
            raise MLBHRValidationMetricError(
                f"game_dates[{index}] must be an ISO-8601 date"
            )
        normalized.append(parsed.isoformat())
    return tuple(normalized)


def _percentile(sorted_values: Sequence[float], quantile: float) -> float:
    position = (len(sorted_values) - 1) * quantile
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return sorted_values[lower_index]
    fraction = position - lower_index
    return (
        sorted_values[lower_index] * (1.0 - fraction)
        + sorted_values[upper_index] * fraction
    )


def paired_game_date_bootstrap(
    *,
    labels: Sequence[object],
    game_dates: Sequence[date | str],
    probability_series: Mapping[str, Sequence[object]],
    replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    minimum_successful_replicates: int = (
        DEFAULT_BOOTSTRAP_MINIMUM_SUCCESSFUL_REPLICATES
    ),
    confidence_level: float = DEFAULT_BOOTSTRAP_CONFIDENCE_LEVEL,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> MLBHRPairedGameDateBootstrapResult:
    """Return paired percentile intervals from whole game-date resamples.

    The default policy is fixed at 2,000 draws with seed 20260629.  One draw
    samples the original number of unique dates with replacement and includes
    every row from each selected date.  All probability series and metrics use
    the same sampled row indices, making the output deterministic and paired.
    """

    if (
        replicates <= 0
        or not 0 < minimum_successful_replicates <= replicates
        or not 0.0 < confidence_level < 1.0
        or isinstance(seed, bool)
        or not isinstance(seed, int)
    ):
        raise MLBHRValidationMetricError("invalid bootstrap policy")
    validated_labels = _validated_labels(labels)
    normalized_dates = _normalized_game_dates(
        game_dates, expected_length=len(validated_labels)
    )
    if not probability_series:
        raise MLBHRValidationMetricError(
            "probability_series must contain at least one named series"
        )
    validated_series: dict[str, tuple[float, ...]] = {}
    for raw_name, probabilities in probability_series.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise MLBHRValidationMetricError(
                "probability series names must be non-empty text"
            )
        if raw_name in validated_series:
            raise MLBHRValidationMetricError("probability series names must be unique")
        validated_series[raw_name] = _validated_probabilities(
            probabilities,
            expected_length=len(validated_labels),
            location=f"probability_series[{raw_name!r}]",
        )

    blocks: dict[str, list[int]] = {}
    for index, game_date_text in enumerate(normalized_dates):
        blocks.setdefault(game_date_text, []).append(index)
    ordered_dates = tuple(sorted(blocks))
    ordered_series_names = tuple(sorted(validated_series))
    sample_values: dict[tuple[str, str], list[float]] = {
        (series_name, metric_name): []
        for series_name in ordered_series_names
        for metric_name in METRIC_NAMES
    }

    generator = random.Random(seed)
    for _ in range(replicates):
        sampled_indices: list[int] = []
        for _ in ordered_dates:
            sampled_date = ordered_dates[generator.randrange(len(ordered_dates))]
            sampled_indices.extend(blocks[sampled_date])
        sampled_labels = tuple(validated_labels[index] for index in sampled_indices)
        for series_name in ordered_series_names:
            probabilities = validated_series[series_name]
            sampled_probabilities = tuple(
                probabilities[index] for index in sampled_indices
            )
            for metric_name in METRIC_NAMES:
                try:
                    value = _compute_metric(
                        metric_name, sampled_labels, sampled_probabilities
                    )
                except MLBHRValidationMetricError:
                    continue
                sample_values[(series_name, metric_name)].append(value)

    tail_probability = (1.0 - confidence_level) / 2.0
    intervals: list[MLBHRBootstrapInterval] = []
    for series_name in ordered_series_names:
        probabilities = validated_series[series_name]
        for metric_name in METRIC_NAMES:
            estimate = _compute_metric(metric_name, validated_labels, probabilities)
            values = sorted(sample_values[(series_name, metric_name)])
            successful = len(values)
            estimable = successful >= minimum_successful_replicates
            intervals.append(
                MLBHRBootstrapInterval(
                    series_name=series_name,
                    metric_name=metric_name,
                    estimate=estimate,
                    lower_bound=(
                        _percentile(values, tail_probability) if estimable else None
                    ),
                    upper_bound=(
                        _percentile(values, 1.0 - tail_probability)
                        if estimable
                        else None
                    ),
                    confidence_level=confidence_level,
                    requested_replicates=replicates,
                    successful_replicates=successful,
                    seed=seed,
                    status="estimated" if estimable else "inconclusive",
                )
            )

    positive_count = sum(validated_labels)
    return MLBHRPairedGameDateBootstrapResult(
        intervals=tuple(intervals),
        row_count=len(validated_labels),
        positive_count=positive_count,
        negative_count=len(validated_labels) - positive_count,
        unique_game_date_count=len(ordered_dates),
        requested_replicates=replicates,
        minimum_successful_replicates=minimum_successful_replicates,
        confidence_level=confidence_level,
        seed=seed,
    )


__all__ = [
    "CALIBRATION_BIN_COUNT",
    "DEFAULT_BOOTSTRAP_CONFIDENCE_LEVEL",
    "DEFAULT_BOOTSTRAP_MINIMUM_SUCCESSFUL_REPLICATES",
    "DEFAULT_BOOTSTRAP_REPLICATES",
    "DEFAULT_BOOTSTRAP_SEED",
    "LOG_LOSS_CLIP",
    "METRIC_NAMES",
    "MLBHRBootstrapInterval",
    "MLBHRMetricEstimate",
    "MLBHRPairedGameDateBootstrapResult",
    "MLBHRValidationMetricError",
    "brier_score",
    "calibration_error",
    "compute_binary_metrics",
    "log_loss",
    "paired_game_date_bootstrap",
    "pr_auc",
    "roc_auc",
]
