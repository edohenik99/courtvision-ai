"""CourtVision Power Rating Shadow Analysis — CLI.

Joins graded pick history with Power Rating context diagnostics and writes
per-dimension performance reports.  Observation-only: does not affect
pick selection, Kelly sizing, or any betting output.

Usage examples:
    py -3.13 scripts/analyze_power_rating_shadow.py --date 2026-05-07
    py -3.13 scripts/analyze_power_rating_shadow.py --from-date 2026-04-19 --to-date 2026-05-07
    py -3.13 scripts/analyze_power_rating_shadow.py  # all dates in pick_history
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.power_rating_shadow import write_shadow_outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyse Power Rating shadow performance against graded picks."
    )
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument("--date", metavar="YYYY-MM-DD", help="Single prediction date.")
    date_group.add_argument("--from-date", metavar="YYYY-MM-DD", help="Start of date range (inclusive).")
    parser.add_argument(
        "--to-date",
        metavar="YYYY-MM-DD",
        default="",
        help="End of date range (inclusive). Required with --from-date.",
    )
    parser.add_argument(
        "--history-root",
        default="data/history",
        help="Long-lived history directory (default: data/history)",
    )
    parser.add_argument(
        "--diagnostics-dir",
        default="outputs/runtime/diagnostics",
        help="Diagnostics directory (default: outputs/runtime/diagnostics)",
    )
    return parser.parse_args()


def _date_range(from_date: str, to_date: str) -> list[str]:
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date) if to_date else start
    out: list[str] = []
    d = start
    while d <= end:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _fmt_hit_rate(hr: float | None) -> str:
    return f"{hr:.1%}" if hr is not None else "n/a"


def main() -> int:
    args = parse_args()

    if args.date:
        prediction_dates: list[str] | None = [args.date]
        analysis_date = args.date
    elif args.from_date:
        if not args.to_date:
            print("ERROR: --to-date is required when --from-date is used.", file=sys.stderr)
            return 1
        prediction_dates = _date_range(args.from_date, args.to_date)
        analysis_date = args.to_date
    else:
        prediction_dates = None
        analysis_date = date.today().isoformat()

    pick_history_path = Path(args.history_root) / "pick_history.csv"

    result = write_shadow_outputs(
        prediction_dates=prediction_dates,
        pick_history_path=pick_history_path,
        diagnostics_dir=args.diagnostics_dir,
        analysis_date=analysis_date,
    )

    if result.get("error"):
        print(f"ERROR: {result['error']}", file=sys.stderr)
        return 1

    print(f"dates analyzed:          {result['dates_analyzed']}")
    print(f"total picks:             {result['total_picks']}")
    print(f"graded picks:            {result['graded_picks']}")
    print(f"overall hit rate:        {_fmt_hit_rate(result.get('hit_rate'))}")
    print(f"context joined:          {result['context_joined_count']}")
    print(f"row CSV:                 {result.get('csv_path') or 'not written (no joined rows)'}")
    print(f"summary JSON:            {result['json_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
