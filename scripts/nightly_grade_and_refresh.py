from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.history_tracking import grade_completed_picks


def _safe_read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _pending_count(history_path: Path) -> int:
    history = _safe_read_csv(history_path)
    if history.empty or "result_status" not in history.columns:
        return 0
    return int(history["result_status"].astype(str).str.lower().eq("pending").sum())


def _hit_rates(summary_path: Path) -> tuple[float, float]:
    perf = _safe_read_csv(summary_path)
    if perf.empty:
        return 0.0, 0.0

    hits = int(perf.get("hits", pd.Series(dtype=int)).sum())
    misses = int(perf.get("misses", pd.Series(dtype=int)).sum())
    denom = hits + misses
    all_time = float(hits / denom) if denom else 0.0

    recent = perf.sort_values("date").tail(7)
    r_hits = int(recent.get("hits", pd.Series(dtype=int)).sum())
    r_misses = int(recent.get("misses", pd.Series(dtype=int)).sum())
    r_denom = r_hits + r_misses
    last_7 = float(r_hits / r_denom) if r_denom else 0.0
    return all_time, last_7


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grade completed picks and refresh performance summaries.")
    parser.add_argument(
        "--history-root",
        default=str(ROOT_DIR / "data" / "history"),
        help="Long-lived history directory.",
    )
    parser.add_argument(
        "--runtime-root",
        default=str(ROOT_DIR / "outputs" / "runtime"),
        help="Runtime outputs root.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute grading updates without writing pick_history, graded runtime CSVs, or performance summaries.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    history_root = Path(args.history_root)
    runtime_root = Path(args.runtime_root)
    pick_history_path = history_root / "pick_history.csv"
    performance_summary_path = history_root / "performance_summary.csv"

    pending_before = _pending_count(pick_history_path)
    grade_result = grade_completed_picks(
        history_root=history_root,
        runtime_root=runtime_root,
        dry_run=args.dry_run,
    )
    newly_graded = int(grade_result.get("updated_rows", 0))
    still_pending = int(grade_result.get("pending_rows", 0))
    all_time_hit_rate, last_7_hit_rate = _hit_rates(performance_summary_path)

    print("CourtVision Nightly Grade + Refresh")
    print("====================================")
    print(f"dry_run={str(bool(grade_result.get('dry_run', args.dry_run))).lower()}")
    print(f"pending_before={pending_before}")
    print(f"newly_graded={newly_graded}")
    print(f"still_pending={still_pending}")
    print(f"all_time_hit_rate={all_time_hit_rate:.4f}")
    print(f"last_7_slates_hit_rate={last_7_hit_rate:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

