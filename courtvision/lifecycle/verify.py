"""Read-only command for verifying committed lifecycle shadow segments."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from courtvision.lifecycle.writer import verify_all_segments, verify_segment


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify immutable CourtVision lifecycle shadow segments."
    )
    parser.add_argument(
        "--lifecycle-root",
        default="data/lifecycle",
        help="Lifecycle root. Defaults to data/lifecycle.",
    )
    parser.add_argument(
        "--segment",
        help="Optional specific segment directory; otherwise verify every complete segment.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.lifecycle_root)
    if args.segment:
        results = (verify_segment(args.segment, lifecycle_root=root),)
    else:
        results = verify_all_segments(root)
    payload = {
        "lifecycle_root": root.as_posix(),
        "segment_count": len(results),
        "ok": all(result.ok for result in results),
        "segments": [
            {
                "segment_directory": result.segment_directory.as_posix(),
                "ok": result.ok,
                "event_count": result.event_count,
                "violations": list(result.violations),
            }
            for result in results
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
