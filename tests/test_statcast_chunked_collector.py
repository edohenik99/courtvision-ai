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
