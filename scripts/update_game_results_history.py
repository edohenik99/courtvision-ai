"""Update CourtVision Power Rating game results history.

Fetches completed NBA games for a date or date range from the BallDontLie
provider and appends them to data/history/game_results.csv.

Usage examples:
    py -3.13 scripts/update_game_results_history.py --date 2026-05-07
    py -3.13 scripts/update_game_results_history.py --from-date 2026-04-19 --to-date 2026-05-07
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.ratings.power_ratings_store import (
    GAME_RESULTS_COLUMNS,
    GAME_RESULTS_FILENAME,
    append_game_results,
    games_df_to_results,
)
from courtvision_ai import CourtVisionAI


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append completed NBA game results to data/history/game_results.csv."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", metavar="YYYY-MM-DD", help="Single game date.")
    group.add_argument("--from-date", metavar="YYYY-MM-DD", help="Start of date range (inclusive).")
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
        "--runtime-root",
        default="outputs/runtime",
        help="Runtime outputs root for CourtVisionAI (default: outputs/runtime)",
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


def main() -> int:
    args = parse_args()

    if args.date:
        dates = [args.date]
    else:
        if not args.to_date:
            print("ERROR: --to-date is required when --from-date is used.", file=sys.stderr)
            return 1
        dates = _date_range(args.from_date, args.to_date)

    history_root = Path(args.history_root)
    results_path = history_root / GAME_RESULTS_FILENAME

    try:
        ai = CourtVisionAI(out_dir=args.runtime_root)
        client = ai._get_client()
    except Exception as exc:
        print(f"ERROR: Could not initialise CourtVisionAI client: {exc}", file=sys.stderr)
        return 1

    all_results_frames: list[pd.DataFrame] = []
    total_fetched_raw = 0

    for d in dates:
        try:
            raw_df = client.get_games(d)
        except Exception as exc:
            print(f"  WARNING: get_games({d!r}) failed — {exc}")
            continue

        if raw_df is None or (hasattr(raw_df, "empty") and raw_df.empty):
            print(f"  {d}: 0 games returned from provider")
            continue

        total_fetched_raw += len(raw_df)
        results_df = games_df_to_results(raw_df, fallback_date=d)
        all_results_frames.append(results_df)
        print(f"  {d}: {len(raw_df)} fetched -> {len(results_df)} completed")

    if all_results_frames:
        combined_new = pd.concat(all_results_frames, ignore_index=True)
    else:
        combined_new = pd.DataFrame(columns=list(GAME_RESULTS_COLUMNS))

    summary = append_game_results(combined_new, path=results_path)

    print()
    print(f"games fetched (raw):     {total_fetched_raw}")
    print(f"completed accepted:      {summary['accepted']}")
    print(f"rows appended:           {summary['appended']}")
    print(f"duplicates skipped:      {summary['skipped_duplicates']}")
    print(f"total rows in history:   {summary['total_rows']}")
    print(f"output path:             {summary['output_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
