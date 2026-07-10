"""
Export strict live_hr_results.csv from the human-friendly workbook CSV.

Offline-only:
- No API calls
- Reads live_hr_results_workbook.csv
- Writes live_hr_results.csv with only grader-required columns

Output columns:
event_id,player,actual_home_runs,game_status,result_reason
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


DEFAULT_INPUT = Path("data/theoddsapi/live_hr_snapshots/live_hr_results_workbook.csv")
DEFAULT_OUTPUT = Path("data/theoddsapi/live_hr_snapshots/live_hr_results.csv")

REQUIRED_INPUT_COLUMNS = [
    "event_id",
    "player",
    "actual_home_runs",
    "game_status",
]

OUTPUT_COLUMNS = [
    "event_id",
    "player",
    "actual_home_runs",
    "game_status",
    "result_reason",
]


def validate_columns(fieldnames: list[str] | None) -> None:
    if not fieldnames:
        raise ValueError("Workbook CSV has no header row.")

    missing = [column for column in REQUIRED_INPUT_COLUMNS if column not in fieldnames]

    if missing:
        raise ValueError(
            f"Workbook CSV is missing required columns: {missing}. "
            f"Available columns: {fieldnames}"
        )


def export_results_from_workbook(
    input_path: Path,
    output_path: Path,
    overwrite: bool,
) -> int:
    if not input_path.exists():
        raise FileNotFoundError(f"Workbook CSV not found: {input_path}")

    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Output already exists: {output_path}\n"
            "Use --overwrite only if you intentionally want to replace it."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_rows: list[dict[str, str]] = []

    with input_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        validate_columns(reader.fieldnames)

        for row in reader:
            output_rows.append(
                {
                    "event_id": (row.get("event_id") or "").strip(),
                    "player": (row.get("player") or "").strip(),
                    "actual_home_runs": (row.get("actual_home_runs") or "").strip(),
                    "game_status": (row.get("game_status") or "").strip(),
                    "result_reason": (row.get("result_reason") or "").strip(),
                }
            )

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(output_rows)

    return len(output_rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export strict live_hr_results.csv from workbook CSV."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help=f"Input workbook CSV. Default: {DEFAULT_INPUT}",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Output results CSV. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output file if it already exists.",
    )

    args = parser.parse_args()

    count = export_results_from_workbook(
        input_path=Path(args.input),
        output_path=Path(args.output),
        overwrite=args.overwrite,
    )

    print(f"Exported strict results CSV: {args.output}")
    print(f"Rows: {count}")
    print("Next step: run the results coverage checker.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
