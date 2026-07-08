"""Append one offline daily-run record to the CourtVision evidence manifest."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import csv
from datetime import date, datetime
import hashlib
from pathlib import Path
import subprocess

try:
    from scripts.init_evidence_daily_manifest import MANIFEST_COLUMNS
except ModuleNotFoundError:  # Support ``python scripts/append_...py``.
    from init_evidence_daily_manifest import MANIFEST_COLUMNS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = (
    PROJECT_ROOT / "data" / "history" / "evidence_daily_manifest.csv"
)

VALID_RUN_STATUSES = frozenset(
    {
        "complete",
        "no_slate",
        "no_picks",
        "provider_failure",
        "failed_validation",
        "failed_grading",
        "failed_other",
    }
)

ARTIFACT_FIELDS = (
    "source_board_path",
    "elite_board_path",
    "kelly_artifact_path",
    "operator_card_path",
    "completion_audit_path",
    "artifact_manifest_path",
    "run_log_path",
    "validation_log_path",
    "grading_log_path",
)


class EvidenceManifestAppendError(RuntimeError):
    """Raised when a daily evidence row cannot be appended safely."""


def _parse_date(value: str, field_name: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceManifestAppendError(
            f"{field_name} must be a valid date in YYYY-MM-DD format"
        ) from exc
    if parsed.isoformat() != value:
        raise EvidenceManifestAppendError(
            f"{field_name} must be a valid date in YYYY-MM-DD format"
        )
    return parsed.isoformat()


def _require_text(value: str, field_name: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise EvidenceManifestAppendError(f"{field_name} is required")
    return cleaned


def _validate_manifest_schema(manifest_path: Path) -> None:
    if not manifest_path.is_file():
        raise EvidenceManifestAppendError(
            f"daily evidence manifest does not exist: {manifest_path}"
        )

    try:
        with manifest_path.open("r", encoding="utf-8", newline="") as handle:
            actual_columns = tuple(next(csv.reader(handle), ()))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise EvidenceManifestAppendError(
            f"could not read daily evidence manifest header: {exc}"
        ) from exc

    if actual_columns != MANIFEST_COLUMNS:
        raise EvidenceManifestAppendError(
            "daily evidence manifest has the wrong schema; "
            f"expected {list(MANIFEST_COLUMNS)!r}, got {list(actual_columns)!r}"
        )


def _capture_code_sha(repo_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or exc.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise EvidenceManifestAppendError(
            f"git rev-parse HEAD failed{suffix}"
        ) from exc
    return _require_text(completed.stdout, "code_sha")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise EvidenceManifestAppendError(
            f"could not hash artifact {path}: {exc}"
        ) from exc
    return digest.hexdigest()


def _artifact_values(
    artifact_paths: Mapping[str, str | Path | None],
    *,
    repo_root: Path,
    allow_missing_artifacts: bool,
) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    missing: list[str] = []

    for field_name in ARTIFACT_FIELDS:
        supplied = artifact_paths.get(field_name)
        if supplied is None or str(supplied).strip() == "":
            values[field_name] = ""
            values[field_name.replace("_path", "_sha256")] = ""
            continue

        supplied_path = Path(str(supplied).strip())
        resolved_path = (
            supplied_path.resolve()
            if supplied_path.is_absolute()
            else (repo_root / supplied_path).resolve()
        )
        try:
            stored_path = resolved_path.relative_to(repo_root).as_posix()
        except ValueError as exc:
            raise EvidenceManifestAppendError(
                f"{field_name} must resolve inside the repository: {supplied}"
            ) from exc

        if not resolved_path.is_file():
            if not allow_missing_artifacts:
                raise EvidenceManifestAppendError(
                    f"artifact does not exist or is not a file: {resolved_path}"
                )
            # The contract requires artifact path/hash pairs to be both populated
            # or both blank. Retain the missing intended path in notes instead.
            values[field_name] = ""
            values[field_name.replace("_path", "_sha256")] = ""
            missing.append(f"{field_name}={stored_path}")
            continue

        values[field_name] = stored_path
        values[field_name.replace("_path", "_sha256")] = _sha256(resolved_path)

    return values, missing


def _normalize_optional_boolean(value: str | bool | None, field_name: str) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    normalized = str(value).strip().lower()
    if normalized not in {"true", "false"}:
        raise EvidenceManifestAppendError(
            f"{field_name} must be 'true' or 'false' when supplied"
        )
    return normalized


def _normalize_optional_count(value: int | str | None) -> str:
    if value is None or value == "":
        return ""
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceManifestAppendError(
            "released_recommendation_count must be a non-negative integer"
        ) from exc
    if normalized < 0 or str(normalized) != str(value).strip():
        raise EvidenceManifestAppendError(
            "released_recommendation_count must be a non-negative integer"
        )
    return str(normalized)


def append_evidence_daily_manifest(
    *,
    trial_id: str,
    prediction_date: str,
    run_status: str,
    config_hash: str,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    repo_root: Path = PROJECT_ROOT,
    run_date: str | None = None,
    code_sha: str | None = None,
    provider_attempted: str = "",
    provider_used: str = "",
    fallback_used: str | bool | None = None,
    released_recommendation_count: int | str | None = None,
    source_board_path: str | Path | None = None,
    elite_board_path: str | Path | None = None,
    kelly_artifact_path: str | Path | None = None,
    operator_card_path: str | Path | None = None,
    completion_audit_path: str | Path | None = None,
    artifact_manifest_path: str | Path | None = None,
    run_log_path: str | Path | None = None,
    validation_log_path: str | Path | None = None,
    grading_log_path: str | Path | None = None,
    failure_reason: str = "",
    manual_intervention: str | bool | None = None,
    notes: str = "",
    allow_missing_artifacts: bool = False,
) -> dict[str, str]:
    """Validate and append exactly one daily evidence row."""

    repo_root = Path(repo_root).resolve()
    manifest_path = Path(manifest_path).resolve()
    _validate_manifest_schema(manifest_path)

    trial_id = _require_text(trial_id, "trial_id")
    config_hash = _require_text(config_hash, "config_hash")
    prediction_date = _parse_date(prediction_date, "prediction_date")
    if run_date is None:
        run_date = datetime.now().astimezone().date().isoformat()
    run_date = _parse_date(run_date, "run_date")

    run_status = str(run_status).strip()
    if run_status not in VALID_RUN_STATUSES:
        raise EvidenceManifestAppendError(
            "run_status must be one of: " + ", ".join(sorted(VALID_RUN_STATUSES))
        )

    resolved_code_sha = (
        _require_text(code_sha, "code_sha")
        if code_sha is not None
        else _capture_code_sha(repo_root)
    )

    artifact_values, missing_artifacts = _artifact_values(
        {
            "source_board_path": source_board_path,
            "elite_board_path": elite_board_path,
            "kelly_artifact_path": kelly_artifact_path,
            "operator_card_path": operator_card_path,
            "completion_audit_path": completion_audit_path,
            "artifact_manifest_path": artifact_manifest_path,
            "run_log_path": run_log_path,
            "validation_log_path": validation_log_path,
            "grading_log_path": grading_log_path,
        },
        repo_root=repo_root,
        allow_missing_artifacts=allow_missing_artifacts,
    )

    notes = str(notes)
    if missing_artifacts:
        missing_note = "MISSING ARTIFACTS ALLOWED: " + "; ".join(missing_artifacts)
        notes = f"{notes}; {missing_note}" if notes else missing_note

    created_at = datetime.now().astimezone().isoformat()
    row = {
        "trial_id": trial_id,
        "run_date": run_date,
        "prediction_date": prediction_date,
        "code_sha": resolved_code_sha,
        "config_hash": config_hash,
        "run_status": run_status,
        "provider_attempted": str(provider_attempted),
        "provider_used": str(provider_used),
        "fallback_used": _normalize_optional_boolean(fallback_used, "fallback_used"),
        "released_recommendation_count": _normalize_optional_count(
            released_recommendation_count
        ),
        **artifact_values,
        "failure_reason": str(failure_reason),
        "manual_intervention": _normalize_optional_boolean(
            manual_intervention, "manual_intervention"
        ),
        "notes": notes,
        "created_at": created_at,
    }

    try:
        with manifest_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=MANIFEST_COLUMNS, lineterminator="\n"
            )
            writer.writerow(row)
    except (OSError, csv.Error) as exc:
        raise EvidenceManifestAppendError(
            f"could not append daily evidence manifest row: {exc}"
        ) from exc

    return row


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append one offline run record to the daily evidence manifest."
    )
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--prediction-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--run-status", required=True, choices=sorted(VALID_RUN_STATUSES))
    parser.add_argument("--config-hash", required=True)
    parser.add_argument("--run-date", help="YYYY-MM-DD; defaults to today's local date")
    parser.add_argument("--code-sha")
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--provider-attempted", default="")
    parser.add_argument("--provider-used", default="")
    parser.add_argument("--fallback-used", choices=("true", "false"))
    parser.add_argument("--released-recommendation-count", type=int)
    for field_name in ARTIFACT_FIELDS:
        parser.add_argument(f"--{field_name.replace('_', '-')}")
    parser.add_argument("--failure-reason", default="")
    parser.add_argument("--manual-intervention", choices=("true", "false"))
    parser.add_argument("--notes", default="")
    parser.add_argument("--allow-missing-artifacts", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        row = append_evidence_daily_manifest(
            **vars(args),
            repo_root=PROJECT_ROOT,
        )
    except EvidenceManifestAppendError as exc:
        print(f"Status: failed: {exc}")
        return 1

    print(f"Manifest path: {Path(args.manifest_path).resolve()}")
    print(f"Created at: {row['created_at']}")
    print("Status: appended one row")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
