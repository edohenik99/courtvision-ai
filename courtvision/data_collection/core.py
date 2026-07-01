"""Sport-agnostic orchestration for immutable raw source collections."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
import re
import shutil
from typing import Callable, Mapping, Protocol

from courtvision.data_collection.manifest import (
    COLLECTOR_VERSION,
    CollectionManifest,
    ManifestSource,
    row_count_for_file,
    sha256_file,
    write_collection_manifest,
)
from courtvision.data_collection.path_guards import (
    ensure_within_output_root,
    validate_output_root,
)
from courtvision.data_collection.source_contracts import (
    SourceContract,
    reject_disallowed_source,
)


class CollectionError(RuntimeError):
    """Base exception for collection planning or acquisition failures."""


class UnsupportedSportCollectionError(CollectionError):
    """Raised by registered sport stubs that have no collection adapter."""


Materializer = Callable[[Path], tuple[Path, ...]]
RowCounter = Callable[[Path], int | None]


@dataclass(frozen=True, slots=True)
class CollectionRequest:
    sport: str
    season: int
    start_date: date
    end_date: date
    output_raw_dir: Path
    dry_run: bool = False
    resume: bool = False
    source_options: Mapping[str, object] = field(default_factory=dict)
    collection_id: str | None = None
    collection_timestamp: datetime | None = None

    def __post_init__(self) -> None:
        sport = self.sport.strip().lower()
        if not sport:
            raise ValueError("sport is required")
        if isinstance(self.season, bool) or not isinstance(self.season, int):
            raise ValueError("season must be an integer")
        if not isinstance(self.start_date, date) or isinstance(self.start_date, datetime):
            raise ValueError("start_date must be a date")
        if not isinstance(self.end_date, date) or isinstance(self.end_date, datetime):
            raise ValueError("end_date must be a date")
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        options = dict(self.source_options)
        option_resume = options.get("resume", False)
        if not isinstance(self.resume, bool) or not isinstance(option_resume, bool):
            raise ValueError("resume must be a boolean")
        if not self.start_date.year <= self.season <= self.end_date.year:
            raise ValueError("season must overlap the requested date range")
        timestamp = self.collection_timestamp or datetime.now(timezone.utc)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("collection_timestamp must be timezone-aware")
        collection_id = self.collection_id or timestamp.astimezone(timezone.utc).strftime(
            "collection-%Y%m%dT%H%M%S%fZ"
        )
        if not re.fullmatch(r"(?:collection-|v)[A-Za-z0-9][A-Za-z0-9_.-]*", collection_id):
            raise ValueError("collection_id must be a safe versioned token")
        object.__setattr__(self, "sport", sport)
        object.__setattr__(self, "output_raw_dir", Path(self.output_raw_dir))
        object.__setattr__(self, "collection_timestamp", timestamp)
        object.__setattr__(self, "collection_id", collection_id)
        object.__setattr__(self, "resume", self.resume or option_resume)
        object.__setattr__(self, "source_options", options)


@dataclass(frozen=True, slots=True)
class PlannedSource:
    contract: SourceContract
    input_path: Path | None = None
    materializer: Materializer | None = None
    row_counter: RowCounter | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if (self.input_path is None) == (self.materializer is None):
            raise ValueError("planned source requires exactly one input or materializer")


@dataclass(frozen=True, slots=True)
class CollectionPlan:
    sources: tuple[PlannedSource, ...] = field(default_factory=tuple)
    blockers: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)


class CollectionAdapter(Protocol):
    sport: str
    required_sources: tuple[str, ...]

    def source_contracts(self) -> tuple[SourceContract, ...]: ...

    def build_plan(self, request: CollectionRequest) -> CollectionPlan: ...


@dataclass(frozen=True, slots=True)
class CollectionResult:
    collection_dir: Path
    dry_run: bool
    manifest: CollectionManifest | None
    planned_sources: tuple[str, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]


def _collection_dir(request: CollectionRequest) -> tuple[Path, Path]:
    root = validate_output_root(request.output_raw_dir)
    destination = root / request.sport / str(request.season) / str(request.collection_id)
    ensure_within_output_root(destination, root)
    return root, destination


def _source_files(source: Path) -> tuple[tuple[Path, Path], ...]:
    if source.is_file():
        return ((source, Path(source.name)),)
    files = tuple(
        (path, path.relative_to(source))
        for path in sorted(source.rglob("*"))
        if path.is_file()
    )
    if not files:
        raise CollectionError(f"supplied source directory contains no files: {source}")
    return files


def _validate_plan(plan: CollectionPlan) -> None:
    seen: set[str] = set()
    for planned in plan.sources:
        name = planned.contract.source_name
        if name in seen:
            raise CollectionError(f"source is planned more than once: {name}")
        seen.add(name)
        if planned.input_path is not None:
            reject_disallowed_source(str(planned.input_path))
            planned.contract.validate_input_path(planned.input_path)


def _record_file(
    *,
    file_path: Path,
    collection_dir: Path,
    planned: PlannedSource,
    timestamp: datetime,
) -> ManifestSource:
    return ManifestSource(
        source_name=planned.contract.source_name,
        source_type=planned.contract.source_type,
        source_url_provider=planned.contract.source_url_provider,
        license_terms_note=planned.contract.license_terms_note,
        local_file_path=file_path.relative_to(collection_dir).as_posix(),
        sha256=sha256_file(file_path),
        file_size=file_path.stat().st_size,
        row_count=(
            planned.row_counter(file_path)
            if planned.row_counter is not None
            else row_count_for_file(file_path)
        ),
        collection_timestamp=timestamp.isoformat(),
        warnings=planned.warnings,
    )


def _materialize_source(
    planned: PlannedSource,
    collection_dir: Path,
    output_root: Path,
    *,
    resume: bool = False,
) -> tuple[Path, ...]:
    source_dir = collection_dir / "sources" / planned.contract.source_name
    ensure_within_output_root(source_dir, output_root)
    source_dir.mkdir(parents=True, exist_ok=resume)
    if planned.input_path is not None:
        copied: list[Path] = []
        for source_file, relative_path in _source_files(
            planned.contract.validate_input_path(planned.input_path)
        ):
            destination = source_dir / relative_path
            ensure_within_output_root(destination, output_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if not resume:
                    raise FileExistsError(destination)
                if sha256_file(destination) != sha256_file(source_file):
                    raise CollectionError(
                        f"resumed supplied source differs from checkpoint: {destination}"
                    )
                copied.append(destination)
                continue
            with source_file.open("rb") as source_handle, destination.open("xb") as output:
                shutil.copyfileobj(source_handle, output)
            copied.append(destination)
        return tuple(copied)
    assert planned.materializer is not None
    existing_files = tuple(path for path in sorted(source_dir.rglob("*")) if path.is_file())
    # Resumable materializers own their checkpoint directory and must be
    # invoked again. Other completed materializers can safely reuse their
    # immutable files when a later source caused the prior invocation to fail.
    has_collection_state = any(path.name == "collection_state.json" for path in existing_files)
    files = (
        tuple(planned.materializer(source_dir))
        if not (resume and existing_files and not has_collection_state)
        else existing_files
    )
    if not files:
        raise CollectionError(
            f"source materializer produced no files: {planned.contract.source_name}"
        )
    for path in files:
        resolved = ensure_within_output_root(path, output_root)
        if not resolved.is_file():
            raise CollectionError(f"source materializer did not create a file: {resolved}")
    return files


def collect_sources(
    request: CollectionRequest, adapter: CollectionAdapter | None = None
) -> CollectionResult:
    """Plan or create a new immutable raw collection folder."""

    if adapter is None:
        from courtvision.data_collection.registry import get_collection_adapter

        adapter = get_collection_adapter(request.sport)
    if adapter.sport != request.sport:
        raise CollectionError(
            f"adapter sport {adapter.sport!r} does not match request {request.sport!r}"
        )
    output_root, destination = _collection_dir(request)
    destination_preexisted = destination.exists()
    if destination_preexisted:
        if not request.resume:
            raise FileExistsError(f"collection folder already exists: {destination}")
        if not destination.is_dir():
            raise FileExistsError(f"collection path is not a folder: {destination}")
        manifest_path = destination / "collection_manifest.json"
        if manifest_path.exists():
            raise FileExistsError(
                f"completed collection is immutable and cannot be resumed: {destination}"
            )
        if not any(destination.rglob("collection_state.json")):
            raise FileNotFoundError(
                f"cannot resume collection without a checkpoint: {destination}"
            )
    elif request.resume:
        raise FileNotFoundError(
            f"cannot resume collection because its folder does not exist: {destination}"
        )
    plan = adapter.build_plan(request)
    _validate_plan(plan)
    source_names = tuple(item.contract.source_name for item in plan.sources)
    if request.dry_run:
        return CollectionResult(
            collection_dir=destination,
            dry_run=True,
            manifest=None,
            planned_sources=source_names,
            blockers=plan.blockers,
            warnings=plan.warnings,
        )

    destination.mkdir(parents=True, exist_ok=request.resume)
    try:
        records: list[ManifestSource] = []
        for planned in plan.sources:
            for file_path in _materialize_source(
                planned,
                destination,
                output_root,
                resume=request.resume,
            ):
                records.append(
                    _record_file(
                        file_path=file_path,
                        collection_dir=destination,
                        planned=planned,
                        timestamp=request.collection_timestamp,
                    )
                )
        manifest = CollectionManifest(
            sport=request.sport,
            season=request.season,
            start_date=request.start_date,
            end_date=request.end_date,
            collection_timestamp=request.collection_timestamp,
            collector_version=COLLECTOR_VERSION,
            collection_id=str(request.collection_id),
            sources=tuple(records),
            blockers=plan.blockers,
            warnings=plan.warnings,
        )
        write_collection_manifest(manifest, destination)
    except Exception:
        # Preserve a valid resumable checkpoint. All other failed new
        # collections retain the v1 cleanup behavior.
        has_checkpoint = destination.exists() and any(
            path.name == "collection_state.json"
            for path in destination.rglob("collection_state.json")
        )
        if not destination_preexisted and not has_checkpoint:
            shutil.rmtree(destination, ignore_errors=True)
        raise
    return CollectionResult(
        collection_dir=destination,
        dry_run=False,
        manifest=manifest,
        planned_sources=source_names,
        blockers=plan.blockers,
        warnings=plan.warnings,
    )


__all__ = [
    "CollectionAdapter",
    "CollectionError",
    "CollectionPlan",
    "CollectionRequest",
    "CollectionResult",
    "PlannedSource",
    "UnsupportedSportCollectionError",
    "collect_sources",
]
