"""Strict in-memory queue model for unresolved MLB official-pick reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from courtvision.lifecycle.canonical import (
    deterministic_id,
    format_utc_datetime,
    parse_utc_datetime,
)
from courtvision.lifecycle.clock import Clock, SystemClock
from courtvision.official_picks.service import read_official_pick
from courtvision.official_picks.settlement import (
    OfficialPickSettlementReferenceError,
    OfficialPickSettlementTransitionError,
    read_official_pick_settlement_state,
)


MLB_OFFICIAL_PICK_RECONCILIATION_SCHEMA_VERSION = 1


class MLBOfficialPickReconciliationReason(str, Enum):
    GAME_NOT_FINAL = "game_not_final"
    PLAYER_MISSING_FROM_BOXSCORE = "player_missing_from_boxscore"
    EVENT_NOT_MATCHED = "event_not_matched"
    AMBIGUOUS_PLAYER_IDENTITY = "ambiguous_player_identity"
    SOURCE_UNAVAILABLE = "source_unavailable"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class MLBOfficialPickReconciliationValidationError(ValueError):
    """Raised when a row is not an unresolved committed MLB official pick."""


@dataclass(frozen=True, slots=True)
class MLBOfficialPickReconciliationItem:
    reconciliation_id: str
    pick_id: str
    reason: str
    queued_at: datetime
    reconciliation_run_id: str
    provenance: Mapping[str, Any]
    schema_version: int = MLB_OFFICIAL_PICK_RECONCILIATION_SCHEMA_VERSION
    record_kind: str = "OFFICIAL_PICK_RECONCILIATION"

    def __post_init__(self) -> None:
        if re.fullmatch(r"mlbrec_[0-9a-f]{64}", self.reconciliation_id) is None:
            raise MLBOfficialPickReconciliationValidationError(
                "reconciliation_id must be a generated MLB reconciliation key"
            )
        if re.fullmatch(r"pick_[0-9a-f]{32}", self.pick_id) is None:
            raise MLBOfficialPickReconciliationValidationError(
                "MLB reconciliation requires a committed pick_id"
            )
        reason = (
            self.reason.value
            if isinstance(self.reason, MLBOfficialPickReconciliationReason)
            else str(self.reason).strip().lower()
        )
        if reason not in {
            item.value for item in MLBOfficialPickReconciliationReason
        }:
            raise MLBOfficialPickReconciliationValidationError(
                "unsupported MLB reconciliation reason"
            )
        try:
            queued_at = parse_utc_datetime(self.queued_at)
        except (TypeError, ValueError) as exc:
            raise MLBOfficialPickReconciliationValidationError(
                "queued_at must be a timezone-aware ISO-8601 datetime"
            ) from exc
        if queued_at is None:
            raise MLBOfficialPickReconciliationValidationError(
                "queued_at is required"
            )
        run_id = str(self.reconciliation_run_id or "").strip()
        if not run_id:
            raise MLBOfficialPickReconciliationValidationError(
                "reconciliation_run_id is required"
            )
        if not isinstance(self.provenance, Mapping):
            raise MLBOfficialPickReconciliationValidationError(
                "provenance must be a mapping"
            )
        provenance = dict(self.provenance)
        if (
            provenance.get("reconciliation_service")
            != "courtvision.official_picks.mlb_reconciliation"
        ):
            raise MLBOfficialPickReconciliationValidationError(
                "provenance.reconciliation_service is invalid"
            )
        if not str(provenance.get("reconciliation_actor") or "").strip():
            raise MLBOfficialPickReconciliationValidationError(
                "provenance.reconciliation_actor is required"
            )
        if self.schema_version != MLB_OFFICIAL_PICK_RECONCILIATION_SCHEMA_VERSION:
            raise MLBOfficialPickReconciliationValidationError(
                "unsupported MLB reconciliation schema_version"
            )
        if self.record_kind != "OFFICIAL_PICK_RECONCILIATION":
            raise MLBOfficialPickReconciliationValidationError(
                "record_kind must be OFFICIAL_PICK_RECONCILIATION"
            )
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "queued_at", queued_at.astimezone(UTC))
        object.__setattr__(self, "reconciliation_run_id", run_id)
        object.__setattr__(self, "provenance", provenance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reconciliation_id": self.reconciliation_id,
            "pick_id": self.pick_id,
            "reason": self.reason,
            "queued_at": format_utc_datetime(self.queued_at),
            "reconciliation_run_id": self.reconciliation_run_id,
            "provenance": dict(self.provenance),
            "schema_version": self.schema_version,
            "record_kind": self.record_kind,
        }


@dataclass(frozen=True, slots=True)
class MLBOfficialPickReconciliationQueue:
    items: tuple[MLBOfficialPickReconciliationItem, ...]
    queue_scope: str = "UNRESOLVED_MLB_OFFICIAL_PICKS_ONLY"

    def to_rows(self) -> tuple[dict[str, Any], ...]:
        return tuple(item.to_dict() for item in self.items)


def create_mlb_official_pick_reconciliation_item(
    pick_id: str,
    *,
    reason: str | MLBOfficialPickReconciliationReason,
    reconciliation_run_id: str,
    lifecycle_root: str | Path,
    reconciliation_actor: str = "courtvision.operator",
    provenance: Mapping[str, Any] | None = None,
    clock: Clock | None = None,
) -> MLBOfficialPickReconciliationItem:
    """Create a queue item only for a committed unresolved MLB official pick."""

    target = str(pick_id or "").strip()
    if re.fullmatch(r"pick_[0-9a-f]{32}", target) is None:
        raise OfficialPickSettlementReferenceError(
            "MLB reconciliation requires a committed pick_<uuid4 hex> pick_id"
        )
    pick = read_official_pick(lifecycle_root, target)
    if pick is None:
        raise OfficialPickSettlementReferenceError(
            f"MLB reconciliation pick_id is not committed: {target}"
        )
    if pick.league != "MLB":
        raise MLBOfficialPickReconciliationValidationError(
            "MLB reconciliation requires an MLB official pick"
        )
    state = read_official_pick_settlement_state(lifecycle_root, target)
    if state is None:
        raise OfficialPickSettlementReferenceError(
            f"MLB reconciliation pick_id is not committed: {target}"
        )
    if state.final_settlement is not None:
        raise OfficialPickSettlementTransitionError(
            "final MLB official picks cannot enter the unresolved reconciliation queue"
        )
    reason_value = (
        reason.value
        if isinstance(reason, MLBOfficialPickReconciliationReason)
        else str(reason).strip().lower()
    )
    run_id = str(reconciliation_run_id or "").strip()
    actor = str(reconciliation_actor or "").strip()
    if not run_id:
        raise MLBOfficialPickReconciliationValidationError(
            "reconciliation_run_id is required"
        )
    if not actor:
        raise MLBOfficialPickReconciliationValidationError(
            "reconciliation_actor is required"
        )
    if provenance is not None and not isinstance(provenance, Mapping):
        raise MLBOfficialPickReconciliationValidationError(
            "provenance must be a mapping"
        )
    queued_at = (clock or SystemClock()).now()
    reconciliation_id = deterministic_id(
        "mlbrec",
        "courtvision.mlb_official_pick_reconciliation.v1",
        {
            "pick_id": target,
            "reason": reason_value,
            "reconciliation_run_id": run_id,
        },
    )
    return MLBOfficialPickReconciliationItem(
        reconciliation_id=reconciliation_id,
        pick_id=target,
        reason=reason_value,
        queued_at=queued_at,
        reconciliation_run_id=run_id,
        provenance={
            **dict(provenance or {}),
            "reconciliation_service": (
                "courtvision.official_picks.mlb_reconciliation"
            ),
            "reconciliation_actor": actor,
        },
    )


def build_mlb_official_pick_reconciliation_queue(
    rows: Iterable[Mapping[str, Any]],
    *,
    lifecycle_root: str | Path,
    reconciliation_actor: str = "courtvision.operator",
    clock: Clock | None = None,
) -> MLBOfficialPickReconciliationQueue:
    active_clock = clock or SystemClock()
    items = tuple(
        create_mlb_official_pick_reconciliation_item(
            str(row.get("pick_id") or ""),
            reason=str(row.get("reason") or ""),
            reconciliation_run_id=str(
                row.get("reconciliation_run_id") or ""
            ),
            lifecycle_root=lifecycle_root,
            reconciliation_actor=reconciliation_actor,
            provenance=row.get("provenance"),  # type: ignore[arg-type]
            clock=active_clock,
        )
        for row in rows
    )
    return MLBOfficialPickReconciliationQueue(items=items)


__all__ = [
    "MLB_OFFICIAL_PICK_RECONCILIATION_SCHEMA_VERSION",
    "MLBOfficialPickReconciliationItem",
    "MLBOfficialPickReconciliationQueue",
    "MLBOfficialPickReconciliationReason",
    "MLBOfficialPickReconciliationValidationError",
    "build_mlb_official_pick_reconciliation_queue",
    "create_mlb_official_pick_reconciliation_item",
]
