from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.learning_brain import write_learning_brain_report_outputs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write the reporting-only Learning Brain report.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument("--min-sample", type=int, default=20)
    parser.add_argument("--json", action="store_true", help="Also print JSON diagnostics to stdout.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    txt_path, json_path, payload = write_learning_brain_report_outputs(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
        min_sample=args.min_sample,
    )
    print(f"learning_brain_report_txt={txt_path}")
    print(f"learning_brain_report_json={json_path}")
    print(f"learning_brain_status={payload['status']}")
    print(f"promotion_candidates={len(payload.get('promotion_candidates', []))}")
    print(f"applied_changes={str(payload.get('applied_changes', False)).lower()}")
    if args.json:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
