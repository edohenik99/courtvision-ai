from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from courtvision.artifact_guard import guard_no_existing_artifact


PASS = "PASS"
PASS_NO_SLATE = "PASS_NO_SLATE"
WARN_MISSING_ARTIFACTS = "WARN_MISSING_ARTIFACTS"
WARN_AUDIT_ISSUES = "WARN_AUDIT_ISSUES"
WARN_MISSING_RECOMMENDED_ACTION = "WARN_MISSING_RECOMMENDED_ACTION"
FAIL_PENDING_REAL_PICKS = "FAIL_PENDING_REAL_PICKS"
FAIL_UNREADABLE = "FAIL_UNREADABLE"

EXPECTED_ARTIFACTS = (
    "operator_card",
    "completion_audit_json",
    "completion_audit_text",
    "daily_summary",
    "quality_summary",
)


def _artifact_paths(runtime_root: Path, prediction_date: str) -> dict[str, Path]:
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    return {
        "operator_card": operator / f"operator_card_{prediction_date}.txt",
        "completion_audit_text": operator / f"completion_state_audit_{prediction_date}.txt",
        "completion_audit_json": diagnostics / f"completion_state_audit_{prediction_date}.json",
        "daily_summary": operator / f"daily_summary_{prediction_date}.txt",
        "quality_summary": operator / f"quality_summary_{prediction_date}.txt",
    }


def _output_paths(runtime_root: Path, start_date: str, end_date: str) -> dict[str, Path]:
    suffix = f"{start_date}_{end_date}"
    return {
        "text": runtime_root / "operator" / f"historical_cockpit_validation_{suffix}.txt",
        "json": runtime_root / "diagnostics" / f"historical_cockpit_validation_{suffix}.json",
        "csv": runtime_root / "diagnostics" / f"historical_cockpit_validation_{suffix}.csv",
    }


def _date_range(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise ValueError("end-date must be on or after start-date")
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _count_items(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, list | tuple | set | dict):
        return len(value)
    number = _safe_int(value)
    if number is not None:
        return number
    return 1 if str(value).strip() else 0


def _read_text(path: Path) -> tuple[str, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except Exception as exc:
        return "", f"Could not read {path}: {exc}"


def _read_json(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"Could not parse {path}: {exc}"
    if not isinstance(payload, dict):
        return {}, f"Expected JSON object in {path}"
    return payload, None


def _extract_line_value(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


def _classify_row(row: dict[str, Any]) -> str:
    if row["parse_errors"]:
        return FAIL_UNREADABLE
    real_pending = row.get("real_pick_pending_count")
    if real_pending is not None and int(real_pending) > 0:
        return FAIL_PENDING_REAL_PICKS
    if int(row.get("warning_count") or 0) > 0 or int(row.get("agreement_issue_count") or 0) > 0:
        return WARN_AUDIT_ISSUES
    if row["missing_artifacts"]:
        return WARN_MISSING_ARTIFACTS
    if row["missing_operator_card_fields"]:
        return WARN_MISSING_RECOMMENDED_ACTION
    if (
        row.get("final_decision") == "NO BET"
        and row.get("games_count") == 0
        and row.get("report_agreement_status") == "COMPLETE"
        and row.get("real_pick_pending_count") == 0
        and row.get("recommended_action") == "slate closed / no action required"
    ):
        return PASS_NO_SLATE
    if (
        row.get("operator_card_exists")
        and row.get("completion_audit_json_exists")
        and row.get("report_agreement_status")
        and row.get("real_pick_pending_count") is not None
        and row.get("recommended_action")
    ):
        return PASS
    return WARN_MISSING_RECOMMENDED_ACTION


def validate_cockpit_date(prediction_date: str, runtime_root: str | Path = "outputs/runtime") -> dict[str, Any]:
    runtime_root = Path(runtime_root)
    paths = _artifact_paths(runtime_root, prediction_date)
    missing_artifacts = [key for key in EXPECTED_ARTIFACTS if not paths[key].exists()]
    parse_errors: list[str] = []
    missing_operator_card_fields: list[str] = []

    operator_text = ""
    final_decision = None
    games_count = None
    recommended_action = None
    if paths["operator_card"].exists():
        operator_text, error = _read_text(paths["operator_card"])
        if error:
            parse_errors.append(error)
        else:
            final_decision = _extract_line_value(operator_text, r"^final_decision:\s*(.+)$")
            games_count = _safe_int(_extract_line_value(operator_text, r"^\s*-\s*games count:\s*(\d+)\s*$"))
            recommended_action = _extract_line_value(operator_text, r"^\s*-\s*recommended action:\s*(.+)$")
            if recommended_action is None:
                missing_operator_card_fields.append("recommended_action")

    completion_payload: dict[str, Any] = {}
    report_agreement_status = None
    real_pick_pending_count = None
    warning_count = 0
    agreement_issue_count = 0
    if paths["completion_audit_json"].exists():
        completion_payload, error = _read_json(paths["completion_audit_json"])
        if error:
            parse_errors.append(error)
        else:
            report_agreement_status = completion_payload.get("report_agreement_status")
            real_pick_pending_count = _safe_int(completion_payload.get("real_pick_pending_count"))
            warning_count = _count_items(completion_payload.get("warnings"))
            agreement_issue_count = _count_items(completion_payload.get("agreement_issues"))
            if not report_agreement_status:
                parse_errors.append(f"Missing report_agreement_status in {paths['completion_audit_json']}")
            if real_pick_pending_count is None:
                parse_errors.append(f"Missing real_pick_pending_count in {paths['completion_audit_json']}")

    row: dict[str, Any] = {
        "date": prediction_date,
        "operator_card_exists": paths["operator_card"].exists(),
        "completion_audit_json_exists": paths["completion_audit_json"].exists(),
        "completion_audit_text_exists": paths["completion_audit_text"].exists(),
        "daily_summary_exists": paths["daily_summary"].exists(),
        "quality_summary_exists": paths["quality_summary"].exists(),
        "final_decision": final_decision,
        "games_count": games_count,
        "report_agreement_status": report_agreement_status,
        "real_pick_pending_count": real_pick_pending_count,
        "warning_count": warning_count,
        "agreement_issue_count": agreement_issue_count,
        "recommended_action": recommended_action,
        "missing_artifacts": missing_artifacts,
        "missing_operator_card_fields": missing_operator_card_fields,
        "parse_errors": parse_errors,
        "artifact_paths": {key: str(path) for key, path in paths.items()},
    }
    row["validation_status"] = _classify_row(row)
    return row


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    pass_count = sum(1 for row in rows if row["validation_status"] in {PASS, PASS_NO_SLATE})
    warning_count = sum(1 for row in rows if str(row["validation_status"]).startswith("WARN"))
    fail_count = sum(1 for row in rows if str(row["validation_status"]).startswith("FAIL"))
    missing_artifact_count = sum(len(row["missing_artifacts"]) for row in rows)
    return {
        "total_dates_checked": len(rows),
        "pass_count": pass_count,
        "warning_count": warning_count,
        "fail_count": fail_count,
        "missing_artifact_count": missing_artifact_count,
    }


def build_historical_cockpit_validation(
    *,
    start_date: str,
    end_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> dict[str, Any]:
    runtime_root = Path(runtime_root)
    rows = [validate_cockpit_date(day, runtime_root=runtime_root) for day in _date_range(start_date, end_date)]
    return {
        "start_date": start_date,
        "end_date": end_date,
        "runtime_root": str(runtime_root),
        "read_only": True,
        "summary": _summary(rows),
        "dates": rows,
    }


def render_historical_cockpit_validation_text(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    rows = payload["dates"]
    lines = [
        f"Historical Cockpit Validation - {payload['start_date']}..{payload['end_date']}",
        "=" * 72,
        "Read-only validation; no predictions, grading, Kelly, selection, suppression, or history mutation.",
        "",
        "Summary",
        "-" * 72,
        f"- total dates checked: {summary['total_dates_checked']}",
        f"- pass count: {summary['pass_count']}",
        f"- warning count: {summary['warning_count']}",
        f"- fail count: {summary['fail_count']}",
        f"- missing artifact count: {summary['missing_artifact_count']}",
        "",
        "Per-Date Rows",
        "-" * 72,
        "date       validation_status                  decision        games audit_status                     pending warn issues recommended_action",
        "---------- ---------------------------------- --------------- ----- -------------------------------- ------- ---- ------ -------------------",
    ]
    for row in rows:
        lines.append(
            f"{row['date']:<10} "
            f"{row['validation_status']:<34} "
            f"{str(row.get('final_decision') or 'missing'):<15} "
            f"{str(row.get('games_count') if row.get('games_count') is not None else 'n/a'):<5} "
            f"{str(row.get('report_agreement_status') or 'missing'):<32} "
            f"{str(row.get('real_pick_pending_count') if row.get('real_pick_pending_count') is not None else 'n/a'):<7} "
            f"{row.get('warning_count', 0):<4} "
            f"{row.get('agreement_issue_count', 0):<6} "
            f"{row.get('recommended_action') or 'missing'}"
        )

    lines.extend(["", "Recommended Next Actions", "-" * 72])
    action_lines: list[str] = []
    for row in rows:
        status = row["validation_status"]
        if status in {PASS, PASS_NO_SLATE}:
            continue
        if status == WARN_MISSING_ARTIFACTS:
            action = f"restore or generate missing artifacts: {', '.join(row['missing_artifacts'])}"
        elif status == WARN_AUDIT_ISSUES:
            action = "inspect completion audit before trusting results"
        elif status == WARN_MISSING_RECOMMENDED_ACTION:
            action = f"regenerate operator card: py -3.13 scripts/write_operator_card.py --prediction-date {row['date']}"
        elif status == FAIL_PENDING_REAL_PICKS:
            action = "inspect grading before trusting results"
        else:
            action = "inspect unreadable cockpit artifacts"
        action_lines.append(f"- {row['date']}: {status} - {action}")
        for error in row.get("parse_errors", []):
            action_lines.append(f"  parse_error: {error}")
    lines.extend(action_lines or ["- none"])
    return "\n".join(lines) + "\n"


def _csv_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    columns = [
        "date",
        "operator_card_exists",
        "completion_audit_json_exists",
        "completion_audit_text_exists",
        "daily_summary_exists",
        "quality_summary_exists",
        "final_decision",
        "games_count",
        "report_agreement_status",
        "real_pick_pending_count",
        "warning_count",
        "agreement_issue_count",
        "recommended_action",
        "validation_status",
    ]
    return [{column: row.get(column) for column in columns} for row in rows]


def write_historical_cockpit_validation(
    *,
    start_date: str,
    end_date: str,
    runtime_root: str | Path = "outputs/runtime",
    force: bool = False,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    payload = build_historical_cockpit_validation(
        start_date=start_date,
        end_date=end_date,
        runtime_root=runtime_root,
    )
    paths = _output_paths(runtime_root, start_date, end_date)
    for artifact_key, path in paths.items():
        guard_no_existing_artifact(
            output_path=path,
            force=force,
            caller="write_historical_cockpit_validation",
            artifact_label=f"historical_cockpit_validation_{artifact_key}",
        )
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    paths["text"].write_text(render_historical_cockpit_validation_text(payload), encoding="utf-8")
    paths["json"].write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    csv_rows = _csv_rows(payload["dates"])
    with paths["csv"].open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(csv_rows[0].keys()) if csv_rows else [])
        writer.writeheader()
        writer.writerows(csv_rows)
    return paths["text"], paths["json"], paths["csv"], payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate historical CourtVision cockpit artifacts.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow existing historical cockpit validation artifacts to be overwritten intentionally.",
    )
    args = parser.parse_args(argv)

    text_path, json_path, csv_path, payload = write_historical_cockpit_validation(
        start_date=args.start_date,
        end_date=args.end_date,
        runtime_root=args.runtime_root,
        force=args.force,
    )
    summary = payload["summary"]
    print(f"historical_cockpit_validation_txt={text_path}")
    print(f"historical_cockpit_validation_json={json_path}")
    print(f"historical_cockpit_validation_csv={csv_path}")
    print(
        "historical_cockpit_validation_summary "
        f"total={summary['total_dates_checked']} "
        f"pass={summary['pass_count']} "
        f"warning={summary['warning_count']} "
        f"fail={summary['fail_count']} "
        f"missing_artifacts={summary['missing_artifact_count']}"
    )
    for row in payload["dates"]:
        print(
            "historical_cockpit_validation_row "
            f"date={row['date']} "
            f"status={row['validation_status']} "
            f"decision={row.get('final_decision') or 'missing'} "
            f"audit={row.get('report_agreement_status') or 'missing'} "
            f"pending={row.get('real_pick_pending_count') if row.get('real_pick_pending_count') is not None else 'missing'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
