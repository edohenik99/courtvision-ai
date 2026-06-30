"""Dry-run-only CLI for sealed MLB HR preprocessing planning."""

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

from courtvision.sports.mlb.training.hr_preprocessing_plan import (  # noqa: E402
    MLBHRPreprocessingPlan,
    MLBHRPreprocessingPlanningError,
    plan_mlb_hr_preprocessing,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only MLB HR preprocessing planner. Validates a feature pack "
            "and temporal split, computes train-only missing/category summaries, "
            "and reports validation/test transform diagnostics. No transformer "
            "artifact, model training, prediction, backtest, or wagering action."
        )
    )
    parser.add_argument("--feature-pack", required=True, type=Path)
    split_source = parser.add_mutually_exclusive_group(required=True)
    split_source.add_argument("--temporal-split-plan", type=Path)
    split_source.add_argument("--staged-pack", type=Path)
    return parser


def _values(values: Sequence[str]) -> str:
    return ", ".join(values) if values else "(none)"


def _render(plan: MLBHRPreprocessingPlan) -> str:
    lines = [
        "CourtVision MLB HR sealed preprocessing dry run",
        "research only | read-only | train-fit planning only | not approved",
        f"schema_version: {plan.schema_version}",
        f"policy_version: {plan.policy_version}",
        f"feature_pack: {plan.feature_pack_path}",
        f"feature_pack_sha256: {plan.feature_pack_sha256}",
        f"split_source_kind: {plan.split_source_kind}",
        f"split_source: {plan.split_source_path}",
        "feature_firewall: valid",
        "temporal_split: valid",
        f"fit_split: {plan.fit_split}",
        f"validation_transform_only: {str(plan.validation_transform_only).lower()}",
        f"test_transform_only: {str(plan.test_transform_only).lower()}",
        f"train_rows: {plan.train_row_count}",
        f"validation_rows: {plan.validation_row_count}",
        f"test_rows: {plan.test_row_count}",
        f"numeric_columns: {_values(plan.numeric_columns)}",
        f"categorical_columns: {_values(plan.categorical_columns)}",
        f"market_columns: {_values(plan.market_columns)}",
        f"lineage_columns: {_values(plan.lineage_columns)}",
    ]
    for summary in plan.numeric_summaries:
        lines.append(
            f"train_missing[{summary.column}]: "
            f"{summary.train_missing_count}/{summary.train_row_count} "
            f"({summary.train_missing_rate:.6f}); "
            f"train_median={summary.train_median:g}; "
            "missing_indicator=true"
        )
    for summary in plan.categorical_summaries:
        lines.extend(
            (
                f"train_missing[{summary.column}]: "
                f"{summary.train_missing_count}/{summary.train_row_count} "
                f"({summary.train_missing_rate:.6f})",
                f"retained_train_categories[{summary.column}]: "
                f"{_values(summary.retained_train_categories)}",
                f"rare_train_categories[{summary.column}]: "
                f"{_values(summary.rare_train_categories)}",
                f"validation_only_categories[{summary.column}]: "
                f"{_values(summary.validation_only_categories)}",
                f"test_only_categories[{summary.column}]: "
                f"{_values(summary.test_only_categories)}",
            )
        )
    lines.extend(
        (
            f"approval_status: {plan.approval_status}",
            f"model_training_enabled: {str(plan.model_training_enabled).lower()}",
            f"backtesting_enabled: {str(plan.backtesting_enabled).lower()}",
            f"predictions_enabled: {str(plan.predictions_enabled).lower()}",
            f"eligible_for_betting: {str(plan.eligible_for_betting).lower()}",
            f"ev_enabled: {str(plan.ev_enabled).lower()}",
            f"kelly_eligible: {str(plan.kelly_eligible).lower()}",
            f"elite_enabled: {str(plan.elite_enabled).lower()}",
            f"staking_enabled: {str(plan.staking_enabled).lower()}",
            f"artifacts_written: {str(plan.artifacts_written).lower()}",
        )
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = plan_mlb_hr_preprocessing(
            feature_pack_path=args.feature_pack,
            temporal_split_plan_path=args.temporal_split_plan,
            staged_pack_path=args.staged_pack,
        )
    except (MLBHRPreprocessingPlanningError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(_render(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
