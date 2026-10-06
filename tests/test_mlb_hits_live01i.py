"""Offline scoring-index and structural pregame evidence regressions."""
from datetime import timedelta

import pytest

from courtvision.sports.mlb import live01
from courtvision.sports.mlb.fact_backfill_evidence import EvidenceJournal
from test_mlb_fact_backfill import schedule
from test_mlb_hits_live01 import BaseballProvider, NOW, SHA, full_feed, target_game
from test_mlb_hits_live01g import advisory_play, change, offline, plays

AMBIGUOUS = "AMBIGUOUS_OR_CONTRADICTORY_PLAY_EVIDENCE"
ACTUAL = "ACTUAL_GAMEPLAY_EVIDENCE"


def scoring_play(index=0):
    # Structural excerpt of preserved 849828 raw/000002, play 26, rebased to index.
    # Raw SHA-256: 0d01b085bde0ef9adf0b3ac3722caf651a2966d2d10ceb7e6cb4525c4df2fce6
    return {"atBatIndex": index,
            "about": {"atBatIndex": index, "isComplete": True, "isScoringPlay": True},
            "result": {"type": "atBat", "eventType": "home_run", "rbi": 2,
                       "awayScore": 0, "homeScore": 2, "isOut": False},
            "playEvents": [{"type": "pitch", "isPitch": True}]}


@pytest.mark.parametrize("value,state", [
    ({}, "NO_GAMEPLAY_EVIDENCE"),
    ({"allPlays": [], "scoringPlays": []}, "NO_GAMEPLAY_EVIDENCE"),
    ({"allPlays": [advisory_play()], "scoringPlays": []}, "ADMINISTRATIVE_PREGAME_ONLY"),
    ({"allPlays": [scoring_play()], "scoringPlays": [0]}, ACTUAL),
    ({"allPlays": [advisory_play()], "scoringPlays": [0]}, AMBIGUOUS),
])
def test_four_structural_evidence_states(value, state):
    assert live01._classify_game_start_plays(value) == state
    assert live01._has_game_start_play(value) == (state in {ACTUAL, AMBIGUOUS})


@pytest.mark.parametrize("scoring", [None, {}, "", 0, False, [None], [True], [False],
                                     ["0"], [0.0], [-1], [1], [999], [{}], [[]], [0, 0]])
def test_malformed_unresolvable_or_duplicate_scoring_references_fail_closed(scoring):
    value = {"allPlays": [scoring_play()], "scoringPlays": scoring}
    assert live01._classify_game_start_plays(value) == AMBIGUOUS
    assert live01._has_game_start_play(value)


@pytest.mark.parametrize("path,value", [
    (("atBatIndex",), 1), (("atBatIndex",), False),
    (("about", "atBatIndex"), 1), (("about", "atBatIndex"), "0"),
    (("about", "isScoringPlay"), False), (("about", "isScoringPlay"), None),
    (("result", "homeScore"), 0), (("result", "homeScore"), True),
    (("result", "awayScore"), -1), (("result", "rbi"), None),
    (("result", "eventType"), "game_advisory"), (("result",), None),
    (("result", "type"), None), (("result", "eventType"), None),
    (("result", "isScoringPlay"), False), (("result", "isOut"), None),
])
def test_scoring_reference_requires_corroborating_play_metadata(path, value):
    play = scoring_play()
    change(play, path, value)
    container = {"allPlays": [play], "scoringPlays": [0]}
    assert live01._classify_game_start_plays(container) == AMBIGUOUS


def test_array_position_and_both_at_bat_indexes_must_agree():
    value = {"allPlays": [advisory_play(), scoring_play(1)], "scoringPlays": [1]}
    assert live01._classify_game_start_plays(value) == ACTUAL
    value["allPlays"].reverse()
    assert live01._classify_game_start_plays(value) == AMBIGUOUS


@pytest.mark.parametrize("score", [2, 1])
def test_scoring_flag_cannot_claim_unchanged_or_decreased_score(score):
    first, second = scoring_play(), scoring_play(1)
    second["result"]["homeScore"] = score
    value = {"allPlays": [first, second], "scoringPlays": [1]}
    assert live01._classify_game_start_plays(value) == AMBIGUOUS


@pytest.mark.parametrize("text", [None, "Status Change - In Progress", "Localized advisory"])
def test_provider_prose_does_not_classify_administrative_structure(text):
    play = advisory_play()
    for details in (play["result"], play["playEvents"][0]["details"]):
        details["description"] = details["event"] = text
    play["diagnosticMetadata"] = {"futureField": "harmless"}
    container = plays(play)
    container["diagnosticMetadata"] = {"futureField": "harmless"}
    assert live01._classify_game_start_plays(container) == "ADMINISTRATIVE_PREGAME_ONLY"


@pytest.mark.parametrize("path,value", [
    (("pitchData",), {}), (("hitData",), None), (("isPitch",), True),
    (("actionIndex",), None), (("actionIndex",), [True]), (("actionIndex",), [1]),
    (("result", "isScoringPlay"), True),
    (("result", "pitchData"), {}), (("playEvents", 0, "details", "hitData"), {}),
    (("playEvents", 0, "index"), True), (("playEvents", 0, "index"), 1),
    (("playEvents", 0, "details", "isInPlay"), True),
    (("playEvents", 0, "details", "isBall"), True),
    (("playEvents", 0, "details", "isStrike"), True),
])
def test_administrative_play_cannot_hide_relevant_gameplay_structure(path, value):
    play = advisory_play()
    change(play, path, value)
    assert live01._classify_game_start_plays(plays(play)) == AMBIGUOUS


@pytest.mark.parametrize("innings", [None, {}, [None], [{"top": None}], [{"top": [1]}],
                                      [{"top": [True]}], [{"startIndex": -1}],
                                      [{"bottom": [0]}], [{"top": [0], "bottom": [0]}],
                                      [{"endIndex": "0"}], [{"hits": None}],
                                      [{"hits": {"away": {}}}], [{"hits": {"home": [{}]}}]])
def test_other_observed_play_index_cannot_contradict_administrative_only(innings):
    value = plays(advisory_play())
    value["playsByInning"] = innings
    assert live01._classify_game_start_plays(value) == AMBIGUOUS


def test_observed_administrative_indexes_remain_eligible():
    play = advisory_play()
    play["actionIndex"] = [0]
    value = plays(play)
    value["scoringPlays"] = []
    value["playsByInning"] = [{"startIndex": 0, "endIndex": 0, "top": [0], "bottom": [],
                              "hits": {"away": [], "home": []}}]
    assert live01._classify_game_start_plays(value) == "ADMINISTRATIVE_PREGAME_ONLY"


@pytest.mark.parametrize("signal", ["scoring", "contradictory_scoring", "malformed_scoring",
                                  "unresolved_scoring", "unknown_play", "pitch", "first_pitch",
                                  "live", "clock_at_start", "clock_after_start"])
def test_complete_lineups_cannot_override_started_or_ambiguous_evidence(tmp_path, monkeypatch, signal):
    row = target_game()
    current = full_feed(row, pregame=True)
    current["liveData"]["plays"] = plays(advisory_play())
    value = current["liveData"]["plays"]
    if signal == "scoring":
        value.update(allPlays=[scoring_play()], currentPlay=scoring_play(), scoringPlays=[0])
    elif signal == "contradictory_scoring":
        value["scoringPlays"] = [0]
    elif signal == "malformed_scoring":
        value["scoringPlays"] = {}
    elif signal == "unresolved_scoring":
        value["scoringPlays"] = [1]
    elif signal == "unknown_play":
        value["currentPlay"] = {"pitchData": {}}
    elif signal == "pitch":
        value["currentPlay"]["playEvents"] = [{"type": "pitch", "isPitch": True}]
    elif signal == "first_pitch":
        current["gameData"]["datetime"]["firstPitch"] = NOW.isoformat()
    elif signal == "live":
        current["gameData"]["status"]["abstractGameState"] = "Live"
    provider = BaseballProvider({"target-schedule-2026-10-01": schedule(row),
                                "pregame-feed-900001": current})
    journal = EvidenceJournal(tmp_path / "synthetic-raw", 8)
    _, inventory, record, _ = live01.select_target(journal, provider, now=NOW, clock=lambda: NOW)
    clocks = iter([NOW, NOW + timedelta(hours=1, seconds=signal == "clock_after_start")]
                  if signal.startswith("clock_") else [NOW, NOW])
    def forbidden(*args, **kwargs):
        raise AssertionError("start rejection must precede identity, lineup, ledger, and model")
    for name in ("bind_statsapi_event", "extract_batter_lineup_evidence", "load_ledger_season", "prediction_row"):
        monkeypatch.setattr(live01, name, forbidden)
    rows, sources, excluded, counts = live01.generate_cohort(
        inventory, record, journal, provider, index={}, store=None,
        run_id="offline-scoring-gate", repository_sha=SHA, clock=lambda: next(clocks))
    assert rows == [] and sources == {}
    assert excluded == [{"gamePk": "900001", "reason": "GAME_STARTED"}]
    assert counts["confirmed_batters"] == counts["ledger_qualified_batters"] == 0
    assert all(len(current["liveData"]["boxscore"]["teams"][side]["battingOrder"]) == 9
               for side in ("away", "home"))
