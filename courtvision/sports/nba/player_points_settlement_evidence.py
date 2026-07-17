"""Append-only NBA player-points settlement evidence writer.

This module persists already-validated settlement contract rows for offline
research. It performs no provider I/O, reads no credentials, does not calculate
settlement, and does not touch prediction, closing, grading, Kelly, bankroll,
dashboard, MLB, scheduler, or production runtime paths.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
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
from typing import Final
from uuid import uuid4

from courtvision.sports.nba.player_points_evidence import (
    NBA_PLAYER_POINTS_EVIDENCE_DIR_NAME,
    NBA_PLAYER_POINTS_LEDGER_SCHEMA_VERSION,
    NBAPlayerPointsEvidenceWriterConfig,
    verify_nba_player_points_evidence,
)
from courtvision.sports.nba.player_points_research import (
    NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL,
)
from courtvision.sports.nba.player_points_settlement import (
    NBA_PLAYER_POINTS_SETTLEMENT_ROW_FIELDS,
    NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_SETTLEMENT_STATUSES,
    NBAPlayerPointsSettlementRow,
    validate_settlement_rows,
)


NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_SCHEMA_VERSION: Final = (
    "nba-player-points-settlement-evidence-v1"
)
NBA_PLAYER_POINTS_DEFAULT_SETTLEMENT_POLICY_ID: Final = (
    "nba-player-points-offline-final-stats-settlement-v1"
)
NBA_PLAYER_POINTS_DEFAULT_SETTLEMENT_POLICY_VERSION: Final = "1.0"
NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_COMPLETION_STATUSES: Final = (
    "writing",
    "complete",
    "conflicting",
    "already_complete",
)
NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_LOCK_FILE: Final = ".settlement-writer.lock"
NBA_PLAYER_POINTS_SETTLEMENT_COMPLETION_MARKER_FILE: Final = "COMPLETE"
NBA_PLAYER_POINTS_SETTLEMENT_ROWS_FILE: Final = "settlement_rows.jsonl"
NBA_PLAYER_POINTS_SETTLEMENT_CONFLICTS_FILE: Final = "settlement_conflicts.jsonl"
NBA_PLAYER_POINTS_SETTLEMENT_MANIFEST_FILE: Final = "settlement_manifest.json"
NBA_PLAYER_POINTS_SETTLEMENT_INTEGRITY_REPORT_FILE: Final = "integrity_report.json"

_UTC: Final = timezone.utc
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA_RE: Final = re.compile(r"^[0-9a-f]{7,40}$")
_AUTHORITATIVE_SETTLEMENT_STATUSES: Final = ("settled", "void")
_TERMINAL_SETTLEMENT_STATUSES: Final = (*_AUTHORITATIVE_SETTLEMENT_STATUSES, "conflicting")
_INCOMPLETE_SETTLEMENT_STATUSES: Final = (
    "pending",
    "unresolved",
    "manual_review_required",
)
_CONTRADICTORY_SETTLEMENT_STATUSES: Final = ("ambiguous", "conflicting")
_KNOWN_PARTICIPATION_STATUSES: Final = (
    "participated",
    "zero_minutes",
    "did_not_participate",
)
_CONTRADICTORY_IDENTITY_STATUSES: Final = (
    "ambiguous",
    "conflicting",
    "quarantined",
)

FailureHook = Callable[[str], None]


class NBAPlayerPointsSettlementEvidenceError(ValueError):
    """Raised when settlement evidence persistence fails closed."""


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsSettlementPolicy:
    """Versioned settlement policy identity for effective-settlement resolution."""

    settlement_policy_id: str = NBA_PLAYER_POINTS_DEFAULT_SETTLEMENT_POLICY_ID
    settlement_policy_version: str = NBA_PLAYER_POINTS_DEFAULT_SETTLEMENT_POLICY_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "settlement_policy_id",
            _require_identifier(self.settlement_policy_id, "settlement_policy_id"),
        )
        object.__setattr__(
            self,
            "settlement_policy_version",
            _require_text(self.settlement_policy_version, "settlement_policy_version"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "settlement_policy_id": self.settlement_policy_id,
            "settlement_policy_version": self.settlement_policy_version,
            "terminal_statuses": list(_TERMINAL_SETTLEMENT_STATUSES),
            "pending_revision_rule": "pending rows may be superseded by terminal rows",
            "compatible_enrichment_rule": (
                "missing settlement fields may be enriched by later compatible "
                "authoritative evidence; known authoritative fields cannot regress"
            ),
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsSettlementEvidenceWriterConfig:
    """Explicit append-only settlement evidence writer configuration."""

    evidence_dir_name: str = NBA_PLAYER_POINTS_EVIDENCE_DIR_NAME
    settlement_dir_name: str = "settlement"
    segments_dir_name: str = "segments"
    completion_marker_file_name: str = NBA_PLAYER_POINTS_SETTLEMENT_COMPLETION_MARKER_FILE
    lock_timeout_seconds: float = 10.0
    research_label: str = NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL
    policy: NBAPlayerPointsSettlementPolicy = field(
        default_factory=NBAPlayerPointsSettlementPolicy
    )

    def __post_init__(self) -> None:
        if not isinstance(self.policy, NBAPlayerPointsSettlementPolicy):
            raise TypeError("policy must be NBAPlayerPointsSettlementPolicy")
        if self.research_label != NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL:
            raise NBAPlayerPointsSettlementEvidenceError("research_label is unsupported")
        for field_name in (
            "evidence_dir_name",
            "settlement_dir_name",
            "segments_dir_name",
            "completion_marker_file_name",
        ):
            _require_safe_path_component(getattr(self, field_name), field_name)
        if (
            isinstance(self.lock_timeout_seconds, bool)
            or not isinstance(self.lock_timeout_seconds, (int, float))
            or not math.isfinite(float(self.lock_timeout_seconds))
            or float(self.lock_timeout_seconds) < 0
        ):
            raise NBAPlayerPointsSettlementEvidenceError(
                "lock_timeout_seconds must be finite and non-negative"
            )


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsSettlementEvidenceWriteResult:
    """Structured result for one settlement evidence write attempt."""

    completion_status: str
    evidence_root: Path
    settlement_segment_directory: Path
    settlement_batch_id: str
    settlement_manifest: Mapping[str, object]
    integrity_report: Mapping[str, object]
    settlement_rows_written: int
    conflicts_written: int

    def to_dict(self) -> dict[str, object]:
        return {
            "completion_status": self.completion_status,
            "evidence_root": str(self.evidence_root),
            "settlement_segment_directory": str(self.settlement_segment_directory),
            "settlement_batch_id": self.settlement_batch_id,
            "settlement_manifest": _json_ready(self.settlement_manifest),
            "integrity_report": _json_ready(self.integrity_report),
            "settlement_rows_written": self.settlement_rows_written,
            "conflicts_written": self.conflicts_written,
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsSettlementEvidenceIntegrityReport:
    """Pure verifier report for settlement evidence."""

    ok: bool
    violations: tuple[str, ...]
    evidence_root: Path
    settlement_segments: tuple[Mapping[str, object], ...]
    effective_settlements: tuple[Mapping[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "violations": list(self.violations),
            "evidence_root": str(self.evidence_root),
            "settlement_segments": [
                _json_ready(segment) for segment in self.settlement_segments
            ],
            "effective_settlements": [
                _json_ready(row) for row in self.effective_settlements
            ],
        }


@dataclass(frozen=True, slots=True)
class _PredictionIndex:
    rows_by_prediction_id: Mapping[str, Mapping[str, object]]
    completed_run_ids: frozenset[str]
    integrity_report: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _PreparedSettlementBatch:
    settlement_batch_id: str
    operating_date: str
    records: tuple[Mapping[str, object], ...]
    conflicts: tuple[Mapping[str, object], ...]
    completion_status: str


@dataclass(frozen=True, slots=True)
class _ExistingSettlementEvidence:
    records: tuple[Mapping[str, object], ...]
    records_by_policy_key: Mapping[tuple[str, str, str], tuple[Mapping[str, object], ...]]
    violations: tuple[str, ...]


def default_settlement_policy() -> NBAPlayerPointsSettlementPolicy:
    """Return the default effective-settlement policy identity."""

    return NBAPlayerPointsSettlementPolicy()


def settlement_evidence_schema_definition() -> dict[str, object]:
    """Return the versioned settlement evidence contract."""

    return {
        "schema_version": NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_SCHEMA_VERSION,
        "settlement_contract_schema_version": NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION,
        "completion_statuses": list(NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_COMPLETION_STATUSES),
        "required_record_fields": list(_settlement_evidence_record_field_names()),
        "hash_algorithm": "SHA-256",
        "canonical_json": {
            "sort_keys": True,
            "separators": [",", ":"],
            "encoding": "utf-8",
            "allow_nan": False,
        },
        "layout": {
            "segments": (
                "settlement/segments/{operating_date}/"
                "{settlement_batch_id}/settlement_rows.jsonl"
            ),
            "conflicts": NBA_PLAYER_POINTS_SETTLEMENT_CONFLICTS_FILE,
            "manifest": NBA_PLAYER_POINTS_SETTLEMENT_MANIFEST_FILE,
            "completion_marker": NBA_PLAYER_POINTS_SETTLEMENT_COMPLETION_MARKER_FILE,
        },
        "effective_settlement_ordering": _effective_settlement_ordering_rule(),
        "compatible_enrichment_rule": _compatible_enrichment_rule(),
    }


def write_nba_player_points_settlement_evidence(
    evidence_root: str | Path,
    settlement_rows: Sequence[NBAPlayerPointsSettlementRow],
    config: NBAPlayerPointsSettlementEvidenceWriterConfig | None = None,
    *,
    collection_timestamp_utc: datetime | str,
    repository_commit_sha: str,
    writer_timestamp_utc: datetime | str,
    failure_hook: FailureHook | None = None,
) -> NBAPlayerPointsSettlementEvidenceWriteResult:
    """Persist validated settlement rows into immutable evidence segments.

    The writer accepts settlement-contract rows only. It never maps final stats,
    matches final stats to predictions, computes outcomes, mutates prediction
    evidence, or writes production history.
    """

    cfg = config or NBAPlayerPointsSettlementEvidenceWriterConfig()
    _validate_config(cfg)
    collection_time = _coerce_utc_datetime(
        collection_timestamp_utc,
        "collection_timestamp_utc",
    )
    writer_time = _coerce_utc_datetime(writer_timestamp_utc, "writer_timestamp_utc")
    commit_sha = _require_commit_sha(repository_commit_sha, "repository_commit_sha")
    rows = _validate_input_settlement_rows(settlement_rows)
    root = _evidence_root(Path(evidence_root), cfg)

    _call_failure_hook(failure_hook, "before_any_write")
    with _SettlementRootLock(root, cfg):
        prediction_index = _load_prediction_index(root)
        existing = _scan_settlement_evidence(root, cfg, prediction_index)
        if existing.violations:
            raise NBAPlayerPointsSettlementEvidenceError(
                "existing settlement evidence failed verification: "
                + "; ".join(existing.violations)
            )
        prepared = _prepare_settlement_batch(
            rows,
            prediction_index,
            existing,
            config=cfg,
        )
        segment_dir = _settlement_segment_directory(
            root,
            prepared.operating_date,
            prepared.settlement_batch_id,
            cfg,
        )
        _assert_no_existing_symlink(segment_dir)
        _ensure_under_root(root, segment_dir, "settlement_segment_directory")

        if segment_dir.exists():
            manifest = _read_existing_completed_manifest(segment_dir, cfg)
            verifier_report = verify_nba_player_points_settlement_evidence(root, cfg)
            if not verifier_report.ok:
                raise NBAPlayerPointsSettlementEvidenceError(
                    "settlement evidence root failed verification after replay: "
                    + "; ".join(verifier_report.violations)
                )
            return NBAPlayerPointsSettlementEvidenceWriteResult(
                completion_status="already_complete",
                evidence_root=root,
                settlement_segment_directory=segment_dir,
                settlement_batch_id=prepared.settlement_batch_id,
                settlement_manifest=manifest,
                integrity_report=verifier_report.to_dict(),
                settlement_rows_written=0,
                conflicts_written=0,
            )

        manifest = _publish_settlement_segment(
            root,
            prepared,
            collection_timestamp_utc=collection_time,
            writer_timestamp_utc=writer_time,
            repository_commit_sha=commit_sha,
            config=cfg,
            failure_hook=failure_hook,
        )
        verifier_report = verify_nba_player_points_settlement_evidence(root, cfg)
        if not verifier_report.ok:
            raise NBAPlayerPointsSettlementEvidenceError(
                "settlement evidence root failed verification after write: "
                + "; ".join(verifier_report.violations)
            )
        return NBAPlayerPointsSettlementEvidenceWriteResult(
            completion_status=prepared.completion_status,
            evidence_root=root,
            settlement_segment_directory=segment_dir,
            settlement_batch_id=prepared.settlement_batch_id,
            settlement_manifest=manifest,
            integrity_report=verifier_report.to_dict(),
            settlement_rows_written=len(prepared.records),
            conflicts_written=len(prepared.conflicts),
        )


def verify_nba_player_points_settlement_evidence(
    evidence_root: str | Path,
    config: NBAPlayerPointsSettlementEvidenceWriterConfig | None = None,
) -> NBAPlayerPointsSettlementEvidenceIntegrityReport:
    """Inspect settlement evidence without mutation or provider access."""

    cfg = config or NBAPlayerPointsSettlementEvidenceWriterConfig()
    _validate_config(cfg)
    root = _evidence_root(Path(evidence_root), cfg)
    violations: list[str] = []
    segment_reports: list[Mapping[str, object]] = []
    effective_reports: tuple[Mapping[str, object], ...] = ()

    if not root.exists():
        return NBAPlayerPointsSettlementEvidenceIntegrityReport(
            ok=False,
            violations=(f"evidence root does not exist: {root}",),
            evidence_root=root,
            settlement_segments=(),
            effective_settlements=(),
        )

    try:
        prediction_index = _load_prediction_index(root)
    except NBAPlayerPointsSettlementEvidenceError as exc:
        return NBAPlayerPointsSettlementEvidenceIntegrityReport(
            ok=False,
            violations=(str(exc),),
            evidence_root=root,
            settlement_segments=(),
            effective_settlements=(),
        )

    for segment in _iter_completed_settlement_segments(root, cfg):
        report = _verify_settlement_segment(segment, cfg, prediction_index)
        segment_reports.append(report)
        violations.extend(str(item) for item in report["violations"])

    if not violations:
        effective_reports, effective_violations = _build_effective_settlement_reports(root, cfg)
        violations.extend(effective_violations)

    return NBAPlayerPointsSettlementEvidenceIntegrityReport(
        ok=not violations,
        violations=tuple(violations),
        evidence_root=root,
        settlement_segments=tuple(segment_reports),
        effective_settlements=effective_reports,
    )


def resolve_nba_player_points_effective_settlement(
    evidence_root: str | Path,
    prediction_id: str,
    config: NBAPlayerPointsSettlementEvidenceWriterConfig | None = None,
) -> Mapping[str, object]:
    """Resolve effective settlement for one prediction and policy identity."""

    cfg = config or NBAPlayerPointsSettlementEvidenceWriterConfig()
    report = verify_nba_player_points_settlement_evidence(evidence_root, cfg)
    if not report.ok:
        raise NBAPlayerPointsSettlementEvidenceError(
            "settlement evidence failed verification: " + "; ".join(report.violations)
        )
    prediction = _require_identifier(prediction_id, "prediction_id")
    matches = tuple(
        row
        for row in report.effective_settlements
        if row.get("prediction_id") == prediction
        and row.get("settlement_policy_id") == cfg.policy.settlement_policy_id
        and row.get("settlement_policy_version") == cfg.policy.settlement_policy_version
    )
    if len(matches) != 1:
        raise NBAPlayerPointsSettlementEvidenceError(
            "expected exactly one effective settlement for prediction and policy identity"
        )
    return MappingProxyType(dict(matches[0]))


def _validate_input_settlement_rows(
    rows: Sequence[NBAPlayerPointsSettlementRow],
) -> tuple[NBAPlayerPointsSettlementRow, ...]:
    if not rows:
        raise NBAPlayerPointsSettlementEvidenceError("settlement_rows must not be empty")
    for row in rows:
        if not isinstance(row, NBAPlayerPointsSettlementRow):
            raise TypeError("settlement_rows must contain NBAPlayerPointsSettlementRow values")
    try:
        return validate_settlement_rows(tuple(rows))
    except Exception as exc:
        raise NBAPlayerPointsSettlementEvidenceError(str(exc)) from exc


def _prepare_settlement_batch(
    rows: Sequence[NBAPlayerPointsSettlementRow],
    prediction_index: _PredictionIndex,
    existing: _ExistingSettlementEvidence,
    *,
    config: NBAPlayerPointsSettlementEvidenceWriterConfig,
) -> _PreparedSettlementBatch:
    records = tuple(
        _settlement_evidence_record(row, prediction_index, config=config)
        for row in rows
    )
    operating_dates = {str(record["operating_date"]) for record in records}
    if len(operating_dates) != 1:
        raise NBAPlayerPointsSettlementEvidenceError(
            "settlement batch must contain one operating_date"
        )
    conflicts = _terminal_conflicts(records, existing)
    completion_status = (
        "conflicting"
        if conflicts or any(row.get("settlement_status") == "conflicting" for row in records)
        else "complete"
    )
    batch_id = _settlement_batch_id(
        records=records,
        conflicts=conflicts,
        operating_date=next(iter(operating_dates)),
        policy=config.policy,
    )
    return _PreparedSettlementBatch(
        settlement_batch_id=batch_id,
        operating_date=next(iter(operating_dates)),
        records=records,
        conflicts=conflicts,
        completion_status=completion_status,
    )


def _settlement_evidence_record(
    row: NBAPlayerPointsSettlementRow,
    prediction_index: _PredictionIndex,
    *,
    config: NBAPlayerPointsSettlementEvidenceWriterConfig,
) -> Mapping[str, object]:
    payload = row.to_dict()
    prediction_entry = _validate_prediction_reference(payload, prediction_index)
    prediction_row = prediction_entry["record"]
    reference = prediction_entry["evidence_reference"]
    record = {
        **payload,
        "evidence_schema_version": NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_SCHEMA_VERSION,
        "settlement_policy_id": config.policy.settlement_policy_id,
        "settlement_policy_version": config.policy.settlement_policy_version,
        "prediction_evidence_segment": reference["prediction_evidence_segment"],
        "prediction_evidence_line_number": reference["line_number"],
        "prediction_record_hash": prediction_row["ledger_record_hash"],
        "prediction_assembled_record_hash": prediction_row["assembled_record_hash"],
    }
    record["settlement_evidence_record_hash"] = _record_hash(
        record,
        "settlement_evidence_record_hash",
    )
    return MappingProxyType(record)


def _validate_prediction_reference(
    settlement: Mapping[str, object],
    prediction_index: _PredictionIndex,
) -> Mapping[str, object]:
    prediction_id = _require_identifier(settlement.get("prediction_id"), "prediction_id")
    entry = prediction_index.rows_by_prediction_id.get(prediction_id)
    if not isinstance(entry, Mapping):
        raise NBAPlayerPointsSettlementEvidenceError(
            f"prediction evidence not found for prediction_id: {prediction_id}"
        )
    prediction_row = entry.get("record")
    if not isinstance(prediction_row, Mapping):
        raise NBAPlayerPointsSettlementEvidenceError("prediction evidence entry is malformed")
    prediction_run_id = str(settlement.get("prediction_run_id"))
    if prediction_run_id not in prediction_index.completed_run_ids:
        raise NBAPlayerPointsSettlementEvidenceError(
            f"prediction evidence is not complete: {prediction_run_id}"
        )
    expected_pairs = (
        ("prediction_run_id", "prediction_run_id"),
        ("model_id", "model_id"),
        ("canonical_event_id", "canonical_event_id"),
        ("provider_event_id", "provider_event_id"),
        ("operating_date", "operating_date"),
        ("commence_time_utc", "commence_time_utc"),
        ("player_id", "player_id"),
        ("team", "team"),
        ("opponent", "opponent"),
    )
    for settlement_field, prediction_field in expected_pairs:
        if settlement.get(settlement_field) != prediction_row.get(prediction_field):
            raise NBAPlayerPointsSettlementEvidenceError(
                f"{settlement_field} mismatch with prediction evidence"
            )
    if settlement.get("prediction_artifact_hash") != prediction_row.get(
        "assembled_record_hash"
    ):
        raise NBAPlayerPointsSettlementEvidenceError(
            "prediction_artifact_hash mismatch with prediction evidence"
        )
    return entry


def _terminal_conflicts(
    records: Sequence[Mapping[str, object]],
    existing: _ExistingSettlementEvidence,
) -> tuple[Mapping[str, object], ...]:
    conflicts: list[Mapping[str, object]] = []
    seen_by_policy_key: dict[tuple[str, str, str], list[Mapping[str, object]]] = {}
    for record in sorted(records, key=_settlement_history_sort_key):
        key = _policy_key(record)
        for prior in existing.records_by_policy_key.get(key, ()):
            reason = _settlement_pair_conflict_reason(prior, record)
            if reason is not None:
                conflicts.append(
                    _conflict_record(
                        record,
                        existing=prior,
                        reason=reason,
                    )
                )
        for prior_in_batch in seen_by_policy_key.get(key, []):
            reason = _settlement_pair_conflict_reason(prior_in_batch, record)
            if reason is not None:
                conflicts.append(
                    _conflict_record(
                        record,
                        existing=prior_in_batch,
                        reason=reason,
                    )
                )
        seen_by_policy_key.setdefault(key, []).append(record)
    return _dedupe_conflicts(conflicts)


def _publish_settlement_segment(
    root: Path,
    prepared: _PreparedSettlementBatch,
    *,
    collection_timestamp_utc: datetime,
    writer_timestamp_utc: datetime,
    repository_commit_sha: str,
    config: NBAPlayerPointsSettlementEvidenceWriterConfig,
    failure_hook: FailureHook | None,
) -> Mapping[str, object]:
    segment_dir = _settlement_segment_directory(
        root,
        prepared.operating_date,
        prepared.settlement_batch_id,
        config,
    )
    if segment_dir.exists():
        return _read_existing_completed_manifest(segment_dir, config)
    parent = segment_dir.parent
    _make_directory(parent)
    stage_dir = parent / f".settlement-{uuid4().hex[:12]}"
    try:
        stage_dir.mkdir()
        _call_failure_hook(failure_hook, "after_settlement_temp_dir_created")
        files = {
            NBA_PLAYER_POINTS_SETTLEMENT_ROWS_FILE: _jsonl_bytes(prepared.records),
            NBA_PLAYER_POINTS_SETTLEMENT_CONFLICTS_FILE: _jsonl_bytes(prepared.conflicts),
            NBA_PLAYER_POINTS_SETTLEMENT_INTEGRITY_REPORT_FILE: _json_file_bytes(
                {"status": "writing", "violations": []}
            ),
        }
        for name, data in files.items():
            _write_bytes_verified(stage_dir / name, data)
            if name == NBA_PLAYER_POINTS_SETTLEMENT_ROWS_FILE:
                _call_failure_hook(failure_hook, "after_settlement_rows_write")
        file_hashes = {name: _sha256_bytes(data) for name, data in files.items()}
        manifest = _settlement_manifest_payload(
            settlement_batch_id=prepared.settlement_batch_id,
            operating_date=prepared.operating_date,
            settlement_count=len(prepared.records),
            conflict_count=len(prepared.conflicts),
            collection_timestamp_utc=collection_timestamp_utc,
            writer_timestamp_utc=writer_timestamp_utc,
            repository_commit_sha=repository_commit_sha,
            completion_status=prepared.completion_status,
            file_hashes=file_hashes,
            config=config,
        )
        _write_json_file(stage_dir / NBA_PLAYER_POINTS_SETTLEMENT_MANIFEST_FILE, manifest)
        manifest_hash = _sha256_file(stage_dir / NBA_PLAYER_POINTS_SETTLEMENT_MANIFEST_FILE)
        integrity_payload = {
            "status": prepared.completion_status,
            "violations": [],
            "settlement_batch_id": prepared.settlement_batch_id,
            "file_hashes": file_hashes,
            "manifest_hash": manifest_hash,
            "settlement_count": len(prepared.records),
            "conflict_count": len(prepared.conflicts),
        }
        _write_json_file(
            stage_dir / NBA_PLAYER_POINTS_SETTLEMENT_INTEGRITY_REPORT_FILE,
            integrity_payload,
        )
        final_hashes = dict(file_hashes)
        final_hashes[NBA_PLAYER_POINTS_SETTLEMENT_INTEGRITY_REPORT_FILE] = _sha256_file(
            stage_dir / NBA_PLAYER_POINTS_SETTLEMENT_INTEGRITY_REPORT_FILE
        )
        final_manifest = _settlement_manifest_payload(
            settlement_batch_id=prepared.settlement_batch_id,
            operating_date=prepared.operating_date,
            settlement_count=len(prepared.records),
            conflict_count=len(prepared.conflicts),
            collection_timestamp_utc=collection_timestamp_utc,
            writer_timestamp_utc=writer_timestamp_utc,
            repository_commit_sha=repository_commit_sha,
            completion_status=prepared.completion_status,
            file_hashes=final_hashes,
            config=config,
        )
        _write_json_file(stage_dir / NBA_PLAYER_POINTS_SETTLEMENT_MANIFEST_FILE, final_manifest)
        _write_json_file(
            stage_dir / config.completion_marker_file_name,
            {
                "completion_status": prepared.completion_status,
                "manifest_file": NBA_PLAYER_POINTS_SETTLEMENT_MANIFEST_FILE,
                "manifest_hash": _sha256_file(
                    stage_dir / NBA_PLAYER_POINTS_SETTLEMENT_MANIFEST_FILE
                ),
            },
        )
        _call_failure_hook(failure_hook, "before_settlement_segment_publication")
        stage_dir.rename(segment_dir)
        return MappingProxyType(dict(final_manifest))
    except Exception:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def _settlement_manifest_payload(
    *,
    settlement_batch_id: str,
    operating_date: str,
    settlement_count: int,
    conflict_count: int,
    collection_timestamp_utc: datetime,
    writer_timestamp_utc: datetime,
    repository_commit_sha: str,
    completion_status: str,
    file_hashes: Mapping[str, str],
    config: NBAPlayerPointsSettlementEvidenceWriterConfig,
) -> Mapping[str, object]:
    if completion_status not in NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_COMPLETION_STATUSES:
        raise NBAPlayerPointsSettlementEvidenceError("unsupported completion_status")
    manifest = {
        "schema_version": NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_SCHEMA_VERSION,
        "settlement_contract_schema_version": NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION,
        "settlement_batch_id": settlement_batch_id,
        "operating_date": operating_date,
        "settlement_policy": config.policy.to_dict(),
        "settlement_count": settlement_count,
        "conflict_count": conflict_count,
        "collection_timestamp_utc": _format_utc(collection_timestamp_utc),
        "writer_timestamp_utc": _format_utc(writer_timestamp_utc),
        "repository_commit_sha": repository_commit_sha,
        "settlement_rows_file": NBA_PLAYER_POINTS_SETTLEMENT_ROWS_FILE,
        "settlement_rows_hash": file_hashes.get(NBA_PLAYER_POINTS_SETTLEMENT_ROWS_FILE, ""),
        "settlement_conflicts_file": NBA_PLAYER_POINTS_SETTLEMENT_CONFLICTS_FILE,
        "settlement_conflicts_hash": file_hashes.get(
            NBA_PLAYER_POINTS_SETTLEMENT_CONFLICTS_FILE,
            "",
        ),
        "integrity_report_file": NBA_PLAYER_POINTS_SETTLEMENT_INTEGRITY_REPORT_FILE,
        "integrity_report_hash": file_hashes.get(
            NBA_PLAYER_POINTS_SETTLEMENT_INTEGRITY_REPORT_FILE,
            "",
        ),
        "completion_status": completion_status,
    }
    manifest["settlement_manifest_hash"] = _record_hash(
        manifest,
        "settlement_manifest_hash",
    )
    return MappingProxyType(manifest)


def _load_prediction_index(root: Path) -> _PredictionIndex:
    evidence_report = verify_nba_player_points_evidence(
        root,
        NBAPlayerPointsEvidenceWriterConfig(),
    )
    report_dict = evidence_report.to_dict()
    if not evidence_report.ok:
        raise NBAPlayerPointsSettlementEvidenceError(
            "prediction evidence failed verification: "
            + "; ".join(evidence_report.violations)
        )
    completed_run_ids = frozenset(
        str(item) for item in evidence_report.ledger_summary.get("completed_run_ids", ())
    )
    rows_by_prediction_id: dict[str, Mapping[str, object]] = {}
    ledgers_root = root / "ledgers" / "segments"
    if not ledgers_root.exists():
        return _PredictionIndex(
            rows_by_prediction_id=MappingProxyType({}),
            completed_run_ids=completed_run_ids,
            integrity_report=MappingProxyType(report_dict),
        )
    for ledger_path in sorted(ledgers_root.glob("*/*/prediction_ledger.jsonl")):
        if ledger_path.is_symlink():
            raise NBAPlayerPointsSettlementEvidenceError(
                f"prediction ledger segment is a symlink: {ledger_path}"
            )
        operating_date = _require_operating_date(
            ledger_path.parent.parent.name,
            "operating_date",
        )
        prediction_run_id = _require_safe_path_component(
            ledger_path.parent.name,
            "prediction_run_id",
        )
        rows = _read_jsonl_strict(ledger_path)
        for line_number, row in enumerate(rows, start=1):
            if row.get("ledger_schema_version") != NBA_PLAYER_POINTS_LEDGER_SCHEMA_VERSION:
                raise NBAPlayerPointsSettlementEvidenceError(
                    "unsupported prediction ledger schema"
                )
            if row.get("operating_date") != operating_date:
                raise NBAPlayerPointsSettlementEvidenceError(
                    "prediction ledger operating_date path mismatch"
                )
            if row.get("prediction_run_id") != prediction_run_id:
                raise NBAPlayerPointsSettlementEvidenceError(
                    "prediction ledger run path mismatch"
                )
            if _record_hash(row, "ledger_record_hash") != row.get("ledger_record_hash"):
                raise NBAPlayerPointsSettlementEvidenceError(
                    "prediction ledger_record_hash mismatch"
                )
            prediction_id = str(row["prediction_id"])
            rows_by_prediction_id[prediction_id] = MappingProxyType(
                {
                    "record": row,
                    "evidence_reference": {
                        "prediction_evidence_segment": _relative_to_root(
                            ledger_path,
                            root,
                        ),
                        "line_number": line_number,
                    },
                }
            )
    return _PredictionIndex(
        rows_by_prediction_id=MappingProxyType(rows_by_prediction_id),
        completed_run_ids=completed_run_ids,
        integrity_report=MappingProxyType(report_dict),
    )


def _scan_settlement_evidence(
    root: Path,
    config: NBAPlayerPointsSettlementEvidenceWriterConfig,
    prediction_index: _PredictionIndex,
) -> _ExistingSettlementEvidence:
    records: list[Mapping[str, object]] = []
    by_key: dict[tuple[str, str, str], list[Mapping[str, object]]] = {}
    violations: list[str] = []

    for segment in _iter_completed_settlement_segments(root, config):
        report = _verify_settlement_segment(segment, config, prediction_index)
        if report["violations"]:
            violations.extend(str(item) for item in report["violations"])
            continue
        for row in _read_jsonl_strict(segment / NBA_PLAYER_POINTS_SETTLEMENT_ROWS_FILE):
            records.append(row)
            by_key.setdefault(_policy_key(row), []).append(row)
    return _ExistingSettlementEvidence(
        records=tuple(records),
        records_by_policy_key=MappingProxyType(
            {key: tuple(value) for key, value in by_key.items()}
        ),
        violations=tuple(violations),
    )


def _build_effective_settlement_reports(
    root: Path,
    config: NBAPlayerPointsSettlementEvidenceWriterConfig,
) -> tuple[tuple[Mapping[str, object], ...], tuple[str, ...]]:
    rows: list[Mapping[str, object]] = []
    lineage: dict[tuple[str, str], Mapping[str, object]] = {}
    violations: list[str] = []

    for segment in _iter_completed_settlement_segments(root, config):
        manifest = _read_json_file(segment / NBA_PLAYER_POINTS_SETTLEMENT_MANIFEST_FILE)
        for row in _read_jsonl_strict(segment / NBA_PLAYER_POINTS_SETTLEMENT_ROWS_FILE):
            row_hash = str(row["settlement_evidence_record_hash"])
            row_id = str(row["settlement_id"])
            rows.append(row)
            lineage[(row_id, row_hash)] = MappingProxyType(
                {
                    "kind": "settlement",
                    "segment_directory": str(segment),
                    "settlement_batch_id": manifest.get("settlement_batch_id"),
                    "settlement_id": row_id,
                    "settlement_evidence_record_hash": row_hash,
                    "manifest_hash": manifest.get("settlement_manifest_hash"),
                }
            )

    policy_keys = {_policy_key(row) for row in rows}
    reports: list[Mapping[str, object]] = []
    for key in sorted(policy_keys):
        key_rows = tuple(
            sorted(
                (row for row in rows if _policy_key(row) == key),
                key=_settlement_history_sort_key,
            )
        )
        if not key_rows:
            continue
        conflict_reason = _settlement_rows_conflict_reason(key_rows)
        if conflict_reason is not None:
            reports.append(
                _effective_settlement_report(
                    key=key,
                    effective_status="conflicting",
                    selected_settlement=None,
                    historical_rows=key_rows,
                    lineage=lineage,
                    conflict_reason=conflict_reason,
                )
            )
            continue

        selected = max(key_rows, key=_effective_settlement_sort_key)
        reports.append(
            _effective_settlement_report(
                key=key,
                effective_status=str(selected["settlement_status"]),
                selected_settlement=selected,
                historical_rows=key_rows,
                lineage=lineage,
                conflict_reason="none",
            )
        )

    if violations:
        return (), tuple(violations)
    return tuple(reports), ()


def _effective_settlement_report(
    *,
    key: tuple[str, str, str],
    effective_status: str,
    selected_settlement: Mapping[str, object] | None,
    historical_rows: Sequence[Mapping[str, object]],
    lineage: Mapping[tuple[str, str], Mapping[str, object]],
    conflict_reason: str,
) -> Mapping[str, object]:
    prediction_id, policy_id, policy_version = key
    terminal_rows = tuple(row for row in historical_rows if _is_terminal(row))
    selected_lineage: list[Mapping[str, object]] = []
    if selected_settlement is not None:
        for historical_row in historical_rows:
            entry = lineage.get(
                (
                    str(historical_row["settlement_id"]),
                    str(historical_row["settlement_evidence_record_hash"]),
                )
            )
            if entry is not None:
                selected_lineage.append(entry)
    return MappingProxyType(
        {
            "prediction_id": prediction_id,
            "settlement_policy_id": policy_id,
            "settlement_policy_version": policy_version,
            "effective_status": effective_status,
            "settlement_status": (
                selected_settlement.get("settlement_status")
                if selected_settlement is not None
                else "conflicting"
            ),
            "selected_settlement_id": (
                selected_settlement.get("settlement_id")
                if selected_settlement is not None
                else None
            ),
            "selected_settlement_hash": (
                selected_settlement.get("settlement_evidence_record_hash")
                if selected_settlement is not None
                else None
            ),
            "selected_settlement_record": (
                _json_ready(selected_settlement)
                if selected_settlement is not None
                else None
            ),
            "historical_settlement_count": len(historical_rows),
            "terminal_revision_count": len(terminal_rows),
            "compatible_terminal_revision_count": (
                len(terminal_rows) if terminal_rows and conflict_reason == "none" else 0
            ),
            "historical_settlement_records": [
                _json_ready(row) for row in historical_rows
            ],
            "conflict_reason": conflict_reason,
            "ordering_rule": _effective_settlement_ordering_rule(),
            "compatible_enrichment_rule": _compatible_enrichment_rule(),
            "evidence_lineage": [_json_ready(item) for item in selected_lineage],
        }
    )


def _verify_settlement_segment(
    segment: Path,
    config: NBAPlayerPointsSettlementEvidenceWriterConfig,
    prediction_index: _PredictionIndex,
) -> Mapping[str, object]:
    violations: list[str] = []
    manifest_path = segment / NBA_PLAYER_POINTS_SETTLEMENT_MANIFEST_FILE
    marker_path = segment / config.completion_marker_file_name
    rows_path = segment / NBA_PLAYER_POINTS_SETTLEMENT_ROWS_FILE
    conflicts_path = segment / NBA_PLAYER_POINTS_SETTLEMENT_CONFLICTS_FILE
    integrity_path = segment / NBA_PLAYER_POINTS_SETTLEMENT_INTEGRITY_REPORT_FILE
    for path in (manifest_path, marker_path, rows_path, conflicts_path, integrity_path):
        if path.is_symlink():
            violations.append(f"settlement evidence file is a symlink: {path}")
        if not path.exists():
            violations.append(f"settlement evidence expected file missing: {path.name}")

    manifest: Mapping[str, object] = MappingProxyType({})
    marker: Mapping[str, object] = MappingProxyType({})
    rows: tuple[Mapping[str, object], ...] = ()
    conflicts: tuple[Mapping[str, object], ...] = ()
    if not violations:
        try:
            manifest = _read_json_file(manifest_path)
            marker = _read_json_file(marker_path)
            rows = _read_jsonl_strict(rows_path)
            conflicts = _read_jsonl_strict(conflicts_path)
        except NBAPlayerPointsSettlementEvidenceError as exc:
            violations.append(str(exc))

    if manifest:
        if manifest.get("schema_version") != NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_SCHEMA_VERSION:
            violations.append("settlement manifest schema_version mismatch")
        if manifest.get("settlement_contract_schema_version") != NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION:
            violations.append("settlement manifest contract schema mismatch")
        if manifest.get("settlement_manifest_hash") != _record_hash(
            manifest,
            "settlement_manifest_hash",
        ):
            violations.append("settlement_manifest_hash mismatch")
        if marker.get("manifest_hash") != _sha256_file(manifest_path):
            violations.append("settlement completion marker manifest_hash mismatch")
        expected_hashes = {
            NBA_PLAYER_POINTS_SETTLEMENT_ROWS_FILE: manifest.get("settlement_rows_hash"),
            NBA_PLAYER_POINTS_SETTLEMENT_CONFLICTS_FILE: manifest.get(
                "settlement_conflicts_hash"
            ),
            NBA_PLAYER_POINTS_SETTLEMENT_INTEGRITY_REPORT_FILE: manifest.get(
                "integrity_report_hash"
            ),
        }
        for filename, expected_hash in expected_hashes.items():
            path = segment / filename
            if path.exists() and expected_hash != _sha256_file(path):
                violations.append(f"{filename} hash mismatch")
        if int(manifest.get("settlement_count", -1)) != len(rows):
            violations.append("settlement manifest settlement_count mismatch")
        if int(manifest.get("conflict_count", -1)) != len(conflicts):
            violations.append("settlement manifest conflict_count mismatch")
        if not _manifest_policy_matches(manifest, config.policy):
            # Policy isolation is enforced by rows, but manifests still need a
            # valid policy payload for integrity scans across all policy versions.
            manifest_policy = manifest.get("settlement_policy")
            if not isinstance(manifest_policy, Mapping):
                violations.append("settlement manifest policy missing")

    for line_number, row in enumerate(rows, start=1):
        violations.extend(
            _validate_settlement_evidence_record(
                row,
                prediction_index,
                segment=segment,
                line_number=line_number,
            )
        )
    for line_number, conflict in enumerate(conflicts, start=1):
        if conflict.get("settlement_conflict_hash") != _record_hash(
            conflict,
            "settlement_conflict_hash",
        ):
            violations.append(f"{segment}:{line_number}: settlement_conflict_hash mismatch")
    return MappingProxyType(
        {
            "segment_directory": str(segment),
            "manifest": _json_ready(manifest),
            "settlement_count": len(rows),
            "conflict_count": len(conflicts),
            "violations": tuple(violations),
        }
    )


def _validate_settlement_evidence_record(
    row: Mapping[str, object],
    prediction_index: _PredictionIndex,
    *,
    segment: Path,
    line_number: int,
) -> tuple[str, ...]:
    violations: list[str] = []
    missing = [field for field in _settlement_evidence_record_field_names() if field not in row]
    if missing:
        violations.append(
            f"{segment}:{line_number}: settlement evidence missing fields: {','.join(missing)}"
        )
        return tuple(violations)
    if row.get("evidence_schema_version") != NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_SCHEMA_VERSION:
        violations.append(f"{segment}:{line_number}: unsupported settlement evidence schema")
    if row.get("settlement_schema_version") != NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION:
        violations.append(f"{segment}:{line_number}: unsupported settlement contract schema")
    if row.get("settlement_status") not in NBA_PLAYER_POINTS_SETTLEMENT_STATUSES:
        violations.append(f"{segment}:{line_number}: unsupported settlement_status")
    if row.get("settlement_evidence_record_hash") != _record_hash(
        row,
        "settlement_evidence_record_hash",
    ):
        violations.append(f"{segment}:{line_number}: settlement_evidence_record_hash mismatch")
    contract_payload = _settlement_contract_payload(row)
    if row.get("settlement_record_hash") != _record_hash(
        contract_payload,
        "settlement_record_hash",
    ):
        violations.append(f"{segment}:{line_number}: settlement_record_hash mismatch")
    try:
        _coerce_utc_datetime(row["commence_time_utc"], "commence_time_utc")
        _coerce_utc_datetime(row["settlement_timestamp_utc"], "settlement_timestamp_utc")
        _coerce_utc_datetime(
            row["settlement_source_timestamp_utc"],
            "settlement_source_timestamp_utc",
        )
        _require_sha256(row["settlement_source_hash"], "settlement_source_hash")
        _require_sha256(row["prediction_artifact_hash"], "prediction_artifact_hash")
        _require_sha256(row["prediction_record_hash"], "prediction_record_hash")
        _require_sha256(
            row["prediction_assembled_record_hash"],
            "prediction_assembled_record_hash",
        )
    except NBAPlayerPointsSettlementEvidenceError as exc:
        violations.append(f"{segment}:{line_number}: {exc}")
    prediction_entry = prediction_index.rows_by_prediction_id.get(str(row["prediction_id"]))
    prediction_row = prediction_entry.get("record") if isinstance(prediction_entry, Mapping) else None
    if not isinstance(prediction_row, Mapping):
        violations.append(f"{segment}:{line_number}: prediction evidence missing")
    elif str(row["prediction_run_id"]) not in prediction_index.completed_run_ids:
        violations.append(f"{segment}:{line_number}: prediction evidence not complete")
    else:
        for field_name in (
            "prediction_run_id",
            "model_id",
            "canonical_event_id",
            "provider_event_id",
            "operating_date",
            "commence_time_utc",
            "player_id",
            "team",
            "opponent",
        ):
            if row.get(field_name) != prediction_row.get(field_name):
                violations.append(
                    f"{segment}:{line_number}: {field_name} mismatch with prediction evidence"
                )
        if row.get("prediction_artifact_hash") != prediction_row.get("assembled_record_hash"):
            violations.append(
                f"{segment}:{line_number}: prediction_artifact_hash mismatch"
            )
        if row.get("prediction_record_hash") != prediction_row.get("ledger_record_hash"):
            violations.append(f"{segment}:{line_number}: prediction_record_hash mismatch")
    return tuple(violations)


def _settlement_contract_payload(row: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(
        {field: row.get(field) for field in NBA_PLAYER_POINTS_SETTLEMENT_ROW_FIELDS}
    )


def _settlement_evidence_record_field_names() -> tuple[str, ...]:
    return (
        *NBA_PLAYER_POINTS_SETTLEMENT_ROW_FIELDS,
        "evidence_schema_version",
        "settlement_policy_id",
        "settlement_policy_version",
        "prediction_evidence_segment",
        "prediction_evidence_line_number",
        "prediction_record_hash",
        "prediction_assembled_record_hash",
        "settlement_evidence_record_hash",
    )


def _terminal_fingerprint(
    row: Mapping[str, object],
) -> tuple[object, object, object, object, object]:
    return (
        row.get("settlement_status"),
        row.get("final_points"),
        row.get("actual_minutes"),
        row.get("participation_status"),
        row.get("exclusion_reason"),
    )


def _is_terminal(row: Mapping[str, object]) -> bool:
    return row.get("settlement_status") in _TERMINAL_SETTLEMENT_STATUSES


def _is_authoritative_terminal(row: Mapping[str, object]) -> bool:
    return row.get("settlement_status") in _AUTHORITATIVE_SETTLEMENT_STATUSES


def _is_incomplete_settlement(row: Mapping[str, object]) -> bool:
    return row.get("settlement_status") in _INCOMPLETE_SETTLEMENT_STATUSES


def _is_known_participation(row: Mapping[str, object]) -> bool:
    return row.get("participation_status") in _KNOWN_PARTICIPATION_STATUSES


def _policy_key(row: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        str(row.get("prediction_id")),
        str(row.get("settlement_policy_id")),
        str(row.get("settlement_policy_version")),
    )


def _settlement_history_sort_key(row: Mapping[str, object]) -> tuple[datetime, datetime, str, str]:
    return (
        _coerce_utc_datetime(
            row["settlement_source_timestamp_utc"],
            "settlement_source_timestamp_utc",
        ),
        _coerce_utc_datetime(row["settlement_timestamp_utc"], "settlement_timestamp_utc"),
        str(row.get("settlement_evidence_record_hash", "")),
        str(row.get("settlement_id", "")),
    )


def _settlement_chronology_key(row: Mapping[str, object]) -> tuple[datetime, datetime]:
    return (
        _coerce_utc_datetime(
            row["settlement_source_timestamp_utc"],
            "settlement_source_timestamp_utc",
        ),
        _coerce_utc_datetime(row["settlement_timestamp_utc"], "settlement_timestamp_utc"),
    )


def _is_strictly_later(
    candidate: Mapping[str, object],
    baseline: Mapping[str, object],
) -> bool:
    return _settlement_chronology_key(candidate) > _settlement_chronology_key(baseline)


def _settlement_rows_conflict_reason(
    rows: Sequence[Mapping[str, object]],
) -> str | None:
    if any(row.get("settlement_status") == "conflicting" for row in rows):
        return "settlement_contract_conflicting"
    ordered = tuple(sorted(rows, key=_settlement_history_sort_key))
    for index, row in enumerate(ordered):
        for prior in ordered[:index]:
            reason = _settlement_pair_conflict_reason(prior, row)
            if reason is not None:
                return reason
    return None


def _settlement_pair_conflict_reason(
    first: Mapping[str, object],
    second: Mapping[str, object],
) -> str | None:
    if _policy_key(first) != _policy_key(second):
        return None
    identity_reason = _identity_conflict_reason(first, second)
    if identity_reason is not None:
        return identity_reason
    if _same_source_identity_and_timestamp(first, second) and (
        _settlement_values_fingerprint(first) != _settlement_values_fingerprint(second)
    ):
        return "same_source_timestamp_conflicting_settlement"
    if _contradictory_identity_blocks_resolution(first, second):
        return "contradictory_identity_evidence"
    if _final_points_conflict(first, second):
        return "conflicting_terminal_outcome"
    if _actual_minutes_conflict(first, second):
        return "conflicting_terminal_outcome"
    if _participation_conflict(first, second):
        return "conflicting_terminal_outcome"
    if _void_state_conflict(first, second):
        return "conflicting_terminal_outcome"
    older, later = _chronological_pair(first, second)
    if _known_authoritative_information_regressed(older, later):
        return "known_authoritative_information_regressed"
    return None


def _identity_conflict_reason(
    first: Mapping[str, object],
    second: Mapping[str, object],
) -> str | None:
    for field_name in ("canonical_event_id", "player_id"):
        first_value = first.get(field_name)
        second_value = second.get(field_name)
        if (
            first_value not in (None, "")
            and second_value not in (None, "")
            and str(first_value) != str(second_value)
        ):
            return f"conflicting_{field_name}"
    return None


def _same_source_identity_and_timestamp(
    first: Mapping[str, object],
    second: Mapping[str, object],
) -> bool:
    return (
        first.get("settlement_provider") == second.get("settlement_provider")
        and first.get("settlement_source_id") == second.get("settlement_source_id")
        and first.get("settlement_source_timestamp_utc")
        == second.get("settlement_source_timestamp_utc")
    )


def _settlement_values_fingerprint(row: Mapping[str, object]) -> tuple[object, ...]:
    return (
        row.get("settlement_status"),
        row.get("final_points"),
        row.get("actual_minutes"),
        row.get("participation_status"),
        row.get("exclusion_reason"),
    )


def _contradictory_identity_blocks_resolution(
    first: Mapping[str, object],
    second: Mapping[str, object],
) -> bool:
    return (
        _asserts_contradictory_identity(first) and _is_authoritative_terminal(second)
    ) or (_asserts_contradictory_identity(second) and _is_authoritative_terminal(first))


def _asserts_contradictory_identity(row: Mapping[str, object]) -> bool:
    return (
        row.get("settlement_status") in _CONTRADICTORY_SETTLEMENT_STATUSES
        or row.get("event_identity_status") in _CONTRADICTORY_IDENTITY_STATUSES
        or row.get("player_identity_status") in _CONTRADICTORY_IDENTITY_STATUSES
    )


def _final_points_conflict(
    first: Mapping[str, object],
    second: Mapping[str, object],
) -> bool:
    first_points = first.get("final_points")
    second_points = second.get("final_points")
    return (
        first_points is not None
        and second_points is not None
        and first_points != second_points
    )


def _actual_minutes_conflict(
    first: Mapping[str, object],
    second: Mapping[str, object],
) -> bool:
    first_minutes = first.get("actual_minutes")
    second_minutes = second.get("actual_minutes")
    return (
        _is_authoritative_terminal(first)
        and _is_authoritative_terminal(second)
        and first_minutes is not None
        and second_minutes is not None
        and first_minutes != second_minutes
    )


def _participation_conflict(
    first: Mapping[str, object],
    second: Mapping[str, object],
) -> bool:
    first_status = first.get("participation_status")
    second_status = second.get("participation_status")
    if first_status == second_status or "unknown" in {first_status, second_status}:
        return False
    return {first_status, second_status} in (
        {"participated", "did_not_participate"},
        {"zero_minutes", "did_not_participate"},
    )


def _void_state_conflict(
    first: Mapping[str, object],
    second: Mapping[str, object],
) -> bool:
    if not (_is_authoritative_terminal(first) and _is_authoritative_terminal(second)):
        return False
    first_status = first.get("settlement_status")
    second_status = second.get("settlement_status")
    if first_status != second_status and {first_status, second_status} == {
        "settled",
        "void",
    }:
        return True
    return (
        first_status == "void"
        and second_status == "void"
        and first.get("exclusion_reason") != second.get("exclusion_reason")
    )


def _chronological_pair(
    first: Mapping[str, object],
    second: Mapping[str, object],
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    if _is_strictly_later(first, second):
        return second, first
    return first, second


def _known_authoritative_information_regressed(
    older: Mapping[str, object],
    later: Mapping[str, object],
) -> bool:
    if not (
        _is_authoritative_terminal(older)
        and _is_strictly_later(later, older)
        and not _is_authoritative_terminal(later)
    ):
        return False
    if older.get("final_points") is not None and later.get("final_points") is None:
        return True
    if older.get("actual_minutes") is not None and later.get("actual_minutes") is None:
        return True
    return _is_known_participation(older) and later.get("participation_status") == "unknown"


def _effective_settlement_sort_key(
    row: Mapping[str, object],
) -> tuple[int, int, datetime, datetime, str]:
    source_time, settlement_time = _settlement_chronology_key(row)
    return (
        _effective_status_rank(row),
        _settlement_completion_score(row),
        source_time,
        settlement_time,
        str(row.get("settlement_evidence_record_hash", "")),
    )


def _effective_status_rank(row: Mapping[str, object]) -> int:
    if _is_authoritative_terminal(row):
        return 2
    if _is_incomplete_settlement(row):
        return 1
    return 0


def _settlement_completion_score(row: Mapping[str, object]) -> int:
    score = 0
    if row.get("final_points") is not None:
        score += 1
    if row.get("actual_minutes") is not None:
        score += 1
    if _is_known_participation(row):
        score += 1
    if row.get("event_identity_status") == "resolved":
        score += 1
    if row.get("player_identity_status") == "resolved":
        score += 1
    return score


def _effective_settlement_ordering_rule() -> str:
    return (
        "after compatibility checks, valid authoritative settled/void evidence "
        "wins over pending, unresolved, and manual-review evidence; then more "
        "complete compatible evidence wins; then later "
        "settlement_source_timestamp_utc, later settlement_timestamp_utc, and "
        "canonical record hash only for content-equivalent deterministic ties"
    )


def _compatible_enrichment_rule() -> str:
    return (
        "final_points: None -> finite may enrich incomplete evidence, same finite "
        "is compatible, different finite values conflict, finite -> None cannot "
        "supersede known authoritative points. actual_minutes: None -> finite "
        "including 0.0 may enrich, same finite is compatible, different finite "
        "authoritative minutes conflict, finite -> None cannot supersede known "
        "authoritative minutes. participation_status: unknown -> participated, "
        "zero_minutes, or did_not_participate may enrich; known participated or "
        "zero_minutes conflicts with did_not_participate; known status cannot "
        "regress to unknown. void state: pending, unresolved, or manual-review "
        "evidence may progress to supported void evidence, but final settled and "
        "final void evidence conflict; conflicting final void reasons conflict; "
        "null actual_minutes and zero actual_minutes are distinct"
    )


def _settlement_batch_id(
    *,
    records: Sequence[Mapping[str, object]],
    conflicts: Sequence[Mapping[str, object]],
    operating_date: str,
    policy: NBAPlayerPointsSettlementPolicy,
) -> str:
    payload = {
        "schema_version": NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_SCHEMA_VERSION,
        "operating_date": operating_date,
        "policy": policy.to_dict(),
        "settlement_ids": [row["settlement_id"] for row in records],
        "settlement_hashes": [row["settlement_evidence_record_hash"] for row in records],
        "conflict_hashes": [row["settlement_conflict_hash"] for row in conflicts],
    }
    return "nba-settlement-batch-" + _canonical_payload_sha256(payload)[:32]


def _conflict_record(
    row: Mapping[str, object],
    *,
    existing: Mapping[str, object],
    reason: str,
) -> Mapping[str, object]:
    payload = {
        "settlement_conflict_id": "",
        "settlement_conflict_hash": "",
        "conflict_reason": reason,
        "prediction_id": row.get("prediction_id"),
        "settlement_policy_id": row.get("settlement_policy_id"),
        "settlement_policy_version": row.get("settlement_policy_version"),
        "new_settlement_id": row.get("settlement_id"),
        "new_settlement_evidence_record_hash": row.get("settlement_evidence_record_hash"),
        "existing_settlement_id": existing.get("settlement_id"),
        "existing_settlement_evidence_record_hash": existing.get(
            "settlement_evidence_record_hash"
        ),
        "new_terminal_fingerprint": list(_terminal_fingerprint(row)),
        "existing_terminal_fingerprint": list(_terminal_fingerprint(existing)),
    }
    payload["settlement_conflict_id"] = "nba-settlement-conflict-" + _canonical_payload_sha256(
        {
            key: value
            for key, value in payload.items()
            if key not in {"settlement_conflict_id", "settlement_conflict_hash"}
        }
    )[:32]
    payload["settlement_conflict_hash"] = _record_hash(
        payload,
        "settlement_conflict_hash",
    )
    return MappingProxyType(payload)


def _dedupe_conflicts(
    conflicts: Sequence[Mapping[str, object]]
) -> tuple[Mapping[str, object], ...]:
    deduped: dict[str, Mapping[str, object]] = {}
    for conflict in conflicts:
        deduped[str(conflict["settlement_conflict_id"])] = conflict
    return tuple(deduped.values())


def _iter_completed_settlement_segments(
    root: Path,
    config: NBAPlayerPointsSettlementEvidenceWriterConfig,
) -> tuple[Path, ...]:
    segments_root = root / config.settlement_dir_name / config.segments_dir_name
    if not segments_root.exists():
        return ()
    segments: list[Path] = []
    for candidate in sorted(segments_root.glob("*/*")):
        if candidate.name.startswith("."):
            continue
        if candidate.is_dir() and (candidate / config.completion_marker_file_name).exists():
            segments.append(candidate)
    return tuple(segments)


def _settlement_segment_directory(
    root: Path,
    operating_date: str,
    settlement_batch_id: str,
    config: NBAPlayerPointsSettlementEvidenceWriterConfig,
) -> Path:
    safe_operating_date = _require_operating_date(operating_date, "operating_date")
    safe_batch_id = _require_safe_path_component(
        settlement_batch_id,
        "settlement_batch_id",
    )
    return (
        root
        / config.settlement_dir_name
        / config.segments_dir_name
        / safe_operating_date
        / safe_batch_id
    )


def _read_existing_completed_manifest(
    segment_dir: Path,
    config: NBAPlayerPointsSettlementEvidenceWriterConfig,
) -> Mapping[str, object]:
    if segment_dir.is_symlink():
        raise NBAPlayerPointsSettlementEvidenceError(
            f"settlement segment is a symlink: {segment_dir}"
        )
    marker_path = segment_dir / config.completion_marker_file_name
    if not marker_path.exists():
        raise NBAPlayerPointsSettlementEvidenceError(
            f"settlement segment exists without completion marker: {segment_dir}"
        )
    manifest_path = segment_dir / NBA_PLAYER_POINTS_SETTLEMENT_MANIFEST_FILE
    manifest = _read_json_file(manifest_path)
    marker = _read_json_file(marker_path)
    if marker.get("manifest_hash") != _sha256_file(manifest_path):
        raise NBAPlayerPointsSettlementEvidenceError(
            "existing settlement segment failed completion-marker verification"
        )
    return manifest


def _manifest_policy_matches(
    manifest: Mapping[str, object],
    policy: NBAPlayerPointsSettlementPolicy,
) -> bool:
    manifest_policy = manifest.get("settlement_policy")
    if not isinstance(manifest_policy, Mapping):
        return False
    return (
        manifest_policy.get("settlement_policy_id") == policy.settlement_policy_id
        and manifest_policy.get("settlement_policy_version")
        == policy.settlement_policy_version
    )


def _validate_config(config: NBAPlayerPointsSettlementEvidenceWriterConfig) -> None:
    if not isinstance(config, NBAPlayerPointsSettlementEvidenceWriterConfig):
        raise TypeError("config must be NBAPlayerPointsSettlementEvidenceWriterConfig")


class _SettlementRootLock:
    def __init__(
        self,
        evidence_root: Path,
        config: NBAPlayerPointsSettlementEvidenceWriterConfig,
    ) -> None:
        self._evidence_root = evidence_root
        self._config = config
        self._lock_path = evidence_root / NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_LOCK_FILE
        self._fd: int | None = None

    def __enter__(self) -> "_SettlementRootLock":
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
                os.write(
                    self._fd,
                    _json_file_bytes(
                        {
                            "pid": os.getpid(),
                            "created_at_utc": _format_utc(datetime.now(tz=_UTC)),
                        }
                    ),
                )
                os.fsync(self._fd)
                return self
            except FileExistsError as exc:
                if time.monotonic() >= deadline:
                    raise NBAPlayerPointsSettlementEvidenceError(
                        f"settlement writer lock is already held: {self._lock_path}"
                    ) from exc
                time.sleep(0.01)
            except OSError as exc:
                raise NBAPlayerPointsSettlementEvidenceError(
                    f"unable to acquire settlement writer lock: {self._lock_path}"
                ) from exc

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            pass


def _evidence_root(
    path: Path,
    config: NBAPlayerPointsSettlementEvidenceWriterConfig,
) -> Path:
    base = path.expanduser()
    evidence_root = base if base.name == config.evidence_dir_name else base / config.evidence_dir_name
    if evidence_root.name != config.evidence_dir_name:
        raise NBAPlayerPointsSettlementEvidenceError("evidence root name mismatch")
    _assert_no_existing_symlink(evidence_root)
    return evidence_root


def _make_directory(path: Path) -> None:
    _assert_no_existing_symlink(path)
    path.mkdir(parents=True, exist_ok=True)
    _assert_no_existing_symlink(path)


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
                raise NBAPlayerPointsSettlementEvidenceError(
                    f"path component is a symlink: {probe}"
                )
        except OSError as exc:
            raise NBAPlayerPointsSettlementEvidenceError(
                f"unable to inspect path: {probe}"
            ) from exc


def _ensure_under_root(root: Path, path: Path, field_name: str) -> None:
    root_resolved = root.resolve(strict=False)
    path_resolved = path.resolve(strict=False)
    if path_resolved == root_resolved:
        return
    if root_resolved not in path_resolved.parents:
        raise NBAPlayerPointsSettlementEvidenceError(f"{field_name} escapes evidence root")


def _relative_to_root(path: Path, root: Path) -> str:
    _ensure_under_root(root, path, "path")
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise NBAPlayerPointsSettlementEvidenceError("path escapes evidence root") from exc


def _write_bytes_verified(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    if path.read_bytes() != data:
        raise NBAPlayerPointsSettlementEvidenceError(f"short write detected: {path}")


def _write_json_file(path: Path, payload: Mapping[str, object]) -> None:
    path.write_bytes(_json_file_bytes(payload))


def _read_json_file(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NBAPlayerPointsSettlementEvidenceError(f"invalid JSON file: {path}") from exc
    if not isinstance(payload, Mapping):
        raise NBAPlayerPointsSettlementEvidenceError(
            f"JSON file must contain an object: {path}"
        )
    return MappingProxyType(_json_clone_mapping(payload))


def _read_jsonl_strict(path: Path) -> tuple[Mapping[str, object], ...]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise NBAPlayerPointsSettlementEvidenceError(
            f"unable to read JSONL file: {path}"
        ) from exc
    if not data:
        return ()
    if not data.endswith(b"\n"):
        raise NBAPlayerPointsSettlementEvidenceError(
            f"JSONL frame missing final newline: {path}"
        )
    rows: list[Mapping[str, object]] = []
    for line_number, raw_line in enumerate(data.splitlines(), start=1):
        if not raw_line.strip():
            raise NBAPlayerPointsSettlementEvidenceError(
                f"empty JSONL line at {path}:{line_number}"
            )
        try:
            payload = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NBAPlayerPointsSettlementEvidenceError(
                f"invalid JSONL at {path}:{line_number}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise NBAPlayerPointsSettlementEvidenceError(
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


def _record_hash(payload: Mapping[str, object], hash_field: str) -> str:
    return _canonical_payload_sha256(
        {key: value for key, value in payload.items() if key != hash_field}
    )


def _canonical_payload_sha256(payload: object) -> str:
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
        raise NBAPlayerPointsSettlementEvidenceError(
            "canonical JSON cannot contain NaN or infinity"
        ) from exc


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
            raise NBAPlayerPointsSettlementEvidenceError("numeric values must be finite")
        return value
    return value


def _json_clone_mapping(value: Mapping[str, object]) -> dict[str, object]:
    cloned = json.loads(
        json.dumps(_json_ready(value), sort_keys=True, ensure_ascii=True, allow_nan=False)
    )
    if not isinstance(cloned, dict):
        raise NBAPlayerPointsSettlementEvidenceError("value must be an object")
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
            raise NBAPlayerPointsSettlementEvidenceError(
                f"{field_name} must be an ISO-8601 UTC timestamp"
            ) from exc
    else:
        raise NBAPlayerPointsSettlementEvidenceError(
            f"{field_name} must be an ISO-8601 UTC timestamp"
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NBAPlayerPointsSettlementEvidenceError(f"{field_name} must be timezone-aware")
    if parsed.utcoffset() != timedelta(0):
        raise NBAPlayerPointsSettlementEvidenceError(f"{field_name} must be UTC")
    return parsed.astimezone(_UTC)


def _format_utc(value: datetime) -> str:
    return _coerce_utc_datetime(value, "timestamp").isoformat().replace("+00:00", "Z")


def _require_operating_date(value: object, field_name: str) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        text = value.isoformat()
    else:
        text = _require_text(value, field_name)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) is None:
        raise NBAPlayerPointsSettlementEvidenceError(
            f"{field_name} must use strict YYYY-MM-DD format"
        )
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise NBAPlayerPointsSettlementEvidenceError(
            f"{field_name} must be a valid date"
        ) from exc
    if parsed.isoformat() != text:
        raise NBAPlayerPointsSettlementEvidenceError(
            f"{field_name} must use strict YYYY-MM-DD format"
        )
    return text


def _require_safe_path_component(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if text in {".", ".."} or ".." in text:
        raise NBAPlayerPointsSettlementEvidenceError(f"{field_name} must not contain '..'")
    if "/" in text or "\\" in text:
        raise NBAPlayerPointsSettlementEvidenceError(
            f"{field_name} must not contain path separators"
        )
    if Path(text).is_absolute():
        raise NBAPlayerPointsSettlementEvidenceError(f"{field_name} must not be absolute")
    return text


def _require_identifier(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if text == "0":
        raise NBAPlayerPointsSettlementEvidenceError(f"{field_name} is required")
    return text


def _require_text(value: object, field_name: str) -> str:
    if value is None:
        raise NBAPlayerPointsSettlementEvidenceError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise NBAPlayerPointsSettlementEvidenceError(f"{field_name} is required")
    return text


def _require_sha256(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold()
    if _SHA256_RE.fullmatch(text) is None:
        raise NBAPlayerPointsSettlementEvidenceError(f"{field_name} must be lowercase SHA-256")
    return text


def _require_commit_sha(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold()
    if _COMMIT_SHA_RE.fullmatch(text) is None:
        raise NBAPlayerPointsSettlementEvidenceError(
            f"{field_name} must be a 7-40 character lowercase git SHA"
        )
    return text


def _call_failure_hook(failure_hook: FailureHook | None, stage: str) -> None:
    if failure_hook is not None:
        failure_hook(stage)


__all__ = [
    "NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_COMPLETION_STATUSES",
    "NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_SCHEMA_VERSION",
    "NBAPlayerPointsSettlementEvidenceError",
    "NBAPlayerPointsSettlementEvidenceIntegrityReport",
    "NBAPlayerPointsSettlementEvidenceWriteResult",
    "NBAPlayerPointsSettlementEvidenceWriterConfig",
    "NBAPlayerPointsSettlementPolicy",
    "default_settlement_policy",
    "resolve_nba_player_points_effective_settlement",
    "settlement_evidence_schema_definition",
    "verify_nba_player_points_settlement_evidence",
    "write_nba_player_points_settlement_evidence",
]
