from __future__ import annotations

from courtvision.core.hit_rate_engine import HitRateEngine, HitRateObservation, calculate_hit_rates


def test_hit_rate_windows_venue_opponent_and_line_specific_rates() -> None:
    observations = [
        HitRateObservation(value=float(value), is_home=value % 2 == 0, opponent="BOS" if value in {2, 8} else "NYK")
        for value in range(1, 11)
    ]

    rates = calculate_hit_rates(
        observations,
        line=5.5,
        direction="over",
        upcoming_is_home=True,
        opponent="BOS",
    )

    assert rates.last_5_hit_rate == 100.0
    assert rates.last_10_hit_rate == 50.0
    assert rates.last_20_hit_rate == 50.0
    assert rates.season_hit_rate == 50.0
    assert rates.home_hit_rate == 60.0
    assert rates.away_hit_rate == 40.0
    assert rates.home_away_hit_rate == 60.0
    assert rates.opponent_adjusted_hit_rate == 50.0
    assert rates.line_specific_hit_rate == 50.0
    assert HitRateEngine.calculate(observations, line=5.5).season_hit_rate == 50.0


def test_pushes_are_excluded_from_hit_rate_attempts() -> None:
    rates = calculate_hit_rates(
        [
            HitRateObservation(10.0, True, "A"),
            HitRateObservation(11.0, False, "B"),
            HitRateObservation(12.0, True, "C"),
        ],
        line=11.0,
    )

    assert rates.season_hit_rate == 50.0
