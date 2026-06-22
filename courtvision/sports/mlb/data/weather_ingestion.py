"""Controlled local historical-weather ingestion for MLB HR research.

This prototype reads caller-supplied CSV files only. It performs no network
access, downloads, joins, feature generation, scoring, or runtime promotion.
Every normalized row remains explicitly historical and research-only.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import shutil
from typing import Final, Mapping, Sequence

from courtvision.sports.mlb.data_manifest import (
    MLBDataDomain,
    MLBSourceFileRecord,
    MLBSourceManifest,
    compute_file_sha256,
    validate_source_manifest,
    write_manifest,
)
from courtvision.sports.mlb.research_context import MLBWeatherContext


WEATHER_SCHEMA_VERSION: Final = "1.0"
WEATHER_DEFAULT_SOURCE_NAME: Final = "weather_historical_fixture"
WEATHER_SOURCE_TYPES: Final = frozenset(
    {"historical", "public", "manual", "sample"}
)
REQUIRED_WEATHER_COLUMNS: Final = frozenset(
    {
        "game_date",
        "venue_name",
        "temperature",
        "wind_speed",
        "wind_direction",
        "source_name",
        "source_type",
        "collected_at",
    }
)


class WeatherIngestionError(ValueError):
    """Raised when a local weather CSV violates the narrow contract."""


@dataclass(frozen=True, slots=True)
class MLBWeatherObservationRow:
    """Stable historical weather observation for MLB HR research."""

    sport: str
    league: str
    source: str
    game_id: str | None
    game_date: date
    event_start_time: datetime | None
    venue_name: str
    latitude: float | None
    longitude: float | None
    temperature: float | None
    wind_speed: float | None
    wind_direction: str | None
    wind_out_to_field: str | None
    humidity: float | None
    precipitation: float | None
    roof_status: str | None
    source_type: str
    as_of_date: date | None
    collected_at: datetime
    raw_row_hash: str
    data_quality: str
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WeatherIngestionResult:
    """Normalized rows, validated provenance, and explicit output paths."""

    rows: tuple[MLBWeatherObservationRow, ...]
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
        raise WeatherIngestionError(
            f"row {row_number}: {field_name} must not be empty"
        )
    return text


def _parse_row_date(value: object, field_name: str, row_number: int) -> date:
    text = _required_text(value, field_name, row_number)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise WeatherIngestionError(
            f"row {row_number}: {field_name} must be an ISO date "
            f"(YYYY-MM-DD): {text!r}"
        ) from exc


def _optional_date(
    value: object, field_name: str, row_number: int
) -> date | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise WeatherIngestionError(
            f"row {row_number}: {field_name} must be an ISO date "
            f"(YYYY-MM-DD) or empty: {text!r}"
        ) from exc


def _parse_datetime_text(
    value: object,
    field_name: str,
    row_number: int,
    *,
    required: bool,
) -> datetime | None:
    text = _optional_text(value)
    if text is None:
        if required:
            raise WeatherIngestionError(
                f"row {row_number}: {field_name} must not be empty"
            )
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WeatherIngestionError(
            f"row {row_number}: {field_name} must be an ISO datetime: {text!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WeatherIngestionError(
            f"row {row_number}: {field_name} must include a UTC offset"
        )
    return parsed


def _optional_float(
    value: object, field_name: str, row_number: int
) -> float | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        parsed = float(text)
    except ValueError as exc:
        raise WeatherIngestionError(
            f"row {row_number}: {field_name} must be numeric or empty"
        ) from exc
    if not math.isfinite(parsed):
        raise WeatherIngestionError(
            f"row {row_number}: {field_name} must be finite or empty"
        )
    return parsed


def _source_type(value: object, row_number: int) -> str:
    source_type = _required_text(value, "source_type", row_number).lower()
    if source_type not in WEATHER_SOURCE_TYPES:
        supported = ", ".join(sorted(WEATHER_SOURCE_TYPES))
        raise WeatherIngestionError(
            f"row {row_number}: source_type must be one of: {supported}"
        )
    return source_type


def _raw_row_hash(row: Mapping[str, object]) -> str:
    payload = json.dumps(
        dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _quality_and_warnings(
    *,
    game_id: str | None,
    temperature: float | None,
    wind_speed: float | None,
    wind_direction: str | None,
) -> tuple[str, tuple[str, ...]]:
    warnings = ["Historical weather observation; not a pregame forecast."]
    missing: list[str] = []
    if game_id is None:
        warnings.append("game_id is missing; venue/date matching is required later.")
    if temperature is None:
        missing.append("temperature")
    if wind_speed is None:
        missing.append("wind_speed")
    if wind_direction is None:
        missing.append("wind_direction")
    warnings.extend(f"Missing weather field: {field}." for field in missing)
    quality = "incomplete_historical" if missing else "complete_historical"
    return quality, tuple(warnings)


def _normalize_row(
    raw_row: Mapping[str, object], *, row_number: int
) -> MLBWeatherObservationRow:
    game_date = _parse_row_date(raw_row.get("game_date"), "game_date", row_number)
    as_of_date = _optional_date(raw_row.get("as_of_date"), "as_of_date", row_number)
    if as_of_date is not None and as_of_date < game_date:
        raise WeatherIngestionError(
            f"row {row_number}: as_of_date must not be before game_date"
        )
    collected_at = _parse_datetime_text(
        raw_row.get("collected_at"), "collected_at", row_number, required=True
    )
    assert collected_at is not None
    game_id = _optional_text(raw_row.get("game_id"))
    temperature = _optional_float(
        raw_row.get("temperature"), "temperature", row_number
    )
    wind_speed = _optional_float(
        raw_row.get("wind_speed"), "wind_speed", row_number
    )
    wind_direction = _optional_text(raw_row.get("wind_direction"))
    data_quality, warnings = _quality_and_warnings(
        game_id=game_id,
        temperature=temperature,
        wind_speed=wind_speed,
        wind_direction=wind_direction,
    )
    return MLBWeatherObservationRow(
        sport="MLB",
        league="MLB",
        source=_required_text(raw_row.get("source_name"), "source_name", row_number),
        game_id=game_id,
        game_date=game_date,
        event_start_time=_parse_datetime_text(
            raw_row.get("event_start_time"),
            "event_start_time",
            row_number,
            required=False,
        ),
        venue_name=_required_text(
            raw_row.get("venue_name"), "venue_name", row_number
        ),
        latitude=_optional_float(raw_row.get("latitude"), "latitude", row_number),
        longitude=_optional_float(
            raw_row.get("longitude"), "longitude", row_number
        ),
        temperature=temperature,
        wind_speed=wind_speed,
        wind_direction=wind_direction,
        wind_out_to_field=_optional_text(raw_row.get("wind_out_to_field")),
        humidity=_optional_float(raw_row.get("humidity"), "humidity", row_number),
        precipitation=_optional_float(
            raw_row.get("precipitation"), "precipitation", row_number
        ),
        roof_status=_optional_text(raw_row.get("roof_status")),
        source_type=_source_type(raw_row.get("source_type"), row_number),
        as_of_date=as_of_date,
        collected_at=collected_at,
        raw_row_hash=_raw_row_hash(raw_row),
        data_quality=data_quality,
        warnings=warnings,
    )


def weather_row_to_dict(row: MLBWeatherObservationRow) -> dict[str, object]:
    """Serialize a normalized row with stable names and temporal values."""

    return {
        "as_of_date": row.as_of_date.isoformat() if row.as_of_date else None,
        "collected_at": row.collected_at.isoformat(),
        "data_quality": row.data_quality,
        "event_start_time": (
            row.event_start_time.isoformat() if row.event_start_time else None
        ),
        "game_date": row.game_date.isoformat(),
        "game_id": row.game_id,
        "humidity": row.humidity,
        "latitude": row.latitude,
        "league": row.league,
        "longitude": row.longitude,
        "precipitation": row.precipitation,
        "raw_row_hash": row.raw_row_hash,
        "roof_status": row.roof_status,
        "source": row.source,
        "source_type": row.source_type,
        "sport": row.sport,
        "temperature": row.temperature,
        "venue_name": row.venue_name,
        "warnings": list(row.warnings),
        "wind_direction": row.wind_direction,
        "wind_out_to_field": row.wind_out_to_field,
        "wind_speed": row.wind_speed,
    }


def weather_row_to_json(row: MLBWeatherObservationRow) -> str:
    """Return deterministic compact JSON for one weather row."""

    return json.dumps(
        weather_row_to_dict(row),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def weather_row_to_context(row: MLBWeatherObservationRow) -> MLBWeatherContext:
    """Map one historical row into the existing research-only context."""

    if not isinstance(row, MLBWeatherObservationRow):
        raise TypeError("row must be an MLBWeatherObservationRow")
    return MLBWeatherContext(
        game_id=row.game_id or "",
        venue_name=row.venue_name,
        temperature=row.temperature,
        wind_speed=row.wind_speed,
        wind_direction=row.wind_direction,
        wind_out_to_field=row.wind_out_to_field,
        humidity=row.humidity,
        roof_status=row.roof_status,
        source_type=row.source_type,
        collected_at=row.collected_at,
        data_quality=row.data_quality,
        warnings=row.warnings,
    )


def _read_local_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise WeatherIngestionError("Weather CSV is missing a header row")
            missing = sorted(REQUIRED_WEATHER_COLUMNS - set(reader.fieldnames))
            if missing:
                raise WeatherIngestionError(
                    "Weather CSV is missing required columns: " + ", ".join(missing)
                )
            rows = []
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise WeatherIngestionError(
                        f"row {row_number}: Weather CSV has extra values"
                    )
                rows.append(dict(row))
    except WeatherIngestionError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise WeatherIngestionError(
            f"could not read Weather CSV {path}: {exc}"
        ) from exc
    if not rows:
        raise WeatherIngestionError("Weather CSV contains no data rows")
    return rows


def _output_paths(
    output_dir: Path, start: date, end: date
) -> tuple[Path, Path, Path]:
    stem = f"weather_{start.isoformat()}_{end.isoformat()}"
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


def ingest_local_weather_csv(
    input_csv: str | Path,
    *,
    output_dir: str | Path | None = None,
    write_raw: bool = False,
    write_normalized: bool = False,
    write_manifest_file: bool = False,
    overwrite: bool = False,
) -> WeatherIngestionResult:
    """Parse a local weather CSV and write only explicitly requested files."""

    source_path = Path(input_csv).expanduser().resolve()
    if not source_path.is_file():
        raise WeatherIngestionError(
            f"Weather input CSV does not exist: {source_path}"
        )
    raw_rows = _read_local_rows(source_path)
    rows = tuple(
        _normalize_row(raw_row, row_number=row_number)
        for row_number, raw_row in enumerate(raw_rows, start=2)
    )
    source_names = {row.source for row in rows}
    if len(source_names) != 1:
        raise WeatherIngestionError(
            "all weather rows must use the same source_name for one manifest"
        )
    source_types = {row.source_type for row in rows}
    if len(source_types) != 1:
        raise WeatherIngestionError(
            "all weather rows must use the same source_type for one manifest"
        )

    start = min(row.game_date for row in rows)
    end = max(row.game_date for row in rows)
    manifest_collected_at = max(row.collected_at for row in rows)
    as_of_dates = [row.as_of_date for row in rows if row.as_of_date is not None]
    manifest_as_of_date = max(as_of_dates) if as_of_dates else None
    any_write = write_raw or write_normalized or write_manifest_file
    if any_write and output_dir is None:
        raise WeatherIngestionError(
            "output_dir is required when any write is requested"
        )

    raw_output_path: Path | None = None
    normalized_output_path: Path | None = None
    manifest_output_path: Path | None = None
    destinations: list[Path] = []
    if output_dir is not None:
        output_root = Path(output_dir).expanduser().resolve()
        raw_candidate, normalized_candidate, manifest_candidate = _output_paths(
            output_root, start, end
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
    manifest = MLBSourceManifest(
        source_name=source_names.pop() or WEATHER_DEFAULT_SOURCE_NAME,
        source_type=source_types.pop(),
        data_domain=MLBDataDomain.WEATHER,
        collected_at=manifest_collected_at,
        raw_path=raw_output_path or source_path,
        schema_version=WEATHER_SCHEMA_VERSION,
        date_range_start=start,
        date_range_end=end,
        as_of_date=manifest_as_of_date,
        normalized_path=normalized_output_path,
        checksum=checksum,
        row_count=len(rows),
        file_count=1,
        generated_by="courtvision.sports.mlb.data.weather_ingestion",
        notes=("Local historical weather CSV ingestion prototype.",),
        warnings=(
            "Historical research use only; not a pregame forecast.",
            "No joins, weather features, training rows, or scoring were generated.",
        ),
        files=(
            MLBSourceFileRecord(
                path=raw_output_path or source_path,
                checksum=checksum,
                row_count=len(rows),
                byte_size=source_path.stat().st_size,
                content_type="text/csv",
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
                    handle.write(f"{weather_row_to_json(row)}\n")
        if manifest_output_path is not None:
            write_manifest(manifest, manifest_output_path, overwrite=overwrite)

    return WeatherIngestionResult(
        rows=rows,
        manifest=manifest,
        raw_output_path=raw_output_path,
        normalized_output_path=normalized_output_path,
        manifest_output_path=manifest_output_path,
    )


__all__ = [
    "MLBWeatherObservationRow",
    "REQUIRED_WEATHER_COLUMNS",
    "WEATHER_DEFAULT_SOURCE_NAME",
    "WEATHER_SCHEMA_VERSION",
    "WEATHER_SOURCE_TYPES",
    "WeatherIngestionError",
    "WeatherIngestionResult",
    "ingest_local_weather_csv",
    "weather_row_to_context",
    "weather_row_to_dict",
    "weather_row_to_json",
]
