from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.history_tracking import grade_completed_picks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grade pending rows in data/history/pick_history.csv.")
    parser.add_argument("--prediction-date", default="", help="Optional YYYY-MM-DD filter for pending picks.")
    parser.add_argument("--history-root", default="data/history", help="Long-lived history directory")
    parser.add_argument("--runtime-root", default="outputs/runtime", help="Runtime outputs root")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = grade_completed_picks(
        history_root=args.history_root,
        runtime_root=args.runtime_root,
        prediction_date=(args.prediction_date or None),
    )
    print(f"graded_updates={result['updated_rows']}")
    print(f"pending_remaining={result['pending_rows']}")
    for reason, count in result.get("skip_reasons", {}).items():
        print(f"skip_reason={reason},{count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
