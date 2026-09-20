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


SCHEMA_VERSION = "mlb-market-ingestion-evidence-v1"
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
    raw_json: str | None
    response_sha256: str | None
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


def _safe_payload(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise MLBOddsEvidenceError("INVALID_JSON_EVIDENCE")
            if is_sensitive_evidence_key(key):
                raise MLBOddsEvidenceError("UNSANITIZED_EVIDENCE")
            _safe_payload(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _safe_payload(item)
    elif isinstance(value, str) and re.search(r"(?i)(?:apikey|authorization|cookie|access_token)\s*[=:]", value):
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
    if exchange.raw_json is None:
        if exchange.response_sha256 is not None:
            raise MLBOddsEvidenceError("RAW_HASH_MISMATCH")
        return
    try:
        raw = json.loads(exchange.raw_json)
        _safe_payload(raw)
        canonical = _canonical(raw)
    except (ValueError, TypeError, RecursionError):
        raise MLBOddsEvidenceError("INVALID_RAW_EVIDENCE") from None
    if exchange.raw_json.encode("utf-8") != canonical or _sha(canonical) != exchange.response_sha256:
        raise MLBOddsEvidenceError("RAW_HASH_MISMATCH")


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
    path.mkdir(parents=True, exist_ok=True)
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
    found = set()
    for current, directories, files in os.walk(directory, followlinks=False):
        for name in (*directories, *files):
            _assert_plain_path(Path(current) / name)
        for name in directories:
            if (Path(current) / name).relative_to(directory).as_posix() != "raw":
                return False
        for name in files:
            path = Path(current) / name
            relative = path.relative_to(directory).as_posix()
            if relative not in blobs or not path.is_file() or path.stat().st_size != len(blobs[relative]) or path.read_bytes() != blobs[relative]:
                return False
            found.add(relative)
    return found == set(blobs)


def write_ingestion_evidence(
    *,
    output_root: str | Path,
    run_id: str,
    operating_date: date,
    exchanges: tuple[MLBOddsHTTPExchange, ...],
    normalization_summary: Mapping[str, object],
    run_status: str,
    failure_hook: Callable[[str], None] | None = None,
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
        row = asdict(exchange)
        row.pop("raw_json")
        row["requested_at"] = exchange.requested_at.astimezone(timezone.utc).isoformat()
        row["responded_at"] = exchange.responded_at.astimezone(timezone.utc).isoformat()
        row["raw_file"] = None
        if exchange.raw_json is not None:
            raw_file = f"raw/{exchange.request_id}.json"
            blobs[raw_file] = exchange.raw_json.encode("utf-8")
            row["raw_file"] = raw_file
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
        "requests": [{name: row[name] for name in ("request_id", "kind", "response_sha256", "normalized_record_count", "diagnostic_count", "raw_body_redacted")} for row in rows],
        "files": {name: _sha(data) for name, data in sorted(blobs.items())},
    }
    blobs["manifest.json"] = _canonical(manifest)
    manifest_hash = _sha(blobs["manifest.json"])
    blobs["COMPLETE"] = _canonical({"manifest_sha256": manifest_hash})
    run_directory = root / "runs" / operating_date.isoformat() / run_id
    _assert_plain_path(run_directory)
    if run_directory.exists():
        if not _verify_directory(run_directory, blobs):
            raise MLBOddsEvidenceError("CONFLICT")
        return MLBOddsEvidenceWriteResult("ALREADY_COMPLETE", run_directory, manifest_hash)
    staging = run_directory.with_name(f".{run_id}.staging-{uuid4().hex}")

    def checkpoint(stage: str) -> None:
        if failure_hook is not None:
            failure_hook(stage)

    try:
        checkpoint("before_any_write")
        _mkdir(run_directory.parent)
        _assert_plain_path(staging)
        staging.mkdir(exist_ok=False)
        _mkdir(staging / "raw")
        for name, data in blobs.items():
            if name == "COMPLETE":
                continue
            if name.startswith("raw/"):
                checkpoint("during_raw_staging")
            _write_bytes(staging / name, data)
        checkpoint("before_completion_marker")
        _write_bytes(staging / "COMPLETE", blobs["COMPLETE"])
        if not _verify_directory(staging, blobs):
            raise MLBOddsEvidenceError("STAGING_INTEGRITY_FAILURE")
        _fsync_directory(staging / "raw")
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
    "is_sensitive_evidence_key",
]
