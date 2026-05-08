"""Integration tests — CourtVision Power Rating in runtime diagnostics.

Verifies that the power rating context layer is purely additive:
- existing board columns unchanged
- missing ratings never crash
- pick selection, confidence, edge, quality_score, Kelly staking unaffected
- quality summary can report power rating diagnostics
- real ratings built from game results file produce non-zero context_applied_count
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from courtvision.context.game_strength import (
    POWER_RATING_CONTEXT_COLUMNS,
    apply_power_rating_context_to_df,
)
from courtvision.ratings.power_ratings_store import (
    build_current_power_ratings,
    get_latest_team_power_ratings,
    load_game_results,
)
from courtvision.reporting.quality_summary import _power_rating_context_summary

_FIXTURE_CSV = Path(__file__).parent / "fixtures" / "game_results_sample.csv"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CORE_BOARD_COLUMNS = [
    "player_name", "team_abbr", "opponent", "home_away",
    "market", "line", "edge", "confidence", "quality_score",
    "kelly_fraction", "stake_amount",
]

_RATINGS = {"LAL": 1620.0, "GSW": 1510.0, "BOS": 1700.0, "MIA": 1390.0}


def _make_board_df() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "player_name": "Player A", "team_abbr": "LAL", "opponent": "GSW",
            "home_away": "home", "market": "PTS", "line": 24.5,
            "edge": 0.08, "confidence": 0.72, "quality_score": 0.81,
            "kelly_fraction": 0.04, "stake_amount": 40.0,
        },
        {
            "player_name": "Player B", "team_abbr": "GSW", "opponent": "LAL",
            "home_away": "away", "market": "AST", "line": 6.5,
            "edge": 0.05, "confidence": 0.65, "quality_score": 0.70,
            "kelly_fraction": 0.025, "stake_amount": 25.0,
        },
        {
            "player_name": "Player C", "team_abbr": "BOS", "opponent": "MIA",
            "home_away": "home", "market": "REB", "line": 9.5,
            "edge": 0.06, "confidence": 0.68, "quality_score": 0.75,
            "kelly_fraction": 0.03, "stake_amount": 30.0,
        },
    ])


# ---------------------------------------------------------------------------
# Additive-only guarantees
# ---------------------------------------------------------------------------

class TestPowerRatingIsAdditiveOnly:

    def test_core_columns_unchanged_after_enrichment(self):
        df = _make_board_df()
        original_values = {col: list(df[col]) for col in _CORE_BOARD_COLUMNS}
        apply_power_rating_context_to_df(df, ratings=_RATINGS)
        for col, vals in original_values.items():
            assert list(df[col]) == vals, f"column {col!r} was modified"

    def test_row_count_unchanged_after_enrichment(self):
        df = _make_board_df()
        n = len(df)
        apply_power_rating_context_to_df(df, ratings=_RATINGS)
        assert len(df) == n

    def test_power_rating_columns_added_without_removing_existing(self):
        df = _make_board_df()
        apply_power_rating_context_to_df(df, ratings=_RATINGS)
        for col in _CORE_BOARD_COLUMNS:
            assert col in df.columns
        for col in POWER_RATING_CONTEXT_COLUMNS:
            assert col in df.columns

    def test_edge_and_quality_score_unmodified(self):
        df = _make_board_df()
        edges_before = list(df["edge"])
        qs_before = list(df["quality_score"])
        apply_power_rating_context_to_df(df, ratings=_RATINGS)
        assert list(df["edge"]) == edges_before
        assert list(df["quality_score"]) == qs_before

    def test_kelly_stake_unmodified(self):
        df = _make_board_df()
        stakes_before = list(df["stake_amount"])
        apply_power_rating_context_to_df(df, ratings=_RATINGS)
        assert list(df["stake_amount"]) == stakes_before


# ---------------------------------------------------------------------------
# Missing-data safety
# ---------------------------------------------------------------------------

class TestMissingDataSafety:

    def test_empty_ratings_never_crashes(self):
        df = _make_board_df()
        result = apply_power_rating_context_to_df(df, ratings={})
        assert isinstance(result, pd.DataFrame)
        assert len(result) == len(df)

    def test_none_ratings_never_crashes(self):
        df = _make_board_df()
        result = apply_power_rating_context_to_df(df, ratings=None)
        assert isinstance(result, pd.DataFrame)

    def test_empty_dataframe_never_crashes(self):
        result = apply_power_rating_context_to_df(pd.DataFrame(), ratings=_RATINGS)
        assert isinstance(result, pd.DataFrame)

    def test_missing_home_away_column_never_crashes(self):
        df = _make_board_df().drop(columns=["home_away"])
        result = apply_power_rating_context_to_df(df, ratings=_RATINGS)
        assert isinstance(result, pd.DataFrame)
        assert "team_power_context_applied" in result.columns

    def test_missing_team_abbr_column_never_crashes(self):
        df = _make_board_df().drop(columns=["team_abbr"])
        result = apply_power_rating_context_to_df(df, ratings=_RATINGS)
        assert isinstance(result, pd.DataFrame)

    def test_partial_ratings_only_known_teams_get_applied_true(self):
        df = _make_board_df()
        partial = {"LAL": 1620.0, "GSW": 1510.0}
        result = apply_power_rating_context_to_df(df, ratings=partial)
        lal_row = result[result["team_abbr"] == "LAL"].iloc[0]
        bos_row = result[result["team_abbr"] == "BOS"].iloc[0]
        assert lal_row["team_power_context_applied"] == True  # noqa: E712  (numpy bool)
        assert bos_row["team_power_context_applied"] == False  # noqa: E712


# ---------------------------------------------------------------------------
# Quality summary integration
# ---------------------------------------------------------------------------

class TestPowerRatingContextSummary:

    def test_returns_dict_with_required_keys(self):
        df = apply_power_rating_context_to_df(_make_board_df(), ratings=_RATINGS)
        summary = _power_rating_context_summary(df)
        for key in (
            "total_rows", "context_applied_count", "context_missing_count",
            "blowout_risk_distribution", "competitiveness_distribution", "observation_only",
        ):
            assert key in summary, f"missing key: {key}"

    def test_observation_only_always_true(self):
        df = apply_power_rating_context_to_df(_make_board_df(), ratings=_RATINGS)
        assert _power_rating_context_summary(df)["observation_only"] is True

    def test_empty_df_returns_zero_counts(self):
        summary = _power_rating_context_summary(pd.DataFrame())
        assert summary["total_rows"] == 0
        assert summary["context_applied_count"] == 0

    def test_df_without_context_columns_returns_empty_summary(self):
        summary = _power_rating_context_summary(_make_board_df())
        assert summary["total_rows"] == 0

    def test_all_applied_when_ratings_present(self):
        df = apply_power_rating_context_to_df(_make_board_df(), ratings=_RATINGS)
        summary = _power_rating_context_summary(df)
        assert summary["context_applied_count"] == len(df)
        assert summary["context_missing_count"] == 0

    def test_none_applied_when_ratings_empty(self):
        df = apply_power_rating_context_to_df(_make_board_df(), ratings={})
        summary = _power_rating_context_summary(df)
        assert summary["context_applied_count"] == 0
        assert summary["context_missing_count"] == len(df)

    def test_blowout_risk_distribution_contains_known_labels(self):
        df = apply_power_rating_context_to_df(_make_board_df(), ratings=_RATINGS)
        dist = _power_rating_context_summary(df)["blowout_risk_distribution"]
        for label in dist:
            assert label in ("HIGH", "MEDIUM", "LOW", "UNKNOWN")

    def test_competitiveness_distribution_sums_to_total_rows(self):
        df = apply_power_rating_context_to_df(_make_board_df(), ratings=_RATINGS)
        summary = _power_rating_context_summary(df)
        total = sum(summary["competitiveness_distribution"].values())
        assert total == summary["total_rows"]


# ---------------------------------------------------------------------------
# Power ratings store
# ---------------------------------------------------------------------------

class TestLoadGameResults:

    def test_fixture_file_loads_without_error(self):
        df = load_game_results(_FIXTURE_CSV)
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    def test_fixture_has_required_columns(self):
        df = load_game_results(_FIXTURE_CSV)
        for col in ("date", "home_team_id", "away_team_id", "home_score", "away_score"):
            assert col in df.columns, f"missing column: {col}"

    def test_missing_file_returns_empty_dataframe(self):
        df = load_game_results("nonexistent/path/game_results.csv")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_malformed_file_returns_empty_dataframe(self, tmp_path):
        bad = tmp_path / "bad.csv"
        bad.write_text("col_a,col_b\n1,2\n")
        df = load_game_results(bad)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_empty_file_returns_empty_dataframe(self, tmp_path):
        empty = tmp_path / "empty.csv"
        empty.write_text("")
        df = load_game_results(empty)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0


class TestBuildCurrentPowerRatings:

    def test_returns_dict(self):
        df = load_game_results(_FIXTURE_CSV)
        ratings = build_current_power_ratings(df)
        assert isinstance(ratings, dict)

    def test_fixture_produces_four_teams(self):
        df = load_game_results(_FIXTURE_CSV)
        ratings = build_current_power_ratings(df)
        assert set(ratings.keys()) == {"OKC", "LAL", "CLE", "DET"}

    def test_ratings_are_floats(self):
        df = load_game_results(_FIXTURE_CSV)
        for v in build_current_power_ratings(df).values():
            assert isinstance(v, float)

    def test_winner_rated_above_loser(self):
        df = load_game_results(_FIXTURE_CSV)
        ratings = build_current_power_ratings(df)
        # OKC won 4 of 5 against LAL in fixture → should be rated higher
        assert ratings["OKC"] > ratings["LAL"]

    def test_empty_df_returns_empty_dict(self):
        assert build_current_power_ratings(pd.DataFrame()) == {}

    def test_none_returns_empty_dict(self):
        assert build_current_power_ratings(None) == {}

    def test_ratings_differ_from_default(self):
        from courtvision.ratings.power_rating import DEFAULT_RATING
        df = load_game_results(_FIXTURE_CSV)
        ratings = build_current_power_ratings(df)
        assert any(abs(v - DEFAULT_RATING) > 0 for v in ratings.values())


class TestGetLatestTeamPowerRatings:

    def test_fixture_path_returns_nonempty_dict(self):
        ratings = get_latest_team_power_ratings(_FIXTURE_CSV)
        assert isinstance(ratings, dict)
        assert len(ratings) > 0

    def test_missing_path_returns_empty_dict(self):
        ratings = get_latest_team_power_ratings("nonexistent/path/x.csv")
        assert ratings == {}

    def test_default_path_returns_dict(self):
        # Default path may not exist in CI — must not crash either way
        ratings = get_latest_team_power_ratings()
        assert isinstance(ratings, dict)


# ---------------------------------------------------------------------------
# End-to-end: real ratings → board enrichment → quality summary
# ---------------------------------------------------------------------------

class TestRealRatingsEndToEnd:

    def _board_with_fixture_teams(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"team_abbr": "OKC", "opponent": "LAL", "home_away": "home",
             "player_name": "P1", "edge": 0.07, "quality_score": 0.80},
            {"team_abbr": "LAL", "opponent": "OKC", "home_away": "away",
             "player_name": "P2", "edge": 0.05, "quality_score": 0.72},
            {"team_abbr": "CLE", "opponent": "DET", "home_away": "away",
             "player_name": "P3", "edge": 0.06, "quality_score": 0.75},
            {"team_abbr": "DET", "opponent": "CLE", "home_away": "home",
             "player_name": "P4", "edge": 0.04, "quality_score": 0.68},
        ])

    def test_real_ratings_produce_nonzero_context_applied(self):
        ratings = get_latest_team_power_ratings(_FIXTURE_CSV)
        df = apply_power_rating_context_to_df(self._board_with_fixture_teams(), ratings=ratings)
        summary = _power_rating_context_summary(df)
        assert summary["context_applied_count"] > 0

    def test_real_ratings_produce_zero_missing_count(self):
        ratings = get_latest_team_power_ratings(_FIXTURE_CSV)
        df = apply_power_rating_context_to_df(self._board_with_fixture_teams(), ratings=ratings)
        summary = _power_rating_context_summary(df)
        assert summary["context_missing_count"] == 0

    def test_real_ratings_no_unknown_in_blowout_distribution(self):
        ratings = get_latest_team_power_ratings(_FIXTURE_CSV)
        df = apply_power_rating_context_to_df(self._board_with_fixture_teams(), ratings=ratings)
        summary = _power_rating_context_summary(df)
        assert summary["blowout_risk_distribution"].get("UNKNOWN", 0) == 0

    def test_quality_summary_json_shows_applied_count_from_ratings(self):
        ratings = get_latest_team_power_ratings(_FIXTURE_CSV)
        df = self._board_with_fixture_teams()
        enriched = df.copy()
        apply_power_rating_context_to_df(enriched, ratings=ratings)
        summary = _power_rating_context_summary(enriched)
        assert summary["context_applied_count"] == len(df)
        assert summary["observation_only"] is True

    def test_core_board_columns_unchanged_with_real_ratings(self):
        ratings = get_latest_team_power_ratings(_FIXTURE_CSV)
        df = self._board_with_fixture_teams()
        edges_before = list(df["edge"])
        qs_before = list(df["quality_score"])
        apply_power_rating_context_to_df(df, ratings=ratings)
        assert list(df["edge"]) == edges_before
        assert list(df["quality_score"]) == qs_before

    def test_missing_file_falls_back_to_safe_defaults(self):
        ratings = get_latest_team_power_ratings("nonexistent/x.csv")
        df = apply_power_rating_context_to_df(self._board_with_fixture_teams(), ratings=ratings)
        summary = _power_rating_context_summary(df)
        assert summary["context_applied_count"] == 0
        assert summary["context_missing_count"] == len(self._board_with_fixture_teams())
