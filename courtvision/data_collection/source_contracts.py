"""Closed source contracts for raw data acquisition.

Contracts are code-owned allowlist entries.  Callers may provide files or
configuration for a contract, but may not introduce an arbitrary URL or web
source at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re


class SourceContractError(ValueError):
    """Raised when a source falls outside the collector allowlist contract."""


class AcquisitionMethod(StrEnum):
    SUPPLIED_FILE = "supplied_file"
    SUPPLIED_ARCHIVE = "supplied_archive"
    OFFICIAL_DOWNLOAD = "official_download"
    PYBASEBALL = "pybaseball"


_DISALLOWED_SOURCE_PATTERNS = (
    re.compile(r"stat\s*muse", re.IGNORECASE),
    re.compile(r"sportsbook[\s_-]*(?:web[\s_-]*)?scrap", re.IGNORECASE),
    re.compile(r"scrap(?:e|er|ing).*(?:sportsbook|bookmaker)", re.IGNORECASE),
)


def reject_disallowed_source(value: str) -> None:
    """Reject sources that would cross an explicit no-scrape boundary."""

    if any(pattern.search(value) for pattern in _DISALLOWED_SOURCE_PATTERNS):
        raise SourceContractError(
            "StatMuse and sportsbook scraping sources are not allowed"
        )


@dataclass(frozen=True, slots=True)
class SourceContract:
    """One approved, auditable source and its acquisition boundary."""

    source_name: str
    source_type: str
    source_url_provider: str
    license_terms_note: str
    acquisition_method: AcquisitionMethod
    required: bool = False
    allowed_extensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        text_fields = (
            self.source_name,
            self.source_type,
            self.source_url_provider,
            self.license_terms_note,
        )
        if any(not isinstance(value, str) or not value.strip() for value in text_fields):
            raise SourceContractError("source contract text fields must be non-empty")
        reject_disallowed_source(" ".join(text_fields))
        normalized = tuple(
            extension.lower() if extension.startswith(".") else f".{extension.lower()}"
            for extension in self.allowed_extensions
        )
        object.__setattr__(self, "allowed_extensions", normalized)

    def validate_input_path(self, path: str | Path) -> Path:
        """Validate a supplied file/archive without modifying it."""

        candidate = Path(path).expanduser().resolve()
        if not candidate.exists():
            raise FileNotFoundError(candidate)
        if not (candidate.is_file() or candidate.is_dir()):
            raise SourceContractError(f"source is not a regular file or directory: {candidate}")
        if candidate.is_file() and self.allowed_extensions:
            if candidate.suffix.lower() not in self.allowed_extensions:
                allowed = ", ".join(self.allowed_extensions)
                raise SourceContractError(
                    f"{self.source_name} requires one of [{allowed}]: {candidate}"
                )
        return candidate


__all__ = [
    "AcquisitionMethod",
    "SourceContract",
    "SourceContractError",
    "reject_disallowed_source",
]
