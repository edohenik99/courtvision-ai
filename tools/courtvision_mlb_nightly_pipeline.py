"""Orchestrate the nightly CourtVision MLB live HR finalization pipeline."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping, Protocol, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = PROJECT_ROOT / "data" / "theoddsapi" / "live_hr_snapshots"
SUMMARY_PREFIX = "mlb_nightly_summary"
NON_GRADEABLE_RESULT_STATUSES = {
    "void",
    "void_candidate",
    "manual_review_required",
}


@dataclass(frozen=True)
class PipelinePaths:
    repo_root: Path
    snapshot_dir: Path
    master_csv: Path
    workbook_csv: Path
    results_csv: Path
    reports_dir: Path
    log_dir: Path

    @classmethod
    def defaults(cls, repo_root: Path = PROJECT_ROOT) -> "PipelinePaths":
        snapshot_dir = repo_root / "data" / "theoddsapi" / "live_hr_snapshots"
        return cls(
            repo_root=repo_root,
            snapshot_dir=snapshot_dir,
            master_csv=snapshot_dir / "live_hr_props_master.csv",
            workbook_csv=snapshot_dir / "live_hr_results_workbook.csv",
            results_csv=snapshot_dir / "live_hr_results.csv",
            reports_dir=snapshot_dir / "reports",
            log_dir=snapshot_dir / "automation_logs",
        )

    def for_dry_run(self, run_id: str) -> "PipelinePaths":
        dry_run_dir = self.log_dir / f"dry_run_{run_id}"
        return PipelinePaths(
            repo_root=self.repo_root,
            snapshot_dir=dry_run_dir,
            master_csv=dry_run_dir / self.master_csv.name,
            workbook_csv=dry_run_dir / self.workbook_csv.name,
            results_csv=dry_run_dir / self.results_csv.name,
            reports_dir=dry_run_dir / "reports",
            log_dir=self.log_dir,
        )


@dataclass(frozen=True)
class CompletedCommand:
    args: tuple[str, ...]
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def combined_output(self) -> str:
        if self.stdout and self.stderr:
            return f"{self.stdout.rstrip()}\n{self.stderr.rstrip()}"
        return self.stdout or self.stderr


class CommandRunner(Protocol):
    def run(
        self, args: Sequence[str], *, cwd: Path, stage: str
    ) -> CompletedCommand:
        ...


class SubprocessCommandRunner:
    def run(
        self, args: Sequence[str], *, cwd: Path, stage: str
    ) -> CompletedCommand:
        completed = subprocess.run(
            [str(arg) for arg in args],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
        )
        return CompletedCommand(
            args=tuple(str(arg) for arg in args),
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


class CommandFailure(RuntimeError):
    def __init__(
        self,
        stage: str,
        command: Sequence[str],
        exit_code: int,
        output: str = "",
    ) -> None:
        self.stage = stage
        self.command = tuple(str(arg) for arg in command)
        self.exit_code = exit_code
        self.output = output
        super().__init__(
            f"Stage {stage!r} failed with exit code {exit_code}: "
            + format_command(self.command)
        )


class SecretMasker:
    ENV_MARKERS = ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        extra_values: Sequence[str] = (),
    ) -> None:
        env = env or os.environ
        secrets: list[str] = []
        for name, value in env.items():
            upper_name = name.upper()
            if not value or len(value) < 4:
                continue
            if any(marker in upper_name for marker in self.ENV_MARKERS):
                secrets.append(value)
        secrets.extend(value for value in extra_values if value and len(value) >= 4)
        self._secrets = tuple(sorted(set(secrets), key=len, reverse=True))

    def mask(self, value: object) -> str:
        text = str(value)
        for secret in self._secrets:
            text = text.replace(secret, "[MASKED]")
        text = re.sub(r"(?i)(apiKey=)[^\s&]+", r"\1[MASKED]", text)
        text = re.sub(r"(?i)(api_key=)[^\s&]+", r"\1[MASKED]", text)
        return text


class PipelineLog:
    def __init__(self, masker: SecretMasker) -> None:
        self.masker = masker
        self.lines: list[str] = []

    def write(self, message: object = "") -> None:
        line = self.masker.mask(message)
        self.lines.append(line)
        print(line)

    def stage(self, message: str) -> None:
        self.write("")
        self.write(f"=== {message} ===")


@dataclass(frozen=True)
class MasterInfo:
    exists: bool
    row_count: int
    odds_rows_by_date: dict[str, int]
    event_ids_by_date: dict[str, set[str]]


@dataclass(frozen=True)
class PipelineResult:
    exit_code: int
    summary: dict[str, object]
    json_summary_path: Path
    text_summary_path: Path
    log_lines: tuple[str, ...]


def format_command(command: Sequence[str]) -> str:
    parts = []
    for arg in command:
        text = str(arg)
        if not text:
            parts.append('""')
        elif re.search(r"\s", text):
            parts.append(f'"{text}"')
        else:
            parts.append(text)
    return " ".join(parts)


def _script(name: str) -> str:
    return str(Path("tools") / name)


def _parse_iso_date(value: object, *, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} has blank commence_time")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} has invalid commence_time: {text!r}") from exc


def load_master_info(master_csv: Path) -> MasterInfo:
    if not master_csv.exists():
        return MasterInfo(
            exists=False,
            row_count=0,
            odds_rows_by_date={},
            event_ids_by_date={},
        )

    odds_rows_by_date: Counter[str] = Counter()
    event_ids_by_date: defaultdict[str, set[str]] = defaultdict(set)
    row_count = 0
    with master_csv.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        missing = [
            column
            for column in ("event_id", "commence_time")
            if column not in fieldnames
        ]
        if missing:
            raise ValueError(
                f"Master CSV is missing required columns for date selection: {missing}"
            )

        for row_number, row in enumerate(reader, start=2):
            row_count += 1
            row_date = _parse_iso_date(
                row.get("commence_time"), label=f"Master CSV row {row_number}"
            )
            odds_rows_by_date[row_date] += 1
            event_id = str(row.get("event_id") or "").strip()
            if event_id:
                event_ids_by_date[row_date].add(event_id)

    return MasterInfo(
        exists=True,
        row_count=row_count,
        odds_rows_by_date=dict(sorted(odds_rows_by_date.items())),
        event_ids_by_date=dict(sorted(event_ids_by_date.items())),
    )


def validate_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date {value!r}; expected YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"Invalid date {value!r}; expected YYYY-MM-DD")
    return value


def select_processing_dates(
    master_info: MasterInfo,
    *,
    today: date,
    target_dates: Sequence[str] | None,
    lookback_days: int,
) -> list[str]:
    if lookback_days < 1:
        raise ValueError("--lookback-days must be at least 1")

    if target_dates:
        return sorted({validate_date(value) for value in target_dates})

    yesterday = today - timedelta(days=1)
    start_date = today - timedelta(days=lookback_days)
    dates = {
        row_date
        for row_date in master_info.odds_rows_by_date
        if start_date <= date.fromisoformat(row_date) <= yesterday
    }
    dates.add(yesterday.isoformat())
    return sorted(dates)


def parse_int_line(output: str, label: str) -> int | None:
    pattern = re.compile(rf"^\s*{re.escape(label)}\s*:\s*(-?\d+)\s*$", re.I | re.M)
    match = pattern.search(output)
    return int(match.group(1)) if match else None


def parse_ready_to_grade(output: str) -> bool | None:
    match = re.search(r"Ready to grade:\s*(YES|NO)", output, flags=re.I)
    if not match:
        return None
    return match.group(1).upper() == "YES"


def parse_fill_summary(output: str) -> dict[str, int]:
    labels = {
        "games_matched": "Games matched",
        "games_final": "Games final",
        "rows_filled": "Rows filled",
        "rows_skipped": "Rows skipped",
        "unmatched_games": "Unmatched games",
        "unmatched_players": "Unmatched players",
    }
    return {
        key: parse_int_line(output, label) or 0
        for key, label in labels.items()
    }


def parse_export_summary(output: str) -> dict[str, int]:
    return {"rows": parse_int_line(output, "Rows") or 0}


def parse_coverage_summary(output: str) -> dict[str, int | bool]:
    labels = {
        "total_rows": "Rows",
        "void_rows": "Void rows",
        "void_candidate_rows": "Void candidate rows",
        "manual_review_rows": "Manual review rows",
        "non_gradeable_rows": "Non-gradeable rows",
        "gradeable_rows": "Gradeable rows",
        "missing_event_id": "Missing event_id",
        "missing_player": "Missing player",
        "missing_actual_home_runs": "Missing actual_home_runs",
        "missing_game_status": "Missing game_status",
        "invalid_actual_home_runs": "Invalid actual_home_runs",
        "invalid_game_status": "Invalid game_status",
    }
    report: dict[str, int | bool] = {
        key: parse_int_line(output, label) or 0
        for key, label in labels.items()
    }
    ready = parse_ready_to_grade(output)
    report["ready_to_grade"] = bool(ready)
    return report


def parse_grade_summary(output: str) -> dict[str, int]:
    labels = {
        "total_rows": "Total rows",
        "graded_rows": "Graded rows",
        "missing_result_rows": "Missing result rows",
        "excluded_void_rows": "Excluded void rows",
        "void_candidate_rows": "Void candidate rows",
        "manual_review_rows": "Manual review rows",
        "wins": "Wins",
        "losses": "Losses",
    }
    return {
        key: parse_int_line(output, label) or 0
        for key, label in labels.items()
    }


def resolved_workbook_rows(workbook_csv: Path, event_ids: set[str]) -> int:
    if not workbook_csv.exists() or not event_ids:
        return 0
    with workbook_csv.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return 0
        required = {"event_id", "actual_home_runs", "game_status"}
        if not required.issubset(set(reader.fieldnames)):
            return 0
        resolved = 0
        for row in reader:
            event_id = str(row.get("event_id") or "").strip()
            if event_id not in event_ids:
                continue
            game_status = str(row.get("game_status") or "").strip().casefold()
            actual_home_runs = str(row.get("actual_home_runs") or "").strip()
            if game_status in NON_GRADEABLE_RESULT_STATUSES or (
                game_status == "final" and actual_home_runs != ""
            ):
                resolved += 1
        return resolved


def copy_dry_run_inputs(source: PipelinePaths, target: PipelinePaths) -> None:
    target.snapshot_dir.mkdir(parents=True, exist_ok=True)
    target.reports_dir.mkdir(parents=True, exist_ok=True)
    if source.master_csv.exists():
        shutil.copy2(source.master_csv, target.master_csv)
    if source.workbook_csv.exists():
        shutil.copy2(source.workbook_csv, target.workbook_csv)
    if source.results_csv.exists():
        shutil.copy2(source.results_csv, target.results_csv)


def run_checked(
    runner: CommandRunner,
    command: Sequence[str],
    *,
    cwd: Path,
    stage: str,
    log: PipelineLog,
    summary: dict[str, object],
) -> CompletedCommand:
    log.stage(stage.replace("_", " ").title())
    log.write("> " + log.masker.mask(format_command(command)))
    completed = runner.run(command, cwd=cwd, stage=stage)
    masked_output = log.masker.mask(completed.combined_output)
    if masked_output.strip():
        for line in masked_output.rstrip().splitlines():
            log.write(line)
    log.write(f"Exit code: {completed.exit_code}")

    commands = summary.setdefault("commands", [])
    assert isinstance(commands, list)
    commands.append(
        {
            "stage": stage,
            "command": log.masker.mask(format_command(command)),
            "exit_code": completed.exit_code,
        }
    )

    if completed.exit_code != 0:
        raise CommandFailure(stage, command, completed.exit_code, masked_output)
    return CompletedCommand(
        args=completed.args,
        exit_code=completed.exit_code,
        stdout=log.masker.mask(completed.stdout),
        stderr=log.masker.mask(completed.stderr),
    )


def grade_output_path(paths: PipelinePaths, target_date: str) -> Path:
    return paths.snapshot_dir / f"live_hr_grades_{target_date.replace('-', '')}.csv"


def grade_report_path(paths: PipelinePaths, target_date: str) -> Path:
    return paths.reports_dir / (
        f"live_hr_grade_summary_{target_date.replace('-', '')}.md"
    )


def _summary_paths(paths: PipelinePaths, run_id: str) -> tuple[Path, Path]:
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    return (
        paths.log_dir / f"{SUMMARY_PREFIX}_{run_id}.json",
        paths.log_dir / f"{SUMMARY_PREFIX}_{run_id}.txt",
    )


def _render_text_summary(summary: Mapping[str, object]) -> str:
    lines = [
        "CourtVision MLB nightly pipeline summary",
        f"Run ID: {summary.get('run_id')}",
        f"Started: {summary.get('run_start_time')}",
        f"Ended: {summary.get('run_end_time')}",
        f"Dry run: {summary.get('dry_run')}",
        f"Overall success: {summary.get('overall_success')}",
        f"Git pull result: {summary.get('git_pull_result')}",
        f"Dates considered: {summary.get('dates_considered')}",
        f"Dates processed: {summary.get('dates_processed')}",
        f"Dates graded: {summary.get('dates_graded')}",
        f"Dates skipped: {summary.get('dates_skipped')}",
        f"Games processed: {summary.get('games_processed')}",
        f"Rows processed: {summary.get('rows_processed')}",
        f"Results filled: {summary.get('results_filled')}",
        f"Results preserved: {summary.get('results_preserved')}",
        f"Unmatched players: {summary.get('unmatched_players')}",
        f"Void candidates: {summary.get('void_candidate_rows')}",
        f"Manual review rows: {summary.get('manual_review_rows')}",
        f"Duplicate count: {summary.get('duplicate_count')}",
        f"Coverage status per date: {summary.get('coverage_status_per_date')}",
        f"Grading output paths: {summary.get('grading_output_paths')}",
        f"Warnings: {summary.get('warnings')}",
        f"Errors: {summary.get('errors')}",
    ]
    return "\n".join(lines) + "\n"


def write_summary_files(
    summary: dict[str, object], paths: PipelinePaths, run_id: str
) -> tuple[Path, Path]:
    json_path, text_path = _summary_paths(paths, run_id)
    json_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    text_path.write_text(_render_text_summary(summary), encoding="utf-8")
    return json_path, text_path


def run_pipeline(
    *,
    paths: PipelinePaths,
    runner: CommandRunner,
    run_id: str,
    started_at: datetime,
    today: date | None = None,
    target_dates: Sequence[str] | None = None,
    lookback_days: int = 3,
    dry_run: bool = False,
    skip_git: bool = False,
    masker: SecretMasker | None = None,
) -> PipelineResult:
    original_cwd = Path.cwd()
    masker = masker or SecretMasker()
    log = PipelineLog(masker)
    today = today or started_at.date()
    json_summary_path, text_summary_path = _summary_paths(paths, run_id)
    effective_paths = paths
    exit_code = 0
    summary: dict[str, object] = {
        "run_id": run_id,
        "dry_run": dry_run,
        "run_start_time": started_at.isoformat(timespec="seconds"),
        "run_end_time": "",
        "repo_root": str(paths.repo_root),
        "git_pull_result": {"status": "not_started"},
        "dates_considered": [],
        "dates_processed": [],
        "games_processed": 0,
        "rows_processed": 0,
        "results_filled": 0,
        "results_preserved": 0,
        "unmatched_players": 0,
        "void_candidate_rows": 0,
        "manual_review_rows": 0,
        "duplicate_count": None,
        "coverage_status_per_date": {},
        "dates_graded": [],
        "dates_skipped": [],
        "grading_output_paths": [],
        "warnings": [],
        "errors": [],
        "per_date": {},
        "exports": {},
        "overall_success": False,
    }

    warnings = summary["warnings"]
    errors = summary["errors"]
    assert isinstance(warnings, list)
    assert isinstance(errors, list)

    try:
        paths.log_dir.mkdir(parents=True, exist_ok=True)
        paths.reports_dir.mkdir(parents=True, exist_ok=True)
        log.write(
            "CourtVision MLB nightly pipeline started at "
            f"{started_at.isoformat(timespec='seconds')}"
        )
        log.write(f"Run ID: {run_id}")
        log.write(f"Dry run: {dry_run}")

        if not paths.repo_root.exists():
            raise FileNotFoundError(f"Repository root not found: {paths.repo_root}")
        os.chdir(paths.repo_root)

        if skip_git:
            summary["git_pull_result"] = {"status": "skipped"}
            warnings.append("Git checkout/pull skipped by explicit operator flag.")
            log.stage("Git")
            log.write("Git checkout/pull skipped by explicit operator flag.")
        else:
            run_checked(
                runner,
                ("git", "checkout", "main"),
                cwd=paths.repo_root,
                stage="git_checkout_main",
                log=log,
                summary=summary,
            )
            pull = run_checked(
                runner,
                ("git", "pull", "origin", "main"),
                cwd=paths.repo_root,
                stage="git_pull_origin_main",
                log=log,
                summary=summary,
            )
            pull_output = pull.combined_output.strip()
            summary["git_pull_result"] = {
                "status": "success",
                "exit_code": pull.exit_code,
                "output": pull_output,
            }

        master_info = load_master_info(paths.master_csv)
        selected_dates = select_processing_dates(
            master_info,
            today=today,
            target_dates=target_dates,
            lookback_days=lookback_days,
        )
        summary["dates_considered"] = selected_dates
        log.stage("Date Selection")
        log.write(f"Selected dates: {', '.join(selected_dates) or '(none)'}")

        if dry_run:
            effective_paths = paths.for_dry_run(run_id)
            copy_dry_run_inputs(paths, effective_paths)
            log.write(f"Dry-run workspace: {effective_paths.snapshot_dir}")
            master_info = load_master_info(effective_paths.master_csv)

        dates_with_rows = [
            item for item in selected_dates if master_info.odds_rows_by_date.get(item, 0) > 0
        ]

        if not master_info.exists:
            message = f"Master odds CSV not found: {paths.master_csv}"
            warnings.append(message)
            log.write(message)

        if master_info.exists:
            health = run_checked(
                runner,
                ("python", _script("run_live_hr_daily_check.py"), str(effective_paths.master_csv)),
                cwd=paths.repo_root,
                stage="preflight_live_hr_daily_check",
                log=log,
                summary=summary,
            )
            duplicate_count = parse_int_line(health.combined_output, "Duplicates")
            summary["duplicate_count"] = duplicate_count if duplicate_count is not None else 0

        if not dates_with_rows:
            for target_date in selected_dates:
                skipped = {"date": target_date, "reason": "no_data"}
                cast_list = summary["dates_skipped"]
                assert isinstance(cast_list, list)
                cast_list.append(skipped)
                warning = f"No live HR master rows found for {target_date}; skipping."
                warnings.append(warning)
                log.write(warning)
        else:
            generated = run_checked(
                runner,
                (
                    "python",
                    _script("generate_live_hr_results_workbook.py"),
                    "--input",
                    str(effective_paths.master_csv),
                    "--output",
                    str(effective_paths.workbook_csv),
                    "--overwrite",
                    "--preserve-results",
                ),
                cwd=paths.repo_root,
                stage="generate_results_workbook",
                log=log,
                summary=summary,
            )
            workbook_rows = parse_int_line(generated.combined_output, "Rows")
            summary["workbook_rows"] = workbook_rows if workbook_rows is not None else 0

            for target_date in selected_dates:
                event_ids = master_info.event_ids_by_date.get(target_date, set())
                odds_rows = master_info.odds_rows_by_date.get(target_date, 0)
                game_count = len(event_ids)
                date_summary: dict[str, object] = {
                    "date": target_date,
                    "games_processed": game_count,
                    "rows_processed": odds_rows,
                    "results_filled": 0,
                    "results_preserved": 0,
                    "unmatched_players": 0,
                    "void_candidate_rows": 0,
                    "manual_review_rows": 0,
                    "coverage": None,
                    "graded": False,
                    "grading_output_path": "",
                    "status": "pending",
                }
                per_date = summary["per_date"]
                assert isinstance(per_date, dict)
                per_date[target_date] = date_summary

                if not event_ids:
                    date_summary["status"] = "skipped_no_data"
                    skipped = {"date": target_date, "reason": "no_data"}
                    cast_list = summary["dates_skipped"]
                    assert isinstance(cast_list, list)
                    cast_list.append(skipped)
                    warnings.append(
                        f"No live HR master rows found for {target_date}; skipping."
                    )
                    continue

                date_summary["results_preserved"] = resolved_workbook_rows(
                    effective_paths.workbook_csv, event_ids
                )
                processed = summary["dates_processed"]
                assert isinstance(processed, list)
                processed.append(target_date)
                summary["games_processed"] = int(summary["games_processed"]) + game_count
                summary["rows_processed"] = int(summary["rows_processed"]) + odds_rows

                fill = run_checked(
                    runner,
                    (
                        "python",
                        _script("fill_live_hr_results_from_mlb_statsapi.py"),
                        "--date",
                        target_date,
                        "--workbook",
                        str(effective_paths.workbook_csv),
                        "--diagnostic",
                        "--diagnostic-report-dir",
                        str(effective_paths.log_dir),
                    ),
                    cwd=paths.repo_root,
                    stage=f"fill_results_{target_date}",
                    log=log,
                    summary=summary,
                )
                fill_summary = parse_fill_summary(fill.combined_output)
                date_summary.update(
                    {
                        "results_filled": fill_summary["rows_filled"],
                        "unmatched_players": fill_summary["unmatched_players"],
                        "fill": fill_summary,
                    }
                )
                summary["results_filled"] = (
                    int(summary["results_filled"]) + fill_summary["rows_filled"]
                )
                summary["results_preserved"] = (
                    int(summary["results_preserved"])
                    + int(date_summary["results_preserved"])
                )
                summary["unmatched_players"] = (
                    int(summary["unmatched_players"])
                    + fill_summary["unmatched_players"]
                )

            export = run_checked(
                runner,
                (
                    "python",
                    _script("export_live_hr_results_from_workbook.py"),
                    "--input",
                    str(effective_paths.workbook_csv),
                    "--output",
                    str(effective_paths.results_csv),
                    "--overwrite",
                ),
                cwd=paths.repo_root,
                stage="export_strict_results",
                log=log,
                summary=summary,
            )
            export_summary = parse_export_summary(export.combined_output)
            summary["exports"] = {
                "strict_results": {
                    "success": True,
                    "path": str(effective_paths.results_csv),
                    **export_summary,
                }
            }

            for target_date in selected_dates:
                event_ids = master_info.event_ids_by_date.get(target_date, set())
                if not event_ids:
                    continue

                coverage = run_checked(
                    runner,
                    (
                        "python",
                        _script("check_live_hr_results_coverage.py"),
                        "--results",
                        str(effective_paths.results_csv),
                        "--odds-csv",
                        str(effective_paths.master_csv),
                        "--date",
                        target_date,
                    ),
                    cwd=paths.repo_root,
                    stage=f"coverage_{target_date}",
                    log=log,
                    summary=summary,
                )
                coverage_summary = parse_coverage_summary(coverage.combined_output)
                coverage_by_date = summary["coverage_status_per_date"]
                assert isinstance(coverage_by_date, dict)
                coverage_by_date[target_date] = coverage_summary
                per_date = summary["per_date"]
                assert isinstance(per_date, dict)
                date_summary = per_date[target_date]
                assert isinstance(date_summary, dict)
                date_summary["coverage"] = coverage_summary
                date_summary["void_candidate_rows"] = coverage_summary[
                    "void_candidate_rows"
                ]
                date_summary["manual_review_rows"] = coverage_summary[
                    "manual_review_rows"
                ]
                summary["void_candidate_rows"] = (
                    int(summary["void_candidate_rows"])
                    + int(coverage_summary["void_candidate_rows"])
                )
                summary["manual_review_rows"] = (
                    int(summary["manual_review_rows"])
                    + int(coverage_summary["manual_review_rows"])
                )

                if not bool(coverage_summary["ready_to_grade"]):
                    date_summary["status"] = "skipped_incomplete_coverage"
                    skipped = {
                        "date": target_date,
                        "reason": "coverage_incomplete",
                        "missing_actual_home_runs": coverage_summary[
                            "missing_actual_home_runs"
                        ],
                        "missing_game_status": coverage_summary["missing_game_status"],
                        "void_candidate_rows": coverage_summary[
                            "void_candidate_rows"
                        ],
                        "manual_review_rows": coverage_summary["manual_review_rows"],
                    }
                    skipped_dates = summary["dates_skipped"]
                    assert isinstance(skipped_dates, list)
                    skipped_dates.append(skipped)
                    warnings.append(
                        "Coverage incomplete for "
                        f"{target_date}; grader skipped."
                    )
                    continue

                output_path = grade_output_path(effective_paths, target_date)
                grade = run_checked(
                    runner,
                    (
                        "python",
                        _script("grade_live_hr_results.py"),
                        "--odds-csv",
                        str(effective_paths.master_csv),
                        "--results-csv",
                        str(effective_paths.results_csv),
                        "--output-csv",
                        str(output_path),
                        "--date",
                        target_date,
                    ),
                    cwd=paths.repo_root,
                    stage=f"grade_{target_date}",
                    log=log,
                    summary=summary,
                )
                date_summary["grade"] = parse_grade_summary(grade.combined_output)

                report_path = grade_report_path(effective_paths, target_date)
                run_checked(
                    runner,
                    (
                        "python",
                        _script("summarize_live_hr_grades.py"),
                        "--date",
                        target_date,
                        "--grade-csv",
                        str(output_path),
                        "--output",
                        str(report_path),
                    ),
                    cwd=paths.repo_root,
                    stage=f"summarize_grades_{target_date}",
                    log=log,
                    summary=summary,
                )
                date_summary.update(
                    {
                        "graded": True,
                        "grading_output_path": str(output_path),
                        "grade_summary_path": str(report_path),
                        "status": "graded",
                    }
                )
                graded_dates = summary["dates_graded"]
                assert isinstance(graded_dates, list)
                graded_dates.append(target_date)
                output_paths = summary["grading_output_paths"]
                assert isinstance(output_paths, list)
                output_paths.extend([str(output_path), str(report_path)])

            if master_info.exists:
                health = run_checked(
                    runner,
                    (
                        "python",
                        _script("run_live_hr_daily_check.py"),
                        str(effective_paths.master_csv),
                    ),
                    cwd=paths.repo_root,
                    stage="postflight_live_hr_daily_check",
                    log=log,
                    summary=summary,
                )
                duplicate_count = parse_int_line(health.combined_output, "Duplicates")
                summary["duplicate_count"] = (
                    duplicate_count if duplicate_count is not None else summary["duplicate_count"]
                )

        summary["overall_success"] = True
    except Exception as exc:
        exit_code = 1
        masked_error = masker.mask(str(exc))
        errors.append(masked_error)
        summary["overall_success"] = False
        if isinstance(exc, CommandFailure):
            if exc.stage == "git_pull_origin_main":
                summary["git_pull_result"] = {
                    "status": "failed",
                    "exit_code": exc.exit_code,
                    "output": exc.output.strip(),
                }
            elif exc.stage == "git_checkout_main":
                summary["git_pull_result"] = {
                    "status": "not_run",
                    "reason": "git checkout main failed",
                }

            if "live_hr_daily_check" in exc.stage:
                duplicate_count = parse_int_line(exc.output, "Duplicates")
                if duplicate_count is not None:
                    summary["duplicate_count"] = duplicate_count

            if exc.stage == "export_strict_results":
                summary["exports"] = {
                    "strict_results": {
                        "success": False,
                        "path": str(effective_paths.results_csv),
                        "error": masked_error,
                    }
                }
        log.write(f"ERROR: {masked_error}")
    finally:
        try:
            ended_at = datetime.now().astimezone()
            summary["run_end_time"] = ended_at.isoformat(timespec="seconds")
            json_summary_path, text_summary_path = write_summary_files(
                summary, paths, run_id
            )
            log.stage("Summary")
            log.write(f"JSON summary: {json_summary_path}")
            log.write(f"Text summary: {text_summary_path}")
        finally:
            os.chdir(original_cwd)

    return PipelineResult(
        exit_code=exit_code,
        summary=summary,
        json_summary_path=json_summary_path,
        text_summary_path=text_summary_path,
        log_lines=tuple(log.lines),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the CourtVision MLB live HR nightly finalization pipeline."
    )
    parser.add_argument(
        "--date",
        action="append",
        dest="target_dates",
        help="Process one completed MLB date in YYYY-MM-DD format. Repeatable.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=3,
        help="Completed-date lookback window when --date is omitted. Default: 3.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run against copies under automation_logs without mutating canonical "
            "workbook, results, grade, or report files."
        ),
    )
    parser.add_argument(
        "--run-id",
        help="Timestamp/run id used for summary file names. Default: current time.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=PROJECT_ROOT,
        help=f"Repository root. Default: {PROJECT_ROOT}",
    )
    parser.add_argument(
        "--skip-git",
        action="store_true",
        help=(
            "Skip git checkout/pull. Intended only for local dry-run validation "
            "with uncommitted work."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    started_at = datetime.now().astimezone()
    run_id = args.run_id or started_at.strftime("%Y%m%d_%H%M%S")
    paths = PipelinePaths.defaults(args.repo_root.resolve())
    result = run_pipeline(
        paths=paths,
        runner=SubprocessCommandRunner(),
        run_id=run_id,
        started_at=started_at,
        target_dates=args.target_dates,
        lookback_days=args.lookback_days,
        dry_run=args.dry_run,
        skip_git=args.skip_git,
    )
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
