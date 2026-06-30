"""Write-free dry-run and validation modes for frozen MLB HR predictions."""

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

from courtvision.sports.mlb.training.hr_evaluation_contract import (  # noqa: E402
    MLBHREvaluationContractError,
    MLBHREvaluationPlan,
    MLBHRValidationEvaluationResult,
    evaluate_frozen_mlb_hr_validation,
    plan_frozen_mlb_hr_research_evaluation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate four frozen local MLB HR research artifacts and print "
            "either the dry-run plan or in-memory validation metrics. This "
            "command writes nothing and performs no training, prediction "
            "generation, test evaluation, live fetching, wagering, or "
            "production approval."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("dry-run", "validation"),
        default="dry-run",
        help=(
            "dry-run validates and prints the plan; validation additionally "
            "opens validation labels and computes write-free metrics"
        ),
    )
    parser.add_argument("--feature-pack", type=Path, required=True)
    parser.add_argument("--temporal-split-plan", type=Path, required=True)
    parser.add_argument(
        "--fitted-preprocessing-artifact",
        type=Path,
        required=True,
    )
    parser.add_argument("--prediction-artifact", type=Path, required=True)
    return parser


def _render(plan: MLBHREvaluationPlan) -> str:
    coverage = plan.population_coverage
    bootstrap = plan.bootstrap_policy
    segments = plan.segmentation_policy
    artifact_policy = plan.evaluation_artifact_policy
    lines = [
        "CourtVision MLB HR frozen research evaluator dry run",
        "research only | read-only | metrics not computed | not approved",
        f"contract_version: {plan.contract_version}",
        f"status: {plan.status}",
        f"feature_pack: {plan.feature_pack_path}",
        f"feature_pack_sha256: {plan.feature_pack_sha256}",
        f"temporal_split_plan: {plan.temporal_split_plan_path}",
        f"temporal_split_plan_sha256: {plan.temporal_split_plan_sha256}",
        f"fitted_preprocessing_artifact: "
        f"{plan.fitted_preprocessing_artifact_path}",
        f"fitted_preprocessing_artifact_sha256: "
        f"{plan.fitted_preprocessing_artifact_sha256}",
        f"prediction_artifact: {plan.prediction_artifact_path}",
        f"prediction_artifact_sha256: {plan.prediction_artifact_sha256}",
        f"split_id: {plan.split_id}",
        f"window_id: {plan.window_id}",
    ]
    for index, step in enumerate(plan.label_opening_sequence, start=1):
        lines.append(f"label_opening_sequence.{index}: {step}")
    lines.extend(
        (
            "gate.frozen_prediction_artifact: PASSED",
            "gate.prediction_population_coverage: PASSED",
            "gate.sealed_runner: PASSED",
            "gate.evaluation_only_label_handoff: PASSED",
            f"population.expected_rows: {coverage.expected_rows}",
            f"population.predicted_rows: {coverage.predicted_rows}",
            f"population.matched_rows: {coverage.matched_rows}",
            f"population.missing_rows: {coverage.missing_rows}",
            f"population.extra_rows: {coverage.extra_rows}",
            f"population.policy: {coverage.missing_prediction_policy}",
            "population.labels_accessed_during_coverage: false",
            "labels_opened_after_prediction_validation: true",
            "label_values_exposed: false",
        )
    )
    for metric in plan.metric_definitions:
        lines.append(
            f"metric.{metric.name}: {metric.status} | "
            f"direction={metric.direction} | definition={metric.definition}"
        )
    for baseline in plan.baseline_comparisons:
        lines.append(
            f"baseline.{baseline.name}: {baseline.status} | "
            f"population={baseline.population} | source={baseline.source}"
        )
    lines.extend(
        (
            f"bootstrap.unit: {bootstrap.unit}",
            f"bootstrap.method: {bootstrap.method}",
            f"bootstrap.confidence_level: {bootstrap.confidence_level}",
            f"bootstrap.replicates: {bootstrap.replicates}",
            f"bootstrap.minimum_successful_replicates: "
            f"{bootstrap.minimum_successful_replicates}",
            f"bootstrap.seed: {bootstrap.seed}",
            f"bootstrap.failure_result: {bootstrap.failure_result}",
            f"segmentation.primary_population: {segments.primary_population}",
            "segmentation.diagnostic_only: "
            + ", ".join(segments.diagnostic_segments),
            f"segmentation.minimum_rows: {segments.minimum_rows}",
            f"segmentation.minimum_positives: {segments.minimum_positives}",
            f"segmentation.minimum_negatives: {segments.minimum_negatives}",
            f"segmentation.underpowered_action: {segments.underpowered_action}",
            f"evaluation_artifact.policy: {artifact_policy.policy}",
            "evaluation_artifact.writer_implemented: false",
            "evaluation_artifact.overwrite_allowed: false",
            "evaluation_artifact.operational_paths_prohibited: true",
            f"approval_status: {plan.approval_status}",
            f"model_training_enabled: {str(plan.model_training_enabled).lower()}",
            f"prediction_generation_enabled: "
            f"{str(plan.prediction_generation_enabled).lower()}",
            f"final_metrics_calculated: "
            f"{str(plan.final_metrics_calculated).lower()}",
            f"metric_computation_enabled: "
            f"{str(plan.metric_computation_enabled).lower()}",
            f"live_fetching_enabled: {str(plan.live_fetching_enabled).lower()}",
            f"operational_use_enabled: "
            f"{str(plan.operational_use_enabled).lower()}",
            f"eligible_for_betting: {str(plan.eligible_for_betting).lower()}",
            f"ev_enabled: {str(plan.ev_enabled).lower()}",
            f"kelly_eligible: {str(plan.kelly_eligible).lower()}",
            f"elite_enabled: {str(plan.elite_enabled).lower()}",
            f"staking_enabled: {str(plan.staking_enabled).lower()}",
            f"production_approved: {str(plan.production_approved).lower()}",
            f"evaluation_artifact_writing_enabled: "
            f"{str(plan.evaluation_artifact_writing_enabled).lower()}",
            f"artifacts_written: {str(plan.artifacts_written).lower()}",
            "stop_boundary: evaluation plan printed; no metrics or artifacts produced",
        )
    )
    return "\n".join(lines)


def _render_validation(result: MLBHRValidationEvaluationResult) -> str:
    plan = result.plan
    bootstrap = result.bootstrap
    lines = [
        "CourtVision MLB HR frozen validation evaluator",
        "research only | validation metrics only | read-only | not approved",
        f"contract_version: {result.contract_version}",
        f"status: {result.status}",
        f"split_id: {result.split_id}",
        f"window_id: {plan.window_id}",
        "gate.frozen_prediction_artifact: PASSED",
        "gate.prediction_population_coverage: PASSED",
        "gate.sealed_runner: PASSED",
        "gate.evaluation_only_label_handoff: PASSED",
        f"population.rows: {bootstrap.row_count}",
        f"population.positive_labels: {bootstrap.positive_count}",
        f"population.negative_labels: {bootstrap.negative_count}",
        f"population.unique_game_dates: {bootstrap.unique_game_date_count}",
        "validation_labels_opened_after_prediction_validation: true",
        "test_labels_opened: false",
        "test_labels_sealed: true",
    ]
    for interval in bootstrap.intervals:
        if interval.status == "estimated":
            interval_text = (
                f"[{interval.lower_bound:.17g}, {interval.upper_bound:.17g}]"
            )
        else:
            interval_text = "inconclusive"
        lines.append(
            f"metric.{interval.series_name}.{interval.metric_name}: "
            f"{interval.estimate:.17g} | ci={interval_text} | "
            f"successful_replicates={interval.successful_replicates}"
        )
    lines.extend(
        (
            f"bootstrap.unit: {bootstrap.unit}",
            f"bootstrap.method: {bootstrap.method}",
            f"bootstrap.confidence_level: {bootstrap.confidence_level}",
            f"bootstrap.replicates: {bootstrap.requested_replicates}",
            f"bootstrap.minimum_successful_replicates: "
            f"{bootstrap.minimum_successful_replicates}",
            f"bootstrap.seed: {bootstrap.seed}",
            "bootstrap.deterministic: true",
            f"approval_status: {result.approval_status}",
            f"model_training_enabled: "
            f"{str(result.model_training_enabled).lower()}",
            f"prediction_generation_enabled: "
            f"{str(result.prediction_generation_enabled).lower()}",
            f"live_fetching_enabled: {str(result.live_fetching_enabled).lower()}",
            f"operational_use_enabled: "
            f"{str(result.operational_use_enabled).lower()}",
            f"eligible_for_betting: "
            f"{str(result.eligible_for_betting).lower()}",
            f"ev_enabled: {str(result.ev_enabled).lower()}",
            f"kelly_eligible: {str(result.kelly_eligible).lower()}",
            f"elite_enabled: {str(result.elite_enabled).lower()}",
            f"staking_enabled: {str(result.staking_enabled).lower()}",
            f"production_approved: {str(result.production_approved).lower()}",
            f"artifacts_written: {str(result.artifacts_written).lower()}",
            "stop_boundary: validation metrics printed from memory; "
            "no artifact produced",
        )
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        common_arguments = {
            "feature_pack_path": args.feature_pack,
            "temporal_split_plan_path": args.temporal_split_plan,
            "fitted_preprocessing_artifact_path": (
                args.fitted_preprocessing_artifact
            ),
            "prediction_artifact_path": args.prediction_artifact,
        }
        if args.mode == "validation":
            result = evaluate_frozen_mlb_hr_validation(**common_arguments)
        else:
            result = plan_frozen_mlb_hr_research_evaluation(**common_arguments)
    except MLBHREvaluationContractError as exc:
        print(f"frozen evaluator dry run refused: {exc}", file=sys.stderr)
        return 2
    if isinstance(result, MLBHRValidationEvaluationResult):
        print(_render_validation(result))
    else:
        print(_render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
