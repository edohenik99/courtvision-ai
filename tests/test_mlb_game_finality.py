"""Corroborated factual finality; exact status observations with synthetic stats."""
from copy import deepcopy
from dataclasses import replace

import pytest

from courtvision.core.candidates import EventIdentity, IdentityStatus
from courtvision.sports.mlb.batting_results import validate_boxscore_binding
from courtvision.sports.mlb.data import prospective_statcast_history as history
from courtvision.sports.mlb.fact_backfill import BackfillError, facts_from_feed
from courtvision.sports.mlb.fact_backfill_evidence import digest, publish_document
from courtvision.sports.mlb.game_finality import classify_game_finality
from courtvision.sports.mlb.schedule_revisions import is_final_schedule_state
from test_mlb_fact_backfill import Provider, feed, game, job, no_network, schedule, schedule_inventory


# Verbatim statuses in preserved response d4f73550...4e7730c, captured
# 2026-09-25T06:36:14.873757+00:00. FR is not assigned an expanded meaning.
OBSERVED_STATUS = {
    "abstractGameState": "Final", "codedGameState": "F",
    "detailedState": "Completed Early", "statusCode": "FR",
    "startTimeTBD": False, "reason": "Rain", "abstractGameCode": "F",
}
# Exact gameData.status from preserved final-feed-824295, journal sequence 119,
# raw SHA-256 b4451223647424b0698668476148094e1d105bf113c674e19ec98efc3e51eee5.
OBSERVED_QUALIFIED_STATUS = {
    "abstractGameState": "Final", "codedGameState": "F",
    "detailedState": "Completed Early: Rain", "statusCode": "FR",
    "startTimeTBD": False, "reason": "Rain", "abstractGameCode": "F",
}
OBSERVED_GAMES = [
    (824295, "2026-04-04", "2026-04-04T17:10:00Z", 116, 138),
    (824807, "2026-08-02", "2026-08-02T17:35:00Z", 110, 143),
]


def early_game():
    row = game()
    row["status"] = deepcopy(OBSERVED_STATUS)
    return row


def test_exact_reason_qualified_status_preserves_raw_provider_values():
    raw = deepcopy(OBSERVED_QUALIFIED_STATUS)
    before = deepcopy(raw)
    result = classify_game_finality(raw)
    assert result.canonical_state == "FINAL"
    assert result.detailed_state == "Completed Early: Rain"
    assert result.status_code == "FR" and result.coded_state == "F"
    assert "codedGameState=F" in result.decision_reason
    assert "Rain" not in result.decision_reason and "statusCode=FR" not in result.decision_reason
    assert raw == before


@pytest.mark.parametrize("status", [
    {"detailedState": "Completed Early: Rain"},
    {"detailedState": "Completed Early: Rain", "codedGameState": "F"},
    {"abstractGameState": "Final", "detailedState": "Completed Early: Rain"},
    {"abstractGameState": "Final", "detailedState": "Completed Early: Rain", "statusCode": "F"},
    {"abstractGameState": "Final", "detailedState": "Completed Early: Rain", "statusCode": "FR"},
    {"abstractGameState": "Final", "detailedState": "Completed Early: Rain", "abstractGameCode": "F"},
    {"statusCode": "FR", "reason": "Rain"},
])
def test_qualified_completed_early_requires_abstract_final_and_coded_f(status):
    assert classify_game_finality(status).canonical_state == "AMBIGUOUS"


def test_qualifier_and_separate_reason_are_not_finality_witnesses():
    # Synthetic description within the observed syntax; not a new provider observation.
    status = {"abstractGameState": "Final", "detailedState": "Completed Early: Fixture reason",
              "codedGameState": "F"}
    assert classify_game_finality(status).is_final
    del status["codedGameState"]
    assert not classify_game_finality(status).is_final
    assert not classify_game_finality({"abstractGameState": "Final", "reason": "Rain"}).is_final


@pytest.mark.parametrize("detailed", [
    "Completed Early:", "Completed Early: ", "Completed Early:   ",
    "Completed Early Rain", "Completed Early - Rain", "Completed Early (7)",
    "Completed Early:Rain", "Completed Early : Rain", "Completed Earlyish: Rain",
    "Unknown: Completed Early: Rain", "Final: Rain", "Game Over: Rain",
])
def test_empty_qualifier_and_unobserved_terminal_forms_remain_ambiguous(detailed):
    status = {**OBSERVED_QUALIFIED_STATUS, "detailedState": detailed}
    assert classify_game_finality(status).canonical_state == "AMBIGUOUS"


@pytest.mark.parametrize("field,value", [
    ("abstractGameState", "Live"), ("codedGameState", "I"),
    ("statusCode", "I"), ("statusCode", "DR"), ("abstractGameCode", "I"),
    ("detailedState", "Suspended"), ("detailedState", "Postponed"),
    ("detailedState", "Cancelled"), ("detailedState", "Canceled"),
    ("detailedState", "In Progress"), ("detailedState", "Delayed"),
    ("detailedState", "Scheduled"), ("detailedState", "Warmup"),
    ("detailedState", "Pre-Game"), ("detailedState", "Preview"),
])
def test_qualified_status_contradictions_remain_conflicts(field, value):
    assert classify_game_finality({**OBSERVED_QUALIFIED_STATUS, field: value}).canonical_state == "CONFLICT"


@pytest.mark.parametrize("change", [
    {}, {"abstractGameState": "Live"}, {"codedGameState": "I"},
    {"abstractGameCode": "I"}, {"statusCode": "DR"}, {"statusCode": "FX"},
    {"codedGameState": None}, {"detailedState": "Completed Early Rain"},
])
@pytest.mark.parametrize("surface", ["root", "gameData", "boxscore", "schedule"])
def test_reason_qualified_schedule_feed_compatibility_and_binding(change, surface):
    # Exact observed identity/status with explicitly synthetic scores and player stats.
    row = game(824295, "2026-04-04")
    row["teams"]["home"]["team"]["id"] = 116
    row["teams"]["away"]["team"]["id"] = 138
    row["status"] = deepcopy(OBSERVED_STATUS)
    inv = schedule_inventory(schedule(row), "2026-01-01", "2026-09-24")
    selected = inv["games"][0]
    payload = feed(row)
    payload["gameData"]["status"] = deepcopy(OBSERVED_QUALIFIED_STATUS)
    target = {"root": payload, "gameData": payload["gameData"],
              "boxscore": payload["liveData"]["boxscore"], "schedule": selected}[surface]
    target["status"] = {**OBSERVED_QUALIFIED_STATUS, **change}
    original = deepcopy(payload)
    event = EventIdentity("824295", "fixture", IdentityStatus.RESOLVED, "824295")
    record = {"claim": {"request_id": "fixture"},
              "response": {"responded_at": "2026-09-25T01:00:00+00:00", "sha256": "a" * 64}}
    if change:
        with pytest.raises(ValueError, match="conflicts"):
            validate_boxscore_binding(target, event, "final")
        with pytest.raises(ValueError):
            facts_from_feed(selected, payload, record, record)
    else:
        assert validate_boxscore_binding(target, event, "final") == "final"
        facts = facts_from_feed(selected, payload, record, record)
        assert {f.role for f in facts} == {"GAME", "BATTER", "PITCHER"}
        assert all(f.game_status == "final" and f.mlbam_game_id == "824295" for f in facts)
        assert payload["gameData"]["status"] == OBSERVED_QUALIFIED_STATUS
    assert payload == original


@pytest.mark.parametrize("detailed", ["Final", "Game Over", "Completed Early"])
def test_abstract_final_requires_supported_terminal_corroboration(detailed):
    status = {"abstractGameState": "Final", "detailedState": detailed}
    result = classify_game_finality(status)
    assert result.canonical_state == "FINAL"
    assert result.detailed_state == detailed
    assert "recognized_detailed_state" in result.decision_reason


@pytest.mark.parametrize("status,state", [
    ({"detailedState": "Completed Early"}, "AMBIGUOUS"),
    ({"abstractGameState": "Live", "detailedState": "Completed Early"}, "CONFLICT"),
    ({"abstractGameState": "Final"}, "AMBIGUOUS"),
    ({"abstractGameState": "Final", "detailedState": "Postponed"}, "CONFLICT"),
    ({"abstractGameState": "Final", "detailedState": "Suspended"}, "CONFLICT"),
    ({"abstractGameState": "Final", "detailedState": "Unknown", "codedGameState": "F"}, "AMBIGUOUS"),
    ({"abstractGameState": "Final", "statusCode": "FR"}, "AMBIGUOUS"),
    ({"abstractGameState": "Final", "statusCode": "FX"}, "AMBIGUOUS"),
    ({"abstractGameState": "Final", "statusCode": "F"}, "FINAL"),
    ({"abstractGameState": "Final", "codedGameState": "F"}, "FINAL"),
    ({"abstractGameState": "Final", "abstractGameCode": "F"}, "AMBIGUOUS"),
    ({"abstractGameState": "Final", "detailedState": "Completed Early", "statusCode": "FR"}, "AMBIGUOUS"),
    ({}, "AMBIGUOUS"),
])
def test_missing_unknown_conflicting_and_code_corroboration(status, state):
    assert classify_game_finality(status).canonical_state == state


@pytest.mark.parametrize("key", ["abstractGameState", "detailedState"])
@pytest.mark.parametrize("metadata", [{}, {"startTimeTBD": False}, {"reason": "Rain", "startTimeTBD": False}])
def test_partial_literal_final_only_checks_existing_caller_bound_status(key, metadata):
    status = {**metadata, key: "Final"}
    event = EventIdentity("824295", "fixture", IdentityStatus.RESOLVED, "824295")
    assert classify_game_finality(status).canonical_state == "AMBIGUOUS"
    assert validate_boxscore_binding({"status": status}, event, "final") == "final"
    with pytest.raises(ValueError, match="conflicts"):
        validate_boxscore_binding({"status": status}, event, "live")
    with pytest.raises(ValueError, match="conflicts"):
        validate_boxscore_binding({"status": {key: "Completed Early"}}, event, "final")


@pytest.mark.parametrize("key", ["abstractGameState", "detailedState"])
@pytest.mark.parametrize("extra", [
    {"codedGameState": "I"}, {"statusCode": "FX"}, {"abstractGameCode": None},
])
def test_partial_final_with_metadata_does_not_ignore_finality_fields(key, extra):
    status = {"startTimeTBD": False, key: "Final", **extra}
    event = EventIdentity("824295", "fixture", IdentityStatus.RESOLVED, "824295")
    assert not classify_game_finality(status).is_final
    with pytest.raises(ValueError, match="conflicts"):
        validate_boxscore_binding({"status": status}, event, "final")


@pytest.mark.parametrize("metadata", [{}, {"startTimeTBD": False, "reason": "Rain"}])
def test_partial_abstract_final_never_qualifies_provider_acquisition(tmp_path, metadata):
    row = early_game()
    row["status"] = {"abstractGameState": "Final", **metadata}
    provider = Provider([row])
    result, _ = job(tmp_path, fetch=False, materialize=False)
    with pytest.raises(BackfillError, match="unresolved finality"):
        result.fetch(provider)
    assert provider.calls == ["regular-season-inventory"]
    assert result.verify()["game_fact_count"] == 0


@pytest.mark.parametrize("detailed", ["Postponed", "Suspended", "Scheduled", "In Progress", "Delayed"])
def test_nonfinal_status_cannot_be_replaced_by_scores_stats_or_age(detailed):
    status = {"abstractGameState": "Live" if detailed == "In Progress" else "Preview",
              "detailedState": detailed, "score": 10, "inning": 9, "hits": 5, "year": 1900}
    assert classify_game_finality(status).canonical_state == "NON_FINAL"


@pytest.mark.parametrize("field,value", [
    ("abstractGameState", "Live"), ("detailedState", "Postponed"),
    ("codedGameState", "I"), ("codedGameState", "D"), ("statusCode", "S"),
    ("statusCode", "DR"), ("abstractGameCode", "I"),
    ("statusCode", "FX"), ("codedGameState", "FR"), ("statusCode", ""),
    ("codedGameState", None), ("detailedState", "Unknown"),
])
def test_exceptional_status_contradictions_and_unknowns_fail_binding(field, value):
    raw = {**OBSERVED_STATUS, field: value}
    result = classify_game_finality(raw)
    assert result.canonical_state in {"CONFLICT", "AMBIGUOUS"}
    event = EventIdentity("824295", "fixture", IdentityStatus.RESOLVED, "824295")
    with pytest.raises(ValueError, match="conflicts"):
        validate_boxscore_binding({"gamePk": 824295, "status": raw}, event, "final")


@pytest.mark.parametrize("game_id,day,start,home,away", OBSERVED_GAMES)
def test_exact_observed_exceptional_game(game_id, day, start, home, away):
    row = game(game_id, day)
    row.update(gameDate=start, season="2026", status=deepcopy(OBSERVED_STATUS))
    row["teams"]["home"]["team"]["id"] = home
    row["teams"]["away"]["team"]["id"] = away
    inv = schedule_inventory(schedule(row), "2026-01-01", "2026-09-24")
    selected = inv["games"][0]["reconciliation"]["selected_canonical_state"]
    assert selected["is_final"] and selected["status_payload"] == OBSERVED_STATUS
    result = classify_game_finality(row["status"])
    assert result.is_final and result.status_code == "FR" and result.coded_state == "F"
    assert "codedGameState=F" in result.decision_reason
    assert "statusCode=FR" not in result.decision_reason
    event = EventIdentity(str(game_id), "fixture", IdentityStatus.RESOLVED, str(game_id))
    assert validate_boxscore_binding(row, event, "final") == "final"


@pytest.mark.parametrize("detailed,abstract,expected", [
    ("Final", "Final", True), ("Game Over", "Final", True),
    ("Completed Early", "Final", True), ("Postponed", "Final", False),
    ("Scheduled", "Preview", False), ("Suspended", "Live", False),
    ("In Progress", "Live", False),
])
def test_historical_statcast_uses_identical_shared_finality(detailed, abstract, expected):
    state = {"abstract_state": abstract, "detailed_state": detailed,
             "coded_state": "F" if expected else "D", "status_code": "F" if expected else "DI"}
    assert history._is_final_schedule_state is is_final_schedule_state
    assert history._is_final_schedule_state(state) is expected


def test_completed_early_materializes_normalized_facts_and_retains_provenance(tmp_path):
    row = early_game()
    provider = Provider([row])
    provider.values["final-feed-823100"]["liveData"]["boxscore"]["status"] = deepcopy(OBSERVED_STATUS)
    result, _ = job(tmp_path, provider=provider)
    records = result.journal.records()
    facts = result._expected(result.inventory()["games"][0], records)
    assert {f.role for f in facts} == {"GAME", "BATTER", "PITCHER"}
    assert all(f.game_status == "final" and result._matches(f) for f in facts)
    assert facts[0].source_hash == digest(row)
    assert any("raw-sha256:" + records[0]["response"]["sha256"] in ref for ref in facts[0].source_refs)
    assert result.journal.payload(records[0])["dates"][0]["games"][0]["status"] == OBSERVED_STATUS
    assert result.journal.payload(records[1])["gameData"]["status"] == OBSERVED_STATUS
    with pytest.raises(ValueError, match="explicitly final"):
        replace(facts[0], game_status="Completed Early")


@pytest.mark.parametrize("location", ["feed", "gameData", "boxscore"])
def test_contradictory_embedded_feed_status_blocks_all_fact_publication(tmp_path, location):
    provider = Provider([early_game()])
    supplied = provider.values["final-feed-823100"]
    target = {"feed": supplied, "gameData": supplied["gameData"],
              "boxscore": supplied["liveData"]["boxscore"]}[location]
    target["status"] = {**OBSERVED_STATUS, "codedGameState": "I"}
    result, _ = job(tmp_path, fetch=False, materialize=False)
    with pytest.raises(ValueError):
        result.fetch(provider)
    assert result.verify()["game_fact_count"] == 0
    with pytest.raises(BackfillError):
        result.materialize()


@pytest.mark.parametrize("detailed", ["Unknown", "Postponed"])
def test_unresolved_finality_blocks_fetch_and_coverage(tmp_path, detailed):
    row = early_game()
    row["status"]["detailedState"] = detailed
    provider = Provider([row])
    result, _ = job(tmp_path, fetch=False, materialize=False)
    with pytest.raises(BackfillError, match="unresolved finality"):
        result.fetch(provider)
    assert provider.calls == ["regular-season-inventory"]
    index = result.coverage_index(through="2026-03-25")
    assert not index["complete"] and index["unresolved_finality_game_pks"] == ["823100"]
    gap = result.gap_report()
    assert gap["ambiguous_finality_game_pks"] + gap["finality_conflict_game_pks"] == ["823100"]


def test_legacy_false_eligibility_hint_revalidated_without_inventory_rewrite(tmp_path):
    provider = Provider([early_game()])
    result, _ = job(tmp_path, fetch=False, materialize=False)
    record = result.journal.capture(result._request(), provider)
    old = result._inventory_document(record)
    old["games"][0]["eligible_final"] = False
    path = result.root / "inventory.json"
    publish_document(path, old)
    before = path.read_bytes()
    result._checkpoint()
    result.fetch(provider)
    assert result.materialize()["status"] == "COMPLETE"
    assert result.resume(provider)["status"] == "COMPLETE"
    assert result.reconcile_inventory()["status"] == "COMPLETE"
    assert result.inventory() == old and path.read_bytes() == before
    assert len(provider.calls) == 2
    assert result.gap_report()["unsupported_fact_finality_game_pks"] == []


@pytest.mark.parametrize("tamper", ["raw_status", "canonical_final", "true_hint"])
def test_inventory_hint_compatibility_never_trusts_other_changed_evidence(tmp_path, tamper):
    provider = Provider([early_game() if tamper != "true_hint" else game(state="Scheduled")])
    result, _ = job(tmp_path, fetch=False, materialize=False)
    record = result.journal.capture(result._request(), provider)
    old = result._inventory_document(record)
    row = old["games"][0]
    row["eligible_final"] = tamper == "true_hint"
    if tamper == "raw_status":
        row["status"]["reason"] = "changed"
    elif tamper == "canonical_final":
        row["canonical_final"] = False
    publish_document(result.root / "inventory.json", old)
    with pytest.raises(BackfillError, match="differs from preserved schedule"):
        result.inventory()
