from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.write_operator_card import write_operator_card_outputs


REFRESHED = "REFRESHED"
SKIPPED_CURRENT = "SKIPPED_CURRENT"
SKIPPED_NOT_STALE = "SKIPPED_NOT_STALE"
WOULD_REFRESH = "WOULD_REFRESH"
FAILED = "FAILED"

REQUIRED_SOURCE_ARTIFACTS = (
    "elite_board",
    "full_market_board",
    "quality_summary_json",
    "board_diagnostics",
)


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


def _artifact_paths(runtime_root: Path, prediction_date: str) -> dict[str, Path]:
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    return {
        "operator_card": operator / f"operator_card_{prediction_date}.txt",
        "elite_board": operator / f"elite_board_{prediction_date}.csv",
        "full_market_board": operator / f"full_market_board_{prediction_date}.csv",
        "sgp_board": operator / f"sgp_board_{prediction_date}.csv",
        "daily_summary": operator / f"daily_summary_{prediction_date}.txt",
        "quality_summary": operator / f"quality_summary_{prediction_date}.txt",
        "quality_summary_json": operator / f"quality_summary_{prediction_date}.json",
        "completion_audit_json": diagnostics / f"completion_state_audit_{prediction_date}.json",
        "completion_audit_text": operator / f"completion_state_audit_{prediction_date}.txt",
        "board_diagnostics": diagnostics / f"board_diagnostics_{prediction_date}.json",
    }


def _operator_card_stale(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return True, "operator_card_missing"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        return True, f"operator_card_unreadable:{exc}"
    if "- recommended action:" not in text:
        return True, "missing_recommended_action"
    return False, "fresh"


def _missing_required_sources(paths: dict[str, Path]) -> list[str]:
    return [key for key in REQUIRED_SOURCE_ARTIFACTS if not paths[key].exists()]


def _is_current_or_future(prediction_date: str, today: str) -> bool:
    return date.fromisoformat(prediction_date) >= date.fromisoformat(today)


def refresh_historical_operator_cards(
    *,
    start_date: str,
    end_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    only_stale: bool = False,
    dry_run: bool = False,
    today: str | None = None,
) -> dict[str, Any]:
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)
    today_text = today or date.today().isoformat()
    rows: list[dict[str, Any]] = []

    for prediction_date in _date_range(start_date, end_date):
        paths = _artifact_paths(runtime_root, prediction_date)
        is_stale, stale_reason = _operator_card_stale(paths["operator_card"])
        row: dict[str, Any] = {
            "date": prediction_date,
            "action": None,
            "reason": stale_reason,
            "operator_card_path": str(paths["operator_card"]),
            "only_stale": only_stale,
            "dry_run": dry_run,
            "missing_required_sources": [],
        }

        if _is_current_or_future(prediction_date, today_text):
            row["action"] = SKIPPED_CURRENT
            row["reason"] = "current_or_future_date"
            rows.append(row)
            continue

        if only_stale and not is_stale:
            row["action"] = SKIPPED_NOT_STALE
            rows.append(row)
            continue

        if dry_run:
            row["action"] = WOULD_REFRESH
            rows.append(row)
            continue

        missing_sources = _missing_required_sources(paths)
        if missing_sources:
            row["action"] = FAILED
            row["reason"] = "missing_required_source_artifacts"
            row["missing_required_sources"] = missing_sources
            rows.append(row)
            continue

        try:
            output_path, payload = write_operator_card_outputs(
                prediction_date=prediction_date,
                runtime_root=runtime_root,
                history_root=history_root,
                force=True,
            )
        except Exception as exc:
            row["action"] = FAILED
            row["reason"] = f"operator_card_refresh_failed:{exc}"
            rows.append(row)
            continue

        row["action"] = REFRESHED
        row["operator_card_path"] = str(output_path)
        row["final_decision"] = payload.get("final_decision")
        row["completion_state_audit_status"] = payload.get("completion_state_audit_status")
        rows.append(row)

    summary = {
        "total": len(rows),
        "refreshed": sum(1 for row in rows if row["action"] == REFRESHED),
        "skipped_current": sum(1 for row in rows if row["action"] == SKIPPED_CURRENT),
        "skipped_not_stale": sum(1 for row in rows if row["action"] == SKIPPED_NOT_STALE),
        "would_refresh": sum(1 for row in rows if row["action"] == WOULD_REFRESH),
        "failed": sum(1 for row in rows if row["action"] == FAILED),
    }
    return {
        "start_date": start_date,
        "end_date": end_date,
        "runtime_root": str(runtime_root),
        "history_root": str(history_root),
        "only_stale": only_stale,
        "dry_run": dry_run,
        "today": today_text,
        "summary": summary,
        "dates": rows,
    }


def _print_payload(payload: dict[str, Any]) -> None:
    for row in payload["dates"]:
        extras = ""
        if row.get("missing_required_sources"):
            extras = f" missing_required_sources={','.join(row['missing_required_sources'])}"
        print(
            "refresh_historical_operator_card_row "
            f"date={row['date']} "
            f"action={row['action']} "
            f"reason={row['reason']} "
            f"path={row['operator_card_path']}"
            f"{extras}"
        )
    summary = payload["summary"]
    print(
        "refresh_historical_operator_cards_summary "
        f"total={summary['total']} "
        f"refreshed={summary['refreshed']} "
        f"would_refresh={summary['would_refresh']} "
        f"skipped_current={summary['skipped_current']} "
        f"skipped_not_stale={summary['skipped_not_stale']} "
        f"failed={summary['failed']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh stale historical CourtVision operator cards.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--only-stale", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    payload = refresh_historical_operator_cards(
        start_date=args.start_date,
        end_date=args.end_date,
        runtime_root=args.runtime_root,
        only_stale=args.only_stale,
        dry_run=args.dry_run,
    )
    _print_payload(payload)
    return 0 if payload["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
