from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO

from courtvision.reporting.calibration_bucket_report import (
    calibration_bucket_json_path_for_date,
    calibration_bucket_txt_path_for_date,
    write_calibration_bucket_report,
)
from courtvision.reporting.clv_market_movement import (
    clv_market_movement_json_path_for_date,
    clv_market_movement_txt_path_for_date,
    write_clv_market_movement_report,
)
from courtvision.reporting.feature_completeness_tracker import (
    performance_csv_path_for_date as feature_completeness_csv_path_for_date,
    performance_json_path_for_date as feature_completeness_json_path_for_date,
    performance_txt_path_for_date as feature_completeness_txt_path_for_date,
    write_feature_completeness_report,
)
from courtvision.reporting.meta_label_promotion import (
    meta_label_promotion_csv_path_for_date,
    meta_label_promotion_json_path_for_date,
    meta_label_promotion_txt_path_for_date,
    write_meta_label_promotion_report,
)
from courtvision.reporting.meta_label_rules_performance import (
    performance_csv_path_for_date as rules_performance_csv_path_for_date,
    performance_json_path_for_date as rules_performance_json_path_for_date,
    performance_txt_path_for_date as rules_performance_txt_path_for_date,
    write_rules_performance_report,
)
from courtvision.reporting.player_role_stability import (
    player_role_stability_json_path_for_date,
    player_role_stability_txt_path_for_date,
    write_player_role_stability_report,
)
from courtvision.reporting.shadow_artifact_metadata import (
    shadow_orchestrator_run_id,
    utc_now_iso,
)


GENERATED_BY = "courtvision.reporting.shadow_artifact_orchestrator.write_shadow_artifacts"
PHASE4B_SHADOW_REPORT_ORDER: tuple[str, ...] = (
    "clv_market_movement",
    "calibration_bucket_report",
    "player_role_stability",
    "meta_label_promotion",
    "meta_label_rules_performance",
    "feature_completeness_tracker",
)


@dataclass(frozen=True, slots=True)
class ShadowArtifactPaths:
    txt_path: Path
    json_path: Path
    csv_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ShadowArtifactResult:
    report_name: str
    status: str
    txt_path: str
    json_path: str
    csv_path: str | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "report_name": self.report_name,
            "status": self.status,
            "txt_path": self.txt_path,
            "json_path": self.json_path,
            "csv_path": self.csv_path,
            "error_message": self.error_message,
        }


def shadow_artifact_paths(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> dict[str, ShadowArtifactPaths]:
    runtime_root = Path(runtime_root)
    return {
        "clv_market_movement": ShadowArtifactPaths(
            txt_path=clv_market_movement_txt_path_for_date(prediction_date, runtime_root),
            json_path=clv_market_movement_json_path_for_date(prediction_date, runtime_root),
        ),
        "calibration_bucket_report": ShadowArtifactPaths(
            txt_path=calibration_bucket_txt_path_for_date(prediction_date, runtime_root),
            json_path=calibration_bucket_json_path_for_date(prediction_date, runtime_root),
        ),
        "player_role_stability": ShadowArtifactPaths(
            txt_path=player_role_stability_txt_path_for_date(prediction_date, runtime_root),
            json_path=player_role_stability_json_path_for_date(prediction_date, runtime_root),
        ),
        "meta_label_promotion": ShadowArtifactPaths(
            txt_path=meta_label_promotion_txt_path_for_date(prediction_date, runtime_root),
            json_path=meta_label_promotion_json_path_for_date(prediction_date, runtime_root),
            csv_path=meta_label_promotion_csv_path_for_date(prediction_date, runtime_root),
        ),
        "meta_label_rules_performance": ShadowArtifactPaths(
            txt_path=rules_performance_txt_path_for_date(prediction_date, runtime_root),
            json_path=rules_performance_json_path_for_date(prediction_date, runtime_root),
            csv_path=rules_performance_csv_path_for_date(prediction_date, runtime_root),
        ),
        "feature_completeness_tracker": ShadowArtifactPaths(
            txt_path=feature_completeness_txt_path_for_date(prediction_date, runtime_root),
            json_path=feature_completeness_json_path_for_date(prediction_date, runtime_root),
            csv_path=feature_completeness_csv_path_for_date(prediction_date, runtime_root),
        ),
    }


def _result(
    *,
    report_name: str,
    status: str,
    paths: ShadowArtifactPaths,
    error_message: str | None = None,
) -> ShadowArtifactResult:
    return ShadowArtifactResult(
        report_name=report_name,
        status=status,
        txt_path=str(paths.txt_path),
        json_path=str(paths.json_path),
        csv_path=str(paths.csv_path) if paths.csv_path is not None else None,
        error_message=error_message,
    )


def _run_writer(
    *,
    report_name: str,
    paths: ShadowArtifactPaths,
    writer: Callable[[], tuple[Any, ...]],
    stderr: TextIO,
) -> tuple[ShadowArtifactResult, dict[str, Any]]:
    try:
        output = writer()
    except Exception as exc:
        print(f"[shadow_artifacts] {report_name} failed: {exc}", file=stderr)
        traceback.print_exc(file=stderr)
        return (
            _result(
                report_name=report_name,
                status="failed",
                paths=paths,
                error_message=str(exc),
            ),
            {},
        )

    payload = output[-1] if output else {}
    return (
        _result(report_name=report_name, status="written", paths=paths),
        payload if isinstance(payload, dict) else {},
    )


def write_shadow_artifacts(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    closed_slate_safe: bool = False,
    stderr: TextIO | None = None,
) -> dict[str, Any]:
    """Write all Phase 4B shadow artifacts in dependency order.

    The reports are diagnostics-only. This function does not alter prediction
    logic, Elite gates, Kelly sizing, bankroll logic, board generation, or final
    decisions.
    """
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)
    stderr = stderr or sys.stderr
    paths_by_report = shadow_artifact_paths(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    generated_at_utc = utc_now_iso()
    orchestrator_run_id = shadow_orchestrator_run_id(
        prediction_date=prediction_date,
        generated_at_utc=generated_at_utc,
    )

    results: list[ShadowArtifactResult] = []
    payloads: dict[str, dict[str, Any]] = {}

    result, payload = _run_writer(
        report_name="clv_market_movement",
        paths=paths_by_report["clv_market_movement"],
        writer=lambda: write_clv_market_movement_report(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
            generated_at_utc=generated_at_utc,
            generated_by=GENERATED_BY,
            source_runtime_root=runtime_root,
            report_name="clv_market_movement",
            orchestrator_run_id=orchestrator_run_id,
        ),
        stderr=stderr,
    )
    results.append(result)
    payloads["clv_market_movement"] = payload

    result, payload = _run_writer(
        report_name="calibration_bucket_report",
        paths=paths_by_report["calibration_bucket_report"],
        writer=lambda: write_calibration_bucket_report(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
            history_root=history_root,
            generated_at_utc=generated_at_utc,
            generated_by=GENERATED_BY,
            source_runtime_root=runtime_root,
            source_history_root=history_root,
            report_name="calibration_bucket_report",
            orchestrator_run_id=orchestrator_run_id,
        ),
        stderr=stderr,
    )
    results.append(result)
    payloads["calibration_bucket_report"] = payload

    result, payload = _run_writer(
        report_name="player_role_stability",
        paths=paths_by_report["player_role_stability"],
        writer=lambda: write_player_role_stability_report(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
            history_root=history_root,
            generated_at_utc=generated_at_utc,
            generated_by=GENERATED_BY,
            source_runtime_root=runtime_root,
            source_history_root=history_root,
            report_name="player_role_stability",
            orchestrator_run_id=orchestrator_run_id,
        ),
        stderr=stderr,
    )
    results.append(result)
    payloads["player_role_stability"] = payload

    result, payload = _run_writer(
        report_name="meta_label_promotion",
        paths=paths_by_report["meta_label_promotion"],
        writer=lambda: write_meta_label_promotion_report(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
            history_root=history_root,
            role_payload=payloads.get("player_role_stability") or None,
            cal_payload=payloads.get("calibration_bucket_report") or None,
            generated_at_utc=generated_at_utc,
            generated_by=GENERATED_BY,
            source_runtime_root=runtime_root,
            source_history_root=history_root,
            report_name="meta_label_promotion",
            orchestrator_run_id=orchestrator_run_id,
        ),
        stderr=stderr,
    )
    results.append(result)
    payloads["meta_label_promotion"] = payload

    result, payload = _run_writer(
        report_name="meta_label_rules_performance",
        paths=paths_by_report["meta_label_rules_performance"],
        writer=lambda: write_rules_performance_report(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
            history_root=history_root,
            generated_at_utc=generated_at_utc,
            generated_by=GENERATED_BY,
            source_runtime_root=runtime_root,
            source_history_root=history_root,
            report_name="meta_label_rules_performance",
            orchestrator_run_id=orchestrator_run_id,
        ),
        stderr=stderr,
    )
    results.append(result)
    payloads["meta_label_rules_performance"] = payload

    result, payload = _run_writer(
        report_name="feature_completeness_tracker",
        paths=paths_by_report["feature_completeness_tracker"],
        writer=lambda: write_feature_completeness_report(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
            history_root=history_root,
            generated_at_utc=generated_at_utc,
            generated_by=GENERATED_BY,
            source_runtime_root=runtime_root,
            source_history_root=history_root,
            report_name="feature_completeness_tracker",
            orchestrator_run_id=orchestrator_run_id,
        ),
        stderr=stderr,
    )
    results.append(result)
    payloads["feature_completeness_tracker"] = payload

    reports = [result.as_dict() for result in results]
    failed_count = sum(1 for report in reports if report["status"] == "failed")
    return {
        "prediction_date": prediction_date,
        "runtime_root": str(runtime_root),
        "history_root": str(history_root),
        "closed_slate_safe": bool(closed_slate_safe),
        "generated_at_utc": generated_at_utc,
        "generated_by": GENERATED_BY,
        "orchestrator_run_id": orchestrator_run_id,
        "status": "completed_with_failures" if failed_count else "completed",
        "failed_count": failed_count,
        "reports": reports,
    }


__all__ = [
    "PHASE4B_SHADOW_REPORT_ORDER",
    "ShadowArtifactPaths",
    "ShadowArtifactResult",
    "shadow_artifact_paths",
    "write_shadow_artifacts",
]
