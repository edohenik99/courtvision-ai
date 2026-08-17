"""Explicit, unscheduled acquisition of prospective historical Statcast evidence."""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.sports.mlb.data.prospective_context_acquisition import (  # noqa: E402
    DEFAULT_ACQUISITION_ROOT,
    HttpEvidenceProvider,
)
from courtvision.sports.mlb.data.prospective_statcast_history import (  # noqa: E402
    DEFAULT_INITIAL_CHUNK_DAYS,
    DEFAULT_SUSPICIOUS_CHUNK_ROW_COUNT,
    acquire_historical_statcast_snapshot,
)


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _ids(path: Path, column: str) -> tuple[str, ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or column not in reader.fieldnames:
            raise ValueError(f"{path} lacks required column {column}")
        values = {
            str(row.get(column) or "").strip()
            for row in reader
            if str(row.get(column) or "").strip()
        }
    return tuple(sorted(values, key=int))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Acquire one research-only, cutoff-safe daily Statcast history snapshot"
    )
    parser.add_argument("--operating-date", required=True)
    parser.add_argument("--season-start-date", required=True)
    parser.add_argument("--requested-as-of-utc", required=True)
    parser.add_argument("--target-game-id", action="append", required=True)
    parser.add_argument("--eligible-hitter-csv", type=Path, required=True)
    parser.add_argument("--eligible-hitter-id-column", default="mlbam_batter_id")
    parser.add_argument("--probable-pitcher-csv", type=Path, required=True)
    parser.add_argument("--probable-pitcher-id-column", default="pitcher_id")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument(
        "--initial-chunk-days", type=int, default=DEFAULT_INITIAL_CHUNK_DAYS
    )
    parser.add_argument(
        "--suspicious-chunk-row-count",
        type=int,
        default=DEFAULT_SUSPICIOUS_CHUNK_ROW_COUNT,
    )
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--acquisition-root", type=Path, default=DEFAULT_ACQUISITION_ROOT)
    parser.add_argument("--git-commit")
    parser.add_argument(
        "--resume-from-partial",
        type=Path,
        help="validated immutable partial snapshot whose failed leaves alone may be retried",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_network:
        raise PermissionError("live Statcast acquisition requires --allow-network")
    result = acquire_historical_statcast_snapshot(
        operating_date=date.fromisoformat(args.operating_date),
        season_start_date=date.fromisoformat(args.season_start_date),
        requested_as_of_utc=datetime.fromisoformat(
            args.requested_as_of_utc.replace("Z", "+00:00")
        ),
        target_game_ids=tuple(args.target_game_id),
        eligible_hitter_ids=_ids(
            args.eligible_hitter_csv, args.eligible_hitter_id_column
        ),
        probable_pitcher_ids=_ids(
            args.probable_pitcher_csv, args.probable_pitcher_id_column
        ),
        provider=HttpEvidenceProvider(timeout_seconds=args.timeout_seconds),
        acquisition_root=args.acquisition_root,
        git_commit=args.git_commit or _git_commit(),
        initial_chunk_days=args.initial_chunk_days,
        suspicious_chunk_row_count=args.suspicious_chunk_row_count,
        resume_from_snapshot=args.resume_from_partial,
    )
    print(
        json.dumps(
            {
                "snapshot_id": result.snapshot_id,
                "snapshot_state": result.snapshot_state,
                "manifest_path": str(result.manifest_path),
                "manifest_digest": result.manifest_digest,
                "no_op": result.no_op,
                "provider_call_count": result.provider_call_count,
                "provider_credit_cost": None,
                "game_count": result.game_count,
                "pitch_count": result.pitch_count,
                "plate_appearance_count": result.plate_appearance_count,
                "reused_chunk_count": result.reused_chunk_count,
                "recovered_chunk_count": result.recovered_chunk_count,
                "completion_witness_counts_by_source_type": (
                    result.completion_witness_counts_by_source_type
                ),
                "model_training_performed": False,
                "prediction_or_operational_publication_performed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
