"""Read-only CLI for MLB HR per-window statistical-power gates."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.sports.mlb.training.hr_window_readiness import (  # noqa: E402
    MLBHRWindowReadinessError,
    MLBHRWindowReadinessReport,
    validate_mlb_hr_window_readiness,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate read-only MLB HR statistical-power and context gates for "
            "train, validation, and test windows. No training, predictions, "
            "fetching, backtest execution, wagering, approval, or writes."
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


def _render(report: MLBHRWindowReadinessReport) -> str:
    lines = [
        "CourtVision MLB HR per-window readiness",
        "research only | read-only | no backtest execution",
        f"feature_pack: {report.feature_pack_path}",
        f"temporal_split_plan: {report.temporal_split_plan_path}",
        f"fitted_preprocessing_artifact: "
        f"{report.fitted_preprocessing_artifact_path}",
        f"feature_firewall_valid: {str(report.feature_firewall_valid).lower()}",
        f"strict_temporal_order_valid: "
        f"{str(report.temporal_split_valid).lower()}",
        f"preprocessing_artifact_hash_match: "
        f"{str(report.preprocessing_artifact_hash_match).lower()}",
    ]
    for window in report.windows:
        prefix = window.name
        lines.extend(
            (
                f"{prefix}_player_game_rows: {window.player_game_rows}",
                f"{prefix}_unique_games: {window.unique_games}",
                f"{prefix}_unique_players: {window.unique_players}",
                f"{prefix}_positive_hr_labels: {window.positive_labels}",
                f"{prefix}_negative_labels: {window.negative_labels}",
                f"{prefix}_odds_coverage: {window.odds_coverage:.2%} "
                f"({window.odds_covered_rows}/{window.player_game_rows})",
                f"{prefix}_weather_coverage: {window.weather_coverage:.2%} "
                f"({window.weather_covered_rows}/{window.player_game_rows})",
                f"{prefix}_ballpark_coverage: {window.ballpark_coverage:.2%} "
                f"({window.ballpark_covered_rows}/{window.player_game_rows})",
                f"{prefix}_date_start: {window.date_start or 'none'}",
                f"{prefix}_date_end: {window.date_end or 'none'}",
                f"{prefix}_date_span_days: {window.date_span_days}",
                f"{prefix}_verdict: {window.verdict}",
            )
        )
        failures = (
            window.review_failures
            if window.review_failures
            else window.research_failures
        )
        lines.extend(f"{prefix}_gate_failure: {failure}" for failure in failures)
    lines.extend(
        (
            f"overall_verdict: {report.verdict}",
            f"approval_status: {report.approval_status}",
            f"model_training_enabled: "
            f"{str(report.model_training_enabled).lower()}",
            f"backtesting_enabled: {str(report.backtesting_enabled).lower()}",
            f"predictions_enabled: {str(report.predictions_enabled).lower()}",
            f"live_fetching_enabled: "
            f"{str(report.live_fetching_enabled).lower()}",
            f"eligible_for_betting: "
            f"{str(report.eligible_for_betting).lower()}",
            f"ev_enabled: {str(report.ev_enabled).lower()}",
            f"kelly_eligible: {str(report.kelly_eligible).lower()}",
            f"elite_enabled: {str(report.elite_enabled).lower()}",
            f"staking_enabled: {str(report.staking_enabled).lower()}",
            f"artifacts_written: {str(report.artifacts_written).lower()}",
        )
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = validate_mlb_hr_window_readiness(
            feature_pack_path=args.feature_pack,
            temporal_split_plan_path=args.temporal_split_plan,
            fitted_preprocessing_artifact_path=(
                args.fitted_preprocessing_artifact
            ),
        )
    except MLBHRWindowReadinessError as exc:
        print(f"window readiness failed: {exc}", file=sys.stderr)
        return 2
    print(_render(report))
    return 0 if report.ready_for_research_backtest else 2


if __name__ == "__main__":
    raise SystemExit(main())
