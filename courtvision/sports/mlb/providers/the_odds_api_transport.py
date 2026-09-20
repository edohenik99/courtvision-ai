"""Bounded, single-attempt HTTPS transport; no credentials or I/O at import."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import json
from threading import RLock
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .the_odds_api_live import MLBOddsEventRequest, MLBOddsRequestPlan
    from ..market_ingestion_evidence import MLBOddsHTTPExchange, MLBOddsRunClaim


@dataclass(frozen=True, slots=True)
class OddsAPIHTTPResponse:
    status_code: int | None
    headers: tuple[tuple[str, str], ...]
    body: bytes
    error: str | None = None


class OddsAPITransport(Protocol):
    def send(self, request: MLBOddsEventRequest, *, api_key: str,
             timeout_seconds: float, max_response_bytes: int) -> OddsAPIHTTPResponse: ...


@dataclass(frozen=True, slots=True)
class LiveExecutionPermit:
    """One request capability; copied or reconstructed metadata is not authority.

    No credentials are retained. The live issuer registry verifies object identity,
    immutable metadata, claimed run, exact request and a single consumption. This
    is an in-process capability boundary, not a sandbox against arbitrary Python.
    """

    run_id: str
    operating_date: date
    claim_sha256: str
    plan_identity_sha256: str
    request_id: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class ExecutionPermitIssuer:
    run_id: str
    plan_identity_sha256: str

    def issue(self, request: MLBOddsEventRequest) -> LiveExecutionPermit:
        from ..market_ingestion_evidence import validate_ingestion_claim
        with _CAPABILITY_LOCK:
            state = _issuer_state(self)
            validate_ingestion_claim(state.claim)
            if (state.closed or state.current_permit is not None or state.waiting_request is not None
                    or not state.pending or request != state.pending[0]):
                raise ValueError("EXECUTION_PERMIT_REQUIRED")
            config = state.plan.config
            if (state.attempts >= config.maximum_http_requests
                    or state.actual_cost + sum(r.declared_max_credit_cost for r in state.pending)
                    > config.maximum_provider_credits):
                raise ValueError("EXECUTION_PERMIT_REQUIRED")
            permit = LiveExecutionPermit(
                state.claim.run_id, config.operating_date, state.claim.claim_sha256,
                state.claim.plan_identity_sha256, request.request_id, state.attempts + 1,
            )
            state.current_permit = permit
            state.permit_metadata = _permit_metadata(permit)
            _PERMITS[id(permit)] = (permit, state)
            return permit

    def after_staging(self, exchange: MLBOddsHTTPExchange) -> None:
        """Advance only after the consumed request has exact durable evidence."""
        from ..market_ingestion_evidence import validate_staged_ingestion_exchange
        from .the_odds_api_live import _select_events, _odds_request
        with _CAPABILITY_LOCK:
            state = _issuer_state(self)
            request = state.waiting_request
            if state.closed or request is None:
                raise ValueError("EXECUTION_PERMIT_REQUIRED")
            validate_staged_ingestion_exchange(state.claim, exchange)
            config = state.plan.config
            if (exchange.request_id != request.request_id or exchange.kind != request.kind
                    or exchange.endpoint != request.endpoint or exchange.query != request.query
                    or exchange.declared_budget != config.maximum_provider_credits
                    or exchange.declared_max_credit_cost != request.declared_max_credit_cost):
                state.closed = True
                raise ValueError("EXECUTION_PERMIT_REQUIRED")
            state.current_permit = None
            state.waiting_request = None
            # Errors and missing/conflicting accounting never authorize another call.
            last = exchange.actual_reported_last_cost
            used, remaining = exchange.actual_reported_used, exchange.actual_reported_remaining
            if (exchange.status != "OK" or exchange.raw_body_redacted or last is None
                    or last > request.declared_max_credit_cost
                    or (used is not None and state.previous_used is not None and used < state.previous_used)
                    or (remaining is not None and state.previous_remaining is not None
                        and remaining > state.previous_remaining)):
                state.closed = True
                return
            state.actual_cost += last
            if used is not None:
                state.previous_used = used
            if remaining is not None:
                state.previous_remaining = remaining
            if request.kind == "discovery":
                try:
                    events = _select_events(json.loads(exchange.canonical_json), config, exchange.responded_at)
                    state.pending = [_odds_request(config, event.event_id) for event in events]
                except (ValueError, TypeError):
                    state.closed = True
                    raise ValueError("EXECUTION_PERMIT_REQUIRED") from None
            if (state.attempts + len(state.pending) > config.maximum_http_requests
                    or state.actual_cost + sum(r.declared_max_credit_cost for r in state.pending)
                    > config.maximum_provider_credits):
                state.closed = True
                raise ValueError("EXECUTION_PERMIT_REQUIRED")

    def close(self) -> None:
        with _CAPABILITY_LOCK:
            entry = _ISSUERS.get(id(self))
            if entry is not None and entry[0] is self:
                state = entry[1]
                state.closed = True
                if state.current_permit is not None:
                    _PERMITS.pop(id(state.current_permit), None)
                _ISSUERS.pop(id(self), None)


class _RunPermitState:
    def __init__(self, plan: MLBOddsRequestPlan, claim: MLBOddsRunClaim) -> None:
        # Keep authority independent of caller-owned frozen dataclass objects.
        # object.__setattr__ can bypass their public frozen API.
        self.plan = replace(plan, config=replace(plan.config),
                            eligible_events=tuple(replace(event) for event in plan.eligible_events))
        self.claim = replace(claim)
        self.pending = list(self.plan.requests)
        self.current_permit = None
        self.permit_metadata = None
        self.waiting_request = None
        self.attempts = 0
        self.actual_cost = 0
        self.previous_used = self.previous_remaining = None
        self.closed = False


_CAPABILITY_LOCK = RLock()
_ISSUERS: dict[int, tuple[ExecutionPermitIssuer, _RunPermitState]] = {}
_PERMITS: dict[int, tuple[LiveExecutionPermit, _RunPermitState]] = {}


def _issuer_state(issuer: ExecutionPermitIssuer) -> _RunPermitState:
    entry = _ISSUERS.get(id(issuer))
    if entry is None or entry[0] is not issuer or (issuer.run_id, issuer.plan_identity_sha256) != (
            entry[1].claim.run_id, entry[1].claim.plan_identity_sha256):
        raise ValueError("EXECUTION_PERMIT_REQUIRED")
    return entry[1]


def _permit_metadata(permit: LiveExecutionPermit) -> tuple:
    return (permit.run_id, permit.operating_date, permit.claim_sha256,
            permit.plan_identity_sha256, permit.request_id, permit.ordinal)


def create_execution_permit_issuer(plan: MLBOddsRequestPlan, claim: MLBOddsRunClaim) -> ExecutionPermitIssuer:
    """Recheck every run gate; a public constructor cannot mint authority."""
    from ..market_ingestion_evidence import validate_ingestion_claim, reserve_execution_permit_issuer
    from .the_odds_api_live import _validated_plan, request_plan_identity
    plan = _validated_plan(plan)
    validate_ingestion_claim(claim)
    state = _RunPermitState(plan, claim)
    plan, claim = state.plan, state.claim
    if plan.config.network_enabled is not True:
        raise ValueError("EXECUTION_PERMIT_REQUIRED")
    validate_ingestion_claim(claim)
    if (claim.operating_date != plan.config.operating_date
            or json.loads(claim.plan_identity_json) != request_plan_identity(plan)):
        raise ValueError("EXECUTION_PERMIT_REQUIRED")
    with _CAPABILITY_LOCK:
        # A second issuer for the same durable reservation must not multiply budgets.
        if any(state.claim.claim_path == claim.claim_path for _, state in _ISSUERS.values()):
            raise ValueError("EXECUTION_PERMIT_REQUIRED")
        reserve_execution_permit_issuer(claim)
        issuer = ExecutionPermitIssuer(claim.run_id, claim.plan_identity_sha256)
        _ISSUERS[id(issuer)] = (issuer, state)
        return issuer


def _consume_execution_permit(permit: object, request: MLBOddsEventRequest, run_id: str | None,
                              timeout_seconds: float, max_response_bytes: int) -> bool:
    from ..market_ingestion_evidence import validate_ingestion_claim
    with _CAPABILITY_LOCK:
        entry = _PERMITS.get(id(permit))
        if type(permit) is not LiveExecutionPermit or entry is None or entry[0] is not permit:
            return False
        state = entry[1]
        config = state.plan.config
        if (state.closed or state.current_permit is not permit or state.waiting_request is not None
                or _permit_metadata(permit) != state.permit_metadata or run_id != state.claim.run_id
                or not state.pending or request != state.pending[0]
                or timeout_seconds != config.timeout_seconds or max_response_bytes != config.max_response_bytes):
            return False
        try:
            validate_ingestion_claim(state.claim)
        except (ValueError, OSError, TypeError, AttributeError):
            state.closed = True
            return False
        _PERMITS.pop(id(permit))
        state.waiting_request = state.pending.pop(0)
        state.attempts += 1
        return True


class RequestsOddsAPITransport:
    """Explicitly enabled HTTPS only. No redirects, retries or environment auth.

    Streamed decoded bytes and Content-Length are bounded. The caller's default
    is 4 MiB. Only accounting headers and Retry-After reach the executor, which
    strictly sanitizes their values before creating any evidence.
    """

    def __init__(self, *, network_enabled: bool = False) -> None:
        self.network_enabled = network_enabled

    def send(self, request: MLBOddsEventRequest, *, api_key: str,
             timeout_seconds: float, max_response_bytes: int,
             permit: LiveExecutionPermit | None = None, run_id: str | None = None) -> OddsAPIHTTPResponse:
        if self.network_enabled is not True:
            return OddsAPIHTTPResponse(None, (), b"", "NETWORK_DISABLED")
        if not _consume_execution_permit(permit, request, run_id, timeout_seconds, max_response_bytes):
            return OddsAPIHTTPResponse(None, (), b"", "EXECUTION_PERMIT_REQUIRED")
        from .the_odds_api_live import _validate_request, _positive_timeout, _integer
        try:
            _validate_request(request)
            _positive_timeout(timeout_seconds)
            _integer(max_response_bytes, minimum=1, maximum=16 * 1024 * 1024)
            if not isinstance(api_key, str) or not api_key.strip():
                raise ValueError("MISSING_API_KEY")
        except (ValueError, TypeError, AttributeError):
            return OddsAPIHTTPResponse(None, (), b"", "INVALID_REQUEST")
        import requests
        import http.client
        import logging
        import urllib3.connection

        # urllib3 DEBUG and http.client wire debugging can print query keys.
        # Refuse before preparing an authenticated request; change no global
        # logging policy. Callers must keep runtime logging configuration stable.
        if any(logging.getLogger(name).isEnabledFor(logging.DEBUG) for name in (
            "urllib3.connectionpool", "urllib3.util.retry",
        )) or any(connection.debuglevel for connection in (
            http.client.HTTPConnection, http.client.HTTPSConnection,
            urllib3.connection.HTTPConnection, urllib3.connection.HTTPSConnection,
        )):
            return OddsAPIHTTPResponse(None, (), b"", "UNSAFE_HTTP_LOGGING")

        status = None
        safe_headers = ()
        try:
            with requests.Session() as session:
                session.trust_env = False
                session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
                params = dict(request.query)
                params["apiKey"] = api_key
                with session.get(
                    "https://api.the-odds-api.com" + request.endpoint,
                    params=params, timeout=timeout_seconds, stream=True,
                    allow_redirects=False,
                ) as response:
                    status = response.status_code
                    safe_headers = tuple(
                        (str(k).lower(), str(v)) for k, v in response.headers.items()
                        if str(k).lower() in {
                            "x-requests-used", "x-requests-remaining", "x-requests-last",
                            "retry-after",
                        }
                    )
                    length = response.headers.get("Content-Length")
                    if length is not None and (not length.isascii() or not length.isdigit()):
                        return OddsAPIHTTPResponse(status, safe_headers, b"", "INVALID_RESPONSE_SHAPE")
                    if length is not None and int(length) > max_response_bytes:
                        return OddsAPIHTTPResponse(status, safe_headers, b"", "RESPONSE_TOO_LARGE")
                    chunks = []
                    size = 0
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        size += len(chunk)
                        if size > max_response_bytes:
                            return OddsAPIHTTPResponse(status, safe_headers, b"", "RESPONSE_TOO_LARGE")
                        chunks.append(chunk)
                    return OddsAPIHTTPResponse(status, safe_headers, b"".join(chunks))
        except requests.Timeout:
            return OddsAPIHTTPResponse(status, safe_headers, b"", "TIMEOUT")
        except Exception:
            # Client exception text can contain the full authenticated URL.
            return OddsAPIHTTPResponse(status, safe_headers, b"", "NETWORK_ERROR")
