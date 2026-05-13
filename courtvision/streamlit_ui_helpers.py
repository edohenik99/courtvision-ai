"""Pure helpers for the CourtVision Streamlit UI.

These helpers are deliberately presentation-only so they can be tested without
importing Streamlit or touching prediction, grading, Kelly, or history code.
"""

from __future__ import annotations

import os
from collections.abc import Mapping


TRUE_ENV_VALUES = {"1", "true", "yes", "y", "on"}


def env_flag_enabled(name: str, env: Mapping[str, str] | None = None) -> bool:
    """Return True when an environment-style flag is explicitly enabled."""
    source = env if env is not None else os.environ
    value = str(source.get(name, "")).strip().lower()
    return value in TRUE_ENV_VALUES


def mutation_actions_enabled(view_only_demo: bool) -> bool:
    """Return whether UI mutation controls should be visible/enabled."""
    return not bool(view_only_demo)


def raw_diagnostics_visible(view_only_demo: bool) -> bool:
    """Return whether raw JSON/text diagnostics should be visible."""
    return not bool(view_only_demo)


def raw_review_artifacts_visible(view_only_demo: bool) -> bool:
    """Return whether raw review artifact text/JSON should be visible."""
    return not bool(view_only_demo)


def dataframe_height(
    row_count: int,
    *,
    max_height: int = 420,
    min_height: int = 120,
    row_height: int = 38,
    padding_rows: int = 2,
) -> int:
    """Return a compact bounded height for Streamlit dataframe previews."""
    safe_rows = max(0, int(row_count or 0))
    return int(min(max_height, max(min_height, row_height * (safe_rows + padding_rows))))


__all__ = [
    "TRUE_ENV_VALUES",
    "dataframe_height",
    "env_flag_enabled",
    "mutation_actions_enabled",
    "raw_diagnostics_visible",
    "raw_review_artifacts_visible",
]
