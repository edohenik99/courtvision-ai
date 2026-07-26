"""Narrow non-prediction facade for grading, history, and provider utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class PredictionOperationForbidden(RuntimeError):
    """Raised if a downstream workflow tries to create predictions."""


class CourtVisionOperations:
    """Expose operational helpers without exposing ``predict``.

    The legacy runtime remains the temporary implementation source for grading
    parity, but downstream scripts depend on this restricted facade and cannot
    enter the canonical prediction application.
    """

    def __init__(self, out_dir: str | Path = "outputs") -> None:
        from courtvision_ai import CourtVisionAI

        self._runtime = CourtVisionAI(out_dir=str(out_dir))

    @property
    def runtime_dir(self) -> Path:
        return self._runtime.runtime_dir

    @runtime_dir.setter
    def runtime_dir(self, value: str | Path) -> None:
        self._runtime.runtime_dir = Path(value)

    @property
    def runtime_history_dir(self) -> Path:
        return self._runtime.runtime_history_dir

    @runtime_history_dir.setter
    def runtime_history_dir(self, value: str | Path) -> None:
        self._runtime.runtime_history_dir = Path(value)

    @property
    def feedback_path(self) -> Path:
        return self._runtime.feedback_path

    @feedback_path.setter
    def feedback_path(self, value: str | Path) -> None:
        self._runtime.feedback_path = Path(value)

    def get_client(self) -> Any:
        return self._runtime._get_client()

    def normalize_stats(self, value: Any) -> Any:
        return self._runtime._normalize_stats(value)

    def auto_grade(self, prediction_date: str) -> Any:
        return self._runtime.auto_grade(prediction_date)

    def grade_single_prediction(self, *args: Any, **kwargs: Any) -> Any:
        return self._runtime._grade_single_prediction(*args, **kwargs)

    def append_history(self, *args: Any, **kwargs: Any) -> Any:
        return self._runtime._append_history(*args, **kwargs)

    def predict(self, *args: Any, **kwargs: Any) -> None:
        raise PredictionOperationForbidden(
            "non-prediction workflows cannot invoke prediction generation"
        )


def operations_client(operations: Any) -> Any:
    method = getattr(operations, "get_client", None)
    return method() if callable(method) else operations._get_client()


def normalize_operation_stats(operations: Any, value: Any) -> Any:
    method = getattr(operations, "normalize_stats", None)
    return method(value) if callable(method) else operations._normalize_stats(value)


def grade_operation_prediction(
    operations: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    method = getattr(operations, "grade_single_prediction", None)
    if callable(method):
        return method(*args, **kwargs)
    return operations._grade_single_prediction(*args, **kwargs)


def append_operation_history(
    operations: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    method = getattr(operations, "append_history", None)
    if callable(method):
        return method(*args, **kwargs)
    return operations._append_history(*args, **kwargs)


__all__ = [
    "CourtVisionOperations",
    "PredictionOperationForbidden",
    "append_operation_history",
    "grade_operation_prediction",
    "normalize_operation_stats",
    "operations_client",
]
