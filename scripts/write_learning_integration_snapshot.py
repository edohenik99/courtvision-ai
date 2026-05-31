from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.learning_integration_snapshot import (  # noqa: E402
    write_learning_integration_snapshot_outputs,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write the reporting-only learning integration snapshot.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--json", action="store_true", help="Also print JSON diagnostics to stdout.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    txt_path, json_path, payload = write_learning_integration_snapshot_outputs(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
    )
    print(f"learning_integration_snapshot_txt={txt_path}")
    print(f"learning_integration_snapshot_json={json_path}")
    print(f"integration_status={payload['status']}")
    print(f"learning_brain_status={payload['learning_brain_status']}")
    print(f"shadow_rule_proposal_status={payload['shadow_rule_proposal_status']}")
    print(f"total_proposals={payload['total_proposals']}")
    print(f"active_proposal_count={payload['active_proposal_count']}")
    print(f"production_effect_count={payload['production_effect_count']}")
    print(f"applied_changes={str(payload.get('applied_changes', False)).lower()}")
    if args.json:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
