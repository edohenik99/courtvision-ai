"""Typed contracts for the canonical CourtVision prediction boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PredictionRequest:
    """One request to create new model-derived predictions."""

    sport: str
    prediction_date: str
    mode: str
    run_id: str | None = None
    out_dir: str | None = None
    dry_run: bool = False
    force_overwrite: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PredictionResult:
    """Typed result returned by every prediction-producing entrypoint."""

    sport: str
    prediction_date: str
    run_id: str
    status: str
    outputs: Mapping[str, Any]
    artifact_paths: Mapping[str, str]
    provider_provenance: Mapping[str, Any]
    lifecycle_status: str
    manifest_path: str | None = None
    failure_classification: str | None = None


@dataclass(slots=True)
class EnginePrediction:
    """Sport-engine output before canonical artifact publication."""

    outputs: Mapping[str, Any]
    provider_provenance: Mapping[str, Any] = field(default_factory=dict)
    model_version: str | None = None
    status: str | None = None


@runtime_checkable
class PredictionEngine(Protocol):
    """Minimal interface implemented by sport-specific prediction engines."""

    sport: str
    modes: frozenset[str]

    def execute(self, request: PredictionRequest) -> EnginePrediction:
        """Create model predictions without owning application orchestration."""


__all__ = [
    "EnginePrediction",
    "PredictionEngine",
    "PredictionRequest",
    "PredictionResult",
]
