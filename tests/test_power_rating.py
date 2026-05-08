"""Tests for courtvision/ratings/power_rating.py — CourtVision Power Rating."""
from __future__ import annotations

import pandas as pd
import pytest

from courtvision.ratings.power_rating import (
    DEFAULT_RATING,
    build_team_power_rating_history,
    expected_score,
    regress_to_mean,
    update_power_rating,
)


class TestExpectedScore:

    def test_equal_ratings_return_half(self):
        assert abs(expected_score(1500, 1500) - 0.5) < 1e-9

    def test_higher_rating_returns_greater_than_half(self):
        assert expected_score(1600, 1500) > 0.5

    def test_lower_rating_returns_less_than_half(self):
        assert expected_score(1400, 1500) < 0.5

    def test_output_is_valid_probability(self):
        for diff in [-400, -200, -100, 0, 100, 200, 400]:
            p = expected_score(1500 + diff, 1500)
            assert 0.0 < p < 1.0

    def test_symmetry(self):
        p_a = expected_score(1600, 1400)
        p_b = expected_score(1400, 1600)
        assert abs(p_a + p_b - 1.0) < 1e-9

    def test_large_advantage_approaches_one(self):
        assert expected_score(2000, 1000) > 0.99


class TestUpdatePowerRating:

    def test_win_increases_rating(self):
        assert update_power_rating(1500, 1500, 110, 100) > 1500

    def test_loss_decreases_rating(self):
        assert update_power_rating(1500, 1500, 90, 110) < 1500

    def test_expected_win_increases_rating(self):
        assert update_power_rating(1600, 1400, 110, 100) > 1600

    def test_unexpected_win_increases_rating_more_than_expected_win(self):
        favorite_gain = update_power_rating(1600, 1400, 110, 100) - 1600
        underdog_gain = update_power_rating(1400, 1600, 110, 100) - 1400
        assert underdog_gain > favorite_gain

    def test_margin_scaling_increases_movement_for_large_wins(self):
        narrow = update_power_rating(1500, 1500, 101, 100, margin_scaling=True)
        blowout = update_power_rating(1500, 1500, 140, 100, margin_scaling=True)
        assert blowout > narrow

    def test_no_margin_scaling_ignores_margin(self):
        narrow = update_power_rating(1500, 1500, 101, 100, margin_scaling=False)
        blowout = update_power_rating(1500, 1500, 140, 100, margin_scaling=False)
        assert narrow == blowout

    def test_tie_produces_no_rating_change(self):
        assert update_power_rating(1500, 1500, 100, 100) == 1500.0

    def test_favorite_loss_decreases_rating_more_than_upset_loss(self):
        favorite_drop = 1600 - update_power_rating(1600, 1400, 90, 100)
        underdog_drop = 1400 - update_power_rating(1400, 1600, 90, 100)
        assert favorite_drop > underdog_drop

    def test_k_factor_scales_movement(self):
        low_k = abs(update_power_rating(1500, 1500, 110, 100, k=10) - 1500)
        high_k = abs(update_power_rating(1500, 1500, 110, 100, k=40) - 1500)
        assert high_k > low_k


class TestRegressToMean:

    def test_moves_above_average_down(self):
        new = regress_to_mean(1600, mean=1500, regression_factor=0.25)
        assert 1500 < new < 1600

    def test_moves_under_average_up(self):
        new = regress_to_mean(1400, mean=1500, regression_factor=0.25)
        assert 1400 < new < 1500

    def test_at_mean_unchanged(self):
        assert regress_to_mean(1500, mean=1500, regression_factor=0.25) == 1500.0

    def test_quarter_regression_closes_25_percent_of_gap(self):
        new = regress_to_mean(1600, mean=1500, regression_factor=0.25)
        assert abs(new - 1575.0) < 1e-6

    def test_full_regression_returns_mean(self):
        assert abs(regress_to_mean(1700, mean=1500, regression_factor=1.0) - 1500.0) < 1e-6


class TestBuildTeamPowerRatingHistory:

    def _games_df(self):
        return pd.DataFrame([
            {
                "date": "2026-01-01",
                "home_team_id": "LAL", "away_team_id": "GSW",
                "home_score": 115, "away_score": 108,
                "game_id": "G001",
                "home_team_name": "Lakers", "away_team_name": "Warriors",
            },
            {
                "date": "2026-01-03",
                "home_team_id": "BOS", "away_team_id": "LAL",
                "home_score": 110, "away_score": 120,
                "game_id": "G002",
                "home_team_name": "Celtics", "away_team_name": "Lakers",
            },
        ])

    def test_returns_dataframe(self):
        assert isinstance(build_team_power_rating_history(self._games_df()), pd.DataFrame)

    def test_output_has_required_columns(self):
        result = build_team_power_rating_history(self._games_df())
        for col in ("date", "team_id", "power_rating", "games_played", "power_rating_change"):
            assert col in result.columns, f"missing column: {col}"

    def test_two_games_produce_four_rows(self):
        assert len(build_team_power_rating_history(self._games_df())) == 4

    def test_empty_dataframe_returns_empty(self):
        result = build_team_power_rating_history(pd.DataFrame())
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_missing_required_columns_returns_empty(self):
        result = build_team_power_rating_history(pd.DataFrame([{"date": "2026-01-01", "team": "LAL"}]))
        assert len(result) == 0

    def test_winner_rating_increases(self):
        result = build_team_power_rating_history(self._games_df())
        row = result[(result["team_id"] == "LAL") & (result["games_played"] == 1)]
        assert row["power_rating_change"].iloc[0] > 0

    def test_loser_rating_decreases(self):
        result = build_team_power_rating_history(self._games_df())
        row = result[(result["team_id"] == "GSW") & (result["games_played"] == 1)]
        assert row["power_rating_change"].iloc[0] < 0

    def test_team_names_propagated(self):
        result = build_team_power_rating_history(self._games_df())
        lal = result[result["team_id"] == "LAL"]
        assert (lal["team_name"] == "Lakers").any()

    def test_invalid_score_rows_skipped_safely(self):
        df = pd.DataFrame([
            {"date": "2026-01-01", "home_team_id": "LAL", "away_team_id": "GSW",
             "home_score": "N/A", "away_score": 108},
            {"date": "2026-01-02", "home_team_id": "BOS", "away_team_id": "MIA",
             "home_score": 115, "away_score": 110},
        ])
        result = build_team_power_rating_history(df)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2

    def test_missing_team_ids_skipped_safely(self):
        df = pd.DataFrame([
            {"date": "2026-01-01", "home_team_id": "", "away_team_id": "GSW",
             "home_score": 115, "away_score": 108},
            {"date": "2026-01-02", "home_team_id": "BOS", "away_team_id": "MIA",
             "home_score": 115, "away_score": 110},
        ])
        result = build_team_power_rating_history(df)
        assert len(result) == 2

    def test_new_team_initialized_at_default_rating(self):
        result = build_team_power_rating_history(self._games_df())
        bos_g1 = result[(result["team_id"] == "BOS") & (result["games_played"] == 1)]
        prior_bos = DEFAULT_RATING
        assert abs(bos_g1["power_rating"].iloc[0] - prior_bos) > 0
