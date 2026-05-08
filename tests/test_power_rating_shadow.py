"""Tests for CourtVision Power Rating Shadow Analysis.

Verifies:
- join works when matching keys exist
- missing diagnostics file is skipped safely
- missing graded picks reports cleanly
- grouping by blowout_risk and competitiveness works
- no selection/scoring/Kelly logic changes
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.power_rating_shadow import (
    OBSERVATION_ONLY,
    PICK_HISTORY_REQUIRED_COLUMNS,
    _group_metrics,
    _rating_diff_bucket,
    build_shadow_analysis,
    join_picks_with_context,
    load_pick_history,
    load_power_rating_context,
    write_shadow_outputs,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_picks(rows: list[dict] | None = None) -> pd.DataFrame:
    if rows is None:
        rows = [
            {
                "prediction_date": "2026-05-01", "run_timestamp": "2026-05-01T12:00:00Z",
                "player_name": "Player A", "team": "OKC", "opponent": "LAL",
                "market": "player_points", "selection": "over", "line": 24.5,
                "edge": 8.0, "confidence": 0.72, "quality_score": 0.81,
                "result_status": "hit", "odds": -110, "game_id": "G1",
            },
            {
                "prediction_date": "2026-05-01", "run_timestamp": "2026-05-01T12:00:00Z",
                "player_name": "Player B", "team": "LAL", "opponent": "OKC",
                "market": "player_points", "selection": "under", "line": 18.5,
                "edge": 5.0, "confidence": 0.65, "quality_score": 0.70,
                "result_status": "miss", "odds": -110, "game_id": "G1",
            },
            {
                "prediction_date": "2026-05-02", "run_timestamp": "2026-05-02T12:00:00Z",
                "player_name": "Player C", "team": "CLE", "opponent": "DET",
                "market": "player_points", "selection": "over", "line": 21.5,
                "edge": 6.0, "confidence": 0.68, "quality_score": 0.75,
                "result_status": "hit", "odds": -105, "game_id": "G2",
            },
            {
                "prediction_date": "2026-05-02", "run_timestamp": "2026-05-02T12:00:00Z",
                "player_name": "Player D", "team": "DET", "opponent": "CLE",
                "market": "player_points", "selection": "over", "line": 14.5,
                "edge": 4.0, "confidence": 0.62, "quality_score": 0.66,
                "result_status": "void", "odds": -110, "game_id": "G2",
            },
        ]
    return pd.DataFrame(rows)


def _make_context(rows: list[dict] | None = None) -> pd.DataFrame:
    if rows is None:
        rows = [
            {
                "player_name": "Player A", "team_abbr": "OKC", "opponent_abbr": "LAL",
                "market_type": "player_points", "side": "over", "game_id": "G1",
                "home_team_power_rating": 1620.0, "away_team_power_rating": 1510.0,
                "power_rating_diff": 110.0, "home_adjusted_power_rating_diff": 145.0,
                "home_win_probability": 0.71, "away_win_probability": 0.29,
                "expected_competitiveness": "LOW", "blowout_risk": "HIGH",
                "team_power_context_applied": True,
                "team_power_context_missing_reason": "",
            },
            {
                "player_name": "Player B", "team_abbr": "LAL", "opponent_abbr": "OKC",
                "market_type": "player_points", "side": "under", "game_id": "G1",
                "home_team_power_rating": 1620.0, "away_team_power_rating": 1510.0,
                "power_rating_diff": 110.0, "home_adjusted_power_rating_diff": 145.0,
                "home_win_probability": 0.71, "away_win_probability": 0.29,
                "expected_competitiveness": "LOW", "blowout_risk": "HIGH",
                "team_power_context_applied": True,
                "team_power_context_missing_reason": "",
            },
        ]
    return pd.DataFrame(rows)


def _write_picks_csv(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    p = tmp_path / "pick_history.csv"
    _make_picks(rows).to_csv(p, index=False)
    return p


def _write_context_csv(tmp_path: Path, date: str, rows: list[dict] | None = None) -> Path:
    p = tmp_path / f"power_rating_context_{date}.csv"
    _make_context(rows).to_csv(p, index=False)
    return p


# ---------------------------------------------------------------------------
# _rating_diff_bucket
# ---------------------------------------------------------------------------

class TestRatingDiffBucket:

    def test_large_home_adv(self):
        assert _rating_diff_bucket(150.0) == "LARGE_HOME_ADV"

    def test_mod_home_adv(self):
        assert _rating_diff_bucket(80.0) == "MOD_HOME_ADV"

    def test_competitive(self):
        assert _rating_diff_bucket(0.0) == "COMPETITIVE"
        assert _rating_diff_bucket(-30.0) == "COMPETITIVE"

    def test_mod_away_adv(self):
        assert _rating_diff_bucket(-90.0) == "MOD_AWAY_ADV"

    def test_large_away_adv(self):
        assert _rating_diff_bucket(-130.0) == "LARGE_AWAY_ADV"

    def test_none_returns_unknown(self):
        assert _rating_diff_bucket(None) == "UNKNOWN"

    def test_nan_returns_unknown(self):
        import math
        assert _rating_diff_bucket(float("nan")) == "UNKNOWN"

    def test_boundary_120_is_large_home(self):
        assert _rating_diff_bucket(120.0) == "LARGE_HOME_ADV"

    def test_boundary_59_is_competitive(self):
        assert _rating_diff_bucket(59.9) == "COMPETITIVE"


# ---------------------------------------------------------------------------
# load_pick_history
# ---------------------------------------------------------------------------

class TestLoadPickHistory:

    def test_loads_csv(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        df = load_pick_history(p)
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    def test_missing_file_returns_empty(self):
        df = load_pick_history("nonexistent/pick_history.csv")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_dedup_keeps_latest_run(self, tmp_path):
        rows = [
            {"prediction_date": "2026-05-01", "run_timestamp": "2026-05-01T12:00:00Z",
             "player_name": "P1", "team": "OKC", "market": "player_points",
             "selection": "over", "result_status": "hit", "line": 24.5,
             "edge": 8.0, "confidence": 0.72, "quality_score": 0.81, "odds": -110, "game_id": "G1"},
            {"prediction_date": "2026-05-01", "run_timestamp": "2026-05-01T14:00:00Z",
             "player_name": "P1", "team": "OKC", "market": "player_points",
             "selection": "over", "result_status": "hit", "line": 25.5,
             "edge": 7.0, "confidence": 0.70, "quality_score": 0.79, "odds": -120, "game_id": "G1"},
        ]
        p = _write_picks_csv(tmp_path, rows)
        df = load_pick_history(p, dedup_latest_run=True)
        assert len(df) == 1
        assert df.iloc[0]["line"] == 25.5

    def test_no_dedup_keeps_all(self, tmp_path):
        rows = [
            {"prediction_date": "2026-05-01", "run_timestamp": "2026-05-01T12:00:00Z",
             "player_name": "P1", "team": "OKC", "market": "player_points",
             "selection": "over", "result_status": "hit", "line": 24.5,
             "edge": 8.0, "confidence": 0.72, "quality_score": 0.81, "odds": -110, "game_id": "G1"},
            {"prediction_date": "2026-05-01", "run_timestamp": "2026-05-01T14:00:00Z",
             "player_name": "P1", "team": "OKC", "market": "player_points",
             "selection": "over", "result_status": "hit", "line": 25.5,
             "edge": 7.0, "confidence": 0.70, "quality_score": 0.79, "odds": -120, "game_id": "G1"},
        ]
        p = _write_picks_csv(tmp_path, rows)
        df = load_pick_history(p, dedup_latest_run=False)
        assert len(df) == 2

    def test_malformed_file_returns_empty(self, tmp_path):
        bad = tmp_path / "pick_history.csv"
        bad.write_text("col_a,col_b\n1,2\n")
        df = load_pick_history(bad)
        assert len(df) == 0


# ---------------------------------------------------------------------------
# load_power_rating_context
# ---------------------------------------------------------------------------

class TestLoadPowerRatingContext:

    def test_loads_existing_file(self, tmp_path):
        _write_context_csv(tmp_path, "2026-05-01")
        df = load_power_rating_context(tmp_path, "2026-05-01")
        assert df is not None
        assert len(df) == 2

    def test_missing_file_returns_none(self, tmp_path):
        result = load_power_rating_context(tmp_path, "2026-04-01")
        assert result is None

    def test_returns_dataframe(self, tmp_path):
        _write_context_csv(tmp_path, "2026-05-01")
        df = load_power_rating_context(tmp_path, "2026-05-01")
        assert isinstance(df, pd.DataFrame)


# ---------------------------------------------------------------------------
# join_picks_with_context
# ---------------------------------------------------------------------------

class TestJoinPicksWithContext:

    def test_matching_rows_get_context(self):
        picks = _make_picks([
            {"prediction_date": "2026-05-01", "run_timestamp": "T",
             "player_name": "Player A", "team": "OKC", "market": "player_points",
             "selection": "over", "result_status": "hit", "line": 24.5,
             "edge": 8.0, "confidence": 0.72, "quality_score": 0.81, "odds": -110, "game_id": "G1"},
        ])
        ctx = _make_context()
        result = join_picks_with_context(picks, ctx)
        assert result.iloc[0]["context_available"] == True  # noqa: E712
        assert result.iloc[0]["blowout_risk"] == "HIGH"

    def test_unmatched_rows_get_no_context(self):
        picks = _make_picks([
            {"prediction_date": "2026-05-01", "run_timestamp": "T",
             "player_name": "Player Z", "team": "GSW", "market": "player_points",
             "selection": "over", "result_status": "hit", "line": 20.5,
             "edge": 7.0, "confidence": 0.70, "quality_score": 0.78, "odds": -110, "game_id": "G9"},
        ])
        ctx = _make_context()
        result = join_picks_with_context(picks, ctx)
        assert result.iloc[0]["context_available"] == False  # noqa: E712

    def test_empty_context_returns_picks_intact(self):
        picks = _make_picks()
        result = join_picks_with_context(picks, pd.DataFrame())
        assert len(result) == len(picks)
        assert "context_available" in result.columns

    def test_none_context_returns_picks_intact(self):
        picks = _make_picks()
        result = join_picks_with_context(picks, None)
        assert len(result) == len(picks)

    def test_original_pick_columns_unchanged(self):
        picks = _make_picks([
            {"prediction_date": "2026-05-01", "run_timestamp": "T",
             "player_name": "Player A", "team": "OKC", "market": "player_points",
             "selection": "over", "result_status": "hit", "line": 24.5,
             "edge": 8.0, "confidence": 0.72, "quality_score": 0.81, "odds": -110, "game_id": "G1"},
        ])
        ctx = _make_context()
        edge_before = picks["edge"].iloc[0]
        qs_before = picks["quality_score"].iloc[0]
        result = join_picks_with_context(picks, ctx)
        assert result["edge"].iloc[0] == edge_before
        assert result["quality_score"].iloc[0] == qs_before

    def test_case_insensitive_side_matching(self):
        picks = _make_picks([
            {"prediction_date": "2026-05-01", "run_timestamp": "T",
             "player_name": "Player A", "team": "OKC", "market": "player_points",
             "selection": "OVER", "result_status": "hit", "line": 24.5,
             "edge": 8.0, "confidence": 0.72, "quality_score": 0.81, "odds": -110, "game_id": "G1"},
        ])
        ctx = _make_context()
        result = join_picks_with_context(picks, ctx)
        assert result.iloc[0]["blowout_risk"] == "HIGH"


# ---------------------------------------------------------------------------
# _group_metrics
# ---------------------------------------------------------------------------

class TestGroupMetrics:

    def test_basic_metrics(self):
        df = pd.DataFrame([
            {"result_status": "hit", "edge": 8.0, "confidence": 0.72, "quality_score": 0.81, "odds": -110},
            {"result_status": "miss", "edge": 5.0, "confidence": 0.65, "quality_score": 0.70, "odds": -110},
            {"result_status": "void", "edge": 6.0, "confidence": 0.68, "quality_score": 0.75, "odds": -110},
        ])
        m = _group_metrics(df)
        assert m["total_picks"] == 3
        assert m["graded_picks"] == 2
        assert m["hit_count"] == 1
        assert m["miss_count"] == 1
        assert m["void_count"] == 1
        assert m["hit_rate"] == 0.5

    def test_all_hits(self):
        df = pd.DataFrame([
            {"result_status": "hit", "edge": 8.0, "confidence": 0.72, "quality_score": 0.81, "odds": -110},
            {"result_status": "hit", "edge": 7.0, "confidence": 0.70, "quality_score": 0.79, "odds": -110},
        ])
        assert _group_metrics(df)["hit_rate"] == 1.0

    def test_no_graded_returns_none_hit_rate(self):
        df = pd.DataFrame([{"result_status": "pending"}, {"result_status": "void"}])
        assert _group_metrics(df)["hit_rate"] is None

    def test_empty_df(self):
        m = _group_metrics(pd.DataFrame())
        assert m["total_picks"] == 0
        assert m["hit_rate"] is None

    def test_unit_roi_computed(self):
        df = pd.DataFrame([
            {"result_status": "hit", "odds": -110, "edge": 7.0, "confidence": 0.72, "quality_score": 0.80},
            {"result_status": "miss", "odds": -110, "edge": 6.0, "confidence": 0.68, "quality_score": 0.75},
        ])
        m = _group_metrics(df)
        assert m["unit_roi_per_pick"] is not None


# ---------------------------------------------------------------------------
# build_shadow_analysis
# ---------------------------------------------------------------------------

class TestBuildShadowAnalysis:

    def test_returns_observation_only(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        _write_context_csv(tmp_path, "2026-05-01")
        result, _ = build_shadow_analysis(["2026-05-01"], p, tmp_path)
        assert result["observation_only"] is True

    def test_overall_metrics_present(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        _write_context_csv(tmp_path, "2026-05-01")
        result, _ = build_shadow_analysis(["2026-05-01"], p, tmp_path)
        assert "overall" in result
        assert "total_picks" in result["overall"]
        assert "hit_rate" in result["overall"]

    def test_by_blowout_risk_present(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        _write_context_csv(tmp_path, "2026-05-01")
        result, _ = build_shadow_analysis(["2026-05-01"], p, tmp_path)
        assert "by_blowout_risk" in result
        assert isinstance(result["by_blowout_risk"], dict)

    def test_by_competitiveness_present(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        _write_context_csv(tmp_path, "2026-05-01")
        result, _ = build_shadow_analysis(["2026-05-01"], p, tmp_path)
        assert "by_expected_competitiveness" in result

    def test_missing_context_date_still_reports(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        # No context file written for 2026-05-02
        result, combined = build_shadow_analysis(["2026-05-02"], p, tmp_path)
        assert "2026-05-02" in result["dates_without_context"]
        assert result["overall"]["total_picks"] > 0

    def test_missing_picks_file_returns_safe_result(self, tmp_path):
        result = build_shadow_analysis(None, tmp_path / "nonexistent.csv", tmp_path)
        assert isinstance(result, dict)
        assert result.get("observation_only") is True

    def test_none_dates_uses_all_dates(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        result, combined = build_shadow_analysis(None, p, tmp_path)
        assert result["overall"]["total_picks"] > 0

    def test_context_joined_count_correct(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        _write_context_csv(tmp_path, "2026-05-01")
        result, _ = build_shadow_analysis(["2026-05-01"], p, tmp_path)
        assert result["context_joined_count"] >= 0
        total = result["context_joined_count"] + result["context_missing_count"]
        assert total == result["overall"]["total_picks"]

    def test_grouping_by_blowout_risk_sums_to_total(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        _write_context_csv(tmp_path, "2026-05-01")
        result, _ = build_shadow_analysis(["2026-05-01"], p, tmp_path)
        group_total = sum(v["total_picks"] for v in result["by_blowout_risk"].values())
        assert group_total == result["overall"]["total_picks"]

    def test_grouping_by_side_sums_to_total(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        _write_context_csv(tmp_path, "2026-05-01")
        result, _ = build_shadow_analysis(["2026-05-01"], p, tmp_path)
        group_total = sum(v["total_picks"] for v in result["by_side"].values())
        assert group_total == result["overall"]["total_picks"]


# ---------------------------------------------------------------------------
# write_shadow_outputs
# ---------------------------------------------------------------------------

class TestWriteShadowOutputs:

    def test_json_written(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        _write_context_csv(tmp_path, "2026-05-01")
        result = write_shadow_outputs(["2026-05-01"], p, tmp_path, "2026-05-01")
        assert result.get("json_path") is not None
        assert Path(result["json_path"]).exists()

    def test_csv_written_when_data_available(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        _write_context_csv(tmp_path, "2026-05-01")
        result = write_shadow_outputs(["2026-05-01"], p, tmp_path, "2026-05-01")
        assert result.get("csv_path") is not None
        assert Path(result["csv_path"]).exists()

    def test_csv_contains_power_rating_columns(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        _write_context_csv(tmp_path, "2026-05-01")
        result = write_shadow_outputs(["2026-05-01"], p, tmp_path, "2026-05-01")
        df = pd.read_csv(result["csv_path"])
        assert "blowout_risk" in df.columns
        assert "expected_competitiveness" in df.columns

    def test_missing_diagnostics_still_writes_json(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        result = write_shadow_outputs(["2026-05-01"], p, tmp_path, "2026-05-01")
        assert result.get("json_path") is not None

    def test_missing_picks_returns_json_not_csv(self, tmp_path):
        result = write_shadow_outputs(None, tmp_path / "none.csv", tmp_path, "2026-05-01")
        assert isinstance(result, dict)

    def test_observation_only_always_true(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        result = write_shadow_outputs(["2026-05-01"], p, tmp_path, "2026-05-01")
        assert result["observation_only"] is True

    def test_json_has_required_keys(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        _write_context_csv(tmp_path, "2026-05-01")
        result = write_shadow_outputs(["2026-05-01"], p, tmp_path, "2026-05-01")
        import json
        payload = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
        for key in ("observation_only", "overall", "by_blowout_risk", "by_expected_competitiveness"):
            assert key in payload, f"missing key: {key}"


# ---------------------------------------------------------------------------
# Safeguards: no scoring/selection/Kelly changes
# ---------------------------------------------------------------------------

class TestObservationOnlySafeguards:

    def test_original_picks_df_unchanged_after_join(self):
        picks = _make_picks()
        edge_before = list(picks["edge"])
        qs_before = list(picks["quality_score"])
        join_picks_with_context(picks, _make_context())
        assert list(picks["edge"]) == edge_before
        assert list(picks["quality_score"]) == qs_before

    def test_observation_only_constant_is_true(self):
        assert OBSERVATION_ONLY is True

    def test_build_analysis_does_not_write_files(self, tmp_path):
        p = _write_picks_csv(tmp_path)
        _write_context_csv(tmp_path, "2026-05-01")
        before = set(tmp_path.glob("*"))
        build_shadow_analysis(["2026-05-01"], p, tmp_path)
        after = set(tmp_path.glob("*"))
        assert before == after, "build_shadow_analysis should not write any files"
