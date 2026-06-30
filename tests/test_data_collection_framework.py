from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from courtvision.data_collection.core import (
    CollectionRequest,
    UnsupportedSportCollectionError,
    collect_sources,
)
from courtvision.data_collection.path_guards import ProtectedPathError
from courtvision.data_collection.registry import get_collection_adapter
from courtvision.data_collection.source_contracts import (
    AcquisitionMethod,
    SourceContract,
    SourceContractError,
)


COLLECTED_AT = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


def _request(tmp_path: Path, **overrides: object) -> CollectionRequest:
    values: dict[str, object] = {
        "sport": "mlb",
        "season": 2025,
        "start_date": date(2025, 3, 27),
        "end_date": date(2025, 9, 28),
        "output_raw_dir": tmp_path / "raw-collections",
        "collection_id": "v2025-test",
        "collection_timestamp": COLLECTED_AT,
    }
    values.update(overrides)
    return CollectionRequest(**values)  # type: ignore[arg-type]


def test_dry_run_writes_nothing_and_reports_required_blockers(tmp_path: Path) -> None:
    request = _request(tmp_path, dry_run=True)

    result = collect_sources(request)

    assert result.dry_run
    assert not request.output_raw_dir.exists()
    assert any("odds" in blocker.lower() for blocker in result.blockers)
    assert any("ballpark" in blocker.lower() for blocker in result.blockers)


@pytest.mark.parametrize(
    "protected",
    ("outputs", "history", "runtime", "cache", "manual-data", "test_outputs"),
)
def test_protected_output_paths_are_rejected(tmp_path: Path, protected: str) -> None:
    with pytest.raises(ProtectedPathError):
        collect_sources(_request(tmp_path, output_raw_dir=tmp_path / protected, dry_run=True))


def test_existing_versioned_collection_is_never_overwritten(tmp_path: Path) -> None:
    first = collect_sources(_request(tmp_path))
    manifest_before = (first.collection_dir / "collection_manifest.json").read_bytes()

    with pytest.raises(FileExistsError):
        collect_sources(_request(tmp_path))

    assert (first.collection_dir / "collection_manifest.json").read_bytes() == manifest_before


def test_manifest_records_hash_size_and_csv_row_count(tmp_path: Path) -> None:
    ballpark = tmp_path / "ballpark.csv"
    ballpark.write_text("park,factor\nA,1.05\nB,0.98\n", encoding="utf-8")
    odds = tmp_path / "odds.jsonl"
    odds.write_text('{"game": 1}\n{"game": 2}\n', encoding="utf-8")

    result = collect_sources(
        _request(
            tmp_path,
            source_options={
                "ballpark_factors_path": ballpark,
                "odds_archive_path": odds,
                "odds_provider": "licensed-test-archive",
            },
        )
    )
    assert result.manifest is not None
    payload = json.loads(
        (result.collection_dir / "collection_manifest.json").read_text(encoding="utf-8")
    )
    by_name = {source["source_name"]: source for source in payload["sources"]}
    record = by_name["approved_supplied_ballpark_factors"]
    copied = result.collection_dir / record["local_file_path"]

    assert record["sha256"] == hashlib.sha256(ballpark.read_bytes()).hexdigest()
    assert record["file_size"] == ballpark.stat().st_size == copied.stat().st_size
    assert record["row_count"] == 2
    assert by_name["approved_supplied_odds"]["row_count"] == 2
    assert payload["blockers"] == []


@pytest.mark.parametrize("sport", ("nba", "nfl", "nhl", "wnba"))
def test_future_sport_stubs_fail_closed_without_writing(
    tmp_path: Path, sport: str
) -> None:
    adapter = get_collection_adapter(sport)
    assert adapter.source_contracts() == ()
    assert adapter.required_sources

    with pytest.raises(UnsupportedSportCollectionError, match="registry stub"):
        collect_sources(_request(tmp_path, sport=sport, dry_run=True))
    assert not (tmp_path / "raw-collections").exists()


@pytest.mark.parametrize("name", ("StatMuse", "sportsbook_scraper", "Sportsbook Web Scraping"))
def test_disallowed_scraping_contracts_are_rejected(name: str) -> None:
    with pytest.raises(SourceContractError):
        SourceContract(
            source_name=name,
            source_type="website",
            source_url_provider="unsupported website",
            license_terms_note="test",
            acquisition_method=AcquisitionMethod.SUPPLIED_FILE,
        )


def test_statmuse_named_supplied_file_is_rejected(tmp_path: Path) -> None:
    forbidden = tmp_path / "statmuse.csv"
    forbidden.write_text("a\n1\n", encoding="utf-8")

    with pytest.raises(SourceContractError):
        collect_sources(
            _request(
                tmp_path,
                dry_run=True,
                source_options={"odds_archive_path": forbidden},
            )
        )
