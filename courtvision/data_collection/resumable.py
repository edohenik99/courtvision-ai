"""Sport-agnostic resumable chunked collection core.

Owns generic infrastructure for all sports:
  - Chunk specifications and deterministic planning
  - Collection state persistence (atomic JSON writes via os.replace)
  - Retry / exponential-backoff policy
  - Resume behaviour (skip already-completed chunks)
  - Post-all-chunks merge (headers-once CSV concatenation)
  - Protected-path validation

Does NOT import from any sport-specific module.
Does NOT know how to fetch data; that is the sport adapter's responsibility.

When a sport adapter wants resumable collection it:
  1. Imports ChunkSize, build_chunk_plan, ResumableCollector, RetryPolicy.
  2. Provides a fetch_chunk_csv callable: (ChunkSpec, chunks_dir: Path) -> Path.
  3. Calls ResumableCollector.merge(output_path, fetch_chunk_csv, transient_errors).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import IntEnum
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Callable, Final, Literal

from courtvision.data_collection.core import CollectionError
from courtvision.data_collection.path_guards import (
    ProtectedPathError,  # re-exported for adapter convenience
    validate_output_root,
)


STATE_FILENAME: Final = "collection_state.json"
STATE_SCHEMA_VERSION: Final = "1.1"
_LEGACY_STATE_SCHEMA_VERSIONS: Final = frozenset({"1.0"})
ChunkStatus = Literal["valid_data", "empty_no_data", "invalid_schema"]
_CHUNK_STATUSES: Final = frozenset(
    {"valid_data", "empty_no_data", "invalid_schema"}
)


# ---------------------------------------------------------------------------
# Chunk planning
# ---------------------------------------------------------------------------


class ChunkSize(IntEnum):
    """Supported chunk durations in calendar days."""

    WEEKLY = 7
    BIWEEKLY = 14


@dataclass(frozen=True, slots=True)
class ChunkSpec:
    """Immutable identity for one date-range collection window.

    chunk_key has the canonical form ``"YYYY-MM-DD_YYYY-MM-DD"`` and is used
    as the stable filename stem and state-tracking key.
    """

    start: date
    end: date
    chunk_index: int
    chunk_key: str

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("chunk start must not be after end")
        if (
            isinstance(self.chunk_index, bool)
            or not isinstance(self.chunk_index, int)
            or self.chunk_index < 0
        ):
            raise ValueError("chunk_index must be a non-negative integer")
        expected = f"{self.start.isoformat()}_{self.end.isoformat()}"
        if self.chunk_key != expected:
            raise ValueError(
                f"chunk_key must be {expected!r}, got {self.chunk_key!r}"
            )


def build_chunk_plan(
    start: date,
    end: date,
    chunk_size: ChunkSize | int,
) -> tuple[ChunkSpec, ...]:
    """Return an ordered, deterministic tuple of ChunkSpec covering [start, end].

    The final chunk receives any remaining days (≤ chunk_size).
    """
    if not isinstance(start, date) or not isinstance(end, date):
        raise TypeError("start and end must be dates")
    if start > end:
        raise ValueError("chunk plan start must not be after end")
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int):
        raise ValueError("chunk_size must be a positive number of days")
    step = int(chunk_size)
    if step <= 0:
        raise ValueError("chunk_size must be a positive number of days")
    chunks: list[ChunkSpec] = []
    current = start
    index = 0
    while current <= end:
        chunk_end = min(current + timedelta(days=step - 1), end)
        key = f"{current.isoformat()}_{chunk_end.isoformat()}"
        chunks.append(
            ChunkSpec(
                start=current,
                end=chunk_end,
                chunk_index=index,
                chunk_key=key,
            )
        )
        current = chunk_end + timedelta(days=1)
        index += 1
    return tuple(chunks)


# ---------------------------------------------------------------------------
# Persistent collection state
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChunkMetadata:
    """Integrity and CSV classification evidence for one downloaded chunk."""

    chunk_key: str
    status: ChunkStatus
    row_count: int
    header_hash: str
    file_hash: str

    def __post_init__(self) -> None:
        if not self.chunk_key.strip():
            raise ValueError("chunk metadata chunk_key is required")
        if self.status not in _CHUNK_STATUSES:
            raise ValueError(f"unsupported chunk metadata status: {self.status!r}")
        if (
            isinstance(self.row_count, bool)
            or not isinstance(self.row_count, int)
            or self.row_count < 0
        ):
            raise ValueError("chunk metadata row_count must be non-negative")
        for field_name in ("header_hash", "file_hash"):
            value = getattr(self, field_name)
            if len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise ValueError(
                    f"chunk metadata {field_name} must be a SHA-256 hex digest"
                )


@dataclass
class CollectionState:
    """Mutable run state serialised to ``collection_state.json``.

    Fields use plain Python types so the file is human-readable and easy to
    inspect after an interrupted run.
    """

    sport: str
    season: int
    start_date: str   # ISO date string
    end_date: str     # ISO date string
    chunk_size_days: int
    chunks_planned: list[str] = field(default_factory=list)
    chunks_completed: list[str] = field(default_factory=list)
    chunks_failed: list[str] = field(default_factory=list)
    chunk_metadata: list[ChunkMetadata] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    merged: bool = False
    schema_version: str = STATE_SCHEMA_VERSION

    # Internal sentinel so load_collection_state can detect stale schemas
    _KNOWN_FIELDS: frozenset[str] = field(
        default=frozenset({
            "sport", "season", "start_date", "end_date", "chunk_size_days",
            "chunks_planned", "chunks_completed", "chunks_failed",
            "chunk_metadata", "warnings", "merged", "schema_version",
        }),
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not self.sport.strip():
            raise ValueError("collection state sport is required")
        if isinstance(self.season, bool) or not isinstance(self.season, int):
            raise ValueError("collection state season must be an integer")
        if (
            isinstance(self.chunk_size_days, bool)
            or not isinstance(self.chunk_size_days, int)
            or self.chunk_size_days <= 0
        ):
            raise ValueError("collection state chunk_size_days must be positive")
        for field_name in ("chunks_planned", "chunks_completed", "chunks_failed"):
            values = getattr(self, field_name)
            if not isinstance(values, list) or not all(
                isinstance(value, str) for value in values
            ):
                raise ValueError(f"collection state {field_name} must be a list of strings")
            if len(values) != len(set(values)):
                raise ValueError(f"collection state {field_name} contains duplicates")
        overlap = set(self.chunks_completed) & set(self.chunks_failed)
        if overlap:
            raise ValueError("collection state cannot mark a chunk complete and failed")
        if self.chunks_planned:
            unknown = (
                set(self.chunks_completed) | set(self.chunks_failed)
            ) - set(self.chunks_planned)
            if unknown:
                raise ValueError("collection state references chunks outside its plan")
        if not isinstance(self.chunk_metadata, list) or not all(
            isinstance(item, ChunkMetadata) for item in self.chunk_metadata
        ):
            raise ValueError("collection state chunk_metadata must be a list")
        metadata_keys = [item.chunk_key for item in self.chunk_metadata]
        if len(metadata_keys) != len(set(metadata_keys)):
            raise ValueError("collection state chunk_metadata contains duplicate keys")
        if self.chunks_planned and set(metadata_keys) - set(self.chunks_planned):
            raise ValueError("collection state metadata references chunks outside its plan")
        if not isinstance(self.warnings, list) or not all(
            isinstance(value, str) and value.strip() for value in self.warnings
        ):
            raise ValueError("collection state warnings must be a list of strings")
        if not isinstance(self.merged, bool):
            raise ValueError("collection state merged must be a boolean")
        if self.schema_version not in {STATE_SCHEMA_VERSION, *_LEGACY_STATE_SCHEMA_VERSIONS}:
            raise ValueError(
                "unsupported collection state schema version: "
                f"{self.schema_version!r}"
            )


def load_collection_state(staging_dir: Path) -> CollectionState | None:
    """Return persisted state, or ``None`` only when no checkpoint exists.

    A malformed checkpoint fails closed. Treating it as a fresh run could mix
    chunks from different requests or overwrite the only recovery evidence.
    """
    path = staging_dir / STATE_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("collection state must be a JSON object")
        # Accept only known fields; silently drop extras from future versions.
        known = {
            "sport", "season", "start_date", "end_date", "chunk_size_days",
            "chunks_planned", "chunks_completed", "chunks_failed",
            "chunk_metadata", "warnings", "merged", "schema_version",
        }
        filtered = {k: v for k, v in payload.items() if k in known}
        raw_metadata = filtered.get("chunk_metadata", [])
        if not isinstance(raw_metadata, list):
            raise TypeError("collection state chunk_metadata must be a list")
        filtered["chunk_metadata"] = [
            ChunkMetadata(**item) if isinstance(item, dict) else item
            for item in raw_metadata
        ]
        state = CollectionState(**filtered)
        if state.schema_version in _LEGACY_STATE_SCHEMA_VERSIONS:
            state.schema_version = STATE_SCHEMA_VERSION
        return state
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CollectionError(f"invalid collection checkpoint: {path}: {exc}") from exc


def save_collection_state(state: CollectionState, staging_dir: Path) -> None:
    """Atomically persist CollectionState.

    Writes to ``collection_state.json.tmp`` then calls ``os.replace`` so
    readers never see a partial file.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)
    target = staging_dir / STATE_FILENAME
    tmp = staging_dir / (STATE_FILENAME + ".tmp")
    payload = {
        "sport": state.sport,
        "season": state.season,
        "start_date": state.start_date,
        "end_date": state.end_date,
        "chunk_size_days": state.chunk_size_days,
        "chunks_planned": list(state.chunks_planned),
        "chunks_completed": list(state.chunks_completed),
        "chunks_failed": list(state.chunks_failed),
        "chunk_metadata": [
            {
                "chunk_key": item.chunk_key,
                "status": item.status,
                "row_count": item.row_count,
                "header_hash": item.header_hash,
                "file_hash": item.file_hash,
            }
            for item in state.chunk_metadata
        ],
        "warnings": list(state.warnings),
        "merged": state.merged,
        "schema_version": state.schema_version,
    }
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, target)


# ---------------------------------------------------------------------------
# Path guard
# ---------------------------------------------------------------------------


def validate_staging_dir(staging_dir: Path) -> Path:
    """Validate that staging_dir does not contain protected path components.

    Raises ProtectedPathError (from path_guards) if any part of the path
    matches the protected component set (e.g. ``outputs``, ``history``).

    Note: containment within the output root is already enforced by
    ``core._materialize_source`` via ``ensure_within_output_root`` before the
    sport materializer is called.  This guard is the second line of defence
    for callers that invoke ``run_chunked_*`` functions directly.
    """
    return validate_output_root(staging_dir)


# ---------------------------------------------------------------------------
# Retry / backoff
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Sport-agnostic exponential backoff configuration.

    ``transient_errors`` is supplied by the sport adapter (e.g. OSError,
    urllib.error.URLError for Statcast) rather than being hard-coded here.
    """

    max_retries: int = 3
    backoff_base: float = 2.0
    backoff_cap: float = 60.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or self.max_retries < 0
        ):
            raise ValueError("max_retries must be a non-negative integer")
        if self.backoff_base < 0:
            raise ValueError("backoff_base must be non-negative")
        if self.backoff_cap < 0:
            raise ValueError("backoff_cap must be non-negative")


def run_with_retry(
    fn: Callable[[], object],
    retry_policy: RetryPolicy,
    transient_errors: tuple[type[BaseException], ...],
    *,
    _sleep: Callable[[float], None] = time.sleep,
) -> object:
    """Call ``fn()``, retrying on ``transient_errors`` with exponential backoff.

    Raises the final exception after ``max_retries`` retries are exhausted.
    The ``_sleep`` parameter is injectable for tests (pass ``lambda _: None``).
    """
    last_exc: BaseException | None = None
    for attempt in range(retry_policy.max_retries + 1):
        try:
            return fn()
        except transient_errors as exc:  # type: ignore[misc]
            last_exc = exc
            if attempt < retry_policy.max_retries:
                wait = min(
                    retry_policy.backoff_base ** attempt,
                    retry_policy.backoff_cap,
                )
                _sleep(wait)
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def _header_hash(header: list[str]) -> str:
    """Hash a canonical JSON representation of parsed CSV header fields."""

    payload = json.dumps(
        header,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def classify_chunk_csvs(chunk_paths: tuple[Path, ...]) -> tuple[ChunkMetadata, ...]:
    """Classify chunks against the first non-empty data chunk's CSV header.

    Zero-row chunks are safe no-data chunks regardless of whether pybaseball
    emitted a header. Non-empty chunks must all match the first non-empty
    header exactly; mismatches are retained as ``invalid_schema`` evidence.
    """

    missing = [str(path) for path in chunk_paths if not path.is_file()]
    if missing:
        raise CollectionError(
            "cannot merge: missing chunk files: " + ", ".join(missing)
        )
    if not chunk_paths:
        raise CollectionError("cannot merge an empty chunk plan")

    inspected: list[tuple[Path, list[str], int, str, str]] = []
    expected_header: list[str] | None = None
    for chunk_path in chunk_paths:
        file_hash = _file_hash(chunk_path)
        with chunk_path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source)
            try:
                header = next(reader)
            except StopIteration:
                header = []
            row_count = sum(
                1
                for row in reader
                if row and any(value.strip() for value in row)
            )
        if row_count > 0 and header and expected_header is None:
            expected_header = header
        inspected.append(
            (chunk_path, header, row_count, _header_hash(header), file_hash)
        )

    metadata: list[ChunkMetadata] = []
    for chunk_path, header, row_count, header_hash, file_hash in inspected:
        if row_count == 0:
            status: ChunkStatus = "empty_no_data"
        elif header and header == expected_header:
            status = "valid_data"
        else:
            status = "invalid_schema"
        metadata.append(
            ChunkMetadata(
                chunk_key=chunk_path.stem,
                status=status,
                row_count=row_count,
                header_hash=header_hash,
                file_hash=file_hash,
            )
        )
    return tuple(metadata)


def _validate_chunk_metadata(
    chunk_paths: tuple[Path, ...],
    metadata: tuple[ChunkMetadata, ...],
) -> None:
    invalid = [item for item in metadata if item.status == "invalid_schema"]
    if invalid:
        item = invalid[0]
        path_by_key = {path.stem: path for path in chunk_paths}
        expected_header_hash = next(
            candidate.header_hash
            for candidate in metadata
            if candidate.status == "valid_data"
        )
        raise CollectionError(
            "cannot merge non-empty chunk with mismatched CSV header: "
            f"{path_by_key[item.chunk_key]} "
            f"(rows={item.row_count}, header_hash={item.header_hash}, "
            f"expected_header_hash={expected_header_hash})"
        )
    if not any(item.status == "valid_data" for item in metadata):
        raise CollectionError("cannot merge: no valid data chunks were found")


def _merge_classified_chunk_csvs(
    chunk_paths: tuple[Path, ...],
    metadata: tuple[ChunkMetadata, ...],
    output_path: Path,
) -> tuple[Path, int]:
    _validate_chunk_metadata(chunk_paths, metadata)
    if output_path.exists():
        raise FileExistsError(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f"{output_path.name}.tmp")
    if temporary.exists():
        raise FileExistsError(temporary)

    total_rows = 0
    wrote_header = False
    try:
        with temporary.open("x", encoding="utf-8", newline="") as out:
            writer = csv.writer(out, lineterminator="\n")
            for chunk_path, item in zip(chunk_paths, metadata, strict=True):
                if item.status == "empty_no_data":
                    continue
                with chunk_path.open("r", encoding="utf-8-sig", newline="") as source:
                    reader = csv.reader(source)
                    header = next(reader)
                    if not wrote_header:
                        writer.writerow(header)
                        wrote_header = True
                    for row in reader:
                        if not row or not any(value.strip() for value in row):
                            continue
                        writer.writerow(row)
                        total_rows += 1
        os.link(temporary, output_path)
        temporary.unlink()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output_path, total_rows


def merge_chunk_csvs(
    chunk_paths: tuple[Path, ...],
    output_path: Path,
) -> tuple[Path, int]:
    """Concatenate ordered chunk CSVs (headers-once) into an immutable file.

    The first valid data chunk's header row is written once; subsequent valid
    chunks skip their header. Empty/no-data chunks are omitted. The merge is
    sport-agnostic; all CSV structure knowledge lives in the sport adapter's
    fetch logic.

    Returns ``(output_path, total_data_row_count)``.

    Raises:
        CollectionError:  if any chunk file is missing.
        FileExistsError:  if ``output_path`` already exists (immutable write).
    """
    metadata = classify_chunk_csvs(chunk_paths)
    return _merge_classified_chunk_csvs(chunk_paths, metadata, output_path)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class ResumableCollector:
    """Sport-agnostic stateful orchestrator for chunked resumable collection.

    Usage::

        collector = ResumableCollector(staging_dir, output_root, state, policy)
        collector.set_chunks(chunks)
        result_path = collector.merge(output_path, fetch_fn, transient_errs)

    ``fetch_chunk_csv`` signature: ``(chunk: ChunkSpec, chunks_dir: Path) -> Path``

    The collector does not know the format or provider; it only manages which
    chunks are complete and drives the retry/persist loop.
    """

    def __init__(
        self,
        staging_dir: Path,
        state: CollectionState,
        retry_policy: RetryPolicy,
        *,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._staging_dir = staging_dir
        self._state = state
        self._retry_policy = retry_policy
        self._sleep = _sleep
        self._all_chunks: tuple[ChunkSpec, ...] = ()

    def set_chunks(self, chunks: tuple[ChunkSpec, ...]) -> None:
        """Register the full deterministic chunk plan for this run."""
        if not chunks:
            raise CollectionError("chunk plan must not be empty")
        planned = [c.chunk_key for c in chunks]
        if self._state.chunks_planned and self._state.chunks_planned != planned:
            raise CollectionError("checkpoint chunk plan does not match this request")
        self._all_chunks = chunks
        self._state.chunks_planned = planned
        save_collection_state(self._state, self._staging_dir)

    def pending_chunks(self) -> tuple[ChunkSpec, ...]:
        """Return chunks not yet in ``chunks_completed``, in order."""
        done = set(self._state.chunks_completed)
        return tuple(c for c in self._all_chunks if c.chunk_key not in done)

    def mark_complete(self, chunk: ChunkSpec) -> None:
        """Mark chunk as complete and atomically persist state."""
        if chunk.chunk_key not in self._state.chunks_completed:
            self._state.chunks_completed.append(chunk.chunk_key)
        if chunk.chunk_key in self._state.chunks_failed:
            self._state.chunks_failed.remove(chunk.chunk_key)
        save_collection_state(self._state, self._staging_dir)

    def mark_failed(self, chunk: ChunkSpec) -> None:
        """Mark chunk as failed and atomically persist state."""
        if chunk.chunk_key not in self._state.chunks_failed:
            self._state.chunks_failed.append(chunk.chunk_key)
        save_collection_state(self._state, self._staging_dir)

    def all_complete(self) -> bool:
        """Return True iff every planned chunk is in ``chunks_completed``."""
        planned = set(self._state.chunks_planned)
        completed = set(self._state.chunks_completed)
        return bool(planned) and planned == completed

    def merge(
        self,
        output_path: Path,
        fetch_chunk_csv: Callable[[ChunkSpec, Path], Path],
        transient_errors: tuple[type[BaseException], ...],
    ) -> Path:
        """Fetch all pending chunks then merge to ``output_path``.

        - Each successful chunk is persisted immediately before the next starts.
        - On a transient fetch error the chunk is retried per ``retry_policy``.
        - On a permanent failure the chunk is marked failed and the exception
          propagates; already-completed chunks are preserved in state.
        - Merge is called only after ``all_complete()`` is True.
        """
        chunks_dir = self._staging_dir / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)

        known_keys = {chunk.chunk_key for chunk in self._all_chunks}
        unknown_completed = set(self._state.chunks_completed) - known_keys
        unknown_state = unknown_completed | (set(self._state.chunks_failed) - known_keys)
        if unknown_state:
            raise CollectionError(
                "checkpoint contains chunks outside the plan: "
                + ", ".join(sorted(unknown_state))
            )
        for chunk in self._all_chunks:
            if chunk.chunk_key in self._state.chunks_completed:
                checkpoint_path = chunks_dir / f"{chunk.chunk_key}.csv"
                if not checkpoint_path.is_file():
                    raise CollectionError(
                        f"completed checkpoint chunk is missing: {checkpoint_path}"
                    )
            else:
                checkpoint_path = chunks_dir / f"{chunk.chunk_key}.csv"
                if checkpoint_path.is_file():
                    # Recover the narrow crash window after an atomic chunk
                    # write but before its state update.
                    self.mark_complete(chunk)

        for chunk in self.pending_chunks():
            def _fetch(c: ChunkSpec = chunk) -> Path:
                return fetch_chunk_csv(c, chunks_dir)

            try:
                fetched = run_with_retry(
                    _fetch,
                    self._retry_policy,
                    transient_errors,
                    _sleep=self._sleep,
                )
                expected = (chunks_dir / f"{chunk.chunk_key}.csv").resolve()
                if not isinstance(fetched, Path) or fetched.resolve() != expected:
                    raise CollectionError(
                        f"chunk fetch returned unexpected path: {fetched!r}"
                    )
                if not expected.is_file():
                    raise CollectionError(f"chunk fetch produced no file: {expected}")
                self.mark_complete(chunk)
            except Exception:
                self.mark_failed(chunk)
                raise

        if not self.all_complete():
            raise CollectionError(
                "not all chunks completed; cannot merge"
            )

        ordered_paths = tuple(
            self._staging_dir / "chunks" / f"{c.chunk_key}.csv"
            for c in self._all_chunks
        )
        metadata = classify_chunk_csvs(ordered_paths)
        prior_metadata = {item.chunk_key: item for item in self._state.chunk_metadata}
        for item in metadata:
            prior = prior_metadata.get(item.chunk_key)
            if prior is not None and prior.file_hash != item.file_hash:
                raise CollectionError(
                    "completed checkpoint chunk changed after classification: "
                    f"{item.chunk_key}"
                )
        self._state.chunk_metadata = list(metadata)
        self._state.warnings = [
            f"Chunk {item.chunk_key} contained no data and was skipped during CSV merge."
            for item in metadata
            if item.status == "empty_no_data"
        ]
        save_collection_state(self._state, self._staging_dir)
        result_path, _ = _merge_classified_chunk_csvs(
            ordered_paths,
            metadata,
            output_path,
        )
        self._state.merged = True
        save_collection_state(self._state, self._staging_dir)
        return result_path


__all__ = [
    "ChunkSize",
    "ChunkSpec",
    "ChunkMetadata",
    "ChunkStatus",
    "CollectionState",
    "ProtectedPathError",
    "ResumableCollector",
    "RetryPolicy",
    "STATE_FILENAME",
    "STATE_SCHEMA_VERSION",
    "build_chunk_plan",
    "classify_chunk_csvs",
    "load_collection_state",
    "merge_chunk_csvs",
    "run_with_retry",
    "save_collection_state",
    "validate_staging_dir",
]
