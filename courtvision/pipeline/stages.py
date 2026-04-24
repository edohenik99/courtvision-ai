from __future__ import annotations

from typing import Any

from .contracts import StageResult


def stage(
    stage_name: str,
    *,
    status: str = "success",
    critical: bool = True,
    input_count: int | None = None,
    output_count: int | None = None,
    warnings: list[str] | None = None,
    artifacts: list[str] | None = None,
    fallback_used: bool = False,
    **notes: Any,
) -> StageResult:
    return StageResult(
        stage_name=stage_name,
        status=status,  # type: ignore[arg-type]
        critical=critical,
        input_count=input_count,
        output_count=output_count,
        warnings=list(warnings or []),
        artifacts=list(artifacts or []),
        fallback_used=fallback_used,
        notes=notes,
    )
