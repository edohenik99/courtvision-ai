"""Controlled, research-only MLB Odds API planning and ingestion.

No CLI, scheduler, environment credentials or cache. A caller explicitly opts
into networking and supplies a key, immutable plan and isolated evidence root.
Discovery reserves worst-case declared cost; no provider billing is inferred.
The default 4 MiB decoded/canonical body ceiling is configurable up to 16 MiB.
Every failure stops the run, retaining all attempted exchanges for publication.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Callable
from zoneinfo import ZoneInfo

from courtvision.sports.mlb.market_data import MLBMarketDataBatch
from courtvision.sports.mlb.market_ingestion_evidence import (
    MLBOddsHTTPExchange, MLBOddsEvidenceWriteResult, validate_evidence_destination,
    is_sensitive_evidence_key, write_ingestion_evidence,
)
from .the_odds_api_market_adapter import PROVIDER_MARKET_MAPPING, normalize_mlb_event_odds
from .the_odds_api_transport import OddsAPIHTTPResponse, OddsAPITransport

INITIAL_RESEARCH_MARKETS = ("batter_hits",)
_USAGE = ("x-requests-last", "x-requests-used", "x-requests-remaining")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def _integer(value: object, *, minimum: int = 0, maximum: int | None = None) -> None:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError("INVALID_INTEGER_LIMIT")


def _positive_timeout(value: object) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 60:
        raise ValueError("INVALID_TIMEOUT")


def _markets(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value or any(
        not isinstance(key, str) or key not in PROVIDER_MARKET_MAPPING for key in value
    ):
        raise ValueError("UNSUPPORTED_MARKET")
    return tuple(sorted(set(value)))


def _regions(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-z]+(?:,[a-z]+)*", value) is None:
        raise ValueError("INVALID_REGIONS")
    return ",".join(sorted(set(value.split(","))))


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("INVALID_TIMESTAMP")
    return value.astimezone(timezone.utc)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True, slots=True)
class MLBOddsIngestionConfig:
    operating_date: date
    markets: tuple[str, ...]
    regions: str
    maximum_events: int
    maximum_http_requests: int
    maximum_provider_credits: int
    event_odds_declared_max_credit_cost: int | None
    discovery_declared_max_credit_cost: int | None = None
    discovery_zero_cost_verified: bool = False
    minimum_pregame_lead_seconds: int = 300
    timeout_seconds: float = 15
    max_response_bytes: int = 4 * 1024 * 1024
    network_enabled: bool = False
    research_only: bool = True
    provider: str = "the_odds_api"
    sport_key: str = "baseball_mlb"
    odds_format: str = "american"

    def __post_init__(self) -> None:
        if type(self.operating_date) is not date:
            raise ValueError("INVALID_OPERATING_DATE")
        if (self.provider, self.sport_key, self.odds_format) != ("the_odds_api", "baseball_mlb", "american"):
            raise ValueError("INVALID_PROVIDER_SCOPE")
        if type(self.network_enabled) is not bool or self.research_only is not True:
            raise ValueError("INVALID_RESEARCH_GATE")
        if type(self.discovery_zero_cost_verified) is not bool:
            raise ValueError("INVALID_DISCOVERY_POLICY")
        object.__setattr__(self, "markets", _markets(self.markets))
        object.__setattr__(self, "regions", _regions(self.regions))
        for limit in (self.maximum_events, self.maximum_http_requests, self.minimum_pregame_lead_seconds):
            _integer(limit, minimum=1)
        _integer(self.maximum_provider_credits)
        if self.event_odds_declared_max_credit_cost is not None:
            _integer(self.event_odds_declared_max_credit_cost, minimum=1)
        if self.discovery_declared_max_credit_cost is not None:
            _integer(self.discovery_declared_max_credit_cost)
            if self.discovery_declared_max_credit_cost == 0 and not self.discovery_zero_cost_verified:
                raise ValueError("DISCOVERY_ZERO_COST_NOT_VERIFIED")
        _positive_timeout(self.timeout_seconds)
        _integer(self.max_response_bytes, minimum=1, maximum=16 * 1024 * 1024)


@dataclass(frozen=True, slots=True)
class MLBOddsEvent:
    event_id: str
    commence_time: datetime
    home_team: str
    away_team: str


@dataclass(frozen=True, slots=True)
class MLBOddsEventRequest:
    kind: str
    event_id: str | None
    markets: tuple[str, ...]
    regions: str
    odds_format: str
    declared_max_credit_cost: int | None

    @property
    def endpoint(self) -> str:
        base = "/v4/sports/baseball_mlb/events"
        return base if self.kind == "discovery" else f"{base}/{self.event_id}/odds"

    @property
    def query(self) -> tuple[tuple[str, str], ...]:
        if self.kind == "discovery":
            return (("dateFormat", "iso"),)
        return tuple(sorted({"dateFormat": "iso", "markets": ",".join(_markets(self.markets)),
                             "regions": _regions(self.regions), "oddsFormat": self.odds_format}.items()))

    @property
    def request_id(self) -> str:
        identity = ("the_odds_api", "baseball_mlb", self.endpoint, self.query)
        return hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()


def _validate_request(request: MLBOddsEventRequest) -> None:
    if type(request) is not MLBOddsEventRequest or request.kind not in ("discovery", "event_odds"):
        raise ValueError("INVALID_REQUEST")
    if request.odds_format != "american" or _regions(request.regions) != request.regions:
        raise ValueError("INVALID_REQUEST")
    _integer(request.declared_max_credit_cost, minimum=0 if request.kind == "discovery" else 1)
    if request.kind == "discovery":
        if request.event_id is not None or request.markets != ():
            raise ValueError("INVALID_DISCOVERY_REQUEST")
    elif not isinstance(request.event_id, str) or not _SAFE_ID.fullmatch(request.event_id):
        raise ValueError("INVALID_EVENT_ID")
    else:
        _markets(request.markets)


@dataclass(frozen=True, slots=True)
class MLBOddsRequestPlan:
    config: MLBOddsIngestionConfig
    requests: tuple[MLBOddsEventRequest, ...]
    eligible_events: tuple[MLBOddsEvent, ...] = ()
    discovery_pending: bool = False
    skipped_duplicate_request_count: int = 0


def _event(value: object) -> MLBOddsEvent:
    if not isinstance(value, dict) or value.get("sport_key") != "baseball_mlb":
        raise ValueError("INVALID_RESPONSE_SHAPE")
    event_id = value.get("id")
    if not isinstance(event_id, str) or not _SAFE_ID.fullmatch(event_id):
        raise ValueError("INVALID_RESPONSE_SHAPE")
    start = value.get("commence_time")
    if not isinstance(start, str):
        raise ValueError("INVALID_RESPONSE_SHAPE")
    try:
        start = _aware(datetime.fromisoformat(start.replace("Z", "+00:00")))
    except (ValueError, OverflowError):
        raise ValueError("INVALID_RESPONSE_SHAPE") from None
    home, away = value.get("home_team"), value.get("away_team")
    if any(not isinstance(team, str) or not team.strip() or team != team.strip() for team in (home, away)):
        raise ValueError("INVALID_RESPONSE_SHAPE")
    if home.casefold() == away.casefold():
        raise ValueError("INVALID_RESPONSE_SHAPE")
    return MLBOddsEvent(event_id, start, home, away)


def _select_events(events: object, config: MLBOddsIngestionConfig, captured_at: datetime) -> tuple[MLBOddsEvent, ...]:
    if not isinstance(events, (list, tuple)):
        raise ValueError("INVALID_RESPONSE_SHAPE")
    capture = _aware(captured_at)
    toronto = ZoneInfo("America/Toronto")
    seen = {}
    for row in events:
        item = _event(row)
        previous = seen.get(item.event_id)
        if previous is not None and previous != item:
            raise ValueError("CONFLICTING_DUPLICATE")
        seen[item.event_id] = item
    eligible = tuple(sorted((item for item in seen.values()
        if item.commence_time.astimezone(toronto).date() == config.operating_date
        and (item.commence_time - capture).total_seconds() >= config.minimum_pregame_lead_seconds),
        key=lambda item: (item.commence_time, item.event_id)))
    if len(eligible) > config.maximum_events:
        raise ValueError("SLATE_EXCEEDS_LIMIT")
    return eligible


def _odds_request(config: MLBOddsIngestionConfig, event_id: str) -> MLBOddsEventRequest:
    return MLBOddsEventRequest("event_odds", event_id, config.markets, config.regions,
                              config.odds_format, config.event_odds_declared_max_credit_cost)


def _validated_plan(plan: MLBOddsRequestPlan) -> MLBOddsRequestPlan:
    if type(plan) is not MLBOddsRequestPlan or type(plan.config) is not MLBOddsIngestionConfig:
        raise ValueError("INVALID_PLAN")
    config = replace(plan.config)  # Revalidate even a forged/frozen instance.
    if config != plan.config or type(plan.discovery_pending) is not bool:
        raise ValueError("INVALID_PLAN")
    if not isinstance(plan.requests, tuple) or not isinstance(plan.eligible_events, tuple):
        raise ValueError("INVALID_PLAN")
    _integer(plan.skipped_duplicate_request_count)
    _integer(config.event_odds_declared_max_credit_cost, minimum=1)
    unique = {}
    skipped = plan.skipped_duplicate_request_count
    for request in plan.requests:
        _validate_request(request)
        if request.regions != config.regions or request.odds_format != config.odds_format:
            raise ValueError("INVALID_PLAN")
        if request.kind == "event_odds" and _markets(request.markets) != config.markets:
            raise ValueError("INVALID_PLAN")
        canonical = replace(request, markets=_markets(request.markets) if request.kind == "event_odds" else ())
        previous = unique.get(request.request_id)
        if previous is not None:
            if previous != canonical:
                raise ValueError("CONFLICTING_DUPLICATE")
            skipped += 1
        else:
            unique[request.request_id] = canonical
    requests = tuple(unique.values())
    events = {}
    for item in plan.eligible_events:
        if type(item) is not MLBOddsEvent:
            raise ValueError("INVALID_PLAN")
        validated = _event({"id": item.event_id, "commence_time": _aware(item.commence_time).isoformat(),
                            "sport_key": "baseball_mlb", "home_team": item.home_team, "away_team": item.away_team})
        if validated != item or item.event_id in events:
            raise ValueError("INVALID_PLAN")
        if item.commence_time.astimezone(ZoneInfo("America/Toronto")).date() != config.operating_date:
            raise ValueError("INVALID_PLAN")
        events[item.event_id] = item
    if len(events) > config.maximum_events:
        raise ValueError("SLATE_EXCEEDS_LIMIT")
    if plan.discovery_pending:
        _integer(config.discovery_declared_max_credit_cost)
        if len(requests) != 1 or requests[0].kind != "discovery" or events:
            raise ValueError("INVALID_PLAN")
        if requests[0].declared_max_credit_cost != config.discovery_declared_max_credit_cost:
            raise ValueError("INVALID_PLAN")
        count = 1 + config.maximum_events
        cost = config.discovery_declared_max_credit_cost + config.maximum_events * config.event_odds_declared_max_credit_cost
    else:
        if any(r.kind != "event_odds" for r in requests) or {r.event_id for r in requests} != set(events):
            raise ValueError("INVALID_PLAN")
        count = len(requests)
        cost = sum(r.declared_max_credit_cost for r in requests)
    if count > config.maximum_http_requests or cost > config.maximum_provider_credits:
        raise ValueError("BUDGET_EXCEEDED")
    return replace(plan, requests=requests, skipped_duplicate_request_count=skipped)


def build_request_plan(config: MLBOddsIngestionConfig, *, events: list | None = None,
                       captured_at: datetime) -> MLBOddsRequestPlan:
    """Pure plan: no credential lookup, provider calls, cache or evidence writes."""
    _aware(captured_at)
    config = replace(config)
    if events is None:
        requests = (MLBOddsEventRequest("discovery", None, (), config.regions,
                                       config.odds_format, config.discovery_declared_max_credit_cost),)
        return _validated_plan(MLBOddsRequestPlan(config, requests, discovery_pending=True))
    selected = _select_events(events, config, captured_at)
    return _validated_plan(MLBOddsRequestPlan(config, tuple(_odds_request(config, e.event_id) for e in selected), selected))


@dataclass(frozen=True, slots=True)
class MLBOddsIngestionResult:
    run_id: str
    operating_date: date | None
    status: str
    planned_request_count: int = 0
    executed_request_count: int = 0
    skipped_duplicate_request_count: int = 0
    declared_credit_budget: int = 0
    declared_max_cost: int = 0
    reported_credit_cost: int | None = None
    provider_remaining: int | None = None
    eligible_events: tuple[MLBOddsEvent, ...] = ()
    market_batches: tuple[MLBMarketDataBatch, ...] = ()
    transport_failures: tuple[str, ...] = ()
    exchanges: tuple[MLBOddsHTTPExchange, ...] = ()
    evidence_result: MLBOddsEvidenceWriteResult | None = None
    request_plan: MLBOddsRequestPlan | None = None

    @property
    def normalization_diagnostics(self) -> tuple:
        return tuple(d for batch in self.market_batches for d in batch.diagnostics)


def dry_run(plan: MLBOddsRequestPlan) -> MLBOddsIngestionResult:
    plan = _validated_plan(plan)
    config = plan.config
    count = 1 + config.maximum_events if plan.discovery_pending else len(plan.requests)
    cost = (config.discovery_declared_max_credit_cost + config.maximum_events * config.event_odds_declared_max_credit_cost
            if plan.discovery_pending else sum(r.declared_max_credit_cost for r in plan.requests))
    return MLBOddsIngestionResult("dry-run", config.operating_date, "DRY_RUN", count,
        skipped_duplicate_request_count=plan.skipped_duplicate_request_count,
        declared_credit_budget=config.maximum_provider_credits, declared_max_cost=cost,
        eligible_events=plan.eligible_events, request_plan=plan)


def _safe_json(body: bytes, api_key: str, limit: int) -> tuple[object, str, bool]:
    if len(body) > limit:
        raise ValueError("RESPONSE_TOO_LARGE")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("INVALID_JSON")
            result[key] = value
        return result
    def invalid_constant(_value):
        raise ValueError("INVALID_JSON")
    try:
        value = json.loads(body, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("INVALID_JSON") from None
    redacted = False
    def clean(item):
        nonlocal redacted
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                if is_sensitive_evidence_key(key) or api_key in key:
                    redacted = True
                    continue
                result[key] = clean(child)
            return result
        if isinstance(item, list):
            return [clean(child) for child in item]
        if isinstance(item, str) and (api_key in item or re.search(r"(?i)(apikey|authorization|cookie|access_token)\s*[=:]", item)):
            redacted = True
            return "[REDACTED]"
        return item
    try:
        value = clean(value)
        raw = _canonical(value)
    except (ValueError, OverflowError, RecursionError):
        raise ValueError("INVALID_JSON") from None
    if len(raw.encode("utf-8")) > limit:
        raise ValueError("RESPONSE_TOO_LARGE")
    return value, raw, redacted


def _headers(headers: object, api_key: str) -> tuple[tuple, tuple, bool]:
    values = {}
    invalid = False
    if not isinstance(headers, tuple):
        return (), (None, None, None), True
    for pair in headers:
        if not isinstance(pair, tuple) or len(pair) != 2 or not isinstance(pair[0], str):
            invalid = True
            continue
        name, value = pair[0].lower(), pair[1]
        if name not in (*_USAGE, "retry-after"):
            continue
        if (name in values or not isinstance(value, str) or len(value) > 20
                or api_key in value or re.fullmatch(r"0|[1-9][0-9]*", value) is None):
            invalid = invalid or name in _USAGE
            values[name] = "[INVALID]"
        else:
            values[name] = value
    parsed = tuple(int(values[name]) if name in values and values[name] != "[INVALID]" else None for name in _USAGE)
    return tuple(sorted(values.items())), parsed, invalid


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def execute_ingestion(plan: MLBOddsRequestPlan, *, api_key: str | None = None,
                      transport: OddsAPITransport, output_root: str | Path | None = None,
                      run_id: str, clock: Callable[[], datetime] = _utcnow) -> MLBOddsIngestionResult:
    """One attempt per semantic request; any failure stops subsequent requests.

    Missing last-cost accounting stops the run as ACCOUNTING_UNAVAILABLE. Missing
    account totals remain None; monotonicity is checked whenever supplied. The
    result's reported_credit_cost is None if any attempt lacks reported cost.
    declared_cost_before_request is the reservation for this and later requests.
    """
    # This gate precedes credential access, clock calls and filesystem operations.
    if type(plan) is not MLBOddsRequestPlan or type(plan.config) is not MLBOddsIngestionConfig:
        return MLBOddsIngestionResult("unavailable", None, "INVALID_PLAN")
    if plan.config.network_enabled is not True:
        return MLBOddsIngestionResult("unavailable", None, "NETWORK_DISABLED")
    if not isinstance(api_key, str) or not api_key.strip():
        return MLBOddsIngestionResult("unavailable", None, "MISSING_API_KEY")
    try:
        if not isinstance(run_id, str) or not _SAFE_ID.fullmatch(run_id) or api_key in run_id:
            raise ValueError("INVALID_PLAN")
        # Do not retain credentials accidentally supplied in nominal metadata.
        if api_key in repr(plan) or (output_root is not None and api_key in str(output_root)):
            raise ValueError("INVALID_PLAN")
        plan = _validated_plan(plan)
        preview = dry_run(plan)
    except (ValueError, TypeError, AttributeError, OverflowError):
        return MLBOddsIngestionResult("unavailable", None, "INVALID_PLAN")
    if output_root is None:
        return replace(preview, run_id=run_id, status="EVIDENCE_ROOT_REQUIRED")
    try:
        validate_evidence_destination(output_root, run_id, plan.config.operating_date)
        root = Path(output_root)
    except (ValueError, OSError):
        return replace(preview, run_id=run_id, status="INVALID_EVIDENCE_ROOT")
    config = plan.config
    pending = list(plan.requests)
    eligible = plan.eligible_events
    batches = []
    exchanges = []
    failures = []
    actual_total = 0
    accounting_complete = True
    previous_used = previous_remaining = None
    remaining_reservation = preview.declared_max_cost
    attempted = set()
    run_status = "COMPLETE"
    while pending:
        request = pending.pop(0)
        try:
            requested_at = _aware(clock())
            if exchanges and requested_at < exchanges[-1].responded_at:
                raise ValueError("CLOCK_FAILURE")
        except Exception:
            failures.append("CLOCK_FAILURE")
            run_status = "PARTIAL" if batches else "FAILED"
            break
        if not exchanges and any(
            (item.commence_time - requested_at).total_seconds() < config.minimum_pregame_lead_seconds
            for item in eligible
        ):
            failures.append("PREGAME_LEAD_TIME")
            run_status = "FAILED"
            break
        # Recheck pregame eligibility as time passes, including supplied plans.
        if request.kind == "event_odds":
            event = next(e for e in eligible if e.event_id == request.event_id)
            if (event.commence_time - requested_at).total_seconds() < config.minimum_pregame_lead_seconds:
                failures.append("PREGAME_LEAD_TIME")
                run_status = "PARTIAL" if batches else "FAILED"
                break
        if (request.request_id in attempted or len(exchanges) >= config.maximum_http_requests
                or actual_total + remaining_reservation > config.maximum_provider_credits):
            failures.append("BUDGET_EXCEEDED")
            run_status = "BUDGET_BLOCKED"
            break
        attempted.add(request.request_id)
        try:
            response = transport.send(request, api_key=api_key, timeout_seconds=config.timeout_seconds,
                                      max_response_bytes=config.max_response_bytes)
        except TimeoutError:
            response = OddsAPIHTTPResponse(None, (), b"", "TIMEOUT")
        except Exception:
            response = OddsAPIHTTPResponse(None, (), b"", "NETWORK_ERROR")
        clock_failed = False
        try:
            responded_at = _aware(clock())
            if responded_at < requested_at:
                raise ValueError("CLOCK_FAILURE")
        except Exception:
            # Retain the attempted exchange with an explicit clock failure;
            # this fallback samples the actual observation time, never the error.
            responded_at = max(requested_at, _utcnow())
            clock_failed = True
        if type(response) is not OddsAPIHTTPResponse or type(response.body) is not bytes:
            response = OddsAPIHTTPResponse(None, (), b"", "INVALID_RESPONSE_SHAPE")
        safe_headers, (last, used, remaining), invalid_headers = _headers(response.headers, api_key)
        if last is None:
            accounting_complete = False
        else:
            actual_total += last
        status = response.error if isinstance(response.error, str) and response.error in {
            "TIMEOUT", "NETWORK_ERROR", "NETWORK_DISABLED", "RESPONSE_TOO_LARGE",
            "INVALID_RESPONSE_SHAPE", "INVALID_REQUEST", "UNSAFE_HTTP_LOGGING",
        } else "OK" if response.error is None else "INVALID_RESPONSE_SHAPE"
        http_status = response.status_code
        if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
            http_status = None
            status = "INVALID_RESPONSE_SHAPE"
        if http_status is None and status == "OK":
            status = "INVALID_RESPONSE_SHAPE"
        payload = raw = None
        redacted = False
        if response.body:
            try:
                payload, raw, redacted = _safe_json(response.body, api_key, config.max_response_bytes)
            except ValueError as error:
                if status == "OK":
                    status = str(error)
        elif status == "OK":
            status = "INVALID_JSON"
        if status in {"OK", "INVALID_JSON"} and http_status is not None and not 200 <= http_status < 300:
            status = "HTTP_429" if http_status == 429 else "HTTP_ERROR"
        if redacted:
            status = "SECRET_REDACTED"
        if clock_failed:
            status = "CLOCK_FAILURE"
        accounting_status = None
        if last is not None and (last > request.declared_max_credit_cost or actual_total > config.maximum_provider_credits):
            accounting_status = "BUDGET_EXCEEDED"
        elif invalid_headers or (used is not None and previous_used is not None and used < previous_used) or (
                remaining is not None and previous_remaining is not None and remaining > previous_remaining):
            accounting_status = "ACCOUNTING_CONFLICT"
        elif last is None and status == "OK":
            accounting_status = "ACCOUNTING_UNAVAILABLE"
        if accounting_status is not None:
            status = accounting_status
        if used is not None:
            previous_used = used
        if remaining is not None:
            previous_remaining = remaining
        batch = None
        if status == "OK":
            try:
                if request.kind == "discovery":
                    eligible = _select_events(payload, config, responded_at)
                    pending = [_odds_request(config, e.event_id) for e in eligible]
                    exact_cost = sum(r.declared_max_credit_cost for r in pending)
                    if len(exchanges) + 1 + len(pending) > config.maximum_http_requests or actual_total + exact_cost > config.maximum_provider_credits:
                        raise ValueError("BUDGET_EXCEEDED")
                else:
                    received = _event(payload)
                    expected = next(e for e in eligible if e.event_id == request.event_id)
                    if received != expected or not isinstance(payload.get("bookmakers"), list):
                        raise ValueError("INVALID_RESPONSE_SHAPE")
                    if (received.commence_time - responded_at).total_seconds() < config.minimum_pregame_lead_seconds:
                        raise ValueError("PREGAME_LEAD_TIME")
                    batch = normalize_mlb_event_odds(payload, collected_at=responded_at,
                        source_refs=(f"mlb-odds:{run_id}:{request.request_id}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}",))
                    batches.append(batch)
            except (ValueError, TypeError, OverflowError) as error:
                # Only fixed exception codes generated in this module are safe.
                status = str(error) if str(error) in {"BUDGET_EXCEEDED", "SLATE_EXCEEDS_LIMIT", "CONFLICTING_DUPLICATE", "PREGAME_LEAD_TIME"} else "INVALID_RESPONSE_SHAPE"
        exchange = MLBOddsHTTPExchange(
            request_id=request.request_id, kind=request.kind, endpoint=request.endpoint, query=request.query,
            requested_at=requested_at, responded_at=responded_at, http_status=http_status,
            usage_headers=safe_headers, raw_json=raw,
            response_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw is not None else None,
            status=status, raw_body_redacted=redacted, declared_budget=config.maximum_provider_credits,
            declared_cost_before_request=remaining_reservation, declared_max_credit_cost=request.declared_max_credit_cost,
            actual_reported_last_cost=last, actual_reported_used=used, actual_reported_remaining=remaining,
            normalized_record_count=len(batch.records) if batch else 0, diagnostic_count=len(batch.diagnostics) if batch else 0,
        )
        exchanges.append(exchange)
        remaining_reservation = sum(r.declared_max_credit_cost for r in pending)
        if status != "OK":
            failures.append(status)
            run_status = ("BUDGET_BLOCKED" if status == "BUDGET_EXCEEDED" else status
                          if status in {"ACCOUNTING_CONFLICT", "ACCOUNTING_UNAVAILABLE"}
                          else "PARTIAL" if batches else "FAILED")
            break
    result = replace(preview, run_id=run_id, status=run_status, executed_request_count=len(exchanges),
        reported_credit_cost=actual_total if accounting_complete else None, provider_remaining=previous_remaining,
        eligible_events=eligible, market_batches=tuple(batches), transport_failures=tuple(failures), exchanges=tuple(exchanges))
    summary = {"record_count": sum(len(b.records) for b in batches),
               "diagnostic_count": sum(len(b.diagnostics) for b in batches),
               "request_counts": [{"request_id": e.request_id, "records": e.normalized_record_count,
                                   "diagnostics": e.diagnostic_count} for e in exchanges]}
    try:
        receipt = write_ingestion_evidence(output_root=root, run_id=run_id, operating_date=config.operating_date,
            exchanges=tuple(exchanges), normalization_summary=summary, run_status=run_status)
        return replace(result, evidence_result=receipt)
    except (ValueError, OSError):
        return replace(result, status="EVIDENCE_FAILED", transport_failures=result.transport_failures + ("EVIDENCE_WRITE_FAILED",))
