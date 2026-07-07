from __future__ import annotations

import csv
from pathlib import Path
import socket
import urllib.request

from scripts import init_evidence_daily_manifest as initializer


EXPECTED_COLUMNS = [
    "trial_id",
    "run_date",
    "prediction_date",
    "code_sha",
    "config_hash",
    "run_status",
    "provider_attempted",
    "provider_used",
    "fallback_used",
    "released_recommendation_count",
    "source_board_path",
    "source_board_sha256",
    "elite_board_path",
    "elite_board_sha256",
    "kelly_artifact_path",
    "kelly_artifact_sha256",
    "operator_card_path",
    "operator_card_sha256",
    "completion_audit_path",
    "completion_audit_sha256",
    "artifact_manifest_path",
    "artifact_manifest_sha256",
    "run_log_path",
    "run_log_sha256",
    "validation_log_path",
    "validation_log_sha256",
    "grading_log_path",
    "grading_log_sha256",
    "failure_reason",
    "manual_intervention",
    "notes",
    "created_at",
]


def _read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle))


def test_manifest_is_created_with_exact_expected_columns(tmp_path: Path) -> None:
    manifest_path = tmp_path / "data" / "history" / "evidence_daily_manifest.csv"

    assert initializer.initialize_evidence_daily_manifest(manifest_path) is True

    assert _read_header(manifest_path) == EXPECTED_COLUMNS
    assert manifest_path.read_text(encoding="utf-8").count("\n") == 1


def test_parent_directory_is_created(tmp_path: Path) -> None:
    manifest_path = tmp_path / "missing" / "nested" / "evidence_daily_manifest.csv"
    assert not manifest_path.parent.exists()

    initializer.initialize_evidence_daily_manifest(manifest_path)

    assert manifest_path.parent.is_dir()
    assert manifest_path.is_file()


def test_existing_valid_manifest_is_not_overwritten(tmp_path: Path) -> None:
    manifest_path = tmp_path / "evidence_daily_manifest.csv"
    original = ",".join(EXPECTED_COLUMNS) + "\ntrial-1,existing-row\n"
    manifest_path.write_text(original, encoding="utf-8")
    before = manifest_path.read_bytes()

    assert initializer.initialize_evidence_daily_manifest(manifest_path) is False

    assert manifest_path.read_bytes() == before


def test_existing_invalid_schema_fails_safely(tmp_path: Path, capsys) -> None:
    manifest_path = tmp_path / "evidence_daily_manifest.csv"
    manifest_path.write_text("wrong,columns\nsentinel,data\n", encoding="utf-8")
    before = manifest_path.read_bytes()

    assert initializer.main(manifest_path) != 0

    assert manifest_path.read_bytes() == before
    output = capsys.readouterr().out
    assert str(manifest_path.resolve()) in output
    assert "invalid schema" in output


def test_script_is_offline_safe(tmp_path: Path, monkeypatch) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("daily manifest initialization must not use the network")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)

    manifest_path = tmp_path / "evidence_daily_manifest.csv"
    assert initializer.main(manifest_path) == 0
    assert manifest_path.is_file()


def test_script_exits_successfully_when_manifest_already_exists(
    tmp_path: Path, capsys
) -> None:
    manifest_path = tmp_path / "evidence_daily_manifest.csv"
    assert initializer.main(manifest_path) == 0
    first_bytes = manifest_path.read_bytes()
    capsys.readouterr()

    assert initializer.main(manifest_path) == 0

    assert manifest_path.read_bytes() == first_bytes
    output = capsys.readouterr().out
    assert str(manifest_path.resolve()) in output
    assert "already existed (schema valid)" in output
