"""Thin manual CLI for NBA player-points research orchestration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from courtvision.sports.nba.player_points_research_runner import (
    EXIT_CODES,
    NBAPlayerPointsManualRunnerError,
    run_manual_bundle,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one manual, offline NBA player-points research operation.",
    )
    parser.add_argument("--bundle", required=True, help="Path to a versioned manual-run bundle JSON file.")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish evidence only after recomputing the plan and matching the approval digest.",
    )
    parser.add_argument(
        "--approval-digest",
        help="Exact approval digest from the reviewed plan.",
    )
    parser.add_argument(
        "--approval-operator-id",
        help="Explicit operator ID supplying publication approval.",
    )
    parser.add_argument(
        "--approval-timestamp-utc",
        help="Explicit UTC timestamp for the operator approval.",
    )
    parser.add_argument(
        "--approval-note",
        help="Optional operator approval note to include in the receipt.",
    )
    parser.add_argument(
        "--repository-root",
        help="Optional repository root to inspect for read-only Git state.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run_manual_bundle(
            Path(args.bundle),
            publish=args.publish,
            approval_digest=args.approval_digest,
            approval_operator_id=args.approval_operator_id,
            approval_timestamp_utc=args.approval_timestamp_utc,
            approval_note=args.approval_note,
            repository_root=args.repository_root,
        )
    except NBAPlayerPointsManualRunnerError as exc:
        payload = {
            "exit_code": exc.exit_code,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        return int(exc.exit_code)
    except Exception as exc:
        payload = {
            "exit_code": EXIT_CODES["invalid_command_or_bundle"],
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        return int(EXIT_CODES["invalid_command_or_bundle"])

    print(json.dumps(result, sort_keys=True))
    return int(result.get("exit_code", EXIT_CODES["success"]))


if __name__ == "__main__":
    raise SystemExit(main())
