"""Create-once NBA response custody. No transport, cache reader, or credentials.

v1 admits synthetic UTF-8 responses only; custody never qualifies a live input.
Retries are idempotent only when the complete capture (including clocks) matches.
Interrupted directories are retained and fail closed, never resumed or replaced.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import stat
from types import MappingProxyType
from typing import Callable

from courtvision.sports.nba.artifact_domains import NBA_PROSPECTIVE_EVIDENCE, require_artifact_path
from courtvision.sports.nba.prospective_io import ArtifactConfinementError, create_once_bytes

EVIDENCE_SCHEMA = "nba-prospective-provider-evidence-v1"
CAPTURE_MODE = "SYNTHETIC_OFFLINE"
_REQUEST_FIELDS = frozenset({"request_id", "provider", "source_role", "endpoint", "parameters",
    "operating_date", "canonical_event_id", "provider_event_id", "repository_commit_sha"})
_CAPTURE_FIELDS = _REQUEST_FIELDS | {"schema_version", "capture_mode", "request_identity_sha256",
    "requested_at_utc", "responded_at_utc", "http_status", "response_metadata",
    "raw_body_byte_length", "raw_body_sha256", "capture_sha256"}
_SECRET_NAMES = frozenset({"apikey", "key", "authorization", "proxyauthorization", "cookie",
    "cookies", "setcookie", "token", "accesstoken", "refreshtoken", "password", "secret",
    "clientsecret", "credentials", "xapikey", "xrapidapikey", "theoddsapikey", "auth",
    "authentication", "signature", "sessionid", "xapisportskey", "apitoken",
    "subscriptionkey", "ocpapimsubscriptionkey", "session", "xsession", "requestsession",
    "privatekey", "signingkey"})
_SECRET_SUFFIXES = ("apikey", "authorization", "password", "secret", "credential",
    "credentials", "token", "cookie", "subscriptionkey", "privatekey", "signingkey")
_PARAMETER_DESCRIPTORS = ("parameters", "parameter", "params", "param", "arguments",
    "argument", "args", "arg")
_SECRET_DESCRIPTORS = (tuple("query_" + item for item in _PARAMETER_DESCRIPTORS)
    + _PARAMETER_DESCRIPTORS + ("values", "value", "headers", "header", "query"))
_FIELD_LABELS = frozenset({"name", "key", "header", "headername", "field", "fieldname",
    "feature", "featurename"})
_FIELD_VALUES = frozenset({"value", "headervalue", "fieldvalue", "featurevalue"})
_SAFE_RESPONSE_METADATA = frozenset({"content-type", "content-length", "x-ratelimit-remaining",
    "x-ratelimit-limit", "x-requests-used", "x-requests-remaining", "x-requests-last"})


class ProspectiveEvidenceError(ValueError):
    """An untrusted or conflicting prospective artifact failed validation."""


def canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ProspectiveEvidenceError("value is not finite canonical JSON") from exc


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def require_hash(value: object, size: int = 64) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{" + str(size) + "}", value) is None:
        raise ProspectiveEvidenceError("invalid provenance hash")
    return value


def require_id(value: object) -> str:
    if (not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value) is None
            or value.casefold().split(".")[0] in {"con", "prn", "aux", "nul",
                *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}):
        raise ProspectiveEvidenceError("invalid artifact identifier")
    return value


def utc_clock(value: object) -> datetime:
    if not isinstance(value, str):
        raise ProspectiveEvidenceError("timestamp must be an explicit UTC string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProspectiveEvidenceError("invalid timestamp") from exc
    if parsed.utcoffset() != timedelta(0):
        raise ProspectiveEvidenceError("timestamp must be UTC-aware")
    return parsed


def require_date(value: object) -> str:
    if not isinstance(value, str):
        raise ProspectiveEvidenceError("operating date must be explicit")
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError as exc:
        raise ProspectiveEvidenceError("invalid operating date") from exc
    return value


def _ordinary_quoted_text(text: str) -> bool:
    """Only JSON whitespace may precede the ordinary quoted-text fallback."""
    return text.lstrip(" \t\r\n").startswith('"')


def _decoded_field_label(key: str) -> str:
    """Inspect quoted JSON labels in their field role without rewriting callers."""
    while _json_inspection_text(key).startswith('"'):
        try:
            encoded = _json_inspection_text(key).encode("utf-8")
        except UnicodeError:
            raise ProspectiveEvidenceError("serialized field label cannot be encoded") from None
        try:
            decoded = _decode_json(encoded)
        except ProspectiveEvidenceError:
            if not _ordinary_quoted_text(key):
                raise
            return key  # Ordinary quoted nicknames retain their existing meaning.
        if not _ordinary_quoted_text(key):
            # A nonstandard declared prefix must be valid before request stripping.
            _decode_json(key.encode("utf-8"))
        if not isinstance(decoded, str):
            return key
        key = decoded
    return key


def _secret_key(key: str) -> bool:
    key = _decoded_field_label(key)
    name = re.sub(r"[^a-z0-9]", "", key.casefold())
    tokens = _semantic_key(key).split("_")
    while name:
        if (name in _SECRET_NAMES or any(name.endswith(x) for x in _SECRET_SUFFIXES)
                or tokens[-1:] == ["session"]):
            return True
        descriptor = next((item for item in _SECRET_DESCRIPTORS
                           if name.endswith(item.replace("_", ""))), None)
        if descriptor is None:
            return False
        name = name[:-len(descriptor.replace("_", ""))]
        descriptor_tokens = descriptor.split("_")
        if tokens[-len(descriptor_tokens):] == descriptor_tokens:
            del tokens[-len(descriptor_tokens):]
    return False


def _semantic_key(key: str) -> str:
    key = _decoded_field_label(key)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def _field_record(value: Mapping) -> tuple[str, str] | None:
    """Recognize a named field without mistaking its structural `key` for a secret."""
    labels, values = [], []
    for key in value:
        if not isinstance(key, str):
            raise ProspectiveEvidenceError("JSON keys must be strings")
        compact = _semantic_key(key).replace("_", "")
        if compact in _FIELD_LABELS:
            labels.append(key)
        if compact in _FIELD_VALUES:
            values.append(key)
    if not labels or not values:
        return None
    if len(labels) != 1 or len(values) != 1 or not isinstance(value[labels[0]], str):
        raise ProspectiveEvidenceError("ambiguous semantic field record")
    return labels[0], values[0]


def _safe_text(value: str) -> None:
    if re.search(r"(?i)\b(?:bearer|basic)\s+\S+", value):
        raise ProspectiveEvidenceError("credential-bearing text is prohibited")
    for match in re.finditer(r"([A-Za-z0-9][A-Za-z0-9_.-]*)[\"']?\s*[=:]\s*(?=\S)", value):
        if _secret_key(match[1]):
            raise ProspectiveEvidenceError("credential-bearing text is prohibited")


def _json_inspection_text(text: str) -> str:
    """Recognize BOM-prefixed JSON without changing retained strings or bytes."""
    if "\0" in text:
        raise ProspectiveEvidenceError("NUL-bearing text is not inspectable UTF-8 JSON/text")
    inspected = text.lstrip()
    while inspected.startswith("\ufeff"):
        inspected = inspected[1:].lstrip()
    return inspected


def _safe_headers(value: object, *, strip_secrets: bool) -> dict:
    """Normalize supported header forms before any request identity is constructed."""
    if isinstance(value, Mapping):
        labels = {key.casefold() for key in value if isinstance(key, str)}
        entries = [value] if "value" in labels and labels & {"name", "key", "header"} else list(value.items())
    elif isinstance(value, (list, tuple)):
        entries = [value] if value and isinstance(value[0], str) else value
    else:
        raise ProspectiveEvidenceError("unsupported header container")
    result = {}
    for entry in entries:
        if isinstance(entry, Mapping):
            if any(not isinstance(key, str) for key in entry):
                raise ProspectiveEvidenceError("malformed header record")
            fields = {key.casefold(): item for key, item in entry.items()}
            names = set(fields) & {"name", "key", "header"}
            if len(fields) != len(entry) or len(names) != 1 or set(fields) != names | {"value"}:
                raise ProspectiveEvidenceError("malformed header record")
            name, item = fields[names.pop()], fields["value"]
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            name, item = entry
        else:
            raise ProspectiveEvidenceError("malformed header pair")
        if not isinstance(name, str) or re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) is None:
            raise ProspectiveEvidenceError("invalid header name")
        if not isinstance(item, str) or any(char in item for char in "\r\n\0"):
            raise ProspectiveEvidenceError("invalid header value")
        if _secret_key(name):
            if strip_secrets:
                continue
            raise ProspectiveEvidenceError("credential field is prohibited")
        _safe_json(item, semantic_fields=True)
        name = name.casefold()
        if name in result:
            raise ProspectiveEvidenceError("ambiguous duplicate header")
        result[name] = item
    return result


def _safe_json(value: object, *, strip_secrets: bool = False, screen_headers: bool = False,
               semantic_fields: bool = False,
               field_policy: Callable[[str, object], None] | None = None) -> object:
    """Validate JSON with one optional mapping/pair/record semantic traversal.

    Requests alone may strip credential mapping/header fields. Model state and
    parsed raw responses reject them; response callers retain the original bytes.
    A caller's field policy never applies to provider bodies unless requested.
    """
    def check_text(text: str) -> None:
        inspected = _json_inspection_text(text)
        if (semantic_fields or screen_headers) and inspected.startswith(("{", "[", '"')):
            # Retain the caller's exact string, but do not let serialized JSON
            # bypass the same policy. Inspection is reject-only: stripping a
            # parsed secret would leave it in the retained original string.
            try:
                encoded = text.encode("utf-8")
            except UnicodeError as exc:
                raise ProspectiveEvidenceError("serialized JSON cannot be encoded") from exc
            try:
                decoded = _decode_json(encoded)
            except ProspectiveEvidenceError:
                if inspected.startswith('"') and _ordinary_quoted_text(text):
                    # An ordinary quoted nickname is not declared container JSON.
                    _safe_text(text)
                    return
                raise
            _safe_json(decoded, screen_headers=screen_headers, semantic_fields=True,
                       field_policy=field_policy)
        else:
            _safe_text(text)

    def check_field(name: str, item: object) -> None:
        label = _decoded_field_label(name)
        if _secret_key(label):
            raise ProspectiveEvidenceError("credential field is prohibited")
        if field_policy is not None:
            field_policy(label, item)
        check_text(name)

    def descend(item: object) -> object:
        return _safe_json(item, strip_secrets=strip_secrets, screen_headers=screen_headers,
                          semantic_fields=semantic_fields, field_policy=field_policy)

    if isinstance(value, Mapping):
        record = _field_record(value) if semantic_fields or screen_headers else None
        if record is not None:
            check_field(value[record[0]], value[record[1]])
        elif semantic_fields or screen_headers:
            for key, item in value.items():
                compact = _semantic_key(key).replace("_", "")
                if compact not in _FIELD_LABELS:
                    continue
                if isinstance(item, str):
                    check_field(item, None)
                elif compact in {"headername", "fieldname", "featurename"}:
                    raise ProspectiveEvidenceError("ambiguous semantic field label")
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProspectiveEvidenceError("JSON keys must be strings")
            if record is None or key not in record:
                if _secret_key(key) and strip_secrets:
                    continue
                check_field(key, item)
            check_text(key)
            if (screen_headers and (record is None or key not in record)
                    and re.sub(r"[^a-z0-9]", "", key.casefold()).endswith(("header", "headers"))):
                result[key] = _safe_headers(item, strip_secrets=strip_secrets)
            else:
                result[key] = descend(item)
        return result
    if isinstance(value, (list, tuple)):
        if (semantic_fields or screen_headers) and value and isinstance(value[0], str):
            if _secret_key(value[0]):
                raise ProspectiveEvidenceError("credential field is prohibited")
            check_field(value[0], value[1] if len(value) > 1 else None)
        return [descend(item) for item in value]
    if isinstance(value, str):
        check_text(value)
    elif value is not None and type(value) not in (bool, int, float):
        raise ProspectiveEvidenceError("unsupported JSON value")
    canonical_bytes(value)
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProspectiveEvidenceError("duplicate JSON key")
        result[key] = value
    return result


def _decode_json(raw: bytes) -> object:
    def reject_constant(_: str) -> None:
        raise ProspectiveEvidenceError("non-finite JSON")
    try:
        return json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=reject_constant)
    except (UnicodeError, ValueError) as exc:
        raise ProspectiveEvidenceError("invalid JSON artifact") from exc


def _check_body(raw: bytes) -> None:
    if not isinstance(raw, bytes):
        raise ProspectiveEvidenceError("raw response must be bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ProspectiveEvidenceError("v1 requires inspectable UTF-8 response bytes") from exc
    inspected = _json_inspection_text(text)
    if inspected.startswith(("{", "[", '"')):
        try:
            decoded = _decode_json(raw)
        except ProspectiveEvidenceError:
            if inspected.startswith('"') and _ordinary_quoted_text(text):
                _safe_text(text)
                return
            raise
        _safe_json(decoded, semantic_fields=True)
    else:
        _safe_text(text)


def immutable(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({k: immutable(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(immutable(v) for v in value)
    return value


def plain_path(path: str | Path) -> Path:
    """Fail closed for links, Windows junctions, nonregular files, and domain aliases."""
    candidate = Path(path).absolute()
    for probe in (candidate, *candidate.parents):
        try:
            info = probe.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ProspectiveEvidenceError("artifact path cannot be inspected") from exc
        if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) &
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise ProspectiveEvidenceError("artifact path contains a link/reparse point")
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ProspectiveEvidenceError("artifact path is not a regular file/directory")
    require_artifact_path(candidate, NBA_PROSPECTIVE_EVIDENCE)
    return candidate


def write_once(path: Path, raw: bytes) -> None:
    path = plain_path(path)
    try:
        create_once_bytes(path, raw)
    except (ArtifactConfinementError, UnicodeError):
        raise ProspectiveEvidenceError("artifact write confinement failed") from None


def read_document(path: Path) -> dict:
    try:
        raw = plain_path(path).read_bytes()
    except OSError as exc:
        raise ProspectiveEvidenceError("required artifact is missing/inaccessible") from exc
    value = _decode_json(raw)
    if not isinstance(value, dict) or canonical_bytes(value) + b"\n" != raw:
        raise ProspectiveEvidenceError("artifact is not a canonical JSON object")
    return value


def normalized_request(*, request_id: str, provider: str, source_role: str, endpoint: str,
                       parameters: Mapping, repository_commit_sha: str,
                       operating_date: str | None = None, canonical_event_id: str | None = None,
                       provider_event_id: str | None = None) -> dict:
    request = dict(request_id=require_id(request_id), provider=require_id(provider),
        source_role=source_role, endpoint=endpoint,
        parameters=_safe_json(parameters, strip_secrets=True, screen_headers=True),
        repository_commit_sha=require_hash(repository_commit_sha, 40), operating_date=operating_date,
        canonical_event_id=canonical_event_id, provider_event_id=provider_event_id)
    _validate_request(request)
    return request


def _validate_request(request: dict) -> None:
    if not isinstance(request, dict) or set(request) != _REQUEST_FIELDS:
        raise ProspectiveEvidenceError("request schema fields differ")
    for key in ("request_id", "provider"):
        require_id(request[key])
    if request["source_role"] not in {"factual", "market"}:
        raise ProspectiveEvidenceError("explicit factual/market source role required")
    endpoint = request["endpoint"]
    if (not isinstance(endpoint, str) or re.fullmatch(r"/[A-Za-z0-9][A-Za-z0-9_./-]*", endpoint) is None
            or ".." in endpoint.split("/") or any(_secret_key(p) for p in endpoint.split("/") if p)):
        raise ProspectiveEvidenceError("endpoint must be a non-secret path without a query")
    if not isinstance(request["parameters"], dict):
        raise ProspectiveEvidenceError("request parameters must be an object")
    require_hash(request["repository_commit_sha"], 40)
    if request["operating_date"] is not None:
        require_date(request["operating_date"])
    for key in ("canonical_event_id", "provider_event_id"):
        if request[key] is not None:
            require_id(request[key])
    _safe_json(request, screen_headers=True)


def _validate_capture(manifest: dict) -> None:
    if (set(manifest) != _CAPTURE_FIELDS or manifest["schema_version"] != EVIDENCE_SCHEMA
            or manifest["capture_mode"] != CAPTURE_MODE):
        raise ProspectiveEvidenceError("unsupported evidence schema/fields/mode")
    _validate_request({k: manifest[k] for k in _REQUEST_FIELDS})
    if digest({k: manifest[k] for k in _REQUEST_FIELDS}) != manifest["request_identity_sha256"]:
        raise ProspectiveEvidenceError("request identity hash mismatch")
    if utc_clock(manifest["responded_at_utc"]) < utc_clock(manifest["requested_at_utc"]):
        raise ProspectiveEvidenceError("response precedes request")
    if type(manifest["http_status"]) is not int or not 100 <= manifest["http_status"] <= 599:
        raise ProspectiveEvidenceError("invalid HTTP status")
    if type(manifest["raw_body_byte_length"]) is not int or manifest["raw_body_byte_length"] < 0:
        raise ProspectiveEvidenceError("invalid raw byte count")
    metadata = manifest["response_metadata"]
    if (not isinstance(metadata, dict) or not set(metadata) <= _SAFE_RESPONSE_METADATA
            or any(not isinstance(v, str) for v in metadata.values())):
        raise ProspectiveEvidenceError("response metadata is not allowlisted")
    _safe_json(manifest, semantic_fields=True)
    require_hash(manifest["raw_body_sha256"])
    if digest({k: v for k, v in manifest.items() if k != "capture_sha256"}) != manifest["capture_sha256"]:
        raise ProspectiveEvidenceError("capture hash mismatch")


@dataclass(frozen=True, slots=True)
class VerifiedCapture:
    manifest: Mapping
    raw_body: bytes


def verify_capture(journal_root: str | Path, request_id: str) -> VerifiedCapture:
    root = plain_path(Path(journal_root) / EVIDENCE_SCHEMA / require_id(request_id))
    manifest = read_document(root / "manifest.json")
    _validate_capture(manifest)
    if manifest["request_id"] != root.name or {p.name for p in root.iterdir()} != {"body.bin", "manifest.json"}:
        raise ProspectiveEvidenceError("capture path/files do not match declared identity")
    try:
        body = plain_path(root / "body.bin").read_bytes()
    except OSError as exc:
        raise ProspectiveEvidenceError("raw response is missing/inaccessible") from exc
    if (len(body) != manifest["raw_body_byte_length"] or
            hashlib.sha256(body).hexdigest() != manifest["raw_body_sha256"]):
        raise ProspectiveEvidenceError("raw body hash/length mismatch")
    _check_body(body)
    return VerifiedCapture(immutable(manifest), body)


def capture_response(journal_root: str | Path, *, request: dict, requested_at_utc: str,
                     responded_at_utc: str, http_status: int, response_metadata: dict,
                     raw_body: bytes) -> VerifiedCapture:
    """Persist already-obtained synthetic bytes; never executes a provider request."""
    _validate_request(request)
    _check_body(raw_body)
    manifest = dict(request, schema_version=EVIDENCE_SCHEMA, capture_mode=CAPTURE_MODE,
        requested_at_utc=requested_at_utc, responded_at_utc=responded_at_utc, http_status=http_status,
        response_metadata=response_metadata, raw_body_byte_length=len(raw_body),
        raw_body_sha256=hashlib.sha256(raw_body).hexdigest(), request_identity_sha256=digest(request))
    manifest["capture_sha256"] = digest(manifest)
    _validate_capture(manifest)
    # Detach nested caller-owned mappings before any filesystem operation.
    manifest = _decode_json(canonical_bytes(manifest))
    root = plain_path(Path(journal_root) / EVIDENCE_SCHEMA / manifest["request_id"])
    root.parent.mkdir(parents=True, exist_ok=True)
    plain_path(root)
    try:
        root.mkdir()
    except FileExistsError:
        saved = verify_capture(journal_root, manifest["request_id"])
        if saved.manifest != immutable(manifest) or saved.raw_body != raw_body:
            raise ProspectiveEvidenceError("conflicting same capture identity")
        return saved
    write_once(root / "body.bin", raw_body)
    write_once(root / "manifest.json", canonical_bytes(manifest) + b"\n")
    return verify_capture(journal_root, manifest["request_id"])


def source_manifest(journal_root: str | Path, request_ids: list[str]) -> dict:
    """Only verified factual captures can become model sources; no cache fallback."""
    if len(request_ids) != len(set(request_ids)):
        raise ProspectiveEvidenceError("duplicate source capture")
    result = {}
    for request_id in sorted(request_ids):
        capture = verify_capture(journal_root, request_id)
        if capture.manifest["source_role"] != "factual":
            raise ProspectiveEvidenceError("market evidence cannot enter model sources")
        # Return exactly the verified metadata, without an unverified second read.
        result[request_id] = _safe_json(capture.manifest, semantic_fields=True)
    return result
