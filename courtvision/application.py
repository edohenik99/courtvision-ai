from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from courtvision.balldontlie_auth import BALLDONTLIE_API_KEY_ENV_VAR, resolve_api_key
from courtvision.pipeline.runner import (
    PipelineRunner,
    build_grading_manifest,
    build_prediction_manifest,
    stage_result_for_exception,
    write_manifest,
)
from courtvision.pipeline.stages import StageDefinition

LegacyRuntimeFactory = Callable[[str | Path], Any]


@dataclass(slots=True)
class PredictionRunResult:
    prediction_outputs: dict[str, Any]
    manifest_path: Path
    telegram_sent: bool = False


@dataclass(slots=True)
class GradingRunResult:
    graded_df: pd.DataFrame
    summary: dict[str, Any]
    manifest_path: Path


class CourtVisionApplication:
    """Application service that owns runtime orchestration.

    The legacy `CourtVisionAI` runtime still performs the core prediction and
    grading logic, but scripts and CLIs should depend on this layer instead of
    directly orchestrating the monolith.
    """

    def __init__(
        self,
        *,
        out_dir: str | Path,
        runtime_factory: LegacyRuntimeFactory,
        logger: logging.Logger | None = None,
    ) -> None:
        self.out_dir = Path(out_dir)
        self._runtime_factory = runtime_factory
        self.logger = logger or logging.getLogger("courtvision_application")

    def _resolve_runtime(self) -> Any:
        _, key_details = resolve_api_key(
            entrypoint="courtvision.application",
            env_var_name=BALLDONTLIE_API_KEY_ENV_VAR,
            logger=self.logger,
        )
        self.logger.info(
            "runtime_auth_resolved env_var=%s source=%s key=%s",
            key_details.get("env_var_name", BALLDONTLIE_API_KEY_ENV_VAR),
            key_details.get("source", "unknown"),
            key_details.get("masked_preview", "<empty>"),
        )
        return self._runtime_factory(self.out_dir)

    def run_prediction(self, prediction_date: str, *, send_telegram: bool = False) -> PredictionRunResult:
        context: dict[str, Any] = {
            "prediction_date": prediction_date,
            "out_dir": self.out_dir,
            "send_telegram": send_telegram,
            "runtime": None,
            "prediction_outputs": None,
            "manifest_path": None,
            "telegram_sent": False,
        }

        stages = [
            StageDefinition(name="resolve_runtime", handler=self._stage_resolve_runtime),
            StageDefinition(name="run_prediction", handler=self._stage_run_prediction),
            StageDefinition(name="send_telegram", handler=self._stage_send_telegram, critical=False, enabled=send_telegram),
            StageDefinition(name="write_manifest", handler=self._stage_write_prediction_manifest, critical=False),
        ]
        PipelineRunner(self.logger).run(stages, context)
        return PredictionRunResult(
            prediction_outputs=dict(context["prediction_outputs"] or {}),
            manifest_path=Path(context["manifest_path"]),
            telegram_sent=bool(context.get("telegram_sent", False)),
        )

    def run_grading(self, prediction_date: str) -> GradingRunResult:
        context: dict[str, Any] = {
            "prediction_date": prediction_date,
            "out_dir": self.out_dir,
            "runtime": None,
            "graded_df": pd.DataFrame(),
            "summary": {},
            "manifest_path": None,
        }
        stages = [
            StageDefinition(name="resolve_runtime", handler=self._stage_resolve_runtime),
            StageDefinition(name="run_grading", handler=self._stage_run_grading),
            StageDefinition(name="write_manifest", handler=self._stage_write_grading_manifest, critical=False),
        ]
        PipelineRunner(self.logger).run(stages, context)
        return GradingRunResult(
            graded_df=context["graded_df"],
            summary=dict(context.get("summary", {})),
            manifest_path=Path(context["manifest_path"]),
        )

    def _stage_resolve_runtime(self, context: dict[str, Any]) -> dict[str, Any]:
        runtime = self._resolve_runtime()
        context["runtime"] = runtime
        return {"notes": {"runtime_class": runtime.__class__.__name__}}

    def _stage_run_prediction(self, context: dict[str, Any]) -> dict[str, Any]:
        runtime = context["runtime"]
        prediction_date = str(context["prediction_date"])
        outputs = runtime.predict(prediction_date)
        context["prediction_outputs"] = outputs
        summary = dict(outputs.get("summary", {}))
        return {
            "output_count": int(summary.get("selected_count", 0) or 0),
            "notes": {
                "elite_count": int(summary.get("elite_count", 0) or 0),
                "market_quality_status": summary.get("market_quality_status"),
            },
        }

    def _stage_send_telegram(self, context: dict[str, Any]) -> dict[str, Any]:
        runtime = context["runtime"]
        outputs = dict(context.get("prediction_outputs") or {})
        selected_df = outputs.get("selected_props")
        if not isinstance(selected_df, pd.DataFrame):
            selected_df = pd.DataFrame()
        sent = runtime.send_telegram_top_plays(
            prediction_date=str(context["prediction_date"]),
            selected_df=selected_df,
            summary=dict(outputs.get("summary", {})),
        )
        context["telegram_sent"] = bool(sent)
        return {
            "status": "success" if sent else "skipped",
            "notes": {"telegram_sent": bool(sent)},
        }

    def _stage_write_prediction_manifest(self, context: dict[str, Any]) -> dict[str, Any]:
        outputs = dict(context.get("prediction_outputs") or {})
        manifest = build_prediction_manifest(str(context["prediction_date"]), outputs)
        manifest_path = write_manifest(manifest, context["out_dir"])
        context["manifest_path"] = manifest_path
        return {"artifacts": [str(manifest_path)]}

    def _stage_run_grading(self, context: dict[str, Any]) -> dict[str, Any]:
        runtime = context["runtime"]
        prediction_date = str(context["prediction_date"])
        graded_df = runtime.auto_grade(prediction_date)
        summary = {
            "graded_rows": int(len(graded_df)),
        }
        if not graded_df.empty and "hit" in graded_df.columns:
            hit_rate = pd.to_numeric(graded_df["hit"], errors="coerce").mean()
            summary["hit_rate"] = round(float(hit_rate), 4) if pd.notna(hit_rate) else None
        context["graded_df"] = graded_df
        context["summary"] = summary
        return {"output_count": int(len(graded_df)), "notes": summary}

    def _stage_write_grading_manifest(self, context: dict[str, Any]) -> dict[str, Any]:
        manifest = build_grading_manifest(
            str(context["prediction_date"]),
            context.get("graded_df", pd.DataFrame()),
            dict(context.get("summary", {})),
        )
        manifest_path = write_manifest(manifest, context["out_dir"])
        context["manifest_path"] = manifest_path
        return {"artifacts": [str(manifest_path)]}
