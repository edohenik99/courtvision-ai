"""Shared schedule policy and factual-inventory revision acceptance, offline."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from itertools import permutations
import json

import pytest

from courtvision.sports.mlb.data import prospective_statcast_history as history
from courtvision.sports.mlb.data.prospective_context_acquisition import EvidenceRequest, ProviderResponse
from courtvision.sports.mlb.fact_backfill import BackfillError
from courtvision.sports.mlb.fact_backfill_evidence import digest
from courtvision.sports.mlb.schedule_revisions import resolve_schedule_responses
from test_mlb_fact_backfill import Provider, feed, game, job, no_network, schedule, schedule_inventory


def inventory(*rows, start="2026-01-01", end="2026-09-24"):
    return schedule_inventory(schedule(*rows), start, end)


def test_legacy_statcast_caller_is_the_shared_contract():
    assert history._resolve_schedule_responses is resolve_schedule_responses


def test_exact_duplicate_keeps_every_occurrence_and_one_expected_game():
    result = inventory(game(), game(), game())
    assert len(result["games"]) == 1
    logical = result["games"][0]["reconciliation"]
    assert logical["observation_count"] == len(logical["observed_states"]) == 3
    assert logical["distinct_mutable_state_count"] == 1
    assert logical["revision_count"] == 0
    assert result["reconciliation_summary"]["duplicate_observation_count"] == 2


def test_scheduled_postponed_rescheduled_final_order_and_hash_are_deterministic():
    scheduled = game(state="Scheduled", day="2026-03-24")
    postponed = game(state="Postponed", day="2026-03-24")
    rescheduled = game(state="Scheduled")
    final = game()
    results = [inventory(*rows) for rows in permutations((scheduled, postponed, rescheduled, final))]
    assert len({digest(result) for result in results}) == 1
    row = results[0]["games"][0]
    assert row["officialDate"] == "2026-03-25" and row["eligible_final"]
    assert row["reconciliation"]["revision_count"] == 3
    assert row["reconciliation"]["observation_count"] == 4


def test_scheduled_to_final_has_one_identity():
    result = inventory(game(state="Scheduled"), game())
    assert len(result["games"]) == 1
    assert result["games"][0]["eligible_final"]
    assert result["games"][0]["reconciliation"]["distinct_mutable_state_count"] == 2


def test_official_date_and_start_revisions_use_only_final_date_for_window():
    old = game(day="2026-03-24", state="Postponed")
    final = game(day="2026-03-25")
    original_window = inventory(old, final, start="2026-03-24", end="2026-03-24")
    new_window = inventory(old, final, start="2026-03-25", end="2026-03-25")
    assert len(original_window["games"]) == len(new_window["games"]) == 1
    assert not original_window["games"][0]["canonical_final"]
    assert new_window["games"][0]["canonical_final"]
    assert new_window["games"][0]["officialDate"] == "2026-03-25"


@pytest.mark.parametrize("field", ["home_team_id", "away_team_id", "venue_id", "game_guid", "game_type", "season", "sport_id", "league_id"])
def test_immutable_identity_conflicts_fail_closed(field):
    first, second = game(), game()
    if field.endswith("team_id"):
        second["teams"][field.split("_")[0]]["team"]["id"] = 999
    elif field == "venue_id":
        second["venue"]["id"] = 99
    elif field == "game_guid":
        second["gameGuid"] = "conflict"
    elif field == "game_type":
        second["gameType"] = "S"
    elif field == "season":
        second["season"] = 2025
    elif field == "sport_id":
        first["sport"], second["sport"] = {"id": 1}, {"id": 2}
    else:
        first["league"], second["league"] = {"id": 103}, {"id": 104}
    with pytest.raises(BackfillError, match="IDENTITY_CONFLICT"):
        inventory(first, second)


def test_doubleheaders_remain_two_unique_games():
    first, second = game(), game(823102)
    result = inventory(first, deepcopy(first), second, deepcopy(second))
    assert [r["gamePk"] for r in result["games"]] == ["823100", "823102"]
    assert sum(r["canonical_final"] for r in result["games"]) == 2


def test_latest_capture_nonfinal_overrides_older_final_in_shared_policy():
    first, second = game(), game(state="Suspended")
    request = EvidenceRequest(request_id="fixture", evidence_class="stable_history", source_name="fixture",
        provider="mlb_statsapi", url="https://statsapi.mlb.com/api/v1/schedule?sportId=1&gameTypes=R")
    sources = []
    for hour, row in enumerate((first, second), 1):
        raw = json.dumps(schedule(row)).encode()
        observed = datetime(2026, 9, 25, hour, tzinfo=timezone.utc)
        sources.append((request, {"sha256": hashlib.sha256(raw).hexdigest(),
            "captured_at_utc": observed.isoformat(), "body_path": f"fixture/{hour}.json"},
            ProviderResponse(raw, 200, {}, observed, observed)))
    resolved, _ = resolve_schedule_responses(sources[::-1])
    selected = resolved["823100"]["selected_canonical_state"]
    assert not selected["is_final"] and selected["detailed_state"] == "Suspended"


def test_nonfinal_canonical_state_produces_no_facts(tmp_path):
    provider = Provider([game(state="Scheduled"), game(state="Postponed")])
    result, _ = job(tmp_path, provider=provider)
    assert result.verify()["game_fact_count"] == 0
    assert provider.calls == ["regular-season-inventory"]


def test_revisions_publish_once_and_do_not_inflate_coverage_or_estimate(tmp_path):
    first, target = game(), game(823101, "2026-03-26")
    prior = game(day="2026-03-24", state="Postponed")
    provider = Provider([prior, first, deepcopy(first), target, deepcopy(target)])
    result, _ = job(tmp_path, provider=provider)
    state = result.verify()
    assert state["expected_final_game_pks"] == ["823100"]
    assert state["game_fact_count"] == 1 and state["batter_fact_count"] == 2
    canonical = result.store.read("GAME", "823100")
    resolution = result.inventory()["games"][0]["reconciliation"]
    assert any(ref.endswith(digest(resolution)) and ref.startswith("cv-schedule-reconciliation:")
               for ref in canonical.source_refs)
    index, coverage = result.batter_coverage("700001", "823101")
    assert index["complete"] and len(coverage.expected_records) == 1
    assert coverage.expected_records[0].mlbam_game_id == "823100"
    gap = result.gap_report()
    assert gap["total_expected_final_games"] == 2
    assert gap["full_backfill_estimated_requests"] == 1
    assert len(provider.calls) == 2
    result.resume(provider)
    assert len(provider.calls) == 2


def test_conflicting_equal_state_scores_fail_closed():
    first, second = game(), game()
    second["teams"]["home"]["score"] += 1
    with pytest.raises(ValueError, match="ambiguous source content"):
        inventory(first, second)


def test_completed_early_is_not_omitted_from_expected_coverage(tmp_path):
    early = game(823102, "2026-03-26")
    early["status"].update(detailedState="Completed Early", statusCode="FR")
    provider = Provider([game(), early])
    result, _ = job(tmp_path, provider=provider)
    gap = result.gap_report()
    assert gap["total_expected_final_games"] == 2
    assert gap["full_backfill_estimated_requests"] == 1
    assert gap["unsupported_fact_finality_game_pks"] == []
    index = result.coverage_index(through="2026-03-26")
    assert not index["complete"]
    assert index["unknown_participation_game_pks"] == ["823102"]


def test_completed_early_in_pilot_materializes_with_corroboration(tmp_path):
    early = game()
    early["status"].update(detailedState="Completed Early", statusCode="FR")
    provider = Provider([early])
    result, _ = job(tmp_path, fetch=False, materialize=False)
    result.fetch(provider)
    result.materialize()
    assert provider.calls == ["regular-season-inventory", "final-feed-823100"]
    assert result.verify()["expected_final_game_pks"] == ["823100"]
    assert result.verify()["missing_count"] == 0
    assert result.store.read("GAME", "823100").game_status == "final"
