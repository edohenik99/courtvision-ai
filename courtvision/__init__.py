from __future__ import annotations

__all__ = ["CourtVisionPro"]


def __getattr__(name: str):
    if name == "CourtVisionPro":
        from .engine import CourtVisionPro

        return CourtVisionPro
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
