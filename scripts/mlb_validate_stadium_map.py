"""Read-only validator for reviewed Retrosheet stadium mapping CSVs."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date
import math
from pathlib import Path
import sys
from typing import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.data_collection.core import CollectionError  # noqa: E402
from courtvision.sports.mlb.data_collection.weather_collector import (  # noqa: E402
    load_retrosheet_games,
)


REQUIRED_COLUMNS = (
    "park_id",
    "stadium_name",
    "latitude",
    "longitude",
    "timezone",
    "elevation_m",
)


class StadiumMapValidationError(ValueError):
    """Raised when a stadium map or its Retrosheet coverage is invalid."""


@dataclass(frozen=True, slots=True)
class StadiumMapValidationReport:
    stadium_map_path: Path
    game_log_path: Path
    stadium_count: int
    game_count: int
    covered_park_ids: tuple[str, ...]


def _finite_number(value: str, *, field: str, row_number: int) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise StadiumMapValidationError(
            f"row {row_number}: {field} must be numeric"
        ) from exc
    if not math.isfinite(number):
        raise StadiumMapValidationError(
            f"row {row_number}: {field} must be finite"
        )
    return number


def _load_map_park_ids(path: Path) -> set[str]:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise StadiumMapValidationError(
            f"stadium map must be a CSV file: {source}"
        )

    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise StadiumMapValidationError("stadium map has no header")

        normalized_headers = [
            header.strip().lower() for header in reader.fieldnames
        ]
        duplicate_headers = sorted(
            {
                header
                for header in normalized_headers
                if normalized_headers.count(header) > 1
            }
        )
        if duplicate_headers:
            raise StadiumMapValidationError(
                "stadium map has duplicate columns: " + ", ".join(duplicate_headers)
            )
        missing_columns = [
            column for column in REQUIRED_COLUMNS if column not in normalized_headers
        ]
        if missing_columns:
            raise StadiumMapValidationError(
                "stadium map is missing required columns: "
                + ", ".join(missing_columns)
            )

        park_ids: set[str] = set()
        for row_number, raw_row in enumerate(reader, start=2):
            row = {
                str(key).strip().lower(): "" if value is None else value.strip()
                for key, value in raw_row.items()
            }
            park_id = row["park_id"].upper()
            if not park_id:
                raise StadiumMapValidationError(
                    f"row {row_number}: park_id must not be blank"
                )
            if park_id in park_ids:
                raise StadiumMapValidationError(
                    f"row {row_number}: duplicate park_id {park_id!r}"
                )

            latitude = _finite_number(
                row["latitude"], field="latitude", row_number=row_number
            )
            longitude = _finite_number(
                row["longitude"], field="longitude", row_number=row_number
            )
            if not -90 <= latitude <= 90:
                raise StadiumMapValidationError(
                    f"row {row_number}: latitude must be between -90 and 90"
                )
            if not -180 <= longitude <= 180:
                raise StadiumMapValidationError(
                    f"row {row_number}: longitude must be between -180 and 180"
                )

            timezone_name = row["timezone"]
            if not timezone_name:
                raise StadiumMapValidationError(
                    f"row {row_number}: timezone must not be blank"
                )
            try:
                ZoneInfo(timezone_name)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise StadiumMapValidationError(
                    f"row {row_number}: timezone {timezone_name!r} "
                    "is not a valid IANA timezone"
                ) from exc

            if row["elevation_m"]:
                _finite_number(
                    row["elevation_m"], field="elevation_m", row_number=row_number
                )
            park_ids.add(park_id)

    if not park_ids:
        raise StadiumMapValidationError("stadium map has no data rows")
    return park_ids


def validate_stadium_map(
    stadium_map_path: str | Path, game_log_path: str | Path
) -> StadiumMapValidationReport:
    """Validate a reviewed map and coverage for every park in a game log."""

    stadium_map = Path(stadium_map_path).expanduser().resolve()
    game_log = Path(game_log_path).expanduser().resolve()
    park_ids = _load_map_park_ids(stadium_map)
    try:
        games = load_retrosheet_games(game_log, date.min, date.max)
    except CollectionError as exc:
        raise StadiumMapValidationError(str(exc)) from exc

    required_park_ids = {game.park_id.upper() for game in games}
    missing_park_ids = sorted(required_park_ids - park_ids)
    if missing_park_ids:
        raise StadiumMapValidationError(
            "stadium map is missing Retrosheet park IDs: "
            + ", ".join(missing_park_ids)
        )

    return StadiumMapValidationReport(
        stadium_map_path=stadium_map,
        game_log_path=game_log,
        stadium_count=len(park_ids),
        game_count=len(games),
        covered_park_ids=tuple(sorted(required_park_ids)),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a reviewed MLB stadium map and Retrosheet game-log coverage. "
            "Reads local files only and writes no outputs."
        )
    )
    parser.add_argument("--stadium-map", required=True, type=Path)
    parser.add_argument("--game-log", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = validate_stadium_map(args.stadium_map, args.game_log)
    except StadiumMapValidationError as exc:
        print(f"stadium map validation failed: {exc}", file=sys.stderr)
        return 2

    print(
        "stadium map valid: "
        f"{report.stadium_count} stadium(s), "
        f"{report.game_count} game(s), "
        f"{len(report.covered_park_ids)} covered Retrosheet park ID(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
