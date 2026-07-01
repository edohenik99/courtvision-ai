"""Validated, local-only MLB ballpark-factor collection artifacts."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Mapping

from courtvision.data_collection.core import CollectionError
from courtvision.data_collection.manifest import COLLECTOR_VERSION, sha256_file
from courtvision.data_collection.source_contracts import reject_disallowed_source


BALLPARK_FACTOR_SCHEMA_VERSION = "1.0"
NORMALIZED_BALLPARK_FACTORS_FILENAME = "normalized_ballpark_factors.csv"
VALIDATION_REPORT_FILENAME = "validation_report.json"
REQUIRED_BALLPARK_FACTOR_COLUMNS = (
    "season",
    "park_id",
    "stadium_name",
    "handedness",
    "hr_factor",
    "run_factor",
)
MIN_SUPPORTED_SEASON = 1876
MIN_FACTOR = Decimal("0.5")
MAX_FACTOR = Decimal("1.5")


class BallparkFactorCollectionError(CollectionError):
    """Raised when a supplied ballpark-factor CSV fails closed validation."""


@dataclass(frozen=True, slots=True)
class NormalizedBallparkFactor:
    season: int
    park_id: str
    stadium_name: str
    handedness: str
    hr_factor: Decimal
    run_factor: Decimal

    def as_csv_row(self) -> dict[str, object]:
        return {
            "season": self.season,
            "park_id": self.park_id,
            "stadium_name": self.stadium_name,
            "handedness": self.handedness,
            "hr_factor": _decimal_text(self.hr_factor),
            "run_factor": _decimal_text(self.run_factor),
        }


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text if "." in text else f"{text}.0"


def _normalized_headers(
    fieldnames: list[str] | None, *, label: str
) -> tuple[str, ...]:
    if fieldnames is None:
        raise BallparkFactorCollectionError(f"{label} has no header")
    headers = tuple(str(header).strip().lower() for header in fieldnames)
    duplicates = sorted({header for header in headers if headers.count(header) > 1})
    if duplicates:
        raise BallparkFactorCollectionError(
            f"{label} has duplicate columns: " + ", ".join(duplicates)
        )
    return headers


def _load_stadium_map(path: Path) -> dict[str, str]:
    if not path.is_file() or path.suffix.lower() != ".csv":
        raise BallparkFactorCollectionError(
            f"validated stadium map must be a CSV file: {path}"
        )
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = _normalized_headers(reader.fieldnames, label="stadium map")
        missing = [name for name in ("park_id", "stadium_name") if name not in headers]
        if missing:
            raise BallparkFactorCollectionError(
                "stadium map is missing required columns: " + ", ".join(missing)
            )
        stadiums: dict[str, str] = {}
        for row_number, raw_row in enumerate(reader, start=2):
            row = {
                str(key).strip().lower(): "" if value is None else value.strip()
                for key, value in raw_row.items()
                if key is not None
            }
            if not any(row.values()):
                continue
            park_id = row["park_id"].upper()
            stadium_name = row["stadium_name"]
            if not park_id:
                raise BallparkFactorCollectionError(
                    f"stadium map row {row_number}: park_id must not be blank"
                )
            if not stadium_name:
                raise BallparkFactorCollectionError(
                    f"stadium map row {row_number}: stadium_name must not be blank"
                )
            if park_id in stadiums:
                raise BallparkFactorCollectionError(
                    f"stadium map row {row_number}: duplicate park_id {park_id!r}"
                )
            stadiums[park_id] = stadium_name
    if not stadiums:
        raise BallparkFactorCollectionError("stadium map has no data rows")
    return stadiums


def _factor(value: str, *, field: str, row_number: int) -> Decimal:
    if not value:
        raise BallparkFactorCollectionError(
            f"row {row_number}: {field} must not be blank"
        )
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise BallparkFactorCollectionError(
            f"row {row_number}: {field} must be numeric"
        ) from exc
    if not number.is_finite():
        raise BallparkFactorCollectionError(
            f"row {row_number}: {field} must be finite"
        )
    if not MIN_FACTOR <= number <= MAX_FACTOR:
        raise BallparkFactorCollectionError(
            f"row {row_number}: {field} must be between {MIN_FACTOR} and {MAX_FACTOR}"
        )
    return number


def _season(value: str, *, row_number: int) -> int:
    try:
        season = int(value)
    except ValueError as exc:
        raise BallparkFactorCollectionError(
            f"row {row_number}: season must be an integer"
        ) from exc
    if str(season) != value.strip():
        raise BallparkFactorCollectionError(
            f"row {row_number}: season must be an integer"
        )
    current_season = date.today().year
    if not MIN_SUPPORTED_SEASON <= season <= current_season:
        raise BallparkFactorCollectionError(
            f"row {row_number}: unsupported season {season}; expected "
            f"{MIN_SUPPORTED_SEASON} through {current_season}"
        )
    return season


def _load_rows(
    source: Path,
    *,
    requested_season: int,
    stadiums: Mapping[str, str],
) -> tuple[NormalizedBallparkFactor, ...]:
    if not source.is_file() or source.suffix.lower() != ".csv":
        raise BallparkFactorCollectionError(
            f"ballpark factors source must be a supplied CSV file: {source}"
        )
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = _normalized_headers(reader.fieldnames, label="ballpark factors CSV")
        missing = [
            column for column in REQUIRED_BALLPARK_FACTOR_COLUMNS if column not in headers
        ]
        if missing:
            raise BallparkFactorCollectionError(
                "ballpark factors CSV is missing required columns: "
                + ", ".join(missing)
            )

        rows: list[NormalizedBallparkFactor] = []
        seen: set[tuple[int, str, str]] = set()
        for row_number, raw_row in enumerate(reader, start=2):
            row = {
                str(key).strip().lower(): "" if value is None else value.strip()
                for key, value in raw_row.items()
                if key is not None
            }
            if not any(row.values()):
                continue
            season = _season(row["season"], row_number=row_number)
            if season != requested_season:
                raise BallparkFactorCollectionError(
                    f"row {row_number}: season {season} does not match requested "
                    f"collection season {requested_season}"
                )
            park_id = row["park_id"].upper()
            if not park_id:
                raise BallparkFactorCollectionError(
                    f"row {row_number}: park_id must not be blank"
                )
            if park_id not in stadiums:
                raise BallparkFactorCollectionError(
                    f"row {row_number}: unknown park_id {park_id!r}; not present in "
                    "the validated stadium map"
                )
            stadium_name = row["stadium_name"]
            if not stadium_name:
                raise BallparkFactorCollectionError(
                    f"row {row_number}: stadium_name must not be blank"
                )
            handedness = row["handedness"].upper()
            if not handedness:
                raise BallparkFactorCollectionError(
                    f"row {row_number}: handedness must not be blank"
                )
            key = (season, park_id, handedness)
            if key in seen:
                raise BallparkFactorCollectionError(
                    "row "
                    f"{row_number}: duplicate park/season/handedness row for "
                    f"{park_id}/{season}/{handedness}"
                )
            seen.add(key)
            rows.append(
                NormalizedBallparkFactor(
                    season=season,
                    park_id=park_id,
                    stadium_name=stadium_name,
                    handedness=handedness,
                    hr_factor=_factor(
                        row["hr_factor"], field="hr_factor", row_number=row_number
                    ),
                    run_factor=_factor(
                        row["run_factor"], field="run_factor", row_number=row_number
                    ),
                )
            )
    if not rows:
        raise BallparkFactorCollectionError("ballpark factors CSV has no data rows")
    return tuple(sorted(rows, key=lambda item: (item.season, item.park_id, item.handedness)))


@dataclass(frozen=True, slots=True)
class BallparkFactorCollector:
    """Validated local CSV collector; it never downloads or scrapes data."""

    source_path: Path
    stadium_map_path: Path
    requested_season: int
    rows: tuple[NormalizedBallparkFactor, ...]
    stadium_count: int
    source_sha256: str
    stadium_map_sha256: str

    @classmethod
    def validate(
        cls,
        source_path: str | Path,
        stadium_map_path: str | Path,
        *,
        requested_season: int,
    ) -> BallparkFactorCollector:
        source = Path(source_path).expanduser().resolve()
        stadium_map = Path(stadium_map_path).expanduser().resolve()
        reject_disallowed_source(str(source))
        reject_disallowed_source(str(stadium_map))
        stadiums = _load_stadium_map(stadium_map)
        rows = _load_rows(
            source,
            requested_season=requested_season,
            stadiums=stadiums,
        )
        return cls(
            source_path=source,
            stadium_map_path=stadium_map,
            requested_season=requested_season,
            rows=rows,
            stadium_count=len(stadiums),
            source_sha256=sha256_file(source),
            stadium_map_sha256=sha256_file(stadium_map),
        )

    def materialize(self, destination: Path) -> tuple[Path, ...]:
        if sha256_file(self.source_path) != self.source_sha256:
            raise BallparkFactorCollectionError(
                "ballpark factors source changed after validation"
            )
        if sha256_file(self.stadium_map_path) != self.stadium_map_sha256:
            raise BallparkFactorCollectionError(
                "stadium map changed after ballpark-factor validation"
            )
        normalized = destination / NORMALIZED_BALLPARK_FACTORS_FILENAME
        report_path = destination / VALIDATION_REPORT_FILENAME
        with normalized.open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=REQUIRED_BALLPARK_FACTOR_COLUMNS,
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(row.as_csv_row() for row in self.rows)

        normalized_sha256 = sha256_file(normalized)
        report = {
            "artifact_type": "mlb_ballpark_factor_validation_report",
            "collector_version": COLLECTOR_VERSION,
            "normalized_output": {
                "filename": NORMALIZED_BALLPARK_FACTORS_FILENAME,
                "row_count": len(self.rows),
                "sha256": normalized_sha256,
            },
            "provenance": {
                "acquisition_method": "approved_supplied_csv",
                "network_accessed": False,
                "scraping_performed": False,
                "source_filename": self.source_path.name,
                "source_sha256": self.source_sha256,
                "stadium_map_filename": self.stadium_map_path.name,
                "stadium_map_sha256": self.stadium_map_sha256,
            },
            "requested_season": self.requested_season,
            "schema_version": BALLPARK_FACTOR_SCHEMA_VERSION,
            "source_row_count": len(self.rows),
            "stadium_map_row_count": self.stadium_count,
            "status": "valid",
            "validations": {
                "duplicate_park_season_handedness": "passed",
                "numeric_ranges": "passed",
                "park_ids_against_stadium_map": "passed",
                "required_columns": "passed",
                "seasons_against_request": "passed",
            },
        }
        try:
            with report_path.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(report, handle, indent=2, sort_keys=True)
                handle.write("\n")
        except Exception:
            normalized.unlink(missing_ok=True)
            raise
        return normalized, report_path

    def manifest_metadata(self, source_dir: Path) -> Mapping[str, object]:
        normalized = source_dir / NORMALIZED_BALLPARK_FACTORS_FILENAME
        report = source_dir / VALIDATION_REPORT_FILENAME
        return {
            "normalized_filename": normalized.name,
            "normalized_row_count": len(self.rows),
            "normalized_sha256": sha256_file(normalized),
            "provenance": {
                "acquisition_method": "approved_supplied_csv",
                "network_accessed": False,
                "scraping_performed": False,
                "stadium_map_filename": self.stadium_map_path.name,
                "stadium_map_sha256": self.stadium_map_sha256,
            },
            "requested_season": self.requested_season,
            "schema_version": BALLPARK_FACTOR_SCHEMA_VERSION,
            "source_filename": self.source_path.name,
            "source_row_count": len(self.rows),
            "source_sha256": self.source_sha256,
            "validation_report_filename": report.name,
            "validation_report_sha256": sha256_file(report),
        }


__all__ = [
    "BALLPARK_FACTOR_SCHEMA_VERSION",
    "MAX_FACTOR",
    "MIN_FACTOR",
    "MIN_SUPPORTED_SEASON",
    "NORMALIZED_BALLPARK_FACTORS_FILENAME",
    "REQUIRED_BALLPARK_FACTOR_COLUMNS",
    "VALIDATION_REPORT_FILENAME",
    "BallparkFactorCollectionError",
    "BallparkFactorCollector",
    "NormalizedBallparkFactor",
]
