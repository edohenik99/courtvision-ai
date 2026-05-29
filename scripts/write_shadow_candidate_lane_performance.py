from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.shadow_candidate_lane_performance import (
    write_shadow_candidate_lane_performance_outputs,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Persist and grade Shadow Candidate Lane history, then write paper-only performance reports."
    )
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument(
        "--skip-grading",
        action="store_true",
        help="Persist the daily shadow candidate lane rows and report current performance without grading pending rows.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate persist, grade, and report results without writing history or report artifacts.",
    )
    parser.add_argument(
        "--override-date-integrity",
        action="store_true",
        help="Force history persistence even when a source date mismatch is detected.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    txt_path, csv_path, json_path, payload = write_shadow_candidate_lane_performance_outputs(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
        grade_pending=not args.skip_grading,
        dry_run=args.dry_run,
        override_date_integrity=args.override_date_integrity,
    )
    persist_result = payload.get("persist_result", {})
    grade_result = payload.get("grade_result", {})
    overall = payload.get("overall", {})

    print(f"dry_run={str(bool(args.dry_run)).lower()}")
    print(f"shadow_candidate_lane_history_csv={persist_result.get('shadow_candidate_lane_history_path')}")
    print(f"shadow_candidate_lane_performance_txt={txt_path}")
    print(f"shadow_candidate_lane_performance_csv={csv_path}")
    print(f"shadow_candidate_lane_performance_json={json_path}")
    print(f"persisted_shadow_candidates={persist_result.get('persisted_rows', 0)}")
    print(f"incoming_shadow_candidates={persist_result.get('incoming_rows', 0)}")
    print(f"total_history_rows={persist_result.get('total_rows', 0)}")
    print(f"graded_rows={overall.get('graded_rows', 0)}")
    print(f"pending_rows={overall.get('pending_rows', 0)}")
    print(f"updated_grade_rows={grade_result.get('updated_rows', 0)}")
    print(f"all_rows_real_money_eligible_false={payload.get('all_rows_real_money_eligible_false')}")
    print(f"all_rows_kelly_eligible_false={payload.get('all_rows_kelly_eligible_false')}")
    print(f"all_rows_elite_eligible_false={payload.get('all_rows_elite_eligible_false')}")
    print(f"all_rows_shadow_only_true={payload.get('all_rows_shadow_only_true')}")
    print(f"betting_logic_changed={payload.get('betting_logic_changed')}")

    by_lane = (payload.get("by_dimension") or {}).get("lane", [])
    for item in by_lane:
        print(
            "lane_performance="
            f"{item.get('segment')},"
            f"total={item.get('total_rows', 0)},"
            f"graded={item.get('graded_rows', 0)},"
            f"pending={item.get('pending_rows', 0)},"
            f"hits={item.get('hits', 0)},"
            f"misses={item.get('misses', 0)},"
            f"pushes={item.get('pushes', 0)},"
            f"hit_rate={item.get('hit_rate')},"
            f"flat_roi={item.get('flat_roi')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
