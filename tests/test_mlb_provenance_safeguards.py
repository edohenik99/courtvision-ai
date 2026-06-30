from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from courtvision.sports.mlb.data_manifest import (
    compute_file_sha256,
    verify_source_manifest_file,
    verify_source_manifest_payload,
)
import scripts.mlb_build_hr_local_dataset as cli


CREATED_AT = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc).isoformat()


def _payload(source: Path, *, classification: str = "real") -> dict[str, object]:
    return {
        "manifest_version": "test-provenance-v1",
        "mode": "historical_dry_run",
        "created_at": CREATED_AT,
        "source_classification": classification,
        "dataset_date_range_start": "2025-04-01",
        "dataset_date_range_end": "2025-04-07",
        "approval_status": "not_approved",
        "eligible_for_betting": False,
        "kelly_eligible": False,
        "sources": [
            {
                "source_name": "statcast",
                "provider_label": "baseball_savant_statcast",
                "source_type": "local_file",
                "source_classification": classification,
                "path": str(source.resolve()),
                "sha256": compute_file_sha256(source),
                "byte_size": source.stat().st_size,
                "parsed_row_count": 1,
                "created_at": CREATED_AT,
                "date_range_start": "2025-04-01",
                "date_range_end": "2025-04-07",
            }
        ],
    }


def test_manifest_verification_accepts_matching_immutable_source(tmp_path: Path) -> None:
    source = tmp_path / "statcast.csv"
    source.write_text("game_date,game_pk\n2025-04-01,1\n", encoding="utf-8")

    result = verify_source_manifest_payload(_payload(source))

    assert result.is_valid
    assert result.errors == ()


def test_manifest_verification_detects_hash_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "statcast.csv"
    source.write_text("original\n", encoding="utf-8")
    payload = _payload(source)
    source.write_text("drifted!\n", encoding="utf-8")
    payload["sources"][0]["byte_size"] = source.stat().st_size  # type: ignore[index]

    result = verify_source_manifest_payload(payload)

    assert not result.is_valid
    assert any("SHA-256 mismatch" in error for error in result.errors)


def test_manifest_verification_detects_size_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "statcast.csv"
    source.write_text("original\n", encoding="utf-8")
    payload = _payload(source)
    payload["sources"][0]["byte_size"] = source.stat().st_size + 1  # type: ignore[index]

    result = verify_source_manifest_payload(payload)

    assert not result.is_valid
    assert any("source file size mismatch" in error for error in result.errors)


def test_manifest_verification_detects_missing_raw_source(tmp_path: Path) -> None:
    source = tmp_path / "statcast.csv"
    source.write_text("original\n", encoding="utf-8")
    payload = _payload(source)
    source.unlink()

    result = verify_source_manifest_payload(payload)

    assert not result.is_valid
    assert any("source file is missing" in error for error in result.errors)


def test_sample_manifest_cannot_claim_production_readiness(tmp_path: Path) -> None:
    source = tmp_path / "fixture.csv"
    source.write_text("fixture\n", encoding="utf-8")
    payload = _payload(source, classification="fixture")
    payload["approval_status"] = "production_approved"
    payload["eligible_for_betting"] = True

    result = verify_source_manifest_payload(payload)

    assert not result.is_valid
    assert "fixture/sample sources cannot be treated as production-ready" in result.errors


def test_read_only_manifest_cli_detects_drift_without_mutating_operational_dirs(
    tmp_path: Path,
    capsys,
) -> None:
    source = tmp_path / "raw" / "statcast.csv"
    source.parent.mkdir()
    source.write_text("original\n", encoding="utf-8")
    manifest = tmp_path / "source_manifest.json"
    manifest.write_text(json.dumps(_payload(source)), encoding="utf-8")

    sentinels = []
    for relative in ("outputs/sentinel.txt", "data/history/sentinel.txt", "runtime/sentinel.txt"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
        sentinels.append(path)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in sentinels}

    assert cli.main(["--verify-source-manifest", str(manifest)]) == 0
    captured = capsys.readouterr()
    assert "source_manifest_status: valid" in captured.out
    assert before == {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in sentinels
    }

    source.write_text("changed bytes\n", encoding="utf-8")
    assert cli.main(["--verify-source-manifest", str(manifest)]) == 2
    captured = capsys.readouterr()
    assert "source file SHA-256 mismatch" in captured.err
    assert before == {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in sentinels
    }


def test_verify_source_manifest_file_reports_missing_manifest(tmp_path: Path) -> None:
    result = verify_source_manifest_file(tmp_path / "missing.json")

    assert not result.is_valid
    assert "source manifest file is missing" in result.errors[0]
