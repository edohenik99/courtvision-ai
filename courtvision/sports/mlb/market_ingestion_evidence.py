"""Immutable research evidence for explicit MLB Odds API observations.

The caller supplies already-sanitized HTTP exchanges. This module performs no
HTTP, credential lookup, market normalization, candidate creation or settlement.
The explicit root must be outside legacy outputs, recovery and sealed HR trees.
Staging directories are retained on failure; only an atomic no-replace rename
publishes a completed run. A COMPLETE marker inside staging is not publication.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from uuid import uuid4
from urllib.parse import unquote


SCHEMA_VERSION = "mlb-market-ingestion-evidence-v2"
_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_STATUS = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_USAGE_HEADERS = frozenset(("x-requests-last", "x-requests-used", "x-requests-remaining", "retry-after"))
_QUERY_FIELDS = frozenset(("dateFormat", "regions", "markets", "oddsFormat"))
_SECRET_KEYS = frozenset(("apikey", "authorization", "cookie", "cookies", "setcookie", "token", "accesstoken", "secret", "password", "session", "sessionid", "sessionkey", "secretkey"))
_RESERVED_COMPONENTS = frozenset(("con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))))


class MLBOddsEvidenceError(ValueError):
    """Fixed diagnostic code; never includes provider or credential text."""


@dataclass(frozen=True, slots=True)
class MLBOddsHTTPExchange:
    request_id: str
    kind: str
    endpoint: str
    query: tuple[tuple[str, str], ...]
    requested_at: datetime
    responded_at: datetime
    http_status: int | None
    usage_headers: tuple[tuple[str, str], ...]
    canonical_json: str | None
    canonical_json_sha256: str | None
    status: str
    declared_budget: int
    declared_cost_before_request: int
    declared_max_credit_cost: int
    actual_reported_last_cost: int | None
    actual_reported_used: int | None
    actual_reported_remaining: int | None
    normalized_record_count: int = 0
    diagnostic_count: int = 0
    raw_body_redacted: bool = False
    received_body: bytes | None = None
    received_body_sha256: str | None = None
    provider: str = field(default="the_odds_api", init=False)

    def __post_init__(self) -> None:
        _validate_exchange(self)


@dataclass(frozen=True, slots=True)
class MLBOddsEvidenceWriteResult:
    status: str
    run_directory: Path
    manifest_sha256: str


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True).encode("utf-8")
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise MLBOddsEvidenceError("INVALID_JSON_EVIDENCE") from None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _component(value: object) -> None:
    if not isinstance(value, str) or not _SAFE_COMPONENT.fullmatch(value) or value.casefold() in _RESERVED_COMPONENTS:
        raise MLBOddsEvidenceError("INVALID_PATH_COMPONENT")


def _nonnegative(value: object) -> None:
    if type(value) is not int or value < 0:
        raise MLBOddsEvidenceError("INVALID_EVIDENCE_COUNT")


def is_sensitive_evidence_key(key: str) -> bool:
    """Shared credential-field predicate for collection and persistence."""
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    return normalized in _SECRET_KEYS or any(normalized.endswith(name) for name in _SECRET_KEYS)


def is_sensitive_evidence_text(value: str) -> bool:
    """Detect credential assignments and URL userinfo, including escaped keys."""
    decoded = value
    for _ in range(3):
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    if re.search(r"(?i)[a-z][a-z0-9+.-]*://[^/\s?#]*@", decoded):
        return True
    return any(is_sensitive_evidence_key(match.group(1)) for match in re.finditer(r"([A-Za-z_][A-Za-z0-9_.-]*)[\"']?\s*[=:]", decoded))


def _safe_payload(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise MLBOddsEvidenceError("INVALID_JSON_EVIDENCE")
            if is_sensitive_evidence_key(key) or is_sensitive_evidence_text(key):
                raise MLBOddsEvidenceError("UNSANITIZED_EVIDENCE")
            _safe_payload(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _safe_payload(item)
    elif isinstance(value, str) and is_sensitive_evidence_text(value):
        raise MLBOddsEvidenceError("UNSANITIZED_EVIDENCE")


def _validate_pairs(value: object, allowed: frozenset[str]) -> None:
    if not isinstance(value, tuple):
        raise MLBOddsEvidenceError("MUTABLE_EXCHANGE_FIELDS")
    seen = set()
    for pair in value:
        if not isinstance(pair, tuple) or len(pair) != 2 or not all(isinstance(part, str) for part in pair):
            raise MLBOddsEvidenceError("INVALID_EXCHANGE_FIELDS")
        key, item = pair
        if key not in allowed or key in seen or not item or len(item) > 4096:
            raise MLBOddsEvidenceError("UNSANITIZED_EXCHANGE_FIELDS")
        seen.add(key)
        if allowed == _USAGE_HEADERS and item != "[INVALID]" and re.fullmatch(r"0|[1-9][0-9]*", item) is None:
            raise MLBOddsEvidenceError("UNSANITIZED_EXCHANGE_FIELDS")
    _safe_payload(dict(value))


def _parse_json_evidence(value: str | bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        parsed = {}
        for key, item in items:
            if key in parsed:
                raise MLBOddsEvidenceError("AMBIGUOUS_JSON_EVIDENCE")
            parsed[key] = item
        return parsed

    return json.loads(value, object_pairs_hook=pairs)


def _validate_exchange(exchange: MLBOddsHTTPExchange) -> None:
    _component(exchange.request_id)
    if type(exchange.raw_body_redacted) is not bool:
        raise MLBOddsEvidenceError("INVALID_REDACTION_FLAG")
    if exchange.kind not in ("discovery", "event_odds") or exchange.provider != "the_odds_api":
        raise MLBOddsEvidenceError("INVALID_EXCHANGE_IDENTITY")
    endpoint = r"/v4/sports/baseball_mlb/events" if exchange.kind == "discovery" else r"/v4/sports/baseball_mlb/events/[A-Za-z0-9_-]+/odds"
    if not isinstance(exchange.endpoint, str) or re.fullmatch(endpoint, exchange.endpoint) is None:
        raise MLBOddsEvidenceError("INVALID_EXCHANGE_ENDPOINT")
    _validate_pairs(exchange.query, _QUERY_FIELDS)
    _validate_pairs(exchange.usage_headers, _USAGE_HEADERS)
    for timestamp in (exchange.requested_at, exchange.responded_at):
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise MLBOddsEvidenceError("INVALID_EXCHANGE_TIMESTAMP")
    if exchange.responded_at < exchange.requested_at:
        raise MLBOddsEvidenceError("INVALID_EXCHANGE_TIMESTAMP")
    if exchange.http_status is not None and (type(exchange.http_status) is not int or not 100 <= exchange.http_status <= 599):
        raise MLBOddsEvidenceError("INVALID_HTTP_STATUS")
    if not isinstance(exchange.status, str) or not _STATUS.fullmatch(exchange.status):
        raise MLBOddsEvidenceError("INVALID_EXCHANGE_STATUS")
    for value in (exchange.declared_budget, exchange.declared_cost_before_request, exchange.declared_max_credit_cost, exchange.normalized_record_count, exchange.diagnostic_count):
        _nonnegative(value)
    for value in (exchange.actual_reported_last_cost, exchange.actual_reported_used, exchange.actual_reported_remaining):
        if value is not None:
            _nonnegative(value)
    if exchange.canonical_json is None:
        if exchange.canonical_json_sha256 is not None or exchange.received_body is not None or exchange.received_body_sha256 is not None:
            raise MLBOddsEvidenceError("CANONICAL_HASH_MISMATCH")
        return
    try:
        parsed = _parse_json_evidence(exchange.canonical_json)
        _safe_payload(parsed)
        canonical = _canonical(parsed)
    except (ValueError, TypeError, RecursionError):
        raise MLBOddsEvidenceError("INVALID_CANONICAL_EVIDENCE") from None
    if exchange.canonical_json.encode("utf-8") != canonical or _sha(canonical) != exchange.canonical_json_sha256:
        raise MLBOddsEvidenceError("CANONICAL_HASH_MISMATCH")
    if exchange.received_body is None:
        if exchange.received_body_sha256 is not None:
            raise MLBOddsEvidenceError("RECEIVED_BODY_HASH_MISMATCH")
        return
    if exchange.raw_body_redacted or type(exchange.received_body) is not bytes:
        raise MLBOddsEvidenceError("UNSAFE_RECEIVED_BODY")
    try:
        received = _parse_json_evidence(exchange.received_body)
        _safe_payload(received)
        received_canonical = _canonical(received)
    except (ValueError, TypeError, RecursionError):
        raise MLBOddsEvidenceError("UNSAFE_RECEIVED_BODY") from None
    if received_canonical != canonical or _sha(exchange.received_body) != exchange.received_body_sha256:
        raise MLBOddsEvidenceError("RECEIVED_BODY_HASH_MISMATCH")


def _assert_plain_path(path: Path) -> None:
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise MLBOddsEvidenceError("UNSAFE_EVIDENCE_PATH") from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise MLBOddsEvidenceError("UNSAFE_EVIDENCE_PATH")


def validate_evidence_root(output_root: str | Path) -> Path:
    """Read-only gate, rejecting links/reparse points and protected HR roots.

    The lane never accepts legacy outputs/test_outputs, recovery, sealed HR,
    prospective HR or mlb_hr trees. A dedicated caller-selected root is required.
    No directories are created here, including during execution preflight.
    """
    if not isinstance(output_root, (str, Path)) or not str(output_root).strip():
        raise MLBOddsEvidenceError("EVIDENCE_ROOT_REQUIRED")
    text = str(output_root)
    # Reject UNC and Windows device namespaces lexically before any stat call.
    if text.replace("/", "\\").startswith("\\\\"):
        raise MLBOddsEvidenceError("UNSAFE_EVIDENCE_PATH")
    root = Path(output_root)
    if not root.is_absolute() or ".." in root.parts:
        raise MLBOddsEvidenceError("EVIDENCE_ROOT_MUST_BE_ABSOLUTE")
    for part in root.parts[1:]:
        if part.endswith((".", " ")) or any(char in part for char in '<>:"|?*~') or part.split(".")[0].casefold() in _RESERVED_COMPONENTS:
            raise MLBOddsEvidenceError("UNSAFE_EVIDENCE_PATH")
    if not _drive_is_local(root):
        raise MLBOddsEvidenceError("UNSAFE_EVIDENCE_PATH")
    for part in root.parts:
        normalized = part.casefold().replace("-", "_")
        if normalized in ("outputs", "test_outputs", ".git", "automation", "sealed", "hr_evidence", "hr_prospective_trial", "theoddsapi", "live_hr_snapshots") or normalized.startswith(("mlb_hr", "mlb_prospective", "sealed_hr")):
            raise MLBOddsEvidenceError("PROTECTED_EVIDENCE_ROOT")
    _assert_plain_path(root)
    if root.exists() and not root.is_dir():
        raise MLBOddsEvidenceError("INVALID_EVIDENCE_ROOT")
    return root


def _drive_is_local(root: Path) -> bool:
    if os.name != "nt":
        return True
    import ctypes

    get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
    get_drive_type.argtypes = (ctypes.c_wchar_p,)
    get_drive_type.restype = ctypes.c_uint
    # Fixed local disks or RAM disks only; never probe mapped network drives.
    return get_drive_type(root.anchor) in (3, 6)


def validate_evidence_destination(
    output_root: str | Path, run_id: str, operating_date: date,
) -> Path:
    """Read-only execution gate: reserve no files and reject any existing run.

    Execution must invoke this after its network flag gate, before transport.
    The writer itself separately supports full-content completed-run replay.
    """
    root = validate_evidence_root(output_root)
    _component(run_id)
    if type(operating_date) is not date:
        raise MLBOddsEvidenceError("INVALID_RUN_EVIDENCE")
    destination = root / "runs" / operating_date.isoformat() / run_id
    _assert_plain_path(destination)
    for parent in (root / "runs", destination.parent):
        if parent.exists() and not parent.is_dir():
            raise MLBOddsEvidenceError("INVALID_EVIDENCE_ROOT")
    if destination.exists():
        raise MLBOddsEvidenceError("EVIDENCE_RUN_ALREADY_EXISTS")
    return destination


def _mkdir(path: Path) -> None:
    _assert_plain_path(path)
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for current in reversed(missing):
        _assert_plain_path(current)
        current.mkdir(exist_ok=True)
        _assert_plain_path(current)
        _fsync_directory(current.parent)
    if not path.is_dir():
        raise MLBOddsEvidenceError("INVALID_EVIDENCE_ROOT")
    _assert_plain_path(path)


def _write_bytes(path: Path, data: bytes) -> None:
    _assert_plain_path(path)
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if path.read_bytes() != data:
        raise MLBOddsEvidenceError("STAGING_INTEGRITY_FAILURE")


def _fsync_directory(path: Path) -> None:
    # Windows does not expose a portable directory fsync; every file is fsynced.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _publish_directory(staging: Path, target: Path) -> None:
    """Atomic directory rename that cannot replace even an empty destination."""
    if os.name == "nt":
        staging.rename(target)
        return
    if sys.platform.startswith("linux"):
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise MLBOddsEvidenceError("ATOMIC_PUBLICATION_UNAVAILABLE")
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(staging), -100, os.fsencode(target), 1):
            error = ctypes.get_errno()
            raise OSError(error, "EVIDENCE_PUBLICATION_FAILED")
        return
    raise MLBOddsEvidenceError("ATOMIC_PUBLICATION_UNAVAILABLE")


def _verify_directory(directory: Path, blobs: Mapping[str, bytes]) -> bool:
    _assert_plain_path(directory)
    if not directory.is_dir():
        return False
    expected_directories = {str(Path(name).parent).replace("\\", "/") for name in blobs if "/" in name}
    found = set()
    for current, directories, files in os.walk(directory, followlinks=False):
        for name in (*directories, *files):
            _assert_plain_path(Path(current) / name)
        for name in directories:
            if (Path(current) / name).relative_to(directory).as_posix() not in expected_directories:
                return False
        for name in files:
            path = Path(current) / name
            relative = path.relative_to(directory).as_posix()
            if relative not in blobs or not path.is_file() or path.stat().st_size != len(blobs[relative]) or path.read_bytes() != blobs[relative]:
                return False
            found.add(relative)
    return found == set(blobs)


@dataclass(frozen=True, slots=True)
class MLBOddsRunClaim:
    output_root: Path
    run_id: str
    operating_date: date
    run_directory: Path
    claim_path: Path
    staging_directory: Path
    plan_identity_json: str
    plan_identity_sha256: str
    claim_sha256: str


def _claim_document(claim: MLBOddsRunClaim) -> bytes:
    plan_identity = json.loads(claim.plan_identity_json)
    return _canonical({
        "schema_version": SCHEMA_VERSION,
        "run_id": claim.run_id,
        "operating_date": claim.operating_date.isoformat(),
        "provider": "the_odds_api",
        "research_only": True,
        "request_plan_identity": plan_identity,
        "request_plan_sha256": claim.plan_identity_sha256,
        "max_http_requests": plan_identity["max_http_requests"],
        "declared_credit_budget": plan_identity["declared_credit_budget"],
        "requested_markets": plan_identity["requested_markets"],
        "staging_directory": claim.staging_directory.name,
    })


def _validate_plan_identity(plan_identity: Mapping[str, object]) -> bytes:
    if not isinstance(plan_identity, Mapping):
        raise MLBOddsEvidenceError("INVALID_CLAIM_IDENTITY")
    if plan_identity.get("provider") != "the_odds_api" or plan_identity.get("research_only") is not True:
        raise MLBOddsEvidenceError("INVALID_CLAIM_IDENTITY")
    for name in ("max_http_requests", "declared_credit_budget"):
        _nonnegative(plan_identity.get(name))
    markets = plan_identity.get("requested_markets")
    if not isinstance(markets, (tuple, list)) or not markets or any(not isinstance(item, str) or not _SAFE_COMPONENT.fullmatch(item) for item in markets):
        raise MLBOddsEvidenceError("INVALID_CLAIM_IDENTITY")
    if not isinstance(plan_identity.get("plan"), Mapping):
        raise MLBOddsEvidenceError("INVALID_CLAIM_IDENTITY")
    _safe_payload(plan_identity)
    return _canonical(dict(plan_identity))


def _conflicting_run_material(run_directory: Path) -> bool:
    if not run_directory.parent.exists():
        return False
    run_name = run_directory.name.casefold()
    for path in run_directory.parent.iterdir():
        name = path.name.casefold()
        if name == run_name or name.startswith(f".{run_name}.") or name.startswith(f"{run_name}."):
            return True
    return False


def acquire_ingestion_claim(
    *, output_root: str | Path, run_id: str, operating_date: date,
    plan_identity: Mapping[str, object],
) -> MLBOddsRunClaim:
    """Exclusively reserve a run and fsync its identity before any paid work.

    No stale reservation or staging is resumed. Even a partly written claim is
    retained and permanently blocks reuse of this run identity.
    """
    run_directory = validate_evidence_destination(output_root, run_id, operating_date)
    identity_bytes = _validate_plan_identity(plan_identity)
    claim = MLBOddsRunClaim(
        validate_evidence_root(output_root), run_id, operating_date, run_directory,
        run_directory.with_name(f".{run_id}.claim.json"),
        run_directory.with_name(f".{run_id}.staging"),
        identity_bytes.decode("utf-8"), _sha(identity_bytes), "",
    )
    document = _claim_document(claim)
    claim = MLBOddsRunClaim(
        claim.output_root, claim.run_id, claim.operating_date, claim.run_directory,
        claim.claim_path, claim.staging_directory, claim.plan_identity_json,
        claim.plan_identity_sha256, _sha(document),
    )
    try:
        if _conflicting_run_material(run_directory):
            raise MLBOddsEvidenceError("EVIDENCE_CLAIM_FAILED")
        _mkdir(run_directory.parent)
        # The exclusive file creation is the cross-process arbitration point.
        _write_bytes(claim.claim_path, document)
        _fsync_directory(run_directory.parent)
        # Recheck other material after arbitration; never reuse stale staging.
        for path in run_directory.parent.iterdir():
            if path != claim.claim_path and (path.name.casefold() == run_id.casefold() or path.name.casefold().startswith((f".{run_id.casefold()}.", f"{run_id.casefold()}."))):
                raise MLBOddsEvidenceError("EVIDENCE_CLAIM_FAILED")
        _assert_plain_path(claim.staging_directory)
        claim.staging_directory.mkdir(exist_ok=False)
        _fsync_directory(claim.staging_directory)
        _fsync_directory(run_directory.parent)
        validate_ingestion_claim(claim)
    except Exception:
        raise MLBOddsEvidenceError("EVIDENCE_CLAIM_FAILED") from None
    return claim


def validate_ingestion_claim(claim: MLBOddsRunClaim) -> None:
    """Recheck the durable reservation and its exact run/plan binding."""
    try:
        if type(claim) is not MLBOddsRunClaim:
            raise ValueError
        root = validate_evidence_root(claim.output_root)
        _component(claim.run_id)
        if type(claim.operating_date) is not date:
            raise ValueError
        target = root / "runs" / claim.operating_date.isoformat() / claim.run_id
        if claim.run_directory != target or claim.claim_path != target.with_name(f".{claim.run_id}.claim.json") or claim.staging_directory != target.with_name(f".{claim.run_id}.staging"):
            raise ValueError
        for path in (target, claim.claim_path, claim.staging_directory):
            _assert_plain_path(path)
        identity_bytes = _validate_plan_identity(json.loads(claim.plan_identity_json))
        document = _claim_document(claim)
        if identity_bytes != claim.plan_identity_json.encode("utf-8") or _sha(identity_bytes) != claim.plan_identity_sha256 or _sha(document) != claim.claim_sha256:
            raise ValueError
        if target.exists() or not claim.staging_directory.is_dir() or (claim.staging_directory / "COMPLETE").exists():
            raise ValueError
        if not claim.claim_path.is_file() or claim.claim_path.read_bytes() != document:
            raise ValueError
    except Exception:
        raise MLBOddsEvidenceError("INVALID_RUN_CLAIM") from None


def reserve_execution_permit_issuer(claim: MLBOddsRunClaim) -> None:
    """Reserve one executor capability issuer for this durable run forever."""
    validate_ingestion_claim(claim)
    marker = claim.run_directory.with_name(f".{claim.run_id}.permit-issuer.json")
    document = _canonical({"run_id": claim.run_id, "operating_date": claim.operating_date.isoformat(), "claim_sha256": claim.claim_sha256, "request_plan_sha256": claim.plan_identity_sha256})
    try:
        _write_bytes(marker, document)
        _fsync_directory(marker.parent)
    except Exception:
        raise MLBOddsEvidenceError("EXECUTION_PERMIT_ISSUER_ALREADY_RESERVED") from None


def _exchange_blobs(exchange: MLBOddsHTTPExchange) -> tuple[dict[str, object], dict[str, bytes]]:
    if not isinstance(exchange, MLBOddsHTTPExchange):
        raise MLBOddsEvidenceError("INVALID_EXCHANGE")
    _validate_exchange(exchange)
    row = asdict(exchange)
    row.pop("canonical_json")
    row.pop("received_body")
    row["requested_at"] = exchange.requested_at.astimezone(timezone.utc).isoformat()
    row["responded_at"] = exchange.responded_at.astimezone(timezone.utc).isoformat()
    row["canonical_json_file"] = None
    row["received_body_file"] = None
    blobs = {}
    if exchange.canonical_json is not None:
        name = f"canonical/{exchange.request_id}.json"
        blobs[name] = exchange.canonical_json.encode("utf-8")
        row["canonical_json_file"] = name
    if exchange.received_body is not None:
        name = f"received/{exchange.request_id}.body"
        blobs[name] = exchange.received_body
        row["received_body_file"] = name
    blobs[f"exchanges/{exchange.request_id}.json"] = _canonical(row)
    return row, blobs


def _verify_exchange_blobs(directory: Path, blobs: Mapping[str, bytes]) -> None:
    for name, data in blobs.items():
        path = directory / name
        _assert_plain_path(path)
        if not path.is_file() or path.stat().st_size != len(data) or path.read_bytes() != data:
            raise MLBOddsEvidenceError("STAGED_EXCHANGE_INTEGRITY_FAILURE")


def validate_staged_ingestion_exchange(claim: MLBOddsRunClaim, exchange: MLBOddsHTTPExchange) -> None:
    validate_ingestion_claim(claim)
    _, blobs = _exchange_blobs(exchange)
    try:
        _verify_exchange_blobs(claim.staging_directory, blobs)
    except Exception:
        raise MLBOddsEvidenceError("STAGED_EXCHANGE_INTEGRITY_FAILURE") from None


def stage_ingestion_exchange(claim: MLBOddsRunClaim, exchange: MLBOddsHTTPExchange) -> None:
    """Durably retain one finished attempt before authorizing another request.

    The exchange receipt is written last; partial bodies and receipts are never
    overwritten or removed, including after a crash or a failed fsync.
    """
    validate_ingestion_claim(claim)
    _, blobs = _exchange_blobs(exchange)
    try:
        for name, data in blobs.items():
            path = claim.staging_directory / name
            _mkdir(path.parent)
            _write_bytes(path, data)
            _fsync_directory(path.parent)
        _fsync_directory(claim.staging_directory)
        _verify_exchange_blobs(claim.staging_directory, blobs)
    except Exception:
        raise MLBOddsEvidenceError("EVIDENCE_STAGE_FAILED") from None


def write_ingestion_evidence(
    *,
    output_root: str | Path,
    run_id: str,
    operating_date: date,
    exchanges: tuple[MLBOddsHTTPExchange, ...],
    normalization_summary: Mapping[str, object],
    run_status: str,
    failure_hook: Callable[[str], None] | None = None,
    claim: MLBOddsRunClaim | None = None,
) -> MLBOddsEvidenceWriteResult:
    """Publish supplied observations once, or verify every byte on exact replay.

    Failure injection stages are before_any_write, during_raw_staging,
    before_completion_marker, and before_atomic_rename. Failed staging is kept.
    COMPLETE certifies persistence, independently of run_status (which may fail).
    """
    root = validate_evidence_root(output_root)
    _component(run_id)
    if type(operating_date) is not date or not isinstance(exchanges, tuple):
        raise MLBOddsEvidenceError("INVALID_RUN_EVIDENCE")
    if not isinstance(run_status, str) or not _STATUS.fullmatch(run_status):
        raise MLBOddsEvidenceError("INVALID_RUN_STATUS")
    if not isinstance(normalization_summary, Mapping):
        raise MLBOddsEvidenceError("INVALID_NORMALIZATION_SUMMARY")
    _safe_payload(normalization_summary)
    blobs = {"normalization.json": _canonical(dict(normalization_summary))}
    rows = []
    identities = set()
    for exchange in exchanges:
        if not isinstance(exchange, MLBOddsHTTPExchange):
            raise MLBOddsEvidenceError("INVALID_EXCHANGE")
        _validate_exchange(exchange)
        if exchange.request_id in identities:
            raise MLBOddsEvidenceError("DUPLICATE_EXCHANGE")
        identities.add(exchange.request_id)
        row, exchange_blobs = _exchange_blobs(exchange)
        blobs.update(exchange_blobs)
        rows.append(row)
    blobs["requests.jsonl"] = b"".join(_canonical(row) + b"\n" for row in rows)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "provider": "the_odds_api",
        "research_only": True,
        "run_id": run_id,
        "operating_date": operating_date.isoformat(),
        "run_status": run_status,
        "request_count": len(rows),
        "requests": [{name: row[name] for name in ("request_id", "kind", "received_body_sha256", "received_body_file", "canonical_json_sha256", "canonical_json_file", "normalized_record_count", "diagnostic_count", "raw_body_redacted")} for row in rows],
        "files": {name: _sha(data) for name, data in sorted(blobs.items())},
    }
    if claim is not None:
        if claim.output_root != root or claim.run_id != run_id or claim.operating_date != operating_date:
            raise MLBOddsEvidenceError("INVALID_RUN_CLAIM")
        manifest["claim_sha256"] = claim.claim_sha256
        manifest["request_plan_sha256"] = claim.plan_identity_sha256
    blobs["manifest.json"] = _canonical(manifest)
    manifest_hash = _sha(blobs["manifest.json"])
    blobs["COMPLETE"] = _canonical({"manifest_sha256": manifest_hash})
    run_directory = root / "runs" / operating_date.isoformat() / run_id
    _assert_plain_path(run_directory)
    if run_directory.exists():
        if not _verify_directory(run_directory, blobs):
            raise MLBOddsEvidenceError("CONFLICT")
        return MLBOddsEvidenceWriteResult("ALREADY_COMPLETE", run_directory, manifest_hash)
    if claim is not None:
        validate_ingestion_claim(claim)
        for exchange in exchanges:
            validate_staged_ingestion_exchange(claim, exchange)
        expected_staging = {name: data for exchange in exchanges for name, data in _exchange_blobs(exchange)[1].items()}
        if not _verify_directory(claim.staging_directory, expected_staging):
            raise MLBOddsEvidenceError("STAGING_INTEGRITY_FAILURE")
    staging = claim.staging_directory if claim is not None else run_directory.with_name(f".{run_id}.staging-{uuid4().hex}")

    def checkpoint(stage: str) -> None:
        if failure_hook is not None:
            failure_hook(stage)

    try:
        checkpoint("before_any_write")
        _mkdir(run_directory.parent)
        _assert_plain_path(staging)
        if claim is None:
            staging.mkdir(exist_ok=False)
        for name, data in blobs.items():
            if name == "COMPLETE":
                continue
            if claim is not None and name.startswith(("canonical/", "received/", "exchanges/")):
                continue
            if name.startswith(("canonical/", "received/")):
                checkpoint("during_raw_staging")
            _mkdir((staging / name).parent)
            _write_bytes(staging / name, data)
        checkpoint("before_completion_marker")
        _write_bytes(staging / "COMPLETE", blobs["COMPLETE"])
        if not _verify_directory(staging, blobs):
            raise MLBOddsEvidenceError("STAGING_INTEGRITY_FAILURE")
        for directory in (staging / "canonical", staging / "received", staging / "exchanges"):
            if directory.exists():
                _fsync_directory(directory)
        _fsync_directory(staging)
        checkpoint("before_atomic_rename")
        _assert_plain_path(run_directory)
        _assert_plain_path(staging)
        _publish_directory(staging, run_directory)
        _fsync_directory(run_directory.parent)
    except Exception:
        # Another writer may have published exactly this run. Inspect all bytes;
        # a marker alone never proves a safe replay, and no staging is deleted.
        if run_directory.exists() and _verify_directory(run_directory, blobs):
            return MLBOddsEvidenceWriteResult("ALREADY_COMPLETE", run_directory, manifest_hash)
        if run_directory.exists():
            raise MLBOddsEvidenceError("CONFLICT") from None
        raise MLBOddsEvidenceError("EVIDENCE_WRITE_FAILED") from None
    return MLBOddsEvidenceWriteResult("COMPLETE", run_directory, manifest_hash)


__all__ = [
    "MLBOddsEvidenceError", "MLBOddsEvidenceWriteResult", "MLBOddsHTTPExchange",
    "validate_evidence_root", "validate_evidence_destination", "write_ingestion_evidence",
    "is_sensitive_evidence_key", "is_sensitive_evidence_text", "reserve_execution_permit_issuer", "MLBOddsRunClaim", "acquire_ingestion_claim",
    "validate_ingestion_claim", "stage_ingestion_exchange", "validate_staged_ingestion_exchange",
]
