"""
Check fill coverage for live_hr_results.csv before running the MLB HR grader.

Offline-only:
- No API calls
- Reads the generated results CSV
- Reports missing actual_home_runs and game_status values
- Does not modify files

Default behavior:
- Prints a report
- Exits 0 even if not ready, so it can be used as a friendly preflight check

Use --strict if you want a non-zero exit code when results are incomplete.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


DEFAULT_RESULTS = Path("data/theoddsapi/live_hr_snapshots/live_hr_results.csv")

REQUIRED_COLUMNS = [
    "event_id",
    "player",
    "actual_home_runs",
    "game_status",
]


def is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


def check_results_coverage(results_path: Path) -> dict[str, int | bool]:
    if not results_path.exists():
        raise FileNotFoundError(f"Results CSV not found: {results_path}")

    with results_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames:
            raise ValueError(f"Results CSV has no header row: {results_path}")

        missing_columns = [
            column for column in REQUIRED_COLUMNS if column not in reader.fieldnames
        ]

        if missing_columns:
            raise ValueError(
                f"Results CSV is missing required columns: {missing_columns}. "
                f"Available columns: {reader.fieldnames}"
            )

        total_rows = 0
        missing_event_id = 0
        missing_player = 0
        missing_actual_home_runs = 0
        missing_game_status = 0
        invalid_actual_home_runs = 0

        for row in reader:
            total_rows += 1

            if is_blank(row.get("event_id")):
                missing_event_id += 1

            if is_blank(row.get("player")):
                missing_player += 1

            actual_home_runs = row.get("actual_home_runs")

            if is_blank(actual_home_runs):
                missing_actual_home_runs += 1
            else:
                try:
                    parsed_hr = int(actual_home_runs.strip())
                    if parsed_hr < 0:
                        invalid_actual_home_runs += 1
                except ValueError:
                    invalid_actual_home_runs += 1

            if is_blank(row.get("game_status")):
                missing_game_status += 1

    ready_to_grade = (
        total_rows > 0
        and missing_event_id == 0
        and missing_player == 0
        and missing_actual_home_runs == 0
        and missing_game_status == 0
        and invalid_actual_home_runs == 0
    )

    return {
        "total_rows": total_rows,
        "missing_event_id": missing_event_id,
        "missing_player": missing_player,
        "missing_actual_home_runs": missing_actual_home_runs,
        "missing_game_status": missing_game_status,
        "invalid_actual_home_runs": invalid_actual_home_runs,
        "ready_to_grade": ready_to_grade,
    }


def print_report(results_path: Path, report: dict[str, int | bool]) -> None:
    total_rows = int(report["total_rows"])
    missing_event_id = int(report["missing_event_id"])
    missing_player = int(report["missing_player"])
    missing_actual_home_runs = int(report["missing_actual_home_runs"])
    missing_game_status = int(report["missing_game_status"])
    invalid_actual_home_runs = int(report["invalid_actual_home_runs"])
    ready_to_grade = bool(report["ready_to_grade"])

    filled_actual_home_runs = total_rows - missing_actual_home_runs
    filled_game_status = total_rows - missing_game_status

    print("Live HR results coverage")
    print(f"File: {results_path}")
    print(f"Rows: {total_rows}")
    print(f"Filled actual_home_runs: {filled_actual_home_runs}")
    print(f"Filled game_status: {filled_game_status}")
    print(f"Missing event_id: {missing_event_id}")
    print(f"Missing player: {missing_player}")
    print(f"Missing actual_home_runs: {missing_actual_home_runs}")
    print(f"Missing game_status: {missing_game_status}")
    print(f"Invalid actual_home_runs: {invalid_actual_home_runs}")
    print(f"Ready to grade: {'YES' if ready_to_grade else 'NO'}")

    if not ready_to_grade:
        print()
        print("Next step: fill actual_home_runs and game_status before grading.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check live HR results CSV coverage before grading."
    )
    parser.add_argument(
        "--results",
        default=str(DEFAULT_RESULTS),
        help=f"Path to results CSV. Default: {DEFAULT_RESULTS}",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if the results CSV is not ready to grade.",
    )

    args = parser.parse_args()

    results_path = Path(args.results)

    try:
        report = check_results_coverage(results_path)
        print_report(results_path, report)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.strict and not bool(report["ready_to_grade"]):
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())