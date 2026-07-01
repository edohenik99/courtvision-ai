from __future__ import annotations

import csv
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from courtvision.cli.main import main as cli_main
from courtvision.data_collection.core import CollectionRequest, collect_sources
from courtvision.data_collection.path_guards import ProtectedPathError
from courtvision.sports.mlb.data_collection.ballpark_factor_collector import (
    BALLPARK_FACTOR_SCHEMA_VERSION,
    NORMALIZED_BALLPARK_FACTORS_FILENAME,
    VALIDATION_REPORT_FILENAME,
    BallparkFactorCollectionError,
)


COLLECTED_AT = datetime(2026, 6, 30, 16, 0, tzinfo=timezone.utc)
HEADER = "season,park_id,stadium_name,handedness,hr_factor,run_factor\n"


def _stadium_map(tmp_path: Path) -> Path:
    path = tmp_path / "stadiums.csv"
    path.write_text(
        "park_id,stadium_name,latitude,longitude,timezone,elevation_m\n"
        "NYC21,Yankee Stadium,40.8296,-73.9262,America/New_York,17\n"
        "BOS07,Fenway Park,42.3467,-71.0972,America/New_York,6\n",
        encoding="utf-8",
    )
    return path


def _ballpark_csv(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "official_ballpark_factors.csv"
    path.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def _valid_ballpark_csv(tmp_path: Path) -> Path:
    return _ballpark_csv(
        tmp_path,
        "2025,NYC21,Yankee Stadium,RHB,1.12,1.05",
        "2025,NYC21,Yankee Stadium,LHB,1.17,1.08",
        "2025,BOS07,Fenway Park,ALL,1.04,1.09",
    )


def _request(
    tmp_path: Path,
    ballpark_path: Path,
    stadium_map_path: Path,
    *,
    dry_run: bool = False,
    output_raw_dir: Path | None = None,
) -> CollectionRequest:
    odds = tmp_path / "odds.jsonl"
    odds.write_text('{"game": 1}\n', encoding="utf-8")
    return CollectionRequest(
        sport="mlb",
        season=2025,
        start_date=date(2025, 3, 27),
        end_date=date(2025, 9, 28),
        output_raw_dir=output_raw_dir or tmp_path / "raw",
        dry_run=dry_run,
        collection_id="v2025-ballpark-test",
        collection_timestamp=COLLECTED_AT,
        source_options={
            "ballpark_factors_path": ballpark_path,
            "stadium_map_path": stadium_map_path,
            "odds_archive_path": odds,
            "odds_provider": "licensed-test-archive",
        },
    )


def test_valid_csv_writes_normalized_factors_and_validation_report(
    tmp_path: Path,
) -> None:
    source = _valid_ballpark_csv(tmp_path)
    result = collect_sources(_request(tmp_path, source, _stadium_map(tmp_path)))
    source_dir = result.collection_dir / "sources" / "approved_supplied_ballpark_factors"
    normalized = source_dir / NORMALIZED_BALLPARK_FACTORS_FILENAME
    report_path = source_dir / VALIDATION_REPORT_FILENAME

    assert normalized.is_file()
    assert report_path.is_file()
    with normalized.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [(row["park_id"], row["handedness"]) for row in rows] == [
        ("BOS07", "ALL"),
        ("NYC21", "LHB"),
        ("NYC21", "RHB"),
    ]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "valid"
    assert report["schema_version"] == BALLPARK_FACTOR_SCHEMA_VERSION
    assert report["source_row_count"] == 3
    assert report["provenance"]["network_accessed"] is False
    assert report["provenance"]["scraping_performed"] is False


def test_duplicate_park_season_handedness_rows_are_rejected(tmp_path: Path) -> None:
    source = _ballpark_csv(
        tmp_path,
        "2025,NYC21,Yankee Stadium,RHB,1.12,1.05",
        "2025,nyc21,Yankee Stadium,rhb,1.10,1.03",
    )

    with pytest.raises(BallparkFactorCollectionError, match="duplicate park/season/handedness"):
        collect_sources(_request(tmp_path, source, _stadium_map(tmp_path)))
    assert not (tmp_path / "raw").exists()


def test_missing_park_ids_are_rejected(tmp_path: Path) -> None:
    source = _ballpark_csv(tmp_path, "2025,,Unknown Park,RHB,1.12,1.05")

    with pytest.raises(BallparkFactorCollectionError, match="park_id must not be blank"):
        collect_sources(_request(tmp_path, source, _stadium_map(tmp_path)))


def test_unknown_park_ids_are_rejected(tmp_path: Path) -> None:
    source = _ballpark_csv(tmp_path, "2025,UNK01,Unknown Park,RHB,1.12,1.05")

    with pytest.raises(BallparkFactorCollectionError, match="unknown park_id 'UNK01'"):
        collect_sources(_request(tmp_path, source, _stadium_map(tmp_path)))


@pytest.mark.parametrize("value", ("not-a-number", "NaN", "0.49", "1.51"))
def test_bad_numeric_values_are_rejected(tmp_path: Path, value: str) -> None:
    source = _ballpark_csv(
        tmp_path, f"2025,NYC21,Yankee Stadium,RHB,{value},1.05"
    )

    with pytest.raises(BallparkFactorCollectionError, match="hr_factor"):
        collect_sources(_request(tmp_path, source, _stadium_map(tmp_path)))


def test_season_mismatch_is_rejected(tmp_path: Path) -> None:
    source = _ballpark_csv(
        tmp_path, "2024,NYC21,Yankee Stadium,RHB,1.12,1.05"
    )

    with pytest.raises(BallparkFactorCollectionError, match="does not match requested"):
        collect_sources(_request(tmp_path, source, _stadium_map(tmp_path)))


def test_unsupported_season_is_rejected_before_collection(tmp_path: Path) -> None:
    source = _ballpark_csv(
        tmp_path, "1800,NYC21,Yankee Stadium,RHB,1.12,1.05"
    )

    with pytest.raises(BallparkFactorCollectionError, match="unsupported season 1800"):
        collect_sources(_request(tmp_path, source, _stadium_map(tmp_path)))


def test_manifest_records_artifact_and_source_provenance(tmp_path: Path) -> None:
    source = _valid_ballpark_csv(tmp_path)
    result = collect_sources(_request(tmp_path, source, _stadium_map(tmp_path)))

    assert result.manifest is not None
    assert result.manifest.collector_version == "1.4.0"
    records = [
        item
        for item in result.manifest.sources
        if item.source_name == "approved_supplied_ballpark_factors"
    ]
    assert {Path(item.local_file_path).name for item in records} == {
        NORMALIZED_BALLPARK_FACTORS_FILENAME,
        VALIDATION_REPORT_FILENAME,
    }
    normalized_record = next(
        item
        for item in records
        if Path(item.local_file_path).name == NORMALIZED_BALLPARK_FACTORS_FILENAME
    )
    normalized = result.collection_dir / normalized_record.local_file_path
    metadata = normalized_record.metadata
    assert normalized_record.row_count == metadata["normalized_row_count"] == 3
    assert normalized_record.sha256 == hashlib.sha256(normalized.read_bytes()).hexdigest()
    assert metadata["schema_version"] == BALLPARK_FACTOR_SCHEMA_VERSION
    assert metadata["source_filename"] == source.name
    assert metadata["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert metadata["source_row_count"] == 3
    assert metadata["validation_report_sha256"]
    assert metadata["provenance"]["acquisition_method"] == "approved_supplied_csv"


def test_cli_dry_run_validates_without_writing(tmp_path: Path) -> None:
    source = _valid_ballpark_csv(tmp_path)
    stadium_map = _stadium_map(tmp_path)
    output = tmp_path / "raw"

    assert cli_main(
        [
            "collect",
            "mlb",
            "--season",
            "2025",
            "--ballpark-factors-path",
            str(source),
            "--stadium-map-path",
            str(stadium_map),
            "--output-raw-dir",
            str(output),
            "--dry-run",
        ]
    ) == 0
    assert not output.exists()


def test_protected_output_folder_is_rejected(tmp_path: Path) -> None:
    source = _valid_ballpark_csv(tmp_path)

    with pytest.raises(ProtectedPathError):
        collect_sources(
            _request(
                tmp_path,
                source,
                _stadium_map(tmp_path),
                output_raw_dir=tmp_path / "outputs",
            )
        )


def test_required_columns_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "missing-column.csv"
    source.write_text(
        "season,park_id,stadium_name,handedness,hr_factor\n"
        "2025,NYC21,Yankee Stadium,RHB,1.12\n",
        encoding="utf-8",
    )

    with pytest.raises(BallparkFactorCollectionError, match="run_factor"):
        collect_sources(_request(tmp_path, source, _stadium_map(tmp_path)))
