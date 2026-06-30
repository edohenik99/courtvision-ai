"""Read-only CLI for the MLB HR model-spec and label-handoff boundary."""

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
    MLBHRLabelHandoffError,
    MLBHRLabelHandoffReport,
    validate_mlb_hr_label_handoff,
)


MODEL_SPECIFICATION_ID = "mlb-hr-first-research-model-v1"
MODEL_SPECIFICATION_DOCUMENT = (
    "docs/COURTVISION_MLB_HR_MODEL_SPECIFICATION_AND_LABEL_HANDOFF.md"
)
FIRST_MODEL_FAMILY = "L2-regularized binomial logistic regression"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the sealed runner plan and MLB HR label handoff. The "
            "command reads three local artifacts and prints aggregate plans "
            "only; it performs no training, predictions, evaluation, fetching, "
            "betting operation, approval, or write."
        )
    )
    parser.add_argument("--feature-pack", type=Path, required=True)
    parser.add_argument("--temporal-split-plan", type=Path, required=True)
    parser.add_argument(
        "--fitted-preprocessing-artifact",
        type=Path,
        required=True,
    )
    return parser


def _render(report: MLBHRLabelHandoffReport, runner_status: str) -> str:
    lines = [
        "CourtVision MLB HR model specification and label handoff dry run",
        "research only | read-only | execution prohibited | not approved",
        f"model_specification_id: {MODEL_SPECIFICATION_ID}",
        f"model_specification_document: {MODEL_SPECIFICATION_DOCUMENT}",
        f"first_model_family: {FIRST_MODEL_FAMILY}",
        "model_specification_status: allowed_for_future_research_only",
        f"runner_status: {runner_status}",
        "gate.runner_plan_checks: PASSED",
        "gate.label_handoff: PASSED",
        f"label_handoff_contract_version: {report.contract_version}",
        f"label_handoff_status: {report.status}",
        f"label_column: {report.label_column}",
        f"feature_pack: {report.feature_pack_path}",
        f"temporal_split_plan: {report.temporal_split_plan_path}",
        "fitted_preprocessing_artifact: "
        f"{report.fitted_preprocessing_artifact_path}",
    ]
    for distribution in report.distributions:
        prefix = f"labels.{distribution.split}"
        lines.extend(
            (
                f"{prefix}.rows: {distribution.row_count}",
                f"{prefix}.positive: {distribution.positive_count}",
                f"{prefix}.negative: {distribution.negative_count}",
                f"{prefix}.positive_rate: {distribution.positive_rate:.6f}",
            )
        )
    for phase in report.phases:
        lines.append(
            f"phase.{phase.name}.labels: train={phase.train}; "
            f"validation={phase.validation}; test={phase.test}; "
            f"predictions_frozen={str(phase.predictions_frozen).lower()}"
        )
    lines.extend(
        (
            "label_values_exposed: false",
            f"approval_status: {report.approval_status}",
            f"model_training_enabled: "
            f"{str(report.model_training_enabled).lower()}",
            f"predictions_enabled: {str(report.predictions_enabled).lower()}",
            f"live_fetching_enabled: "
            f"{str(report.live_fetching_enabled).lower()}",
            f"backtesting_enabled: {str(report.backtesting_enabled).lower()}",
            f"eligible_for_betting: "
            f"{str(report.eligible_for_betting).lower()}",
            f"ev_enabled: {str(report.ev_enabled).lower()}",
            f"kelly_eligible: {str(report.kelly_eligible).lower()}",
            f"elite_enabled: {str(report.elite_enabled).lower()}",
            f"staking_enabled: {str(report.staking_enabled).lower()}",
            f"production_approved: {str(report.production_approved).lower()}",
            f"artifacts_written: {str(report.artifacts_written).lower()}",
            "stop_boundary: handoff plan printed; no model operation exists",
        )
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        runner_plan = plan_sealed_mlb_hr_research_backtest(
            feature_pack_path=args.feature_pack,
            temporal_split_plan_path=args.temporal_split_plan,
            fitted_preprocessing_artifact_path=(
                args.fitted_preprocessing_artifact
            ),
        )
        report = validate_mlb_hr_label_handoff(
            feature_pack_path=args.feature_pack,
            temporal_split_plan_path=args.temporal_split_plan,
            fitted_preprocessing_artifact_path=(
                args.fitted_preprocessing_artifact
            ),
        )
        runner_counts = {
            window.name: (
                window.player_game_rows,
                window.positive_labels,
                window.negative_labels,
            )
            for window in runner_plan.windows
        }
        handoff_counts = {
            item.split: (
                item.row_count,
                item.positive_count,
                item.negative_count,
            )
            for item in report.distributions
        }
        if handoff_counts != runner_counts:
            raise MLBHRLabelHandoffError(
                "runner and label handoff split distributions do not match"
            )
    except (MLBHRBacktestRunnerContractError, MLBHRLabelHandoffError) as exc:
        print(f"model/label handoff dry run refused: {exc}", file=sys.stderr)
        return 2
    print(_render(report, runner_plan.status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
