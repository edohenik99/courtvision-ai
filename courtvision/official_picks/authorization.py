"""Shared review authorization validation for OfficialPick schema v2."""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from courtvision.lifecycle.canonical import (
    canonical_equal,
    canonical_equality_sha256,
    canonical_json_value,
)
from courtvision.official_picks.contracts import (
    OFFICIAL_PICK_PROMOTION_POLICY_VERSION,
    OFFICIAL_PICK_SCHEMA_VERSION,
    OfficialPick,
    OfficialPickCandidateReview,
    OfficialPickDesignation,
    OfficialPickOperatorDecision,
    OfficialPickPromotionRequest,
    OfficialPickReviewStatus,
    PickRecordKind,
)


class OfficialPickAuthorizationValidationError(ValueError):
    """A schema-v2 pick does not reproduce its committed approved review."""


# Windows may deny reads of an open create-exclusive lock file. This
# process-local admission lock keeps same-process operator transactions
# deterministic; LifecycleWriterLock remains the cross-process authority.
OFFICIAL_PICK_PROCESS_TRANSACTION_LOCK = RLock()


_SNAPSHOT_TO_PICK_FIELDS = {
    "sport": "sport",
    "league": "league",
    "event_id": "event_id",
    "event_start_time": "event_start_time",
    "prediction_date": "prediction_date",
    "market_key": "market_key",
    "selection": "selection",
    "line": "line",
    "odds": "odds",
    "sportsbook": "sportsbook",
    "player_id": "player_id",
    "player_name": "player_name",
    "team_id": "team_id",
    "model_name": "model_name",
    "model_version": "model_version",
    "run_id": "run_id",
    "designation": "designation",
    "source_candidate_id": "source_candidate_id",
    "source_observation_id": "source_observation_id",
}

OFFICIAL_PICK_GENERATED_PROVENANCE_KEYS = frozenset(
    {
        "source_type",
        "source_id",
        "promotion_service",
        "promotion_actor",
        "promotion_policy_version",
        "review_id",
        "review_decision",
        "review_operator_id",
        "review_run_id",
        "candidate_snapshot_sha256",
    }
)


def validate_schema_v2_review_authorization(
    pick: OfficialPick,
    review: OfficialPickCandidateReview,
    *,
    candidate: OfficialPickPromotionRequest | None = None,
) -> None:
    """Fail closed unless *pick* exactly reproduces its approved review."""

    if pick.schema_version != OFFICIAL_PICK_SCHEMA_VERSION:
        raise OfficialPickAuthorizationValidationError(
            "review authorization applies only to OfficialPick schema v2"
        )
    if review.review_status != OfficialPickReviewStatus.COMMITTED.value:
        raise OfficialPickAuthorizationValidationError(
            "review authorization is not committed"
        )
    if review.operator_decision != OfficialPickOperatorDecision.APPROVED.value:
        raise OfficialPickAuthorizationValidationError(
            "review authorization decision is not APPROVED"
        )
    if pick.review_id != review.review_id:
        raise OfficialPickAuthorizationValidationError(
            "review_id does not match the committed review"
        )
    if pick.source_candidate_id != review.source_candidate_id:
        raise OfficialPickAuthorizationValidationError(
            "source_candidate_id does not match the committed review"
        )

    reviewed_snapshot = review.candidate_snapshot
    reviewed_hash = canonical_equality_sha256(reviewed_snapshot)
    if (
        review.candidate_snapshot_sha256 != reviewed_hash
        or pick.candidate_snapshot_sha256 != reviewed_hash
    ):
        raise OfficialPickAuthorizationValidationError(
            "candidate snapshot hash does not match the committed review"
        )
    if candidate is not None:
        requested_snapshot = candidate.to_candidate_snapshot()
        if not canonical_equal(requested_snapshot, reviewed_snapshot):
            raise OfficialPickAuthorizationValidationError(
                "promotion candidate differs from the approved frozen snapshot"
            )

    pick_values = pick.to_dict()
    for snapshot_field, pick_field in _SNAPSHOT_TO_PICK_FIELDS.items():
        if snapshot_field not in reviewed_snapshot:
            continue
        if not canonical_equal(
            reviewed_snapshot[snapshot_field],
            pick_values[pick_field],
        ):
            raise OfficialPickAuthorizationValidationError(
                f"official pick field {pick_field} differs from the "
                "approved frozen snapshot"
            )
    if not canonical_equal(
        reviewed_snapshot.get("record_kind"),
        PickRecordKind.MODEL_CANDIDATE.value,
    ):
        raise OfficialPickAuthorizationValidationError(
            "approved snapshot is not a MODEL_CANDIDATE"
        )

    reviewed_provenance = reviewed_snapshot.get("provenance")
    if not isinstance(reviewed_provenance, Mapping):
        raise OfficialPickAuthorizationValidationError(
            "approved snapshot provenance is malformed"
        )
    pick_provenance = canonical_json_value(pick.provenance)
    generated_provenance: dict[str, Any] = {
        "source_type": "CANDIDATE",
        "source_id": review.source_candidate_id,
        "promotion_service": "courtvision.official_picks",
        "promotion_actor": pick_provenance.get("promotion_actor"),
        "promotion_policy_version": OFFICIAL_PICK_PROMOTION_POLICY_VERSION,
        "review_id": review.review_id,
        "review_decision": OfficialPickOperatorDecision.APPROVED.value,
        "review_operator_id": review.operator_id,
        "review_run_id": review.review_run_id,
        "candidate_snapshot_sha256": reviewed_hash,
    }
    promotion_actor = generated_provenance["promotion_actor"]
    if not isinstance(promotion_actor, str) or not promotion_actor.strip():
        raise OfficialPickAuthorizationValidationError(
            "official pick provenance.promotion_actor is required"
        )
    normalized_reviewed_provenance = canonical_json_value(reviewed_provenance)
    for key in (
        set(normalized_reviewed_provenance)
        & OFFICIAL_PICK_GENERATED_PROVENANCE_KEYS
    ):
        if not canonical_equal(
            normalized_reviewed_provenance[key],
            generated_provenance[key],
        ):
            raise OfficialPickAuthorizationValidationError(
                f"reviewed provenance.{key} conflicts with generated authority"
            )
    expected_provenance = {
        **normalized_reviewed_provenance,
        **generated_provenance,
    }
    if set(pick_provenance) != set(expected_provenance):
        missing = sorted(set(expected_provenance) - set(pick_provenance))
        unexpected = sorted(set(pick_provenance) - set(expected_provenance))
        raise OfficialPickAuthorizationValidationError(
            "official pick provenance keys are not exact; "
            f"missing={missing}, unexpected={unexpected}"
        )
    if not canonical_equal(pick_provenance, expected_provenance):
        raise OfficialPickAuthorizationValidationError(
            "official pick provenance values differ from reviewed and "
            "generated authority"
        )

    if pick.published_at < review.reviewed_at:
        raise OfficialPickAuthorizationValidationError(
            "published_at precedes reviewed_at"
        )
    if pick.designation not in {
        OfficialPickDesignation.PAPER.value,
        OfficialPickDesignation.RESEARCH.value,
    }:
        raise OfficialPickAuthorizationValidationError(
            "designation must be PAPER or RESEARCH"
        )
    if pick.designation != review.approved_designation:
        raise OfficialPickAuthorizationValidationError(
            "designation differs from the approved designation"
        )
    if not canonical_equal(
        reviewed_snapshot.get("designation"),
        review.approved_designation,
    ):
        raise OfficialPickAuthorizationValidationError(
            "approved designation is not bound into the candidate snapshot"
        )


def validate_committed_schema_v2_review_authorization(
    lifecycle_root: str | Path,
    pick: OfficialPick,
    *,
    candidate: OfficialPickPromotionRequest | None = None,
) -> OfficialPickCandidateReview:
    """Reconstruct verified review state from *lifecycle_root* and authorize."""

    try:
        from courtvision.official_picks.review import (
            read_official_pick_candidate_review,
        )

        review = read_official_pick_candidate_review(
            Path(lifecycle_root),
            str(pick.review_id or ""),
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise OfficialPickAuthorizationValidationError(
            "unable to reconstruct verified committed review state: "
            f"{exc}"
        ) from exc
    if review is None:
        raise OfficialPickAuthorizationValidationError(
            "review_id is not committed in the target lifecycle root"
        )
    validate_schema_v2_review_authorization(
        pick,
        review,
        candidate=candidate,
    )
    return review


__all__ = [
    "OFFICIAL_PICK_GENERATED_PROVENANCE_KEYS",
    "OFFICIAL_PICK_PROCESS_TRANSACTION_LOCK",
    "OfficialPickAuthorizationValidationError",
    "validate_committed_schema_v2_review_authorization",
    "validate_schema_v2_review_authorization",
]
