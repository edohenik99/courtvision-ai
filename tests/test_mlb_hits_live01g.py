"""Offline regressions for the preserved 849825 pregame advisory start gate."""
from copy import deepcopy
from datetime import timedelta
import socket

import pytest

from courtvision.sports.mlb import live01
from courtvision.sports.mlb import fact_backfill_evidence as evidence
from courtvision.sports.mlb.fact_backfill_evidence import EvidenceJournal
from test_mlb_fact_backfill import schedule
from test_mlb_hits_live01 import BaseballProvider, NOW, SHA, cohort, full_feed, target_game


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("LIVE-01G tests cannot access providers or freeze predictions")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(live01, "freeze_predictions", forbidden)
    monkeypatch.setattr(live01, "capture_market", forbidden)
    monkeypatch.setattr(live01, "execute_live01", forbidden)
    monkeypatch.setattr(evidence, "utc_now", lambda: NOW - timedelta(seconds=1))


def advisory_play():
    # Gate-relevant excerpt of the preserved 849825 feed, not invented lineups.
    # cv-mlb-hits-live01-20261004-1431/raw/000002/body.bin SHA-256:
    # 05a8cf5a4084294645b70e7a1b11457e6e703978258a0f52054a2a4006d2f3c9
    return {
        "about": {"atBatIndex": 0, "halfInning": "top", "hasOut": False,
                  "inning": 1, "isComplete": False, "isScoringPlay": False, "isTopInning": True},
        "atBatIndex": 0, "count": {"balls": 0, "outs": 0, "strikes": 0},
        "pitchIndex": [], "runnerIndex": [], "runners": [],
        "result": {"awayScore": 0, "description": "Status Change - Pre-Game",
                   "event": "Game Advisory", "eventType": "game_advisory", "homeScore": 0,
                   "isOut": False, "rbi": 0, "type": "atBat"},
        "playEvents": [{"count": {"balls": 0, "outs": 0, "strikes": 0},
                        "details": {"awayScore": 0, "description": "Status Change - Pre-Game",
                                    "event": "Game Advisory", "eventType": "game_advisory",
                                    "homeScore": 0, "isOut": False, "isScoringPlay": False},
                        "isPitch": False, "type": "action"}],
    }


def plays(play):
    return {"allPlays": [play], "currentPlay": deepcopy(play)}


def change(play, path, value):
    for key in path[:-1]:
        play = play[key]
    play[path[-1]] = value


def test_preserved_non_pitch_advisory_is_not_game_start():
    assert not live01._has_game_start_play(plays(advisory_play()))
    assert not live01._has_game_start_play({"allPlays": [advisory_play(), advisory_play()]})
    assert not live01._has_game_start_play({"currentPlay": advisory_play()})


@pytest.mark.parametrize("empty", [{}, {"allPlays": []}, {"allPlays": [], "currentPlay": {}},
                                  {"allPlays": [], "currentPlay": None}])
def test_absent_play_evidence_retains_existing_pregame_behavior(empty):
    assert not live01._has_game_start_play(empty)


@pytest.mark.parametrize("path,value", [
    (("pitchIndex",), [0]),
    (("playEvents", 0, "isPitch"), True),
    (("playEvents", 0, "type"), "pitch"),
    (("playEvents", 0, "pitchData"), {}),
    (("playEvents", 0, "hitData"), {}),
    (("about", "isComplete"), True),
    (("about", "hasOut"), True),
    (("about", "isScoringPlay"), True),
    (("about", "atBatIndex"), 1),
    (("atBatIndex",), 1),
    (("about", "inning"), 2),
    (("about", "halfInning"), "bottom"),
    (("count", "balls"), 1),
    (("count", "strikes"), 1),
    (("count", "outs"), 1),
    (("playEvents", 0, "count", "balls"), 1),
    (("playEvents", 0, "count", "strikes"), 1),
    (("playEvents", 0, "count", "outs"), 1),
    (("runnerIndex",), [0]),
    (("runners",), [{"movement": {"end": "1B"}}]),
    (("result", "homeScore"), 1),
    (("result", "awayScore"), 1),
    (("result", "rbi"), 1),
    (("result", "isOut"), True),
    (("result", "eventType"), "single"),
    (("playEvents", 0, "details", "eventType"), "pickoff"),
    (("playEvents", 0, "details", "isScoringPlay"), True),
    (("playEvents", 0, "details", "isOut"), True),
    (("playEvents", 0, "details", "homeScore"), 1),
    (("playEvents", 0, "details", "awayScore"), 1),
    (("playEvents", 0, "isPitch"), None),
    (("about", "isComplete"), None),
    (("count", "balls"), False),
    (("count", "strikes"), "0"),
    (("result", "awayScore"), None),
    (("playEvents",), []),
    (("playEvents",), [None]),
    (("result",), {}),
])
def test_game_action_or_unproven_advisory_fails_closed(path, value):
    play = advisory_play()
    change(play, path, value)
    assert live01._has_game_start_play(plays(play))


@pytest.mark.parametrize("container", [None, [], {"allPlays": None}, {"allPlays": {}},
                                      {"allPlays": [None]}, {"allPlays": [{}]},
                                      {"currentPlay": []}])
def test_unknown_play_shapes_fail_closed(container):
    assert live01._has_game_start_play(container)


@pytest.mark.parametrize("location", ["before", "after", "current_only", "event"])
def test_advisory_cannot_mask_other_game_action(location):
    advisory, pitch = advisory_play(), advisory_play()
    pitch["playEvents"][0]["isPitch"] = True
    value = plays(advisory)
    if location == "before":
        value["allPlays"].insert(0, pitch)
    elif location == "after":
        value["allPlays"].append(pitch)
    elif location == "current_only":
        value["currentPlay"] = pitch
    else:
        value["allPlays"][0]["playEvents"].append(pitch["playEvents"][0])
    assert live01._has_game_start_play(value)


@pytest.mark.parametrize("game_type", ["R", "D"])
def test_advisory_reaches_complete_ledger_cohort_without_changing_formula(tmp_path, game_type):
    def add_advisory(current):
        current["liveData"]["plays"] = plays(advisory_play())
    (rows, _, excluded, counts), _, _, _ = cohort(
        tmp_path, change_feed=add_advisory, game_type=game_type)
    assert not any(item["reason"] == "GAME_STARTED" for item in excluded)
    assert counts["games_with_confirmed_lineups"] == 1
    assert counts["confirmed_batters"] == 18
    assert counts["ledger_qualified_batters"] == len(rows) == 2
    assert {row["mlbam_game_id"] for row in rows} == {"900001"}
    assert {row["mlbam_player_id"] for row in rows} == {"700001", "700002"}
    assert all(row["projected_at_bats"] == 4 and row["model_probability"] == 1 - .75 ** 4
               and row["projection_model_version"] == "cv_ab_projection_v2" for row in rows)


@pytest.mark.parametrize("side", ["home", "away"])
def test_advisory_does_not_qualify_an_incomplete_lineup(tmp_path, monkeypatch, side):
    def partial(current):
        current["liveData"]["plays"] = plays(advisory_play())
        current["liveData"]["boxscore"]["teams"][side]["battingOrder"].pop()
    def forbidden(*args, **kwargs):
        raise AssertionError("incomplete orders cannot reach model generation")
    monkeypatch.setattr(live01, "prediction_row", forbidden)
    (rows, sources, excluded, counts), _, _, _ = cohort(tmp_path, change_feed=partial)
    assert rows == [] and sources == {}
    assert any(item.get("detail") == "INCOMPLETE_BATTING_ORDERS" and item["team_sides"] == [side]
               for item in excluded)
    assert counts["games_without_confirmed_lineups"] == 1
    assert counts["games_with_confirmed_lineups"] == counts["confirmed_batters"] == 0


@pytest.mark.parametrize("signal", ["pitch", "complete_at_bat", "first_pitch", "live", "final",
                                  "suspended", "clock_at_start", "clock_after_start"])
def test_genuine_start_gates_stop_before_identity_and_model(tmp_path, monkeypatch, signal):
    row = target_game()
    current = full_feed(row, pregame=True)
    current["liveData"]["plays"] = plays(advisory_play())
    if signal == "pitch":
        current["liveData"]["plays"]["currentPlay"]["playEvents"][0]["isPitch"] = True
    elif signal == "complete_at_bat":
        current["liveData"]["plays"]["allPlays"][0]["about"]["isComplete"] = True
    elif signal == "first_pitch":
        current["gameData"]["datetime"]["firstPitch"] = NOW.isoformat()
    elif signal in {"live", "final", "suspended"}:
        current["gameData"]["status"]["abstractGameState"] = signal.title()
    provider = BaseballProvider({"target-schedule-2026-10-01": schedule(row),
                                "pregame-feed-900001": current})
    journal = EvidenceJournal(tmp_path / "synthetic-raw", 8)
    _, inventory, record, _ = live01.select_target(journal, provider, now=NOW, clock=lambda: NOW)
    clocks = iter([NOW, NOW + timedelta(hours=1, seconds=signal == "clock_after_start")]
                  if signal.startswith("clock_") else [NOW, NOW])
    def forbidden(*args, **kwargs):
        raise AssertionError("started games must stop before identity/model/ledger work")
    monkeypatch.setattr(live01, "bind_statsapi_event", forbidden)
    monkeypatch.setattr(live01, "load_ledger_season", forbidden)
    monkeypatch.setattr(live01, "prediction_row", forbidden)
    rows, sources, excluded, counts = live01.generate_cohort(
        inventory, record, journal, provider, index={}, store=None,
        run_id="offline-start-gate", repository_sha=SHA, clock=lambda: next(clocks))
    assert rows == [] and sources == {}
    assert excluded == [{"gamePk": "900001", "reason": "GAME_STARTED"}]
    assert counts["target_game_feeds_captured"] == 1
    assert counts["confirmed_batters"] == counts["ledger_qualified_batters"] == 0
    assert provider.calls == ["target-schedule-2026-10-01", "pregame-feed-900001"]
