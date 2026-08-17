"""Immutable research-only authority records for prospective materializations."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Final, Mapping, Sequence

from courtvision.sports.mlb.data.prospective_context_acquisition import (
    DEFAULT_ACQUISITION_ROOT,
    ImmutableCaptureConflictError,
    ProspectiveAcquisitionError,
)


AUTHORITY_SCHEMA_VERSION: Final = "mlb-hr-materialization-authority-v1"
DEFAULT_AUTHORITY_ROOT: Final = DEFAULT_ACQUISITION_ROOT / "authorities"


@dataclass(frozen=True, slots=True)
class MaterializationAuthorityResult:
    authority_id: str
    authority_path: Path
    authoritative_materialization_id: str
    superseded_materialization_ids: tuple[str, ...]
    no_op: bool


def _canonical_json(value: object, *, pretty: bool = False) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=None if pretty else (",", ":"),
        indent=2 if pretty else None,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8") + (b"\n" if pretty else b"")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _value_digest(value: object) -> str:
    return _sha256(_canonical_json(value))


def _load_materialization(
    manifest_path: str | Path,
    *,
    operating_date: date,
    event_ids: tuple[str, ...],
) -> dict[str, object]:
    source = Path(manifest_path).expanduser().resolve()
    try:
        raw = source.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProspectiveAcquisitionError("materialization manifest is invalid") from exc
    if not isinstance(payload, dict):
        raise ProspectiveAcquisitionError("materialization manifest must be an object")
    digest = payload.get("manifest_digest")
    unsigned = dict(payload)
    unsigned.pop("manifest_digest", None)
    if digest != _value_digest(unsigned):
        raise ProspectiveAcquisitionError("materialization manifest digest mismatch")
    if payload.get("operating_date") != operating_date.isoformat():
        raise ProspectiveAcquisitionError("materialization operating date mismatch")
    materialization_id = str(payload.get("materialization_id") or "")
    if not materialization_id.startswith("materialization-"):
        raise ProspectiveAcquisitionError("materialization id is invalid")
    schedule_path = source.parent / "schedule.csv"
    try:
        with schedule_path.open("r", encoding="utf-8-sig", newline="") as handle:
            observed_ids = {
                str(row.get("event_id") or "").strip() for row in csv.DictReader(handle)
            }
    except OSError as exc:
        raise ProspectiveAcquisitionError("materialization schedule is unavailable") from exc
    if observed_ids != set(event_ids):
        raise ProspectiveAcquisitionError("materialization event set mismatch")
    return {
        "materialization_id": materialization_id,
        "manifest_path": str(source),
        "manifest_digest": str(digest),
        "manifest_sha256": _sha256(raw),
    }


def _authority_scope(
    root: str | Path, *, operating_date: date, event_ids: tuple[str, ...]
) -> Path:
    event_set_digest = _value_digest(
        {
            "operating_date": operating_date.isoformat(),
            "event_ids": list(event_ids),
        }
    )
    return (
        Path(root).expanduser().resolve()
        / operating_date.isoformat()
        / ("events-" + event_set_digest[:20])
    )


def _validate_authority(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ImmutableCaptureConflictError("authority record is invalid") from exc
    if not isinstance(payload, dict):
        raise ImmutableCaptureConflictError("authority record must be an object")
    digest = payload.get("manifest_digest")
    unsigned = dict(payload)
    unsigned.pop("manifest_digest", None)
    if digest != _value_digest(unsigned):
        raise ImmutableCaptureConflictError("authority record digest mismatch")
    record_id = str(payload.get("authority_id") or "")
    identity = dict(unsigned)
    identity.pop("authority_id", None)
    if record_id != "authority-" + _value_digest(identity):
        raise ImmutableCaptureConflictError("authority record id mismatch")
    authoritative = payload.get("authoritative")
    if not isinstance(authoritative, Mapping):
        raise ImmutableCaptureConflictError("authority target is missing")
    manifest_path = Path(str(authoritative.get("manifest_path") or "")).resolve()
    if not manifest_path.is_file():
        raise ImmutableCaptureConflictError("authority target manifest is missing")
    raw = manifest_path.read_bytes()
    if _sha256(raw) != authoritative.get("manifest_sha256"):
        raise ImmutableCaptureConflictError("authority target manifest changed")
    return payload


def publish_materialization_authority(
    *,
    operating_date: date,
    event_ids: Sequence[str],
    authoritative_manifest_path: str | Path,
    superseded_manifest_paths: Sequence[str | Path] = (),
    authority_root: str | Path = DEFAULT_AUTHORITY_ROOT,
) -> MaterializationAuthorityResult:
    ordered_events = tuple(sorted({str(value).strip() for value in event_ids}, key=int))
    if not ordered_events or any(not value.isdigit() for value in ordered_events):
        raise ProspectiveAcquisitionError("authority requires canonical event ids")
    authoritative = _load_materialization(
        authoritative_manifest_path,
        operating_date=operating_date,
        event_ids=ordered_events,
    )
    superseded = [
        _load_materialization(
            path, operating_date=operating_date, event_ids=ordered_events
        )
        for path in superseded_manifest_paths
    ]
    superseded.sort(key=lambda item: str(item["materialization_id"]))
    superseded_ids = tuple(str(item["materialization_id"]) for item in superseded)
    if str(authoritative["materialization_id"]) in superseded_ids:
        raise ProspectiveAcquisitionError(
            "authoritative materialization cannot also be superseded"
        )
    if len(superseded_ids) != len(set(superseded_ids)):
        raise ProspectiveAcquisitionError("duplicate superseded materialization")
    identity: dict[str, object] = {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "operating_date": operating_date.isoformat(),
        "event_ids": list(ordered_events),
        "authoritative": authoritative,
        "superseded": superseded,
        "research_only": True,
        "underlying_artifacts_mutated": False,
    }
    authority_id = "authority-" + _value_digest(identity)
    payload = {**identity, "authority_id": authority_id}
    payload["manifest_digest"] = _value_digest(payload)
    scope = _authority_scope(
        authority_root, operating_date=operating_date, event_ids=ordered_events
    )
    destination = scope / ("a-" + authority_id.removeprefix("authority-")[:20] + ".json")
    encoded = _canonical_json(payload, pretty=True)
    if destination.exists():
        existing = _validate_authority(destination)
        if existing != payload:
            raise ImmutableCaptureConflictError("authority record conflicts with content")
        return MaterializationAuthorityResult(
            authority_id=authority_id,
            authority_path=destination,
            authoritative_materialization_id=str(authoritative["materialization_id"]),
            superseded_materialization_ids=superseded_ids,
            no_op=True,
        )
    if scope.exists():
        existing_records = [
            _validate_authority(path) for path in sorted(scope.glob("a-*.json"))
        ]
        conflicts = {
            str(record.get("authoritative", {}).get("materialization_id"))
            for record in existing_records
            if isinstance(record.get("authoritative"), Mapping)
        }
        if conflicts and conflicts != {str(authoritative["materialization_id"])}:
            raise ImmutableCaptureConflictError(
                "conflicting authority already exists for operating date/event set"
            )
    scope.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".t-", dir=scope))
    try:
        temp_path = temporary / destination.name
        temp_path.write_bytes(encoded)
        temp_path.replace(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    _validate_authority(destination)
    return MaterializationAuthorityResult(
        authority_id=authority_id,
        authority_path=destination,
        authoritative_materialization_id=str(authoritative["materialization_id"]),
        superseded_materialization_ids=superseded_ids,
        no_op=False,
    )


def resolve_materialization_authority(
    *,
    operating_date: date,
    event_ids: Sequence[str],
    authority_root: str | Path = DEFAULT_AUTHORITY_ROOT,
) -> MaterializationAuthorityResult:
    ordered_events = tuple(sorted({str(value).strip() for value in event_ids}, key=int))
    scope = _authority_scope(
        authority_root, operating_date=operating_date, event_ids=ordered_events
    )
    records = [_validate_authority(path) for path in sorted(scope.glob("a-*.json"))]
    if not records:
        raise ProspectiveAcquisitionError("no authority record exists for event set")
    targets = {
        (
            str(record["authoritative"]["materialization_id"]),
            str(record["authoritative"]["manifest_digest"]),
        )
        for record in records
        if isinstance(record.get("authoritative"), Mapping)
    }
    if len(targets) != 1:
        raise ImmutableCaptureConflictError(
            "conflicting authority records exist for operating date/event set"
        )
    selected = records[0]
    authoritative_id = str(selected["authoritative"]["materialization_id"])
    superseded_ids = tuple(
        sorted(str(item["materialization_id"]) for item in selected.get("superseded") or [])
    )
    path = next(
        item
        for item in sorted(scope.glob("a-*.json"))
        if _validate_authority(item).get("authority_id") == selected.get("authority_id")
    )
    return MaterializationAuthorityResult(
        authority_id=str(selected["authority_id"]),
        authority_path=path,
        authoritative_materialization_id=authoritative_id,
        superseded_materialization_ids=superseded_ids,
        no_op=True,
    )


__all__ = [
    "AUTHORITY_SCHEMA_VERSION",
    "DEFAULT_AUTHORITY_ROOT",
    "MaterializationAuthorityResult",
    "publish_materialization_authority",
    "resolve_materialization_authority",
]
