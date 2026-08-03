"""Single-writer immutable JSONL lifecycle segment storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import stat
from threading import Lock
import time
from typing import Any, Callable, Literal, Mapping, Sequence
from uuid import uuid4

from courtvision.lifecycle.canonical import (
    canonical_json_v1,
    canonical_payload_bytes,
    file_sha256,
    format_utc_datetime,
    payload_sha256,
    sha256_bytes,
)
from courtvision.lifecycle.clock import Clock, SystemClock
from courtvision.lifecycle.evidence import (
    PreparedEvidenceObject,
    commit_prepared_evidence,
    verify_evidence,
)
from courtvision.lifecycle.models import EventEnvelope, ReconciliationReport, RunManifest


SEGMENT_MANIFEST_SCHEMA_VERSION = 1
COMPLETE_MARKER = "COMPLETE"
EVENTS_FILE = "events.jsonl"
RUN_MANIFEST_FILE = "run_manifest.json"
SEGMENT_MANIFEST_FILE = "manifest.json"
WRITER_LOCK_FILE = ".writer.lock"


class LifecycleWriterError(RuntimeError):
    """Base error for lifecycle persistence failures."""


class LifecycleWriterBusyError(LifecycleWriterError):
    """Raised when another verified-live writer owns the lifecycle lock."""


class LifecycleWriterReentrancyError(LifecycleWriterBusyError):
    """Raised when one writer instance recursively starts another write."""


class LifecycleIntegrityError(LifecycleWriterError):
    """Raised when immutable data fails closed integrity checks."""


class IdempotencyConflictError(LifecycleIntegrityError):
    """Raised for same idempotency key with different canonical content."""


@dataclass(frozen=True, slots=True)
class SegmentCommitResult:
    status: str
    segment_directory: Path
    segment_manifest: Mapping[str, Any]
    event_count: int


@dataclass(frozen=True, slots=True)
class SegmentVerificationResult:
    ok: bool
    segment_directory: Path
    violations: tuple[str, ...]
    event_count: int


ProcessChecker = Callable[[int], bool]
FailureHook = Callable[[str], None]


def process_is_alive(pid: int) -> bool:
    """Return whether *pid* is alive without sending a terminating signal."""

    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(
                process_query_limited_information, False, pid
            )
            if handle:
                kernel32.CloseHandle(handle)
                return True
            error = ctypes.get_last_error()
            # Access denied proves a process exists but is not inspectable.
            return error == 5
        except (AttributeError, OSError):
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


class LifecycleWriterLock:
    """Repository-level create-exclusive writer lock with verified stale recovery."""

    def __init__(
        self,
        lifecycle_root: Path,
        *,
        prediction_run_id: str,
        command: str,
        clock: Clock | None = None,
        timeout_seconds: float = 0.25,
        process_checker: ProcessChecker = process_is_alive,
    ) -> None:
        self.root = Path(lifecycle_root)
        self.path = self.root / WRITER_LOCK_FILE
        self.prediction_run_id = str(prediction_run_id)
        self.command = str(command)
        self.clock = clock or SystemClock()
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self.process_checker = process_checker
        self.lock_id = str(uuid4())
        self.acquired_at_utc: str | None = None
        self._fd: int | None = None

    def __enter__(self) -> "LifecycleWriterLock":
        if self.root.exists() and self.root.is_symlink():
            raise LifecycleIntegrityError(
                f"lifecycle root must not be a symlink: {self.root}"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        invisible_lock_permission_retries = 3
        while True:
            try:
                self._fd = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError as exc:
                if self._recover_verified_dead_owner():
                    continue
                if time.monotonic() >= deadline:
                    raise LifecycleWriterBusyError(
                        f"lifecycle writer lock is already held: {self.path}"
                    ) from exc
                time.sleep(0.01)
            except PermissionError as exc:
                permission_state = self._classify_lock_permission_error()
                if permission_state == "filesystem_error":
                    raise LifecycleWriterError(
                        f"unable to acquire lifecycle writer lock: {self.path}"
                    ) from exc

                if permission_state == "lock_not_visible":
                    if invisible_lock_permission_retries <= 0:
                        raise LifecycleWriterError(
                            f"unable to acquire lifecycle writer lock: {self.path}"
                        ) from exc
                    invisible_lock_permission_retries -= 1
                    time.sleep(0.001)
                    continue

                # Windows can report EACCES instead of FileExistsError while
                # another process owns the exact create-exclusive lock path.
                # Treat it like ordinary contention and retry until timeout.
                if time.monotonic() >= deadline:
                    raise LifecycleWriterBusyError(
                        f"lifecycle writer lock is already held: {self.path}"
                    ) from exc

                time.sleep(0.01)
            except OSError as exc:
                raise LifecycleWriterError(
                    f"unable to acquire lifecycle writer lock: {self.path}"
                ) from exc
            else:
                break
        try:
            self.acquired_at_utc = format_utc_datetime(self.clock.now())
            metadata = {
                "lock_id": self.lock_id,
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "root": str(self.root),
                "prediction_run_id": self.prediction_run_id,
                "command": self.command,
                "acquired_at_utc": self.acquired_at_utc,
            }
            data = canonical_payload_bytes(metadata)
            os.write(self._fd, data)
            os.fsync(self._fd)
            return self
        except OSError as exc:
            raise LifecycleWriterError(
                f"unable to acquire lifecycle writer lock: {self.path}"
            ) from exc

    def _classify_lock_permission_error(
        self,
    ) -> Literal["visible_lock", "lock_not_visible", "filesystem_error"]:
        """Classify a Windows create-exclusive PermissionError.

        Windows may return PermissionError/EACCES rather than
        FileExistsError when another process owns the create-exclusive lock.

        A uniquely named sibling probe confirms that the lifecycle directory
        itself remains writable before the exact lock path is inspected.
        """

        if os.name != "nt":
            return "filesystem_error"

        try:
            root_stat = self.root.stat()
        except OSError:
            return "filesystem_error"
        if not stat.S_ISDIR(root_stat.st_mode):
            return "filesystem_error"

        probe_path = self.root / (
            f".writer-lock-probe-{os.getpid()}-{uuid4().hex}"
        )
        probe_fd: int | None = None
        probe_created = False
        probe_succeeded = False
        cleanup_failed = False

        try:
            probe_fd = os.open(
                probe_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            probe_created = True
            os.close(probe_fd)
            probe_fd = None
            probe_path.unlink()
            probe_created = False
            probe_succeeded = True
        except OSError:
            pass
        finally:
            if probe_fd is not None:
                try:
                    os.close(probe_fd)
                except OSError:
                    cleanup_failed = True

            if probe_created:
                try:
                    probe_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    cleanup_failed = True

        if not probe_succeeded or cleanup_failed:
            return "filesystem_error"

        try:
            self.path.stat()
        except FileNotFoundError:
            return "lock_not_visible"
        except PermissionError:
            return "visible_lock"
        except OSError:
            return "filesystem_error"
        return "visible_lock"

    def _recover_verified_dead_owner(self) -> bool:
        try:
            if self.path.is_symlink():
                raise LifecycleIntegrityError(
                    f"lifecycle writer lock must not be a symlink: {self.path}"
                )
            data = self.path.read_text(encoding="utf-8")
            metadata = json.loads(data)
        except FileNotFoundError:
            return True
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise LifecycleWriterBusyError(
                f"existing lifecycle lock metadata is unreadable: {self.path}"
            ) from exc
        if not isinstance(metadata, dict):
            raise LifecycleWriterBusyError(
                f"existing lifecycle lock metadata is unreadable: {self.path}"
            )
        lock_id = str(metadata.get("lock_id") or "")
        lock_root = str(metadata.get("root") or "")
        if (
            not lock_id
            or lock_root != str(self.root)
            or not str(metadata.get("prediction_run_id") or "")
            or not str(metadata.get("command") or "")
        ):
            raise LifecycleWriterBusyError(
                f"existing lifecycle lock has invalid ownership metadata: {self.path}"
            )
        owner_host = str(metadata.get("hostname") or "")
        try:
            owner_pid = int(metadata["pid"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LifecycleWriterBusyError(
                f"existing lifecycle lock has no verifiable owner: {self.path}"
            ) from exc
        if owner_host != socket.gethostname():
            return False
        if self.process_checker(owner_pid):
            return False
        recovery_path = self.root / f".writer.lock.recovered-{uuid4().hex}"
        try:
            os.replace(self.path, recovery_path)
        except FileNotFoundError:
            return True
        except OSError as exc:
            raise LifecycleWriterBusyError(
                f"unable to recover verified-dead lifecycle lock: {self.path}"
            ) from exc
        try:
            recovery_path.unlink()
        except FileNotFoundError:
            pass
        return True

    def verify_ownership(self) -> None:
        """Verify that this entered lock still owns the real lock path."""

        if self._fd is None:
            raise LifecycleIntegrityError("lifecycle writer lock is not entered")
        try:
            if self.path.is_symlink():
                raise LifecycleIntegrityError(
                    f"lifecycle writer lock must not be a symlink: {self.path}"
                )
            metadata = json.loads(self.path.read_text(encoding="utf-8"))
            descriptor_stat = os.fstat(self._fd)
            path_stat = self.path.stat()
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise LifecycleIntegrityError(
                f"unable to verify lifecycle writer lock ownership: {self.path}"
            ) from exc
        if not isinstance(metadata, dict):
            raise LifecycleIntegrityError(
                f"lifecycle writer lock metadata is malformed: {self.path}"
            )
        expected = {
            "lock_id": self.lock_id,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "root": str(self.root),
            "prediction_run_id": self.prediction_run_id,
            "command": self.command,
            "acquired_at_utc": self.acquired_at_utc,
        }
        if (
            any(metadata.get(key) != value for key, value in expected.items())
            or (descriptor_stat.st_dev, descriptor_stat.st_ino)
            != (path_stat.st_dev, path_stat.st_ino)
        ):
            raise LifecycleIntegrityError(
                f"lifecycle writer lock ownership changed: {self.path}"
            )

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        expected = {
            "lock_id": self.lock_id,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "root": str(self.root),
            "prediction_run_id": self.prediction_run_id,
            "command": self.command,
        }
        for attempt in range(5):
            try:
                metadata = json.loads(self.path.read_text(encoding="utf-8"))
                if all(metadata.get(key) == value for key, value in expected.items()):
                    self.path.unlink()
                return
            except FileNotFoundError:
                return
            except (OSError, json.JSONDecodeError):
                if attempt < 4:
                    time.sleep(0.01)
        # Do not delete a lock whose ownership can no longer be proven.


class LifecycleWriter:
    def __init__(
        self,
        lifecycle_root: str | Path,
        *,
        clock: Clock | None = None,
        lock_timeout_seconds: float = 0.25,
        process_checker: ProcessChecker = process_is_alive,
    ) -> None:
        self.root = Path(lifecycle_root)
        self.clock = clock or SystemClock()
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        self.process_checker = process_checker
        self._active_write_lock = Lock()

    def commit_segment(
        self,
        run_manifest: RunManifest,
        events: Sequence[EventEnvelope],
        *,
        evidence_objects: Sequence[PreparedEvidenceObject] = (),
        failure_hook: FailureHook | None = None,
        command: str = "courtvision lifecycle shadow commit",
    ) -> SegmentCommitResult:
        result = self.run_locked_transaction(
            prediction_run_id=run_manifest.prediction_run_id,
            prepare=lambda: (
                run_manifest,
                tuple(events),
                tuple(evidence_objects),
            ),
            command=command,
            failure_hook=failure_hook,
        )
        if result is None:
            raise LifecycleIntegrityError(
                "ordinary lifecycle commit unexpectedly prepared no segment"
            )
        return result

    def run_locked_transaction(
        self,
        *,
        prediction_run_id: str,
        prepare: Callable[
            [],
            tuple[
                RunManifest,
                Sequence[EventEnvelope],
                Sequence[PreparedEvidenceObject],
            ]
            | None,
        ],
        failure_hook: FailureHook | None = None,
        command: str = "courtvision lifecycle transaction",
    ) -> SegmentCommitResult | None:
        """Prepare and commit at most one segment under one internally held lock.

        The callback receives no lock handle, token, registry, or reusable
        commit capability. Returning ``None`` performs a validated no-op.
        """

        self._enter_active_write()
        try:
            configured_root = Path(self.root)
            if configured_root.exists() and configured_root.is_symlink():
                raise LifecycleIntegrityError(
                    f"lifecycle root must not be a symlink: {configured_root}"
                )
            transaction_root = configured_root.resolve()
            lock = LifecycleWriterLock(
                transaction_root,
                prediction_run_id=prediction_run_id,
                command=command,
                clock=self.clock,
                timeout_seconds=self.lock_timeout_seconds,
                process_checker=self.process_checker,
            )
            with lock:
                lock.verify_ownership()
                prepared = prepare()
                lock.verify_ownership()
                self._verify_transaction_root_unchanged(transaction_root)
                if prepared is None:
                    return None
                run_manifest, events, evidence_objects = prepared
                if run_manifest.prediction_run_id != prediction_run_id:
                    raise LifecycleIntegrityError(
                        "locked transaction/run manifest run ID mismatch"
                    )
                event_tuple = tuple(events)
                evidence_tuple = tuple(evidence_objects)
                self._validate_prepared_segment(
                    run_manifest,
                    event_tuple,
                    evidence_tuple,
                )
                file_blobs = _segment_file_blobs(run_manifest, event_tuple)
                file_metadata = {
                    name: {"sha256": sha256_bytes(data), "size_bytes": len(data)}
                    for name, data in file_blobs.items()
                }
                segment_content_hash = _segment_content_hash(
                    run_manifest.prediction_run_id,
                    file_metadata,
                    event_tuple,
                    evidence_tuple,
                )
                operating = _operating_date_parts(run_manifest.operating_date)
                final_directory = (
                    transaction_root
                    / "ledger"
                    / operating[0]
                    / operating[1]
                    / operating[2]
                    / run_manifest.prediction_run_id
                )
                self._validate_locked_semantic_events(
                    transaction_root,
                    event_tuple,
                )
                existing = self._existing_run_result(
                    transaction_root,
                    final_directory,
                    expected_content_hash=segment_content_hash,
                )
                if existing is not None:
                    return existing
                duplicate_directory = self._validate_global_idempotency(
                    transaction_root,
                    event_tuple,
                )
                if duplicate_directory is not None:
                    verification = verify_segment(
                        duplicate_directory,
                        lifecycle_root=transaction_root,
                    )
                    manifest = _read_canonical_json(
                        duplicate_directory / SEGMENT_MANIFEST_FILE
                    )
                    return SegmentCommitResult(
                        status="ALREADY_COMMITTED",
                        segment_directory=duplicate_directory,
                        segment_manifest=manifest,
                        event_count=verification.event_count,
                    )
                for evidence in evidence_tuple:
                    commit_prepared_evidence(transaction_root, evidence)
                parent = final_directory.parent
                parent.mkdir(parents=True, exist_ok=True)
                stage = (
                    parent
                    / f".{run_manifest.prediction_run_id}.tmp-{uuid4().hex}"
                )
                if stage.exists():
                    raise LifecycleIntegrityError(
                        f"temporary segment already exists: {stage}"
                    )
                stage.mkdir()
                try:
                    _call_failure_hook(
                        failure_hook,
                        "after_temp_directory_created",
                    )
                    for name in (EVENTS_FILE, RUN_MANIFEST_FILE):
                        _write_new_file(stage / name, file_blobs[name])
                    _call_failure_hook(failure_hook, "after_data_files_written")
                    segment_manifest = {
                        "segment_manifest_schema_version": (
                            SEGMENT_MANIFEST_SCHEMA_VERSION
                        ),
                        "canonicalization_version": "canonical_json_v1",
                        "hash_algorithm": "SHA-256",
                        "prediction_run_id": run_manifest.prediction_run_id,
                        "operating_date": run_manifest.operating_date,
                        "event_count": len(event_tuple),
                        "event_hashes": [
                            event.event_hash for event in event_tuple
                        ],
                        "idempotency_keys": [
                            event.idempotency_key for event in event_tuple
                        ],
                        "file_metadata": file_metadata,
                        "evidence_hashes": sorted(
                            {evidence.sha256 for evidence in evidence_tuple}
                        ),
                        "segment_content_sha256": segment_content_hash,
                    }
                    manifest_blob = canonical_payload_bytes(segment_manifest)
                    _write_new_file(
                        stage / SEGMENT_MANIFEST_FILE,
                        manifest_blob,
                    )
                    marker_blob = (
                        f"manifest_sha256={sha256_bytes(manifest_blob)}\n".encode(
                            "ascii"
                        )
                    )
                    _write_new_file(stage / COMPLETE_MARKER, marker_blob)
                    verification = verify_segment(
                        stage,
                        lifecycle_root=transaction_root,
                    )
                    if not verification.ok:
                        raise LifecycleIntegrityError(
                            "staged lifecycle segment failed verification: "
                            + "; ".join(verification.violations)
                        )
                    _call_failure_hook(failure_hook, "before_atomic_rename")
                    _fsync_directory(stage)
                    _rename_directory_with_retry(stage, final_directory)
                    _fsync_directory(parent)
                except Exception:
                    if stage.exists():
                        shutil.rmtree(stage)
                    raise
                committed_verification = verify_segment(
                    final_directory,
                    lifecycle_root=transaction_root,
                )
                if not committed_verification.ok:
                    raise LifecycleIntegrityError(
                        "committed lifecycle segment failed verification: "
                        + "; ".join(committed_verification.violations)
                    )
                return SegmentCommitResult(
                    status="COMMITTED",
                    segment_directory=final_directory,
                    segment_manifest=segment_manifest,
                    event_count=len(event_tuple),
                )
        finally:
            self._active_write_lock.release()

    def _enter_active_write(self) -> None:
        if not self._active_write_lock.acquire(blocking=False):
            raise LifecycleWriterReentrancyError(
                "this LifecycleWriter instance already has an active write"
            )

    def _verify_transaction_root_unchanged(
        self,
        transaction_root: Path,
    ) -> None:
        try:
            configured_root = Path(self.root).resolve()
        except (OSError, TypeError, ValueError) as exc:
            raise LifecycleIntegrityError(
                "writer.root was mutated during the active transaction"
            ) from exc
        if configured_root != transaction_root:
            raise LifecycleIntegrityError(
                "writer.root was mutated during the active transaction; "
                f"captured={transaction_root}, current={configured_root}"
            )

    def _validate_locked_semantic_events(
        self,
        transaction_root: Path,
        events: Sequence[EventEnvelope],
    ) -> None:
        review_events = tuple(
            event
            for event in events
            if event.event_type == "OFFICIAL_PICK_CANDIDATE_REVIEWED"
        )
        publication_events = tuple(
            event
            for event in events
            if event.event_type == "OFFICIAL_PICK_PUBLISHED"
        )
        if review_events:
            try:
                from courtvision.official_picks.review import (
                    validate_new_official_pick_review_events,
                )

                validate_new_official_pick_review_events(
                    transaction_root,
                    review_events,
                )
            except (ValueError, RuntimeError) as exc:
                raise LifecycleIntegrityError(
                    "invalid or unauthorized OfficialPick review publication"
                ) from exc
        if publication_events:
            try:
                from courtvision.official_picks.service import (
                    validate_new_official_pick_publication_events,
                )

                validate_new_official_pick_publication_events(
                    transaction_root,
                    publication_events,
                )
            except (ValueError, RuntimeError) as exc:
                raise LifecycleIntegrityError(
                    "invalid or unauthorized new OfficialPick publication"
                ) from exc

    def _existing_run_result(
        self,
        transaction_root: Path,
        final_directory: Path,
        *,
        expected_content_hash: str,
    ) -> SegmentCommitResult | None:
        if not final_directory.exists():
            return None
        verification = verify_segment(
            final_directory,
            lifecycle_root=transaction_root,
        )
        if not verification.ok:
            raise LifecycleIntegrityError(
                "existing lifecycle segment failed verification: "
                + "; ".join(verification.violations)
            )
        manifest = _read_canonical_json(final_directory / SEGMENT_MANIFEST_FILE)
        if manifest.get("segment_content_sha256") != expected_content_hash:
            raise IdempotencyConflictError(
                "IDEMPOTENCY_CONFLICT: completed run exists with different content"
            )
        return SegmentCommitResult(
            status="ALREADY_COMMITTED",
            segment_directory=final_directory,
            segment_manifest=manifest,
            event_count=verification.event_count,
        )

    def _validate_global_idempotency(
        self,
        transaction_root: Path,
        events: Sequence[EventEnvelope],
    ) -> Path | None:
        requested = {
            event.idempotency_key: (event.payload_sha256, event.event_hash)
            for event in events
        }
        found: dict[str, tuple[str, str, Path]] = {}
        for segment in completed_segment_directories(transaction_root):
            verification = verify_segment(
                segment,
                lifecycle_root=transaction_root,
            )
            if not verification.ok:
                raise LifecycleIntegrityError(
                    "committed segment failed during idempotency scan: "
                    + "; ".join(verification.violations)
                )
            for existing in read_segment_events(segment):
                key = existing.idempotency_key
                if key not in requested:
                    continue
                expected_payload, expected_event = requested[key]
                if (
                    existing.payload_sha256 != expected_payload
                    or existing.event_hash != expected_event
                ):
                    raise IdempotencyConflictError(
                        f"IDEMPOTENCY_CONFLICT: {key}"
                    )
                found[key] = (existing.payload_sha256, existing.event_hash, segment)
        if not found:
            return None
        if set(found) != set(requested):
            raise LifecycleIntegrityError(
                "partial prior idempotency set found; refusing an ambiguous commit"
            )
        directories = {item[2] for item in found.values()}
        if len(directories) != 1:
            raise LifecycleIntegrityError(
                "prepared run is already distributed across multiple segments"
            )
        return directories.pop()

    def _validate_prepared_segment(
        self,
        run_manifest: RunManifest,
        events: Sequence[EventEnvelope],
        evidence: Sequence[PreparedEvidenceObject],
    ) -> None:
        if not events:
            raise LifecycleWriterError("a lifecycle segment requires at least one event")
        expected_sequence = 1
        previous_hash: str | None = None
        idempotency_keys: set[str] = set()
        for event in events:
            if event.prediction_run_id != run_manifest.prediction_run_id:
                raise LifecycleIntegrityError("event/run manifest run ID mismatch")
            if event.operating_date != run_manifest.operating_date:
                raise LifecycleIntegrityError("event/run manifest operating date mismatch")
            if event.event_sequence != expected_sequence:
                raise LifecycleIntegrityError("event sequences must be contiguous from 1")
            if event.previous_event_hash != previous_hash:
                raise LifecycleIntegrityError("event hash chain is invalid")
            if event.idempotency_key in idempotency_keys:
                raise LifecycleIntegrityError("duplicate idempotency key in segment")
            EventEnvelope.from_dict(event.to_dict())
            idempotency_keys.add(event.idempotency_key)
            previous_hash = event.event_hash
            expected_sequence += 1
        seen_evidence: dict[str, bytes] = {}
        for item in evidence:
            prior = seen_evidence.get(item.sha256)
            if prior is not None and prior != item.data:
                raise LifecycleIntegrityError(
                    "same evidence hash was prepared with different content"
                )
            if sha256_bytes(item.data) != item.sha256:
                raise LifecycleIntegrityError("prepared evidence hash is invalid")
            seen_evidence[item.sha256] = item.data

    def write_reconciliation(
        self,
        report: ReconciliationReport,
        *,
        command: str = "courtvision lifecycle reconciliation",
    ) -> Path:
        self._enter_active_write()
        try:
            configured_root = Path(self.root)
            if configured_root.exists() and configured_root.is_symlink():
                raise LifecycleIntegrityError(
                    f"lifecycle root must not be a symlink: {configured_root}"
                )
            transaction_root = configured_root.resolve()
            operating = _operating_date_parts_from_run_or_report(report)
            path = (
                transaction_root
                / "reconciliation"
                / operating[0]
                / operating[1]
                / operating[2]
                / f"{report.prediction_run_id}.json"
            )
            payload = report.to_dict()
            wrapper = {
                "report": payload,
                "report_sha256": payload_sha256(payload),
            }
            data = canonical_payload_bytes(wrapper)
            with LifecycleWriterLock(
                transaction_root,
                prediction_run_id=report.prediction_run_id,
                command=command,
                clock=self.clock,
                timeout_seconds=self.lock_timeout_seconds,
                process_checker=self.process_checker,
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    if path.is_symlink() or path.read_bytes() != data:
                        raise IdempotencyConflictError(
                            "IDEMPOTENCY_CONFLICT: reconciliation report differs"
                        )
                    return path
                temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
                try:
                    _write_new_file(temporary, data)
                    temporary.rename(path)
                    _fsync_directory(path.parent)
                finally:
                    if temporary.exists():
                        temporary.unlink()
            return path
        finally:
            self._active_write_lock.release()


def _operating_date_parts_from_run_or_report(
    report: ReconciliationReport,
) -> tuple[str, str, str]:
    return _operating_date_parts(report.operating_date)


def _segment_file_blobs(
    run_manifest: RunManifest, events: Sequence[EventEnvelope]
) -> dict[str, bytes]:
    events_blob = b"".join(
        canonical_payload_bytes(event.to_dict()) + b"\n" for event in events
    )
    return {
        EVENTS_FILE: events_blob,
        RUN_MANIFEST_FILE: canonical_payload_bytes(run_manifest.to_dict()),
    }


def _segment_content_hash(
    prediction_run_id: str,
    file_metadata: Mapping[str, Any],
    events: Sequence[EventEnvelope],
    evidence: Sequence[PreparedEvidenceObject],
) -> str:
    return payload_sha256(
        {
            "segment_manifest_schema_version": SEGMENT_MANIFEST_SCHEMA_VERSION,
            "prediction_run_id": prediction_run_id,
            "file_metadata": dict(file_metadata),
            "event_hashes": [event.event_hash for event in events],
            "evidence_hashes": sorted({item.sha256 for item in evidence}),
        }
    )


def _write_new_file(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _rename_directory_with_retry(source: Path, target: Path) -> None:
    """Perform the final same-volume rename, tolerating brief Windows sharing."""

    for attempt in range(5):
        try:
            source.rename(target)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.01)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _operating_date_parts(value: str) -> tuple[str, str, str]:
    try:
        parsed = datetime.strptime(str(value), "%Y-%m-%d")
    except ValueError as exc:
        raise LifecycleWriterError(
            f"operating_date must be YYYY-MM-DD: {value!r}"
        ) from exc
    return parsed.strftime("%Y"), parsed.strftime("%m"), parsed.strftime("%d")


def _call_failure_hook(hook: FailureHook | None, stage: str) -> None:
    if hook is not None:
        hook(stage)


def _read_canonical_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleIntegrityError(f"unable to read JSON file: {path}") from exc
    if not isinstance(value, dict) or canonical_json_v1(value) != text:
        raise LifecycleIntegrityError(f"JSON file is not canonical JSON v1: {path}")
    return value


def completed_segment_directories(lifecycle_root: str | Path) -> tuple[Path, ...]:
    ledger = Path(lifecycle_root) / "ledger"
    if not ledger.is_dir():
        return ()
    segments: list[Path] = []
    for marker in ledger.rglob(COMPLETE_MARKER):
        segment = marker.parent
        relative_parts = segment.relative_to(ledger).parts
        if any(part.startswith(".") for part in relative_parts):
            continue
        if segment.is_symlink() or marker.is_symlink():
            continue
        segments.append(segment)
    return tuple(sorted(set(segments), key=lambda item: item.as_posix()))


def read_segment_events(segment_directory: str | Path) -> tuple[EventEnvelope, ...]:
    path = Path(segment_directory) / EVENTS_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LifecycleIntegrityError(f"unable to read event file: {path}") from exc
    events: list[EventEnvelope] = []
    for index, line in enumerate(lines, start=1):
        try:
            value = json.loads(line)
            if canonical_json_v1(value) != line:
                raise ValueError("event line is not canonical JSON v1")
            events.append(EventEnvelope.from_dict(value))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise LifecycleIntegrityError(
                f"invalid event at {path}:{index}"
            ) from exc
    return tuple(events)


def verify_segment(
    segment_directory: str | Path,
    *,
    lifecycle_root: str | Path | None = None,
) -> SegmentVerificationResult:
    segment = Path(segment_directory)
    violations: list[str] = []
    event_count = 0
    required = (
        EVENTS_FILE,
        RUN_MANIFEST_FILE,
        SEGMENT_MANIFEST_FILE,
        COMPLETE_MARKER,
    )
    if not segment.is_dir() or segment.is_symlink():
        return SegmentVerificationResult(
            False, segment, ("segment directory is missing or a symlink",), 0
        )
    for name in required:
        path = segment / name
        if not path.is_file() or path.is_symlink():
            violations.append(f"missing or unsafe file: {name}")
    if violations:
        return SegmentVerificationResult(False, segment, tuple(violations), 0)
    try:
        manifest = _read_canonical_json(segment / SEGMENT_MANIFEST_FILE)
    except LifecycleIntegrityError as exc:
        return SegmentVerificationResult(False, segment, (str(exc),), 0)
    manifest_blob = (segment / SEGMENT_MANIFEST_FILE).read_bytes()
    expected_marker = (
        f"manifest_sha256={sha256_bytes(manifest_blob)}\n".encode("ascii")
    )
    if (segment / COMPLETE_MARKER).read_bytes() != expected_marker:
        violations.append("COMPLETE marker does not bind the segment manifest")
    if manifest.get("segment_manifest_schema_version") != SEGMENT_MANIFEST_SCHEMA_VERSION:
        violations.append("unsupported segment manifest schema version")
    file_metadata = manifest.get("file_metadata")
    if not isinstance(file_metadata, dict):
        violations.append("segment file_metadata is missing")
        file_metadata = {}
    for name in (EVENTS_FILE, RUN_MANIFEST_FILE):
        expected = file_metadata.get(name)
        path = segment / name
        if not isinstance(expected, dict):
            violations.append(f"missing file metadata: {name}")
            continue
        if expected.get("sha256") != file_sha256(path):
            violations.append(f"file SHA-256 mismatch: {name}")
        if expected.get("size_bytes") != path.stat().st_size:
            violations.append(f"file size mismatch: {name}")
    try:
        run_manifest = _read_canonical_json(segment / RUN_MANIFEST_FILE)
        RunManifest.from_dict(run_manifest)
    except (LifecycleIntegrityError, TypeError, ValueError) as exc:
        violations.append(str(exc))
        run_manifest = {}
    try:
        events = read_segment_events(segment)
        event_count = len(events)
    except LifecycleIntegrityError as exc:
        violations.append(str(exc))
        events = ()
    if events:
        previous: str | None = None
        for sequence, event in enumerate(events, start=1):
            if event.event_sequence != sequence:
                violations.append("event sequence is not contiguous")
            if event.previous_event_hash != previous:
                violations.append("event previous_event_hash chain mismatch")
            previous = event.event_hash
    if manifest.get("event_count") != len(events):
        violations.append("event count mismatch")
    if manifest.get("event_hashes") != [event.event_hash for event in events]:
        violations.append("event hash list mismatch")
    run_id = manifest.get("prediction_run_id")
    if run_manifest.get("prediction_run_id") != run_id:
        violations.append("run manifest ID mismatch")
    if any(event.prediction_run_id != run_id for event in events):
        violations.append("event run ID mismatch")
    evidence_hashes = manifest.get("evidence_hashes")
    if not isinstance(evidence_hashes, list):
        violations.append("evidence hash list is invalid")
        evidence_hashes = []
    if lifecycle_root is not None:
        for digest in evidence_hashes:
            if not verify_evidence(Path(lifecycle_root), str(digest)):
                violations.append(f"evidence object failed verification: {digest}")
    recomputed_content_hash = _segment_content_hash(
        str(run_id),
        file_metadata,
        events,
        tuple(
            PreparedEvidenceObject("verified", str(digest), b"")
            for digest in evidence_hashes
        ),
    )
    if manifest.get("segment_content_sha256") != recomputed_content_hash:
        violations.append("segment content SHA-256 mismatch")
    return SegmentVerificationResult(
        not violations,
        segment,
        tuple(violations),
        event_count,
    )


def verify_all_segments(
    lifecycle_root: str | Path,
) -> tuple[SegmentVerificationResult, ...]:
    root = Path(lifecycle_root)
    return tuple(
        verify_segment(segment, lifecycle_root=root)
        for segment in completed_segment_directories(root)
    )


__all__ = [
    "COMPLETE_MARKER",
    "EVENTS_FILE",
    "IdempotencyConflictError",
    "LifecycleIntegrityError",
    "LifecycleWriter",
    "LifecycleWriterBusyError",
    "LifecycleWriterError",
    "LifecycleWriterReentrancyError",
    "LifecycleWriterLock",
    "RUN_MANIFEST_FILE",
    "SEGMENT_MANIFEST_FILE",
    "SegmentCommitResult",
    "SegmentVerificationResult",
    "completed_segment_directories",
    "process_is_alive",
    "read_segment_events",
    "verify_all_segments",
    "verify_segment",
]
