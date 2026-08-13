"""Explicit, unscheduled MLB HR context-source collection commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.sports.mlb.data.context_source_pack import (  # noqa: E402
    ContextSourceError,
    DEFAULT_SOURCE_RESEARCH_ROOT,
    OPTIONAL_SOURCES,
    SOURCE_NAMES,
    assemble_context_source_pack,
    collect_candidate_snapshot,
    collect_identity_snapshot,
    collect_normalized_source_snapshot,
    collect_statcast_snapshot,
    validate_context_source_pack,
)


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _common_collection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--operating-date", required=True)
    parser.add_argument("--cutoff-utc", required=True)
    parser.add_argument("--collected-at-utc", required=True)
    parser.add_argument("--git-commit", default=None)
    parser.add_argument("--research-root", type=Path, default=DEFAULT_SOURCE_RESEARCH_ROOT)


def _normalized_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[name-defined]
    command: str,
    source_name: str,
) -> None:
    parser = subparsers.add_parser(command)
    _common_collection_arguments(parser)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--provider-version", required=True)
    parser.set_defaults(action="normalized", source_name=source_name)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Collect and assemble immutable, research-only MLB HR context source snapshots. "
            "No command is scheduled and no command trains a model."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    candidates = subparsers.add_parser("collect-context-candidates")
    _common_collection_arguments(candidates)
    candidates.add_argument("--schedule-csv", type=Path, required=True)
    candidates.add_argument("--roster-csv", type=Path, required=True)
    candidates.add_argument("--identity-crosswalk-csv", type=Path, required=True)
    candidates.set_defaults(action="candidates")

    identity = subparsers.add_parser("collect-context-identity")
    _common_collection_arguments(identity)
    identity.add_argument("--identity-crosswalk-csv", type=Path, required=True)
    identity.add_argument("--mapping-source", required=True)
    identity.add_argument("--mapping-version", required=True)
    identity.set_defaults(action="identity")

    statcast = subparsers.add_parser("collect-context-statcast")
    _common_collection_arguments(statcast)
    statcast.add_argument("--statcast-csv", type=Path, required=True)
    statcast.add_argument("--game-clock-csv", type=Path, required=True)
    statcast.set_defaults(action="statcast")

    _normalized_parser(
        subparsers, "collect-context-probable-pitchers", "probable_pitchers"
    )
    _normalized_parser(subparsers, "collect-context-lineups", "lineups")
    _normalized_parser(subparsers, "collect-context-weather", "weather")
    _normalized_parser(subparsers, "collect-context-park-factors", "park_factors")
    _normalized_parser(subparsers, "collect-context-market", "market")

    assemble = subparsers.add_parser("assemble-context-source-pack")
    assemble.add_argument("--operating-date", required=True)
    assemble.add_argument("--cutoff-utc", required=True)
    assemble.add_argument("--assembled-at-utc", required=True)
    assemble.add_argument("--git-commit", default=None)
    assemble.add_argument("--research-root", type=Path, default=DEFAULT_SOURCE_RESEARCH_ROOT)
    for source_name in SOURCE_NAMES:
        assemble.add_argument(
            f"--{source_name.replace('_', '-')}-snapshot",
            type=Path,
            dest=f"snapshot_{source_name}",
        )
    assemble.add_argument(
        "--unavailable",
        action="append",
        default=[],
        metavar="SOURCE=REASON",
        help="Explicit reason for each optional source not supplied.",
    )
    assemble.add_argument(
        "--skip-feature-compatibility",
        action="store_true",
        help="Validate the pack contract without invoking the offline v2 materializer.",
    )
    assemble.set_defaults(action="assemble")

    validate = subparsers.add_parser("validate-context-source-pack")
    validate.add_argument("--pack-root", type=Path, required=True)
    validate.add_argument("--skip-feature-compatibility", action="store_true")
    validate.set_defaults(action="validate")
    return parser


def _unavailable(values: Sequence[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        source_name, separator, reason = value.partition("=")
        if not separator or source_name not in OPTIONAL_SOURCES or not reason.strip():
            raise ContextSourceError(
                "--unavailable must be OPTIONAL_SOURCE=NONEMPTY_REASON"
            )
        if source_name in parsed:
            raise ContextSourceError(f"duplicate --unavailable source: {source_name}")
        parsed[source_name] = reason.strip()
    return parsed


def _snapshot_payload(result: object) -> dict[str, object]:
    return {
        "source_name": getattr(result, "source_name"),
        "snapshot_id": getattr(result, "snapshot_id"),
        "snapshot_dir": str(getattr(result, "snapshot_dir")),
        "data_path": str(getattr(result, "data_path")),
        "manifest_path": str(getattr(result, "manifest_path")),
        "sha256": getattr(result, "sha256"),
        "row_count": getattr(result, "row_count"),
        "research_only": True,
        "model_training_performed": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.action == "validate":
            validation = validate_context_source_pack(
                args.pack_root,
                require_feature_compatibility=not args.skip_feature_compatibility,
            )
            print(
                json.dumps(
                    {
                        "pack_dir": str(validation.pack_dir),
                        "is_valid": validation.is_valid,
                        "errors": list(validation.errors),
                        "warnings": list(validation.warnings),
                        "feature_row_count": validation.feature_row_count,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0 if validation.is_valid else 2

        commit = args.git_commit or _git_commit()
        if args.action == "candidates":
            result = collect_candidate_snapshot(
                args.schedule_csv,
                args.roster_csv,
                args.identity_crosswalk_csv,
                operating_date=args.operating_date,
                cutoff_utc=args.cutoff_utc,
                collected_at_utc=args.collected_at_utc,
                git_commit=commit,
                research_root=args.research_root,
            )
        elif args.action == "identity":
            result = collect_identity_snapshot(
                args.identity_crosswalk_csv,
                operating_date=args.operating_date,
                cutoff_utc=args.cutoff_utc,
                collected_at_utc=args.collected_at_utc,
                mapping_source=args.mapping_source,
                mapping_version=args.mapping_version,
                git_commit=commit,
                research_root=args.research_root,
            )
        elif args.action == "statcast":
            result = collect_statcast_snapshot(
                args.statcast_csv,
                args.game_clock_csv,
                operating_date=args.operating_date,
                cutoff_utc=args.cutoff_utc,
                collected_at_utc=args.collected_at_utc,
                git_commit=commit,
                research_root=args.research_root,
            )
        elif args.action == "normalized":
            result = collect_normalized_source_snapshot(
                args.source_name,
                args.input_csv,
                operating_date=args.operating_date,
                cutoff_utc=args.cutoff_utc,
                collected_at_utc=args.collected_at_utc,
                provider=args.provider,
                collector_configuration={"provider_version": args.provider_version},
                git_commit=commit,
                research_root=args.research_root,
            )
        elif args.action == "assemble":
            snapshots = {
                source_name: value
                for source_name in SOURCE_NAMES
                if (value := getattr(args, f"snapshot_{source_name}")) is not None
            }
            pack = assemble_context_source_pack(
                operating_date=args.operating_date,
                cutoff_utc=args.cutoff_utc,
                assembled_at_utc=args.assembled_at_utc,
                snapshot_dirs=snapshots,
                unavailable_sources=_unavailable(args.unavailable),
                git_commit=commit,
                research_root=args.research_root,
                validate_feature_compatibility=not args.skip_feature_compatibility,
            )
            print(
                json.dumps(
                    {
                        "pack_id": pack.pack_id,
                        "pack_dir": str(pack.pack_dir),
                        "manifest_path": str(pack.manifest_path),
                        "source_paths": {
                            key: str(value) for key, value in pack.source_paths.items()
                        },
                        "research_only": True,
                        "model_training_performed": False,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        else:  # pragma: no cover - argparse owns the closed command set.
            parser.error("unsupported command")
            return 2
    except (ContextSourceError, FileExistsError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(_snapshot_payload(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
