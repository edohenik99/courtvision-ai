from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.shadow_artifact_orchestrator import write_shadow_artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write Phase 4B shadow artifacts before operator summaries consume them."
    )
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument(
        "--closed-slate-safe",
        action="store_true",
        help="Declare the call as a closed-slate reporting refresh. No betting artifacts are changed.",
    )
    args = parser.parse_args(argv)

    summary = write_shadow_artifacts(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
        closed_slate_safe=args.closed_slate_safe,
    )
    print(
        "shadow_artifacts_status "
        f"status={summary['status']} "
        f"failed_count={summary['failed_count']}"
    )
    for report in summary["reports"]:
        line = (
            "shadow_artifact "
            f"report={report['report_name']} "
            f"status={report['status']} "
            f"txt_path={report['txt_path']} "
            f"json_path={report['json_path']}"
        )
        if report.get("csv_path"):
            line += f" csv_path={report['csv_path']}"
        if report.get("error_message"):
            line += f" error={report['error_message']}"
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
