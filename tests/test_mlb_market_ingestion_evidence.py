from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import socket
import stat
from threading import Barrier
from types import SimpleNamespace

import pytest

from courtvision.sports.mlb import market_ingestion_evidence as evidence


NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
DAY = date(2026, 9, 20)
SECRET = "cv-secret-test-key-DO-NOT-LEAK-9347"


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def exchange(**changes: object) -> evidence.MLBOddsHTTPExchange:
    raw = canonical({"id": "event-a", "bookmakers": []})
    values = {
        "request_id": "request-a", "kind": "event_odds",
        "endpoint": "/v4/sports/baseball_mlb/events/event-a/odds",
        "query": (("dateFormat", "iso"), ("markets", "batter_hits"), ("oddsFormat", "american"), ("regions", "us")),
        "requested_at": NOW, "responded_at": NOW + timedelta(seconds=1),
        "http_status": 200, "usage_headers": (("x-requests-last", "1"),),
        "canonical_json": raw, "canonical_json_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "status": "COMPLETE", "declared_budget": 2,
        "declared_cost_before_request": 2, "declared_max_credit_cost": 1,
        "actual_reported_last_cost": 1, "actual_reported_used": 100,
        "actual_reported_remaining": 400, "normalized_record_count": 1,
        "diagnostic_count": 0,
    }
    values.update(changes)
    return evidence.MLBOddsHTTPExchange(**values)


def write(root: Path, **changes: object) -> evidence.MLBOddsEvidenceWriteResult:
    values = {"output_root": root, "run_id": "run-a", "operating_date": DAY,
              "exchanges": (exchange(),), "normalization_summary": {"records": 1, "diagnostics": 0},
              "run_status": "COMPLETE"}
    values.update(changes)
    return evidence.write_ingestion_evidence(**values)


def file_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def test_manifest_binds_every_file_request_identity_and_normalization_count(tmp_path: Path) -> None:
    result = write(tmp_path)
    assert result.status == "COMPLETE"
    assert result.run_directory == tmp_path / "runs" / DAY.isoformat() / "run-a"
    manifest_bytes = (result.run_directory / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert hashlib.sha256(manifest_bytes).hexdigest() == result.manifest_sha256
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((result.run_directory / name).read_bytes()).hexdigest() == digest
    request = json.loads((result.run_directory / "requests.jsonl").read_text())
    assert request["provider"] == "the_odds_api"
    assert request["canonical_json_file"] == "canonical/request-a.json"
    assert request["canonical_json_sha256"] == manifest["files"][request["canonical_json_file"]]
    assert request["declared_budget"] == request["declared_cost_before_request"] == 2
    assert request["actual_reported_last_cost"] == 1
    assert manifest["requests"] == [{"request_id": "request-a", "kind": "event_odds", "canonical_json_sha256": request["canonical_json_sha256"], "canonical_json_file": "canonical/request-a.json", "received_body_sha256": None, "received_body_file": None, "normalized_record_count": 1, "diagnostic_count": 0, "raw_body_redacted": False}]
    assert json.loads((result.run_directory / "COMPLETE").read_text()) == {"manifest_sha256": result.manifest_sha256}
    assert manifest["research_only"] is True
    assert (result.run_directory / request["canonical_json_file"]).read_text() == exchange().canonical_json


def test_exchange_and_receipt_are_immutable(tmp_path: Path) -> None:
    with pytest.raises(FrozenInstanceError):
        exchange().status = "FAILED"
    with pytest.raises(FrozenInstanceError):
        write(tmp_path).status = "FAILED"
    with pytest.raises(evidence.MLBOddsEvidenceError, match="MUTABLE"):
        exchange(query=[("dateFormat", "iso")])


def test_replay_is_read_only_and_exact_content(tmp_path: Path) -> None:
    first = write(tmp_path)
    before = file_snapshot(tmp_path)
    replay = write(tmp_path)
    assert replay.status == "ALREADY_COMPLETE"
    assert replay.manifest_sha256 == first.manifest_sha256
    assert file_snapshot(tmp_path) == before
    assert list(first.run_directory.parent.glob("*.staging-*")) == []


def test_same_run_different_content_conflict_retains_every_original_byte(tmp_path: Path) -> None:
    write(tmp_path)
    before = file_snapshot(tmp_path)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="CONFLICT"):
        write(tmp_path, run_status="PARTIAL")
    assert file_snapshot(tmp_path) == before


@pytest.mark.parametrize("name", ["canonical/request-a.json", "requests.jsonl", "normalization.json", "manifest.json", "COMPLETE"])
def test_complete_marker_never_masks_modified_run_content(tmp_path: Path, name: str) -> None:
    result = write(tmp_path)
    (result.run_directory / name).write_bytes(b"tampered")
    before = file_snapshot(tmp_path)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="CONFLICT"):
        write(tmp_path)
    assert file_snapshot(tmp_path) == before


def test_unexpected_file_and_empty_directory_are_rejected_on_replay(tmp_path: Path) -> None:
    result = write(tmp_path)
    (result.run_directory / "extra").mkdir()
    with pytest.raises(evidence.MLBOddsEvidenceError, match="CONFLICT"):
        write(tmp_path)
    (result.run_directory / "extra" / "foreign.json").write_text("preserve")
    with pytest.raises(evidence.MLBOddsEvidenceError, match="CONFLICT"):
        write(tmp_path)
    assert (result.run_directory / "extra" / "foreign.json").read_text() == "preserve"


@pytest.mark.parametrize("stage", ["before_any_write", "during_raw_staging", "before_completion_marker", "before_atomic_rename"])
def test_crash_never_publishes_partial_run_or_deletes_staging(tmp_path: Path, stage: str) -> None:
    root = tmp_path / "evidence"
    def fail(at: str) -> None:
        if stage == at:
            raise RuntimeError("https://api.the-odds-api.com/?apiKey=" + SECRET)
    with pytest.raises(evidence.MLBOddsEvidenceError) as caught:
        write(root, failure_hook=fail)
    assert SECRET not in str(caught.value)
    assert not (root / "runs" / DAY.isoformat() / "run-a").exists()
    if stage == "before_any_write":
        assert not root.exists()
    else:
        staging = list((root / "runs" / DAY.isoformat()).glob(".run-a.staging-*"))
        assert len(staging) == 1
        assert (staging[0] / "COMPLETE").exists() is (stage == "before_atomic_rename")
        before = file_snapshot(staging[0])
        assert write(root).status == "COMPLETE"
        assert file_snapshot(staging[0]) == before
    assert all(SECRET not in path.read_text() for path in root.rglob("*") if path.is_file())


def test_short_write_never_publishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = evidence._write_bytes
    def truncated(path: Path, data: bytes) -> None:
        original(path, data[:-1])
    monkeypatch.setattr(evidence, "_write_bytes", truncated)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="EVIDENCE_WRITE_FAILED"):
        write(tmp_path)
    assert not (tmp_path / "runs" / DAY.isoformat() / "run-a").exists()


def test_writer_never_calls_delete_or_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("DELETION_FORBIDDEN")
    monkeypatch.setattr(Path, "unlink", forbidden)
    monkeypatch.setattr(Path, "rmdir", forbidden)
    monkeypatch.setattr(shutil, "rmtree", forbidden)
    assert write(tmp_path).status == "COMPLETE"
    assert write(tmp_path).status == "ALREADY_COMPLETE"
    with pytest.raises(evidence.MLBOddsEvidenceError, match="CONFLICT"):
        write(tmp_path, run_status="FAILED")


def test_failed_discovery_and_odds_are_distinct_persistable_observations(tmp_path: Path) -> None:
    discovery_raw = canonical([{"id": "event-a"}])
    discovery = exchange(request_id="discovery", kind="discovery", endpoint="/v4/sports/baseball_mlb/events", query=(("dateFormat", "iso"),), canonical_json=discovery_raw, canonical_json_sha256=hashlib.sha256(discovery_raw.encode()).hexdigest(), normalized_record_count=0)
    timeout = exchange(request_id="timeout", canonical_json=None, canonical_json_sha256=None, http_status=None, status="TIMEOUT", usage_headers=(), actual_reported_last_cost=None, actual_reported_used=None, actual_reported_remaining=None, normalized_record_count=0)
    http_error = exchange(request_id="http-error", http_status=500, status="HTTP_ERROR", normalized_record_count=0)
    result = write(tmp_path, exchanges=(discovery, timeout, http_error), run_status="PARTIAL")
    rows = [json.loads(line) for line in (result.run_directory / "requests.jsonl").read_text().splitlines()]
    assert [row["kind"] for row in rows] == ["discovery", "event_odds", "event_odds"]
    assert rows[1]["actual_reported_last_cost"] is None
    assert rows[1]["canonical_json_file"] is None
    assert rows[2]["http_status"] == 500
    assert json.loads((result.run_directory / "manifest.json").read_text())["run_status"] == "PARTIAL"


@pytest.mark.parametrize("component", ["outputs", "test_outputs", "mlb_hr", "mlb_hr_prospective", "mlb-prospective-runtime-123", "sealed_hr_evidence", "sealed", "hr_evidence", "hr_prospective_trial", "automation", "theoddsapi", "live_hr_snapshots"])
def test_legacy_and_sealed_roots_rejected_without_writes(tmp_path: Path, component: str) -> None:
    root = tmp_path / component / "new-ingestion"
    with pytest.raises(evidence.MLBOddsEvidenceError, match="PROTECTED"):
        write(root)
    assert not root.exists()


@pytest.mark.parametrize("root", ["", "relative-root", None, ".."])
def test_missing_or_relative_root_fails_closed(root: object) -> None:
    with pytest.raises(evidence.MLBOddsEvidenceError):
        evidence.validate_evidence_root(root)


@pytest.mark.parametrize("run_id", ["../outside", "a/b", "a\\b", "CON", "nul", "a.", "", "a:stream"])
def test_run_path_components_are_strict(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(evidence.MLBOddsEvidenceError):
        write(tmp_path, run_id=run_id)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mode,attributes", [(stat.S_IFLNK, 0), (stat.S_IFDIR, 0x400)])
def test_symlink_or_windows_reparse_ancestor_fails_before_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int, attributes: int) -> None:
    root = tmp_path / "linked" / "evidence"
    original = Path.lstat
    def lstat(path: Path, *args: object, **kwargs: object):
        if path == root.parent:
            return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="UNSAFE"):
        write(root)
    assert not root.exists()


def test_replayed_raw_reparse_file_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = write(tmp_path)
    raw = result.run_directory / "canonical" / "request-a.json"
    original = Path.lstat
    def lstat(path: Path, *args: object, **kwargs: object):
        if path == raw:
            return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="UNSAFE"):
        write(tmp_path)


@pytest.mark.parametrize("changes", [
    {"query": (("apiKey", SECRET),)},
    {"usage_headers": (("Authorization", SECRET),)},
    {"usage_headers": (("x-requests-last", SECRET),)},
    {"endpoint": "https://api.the-odds-api.com/?apiKey=" + SECRET},
    {"status": SECRET},
    {"query": (("regions", "us&apiKey=" + SECRET),)},
])
def test_unsanitized_request_metadata_is_rejected_without_leaking(changes: dict) -> None:
    with pytest.raises(evidence.MLBOddsEvidenceError) as caught:
        exchange(**changes)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("field", ["apiKey", "api_key", "Authorization", "cookies", "access_token", "password", "client_secret"])
def test_nested_secret_fields_are_rejected_in_raw_and_summary(tmp_path: Path, field: str) -> None:
    raw = canonical({"nested": [{field: SECRET}]})
    with pytest.raises(evidence.MLBOddsEvidenceError) as caught:
        exchange(canonical_json=raw, canonical_json_sha256=hashlib.sha256(raw.encode()).hexdigest())
    assert SECRET not in str(caught.value)
    with pytest.raises(evidence.MLBOddsEvidenceError) as caught:
        write(tmp_path, normalization_summary={"nested": [{field: SECRET}]})
    assert SECRET not in str(caught.value)
    assert list(tmp_path.iterdir()) == []


def test_sanitized_secret_removal_and_redaction_are_persistable(tmp_path: Path) -> None:
    raw = canonical({"message": "credential [REDACTED]", "nested": [{}]})
    clean = exchange(canonical_json=raw, canonical_json_sha256=hashlib.sha256(raw.encode()).hexdigest(), status="SECRET_REDACTED", normalized_record_count=0)
    result = write(tmp_path, exchanges=(clean,), run_status="FAILED")
    assert SECRET not in repr(result)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert SECRET not in path.read_text()
            assert "apiKey" not in path.read_text()


@pytest.mark.parametrize("raw,hash_value", [("{}", "0" * 64), ("{ }", hashlib.sha256(b"{ }").hexdigest()), ("NaN", "0" * 64), (None, "0" * 64)])
def test_raw_bytes_and_hash_must_match_strict_canonical_json(raw: str | None, hash_value: str) -> None:
    with pytest.raises(evidence.MLBOddsEvidenceError):
        exchange(canonical_json=raw, canonical_json_sha256=hash_value)


@pytest.mark.parametrize("changes", [{"declared_budget": -1}, {"declared_max_credit_cost": float("nan")}, {"actual_reported_used": -1}, {"diagnostic_count": True}, {"requested_at": datetime(2026, 9, 20)}, {"responded_at": NOW - timedelta(seconds=1)}])
def test_invalid_counts_and_timestamps_rejected(changes: dict) -> None:
    with pytest.raises(evidence.MLBOddsEvidenceError):
        exchange(**changes)


def test_missing_and_invalid_accounting_values_remain_explicit(tmp_path: Path) -> None:
    observation = exchange(usage_headers=(("x-requests-last", "[INVALID]"),), actual_reported_last_cost=None, actual_reported_used=None, actual_reported_remaining=None, status="ACCOUNTING_CONFLICT")
    result = write(tmp_path, exchanges=(observation,), run_status="ACCOUNTING_CONFLICT")
    row = json.loads((result.run_directory / "requests.jsonl").read_text())
    assert row["usage_headers"] == [["x-requests-last", "[INVALID]"]]
    assert row["actual_reported_last_cost"] is None


def test_duplicate_exchange_identity_fails_before_write(tmp_path: Path) -> None:
    with pytest.raises(evidence.MLBOddsEvidenceError, match="DUPLICATE"):
        write(tmp_path, exchanges=(exchange(), exchange()))
    assert list(tmp_path.iterdir()) == []


def test_concurrent_identical_publications_leave_one_complete_run(tmp_path: Path) -> None:
    barrier = Barrier(2)
    def coordinate(stage: str) -> None:
        if stage == "before_atomic_rename":
            barrier.wait(timeout=10)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: write(tmp_path, failure_hook=coordinate), range(2)))
    assert sorted(result.status for result in results) == ["ALREADY_COMPLETE", "COMPLETE"]
    assert write(tmp_path).status == "ALREADY_COMPLETE"
    assert len(list((tmp_path / "runs" / DAY.isoformat()).glob(".run-a.staging-*"))) == 1


def test_destination_created_before_publication_is_never_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "runs" / DAY.isoformat() / "run-a"
    def race(stage: str) -> None:
        if stage == "before_atomic_rename":
            target.mkdir()
    with pytest.raises(evidence.MLBOddsEvidenceError, match="CONFLICT"):
        write(tmp_path, failure_hook=race)
    assert target.is_dir()
    assert list(target.iterdir()) == []
    assert len(list(target.parent.glob(".run-a.staging-*"))) == 1


@pytest.mark.parametrize("root", [r"\\server\share\evidence", "//server/share/evidence", r"\\?\C:\evidence", r"\\.\C:\evidence"])
def test_unc_and_device_roots_rejected_without_filesystem_inspection(root: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("FILESYSTEM_PROBE_FORBIDDEN")
    monkeypatch.setattr(Path, "lstat", forbidden)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="UNSAFE"):
        evidence.validate_evidence_root(root)


@pytest.mark.parametrize("component", ["outputs.", "outputs ", "test_outputs...", "mlb_hr ", "artifact:stream", "NUL.txt", "CON"])
def test_windows_path_aliases_rejected_without_filesystem_inspection(tmp_path: Path, component: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("FILESYSTEM_PROBE_FORBIDDEN")
    monkeypatch.setattr(Path, "lstat", forbidden)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="UNSAFE"):
        evidence.validate_evidence_root(tmp_path / component / "ingestion")


def test_nonlocal_drive_is_rejected_before_filesystem_inspection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evidence, "_drive_is_local", lambda root: False)
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("FILESYSTEM_PROBE_FORBIDDEN")
    monkeypatch.setattr(Path, "lstat", forbidden)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="UNSAFE"):
        evidence.validate_evidence_root(tmp_path)


def test_destination_gate_is_read_only_and_blocks_reused_run_id(tmp_path: Path) -> None:
    root = tmp_path / "new-root"
    target = evidence.validate_evidence_destination(root, "run-a", DAY)
    assert target == root / "runs" / DAY.isoformat() / "run-a"
    assert not root.exists()
    write(root)
    before = file_snapshot(root)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="ALREADY_EXISTS"):
        evidence.validate_evidence_destination(root, "run-a", DAY)
    assert file_snapshot(root) == before


@pytest.mark.parametrize("part", ["runs", "runs/2026-09-20"])
def test_destination_gate_rejects_parent_file_collisions(tmp_path: Path, part: str) -> None:
    collision = tmp_path / part
    collision.parent.mkdir(exist_ok=True)
    collision.write_text("keep")
    with pytest.raises(evidence.MLBOddsEvidenceError):
        evidence.validate_evidence_destination(tmp_path, "run-a", DAY)
    assert collision.read_text() == "keep"


def test_destination_gate_rejects_reserved_run_id(tmp_path: Path) -> None:
    with pytest.raises(evidence.MLBOddsEvidenceError, match="INVALID_PATH_COMPONENT"):
        evidence.validate_evidence_destination(tmp_path, "NUL", DAY)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("key", ["apiKey", "api_key", "cookies", "client_secret", "session_id", "refreshToken", "secret_key"])
def test_shared_sensitive_field_predicate(key: str) -> None:
    assert evidence.is_sensitive_evidence_key(key)
    assert not evidence.is_sensitive_evidence_key("key")
    assert not evidence.is_sensitive_evidence_key("bookmakers")


def test_redaction_provenance_survives_separate_accounting_failure(tmp_path: Path) -> None:
    observation = exchange(status="ACCOUNTING_CONFLICT", raw_body_redacted=True)
    result = write(tmp_path, exchanges=(observation,), run_status="ACCOUNTING_CONFLICT")
    row = json.loads((result.run_directory / "requests.jsonl").read_text())
    manifest = json.loads((result.run_directory / "manifest.json").read_text())
    assert row["raw_body_redacted"] is True
    assert manifest["requests"][0]["raw_body_redacted"] is True
    assert row["status"] == "ACCOUNTING_CONFLICT"
    with pytest.raises(evidence.MLBOddsEvidenceError, match="INVALID_REDACTION_FLAG"):
        exchange(raw_body_redacted=1)


@pytest.mark.parametrize("value", ["0", "60", "[INVALID]"])
def test_safe_retry_after_preserved_without_retry(tmp_path: Path, value: str) -> None:
    observation = exchange(http_status=429, status="RATE_LIMITED", usage_headers=(("retry-after", value),))
    result = write(tmp_path, exchanges=(observation,), run_status="FAILED")
    row = json.loads((result.run_directory / "requests.jsonl").read_text())
    assert row["usage_headers"] == [["retry-after", value]]


@pytest.mark.parametrize("value", [SECRET, "-1", "1.5", "Monday, 21 September 2026"])
def test_retry_after_untrusted_text_not_persisted(value: str) -> None:
    with pytest.raises(evidence.MLBOddsEvidenceError) as caught:
        exchange(usage_headers=(("retry-after", value),))
    assert SECRET not in str(caught.value)


def test_windows_short_name_alias_rejected_before_filesystem_inspection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("FILESYSTEM_PROBE_FORBIDDEN")
    monkeypatch.setattr(Path, "lstat", forbidden)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="UNSAFE"):
        evidence.validate_evidence_root(tmp_path / "THEODD~1" / "LIVE_H~1" / "ingestion")


def claim(root: Path, **changes: object) -> evidence.MLBOddsRunClaim:
    values = {"output_root": root, "run_id": "run-a", "operating_date": DAY,
              "plan_identity": {"provider": "the_odds_api", "research_only": True,
                                "max_http_requests": 2, "declared_credit_budget": 2,
                                "requested_markets": ["batter_hits"],
                                "plan": {"date": DAY.isoformat(), "event_ids": ["event-a"]}}}
    values.update(changes)
    return evidence.acquire_ingestion_claim(**values)


def test_claim_is_exclusive_durable_immutable_and_binds_declared_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    synced = []
    original = evidence.os.fsync
    def fsync(descriptor: int) -> None:
        original(descriptor)
        synced.append(descriptor)
    monkeypatch.setattr(evidence.os, "fsync", fsync)
    reservation = claim(tmp_path)
    document = json.loads(reservation.claim_path.read_bytes())
    assert synced
    assert document["run_id"] == "run-a"
    assert document["operating_date"] == DAY.isoformat()
    assert document["provider"] == "the_odds_api"
    assert document["research_only"] is True
    assert document["max_http_requests"] == document["declared_credit_budget"] == 2
    assert document["requested_markets"] == ["batter_hits"]
    assert hashlib.sha256(reservation.plan_identity_json.encode()).hexdigest() == document["request_plan_sha256"]
    assert hashlib.sha256(reservation.claim_path.read_bytes()).hexdigest() == reservation.claim_sha256
    evidence.validate_ingestion_claim(reservation)
    with pytest.raises(FrozenInstanceError):
        reservation.run_id = "other"
    before = file_snapshot(tmp_path)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="CLAIM_FAILED"):
        claim(tmp_path)
    assert file_snapshot(tmp_path) == before


@pytest.mark.parametrize("name", ["run-a", ".run-a.claim.json", ".run-a.staging", ".run-a.staging-legacy", "run-a.conflict", ".run-a.permit-issuer.json"])
def test_claim_rejects_stale_and_conflicting_material_without_cleanup(tmp_path: Path, name: str) -> None:
    parent = tmp_path / "runs" / DAY.isoformat()
    parent.mkdir(parents=True)
    (parent / name).write_bytes(b"preserve evidence")
    before = file_snapshot(tmp_path)
    with pytest.raises(evidence.MLBOddsEvidenceError):
        claim(tmp_path)
    assert file_snapshot(tmp_path) == before


def test_claim_creation_failure_is_fixed_and_retained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(path: Path, data: bytes) -> None:
        raise PermissionError(SECRET)
    monkeypatch.setattr(evidence, "_write_bytes", fail)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="^EVIDENCE_CLAIM_FAILED$") as caught:
        claim(tmp_path)
    assert SECRET not in repr(caught.value)
    assert not list(tmp_path.rglob("*.json"))


def test_two_simultaneous_claims_have_exactly_one_winner(tmp_path: Path) -> None:
    barrier = Barrier(2)
    def acquire(_: int):
        barrier.wait(timeout=10)
        try:
            return claim(tmp_path)
        except evidence.MLBOddsEvidenceError:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(acquire, range(2)))
    assert sum(item is not None for item in results) == 1
    winner = next(item for item in results if item is not None)
    evidence.validate_ingestion_claim(winner)


def test_stage_is_durable_before_return_and_final_publication_binds_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reservation = claim(tmp_path)
    exact = b'{ "bookmakers": [], "id": "event-a" }'
    observation = exchange(received_body=exact, received_body_sha256=hashlib.sha256(exact).hexdigest())
    written = []
    original = evidence._write_bytes
    def record(path: Path, data: bytes) -> None:
        original(path, data)
        written.append(path.relative_to(reservation.staging_directory).as_posix())
    monkeypatch.setattr(evidence, "_write_bytes", record)
    evidence.stage_ingestion_exchange(reservation, observation)
    assert written == ["canonical/request-a.json", "received/request-a.body", "exchanges/request-a.json"]
    assert not (reservation.staging_directory / "COMPLETE").exists()
    evidence.validate_staged_ingestion_exchange(reservation, observation)
    result = write(tmp_path, exchanges=(observation,), claim=reservation)
    manifest = json.loads((result.run_directory / "manifest.json").read_bytes())
    assert manifest["claim_sha256"] == reservation.claim_sha256
    assert manifest["request_plan_sha256"] == reservation.plan_identity_sha256
    assert reservation.claim_path.is_file()
    assert not reservation.staging_directory.exists()
    row = manifest["requests"][0]
    assert (result.run_directory / row["received_body_file"]).read_bytes() == exact
    assert row["received_body_sha256"] != row["canonical_json_sha256"]


def test_stage_failure_keeps_preceding_observation_and_partial_next_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reservation = claim(tmp_path)
    evidence.stage_ingestion_exchange(reservation, exchange())
    original = evidence._write_bytes
    def fail(path: Path, data: bytes) -> None:
        if path.name == "request-b.json" and path.parent.name == "exchanges":
            raise OSError(SECRET)
        original(path, data)
    monkeypatch.setattr(evidence, "_write_bytes", fail)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="^EVIDENCE_STAGE_FAILED$"):
        evidence.stage_ingestion_exchange(reservation, exchange(request_id="request-b"))
    evidence.validate_staged_ingestion_exchange(reservation, exchange())
    assert (reservation.staging_directory / "canonical/request-b.json").is_file()
    assert not (reservation.staging_directory / "exchanges/request-b.json").exists()
    assert not reservation.run_directory.exists()
    assert not (reservation.staging_directory / "COMPLETE").exists()
    with pytest.raises(evidence.MLBOddsEvidenceError):
        claim(tmp_path)


def test_staged_observation_cannot_be_overwritten_or_silently_published_if_tampered(tmp_path: Path) -> None:
    reservation = claim(tmp_path)
    evidence.stage_ingestion_exchange(reservation, exchange())
    before = file_snapshot(tmp_path)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="STAGE_FAILED"):
        evidence.stage_ingestion_exchange(reservation, exchange())
    assert file_snapshot(tmp_path) == before
    (reservation.staging_directory / "canonical/request-a.json").write_bytes(b"tampered")
    with pytest.raises(evidence.MLBOddsEvidenceError, match="INTEGRITY"):
        write(tmp_path, claim=reservation)
    assert not reservation.run_directory.exists()


def test_claim_tamper_and_claim_from_another_run_fail_closed(tmp_path: Path) -> None:
    reservation = claim(tmp_path)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="INVALID_RUN_CLAIM"):
        write(tmp_path, run_id="other-run", exchanges=(), claim=reservation)
    reservation.claim_path.write_bytes(b"{}")
    with pytest.raises(evidence.MLBOddsEvidenceError, match="INVALID_RUN_CLAIM"):
        evidence.stage_ingestion_exchange(reservation, exchange())


def test_permit_issuer_reservation_is_durable_and_cannot_be_reissued(tmp_path: Path) -> None:
    reservation = claim(tmp_path)
    evidence.reserve_execution_permit_issuer(reservation)
    marker = reservation.run_directory.with_name(".run-a.permit-issuer.json")
    assert json.loads(marker.read_bytes())["claim_sha256"] == reservation.claim_sha256
    before = file_snapshot(tmp_path)
    with pytest.raises(evidence.MLBOddsEvidenceError, match="ALREADY_RESERVED"):
        evidence.reserve_execution_permit_issuer(reservation)
    assert file_snapshot(tmp_path) == before


def test_exact_body_hash_is_distinct_from_semantic_json_hash(tmp_path: Path) -> None:
    bodies = (b'{"a":1,"b":2}', b'{\n  "b": 2,\n  "a": 1\n}')
    observations = tuple(exchange(request_id=f"request-{index}", canonical_json='{"a":1,"b":2}', canonical_json_sha256=hashlib.sha256(b'{"a":1,"b":2}').hexdigest(), received_body=body, received_body_sha256=hashlib.sha256(body).hexdigest()) for index, body in enumerate(bodies))
    result = write(tmp_path, exchanges=observations)
    rows = json.loads((result.run_directory / "manifest.json").read_bytes())["requests"]
    assert rows[0]["received_body_sha256"] != rows[1]["received_body_sha256"]
    assert rows[0]["canonical_json_sha256"] == rows[1]["canonical_json_sha256"]
    for row, body in zip(rows, bodies):
        assert (result.run_directory / row["received_body_file"]).read_bytes() == body


def test_secret_redaction_withholds_exact_bytes_and_digest(tmp_path: Path) -> None:
    observation = exchange(raw_body_redacted=True)
    result = write(tmp_path, exchanges=(observation,))
    row = json.loads((result.run_directory / "requests.jsonl").read_bytes())
    assert row["raw_body_redacted"] is True
    assert row["received_body_file"] is row["received_body_sha256"] is None
    assert row["canonical_json_file"] is not None
    assert not (result.run_directory / "received").exists()
    raw = observation.canonical_json.encode()
    with pytest.raises(evidence.MLBOddsEvidenceError, match="UNSAFE_RECEIVED_BODY"):
        exchange(raw_body_redacted=True, received_body=raw, received_body_sha256=hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize("value", ["https://provider.invalid/?token=secret", "https://provider.invalid/?api_key=secret", "https://provider.invalid/?api%4Bey=secret", "https://user:secret@provider.invalid/a", "password=secret", "secret: abc", "refreshToken=abc"])
def test_sensitive_text_predicate_rejects_secret_bearing_urls(value: str) -> None:
    assert evidence.is_sensitive_evidence_text(value)
    raw = canonical({"message": value})
    with pytest.raises(evidence.MLBOddsEvidenceError):
        exchange(canonical_json=raw, canonical_json_sha256=hashlib.sha256(raw.encode()).hexdigest())


def test_received_duplicate_json_keys_cannot_hide_earlier_secret_material() -> None:
    exact = b'{"message":"https://provider.invalid/?token=secret","message":"safe"}'
    semantic = canonical({"message": "safe"})
    with pytest.raises(evidence.MLBOddsEvidenceError, match="UNSAFE_RECEIVED_BODY"):
        exchange(canonical_json=semantic, canonical_json_sha256=hashlib.sha256(semantic.encode()).hexdigest(), received_body=exact, received_body_sha256=hashlib.sha256(exact).hexdigest())


def test_sensitive_url_in_json_key_cannot_be_persisted(tmp_path: Path) -> None:
    unsafe = {"https://provider.invalid/?api_key=secret": "value"}
    raw = canonical(unsafe)
    with pytest.raises(evidence.MLBOddsEvidenceError):
        exchange(canonical_json=raw, canonical_json_sha256=hashlib.sha256(raw.encode()).hexdigest())
    with pytest.raises(evidence.MLBOddsEvidenceError, match="UNSANITIZED_EVIDENCE"):
        write(tmp_path, normalization_summary=unsafe)
    assert not list(tmp_path.iterdir())
