"""Controlled local ballpark-factor ingestion for MLB HR research.

This prototype reads caller-supplied CSV files only. It performs no network
access, downloads, joins, feature generation, scoring, or runtime promotion.
Every normalized row remains explicitly static and research-only.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
from typing import Final, Mapping, Sequence
import unicodedata

from courtvision.sports.mlb.data_manifest import (
    MLBDataDomain,
    MLBSourceFileRecord,
    MLBSourceManifest,
    compute_file_sha256,
    validate_source_manifest,
    write_manifest,
)
from courtvision.sports.mlb.research_context import MLBBallparkContext


BALLPARK_SCHEMA_VERSION: Final = "1.0"
BALLPARK_DEFAULT_SOURCE_NAME: Final = "ballpark_factors_static_fixture"
BALLPARK_SOURCE_TYPES: Final = frozenset({"static", "manual", "sample"})
REQUIRED_BALLPARK_COLUMNS: Final = frozenset(
    {
        "venue_name",
        "park_factor_hr",
        "source_name",
        "source_type",
        "data_version",
        "collected_at",
    }
)
_NUMERIC_FIELDS: Final = (
    "park_factor_hr",
    "handedness_factor_lhb",
    "handedness_factor_rhb",
    "altitude",
    "left_field_distance",
    "center_field_distance",
    "right_field_distance",
)


class BallparkFactorError(ValueError):
    """Raised when a local ballpark CSV violates the narrow contract."""


@dataclass(frozen=True, slots=True)
class MLBBallparkFactorRow:
    """Stable static venue factors for MLB HR research."""

    sport: str
    league: str
    source: str
    venue_name: str
    team: str | None
    park_factor_hr: float | None
    handedness_factor_lhb: float | None
    handedness_factor_rhb: float | None
    altitude: float | None
    left_field_distance: float | None
    center_field_distance: float | None
    right_field_distance: float | None
    roof_type: str | None
    source_type: str
    data_version: str
    as_of_date: date | None
    collected_at: datetime
    raw_row_hash: str
    data_quality: str
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BallparkFactorIngestionResult:
    """Normalized rows, validated provenance, and explicit output paths."""

    rows: tuple[MLBBallparkFactorRow, ...]
    manifest: MLBSourceManifest
    raw_output_path: Path | None = None
    normalized_output_path: Path | None = None
    manifest_output_path: Path | None = None


def _optional_text(value: object) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _required_text(value: object, field_name: str, row_number: int) -> str:
    text = _optional_text(value)
    if text is None:
        raise BallparkFactorError(
            f"row {row_number}: {field_name} must not be empty"
        )
    return text


def _optional_float(
    value: object, field_name: str, row_number: int
) -> float | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        parsed = float(text)
    except ValueError as exc:
        raise BallparkFactorError(
            f"row {row_number}: {field_name} must be numeric or empty"
        ) from exc
    if not math.isfinite(parsed):
        raise BallparkFactorError(
            f"row {row_number}: {field_name} must be finite or empty"
        )
    return parsed


def _optional_date(
    value: object, field_name: str, row_number: int
) -> date | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise BallparkFactorError(
            f"row {row_number}: {field_name} must be an ISO date "
            f"(YYYY-MM-DD) or empty: {text!r}"
        ) from exc


def _required_datetime(
    value: object, field_name: str, row_number: int
) -> datetime:
    text = _required_text(value, field_name, row_number)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BallparkFactorError(
            f"row {row_number}: {field_name} must be an ISO datetime: {text!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BallparkFactorError(
            f"row {row_number}: {field_name} must include a UTC offset"
        )
    return parsed


def _source_type(value: object, row_number: int) -> str:
    source_type = _required_text(value, "source_type", row_number).casefold()
    if source_type not in BALLPARK_SOURCE_TYPES:
        supported = ", ".join(sorted(BALLPARK_SOURCE_TYPES))
        raise BallparkFactorError(
            f"row {row_number}: source_type must be one of: {supported}"
        )
    return source_type


def _raw_row_hash(row: Mapping[str, object]) -> str:
    payload = json.dumps(
        dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_venue_name(venue_name: str) -> str:
    """Return a deterministic comparison key without applying aliases."""

    if not isinstance(venue_name, str):
        raise TypeError("venue_name must be a string")
    text = unicodedata.normalize("NFKC", venue_name).strip().casefold()
    if not text:
        raise BallparkFactorError("venue_name must not be empty")
    text = text.replace("&", " and ")
    return re.sub(r"[\W_]+", " ", text, flags=re.UNICODE).strip()


def _quality_and_warnings(
    *,
    handedness_factor_lhb: float | None,
    handedness_factor_rhb: float | None,
    altitude: float | None,
    left_field_distance: float | None,
    center_field_distance: float | None,
    right_field_distance: float | None,
    roof_type: str | None,
) -> tuple[str, tuple[str, ...]]:
    warnings = ["Static ballpark factor for MLB HR research only."]
    missing: list[str] = []
    optional_values = {
        "handedness_factor_lhb": handedness_factor_lhb,
        "handedness_factor_rhb": handedness_factor_rhb,
        "altitude": altitude,
        "left_field_distance": left_field_distance,
        "center_field_distance": center_field_distance,
        "right_field_distance": right_field_distance,
        "roof_type": roof_type,
    }
    for field_name, value in optional_values.items():
        if value is None:
            missing.append(field_name)
    warnings.extend(f"Missing optional ballpark field: {name}." for name in missing)
    quality = "partial_static" if missing else "complete_static"
    return quality, tuple(warnings)


def _normalize_row(
    raw_row: Mapping[str, object], *, row_number: int
) -> MLBBallparkFactorRow:
    numeric = {
        field_name: _optional_float(raw_row.get(field_name), field_name, row_number)
        for field_name in _NUMERIC_FIELDS
    }
    roof_type = _optional_text(raw_row.get("roof_type"))
    data_quality, warnings = _quality_and_warnings(
        handedness_factor_lhb=numeric["handedness_factor_lhb"],
        handedness_factor_rhb=numeric["handedness_factor_rhb"],
        altitude=numeric["altitude"],
        left_field_distance=numeric["left_field_distance"],
        center_field_distance=numeric["center_field_distance"],
        right_field_distance=numeric["right_field_distance"],
        roof_type=roof_type,
    )
    return MLBBallparkFactorRow(
        sport="MLB",
        league="MLB",
        source=_required_text(raw_row.get("source_name"), "source_name", row_number),
        venue_name=_required_text(
            raw_row.get("venue_name"), "venue_name", row_number
        ),
        team=_optional_text(raw_row.get("team")),
        park_factor_hr=numeric["park_factor_hr"],
        handedness_factor_lhb=numeric["handedness_factor_lhb"],
        handedness_factor_rhb=numeric["handedness_factor_rhb"],
        altitude=numeric["altitude"],
        left_field_distance=numeric["left_field_distance"],
        center_field_distance=numeric["center_field_distance"],
        right_field_distance=numeric["right_field_distance"],
        roof_type=roof_type,
        source_type=_source_type(raw_row.get("source_type"), row_number),
        data_version=_required_text(
            raw_row.get("data_version"), "data_version", row_number
        ),
        as_of_date=_optional_date(raw_row.get("as_of_date"), "as_of_date", row_number),
        collected_at=_required_datetime(
            raw_row.get("collected_at"), "collected_at", row_number
        ),
        raw_row_hash=_raw_row_hash(raw_row),
        data_quality=data_quality,
        warnings=warnings,
    )


def _valid_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def validate_ballpark_factor_row(row: MLBBallparkFactorRow) -> None:
    """Fail clearly unless one normalized row is complete and internally safe."""

    if not isinstance(row, MLBBallparkFactorRow):
        raise TypeError("row must be an MLBBallparkFactorRow")
    if row.sport != "MLB" or row.league != "MLB":
        raise BallparkFactorError("ballpark row sport and league must be MLB")
    for field_name in ("source", "venue_name", "data_version", "data_quality"):
        value = getattr(row, field_name)
        if not isinstance(value, str) or not value.strip():
            raise BallparkFactorError(f"{field_name} must not be empty")
    normalize_venue_name(row.venue_name)
    if row.source_type not in BALLPARK_SOURCE_TYPES:
        raise BallparkFactorError(
            "source_type must be static, manual, or sample"
        )
    if row.park_factor_hr is None:
        raise BallparkFactorError("park_factor_hr is required for a complete row")
    numeric_values = {
        field_name: getattr(row, field_name) for field_name in _NUMERIC_FIELDS
    }
    for field_name, value in numeric_values.items():
        if value is not None and not _valid_number(value):
            raise BallparkFactorError(f"{field_name} must be a finite number or None")
    for field_name in (
        "park_factor_hr",
        "handedness_factor_lhb",
        "handedness_factor_rhb",
        "left_field_distance",
        "center_field_distance",
        "right_field_distance",
    ):
        value = getattr(row, field_name)
        if value is not None and value <= 0:
            raise BallparkFactorError(f"{field_name} must be greater than zero")
    if not isinstance(row.collected_at, datetime):
        raise BallparkFactorError("collected_at must be a datetime")
    if row.collected_at.tzinfo is None or row.collected_at.utcoffset() is None:
        raise BallparkFactorError("collected_at must include a UTC offset")


def validate_ballpark_factor_rows(
    rows: Sequence[MLBBallparkFactorRow],
) -> None:
    """Validate all rows and reject ambiguous normalized venue identities."""

    if not rows:
        raise BallparkFactorError("ballpark factor rows must not be empty")
    seen: dict[str, str] = {}
    for row in rows:
        validate_ballpark_factor_row(row)
        key = normalize_venue_name(row.venue_name)
        if key in seen:
            raise BallparkFactorError(
                "duplicate or ambiguous venue rows: "
                f"{seen[key]!r} and {row.venue_name!r} normalize to {key!r}"
            )
        seen[key] = row.venue_name


def _read_local_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise BallparkFactorError(
                    "Ballpark factor CSV is missing a header row"
                )
            missing = sorted(REQUIRED_BALLPARK_COLUMNS - set(reader.fieldnames))
            if missing:
                raise BallparkFactorError(
                    "Ballpark factor CSV is missing required columns: "
                    + ", ".join(missing)
                )
            rows = []
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise BallparkFactorError(
                        f"row {row_number}: Ballpark factor CSV has extra values"
                    )
                rows.append(dict(row))
    except BallparkFactorError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise BallparkFactorError(
            f"could not read Ballpark factor CSV {path}: {exc}"
        ) from exc
    if not rows:
        raise BallparkFactorError("Ballpark factor CSV contains no data rows")
    return rows


def load_ballpark_factor_rows(
    path: str | Path,
) -> tuple[MLBBallparkFactorRow, ...]:
    """Read and validate a caller-supplied local CSV."""

    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file():
        raise BallparkFactorError(
            f"Ballpark factor input CSV does not exist: {source_path}"
        )
    raw_rows = _read_local_rows(source_path)
    rows = tuple(
        _normalize_row(raw_row, row_number=row_number)
        for row_number, raw_row in enumerate(raw_rows, start=2)
    )
    validate_ballpark_factor_rows(rows)
    return rows


def find_ballpark_by_venue(
    rows: Sequence[MLBBallparkFactorRow], venue_name: str
) -> MLBBallparkFactorRow | None:
    """Return one deterministic venue match, or ``None`` when unknown."""

    validate_ballpark_factor_rows(rows)
    target = normalize_venue_name(venue_name)
    for row in rows:
        if normalize_venue_name(row.venue_name) == target:
            return row
    return None


def ballpark_row_to_dict(row: MLBBallparkFactorRow) -> dict[str, object]:
    """Serialize a normalized row with stable names and temporal values."""

    return {
        "altitude": row.altitude,
        "as_of_date": row.as_of_date.isoformat() if row.as_of_date else None,
        "center_field_distance": row.center_field_distance,
        "collected_at": row.collected_at.isoformat(),
        "data_quality": row.data_quality,
        "data_version": row.data_version,
        "handedness_factor_lhb": row.handedness_factor_lhb,
        "handedness_factor_rhb": row.handedness_factor_rhb,
        "league": row.league,
        "left_field_distance": row.left_field_distance,
        "park_factor_hr": row.park_factor_hr,
        "raw_row_hash": row.raw_row_hash,
        "right_field_distance": row.right_field_distance,
        "roof_type": row.roof_type,
        "source": row.source,
        "source_type": row.source_type,
        "sport": row.sport,
        "team": row.team,
        "venue_name": row.venue_name,
        "warnings": list(row.warnings),
    }


def ballpark_row_to_json(row: MLBBallparkFactorRow) -> str:
    """Return deterministic compact JSON for one ballpark row."""

    return json.dumps(
        ballpark_row_to_dict(row),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def ballpark_row_to_context(row: MLBBallparkFactorRow) -> MLBBallparkContext:
    """Map one static row into the existing research-only context."""

    if not isinstance(row, MLBBallparkFactorRow):
        raise TypeError("row must be an MLBBallparkFactorRow")
    handedness = {
        key: value
        for key, value in (
            ("LHB", row.handedness_factor_lhb),
            ("RHB", row.handedness_factor_rhb),
        )
        if value is not None
    }
    dimensions = {
        key: value
        for key, value in (
            ("LF", row.left_field_distance),
            ("CF", row.center_field_distance),
            ("RF", row.right_field_distance),
        )
        if value is not None
    }
    return MLBBallparkContext(
        venue_name=row.venue_name,
        park_factor_hr=row.park_factor_hr,
        source_type=row.source_type,
        data_version=row.data_version,
        data_quality=row.data_quality,
        handedness_factor=handedness or None,
        altitude=row.altitude,
        dimensions=dimensions or None,
        roof_type=row.roof_type,
        warnings=row.warnings,
    )


def _output_paths(output_dir: Path) -> tuple[Path, Path, Path]:
    stem = "ballpark_factors"
    return (
        output_dir / "raw" / f"{stem}.csv",
        output_dir / "normalized" / f"{stem}.jsonl",
        output_dir / "manifests" / f"{stem}.manifest.json",
    )


def _check_output_collisions(paths: Sequence[Path], overwrite: bool) -> None:
    if not overwrite:
        existing = [path for path in paths if path.exists()]
        if existing:
            raise FileExistsError(existing[0])


def ingest_local_ballpark_factors_csv(
    input_csv: str | Path,
    *,
    output_dir: str | Path | None = None,
    write_raw: bool = False,
    write_normalized: bool = False,
    write_manifest_file: bool = False,
    overwrite: bool = False,
) -> BallparkFactorIngestionResult:
    """Parse a local factor CSV and write only explicitly requested files."""

    source_path = Path(input_csv).expanduser().resolve()
    rows = load_ballpark_factor_rows(source_path)
    source_names = {row.source for row in rows}
    if len(source_names) != 1:
        raise BallparkFactorError(
            "all ballpark rows must use the same source_name for one manifest"
        )
    source_types = {row.source_type for row in rows}
    if len(source_types) != 1:
        raise BallparkFactorError(
            "all ballpark rows must use the same source_type for one manifest"
        )
    data_versions = {row.data_version for row in rows}
    if len(data_versions) != 1:
        raise BallparkFactorError(
            "all ballpark rows must use the same data_version for one manifest"
        )

    any_write = write_raw or write_normalized or write_manifest_file
    if any_write and output_dir is None:
        raise BallparkFactorError(
            "output_dir is required when any write is requested"
        )
    raw_output_path: Path | None = None
    normalized_output_path: Path | None = None
    manifest_output_path: Path | None = None
    destinations: list[Path] = []
    if output_dir is not None:
        output_root = Path(output_dir).expanduser().resolve()
        raw_candidate, normalized_candidate, manifest_candidate = _output_paths(
            output_root
        )
        if write_raw:
            raw_output_path = raw_candidate
            destinations.append(raw_candidate)
        if write_normalized:
            normalized_output_path = normalized_candidate
            destinations.append(normalized_candidate)
        if write_manifest_file:
            manifest_output_path = manifest_candidate
            destinations.append(manifest_candidate)
        _check_output_collisions(destinations, overwrite)

    checksum = compute_file_sha256(source_path)
    as_of_dates = [row.as_of_date for row in rows if row.as_of_date is not None]
    manifest = MLBSourceManifest(
        source_name=source_names.pop() or BALLPARK_DEFAULT_SOURCE_NAME,
        source_type=source_types.pop(),
        data_domain=MLBDataDomain.BALLPARK,
        collected_at=max(row.collected_at for row in rows),
        as_of_date=max(as_of_dates) if as_of_dates else None,
        raw_path=raw_output_path or source_path,
        normalized_path=normalized_output_path,
        schema_version=BALLPARK_SCHEMA_VERSION,
        source_version=data_versions.pop(),
        checksum=checksum,
        row_count=len(rows),
        file_count=1,
        generated_by="courtvision.sports.mlb.data.ballpark_factors",
        notes=("Local static ballpark-factor ingestion prototype.",),
        warnings=(
            "Static research data only; no runtime promotion is implied.",
            "No joins, HR features, training rows, or scoring were generated.",
        ),
        files=(
            MLBSourceFileRecord(
                path=raw_output_path or source_path,
                checksum=checksum,
                row_count=len(rows),
                byte_size=source_path.stat().st_size,
                content_type="text/csv",
                source_version=rows[0].data_version,
            ),
        ),
    )
    validate_source_manifest(manifest).raise_for_errors()

    if any_write:
        for destination in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
        if raw_output_path is not None:
            shutil.copyfile(source_path, raw_output_path)
        if normalized_output_path is not None:
            mode = "w" if overwrite else "x"
            with normalized_output_path.open(
                mode, encoding="utf-8", newline="\n"
            ) as handle:
                for row in rows:
                    handle.write(f"{ballpark_row_to_json(row)}\n")
        if manifest_output_path is not None:
            write_manifest(manifest, manifest_output_path, overwrite=overwrite)

    return BallparkFactorIngestionResult(
        rows=rows,
        manifest=manifest,
        raw_output_path=raw_output_path,
        normalized_output_path=normalized_output_path,
        manifest_output_path=manifest_output_path,
    )


__all__ = [
    "BALLPARK_DEFAULT_SOURCE_NAME",
    "BALLPARK_SCHEMA_VERSION",
    "BALLPARK_SOURCE_TYPES",
    "REQUIRED_BALLPARK_COLUMNS",
    "BallparkFactorError",
    "BallparkFactorIngestionResult",
    "MLBBallparkFactorRow",
    "ballpark_row_to_context",
    "ballpark_row_to_dict",
    "ballpark_row_to_json",
    "find_ballpark_by_venue",
    "ingest_local_ballpark_factors_csv",
    "load_ballpark_factor_rows",
    "normalize_venue_name",
    "validate_ballpark_factor_row",
    "validate_ballpark_factor_rows",
]
