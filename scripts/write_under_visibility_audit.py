from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.under_visibility_audit import write_under_visibility_audit_outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the reporting-only UNDER Candidate Visibility Audit.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    args = parser.parse_args(argv)

    csv_path, text_path, json_path, payload = write_under_visibility_audit_outputs(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
    )
    funnel = payload["funnel_stages"]
    print(f"under_visibility_audit_csv={csv_path}")
    print(f"under_visibility_audit_report_txt={text_path}")
    print(f"under_visibility_audit_json={json_path}")
    print(
        "under_visibility_audit_funnel "
        f"raw_over={funnel['raw_odds']['over']} "
        f"raw_under={funnel['raw_odds']['under']} "
        f"normalized_over={funnel['normalized']['over']} "
        f"normalized_under={funnel['normalized']['under']} "
        f"full_market_over={funnel['full_market']['over']} "
        f"full_market_under={funnel['full_market']['under']} "
        f"shadow_over={funnel['shadow_candidate_lane']['over']} "
        f"shadow_under={funnel['shadow_candidate_lane']['under']} "
        f"betting_logic_changed={payload['betting_logic_changed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
