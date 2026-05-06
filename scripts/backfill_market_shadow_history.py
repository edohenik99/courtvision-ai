from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.history_tracking import backfill_market_shadow_history


def _parse_dates(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    dates: list[str] = []
    for value in values:
        dates.extend(part.strip() for part in str(value).split(",") if part.strip())
    return dates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill market_shadow_history.csv from historical full_market_board_YYYY-MM-DD.csv files."
    )
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument(
        "--date",
        dest="dates",
        action="append",
        help="Prediction date to backfill. May be repeated or comma-separated. Defaults to all discovered boards.",
    )
    parser.add_argument("--start-date", help="Only include discovered dates on or after this YYYY-MM-DD date.")
    parser.add_argument("--end-date", help="Only include discovered dates on or before this YYYY-MM-DD date.")
    parser.add_argument("--result-status", default="pending")
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Refresh dates already in market_shadow_history.csv using same-date replacement.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the dates and rows that would be backfilled without writing files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = backfill_market_shadow_history(
        runtime_root=args.runtime_root,
        history_root=args.history_root,
        dates=_parse_dates(args.dates),
        start_date=args.start_date,
        end_date=args.end_date,
        replace_existing=args.replace_existing,
        dry_run=args.dry_run,
        result_status=args.result_status,
    )

    action_key = "would_backfill_dates" if args.dry_run else "backfilled_dates"
    print(f"full_market_board_files_found={len(result['full_market_board_dates'])}")
    print(f"selected_dates={len(result['selected_dates'])}")
    print(f"{action_key}={','.join(result[action_key])}")
    print(f"skipped_existing_dates={','.join(result['skipped_existing_dates'])}")
    print(f"count_mismatch_dates={','.join(result['count_mismatch_dates'])}")
    print(f"missing_full_market_dates={','.join(result['missing_full_market_dates'])}")
    print(f"dates_processed={result['dates_processed']}")
    print(f"total_incoming_rows={result['total_incoming_rows']}")
    print(f"total_replaced_rows={result['total_replaced_rows']}")
    print(f"market_shadow_history_csv={result['market_shadow_history_path']}")
    print(f"market_readiness_summary_csv={result['market_readiness_summary_path']}")

    for date_result in result["date_results"]:
        print(
            "date_result="
            f"{date_result['prediction_date']},"
            f"board_rows={date_result['board_rows']},"
            f"incoming_rows={date_result['incoming_rows']},"
            f"replaced_rows={date_result['replaced_rows']},"
            f"current_date_rows={date_result['current_date_rows']}"
        )
    for mismatch in result["count_mismatch_details"]:
        print(
            "count_mismatch="
            f"{mismatch['prediction_date']},"
            f"existing_rows={mismatch['existing_rows']},"
            f"board_rows={mismatch['board_rows']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
