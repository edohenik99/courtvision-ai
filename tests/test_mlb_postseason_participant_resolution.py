"""Evidence-shaped postseason slot refinement; every provider stays offline."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from itertools import permutations
import json

import pytest

from courtvision.sports.mlb.data.prospective_context_acquisition import EvidenceRequest, ProviderResponse
from courtvision.sports.mlb.fact_backfill_evidence import digest
from courtvision.sports.mlb.schedule_revisions import (
    resolve_schedule_responses, selected_schedule_game_payload,
)
from test_mlb_fact_backfill import game, no_network, schedule


def participant(team_id, name):
    return {"id": team_id, "name": name, "link": f"/api/v1/teams/{team_id}"}


def postseason_row(side="away"):
    row = game(849828, "2026-10-03", "Scheduled")
    row.update(gameType="D", season="2026", gameGuid="621a974a-b250-4410-992f-37cb6c829743",
               gameDate="2026-10-03T20:00:00Z", venue={"id": 22, "name": "UNIQLO Field at Dodger Stadium"})
    row["status"] = {"abstractGameState": "Preview", "codedGameState": "S",
                     "detailedState": "Scheduled", "statusCode": "S",
                     "startTimeTBD": False, "abstractGameCode": "P"}
    row["teams"][side]["team"] = participant(5532, "ATL/PHI")
    row["teams"]["home" if side == "away" else "away"]["team"] = participant(119, "Los Angeles Dodgers")
    return row


def source(row, hour, *, provider="mlb_statsapi", url="https://statsapi.mlb.com/api/v1/schedule?sportId=1"):
    raw = json.dumps(schedule(row)).encode()
    captured = datetime(2026, 10, 2, hour, tzinfo=timezone.utc)
    request = EvidenceRequest(request_id=f"offline-{hour}", evidence_class="stable_history",
                              source_name="offline", provider=provider, url=url)
    return (request, {"sha256": hashlib.sha256(raw).hexdigest(),
                      "captured_at_utc": captured.isoformat(), "body_path": f"offline/{hour}.bin"},
            ProviderResponse(raw, 200, {}, captured, captured))


def resolved_row(row, side="away", team_id=144, name="Atlanta Braves"):
    result = deepcopy(row)
    result["teams"][side]["team"] = participant(team_id, name)
    return result


def assert_conflict(*rows):
    resolved, summary = resolve_schedule_responses([source(row, i) for i, row in enumerate(rows)])
    assert "849828" not in resolved
    assert summary["identity_conflict_count"] == 1
    assert not summary.get("participant_resolution_count", 0)


def test_preserved_849828_participant_objects_and_identity():
    old = postseason_row()
    new = resolved_row(old)
    resolved, summary = resolve_schedule_responses([source(old, 0), source(new, 4), source(new, 17)])
    logical = resolved["849828"]
    assert summary["identity_conflict_count"] == 0
    assert summary["participant_resolution_count"] == logical["participant_resolution_count"] == 1
    assert logical["identity"]["away_team_id"] == "144"
    assert logical["identity"]["away_team_name"] == "Atlanta Braves"
    assert logical["identity"]["home_team_id"] == "119"
    assert logical["identity"]["game_guid"] == old["gameGuid"]
    assert any(o["identity"]["away_team_id"] == "5532" for o in logical["observed_states"])
    refinement = logical["participant_resolutions"][0]
    assert refinement["placeholder_team"] == old["teams"]["away"]["team"]
    assert refinement["resolved_team"] == new["teams"]["away"]["team"]
    assert refinement["first_placeholder_observation"]["source_request_id"] == "offline-0"
    assert refinement["concrete_resolution_observation"]["source_request_id"] == "offline-4"
    # Old and new have the same schedule state; the concrete slot breaks no tie
    # in scores/content and the exact concrete game payload is selected.
    assert selected_schedule_game_payload([(old["officialDate"], old), (new["officialDate"], new)], logical) == new


def test_repeated_placeholders_and_concrete_are_order_and_hash_stable():
    old = postseason_row()
    new = resolved_row(old)
    captures = [source(old, 0), source(old, 1), source(new, 2), source(new, 3)]
    outputs = [resolve_schedule_responses(order) for order in permutations(captures)]
    assert len({digest({"games": games, "summary": summary}) for games, summary in outputs}) == 1
    assert outputs[0][0]["849828"]["observation_count"] == 4


@pytest.mark.parametrize("side", ["home", "away"])
@pytest.mark.parametrize("game_type", ["F", "D", "L", "W"])
def test_structural_family_is_not_one_game_or_placeholder_id(side, game_type):
    old = postseason_row(side)
    old.update(gamePk=849900, gameGuid="other-official-slot", gameType=game_type)
    old["teams"][side]["team"] = participant(5900, "NYM/MIL")
    new = resolved_row(old, side, 158, "Milwaukee Brewers")
    resolved, summary = resolve_schedule_responses([source(old, 0), source(new, 1)])
    assert summary["identity_conflict_count"] == 0
    assert resolved["849900"]["identity"][side + "_team_id"] == "158"


@pytest.mark.parametrize("game_type", ["R", "D"])
def test_concrete_team_change_never_refines(game_type):
    first = resolved_row(postseason_row())
    first["gameType"] = game_type
    assert_conflict(first, resolved_row(first, team_id=143, name="Philadelphia Phillies"))


def test_placeholder_cannot_resolve_to_two_competing_clubs():
    old = postseason_row()
    assert_conflict(old, resolved_row(old), resolved_row(old, team_id=143, name="Philadelphia Phillies"))


def test_concrete_to_placeholder_is_backward_even_if_input_order_reversed():
    old = postseason_row()
    captures = [source(resolved_row(old), 0), source(old, 1)]
    for order in permutations(captures):
        resolved, summary = resolve_schedule_responses(order)
        assert not resolved and summary["identity_conflict_count"] == 1


def test_placeholder_cannot_return_after_resolution():
    old = postseason_row()
    assert_conflict(old, resolved_row(old), old)


def test_changed_placeholder_candidates_fail_before_resolution():
    old = postseason_row()
    different = deepcopy(old)
    different["teams"]["away"]["team"] = participant(5900, "NYM/MIL")
    assert_conflict(old, different, resolved_row(different, team_id=158, name="Milwaukee Brewers"))


def test_changed_placeholder_name_with_same_id_fails():
    old = postseason_row()
    changed = deepcopy(old)
    changed["teams"]["away"]["team"]["name"] = "ATL/NYM"
    assert_conflict(old, changed, resolved_row(old))


def test_unresolved_placeholder_cannot_change_candidate_identity_with_same_id():
    old = postseason_row()
    changed = deepcopy(old)
    changed["teams"]["away"]["team"]["name"] = "NYM/MIL"
    assert_conflict(old, changed)


def test_repeated_unresolved_placeholder_remains_one_game_without_resolution():
    old = postseason_row()
    resolved, summary = resolve_schedule_responses([source(old, 0), source(old, 1)])
    assert summary["identity_conflict_count"] == 0
    assert resolved["849828"]["identity"]["away_team_id"] == "5532"
    assert not resolved["849828"].get("participant_resolution_count")


def test_equal_capture_time_cannot_prove_a_transition():
    old = postseason_row()
    resolved, summary = resolve_schedule_responses([source(old, 0), source(resolved_row(old), 0)])
    assert not resolved and summary["identity_conflict_count"] == 1


@pytest.mark.parametrize("name", ["ATL", "ATL/XXX", "ATL/ATL", "ATL/PHI/NYM", "atl/phi",
                                   " ATL/PHI", "ATL /PHI", "Atlanta/Philadelphia", "ATH/OAK"])
def test_malformed_or_unobserved_placeholder_forms_conflict(name):
    old = postseason_row()
    old["teams"]["away"]["team"]["name"] = name
    assert_conflict(old, resolved_row(old))


@pytest.mark.parametrize("change", ["known_id", "bad_link", "missing_link", "extra_field"])
def test_placeholder_requires_the_observed_provider_object(change):
    old = postseason_row()
    team = old["teams"]["away"]["team"]
    if change == "known_id":
        team.update(id=143, link="/api/v1/teams/143")
    elif change == "bad_link":
        team["link"] = "/api/v1/teams/9999"
    elif change == "missing_link":
        del team["link"]
    else:
        team["description"] = "unqualified format"
    assert_conflict(old, resolved_row(old))


@pytest.mark.parametrize("team_id,name", [(145, "Chicago White Sox"), (144, "Philadelphia Phillies"),
                                         (999, "Atlanta Braves")])
def test_concrete_must_be_a_canonical_member_of_the_placeholder(team_id, name):
    old = postseason_row()
    assert_conflict(old, resolved_row(old, team_id=team_id, name=name))


@pytest.mark.parametrize("provider,url", [
    ("other", "https://statsapi.mlb.com/api/v1/schedule?sportId=1"),
    ("mlb_statsapi", "https://other.example/api/v1/schedule?sportId=1"),
    ("mlb_statsapi", "https://statsapi.mlb.com/api/v1/schedule?sportId=2"),
])
def test_resolution_requires_explicit_first_party_mlb_context(provider, url):
    old = postseason_row()
    resolved, summary = resolve_schedule_responses([
        source(old, 0, provider=provider, url=url), source(resolved_row(old), 1)])
    assert not resolved and summary["identity_conflict_count"] == 1


def test_side_swap_is_not_participant_resolution():
    old = postseason_row()
    swapped = resolved_row(old)
    swapped["teams"]["away"]["team"], swapped["teams"]["home"]["team"] = (
        swapped["teams"]["home"]["team"], swapped["teams"]["away"]["team"])
    assert_conflict(old, swapped)


def test_opposite_concrete_mutation_is_not_hidden():
    old = postseason_row()
    new = resolved_row(old)
    new["teams"]["home"]["team"] = participant(158, "Milwaukee Brewers")
    assert_conflict(old, new)


def test_both_slots_can_refine_independently():
    old = postseason_row()
    old["teams"]["home"]["team"] = participant(5900, "NYM/MIL")
    new = resolved_row(old)
    new["teams"]["home"]["team"] = participant(158, "Milwaukee Brewers")
    resolved, summary = resolve_schedule_responses([source(old, 0), source(new, 1)])
    assert summary["participant_resolution_count"] == 2
    assert {r["side"] for r in resolved["849828"]["participant_resolutions"]} == {"home", "away"}


def test_resolution_cannot_put_same_concrete_club_in_both_slots():
    old = postseason_row()
    old["teams"]["home"]["team"] = participant(144, "Atlanta Braves")
    assert_conflict(old, resolved_row(old))


@pytest.mark.parametrize("field", ["venue", "gameType", "season", "gameGuid", "sport", "league"])
def test_unrelated_immutable_identity_drift_still_conflicts(field):
    old = postseason_row()
    new = resolved_row(old)
    if field == "venue":
        new["venue"]["id"] = 99
    elif field == "gameType":
        new[field] = "L"
    elif field == "season":
        new[field] = "2025"
    elif field == "gameGuid":
        new[field] = "different-slot"
    else:
        old[field], new[field] = {"id": 1 if field == "sport" else 103}, {"id": 2 if field == "sport" else 104}
    assert_conflict(old, new)


def test_concrete_equal_state_content_ambiguity_still_fails_closed():
    old = postseason_row()
    new = resolved_row(old)
    conflict = deepcopy(new)
    conflict["teams"]["home"]["score"] += 1
    logical = resolve_schedule_responses([source(old, 0), source(new, 1), source(conflict, 2)])[0]["849828"]
    with pytest.raises(ValueError, match="ambiguous source content"):
        selected_schedule_game_payload([(r["officialDate"], r) for r in (old, new, conflict)], logical)


@pytest.mark.parametrize("query", [
    "sportId=2", "sportId=1&sportId=2", "sportId=1&sportId=1",
    "sportId=", "", "sportId=1&gameTypes=R", "sportId=1&gameTypes=L",
    "sportId=1&gameTypes=D&gameTypes=R", "sportId=1&gameTypes=",
    "sportId=1&gameTypes=D,", "sportId=1&gameTypes=D,UNKNOWN",
    "sportId=1&leagueId=104", "sportId=1&leagueId=",
    "sportId=1&leagueId=103&leagueId=104",
])
def test_row_context_cannot_override_incompatible_or_ambiguous_query(query):
    old = postseason_row()
    old.update(sport={"id": 1}, league={"id": 103})
    new = resolved_row(old)
    url = "https://statsapi.mlb.com/api/v1/schedule?" + query
    resolved, summary = resolve_schedule_responses([
        source(old, 0, url=url), source(new, 1, url=url)])
    assert not resolved
    assert summary["identity_conflict_count"] == 1
    assert not summary.get("participant_resolution_count", 0)


@pytest.mark.parametrize("query", [
    "sportId=1", "sportId=1&gameTypes=D", "sportId=1&gameTypes=D,L",
    "sportId=1&gameTypes=R,D", "sportId=1&leagueId=103",
])
def test_compatible_explicit_query_still_qualifies_the_resolution(query):
    old = postseason_row()
    old.update(sport={"id": 1}, league={"id": 103})
    new = resolved_row(old)
    url = "https://statsapi.mlb.com/api/v1/schedule?" + query
    resolved, summary = resolve_schedule_responses([
        source(old, 0, url=url), source(new, 1, url=url)])
    assert resolved["849828"]["identity"]["away_team_id"] == "144"
    assert summary["identity_conflict_count"] == 0
    assert summary["participant_resolution_count"] == 1
