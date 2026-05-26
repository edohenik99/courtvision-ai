from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PYTHON_COMMAND = ("py", "-3.13")
FORBIDDEN_COMMAND_PARTS = (
    "run_today.ps1",
    "run_today.bat",
    "courtvision_ai.py",
    "scripts/run_kelly_stakes.py",
)


@dataclass(frozen=True)
class RefreshCommand:
    label: str
    command: tuple[str, ...]


def _validate_date(value: str, *, field_name: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"{field_name} must use YYYY-MM-DD format: {value}")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid calendar date: {value}") from exc
    return value


def _date_arg(value: str) -> str:
    try:
        return _validate_date(value, field_name="date")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def required_artifact_paths(
    prediction_date: str,
    *,
    runtime_root: str | Path = "outputs/runtime",
) -> dict[str, Path]:
    runtime_root = Path(runtime_root)
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    return {
        "elite_board": operator / f"elite_board_{prediction_date}.csv",
        "full_market_board": operator / f"full_market_board_{prediction_date}.csv",
        "elite_pipeline_audit_summary": operator
        / f"elite_pipeline_audit_summary_{prediction_date}.json",
        "board_diagnostics": diagnostics / f"board_diagnostics_{prediction_date}.json",
    }


def missing_required_artifacts(
    prediction_date: str,
    *,
    runtime_root: str | Path = "outputs/runtime",
) -> dict[str, Path]:
    return {
        name: path
        for name, path in required_artifact_paths(
            prediction_date,
            runtime_root=runtime_root,
        ).items()
        if not path.exists()
    }


def build_refresh_commands(
    *,
    prediction_date: str,
    through_date: str | None = None,
    include_current_date: bool = True,
    skip_repair: bool = False,
    force_operator_card: bool = True,
) -> list[RefreshCommand]:
    prediction_date = _validate_date(prediction_date, field_name="prediction-date")
    through_date = _validate_date(
        through_date or prediction_date,
        field_name="through-date",
    )

    commands: list[RefreshCommand] = []
    if not skip_repair:
        commands.append(
            RefreshCommand(
                "prefill_actual_feedback",
                (
                    *PYTHON_COMMAND,
                    "scripts/prefill_actual_feedback.py",
                    "--prediction-date",
                    through_date,
                ),
            )
        )
        repair_command = [
            *PYTHON_COMMAND,
            "scripts/repair_pending_grades.py",
            "--all-completed",
            "--through-date",
            through_date,
            "--regrade-terminal-player-stat-missing",
            "--terminal-regrade-date",
            through_date,
        ]
        if include_current_date:
            repair_command.append("--include-current-date")
        commands.append(RefreshCommand("repair_pending_grades", tuple(repair_command)))

    commands.extend(
        [
            RefreshCommand(
                "shadow_artifacts",
                (
                    *PYTHON_COMMAND,
                    "scripts/write_shadow_artifacts.py",
                    "--prediction-date",
                    prediction_date,
                    "--closed-slate-safe",
                ),
            ),
            RefreshCommand(
                "daily_summary",
                (
                    *PYTHON_COMMAND,
                    "scripts/write_daily_summary.py",
                    "--prediction-date",
                    prediction_date,
                    "--closed-slate-safe",
                    "--no-board-annotation-write",
                ),
            ),
            RefreshCommand(
                "quality_summary",
                (
                    *PYTHON_COMMAND,
                    "scripts/write_quality_summary.py",
                    "--prediction-date",
                    prediction_date,
                    "--closed-slate-safe",
                    "--no-board-annotation-write",
                ),
            ),
            RefreshCommand(
                "completion_audit",
                (
                    *PYTHON_COMMAND,
                    "scripts/write_completion_state_audit.py",
                    "--prediction-date",
                    prediction_date,
                ),
            ),
            RefreshCommand(
                "operator_card",
                (
                    *PYTHON_COMMAND,
                    "scripts/write_operator_card.py",
                    "--prediction-date",
                    prediction_date,
                    *(("--force",) if force_operator_card else ()),
                ),
            ),
            RefreshCommand(
                "artifact_manifest",
                (
                    *PYTHON_COMMAND,
                    "scripts/write_artifact_manifest.py",
                    "--prediction-date",
                    prediction_date,
                ),
            ),
        ]
    )
    _assert_no_forbidden_commands(commands)
    return commands


def _assert_no_forbidden_commands(commands: Sequence[RefreshCommand]) -> None:
    for refresh_command in commands:
        command_text = _format_command(refresh_command.command).lower()
        for forbidden in FORBIDDEN_COMMAND_PARTS:
            if forbidden.lower() in command_text:
                raise RuntimeError(f"Refusing forbidden command: {command_text}")


def _format_command(command: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(command))


def _print_missing_artifacts(
    missing: dict[str, Path],
    *,
    printer: Callable[[str], None],
) -> None:
    printer("Missing required prediction-date artifacts; no commands run.")
    for name, path in missing.items():
        printer(f"- {name}: {path}")


def _print_success_summary(
    *,
    prediction_date: str,
    through_date: str,
    repair_status: str,
    dry_run: bool,
    printer: Callable[[str], None],
) -> None:
    action_word = "would refresh" if dry_run else "refreshed"
    printer("Closed-slate refresh summary")
    printer(f"prediction_date: {prediction_date}")
    printer(f"through_date: {through_date}")
    printer(f"repair: {repair_status}")
    printer(f"shadow artifacts {action_word}: yes")
    printer(f"daily summary {action_word}: yes")
    printer(f"quality summary {action_word}: yes")
    printer(f"completion audit {action_word}: yes")
    printer(f"operator card {action_word}: yes")
    printer(f"artifact manifest {action_word}: yes")
    printer("no prediction boards regenerated: yes")
    printer("no Kelly stakes regenerated: yes")


def refresh_closed_slate_reports(
    *,
    prediction_date: str,
    through_date: str | None = None,
    include_current_date: bool = True,
    skip_repair: bool = False,
    dry_run: bool = False,
    force_operator_card: bool = True,
    runtime_root: str | Path = "outputs/runtime",
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    printer: Callable[[str], None] = print,
) -> int:
    prediction_date = _validate_date(prediction_date, field_name="prediction-date")
    through_date = _validate_date(
        through_date or prediction_date,
        field_name="through-date",
    )

    missing = missing_required_artifacts(prediction_date, runtime_root=runtime_root)
    if missing:
        _print_missing_artifacts(missing, printer=printer)
        return 1

    commands = build_refresh_commands(
        prediction_date=prediction_date,
        through_date=through_date,
        include_current_date=include_current_date,
        skip_repair=skip_repair,
        force_operator_card=force_operator_card,
    )

    if dry_run:
        printer("Dry run: commands that would run")
        for refresh_command in commands:
            printer(f"would_run {_format_command(refresh_command.command)}")
        _print_success_summary(
            prediction_date=prediction_date,
            through_date=through_date,
            repair_status="skipped" if skip_repair else "would run",
            dry_run=True,
            printer=printer,
        )
        return 0

    for refresh_command in commands:
        printer(f"running {refresh_command.label}: {_format_command(refresh_command.command)}")
        result = runner(refresh_command.command, cwd=ROOT_DIR)
        if result.returncode != 0:
            printer(
                f"stopped {refresh_command.label}: exit_code={result.returncode}"
            )
            return result.returncode or 1

    _print_success_summary(
        prediction_date=prediction_date,
        through_date=through_date,
        repair_status="skipped" if skip_repair else "ran",
        dry_run=False,
        printer=printer,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Safely refresh closed-slate CourtVision reports without rebuilding picks or stakes."
    )
    parser.add_argument("--prediction-date", required=True, type=_date_arg)
    parser.add_argument("--through-date", type=_date_arg)
    parser.add_argument(
        "--include-current-date",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pass --include-current-date to repair_pending_grades.py; enabled by default.",
    )
    parser.add_argument("--skip-repair", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force-operator-card",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pass --force to write_operator_card.py; enabled by default.",
    )
    args = parser.parse_args(argv)

    return refresh_closed_slate_reports(
        prediction_date=args.prediction_date,
        through_date=args.through_date,
        include_current_date=args.include_current_date,
        skip_repair=args.skip_repair,
        dry_run=args.dry_run,
        force_operator_card=args.force_operator_card,
    )


if __name__ == "__main__":
    raise SystemExit(main())
