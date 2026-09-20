"""Offline provider-shape contracts; all payloads are synthetic in-memory data."""
from __future__ import annotations

import builtins
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import socket
import subprocess
from types import MappingProxyType

import pytest
import requests

from courtvision.core.candidates import (
    CandidateProvenance, EventIdentity, IdentityStatus, ParticipantIdentity,
)
from courtvision.sports.mlb.batter_hits import (
    BatterHitsBaselineFeatures, BatterHitsSourceEvidence,
    assemble_batter_hits_candidate, batter_hits_source_evidence_from_market_record,
    compute_batter_hits_probability,
)
from courtvision.sports.mlb.providers.the_odds_api_market_adapter import (
    PROVIDER_MARKET_MAPPING,
    normalize_mlb_event_odds,
)


COLLECTED = datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc)
UPDATED = datetime(2026, 9, 20, 15, 55, tzinfo=timezone.utc)
START = datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc)
SOURCE_REFS = ("synthetic-fixture:event-odds-v1",)
# Independent expected provider contract, not generated from adapter tables.
EXPECTED_MARKETS = {
    "batter_hits": ("batter_hits", "MAIN"),
    "batter_total_bases": ("batter_total_bases", "MAIN"),
    "batter_rbis": ("batter_rbis", "MAIN"),
    "batter_runs_scored": ("batter_runs", "MAIN"),
    "batter_walks": ("batter_walks", "MAIN"),
    "batter_strikeouts": ("batter_strikeouts", "MAIN"),
    "batter_stolen_bases": ("batter_stolen_bases", "MAIN"),
    "pitcher_strikeouts": ("pitcher_strikeouts", "MAIN"),
    "pitcher_hits_allowed": ("pitcher_hits_allowed", "MAIN"),
    "pitcher_walks": ("pitcher_walks_allowed", "MAIN"),
    "pitcher_earned_runs": ("pitcher_earned_runs", "MAIN"),
    "pitcher_outs": ("pitcher_outs_recorded", "MAIN"),
    "batter_hits_alternate": ("batter_hits", "ALTERNATE"),
    "batter_total_bases_alternate": ("batter_total_bases", "ALTERNATE"),
    "batter_rbis_alternate": ("batter_rbis", "ALTERNATE"),
    "batter_runs_scored_alternate": ("batter_runs", "ALTERNATE"),
    "batter_walks_alternate": ("batter_walks", "ALTERNATE"),
    "batter_strikeouts_alternate": ("batter_strikeouts", "ALTERNATE"),
    "pitcher_strikeouts_alternate": ("pitcher_strikeouts", "ALTERNATE"),
    "pitcher_hits_allowed_alternate": ("pitcher_hits_allowed", "ALTERNATE"),
    "pitcher_walks_alternate": ("pitcher_walks_allowed", "ALTERNATE"),
    "pitcher_earned_runs_alternate": ("pitcher_earned_runs", "ALTERNATE"),
    "pitcher_outs_alternate": ("pitcher_outs_recorded", "ALTERNATE"),
}


def _outcome(**updates):
    return {"description": "Synthetic Batter Alpha", "name": "Over", "point": 0.5,
            "price": 110, **updates}


def _market(key="batter_hits", outcomes=None, **updates):
    return {"key": key, "last_update": "2026-09-20T15:55:00Z",
            "outcomes": [_outcome()] if outcomes is None else outcomes, **updates}


def _event(key="batter_hits"):
    return {"id": "provider-event-fixture-001", "sport_key": "baseball_mlb",
            "sport_title": "MLB", "commence_time": "2026-09-20T23:00:00Z",
            "home_team": "Synthetic Home Club", "away_team": "Synthetic Away Club",
            "bookmakers": [{"key": "draftkings", "title": "DraftKings",
                            "last_update": "2026-09-20T15:59:00Z",
                            "markets": [_market(key)]}]}


def _multi_event():
    event = _event()
    event["bookmakers"][0]["markets"] = [
        _market(key, [_outcome(), _outcome(description="Synthetic Participant Beta",
                                          name="Under", price=-120)])
        for key, (_, variant) in EXPECTED_MARKETS.items() if variant == "MAIN"
    ]
    event["bookmakers"].append({
        "key": "fanduel", "title": "FanDuel", "last_update": "2026-09-20T15:58:00Z",
        "markets": [_market("batter_hits", [_outcome(price=120)]),
                    _market("batter_hits_alternate", [_outcome(point=1.5, price=210)]),
                    _market("pitcher_strikeouts_alternate", [
                        _outcome(description="Synthetic Pitcher Gamma", point=6.5, price=150)])],
    })
    return event


def _normalize(payload, **updates):
    return normalize_mlb_event_odds(payload, collected_at=updates.get("collected_at", COLLECTED),
                                    source_refs=updates.get("source_refs", SOURCE_REFS))


def _reverse_arrays(event):
    result = deepcopy(event)
    result["bookmakers"].reverse()
    for book in result["bookmakers"]:
        book["markets"].reverse()
        for market in book["markets"]:
            market["outcomes"].reverse()
    return result


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


def _has_diagnostic(batch, category, field):
    return any(d.category == category and d.field == field for d in batch.diagnostics)


def test_explicit_provider_allowlist_matches_independent_market_contract():
    actual = {key: (canonical, variant.name)
              for key, (canonical, variant) in PROVIDER_MARKET_MAPPING.items()}
    assert actual == EXPECTED_MARKETS
    with pytest.raises(TypeError):
        PROVIDER_MARKET_MAPPING["invented_market"] = ("batter_hits", "MAIN")


@pytest.mark.parametrize("provider_key,expected", EXPECTED_MARKETS.items())
def test_each_documented_market_normalizes_with_explicit_variant(provider_key, expected):
    result = _normalize(_event(provider_key))
    assert not result.diagnostics
    assert len(result.records) == len(result.quotes) == 1
    record = result.records[0]
    assert (record.canonical_market_type, record.market_variant.name) == expected
    assert record.provider_market_key == provider_key
    assert result.quotes[0].market_type == expected[0]


def test_multiple_books_markets_participants_and_sides_keep_distinct_offers():
    payload = _multi_event()
    before = deepcopy(payload)
    result = _normalize(payload)
    assert payload == before
    assert len(result.records) == len(result.quotes) == 27
    assert not result.diagnostics
    assert {r.bookmaker_key for r in result.records} == {"draftkings", "fanduel"}
    assert {r.side.name for r in result.records} == {"OVER", "UNDER"}
    assert len({r.canonical_market_type for r in result.records}) == 12
    hits = [r for r in result.records if r.canonical_market_type == "batter_hits"]
    assert len(hits) == 4
    assert {r.line for r in hits} == {0.5, 1.5}
    assert {r.market_variant.name for r in hits} == {"MAIN", "ALTERNATE"}
    assert len({r.participant_name for r in result.records}) == 3
    for record, quote in zip(result.records, result.quotes, strict=True):
        expected_market, expected_variant = EXPECTED_MARKETS[record.provider_market_key]
        assert record.canonical_market_type == quote.market_type == expected_market
        assert record.market_variant.name == expected_variant
        assert quote.raw_provider_market_id == record.provider_market_key
        assert quote.raw_event_id == record.provider_event_id == payload["id"]
        assert quote.selection_name == record.participant_name
        assert quote.selection_id is None
        assert quote.line == record.line
        assert quote.american_odds == record.american_odds
        assert quote.provider == record.provider == "the_odds_api"
        assert quote.sportsbook == record.bookmaker_name
        assert quote.home_team == record.home_team == payload["home_team"]
        assert quote.away_team == record.away_team == payload["away_team"]
        assert record.source_refs == SOURCE_REFS
        assert quote.quote_timestamp == record.market_updated_at == UPDATED
        assert quote.collected_at == record.collected_at == COLLECTED
        assert quote.event_start_time == record.commence_time == START
        assert quote.quote_timestamp <= quote.collected_at < quote.event_start_time
        assert quote.mode == "research"
        assert quote.source_type == "manual"
        assert quote.is_live is False
        assert quote.eligible_for_betting is False
        assert quote.kelly_eligible is False
        assert quote.approval_status == "not_approved"


def test_deeply_immutable_mapping_input_is_supported_without_mutation():
    expected = _normalize(_multi_event())
    assert _normalize(_freeze(_multi_event())) == expected
    assert isinstance(expected.records, tuple)
    assert isinstance(expected.quotes, tuple)
    assert isinstance(expected.diagnostics, tuple)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        expected.records[0].american_odds = 999


def test_source_provenance_and_quote_timestamps_remain_independent():
    payload = _event()
    result = _normalize(payload)
    record, quote = result.records[0], result.quotes[0]
    assert record.provider == "the_odds_api"
    assert record.provider_sport_key == "baseball_mlb"
    assert record.provider_sport_title == "MLB"
    assert record.provider_event_id == "provider-event-fixture-001"
    assert record.home_team == "Synthetic Home Club"
    assert record.away_team == "Synthetic Away Club"
    assert record.participant_name == "Synthetic Batter Alpha"
    assert record.market_updated_at == UPDATED
    assert record.collected_at == COLLECTED
    assert record.commence_time == START
    assert record.source_refs == SOURCE_REFS
    assert quote.quote_timestamp == UPDATED
    assert quote.collected_at == COLLECTED
    assert quote.event_start_time == START
    assert quote.event_id == record.provider_event_id
    assert quote.source_metadata.raw_event_id == record.provider_event_id
    assert quote.source_metadata.raw_provider_market_id == record.provider_market_key
    assert quote.mode == "research"
    assert quote.source_type == "manual"
    assert quote.eligible_for_betting is False
    assert quote.kelly_eligible is False
    assert quote.approval_status == "not_approved"
    assert quote.selection_id is None
    assert quote.american_odds == 110
    assert quote.decimal_odds == pytest.approx(2.1)
    assert quote.implied_probability == pytest.approx(1 / 2.1)
    assert not hasattr(record, "model_probability")
    assert not hasattr(record, "official_pick_id")


def test_actual_optional_provider_identifiers_preserve_values_and_missing_stays_none():
    payload = _event()
    missing = _normalize(payload).records[0]
    assert missing.provider_outcome_id is None
    assert missing.provider_market_id is None
    assert missing.provider_bookmaker_id is None
    payload["bookmakers"][0]["id"] = "book-raw-001"
    payload["bookmakers"][0]["markets"][0]["id"] = "market-raw-001"
    payload["bookmakers"][0]["markets"][0]["outcomes"][0]["id"] = "outcome-raw-001"
    present = _normalize(payload).records[0]
    assert present.provider_outcome_id == "outcome-raw-001"
    assert present.provider_market_id == "market-raw-001"
    assert present.provider_bookmaker_id == "book-raw-001"
    assert _normalize(payload).quotes[0].selection_id is None


@pytest.mark.parametrize("unsupported", [
    "batter_home_runs", "batter_home_runs_alternate", "batter_stolen_bases_alternate",
    "batter_hits_alternate_alternate", "batter_hits_invented_alternate", "home_runs",
    "batter_runs", "pitcher_walks_allowed", "pitcher_outs_recorded", "player_points", "unknown",
    "batter_first_home_run", "batter_hits_runs_rbis", "batter_singles", "batter_doubles",
    "batter_triples", "batter_fantasy_score", "pitcher_record_a_win",
])
def test_unknown_and_out_of_scope_markets_do_not_enter_supported_siblings(unsupported):
    payload = _event()
    payload["bookmakers"][0]["markets"].append(_market(unsupported))
    result = _normalize(payload)
    assert len(result.records) == 1
    assert result.records[0].provider_market_key == "batter_hits"
    assert _has_diagnostic(result, "UNSUPPORTED_MARKET", "key")


@pytest.mark.parametrize("sport_key", ["basketball_nba", "baseball_milb", "baseball_mlb_preseason", "baseball_npb", "MLB", "Baseball_MLB", None, ""])
def test_provider_sport_key_is_required_and_exact(sport_key):
    payload = _event()
    payload["sport_key"] = sport_key
    result = _normalize(payload)
    assert not result.records
    assert any(d.field == "sport_key" for d in result.diagnostics)


@pytest.mark.parametrize("timestamp", [None, "", "not-a-date", "2026-09-20T15:55:00",
                                        "2026-09-20T16:01:00Z"])
def test_market_timestamp_never_falls_back_to_book_or_collection(timestamp):
    payload = _event()
    payload["bookmakers"][0]["markets"][0]["last_update"] = timestamp
    result = _normalize(payload)
    assert not result.records
    assert any(d.field == "last_update" for d in result.diagnostics)


def test_missing_market_timestamp_and_bad_event_time_fail_closed():
    payload = _event()
    del payload["bookmakers"][0]["markets"][0]["last_update"]
    assert _has_diagnostic(_normalize(payload), "MISSING_FIELD", "last_update")
    payload = _event()
    payload["commence_time"] = "invalid"
    result = _normalize(payload)
    assert not result.records
    assert _has_diagnostic(result, "BAD_TIMESTAMP", "commence_time")


def test_captured_at_or_after_start_is_not_pregame_evidence():
    result = _normalize(_event(), collected_at=START)
    assert not result.records
    assert _has_diagnostic(result, "BAD_TIMESTAMP", "collected_at")


@pytest.mark.parametrize("field,value,category", [
    ("description", None, "MISSING_FIELD"), ("description", "", "MISSING_FIELD"),
    ("name", "Yes", "UNKNOWN_SIDE"), ("name", None, "MISSING_FIELD"),
    ("point", None, "MISSING_FIELD"), ("point", True, "BAD_LINE"),
    ("point", "0.5", "BAD_LINE"), ("point", float("nan"), "BAD_LINE"),
    ("point", float("inf"), "BAD_LINE"), ("price", None, "MISSING_FIELD"),
    ("price", 0, "BAD_PRICE"), ("price", True, "BAD_PRICE"),
    ("price", 99, "BAD_PRICE"), ("price", 1.9, "BAD_PRICE"),
])
def test_bad_rows_are_diagnosed_and_unrelated_valid_siblings_survive(field, value, category):
    payload = _event()
    bad = _outcome(description="Synthetic Malformed Participant")
    bad[field] = value
    payload["bookmakers"][0]["markets"][0]["outcomes"].append(bad)
    result = _normalize(payload)
    assert len(result.records) == 1
    assert result.records[0].participant_name == "Synthetic Batter Alpha"
    assert _has_diagnostic(result, category, field)


@pytest.mark.parametrize("level,field", [("event", "bookmakers"), ("bookmaker", "markets"),
                                          ("market", "outcomes")])
@pytest.mark.parametrize("bad_value", [None, "not-an-array", {"unexpected": "mapping"}])
def test_malformed_array_containers_produce_diagnostics(level, field, bad_value):
    payload = _event()
    row = payload if level == "event" else payload["bookmakers"][0]
    if level == "market":
        row = row["markets"][0]
    row[field] = bad_value
    result = _normalize(payload)
    assert not result.records
    assert any(d.field == field for d in result.diagnostics)


@pytest.mark.parametrize("level", ["bookmaker", "market", "outcome"])
def test_non_mapping_nodes_do_not_discard_valid_siblings(level):
    payload = _event()
    children = payload["bookmakers"]
    if level in {"market", "outcome"}:
        children = children[0]["markets"]
    if level == "outcome":
        children = children[0]["outcomes"]
    children.append("malformed-node")
    result = _normalize(payload)
    assert len(result.records) == 1
    assert _has_diagnostic(result, "INVALID", level)


@pytest.mark.parametrize("payload", [None, [], "not-an-event", 7])
def test_non_mapping_event_is_a_diagnostic(payload):
    result = _normalize(payload)
    assert not result.records
    assert _has_diagnostic(result, "INVALID", "event")


@pytest.mark.parametrize("source_refs", [(), [], ("",), ("   ",), (1,)])
def test_caller_must_supply_explicit_immutable_provenance(source_refs):
    with pytest.raises(ValueError):
        _normalize(_event(), source_refs=source_refs)


def test_valid_output_is_independent_of_all_provider_array_ordering():
    payload = _multi_event()
    assert _normalize(payload) == _normalize(_reverse_arrays(payload))
    assert _normalize(payload) == _normalize(payload)


def test_exact_duplicate_outcome_is_idempotent():
    payload = _event()
    payload["bookmakers"][0]["markets"][0]["outcomes"] *= 3
    result = _normalize(payload)
    assert result == _normalize(_event())
    assert len(result.records) == 1


@pytest.mark.parametrize("conflicting_price", [120, 0, True, None, "invalid"])
def test_conflicting_prices_exclude_every_claim_including_invalid_price_poison(conflicting_price):
    payload = _event()
    payload["bookmakers"][0]["markets"][0]["outcomes"] += [
        _outcome(price=conflicting_price),
        _outcome(description="Unrelated Participant", price=150),
    ]
    result = _normalize(payload)
    assert [r.participant_name for r in result.records] == ["Unrelated Participant"]
    assert sum(d.category == "CONFLICTING_DUPLICATE" for d in result.diagnostics) == 2
    assert result == _normalize(_reverse_arrays(payload))


@pytest.mark.parametrize("changed_field,changed_value", [("description", "Contradictory Participant"),
                                                           ("point", 1.5), ("name", "Under")])
def test_provider_outcome_id_cannot_claim_changed_participant_line_or_side(changed_field, changed_value):
    payload = _event()
    first = _outcome(id="actual-outcome-id")
    second = {**first, changed_field: changed_value}
    payload["bookmakers"][0]["markets"][0]["outcomes"] = [first, second]
    result = _normalize(payload)
    assert not result.records
    assert sum(d.category == "CONFLICTING_DUPLICATE" for d in result.diagnostics) == 2
    assert result == _normalize(_reverse_arrays(payload))


def test_same_provider_outcome_id_cannot_claim_two_market_keys():
    payload = _event()
    payload["bookmakers"][0]["markets"] = [
        _market("batter_hits", [_outcome(id="actual-outcome-id")]),
        _market("batter_total_bases", [_outcome(id="actual-outcome-id")]),
        _market("batter_walks", [_outcome(id="unrelated-id")]),
    ]
    result = _normalize(payload)
    assert [r.provider_market_key for r in result.records] == ["batter_walks"]
    assert sum(d.category == "CONFLICTING_DUPLICATE" for d in result.diagnostics) == 2
    assert result == _normalize(_reverse_arrays(payload))


def test_market_and_bookmaker_scopes_preserve_independent_identical_looking_offers():
    payload = _event()
    payload["bookmakers"][0]["markets"].append(_market("batter_total_bases"))
    second_book = deepcopy(payload["bookmakers"][0])
    second_book.update(key="fanduel", title="FanDuel")
    payload["bookmakers"].append(second_book)
    result = _normalize(payload)
    assert len(result.records) == 4
    assert not result.diagnostics


def test_diagnostics_hold_bounded_scalar_context_without_provider_payload_graphs():
    payload = _event()
    payload["private_unrelated_payload"] = {"secret-marker": ["must-not-appear"]}
    bad = _outcome(description={"nested": "must-not-appear"}, price=0)
    payload["bookmakers"][0]["markets"][0]["outcomes"].append(bad)
    result = _normalize(payload)
    assert len(result.records) == 1
    assert result.diagnostics
    for diagnostic in result.diagnostics:
        assert isinstance(diagnostic.context, tuple)
        assert all(isinstance(key, str) and isinstance(value, (str, int, float, bool, type(None)))
                   for key, value in diagnostic.context)
        assert "must-not-appear" not in repr(diagnostic)


def test_adapter_has_no_io_network_process_or_environment_dependency(monkeypatch):
    payload = _multi_event()
    before = deepcopy(payload)
    expected = _normalize(payload)

    def forbidden(*args, **kwargs):
        raise AssertionError("pure MLB normalization attempted an external operation")

    class ForbiddenEnvironment(Mapping):
        __getitem__ = forbidden
        __iter__ = forbidden
        __len__ = forbidden

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(io, "open", forbidden)
        for name in ("open", "read_text", "read_bytes", "write_text", "write_bytes", "mkdir", "unlink"):
            patch.setattr(Path, name, forbidden)
        for name in ("open", "write", "mkdir", "makedirs", "remove", "unlink", "system", "getenv"):
            patch.setattr(os, name, forbidden)
        patch.setattr(os, "environ", ForbiddenEnvironment())
        patch.setattr(requests.sessions.Session, "request", forbidden)
        patch.setattr(requests, "get", forbidden)
        for name in ("socket", "create_connection", "getaddrinfo"):
            patch.setattr(socket, name, forbidden)
        for name in ("Popen", "run", "call", "check_call", "check_output"):
            patch.setattr(subprocess, name, forbidden)
        result = _normalize(payload)
        quotes = result.quotes
    assert result == expected
    assert quotes == expected.quotes
    assert payload == before


@pytest.mark.parametrize("identifier", [731, "731"])
def test_supplied_numeric_or_text_provider_ids_preserve_original_type(identifier):
    payload = _event()
    book = payload["bookmakers"][0]
    book["id"] = identifier
    book["markets"][0]["id"] = identifier
    book["markets"][0]["outcomes"][0]["id"] = identifier
    result = _normalize(payload)
    assert not result.diagnostics
    record = result.records[0]
    for value in (record.provider_bookmaker_id, record.provider_market_id, record.provider_outcome_id):
        assert value == identifier
        assert type(value) is type(identifier)
    assert result.quotes[0].selection_id is None


def test_cross_market_outcomes_need_no_shared_or_invented_participant_identity():
    payload = _event()
    payload["bookmakers"][0]["markets"] += [
        _market("batter_total_bases", [_outcome(point=1.5)]),
        _market("batter_walks", [_outcome()]),
    ]
    result = _normalize(payload)
    assert len(result.records) == 3
    assert not result.diagnostics
    assert all(r.provider_outcome_id is None for r in result.records)
    assert all(q.selection_id is None for q in result.quotes)


@pytest.mark.parametrize("field", ["price", "point"])
def test_huge_unrepresentable_numeric_row_cannot_crash_valid_siblings(field):
    payload = _event()
    bad = _outcome(description="Extreme Numeric Participant")
    bad[field] = 10 ** 5000
    payload["bookmakers"][0]["markets"][0]["outcomes"].append(bad)
    result = _normalize(payload)
    assert len(result.records) == 1
    assert result.records[0].participant_name == "Synthetic Batter Alpha"
    assert result.diagnostics
    assert any(d.field == field for d in result.diagnostics)


def test_unrepresentable_utc_market_time_is_diagnosed_without_losing_other_market():
    payload = _event()
    payload["bookmakers"][0]["markets"].append(
        _market("batter_walks", last_update="0001-01-01T00:00:00+14:00")
    )
    result = _normalize(payload)
    assert [r.provider_market_key for r in result.records] == ["batter_hits"]
    assert _has_diagnostic(result, "BAD_TIMESTAMP", "last_update")


def test_dst_fold_timestamps_compare_absolute_instants_and_preserve_source_values():
    from zoneinfo import ZoneInfo

    toronto = ZoneInfo("America/Toronto")
    updated = datetime(2026, 11, 1, 1, 45, tzinfo=toronto, fold=0)
    collected = datetime(2026, 11, 1, 1, 15, tzinfo=toronto, fold=1)
    payload = _event()
    payload["commence_time"] = "2026-11-01T12:00:00Z"
    payload["bookmakers"][0]["markets"][0]["last_update"] = updated
    result = _normalize(payload, collected_at=collected)
    assert len(result.records) == 1
    assert not result.diagnostics
    assert result.records[0].market_updated_at == updated.astimezone(timezone.utc)
    assert result.records[0].collected_at == collected.astimezone(timezone.utc)
    quote = result.quotes[0]
    assert quote.quote_timestamp.astimezone(timezone.utc) == datetime(2026, 11, 1, 5, 45, tzinfo=timezone.utc)
    assert quote.collected_at.astimezone(timezone.utc) == datetime(2026, 11, 1, 6, 15, tzinfo=timezone.utc)


def test_repeated_dst_wall_time_updates_retain_two_distinct_quote_instants():
    from zoneinfo import ZoneInfo

    toronto = ZoneInfo("America/Toronto")
    first_update = datetime(2026, 11, 1, 1, 30, tzinfo=toronto, fold=0)
    second_update = datetime(2026, 11, 1, 1, 30, tzinfo=toronto, fold=1)
    collected = datetime(2026, 11, 1, 1, 45, tzinfo=toronto, fold=1)
    payload = _event()
    payload["commence_time"] = "2026-11-01T12:00:00Z"
    payload["bookmakers"][0]["markets"] = [
        _market(last_update=first_update),
        _market(outcomes=[_outcome(price=120)], last_update=second_update),
    ]
    result = _normalize(payload, collected_at=collected)
    assert len(result.records) == 2
    assert not result.diagnostics
    assert {r.market_updated_at for r in result.records} == {
        datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc),
        datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc),
    }
    assert result == _normalize(_reverse_arrays(payload), collected_at=collected)


def test_out_of_scope_sport_is_diagnosed_even_when_no_bookmakers_exist():
    payload = _event()
    payload.update(sport_key="baseball_milb", bookmakers=[])
    result = _normalize(payload)
    assert not result.records
    assert _has_diagnostic(result, "INVALID", "sport_key")


def test_unsupported_market_is_diagnosed_even_when_no_outcomes_exist():
    payload = _event()
    payload["bookmakers"][0]["markets"] = [_market("invented_market", outcomes=[])]
    result = _normalize(payload)
    assert not result.records
    assert _has_diagnostic(result, "UNSUPPORTED_MARKET", "key")


def _assemble_parsed_hits(record, *, quote=None, source=None):
    features = BatterHitsBaselineFeatures(
        season_hits=125, season_at_bats=500, projected_at_bats=4.0,
        lineup_status="unknown", evidence_cutoff=COLLECTED,
        source_refs=("synthetic-season-totals-before-cutoff", "synthetic-ab-projection"),
        batter_name=record.participant_name, event_id=record.provider_event_id,
    )
    return assemble_batter_hits_candidate(
        candidate_id="synthetic-parsed-hits-candidate",
        quote=record.to_normalized_quote() if quote is None else quote,
        source_evidence=batter_hits_source_evidence_from_market_record(record) if source is None else source,
        features=features,
        probability=compute_batter_hits_probability(
            features, generated_at=COLLECTED + timedelta(minutes=1),
        ),
        participant_identity=ParticipantIdentity(
            participant_name=record.participant_name,
            identity_method="explicit_synthetic_name_only",
            identity_status=IdentityStatus.NAME_ONLY_RESEARCH,
        ),
        event_identity=EventIdentity(record.provider_event_id, "supplied_provider_reference_only"),
        provenance=CandidateProvenance("offline-parser-bridge-test", ("synthetic-candidate-input",)),
    )


def test_multi_market_parser_to_hits_bridge_and_existing_candidate_remains_unresolved_research():
    result = _normalize(_multi_event())
    record = next(record for record in result.records
                  if record.provider_market_key == "batter_hits"
                  and record.bookmaker_key == "draftkings"
                  and record.participant_name == "Synthetic Batter Alpha"
                  and record.side.name == "OVER")
    quote = record.to_normalized_quote()
    source = batter_hits_source_evidence_from_market_record(record)
    candidate = _assemble_parsed_hits(record, quote=quote, source=source)
    assert source.market == record.provider_market_key == "batter_hits"
    assert source.side == "OVER"
    assert source.point == 0.5
    assert source.batter_name == record.participant_name == "Synthetic Batter Alpha"
    assert source.snapshot_timestamp == UPDATED
    assert source.event_id == record.provider_event_id
    assert source.source_refs == SOURCE_REFS
    assert candidate.quote is quote
    assert candidate.quote.raw_provider_market_id == "batter_hits"
    assert candidate.probability.model_probability == pytest.approx(1 - (1 - 125 / 500) ** 4)
    assert candidate.event_identity.identity_status is IdentityStatus.UNRESOLVED
    assert candidate.event_identity.canonical_event_id is None
    assert candidate.participant_identity.identity_status is IdentityStatus.NAME_ONLY_RESEARCH
    assert candidate.participant_identity.canonical_player_id is None
    assert candidate.research_only is True
    assert candidate.eligible_for_betting is False
    assert candidate.eligible_for_official_pick is False
    assert candidate.approval_status == "not_approved"
    assert set(SOURCE_REFS) <= set(candidate.provenance.source_refs)


def test_multi_market_parser_retains_alternate_one_and_half_hits_but_bridge_rejects_it():
    result = _normalize(_multi_event())
    alternate = next(record for record in result.records
                     if record.provider_market_key == "batter_hits_alternate")
    assert alternate.canonical_market_type == "batter_hits"
    assert alternate.market_variant.name == "ALTERNATE"
    assert alternate.line == 1.5
    assert alternate.to_normalized_quote().raw_provider_market_id == "batter_hits_alternate"
    with pytest.raises(ValueError, match="main batter_hits"):
        batter_hits_source_evidence_from_market_record(alternate)


def test_parsed_alternate_half_hits_cannot_pass_bridge_or_existing_candidate_raw_market_guard():
    result = _normalize(_event("batter_hits_alternate"))
    assert len(result.records) == 1
    alternate = result.records[0]
    assert alternate.line == 0.5
    assert alternate.canonical_market_type == "batter_hits"
    with pytest.raises(ValueError, match="main batter_hits"):
        batter_hits_source_evidence_from_market_record(alternate)
    invented_main_source = BatterHitsSourceEvidence(
        market="batter_hits", side="Over", point=0.5,
        batter_name=alternate.participant_name, snapshot_timestamp=alternate.market_updated_at,
        event_id=alternate.provider_event_id, provider=alternate.provider,
        sportsbook=alternate.bookmaker_name, source_refs=alternate.source_refs,
    )
    with pytest.raises(ValueError, match="raw provider market"):
        _assemble_parsed_hits(alternate, source=invented_main_source)


@pytest.mark.parametrize("changed_market,independent_market", [
    ("batter_hits", "batter_total_bases"), ("batter_total_bases", "batter_hits"),
])
def test_parser_price_edits_do_not_mutate_other_market_records_or_quotes(changed_market, independent_market):
    original_payload = _multi_event()
    original_copy = deepcopy(original_payload)
    original = _normalize(original_payload)
    changed_payload = deepcopy(original_payload)
    changed_market_payload = next(market for market in changed_payload["bookmakers"][0]["markets"]
                                  if market["key"] == changed_market)
    changed_market_payload["outcomes"][0]["price"] = 130
    changed = _normalize(changed_payload)

    def market_pairs(batch, market_key):
        return tuple((record, quote) for record, quote in zip(batch.records, batch.quotes, strict=True)
                     if record.provider_market_key == market_key)

    assert market_pairs(changed, changed_market) != market_pairs(original, changed_market)
    assert market_pairs(changed, independent_market) == market_pairs(original, independent_market)
    assert original_payload == original_copy
    assert _normalize(original_payload) == original
    assert _normalize(changed_payload) == changed
