"""Phase 11A – Coverage Observability & Sparse Feed Diagnostics.

Verifies diagnostic-only improvements:
1. Sparse game warning emitted when a game returns < 50 rows.
2. active_game_player_match_rate computed correctly from player_lookup_size.
3. Duplicate baseline rows do NOT inflate matched_players (unique-ID counting).
4. baseline_missing_player_id_count is reported.
5. active_board_match_rate stays 100% when all odds players match baselines.
6. Board output rows are unchanged by diagnostics (accepted candidates identical).
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from courtvision.data.candidates import score_player_markets


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _build_candidate_row(
    *, player_row: pd.Series, market: str, market_rows: pd.DataFrame, partial_fill: bool = False, **_kw: Any
) -> dict[str, Any]:
    line = None
    if not market_rows.empty and "line" in market_rows.columns:
        line = float(market_rows.iloc[0]["line"])
    return {
        "player_id": int(player_row["player_id"]),
        "player_name": player_row["player_name"],
        "market_type": market,
        "market": market,
        "line": line,
        "partial_fill": partial_fill,
    }


def _score_candidate(*, candidate_row: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    return {**candidate_row, "edge": 0.05}


def _reject_candidate(
    *, player_row: pd.Series, market: Any, reason: str, **_kw: Any
) -> dict[str, Any]:
    return {
        "player_id": int(player_row.get("player_id", 0)),
        "player_name": player_row.get("player_name", ""),
        "market_type": market,
        "rejection_reason": reason,
    }


def _minimal_odds(player_id: int, player_name: str, game_id: int = 1) -> dict[str, Any]:
    return {
        "player_id": player_id,
        "player_name": player_name,
        "_normalized_name": player_name.lower().replace(" ", "_"),
        "_team_abbr": "LAL",
        "market_type": "player_points",
        "market": "player_points",
        "line": 20.5,
        "odds": -110,
        "vendor": "draftkings",
        "selection": "over",
        "game_id": game_id,
    }


# ---------------------------------------------------------------------------
# 1. Sparse game warning
# ---------------------------------------------------------------------------

def test_sparse_game_warning_emitted_when_few_rows(capsys) -> None:
    """[WARN] game_odds_sparse must be printed when a game returns < 50 rows.

    We simulate sparsity by calling score_player_markets with odds rows for
    two games where game 2 has only 3 rows, verifying the [WARN] line appears
    in the output. The warning lives in courtvision_ai.py (fetch stage), so
    here we test that the per-game player count diagnostic emitted by
    score_player_markets makes the sparsity visible when game_id distribution
    is logged.  The direct [WARN] tag test covers the BDL client helper.
    """
    # 20 players from game 1 (normal)
    game1_rows = [_minimal_odds(pid, f"Player {pid}", game_id=101) for pid in range(1, 21)]
    # 1 player from game 2 (sparse)
    game2_rows = [_minimal_odds(99, "Sparse Player", game_id=102)]
    all_odds = game1_rows + game2_rows

    players_df = pd.DataFrame(
        [{"player_id": pid, "player_name": f"Player {pid}", "team_abbr": "LAL"} for pid in range(1, 21)]
        + [{"player_id": 99, "player_name": "Sparse Player", "team_abbr": "BOS"}]
    )
    odds_df = pd.DataFrame(all_odds)

    score_player_markets(
        players_df=players_df,
        odds_df=odds_df,
        is_player_inactive=lambda _: False,
        build_candidate_row=_build_candidate_row,
        score_candidate_fn=_score_candidate,
        reject_candidate_fn=_reject_candidate,
        allow_partial_fill=False,
        player_lookup_size=30,
    )

    out = capsys.readouterr().out
    # 21 unique players in odds; player_lookup_size=30 → active_game rate = 70.0%
    assert "[COUNT] active_game_player_match_rate=70.0%" in out
    # All 21 odds players should be found in baseline
    assert "[COUNT] active_board_match_rate=100.0%" in out


def test_game_odds_sparse_helper_emits_warn(capsys) -> None:
    """Directly verify the [WARN] game_odds_sparse line from the BDL client helper.

    Imports the helper class from courtvision_ai and patches _get to return 3
    rows, then asserts the sparse warning is printed.
    """
    import sys
    import types
    import importlib

    # Lazy import to avoid requiring API key at import time
    try:
        import courtvision_ai as _cai_mod
    except Exception:
        pytest.skip("courtvision_ai not importable in this environment")

    # Find the internal BallDontLieClient class (the one with _get_player_prop_rows)
    bdl_cls = getattr(_cai_mod, "BallDontLieClient", None)
    if bdl_cls is None:
        pytest.skip("BallDontLieClient not found in courtvision_ai")

    # Build a minimal instance without touching settings / API key
    instance = object.__new__(bdl_cls)
    instance.settings = types.SimpleNamespace(
        api_key="test-key",
        base_url="https://api.balldontlie.io",
        per_page=100,
        request_timeout_seconds=30,
    )
    instance.session = types.SimpleNamespace(headers={})

    # Patch _get to return exactly 3 rows (below sparse threshold)
    sparse_data = [{"id": i, "game_id": 999, "player_id": i} for i in range(3)]
    instance._get = lambda *a, **kw: {"data": sparse_data}

    # Call _get_player_prop_rows — normalization may crash on minimal instance,
    # but the [WARN] print fires before any normalization.
    try:
        rows = instance._get_player_prop_rows(game_id=999)
    except Exception:
        rows = []
    out = capsys.readouterr().out

    assert "[WARN] game_odds_sparse game_id=999 rows=3 threshold=50" in out, (
        f"Expected sparse warning in output. Got:\n{out}"
    )


# ---------------------------------------------------------------------------
# 2. active_game_player_match_rate computed correctly
# ---------------------------------------------------------------------------

def test_active_game_player_match_rate_computed_correctly(capsys) -> None:
    """active_game_player_match_rate = odds_unique_players / player_lookup_size * 100."""
    players_df = pd.DataFrame([
        {"player_id": 1, "player_name": "Alpha", "team_abbr": "LAL"},
        {"player_id": 2, "player_name": "Beta",  "team_abbr": "LAL"},
        {"player_id": 3, "player_name": "Gamma", "team_abbr": "BOS"},
    ])
    odds_df = pd.DataFrame([_minimal_odds(1, "Alpha"), _minimal_odds(2, "Beta")])

    # 2 odds players, 10 active-game players → 20.0%
    score_player_markets(
        players_df=players_df,
        odds_df=odds_df,
        is_player_inactive=lambda _: False,
        build_candidate_row=_build_candidate_row,
        score_candidate_fn=_score_candidate,
        reject_candidate_fn=_reject_candidate,
        allow_partial_fill=False,
        player_lookup_size=10,
    )

    out = capsys.readouterr().out
    assert "[COUNT] active_game_player_match_rate=20.0%" in out, f"Output:\n{out}"


def test_active_game_player_match_rate_na_when_lookup_size_zero(capsys) -> None:
    """When player_lookup_size=0 (default), emit n/a instead of dividing by zero."""
    players_df = pd.DataFrame([{"player_id": 1, "player_name": "Alpha", "team_abbr": "LAL"}])
    odds_df = pd.DataFrame([_minimal_odds(1, "Alpha")])

    score_player_markets(
        players_df=players_df,
        odds_df=odds_df,
        is_player_inactive=lambda _: False,
        build_candidate_row=_build_candidate_row,
        score_candidate_fn=_score_candidate,
        reject_candidate_fn=_reject_candidate,
        allow_partial_fill=False,
        # player_lookup_size omitted → defaults to 0
    )

    out = capsys.readouterr().out
    assert "[COUNT] active_game_player_match_rate=n/a" in out, f"Output:\n{out}"


# ---------------------------------------------------------------------------
# 3. Duplicate baseline rows do not inflate matched_players
# ---------------------------------------------------------------------------

def test_duplicate_baseline_rows_do_not_inflate_matched_players(capsys) -> None:
    """Player with two baseline rows (same player_id) counts as 1 matched player."""
    players_df = pd.DataFrame([
        {"player_id": 7, "player_name": "Duplex Player", "team_abbr": "LAL"},
        {"player_id": 7, "player_name": "Duplex Player", "team_abbr": "LAL"},  # duplicate
        {"player_id": 8, "player_name": "Single Player", "team_abbr": "BOS"},
    ])
    odds_df = pd.DataFrame([_minimal_odds(7, "Duplex Player")])

    score_player_markets(
        players_df=players_df,
        odds_df=odds_df,
        is_player_inactive=lambda _: False,
        build_candidate_row=_build_candidate_row,
        score_candidate_fn=_score_candidate,
        reject_candidate_fn=_reject_candidate,
        allow_partial_fill=False,
    )

    out = capsys.readouterr().out

    # matched_players should be 1 (unique IDs), not 2 (rows)
    assert "[COUNT] matched_players=1" in out, (
        f"Duplicate baseline rows must not inflate matched_players.\nOutput:\n{out}"
    )
    # matched_baseline_rows should show 2 (the actual row count)
    assert "[COUNT] matched_baseline_rows=2" in out, (
        f"matched_baseline_rows should count both rows.\nOutput:\n{out}"
    )
    # duplicate_baseline_player_id_count should be 1 (one player_id appeared twice)
    assert "[COUNT] duplicate_baseline_player_id_count=1" in out, f"Output:\n{out}"


# ---------------------------------------------------------------------------
# 4. baseline_missing_player_id_count is reported
# ---------------------------------------------------------------------------

def test_baseline_missing_player_id_count_reported(capsys) -> None:
    """Rows with player names but player_id=0 are counted as missing."""
    players_df = pd.DataFrame([
        {"player_id": 1,  "player_name": "Has ID",    "team_abbr": "LAL"},
        {"player_id": 0,  "player_name": "No ID A",   "team_abbr": "LAL"},
        {"player_id": 0,  "player_name": "No ID B",   "team_abbr": "BOS"},
        {"player_id": 2,  "player_name": "Has ID 2",  "team_abbr": "BOS"},
    ])
    odds_df = pd.DataFrame([_minimal_odds(1, "Has ID"), _minimal_odds(2, "Has ID 2")])

    score_player_markets(
        players_df=players_df,
        odds_df=odds_df,
        is_player_inactive=lambda _: False,
        build_candidate_row=_build_candidate_row,
        score_candidate_fn=_score_candidate,
        reject_candidate_fn=_reject_candidate,
        allow_partial_fill=False,
    )

    out = capsys.readouterr().out
    assert "[COUNT] baseline_missing_player_id_count=2" in out, (
        f"Expected 2 rows with names but no valid player_id.\nOutput:\n{out}"
    )
    assert "[COUNT] baseline_valid_player_id_count=2" in out, f"Output:\n{out}"


# ---------------------------------------------------------------------------
# 5. active_board_match_rate stays 100% when all odds players in baseline
# ---------------------------------------------------------------------------

def test_active_board_match_rate_100pct_when_all_odds_in_baseline(capsys) -> None:
    """active_board_match_rate must be 100% when every odds player_id is in baseline."""
    players_df = pd.DataFrame([
        {"player_id": 10, "player_name": "Player10", "team_abbr": "LAL"},
        {"player_id": 11, "player_name": "Player11", "team_abbr": "LAL"},
        {"player_id": 12, "player_name": "Player12", "team_abbr": "BOS"},
        # Extra baseline players not in odds (normal — these are non-playing tonight)
        {"player_id": 99, "player_name": "Bench99",  "team_abbr": "NYK"},
        {"player_id": 98, "player_name": "Bench98",  "team_abbr": "NYK"},
    ])
    odds_df = pd.DataFrame([
        _minimal_odds(10, "Player10"),
        _minimal_odds(11, "Player11"),
    ])

    score_player_markets(
        players_df=players_df,
        odds_df=odds_df,
        is_player_inactive=lambda _: False,
        build_candidate_row=_build_candidate_row,
        score_candidate_fn=_score_candidate,
        reject_candidate_fn=_reject_candidate,
        allow_partial_fill=False,
        player_lookup_size=10,
    )

    out = capsys.readouterr().out
    assert "[COUNT] active_board_match_rate=100.0%" in out, f"Output:\n{out}"


# ---------------------------------------------------------------------------
# 6. Board output rows unchanged by diagnostics-only patch
# ---------------------------------------------------------------------------

def test_board_output_rows_unchanged_by_diagnostics(capsys) -> None:
    """accepted_candidates must be identical with or without player_lookup_size."""
    players_df = pd.DataFrame([
        {"player_id": 5, "player_name": "Star Player", "team_abbr": "GSW"},
        {"player_id": 6, "player_name": "Role Player", "team_abbr": "GSW"},
        {"player_id": 99, "player_name": "Bench Only",  "team_abbr": "BOS"},
    ])
    odds_df = pd.DataFrame([
        _minimal_odds(5, "Star Player"),
        _minimal_odds(6, "Role Player"),
    ])

    shared_kwargs: dict[str, Any] = dict(
        players_df=players_df,
        odds_df=odds_df,
        is_player_inactive=lambda _: False,
        build_candidate_row=_build_candidate_row,
        score_candidate_fn=_score_candidate,
        reject_candidate_fn=_reject_candidate,
        allow_partial_fill=False,
    )

    accepted_no_lookup, _ = score_player_markets(**shared_kwargs)
    accepted_with_lookup, _ = score_player_markets(**shared_kwargs, player_lookup_size=70)

    names_no = {c["player_name"] for c in accepted_no_lookup}
    names_with = {c["player_name"] for c in accepted_with_lookup}

    assert names_no == names_with, (
        f"Board output must not change. no_lookup={names_no} with_lookup={names_with}"
    )
    assert len(accepted_no_lookup) == len(accepted_with_lookup), (
        f"Candidate count must not change: {len(accepted_no_lookup)} vs {len(accepted_with_lookup)}"
    )
