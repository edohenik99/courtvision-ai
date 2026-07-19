"""Closing-bound NBA player-points settlement evidence contracts.

This research-only module binds settlement approval and evidence to explicitly
named physical closing observation and selection batches.  It performs no
provider I/O and never resolves a "latest" batch.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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
)
from courtvision.sports.nba.player_points_settlement import (
    NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION,
    NBAPlayerPointsSettlementRow,
    validate_settlement_rows,
)


NBA_PLAYER_POINTS_CLOSING_PREREQUISITE_SCHEMA_VERSION: Final = (
    "nba-player-points-closing-prerequisite-v1"
)
NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION: Final = (
    "nba-player-points-settlement-evidence-v2"
)
NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_CONTRACT_VERSION: Final = (
    "nba-player-points-settlement-approval-v2"
)
NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_REQUEST_SCHEMA_VERSION: Final = (
    "nba-player-points-settlement-approval-request-v2"
)
NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_RECEIPT_SCHEMA_VERSION: Final = (
    "nba-player-points-settlement-approval-receipt-v2"
)
NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION: Final = (
    "nba-player-points-manual-run-v2"
)
NBA_PLAYER_POINTS_MANUAL_PLAN_V2_SCHEMA_VERSION: Final = (
    "nba-player-points-manual-plan-v2"
)

_OBSERVATION_SCHEMA: Final = "nba-player-points-closing-v1"
_SELECTION_SCHEMA: Final = "nba-player-points-closing-selection-v1"
_OBSERVATION_FILE: Final = "closing_observations.jsonl"
_OBSERVATION_CONFLICT_FILE: Final = "closing_conflicts.jsonl"
_OBSERVATION_MANIFEST_FILE: Final = "closing_manifest.json"
_SELECTION_FILE: Final = "selected_closing_rows.jsonl"
_SELECTION_MANIFEST_FILE: Final = "selection_manifest.json"
_INTEGRITY_FILE: Final = "integrity_report.json"
_COMPLETE_FILE: Final = "COMPLETE"
_UTC: Final = timezone.utc
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SAFE_COMPONENT_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WINDOWS_REPARSE_POINT: Final = 0x400
_WINDOWS_INVALID_ATTRIBUTES: Final = 0xFFFFFFFF
_SETTLEMENT_ROWS_FILE: Final = "settlement_rows.jsonl"
_SETTLEMENT_MANIFEST_FILE: Final = "settlement_manifest.json"
_CLOSING_PREREQUISITE_FILE: Final = "closing_prerequisite.json"
_APPROVAL_ENVELOPE_FILE: Final = "approval_envelope.json"
_SETTLEMENT_LOCK_FILE: Final = ".settlement-writer.lock"

FailureHook = Callable[[str], None]


class NBAPlayerPointsClosingBindingError(ValueError):
    """Raised when exact closing prerequisite evidence fails closed."""


@dataclass(frozen=True, slots=True)
class FrozenClosingFile:
    """One immutable in-memory file snapshot used during plan construction."""

    path: Path
    locator: str
    data: bytes
    sha256: str
    size_bytes: int
    filesystem_identity: tuple[int, int, int, int]

    def identity_dict(self) -> dict[str, object]:
        return {
            "locator": self.locator,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class ObservationBatchSnapshot:
    physical_observation_batch_id: str
    segment_locator: str
    observation_jsonl_sha256: str
    observation_internal_manifest_hash: str
    observation_manifest_file_sha256: str
    completion_marker_sha256: str
    operating_date: str
    record_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "physical_observation_batch_id": self.physical_observation_batch_id,
            "segment_locator": self.segment_locator,
            "observation_jsonl_sha256": self.observation_jsonl_sha256,
            "observation_internal_manifest_hash": self.observation_internal_manifest_hash,
            "observation_manifest_file_sha256": self.observation_manifest_file_sha256,
            "completion_marker_sha256": self.completion_marker_sha256,
            "operating_date": self.operating_date,
            "record_count": self.record_count,
        }


@dataclass(frozen=True, slots=True)
class SelectionBatchSnapshot:
    physical_selection_batch_id: str
    segment_locator: str
    selection_jsonl_sha256: str
    selection_internal_manifest_hash: str
    selection_manifest_file_sha256: str
    completion_marker_sha256: str
    operating_date: str
    record_count: int
    effective_selection_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "physical_selection_batch_id": self.physical_selection_batch_id,
            "segment_locator": self.segment_locator,
            "selection_jsonl_sha256": self.selection_jsonl_sha256,
            "selection_internal_manifest_hash": self.selection_internal_manifest_hash,
            "selection_manifest_file_sha256": self.selection_manifest_file_sha256,
            "completion_marker_sha256": self.completion_marker_sha256,
            "operating_date": self.operating_date,
            "record_count": self.record_count,
            "effective_selection_count": self.effective_selection_count,
        }


@dataclass(frozen=True, slots=True)
class PredictionClosingMapping:
    prediction_id: str
    prediction_run_id: str
    prediction_ledger_record_hash: str
    canonical_event_id: str
    operating_date: str
    player_id: str
    sportsbook: str
    market: str
    physical_selection_batch_id: str
    closing_selection_id: str
    selection_record_hash: str
    selected_at_utc: str
    source_observation_batch_id: str
    source_observation_id: str
    source_observation_record_hash: str
    observation_timestamp_utc: str
    closing_line: str
    closing_american_odds: int

    def to_dict(self) -> dict[str, object]:
        return {
            "prediction_id": self.prediction_id,
            "prediction_run_id": self.prediction_run_id,
            "prediction_ledger_record_hash": self.prediction_ledger_record_hash,
            "canonical_event_id": self.canonical_event_id,
            "operating_date": self.operating_date,
            "player_id": self.player_id,
            "sportsbook": self.sportsbook,
            "market": self.market,
            "physical_selection_batch_id": self.physical_selection_batch_id,
            "closing_selection_id": self.closing_selection_id,
            "selection_record_hash": self.selection_record_hash,
            "selected_at_utc": self.selected_at_utc,
            "source_observation_batch_id": self.source_observation_batch_id,
            "source_observation_id": self.source_observation_id,
            "source_observation_record_hash": self.source_observation_record_hash,
            "observation_timestamp_utc": self.observation_timestamp_utc,
            "closing_line": self.closing_line,
            "closing_american_odds": self.closing_american_odds,
        }


@dataclass(frozen=True, slots=True)
class ClosingPrerequisite:
    schema_version: str
    binding_mode: str
    operating_date: str
    closing_policy: Mapping[str, object]
    observation_batches: tuple[ObservationBatchSnapshot, ...]
    selection_batch: SelectionBatchSnapshot
    prediction_mappings: tuple[PredictionClosingMapping, ...]
    mapping_aggregate_sha256: str
    closing_prerequisite_sha256: str
    logical_closing_id: str | None = None

    def unsigned_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "binding_mode": self.binding_mode,
            "operating_date": self.operating_date,
            "closing_policy": _json_clone(self.closing_policy),
            "observation_batches": [item.to_dict() for item in self.observation_batches],
            "selection_batch": self.selection_batch.to_dict(),
            "prediction_mappings": [item.to_dict() for item in self.prediction_mappings],
            "mapping_aggregate_sha256": self.mapping_aggregate_sha256,
        }
        if self.logical_closing_id is not None:
            payload["logical_closing_id"] = self.logical_closing_id
        return payload

    def to_dict(self) -> dict[str, object]:
        return {
            **self.unsigned_dict(),
            "closing_prerequisite_sha256": self.closing_prerequisite_sha256,
        }


@dataclass(frozen=True, slots=True)
class ClosingPrerequisiteValidationResult:
    ok: bool
    binding_status: str
    violations: tuple[str, ...]
    prerequisite: ClosingPrerequisite | None
    snapshot: "ClosingEvidenceSnapshot | None"

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "binding_status": self.binding_status,
            "violations": list(self.violations),
            "closing_prerequisite": (
                self.prerequisite.to_dict() if self.prerequisite is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class ClosingEvidenceSnapshot:
    evidence_root: Path
    files: tuple[FrozenClosingFile, ...]
    observation_records: Mapping[str, Mapping[str, object]]
    selection_records: tuple[Mapping[str, object], ...]
    prediction_records: Mapping[str, Mapping[str, object]]
    prerequisite: ClosingPrerequisite

    def assert_unchanged(self) -> None:
        """Fail closed if bytes or path identity changed since planning."""

        for frozen in self.files:
            _reject_reparse_components(frozen.path)
            try:
                current_stat = frozen.path.stat()
                current = frozen.path.read_bytes()
            except OSError as exc:
                raise NBAPlayerPointsClosingBindingError(
                    f"closing evidence changed after planning: {frozen.locator}"
                ) from exc
            identity = _filesystem_identity(current_stat)
            if (
                identity != frozen.filesystem_identity
                or len(current) != frozen.size_bytes
                or _sha256_bytes(current) != frozen.sha256
            ):
                raise NBAPlayerPointsClosingBindingError(
                    f"closing evidence changed after planning: {frozen.locator}"
                )


def canonical_closing_line(value: object) -> str:
    """Return the required non-exponent canonical decimal line identity."""

    if isinstance(value, bool) or value is None:
        raise NBAPlayerPointsClosingBindingError("closing line must be a finite decimal")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise NBAPlayerPointsClosingBindingError(
            "closing line must be a finite decimal"
        ) from exc
    if not decimal_value.is_finite() or decimal_value < 0:
        raise NBAPlayerPointsClosingBindingError(
            "closing line must be a finite non-negative decimal"
        )
    text = format(decimal_value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        text = "0"
    return text


def canonical_json_bytes(payload: object) -> bytes:
    """Canonical UTF-8 JSON with no trailing newline."""

    try:
        return json.dumps(
            _json_ready(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise NBAPlayerPointsClosingBindingError(
            "canonical JSON cannot contain unsupported, NaN, or infinite values"
        ) from exc


def canonical_sha256(payload: object) -> str:
    return _sha256_bytes(canonical_json_bytes(payload))


def build_nba_player_points_closing_prerequisite(
    evidence_root: str | Path,
    *,
    operating_date: str,
    physical_observation_batch_ids: Sequence[str],
    physical_selection_batch_id: str,
    prediction_ids: Sequence[str],
    expected_closing_policy_id: str,
    expected_closing_policy_version: str,
    logical_closing_id: str | None = None,
) -> ClosingEvidenceSnapshot:
    """Load and bind only the explicitly named physical closing batches."""

    date_value = _require_operating_date(operating_date, "operating_date")
    observation_ids = tuple(
        sorted(
            _require_safe_component(item, "physical_observation_batch_ids[]")
            for item in physical_observation_batch_ids
        )
    )
    if not observation_ids or len(set(observation_ids)) != len(observation_ids):
        raise NBAPlayerPointsClosingBindingError(
            "physical_observation_batch_ids must be a non-empty unique set"
        )
    selection_id = _require_safe_component(
        physical_selection_batch_id,
        "physical_selection_batch_id",
    )
    requested_ids = tuple(sorted(_require_identifier(item, "prediction_ids[]") for item in prediction_ids))
    if not requested_ids or len(set(requested_ids)) != len(requested_ids):
        raise NBAPlayerPointsClosingBindingError(
            "prediction_ids must be a non-empty unique set"
        )
    policy_id = _require_identifier(expected_closing_policy_id, "expected_closing_policy_id")
    policy_version = _require_text(
        expected_closing_policy_version,
        "expected_closing_policy_version",
    )
    logical_id = (
        _require_safe_component(logical_closing_id, "logical_closing_id")
        if logical_closing_id is not None
        else None
    )
    root = _actual_evidence_root(Path(evidence_root))
    _reject_reparse_components(root)
    frozen_files: list[FrozenClosingFile] = []
    observation_snapshots: list[ObservationBatchSnapshot] = []
    observations_by_id: dict[str, Mapping[str, object]] = {}
    observation_batch_by_id: dict[str, str] = {}

    for batch_id in observation_ids:
        segment = (
            root
            / "closing"
            / "observations"
            / "segments"
            / date_value
            / batch_id
        )
        snapshot, records, files = _load_observation_batch(root, segment, batch_id, date_value)
        observation_snapshots.append(snapshot)
        frozen_files.extend(files)
        for record in records:
            record_id = _require_identifier(
                record.get("closing_observation_id"),
                "closing_observation_id",
            )
            if record_id in observations_by_id:
                raise NBAPlayerPointsClosingBindingError(
                    f"duplicate closing observation ID: {record_id}"
                )
            observations_by_id[record_id] = record
            observation_batch_by_id[record_id] = batch_id

    selection_segment = (
        root
        / "closing"
        / "selections"
        / "segments"
        / date_value
        / selection_id
    )
    selection_snapshot, selection_records, selection_files, closing_policy = _load_selection_batch(
        root,
        selection_segment,
        selection_id,
        date_value,
    )
    frozen_files.extend(selection_files)
    if closing_policy.get("closing_policy_id") != policy_id:
        raise NBAPlayerPointsClosingBindingError("closing policy ID mismatch")
    if closing_policy.get("closing_policy_version") != policy_version:
        raise NBAPlayerPointsClosingBindingError("closing policy version mismatch")

    mappings: list[PredictionClosingMapping] = []
    prediction_records: dict[str, Mapping[str, object]] = {}
    selected_prediction_ids: list[str] = []
    seen_selection_ids: set[str] = set()
    discovered_observation_batches: set[str] = set()
    for selection in selection_records:
        closing_selection_id = _require_identifier(
            selection.get("closing_selection_id"),
            "closing_selection_id",
        )
        if closing_selection_id in seen_selection_ids:
            raise NBAPlayerPointsClosingBindingError(
                f"duplicate closing selection ID: {closing_selection_id}"
            )
        seen_selection_ids.add(closing_selection_id)
        if selection.get("selection_status") != "selected":
            raise NBAPlayerPointsClosingBindingError(
                "selection batch contains a non-effective selection"
            )
        prediction_id = _require_identifier(selection.get("prediction_id"), "prediction_id")
        selected_prediction_ids.append(prediction_id)
        if prediction_id not in set(requested_ids):
            raise NBAPlayerPointsClosingBindingError(
                f"unknown or extra prediction mapping: {prediction_id}"
            )
        observation_id = _require_identifier(
            selection.get("selected_observation_id"),
            "selected_observation_id",
        )
        observation = observations_by_id.get(observation_id)
        if observation is None:
            raise NBAPlayerPointsClosingBindingError(
                f"unknown source observation: {observation_id}"
            )
        observation_batch_id = observation_batch_by_id[observation_id]
        discovered_observation_batches.add(observation_batch_id)
        _validate_selection_observation_lineage(
            selection,
            observation,
            closing_policy=closing_policy,
            operating_date=date_value,
        )
        ledger_record, ledger_file = _load_prediction_record(root, observation)
        frozen_files.append(ledger_file)
        if prediction_id in prediction_records:
            raise NBAPlayerPointsClosingBindingError(
                f"multiple effective selections for prediction: {prediction_id}"
            )
        prediction_records[prediction_id] = ledger_record
        _validate_prediction_mapping(selection, observation, ledger_record)
        odds = selection.get("closing_american_odds")
        if isinstance(odds, bool) or not isinstance(odds, int):
            raise NBAPlayerPointsClosingBindingError(
                "closing American odds must be a signed integer"
            )
        mappings.append(
            PredictionClosingMapping(
                prediction_id=prediction_id,
                prediction_run_id=_require_identifier(
                    observation.get("prediction_run_id"),
                    "prediction_run_id",
                ),
                prediction_ledger_record_hash=_require_sha256(
                    observation.get("prediction_record_hash"),
                    "prediction_record_hash",
                ),
                canonical_event_id=_require_identifier(
                    observation.get("canonical_event_id"),
                    "canonical_event_id",
                ),
                operating_date=date_value,
                player_id=_require_identifier(observation.get("player_id"), "player_id"),
                sportsbook=_require_text(observation.get("sportsbook"), "sportsbook"),
                market=_require_text(observation.get("market"), "market"),
                physical_selection_batch_id=selection_id,
                closing_selection_id=closing_selection_id,
                selection_record_hash=_require_sha256(
                    selection.get("selection_record_hash"),
                    "selection_record_hash",
                ),
                selected_at_utc=_require_utc_timestamp(
                    selection.get("selected_at_utc"),
                    "selected_at_utc",
                ),
                source_observation_batch_id=observation_batch_id,
                source_observation_id=observation_id,
                source_observation_record_hash=_require_sha256(
                    observation.get("closing_record_hash"),
                    "closing_record_hash",
                ),
                observation_timestamp_utc=_require_utc_timestamp(
                    observation.get("observation_timestamp_utc"),
                    "observation_timestamp_utc",
                ),
                closing_line=canonical_closing_line(selection.get("closing_line")),
                closing_american_odds=odds,
            )
        )

    if sorted(selected_prediction_ids) != list(requested_ids):
        raise NBAPlayerPointsClosingBindingError(
            "closing mapping prediction set does not exactly match requested prediction set"
        )
    if discovered_observation_batches != set(observation_ids):
        raise NBAPlayerPointsClosingBindingError(
            "closing mapping observation batch set does not exactly match expected batch set"
        )
    sorted_mappings = tuple(
        sorted(
            mappings,
            key=lambda item: (
                item.prediction_id,
                item.closing_selection_id,
                item.selection_record_hash,
            ),
        )
    )
    mapping_payloads = [item.to_dict() for item in sorted_mappings]
    mapping_aggregate = canonical_sha256(mapping_payloads)
    sorted_observation_snapshots = tuple(
        sorted(
            observation_snapshots,
            key=lambda item: item.physical_observation_batch_id,
        )
    )
    unsigned: dict[str, object] = {
        "schema_version": NBA_PLAYER_POINTS_CLOSING_PREREQUISITE_SCHEMA_VERSION,
        "binding_mode": "closing-bound",
        "operating_date": date_value,
        "closing_policy": _json_clone(closing_policy),
        "observation_batches": [item.to_dict() for item in sorted_observation_snapshots],
        "selection_batch": selection_snapshot.to_dict(),
        "prediction_mappings": mapping_payloads,
        "mapping_aggregate_sha256": mapping_aggregate,
    }
    if logical_id is not None:
        unsigned["logical_closing_id"] = logical_id
    prerequisite_hash = canonical_sha256(unsigned)
    prerequisite = ClosingPrerequisite(
        schema_version=NBA_PLAYER_POINTS_CLOSING_PREREQUISITE_SCHEMA_VERSION,
        binding_mode="closing-bound",
        operating_date=date_value,
        closing_policy=MappingProxyType(_json_clone(closing_policy)),
        observation_batches=sorted_observation_snapshots,
        selection_batch=selection_snapshot,
        prediction_mappings=sorted_mappings,
        mapping_aggregate_sha256=mapping_aggregate,
        closing_prerequisite_sha256=prerequisite_hash,
        logical_closing_id=logical_id,
    )
    unique_files = tuple(
        sorted(
            {item.locator: item for item in frozen_files}.values(),
            key=lambda item: item.locator,
        )
    )
    return ClosingEvidenceSnapshot(
        evidence_root=root,
        files=unique_files,
        observation_records=MappingProxyType(dict(observations_by_id)),
        selection_records=tuple(selection_records),
        prediction_records=MappingProxyType(dict(prediction_records)),
        prerequisite=prerequisite,
    )


def validate_nba_player_points_closing_prerequisite(
    evidence_root: str | Path,
    prerequisite_payload: Mapping[str, object],
) -> ClosingPrerequisiteValidationResult:
    """Rebuild an exact prerequisite from physical evidence and compare it."""

    try:
        payload = _require_mapping(prerequisite_payload, "closing_prerequisite")
        if payload.get("schema_version") != NBA_PLAYER_POINTS_CLOSING_PREREQUISITE_SCHEMA_VERSION:
            raise NBAPlayerPointsClosingBindingError("closing prerequisite schema mismatch")
        if payload.get("binding_mode") != "closing-bound":
            raise NBAPlayerPointsClosingBindingError("closing prerequisite binding mode mismatch")
        supplied_hash = _require_sha256(
            payload.get("closing_prerequisite_sha256"),
            "closing_prerequisite_sha256",
        )
        unsigned = {
            key: value
            for key, value in payload.items()
            if key != "closing_prerequisite_sha256"
        }
        if canonical_sha256(unsigned) != supplied_hash:
            raise NBAPlayerPointsClosingBindingError(
                "closing prerequisite SHA-256 mismatch"
            )
        observations = _require_sequence(
            payload.get("observation_batches"),
            "observation_batches",
        )
        selection = _require_mapping(payload.get("selection_batch"), "selection_batch")
        mappings = _require_sequence(payload.get("prediction_mappings"), "prediction_mappings")
        policy = _require_mapping(payload.get("closing_policy"), "closing_policy")
        for mapping in mappings:
            mapping_object = _require_mapping(mapping, "prediction_mappings[]")
            line = mapping_object.get("closing_line")
            if not isinstance(line, str) or canonical_closing_line(line) != line:
                raise NBAPlayerPointsClosingBindingError(
                    "closing line identity is not a canonical decimal string"
                )
        rebuilt = build_nba_player_points_closing_prerequisite(
            evidence_root,
            operating_date=_require_operating_date(
                payload.get("operating_date"),
                "operating_date",
            ),
            physical_observation_batch_ids=[
                _require_mapping(item, "observation_batches[]").get(
                    "physical_observation_batch_id"
                )
                for item in observations
            ],
            physical_selection_batch_id=selection.get("physical_selection_batch_id"),
            prediction_ids=[
                _require_mapping(item, "prediction_mappings[]").get("prediction_id")
                for item in mappings
            ],
            expected_closing_policy_id=policy.get("closing_policy_id"),
            expected_closing_policy_version=policy.get("closing_policy_version"),
            logical_closing_id=(
                str(payload["logical_closing_id"])
                if payload.get("logical_closing_id") is not None
                else None
            ),
        )
        if rebuilt.prerequisite.to_dict() != _json_clone(payload):
            raise NBAPlayerPointsClosingBindingError(
                "closing prerequisite does not match exact physical evidence"
            )
        return ClosingPrerequisiteValidationResult(
            ok=True,
            binding_status="closing-bound",
            violations=(),
            prerequisite=rebuilt.prerequisite,
            snapshot=rebuilt,
        )
    except (NBAPlayerPointsClosingBindingError, OSError, ValueError) as exc:
        return ClosingPrerequisiteValidationResult(
            ok=False,
            binding_status="invalid",
            violations=(str(exc),),
            prerequisite=None,
            snapshot=None,
        )


def _load_observation_batch(
    root: Path,
    segment: Path,
    batch_id: str,
    operating_date: str,
) -> tuple[ObservationBatchSnapshot, tuple[Mapping[str, object], ...], tuple[FrozenClosingFile, ...]]:
    files = _freeze_declared_segment_files(
        root,
        segment,
        manifest_name=_OBSERVATION_MANIFEST_FILE,
        required_names=(
            _OBSERVATION_FILE,
            _OBSERVATION_CONFLICT_FILE,
            _INTEGRITY_FILE,
            _OBSERVATION_MANIFEST_FILE,
            _COMPLETE_FILE,
        ),
    )
    by_name = {item.path.name: item for item in files}
    manifest = _parse_json_object(by_name[_OBSERVATION_MANIFEST_FILE].data, _OBSERVATION_MANIFEST_FILE)
    marker = _parse_json_object(by_name[_COMPLETE_FILE].data, _COMPLETE_FILE)
    if manifest.get("schema_version") != _OBSERVATION_SCHEMA:
        raise NBAPlayerPointsClosingBindingError("observation manifest schema mismatch")
    if manifest.get("closing_batch_id") != batch_id:
        raise NBAPlayerPointsClosingBindingError("physical observation batch ID mismatch")
    if manifest.get("operating_date") != operating_date:
        raise NBAPlayerPointsClosingBindingError("observation operating date mismatch")
    _verify_internal_hash(manifest, "closing_manifest_hash")
    _verify_marker(marker, by_name[_OBSERVATION_MANIFEST_FILE])
    _verify_declared_hash(manifest, "observation_file_hash", by_name[_OBSERVATION_FILE])
    _verify_declared_hash(manifest, "conflict_file_hash", by_name[_OBSERVATION_CONFLICT_FILE])
    _verify_declared_hash(manifest, "integrity_report_hash", by_name[_INTEGRITY_FILE])
    rows = _parse_jsonl(by_name[_OBSERVATION_FILE].data, _OBSERVATION_FILE)
    conflicts = _parse_jsonl(by_name[_OBSERVATION_CONFLICT_FILE].data, _OBSERVATION_CONFLICT_FILE)
    if int(manifest.get("observation_count", -1)) != len(rows):
        raise NBAPlayerPointsClosingBindingError("observation record count mismatch")
    if int(manifest.get("conflict_count", -1)) != len(conflicts):
        raise NBAPlayerPointsClosingBindingError("observation conflict count mismatch")
    for row in rows:
        if row.get("schema_version") != _OBSERVATION_SCHEMA:
            raise NBAPlayerPointsClosingBindingError("observation row schema mismatch")
        _verify_internal_hash(row, "closing_record_hash")
        if row.get("operating_date") != operating_date:
            raise NBAPlayerPointsClosingBindingError("observation row operating date mismatch")
    snapshot = ObservationBatchSnapshot(
        physical_observation_batch_id=batch_id,
        segment_locator=segment.relative_to(root).as_posix(),
        observation_jsonl_sha256=by_name[_OBSERVATION_FILE].sha256,
        observation_internal_manifest_hash=_require_sha256(
            manifest.get("closing_manifest_hash"),
            "closing_manifest_hash",
        ),
        observation_manifest_file_sha256=by_name[_OBSERVATION_MANIFEST_FILE].sha256,
        completion_marker_sha256=by_name[_COMPLETE_FILE].sha256,
        operating_date=operating_date,
        record_count=len(rows),
    )
    return snapshot, rows, files


def _load_selection_batch(
    root: Path,
    segment: Path,
    batch_id: str,
    operating_date: str,
) -> tuple[
    SelectionBatchSnapshot,
    tuple[Mapping[str, object], ...],
    tuple[FrozenClosingFile, ...],
    Mapping[str, object],
]:
    files = _freeze_declared_segment_files(
        root,
        segment,
        manifest_name=_SELECTION_MANIFEST_FILE,
        required_names=(
            _SELECTION_FILE,
            _INTEGRITY_FILE,
            _SELECTION_MANIFEST_FILE,
            _COMPLETE_FILE,
        ),
    )
    by_name = {item.path.name: item for item in files}
    manifest = _parse_json_object(by_name[_SELECTION_MANIFEST_FILE].data, _SELECTION_MANIFEST_FILE)
    marker = _parse_json_object(by_name[_COMPLETE_FILE].data, _COMPLETE_FILE)
    if manifest.get("schema_version") != _SELECTION_SCHEMA:
        raise NBAPlayerPointsClosingBindingError("selection manifest schema mismatch")
    if manifest.get("selection_batch_id") != batch_id:
        raise NBAPlayerPointsClosingBindingError("physical selection batch ID mismatch")
    if manifest.get("operating_date") != operating_date:
        raise NBAPlayerPointsClosingBindingError("selection operating date mismatch")
    _verify_internal_hash(manifest, "selection_manifest_hash")
    _verify_marker(marker, by_name[_SELECTION_MANIFEST_FILE])
    _verify_declared_hash(manifest, "selection_file_hash", by_name[_SELECTION_FILE])
    _verify_declared_hash(manifest, "integrity_report_hash", by_name[_INTEGRITY_FILE])
    rows = _parse_jsonl(by_name[_SELECTION_FILE].data, _SELECTION_FILE)
    if int(manifest.get("selection_count", -1)) != len(rows):
        raise NBAPlayerPointsClosingBindingError("selection record count mismatch")
    for row in rows:
        if row.get("schema_version") != _SELECTION_SCHEMA:
            raise NBAPlayerPointsClosingBindingError("selection row schema mismatch")
        _verify_internal_hash(row, "selection_record_hash")
    policy = _require_mapping(manifest.get("closing_policy"), "closing_policy")
    snapshot = SelectionBatchSnapshot(
        physical_selection_batch_id=batch_id,
        segment_locator=segment.relative_to(root).as_posix(),
        selection_jsonl_sha256=by_name[_SELECTION_FILE].sha256,
        selection_internal_manifest_hash=_require_sha256(
            manifest.get("selection_manifest_hash"),
            "selection_manifest_hash",
        ),
        selection_manifest_file_sha256=by_name[_SELECTION_MANIFEST_FILE].sha256,
        completion_marker_sha256=by_name[_COMPLETE_FILE].sha256,
        operating_date=operating_date,
        record_count=len(rows),
        effective_selection_count=sum(row.get("selection_status") == "selected" for row in rows),
    )
    return snapshot, rows, files, policy


def _load_prediction_record(
    root: Path,
    observation: Mapping[str, object],
) -> tuple[Mapping[str, object], FrozenClosingFile]:
    locator = _require_relative_locator(
        observation.get("prediction_evidence_segment"),
        "prediction_evidence_segment",
    )
    path = root.joinpath(*Path(locator).parts)
    _ensure_under_root(root, path)
    frozen = _freeze_file(root, path)
    rows = _parse_jsonl(frozen.data, locator)
    prediction_id = observation.get("prediction_id")
    matching = [row for row in rows if row.get("prediction_id") == prediction_id]
    if len(matching) != 1:
        raise NBAPlayerPointsClosingBindingError(
            "prediction ledger reference did not resolve exactly one row"
        )
    row = matching[0]
    if row.get("ledger_schema_version") != NBA_PLAYER_POINTS_LEDGER_SCHEMA_VERSION:
        raise NBAPlayerPointsClosingBindingError("prediction ledger schema mismatch")
    _verify_internal_hash(row, "ledger_record_hash")
    if row.get("ledger_record_hash") != observation.get("prediction_record_hash"):
        raise NBAPlayerPointsClosingBindingError("prediction ledger-record hash mismatch")
    return row, frozen


def _validate_selection_observation_lineage(
    selection: Mapping[str, object],
    observation: Mapping[str, object],
    *,
    closing_policy: Mapping[str, object],
    operating_date: str,
) -> None:
    if selection.get("selected_observation_hash") != observation.get("closing_record_hash"):
        raise NBAPlayerPointsClosingBindingError("selection-to-observation hash mismatch")
    for field_name in ("prediction_id", "prediction_run_id", "closing_line", "closing_american_odds"):
        if selection.get(field_name) != observation.get(field_name):
            raise NBAPlayerPointsClosingBindingError(f"{field_name} mismatch")
    for field_name in ("closing_policy_id", "closing_policy_version"):
        expected = closing_policy.get(field_name)
        if selection.get(field_name) != expected or observation.get(field_name) != expected:
            raise NBAPlayerPointsClosingBindingError(f"{field_name} mismatch")
    if observation.get("operating_date") != operating_date:
        raise NBAPlayerPointsClosingBindingError("wrong operating date")
    if observation.get("observation_eligibility_status") != "eligible":
        raise NBAPlayerPointsClosingBindingError("selected observation is not policy eligible")
    observed = _parse_utc(observation.get("observation_timestamp_utc"), "observation_timestamp_utc")
    tip = _parse_utc(observation.get("commence_time_utc"), "commence_time_utc")
    if observed >= tip:
        raise NBAPlayerPointsClosingBindingError("closing observation is at or after scheduled tip")
    for field_name in (
        "closing_window_start_seconds",
        "closing_window_end_seconds",
        "same_book_required",
        "same_market_required",
    ):
        if observation.get(field_name) != closing_policy.get(field_name):
            raise NBAPlayerPointsClosingBindingError(f"closing policy parameter mismatch: {field_name}")


def _validate_prediction_mapping(
    selection: Mapping[str, object],
    observation: Mapping[str, object],
    prediction: Mapping[str, object],
) -> None:
    pairs = (
        ("prediction_id", "prediction_id"),
        ("prediction_run_id", "prediction_run_id"),
        ("canonical_event_id", "canonical_event_id"),
        ("operating_date", "operating_date"),
        ("player_id", "player_id"),
        ("sportsbook", "sportsbook"),
        ("market", "market"),
        ("commence_time_utc", "commence_time_utc"),
    )
    for observation_field, prediction_field in pairs:
        if observation.get(observation_field) != prediction.get(prediction_field):
            raise NBAPlayerPointsClosingBindingError(
                f"wrong {observation_field.replace('_', ' ')} in closing mapping"
            )
    if selection.get("prediction_id") != prediction.get("prediction_id"):
        raise NBAPlayerPointsClosingBindingError("selection prediction mismatch")


def _freeze_declared_segment_files(
    root: Path,
    segment: Path,
    *,
    manifest_name: str,
    required_names: Sequence[str],
) -> tuple[FrozenClosingFile, ...]:
    _ensure_under_root(root, segment)
    _reject_reparse_components(segment)
    if not segment.is_dir():
        raise NBAPlayerPointsClosingBindingError(
            f"explicit physical closing batch is missing: {segment.name}"
        )
    files = tuple(_freeze_file(root, segment / name) for name in required_names)
    if manifest_name not in {item.path.name for item in files}:
        raise NBAPlayerPointsClosingBindingError("closing manifest snapshot is missing")
    return files


def _freeze_file(root: Path, path: Path) -> FrozenClosingFile:
    _ensure_under_root(root, path)
    _reject_reparse_components(path)
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            data = handle.read()
            after = os.fstat(handle.fileno())
        current = path.stat()
    except OSError as exc:
        raise NBAPlayerPointsClosingBindingError(
            f"unable to freeze exact closing evidence file: {path.name}"
        ) from exc
    before_identity = _filesystem_identity(before)
    if before_identity != _filesystem_identity(after) or before_identity != _filesystem_identity(current):
        raise NBAPlayerPointsClosingBindingError(
            f"closing evidence path was replaced during snapshot: {path.name}"
        )
    if len(data) != before.st_size:
        raise NBAPlayerPointsClosingBindingError(
            f"closing evidence size changed during snapshot: {path.name}"
        )
    return FrozenClosingFile(
        path=path,
        locator=path.relative_to(root).as_posix(),
        data=data,
        sha256=_sha256_bytes(data),
        size_bytes=len(data),
        filesystem_identity=before_identity,
    )


def _filesystem_identity(stat_result: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(stat_result.st_dev),
        int(stat_result.st_ino),
        int(stat_result.st_size),
        int(stat_result.st_mtime_ns),
    )


def _reject_reparse_components(path: Path) -> None:
    probes: list[Path] = []
    current = path.absolute()
    while True:
        probes.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for probe in reversed(probes):
        if not probe.exists() and not probe.is_symlink():
            continue
        try:
            if probe.is_symlink() or _has_windows_reparse_attribute(probe):
                raise NBAPlayerPointsClosingBindingError(
                    f"closing evidence path contains a symlink or reparse point: {probe}"
                )
        except OSError as exc:
            raise NBAPlayerPointsClosingBindingError(
                f"unable to inspect closing evidence path: {probe}"
            ) from exc


def _has_windows_reparse_attribute(path: Path) -> bool:
    if os.name != "nt":
        return False
    import ctypes

    attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    if attributes == _WINDOWS_INVALID_ATTRIBUTES:
        raise OSError(f"GetFileAttributesW failed for {path}")
    return bool(attributes & _WINDOWS_REPARSE_POINT)


def _verify_marker(marker: Mapping[str, object], manifest: FrozenClosingFile) -> None:
    if marker.get("completion_status") not in {"complete", "conflicting"}:
        raise NBAPlayerPointsClosingBindingError("closing completion marker is incomplete")
    if marker.get("manifest_hash") != manifest.sha256:
        raise NBAPlayerPointsClosingBindingError("closing completion-marker hash mismatch")


def _verify_declared_hash(
    manifest: Mapping[str, object],
    field_name: str,
    frozen: FrozenClosingFile,
) -> None:
    if _require_sha256(manifest.get(field_name), field_name) != frozen.sha256:
        raise NBAPlayerPointsClosingBindingError(f"{field_name} mismatch")


def _verify_internal_hash(payload: Mapping[str, object], field_name: str) -> None:
    supplied = _require_sha256(payload.get(field_name), field_name)
    expected = canonical_sha256({key: value for key, value in payload.items() if key != field_name})
    if supplied != expected:
        raise NBAPlayerPointsClosingBindingError(f"{field_name} mismatch")


def _parse_json_object(data: bytes, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NBAPlayerPointsClosingBindingError(f"invalid UTF-8 JSON: {label}") from exc
    return _require_mapping(payload, label)


def _parse_jsonl(data: bytes, label: str) -> tuple[Mapping[str, object], ...]:
    if not data:
        return ()
    if not data.endswith(b"\n"):
        raise NBAPlayerPointsClosingBindingError(f"JSONL frame missing final newline: {label}")
    rows: list[Mapping[str, object]] = []
    for line_number, raw in enumerate(data.splitlines(), start=1):
        if not raw:
            raise NBAPlayerPointsClosingBindingError(f"empty JSONL line: {label}:{line_number}")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NBAPlayerPointsClosingBindingError(
                f"invalid JSONL: {label}:{line_number}"
            ) from exc
        rows.append(_require_mapping(payload, f"{label}:{line_number}"))
    return tuple(rows)


def _actual_evidence_root(path: Path) -> Path:
    base = path.expanduser().absolute()
    return base if base.name == NBA_PLAYER_POINTS_EVIDENCE_DIR_NAME else base / NBA_PLAYER_POINTS_EVIDENCE_DIR_NAME


def _ensure_under_root(root: Path, path: Path) -> None:
    root_absolute = root.absolute()
    path_absolute = path.absolute()
    try:
        path_absolute.relative_to(root_absolute)
    except ValueError as exc:
        raise NBAPlayerPointsClosingBindingError("closing evidence path traversal detected") from exc


def _require_relative_locator(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).replace("\\", "/")
    candidate = Path(text)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise NBAPlayerPointsClosingBindingError(f"{field_name} must be a safe root-relative locator")
    return candidate.as_posix()


def _require_safe_component(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if _SAFE_COMPONENT_RE.fullmatch(text) is None or text in {".", ".."} or ".." in text:
        raise NBAPlayerPointsClosingBindingError(f"{field_name} must be a safe path component")
    return text


def _require_identifier(value: object, field_name: str) -> str:
    return _require_text(value, field_name)


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NBAPlayerPointsClosingBindingError(f"{field_name} is required")
    return value.strip()


def _require_sha256(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if _SHA256_RE.fullmatch(text) is None:
        raise NBAPlayerPointsClosingBindingError(f"{field_name} must be lowercase SHA-256")
    return text


def _require_operating_date(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if _DATE_RE.fullmatch(text) is None:
        raise NBAPlayerPointsClosingBindingError(f"{field_name} must be YYYY-MM-DD")
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise NBAPlayerPointsClosingBindingError(f"{field_name} must be a valid date") from exc
    return text


def _require_utc_timestamp(value: object, field_name: str) -> str:
    return _parse_utc(value, field_name).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, field_name: str) -> datetime:
    text = _require_text(value, field_name)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise NBAPlayerPointsClosingBindingError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise NBAPlayerPointsClosingBindingError(f"{field_name} must be timezone-aware UTC")
    return parsed.astimezone(_UTC)


def _require_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise NBAPlayerPointsClosingBindingError(f"{field_name} must be an object")
    return MappingProxyType(_json_clone(value))


def _require_sequence(value: object, field_name: str) -> tuple[object, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = tuple(value)
        if items:
            return items
    raise NBAPlayerPointsClosingBindingError(f"{field_name} must be a non-empty list")


def _json_ready(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(_UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, float) and not math.isfinite(value):
        raise NBAPlayerPointsClosingBindingError("numeric values must be finite")
    if hasattr(value, "to_dict"):
        return _json_ready(value.to_dict())
    return value


def _json_clone(value: Mapping[str, object]) -> dict[str, object]:
    cloned = json.loads(json.dumps(_json_ready(value), sort_keys=True, allow_nan=False))
    if not isinstance(cloned, dict):
        raise NBAPlayerPointsClosingBindingError("value must be an object")
    return cloned


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsSettlementEvidenceV2WriteResult:
    completion_status: str
    evidence_root: Path
    settlement_segment_directory: Path
    logical_settlement_batch_id: str
    physical_settlement_batch_id: str
    settlement_manifest: Mapping[str, object]
    writer_verifier_result: Mapping[str, object]
    settlement_rows_written: int

    def to_dict(self) -> dict[str, object]:
        return {
            "completion_status": self.completion_status,
            "evidence_root": str(self.evidence_root),
            "settlement_segment_directory": str(self.settlement_segment_directory),
            "logical_settlement_batch_id": self.logical_settlement_batch_id,
            "physical_settlement_batch_id": self.physical_settlement_batch_id,
            "settlement_manifest": _json_clone(self.settlement_manifest),
            "writer_verifier_result": _json_clone(self.writer_verifier_result),
            "settlement_rows_written": self.settlement_rows_written,
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsSettlementEvidenceV2IntegrityReport:
    ok: bool
    violations: tuple[str, ...]
    evidence_root: Path
    settlement_segments: tuple[Mapping[str, object], ...]
    binding_status_counts: Mapping[str, int]
    schema_versions: tuple[str, ...]
    approval_contract_versions: tuple[str, ...]
    closing_prerequisite_hashes: tuple[str, ...]
    logical_settlement_batch_ids: tuple[str, ...]
    physical_settlement_batch_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "violations": list(self.violations),
            "evidence_root": str(self.evidence_root),
            "settlement_segments": [_json_clone(item) for item in self.settlement_segments],
            "binding_status_counts": dict(self.binding_status_counts),
            "schema_versions": list(self.schema_versions),
            "approval_contract_versions": list(self.approval_contract_versions),
            "closing_prerequisite_hashes": list(self.closing_prerequisite_hashes),
            "logical_settlement_batch_ids": list(self.logical_settlement_batch_ids),
            "physical_settlement_batch_ids": list(self.physical_settlement_batch_ids),
        }


def build_nba_player_points_settlement_approval_envelope(
    *,
    approval_digest: str,
    operator_id: str,
    approval_timestamp_utc: str,
    bundle_sha256: str,
    repository_commit_sha: str,
    logical_settlement_batch_id: str,
    closing_prerequisite_sha256: str,
    prediction_ids: Sequence[str],
) -> Mapping[str, object]:
    """Build the immutable, non-self-referential in-segment approval envelope."""

    predictions = tuple(sorted(_require_identifier(item, "prediction_ids[]") for item in prediction_ids))
    if not predictions or len(set(predictions)) != len(predictions):
        raise NBAPlayerPointsClosingBindingError("approval prediction IDs must be a unique non-empty set")
    return MappingProxyType(
        {
            "approval_contract_version": NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_CONTRACT_VERSION,
            "approval_digest": _require_sha256(approval_digest, "approval_digest"),
            "operator_id": _require_safe_component(operator_id, "operator_id"),
            "approval_timestamp_utc": _require_utc_timestamp(
                approval_timestamp_utc,
                "approval_timestamp_utc",
            ),
            "bundle_sha256": _require_sha256(bundle_sha256, "bundle_sha256"),
            "repository_commit_sha": _require_commit_sha(
                repository_commit_sha,
                "repository_commit_sha",
            ),
            "logical_settlement_batch_id": _require_safe_component(
                logical_settlement_batch_id,
                "logical_settlement_batch_id",
            ),
            "closing_prerequisite_sha256": _require_sha256(
                closing_prerequisite_sha256,
                "closing_prerequisite_sha256",
            ),
            "prediction_ids": list(predictions),
            "publication_stage_identity": {
                "bundle_schema_version": NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION,
                "plan_schema_version": NBA_PLAYER_POINTS_MANUAL_PLAN_V2_SCHEMA_VERSION,
                "operation_stage": "settlement-publish",
                "evidence_schema_version": NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION,
            },
        }
    )


def preview_nba_player_points_settlement_evidence_v2_records(
    settlement_rows: Sequence[NBAPlayerPointsSettlementRow],
    closing_prerequisite: ClosingPrerequisite,
) -> tuple[Mapping[str, object], ...]:
    """Wrap unchanged v1 settlement rows in deterministic v2 evidence records."""

    try:
        validated_rows = validate_settlement_rows(tuple(settlement_rows))
    except Exception as exc:
        raise NBAPlayerPointsClosingBindingError(str(exc)) from exc
    mappings = {
        mapping.prediction_id: mapping
        for mapping in closing_prerequisite.prediction_mappings
    }
    row_ids = [str(row.prediction_id) for row in validated_rows]
    if sorted(row_ids) != sorted(mappings):
        raise NBAPlayerPointsClosingBindingError(
            "settlement row prediction set does not exactly match closing mapping set"
        )
    records: list[Mapping[str, object]] = []
    for row in sorted(validated_rows, key=lambda item: str(item.prediction_id)):
        v1_row = row.to_dict()
        v1_row_hash = _require_sha256(
            v1_row.get("settlement_record_hash"),
            "settlement_record_hash",
        )
        expected_v1_hash = canonical_sha256(
            {key: value for key, value in v1_row.items() if key != "settlement_record_hash"}
        )
        if v1_row_hash != expected_v1_hash:
            raise NBAPlayerPointsClosingBindingError("v1 settlement row hash mismatch")
        mapping = mappings[str(row.prediction_id)]
        record: dict[str, object] = {
            "evidence_schema_version": NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION,
            "binding_status": "closing-bound",
            "prediction_id": str(row.prediction_id),
            "v1_settlement_row": v1_row,
            "v1_settlement_row_hash": v1_row_hash,
            "closing_prerequisite_sha256": closing_prerequisite.closing_prerequisite_sha256,
            "closing_binding": _closing_binding(
                mapping,
                closing_prerequisite.closing_policy,
            ),
        }
        record["v2_evidence_record_hash"] = canonical_sha256(record)
        records.append(MappingProxyType(record))
    return tuple(records)


def write_nba_player_points_settlement_evidence_v2(
    evidence_root: str | Path,
    settlement_rows: Sequence[NBAPlayerPointsSettlementRow],
    closing_snapshot: ClosingEvidenceSnapshot,
    approval_envelope: Mapping[str, object],
    *,
    logical_settlement_batch_id: str,
    settlement_policy: Mapping[str, object],
    collection_timestamp_utc: str,
    repository_commit_sha: str,
    writer_timestamp_utc: str,
    failure_hook: FailureHook | None = None,
) -> NBAPlayerPointsSettlementEvidenceV2WriteResult:
    """Atomically publish closing-bound v2 settlement evidence."""

    logical_id = _require_safe_component(
        logical_settlement_batch_id,
        "logical_settlement_batch_id",
    )
    collection_time = _require_utc_timestamp(
        collection_timestamp_utc,
        "collection_timestamp_utc",
    )
    writer_time = _require_utc_timestamp(writer_timestamp_utc, "writer_timestamp_utc")
    commit_sha = _require_commit_sha(repository_commit_sha, "repository_commit_sha")
    policy = _require_mapping(settlement_policy, "settlement_policy")
    prerequisite = closing_snapshot.prerequisite
    envelope = _require_mapping(approval_envelope, "approval_envelope")
    _validate_approval_envelope(
        envelope,
        logical_settlement_batch_id=logical_id,
        closing_prerequisite=prerequisite,
        repository_commit_sha=commit_sha,
    )
    records = preview_nba_player_points_settlement_evidence_v2_records(
        settlement_rows,
        prerequisite,
    )
    closing_snapshot.assert_unchanged()
    root = _actual_evidence_root(Path(evidence_root))
    if root != closing_snapshot.evidence_root:
        raise NBAPlayerPointsClosingBindingError(
            "closing snapshot evidence root does not match settlement evidence root"
        )

    row_bytes = _jsonl_bytes(records)
    prerequisite_bytes = canonical_json_bytes(prerequisite.to_dict()) + b"\n"
    envelope_bytes = canonical_json_bytes(envelope) + b"\n"
    integrity_payload = {
        "status": "complete",
        "violations": [],
        "logical_settlement_batch_id": logical_id,
        "settlement_rows_sha256": _sha256_bytes(row_bytes),
        "closing_prerequisite_sha256": prerequisite.closing_prerequisite_sha256,
        "approval_envelope_sha256": _sha256_bytes(envelope_bytes),
        "settlement_count": len(records),
    }
    integrity_bytes = canonical_json_bytes(integrity_payload) + b"\n"
    immutable_files = {
        _SETTLEMENT_ROWS_FILE: row_bytes,
        _CLOSING_PREREQUISITE_FILE: prerequisite_bytes,
        _APPROVAL_ENVELOPE_FILE: envelope_bytes,
        _INTEGRITY_FILE: integrity_bytes,
    }
    file_inventory = {
        name: {"sha256": _sha256_bytes(data), "size_bytes": len(data)}
        for name, data in sorted(immutable_files.items())
    }
    physical_id = "nba-settlement-v2-" + canonical_sha256(
        {
            "schema_version": NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION,
            "logical_settlement_batch_id": logical_id,
            "operating_date": prerequisite.operating_date,
            "closing_prerequisite_sha256": prerequisite.closing_prerequisite_sha256,
            "settlement_policy": policy,
            "file_inventory": file_inventory,
        }
    )[:32]
    segment = (
        root
        / "settlement"
        / "segments"
        / prerequisite.operating_date
        / physical_id
    )
    _ensure_under_root(root, segment)
    _call_failure_hook(failure_hook, "before_any_write")
    with _SettlementV2RootLock(root):
        closing_snapshot.assert_unchanged()
        existing_same_logical = _existing_v2_logical_batches(root, logical_id)
        if existing_same_logical and physical_id not in existing_same_logical:
            raise NBAPlayerPointsClosingBindingError(
                "conflicting closing-bound settlement publication for logical batch ID"
            )
        if segment.exists():
            report = verify_nba_player_points_settlement_evidence_v2(root, segment=segment)
            if not report.ok:
                raise NBAPlayerPointsClosingBindingError(
                    "existing closing-bound settlement failed verification: "
                    + "; ".join(report.violations)
                )
            manifest = _read_path_json(segment / _SETTLEMENT_MANIFEST_FILE)
            return NBAPlayerPointsSettlementEvidenceV2WriteResult(
                completion_status="already_complete",
                evidence_root=root,
                settlement_segment_directory=segment,
                logical_settlement_batch_id=logical_id,
                physical_settlement_batch_id=physical_id,
                settlement_manifest=manifest,
                writer_verifier_result=MappingProxyType(report.to_dict()),
                settlement_rows_written=0,
            )
        parent = segment.parent
        _make_safe_directory(root, parent)
        stage = parent / f".settlement-v2-{uuid4().hex[:12]}"
        try:
            stage.mkdir()
            _call_failure_hook(failure_hook, "after_v2_temp_dir_created")
            write_stages = (
                (_SETTLEMENT_ROWS_FILE, "settlement_rows"),
                (_CLOSING_PREREQUISITE_FILE, "closing_prerequisite"),
                (_APPROVAL_ENVELOPE_FILE, "approval_envelope"),
                (_INTEGRITY_FILE, "integrity_report"),
            )
            for file_name, stage_name in write_stages:
                _call_failure_hook(failure_hook, f"before_{stage_name}_write")
                _write_verified(stage / file_name, immutable_files[file_name])
                _call_failure_hook(failure_hook, f"after_{stage_name}_write")
            manifest = _v2_manifest(
                physical_settlement_batch_id=physical_id,
                logical_settlement_batch_id=logical_id,
                prerequisite=prerequisite,
                approval_envelope=envelope,
                settlement_policy=policy,
                collection_timestamp_utc=collection_time,
                writer_timestamp_utc=writer_time,
                repository_commit_sha=commit_sha,
                file_inventory=file_inventory,
                settlement_count=len(records),
            )
            _call_failure_hook(failure_hook, "before_manifest_write")
            manifest_bytes = canonical_json_bytes(manifest) + b"\n"
            _write_verified(stage / _SETTLEMENT_MANIFEST_FILE, manifest_bytes)
            _call_failure_hook(failure_hook, "after_manifest_write")
            marker = {
                "completion_status": "complete",
                "manifest_file": _SETTLEMENT_MANIFEST_FILE,
                "manifest_hash": _sha256_bytes(manifest_bytes),
                "physical_settlement_batch_id": physical_id,
            }
            _call_failure_hook(failure_hook, "before_complete_write")
            _write_verified(stage / _COMPLETE_FILE, canonical_json_bytes(marker) + b"\n")
            _call_failure_hook(failure_hook, "after_complete_write")
            closing_snapshot.assert_unchanged()
            _call_failure_hook(failure_hook, "before_atomic_rename")
            stage.rename(segment)
        except Exception:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
            raise

        report = verify_nba_player_points_settlement_evidence_v2(root, segment=segment)
        if not report.ok:
            raise NBAPlayerPointsClosingBindingError(
                "closing-bound settlement writer verification failed: "
                + "; ".join(report.violations)
            )
        return NBAPlayerPointsSettlementEvidenceV2WriteResult(
            completion_status="complete",
            evidence_root=root,
            settlement_segment_directory=segment,
            logical_settlement_batch_id=logical_id,
            physical_settlement_batch_id=physical_id,
            settlement_manifest=manifest,
            writer_verifier_result=MappingProxyType(report.to_dict()),
            settlement_rows_written=len(records),
        )


def verify_nba_player_points_settlement_evidence_v2(
    evidence_root: str | Path,
    *,
    segment: Path | None = None,
) -> NBAPlayerPointsSettlementEvidenceV2IntegrityReport:
    """Verify closing-bound v2 segments without enumerating closing batches."""

    root = _actual_evidence_root(Path(evidence_root))
    segments = (segment,) if segment is not None else _iter_v2_segments(root)
    reports: list[Mapping[str, object]] = []
    violations: list[str] = []
    schemas: set[str] = set()
    approvals: set[str] = set()
    prerequisites: set[str] = set()
    logical_ids: set[str] = set()
    physical_ids: set[str] = set()
    counts = {"closing-bound": 0, "invalid": 0}
    for candidate in segments:
        report = _verify_v2_segment(root, candidate)
        reports.append(report)
        report_violations = tuple(str(item) for item in report.get("violations", ()))
        if report_violations:
            counts["invalid"] += 1
            violations.extend(report_violations)
        else:
            counts["closing-bound"] += 1
        manifest = report.get("manifest")
        if isinstance(manifest, Mapping):
            if isinstance(manifest.get("schema_version"), str):
                schemas.add(str(manifest["schema_version"]))
            if isinstance(manifest.get("approval_contract_version"), str):
                approvals.add(str(manifest["approval_contract_version"]))
            if isinstance(manifest.get("closing_prerequisite_sha256"), str):
                prerequisites.add(str(manifest["closing_prerequisite_sha256"]))
            if isinstance(manifest.get("logical_settlement_batch_id"), str):
                logical_ids.add(str(manifest["logical_settlement_batch_id"]))
            if isinstance(manifest.get("physical_settlement_batch_id"), str):
                physical_ids.add(str(manifest["physical_settlement_batch_id"]))
    return NBAPlayerPointsSettlementEvidenceV2IntegrityReport(
        ok=not violations,
        violations=tuple(violations),
        evidence_root=root,
        settlement_segments=tuple(reports),
        binding_status_counts=MappingProxyType(counts),
        schema_versions=tuple(sorted(schemas)),
        approval_contract_versions=tuple(sorted(approvals)),
        closing_prerequisite_hashes=tuple(sorted(prerequisites)),
        logical_settlement_batch_ids=tuple(sorted(logical_ids)),
        physical_settlement_batch_ids=tuple(sorted(physical_ids)),
    )


def _verify_v2_segment(root: Path, segment: Path) -> Mapping[str, object]:
    violations: list[str] = []
    manifest: Mapping[str, object] = MappingProxyType({})
    binding_status = "invalid"
    try:
        files = _freeze_declared_segment_files(
            root,
            segment,
            manifest_name=_SETTLEMENT_MANIFEST_FILE,
            required_names=(
                _SETTLEMENT_ROWS_FILE,
                _CLOSING_PREREQUISITE_FILE,
                _APPROVAL_ENVELOPE_FILE,
                _INTEGRITY_FILE,
                _SETTLEMENT_MANIFEST_FILE,
                _COMPLETE_FILE,
            ),
        )
        by_name = {item.path.name: item for item in files}
        manifest = _parse_json_object(
            by_name[_SETTLEMENT_MANIFEST_FILE].data,
            _SETTLEMENT_MANIFEST_FILE,
        )
        marker = _parse_json_object(by_name[_COMPLETE_FILE].data, _COMPLETE_FILE)
        if manifest.get("schema_version") != NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION:
            raise NBAPlayerPointsClosingBindingError("settlement v2 manifest schema mismatch")
        _verify_internal_hash(manifest, "settlement_manifest_hash")
        _verify_marker(marker, by_name[_SETTLEMENT_MANIFEST_FILE])
        physical_id = _require_safe_component(
            manifest.get("physical_settlement_batch_id"),
            "physical_settlement_batch_id",
        )
        if segment.name != physical_id or marker.get("physical_settlement_batch_id") != physical_id:
            raise NBAPlayerPointsClosingBindingError("physical settlement batch ID mismatch")
        inventory = _require_mapping(manifest.get("file_inventory"), "file_inventory")
        expected_names = {
            _SETTLEMENT_ROWS_FILE,
            _CLOSING_PREREQUISITE_FILE,
            _APPROVAL_ENVELOPE_FILE,
            _INTEGRITY_FILE,
        }
        if set(inventory) != expected_names:
            raise NBAPlayerPointsClosingBindingError("settlement v2 file inventory mismatch")
        for name in sorted(expected_names):
            item = _require_mapping(inventory[name], f"file_inventory.{name}")
            if item.get("sha256") != by_name[name].sha256 or item.get("size_bytes") != by_name[name].size_bytes:
                raise NBAPlayerPointsClosingBindingError(f"settlement v2 file hash mismatch: {name}")
        prerequisite_payload = _parse_json_object(
            by_name[_CLOSING_PREREQUISITE_FILE].data,
            _CLOSING_PREREQUISITE_FILE,
        )
        prerequisite_result = validate_nba_player_points_closing_prerequisite(
            root,
            prerequisite_payload,
        )
        if not prerequisite_result.ok or prerequisite_result.prerequisite is None:
            raise NBAPlayerPointsClosingBindingError(
                "closing prerequisite verification failed: "
                + "; ".join(prerequisite_result.violations)
            )
        prerequisite = prerequisite_result.prerequisite
        envelope = _parse_json_object(
            by_name[_APPROVAL_ENVELOPE_FILE].data,
            _APPROVAL_ENVELOPE_FILE,
        )
        _validate_approval_envelope(
            envelope,
            logical_settlement_batch_id=_require_safe_component(
                manifest.get("logical_settlement_batch_id"),
                "logical_settlement_batch_id",
            ),
            closing_prerequisite=prerequisite,
            repository_commit_sha=_require_commit_sha(
                manifest.get("repository_commit_sha"),
                "repository_commit_sha",
            ),
        )
        if manifest.get("approval_contract_version") != envelope.get("approval_contract_version"):
            raise NBAPlayerPointsClosingBindingError("approval contract version mismatch")
        if manifest.get("approval_digest") != envelope.get("approval_digest"):
            raise NBAPlayerPointsClosingBindingError("approval digest mismatch")
        if manifest.get("approval_envelope_sha256") != by_name[_APPROVAL_ENVELOPE_FILE].sha256:
            raise NBAPlayerPointsClosingBindingError("approval envelope file hash mismatch")
        if manifest.get("closing_prerequisite_sha256") != prerequisite.closing_prerequisite_sha256:
            raise NBAPlayerPointsClosingBindingError("manifest closing prerequisite mismatch")
        if manifest.get("mapping_aggregate_sha256") != prerequisite.mapping_aggregate_sha256:
            raise NBAPlayerPointsClosingBindingError("manifest mapping aggregate mismatch")
        rows = _parse_jsonl(by_name[_SETTLEMENT_ROWS_FILE].data, _SETTLEMENT_ROWS_FILE)
        if manifest.get("settlement_count") != len(rows):
            raise NBAPlayerPointsClosingBindingError("settlement v2 row count mismatch")
        mappings = {item.prediction_id: item for item in prerequisite.prediction_mappings}
        row_ids: list[str] = []
        for row in rows:
            prediction_id = _require_identifier(row.get("prediction_id"), "prediction_id")
            row_ids.append(prediction_id)
            _verify_v2_record(row, mappings.get(prediction_id), prerequisite)
        if sorted(row_ids) != sorted(mappings) or sorted(row_ids) != sorted(envelope.get("prediction_ids", [])):
            raise NBAPlayerPointsClosingBindingError("v2 settlement prediction set mismatch")
        manifest_observations = manifest.get("observation_batches")
        if manifest_observations != [item.to_dict() for item in prerequisite.observation_batches]:
            raise NBAPlayerPointsClosingBindingError("manifest observation batch identities mismatch")
        if manifest.get("selection_batch") != prerequisite.selection_batch.to_dict():
            raise NBAPlayerPointsClosingBindingError("manifest selection batch identity mismatch")
        binding_status = "closing-bound"
    except (NBAPlayerPointsClosingBindingError, OSError, ValueError, TypeError) as exc:
        violations.append(f"{segment}: {exc}")
    return MappingProxyType(
        {
            "segment_directory": str(segment),
            "manifest": _json_clone(manifest),
            "binding_status": binding_status,
            "violations": tuple(violations),
        }
    )


def _verify_v2_record(
    record: Mapping[str, object],
    mapping: PredictionClosingMapping | None,
    prerequisite: ClosingPrerequisite,
) -> None:
    if mapping is None:
        raise NBAPlayerPointsClosingBindingError("unknown prediction in v2 settlement record")
    if record.get("evidence_schema_version") != NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION:
        raise NBAPlayerPointsClosingBindingError("v2 settlement record schema mismatch")
    if record.get("binding_status") != "closing-bound":
        raise NBAPlayerPointsClosingBindingError("v2 settlement binding status mismatch")
    if record.get("closing_prerequisite_sha256") != prerequisite.closing_prerequisite_sha256:
        raise NBAPlayerPointsClosingBindingError("v2 settlement prerequisite hash mismatch")
    v1_row = _require_mapping(record.get("v1_settlement_row"), "v1_settlement_row")
    if v1_row.get("settlement_schema_version") != NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION:
        raise NBAPlayerPointsClosingBindingError("v1 settlement contract schema mismatch")
    expected_v1_hash = canonical_sha256(
        {key: value for key, value in v1_row.items() if key != "settlement_record_hash"}
    )
    if record.get("v1_settlement_row_hash") != expected_v1_hash or v1_row.get("settlement_record_hash") != expected_v1_hash:
        raise NBAPlayerPointsClosingBindingError("v1 settlement row hash mismatch")
    if v1_row.get("prediction_id") != mapping.prediction_id:
        raise NBAPlayerPointsClosingBindingError("v1 settlement prediction mapping mismatch")
    if record.get("closing_binding") != _closing_binding(
        mapping,
        prerequisite.closing_policy,
    ):
        raise NBAPlayerPointsClosingBindingError("per-prediction closing binding mismatch")
    expected_record_hash = canonical_sha256(
        {key: value for key, value in record.items() if key != "v2_evidence_record_hash"}
    )
    if record.get("v2_evidence_record_hash") != expected_record_hash:
        raise NBAPlayerPointsClosingBindingError("v2 evidence-record hash mismatch")


def _closing_binding(
    mapping: PredictionClosingMapping,
    closing_policy: Mapping[str, object],
) -> dict[str, object]:
    return {
        "physical_observation_batch_id": mapping.source_observation_batch_id,
        "physical_selection_batch_id": mapping.physical_selection_batch_id,
        "observation_id": mapping.source_observation_id,
        "observation_record_hash": mapping.source_observation_record_hash,
        "selection_id": mapping.closing_selection_id,
        "selection_record_hash": mapping.selection_record_hash,
        "closing_policy_id": closing_policy.get("closing_policy_id"),
        "closing_policy_version": closing_policy.get("closing_policy_version"),
        "player_id": mapping.player_id,
        "canonical_event_id": mapping.canonical_event_id,
        "sportsbook": mapping.sportsbook,
        "market": mapping.market,
        "operating_date": mapping.operating_date,
        "observation_timestamp_utc": mapping.observation_timestamp_utc,
        "closing_line": mapping.closing_line,
        "closing_american_odds": mapping.closing_american_odds,
    }


def _v2_manifest(
    *,
    physical_settlement_batch_id: str,
    logical_settlement_batch_id: str,
    prerequisite: ClosingPrerequisite,
    approval_envelope: Mapping[str, object],
    settlement_policy: Mapping[str, object],
    collection_timestamp_utc: str,
    writer_timestamp_utc: str,
    repository_commit_sha: str,
    file_inventory: Mapping[str, object],
    settlement_count: int,
) -> Mapping[str, object]:
    manifest: dict[str, object] = {
        "schema_version": NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION,
        "settlement_contract_schema_version": NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION,
        "approval_contract_version": NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_CONTRACT_VERSION,
        "approval_digest": approval_envelope["approval_digest"],
        "binding_status": "closing-bound",
        "physical_settlement_batch_id": physical_settlement_batch_id,
        "logical_settlement_batch_id": logical_settlement_batch_id,
        "operating_date": prerequisite.operating_date,
        "closing_prerequisite_sha256": prerequisite.closing_prerequisite_sha256,
        "observation_batches": [item.to_dict() for item in prerequisite.observation_batches],
        "selection_batch": prerequisite.selection_batch.to_dict(),
        "prediction_mapping_count": len(prerequisite.prediction_mappings),
        "mapping_aggregate_sha256": prerequisite.mapping_aggregate_sha256,
        "settlement_policy": _json_clone(settlement_policy),
        "settlement_count": settlement_count,
        "collection_timestamp_utc": collection_timestamp_utc,
        "writer_timestamp_utc": writer_timestamp_utc,
        "repository_commit_sha": repository_commit_sha,
        "settlement_rows_sha256": file_inventory[_SETTLEMENT_ROWS_FILE]["sha256"],
        "approval_envelope_sha256": file_inventory[_APPROVAL_ENVELOPE_FILE]["sha256"],
        "closing_prerequisite_file_sha256": file_inventory[_CLOSING_PREREQUISITE_FILE]["sha256"],
        "file_inventory": _json_clone(file_inventory),
        "completion_status": "complete",
    }
    manifest["settlement_manifest_hash"] = canonical_sha256(manifest)
    return MappingProxyType(manifest)


def _validate_approval_envelope(
    envelope: Mapping[str, object],
    *,
    logical_settlement_batch_id: str,
    closing_prerequisite: ClosingPrerequisite,
    repository_commit_sha: str,
) -> None:
    required = {
        "approval_contract_version",
        "approval_digest",
        "operator_id",
        "approval_timestamp_utc",
        "bundle_sha256",
        "repository_commit_sha",
        "logical_settlement_batch_id",
        "closing_prerequisite_sha256",
        "prediction_ids",
        "publication_stage_identity",
    }
    if set(envelope) != required:
        raise NBAPlayerPointsClosingBindingError("approval envelope fields mismatch")
    if envelope.get("approval_contract_version") != NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_CONTRACT_VERSION:
        raise NBAPlayerPointsClosingBindingError("approval contract version mismatch")
    _require_sha256(envelope.get("approval_digest"), "approval_digest")
    _require_sha256(envelope.get("bundle_sha256"), "bundle_sha256")
    _require_safe_component(envelope.get("operator_id"), "operator_id")
    _require_utc_timestamp(envelope.get("approval_timestamp_utc"), "approval_timestamp_utc")
    if envelope.get("repository_commit_sha") != repository_commit_sha:
        raise NBAPlayerPointsClosingBindingError("approval envelope repository commit mismatch")
    if envelope.get("logical_settlement_batch_id") != logical_settlement_batch_id:
        raise NBAPlayerPointsClosingBindingError("approval envelope logical batch mismatch")
    if envelope.get("closing_prerequisite_sha256") != closing_prerequisite.closing_prerequisite_sha256:
        raise NBAPlayerPointsClosingBindingError("approval envelope prerequisite mismatch")
    prediction_ids = _require_sequence(envelope.get("prediction_ids"), "prediction_ids")
    if sorted(str(item) for item in prediction_ids) != sorted(
        item.prediction_id for item in closing_prerequisite.prediction_mappings
    ):
        raise NBAPlayerPointsClosingBindingError("approval envelope prediction set mismatch")
    stage = _require_mapping(envelope.get("publication_stage_identity"), "publication_stage_identity")
    expected_stage = {
        "bundle_schema_version": NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION,
        "plan_schema_version": NBA_PLAYER_POINTS_MANUAL_PLAN_V2_SCHEMA_VERSION,
        "operation_stage": "settlement-publish",
        "evidence_schema_version": NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION,
    }
    if stage != expected_stage:
        raise NBAPlayerPointsClosingBindingError("approval envelope publication stage mismatch")
    prohibited = {
        "physical_settlement_batch_id",
        "settlement_manifest_file_sha256",
        "manifest_file_sha256",
        "approval_envelope_sha256",
        "self_hash",
    }
    if prohibited.intersection(envelope):
        raise NBAPlayerPointsClosingBindingError("approval envelope contains circular identity fields")


def _iter_v2_segments(root: Path) -> tuple[Path, ...]:
    segments_root = root / "settlement" / "segments"
    if not segments_root.exists():
        return ()
    result: list[Path] = []
    for candidate in sorted(segments_root.glob("*/*")):
        if candidate.name.startswith(".") or not (candidate / _COMPLETE_FILE).exists():
            continue
        try:
            manifest = _read_path_json(candidate / _SETTLEMENT_MANIFEST_FILE)
        except NBAPlayerPointsClosingBindingError:
            result.append(candidate)
            continue
        if manifest.get("schema_version") == NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION:
            result.append(candidate)
    return tuple(result)


def _existing_v2_logical_batches(root: Path, logical_id: str) -> set[str]:
    physical_ids: set[str] = set()
    for segment in _iter_v2_segments(root):
        manifest = _read_path_json(segment / _SETTLEMENT_MANIFEST_FILE)
        if manifest.get("logical_settlement_batch_id") == logical_id:
            physical_ids.add(str(manifest.get("physical_settlement_batch_id")))
    return physical_ids


class _SettlementV2RootLock:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._path = root / _SETTLEMENT_LOCK_FILE
        self._fd: int | None = None

    def __enter__(self) -> "_SettlementV2RootLock":
        _make_safe_directory(self._root, self._root)
        deadline = time.monotonic() + 10.0
        while True:
            try:
                self._fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(
                    self._fd,
                    canonical_json_bytes(
                        {"pid": os.getpid(), "created_at_utc": datetime.now(tz=_UTC)}
                    ) + b"\n",
                )
                os.fsync(self._fd)
                return self
            except FileExistsError as exc:
                if time.monotonic() >= deadline:
                    raise NBAPlayerPointsClosingBindingError(
                        f"settlement writer lock is already held: {self._path}"
                    ) from exc
                time.sleep(0.01)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass


def _make_safe_directory(root: Path, path: Path) -> None:
    _ensure_under_root(root, path)
    _reject_reparse_components(path)
    path.mkdir(parents=True, exist_ok=True)
    _reject_reparse_components(path)


def _write_verified(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    if path.read_bytes() != data:
        raise NBAPlayerPointsClosingBindingError(f"short write detected: {path.name}")


def _read_path_json(path: Path) -> Mapping[str, object]:
    try:
        return _parse_json_object(path.read_bytes(), path.name)
    except OSError as exc:
        raise NBAPlayerPointsClosingBindingError(f"unable to read JSON: {path}") from exc


def _jsonl_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def _require_commit_sha(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold()
    if re.fullmatch(r"[0-9a-f]{7,40}", text) is None:
        raise NBAPlayerPointsClosingBindingError(f"{field_name} must be a git commit SHA")
    return text


def _call_failure_hook(failure_hook: FailureHook | None, stage: str) -> None:
    if failure_hook is not None:
        failure_hook(stage)


__all__ = [
    "NBA_PLAYER_POINTS_CLOSING_PREREQUISITE_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_MANUAL_PLAN_V2_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_CONTRACT_VERSION",
    "NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_RECEIPT_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_REQUEST_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION",
    "ClosingEvidenceSnapshot",
    "ClosingPrerequisite",
    "ClosingPrerequisiteValidationResult",
    "NBAPlayerPointsSettlementEvidenceV2IntegrityReport",
    "NBAPlayerPointsSettlementEvidenceV2WriteResult",
    "NBAPlayerPointsClosingBindingError",
    "ObservationBatchSnapshot",
    "PredictionClosingMapping",
    "SelectionBatchSnapshot",
    "build_nba_player_points_closing_prerequisite",
    "build_nba_player_points_settlement_approval_envelope",
    "canonical_closing_line",
    "canonical_json_bytes",
    "canonical_sha256",
    "preview_nba_player_points_settlement_evidence_v2_records",
    "validate_nba_player_points_closing_prerequisite",
    "verify_nba_player_points_settlement_evidence_v2",
    "write_nba_player_points_settlement_evidence_v2",
]
