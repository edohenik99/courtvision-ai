"""Local-file CLI for the Phase 3F ballpark-factor table prototype."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.sports.mlb.data.ballpark_factors import (
    ingest_local_ballpark_factors_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse a local static ballpark-factor CSV for MLB HR research."
    )
    parser.add_argument("--input-csv", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path)
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
        help="Also copy the source CSV under --out-dir; requires --write-output.",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.dry_run and args.write_output:
        parser.error("--dry-run and --write-output cannot be used together")
    if args.write_output and args.out_dir is None:
        parser.error("--out-dir is required with --write-output")
    if args.include_raw_copy and not args.write_output:
        parser.error("--include-raw-copy requires --write-output")

    result = ingest_local_ballpark_factors_csv(
        args.input_csv,
        output_dir=args.out_dir,
        write_raw=args.write_output and args.include_raw_copy,
        write_normalized=args.write_output,
        write_manifest_file=args.write_output,
        overwrite=args.overwrite,
    )
    summary = {
        "data_version": result.manifest.source_version,
        "manifest_output_path": (
            str(result.manifest_output_path)
            if result.manifest_output_path
            else None
        ),
        "mode": "write" if args.write_output else "dry-run",
        "normalized_output_path": (
            str(result.normalized_output_path)
            if result.normalized_output_path
            else None
        ),
        "raw_output_path": (
            str(result.raw_output_path) if result.raw_output_path else None
        ),
        "row_count": len(result.rows),
        "source": result.manifest.source_name,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
