"""Write one immutable MLB HR temporal-split research artifact."""

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

from courtvision.sports.mlb.training.hr_temporal_split_artifact import (  # noqa: E402
    MLBHRTemporalSplitArtifactError,
    write_mlb_hr_temporal_split_artifact,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an MLB HR feature pack and sealed label custody, derive a "
            "strict whole-date 60/20/20 split, and create one immutable split "
            "artifact in an isolated staging directory. No labels are opened; "
            "no training, prediction, fetching, backtest, or wagering is run."
        )
    )
    parser.add_argument("--feature-pack", required=True, type=Path)
    parser.add_argument("--label-custody", required=True, type=Path)
    parser.add_argument("--output-staging-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = write_mlb_hr_temporal_split_artifact(
            feature_pack_path=args.feature_pack,
            label_custody_path=args.label_custody,
            output_staging_dir=args.output_staging_dir,
        )
    except (MLBHRTemporalSplitArtifactError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    artifact = result.artifact
    print("CourtVision MLB HR immutable temporal split artifact")
    print("research only | labels sealed | create once | not approved")
    print(f"artifact: {result.artifact_path}")
    print(f"feature_pack_sha256: {artifact.feature_pack_sha256}")
    print(f"label_custody_sha256: {artifact.label_custody_sha256}")
    print(f"row_identity_sha256: {artifact.row_identity_sha256}")
    print(f"artifact_sha256: {artifact.artifact_sha256}")
    print("feature_firewall_valid: true")
    print("label_custody_valid: true")
    print("labels_opened: false")
    print("strict_chronology_valid: true")
    print("model_training_enabled: false")
    print("predictions_enabled: false")
    print("live_fetching_enabled: false")
    print("betting_enabled: false")
    print("ev_enabled: false")
    print("kelly_eligible: false")
    print("elite_enabled: false")
    print("staking_enabled: false")
    print("approval_status: not_approved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
