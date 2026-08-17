"""Offline materialization of immutable prospective MLB context evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.sports.mlb.data.prospective_context_materializer import (  # noqa: E402
    DEFAULT_FORWARD_SOURCE_ROOT,
    DEFAULT_MATERIALIZATION_ROOT,
    materialize_prospective_source_pack,
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Materialize a research-only prospective context source pack"
    )
    parser.add_argument("--history-manifest", type=Path, required=True)
    parser.add_argument("--volatile-manifest", type=Path, required=True)
    parser.add_argument("--weather-manifest", type=Path)
    parser.add_argument("--statcast-history-manifest", type=Path)
    parser.add_argument(
        "--materialization-root", type=Path, default=DEFAULT_MATERIALIZATION_ROOT
    )
    parser.add_argument(
        "--source-research-root", type=Path, default=DEFAULT_FORWARD_SOURCE_ROOT
    )
    parser.add_argument("--git-commit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = materialize_prospective_source_pack(
        history_manifest_path=args.history_manifest,
        volatile_manifest_path=args.volatile_manifest,
        weather_manifest_path=args.weather_manifest,
        statcast_history_manifest_path=args.statcast_history_manifest,
        git_commit=args.git_commit or _git_commit(),
        materialization_root=args.materialization_root,
        source_research_root=args.source_research_root,
    )
    print(
        json.dumps(
            {
                "materialization_id": result.materialization_id,
                "materialization_dir": str(result.materialization_dir),
                "pack_id": result.source_pack.pack_id,
                "pack_dir": str(result.source_pack.pack_dir),
                "candidate_count": result.candidate_count,
                "probable_pitcher_count": result.probable_pitcher_count,
                "lineup_slot_count": result.lineup_slot_count,
                "feature_v2_dry_run_row_count": len(result.feature_dry_run.rows),
                "feature_v2_summary": result.feature_dry_run.summary,
                "model_training_performed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
