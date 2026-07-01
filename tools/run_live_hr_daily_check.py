"""Run a network-free health check for the collected live MLB HR dataset."""

from __future__ import annotations

import argparse
import csv
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

if __package__:
    from .validate_live_hr_data import DEFAULT_CSV, REQUIRED_COLUMNS, ValidationReport, validate_live_hr_data
else:
    from validate_live_hr_data import DEFAULT_CSV, REQUIRED_COLUMNS, ValidationReport, validate_live_hr_data


DEDUPE_FIELDS = (
    "event_id",
    "bookmaker_key",
    "market",
    "player",
    "side",
    "point",
)


def _parse_snapshot_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def dedupe_live_hr_data(path: str | Path) -> tuple[int, int]:
    """Deduplicate a CSV in place, keeping the latest snapshot for each daily key."""

    csv_path = Path(path)
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = tuple(reader.fieldnames or ())
        missing = [column for column in REQUIRED_COLUMNS if column not in columns]
        if missing:
            raise ValueError("cannot dedupe; missing required columns: " + ", ".join(missing))
        rows = list(reader)

    selected: dict[tuple[str, ...], tuple[datetime | None, int, dict[str, str]]] = {}
    for index, row in enumerate(rows):
        snapshot_time = _parse_snapshot_time(row.get("snapshot_time"))
        snapshot_date = snapshot_time.date().isoformat() if snapshot_time else ""
        key = (snapshot_date,) + tuple(
            str(row.get(field, "")).strip() for field in DEDUPE_FIELDS
        )
        current = selected.get(key)
        current_sort = (snapshot_time is not None, snapshot_time, index)
        if current is None:
            selected[key] = (snapshot_time, index, row)
            continue
        previous_time, previous_index, _ = current
        previous_sort = (
            previous_time is not None,
            previous_time,
            previous_index,
        )
        if current_sort > previous_sort:
            selected[key] = (snapshot_time, index, row)

    deduped_rows = [item[2] for item in sorted(selected.values(), key=lambda item: item[1])]

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8",
            dir=csv_path.parent,
            prefix=f".{csv_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(deduped_rows)
        os.replace(temp_path, csv_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

    return len(rows), len(deduped_rows)


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "(none)"
    return ", ".join(f"{label}={count}" for label, count in counts.items())


def print_health_report(report: ValidationReport) -> None:
    status = "VALID" if report.valid else "INVALID"
    print(f"Live HR daily check: {status}")
    print(f"Rows: {report.row_count}")
    print(f"Duplicates: {report.duplicate_count}")
    print(f"Snapshot dates: {_format_counts(report.snapshot_date_counts)}")
    print(f"Bookmakers: {_format_counts(report.bookmaker_counts)}")
    print(f"Games: {len(report.game_counts)}")
    if report.errors:
        print("Errors:")
        for error in report.errors:
            print(f"  - {error}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the local live MLB HR odds dataset without network access."
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        type=Path,
        default=DEFAULT_CSV,
        help=f"CSV to check (default: {DEFAULT_CSV})",
    )
    parser.add_argument(
        "--dedupe",
        action="store_true",
        help="Deduplicate the selected CSV in place before validation.",
    )
    args = parser.parse_args(argv)

    if args.dedupe:
        try:
            before, after = dedupe_live_hr_data(args.csv_path)
        except (OSError, ValueError) as exc:
            print("Live HR daily check: INVALID")
            print(f"Dedupe failed: {exc}")
            return 1
        print(f"Dedupe rows: {before} -> {after}")

    report = validate_live_hr_data(args.csv_path)
    print_health_report(report)
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
