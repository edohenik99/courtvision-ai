"""Publish or resolve immutable research-only materialization authority records."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.sports.mlb.data.prospective_materialization_authority import (  # noqa: E402
    DEFAULT_AUTHORITY_ROOT,
    publish_materialization_authority,
    resolve_materialization_authority,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage immutable research-only materialization authority records"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("publish", "resolve"):
        child = subparsers.add_parser(name)
        child.add_argument("--operating-date", required=True)
        child.add_argument("--event-id", action="append", required=True)
        child.add_argument("--authority-root", type=Path, default=DEFAULT_AUTHORITY_ROOT)
        if name == "publish":
            child.add_argument("--authoritative-manifest", type=Path, required=True)
            child.add_argument("--superseded-manifest", type=Path, action="append", default=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    common = {
        "operating_date": date.fromisoformat(args.operating_date),
        "event_ids": tuple(args.event_id),
        "authority_root": args.authority_root,
    }
    if args.command == "publish":
        result = publish_materialization_authority(
            **common,
            authoritative_manifest_path=args.authoritative_manifest,
            superseded_manifest_paths=tuple(args.superseded_manifest),
        )
    else:
        result = resolve_materialization_authority(**common)
    print(
        json.dumps(
            {
                "authority_id": result.authority_id,
                "authority_path": str(result.authority_path),
                "authoritative_materialization_id": result.authoritative_materialization_id,
                "superseded_materialization_ids": list(
                    result.superseded_materialization_ids
                ),
                "no_op": result.no_op,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
