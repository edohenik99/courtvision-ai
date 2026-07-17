from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import threading
from unittest.mock import patch

import pytest

from courtvision.sports.nba.player_points_assembly import (
    NBAPlayerPointsAssemblyBatchResult,
    assemble_nba_player_points_batch,
)
from courtvision.sports.nba.player_points_evidence import (
    NBA_PLAYER_POINTS_EVIDENCE_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_LEDGER_SCHEMA_VERSION,
    NBAPlayerPointsEvidenceError,
    NBAPlayerPointsEvidenceWriterConfig,
    evidence_manifest_schema_definition,
    ledger_record_schema_definition,
    verify_nba_player_points_evidence,
    write_nba_player_points_evidence,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "nba" / "player_points"
ASSEMBLY_CASES_FIXTURE = FIXTURE_ROOT / "assembly_cases.json"
EVIDENCE_MODULE = (
    Path(__file__).resolve().parents[1]
    / "courtvision"
    / "sports"
    / "nba"
    / "player_points_evidence.py"
)
WRITER_TIMESTAMP = "2026-06-05T18:10:00Z"
CONFIG = NBAPlayerPointsEvidenceWriterConfig()


def _load_fixture() -> dict[str, object]:
    return json.loads(ASSEMBLY_CASES_FIXTURE.read_text(encoding="utf-8"))


def _deep_merge(base: object, overrides: object) -> object:
    if isinstance(base, dict) and isinstance(overrides, dict):
        merged = deepcopy(base)
        for key, value in overrides.items():
            merged[key] = _deep_merge(merged.get(key), value)
        return merged
    return deepcopy(overrides)


def _case_payload(
    case_id: str,
    extra_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    fixture = _load_fixture()
    base = fixture["base_case"]
    cases = {case["case_id"]: case for case in fixture["cases"]}
    payload = _deep_merge(base, cases[case_id].get("overrides", {}))
    if extra_overrides:
        payload = _deep_merge(payload, extra_overrides)
    return payload


def _alternate_probability_payload() -> dict[str, object]:
    return _case_payload(
        "valid_probability_research",
        {
            "market": {
                "sportsbook": "FanDuel",
                "american_odds": -105,
                "decimal_odds": 1.952381,
                "implied_probability": 0.512195,
                "market_source_id": "the-odds-api:odds_evt_20260605_okc_ind:fanduel:player_points:sga:31.5:-105",
                "market_source_hash": "3333333333333333333333333333333333333333333333333333333333333333",
            }
        },
    )


def _batch_payloads(*payloads: dict[str, object]) -> NBAPlayerPointsAssemblyBatchResult:
    return assemble_nba_player_points_batch(
        payloads,
        manifest_created_at_utc="2026-06-05T18:06:00Z",
    )


def _batch(*case_ids: str) -> NBAPlayerPointsAssemblyBatchResult:
    return _batch_payloads(*(_case_payload(case_id) for case_id in case_ids))


def _batch_with_run_id(
    run_id: str,
    case_id: str = "valid_projection_no_probabilities",
) -> NBAPlayerPointsAssemblyBatchResult:
    return _batch_payloads(
        _case_payload(
            case_id,
            {
                "provenance": {
                    "prediction_run_id": run_id,
                }
            },
        )
    )


def _fixture_commit(result: NBAPlayerPointsAssemblyBatchResult) -> str:
    return result.source_manifest_preview.repository_commit_sha


def _write(
    tmp_path: Path,
    result: NBAPlayerPointsAssemblyBatchResult,
    *,
    failure_hook=None,
    writer_timestamp_utc: str = WRITER_TIMESTAMP,
):
    return write_nba_player_points_evidence(
        result,
        result.source_manifest_preview,
        tmp_path,
        CONFIG,
        repository_commit_sha=_fixture_commit(result),
        writer_timestamp_utc=writer_timestamp_utc,
        failure_hook=failure_hook,
    )


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(root: Path) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        sorted(
            (str(path.relative_to(root)), _sha256(path), path.stat().st_size)
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def _rewrite_manifest_and_marker(run_directory: Path, manifest: dict[str, object]) -> None:
    manifest_path = run_directory / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    marker_path = run_directory / "COMPLETE"
    marker = _read_json(marker_path)
    marker["run_manifest_hash"] = _sha256(manifest_path)
    marker_path.write_text(json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8")


def _fail_at(stage_name: str):
    def hook(stage: str) -> None:
        if stage == stage_name:
            raise RuntimeError(stage_name)

    return hook


class _FakeIsoDate:
    def __init__(self, text: str) -> None:
        self._text = text

    def isoformat(self) -> str:
        return self._text

    def __str__(self) -> str:
        return self._text


def _corrupt_run_identity(
    result: NBAPlayerPointsAssemblyBatchResult,
    value: str,
) -> NBAPlayerPointsAssemblyBatchResult:
    object.__setattr__(result.source_manifest_preview, "prediction_run_id", value)
    for row in result.rows:
        object.__setattr__(row, "prediction_run_id", value)
    return result


def _corrupt_operating_date(
    result: NBAPlayerPointsAssemblyBatchResult,
    value: str,
) -> NBAPlayerPointsAssemblyBatchResult:
    fake = _FakeIsoDate(value)
    object.__setattr__(result.source_manifest_preview, "operating_date", fake)
    for row in result.rows:
        object.__setattr__(row, "operating_date", fake)
    return result


def _ledger_rows(tmp_path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in _ledger_segment_paths(tmp_path):
        rows.extend(_read_jsonl(path))
    return rows


def _evidence_root(tmp_path: Path) -> Path:
    return tmp_path / "nba_player_points_evidence"


def _ledger_segments_root(tmp_path: Path) -> Path:
    return _evidence_root(tmp_path) / "ledgers" / "segments"


def _ledger_segment_paths(tmp_path: Path) -> list[Path]:
    root = _ledger_segments_root(tmp_path)
    if not root.exists():
        return []
    return sorted(root.glob("*/*/prediction_ledger.jsonl"))


def _ledger_segment_path(tmp_path: Path, write_result) -> Path:
    return (
        _ledger_segments_root(tmp_path)
        / str(write_result.run_manifest["operating_date"])
        / str(write_result.run_manifest["prediction_run_id"])
        / "prediction_ledger.jsonl"
    )


def _ledger_snapshot(tmp_path: Path) -> tuple[tuple[str, bytes], ...]:
    root = _evidence_root(tmp_path)
    return tuple(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in _ledger_segment_paths(tmp_path)
    )


def test_schema_definitions_document_append_only_contract() -> None:
    manifest_schema = evidence_manifest_schema_definition()
    ledger_schema = ledger_record_schema_definition()

    assert manifest_schema["evidence_schema_version"] == NBA_PLAYER_POINTS_EVIDENCE_SCHEMA_VERSION
    assert "already_complete" in manifest_schema["completion_statuses"]
    assert "run_content_hash" in manifest_schema["required_fields"]
    assert ledger_schema["ledger_schema_version"] == NBA_PLAYER_POINTS_LEDGER_SCHEMA_VERSION
    assert "ledger_record_hash" in ledger_schema["required_fields"]
    assert "settlement" in ledger_schema["forbidden_fields"]
    assert "closing" in ledger_schema["forbidden_fields"]


def test_successful_evidence_write_creates_versioned_layout(tmp_path: Path) -> None:
    result = _write(tmp_path, _batch("valid_projection_no_probabilities"))

    assert result.completion_status == "complete"
    assert result.run_directory.exists()
    assert (result.run_directory / "run_manifest.json").exists()
    assert (result.run_directory / "prediction_rows.jsonl").exists()
    assert (result.run_directory / "excluded_rows.jsonl").exists()
    assert (result.run_directory / "quarantined_rows.jsonl").exists()
    assert (result.run_directory / "conflicting_rows.jsonl").exists()
    assert (result.run_directory / "duplicate_diagnostics.json").exists()
    assert (result.run_directory / "integrity_report.json").exists()
    assert (result.run_directory / "source_manifest_preview.json").exists()
    assert (result.run_directory / "COMPLETE").exists()
    assert result.ledger_path.exists()


def test_manifest_contents_are_complete_and_hashed(tmp_path: Path) -> None:
    write_result = _write(tmp_path, _batch("valid_projection_no_probabilities"))
    manifest = _read_json(write_result.run_directory / "run_manifest.json")
    marker = _read_json(write_result.run_directory / "COMPLETE")

    assert manifest["evidence_schema_version"] == NBA_PLAYER_POINTS_EVIDENCE_SCHEMA_VERSION
    assert manifest["ledger_schema_version"] == NBA_PLAYER_POINTS_LEDGER_SCHEMA_VERSION
    assert manifest["completion_status"] == "complete"
    assert manifest["created_at_utc"] == WRITER_TIMESTAMP
    assert manifest["completed_at_utc"] == WRITER_TIMESTAMP
    assert manifest["total_input_rows"] == 1
    assert manifest["eligible_projection_rows"] == 1
    assert manifest["ledger_append_count"] == 1
    assert marker["run_manifest_hash"] == _sha256(write_result.run_directory / "run_manifest.json")


def test_eligible_projection_row_appends_to_ledger(tmp_path: Path) -> None:
    _write(tmp_path, _batch("valid_projection_no_probabilities"))
    rows = _ledger_rows(tmp_path)

    assert len(rows) == 1
    assert rows[0]["probability_status"] == "unavailable"
    assert rows[0]["model_over_probability"] is None
    assert rows[0]["model_under_probability"] is None
    assert rows[0]["projected_minutes_low"] is None
    assert rows[0]["projection_timestamp_utc"] is None


def test_eligible_probability_row_appends_with_validated_probabilities(tmp_path: Path) -> None:
    result = _batch_payloads(_alternate_probability_payload())
    _write(tmp_path, result)
    row = _ledger_rows(tmp_path)[0]

    assert row["probability_status"] == "valid"
    assert row["model_over_probability"] == pytest.approx(0.56)
    assert row["model_under_probability"] == pytest.approx(0.44)
    assert row["probability_model_id"] == "nba-probability-validation-fixture-v1"


def test_projection_and_probability_rows_append_together(tmp_path: Path) -> None:
    result = _batch_payloads(
        _case_payload("valid_projection_no_probabilities"),
        _alternate_probability_payload(),
    )
    write_result = _write(tmp_path, result)
    rows = _ledger_rows(tmp_path)

    assert write_result.ledger_append_count == 2
    assert len(rows) == 2
    assert {row["probability_status"] for row in rows} == {"unavailable", "valid"}


def test_excluded_row_is_stored_but_not_ledgered(tmp_path: Path) -> None:
    write_result = _write(tmp_path, _batch("missing_projected_minutes"))

    assert _read_jsonl(write_result.run_directory / "excluded_rows.jsonl")
    assert _ledger_rows(tmp_path) == []
    assert _read_json(write_result.run_directory / "run_manifest.json")["excluded_rows"] == 1


def test_quarantined_row_is_stored_but_not_ledgered(tmp_path: Path) -> None:
    write_result = _write(tmp_path, _batch("target_game_actual_points_leak"))

    assert _read_jsonl(write_result.run_directory / "quarantined_rows.jsonl")
    assert _ledger_rows(tmp_path) == []
    assert _read_json(write_result.run_directory / "run_manifest.json")["quarantined_rows"] == 1


def test_conflicting_row_is_stored_but_not_ledgered(tmp_path: Path) -> None:
    write_result = _write(tmp_path, _batch("team_mismatch"))

    assert write_result.completion_status == "conflicting"
    assert _read_jsonl(write_result.run_directory / "conflicting_rows.jsonl")
    assert _ledger_rows(tmp_path) == []


def test_identical_within_batch_duplicate_collapses_idempotently(tmp_path: Path) -> None:
    write_result = _write(
        tmp_path,
        _batch("identical_duplicate_rows", "identical_duplicate_rows"),
    )
    diagnostics = _read_json(write_result.run_directory / "duplicate_diagnostics.json")

    assert write_result.completion_status == "complete"
    assert len(_ledger_rows(tmp_path)) == 1
    assert diagnostics["diagnostics"][0]["duplicate_status"] == "identical_collapsed"


def test_conflicting_within_batch_duplicate_publishes_conflicting_run(tmp_path: Path) -> None:
    write_result = _write(
        tmp_path,
        _batch("identical_duplicate_rows", "conflicting_duplicate_rows"),
    )

    assert write_result.completion_status == "conflicting"
    assert len(_read_jsonl(write_result.run_directory / "conflicting_rows.jsonl")) == 2
    assert _ledger_rows(tmp_path) == []


def test_identical_run_replay_returns_already_complete_without_rewriting_ledger(tmp_path: Path) -> None:
    result = _batch("valid_projection_no_probabilities")
    first = _write(tmp_path, result)
    ledger_before = _ledger_snapshot(tmp_path)

    second = _write(tmp_path, result)

    assert second.completion_status == "already_complete"
    assert second.ledger_append_count == 0
    assert _ledger_snapshot(tmp_path) == ledger_before


def test_conflicting_run_replay_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path, _batch("valid_projection_no_probabilities"))

    with pytest.raises(NBAPlayerPointsEvidenceError, match="different content"):
        _write(tmp_path, _batch("conflicting_duplicate_rows"))


def test_identical_ledger_replay_is_idempotent_when_run_directory_is_missing(tmp_path: Path) -> None:
    result = _batch("valid_projection_no_probabilities")
    _write(tmp_path, result)
    ledger_before = _ledger_snapshot(tmp_path)
    shutil.rmtree(tmp_path / "nba_player_points_evidence" / "runs")

    second = _write(tmp_path, result)

    assert second.completion_status == "complete"
    assert second.ledger_append_count == 0
    assert _ledger_snapshot(tmp_path) == ledger_before


def test_existing_corrupted_ledger_fails_closed_before_write(tmp_path: Path) -> None:
    ledger_path = (
        tmp_path
        / "nba_player_points_evidence"
        / "ledgers"
        / "prediction_ledger.jsonl"
    )
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(NBAPlayerPointsEvidenceError, match="unsupported legacy global ledger"):
        _write(tmp_path, _batch("valid_projection_no_probabilities"))


def test_prediction_id_verification_fails_closed(tmp_path: Path) -> None:
    result = _batch("valid_projection_no_probabilities")
    bad_row = replace(result.rows[0], prediction_id="bad-prediction-id")
    bad_result = NBAPlayerPointsAssemblyBatchResult(
        rows=(bad_row,),
        duplicate_diagnostics=result.duplicate_diagnostics,
        source_manifest_preview=result.source_manifest_preview,
        batch_summary_counts=result.batch_summary_counts,
    )

    with pytest.raises(NBAPlayerPointsEvidenceError, match="prediction_id"):
        _write(tmp_path, bad_result)


def test_source_manifest_hash_verification_fails_closed(tmp_path: Path) -> None:
    result = _batch("valid_projection_no_probabilities")
    bad_row = replace(result.rows[0], source_manifest_hash="0" * 64)
    bad_result = NBAPlayerPointsAssemblyBatchResult(
        rows=(bad_row,),
        duplicate_diagnostics=result.duplicate_diagnostics,
        source_manifest_preview=result.source_manifest_preview,
        batch_summary_counts=result.batch_summary_counts,
    )

    with pytest.raises(NBAPlayerPointsEvidenceError, match="source_manifest_hash"):
        _write(tmp_path, bad_result)


def test_ledger_record_hash_verifier_detects_corruption(tmp_path: Path) -> None:
    write_result = _write(tmp_path, _batch("valid_projection_no_probabilities"))
    ledger_path = _ledger_segment_path(tmp_path, write_result)
    row = _read_jsonl(ledger_path)[0]
    row["ledger_record_hash"] = "0" * 64
    ledger_path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    report = verify_nba_player_points_evidence(tmp_path, CONFIG)
    assert report.ok is False
    assert any("ledger_record_hash" in violation for violation in report.violations)


def test_run_content_hash_verifier_detects_manifest_corruption(tmp_path: Path) -> None:
    write_result = _write(tmp_path, _batch("valid_projection_no_probabilities"))
    manifest_path = write_result.run_directory / "run_manifest.json"
    manifest = _read_json(manifest_path)
    manifest["run_content_hash"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    report = verify_nba_player_points_evidence(tmp_path, CONFIG)
    assert report.ok is False
    assert any("run_content_hash" in violation for violation in report.violations)


def test_completion_marker_hash_detects_completed_run_mutation(tmp_path: Path) -> None:
    write_result = _write(tmp_path, _batch("valid_projection_no_probabilities"))
    manifest_path = write_result.run_directory / "run_manifest.json"
    manifest = _read_json(manifest_path)
    manifest["ledger_append_count"] = 99
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    report = verify_nba_player_points_evidence(tmp_path, CONFIG)
    assert report.ok is False
    assert any("completion marker run_manifest_hash" in violation for violation in report.violations)


def test_failure_before_any_write_leaves_no_evidence_root(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="before_any_write"):
        _write(
            tmp_path,
            _batch("valid_projection_no_probabilities"),
            failure_hook=_fail_at("before_any_write"),
        )

    assert not (tmp_path / "nba_player_points_evidence").exists()


@pytest.mark.parametrize(
    "stage_name",
    [
        "after_temp_dir_created",
        "after_first_evidence_file",
        "before_ledger_append",
        "during_ledger_append",
        "before_completion_marker",
    ],
)
def test_failure_injection_never_publishes_complete_run(
    tmp_path: Path,
    stage_name: str,
) -> None:
    with pytest.raises(RuntimeError, match=stage_name):
        _write(
            tmp_path,
            _batch("valid_projection_no_probabilities"),
            failure_hook=_fail_at(stage_name),
        )

    complete_markers = list((tmp_path / "nba_player_points_evidence").rglob("COMPLETE"))
    assert complete_markers == []


def test_retry_recovers_after_ledger_append_before_run_publication(tmp_path: Path) -> None:
    result = _batch("valid_projection_no_probabilities")

    with pytest.raises(RuntimeError, match="after_ledger_append_before_run_publication"):
        _write(
            tmp_path,
            result,
            failure_hook=_fail_at("after_ledger_append_before_run_publication"),
        )

    assert list(_evidence_root(tmp_path).rglob("COMPLETE")) == []
    ledger_before = _ledger_snapshot(tmp_path)
    interrupted_report = verify_nba_player_points_evidence(tmp_path, CONFIG)
    assert interrupted_report.ok is True
    assert interrupted_report.ledger_summary["recoverable_interrupted_segments"]

    recovered = _write(tmp_path, result)

    assert recovered.completion_status == "complete"
    assert recovered.ledger_append_count == 0
    assert _ledger_snapshot(tmp_path) == ledger_before
    final_report = verify_nba_player_points_evidence(tmp_path, CONFIG)
    assert final_report.ok is True
    assert final_report.ledger_summary["recoverable_interrupted_segments"] == []


def test_conflicting_retry_after_interrupted_ledger_append_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="after_ledger_append_before_run_publication"):
        _write(
            tmp_path,
            _batch("valid_projection_no_probabilities"),
            failure_hook=_fail_at("after_ledger_append_before_run_publication"),
        )

    with pytest.raises(NBAPlayerPointsEvidenceError, match="conflicting retry"):
        _write(tmp_path, _batch("conflicting_duplicate_rows"))

    assert list(_evidence_root(tmp_path).rglob("COMPLETE")) == []
    assert len(_ledger_rows(tmp_path)) == 1


def test_short_write_during_first_ledger_record_leaves_no_final_segment(
    tmp_path: Path,
) -> None:
    def short_write(path: Path, data: bytes) -> None:
        path.write_bytes(data[:8])
        raise RuntimeError("short write first record")

    with patch(
        "courtvision.sports.nba.player_points_evidence._write_bytes_verified",
        side_effect=short_write,
    ):
        with pytest.raises(RuntimeError, match="short write first record"):
            _write(tmp_path, _batch("valid_projection_no_probabilities"))

    assert _ledger_segment_paths(tmp_path) == []
    recovered = _write(tmp_path, _batch("valid_projection_no_probabilities"))
    assert recovered.completion_status == "complete"
    assert len(_ledger_rows(tmp_path)) == 1


def test_short_write_between_ledger_records_leaves_no_final_segment(
    tmp_path: Path,
) -> None:
    result = _batch_payloads(
        _case_payload("valid_projection_no_probabilities"),
        _alternate_probability_payload(),
    )

    def short_write(path: Path, data: bytes) -> None:
        first_line_end = data.index(b"\n") + 1
        path.write_bytes(data[: first_line_end + 6])
        raise RuntimeError("short write between records")

    with patch(
        "courtvision.sports.nba.player_points_evidence._write_bytes_verified",
        side_effect=short_write,
    ):
        with pytest.raises(RuntimeError, match="short write between records"):
            _write(tmp_path, result)

    assert _ledger_segment_paths(tmp_path) == []
    recovered = _write(tmp_path, result)
    assert recovered.completion_status == "complete"
    assert len(_ledger_rows(tmp_path)) == 2


def test_missing_final_newline_in_final_ledger_segment_is_precise_corruption(
    tmp_path: Path,
) -> None:
    write_result = _write(tmp_path, _batch("valid_projection_no_probabilities"))
    ledger_path = _ledger_segment_path(tmp_path, write_result)
    ledger_path.write_bytes(ledger_path.read_bytes().rstrip(b"\n"))

    report = verify_nba_player_points_evidence(tmp_path, CONFIG)

    assert report.ok is False
    assert any("missing final newline" in violation for violation in report.violations)


def test_later_independent_run_after_interrupted_temp_append_succeeds(
    tmp_path: Path,
) -> None:
    def short_write(path: Path, data: bytes) -> None:
        path.write_bytes(data[:12])
        raise RuntimeError("short write")

    with patch(
        "courtvision.sports.nba.player_points_evidence._write_bytes_verified",
        side_effect=short_write,
    ):
        with pytest.raises(RuntimeError, match="short write"):
            _write(tmp_path, _batch("valid_projection_no_probabilities"))

    independent = _write(tmp_path, _batch_with_run_id("run-nba-points-20260605-independent"))

    assert independent.completion_status == "complete"
    assert len(_ledger_rows(tmp_path)) == 1
    assert verify_nba_player_points_evidence(tmp_path, CONFIG).ok is True


def test_existing_conflicting_run_directory_fails_closed(tmp_path: Path) -> None:
    result = _batch("valid_projection_no_probabilities")
    run_dir = (
        tmp_path
        / "nba_player_points_evidence"
        / "runs"
        / "2026-06-05"
        / result.source_manifest_preview.prediction_run_id
    )
    run_dir.mkdir(parents=True)

    with pytest.raises(NBAPlayerPointsEvidenceError, match="without completion marker"):
        _write(tmp_path, result)


def test_corrupted_prediction_run_file_is_detected(tmp_path: Path) -> None:
    write_result = _write(tmp_path, _batch("valid_projection_no_probabilities"))
    prediction_file = write_result.run_directory / "prediction_rows.jsonl"
    row = _read_jsonl(prediction_file)[0]
    row["projected_points"] = 99.0
    prediction_file.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    report = verify_nba_player_points_evidence(tmp_path, CONFIG)
    assert report.ok is False
    assert any("prediction_rows.jsonl hash mismatch" in violation for violation in report.violations)


def test_corrupted_prediction_file_with_complete_does_not_replay(
    tmp_path: Path,
) -> None:
    result = _batch("valid_projection_no_probabilities")
    write_result = _write(tmp_path, result)
    prediction_file = write_result.run_directory / "prediction_rows.jsonl"
    row = _read_jsonl(prediction_file)[0]
    row["projected_points"] = 99.0
    prediction_file.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(NBAPlayerPointsEvidenceError, match="failed verification"):
        _write(tmp_path, result)


def test_corrupted_manifest_file_is_detected(tmp_path: Path) -> None:
    write_result = _write(tmp_path, _batch("valid_projection_no_probabilities"))
    (write_result.run_directory / "run_manifest.json").write_text("{bad", encoding="utf-8")

    report = verify_nba_player_points_evidence(tmp_path, CONFIG)
    assert report.ok is False
    assert any("invalid JSON file" in violation for violation in report.violations)


def test_corrupted_manifest_with_complete_does_not_replay(tmp_path: Path) -> None:
    result = _batch("valid_projection_no_probabilities")
    write_result = _write(tmp_path, result)
    manifest = _read_json(write_result.run_directory / "run_manifest.json")
    manifest["ledger_append_count"] = 2
    _rewrite_manifest_and_marker(write_result.run_directory, manifest)

    with pytest.raises(NBAPlayerPointsEvidenceError, match="different content|failed verification"):
        _write(tmp_path, result)


def test_missing_file_is_detected(tmp_path: Path) -> None:
    write_result = _write(tmp_path, _batch("valid_projection_no_probabilities"))
    (write_result.run_directory / "prediction_rows.jsonl").unlink()

    report = verify_nba_player_points_evidence(tmp_path, CONFIG)
    assert report.ok is False
    assert any("expected file missing: prediction_rows.jsonl" in violation for violation in report.violations)


def test_ledger_reference_to_incomplete_run_is_detected(tmp_path: Path) -> None:
    write_result = _write(tmp_path, _batch("valid_projection_no_probabilities"))
    shutil.rmtree(write_result.run_directory)

    report = verify_nba_player_points_evidence(tmp_path, CONFIG)
    assert report.ok is True
    assert report.ledger_summary["recoverable_interrupted_segments"]


def test_completed_run_missing_ledger_reference_is_detected(tmp_path: Path) -> None:
    write_result = _write(tmp_path, _batch("valid_projection_no_probabilities"))
    manifest = _read_json(write_result.run_directory / "run_manifest.json")
    manifest["ledger_segment_file"] = ""
    manifest["ledger_segment_hash"] = ""
    manifest["ledger_record_hashes"] = []
    _rewrite_manifest_and_marker(write_result.run_directory, manifest)

    report = verify_nba_player_points_evidence(tmp_path, CONFIG)

    assert report.ok is False
    assert any("missing ledger reference" in violation for violation in report.violations)


def test_completed_run_conflicting_ledger_record_is_detected(tmp_path: Path) -> None:
    write_result = _write(tmp_path, _batch("valid_projection_no_probabilities"))
    ledger_path = _ledger_segment_path(tmp_path, write_result)
    row = _read_jsonl(ledger_path)[0]
    conflict = dict(row)
    conflict["projected_points"] = 99.0
    ledger_path.write_text(
        json.dumps(row, sort_keys=True) + "\n" + json.dumps(conflict, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report = verify_nba_player_points_evidence(tmp_path, CONFIG)

    assert report.ok is False
    assert any(
        "ledger_record_hash mismatch" in violation or "duplicate prediction_id" in violation
        for violation in report.violations
    )


def test_symlinked_run_file_is_detected(tmp_path: Path) -> None:
    write_result = _write(tmp_path, _batch("valid_projection_no_probabilities"))
    target = tmp_path / "outside_prediction_rows.jsonl"
    target.write_text("[]\n", encoding="utf-8")
    prediction_file = write_result.run_directory / "prediction_rows.jsonl"
    prediction_file.unlink()
    try:
        prediction_file.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    report = verify_nba_player_points_evidence(tmp_path, CONFIG)

    assert report.ok is False
    assert any("symlink" in violation for violation in report.violations)


def test_identical_intact_replay_with_new_timestamp_is_already_complete(
    tmp_path: Path,
) -> None:
    result = _batch("valid_projection_no_probabilities")
    first = _write(tmp_path, result)

    second = _write(
        tmp_path,
        result,
        writer_timestamp_utc="2026-06-05T18:20:00Z",
    )

    assert second.completion_status == "already_complete"
    assert second.run_manifest["created_at_utc"] == first.run_manifest["created_at_utc"]
    assert second.run_manifest["completed_at_utc"] == first.run_manifest["completed_at_utc"]


@pytest.mark.parametrize("field_name", ["settlement_status", "closing_line"])
def test_settlement_and_closing_line_fields_are_rejected_in_ledger(
    tmp_path: Path,
    field_name: str,
) -> None:
    write_result = _write(tmp_path, _batch("valid_projection_no_probabilities"))
    ledger_path = _ledger_segment_path(tmp_path, write_result)
    row = _read_jsonl(ledger_path)[0]
    row[field_name] = "forbidden"
    ledger_path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    report = verify_nba_player_points_evidence(tmp_path, CONFIG)
    assert report.ok is False
    assert any("prohibited field" in violation for violation in report.violations)


def test_input_order_independence_for_run_content_and_ledger(tmp_path: Path) -> None:
    forward_dir = tmp_path / "forward"
    reverse_dir = tmp_path / "reverse"
    forward = _batch_payloads(
        _case_payload("valid_projection_no_probabilities"),
        _alternate_probability_payload(),
        _case_payload("input_order_independence"),
    )
    reverse = _batch_payloads(
        _case_payload("input_order_independence"),
        _alternate_probability_payload(),
        _case_payload("valid_projection_no_probabilities"),
    )
    first = _write(forward_dir, forward)
    second = _write(reverse_dir, reverse)

    assert first.run_manifest["run_content_hash"] == second.run_manifest["run_content_hash"]
    assert first.ledger_path.read_text(encoding="utf-8") == second.ledger_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "run_id",
    [
        "../evil",
        "run/evil",
        "run\\evil",
        "C:\\absolute\\evil",
        "   ",
        "run..evil",
    ],
)
def test_invalid_prediction_run_id_path_inputs_are_rejected(
    tmp_path: Path,
    run_id: str,
) -> None:
    result = _corrupt_run_identity(_batch("valid_projection_no_probabilities"), run_id)

    with pytest.raises(NBAPlayerPointsEvidenceError, match="prediction_run_id"):
        _write(tmp_path, result)


def test_operating_date_must_use_strict_iso_format(tmp_path: Path) -> None:
    result = _corrupt_operating_date(
        _batch("valid_projection_no_probabilities"),
        "2026/06/05",
    )

    with pytest.raises(NBAPlayerPointsEvidenceError, match="operating_date"):
        _write(tmp_path, result)


def test_symlinked_caller_evidence_root_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "linked-output"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(NBAPlayerPointsEvidenceError, match="symlink"):
        _write(link, _batch("valid_projection_no_probabilities"))


def test_symlinked_run_directory_is_rejected(tmp_path: Path) -> None:
    result = _batch("valid_projection_no_probabilities")
    outside = tmp_path / "outside-run"
    outside.mkdir()
    run_dir = (
        _evidence_root(tmp_path)
        / "runs"
        / "2026-06-05"
        / result.source_manifest_preview.prediction_run_id
    )
    run_dir.parent.mkdir(parents=True)
    try:
        run_dir.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(NBAPlayerPointsEvidenceError, match="symlink"):
        _write(tmp_path, result)


def _run_writes_concurrently(
    tmp_path: Path,
    *results: NBAPlayerPointsAssemblyBatchResult,
) -> list[object]:
    barrier = threading.Barrier(len(results))

    def worker(result: NBAPlayerPointsAssemblyBatchResult) -> object:
        barrier.wait(timeout=5)
        return _write(tmp_path, result)

    outcomes: list[object] = []
    with ThreadPoolExecutor(max_workers=len(results)) as executor:
        futures = [executor.submit(worker, result) for result in results]
        for future in as_completed(futures):
            try:
                outcomes.append(future.result())
            except Exception as exc:  # tests assert exact exception classes below
                outcomes.append(exc)
    return outcomes


def test_concurrent_same_run_same_content_does_not_duplicate_ledger(
    tmp_path: Path,
) -> None:
    result = _batch("valid_projection_no_probabilities")

    outcomes = _run_writes_concurrently(tmp_path, result, result)

    statuses = sorted(
        outcome.completion_status
        for outcome in outcomes
        if not isinstance(outcome, Exception)
    )
    assert statuses == ["already_complete", "complete"]
    assert len(_ledger_rows(tmp_path)) == 1


def test_concurrent_same_run_conflicting_content_fails_closed(
    tmp_path: Path,
) -> None:
    outcomes = _run_writes_concurrently(
        tmp_path,
        _batch("valid_projection_no_probabilities"),
        _batch("conflicting_duplicate_rows"),
    )

    successes = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], NBAPlayerPointsEvidenceError)
    assert len(_ledger_rows(tmp_path)) == 1
    assert verify_nba_player_points_evidence(tmp_path, CONFIG).ok is True


def test_concurrent_different_runs_do_not_interleave_ledger_segments(
    tmp_path: Path,
) -> None:
    outcomes = _run_writes_concurrently(
        tmp_path,
        _batch("valid_projection_no_probabilities"),
        _batch_with_run_id("run-nba-points-20260605-concurrent-2"),
    )

    assert all(not isinstance(outcome, Exception) for outcome in outcomes)
    assert sorted(outcome.completion_status for outcome in outcomes) == ["complete", "complete"]
    assert len(_ledger_rows(tmp_path)) == 2
    assert len(_ledger_segment_paths(tmp_path)) == 2
    assert verify_nba_player_points_evidence(tmp_path, CONFIG).ok is True


def test_writer_failure_while_holding_lock_releases_for_retry(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="after_temp_dir_created"):
        _write(
            tmp_path,
            _batch("valid_projection_no_probabilities"),
            failure_hook=_fail_at("after_temp_dir_created"),
        )

    assert not (_evidence_root(tmp_path) / ".evidence-writer.lock").exists()
    retry = _write(tmp_path, _batch("valid_projection_no_probabilities"))
    assert retry.completion_status == "complete"
    assert len(_ledger_rows(tmp_path)) == 1


def test_repeated_deterministic_write_matches_across_roots(tmp_path: Path) -> None:
    result = _batch("valid_projection_no_probabilities")
    first = _write(tmp_path / "first", result)
    second = _write(tmp_path / "second", result)

    assert first.run_manifest["run_content_hash"] == second.run_manifest["run_content_hash"]
    assert first.ledger_path.read_text(encoding="utf-8") == second.ledger_path.read_text(encoding="utf-8")


def test_source_fixture_immutability(tmp_path: Path) -> None:
    before_hash = _sha256(ASSEMBLY_CASES_FIXTURE)

    _write(tmp_path, _batch("source_fixture_immutability"))

    assert _sha256(ASSEMBLY_CASES_FIXTURE) == before_hash


def test_no_live_calls_credentials_or_production_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    before = _snapshot(tmp_path)

    with (
        patch("requests.Session.get", side_effect=AssertionError("live call attempted")) as mock_get,
        patch("os.getenv", side_effect=AssertionError("credential read attempted")) as mock_getenv,
    ):
        _write(tmp_path, _batch("valid_projection_no_probabilities"))

    assert mock_get.call_count == 0
    assert mock_getenv.call_count == 0
    assert not (tmp_path / "outputs").exists()
    assert not (tmp_path / "test_outputs").exists()
    assert not (tmp_path / "data" / "history").exists()
    assert before == ()


def test_architectural_boundary_and_backward_compatibility_imports() -> None:
    source_text = EVIDENCE_MODULE.read_text(encoding="utf-8").casefold()

    assert "courtvision_ai" not in source_text
    assert "run_today" not in source_text
    assert "requests" not in source_text
    assert "os.getenv" not in source_text
    assert "os.environ" not in source_text
    assert "sports.mlb" not in source_text
    assert "import kelly" not in source_text
    assert "kelly_" not in source_text
    assert "import bankroll" not in source_text
    assert "bankroll_" not in source_text
    assert "settle_nba_player_points_predictions" not in source_text

    from courtvision.sports.nba import player_minutes_research, player_points_assembly
    from courtvision.sports.nba import player_points_crosswalk, player_points_settlement

    assert player_points_assembly.NBA_PLAYER_POINTS_ASSEMBLY_SCHEMA_VERSION == "nba-player-points-assembly-v1"
    assert player_points_crosswalk.NBA_PLAYER_POINTS_CROSSWALK_SCHEMA_VERSION == "nba-player-points-crosswalk-v1"
    assert player_points_settlement.NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION == "nba-player-points-settlement-v1"
    assert player_minutes_research.NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION == "nba-player-minutes-feature-v1"


def test_no_probabilities_are_fabricated(tmp_path: Path) -> None:
    _write(tmp_path, _batch("valid_projection_no_probabilities"))
    row = _ledger_rows(tmp_path)[0]

    assert row["probability_status"] == "unavailable"
    assert row["model_over_probability"] is None
    assert row["model_under_probability"] is None
    assert row["probability_model_id"] is None
