from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path

from tools.generate_live_hr_daily_report import ReportPaths, generate_daily_report
from tools.validate_live_hr_data import REQUIRED_COLUMNS


def _master_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "snapshot_time": "2026-07-05T15:30:00Z",
        "event_id": "event-1",
        "commence_time": "2026-07-05T19:00:00Z",
        "home_team": "Toronto Blue Jays",
        "away_team": "New York Yankees",
        "bookmaker_key": "draftkings",
        "bookmaker": "DraftKings",
        "bookmaker_last_update": "2026-07-05T15:29:00Z",
        "market": "batter_home_runs_alternate",
        "market_last_update": "2026-07-05T15:29:00Z",
        "player": "Aaron Judge",
        "side": "Over",
        "price": 300,
        "point": 0.5,
        "hr_label": "1+ HR",
    }
    row.update(overrides)
    return row


def _write_csv(
    path: Path, fieldnames: list[str] | tuple[str, ...], rows: list[dict[str, object]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _fixtures(tmp_path: Path) -> ReportPaths:
    paths = ReportPaths(
        master=tmp_path / "live_hr_props_master.csv",
        run_log=tmp_path / "run_log.csv",
        results=tmp_path / "live_hr_results.csv",
        results_workbook=tmp_path / "live_hr_results_workbook.csv",
        automation_logs=tmp_path / "automation_logs",
        final_automation_logs=tmp_path / "final_automation_logs",
        reports=tmp_path / "reports",
    )
    _write_csv(
        paths.master,
        REQUIRED_COLUMNS,
        [
            _master_row(),
            _master_row(
                event_id="event-2",
                player="Vladimir Guerrero Jr.",
                bookmaker_key="fanduel",
                bookmaker="FanDuel",
            ),
            _master_row(
                snapshot_time="2026-07-04T15:30:00Z",
                event_id="older-event",
                player="Older Player",
            ),
        ],
    )
    _write_csv(
        paths.run_log,
        [
            "run_date",
            "snapshot_time",
            "status",
            "credits_used_this_run",
            "credits_remaining",
            "snapshot_csv",
        ],
        [
            {
                "run_date": "2026-07-05",
                "snapshot_time": "2026-07-05T15:30:00Z",
                "status": "success",
                "credits_used_this_run": "8",
                "credits_remaining": "444",
                "snapshot_csv": "snapshot.csv",
            }
        ],
    )
    _write_csv(
        paths.results,
        ["event_id", "player", "actual_home_runs", "game_status"],
        [
            {
                "event_id": "event-1",
                "player": "Aaron Judge",
                "actual_home_runs": "1",
                "game_status": "final",
            },
            {
                "event_id": "event-2",
                "player": "Vladimir Guerrero Jr.",
                "actual_home_runs": "",
                "game_status": "",
            },
        ],
    )
    paths.automation_logs.mkdir()
    paths.final_automation_logs.mkdir()
    (paths.automation_logs / "daily.log").write_text("daily", encoding="utf-8")
    (paths.final_automation_logs / "final.log").write_text("final", encoding="utf-8")
    return paths


def test_report_is_generated_with_date_scoped_collection_counts(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)

    output, summary = generate_daily_report(
        date(2026, 7, 5),
        paths=paths,
        generated_at=datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc),
    )

    assert output == paths.reports / "live_hr_daily_report_20260705.md"
    assert output.is_file()
    assert summary["snapshot_rows"] == 2
    report = output.read_text(encoding="utf-8")
    assert "- Snapshot rows for that date: 2" in report
    assert "- Total master rows: 3" in report


def test_report_calculates_bookmaker_counts(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)

    output, _ = generate_daily_report(date(2026, 7, 5), paths=paths)

    report = output.read_text(encoding="utf-8")
    assert "  - draftkings: 1" in report
    assert "  - fanduel: 1" in report


def test_report_shows_missing_coverage_and_results_incomplete_action(
    tmp_path: Path,
) -> None:
    paths = _fixtures(tmp_path)

    output, summary = generate_daily_report(date(2026, 7, 5), paths=paths)

    report = output.read_text(encoding="utf-8")
    assert summary["ready_to_grade"] is False
    assert "- Total result rows: 2" in report
    assert "- Filled `actual_home_runs`: 1" in report
    assert "- Missing `actual_home_runs`: 1" in report
    assert "- Missing `game_status`: 1" in report
    assert "- Ready to grade: NO" in report
    assert "- Results incomplete." in report
