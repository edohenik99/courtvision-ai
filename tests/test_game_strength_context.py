"""Tests for courtvision/context/game_strength.py — CourtVision Power Rating matchup context."""
from __future__ import annotations

import pandas as pd
import pytest

from courtvision.context.game_strength import (
    DEFAULT_HOME_COURT_ADVANTAGE,
    POWER_RATING_CONTEXT_COLUMNS,
    apply_power_rating_context_to_df,
    get_matchup_context,
    get_matchup_context_batch,
)
from courtvision.ratings.power_rating import DEFAULT_RATING

_RATINGS = {
    "LAL": 1600.0,
    "GSW": 1500.0,
    "BOS": 1700.0,
    "MIA": 1400.0,
}


class TestMatchupContext:

    def test_returns_all_required_columns(self):
        ctx = get_matchup_context("LAL", "GSW", _RATINGS)
        for col in POWER_RATING_CONTEXT_COLUMNS:
            assert col in ctx, f"missing column: {col}"

    def test_context_applied_flag_true_when_ratings_present(self):
        assert get_matchup_context("LAL", "GSW", _RATINGS)["team_power_context_applied"] is True

    def test_missing_home_team_does_not_crash(self):
        ctx = get_matchup_context("UNKNOWN_TEAM", "GSW", _RATINGS)
        assert ctx["team_power_context_applied"] is False
        assert "team_power_context_missing" in ctx["team_power_context_missing_reason"]

    def test_missing_away_team_does_not_crash(self):
        ctx = get_matchup_context("LAL", "UNKNOWN_TEAM", _RATINGS)
        assert ctx["team_power_context_applied"] is False

    def test_empty_ratings_does_not_crash(self):
        ctx = get_matchup_context("LAL", "GSW", {})
        assert ctx["team_power_context_applied"] is False

    def test_win_probabilities_sum_to_one(self):
        ctx = get_matchup_context("LAL", "GSW", _RATINGS)
        assert abs(ctx["home_win_probability"] + ctx["away_win_probability"] - 1.0) < 1e-6

    def test_higher_rated_home_team_has_higher_win_probability(self):
        ctx = get_matchup_context("BOS", "MIA", _RATINGS)
        assert ctx["home_win_probability"] > 0.5

    def test_home_court_advantage_increases_home_win_probability(self):
        ctx_no = get_matchup_context("GSW", "LAL", _RATINGS, home_court_advantage=0.0)
        ctx_hca = get_matchup_context("GSW", "LAL", _RATINGS, home_court_advantage=50.0)
        assert ctx_hca["home_win_probability"] > ctx_no["home_win_probability"]

    def test_home_adjusted_diff_includes_home_court_advantage(self):
        ctx = get_matchup_context("LAL", "GSW", _RATINGS, home_court_advantage=50.0)
        expected_raw = _RATINGS["LAL"] - _RATINGS["GSW"]
        assert abs(ctx["power_rating_diff"] - expected_raw) < 1e-6
        assert abs(ctx["home_adjusted_power_rating_diff"] - (expected_raw + 50.0)) < 1e-6

    def test_large_rating_gap_produces_high_blowout_risk(self):
        ctx = get_matchup_context("BOS", "MIA", _RATINGS)
        assert ctx["blowout_risk"] == "HIGH"

    def test_close_matchup_produces_low_blowout_risk(self):
        ctx = get_matchup_context("T1", "T2", {"T1": 1505.0, "T2": 1495.0},
                                  home_court_advantage=0.0)
        assert ctx["blowout_risk"] == "LOW"

    def test_close_matchup_has_high_competitiveness(self):
        ctx = get_matchup_context("T1", "T2", {"T1": 1505.0, "T2": 1495.0},
                                  home_court_advantage=0.0)
        assert ctx["expected_competitiveness"] == "HIGH"

    def test_large_gap_has_low_competitiveness(self):
        ctx = get_matchup_context("BOS", "MIA", _RATINGS)
        assert ctx["expected_competitiveness"] == "LOW"

    def test_column_names_use_courtvision_power_rating_naming(self):
        ctx = get_matchup_context("LAL", "GSW", _RATINGS)
        assert "home_team_power_rating" in ctx
        assert "away_team_power_rating" in ctx
        assert "team_power_context_applied" in ctx
        assert "team_power_context_missing_reason" in ctx
        assert "blowout_risk" in ctx
        assert "expected_competitiveness" in ctx

    def test_missing_reason_empty_when_context_applied(self):
        ctx = get_matchup_context("LAL", "GSW", _RATINGS)
        assert ctx["team_power_context_missing_reason"] == ""

    def test_missing_reason_descriptive_when_not_applied(self):
        ctx = get_matchup_context("MISSING", "ALSO_MISSING", _RATINGS)
        reason = ctx["team_power_context_missing_reason"]
        assert "team_power_context_missing" in reason
        assert len(reason) > 10

    def test_power_rating_diff_is_home_minus_away(self):
        ctx = get_matchup_context("LAL", "GSW", _RATINGS)
        assert abs(ctx["power_rating_diff"] - (_RATINGS["LAL"] - _RATINGS["GSW"])) < 1e-6

    def test_safe_default_fields_are_valid_when_missing(self):
        ctx = get_matchup_context("GHOST", "GSW", _RATINGS)
        assert ctx["home_win_probability"] == 0.5
        assert ctx["away_win_probability"] == 0.5
        assert ctx["home_team_power_rating"] == DEFAULT_RATING
        assert ctx["away_team_power_rating"] == DEFAULT_RATING


class TestMatchupContextBatch:

    def test_returns_all_matchups(self):
        matchups = [
            {"home_team_id": "LAL", "away_team_id": "GSW"},
            {"home_team_id": "BOS", "away_team_id": "MIA"},
        ]
        results = get_matchup_context_batch(matchups, _RATINGS)
        assert len(results) == 2

    def test_preserves_original_keys(self):
        matchups = [{"home_team_id": "LAL", "away_team_id": "GSW", "game_id": "G001"}]
        results = get_matchup_context_batch(matchups, _RATINGS)
        assert results[0]["game_id"] == "G001"

    def test_missing_team_id_does_not_crash(self):
        matchups = [
            {"home_team_id": "", "away_team_id": "GSW"},
            {"home_team_id": "LAL", "away_team_id": "GSW"},
        ]
        results = get_matchup_context_batch(matchups, _RATINGS)
        assert len(results) == 2
        assert results[0]["team_power_context_applied"] is False
        assert results[1]["team_power_context_applied"] is True

    def test_empty_matchups_returns_empty(self):
        assert get_matchup_context_batch([], _RATINGS) == []

    def test_all_context_columns_present_in_each_result(self):
        matchups = [{"home_team_id": "LAL", "away_team_id": "GSW"}]
        result = get_matchup_context_batch(matchups, _RATINGS)[0]
        for col in POWER_RATING_CONTEXT_COLUMNS:
            assert col in result, f"missing column: {col}"


class TestApplyPowerRatingContextToDf:

    def _board_df(self):
        return pd.DataFrame([
            {"team_abbr": "LAL", "opponent": "GSW", "home_away": "home", "player_name": "P1"},
            {"team_abbr": "GSW", "opponent": "LAL", "home_away": "away", "player_name": "P2"},
            {"team_abbr": "BOS", "opponent": "MIA", "home_away": "home", "player_name": "P3"},
            {"team_abbr": "MIA", "opponent": "BOS", "home_away": "away", "player_name": "P4"},
        ])

    def test_all_context_columns_added(self):
        df = apply_power_rating_context_to_df(self._board_df(), ratings={})
        for col in POWER_RATING_CONTEXT_COLUMNS:
            assert col in df.columns, f"missing column: {col}"

    def test_empty_ratings_produces_safe_defaults(self):
        df = apply_power_rating_context_to_df(self._board_df(), ratings={})
        assert not df["team_power_context_applied"].any()

    def test_rated_teams_produce_applied_true(self):
        df = apply_power_rating_context_to_df(self._board_df(), ratings=_RATINGS)
        assert df["team_power_context_applied"].all()

    def test_none_ratings_treated_as_empty(self):
        df = apply_power_rating_context_to_df(self._board_df(), ratings=None)
        assert not df["team_power_context_applied"].any()

    def test_home_row_and_away_row_share_same_win_probability(self):
        df = apply_power_rating_context_to_df(self._board_df(), ratings=_RATINGS)
        lal_row = df[df["team_abbr"] == "LAL"].iloc[0]
        gsw_row = df[df["team_abbr"] == "GSW"].iloc[0]
        assert abs(lal_row["home_win_probability"] - gsw_row["home_win_probability"]) < 1e-9

    def test_missing_required_columns_returns_safe_defaults(self):
        df = pd.DataFrame([{"team_abbr": "LAL", "player_name": "P1"}])
        result = apply_power_rating_context_to_df(df, ratings=_RATINGS)
        for col in POWER_RATING_CONTEXT_COLUMNS:
            assert col in result.columns, f"missing column: {col}"
        assert not result["team_power_context_applied"].any()

    def test_empty_dataframe_returns_empty_with_context_columns(self):
        df = apply_power_rating_context_to_df(pd.DataFrame(), ratings=_RATINGS)
        assert isinstance(df, pd.DataFrame)

    def test_missing_team_abbr_produces_safe_default_row(self):
        df = pd.DataFrame([
            {"team_abbr": "", "opponent": "GSW", "home_away": "home", "player_name": "P1"},
            {"team_abbr": "BOS", "opponent": "MIA", "home_away": "home", "player_name": "P2"},
        ])
        result = apply_power_rating_context_to_df(df, ratings=_RATINGS)
        empty_row = result[result["player_name"] == "P1"]
        assert not empty_row["team_power_context_applied"].iloc[0]

    def test_unknown_home_away_value_produces_safe_default(self):
        df = pd.DataFrame([
            {"team_abbr": "LAL", "opponent": "GSW", "home_away": "neutral", "player_name": "P1"},
        ])
        result = apply_power_rating_context_to_df(df, ratings=_RATINGS)
        assert not result["team_power_context_applied"].iloc[0]

    def test_original_columns_preserved(self):
        df = apply_power_rating_context_to_df(self._board_df(), ratings={})
        assert "player_name" in df.columns
        assert list(df["player_name"]) == ["P1", "P2", "P3", "P4"]

    def test_win_probabilities_sum_to_one_per_game(self):
        df = apply_power_rating_context_to_df(self._board_df(), ratings=_RATINGS)
        for _, row in df.iterrows():
            assert abs(row["home_win_probability"] + row["away_win_probability"] - 1.0) < 1e-6
