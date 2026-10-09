"""Offline contracts for immutable, bounded NBA factual custody rehearsals."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import Message
import hashlib
import io
import json
import os
from pathlib import Path
import socket
from urllib.error import HTTPError

import pytest

from courtvision.sports.nba import prospective_live_capture as live
from courtvision.sports.nba.prospective_evidence import (
    ProspectiveEvidenceError, canonical_bytes, digest, read_document,
)


REPOSITORY_SHA = "ef5ab02ad3d28f6298c87a23a853e1289750a427"
CREDENTIAL = "SYNTHETIC-CREDENTIAL-A"
BDL_FIRST = "/account/v1/subscriptions"
SDI_FIRST = "/v3/nba/scores/json/CurrentSeason"
BDL_SECOND = "/v1/games"
SDI_SECOND = "/v3/nba/scores/json/SchedulesBasic/2027PRE"
BDL_PARAMETERS = {"dates[]": ["2026-10-08"], "season_type": "preseason", "per_page": 100}
BDL_PAID = {"data": [{"sport": "nba", "tier": "paid", "status": "active",
    "current_period_end": "2027-05-01T00:00:00Z", "cancel_at_period_end": False}]}
BDL_FREE = {"data": [{"sport": "nba", "tier": "free", "status": None,
    "current_period_end": None, "cancel_at_period_end": False}]}
SDI_PRE = {"Season": 2027, "StartYear": 2026, "EndYear": 2027,
           "SeasonType": "2", "ApiSeason": "2027PRE"}


def _body(value):
    # Deliberate formatting verifies custody of bytes, not reconstructed JSON.
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def _response(body, status=200, metadata=None):
    return status, {"content-type": "application/json"} if metadata is None else metadata, body


def _first(provider):
    return BDL_FIRST if provider == "balldontlie" else SDI_FIRST


def _second(provider):
    return (BDL_SECOND, json.loads(json.dumps(BDL_PARAMETERS))) if provider == "balldontlie" else (SDI_SECOND, {})


def _first_body(provider):
    return _body(BDL_PAID if provider == "balldontlie" else SDI_PRE)


def _snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def _resign(path, field, **changes):
    document = read_document(path)
    document.update(changes)
    document.pop(field)
    document[field] = digest(document)
    path.write_bytes(canonical_bytes(document) + b"\n")
    return document


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)

    def advance(self, seconds=15):
        self.value += timedelta(seconds=seconds)


class Transport:
    """Test double that witnesses durable reservation before each physical call."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.root = None

    def __call__(self, provider, endpoint, parameters, credential):
        ordinal = len(self.calls) + 1
        directory = self.root / f"{ordinal:03d}"
        intent = read_document(directory / "intent.json")
        assert intent["ordinal"] == ordinal
        assert digest({key: value for key, value in intent.items() if key != "intent_sha256"}) == intent["intent_sha256"]
        assert intent["request"]["endpoint"] == endpoint
        assert intent["request"]["parameters"] == parameters
        assert not (directory / "receipt.json").exists()
        assert credential.encode() not in (directory / "intent.json").read_bytes()
        self.calls.append((provider, endpoint, parameters, credential))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result() if callable(result) else result


@pytest.fixture(autouse=True)
def deny_real_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("offline custody tests must not access the network")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture
def clock(monkeypatch):
    value = Clock()
    monkeypatch.setattr(live, "_now", lambda: value.value)
    return value


def _claim(tmp_path, provider="balldontlie", responses=None, run_id="offline-custody", **options):
    transport = Transport(responses or [_response(_first_body(provider)), _response(b"[]")])
    pilot = live.claim_live_pilot(tmp_path, run_id=run_id, provider=provider,
        repository_commit_sha=REPOSITORY_SHA, transport=transport, **options)
    transport.root = pilot.root
    return pilot, transport


def _fetch_first(pilot, provider="balldontlie", credential=CREDENTIAL):
    return pilot.fetch_next(endpoint=_first(provider), parameters={}, credential=credential)


def _fetch_second(pilot, provider="balldontlie", credential=CREDENTIAL):
    endpoint, parameters = _second(provider)
    return pilot.fetch_next(endpoint=endpoint, parameters=parameters, credential=credential)


@pytest.mark.parametrize("provider,control", [
    ("balldontlie", BDL_PAID), ("balldontlie", BDL_FREE), ("sportsdataio", SDI_PRE),
])
def test_two_attempt_roundtrip_preserves_raw_bytes_and_never_qualifies_model_inputs(tmp_path, clock, provider, control):
    raw = _body(control)
    pilot, transport = _claim(tmp_path, provider, [_response(raw), _response(b"[ ]\n")])
    first = _fetch_first(pilot, provider)
    assert first.raw_body == raw
    assert first.manifest["raw_body_sha256"] == hashlib.sha256(raw).hexdigest()
    assert first.manifest["raw_body_byte_length"] == len(raw)
    assert first.manifest["capture_mode"] == "TEST_DOUBLE"
    assert first.manifest["requested_at_utc"] == first.manifest["responded_at_utc"] == "2026-10-08T18:00:00Z"
    clock.advance()
    second = _fetch_second(pilot, provider)
    assert second.raw_body == b"[ ]\n"
    verified = live.verify_live_pilot(pilot.root)
    assert verified.attempts_reserved == 2
    assert not verified.can_continue
    assert verified.qualified_minutes_inputs == 0
    assert verified.required_minutes_inputs == 16
    assert verified.qualification_status == "CUSTODY_ONLY"
    assert all(item["state"] == "SUCCESS" for item in verified.receipts)
    intent = read_document(pilot.root / "002" / "intent.json")
    assert intent["request"]["method"] == "GET"
    assert intent["request"]["source_role"] == "factual_schedule"
    assert intent["request"]["origin"] == {"balldontlie": "https://api.balldontlie.io", "sportsdataio": "https://api.sportsdata.io"}[provider]
    before = _snapshot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot, provider)
    assert len(transport.calls) == 2
    assert _snapshot(pilot.root) == before
    assert CREDENTIAL.encode() not in b"".join(before.values())


@pytest.mark.parametrize("provider,endpoint", [
    ("balldontlie", "/v1/odds"), ("balldontlie", "/account/v1/me"),
    ("balldontlie", "/account/v1/subscriptions/cancel"),
    ("balldontlie", "https://api.balldontlie.io/account/v1/subscriptions"),
    ("sportsdataio", "/v3/nba/odds/json/GameOddsByDate/2026-10-08"),
    ("sportsdataio", "/v3/nba/scores/json/../CurrentSeason"),
])
def test_forbidden_route_has_no_reservation_or_call(tmp_path, clock, provider, endpoint):
    pilot, transport = _claim(tmp_path, provider)
    before = _snapshot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        pilot.fetch_next(endpoint=endpoint, parameters={}, credential=CREDENTIAL)
    assert transport.calls == []
    assert _snapshot(pilot.root) == before


@pytest.mark.parametrize("parameters", [[], [["date", "2026-10-08"]], None, {"api_key": "synthetic-other"}])
def test_invalid_request_parameters_stop_before_reservation(tmp_path, clock, parameters):
    pilot, transport = _claim(tmp_path)
    before = _snapshot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        pilot.fetch_next(endpoint=BDL_FIRST, parameters=parameters, credential=CREDENTIAL)
    assert transport.calls == []
    assert _snapshot(pilot.root) == before


@pytest.mark.parametrize("options", [
    {"max_attempts": 0}, {"max_attempts": 3}, {"max_attempts": True},
    {"min_spacing_seconds": 0}, {"min_spacing_seconds": 14}, {"min_spacing_seconds": True},
])
def test_invalid_budget_or_spacing_cannot_claim_a_run(tmp_path, clock, options):
    with pytest.raises(ProspectiveEvidenceError):
        _claim(tmp_path, **options)
    assert not list(tmp_path.rglob("plan.json"))


def test_credential_rotation_cannot_change_logical_request_identity(tmp_path, clock):
    captures, intents = [], []
    for index, credential in enumerate((CREDENTIAL, "SYNTHETIC-CREDENTIAL-B")):
        pilot, transport = _claim(tmp_path, run_id=f"rotation-{index}")
        captures.append(_fetch_first(pilot, credential=credential))
        intents.append(read_document(pilot.root / "001" / "intent.json"))
        assert len(transport.calls) == 1
        assert credential.encode() not in b"".join(_snapshot(pilot.root).values())
        assert credential not in repr(pilot)
    assert intents[0]["request"] == intents[1]["request"]
    assert intents[0]["request_sha256"] == intents[1]["request_sha256"]
    assert captures[0].manifest["raw_body_sha256"] == captures[1].manifest["raw_body_sha256"]


@pytest.mark.parametrize("status", [301, 401, 403, 429])
def test_http_stop_is_recorded_once_and_blocks_later_calls(tmp_path, clock, status):
    pilot, transport = _claim(tmp_path, responses=[_response(b'{"message":"unavailable"}', status)])
    captured = _fetch_first(pilot)
    assert captured.manifest["http_status"] == status
    verified = live.verify_live_pilot(pilot.root)
    assert verified.receipts[0]["state"] == "HTTP_STOP"
    assert not verified.can_continue
    clock.advance()
    before = _snapshot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot)
    assert len(transport.calls) == 1
    assert _snapshot(pilot.root) == before


def test_reported_zero_quota_stops_but_absent_quota_is_not_fabricated(tmp_path, clock):
    blocked, transport = _claim(tmp_path, responses=[_response(_body(BDL_PAID), metadata={"x-ratelimit-remaining": "0"})])
    captured = _fetch_first(blocked)
    assert captured.manifest["response_metadata"] == {"x-ratelimit-remaining": "0"}
    assert not live.verify_live_pilot(blocked.root).can_continue
    clock.advance()
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(blocked)
    assert len(transport.calls) == 1
    allowed, _ = _claim(tmp_path, run_id="quota-unknown", responses=[_response(_body(BDL_PAID), metadata={})])
    unknown = _fetch_first(allowed)
    assert unknown.manifest["response_metadata"] == {}
    assert live.verify_live_pilot(allowed.root).can_continue


def test_transport_exception_is_sanitized_and_consumes_attempt_permanently(tmp_path, clock):
    pilot, transport = _claim(tmp_path, responses=[TimeoutError(f"private URL ?key={CREDENTIAL}")])
    with pytest.raises(live.LiveCaptureError) as caught:
        _fetch_first(pilot)
    assert CREDENTIAL not in str(caught.value)
    assert caught.value.__suppress_context__
    files = _snapshot(pilot.root)
    assert set(path.split("/")[-1] for path in files if path.startswith("001/")) == {"intent.json", "receipt.json"}
    assert CREDENTIAL.encode() not in b"".join(files.values())
    assert live.verify_live_pilot(pilot.root).attempts_reserved == 1
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot)
    assert len(transport.calls) == 1
    assert _snapshot(pilot.root) == files


@pytest.mark.parametrize("failed_file,expected_calls", [("intent.json", 0), ("manifest.json", 1)])
def test_partial_write_never_refunds_or_retries_attempt(tmp_path, clock, monkeypatch, failed_file, expected_calls):
    pilot, transport = _claim(tmp_path)
    original = live.write_once
    def fail_owned_write(path, body):
        if Path(path).name == failed_file:
            raise OSError("synthetic custody write failure")
        return original(path, body)
    monkeypatch.setattr(live, "write_once", fail_owned_write)
    with pytest.raises((OSError, ProspectiveEvidenceError)):
        _fetch_first(pilot)
    before = _snapshot(pilot.root)
    assert (pilot.root / "001").is_dir()
    assert len(transport.calls) == expected_calls
    with pytest.raises((OSError, ProspectiveEvidenceError)):
        _fetch_second(pilot)
    assert len(transport.calls) == expected_calls
    assert not (pilot.root / "002").exists()
    assert _snapshot(pilot.root) == before


@pytest.mark.parametrize("artifact", ["intent.json", "receipt.json", "body.bin"])
def test_corrupt_prior_attempt_stops_before_next_call(tmp_path, clock, artifact):
    pilot, transport = _claim(tmp_path)
    _fetch_first(pilot)
    target = pilot.root / "001" / artifact
    target.write_bytes(target.read_bytes() + b"corrupt")
    clock.advance()
    before = _snapshot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot)
    assert len(transport.calls) == 1
    assert _snapshot(pilot.root) == before


def test_spacing_gate_does_not_reserve_early_attempt(tmp_path, clock):
    pilot, transport = _claim(tmp_path)
    _fetch_first(pilot)
    clock.advance(14)
    before = _snapshot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot)
    assert len(transport.calls) == 1
    assert _snapshot(pilot.root) == before
    clock.advance(1)
    _fetch_second(pilot)
    assert len(transport.calls) == 2


def test_independently_resigned_disk_attempt_cannot_bypass_spacing(tmp_path, clock):
    pilot, _ = _claim(tmp_path)
    _fetch_first(pilot)
    clock.advance()
    _fetch_second(pilot)
    directory = pilot.root / "002"
    early = "2026-10-08T18:00:14Z"
    intent = _resign(directory / "intent.json", "intent_sha256", reserved_at_utc=early)
    manifest = _resign(directory / "manifest.json", "capture_sha256",
        intent_sha256=intent["intent_sha256"], requested_at_utc=early)
    _resign(directory / "receipt.json", "receipt_sha256", intent_sha256=intent["intent_sha256"], capture_sha256=manifest["capture_sha256"])
    with pytest.raises(ProspectiveEvidenceError):
        live.verify_live_pilot(pilot.root)


@pytest.mark.parametrize("records", [
    [], [{"sport": "mlb", "tier": "free", "status": None}],
    [{"sport": "nba", "tier": "unknown", "status": "active"}],
    [{"sport": "nba", "tier": "paid", "status": "canceled", "current_period_end": "2027-05-01T00:00:00Z"}],
    [{"sport": "nba", "tier": "paid", "status": "active", "current_period_end": "2026-01-01T00:00:00Z"}],
    BDL_FREE["data"] * 2,
])
def test_unqualified_observed_nba_entitlement_retains_control_and_blocks_games(tmp_path, clock, records):
    raw = _body({"data": records})
    pilot, transport = _claim(tmp_path, responses=[_response(raw)])
    assert _fetch_first(pilot).raw_body == raw
    assert not live.verify_live_pilot(pilot.root).can_continue
    clock.advance()
    before = _snapshot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot)
    assert len(transport.calls) == 1
    assert _snapshot(pilot.root) == before


@pytest.mark.parametrize("changes", [
    {"ApiSeason": "2027REG", "SeasonType": "1"},
    {"ApiSeason": "2027POST", "SeasonType": "3"},
    {"Season": 2026}, {"SeasonType": None},
])
def test_missing_or_conflicting_observed_preseason_witness_stops_sdi(tmp_path, clock, changes):
    raw = _body(dict(SDI_PRE, **changes))
    pilot, transport = _claim(tmp_path, "sportsdataio", [_response(raw)])
    assert _fetch_first(pilot, "sportsdataio").raw_body == raw
    assert not live.verify_live_pilot(pilot.root).can_continue
    clock.advance()
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot, "sportsdataio")
    assert len(transport.calls) == 1
    assert not (pilot.root / "002").exists()


def test_sdi_schedule_year_must_equal_observed_api_season_year(tmp_path, clock):
    pilot, transport = _claim(tmp_path, "sportsdataio")
    _fetch_first(pilot, "sportsdataio")
    clock.advance()
    before = _snapshot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        pilot.fetch_next(endpoint="/v3/nba/scores/json/SchedulesBasic/2026PRE", parameters={}, credential=CREDENTIAL)
    assert len(transport.calls) == 1
    assert _snapshot(pilot.root) == before


def test_capture_mode_string_cannot_promote_test_double_into_live_qualification(tmp_path, clock):
    pilot, transport = _claim(tmp_path)
    _resign(pilot.root / "plan.json", "plan_sha256", capture_mode="LIVE_HTTP")
    verified = live.verify_live_pilot(pilot.root)
    assert verified.qualified_minutes_inputs == 0
    assert verified.qualification_status == "CUSTODY_ONLY"
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_first(pilot)
    assert transport.calls == []


@pytest.mark.parametrize("kind", ["raw-echo", "escaped-json-echo", "bom-json-echo", "bom-metadata-echo", "retry-after-echo", "content-type-echo", "credential-label"])
def test_credential_echo_is_rejected_before_body_or_manifest_persistence(tmp_path, clock, kind):
    body, metadata = _body(BDL_PAID), {}
    if kind == "raw-echo":
        body = _body({"echo": CREDENTIAL})
    elif kind == "escaped-json-echo":
        escaped = "".join("\\u%04x" % ord(char) for char in CREDENTIAL)
        body = ('{"echo":"' + escaped + '"}').encode()
        assert CREDENTIAL.encode() not in body
    elif kind in {"bom-json-echo", "bom-metadata-echo"}:
        escaped = "".join("\\u%04x" % ord(char) for char in CREDENTIAL)
        encoded = '\ufeff{"echo":"' + escaped + '"}'
        if kind == "bom-json-echo":
            body = encoded.encode("utf-8")
            assert CREDENTIAL.encode() not in body
        else:
            metadata["content-type"] = encoded
            assert CREDENTIAL not in encoded
    elif kind.endswith("-echo"):
        metadata[kind.removesuffix("-echo")] = CREDENTIAL
    else:
        body = _body({"headers": [["Ocp-Apim-Subscription-Key", "synthetic-other-marker"]]})
    pilot, transport = _claim(tmp_path, responses=[_response(body, metadata=metadata)])
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_first(pilot)
    directory = pilot.root / "001"
    assert {path.name for path in directory.iterdir()} == {"intent.json", "receipt.json"}
    assert CREDENTIAL.encode() not in b"".join(_snapshot(pilot.root).values())
    assert not live.verify_live_pilot(pilot.root).can_continue
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot)
    assert len(transport.calls) == 1


def test_response_clock_reversal_keeps_only_sanitized_failure_receipt(tmp_path, clock):
    def reverse_clock():
        clock.advance(-1)
        return _response(_body(BDL_PAID))
    pilot, transport = _claim(tmp_path, responses=[reverse_clock])
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_first(pilot)
    assert len(transport.calls) == 1
    assert {path.name for path in (pilot.root / "001").iterdir()} == {"intent.json", "receipt.json"}
    assert not live.verify_live_pilot(pilot.root).can_continue


def test_existing_run_claim_cannot_be_replaced_or_resumed(tmp_path, clock):
    pilot, transport = _claim(tmp_path)
    before = _snapshot(pilot.root)
    with pytest.raises(FileExistsError):
        _claim(tmp_path)
    assert transport.calls == []
    assert _snapshot(pilot.root) == before


class Response:
    def __init__(self, body, status=200, headers=None):
        self.body = body
        self.code = status
        self.headers = Message() if headers is None else headers
        self.read_limits = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        self.read_limits.append(limit)
        return self.body[:limit]


def _fake_opener(monkeypatch, response=None, error=None):
    calls, handlers = [], []
    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            if error is not None:
                raise error
            return response
    def build(*provided):
        handlers.extend(provided)
        return Opener()
    monkeypatch.setattr(live, "build_opener", build)
    return calls, handlers


@pytest.mark.parametrize("provider,endpoint,auth", [
    ("balldontlie", BDL_FIRST, "Authorization"),
    ("sportsdataio", SDI_FIRST, "Ocp-Apim-Subscription-Key"),
])
def test_production_transport_uses_one_private_get_without_proxy_or_redirect(monkeypatch, provider, endpoint, auth):
    headers = Message()
    headers["Content-Type"] = "application/json"
    headers["X-RateLimit-Remaining"] = "5"
    headers["Set-Cookie"] = "SYNTHETIC-PRIVATE-COOKIE"
    response = Response(b"[ ]\n", headers=headers)
    calls, handlers = _fake_opener(monkeypatch, response)
    status, metadata, body = live._http_once(provider, endpoint, {}, CREDENTIAL)
    assert status == 200 and body == b"[ ]\n"
    assert metadata == {"content-type": "application/json", "x-ratelimit-remaining": "5"}
    assert len(calls) == 1
    request, timeout = calls[0]
    assert request.get_method() == "GET" and timeout == 30
    assert request.full_url == {"balldontlie": "https://api.balldontlie.io", "sportsdataio": "https://api.sportsdata.io"}[provider] + endpoint
    private_headers = {key.casefold(): value for key, value in request.header_items()}
    assert private_headers[auth.casefold()] == ("Bearer " if provider == "balldontlie" else "") + CREDENTIAL
    assert private_headers["accept-encoding"] == "identity"
    assert any(isinstance(handler, live.ProxyHandler) and handler.proxies == {} for handler in handlers)
    redirect = next(handler for handler in handlers if isinstance(handler, live._NoRedirect))
    assert redirect.redirect_request(request, None, 302, "redirect", {}, "https://example.invalid") is None
    assert response.read_limits == [live.MAX_BODY_BYTES + 1]


def test_http_error_response_is_captured_without_following_or_retrying(monkeypatch):
    headers = Message()
    headers["Content-Type"] = "application/json"
    error = HTTPError("https://api.balldontlie.io/account/v1/subscriptions", 302,
                      "moved", headers, io.BytesIO(b'{"message":"moved"}'))
    calls, _ = _fake_opener(monkeypatch, error=error)
    status, _, body = live._http_once("balldontlie", BDL_FIRST, {}, CREDENTIAL)
    assert status == 302 and body == b'{"message":"moved"}'
    assert len(calls) == 1


@pytest.mark.parametrize("defect", ["compression", "oversize", "conflicting-quota"])
def test_unsupported_wire_response_has_no_capture_and_no_hidden_retry(tmp_path, clock, monkeypatch, defect):
    headers = Message()
    body = _body(BDL_PAID)
    if defect == "compression":
        headers["Content-Encoding"] = "gzip"
    elif defect == "oversize":
        body = b"x" * (live.MAX_BODY_BYTES + 1)
    else:
        headers["X-RateLimit-Remaining"] = "5"
        headers["x-ratelimit-remaining"] = "0"
    calls, _ = _fake_opener(monkeypatch, Response(body, headers=headers))
    pilot = live.claim_live_pilot(tmp_path, run_id="offline-http-boundary", provider="balldontlie", repository_commit_sha=REPOSITORY_SHA)
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_first(pilot)
    assert len(calls) == 1
    assert {path.name for path in (pilot.root / "001").iterdir()} == {"intent.json", "receipt.json"}
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot)
    assert len(calls) == 1


@pytest.mark.parametrize("field", ["provider", "capture_mode"])
def test_resigned_malformed_plan_fields_are_domain_errors(tmp_path, clock, field):
    pilot, transport = _claim(tmp_path)
    _resign(pilot.root / "plan.json", "plan_sha256", **{field: []})
    before = _snapshot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        live.verify_live_pilot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_first(pilot)
    assert transport.calls == []
    assert _snapshot(pilot.root) == before


@pytest.mark.parametrize("shape", ["request", "parameters"])
def test_fully_resigned_malformed_nested_request_is_a_domain_error(tmp_path, clock, shape):
    pilot, transport = _claim(tmp_path)
    _fetch_first(pilot)
    directory = pilot.root / "001"
    request = read_document(directory / "intent.json")["request"]
    if shape == "request":
        request = None
    else:
        request["parameters"] = []
    intent = _resign(directory / "intent.json", "intent_sha256", request=request, request_sha256=digest(request))
    manifest = _resign(directory / "manifest.json", "capture_sha256", intent_sha256=intent["intent_sha256"], request_sha256=intent["request_sha256"])
    _resign(directory / "receipt.json", "receipt_sha256", intent_sha256=intent["intent_sha256"], capture_sha256=manifest["capture_sha256"])
    before = _snapshot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        live.verify_live_pilot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot)
    assert len(transport.calls) == 1
    assert _snapshot(pilot.root) == before


@pytest.mark.parametrize("provider,control", [
    ("balldontlie", {"data": [{"sport": "nba", "tier": [], "status": "active",
        "current_period_end": "2027-05-01T00:00:00Z", "cancel_at_period_end": False}]}),
    ("sportsdataio", dict(SDI_PRE, ApiSeason={"phase": "PRE"})),
])
def test_malformed_observed_control_retains_raw_custody_and_stops_next_call(tmp_path, clock, provider, control):
    raw = _body(control)
    pilot, transport = _claim(tmp_path, provider, [_response(raw)])
    assert _fetch_first(pilot, provider).raw_body == raw
    verified = live.verify_live_pilot(pilot.root)
    assert not verified.can_continue
    clock.advance()
    before = _snapshot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot, provider)
    assert len(transport.calls) == 1
    assert _snapshot(pilot.root) == before


def test_quota_remaining_above_reported_limit_stops_next_request(tmp_path, clock):
    metadata = {"x-ratelimit-limit": "2", "x-ratelimit-remaining": "3"}
    pilot, transport = _claim(tmp_path, responses=[_response(_body(BDL_PAID), metadata=metadata)])
    captured = _fetch_first(pilot)
    assert captured.manifest["response_metadata"] == metadata
    assert not live.verify_live_pilot(pilot.root).can_continue
    clock.advance()
    before = _snapshot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot)
    assert len(transport.calls) == 1
    assert _snapshot(pilot.root) == before


def test_access_expiry_after_durable_reservation_prevents_physical_get(tmp_path, clock, monkeypatch):
    control = json.loads(json.dumps(BDL_PAID))
    control["data"][0]["current_period_end"] = "2026-10-08T18:00:20Z"
    pilot, transport = _claim(tmp_path, responses=[_response(_body(control)), _response(b"[]")])
    _fetch_first(pilot)
    clock.advance(15)
    original = live.write_once
    def expire_after_durable_reservation(path, body):
        result = original(path, body)
        if Path(path).name == "intent.json" and Path(path).parent.name == "002":
            clock.advance(6)
        return result
    monkeypatch.setattr(live, "write_once", expire_after_durable_reservation)
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot)
    assert len(transport.calls) == 1
    directory = pilot.root / "002"
    assert {path.name for path in directory.iterdir()} == {"intent.json", "receipt.json"}
    assert read_document(directory / "intent.json")["reserved_at_utc"] == "2026-10-08T18:00:15Z"
    receipt = read_document(directory / "receipt.json")
    assert receipt["state"] == "FAILURE" and receipt["error_code"] == "PREREQUISITE_FAILED"
    verified = live.verify_live_pilot(pilot.root)
    assert verified.attempts_reserved == 2 and not verified.can_continue
    before = _snapshot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot)
    assert len(transport.calls) == 1
    assert _snapshot(pilot.root) == before


@pytest.mark.parametrize("encoding", ["utf-16-be", "utf-32-be"])
def test_nul_interleaved_actual_private_key_response_cannot_enter_custody(tmp_path, clock, encoding):
    body = json.dumps({"echo": CREDENTIAL}).encode(encoding)
    assert CREDENTIAL.encode() not in body and b"\0" in body
    pilot, transport = _claim(tmp_path, responses=[_response(body)])
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_first(pilot)
    assert {path.name for path in (pilot.root / "001").iterdir()} == {"intent.json", "receipt.json"}
    assert CREDENTIAL.encode() not in b"".join(_snapshot(pilot.root).values())
    assert not live.verify_live_pilot(pilot.root).can_continue
    before = _snapshot(pilot.root)
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot)
    assert len(transport.calls) == 1
    assert _snapshot(pilot.root) == before


@pytest.mark.parametrize("spelling", ["quoted-fragment", "unquoted", "mixed"])
@pytest.mark.parametrize("boundary", ["raw-text", "nested-nickname", "metadata"])
def test_escaped_ordinary_text_private_echo_never_enters_live_custody(tmp_path, clock, spelling, boundary):
    escaped = "".join("\\u%04x" % ord(char) for char in CREDENTIAL)
    if spelling == "quoted-fragment":
        text = '"' + escaped + '" Jordan'
    elif spelling == "unquoted":
        text = "Nickname " + escaped
    else:
        mixed = "".join(char if index % 2 == 0 else "\\u%04x" % ord(char)
                        for index, char in enumerate(CREDENTIAL))
        text = '"' + mixed + '" Jordan'
    assert CREDENTIAL not in text
    body, metadata = _body(BDL_PAID), {}
    if boundary == "raw-text":
        body = text.encode("utf-8")
    elif boundary == "nested-nickname":
        control = json.loads(json.dumps(BDL_PAID))
        control["data"][0]["nickname"] = {"display": text}
        body = _body(control)
    else:
        metadata["content-type"] = text
    pilot, transport = _claim(tmp_path, responses=[_response(body, metadata=metadata)])
    with pytest.raises(ProspectiveEvidenceError) as caught:
        _fetch_first(pilot)
    assert CREDENTIAL not in str(caught.value) and text not in str(caught.value)
    assert caught.value.__suppress_context__
    directory = pilot.root / "001"
    assert {path.name for path in directory.iterdir()} == {"intent.json", "receipt.json"}
    receipt = read_document(directory / "receipt.json")
    assert receipt["state"] == "FAILURE"
    assert receipt["error_code"] == "RESPONSE_REJECTED"
    assert receipt["capture_sha256"] is None
    before = _snapshot(pilot.root)
    assert CREDENTIAL.encode() not in b"".join(before.values())
    assert text.encode() not in b"".join(before.values())
    verified = live.verify_live_pilot(pilot.root)
    assert verified.attempts_reserved == 1 and not verified.can_continue
    clock.advance()
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot)
    assert len(transport.calls) == 1 and not (pilot.root / "002").exists()
    assert _snapshot(pilot.root) == before


@pytest.mark.parametrize("boundary", ["raw-text", "nested-nickname"])
def test_safe_quoted_nickname_and_unicode_are_preserved_exactly_in_live_custody(tmp_path, clock, boundary):
    text = '"Air" Jordan \\u03bb ' + chr(0x03BB)
    metadata = {"content-type": "text/plain; nickname=" + text}
    if boundary == "raw-text":
        body = text.encode("utf-8")
    else:
        control = json.loads(json.dumps(BDL_PAID))
        control["data"][0]["nickname"] = {"display": text}
        body = _body(control)
    pilot, transport = _claim(tmp_path, responses=[_response(body, metadata=metadata)])
    captured = _fetch_first(pilot)
    assert captured.raw_body == body
    assert captured.manifest["response_metadata"] == metadata
    assert (pilot.root / "001" / "body.bin").read_bytes() == body
    verified = live.verify_live_pilot(pilot.root)
    assert verified.receipts[0]["state"] == "SUCCESS"
    assert verified.captures[0].raw_body == body
    assert verified.captures[0].manifest["response_metadata"] == metadata
    assert verified.qualification_status == "CUSTODY_ONLY"
    assert verified.qualified_minutes_inputs == 0
    assert len(transport.calls) == 1

@pytest.mark.parametrize("spelling", ["full", "mixed", "repeated"])
@pytest.mark.parametrize("boundary", ["raw-text", "nested-nickname", "metadata"])
def test_percent_encoded_actual_private_echo_never_enters_live_custody(tmp_path, clock, spelling, boundary):
    escaped = "".join("%{:02X}".format(ord(char)) for char in CREDENTIAL)
    if spelling == "mixed":
        escaped = "".join(char if index % 2 == 0 else "%{:02X}".format(ord(char))
                          for index, char in enumerate(CREDENTIAL))
    elif spelling == "repeated":
        escaped = escaped.replace("%", "%25")
    text = "Nickname " + escaped
    assert CREDENTIAL not in text
    body, metadata = _body(BDL_PAID), {}
    if boundary == "raw-text":
        body = text.encode("utf-8")
    elif boundary == "nested-nickname":
        control = json.loads(json.dumps(BDL_PAID))
        control["data"][0]["nickname"] = {"display": text}
        body = _body(control)
    else:
        metadata["content-type"] = text
    pilot, transport = _claim(tmp_path, responses=[_response(body, metadata=metadata)])
    with pytest.raises(ProspectiveEvidenceError) as caught:
        _fetch_first(pilot)
    assert CREDENTIAL not in str(caught.value) and text not in str(caught.value)
    assert caught.value.__suppress_context__
    directory = pilot.root / "001"
    assert {path.name for path in directory.iterdir()} == {"intent.json", "receipt.json"}
    receipt = read_document(directory / "receipt.json")
    assert receipt["state"] == "FAILURE" and receipt["error_code"] == "RESPONSE_REJECTED"
    assert receipt["capture_sha256"] is None
    before = _snapshot(pilot.root)
    assert CREDENTIAL.encode() not in b"".join(before.values())
    assert text.encode() not in b"".join(before.values())
    verified = live.verify_live_pilot(pilot.root)
    assert verified.attempts_reserved == 1 and not verified.can_continue
    clock.advance()
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_second(pilot)
    assert len(transport.calls) == 1 and not (pilot.root / "002").exists()
    assert _snapshot(pilot.root) == before


@pytest.mark.parametrize("boundary", ["raw-text", "nested-nickname"])
def test_safe_percent_literals_plus_and_encoded_unicode_retain_exact_live_custody(tmp_path, clock, boundary):
    text = '"Air" Jordan C++ 99% efficient %CE%BB ' + chr(0x03BB)
    metadata = {"content-type": "text/plain; nickname=" + text}
    if boundary == "raw-text":
        body = text.encode("utf-8")
    else:
        control = json.loads(json.dumps(BDL_PAID))
        control["data"][0]["nickname"] = {"display": text}
        body = _body(control)
    pilot, transport = _claim(tmp_path, responses=[_response(body, metadata=metadata)])
    captured = _fetch_first(pilot)
    assert captured.raw_body == body
    assert captured.manifest["response_metadata"] == metadata
    assert (pilot.root / "001" / "body.bin").read_bytes() == body
    verified = live.verify_live_pilot(pilot.root)
    assert verified.receipts[0]["state"] == "SUCCESS"
    assert verified.captures[0].raw_body == body
    assert verified.captures[0].manifest["response_metadata"] == metadata
    assert verified.qualification_status == "CUSTODY_ONLY" and verified.qualified_minutes_inputs == 0
    assert len(transport.calls) == 1


def test_valid_live_json_with_percent_encoded_quotes_retains_exact_raw_custody(tmp_path, clock):
    context = {"query_url": "https://example.invalid/stats?label=x%22y",
        "quoted_scientific_name": "labelquote%22nickname",
        "description": 'coach "Al" uses the guard',
        "serialized_science": "%7B%22pace_adjustment%22%3A1.02%7D"}
    control = json.loads(json.dumps(BDL_PAID))
    control["data"][0]["source_context"] = context
    body = _body(control)
    assert b'coach \\"Al\\" uses the guard' in body
    pilot, transport = _claim(tmp_path, responses=[_response(body)])
    captured = _fetch_first(pilot)
    assert captured.raw_body == body
    assert captured.manifest["raw_body_sha256"] == hashlib.sha256(body).hexdigest()
    assert (pilot.root / "001" / "body.bin").read_bytes() == body
    verified = live.verify_live_pilot(pilot.root)
    assert verified.receipts[0]["state"] == "SUCCESS"
    assert verified.captures[0].raw_body == body
    assert json.loads(verified.captures[0].raw_body)["data"][0]["source_context"] == context
    assert verified.qualification_status == "CUSTODY_ONLY" and verified.qualified_minutes_inputs == 0
    assert len(transport.calls) == 1


def test_pilot_directory_claim_barrier_failure_returns_no_pilot_or_plan(
        tmp_path, clock, monkeypatch):
    transport = Transport([_response(_body(BDL_PAID))])
    root = tmp_path / live.LIVE_SCHEMA / "claim-failure"
    real_claim = live.claim_directory
    claims = []

    def fail_after_real_claim(path):
        real_claim(path)
        claims.append(path)
        raise OSError("synthetic directory-claim barrier failure")

    monkeypatch.setattr(live, "claim_directory", fail_after_real_claim)
    pilot = None
    with pytest.raises(OSError, match="directory-claim barrier failure"):
        pilot = live.claim_live_pilot(
            tmp_path, run_id="claim-failure", provider="balldontlie",
            repository_commit_sha=REPOSITORY_SHA, transport=transport)

    assert pilot is None and claims == [root]
    assert root.is_dir() and list(root.iterdir()) == []
    assert not (root / "plan.json").exists()
    assert transport.calls == []
    with pytest.raises(ProspectiveEvidenceError):
        live.verify_live_pilot(root)

    monkeypatch.setattr(live, "claim_directory", real_claim)
    with pytest.raises(FileExistsError):
        live.claim_live_pilot(
            tmp_path, run_id="claim-failure", provider="balldontlie",
            repository_commit_sha=REPOSITORY_SHA, transport=transport)
    assert list(root.iterdir()) == [] and transport.calls == []


def test_attempt_directory_claim_barrier_failure_blocks_retry_without_calls(
        tmp_path, clock, monkeypatch):
    pilot, transport = _claim(tmp_path)
    directory = pilot.root / "001"
    plan_bytes = (pilot.root / "plan.json").read_bytes()
    real_claim = live.claim_directory
    claims = []

    def fail_after_real_claim(path):
        real_claim(path)
        claims.append(path)
        raise OSError("synthetic directory-claim barrier failure")

    monkeypatch.setattr(live, "claim_directory", fail_after_real_claim)
    captured = None
    with pytest.raises(OSError, match="directory-claim barrier failure"):
        captured = _fetch_first(pilot)

    assert captured is None and claims == [directory]
    assert directory.is_dir() and list(directory.iterdir()) == []
    assert {path.name for path in pilot.root.iterdir()} == {"plan.json", "001"}
    assert (pilot.root / "plan.json").read_bytes() == plan_bytes
    assert transport.calls == []
    with pytest.raises(ProspectiveEvidenceError):
        live.verify_live_pilot(pilot.root)

    monkeypatch.setattr(live, "claim_directory", real_claim)
    before = _snapshot(pilot.root)
    clock.advance()
    with pytest.raises(ProspectiveEvidenceError):
        _fetch_first(pilot)
    assert transport.calls == [] and not (pilot.root / "002").exists()
    assert directory.is_dir() and list(directory.iterdir()) == []
    assert _snapshot(pilot.root) == before


def _namespace_barrier_operation(tmp_path, boundary):
    if boundary == "pilot":
        transport = Transport([_response(_body(BDL_PAID))])
        root = tmp_path / live.LIVE_SCHEMA / "namespace-failure"
        def invoke():
            return live.claim_live_pilot(tmp_path, run_id="namespace-failure",
                provider="balldontlie", repository_commit_sha=REPOSITORY_SHA,
                transport=transport)
        return root, root, transport, invoke
    pilot, transport = _claim(tmp_path)
    return pilot.root / "001", pilot.root, transport, lambda: _fetch_first(pilot)


def _native_directory_identity(kernel, information_type, handle):
    import ctypes
    info = information_type()
    assert kernel.GetFileInformationByHandle(handle, ctypes.byref(info))
    assert kernel.GetFileType(handle) == 1
    assert info.Attributes & 0x10 and not info.Attributes & 0x400
    return info.Volume, (info.IndexHigh << 32) | info.IndexLow


def _native_directory_path_identity(secure, path):
    import ctypes
    kernel, _, _, _, _, information_type = secure._windows_api()
    handle = kernel.CreateFileW(str(path), 0x80, 0x7, None, 3,
        0x02000000 | 0x00200000, None)
    assert handle and handle != ctypes.c_void_p(-1).value
    try:
        return _native_directory_identity(kernel, information_type, handle)
    finally:
        assert kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows namespace durability requires real native directory handles")
def test_windows_directory_claim_namespace_flush_uses_real_native_normal_flags_and_bottom_up_identities(tmp_path, monkeypatch):
    import ctypes
    from courtvision.sports.nba import prospective_io as secure
    root = tmp_path / "namespace-parent" / "namespace-leaf"
    root.mkdir(parents=True)
    kernel, native, _, attributes_type, status_type, information_type = secure._windows_api()
    expected = [_native_directory_path_identity(secure, path) for path in (root, *root.parents)]
    real_flush, real_create, real_root = native.NtFlushBuffersFileEx, native.NtCreateFile, kernel.CreateFileW
    flushed, handles, child_opens, root_opens = [], [], [], []
    def record_root(*args):
        root_opens.append((args[0], args[1], args[2], args[4], args[5]))
        return real_root(*args)
    def record_create(*args):
        attributes = ctypes.cast(args[2], ctypes.POINTER(attributes_type)).contents
        child_opens.append((args[1], args[6], args[7], attributes.Attributes, args[8]))
        return real_create(*args)
    def record_flush(handle, flags, parameters, length, io_status):
        assert flags == 0 and parameters is None and length == 0
        identity = _native_directory_identity(kernel, information_type, handle)
        status = real_flush(handle, flags, parameters, length, io_status)
        completion = ctypes.cast(io_status, ctypes.POINTER(status_type)).contents.Result.Status
        assert status == 0 and completion == 0
        flushed.append(identity)
        handles.append(handle)
        return status
    monkeypatch.setattr(kernel, "CreateFileW", record_root)
    monkeypatch.setattr(native, "NtCreateFile", record_create)
    monkeypatch.setattr(native, "NtFlushBuffersFileEx", record_flush)
    secure.sync_directory_namespace(root)
    assert flushed == expected
    assert root_opens == [(root.anchor, 0x1000A4, 0x1, 3, 0x02000000 | 0x00200000)]
    assert len(child_opens) == len(expected) - 1
    for access, share, disposition, attributes, options in child_opens:
        assert access == 0x1000A4 and share == 0x1 and disposition == 0x1
        assert attributes & 0x1000
        assert options & 0x00200000 and options & 0x1 and options & 0x20
    for handle in handles:
        info = information_type()
        assert not kernel.GetFileInformationByHandle(handle, ctypes.byref(info))


@pytest.mark.skipif(os.name != "nt", reason="Windows namespace failures require real native directory flush calls")
@pytest.mark.parametrize("boundary,target,defect", [
    ("pilot", "leaf", "status"), ("pilot", "parent", "completion"),
    ("attempt", "leaf", "completion"), ("attempt", "parent", "status"),
])
def test_windows_directory_claim_native_namespace_failure_retains_budget_before_http(
        tmp_path, clock, monkeypatch, boundary, target, defect):
    import ctypes
    from courtvision.sports.nba import prospective_io as secure
    directory, run_root, transport, invoke = _namespace_barrier_operation(tmp_path, boundary)
    kernel, native, _, _, status_type, information_type = secure._windows_api()
    real_flush, real_namespace = native.NtFlushBuffersFileEx, live.sync_directory_namespace
    expected, flushed, injected = [], [], []
    target_identity = None
    def observe_namespace(path):
        nonlocal target_identity
        assert path == directory
        expected.extend(_native_directory_path_identity(secure, item)
            for item in (directory, directory.parent))
        target_identity = expected[0 if target == "leaf" else 1]
        return real_namespace(path)
    def fail_reported_native_result(handle, flags, parameters, length, io_status):
        assert flags == 0 and parameters is None and length == 0
        identity = _native_directory_identity(kernel, information_type, handle)
        status = real_flush(handle, flags, parameters, length, io_status)
        completion = ctypes.cast(io_status, ctypes.POINTER(status_type)).contents
        assert status == 0 and completion.Result.Status == 0
        flushed.append(identity)
        if identity == target_identity:
            injected.append(identity)
            denied = ctypes.c_int32(0xC0000022).value
            if defect == "completion":
                completion.Result.Status = denied
                return 0
            return denied
        return status
    monkeypatch.setattr(live, "sync_directory_namespace", observe_namespace)
    monkeypatch.setattr(native, "NtFlushBuffersFileEx", fail_reported_native_result)
    result = None
    with pytest.raises(OSError) as caught:
        result = invoke()
    assert result is None and caught.value.winerror == 5
    assert CREDENTIAL not in str(caught.value)
    assert injected == [target_identity]
    assert flushed == expected[:1 if target == "leaf" else 2]
    assert transport.calls == []
    assert {path.name for path in directory.iterdir()} == (
        {"plan.json"} if boundary == "pilot" else {"intent.json"})
    before = _snapshot(run_root)
    assert CREDENTIAL.encode() not in b"".join(before.values())
    if boundary == "attempt":
        assert read_document(directory / "intent.json")["ordinal"] == 1
        with pytest.raises(ProspectiveEvidenceError):
            live.verify_live_pilot(run_root)
    monkeypatch.setattr(native, "NtFlushBuffersFileEx", real_flush)
    monkeypatch.setattr(live, "sync_directory_namespace", real_namespace)
    with pytest.raises(FileExistsError if boundary == "pilot" else ProspectiveEvidenceError):
        invoke()
    assert transport.calls == [] and not (run_root / "002").exists()
    assert _snapshot(run_root) == before


@pytest.mark.parametrize("boundary", ["pilot", "attempt"])
def test_directory_claim_namespace_failure_blocks_return_or_http_without_replacing_claim(
        tmp_path, clock, monkeypatch, boundary):
    directory, run_root, transport, invoke = _namespace_barrier_operation(tmp_path, boundary)
    real_namespace, barriers = live.sync_directory_namespace, []
    def fail_after_real_namespace(path):
        assert path == directory
        real_namespace(path)
        barriers.append(path)
        raise OSError("synthetic completed namespace barrier failure")
    monkeypatch.setattr(live, "sync_directory_namespace", fail_after_real_namespace)
    result = None
    with pytest.raises(OSError, match="completed namespace barrier failure"):
        result = invoke()
    assert result is None and barriers == [directory] and transport.calls == []
    assert {path.name for path in directory.iterdir()} == (
        {"plan.json"} if boundary == "pilot" else {"intent.json"})
    before = _snapshot(run_root)
    assert CREDENTIAL.encode() not in b"".join(before.values())
    if boundary == "attempt":
        with pytest.raises(ProspectiveEvidenceError):
            live.verify_live_pilot(run_root)
    monkeypatch.setattr(live, "sync_directory_namespace", real_namespace)
    with pytest.raises(FileExistsError if boundary == "pilot" else ProspectiveEvidenceError):
        invoke()
    assert transport.calls == [] and not (run_root / "002").exists()
    assert _snapshot(run_root) == before
