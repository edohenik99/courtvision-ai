"""Immutable safety constants for unvalidated MLB home-run research."""

from __future__ import annotations


MLB_RESEARCH_MODE = "research"
MLB_BETTING_APPROVAL_STATUS = "research_only_not_betting_approved"
MLB_NO_BETTING_REASON = (
    "MLB HR mode is unvalidated research-only. Historical training, calibration, "
    "EV validation, and promotion approval are required before betting use."
)


def mlb_research_safety_fields() -> dict[str, object]:
    """Return a fresh, non-overrideable serialization payload."""

    return {
        "mode": MLB_RESEARCH_MODE,
        "eligible_for_betting": False,
        "kelly_eligible": False,
        "betting_approval_status": MLB_BETTING_APPROVAL_STATUS,
        "no_betting_reason": MLB_NO_BETTING_REASON,
    }


__all__ = [
    "MLB_BETTING_APPROVAL_STATUS",
    "MLB_NO_BETTING_REASON",
    "MLB_RESEARCH_MODE",
    "mlb_research_safety_fields",
]
