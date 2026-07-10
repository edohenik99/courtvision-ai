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
from datetime import date, datetime
from pathlib import Path
from typing import Sequence


DEFAULT_RESULTS = Path("data/theoddsapi/live_hr_snapshots/live_hr_results.csv")
DEFAULT_ODDS = Path(
    "data/theoddsapi/live_hr_snapshots/live_hr_props_master.csv"
)

REQUIRED_COLUMNS = [
    "event_id",
    "player",
    "actual_home_runs",
    "game_status",
]
ODDS_DATE_COLUMNS = ["event_id", "commence_time"]
FINAL_STATUS = "final"
VOID_STATUS = "void"
VOID_CANDIDATE_STATUS = "void_candidate"
MANUAL_REVIEW_STATUS = "manual_review_required"
NON_GRADEABLE_STATUSES = {
    VOID_STATUS,
    VOID_CANDIDATE_STATUS,
    MANUAL_REVIEW_STATUS,
}


def is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


def _validate_target_date(target_date: str) -> str:
    try:
        parsed_date = date.fromisoformat(target_date)
    except ValueError as exc:
        raise ValueError(
            f"Invalid target date {target_date!r}; expected YYYY-MM-DD."
        ) from exc

    if parsed_date.isoformat() != target_date:
        raise ValueError(
            f"Invalid target date {target_date!r}; expected YYYY-MM-DD."
        )
    return target_date


def _commence_date(value: str, odds_path: Path, row_number: int) -> str:
    try:
        parsed_time = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed_time.date().isoformat()
    except ValueError as exc:
        raise ValueError(
            f"Odds CSV row {row_number} has invalid commence_time {value!r}: "
            f"{odds_path}"
        ) from exc


def event_ids_for_date(odds_path: Path, target_date: str) -> set[str]:
    target_date = _validate_target_date(target_date)
    if not odds_path.exists():
        raise FileNotFoundError(f"Odds CSV not found: {odds_path}")

    with odds_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Odds CSV has no header row: {odds_path}")

        missing_columns = [
            column for column in ODDS_DATE_COLUMNS if column not in reader.fieldnames
        ]
        if missing_columns:
            raise ValueError(
                f"Odds CSV is missing required columns: {missing_columns}. "
                f"Available columns: {reader.fieldnames}"
            )

        event_ids: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            event_id = str(row.get("event_id") or "").strip()
            commence_time = str(row.get("commence_time") or "").strip()
            if not event_id:
                raise ValueError(f"Odds CSV row {row_number} has blank event_id")
            if not commence_time:
                raise ValueError(f"Odds CSV row {row_number} has blank commence_time")
            if _commence_date(commence_time, odds_path, row_number) == target_date:
                event_ids.add(event_id)

    return event_ids


def check_results_coverage(
    results_path: Path,
    odds_path: Path = DEFAULT_ODDS,
    target_date: str | None = None,
) -> dict[str, int | bool]:
    if not results_path.exists():
        raise FileNotFoundError(f"Results CSV not found: {results_path}")

    target_event_ids = (
        event_ids_for_date(odds_path, target_date) if target_date else None
    )

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
        invalid_game_status = 0
        void_rows = 0
        void_candidate_rows = 0
        manual_review_rows = 0
        non_gradeable_rows = 0
        gradeable_rows = 0

        for row in reader:
            if (
                target_event_ids is not None
                and str(row.get("event_id") or "").strip() not in target_event_ids
            ):
                continue

            total_rows += 1

            if is_blank(row.get("event_id")):
                missing_event_id += 1

            if is_blank(row.get("player")):
                missing_player += 1

            game_status = str(row.get("game_status") or "").strip().casefold()
            if game_status in NON_GRADEABLE_STATUSES:
                non_gradeable_rows += 1
                if game_status == VOID_STATUS:
                    void_rows += 1
                elif game_status == VOID_CANDIDATE_STATUS:
                    void_candidate_rows += 1
                elif game_status == MANUAL_REVIEW_STATUS:
                    manual_review_rows += 1
                continue

            gradeable_rows += 1
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

            if not game_status:
                missing_game_status += 1
            elif game_status != FINAL_STATUS:
                invalid_game_status += 1

    ready_to_grade = (
        total_rows > 0
        and missing_event_id == 0
        and missing_player == 0
        and missing_actual_home_runs == 0
        and missing_game_status == 0
        and invalid_actual_home_runs == 0
        and invalid_game_status == 0
    )

    return {
        "total_rows": total_rows,
        "missing_event_id": missing_event_id,
        "missing_player": missing_player,
        "missing_actual_home_runs": missing_actual_home_runs,
        "missing_game_status": missing_game_status,
        "invalid_actual_home_runs": invalid_actual_home_runs,
        "invalid_game_status": invalid_game_status,
        "void_rows": void_rows,
        "void_candidate_rows": void_candidate_rows,
        "manual_review_rows": manual_review_rows,
        "non_gradeable_rows": non_gradeable_rows,
        "gradeable_rows": gradeable_rows,
        "ready_to_grade": ready_to_grade,
    }


def print_report(
    results_path: Path,
    report: dict[str, int | bool],
    target_date: str | None = None,
) -> None:
    total_rows = int(report["total_rows"])
    missing_event_id = int(report["missing_event_id"])
    missing_player = int(report["missing_player"])
    missing_actual_home_runs = int(report["missing_actual_home_runs"])
    missing_game_status = int(report["missing_game_status"])
    invalid_actual_home_runs = int(report["invalid_actual_home_runs"])
    invalid_game_status = int(report["invalid_game_status"])
    void_rows = int(report["void_rows"])
    void_candidate_rows = int(report["void_candidate_rows"])
    manual_review_rows = int(report["manual_review_rows"])
    non_gradeable_rows = int(report["non_gradeable_rows"])
    gradeable_rows = int(report["gradeable_rows"])
    ready_to_grade = bool(report["ready_to_grade"])

    filled_actual_home_runs = gradeable_rows - missing_actual_home_runs
    filled_game_status = total_rows - missing_game_status

    print("Live HR results coverage")
    print(f"File: {results_path}")
    if target_date:
        print(f"Target date: {target_date}")
    print(f"Rows: {total_rows}")
    print(f"Void rows: {void_rows}")
    print(f"Void candidate rows: {void_candidate_rows}")
    print(f"Manual review rows: {manual_review_rows}")
    print(f"Non-gradeable rows: {non_gradeable_rows}")
    print(f"Gradeable rows: {gradeable_rows}")
    print(f"Filled actual_home_runs: {filled_actual_home_runs}")
    print(f"Filled game_status: {filled_game_status}")
    print(f"Missing event_id: {missing_event_id}")
    print(f"Missing player: {missing_player}")
    print(f"Missing actual_home_runs: {missing_actual_home_runs}")
    print(f"Missing game_status: {missing_game_status}")
    print(f"Invalid actual_home_runs: {invalid_actual_home_runs}")
    print(f"Invalid game_status: {invalid_game_status}")
    print(f"Ready to grade: {'YES' if ready_to_grade else 'NO'}")

    if not ready_to_grade:
        print()
        print("Next step: fill actual_home_runs and game_status before grading.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check live HR results CSV coverage before grading."
    )
    parser.add_argument(
        "--results",
        default=str(DEFAULT_RESULTS),
        help=f"Path to results CSV. Default: {DEFAULT_RESULTS}",
    )
    parser.add_argument(
        "--date",
        dest="target_date",
        help="Check only event IDs whose commence_time is on YYYY-MM-DD.",
    )
    parser.add_argument(
        "--odds-csv",
        type=Path,
        default=DEFAULT_ODDS,
        help=f"Master odds CSV used for date scoping. Default: {DEFAULT_ODDS}",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if the results CSV is not ready to grade.",
    )

    args = parser.parse_args(argv)

    results_path = Path(args.results)

    try:
        report = check_results_coverage(
            results_path,
            odds_path=args.odds_csv,
            target_date=args.target_date,
        )
        print_report(results_path, report, target_date=args.target_date)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.strict and not bool(report["ready_to_grade"]):
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
