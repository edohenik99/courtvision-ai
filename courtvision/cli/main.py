"""Single command-line entrypoint for safe CourtVision operations."""

from __future__ import annotations

import argparse
from datetime import date
from importlib import metadata
import json
from pathlib import Path
import sys
from typing import Sequence

from courtvision.data_collection.core import (
    CollectionError,
    CollectionRequest,
    UnsupportedSportCollectionError,
    collect_sources,
)
from courtvision.data_collection import doctor as collector_doctor


SPORTS = ("mlb", "nba", "nfl", "nhl", "wnba")
SOURCE_OPTION_NAMES = (
    "statcast_csv",
    "fetch_statcast",
    "statcast_chunk_size",
    "retrosheet_path",
    "chadwick_register_path",
    "fetch_chadwick_register",
    "weather_path",
    "fetch_weather",
    "weather_provider",
    "stadium_map_path",
    "max_station_attempts",
    "ballpark_factors_path",
    "odds_archive_path",
    "odds_provider",
)
SOURCE_VERSION_FALLBACK = "0.1.0"


def _package_version() -> str:
    try:
        return metadata.version("courtvision")
    except metadata.PackageNotFoundError:
        return SOURCE_VERSION_FALLBACK


def _add_collect_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("sport", choices=SPORTS)
    parser.add_argument("--season", required=True, type=int)
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        help="Collection start date (YYYY-MM-DD; defaults to January 1 of season).",
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        help="Collection end date (YYYY-MM-DD; defaults to December 31 of season).",
    )
    parser.add_argument(
        "--output-raw-dir",
        type=Path,
        default=Path("courtvision-raw"),
        help="Raw collection root (default: ./courtvision-raw).",
    )
    parser.add_argument("--collection-id", help="Optional new version token, e.g. v2025-01.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan and validate without fetching sports data or writing files.",
    )

    mlb = parser.add_argument_group("MLB approved source options")
    mlb.add_argument("--statcast-csv", type=Path, help="Supplied pybaseball Statcast CSV.")
    mlb.add_argument(
        "--fetch-statcast",
        action="store_true",
        help="Fetch through pybaseball (never fetched during --dry-run).",
    )
    mlb.add_argument(
        "--resume",
        action="store_true",
        help="Resume an interrupted chunked Statcast collection.",
    )
    mlb.add_argument(
        "--statcast-chunk-size",
        choices=("weekly", "biweekly"),
        help="Statcast request window size (default: weekly).",
    )
    mlb.add_argument("--retrosheet-path", type=Path)
    mlb.add_argument(
        "--chadwick-register-path",
        type=Path,
        help="Supplied Chadwick Bureau register file/directory fallback.",
    )
    mlb.add_argument(
        "--fetch-chadwick-register",
        action="store_true",
        help="Download the approved Chadwick Bureau register archive.",
    )
    mlb.add_argument("--weather-path", type=Path)
    mlb.add_argument("--weather-provider", choices=("meteostat", "noaa"))
    mlb.add_argument(
        "--fetch-weather",
        action="store_true",
        help="Fetch historical MLB hourly weather through Meteostat.",
    )
    mlb.add_argument(
        "--stadium-map-path",
        type=Path,
        help="Approved CSV mapping Retrosheet park IDs to coordinates and timezones.",
    )
    mlb.add_argument(
        "--max-station-attempts",
        type=int,
        help="Maximum nearby Meteostat stations tried per game (default: 5).",
    )
    mlb.add_argument("--ballpark-factors-path", type=Path)
    mlb.add_argument("--odds-archive-path", type=Path)
    mlb.add_argument("--odds-provider")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="courtvision",
        description=(
            "CourtVision diagnostics and raw-only collection. These commands do not "
            "train models or enable prediction or betting behavior."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser(
        "doctor", help="Check optional collector dependencies without fetching or writing."
    )
    doctor.add_argument("--json", action="store_true", help="Print the report as JSON.")

    collect = commands.add_parser(
        "collect", help="Plan or collect approved raw sport sources."
    )
    _add_collect_arguments(collect)

    commands.add_parser("version", help="Print the installed CourtVision version.")
    return parser


def _source_options(args: argparse.Namespace) -> dict[str, object]:
    return {
        name: getattr(args, name)
        for name in SOURCE_OPTION_NAMES
        if getattr(args, name, None) not in (None, False, "")
    }


def _run_collect(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    source_options = _source_options(args)
    if args.sport != "mlb" and (source_options or args.resume):
        parser.error("MLB source flags may only be used with collect mlb")
    if args.resume and not args.fetch_statcast:
        parser.error("--resume requires --fetch-statcast")
    if args.resume and args.collection_id is None:
        parser.error("--resume requires --collection-id")
    if args.statcast_chunk_size is not None and not args.fetch_statcast:
        parser.error("--statcast-chunk-size requires --fetch-statcast")
    if args.weather_path is not None and args.fetch_weather:
        parser.error("choose either --weather-path or --fetch-weather")
    if (
        args.weather_path is not None or args.fetch_weather
    ) and args.weather_provider is None:
        parser.error("--weather-provider is required for weather collection")
    if args.weather_provider is not None and not (args.weather_path or args.fetch_weather):
        parser.error("--weather-provider requires --weather-path or --fetch-weather")
    if args.fetch_weather and args.weather_provider != "meteostat":
        parser.error("--fetch-weather requires --weather-provider meteostat")
    if args.fetch_weather and args.retrosheet_path is None:
        parser.error("--fetch-weather requires --retrosheet-path")
    if args.fetch_weather and args.stadium_map_path is None:
        parser.error("--fetch-weather requires --stadium-map-path")
    if args.stadium_map_path is not None and not args.fetch_weather:
        parser.error("--stadium-map-path requires --fetch-weather")
    if args.max_station_attempts is not None and not args.fetch_weather:
        parser.error("--max-station-attempts requires --fetch-weather")
    if args.max_station_attempts is not None and args.max_station_attempts < 1:
        parser.error("--max-station-attempts must be a positive integer")

    try:
        start_date = args.start_date or date(args.season, 1, 1)
        end_date = args.end_date or date(args.season, 12, 31)
        result = collect_sources(
            CollectionRequest(
                sport=args.sport,
                season=args.season,
                start_date=start_date,
                end_date=end_date,
                output_raw_dir=args.output_raw_dir,
                dry_run=args.dry_run,
                resume=args.resume,
                collection_id=args.collection_id,
                source_options=source_options,
            )
        )
    except UnsupportedSportCollectionError as exc:
        print(f"collection failed closed for {args.sport}: {exc}", file=sys.stderr)
        return 2
    except (CollectionError, FileNotFoundError, FileExistsError, ValueError) as exc:
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
    if result.manifest is not None:
        weather_metadata = next(
            (
                source.metadata
                for source in result.manifest.sources
                if source.source_name == "weather_meteostat"
                and "weather_summary" in source.metadata
            ),
            None,
        )
        if weather_metadata is not None:
            summary["weather_summary"] = weather_metadata["weather_summary"]
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return collector_doctor.main(["--json"] if args.json else [])
    if args.command == "collect":
        return _run_collect(args, parser)
    if args.command == "version":
        print(f"courtvision {_package_version()}")
        return 0
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
