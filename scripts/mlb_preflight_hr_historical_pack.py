"""Read-only CLI preflight for a real aligned MLB HR historical input pack."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.sports.mlb.data.historical_input_pack import (  # noqa: E402
    HistoricalInputPackError,
    preflight_historical_input_pack,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate one local, aligned, real MLB HR historical input pack. "
            "This command is read-only and performs no network access or build."
        )
    )
    parser.add_argument("pack_dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = preflight_historical_input_pack(args.pack_dir)
    try:
        result.raise_for_errors()
    except HistoricalInputPackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("CourtVision MLB HR historical input pack preflight")
    print("historical research only | read-only | local files only | not approved")
    print(f"pack_dir: {result.paths.root}")
    print("preflight_status: valid")
    print(f"date_range_start: {result.date_range_start}")
    print(f"date_range_end: {result.date_range_end}")
    for source_name, row_count in result.row_counts.items():
        print(f"{source_name}_rows: {row_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
