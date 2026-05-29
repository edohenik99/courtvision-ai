from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.safe_action_discovery import write_safe_action_discovery_report_outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the reporting-only Safe Action Discovery audit.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    args = parser.parse_args(argv)

    text_path, json_path, csv_path, payload = write_safe_action_discovery_report_outputs(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
    )
    summary = payload["summary"]
    print(f"safe_action_discovery_report_txt={text_path}")
    print(f"safe_action_discovery_report_json={json_path}")
    print(f"safe_action_discovery_report_csv={csv_path}")
    print(
        "safe_action_discovery_totals "
        f"historical_graded_rows={summary['historical_graded_rows']} "
        f"blocked_historical_rows={summary['blocked_historical_rows']} "
        f"near_elite_artifact_rows={summary['near_elite_artifact_rows']} "
        f"incubator_rows={summary['incubator_rows']} "
        f"high_caution_over_historical_rows={summary['high_caution_over_historical_rows']} "
        f"real_money_promotion_recommended={payload['real_money_promotion_recommended']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
