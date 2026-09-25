"""Publication failure and concurrent-writer contracts on the actual filesystem."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import stat
from threading import Barrier
from types import SimpleNamespace

import pytest

from courtvision.sports.mlb import fact_ledger
from courtvision.sports.mlb.fact_ledger import FactLedgerConflict, MLBFactStore
from test_mlb_game_facts import batter, game, pitcher


def test_create_once_identical_replay_and_roundtrip(tmp_path):
    store = MLBFactStore(tmp_path)
    b = batter()
    path = store.publish(b)
    before = (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
    assert store.publish(b) == path
    assert before == (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
    assert store.read("BATTER", "823100", "700001") == b
    assert list(tmp_path.rglob("*.tmp")) == []
    assert len(list(tmp_path.rglob("*.json"))) == 1


def test_game_and_player_roles_do_not_collide(tmp_path):
    store = MLBFactStore(tmp_path)
    paths = {store.publish(fact) for fact in (game(), batter(), pitcher())}
    assert len(paths) == 3
    assert store.read("GAME", "823100") == game()
    assert store.read("PITCHER", "823100", "700001") == pitcher()


def test_conflicting_replay_cannot_overwrite(tmp_path):
    store = MLBFactStore(tmp_path)
    b = batter()
    path = store.publish(b)
    before = path.read_bytes()
    with pytest.raises(FactLedgerConflict, match="conflicting fact"):
        store.publish(replace(b, runs=3))
    assert path.read_bytes() == before


@pytest.mark.parametrize("fault", ["flush", "link"])
def test_interrupted_publication_exposes_no_partial_record(tmp_path, monkeypatch, fault):
    store = MLBFactStore(tmp_path)
    real_link = os.link
    def denied(*args):
        assert not list(tmp_path.rglob("*.json"))
        raise OSError("simulated interrupted publication")
    with monkeypatch.context() as patch:
        patch.setattr(fact_ledger.os, "fsync" if fault == "flush" else "link", denied)
        with pytest.raises(OSError, match="simulated"):
            store.publish(batter())
    assert not list(tmp_path.rglob("*.json"))
    assert not list(tmp_path.rglob("*.tmp"))
    assert os.link is real_link
    assert store.publish(batter()).is_file()


def test_hard_interruption_staging_is_not_readable_as_a_record(tmp_path):
    directory = tmp_path / "batter" / "823100"
    directory.mkdir(parents=True)
    (directory / ".fact-interrupted.tmp").write_bytes(b'{"fact":')
    store = MLBFactStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.read("BATTER", "823100", "700001")
    store.publish(batter())
    assert store.read("BATTER", "823100", "700001") == batter()


@pytest.mark.parametrize("conflicting", [False, True])
def test_concurrent_publication_is_atomic_and_never_duplicates(tmp_path, monkeypatch, conflicting):
    count = 8
    barrier = Barrier(count)
    original = os.link
    b = batter()
    def simultaneous(source, target):
        # All writers have completed, fsynced and closed staging before racing.
        barrier.wait(timeout=15)
        return original(source, target)
    monkeypatch.setattr(fact_ledger.os, "link", simultaneous)
    def publish(index):
        fact = replace(b, runs=index) if conflicting else b
        try:
            MLBFactStore(tmp_path).publish(fact)
            return fact
        except FactLedgerConflict:
            return None
    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(publish, range(count)))
    successes = [r for r in results if r is not None]
    assert len(successes) == (1 if conflicting else count)
    assert len(list(tmp_path.rglob("*.json"))) == 1
    assert not list(tmp_path.rglob("*.tmp"))
    assert MLBFactStore(tmp_path).read("BATTER", "823100", "700001") == successes[0]


@pytest.mark.parametrize("corruption", ["partial", "hash", "identity", "safety", "duplicate"])
def test_corrupt_committed_record_fails_closed(tmp_path, corruption):
    store = MLBFactStore(tmp_path)
    path = store.publish(batter())
    envelope = json.loads(path.read_bytes())
    if corruption == "partial":
        data = b'{"fact":'
    elif corruption == "duplicate":
        data = b'{"fact":{},"fact":{}}'
    else:
        if corruption == "hash":
            envelope["factual_record_hash"] = "0" * 64
        elif corruption == "identity":
            envelope["fact"]["mlbam_player_id"] = "700002"
        else:
            envelope["fact"]["eligible_for_betting"] = True
        data = json.dumps(envelope).encode()
    path.write_bytes(data)
    with pytest.raises(FactLedgerConflict):
        store.read("BATTER", "823100", "700001")
    with pytest.raises(FactLedgerConflict):
        store.publish(batter())
    assert path.read_bytes() == data


@pytest.mark.parametrize("role,game_id,player_id", [
    ("../batter", "823100", "700001"), ("BATTER", "../823100", "700001"),
    ("BATTER", "823100", "../../700001"), ("GAME", "823100", "700001"),
])
def test_identity_cannot_escape_store(tmp_path, role, game_id, player_id):
    with pytest.raises(ValueError):
        MLBFactStore(tmp_path).read(role, game_id, player_id)


def test_reparse_point_detected_before_write(tmp_path, monkeypatch):
    store = MLBFactStore(tmp_path)
    original = Path.lstat
    def junction_stat(path, *args, **kwargs):
        if path.name == "batter":
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=stat.IO_REPARSE_TAG_MOUNT_POINT)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", junction_stat)
    with pytest.raises(ValueError, match="symlinks or junctions"):
        store.publish(batter())
    assert not list(tmp_path.iterdir())


def test_store_does_not_require_path_is_junction(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise AttributeError("Path.is_junction is unavailable on Python 3.11")
    monkeypatch.setattr(Path, "is_junction", unavailable, raising=False)
    store = MLBFactStore(tmp_path / "new-store")
    path = store.publish(batter())
    assert store.read("BATTER", "823100", "700001") == batter()
    assert store.publish(batter()) == path


def test_junction_inspection_permission_error_fails_closed(tmp_path, monkeypatch):
    original = Path.lstat
    def denied(path, *args, **kwargs):
        if path == tmp_path:
            raise PermissionError("junction inspection denied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", denied)
    with pytest.raises(PermissionError, match="junction inspection denied"):
        MLBFactStore(tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="Windows junction containment")
def test_real_windows_junction_is_rejected_without_writing_target(tmp_path):
    import _winapi
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "junction"
    _winapi.CreateJunction(str(target), str(alias))
    with pytest.raises(ValueError, match="symlinks or junctions"):
        MLBFactStore(alias / "nested")
    store_root = tmp_path / "store"
    store_root.mkdir()
    _winapi.CreateJunction(str(target), str(store_root / "batter"))
    with pytest.raises(ValueError, match="symlinks or junctions"):
        MLBFactStore(store_root).publish(batter())
    assert not list(target.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path identity")
def test_extended_windows_path_identity_cannot_create_false_escape(tmp_path, monkeypatch):
    store = MLBFactStore(tmp_path)
    original = Path.resolve
    def extended(path, *args, **kwargs):
        resolved = str(original(path, *args, **kwargs))
        return Path(resolved if resolved.startswith("\\\\?\\") else "\\\\?\\" + resolved)
    monkeypatch.setattr(Path, "resolve", extended)
    store.publish(batter())
    assert store.read("BATTER", "823100", "700001") == batter()
