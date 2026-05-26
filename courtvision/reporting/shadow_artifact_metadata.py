from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FRESHNESS_FRESH = "fresh"
FRESHNESS_MISSING = "missing"
FRESHNESS_STALE_DATE = "stale_date"
FRESHNESS_MISSING_METADATA = "missing_metadata"
FRESHNESS_UNREADABLE = "unreadable"
FRESHNESS_UNKNOWN = "unknown"

FRESHNESS_UNAVAILABLE_STATUSES = {
    FRESHNESS_MISSING,
    FRESHNESS_STALE_DATE,
    FRESHNESS_UNREADABLE,
}

REQUIRED_SHADOW_METADATA_FIELDS = (
    "prediction_date",
    "generated_at_utc",
    "generated_by",
    "source_runtime_root",
    "report_name",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_mtime_iso(path: Path) -> str | None:
    try:
        return (
            datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except OSError:
        return None


def shadow_orchestrator_run_id(*, prediction_date: str, generated_at_utc: str | None = None) -> str:
    generated = generated_at_utc or utc_now_iso()
    compact = (
        generated.replace("-", "")
        .replace(":", "")
        .replace("+0000", "Z")
        .replace("+00:00", "Z")
    )
    if compact.endswith("Z"):
        compact = compact[:-1] + "Z"
    return f"shadow_artifacts_{prediction_date}_{compact}"


def apply_shadow_report_metadata(
    payload: dict[str, Any],
    *,
    prediction_date: str,
    report_name: str,
    source_runtime_root: str | Path,
    history_root: str | Path | None = None,
    source_history_root: str | Path | None = None,
    generated_at_utc: str | None = None,
    generated_by: str,
    orchestrator_run_id: str | None = None,
) -> dict[str, Any]:
    payload["prediction_date"] = prediction_date
    payload["generated_at_utc"] = generated_at_utc or utc_now_iso()
    payload["generated_by"] = generated_by
    payload["source_runtime_root"] = str(Path(source_runtime_root))
    selected_history_root = source_history_root if source_history_root is not None else history_root
    if selected_history_root is not None:
        payload["source_history_root"] = str(Path(selected_history_root))
    payload["report_name"] = report_name
    if orchestrator_run_id:
        payload["orchestrator_run_id"] = orchestrator_run_id
    return payload


def inspect_shadow_json_freshness(path: str | Path, *, prediction_date: str) -> dict[str, Any]:
    path = Path(path)
    exists = path.exists()
    freshness: dict[str, Any] = {
        "exists": bool(exists),
        "mtime_utc": utc_mtime_iso(path) if exists else None,
        "prediction_date_match": "unknown",
        "generated_at_utc": None,
        "generated_by": None,
        "orchestrator_run_id": None,
        "report_name": None,
        "report_version": None,
        "source_runtime_root": None,
        "source_history_root": None,
        "freshness_status": FRESHNESS_MISSING if not exists else FRESHNESS_UNKNOWN,
    }
    if not exists:
        return freshness

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        freshness["freshness_status"] = FRESHNESS_UNREADABLE
        return freshness
    if not isinstance(payload, dict):
        freshness["freshness_status"] = FRESHNESS_UNREADABLE
        return freshness

    artifact_prediction_date = payload.get("prediction_date")
    if artifact_prediction_date is not None and str(artifact_prediction_date).strip():
        freshness["prediction_date_match"] = str(artifact_prediction_date).strip() == str(prediction_date)

    for field in (
        "generated_at_utc",
        "generated_by",
        "orchestrator_run_id",
        "report_name",
        "report_version",
        "source_runtime_root",
        "source_history_root",
    ):
        freshness[field] = payload.get(field)

    if freshness["prediction_date_match"] is False:
        freshness["freshness_status"] = FRESHNESS_STALE_DATE
        return freshness

    missing_required = [
        field
        for field in REQUIRED_SHADOW_METADATA_FIELDS
        if payload.get(field) is None or str(payload.get(field)).strip() == ""
    ]
    if missing_required:
        freshness["freshness_status"] = FRESHNESS_MISSING_METADATA
        freshness["missing_metadata_fields"] = missing_required
        return freshness

    if freshness["prediction_date_match"] is True:
        freshness["freshness_status"] = FRESHNESS_FRESH
    else:
        freshness["freshness_status"] = FRESHNESS_UNKNOWN
    return freshness


def shadow_json_unavailable_or_stale(freshness: dict[str, Any]) -> bool:
    return (
        freshness.get("prediction_date_match") is False
        or str(freshness.get("freshness_status", "")).strip() in FRESHNESS_UNAVAILABLE_STATUSES
    )


__all__ = [
    "FRESHNESS_FRESH",
    "FRESHNESS_MISSING",
    "FRESHNESS_MISSING_METADATA",
    "FRESHNESS_STALE_DATE",
    "FRESHNESS_UNKNOWN",
    "FRESHNESS_UNREADABLE",
    "FRESHNESS_UNAVAILABLE_STATUSES",
    "apply_shadow_report_metadata",
    "inspect_shadow_json_freshness",
    "shadow_json_unavailable_or_stale",
    "shadow_orchestrator_run_id",
    "utc_mtime_iso",
    "utc_now_iso",
]
