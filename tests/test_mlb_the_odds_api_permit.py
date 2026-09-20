"""Concrete transport capability tests. All HTTP, sockets and DNS are mocked."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
import hashlib
import json
import socket
from threading import Barrier

import pytest
import requests

from courtvision.sports.mlb import market_ingestion_evidence as evidence
from courtvision.sports.mlb.providers import the_odds_api_live as live
from courtvision.sports.mlb.providers.the_odds_api_transport import (
    ExecutionPermitIssuer, LiveExecutionPermit, RequestsOddsAPITransport,
    create_execution_permit_issuer,
)


NOW = datetime(2026, 9, 20, 16, tzinfo=timezone.utc)
DAY = date(2026, 9, 20)
SECRET = "permit-test-secret-never-persist-987654"


def _forbidden(*args, **kwargs):
    raise AssertionError("Real HTTP, DNS and sockets are forbidden")


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(requests.sessions.Session, "request", _forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, _forbidden)
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes()
            assert hashlib.sha256(SECRET.encode()).hexdigest().encode() not in path.read_bytes()


def _event(event_id="event-a"):
    return {"id": event_id, "sport_key": "baseball_mlb",
            "commence_time": "2026-09-20T23:00:00Z", "home_team": "Home",
            "away_team": "Away", "bookmakers": []}


def _plan(*, count=1, discovery=False):
    config = live.MLBOddsIngestionConfig(
        operating_date=DAY, markets=("batter_hits",), regions="us", maximum_events=count,
        maximum_http_requests=count + 1, maximum_provider_credits=count,
        event_odds_declared_max_credit_cost=1, discovery_declared_max_credit_cost=0,
        discovery_zero_cost_verified=True, network_enabled=True,
    )
    return live.build_request_plan(config, captured_at=NOW,
        events=None if discovery else [_event(f"event-{index}") for index in range(count)])


def _claim(tmp_path, plan, run_id="permit-run"):
    return evidence.acquire_ingestion_claim(output_root=tmp_path / "evidence", run_id=run_id,
        operating_date=DAY, plan_identity=live.request_plan_identity(plan))


def _issuer(tmp_path, plan=None, run_id="permit-run"):
    plan = _plan() if plan is None else plan
    claim = _claim(tmp_path, plan, run_id)
    return plan, claim, create_execution_permit_issuer(plan, claim)


def _send(request, permit=None, *, run_id="permit-run", **overrides):
    args = dict(api_key=SECRET, timeout_seconds=15, max_response_bytes=4 * 1024 * 1024,
                permit=permit, run_id=run_id)
    args.update(overrides)
    return RequestsOddsAPITransport(network_enabled=True).send(request, **args)


def _exchange(plan, request, *, payload=None, status="OK"):
    payload = _event(request.event_id) if payload is None else payload
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    body = canonical.encode()
    last = request.declared_max_credit_cost
    return evidence.MLBOddsHTTPExchange(
        request_id=request.request_id, kind=request.kind, endpoint=request.endpoint,
        query=request.query, requested_at=NOW, responded_at=NOW, http_status=200,
        usage_headers=(("x-requests-last", str(last)),), canonical_json=canonical,
        canonical_json_sha256=hashlib.sha256(body).hexdigest(), received_body=body,
        received_body_sha256=hashlib.sha256(body).hexdigest(), status=status,
        declared_budget=plan.config.maximum_provider_credits,
        declared_cost_before_request=plan.config.maximum_provider_credits,
        declared_max_credit_cost=last, actual_reported_last_cost=last,
        actual_reported_used=last, actual_reported_remaining=100 - last,
    )


class MockResponse:
    status_code = 200

    def __init__(self, body, last=1):
        self.body = body
        self.headers = {"x-requests-last": str(last)}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, chunk_size):
        yield self.body


class MockSession:
    def __init__(self, calls, body, last=1, before_get=None):
        self.calls, self.body, self.last, self.before_get = calls, body, last, before_get

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def mount(self, prefix, adapter):
        assert prefix == "https://"
        assert adapter.max_retries.total == 0

    def get(self, url, **kwargs):
        if self.before_get is not None:
            self.before_get()
        self.calls.append(url)
        assert kwargs["params"]["apiKey"] == SECRET
        assert kwargs["allow_redirects"] is False
        assert self.trust_env is False
        return MockResponse(self.body, self.last)


def _mock_http(monkeypatch, *, body=b"{}", last=1, before_get=None):
    calls = []
    monkeypatch.setattr(requests, "Session", lambda: MockSession(calls, body, last, before_get))
    return calls


def test_direct_concrete_send_without_permit_precedes_session_and_credential_preparation(monkeypatch):
    constructions = []
    def forbidden_session():
        constructions.append(True)
        raise AssertionError("Session must not be constructed")
    monkeypatch.setattr(requests, "Session", forbidden_session)
    response = _send(_plan().requests[0], api_key=object())
    assert response.error == "EXECUTION_PERMIT_REQUIRED"
    assert constructions == []


@pytest.mark.parametrize("permit", [None, object(), LiveExecutionPermit(
    "permit-run", DAY, "a" * 64, "b" * 64, "c" * 64, 1)])
def test_invalid_and_forged_permits_fail_before_session(monkeypatch, permit):
    monkeypatch.setattr(requests, "Session", _forbidden)
    assert _send(_plan().requests[0], permit).error == "EXECUTION_PERMIT_REQUIRED"


@pytest.mark.parametrize("clone", [copy.copy, lambda item: replace(item),
                                   lambda item: replace(item, run_id="other-run")])
def test_copied_permit_metadata_has_no_authority(tmp_path, monkeypatch, clone):
    plan, claim, issuer = _issuer(tmp_path)
    permit = issuer.issue(plan.requests[0])
    copied = clone(permit)
    assert copied is not permit
    monkeypatch.setattr(requests, "Session", _forbidden)
    assert _send(plan.requests[0], copied).error == "EXECUTION_PERMIT_REQUIRED"
    issuer.close()


def test_permit_and_issuer_are_immutable_secret_free_and_claim_bound(tmp_path):
    plan, claim, issuer = _issuer(tmp_path)
    permit = issuer.issue(plan.requests[0])
    assert permit.claim_sha256 == claim.claim_sha256
    assert permit.plan_identity_sha256 == claim.plan_identity_sha256
    assert permit.operating_date == DAY
    assert permit.request_id == plan.requests[0].request_id
    assert SECRET not in repr(permit) + repr(issuer)
    with pytest.raises(FrozenInstanceError):
        permit.run_id = "different-run"
    with pytest.raises(FrozenInstanceError):
        issuer.run_id = "different-run"
    with pytest.raises(ValueError, match="EXECUTION_PERMIT_REQUIRED"):
        replace(issuer).issue(plan.requests[0])
    with pytest.raises(ValueError, match="EXECUTION_PERMIT_REQUIRED"):
        ExecutionPermitIssuer(issuer.run_id, issuer.plan_identity_sha256).issue(plan.requests[0])
    issuer.close()


@pytest.mark.parametrize("change", ["run", "event", "cost", "timeout", "size"])
def test_permit_cannot_authorize_another_run_or_request_scope(tmp_path, monkeypatch, change):
    plan, claim, issuer = _issuer(tmp_path)
    request = plan.requests[0]
    permit = issuer.issue(request)
    overrides = {}
    if change == "run":
        overrides["run_id"] = "another-run"
    elif change == "event":
        request = replace(request, event_id="another-event")
    elif change == "cost":
        request = replace(request, declared_max_credit_cost=2)
    elif change == "timeout":
        overrides["timeout_seconds"] = 30
    else:
        overrides["max_response_bytes"] = 1024
    monkeypatch.setattr(requests, "Session", _forbidden)
    assert _send(request, permit, **overrides).error == "EXECUTION_PERMIT_REQUIRED"
    issuer.close()


def test_mutated_frozen_permit_is_rejected_by_authoritative_metadata(tmp_path, monkeypatch):
    plan, claim, issuer = _issuer(tmp_path)
    permit = issuer.issue(plan.requests[0])
    object.__setattr__(permit, "run_id", "tampered-run")
    monkeypatch.setattr(requests, "Session", _forbidden)
    assert _send(plan.requests[0], permit).error == "EXECUTION_PERMIT_REQUIRED"
    issuer.close()


def test_valid_permit_is_single_use_with_mocked_http(tmp_path, monkeypatch):
    plan, claim, issuer = _issuer(tmp_path)
    request = plan.requests[0]
    permit = issuer.issue(request)
    calls = _mock_http(monkeypatch)
    assert _send(request, permit).error is None
    assert _send(request, permit).error == "EXECUTION_PERMIT_REQUIRED"
    assert len(calls) == 1
    issuer.close()


def test_next_permit_requires_consumed_request_and_exact_durable_stage(tmp_path, monkeypatch):
    plan, claim, issuer = _issuer(tmp_path, _plan(count=2))
    first, second = plan.requests
    permit = issuer.issue(first)
    with pytest.raises(ValueError, match="EXECUTION_PERMIT_REQUIRED"):
        issuer.issue(second)
    calls = _mock_http(monkeypatch)
    assert _send(first, permit).error is None
    with pytest.raises(ValueError, match="EXECUTION_PERMIT_REQUIRED"):
        issuer.issue(second)
    exchange = _exchange(plan, first)
    with pytest.raises(ValueError):
        issuer.after_staging(exchange)
    evidence.stage_ingestion_exchange(claim, exchange)
    issuer.after_staging(exchange)
    assert _send(second, issuer.issue(second)).error is None
    assert len(calls) == 2
    issuer.close()


def test_modified_durable_exchange_never_unlocks_next_request(tmp_path, monkeypatch):
    plan, claim, issuer = _issuer(tmp_path, _plan(count=2))
    first, second = plan.requests
    calls = _mock_http(monkeypatch)
    assert _send(first, issuer.issue(first)).error is None
    exchange = _exchange(plan, first)
    evidence.stage_ingestion_exchange(claim, exchange)
    (claim.staging_directory / "canonical" / f"{first.request_id}.json").write_bytes(b"{}")
    with pytest.raises(ValueError):
        issuer.after_staging(exchange)
    with pytest.raises(ValueError, match="EXECUTION_PERMIT_REQUIRED"):
        issuer.issue(second)
    assert len(calls) == 1
    issuer.close()


@pytest.mark.parametrize("status", ["HTTP_429", "ACCOUNTING_UNAVAILABLE", "ACCOUNTING_CONFLICT"])
def test_failed_exchange_never_unlocks_next_permit(tmp_path, monkeypatch, status):
    plan, claim, issuer = _issuer(tmp_path, _plan(count=2))
    first, second = plan.requests
    calls = _mock_http(monkeypatch)
    assert _send(first, issuer.issue(first)).error is None
    exchange = _exchange(plan, first, status=status)
    evidence.stage_ingestion_exchange(claim, exchange)
    issuer.after_staging(exchange)
    with pytest.raises(ValueError, match="EXECUTION_PERMIT_REQUIRED"):
        issuer.issue(second)
    assert len(calls) == 1
    issuer.close()


def test_discovery_only_authorizes_requests_selected_from_durable_response(tmp_path, monkeypatch):
    plan, claim, issuer = _issuer(tmp_path, _plan(discovery=True))
    discovery = plan.requests[0]
    calls = _mock_http(monkeypatch, last=0)
    assert _send(discovery, issuer.issue(discovery)).error is None
    exchange = _exchange(plan, discovery, payload=[_event("discovered-event")])
    evidence.stage_ingestion_exchange(claim, exchange)
    issuer.after_staging(exchange)
    request = live._odds_request(plan.config, "unobserved-event")
    with pytest.raises(ValueError, match="EXECUTION_PERMIT_REQUIRED"):
        issuer.issue(request)
    request = live._odds_request(plan.config, "discovered-event")
    assert _send(request, issuer.issue(request)).error is None
    assert len(calls) == 2
    issuer.close()


def test_factory_rechecks_validated_plan_and_durable_claim(tmp_path):
    plan = _plan()
    claim = _claim(tmp_path, plan)
    invalid = replace(plan, config=replace(plan.config, maximum_provider_credits=0))
    with pytest.raises(ValueError):
        create_execution_permit_issuer(invalid, claim)
    other = replace(plan, config=replace(plan.config, maximum_provider_credits=2))
    with pytest.raises(ValueError, match="EXECUTION_PERMIT_REQUIRED"):
        create_execution_permit_issuer(other, claim)
    claim.claim_path.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        create_execution_permit_issuer(plan, claim)


def test_durable_claim_cannot_create_replacement_execution_lane_after_close(tmp_path):
    plan, claim, issuer = _issuer(tmp_path)
    with pytest.raises(ValueError, match="EXECUTION_PERMIT_REQUIRED"):
        create_execution_permit_issuer(plan, claim)
    issuer.close()
    with pytest.raises(ValueError):
        create_execution_permit_issuer(plan, claim)
    assert claim.claim_path.is_file()
    assert claim.claim_path.with_name(f".{claim.run_id}.permit-issuer.json").is_file()


def test_validated_executor_supplies_permit_and_stages_before_next_mocked_http(tmp_path, monkeypatch):
    plan = _plan(count=2)
    calls = []
    sessions = []
    root = tmp_path / "executor-evidence"
    staging = root / "runs" / DAY.isoformat() / ".executor-run.staging"
    def session():
        index = len(sessions)
        body = json.dumps(_event(f"event-{index}")).encode()
        def before_get():
            if index:
                assert (staging / "exchanges" / f"{plan.requests[0].request_id}.json").is_file()
        result = MockSession(calls, body, before_get=before_get)
        sessions.append(result)
        return result
    monkeypatch.setattr(requests, "Session", session)
    result = live.execute_ingestion(plan, api_key=SECRET,
        transport=RequestsOddsAPITransport(network_enabled=True), output_root=root,
        run_id="executor-run", clock=lambda: NOW)
    assert result.status == "COMPLETE"
    assert result.executed_request_count == len(calls) == 2
    assert result.evidence_result is not None
    assert result.evidence_result.run_directory.is_dir()


def test_invalid_key_consumes_valid_permit_without_creating_session(tmp_path, monkeypatch):
    plan, claim, issuer = _issuer(tmp_path)
    request = plan.requests[0]
    permit = issuer.issue(request)
    constructions = []
    def forbidden_session():
        constructions.append(True)
        raise AssertionError("No session for invalid credentials")
    monkeypatch.setattr(requests, "Session", forbidden_session)
    assert _send(request, permit, api_key="").error == "INVALID_REQUEST"
    assert _send(request, permit).error == "EXECUTION_PERMIT_REQUIRED"
    assert constructions == []
    issuer.close()


def test_concurrent_consumption_of_one_permit_constructs_one_session(tmp_path, monkeypatch):
    plan, claim, issuer = _issuer(tmp_path)
    request = plan.requests[0]
    permit = issuer.issue(request)
    barrier = Barrier(2)
    calls, constructions = [], []
    def session():
        constructions.append(True)
        return MockSession(calls, b"{}")
    monkeypatch.setattr(requests, "Session", session)
    def execute():
        barrier.wait(timeout=5)
        return _send(request, permit)
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = [pool.submit(execute) for _ in range(2)]
        responses = [attempt.result(timeout=10) for attempt in attempts]
    assert sorted(response.error or "OK" for response in responses) == ["EXECUTION_PERMIT_REQUIRED", "OK"]
    assert len(constructions) == len(calls) == 1
    issuer.close()


def test_validated_executor_discovery_expands_only_after_durable_response(tmp_path, monkeypatch):
    plan = _plan(count=2, discovery=True)
    calls, sessions = [], []
    root = tmp_path / "discovery-evidence"
    staging = root / "runs" / DAY.isoformat() / ".discovery-run.staging"
    expected_requests = (plan.requests[0], *(live._odds_request(plan.config, f"event-{i}") for i in range(2)))
    def session():
        index = len(sessions)
        payload = [_event("event-0"), _event("event-1")] if index == 0 else _event(f"event-{index - 1}")
        def before_get():
            if index:
                assert (staging / "exchanges" / f"{expected_requests[index - 1].request_id}.json").is_file()
        result = MockSession(calls, json.dumps(payload).encode(), last=0 if index == 0 else 1,
                             before_get=before_get)
        sessions.append(result)
        return result
    monkeypatch.setattr(requests, "Session", session)
    result = live.execute_ingestion(plan, api_key=SECRET,
        transport=RequestsOddsAPITransport(network_enabled=True), output_root=root,
        run_id="discovery-run", clock=lambda: NOW)
    assert result.status == "COMPLETE"
    assert result.executed_request_count == len(calls) == 3
    assert result.reported_credit_cost == 2
    assert tuple(exchange.request_id for exchange in result.exchanges) == tuple(
        request.request_id for request in expected_requests)
    assert result.evidence_result is not None


def test_mutating_caller_config_cannot_expand_issued_authority(tmp_path, monkeypatch):
    plan, claim, issuer = _issuer(tmp_path)
    request = plan.requests[0]
    object.__setattr__(plan.config, "timeout_seconds", 60)
    object.__setattr__(plan.config, "max_response_bytes", 16 * 1024 * 1024)
    object.__setattr__(plan.config, "maximum_provider_credits", 1000)
    permit = issuer.issue(request)
    calls = _mock_http(monkeypatch)
    assert _send(request, permit, timeout_seconds=60,
                 max_response_bytes=16 * 1024 * 1024).error == "EXECUTION_PERMIT_REQUIRED"
    assert calls == []
    assert _send(request, permit).error is None
    assert len(calls) == 1
    issuer.close()


def test_mutating_caller_claim_cannot_rebind_permit_to_another_run(tmp_path, monkeypatch):
    plan, claim, issuer = _issuer(tmp_path)
    other_claim = _claim(tmp_path, plan, run_id="another-run")
    permit = issuer.issue(plan.requests[0])
    # Simulate replacement with fully valid, already durable material for run B.
    for name in claim.__dataclass_fields__:
        object.__setattr__(claim, name, getattr(other_claim, name))
    calls = _mock_http(monkeypatch)
    assert _send(plan.requests[0], permit, run_id="another-run").error == "EXECUTION_PERMIT_REQUIRED"
    assert calls == []
    assert _send(plan.requests[0], permit).error is None
    assert permit.run_id == issuer.run_id == "permit-run"
    assert len(calls) == 1
    issuer.close()
