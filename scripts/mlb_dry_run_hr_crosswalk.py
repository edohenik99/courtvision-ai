"""Read-only dry run for a proposed MLB HR identity crosswalk CSV."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

# A direct dry-run launch must not create repository-local __pycache__ files.
if __name__ == "__main__":
    sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.sports.mlb.data.crosswalk_validation import (  # noqa: E402
    MLB_HR_CROSSWALK_VERSION,
    validate_mlb_hr_crosswalk_csv,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a proposed local MLB HR batter-game crosswalk. "
            "Read-only: no network access, repairs, reports, or operational writes."
        )
    )
    parser.add_argument("crosswalk_csv", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = validate_mlb_hr_crosswalk_csv(args.crosswalk_csv)

    print("CourtVision MLB HR crosswalk dry run")
    print("research only | read-only | local file only | not approved")
    print(f"contract_version: {MLB_HR_CROSSWALK_VERSION}")
    print(f"crosswalk_file: {result.source_path}")
    print(f"status: {'PASS' if result.is_valid else 'FAIL'}")
    print(f"rows_total: {result.row_count}")
    print(f"rows_valid: {result.valid_row_count}")
    print(f"rows_invalid: {result.invalid_row_count}")
    print(f"mlbam_batters: {result.mlbam_batter_count}")
    print(f"retrosheet_batters: {result.retrosheet_batter_count}")
    print(f"mlbam_games: {result.mlbam_game_count}")
    print(f"retrosheet_games: {result.retrosheet_game_count}")
    print(f"duplicate_mappings: {result.duplicate_mapping_count}")
    print(f"conflicting_mappings: {result.conflicting_mapping_count}")
    print(f"missing_required_ids: {result.missing_required_id_count}")
    print(f"sample_identities: {result.sample_identity_count}")
    print(f"warnings: {len(result.warnings)}")
    for warning in result.warnings:
        print(f"warning: {warning}")
    print(f"errors: {len(result.errors)}")
    for error in result.errors:
        print(f"error: {error}")
    return 0 if result.is_valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
