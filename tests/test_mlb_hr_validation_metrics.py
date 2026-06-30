from __future__ import annotations

import pytest

from courtvision.sports.mlb.training.hr_validation_metrics import (
    DEFAULT_BOOTSTRAP_REPLICATES,
    DEFAULT_BOOTSTRAP_SEED,
    brier_score,
    calibration_error,
    log_loss,
    paired_game_date_bootstrap,
    pr_auc,
    roc_auc,
)


def test_binary_metrics_match_known_reference_values() -> None:
    labels = (0, 0, 1, 1)
    probabilities = (0.1, 0.4, 0.35, 0.8)

    assert log_loss(labels, probabilities) == pytest.approx(0.47228795380917615)
    assert brier_score(labels, probabilities) == pytest.approx(0.158125)
    assert roc_auc(labels, probabilities) == pytest.approx(0.75)
    assert pr_auc(labels, probabilities) == pytest.approx(5.0 / 6.0)
    assert calibration_error(labels, probabilities) == pytest.approx(0.3375)


def test_paired_game_date_bootstrap_is_deterministic_reference_output() -> None:
    labels = (0, 1, 0, 1, 0, 1, 0, 1)
    game_dates = (
        "2024-04-01",
        "2024-04-01",
        "2024-04-02",
        "2024-04-02",
        "2024-04-03",
        "2024-04-03",
        "2024-04-04",
        "2024-04-04",
    )
    probability_series = {"model": (0.1, 0.8, 0.2, 0.7, 0.3, 0.6, 0.4, 0.9)}

    first = paired_game_date_bootstrap(
        labels=labels,
        game_dates=game_dates,
        probability_series=probability_series,
    )
    second = paired_game_date_bootstrap(
        labels=labels,
        game_dates=game_dates,
        probability_series=probability_series,
    )

    assert first == second
    assert first.seed == DEFAULT_BOOTSTRAP_SEED == 20260629
    assert first.requested_replicates == DEFAULT_BOOTSTRAP_REPLICATES == 2_000
    assert first.unique_game_date_count == 4
    expected_bounds = {
        "log_loss": (0.20021229254249065, 0.397790024795889),
        "brier_score": (0.039999999999999994, 0.11000000000000001),
        "roc_auc": (1.0, 1.0),
        "pr_auc": (1.0, 1.0),
        "calibration_error": (0.17499999999999996, 0.325),
    }
    assert {
        interval.metric_name: (
            interval.lower_bound,
            interval.upper_bound,
        )
        for interval in first.intervals
    } == expected_bounds
    assert all(
        interval.successful_replicates == 2_000
        and interval.status == "estimated"
        for interval in first.intervals
    )
