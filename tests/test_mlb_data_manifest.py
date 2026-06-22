from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from courtvision.sports.mlb.data_manifest import (
    MLB_MANIFEST_SCHEMA_VERSION,
    MLBDataDomain,
    MLBSourceFileRecord,
    MLBSourceManifest,
    MLBSourceType,
    compute_file_sha256,
    ensure_mlb_storage_dirs,
    get_mlb_storage_layout,
    manifest_path_for,
    manifest_to_dict,
    manifest_to_json,
    validate_source_manifest,
    write_manifest,
)


COLLECTED_AT = datetime(2026, 6, 19, 16, 30, tzinfo=timezone.utc)


def _manifest(**overrides: object) -> MLBSourceManifest:
    values: dict[str, object] = {
        "source_name": "sample-statcast",
        "source_type": MLBSourceType.SAMPLE,
        "data_domain": MLBDataDomain.STATCAST,
        "season": 2025,
        "date_range_start": date(2025, 3, 27),
        "date_range_end": date(2025, 9, 28),
        "collected_at": COLLECTED_AT,
        "as_of_date": date(2026, 6, 19),
        "provider_name": "pytest-sample",
        "raw_path": "data/raw/mlb/statcast/sample/2025",
        "normalized_path": "data/normalized/mlb/statcast/2025",
        "schema_version": MLB_MANIFEST_SCHEMA_VERSION,
        "source_version": "sample-v1",
        "checksum": "a" * 64,
        "row_count": 2,
        "file_count": 1,
        "generated_by": "pytest",
        "notes": ("Synthetic contract fixture; no source data exists.",),
        "warnings": ("Sample data only.",),
        "files": (
            MLBSourceFileRecord(
                path="sample.json",
                checksum="b" * 64,
                row_count=2,
                byte_size=32,
            ),
        ),
    }
    values.update(overrides)
    return MLBSourceManifest(**values)  # type: ignore[arg-type]


def test_storage_layout_contains_every_required_mlb_path(tmp_path: Path) -> None:
    layout = get_mlb_storage_layout(tmp_path)
    expected = {
        tmp_path / "data/raw/mlb/statcast",
        tmp_path / "data/raw/mlb/retrosheet",
        tmp_path / "data/raw/mlb/lahman",
        tmp_path / "data/raw/mlb/weather",
        tmp_path / "data/raw/mlb/odds",
        tmp_path / "data/raw/mlb/lineups",
        tmp_path / "data/raw/mlb/probable_pitchers",
        tmp_path / "data/raw/mlb/ballpark",
        tmp_path / "data/normalized/mlb",
        tmp_path / "data/research/mlb/hr",
        tmp_path / "data/training/mlb/hr",
        tmp_path / "data/manifests/mlb",
    }

    assert set(layout.directories) == expected
    assert manifest_path_for(
        "sample-statcast", "statcast", 2025, root=tmp_path
    ) == tmp_path / "data/manifests/mlb/statcast-sample-statcast-2025.manifest.json"


def test_dry_run_plans_directories_without_creating_them(tmp_path: Path) -> None:
    root = tmp_path / "dry-run-root"

    planned = ensure_mlb_storage_dirs(root)

    assert planned == get_mlb_storage_layout(root).directories
    assert not root.exists()


def test_explicit_directory_creation_is_confined_to_pytest_tmp_path(
    tmp_path: Path,
) -> None:
    root = tmp_path / "explicit-root"

    created = ensure_mlb_storage_dirs(root, dry_run=False)

    assert all(path.is_dir() for path in created)
    assert not any(path.is_file() for path in root.rglob("*"))


def test_manifest_is_immutable_valid_and_serializes_deterministically() -> None:
    manifest = _manifest()

    with pytest.raises(FrozenInstanceError):
        manifest.source_name = "changed"  # type: ignore[misc]

    result = validate_source_manifest(manifest)
    first = manifest_to_json(manifest)
    second = manifest_to_json(manifest)
    payload = manifest_to_dict(manifest)

    assert result.is_valid
    assert first == second
    assert json.loads(first) == payload
    assert payload["source_type"] == "sample"
    assert payload["data_domain"] == "statcast"
    assert payload["collected_at"] == "2026-06-19T16:30:00+00:00"
    assert payload["approval_status"] == "not_approved"
    assert payload["eligible_for_betting"] is False
    assert payload["kelly_eligible"] is False


@pytest.mark.parametrize(
    ("overrides", "expected_error"),
    [
        ({"source_name": ""}, "source_name is required"),
        ({"source_type": ""}, "source_type is required"),
        ({"source_type": "unknown"}, "unsupported source_type"),
        ({"data_domain": "unsupported"}, "unsupported data_domain"),
        ({"collected_at": None}, "collected_at must be a datetime"),
        (
            {
                "date_range_start": date(2025, 9, 28),
                "date_range_end": date(2025, 3, 27),
            },
            "date_range_start must not be after date_range_end",
        ),
        ({"sport": "NBA"}, "sport must be 'MLB'"),
        ({"league": "AL"}, "league must be 'MLB'"),
        ({"schema_version": ""}, "schema_version is required"),
        ({"raw_path": None}, "raw_path is required"),
    ],
)
def test_invalid_manifest_fields_fail_closed(
    overrides: dict[str, object], expected_error: str
) -> None:
    result = validate_source_manifest(_manifest(**overrides))

    assert not result.is_valid
    assert any(expected_error in error for error in result.errors)


@pytest.mark.parametrize(
    "overrides",
    [
        {"approval_status": "approved"},
        {"eligible_for_betting": True},
        {"kelly_eligible": True},
        {"notes": ("Approved for production use.",)},
        {"warnings": ("Betting approval granted.",)},
    ],
)
def test_manifest_cannot_claim_unsafe_approval(overrides: dict[str, object]) -> None:
    manifest = _manifest(**overrides)
    result = validate_source_manifest(manifest)

    assert not result.is_valid
    with pytest.raises(ValueError):
        manifest_to_json(manifest)


def test_checksum_helper_hashes_exact_temp_file_bytes(tmp_path: Path) -> None:
    content = b"tiny pytest-only MLB manifest checksum fixture\n"
    path = tmp_path / "fixture.bin"
    path.write_bytes(content)

    assert compute_file_sha256(path) == hashlib.sha256(content).hexdigest()


def test_write_manifest_refuses_overwrite_unless_explicitly_allowed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manifest.json"
    first = _manifest(notes=("first",))
    second = replace(first, notes=("second",))

    assert write_manifest(first, path) == path
    with pytest.raises(FileExistsError):
        write_manifest(second, path)
    assert json.loads(path.read_text(encoding="utf-8"))["notes"] == ["first"]

    assert write_manifest(second, path, overwrite=True) == path
    assert json.loads(path.read_text(encoding="utf-8"))["notes"] == ["second"]


def test_normal_contract_operations_do_not_create_raw_data(tmp_path: Path) -> None:
    root = tmp_path / "no-data-root"

    get_mlb_storage_layout(root)
    ensure_mlb_storage_dirs(root)
    manifest_path_for("sample", MLBDataDomain.RESEARCH, "2026-06-19", root=root)
    manifest_to_json(_manifest())

    assert not root.exists()
