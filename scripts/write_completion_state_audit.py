from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.completion_state_audit import write_completion_state_audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the completion state audit report.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--diagnostics-output-path")
    parser.add_argument("--operator-output-path")
    args = parser.parse_args(argv)

    text_path, json_path, payload = write_completion_state_audit(
        prediction_date=args.prediction_date,
        history_root=args.history_root,
        runtime_root=args.runtime_root,
        diagnostics_output_path=args.diagnostics_output_path,
        operator_output_path=args.operator_output_path,
    )
    print(f"completion_state_audit_txt={text_path}")
    print(f"completion_state_audit_json={json_path}")
    print(
        "completion_state_audit_status "
        f"status={payload['report_agreement_status']} "
        f"real_pending={payload['real_pick_pending_count']} "
        f"shadow_pending={payload['shadow_pending_count']} "
        f"shadow_open_game_pending={payload['shadow_open_game_pending_count']} "
        f"shadow_stale_pending={payload['shadow_stale_pending_count']} "
        f"paper_pending={payload['paper_pending_count']} "
        f"paper_open_game_pending={payload['paper_open_game_pending_count']} "
        f"paper_stale_pending={payload['paper_stale_pending_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
