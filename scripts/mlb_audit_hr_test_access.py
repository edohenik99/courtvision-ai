"""Read-only audit before frozen MLB HR test-label access review."""

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

from courtvision.sports.mlb.training.hr_test_evaluation_access import (  # noqa: E402
    APPROVE_TEST_LABEL_ACCESS_REVIEW,
    MLBHRTestEvaluationAccessDecision,
    audit_mlb_hr_test_evaluation_access,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit immutable MLB HR validation-promotion evidence and frozen "
            "full-population test predictions. The command reads and hashes "
            "existing local artifacts, writes nothing, never opens labels or "
            "calculates metrics, and cannot authorize production or wagering."
        )
    )
    parser.add_argument("--feature-pack", type=Path, required=True)
    parser.add_argument(
        "--split-plan",
        "--temporal-split-plan",
        dest="temporal_split_plan",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--preprocessing-artifact",
        "--fitted-preprocessing-artifact",
        dest="fitted_preprocessing_artifact",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--validation-prediction-artifact", type=Path, required=True
    )
    parser.add_argument("--validation-results", type=Path, required=True)
    parser.add_argument(
        "--validation-promotion-audit-result", type=Path, required=True
    )
    parser.add_argument("--test-prediction-artifact", type=Path, required=True)
    return parser


def _render(decision: MLBHRTestEvaluationAccessDecision) -> str:
    population = "unavailable"
    if decision.expected_test_rows is not None:
        population = (
            f"{decision.matched_test_rows}/{decision.expected_test_rows} matched; "
            f"{decision.predicted_test_rows} predicted"
        )
    lines = [
        "CourtVision MLB HR frozen test-evaluation access audit",
        "research only | read-only | labels sealed | not production approved",
        f"pipeline_sha256: {decision.pipeline_sha256 or 'unavailable'}",
        "validation_result_sha256: "
        + (decision.validation_result_sha256 or "unavailable"),
        "test_prediction_artifact_sha256: "
        + (decision.test_prediction_artifact_sha256 or "unavailable"),
        f"test_population_coverage: {population}",
        "test_predictions_frozen: "
        + str(decision.test_predictions_frozen).lower(),
        "test_labels_sealed: " + str(decision.test_labels_sealed).lower(),
        "labels_accessed: false",
        "test_metrics_calculated: false",
        "test_label_access_authorized: false",
        "test_evaluation_authorized: false",
        "production_approved: false",
        "operational_use_enabled: false",
        "eligible_for_betting: false",
        "ev_enabled: false",
        "kelly_eligible: false",
        "elite_enabled: false",
        "staking_enabled: false",
        "writes_performed: false",
    ]
    for index, failure in enumerate(decision.failures, start=1):
        lines.append(f"failure.{index}: {failure}")
    lines.append(decision.verdict)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    decision = audit_mlb_hr_test_evaluation_access(
        feature_pack_path=args.feature_pack,
        temporal_split_plan_path=args.temporal_split_plan,
        fitted_preprocessing_artifact_path=args.fitted_preprocessing_artifact,
        validation_prediction_artifact_path=(
            args.validation_prediction_artifact
        ),
        validation_results_path=args.validation_results,
        validation_promotion_audit_result_path=(
            args.validation_promotion_audit_result
        ),
        test_prediction_artifact_path=args.test_prediction_artifact,
    )
    print(_render(decision))
    return 0 if decision.verdict == APPROVE_TEST_LABEL_ACCESS_REVIEW else 2


if __name__ == "__main__":
    raise SystemExit(main())
