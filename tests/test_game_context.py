from __future__ import annotations

import json

import pandas as pd

from courtvision.context.game_context import apply_game_context, write_game_context_outputs


def test_game_context_attaches_passive_fields_without_changing_scores(tmp_path) -> None:
    candidates = pd.DataFrame(
        [
            {
                "player_name": "Jalen Green",
                "team": "PHX",
                "team_abbr": "PHX",
                "game_id": 10,
                "projection": 22.5,
                "confidence": 0.8,
                "quality_score": 99.0,
            }
        ]
    )
    games = pd.DataFrame(
        [
            {
                "game_id": 10,
                "home_team_abbr": "PHX",
                "visitor_team_abbr": "DEN",
                "postseason": True,
            }
        ]
    )
    team_baselines = pd.DataFrame(
        [
            {
                "team_abbr": "PHX",
                "team_pace": 101.2,
                "team_def_rating": 112.1,
                "team_off_rating": 116.3,
                "rest_days": 1,
            },
            {
                "team_abbr": "DEN",
                "team_pace": 98.4,
                "team_def_rating": 110.2,
                "team_off_rating": 118.8,
                "rest_days": 0,
            },
        ]
    )
    odds = pd.DataFrame(
        [
            {"game_id": 10, "team": "PHX", "market_type": "team_total", "line": 114.5},
            {"game_id": 10, "market_type": "game_total", "line": 228.0},
            {"game_id": 10, "team": "PHX", "market_type": "spread", "line": -3.5},
        ]
    )

    out, diagnostics = apply_game_context(
        candidates,
        games=games,
        team_baselines=team_baselines,
        odds=odds,
    )

    row = out.iloc[0]
    assert row["opponent"] == "DEN"
    assert row["home_away"] == "home"
    assert bool(row["postseason"]) is True
    assert row["team_pace"] == 101.2
    assert row["opponent_pace"] == 98.4
    assert row["team_def_rating"] == 112.1
    assert row["opponent_def_rating"] == 110.2
    assert row["team_off_rating"] == 116.3
    assert row["opponent_off_rating"] == 118.8
    assert row["rest_days"] == 1.0
    assert row["opponent_rest_days"] == 0.0
    assert bool(row["is_back_to_back"]) is False
    assert bool(row["opponent_is_back_to_back"]) is True
    assert row["implied_team_total"] == 114.5
    assert row["game_total"] == 228.0
    assert row["spread"] == -3.5
    assert row["projection"] == 22.5
    assert row["confidence"] == 0.8
    assert row["quality_score"] == 99.0
    assert diagnostics["rows"] == 1
    assert diagnostics["candidates_with_opponent"] == 1
    assert diagnostics["candidates_with_postseason"] == 1
    assert diagnostics["candidates_with_rest_days"] == 1
    assert diagnostics["candidates_with_def_rating"] == 1
    assert diagnostics["candidates_with_pace"] == 1

    json_path, report_path, payload = write_game_context_outputs(
        prediction_date="2026-04-28",
        runtime_root=tmp_path / "runtime",
        diagnostics=diagnostics,
        candidates=out,
    )
    assert json_path.name == "game_context_2026-04-28.json"
    assert report_path.name == "game_context_report_2026-04-28.txt"
    assert payload["passive_mode"] is True
    assert json.loads(json_path.read_text(encoding="utf-8"))["projection_changed"] is False


def test_game_context_derives_candidate_game_id_from_odds_without_selection_input() -> None:
    candidates = pd.DataFrame(
        [
            {
                "player_name": "VJ Edgecombe",
                "team": "PHI",
                "team_abbr": "PHI",
                "market_type": "player_points",
                "projection": 16.3,
                "confidence": 0.8,
            }
        ]
    )
    games = pd.DataFrame(
        [
            {
                "id": 21681988,
                "home_team.abbreviation": "PHI",
                "visitor_team.abbreviation": "BOS",
                "postseason": True,
            }
        ]
    )
    odds = pd.DataFrame(
        [
            {
                "game_id": 21681988,
                "player_name": "VJ Edgecombe",
                "_team_abbr": "PHI",
                "market_type": "player_points",
                "line": 12.5,
            }
        ]
    )

    out, diagnostics = apply_game_context(
        candidates,
        games=games,
        team_baselines=pd.DataFrame(),
        odds=odds,
    )

    assert int(out.loc[0, "game_id"]) == 21681988
    assert out.loc[0, "opponent"] == "BOS"
    assert out.loc[0, "home_away"] == "home"
    assert bool(out.loc[0, "postseason"]) is True
    assert diagnostics["candidates_with_opponent"] == 1
    assert diagnostics["candidates_with_postseason"] == 1
