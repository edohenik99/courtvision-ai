"""Collect approved raw sport sources into a new versioned folder."""

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

from courtvision.data_collection.core import CollectionRequest, collect_sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Collect approved, raw CourtVision source files. This command never "
            "trains models or enables prediction/betting behavior."
        )
    )
    parser.add_argument("--sport", required=True, choices=("mlb", "nba", "nfl", "nhl", "wnba"))
    parser.add_argument("--season", required=True, type=int)
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--output-raw-dir", required=True, type=Path)
    parser.add_argument("--collection-id", help="Optional new version token, e.g. v2025-01.")
    parser.add_argument("--dry-run", action="store_true", help="Plan and validate; write nothing.")

    mlb = parser.add_argument_group("MLB approved source options")
    mlb.add_argument("--statcast-csv", type=Path, help="Supplied pybaseball Statcast CSV.")
    mlb.add_argument(
        "--fetch-statcast",
        action="store_true",
        help="Fetch the date range through the optional pybaseball package.",
    )
    mlb.add_argument("--retrosheet-path", type=Path, help="Official Retrosheet file/directory.")
    mlb.add_argument(
        "--chadwick-register-path", type=Path, help="Chadwick Bureau register file/directory."
    )
    mlb.add_argument(
        "--fetch-chadwick-register",
        action="store_true",
        help="Download the approved Chadwick Bureau register archive.",
    )
    mlb.add_argument("--weather-path", type=Path, help="Meteostat or NOAA export/archive.")
    mlb.add_argument("--weather-provider", choices=("meteostat", "noaa"))
    mlb.add_argument("--ballpark-factors-path", type=Path, help="Approved supplied CSV.")
    mlb.add_argument("--odds-archive-path", type=Path, help="Approved supplied provider archive.")
    mlb.add_argument("--odds-provider", help="Licensed provider/API/archive label for provenance.")
    return parser


def _source_options(args: argparse.Namespace) -> dict[str, object]:
    names = (
        "statcast_csv",
        "fetch_statcast",
        "retrosheet_path",
        "chadwick_register_path",
        "fetch_chadwick_register",
        "weather_path",
        "weather_provider",
        "ballpark_factors_path",
        "odds_archive_path",
        "odds_provider",
    )
    return {
        name: getattr(args, name)
        for name in names
        if getattr(args, name, None) not in (None, False, "")
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.sport != "mlb" and _source_options(args):
        parser.error("MLB source flags may only be used with --sport mlb")
    if args.weather_path is not None and args.weather_provider is None:
        parser.error("--weather-provider is required with --weather-path")
    if args.weather_provider is not None and args.weather_path is None:
        parser.error("--weather-provider requires --weather-path")

    try:
        result = collect_sources(
            CollectionRequest(
                sport=args.sport,
                season=args.season,
                start_date=args.start_date,
                end_date=args.end_date,
                output_raw_dir=args.output_raw_dir,
                dry_run=args.dry_run,
                collection_id=args.collection_id,
                source_options=_source_options(args),
            )
        )
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        print(f"collection failed: {exc}", file=sys.stderr)
        return 2

    summary = {
        "blockers": list(result.blockers),
        "collection_dir": str(result.collection_dir),
        "dry_run": result.dry_run,
        "manifest_path": (
            None
            if result.manifest is None
            else str(result.collection_dir / "collection_manifest.json")
        ),
        "planned_sources": list(result.planned_sources),
        "sport": args.sport,
        "warnings": list(result.warnings),
        "writes_performed": result.manifest is not None,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
