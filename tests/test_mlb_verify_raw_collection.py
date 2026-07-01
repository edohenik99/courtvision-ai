from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.mlb_verify_raw_collection import (
    EXPECTED_BLOCKER,
    EXPECTED_SOURCES,
    verify_collection,
)


SCRIPT_PATH = PROJECT_ROOT / "scripts" / "mlb_verify_raw_collection.py"


def _source_record(
    collection_dir: Path,
    source_name: str,
    filename: str,
    content: bytes,
    *,
    row_count: int | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    relative = Path("sources") / source_name / filename
    path = collection_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "source_name": source_name,
        "local_file_path": relative.as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "row_count": row_count,
        "blockers": [],
        "warnings": [],
        "metadata": metadata or {},
    }


@pytest.fixture
def valid_five_source_collection(tmp_path: Path) -> Path:
    collection_dir = tmp_path / "v2025-five-source-fixture"
    records = [
        _source_record(
            collection_dir,
            "statcast_pybaseball",
            "statcast.csv",
            b"game_pk,pitch_type\n1,FF\n",
            row_count=1,
        ),
        _source_record(
            collection_dir,
            "retrosheet_official",
            "gl2025.txt",
            b'"20250401","0","Tue"\n',
            row_count=None,
        ),
        _source_record(
            collection_dir,
            "chadwick_bureau_register",
            "register.zip",
            b"fixture archive",
            row_count=1,
        ),
        _source_record(
            collection_dir,
            "approved_stadium_coordinates",
            "stadiums.csv",
            b"park_id,latitude,longitude\nAAA01,1,2\n",
            row_count=1,
        ),
        _source_record(
            collection_dir,
            "weather_meteostat",
            "weather.csv",
            b"game_id,temp\n1,20\n",
            row_count=1,
            metadata={
                "weather_summary": {
                    "games_processed": 2430,
                    "missing_weather": 0,
                }
            },
        ),
        _source_record(
            collection_dir,
            "approved_supplied_ballpark_factors",
            "normalized_ballpark_factors.csv",
            b"park_id,hr_factor\nAAA01,1.0\n",
            row_count=1,
        ),
    ]
    manifest = {
        "collection_id": collection_dir.name,
        "sources": records,
        "warnings": ["fixture warning"],
        "blockers": [EXPECTED_BLOCKER],
    }
    (collection_dir / "collection_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return collection_dir


def _manifest(collection_dir: Path) -> dict[str, Any]:
    return json.loads(
        (collection_dir / "collection_manifest.json").read_text(encoding="utf-8")
    )


def _write_manifest(collection_dir: Path, manifest: dict[str, Any]) -> None:
    (collection_dir / "collection_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def _tree_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_valid_five_source_collection_passes(
    valid_five_source_collection: Path,
) -> None:
    report = verify_collection(valid_five_source_collection)

    assert report == {
        "verdict": "PASS",
        "collection_id": "v2025-five-source-fixture",
        "sources_present": list(EXPECTED_SOURCES),
        "row_counts": {
            "retrosheet_games": 1,
            "statcast_rows": 1,
            "weather_games_processed": 2430,
            "weather_missing_weather": 0,
            "ballpark_factor_rows": 1,
        },
        "warnings_count": 1,
        "blockers": [EXPECTED_BLOCKER],
        "hash_failures": [],
        "missing_files": [],
    }


def test_missing_manifest_fails(tmp_path: Path) -> None:
    report = verify_collection(tmp_path)

    assert report["verdict"] == "FAIL"
    assert report["missing_files"] == ["collection_manifest.json"]


def test_invalid_manifest_fails(tmp_path: Path) -> None:
    (tmp_path / "collection_manifest.json").write_text("{invalid", encoding="utf-8")

    report = verify_collection(tmp_path)

    assert report["verdict"] == "FAIL"
    assert report["blockers"][0].startswith("Invalid collection manifest:")


def test_missing_source_fails(valid_five_source_collection: Path) -> None:
    manifest = _manifest(valid_five_source_collection)
    manifest["sources"] = [
        record
        for record in manifest["sources"]
        if record["source_name"] != "chadwick_bureau_register"
    ]
    _write_manifest(valid_five_source_collection, manifest)

    report = verify_collection(valid_five_source_collection)

    assert report["verdict"] == "FAIL"
    assert "chadwick_bureau_register" not in report["sources_present"]


def test_hash_mismatch_fails(valid_five_source_collection: Path) -> None:
    manifest = _manifest(valid_five_source_collection)
    statcast = next(
        record
        for record in manifest["sources"]
        if record["source_name"] == "statcast_pybaseball"
    )
    path = valid_five_source_collection / statcast["local_file_path"]
    path.write_bytes(path.read_bytes() + b"2,SL\n")

    report = verify_collection(valid_five_source_collection)

    assert report["verdict"] == "FAIL"
    assert report["hash_failures"] == [statcast["local_file_path"]]


def test_missing_manifest_hashed_file_fails(
    valid_five_source_collection: Path,
) -> None:
    manifest = _manifest(valid_five_source_collection)
    chadwick = next(
        record
        for record in manifest["sources"]
        if record["source_name"] == "chadwick_bureau_register"
    )
    (valid_five_source_collection / chadwick["local_file_path"]).unlink()

    report = verify_collection(valid_five_source_collection)

    assert report["verdict"] == "FAIL"
    assert report["missing_files"] == [chadwick["local_file_path"]]


def test_unexpected_blocker_fails(valid_five_source_collection: Path) -> None:
    manifest = _manifest(valid_five_source_collection)
    manifest["blockers"].append("Unexpected fixture blocker.")
    _write_manifest(valid_five_source_collection, manifest)

    report = verify_collection(valid_five_source_collection)

    assert report["verdict"] == "FAIL"
    assert report["blockers"] == [EXPECTED_BLOCKER, "Unexpected fixture blocker."]


def test_weather_missing_greater_than_zero_fails(
    valid_five_source_collection: Path,
) -> None:
    manifest = _manifest(valid_five_source_collection)
    weather = next(
        record
        for record in manifest["sources"]
        if record["source_name"] == "weather_meteostat"
    )
    weather["metadata"]["weather_summary"]["missing_weather"] = 1
    _write_manifest(valid_five_source_collection, manifest)

    report = verify_collection(valid_five_source_collection)

    assert report["verdict"] == "FAIL"
    assert report["row_counts"]["weather_missing_weather"] == 1


def test_cli_is_read_only(valid_five_source_collection: Path) -> None:
    before = _tree_snapshot(valid_five_source_collection)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--collection-dir",
            str(valid_five_source_collection),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["verdict"] == "PASS"
    assert _tree_snapshot(valid_five_source_collection) == before
