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
                "selection_score": 88.0,
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
            {"game_id": 10, "team": "DEN", "market_type": "team_total", "line": 110.5},
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
    assert round(float(row["matchup_pace"]), 1) == 99.8
    assert row["team_def_rating"] == 112.1
    assert row["opponent_def_rating"] == 110.2
    assert row["team_off_rating"] == 116.3
    assert row["opponent_off_rating"] == 118.8
    assert round(float(row["team_net_rating"]), 1) == 4.2
    assert round(float(row["opponent_net_rating"]), 1) == 8.6
    assert row["rest_days"] == 1.0
    assert row["opponent_rest_days"] == 0.0
    assert row["rest_edge"] == 1.0
    assert bool(row["is_back_to_back"]) is False
    assert bool(row["opponent_is_back_to_back"]) is True
    assert row["implied_team_total"] == 114.5
    assert row["opponent_implied_team_total"] == 110.5
    assert row["game_total"] == 228.0
    assert row["spread"] == -3.5
    assert row["pace_context_signal"] == "neutral"
    assert row["defense_context_signal"] == "supports_under"
    assert row["rest_context_signal"] == "supports_over"
    assert row["playoff_context_signal"] == "supports_under"
    assert row["overall_context_signal"] == "supports_under"
    assert row["context_pick_alignment"] == "insufficient_data"
    assert bool(row["context_preview_applied"]) is False
    assert row["projection"] == 22.5
    assert row["confidence"] == 0.8
    assert row["quality_score"] == 99.0
    assert row["selection_score"] == 88.0
    assert diagnostics["rows"] == 1
    assert diagnostics["total_candidates"] == 1
    assert diagnostics["candidates_with_game_id"] == 1
    assert diagnostics["candidates_with_opponent"] == 1
    assert diagnostics["candidates_with_home_away"] == 1
    assert diagnostics["candidates_with_postseason"] == 1
    assert diagnostics["candidates_with_rest_days"] == 1
    assert diagnostics["candidates_with_def_rating"] == 1
    assert diagnostics["candidates_with_off_rating"] == 1
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
    written = json.loads(json_path.read_text(encoding="utf-8"))
    assert written["projection_changed"] is False
    assert written["kelly_logic_changed"] is False
    assert written["sample_rows"][0]["overall_context_signal"] == "supports_under"
    assert "Mode: passive diagnostics only" in report_path.read_text(encoding="utf-8")
    assert "overall:supports_under" in report_path.read_text(encoding="utf-8")


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


def test_game_context_parses_nested_bdl_schema_for_home_and_away_candidates() -> None:
    candidates = pd.DataFrame(
        [
            {"player_name": "Home Player", "team": "PHI", "team_abbr": "PHI", "game_id": 99},
            {"player_name": "Away Player", "team": "BOS", "team_abbr": "BOS", "game_id": 99},
        ]
    )
    games = pd.DataFrame(
        [
            {
                "id": 99,
                "home_team": {"id": 18, "abbreviation": "PHI"},
                "visitor_team": {"id": 2, "abbreviation": "BOS"},
                "postseason": True,
            }
        ]
    )

    out, diagnostics = apply_game_context(
        candidates,
        games=games,
        team_baselines=pd.DataFrame(),
        odds=pd.DataFrame(),
    )

    home = out[out["team"] == "PHI"].iloc[0]
    away = out[out["team"] == "BOS"].iloc[0]
    assert home["opponent"] == "BOS"
    assert home["home_away"] == "home"
    assert away["opponent"] == "PHI"
    assert away["home_away"] == "away"
    assert bool(home["postseason"]) is True
    assert bool(away["postseason"]) is True
    assert diagnostics["candidates_with_opponent"] == 2
    assert diagnostics["candidates_with_home_away"] == 2
    assert diagnostics["candidates_with_postseason"] == 2


def test_game_context_suppresses_context_when_candidate_team_not_in_game(tmp_path) -> None:
    candidates = pd.DataFrame(
        [
            {
                "player_id": 460,
                "player_name": "Nikola Vucevic",
                "team": "CHI",
                "team_abbr": "CHI",
                "game_id": 21682743,
                "market_type": "player_points",
                "selection": "over",
            },
            {
                "player_id": 460,
                "player_name": "Nikola Vucevic",
                "team": "BOS",
                "team_abbr": "BOS",
                "game_id": 21682743,
                "market_type": "player_points",
                "selection": "over",
            },
        ]
    )
    games = pd.DataFrame(
        [
            {
                "game_id": 21682743,
                "home_team_abbr": "BOS",
                "visitor_team_abbr": "PHI",
                "postseason": True,
            }
        ]
    )
    team_baselines = pd.DataFrame(
        [
            {"team_abbr": "BOS", "team_pace": 95.58, "team_def_rating": 111.7, "team_off_rating": 120.0},
            {"team_abbr": "PHI", "team_pace": 100.4, "team_def_rating": 114.4, "team_off_rating": 114.3},
            {"team_abbr": "CHI", "team_pace": 101.0, "team_def_rating": 115.0, "team_off_rating": 113.0},
        ]
    )

    out, diagnostics = apply_game_context(
        candidates,
        games=games,
        team_baselines=team_baselines,
        odds=pd.DataFrame(),
    )

    by_team = {row["team"]: row for _, row in out.iterrows()}
    stale = by_team["CHI"]
    valid = by_team["BOS"]

    assert stale["opponent"] == ""
    assert stale["home_away"] == ""
    assert pd.isna(stale["postseason"])
    assert pd.isna(stale["team_pace"])
    assert pd.isna(stale["opponent_pace"])
    assert stale["pace_context_signal"] == "insufficient_data"
    assert stale["defense_context_signal"] == "insufficient_data"
    assert stale["rest_context_signal"] == "insufficient_data"
    assert stale["playoff_context_signal"] == "insufficient_data"
    assert stale["overall_context_signal"] == "insufficient_data"
    assert stale["context_pick_alignment"] == "insufficient_data"
    assert stale["context_caution_level"] == "insufficient_data"
    assert bool(stale["candidate_team_not_in_game"]) is True
    assert bool(stale["game_context_suppressed"]) is True
    assert stale["game_context_suppression_reason"] == "team_not_in_game_context"

    assert valid["opponent"] == "PHI"
    assert valid["home_away"] == "home"
    assert bool(valid["postseason"]) is True
    assert valid["playoff_context_signal"] == "supports_under"
    assert bool(valid["game_context_suppressed"]) is False

    assert diagnostics["candidate_team_not_in_game_count"] == 1
    assert diagnostics["game_context_suppressed_count"] == 1
    assert diagnostics["playoff_only_high_caution_count"] == 1
    assert diagnostics["stale_team_not_in_game_count"] == 1
    assert diagnostics["context_conflict_cause_counts"]["stale_team_not_in_game"] == 1
    assert stale["context_conflict_cause"] == "stale_team_not_in_game"
    assert diagnostics["candidates_with_postseason"] == 1

    json_path, report_path, payload = write_game_context_outputs(
        prediction_date="2026-05-02",
        runtime_root=tmp_path / "runtime",
        diagnostics=diagnostics,
        candidates=out,
    )
    written = json.loads(json_path.read_text(encoding="utf-8"))
    assert written["game_context_suppressed_count"] == 1
    assert written["sample_rows"][0]["game_context_suppression_reason"] == "team_not_in_game_context"
    report_text = report_path.read_text(encoding="utf-8")
    assert "game_context_suppressed_count: 1" in report_text
    assert payload["candidate_team_not_in_game_count"] == 1


def test_game_context_conflict_cause_buckets_split_playoff_and_defense() -> None:
    candidates = pd.DataFrame(
        [
            {
                "player_name": "Playoff Only Over",
                "team": "AAA",
                "team_abbr": "AAA",
                "game_id": 1,
                "market_type": "player_points",
                "selection": "over",
            },
            {
                "player_name": "Defense Driven Over",
                "team": "CCC",
                "team_abbr": "CCC",
                "game_id": 2,
                "market_type": "player_points",
                "selection": "over",
            },
        ]
    )
    games = pd.DataFrame(
        [
            {"game_id": 1, "home_team_abbr": "AAA", "visitor_team_abbr": "BBB", "postseason": True},
            {"game_id": 2, "home_team_abbr": "CCC", "visitor_team_abbr": "DDD", "postseason": False},
        ]
    )
    team_baselines = pd.DataFrame(
        [
            {"team_abbr": "AAA", "team_pace": 98.5, "team_def_rating": 114.0, "team_off_rating": 115.0},
            {"team_abbr": "BBB", "team_pace": 98.0, "team_def_rating": 114.0, "team_off_rating": 114.0},
            {"team_abbr": "CCC", "team_pace": 98.5, "team_def_rating": 114.0, "team_off_rating": 115.0},
            {"team_abbr": "DDD", "team_pace": 98.0, "team_def_rating": 111.0, "team_off_rating": 114.0},
        ]
    )

    out, diagnostics = apply_game_context(
        candidates,
        games=games,
        team_baselines=team_baselines,
        odds=pd.DataFrame(),
    )

    by_player = {row["player_name"]: row for _, row in out.iterrows()}
    assert by_player["Playoff Only Over"]["context_conflict_cause"] == "playoff_only"
    assert by_player["Defense Driven Over"]["context_conflict_cause"] == "defense_driven"
    assert diagnostics["context_conflict_cause_counts"]["playoff_only"] == 1
    assert diagnostics["context_conflict_cause_counts"]["defense_driven"] == 1
    assert diagnostics["context_conflict_cause_counts"]["pace_driven"] == 0


def test_game_context_calculates_one_day_rest_from_schedule() -> None:
    candidates = pd.DataFrame([{"player_name": "Player A", "team": "AAA", "team_abbr": "AAA", "game_id": 1}])
    games = pd.DataFrame([{"game_id": 1, "home_team_abbr": "AAA", "visitor_team_abbr": "BBB"}])
    schedule = pd.DataFrame(
        [
            {"id": 99, "date": "2026-04-26", "home_team": {"abbreviation": "AAA"}, "visitor_team": {"abbreviation": "CCC"}},
            {"id": 98, "date": "2026-04-25", "home_team": {"abbreviation": "BBB"}, "visitor_team": {"abbreviation": "DDD"}},
        ]
    )

    out, diagnostics = apply_game_context(
        candidates,
        games=games,
        team_baselines=pd.DataFrame(),
        odds=pd.DataFrame(),
        prediction_date="2026-04-28",
        schedule_games=schedule,
    )

    assert out.loc[0, "rest_days"] == 1.0
    assert out.loc[0, "opponent_rest_days"] == 2.0
    assert out.loc[0, "rest_edge"] == -1.0
    assert bool(out.loc[0, "is_back_to_back"]) is False
    assert bool(out.loc[0, "opponent_is_back_to_back"]) is False
    assert diagnostics["candidates_with_rest_days"] == 1
    assert diagnostics["candidates_with_back_to_back"] == 0


def test_game_context_calculates_back_to_back_from_schedule() -> None:
    candidates = pd.DataFrame([{"player_name": "Player A", "team": "AAA", "team_abbr": "AAA", "game_id": 1}])
    games = pd.DataFrame([{"game_id": 1, "home_team_abbr": "AAA", "visitor_team_abbr": "BBB"}])
    schedule = pd.DataFrame(
        [
            {"id": 99, "date": "2026-04-27", "home_team": {"abbreviation": "AAA"}, "visitor_team": {"abbreviation": "CCC"}},
        ]
    )

    out, diagnostics = apply_game_context(
        candidates,
        games=games,
        team_baselines=pd.DataFrame(),
        odds=pd.DataFrame(),
        prediction_date="2026-04-28",
        schedule_games=schedule,
    )

    assert out.loc[0, "rest_days"] == 0.0
    assert bool(out.loc[0, "is_back_to_back"]) is True
    assert pd.isna(out.loc[0, "opponent_rest_days"])
    assert pd.isna(out.loc[0, "rest_edge"])
    assert diagnostics["candidates_with_rest_days"] == 1
    assert diagnostics["candidates_with_back_to_back"] == 1


def test_game_context_missing_previous_game_leaves_rest_null() -> None:
    candidates = pd.DataFrame([{"player_name": "Player A", "team": "AAA", "team_abbr": "AAA", "game_id": 1}])
    games = pd.DataFrame([{"game_id": 1, "home_team_abbr": "AAA", "visitor_team_abbr": "BBB"}])

    out, diagnostics = apply_game_context(
        candidates,
        games=games,
        team_baselines=pd.DataFrame(),
        odds=pd.DataFrame(),
        prediction_date="2026-04-28",
        schedule_games=pd.DataFrame(),
    )

    assert pd.isna(out.loc[0, "rest_days"])
    assert pd.isna(out.loc[0, "opponent_rest_days"])
    assert pd.isna(out.loc[0, "is_back_to_back"])
    assert pd.isna(out.loc[0, "opponent_is_back_to_back"])
    assert diagnostics["candidates_with_rest_days"] == 0
    assert diagnostics["candidates_with_back_to_back"] == 0


def test_game_context_missing_pace_and_ratings_stay_null_and_report_missing() -> None:
    candidates = pd.DataFrame([{"player_name": "Player A", "team": "AAA", "team_abbr": "AAA", "game_id": 1}])
    games = pd.DataFrame([{"game_id": 1, "home_team_abbr": "AAA", "visitor_team_abbr": "BBB"}])

    out, diagnostics = apply_game_context(
        candidates,
        games=games,
        team_baselines=pd.DataFrame(),
        odds=pd.DataFrame(),
    )

    for column in (
        "team_pace",
        "opponent_pace",
        "matchup_pace",
        "team_off_rating",
        "opponent_off_rating",
        "team_def_rating",
        "opponent_def_rating",
        "team_net_rating",
        "opponent_net_rating",
    ):
        assert pd.isna(out.loc[0, column])
        assert column in diagnostics["missing_fields"]
        assert diagnostics["missing_fields_breakdown"][column] == 1
    assert diagnostics["candidates_with_pace"] == 0
    assert diagnostics["candidates_with_def_rating"] == 0
    assert diagnostics["candidates_with_off_rating"] == 0
    assert out.loc[0, "pace_context_signal"] == "insufficient_data"
    assert out.loc[0, "defense_context_signal"] == "insufficient_data"
    assert out.loc[0, "rest_context_signal"] == "insufficient_data"
    assert out.loc[0, "playoff_context_signal"] == "insufficient_data"
    assert out.loc[0, "overall_context_signal"] == "insufficient_data"
    assert out.loc[0, "context_pick_alignment"] == "insufficient_data"
    assert out.loc[0, "context_caution_level"] == "insufficient_data"
    assert bool(out.loc[0, "context_preview_applied"]) is False


def test_game_context_preview_signals_are_passive_labels() -> None:
    candidates = pd.DataFrame(
        [
            {
                "player_name": "Player A",
                "team": "AAA",
                "team_abbr": "AAA",
                "game_id": 1,
                "projection": 18.0,
                "confidence": 0.7,
                "selection_score": 55.0,
                "selection": "over",
            }
        ]
    )
    games = pd.DataFrame([{"game_id": 1, "home_team_abbr": "AAA", "visitor_team_abbr": "BBB", "postseason": False}])
    team_baselines = pd.DataFrame(
        [
            {"team_abbr": "AAA", "team_pace": 103.0, "team_def_rating": 118.0, "team_off_rating": 119.0},
            {"team_abbr": "BBB", "team_pace": 102.0, "team_def_rating": 119.0, "team_off_rating": 111.0},
        ]
    )

    out, _ = apply_game_context(
        candidates,
        games=games,
        team_baselines=team_baselines,
        odds=pd.DataFrame(),
    )

    row = out.iloc[0]
    assert row["pace_context_signal"] == "supports_over"
    assert row["defense_context_signal"] == "supports_over"
    assert row["rest_context_signal"] == "insufficient_data"
    assert row["playoff_context_signal"] == "neutral"
    assert row["overall_context_signal"] == "supports_over"
    assert row["context_pick_alignment"] == "aligned"
    assert row["context_caution_level"] == "low"
    assert bool(row["context_preview_applied"]) is False
    assert row["projection"] == 18.0
    assert row["confidence"] == 0.7
    assert row["selection_score"] == 55.0


def test_game_context_pick_alignment_rules() -> None:
    candidates = pd.DataFrame(
        [
            {"player_name": "Over Player", "team": "AAA", "team_abbr": "AAA", "game_id": 1, "selection": "over"},
            {"player_name": "Under Player", "team": "AAA", "team_abbr": "AAA", "game_id": 1, "selection": "under"},
        ]
    )
    games = pd.DataFrame([{"game_id": 1, "home_team_abbr": "AAA", "visitor_team_abbr": "BBB", "postseason": False}])
    team_baselines = pd.DataFrame(
        [
            {"team_abbr": "AAA", "team_pace": 103.0, "team_def_rating": 118.0, "team_off_rating": 119.0},
            {"team_abbr": "BBB", "team_pace": 102.0, "team_def_rating": 119.0, "team_off_rating": 111.0},
        ]
    )

    out, _ = apply_game_context(
        candidates,
        games=games,
        team_baselines=team_baselines,
        odds=pd.DataFrame(),
    )

    by_selection = {row["selection"]: row["context_pick_alignment"] for _, row in out.iterrows()}
    caution_by_selection = {row["selection"]: row["context_caution_level"] for _, row in out.iterrows()}
    assert by_selection["over"] == "aligned"
    assert by_selection["under"] == "conflicted"
    assert caution_by_selection["over"] == "low"
    assert caution_by_selection["under"] == "insufficient_data"


def test_game_context_caution_flags_are_passive_labels() -> None:
    candidates = pd.DataFrame(
        [
            {
                "player_name": "Caution Player",
                "team": "AAA",
                "team_abbr": "AAA",
                "game_id": 1,
                "selection": "over",
                "projection": 18.0,
                "confidence": 0.7,
                "selection_score": 55.0,
            },
            {
                "player_name": "Neutral Player",
                "team": "AAA",
                "team_abbr": "AAA",
                "game_id": 2,
                "selection": "over",
                "projection": 12.0,
                "confidence": 0.6,
                "selection_score": 40.0,
            },
        ]
    )
    games = pd.DataFrame(
        [
            {"game_id": 1, "home_team_abbr": "AAA", "visitor_team_abbr": "BBB", "postseason": True},
            {"game_id": 2, "home_team_abbr": "AAA", "visitor_team_abbr": "CCC", "postseason": False},
        ]
    )
    team_baselines = pd.DataFrame(
        [
            {"team_abbr": "AAA", "team_pace": 98.5, "team_def_rating": 114.0, "team_off_rating": 115.0},
            {"team_abbr": "BBB", "team_pace": 98.0, "team_def_rating": 111.0, "team_off_rating": 119.0},
            {"team_abbr": "CCC", "team_pace": 98.5, "team_def_rating": 114.0, "team_off_rating": 115.0},
        ]
    )

    out, _ = apply_game_context(
        candidates,
        games=games,
        team_baselines=team_baselines,
        odds=pd.DataFrame(),
    )

    by_player = {row["player_name"]: row for _, row in out.iterrows()}
    assert by_player["Caution Player"]["overall_context_signal"] == "supports_under"
    assert by_player["Caution Player"]["context_pick_alignment"] == "conflicted"
    assert by_player["Caution Player"]["context_caution_level"] == "high"
    assert by_player["Neutral Player"]["overall_context_signal"] == "neutral"
    assert by_player["Neutral Player"]["context_pick_alignment"] == "neutral"
    assert by_player["Neutral Player"]["context_caution_level"] == "medium"
    assert by_player["Caution Player"]["projection"] == 18.0
    assert by_player["Caution Player"]["confidence"] == 0.7
    assert by_player["Caution Player"]["selection_score"] == 55.0
