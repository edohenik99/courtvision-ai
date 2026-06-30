"""Build a research-only MLB HR feature pack from a staged input pack."""

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

from courtvision.sports.mlb.data.historical_feature_pack import (  # noqa: E402
    HistoricalFeaturePackBuildError,
    build_historical_feature_pack,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a timestamp-firewalled MLB HR feature pack from a validated "
            "staged historical input pack. Research only; no fetch, training, "
            "prediction, backtest execution, or wagering action."
        )
    )
    parser.add_argument("--historical-input-pack", required=True, type=Path)
    parser.add_argument("--output-staging-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = build_historical_feature_pack(
            historical_input_pack=args.historical_input_pack,
            output_staging_dir=args.output_staging_dir,
        )
    except (HistoricalFeaturePackBuildError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("CourtVision MLB HR historical feature-pack build")
    print("research only | local files only | not approved")
    print(f"historical_input_pack: {result.preflight.paths.root}")
    print("input_pack_preflight: valid")
    print(f"readiness_verdict: {result.readiness.verdict}")
    print("feature_firewall: valid")
    print(f"feature_count: {len(result.feature_pack.feature_names)}")
    print(f"row_count: {result.row_count}")
    print(f"feature_pack: {result.feature_pack_path}")
    print("approval_status: not_approved")
    print("model_training_enabled: false")
    print("backtesting_enabled: false")
    print("predictions_enabled: false")
    print("eligible_for_betting: false")
    print("ev_enabled: false")
    print("kelly_eligible: false")
    print("elite_enabled: false")
    print("staking_enabled: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
