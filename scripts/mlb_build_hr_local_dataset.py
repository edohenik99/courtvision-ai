"""Build an audited MLB HR historical dataset from explicit local CSV files."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Final, Mapping, Sequence


PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
FIXTURE_DIR: Final = PROJECT_ROOT / "tests" / "fixtures" / "mlb"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.sports.mlb.data.ballpark_factors import (
    ingest_local_ballpark_factors_csv,
    normalize_venue_name,
)
from courtvision.sports.mlb.data.retrosheet_ingestion import (
    ingest_local_retrosheet_csvs,
)
from courtvision.sports.mlb.data.odds_snapshot_ingestion import (
    ingest_local_odds_snapshot_csv,
)
from courtvision.sports.mlb.data.statcast_ingestion import (
    GAME_ID_COLUMNS,
    REQUIRED_STATCAST_COLUMNS,
    ingest_local_statcast_csv,
)
from courtvision.sports.mlb.data.weather_ingestion import (
    ingest_local_weather_csv,
)
from courtvision.sports.mlb.data_manifest import compute_file_sha256
from courtvision.sports.mlb.training.hr_dataset_builder import (
    build_hr_batter_game_rows_from_sources,
    write_hr_dataset_csv,
    write_hr_dataset_metadata_json,
)
from courtvision.sports.mlb.training.hr_leakage_audit import (
    audit_hr_batter_game_rows,
    write_audit_report_json,
)
from courtvision.sports.mlb.training.hr_dataset_readiness import (
    build_hr_dataset_readiness_report,
    write_readiness_report_json,
    write_readiness_report_txt,
)


LOCAL_DATASET_VERSION: Final = "phase4d-local-file-v1"
LOCAL_GENERATED_BY: Final = "scripts.mlb_build_hr_local_dataset"
SOURCE_MANIFEST_VERSION: Final = "phase4e-source-manifest-v1"
STATCAST_TRIAL_MANIFEST_VERSION: Final = "phase5a-statcast-trial-v1"
LABEL_PAIRING_TRIAL_VERSION: Final = "phase5b-label-pairing-trial-v1"
CONTEXT_PAIRING_TRIAL_VERSION: Final = "phase5c-context-pairing-trial-v1"
ODDS_PAIRING_TRIAL_VERSION: Final = "phase5d-odds-pairing-trial-v1"
HISTORICAL_DRY_RUN_VERSION: Final = "phase6a-historical-dry-run-v1"
FIXTURE_SOURCE_COLLECTED_AT: Final = datetime(
    2026, 6, 19, 18, 0, tzinfo=timezone.utc
)
PACK_FILENAMES: Final = (
    "dataset.csv",
    "metadata.json",
    "audit.json",
    "source_manifest.json",
    "build_summary.txt",
    "readiness.json",
    "readiness_summary.txt",
)
STATCAST_TRIAL_PACK_FILENAMES: Final = (
    "statcast_preview.json",
    "source_manifest.json",
    "build_summary.txt",
)
BASE_INPUT_ARGUMENTS: Final = (
    "statcast_csv",
    "retrosheet_games_csv",
    "retrosheet_events_csv",
    "weather_csv",
    "ballpark_csv",
)
INPUT_ARGUMENTS: Final = (*BASE_INPUT_ARGUMENTS, "odds_csv")
INPUT_LABELS: Final = {
    "statcast_csv": "--statcast-csv",
    "retrosheet_games_csv": "--retrosheet-games-csv",
    "retrosheet_events_csv": "--retrosheet-events-csv",
    "weather_csv": "--weather-csv",
    "ballpark_csv": "--ballpark-csv",
    "odds_csv": "--odds-csv",
}
FIXTURE_PATHS: Final = {
    "statcast_csv": FIXTURE_DIR / "statcast_sample.csv",
    "retrosheet_games_csv": FIXTURE_DIR / "retrosheet_games_sample.csv",
    "retrosheet_events_csv": FIXTURE_DIR / "retrosheet_events_sample.csv",
    "weather_csv": FIXTURE_DIR / "weather_sample.csv",
    "ballpark_csv": FIXTURE_DIR / "ballpark_factors_sample.csv",
    "odds_csv": None,
}
DISPLAY_FIELDS: Final = (
    "player_name",
    "player_id",
    "game_id",
    "game_date",
    "team",
    "opponent",
    "venue_name",
    "hit_hr_today",
    "home_run_count",
    "weather_temperature",
    "weather_wind_speed",
    "park_factor_hr",
    "sportsbook",
    "american_odds",
    "decimal_odds",
    "implied_probability",
    "odds_collected_at",
    "odds_is_fresh_for_pregame",
    "eligible_for_training",
    "eligible_for_backtest",
    "approval_status",
    "missing_required_fields",
    "warnings",
)
STATCAST_TRIAL_PREVIEW_FIELDS: Final = (
    "game_date",
    "game_id",
    "player_id",
    "player_name",
    "event_type",
    "is_home_run",
    "home_team",
    "away_team",
)


class LocalDatasetCLIError(ValueError):
    """Raised for clear command-line contract failures."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build and audit MLB HR batter-game rows from local CSV files for "
            "historical research only. The default is a dry run."
        )
    )
    parser.add_argument(
        "--fixtures",
        action="store_true",
        help="Use the repository's local MLB CSV fixtures.",
    )
    parser.add_argument("--statcast-csv", type=Path)
    parser.add_argument(
        "--statcast-trial",
        action="store_true",
        help=(
            "Validate only the provided local Statcast CSV. No HR batter-game "
            "dataset rows are built without Retrosheet game/event context."
        ),
    )
    parser.add_argument(
        "--label-pairing-trial",
        action="store_true",
        help=(
            "Build Retrosheet-labeled batter-game rows from explicit local "
            "Statcast and Retrosheet CSVs. Exact identities only; no network access."
        ),
    )
    parser.add_argument(
        "--context-pairing-trial",
        action="store_true",
        help=(
            "Build Retrosheet-labeled batter-game rows with weather and ballpark "
            "context from five explicit local CSVs. No network access."
        ),
    )
    parser.add_argument(
        "--odds-pairing-trial",
        action="store_true",
        help=(
            "Attach local HR odds snapshot market references to labeled rows with "
            "weather and ballpark context. Local files only."
        ),
    )
    parser.add_argument(
        "--historical-dry-run",
        action="store_true",
        help=(
            "Run a real local-file historical dry run with explicit CSV paths. "
            "No APIs, downloads, model training, or production approval."
        ),
    )
    parser.add_argument("--retrosheet-games-csv", type=Path)
    parser.add_argument("--retrosheet-events-csv", type=Path)
    parser.add_argument("--weather-csv", type=Path)
    parser.add_argument("--ballpark-csv", type=Path)
    parser.add_argument("--odds-csv", type=Path)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Allow absent input sources and keep missing context visible.",
    )
    parser.add_argument("--output-csv", type=Path)
    parser.add_argument("--audit-json", type=Path)
    parser.add_argument("--metadata-json", type=Path)
    parser.add_argument("--readiness-report-json", type=Path)
    parser.add_argument("--readiness-report-txt", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Write a complete reproducibility pack to this directory.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace explicitly requested output files that already exist.",
    )
    return parser


def _resolve_inputs(args: argparse.Namespace) -> tuple[str, dict[str, Path | None]]:
    supplied = {name: getattr(args, name) for name in INPUT_ARGUMENTS}
    selected_trials = sum(
        bool(value)
        for value in (
            args.statcast_trial,
            args.label_pairing_trial,
            args.context_pairing_trial,
            args.odds_pairing_trial,
            args.historical_dry_run,
        )
    )
    if selected_trials > 1:
        raise LocalDatasetCLIError(
            "--statcast-trial, --label-pairing-trial, "
            "--context-pairing-trial, --odds-pairing-trial, and "
            "--historical-dry-run are mutually exclusive"
        )
    if args.historical_dry_run:
        if args.fixtures:
            raise LocalDatasetCLIError(
                "--historical-dry-run requires explicit local CSV paths"
            )
        missing = [
            INPUT_LABELS[name]
            for name in INPUT_ARGUMENTS
            if supplied[name] is None
        ]
        if missing:
            raise LocalDatasetCLIError(
                "--historical-dry-run requires " + ", ".join(missing)
            )
        paths = supplied
        mode = "historical_dry_run"
    elif args.odds_pairing_trial:
        if args.fixtures:
            raise LocalDatasetCLIError(
                "--odds-pairing-trial requires explicit local CSV paths"
            )
        missing = [
            INPUT_LABELS[name] for name in INPUT_ARGUMENTS if supplied[name] is None
        ]
        if missing:
            raise LocalDatasetCLIError(
                "--odds-pairing-trial requires " + ", ".join(missing)
            )
        paths = supplied
        mode = "odds_pairing_trial"
    elif args.context_pairing_trial:
        if args.fixtures:
            raise LocalDatasetCLIError(
                "--context-pairing-trial requires explicit local CSV paths"
            )
        missing = [
            INPUT_LABELS[name]
            for name in BASE_INPUT_ARGUMENTS
            if supplied[name] is None
        ]
        if missing:
            raise LocalDatasetCLIError(
                "--context-pairing-trial requires " + ", ".join(missing)
            )
        paths = supplied
        mode = "context_pairing_trial"
        if supplied["odds_csv"] is not None:
            raise LocalDatasetCLIError(
                "--context-pairing-trial does not load --odds-csv; "
                "use --odds-pairing-trial"
            )
    elif args.label_pairing_trial:
        if args.fixtures:
            raise LocalDatasetCLIError(
                "--label-pairing-trial requires explicit local CSV paths"
            )
        required = (
            "statcast_csv",
            "retrosheet_games_csv",
            "retrosheet_events_csv",
        )
        missing = [INPUT_LABELS[name] for name in required if supplied[name] is None]
        if missing:
            raise LocalDatasetCLIError(
                "--label-pairing-trial requires " + ", ".join(missing)
            )
        later_phase_inputs = [
            INPUT_LABELS[name]
            for name in ("weather_csv", "ballpark_csv", "odds_csv")
            if supplied[name] is not None
        ]
        if later_phase_inputs:
            raise LocalDatasetCLIError(
                "--label-pairing-trial is limited to Statcast and Retrosheet; "
                "remove: " + ", ".join(later_phase_inputs)
            )
        paths = supplied
        mode = "label_pairing_trial"
    elif args.statcast_trial:
        non_statcast = [
            INPUT_LABELS[name]
            for name, path in supplied.items()
            if name != "statcast_csv" and path is not None
        ]
        if args.fixtures:
            raise LocalDatasetCLIError(
                "--statcast-trial requires an explicit --statcast-csv path; "
                "use the repository fixture path for a fixture trial"
            )
        if args.statcast_csv is None:
            raise LocalDatasetCLIError(
                "--statcast-trial requires --statcast-csv"
            )
        if non_statcast:
            raise LocalDatasetCLIError(
                "--statcast-trial loads only --statcast-csv; remove: "
                + ", ".join(non_statcast)
            )
        paths = {name: None for name in INPUT_ARGUMENTS}
        paths["statcast_csv"] = args.statcast_csv
        mode = "statcast_trial"
    elif args.fixtures and any(supplied.values()):
        raise LocalDatasetCLIError(
            "--fixtures cannot be combined with explicit local CSV paths"
        )
    elif args.fixtures:
        paths = dict(FIXTURE_PATHS)
        mode = "fixtures"
    else:
        if not any(supplied.values()):
            raise LocalDatasetCLIError(
                "provide --fixtures or explicit local CSV paths"
            )
        paths = supplied
        mode = "local_files"
        if supplied["odds_csv"] is not None:
            raise LocalDatasetCLIError(
                "--odds-csv requires --odds-pairing-trial"
            )

    for name, path in paths.items():
        if path is not None and not path.expanduser().is_file():
            raise LocalDatasetCLIError(
                f"{INPUT_LABELS[name]} path does not exist: {path.expanduser()}"
            )

    missing = [
        INPUT_LABELS[name]
        for name in BASE_INPUT_ARGUMENTS
        if paths[name] is None
    ]
    if missing and not args.allow_partial and mode not in {
        "statcast_trial",
        "label_pairing_trial",
        "context_pairing_trial",
        "odds_pairing_trial",
        "historical_dry_run",
    }:
        raise LocalDatasetCLIError(
            "partial local inputs require --allow-partial; missing: "
            + ", ".join(missing)
        )
    return mode, {
        name: path.expanduser().resolve() if path is not None else None
        for name, path in paths.items()
    }


def _pack_paths(
    output_dir: Path, *, filenames: Sequence[str] = PACK_FILENAMES
) -> dict[str, Path]:
    root = output_dir.expanduser().resolve()
    return {filename: root / filename for filename in filenames}


def _preflight_outputs(args: argparse.Namespace) -> dict[str, Path]:
    dataset_outputs = (
        ("--output-csv", args.output_csv),
        ("--audit-json", args.audit_json),
        ("--metadata-json", args.metadata_json),
    )
    readiness_outputs = (
        ("--readiness-report-json", args.readiness_report_json),
        ("--readiness-report-txt", args.readiness_report_txt),
    )
    requested = (*dataset_outputs, *readiness_outputs)
    if args.output_dir is not None and any(path for _, path in requested):
        raise LocalDatasetCLIError(
            "--output-dir cannot be combined with explicit output file flags"
        )

    if args.statcast_trial and any(path for _, path in requested):
        raise LocalDatasetCLIError(
            "--statcast-trial does not write dataset, audit, or metadata outputs; "
            "use --output-dir for the trial-safe pack"
        )

    if (
        args.label_pairing_trial
        or args.context_pairing_trial
        or args.odds_pairing_trial
        or args.historical_dry_run
    ) and any(
        path for _, path in dataset_outputs
    ):
        trial_flag = (
            "--historical-dry-run"
            if args.historical_dry_run
            else "--odds-pairing-trial"
            if args.odds_pairing_trial
            else
            "--context-pairing-trial"
            if args.context_pairing_trial
            else "--label-pairing-trial"
        )
        raise LocalDatasetCLIError(
            f"{trial_flag} writes only the explicit --output-dir pack"
        )

    if args.output_dir is not None:
        output_dir = args.output_dir.expanduser().resolve()
        if output_dir.exists() and not output_dir.is_dir():
            raise LocalDatasetCLIError(
                f"--output-dir is not a directory: {output_dir}"
            )
        filenames = (
            STATCAST_TRIAL_PACK_FILENAMES
            if args.statcast_trial
            else PACK_FILENAMES
        )
        pack_paths = _pack_paths(output_dir, filenames=filenames)
        if not args.overwrite:
            for path in pack_paths.values():
                if path.exists():
                    raise LocalDatasetCLIError(
                        "build pack target already exists; pass --overwrite to "
                        f"replace it: {path}"
                    )
        return pack_paths

    destinations: list[tuple[str, Path]] = []
    for label, raw_path in requested:
        if raw_path is None:
            continue
        path = raw_path.expanduser().resolve()
        if not path.parent.is_dir():
            raise LocalDatasetCLIError(
                f"{label} parent directory does not exist: {path.parent}"
            )
        destinations.append((label, path))

    normalized = [path for _, path in destinations]
    if len(normalized) != len(set(normalized)):
        raise LocalDatasetCLIError("output paths must be distinct")
    if not args.overwrite:
        for label, path in destinations:
            if path.exists():
                raise LocalDatasetCLIError(
                    f"{label} already exists; pass --overwrite to replace it: {path}"
                )
    return {}


def _manifest_id(result: object) -> str | None:
    manifest = getattr(result, "manifest", None)
    if manifest is None:
        return None
    source_name = str(getattr(manifest, "source_name", "")).strip()
    checksum = str(getattr(manifest, "checksum", "")).strip()
    if source_name and checksum:
        return f"{source_name}:{checksum}"
    return source_name or None


def _display_value(value: object) -> object:
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else value


def _source_warnings(result: object) -> list[str]:
    manifest = getattr(result, "manifest", None)
    return list(getattr(manifest, "warnings", ())) if manifest is not None else []


def _source_manifest_entries(
    *,
    mode: str,
    paths: Mapping[str, Path | None],
    statcast: object | None,
    retrosheet: object | None,
    weather: object | None,
    ballpark: object | None,
    odds: object | None,
    allow_partial: bool,
) -> list[dict[str, object]]:
    source_specs = (
        (
            "statcast_csv",
            "statcast",
            statcast,
            len(statcast.rows) if statcast is not None else 0,
        ),
        (
            "retrosheet_games_csv",
            "retrosheet_games",
            retrosheet,
            len(retrosheet.games) if retrosheet is not None else 0,
        ),
        (
            "retrosheet_events_csv",
            "retrosheet_events",
            retrosheet,
            len(retrosheet.events) if retrosheet is not None else 0,
        ),
        (
            "weather_csv",
            "weather",
            weather,
            len(weather.rows) if weather is not None else 0,
        ),
        (
            "ballpark_csv",
            "ballpark_factors",
            ballpark,
            len(ballpark.rows) if ballpark is not None else 0,
        ),
        (
            "odds_csv",
            "odds_snapshot",
            odds,
            len(odds.rows) if odds is not None else 0,
        ),
    )
    entries: list[dict[str, object]] = []
    for argument_name, source_name, result, parsed_row_count in source_specs:
        path = paths[argument_name]
        if path is None:
            continue
        entries.append(
            {
                "source_name": source_name,
                "source_type": "fixture" if mode == "fixtures" else "local_file",
                "path": str(path),
                "file_exists": path.is_file(),
                "byte_size": path.stat().st_size,
                "sha256": compute_file_sha256(path),
                "parsed_row_count": parsed_row_count,
                "required_or_optional": "optional" if allow_partial else "required",
                "loaded_successfully": result is not None,
                "warnings": _source_warnings(result) if result is not None else [],
            }
        )
    return entries


def _write_json(path: Path, payload: Mapping[str, object], *, overwrite: bool) -> None:
    mode = "w" if overwrite else "x"
    with path.open(mode, encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _statcast_column_warnings(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            fieldnames = csv.DictReader(handle).fieldnames
    except (OSError, UnicodeError, csv.Error) as exc:
        return [f"could not inspect Statcast CSV header: {exc}"]

    if not fieldnames:
        return ["missing required Statcast header row"]

    available = set(fieldnames)
    warnings = [
        f"missing required Statcast column: {column}"
        for column in sorted(REQUIRED_STATCAST_COLUMNS - available)
    ]
    if not any(column in available for column in GAME_ID_COLUMNS):
        warnings.append("missing required Statcast game id column: game_pk or game_id")
    return warnings


def _statcast_trial_preview_rows(statcast: object) -> list[dict[str, object]]:
    return [
        {
            field_name: _display_value(getattr(row, field_name))
            for field_name in STATCAST_TRIAL_PREVIEW_FIELDS
        }
        for row in statcast.rows[:5]
    ]


def _statcast_trial_metrics(statcast: object) -> dict[str, object]:
    rows = statcast.rows
    return {
        "parsed_row_count": len(rows),
        "detected_date_range_start": statcast.manifest.date_range_start.isoformat(),
        "detected_date_range_end": statcast.manifest.date_range_end.isoformat(),
        "unique_game_count": len({row.game_id for row in rows}),
        "unique_batter_count": len({row.player_id for row in rows}),
        "hr_event_count": sum(1 for row in rows if row.is_home_run),
    }


def _statcast_trial_source_entry(
    *,
    path: Path,
    statcast: object,
    metrics: Mapping[str, object],
    column_warnings: Sequence[str],
) -> dict[str, object]:
    warnings = [
        *_source_warnings(statcast),
        *column_warnings,
        "Partial context: Retrosheet game/event labels were not provided.",
        "Dataset rows were not built without Retrosheet labels.",
    ]
    return {
        "source_name": "statcast",
        "source_type": "local_file",
        "path": str(path),
        "file_exists": path.is_file(),
        "byte_size": path.stat().st_size,
        "sha256": compute_file_sha256(path),
        **metrics,
        "loaded_successfully": True,
        "warnings": warnings,
    }


def _statcast_trial_summary_text(
    *,
    generated_at: datetime,
    metrics: Mapping[str, object],
    column_warnings: Sequence[str],
) -> str:
    return "\n".join(
        (
            "CourtVision local Statcast trial",
            "historical research only",
            "local Statcast trial",
            "partial context",
            "not production approved",
            "Statcast parsed successfully",
            f"generated_at = {generated_at.isoformat()}",
            f"parsed Statcast row count = {metrics['parsed_row_count']}",
            "detected date range = "
            f"{metrics['detected_date_range_start']} to "
            f"{metrics['detected_date_range_end']}",
            f"unique game count = {metrics['unique_game_count']}",
            f"unique batter count = {metrics['unique_batter_count']}",
            f"HR event count = {metrics['hr_event_count']}",
            "missing required Statcast column warnings = "
            f"{len(column_warnings)}",
            "Dataset rows require Retrosheet game/event context",
            "dataset rows not built without Retrosheet labels",
            "HR batter-game dataset row count = 0",
            "",
        )
    )


def _write_statcast_trial_pack(
    *,
    args: argparse.Namespace,
    pack_paths: Mapping[str, Path],
    generated_at: datetime,
    source_path: Path,
    statcast: object,
    metrics: Mapping[str, object],
    column_warnings: Sequence[str],
    preview_rows: Sequence[Mapping[str, object]],
) -> None:
    output_dir = next(iter(pack_paths.values())).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    safety_notice = (
        "historical research only",
        "local Statcast trial",
        "partial context",
        "not production approved",
        "dataset rows not built without Retrosheet labels",
    )
    preview = {
        "preview_version": STATCAST_TRIAL_MANIFEST_VERSION,
        "mode": "statcast_trial",
        "generated_at": generated_at.isoformat(),
        **metrics,
        "dataset_row_count": 0,
        "safety_notice": safety_notice,
        "rows": list(preview_rows),
    }
    source_entry = _statcast_trial_source_entry(
        path=source_path,
        statcast=statcast,
        metrics=metrics,
        column_warnings=column_warnings,
    )
    source_manifest = {
        "manifest_version": STATCAST_TRIAL_MANIFEST_VERSION,
        "mode": "statcast_trial",
        "generated_at": generated_at.isoformat(),
        "dataset_row_count": 0,
        "safety_notice": safety_notice,
        "sources": [source_entry],
    }
    _write_json(
        pack_paths["statcast_preview.json"], preview, overwrite=args.overwrite
    )
    _write_json(
        pack_paths["source_manifest.json"],
        source_manifest,
        overwrite=args.overwrite,
    )
    mode = "w" if args.overwrite else "x"
    with pack_paths["build_summary.txt"].open(
        mode, encoding="utf-8", newline="\n"
    ) as handle:
        handle.write(
            _statcast_trial_summary_text(
                generated_at=generated_at,
                metrics=metrics,
                column_warnings=column_warnings,
            )
        )


def _run_statcast_trial(
    *,
    args: argparse.Namespace,
    paths: Mapping[str, Path | None],
    pack_paths: Mapping[str, Path],
    generated_at: datetime,
) -> None:
    source_path = paths["statcast_csv"]
    if source_path is None:
        raise LocalDatasetCLIError("--statcast-trial requires --statcast-csv")

    column_warnings = _statcast_column_warnings(source_path)
    for warning in column_warnings:
        print(f"warning: {warning}", file=sys.stderr)

    statcast = ingest_local_statcast_csv(source_path, collected_at=generated_at)
    metrics = _statcast_trial_metrics(statcast)
    preview_rows = _statcast_trial_preview_rows(statcast)

    if pack_paths:
        _write_statcast_trial_pack(
            args=args,
            pack_paths=pack_paths,
            generated_at=generated_at,
            source_path=source_path,
            statcast=statcast,
            metrics=metrics,
            column_warnings=column_warnings,
            preview_rows=preview_rows,
        )

    print("CourtVision local Statcast trial")
    print(
        "historical research only | local Statcast trial | partial context | "
        "not production approved"
    )
    print("Statcast parsed successfully")
    print(f"mode: statcast_trial")
    print(f"parsed Statcast row count: {metrics['parsed_row_count']}")
    print(
        "detected date range: "
        f"{metrics['detected_date_range_start']} to "
        f"{metrics['detected_date_range_end']}"
    )
    print(f"unique game count: {metrics['unique_game_count']}")
    print(f"unique batter count: {metrics['unique_batter_count']}")
    print(f"HR event count: {metrics['hr_event_count']}")
    print(
        "missing required Statcast column warnings: "
        f"{len(column_warnings)}"
    )
    print("Dataset rows require Retrosheet game/event context")
    print("dataset rows not built without Retrosheet labels")
    print("HR batter-game rows: 0")
    print("first 5 parsed Statcast rows:")
    for row in preview_rows:
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))


def _identity_text(value: object) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _row_identity(row: object, *, player_field: str) -> tuple[str, object, str] | None:
    game_id = _identity_text(getattr(row, "game_id", None))
    game_date = getattr(row, "game_date", None)
    player_id = _identity_text(getattr(row, player_field, None))
    if game_id is None or game_date is None or player_id is None:
        return None
    return game_id, game_date, player_id


def _label_pairing_quality(
    *,
    statcast: object,
    retrosheet: object,
    dataset: object,
) -> dict[str, object]:
    statcast_game_keys = {
        (row.game_id, row.game_date)
        for row in statcast.rows
        if _identity_text(getattr(row, "game_id", None)) is not None
        and getattr(row, "game_date", None) is not None
    }
    retrosheet_game_keys = {
        (row.game_id, row.game_date)
        for row in retrosheet.games
        if _identity_text(getattr(row, "game_id", None)) is not None
        and getattr(row, "game_date", None) is not None
    }
    statcast_batters = {
        identity
        for row in statcast.rows
        if (identity := _row_identity(row, player_field="player_id")) is not None
    }
    retrosheet_batters = {
        identity
        for row in retrosheet.events
        if (identity := _row_identity(row, player_field="batter_id")) is not None
    }
    output_batters = {
        identity
        for row in dataset.rows
        if (identity := _row_identity(row, player_field="player_id")) is not None
    }
    row_ids = [
        value
        for row in dataset.rows
        if (value := _identity_text(getattr(row, "row_id", None))) is not None
    ]
    source_rows = (*statcast.rows, *retrosheet.games, *retrosheet.events)
    player_rows = (*statcast.rows, *retrosheet.events)
    metrics: dict[str, object] = {
        "unmatched_statcast_games": len(statcast_game_keys - retrosheet_game_keys),
        "unmatched_retrosheet_games": len(retrosheet_game_keys - statcast_game_keys),
        "unmatched_batters": len(statcast_batters ^ retrosheet_batters),
        "retrosheet_events_without_matching_batter_game_rows": sum(
            _row_identity(row, player_field="batter_id") not in output_batters
            for row in retrosheet.events
        ),
        "statcast_rows_without_game_labels": sum(
            _row_identity(row, player_field="player_id") not in retrosheet_batters
            for row in statcast.rows
        ),
        "duplicate_batter_game_row_ids": len(row_ids) - len(set(row_ids)),
        "missing_player_ids": sum(
            _identity_text(
                getattr(
                    row,
                    "batter_id" if hasattr(row, "batter_id") else "player_id",
                    None,
                )
            )
            is None
            for row in player_rows
        ),
        "missing_game_ids": sum(
            _identity_text(getattr(row, "game_id", None)) is None
            for row in source_rows
        ),
        "missing_game_dates": sum(
            getattr(row, "game_date", None) is None for row in source_rows
        ),
    }
    warning_labels = {
        "unmatched_statcast_games": "unmatched Statcast games",
        "unmatched_retrosheet_games": "unmatched Retrosheet games",
        "unmatched_batters": "unmatched batters",
        "retrosheet_events_without_matching_batter_game_rows": (
            "Retrosheet events without matching batter-game rows"
        ),
        "statcast_rows_without_game_labels": "Statcast rows without game labels",
        "duplicate_batter_game_row_ids": "duplicate batter-game row IDs",
        "missing_player_ids": "missing player IDs",
        "missing_game_ids": "missing game IDs",
        "missing_game_dates": "missing game dates",
    }
    metrics["warnings"] = tuple(
        f"{label}: {metrics[key]}"
        for key, label in warning_labels.items()
        if metrics[key]
    )
    return metrics


def _context_pairing_quality(
    *,
    retrosheet: object,
    weather: object,
    ballpark: object,
    dataset: object,
) -> dict[str, object]:
    """Summarize deterministic local context joins without changing row data."""

    weather_by_game: dict[tuple[str, object], list[object]] = {}
    weather_by_venue: dict[tuple[str, object], list[object]] = {}
    game_dates_by_id: dict[str, set[object]] = {}
    for game in retrosheet.games:
        game_id = _identity_text(getattr(game, "game_id", None))
        game_date = getattr(game, "game_date", None)
        if game_id is not None and game_date is not None:
            game_dates_by_id.setdefault(game_id, set()).add(game_date)
    for row in weather.rows:
        game_id = _identity_text(getattr(row, "game_id", None))
        game_date = getattr(row, "game_date", None)
        venue_name = _identity_text(getattr(row, "venue_name", None))
        if game_id is not None and game_date is not None:
            weather_by_game.setdefault((game_id, game_date), []).append(row)
        if venue_name is not None and game_date is not None:
            weather_by_venue.setdefault(
                (normalize_venue_name(venue_name), game_date), []
            ).append(row)

    ballparks_by_venue: dict[str, list[object]] = {}
    for row in ballpark.rows:
        venue_name = _identity_text(getattr(row, "venue_name", None))
        if venue_name is not None:
            ballparks_by_venue.setdefault(normalize_venue_name(venue_name), []).append(
                row
            )

    matched_weather_ids: set[int] = set()
    matched_ballpark_ids: set[int] = set()
    games_missing_weather = 0
    games_missing_ballpark = 0
    duplicate_weather_matches = 0
    duplicate_ballpark_matches = sum(
        max(0, len(rows) - 1) for rows in ballparks_by_venue.values()
    )
    unmatched_venue_names: set[str] = set()
    ballpark_normalization_mismatches = 0

    for game in retrosheet.games:
        game_id = _identity_text(getattr(game, "game_id", None))
        game_date = getattr(game, "game_date", None)
        venue_name = _identity_text(getattr(game, "venue_name", None))

        weather_candidates = (
            weather_by_game.get((game_id, game_date), [])
            if game_id is not None and game_date is not None
            else []
        )
        if not weather_candidates and venue_name is not None and game_date is not None:
            weather_candidates = weather_by_venue.get(
                (normalize_venue_name(venue_name), game_date), []
            )
        if len(weather_candidates) == 1:
            matched_weather_ids.add(id(weather_candidates[0]))
        else:
            games_missing_weather += 1
            duplicate_weather_matches += max(0, len(weather_candidates) - 1)

        if venue_name is None:
            games_missing_ballpark += 1
            continue
        ballpark_candidates = ballparks_by_venue.get(
            normalize_venue_name(venue_name), []
        )
        if len(ballpark_candidates) == 1:
            matched_ballpark_ids.add(id(ballpark_candidates[0]))
            matched_name = _identity_text(
                getattr(ballpark_candidates[0], "venue_name", None)
            )
            if matched_name != venue_name:
                ballpark_normalization_mismatches += 1
        else:
            games_missing_ballpark += 1
            unmatched_venue_names.add(venue_name)

    metrics: dict[str, object] = {
        "unmatched_weather_rows": sum(
            id(row) not in matched_weather_ids for row in weather.rows
        ),
        "games_missing_weather": games_missing_weather,
        "games_missing_ballpark": games_missing_ballpark,
        "unmatched_venue_names": len(unmatched_venue_names),
        "duplicate_weather_matches": duplicate_weather_matches,
        "duplicate_ballpark_matches": duplicate_ballpark_matches,
        "weather_date_mismatch": sum(
            bool(
                (game_id := _identity_text(getattr(row, "game_id", None)))
                and game_id in game_dates_by_id
                and getattr(row, "game_date", None) not in game_dates_by_id[game_id]
            )
            for row in weather.rows
        ),
        "ballpark_venue_normalization_mismatch": ballpark_normalization_mismatches,
        "rows_with_labels_but_missing_weather": sum(
            row.label_available and row.weather_source_type is None
            for row in dataset.rows
        ),
        "rows_with_labels_but_missing_ballpark": sum(
            row.label_available and row.ballpark_source_type is None
            for row in dataset.rows
        ),
        "weather_attached_rows": sum(
            row.weather_source_type is not None for row in dataset.rows
        ),
        "ballpark_attached_rows": sum(
            row.ballpark_source_type is not None for row in dataset.rows
        ),
        "rows_with_full_context": sum(
            row.weather_source_type is not None
            and row.ballpark_source_type is not None
            for row in dataset.rows
        ),
    }
    warning_labels = {
        "unmatched_weather_rows": "unmatched weather rows",
        "games_missing_weather": "games missing weather",
        "games_missing_ballpark": "games missing ballpark",
        "unmatched_venue_names": "unmatched venue names",
        "duplicate_weather_matches": "duplicate weather matches",
        "duplicate_ballpark_matches": "duplicate ballpark matches",
        "weather_date_mismatch": "weather date mismatch",
        "ballpark_venue_normalization_mismatch": (
            "ballpark venue normalization mismatch"
        ),
        "rows_with_labels_but_missing_weather": (
            "rows with labels but missing weather"
        ),
        "rows_with_labels_but_missing_ballpark": (
            "rows with labels but missing ballpark"
        ),
    }
    metrics["warnings"] = tuple(
        f"{label}: {metrics[key]}"
        for key, label in warning_labels.items()
        if metrics[key]
    )
    return metrics


CONTEXT_QUALITY_KEYS: Final = (
    "unmatched_weather_rows",
    "games_missing_weather",
    "games_missing_ballpark",
    "unmatched_venue_names",
    "duplicate_weather_matches",
    "duplicate_ballpark_matches",
    "weather_date_mismatch",
    "ballpark_venue_normalization_mismatch",
    "rows_with_labels_but_missing_weather",
    "rows_with_labels_but_missing_ballpark",
    "rows_with_full_context",
)

ODDS_QUALITY_KEYS: Final = (
    "unmatched_odds_rows",
    "rows_missing_odds",
    "duplicate_odds_matches",
    "stale_odds",
    "invalid_odds_format",
    "missing_player_id_in_odds",
    "missing_game_id_in_odds",
    "market_type_mismatch",
    "team_opponent_mismatch",
    "odds_timestamp_after_event_start_time",
)


def _odds_pairing_quality(
    *,
    retrosheet: object,
    weather: object,
    ballpark: object,
    odds: object,
    dataset: object,
) -> dict[str, object]:
    metrics = _context_pairing_quality(
        retrosheet=retrosheet,
        weather=weather,
        ballpark=ballpark,
        dataset=dataset,
    )
    metrics.update(dict(dataset.odds_pairing_summary))
    metrics["invalid_odds_format"] = odds.rejected_row_count
    metrics["full_context_plus_odds_rows"] = sum(
        row.weather_source_type is not None
        and row.ballpark_source_type is not None
        and row.american_odds is not None
        for row in dataset.rows
    )
    warning_labels = {
        "unmatched_odds_rows": "unmatched odds rows",
        "rows_missing_odds": "rows missing odds",
        "duplicate_odds_matches": "duplicate odds matches",
        "stale_odds": "stale odds",
        "invalid_odds_format": "invalid odds format",
        "missing_player_id_in_odds": "missing player id in odds",
        "missing_game_id_in_odds": "missing game id in odds",
        "market_type_mismatch": "market type mismatch",
        "team_opponent_mismatch": "team opponent mismatch",
        "odds_timestamp_after_event_start_time": (
            "odds timestamp after event start time"
        ),
    }
    context_warnings = tuple(metrics.get("warnings", ()))
    metrics["warnings"] = (
        *context_warnings,
        *(
            f"{label}: {metrics[key]}"
            for key, label in warning_labels.items()
            if metrics[key]
        ),
        *odds.warnings,
    )
    return metrics


def _build_summary_text(
    *,
    mode: str,
    generated_at: datetime,
    statcast_count: int,
    retrosheet_game_count: int,
    retrosheet_event_count: int,
    weather_count: int,
    ballpark_count: int,
    odds_count: int,
    dataset: object,
    audit: object,
    readiness: object,
    pack_paths: Mapping[str, Path],
    pairing_quality: Mapping[str, object] | None = None,
) -> str:
    lines = [
        "CourtVision MLB HR local dataset reproducibility pack",
        "historical research only",
        "not production approved",
    ]
    if mode == "label_pairing_trial":
        lines.extend(
            (
                "local label pairing trial",
                "partial context",
                "leakage audit summary",
                "default-deny",
            )
        )
    elif mode == "context_pairing_trial":
        lines.extend(
            (
                "local context pairing trial",
                "local files only",
                "leakage audit summary",
                "default-deny",
            )
        )
    elif mode == "odds_pairing_trial":
        lines.extend(
            (
                "local odds snapshot trial",
                "market reference only",
                "local files only",
                "leakage audit summary",
                "default-deny",
            )
        )
    lines.extend(
        (
            f"mode = {mode}",
            f"generated_at = {generated_at.isoformat()}",
            f"statcast row count = {statcast_count}",
            f"retrosheet game count = {retrosheet_game_count}",
            f"retrosheet event count = {retrosheet_event_count}",
            f"weather row count = {weather_count}",
            f"ballpark row count = {ballpark_count}",
            f"odds snapshot row count = {odds_count}",
            f"HR batter-game dataset row count = {dataset.row_count}",
            f"HR-positive row count = {sum(row.hit_hr_today is True for row in dataset.rows)}",
            f"HR-negative row count = {sum(row.hit_hr_today is False for row in dataset.rows)}",
            f"label_available row count = {sum(row.label_available is True for row in dataset.rows)}",
            f"game_completed row count = {sum(row.game_completed is True for row in dataset.rows)}",
            *(
                (
                    "weather-attached row count = "
                    f"{pairing_quality['weather_attached_rows']}",
                    "ballpark-attached row count = "
                    f"{pairing_quality['ballpark_attached_rows']}",
                    "full-context row count = "
                    f"{pairing_quality['rows_with_full_context']}",
                )
                if mode == "context_pairing_trial" and pairing_quality is not None
                else ()
            ),
            *(
                (
                    "weather-attached row count = "
                    f"{pairing_quality['weather_attached_rows']}",
                    "ballpark-attached row count = "
                    f"{pairing_quality['ballpark_attached_rows']}",
                    "odds-attached row count = "
                    f"{pairing_quality['odds_attached_rows']}",
                    "full-context-plus-odds row count = "
                    f"{pairing_quality['full_context_plus_odds_rows']}",
                    "unmatched odds row count = "
                    f"{pairing_quality['unmatched_odds_rows']}",
                    "rows missing odds count = "
                    f"{pairing_quality['rows_missing_odds']}",
                )
                if mode == "odds_pairing_trial" and pairing_quality is not None
                else ()
            ),
            f"training eligible row count = {dataset.eligible_for_training_count}",
            f"backtest eligible row count = {dataset.eligible_for_backtest_count}",
            f"audit errors = {audit.error_count}",
            f"audit warnings = {audit.warning_count}",
            f"audit passed = {str(audit.passed).lower()}",
            f"readiness_status = {readiness.readiness_status}",
            f"readiness_score = {readiness.readiness_score}",
            f"blocking_issue_count = {readiness.blocking_issue_count}",
            f"warning_issue_count = {readiness.warning_issue_count}",
            f"readiness output path = {pack_paths['readiness.json']}",
            f"readiness summary path = {pack_paths['readiness_summary.txt']}",
            f"dataset output path = {pack_paths['dataset.csv']}",
            f"metadata output path = {pack_paths['metadata.json']}",
            f"audit output path = {pack_paths['audit.json']}",
            f"source manifest output path = {pack_paths['source_manifest.json']}",
            f"approval_status = {audit.approval_status}",
        )
    )
    if mode not in {
        "label_pairing_trial",
        "context_pairing_trial",
        "odds_pairing_trial",
    }:
        lines.extend(
            (
                f"eligible_for_betting = {str(audit.eligible_for_betting).lower()}",
                f"kelly_eligible = {str(audit.kelly_eligible).lower()}",
            )
        )
    if pairing_quality is not None:
        quality_keys = (
            ODDS_QUALITY_KEYS
            if mode in {"odds_pairing_trial", "historical_dry_run"}
            else CONTEXT_QUALITY_KEYS
            if mode == "context_pairing_trial"
            else (
            "unmatched_statcast_games",
            "unmatched_retrosheet_games",
            "unmatched_batters",
            "retrosheet_events_without_matching_batter_game_rows",
            "statcast_rows_without_game_labels",
            "duplicate_batter_game_row_ids",
            "missing_player_ids",
            "missing_game_ids",
            "missing_game_dates",
            )
        )
        for key in quality_keys:
            lines.append(f"{key.replace('_', ' ')} = {pairing_quality[key]}")
    lines.append("")
    return "\n".join(lines)


def _write_build_pack(
    *,
    args: argparse.Namespace,
    pack_paths: Mapping[str, Path],
    mode: str,
    generated_at: datetime,
    paths: Mapping[str, Path | None],
    statcast: object | None,
    retrosheet: object | None,
    weather: object | None,
    ballpark: object | None,
    odds: object | None,
    dataset: object,
    audit: object,
    readiness: object,
    pairing_quality: Mapping[str, object] | None = None,
) -> None:
    output_dir = next(iter(pack_paths.values())).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    write_hr_dataset_csv(
        dataset, pack_paths["dataset.csv"], overwrite=args.overwrite
    )
    write_hr_dataset_metadata_json(
        dataset, pack_paths["metadata.json"], overwrite=args.overwrite
    )
    write_audit_report_json(
        audit, pack_paths["audit.json"], overwrite=args.overwrite
    )
    write_readiness_report_json(
        readiness, pack_paths["readiness.json"], overwrite=args.overwrite
    )
    write_readiness_report_txt(
        readiness, pack_paths["readiness_summary.txt"], overwrite=args.overwrite
    )

    source_manifest = {
        "manifest_version": (
            HISTORICAL_DRY_RUN_VERSION
            if mode == "historical_dry_run"
            else ODDS_PAIRING_TRIAL_VERSION
            if mode == "odds_pairing_trial"
            else CONTEXT_PAIRING_TRIAL_VERSION
            if mode == "context_pairing_trial"
            else LABEL_PAIRING_TRIAL_VERSION
            if mode == "label_pairing_trial"
            else SOURCE_MANIFEST_VERSION
        ),
        "mode": mode,
        "generated_at": generated_at.isoformat(),
        "dataset_schema_version": dataset.metadata.schema_version,
        "dataset_version": LOCAL_DATASET_VERSION,
        "dataset_id": dataset.metadata.dataset_id,
        "dataset_row_count": dataset.row_count,
        "audit": {
            "error_count": audit.error_count,
            "warning_count": audit.warning_count,
            "passed": audit.passed,
        },
        "pairing_quality": dict(pairing_quality) if pairing_quality else None,
        "sources": _source_manifest_entries(
            mode=mode,
            paths=paths,
            statcast=statcast,
            retrosheet=retrosheet,
            weather=weather,
            ballpark=ballpark,
            odds=odds,
            allow_partial=args.allow_partial,
        ),
        "approval_status": audit.approval_status,
        "eligible_for_betting": audit.eligible_for_betting,
        "kelly_eligible": audit.kelly_eligible,
    }
    _write_json(
        pack_paths["source_manifest.json"],
        source_manifest,
        overwrite=args.overwrite,
    )

    statcast_count = len(statcast.rows) if statcast is not None else 0
    retrosheet_game_count = len(retrosheet.games) if retrosheet is not None else 0
    retrosheet_event_count = len(retrosheet.events) if retrosheet is not None else 0
    weather_count = len(weather.rows) if weather is not None else 0
    ballpark_count = len(ballpark.rows) if ballpark is not None else 0
    odds_count = len(odds.rows) if odds is not None else 0
    summary = _build_summary_text(
        mode=mode,
        generated_at=generated_at,
        statcast_count=statcast_count,
        retrosheet_game_count=retrosheet_game_count,
        retrosheet_event_count=retrosheet_event_count,
        weather_count=weather_count,
        ballpark_count=ballpark_count,
        odds_count=odds_count,
        dataset=dataset,
        audit=audit,
        readiness=readiness,
        pack_paths=pack_paths,
        pairing_quality=pairing_quality,
    )
    mode_flag = "w" if args.overwrite else "x"
    with pack_paths["build_summary.txt"].open(
        mode_flag, encoding="utf-8", newline="\n"
    ) as handle:
        handle.write(summary)


def _run(args: argparse.Namespace) -> None:
    mode, paths = _resolve_inputs(args)
    pack_paths = _preflight_outputs(args)
    generated_at = datetime.now(timezone.utc)
    if mode == "statcast_trial":
        _run_statcast_trial(
            args=args,
            paths=paths,
            pack_paths=pack_paths,
            generated_at=generated_at,
        )
        return

    ingestion_collected_at = (
        FIXTURE_SOURCE_COLLECTED_AT if mode == "fixtures" else generated_at
    )

    statcast = (
        ingest_local_statcast_csv(
            paths["statcast_csv"], collected_at=ingestion_collected_at
        )
        if paths["statcast_csv"] is not None
        else None
    )
    retrosheet = (
        ingest_local_retrosheet_csvs(
            games_csv=paths["retrosheet_games_csv"],
            events_csv=paths["retrosheet_events_csv"],
            collected_at=ingestion_collected_at,
        )
        if paths["retrosheet_games_csv"] is not None
        or paths["retrosheet_events_csv"] is not None
        else None
    )
    weather = (
        ingest_local_weather_csv(paths["weather_csv"])
        if paths["weather_csv"] is not None
        else None
    )
    ballpark = (
        ingest_local_ballpark_factors_csv(paths["ballpark_csv"])
        if paths["ballpark_csv"] is not None
        else None
    )
    odds = (
        ingest_local_odds_snapshot_csv(paths["odds_csv"])
        if paths["odds_csv"] is not None
        else None
    )

    manifest_ids = {
        key: identifier
        for key, result in (
            ("statcast", statcast),
            ("retrosheet", retrosheet),
            ("weather", weather),
            ("ballpark", ballpark),
            ("odds", odds),
        )
        if result is not None and (identifier := _manifest_id(result)) is not None
    }
    builder_statcast_rows = statcast.rows if statcast is not None else ()
    if mode in {
        "label_pairing_trial",
        "context_pairing_trial",
        "odds_pairing_trial",
        "historical_dry_run",
    }:
        if statcast is None or retrosheet is None:
            raise LocalDatasetCLIError(
                f"{mode.replace('_', ' ')} requires parsed Statcast and Retrosheet inputs"
            )
        # Phase 5B-5D labels are Retrosheet-owned. Same-game Statcast outcomes
        # remain outside the label namespace in these controlled trials.
        builder_statcast_rows = ()

    dataset = build_hr_batter_game_rows_from_sources(
        statcast_rows=builder_statcast_rows,
        retrosheet_game_rows=retrosheet.games if retrosheet is not None else (),
        retrosheet_event_rows=retrosheet.events if retrosheet is not None else (),
        weather_rows=weather.rows if weather is not None else (),
        ballpark_rows=ballpark.rows if ballpark is not None else (),
        odds_rows=odds.rows if odds is not None else (),
        source_manifest_ids=manifest_ids,
        generated_at=generated_at,
        dataset_version=LOCAL_DATASET_VERSION,
        generated_by=LOCAL_GENERATED_BY,
    )
    audit = audit_hr_batter_game_rows(dataset.rows, checked_at=generated_at)
    pairing_quality = (
        _label_pairing_quality(
            statcast=statcast,
            retrosheet=retrosheet,
            dataset=dataset,
        )
        if mode == "label_pairing_trial"
        else _context_pairing_quality(
            retrosheet=retrosheet,
            weather=weather,
            ballpark=ballpark,
            dataset=dataset,
        )
        if mode == "context_pairing_trial"
        else _odds_pairing_quality(
            retrosheet=retrosheet,
            weather=weather,
            ballpark=ballpark,
            odds=odds,
            dataset=dataset,
        )
        if mode in {"odds_pairing_trial", "historical_dry_run"}
        else None
    )
    readiness_source_manifest = {
        "sources": _source_manifest_entries(
            mode=mode,
            paths=paths,
            statcast=statcast,
            retrosheet=retrosheet,
            weather=weather,
            ballpark=ballpark,
            odds=odds,
            allow_partial=args.allow_partial,
        )
    }
    readiness = build_hr_dataset_readiness_report(
        dataset.rows,
        metadata=dataset.metadata,
        audit_report=audit,
        source_manifest=readiness_source_manifest,
        pairing_summary=pairing_quality,
    )

    if args.output_csv is not None:
        write_hr_dataset_csv(dataset, args.output_csv, overwrite=args.overwrite)
    if args.audit_json is not None:
        write_audit_report_json(audit, args.audit_json, overwrite=args.overwrite)
    if args.metadata_json is not None:
        write_hr_dataset_metadata_json(
            dataset, args.metadata_json, overwrite=args.overwrite
        )
    if args.readiness_report_json is not None:
        write_readiness_report_json(
            readiness,
            args.readiness_report_json,
            overwrite=args.overwrite,
        )
    if args.readiness_report_txt is not None:
        write_readiness_report_txt(
            readiness,
            args.readiness_report_txt,
            overwrite=args.overwrite,
        )
    if pack_paths:
        _write_build_pack(
            args=args,
            pack_paths=pack_paths,
            mode=mode,
            generated_at=generated_at,
            paths=paths,
            statcast=statcast,
            retrosheet=retrosheet,
            weather=weather,
            ballpark=ballpark,
            odds=odds,
            dataset=dataset,
            audit=audit,
            readiness=readiness,
            pairing_quality=pairing_quality,
        )

    required_source_names = (
        INPUT_ARGUMENTS if mode == "odds_pairing_trial" else BASE_INPUT_ARGUMENTS
    )
    missing_sources = [
        INPUT_LABELS[name] for name in required_source_names if paths[name] is None
    ]
    if mode == "label_pairing_trial":
        print("CourtVision MLB HR local label pairing trial")
        print(
            "historical research only | local label pairing trial | partial context | "
            "leakage audit summary | default-deny | not production approved"
        )
    elif mode == "context_pairing_trial":
        print("CourtVision MLB HR local context pairing trial")
        print(
            "historical research only | local context pairing trial | "
            "local files only | leakage audit summary | default-deny | "
            "not production approved"
        )
    elif mode == "odds_pairing_trial":
        print("CourtVision MLB HR local odds snapshot pairing trial")
        print(
            "historical research only | local odds snapshot trial | "
            "market reference only | local files only | leakage audit summary | "
            "default-deny | not production approved"
        )
    elif mode == "historical_dry_run":
        print("CourtVision MLB HR historical CSV dry run")
        print(
            "historical research only | dry run | local files only | "
            "market reference only for odds | readiness report | "
            "default-deny | not production approved"
        )
    else:
        print("CourtVision MLB HR local-file dataset build")
        print(
            "historical research only | local-file build | default-deny | "
            "not production approved"
        )
    print(f"mode: {mode}")
    print(f"statcast rows: {len(statcast.rows) if statcast is not None else 0}")
    print(
        f"retrosheet games: {len(retrosheet.games) if retrosheet is not None else 0}"
    )
    print(
        f"retrosheet events: {len(retrosheet.events) if retrosheet is not None else 0}"
    )
    print(f"weather rows: {len(weather.rows) if weather is not None else 0}")
    print(f"ballpark rows: {len(ballpark.rows) if ballpark is not None else 0}")
    print(f"odds snapshot rows: {len(odds.rows) if odds is not None else 0}")
    print(f"HR batter-game rows: {dataset.row_count}")
    print(
        "HR-positive rows: "
        f"{sum(row.hit_hr_today is True for row in dataset.rows)}"
    )
    print(
        "HR-negative rows: "
        f"{sum(row.hit_hr_today is False for row in dataset.rows)}"
    )
    print(
        "label_available rows: "
        f"{sum(row.label_available is True for row in dataset.rows)}"
    )
    print(
        "game_completed rows: "
        f"{sum(row.game_completed is True for row in dataset.rows)}"
    )
    if mode == "context_pairing_trial" and pairing_quality is not None:
        print(f"weather-attached rows: {pairing_quality['weather_attached_rows']}")
        print(f"ballpark-attached rows: {pairing_quality['ballpark_attached_rows']}")
        print(f"full-context rows: {pairing_quality['rows_with_full_context']}")
    if mode in {"odds_pairing_trial", "historical_dry_run"} and pairing_quality is not None:
        print(f"weather-attached rows: {pairing_quality['weather_attached_rows']}")
        print(f"ballpark-attached rows: {pairing_quality['ballpark_attached_rows']}")
        print(f"odds-attached rows: {pairing_quality['odds_attached_rows']}")
        print(
            "full-context-plus-odds rows: "
            f"{pairing_quality['full_context_plus_odds_rows']}"
        )
        print(f"unmatched odds rows: {pairing_quality['unmatched_odds_rows']}")
        print(f"rows missing odds: {pairing_quality['rows_missing_odds']}")
    print(f"training eligible rows: {dataset.eligible_for_training_count}")
    print(f"backtest eligible rows: {dataset.eligible_for_backtest_count}")
    print(f"audit errors: {audit.error_count}")
    print(f"audit warnings: {audit.warning_count}")
    print(f"audit passed: {str(audit.passed).lower()}")
    print(f"approval_status: {audit.approval_status}")
    print(f"readiness_status: {readiness.readiness_status}")
    print(f"readiness_score: {readiness.readiness_score}")
    print(f"blocking_issue_count: {readiness.blocking_issue_count}")
    print(f"warning_issue_count: {readiness.warning_issue_count}")
    print(f"dataset_row_count: {readiness.dataset_row_count}")
    print(f"label_available_count: {readiness.label_available_count}")
    print(f"full_context_count: {readiness.full_context_count}")
    print(f"odds_attached_count: {readiness.odds_attached_count}")
    print(f"leakage_error_count: {readiness.leakage_error_count}")
    print(f"leakage_warning_count: {readiness.leakage_warning_count}")
    if pairing_quality is not None:
        quality_keys = (
            ODDS_QUALITY_KEYS
            if mode in {"odds_pairing_trial", "historical_dry_run"}
            else CONTEXT_QUALITY_KEYS
            if mode == "context_pairing_trial"
            else (
            "unmatched_statcast_games",
            "unmatched_retrosheet_games",
            "unmatched_batters",
            "retrosheet_events_without_matching_batter_game_rows",
            "statcast_rows_without_game_labels",
            "duplicate_batter_game_row_ids",
            "missing_player_ids",
            "missing_game_ids",
            "missing_game_dates",
            )
        )
        for key in quality_keys:
            print(f"{key.replace('_', ' ')}: {pairing_quality[key]}")
        for warning in pairing_quality["warnings"]:
            print(f"warning: {warning}")
        missing_weather = dataset.missing_context_summary["weather"]
        missing_ballpark = dataset.missing_context_summary["ballpark"]
        if missing_weather:
            print(
                f"warning: weather context missing for {missing_weather} rows; "
                "values were not fabricated"
            )
        if missing_ballpark:
            print(
                f"warning: ballpark context missing for {missing_ballpark} rows; "
                "values were not fabricated"
            )
    print(f"source warnings: {len(missing_sources)}")
    for label in missing_sources:
        print(f"warning: missing source {label}; context was not fabricated")
    print("first 5 dataset rows:")
    for row in dataset.rows[:5]:
        displayed = {
            field_name: _display_value(getattr(row, field_name))
            for field_name in DISPLAY_FIELDS
        }
        print(json.dumps(displayed, ensure_ascii=False))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the local-only build and return a process-style status code."""

    parser = _parser()
    try:
        args = parser.parse_args(argv)
        _run(args)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
