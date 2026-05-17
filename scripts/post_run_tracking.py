from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.history_tracking import (
    grade_completed_picks,
    persist_market_shadow_history,
    persist_daily_picks,
    update_performance_summaries,
)


def _safe_read(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _recent_metrics(history_root: Path) -> dict[str, float | int]:
    perf = _safe_read(history_root / "performance_summary.csv")
    if perf.empty:
        return {
            "all_time_graded_hit_rate": 0.0,
            "last_7_hit_rate": 0.0,
            "pending_count": 0,
        }
    hits = int(perf.get("hits", pd.Series(dtype=int)).sum())
    misses = int(perf.get("misses", pd.Series(dtype=int)).sum())
    denom = hits + misses
    all_time = float(hits / denom) if denom else 0.0
    recent = perf.sort_values("date").tail(7)
    r_hits = int(recent.get("hits", pd.Series(dtype=int)).sum())
    r_misses = int(recent.get("misses", pd.Series(dtype=int)).sum())
    r_denom = r_hits + r_misses
    last_7 = float(r_hits / r_denom) if r_denom else 0.0
    pending = int(perf.get("pending", pd.Series(dtype=int)).sum())
    return {
        "all_time_graded_hit_rate": all_time,
        "last_7_hit_rate": last_7,
        "pending_count": pending,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persist picks and refresh performance tracking.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument("--grade-pending", action="store_true")
    parser.add_argument("--result-status", default="pending")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate post-run tracking updates without writing history or performance artifacts.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    history_root = Path(args.history_root)
    print(f"dry_run={str(bool(args.dry_run)).lower()}")
    persist_result = persist_daily_picks(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
        result_status=args.result_status,
        dry_run=args.dry_run,
    )
    print(f"persisted_today_rows={persist_result['appended_rows']}")
    print(f"today_elite_count={persist_result['appended_rows']}")

    shadow_result = persist_market_shadow_history(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
        result_status=args.result_status,
        dry_run=args.dry_run,
    )
    print(f"market_shadow_rows_today={shadow_result['current_date_rows']}")
    print(f"market_shadow_non_points_today={shadow_result['current_date_non_points_rows']}")
    print(f"market_shadow_history_csv={shadow_result['market_shadow_history_path']}")
    print(f"market_readiness_summary_csv={shadow_result['market_readiness_summary_path']}")

    if args.grade_pending:
        grade_result = grade_completed_picks(
            history_root=args.history_root,
            runtime_root=args.runtime_root,
            dry_run=args.dry_run,
        )
        print(f"graded_updates={grade_result['updated_rows']}")
    else:
        if args.dry_run:
            print("performance_summary_update_skipped=true")
        else:
            update_performance_summaries(history_root=args.history_root, runtime_root=args.runtime_root)

    metrics = _recent_metrics(history_root)
    print(f"all_time_graded_hit_rate={metrics['all_time_graded_hit_rate']:.4f}")
    print(f"last_7_slates_hit_rate={metrics['last_7_hit_rate']:.4f}")
    print(f"pending_picks_count={int(metrics['pending_count'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
