"""Paper trading / shadow run mode.

Runs the full system, records all decisions and context, without execution.
Enables audit and comparison against closing lines and results.

VALIDATE + CALIBRATE mode - Measurement and validation only.

Task C: Add "paper trading / shadow run" mode
"""

from __future__ import annotations

from courtvision.shadow_run.artifact import ShadowRunArtifact, ShadowRunEntry
from courtvision.shadow_run.runner import ShadowRunRunner

__all__ = [
    "ShadowRunArtifact",
    "ShadowRunEntry",
    "ShadowRunRunner",
]
