"""Default-deny output path guards for raw collections."""

from __future__ import annotations

from pathlib import Path
import re


class ProtectedPathError(ValueError):
    """Raised when a collection target overlaps a protected data category."""


PROTECTED_PATH_COMPONENTS = frozenset(
    {
        ".pytest-cache",
        "__pycache__",
        "cache",
        "caches",
        "history",
        "logs",
        "manual-data",
        "manualdata",
        "outputs",
        "runtime",
        "test-output",
        "test-outputs",
    }
)


def _normalized_component(value: str) -> str:
    return re.sub(r"[-_]+", "-", value.strip().lower())


def validate_output_root(path: str | Path) -> Path:
    """Resolve and validate the caller's explicit raw-data output root."""

    root = Path(path).expanduser().resolve()
    protected = [
        part
        for part in root.parts
        if _normalized_component(part) in PROTECTED_PATH_COMPONENTS
    ]
    if protected:
        raise ProtectedPathError(
            f"raw collection output uses protected path component: {protected[-1]}"
        )
    if root.exists() and not root.is_dir():
        raise ProtectedPathError(f"raw collection output root is not a directory: {root}")
    return root


def ensure_within_output_root(path: str | Path, output_root: str | Path) -> Path:
    """Require a prospective write path to remain beneath the explicit root."""

    root = validate_output_root(output_root)
    candidate = Path(path).expanduser().resolve()
    if candidate == root or root not in candidate.parents:
        raise ProtectedPathError(f"write target escapes raw collection root: {candidate}")
    return candidate


__all__ = [
    "PROTECTED_PATH_COMPONENTS",
    "ProtectedPathError",
    "ensure_within_output_root",
    "validate_output_root",
]
