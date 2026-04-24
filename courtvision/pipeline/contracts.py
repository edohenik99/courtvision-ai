from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

StageStatus = Literal["success", "partial", "failed", "skipped"]


@dataclass(slots=True)
class StageResult:
    stage_name: str
    status: StageStatus
    critical: bool = True
    input_count: int | None = None
    output_count: int | None = None
    warnings: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    fallback_used: bool = False
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["warning_count"] = len(self.warnings)
        payload["artifact_count"] = len(self.artifacts)
        return payload


@dataclass(slots=True)
class PipelineManifest:
    run_type: Literal["prediction", "grading"]
    run_date: str
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    overall_status: StageStatus = "success"
    quality_status: str = "live"
    stages: list[StageResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def add_stage(self, stage: StageResult) -> None:
        self.stages.append(stage)
        if stage.status == "failed":
            self.overall_status = "failed"
        elif stage.status == "partial" and self.overall_status != "failed":
            self.overall_status = "partial"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_type": self.run_type,
            "run_date": self.run_date,
            "created_at_utc": self.created_at_utc,
            "overall_status": self.overall_status,
            "quality_status": self.quality_status,
            "summary": self.summary,
            "stages": [stage.to_dict() for stage in self.stages],
        }

    def default_path(self, out_dir: Path) -> Path:
        return out_dir / "manifests" / f"{self.run_type}_{self.run_date}.json"
