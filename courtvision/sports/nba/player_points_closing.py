"""Append-only NBA player-points closing-line evidence writer.

This module stores already-normalized closing observations for offline research.
It reads completed prediction evidence only to validate references, then writes
separate immutable closing observation and selection segments. It performs no
provider I/O, reads no credentials, recalculates no predictions, settles no
outcomes, grades no results, and touches no production, Kelly, bankroll,
operator-board, or dashboard paths.
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
from typing import Any, Final
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from courtvision.sports.nba.player_points_evidence import (
    NBA_PLAYER_POINTS_EVIDENCE_DIR_NAME,
    NBA_PLAYER_POINTS_LEDGER_SCHEMA_VERSION,
    NBAPlayerPointsEvidenceWriterConfig,
    verify_nba_player_points_evidence,
)
from courtvision.sports.nba.player_points_research import (
    NBA_PLAYER_POINTS_MARKET,
    NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
    NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL,
    decimal_odds_from_american,
    implied_probability_from_american,
)


NBA_PLAYER_POINTS_CLOSING_SCHEMA_VERSION: Final = "nba-player-points-closing-v1"
NBA_PLAYER_POINTS_CLOSING_SELECTION_SCHEMA_VERSION: Final = (
    "nba-player-points-closing-selection-v1"
)
NBA_PLAYER_POINTS_DEFAULT_CLOSING_POLICY_ID: Final = (
    "nba-player-points-same-book-latest-pre-tip-v1"
)
NBA_PLAYER_POINTS_DEFAULT_CLOSING_POLICY_VERSION: Final = "1.0"

NBA_PLAYER_POINTS_CLOSING_STATUSES: Final = (
    "eligible",
    "too_early",
    "after_tipoff",
    "wrong_book",
    "wrong_market",
    "missing_line",
    "missing_price",
    "prediction_not_found",
    "prediction_not_complete",
    "conflicting",
    "quarantined",
    "manual_review_required",
)
NBA_PLAYER_POINTS_CLOSING_SELECTION_STATUSES: Final = (
    "selected",
    "no_eligible_observation",
    "prediction_not_found",
    "prediction_not_complete",
    "conflicting",
)

NBA_PLAYER_POINTS_CLOSING_COMPLETION_STATUSES: Final = (
    "writing",
    "complete",
    "conflicting",
    "already_complete",
)

NBA_PLAYER_POINTS_CLOSING_LOCK_FILE: Final = ".closing-writer.lock"
NBA_PLAYER_POINTS_CLOSING_COMPLETION_MARKER_FILE: Final = "COMPLETE"
NBA_PLAYER_POINTS_CLOSING_OBSERVATION_FILE: Final = "closing_observations.jsonl"
NBA_PLAYER_POINTS_CLOSING_CONFLICT_FILE: Final = "closing_conflicts.jsonl"
NBA_PLAYER_POINTS_CLOSING_MANIFEST_FILE: Final = "closing_manifest.json"
NBA_PLAYER_POINTS_SELECTION_FILE: Final = "selected_closing_rows.jsonl"
NBA_PLAYER_POINTS_SELECTION_MANIFEST_FILE: Final = "selection_manifest.json"
NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE: Final = "integrity_report.json"

_UTC: Final = timezone.utc
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA_RE: Final = re.compile(r"^[0-9a-f]{7,40}$")
_PROHIBITED_FIELD_FRAGMENTS: Final = (
    "settlement",
    "actual",
    "final_points",
    "actual_minutes",
    "roi",
    "kelly",
    "stake",
    "bankroll",
    "profit",
    "result",
    "grade",
)
_TORONTO_FALLBACK: Final = object()

FailureHook = Callable[[str], None]


class NBAPlayerPointsClosingError(ValueError):
    """Raised when closing-line evidence persistence fails closed."""


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsClosingPolicy:
    """Versioned deterministic close policy."""

    closing_policy_id: str = NBA_PLAYER_POINTS_DEFAULT_CLOSING_POLICY_ID
    closing_policy_version: str = NBA_PLAYER_POINTS_DEFAULT_CLOSING_POLICY_VERSION
    closing_window_start_seconds: int = 30 * 60
    closing_window_end_seconds: int = 0
    same_book_required: bool = True
    same_market_required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "closing_policy_id",
            _require_identifier(self.closing_policy_id, "closing_policy_id"),
        )
        object.__setattr__(
            self,
            "closing_policy_version",
            _require_text(self.closing_policy_version, "closing_policy_version"),
        )
        for field_name in (
            "closing_window_start_seconds",
            "closing_window_end_seconds",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise NBAPlayerPointsClosingError(f"{field_name} must be an integer")
            if value < 0:
                raise NBAPlayerPointsClosingError(f"{field_name} must be non-negative")
        if self.closing_window_start_seconds < self.closing_window_end_seconds:
            raise NBAPlayerPointsClosingError(
                "closing_window_start_seconds must be >= closing_window_end_seconds"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "closing_policy_id": self.closing_policy_id,
            "closing_policy_version": self.closing_policy_version,
            "closing_window_start_seconds": self.closing_window_start_seconds,
            "closing_window_end_seconds": self.closing_window_end_seconds,
            "same_book_required": self.same_book_required,
            "same_market_required": self.same_market_required,
            "selection_rule": (
                "latest eligible observation inside window; same-timestamp candidates "
                "with matching line and price tie by canonical hash; same-timestamp "
                "candidates with conflicting line or price fail closed"
            ),
            "window_definition": (
                "eligible observations have observation_timestamp_utc before tipoff, "
                "seconds_before_tipoff <= closing_window_start_seconds, and "
                "seconds_before_tipoff > closing_window_end_seconds"
            ),
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsClosingWriterConfig:
    """Explicit append-only closing writer configuration."""

    evidence_dir_name: str = NBA_PLAYER_POINTS_EVIDENCE_DIR_NAME
    closing_dir_name: str = "closing"
    observations_dir_name: str = "observations"
    selections_dir_name: str = "selections"
    segments_dir_name: str = "segments"
    completion_marker_file_name: str = NBA_PLAYER_POINTS_CLOSING_COMPLETION_MARKER_FILE
    lock_timeout_seconds: float = 10.0
    research_label: str = NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL
    policy: NBAPlayerPointsClosingPolicy = field(
        default_factory=NBAPlayerPointsClosingPolicy
    )

    def __post_init__(self) -> None:
        if not isinstance(self.policy, NBAPlayerPointsClosingPolicy):
            raise TypeError("policy must be NBAPlayerPointsClosingPolicy")
        if self.research_label != NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL:
            raise NBAPlayerPointsClosingError("research_label is unsupported")
        for field_name in (
            "evidence_dir_name",
            "closing_dir_name",
            "observations_dir_name",
            "selections_dir_name",
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
            raise NBAPlayerPointsClosingError(
                "lock_timeout_seconds must be finite and non-negative"
            )


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsPredictionReference:
    """Immutable pointer to a completed prediction ledger row."""

    prediction_id: str
    prediction_run_id: str
    prediction_evidence_segment: str
    prediction_record_hash: str

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, object]
    ) -> "NBAPlayerPointsPredictionReference":
        return cls(
            prediction_id=_require_identifier(payload.get("prediction_id"), "prediction_id"),
            prediction_run_id=_require_safe_path_component(
                payload.get("prediction_run_id"),
                "prediction_run_id",
            ),
            prediction_evidence_segment=_require_relative_path(
                payload.get("prediction_evidence_segment"),
                "prediction_evidence_segment",
            ),
            prediction_record_hash=_require_sha256(
                payload.get("prediction_record_hash"),
                "prediction_record_hash",
            ),
        )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "prediction_id",
            _require_identifier(self.prediction_id, "prediction_id"),
        )
        object.__setattr__(
            self,
            "prediction_run_id",
            _require_safe_path_component(self.prediction_run_id, "prediction_run_id"),
        )
        object.__setattr__(
            self,
            "prediction_evidence_segment",
            _require_relative_path(
                self.prediction_evidence_segment,
                "prediction_evidence_segment",
            ),
        )
        object.__setattr__(
            self,
            "prediction_record_hash",
            _require_sha256(self.prediction_record_hash, "prediction_record_hash"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "prediction_id": self.prediction_id,
            "prediction_run_id": self.prediction_run_id,
            "prediction_evidence_segment": self.prediction_evidence_segment,
            "prediction_record_hash": self.prediction_record_hash,
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsClosingObservationInput:
    """Already-normalized closing observation supplied by an offline caller."""

    prediction_reference: NBAPlayerPointsPredictionReference
    canonical_event_id: str
    provider_event_id: str
    player_id: str
    sportsbook: str
    market: str
    operating_date: str
    commence_time_utc: datetime | str
    closing_line: float | None
    closing_american_odds: int | None
    closing_market_status: str
    observation_timestamp_utc: datetime | str
    source_market_update_timestamp_utc: datetime | str
    closing_provider: str
    closing_source_id: str
    closing_source_hash: str

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, object]
    ) -> "NBAPlayerPointsClosingObservationInput":
        reference_payload = payload.get("prediction_reference")
        if isinstance(reference_payload, NBAPlayerPointsPredictionReference):
            reference = reference_payload
        elif isinstance(reference_payload, Mapping):
            reference = NBAPlayerPointsPredictionReference.from_mapping(reference_payload)
        else:
            reference = NBAPlayerPointsPredictionReference.from_mapping(payload)
        return cls(
            prediction_reference=reference,
            canonical_event_id=_require_identifier(
                payload.get("canonical_event_id"),
                "canonical_event_id",
            ),
            provider_event_id=_require_identifier(
                payload.get("provider_event_id"),
                "provider_event_id",
            ),
            player_id=_require_identifier(payload.get("player_id"), "player_id"),
            sportsbook=_require_text(payload.get("sportsbook"), "sportsbook"),
            market=_normalize_market(payload.get("market")),
            operating_date=_require_operating_date(
                payload.get("operating_date"),
                "operating_date",
            ),
            commence_time_utc=_coerce_utc_datetime(
                payload.get("commence_time_utc"),
                "commence_time_utc",
            ),
            closing_line=_optional_nonnegative_number(
                payload.get("closing_line"),
                "closing_line",
            ),
            closing_american_odds=_optional_american_odds(
                payload.get("closing_american_odds"),
                "closing_american_odds",
            ),
            closing_market_status=_require_text(
                payload.get("closing_market_status"),
                "closing_market_status",
            ),
            observation_timestamp_utc=_coerce_utc_datetime(
                payload.get("observation_timestamp_utc"),
                "observation_timestamp_utc",
            ),
            source_market_update_timestamp_utc=_coerce_utc_datetime(
                payload.get("source_market_update_timestamp_utc"),
                "source_market_update_timestamp_utc",
            ),
            closing_provider=_require_text(payload.get("closing_provider"), "closing_provider"),
            closing_source_id=_require_identifier(
                payload.get("closing_source_id"),
                "closing_source_id",
            ),
            closing_source_hash=_require_sha256(
                payload.get("closing_source_hash"),
                "closing_source_hash",
            ),
        )

    def __post_init__(self) -> None:
        if not isinstance(self.prediction_reference, NBAPlayerPointsPredictionReference):
            raise TypeError("prediction_reference must be NBAPlayerPointsPredictionReference")
        object.__setattr__(
            self,
            "canonical_event_id",
            _require_identifier(self.canonical_event_id, "canonical_event_id"),
        )
        object.__setattr__(
            self,
            "provider_event_id",
            _require_identifier(self.provider_event_id, "provider_event_id"),
        )
        object.__setattr__(self, "player_id", _require_identifier(self.player_id, "player_id"))
        object.__setattr__(self, "sportsbook", _require_text(self.sportsbook, "sportsbook"))
        object.__setattr__(self, "market", _normalize_market(self.market))
        object.__setattr__(
            self,
            "operating_date",
            _require_operating_date(self.operating_date, "operating_date"),
        )
        commence = _coerce_utc_datetime(self.commence_time_utc, "commence_time_utc")
        object.__setattr__(self, "commence_time_utc", commence)
        expected_operating_date = _toronto_operating_date(commence)
        if self.operating_date != expected_operating_date:
            raise NBAPlayerPointsClosingError(
                "operating_date must equal the America/Toronto date for commence_time_utc"
            )
        object.__setattr__(
            self,
            "closing_line",
            _optional_nonnegative_number(self.closing_line, "closing_line"),
        )
        object.__setattr__(
            self,
            "closing_american_odds",
            _optional_american_odds(self.closing_american_odds, "closing_american_odds"),
        )
        object.__setattr__(
            self,
            "closing_market_status",
            _require_text(self.closing_market_status, "closing_market_status"),
        )
        observation_time = _coerce_utc_datetime(
            self.observation_timestamp_utc,
            "observation_timestamp_utc",
        )
        source_time = _coerce_utc_datetime(
            self.source_market_update_timestamp_utc,
            "source_market_update_timestamp_utc",
        )
        if source_time > observation_time:
            raise NBAPlayerPointsClosingError(
                "source_market_update_timestamp_utc must be <= observation_timestamp_utc"
            )
        object.__setattr__(self, "observation_timestamp_utc", observation_time)
        object.__setattr__(self, "source_market_update_timestamp_utc", source_time)
        object.__setattr__(
            self,
            "closing_provider",
            _require_text(self.closing_provider, "closing_provider"),
        )
        object.__setattr__(
            self,
            "closing_source_id",
            _require_identifier(self.closing_source_id, "closing_source_id"),
        )
        object.__setattr__(
            self,
            "closing_source_hash",
            _require_sha256(self.closing_source_hash, "closing_source_hash"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "prediction_reference": self.prediction_reference.to_dict(),
            "canonical_event_id": self.canonical_event_id,
            "provider_event_id": self.provider_event_id,
            "player_id": self.player_id,
            "sportsbook": self.sportsbook,
            "market": self.market,
            "operating_date": self.operating_date,
            "commence_time_utc": _format_utc(self.commence_time_utc),
            "closing_line": self.closing_line,
            "closing_american_odds": self.closing_american_odds,
            "closing_market_status": self.closing_market_status,
            "observation_timestamp_utc": _format_utc(self.observation_timestamp_utc),
            "source_market_update_timestamp_utc": _format_utc(
                self.source_market_update_timestamp_utc
            ),
            "closing_provider": self.closing_provider,
            "closing_source_id": self.closing_source_id,
            "closing_source_hash": self.closing_source_hash,
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsClosingWriteResult:
    """Structured result for one closing write attempt."""

    completion_status: str
    evidence_root: Path
    observation_segment_directory: Path | None
    selection_segment_directory: Path | None
    closing_batch_id: str
    selection_batch_id: str
    closing_manifest: Mapping[str, object]
    selection_manifest: Mapping[str, object]
    integrity_report: Mapping[str, object]
    observations_written: int
    conflicts_written: int
    selections_written: int

    def to_dict(self) -> dict[str, object]:
        return {
            "completion_status": self.completion_status,
            "evidence_root": str(self.evidence_root),
            "observation_segment_directory": (
                str(self.observation_segment_directory)
                if self.observation_segment_directory is not None
                else None
            ),
            "selection_segment_directory": (
                str(self.selection_segment_directory)
                if self.selection_segment_directory is not None
                else None
            ),
            "closing_batch_id": self.closing_batch_id,
            "selection_batch_id": self.selection_batch_id,
            "closing_manifest": _json_ready(self.closing_manifest),
            "selection_manifest": _json_ready(self.selection_manifest),
            "integrity_report": _json_ready(self.integrity_report),
            "observations_written": self.observations_written,
            "conflicts_written": self.conflicts_written,
            "selections_written": self.selections_written,
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsClosingIntegrityReport:
    """Pure verifier report for closing evidence."""

    ok: bool
    violations: tuple[str, ...]
    evidence_root: Path
    observation_segments: tuple[Mapping[str, object], ...]
    selection_segments: tuple[Mapping[str, object], ...]
    effective_selections: tuple[Mapping[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "violations": list(self.violations),
            "evidence_root": str(self.evidence_root),
            "observation_segments": [_json_ready(item) for item in self.observation_segments],
            "selection_segments": [_json_ready(item) for item in self.selection_segments],
            "effective_selections": [
                _json_ready(item) for item in self.effective_selections
            ],
        }


@dataclass(frozen=True, slots=True)
class _PredictionIndex:
    rows_by_prediction_id: Mapping[str, Mapping[str, object]]
    completed_run_ids: frozenset[str]
    integrity_report: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _PreparedClosingBatch:
    observations: tuple[Mapping[str, object], ...]
    conflicts: tuple[Mapping[str, object], ...]
    affected_prediction_ids: tuple[str, ...]
    closing_batch_id: str
    operating_date: str


@dataclass(frozen=True, slots=True)
class _ExistingClosingEvidence:
    observations_by_id: Mapping[str, Mapping[str, object]]
    conflicts_by_id: Mapping[str, Mapping[str, object]]
    observations_by_prediction_id: Mapping[str, tuple[Mapping[str, object], ...]]
    selection_records_by_id: Mapping[str, Mapping[str, object]]
    violations: tuple[str, ...]


def default_closing_policy() -> NBAPlayerPointsClosingPolicy:
    """Return the explicit default close policy.

    The default window begins 1,800 seconds before tipoff and ends at 0 seconds
    before tipoff. Because observations at tipoff are prohibited, the inclusive
    practical window is 30:00 through 00:01 before tipoff.
    """

    return NBAPlayerPointsClosingPolicy()


def closing_observation_schema_definition() -> dict[str, object]:
    """Return the versioned closing observation contract."""

    return {
        "schema_version": NBA_PLAYER_POINTS_CLOSING_SCHEMA_VERSION,
        "required_fields": list(_closing_observation_field_names()),
        "statuses": list(NBA_PLAYER_POINTS_CLOSING_STATUSES),
        "hash_algorithm": "SHA-256",
        "canonical_json": {
            "sort_keys": True,
            "separators": [",", ":"],
            "encoding": "utf-8",
            "allow_nan": False,
        },
        "closing_observation_id_inputs": [
            "schema_version",
            "prediction_id",
            "prediction_run_id",
            "canonical_event_id",
            "provider_event_id",
            "player_id",
            "sportsbook",
            "market",
            "closing_line",
            "closing_american_odds",
            "observation_timestamp_utc",
            "source_market_update_timestamp_utc",
            "closing_provider",
            "closing_source_id",
            "closing_source_hash",
            "closing_policy_id",
            "closing_policy_version",
        ],
        "closing_record_hash_excludes": ["closing_record_hash"],
        "storage": (
            "closing/observations/segments/{operating_date}/{closing_batch_id}/"
            "closing_observations.jsonl"
        ),
    }


def closing_selection_schema_definition() -> dict[str, object]:
    """Return the versioned selected-closing contract."""

    return {
        "schema_version": NBA_PLAYER_POINTS_CLOSING_SELECTION_SCHEMA_VERSION,
        "required_fields": list(_closing_selection_field_names()),
        "selection_statuses": list(NBA_PLAYER_POINTS_CLOSING_SELECTION_STATUSES),
        "hash_algorithm": "SHA-256",
        "closing_selection_id_inputs": [
            "schema_version",
            "prediction_id",
            "prediction_run_id",
            "selected_observation_id",
            "selected_observation_hash",
            "closing_policy_id",
            "closing_policy_version",
            "selected_at_utc",
            "selection_status",
            "selection_exclusion_reason",
        ],
        "selection_record_hash_excludes": ["selection_record_hash"],
        "storage": (
            "closing/selections/segments/{operating_date}/{selection_batch_id}/"
            "selected_closing_rows.jsonl"
        ),
        "diagnostic_scope": "market movement only; no profit, ROI, grading, or Kelly fields",
    }


def write_nba_player_points_closing_evidence(
    evidence_root: str | Path,
    observations: Sequence[
        NBAPlayerPointsClosingObservationInput | Mapping[str, object]
    ],
    config: NBAPlayerPointsClosingWriterConfig | None = None,
    *,
    collection_timestamp_utc: datetime | str,
    repository_commit_sha: str,
    writer_timestamp_utc: datetime | str,
    failure_hook: FailureHook | None = None,
) -> NBAPlayerPointsClosingWriteResult:
    """Persist normalized closing observations and deterministic selections.

    The writer accepts completed prediction references and normalized closing
    observations only. It never invokes providers, never normalizes raw APIs,
    never recalculates predictions, never mutates prediction evidence, and never
    writes production histories or bankroll-facing artifacts.
    """

    cfg = config or NBAPlayerPointsClosingWriterConfig()
    _validate_config(cfg)
    collection_time = _coerce_utc_datetime(
        collection_timestamp_utc,
        "collection_timestamp_utc",
    )
    writer_time = _coerce_utc_datetime(writer_timestamp_utc, "writer_timestamp_utc")
    commit_sha = _require_commit_sha(repository_commit_sha, "repository_commit_sha")
    normalized_observations = tuple(
        observation
        if isinstance(observation, NBAPlayerPointsClosingObservationInput)
        else NBAPlayerPointsClosingObservationInput.from_mapping(observation)
        for observation in observations
    )
    if not normalized_observations:
        raise NBAPlayerPointsClosingError("observations must not be empty")
    root = _evidence_root(Path(evidence_root), cfg)

    _call_failure_hook(failure_hook, "before_any_write")
    with _ClosingRootLock(root, cfg):
        prediction_index = _load_prediction_index(root)
        existing = _scan_closing_evidence(root, cfg)
        if existing.violations:
            raise NBAPlayerPointsClosingError(
                "existing closing evidence failed verification: "
                + "; ".join(existing.violations)
            )
        prepared = _prepare_closing_batch(
            normalized_observations,
            prediction_index,
            existing,
            collection_timestamp_utc=collection_time,
            writer_timestamp_utc=writer_time,
            repository_commit_sha=commit_sha,
            config=cfg,
        )
        observation_segment_dir = _observation_segment_directory(
            root,
            prepared.operating_date,
            prepared.closing_batch_id,
            cfg,
        )
        _assert_no_existing_symlink(observation_segment_dir)
        _ensure_under_root(root, observation_segment_dir, "observation_segment_directory")

        observations_to_write, conflicts_to_write = _dedupe_prepared_against_existing(
            prepared,
            existing,
        )
        if observation_segment_dir.exists():
            closing_manifest = _read_existing_completed_closing_manifest(
                observation_segment_dir,
                cfg,
            )
            selection_manifest = _find_existing_selection_manifest_for_replay(
                root,
                cfg,
                affected_prediction_ids=prepared.affected_prediction_ids,
                collection_timestamp_utc=collection_time,
                writer_timestamp_utc=writer_time,
            )
            if not selection_manifest:
                selection_batch = _prepare_selection_batch(
                    root,
                    cfg,
                    existing,
                    observations_to_write,
                    affected_prediction_ids=prepared.affected_prediction_ids,
                    collection_timestamp_utc=collection_time,
                    writer_timestamp_utc=writer_time,
                    repository_commit_sha=commit_sha,
                )
                selection_manifest = _publish_selection_segment(
                    root,
                    selection_batch,
                    cfg,
                    failure_hook=failure_hook,
                )
            verifier_report = verify_nba_player_points_closing_evidence(root, cfg)
            if not verifier_report.ok:
                raise NBAPlayerPointsClosingError(
                    "closing evidence root failed verification after replay: "
                    + "; ".join(verifier_report.violations)
                )
            return NBAPlayerPointsClosingWriteResult(
                completion_status="already_complete",
                evidence_root=root,
                observation_segment_directory=observation_segment_dir,
                selection_segment_directory=(
                    _selection_segment_directory(
                        root,
                        str(selection_manifest["operating_date"]),
                        str(selection_manifest["selection_batch_id"]),
                        cfg,
                    )
                    if selection_manifest
                    else None
                ),
                closing_batch_id=prepared.closing_batch_id,
                selection_batch_id=str(selection_manifest.get("selection_batch_id", "")),
                closing_manifest=closing_manifest,
                selection_manifest=selection_manifest,
                integrity_report=verifier_report.to_dict(),
                observations_written=0,
                conflicts_written=0,
                selections_written=0,
            )

        closing_manifest = _publish_observation_segment(
            root,
            prepared,
            observations_to_write,
            conflicts_to_write,
            collection_timestamp_utc=collection_time,
            writer_timestamp_utc=writer_time,
            repository_commit_sha=commit_sha,
            config=cfg,
            failure_hook=failure_hook,
        )
        selection_existing = _scan_closing_evidence(root, cfg)
        if selection_existing.violations:
            raise NBAPlayerPointsClosingError(
                "closing observations failed verification before selection: "
                + "; ".join(selection_existing.violations)
            )
        selection_batch = _prepare_selection_batch(
            root,
            cfg,
            selection_existing,
            (),
            affected_prediction_ids=prepared.affected_prediction_ids,
            collection_timestamp_utc=collection_time,
            writer_timestamp_utc=writer_time,
            repository_commit_sha=commit_sha,
        )
        selection_manifest = _publish_selection_segment(
            root,
            selection_batch,
            cfg,
            failure_hook=failure_hook,
        )

        verifier_report = verify_nba_player_points_closing_evidence(root, cfg)
        if not verifier_report.ok:
            raise NBAPlayerPointsClosingError(
                "closing evidence root failed verification after write: "
                + "; ".join(verifier_report.violations)
            )
        return NBAPlayerPointsClosingWriteResult(
            completion_status=(
                "conflicting" if conflicts_to_write else "complete"
            ),
            evidence_root=root,
            observation_segment_directory=observation_segment_dir,
            selection_segment_directory=_selection_segment_directory(
                root,
                str(selection_manifest["operating_date"]),
                str(selection_manifest["selection_batch_id"]),
                cfg,
            ),
            closing_batch_id=prepared.closing_batch_id,
            selection_batch_id=str(selection_manifest["selection_batch_id"]),
            closing_manifest=closing_manifest,
            selection_manifest=selection_manifest,
            integrity_report=verifier_report.to_dict(),
            observations_written=len(observations_to_write),
            conflicts_written=len(conflicts_to_write),
            selections_written=int(selection_manifest.get("selection_count", 0)),
        )


def verify_nba_player_points_closing_evidence(
    evidence_root: str | Path,
    config: NBAPlayerPointsClosingWriterConfig | None = None,
) -> NBAPlayerPointsClosingIntegrityReport:
    """Inspect closing evidence without mutation or provider access."""

    cfg = config or NBAPlayerPointsClosingWriterConfig()
    _validate_config(cfg)
    root = _evidence_root(Path(evidence_root), cfg)
    violations: list[str] = []
    observation_segments: list[Mapping[str, object]] = []
    selection_segments: list[Mapping[str, object]] = []

    try:
        prediction_index = _load_prediction_index(root)
    except NBAPlayerPointsClosingError as exc:
        violations.append(str(exc))
        prediction_index = _PredictionIndex(
            rows_by_prediction_id=MappingProxyType({}),
            completed_run_ids=frozenset(),
            integrity_report=MappingProxyType({}),
        )

    scan = _scan_closing_evidence(root, cfg)
    violations.extend(scan.violations)

    for segment in _iter_completed_observation_segments(root, cfg):
        report = _verify_observation_segment(segment, cfg, prediction_index)
        observation_segments.append(MappingProxyType(report))
        violations.extend(str(item) for item in report["violations"])

    observations_by_id = _valid_observation_index_from_segments(observation_segments)
    for segment in _iter_completed_selection_segments(root, cfg):
        report = _verify_selection_segment(segment, cfg, observations_by_id)
        selection_segments.append(MappingProxyType(report))
        violations.extend(str(item) for item in report["violations"])

    effective_selections: tuple[Mapping[str, object], ...] = ()
    if not violations:
        try:
            effective_selections, effective_violations = _build_effective_selection_reports(
                root,
                cfg,
            )
            violations.extend(effective_violations)
        except NBAPlayerPointsClosingError as exc:
            violations.append(str(exc))

    return NBAPlayerPointsClosingIntegrityReport(
        ok=not violations,
        violations=tuple(violations),
        evidence_root=root,
        observation_segments=tuple(observation_segments),
        selection_segments=tuple(selection_segments),
        effective_selections=effective_selections if not violations else (),
    )


def resolve_nba_player_points_effective_closing_selection(
    evidence_root: str | Path,
    prediction_id: str,
    config: NBAPlayerPointsClosingWriterConfig | None = None,
) -> Mapping[str, object]:
    """Return the effective close for one prediction and explicit policy identity.

    The resolver is pure and offline. It reads completed closing evidence,
    validates observation and selection lineage, filters by prediction plus
    closing policy id/version, and returns one deterministic effective selection.
    """

    cfg = config or NBAPlayerPointsClosingWriterConfig()
    _validate_config(cfg)
    requested_prediction_id = _require_identifier(prediction_id, "prediction_id")
    report = verify_nba_player_points_closing_evidence(evidence_root, cfg)
    if not report.ok:
        raise NBAPlayerPointsClosingError(
            "closing evidence failed effective-selection verification: "
            + "; ".join(report.violations)
        )
    matches = tuple(
        item
        for item in report.effective_selections
        if item.get("prediction_id") == requested_prediction_id
        and item.get("closing_policy_id") == cfg.policy.closing_policy_id
        and item.get("closing_policy_version") == cfg.policy.closing_policy_version
    )
    if len(matches) != 1:
        raise NBAPlayerPointsClosingError(
            "expected exactly one effective selection for prediction and policy identity"
        )
    return MappingProxyType(_json_clone_mapping(matches[0]))


def _prepare_closing_batch(
    observations: Sequence[NBAPlayerPointsClosingObservationInput],
    prediction_index: _PredictionIndex,
    existing: _ExistingClosingEvidence,
    *,
    collection_timestamp_utc: datetime,
    writer_timestamp_utc: datetime,
    repository_commit_sha: str,
    config: NBAPlayerPointsClosingWriterConfig,
) -> _PreparedClosingBatch:
    rows: list[Mapping[str, object]] = []
    conflicts: list[Mapping[str, object]] = []
    affected_prediction_ids: set[str] = set()
    operating_dates: set[str] = set()
    logical_keys: dict[tuple[object, ...], Mapping[str, object]] = {}
    conflicting_ids: set[str] = set()

    for observation in observations:
        reference = observation.prediction_reference
        affected_prediction_ids.add(reference.prediction_id)
        operating_dates.add(observation.operating_date)
        record = _closing_record_from_observation(
            observation,
            prediction_index,
            collection_timestamp_utc=collection_timestamp_utc,
            writer_timestamp_utc=writer_timestamp_utc,
            repository_commit_sha=repository_commit_sha,
            config=config,
        )
        _validate_no_prohibited_fields(record)
        logical_key = _observation_logical_conflict_key(record)
        prior = logical_keys.get(logical_key)
        if prior is not None and prior["closing_record_hash"] != record["closing_record_hash"]:
            conflicting_ids.add(str(prior["closing_observation_id"]))
            conflicting_ids.add(str(record["closing_observation_id"]))
            conflicts.append(
                _conflict_record(
                    record,
                    "within_batch",
                    "same timestamp/source identity has conflicting line or price",
                    prior,
                )
            )
            conflicts.append(
                _conflict_record(
                    prior,
                    "within_batch",
                    "same timestamp/source identity has conflicting line or price",
                    record,
                )
            )
            continue
        logical_keys[logical_key] = record
        rows.append(record)

    unique_rows: dict[str, Mapping[str, object]] = {}
    for row in rows:
        observation_id = str(row["closing_observation_id"])
        if observation_id in conflicting_ids:
            continue
        existing_row = unique_rows.get(observation_id)
        if existing_row is None:
            unique_rows[observation_id] = row
            continue
        if existing_row["closing_record_hash"] == row["closing_record_hash"]:
            continue
        conflicting_ids.add(observation_id)
        conflicts.append(
            _conflict_record(
                row,
                "within_batch",
                "same closing_observation_id has conflicting canonical content",
                existing_row,
            )
        )

    for row in tuple(unique_rows.values()):
        observation_id = str(row["closing_observation_id"])
        if observation_id in conflicting_ids:
            unique_rows.pop(observation_id, None)
            continue
        existing_row = existing.observations_by_id.get(observation_id)
        if existing_row is not None and existing_row["closing_record_hash"] != row["closing_record_hash"]:
            unique_rows.pop(observation_id, None)
            conflicts.append(
                _conflict_record(
                    row,
                    "existing_observation",
                    "existing closing_observation_id has conflicting canonical content",
                    existing_row,
                )
            )
            continue
        for existing_record in existing.observations_by_prediction_id.get(
            str(row["prediction_id"]),
            (),
        ):
            if (
                _observation_logical_conflict_key(existing_record)
                == _observation_logical_conflict_key(row)
                and existing_record["closing_record_hash"] != row["closing_record_hash"]
            ):
                unique_rows.pop(observation_id, None)
                conflicts.append(
                    _conflict_record(
                        row,
                        "existing_observation",
                        "same timestamp/source identity conflicts with existing closing content",
                        existing_record,
                    )
                )
                break

    if len(operating_dates) != 1:
        raise NBAPlayerPointsClosingError("exactly one operating_date is required per closing batch")
    operating_date = next(iter(operating_dates))
    sorted_rows = _canonical_sort_payloads(tuple(unique_rows.values()))
    sorted_conflicts = _canonical_sort_payloads(_dedupe_conflicts(conflicts))
    batch_id = _closing_batch_id(
        observations=sorted_rows,
        conflicts=sorted_conflicts,
        operating_date=operating_date,
        collection_timestamp_utc=collection_timestamp_utc,
        policy=config.policy,
    )
    return _PreparedClosingBatch(
        observations=sorted_rows,
        conflicts=sorted_conflicts,
        affected_prediction_ids=tuple(sorted(affected_prediction_ids)),
        closing_batch_id=batch_id,
        operating_date=operating_date,
    )


def _closing_record_from_observation(
    observation: NBAPlayerPointsClosingObservationInput,
    prediction_index: _PredictionIndex,
    *,
    collection_timestamp_utc: datetime,
    writer_timestamp_utc: datetime,
    repository_commit_sha: str,
    config: NBAPlayerPointsClosingWriterConfig,
) -> Mapping[str, object]:
    reference = observation.prediction_reference
    policy = config.policy
    prediction_entry = prediction_index.rows_by_prediction_id.get(reference.prediction_id)
    prediction_row = (
        prediction_entry.get("record")
        if isinstance(prediction_entry, Mapping)
        else None
    )
    evidence_reference = (
        prediction_entry.get("evidence_reference")
        if isinstance(prediction_entry, Mapping)
        else None
    )
    if isinstance(prediction_row, Mapping):
        if reference.prediction_run_id != prediction_row.get("prediction_run_id"):
            prediction_row = None
        elif reference.prediction_record_hash != prediction_row.get("ledger_record_hash"):
            raise NBAPlayerPointsClosingError("prediction_record_hash does not match ledger row")
        elif (
            isinstance(evidence_reference, Mapping)
            and reference.prediction_evidence_segment
            != evidence_reference.get("prediction_evidence_segment")
        ):
            raise NBAPlayerPointsClosingError(
                "prediction_evidence_segment does not match ledger row"
            )

    if prediction_row is not None and reference.prediction_run_id not in prediction_index.completed_run_ids:
        status = "prediction_not_complete"
        exclusion_reason = "prediction_run_not_complete"
    elif prediction_row is None:
        status = "prediction_not_found"
        exclusion_reason = "prediction_reference_not_found"
    else:
        status, exclusion_reason = _evaluate_observation_status(
            observation,
            prediction_row,
            policy,
        )
    seconds_before_tipoff = int(
        (
            _coerce_utc_datetime(observation.commence_time_utc, "commence_time_utc")
            - _coerce_utc_datetime(
                observation.observation_timestamp_utc,
                "observation_timestamp_utc",
            )
        ).total_seconds()
    )
    closing_decimal_odds = (
        decimal_odds_from_american(observation.closing_american_odds)
        if observation.closing_american_odds is not None
        else None
    )
    closing_implied_probability = (
        implied_probability_from_american(observation.closing_american_odds)
        if observation.closing_american_odds is not None
        else None
    )
    if prediction_row is None:
        prediction_sportsbook = None
        prediction_market = None
        prediction_line = None
        prediction_american_odds = None
    else:
        prediction_sportsbook = prediction_row["sportsbook"]
        prediction_market = prediction_row["market"]
        prediction_line = prediction_row["line"]
        prediction_american_odds = prediction_row["american_odds"]
    payload_without_ids = {
        "schema_version": NBA_PLAYER_POINTS_CLOSING_SCHEMA_VERSION,
        "prediction_id": reference.prediction_id,
        "prediction_run_id": reference.prediction_run_id,
        "prediction_evidence_segment": reference.prediction_evidence_segment,
        "prediction_record_hash": reference.prediction_record_hash,
        "canonical_event_id": observation.canonical_event_id,
        "provider_event_id": observation.provider_event_id,
        "player_id": observation.player_id,
        "sportsbook": observation.sportsbook,
        "market": observation.market,
        "operating_date": observation.operating_date,
        "commence_time_utc": _format_utc(observation.commence_time_utc),
        "closing_line": observation.closing_line,
        "closing_american_odds": observation.closing_american_odds,
        "closing_decimal_odds": closing_decimal_odds,
        "closing_implied_probability": closing_implied_probability,
        "closing_market_status": observation.closing_market_status,
        "observation_timestamp_utc": _format_utc(observation.observation_timestamp_utc),
        "source_market_update_timestamp_utc": _format_utc(
            observation.source_market_update_timestamp_utc
        ),
        "seconds_before_tipoff": seconds_before_tipoff,
        "closing_policy_id": policy.closing_policy_id,
        "closing_policy_version": policy.closing_policy_version,
        "closing_window_start_seconds": policy.closing_window_start_seconds,
        "closing_window_end_seconds": policy.closing_window_end_seconds,
        "same_book_required": policy.same_book_required,
        "same_market_required": policy.same_market_required,
        "original_prediction_line": prediction_line,
        "original_prediction_american_odds": prediction_american_odds,
        "line_match_status": _line_match_status(observation.closing_line, prediction_line),
        "book_match_status": _match_status(observation.sportsbook, prediction_sportsbook),
        "market_match_status": _match_status(observation.market, prediction_market),
        "event_match_status": _match_status(
            observation.canonical_event_id,
            prediction_row.get("canonical_event_id") if isinstance(prediction_row, Mapping) else None,
        ),
        "player_match_status": _match_status(
            observation.player_id,
            prediction_row.get("player_id") if isinstance(prediction_row, Mapping) else None,
        ),
        "observation_eligibility_status": status,
        "exclusion_reason": exclusion_reason,
        "closing_provider": observation.closing_provider,
        "closing_source_id": observation.closing_source_id,
        "closing_source_hash": observation.closing_source_hash,
        "collection_timestamp_utc": _format_utc(collection_timestamp_utc),
        "repository_commit_sha": repository_commit_sha,
        "writer_timestamp_utc": _format_utc(writer_timestamp_utc),
        "research_label": config.research_label,
    }
    payload = {
        "closing_observation_id": _closing_observation_id(payload_without_ids),
        **payload_without_ids,
    }
    payload["closing_record_hash"] = _record_hash(payload, "closing_record_hash")
    return MappingProxyType(payload)


def _evaluate_observation_status(
    observation: NBAPlayerPointsClosingObservationInput,
    prediction_row: Mapping[str, object],
    policy: NBAPlayerPointsClosingPolicy,
) -> tuple[str, str]:
    if prediction_row.get("projection_research_eligible") is not True:
        return "prediction_not_complete", "prediction_row_not_projection_research_eligible"
    if prediction_row.get("assembly_status") not in {
        "eligible_projection_research",
        "eligible_probability_research",
    }:
        return "prediction_not_complete", "prediction_row_not_complete"
    if observation.closing_line is None:
        return "missing_line", "missing_closing_line"
    if observation.closing_american_odds is None:
        return "missing_price", "missing_closing_american_odds"
    if observation.canonical_event_id != prediction_row.get("canonical_event_id"):
        return "manual_review_required", "wrong_event"
    if observation.player_id != prediction_row.get("player_id"):
        return "manual_review_required", "wrong_player"
    if observation.operating_date != prediction_row.get("operating_date"):
        return "manual_review_required", "operating_date_mismatch"
    if _format_utc(observation.commence_time_utc) != prediction_row.get("commence_time_utc"):
        return "manual_review_required", "commence_time_mismatch"
    if policy.same_book_required and observation.sportsbook != prediction_row.get("sportsbook"):
        return "wrong_book", "same_book_required"
    if policy.same_market_required and observation.market != prediction_row.get("market"):
        return "wrong_market", "same_market_required"
    if observation.observation_timestamp_utc >= observation.commence_time_utc:
        return "after_tipoff", "observation_at_or_after_tipoff"
    seconds_before_tipoff = int(
        (observation.commence_time_utc - observation.observation_timestamp_utc).total_seconds()
    )
    if seconds_before_tipoff > policy.closing_window_start_seconds:
        return "too_early", "observation_before_close_window"
    if seconds_before_tipoff <= policy.closing_window_end_seconds:
        return "after_tipoff", "observation_not_before_window_end"
    if not str(observation.closing_market_status).strip():
        return "manual_review_required", "missing_market_status"
    return "eligible", "none"


def _dedupe_prepared_against_existing(
    prepared: _PreparedClosingBatch,
    existing: _ExistingClosingEvidence,
) -> tuple[tuple[Mapping[str, object], ...], tuple[Mapping[str, object], ...]]:
    observations: list[Mapping[str, object]] = []
    conflicts: list[Mapping[str, object]] = list(prepared.conflicts)
    for row in prepared.observations:
        existing_row = existing.observations_by_id.get(str(row["closing_observation_id"]))
        if existing_row is not None:
            if existing_row["closing_record_hash"] != row["closing_record_hash"]:
                conflicts.append(
                    _conflict_record(
                        row,
                        "existing_observation",
                        "existing closing_observation_id has conflicting canonical content",
                        existing_row,
                    )
                )
            continue
        observations.append(row)
    return _canonical_sort_payloads(observations), _canonical_sort_payloads(
        _dedupe_conflicts(conflicts)
    )


def _prepare_selection_batch(
    root: Path,
    config: NBAPlayerPointsClosingWriterConfig,
    existing: _ExistingClosingEvidence,
    newly_published_observations: Sequence[Mapping[str, object]],
    *,
    affected_prediction_ids: Sequence[str],
    collection_timestamp_utc: datetime,
    writer_timestamp_utc: datetime,
    repository_commit_sha: str,
) -> Mapping[str, object]:
    observations_by_prediction: dict[str, list[Mapping[str, object]]] = {
        prediction_id: list(existing.observations_by_prediction_id.get(prediction_id, ()))
        for prediction_id in affected_prediction_ids
    }
    for row in newly_published_observations:
        observations_by_prediction.setdefault(str(row["prediction_id"]), []).append(row)
    records: list[Mapping[str, object]] = []
    operating_dates: set[str] = set()
    for prediction_id in sorted(set(affected_prediction_ids)):
        candidates = tuple(observations_by_prediction.get(prediction_id, ()))
        if candidates:
            operating_dates.update(str(row["operating_date"]) for row in candidates)
        records.append(
            _selection_record_for_prediction(
                prediction_id,
                candidates,
                policy=config.policy,
                selected_at_utc=writer_timestamp_utc,
                repository_commit_sha=repository_commit_sha,
                research_label=config.research_label,
            )
        )
    if not records:
        return MappingProxyType({})
    if len(operating_dates) != 1:
        # If this batch only produced no-reference conflicts, use the caller batch date.
        operating_dates.add(
            _require_operating_date(
                _toronto_operating_date(collection_timestamp_utc),
                "operating_date",
            )
        )
    operating_date = sorted(operating_dates)[0]
    sorted_records = _canonical_sort_payloads(records)
    selection_batch_id = _selection_batch_id(
        records=sorted_records,
        operating_date=operating_date,
        collection_timestamp_utc=collection_timestamp_utc,
        policy=config.policy,
    )
    return MappingProxyType(
        {
            "selection_batch_id": selection_batch_id,
            "operating_date": operating_date,
            "records": sorted_records,
            "collection_timestamp_utc": _format_utc(collection_timestamp_utc),
            "writer_timestamp_utc": _format_utc(writer_timestamp_utc),
            "repository_commit_sha": repository_commit_sha,
        }
    )


def _selection_record_for_prediction(
    prediction_id: str,
    observations: Sequence[Mapping[str, object]],
    *,
    policy: NBAPlayerPointsClosingPolicy,
    selected_at_utc: datetime,
    repository_commit_sha: str,
    research_label: str,
) -> Mapping[str, object]:
    eligible = [
        row
        for row in observations
        if row.get("observation_eligibility_status") == "eligible"
        and row.get("closing_policy_id") == policy.closing_policy_id
        and row.get("closing_policy_version") == policy.closing_policy_version
    ]
    selected, conflict_reason = _select_effective_observation(eligible)
    if selected is None:
        fallback = observations[0] if observations else {}
        status = "conflicting" if conflict_reason else "no_eligible_observation"
        reason = conflict_reason or "no_eligible_observation"
        payload = {
            "schema_version": NBA_PLAYER_POINTS_CLOSING_SELECTION_SCHEMA_VERSION,
            "prediction_id": prediction_id,
            "prediction_run_id": fallback.get("prediction_run_id"),
            "selected_observation_id": None,
            "selected_observation_hash": None,
            "closing_policy_id": policy.closing_policy_id,
            "closing_policy_version": policy.closing_policy_version,
            "selected_at_utc": _format_utc(selected_at_utc),
            "closing_line": None,
            "closing_american_odds": None,
            "closing_decimal_odds": None,
            "closing_implied_probability": None,
            "original_prediction_line": None,
            "original_prediction_american_odds": None,
            "line_movement": None,
            "price_movement": None,
            "selection_status": status,
            "selection_exclusion_reason": reason,
            "repository_commit_sha": repository_commit_sha,
            "research_label": research_label,
        }
    else:
        original_line, original_american = _original_prediction_prices(selected)
        status = "selected"
        reason = "none"
        payload = {
            "schema_version": NBA_PLAYER_POINTS_CLOSING_SELECTION_SCHEMA_VERSION,
            "prediction_id": selected["prediction_id"],
            "prediction_run_id": selected["prediction_run_id"],
            "selected_observation_id": selected["closing_observation_id"],
            "selected_observation_hash": selected["closing_record_hash"],
            "closing_policy_id": policy.closing_policy_id,
            "closing_policy_version": policy.closing_policy_version,
            "selected_at_utc": _format_utc(selected_at_utc),
            "closing_line": selected["closing_line"],
            "closing_american_odds": selected["closing_american_odds"],
            "closing_decimal_odds": selected["closing_decimal_odds"],
            "closing_implied_probability": selected["closing_implied_probability"],
            "original_prediction_line": original_line,
            "original_prediction_american_odds": original_american,
            "line_movement": (
                round(float(selected["closing_line"]) - float(original_line), 6)
                if original_line is not None and selected["closing_line"] is not None
                else None
            ),
            "price_movement": (
                int(selected["closing_american_odds"]) - int(original_american)
                if original_american is not None and selected["closing_american_odds"] is not None
                else None
            ),
            "selection_status": status,
            "selection_exclusion_reason": reason,
            "repository_commit_sha": repository_commit_sha,
            "research_label": research_label,
        }
    payload["closing_selection_id"] = _closing_selection_id(payload)
    payload["selection_record_hash"] = _record_hash(payload, "selection_record_hash")
    _validate_no_prohibited_fields(payload)
    return MappingProxyType(payload)


def _select_effective_observation(
    eligible: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object] | None, str | None]:
    if not eligible:
        return None, None
    latest_timestamp = max(
        _coerce_utc_datetime(
            row["observation_timestamp_utc"],
            "observation_timestamp_utc",
        )
        for row in eligible
    )
    latest = tuple(
        row
        for row in eligible
        if _coerce_utc_datetime(
            row["observation_timestamp_utc"],
            "observation_timestamp_utc",
        )
        == latest_timestamp
    )
    latest_prices = {
        (
            row.get("closing_line"),
            row.get("closing_american_odds"),
        )
        for row in latest
    }
    if len(latest_prices) > 1:
        return None, "same_timestamp_conflicting_eligible_observations"
    return (
        sorted(
            latest,
            key=lambda row: str(row["closing_record_hash"]),
        )[-1],
        None,
    )


def _original_prediction_prices(selected: Mapping[str, object]) -> tuple[float | None, int | None]:
    # The selected observation carries prediction lineage but not the whole row.
    # This is intentionally resolved by the verifier/writer from the linked
    # prediction evidence rather than persisted into observation records.
    return (
        _optional_nonnegative_number(selected.get("original_prediction_line"), "original_prediction_line")
        if "original_prediction_line" in selected
        else None,
        _optional_american_odds(
            selected.get("original_prediction_american_odds"),
            "original_prediction_american_odds",
        )
        if "original_prediction_american_odds" in selected
        else None,
    )


def _publish_observation_segment(
    root: Path,
    prepared: _PreparedClosingBatch,
    observations_to_write: Sequence[Mapping[str, object]],
    conflicts_to_write: Sequence[Mapping[str, object]],
    *,
    collection_timestamp_utc: datetime,
    writer_timestamp_utc: datetime,
    repository_commit_sha: str,
    config: NBAPlayerPointsClosingWriterConfig,
    failure_hook: FailureHook | None,
) -> Mapping[str, object]:
    segment_dir = _observation_segment_directory(
        root,
        prepared.operating_date,
        prepared.closing_batch_id,
        config,
    )
    if segment_dir.exists():
        return _read_existing_completed_closing_manifest(segment_dir, config)
    parent = segment_dir.parent
    _make_directory(parent)
    stage_dir = parent / f".obs-{uuid4().hex[:12]}"
    try:
        stage_dir.mkdir()
        _call_failure_hook(failure_hook, "after_observation_temp_dir_created")
        files = {
            NBA_PLAYER_POINTS_CLOSING_OBSERVATION_FILE: _jsonl_bytes(observations_to_write),
            NBA_PLAYER_POINTS_CLOSING_CONFLICT_FILE: _jsonl_bytes(conflicts_to_write),
            NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE: _json_file_bytes(
                {"status": "writing", "violations": []}
            ),
        }
        for name, data in files.items():
            _write_bytes_verified(stage_dir / name, data)
            if name == NBA_PLAYER_POINTS_CLOSING_OBSERVATION_FILE:
                _call_failure_hook(failure_hook, "after_observation_file_write")
        file_hashes = {name: _sha256_bytes(data) for name, data in files.items()}
        manifest = _closing_manifest_payload(
            closing_batch_id=prepared.closing_batch_id,
            operating_date=prepared.operating_date,
            observation_count=len(observations_to_write),
            conflict_count=len(conflicts_to_write),
            collection_timestamp_utc=collection_timestamp_utc,
            writer_timestamp_utc=writer_timestamp_utc,
            repository_commit_sha=repository_commit_sha,
            completion_status="complete",
            file_hashes=file_hashes,
            config=config,
        )
        _write_json_file(stage_dir / NBA_PLAYER_POINTS_CLOSING_MANIFEST_FILE, manifest)
        manifest_hash = _sha256_file(stage_dir / NBA_PLAYER_POINTS_CLOSING_MANIFEST_FILE)
        integrity_payload = {
            "status": "complete",
            "violations": [],
            "closing_batch_id": prepared.closing_batch_id,
            "file_hashes": file_hashes,
            "manifest_hash": manifest_hash,
            "observation_count": len(observations_to_write),
            "conflict_count": len(conflicts_to_write),
        }
        _write_json_file(stage_dir / NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE, integrity_payload)
        final_file_hashes = dict(file_hashes)
        final_file_hashes[NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE] = _sha256_file(
            stage_dir / NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE
        )
        final_manifest = _closing_manifest_payload(
            closing_batch_id=prepared.closing_batch_id,
            operating_date=prepared.operating_date,
            observation_count=len(observations_to_write),
            conflict_count=len(conflicts_to_write),
            collection_timestamp_utc=collection_timestamp_utc,
            writer_timestamp_utc=writer_timestamp_utc,
            repository_commit_sha=repository_commit_sha,
            completion_status="complete",
            file_hashes=final_file_hashes,
            config=config,
        )
        _write_json_file(stage_dir / NBA_PLAYER_POINTS_CLOSING_MANIFEST_FILE, final_manifest)
        _write_json_file(
            stage_dir / config.completion_marker_file_name,
            {
                "completion_status": "complete",
                "manifest_file": NBA_PLAYER_POINTS_CLOSING_MANIFEST_FILE,
                "manifest_hash": _sha256_file(
                    stage_dir / NBA_PLAYER_POINTS_CLOSING_MANIFEST_FILE
                ),
            },
        )
        _call_failure_hook(failure_hook, "before_observation_segment_publication")
        stage_dir.rename(segment_dir)
        return MappingProxyType(dict(final_manifest))
    except Exception:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def _publish_selection_segment(
    root: Path,
    selection_batch: Mapping[str, object],
    config: NBAPlayerPointsClosingWriterConfig,
    *,
    failure_hook: FailureHook | None,
) -> Mapping[str, object]:
    if not selection_batch:
        return MappingProxyType({})
    records = tuple(selection_batch["records"])  # type: ignore[index]
    if not records:
        return MappingProxyType({})
    segment_dir = _selection_segment_directory(
        root,
        str(selection_batch["operating_date"]),
        str(selection_batch["selection_batch_id"]),
        config,
    )
    if segment_dir.exists():
        return _read_existing_completed_selection_manifest(segment_dir, config)
    parent = segment_dir.parent
    _make_directory(parent)
    stage_dir = parent / f".sel-{uuid4().hex[:12]}"
    try:
        stage_dir.mkdir()
        _call_failure_hook(failure_hook, "after_selection_temp_dir_created")
        selection_bytes = _jsonl_bytes(records)
        _write_bytes_verified(stage_dir / NBA_PLAYER_POINTS_SELECTION_FILE, selection_bytes)
        _call_failure_hook(failure_hook, "after_selection_file_write")
        integrity = {
            "status": "complete",
            "violations": [],
            "selection_batch_id": selection_batch["selection_batch_id"],
            "selection_hash": _sha256_bytes(selection_bytes),
            "selection_count": len(records),
        }
        _write_json_file(stage_dir / NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE, integrity)
        file_hashes = {
            NBA_PLAYER_POINTS_SELECTION_FILE: _sha256_bytes(selection_bytes),
            NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE: _sha256_file(
                stage_dir / NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE
            ),
        }
        manifest = _selection_manifest_payload(
            selection_batch=selection_batch,
            selection_count=len(records),
            file_hashes=file_hashes,
            completion_status="complete",
            config=config,
        )
        _write_json_file(stage_dir / NBA_PLAYER_POINTS_SELECTION_MANIFEST_FILE, manifest)
        _write_json_file(
            stage_dir / config.completion_marker_file_name,
            {
                "completion_status": "complete",
                "manifest_file": NBA_PLAYER_POINTS_SELECTION_MANIFEST_FILE,
                "manifest_hash": _sha256_file(
                    stage_dir / NBA_PLAYER_POINTS_SELECTION_MANIFEST_FILE
                ),
            },
        )
        _call_failure_hook(failure_hook, "before_selection_segment_publication")
        stage_dir.rename(segment_dir)
        return MappingProxyType(dict(manifest))
    except Exception:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def _find_existing_selection_manifest_for_replay(
    root: Path,
    config: NBAPlayerPointsClosingWriterConfig,
    *,
    affected_prediction_ids: Sequence[str],
    collection_timestamp_utc: datetime,
    writer_timestamp_utc: datetime,
) -> Mapping[str, object]:
    affected = set(affected_prediction_ids)
    collection_text = _format_utc(collection_timestamp_utc)
    writer_text = _format_utc(writer_timestamp_utc)
    for segment in _iter_completed_selection_segments(root, config):
        manifest = _read_json_file(segment / NBA_PLAYER_POINTS_SELECTION_MANIFEST_FILE)
        if manifest.get("collection_timestamp_utc") != collection_text:
            continue
        if manifest.get("writer_timestamp_utc") != writer_text:
            continue
        if not _manifest_policy_matches(manifest, config.policy):
            continue
        rows = _read_jsonl_strict(segment / NBA_PLAYER_POINTS_SELECTION_FILE)
        row_prediction_ids = {str(row.get("prediction_id")) for row in rows}
        if affected and affected.issubset(row_prediction_ids):
            return manifest
    return MappingProxyType({})


def _manifest_policy_matches(
    manifest: Mapping[str, object],
    policy: NBAPlayerPointsClosingPolicy,
) -> bool:
    manifest_policy = manifest.get("closing_policy")
    if not isinstance(manifest_policy, Mapping):
        return False
    return (
        manifest_policy.get("closing_policy_id") == policy.closing_policy_id
        and manifest_policy.get("closing_policy_version")
        == policy.closing_policy_version
    )


def _closing_manifest_payload(
    *,
    closing_batch_id: str,
    operating_date: str,
    observation_count: int,
    conflict_count: int,
    collection_timestamp_utc: datetime,
    writer_timestamp_utc: datetime,
    repository_commit_sha: str,
    completion_status: str,
    file_hashes: Mapping[str, str],
    config: NBAPlayerPointsClosingWriterConfig,
) -> Mapping[str, object]:
    if completion_status not in NBA_PLAYER_POINTS_CLOSING_COMPLETION_STATUSES:
        raise NBAPlayerPointsClosingError("unsupported closing completion status")
    manifest = {
        "schema_version": NBA_PLAYER_POINTS_CLOSING_SCHEMA_VERSION,
        "closing_batch_id": closing_batch_id,
        "operating_date": operating_date,
        "collection_timestamp_utc": _format_utc(collection_timestamp_utc),
        "writer_timestamp_utc": _format_utc(writer_timestamp_utc),
        "repository_commit_sha": repository_commit_sha,
        "research_label": config.research_label,
        "closing_policy": config.policy.to_dict(),
        "observation_count": observation_count,
        "conflict_count": conflict_count,
        "observation_file": NBA_PLAYER_POINTS_CLOSING_OBSERVATION_FILE,
        "observation_file_hash": file_hashes.get(NBA_PLAYER_POINTS_CLOSING_OBSERVATION_FILE, ""),
        "conflict_file": NBA_PLAYER_POINTS_CLOSING_CONFLICT_FILE,
        "conflict_file_hash": file_hashes.get(NBA_PLAYER_POINTS_CLOSING_CONFLICT_FILE, ""),
        "integrity_report_file": NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE,
        "integrity_report_hash": file_hashes.get(NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE, ""),
        "completion_status": completion_status,
    }
    manifest["closing_manifest_hash"] = _record_hash(manifest, "closing_manifest_hash")
    return MappingProxyType(manifest)


def _selection_manifest_payload(
    *,
    selection_batch: Mapping[str, object],
    selection_count: int,
    file_hashes: Mapping[str, str],
    completion_status: str,
    config: NBAPlayerPointsClosingWriterConfig,
) -> Mapping[str, object]:
    manifest = {
        "schema_version": NBA_PLAYER_POINTS_CLOSING_SELECTION_SCHEMA_VERSION,
        "selection_batch_id": selection_batch["selection_batch_id"],
        "operating_date": selection_batch["operating_date"],
        "collection_timestamp_utc": selection_batch["collection_timestamp_utc"],
        "writer_timestamp_utc": selection_batch["writer_timestamp_utc"],
        "repository_commit_sha": selection_batch["repository_commit_sha"],
        "research_label": config.research_label,
        "closing_policy": config.policy.to_dict(),
        "selection_count": selection_count,
        "selection_file": NBA_PLAYER_POINTS_SELECTION_FILE,
        "selection_file_hash": file_hashes.get(NBA_PLAYER_POINTS_SELECTION_FILE, ""),
        "integrity_report_file": NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE,
        "integrity_report_hash": file_hashes.get(NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE, ""),
        "completion_status": completion_status,
    }
    manifest["selection_manifest_hash"] = _record_hash(manifest, "selection_manifest_hash")
    return MappingProxyType(manifest)


def _load_prediction_index(root: Path) -> _PredictionIndex:
    evidence_report = verify_nba_player_points_evidence(
        root,
        NBAPlayerPointsEvidenceWriterConfig(),
    )
    report_dict = evidence_report.to_dict()
    if not evidence_report.ok:
        raise NBAPlayerPointsClosingError(
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
            raise NBAPlayerPointsClosingError(f"prediction ledger segment is a symlink: {ledger_path}")
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
                raise NBAPlayerPointsClosingError("unsupported prediction ledger schema")
            if row.get("operating_date") != operating_date:
                raise NBAPlayerPointsClosingError("prediction ledger operating_date path mismatch")
            if row.get("prediction_run_id") != prediction_run_id:
                raise NBAPlayerPointsClosingError("prediction ledger run path mismatch")
            if _record_hash(row, "ledger_record_hash") != row.get("ledger_record_hash"):
                raise NBAPlayerPointsClosingError("prediction ledger_record_hash mismatch")
            prediction_id = str(row["prediction_id"])
            rows_by_prediction_id[prediction_id] = MappingProxyType(
                {
                    "record": row,
                    "evidence_reference": {
                        "prediction_evidence_segment": _relative_to_root(ledger_path, root),
                        "line_number": line_number,
                    },
                }
            )
    return _PredictionIndex(
        rows_by_prediction_id=MappingProxyType(rows_by_prediction_id),
        completed_run_ids=completed_run_ids,
        integrity_report=MappingProxyType(report_dict),
    )


def _scan_closing_evidence(
    root: Path,
    config: NBAPlayerPointsClosingWriterConfig,
) -> _ExistingClosingEvidence:
    observations_by_id: dict[str, Mapping[str, object]] = {}
    conflicts_by_id: dict[str, Mapping[str, object]] = {}
    observations_by_prediction_id: dict[str, list[Mapping[str, object]]] = {}
    selection_records_by_id: dict[str, Mapping[str, object]] = {}
    violations: list[str] = []

    for segment in _iter_completed_observation_segments(root, config):
        if segment.is_symlink():
            violations.append(f"closing observation segment is a symlink: {segment}")
            continue
        try:
            rows = _read_jsonl_strict(segment / NBA_PLAYER_POINTS_CLOSING_OBSERVATION_FILE)
            conflicts = _read_jsonl_strict(segment / NBA_PLAYER_POINTS_CLOSING_CONFLICT_FILE)
        except NBAPlayerPointsClosingError as exc:
            violations.append(str(exc))
            continue
        for row in rows:
            observation_id = str(row.get("closing_observation_id"))
            if row.get("closing_record_hash") != _record_hash(row, "closing_record_hash"):
                violations.append(f"closing_record_hash mismatch: {observation_id}")
                continue
            existing = observations_by_id.get(observation_id)
            if existing is not None and existing.get("closing_record_hash") != row.get("closing_record_hash"):
                violations.append(f"duplicate conflicting closing_observation_id: {observation_id}")
                continue
            observations_by_id[observation_id] = row
            observations_by_prediction_id.setdefault(str(row["prediction_id"]), []).append(row)
        for row in conflicts:
            conflict_id = str(row.get("closing_conflict_id"))
            if row.get("closing_conflict_hash") != _record_hash(row, "closing_conflict_hash"):
                violations.append(f"closing_conflict_hash mismatch: {conflict_id}")
                continue
            conflicts_by_id[conflict_id] = row

    for segment in _iter_completed_selection_segments(root, config):
        try:
            rows = _read_jsonl_strict(segment / NBA_PLAYER_POINTS_SELECTION_FILE)
        except NBAPlayerPointsClosingError as exc:
            violations.append(str(exc))
            continue
        for row in rows:
            selection_id = str(row.get("closing_selection_id"))
            if row.get("selection_record_hash") != _record_hash(row, "selection_record_hash"):
                violations.append(f"selection_record_hash mismatch: {selection_id}")
                continue
            existing = selection_records_by_id.get(selection_id)
            if existing is not None and existing.get("selection_record_hash") != row.get("selection_record_hash"):
                violations.append(f"duplicate conflicting closing_selection_id: {selection_id}")
                continue
            selection_records_by_id[selection_id] = row

    return _ExistingClosingEvidence(
        observations_by_id=MappingProxyType(observations_by_id),
        conflicts_by_id=MappingProxyType(conflicts_by_id),
        observations_by_prediction_id=MappingProxyType(
            {key: tuple(value) for key, value in observations_by_prediction_id.items()}
        ),
        selection_records_by_id=MappingProxyType(selection_records_by_id),
        violations=tuple(violations),
    )


def _build_effective_selection_reports(
    root: Path,
    config: NBAPlayerPointsClosingWriterConfig,
) -> tuple[tuple[Mapping[str, object], ...], tuple[str, ...]]:
    observations: list[Mapping[str, object]] = []
    observation_lineage: dict[str, Mapping[str, object]] = {}
    selections: list[Mapping[str, object]] = []
    selection_lineage: dict[tuple[str, str], Mapping[str, object]] = {}
    violations: list[str] = []

    for segment in _iter_completed_observation_segments(root, config):
        manifest = _read_json_file(segment / NBA_PLAYER_POINTS_CLOSING_MANIFEST_FILE)
        for row in _read_jsonl_strict(segment / NBA_PLAYER_POINTS_CLOSING_OBSERVATION_FILE):
            observation_id = str(row["closing_observation_id"])
            observations.append(row)
            observation_lineage[observation_id] = MappingProxyType(
                {
                    "kind": "observation",
                    "segment_directory": str(segment),
                    "closing_batch_id": manifest.get("closing_batch_id"),
                    "closing_observation_id": observation_id,
                    "closing_record_hash": row.get("closing_record_hash"),
                    "manifest_hash": manifest.get("closing_manifest_hash"),
                }
            )

    for segment in _iter_completed_selection_segments(root, config):
        manifest = _read_json_file(segment / NBA_PLAYER_POINTS_SELECTION_MANIFEST_FILE)
        for row in _read_jsonl_strict(segment / NBA_PLAYER_POINTS_SELECTION_FILE):
            selection_id = str(row["closing_selection_id"])
            selection_hash = str(row["selection_record_hash"])
            selections.append(row)
            selection_lineage[(selection_id, selection_hash)] = MappingProxyType(
                {
                    "kind": "selection",
                    "segment_directory": str(segment),
                    "selection_batch_id": manifest.get("selection_batch_id"),
                    "closing_selection_id": selection_id,
                    "selection_record_hash": selection_hash,
                    "manifest_hash": manifest.get("selection_manifest_hash"),
                }
            )

    observations_by_id = {
        str(row["closing_observation_id"]): row for row in observations
    }
    logical_observations: dict[tuple[object, ...], Mapping[str, object]] = {}
    for row in observations:
        key = _observation_logical_conflict_key(row)
        prior = logical_observations.get(key)
        if prior is not None and (
            prior.get("closing_line") != row.get("closing_line")
            or prior.get("closing_american_odds") != row.get("closing_american_odds")
        ):
            violations.append(
                "same timestamp and policy has conflicting closing line or price: "
                f"{prior.get('closing_observation_id')} vs {row.get('closing_observation_id')}"
            )
        else:
            logical_observations[key] = row

    selection_identity_counts: dict[tuple[str, str], int] = {}
    for row in selections:
        identity = (
            str(row.get("closing_selection_id")),
            str(row.get("selection_record_hash")),
        )
        selection_identity_counts[identity] = selection_identity_counts.get(identity, 0) + 1
    for identity, count in selection_identity_counts.items():
        if count > 1:
            violations.append(
                "duplicate completed selection record: " + "|".join(identity)
            )

    policy_keys = {
        (
            str(row.get("prediction_id")),
            str(row.get("closing_policy_id")),
            str(row.get("closing_policy_version")),
        )
        for row in observations
    } | {
        (
            str(row.get("prediction_id")),
            str(row.get("closing_policy_id")),
            str(row.get("closing_policy_version")),
        )
        for row in selections
    }

    reports: list[Mapping[str, object]] = []
    for prediction_id, policy_id, policy_version in sorted(policy_keys):
        key_observations = tuple(
            row
            for row in observations
            if row.get("prediction_id") == prediction_id
            and row.get("closing_policy_id") == policy_id
            and row.get("closing_policy_version") == policy_version
        )
        key_selections = tuple(
            sorted(
                (
                    row
                    for row in selections
                    if row.get("prediction_id") == prediction_id
                    and row.get("closing_policy_id") == policy_id
                    and row.get("closing_policy_version") == policy_version
                ),
                key=_selection_history_sort_key,
            )
        )
        eligible = tuple(
            row
            for row in key_observations
            if row.get("observation_eligibility_status") == "eligible"
        )
        selected_observation, conflict_reason = _select_effective_observation(eligible)
        if conflict_reason:
            matching_selections = tuple(
                row
                for row in key_selections
                if row.get("selection_status") == "conflicting"
                and row.get("selection_exclusion_reason") == conflict_reason
            )
            effective_selection = (
                sorted(matching_selections, key=_selection_history_sort_key)[-1]
                if matching_selections
                else None
            )
            if effective_selection is None:
                violations.append(
                    "missing conflicting effective selection record for "
                    f"{prediction_id}|{policy_id}|{policy_version}"
                )
            reports.append(
                _effective_selection_report(
                    prediction_id=prediction_id,
                    policy_id=policy_id,
                    policy_version=policy_version,
                    selection_status="conflicting",
                    selected_observation=None,
                    effective_selection=effective_selection,
                    historical_selections=key_selections,
                    observation_lineage=observation_lineage,
                    selection_lineage=selection_lineage,
                    ordering_rule=_effective_selection_ordering_rule(),
                    conflict_reason=conflict_reason,
                )
            )
            continue
        if selected_observation is None:
            matching_selections = tuple(
                row
                for row in key_selections
                if row.get("selection_status") == "no_eligible_observation"
            )
            effective_selection = (
                sorted(matching_selections, key=_selection_history_sort_key)[-1]
                if matching_selections
                else None
            )
            if key_observations and effective_selection is None:
                violations.append(
                    "missing no-eligible effective selection record for "
                    f"{prediction_id}|{policy_id}|{policy_version}"
                )
            reports.append(
                _effective_selection_report(
                    prediction_id=prediction_id,
                    policy_id=policy_id,
                    policy_version=policy_version,
                    selection_status="no_eligible_observation",
                    selected_observation=None,
                    effective_selection=effective_selection,
                    historical_selections=key_selections,
                    observation_lineage=observation_lineage,
                    selection_lineage=selection_lineage,
                    ordering_rule=_effective_selection_ordering_rule(),
                    conflict_reason="no_eligible_observation",
                )
            )
            continue
        selected_id = str(selected_observation["closing_observation_id"])
        selected_hash = str(selected_observation["closing_record_hash"])
        if observations_by_id.get(selected_id) is None:
            violations.append(f"selected observation missing from index: {selected_id}")
        matching_selections = tuple(
            row
            for row in key_selections
            if row.get("selection_status") == "selected"
            and row.get("selected_observation_id") == selected_id
            and row.get("selected_observation_hash") == selected_hash
        )
        effective_selection = (
            sorted(matching_selections, key=_selection_history_sort_key)[-1]
            if matching_selections
            else None
        )
        if effective_selection is None:
            violations.append(
                "missing effective selected-observation record for "
                f"{prediction_id}|{policy_id}|{policy_version}"
            )
        reports.append(
            _effective_selection_report(
                prediction_id=prediction_id,
                policy_id=policy_id,
                policy_version=policy_version,
                selection_status="selected",
                selected_observation=selected_observation,
                effective_selection=effective_selection,
                historical_selections=key_selections,
                observation_lineage=observation_lineage,
                selection_lineage=selection_lineage,
                ordering_rule=_effective_selection_ordering_rule(),
                conflict_reason="none",
            )
        )

    if violations:
        return (), tuple(violations)
    return tuple(reports), ()


def _effective_selection_report(
    *,
    prediction_id: str,
    policy_id: str,
    policy_version: str,
    selection_status: str,
    selected_observation: Mapping[str, object] | None,
    effective_selection: Mapping[str, object] | None,
    historical_selections: Sequence[Mapping[str, object]],
    observation_lineage: Mapping[str, Mapping[str, object]],
    selection_lineage: Mapping[tuple[str, str], Mapping[str, object]],
    ordering_rule: str,
    conflict_reason: str,
) -> Mapping[str, object]:
    lineage: list[Mapping[str, object]] = []
    if selected_observation is not None:
        observation_entry = observation_lineage.get(
            str(selected_observation["closing_observation_id"])
        )
        if observation_entry is not None:
            lineage.append(observation_entry)
    if effective_selection is not None:
        selection_entry = selection_lineage.get(
            (
                str(effective_selection["closing_selection_id"]),
                str(effective_selection["selection_record_hash"]),
            )
        )
        if selection_entry is not None:
            lineage.append(selection_entry)
    return MappingProxyType(
        {
            "prediction_id": prediction_id,
            "closing_policy_id": policy_id,
            "closing_policy_version": policy_version,
            "selection_status": selection_status,
            "selection_exclusion_reason": conflict_reason,
            "selected_observation_id": (
                selected_observation.get("closing_observation_id")
                if selected_observation is not None
                else None
            ),
            "selected_observation_hash": (
                selected_observation.get("closing_record_hash")
                if selected_observation is not None
                else None
            ),
            "selected_observation": (
                _json_ready(selected_observation)
                if selected_observation is not None
                else None
            ),
            "effective_selection_record": (
                _json_ready(effective_selection)
                if effective_selection is not None
                else None
            ),
            "historical_selection_count": len(historical_selections),
            "historical_selection_records": [
                _json_ready(row) for row in historical_selections
            ],
            "evidence_lineage": [_json_ready(item) for item in lineage],
            "ordering_rule": ordering_rule,
        }
    )


def _selection_history_sort_key(row: Mapping[str, object]) -> tuple[datetime, str, str]:
    return (
        _coerce_utc_datetime(row["selected_at_utc"], "selected_at_utc"),
        str(row.get("selection_record_hash", "")),
        str(row.get("closing_selection_id", "")),
    )


def _effective_selection_ordering_rule() -> str:
    return (
        "latest eligible observation_timestamp_utc wins; same-timestamp candidates "
        "with matching closing_line and closing_american_odds tie by closing_record_hash; "
        "same-timestamp candidates with conflicting line or price fail closed"
    )


def _verify_observation_segment(
    segment: Path,
    config: NBAPlayerPointsClosingWriterConfig,
    prediction_index: _PredictionIndex,
) -> Mapping[str, object]:
    violations: list[str] = []
    if segment.is_symlink():
        violations.append(f"closing observation segment is a symlink: {segment}")
    manifest_path = segment / NBA_PLAYER_POINTS_CLOSING_MANIFEST_FILE
    marker_path = segment / config.completion_marker_file_name
    for path in (
        manifest_path,
        marker_path,
        segment / NBA_PLAYER_POINTS_CLOSING_OBSERVATION_FILE,
        segment / NBA_PLAYER_POINTS_CLOSING_CONFLICT_FILE,
        segment / NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE,
    ):
        if path.is_symlink():
            violations.append(f"closing observation file is a symlink: {path}")
        if not path.exists():
            violations.append(f"closing observation expected file missing: {path.name}")
    manifest: Mapping[str, object] = MappingProxyType({})
    marker: Mapping[str, object] = MappingProxyType({})
    rows: tuple[Mapping[str, object], ...] = ()
    conflicts: tuple[Mapping[str, object], ...] = ()
    if not violations:
        try:
            manifest = _read_json_file(manifest_path)
            marker = _read_json_file(marker_path)
            rows = _read_jsonl_strict(segment / NBA_PLAYER_POINTS_CLOSING_OBSERVATION_FILE)
            conflicts = _read_jsonl_strict(segment / NBA_PLAYER_POINTS_CLOSING_CONFLICT_FILE)
        except NBAPlayerPointsClosingError as exc:
            violations.append(str(exc))
    if manifest:
        if manifest.get("schema_version") != NBA_PLAYER_POINTS_CLOSING_SCHEMA_VERSION:
            violations.append("closing manifest schema_version mismatch")
        if manifest.get("closing_manifest_hash") != _record_hash(
            manifest,
            "closing_manifest_hash",
        ):
            violations.append("closing_manifest_hash mismatch")
        if marker.get("manifest_hash") != _sha256_file(manifest_path):
            violations.append("closing completion marker manifest_hash mismatch")
        expected_hashes = {
            NBA_PLAYER_POINTS_CLOSING_OBSERVATION_FILE: manifest.get("observation_file_hash"),
            NBA_PLAYER_POINTS_CLOSING_CONFLICT_FILE: manifest.get("conflict_file_hash"),
            NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE: manifest.get("integrity_report_hash"),
        }
        for filename, expected_hash in expected_hashes.items():
            path = segment / filename
            if path.exists() and expected_hash != _sha256_file(path):
                violations.append(f"{filename} hash mismatch")
        if int(manifest.get("observation_count", -1)) != len(rows):
            violations.append("closing manifest observation_count mismatch")
        if int(manifest.get("conflict_count", -1)) != len(conflicts):
            violations.append("closing manifest conflict_count mismatch")
    for line_number, row in enumerate(rows, start=1):
        violations.extend(
            _validate_closing_observation_record(
                row,
                prediction_index,
                segment=segment,
                line_number=line_number,
            )
        )
    for line_number, conflict in enumerate(conflicts, start=1):
        if conflict.get("closing_conflict_hash") != _record_hash(
            conflict,
            "closing_conflict_hash",
        ):
            violations.append(f"{segment}:{line_number}: closing_conflict_hash mismatch")
    return MappingProxyType(
        {
            "segment_directory": str(segment),
            "manifest": _json_ready(manifest),
            "observation_count": len(rows),
            "conflict_count": len(conflicts),
            "violations": tuple(violations),
        }
    )


def _verify_selection_segment(
    segment: Path,
    config: NBAPlayerPointsClosingWriterConfig,
    observations_by_id: Mapping[str, Mapping[str, object]],
) -> Mapping[str, object]:
    violations: list[str] = []
    manifest_path = segment / NBA_PLAYER_POINTS_SELECTION_MANIFEST_FILE
    marker_path = segment / config.completion_marker_file_name
    for path in (
        manifest_path,
        marker_path,
        segment / NBA_PLAYER_POINTS_SELECTION_FILE,
        segment / NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE,
    ):
        if path.is_symlink():
            violations.append(f"closing selection file is a symlink: {path}")
        if not path.exists():
            violations.append(f"closing selection expected file missing: {path.name}")
    manifest: Mapping[str, object] = MappingProxyType({})
    rows: tuple[Mapping[str, object], ...] = ()
    marker: Mapping[str, object] = MappingProxyType({})
    if not violations:
        try:
            manifest = _read_json_file(manifest_path)
            marker = _read_json_file(marker_path)
            rows = _read_jsonl_strict(segment / NBA_PLAYER_POINTS_SELECTION_FILE)
        except NBAPlayerPointsClosingError as exc:
            violations.append(str(exc))
    if manifest:
        if manifest.get("schema_version") != NBA_PLAYER_POINTS_CLOSING_SELECTION_SCHEMA_VERSION:
            violations.append("selection manifest schema_version mismatch")
        if manifest.get("selection_manifest_hash") != _record_hash(
            manifest,
            "selection_manifest_hash",
        ):
            violations.append("selection_manifest_hash mismatch")
        if marker.get("manifest_hash") != _sha256_file(manifest_path):
            violations.append("selection completion marker manifest_hash mismatch")
        if manifest.get("selection_file_hash") != _sha256_file(
            segment / NBA_PLAYER_POINTS_SELECTION_FILE
        ):
            violations.append("selected_closing_rows.jsonl hash mismatch")
        if manifest.get("integrity_report_hash") != _sha256_file(
            segment / NBA_PLAYER_POINTS_INTEGRITY_REPORT_FILE
        ):
            violations.append("selection integrity_report.json hash mismatch")
        if int(manifest.get("selection_count", -1)) != len(rows):
            violations.append("selection manifest selection_count mismatch")
    for line_number, row in enumerate(rows, start=1):
        violations.extend(
            _validate_selection_record(
                row,
                observations_by_id,
                segment=segment,
                line_number=line_number,
            )
        )
    return MappingProxyType(
        {
            "segment_directory": str(segment),
            "manifest": _json_ready(manifest),
            "selection_count": len(rows),
            "violations": tuple(violations),
        }
    )


def _validate_closing_observation_record(
    row: Mapping[str, object],
    prediction_index: _PredictionIndex,
    *,
    segment: Path,
    line_number: int,
) -> tuple[str, ...]:
    violations: list[str] = []
    missing = [field for field in _closing_observation_field_names() if field not in row]
    if missing:
        violations.append(
            f"{segment}:{line_number}: closing observation missing fields: {','.join(missing)}"
        )
        return tuple(violations)
    if row.get("schema_version") != NBA_PLAYER_POINTS_CLOSING_SCHEMA_VERSION:
        violations.append(f"{segment}:{line_number}: unsupported closing schema_version")
    if row.get("observation_eligibility_status") not in NBA_PLAYER_POINTS_CLOSING_STATUSES:
        violations.append(f"{segment}:{line_number}: unsupported closing status")
    if row.get("closing_record_hash") != _record_hash(row, "closing_record_hash"):
        violations.append(f"{segment}:{line_number}: closing_record_hash mismatch")
    if row.get("closing_observation_id") != _closing_observation_id(row):
        violations.append(f"{segment}:{line_number}: closing_observation_id mismatch")
    if _contains_prohibited_field(row):
        violations.append(f"{segment}:{line_number}: prohibited settlement or bankroll field")
    try:
        observation_time = _coerce_utc_datetime(
            row["observation_timestamp_utc"],
            "observation_timestamp_utc",
        )
        source_time = _coerce_utc_datetime(
            row["source_market_update_timestamp_utc"],
            "source_market_update_timestamp_utc",
        )
        commence_time = _coerce_utc_datetime(row["commence_time_utc"], "commence_time_utc")
        if source_time > observation_time:
            violations.append(
                f"{segment}:{line_number}: source timestamp is after observation timestamp"
            )
        if row.get("observation_eligibility_status") == "eligible" and observation_time >= commence_time:
            violations.append(f"{segment}:{line_number}: post-tip observation marked eligible")
    except NBAPlayerPointsClosingError as exc:
        violations.append(f"{segment}:{line_number}: {exc}")
    prediction_entry = prediction_index.rows_by_prediction_id.get(str(row["prediction_id"]))
    prediction_row = prediction_entry.get("record") if isinstance(prediction_entry, Mapping) else None
    if row.get("observation_eligibility_status") == "eligible":
        if not isinstance(prediction_row, Mapping):
            violations.append(f"{segment}:{line_number}: eligible observation missing prediction")
        elif prediction_row.get("ledger_record_hash") != row.get("prediction_record_hash"):
            violations.append(f"{segment}:{line_number}: prediction_record_hash mismatch")
        elif prediction_row.get("sportsbook") != row.get("sportsbook"):
            violations.append(f"{segment}:{line_number}: eligible observation wrong sportsbook")
        elif prediction_row.get("market") != row.get("market"):
            violations.append(f"{segment}:{line_number}: eligible observation wrong market")
        elif prediction_row.get("canonical_event_id") != row.get("canonical_event_id"):
            violations.append(f"{segment}:{line_number}: eligible observation wrong event")
        elif prediction_row.get("player_id") != row.get("player_id"):
            violations.append(f"{segment}:{line_number}: eligible observation wrong player")
    return tuple(violations)


def _validate_selection_record(
    row: Mapping[str, object],
    observations_by_id: Mapping[str, Mapping[str, object]],
    *,
    segment: Path,
    line_number: int,
) -> tuple[str, ...]:
    violations: list[str] = []
    missing = [field for field in _closing_selection_field_names() if field not in row]
    if missing:
        violations.append(f"{segment}:{line_number}: selection missing fields: {','.join(missing)}")
        return tuple(violations)
    if row.get("schema_version") != NBA_PLAYER_POINTS_CLOSING_SELECTION_SCHEMA_VERSION:
        violations.append(f"{segment}:{line_number}: unsupported selection schema_version")
    if row.get("selection_status") not in NBA_PLAYER_POINTS_CLOSING_SELECTION_STATUSES:
        violations.append(f"{segment}:{line_number}: unsupported selection_status")
    if row.get("selection_record_hash") != _record_hash(row, "selection_record_hash"):
        violations.append(f"{segment}:{line_number}: selection_record_hash mismatch")
    if row.get("closing_selection_id") != _closing_selection_id(row):
        violations.append(f"{segment}:{line_number}: closing_selection_id mismatch")
    if _contains_prohibited_field(row):
        violations.append(f"{segment}:{line_number}: prohibited settlement or bankroll field")
    try:
        _coerce_utc_datetime(row["selected_at_utc"], "selected_at_utc")
    except NBAPlayerPointsClosingError as exc:
        violations.append(f"{segment}:{line_number}: {exc}")
    if row.get("selection_status") == "selected":
        selected_id = str(row["selected_observation_id"])
        observation = observations_by_id.get(selected_id)
        if observation is None:
            violations.append(f"{segment}:{line_number}: selected observation missing")
        elif observation.get("closing_record_hash") != row.get("selected_observation_hash"):
            violations.append(f"{segment}:{line_number}: selected observation hash mismatch")
        elif observation.get("prediction_id") != row.get("prediction_id"):
            violations.append(f"{segment}:{line_number}: selected observation wrong prediction")
        elif observation.get("prediction_run_id") != row.get("prediction_run_id"):
            violations.append(f"{segment}:{line_number}: selected observation wrong prediction run")
        elif observation.get("closing_policy_id") != row.get("closing_policy_id"):
            violations.append(f"{segment}:{line_number}: selected observation wrong policy id")
        elif observation.get("closing_policy_version") != row.get("closing_policy_version"):
            violations.append(f"{segment}:{line_number}: selected observation wrong policy version")
        elif observation.get("observation_eligibility_status") != "eligible":
            violations.append(f"{segment}:{line_number}: selected observation not eligible")
        else:
            observation_time = _coerce_utc_datetime(
                observation["observation_timestamp_utc"],
                "observation_timestamp_utc",
            )
            commence_time = _coerce_utc_datetime(
                observation["commence_time_utc"],
                "commence_time_utc",
            )
            if observation_time >= commence_time:
                violations.append(f"{segment}:{line_number}: post-tip observation selected")
    return tuple(violations)


def _valid_observation_index_from_segments(
    segment_reports: Sequence[Mapping[str, object]]
) -> Mapping[str, Mapping[str, object]]:
    observations: dict[str, Mapping[str, object]] = {}
    for report in segment_reports:
        if report.get("violations"):
            continue
        segment = Path(str(report["segment_directory"]))
        for row in _read_jsonl_strict(segment / NBA_PLAYER_POINTS_CLOSING_OBSERVATION_FILE):
            observations[str(row["closing_observation_id"])] = row
    return MappingProxyType(observations)


def _read_existing_completed_closing_manifest(
    segment_dir: Path,
    config: NBAPlayerPointsClosingWriterConfig,
) -> Mapping[str, object]:
    marker_path = segment_dir / config.completion_marker_file_name
    manifest_path = segment_dir / NBA_PLAYER_POINTS_CLOSING_MANIFEST_FILE
    if not marker_path.exists():
        raise NBAPlayerPointsClosingError("closing segment exists without completion marker")
    manifest = _read_json_file(manifest_path)
    marker = _read_json_file(marker_path)
    if marker.get("manifest_hash") != _sha256_file(manifest_path):
        raise NBAPlayerPointsClosingError("existing closing segment manifest hash mismatch")
    return manifest


def _read_existing_completed_selection_manifest(
    segment_dir: Path,
    config: NBAPlayerPointsClosingWriterConfig,
) -> Mapping[str, object]:
    marker_path = segment_dir / config.completion_marker_file_name
    manifest_path = segment_dir / NBA_PLAYER_POINTS_SELECTION_MANIFEST_FILE
    if not marker_path.exists():
        raise NBAPlayerPointsClosingError("selection segment exists without completion marker")
    manifest = _read_json_file(manifest_path)
    marker = _read_json_file(marker_path)
    if marker.get("manifest_hash") != _sha256_file(manifest_path):
        raise NBAPlayerPointsClosingError("existing selection segment manifest hash mismatch")
    return manifest


def _iter_completed_observation_segments(
    root: Path,
    config: NBAPlayerPointsClosingWriterConfig,
) -> tuple[Path, ...]:
    segments_root = (
        root
        / config.closing_dir_name
        / config.observations_dir_name
        / config.segments_dir_name
    )
    if not segments_root.exists():
        return ()
    return tuple(
        sorted(
            path
            for path in segments_root.glob("*/*")
            if path.is_dir() and not path.name.startswith(".")
        )
    )


def _iter_completed_selection_segments(
    root: Path,
    config: NBAPlayerPointsClosingWriterConfig,
) -> tuple[Path, ...]:
    segments_root = (
        root
        / config.closing_dir_name
        / config.selections_dir_name
        / config.segments_dir_name
    )
    if not segments_root.exists():
        return ()
    return tuple(
        sorted(
            path
            for path in segments_root.glob("*/*")
            if path.is_dir() and not path.name.startswith(".")
        )
    )


def _observation_segment_directory(
    root: Path,
    operating_date: str,
    closing_batch_id: str,
    config: NBAPlayerPointsClosingWriterConfig,
) -> Path:
    return (
        root
        / config.closing_dir_name
        / config.observations_dir_name
        / config.segments_dir_name
        / _require_operating_date(operating_date, "operating_date")
        / _require_safe_path_component(closing_batch_id, "closing_batch_id")
    )


def _selection_segment_directory(
    root: Path,
    operating_date: str,
    selection_batch_id: str,
    config: NBAPlayerPointsClosingWriterConfig,
) -> Path:
    return (
        root
        / config.closing_dir_name
        / config.selections_dir_name
        / config.segments_dir_name
        / _require_operating_date(operating_date, "operating_date")
        / _require_safe_path_component(selection_batch_id, "selection_batch_id")
    )


def _closing_observation_id(payload: Mapping[str, object]) -> str:
    values = {
        key: payload.get(key)
        for key in (
            "schema_version",
            "prediction_id",
            "prediction_run_id",
            "canonical_event_id",
            "provider_event_id",
            "player_id",
            "sportsbook",
            "market",
            "closing_line",
            "closing_american_odds",
            "observation_timestamp_utc",
            "source_market_update_timestamp_utc",
            "closing_provider",
            "closing_source_id",
            "closing_source_hash",
            "closing_policy_id",
            "closing_policy_version",
        )
    }
    return "nba-close-obs-" + _canonical_payload_sha256(values)[:32]


def _closing_batch_id(
    *,
    observations: Sequence[Mapping[str, object]],
    conflicts: Sequence[Mapping[str, object]],
    operating_date: str,
    collection_timestamp_utc: datetime,
    policy: NBAPlayerPointsClosingPolicy,
) -> str:
    payload = {
        "schema_version": NBA_PLAYER_POINTS_CLOSING_SCHEMA_VERSION,
        "operating_date": operating_date,
        "collection_timestamp_utc": _format_utc(collection_timestamp_utc),
        "policy": policy.to_dict(),
        "observation_ids": [row["closing_observation_id"] for row in observations],
        "observation_hashes": [row["closing_record_hash"] for row in observations],
        "conflict_hashes": [row["closing_conflict_hash"] for row in conflicts],
    }
    return "nba-close-batch-" + _canonical_payload_sha256(payload)[:32]


def _closing_selection_id(payload: Mapping[str, object]) -> str:
    values = {
        key: payload.get(key)
        for key in (
            "schema_version",
            "prediction_id",
            "prediction_run_id",
            "selected_observation_id",
            "selected_observation_hash",
            "closing_policy_id",
            "closing_policy_version",
            "selected_at_utc",
            "selection_status",
            "selection_exclusion_reason",
        )
    }
    return "nba-close-selection-" + _canonical_payload_sha256(values)[:32]


def _selection_batch_id(
    *,
    records: Sequence[Mapping[str, object]],
    operating_date: str,
    collection_timestamp_utc: datetime,
    policy: NBAPlayerPointsClosingPolicy,
) -> str:
    payload = {
        "schema_version": NBA_PLAYER_POINTS_CLOSING_SELECTION_SCHEMA_VERSION,
        "operating_date": operating_date,
        "collection_timestamp_utc": _format_utc(collection_timestamp_utc),
        "policy": policy.to_dict(),
        "selection_ids": [row["closing_selection_id"] for row in records],
        "selection_hashes": [row["selection_record_hash"] for row in records],
    }
    return "nba-close-sel-batch-" + _canonical_payload_sha256(payload)[:32]


def _conflict_record(
    row: Mapping[str, object],
    conflict_scope: str,
    reason: str,
    existing: Mapping[str, object],
) -> Mapping[str, object]:
    payload = {
        "schema_version": NBA_PLAYER_POINTS_CLOSING_SCHEMA_VERSION,
        "closing_conflict_id": "",
        "conflict_scope": _require_text(conflict_scope, "conflict_scope"),
        "reason": _require_text(reason, "reason"),
        "closing_observation_id": row.get("closing_observation_id"),
        "closing_record_hash": row.get("closing_record_hash"),
        "conflicting_record": _json_ready(row),
        "existing_closing_observation_id": existing.get("closing_observation_id"),
        "existing_closing_record_hash": existing.get("closing_record_hash"),
    }
    payload["closing_conflict_id"] = "nba-close-conflict-" + _canonical_payload_sha256(
        {
            key: value
            for key, value in payload.items()
            if key not in {"closing_conflict_id", "closing_conflict_hash"}
        }
    )[:32]
    payload["closing_conflict_hash"] = _record_hash(payload, "closing_conflict_hash")
    return MappingProxyType(payload)


def _dedupe_conflicts(
    conflicts: Sequence[Mapping[str, object]]
) -> tuple[Mapping[str, object], ...]:
    deduped: dict[str, Mapping[str, object]] = {}
    for conflict in conflicts:
        deduped[str(conflict["closing_conflict_id"])] = conflict
    return tuple(deduped.values())


def _observation_logical_conflict_key(row: Mapping[str, object]) -> tuple[object, ...]:
    return (
        row.get("prediction_id"),
        row.get("prediction_run_id"),
        row.get("canonical_event_id"),
        row.get("player_id"),
        row.get("sportsbook"),
        row.get("market"),
        row.get("closing_policy_id"),
        row.get("closing_policy_version"),
        row.get("observation_timestamp_utc"),
        row.get("source_market_update_timestamp_utc"),
        row.get("closing_provider"),
        row.get("closing_source_id"),
    )


def _line_match_status(closing_line: object, prediction_line: object) -> str:
    if closing_line is None or prediction_line is None:
        return "missing"
    return "match" if float(closing_line) == float(prediction_line) else "moved"


def _match_status(value: object, expected: object) -> str:
    if expected in (None, ""):
        return "unknown"
    return "match" if value == expected else "mismatch"


def _closing_observation_field_names() -> tuple[str, ...]:
    return (
        "closing_observation_id",
        "prediction_id",
        "prediction_run_id",
        "prediction_evidence_segment",
        "prediction_record_hash",
        "canonical_event_id",
        "provider_event_id",
        "player_id",
        "sportsbook",
        "market",
        "operating_date",
        "commence_time_utc",
        "closing_line",
        "closing_american_odds",
        "closing_decimal_odds",
        "closing_implied_probability",
        "closing_market_status",
        "observation_timestamp_utc",
        "source_market_update_timestamp_utc",
        "seconds_before_tipoff",
        "closing_policy_id",
        "closing_policy_version",
        "closing_window_start_seconds",
        "closing_window_end_seconds",
        "same_book_required",
        "same_market_required",
        "original_prediction_line",
        "original_prediction_american_odds",
        "line_match_status",
        "book_match_status",
        "market_match_status",
        "event_match_status",
        "player_match_status",
        "observation_eligibility_status",
        "exclusion_reason",
        "closing_provider",
        "closing_source_id",
        "closing_source_hash",
        "collection_timestamp_utc",
        "schema_version",
        "repository_commit_sha",
        "writer_timestamp_utc",
        "closing_record_hash",
        "research_label",
    )


def _closing_selection_field_names() -> tuple[str, ...]:
    return (
        "closing_selection_id",
        "schema_version",
        "prediction_id",
        "prediction_run_id",
        "selected_observation_id",
        "selected_observation_hash",
        "closing_policy_id",
        "closing_policy_version",
        "selected_at_utc",
        "closing_line",
        "closing_american_odds",
        "closing_decimal_odds",
        "closing_implied_probability",
        "original_prediction_line",
        "original_prediction_american_odds",
        "line_movement",
        "price_movement",
        "selection_status",
        "selection_exclusion_reason",
        "repository_commit_sha",
        "selection_record_hash",
        "research_label",
    )


def _validate_config(config: NBAPlayerPointsClosingWriterConfig) -> None:
    if not isinstance(config, NBAPlayerPointsClosingWriterConfig):
        raise TypeError("config must be NBAPlayerPointsClosingWriterConfig")


class _ClosingRootLock:
    def __init__(self, evidence_root: Path, config: NBAPlayerPointsClosingWriterConfig) -> None:
        self._evidence_root = evidence_root
        self._config = config
        self._lock_path = evidence_root / NBA_PLAYER_POINTS_CLOSING_LOCK_FILE
        self._fd: int | None = None

    def __enter__(self) -> "_ClosingRootLock":
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
                    raise NBAPlayerPointsClosingError(
                        f"closing writer lock is already held: {self._lock_path}"
                    ) from exc
                time.sleep(0.01)
            except OSError as exc:
                raise NBAPlayerPointsClosingError(
                    f"unable to acquire closing writer lock: {self._lock_path}"
                ) from exc

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            pass


def _evidence_root(path: Path, config: NBAPlayerPointsClosingWriterConfig) -> Path:
    base = path.expanduser()
    evidence_root = base if base.name == config.evidence_dir_name else base / config.evidence_dir_name
    if evidence_root.name != config.evidence_dir_name:
        raise NBAPlayerPointsClosingError("evidence root name mismatch")
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
                raise NBAPlayerPointsClosingError(f"path component is a symlink: {probe}")
        except OSError as exc:
            raise NBAPlayerPointsClosingError(f"unable to inspect path: {probe}") from exc


def _ensure_under_root(root: Path, path: Path, field_name: str) -> None:
    root_resolved = root.resolve(strict=False)
    path_resolved = path.resolve(strict=False)
    if path_resolved == root_resolved:
        return
    if root_resolved not in path_resolved.parents:
        raise NBAPlayerPointsClosingError(f"{field_name} escapes evidence root")


def _relative_to_root(path: Path, root: Path) -> str:
    _ensure_under_root(root, path, "path")
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise NBAPlayerPointsClosingError("path escapes evidence root") from exc


def _write_bytes_verified(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    if path.read_bytes() != data:
        raise NBAPlayerPointsClosingError(f"short write detected: {path}")


def _write_json_file(path: Path, payload: Mapping[str, object]) -> None:
    path.write_bytes(_json_file_bytes(payload))


def _read_json_file(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NBAPlayerPointsClosingError(f"invalid JSON file: {path}") from exc
    if not isinstance(payload, Mapping):
        raise NBAPlayerPointsClosingError(f"JSON file must contain an object: {path}")
    return MappingProxyType(_json_clone_mapping(payload))


def _read_jsonl_strict(path: Path) -> tuple[Mapping[str, object], ...]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise NBAPlayerPointsClosingError(f"unable to read JSONL file: {path}") from exc
    if not data:
        return ()
    if not data.endswith(b"\n"):
        raise NBAPlayerPointsClosingError(f"JSONL frame missing final newline: {path}")
    rows: list[Mapping[str, object]] = []
    for line_number, raw_line in enumerate(data.splitlines(), start=1):
        if not raw_line.strip():
            raise NBAPlayerPointsClosingError(f"empty JSONL line at {path}:{line_number}")
        try:
            payload = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NBAPlayerPointsClosingError(
                f"invalid JSONL at {path}:{line_number}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise NBAPlayerPointsClosingError(
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
        raise NBAPlayerPointsClosingError("canonical JSON cannot contain NaN or infinity") from exc


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
            raise NBAPlayerPointsClosingError("numeric values must be finite")
        return value
    return value


def _json_clone_mapping(value: Mapping[str, object]) -> dict[str, object]:
    cloned = json.loads(
        json.dumps(_json_ready(value), sort_keys=True, ensure_ascii=True, allow_nan=False)
    )
    if not isinstance(cloned, dict):
        raise NBAPlayerPointsClosingError("value must be an object")
    return cloned


def _canonical_sort_payloads(
    payloads: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        sorted(
            (MappingProxyType(_json_clone_mapping(payload)) for payload in payloads),
            key=lambda item: (
                str(item.get("prediction_id", "")),
                str(item.get("observation_timestamp_utc", "")),
                str(item.get("closing_observation_id", "")),
                str(item.get("closing_record_hash", "")),
                _stable_json_bytes(item),
            ),
        )
    )


def _coerce_utc_datetime(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise NBAPlayerPointsClosingError(
                f"{field_name} must be an ISO-8601 UTC timestamp"
            ) from exc
    else:
        raise NBAPlayerPointsClosingError(
            f"{field_name} must be an ISO-8601 UTC timestamp"
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NBAPlayerPointsClosingError(f"{field_name} must be timezone-aware")
    if parsed.utcoffset() != timedelta(0):
        raise NBAPlayerPointsClosingError(f"{field_name} must be UTC")
    return parsed.astimezone(_UTC)


def _format_utc(value: datetime) -> str:
    return _coerce_utc_datetime(value, "timestamp").isoformat().replace("+00:00", "Z")


def _resolve_toronto_timezone() -> ZoneInfo | object:
    try:
        return ZoneInfo(NBA_PLAYER_POINTS_OPERATING_TIMEZONE)
    except ZoneInfoNotFoundError:
        return _TORONTO_FALLBACK


def _toronto_operating_date(value: datetime) -> str:
    utc_value = _coerce_utc_datetime(value, "commence_time_utc")
    timezone_info = _resolve_toronto_timezone()
    if timezone_info is not _TORONTO_FALLBACK:
        return utc_value.astimezone(timezone_info).date().isoformat()
    offset = timezone(timedelta(hours=-4), "EDT")
    return utc_value.astimezone(offset).date().isoformat()


def _normalize_market(value: object) -> str:
    text = _require_text(value, "market").casefold().strip()
    normalized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return normalized


def _require_operating_date(value: object, field_name: str) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        text = value.isoformat()
    else:
        text = _require_text(value, field_name)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) is None:
        raise NBAPlayerPointsClosingError(f"{field_name} must use strict YYYY-MM-DD format")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise NBAPlayerPointsClosingError(f"{field_name} must be a valid date") from exc
    if parsed.isoformat() != text:
        raise NBAPlayerPointsClosingError(f"{field_name} must use strict YYYY-MM-DD format")
    return text


def _require_safe_path_component(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if text in {".", ".."} or ".." in text:
        raise NBAPlayerPointsClosingError(f"{field_name} must not contain '..'")
    if "/" in text or "\\" in text:
        raise NBAPlayerPointsClosingError(f"{field_name} must not contain path separators")
    if Path(text).is_absolute():
        raise NBAPlayerPointsClosingError(f"{field_name} must not be absolute")
    return text


def _require_relative_path(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).replace("\\", "/")
    if Path(text).is_absolute():
        raise NBAPlayerPointsClosingError(f"{field_name} must be relative")
    parts = [part for part in text.split("/") if part]
    if not parts or any(part in {".", ".."} or ".." in part for part in parts):
        raise NBAPlayerPointsClosingError(f"{field_name} must not traverse directories")
    return "/".join(parts)


def _require_identifier(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if text == "0":
        raise NBAPlayerPointsClosingError(f"{field_name} is required")
    return text


def _require_text(value: object, field_name: str) -> str:
    if value is None:
        raise NBAPlayerPointsClosingError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise NBAPlayerPointsClosingError(f"{field_name} is required")
    return text


def _require_sha256(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold()
    if _SHA256_RE.fullmatch(text) is None:
        raise NBAPlayerPointsClosingError(f"{field_name} must be lowercase SHA-256")
    return text


def _require_commit_sha(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold()
    if _COMMIT_SHA_RE.fullmatch(text) is None:
        raise NBAPlayerPointsClosingError(
            f"{field_name} must be a 7-40 character lowercase git SHA"
        )
    return text


def _optional_nonnegative_number(value: object, field_name: str) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NBAPlayerPointsClosingError(f"{field_name} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise NBAPlayerPointsClosingError(f"{field_name} must be finite")
    if parsed < 0:
        raise NBAPlayerPointsClosingError(f"{field_name} must be non-negative")
    return parsed


def _optional_american_odds(value: object, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise NBAPlayerPointsClosingError(f"{field_name} must be an integer")
    if value == 0:
        raise NBAPlayerPointsClosingError(f"{field_name} cannot be 0")
    return value


def _contains_prohibited_field(payload: object) -> bool:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            text = str(key).casefold()
            if any(fragment in text for fragment in _PROHIBITED_FIELD_FRAGMENTS):
                return True
            if _contains_prohibited_field(value):
                return True
    elif isinstance(payload, list | tuple):
        return any(_contains_prohibited_field(item) for item in payload)
    return False


def _validate_no_prohibited_fields(payload: Mapping[str, object]) -> None:
    if _contains_prohibited_field(payload):
        raise NBAPlayerPointsClosingError("closing evidence contains prohibited field")


def _call_failure_hook(failure_hook: FailureHook | None, stage: str) -> None:
    if failure_hook is not None:
        failure_hook(stage)


__all__ = [
    "NBA_PLAYER_POINTS_CLOSING_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_CLOSING_SELECTION_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_CLOSING_STATUSES",
    "NBAPlayerPointsClosingError",
    "NBAPlayerPointsClosingIntegrityReport",
    "NBAPlayerPointsClosingObservationInput",
    "NBAPlayerPointsClosingPolicy",
    "NBAPlayerPointsClosingWriteResult",
    "NBAPlayerPointsClosingWriterConfig",
    "NBAPlayerPointsPredictionReference",
    "closing_observation_schema_definition",
    "closing_selection_schema_definition",
    "default_closing_policy",
    "resolve_nba_player_points_effective_closing_selection",
    "verify_nba_player_points_closing_evidence",
    "write_nba_player_points_closing_evidence",
]
