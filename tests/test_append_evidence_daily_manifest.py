from __future__ import annotations

import csv
from datetime import datetime
import hashlib
from pathlib import Path
import socket
import urllib.request

import pytest

from scripts import append_evidence_daily_manifest as appender
from scripts.init_evidence_daily_manifest import MANIFEST_COLUMNS


def _create_manifest(path: Path, rows: list[dict[str, str]] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows or [])


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _append(manifest_path: Path, repo_root: Path, **overrides) -> dict[str, str]:
    kwargs = {
        "trial_id": "nba-forward-2026-01",
        "prediction_date": "2026-07-07",
        "run_date": "2026-07-07",
        "run_status": "complete",
        "config_hash": "a" * 64,
        "code_sha": "b" * 40,
        "manifest_path": manifest_path,
        "repo_root": repo_root,
    }
    kwargs.update(overrides)
    return appender.append_evidence_daily_manifest(**kwargs)


def test_valid_row_appends_with_exact_schema(tmp_path: Path) -> None:
    manifest_path = tmp_path / "data" / "history" / "evidence_daily_manifest.csv"
    _create_manifest(manifest_path)

    appended = _append(
        manifest_path,
        tmp_path,
        provider_attempted="primary|fallback",
        provider_used="fallback",
        fallback_used=True,
        released_recommendation_count=2,
        manual_intervention=False,
        notes="daily custody row",
    )

    header, rows = _read_rows(manifest_path)
    assert header == list(MANIFEST_COLUMNS)
    assert len(rows) == 1
    assert rows[0] == appended
    assert rows[0]["fallback_used"] == "true"
    assert rows[0]["released_recommendation_count"] == "2"
    assert rows[0]["manual_intervention"] == "false"
    assert datetime.fromisoformat(rows[0]["created_at"]).tzinfo is not None


def test_invalid_run_status_fails_without_appending(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.csv"
    _create_manifest(manifest_path)
    before = manifest_path.read_bytes()

    with pytest.raises(appender.EvidenceManifestAppendError, match="run_status"):
        _append(manifest_path, tmp_path, run_status="partial_success")

    assert manifest_path.read_bytes() == before


def test_missing_manifest_fails(tmp_path: Path) -> None:
    manifest_path = tmp_path / "missing.csv"

    with pytest.raises(appender.EvidenceManifestAppendError, match="does not exist"):
        _append(manifest_path, tmp_path)

    assert not manifest_path.exists()


def test_invalid_manifest_schema_fails_without_modifying_file(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.csv"
    manifest_path.write_text("wrong,columns\nsentinel,row\n", encoding="utf-8")
    before = manifest_path.read_bytes()

    with pytest.raises(appender.EvidenceManifestAppendError, match="wrong schema"):
        _append(manifest_path, tmp_path)

    assert manifest_path.read_bytes() == before


def test_artifact_sha256_is_computed(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.csv"
    artifact_path = tmp_path / "outputs" / "source.csv"
    artifact_path.parent.mkdir()
    artifact_path.write_bytes(b"frozen evidence\n")
    _create_manifest(manifest_path)

    _append(
        manifest_path,
        tmp_path,
        source_board_path="outputs/source.csv",
    )

    _, rows = _read_rows(manifest_path)
    assert rows[0]["source_board_path"] == "outputs/source.csv"
    assert rows[0]["source_board_sha256"] == hashlib.sha256(
        b"frozen evidence\n"
    ).hexdigest()


def test_missing_artifact_path_fails_by_default(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.csv"
    _create_manifest(manifest_path)
    before = manifest_path.read_bytes()

    with pytest.raises(appender.EvidenceManifestAppendError, match="does not exist"):
        _append(
            manifest_path,
            tmp_path,
            source_board_path="outputs/missing.csv",
        )

    assert manifest_path.read_bytes() == before


def test_missing_artifact_path_can_be_allowed_explicitly(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.csv"
    _create_manifest(manifest_path)

    _append(
        manifest_path,
        tmp_path,
        source_board_path="outputs/missing.csv",
        allow_missing_artifacts=True,
    )

    _, rows = _read_rows(manifest_path)
    assert len(rows) == 1
    assert rows[0]["source_board_path"] == ""
    assert rows[0]["source_board_sha256"] == ""
    assert "source_board_path=outputs/missing.csv" in rows[0]["notes"]


def test_script_is_offline_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("daily manifest append must not use the network")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    manifest_path = tmp_path / "manifest.csv"
    _create_manifest(manifest_path)

    _append(manifest_path, tmp_path)

    assert len(_read_rows(manifest_path)[1]) == 1


def test_existing_rows_are_preserved(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.csv"
    existing = {column: "" for column in MANIFEST_COLUMNS}
    existing.update(
        {
            "trial_id": "existing-trial",
            "run_date": "2026-07-06",
            "prediction_date": "2026-07-06",
            "code_sha": "old-sha",
            "config_hash": "old-hash",
            "run_status": "no_slate",
            "created_at": "2026-07-06T12:00:00-04:00",
        }
    )
    _create_manifest(manifest_path, [existing])

    _append(manifest_path, tmp_path)

    _, rows = _read_rows(manifest_path)
    assert len(rows) == 2
    assert rows[0] == existing
    assert rows[1]["trial_id"] == "nba-forward-2026-01"
