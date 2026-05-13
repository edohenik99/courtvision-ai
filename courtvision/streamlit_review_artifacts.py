"""Streamlit-side loaders for quality review runtime artifacts.

This module is intentionally read-only. It resolves and loads already-emitted
operator artifacts for the Streamlit UI without invoking prediction, grading,
Kelly, suppression, or report generation code.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd


QUALITY_SUMMARY_ARTIFACTS: dict[str, dict[str, str]] = {
    "quality_summary_text": {
        "folder": "operator",
        "template": "quality_summary_{date}.txt",
        "label": "Quality summary",
        "kind": "text",
    },
    "quality_summary_json": {
        "folder": "operator",
        "template": "quality_summary_{date}.json",
        "label": "Quality summary JSON",
        "kind": "json",
    },
}

PHASE15_REVIEW_ARTIFACTS: dict[str, dict[str, str]] = {
    "phase15d_review": {
        "title": "LOW-LINE OVER MINUTES GUARD REVIEW (Phase 15D -- REVIEW ONLY)",
        "short_title": "Phase 15D guard review",
        "mode": "REVIEW ONLY",
        "summary_key": "low_line_over_minutes_guard_review",
        "text_template": "low_line_over_minutes_guard_review_{date}.txt",
        "csv_template": "low_line_over_minutes_guard_review_{date}.csv",
    },
    "phase15e_outcome": {
        "title": "LOW-LINE OVER MINUTES GUARD OUTCOME VALIDATION (Phase 15E -- REVIEW ONLY)",
        "short_title": "Phase 15E outcome validation",
        "mode": "REVIEW ONLY",
        "summary_key": "low_line_over_minutes_guard_outcome",
        "text_template": "low_line_over_minutes_guard_outcome_{date}.txt",
        "csv_template": "low_line_over_minutes_guard_outcome_{date}.csv",
    },
    "phase15f_policy_simulation": {
        "title": "LOW-LINE OVER MINUTES GUARD POLICY SIMULATION (Phase 15F -- SIMULATION ONLY)",
        "short_title": "Phase 15F policy simulation",
        "mode": "SIMULATION ONLY",
        "summary_key": "low_line_over_minutes_guard_policy_simulation",
        "text_template": "low_line_over_minutes_guard_policy_simulation_{date}.txt",
        "csv_template": "low_line_over_minutes_guard_policy_simulation_{date}.csv",
    },
    "phase15g_missed_winner_attribution": {
        "title": "LOW-LINE OVER MINUTES GUARD MISSED WINNER ATTRIBUTION (Phase 15G -- REVIEW ONLY)",
        "short_title": "Phase 15G missed-winner attribution",
        "mode": "REVIEW ONLY",
        "summary_key": "low_line_over_minutes_guard_missed_winner_attribution",
        "text_template": "low_line_over_minutes_guard_missed_winner_attribution_{date}.txt",
        "csv_template": "low_line_over_minutes_guard_missed_winner_attribution_{date}.csv",
    },
}

PHASE15_READINESS_LABELS: dict[str, str] = {
    "phase15d_review": "Phase 15D readiness",
    "phase15e_outcome": "Phase 15E readiness",
    "phase15f_policy_simulation": "Phase 15F readiness",
    "phase15g_missed_winner_attribution": "Phase 15G readiness",
}


def resolve_output_dir(out_dir: str | Path, repo_root: str | Path | None = None) -> Path:
    """Resolve an output directory, using repo_root for relative paths."""
    path = Path(str(out_dir).strip() or "outputs")
    if path.is_absolute():
        return path
    base = Path(repo_root) if repo_root is not None else Path.cwd()
    return base / path


def normalize_prediction_date_text(value: Any) -> str:
    """Return YYYY-MM-DD text for artifact filenames."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text:
        return date.today().isoformat()

    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return text


def runtime_artifact_path(
    out_dir: str | Path,
    prediction_date_text: Any,
    folder: str,
    template: str,
    repo_root: str | Path | None = None,
) -> Path:
    """Resolve one dated runtime artifact path."""
    date_text = normalize_prediction_date_text(prediction_date_text)
    return resolve_output_dir(out_dir, repo_root=repo_root) / "runtime" / folder / template.format(date=date_text)


def quality_review_artifact_paths(
    out_dir: str | Path,
    prediction_date_text: Any,
    repo_root: str | Path | None = None,
) -> dict[str, Path]:
    """Return all Quality Review artifact paths for the selected date."""
    paths: dict[str, Path] = {}
    for key, spec in QUALITY_SUMMARY_ARTIFACTS.items():
        paths[key] = runtime_artifact_path(
            out_dir,
            prediction_date_text,
            spec["folder"],
            spec["template"],
            repo_root=repo_root,
        )
    for phase_key, spec in PHASE15_REVIEW_ARTIFACTS.items():
        paths[f"{phase_key}_text"] = runtime_artifact_path(
            out_dir,
            prediction_date_text,
            "operator",
            spec["text_template"],
            repo_root=repo_root,
        )
        paths[f"{phase_key}_csv"] = runtime_artifact_path(
            out_dir,
            prediction_date_text,
            "operator",
            spec["csv_template"],
            repo_root=repo_root,
        )
    return paths


def _file_record(label: str, path: Path, kind: str) -> dict[str, Any]:
    exists = path.exists()
    stat = path.stat() if exists else None
    return {
        "label": label,
        "kind": kind,
        "path": str(path.resolve()),
        "exists": bool(exists),
        "bytes": int(stat.st_size) if stat else 0,
        "modified": (
            datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
            if stat
            else ""
        ),
        "rows": None,
        "columns": None,
        "status": "found" if exists else "missing",
        "error": "",
    }


def read_runtime_csv(label: str, path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read a CSV artifact safely, returning an empty frame on missing/error."""
    record = _file_record(label, path, "csv")
    if not record["exists"]:
        return pd.DataFrame(), record

    try:
        df = pd.read_csv(path)
        record["rows"] = int(len(df))
        record["columns"] = int(len(df.columns))
        record["status"] = "loaded" if not df.empty else "empty"
    except pd.errors.EmptyDataError:
        df = pd.DataFrame()
        record["rows"] = 0
        record["columns"] = 0
        record["status"] = "empty"
        record["error"] = "CSV has no header or data rows."
    except Exception as exc:
        df = pd.DataFrame()
        record["rows"] = 0
        record["columns"] = 0
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"
    return df, record


def read_runtime_json(label: str, path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read a JSON artifact safely, returning an empty dict on missing/error."""
    record = _file_record(label, path, "json")
    if not record["exists"]:
        return {}, record

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        record["status"] = "loaded"
    except Exception as exc:
        payload = {}
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"
    return payload if isinstance(payload, dict) else {}, record


def read_runtime_text(label: str, path: Path) -> tuple[str, dict[str, Any]]:
    """Read a text artifact safely, returning an empty string on missing/error."""
    record = _file_record(label, path, "text")
    if not record["exists"]:
        return "", record

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        record["status"] = "loaded" if text.strip() else "empty"
    except Exception as exc:
        text = ""
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"
    return text, record


def _nested_get(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def extract_quality_review_statuses(
    quality_summary: dict[str, Any],
    board_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract top-card status values for the Streamlit review page."""
    board_summary = board_summary or {}
    run_health = _nested_get(quality_summary, "run_health", "status")
    if not run_health:
        run_health = quality_summary.get("run_health_status")

    candidate_funnel = quality_summary.get("candidate_funnel") or {}
    kelly_summary = quality_summary.get("kelly_safety_summary") or {}
    statuses: dict[str, Any] = {
        "run_health": run_health or "not_available",
        "elite_count": candidate_funnel.get(
            "elite_board_count",
            board_summary.get("elite_count", board_summary.get("selected_count", 0)),
        ),
        "full_market_count": candidate_funnel.get(
            "full_market_board_count",
            board_summary.get("full_market_count", 0),
        ),
        "kelly_eligible_count": kelly_summary.get(
            "kelly_eligible_count",
            candidate_funnel.get("kelly_rows_count", board_summary.get("kelly_eligible_count", 0)),
        ),
    }

    for phase_key, spec in PHASE15_REVIEW_ARTIFACTS.items():
        summary_key = spec["summary_key"]
        statuses[f"{phase_key}_readiness_verdict"] = (
            _nested_get(quality_summary, summary_key, "readiness_verdict")
            or "not_available"
        )
    return statuses


def load_quality_review_artifacts(
    out_dir: str | Path,
    prediction_date_text: Any,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Load Quality Summary and Phase 15D-G review artifacts for Streamlit."""
    date_text = normalize_prediction_date_text(prediction_date_text)
    out_dir_path = resolve_output_dir(out_dir, repo_root=repo_root)
    runtime_root = out_dir_path / "runtime"
    paths = quality_review_artifact_paths(out_dir_path, date_text)
    records: list[dict[str, Any]] = []

    quality_text, quality_text_record = read_runtime_text(
        QUALITY_SUMMARY_ARTIFACTS["quality_summary_text"]["label"],
        paths["quality_summary_text"],
    )
    records.append(quality_text_record)

    quality_json, quality_json_record = read_runtime_json(
        QUALITY_SUMMARY_ARTIFACTS["quality_summary_json"]["label"],
        paths["quality_summary_json"],
    )
    records.append(quality_json_record)

    phases: dict[str, dict[str, Any]] = {}
    for phase_key, spec in PHASE15_REVIEW_ARTIFACTS.items():
        text_path = paths[f"{phase_key}_text"]
        csv_path = paths[f"{phase_key}_csv"]
        text, text_record = read_runtime_text(spec["short_title"], text_path)
        csv_df, csv_record = read_runtime_csv(f"{spec['short_title']} CSV", csv_path)
        records.extend([text_record, csv_record])
        phases[phase_key] = {
            **spec,
            "text": text,
            "csv": csv_df,
            "text_record": text_record,
            "csv_record": csv_record,
        }

    return {
        "prediction_date": date_text,
        "out_dir": str(out_dir_path.resolve()),
        "runtime_root": str(runtime_root.resolve()),
        "quality_summary_text": quality_text,
        "quality_summary_json": quality_json,
        "quality_summary_text_record": quality_text_record,
        "quality_summary_json_record": quality_json_record,
        "phases": phases,
        "records": records,
    }


__all__ = [
    "PHASE15_READINESS_LABELS",
    "PHASE15_REVIEW_ARTIFACTS",
    "QUALITY_SUMMARY_ARTIFACTS",
    "extract_quality_review_statuses",
    "load_quality_review_artifacts",
    "normalize_prediction_date_text",
    "quality_review_artifact_paths",
    "read_runtime_csv",
    "read_runtime_json",
    "read_runtime_text",
    "resolve_output_dir",
    "runtime_artifact_path",
]
