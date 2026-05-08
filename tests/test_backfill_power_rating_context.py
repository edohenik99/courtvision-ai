"""Tests for scripts/backfill_power_rating_context.py.

Verifies:
- One date can be backfilled
- A date range can be backfilled
- Existing files are skipped unless --overwrite is used
- --dry-run writes nothing
- Backfill uses date < prediction_date (as_of_date safety)
- Original full_market_board CSV is unchanged
- Missing/empty boards are skipped safely
- Shadow analysis gains joined context when diagnostics files exist
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.backfill_power_rating_context import _load_board, backfill_power_rating_context


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _board_rows(n: int = 3) -> list[dict]:
    return [
        {
            "player_name": f"Player {i}",
            "team_abbr": "OKC",
            "opponent": "LAL",
            "home_away": "home",
            "market_type": "player_points",
            "selection_side": "over",
            "game_id": "G1",
            "edge": 0.07,
            "quality_score": 0.80,
            "kelly_fraction": 0.03,
            "stake_amount": 30.0,
        }
        for i in range(n)
    ]


def _write_board(runtime_root: Path, date: str, rows: list[dict] | None = None) -> Path:
    operator_dir = runtime_root / "operator"
    operator_dir.mkdir(parents=True, exist_ok=True)
    path = operator_dir / f"full_market_board_{date}.csv"
    pd.DataFrame(rows if rows is not None else _board_rows()).to_csv(path, index=False)
    return path


def _write_game_results(base: Path, rows: list[dict]) -> Path:
    p = base / "game_results.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def _result(date: str, home: str, away: str, hs: int, as_: int, gid: str) -> dict:
    return {
        "date": date,
        "home_team_id": home,
        "away_team_id": away,
        "home_score": hs,
        "away_score": as_,
        "game_id": gid,
        "home_team_name": "",
        "away_team_name": "",
    }


# ---------------------------------------------------------------------------
# _load_board
# ---------------------------------------------------------------------------

class TestLoadBoard:

    def test_valid_board_loaded(self, tmp_path):
        path = _write_board(tmp_path / "runtime", "2026-05-01")
        assert _load_board(path) is not None

    def test_missing_file_returns_none(self, tmp_path):
        assert _load_board(tmp_path / "nonexistent.csv") is None

    def test_empty_file_returns_none(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text("")
        assert _load_board(p) is None

    def test_missing_required_columns_returns_none(self, tmp_path):
        p = tmp_path / "board.csv"
        pd.DataFrame([{"player_name": "P1", "edge": 0.07}]).to_csv(p, index=False)
        assert _load_board(p) is None

    def test_partial_required_columns_returns_none(self, tmp_path):
        p = tmp_path / "board.csv"
        pd.DataFrame([{"team_abbr": "OKC", "opponent": "LAL"}]).to_csv(p, index=False)
        assert _load_board(p) is None

    def test_all_required_columns_returns_df(self, tmp_path):
        p = tmp_path / "board.csv"
        pd.DataFrame([{"team_abbr": "OKC", "opponent": "LAL", "home_away": "home"}]).to_csv(p, index=False)
        result = _load_board(p)
        assert result is not None
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Single date backfill
# ---------------------------------------------------------------------------

class TestSingleDateBackfill:

    def test_single_date_written(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        _write_board(runtime_root, "2026-05-01")
        result = backfill_power_rating_context(runtime_root=runtime_root, dates=["2026-05-01"])
        assert "2026-05-01" in result["dates_written"]

    def test_diagnostics_file_created(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        _write_board(runtime_root, "2026-05-01")
        backfill_power_rating_context(runtime_root=runtime_root, dates=["2026-05-01"])
        assert (runtime_root / "diagnostics" / "power_rating_context_2026-05-01.csv").exists()

    def test_diagnostics_csv_has_power_rating_columns(self, tmp_path):
        from courtvision.context.game_strength import POWER_RATING_CONTEXT_COLUMNS
        runtime_root = tmp_path / "runtime"
        _write_board(runtime_root, "2026-05-01")
        backfill_power_rating_context(runtime_root=runtime_root, dates=["2026-05-01"])
        df = pd.read_csv(runtime_root / "diagnostics" / "power_rating_context_2026-05-01.csv")
        for col in POWER_RATING_CONTEXT_COLUMNS:
            assert col in df.columns, f"missing column: {col}"

    def test_observation_only_always_true(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        _write_board(runtime_root, "2026-05-01")
        result = backfill_power_rating_context(runtime_root=runtime_root, dates=["2026-05-01"])
        assert result["observation_only"] is True

    def test_rows_enriched_matches_board_size(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        _write_board(runtime_root, "2026-05-01", rows=_board_rows(n=5))
        result = backfill_power_rating_context(runtime_root=runtime_root, dates=["2026-05-01"])
        assert result["rows_enriched"] == 5

    def test_date_not_in_boards_lands_in_not_found(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        (runtime_root / "operator").mkdir(parents=True, exist_ok=True)
        result = backfill_power_rating_context(runtime_root=runtime_root, dates=["2026-05-01"])
        assert "2026-05-01" in result["dates_not_found"]


# ---------------------------------------------------------------------------
# Date range backfill
# ---------------------------------------------------------------------------

class TestDateRangeBackfill:

    def test_date_range_backfills_only_specified_window(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        for d in ["2026-05-01", "2026-05-02", "2026-05-03"]:
            _write_board(runtime_root, d)
        result = backfill_power_rating_context(
            runtime_root=runtime_root,
            from_date="2026-05-01",
            to_date="2026-05-02",
        )
        assert set(result["dates_written"]) == {"2026-05-01", "2026-05-02"}
        assert "2026-05-03" not in result["dates_written"]

    def test_all_dates_backfilled_when_no_filter(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        for d in ["2026-05-01", "2026-05-02", "2026-05-03"]:
            _write_board(runtime_root, d)
        result = backfill_power_rating_context(runtime_root=runtime_root)
        assert set(result["dates_written"]) == {"2026-05-01", "2026-05-02", "2026-05-03"}

    def test_from_date_only_filters_lower_bound(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        for d in ["2026-04-30", "2026-05-01", "2026-05-02"]:
            _write_board(runtime_root, d)
        result = backfill_power_rating_context(runtime_root=runtime_root, from_date="2026-05-01")
        assert "2026-04-30" not in result["dates_written"]
        assert "2026-05-01" in result["dates_written"]
        assert "2026-05-02" in result["dates_written"]

    def test_to_date_only_filters_upper_bound(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        for d in ["2026-05-01", "2026-05-02", "2026-05-03"]:
            _write_board(runtime_root, d)
        result = backfill_power_rating_context(runtime_root=runtime_root, to_date="2026-05-02")
        assert "2026-05-03" not in result["dates_written"]

    def test_result_dates_found_reflects_all_boards(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        for d in ["2026-05-01", "2026-05-02", "2026-05-03"]:
            _write_board(runtime_root, d)
        result = backfill_power_rating_context(
            runtime_root=runtime_root,
            from_date="2026-05-01",
            to_date="2026-05-01",
        )
        assert len(result["dates_found"]) == 3
        assert len(result["dates_selected"]) == 1


# ---------------------------------------------------------------------------
# Skip / overwrite logic
# ---------------------------------------------------------------------------

class TestSkipAndOverwrite:

    def test_existing_file_skipped_without_overwrite(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        _write_board(runtime_root, "2026-05-01")
        backfill_power_rating_context(runtime_root=runtime_root, dates=["2026-05-01"])
        result = backfill_power_rating_context(runtime_root=runtime_root, dates=["2026-05-01"])
        assert "2026-05-01" in result["dates_skipped_no_overwrite"]
        assert "2026-05-01" not in result["dates_written"]

    def test_existing_file_rewritten_with_overwrite(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        _write_board(runtime_root, "2026-05-01")
        backfill_power_rating_context(runtime_root=runtime_root, dates=["2026-05-01"])
        result = backfill_power_rating_context(
            runtime_root=runtime_root, dates=["2026-05-01"], overwrite=True
        )
        assert "2026-05-01" in result["dates_written"]
        assert "2026-05-01" not in result["dates_skipped_no_overwrite"]

    def test_empty_board_skipped_safely(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        op = runtime_root / "operator"
        op.mkdir(parents=True, exist_ok=True)
        (op / "full_market_board_2026-05-01.csv").write_text("")
        result = backfill_power_rating_context(runtime_root=runtime_root, dates=["2026-05-01"])
        assert "2026-05-01" in result["dates_skipped"]
        assert "2026-05-01" not in result["dates_written"]

    def test_board_missing_required_columns_skipped(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        op = runtime_root / "operator"
        op.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"player_name": "P1", "edge": 0.07}]).to_csv(
            op / "full_market_board_2026-05-01.csv", index=False
        )
        result = backfill_power_rating_context(runtime_root=runtime_root, dates=["2026-05-01"])
        assert "2026-05-01" in result["dates_skipped"]

    def test_no_operator_dir_returns_empty_result(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)
        result = backfill_power_rating_context(runtime_root=runtime_root)
        assert result["dates_found"] == []
        assert result["dates_written"] == []


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

class TestDryRun:

    def test_dry_run_writes_no_files(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        _write_board(runtime_root, "2026-05-01")
        backfill_power_rating_context(
            runtime_root=runtime_root, dates=["2026-05-01"], dry_run=True
        )
        assert not (runtime_root / "diagnostics" / "power_rating_context_2026-05-01.csv").exists()

    def test_dry_run_reports_would_write(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        _write_board(runtime_root, "2026-05-01")
        result = backfill_power_rating_context(
            runtime_root=runtime_root, dates=["2026-05-01"], dry_run=True
        )
        assert "2026-05-01" in result["dates_would_write"]
        assert "2026-05-01" not in result["dates_written"]

    def test_dry_run_still_counts_rows_enriched(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        _write_board(runtime_root, "2026-05-01", rows=_board_rows(n=4))
        result = backfill_power_rating_context(
            runtime_root=runtime_root, dates=["2026-05-01"], dry_run=True
        )
        assert result["rows_enriched"] == 4

    def test_dry_run_result_flag_is_true(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        _write_board(runtime_root, "2026-05-01")
        result = backfill_power_rating_context(
            runtime_root=runtime_root, dates=["2026-05-01"], dry_run=True
        )
        assert result["dry_run"] is True


# ---------------------------------------------------------------------------
# as_of_date safety — backfill uses games before prediction_date only
# ---------------------------------------------------------------------------

class TestAsOfDateSafety:

    def _setup(self, tmp_path: Path):
        runtime_root = tmp_path / "runtime"
        game_results_path = _write_game_results(tmp_path / "history", [
            _result("2026-05-01", "OKC", "LAL", 110, 100, "G1"),
            _result("2026-05-02", "OKC", "LAL", 105, 108, "G2"),
            _result("2026-05-03", "OKC", "LAL", 115, 109, "G3"),  # same day as prediction
        ])
        _write_board(runtime_root, "2026-05-03", rows=[
            {"player_name": "P1", "team_abbr": "OKC", "opponent": "LAL",
             "home_away": "home", "market_type": "player_points",
             "selection_side": "over", "game_id": "G3",
             "edge": 0.07, "quality_score": 0.80, "kelly_fraction": 0.03, "stake_amount": 30.0},
        ])
        return runtime_root, game_results_path

    def test_as_of_date_excludes_same_day_game(self, tmp_path):
        from courtvision.ratings.power_ratings_store import (
            build_current_power_ratings,
            get_latest_team_power_ratings,
            load_game_results,
        )
        _, game_results_path = self._setup(tmp_path)

        # Ratings excluding 2026-05-03 must differ from ratings including it
        r_before = get_latest_team_power_ratings(game_results_path, as_of_date="2026-05-03")
        r_after = get_latest_team_power_ratings(game_results_path, as_of_date="2026-05-04")
        assert r_before != r_after

    def test_backfill_uses_only_prior_games_for_ratings(self, tmp_path):
        from courtvision.ratings.power_ratings_store import (
            build_current_power_ratings,
            get_latest_team_power_ratings,
            load_game_results,
        )
        runtime_root, game_results_path = self._setup(tmp_path)

        backfill_power_rating_context(
            runtime_root=runtime_root,
            game_results_path=game_results_path,
            dates=["2026-05-03"],
        )

        # The ratings produced for 2026-05-03 should equal as_of 2026-05-03
        games_df = load_game_results(game_results_path)
        expected = build_current_power_ratings(games_df[games_df["date"] < "2026-05-03"])
        actual = get_latest_team_power_ratings(game_results_path, as_of_date="2026-05-03")
        assert actual == expected

    def test_as_of_before_all_games_produces_missing_context(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        game_results_path = _write_game_results(tmp_path / "history", [
            _result("2026-05-05", "OKC", "LAL", 110, 100, "G1"),
        ])
        _write_board(runtime_root, "2026-05-03")

        result = backfill_power_rating_context(
            runtime_root=runtime_root,
            game_results_path=game_results_path,
            dates=["2026-05-03"],
        )
        # No games before 2026-05-03, so ratings are empty → all context missing
        assert result["context_missing_count"] == result["rows_enriched"]
        assert result["context_applied_count"] == 0


# ---------------------------------------------------------------------------
# Original board CSV unchanged
# ---------------------------------------------------------------------------

class TestOriginalBoardUnchanged:

    def test_original_csv_content_identical_after_backfill(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        board_path = _write_board(runtime_root, "2026-05-01", rows=_board_rows(n=3))
        original = board_path.read_text(encoding="utf-8")

        backfill_power_rating_context(runtime_root=runtime_root, dates=["2026-05-01"])

        assert board_path.read_text(encoding="utf-8") == original

    def test_original_csv_columns_unchanged_after_backfill(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        board_path = _write_board(runtime_root, "2026-05-01")
        before_cols = set(pd.read_csv(board_path).columns)

        backfill_power_rating_context(runtime_root=runtime_root, dates=["2026-05-01"])

        after_cols = set(pd.read_csv(board_path).columns)
        assert after_cols == before_cols

    def test_edge_and_stake_in_original_board_unchanged(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        board_path = _write_board(runtime_root, "2026-05-01", rows=_board_rows(n=2))
        df_before = pd.read_csv(board_path)
        edge_before = list(df_before["edge"])
        stake_before = list(df_before["stake_amount"])

        backfill_power_rating_context(runtime_root=runtime_root, dates=["2026-05-01"])

        df_after = pd.read_csv(board_path)
        assert list(df_after["edge"]) == edge_before
        assert list(df_after["stake_amount"]) == stake_before

    def test_original_board_row_count_unchanged(self, tmp_path):
        runtime_root = tmp_path / "runtime"
        board_path = _write_board(runtime_root, "2026-05-01", rows=_board_rows(n=4))
        n_before = len(pd.read_csv(board_path))

        backfill_power_rating_context(runtime_root=runtime_root, dates=["2026-05-01"])

        assert len(pd.read_csv(board_path)) == n_before


# ---------------------------------------------------------------------------
# Shadow analysis integration — gains context after backfill
# ---------------------------------------------------------------------------

class TestShadowAnalysisIntegration:

    def _setup_pick_history(self, tmp_path: Path) -> Path:
        p = tmp_path / "pick_history.csv"
        pd.DataFrame([
            {
                "prediction_date": "2026-05-01", "run_timestamp": "2026-05-01T12:00:00Z",
                "player_name": "Player 0", "team": "OKC", "opponent": "LAL",
                "market": "player_points", "selection": "over", "line": 24.5,
                "edge": 8.0, "confidence": 0.72, "quality_score": 0.81,
                "result_status": "hit", "odds": -110, "game_id": "G1",
            },
        ]).to_csv(p, index=False)
        return p

    def test_shadow_analysis_gains_context_after_backfill(self, tmp_path):
        from courtvision.reporting.power_rating_shadow import build_shadow_analysis

        runtime_root = tmp_path / "runtime"
        diagnostics_dir = runtime_root / "diagnostics"
        pick_history_path = self._setup_pick_history(tmp_path)

        _write_board(runtime_root, "2026-05-01", rows=_board_rows(n=1))

        # Before backfill — no context file exists
        before_out = build_shadow_analysis(["2026-05-01"], pick_history_path, diagnostics_dir)
        result_before = before_out[0] if isinstance(before_out, tuple) else before_out
        joined_before = result_before.get("context_joined_count", 0)

        # Provide game results so OKC/LAL get real ratings
        game_results_path = _write_game_results(tmp_path / "history", [
            _result("2026-04-30", "OKC", "LAL", 110, 100, "G0"),
        ])

        backfill_power_rating_context(
            runtime_root=runtime_root,
            game_results_path=game_results_path,
            dates=["2026-05-01"],
        )

        # After backfill — context file exists and picks join to it
        after_out = build_shadow_analysis(["2026-05-01"], pick_history_path, diagnostics_dir)
        result_after = after_out[0] if isinstance(after_out, tuple) else after_out
        joined_after = result_after.get("context_joined_count", 0)

        assert joined_after > joined_before

    def test_no_diagnostics_means_zero_context_joined(self, tmp_path):
        from courtvision.reporting.power_rating_shadow import build_shadow_analysis

        diagnostics_dir = tmp_path / "diagnostics"
        pick_history_path = self._setup_pick_history(tmp_path)

        out = build_shadow_analysis(["2026-05-01"], pick_history_path, diagnostics_dir)
        result = out[0] if isinstance(out, tuple) else out
        assert result.get("context_joined_count", 0) == 0

    def test_backfill_creates_file_shadow_analysis_can_load(self, tmp_path):
        from courtvision.reporting.power_rating_shadow import load_power_rating_context

        runtime_root = tmp_path / "runtime"
        diagnostics_dir = runtime_root / "diagnostics"
        game_results_path = _write_game_results(tmp_path / "history", [
            _result("2026-04-30", "OKC", "LAL", 110, 100, "G0"),
        ])
        _write_board(runtime_root, "2026-05-01")

        assert load_power_rating_context(diagnostics_dir, "2026-05-01") is None

        backfill_power_rating_context(
            runtime_root=runtime_root,
            game_results_path=game_results_path,
            dates=["2026-05-01"],
        )

        ctx = load_power_rating_context(diagnostics_dir, "2026-05-01")
        assert ctx is not None
        assert len(ctx) > 0
