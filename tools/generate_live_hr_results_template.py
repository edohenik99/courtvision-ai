"""
Generate a fillable live_hr_results.csv template from the master MLB HR odds CSV.

Offline-only:
- No API calls
- Reads the master odds CSV
- Dedupes to one row per event_id + player
- Creates the required grader columns:
  event_id,player,actual_home_runs,game_status
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


DEFAULT_INPUT = Path("data/theoddsapi/live_hr_snapshots/live_hr_props_master.csv")
DEFAULT_OUTPUT = Path("data/theoddsapi/live_hr_snapshots/live_hr_results.csv")

REQUIRED_OUTPUT_COLUMNS = [
    "event_id",
    "player",
    "actual_home_runs",
    "game_status",
]

PLAYER_COLUMN_CANDIDATES = [
    "player",
    "player_name",
    "participant",
    "description",
]


def find_column(fieldnames: list[str], candidates: list[str]) -> str:
    normalized = {name.lower().strip(): name for name in fieldnames}

    for candidate in candidates:
        if candidate.lower() in normalized:
            return normalized[candidate.lower()]

    raise ValueError(
        f"Could not find any of these columns: {candidates}. "
        f"Available columns: {fieldnames}"
    )


def generate_template(input_path: Path, output_path: Path, overwrite: bool) -> int:
    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Output already exists: {output_path}\n"
            "Use --overwrite only if you intentionally want to replace it."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames:
            raise ValueError(f"Input CSV has no header row: {input_path}")

        event_id_column = find_column(reader.fieldnames, ["event_id"])
        player_column = find_column(reader.fieldnames, PLAYER_COLUMN_CANDIDATES)

        unique_rows: dict[tuple[str, str], dict[str, str]] = {}

        for row in reader:
            event_id = (row.get(event_id_column) or "").strip()
            player = (row.get(player_column) or "").strip()

            if not event_id or not player:
                continue

            key = (event_id, player)

            unique_rows[key] = {
                "event_id": event_id,
                "player": player,
                "actual_home_runs": "",
                "game_status": "",
            }

    rows = sorted(
        unique_rows.values(),
        key=lambda r: (r["event_id"].lower(), r["player"].lower()),
    )

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REQUIRED_OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate fillable live_hr_results.csv from master MLB HR odds CSV."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help=f"Input master odds CSV. Default: {DEFAULT_INPUT}",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Output results template CSV. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output file if it already exists.",
    )

    args = parser.parse_args()

    count = generate_template(
        input_path=Path(args.input),
        output_path=Path(args.output),
        overwrite=args.overwrite,
    )

    print(f"Generated results template: {args.output}")
    print(f"Rows: {count}")
    print("Fill actual_home_runs and game_status before running the grader.")


if __name__ == "__main__":
    main()