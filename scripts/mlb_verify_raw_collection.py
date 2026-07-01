"""Verify an existing five-source MLB raw collection without modifying it."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence


MANIFEST_FILENAME = "collection_manifest.json"
EXPECTED_SOURCES = (
    "statcast_pybaseball",
    "retrosheet_official",
    "chadwick_bureau_register",
    "approved_stadium_coordinates",
    "weather_meteostat",
    "approved_supplied_ballpark_factors",
)
EXPECTED_BLOCKER = "Missing required approved odds provider/API/archive source."


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _source_row_count(records: list[dict[str, Any]], source_name: str) -> int | None:
    counts = [
        count
        for record in records
        if record.get("source_name") == source_name
        and (count := _integer(record.get("row_count"))) is not None
    ]
    return max(counts) if counts else None


def _safe_manifest_file(
    collection_dir: Path, local_file_path: object
) -> tuple[Path | None, str]:
    label = str(local_file_path) if local_file_path else "<missing local_file_path>"
    if not isinstance(local_file_path, str) or not local_file_path.strip():
        return None, label

    relative = Path(local_file_path)
    if relative.is_absolute():
        return None, relative.as_posix()

    root = collection_dir.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, relative.as_posix()
    return candidate, relative.as_posix()


def _retrosheet_game_count(
    collection_dir: Path, records: list[dict[str, Any]]
) -> int | None:
    record = next(
        (
            item
            for item in records
            if item.get("source_name") == "retrosheet_official"
        ),
        None,
    )
    if record is None:
        return None
    path, _ = _safe_manifest_file(collection_dir, record.get("local_file_path"))
    if path is None or not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return None


def _weather_counts(records: list[dict[str, Any]]) -> tuple[int | None, int | None, bool]:
    summaries: list[dict[str, Any]] = []
    for record in records:
        if record.get("source_name") != "weather_meteostat":
            continue
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            continue
        summary = metadata.get("weather_summary")
        if isinstance(summary, dict):
            summaries.append(summary)

    counts: list[tuple[int, int]] = []
    for summary in summaries:
        games_processed = _integer(summary.get("games_processed"))
        missing_weather = _integer(summary.get("missing_weather"))
        if games_processed is not None and missing_weather is not None:
            counts.append((games_processed, missing_weather))
    if not counts:
        return None, None, False

    games_values = {games for games, _ in counts}
    games_processed = next(iter(games_values)) if len(games_values) == 1 else None
    missing_weather = max(missing for _, missing in counts)
    all_counts_valid = len(counts) == len(summaries) and all(
        games == 2430 and missing == 0 for games, missing in counts
    )
    return games_processed, missing_weather, all_counts_valid


def _ordered_sources_present(source_names: set[str]) -> list[str]:
    expected = [name for name in EXPECTED_SOURCES if name in source_names]
    unexpected = sorted(source_names.difference(EXPECTED_SOURCES))
    return expected + unexpected


def _base_report() -> dict[str, Any]:
    return {
        "verdict": "FAIL",
        "collection_id": None,
        "sources_present": [],
        "row_counts": {
            "retrosheet_games": None,
            "statcast_rows": None,
            "weather_games_processed": None,
            "weather_missing_weather": None,
            "ballpark_factor_rows": None,
        },
        "warnings_count": 0,
        "blockers": [],
        "hash_failures": [],
        "missing_files": [],
    }


def verify_collection(collection_dir: str | Path) -> dict[str, Any]:
    """Return verification evidence for ``collection_dir`` without writing files."""

    root = Path(collection_dir)
    report = _base_report()
    manifest_path = root / MANIFEST_FILENAME
    if not manifest_path.is_file():
        report["missing_files"] = [MANIFEST_FILENAME]
        return report

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        report["blockers"] = [f"Invalid collection manifest: {exc}"]
        return report
    if not isinstance(manifest, dict):
        report["blockers"] = ["Invalid collection manifest: root must be an object."]
        return report

    validation_failures: list[str] = []
    collection_id = manifest.get("collection_id")
    report["collection_id"] = collection_id if isinstance(collection_id, str) else None
    if report["collection_id"] is None:
        validation_failures.append("missing collection_id")

    warnings = manifest.get("warnings", [])
    if isinstance(warnings, list):
        report["warnings_count"] = len(warnings)
    else:
        validation_failures.append("warnings must be a list")

    raw_sources = manifest.get("sources")
    if not isinstance(raw_sources, list):
        raw_sources = []
        validation_failures.append("sources must be a list")
    records = [record for record in raw_sources if isinstance(record, dict)]
    if len(records) != len(raw_sources):
        validation_failures.append("every source record must be an object")

    source_names = {
        name
        for record in records
        if isinstance((name := record.get("source_name")), str)
    }
    report["sources_present"] = _ordered_sources_present(source_names)
    if not set(EXPECTED_SOURCES).issubset(source_names):
        validation_failures.append("one or more expected sources are missing")

    blockers: list[str] = []
    raw_blockers = manifest.get("blockers", [])
    if not isinstance(raw_blockers, list):
        validation_failures.append("blockers must be a list")
        raw_blockers = []
    for blocker in raw_blockers:
        if isinstance(blocker, str) and blocker not in blockers:
            blockers.append(blocker)
        elif not isinstance(blocker, str):
            validation_failures.append("every blocker must be a string")
    for record in records:
        source_blockers = record.get("blockers", [])
        if not isinstance(source_blockers, list):
            validation_failures.append("source blockers must be a list")
            continue
        for blocker in source_blockers:
            if isinstance(blocker, str) and blocker not in blockers:
                blockers.append(blocker)
            elif not isinstance(blocker, str):
                validation_failures.append("every source blocker must be a string")
    report["blockers"] = blockers
    if blockers != [EXPECTED_BLOCKER]:
        validation_failures.append("the approved odds-source blocker is not the sole blocker")

    missing_files: list[str] = []
    hash_failures: list[str] = []
    hash_cache: dict[Path, str] = {}
    for record in records:
        file_path, label = _safe_manifest_file(root, record.get("local_file_path"))
        expected_hash = record.get("sha256")
        if file_path is None or not file_path.is_file():
            if label not in missing_files:
                missing_files.append(label)
            continue
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            if label not in hash_failures:
                hash_failures.append(label)
            continue
        try:
            if file_path not in hash_cache:
                hash_cache[file_path] = _sha256_file(file_path)
            actual_hash = hash_cache[file_path]
        except OSError:
            if label not in hash_failures:
                hash_failures.append(label)
            continue
        if actual_hash.lower() != expected_hash.lower() and label not in hash_failures:
            hash_failures.append(label)
    report["missing_files"] = sorted(missing_files)
    report["hash_failures"] = sorted(hash_failures)

    weather_games, weather_missing, weather_counts_valid = _weather_counts(records)
    row_counts = {
        "retrosheet_games": _retrosheet_game_count(root, records),
        "statcast_rows": _source_row_count(records, "statcast_pybaseball"),
        "weather_games_processed": weather_games,
        "weather_missing_weather": weather_missing,
        "ballpark_factor_rows": _source_row_count(
            records, "approved_supplied_ballpark_factors"
        ),
    }
    report["row_counts"] = row_counts
    if not isinstance(row_counts["retrosheet_games"], int) or row_counts[
        "retrosheet_games"
    ] <= 0:
        validation_failures.append("Retrosheet games must be greater than zero")
    if not isinstance(row_counts["statcast_rows"], int) or row_counts[
        "statcast_rows"
    ] <= 0:
        validation_failures.append("Statcast rows must be greater than zero")
    if row_counts["weather_games_processed"] != 2430:
        validation_failures.append("weather games processed must equal 2430")
    if row_counts["weather_missing_weather"] != 0:
        validation_failures.append("weather missing_weather must equal zero")
    if not weather_counts_valid:
        validation_failures.append("weather summaries must consistently contain expected counts")
    if not isinstance(row_counts["ballpark_factor_rows"], int) or row_counts[
        "ballpark_factor_rows"
    ] <= 0:
        validation_failures.append("ballpark factor rows must be greater than zero")

    if not validation_failures and not missing_files and not hash_failures:
        report["verdict"] = "PASS"
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify an existing five-source MLB raw collection read-only."
    )
    parser.add_argument("--collection-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = verify_collection(args.collection_dir)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
