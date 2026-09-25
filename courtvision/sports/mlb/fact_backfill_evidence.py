"""Create-once backfill evidence and bounded, credential-free StatsAPI transport.

Request claims survive interruption and count against the budget even when no
response was received. No redirects, retries, credentials, or environment proxy
configuration are used. This module never publishes factual ledger records.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request

from courtvision.sports.mlb.data.prospective_context_acquisition import (
    EvidenceRequest, ProviderResponse,
)
from courtvision.sports.mlb.fact_ledger import _plain_path, _unique_object
from courtvision.sports.mlb.game_facts import canonical_json

SCHEMA = "cv_mlb_fact_backfill_v1"
BASE = "https://statsapi.mlb.com"


class BackfillError(ValueError):
    """Invalid, damaged, incomplete, or out-of-budget backfill evidence."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def strict_json(raw: bytes) -> dict:
    def reject(value):
        raise BackfillError("non-finite JSON")
    value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=reject)
    if not isinstance(value, dict):
        raise BackfillError("JSON response must be an object")
    return value


def publish_bytes(path: Path, raw: bytes) -> None:
    """Atomic hard-link publication, identical replay only; never replace bytes."""
    _plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".backfill-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _plain_path(path)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise BackfillError("immutable backfill artifact conflict")
    finally:
        temporary.unlink(missing_ok=True)


def publish_document(path: Path, payload: dict) -> str:
    sha = digest(payload)
    publish_bytes(path, canonical_json({"payload": payload, "sha256": sha}) + b"\n")
    return sha


def read_document(path: Path) -> dict:
    _plain_path(path)
    raw = path.read_bytes()
    envelope = strict_json(raw)
    if (set(envelope) != {"payload", "sha256"}
            or not isinstance(envelope["payload"], dict)
            or digest(envelope["payload"]) != envelope["sha256"]
            or canonical_json(envelope) + b"\n" != raw):
        raise BackfillError("damaged backfill manifest/evidence")
    return envelope["payload"]


@contextmanager
def operation_lock(root: Path):
    """A killed writer leaves a fail-closed lock; never guess that it is stale."""
    path = root / ".operation.lock"
    _plain_path(path)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise BackfillError("backfill operation already claimed; inspect interruption") from exc
    try:
        os.close(fd)
        yield
    finally:
        path.unlink()


def validate_url(url: str) -> None:
    schedule = re.fullmatch(
        re.escape(BASE) + r"/api/v1/schedule\?sportId=1&gameTypes=R"
        r"&startDate=\d{4}-\d{2}-\d{2}&endDate=\d{4}-\d{2}-\d{2}", url)
    feed = re.fullmatch(re.escape(BASE) + r"/api/v1\.1/game/[1-9]\d*/feed/live", url)
    if not (schedule or feed):
        raise BackfillError("only the declared first-party StatsAPI paths are allowed")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class StatsAPIProvider:
    """One bounded HTTP attempt per fetch; HTTP error bodies are returned too."""
    def fetch(self, request: EvidenceRequest) -> ProviderResponse:
        validate_url(request.url)
        if request.headers:
            raise BackfillError("backfill does not accept credential/custom headers")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        http = urllib.request.Request(request.url, headers={
            "User-Agent": "CourtVision-Factual-Research/1.0", "Accept": "application/json",
            "Accept-Encoding": "identity",
        })
        try:
            response = opener.open(http, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read(64 * 1024 * 1024 + 1)
            captured = utc_now()
            return ProviderResponse(body=body, status_code=int(response.code), headers={},
                first_observed_at_utc=captured, captured_at_utc=captured)


class EvidenceJournal:
    def __init__(self, root: Path, budget: int):
        self.root, self.budget = root, budget

    def records(self) -> list[dict]:
        """Verify all claims and responses, including failures and unused bytes."""
        _plain_path(self.root)
        claims = sorted(self.root.glob("*/request.json"))
        records = []
        for number, path in enumerate(claims, 1):
            if path.parent.name != f"{number:06d}":
                raise BackfillError("request journal is not contiguous")
            claim = read_document(path)
            validate_url(claim["url"])
            if claim["sequence"] != number or claim["schema_version"] != SCHEMA:
                raise BackfillError("request identity mismatch")
            result_path = path.parent / "response.json"
            result = read_document(result_path) if result_path.exists() else None
            if result is not None:
                if result["request_hash"] != digest(claim):
                    raise BackfillError("response request binding mismatch")
                if result["sha256"] is not None:
                    body_path = path.parent / "body.bin"
                    _plain_path(body_path)
                    body = body_path.read_bytes()
                    if hashlib.sha256(body).hexdigest() != result["sha256"]:
                        raise BackfillError("raw evidence hash mismatch")
                    if len(body) != result["byte_count"]:
                        raise BackfillError("raw evidence length mismatch")
                if datetime.fromisoformat(result["responded_at"]) < datetime.fromisoformat(claim["requested_at"]):
                    raise BackfillError("response predates request")
            records.append({"claim": claim, "response": result})
        if len(records) > self.budget:
            raise BackfillError("provider request budget exceeded")
        return records

    def capture(self, request: EvidenceRequest, provider) -> dict:
        validate_url(request.url)
        if request.headers:
            raise BackfillError("custom headers are forbidden")
        records = self.records()
        if any(r["response"] is None for r in records):
            raise BackfillError("interrupted request has unknown outcome; no automatic retry")
        if len(records) >= self.budget:
            raise BackfillError("provider request budget exhausted")
        number = len(records) + 1
        folder = self.root / f"{number:06d}"
        claim = {"schema_version": SCHEMA, "sequence": number,
            "request_id": request.request_id, "url": request.url,
            "endpoint_path_query": request.url.removeprefix(BASE),
            "gamePk": request.event_id, "requested_at": utc_now().isoformat(),
            "source_schema_version": "mlb_statsapi_v1_schedule_or_v1.1_feed"}
        publish_document(folder / "request.json", claim)
        try:
            response = provider.fetch(request)
        except Exception as exc:
            # Class only: arbitrary exception messages may contain sensitive URLs.
            result = {"request_hash": digest(claim), "responded_at": utc_now().isoformat(),
                "http_status": None, "provider_status": "TRANSPORT_ERROR",
                "error_type": type(exc).__name__, "sha256": None, "byte_count": None}
            publish_document(folder / "response.json", result)
            raise BackfillError("provider transport failed; attempt preserved") from None
        publish_bytes(folder / "body.bin", response.body)
        result = {"request_hash": digest(claim),
            "responded_at": response.captured_at_utc.isoformat(),
            "http_status": response.status_code, "provider_status": "RECEIVED",
            "error_type": None, "sha256": hashlib.sha256(response.body).hexdigest(),
            "byte_count": len(response.body)}
        publish_document(folder / "response.json", result)
        record = {"claim": claim, "response": result}
        self.payload(record)  # Validate only after durable preservation.
        return record

    def payload(self, record: dict) -> dict:
        result = record["response"]
        if result is None or result["http_status"] != 200:
            raise BackfillError("provider response is unavailable or non-200")
        if result["byte_count"] > 64 * 1024 * 1024:
            raise BackfillError("provider response exceeds bounded size")
        path = self.root / f'{record["claim"]["sequence"]:06d}' / "body.bin"
        _plain_path(path)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != result["sha256"]:
            raise BackfillError("raw evidence hash mismatch")
        return strict_json(raw)


def source_ref(record: dict) -> str:
    return (f'mlb-statsapi:{record["claim"]["request_id"]}:'
            f'request-sha256:{digest(record["claim"])}:raw-sha256:{record["response"]["sha256"]}')
