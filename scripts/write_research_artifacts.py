from __future__ import annotations

import argparse
import sys
import subprocess
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _format_command(command: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(command))


def orchestrate_research_artifacts(
    *,
    prediction_date: str,
    force: bool = False,
    dry_run: bool = False,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    printer: Callable[[str], None] = print,
    skip_operator_card: bool = False,
) -> int:
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)
    
    if runner is None:
        runner = subprocess.run

    # 1. Required source check
    required_board = runtime_root / "operator" / f"full_market_board_{prediction_date}.csv"
    if not required_board.exists():
        if dry_run:
            printer(f"[DRY-RUN][WARN] Required source artifact is missing: {required_board}")
        else:
            printer(f"[ERROR] Required minimum source artifact is missing: {required_board}")
            return 1

    # Define steps to execute
    steps = [
        {
            "name": "Under visibility audit",
            "script": "scripts/write_under_visibility_audit.py",
            "args": ["--prediction-date", prediction_date, "--runtime-root", str(runtime_root), "--history-root", str(history_root)],
            "optional": True,
        },
        {
            "name": "Shadow candidate lane",
            "script": "scripts/write_shadow_candidate_lane_report.py",
            "args": ["--prediction-date", prediction_date, "--runtime-root", str(runtime_root), "--history-root", str(history_root)],
            "optional": True,
        },
        {
            "name": "Shadow candidate performance",
            "script": "scripts/write_shadow_candidate_lane_performance.py",
            "args": ["--prediction-date", prediction_date, "--runtime-root", str(runtime_root), "--history-root", str(history_root)],
            "optional": True,
        },
        {
            "name": "Daily summary refresh",
            "script": "scripts/write_daily_summary.py",
            "args": ["--prediction-date", prediction_date, "--runtime-root", str(runtime_root), "--history-root", str(history_root)],
            "optional": False,
        },
        {
            "name": "Operator card refresh",
            "script": "scripts/write_operator_card.py",
            "args": ["--prediction-date", prediction_date, "--runtime-root", str(runtime_root), "--history-root", str(history_root), "--force"],
            "optional": False,
        },
    ]

    has_errors = False

    for step in steps:
        name = step["name"]
        script_relative = step["script"]
        script_args = step["args"]
        optional = step["optional"]

        if name == "Operator card refresh" and skip_operator_card:
            printer("[SKIP] Operator card refresh skipped by caller")
            continue

        command = [sys.executable, script_relative] + script_args

        printer(f"[START] {name}")

        if dry_run:
            printer(f"[DRY-RUN] Would run command: {_format_command(command)}")
            printer(f"[OK] {name}")
            continue

        script_path = ROOT_DIR / script_relative
        if not script_path.exists():
            msg = f"Script file missing: {script_relative}"
            if optional:
                printer(f"[WARN] {name} failed: {msg}")
                printer(f"[OK] {name}")  # Keep ok print but with warning
            else:
                printer(f"!!! WARNING: CORE SUMMARY/CARD REFRESH FAILED !!! {name} missing: {msg}")
                has_errors = True
            continue

        try:
            result = runner(command, cwd=ROOT_DIR, capture_output=True, text=True)
            if result.returncode != 0:
                err_msg = result.stderr.strip() or f"exit code {result.returncode}"
                if optional:
                    printer(f"[WARN] {name} failed: {err_msg}")
                    printer(f"[OK] {name}")  # Keep ok print but with warning
                else:
                    printer(f"!!! WARNING: CORE SUMMARY/CARD REFRESH FAILED !!! {name} failed: {err_msg}")
                    has_errors = True
            else:
                printer(f"[OK] {name}")
        except Exception as exc:
            if optional:
                printer(f"[WARN] {name} encountered error: {exc}")
                printer(f"[OK] {name}")
            else:
                printer(f"!!! WARNING: CORE SUMMARY/CARD REFRESH FAILED !!! {name} encountered error: {exc}")
                has_errors = True

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Orchestrate Phase 5 research artifact generation.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Propagate force to Operator Card script if executed.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not execute file-writing subprocesses; only print planned command order.",
    )
    parser.add_argument(
        "--skip-operator-card",
        action="store_true",
        help="Skip the operator card refresh step.",
    )
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    
    args = parser.parse_args(argv)

    return orchestrate_research_artifacts(
        prediction_date=args.prediction_date,
        force=args.force,
        dry_run=args.dry_run,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
        skip_operator_card=args.skip_operator_card,
    )


if __name__ == "__main__":
    raise SystemExit(main())
