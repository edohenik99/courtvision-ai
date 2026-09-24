"""Offline final-fact contracts and a labelled realistic fixture rehearsal."""
from __future__ import annotations

import builtins
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import socket
import subprocess

import pytest

from courtvision.core.candidates import EventIdentity, IdentityStatus
from courtvision.sports.mlb.batting_results import extract_batting_results
from courtvision.sports.mlb.fact_ledger import MLBFactStore
from courtvision.sports.mlb.game_facts import (
    MLBBatterGameFact, MLBPitcherGameFact, extract_game_fact,
    extract_player_game_facts, fact_from_payload, fact_payload, innings_to_outs,
)
from courtvision.sports.mlb.season_facts import (
    MLBBatterSeasonFacts, MLBPitcherSeasonFacts, bridge_batter_season_hitting_evidence,
)


DAY = date(2026, 9, 18)
OBSERVED = datetime(2026, 9, 19, 4, tzinfo=timezone.utc)
BAT = {"atBats": 5, "hits": 4, "plateAppearances": 6, "doubles": 1,
       "triples": 1, "homeRuns": 1, "baseOnBalls": 1, "strikeOuts": 1, "runs": 2, "rbi": 3}
PITCH = {"inningsPitched": "6.1", "battersFaced": 26, "strikeOuts": 8,
         "baseOnBalls": 2, "hits": 5, "homeRuns": 1, "numberOfPitches": 96, "strikes": 62}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("factual core tests must remain offline")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


def game(game_id="823100", *, home=114, away=142, official_date=DAY, **kwargs):
    payload = {"gamePk": int(game_id), "officialDate": official_date.isoformat(),
        "status": {"abstractGameState": "Final", "detailedState": "Final", "codedGameState": "F"},
        "teams": {"home": {"team": {"id": home, "name": "Fixture Home"}, "score": 6},
                  "away": {"team": {"id": away, "name": "Fixture Away"}, "score": 3}}}
    values = dict(event_identity=EventIdentity(game_id, "fixture_bound", IdentityStatus.RESOLVED, game_id),
        game_date=official_date, game_status="Final", observed_at=OBSERVED, source_refs=("fixture:final-schedule",))
    values.update(kwargs)
    return extract_game_fact(payload, **values)


def boxscore(game_id="823100", *, player=700001, batting=None, pitching=None):
    return {"gamePk": int(game_id), "teams": {
        "away": {"team": {"id": 142}, "players": {}},
        "home": {"team": {"id": 114}, "players": {
            "arbitrary-row-key": {"person": {"id": player, "fullName": "Fixture Player"},
                "stats": {"batting": deepcopy(BAT if batting is None else batting),
                          "pitching": deepcopy(PITCH if pitching is None else pitching)}}}}}}


def extract(payload=None, *, bound_game=None):
    return extract_player_game_facts(boxscore() if payload is None else payload,
        game=game() if bound_game is None else bound_game, observed_at=OBSERVED,
        source_refs=("fixture:final-boxscore",))


def batter():
    return next(f for f in extract() if isinstance(f, MLBBatterGameFact))


def pitcher():
    return next(f for f in extract() if isinstance(f, MLBPitcherGameFact))


def batting_summary(*facts):
    return MLBBatterSeasonFacts(season=2026, mlbam_player_id="700001", facts=tuple(facts))


def test_final_schedule_and_both_player_roles_are_bound():
    g = game()
    assert (g.home_team_id, g.away_team_id, g.final_home_runs, g.final_away_runs) == ("114", "142", 6, 3)
    b, p = extract()
    assert b.logical_identity == ("823100", "700001", "BATTER")
    assert p.logical_identity == ("823100", "700001", "PITCHER")
    assert b.game_fact_hash == g.factual_record_hash
    assert b.side == p.side == "home" and b.team_id == "114" and b.opponent_id == "142"
    assert g.source_refs[0] in b.source_refs


def test_existing_batting_extractor_preserves_additional_facts():
    result = extract_batting_results(boxscore(), event_identity=EventIdentity(
        "823100", "fixture", IdentityStatus.RESOLVED, "823100"), game_status="final",
        observed_at=OBSERVED, source_refs=("fixture:boxscore",)).batters[0]
    assert (result.at_bats, result.hits, result.doubles, result.triples, result.home_runs) == (5, 4, 1, 1, 1)
    assert result.singles == 1 and result.has_batting_stats
    b = batter()
    assert (b.plate_appearances, b.walks, b.strikeouts, b.runs, b.rbi) == (6, 1, 1, 2, 3)
    assert b.singles == 1 and b.total_bases == 10


@pytest.mark.parametrize("status", ["Live", "Suspended", "Game Over", "unknown", "Scheduled", ""])
def test_nonfinal_rejected_even_with_scores_and_stats(status):
    with pytest.raises(ValueError):
        game(game_status=status)
    with pytest.raises(ValueError, match="explicitly final"):
        replace(batter(), game_status=status)


@pytest.mark.parametrize("embedded", [{"abstractGameState": "Live"},
    {"abstractGameState": "Final", "statusCode": "I"}, {"statusCode": "F"}])
def test_boxscore_cannot_override_finality(embedded):
    payload = boxscore()
    payload["status"] = embedded
    with pytest.raises(ValueError):
        extract(payload)


def test_absent_caller_identity_or_mismatched_game_date_fails():
    with pytest.raises(ValueError):
        game(event_identity=EventIdentity("name-only", "fixture"))
    with pytest.raises(ValueError, match="officialDate"):
        game(game_date=DAY + timedelta(days=1))
    with pytest.raises(ValueError, match="canonical event"):
        extract(boxscore("823101"))


@pytest.mark.parametrize("value", [None, "2", 2.0, True, False, -1])
def test_missing_or_invalid_batting_count_never_becomes_zero(value):
    payload = boxscore(batting={**BAT, "doubles": value})
    b = next(f for f in extract(payload) if isinstance(f, MLBBatterGameFact))
    assert b.doubles is None and b.singles is None and b.total_bases is None


def test_invalid_hits_ab_missing_and_optional_count_semantics():
    b = extract(boxscore(batting={"atBats": 4, "hits": 5}, pitching={}))[0]
    assert b.at_bats == 4 and b.hits is None
    assert b.plate_appearances is None and b.home_runs is None
    assert not batting_summary(b).qualified_ab_h
    with pytest.raises(ValueError, match="missing required"):
        bridge_batter_season_hitting_evidence(batting_summary(b), player_name="Fixture Player", evidence_cutoff=OBSERVED)


def test_impossible_singles_and_direct_invalid_counts_rejected():
    with pytest.raises(ValueError, match="impossible singles"):
        extract(boxscore(batting={**BAT, "hits": 2}))
    for changes in ({"hits": 6}, {"hits": True}, {"doubles": "1"}, {"plate_appearances": 4}):
        with pytest.raises(ValueError):
            replace(batter(), **changes)


@pytest.mark.parametrize("raw,outs", [("0.0", 0), ("0.1", 1), ("6.0", 18), ("6.1", 19), ("6.2", 20)])
def test_innings_conversion_and_pitcher_counts(raw, outs):
    assert innings_to_outs(raw) == outs
    p = next(f for f in extract(boxscore(pitching={**PITCH, "inningsPitched": raw})) if isinstance(f, MLBPitcherGameFact))
    assert p.outs_recorded == outs
    assert (p.strikeouts, p.walks, p.hits_allowed, p.home_runs_allowed) == (8, 2, 5, 1)
    assert (p.batters_faced, p.pitches, p.strikes) == (26, 96, 62)


@pytest.mark.parametrize("raw", ["6.3", "6.9", "6.10", "6", "06.1", "-1.0", "6.1 ", 6.1, 6, True])
def test_invalid_innings_rejected(raw):
    with pytest.raises(ValueError, match="baseball text"):
        extract(boxscore(pitching={**PITCH, "inningsPitched": raw}))


@pytest.mark.parametrize("raw", [None, "8", 8.0, True, -1])
def test_missing_pitching_count_never_becomes_zero(raw):
    p = extract(boxscore(batting={}, pitching={"strikeOuts": raw}))[0]
    assert p.strikeouts is None and p.outs_recorded is None and p.walks is None
    s = MLBPitcherSeasonFacts(season=2026, mlbam_player_id="700001", facts=(p,))
    assert s.strikeouts is None and s.outs_recorded is None and s.hits_allowed is None


def test_roster_only_players_are_not_games_and_names_are_not_keys():
    assert extract(boxscore(batting={}, pitching={})) == ()
    payload = boxscore(pitching={})
    players = payload["teams"]["home"]["players"]
    players["second"] = deepcopy(players["arbitrary-row-key"])
    players["second"]["person"]["id"] = 700002
    results = extract(payload)
    assert len(results) == 2 and results[0].player_name == results[1].player_name
    assert results[0].logical_identity != results[1].logical_identity


@pytest.mark.parametrize("player_id", [None, "unknown", True, 0, "0700001"])
def test_active_stat_rows_require_explicit_player_identity(player_id):
    with pytest.raises(ValueError, match="mlbam_player_id"):
        extract(boxscore(player=player_id))


def test_duplicate_roster_ids_are_ambiguous_even_across_teams():
    payload = boxscore()
    payload["teams"]["away"]["players"] = deepcopy(payload["teams"]["home"]["players"])
    with pytest.raises(ValueError, match="duplicate roster"):
        extract(payload)


def test_team_identity_conflicts_rejected():
    payload = boxscore()
    payload["teams"]["home"]["team"]["id"] = 147
    with pytest.raises(ValueError, match="team identity"):
        extract(payload)


def test_distinct_games_doubleheader_and_trade_aggregation():
    first = batter()
    # Same official date, different gamePk: doubleheader remains two games.
    second = replace(first, mlbam_game_id="823101", team_id="147", opponent_id="121")
    summary = batting_summary(second, first, first)
    assert summary.game_ids == ("823100", "823101")
    assert summary.distinct_batting_games == 2
    assert (summary.at_bats, summary.hits, summary.singles, summary.total_bases) == (10, 8, 2, 20)
    assert (summary.plate_appearances, summary.doubles, summary.triples, summary.home_runs,
            summary.walks, summary.strikeouts, summary.runs, summary.rbi) == (12, 2, 2, 2, 2, 2, 4, 6)


@pytest.mark.parametrize("changes", [{"hits": 3}, {"team_id": "147"}, {"source_refs": ("fixture:other",)}])
def test_conflicting_duplicate_game_fails(changes):
    first = batter()
    with pytest.raises(ValueError, match="conflicting duplicate"):
        batting_summary(first, replace(first, **changes))


def test_wrong_player_season_role_and_empty_summary_fail():
    for changes in ({"mlbam_player_id": "700002"}, {"game_date": date(2025, 9, 18)}):
        with pytest.raises(ValueError):
            batting_summary(replace(batter(), **changes))
    with pytest.raises(TypeError):
        batting_summary(pitcher())
    with pytest.raises(ValueError):
        batting_summary()


def test_nonfinal_tampered_record_cannot_aggregate():
    b = batter()
    object.__setattr__(b, "game_status", "live")
    with pytest.raises(ValueError, match="explicitly final"):
        batting_summary(b)


@pytest.mark.parametrize("missing", ["at_bats", "hits"])
def test_missing_required_ab_h_blocks_qualified_summary(missing):
    summary = batting_summary(batter(), replace(batter(), mlbam_game_id="823101", **{missing: None}))
    assert getattr(summary, missing) is None and not summary.qualified_ab_h
    with pytest.raises(ValueError, match="missing required"):
        bridge_batter_season_hitting_evidence(summary, player_name="Fixture Player", evidence_cutoff=OBSERVED)


def test_optional_incompleteness_poisons_only_that_total():
    summary = batting_summary(batter(), replace(batter(), mlbam_game_id="823101", doubles=None))
    assert summary.qualified_ab_h and summary.at_bats == 10
    assert summary.doubles is None and summary.total_bases is None
    assert summary.home_runs == 2


def test_pitcher_distinct_aggregation_and_partial_counts():
    p = pitcher()
    second = replace(p, mlbam_game_id="823101", innings_pitched="6.2", outs_recorded=20, strikeouts=9)
    s = MLBPitcherSeasonFacts(season=2026, mlbam_player_id="700001", facts=(p, second, p))
    assert s.distinct_pitching_games == 2 and s.outs_recorded == 39 and s.strikeouts == 17
    assert (s.walks, s.hits_allowed, s.home_runs_allowed, s.batters_faced, s.pitches, s.strikes) == (4, 10, 2, 52, 192, 124)


def test_hash_is_portable_and_order_independent_but_content_bound():
    payload = boxscore()
    reordered = json.loads(json.dumps(payload, sort_keys=True))
    assert extract(payload) == extract(reordered)
    b = batter()
    portable = replace(b, source_refs=("C:\\evidence\\game.json", "fixture:z"))
    equivalent = replace(b, source_refs=("fixture:z", "C:/evidence/game.json"),
                         observed_at=OBSERVED.astimezone(timezone(timedelta(hours=-4))))
    assert portable.factual_record_hash == equivalent.factual_record_hash
    assert replace(b, runs=3).factual_record_hash != b.factual_record_hash
    assert fact_from_payload(fact_payload(b)) == b
    with pytest.raises(FrozenInstanceError):
        b.hits = 0


@pytest.mark.parametrize("field,value", [("game_status", "live"), ("research_only", False),
    ("eligible_for_betting", True), ("kelly_eligible", True), ("eligible_for_official_pick", True),
    ("approval_status", "approved"), ("fact_schema_version", "future")])
def test_payload_revalidation_preserves_safety(field, value):
    payload = fact_payload(batter())
    payload[field] = value
    with pytest.raises(ValueError):
        fact_from_payload(payload)


def test_bridge_preserves_identity_lineage_and_never_fabricates_gamesplayed():
    b = batter()
    summary = batting_summary(b, replace(b, mlbam_game_id="823101"))
    evidence = bridge_batter_season_hitting_evidence(summary, player_name="Fixture Player", evidence_cutoff=OBSERVED)
    assert evidence.mlbam_player_id == "700001" and (evidence.at_bats, evidence.hits) == (10, 8)
    assert evidence.games_played is None and evidence.source == "courtvision_game_ledger"
    assert set(summary.source_refs) <= set(evidence.source_refs)
    provenance = json.loads(evidence.source_refs[-1].split("=", 1)[1])
    assert provenance["distinct_batting_games"] == 2 and provenance["provider_gamesPlayed"] is None
    assert tuple(r["factual_record_hash"] for r in provenance["records"]) == summary.factual_record_hashes
    with pytest.raises(ValueError, match="precedes"):
        bridge_batter_season_hitting_evidence(summary, player_name="Fixture Player", evidence_cutoff=OBSERVED - timedelta(seconds=1))


def test_bridge_compatible_with_features_without_live_ab_cutover():
    from courtvision.sports.mlb.ab_projection import AtBatProjectionError, project_batter_at_bats
    from courtvision.sports.mlb.hits_features import assemble_batter_hits_features, BatterAtBatProjectionEvidence
    from test_mlb_ab_projection import _acquired, GENERATED
    acquired = _acquired()
    name = acquired.season_evidence.player_name
    evidence = bridge_batter_season_hitting_evidence(batting_summary(batter()), player_name=name,
                                                    evidence_cutoff=acquired.evidence_cutoff)
    acquired = replace(acquired, season_evidence=evidence)
    with pytest.raises(AtBatProjectionError, match="games_played"):
        project_batter_at_bats(acquired, generated_at=GENERATED)
    projection = BatterAtBatProjectionEvidence(mlbam_game_id=acquired.player_binding.mlbam_game_id,
        mlbam_player_id="700001", player_name=name, projected_at_bats=4,
        projection_method="fixture_only", model_version="fixture", source="fixture",
        generated_at=GENERATED, evidence_cutoff=acquired.evidence_cutoff, source_refs=("fixture:projection",))
    features = assemble_batter_hits_features(event_binding=acquired.player_binding.event_binding,
        player_binding=acquired.player_binding, season_evidence=evidence,
        lineup_evidence=acquired.lineup_evidence, projection_evidence=projection, evidence_cutoff=GENERATED)
    assert (features.season_at_bats, features.season_hits) == (5, 4)


def test_pure_pipeline_requires_no_io(monkeypatch):
    payload, bound = boxscore(), game()
    before = deepcopy(payload)
    def denied(*args, **kwargs):
        raise AssertionError("pure facts attempted I/O")
    with monkeypatch.context() as patch:
        for obj, name in ((builtins, "open"), (io, "open"), (socket, "socket"),
                          (subprocess, "Popen"), (Path, "write_bytes")):
            patch.setattr(obj, name, denied)
        facts = extract(payload, bound_game=bound)
        summary = batting_summary(facts[0])
        bridge_batter_season_hitting_evidence(summary, player_name="Fixture Player", evidence_cutoff=OBSERVED)
    assert before == payload


def test_realistic_fixture_rehearsal_trade_and_doubleheader(tmp_path):
    """Synthetic final evidence; deliberately no claim of real historical coverage."""
    store = MLBFactStore(tmp_path / "facts")
    batters, pitchers = [], []
    for index, team_id in enumerate((114, 114, 147)):
        game_id = str(823100 + index)
        bound = game(game_id, home=team_id,
                     official_date=DAY if index < 2 else DAY + timedelta(days=1))
        payload = boxscore(game_id)
        payload["teams"]["home"]["team"]["id"] = team_id
        store.publish(bound)
        for fact in extract(payload, bound_game=bound):
            store.publish(fact)
            restored = store.read(fact.role, game_id, fact.mlbam_player_id)
            (batters if fact.role == "BATTER" else pitchers).append(restored)
    b = batting_summary(*batters, batters[0])
    p = MLBPitcherSeasonFacts(season=2026, mlbam_player_id="700001", facts=tuple(pitchers))
    assert (b.distinct_batting_games, b.at_bats, b.hits, b.total_bases) == (3, 15, 12, 30)
    assert (p.distinct_pitching_games, p.outs_recorded, p.strikeouts) == (3, 57, 24)
    assert len(list((tmp_path / "facts").rglob("*.json"))) == 9
    assert b.research_only and not b.eligible_for_betting and not b.kelly_eligible
    for f in (*batters, *pitchers):
        assert f.research_only and f.approval_status == "not_approved"
        assert not f.eligible_for_betting and not f.kelly_eligible and not f.eligible_for_official_pick


def test_team_split_row_counts_cannot_inflate_distinct_game_count():
    first = batter()
    records = tuple(replace(first, mlbam_game_id=str(823100 + index),
                            team_id="114" if index < 50 else "147") for index in range(91))
    # Two overlapping supplied groups of 50 and 42 records contain 91 games.
    summary = batting_summary(*records[:50], *records[49:])
    assert summary.distinct_batting_games == 91
    assert summary.at_bats == 91 * first.at_bats and summary.hits == 91 * first.hits
