"""Build an isolated candidate MLB HR historical input pack from local files."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

# Direct execution must not create repository-local bytecode artifacts.
if __name__ == "__main__":
    sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.sports.mlb.data.historical_staging import (  # noqa: E402
    HistoricalStagingBuildError,
    build_historical_input_pack_staging,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Transform validated local MLB HR sources into an isolated candidate "
            "historical input pack. Research only; no network or operational writes."
        )
    )
    parser.add_argument("--statcast-csv", required=True, type=Path)
    parser.add_argument("--retrosheet-labels-csv", required=True, type=Path)
    parser.add_argument("--crosswalk-csv", required=True, type=Path)
    parser.add_argument("--weather-csv", required=True, type=Path)
    parser.add_argument("--ballpark-csv", required=True, type=Path)
    parser.add_argument("--odds-context-csv", required=True, type=Path)
    parser.add_argument("--output-staging-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = build_historical_input_pack_staging(
            statcast_csv=args.statcast_csv,
            retrosheet_labels_csv=args.retrosheet_labels_csv,
            crosswalk_csv=args.crosswalk_csv,
            weather_csv=args.weather_csv,
            ballpark_csv=args.ballpark_csv,
            odds_context_csv=args.odds_context_csv,
            output_staging_dir=args.output_staging_dir,
        )
    except (HistoricalStagingBuildError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("CourtVision MLB HR historical input pack staging build")
    print("research only | local files only | not approved")
    print(f"output_staging_dir: {result.output_dir}")
    print("crosswalk_validation: valid")
    print("input_pack_preflight: valid")
    print("approval_status: not_approved")
    print("eligible_for_betting: false")
    print("kelly_eligible: false")
    for source_name, row_count in result.preflight.row_counts.items():
        print(f"{source_name}_rows: {row_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
