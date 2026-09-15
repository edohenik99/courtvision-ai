"""Deterministic regressions for complete create-exclusive lock publication."""
from datetime import datetime, timezone
import errno
from pathlib import Path

import pytest

from courtvision.sports.mlb.training import hr_prospective_trial as trial


def _lock(root: Path, operation: str):
    return trial._TrialStoreLock(
        root, operation=operation, control_id="fixture-control",
        clock=lambda: datetime(2026, 9, 15, 12, tzinfo=timezone.utc),
    )


def test_contender_never_observes_the_first_writers_uninitialized_lock(tmp_path, monkeypatch):
    first = _lock(tmp_path, "first")
    contender = _lock(tmp_path, "contender")
    original_open = trial.os.open
    interrupted = False

    def interrupt_before_metadata_write(path, flags, mode=0o777):
        nonlocal interrupted
        descriptor = original_open(path, flags, mode)
        if first.metadata["owner_token"] in str(path):
            interrupted = True
            assert Path(path).read_bytes() == b""
            assert not first.path.exists()
            contender.acquire()
        return descriptor

    monkeypatch.setattr(trial.os, "open", interrupt_before_metadata_write)
    with pytest.raises(trial.MLBHRProspectiveTrialBusyError):
        first.acquire()
    assert interrupted
    assert contender.acquired is True
    assert first.acquired is False
    assert trial._load_lock(first.path)[0] == contender.metadata


def test_atomic_publication_source_is_complete_before_link(tmp_path, monkeypatch):
    lock = _lock(tmp_path, "owner")
    original_link = trial.os.link
    observations = []

    def verify_complete(source, destination):
        observations.append(trial._load_lock(Path(source))[0])
        assert not Path(destination).exists()
        original_link(source, destination)

    monkeypatch.setattr(trial.os, "link", verify_complete)
    lock.acquire()
    assert observations == [lock.metadata]
    assert lock.path.read_bytes() == lock.data


def test_malformed_existing_lock_is_not_reclassified_as_busy_or_replaced(tmp_path):
    lock = _lock(tmp_path, "contender")
    lock.path.write_bytes(b'{"truncated":')
    with pytest.raises(trial.MLBHRProspectiveTrialLockError, match="malformed"):
        lock.acquire()
    assert lock.path.read_bytes() == b'{"truncated":'
    assert lock.acquired is False


def test_unsupported_atomic_publication_fails_without_fallback(tmp_path, monkeypatch):
    lock = _lock(tmp_path, "owner")

    def unsupported(source, destination):
        raise OSError(errno.ENOTSUP, "hard links unavailable")

    monkeypatch.setattr(trial.os, "link", unsupported)
    with pytest.raises(trial.MLBHRProspectiveTrialLockError, match="cannot publish"):
        lock.acquire()
    assert not lock.path.exists()
    assert lock.acquired is False


def test_failed_metadata_sync_never_publishes_canonical_lock(tmp_path, monkeypatch):
    lock = _lock(tmp_path, "owner")

    def failed_sync(descriptor):
        raise OSError("simulated failed durable write")

    monkeypatch.setattr(trial.os, "fsync", failed_sync)
    with pytest.raises(trial.MLBHRProspectiveTrialLockError, match="staging evidence retained"):
        lock.acquire()
    assert not lock.path.exists()
    prepared = list(tmp_path.glob(".prospective-lock-*.tmp"))
    assert len(prepared) == 1
    assert prepared[0].read_bytes() == lock.data
