"""Shadow-only immutable prediction lifecycle infrastructure.

The current CSV/runtime pipeline remains operationally authoritative.  Nothing
in this package participates in prediction qualification, selection, Kelly
sizing, grading, or settlement.
"""

from courtvision.lifecycle.clock import Clock, FixedClock, SystemClock, utc_now
from courtvision.lifecycle.models import (
    EventEnvelope,
    EventType,
    ReconciliationReport,
    ReconciliationStatus,
    RunManifest,
    RunMode,
)

__all__ = [
    "Clock",
    "EventEnvelope",
    "EventType",
    "FixedClock",
    "ReconciliationReport",
    "ReconciliationStatus",
    "RunManifest",
    "RunMode",
    "SystemClock",
    "utc_now",
]
