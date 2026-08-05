from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
import hashlib
import json
import os
from pathlib import Path
import threading

import pandas as pd
import pytest

from courtvision.prospective.contracts import GitProvenanceV1, canonical_json_bytes
from courtvision.prospective.model_build import (
    PLAYER_BASELINE_COLUMNS,
    TEAM_BASELINE_COLUMNS,
    create_verified_model_build,
)
from courtvision.prospective import model_manifest_io
from courtvision.prospective.model_manifest_io import (
    BUILD_STORE_LOCK_FILENAME,
    REQUIRED_BUILD_FILENAMES,
    ModelBuildConflictError,
    ModelBuildLockError,
    ModelBuildPublicationError,
    ModelBuildStoreBusyError,
    ModelBuildValidationError,
    load_model_build_manifest,
    load_verified_model_build,
    validate_verified_model_build,
)


NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
START = date(2025, 1, 1)
END = date(2025, 6, 30)


def _row() -> dict[str, object]:
    return {
        "game_id": 1001,
        "game_date": "2025-01-02",
        "player_id": 7,
        "player_name": "Example Player",
        "team_abbr": "TOR",
        "min": 32,
        "pts": 24,
        "reb": 8,
        "ast": 6,
        "stl": 1,
        "blk": 0,
        "fg3m": 3,
    }


def _player_frame(points: float = 20.5) -> pd.DataFrame:
    values: dict[str, object] = {
        "player_id": "7",
        "player_name": "Example Player",
        "team_abbr": "TOR",
        "games": 10,
        "min_avg": 31.0,
        "min_recent": 32.0,
        "player_key": "example player__TOR",
    }
    for stat in ("pts", "reb", "ast", "stl", "blk", "fg3m"):
        values[f"{stat}_avg"] = points if stat == "pts" else 2.0
        values[f"{stat}_recent"] = 3.0
        values[f"{stat}_std"] = 1.0
    return pd.DataFrame([values], columns=PLAYER_BASELINE_COLUMNS)


def _team_frame(points: float = 110.5) -> pd.DataFrame:
    values: dict[str, object] = {"team_abbr": "TOR", "games": 10}
    for column in TEAM_BASELINE_COLUMNS[2:]:
        values[column] = points if column == "team_pts_avg" else 5.0
    return pd.DataFrame([values], columns=TEAM_BASELINE_COLUMNS)


def _create(tmp_path: Path, **overrides: object):
    values: dict[str, object] = {
        "normalized_rows": [_row()],
        "provider_name": "synthetic-provider",
        "provider_endpoint_version": "stats-v1",
        "requested_start_date": START,
        "requested_end_date": END,
        "build_configuration": {"baseline": {"minimum_minutes": 8}},
        "build_git_provenance": GitProvenanceV1("1" * 40, False, "2" * 64),
        "model_build_tool_version": "synthetic-builder-v1",
        "player_baseline_builder": lambda _: _player_frame(),
        "team_baseline_builder": lambda _: _team_frame(),
        "repository_root": tmp_path,
        "output_root": tmp_path / "outputs",
        "training_run_id": "run-001",
        "clock": lambda: NOW,
    }
    values.update(overrides)
    return create_verified_model_build(**values)  # type: ignore[arg-type]


def _hashes(path: Path) -> dict[str, str]:
    return {
        file.name: hashlib.sha256(file.read_bytes()).hexdigest()
        for file in path.iterdir()
    }


def test_successful_publication_has_exact_files_and_matching_hashes(tmp_path: Path) -> None:
    result = _create(tmp_path)
    assert {path.name for path in result.path.iterdir()} == REQUIRED_BUILD_FILENAMES
    artifacts = {item.logical_name: item for item in result.manifest.artifacts}
    for logical_name, filename in (
        ("player_baseline", "player_baselines.csv"),
        ("team_baseline", "team_baselines.csv"),
    ):
        data = (result.path / filename).read_bytes()
        assert artifacts[logical_name].sha256 == hashlib.sha256(data).hexdigest()
        assert artifacts[logical_name].size_bytes == len(data)
        assert artifacts[logical_name].repository_relative_path.endswith(
            f"/{result.path.name}/{filename}"
        )


def test_supporting_evidence_and_strict_manifest_round_trip(tmp_path: Path) -> None:
    result = _create(tmp_path)
    loaded = load_model_build_manifest(result.path / "model_build_manifest_v1.json")
    verified = load_verified_model_build(result.path, repository_root=tmp_path)
    assert loaded == result.manifest
    assert verified.manifest == result.manifest
    training = json.loads((result.path / "training_inputs_v1.json").read_text("utf-8"))
    feature = json.loads((result.path / "feature_schema_v1.json").read_text("utf-8"))
    assert training["training_data_digest"] == result.manifest.training.training_data_digest
    assert feature["feature_schema_digest"] == result.manifest.feature_schema_digest


@pytest.mark.parametrize("mutation", ["unknown", "missing"])
def test_unknown_and_missing_manifest_fields_fail(tmp_path: Path, mutation: str) -> None:
    result = _create(tmp_path)
    path = result.path / "model_build_manifest_v1.json"
    payload = json.loads(path.read_bytes())
    if mutation == "unknown":
        payload["unknown"] = True
    else:
        payload.pop("league")
    path.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(ModelBuildValidationError, match="fields"):
        load_model_build_manifest(path)


@pytest.mark.parametrize(
    "raw, message",
    [
        (b'{"schema_version":1,"schema_version":1}', "duplicate"),
        (b'{"value":NaN}', "numeric constant"),
        (b'{"value":Infinity}', "numeric constant"),
    ],
)
def test_duplicate_keys_nan_and_infinity_fail(
    tmp_path: Path, raw: bytes, message: str
) -> None:
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)
    with pytest.raises(ModelBuildValidationError, match=message):
        load_model_build_manifest(path)


def test_supporting_evidence_digest_tamper_fails(tmp_path: Path) -> None:
    result = _create(tmp_path)
    path = result.path / "training_inputs_v1.json"
    payload = json.loads(path.read_bytes())
    payload["training_data_digest"] = "0" * 64
    path.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(ModelBuildValidationError, match="supporting"):
        validate_verified_model_build(result.path, repository_root=tmp_path)


def test_failure_after_one_staged_file_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = model_manifest_io._write_exclusive
    calls = 0

    def fail_second(path: Path, data: bytes, description: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ModelBuildPublicationError("synthetic staged write failure")
        original(path, data, description)

    monkeypatch.setattr(model_manifest_io, "_write_exclusive", fail_second)
    with pytest.raises(ModelBuildPublicationError, match="synthetic"):
        _create(tmp_path)
    store = tmp_path / "outputs" / "model" / "verified_builds"
    assert store.is_dir()
    assert list(store.iterdir()) == []


def test_manifest_timestamp_failure_publishes_nothing(tmp_path: Path) -> None:
    times = iter(
        [
            datetime(2026, 8, 5, 13, tzinfo=UTC),
            datetime(2026, 8, 5, 12, tzinfo=UTC),
        ]
    )
    with pytest.raises(Exception, match="created_at_utc"):
        _create(tmp_path, clock=lambda: next(times))
    store = tmp_path / "outputs" / "model" / "verified_builds"
    assert store.is_dir()
    assert list(store.iterdir()) == []


def test_final_directory_appears_only_at_atomic_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = model_manifest_io.os.rename
    observed: dict[str, object] = {}

    def observe(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        observed["before_destination"] = os.path.lexists(destination_path)
        observed["staged_files"] = {path.name for path in source_path.iterdir()}
        original(source, destination)
        observed["after_files"] = {path.name for path in destination_path.iterdir()}

    monkeypatch.setattr(model_manifest_io.os, "rename", observe)
    result = _create(tmp_path)
    assert observed["before_destination"] is False
    assert observed["staged_files"] == REQUIRED_BUILD_FILENAMES
    assert observed["after_files"] == REQUIRED_BUILD_FILENAMES
    assert result.path.is_dir()


def test_identical_replay_preserves_existing_hashes_and_mtimes(tmp_path: Path) -> None:
    first = _create(tmp_path)
    hashes = _hashes(first.path)
    mtimes = {path.name: path.stat().st_mtime_ns for path in first.path.iterdir()}
    second = _create(tmp_path)
    assert second.replayed is True
    assert second.path == first.path
    assert _hashes(first.path) == hashes
    assert {path.name: path.stat().st_mtime_ns for path in first.path.iterdir()} == mtimes


def test_conflicting_replay_fails_closed_and_preserves_existing(tmp_path: Path) -> None:
    first = _create(tmp_path)
    before = _hashes(first.path)
    with pytest.raises(ModelBuildConflictError, match="different manifest"):
        _create(
            tmp_path,
            player_baseline_builder=lambda _: _player_frame(points=99.0),
        )
    assert _hashes(first.path) == before


@pytest.mark.parametrize("damage", ["incomplete", "corrupt"])
def test_corrupt_or_incomplete_existing_destination_fails_closed(
    tmp_path: Path, damage: str
) -> None:
    result = _create(tmp_path)
    if damage == "incomplete":
        (result.path / "team_baselines.csv").unlink()
    else:
        (result.path / "model_build_manifest_v1.json").write_bytes(b"not-json")
    with pytest.raises(ModelBuildConflictError, match="invalid"):
        _create(tmp_path)
    if damage == "incomplete":
        assert not (result.path / "team_baselines.csv").exists()
    else:
        assert (result.path / "model_build_manifest_v1.json").read_bytes() == b"not-json"


def test_unexpected_file_fails_validation_and_is_not_repaired(tmp_path: Path) -> None:
    result = _create(tmp_path)
    extra = result.path / "unexpected.txt"
    extra.write_text("unexpected\n", encoding="utf-8")
    with pytest.raises(ModelBuildValidationError, match="unexpected"):
        validate_verified_model_build(result.path, repository_root=tmp_path)
    with pytest.raises(ModelBuildConflictError):
        _create(tmp_path)
    assert extra.exists()


def test_symlink_fails_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _create(tmp_path)
    target = result.path / "player_baselines.csv"
    link = result.path / "linked.csv"
    try:
        link.symlink_to(target)
    except OSError:
        link.write_bytes(b"simulated-link-entry")
        original = Path.is_symlink

        def classify_simulated_link(path: Path) -> bool:
            return path == link or original(path)

        monkeypatch.setattr(Path, "is_symlink", classify_simulated_link)
    with pytest.raises(ModelBuildValidationError, match="symlink"):
        validate_verified_model_build(result.path, repository_root=tmp_path)


def test_visible_valid_store_lock_reports_busy(tmp_path: Path) -> None:
    store = tmp_path / "outputs" / "model" / "verified_builds"
    store.mkdir(parents=True)
    lock = model_manifest_io._BuildStoreLock(
        store / BUILD_STORE_LOCK_FILENAME,
        training_run_id="owner-run",
        clock=lambda: NOW,
    )
    lock.acquire()
    try:
        with pytest.raises(ModelBuildStoreBusyError, match="busy"):
            _create(tmp_path, training_run_id="other-run")
    finally:
        lock.release()
    assert not (store / BUILD_STORE_LOCK_FILENAME).exists()


def test_malformed_store_lock_fails_closed_and_is_not_deleted(tmp_path: Path) -> None:
    store = tmp_path / "outputs" / "model" / "verified_builds"
    store.mkdir(parents=True)
    lock_path = store / BUILD_STORE_LOCK_FILENAME
    lock_path.write_bytes(b"{}")
    with pytest.raises(ModelBuildLockError, match="malformed"):
        _create(tmp_path)
    assert lock_path.read_bytes() == b"{}"


def test_inaccessible_lock_creation_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = model_manifest_io.os.open

    def deny(path: str | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        if str(path).endswith(BUILD_STORE_LOCK_FILENAME):
            raise PermissionError("synthetic lock denial")
        return original(path, flags, mode)

    monkeypatch.setattr(model_manifest_io.os, "open", deny)
    with pytest.raises(ModelBuildLockError, match="cannot create"):
        _create(tmp_path)
    store = tmp_path / "outputs" / "model" / "verified_builds"
    assert list(store.iterdir()) == []


def test_only_verified_lock_owner_removes_lock(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    path = store / BUILD_STORE_LOCK_FILENAME
    lock = model_manifest_io._BuildStoreLock(
        path, training_run_id="owner-run", clock=lambda: NOW
    )
    lock.acquire()
    replacement = dict(lock.metadata)
    replacement["owner_token"] = "different-owner"
    path.write_bytes(canonical_json_bytes(replacement))
    with pytest.raises(ModelBuildLockError, match="ownership changed"):
        lock.release()
    assert path.exists()


def test_concurrent_duplicate_attempts_produce_one_verified_build(tmp_path: Path) -> None:
    barrier = threading.Barrier(2)

    def player_builder(_: pd.DataFrame) -> pd.DataFrame:
        barrier.wait(timeout=5)
        return _player_frame()

    def attempt(run_id: str):
        try:
            return _create(
                tmp_path,
                training_run_id=run_id,
                player_baseline_builder=player_builder,
            )
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("concurrent-one", "concurrent-two")))
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], (ModelBuildStoreBusyError, ModelBuildConflictError))
    store = tmp_path / "outputs" / "model" / "verified_builds"
    final_directories = [path for path in store.iterdir() if not path.name.startswith(".")]
    assert len(final_directories) == 1
    validate_verified_model_build(final_directories[0], repository_root=tmp_path)


def test_windows_rename_permission_error_leaves_existing_and_legacy_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "outputs" / "model" / "player_baselines.csv"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy\n")

    def deny_rename(source: object, destination: object) -> None:
        raise PermissionError("synthetic Windows rename denial")

    monkeypatch.setattr(model_manifest_io.os, "rename", deny_rename)
    with pytest.raises(ModelBuildPublicationError, match="denied"):
        _create(tmp_path)
    assert legacy.read_bytes() == b"legacy\n"
    store = tmp_path / "outputs" / "model" / "verified_builds"
    assert list(store.iterdir()) == []


def test_tests_and_publication_use_only_temporary_roots(tmp_path: Path) -> None:
    result = _create(tmp_path)
    assert result.path.is_relative_to(tmp_path)
    assert "outputs/model/verified_builds" in result.path.as_posix()
    assert not (tmp_path / "data" / "history").exists()
    assert not (tmp_path / "outputs" / "runtime").exists()
