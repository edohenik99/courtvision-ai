"""Controlled ingestion contracts: synthetic bodies and injected HTTP only."""
from __future__ import annotations

import builtins
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
import io
import hashlib
import json
import os
from pathlib import Path
import socket

import pytest
import requests

from courtvision.core.candidates import (
    CandidateProvenance, EventIdentity, IdentityStatus, ParticipantIdentity,
)
from courtvision.sports.mlb.batter_hits import (
    BatterHitsBaselineFeatures, assemble_batter_hits_candidate,
    batter_hits_source_evidence_from_market_record, compute_batter_hits_probability,
)
from courtvision.sports.mlb.providers import the_odds_api_live as live
from courtvision.sports.mlb.providers.the_odds_api_market_adapter import PROVIDER_MARKET_MAPPING
from courtvision.sports.mlb.providers.the_odds_api_transport import OddsAPIHTTPResponse


SECRET = "cv-secret-test-key-DO-NOT-LEAK-9347"
NOW = datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc)
DAY = date(2026, 9, 20)
MARKETS = (
    "batter_hits", "batter_total_bases", "batter_rbis", "batter_runs_scored",
    "batter_walks", "batter_strikeouts", "pitcher_strikeouts", "pitcher_outs",
)


def _forbidden(*args, **kwargs):
    raise AssertionError("Real network and provider credential access are forbidden")


@pytest.fixture(autouse=True)
def _offline_and_secret_scan(monkeypatch, tmp_path):
    monkeypatch.setattr(requests.sessions.Session, "request", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", _forbidden)
    monkeypatch.setattr(socket.socket, "connect", _forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", _forbidden)
    monkeypatch.setattr(socket.socket, "sendto", _forbidden)
    monkeypatch.setenv("THE_ODDS_API_KEY", "environment-must-not-be-consumed")
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert SECRET.encode() not in content, path
            assert hashlib.sha256(SECRET.encode()).hexdigest().encode() not in content, path
            if path.suffix in {".json", ".jsonl"}:
                assert b'"apiKey"' not in content, path


def _config(**overrides):
    fields = dict(
        operating_date=DAY, markets=("batter_hits",), regions="us",
        maximum_events=3, maximum_http_requests=4, maximum_provider_credits=4,
        event_odds_declared_max_credit_cost=1, discovery_declared_max_credit_cost=0,
        discovery_zero_cost_verified=True, network_enabled=True,
    )
    fields.update(overrides)
    return live.MLBOddsIngestionConfig(**fields)


def _event(event_id="event-a", commence_time="2026-09-20T23:00:00Z", markets=("batter_hits",)):
    return {
        "id": event_id, "sport_key": "baseball_mlb", "sport_title": "MLB",
        "commence_time": commence_time, "home_team": "Synthetic Home Club",
        "away_team": "Synthetic Away Club", "bookmakers": [{
            "key": "draftkings", "title": "DraftKings", "markets": [{
                "key": key, "last_update": "2026-09-20T15:55:00Z", "outcomes": [{
                    "description": "Example Batter", "name": "Over", "point": 0.5,
                    "price": 110,
                }],
            } for key in markets],
        }],
    }


def _headers(used=100, remaining=400, last=1):
    return tuple((key, str(value)) for key, value in (
        ("x-requests-used", used), ("x-requests-remaining", remaining),
        ("x-requests-last", last),
    ))


def _response(payload=None, *, status=200, headers=None, body=None):
    return OddsAPIHTTPResponse(
        status_code=status, headers=_headers() if headers is None else headers,
        body=json.dumps(_event() if payload is None else payload).encode() if body is None else body,
    )


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def send(self, request, *, api_key, timeout_seconds, max_response_bytes):
        assert api_key == SECRET
        assert timeout_seconds > 0
        assert max_response_bytes > 0
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _plan(config=None, events=None):
    return live.build_request_plan(
        _config() if config is None else config,
        events=[_event()] if events is None else events, captured_at=NOW,
    )


def _execute(tmp_path, transport, plan=None, **overrides):
    fields = dict(
        api_key=SECRET, transport=transport, output_root=tmp_path / "research-evidence",
        run_id="synthetic-run", clock=lambda: NOW,
    )
    fields.update(overrides)
    result = live.execute_ingestion(_plan() if plan is None else plan, **fields)
    assert SECRET not in repr(result)
    assert "environment-must-not-be-consumed" not in repr(result)
    return result


def test_plan_and_config_are_immutable_and_markets_deterministic():
    config = _config(markets=("pitcher_outs", "batter_hits", "batter_total_bases"))
    plan = _plan(config)
    other = _plan(_config(markets=tuple(reversed(config.markets))))
    assert plan.requests[0].request_id == other.requests[0].request_id
    assert plan.requests[0].markets == tuple(sorted(config.markets))
    assert plan.requests[0].endpoint == "/v4/sports/baseball_mlb/events/event-a/odds"
    assert dict(plan.requests[0].query) == {
        "dateFormat": "iso", "markets": "batter_hits,batter_total_bases,pitcher_outs",
        "oddsFormat": "american", "regions": "us",
    }
    for item, field, value in ((config, "network_enabled", False), (plan, "requests", ()),
                               (plan.requests[0], "event_id", "changed")):
        with pytest.raises((FrozenInstanceError, AttributeError)):
            setattr(item, field, value)


def test_network_defaults_off_and_smoke_profile_is_hits_only():
    fields = dict(operating_date=DAY, markets=("batter_hits",), regions="us",
                  maximum_events=1, maximum_http_requests=1, maximum_provider_credits=1,
                  event_odds_declared_max_credit_cost=1)
    assert live.MLBOddsIngestionConfig(**fields).network_enabled is False
    assert live.INITIAL_RESEARCH_MARKETS == ("batter_hits",)


@pytest.mark.parametrize("market", sorted(PROVIDER_MARKET_MAPPING))
def test_all_existing_provider_markets_are_plannable(market):
    assert _plan(_config(markets=(market,))).requests[0].markets == (market,)


@pytest.mark.parametrize("market", ["batter_home_runs", "batter_hits_unknown", "batter_hits_alternate_alternate"])
def test_unsupported_market_rejected_without_suffix_guessing(market):
    with pytest.raises(ValueError):
        _plan(_config(markets=(market,)))


@pytest.mark.parametrize("field,value", [
    ("maximum_events", 0), ("maximum_events", -1), ("maximum_events", True),
    ("maximum_http_requests", 0), ("maximum_http_requests", -1),
    ("maximum_provider_credits", -1), ("maximum_provider_credits", float("nan")),
    ("maximum_provider_credits", float("inf")), ("maximum_provider_credits", 1.5),
    ("event_odds_declared_max_credit_cost", -1),
    ("timeout_seconds", 0), ("timeout_seconds", -1),
    ("timeout_seconds", float("nan")), ("timeout_seconds", float("inf")),
    ("timeout_seconds", 10000), ("regions", ""), ("regions", "  "),
    ("sport_key", "basketball_nba"), ("provider", "another-provider"),
    ("odds_format", "decimal"), ("research_only", False),
    ("max_response_bytes", 0), ("minimum_pregame_lead_seconds", -1),
])
def test_invalid_configuration_fails_before_transport(field, value):
    with pytest.raises(ValueError):
        _plan(_config(**{field: value}))


def test_toronto_date_and_pregame_lead_are_applied_to_full_slate():
    payloads = [
        _event("next-toronto-day", "2026-09-21T04:01:00Z"),
        _event("late-toronto-day", "2026-09-21T03:59:00Z"),
        _event("started", "2026-09-20T15:59:00Z"),
        _event("at-capture", "2026-09-20T16:00:00Z"),
        _event("too-close", "2026-09-20T16:04:59Z"),
        _event("minimum-lead", "2026-09-20T16:05:00Z"),
        _event("previous-toronto-day", "2026-09-20T03:59:00Z"),
    ]
    plan = _plan(events=payloads)
    reverse = _plan(events=list(reversed(payloads)))
    assert [request.event_id for request in plan.requests] == ["minimum-lead", "late-toronto-day"]
    assert plan.requests == reverse.requests


@pytest.mark.parametrize("commence_time", ["2026-09-20T23:00:00", "invalid", None])
def test_event_selection_rejects_unproven_timestamps(commence_time):
    with pytest.raises(ValueError):
        _plan(events=[_event(commence_time=commence_time)])


def test_oversized_slate_is_rejected_instead_of_truncated():
    with pytest.raises(ValueError, match="SLATE_EXCEEDS_LIMIT"):
        _plan(_config(maximum_events=1), [_event("a"), _event("b")])


def test_conflicting_duplicate_event_identity_fails_closed():
    with pytest.raises(ValueError, match="CONFLICTING_DUPLICATE"):
        _plan(events=[_event(), _event(commence_time="2026-09-20T23:30:00Z")])


def test_discovery_plan_reserves_full_configured_slate_and_true_dry_run_has_no_io(monkeypatch):
    plan = live.build_request_plan(_config(), captured_at=NOW)
    assert plan.discovery_pending is True
    assert len(plan.requests) == 1
    assert plan.requests[0].endpoint == "/v4/sports/baseball_mlb/events"
    assert dict(plan.requests[0].query) == {"dateFormat": "iso"}
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", _forbidden)
        patch.setattr(io, "open", _forbidden)
        patch.setattr(Path, "mkdir", _forbidden)
        patch.setattr(os, "getenv", _forbidden)
        result = live.dry_run(plan)
    assert result.status == "DRY_RUN"
    assert result.executed_request_count == 0
    assert SECRET not in repr(result)


@pytest.mark.parametrize("updates", [
    {"maximum_http_requests": 2}, {"maximum_provider_credits": 2},
    {"event_odds_declared_max_credit_cost": None},
    {"discovery_declared_max_credit_cost": None, "discovery_zero_cost_verified": False},
    {"discovery_declared_max_credit_cost": 0, "discovery_zero_cost_verified": False},
])
def test_full_discovery_budget_must_be_explicit_before_start(updates):
    with pytest.raises(ValueError):
        live.build_request_plan(_config(**updates), captured_at=NOW)


def test_disabled_execution_touches_no_credentials_files_or_transport(monkeypatch):
    plan = _plan(_config(network_enabled=False))
    transport = FakeTransport()
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", _forbidden)
        patch.setattr(io, "open", _forbidden)
        patch.setattr(Path, "mkdir", _forbidden)
        patch.setattr(os, "getenv", _forbidden)
        result = live.execute_ingestion(plan, api_key=None, transport=transport,
                                        output_root=None, run_id="disabled", clock=lambda: NOW)
    assert result.status == "NETWORK_DISABLED"
    assert result.executed_request_count == 0
    assert transport.calls == []


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_missing_explicit_key_does_not_use_environment(tmp_path, api_key):
    transport = FakeTransport()
    result = _execute(tmp_path, transport, api_key=api_key)
    assert result.status != "COMPLETE"
    assert result.executed_request_count == 0
    assert not transport.calls


def test_evidence_root_is_required_before_transport(tmp_path):
    transport = FakeTransport()
    result = _execute(tmp_path, transport, output_root=None)
    assert result.status != "COMPLETE"
    assert not transport.calls


@pytest.mark.parametrize("malformation", ["too_many_requests", "too_many_credits", "missing_cost", "unknown_market"])
def test_executor_revalidates_tampered_plan_before_transport(tmp_path, malformation):
    plan = _plan()
    if malformation == "too_many_requests":
        plan = replace(plan, config=replace(plan.config, maximum_http_requests=1),
                       requests=(plan.requests[0], replace(plan.requests[0], event_id="event-b")))
    elif malformation == "too_many_credits":
        plan = replace(plan, requests=(replace(plan.requests[0], declared_max_credit_cost=5),))
    elif malformation == "missing_cost":
        plan = replace(plan, requests=(replace(plan.requests[0], declared_max_credit_cost=None),))
    else:
        plan = replace(plan, requests=(replace(plan.requests[0], markets=("unknown_market",)),))
    transport = FakeTransport()
    result = _execute(tmp_path, transport, plan)
    assert result.status != "COMPLETE"
    assert result.executed_request_count == 0
    assert not transport.calls


def test_exact_duplicate_requests_issue_one_transport_call(tmp_path):
    plan = _plan(_config(markets=("batter_hits", "pitcher_outs")))
    request = plan.requests[0]
    plan = replace(plan, requests=(request, replace(request, markets=tuple(reversed(request.markets))), request))
    transport = FakeTransport(_response(_event(markets=("batter_hits", "pitcher_outs"))))
    result = _execute(tmp_path, transport, plan)
    assert result.status == "COMPLETE"
    assert len(transport.calls) == result.executed_request_count == 1
    assert result.skipped_duplicate_request_count == 2


def test_conflicting_duplicate_cost_blocks_entire_execution(tmp_path):
    plan = _plan()
    plan = replace(plan, requests=(plan.requests[0], replace(plan.requests[0], declared_max_credit_cost=2)))
    transport = FakeTransport()
    result = _execute(tmp_path, transport, plan)
    assert result.status != "COMPLETE"
    assert not transport.calls


def test_success_has_immutable_exchange_usage_and_research_quote(tmp_path):
    transport = FakeTransport(_response())
    result = _execute(tmp_path, transport)
    assert result.status == "COMPLETE"
    assert result.executed_request_count == len(result.exchanges) == 1
    assert result.reported_credit_cost == 1
    assert result.provider_remaining == 400
    assert not result.transport_failures
    exchange = result.exchanges[0]
    assert json.loads(exchange.raw_json) == _event()
    assert hashlib.sha256(exchange.raw_json.encode()).hexdigest() == exchange.response_sha256
    assert exchange.requested_at == exchange.responded_at == NOW
    assert exchange.http_status == 200
    assert exchange.actual_reported_last_cost == 1
    assert exchange.actual_reported_used == 100
    assert exchange.actual_reported_remaining == 400
    assert exchange.declared_budget == 4
    assert exchange.declared_max_credit_cost == 1
    assert exchange.declared_cost_before_request == 1
    assert "apiKey" not in dict(exchange.query)
    assert len(result.market_batches) == 1
    assert len(result.market_batches[0].records) == 1
    with pytest.raises((FrozenInstanceError, AttributeError)):
        result.status = "FORGED"
    for quote in result.market_batches[0].quotes:
        assert quote.mode == "research"
        assert quote.eligible_for_betting is False
        assert quote.kelly_eligible is False
        assert quote.approval_status == "not_approved"
        assert quote.selection_id is None
    assert list(tmp_path.rglob("COMPLETE"))


@pytest.mark.parametrize("header,value", [
    ("x-requests-used", "1.5"), ("x-requests-remaining", "abc"),
    ("x-requests-last", "-1"), ("x-requests-last", "NaN"),
    ("x-requests-last", "+1"),
])
def test_invalid_usage_stops_following_calls(tmp_path, header, value):
    headers = dict(_headers())
    headers.pop(header)
    if value is not None:
        headers[header] = value
    plan = _plan(events=[_event("event-a"), _event("event-b")])
    transport = FakeTransport(_response(headers=tuple(headers.items())))
    result = _execute(tmp_path, transport, plan)
    assert result.status == "ACCOUNTING_CONFLICT"
    assert len(transport.calls) == 1
    assert len(result.exchanges) == 1
    assert list(tmp_path.rglob("COMPLETE"))


@pytest.mark.parametrize("second_headers", [_headers(99, 399, 1), _headers(101, 401, 1)])
def test_usage_monotonicity_break_preserves_exchanges_and_stops(tmp_path, second_headers):
    plan = _plan(events=[_event("event-a"), _event("event-b"), _event("event-c")])
    transport = FakeTransport(_response(), _response(_event("event-b"), headers=second_headers))
    result = _execute(tmp_path, transport, plan)
    assert result.status == "ACCOUNTING_CONFLICT"
    assert len(transport.calls) == len(result.exchanges) == 2
    assert len(result.market_batches) >= 1


def test_reported_last_cost_above_declared_max_stops_immediately(tmp_path):
    plan = _plan(events=[_event("event-a"), _event("event-b")])
    transport = FakeTransport(_response(headers=_headers(last=2)))
    result = _execute(tmp_path, transport, plan)
    assert result.status == "BUDGET_BLOCKED"
    assert len(transport.calls) == len(result.exchanges) == 1


def test_actual_reported_run_cost_above_run_budget_is_not_ignored(tmp_path):
    plan = _plan(_config(maximum_provider_credits=2), [_event("event-a"), _event("event-b")])
    transport = FakeTransport(_response(), _response(_event("event-b"), headers=_headers(102, 398, 2)))
    result = _execute(tmp_path, transport, plan)
    assert result.status == "BUDGET_BLOCKED"
    assert result.reported_credit_cost == 3
    assert len(transport.calls) == 2


@pytest.mark.parametrize("response,expected", [
    (_response(status=500), "HTTP_ERROR"), (_response(status=429), "HTTP_429"),
    (TimeoutError("timeout https://api.the-odds-api.com/?apiKey=" + SECRET), "TIMEOUT"),
    (RuntimeError("error https://api.the-odds-api.com/?apiKey=" + SECRET), "NETWORK_ERROR"),
    (_response(body=b"not json"), "INVALID_JSON"),
    (_response(payload=["unexpected"]), "INVALID_RESPONSE_SHAPE"),
])
def test_failures_are_explicit_redacted_and_never_retried(tmp_path, response, expected):
    plan = _plan(events=[_event("event-a"), _event("event-b")])
    transport = FakeTransport(response)
    result = _execute(tmp_path, transport, plan)
    assert result.status == "FAILED"
    assert expected in repr(result.transport_failures)
    assert len(transport.calls) == len(result.exchanges) == 1
    assert not result.market_batches
    assert list(tmp_path.rglob("COMPLETE"))


def test_oversized_injected_response_is_rejected_even_if_transport_did_not_guard_it(tmp_path):
    transport = FakeTransport(_response(body=b" " * 2048))
    result = _execute(tmp_path, transport, _plan(_config(max_response_bytes=1024)))
    assert result.status == "FAILED"
    assert "RESPONSE_TOO_LARGE" in repr(result.transport_failures)
    assert len(transport.calls) == 1


def test_discovery_then_partial_failure_retains_a_and_b_and_never_executes_c(tmp_path):
    events = [_event("event-a"), _event("event-b"), _event("event-c")]
    plan = live.build_request_plan(_config(maximum_provider_credits=3), captured_at=NOW)
    transport = FakeTransport(
        _response(events, headers=_headers(100, 400, 0)),
        _response(events[0], headers=_headers(101, 399, 1)),
        _response({"message": "synthetic unavailable"}, status=500, headers=_headers(102, 398, 1)),
    )
    result = _execute(tmp_path, transport, plan)
    assert result.status == "PARTIAL"
    assert len(transport.calls) == len(result.exchanges) == 3
    assert len(result.market_batches) == 1
    assert [request.event_id for request in transport.calls] == [None, "event-a", "event-b"]
    assert list(tmp_path.rglob("COMPLETE"))
    raw_files = list(tmp_path.rglob("raw/*.json"))
    assert len(raw_files) == 3


def test_discovered_slate_over_max_stops_after_discovery_and_keeps_evidence(tmp_path):
    plan = live.build_request_plan(_config(maximum_events=1), captured_at=NOW)
    transport = FakeTransport(_response([_event("a"), _event("b")], headers=_headers(last=0)))
    result = _execute(tmp_path, transport, plan)
    assert result.status != "COMPLETE"
    assert "SLATE_EXCEEDS_LIMIT" in repr(result.transport_failures)
    assert len(transport.calls) == 1
    assert list(tmp_path.rglob("COMPLETE"))


def test_eight_market_response_reuses_pure_adapter_and_keeps_independent_rows(tmp_path):
    event = _event(markets=MARKETS)
    event["bookmakers"][0]["markets"][0]["outcomes"].append({
        "description": "Malformed Sibling", "name": "Over", "point": 0.5, "price": 0,
    })
    result = _execute(tmp_path, FakeTransport(_response(event)), _plan(_config(markets=MARKETS)))
    assert result.status == "COMPLETE"
    batch = result.market_batches[0]
    assert len(batch.records) == len(batch.quotes) == 8
    assert {record.provider_market_key for record in batch.records} == set(MARKETS)
    assert {record.canonical_market_type for record in batch.records} == {
        "batter_hits", "batter_total_bases", "batter_rbis", "batter_runs", "batter_walks",
        "batter_strikeouts", "pitcher_strikeouts", "pitcher_outs_recorded",
    }
    assert batch.diagnostics
    assert result.normalization_diagnostics
    for record, quote in zip(batch.records, batch.quotes, strict=True):
        assert record.provider_event_id == quote.raw_event_id == "event-a"
        assert record.participant_name == "Example Batter"
        assert quote.selection_id is None
        assert quote.eligible_for_betting is False
        assert quote.kelly_eligible is False
        assert quote.approval_status == "not_approved"


def test_http_exchange_to_existing_hits_candidate_stays_name_only_research(tmp_path):
    result = _execute(tmp_path, FakeTransport(_response()))
    record = result.market_batches[0].records[0]
    quote = result.market_batches[0].quotes[0]
    source = batter_hits_source_evidence_from_market_record(record)
    features = BatterHitsBaselineFeatures(
        season_hits=125, season_at_bats=500, projected_at_bats=4.0,
        lineup_status="unknown", evidence_cutoff=NOW,
        source_refs=("synthetic-season-totals", "synthetic-ab-projection"),
        batter_name=record.participant_name, event_id=record.provider_event_id,
    )
    candidate = assemble_batter_hits_candidate(
        candidate_id="synthetic-ingestion-hits", quote=quote, source_evidence=source,
        features=features, probability=compute_batter_hits_probability(
            features, generated_at=NOW + timedelta(minutes=1),
        ),
        participant_identity=ParticipantIdentity(
            participant_name=record.participant_name, identity_method="synthetic_name_only",
            identity_status=IdentityStatus.NAME_ONLY_RESEARCH,
        ),
        event_identity=EventIdentity(record.provider_event_id, "provider_reference_only"),
        provenance=CandidateProvenance("offline-ingestion-test", ("synthetic-candidate-input",)),
    )
    assert source.batter_name == "Example Batter"
    assert source.market == "batter_hits"
    assert source.side == "OVER"
    assert source.point == 0.5
    assert source.source_refs == record.source_refs
    assert candidate.event_identity.canonical_event_id is None
    assert candidate.event_identity.identity_status is IdentityStatus.UNRESOLVED
    assert candidate.participant_identity.canonical_player_id is None
    assert candidate.participant_identity.identity_status is IdentityStatus.NAME_ONLY_RESEARCH
    assert candidate.research_only is True
    assert candidate.eligible_for_betting is False
    assert candidate.eligible_for_official_pick is False
    assert candidate.approval_status == "not_approved"
    assert set(record.source_refs) <= set(candidate.provenance.source_refs)

@pytest.mark.parametrize("header", ["x-requests-used", "x-requests-remaining"])
def test_missing_account_total_remains_unavailable_without_inventing_zero(tmp_path, header):
    headers = dict(_headers())
    headers.pop(header)
    result = _execute(tmp_path, FakeTransport(_response(headers=tuple(headers.items()))))
    assert result.status == "COMPLETE"
    assert result.reported_credit_cost == 1
    if header == "x-requests-remaining":
        assert result.provider_remaining is None
        assert result.exchanges[0].actual_reported_remaining is None
    else:
        assert result.exchanges[0].actual_reported_used is None


def test_missing_last_cost_stops_with_explicit_accounting_unavailable(tmp_path):
    headers = dict(_headers())
    headers.pop("x-requests-last")
    transport = FakeTransport(_response(headers=tuple(headers.items())))
    result = _execute(tmp_path, transport, _plan(events=[_event("event-a"), _event("event-b")]))
    assert result.status == "ACCOUNTING_UNAVAILABLE"
    assert len(transport.calls) == 1
    assert result.reported_credit_cost is None
    assert list(tmp_path.rglob("COMPLETE"))


def test_sensitive_raw_response_never_becomes_hashed_or_normalized_secret(tmp_path):
    payload = _event()
    payload["apiKey"] = SECRET
    payload["debug"] = {SECRET: "https://api.the-odds-api.com/?apiKey=" + SECRET,
                        "Authorization": "Bearer synthetic-other-secret",
                        "cookies": "synthetic-other-secret"}
    result = _execute(tmp_path, FakeTransport(_response(payload)))
    assert result.status != "COMPLETE"
    assert "SECRET_REDACTED" in repr(result)
    assert not result.market_batches
    assert list(tmp_path.rglob("COMPLETE"))


def test_mismatched_provider_event_response_is_not_normalized(tmp_path):
    result = _execute(tmp_path, FakeTransport(_response(_event("wrong-event"))))
    assert result.status == "FAILED"
    assert not result.market_batches
    assert "INVALID_RESPONSE_SHAPE" in repr(result.transport_failures)


@pytest.mark.parametrize("event_id", ["../escape", "https://example.invalid/", "event?apiKey=bad", "event#fragment"])
def test_event_reference_cannot_change_endpoint_or_inject_query(event_id):
    with pytest.raises(ValueError):
        _plan(events=[_event(event_id)])


def test_importing_module_definitions_has_no_network_environment_or_write_side_effects(monkeypatch):
    import sys
    import types

    source_path = Path(live.__file__)
    code = compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec")
    name = "courtvision.sports.mlb.providers._synthetic_live_import_check"
    module = types.ModuleType(name)
    module.__package__ = live.__package__
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, name, module)
        patch.setattr(builtins, "open", _forbidden)
        patch.setattr(io, "open", _forbidden)
        patch.setattr(Path, "mkdir", _forbidden)
        patch.setattr(Path, "write_text", _forbidden)
        patch.setattr(Path, "write_bytes", _forbidden)
        patch.setattr(os, "getenv", _forbidden)
        exec(code, module.__dict__)
    assert module.INITIAL_RESEARCH_MARKETS == ("batter_hits",)

class MockHTTPResponse:
    def __init__(self, *, status=200, headers=None, chunks=None):
        self.status_code = status
        self.headers = dict(_headers()) if headers is None else headers
        self.chunks = [json.dumps(_event()).encode()] if chunks is None else chunks
        self.read_count = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        assert chunk_size > 0
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk


class MockHTTPSession:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.adapters = []
        self.trust_env = True
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def mount(self, prefix, adapter):
        self.adapters.append((prefix, adapter))

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def _concrete_send(monkeypatch, response, **overrides):
    from courtvision.sports.mlb.providers.the_odds_api_transport import RequestsOddsAPITransport

    session = MockHTTPSession(response)
    monkeypatch.setattr(requests, "Session", lambda: session)
    kwargs = dict(api_key=SECRET, timeout_seconds=15, max_response_bytes=4 * 1024 * 1024)
    kwargs.update(overrides)
    result = RequestsOddsAPITransport(network_enabled=True).send(_plan().requests[0], **kwargs)
    assert SECRET not in repr(result)
    return result, session


def test_concrete_transport_requires_its_own_explicit_network_flag(monkeypatch):
    from courtvision.sports.mlb.providers.the_odds_api_transport import RequestsOddsAPITransport

    monkeypatch.setattr(requests, "Session", _forbidden)
    response = RequestsOddsAPITransport().send(_plan().requests[0], api_key=SECRET,
                                              timeout_seconds=15, max_response_bytes=4096)
    assert response.error == "NETWORK_DISABLED"
    assert response.body == b""


def test_concrete_transport_uses_only_fixed_https_bounded_get_with_no_redirect_or_retry(monkeypatch):
    response = MockHTTPResponse(headers={**dict(_headers()), "Authorization": SECRET,
                                         "Set-Cookie": SECRET, "Retry-After": "60"})
    result, session = _concrete_send(monkeypatch, response)
    assert result.status_code == 200
    assert session.trust_env is False
    assert len(session.calls) == 1
    url, parameters = session.calls[0]
    assert url == "https://api.the-odds-api.com/v4/sports/baseball_mlb/events/event-a/odds"
    assert SECRET not in url
    assert parameters == {
        "params": {"dateFormat": "iso", "markets": "batter_hits", "oddsFormat": "american",
                   "regions": "us", "apiKey": SECRET},
        "timeout": 15, "stream": True, "allow_redirects": False,
    }
    assert len(session.adapters) == 1
    assert session.adapters[0][0] == "https://"
    assert session.adapters[0][1].max_retries.total == 0
    assert dict(result.headers) == {**dict(_headers()), "retry-after": "60"}
    assert response.closed and session.closed


@pytest.mark.parametrize("response,expected,reads", [
    (MockHTTPResponse(headers={"Content-Length": "1025"}, chunks=[b"not read"]), "RESPONSE_TOO_LARGE", 0),
    (MockHTTPResponse(headers={}, chunks=[b"x" * 800, b"x" * 225]), "RESPONSE_TOO_LARGE", 2),
    (MockHTTPResponse(headers={"Content-Length": "1"}, chunks=[b"x" * 1025]), "RESPONSE_TOO_LARGE", 1),
    (MockHTTPResponse(headers={"Content-Length": "invalid"}), "INVALID_RESPONSE_SHAPE", 0),
    (MockHTTPResponse(headers={"Content-Length": "-1"}), "INVALID_RESPONSE_SHAPE", 0),
])
def test_concrete_transport_bounds_declared_and_streamed_response_size(monkeypatch, response, expected, reads):
    result, session = _concrete_send(monkeypatch, response, max_response_bytes=1024)
    assert result.error == expected
    assert result.body == b""
    assert response.read_count == reads
    assert len(session.calls) == 1
    assert response.closed and session.closed


@pytest.mark.parametrize("response,expected", [
    (requests.Timeout("timeout https://api.the-odds-api.com/?apiKey=" + SECRET), "TIMEOUT"),
    (requests.ConnectionError("failed https://api.the-odds-api.com/?apiKey=" + SECRET), "NETWORK_ERROR"),
    (MockHTTPResponse(status=429), None), (MockHTTPResponse(status=500), None),
    (MockHTTPResponse(status=302, headers={"Location": "https://example.invalid/"}), None),
])
def test_concrete_transport_never_retries_or_exposes_client_exception_url(monkeypatch, response, expected):
    result, session = _concrete_send(monkeypatch, response)
    assert result.error == expected
    assert len(session.calls) == 1
    assert session.closed


@pytest.mark.parametrize("updates", [
    {"api_key": ""}, {"timeout_seconds": 0}, {"timeout_seconds": float("nan")},
    {"max_response_bytes": 0},
])
def test_concrete_invalid_parameters_never_construct_http_session(monkeypatch, updates):
    from courtvision.sports.mlb.providers.the_odds_api_transport import RequestsOddsAPITransport

    monkeypatch.setattr(requests, "Session", _forbidden)
    kwargs = dict(api_key=SECRET, timeout_seconds=15, max_response_bytes=4096)
    kwargs.update(updates)
    result = RequestsOddsAPITransport(network_enabled=True).send(_plan().requests[0], **kwargs)
    assert result.error == "INVALID_REQUEST"


def test_arbitrary_host_override_is_not_part_of_concrete_transport_api():
    from courtvision.sports.mlb.providers.the_odds_api_transport import RequestsOddsAPITransport

    with pytest.raises(TypeError):
        RequestsOddsAPITransport(network_enabled=True, base_url="https://example.invalid")


def test_partial_failure_with_unexpected_cost_never_attempts_budget_exceeding_event_c(tmp_path):
    events = [_event("event-a"), _event("event-b"), _event("event-c")]
    plan = live.build_request_plan(_config(maximum_provider_credits=3), captured_at=NOW)
    transport = FakeTransport(
        _response(events, headers=_headers(100, 400, 0)),
        _response(events[0], headers=_headers(101, 399, 1)),
        _response({"message": "synthetic failure"}, status=500, headers=_headers(103, 397, 2)),
    )
    result = _execute(tmp_path, transport, plan)
    assert result.status in {"BUDGET_BLOCKED", "PARTIAL"}
    assert result.reported_credit_cost == 3
    assert [request.event_id for request in transport.calls] == [None, "event-a", "event-b"]
    assert len(result.market_batches) == 1
    assert len(result.exchanges) == 3
    assert len(list(tmp_path.rglob("raw/*.json"))) == 3


@pytest.mark.parametrize("run_id", ["CON", "NUL", "COM1", "LPT9"])
def test_windows_reserved_run_identifier_fails_before_transport(tmp_path, run_id):
    transport = FakeTransport()
    result = _execute(tmp_path, transport, run_id=run_id)
    assert result.status != "COMPLETE"
    assert result.executed_request_count == 0
    assert not transport.calls


def test_legacy_hr_snapshot_destination_is_rejected_before_transport(tmp_path):
    transport = FakeTransport()
    result = _execute(tmp_path, transport,
                      output_root=tmp_path / "data" / "theoddsapi" / "live_hr_snapshots")
    assert result.status != "COMPLETE"
    assert result.executed_request_count == 0
    assert not transport.calls


@pytest.mark.parametrize("updates", [{"maximum_http_requests": 1}, {"maximum_provider_credits": 1}])
def test_supplied_full_slate_rejects_http_or_credit_limit_without_silent_truncation(updates):
    with pytest.raises(ValueError, match="BUDGET_EXCEEDED"):
        _plan(_config(**updates), [_event("event-a"), _event("event-b")])


def test_execution_rechecks_lead_time_before_any_http_when_plan_has_gone_stale(tmp_path):
    transport = FakeTransport()
    result = _execute(tmp_path, transport,
                      clock=lambda: datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc))
    assert result.status == "FAILED"
    assert "PREGAME_LEAD_TIME" in repr(result.transport_failures)
    assert result.executed_request_count == 0
    assert not transport.calls


@pytest.mark.parametrize("status,body,expected", [(500, b"<html>failure</html>", "HTTP_ERROR"),
                                                 (429, b"", "HTTP_429")])
def test_http_failure_status_survives_non_json_or_empty_body(tmp_path, status, body, expected):
    transport = FakeTransport(_response(status=status, body=body))
    result = _execute(tmp_path, transport)
    assert result.status == "FAILED"
    assert result.transport_failures == (expected,)
    assert result.exchanges[0].http_status == status
    assert len(transport.calls) == 1


def test_canonical_serialized_size_is_bounded_after_utf8_body_read(tmp_path):
    payload = _event()
    payload["padding"] = "\u00e9" * 1000
    body = json.dumps(payload, ensure_ascii=False).encode()
    assert len(body) < 4096
    transport = FakeTransport(_response(body=body))
    result = _execute(tmp_path, transport, _plan(_config(max_response_bytes=4096)))
    assert result.status == "FAILED"
    assert result.transport_failures == ("RESPONSE_TOO_LARGE",)
    assert not result.market_batches


def test_raw_redaction_stays_explicit_when_credit_violation_takes_status_priority(tmp_path):
    payload = _event()
    payload["cookies"] = "synthetic-other-secret"
    payload["debug"] = SECRET
    transport = FakeTransport(_response(payload, headers=_headers(last=2)))
    result = _execute(tmp_path, transport)
    assert result.status == "BUDGET_BLOCKED"
    assert result.exchanges[0].raw_body_redacted is True
    assert not result.market_batches
    manifests = list(tmp_path.rglob("manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest["requests"][0]["raw_body_redacted"] is True
    assert "synthetic-other-secret" not in result.exchanges[0].raw_json


def test_safe_retry_after_is_retained_for_429_without_sleep_or_retry(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", _forbidden)
    headers = (*_headers(), ("Retry-After", "60"))
    transport = FakeTransport(_response(status=429, headers=headers))
    result = _execute(tmp_path, transport)
    assert result.transport_failures == ("HTTP_429",)
    assert dict(result.exchanges[0].usage_headers)["retry-after"] == "60"
    assert len(transport.calls) == 1
    rows = list(tmp_path.rglob("requests.jsonl"))
    assert len(rows) == 1
    assert "retry-after" in rows[0].read_text()


@pytest.mark.parametrize("malformed_error", [[], {"detail": SECRET}, "unknown-secret-error:" + SECRET])
def test_malformed_transport_error_retains_exchange_without_raising_or_secret_leak(tmp_path, malformed_error):
    response = replace(_response(), error=malformed_error)
    transport = FakeTransport(response)
    result = _execute(tmp_path, transport)
    assert result.status == "FAILED"
    assert result.transport_failures == ("INVALID_RESPONSE_SHAPE",)
    assert len(result.exchanges) == len(transport.calls) == 1
    assert list(tmp_path.rglob("COMPLETE"))


@pytest.mark.parametrize("bad_time", [NOW - timedelta(seconds=1), RuntimeError("clock: " + SECRET)])
def test_post_request_clock_failure_preserves_response_and_stops(tmp_path, bad_time):
    values = iter((NOW, bad_time))

    def clock():
        value = next(values)
        if isinstance(value, BaseException):
            raise value
        return value

    transport = FakeTransport(_response())
    result = _execute(tmp_path, transport, clock=clock)
    assert result.status == "FAILED"
    assert result.transport_failures == ("CLOCK_FAILURE",)
    assert len(result.exchanges) == len(transport.calls) == 1
    assert result.exchanges[0].responded_at >= result.exchanges[0].requested_at
    assert json.loads(result.exchanges[0].raw_json) == _event()
    assert not result.market_batches


def test_later_clock_failure_preserves_previous_success_without_second_http(tmp_path):
    values = iter((NOW, NOW, RuntimeError("clock: " + SECRET)))

    def clock():
        value = next(values)
        if isinstance(value, BaseException):
            raise value
        return value

    transport = FakeTransport(_response())
    plan = _plan(events=[_event("event-a"), _event("event-b")])
    result = _execute(tmp_path, transport, plan, clock=clock)
    assert result.status == "PARTIAL"
    assert result.transport_failures == ("CLOCK_FAILURE",)
    assert len(result.market_batches) == 1
    assert len(result.exchanges) == len(transport.calls) == 1


def test_fresh_time_gate_checks_entire_supplied_slate_even_if_requests_are_reordered(tmp_path):
    plan = _plan(events=[_event("event-a", "2026-09-20T17:00:00Z"), _event("event-b")])
    plan = replace(plan, requests=tuple(reversed(plan.requests)))
    transport = FakeTransport()
    result = _execute(tmp_path, transport, plan,
                      clock=lambda: datetime(2026, 9, 20, 16, 58, tzinfo=timezone.utc))
    assert result.status == "FAILED"
    assert result.transport_failures == ("PREGAME_LEAD_TIME",)
    assert not transport.calls


def test_missing_http_status_cannot_become_successful_normalization(tmp_path):
    transport = FakeTransport(replace(_response(), status_code=None))
    result = _execute(tmp_path, transport)
    assert result.status == "FAILED"
    assert result.transport_failures == ("INVALID_RESPONSE_SHAPE",)
    assert not result.market_batches
    assert len(result.exchanges) == len(transport.calls) == 1
    assert list(tmp_path.rglob("COMPLETE"))


@pytest.mark.parametrize("logger_name", ["urllib3.connectionpool", "urllib3.util.retry"])
def test_debug_http_logger_blocks_session_construction_and_preserves_safe_failure(tmp_path, monkeypatch, logger_name):
    import logging
    from courtvision.sports.mlb.providers.the_odds_api_transport import RequestsOddsAPITransport

    constructed = []

    def forbidden_session():
        constructed.append(True)
        raise AssertionError("Unsafe HTTP logging must block before Session construction")

    monkeypatch.setattr(requests, "Session", forbidden_session)
    logger = logging.getLogger(logger_name)
    original_level = logger.level
    try:
        logger.setLevel(logging.DEBUG)
        response = RequestsOddsAPITransport(network_enabled=True).send(
            _plan().requests[0], api_key=SECRET, timeout_seconds=15, max_response_bytes=4096,
        )
    finally:
        logger.setLevel(original_level)
    assert response.error == "UNSAFE_HTTP_LOGGING"
    assert constructed == []
    assert SECRET not in repr(response)
    result = _execute(tmp_path, FakeTransport(response))
    assert result.status == "FAILED"
    assert result.transport_failures == ("UNSAFE_HTTP_LOGGING",)
    assert not result.market_batches


@pytest.mark.parametrize("connection_name", ["HTTPConnection", "HTTPSConnection"])
@pytest.mark.parametrize("module_name", ["http.client", "urllib3.connection"])
def test_http_client_wire_debug_blocks_session_before_secret_use(monkeypatch, connection_name, module_name):
    import importlib
    from courtvision.sports.mlb.providers.the_odds_api_transport import RequestsOddsAPITransport

    constructed = []

    def forbidden_session():
        constructed.append(True)
        raise AssertionError("Wire debug must block before Session construction")

    monkeypatch.setattr(requests, "Session", forbidden_session)
    monkeypatch.setattr(getattr(importlib.import_module(module_name), connection_name), "debuglevel", 1)
    response = RequestsOddsAPITransport(network_enabled=True).send(
        _plan().requests[0], api_key=SECRET, timeout_seconds=15, max_response_bytes=4096,
    )
    assert response.error == "UNSAFE_HTTP_LOGGING"
    assert constructed == []
    assert SECRET not in repr(response)
