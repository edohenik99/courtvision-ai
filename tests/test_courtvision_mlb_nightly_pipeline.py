from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

from tools.courtvision_mlb_nightly_pipeline import (
    CompletedCommand,
    PipelinePaths,
    SecretMasker,
    run_pipeline,
)


MASTER_COLUMNS = [
    "snapshot_time",
    "event_id",
    "commence_time",
    "home_team",
    "away_team",
    "bookmaker_key",
    "bookmaker",
    "bookmaker_last_update",
    "market",
    "market_last_update",
    "player",
    "side",
    "price",
    "point",
    "hr_label",
]


class FakeRunner:
    def __init__(
        self,
        *,
        coverage_ready: bool = True,
        void_candidate_rows: int = 0,
        manual_review_rows: int = 0,
        fail_stage: str | None = None,
        secret_output: str = "",
    ) -> None:
        self.coverage_ready = coverage_ready
        self.void_candidate_rows = void_candidate_rows
        self.manual_review_rows = manual_review_rows
        self.fail_stage = fail_stage
        self.secret_output = secret_output
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(
        self, args: Sequence[str], *, cwd: Path, stage: str
    ) -> CompletedCommand:
        command = tuple(str(arg) for arg in args)
        self.calls.append((stage, command))
        if stage == self.fail_stage:
            return CompletedCommand(command, 7, stdout="failure")
        output = self._output_for_stage(stage)
        if self.secret_output:
            output += f"\nURL: https://example.test/?apiKey={self.secret_output}"
        return CompletedCommand(command, 0, stdout=output)

    def _output_for_stage(self, stage: str) -> str:
        if "live_hr_daily_check" in stage:
            return "Live HR daily check: VALID\nRows: 1\nDuplicates: 0\n"
        if stage == "generate_results_workbook":
            return "Generated results workbook: workbook.csv\nRows: 1\n"
        if stage.startswith("fill_results"):
            return (
                "MLB StatsAPI HR result fill\n"
                "Games matched: 1\n"
                "Games final: 1\n"
                "Rows filled: 1\n"
                "Rows skipped: 0\n"
                "Unmatched games: 0\n"
                "Unmatched players: 0\n"
            )
        if stage == "export_strict_results":
            return "Exported strict results CSV: results.csv\nRows: 1\n"
        if stage.startswith("coverage"):
            ready = "YES" if self.coverage_ready else "NO"
            missing = 0 if self.coverage_ready else 1
            non_gradeable = self.void_candidate_rows + self.manual_review_rows
            return (
                "Live HR results coverage\n"
                f"Rows: {1 + non_gradeable}\n"
                "Void rows: 0\n"
                f"Void candidate rows: {self.void_candidate_rows}\n"
                f"Manual review rows: {self.manual_review_rows}\n"
                f"Non-gradeable rows: {non_gradeable}\n"
                "Gradeable rows: 1\n"
                "Missing event_id: 0\n"
                "Missing player: 0\n"
                f"Missing actual_home_runs: {missing}\n"
                "Missing game_status: 0\n"
                "Invalid actual_home_runs: 0\n"
                "Invalid game_status: 0\n"
                f"Ready to grade: {ready}\n"
            )
        if stage.startswith("grade"):
            return (
                "Target date: 2026-07-09\n"
                "Total rows: 1\n"
                "Graded rows: 1\n"
                "Missing result rows: 0\n"
                "Excluded void rows: 0\n"
                f"Void candidate rows: {self.void_candidate_rows}\n"
                f"Manual review rows: {self.manual_review_rows}\n"
                "Wins: 1\n"
                "Losses: 0\n"
            )
        if stage.startswith("summarize"):
            return "MLB live HR grade summary: 2026-07-09\nRows: 1 graded\n"
        return "ok\n"

    def stages(self) -> list[str]:
        return [stage for stage, _ in self.calls]

    def commands(self) -> list[tuple[str, ...]]:
        return [command for _, command in self.calls]


def _paths(tmp_path: Path) -> PipelinePaths:
    snapshot_dir = tmp_path / "data" / "theoddsapi" / "live_hr_snapshots"
    return PipelinePaths(
        repo_root=tmp_path,
        snapshot_dir=snapshot_dir,
        master_csv=snapshot_dir / "live_hr_props_master.csv",
        workbook_csv=snapshot_dir / "live_hr_results_workbook.csv",
        results_csv=snapshot_dir / "live_hr_results.csv",
        reports_dir=snapshot_dir / "reports",
        log_dir=snapshot_dir / "automation_logs",
    )


def _write_master(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MASTER_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _master_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "snapshot_time": "2026-07-09T16:00:00Z",
        "event_id": "event-1",
        "commence_time": "2026-07-09T23:10:00Z",
        "home_team": "New York Yankees",
        "away_team": "Boston Red Sox",
        "bookmaker_key": "draftkings",
        "bookmaker": "DraftKings",
        "bookmaker_last_update": "2026-07-09T16:00:00Z",
        "market": "batter_home_runs_alternate",
        "market_last_update": "2026-07-09T16:00:00Z",
        "player": "Aaron Judge",
        "side": "Over",
        "price": 300,
        "point": 0.5,
        "hr_label": "1+ HR",
    }
    row.update(overrides)
    return row


def _write_workbook(path: Path, *, actual: str = "", status: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "commence_time",
        "home_team",
        "away_team",
        "event_id",
        "player",
        "books_available",
        "best_bookmaker",
        "best_price",
        "all_prices",
        "actual_home_runs",
        "game_status",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerow(
            {
                "commence_time": "2026-07-09T23:10:00Z",
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "event_id": "event-1",
                "player": "Aaron Judge",
                "books_available": "DraftKings",
                "best_bookmaker": "DraftKings",
                "best_price": "300",
                "all_prices": "DraftKings: 300",
                "actual_home_runs": actual,
                "game_status": status,
            }
        )


def _run(
    tmp_path: Path,
    runner: FakeRunner,
    *,
    target_dates: list[str] | None = None,
) -> dict[str, object]:
    result = run_pipeline(
        paths=_paths(tmp_path),
        runner=runner,
        run_id="20260710_033000",
        started_at=datetime(2026, 7, 10, 3, 30),
        today=date(2026, 7, 10),
        target_dates=target_dates or ["2026-07-09"],
        skip_git=True,
    )
    assert result.json_summary_path.exists()
    assert result.text_summary_path.exists()
    return result.summary


def test_no_games_available_exits_successfully_without_grading(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_master(paths.master_csv, [])
    runner = FakeRunner()

    summary = _run(tmp_path, runner)

    assert summary["overall_success"] is True
    assert summary["dates_processed"] == []
    assert summary["dates_skipped"] == [
        {"date": "2026-07-09", "reason": "no_data"}
    ]
    assert not any(stage.startswith("grade") for stage in runner.stages())


def test_incomplete_coverage_skips_grader(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_master(paths.master_csv, [_master_row()])
    runner = FakeRunner(coverage_ready=False)

    summary = _run(tmp_path, runner)

    assert summary["overall_success"] is True
    assert summary["dates_graded"] == []
    assert summary["dates_skipped"][0]["reason"] == "coverage_incomplete"
    assert not any(stage.startswith("grade") for stage in runner.stages())


def test_complete_coverage_calls_grader_and_summary(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_master(paths.master_csv, [_master_row()])
    runner = FakeRunner(coverage_ready=True)

    summary = _run(tmp_path, runner)

    assert summary["dates_graded"] == ["2026-07-09"]
    assert any(stage == "grade_2026-07-09" for stage in runner.stages())
    assert any(stage == "summarize_grades_2026-07-09" for stage in runner.stages())
    assert len(summary["grading_output_paths"]) == 2


def test_repeated_run_uses_stable_outputs_and_no_append_commands(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write_master(paths.master_csv, [_master_row()])
    first_runner = FakeRunner()
    second_runner = FakeRunner()

    first = _run(tmp_path, first_runner)
    second = _run(tmp_path, second_runner)

    assert first["grading_output_paths"] == second["grading_output_paths"]
    assert all("--overwrite-filled" not in command for command in first_runner.commands())
    assert all("--force" not in command for command in first_runner.commands())


def test_existing_results_are_preserved_by_command_shape_and_counted(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write_master(paths.master_csv, [_master_row()])
    _write_workbook(paths.workbook_csv, actual="2", status="final")
    runner = FakeRunner()

    summary = _run(tmp_path, runner)

    generate_command = next(
        command
        for stage, command in runner.calls
        if stage == "generate_results_workbook"
    )
    fill_command = next(
        command for stage, command in runner.calls if stage.startswith("fill_results")
    )
    assert "--preserve-results" in generate_command
    assert "--overwrite-filled" not in fill_command
    assert summary["results_preserved"] == 1


def test_grader_not_called_when_coverage_is_incomplete(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_master(paths.master_csv, [_master_row()])
    runner = FakeRunner(coverage_ready=False)

    _run(tmp_path, runner)

    assert "grade_2026-07-09" not in runner.stages()


def test_grader_called_when_coverage_is_complete(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_master(paths.master_csv, [_master_row()])
    runner = FakeRunner(coverage_ready=True)

    _run(tmp_path, runner)

    assert "grade_2026-07-09" in runner.stages()


def test_void_candidate_coverage_still_calls_grader(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_master(paths.master_csv, [_master_row()])
    runner = FakeRunner(coverage_ready=True, void_candidate_rows=1)

    summary = _run(tmp_path, runner)

    assert summary["dates_graded"] == ["2026-07-09"]
    assert summary["void_candidate_rows"] == 1
    assert summary["manual_review_rows"] == 0
    coverage = summary["coverage_status_per_date"]["2026-07-09"]
    assert coverage["ready_to_grade"] is True
    assert coverage["void_candidate_rows"] == 1
    assert "grade_2026-07-09" in runner.stages()


def test_child_script_failure_propagates_nonzero_exit_code(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_master(paths.master_csv, [_master_row()])
    runner = FakeRunner(fail_stage="export_strict_results")

    result = run_pipeline(
        paths=paths,
        runner=runner,
        run_id="20260710_033000",
        started_at=datetime(2026, 7, 10, 3, 30),
        today=date(2026, 7, 10),
        target_dates=["2026-07-09"],
        skip_git=True,
    )

    assert result.exit_code == 1
    assert result.summary["overall_success"] is False
    assert result.summary["exports"]["strict_results"]["success"] is False
    assert "export_strict_results" in result.summary["errors"][0]


def test_api_keys_are_not_exposed_in_logs_or_summary(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_master(paths.master_csv, [_master_row()])
    secret = "sk_live_should_not_log"
    runner = FakeRunner(secret_output=secret)
    masker = SecretMasker(env={"THE_ODDS_API_KEY": secret})

    result = run_pipeline(
        paths=paths,
        runner=runner,
        run_id="20260710_033000",
        started_at=datetime(2026, 7, 10, 3, 30),
        today=date(2026, 7, 10),
        target_dates=["2026-07-09"],
        skip_git=True,
        masker=masker,
    )

    log_text = "\n".join(result.log_lines)
    json_text = result.json_summary_path.read_text(encoding="utf-8")
    assert secret not in log_text
    assert secret not in json_text
    assert "apiKey=[MASKED]" in log_text
