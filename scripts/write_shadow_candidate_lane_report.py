from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.shadow_candidate_lane import write_shadow_candidate_lane_outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the reporting-only Shadow Candidate Lane board.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument("--source-artifact-date", default=None)
    args = parser.parse_args(argv)

    board_path, text_path, json_path, payload = write_shadow_candidate_lane_outputs(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
        source_artifact_date=args.source_artifact_date,
    )
    summary = payload["summary"]
    print(f"shadow_candidate_lane_csv={board_path}")
    print(f"shadow_candidate_lane_report_txt={text_path}")
    print(f"shadow_candidate_lane_json={json_path}")
    print(
        "shadow_candidate_lane_totals "
        f"source_artifact_date={payload['source_artifact_date']} "
        f"shadow_candidates={summary['shadow_candidate_count']} "
        f"lane_counts={summary['lane_counts']} "
        f"all_rows_real_money_eligible_false={payload['all_rows_real_money_eligible_false']} "
        f"betting_logic_changed={payload['betting_logic_changed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
