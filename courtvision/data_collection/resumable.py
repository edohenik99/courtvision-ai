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
import json
import os
from pathlib import Path
import time
from typing import Callable, Final

from courtvision.data_collection.core import CollectionError
from courtvision.data_collection.path_guards import (
    ProtectedPathError,  # re-exported for adapter convenience
    validate_output_root,
)


STATE_FILENAME: Final = "collection_state.json"
STATE_SCHEMA_VERSION: Final = "1.0"


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
    merged: bool = False
    schema_version: str = STATE_SCHEMA_VERSION

    # Internal sentinel so load_collection_state can detect stale schemas
    _KNOWN_FIELDS: frozenset[str] = field(
        default=frozenset({
            "sport", "season", "start_date", "end_date", "chunk_size_days",
            "chunks_planned", "chunks_completed", "chunks_failed",
            "merged", "schema_version",
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
        if not isinstance(self.merged, bool):
            raise ValueError("collection state merged must be a boolean")
        if self.schema_version != STATE_SCHEMA_VERSION:
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
            "merged", "schema_version",
        }
        filtered = {k: v for k, v in payload.items() if k in known}
        return CollectionState(**filtered)
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


def merge_chunk_csvs(
    chunk_paths: tuple[Path, ...],
    output_path: Path,
) -> tuple[Path, int]:
    """Concatenate ordered chunk CSVs (headers-once) into an immutable file.

    The first chunk's header row is written once; subsequent chunks skip their
    header.  The merge is sport-agnostic; all CSV structure knowledge lives in
    the sport adapter's fetch logic.

    Returns ``(output_path, total_data_row_count)``.

    Raises:
        CollectionError:  if any chunk file is missing.
        FileExistsError:  if ``output_path`` already exists (immutable write).
    """
    missing = [str(p) for p in chunk_paths if not p.is_file()]
    if missing:
        raise CollectionError(
            "cannot merge: missing chunk files: " + ", ".join(missing)
        )
    if output_path.exists():
        raise FileExistsError(output_path)

    if not chunk_paths:
        raise CollectionError("cannot merge an empty chunk plan")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f"{output_path.name}.tmp")
    if temporary.exists():
        raise FileExistsError(temporary)

    total_rows = 0
    expected_header: list[str] | None = None
    try:
        with temporary.open("x", encoding="utf-8", newline="") as out:
            writer = csv.writer(out, lineterminator="\n")
            for chunk_path in chunk_paths:
                with chunk_path.open("r", encoding="utf-8-sig", newline="") as source:
                    reader = csv.reader(source)
                    try:
                        header = next(reader)
                    except StopIteration as exc:
                        raise CollectionError(
                            f"cannot merge empty chunk CSV: {chunk_path}"
                        ) from exc
                    if expected_header is None:
                        expected_header = header
                        writer.writerow(header)
                    elif header != expected_header:
                        raise CollectionError(
                            f"cannot merge chunk with mismatched CSV header: {chunk_path}"
                        )
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
        result_path, _ = merge_chunk_csvs(ordered_paths, output_path)
        self._state.merged = True
        save_collection_state(self._state, self._staging_dir)
        return result_path


__all__ = [
    "ChunkSize",
    "ChunkSpec",
    "CollectionState",
    "ProtectedPathError",
    "ResumableCollector",
    "RetryPolicy",
    "STATE_FILENAME",
    "STATE_SCHEMA_VERSION",
    "build_chunk_plan",
    "load_collection_state",
    "merge_chunk_csvs",
    "run_with_retry",
    "save_collection_state",
    "validate_staging_dir",
]
