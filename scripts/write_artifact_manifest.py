from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.artifact_manifest import write_artifact_manifest_outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a read-only CourtVision artifact manifest.")
    parser.add_argument("--prediction-date", required=True, help="Prediction date in YYYY-MM-DD format.")
    parser.add_argument("--runtime-root", default="outputs/runtime", help="Runtime output root.")
    args = parser.parse_args(argv)

    operator_path, diagnostics_path, manifest = write_artifact_manifest_outputs(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
    )
    missing = manifest.get("missing_by_severity", {})
    print(f"artifact_manifest_txt={operator_path}")
    print(f"artifact_manifest_json={diagnostics_path}")
    print(
        "artifact_manifest_status "
        f"status={manifest.get('status')} "
        f"fatal_missing={int(missing.get('fatal', 0) or 0)} "
        f"warning_missing={int(missing.get('warning', 0) or 0)} "
        f"informational_missing={int(missing.get('informational', 0) or 0)} "
        f"shadow_only_missing={int(missing.get('shadow_only', 0) or 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
