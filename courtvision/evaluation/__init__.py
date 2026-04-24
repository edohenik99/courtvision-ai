"""Evaluation package for system performance measurement and validation.

VALIDATE + CALIBRATE mode - No new architectural features.

Provides:
- Report builder for rolling window analysis
- Performance metrics by bucket (confidence, edge, stat type)
- Market regime analysis
- Signal reliability tracking
- Export to CSV/JSON/TXT

Task A: Evaluation dashboard/report layer
"""

from __future__ import annotations

from courtvision.evaluation.report_builder import (
    EvaluationReport,
    ReportBuilder,
    RollingWindow,
)
from courtvision.evaluation.exporter import ReportExporter

__all__ = [
    "EvaluationReport",
    "ReportBuilder",
    "RollingWindow",
    "ReportExporter",
]
