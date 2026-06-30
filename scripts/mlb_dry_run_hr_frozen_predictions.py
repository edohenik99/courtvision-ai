"""Read-only dry run for the MLB HR frozen-prediction handoff."""

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

from courtvision.sports.mlb.training.hr_backtest_runner import (  # noqa: E402
    MLBHRBacktestRunnerContractError,
    plan_sealed_mlb_hr_research_backtest,
)
from courtvision.sports.mlb.training.hr_label_handoff import (  # noqa: E402
    EVALUATION_ONLY,
    MLBHRLabelHandoffError,
    validate_mlb_hr_label_handoff,
)
from courtvision.sports.mlb.training.hr_prediction_artifact import (  # noqa: E402
    MLBHRFrozenPredictionArtifact,
    MLBHRFrozenPredictionArtifactError,
    load_frozen_prediction_artifact,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an existing frozen MLB HR research prediction artifact "
            "before running aggregate-only runner/label-handoff checks. The "
            "command reads four local artifacts, writes nothing, and performs "
            "no training, prediction generation, evaluation, fetching, "
            "wagering, or production approval."
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
    return parser


def _matching_hashes(
    artifact: MLBHRFrozenPredictionArtifact,
    runner_plan: object,
    handoff_report: object,
) -> bool:
    expected = (
        artifact.feature_pack_sha256,
        artifact.temporal_split_plan_sha256,
        artifact.fitted_preprocessing_artifact_sha256,
    )
    return all(
        (
            getattr(report, "feature_pack_sha256", None),
            getattr(report, "temporal_split_plan_sha256", None),
            getattr(report, "fitted_preprocessing_artifact_sha256", None),
        )
        == expected
        for report in (runner_plan, handoff_report)
    )


def _frozen_evaluation_phase(handoff_report: object, split_id: str) -> bool:
    expected_name = f"{split_id}_evaluation_after_predictions_frozen"
    for phase in getattr(handoff_report, "phases", ()):
        if getattr(phase, "name", None) != expected_name:
            continue
        return (
            getattr(phase, split_id, None) == EVALUATION_ONLY
            and getattr(phase, "predictions_frozen", False) is True
        )
    return False


def _render(
    artifact: MLBHRFrozenPredictionArtifact,
    runner_plan: object,
    handoff_report: object,
) -> str:
    return "\n".join(
        (
            "CourtVision MLB HR frozen prediction artifact dry run",
            "research only | read-only | evaluation prohibited | not approved",
            f"prediction_schema_version: {artifact.schema_version}",
            f"prediction_artifact: {artifact.path}",
            f"prediction_artifact_sha256: {artifact.artifact_sha256}",
            f"split_id: {artifact.split_id}",
            f"window_id: {artifact.window_id}",
            f"prediction_timestamp: {artifact.prediction_timestamp.isoformat()}",
            f"prediction_rows: {len(artifact.rows)}",
            "gate.prediction_artifact_validated_before_label_handoff: PASSED",
            "gate.runner_plan_checks: PASSED",
            "gate.label_handoff_contract: PASSED",
            "evaluation_data_sealed_during_prediction_validation: true",
            "labels_may_open_only_after_prediction_validation: true",
            "label_values_exposed: false",
            f"runner_status: {getattr(runner_plan, 'status', '')}",
            f"label_handoff_status: {getattr(handoff_report, 'status', '')}",
            f"research_only: {str(artifact.research_only).lower()}",
            f"approval_status: {artifact.approval_status}",
            f"production_approved: {str(artifact.production_approved).lower()}",
            f"operational_use_enabled: "
            f"{str(artifact.operational_use_enabled).lower()}",
            f"model_training_enabled: "
            f"{str(artifact.model_training_enabled).lower()}",
            f"prediction_generation_enabled: "
            f"{str(artifact.prediction_generation_enabled).lower()}",
            f"evaluation_enabled: {str(artifact.evaluation_enabled).lower()}",
            f"live_fetching_enabled: "
            f"{str(artifact.live_fetching_enabled).lower()}",
            "writes_performed: false",
            "stop_boundary: validation complete; no evaluation was performed",
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        # This validator hashes the feature pack but never opens its labels.
        artifact = load_frozen_prediction_artifact(
            args.prediction_artifact,
            feature_pack_path=args.feature_pack,
            temporal_split_plan_path=args.temporal_split_plan,
            fitted_preprocessing_artifact_path=(
                args.fitted_preprocessing_artifact
            ),
        )

        # Aggregate label-contract inspection occurs only after predictions
        # have passed the immutable artifact boundary above.
        runner_plan = plan_sealed_mlb_hr_research_backtest(
            feature_pack_path=args.feature_pack,
            temporal_split_plan_path=args.temporal_split_plan,
            fitted_preprocessing_artifact_path=(
                args.fitted_preprocessing_artifact
            ),
        )
        handoff_report = validate_mlb_hr_label_handoff(
            feature_pack_path=args.feature_pack,
            temporal_split_plan_path=args.temporal_split_plan,
            fitted_preprocessing_artifact_path=(
                args.fitted_preprocessing_artifact
            ),
        )
        if not _matching_hashes(artifact, runner_plan, handoff_report):
            raise MLBHRFrozenPredictionArtifactError(
                "prediction, runner, and label-handoff input hashes do not match"
            )
        if not _frozen_evaluation_phase(handoff_report, artifact.split_id):
            raise MLBHRFrozenPredictionArtifactError(
                "label handoff does not require frozen predictions before "
                f"{artifact.split_id} evaluation"
            )
        if getattr(handoff_report, "label_values_exposed", None) is not False:
            raise MLBHRFrozenPredictionArtifactError(
                "label handoff exposed row-level label values"
            )
        if getattr(handoff_report, "artifacts_written", None) is not False:
            raise MLBHRFrozenPredictionArtifactError(
                "label handoff must remain write-free"
            )
    except (
        MLBHRFrozenPredictionArtifactError,
        MLBHRBacktestRunnerContractError,
        MLBHRLabelHandoffError,
    ) as exc:
        print(f"frozen prediction dry run refused: {exc}", file=sys.stderr)
        return 2
    print(_render(artifact, runner_plan, handoff_report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
