"""Fail-closed reporting and settlement boundaries for official picks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from courtvision.lifecycle.canonical import canonical_json_v1, format_utc_datetime
from courtvision.official_picks.contracts import (
    OFFICIAL_PICK_SCHEMA_VERSION,
    OfficialPick,
    OfficialPickOperatorDecision,
    OfficialPickSettlement,
    OfficialPickSettlementCorrection,
    OfficialPickValidationError,
    PickRecordKind,
)
from courtvision.official_picks.review import (
    read_official_pick_candidate_reviews,
)
from courtvision.official_picks.service import read_official_pick, read_official_picks
from courtvision.official_picks.settlement import (
    OfficialPickSettlementReferenceError,
    read_official_pick_settlement_states,
)


class OfficialPickReportBoundaryError(ValueError):
    """Raised when a report attempts to misclassify or combine record kinds."""


@dataclass(frozen=True, slots=True)
class OfficialPickReportRow:
    pick_id: str
    review_id: str
    source_candidate_id: str
    sport: str
    league: str
    event_id: str
    event_start_time: str
    prediction_date: str
    market_key: str
    selection: str
    line: str
    odds: int | float
    sportsbook: str
    player_id: str | None
    player_name: str | None
    team_id: str | None
    model_name: str
    model_version: str
    source_run_id: str
    published_at: str
    status: str
    designation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class OfficialPickReportDataset:
    rows: tuple[OfficialPickReportRow, ...]
    report_scope: str
    performance_label: str
    excluded_observation_count: int
    excluded_candidate_count: int
    excluded_settlement_count: int
    excluded_legacy_count: int
    excluded_review_count: int = 0

    def to_rows(self) -> tuple[dict[str, Any], ...]:
        return tuple(item.to_dict() for item in self.rows)


@dataclass(frozen=True, slots=True)
class OfficialPickSettlementReportRow:
    pick_id: str
    review_id: str
    source_candidate_id: str
    sport: str
    league: str
    event_id: str
    prediction_date: str
    market_key: str
    selection: str
    line: str
    odds: int | float
    sportsbook: str
    player_name: str | None
    model_name: str
    model_version: str
    designation: str
    published_at: str
    settlement_id: str | None
    settlement_status: str
    outcome: str | None
    effective_outcome: str
    final_score_text: str | None
    settled_at: str | None
    result_source: str | None
    source_record_id: str | None
    correction_id: str | None
    correction_reason: str | None
    corrected_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class OfficialPickSettlementReportDataset:
    settled_rows: tuple[OfficialPickSettlementReportRow, ...]
    unresolved_rows: tuple[OfficialPickSettlementReportRow, ...]
    report_scope: str
    performance_label: str
    excluded_observation_count: int
    excluded_candidate_count: int
    excluded_legacy_count: int
    excluded_review_count: int = 0

    def to_rows(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            item.to_dict()
            for item in (*self.settled_rows, *self.unresolved_rows)
        )


@dataclass(frozen=True, slots=True)
class OfficialPickOperatorReviewRow:
    review_id: str
    source_candidate_id: str
    operator_decision: str
    approved_designation: str
    review_status: str
    operator_id: str
    decision_reason: str
    reviewed_at: str
    review_run_id: str
    sport: str
    league: str
    event_id: str
    event_start_time: str
    prediction_date: str
    market_key: str
    selection: str
    line: str
    odds: int | float
    sportsbook: str
    player_id: str | None
    player_name: str | None
    team_id: str | None
    model_name: str
    model_version: str
    source_run_id: str
    promoted: bool
    pick_id: str | None
    designation: str | None
    published_at: str | None
    promotion_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "source_candidate_id": self.source_candidate_id,
            "operator_decision": self.operator_decision,
            "approved_designation": self.approved_designation,
            "review_status": self.review_status,
            "operator_id": self.operator_id,
            "decision_reason": self.decision_reason,
            "reviewed_at": self.reviewed_at,
            "review_run_id": self.review_run_id,
            "sport": self.sport,
            "league": self.league,
            "event_id": self.event_id,
            "event_start_time": self.event_start_time,
            "prediction_date": self.prediction_date,
            "market_key": self.market_key,
            "selection": self.selection,
            "line": self.line,
            "odds": self.odds,
            "sportsbook": self.sportsbook,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "team_id": self.team_id,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "source_run_id": self.source_run_id,
            "promoted": self.promoted,
            "pick_id": self.pick_id,
            "designation": self.designation,
            "published_at": self.published_at,
            "promotion_status": self.promotion_status,
        }


@dataclass(frozen=True, slots=True)
class OfficialPickOperatorReviewDataset:
    approved_candidates: tuple[OfficialPickOperatorReviewRow, ...]
    rejected_candidates: tuple[OfficialPickOperatorReviewRow, ...]
    deferred_candidates: tuple[OfficialPickOperatorReviewRow, ...]
    expired_candidates: tuple[OfficialPickOperatorReviewRow, ...]
    approved_not_promoted_candidates: tuple[
        OfficialPickOperatorReviewRow, ...
    ]
    approved_promoted_candidates: tuple[OfficialPickOperatorReviewRow, ...]
    report_scope: str = "OFFICIAL_PICK_OPERATOR_REVIEWS_ONLY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_scope": self.report_scope,
            "approved_candidates": [
                item.to_dict() for item in self.approved_candidates
            ],
            "rejected_candidates": [
                item.to_dict() for item in self.rejected_candidates
            ],
            "deferred_candidates": [
                item.to_dict() for item in self.deferred_candidates
            ],
            "expired_candidates": [
                item.to_dict() for item in self.expired_candidates
            ],
            "approved_not_promoted_candidates": [
                item.to_dict()
                for item in self.approved_not_promoted_candidates
            ],
            "approved_promoted_candidates": [
                item.to_dict()
                for item in self.approved_promoted_candidates
            ],
        }


def _active_official_pick(
    value: OfficialPick | Mapping[str, Any],
) -> OfficialPick:
    if isinstance(value, OfficialPick):
        if value.schema_version != OFFICIAL_PICK_SCHEMA_VERSION:
            raise OfficialPickValidationError(
                "reporting requires active OfficialPick schema_version 2"
            )
        return OfficialPick.from_dict(value.to_dict())
    if isinstance(value, Mapping):
        return OfficialPick.from_dict(value)
    raise OfficialPickValidationError(
        "reporting requires an active OfficialPick or v2 mapping"
    )


def _official_pick_report_row(pick: OfficialPick) -> OfficialPickReportRow:
    active = _active_official_pick(pick)
    return OfficialPickReportRow(
        pick_id=active.pick_id,
        review_id=str(active.review_id),
        source_candidate_id=str(active.source_candidate_id),
        sport=active.sport,
        league=active.league,
        event_id=active.event_id,
        event_start_time=format_utc_datetime(active.event_start_time),
        prediction_date=active.prediction_date,
        market_key=active.market_key,
        selection=active.selection,
        line=active.line,
        odds=active.odds,
        sportsbook=active.sportsbook,
        player_id=active.player_id,
        player_name=active.player_name,
        team_id=active.team_id,
        model_name=active.model_name,
        model_version=active.model_version,
        source_run_id=active.run_id,
        published_at=format_utc_datetime(active.published_at),
        status=active.status,
        designation=active.designation,
    )


def _score_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return canonical_json_v1(value)
    return str(value)


def _settlement_report_row(
    pick: OfficialPick,
    *,
    settlement: OfficialPickSettlement | None,
    correction: OfficialPickSettlementCorrection | None,
    settlement_status: str,
    effective_outcome: str,
) -> OfficialPickSettlementReportRow:
    active = _active_official_pick(pick)
    score = (
        correction.corrected_final_score
        if correction is not None
        else settlement.final_score if settlement is not None else None
    )
    result_source = (
        correction.result_source
        if correction is not None
        else settlement.result_source if settlement is not None else None
    )
    source_record_id = (
        correction.source_record_id
        if correction is not None
        else settlement.source_record_id if settlement is not None else None
    )
    return OfficialPickSettlementReportRow(
        pick_id=active.pick_id,
        review_id=str(active.review_id),
        source_candidate_id=str(active.source_candidate_id),
        sport=active.sport,
        league=active.league,
        event_id=active.event_id,
        prediction_date=active.prediction_date,
        market_key=active.market_key,
        selection=active.selection,
        line=active.line,
        odds=active.odds,
        sportsbook=active.sportsbook,
        player_name=active.player_name,
        model_name=active.model_name,
        model_version=active.model_version,
        designation=active.designation,
        published_at=format_utc_datetime(active.published_at),
        settlement_id=(
            settlement.settlement_id if settlement is not None else None
        ),
        settlement_status=settlement_status,
        outcome=settlement.outcome if settlement is not None else None,
        effective_outcome=effective_outcome,
        final_score_text=_score_text(score),
        settled_at=(
            format_utc_datetime(settlement.settled_at)
            if settlement is not None
            else None
        ),
        result_source=result_source,
        source_record_id=source_record_id,
        correction_id=(
            correction.correction_id if correction is not None else None
        ),
        correction_reason=(
            correction.correction_reason if correction is not None else None
        ),
        corrected_at=(
            format_utc_datetime(correction.corrected_at)
            if correction is not None
            else None
        ),
    )


def classify_record(value: OfficialPick | Mapping[str, Any]) -> str:
    if isinstance(value, OfficialPick):
        try:
            _active_official_pick(value)
        except OfficialPickValidationError as exc:
            raise OfficialPickReportBoundaryError(
                "constructed OfficialPick does not satisfy the active v2 contract"
            ) from exc
        return PickRecordKind.OFFICIAL_PICK.value
    if not isinstance(value, Mapping):
        raise OfficialPickReportBoundaryError("performance row must be a mapping")
    kind = str(value.get("record_kind") or "").strip().upper()
    if kind == "OFFICIAL_PICK_SETTLEMENT_CORRECTION":
        return PickRecordKind.SETTLED_OFFICIAL_PICK.value
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

    picks: list[OfficialPickReportRow] = []
    observations = 0
    candidates = 0
    settlements = 0
    legacy = 0
    reviews = 0
    for value in rows:
        kind = classify_record(value)
        if kind == PickRecordKind.OFFICIAL_PICK.value:
            try:
                pick = _active_official_pick(value)
                picks.append(
                    _official_pick_report_row(pick)
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
        elif kind == PickRecordKind.OFFICIAL_PICK_CANDIDATE_REVIEW.value:
            reviews += 1
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
        excluded_review_count=reviews,
    )


def require_official_pick_roi_rows(
    rows: Iterable[OfficialPick | Mapping[str, Any]],
) -> tuple[OfficialPickReportRow, ...]:
    """Reject mixed inputs instead of silently describing them as betting ROI."""

    dataset = build_official_pick_report_dataset(rows)
    excluded = (
        dataset.excluded_observation_count
        + dataset.excluded_candidate_count
        + dataset.excluded_settlement_count
        + dataset.excluded_legacy_count
        + dataset.excluded_review_count
    )
    if excluded:
        raise OfficialPickReportBoundaryError(
            "official-pick ROI input contains observations, candidates, "
            "operator reviews, or legacy unidentified rows"
        )
    return dataset.rows


def build_official_pick_settlement_dataset(
    *,
    lifecycle_root: str | Path,
    source_rows: Iterable[OfficialPick | Mapping[str, Any]] = (),
) -> OfficialPickSettlementReportDataset:
    """Build an official-pick-only current settlement view from the ledger.

    ``source_rows`` is accepted only to make exclusions explicit. It never
    supplies a settlement join: official picks and settlements are reconstructed
    from verified lifecycle events and joined solely through committed
    ``pick_id`` values.
    """

    observations = 0
    candidates = 0
    legacy = 0
    reviews = 0
    for value in source_rows:
        kind = classify_record(value)
        if kind == PickRecordKind.MARKET_OBSERVATION.value:
            observations += 1
        elif kind == PickRecordKind.MODEL_CANDIDATE.value:
            candidates += 1
        elif kind == PickRecordKind.LEGACY_UNIDENTIFIED.value:
            legacy += 1
        elif kind == PickRecordKind.OFFICIAL_PICK_CANDIDATE_REVIEW.value:
            reviews += 1

    picks = {item.pick_id: item for item in read_official_picks(lifecycle_root)}
    states = read_official_pick_settlement_states(lifecycle_root)
    settled: list[OfficialPickSettlementReportRow] = []
    unresolved: list[OfficialPickSettlementReportRow] = []
    for state in states:
        pick = picks.get(state.pick_id)
        if pick is None:
            raise OfficialPickReportBoundaryError(
                f"settlement state has no committed official pick: {state.pick_id}"
            )
        if state.final_settlement is not None:
            settled.append(
                _settlement_report_row(
                    pick,
                    settlement=state.final_settlement,
                    correction=state.correction,
                    settlement_status=state.settlement_status,
                    effective_outcome=state.effective_outcome,
                )
            )
        else:
            unresolved.append(
                _settlement_report_row(
                    pick,
                    settlement=state.unresolved_settlement,
                    correction=None,
                    settlement_status=state.settlement_status,
                    effective_outcome=state.effective_outcome,
                )
            )
    return OfficialPickSettlementReportDataset(
        settled_rows=tuple(settled),
        unresolved_rows=tuple(unresolved),
        report_scope="OFFICIAL_PICK_SETTLEMENTS_ONLY",
        performance_label="official-pick settlement performance",
        excluded_observation_count=observations,
        excluded_candidate_count=candidates,
        excluded_legacy_count=legacy,
        excluded_review_count=reviews,
    )


def build_official_pick_operator_review_dataset(
    *,
    lifecycle_root: str | Path,
) -> OfficialPickOperatorReviewDataset:
    """Build a review-only state report joined by review and candidate IDs."""

    reviews = read_official_pick_candidate_reviews(lifecycle_root)
    picks = read_official_picks(lifecycle_root)
    reviews_by_id = {item.review_id: item for item in reviews}
    picks_by_review: dict[str, OfficialPick] = {}
    for pick in picks:
        if pick.review_id is None:
            continue
        review = reviews_by_id.get(pick.review_id)
        if review is None:
            raise OfficialPickReportBoundaryError(
                f"official pick has no committed review: {pick.pick_id}"
            )
        if (
            review.source_candidate_id != pick.source_candidate_id
            or review.operator_decision
            != OfficialPickOperatorDecision.APPROVED.value
            or review.approved_designation != pick.designation
        ):
            raise OfficialPickReportBoundaryError(
                f"official pick review join is invalid: {pick.pick_id}"
            )
        if pick.review_id in picks_by_review:
            raise OfficialPickReportBoundaryError(
                f"review joins to multiple official picks: {pick.review_id}"
            )
        picks_by_review[pick.review_id] = pick

    approved: list[OfficialPickOperatorReviewRow] = []
    rejected: list[OfficialPickOperatorReviewRow] = []
    deferred: list[OfficialPickOperatorReviewRow] = []
    expired: list[OfficialPickOperatorReviewRow] = []
    approved_not_promoted: list[OfficialPickOperatorReviewRow] = []
    approved_promoted: list[OfficialPickOperatorReviewRow] = []
    for review in reviews:
        pick = picks_by_review.get(review.review_id)
        review_dict = review.to_dict()
        snapshot = review_dict["candidate_snapshot"]
        if not isinstance(snapshot, dict):
            raise OfficialPickReportBoundaryError(
                f"review candidate snapshot is malformed: {review.review_id}"
            )
        pick_dict = pick.to_dict() if pick is not None else None
        row = OfficialPickOperatorReviewRow(
            review_id=review.review_id,
            source_candidate_id=review.source_candidate_id,
            operator_decision=review.operator_decision,
            approved_designation=review.approved_designation,
            review_status=review.review_status,
            operator_id=review.operator_id,
            decision_reason=review.decision_reason,
            reviewed_at=str(review_dict["reviewed_at"]),
            review_run_id=review.review_run_id,
            sport=str(snapshot["sport"]),
            league=str(snapshot["league"]),
            event_id=str(snapshot["event_id"]),
            event_start_time=str(snapshot["event_start_time"]),
            prediction_date=str(snapshot["prediction_date"]),
            market_key=str(snapshot["market_key"]),
            selection=str(snapshot["selection"]),
            line=str(snapshot["line"]),
            odds=snapshot["odds"],
            sportsbook=str(snapshot["sportsbook"]),
            player_id=_report_optional_text(snapshot.get("player_id")),
            player_name=_report_optional_text(snapshot.get("player_name")),
            team_id=_report_optional_text(snapshot.get("team_id")),
            model_name=str(snapshot["model_name"]),
            model_version=str(snapshot["model_version"]),
            source_run_id=str(snapshot["run_id"]),
            promoted=pick is not None,
            pick_id=pick.pick_id if pick is not None else None,
            designation=pick.designation if pick is not None else None,
            published_at=(
                str(pick_dict["published_at"])
                if pick_dict is not None
                else None
            ),
            promotion_status=(
                "PROMOTED" if pick is not None else "NOT_PROMOTED"
            ),
        )
        if review.operator_decision == OfficialPickOperatorDecision.APPROVED.value:
            approved.append(row)
            if pick is None:
                approved_not_promoted.append(row)
            else:
                approved_promoted.append(row)
        elif review.operator_decision == OfficialPickOperatorDecision.REJECTED.value:
            rejected.append(row)
        elif review.operator_decision == OfficialPickOperatorDecision.DEFERRED.value:
            deferred.append(row)
        elif review.operator_decision == OfficialPickOperatorDecision.EXPIRED.value:
            expired.append(row)

    return OfficialPickOperatorReviewDataset(
        approved_candidates=tuple(approved),
        rejected_candidates=tuple(rejected),
        deferred_candidates=tuple(deferred),
        expired_candidates=tuple(expired),
        approved_not_promoted_candidates=tuple(approved_not_promoted),
        approved_promoted_candidates=tuple(approved_promoted),
    )


def _report_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


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
    if not isinstance(settlement, Mapping):
        raise OfficialPickSettlementReferenceError(
            "settlement reference must be an active mapping"
        )
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
    "OfficialPickReportRow",
    "OfficialPickSettlementReportDataset",
    "OfficialPickSettlementReportRow",
    "OfficialPickOperatorReviewDataset",
    "OfficialPickOperatorReviewRow",
    "OfficialPickSettlementReferenceError",
    "adapt_legacy_unidentified",
    "build_official_pick_report_dataset",
    "build_official_pick_operator_review_dataset",
    "build_official_pick_settlement_dataset",
    "candidate_performance_metadata",
    "classify_record",
    "observation_performance_metadata",
    "require_official_pick_roi_rows",
    "validate_settlement_pick_reference",
]
