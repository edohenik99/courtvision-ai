"""One injectable UTC clock for all new lifecycle code."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return a timezone-aware UTC datetime."""


def _require_aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("clock values must be datetime instances")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("lifecycle timestamps must be timezone-aware")
    return value.astimezone(UTC)


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""

    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class SystemClock:
    def now(self) -> datetime:
        return utc_now()


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_aware_utc(self.value))

    def now(self) -> datetime:
        return self.value


__all__ = ["Clock", "FixedClock", "SystemClock", "utc_now"]
