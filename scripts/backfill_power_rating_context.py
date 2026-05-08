"""CourtVision Power Rating Context Backfill — CLI.

Backfills outputs/runtime/diagnostics/power_rating_context_YYYY-MM-DD.csv
for historical prediction dates so shadow analysis has more joined graded picks.

Observation-only: does not change pick selection, edge, confidence,
quality_score, board ranking, Kelly sizing, or stake calculation.

Usage examples:
    py -3.13 scripts/backfill_power_rating_context.py
    py -3.13 scripts/backfill_power_rating_context.py --date 2026-05-01
    py -3.13 scripts/backfill_power_rating_context.py --from-date 2026-04-01 --to-date 2026-05-01
    py -3.13 scripts/backfill_power_rating_context.py --overwrite
    py -3.13 scripts/backfill_power_rating_context.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pandas as pd

from courtvision.context.game_strength import apply_power_rating_context_to_df
from courtvision.ratings.power_ratings_store import get_latest_team_power_ratings
from courtvision.reporting.quality_summary import _write_power_rating_diagnostics_csv
from scripts.history_tracking import discover_full_market_board_dates

_REQUIRED_BOARD_COLUMNS = {"team_abbr", "opponent", "home_away"}


def _load_board(path: Path) -> pd.DataFrame | None:
    """Load a full-market board CSV. Returns None on any failure."""
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception:
        return None
    if df.empty:
        return None
    if not _REQUIRED_BOARD_COLUMNS.issubset(df.columns):
        return None
    return df


def backfill_power_rating_context(
    runtime_root: str | Path = "outputs/runtime",
    game_results_path: str | Path | None = None,
    dates: list[str] | None = None,
    from_date: str = "",
    to_date: str = "",
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict:
    """Backfill power_rating_context_YYYY-MM-DD.csv for historical prediction dates.

    Never mutates full_market_board CSVs. Observation-only: no impact on picks,
    Kelly sizing, edge, quality_score, or stake calculation.

    Args:
        runtime_root: Root of runtime outputs (contains operator/ and diagnostics/).
        game_results_path: Path to game_results.csv. None uses default store path.
        dates: Explicit list of prediction dates. None discovers all board dates.
        from_date: Include only dates >= this value (YYYY-MM-DD).
        to_date: Include only dates <= this value (YYYY-MM-DD).
        overwrite: When True, re-write existing diagnostics files.
        dry_run: When True, count and report without writing any files.

    Returns:
        Summary dict with counts and per-category date lists.
    """
    runtime_root_path = Path(runtime_root)
    diagnostics_dir = runtime_root_path / "diagnostics"

    board_dates: dict[str, Path] = discover_full_market_board_dates(runtime_root)

    if dates is not None:
        selected = {d: board_dates[d] for d in dates if d in board_dates}
        not_found = [d for d in dates if d not in board_dates]
    else:
        selected = dict(board_dates)
        not_found = []

    if from_date:
        selected = {d: p for d, p in selected.items() if d >= from_date}
    if to_date:
        selected = {d: p for d, p in selected.items() if d <= to_date}

    dates_found = sorted(board_dates)
    dates_selected = sorted(selected)

    dates_written: list[str] = []
    dates_would_write: list[str] = []
    dates_skipped: list[str] = []
    dates_skipped_no_overwrite: list[str] = []
    rows_enriched = 0
    context_applied_total = 0
    context_missing_total = 0
    skip_reasons: dict[str, str] = {}

    for prediction_date in dates_selected:
        board_path = selected[prediction_date]
        out_path = diagnostics_dir / f"power_rating_context_{prediction_date}.csv"

        if out_path.exists() and not overwrite:
            dates_skipped_no_overwrite.append(prediction_date)
            continue

        board_df = _load_board(board_path)
        if board_df is None:
            dates_skipped.append(prediction_date)
            skip_reasons[prediction_date] = "board_missing_or_unusable"
            continue

        ratings = get_latest_team_power_ratings(
            path=game_results_path,
            as_of_date=prediction_date,
        )

        enriched = board_df.copy()
        apply_power_rating_context_to_df(enriched, ratings=ratings)

        if "team_power_context_applied" in enriched.columns:
            applied_mask = enriched["team_power_context_applied"].map(
                lambda x: x is True or str(x).lower() == "true"
            )
            context_applied_total += int(applied_mask.sum())
            context_missing_total += int((~applied_mask).sum())
        else:
            context_missing_total += len(enriched)

        rows_enriched += len(enriched)

        if dry_run:
            dates_would_write.append(prediction_date)
        else:
            written = _write_power_rating_diagnostics_csv(enriched, prediction_date, diagnostics_dir)
            if written:
                dates_written.append(prediction_date)
            else:
                dates_skipped.append(prediction_date)
                skip_reasons[prediction_date] = "write_failed_or_no_context"

    return {
        "dates_found": dates_found,
        "dates_selected": dates_selected,
        "dates_written": dates_written,
        "dates_would_write": dates_would_write,
        "dates_skipped": dates_skipped,
        "dates_skipped_no_overwrite": dates_skipped_no_overwrite,
        "dates_not_found": not_found,
        "rows_enriched": rows_enriched,
        "context_applied_count": context_applied_total,
        "context_missing_count": context_missing_total,
        "skip_reasons": skip_reasons,
        "dry_run": dry_run,
        "observation_only": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill power_rating_context_YYYY-MM-DD.csv for historical prediction dates. "
            "Observation-only: never affects pick selection, edge, Kelly, or stakes."
        )
    )
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument("--date", metavar="YYYY-MM-DD", help="Single prediction date to backfill.")
    date_group.add_argument("--from-date", metavar="YYYY-MM-DD", help="Start of date range (inclusive).")
    parser.add_argument(
        "--to-date",
        metavar="YYYY-MM-DD",
        default="",
        help="End of date range (inclusive). Required with --from-date.",
    )
    parser.add_argument(
        "--runtime-root",
        default="outputs/runtime",
        help="Runtime root directory (default: outputs/runtime).",
    )
    parser.add_argument(
        "--game-results",
        default="",
        help="Path to game_results.csv (default: data/history/game_results.csv).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing diagnostics files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written without writing any files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.date:
        dates: list[str] | None = [args.date]
        from_date = ""
        to_date = ""
    elif args.from_date:
        if not args.to_date:
            print("ERROR: --to-date is required when --from-date is used.", file=sys.stderr)
            return 1
        dates = None
        from_date = args.from_date
        to_date = args.to_date
    else:
        dates = None
        from_date = ""
        to_date = ""

    game_results_path: Path | None = Path(args.game_results) if args.game_results else None

    if args.dry_run:
        print("[DRY RUN] No files will be written.")

    result = backfill_power_rating_context(
        runtime_root=args.runtime_root,
        game_results_path=game_results_path,
        dates=dates,
        from_date=from_date,
        to_date=to_date,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )

    action_label = "would_write" if args.dry_run else "written"
    action_dates = result["dates_would_write"] if args.dry_run else result["dates_written"]

    print(f"dates_found={len(result['dates_found'])}")
    print(f"dates_selected={len(result['dates_selected'])}")
    print(f"dates_{action_label}={len(action_dates)}  [{','.join(action_dates)}]")
    print(f"dates_skipped_no_board={len(result['dates_skipped'])}  [{','.join(result['dates_skipped'])}]")
    print(f"dates_skipped_existing={len(result['dates_skipped_no_overwrite'])}  [{','.join(result['dates_skipped_no_overwrite'])}]")
    if result["dates_not_found"]:
        print(f"dates_not_found={','.join(result['dates_not_found'])}")
    print(f"rows_enriched={result['rows_enriched']}")
    print(f"context_applied={result['context_applied_count']}")
    print(f"context_missing={result['context_missing_count']}")
    print(f"observation_only={result['observation_only']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
