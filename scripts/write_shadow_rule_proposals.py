from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.shadow_rule_proposals import write_shadow_rule_proposals_outputs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write disabled shadow adaptive rule proposal reports.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--min-sample", type=int, default=20)
    parser.add_argument("--confidence-z", type=float, default=1.96)
    parser.add_argument("--json", action="store_true", help="Also print JSON diagnostics to stdout.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    txt_path, json_path, payload = write_shadow_rule_proposals_outputs(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        min_sample=args.min_sample,
        confidence_z=args.confidence_z,
    )
    print(f"shadow_rule_proposals_txt={txt_path}")
    print(f"shadow_rule_proposals_json={json_path}")
    print(f"shadow_rule_proposals_status={payload['status']}")
    print(f"proposal_count={len(payload.get('proposals', []))}")
    print(f"active_proposal_count={payload.get('active_proposal_count', 0)}")
    print(f"production_effect_count={payload.get('production_effect_count', 0)}")
    print(f"applied_changes={str(payload.get('applied_changes', False)).lower()}")
    if args.json:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
