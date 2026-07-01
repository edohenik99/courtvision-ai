from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest

from courtvision.data_collection.core import CollectionError
from courtvision.data_collection.path_guards import ProtectedPathError
from courtvision.data_collection.resumable import (
    ChunkSize,
    CollectionState,
    ResumableCollector,
    RetryPolicy,
    build_chunk_plan,
    load_collection_state,
    merge_chunk_csvs,
    run_with_retry,
    save_collection_state,
    validate_staging_dir,
)


def _state() -> CollectionState:
    return CollectionState(
        sport="mlb",
        season=2025,
        start_date="2025-04-01",
        end_date="2025-04-15",
        chunk_size_days=7,
    )


def test_chunk_plan_is_deterministic_inclusive_and_validated() -> None:
    chunks = build_chunk_plan(
        date(2025, 4, 1), date(2025, 4, 15), ChunkSize.WEEKLY
    )

    assert [(chunk.start, chunk.end) for chunk in chunks] == [
        (date(2025, 4, 1), date(2025, 4, 7)),
        (date(2025, 4, 8), date(2025, 4, 14)),
        (date(2025, 4, 15), date(2025, 4, 15)),
    ]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]
    assert chunks[0].chunk_key == "2025-04-01_2025-04-07"
    with pytest.raises(ValueError, match="positive"):
        build_chunk_plan(date(2025, 4, 1), date(2025, 4, 2), 0)
    with pytest.raises(ValueError, match="must not be after"):
        build_chunk_plan(date(2025, 4, 2), date(2025, 4, 1), 7)


def test_collection_state_round_trips_and_corruption_fails_closed(
    tmp_path: Path,
) -> None:
    state = _state()
    state.chunks_completed.append("2025-04-01_2025-04-07")
    save_collection_state(state, tmp_path)

    assert load_collection_state(tmp_path) == state
    assert not (tmp_path / "collection_state.json.tmp").exists()

    (tmp_path / "collection_state.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(CollectionError, match="invalid collection checkpoint"):
        load_collection_state(tmp_path)


def test_retry_uses_bounded_exponential_backoff_and_propagates_permanent_errors() -> None:
    attempts = 0
    sleeps: list[float] = []

    def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("temporary")
        return "ok"

    assert run_with_retry(
        flaky,
        RetryPolicy(max_retries=3, backoff_base=2, backoff_cap=3),
        (OSError,),
        _sleep=sleeps.append,
    ) == "ok"
    assert attempts == 3
    assert sleeps == [1, 2]

    with pytest.raises(ValueError, match="permanent"):
        run_with_retry(
            lambda: (_ for _ in ()).throw(ValueError("permanent")),
            RetryPolicy(),
            (OSError,),
            _sleep=sleeps.append,
        )


def test_merge_writes_one_header_counts_rows_and_rejects_mismatched_headers(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_text("game_date,game_pk\n2025-04-01,1\n", encoding="utf-8")
    second.write_text("game_date,game_pk\n2025-04-02,2\n", encoding="utf-8")
    output = tmp_path / "merged.csv"

    merged, rows = merge_chunk_csvs((first, second), output)

    assert merged == output
    assert rows == 2
    assert output.read_text(encoding="utf-8").splitlines() == [
        "game_date,game_pk",
        "2025-04-01,1",
        "2025-04-02,2",
    ]
    with pytest.raises(FileExistsError):
        merge_chunk_csvs((first, second), output)

    bad = tmp_path / "bad.csv"
    bad.write_text("other\nvalue\n", encoding="utf-8")
    with pytest.raises(CollectionError, match="mismatched CSV header"):
        merge_chunk_csvs((first, bad), tmp_path / "bad-merged.csv")


def test_resumable_collector_checkpoints_failure_then_skips_completed_chunk(
    tmp_path: Path,
) -> None:
    chunks = build_chunk_plan(date(2025, 4, 1), date(2025, 4, 15), 7)
    state = _state()
    first_calls: list[str] = []

    def interrupted(chunk, chunks_dir: Path) -> Path:
        first_calls.append(chunk.chunk_key)
        if chunk.chunk_index == 1:
            raise OSError("interrupted")
        path = chunks_dir / f"{chunk.chunk_key}.csv"
        path.write_text(
            f"game_date,game_pk\n{chunk.start.isoformat()},{chunk.chunk_index}\n",
            encoding="utf-8",
        )
        return path.resolve()

    collector = ResumableCollector(
        tmp_path,
        state,
        RetryPolicy(max_retries=0),
        _sleep=lambda _: None,
    )
    collector.set_chunks(chunks)
    with pytest.raises(OSError, match="interrupted"):
        collector.merge(tmp_path / "merged.csv", interrupted, (OSError,))

    checkpoint = load_collection_state(tmp_path)
    assert checkpoint is not None
    assert checkpoint.chunks_completed == [chunks[0].chunk_key]
    assert checkpoint.chunks_failed == [chunks[1].chunk_key]

    resumed_calls: list[str] = []

    def resumed(chunk, chunks_dir: Path) -> Path:
        resumed_calls.append(chunk.chunk_key)
        path = chunks_dir / f"{chunk.chunk_key}.csv"
        path.write_text(
            f"game_date,game_pk\n{chunk.start.isoformat()},{chunk.chunk_index}\n",
            encoding="utf-8",
        )
        return path.resolve()

    resumed_collector = ResumableCollector(
        tmp_path,
        checkpoint,
        RetryPolicy(max_retries=0),
        _sleep=lambda _: None,
    )
    resumed_collector.set_chunks(chunks)
    result = resumed_collector.merge(tmp_path / "merged.csv", resumed, (OSError,))

    assert result == tmp_path / "merged.csv"
    assert chunks[0].chunk_key not in resumed_calls
    assert resumed_calls == [chunks[1].chunk_key, chunks[2].chunk_key]
    final_state = json.loads(
        (tmp_path / "collection_state.json").read_text(encoding="utf-8")
    )
    assert final_state["merged"] is True
    assert final_state["chunks_failed"] == []


def test_staging_path_guard_rejects_protected_components(tmp_path: Path) -> None:
    with pytest.raises(ProtectedPathError):
        validate_staging_dir(tmp_path / "outputs" / "statcast")
