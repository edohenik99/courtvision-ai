from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from courtvision.data_collection.core import (
    CollectionError,
    CollectionRequest,
    UnsupportedSportCollectionError,
    collect_sources,
)
from courtvision.sports.mlb.data_collection.adapter import (
    CHADWICK_REGISTER_FILENAME,
    CHADWICK_REGISTER_URL,
)
from courtvision.data_collection.path_guards import ProtectedPathError
from courtvision.data_collection.resumable import (
    ChunkSize,
    CollectionState,
    save_collection_state,
)
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


def _chadwick_archive(rows_per_shard: int = 2) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for suffix in "0123456789abcdef":
            rows = "".join(
                f"{suffix}{index},Player {suffix}{index}\n"
                for index in range(rows_per_shard)
            )
            archive.writestr(
                f"register-master/data/people-{suffix}.csv",
                "key_person,name_last\n" + rows,
            )
    return payload.getvalue()


class _DownloadResponse:
    def __init__(self, payload: bytes, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.closed = False

    def raise_for_status(self) -> None:
        if self.error is not None:
            raise self.error

    def iter_content(self, chunk_size: int) -> tuple[bytes, ...]:
        return tuple(
            self.payload[offset : offset + chunk_size]
            for offset in range(0, len(self.payload), chunk_size)
        )

    def close(self) -> None:
        self.closed = True


def test_dry_run_writes_nothing_and_reports_required_blockers(tmp_path: Path) -> None:
    request = _request(tmp_path, dry_run=True)

    result = collect_sources(request)

    assert result.dry_run
    assert not request.output_raw_dir.exists()
    assert any("odds" in blocker.lower() for blocker in result.blockers)
    assert any("ballpark" in blocker.lower() for blocker in result.blockers)


def test_statcast_adapter_wires_chunk_size_and_manifest_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    def fake_chunked_statcast(request, staging_dir, output_path, **kwargs):
        calls.append(
            {
                "request": request,
                "staging_dir": staging_dir,
                "output_path": output_path,
                **kwargs,
            }
        )
        output_path.write_text(
            "game_date,game_pk\n2025-04-01,1\n", encoding="utf-8"
        )
        state = CollectionState(
            sport=request.sport,
            season=request.season,
            start_date=request.start_date.isoformat(),
            end_date=request.end_date.isoformat(),
            chunk_size_days=14,
            chunks_planned=["2025-04-01_2025-04-02"],
            chunks_completed=["2025-04-01_2025-04-02"],
            merged=True,
        )
        save_collection_state(state, staging_dir)
        return output_path

    monkeypatch.setattr(
        "courtvision.sports.mlb.data_collection.adapter.run_chunked_statcast",
        fake_chunked_statcast,
    )
    result = collect_sources(
        _request(
            tmp_path,
            start_date=date(2025, 4, 1),
            end_date=date(2025, 4, 2),
            source_options={
                "fetch_statcast": True,
                "statcast_chunk_size": "biweekly",
            },
        )
    )

    assert result.manifest is not None
    assert result.manifest.collector_version == "1.2.0"
    assert calls[0]["chunk_size"] is ChunkSize.BIWEEKLY
    assert calls[0]["resume"] is False
    assert calls[0]["allow_network"] is True
    statcast = next(
        source
        for source in result.manifest.sources
        if source.source_name == "statcast_pybaseball"
    )
    assert (result.collection_dir / statcast.local_file_path).is_file()


def test_framework_preserves_checkpoint_and_resumes_same_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "courtvision.sports.mlb.data_collection.statcast_chunked_collector.enable_pybaseball_cache",
        lambda: None,
    )
    first_calls: list[str] = []

    def interrupted(chunk, chunks_dir: Path, *, allow_network: bool) -> Path:
        first_calls.append(chunk.chunk_key)
        if chunk.chunk_index == 1:
            raise RuntimeError("interrupted")
        path = chunks_dir / f"{chunk.chunk_key}.csv"
        path.write_text("\n", encoding="utf-8")
        return path.resolve()

    monkeypatch.setattr(
        "courtvision.sports.mlb.data_collection.statcast_chunked_collector.fetch_statcast_chunk",
        interrupted,
    )
    request = _request(
        tmp_path,
        start_date=date(2025, 4, 1),
        end_date=date(2025, 4, 8),
        source_options={"fetch_statcast": True},
    )
    with pytest.raises(RuntimeError, match="interrupted"):
        collect_sources(request)

    collection_dir = (
        request.output_raw_dir / "mlb" / "2025" / str(request.collection_id)
    )
    assert (
        collection_dir
        / "sources"
        / "statcast_pybaseball"
        / "collection_state.json"
    ).is_file()

    resumed_calls: list[str] = []

    def resumed(chunk, chunks_dir: Path, *, allow_network: bool) -> Path:
        resumed_calls.append(chunk.chunk_key)
        path = chunks_dir / f"{chunk.chunk_key}.csv"
        path.write_text("game_date,game_pk\n2025-04-08,2\n", encoding="utf-8")
        return path.resolve()

    monkeypatch.setattr(
        "courtvision.sports.mlb.data_collection.statcast_chunked_collector.fetch_statcast_chunk",
        resumed,
    )
    result = collect_sources(
        _request(
            tmp_path,
            start_date=date(2025, 4, 1),
            end_date=date(2025, 4, 8),
            resume=True,
            source_options={"fetch_statcast": True},
        )
    )

    assert result.manifest is not None
    assert first_calls[0] not in resumed_calls
    assert resumed_calls == ["2025-04-08_2025-04-08"]
    skipped_warning = (
        "Chunk 2025-04-01_2025-04-07 contained no data and was skipped during CSV merge."
    )
    assert skipped_warning in result.manifest.warnings
    statcast_source = next(
        source
        for source in result.manifest.sources
        if source.source_name == "statcast_pybaseball"
    )
    assert skipped_warning in statcast_source.warnings
    assert statcast_source.row_count == 1
    manifest_path = result.collection_dir / "collection_manifest.json"
    assert manifest_path.is_file()
    assert skipped_warning in json.loads(manifest_path.read_text())["warnings"]


def test_resume_without_checkpoint_does_not_mutate_existing_folder(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path, resume=True)
    collection_dir = (
        request.output_raw_dir / "mlb" / "2025" / str(request.collection_id)
    )
    collection_dir.mkdir(parents=True)
    sentinel = collection_dir / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="without a checkpoint"):
        collect_sources(request)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert list(collection_dir.iterdir()) == [sentinel]


def test_chadwick_dry_run_plans_without_downloading_or_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_download(*args: object, **kwargs: object) -> None:
        raise AssertionError("Chadwick must not download during dry-run")

    monkeypatch.setattr("requests.get", fail_download)
    request = _request(
        tmp_path,
        dry_run=True,
        source_options={"fetch_chadwick_register": True},
    )

    result = collect_sources(request)

    assert "chadwick_bureau_register" in result.planned_sources
    assert not request.output_raw_dir.exists()


def test_supplied_chadwick_register_fallback_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supplied = tmp_path / "chadwick.csv"
    supplied.write_text("key_person,name_last\nabc12345,Example\n", encoding="utf-8")

    def fail_download(*args: object, **kwargs: object) -> None:
        raise AssertionError("supplied Chadwick fallback must not download")

    monkeypatch.setattr("requests.get", fail_download)
    result = collect_sources(
        _request(
            tmp_path,
            source_options={"chadwick_register_path": supplied},
        )
    )

    assert result.manifest is not None
    record = next(
        source
        for source in result.manifest.sources
        if source.source_name == "chadwick_bureau_register"
    )
    copied = result.collection_dir / record.local_file_path
    assert copied.read_bytes() == supplied.read_bytes()
    assert record.row_count == 1


def test_failed_chadwick_download_reports_blocker_and_leaves_no_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _DownloadResponse(b"", error=RuntimeError("service unavailable"))
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: response)
    request = _request(
        tmp_path,
        source_options={"fetch_chadwick_register": True},
    )

    with pytest.raises(CollectionError, match="download blocker: service unavailable"):
        collect_sources(request)

    assert response.closed
    assert not (
        request.output_raw_dir
        / request.sport
        / str(request.season)
        / str(request.collection_id)
    ).exists()


def test_downloaded_chadwick_manifest_records_provenance_hash_and_row_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _chadwick_archive(rows_per_shard=2)
    response = _DownloadResponse(archive)
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: response)

    result = collect_sources(
        _request(
            tmp_path,
            source_options={"fetch_chadwick_register": True},
        )
    )

    assert result.manifest is not None
    record = next(
        source
        for source in result.manifest.sources
        if source.source_name == "chadwick_bureau_register"
    )
    stored = result.collection_dir / record.local_file_path
    assert stored.name == CHADWICK_REGISTER_FILENAME
    assert stored.read_bytes() == archive
    assert record.source_url_provider == CHADWICK_REGISTER_URL
    assert "Open Data Commons Attribution License 1.0" in record.license_terms_note
    assert record.sha256 == hashlib.sha256(archive).hexdigest()
    assert record.file_size == len(archive)
    assert record.row_count == 32
    assert record.collection_timestamp == COLLECTED_AT.isoformat()


def test_chadwick_fetch_rejects_protected_output_path_before_downloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_download(*args: object, **kwargs: object) -> None:
        raise AssertionError("protected output must be rejected before download")

    monkeypatch.setattr("requests.get", fail_download)

    with pytest.raises(ProtectedPathError):
        collect_sources(
            _request(
                tmp_path,
                output_raw_dir=tmp_path / "outputs",
                source_options={"fetch_chadwick_register": True},
            )
        )


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
