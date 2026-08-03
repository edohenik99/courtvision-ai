"""Deterministic observational metrics for Phase 1 model evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
import math
from typing import Callable, Iterable

from courtvision.evaluation.model_records import ModelEvaluationRecord, Outcome


# These preserve the legacy pick-history display cutoffs while making the
# previously uncovered below-0.55 range explicit. Confidence remains a score.
CONFIDENCE_SCORE_BUCKETS = (
    "<0.55",
    "0.55-<0.65",
    "0.65-<0.75",
    "0.75-<0.85",
    ">=0.85",
    "Unknown",
)

# Legacy raw edge is analyzed by absolute magnitude in stat units.
EDGE_STAT_UNIT_BUCKETS = (
    "<1",
    "1-<2",
    "2-<3",
    "3-<5",
    ">=5",
    "Unknown",
)

STALE_AFTER_DAYS = 30


class SlateWindow(str, Enum):
    LAST_7 = "Last 7 slates"
    LAST_30 = "Last 30 slates"
    ALL = "All available slates"


@dataclass(frozen=True, slots=True)
class HitRateMetrics:
    wins: int
    losses: int
    decisive_sample: int
    hit_rate: float | None


@dataclass(frozen=True, slots=True)
class FlatUnitRoiMetrics:
    roi: float | None
    net_flat_units: float
    eligible_priced_decisions: int
    odds_coverage_count: int
    odds_coverage_percentage: float | None
    excluded_count: int


@dataclass(frozen=True, slots=True)
class BucketMetrics:
    bucket: str
    sample_size: int
    wins: int
    losses: int
    decisive_sample: int
    hit_rate: float | None
    roi: float | None
    net_flat_units: float
    eligible_priced_decisions: int
    odds_coverage_count: int
    odds_coverage_percentage: float | None
    excluded_count: int


@dataclass(frozen=True, slots=True)
class WindowedRecords:
    window: SlateWindow
    records: tuple[ModelEvaluationRecord, ...]
    earliest_date: date | None
    latest_date: date | None
    unique_slates: int


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    outcome_counts: tuple[tuple[Outcome, int], ...]
    hit_rate: HitRateMetrics
    flat_unit_roi: FlatUnitRoiMetrics
    confidence_buckets: tuple[BucketMetrics, ...]
    edge_buckets: tuple[BucketMetrics, ...]


def confidence_bucket(value: float | None) -> str:
    """Classify the legacy score at exact 0.55/0.65/0.75/0.85 cutoffs."""

    if value is None or not math.isfinite(value):
        return "Unknown"
    if value < 0.55:
        return "<0.55"
    if value < 0.65:
        return "0.55-<0.65"
    if value < 0.75:
        return "0.65-<0.75"
    if value < 0.85:
        return "0.75-<0.85"
    return ">=0.85"


def edge_bucket(value: float | None) -> str:
    """Classify absolute legacy edge at 1/2/3/5 raw stat-unit cutoffs."""

    if value is None or not math.isfinite(value):
        return "Unknown"
    magnitude = abs(value)
    if magnitude < 1.0:
        return "<1"
    if magnitude < 2.0:
        return "1-<2"
    if magnitude < 3.0:
        return "2-<3"
    if magnitude < 5.0:
        return "3-<5"
    return ">=5"


def count_outcomes(
    records: Iterable[ModelEvaluationRecord],
) -> tuple[tuple[Outcome, int], ...]:
    materialized = tuple(records)
    return tuple(
        (outcome, sum(record.outcome is outcome for record in materialized))
        for outcome in Outcome
    )


def calculate_hit_rate(
    records: Iterable[ModelEvaluationRecord],
) -> HitRateMetrics:
    materialized = tuple(records)
    wins = sum(record.outcome is Outcome.WIN for record in materialized)
    losses = sum(record.outcome is Outcome.LOSS for record in materialized)
    decisive_sample = wins + losses
    return HitRateMetrics(
        wins=wins,
        losses=losses,
        decisive_sample=decisive_sample,
        hit_rate=(wins / decisive_sample) if decisive_sample else None,
    )


def calculate_flat_unit_roi(
    records: Iterable[ModelEvaluationRecord],
) -> FlatUnitRoiMetrics:
    """Calculate descriptive one-unit entry-price ROI.

    Odds coverage uses WIN/LOSS/PUSH as the priceable decision population.
    PUSH is an eligible priced decision with zero return. All other outcomes,
    and any record without valid odds, are excluded from the ROI denominator.
    """

    materialized = tuple(records)
    priceable_outcomes = {Outcome.WIN, Outcome.LOSS, Outcome.PUSH}
    coverage_population = sum(
        record.outcome in priceable_outcomes for record in materialized
    )
    eligible = tuple(record for record in materialized if record.roi_eligible)
    net_units = 0.0
    for record in eligible:
        if record.outcome is Outcome.WIN:
            assert record.odds_decimal is not None
            net_units += record.odds_decimal - 1.0
        elif record.outcome is Outcome.LOSS:
            net_units -= 1.0

    eligible_count = len(eligible)
    return FlatUnitRoiMetrics(
        roi=(net_units / eligible_count) if eligible_count else None,
        net_flat_units=net_units,
        eligible_priced_decisions=eligible_count,
        odds_coverage_count=eligible_count,
        odds_coverage_percentage=(
            eligible_count / coverage_population if coverage_population else None
        ),
        excluded_count=len(materialized) - eligible_count,
    )


def _bucket_metrics(
    records: tuple[ModelEvaluationRecord, ...],
    *,
    classifier: Callable[[float | None], str],
    value_getter: Callable[[ModelEvaluationRecord], float | None],
    labels: tuple[str, ...],
) -> tuple[BucketMetrics, ...]:
    result: list[BucketMetrics] = []
    for label in labels:
        bucket_records = tuple(
            record
            for record in records
            if classifier(value_getter(record)) == label
        )
        hit_rate = calculate_hit_rate(bucket_records)
        roi = calculate_flat_unit_roi(bucket_records)
        result.append(
            BucketMetrics(
                bucket=label,
                sample_size=len(bucket_records),
                wins=hit_rate.wins,
                losses=hit_rate.losses,
                decisive_sample=hit_rate.decisive_sample,
                hit_rate=hit_rate.hit_rate,
                roi=roi.roi,
                net_flat_units=roi.net_flat_units,
                eligible_priced_decisions=roi.eligible_priced_decisions,
                odds_coverage_count=roi.odds_coverage_count,
                odds_coverage_percentage=roi.odds_coverage_percentage,
                excluded_count=roi.excluded_count,
            )
        )
    return tuple(result)


def calculate_confidence_buckets(
    records: Iterable[ModelEvaluationRecord],
) -> tuple[BucketMetrics, ...]:
    materialized = tuple(records)
    return _bucket_metrics(
        materialized,
        classifier=confidence_bucket,
        value_getter=lambda record: record.confidence,
        labels=CONFIDENCE_SCORE_BUCKETS,
    )


def calculate_edge_buckets(
    records: Iterable[ModelEvaluationRecord],
) -> tuple[BucketMetrics, ...]:
    materialized = tuple(records)
    return _bucket_metrics(
        materialized,
        classifier=edge_bucket,
        value_getter=lambda record: record.edge_value,
        labels=EDGE_STAT_UNIT_BUCKETS,
    )


def calculate_evaluation_metrics(
    records: Iterable[ModelEvaluationRecord],
) -> EvaluationMetrics:
    materialized = tuple(records)
    return EvaluationMetrics(
        outcome_counts=count_outcomes(materialized),
        hit_rate=calculate_hit_rate(materialized),
        flat_unit_roi=calculate_flat_unit_roi(materialized),
        confidence_buckets=calculate_confidence_buckets(materialized),
        edge_buckets=calculate_edge_buckets(materialized),
    )


def select_recent_slates(
    records: Iterable[ModelEvaluationRecord], window: SlateWindow
) -> WindowedRecords:
    """Select the last N unique prediction slates, never calendar days."""

    materialized = tuple(records)
    all_dates = sorted({record.prediction_date for record in materialized})
    if window is SlateWindow.LAST_7:
        included_dates = set(all_dates[-7:])
    elif window is SlateWindow.LAST_30:
        included_dates = set(all_dates[-30:])
    elif window is SlateWindow.ALL:
        included_dates = set(all_dates)
    else:  # pragma: no cover - Enum typing protects normal callers
        raise ValueError(f"Unsupported slate window: {window!r}")

    selected = tuple(
        record for record in materialized if record.prediction_date in included_dates
    )
    return WindowedRecords(
        window=window,
        records=selected,
        earliest_date=min(included_dates) if included_dates else None,
        latest_date=max(included_dates) if included_dates else None,
        unique_slates=len(included_dates),
    )


def source_is_stale(
    latest_date: date | None,
    *,
    as_of_date: date,
    threshold_days: int = STALE_AFTER_DAYS,
) -> bool:
    """Return true when coverage is more than ``threshold_days`` behind."""

    if latest_date is None:
        return False
    return (as_of_date - latest_date).days > threshold_days


__all__ = [
    "BucketMetrics",
    "CONFIDENCE_SCORE_BUCKETS",
    "EDGE_STAT_UNIT_BUCKETS",
    "EvaluationMetrics",
    "FlatUnitRoiMetrics",
    "HitRateMetrics",
    "STALE_AFTER_DAYS",
    "SlateWindow",
    "WindowedRecords",
    "calculate_confidence_buckets",
    "calculate_edge_buckets",
    "calculate_evaluation_metrics",
    "calculate_flat_unit_roi",
    "calculate_hit_rate",
    "confidence_bucket",
    "count_outcomes",
    "edge_bucket",
    "select_recent_slates",
    "source_is_stale",
]
