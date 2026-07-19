"""Append-only NBA player-points prediction evidence writer.

This module persists already-validated NBA player-points assembly results for
offline research. It performs no provider I/O, reads no credentials, does not
assemble rows, does not settle outcomes, and does not touch production
prediction, selection, wager sizing, grading, dashboard, or runtime paths.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import time
from types import MappingProxyType
from typing import Any, Final
from uuid import uuid4

from courtvision.sports.nba.player_minutes_research import (
    NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION,
)
from courtvision.sports.nba.player_points_assembly import (
    NBA_PLAYER_POINTS_ASSEMBLY_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_PROBABILITY_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_SOURCE_MANIFEST_SCHEMA_VERSION,
    NBAPlayerPointsAssembledRow,
    NBAPlayerPointsAssemblyBatchResult,
    NBAPlayerPointsSourceManifestPreview,
    generate_preview_prediction_id,
)
from courtvision.sports.nba.player_points_research import (
    NBA_PLAYER_POINTS_MARKET,
    NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL,
    NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION,
)


NBA_PLAYER_POINTS_EVIDENCE_SCHEMA_VERSION: Final = "nba-player-points-evidence-v1"
NBA_PLAYER_POINTS_LEDGER_SCHEMA_VERSION: Final = "nba-player-points-ledger-v1"
NBA_PLAYER_POINTS_EVIDENCE_DIR_NAME: Final = "nba_player_points_evidence"

NBA_PLAYER_POINTS_EVIDENCE_COMPLETION_STATUSES: Final = (
    "writing",
    "complete",
    "failed",
    "conflicting",
    "already_complete",
)

NBA_PLAYER_POINTS_EVIDENCE_FILES: Final = (
    "source_manifest_preview.json",
    "prediction_rows.jsonl",
    "excluded_rows.jsonl",
    "quarantined_rows.jsonl",
    "conflicting_rows.jsonl",
    "duplicate_diagnostics.json",
    "integrity_report.json",
)

NBA_PLAYER_POINTS_RUN_MANIFEST_FILE: Final = "run_manifest.json"
NBA_PLAYER_POINTS_COMPLETION_MARKER_FILE: Final = "COMPLETE"
NBA_PLAYER_POINTS_LEDGER_FILE: Final = "prediction_ledger.jsonl"
NBA_PLAYER_POINTS_LEDGER_SEGMENTS_DIR_NAME: Final = "segments"
NBA_PLAYER_POINTS_EVIDENCE_LOCK_FILE: Final = ".evidence-writer.lock"

_UTC: Final = timezone.utc
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA_RE: Final = re.compile(r"^[0-9a-f]{7,40}$")
_ELIGIBLE_ASSEMBLY_STATUSES: Final = (
    "eligible_projection_research",
    "eligible_probability_research",
)
_PROHIBITED_PREDICTION_FIELD_FRAGMENTS: Final = (
    "settlement",
    "closing",
)
_PROHIBITED_PREDICTION_FIELDS: Final = (
    "actual_points",
    "final_points",
    "target_game_actual_points",
    "target_game_final_points",
    "actual_minutes",
    "target_game_actual_minutes",
    "final_stats",
    "box_score",
    "roi",
    "kelly",
    "stake",
    "bankroll",
)
_ASSEMBLED_HASH_EXCLUDED_FIELDS: Final = (
    "artifact_hash",
    "assembled_record_hash",
    "evidence_conflict_status",
    "evidence_conflict_reason",
    "evidence_conflict_scope",
    "conflicting_existing_ledger_record_hash",
    "conflicting_existing_assembled_record_hash",
)
_MANIFEST_REQUIRED_FIELDS: Final = (
    "evidence_schema_version",
    "prediction_run_id",
    "operating_date",
    "created_at_utc",
    "completed_at_utc",
    "repository_commit_sha",
    "research_label",
    "model_id",
    "feature_schema_version",
    "assembly_schema_version",
    "source_manifest_id",
    "source_manifest_hash",
    "source_manifest_schema_version",
    "total_input_rows",
    "eligible_projection_rows",
    "eligible_probability_rows",
    "excluded_rows",
    "quarantined_rows",
    "conflicting_rows",
    "duplicate_diagnostics_count",
    "source_manifest_preview_file",
    "source_manifest_preview_hash",
    "prediction_rows_file",
    "prediction_rows_hash",
    "excluded_rows_file",
    "excluded_rows_hash",
    "quarantined_rows_file",
    "quarantined_rows_hash",
    "conflicting_rows_file",
    "conflicting_rows_hash",
    "duplicate_diagnostics_file",
    "duplicate_diagnostics_hash",
    "integrity_report_file",
    "integrity_report_hash",
    "ledger_schema_version",
    "ledger_segment_file",
    "ledger_segment_hash",
    "ledger_record_hashes",
    "ledger_append_count",
    "run_content_hash",
    "completion_status",
)

FailureHook = Callable[[str], None]


class NBAPlayerPointsEvidenceError(ValueError):
    """Raised when NBA player-points evidence persistence fails closed."""


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsEvidenceWriterConfig:
    """Explicit append-only evidence writer configuration."""

    evidence_dir_name: str = NBA_PLAYER_POINTS_EVIDENCE_DIR_NAME
    evidence_schema_version: str = NBA_PLAYER_POINTS_EVIDENCE_SCHEMA_VERSION
    ledger_schema_version: str = NBA_PLAYER_POINTS_LEDGER_SCHEMA_VERSION
    research_label: str = NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL
    runs_dir_name: str = "runs"
    ledgers_dir_name: str = "ledgers"
    ledger_file_name: str = NBA_PLAYER_POINTS_LEDGER_FILE
    completion_marker_file_name: str = NBA_PLAYER_POINTS_COMPLETION_MARKER_FILE
    lock_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.evidence_schema_version != NBA_PLAYER_POINTS_EVIDENCE_SCHEMA_VERSION:
            raise NBAPlayerPointsEvidenceError(
                f"unsupported evidence_schema_version: {self.evidence_schema_version!r}"
            )
        if self.ledger_schema_version != NBA_PLAYER_POINTS_LEDGER_SCHEMA_VERSION:
            raise NBAPlayerPointsEvidenceError(
                f"unsupported ledger_schema_version: {self.ledger_schema_version!r}"
            )
        if self.research_label != NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL:
            raise NBAPlayerPointsEvidenceError("research_label is unsupported")
        for field_name in (
            "evidence_dir_name",
            "runs_dir_name",
            "ledgers_dir_name",
            "ledger_file_name",
            "completion_marker_file_name",
        ):
            _require_safe_path_component(getattr(self, field_name), field_name)
        if (
            isinstance(self.lock_timeout_seconds, bool)
            or not isinstance(self.lock_timeout_seconds, (int, float))
            or not math.isfinite(float(self.lock_timeout_seconds))
            or float(self.lock_timeout_seconds) < 0
        ):
            raise NBAPlayerPointsEvidenceError("lock_timeout_seconds must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsEvidenceWriteResult:
    """Structured result for one evidence write attempt."""

    completion_status: str
    evidence_root: Path
    run_directory: Path
    ledger_path: Path
    run_manifest: Mapping[str, object]
    integrity_report: Mapping[str, object]
    ledger_append_count: int
    duplicate_diagnostics: tuple[Mapping[str, object], ...] = ()
    conflict_diagnostics: tuple[Mapping[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "completion_status": self.completion_status,
            "evidence_root": str(self.evidence_root),
            "run_directory": str(self.run_directory),
            "ledger_path": str(self.ledger_path),
            "run_manifest": _json_ready(self.run_manifest),
            "integrity_report": _json_ready(self.integrity_report),
            "ledger_append_count": self.ledger_append_count,
            "duplicate_diagnostics": [_json_ready(item) for item in self.duplicate_diagnostics],
            "conflict_diagnostics": [_json_ready(item) for item in self.conflict_diagnostics],
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsEvidenceIntegrityReport:
    """Pure verifier report for an evidence root."""

    ok: bool
    violations: tuple[str, ...]
    evidence_root: Path
    completed_runs: tuple[Mapping[str, object], ...]
    ledger_summary: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "violations": list(self.violations),
            "evidence_root": str(self.evidence_root),
            "completed_runs": [_json_ready(run) for run in self.completed_runs],
            "ledger_summary": _json_ready(self.ledger_summary),
        }


def evidence_manifest_schema_definition() -> dict[str, object]:
    """Return the versioned run-manifest contract."""

    return {
        "evidence_schema_version": NBA_PLAYER_POINTS_EVIDENCE_SCHEMA_VERSION,
        "ledger_schema_version": NBA_PLAYER_POINTS_LEDGER_SCHEMA_VERSION,
        "completion_statuses": list(NBA_PLAYER_POINTS_EVIDENCE_COMPLETION_STATUSES),
        "required_fields": list(_MANIFEST_REQUIRED_FIELDS),
        "hash_algorithm": "SHA-256",
        "canonical_json": {
            "sort_keys": True,
            "separators": [",", ":"],
            "encoding": "utf-8",
            "allow_nan": False,
        },
        "layout": {
            "run_manifest": NBA_PLAYER_POINTS_RUN_MANIFEST_FILE,
            "prediction_rows": "prediction_rows.jsonl",
            "excluded_rows": "excluded_rows.jsonl",
            "quarantined_rows": "quarantined_rows.jsonl",
            "conflicting_rows": "conflicting_rows.jsonl",
            "duplicate_diagnostics": "duplicate_diagnostics.json",
            "source_manifest_preview": "source_manifest_preview.json",
            "integrity_report": "integrity_report.json",
            "completion_marker": NBA_PLAYER_POINTS_COMPLETION_MARKER_FILE,
            "ledger_segments": (
                f"ledgers/{NBA_PLAYER_POINTS_LEDGER_SEGMENTS_DIR_NAME}/"
                f"{{operating_date}}/{{prediction_run_id}}/{NBA_PLAYER_POINTS_LEDGER_FILE}"
            ),
        },
    }


def ledger_record_schema_definition() -> dict[str, object]:
    """Return the versioned append-only prediction-ledger record contract."""

    return {
        "ledger_schema_version": NBA_PLAYER_POINTS_LEDGER_SCHEMA_VERSION,
        "required_fields": list(_ledger_record_field_names()),
        "hash_algorithm": "SHA-256",
        "storage": "immutable per-run JSONL segments published by atomic directory rename",
        "ledger_record_hash_excludes": ["ledger_record_hash"],
        "forbidden_fields": [
            *_PROHIBITED_PREDICTION_FIELDS,
            *_PROHIBITED_PREDICTION_FIELD_FRAGMENTS,
        ],
        "probability_status_without_probabilities": "unavailable",
    }


def write_nba_player_points_evidence(
    assembly_result: NBAPlayerPointsAssemblyBatchResult,
    source_manifest_preview: NBAPlayerPointsSourceManifestPreview,
    output_directory: str | Path,
    config: NBAPlayerPointsEvidenceWriterConfig,
    *,
    repository_commit_sha: str,
    writer_timestamp_utc: datetime | str,
    failure_hook: FailureHook | None = None,
) -> NBAPlayerPointsEvidenceWriteResult:
    """Persist a validated assembly result into immutable run files and ledger.

    The writer is intentionally a persistence boundary: it accepts already-built
    assembly and source-manifest objects and never invokes assembly, providers,
    settlement, production prediction code, or bankroll-facing logic.
    """

    _validate_config(config)
    timestamp = _coerce_utc_datetime(writer_timestamp_utc, "writer_timestamp_utc")
    commit_sha = _require_commit_sha(repository_commit_sha, "repository_commit_sha")
    source_manifest = _validate_inputs(
        assembly_result,
        source_manifest_preview,
        repository_commit_sha=commit_sha,
        config=config,
    )
    row_payloads = tuple(row.to_dict() for row in assembly_result.rows)
    _validate_path_identity_payloads(row_payloads, source_manifest)
    for payload in row_payloads:
        _validate_assembled_payload(
            payload,
            source_manifest_hash=source_manifest.manifest_hash,
            repository_commit_sha=commit_sha,
        )

    run_identity = _run_identity(row_payloads, source_manifest, commit_sha, config)
    evidence_root = _evidence_root(Path(output_directory), config)
    runs_parent = evidence_root / config.runs_dir_name / str(run_identity["operating_date"])
    run_directory = runs_parent / str(run_identity["prediction_run_id"])
    ledger_path = _ledger_segment_path(evidence_root, run_identity, config)

    _call_failure_hook(failure_hook, "before_any_write")
    with _EvidenceRootLock(evidence_root, config):
        _assert_transaction_paths_safe(evidence_root, run_directory, ledger_path)

        if run_directory.is_symlink():
            raise NBAPlayerPointsEvidenceError(
                f"prediction-run directory is a symlink: {run_directory}"
            )
        if run_directory.exists():
            existing_manifest = _read_existing_completed_manifest(
                run_directory,
                evidence_root,
                config,
            )
            replay_prepared = _prepare_run_payloads(
                row_payloads,
                assembly_result,
                source_manifest,
                {},
                config=config,
            )
            replay_file_blobs = _run_file_blobs(replay_prepared)
            replay_file_hashes = {
                name: _sha256_bytes(data) for name, data in replay_file_blobs.items()
            }
            replay_row_counts = _row_counts(replay_prepared)
            replay_run_content_hash = _run_content_hash(
                run_identity=run_identity,
                file_hashes=replay_file_hashes,
                row_counts=replay_row_counts,
                config=config,
            )
            if existing_manifest.get("run_content_hash") == replay_run_content_hash:
                integrity_report = verify_nba_player_points_evidence(
                    evidence_root,
                    config,
                )
                if not integrity_report.ok:
                    raise NBAPlayerPointsEvidenceError(
                        "existing completed run failed verification: "
                        + "; ".join(integrity_report.violations)
                    )
                return NBAPlayerPointsEvidenceWriteResult(
                    completion_status="already_complete",
                    evidence_root=evidence_root,
                    run_directory=run_directory,
                    ledger_path=ledger_path,
                    run_manifest=MappingProxyType(dict(existing_manifest)),
                    integrity_report=MappingProxyType(integrity_report.to_dict()),
                    ledger_append_count=0,
                    duplicate_diagnostics=tuple(replay_prepared.duplicate_diagnostics),
                    conflict_diagnostics=tuple(replay_prepared.conflict_diagnostics),
                )
            raise NBAPlayerPointsEvidenceError(
                "completed prediction-run directory already exists with different content"
            )

        ledger_index = _load_ledger_index(evidence_root, config)
        prepared = _prepare_run_payloads(
            row_payloads,
            assembly_result,
            source_manifest,
            ledger_index,
            config=config,
        )
        _raise_on_same_run_ledger_conflict(prepared, run_identity)

        file_blobs = _run_file_blobs(prepared)
        file_hashes = {name: _sha256_bytes(data) for name, data in file_blobs.items()}
        row_counts = _row_counts(prepared)
        run_content_hash = _run_content_hash(
            run_identity=run_identity,
            file_hashes=file_hashes,
            row_counts=row_counts,
            config=config,
        )
        completion_status = (
            "conflicting"
            if prepared.conflict_diagnostics or prepared.conflicting_rows
            else "complete"
        )

        stage_directory = runs_parent / f".{run_identity['prediction_run_id']}.tmp-{uuid4().hex}"
        ledger_publication = _ledger_segment_publication_metadata(
            evidence_root,
            run_identity,
            (),
            config,
        )
        final_manifest: Mapping[str, object] | None = None
        try:
            _make_directory(runs_parent)
            stage_directory.mkdir()
            _call_failure_hook(failure_hook, "after_temp_dir_created")

            writing_manifest = _run_manifest_payload(
                run_identity=run_identity,
                file_hashes={name: "" for name in NBA_PLAYER_POINTS_EVIDENCE_FILES},
                row_counts=row_counts,
                duplicate_diagnostics_count=len(prepared.duplicate_diagnostics),
                ledger_append_count=0,
                ledger_segment_file="",
                ledger_segment_hash="",
                ledger_record_hashes=(),
                run_content_hash="",
                integrity_report_hash="",
                completion_status="writing",
                created_at_utc=timestamp,
                completed_at_utc=None,
                config=config,
            )
            _write_json_file(stage_directory / NBA_PLAYER_POINTS_RUN_MANIFEST_FILE, writing_manifest)

            for index, (file_name, data) in enumerate(file_blobs.items()):
                (stage_directory / file_name).write_bytes(data)
                if index == 0:
                    _call_failure_hook(failure_hook, "after_first_evidence_file")

            _verify_written_files(stage_directory, file_blobs)
            _call_failure_hook(failure_hook, "after_hash_verification")

            _call_failure_hook(failure_hook, "before_ledger_append")
            if completion_status == "complete":
                ledger_publication = _publish_ledger_segment(
                    evidence_root,
                    run_identity,
                    prepared.ledger_records_for_run,
                    config,
                    failure_hook=failure_hook,
                )

            integrity_payload = _run_integrity_payload(
                status=completion_status,
                run_identity=run_identity,
                file_hashes=file_hashes,
                row_counts=row_counts,
                duplicate_diagnostics=prepared.duplicate_diagnostics,
                conflict_diagnostics=prepared.conflict_diagnostics,
                ledger_append_count=ledger_publication.record_count,
                ledger_segment_file=ledger_publication.relative_path,
                ledger_segment_hash=ledger_publication.segment_hash,
                ledger_record_hashes=ledger_publication.record_hashes,
                run_content_hash=run_content_hash,
            )
            _write_json_file(stage_directory / "integrity_report.json", integrity_payload)
            integrity_hash = _sha256_file(stage_directory / "integrity_report.json")

            final_hashes = dict(file_hashes)
            final_hashes["integrity_report.json"] = integrity_hash
            final_manifest = _run_manifest_payload(
                run_identity=run_identity,
                file_hashes=final_hashes,
                row_counts=row_counts,
                duplicate_diagnostics_count=len(prepared.duplicate_diagnostics),
                ledger_append_count=ledger_publication.record_count,
                ledger_segment_file=ledger_publication.relative_path,
                ledger_segment_hash=ledger_publication.segment_hash,
                ledger_record_hashes=ledger_publication.record_hashes,
                run_content_hash=run_content_hash,
                integrity_report_hash=integrity_hash,
                completion_status=completion_status,
                created_at_utc=timestamp,
                completed_at_utc=timestamp,
                config=config,
            )
            _write_json_file(stage_directory / NBA_PLAYER_POINTS_RUN_MANIFEST_FILE, final_manifest)

            _call_failure_hook(failure_hook, "before_completion_marker")
            marker = {
                "evidence_schema_version": config.evidence_schema_version,
                "prediction_run_id": run_identity["prediction_run_id"],
                "completion_status": completion_status,
                "run_manifest_file": NBA_PLAYER_POINTS_RUN_MANIFEST_FILE,
                "run_manifest_hash": _sha256_file(
                    stage_directory / NBA_PLAYER_POINTS_RUN_MANIFEST_FILE
                ),
                "created_at_utc": _format_utc(timestamp),
            }
            _write_json_file(stage_directory / config.completion_marker_file_name, marker)
            _verify_completed_stage(stage_directory, final_manifest, config)
            stage_directory.rename(run_directory)
        except Exception:
            if stage_directory.exists():
                shutil.rmtree(stage_directory, ignore_errors=True)
            raise

        verifier_report = verify_nba_player_points_evidence(evidence_root, config)
        if not verifier_report.ok:
            raise NBAPlayerPointsEvidenceError(
                "evidence root failed verification after write: "
                + "; ".join(verifier_report.violations)
            )
        if final_manifest is None:
            raise NBAPlayerPointsEvidenceError("final manifest was not published")
        return NBAPlayerPointsEvidenceWriteResult(
            completion_status=completion_status,
            evidence_root=evidence_root,
            run_directory=run_directory,
            ledger_path=ledger_publication.segment_path,
            run_manifest=MappingProxyType(dict(final_manifest)),
            integrity_report=MappingProxyType(verifier_report.to_dict()),
            ledger_append_count=ledger_publication.newly_published_count,
            duplicate_diagnostics=tuple(prepared.duplicate_diagnostics),
            conflict_diagnostics=tuple(prepared.conflict_diagnostics),
        )


def verify_nba_player_points_evidence(
    output_directory: str | Path,
    config: NBAPlayerPointsEvidenceWriterConfig | None = None,
) -> NBAPlayerPointsEvidenceIntegrityReport:
    """Inspect an evidence root without provider access or mutation."""

    cfg = config or NBAPlayerPointsEvidenceWriterConfig()
    _validate_config(cfg)
    evidence_root = _evidence_root(Path(output_directory), cfg)
    violations: list[str] = []
    completed_runs: list[Mapping[str, object]] = []
    completed_run_ids: set[str] = set()
    completed_run_statuses: dict[str, str] = {}
    blocked_prediction_ids: set[str] = set()

    if not evidence_root.exists():
        violations.append(f"evidence root does not exist: {evidence_root}")
        return NBAPlayerPointsEvidenceIntegrityReport(
            ok=False,
            violations=tuple(violations),
            evidence_root=evidence_root,
            completed_runs=(),
            ledger_summary=MappingProxyType({"ledger_rows": 0, "ledger_path": ""}),
        )

    runs_root = evidence_root / cfg.runs_dir_name
    if runs_root.exists():
        for run_dir in sorted(path for path in runs_root.glob("*/*") if path.is_dir()):
            report = _verify_run_directory(run_dir, cfg)
            completed_runs.append(MappingProxyType(report))
            manifest = report.get("manifest")
            status = None
            run_id = None
            if isinstance(manifest, Mapping):
                status = manifest.get("completion_status")
                run_id = manifest.get("prediction_run_id")
            if report["violations"]:
                violations.extend(str(item) for item in report["violations"])
            if run_id and status in {"complete", "conflicting"} and not report["violations"]:
                completed_run_ids.add(str(run_id))
                completed_run_statuses[str(run_id)] = str(status)
            blocked_prediction_ids.update(str(item) for item in report["blocked_prediction_ids"])

    ledger_scan = _scan_ledger_segments(
        evidence_root,
        cfg,
        completed_run_statuses=MappingProxyType(completed_run_statuses),
    )
    violations.extend(ledger_scan.violations)
    for row in ledger_scan.rows:
        prediction_id = str(row["prediction_id"])
        if prediction_id in blocked_prediction_ids:
            violations.append(f"blocked prediction_id appears in ledger: {prediction_id}")

    for report in completed_runs:
        if report["violations"]:
            continue
        manifest = report.get("manifest")
        if not isinstance(manifest, Mapping) or manifest.get("completion_status") != "complete":
            continue
        violations.extend(
            _verify_completed_run_ledger_references(
                evidence_root,
                Path(str(report["run_directory"])),
                manifest,
                ledger_scan,
                cfg,
            )
        )

    segments_root = (
        evidence_root / cfg.ledgers_dir_name / NBA_PLAYER_POINTS_LEDGER_SEGMENTS_DIR_NAME
    )
    ledger_summary = MappingProxyType(
        {
            "ledger_path": str(segments_root),
            "ledger_exists": segments_root.exists(),
            "ledger_rows": len(ledger_scan.rows),
            "unique_prediction_ids": len(ledger_scan.index),
            "completed_run_ids": sorted(completed_run_ids),
            "recoverable_interrupted_segments": list(
                ledger_scan.recoverable_interrupted_segments
            ),
            "ledger_segment_count": len(ledger_scan.segments_by_run_id),
        }
    )
    return NBAPlayerPointsEvidenceIntegrityReport(
        ok=not violations,
        violations=tuple(violations),
        evidence_root=evidence_root,
        completed_runs=tuple(completed_runs),
        ledger_summary=ledger_summary,
    )


@dataclass(frozen=True, slots=True)
class _PreparedRunPayloads:
    source_manifest_payload: Mapping[str, object]
    prediction_rows: tuple[Mapping[str, object], ...]
    excluded_rows: tuple[Mapping[str, object], ...]
    quarantined_rows: tuple[Mapping[str, object], ...]
    conflicting_rows: tuple[Mapping[str, object], ...]
    duplicate_diagnostics: tuple[Mapping[str, object], ...]
    conflict_diagnostics: tuple[Mapping[str, object], ...]
    ledger_records_for_run: tuple[Mapping[str, object], ...]
    ledger_records_to_append: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class _LedgerSegment:
    prediction_run_id: str
    operating_date: str
    segment_path: Path
    relative_path: str
    segment_hash: str
    rows: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class _LedgerScan:
    rows: tuple[Mapping[str, object], ...]
    index: Mapping[str, Mapping[str, object]]
    segments_by_run_id: Mapping[str, _LedgerSegment]
    violations: tuple[str, ...]
    recoverable_interrupted_segments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _LedgerSegmentPublication:
    segment_path: Path
    relative_path: str
    segment_hash: str
    record_hashes: tuple[str, ...]
    record_count: int
    newly_published_count: int


def _validate_config(config: NBAPlayerPointsEvidenceWriterConfig) -> None:
    if not isinstance(config, NBAPlayerPointsEvidenceWriterConfig):
        raise TypeError("config must be NBAPlayerPointsEvidenceWriterConfig")


class _EvidenceRootLock:
    def __init__(self, evidence_root: Path, config: NBAPlayerPointsEvidenceWriterConfig) -> None:
        self._evidence_root = evidence_root
        self._config = config
        self._lock_path = evidence_root / NBA_PLAYER_POINTS_EVIDENCE_LOCK_FILE
        self._fd: int | None = None

    def __enter__(self) -> "_EvidenceRootLock":
        _assert_no_existing_symlink(self._evidence_root)
        self._evidence_root.mkdir(parents=True, exist_ok=True)
        _assert_no_existing_symlink(self._evidence_root)
        deadline = time.monotonic() + float(self._config.lock_timeout_seconds)
        while True:
            try:
                self._fd = os.open(
                    self._lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                payload = _json_file_bytes(
                    {
                        "pid": os.getpid(),
                        "created_at_utc": _format_utc(datetime.now(tz=_UTC)),
                    }
                )
                os.write(self._fd, payload)
                os.fsync(self._fd)
                return self
            except FileExistsError as exc:
                if time.monotonic() >= deadline:
                    raise NBAPlayerPointsEvidenceError(
                        f"evidence writer lock is already held: {self._lock_path}"
                    ) from exc
                time.sleep(0.01)
            except OSError as exc:
                raise NBAPlayerPointsEvidenceError(
                    f"unable to acquire evidence writer lock: {self._lock_path}"
                ) from exc

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            pass


def _validate_inputs(
    assembly_result: NBAPlayerPointsAssemblyBatchResult,
    source_manifest_preview: NBAPlayerPointsSourceManifestPreview,
    *,
    repository_commit_sha: str,
    config: NBAPlayerPointsEvidenceWriterConfig,
) -> NBAPlayerPointsSourceManifestPreview:
    if not isinstance(assembly_result, NBAPlayerPointsAssemblyBatchResult):
        raise TypeError("assembly_result must be NBAPlayerPointsAssemblyBatchResult")
    if not isinstance(source_manifest_preview, NBAPlayerPointsSourceManifestPreview):
        raise TypeError("source_manifest_preview must be NBAPlayerPointsSourceManifestPreview")
    if not assembly_result.rows:
        raise NBAPlayerPointsEvidenceError("assembly_result rows must not be empty")
    if (
        source_manifest_preview.to_dict()
        != assembly_result.source_manifest_preview.to_dict()
    ):
        raise NBAPlayerPointsEvidenceError(
            "source_manifest_preview must match the assembly batch source manifest"
        )
    if source_manifest_preview.repository_commit_sha != repository_commit_sha:
        raise NBAPlayerPointsEvidenceError("source manifest repository_commit_sha mismatch")
    if source_manifest_preview.manifest_schema_version != NBA_PLAYER_POINTS_SOURCE_MANIFEST_SCHEMA_VERSION:
        raise NBAPlayerPointsEvidenceError("unsupported source_manifest_schema_version")
    if config.research_label != NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL:
        raise NBAPlayerPointsEvidenceError("unsupported research_label")
    return source_manifest_preview


def _validate_path_identity_payloads(
    rows: Sequence[Mapping[str, object]],
    source_manifest: NBAPlayerPointsSourceManifestPreview,
) -> None:
    _require_safe_path_component(source_manifest.prediction_run_id, "prediction_run_id")
    if source_manifest.operating_date is not None:
        _require_operating_date(source_manifest.operating_date, "operating_date")
    for row in rows:
        _require_safe_path_component(row.get("prediction_run_id"), "prediction_run_id")
        if row.get("operating_date") not in (None, ""):
            _require_operating_date(row.get("operating_date"), "operating_date")


def _run_identity(
    rows: Sequence[Mapping[str, object]],
    source_manifest: NBAPlayerPointsSourceManifestPreview,
    repository_commit_sha: str,
    config: NBAPlayerPointsEvidenceWriterConfig,
) -> Mapping[str, object]:
    prediction_run_ids = _unique_values(rows, "prediction_run_id")
    if prediction_run_ids != {source_manifest.prediction_run_id}:
        raise NBAPlayerPointsEvidenceError("prediction_run_id mismatch")
    model_ids = _unique_values(rows, "model_id")
    feature_versions = _unique_values(rows, "feature_schema_version")
    assembly_versions = _unique_values(rows, "assembly_schema_version")
    source_manifest_ids = _unique_values(rows, "source_manifest_id")
    research_labels = _unique_values(rows, "research_only_label")
    operating_dates = {
        str(value)
        for value in (
            row.get("operating_date") for row in rows if row.get("operating_date") not in (None, "")
        )
    }
    if source_manifest.operating_date is not None:
        operating_dates.add(source_manifest.operating_date.isoformat())
    if len(model_ids) != 1:
        raise NBAPlayerPointsEvidenceError("exactly one model_id is required")
    if feature_versions != {NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION}:
        raise NBAPlayerPointsEvidenceError("unsupported feature_schema_version")
    if assembly_versions != {NBA_PLAYER_POINTS_ASSEMBLY_SCHEMA_VERSION}:
        raise NBAPlayerPointsEvidenceError("unsupported assembly_schema_version")
    if len(source_manifest_ids) != 1:
        raise NBAPlayerPointsEvidenceError("exactly one source_manifest_id is required")
    if research_labels != {config.research_label}:
        raise NBAPlayerPointsEvidenceError("research_label mismatch")
    if len(operating_dates) != 1:
        raise NBAPlayerPointsEvidenceError("exactly one operating_date is required")
    prediction_run_id = _require_safe_path_component(
        source_manifest.prediction_run_id,
        "prediction_run_id",
    )
    operating_date = _require_operating_date(next(iter(operating_dates)), "operating_date")
    return MappingProxyType(
        {
            "evidence_schema_version": config.evidence_schema_version,
            "prediction_run_id": prediction_run_id,
            "operating_date": operating_date,
            "repository_commit_sha": repository_commit_sha,
            "research_label": config.research_label,
            "model_id": next(iter(model_ids)),
            "feature_schema_version": next(iter(feature_versions)),
            "assembly_schema_version": next(iter(assembly_versions)),
            "source_manifest_id": next(iter(source_manifest_ids)),
            "source_manifest_hash": source_manifest.manifest_hash,
            "source_manifest_schema_version": source_manifest.manifest_schema_version,
        }
    )


def _prepare_run_payloads(
    rows: Sequence[Mapping[str, object]],
    assembly_result: NBAPlayerPointsAssemblyBatchResult,
    source_manifest: NBAPlayerPointsSourceManifestPreview,
    ledger_index: Mapping[str, Mapping[str, object]],
    *,
    config: NBAPlayerPointsEvidenceWriterConfig,
) -> _PreparedRunPayloads:
    prediction_rows: list[Mapping[str, object]] = []
    excluded_rows: list[Mapping[str, object]] = []
    quarantined_rows: list[Mapping[str, object]] = []
    conflicting_rows: list[Mapping[str, object]] = []
    duplicate_diagnostics: list[Mapping[str, object]] = [
        {
            **diagnostic.to_dict(),
            "diagnostic_scope": "assembly",
        }
        for diagnostic in assembly_result.duplicate_diagnostics
    ]
    conflict_diagnostics: list[Mapping[str, object]] = []

    eligible_records_by_prediction_id: dict[str, Mapping[str, object]] = {}
    eligible_rows_by_prediction_id: dict[str, Mapping[str, object]] = {}
    batch_conflicting_prediction_ids: set[str] = set()

    for row in rows:
        status = str(row["assembly_status"])
        if status in _ELIGIBLE_ASSEMBLY_STATUSES:
            _validate_projection_ledger_eligibility(row)
            ledger_record = _ledger_record_from_row(row, config=config)
            prediction_id = str(row["prediction_id"])
            existing = eligible_records_by_prediction_id.get(prediction_id)
            if existing is None:
                eligible_records_by_prediction_id[prediction_id] = ledger_record
                eligible_rows_by_prediction_id[prediction_id] = row
                continue
            if _canonical_json_text(existing) == _canonical_json_text(ledger_record):
                duplicate_diagnostics.append(
                    MappingProxyType(
                        {
                            "diagnostic_scope": "evidence_writer",
                            "prediction_id": prediction_id,
                            "duplicate_status": "identical_collapsed",
                            "record_hashes": [ledger_record["ledger_record_hash"]],
                            "reason": "identical ledger content collapsed idempotently",
                        }
                    )
                )
                continue
            batch_conflicting_prediction_ids.add(prediction_id)
            duplicate_diagnostics.append(
                MappingProxyType(
                    {
                        "diagnostic_scope": "evidence_writer",
                        "prediction_id": prediction_id,
                        "duplicate_status": "conflicting",
                        "record_hashes": [
                            existing["ledger_record_hash"],
                            ledger_record["ledger_record_hash"],
                        ],
                        "reason": "same prediction ID has conflicting ledger content",
                    }
                )
            )
            continue
        if status == "excluded":
            excluded_rows.append(row)
        elif status == "quarantined":
            quarantined_rows.append(row)
        elif status == "conflicting":
            conflicting_rows.append(row)
        else:
            raise NBAPlayerPointsEvidenceError(f"unsupported assembly_status: {status!r}")

    ledger_records_to_append: list[Mapping[str, object]] = []
    ledger_records_for_run: list[Mapping[str, object]] = []
    for prediction_id in sorted(eligible_records_by_prediction_id):
        row = eligible_rows_by_prediction_id[prediction_id]
        ledger_record = eligible_records_by_prediction_id[prediction_id]
        if prediction_id in batch_conflicting_prediction_ids:
            conflict = _conflict_row(
                row,
                scope="within_batch",
                reason="within-batch conflicting duplicate prediction_id",
            )
            conflicting_rows.append(conflict)
            conflict_diagnostics.append(
                MappingProxyType(
                    {
                        "prediction_id": prediction_id,
                        "conflict_scope": "within_batch",
                        "reason": "within-batch conflicting duplicate prediction_id",
                    }
                )
            )
            continue
        existing_ledger = ledger_index.get(prediction_id)
        if existing_ledger is None:
            ledger_records_to_append.append(ledger_record)
            ledger_records_for_run.append(ledger_record)
            prediction_rows.append(row)
            continue
        if _canonical_json_text(existing_ledger["record"]) == _canonical_json_text(ledger_record):
            duplicate_diagnostics.append(
                MappingProxyType(
                    {
                        "diagnostic_scope": "ledger",
                        "prediction_id": prediction_id,
                        "duplicate_status": "identical_replay",
                        "record_hashes": [ledger_record["ledger_record_hash"]],
                        "existing_evidence_reference": existing_ledger["evidence_reference"],
                        "reason": "existing ledger row is byte-equivalent canonical content",
                    }
                )
            )
            ledger_records_for_run.append(ledger_record)
            prediction_rows.append(row)
            continue
        conflict = _conflict_row(
            row,
            scope="ledger",
            reason="existing ledger prediction_id has conflicting canonical content",
            existing=existing_ledger["record"],
        )
        conflicting_rows.append(conflict)
        conflict_diagnostics.append(
            MappingProxyType(
                {
                    "prediction_id": prediction_id,
                    "conflict_scope": "ledger",
                    "reason": "existing ledger prediction_id has conflicting canonical content",
                    "existing_evidence_reference": existing_ledger["evidence_reference"],
                }
            )
        )

    return _PreparedRunPayloads(
        source_manifest_payload=source_manifest.to_dict(),
        prediction_rows=tuple(_canonical_sort_payloads(prediction_rows)),
        excluded_rows=tuple(_canonical_sort_payloads(excluded_rows)),
        quarantined_rows=tuple(_canonical_sort_payloads(quarantined_rows)),
        conflicting_rows=tuple(_canonical_sort_payloads(conflicting_rows)),
        duplicate_diagnostics=tuple(_canonical_sort_payloads(duplicate_diagnostics)),
        conflict_diagnostics=tuple(_canonical_sort_payloads(conflict_diagnostics)),
        ledger_records_for_run=tuple(_canonical_sort_payloads(ledger_records_for_run)),
        ledger_records_to_append=tuple(_canonical_sort_payloads(ledger_records_to_append)),
    )


def _run_file_blobs(prepared: _PreparedRunPayloads) -> Mapping[str, bytes]:
    return MappingProxyType(
        {
            "source_manifest_preview.json": _json_file_bytes(prepared.source_manifest_payload),
            "prediction_rows.jsonl": _jsonl_bytes(prepared.prediction_rows),
            "excluded_rows.jsonl": _jsonl_bytes(prepared.excluded_rows),
            "quarantined_rows.jsonl": _jsonl_bytes(prepared.quarantined_rows),
            "conflicting_rows.jsonl": _jsonl_bytes(prepared.conflicting_rows),
            "duplicate_diagnostics.json": _json_file_bytes(
                {
                    "diagnostics": list(prepared.duplicate_diagnostics),
                    "conflicts": list(prepared.conflict_diagnostics),
                }
            ),
            "integrity_report.json": _json_file_bytes(
                {
                    "status": "writing",
                    "violations": [],
                }
            ),
        }
    )


def _row_counts(prepared: _PreparedRunPayloads) -> Mapping[str, int]:
    return MappingProxyType(
        {
            "total_input_rows": (
                len(prepared.prediction_rows)
                + len(prepared.excluded_rows)
                + len(prepared.quarantined_rows)
                + len(prepared.conflicting_rows)
            ),
            "eligible_projection_rows": len(prepared.prediction_rows),
            "eligible_probability_rows": sum(
                1 for row in prepared.prediction_rows if row.get("probability_research_eligible") is True
            ),
            "excluded_rows": len(prepared.excluded_rows),
            "quarantined_rows": len(prepared.quarantined_rows),
            "conflicting_rows": len(prepared.conflicting_rows),
        }
    )


def _run_manifest_payload(
    *,
    run_identity: Mapping[str, object],
    file_hashes: Mapping[str, str],
    row_counts: Mapping[str, int],
    duplicate_diagnostics_count: int,
    ledger_append_count: int,
    ledger_segment_file: str,
    ledger_segment_hash: str,
    ledger_record_hashes: Sequence[str],
    run_content_hash: str,
    integrity_report_hash: str,
    completion_status: str,
    created_at_utc: datetime,
    completed_at_utc: datetime | None,
    config: NBAPlayerPointsEvidenceWriterConfig,
) -> Mapping[str, object]:
    if completion_status not in NBA_PLAYER_POINTS_EVIDENCE_COMPLETION_STATUSES:
        raise NBAPlayerPointsEvidenceError(f"unsupported completion_status: {completion_status!r}")
    return MappingProxyType(
        {
            "evidence_schema_version": config.evidence_schema_version,
            "prediction_run_id": run_identity["prediction_run_id"],
            "operating_date": run_identity["operating_date"],
            "created_at_utc": _format_utc(created_at_utc),
            "completed_at_utc": _format_optional_utc(completed_at_utc),
            "repository_commit_sha": run_identity["repository_commit_sha"],
            "research_label": run_identity["research_label"],
            "model_id": run_identity["model_id"],
            "feature_schema_version": run_identity["feature_schema_version"],
            "assembly_schema_version": run_identity["assembly_schema_version"],
            "source_manifest_id": run_identity["source_manifest_id"],
            "source_manifest_hash": run_identity["source_manifest_hash"],
            "source_manifest_schema_version": run_identity["source_manifest_schema_version"],
            "total_input_rows": int(row_counts["total_input_rows"]),
            "eligible_projection_rows": int(row_counts["eligible_projection_rows"]),
            "eligible_probability_rows": int(row_counts["eligible_probability_rows"]),
            "excluded_rows": int(row_counts["excluded_rows"]),
            "quarantined_rows": int(row_counts["quarantined_rows"]),
            "conflicting_rows": int(row_counts["conflicting_rows"]),
            "duplicate_diagnostics_count": int(duplicate_diagnostics_count),
            "source_manifest_preview_file": "source_manifest_preview.json",
            "source_manifest_preview_hash": file_hashes.get("source_manifest_preview.json", ""),
            "prediction_rows_file": "prediction_rows.jsonl",
            "prediction_rows_hash": file_hashes.get("prediction_rows.jsonl", ""),
            "excluded_rows_file": "excluded_rows.jsonl",
            "excluded_rows_hash": file_hashes.get("excluded_rows.jsonl", ""),
            "quarantined_rows_file": "quarantined_rows.jsonl",
            "quarantined_rows_hash": file_hashes.get("quarantined_rows.jsonl", ""),
            "conflicting_rows_file": "conflicting_rows.jsonl",
            "conflicting_rows_hash": file_hashes.get("conflicting_rows.jsonl", ""),
            "duplicate_diagnostics_file": "duplicate_diagnostics.json",
            "duplicate_diagnostics_hash": file_hashes.get("duplicate_diagnostics.json", ""),
            "integrity_report_file": "integrity_report.json",
            "integrity_report_hash": integrity_report_hash,
            "ledger_schema_version": config.ledger_schema_version,
            "ledger_segment_file": ledger_segment_file,
            "ledger_segment_hash": ledger_segment_hash,
            "ledger_record_hashes": list(ledger_record_hashes),
            "ledger_append_count": int(ledger_append_count),
            "run_content_hash": run_content_hash,
            "completion_status": completion_status,
        }
    )


def _run_integrity_payload(
    *,
    status: str,
    run_identity: Mapping[str, object],
    file_hashes: Mapping[str, str],
    row_counts: Mapping[str, int],
    duplicate_diagnostics: Sequence[Mapping[str, object]],
    conflict_diagnostics: Sequence[Mapping[str, object]],
    ledger_append_count: int,
    ledger_segment_file: str,
    ledger_segment_hash: str,
    ledger_record_hashes: Sequence[str],
    run_content_hash: str,
) -> Mapping[str, object]:
    return MappingProxyType(
        {
            "status": status,
            "violations": [],
            "run_identity": dict(run_identity),
            "file_hashes": dict(file_hashes),
            "row_counts": dict(row_counts),
            "duplicate_diagnostics_count": len(duplicate_diagnostics),
            "conflict_diagnostics_count": len(conflict_diagnostics),
            "ledger_append_count": ledger_append_count,
            "ledger_segment_file": ledger_segment_file,
            "ledger_segment_hash": ledger_segment_hash,
            "ledger_record_hashes": list(ledger_record_hashes),
            "run_content_hash": run_content_hash,
        }
    )


def _run_content_hash(
    *,
    run_identity: Mapping[str, object],
    file_hashes: Mapping[str, str],
    row_counts: Mapping[str, int],
    config: NBAPlayerPointsEvidenceWriterConfig,
) -> str:
    payload = {
        "evidence_schema_version": config.evidence_schema_version,
        "ledger_schema_version": config.ledger_schema_version,
        "run_identity": dict(run_identity),
        "files": [
            {
                "file_name": name,
                "sha256": file_hashes[name],
            }
            for name in (
                "source_manifest_preview.json",
                "prediction_rows.jsonl",
                "excluded_rows.jsonl",
                "quarantined_rows.jsonl",
                "conflicting_rows.jsonl",
                "duplicate_diagnostics.json",
            )
        ],
        "counts": dict(row_counts),
    }
    return _canonical_payload_sha256(payload)


def _verify_run_directory(
    run_dir: Path,
    config: NBAPlayerPointsEvidenceWriterConfig,
) -> Mapping[str, object]:
    violations: list[str] = []
    blocked_prediction_ids: set[str] = set()
    manifest: Mapping[str, object] | None = None
    marker: Mapping[str, object] | None = None

    manifest_path = run_dir / NBA_PLAYER_POINTS_RUN_MANIFEST_FILE
    marker_path = run_dir / config.completion_marker_file_name
    if run_dir.is_symlink():
        violations.append(f"{run_dir}: run directory is a symlink")
    if manifest_path.is_symlink():
        violations.append(f"{run_dir}: run_manifest.json is a symlink")
    elif not manifest_path.exists():
        violations.append(f"{run_dir}: missing run_manifest.json")
    else:
        try:
            manifest = _read_json_file(manifest_path)
            _validate_manifest_payload(manifest)
        except NBAPlayerPointsEvidenceError as exc:
            violations.append(f"{run_dir}: {exc}")
    if marker_path.is_symlink():
        violations.append(f"{run_dir}: completion marker is a symlink")
    elif not marker_path.exists():
        violations.append(f"{run_dir}: missing completion marker")
    else:
        try:
            marker = _read_json_file(marker_path)
        except NBAPlayerPointsEvidenceError as exc:
            violations.append(f"{run_dir}: {exc}")

    if manifest is None:
        return MappingProxyType(
            {
                "run_directory": str(run_dir),
                "manifest": {},
                "violations": tuple(violations),
                "blocked_prediction_ids": tuple(blocked_prediction_ids),
            }
        )

    if marker is not None:
        expected_manifest_hash = _sha256_file(manifest_path)
        if marker.get("run_manifest_hash") != expected_manifest_hash:
            violations.append(f"{run_dir}: completion marker run_manifest_hash mismatch")

    expected_files = {
        "source_manifest_preview_file": "source_manifest_preview_hash",
        "prediction_rows_file": "prediction_rows_hash",
        "excluded_rows_file": "excluded_rows_hash",
        "quarantined_rows_file": "quarantined_rows_hash",
        "conflicting_rows_file": "conflicting_rows_hash",
        "duplicate_diagnostics_file": "duplicate_diagnostics_hash",
        "integrity_report_file": "integrity_report_hash",
    }
    file_hashes: dict[str, str] = {}
    for file_field, hash_field in expected_files.items():
        file_name = manifest.get(file_field)
        if not isinstance(file_name, str) or not file_name:
            violations.append(f"{run_dir}: {file_field} is missing")
            continue
        file_path = Path(file_name)
        if file_path.is_absolute() or ".." in file_path.parts or len(file_path.parts) != 1:
            violations.append(f"{run_dir}: {file_field} is not a safe file name")
            continue
        path = run_dir / file_name
        if path.is_symlink():
            violations.append(f"{run_dir}: expected file is a symlink: {file_name}")
            continue
        if not path.exists():
            violations.append(f"{run_dir}: expected file missing: {file_name}")
            continue
        digest = _sha256_file(path)
        file_hashes[file_name] = digest
        if manifest.get(hash_field) != digest:
            violations.append(f"{run_dir}: {file_name} hash mismatch")

    rows_by_file: dict[str, tuple[Mapping[str, object], ...]] = {}
    for file_name in (
        "prediction_rows.jsonl",
        "excluded_rows.jsonl",
        "quarantined_rows.jsonl",
        "conflicting_rows.jsonl",
    ):
        path = run_dir / file_name
        if path.is_symlink():
            rows_by_file[file_name] = ()
        elif path.exists():
            try:
                rows_by_file[file_name] = _read_jsonl(path)
            except NBAPlayerPointsEvidenceError as exc:
                violations.append(f"{run_dir}: {exc}")
                rows_by_file[file_name] = ()
        else:
            rows_by_file[file_name] = ()

    expected_counts = {
        "eligible_projection_rows": len(rows_by_file["prediction_rows.jsonl"]),
        "eligible_probability_rows": sum(
            1
            for row in rows_by_file["prediction_rows.jsonl"]
            if row.get("probability_research_eligible") is True
        ),
        "excluded_rows": len(rows_by_file["excluded_rows.jsonl"]),
        "quarantined_rows": len(rows_by_file["quarantined_rows.jsonl"]),
        "conflicting_rows": len(rows_by_file["conflicting_rows.jsonl"]),
    }
    expected_counts["total_input_rows"] = sum(
        expected_counts[key]
        for key in (
            "eligible_projection_rows",
            "excluded_rows",
            "quarantined_rows",
            "conflicting_rows",
        )
    )
    for field_name, expected in expected_counts.items():
        if manifest.get(field_name) != expected:
            violations.append(f"{run_dir}: {field_name} count mismatch")

    source_manifest_path = run_dir / "source_manifest_preview.json"
    if not source_manifest_path.is_symlink():
        try:
            source_manifest = _read_json_file(source_manifest_path)
            _validate_source_manifest_payload(
                source_manifest,
                source_manifest_hash=str(manifest["source_manifest_hash"]),
            )
            if source_manifest.get("prediction_run_id") != manifest["prediction_run_id"]:
                violations.append(f"{run_dir}: source manifest prediction_run_id mismatch")
            if source_manifest.get("operating_date") != manifest["operating_date"]:
                violations.append(f"{run_dir}: source manifest operating_date mismatch")
            if source_manifest.get("repository_commit_sha") != manifest["repository_commit_sha"]:
                violations.append(f"{run_dir}: source manifest repository_commit_sha mismatch")
        except NBAPlayerPointsEvidenceError as exc:
            violations.append(f"{run_dir}: {exc}")

    run_identity = {
        "evidence_schema_version": manifest["evidence_schema_version"],
        "prediction_run_id": manifest["prediction_run_id"],
        "operating_date": manifest["operating_date"],
        "repository_commit_sha": manifest["repository_commit_sha"],
        "research_label": manifest["research_label"],
        "model_id": manifest["model_id"],
        "feature_schema_version": manifest["feature_schema_version"],
        "assembly_schema_version": manifest["assembly_schema_version"],
        "source_manifest_id": manifest["source_manifest_id"],
        "source_manifest_hash": manifest["source_manifest_hash"],
        "source_manifest_schema_version": manifest["source_manifest_schema_version"],
    }
    hash_inputs = {
        name: file_hashes[name]
        for name in (
            "source_manifest_preview.json",
            "prediction_rows.jsonl",
            "excluded_rows.jsonl",
            "quarantined_rows.jsonl",
            "conflicting_rows.jsonl",
            "duplicate_diagnostics.json",
        )
        if name in file_hashes
    }
    if len(hash_inputs) == 6:
        recomputed = _run_content_hash(
            run_identity=run_identity,
            file_hashes=hash_inputs,
            row_counts={
                "total_input_rows": expected_counts["total_input_rows"],
                "eligible_projection_rows": expected_counts["eligible_projection_rows"],
                "eligible_probability_rows": expected_counts["eligible_probability_rows"],
                "excluded_rows": expected_counts["excluded_rows"],
                "quarantined_rows": expected_counts["quarantined_rows"],
                "conflicting_rows": expected_counts["conflicting_rows"],
            },
            config=config,
        )
        if manifest.get("run_content_hash") != recomputed:
            violations.append(f"{run_dir}: run_content_hash mismatch")

    all_rows = tuple(row for rows in rows_by_file.values() for row in rows)
    for row in all_rows:
        try:
            _validate_assembled_payload(
                row,
                source_manifest_hash=str(manifest["source_manifest_hash"]),
                repository_commit_sha=str(manifest["repository_commit_sha"]),
            )
        except NBAPlayerPointsEvidenceError as exc:
            violations.append(f"{run_dir}: assembled row verification failed: {exc}")
        if _contains_prohibited_prediction_field(row):
            violations.append(f"{run_dir}: prediction row contains prohibited field")

    for file_name in (
        "excluded_rows.jsonl",
        "quarantined_rows.jsonl",
        "conflicting_rows.jsonl",
    ):
        blocked_prediction_ids.update(
            str(row.get("prediction_id"))
            for row in rows_by_file[file_name]
            if row.get("prediction_id") not in (None, "")
        )

    return MappingProxyType(
        {
            "run_directory": str(run_dir),
            "manifest": dict(manifest),
            "violations": tuple(violations),
            "blocked_prediction_ids": tuple(sorted(blocked_prediction_ids)),
        }
    )


def _verify_completed_run_ledger_references(
    evidence_root: Path,
    run_dir: Path,
    manifest: Mapping[str, object],
    ledger_scan: _LedgerScan,
    config: NBAPlayerPointsEvidenceWriterConfig,
) -> tuple[str, ...]:
    violations: list[str] = []
    prediction_run_id = str(manifest["prediction_run_id"])
    expected_count = int(manifest["ledger_append_count"])
    segment = ledger_scan.segments_by_run_id.get(prediction_run_id)
    ledger_segment_file = str(manifest.get("ledger_segment_file") or "")
    ledger_record_hashes = tuple(str(item) for item in manifest.get("ledger_record_hashes", ()))

    if expected_count == 0:
        if ledger_segment_file or manifest.get("ledger_segment_hash") or ledger_record_hashes:
            violations.append(f"{run_dir}: zero-ledger run has ledger references")
        if segment is not None and segment.rows:
            violations.append(f"{run_dir}: zero-ledger run has a ledger segment")
        return tuple(violations)

    if not ledger_segment_file:
        violations.append(f"{run_dir}: missing ledger reference")
        return tuple(violations)

    segment_path = evidence_root / ledger_segment_file
    try:
        _ensure_under_root(evidence_root, segment_path, "ledger_segment_file")
    except NBAPlayerPointsEvidenceError as exc:
        violations.append(f"{run_dir}: {exc}")
        return tuple(violations)
    if segment_path.is_symlink():
        violations.append(f"{run_dir}: ledger segment reference is a symlink")
        return tuple(violations)
    if segment is None:
        violations.append(f"{run_dir}: missing ledger segment for completed run")
        return tuple(violations)
    if segment.segment_path.resolve(strict=False) != segment_path.resolve(strict=False):
        violations.append(f"{run_dir}: ledger segment path mismatch")
    if segment.segment_hash != manifest.get("ledger_segment_hash"):
        violations.append(f"{run_dir}: ledger segment hash mismatch")
    segment_record_hashes = tuple(str(row["ledger_record_hash"]) for row in segment.rows)
    if ledger_record_hashes != segment_record_hashes:
        violations.append(f"{run_dir}: ledger record hash list mismatch")
    if expected_count != len(segment.rows):
        violations.append(f"{run_dir}: ledger_append_count mismatch")

    try:
        prediction_rows = _read_jsonl(run_dir / "prediction_rows.jsonl")
        expected_records = tuple(
            _ledger_record_from_row(row, config=config) for row in prediction_rows
        )
    except NBAPlayerPointsEvidenceError as exc:
        violations.append(f"{run_dir}: unable to rebuild ledger records: {exc}")
        return tuple(violations)

    expected_record_hashes = tuple(
        str(record["ledger_record_hash"]) for record in expected_records
    )
    if expected_record_hashes != ledger_record_hashes:
        violations.append(f"{run_dir}: prediction rows do not match ledger record hashes")
    if tuple(_canonical_json_text(row) for row in segment.rows) != tuple(
        _canonical_json_text(record) for record in expected_records
    ):
        violations.append(f"{run_dir}: ledger segment content mismatch")
    return tuple(violations)


def _read_existing_completed_manifest(
    run_directory: Path,
    evidence_root: Path,
    config: NBAPlayerPointsEvidenceWriterConfig,
) -> Mapping[str, object]:
    if run_directory.is_symlink():
        raise NBAPlayerPointsEvidenceError(
            f"prediction-run directory is a symlink: {run_directory}"
        )
    if not (run_directory / NBA_PLAYER_POINTS_RUN_MANIFEST_FILE).exists() or not (
        run_directory / config.completion_marker_file_name
    ).exists():
        raise NBAPlayerPointsEvidenceError(
            "prediction-run directory already exists without completion marker"
        )
    report = _verify_run_directory(run_directory, config)
    if report["violations"]:
        raise NBAPlayerPointsEvidenceError(
            "existing completed run failed verification: "
            + "; ".join(str(item) for item in report["violations"])
        )
    manifest = report["manifest"]
    _ensure_under_root(evidence_root, run_directory, "run_directory")
    if manifest.get("completion_status") not in {"complete", "conflicting"}:
        raise NBAPlayerPointsEvidenceError("existing run is not complete")
    return MappingProxyType(dict(manifest))


def _validate_manifest_payload(payload: Mapping[str, object]) -> None:
    missing = [field for field in _MANIFEST_REQUIRED_FIELDS if field not in payload]
    if missing:
        raise NBAPlayerPointsEvidenceError(f"manifest missing fields: {','.join(missing)}")
    if payload["evidence_schema_version"] != NBA_PLAYER_POINTS_EVIDENCE_SCHEMA_VERSION:
        raise NBAPlayerPointsEvidenceError("unsupported evidence_schema_version")
    if payload["ledger_schema_version"] != NBA_PLAYER_POINTS_LEDGER_SCHEMA_VERSION:
        raise NBAPlayerPointsEvidenceError("unsupported ledger_schema_version")
    if payload["completion_status"] not in NBA_PLAYER_POINTS_EVIDENCE_COMPLETION_STATUSES:
        raise NBAPlayerPointsEvidenceError("unsupported completion_status")
    _require_sha256(payload["source_manifest_hash"], "source_manifest_hash")
    if payload["run_content_hash"]:
        _require_sha256(payload["run_content_hash"], "run_content_hash")
    if payload["ledger_segment_hash"]:
        _require_sha256(payload["ledger_segment_hash"], "ledger_segment_hash")
    record_hashes = payload.get("ledger_record_hashes")
    if not isinstance(record_hashes, list):
        raise NBAPlayerPointsEvidenceError("ledger_record_hashes must be a list")
    for index, record_hash in enumerate(record_hashes):
        _require_sha256(record_hash, f"ledger_record_hashes[{index}]")
    segment_file = payload.get("ledger_segment_file")
    if segment_file:
        if not isinstance(segment_file, str):
            raise NBAPlayerPointsEvidenceError("ledger_segment_file must be a string")
        segment_path = Path(segment_file)
        if segment_path.is_absolute() or ".." in segment_path.parts:
            raise NBAPlayerPointsEvidenceError("ledger_segment_file must stay under evidence root")
    for field_name in _MANIFEST_REQUIRED_FIELDS:
        if field_name.endswith("_hash") and payload[field_name]:
            _require_sha256(payload[field_name], field_name)


def _validate_source_manifest_payload(
    payload: Mapping[str, object],
    *,
    source_manifest_hash: str,
) -> None:
    if payload.get("manifest_schema_version") != NBA_PLAYER_POINTS_SOURCE_MANIFEST_SCHEMA_VERSION:
        raise NBAPlayerPointsEvidenceError("unsupported source manifest schema")
    declared = _require_sha256(payload.get("manifest_hash"), "manifest_hash")
    if declared != source_manifest_hash:
        raise NBAPlayerPointsEvidenceError("source manifest hash mismatch")
    recomputed_payload = dict(payload)
    recomputed_payload.pop("manifest_hash", None)
    if _canonical_payload_sha256(recomputed_payload) != declared:
        raise NBAPlayerPointsEvidenceError("source manifest hash does not recompute")


def _validate_assembled_payload(
    payload: Mapping[str, object],
    *,
    source_manifest_hash: str,
    repository_commit_sha: str,
) -> None:
    if payload.get("schema_version") != NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION:
        raise NBAPlayerPointsEvidenceError("unsupported prediction schema_version")
    if payload.get("assembly_schema_version") != NBA_PLAYER_POINTS_ASSEMBLY_SCHEMA_VERSION:
        raise NBAPlayerPointsEvidenceError("unsupported assembly_schema_version")
    if payload.get("source_manifest_hash") != source_manifest_hash:
        raise NBAPlayerPointsEvidenceError("source_manifest_hash mismatch")
    if payload.get("repository_commit_sha") != repository_commit_sha:
        raise NBAPlayerPointsEvidenceError("repository_commit_sha mismatch")
    if payload.get("artifact_hash") != payload.get("assembled_record_hash"):
        raise NBAPlayerPointsEvidenceError("artifact_hash must match assembled_record_hash")
    assembled_hash = _require_sha256(payload.get("assembled_record_hash"), "assembled_record_hash")
    hash_payload = {
        key: value
        for key, value in payload.items()
        if key not in _ASSEMBLED_HASH_EXCLUDED_FIELDS
    }
    if _canonical_payload_sha256(hash_payload) != assembled_hash:
        raise NBAPlayerPointsEvidenceError("assembled_record_hash does not recompute")
    if _expected_prediction_id(payload) != payload.get("prediction_id"):
        raise NBAPlayerPointsEvidenceError("prediction_id does not recompute")


def _expected_prediction_id(payload: Mapping[str, object]) -> str:
    return generate_preview_prediction_id(
        prediction_run_id=str(payload["prediction_run_id"]),
        canonical_event_id=_optional_text(payload.get("canonical_event_id")),
        provider_event_id=str(payload.get("provider_event_id") or "unresolved-event"),
        player_id=_optional_text(payload.get("player_id")),
        provider_player_name=str(payload.get("player_name") or "unknown-player"),
        sportsbook=_optional_text(payload.get("sportsbook")),
        market=_optional_text(payload.get("market")),
        line=_optional_float(payload.get("line")),
        american_odds=_optional_int(payload.get("american_odds")),
        prediction_timestamp_utc=str(payload["prediction_timestamp_utc"]),
        model_id=str(payload["model_id"]),
    )


def _validate_projection_ledger_eligibility(row: Mapping[str, object]) -> None:
    required_present = (
        "prediction_id",
        "prediction_run_id",
        "canonical_event_id",
        "player_id",
        "sportsbook",
        "market",
        "line",
        "american_odds",
        "decimal_odds",
        "source_manifest_hash",
        "assembled_record_hash",
        "repository_commit_sha",
        "feature_schema_version",
        "assembly_schema_version",
    )
    for field_name in required_present:
        if row.get(field_name) in (None, ""):
            raise NBAPlayerPointsEvidenceError(
                f"eligible ledger row missing {field_name}"
            )
    if row.get("assembly_status") not in _ELIGIBLE_ASSEMBLY_STATUSES:
        raise NBAPlayerPointsEvidenceError("assembly_status is not projection eligible")
    if row.get("projection_research_eligible") is not True:
        raise NBAPlayerPointsEvidenceError("projection_research_eligible must be true")
    if row.get("market") != NBA_PLAYER_POINTS_MARKET:
        raise NBAPlayerPointsEvidenceError("market must be player_points")
    if row.get("market_status") != "valid":
        raise NBAPlayerPointsEvidenceError("market evidence did not pass cutoff checks")
    if row.get("minutes_status") != "projected":
        raise NBAPlayerPointsEvidenceError("minutes evidence did not pass cutoff checks")
    if row.get("projection_status") != "valid":
        raise NBAPlayerPointsEvidenceError("projection evidence did not pass cutoff checks")
    prediction_time = _coerce_utc_datetime(
        row.get("prediction_timestamp_utc"),
        "prediction_timestamp_utc",
    )
    for timestamp_field in ("commence_time_utc", "market_timestamp_utc", "feature_timestamp_utc"):
        if row.get(timestamp_field) not in (None, ""):
            parsed = _coerce_utc_datetime(row[timestamp_field], timestamp_field)
            if timestamp_field != "commence_time_utc" and row.get("commence_time_utc") not in (None, ""):
                commence = _coerce_utc_datetime(row["commence_time_utc"], "commence_time_utc")
                if parsed >= commence:
                    raise NBAPlayerPointsEvidenceError(
                        f"{timestamp_field} must be before tipoff"
                    )
    if row.get("commence_time_utc") not in (None, ""):
        commence = _coerce_utc_datetime(row["commence_time_utc"], "commence_time_utc")
        if prediction_time >= commence:
            raise NBAPlayerPointsEvidenceError(
                "prediction_timestamp_utc must be before tipoff"
            )


def _ledger_record_from_row(
    row: Mapping[str, object],
    *,
    config: NBAPlayerPointsEvidenceWriterConfig,
) -> Mapping[str, object]:
    probability_status = str(row.get("probability_status") or "unavailable")
    if probability_status == "unavailable":
        over = None
        under = None
        probability_model_id = None
    else:
        over = row.get("model_over_probability")
        under = row.get("model_under_probability")
        probability_model_id = row.get("probability_model_id")
    record = {
        "ledger_schema_version": config.ledger_schema_version,
        "prediction_id": row["prediction_id"],
        "prediction_run_id": row["prediction_run_id"],
        "model_id": row["model_id"],
        "canonical_event_id": row["canonical_event_id"],
        "provider_event_id": row["provider_event_id"],
        "player_id": row["player_id"],
        "canonical_player_name": row["player_name"],
        "normalized_player_name": row["normalized_player_name"],
        "team": row["team"],
        "opponent": row["opponent"],
        "operating_date": row["operating_date"],
        "commence_time_utc": row["commence_time_utc"],
        "sportsbook": row["sportsbook"],
        "market": row["market"],
        "line": row["line"],
        "american_odds": row["american_odds"],
        "decimal_odds": row["decimal_odds"],
        "implied_probability": row["implied_probability"],
        "projected_points": row["projected_points"],
        "projected_minutes": row["projected_minutes"],
        "projected_minutes_low": None,
        "projected_minutes_high": None,
        "minutes_confidence": None,
        "model_over_probability": over,
        "model_under_probability": under,
        "probability_status": probability_status,
        "probability_model_id": probability_model_id,
        "projection_line_difference": row["projection_line_difference"],
        "prediction_timestamp_utc": row["prediction_timestamp_utc"],
        "market_timestamp_utc": row["market_timestamp_utc"],
        "feature_timestamp_utc": row["feature_timestamp_utc"],
        "projection_timestamp_utc": None,
        "identity_status": row["identity_status"],
        "identity_source": row["identity_source"],
        "identity_conflict_reason": row["identity_conflict_reason"],
        "eligibility_status": row["eligibility_status"],
        "assembly_status": row["assembly_status"],
        "assembly_exclusion_reason": row["assembly_exclusion_reason"],
        "projection_research_eligible": row["projection_research_eligible"],
        "probability_research_eligible": row["probability_research_eligible"],
        "market_status": row["market_status"],
        "minutes_status": row["minutes_status"],
        "projection_status": row["projection_status"],
        "feature_schema_version": row["feature_schema_version"],
        "assembly_schema_version": row["assembly_schema_version"],
        "repository_commit_sha": row["repository_commit_sha"],
        "source_manifest_id": row["source_manifest_id"],
        "source_manifest_hash": row["source_manifest_hash"],
        "source_hashes": row["source_hashes"],
        "assembled_record_hash": row["assembled_record_hash"],
        "research_label": row["research_only_label"],
    }
    if _contains_prohibited_prediction_field(record):
        raise NBAPlayerPointsEvidenceError("ledger record contains prohibited field")
    record["ledger_record_hash"] = _ledger_record_hash(record)
    return MappingProxyType(record)


def _ledger_record_field_names() -> tuple[str, ...]:
    sample = {
        "ledger_schema_version": None,
        "prediction_id": None,
        "prediction_run_id": None,
        "model_id": None,
        "canonical_event_id": None,
        "provider_event_id": None,
        "player_id": None,
        "canonical_player_name": None,
        "normalized_player_name": None,
        "team": None,
        "opponent": None,
        "operating_date": None,
        "commence_time_utc": None,
        "sportsbook": None,
        "market": None,
        "line": None,
        "american_odds": None,
        "decimal_odds": None,
        "implied_probability": None,
        "projected_points": None,
        "projected_minutes": None,
        "projected_minutes_low": None,
        "projected_minutes_high": None,
        "minutes_confidence": None,
        "model_over_probability": None,
        "model_under_probability": None,
        "probability_status": None,
        "probability_model_id": None,
        "projection_line_difference": None,
        "prediction_timestamp_utc": None,
        "market_timestamp_utc": None,
        "feature_timestamp_utc": None,
        "projection_timestamp_utc": None,
        "identity_status": None,
        "identity_source": None,
        "identity_conflict_reason": None,
        "eligibility_status": None,
        "assembly_status": None,
        "assembly_exclusion_reason": None,
        "projection_research_eligible": None,
        "probability_research_eligible": None,
        "market_status": None,
        "minutes_status": None,
        "projection_status": None,
        "feature_schema_version": None,
        "assembly_schema_version": None,
        "repository_commit_sha": None,
        "source_manifest_id": None,
        "source_manifest_hash": None,
        "source_hashes": None,
        "assembled_record_hash": None,
        "research_label": None,
        "ledger_record_hash": None,
    }
    return tuple(sample)


def _ledger_record_hash(record: Mapping[str, object]) -> str:
    payload = {key: value for key, value in record.items() if key != "ledger_record_hash"}
    return _canonical_payload_sha256(payload)


def _validate_ledger_record_payload(row: Mapping[str, object]) -> None:
    missing = [field for field in _ledger_record_field_names() if field not in row]
    if missing:
        raise NBAPlayerPointsEvidenceError(
            f"ledger record missing fields: {','.join(missing)}"
        )
    if row["ledger_schema_version"] != NBA_PLAYER_POINTS_LEDGER_SCHEMA_VERSION:
        raise NBAPlayerPointsEvidenceError("unsupported ledger_schema_version")
    if _contains_prohibited_prediction_field(row):
        raise NBAPlayerPointsEvidenceError("ledger record contains prohibited field")
    expected = _ledger_record_hash(row)
    if row["ledger_record_hash"] != expected:
        raise NBAPlayerPointsEvidenceError("ledger_record_hash mismatch")
    if row["assembly_status"] not in _ELIGIBLE_ASSEMBLY_STATUSES:
        raise NBAPlayerPointsEvidenceError("ledger row is not assembly eligible")


def _load_ledger_index(
    evidence_root: Path,
    config: NBAPlayerPointsEvidenceWriterConfig,
) -> Mapping[str, Mapping[str, object]]:
    scan = _scan_ledger_segments(evidence_root, config, completed_run_statuses={})
    if scan.violations:
        raise NBAPlayerPointsEvidenceError(
            "existing ledger segments failed verification: " + "; ".join(scan.violations)
        )
    return scan.index


def _ledger_segment_path(
    evidence_root: Path,
    run_identity: Mapping[str, object],
    config: NBAPlayerPointsEvidenceWriterConfig,
) -> Path:
    prediction_run_id = _require_safe_path_component(
        run_identity["prediction_run_id"],
        "prediction_run_id",
    )
    operating_date = _require_operating_date(run_identity["operating_date"], "operating_date")
    return (
        evidence_root
        / config.ledgers_dir_name
        / NBA_PLAYER_POINTS_LEDGER_SEGMENTS_DIR_NAME
        / operating_date
        / prediction_run_id
        / config.ledger_file_name
    )


def _ledger_segment_publication_metadata(
    evidence_root: Path,
    run_identity: Mapping[str, object],
    records: Sequence[Mapping[str, object]],
    config: NBAPlayerPointsEvidenceWriterConfig,
) -> _LedgerSegmentPublication:
    segment_path = _ledger_segment_path(evidence_root, run_identity, config)
    if not records:
        return _LedgerSegmentPublication(
            segment_path=segment_path,
            relative_path="",
            segment_hash="",
            record_hashes=(),
            record_count=0,
            newly_published_count=0,
        )
    data = _jsonl_bytes(records)
    return _LedgerSegmentPublication(
        segment_path=segment_path,
        relative_path=_relative_to_root(segment_path, evidence_root),
        segment_hash=_sha256_bytes(data),
        record_hashes=tuple(str(record["ledger_record_hash"]) for record in records),
        record_count=len(records),
        newly_published_count=0,
    )


def _publish_ledger_segment(
    evidence_root: Path,
    run_identity: Mapping[str, object],
    records: Sequence[Mapping[str, object]],
    config: NBAPlayerPointsEvidenceWriterConfig,
    *,
    failure_hook: FailureHook | None,
) -> _LedgerSegmentPublication:
    metadata = _ledger_segment_publication_metadata(
        evidence_root,
        run_identity,
        records,
        config,
    )
    if not records:
        return metadata

    expected_bytes = _jsonl_bytes(records)
    for record in records:
        _validate_ledger_record_payload(record)

    if metadata.segment_path.is_symlink() or metadata.segment_path.parent.is_symlink():
        raise NBAPlayerPointsEvidenceError(
            f"ledger segment path is a symlink: {metadata.segment_path}"
        )
    if metadata.segment_path.exists():
        existing_bytes = metadata.segment_path.read_bytes()
        if existing_bytes == expected_bytes:
            return metadata
        raise NBAPlayerPointsEvidenceError(
            f"existing ledger segment conflicts with retry: {metadata.segment_path}"
        )
    if metadata.segment_path.parent.exists():
        raise NBAPlayerPointsEvidenceError(
            f"ledger segment directory exists without matching segment: {metadata.segment_path.parent}"
        )

    date_dir = metadata.segment_path.parent.parent
    _make_directory(date_dir)
    stage_dir = date_dir / f".{run_identity['prediction_run_id']}.ledger-tmp-{uuid4().hex}"
    try:
        stage_dir.mkdir()
        stage_file = stage_dir / config.ledger_file_name
        _call_failure_hook(failure_hook, "during_ledger_append")
        _write_bytes_verified(stage_file, expected_bytes)
        stage_dir.rename(metadata.segment_path.parent)
        _call_failure_hook(failure_hook, "after_ledger_append_before_run_publication")
    except Exception:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
        raise

    return _LedgerSegmentPublication(
        segment_path=metadata.segment_path,
        relative_path=metadata.relative_path,
        segment_hash=metadata.segment_hash,
        record_hashes=metadata.record_hashes,
        record_count=metadata.record_count,
        newly_published_count=metadata.record_count,
    )


def _scan_ledger_segments(
    evidence_root: Path,
    config: NBAPlayerPointsEvidenceWriterConfig,
    *,
    completed_run_statuses: Mapping[str, str],
) -> _LedgerScan:
    ledgers_dir = evidence_root / config.ledgers_dir_name
    legacy_ledger_path = ledgers_dir / config.ledger_file_name
    segments_root = ledgers_dir / NBA_PLAYER_POINTS_LEDGER_SEGMENTS_DIR_NAME
    rows: list[Mapping[str, object]] = []
    index: dict[str, Mapping[str, object]] = {}
    segments_by_run_id: dict[str, _LedgerSegment] = {}
    violations: list[str] = []
    recoverable: list[str] = []

    if legacy_ledger_path.exists() or legacy_ledger_path.is_symlink():
        violations.append(f"unsupported legacy global ledger file: {legacy_ledger_path}")
    if not segments_root.exists():
        return _LedgerScan(
            rows=(),
            index=MappingProxyType({}),
            segments_by_run_id=MappingProxyType({}),
            violations=tuple(violations),
            recoverable_interrupted_segments=(),
        )
    if segments_root.is_symlink():
        violations.append(f"ledger segments root is a symlink: {segments_root}")
        return _LedgerScan(
            rows=(),
            index=MappingProxyType({}),
            segments_by_run_id=MappingProxyType({}),
            violations=tuple(violations),
            recoverable_interrupted_segments=(),
        )

    for date_dir in sorted(segments_root.iterdir(), key=lambda path: path.name):
        if date_dir.is_symlink():
            violations.append(f"ledger date path is a symlink: {date_dir}")
            continue
        if not date_dir.is_dir():
            violations.append(f"unexpected ledger path entry: {date_dir}")
            continue
        try:
            operating_date = _require_operating_date(date_dir.name, "operating_date")
        except NBAPlayerPointsEvidenceError as exc:
            violations.append(f"{date_dir}: {exc}")
            continue
        for run_dir in sorted(date_dir.iterdir(), key=lambda path: path.name):
            if run_dir.name.startswith("."):
                recoverable.append(str(run_dir))
                continue
            if run_dir.is_symlink():
                violations.append(f"ledger run path is a symlink: {run_dir}")
                continue
            if not run_dir.is_dir():
                violations.append(f"unexpected ledger run entry: {run_dir}")
                continue
            try:
                prediction_run_id = _require_safe_path_component(
                    run_dir.name,
                    "prediction_run_id",
                )
            except NBAPlayerPointsEvidenceError as exc:
                violations.append(f"{run_dir}: {exc}")
                continue
            segment_path = run_dir / config.ledger_file_name
            if segment_path.is_symlink():
                violations.append(f"ledger segment file is a symlink: {segment_path}")
                continue
            if not segment_path.exists():
                violations.append(f"ledger segment missing file: {segment_path}")
                continue
            try:
                segment_rows = _read_ledger_segment_jsonl(segment_path)
            except NBAPlayerPointsEvidenceError as exc:
                violations.append(str(exc))
                continue
            segment = _LedgerSegment(
                prediction_run_id=prediction_run_id,
                operating_date=operating_date,
                segment_path=segment_path,
                relative_path=_relative_to_root(segment_path, evidence_root),
                segment_hash=_sha256_file(segment_path),
                rows=segment_rows,
            )
            existing_segment = segments_by_run_id.get(prediction_run_id)
            if existing_segment is not None:
                violations.append(
                    f"duplicate ledger segment for prediction_run_id: {prediction_run_id}"
                )
                continue
            segments_by_run_id[prediction_run_id] = segment
            if completed_run_statuses.get(prediction_run_id) is None:
                recoverable.append(str(segment_path))
            elif completed_run_statuses.get(prediction_run_id) != "complete":
                violations.append(
                    f"ledger segment references non-complete run: {prediction_run_id}"
                )
            for line_number, row in enumerate(segment_rows, start=1):
                try:
                    _validate_ledger_record_payload(row)
                except NBAPlayerPointsEvidenceError as exc:
                    violations.append(f"{segment_path}:{line_number}: {exc}")
                    continue
                if row.get("prediction_run_id") != prediction_run_id:
                    violations.append(
                        f"{segment_path}:{line_number}: prediction_run_id path mismatch"
                    )
                if row.get("operating_date") != operating_date:
                    violations.append(
                        f"{segment_path}:{line_number}: operating_date path mismatch"
                    )
                prediction_id = str(row["prediction_id"])
                row_hash = str(row["ledger_record_hash"])
                if prediction_id in index:
                    violations.append(f"ledger duplicate prediction_id: {prediction_id}")
                    continue
                index[prediction_id] = MappingProxyType(
                    {
                        "record": row,
                        "evidence_reference": {
                            "ledger_path": str(segment_path),
                            "line_number": line_number,
                            "prediction_run_id": row["prediction_run_id"],
                            "ledger_record_hash": row_hash,
                            "assembled_record_hash": row["assembled_record_hash"],
                            "recoverable_interrupted": (
                                completed_run_statuses.get(prediction_run_id) is None
                            ),
                        },
                    }
                )
                rows.append(row)

    return _LedgerScan(
        rows=tuple(rows),
        index=MappingProxyType(index),
        segments_by_run_id=MappingProxyType(segments_by_run_id),
        violations=tuple(violations),
        recoverable_interrupted_segments=tuple(sorted(set(recoverable))),
    )


def _read_ledger_segment_jsonl(path: Path) -> tuple[Mapping[str, object], ...]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise NBAPlayerPointsEvidenceError(f"unable to read ledger segment: {path}") from exc
    if not data:
        return ()
    if not data.endswith(b"\n"):
        raise NBAPlayerPointsEvidenceError(
            f"incomplete ledger segment frame at {path}: missing final newline"
        )
    rows: list[Mapping[str, object]] = []
    for line_number, raw_line in enumerate(data.splitlines(), start=1):
        if not raw_line.strip():
            raise NBAPlayerPointsEvidenceError(
                f"incomplete ledger segment frame at {path}:{line_number}: empty line"
            )
        try:
            payload = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NBAPlayerPointsEvidenceError(
                f"invalid ledger segment JSON at {path}:{line_number}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise NBAPlayerPointsEvidenceError(
                f"ledger segment line must contain an object at {path}:{line_number}"
            )
        rows.append(MappingProxyType(_json_clone_mapping(payload)))
    return tuple(rows)


def _conflict_row(
    row: Mapping[str, object],
    *,
    scope: str,
    reason: str,
    existing: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    payload = dict(row)
    payload["evidence_conflict_status"] = "conflicting"
    payload["evidence_conflict_scope"] = scope
    payload["evidence_conflict_reason"] = reason
    if existing is not None:
        payload["conflicting_existing_ledger_record_hash"] = existing.get("ledger_record_hash")
        payload["conflicting_existing_assembled_record_hash"] = existing.get("assembled_record_hash")
    return MappingProxyType(payload)


def _contains_prohibited_prediction_field(payload: object) -> bool:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            text = str(key).casefold()
            if text in _PROHIBITED_PREDICTION_FIELDS:
                return True
            if any(fragment in text for fragment in _PROHIBITED_PREDICTION_FIELD_FRAGMENTS):
                return True
            if _contains_prohibited_prediction_field(value):
                return True
    elif isinstance(payload, list | tuple):
        return any(_contains_prohibited_prediction_field(item) for item in payload)
    return False


def _verify_written_files(stage_directory: Path, file_blobs: Mapping[str, bytes]) -> None:
    for file_name, expected_bytes in file_blobs.items():
        path = stage_directory / file_name
        if not path.exists():
            raise NBAPlayerPointsEvidenceError(f"{file_name} was not written")
        if path.read_bytes() != expected_bytes:
            raise NBAPlayerPointsEvidenceError(f"{file_name} bytes changed after write")


def _verify_completed_stage(
    stage_directory: Path,
    manifest: Mapping[str, object],
    config: NBAPlayerPointsEvidenceWriterConfig,
) -> None:
    for file_name in (
        NBA_PLAYER_POINTS_RUN_MANIFEST_FILE,
        config.completion_marker_file_name,
        *NBA_PLAYER_POINTS_EVIDENCE_FILES,
    ):
        if not (stage_directory / file_name).exists():
            raise NBAPlayerPointsEvidenceError(f"completed stage missing {file_name}")
    _validate_manifest_payload(manifest)


def _raise_on_same_run_ledger_conflict(
    prepared: _PreparedRunPayloads,
    run_identity: Mapping[str, object],
) -> None:
    prediction_run_id = str(run_identity["prediction_run_id"])
    for diagnostic in prepared.conflict_diagnostics:
        reference = diagnostic.get("existing_evidence_reference")
        if isinstance(reference, Mapping) and reference.get("prediction_run_id") == prediction_run_id:
            raise NBAPlayerPointsEvidenceError(
                "conflicting retry found committed ledger segment for the same prediction_run_id"
            )


def _write_bytes_verified(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    if path.read_bytes() != data:
        raise NBAPlayerPointsEvidenceError(f"short write detected: {path}")


def _make_directory(path: Path) -> None:
    _assert_no_existing_symlink(path)
    path.mkdir(parents=True, exist_ok=True)
    _assert_no_existing_symlink(path)


def _assert_transaction_paths_safe(
    evidence_root: Path,
    run_directory: Path,
    ledger_path: Path,
) -> None:
    _assert_no_existing_symlink(evidence_root)
    _assert_no_existing_symlink(run_directory)
    _assert_no_existing_symlink(ledger_path)
    _ensure_under_root(evidence_root, run_directory, "run_directory")
    _ensure_under_root(evidence_root, ledger_path, "ledger_path")


def _assert_no_existing_symlink(path: Path) -> None:
    probes: list[Path] = []
    current = path
    while True:
        probes.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for probe in reversed(probes):
        try:
            if probe.is_symlink():
                raise NBAPlayerPointsEvidenceError(f"path component is a symlink: {probe}")
        except OSError as exc:
            raise NBAPlayerPointsEvidenceError(f"unable to inspect path: {probe}") from exc


def _ensure_under_root(root: Path, path: Path, field_name: str) -> None:
    root_resolved = root.resolve(strict=False)
    path_resolved = path.resolve(strict=False)
    if path_resolved == root_resolved:
        return
    if root_resolved not in path_resolved.parents:
        raise NBAPlayerPointsEvidenceError(f"{field_name} escapes evidence root")


def _relative_to_root(path: Path, root: Path) -> str:
    _ensure_under_root(root, path, "path")
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise NBAPlayerPointsEvidenceError("path escapes evidence root") from exc


def _call_failure_hook(failure_hook: FailureHook | None, stage: str) -> None:
    if failure_hook is not None:
        failure_hook(stage)


def _evidence_root(path: Path, config: NBAPlayerPointsEvidenceWriterConfig) -> Path:
    base = path.expanduser()
    evidence_root = base if base.name == config.evidence_dir_name else base / config.evidence_dir_name
    if evidence_root.name != config.evidence_dir_name:
        raise NBAPlayerPointsEvidenceError("evidence root name mismatch")
    _assert_no_existing_symlink(evidence_root)
    return evidence_root


def _unique_values(rows: Sequence[Mapping[str, object]], field_name: str) -> set[str]:
    values: set[str] = set()
    for row in rows:
        value = row.get(field_name)
        if value in (None, ""):
            continue
        values.add(str(value))
    return values


def _canonical_sort_payloads(
    payloads: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        sorted(
            (MappingProxyType(_json_clone_mapping(payload)) for payload in payloads),
            key=lambda item: (
                str(item.get("prediction_id", "")),
                str(item.get("assembled_record_hash", "")),
                _canonical_json_text(item),
            ),
        )
    )


def _write_json_file(path: Path, payload: Mapping[str, object]) -> None:
    path.write_bytes(_json_file_bytes(payload))


def _read_json_file(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NBAPlayerPointsEvidenceError(f"invalid JSON file: {path}") from exc
    if not isinstance(payload, Mapping):
        raise NBAPlayerPointsEvidenceError(f"JSON file must contain an object: {path}")
    return MappingProxyType(_json_clone_mapping(payload))


def _read_jsonl(path: Path) -> tuple[Mapping[str, object], ...]:
    rows: list[Mapping[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise NBAPlayerPointsEvidenceError(f"unable to read JSONL file: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise NBAPlayerPointsEvidenceError(
                f"invalid JSONL at {path}:{line_number}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise NBAPlayerPointsEvidenceError(
                f"JSONL line must contain an object at {path}:{line_number}"
            )
        rows.append(MappingProxyType(_json_clone_mapping(payload)))
    return tuple(rows)


def _json_file_bytes(payload: Mapping[str, object]) -> bytes:
    return _stable_json_bytes(payload) + b"\n"


def _jsonl_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    if not rows:
        return b""
    return b"".join(_stable_json_bytes(row) + b"\n" for row in rows)


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_payload_sha256(payload: Mapping[str, object]) -> str:
    return _sha256_bytes(_stable_json_bytes(payload))


def _stable_json_bytes(payload: object) -> bytes:
    try:
        return json.dumps(
            _json_ready(payload),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except ValueError as exc:
        raise NBAPlayerPointsEvidenceError("canonical JSON cannot contain NaN or infinity") from exc


def _canonical_json_text(payload: object) -> str:
    return _stable_json_bytes(payload).decode("utf-8")


def _json_ready(value: object) -> object:
    if isinstance(value, datetime):
        return _format_utc(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_ready(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NBAPlayerPointsEvidenceError("numeric values must be finite")
        return value
    return value


def _json_clone_mapping(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise NBAPlayerPointsEvidenceError("value must be an object")
    cloned = json.loads(
        json.dumps(_json_ready(value), sort_keys=True, ensure_ascii=True, allow_nan=False)
    )
    if not isinstance(cloned, dict):
        raise NBAPlayerPointsEvidenceError("value must be an object")
    return cloned


def _coerce_utc_datetime(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise NBAPlayerPointsEvidenceError(
                f"{field_name} must be an ISO-8601 UTC timestamp"
            ) from exc
    else:
        raise NBAPlayerPointsEvidenceError(
            f"{field_name} must be an ISO-8601 UTC timestamp"
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NBAPlayerPointsEvidenceError(f"{field_name} must be timezone-aware")
    if parsed.utcoffset() != timedelta(0):
        raise NBAPlayerPointsEvidenceError(f"{field_name} must be UTC")
    return parsed.astimezone(_UTC)


def _format_utc(value: datetime) -> str:
    return _coerce_utc_datetime(value, "timestamp").isoformat().replace("+00:00", "Z")


def _format_optional_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _format_utc(value)


def _require_identifier(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if text == "0":
        raise NBAPlayerPointsEvidenceError(f"{field_name} is required")
    return text


def _require_safe_path_component(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if text in {".", ".."} or ".." in text:
        raise NBAPlayerPointsEvidenceError(f"{field_name} must not contain '..'")
    if "/" in text or "\\" in text:
        raise NBAPlayerPointsEvidenceError(f"{field_name} must not contain path separators")
    if Path(text).is_absolute():
        raise NBAPlayerPointsEvidenceError(f"{field_name} must not be absolute")
    return text


def _require_operating_date(value: object, field_name: str) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        text = value.isoformat()
    else:
        text = _require_text(value, field_name)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) is None:
        raise NBAPlayerPointsEvidenceError(f"{field_name} must use strict YYYY-MM-DD format")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise NBAPlayerPointsEvidenceError(f"{field_name} must be a valid date") from exc
    if parsed.isoformat() != text:
        raise NBAPlayerPointsEvidenceError(f"{field_name} must use strict YYYY-MM-DD format")
    return text


def _require_text(value: object, field_name: str) -> str:
    if value is None:
        raise NBAPlayerPointsEvidenceError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise NBAPlayerPointsEvidenceError(f"{field_name} is required")
    return text


def _require_sha256(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold()
    if _SHA256_RE.fullmatch(text) is None:
        raise NBAPlayerPointsEvidenceError(f"{field_name} must be lowercase SHA-256")
    return text


def _require_commit_sha(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold()
    if _COMMIT_SHA_RE.fullmatch(text) is None:
        raise NBAPlayerPointsEvidenceError(
            f"{field_name} must be a 7-40 character lowercase git SHA"
        )
    return text


def _optional_text(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NBAPlayerPointsEvidenceError("value must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise NBAPlayerPointsEvidenceError("value must be finite")
    return parsed


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise NBAPlayerPointsEvidenceError("value must be an integer")
    return value


__all__ = [
    "NBA_PLAYER_POINTS_EVIDENCE_COMPLETION_STATUSES",
    "NBA_PLAYER_POINTS_EVIDENCE_DIR_NAME",
    "NBA_PLAYER_POINTS_EVIDENCE_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_LEDGER_SCHEMA_VERSION",
    "NBAPlayerPointsEvidenceError",
    "NBAPlayerPointsEvidenceIntegrityReport",
    "NBAPlayerPointsEvidenceWriterConfig",
    "NBAPlayerPointsEvidenceWriteResult",
    "evidence_manifest_schema_definition",
    "ledger_record_schema_definition",
    "verify_nba_player_points_evidence",
    "write_nba_player_points_evidence",
]
