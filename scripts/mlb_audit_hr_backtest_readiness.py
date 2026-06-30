"""Read-only CLI for staged MLB HR historical backtest readiness."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.sports.mlb.data.historical_backtest_readiness import (  # noqa: E402
    HistoricalBacktestReadinessVerdict,
    audit_historical_backtest_readiness,
    historical_backtest_readiness_to_json,
    historical_backtest_readiness_to_text,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit an existing local MLB HR historical input pack before any "
            "research backtest. This command is read-only and performs no fetch, "
            "build, training, scoring, or wagering action."
        )
    )
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Print the report to stdout; no report file is created.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = audit_historical_backtest_readiness(args.pack_dir)
    if args.format == "json":
        print(historical_backtest_readiness_to_json(report))
    else:
        print(historical_backtest_readiness_to_text(report))
    return (
        2
        if report.verdict == HistoricalBacktestReadinessVerdict.NOT_READY.value
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
