"""Regression test: low baseline-coverage runs must NOT discard already-
accepted candidates.

Background
----------
With BallDontLie returning props for ~10k rows but only ~125 baseline
players matching odds (a normal slate where many of the 600+ trained
baseline players are not playing), the candidate-stage previously hit a
`match_rate < 50%` kill-switch and returned `[], []`. That collapsed the
whole pipeline to zero picks even though 125 valid candidates had already
been scored cleanly.

The kill-switch was a stale guard from when the upstream odds normalizer
was returning unresolved rows. It is *not* the elite-safety layer; that
runs later. Removing the guard preserves valid picks; downstream elite
admission is the authoritative quality filter.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from courtvision.data.candidates import score_player_markets


def _build_candidate_row(
    *, player_row: pd.Series, market: str, market_rows: pd.DataFrame, partial_fill: bool = False, **_kwargs: Any
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


def _score_candidate(*, candidate_row: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
    # Trivial scorer: always accept the candidate.
    return {**candidate_row, "edge": 0.05}


def _reject_candidate(
    *, player_row: pd.Series, market: Any, reason: str, **_kwargs: Any
) -> dict[str, Any]:
    return {
        "player_id": int(player_row["player_id"]),
        "player_name": player_row["player_name"],
        "market_type": market,
        "rejection_reason": reason,
    }


def test_low_match_rate_does_not_discard_accepted_candidates(
    monkeypatch,
    capsys,
) -> None:
    """If only 1/4 baseline players have odds (25%, well below the old
    50% threshold), the function must still return the matched player's
    accepted candidate instead of returning empty lists."""
    players_df = pd.DataFrame(
        [
            {"player_id": 1, "player_name": "LeBron James", "team_abbr": "LAL"},
            {"player_id": 2, "player_name": "Ghost Player A", "team_abbr": "LAL"},
            {"player_id": 3, "player_name": "Ghost Player B", "team_abbr": "BOS"},
            {"player_id": 4, "player_name": "Ghost Player C", "team_abbr": "BOS"},
        ]
    )

    # Only one of the four baseline players has any odds tonight => 25%
    # match rate, which used to trigger the kill-switch.
    odds_df = pd.DataFrame(
        [
            {
                "player_id": 1,
                "player_name": "LeBron James",
                "_normalized_name": "lebron_james",
                "_team_abbr": "LAL",
                "market_type": "player_points",
                "market": "player_points",
                "line": 25.5,
                "odds": -110,
                "vendor": "draftkings",
                "selection": "over",
            }
        ]
    )

    accepted, rejected = score_player_markets(
        players_df=players_df,
        odds_df=odds_df,
        is_player_inactive=lambda name: False,
        build_candidate_row=_build_candidate_row,
        score_candidate_fn=_score_candidate,
        reject_candidate_fn=_reject_candidate,
        allow_partial_fill=False,
    )

    accepted_names = {c.get("player_name") for c in accepted}
    assert "LeBron James" in accepted_names, (
        "Low baseline-coverage must not discard already-accepted candidates. "
        f"accepted={accepted!r} rejected={rejected!r}"
    )
    output = capsys.readouterr().out
    assert "[COUNT] odds_unique_players=1" in output
    assert "[COUNT] odds_baseline_matched_players=1" in output
    assert "[COUNT] active_board_match_rate=100.0%" in output
    assert "[COUNT] baseline_universe_player_count=4" in output
    assert "[WARNING] Low baseline coverage" not in output
    assert "[WARNING] Low active-board baseline match" not in output
