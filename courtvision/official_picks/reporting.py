"""Fail-closed reporting and settlement boundaries for official picks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from courtvision.official_picks.contracts import (
    OfficialPick,
    OfficialPickValidationError,
    PickRecordKind,
)
from courtvision.official_picks.service import read_official_pick


class OfficialPickReportBoundaryError(ValueError):
    """Raised when a report attempts to misclassify or combine record kinds."""


class OfficialPickSettlementReferenceError(ValueError):
    """Raised when settlement does not identify a committed official pick."""


@dataclass(frozen=True, slots=True)
class OfficialPickReportDataset:
    rows: tuple[OfficialPick, ...]
    report_scope: str
    performance_label: str
    excluded_observation_count: int
    excluded_candidate_count: int
    excluded_settlement_count: int
    excluded_legacy_count: int

    def to_rows(self) -> tuple[dict[str, Any], ...]:
        return tuple(item.to_dict() for item in self.rows)


def classify_record(value: OfficialPick | Mapping[str, Any]) -> str:
    if isinstance(value, OfficialPick):
        return PickRecordKind.OFFICIAL_PICK.value
    if not isinstance(value, Mapping):
        raise OfficialPickReportBoundaryError("performance row must be a mapping")
    kind = str(value.get("record_kind") or "").strip().upper()
    if kind in {item.value for item in PickRecordKind}:
        return kind
    event_type = str(value.get("event_type") or "").strip().upper()
    if event_type.endswith("_OBSERVED") or value.get("observation_id"):
        return PickRecordKind.MARKET_OBSERVATION.value
    if value.get("candidate_id") or value.get("prediction_id"):
        return PickRecordKind.MODEL_CANDIDATE.value
    if value.get("pick_id"):
        return PickRecordKind.OFFICIAL_PICK.value
    return PickRecordKind.LEGACY_UNIDENTIFIED.value


def build_official_pick_report_dataset(
    rows: Iterable[OfficialPick | Mapping[str, Any]],
) -> OfficialPickReportDataset:
    """Build an explicitly labeled official-pick-only reporting dataset."""

    picks: list[OfficialPick] = []
    observations = 0
    candidates = 0
    settlements = 0
    legacy = 0
    for value in rows:
        kind = classify_record(value)
        if kind == PickRecordKind.OFFICIAL_PICK.value:
            try:
                picks.append(
                    value
                    if isinstance(value, OfficialPick)
                    else OfficialPick.from_dict(value)
                )
            except OfficialPickValidationError as exc:
                raise OfficialPickReportBoundaryError(
                    "malformed row claims to be an official pick"
                ) from exc
        elif kind == PickRecordKind.MARKET_OBSERVATION.value:
            observations += 1
        elif kind == PickRecordKind.MODEL_CANDIDATE.value:
            candidates += 1
        elif kind == PickRecordKind.SETTLED_OFFICIAL_PICK.value:
            settlements += 1
        else:
            legacy += 1
    return OfficialPickReportDataset(
        rows=tuple(picks),
        report_scope="OFFICIAL_PICKS_ONLY",
        performance_label="official-pick ROI",
        excluded_observation_count=observations,
        excluded_candidate_count=candidates,
        excluded_settlement_count=settlements,
        excluded_legacy_count=legacy,
    )


def require_official_pick_roi_rows(
    rows: Iterable[OfficialPick | Mapping[str, Any]],
) -> tuple[OfficialPick, ...]:
    """Reject mixed inputs instead of silently describing them as betting ROI."""

    dataset = build_official_pick_report_dataset(rows)
    excluded = (
        dataset.excluded_observation_count
        + dataset.excluded_candidate_count
        + dataset.excluded_settlement_count
        + dataset.excluded_legacy_count
    )
    if excluded:
        raise OfficialPickReportBoundaryError(
            "official-pick ROI input contains observations, candidates, or "
            "legacy unidentified rows"
        )
    return dataset.rows


def observation_performance_metadata() -> dict[str, str]:
    return {
        "report_scope": "MARKET_OBSERVATIONS_ONLY",
        "performance_label": "observation performance",
        "betting_roi_claim": "NOT_PERMITTED",
    }


def candidate_performance_metadata() -> dict[str, str]:
    return {
        "report_scope": "MODEL_CANDIDATES_ONLY",
        "performance_label": "model candidate analysis",
        "betting_roi_claim": "NOT_PERMITTED",
    }


def adapt_legacy_unidentified(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Label legacy input without guessing or backfilling a pick ID."""

    if value.get("pick_id"):
        raise OfficialPickReportBoundaryError(
            "record already has pick_id and is not legacy unidentified"
        )
    return {
        **dict(value),
        "record_kind": PickRecordKind.LEGACY_UNIDENTIFIED.value,
        "official_pick_identity_status": "legacy_unidentified",
    }


def validate_settlement_pick_reference(
    settlement: Mapping[str, Any],
    *,
    lifecycle_root: str | Path,
) -> OfficialPick:
    pick_id = str(settlement.get("pick_id") or "").strip()
    if not pick_id:
        raise OfficialPickSettlementReferenceError(
            "settlement requires a non-empty pick_id"
        )
    pick = read_official_pick(lifecycle_root, pick_id)
    if pick is None:
        raise OfficialPickSettlementReferenceError(
            f"settlement pick_id is not present in the official-pick ledger: {pick_id}"
        )
    return pick


__all__ = [
    "OfficialPickReportBoundaryError",
    "OfficialPickReportDataset",
    "OfficialPickSettlementReferenceError",
    "adapt_legacy_unidentified",
    "build_official_pick_report_dataset",
    "candidate_performance_metadata",
    "classify_record",
    "observation_performance_metadata",
    "require_official_pick_roi_rows",
    "validate_settlement_pick_reference",
]
