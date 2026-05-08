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
    GAME_RESULTS_COLUMNS,
    append_game_results,
    build_current_power_ratings,
    games_df_to_results,
    get_latest_team_power_ratings,
    load_game_results,
)
from courtvision.reporting.quality_summary import (
    _power_rating_context_summary,
    _write_power_rating_diagnostics_csv,
)

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


# ---------------------------------------------------------------------------
# Game results history helpers
# ---------------------------------------------------------------------------

def _make_raw_games_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _final_game(game_id: str, date: str, home_abbr: str, away_abbr: str,
                home_score: int, away_score: int, status: str = "Final",
                home_name: str = "", away_name: str = "") -> dict:
    return {
        "id": game_id,
        "date": date,
        "status": status,
        "home_team": {"abbreviation": home_abbr, "full_name": home_name},
        "visitor_team": {"abbreviation": away_abbr, "full_name": away_name},
        "home_team_score": home_score,
        "visitor_team_score": away_score,
    }


class TestGamesDfToResults:

    def test_final_games_included(self):
        raw = _make_raw_games_df([
            _final_game("G1", "2026-05-01", "OKC", "LAL", 110, 100),
            _final_game("G2", "2026-05-02", "CLE", "DET", 105, 98),
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 2
        assert set(result.columns) == set(GAME_RESULTS_COLUMNS)

    def test_non_final_games_skipped(self):
        raw = _make_raw_games_df([
            {"id": "G1", "date": "2026-05-01", "status": "Scheduled",
             "home_team": {"abbreviation": "OKC"}, "visitor_team": {"abbreviation": "LAL"},
             "home_team_score": 0, "visitor_team_score": 0},
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 0

    def test_missing_scores_skipped(self):
        raw = _make_raw_games_df([
            {"id": "G1", "date": "2026-05-01", "status": "Final",
             "home_team": {"abbreviation": "OKC"}, "visitor_team": {"abbreviation": "LAL"},
             "home_team_score": None, "visitor_team_score": None},
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 0

    def test_missing_abbreviation_skipped(self):
        raw = _make_raw_games_df([
            {"id": "G1", "date": "2026-05-01", "status": "Final",
             "home_team": {}, "visitor_team": {},
             "home_team_score": 110, "visitor_team_score": 100},
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 0

    def test_abbreviations_uppercased(self):
        raw = _make_raw_games_df([
            {"id": "G1", "date": "2026-05-01", "status": "Final",
             "home_team": {"abbreviation": "okc"}, "visitor_team": {"abbreviation": "lal"},
             "home_team_score": 110, "visitor_team_score": 100},
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 1
        assert result.iloc[0]["home_team_id"] == "OKC"
        assert result.iloc[0]["away_team_id"] == "LAL"

    def test_fallback_date_used_when_date_missing(self):
        raw = _make_raw_games_df([
            {"id": "G1", "status": "Final",
             "home_team": {"abbreviation": "OKC"}, "visitor_team": {"abbreviation": "LAL"},
             "home_team_score": 110, "visitor_team_score": 100},
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-07")
        assert result.iloc[0]["date"] == "2026-05-07"

    def test_empty_dataframe_returns_empty(self):
        result = games_df_to_results(pd.DataFrame(), fallback_date="2026-05-01")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_none_returns_empty(self):
        result = games_df_to_results(None, fallback_date="2026-05-01")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_complete_status_also_accepted(self):
        raw = _make_raw_games_df([
            {"id": "G1", "date": "2026-05-01", "status": "complete",
             "home_team": {"abbreviation": "OKC"}, "visitor_team": {"abbreviation": "LAL"},
             "home_team_score": 110, "visitor_team_score": 100},
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 1

    def test_nonzero_scores_with_blank_status_skipped(self):
        raw = _make_raw_games_df([
            {"id": "G1", "date": "2026-05-01", "status": "",
             "home_team": {"abbreviation": "OKC"}, "visitor_team": {"abbreviation": "LAL"},
             "home_team_score": 110, "visitor_team_score": 100},
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 0

    def test_nonzero_scores_with_q3_status_skipped(self):
        raw = _make_raw_games_df([
            _final_game("G1", "2026-05-01", "OKC", "LAL", 110, 100, status="Q3"),
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 0

    def test_nonzero_scores_with_halftime_status_skipped(self):
        raw = _make_raw_games_df([
            _final_game("G1", "2026-05-01", "OKC", "LAL", 110, 100, status="Halftime"),
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 0

    def test_nonzero_scores_with_in_progress_status_skipped(self):
        raw = _make_raw_games_df([
            _final_game("G1", "2026-05-01", "OKC", "LAL", 110, 100, status="In Progress"),
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 0

    def test_final_status_with_valid_scores_accepted(self):
        raw = _make_raw_games_df([
            _final_game("G1", "2026-05-01", "OKC", "LAL", 110, 100, status="Final"),
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 1

    def test_final_status_with_zero_home_score_skipped(self):
        raw = _make_raw_games_df([
            _final_game("G1", "2026-05-01", "OKC", "LAL", 0, 100, status="Final"),
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 0

    def test_final_status_with_zero_away_score_skipped(self):
        raw = _make_raw_games_df([
            _final_game("G1", "2026-05-01", "OKC", "LAL", 110, 0, status="Final"),
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 0

    def test_final_status_with_negative_score_skipped(self):
        raw = _make_raw_games_df([
            _final_game("G1", "2026-05-01", "OKC", "LAL", -1, 100, status="Final"),
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 0

    def test_final_ot_status_with_valid_scores_accepted(self):
        raw = _make_raw_games_df([
            _final_game("G1", "2026-05-01", "OKC", "LAL", 110, 100, status="Final/OT"),
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 1

    def test_missing_status_with_nonzero_scores_skipped(self):
        raw = _make_raw_games_df([
            {"id": "G1", "date": "2026-05-01",
             "home_team": {"abbreviation": "OKC"}, "visitor_team": {"abbreviation": "LAL"},
             "home_team_score": 110, "visitor_team_score": 100},
        ])
        result = games_df_to_results(raw, fallback_date="2026-05-01")
        assert len(result) == 0


class TestAppendGameResults:

    def _new_results(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"date": "2026-05-01", "home_team_id": "OKC", "away_team_id": "LAL",
             "home_score": "110", "away_score": "100", "game_id": "G100",
             "home_team_name": "Thunder", "away_team_name": "Lakers"},
            {"date": "2026-05-02", "home_team_id": "CLE", "away_team_id": "DET",
             "home_score": "105", "away_score": "98", "game_id": "G101",
             "home_team_name": "Cavaliers", "away_team_name": "Pistons"},
        ])

    def test_missing_history_file_created(self, tmp_path):
        p = tmp_path / "game_results.csv"
        summary = append_game_results(self._new_results(), path=p)
        assert p.exists()
        assert summary["appended"] == 2
        assert summary["total_rows"] == 2

    def test_existing_history_appended(self, tmp_path):
        p = tmp_path / "game_results.csv"
        append_game_results(self._new_results(), path=p)
        extra = pd.DataFrame([
            {"date": "2026-05-03", "home_team_id": "OKC", "away_team_id": "CLE",
             "home_score": "112", "away_score": "108", "game_id": "G102",
             "home_team_name": "Thunder", "away_team_name": "Cavaliers"},
        ])
        summary = append_game_results(extra, path=p)
        assert summary["appended"] == 1
        assert summary["total_rows"] == 3

    def test_duplicate_game_id_not_appended(self, tmp_path):
        p = tmp_path / "game_results.csv"
        append_game_results(self._new_results(), path=p)
        summary = append_game_results(self._new_results(), path=p)
        assert summary["skipped_duplicates"] == 2
        assert summary["total_rows"] == 2

    def test_dedup_by_date_home_away_when_no_game_id(self, tmp_path):
        p = tmp_path / "game_results.csv"
        no_id = self._new_results().copy()
        no_id["game_id"] = ""
        append_game_results(no_id, path=p)
        summary = append_game_results(no_id, path=p)
        assert summary["total_rows"] == 2

    def test_output_sorted_chronologically(self, tmp_path):
        p = tmp_path / "game_results.csv"
        unordered = pd.DataFrame([
            {"date": "2026-05-03", "home_team_id": "OKC", "away_team_id": "LAL",
             "home_score": "110", "away_score": "100", "game_id": "G103",
             "home_team_name": "", "away_team_name": ""},
            {"date": "2026-05-01", "home_team_id": "CLE", "away_team_id": "DET",
             "home_score": "105", "away_score": "98", "game_id": "G101",
             "home_team_name": "", "away_team_name": ""},
        ])
        append_game_results(unordered, path=p)
        df = pd.read_csv(p)
        assert list(df["date"]) == sorted(df["date"])

    def test_updated_history_loads_via_get_latest(self, tmp_path):
        p = tmp_path / "game_results.csv"
        append_game_results(self._new_results(), path=p)
        ratings = get_latest_team_power_ratings(p)
        assert isinstance(ratings, dict)
        assert len(ratings) > 0

    def test_empty_new_df_does_not_crash(self, tmp_path):
        p = tmp_path / "game_results.csv"
        summary = append_game_results(pd.DataFrame(columns=list(GAME_RESULTS_COLUMNS)), path=p)
        assert summary["appended"] == 0

    def test_returns_summary_dict_keys(self, tmp_path):
        p = tmp_path / "game_results.csv"
        summary = append_game_results(self._new_results(), path=p)
        for key in ("fetched", "accepted", "appended", "skipped_duplicates", "total_rows", "output_path"):
            assert key in summary


class TestHistoryIntegrationSafety:

    def test_diagnostics_unaffected_after_append(self, tmp_path):
        p = tmp_path / "game_results.csv"
        new_results = pd.DataFrame([
            {"date": "2026-05-01", "home_team_id": "OKC", "away_team_id": "LAL",
             "home_score": "110", "away_score": "100", "game_id": "G100",
             "home_team_name": "Thunder", "away_team_name": "Lakers"},
        ])
        append_game_results(new_results, path=p)
        ratings = get_latest_team_power_ratings(p)
        board = pd.DataFrame([
            {"team_abbr": "OKC", "opponent": "LAL", "home_away": "home",
             "player_name": "P1", "edge": 0.07, "quality_score": 0.80,
             "kelly_fraction": 0.03, "stake_amount": 30.0},
        ])
        edge_before = board["edge"].iloc[0]
        qs_before = board["quality_score"].iloc[0]
        apply_power_rating_context_to_df(board, ratings=ratings)
        assert board["edge"].iloc[0] == edge_before
        assert board["quality_score"].iloc[0] == qs_before

    def test_no_kelly_or_stake_change_after_history_update(self, tmp_path):
        p = tmp_path / "game_results.csv"
        new_results = pd.DataFrame([
            {"date": "2026-05-01", "home_team_id": "OKC", "away_team_id": "LAL",
             "home_score": "110", "away_score": "100", "game_id": "G100",
             "home_team_name": "", "away_team_name": ""},
        ])
        append_game_results(new_results, path=p)
        ratings = get_latest_team_power_ratings(p)
        board = pd.DataFrame([
            {"team_abbr": "OKC", "opponent": "LAL", "home_away": "home",
             "player_name": "P1", "edge": 0.07, "quality_score": 0.80,
             "kelly_fraction": 0.03, "stake_amount": 30.0},
        ])
        stakes_before = list(board["stake_amount"])
        apply_power_rating_context_to_df(board, ratings=ratings)
        assert list(board["stake_amount"]) == stakes_before


# ---------------------------------------------------------------------------
# Power rating diagnostics CSV
# ---------------------------------------------------------------------------

def _make_enriched_board(ratings: dict | None = None) -> pd.DataFrame:
    if ratings is None:
        ratings = {"OKC": 1620.0, "LAL": 1510.0, "CLE": 1590.0, "DET": 1470.0}
    df = pd.DataFrame([
        {"player_name": "P1", "team_abbr": "OKC", "opponent": "LAL",
         "home_away": "home", "market_type": "PTS", "selection_side": "OVER",
         "game_id": "G1", "edge": 0.07, "quality_score": 0.80,
         "kelly_fraction": 0.03, "stake_amount": 30.0},
        {"player_name": "P2", "team_abbr": "LAL", "opponent": "OKC",
         "home_away": "away", "market_type": "AST", "selection_side": "OVER",
         "game_id": "G1", "edge": 0.05, "quality_score": 0.72,
         "kelly_fraction": 0.025, "stake_amount": 25.0},
        {"player_name": "P3", "team_abbr": "CLE", "opponent": "DET",
         "home_away": "home", "market_type": "REB", "selection_side": "UNDER",
         "game_id": "G2", "edge": 0.06, "quality_score": 0.75,
         "kelly_fraction": 0.02, "stake_amount": 20.0},
    ])
    apply_power_rating_context_to_df(df, ratings=ratings)
    return df


class TestWritePowerRatingDiagnosticsCSV:

    def test_csv_is_written(self, tmp_path):
        enriched = _make_enriched_board()
        out = _write_power_rating_diagnostics_csv(enriched, "2026-05-08", tmp_path)
        assert out is not None
        assert out.exists()
        assert out.name == "power_rating_context_2026-05-08.csv"

    def test_csv_includes_power_rating_columns(self, tmp_path):
        enriched = _make_enriched_board()
        out = _write_power_rating_diagnostics_csv(enriched, "2026-05-08", tmp_path)
        df = pd.read_csv(out)
        for col in POWER_RATING_CONTEXT_COLUMNS:
            assert col in df.columns, f"missing power rating column: {col}"

    def test_csv_includes_board_identity_columns(self, tmp_path):
        enriched = _make_enriched_board()
        out = _write_power_rating_diagnostics_csv(enriched, "2026-05-08", tmp_path)
        df = pd.read_csv(out)
        for col in ("player_name", "team_abbr", "opponent_abbr", "market_type"):
            assert col in df.columns, f"missing identity column: {col}"

    def test_csv_derives_home_away_team_abbr(self, tmp_path):
        enriched = _make_enriched_board()
        out = _write_power_rating_diagnostics_csv(enriched, "2026-05-08", tmp_path)
        df = pd.read_csv(out)
        assert "home_team_abbr" in df.columns
        assert "away_team_abbr" in df.columns
        p1_row = df[df["player_name"] == "P1"].iloc[0]
        assert p1_row["home_team_abbr"] == "OKC"
        assert p1_row["away_team_abbr"] == "LAL"

    def test_csv_row_count_matches_board(self, tmp_path):
        enriched = _make_enriched_board()
        out = _write_power_rating_diagnostics_csv(enriched, "2026-05-08", tmp_path)
        df = pd.read_csv(out)
        assert len(df) == 3

    def test_missing_ratings_writes_safe_defaults(self, tmp_path):
        enriched = _make_enriched_board(ratings={})
        out = _write_power_rating_diagnostics_csv(enriched, "2026-05-08", tmp_path)
        assert out is not None
        df = pd.read_csv(out)
        assert len(df) == 3
        assert (df["team_power_context_applied"] == False).all()  # noqa: E712

    def test_empty_df_returns_none(self, tmp_path):
        out = _write_power_rating_diagnostics_csv(pd.DataFrame(), "2026-05-08", tmp_path)
        assert out is None

    def test_df_without_context_columns_returns_none(self, tmp_path):
        df = pd.DataFrame([{"player_name": "P1", "team_abbr": "OKC"}])
        out = _write_power_rating_diagnostics_csv(df, "2026-05-08", tmp_path)
        assert out is None

    def test_original_board_columns_unchanged(self, tmp_path):
        df = pd.DataFrame([
            {"player_name": "P1", "team_abbr": "OKC", "opponent": "LAL",
             "home_away": "home", "market_type": "PTS", "selection_side": "OVER",
             "game_id": "G1", "edge": 0.07, "quality_score": 0.80,
             "kelly_fraction": 0.03, "stake_amount": 30.0},
        ])
        edge_before = df["edge"].iloc[0]
        qs_before = df["quality_score"].iloc[0]
        stake_before = df["stake_amount"].iloc[0]
        enriched = df.copy()
        apply_power_rating_context_to_df(enriched, ratings={"OKC": 1620.0, "LAL": 1510.0})
        _write_power_rating_diagnostics_csv(enriched, "2026-05-08", tmp_path)
        assert df["edge"].iloc[0] == edge_before
        assert df["quality_score"].iloc[0] == qs_before
        assert df["stake_amount"].iloc[0] == stake_before

    def test_diagnostics_dir_created_if_missing(self, tmp_path):
        subdir = tmp_path / "diag" / "nested"
        enriched = _make_enriched_board()
        out = _write_power_rating_diagnostics_csv(enriched, "2026-05-08", subdir)
        assert out is not None
        assert out.exists()

    def test_path_returned_reflects_prediction_date(self, tmp_path):
        enriched = _make_enriched_board()
        out = _write_power_rating_diagnostics_csv(enriched, "2026-04-19", tmp_path)
        assert "2026-04-19" in out.name

    def test_power_rating_context_summary_has_diagnostics_path_key(self, tmp_path):
        enriched = _make_enriched_board()
        summary = _power_rating_context_summary(enriched)
        _pr_diag_path = _write_power_rating_diagnostics_csv(enriched, "2026-05-08", tmp_path)
        summary["diagnostics_csv_path"] = str(_pr_diag_path) if _pr_diag_path else None
        assert "diagnostics_csv_path" in summary
        assert summary["diagnostics_csv_path"] is not None
