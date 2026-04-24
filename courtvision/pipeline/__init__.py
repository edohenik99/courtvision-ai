"""Pipeline primitives for the cleaned CourtVision runtime.

Phase 4: Package-owned prediction pipeline with delegation to specialized modules.
"""

from .contracts import PipelineManifest, StageResult, StageStatus
from .predict_pipeline import (
    PredictionConfig,
    PredictionPipeline,
    PredictionResult,
    run_prediction_pipeline,
)
from .runner import (
    build_grading_manifest,
    build_prediction_manifest,
    save_prediction_boards,
    write_manifest,
)

__all__ = [
    # contracts
    "PipelineManifest",
    "StageResult",
    "StageStatus",
    # prediction pipeline (Phase 4)
    "PredictionConfig",
    "PredictionPipeline",
    "PredictionResult",
    "run_prediction_pipeline",
    # runner utilities
    "build_prediction_manifest",
    "build_grading_manifest",
    "write_manifest",
    "save_prediction_boards",
]
