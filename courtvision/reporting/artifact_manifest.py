from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from courtvision.reporting.shadow_artifact_metadata import inspect_shadow_json_freshness


SEVERITY_FATAL = "fatal"
SEVERITY_WARNING = "warning"
SEVERITY_INFORMATIONAL = "informational"
SEVERITY_SHADOW_ONLY = "shadow_only"
SEVERITIES = (
    SEVERITY_FATAL,
    SEVERITY_WARNING,
    SEVERITY_INFORMATIONAL,
    SEVERITY_SHADOW_ONLY,
)
PHASE4B_SHADOW_DIAGNOSTIC_NAMES = frozenset(
    {
        "clv_market_movement_diagnostics",
        "calibration_bucket_report_diagnostics",
        "player_role_stability_report_diagnostics",
        "meta_label_promotion_shadow_diagnostics",
        "meta_label_rules_performance_json",
        "feature_completeness_tracker_json",
        "incubator_performance_report_json",
    }
)


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    category: str
    name: str
    lane: str
    filename: str
    severity: str
    notes: str


def _specs(prediction_date: str) -> tuple[ArtifactSpec, ...]:
    return (
        ArtifactSpec(
            "operator_core",
            "elite_board",
            "operator",
            f"elite_board_{prediction_date}.csv",
            SEVERITY_FATAL,
            "Core operator board expected after a normal prediction run.",
        ),
        ArtifactSpec(
            "operator_core",
            "full_market_board",
            "operator",
            f"full_market_board_{prediction_date}.csv",
            SEVERITY_FATAL,
            "Core operator board expected after a normal prediction run.",
        ),
        ArtifactSpec(
            "operator_core",
            "sgp_board",
            "operator",
            f"sgp_board_{prediction_date}.csv",
            SEVERITY_FATAL,
            "Core operator board expected after a normal prediction run.",
        ),
        ArtifactSpec(
            "operator_prediction_report",
            "top_plays_report",
            "operator",
            f"top_plays_report_{prediction_date}.txt",
            SEVERITY_WARNING,
            "Human-readable prediction report; investigate if absent after prediction.",
        ),
        ArtifactSpec(
            "operator_prediction_report",
            "elite_decision_report",
            "operator",
            f"elite_decision_report_{prediction_date}.txt",
            SEVERITY_WARNING,
            "Human-readable elite decision report; investigate if absent after prediction.",
        ),
        ArtifactSpec(
            "operator_prediction_report",
            "elite_pipeline_audit",
            "operator",
            f"elite_pipeline_audit_{prediction_date}.csv",
            SEVERITY_WARNING,
            "Elite candidate audit CSV expected for validation and review.",
        ),
        ArtifactSpec(
            "operator_prediction_report",
            "elite_pipeline_audit_summary",
            "operator",
            f"elite_pipeline_audit_summary_{prediction_date}.json",
            SEVERITY_WARNING,
            "Elite audit summary used by validation; missing summary should be investigated.",
        ),
        ArtifactSpec(
            "prediction_diagnostics",
            "board_diagnostics",
            "diagnostics",
            f"board_diagnostics_{prediction_date}.json",
            SEVERITY_WARNING,
            "Core diagnostics payload used by quality summary and operator card.",
        ),
        ArtifactSpec(
            "prediction_diagnostics",
            "market_coverage",
            "diagnostics",
            f"market_coverage_{prediction_date}.json",
            SEVERITY_WARNING,
            "Market coverage diagnostics for provider/odds visibility.",
        ),
        ArtifactSpec(
            "prediction_diagnostics",
            "player_points_elite_admission_csv",
            "diagnostics",
            f"player_points_elite_admission_{prediction_date}.csv",
            SEVERITY_INFORMATIONAL,
            "Player-points elite admission diagnostic CSV.",
        ),
        ArtifactSpec(
            "prediction_diagnostics",
            "player_points_elite_admission_json",
            "diagnostics",
            f"player_points_elite_admission_{prediction_date}.json",
            SEVERITY_INFORMATIONAL,
            "Player-points elite admission diagnostic summary.",
        ),
        ArtifactSpec(
            "prediction_diagnostics",
            "market_availability_audit_csv",
            "diagnostics",
            f"market_availability_audit_{prediction_date}.csv",
            SEVERITY_INFORMATIONAL,
            "Market availability diagnostic CSV.",
        ),
        ArtifactSpec(
            "prediction_diagnostics",
            "market_availability_audit_json",
            "diagnostics",
            f"market_availability_audit_{prediction_date}.json",
            SEVERITY_INFORMATIONAL,
            "Market availability diagnostic summary.",
        ),
        ArtifactSpec(
            "prediction_diagnostics",
            "market_performance_readiness",
            "diagnostics",
            f"market_performance_readiness_{prediction_date}.json",
            SEVERITY_INFORMATIONAL,
            "Market readiness diagnostic summary.",
        ),
        ArtifactSpec(
            "research",
            "player_predictions",
            "research",
            f"player_predictions_{prediction_date}.csv",
            SEVERITY_INFORMATIONAL,
            "Research artifact; absence should not block operator use.",
        ),
        ArtifactSpec(
            "research",
            "game_predictions",
            "research",
            f"game_predictions_{prediction_date}.csv",
            SEVERITY_INFORMATIONAL,
            "Research artifact; absence should not block operator use.",
        ),
        ArtifactSpec(
            "research",
            "player_edges",
            "research",
            f"player_edges_{prediction_date}.csv",
            SEVERITY_INFORMATIONAL,
            "Research artifact; absence should not block operator use.",
        ),
        ArtifactSpec(
            "research",
            "game_edges",
            "research",
            f"game_edges_{prediction_date}.csv",
            SEVERITY_INFORMATIONAL,
            "Research artifact; absence should not block operator use.",
        ),
        ArtifactSpec(
            "research",
            "model_metrics",
            "research",
            f"model_metrics_{prediction_date}.json",
            SEVERITY_INFORMATIONAL,
            "Research/model metrics artifact.",
        ),
        ArtifactSpec(
            "optional_verbose",
            "stat_only_board",
            "optional",
            f"stat_only_board_{prediction_date}.csv",
            SEVERITY_INFORMATIONAL,
            "Verbose/debug board; optional lane artifact and not a staking input.",
        ),
        ArtifactSpec(
            "optional_verbose",
            "strike_board",
            "optional",
            f"strike_board_{prediction_date}.csv",
            SEVERITY_INFORMATIONAL,
            "Verbose/debug board; optional lane artifact.",
        ),
        ArtifactSpec(
            "optional_verbose",
            "predictive_lines_board",
            "optional",
            f"predictive_lines_board_{prediction_date}.csv",
            SEVERITY_INFORMATIONAL,
            "Verbose/debug board; optional lane artifact.",
        ),
        ArtifactSpec(
            "optional_verbose",
            "team_board",
            "optional",
            f"team_board_{prediction_date}.csv",
            SEVERITY_INFORMATIONAL,
            "Verbose/debug board; optional lane artifact.",
        ),
        ArtifactSpec(
            "optional_verbose",
            "near_miss_board",
            "optional",
            f"near_miss_board_{prediction_date}.csv",
            SEVERITY_INFORMATIONAL,
            "Verbose/debug board; optional lane artifact.",
        ),
        ArtifactSpec(
            "post_run_operator",
            "kelly_stakes",
            "operator",
            f"kelly_stakes_{prediction_date}.csv",
            SEVERITY_WARNING,
            "Expected only when elite board has rows; absence is not fatal for no-bet slates.",
        ),
        ArtifactSpec(
            "post_run_operator",
            "daily_summary",
            "operator",
            f"daily_summary_{prediction_date}.txt",
            SEVERITY_WARNING,
            "Post-run operator summary.",
        ),
        ArtifactSpec(
            "post_run_operator",
            "quality_summary_txt",
            "operator",
            f"quality_summary_{prediction_date}.txt",
            SEVERITY_WARNING,
            "Post-run quality summary text.",
        ),
        ArtifactSpec(
            "post_run_operator",
            "quality_summary_json",
            "operator",
            f"quality_summary_{prediction_date}.json",
            SEVERITY_WARNING,
            "Post-run quality summary payload.",
        ),
        ArtifactSpec(
            "post_run_operator",
            "completion_state_audit_txt",
            "operator",
            f"completion_state_audit_{prediction_date}.txt",
            SEVERITY_WARNING,
            "Post-run completion audit text.",
        ),
        ArtifactSpec(
            "post_run_operator",
            "completion_state_audit_json",
            "diagnostics",
            f"completion_state_audit_{prediction_date}.json",
            SEVERITY_WARNING,
            "Post-run completion audit payload.",
        ),
        ArtifactSpec(
            "post_run_operator",
            "operator_card",
            "operator",
            f"operator_card_{prediction_date}.txt",
            SEVERITY_WARNING,
            "Final operator card.",
        ),
        ArtifactSpec(
            "validation_audit",
            "full_market_sanity_audit_txt",
            "operator",
            f"full_market_sanity_audit_{prediction_date}.txt",
            SEVERITY_WARNING,
            "Warning-path validation audit text.",
        ),
        ArtifactSpec(
            "validation_audit",
            "full_market_sanity_audit_json",
            "diagnostics",
            f"full_market_sanity_audit_{prediction_date}.json",
            SEVERITY_WARNING,
            "Warning-path validation audit payload.",
        ),
        ArtifactSpec(
            "validation_audit",
            "candidate_quality_drift_audit_txt",
            "operator",
            f"candidate_quality_drift_audit_{prediction_date}.txt",
            SEVERITY_WARNING,
            "Warning-path quality drift audit text.",
        ),
        ArtifactSpec(
            "validation_audit",
            "candidate_quality_drift_audit_json",
            "diagnostics",
            f"candidate_quality_drift_audit_{prediction_date}.json",
            SEVERITY_WARNING,
            "Warning-path quality drift audit payload.",
        ),
        ArtifactSpec(
            "shadow_only",
            "near_elite_review",
            "operator",
            f"near_elite_review_{prediction_date}.csv",
            SEVERITY_SHADOW_ONLY,
            "Review-only candidate surface; not an Elite, Kelly, SGP, or staking input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "market_shadow_report",
            "operator",
            f"market_shadow_report_{prediction_date}.txt",
            SEVERITY_SHADOW_ONLY,
            "Shadow/reporting artifact; not a betting input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "market_shadow_grading",
            "diagnostics",
            f"market_shadow_grading_{prediction_date}.json",
            SEVERITY_SHADOW_ONLY,
            "Shadow/reporting artifact; not a betting input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "clv_market_movement_report",
            "operator",
            f"clv_market_movement_{prediction_date}.txt",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only CLV market movement diagnostic; not an Elite, Kelly, SGP, or staking input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "clv_market_movement_diagnostics",
            "diagnostics",
            f"clv_market_movement_{prediction_date}.json",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only CLV market movement diagnostic; not an Elite, Kelly, SGP, or staking input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "calibration_bucket_report",
            "operator",
            f"calibration_bucket_report_{prediction_date}.txt",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only calibration bucket diagnostic; not an Elite, Kelly, SGP, final decision, or staking input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "calibration_bucket_report_diagnostics",
            "diagnostics",
            f"calibration_bucket_report_{prediction_date}.json",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only calibration bucket diagnostic; not an Elite, Kelly, SGP, final decision, or staking input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "player_role_stability_report",
            "operator",
            f"player_role_stability_{prediction_date}.txt",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only player role stability diagnostic; not an Elite, Kelly, SGP, final decision, or staking input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "player_role_stability_report_diagnostics",
            "diagnostics",
            f"player_role_stability_{prediction_date}.json",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only player role stability diagnostic; not an Elite, Kelly, SGP, final decision, or staking input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "meta_label_promotion_shadow_report",
            "operator",
            f"meta_label_promotion_shadow_{prediction_date}.txt",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only meta-label promotion baseline; not an Elite, Kelly, SGP, final decision, or staking input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "meta_label_promotion_shadow_diagnostics",
            "diagnostics",
            f"meta_label_promotion_shadow_{prediction_date}.json",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only meta-label promotion baseline; not an Elite, Kelly, SGP, final decision, or staking input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "meta_label_promotion_shadow_csv",
            "operator",
            f"meta_label_promotion_shadow_{prediction_date}.csv",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only meta-label promotion baseline; not an Elite, Kelly, SGP, final decision, or staking input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "meta_label_rules_performance_txt",
            "operator",
            f"meta_label_rules_performance_{prediction_date}.txt",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only meta-label rules performance baseline; not an Elite, Kelly, SGP, final decision, or staking input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "meta_label_rules_performance_json",
            "diagnostics",
            f"meta_label_rules_performance_{prediction_date}.json",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only meta-label rules performance baseline; not an Elite, Kelly, SGP, final decision, or staking input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "meta_label_rules_performance_csv",
            "operator",
            f"meta_label_rules_performance_{prediction_date}.csv",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only meta-label rules performance baseline; not an Elite, Kelly, SGP, final decision, or staking input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "high_caution_over_watchlist",
            "operator",
            f"high_caution_over_watchlist_{prediction_date}.csv",
            SEVERITY_SHADOW_ONLY,
            "Watchlist/reporting artifact; not a betting input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "incubator_board",
            "operator",
            f"incubator_board_{prediction_date}.csv",
            SEVERITY_SHADOW_ONLY,
            "Incubator/reporting artifact; not a betting input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "combo_under_watchlist",
            "operator",
            f"combo_under_watchlist_{prediction_date}.csv",
            SEVERITY_SHADOW_ONLY,
            "Watchlist/reporting artifact; not a betting input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "same_opponent_under_warnings",
            "operator",
            f"same_opponent_under_warnings_{prediction_date}.csv",
            SEVERITY_SHADOW_ONLY,
            "Watchlist/reporting artifact; not a betting input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "feature_completeness_tracker_txt",
            "operator",
            f"feature_completeness_tracker_{prediction_date}.txt",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only forward feature completeness tracker; not a betting input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "feature_completeness_tracker_json",
            "diagnostics",
            f"feature_completeness_tracker_{prediction_date}.json",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only forward feature completeness tracker; not a betting input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "feature_completeness_tracker_csv",
            "operator",
            f"feature_completeness_tracker_{prediction_date}.csv",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only forward feature completeness tracker; not a betting input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "incubator_performance_report_txt",
            "operator",
            f"incubator_performance_report_{prediction_date}.txt",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only incubator performance report text; not a betting input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "incubator_performance_report_json",
            "diagnostics",
            f"incubator_performance_report_{prediction_date}.json",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only incubator performance report diagnostics; not a betting input.",
        ),
        ArtifactSpec(
            "shadow_only",
            "incubator_performance_report_csv",
            "operator",
            f"incubator_performance_report_{prediction_date}.csv",
            SEVERITY_SHADOW_ONLY,
            "Shadow/report-only incubator performance report CSV; not a betting input.",
        ),
    )


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _csv_row_count(path: Path) -> tuple[int | None, str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            try:
                next(reader)
            except StopIteration:
                return 0, "empty_csv_file"
            return sum(1 for _row in reader), ""
    except Exception as exc:  # pragma: no cover - platform/encoding dependent
        return None, f"row_count_error:{exc}"


def _artifact_row(runtime_root: Path, spec: ArtifactSpec, *, prediction_date: str) -> dict[str, Any]:
    path = runtime_root / spec.lane / spec.filename
    exists = path.exists()
    notes: list[str] = [spec.notes]
    size_bytes: int | None = None
    row_count: int | None = None

    if exists:
        try:
            size_bytes = path.stat().st_size
        except OSError as exc:
            notes.append(f"stat_error:{exc}")
        if path.suffix.lower() == ".csv":
            row_count, row_note = _csv_row_count(path)
            if row_note:
                notes.append(row_note)
    else:
        notes.append("missing")

    row: dict[str, Any] = {
        "category": spec.category,
        "name": spec.name,
        "expected_path": str(path),
        "exists": bool(exists),
        "size_bytes": size_bytes,
        "row_count": row_count,
        "severity": spec.severity,
        "notes": "; ".join(note for note in notes if note),
    }
    if spec.name in PHASE4B_SHADOW_DIAGNOSTIC_NAMES:
        row.update(inspect_shadow_json_freshness(path, prediction_date=prediction_date))
    return row


def build_artifact_manifest(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a read-only artifact manifest for a prediction date."""
    runtime_root_path = Path(runtime_root)
    artifacts = [
        _artifact_row(runtime_root_path, spec, prediction_date=prediction_date)
        for spec in _specs(prediction_date)
    ]
    severity_counts = {severity: 0 for severity in SEVERITIES}
    missing_by_severity = {severity: 0 for severity in SEVERITIES}
    shadow_freshness_counts: dict[str, int] = {}
    for item in artifacts:
        severity = str(item["severity"])
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        if not item["exists"]:
            missing_by_severity[severity] = missing_by_severity.get(severity, 0) + 1
        freshness_status = item.get("freshness_status")
        if freshness_status:
            key = str(freshness_status)
            shadow_freshness_counts[key] = shadow_freshness_counts.get(key, 0) + 1

    if missing_by_severity.get(SEVERITY_FATAL, 0) > 0:
        status = "fatal_missing"
    elif missing_by_severity.get(SEVERITY_WARNING, 0) > 0:
        status = "warning_missing"
    else:
        status = "ok"

    return {
        "prediction_date": prediction_date,
        "generated_at": generated_at or _now_utc(),
        "runtime_root": str(runtime_root_path),
        "status": status,
        "artifact_count": len(artifacts),
        "severity_counts": severity_counts,
        "missing_by_severity": missing_by_severity,
        "shadow_freshness_counts": shadow_freshness_counts,
        "artifacts": artifacts,
    }


def render_artifact_manifest_text(manifest: dict[str, Any]) -> str:
    lines = [
        f"Artifact Manifest - {manifest.get('prediction_date', '')}",
        "=" * 72,
        f"generated_at: {manifest.get('generated_at', '')}",
        f"runtime_root: {manifest.get('runtime_root', '')}",
        f"status: {manifest.get('status', '')}",
        "",
        "Severity Counts",
        "-" * 72,
    ]
    severity_counts = manifest.get("severity_counts", {})
    missing_by_severity = manifest.get("missing_by_severity", {})
    for severity in SEVERITIES:
        lines.append(
            f"- {severity}: total={int(severity_counts.get(severity, 0) or 0)} "
            f"missing={int(missing_by_severity.get(severity, 0) or 0)}"
        )

    lines.extend(["", "Artifacts", "-" * 72])
    header = f"{'severity':13} {'exists':6} {'rows':>6} {'bytes':>8} {'category':28} name"
    lines.append(header)
    lines.append("-" * len(header))
    for item in manifest.get("artifacts", []):
        row_count = item.get("row_count")
        size_bytes = item.get("size_bytes")
        lines.append(
            f"{str(item.get('severity', '')):13} "
            f"{str(bool(item.get('exists', False))).lower():6} "
            f"{str(row_count if row_count is not None else ''):>6} "
            f"{str(size_bytes if size_bytes is not None else ''):>8} "
            f"{str(item.get('category', ''))[:28]:28} "
            f"{item.get('name', '')}"
        )
        if not item.get("exists"):
            lines.append(f"  missing_path: {item.get('expected_path', '')}")
        notes = str(item.get("notes", "")).strip()
        if notes:
            lines.append(f"  notes: {notes}")
        if item.get("freshness_status"):
            lines.append(
                "  freshness: "
                f"status={item.get('freshness_status')} "
                f"prediction_date_match={item.get('prediction_date_match')} "
                f"mtime_utc={item.get('mtime_utc') or 'n/a'} "
                f"generated_at_utc={item.get('generated_at_utc') or 'n/a'} "
                f"generated_by={item.get('generated_by') or 'n/a'} "
                f"orchestrator_run_id={item.get('orchestrator_run_id') or 'n/a'}"
            )
            if item.get("missing_metadata_fields"):
                lines.append(f"  missing_metadata_fields: {item.get('missing_metadata_fields')}")
    return "\n".join(lines) + "\n"


def write_artifact_manifest_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    generated_at: str | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    runtime_root_path = Path(runtime_root)
    manifest = build_artifact_manifest(
        prediction_date=prediction_date,
        runtime_root=runtime_root_path,
        generated_at=generated_at,
    )
    diagnostics_path = runtime_root_path / "diagnostics" / f"artifact_manifest_{prediction_date}.json"
    operator_path = runtime_root_path / "operator" / f"artifact_manifest_{prediction_date}.txt"
    diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
    operator_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostics_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    operator_path.write_text(render_artifact_manifest_text(manifest), encoding="utf-8")
    return operator_path, diagnostics_path, manifest


__all__ = [
    "SEVERITY_FATAL",
    "SEVERITY_WARNING",
    "SEVERITY_INFORMATIONAL",
    "SEVERITY_SHADOW_ONLY",
    "SEVERITIES",
    "PHASE4B_SHADOW_DIAGNOSTIC_NAMES",
    "build_artifact_manifest",
    "render_artifact_manifest_text",
    "write_artifact_manifest_outputs",
]
