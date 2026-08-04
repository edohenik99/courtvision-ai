from __future__ import annotations

from datetime import date, timedelta
from itertools import count

import pytest

from courtvision.evaluation.model_metrics import (
    SlateWindow,
    calculate_evaluation_metrics,
    calculate_flat_unit_roi,
    calculate_hit_rate,
    confidence_bucket,
    edge_bucket,
    select_recent_slates,
    source_is_stale,
)
from courtvision.evaluation.model_records import (
    EVALUATION_POPULATION,
    FEEDBACK_EVALUATION_POPULATION,
    Outcome,
    create_model_evaluation_record,
)


_IDS = count()


def _record(
    *,
    outcome: str = "hit",
    odds: object = -110,
    confidence: float | None = 0.75,
    edge: float | None = 2.0,
    prediction_date: date = date(2026, 5, 1),
    evaluation_population: str = EVALUATION_POPULATION,
):
    index = next(_IDS)
    is_feedback = evaluation_population == FEEDBACK_EVALUATION_POPULATION
    return create_model_evaluation_record(
        source_name="result_feedback.csv" if is_feedback else "pick_history.csv",
        source_path=(
            "C:/research/result_feedback.csv"
            if is_feedback
            else "C:/research/pick_history.csv"
        ),
        source_row_number=index + 2,
        prediction_date=prediction_date,
        participant_id=f"p-{index}",
        participant_name=f"Player {index}",
        team="TOR",
        opponent="BOS",
        event_id=f"g-{index}",
        market="player_points",
        selection="over",
        line=20.5,
        prediction_value=22.5,
        raw_odds=odds,
        confidence=confidence,
        edge_value=edge,
        raw_outcome=outcome,
        actual_value=24.0,
        evaluation_population=evaluation_population,
        source_identity=f"feedback-key-{index}" if is_feedback else None,
    )


def test_hit_rate_uses_only_win_loss_denominator() -> None:
    records = tuple(
        _record(outcome=outcome)
        for outcome in ("hit", "hit", "miss", "push", "void", "pending", "other")
    )

    result = calculate_hit_rate(records)

    assert result.wins == 2
    assert result.losses == 1
    assert result.decisive_sample == 3
    assert result.hit_rate == pytest.approx(2 / 3)


def test_flat_unit_roi_handles_both_odds_signs_losses_and_pushes() -> None:
    records = (
        _record(outcome="hit", odds=+150),
        _record(outcome="hit", odds=-200),
        _record(outcome="miss", odds=-110),
        _record(outcome="push", odds=+120),
    )

    result = calculate_flat_unit_roi(records)

    assert result.net_flat_units == pytest.approx(1.0)
    assert result.eligible_priced_decisions == 4
    assert result.roi == pytest.approx(0.25)
    assert result.odds_coverage_percentage == pytest.approx(1.0)
    assert result.excluded_count == 0


def test_missing_odds_are_excluded_and_coverage_is_explicit() -> None:
    records = (
        _record(outcome="hit", odds=-110),
        _record(outcome="miss", odds=""),
        _record(outcome="void", odds=+120),
    )

    result = calculate_flat_unit_roi(records)

    assert result.eligible_priced_decisions == 1
    assert result.odds_coverage_count == 1
    assert result.odds_coverage_percentage == pytest.approx(0.5)
    assert result.excluded_count == 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "Unknown"),
        (0.0, "<0.55"),
        (0.549999, "<0.55"),
        (0.55, "0.55-<0.65"),
        (0.649999, "0.55-<0.65"),
        (0.65, "0.65-<0.75"),
        (0.749999, "0.65-<0.75"),
        (0.75, "0.75-<0.85"),
        (0.849999, "0.75-<0.85"),
        (0.85, ">=0.85"),
        (1.0, ">=0.85"),
    ],
)
def test_confidence_bucket_boundaries(value: float | None, expected: str) -> None:
    assert confidence_bucket(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "Unknown"),
        (0.0, "<1"),
        (0.999999, "<1"),
        (-0.999999, "<1"),
        (1.0, "1-<2"),
        (-1.999999, "1-<2"),
        (2.0, "2-<3"),
        (-2.999999, "2-<3"),
        (3.0, "3-<5"),
        (-4.999999, "3-<5"),
        (5.0, ">=5"),
        (-5.0, ">=5"),
    ],
)
def test_edge_bucket_boundaries_are_absolute_stat_units(
    value: float | None, expected: str
) -> None:
    assert edge_bucket(value) == expected


def test_recent_windows_count_unique_prediction_slates_not_calendar_days() -> None:
    start = date(2026, 1, 1)
    dates = [start + timedelta(days=index * 2) for index in range(40)]
    records = tuple(_record(prediction_date=slate) for slate in dates)

    last_7 = select_recent_slates(records, SlateWindow.LAST_7)
    last_30 = select_recent_slates(records, SlateWindow.LAST_30)
    all_slates = select_recent_slates(records, SlateWindow.ALL)

    assert last_7.unique_slates == 7
    assert last_7.earliest_date == dates[-7]
    assert last_7.latest_date == dates[-1]
    assert last_30.unique_slates == 30
    assert last_30.earliest_date == dates[-30]
    assert all_slates.unique_slates == 40
    assert all_slates.earliest_date == dates[0]


def test_empty_eligible_populations_return_none_rates_without_error() -> None:
    no_decisions = (_record(outcome="void", odds=""), _record(outcome="pending", odds=""))

    metrics = calculate_evaluation_metrics(no_decisions)

    assert metrics.hit_rate.decisive_sample == 0
    assert metrics.hit_rate.hit_rate is None
    assert metrics.flat_unit_roi.eligible_priced_decisions == 0
    assert metrics.flat_unit_roi.roi is None
    assert metrics.flat_unit_roi.odds_coverage_percentage is None


def test_staleness_uses_injected_as_of_date_and_strict_30_day_threshold() -> None:
    latest = date(2026, 5, 13)
    assert source_is_stale(latest, as_of_date=date(2026, 6, 12)) is False
    assert source_is_stale(latest, as_of_date=date(2026, 6, 13)) is True


def test_feedback_population_reuses_hit_rate_roi_and_outcome_semantics() -> None:
    feedback = FEEDBACK_EVALUATION_POPULATION
    records = (
        _record(outcome="win", odds=+150, evaluation_population=feedback),
        _record(outcome="loss", odds=-110, evaluation_population=feedback),
        _record(outcome="push", odds=+120, evaluation_population=feedback),
        _record(outcome="unresolved", odds=-110, evaluation_population=feedback),
        _record(outcome="ungraded", odds=-110, evaluation_population=feedback),
        _record(outcome="win", odds="", evaluation_population=feedback),
    )

    metrics = calculate_evaluation_metrics(records)
    counts = dict(metrics.outcome_counts)

    assert counts[Outcome.WIN] == 2
    assert counts[Outcome.LOSS] == 1
    assert counts[Outcome.PUSH] == 1
    assert counts[Outcome.PENDING] == 1
    assert counts[Outcome.UNSUPPORTED] == 1
    assert metrics.hit_rate.wins == 2
    assert metrics.hit_rate.losses == 1
    assert metrics.hit_rate.hit_rate == pytest.approx(2 / 3)
    assert metrics.flat_unit_roi.eligible_priced_decisions == 3
    assert metrics.flat_unit_roi.net_flat_units == pytest.approx(0.5)
    assert metrics.flat_unit_roi.roi == pytest.approx(1 / 6)
    assert metrics.flat_unit_roi.odds_coverage_percentage == pytest.approx(0.75)
    assert metrics.flat_unit_roi.excluded_count == 3
