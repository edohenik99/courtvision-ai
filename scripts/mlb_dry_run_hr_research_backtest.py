"""Read-only CLI for the sealed MLB HR research backtest contract."""

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
    MLBHRBacktestExecutionPlan,
    MLBHRBacktestRunnerContractError,
    plan_sealed_mlb_hr_research_backtest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the sealed MLB HR research-backtest contract and print "
            "an execution plan. The command is local-file-only and read-only; "
            "it performs no fitting, transforms, training, predictions, metric "
            "computation, backtest execution, fetching, wagering, or writes."
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


def _render(plan: MLBHRBacktestExecutionPlan) -> str:
    lines = [
        "CourtVision MLB HR sealed research backtest dry run",
        "research only | read-only | execution prohibited | not approved",
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
        "gate.feature_firewall: PASSED",
        "gate.temporal_split: PASSED",
        "gate.fitted_preprocessing_artifact: PASSED",
        "gate.per_window_readiness: PASSED",
        "gate.evaluation_label_access: PASSED",
        f"label_columns: {', '.join(plan.label_columns)}",
        f"label_access_scope: {plan.label_access_scope}",
        f"preprocessing_fit_split: {plan.preprocessing_fit_split}",
        f"validation_preprocessing_boundary: "
        f"{plan.validation_preprocessing_boundary}",
        f"test_preprocessing_boundary: {plan.test_preprocessing_boundary}",
    ]
    for window in plan.windows:
        prefix = f"window.{window.name}"
        lines.extend(
            (
                f"{prefix}.date_range: {window.date_start}/{window.date_end}",
                f"{prefix}.player_game_rows: {window.player_game_rows}",
                f"{prefix}.positive_labels: {window.positive_labels}",
                f"{prefix}.negative_labels: {window.negative_labels}",
                f"{prefix}.preprocessing_boundary: "
                f"{window.preprocessing_boundary}",
                f"{prefix}.current_action: {window.current_action}",
                f"{prefix}.model_action: {window.model_action}",
                f"{prefix}.prediction_action: {window.prediction_action}",
                f"{prefix}.metric_action: {window.metric_action}",
            )
        )
    for metric in plan.metric_definitions:
        lines.append(
            f"metric.{metric.name}: {metric.status} | role={metric.role} | "
            f"direction={metric.direction} | definition={metric.definition}"
        )
    lines.extend(
        (
            f"approval_status: {plan.approval_status}",
            f"execution_authorized: {str(plan.execution_authorized).lower()}",
            f"model_training_enabled: "
            f"{str(plan.model_training_enabled).lower()}",
            f"preprocessing_transform_enabled: "
            f"{str(plan.preprocessing_transform_enabled).lower()}",
            f"predictions_enabled: {str(plan.predictions_enabled).lower()}",
            f"metric_computation_enabled: "
            f"{str(plan.metric_computation_enabled).lower()}",
            f"live_fetching_enabled: "
            f"{str(plan.live_fetching_enabled).lower()}",
            f"backtesting_enabled: {str(plan.backtesting_enabled).lower()}",
            f"eligible_for_betting: "
            f"{str(plan.eligible_for_betting).lower()}",
            f"ev_enabled: {str(plan.ev_enabled).lower()}",
            f"kelly_eligible: {str(plan.kelly_eligible).lower()}",
            f"elite_enabled: {str(plan.elite_enabled).lower()}",
            f"staking_enabled: {str(plan.staking_enabled).lower()}",
            f"production_approved: {str(plan.production_approved).lower()}",
            f"artifacts_written: {str(plan.artifacts_written).lower()}",
            "stop_boundary: plan printed; no backtest operation exists",
        )
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = plan_sealed_mlb_hr_research_backtest(
            feature_pack_path=args.feature_pack,
            temporal_split_plan_path=args.temporal_split_plan,
            fitted_preprocessing_artifact_path=(
                args.fitted_preprocessing_artifact
            ),
        )
    except MLBHRBacktestRunnerContractError as exc:
        print(f"sealed backtest dry run refused: {exc}", file=sys.stderr)
        return 2
    print(_render(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
