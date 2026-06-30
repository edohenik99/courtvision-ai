"""Write one sealed MLB HR fitted-preprocessing research artifact."""

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

from courtvision.sports.mlb.training.hr_preprocessing_artifact import (  # noqa: E402
    MLBHRFittedPreprocessingArtifactError,
    write_fitted_preprocessing_artifact,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Write a train-only MLB HR fitted-preprocessing parameter artifact "
            "to an explicit isolated research staging directory. This command "
            "does not train a model, transform rows, predict, backtest, fetch "
            "data, or enable production or wagering gates."
        )
    )
    parser.add_argument("--feature-pack", required=True, type=Path)
    split_source = parser.add_mutually_exclusive_group(required=True)
    split_source.add_argument("--temporal-split-plan", type=Path)
    split_source.add_argument("--staged-pack", type=Path)
    parser.add_argument("--output-staging-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = write_fitted_preprocessing_artifact(
            feature_pack_path=args.feature_pack,
            temporal_split_plan_path=args.temporal_split_plan,
            staged_pack_path=args.staged_pack,
            output_staging_dir=args.output_staging_dir,
        )
    except (MLBHRFittedPreprocessingArtifactError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    artifact = result.artifact
    print("CourtVision MLB HR fitted preprocessing artifact")
    print("research only | train-fitted parameters only | not approved")
    print(f"artifact: {result.artifact_path}")
    print(f"schema_version: {artifact.schema_version}")
    print(f"feature_pack_sha256: {artifact.feature_pack_sha256}")
    print(f"split_plan_sha256: {artifact.split_plan_sha256}")
    print(f"artifact_sha256: {artifact.artifact_sha256}")
    print(f"train_date_range: {artifact.train_date_start}/{artifact.train_date_end}")
    print(f"train_rows: {artifact.train_row_count}")
    print("model_training_enabled: false")
    print("predictions_enabled: false")
    print("live_fetching_enabled: false")
    print("betting_enabled: false")
    print("ev_enabled: false")
    print("kelly_eligible: false")
    print("elite_enabled: false")
    print("staking_enabled: false")
    print("production_enabled: false")
    print("approval_status: not_approved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
