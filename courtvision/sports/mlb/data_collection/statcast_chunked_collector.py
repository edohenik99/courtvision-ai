"""MLB Statcast-specific chunked and resumable collection."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Final
import urllib.error

from courtvision.data_collection.core import CollectionError, CollectionRequest
from courtvision.data_collection.resumable import (
    ChunkSize,
    ChunkSpec,
    CollectionState,
    ResumableCollector,
    RetryPolicy,
    build_chunk_plan,
    load_collection_state,
    validate_staging_dir,
)


STATCAST_DEFAULT_CHUNK_SIZE: Final[ChunkSize] = ChunkSize.WEEKLY
_TRANSIENT_ERRORS: list[type[BaseException]] = [
    OSError,
    urllib.error.URLError,
    TimeoutError,
]
try:
    from requests.exceptions import RequestException
except ImportError:  # Optional collector dependency; dry-run remains available.
    pass
else:
    _TRANSIENT_ERRORS.append(RequestException)
STATCAST_TRANSIENT_ERRORS: Final[tuple[type[BaseException], ...]] = tuple(
    _TRANSIENT_ERRORS
)
_CHUNK_SIZE_MAP: Final[dict[str, ChunkSize]] = {
    "weekly": ChunkSize.WEEKLY,
    "biweekly": ChunkSize.BIWEEKLY,
}


def chunk_size_from_str(value: str) -> ChunkSize:
    """Parse a CLI chunk-size name."""

    key = value.strip().lower()
    if key not in _CHUNK_SIZE_MAP:
        raise ValueError(
            f"statcast_chunk_size must be 'weekly' or 'biweekly': {value!r}"
        )
    return _CHUNK_SIZE_MAP[key]


def _pybaseball_module():
    try:
        return importlib.import_module("pybaseball")
    except ImportError as exc:
        raise CollectionError(
            "--fetch-statcast requires the optional pybaseball package"
        ) from exc


def enable_pybaseball_cache() -> None:
    """Enable pybaseball's HTTP response cache for an actual collection."""

    _pybaseball_module().cache.enable()


def fetch_statcast_chunk(
    chunk: ChunkSpec,
    chunks_dir: Path,
    *,
    allow_network: bool = False,
) -> Path:
    """Fetch one date chunk through pybaseball into its canonical CSV path."""

    if not allow_network:
        raise PermissionError("Statcast network access requires allow_network=True")

    destination = chunks_dir / f"{chunk.chunk_key}.csv"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    frame = _pybaseball_module().statcast(
        start_dt=chunk.start.isoformat(),
        end_dt=chunk.end.isoformat(),
        verbose=False,
        parallel=False,
    )
    temporary = chunks_dir / f".{chunk.chunk_key}.csv.tmp"
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("x", encoding="utf-8", newline="") as handle:
            frame.to_csv(handle, index=False)
        os.link(temporary, destination)
        temporary.unlink()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination.resolve()


def _state_identity(state: CollectionState) -> tuple[object, ...]:
    return (
        state.sport,
        state.season,
        state.start_date,
        state.end_date,
        state.chunk_size_days,
    )


def run_chunked_statcast(
    request: CollectionRequest,
    staging_dir: Path,
    output_path: Path,
    *,
    chunk_size: ChunkSize = STATCAST_DEFAULT_CHUNK_SIZE,
    resume: bool = False,
    retry_policy: RetryPolicy | None = None,
    dry_run: bool = False,
    allow_network: bool = False,
    _sleep=None,
) -> Path | None:
    """Collect, checkpoint, resume, and merge an approved Statcast request."""

    if dry_run:
        return None

    validate_staging_dir(staging_dir)
    enable_pybaseball_cache()
    chunks = build_chunk_plan(request.start_date, request.end_date, chunk_size)

    existing_state = load_collection_state(staging_dir)
    if resume and existing_state is None:
        raise CollectionError(
            f"--resume requested but no collection checkpoint exists in {staging_dir}"
        )
    if not resume and existing_state is not None:
        raise CollectionError(
            f"collection checkpoint already exists; rerun with --resume: {staging_dir}"
        )

    state = existing_state or CollectionState(
        sport=request.sport,
        season=request.season,
        start_date=request.start_date.isoformat(),
        end_date=request.end_date.isoformat(),
        chunk_size_days=int(chunk_size),
    )
    expected_identity = (
        request.sport,
        request.season,
        request.start_date.isoformat(),
        request.end_date.isoformat(),
        int(chunk_size),
    )
    if _state_identity(state) != expected_identity:
        raise CollectionError("collection checkpoint does not match this request")

    expected_chunk_keys = [chunk.chunk_key for chunk in chunks]
    if state.chunks_planned and state.chunks_planned != expected_chunk_keys:
        raise CollectionError("checkpoint chunk plan does not match this request")

    if state.merged:
        if set(state.chunks_completed) != set(expected_chunk_keys):
            raise CollectionError("merged checkpoint does not contain every planned chunk")
        if not output_path.is_file():
            raise CollectionError(
                f"checkpoint is marked merged but output is missing: {output_path}"
            )
        return output_path.resolve()

    import time

    collector = ResumableCollector(
        staging_dir=staging_dir,
        state=state,
        retry_policy=retry_policy or RetryPolicy(),
        _sleep=_sleep if _sleep is not None else time.sleep,
    )
    collector.set_chunks(chunks)

    def _fetch_chunk(chunk: ChunkSpec, chunks_dir: Path) -> Path:
        return fetch_statcast_chunk(
            chunk,
            chunks_dir,
            allow_network=allow_network,
        )

    return collector.merge(output_path, _fetch_chunk, STATCAST_TRANSIENT_ERRORS)


__all__ = [
    "STATCAST_DEFAULT_CHUNK_SIZE",
    "STATCAST_TRANSIENT_ERRORS",
    "chunk_size_from_str",
    "enable_pybaseball_cache",
    "fetch_statcast_chunk",
    "run_chunked_statcast",
]
