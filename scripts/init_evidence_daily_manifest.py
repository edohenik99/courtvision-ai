"""Initialize the offline NBA forward paper-trial daily evidence manifest."""

from __future__ import annotations

import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = (
    PROJECT_ROOT / "data" / "history" / "evidence_daily_manifest.csv"
)

MANIFEST_COLUMNS = (
    "trial_id",
    "run_date",
    "prediction_date",
    "code_sha",
    "config_hash",
    "run_status",
    "provider_attempted",
    "provider_used",
    "fallback_used",
    "released_recommendation_count",
    "source_board_path",
    "source_board_sha256",
    "elite_board_path",
    "elite_board_sha256",
    "kelly_artifact_path",
    "kelly_artifact_sha256",
    "operator_card_path",
    "operator_card_sha256",
    "completion_audit_path",
    "completion_audit_sha256",
    "artifact_manifest_path",
    "artifact_manifest_sha256",
    "run_log_path",
    "run_log_sha256",
    "validation_log_path",
    "validation_log_sha256",
    "grading_log_path",
    "grading_log_sha256",
    "failure_reason",
    "manual_intervention",
    "notes",
    "created_at",
)


class ManifestSchemaError(ValueError):
    """Raised when an existing daily manifest does not match the contract."""


def _read_header(manifest_path: Path) -> tuple[str, ...]:
    try:
        with manifest_path.open("r", encoding="utf-8", newline="") as handle:
            return tuple(next(csv.reader(handle), ()))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ManifestSchemaError(
            f"could not read existing manifest header: {exc}"
        ) from exc


def _validate_existing_manifest(manifest_path: Path) -> None:
    actual_columns = _read_header(manifest_path)
    if actual_columns != MANIFEST_COLUMNS:
        raise ManifestSchemaError(
            "existing manifest has the wrong schema; "
            f"expected {list(MANIFEST_COLUMNS)!r}, got {list(actual_columns)!r}"
        )


def initialize_evidence_daily_manifest(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> bool:
    """Create the manifest exclusively, or validate it if it already exists.

    Returns ``True`` only when this call created the file. No existing file is
    ever opened for writing.
    """

    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with manifest_path.open("x", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(MANIFEST_COLUMNS)
    except FileExistsError:
        _validate_existing_manifest(manifest_path)
        return False

    return True


def main(manifest_path: Path = DEFAULT_MANIFEST_PATH) -> int:
    manifest_path = Path(manifest_path).resolve()
    print(f"Manifest path: {manifest_path}")

    try:
        created = initialize_evidence_daily_manifest(manifest_path)
    except ManifestSchemaError as exc:
        print(f"Status: already existed (invalid schema): {exc}")
        return 1
    except OSError as exc:
        print(f"Status: initialization failed: {exc}")
        return 1

    if created:
        print("Status: created")
    else:
        print("Status: already existed (schema valid)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
