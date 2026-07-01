"""Validate the collected live 1+ HR master CSV without network access."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = (
    PROJECT_ROOT
    / "data"
    / "theoddsapi"
    / "live_hr_snapshots"
    / "live_hr_props_master.csv"
)

REQUIRED_COLUMNS = (
    "snapshot_time",
    "event_id",
    "commence_time",
    "home_team",
    "away_team",
    "bookmaker_key",
    "bookmaker",
    "bookmaker_last_update",
    "market",
    "market_last_update",
    "player",
    "side",
    "price",
    "point",
    "hr_label",
)

IDENTITY_FIELDS = (
    "event_id",
    "bookmaker_key",
    "market",
    "player",
    "side",
    "point",
)


@dataclass(frozen=True)
class ValidationReport:
    path: Path
    row_count: int
    errors: tuple[str, ...]
    duplicate_count: int
    bookmaker_counts: dict[str, int]
    game_counts: dict[str, int]
    snapshot_date_counts: dict[str, int]

    @property
    def valid(self) -> bool:
        return not self.errors


def _is_blank(value: object) -> bool:
    return value is None or not str(value).strip()


def _snapshot_date(value: object) -> str | None:
    if _is_blank(value):
        return None

    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.date().isoformat()


def _empty_report(path: Path, error: str) -> ValidationReport:
    return ValidationReport(
        path=path,
        row_count=0,
        errors=(error,),
        duplicate_count=0,
        bookmaker_counts={},
        game_counts={},
        snapshot_date_counts={},
    )


def validate_live_hr_data(path: str | Path = DEFAULT_CSV) -> ValidationReport:
    """Read and validate a live HR CSV without changing it."""

    csv_path = Path(path)
    if not csv_path.is_file():
        return _empty_report(csv_path, f"file does not exist: {csv_path}")

    try:
        handle = csv_path.open("r", newline="", encoding="utf-8-sig")
    except OSError as exc:
        return _empty_report(csv_path, f"could not read file: {exc}")

    errors: list[str] = []
    bookmaker_counts: Counter[str] = Counter()
    game_counts: Counter[str] = Counter()
    snapshot_date_counts: Counter[str] = Counter()
    identities: Counter[tuple[str, ...]] = Counter()
    row_count = 0

    with handle:
        reader = csv.DictReader(handle)
        actual_columns = tuple(reader.fieldnames or ())
        missing_columns = [
            column for column in REQUIRED_COLUMNS if column not in actual_columns
        ]
        unexpected_columns = [
            column for column in actual_columns if column not in REQUIRED_COLUMNS
        ]

        if missing_columns:
            errors.append("missing required columns: " + ", ".join(missing_columns))
        if unexpected_columns:
            errors.append("unexpected columns: " + ", ".join(unexpected_columns))
        if not missing_columns and actual_columns != REQUIRED_COLUMNS:
            errors.append("columns are not in the required order")

        # Row checks depend on the complete schema. Avoid manufacturing misleading
        # row-level failures when a required column is absent.
        if not missing_columns:
            missing_players: list[int] = []
            invalid_sides: list[int] = []
            invalid_points: list[int] = []
            invalid_labels: list[int] = []
            invalid_snapshot_times: list[int] = []
            blank_identity_rows: dict[str, list[int]] = {
                field: [] for field in IDENTITY_FIELDS
            }

            for row_number, row in enumerate(reader, start=2):
                row_count += 1

                if None in row:
                    errors.append(f"row {row_number} has more values than the schema")

                if _is_blank(row.get("player")):
                    missing_players.append(row_number)
                if str(row.get("side", "")).strip() != "Over":
                    invalid_sides.append(row_number)

                point = str(row.get("point", "")).strip()
                try:
                    point_is_half = float(point) == 0.5
                except ValueError:
                    point_is_half = False
                if not point_is_half:
                    invalid_points.append(row_number)

                if str(row.get("hr_label", "")).strip() != "1+ HR":
                    invalid_labels.append(row_number)

                for field in IDENTITY_FIELDS:
                    if _is_blank(row.get(field)):
                        blank_identity_rows[field].append(row_number)

                date = _snapshot_date(row.get("snapshot_time"))
                if date is None:
                    invalid_snapshot_times.append(row_number)
                else:
                    snapshot_date_counts[date] += 1

                bookmaker_key = str(row.get("bookmaker_key", "")).strip()
                bookmaker_counts[bookmaker_key or "<blank>"] += 1

                event_id = str(row.get("event_id", "")).strip()
                away_team = str(row.get("away_team", "")).strip()
                home_team = str(row.get("home_team", "")).strip()
                matchup = f"{away_team} @ {home_team}".strip()
                game_label = f"{event_id or '<blank>'} | {matchup}"
                game_counts[game_label] += 1

                if date is not None:
                    identity = (date,) + tuple(
                        str(row.get(field, "")).strip() for field in IDENTITY_FIELDS
                    )
                    identities[identity] += 1

            if missing_players:
                errors.append(
                    "missing player names on rows: "
                    + ", ".join(map(str, missing_players))
                )
            if invalid_sides:
                errors.append(
                    "side must be 'Over' on rows: "
                    + ", ".join(map(str, invalid_sides))
                )
            if invalid_points:
                errors.append(
                    "point must be 0.5 on rows: "
                    + ", ".join(map(str, invalid_points))
                )
            if invalid_labels:
                errors.append(
                    "hr_label must be '1+ HR' on rows: "
                    + ", ".join(map(str, invalid_labels))
                )
            if invalid_snapshot_times:
                errors.append(
                    "snapshot_time must contain a valid date on rows: "
                    + ", ".join(map(str, invalid_snapshot_times))
                )
            for field, rows in blank_identity_rows.items():
                if rows:
                    errors.append(
                        f"identity field '{field}' is blank on rows: "
                        + ", ".join(map(str, rows))
                    )

    duplicate_count = sum(count - 1 for count in identities.values() if count > 1)
    if duplicate_count:
        errors.append(
            f"found {duplicate_count} duplicate row(s) using the required identity"
        )

    return ValidationReport(
        path=csv_path,
        row_count=row_count,
        errors=tuple(errors),
        duplicate_count=duplicate_count,
        bookmaker_counts=dict(sorted(bookmaker_counts.items())),
        game_counts=dict(sorted(game_counts.items())),
        snapshot_date_counts=dict(sorted(snapshot_date_counts.items())),
    )


def _print_counts(title: str, counts: dict[str, int]) -> None:
    print(f"{title}:")
    if not counts:
        print("  (none)")
        return
    for label, count in counts.items():
        print(f"  {label}: {count}")


def print_report(report: ValidationReport) -> None:
    status = "VALID" if report.valid else "INVALID"
    print(f"Live HR data validation: {status}")
    print(f"File: {report.path}")
    print(f"Rows: {report.row_count}")
    print(f"Duplicate rows: {report.duplicate_count}")
    _print_counts("Counts by bookmaker", report.bookmaker_counts)
    _print_counts("Counts by game", report.game_counts)
    _print_counts("Counts by snapshot date", report.snapshot_date_counts)
    if report.errors:
        print("Errors:")
        for error in report.errors:
            print(f"  - {error}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the local live 1+ HR master CSV without network access."
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        type=Path,
        default=DEFAULT_CSV,
        help=f"CSV to validate (default: {DEFAULT_CSV})",
    )
    args = parser.parse_args(argv)

    report = validate_live_hr_data(args.csv_path)
    print_report(report)
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
