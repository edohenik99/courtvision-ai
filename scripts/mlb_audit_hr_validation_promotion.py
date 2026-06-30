"""Read-only promotion audit for frozen MLB HR validation evidence."""

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

from courtvision.sports.mlb.training.hr_validation_promotion import (  # noqa: E402
    MLBHRValidationPromotionDecision,
    PROMOTE_TO_TEST_REVIEW,
    audit_mlb_hr_validation_promotion,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit immutable MLB HR validation evidence against the frozen "
            "acceptance policy. The command hashes and reads existing local "
            "artifacts, writes nothing, never opens test labels, and can only "
            "promote a result to human test review."
        )
    )
    parser.add_argument("--feature-pack", type=Path, required=True)
    parser.add_argument("--temporal-split-plan", type=Path, required=True)
    parser.add_argument(
        "--fitted-preprocessing-artifact",
        type=Path,
        required=True,
    )
    parser.add_argument("--prediction-artifact", type=Path, required=True)
    parser.add_argument("--validation-results", type=Path, required=True)
    return parser


def _render(decision: MLBHRValidationPromotionDecision) -> str:
    lines = [
        "CourtVision MLB HR validation promotion audit",
        "research only | read-only | test labels sealed | not production approved",
        f"pipeline_sha256: {decision.pipeline_sha256 or 'unavailable'}",
        "test_labels_sealed: " + str(decision.test_labels_sealed).lower(),
        "test_label_access_authorized: false",
        "test_evaluation_authorized: false",
        "production_approved: false",
        "writes_performed: false",
    ]
    for index, failure in enumerate(decision.failures, start=1):
        lines.append(f"failure.{index}: {failure}")
    lines.append(decision.verdict)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    decision = audit_mlb_hr_validation_promotion(
        feature_pack_path=args.feature_pack,
        temporal_split_plan_path=args.temporal_split_plan,
        fitted_preprocessing_artifact_path=args.fitted_preprocessing_artifact,
        prediction_artifact_path=args.prediction_artifact,
        validation_results=args.validation_results,
    )
    print(_render(decision))
    return 0 if decision.verdict == PROMOTE_TO_TEST_REVIEW else 2


if __name__ == "__main__":
    raise SystemExit(main())
