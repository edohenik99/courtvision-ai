from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.threshold_pressure_audit import write_threshold_pressure_audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write the shadow-only threshold pressure and profitability audit."
    )
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    args = parser.parse_args(argv)

    txt_path, json_path, csv_path, payload = write_threshold_pressure_audit(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
    )
    summary = payload.get("summary", {})
    print(f"threshold_pressure_audit_txt={txt_path}")
    print(f"threshold_pressure_audit_json={json_path}")
    print(f"threshold_pressure_audit_csv={csv_path}")
    print(
        "threshold_pressure_audit_totals "
        f"graded={summary.get('graded_rows', 0)} "
        f"profit={summary.get('unit_profit', 0.0)} "
        f"roi={summary.get('roi_percentage')} "
        f"pressure={summary.get('pressure_status', 'too_early')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
