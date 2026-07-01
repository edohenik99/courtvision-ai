from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys

import pandas as pd
import pytest

from courtvision.data_collection.core import CollectionError, CollectionRequest
from courtvision.data_collection.resumable import ChunkSize, RetryPolicy
from courtvision.sports.mlb.data_collection import statcast_chunked_collector as module


def _request(tmp_path: Path) -> CollectionRequest:
    return CollectionRequest(
        sport="mlb",
        season=2025,
        start_date=date(2025, 4, 1),
        end_date=date(2025, 4, 15),
        output_raw_dir=tmp_path / "raw",
        collection_id="vstatcast-test",
        collection_timestamp=datetime(2026, 6, 30, tzinfo=timezone.utc),
    )


class _Cache:
    def __init__(self) -> None:
        self.enabled = 0

    def enable(self) -> None:
        self.enabled += 1


class _Pybaseball:
    def __init__(self) -> None:
        self.cache = _Cache()
        self.calls: list[dict[str, object]] = []

    def statcast(self, **kwargs: object) -> pd.DataFrame:
        self.calls.append(kwargs)
        return pd.DataFrame(
            [{"game_date": kwargs["start_dt"], "game_pk": len(self.calls)}]
        )


def test_fetch_uses_pybaseball_chunk_bounds_and_cache_is_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _Pybaseball()
    monkeypatch.setitem(sys.modules, "pybaseball", fake)
    chunk = module.build_chunk_plan(date(2025, 4, 1), date(2025, 4, 7), 7)[0]

    module.enable_pybaseball_cache()
    path = module.fetch_statcast_chunk(chunk, tmp_path, allow_network=True)

    assert fake.cache.enabled == 1
    assert fake.calls == [
        {
            "start_dt": "2025-04-01",
            "end_dt": "2025-04-07",
            "verbose": False,
            "parallel": False,
        }
    ]
    assert path.name == "2025-04-01_2025-04-07.csv"
    assert pd.read_csv(path).to_dict("records") == [
        {"game_date": "2025-04-01", "game_pk": 1}
    ]


def test_fetch_is_default_deny_before_importing_pybaseball(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "_pybaseball_module",
        lambda: (_ for _ in ()).throw(AssertionError("must not import")),
    )
    chunk = module.build_chunk_plan(date(2025, 4, 1), date(2025, 4, 1), 7)[0]

    with pytest.raises(PermissionError, match="allow_network=True"):
        module.fetch_statcast_chunk(chunk, tmp_path)


def test_dry_run_does_not_enable_cache_create_state_or_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "enable_pybaseball_cache",
        lambda: (_ for _ in ()).throw(AssertionError("must not enable cache")),
    )

    result = module.run_chunked_statcast(
        _request(tmp_path),
        tmp_path / "staging",
        tmp_path / "statcast.csv",
        dry_run=True,
    )

    assert result is None
    assert not (tmp_path / "staging").exists()


def test_interrupted_run_resumes_without_refetching_completed_chunks_and_merges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request(tmp_path)
    staging = tmp_path / "raw" / "staging"
    output = tmp_path / "raw" / "statcast.csv"
    monkeypatch.setattr(module, "enable_pybaseball_cache", lambda: None)
    first_calls: list[str] = []

    def interrupted(chunk, chunks_dir: Path, *, allow_network: bool) -> Path:
        assert allow_network is True
        first_calls.append(chunk.chunk_key)
        if chunk.chunk_index == 1:
            raise OSError("network interruption")
        path = chunks_dir / f"{chunk.chunk_key}.csv"
        path.write_text(
            f"game_date,game_pk\n{chunk.start.isoformat()},{chunk.chunk_index}\n",
            encoding="utf-8",
        )
        return path.resolve()

    monkeypatch.setattr(module, "fetch_statcast_chunk", interrupted)
    with pytest.raises(OSError, match="network interruption"):
        module.run_chunked_statcast(
            request,
            staging,
            output,
            allow_network=True,
            retry_policy=RetryPolicy(max_retries=0),
            _sleep=lambda _: None,
        )
    assert not output.exists()

    resumed_calls: list[str] = []

    def resumed(chunk, chunks_dir: Path, *, allow_network: bool) -> Path:
        resumed_calls.append(chunk.chunk_key)
        path = chunks_dir / f"{chunk.chunk_key}.csv"
        path.write_text(
            f"game_date,game_pk\n{chunk.start.isoformat()},{chunk.chunk_index}\n",
            encoding="utf-8",
        )
        return path.resolve()

    monkeypatch.setattr(module, "fetch_statcast_chunk", resumed)
    merged = module.run_chunked_statcast(
        request,
        staging,
        output,
        resume=True,
        allow_network=True,
        retry_policy=RetryPolicy(max_retries=0),
        _sleep=lambda _: None,
    )

    assert merged == output
    assert first_calls[0] not in resumed_calls
    assert output.read_text(encoding="utf-8").count("game_date,game_pk") == 1
    state = json.loads((staging / "collection_state.json").read_text())
    assert state["merged"] is True
    assert len(state["chunks_completed"]) == 3


def test_empty_early_chunk_is_skipped_recorded_and_not_refetched_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = CollectionRequest(
        sport="mlb",
        season=2025,
        start_date=date(2025, 3, 5),
        end_date=date(2025, 3, 18),
        output_raw_dir=tmp_path / "raw",
        collection_id="vstatcast-empty-resume",
        collection_timestamp=datetime(2026, 6, 30, tzinfo=timezone.utc),
    )
    staging = tmp_path / "raw" / "staging"
    output = tmp_path / "raw" / "statcast.csv"
    monkeypatch.setattr(module, "enable_pybaseball_cache", lambda: None)

    def interrupted(chunk, chunks_dir: Path, *, allow_network: bool) -> Path:
        if chunk.chunk_index == 1:
            raise OSError("interrupted after no-data chunk")
        path = chunks_dir / f"{chunk.chunk_key}.csv"
        path.write_text("different,empty_schema\n", encoding="utf-8")
        return path.resolve()

    monkeypatch.setattr(module, "fetch_statcast_chunk", interrupted)
    with pytest.raises(OSError, match="interrupted after no-data chunk"):
        module.run_chunked_statcast(
            request,
            staging,
            output,
            allow_network=True,
            retry_policy=RetryPolicy(max_retries=0),
            _sleep=lambda _: None,
        )

    resumed_calls: list[str] = []

    def resumed(chunk, chunks_dir: Path, *, allow_network: bool) -> Path:
        resumed_calls.append(chunk.chunk_key)
        path = chunks_dir / f"{chunk.chunk_key}.csv"
        path.write_text(
            "game_date,game_pk\n2025-03-12,1\n",
            encoding="utf-8",
        )
        return path.resolve()

    monkeypatch.setattr(module, "fetch_statcast_chunk", resumed)
    merged = module.run_chunked_statcast(
        request,
        staging,
        output,
        resume=True,
        allow_network=True,
        retry_policy=RetryPolicy(max_retries=0),
        _sleep=lambda _: None,
    )

    assert merged == output
    assert resumed_calls == ["2025-03-12_2025-03-18"]
    assert output.read_text(encoding="utf-8").splitlines() == [
        "game_date,game_pk",
        "2025-03-12,1",
    ]
    state = json.loads((staging / "collection_state.json").read_text())
    assert state["schema_version"] == "1.1"
    assert state["chunk_metadata"][0]["status"] == "empty_no_data"
    assert state["chunk_metadata"][0]["row_count"] == 0
    assert len(state["chunk_metadata"][0]["header_hash"]) == 64
    assert len(state["chunk_metadata"][0]["file_hash"]) == 64
    assert state["chunk_metadata"][1]["status"] == "valid_data"
    assert state["chunk_metadata"][1]["row_count"] == 1
    assert sum(item["row_count"] for item in state["chunk_metadata"]) == 1
    assert (
        state["chunk_metadata"][0]["header_hash"]
        != state["chunk_metadata"][1]["header_hash"]
    )
    assert state["warnings"] == [
        "Chunk 2025-03-05_2025-03-11 contained no data and was skipped during CSV merge."
    ]


def test_nonempty_mismatched_schema_fails_and_records_invalid_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = CollectionRequest(
        sport="mlb",
        season=2025,
        start_date=date(2025, 4, 1),
        end_date=date(2025, 4, 14),
        output_raw_dir=tmp_path / "raw",
        collection_id="vstatcast-invalid-schema",
        collection_timestamp=datetime(2026, 6, 30, tzinfo=timezone.utc),
    )
    staging = tmp_path / "raw" / "staging"
    output = tmp_path / "raw" / "statcast.csv"
    monkeypatch.setattr(module, "enable_pybaseball_cache", lambda: None)

    def fetch(chunk, chunks_dir: Path, *, allow_network: bool) -> Path:
        path = chunks_dir / f"{chunk.chunk_key}.csv"
        content = (
            "game_date,game_pk\n2025-04-01,1\n"
            if chunk.chunk_index == 0
            else "different,columns\nvalue,2\n"
        )
        path.write_text(content, encoding="utf-8")
        return path.resolve()

    monkeypatch.setattr(module, "fetch_statcast_chunk", fetch)
    with pytest.raises(
        CollectionError,
        match="non-empty chunk with mismatched CSV header.*2025-04-08_2025-04-14",
    ):
        module.run_chunked_statcast(
            request,
            staging,
            output,
            allow_network=True,
            retry_policy=RetryPolicy(max_retries=0),
            _sleep=lambda _: None,
        )

    assert not output.exists()
    state = json.loads((staging / "collection_state.json").read_text())
    assert [item["status"] for item in state["chunk_metadata"]] == [
        "valid_data",
        "invalid_schema",
    ]
    assert state["chunk_metadata"][1]["row_count"] == 1
    assert set(state["chunk_metadata"][1]) == {
        "chunk_key",
        "status",
        "row_count",
        "header_hash",
        "file_hash",
    }
    assert state["merged"] is False


def test_resume_requires_matching_checkpoint_and_existing_merged_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request(tmp_path)
    staging = tmp_path / "raw" / "staging"
    staging.mkdir(parents=True)
    monkeypatch.setattr(module, "enable_pybaseball_cache", lambda: None)
    (staging / "collection_state.json").write_text(
        json.dumps(
            {
                "sport": "mlb",
                "season": 2025,
                "start_date": "2025-04-01",
                "end_date": "2025-04-15",
                "chunk_size_days": 14,
                "chunks_planned": [],
                "chunks_completed": [],
                "chunks_failed": [],
                "merged": False,
                "schema_version": "1.0",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CollectionError, match="does not match"):
        module.run_chunked_statcast(
            request,
            staging,
            tmp_path / "statcast.csv",
            chunk_size=ChunkSize.WEEKLY,
            resume=True,
            allow_network=True,
        )
