"""Immutable, sport-agnostic contract for non-production research artifacts.

This module is a serialization boundary only.  It does not promote research
outputs, approve betting use, size wagers, select providers, or alter scoring.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
import json
import math
from pathlib import Path
from typing import Final, Sequence


RESEARCH_ARTIFACT_SCHEMA_VERSION: Final = "1.0"
NOT_APPROVED: Final = "not_approved"
SUPPORTED_ARTIFACT_MODES: Final = frozenset({"research", "sample", "historical"})
SUPPORTED_ARTIFACT_TYPES: Final = frozenset(
    {"watchlist", "report", "dataset", "diagnostic", "backtest"}
)


class ResearchArtifactValidationError(ValueError):
    """Raised when an artifact cannot cross a serialization boundary safely."""


def _tuple_of_text(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} must be a sequence of strings")
    normalized = tuple(values)
    if any(not isinstance(value, str) for value in normalized):
        raise TypeError(f"{field_name} must contain only strings")
    return normalized


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True, slots=True)
class ResearchArtifactMetadata:
    """Artifact-level identity, provenance, and explicit safety state."""

    artifact_id: str
    sport: str
    league: str
    market_type: str
    mode: str
    artifact_type: str
    run_date: date
    generated_at: datetime
    provider_names: tuple[str, ...]
    source_types: tuple[str, ...]
    code_version: str | None = None
    data_version: str | None = None
    schema_version: str = RESEARCH_ARTIFACT_SCHEMA_VERSION
    approval_status: str = NOT_APPROVED
    eligible_for_betting: bool = False
    kelly_eligible: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "provider_names", _tuple_of_text(self.provider_names, "provider_names")
        )
        object.__setattr__(
            self, "source_types", _tuple_of_text(self.source_types, "source_types")
        )


@dataclass(frozen=True, slots=True)
class ResearchArtifactRow:
    """One provider-neutral row in a non-production research artifact."""

    row_id: str
    sport: str
    league: str
    market_type: str
    status: str
    data_quality: str
    mode: str
    player_name: str | None = None
    player_id: str | None = None
    team: str | None = None
    opponent: str | None = None
    event_id: str | None = None
    event_date: date | datetime | None = None
    research_score: float | int | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    source_refs: tuple[str, ...] = field(default_factory=tuple)
    approval_status: str = NOT_APPROVED
    eligible_for_betting: bool = False
    kelly_eligible: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", _tuple_of_text(self.reasons, "reasons"))
        object.__setattr__(self, "warnings", _tuple_of_text(self.warnings, "warnings"))
        object.__setattr__(
            self, "source_refs", _tuple_of_text(self.source_refs, "source_refs")
        )

    def to_dict(self) -> dict[str, object]:
        """Return the stable row schema used by JSON and CSV exports."""

        return {
            "row_id": self.row_id,
            "sport": self.sport,
            "league": self.league,
            "player_name": self.player_name,
            "player_id": self.player_id,
            "team": self.team,
            "opponent": self.opponent,
            "event_id": self.event_id,
            "event_date": _iso(self.event_date),
            "market_type": self.market_type,
            "research_score": self.research_score,
            "status": self.status,
            "data_quality": self.data_quality,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "source_refs": list(self.source_refs),
            "mode": self.mode,
            "approval_status": self.approval_status,
            "eligible_for_betting": self.eligible_for_betting,
            "kelly_eligible": self.kelly_eligible,
        }


@dataclass(frozen=True, slots=True)
class ResearchArtifactValidationResult:
    """Deterministic validation result suitable for tests and diagnostics."""

    is_valid: bool
    errors: tuple[str, ...] = field(default_factory=tuple)

    def raise_for_errors(self) -> None:
        if not self.is_valid:
            raise ResearchArtifactValidationError("; ".join(self.errors))


@dataclass(frozen=True, slots=True)
class ResearchArtifact:
    """A validated collection of non-production research rows."""

    metadata: ResearchArtifactMetadata
    rows: tuple[ResearchArtifactRow, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, ResearchArtifactMetadata):
            raise TypeError("metadata must be ResearchArtifactMetadata")
        rows = tuple(self.rows)
        if any(not isinstance(row, ResearchArtifactRow) for row in rows):
            raise TypeError("rows must contain only ResearchArtifactRow values")
        object.__setattr__(self, "rows", rows)

    def _validated(self) -> None:
        validate_artifact(self).raise_for_errors()

    def to_dict(self) -> dict[str, object]:
        """Serialize only after all contract and default-deny checks pass."""

        self._validated()
        metadata = self.metadata
        return {
            "metadata": {
                "artifact_id": metadata.artifact_id,
                "sport": metadata.sport,
                "league": metadata.league,
                "market_type": metadata.market_type,
                "mode": metadata.mode,
                "artifact_type": metadata.artifact_type,
                "run_date": _iso(metadata.run_date),
                "generated_at": _iso(metadata.generated_at),
                "provider_names": list(metadata.provider_names),
                "source_types": list(metadata.source_types),
                "code_version": metadata.code_version,
                "data_version": metadata.data_version,
                "schema_version": metadata.schema_version,
                "approval_status": metadata.approval_status,
                "eligible_for_betting": metadata.eligible_for_betting,
                "kelly_eligible": metadata.kelly_eligible,
            },
            "rows": [row.to_dict() for row in self.rows],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Return deterministic JSON with stable key ordering."""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )

    def to_csv_rows(self) -> list[dict[str, object]]:
        """Return flat rows without any production recommendation or sizing fields."""

        self._validated()
        rows: list[dict[str, object]] = []
        for artifact_row in self.rows:
            row = artifact_row.to_dict()
            for key in ("reasons", "warnings", "source_refs"):
                row[key] = json.dumps(row[key], ensure_ascii=False, sort_keys=True)
            rows.append(row)
        return rows


def _missing_text(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()


def validate_artifact(artifact: ResearchArtifact) -> ResearchArtifactValidationResult:
    """Validate an artifact without mutating it; every ambiguity fails closed."""

    if not isinstance(artifact, ResearchArtifact):
        return ResearchArtifactValidationResult(False, ("artifact has invalid type",))

    metadata = artifact.metadata
    errors: list[str] = []
    for field_name in (
        "artifact_id",
        "sport",
        "league",
        "market_type",
        "mode",
        "artifact_type",
        "schema_version",
        "approval_status",
    ):
        if _missing_text(getattr(metadata, field_name)):
            errors.append(f"metadata.{field_name} is required")
    if not isinstance(metadata.run_date, date) or isinstance(metadata.run_date, datetime):
        errors.append("metadata.run_date must be a date")
    if not isinstance(metadata.generated_at, datetime):
        errors.append("metadata.generated_at must be a datetime")
    if not metadata.provider_names:
        errors.append("metadata.provider_names is required")
    elif any(_missing_text(name) for name in metadata.provider_names):
        errors.append("metadata.provider_names must contain non-empty strings")
    if not metadata.source_types:
        errors.append("metadata.source_types is required")
    elif any(_missing_text(source_type) for source_type in metadata.source_types):
        errors.append("metadata.source_types must contain non-empty strings")
    if metadata.mode not in SUPPORTED_ARTIFACT_MODES:
        errors.append(f"unsupported artifact mode: {metadata.mode!r}")
    if metadata.artifact_type not in SUPPORTED_ARTIFACT_TYPES:
        errors.append(f"unsupported artifact type: {metadata.artifact_type!r}")

    # Phase 1D has no promotion layer. Every mode in this contract is therefore
    # non-production, including historical artifacts.
    if metadata.approval_status != NOT_APPROVED:
        errors.append("metadata.approval_status must be 'not_approved'")
    if metadata.eligible_for_betting is not False:
        errors.append("metadata.eligible_for_betting must be false")
    if metadata.kelly_eligible is not False:
        errors.append("metadata.kelly_eligible must be false")

    for index, row in enumerate(artifact.rows):
        prefix = f"rows[{index}]"
        for field_name in (
            "row_id",
            "sport",
            "league",
            "market_type",
            "status",
            "data_quality",
            "mode",
            "approval_status",
        ):
            if _missing_text(getattr(row, field_name)):
                errors.append(f"{prefix}.{field_name} is required")
        for field_name in ("sport", "league", "market_type", "mode"):
            if getattr(row, field_name) != getattr(metadata, field_name):
                errors.append(f"{prefix}.{field_name} conflicts with artifact metadata")
        if row.approval_status != metadata.approval_status:
            errors.append(f"{prefix}.approval_status conflicts with artifact metadata")
        if row.eligible_for_betting != metadata.eligible_for_betting:
            errors.append(f"{prefix}.eligible_for_betting conflicts with artifact metadata")
        if row.kelly_eligible != metadata.kelly_eligible:
            errors.append(f"{prefix}.kelly_eligible conflicts with artifact metadata")
        if row.eligible_for_betting is not False:
            errors.append(f"{prefix}.eligible_for_betting must be false")
        if row.kelly_eligible is not False:
            errors.append(f"{prefix}.kelly_eligible must be false")
        if row.research_score is not None and (
            isinstance(row.research_score, bool)
            or not isinstance(row.research_score, (int, float))
            or not math.isfinite(float(row.research_score))
        ):
            errors.append(f"{prefix}.research_score must be a finite number")
        if row.event_date is not None and not isinstance(row.event_date, (date, datetime)):
            errors.append(f"{prefix}.event_date must be a date or datetime")

    return ResearchArtifactValidationResult(not errors, tuple(errors))


def write_artifact_json(
    artifact: ResearchArtifact, path: str | Path, *, indent: int | None = 2
) -> Path:
    """Write a validated artifact as UTF-8 JSON."""

    destination = Path(path)
    destination.write_text(f"{artifact.to_json(indent=indent)}\n", encoding="utf-8")
    return destination


def write_artifact_csv(artifact: ResearchArtifact, path: str | Path) -> Path:
    """Write validated artifact rows as CSV using the fixed row contract."""

    destination = Path(path)
    rows = artifact.to_csv_rows()
    fieldnames = list(ResearchArtifactRow(
        row_id="schema",
        sport="schema",
        league="schema",
        market_type="schema",
        status="schema",
        data_quality="schema",
        mode="research",
    ).to_dict())
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return destination


__all__ = [
    "NOT_APPROVED",
    "RESEARCH_ARTIFACT_SCHEMA_VERSION",
    "SUPPORTED_ARTIFACT_MODES",
    "SUPPORTED_ARTIFACT_TYPES",
    "ResearchArtifact",
    "ResearchArtifactMetadata",
    "ResearchArtifactRow",
    "ResearchArtifactValidationError",
    "ResearchArtifactValidationResult",
    "validate_artifact",
    "write_artifact_csv",
    "write_artifact_json",
]
