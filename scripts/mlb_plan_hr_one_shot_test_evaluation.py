"""Read-only planning CLI for one approved frozen MLB HR test evaluation."""

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

from courtvision.sports.mlb.training.hr_one_shot_test_evaluator import (  # noqa: E402
    MLBHROneShotTestEvaluationPlan,
    MLBHROneShotTestEvaluatorError,
    plan_one_shot_frozen_mlb_hr_test_evaluation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify one immutable MLB HR test-access approval receipt and "
            "its exact frozen inputs, then print a write-free evaluation "
            "plan. The command does not open labels, calculate metrics, "
            "train, generate predictions, fetch data, write artifacts, or "
            "enable production or wagering."
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
    parser.add_argument("--test-prediction-artifact", type=Path, required=True)
    parser.add_argument(
        "--test-access-approval-receipt",
        type=Path,
        required=True,
    )
    return parser


def _render(plan: MLBHROneShotTestEvaluationPlan) -> str:
    coverage = plan.population_coverage
    policy = plan.policy
    lines = [
        "CourtVision MLB HR one-shot frozen test evaluator plan",
        "research only | approval verified | labels sealed | read-only",
        f"contract_version: {plan.contract_version}",
        f"status: {plan.status}",
        f"approval_id: {plan.approval_id}",
        "gate.test_access_approval_receipt: PASSED",
        "gate.explicit_label_handoff_approval: PASSED",
        "gate.identical_validation_test_pipeline: PASSED",
        "gate.test_prediction_population_coverage: PASSED",
        "gate.test_labels_sealed: PASSED",
        f"feature_pack_sha256: {plan.feature_pack_sha256}",
        f"temporal_split_plan_sha256: {plan.temporal_split_plan_sha256}",
        "fitted_preprocessing_artifact_sha256: "
        + plan.fitted_preprocessing_artifact_sha256,
        f"test_prediction_file_sha256: {plan.test_prediction_file_sha256}",
        "test_prediction_artifact_sha256: "
        + plan.test_prediction_artifact_sha256,
        "test_access_approval_receipt_file_sha256: "
        + plan.test_access_approval_receipt_file_sha256,
        "test_access_approval_receipt_sha256: "
        + plan.test_access_approval_receipt_sha256,
        "accepted_validation_pipeline_sha256: "
        + plan.accepted_validation_pipeline_sha256,
        f"test_pipeline_sha256: {plan.test_pipeline_sha256}",
        f"split_id: {plan.split_id}",
        f"window_id: {plan.window_id}",
        f"population.expected_rows: {coverage.expected_rows}",
        f"population.predicted_rows: {coverage.predicted_rows}",
        f"population.matched_rows: {coverage.matched_rows}",
        f"population.missing_rows: {coverage.missing_rows}",
        f"population.extra_rows: {coverage.extra_rows}",
        f"population.policy: {coverage.policy}",
        "population.labels_accessed: false",
    ]
    for metric_name in policy.allowed_metrics:
        lines.append(f"metric.{metric_name}: frozen_not_computed")
    lines.extend(
        (
            f"one_shot.maximum_attempts: {policy.maximum_attempts}",
            "one_shot.rerun_allowed: false",
            "one_shot.cherry_pick_allowed: false",
            "one_shot.report_all_frozen_metrics_required: true",
            "one_shot.failed_or_partial_attempt_consumes_one_shot: true",
            "result_artifact.required: true",
            "result_artifact.immutable: true",
            "result_artifact.write_policy: "
            + policy.result_artifact_write_policy,
            "result_artifact.writer_implemented: true",
            "test_access_approved: true",
            "label_handoff_approved: true",
            "test_labels_sealed: true",
            "labels_accessed: false",
            "test_metrics_calculated: false",
            "metric_computation_enabled: false",
            "model_training_enabled: false",
            "prediction_generation_enabled: false",
            "live_fetching_enabled: false",
            "test_evaluation_execution_enabled: false",
            "result_artifact_writing_enabled: false",
            "production_approved: false",
            "operational_use_enabled: false",
            "eligible_for_betting: false",
            "betting_enabled: false",
            "ev_enabled: false",
            "kelly_eligible: false",
            "elite_enabled: false",
            "staking_enabled: false",
            "bankroll_enabled: false",
            "writes_performed: false",
            "stop_boundary: approved test evaluation plan printed; labels "
            "remain sealed and no metrics or artifacts were produced",
        )
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = plan_one_shot_frozen_mlb_hr_test_evaluation(
            feature_pack_path=args.feature_pack,
            temporal_split_plan_path=args.temporal_split_plan,
            fitted_preprocessing_artifact_path=(
                args.fitted_preprocessing_artifact
            ),
            test_prediction_artifact_path=args.test_prediction_artifact,
            test_access_approval_receipt_path=(
                args.test_access_approval_receipt
            ),
        )
    except MLBHROneShotTestEvaluatorError as exc:
        print(f"one-shot frozen test evaluator plan refused: {exc}", file=sys.stderr)
        return 2
    print(_render(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
