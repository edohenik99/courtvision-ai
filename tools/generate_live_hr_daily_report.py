"""Generate an offline daily operations report for live MLB HR props."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

if __package__:
    from .check_live_hr_results_coverage import check_results_coverage
    from .validate_live_hr_data import IDENTITY_FIELDS
else:
    from check_live_hr_results_coverage import check_results_coverage
    from validate_live_hr_data import IDENTITY_FIELDS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data" / "theoddsapi" / "live_hr_snapshots"


@dataclass(frozen=True)
class ReportPaths:
    master: Path
    run_log: Path
    results: Path
    results_workbook: Path
    automation_logs: Path
    final_automation_logs: Path
    reports: Path

    @classmethod
    def defaults(cls) -> "ReportPaths":
        return cls(
            master=DATA_DIR / "live_hr_props_master.csv",
            run_log=DATA_DIR / "run_log.csv",
            results=DATA_DIR / "live_hr_results.csv",
            results_workbook=DATA_DIR / "live_hr_results_workbook.csv",
            automation_logs=DATA_DIR / "automation_logs",
            final_automation_logs=DATA_DIR / "final_automation_logs",
            reports=DATA_DIR / "reports",
        )


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _iso_date(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _timestamp_value(value: object) -> float:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.timestamp()
    except ValueError:
        return float("-inf")


def _collection_summary(
    master_path: Path, target_date: date
) -> dict[str, object]:
    rows = _read_csv(master_path)
    date_key = target_date.isoformat()
    selected = [row for row in rows if _iso_date(row.get("snapshot_time")) == date_key]

    identities: Counter[tuple[str, ...]] = Counter()
    bookmakers: Counter[str] = Counter()
    games: set[str] = set()
    for row in selected:
        identity = tuple(str(row.get(field, "")).strip() for field in IDENTITY_FIELDS)
        identities[identity] += 1

        bookmaker = str(row.get("bookmaker_key", "")).strip() or "<blank>"
        bookmakers[bookmaker] += 1

        event_id = str(row.get("event_id", "")).strip()
        matchup = " @ ".join(
            [
                str(row.get("away_team", "")).strip(),
                str(row.get("home_team", "")).strip(),
            ]
        )
        games.add(event_id or matchup)

    return {
        "snapshot_rows": len(selected),
        "total_master_rows": len(rows),
        "duplicate_count": sum(count - 1 for count in identities.values() if count > 1),
        "games_count": len(games),
        "bookmaker_counts": dict(sorted(bookmakers.items())),
    }


def _latest_run(run_log_path: Path, target_date: date) -> dict[str, str] | None:
    date_key = target_date.isoformat()
    matching = [
        row
        for row in _read_csv(run_log_path)
        if str(row.get("run_date", "")).strip() == date_key
    ]
    if not matching:
        return None
    return max(matching, key=lambda row: _timestamp_value(row.get("snapshot_time")))


def _results_coverage(results_path: Path) -> tuple[dict[str, int | bool], str | None]:
    try:
        return check_results_coverage(results_path), None
    except (FileNotFoundError, OSError, ValueError) as exc:
        return {
            "total_rows": 0,
            "missing_event_id": 0,
            "missing_player": 0,
            "missing_actual_home_runs": 0,
            "missing_game_status": 0,
            "invalid_actual_home_runs": 0,
            "ready_to_grade": False,
        }, str(exc)


def _newest_file(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    files = [path for path in directory.iterdir() if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda path: (path.stat().st_mtime_ns, path.name))


def _display(value: object) -> str:
    text = str(value or "").strip()
    return text or "Not available"


def _action_items(
    collection: dict[str, object], ready_to_grade: bool
) -> list[str]:
    actions: list[str] = []
    if int(collection["snapshot_rows"]) == 0:
        actions.append("Collection missing.")
    if int(collection["duplicate_count"]) > 0:
        actions.append("Dedupe needed.")
    if not ready_to_grade:
        actions.append("Results incomplete.")
    else:
        actions.append("Ready to grade.")
    if actions == ["Ready to grade."]:
        actions.append("No action needed.")
    return actions


def generate_daily_report(
    target_date: date,
    *,
    paths: ReportPaths | None = None,
    generated_at: datetime | None = None,
) -> tuple[Path, dict[str, object]]:
    """Build and write one report using local files only."""

    report_paths = paths or ReportPaths.defaults()
    generated = generated_at or datetime.now().astimezone()
    collection = _collection_summary(report_paths.master, target_date)
    latest_run = _latest_run(report_paths.run_log, target_date)
    coverage, coverage_error = _results_coverage(report_paths.results)
    ready_to_grade = bool(coverage["ready_to_grade"])
    total_results = int(coverage["total_rows"])
    missing_home_runs = int(coverage["missing_actual_home_runs"])
    missing_status = int(coverage["missing_game_status"])
    actions = _action_items(collection, ready_to_grade)

    bookmaker_counts = collection["bookmaker_counts"]
    assert isinstance(bookmaker_counts, dict)
    bookmaker_lines = (
        [f"  - {bookmaker}: {count}" for bookmaker, count in bookmaker_counts.items()]
        if bookmaker_counts
        else ["  - None"]
    )

    run = latest_run or {}
    run_lines = [
        f"- Latest run: {_display(run.get('snapshot_time'))}",
        f"- Status: {_display(run.get('status'))}",
        f"- Credits used: {_display(run.get('credits_used_this_run'))}",
        f"- Credits remaining: {_display(run.get('credits_remaining'))}",
        f"- Snapshot file path: {_display(run.get('snapshot_csv'))}",
    ]

    coverage_lines = [
        f"- Total result rows: {total_results}",
        f"- Filled `actual_home_runs`: {total_results - missing_home_runs}",
        f"- Filled `game_status`: {total_results - missing_status}",
        f"- Missing `actual_home_runs`: {missing_home_runs}",
        f"- Missing `game_status`: {missing_status}",
        f"- Ready to grade: {'YES' if ready_to_grade else 'NO'}",
    ]
    if coverage_error:
        coverage_lines.append(f"- Coverage error: {coverage_error}")

    daily_log = _newest_file(report_paths.automation_logs)
    final_log = _newest_file(report_paths.final_automation_logs)
    lines = [
        "# MLB Live HR Daily Operations Report",
        "",
        f"- Date: {target_date.isoformat()}",
        f"- Generated: {generated.isoformat(timespec='seconds')}",
        "",
        "## Collection summary",
        "",
        f"- Snapshot rows for that date: {collection['snapshot_rows']}",
        f"- Total master rows: {collection['total_master_rows']}",
        f"- Duplicate count: {collection['duplicate_count']}",
        f"- Games count: {collection['games_count']}",
        "- Bookmaker counts:",
        *bookmaker_lines,
        "",
        "## Run log summary",
        "",
        *run_lines,
        "",
        "## Results coverage",
        "",
        *coverage_lines,
        "",
        "## Automation logs",
        "",
        f"- Newest daily automation log path: {_display(daily_log)}",
        f"- Newest final automation log path: {_display(final_log)}",
        "",
        "## Action needed",
        "",
        *[f"- {action}" for action in actions],
        "",
    ]

    report_paths.reports.mkdir(parents=True, exist_ok=True)
    output_path = report_paths.reports / (
        f"live_hr_daily_report_{target_date.strftime('%Y%m%d')}.md"
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")

    summary: dict[str, object] = {
        **collection,
        "total_result_rows": total_results,
        "missing_actual_home_runs": missing_home_runs,
        "missing_game_status": missing_status,
        "ready_to_grade": ready_to_grade,
        "actions": actions,
    }
    return output_path, summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate an offline daily MLB HR operations report."
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=date.today(),
        help="Local report date in YYYY-MM-DD format (default: local today).",
    )
    args = parser.parse_args(argv)

    try:
        output_path, summary = generate_daily_report(args.date)
    except (OSError, csv.Error) as exc:
        parser.exit(1, f"ERROR: {exc}\n")

    print(f"Report written: {output_path}")
    print(
        "Collection: "
        f"rows={summary['snapshot_rows']}, "
        f"games={summary['games_count']}, "
        f"duplicates={summary['duplicate_count']}"
    )
    print(
        "Results coverage: "
        f"rows={summary['total_result_rows']}, "
        f"missing_home_runs={summary['missing_actual_home_runs']}, "
        f"missing_status={summary['missing_game_status']}, "
        f"ready_to_grade={'YES' if summary['ready_to_grade'] else 'NO'}"
    )
    print("Action needed: " + " ".join(summary["actions"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
