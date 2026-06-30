"""Immutable collection manifest serialization and file evidence helpers."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from typing import Any


COLLECTOR_VERSION = "1.0.0"
MANIFEST_FILENAME = "collection_manifest.json"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_count_for_file(path: str | Path) -> int | None:
    """Return a row count for common record formats, otherwise ``None``."""

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            try:
                next(reader)
            except StopIteration:
                return 0
            return sum(1 for row in reader if row)
    if suffix in {".jsonl", ".ndjson"}:
        with source.open("r", encoding="utf-8-sig") as handle:
            return sum(1 for line in handle if line.strip())
    if suffix == ".json":
        try:
            payload: Any = json.loads(source.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return len(payload) if isinstance(payload, list) else None
    return None


@dataclass(frozen=True, slots=True)
class ManifestSource:
    source_name: str
    source_type: str
    source_url_provider: str
    license_terms_note: str
    local_file_path: str
    sha256: str
    file_size: int
    row_count: int | None
    collection_timestamp: str
    collector_version: str = COLLECTOR_VERSION
    blockers: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CollectionManifest:
    sport: str
    season: int
    start_date: date
    end_date: date
    collection_timestamp: datetime
    collector_version: str
    collection_id: str
    sources: tuple[ManifestSource, ...] = field(default_factory=tuple)
    blockers: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["start_date"] = self.start_date.isoformat()
        payload["end_date"] = self.end_date.isoformat()
        payload["date_range"] = {
            "start": self.start_date.isoformat(),
            "end": self.end_date.isoformat(),
        }
        payload["collection_timestamp"] = self.collection_timestamp.isoformat()
        payload["sources"] = [asdict(source) for source in self.sources]
        payload["blockers"] = list(self.blockers)
        payload["warnings"] = list(self.warnings)
        return payload


def write_collection_manifest(
    manifest: CollectionManifest, collection_dir: str | Path
) -> Path:
    """Write the sole manifest using exclusive creation; overwrite is forbidden."""

    destination = Path(collection_dir) / MANIFEST_FILENAME
    with destination.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")
    return destination


__all__ = [
    "COLLECTOR_VERSION",
    "MANIFEST_FILENAME",
    "CollectionManifest",
    "ManifestSource",
    "row_count_for_file",
    "sha256_file",
    "write_collection_manifest",
]
