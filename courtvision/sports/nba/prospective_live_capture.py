"""Bounded factual NBA custody rehearsal; captures never qualify model inputs.

Each run permanently reserves at most two physical GET attempts. Interrupted
attempts and unsuccessful responses stop the run. No retries, redirects, mutable
cache, sportsbook routes or credentials in persisted request identity.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import time
from typing import Callable, Mapping
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from courtvision.sports.nba.prospective_evidence import (
    ProspectiveEvidenceError, _check_body, _decode_json, _inspection_text_forms, _safe_json, canonical_bytes, digest,
    claim_directory, immutable, plain_path, read_document, require_date, require_hash, require_id,
    utc_clock, write_once,
)

LIVE_SCHEMA = "nba-factual-custody-rehearsal-v1"
MAX_BODY_BYTES = 1_048_576
_ORIGINS = {"balldontlie": "https://api.balldontlie.io",
            "sportsdataio": "https://api.sportsdata.io"}
_METADATA = frozenset({"content-type", "content-length", "x-ratelimit-limit",
    "x-ratelimit-remaining", "x-ratelimit-reset", "x-requests-remaining", "retry-after"})
_PLAN_FIELDS = frozenset({"schema_version", "run_id", "provider", "repository_commit_sha",
    "capture_mode", "max_attempts", "min_spacing_seconds", "created_at_utc", "plan_sha256"})
_INTENT_FIELDS = frozenset({"ordinal", "plan_sha256", "request", "request_sha256",
    "reserved_at_utc", "previous_receipt_sha256", "intent_sha256"})
_RECEIPT_FIELDS = frozenset({"intent_sha256", "state", "capture_sha256",
    "error_code", "receipt_sha256"})
_CAPTURE_FIELDS = frozenset({"schema_version", "capture_mode", "intent_sha256",
    "request_sha256", "requested_at_utc", "responded_at_utc", "duration_seconds",
    "http_status", "response_metadata", "raw_body_sha256", "raw_body_byte_length",
    "capture_sha256"})
_FAILURES = frozenset({"TRANSPORT_FAILED", "RESPONSE_REJECTED", "CLOCK_INVALID",
                      "ARTIFACT_WRITE_FAILED", "PREREQUISITE_FAILED"})


class LiveCaptureError(ProspectiveEvidenceError):
    """Sanitized custody or transport failure; never includes private auth."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(clock: datetime) -> str:
    if not isinstance(clock, datetime) or clock.utcoffset() != timedelta(0):
        raise LiveCaptureError("UTC clock required")
    return clock.isoformat().replace("+00:00", "Z")


def _hash_document(document: dict, field: str) -> dict:
    document[field] = digest(document)
    return document


def _check_hash(document: dict, fields: frozenset, field: str) -> None:
    if set(document) != fields:
        raise LiveCaptureError("artifact fields differ")
    _safe_json(document, semantic_fields=True)
    require_hash(document[field])
    if digest({key: value for key, value in document.items() if key != field}) != document[field]:
        raise LiveCaptureError("artifact hash mismatch")


def _route(provider: str, ordinal: int, endpoint: str, parameters: dict) -> str:
    _safe_json(parameters, semantic_fields=True)
    if provider == "balldontlie":
        if ordinal == 1 and endpoint == "/account/v1/subscriptions" and not parameters:
            return "operational_entitlement"
        if ordinal == 2 and endpoint == "/v1/games":
            if set(parameters) != {"dates[]", "season_type", "per_page"}:
                raise LiveCaptureError("schedule parameters differ")
            dates = parameters["dates[]"]
            if not isinstance(dates, list) or len(dates) != 1:
                raise LiveCaptureError("one explicit operating date required")
            require_date(dates[0])
            if parameters["season_type"] != "preseason" or type(parameters["per_page"]) is not int or parameters["per_page"] != 100:
                raise LiveCaptureError("explicit bounded preseason query required")
            return "factual_schedule"
    if provider == "sportsdataio" and not parameters:
        if ordinal == 1 and endpoint == "/v3/nba/scores/json/CurrentSeason":
            return "operational_season"
        if ordinal == 2 and re.fullmatch(r"/v3/nba/scores/json/SchedulesBasic/[12][0-9]{3}PRE", endpoint or ""):
            return "factual_schedule"
    raise LiveCaptureError("endpoint or request order is prohibited")


def _request(provider: str, ordinal: int, endpoint: str, parameters: Mapping) -> dict:
    # Detach caller objects before reservation and before actual URL construction.
    import json
    if (not isinstance(provider, str) or provider not in _ORIGINS
            or type(ordinal) is not int or ordinal not in (1, 2)
            or not isinstance(endpoint, str)):
        raise LiveCaptureError("request route shape is invalid")
    if not isinstance(parameters, Mapping):
        raise LiveCaptureError("request parameters must be an object")
    params = json.loads(canonical_bytes(parameters))
    if not isinstance(params, dict):
        raise LiveCaptureError("request parameters must be an object")
    role = _route(provider, ordinal, endpoint, params)
    return {"provider": provider, "origin": _ORIGINS[provider], "method": "GET",
            "endpoint": endpoint, "parameters": params, "source_role": role}


def _validate_request(request: dict, provider: str, ordinal: int) -> None:
    if not isinstance(request, dict) or set(request) != {"provider", "origin", "method", "endpoint", "parameters", "source_role"}:
        raise LiveCaptureError("request fields differ")
    expected = _request(provider, ordinal, request["endpoint"], request["parameters"])
    if canonical_bytes(request) != canonical_bytes(expected):
        raise LiveCaptureError("request identity differs")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _http_once(provider: str, endpoint: str, parameters: dict, credential: str) -> tuple[int, dict, bytes]:
    query = urlencode(parameters, doseq=True)
    url = _ORIGINS[provider] + endpoint + ("?" + query if query else "")
    auth = "Authorization" if provider == "balldontlie" else "Ocp-Apim-Subscription-Key"
    value = ("Bearer " if endpoint == "/account/v1/subscriptions" else "") + credential
    request = Request(url, headers={auth: value, "Accept": "application/json",
                                   "Accept-Encoding": "identity"}, method="GET")
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        response = opener.open(request, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        body = response.read(MAX_BODY_BYTES + 1)
        if len(body) > MAX_BODY_BYTES:
            raise LiveCaptureError("response exceeds custody limit")
        encoding = response.headers.get("Content-Encoding", "identity").strip().casefold()
        if encoding not in {"", "identity"}:
            raise LiveCaptureError("encoded response prohibited")
        metadata = {}
        for key, value in response.headers.items():
            name = key.casefold()
            if name in _METADATA:
                if name in metadata:
                    raise LiveCaptureError("duplicate response metadata is ambiguous")
                metadata[name] = value
        return response.code, metadata, body


@dataclass(frozen=True, slots=True)
class VerifiedLiveCapture:
    manifest: Mapping
    raw_body: bytes


@dataclass(frozen=True, slots=True)
class VerifiedLivePilot:
    plan: Mapping
    receipts: tuple[Mapping, ...]
    captures: tuple[VerifiedLiveCapture, ...]

    @property
    def attempts_reserved(self) -> int:
        return len(self.receipts)

    @property
    def qualification_status(self) -> str:
        return "CUSTODY_ONLY"

    @property
    def qualified_minutes_inputs(self) -> int:
        return 0

    @property
    def required_minutes_inputs(self) -> int:
        return 16

    @property
    def can_continue(self) -> bool:
        if (len(self.receipts) >= self.plan["max_attempts"]
                or any(receipt["state"] != "SUCCESS" for receipt in self.receipts)):
            return False
        try:
            at = utc_clock(self.captures[-1].manifest["responded_at_utc"] if self.captures
                           else self.plan["created_at_utc"])
            _assert_next_allowed(self.plan, self.captures, None, at)
        except (ProspectiveEvidenceError, TypeError, ValueError):
            return False
        return True


_JSON_ESCAPE_RUN = re.compile(r'(?:\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))+')
_PRIVATE_ESCAPE_PASSES = 32


def _inspect_json_escape_run(match: re.Match[str]) -> str:
    # The matched run contains only supported JSON escapes. Decode contiguous
    # surrogate pairs together, leaving ordinary Unicode and source bytes alone.
    return _decode_json(('"' + match.group() + '"').encode("ascii"))


def _contains_private(value: object, marker: str, *, _depth: int = 0,
                      _seen: set[str] | None = None) -> bool:
    """Inspect supported text encodings without replacing retained response bytes."""
    if _depth > _PRIVATE_ESCAPE_PASSES:
        raise LiveCaptureError("response encoding nesting exceeds inspection limit")
    seen = set() if _seen is None else _seen
    if isinstance(value, str):
        # Inspect the original before form decoding can reinterpret literal plus.
        if marker in value.casefold():
            return True
        forms = _inspection_text_forms(value, max_layers=_PRIVATE_ESCAPE_PASSES - _depth)
        for layer, inspection in enumerate(forms):
            if marker in inspection.casefold():
                return True
            if inspection in seen:
                continue
            seen.add(inspection)
            inspected = inspection.lstrip()
            while inspected.startswith("\ufeff"):
                inspected = inspected[1:].lstrip()
            if inspected.startswith(("{", "[")):
                # The container owns its syntax; decode escapes only in leaves.
                decoded = _decode_json(inspection.encode("utf-8"))
                if _contains_private(decoded, marker, _depth=_depth + layer + 1, _seen=seen):
                    return True
                continue
            decoded_escapes = _JSON_ESCAPE_RUN.sub(_inspect_json_escape_run, inspection)
            if decoded_escapes != inspection and _contains_private(
                    decoded_escapes, marker, _depth=_depth + layer + 1, _seen=seen):
                return True
        return False
    if isinstance(value, Mapping):
        return any(_contains_private(key, marker, _depth=_depth, _seen=seen)
                   or _contains_private(item, marker, _depth=_depth, _seen=seen)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_private(item, marker, _depth=_depth, _seen=seen) for item in value)
    return False


def _quota_allows_next(metadata: Mapping) -> bool:
    # Absent quota stays unknown; a reported exhausted or ambiguous limit stops.
    numeric = {"x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
               "x-requests-remaining"}
    observed = {}
    for name in numeric & metadata.keys():
        value = metadata[name]
        if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
            return False
        try:
            observed[name] = int(value)
        except ValueError:
            return False
        if name != "x-ratelimit-reset" and observed[name] == 0:
            return False
    if ("x-ratelimit-limit" in observed and "x-ratelimit-remaining" in observed
            and observed["x-ratelimit-remaining"] > observed["x-ratelimit-limit"]):
        return False
    if "retry-after" in metadata:
        value = metadata["retry-after"]
        if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None or value.strip("0"):
            return False
    return True


def _operational_endpoint(provider: str, capture: VerifiedLiveCapture, at: datetime) -> str | None:
    if capture.manifest["http_status"] != 200:
        raise LiveCaptureError("operational response does not establish access")
    document = _decode_json(capture.raw_body)
    if provider == "balldontlie":
        if not isinstance(document, dict) or not isinstance(document.get("data"), list):
            raise LiveCaptureError("NBA entitlement shape is unresolved")
        rows = [row for row in document["data"] if isinstance(row, dict) and row.get("sport") == "nba"]
        if len(rows) != 1:
            raise LiveCaptureError("NBA entitlement is missing or ambiguous")
        row = rows[0]
        if (not {"sport", "tier", "status", "current_period_end", "cancel_at_period_end"} <= row.keys()
                or type(row["cancel_at_period_end"]) is not bool):
            raise LiveCaptureError("NBA entitlement fields are unresolved")
        if row["tier"] == "free" and row["status"] is None and row["current_period_end"] is None:
            return None  # Games are in the documented free NBA endpoint scope.
        if (not isinstance(row["tier"], str) or row["tier"] not in {"paid", "paid_plus", "all_access_v3"}
                or not isinstance(row["status"], str) or row["status"] not in {"active", "trialing"}
                or utc_clock(row["current_period_end"]) <= at):
            raise LiveCaptureError("NBA entitlement does not establish current access")
        return None
    if not isinstance(document, dict):
        raise LiveCaptureError("observed preseason token is unresolved")
    token, season, phase = document.get("ApiSeason"), document.get("Season"), document.get("SeasonType")
    if (not isinstance(token, str) or re.fullmatch(r"[12][0-9]{3}PRE", token) is None
            or type(season) is not int or season != int(token[:4])
            or not ((type(phase) is int and phase == 2) or (type(phase) is str and phase == "2"))):
        raise LiveCaptureError("observed preseason token is unresolved")
    return "/v3/nba/scores/json/SchedulesBasic/" + token


def _assert_next_allowed(plan: Mapping, captures: tuple | list, request: Mapping | None,
                         at: datetime) -> None:
    if not captures:
        return
    if not _quota_allows_next(captures[-1].manifest["response_metadata"]):
        raise LiveCaptureError("reported quota does not permit another request")
    endpoint = _operational_endpoint(plan["provider"], captures[0], at)
    if endpoint is not None and request is not None and request["endpoint"] != endpoint:
        raise LiveCaptureError("schedule token differs from observed preseason")


def verify_live_pilot(run_root: str | Path) -> VerifiedLivePilot:
    root = plain_path(run_root)
    plan = read_document(root / "plan.json")
    _check_hash(plan, _PLAN_FIELDS, "plan_sha256")
    if (plan["schema_version"] != LIVE_SCHEMA or not isinstance(plan["provider"], str) or plan["provider"] not in _ORIGINS
            or not isinstance(plan["capture_mode"], str) or plan["capture_mode"] not in {"LIVE_HTTP", "TEST_DOUBLE"}
            or type(plan["max_attempts"]) is not int or not 1 <= plan["max_attempts"] <= 2
            or type(plan["min_spacing_seconds"]) is not int or plan["min_spacing_seconds"] < 15):
        raise LiveCaptureError("pilot policy differs")
    require_id(plan["run_id"])
    require_hash(plan["repository_commit_sha"], 40)
    utc_clock(plan["created_at_utc"])
    if root.name != plan["run_id"] or root.parent.name != LIVE_SCHEMA:
        raise LiveCaptureError("pilot path identity differs")
    names = {path.name for path in root.iterdir()}
    slots = sorted(names - {"plan.json"})
    if len(slots) > plan["max_attempts"] or slots != [f"{index:03d}" for index in range(1, len(slots) + 1)]:
        raise LiveCaptureError("attempt accounting differs")
    receipts, captures = [], []
    previous = None
    prior_response = utc_clock(plan["created_at_utc"])
    stopped = False
    for ordinal, name in enumerate(slots, 1):
        directory = plain_path(root / name)
        intent = read_document(directory / "intent.json")
        _check_hash(intent, _INTENT_FIELDS, "intent_sha256")
        _validate_request(intent["request"], plan["provider"], ordinal)
        if (type(intent["ordinal"]) is not int or intent["ordinal"] != ordinal
                or intent["plan_sha256"] != plan["plan_sha256"]
                or digest(intent["request"]) != intent["request_sha256"]
                or intent["previous_receipt_sha256"] != previous
                or utc_clock(intent["reserved_at_utc"]) < prior_response or stopped):
            raise LiveCaptureError("attempt chain or ordering differs")
        if ordinal > 1:
            reserved = utc_clock(intent["reserved_at_utc"])
            if reserved < prior_response + timedelta(seconds=plan["min_spacing_seconds"]):
                raise LiveCaptureError("request spacing differs")
            _assert_next_allowed(plan, captures, intent["request"], reserved)
        receipt = read_document(directory / "receipt.json")
        _check_hash(receipt, _RECEIPT_FIELDS, "receipt_sha256")
        if receipt["intent_sha256"] != intent["intent_sha256"]:
            raise LiveCaptureError("receipt intent differs")
        if not isinstance(receipt["state"], str) or receipt["state"] not in {"SUCCESS", "HTTP_STOP", "FAILURE"}:
            raise LiveCaptureError("receipt state is invalid")
        if receipt["state"] == "FAILURE":
            if (not isinstance(receipt["error_code"], str) or receipt["error_code"] not in _FAILURES or receipt["capture_sha256"] is not None
                    or {path.name for path in directory.iterdir()} != {"intent.json", "receipt.json"}):
                raise LiveCaptureError("failure receipt differs")
            stopped = True
        else:
            if {path.name for path in directory.iterdir()} != {"intent.json", "manifest.json", "body.bin", "receipt.json"}:
                raise LiveCaptureError("capture files differ")
            manifest = read_document(directory / "manifest.json")
            _check_hash(manifest, _CAPTURE_FIELDS, "capture_sha256")
            started = utc_clock(manifest["requested_at_utc"])
            ended = utc_clock(manifest["responded_at_utc"])
            metadata = manifest["response_metadata"]
            if (manifest["schema_version"] != LIVE_SCHEMA or manifest["capture_mode"] != plan["capture_mode"]
                    or manifest["intent_sha256"] != intent["intent_sha256"]
                    or manifest["request_sha256"] != intent["request_sha256"]
                    or not prior_response <= utc_clock(intent["reserved_at_utc"]) <= started <= ended
                    or type(manifest["duration_seconds"]) not in (int, float) or manifest["duration_seconds"] < 0
                    or type(manifest["http_status"]) is not int or not 100 <= manifest["http_status"] <= 599
                    or not isinstance(metadata, dict) or not set(metadata) <= _METADATA
                    or any(not isinstance(value, str) for value in metadata.values())
                    or receipt["capture_sha256"] != manifest["capture_sha256"]
                    or receipt["error_code"] is not None):
                raise LiveCaptureError("capture policy or clocks differ")
            if ordinal > 1:
                _assert_next_allowed(plan, captures, intent["request"], started)
            state = "SUCCESS" if 200 <= manifest["http_status"] < 300 else "HTTP_STOP"
            if receipt["state"] != state:
                raise LiveCaptureError("HTTP result state differs")
            body = plain_path(directory / "body.bin").read_bytes()
            if (type(manifest["raw_body_byte_length"]) is not int or len(body) != manifest["raw_body_byte_length"]
                    or len(body) > MAX_BODY_BYTES or hashlib.sha256(body).hexdigest() != manifest["raw_body_sha256"]):
                raise LiveCaptureError("raw body custody differs")
            _check_body(body)
            captures.append(VerifiedLiveCapture(immutable(manifest), body))
            prior_response = ended
            stopped = state != "SUCCESS"
        receipts.append(immutable(receipt))
        previous = receipt["receipt_sha256"]
    return VerifiedLivePilot(immutable(plan), tuple(receipts), tuple(captures))


class LivePilot:
    """Created by claim_live_pilot; an instance never retains a provider key."""

    def __init__(self, root: Path, transport: Callable | None):
        self.root = root
        self._transport = transport

    def fetch_next(self, *, endpoint: str, parameters: Mapping, credential: str) -> VerifiedLiveCapture:
        saved = verify_live_pilot(self.root)
        if not saved.can_continue:
            raise LiveCaptureError("pilot is stopped or exhausted")
        mode = "TEST_DOUBLE" if self._transport is not None else "LIVE_HTTP"
        if saved.plan["capture_mode"] != mode:
            raise LiveCaptureError("transport mode differs")
        if not isinstance(credential, str) or not credential or any(char.isspace() or ord(char) < 32 for char in credential):
            raise LiveCaptureError("private header credential required")
        ordinal = saved.attempts_reserved + 1
        request = _request(saved.plan["provider"], ordinal, endpoint, parameters)
        reserved = _now()
        _stamp(reserved)
        if reserved < utc_clock(saved.plan["created_at_utc"]):
            raise LiveCaptureError("reservation clock differs")
        _assert_next_allowed(saved.plan, saved.captures, request, reserved)
        if saved.captures and reserved < utc_clock(saved.captures[-1].manifest["responded_at_utc"]) + timedelta(seconds=saved.plan["min_spacing_seconds"]):
            raise LiveCaptureError("request spacing gate is not open")
        intent = _hash_document({"ordinal": ordinal, "plan_sha256": saved.plan["plan_sha256"],
            "request": request, "request_sha256": digest(request), "reserved_at_utc": _stamp(reserved),
            "previous_receipt_sha256": saved.receipts[-1]["receipt_sha256"] if saved.receipts else None}, "intent_sha256")
        directory = plain_path(self.root / f"{ordinal:03d}")
        claim_directory(directory)  # Exclusive durable attempt claim.
        write_once(directory / "intent.json", canonical_bytes(intent) + b"\n")
        # Reservation readback is mandatory before the sole transport invocation.
        if canonical_bytes(read_document(directory / "intent.json")) != canonical_bytes(intent):
            raise LiveCaptureError("reservation readback differs")
        started = _now()
        monotonic_start = time.monotonic()
        error_code = "TRANSPORT_FAILED"
        try:
            error_code = "CLOCK_INVALID"
            _stamp(started)
            if started < reserved:
                error_code = "CLOCK_INVALID"
                raise LiveCaptureError("request clock differs")
            error_code = "PREREQUISITE_FAILED"
            _assert_next_allowed(saved.plan, saved.captures, request, started)
            transport = self._transport if self._transport is not None else _http_once
            error_code = "TRANSPORT_FAILED"
            status, metadata, body = transport(
                saved.plan["provider"], endpoint, request["parameters"], credential)
            ended = _now()
            duration = time.monotonic() - monotonic_start
            error_code = "RESPONSE_REJECTED"
            _check_body(body)
            marker = credential.casefold()
            if (_contains_private(body.decode("utf-8"), marker)
                    or _contains_private(metadata, marker)):
                raise LiveCaptureError("response contains private credential")
            if len(body) > MAX_BODY_BYTES:
                raise LiveCaptureError("response exceeds custody limit")
            if type(status) is not int or not 100 <= status <= 599:
                raise LiveCaptureError("HTTP status is invalid")
            if not isinstance(metadata, dict) or not set(metadata) <= _METADATA or any(not isinstance(value, str) for value in metadata.values()):
                raise LiveCaptureError("response metadata differs")
            _safe_json(metadata, semantic_fields=True)
            error_code = "CLOCK_INVALID"
            if ended < started or started < utc_clock(intent["reserved_at_utc"]) or duration < 0:
                raise LiveCaptureError("response clock differs")
            manifest = _hash_document({"schema_version": LIVE_SCHEMA, "capture_mode": mode,
                "intent_sha256": intent["intent_sha256"], "request_sha256": intent["request_sha256"],
                "requested_at_utc": _stamp(started), "responded_at_utc": _stamp(ended),
                "duration_seconds": duration, "http_status": status, "response_metadata": metadata,
                "raw_body_sha256": hashlib.sha256(body).hexdigest(), "raw_body_byte_length": len(body)}, "capture_sha256")
            error_code = "ARTIFACT_WRITE_FAILED"
            write_once(directory / "body.bin", body)
            write_once(directory / "manifest.json", canonical_bytes(manifest) + b"\n")
            receipt = _hash_document({"intent_sha256": intent["intent_sha256"],
                "state": "SUCCESS" if 200 <= status < 300 else "HTTP_STOP",
                "capture_sha256": manifest["capture_sha256"], "error_code": None}, "receipt_sha256")
            write_once(directory / "receipt.json", canonical_bytes(receipt) + b"\n")
        except Exception:
            # Partial files are retained and block all further requests. Never
            # serialize a transport exception, URL, auth header or partial body.
            if error_code != "ARTIFACT_WRITE_FAILED":
                receipt = _hash_document({"intent_sha256": intent["intent_sha256"],
                    "state": "FAILURE", "capture_sha256": None, "error_code": error_code}, "receipt_sha256")
                write_once(directory / "receipt.json", canonical_bytes(receipt) + b"\n")
            raise LiveCaptureError("attempt failed; immutable reservation retained") from None
        verified = verify_live_pilot(self.root)
        return verified.captures[-1]


def claim_live_pilot(journal_root: str | Path, *, run_id: str, provider: str,
                     repository_commit_sha: str, max_attempts: int = 2,
                     min_spacing_seconds: int = 15, transport: Callable | None = None) -> LivePilot:
    if not isinstance(provider, str) or provider not in _ORIGINS or type(max_attempts) is not int or not 1 <= max_attempts <= 2:
        raise LiveCaptureError("bounded configured provider plan required")
    if type(min_spacing_seconds) is not int or min_spacing_seconds < 15:
        raise LiveCaptureError("conservative rate spacing required")
    require_id(run_id)
    require_hash(repository_commit_sha, 40)
    plan = _hash_document({"schema_version": LIVE_SCHEMA, "run_id": run_id,
        "provider": provider, "repository_commit_sha": repository_commit_sha,
        "capture_mode": "TEST_DOUBLE" if transport is not None else "LIVE_HTTP",
        "max_attempts": max_attempts, "min_spacing_seconds": min_spacing_seconds,
        "created_at_utc": _stamp(_now())}, "plan_sha256")
    root = plain_path(Path(journal_root) / LIVE_SCHEMA / run_id)
    claim_directory(root)  # Never resume or replace an existing claim.
    write_once(root / "plan.json", canonical_bytes(plan) + b"\n")
    verify_live_pilot(root)
    return LivePilot(root, transport)
