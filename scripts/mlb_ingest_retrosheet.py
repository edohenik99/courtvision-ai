"""Local-file CLI for the Phase 3D Retrosheet ingestion prototype."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.sports.mlb.data.retrosheet_ingestion import (
    ingest_local_retrosheet_csvs,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Parse local Retrosheet-style CSVs for historical MLB research."
        )
    )
    parser.add_argument("--games-csv", type=Path)
    parser.add_argument("--events-csv", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--as-of-date", type=date.fromisoformat)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate without writing (this is the default).",
    )
    parser.add_argument(
        "--write-output",
        action="store_true",
        help="Explicitly write normalized JSONL and a manifest to --out-dir.",
    )
    parser.add_argument(
        "--include-raw-copy",
        action="store_true",
        help="Also copy source CSVs under --out-dir; requires --write-output.",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.games_csv is None and args.events_csv is None:
        parser.error("at least one of --games-csv or --events-csv is required")
    if args.dry_run and args.write_output:
        parser.error("--dry-run and --write-output cannot be used together")
    if args.write_output and args.out_dir is None:
        parser.error("--out-dir is required with --write-output")
    if args.include_raw_copy and not args.write_output:
        parser.error("--include-raw-copy requires --write-output")

    result = ingest_local_retrosheet_csvs(
        games_csv=args.games_csv,
        events_csv=args.events_csv,
        as_of_date=args.as_of_date,
        output_dir=args.out_dir,
        write_raw=args.write_output and args.include_raw_copy,
        write_normalized=args.write_output,
        write_manifest_file=args.write_output,
        overwrite=args.overwrite,
    )
    summary = {
        "date_range_end": result.manifest.date_range_end.isoformat(),
        "date_range_start": result.manifest.date_range_start.isoformat(),
        "event_count": len(result.events),
        "game_count": len(result.games),
        "manifest_output_path": (
            str(result.manifest_output_path)
            if result.manifest_output_path
            else None
        ),
        "mode": "write" if args.write_output else "dry-run",
        "normalized_event_output_path": (
            str(result.normalized_event_output_path)
            if result.normalized_event_output_path
            else None
        ),
        "normalized_game_output_path": (
            str(result.normalized_game_output_path)
            if result.normalized_game_output_path
            else None
        ),
        "source": result.manifest.source_name,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
