from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.rejection_outcome_audit import write_rejection_outcome_audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write the shadow-only rejection/watchlist outcome audit."
    )
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    args = parser.parse_args(argv)

    txt_path, json_path, csv_path, payload = write_rejection_outcome_audit(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
    )
    summary = payload.get("summary", {})
    print(f"rejection_outcome_audit_txt={txt_path}")
    print(f"rejection_outcome_audit_json={json_path}")
    print(f"rejection_outcome_audit_csv={csv_path}")
    print(
        "rejection_outcome_audit_totals "
        f"graded={summary.get('graded_rows', 0)} "
        f"false_rejects={summary.get('false_reject_candidate_count', 0)} "
        f"status={summary.get('sample_size_status', 'insufficient')} "
        f"interpretation={summary.get('recommended_interpretation', 'too_early')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
