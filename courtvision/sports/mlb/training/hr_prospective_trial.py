"""Immutable, research-only MLB home-run prospective paper-trial operations.

This module is an explicit opt-in boundary over the existing MLB HR research
predictor.  It never trains a model, fetches data, creates an OfficialPick, or
enables wagering.  All mutation is confined to an explicitly supplied trial
root and all durable evidence is content- or identity-bound.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import socket
from typing import Any, Callable, Final, Mapping, NoReturn, Sequence
from uuid import uuid4

from courtvision.prospective.contracts import GitProvenanceV1
from courtvision.prospective.provenance import (
    capture_configuration_provenance,
    capture_git_provenance,
    validate_git_provenance,
)
from courtvision.sports.mlb.player_name_normalization import (
    normalize_mlb_player_name,
)
from courtvision.sports.mlb.training import hr_research_baseline as baseline


CONTROL_SCHEMA_VERSION: Final = "mlb-hr-prospective-control-v1"
PREDICTION_SCHEMA_VERSION: Final = "mlb-hr-prospective-prediction-v1"
PREDICTION_MANIFEST_SCHEMA_VERSION: Final = (
    "mlb-hr-prospective-prediction-manifest-v1"
)
RUN_SUMMARY_SCHEMA_VERSION: Final = "mlb-hr-prospective-run-summary-v1"
LEDGER_SCHEMA_VERSION: Final = "mlb-hr-prospective-ledger-v2"
CLOSING_SCHEMA_VERSION: Final = "mlb-hr-prospective-closing-v2"
STATUS_SCHEMA_VERSION: Final = "mlb-hr-prospective-status-v1"
TRIAL_LOCK_SCHEMA_VERSION: Final = "mlb-hr-prospective-trial-lock-v1"

SPORT: Final = "MLB"
MARKET: Final = "batter_home_runs"
APPROVAL_STATUS: Final = "not_approved"
OPERATING_TIMEZONE: Final = baseline.COURTVISION_OPERATING_TIMEZONE_NAME
TRIAL_LOCK_FILENAME: Final = ".prospective-trial-store.lock"
CONTROL_MANIFEST_FILENAME: Final = "control_manifest_v1.json"
PREDICTION_MANIFEST_FILENAME: Final = "prediction_manifest_v1.json"
RUN_SUMMARY_FILENAME: Final = "run_summary_v1.json"
REQUIRED_MODEL_FILES: Final = (
    "model.json",
    "metadata.json",
    "metrics.json",
    "model_card.md",
    "bundle_manifest.json",
)

RESEARCH_BOUNDARY: Final = {
    "sport": SPORT,
    "market": MARKET,
    "research_only": True,
    "approval_status": APPROVAL_STATUS,
    "eligible_for_betting": False,
    "eligible_for_official_pick": False,
}

FROZEN_POLICIES: Final = {
    "population_policy": "all_eligible_pregame_player_game_over_0_5_hr_rows",
    "prediction_population_policy": "retain_complete_eligible_population",
    "operating_timezone": OPERATING_TIMEZONE,
    "prediction_snapshot_rule": baseline.PREDICTION_SNAPSHOT_SELECTION_RULE,
    "training_snapshot_rule": baseline.SNAPSHOT_SELECTION_RULE,
    "sportsbook_selection_rule": baseline.BOOKMAKER_SELECTION_RULE,
    "closing_evidence_rule": (
        "same_book_latest_valid_prestart_then_consensus_latest_prestart"
    ),
    "settlement_join_rule": "strict_event_id_plus_normalized_player_name",
    "special_event_policy": "quarantined",
    "unknown_event_policy": "quarantined",
    "manual_review_event_policy": "quarantined",
    "betting_policy": "disabled",
    "promotion_policy": "disabled",
    "network_access": "disabled",
}

DEFAULT_ACTIVATION_CONFIGURATION: Final = {
    "command": "activate-prospective-control",
    "data_access": "explicit_local_files_only",
    "model_resolution": "explicit_bundle_only",
    "control_resolution": "explicit_control_only",
    "result_access_during_prediction": "disabled",
    "population_filtering": "no_probability_or_edge_threshold",
}

PREDICTION_COLUMNS: Final = (
    "prediction_schema_version",
    "prediction_id",
    "prediction_run_id",
    "control_id",
    "control_manifest_digest",
    "model_id",
    "model_version",
    "model_bundle_manifest_digest",
    "feature_schema_version",
    "prediction_git_commit",
    "prediction_tree_fingerprint",
    "prediction_configuration_digest",
    "sport",
    "market",
    "research_only",
    "approval_status",
    "eligible_for_betting",
    "eligible_for_official_pick",
    "operating_date",
    "operating_timezone",
    "event_id",
    "commence_time_utc",
    "home_team",
    "away_team",
    "player_id",
    "player_name",
    "normalized_player_name",
    "identity_status",
    "identity_mapping_version",
    "sportsbook",
    "sportsbook_name",
    "prediction_time_price",
    "prediction_time_decimal_odds",
    "implied_probability",
    "model_probability",
    "probability_edge",
    "prediction_timestamp_utc",
    "selected_snapshot_timestamp_utc",
    "source_odds_sha256",
    "snapshot_rule",
    "sportsbook_rule",
    "market_key",
    "side",
    "point",
    "integrity_status",
)

EXCLUDED_COLUMNS: Final = (
    "prediction_run_id",
    "control_id",
    "sport",
    "market",
    "research_only",
    "approval_status",
    "eligible_for_betting",
    "eligible_for_official_pick",
    "operating_date",
    "event_id",
    "commence_time_utc",
    "player_name",
    "normalized_player_name",
    "identity_status",
    "event_eligibility_status",
    "selected_snapshot_timestamp_utc",
    "exclusion_reason",
    "source_odds_sha256",
)

LEDGER_COLUMNS: Final = (
    "ledger_schema_version",
    "ledger_record_id",
    "record_type",
    "prediction_id",
    "prediction_run_id",
    "control_id",
    "control_manifest_digest",
    "model_id",
    "model_version",
    "model_bundle_manifest_digest",
    "feature_schema_version",
    "prediction_git_commit",
    "prediction_tree_fingerprint",
    "prediction_configuration_digest",
    "operating_date",
    "event_id",
    "commence_time_utc",
    "player_id",
    "player_name",
    "normalized_player_name",
    "identity_status",
    "identity_mapping_version",
    "sportsbook",
    "original_american_odds",
    "original_decimal_odds",
    "original_implied_probability",
    "model_probability",
    "probability_edge",
    "prediction_timestamp_utc",
    "selected_snapshot_timestamp_utc",
    "source_odds_sha256",
    "prediction_manifest_digest",
    "predictions_csv_sha256",
    "settlement_status",
    "strict_result_status",
    "final_hr_outcome",
    "grade",
    "unit_profit_loss",
    "results_sha256",
    "settlement_timestamp_utc",
    "integrity_status",
    "sport",
    "market",
    "research_only",
    "approval_status",
    "eligible_for_betting",
    "eligible_for_official_pick",
)

CLOSING_COLUMNS: Final = (
    "closing_schema_version",
    "closing_record_id",
    "prediction_id",
    "prediction_run_id",
    "control_id",
    "control_manifest_digest",
    "closing_status",
    "closing_method",
    "closing_snapshot_time_utc",
    "closing_sportsbook",
    "closing_sportsbook_name",
    "closing_american_odds",
    "closing_decimal_odds",
    "closing_implied_probability",
    "consensus_bookmaker_count",
    "consensus_implied_probability",
    "original_american_odds",
    "original_implied_probability",
    "closing_line_movement",
    "closing_probability_movement",
    "source_odds_sha256",
    "captured_at_utc",
    "integrity_status",
    "sport",
    "market",
    "research_only",
    "approval_status",
    "eligible_for_betting",
    "eligible_for_official_pick",
)


class MLBHRProspectiveTrialError(baseline.MLBHRResearchBaselineError):
    """Raised when prospective evidence cannot be trusted."""


class MLBHRProspectiveTrialBusyError(MLBHRProspectiveTrialError):
    """Raised when another verified trial-store owner holds the lock."""


class MLBHRProspectiveTrialConflictError(MLBHRProspectiveTrialError):
    """Raised when immutable evidence conflicts with an existing identity."""


class MLBHRProspectiveTrialLockError(MLBHRProspectiveTrialError):
    """Raised when lock ownership or lock evidence cannot be verified."""


@dataclass(frozen=True, slots=True)
class ControlActivationResult:
    control_id: str
    control_dir: Path
    control_manifest_digest: str
    model_id: str
    model_version: str
    model_bundle_manifest_digest: str
    replayed_existing_control: bool


@dataclass(frozen=True, slots=True)
class ProspectivePaperRunResult:
    status: str
    control_id: str
    prediction_run_id: str
    operating_date: str
    prediction_count: int
    exclusion_count: int
    prediction_manifest_digest: str
    run_dir: Path | None
    ledger_rows_appended: int
    replayed_existing_run: bool
    predictions: tuple[dict[str, str], ...] = ()
    exclusions: tuple[dict[str, str], ...] = ()


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MLBHRProspectiveTrialError("evidence is not canonical JSON") from exc


def _canonical_json_file_bytes(value: object) -> bytes:
    return _canonical_json_bytes(value) + b"\n"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _is_sha256(value: object) -> bool:
    text = str(value)
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _read_stable_bytes(path: Path, description: str) -> bytes:
    try:
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise MLBHRProspectiveTrialError(f"{description} is inaccessible") from exc
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if before_identity != after_identity or len(data) != after.st_size:
        raise MLBHRProspectiveTrialError(
            f"{description} changed while it was being read"
        )
    return data


def _file_sha256(path: str | Path, description: str = "source file") -> str:
    return _sha256_bytes(_read_stable_bytes(Path(path), description))


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-standard JSON number is forbidden: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _load_json_bytes(data: bytes, description: str) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise MLBHRProspectiveTrialError(f"{description} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise MLBHRProspectiveTrialError(f"{description} must be a JSON object")
    return value


def _parse_utc(value: object, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise MLBHRProspectiveTrialError(
            f"{field_name} must be an ISO-8601 datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MLBHRProspectiveTrialError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MLBHRProspectiveTrialError("UTC timestamp must be timezone-aware")
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _clock_value(clock: Callable[[], datetime], field_name: str) -> datetime:
    try:
        value = clock()
    except Exception as exc:
        raise MLBHRProspectiveTrialError(f"{field_name} clock failed") from exc
    if not isinstance(value, datetime):
        raise MLBHRProspectiveTrialError(f"{field_name} clock returned invalid value")
    return _parse_utc(value, field_name)


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _research_csv_fields() -> dict[str, str]:
    return {
        "sport": SPORT,
        "market": MARKET,
        "research_only": "true",
        "approval_status": APPROVAL_STATUS,
        "eligible_for_betting": "false",
        "eligible_for_official_pick": "false",
    }


def _validate_research_mapping(value: Mapping[str, object], description: str) -> None:
    for field_name, expected in RESEARCH_BOUNDARY.items():
        if value.get(field_name) != expected:
            raise MLBHRProspectiveTrialError(
                f"{description} violates research-only field {field_name}"
            )


def _validate_research_csv_row(value: Mapping[str, str], description: str) -> None:
    expected = _research_csv_fields()
    for field_name, expected_value in expected.items():
        if str(value.get(field_name, "")) != expected_value:
            raise MLBHRProspectiveTrialError(
                f"{description} violates research-only field {field_name}"
            )


def _contains_forbidden_secret_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).casefold().replace("-", "_")
            tokens = set(key.split("_"))
            if tokens.intersection(
                {"secret", "password", "credential", "credentials", "apikey", "token"}
            ) or "api_key" in key:
                return True
            if _contains_forbidden_secret_key(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_secret_key(item) for item in value)
    return False


def _safe_relative_reference(
    path: str | Path,
    *,
    repository_root: Path,
    trial_root: Path,
) -> dict[str, str]:
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MLBHRProspectiveTrialError("evidence path is inaccessible") from exc
    for base_name, base in (("repository", repository_root), ("trial", trial_root)):
        try:
            relative = resolved.relative_to(base.resolve(strict=False)).as_posix()
        except ValueError:
            continue
        if not relative or relative.startswith("../"):
            raise MLBHRProspectiveTrialError("evidence path must name a file or directory")
        return {"base": base_name, "path": relative}
    raise MLBHRProspectiveTrialError(
        "durable evidence paths must be under repository_root or trial_root"
    )


def _resolve_reference(
    reference: Mapping[str, object],
    *,
    repository_root: Path | None,
    trial_root: Path,
) -> Path:
    base_name = str(reference.get("base", ""))
    relative = str(reference.get("path", ""))
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise MLBHRProspectiveTrialError("control contains an unsafe relative path")
    if base_name == "repository":
        if repository_root is None:
            raise MLBHRProspectiveTrialError(
                "repository_root is required to revalidate the frozen model"
            )
        base = repository_root
    elif base_name == "trial":
        base = trial_root
    else:
        raise MLBHRProspectiveTrialError("control contains an unsupported path base")
    resolved = (base / Path(relative)).resolve(strict=False)
    try:
        resolved.relative_to(base.resolve(strict=False))
    except ValueError as exc:
        raise MLBHRProspectiveTrialError("control path escaped its declared base") from exc
    return resolved


def _csv_bytes(columns: Sequence[str], rows: Sequence[Mapping[str, object]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(columns),
        extrasaction="raise",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in columns})
    return buffer.getvalue().encode("utf-8")


def _parse_csv_bytes(
    data: bytes,
    columns: Sequence[str],
    description: str,
) -> tuple[dict[str, str], ...]:
    try:
        text = data.decode("utf-8-sig", errors="strict")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if tuple(reader.fieldnames or ()) != tuple(columns):
            raise MLBHRProspectiveTrialError(
                f"{description} schema does not match the expected version"
            )
        rows: list[dict[str, str]] = []
        for row in reader:
            if None in row:
                raise MLBHRProspectiveTrialError(
                    f"{description} contains a malformed row"
                )
            rows.append({column: str(row.get(column, "") or "") for column in columns})
        return tuple(rows)
    except UnicodeError as exc:
        raise MLBHRProspectiveTrialError(f"{description} is not UTF-8") from exc


def _write_exclusive(path: Path, data: bytes, description: str) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise MLBHRProspectiveTrialError(f"could not write staged {description}") from exc


def _safe_remove_owned_stage(stage: Path, parent: Path, prefix: str) -> None:
    if stage.parent != parent or not stage.name.startswith(prefix) or stage.is_symlink():
        raise MLBHRProspectiveTrialError("refusing to clean an unrecognized staging path")
    try:
        shutil.rmtree(stage)
    except OSError as exc:
        raise MLBHRProspectiveTrialError("could not clean owned staging evidence") from exc


def _call_failure_hook(hook: Callable[[str], None] | None, stage: str) -> None:
    if hook is not None:
        hook(stage)


def _capture_clean_git(repository_root: Path) -> GitProvenanceV1:
    try:
        return capture_git_provenance(repository_root, require_clean=True)
    except Exception as exc:
        raise MLBHRProspectiveTrialError(
            "clean Git provenance is unavailable; prospective operation is blocked"
        ) from exc


def _git_from_mapping(value: object) -> GitProvenanceV1:
    if not isinstance(value, Mapping):
        raise MLBHRProspectiveTrialError("control Git provenance is invalid")
    try:
        return GitProvenanceV1(
            commit_sha=str(value.get("commit_sha", "")),
            dirty=value.get("dirty"),  # type: ignore[arg-type]
            working_tree_fingerprint=str(
                value.get("working_tree_fingerprint", "")
            ),
        )
    except Exception as exc:
        raise MLBHRProspectiveTrialError("control Git provenance is invalid") from exc


def _validate_current_git(expected: GitProvenanceV1, repository_root: Path) -> None:
    try:
        validate_git_provenance(expected, repository_root)
    except Exception as exc:
        raise MLBHRProspectiveTrialError(
            "current clean Git commit and tree fingerprint do not match the control"
        ) from exc


def validate_complete_model_bundle(model_dir: str | Path) -> dict[str, object]:
    """Validate all five required legacy bundle files and exact hash bindings."""

    root = Path(model_dir).expanduser().resolve(strict=False)
    if root.is_symlink() or not root.is_dir():
        raise MLBHRProspectiveTrialError("explicit model bundle is not a real directory")
    payloads: dict[str, bytes] = {}
    for filename in REQUIRED_MODEL_FILES:
        path = root / filename
        if path.is_symlink() or not path.is_file():
            raise MLBHRProspectiveTrialError(
                f"model bundle is missing required file {filename}"
            )
        payloads[filename] = _read_stable_bytes(path, f"model bundle {filename}")

    model = _load_json_bytes(payloads["model.json"], "model.json")
    metadata = _load_json_bytes(payloads["metadata.json"], "metadata.json")
    metrics = _load_json_bytes(payloads["metrics.json"], "metrics.json")
    bundle_manifest = _load_json_bytes(
        payloads["bundle_manifest.json"], "bundle_manifest.json"
    )
    try:
        loaded = baseline.load_model_bundle(root)
    except baseline.MLBHRResearchBaselineError as exc:
        raise MLBHRProspectiveTrialError("existing model bundle validation failed") from exc

    model_id = str(metadata.get("model_id", ""))
    model_version = str(metadata.get("model_version", ""))
    feature_schema_version = str(metadata.get("feature_schema_version", ""))
    if not model_id or not model_version or not feature_schema_version:
        raise MLBHRProspectiveTrialError("model bundle identity fields are incomplete")
    if loaded.model_id != model_id or loaded.model_version != model_version:
        raise MLBHRProspectiveTrialError("loaded model identity does not match metadata")
    if model.get("model_id") != model_id or model.get("model_version") != model_version:
        raise MLBHRProspectiveTrialError("model.json identity does not match metadata")
    if model.get("feature_schema_version") != feature_schema_version:
        raise MLBHRProspectiveTrialError("model feature schema does not match metadata")
    if bundle_manifest.get("model_id") != model_id:
        raise MLBHRProspectiveTrialError("bundle manifest model_id does not match metadata")
    recorded_hashes = {
        "model.json": "model_json_sha256",
        "metadata.json": "metadata_json_sha256",
        "metrics.json": "metrics_json_sha256",
    }
    for filename, field_name in recorded_hashes.items():
        if bundle_manifest.get(field_name) != _sha256_bytes(payloads[filename]):
            raise MLBHRProspectiveTrialError(
                f"bundle manifest digest does not match {filename}"
            )
    if metadata.get("model_json_sha256") != _sha256_bytes(payloads["model.json"]):
        raise MLBHRProspectiveTrialError("metadata model digest does not match model.json")
    if metadata.get("evaluation_metrics") != metrics:
        raise MLBHRProspectiveTrialError("metrics.json does not match metadata metrics")
    try:
        card = payloads["model_card.md"].decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise MLBHRProspectiveTrialError("model_card.md is not UTF-8") from exc
    if model_id not in card or baseline.RESEARCH_ONLY_LABEL not in card:
        raise MLBHRProspectiveTrialError("model_card.md does not bind model identity")
    training_range = metadata.get("training_date_range")
    if not isinstance(training_range, Mapping):
        raise MLBHRProspectiveTrialError("model training interval is missing")
    try:
        training_start = date.fromisoformat(str(training_range.get("start", "")))
        training_end = date.fromisoformat(str(training_range.get("end", "")))
    except ValueError as exc:
        raise MLBHRProspectiveTrialError("model training interval is invalid") from exc
    if training_start > training_end:
        raise MLBHRProspectiveTrialError("model training interval is reversed")
    created_at = _parse_utc(metadata.get("training_timestamp"), "model creation timestamp")
    files = [
        {
            "filename": filename,
            "sha256": _sha256_bytes(payloads[filename]),
            "size_bytes": len(payloads[filename]),
        }
        for filename in REQUIRED_MODEL_FILES
    ]
    return {
        "root": root,
        "model_id": model_id,
        "model_version": model_version,
        "feature_schema_version": feature_schema_version,
        "bundle_manifest_digest": _sha256_bytes(payloads["bundle_manifest.json"]),
        "required_files": files,
        "training_interval": {
            "start": training_start.isoformat(),
            "end": training_end.isoformat(),
        },
        "model_created_at_utc": _utc_text(created_at),
    }


def _load_lock(path: Path) -> tuple[dict[str, Any], bytes, os.stat_result]:
    data = _read_stable_bytes(path, "trial-store lock")
    metadata = _load_json_bytes(data, "trial-store lock")
    expected_fields = {
        "schema_version",
        "owner_token",
        "pid",
        "hostname",
        "created_at_utc",
        "operation",
        "control_id",
    }
    if set(metadata) != expected_fields:
        raise MLBHRProspectiveTrialLockError("trial-store lock fields are malformed")
    if metadata.get("schema_version") != TRIAL_LOCK_SCHEMA_VERSION:
        raise MLBHRProspectiveTrialLockError("trial-store lock schema is unsupported")
    if type(metadata.get("pid")) is not int or int(metadata["pid"]) <= 0:
        raise MLBHRProspectiveTrialLockError("trial-store lock pid is invalid")
    for field_name in ("owner_token", "hostname", "operation", "control_id"):
        if not isinstance(metadata.get(field_name), str) or not metadata[field_name]:
            raise MLBHRProspectiveTrialLockError(
                f"trial-store lock {field_name} is invalid"
            )
    _parse_utc(metadata.get("created_at_utc"), "trial-store lock created_at_utc")
    try:
        observed = path.stat()
    except OSError as exc:
        raise MLBHRProspectiveTrialLockError("trial-store lock is inaccessible") from exc
    return metadata, data, observed


def _classify_existing_lock(path: Path) -> NoReturn:
    try:
        metadata, _, _ = _load_lock(path)
    except MLBHRProspectiveTrialError as exc:
        raise MLBHRProspectiveTrialLockError(
            "existing trial-store lock is malformed or inaccessible"
        ) from exc
    raise MLBHRProspectiveTrialBusyError(
        "prospective trial store is busy "
        f"(pid={metadata['pid']}, host={metadata['hostname']})"
    )


class _TrialStoreLock:
    """Create-exclusive lock with no stale-lock deletion."""

    def __init__(
        self,
        trial_root: Path,
        *,
        operation: str,
        control_id: str,
        clock: Callable[[], datetime],
    ) -> None:
        self.path = trial_root / TRIAL_LOCK_FILENAME
        self.metadata = {
            "schema_version": TRIAL_LOCK_SCHEMA_VERSION,
            "owner_token": uuid4().hex,
            "pid": os.getpid(),
            "hostname": socket.gethostname() or "unknown-host",
            "created_at_utc": _utc_text(_clock_value(clock, "trial lock")),
            "operation": operation,
            "control_id": control_id,
        }
        self.data = _canonical_json_file_bytes(self.metadata)
        self.acquired = False

    def acquire(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise MLBHRProspectiveTrialLockError(
                "cannot create explicit trial root"
            ) from exc
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except FileExistsError:
            _classify_existing_lock(self.path)
        except PermissionError as exc:
            try:
                visible = os.path.lexists(self.path)
            except OSError as visibility_exc:
                raise MLBHRProspectiveTrialLockError(
                    "trial-store lock visibility is inaccessible"
                ) from visibility_exc
            if visible:
                _classify_existing_lock(self.path)
            raise MLBHRProspectiveTrialLockError("cannot create trial-store lock") from exc
        except OSError as exc:
            raise MLBHRProspectiveTrialLockError("cannot create trial-store lock") from exc
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(self.data)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise MLBHRProspectiveTrialLockError(
                "failed to initialize trial-store lock; lock was left in place"
            ) from exc
        metadata, data, _ = _load_lock(self.path)
        if metadata != self.metadata or data != self.data:
            raise MLBHRProspectiveTrialLockError(
                "new trial-store lock ownership could not be verified"
            )
        self.acquired = True

    def release(self) -> None:
        if not self.acquired:
            return
        metadata, data, observed = _load_lock(self.path)
        if metadata != self.metadata or data != self.data:
            raise MLBHRProspectiveTrialLockError(
                "trial-store lock ownership changed; refusing removal"
            )
        try:
            current = self.path.stat()
        except OSError as exc:
            raise MLBHRProspectiveTrialLockError(
                "owned trial-store lock became inaccessible"
            ) from exc
        if (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
        ) != (
            observed.st_dev,
            observed.st_ino,
            observed.st_size,
            observed.st_mtime_ns,
        ):
            raise MLBHRProspectiveTrialLockError(
                "trial-store lock changed before release; refusing removal"
            )
        try:
            self.path.unlink()
        except OSError as exc:
            raise MLBHRProspectiveTrialLockError(
                "verified trial-store lock owner could not remove its lock"
            ) from exc
        self.acquired = False

    def __enter__(self) -> _TrialStoreLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


def _control_identity_material(
    *,
    model_evidence: Mapping[str, object],
    git_provenance: GitProvenanceV1,
    configuration: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": CONTROL_SCHEMA_VERSION,
        **RESEARCH_BOUNDARY,
        "model_id": model_evidence["model_id"],
        "model_version": model_evidence["model_version"],
        "model_feature_schema_version": model_evidence[
            "feature_schema_version"
        ],
        "model_bundle_manifest_digest": model_evidence[
            "bundle_manifest_digest"
        ],
        "required_model_files": model_evidence["required_files"],
        "model_training_interval": model_evidence["training_interval"],
        "model_created_at_utc": model_evidence["model_created_at_utc"],
        "activation_git_provenance": git_provenance.to_dict(),
        "activation_configuration": configuration,
        "policies": FROZEN_POLICIES,
    }


def _validate_control_payload(value: Mapping[str, object]) -> None:
    required_fields = {
        "schema_version",
        "control_id",
        "control_digest",
        "created_at_utc",
        "identity_material",
        "model_bundle_reference",
        "activation_configuration_digest",
        *RESEARCH_BOUNDARY.keys(),
    }
    missing = sorted(required_fields - set(value))
    if missing:
        raise MLBHRProspectiveTrialError(
            "control manifest is missing required fields: " + ", ".join(missing)
        )
    unexpected = sorted(set(value) - required_fields)
    if unexpected:
        raise MLBHRProspectiveTrialError(
            "control manifest has unexpected fields: " + ", ".join(unexpected)
        )
    if value.get("schema_version") != CONTROL_SCHEMA_VERSION:
        raise MLBHRProspectiveTrialError("control schema version is unsupported")
    _validate_research_mapping(value, "control manifest")
    control_id = str(value.get("control_id", ""))
    control_digest = str(value.get("control_digest", ""))
    identity_material = value.get("identity_material")
    if not isinstance(identity_material, Mapping):
        raise MLBHRProspectiveTrialError("control identity material is invalid")
    expected_identity_fields = {
        "schema_version",
        "model_id",
        "model_version",
        "model_feature_schema_version",
        "model_bundle_manifest_digest",
        "required_model_files",
        "model_training_interval",
        "model_created_at_utc",
        "activation_git_provenance",
        "activation_configuration",
        "policies",
        *RESEARCH_BOUNDARY.keys(),
    }
    if set(identity_material) != expected_identity_fields:
        raise MLBHRProspectiveTrialError(
            "control identity material fields do not match v1"
        )
    _validate_research_mapping(identity_material, "control identity material")
    if identity_material.get("schema_version") != CONTROL_SCHEMA_VERSION:
        raise MLBHRProspectiveTrialError("control identity schema is unsupported")
    expected_digest = _canonical_sha256(identity_material)
    expected_id = f"mlb-hr-control-v1-{expected_digest[:20]}"
    if control_digest != expected_digest or control_id != expected_id:
        raise MLBHRProspectiveTrialError("control identity digest does not match content")
    _parse_utc(value.get("created_at_utc"), "control created_at_utc")
    configuration = identity_material.get("activation_configuration")
    if not isinstance(configuration, Mapping):
        raise MLBHRProspectiveTrialError("control activation configuration is invalid")
    if value.get("activation_configuration_digest") != _canonical_sha256(
        configuration
    ):
        raise MLBHRProspectiveTrialError(
            "control activation configuration digest does not match"
        )
    if identity_material.get("policies") != FROZEN_POLICIES:
        raise MLBHRProspectiveTrialError("control frozen policies do not match v1")
    _git_from_mapping(identity_material.get("activation_git_provenance"))
    if _contains_forbidden_secret_key(value):
        raise MLBHRProspectiveTrialError(
            "control manifest contains a forbidden secret-like field"
        )
    reference = value.get("model_bundle_reference")
    if not isinstance(reference, Mapping):
        raise MLBHRProspectiveTrialError("control model reference is invalid")
    if set(reference) != {"base", "path"}:
        raise MLBHRProspectiveTrialError("control model reference fields are invalid")


def _expected_control_directory(trial_root: Path, control_id: str) -> Path:
    return (trial_root / "controls" / control_id).resolve(strict=False)


def _read_control(
    control_dir: str | Path,
    *,
    trial_root: str | Path,
    repository_root: str | Path | None = None,
    revalidate_model: bool = False,
) -> tuple[dict[str, Any], str, Path]:
    trial = Path(trial_root).expanduser().resolve(strict=False)
    supplied = Path(control_dir).expanduser()
    if supplied.is_symlink():
        raise MLBHRProspectiveTrialError("control directory may not be a symlink")
    directory = supplied.resolve(strict=False)
    if not directory.is_dir():
        raise MLBHRProspectiveTrialError("explicit control directory does not exist")
    manifest_path = directory / CONTROL_MANIFEST_FILENAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise MLBHRProspectiveTrialError("control manifest is missing")
    try:
        entries = {entry.name for entry in directory.iterdir()}
    except OSError as exc:
        raise MLBHRProspectiveTrialError("control directory is inaccessible") from exc
    allowed_entries = {
        CONTROL_MANIFEST_FILENAME,
        "dates",
        "prospective_ledger.csv",
        "closing_lines.csv",
    }
    if CONTROL_MANIFEST_FILENAME not in entries or not entries.issubset(
        allowed_entries
    ):
        raise MLBHRProspectiveTrialError(
            "control directory contains an unexpected or mutable entry"
        )
    for name in entries - {CONTROL_MANIFEST_FILENAME}:
        entry = directory / name
        if entry.is_symlink():
            raise MLBHRProspectiveTrialError(
                "control evidence entry may not be a symlink"
            )
        if name == "dates" and not entry.is_dir():
            raise MLBHRProspectiveTrialError("control dates entry is invalid")
        if name != "dates" and not entry.is_file():
            raise MLBHRProspectiveTrialError("control evidence file is invalid")
    manifest_bytes = _read_stable_bytes(manifest_path, "control manifest")
    manifest = _load_json_bytes(manifest_bytes, "control manifest")
    _validate_control_payload(manifest)
    control_id = str(manifest["control_id"])
    if directory != _expected_control_directory(trial, control_id):
        raise MLBHRProspectiveTrialError(
            "control directory does not match the immutable control identity"
        )
    if revalidate_model:
        repository = (
            Path(repository_root).expanduser().resolve(strict=False)
            if repository_root is not None
            else None
        )
        model_path = _resolve_reference(
            manifest["model_bundle_reference"],  # type: ignore[arg-type]
            repository_root=repository,
            trial_root=trial,
        )
        observed = validate_complete_model_bundle(model_path)
        material = manifest["identity_material"]
        if not isinstance(material, Mapping):
            raise MLBHRProspectiveTrialError("control identity material is invalid")
        expected_model = {
            "model_id": material.get("model_id"),
            "model_version": material.get("model_version"),
            "feature_schema_version": material.get(
                "model_feature_schema_version"
            ),
            "bundle_manifest_digest": material.get(
                "model_bundle_manifest_digest"
            ),
            "required_files": material.get("required_model_files"),
            "training_interval": material.get("model_training_interval"),
            "model_created_at_utc": material.get("model_created_at_utc"),
        }
        actual_model = {
            key: observed[key]
            for key in (
                "model_id",
                "model_version",
                "feature_schema_version",
                "bundle_manifest_digest",
                "required_files",
                "training_interval",
                "model_created_at_utc",
            )
        }
        if actual_model != expected_model:
            raise MLBHRProspectiveTrialError(
                "current model bundle bytes do not match the frozen control"
            )
    return manifest, _sha256_bytes(manifest_bytes), directory


def activate_prospective_control(
    *,
    model_dir: str | Path,
    trial_root: str | Path,
    repository_root: str | Path,
    activation_configuration: Mapping[str, object] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    failure_hook: Callable[[str], None] | None = None,
) -> ControlActivationResult:
    """Publish or replay one immutable deterministic prospective control."""

    repository = Path(repository_root).expanduser().resolve(strict=False)
    trial = Path(trial_root).expanduser().resolve(strict=False)
    model_evidence = validate_complete_model_bundle(model_dir)
    git_provenance = _capture_clean_git(repository)
    configuration = dict(
        DEFAULT_ACTIVATION_CONFIGURATION
        if activation_configuration is None
        else activation_configuration
    )
    try:
        configuration_provenance = capture_configuration_provenance(configuration)
    except Exception as exc:
        raise MLBHRProspectiveTrialError(
            "activation configuration is invalid or contains secret-like keys"
        ) from exc
    canonical_configuration = (
        configuration_provenance.canonical_configuration.to_dict()
    )
    identity_material = _control_identity_material(
        model_evidence=model_evidence,
        git_provenance=git_provenance,
        configuration=canonical_configuration,
    )
    control_digest = _canonical_sha256(identity_material)
    control_id = f"mlb-hr-control-v1-{control_digest[:20]}"
    model_reference = _safe_relative_reference(
        model_evidence["root"],  # type: ignore[arg-type]
        repository_root=repository,
        trial_root=trial,
    )
    manifest = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "control_id": control_id,
        "control_digest": control_digest,
        "created_at_utc": _utc_text(_clock_value(clock, "control creation")),
        "identity_material": identity_material,
        "model_bundle_reference": model_reference,
        "activation_configuration_digest": (
            configuration_provenance.configuration_digest
        ),
        **RESEARCH_BOUNDARY,
    }
    _validate_control_payload(manifest)
    final = _expected_control_directory(trial, control_id)
    controls_root = final.parent
    stage = controls_root / f".stage-control-{uuid4().hex[:12]}"
    stage_created = False
    with _TrialStoreLock(
        trial,
        operation="activate_prospective_control",
        control_id=control_id,
        clock=clock,
    ):
        if os.path.lexists(final):
            try:
                existing, digest, _ = _read_control(
                    final,
                    trial_root=trial,
                    repository_root=repository,
                    revalidate_model=True,
                )
            except Exception as exc:
                raise MLBHRProspectiveTrialConflictError(
                    "existing control destination is invalid; refusing repair"
                ) from exc
            if existing.get("control_digest") != control_digest:
                raise MLBHRProspectiveTrialConflictError(
                    "existing control destination conflicts with activation"
                )
            return ControlActivationResult(
                control_id=control_id,
                control_dir=final,
                control_manifest_digest=digest,
                model_id=str(model_evidence["model_id"]),
                model_version=str(model_evidence["model_version"]),
                model_bundle_manifest_digest=str(
                    model_evidence["bundle_manifest_digest"]
                ),
                replayed_existing_control=True,
            )
        try:
            controls_root.mkdir(parents=True, exist_ok=True)
            stage.mkdir()
            stage_created = True
            _write_exclusive(
                stage / CONTROL_MANIFEST_FILENAME,
                _canonical_json_file_bytes(manifest),
                CONTROL_MANIFEST_FILENAME,
            )
            staged = _load_json_bytes(
                _read_stable_bytes(
                    stage / CONTROL_MANIFEST_FILENAME, "staged control manifest"
                ),
                "staged control manifest",
            )
            _validate_control_payload(staged)
            _call_failure_hook(failure_hook, "before_control_publication")
            try:
                stage.rename(final)
            except OSError as exc:
                raise MLBHRProspectiveTrialError(
                    "atomic control directory publication failed"
                ) from exc
            stage_created = False
            published, digest, _ = _read_control(
                final,
                trial_root=trial,
                repository_root=repository,
                revalidate_model=True,
            )
            if published.get("control_digest") != control_digest:
                raise MLBHRProspectiveTrialError(
                    "published control failed identity revalidation"
                )
        except Exception:
            if stage_created and os.path.lexists(stage):
                _safe_remove_owned_stage(stage, controls_root, ".stage-control-")
            raise
    return ControlActivationResult(
        control_id=control_id,
        control_dir=final,
        control_manifest_digest=digest,
        model_id=str(model_evidence["model_id"]),
        model_version=str(model_evidence["model_version"]),
        model_bundle_manifest_digest=str(model_evidence["bundle_manifest_digest"]),
        replayed_existing_control=False,
    )


def _identity_cache_rows(
    identity_cache_csv: str | Path | None,
) -> tuple[dict[str, str], ...]:
    if identity_cache_csv is None:
        return ()
    path = Path(identity_cache_csv).expanduser().resolve(strict=False)
    if not path.is_file():
        raise MLBHRProspectiveTrialError("explicit identity cache does not exist")
    return _parse_csv_bytes(
        _read_stable_bytes(path, "identity cache"),
        baseline.IDENTITY_CACHE_COLUMNS,
        "identity cache",
    )


def _identity_decisions(
    feature_rows: Sequence[Mapping[str, object]],
    identity_cache_csv: str | Path | None,
) -> tuple[dict[str, dict[str, str]], str]:
    cache_rows = _identity_cache_rows(identity_cache_csv)
    cache_digest = (
        _file_sha256(identity_cache_csv, "identity cache")
        if identity_cache_csv is not None
        else ""
    )
    by_name: dict[str, list[dict[str, str]]] = {}
    for row in cache_rows:
        normalized = str(row.get("normalized_player_name", ""))
        if normalized:
            by_name.setdefault(normalized, []).append(row)
    decisions: dict[str, dict[str, str]] = {}
    reviewed_statuses = {"reviewed", "approved", "accepted"}
    quarantined_statuses = {
        "quarantined",
        "ambiguous",
        "rejected",
        "manual_review_required",
        "conflict",
    }
    for row in feature_rows:
        normalized = str(row.get("normalized_player_name", ""))
        candidates = by_name.get(normalized, [])
        if not candidates:
            decisions[normalized] = {
                "player_id": "",
                "identity_status": "name_only_research",
                "mapping_version": "",
            }
            continue
        statuses = {str(item.get("identity_status", "")).casefold() for item in candidates}
        versions = {str(item.get("mapping_version", "")) for item in candidates if item.get("mapping_version")}
        ids = {str(item.get("mlb_player_id", "")) for item in candidates if item.get("mlb_player_id")}
        reviewed = [
            item
            for item in candidates
            if str(item.get("identity_status", "")).casefold() == "resolved"
            and str(item.get("review_status", "")).casefold() in reviewed_statuses
            and str(item.get("mlb_player_id", ""))
        ]
        reviewed_pairs = {
            (str(item.get("mlb_player_id", "")), str(item.get("mapping_version", "")))
            for item in reviewed
        }
        if (
            statuses.intersection(quarantined_statuses)
            or len(ids) > 1
            or len(versions) > 1
            or len(reviewed_pairs) > 1
        ):
            decisions[normalized] = {
                "player_id": "",
                "identity_status": "identity_conflict_quarantined",
                "mapping_version": "",
            }
        elif len(reviewed_pairs) == 1:
            player_id, mapping_version = next(iter(reviewed_pairs))
            decisions[normalized] = {
                "player_id": player_id,
                "identity_status": "resolved_reviewed",
                "mapping_version": mapping_version,
            }
        else:
            decisions[normalized] = {
                "player_id": "",
                "identity_status": "name_only_research",
                "mapping_version": next(iter(versions), ""),
            }
    return decisions, cache_digest


def _format_float(value: object) -> str:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{parsed:.12g}" if math.isfinite(parsed) else ""


def _prediction_configuration(
    *,
    control_id: str,
    operating_date: str,
    identity_cache_digest: str,
) -> dict[str, object]:
    return {
        "control_id": control_id,
        "operating_date": operating_date,
        "operating_timezone": OPERATING_TIMEZONE,
        "population_policy": FROZEN_POLICIES["population_policy"],
        "prediction_population_policy": FROZEN_POLICIES[
            "prediction_population_policy"
        ],
        "prediction_snapshot_rule": FROZEN_POLICIES[
            "prediction_snapshot_rule"
        ],
        "sportsbook_selection_rule": FROZEN_POLICIES[
            "sportsbook_selection_rule"
        ],
        "identity_cache_digest": identity_cache_digest,
        "results_access": "disabled",
        "network_access": "disabled",
    }


def _excluded_row(
    *,
    row: Mapping[str, object],
    run_id: str,
    control_id: str,
    operating_date: str,
    source_digest: str,
    identity_status: str = "",
    exclusion_reason: str = "",
) -> dict[str, str]:
    payload = {
        "prediction_run_id": run_id,
        "control_id": control_id,
        **_research_csv_fields(),
        "operating_date": operating_date,
        "event_id": str(row.get("event_id", "")),
        "commence_time_utc": str(
            row.get("commence_time_utc", row.get("commence_time", ""))
        ),
        "player_name": str(row.get("player_name", "")),
        "normalized_player_name": str(row.get("normalized_player_name", "")),
        "identity_status": identity_status,
        "event_eligibility_status": str(
            row.get("event_eligibility_status", "")
        ),
        "selected_snapshot_timestamp_utc": str(row.get("snapshot_time", "")),
        "exclusion_reason": exclusion_reason
        or str(row.get("exclusion_reason", ""))
        or "not_eligible_for_prospective_prediction",
        "source_odds_sha256": source_digest,
    }
    return {column: str(payload.get(column, "")) for column in EXCLUDED_COLUMNS}


def _prediction_row(
    *,
    row: Mapping[str, str],
    identity: Mapping[str, str],
    bundle: baseline.ModelBundle,
    run_id: str,
    control_manifest: Mapping[str, object],
    control_manifest_digest: str,
    git_provenance: GitProvenanceV1,
    prediction_configuration_digest: str,
    operating_date: str,
    prediction_timestamp: datetime,
    source_digest: str,
) -> dict[str, str]:
    probability = baseline.predict_model_probability(row, bundle)
    try:
        implied = float(row.get("implied_probability", ""))
    except ValueError as exc:
        raise MLBHRProspectiveTrialError(
            "eligible prediction row has invalid implied probability"
        ) from exc
    material = control_manifest.get("identity_material")
    if not isinstance(material, Mapping):
        raise MLBHRProspectiveTrialError("control identity material is invalid")
    prediction_id = (
        "mlb-hr-pred-v1-"
        + _canonical_sha256(
            {
                "schema_version": PREDICTION_SCHEMA_VERSION,
                "prediction_run_id": run_id,
                "event_id": row.get("event_id", ""),
                "normalized_player_name": row.get("normalized_player_name", ""),
                "sportsbook": row.get("sportsbook", ""),
                "snapshot_time": row.get("snapshot_time", ""),
                "market_key": row.get("market_key", ""),
                "point": row.get("point", ""),
            }
        )[:24]
    )
    payload = {
        "prediction_schema_version": PREDICTION_SCHEMA_VERSION,
        "prediction_id": prediction_id,
        "prediction_run_id": run_id,
        "control_id": str(control_manifest["control_id"]),
        "control_manifest_digest": control_manifest_digest,
        "model_id": bundle.model_id,
        "model_version": bundle.model_version,
        "model_bundle_manifest_digest": str(
            material.get("model_bundle_manifest_digest", "")
        ),
        "feature_schema_version": str(
            material.get("model_feature_schema_version", "")
        ),
        "prediction_git_commit": git_provenance.commit_sha,
        "prediction_tree_fingerprint": git_provenance.working_tree_fingerprint,
        "prediction_configuration_digest": prediction_configuration_digest,
        **_research_csv_fields(),
        "operating_date": operating_date,
        "operating_timezone": OPERATING_TIMEZONE,
        "event_id": row.get("event_id", ""),
        "commence_time_utc": row.get(
            "commence_time_utc", row.get("commence_time", "")
        ),
        "home_team": row.get("home_team", ""),
        "away_team": row.get("away_team", ""),
        "player_id": identity.get("player_id", ""),
        "player_name": row.get("player_name", ""),
        "normalized_player_name": row.get("normalized_player_name", ""),
        "identity_status": identity.get("identity_status", "name_only_research"),
        "identity_mapping_version": identity.get("mapping_version", ""),
        "sportsbook": row.get("sportsbook", ""),
        "sportsbook_name": row.get("sportsbook_name", ""),
        "prediction_time_price": row.get("american_odds", ""),
        "prediction_time_decimal_odds": row.get("decimal_odds", ""),
        "implied_probability": row.get("implied_probability", ""),
        "model_probability": _format_float(probability),
        "probability_edge": _format_float(probability - implied),
        "prediction_timestamp_utc": _utc_text(prediction_timestamp),
        "selected_snapshot_timestamp_utc": row.get("snapshot_time", ""),
        "source_odds_sha256": source_digest,
        "snapshot_rule": baseline.PREDICTION_SNAPSHOT_SELECTION_RULE,
        "sportsbook_rule": baseline.BOOKMAKER_SELECTION_RULE,
        "market_key": row.get("market_key", ""),
        "side": row.get("side", ""),
        "point": row.get("point", ""),
        "integrity_status": "verified_pregame_research_prediction",
    }
    return {column: str(payload.get(column, "")) for column in PREDICTION_COLUMNS}


def _read_ledger(path: Path) -> tuple[dict[str, str], ...]:
    if not path.exists():
        return ()
    if path.is_symlink() or not path.is_file():
        raise MLBHRProspectiveTrialError("prospective ledger is not a regular file")
    rows = _parse_csv_bytes(
        _read_stable_bytes(path, "prospective ledger"),
        LEDGER_COLUMNS,
        "prospective ledger",
    )
    prediction_ids: set[str] = set()
    settlement_ids: set[str] = set()
    predictions_by_id: dict[str, dict[str, str]] = {}
    immutable_fields = LEDGER_COLUMNS[3:33]
    for index, row in enumerate(rows, start=2):
        _validate_research_csv_row(row, f"prospective ledger row {index}")
        if row["ledger_schema_version"] != LEDGER_SCHEMA_VERSION:
            raise MLBHRProspectiveTrialError(
                "prospective ledger contains a non-v2 or legacy row"
            )
        prediction_id = row["prediction_id"]
        if not prediction_id:
            raise MLBHRProspectiveTrialError("prospective ledger row lacks prediction_id")
        if row["record_type"] == "prediction":
            if prediction_id in prediction_ids:
                raise MLBHRProspectiveTrialError(
                    f"duplicate prediction ledger row: {prediction_id}"
                )
            prediction_ids.add(prediction_id)
            predictions_by_id[prediction_id] = row
        elif row["record_type"] == "settlement":
            if prediction_id in settlement_ids:
                raise MLBHRProspectiveTrialError(
                    f"duplicate settlement ledger row: {prediction_id}"
                )
            settlement_ids.add(prediction_id)
        else:
            raise MLBHRProspectiveTrialError("prospective ledger record_type is invalid")
    for row in rows:
        if row["record_type"] != "settlement":
            continue
        original = predictions_by_id.get(row["prediction_id"])
        if original is None:
            raise MLBHRProspectiveTrialError(
                "prospective settlement references no committed prediction"
            )
        for field_name in immutable_fields:
            if row[field_name] != original[field_name]:
                raise MLBHRProspectiveTrialError(
                    "prospective settlement changed immutable prediction field "
                    + field_name
                )
    return rows


def _transactional_append_csv(
    *,
    path: Path,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, object]],
    description: str,
) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = b""
    if path.exists():
        existing = _read_stable_bytes(path, description)
        _parse_csv_bytes(existing, columns, description)
        if not existing.endswith(b"\n"):
            raise MLBHRProspectiveTrialError(
                f"{description} does not end at a complete record boundary"
            )
        appended = _csv_bytes(columns, rows)
        header_end = appended.find(b"\n")
        if header_end < 0:
            raise MLBHRProspectiveTrialError(f"could not serialize {description}")
        combined = existing + appended[header_end + 1 :]
    else:
        combined = _csv_bytes(columns, rows)
    temporary = path.parent / f".append-{uuid4().hex[:12]}.tmp"
    try:
        _write_exclusive(temporary, combined, description)
        os.replace(temporary, path)
    except OSError as exc:
        raise MLBHRProspectiveTrialError(
            f"transactional append failed for {description}"
        ) from exc
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _prediction_ledger_row(
    prediction: Mapping[str, str],
    *,
    prediction_manifest_digest: str,
    predictions_csv_sha256: str,
) -> dict[str, str]:
    payload = {
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "ledger_record_id": (
            "mlb-hr-ledger-v2-"
            + _canonical_sha256(
                {
                    "record_type": "prediction",
                    "prediction_id": prediction["prediction_id"],
                }
            )[:24]
        ),
        "record_type": "prediction",
        "prediction_id": prediction["prediction_id"],
        "prediction_run_id": prediction["prediction_run_id"],
        "control_id": prediction["control_id"],
        "control_manifest_digest": prediction["control_manifest_digest"],
        "model_id": prediction["model_id"],
        "model_version": prediction["model_version"],
        "model_bundle_manifest_digest": prediction[
            "model_bundle_manifest_digest"
        ],
        "feature_schema_version": prediction["feature_schema_version"],
        "prediction_git_commit": prediction["prediction_git_commit"],
        "prediction_tree_fingerprint": prediction[
            "prediction_tree_fingerprint"
        ],
        "prediction_configuration_digest": prediction[
            "prediction_configuration_digest"
        ],
        "operating_date": prediction["operating_date"],
        "event_id": prediction["event_id"],
        "commence_time_utc": prediction["commence_time_utc"],
        "player_id": prediction["player_id"],
        "player_name": prediction["player_name"],
        "normalized_player_name": prediction["normalized_player_name"],
        "identity_status": prediction["identity_status"],
        "identity_mapping_version": prediction["identity_mapping_version"],
        "sportsbook": prediction["sportsbook"],
        "original_american_odds": prediction["prediction_time_price"],
        "original_decimal_odds": prediction["prediction_time_decimal_odds"],
        "original_implied_probability": prediction["implied_probability"],
        "model_probability": prediction["model_probability"],
        "probability_edge": prediction["probability_edge"],
        "prediction_timestamp_utc": prediction["prediction_timestamp_utc"],
        "selected_snapshot_timestamp_utc": prediction[
            "selected_snapshot_timestamp_utc"
        ],
        "source_odds_sha256": prediction["source_odds_sha256"],
        "prediction_manifest_digest": prediction_manifest_digest,
        "predictions_csv_sha256": predictions_csv_sha256,
        "settlement_status": "unsettled",
        "strict_result_status": "",
        "final_hr_outcome": "",
        "grade": "",
        "unit_profit_loss": "",
        "results_sha256": "",
        "settlement_timestamp_utc": "",
        "integrity_status": "prediction_committed",
        **_research_csv_fields(),
    }
    return {column: str(payload.get(column, "")) for column in LEDGER_COLUMNS}


def _append_prediction_ledger_rows(
    *,
    ledger_path: Path,
    predictions: Sequence[Mapping[str, str]],
    prediction_manifest_digest: str,
    predictions_csv_sha256: str,
) -> tuple[int, int]:
    existing = _read_ledger(ledger_path)
    by_id = {
        row["prediction_id"]: row
        for row in existing
        if row["record_type"] == "prediction"
    }
    new_rows: list[dict[str, str]] = []
    skipped = 0
    for prediction in predictions:
        candidate = _prediction_ledger_row(
            prediction,
            prediction_manifest_digest=prediction_manifest_digest,
            predictions_csv_sha256=predictions_csv_sha256,
        )
        prior = by_id.get(candidate["prediction_id"])
        if prior is None:
            new_rows.append(candidate)
            continue
        if prior != candidate:
            raise MLBHRProspectiveTrialConflictError(
                "conflicting prediction ledger replay for "
                + candidate["prediction_id"]
            )
        skipped += 1
    _transactional_append_csv(
        path=ledger_path,
        columns=LEDGER_COLUMNS,
        rows=new_rows,
        description="prospective ledger",
    )
    return len(new_rows), skipped


def _verify_ledger_linkage(
    *,
    ledger_path: Path,
    predictions: Sequence[Mapping[str, str]],
    prediction_manifest_digest: str,
    predictions_csv_sha256: str,
) -> None:
    if not predictions:
        return
    rows = _read_ledger(ledger_path)
    by_id = {
        row["prediction_id"]: row
        for row in rows
        if row["record_type"] == "prediction"
    }
    for prediction in predictions:
        expected = _prediction_ledger_row(
            prediction,
            prediction_manifest_digest=prediction_manifest_digest,
            predictions_csv_sha256=predictions_csv_sha256,
        )
        if by_id.get(prediction["prediction_id"]) != expected:
            raise MLBHRProspectiveTrialError(
                "prediction artifact lacks exact canonical ledger linkage"
            )


def _validate_prediction_artifact(
    *,
    predictions_csv: str | Path,
    control_manifest: Mapping[str, object],
    control_manifest_digest: str,
    control_dir: Path,
    expected_run_id: str | None = None,
    expected_operating_date: str | None = None,
) -> tuple[
    tuple[dict[str, str], ...],
    tuple[dict[str, str], ...],
    dict[str, Any],
    str,
    Path,
]:
    supplied_predictions_path = Path(predictions_csv).expanduser()
    if supplied_predictions_path.is_symlink():
        raise MLBHRProspectiveTrialError(
            "prospective prediction artifact may not be a symlink"
        )
    predictions_path = supplied_predictions_path.resolve(strict=False)
    if predictions_path.name != "predictions.csv" or not predictions_path.is_file():
        raise MLBHRProspectiveTrialError(
            "predictions-csv must be an existing prospective predictions.csv"
        )
    run_dir = predictions_path.parent
    if run_dir.is_symlink():
        raise MLBHRProspectiveTrialError(
            "prospective prediction run may not be a symlink"
        )
    try:
        relative_parts = run_dir.relative_to(control_dir).parts
    except ValueError as exc:
        raise MLBHRProspectiveTrialError(
            "prediction artifact is outside the explicit control"
        ) from exc
    if len(relative_parts) != 3 or relative_parts[0] != "dates":
        raise MLBHRProspectiveTrialError(
            "prediction artifact does not use the v1 control/date/run layout"
        )
    operating_date = expected_operating_date or relative_parts[1]
    run_id = expected_run_id or relative_parts[2]
    if (
        not operating_date
        or not run_id
        or (expected_operating_date is None and relative_parts[1].startswith("."))
        or (expected_run_id is None and relative_parts[2].startswith("."))
    ):
        raise MLBHRProspectiveTrialError(
            "prediction artifact does not use an admissible date/run identity"
        )
    required_names = {
        "predictions.csv",
        "excluded_rows.csv",
        PREDICTION_MANIFEST_FILENAME,
        RUN_SUMMARY_FILENAME,
    }
    try:
        entries = tuple(run_dir.iterdir())
        names = {entry.name for entry in entries}
    except OSError as exc:
        raise MLBHRProspectiveTrialError("prediction run directory is inaccessible") from exc
    if names != required_names:
        raise MLBHRProspectiveTrialError(
            "prediction run contains an unexpected or incomplete artifact set"
        )
    if any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise MLBHRProspectiveTrialError(
            "prediction run artifacts must be regular files"
        )
    predictions_bytes = _read_stable_bytes(predictions_path, "predictions.csv")
    excluded_bytes = _read_stable_bytes(
        run_dir / "excluded_rows.csv", "excluded_rows.csv"
    )
    manifest_bytes = _read_stable_bytes(
        run_dir / PREDICTION_MANIFEST_FILENAME, "prediction manifest"
    )
    summary_bytes = _read_stable_bytes(
        run_dir / RUN_SUMMARY_FILENAME, "prediction run summary"
    )
    predictions = _parse_csv_bytes(
        predictions_bytes, PREDICTION_COLUMNS, "predictions.csv"
    )
    exclusions = _parse_csv_bytes(
        excluded_bytes, EXCLUDED_COLUMNS, "excluded_rows.csv"
    )
    manifest = _load_json_bytes(manifest_bytes, "prediction manifest")
    summary = _load_json_bytes(summary_bytes, "prediction run summary")
    manifest_fields = {
        "schema_version",
        "prediction_run_id",
        "control_id",
        "control_manifest_digest",
        "model_id",
        "model_version",
        "model_bundle_manifest_digest",
        "feature_schema_version",
        "prediction_git_provenance",
        "prediction_configuration",
        "prediction_configuration_digest",
        "operating_date",
        "operating_timezone",
        "prediction_timestamp_utc",
        "source_odds_reference",
        "source_odds_sha256",
        "prediction_count",
        "exclusion_count",
        "predictions_csv_sha256",
        "excluded_rows_csv_sha256",
        "snapshot_rule",
        "sportsbook_rule",
        "population_policy",
        "completion_rule",
        *RESEARCH_BOUNDARY.keys(),
    }
    summary_fields = {
        "schema_version",
        "prediction_run_id",
        "control_id",
        "operating_date",
        "artifact_status",
        "prediction_count",
        "exclusion_count",
        "prediction_manifest_digest",
        "canonical_ledger_reference",
        "completion_rule",
        *RESEARCH_BOUNDARY.keys(),
    }
    if set(manifest) != manifest_fields or set(summary) != summary_fields:
        raise MLBHRProspectiveTrialError(
            "prediction manifest or summary fields do not match v1"
        )
    if _contains_forbidden_secret_key(manifest) or _contains_forbidden_secret_key(
        summary
    ):
        raise MLBHRProspectiveTrialError(
            "prediction artifact contains a forbidden secret-like field"
        )
    if manifest.get("schema_version") != PREDICTION_MANIFEST_SCHEMA_VERSION:
        raise MLBHRProspectiveTrialError(
            "prediction manifest schema is not admissible prospective v1"
        )
    _validate_research_mapping(manifest, "prediction manifest")
    if summary.get("schema_version") != RUN_SUMMARY_SCHEMA_VERSION:
        raise MLBHRProspectiveTrialError("prediction run summary schema is invalid")
    _validate_research_mapping(summary, "prediction run summary")
    if manifest.get("control_id") != control_manifest.get("control_id"):
        raise MLBHRProspectiveTrialError("prediction control_id does not match control")
    if manifest.get("control_manifest_digest") != control_manifest_digest:
        raise MLBHRProspectiveTrialError(
            "prediction control manifest digest does not match control bytes"
        )
    material = control_manifest.get("identity_material")
    if not isinstance(material, Mapping):
        raise MLBHRProspectiveTrialError("control identity material is invalid")
    expected_manifest_identity = {
        "model_id": material.get("model_id"),
        "model_version": material.get("model_version"),
        "model_bundle_manifest_digest": material.get(
            "model_bundle_manifest_digest"
        ),
        "feature_schema_version": material.get("model_feature_schema_version"),
    }
    if any(
        manifest.get(field_name) != expected_value
        for field_name, expected_value in expected_manifest_identity.items()
    ):
        raise MLBHRProspectiveTrialError(
            "prediction model identity does not match the frozen control"
        )
    frozen_git = _git_from_mapping(material.get("activation_git_provenance"))
    prediction_git = _git_from_mapping(manifest.get("prediction_git_provenance"))
    if prediction_git != frozen_git:
        raise MLBHRProspectiveTrialError(
            "prediction Git provenance does not match the frozen control"
        )
    configuration = manifest.get("prediction_configuration")
    if not isinstance(configuration, Mapping) or _contains_forbidden_secret_key(
        configuration
    ):
        raise MLBHRProspectiveTrialError(
            "prediction configuration is invalid"
        )
    if manifest.get("prediction_configuration_digest") != _canonical_sha256(
        configuration
    ):
        raise MLBHRProspectiveTrialError(
            "prediction configuration digest does not match"
        )
    if configuration.get("control_id") != control_manifest.get("control_id"):
        raise MLBHRProspectiveTrialError(
            "prediction configuration control identity does not match"
        )
    if configuration.get("operating_date") != operating_date:
        raise MLBHRProspectiveTrialError(
            "prediction configuration operating date does not match"
        )
    if manifest.get("operating_timezone") != OPERATING_TIMEZONE:
        raise MLBHRProspectiveTrialError("prediction operating timezone is invalid")
    _parse_utc(manifest.get("prediction_timestamp_utc"), "prediction timestamp")
    if not _is_sha256(manifest.get("source_odds_sha256")):
        raise MLBHRProspectiveTrialError("prediction source digest is invalid")
    source_reference = manifest.get("source_odds_reference")
    if not isinstance(source_reference, Mapping) or set(source_reference) != {
        "base",
        "path",
    }:
        raise MLBHRProspectiveTrialError("prediction source reference is invalid")
    source_base = source_reference.get("base")
    source_relative = str(source_reference.get("path", ""))
    if (
        source_base not in {"repository", "trial"}
        or not source_relative
        or Path(source_relative).is_absolute()
        or ".." in Path(source_relative).parts
    ):
        raise MLBHRProspectiveTrialError("prediction source reference is unsafe")
    if (
        manifest.get("snapshot_rule") != FROZEN_POLICIES["prediction_snapshot_rule"]
        or manifest.get("sportsbook_rule")
        != FROZEN_POLICIES["sportsbook_selection_rule"]
        or manifest.get("population_policy") != FROZEN_POLICIES["population_policy"]
        or manifest.get("completion_rule")
        != "completed_only_after_exact_canonical_ledger_linkage"
    ):
        raise MLBHRProspectiveTrialError("prediction frozen policy binding is invalid")
    if manifest.get("prediction_run_id") != run_id:
        raise MLBHRProspectiveTrialError(
            "prediction run identity does not match its directory"
        )
    if manifest.get("operating_date") != operating_date:
        raise MLBHRProspectiveTrialError(
            "prediction operating date does not match its directory"
        )
    if manifest.get("predictions_csv_sha256") != _sha256_bytes(predictions_bytes):
        raise MLBHRProspectiveTrialError("predictions.csv digest does not match manifest")
    if manifest.get("excluded_rows_csv_sha256") != _sha256_bytes(excluded_bytes):
        raise MLBHRProspectiveTrialError(
            "excluded_rows.csv digest does not match manifest"
        )
    if manifest.get("prediction_count") != len(predictions):
        raise MLBHRProspectiveTrialError("prediction count does not match manifest")
    if manifest.get("exclusion_count") != len(exclusions):
        raise MLBHRProspectiveTrialError("exclusion count does not match manifest")
    if summary.get("prediction_run_id") != manifest.get("prediction_run_id"):
        raise MLBHRProspectiveTrialError("run summary identity does not match manifest")
    if summary.get("prediction_manifest_digest") != _sha256_bytes(manifest_bytes):
        raise MLBHRProspectiveTrialError(
            "run summary prediction manifest digest does not match"
        )
    expected_artifact_status = (
        "artifact_prepared_pending_ledger_linkage"
        if predictions
        else "artifact_prepared_no_predictions"
    )
    if (
        summary.get("control_id") != manifest.get("control_id")
        or summary.get("operating_date") != manifest.get("operating_date")
        or summary.get("prediction_count") != len(predictions)
        or summary.get("exclusion_count") != len(exclusions)
        or summary.get("artifact_status") != expected_artifact_status
        or summary.get("canonical_ledger_reference") != "prospective_ledger.csv"
        or summary.get("completion_rule") != "ledger_linkage_is_authoritative"
    ):
        raise MLBHRProspectiveTrialError(
            "prediction run summary does not match the validated artifact"
        )
    prediction_ids: set[str] = set()
    for index, row in enumerate(predictions, start=2):
        _validate_research_csv_row(row, f"predictions.csv row {index}")
        if row["prediction_schema_version"] != PREDICTION_SCHEMA_VERSION:
            raise MLBHRProspectiveTrialError(
                "prediction row is not prospective prediction v1"
            )
        if row["prediction_id"] in prediction_ids:
            raise MLBHRProspectiveTrialError("duplicate prospective prediction_id")
        prediction_ids.add(row["prediction_id"])
        for field_name in (
            "prediction_run_id",
            "control_id",
            "control_manifest_digest",
            "model_id",
            "model_version",
            "model_bundle_manifest_digest",
            "feature_schema_version",
            "prediction_git_commit",
            "prediction_tree_fingerprint",
            "prediction_configuration_digest",
            "operating_date",
            "event_id",
            "commence_time_utc",
            "normalized_player_name",
            "sportsbook",
            "prediction_time_price",
            "prediction_time_decimal_odds",
            "implied_probability",
            "model_probability",
            "probability_edge",
            "prediction_timestamp_utc",
            "selected_snapshot_timestamp_utc",
            "source_odds_sha256",
            "market_key",
            "side",
            "point",
        ):
            if not row[field_name]:
                raise MLBHRProspectiveTrialError(
                    f"prospective prediction row is missing {field_name}"
                )
        if row["prediction_run_id"] != manifest["prediction_run_id"]:
            raise MLBHRProspectiveTrialError(
                "prediction row run identity does not match manifest"
            )
        if row["control_id"] != control_manifest["control_id"]:
            raise MLBHRProspectiveTrialError(
                "prediction row control identity does not match"
            )
        expected_row_bindings = {
            "prediction_run_id": str(manifest["prediction_run_id"]),
            "control_manifest_digest": control_manifest_digest,
            "model_id": str(manifest["model_id"]),
            "model_version": str(manifest["model_version"]),
            "model_bundle_manifest_digest": str(
                manifest["model_bundle_manifest_digest"]
            ),
            "feature_schema_version": str(manifest["feature_schema_version"]),
            "prediction_git_commit": prediction_git.commit_sha,
            "prediction_tree_fingerprint": (
                prediction_git.working_tree_fingerprint
            ),
            "prediction_configuration_digest": str(
                manifest["prediction_configuration_digest"]
            ),
            "operating_date": operating_date,
            "operating_timezone": OPERATING_TIMEZONE,
            "prediction_timestamp_utc": str(
                manifest["prediction_timestamp_utc"]
            ),
            "source_odds_sha256": str(manifest["source_odds_sha256"]),
            "snapshot_rule": str(manifest["snapshot_rule"]),
            "sportsbook_rule": str(manifest["sportsbook_rule"]),
        }
        if any(
            row[field_name] != expected_value
            for field_name, expected_value in expected_row_bindings.items()
        ):
            raise MLBHRProspectiveTrialError(
                "prediction row provenance does not match its manifest"
            )
        expected_prediction_id = (
            "mlb-hr-pred-v1-"
            + _canonical_sha256(
                {
                    "schema_version": PREDICTION_SCHEMA_VERSION,
                    "prediction_run_id": row["prediction_run_id"],
                    "event_id": row["event_id"],
                    "normalized_player_name": row["normalized_player_name"],
                    "sportsbook": row["sportsbook"],
                    "snapshot_time": row["selected_snapshot_timestamp_utc"],
                    "market_key": row["market_key"],
                    "point": row["point"],
                }
            )[:24]
        )
        if row["prediction_id"] != expected_prediction_id:
            raise MLBHRProspectiveTrialError(
                "prediction_id does not match immutable prediction content"
            )
        prediction_time = _parse_utc(
            row["prediction_timestamp_utc"], "prediction timestamp"
        )
        snapshot_time = _parse_utc(
            row["selected_snapshot_timestamp_utc"], "snapshot timestamp"
        )
        commence_time = _parse_utc(row["commence_time_utc"], "commence time")
        if not (snapshot_time <= prediction_time < commence_time):
            raise MLBHRProspectiveTrialError(
                "prediction or snapshot timestamp is not strictly pregame"
            )
        if row["identity_status"] not in {
            "resolved_reviewed",
            "name_only_research",
        }:
            raise MLBHRProspectiveTrialError(
                "prediction row has a non-admissible identity status"
            )
        if row["identity_status"] == "resolved_reviewed" and (
            not row["player_id"] or not row["identity_mapping_version"]
        ):
            raise MLBHRProspectiveTrialError(
                "reviewed prediction identity lacks its exact mapping"
            )
        if row["identity_status"] == "name_only_research" and row["player_id"]:
            raise MLBHRProspectiveTrialError(
                "name-only prediction may not infer a player ID"
            )
        try:
            american_odds = int(row["prediction_time_price"])
            decimal_odds = float(row["prediction_time_decimal_odds"])
            implied_probability = float(row["implied_probability"])
            model_probability = float(row["model_probability"])
            probability_edge = float(row["probability_edge"])
        except ValueError as exc:
            raise MLBHRProspectiveTrialError(
                "prediction probability evidence is invalid"
            ) from exc
        if (
            not all(
                math.isfinite(value)
                for value in (
                    decimal_odds,
                    implied_probability,
                    model_probability,
                    probability_edge,
                )
            )
            or american_odds == 0
            or decimal_odds <= 1
            or not 0 < implied_probability < 1
            or not 0 <= model_probability <= 1
            or not math.isclose(
                decimal_odds,
                baseline.american_to_decimal(american_odds),
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            or not math.isclose(
                implied_probability,
                baseline.american_to_implied_probability(american_odds),
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            or not math.isclose(
                probability_edge,
                model_probability - implied_probability,
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
        ):
            raise MLBHRProspectiveTrialError(
                "prediction probability evidence is inconsistent"
            )
    for index, row in enumerate(exclusions, start=2):
        _validate_research_csv_row(row, f"excluded_rows.csv row {index}")
        if (
            row["prediction_run_id"] != manifest["prediction_run_id"]
            or row["control_id"] != manifest["control_id"]
            or row["operating_date"] != operating_date
            or row["source_odds_sha256"] != manifest["source_odds_sha256"]
        ):
            raise MLBHRProspectiveTrialError(
                "excluded row provenance does not match manifest"
            )
    return (
        predictions,
        exclusions,
        manifest,
        _sha256_bytes(manifest_bytes),
        run_dir,
    )


def run_prospective_paper_day(
    *,
    target_date: str,
    control_dir: str | Path,
    odds_csv: str | Path,
    trial_root: str | Path,
    repository_root: str | Path,
    identity_cache_csv: str | Path | None = None,
    dry_run: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    failure_hook: Callable[[str], None] | None = None,
) -> ProspectivePaperRunResult:
    """Generate and transactionally link one explicit pregame paper day."""

    try:
        operating_date = date.fromisoformat(str(target_date)).isoformat()
    except ValueError as exc:
        raise MLBHRProspectiveTrialError("date must be YYYY-MM-DD") from exc
    repository = Path(repository_root).expanduser().resolve(strict=False)
    trial = Path(trial_root).expanduser().resolve(strict=False)
    control, control_digest, frozen_control_dir = _read_control(
        control_dir,
        trial_root=trial,
        repository_root=repository,
        revalidate_model=True,
    )
    identity_material = control["identity_material"]
    if not isinstance(identity_material, Mapping):
        raise MLBHRProspectiveTrialError("control identity material is invalid")
    frozen_git = _git_from_mapping(
        identity_material.get("activation_git_provenance")
    )
    _validate_current_git(frozen_git, repository)
    current_git = _capture_clean_git(repository)
    model_path = _resolve_reference(
        control["model_bundle_reference"],  # type: ignore[arg-type]
        repository_root=repository,
        trial_root=trial,
    )
    try:
        bundle = baseline.load_model_bundle(model_path)
    except baseline.MLBHRResearchBaselineError as exc:
        raise MLBHRProspectiveTrialError("frozen model bundle cannot be loaded") from exc
    odds_path = Path(odds_csv).expanduser().resolve(strict=False)
    if not odds_path.is_file():
        raise MLBHRProspectiveTrialError("explicit odds CSV does not exist")
    odds_reference = _safe_relative_reference(
        odds_path,
        repository_root=repository,
        trial_root=trial,
    )
    source_digest_before = _file_sha256(odds_path, "source odds CSV")
    prediction_timestamp = _clock_value(clock, "prediction timestamp")
    try:
        feature_result = baseline.build_live_hr_research_features(
            odds_path=odds_path,
            results_path=None,
            target_date=operating_date,
            prediction_timestamp=prediction_timestamp,
            mode="prediction",
            generated_at=prediction_timestamp,
            repository_root=repository,
        )
    except baseline.MLBHRResearchBaselineError as exc:
        raise MLBHRProspectiveTrialError(
            "existing MLB prediction feature contract rejected the odds source"
        ) from exc
    source_digest_after_read = _file_sha256(odds_path, "source odds CSV")
    if source_digest_after_read != source_digest_before:
        raise MLBHRProspectiveTrialError(
            "source odds CSV changed while it was being read"
        )
    eligible_features = [
        row
        for row in feature_result.rows
        if row.get("eligibility_status") == baseline.PREDICTION_ELIGIBLE_STATUS
        and not row.get("exclusion_reason")
    ]
    decisions, identity_cache_digest = _identity_decisions(
        eligible_features, identity_cache_csv
    )
    configuration = _prediction_configuration(
        control_id=str(control["control_id"]),
        operating_date=operating_date,
        identity_cache_digest=identity_cache_digest,
    )
    configuration_digest = _canonical_sha256(configuration)
    run_digest = _canonical_sha256(
        {
            "schema_version": PREDICTION_MANIFEST_SCHEMA_VERSION,
            "control_id": control["control_id"],
            "control_manifest_digest": control_digest,
            "operating_date": operating_date,
            "prediction_timestamp_utc": _utc_text(prediction_timestamp),
            "source_odds_sha256": source_digest_before,
            "prediction_configuration_digest": configuration_digest,
        }
    )
    # Keep the immutable identifier compact enough for nested Windows temp
    # repositories while retaining 64 bits of content-addressed identity.
    run_id = f"hrv1-{run_digest[:16]}"
    exclusions: list[dict[str, str]] = [
        _excluded_row(
            row=row,
            run_id=run_id,
            control_id=str(control["control_id"]),
            operating_date=operating_date,
            source_digest=source_digest_before,
        )
        for row in feature_result.rows
        if row.get("eligibility_status") != baseline.PREDICTION_ELIGIBLE_STATUS
        or row.get("exclusion_reason")
    ]
    predictions: list[dict[str, str]] = []
    for row in eligible_features:
        normalized = row.get("normalized_player_name", "")
        identity = decisions.get(
            normalized,
            {
                "player_id": "",
                "identity_status": "name_only_research",
                "mapping_version": "",
            },
        )
        if identity["identity_status"] == "identity_conflict_quarantined":
            exclusions.append(
                _excluded_row(
                    row=row,
                    run_id=run_id,
                    control_id=str(control["control_id"]),
                    operating_date=operating_date,
                    source_digest=source_digest_before,
                    identity_status=identity["identity_status"],
                    exclusion_reason="identity_conflict_quarantined",
                )
            )
            continue
        predictions.append(
            _prediction_row(
                row=row,
                identity=identity,
                bundle=bundle,
                run_id=run_id,
                control_manifest=control,
                control_manifest_digest=control_digest,
                git_provenance=current_git,
                prediction_configuration_digest=configuration_digest,
                operating_date=operating_date,
                prediction_timestamp=prediction_timestamp,
                source_digest=source_digest_before,
            )
        )
    predictions.sort(
        key=lambda row: (
            row["event_id"],
            row["normalized_player_name"],
            row["sportsbook"],
        )
    )
    exclusions.sort(
        key=lambda row: (
            row["event_id"],
            row["normalized_player_name"],
            row["exclusion_reason"],
        )
    )
    if dry_run:
        if _file_sha256(odds_path, "source odds CSV") != source_digest_before:
            raise MLBHRProspectiveTrialError("source odds CSV changed during the run")
        return ProspectivePaperRunResult(
            status="dry_run",
            control_id=str(control["control_id"]),
            prediction_run_id=run_id,
            operating_date=operating_date,
            prediction_count=len(predictions),
            exclusion_count=len(exclusions),
            prediction_manifest_digest="",
            run_dir=None,
            ledger_rows_appended=0,
            replayed_existing_run=False,
            predictions=tuple(predictions),
            exclusions=tuple(exclusions),
        )

    predictions_bytes = _csv_bytes(PREDICTION_COLUMNS, predictions)
    excluded_bytes = _csv_bytes(EXCLUDED_COLUMNS, exclusions)
    manifest = {
        "schema_version": PREDICTION_MANIFEST_SCHEMA_VERSION,
        "prediction_run_id": run_id,
        "control_id": control["control_id"],
        "control_manifest_digest": control_digest,
        "model_id": bundle.model_id,
        "model_version": bundle.model_version,
        "model_bundle_manifest_digest": identity_material[
            "model_bundle_manifest_digest"
        ],
        "feature_schema_version": identity_material[
            "model_feature_schema_version"
        ],
        "prediction_git_provenance": current_git.to_dict(),
        "prediction_configuration": configuration,
        "prediction_configuration_digest": configuration_digest,
        "operating_date": operating_date,
        "operating_timezone": OPERATING_TIMEZONE,
        "prediction_timestamp_utc": _utc_text(prediction_timestamp),
        "source_odds_reference": odds_reference,
        "source_odds_sha256": source_digest_before,
        "prediction_count": len(predictions),
        "exclusion_count": len(exclusions),
        "predictions_csv_sha256": _sha256_bytes(predictions_bytes),
        "excluded_rows_csv_sha256": _sha256_bytes(excluded_bytes),
        "snapshot_rule": baseline.PREDICTION_SNAPSHOT_SELECTION_RULE,
        "sportsbook_rule": baseline.BOOKMAKER_SELECTION_RULE,
        "population_policy": FROZEN_POLICIES["population_policy"],
        "completion_rule": "completed_only_after_exact_canonical_ledger_linkage",
        **RESEARCH_BOUNDARY,
    }
    manifest_bytes = _canonical_json_file_bytes(manifest)
    manifest_digest = _sha256_bytes(manifest_bytes)
    artifact_status = (
        "artifact_prepared_no_predictions"
        if not predictions
        else "artifact_prepared_pending_ledger_linkage"
    )
    summary = {
        "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
        "prediction_run_id": run_id,
        "control_id": control["control_id"],
        "operating_date": operating_date,
        "artifact_status": artifact_status,
        "prediction_count": len(predictions),
        "exclusion_count": len(exclusions),
        "prediction_manifest_digest": manifest_digest,
        "canonical_ledger_reference": "prospective_ledger.csv",
        "completion_rule": "ledger_linkage_is_authoritative",
        **RESEARCH_BOUNDARY,
    }
    summary_bytes = _canonical_json_file_bytes(summary)
    dates_parent = frozen_control_dir / "dates"
    dates_root = dates_parent / operating_date
    dates_parent_existed = dates_parent.exists()
    dates_root_existed = dates_root.exists()
    final = dates_root / run_id
    stage = dates_root / f".stage-run-{uuid4().hex[:12]}"
    stage_created = False
    ledger_path = frozen_control_dir / "prospective_ledger.csv"
    try:
        dates_root.mkdir(parents=True, exist_ok=True)
        stage.mkdir()
        stage_created = True
        _write_exclusive(stage / "predictions.csv", predictions_bytes, "predictions.csv")
        _write_exclusive(
            stage / "excluded_rows.csv", excluded_bytes, "excluded_rows.csv"
        )
        _write_exclusive(
            stage / PREDICTION_MANIFEST_FILENAME,
            manifest_bytes,
            PREDICTION_MANIFEST_FILENAME,
        )
        _write_exclusive(
            stage / RUN_SUMMARY_FILENAME,
            summary_bytes,
            RUN_SUMMARY_FILENAME,
        )
        if _file_sha256(odds_path, "source odds CSV") != source_digest_before:
            raise MLBHRProspectiveTrialError("source odds CSV changed during the run")
        _validate_prediction_artifact(
            predictions_csv=stage / "predictions.csv",
            control_manifest=control,
            control_manifest_digest=control_digest,
            control_dir=frozen_control_dir,
            expected_run_id=run_id,
            expected_operating_date=operating_date,
        )
        _call_failure_hook(failure_hook, "before_prediction_publication")
    except Exception:
        if stage_created and os.path.lexists(stage):
            _safe_remove_owned_stage(stage, dates_root, ".stage-run-")
        if not dates_root_existed and dates_root.exists():
            try:
                dates_root.rmdir()
            except OSError:
                pass
        if not dates_parent_existed and dates_parent.exists():
            try:
                dates_parent.rmdir()
            except OSError:
                pass
        raise
    lock = _TrialStoreLock(
        trial,
        operation="run_prospective_paper_day",
        control_id=str(control["control_id"]),
        clock=clock,
    )
    try:
        lock.acquire()
    except Exception:
        if stage_created and os.path.lexists(stage):
            _safe_remove_owned_stage(stage, dates_root, ".stage-run-")
        raise
    try:
        if os.path.lexists(final):
            (
                existing_predictions,
                existing_exclusions,
                _,
                existing_manifest_digest,
                existing_dir,
            ) = _validate_prediction_artifact(
                predictions_csv=final / "predictions.csv",
                control_manifest=control,
                control_manifest_digest=control_digest,
                control_dir=frozen_control_dir,
            )
            if (
                existing_manifest_digest != manifest_digest
                or existing_predictions != tuple(predictions)
                or existing_exclusions != tuple(exclusions)
            ):
                raise MLBHRProspectiveTrialConflictError(
                    "existing prediction run conflicts with content-addressed replay"
                )
            _verify_ledger_linkage(
                ledger_path=ledger_path,
                predictions=existing_predictions,
                prediction_manifest_digest=existing_manifest_digest,
                predictions_csv_sha256=_sha256_bytes(predictions_bytes),
            )
            _safe_remove_owned_stage(stage, dates_root, ".stage-run-")
            stage_created = False
            return ProspectivePaperRunResult(
                status=(
                    "completed" if existing_predictions else "completed_no_predictions"
                ),
                control_id=str(control["control_id"]),
                prediction_run_id=run_id,
                operating_date=operating_date,
                prediction_count=len(existing_predictions),
                exclusion_count=len(existing_exclusions),
                prediction_manifest_digest=existing_manifest_digest,
                run_dir=existing_dir,
                ledger_rows_appended=0,
                replayed_existing_run=True,
                predictions=existing_predictions,
                exclusions=existing_exclusions,
            )
        published = False
        try:
            try:
                stage.rename(final)
            except OSError as exc:
                raise MLBHRProspectiveTrialError(
                    "atomic prediction run directory publication failed"
                ) from exc
            stage_created = False
            published = True
            (
                committed_predictions,
                committed_exclusions,
                _,
                committed_manifest_digest,
                committed_dir,
            ) = _validate_prediction_artifact(
                predictions_csv=final / "predictions.csv",
                control_manifest=control,
                control_manifest_digest=control_digest,
                control_dir=frozen_control_dir,
            )
            _call_failure_hook(failure_hook, "before_prediction_ledger_append")
            appended, _ = _append_prediction_ledger_rows(
                ledger_path=ledger_path,
                predictions=committed_predictions,
                prediction_manifest_digest=committed_manifest_digest,
                predictions_csv_sha256=_sha256_bytes(predictions_bytes),
            )
            _verify_ledger_linkage(
                ledger_path=ledger_path,
                predictions=committed_predictions,
                prediction_manifest_digest=committed_manifest_digest,
                predictions_csv_sha256=_sha256_bytes(predictions_bytes),
            )
        except Exception as original:
            if stage_created and os.path.lexists(stage):
                _safe_remove_owned_stage(stage, dates_root, ".stage-run-")
            if published and os.path.lexists(final):
                try:
                    _safe_remove_owned_stage(final, dates_root, "hrv1-")
                except MLBHRProspectiveTrialError:
                    diagnostic = dates_root / f".failed-{uuid4().hex[:12]}"
                    try:
                        final.rename(diagnostic)
                    except OSError:
                        pass
            if not dates_root_existed and dates_root.exists():
                try:
                    dates_root.rmdir()
                except OSError:
                    pass
            if not dates_parent_existed and dates_parent.exists():
                try:
                    dates_parent.rmdir()
                except OSError:
                    pass
            raise original
    finally:
        try:
            lock.release()
        finally:
            if stage_created and os.path.lexists(stage):
                _safe_remove_owned_stage(stage, dates_root, ".stage-run-")
    return ProspectivePaperRunResult(
        status="completed" if predictions else "completed_no_predictions",
        control_id=str(control["control_id"]),
        prediction_run_id=run_id,
        operating_date=operating_date,
        prediction_count=len(predictions),
        exclusion_count=len(exclusions),
        prediction_manifest_digest=manifest_digest,
        run_dir=committed_dir,
        ledger_rows_appended=appended,
        replayed_existing_run=False,
        predictions=committed_predictions,
        exclusions=committed_exclusions,
    )


def _read_closing_rows(path: Path) -> tuple[dict[str, str], ...]:
    if not path.exists():
        return ()
    if path.is_symlink() or not path.is_file():
        raise MLBHRProspectiveTrialError("closing-line evidence is not a regular file")
    rows = _parse_csv_bytes(
        _read_stable_bytes(path, "closing-line evidence"),
        CLOSING_COLUMNS,
        "closing-line evidence",
    )
    seen: set[str] = set()
    for index, row in enumerate(rows, start=2):
        _validate_research_csv_row(row, f"closing-line row {index}")
        if row["closing_schema_version"] != CLOSING_SCHEMA_VERSION:
            raise MLBHRProspectiveTrialError(
                "closing-line evidence contains a legacy schema"
            )
        if row["prediction_id"] in seen:
            raise MLBHRProspectiveTrialError(
                "duplicate closing-line evidence for one prediction"
            )
        seen.add(row["prediction_id"])
    return rows


def capture_prospective_closing(
    *,
    control_dir: str | Path,
    predictions_csv: str | Path,
    odds_csv: str | Path,
    trial_root: str | Path,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, object]:
    """Append verified same-book or consensus pre-start closing evidence."""

    trial = Path(trial_root).expanduser().resolve(strict=False)
    control, control_digest, frozen_control_dir = _read_control(
        control_dir,
        trial_root=trial,
    )
    (
        predictions,
        _,
        _,
        manifest_digest,
        _,
    ) = _validate_prediction_artifact(
        predictions_csv=predictions_csv,
        control_manifest=control,
        control_manifest_digest=control_digest,
        control_dir=frozen_control_dir,
    )
    predictions_sha = _file_sha256(predictions_csv, "predictions.csv")
    ledger_path = frozen_control_dir / "prospective_ledger.csv"
    _verify_ledger_linkage(
        ledger_path=ledger_path,
        predictions=predictions,
        prediction_manifest_digest=manifest_digest,
        predictions_csv_sha256=predictions_sha,
    )
    odds_path = Path(odds_csv).expanduser().resolve(strict=False)
    if not odds_path.is_file():
        raise MLBHRProspectiveTrialError("explicit closing odds CSV does not exist")
    odds_digest = _file_sha256(odds_path, "closing odds CSV")
    captured_at = _clock_value(clock, "closing capture timestamp")
    adapted = [
        {
            "prediction_id": row["prediction_id"],
            "prediction_run_id": row["prediction_run_id"],
            "event_id": row["event_id"],
            "commence_time": row["commence_time_utc"],
            "sportsbook": row["sportsbook"],
            "sportsbook_name": row["sportsbook_name"],
            "normalized_player_name": row["normalized_player_name"],
            "market_key": row["market_key"],
            "point": row["point"],
            "american_odds": row["prediction_time_price"],
            "implied_probability": row["implied_probability"],
        }
        for row in predictions
    ]
    try:
        selected = baseline.capture_closing_line_snapshots(
            odds_path=odds_path,
            predictions=adapted,
            output_csv=None,
            captured_at=captured_at,
        )
    except baseline.MLBHRResearchBaselineError as exc:
        raise MLBHRProspectiveTrialError(
            "existing MLB closing-line contract rejected the evidence"
        ) from exc
    if _file_sha256(odds_path, "closing odds CSV") != odds_digest:
        raise MLBHRProspectiveTrialError(
            "closing odds CSV changed while it was being read"
        )
    rows: list[dict[str, str]] = []
    for source in selected.rows:
        payload = {
            "closing_schema_version": CLOSING_SCHEMA_VERSION,
            "closing_record_id": (
                "mlb-hr-closing-v2-"
                + _canonical_sha256(
                    {
                        "prediction_id": source["prediction_id"],
                        "closing_status": source["closing_status"],
                        "closing_method": source["closing_method"],
                        "closing_snapshot_time": source[
                            "closing_snapshot_time"
                        ],
                        "closing_sportsbook": source["closing_sportsbook"],
                        "closing_american_odds": source[
                            "closing_american_odds"
                        ],
                    }
                )[:24]
            ),
            "prediction_id": source["prediction_id"],
            "prediction_run_id": source["prediction_run_id"],
            "control_id": str(control["control_id"]),
            "control_manifest_digest": control_digest,
            "closing_status": source["closing_status"],
            "closing_method": source["closing_method"],
            "closing_snapshot_time_utc": source["closing_snapshot_time"],
            "closing_sportsbook": source["closing_sportsbook"],
            "closing_sportsbook_name": source["closing_sportsbook_name"],
            "closing_american_odds": source["closing_american_odds"],
            "closing_decimal_odds": source["closing_decimal_odds"],
            "closing_implied_probability": source[
                "closing_implied_probability"
            ],
            "consensus_bookmaker_count": source[
                "consensus_bookmaker_count"
            ],
            "consensus_implied_probability": source[
                "consensus_implied_probability"
            ],
            "original_american_odds": source["original_american_odds"],
            "original_implied_probability": source[
                "original_implied_probability"
            ],
            "closing_line_movement": source["closing_line_movement"],
            "closing_probability_movement": source[
                "closing_probability_movement"
            ],
            "source_odds_sha256": odds_digest,
            "captured_at_utc": _utc_text(captured_at),
            "integrity_status": source["integrity_status"],
            **_research_csv_fields(),
        }
        rows.append(
            {column: str(payload.get(column, "")) for column in CLOSING_COLUMNS}
        )
    output_path = frozen_control_dir / "closing_lines.csv"
    appended = 0
    with _TrialStoreLock(
        trial,
        operation="capture_prospective_closing",
        control_id=str(control["control_id"]),
        clock=clock,
    ):
        _verify_ledger_linkage(
            ledger_path=ledger_path,
            predictions=predictions,
            prediction_manifest_digest=manifest_digest,
            predictions_csv_sha256=predictions_sha,
        )
        existing = _read_closing_rows(output_path)
        by_prediction = {row["prediction_id"]: row for row in existing}
        new_rows: list[dict[str, str]] = []
        for row in rows:
            prior = by_prediction.get(row["prediction_id"])
            if prior is None:
                new_rows.append(row)
                continue
            comparable = [name for name in CLOSING_COLUMNS if name != "captured_at_utc"]
            if any(prior[name] != row[name] for name in comparable):
                raise MLBHRProspectiveTrialConflictError(
                    "conflicting closing-line evidence for " + row["prediction_id"]
                )
        _transactional_append_csv(
            path=output_path,
            columns=CLOSING_COLUMNS,
            rows=new_rows,
            description="closing-line evidence",
        )
        appended = len(new_rows)
        _read_closing_rows(output_path)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["closing_status"]] = counts.get(row["closing_status"], 0) + 1
    return {
        "success": True,
        "control_id": control["control_id"],
        "predictions_examined": len(predictions),
        "closing_rows_appended": appended,
        "same_book_count": counts.get("captured_same_book", 0),
        "consensus_count": counts.get("captured_consensus", 0),
        "missing_count": counts.get("missing", 0)
        + counts.get("missing_prestart", 0),
        **RESEARCH_BOUNDARY,
    }


def _settlement_row(
    *,
    prediction: Mapping[str, str],
    result: Mapping[str, str],
    results_digest: str,
    settlement_timestamp: datetime,
) -> dict[str, str] | None:
    game_status = str(result.get("game_status", "")).strip().casefold()
    if game_status in {"", "missing", "pending", "unresolved"}:
        return None
    settlement_status = ""
    strict_result_status = game_status
    outcome = ""
    grade = ""
    unit_profit = ""
    integrity = "strict_result_join_verified"
    if game_status == "final":
        raw_actual = str(result.get("actual_home_runs", "")).strip()
        try:
            actual = int(raw_actual)
        except ValueError as exc:
            raise MLBHRProspectiveTrialError(
                "final result has invalid actual_home_runs"
            ) from exc
        if actual < 0:
            raise MLBHRProspectiveTrialError(
                "final result has negative actual_home_runs"
            )
        outcome = "1" if actual >= 1 else "0"
        grade = "win" if actual >= 1 else "loss"
        settlement_status = "settled"
        try:
            original_odds = int(prediction["original_american_odds"])
            original_decimal = float(prediction["original_decimal_odds"])
            original_implied = float(prediction["original_implied_probability"])
            if (
                original_odds == 0
                or not math.isfinite(original_decimal)
                or not math.isfinite(original_implied)
                or original_decimal <= 1
                or not math.isclose(
                    original_decimal,
                    baseline.american_to_decimal(original_odds),
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                )
                or not math.isclose(
                    original_implied,
                    baseline.american_to_implied_probability(original_odds),
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                )
            ):
                raise ValueError
        except ValueError:
            integrity = "settled_missing_complete_original_price"
        else:
            unit_profit = (
                _format_float(baseline.win_profit_1u(original_odds))
                if actual >= 1
                else "-1"
            )
    elif game_status == "void":
        settlement_status = "void"
        grade = "void"
    elif game_status in {"void_candidate", "manual_review_required"}:
        return None
    else:
        return None
    payload = {column: prediction.get(column, "") for column in LEDGER_COLUMNS}
    payload.update(
        {
            "ledger_record_id": (
                "mlb-hr-ledger-v2-"
                + _canonical_sha256(
                    {
                        "record_type": "settlement",
                        "prediction_id": prediction["prediction_id"],
                    }
                )[:24]
            ),
            "record_type": "settlement",
            "settlement_status": settlement_status,
            "strict_result_status": strict_result_status,
            "final_hr_outcome": outcome,
            "grade": grade,
            "unit_profit_loss": unit_profit,
            "results_sha256": results_digest,
            "settlement_timestamp_utc": _utc_text(settlement_timestamp),
            "integrity_status": integrity,
        }
    )
    return {column: str(payload.get(column, "")) for column in LEDGER_COLUMNS}


def _strict_committed_prediction_rows(
    *,
    control_manifest: Mapping[str, object],
    control_manifest_digest: str,
    control_dir: Path,
    ledger_path: Path,
) -> tuple[dict[str, str], ...]:
    ledger_rows = _read_ledger(ledger_path)
    ledger_predictions = tuple(
        row for row in ledger_rows if row["record_type"] == "prediction"
    )
    ledger_ids = {row["prediction_id"] for row in ledger_predictions}
    artifact_ids: set[str] = set()
    dates_root = control_dir / "dates"
    if dates_root.exists():
        if dates_root.is_symlink() or not dates_root.is_dir():
            raise MLBHRProspectiveTrialError(
                "prospective dates store is not a real directory"
            )
        try:
            date_entries = tuple(dates_root.iterdir())
        except OSError as exc:
            raise MLBHRProspectiveTrialError(
                "prospective dates store is inaccessible"
            ) from exc
        for date_dir in date_entries:
            if date_dir.name.startswith("."):
                continue
            if date_dir.is_symlink() or not date_dir.is_dir():
                raise MLBHRProspectiveTrialError(
                    "prospective dates store contains an invalid entry"
                )
            try:
                run_entries = tuple(date_dir.iterdir())
            except OSError as exc:
                raise MLBHRProspectiveTrialError(
                    "prospective operating-date store is inaccessible"
                ) from exc
            for run_dir in run_entries:
                if run_dir.name.startswith("."):
                    continue
                if run_dir.is_symlink() or not run_dir.is_dir():
                    raise MLBHRProspectiveTrialError(
                        "prospective operating-date store contains an invalid entry"
                    )
                predictions, _, _, manifest_digest, _ = (
                    _validate_prediction_artifact(
                        predictions_csv=run_dir / "predictions.csv",
                        control_manifest=control_manifest,
                        control_manifest_digest=control_manifest_digest,
                        control_dir=control_dir,
                    )
                )
                _verify_ledger_linkage(
                    ledger_path=ledger_path,
                    predictions=predictions,
                    prediction_manifest_digest=manifest_digest,
                    predictions_csv_sha256=_file_sha256(
                        run_dir / "predictions.csv", "predictions.csv"
                    ),
                )
                for prediction in predictions:
                    prediction_id = prediction["prediction_id"]
                    if prediction_id in artifact_ids:
                        raise MLBHRProspectiveTrialError(
                            "prediction_id appears in multiple immutable runs"
                        )
                    artifact_ids.add(prediction_id)
    if artifact_ids != ledger_ids:
        raise MLBHRProspectiveTrialError(
            "canonical ledger and immutable prediction artifacts are not exact"
        )
    return ledger_rows


def settle_prospective_paper_day(
    *,
    control_dir: str | Path,
    results_csv: str | Path,
    trial_root: str | Path,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, object]:
    """Append strict final-result settlements without mutating predictions."""

    trial = Path(trial_root).expanduser().resolve(strict=False)
    control, control_digest, frozen_control_dir = _read_control(
        control_dir,
        trial_root=trial,
    )
    ledger_path = frozen_control_dir / "prospective_ledger.csv"
    ledger_rows = _strict_committed_prediction_rows(
        control_manifest=control,
        control_manifest_digest=control_digest,
        control_dir=frozen_control_dir,
        ledger_path=ledger_path,
    )
    predictions = [row for row in ledger_rows if row["record_type"] == "prediction"]
    result_path = Path(results_csv).expanduser().resolve(strict=False)
    if not result_path.is_file():
        raise MLBHRProspectiveTrialError("explicit results CSV does not exist")
    results_digest = _file_sha256(result_path, "results CSV")
    try:
        result_index, duplicate_results, _, _ = baseline._load_result_index(  # type: ignore[attr-defined]
            result_path
        )
    except baseline.MLBHRResearchBaselineError as exc:
        raise MLBHRProspectiveTrialError(
            "existing strict MLB result contract rejected the results source"
        ) from exc
    if _file_sha256(result_path, "results CSV") != results_digest:
        raise MLBHRProspectiveTrialError("results CSV changed while it was being read")
    settlement_timestamp = _clock_value(clock, "settlement timestamp")
    candidate_by_id: dict[str, dict[str, str]] = {}
    pending = 0
    for prediction in predictions:
        key = (
            prediction["event_id"],
            normalize_mlb_player_name(prediction["player_name"]),
        )
        if key in duplicate_results:
            pending += 1
            continue
        result = result_index.get(key)
        if result is None:
            pending += 1
            continue
        candidate = _settlement_row(
            prediction=prediction,
            result=result,
            results_digest=results_digest,
            settlement_timestamp=settlement_timestamp,
        )
        if candidate is None:
            pending += 1
            continue
        candidate_by_id[prediction["prediction_id"]] = candidate
    appended = 0
    skipped = 0
    with _TrialStoreLock(
        trial,
        operation="settle_prospective_paper_day",
        control_id=str(control["control_id"]),
        clock=clock,
    ):
        current = _strict_committed_prediction_rows(
            control_manifest=control,
            control_manifest_digest=control_digest,
            control_dir=frozen_control_dir,
            ledger_path=ledger_path,
        )
        current_predictions = {
            row["prediction_id"]: row
            for row in current
            if row["record_type"] == "prediction"
        }
        existing_settlements = {
            row["prediction_id"]: row
            for row in current
            if row["record_type"] == "settlement"
        }
        new_rows: list[dict[str, str]] = []
        semantic_fields = (
            "settlement_status",
            "strict_result_status",
            "final_hr_outcome",
            "grade",
            "unit_profit_loss",
            "integrity_status",
        )
        for prediction_id, candidate in candidate_by_id.items():
            if prediction_id not in current_predictions:
                raise MLBHRProspectiveTrialError(
                    "settlement candidate lacks a committed prediction"
                )
            prior = existing_settlements.get(prediction_id)
            if prior is None:
                new_rows.append(candidate)
                continue
            if any(prior[name] != candidate[name] for name in semantic_fields):
                raise MLBHRProspectiveTrialConflictError(
                    "conflicting final settlement for " + prediction_id
                )
            skipped += 1
        _transactional_append_csv(
            path=ledger_path,
            columns=LEDGER_COLUMNS,
            rows=new_rows,
            description="prospective ledger",
        )
        appended = len(new_rows)
        _read_ledger(ledger_path)
    return {
        "success": True,
        "control_id": control["control_id"],
        "settlements_appended": appended,
        "pending_predictions": pending,
        "skipped_existing_settlements": skipped,
        "conflicting_settlements": 0,
        **RESEARCH_BOUNDARY,
    }


def _calibration_error(rows: Sequence[Mapping[str, str]]) -> float | None:
    if not rows:
        return None
    buckets: dict[int, list[tuple[float, int]]] = {}
    for row in rows:
        try:
            probability = float(row["model_probability"])
            outcome = int(row["final_hr_outcome"])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(probability) or not 0 <= probability <= 1 or outcome not in {0, 1}:
            return None
        bucket = min(int(probability * 10), 9)
        buckets.setdefault(bucket, []).append((probability, outcome))
    total = len(rows)
    return sum(
        (len(values) / total)
        * abs(
            sum(item[0] for item in values) / len(values)
            - sum(item[1] for item in values) / len(values)
        )
        for values in buckets.values()
    )


def _minimum_gate(current: int | float | None, required: int | float) -> dict[str, object]:
    if current is None:
        return {
            "required_value": required,
            "current_value": None,
            "remaining_gap": None,
            "status": "not_measurable",
        }
    gap = max(float(required) - float(current), 0.0)
    if isinstance(required, int) and isinstance(current, int):
        gap = int(gap)
    return {
        "required_value": required,
        "current_value": current,
        "remaining_gap": gap,
        "status": "pass" if current >= required else "fail",
    }


def _maximum_gate(current: float | None, maximum: float) -> dict[str, object]:
    if current is None:
        return {
            "required_value": {"maximum": maximum},
            "current_value": None,
            "remaining_gap": None,
            "status": "not_measurable",
        }
    return {
        "required_value": {"maximum": maximum},
        "current_value": current,
        "remaining_gap": max(current - maximum, 0.0),
        "status": "pass" if current <= maximum else "fail",
    }


def report_prospective_status(
    *,
    control_dir: str | Path,
    trial_root: str | Path,
) -> dict[str, object]:
    """Read and verify prospective-only evidence without creating any file."""

    trial = Path(trial_root).expanduser().resolve(strict=False)
    control, control_digest, frozen_control_dir = _read_control(
        control_dir,
        trial_root=trial,
    )
    ledger_path = frozen_control_dir / "prospective_ledger.csv"
    closing_path = frozen_control_dir / "closing_lines.csv"
    ledger_before = _file_sha256(ledger_path, "prospective ledger") if ledger_path.exists() else ""
    closing_before = (
        _file_sha256(closing_path, "closing-line evidence")
        if closing_path.exists()
        else ""
    )
    ledger_rows = _read_ledger(ledger_path)
    closing_rows = _read_closing_rows(closing_path)
    predictions = [row for row in ledger_rows if row["record_type"] == "prediction"]
    settlements = [row for row in ledger_rows if row["record_type"] == "settlement"]
    settlements_by_id = {row["prediction_id"]: row for row in settlements}
    valid_settlements = [
        row
        for row in settlements
        if row["settlement_status"] == "settled"
        and row["final_hr_outcome"] in {"0", "1"}
    ]
    artifact_findings: list[dict[str, str]] = []
    artifact_prediction_ids: set[str] = set()
    dates_root = frozen_control_dir / "dates"
    if dates_root.exists():
        try:
            run_dirs = sorted(
                path
                for date_dir in dates_root.iterdir()
                if date_dir.is_dir() and not date_dir.name.startswith(".")
                for path in date_dir.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            )
        except OSError as exc:
            raise MLBHRProspectiveTrialError(
                "prospective date/run store is inaccessible"
            ) from exc
        for run_dir in run_dirs:
            try:
                run_predictions, _, _, manifest_digest, _ = (
                    _validate_prediction_artifact(
                        predictions_csv=run_dir / "predictions.csv",
                        control_manifest=control,
                        control_manifest_digest=control_digest,
                        control_dir=frozen_control_dir,
                    )
                )
                artifact_prediction_ids.update(
                    row["prediction_id"] for row in run_predictions
                )
                _verify_ledger_linkage(
                    ledger_path=ledger_path,
                    predictions=run_predictions,
                    prediction_manifest_digest=manifest_digest,
                    predictions_csv_sha256=_file_sha256(
                        run_dir / "predictions.csv", "predictions.csv"
                    ),
                )
            except MLBHRProspectiveTrialError as exc:
                artifact_findings.append(
                    {
                        "run": run_dir.relative_to(frozen_control_dir).as_posix(),
                        "finding": str(exc),
                    }
                )
    ledger_prediction_ids = {row["prediction_id"] for row in predictions}
    for prediction_id in sorted(ledger_prediction_ids - artifact_prediction_ids):
        artifact_findings.append(
            {
                "run": "canonical_ledger",
                "finding": (
                    "committed prediction lacks an admissible prospective artifact: "
                    + prediction_id
                ),
            }
        )
    closing_by_id = {row["prediction_id"]: row for row in closing_rows}
    for row in closing_rows:
        if row["prediction_id"] not in ledger_prediction_ids:
            artifact_findings.append(
                {
                    "run": "closing_lines.csv",
                    "finding": "closing row lacks committed prediction linkage",
                }
            )
        if row["control_id"] != control["control_id"]:
            artifact_findings.append(
                {
                    "run": "closing_lines.csv",
                    "finding": "closing row control identity mismatch",
                }
            )
    for row in predictions:
        if row["control_id"] != control["control_id"]:
            artifact_findings.append(
                {
                    "run": "prospective_ledger.csv",
                    "finding": "prediction row control identity mismatch",
                }
            )
    pending = [
        row
        for row in predictions
        if row["prediction_id"] not in settlements_by_id
    ]
    void_count = sum(row["settlement_status"] == "void" for row in settlements)
    manual_review_count = sum(
        row["strict_result_status"]
        in {"void_candidate", "manual_review_required", "unresolved"}
        for row in settlements
    )
    identity_counts: dict[str, int] = {}
    for row in predictions:
        identity_counts[row["identity_status"]] = (
            identity_counts.get(row["identity_status"], 0) + 1
        )
    captured_closing = sum(
        row["closing_status"] in {"captured_same_book", "captured_consensus"}
        for row in closing_rows
        if row["prediction_id"] in ledger_prediction_ids
    )
    closing_coverage = (
        captured_closing / len(predictions) if predictions else None
    )
    identity_rate = (
        identity_counts.get("resolved_reviewed", 0) / len(predictions)
        if predictions
        else None
    )
    required_prediction_fields = (
        "control_manifest_digest",
        "model_bundle_manifest_digest",
        "prediction_git_commit",
        "prediction_tree_fingerprint",
        "event_id",
        "commence_time_utc",
        "normalized_player_name",
        "sportsbook",
        "original_american_odds",
        "model_probability",
        "prediction_timestamp_utc",
        "selected_snapshot_timestamp_utc",
        "source_odds_sha256",
    )
    missing_cells = sum(
        not row[field_name]
        for row in predictions
        for field_name in required_prediction_fields
    )
    total_cells = len(predictions) * len(required_prediction_fields)
    missing_rate = missing_cells / total_cells if total_cells else None
    calibration_error = _calibration_error(valid_settlements)
    pnl_values = [
        float(row["unit_profit_loss"])
        for row in valid_settlements
        if row["unit_profit_loss"]
    ]
    metrics: dict[str, object]
    if not valid_settlements:
        metrics = {
            "status": "not_measurable",
            "reason": "no valid settled prospective predictions",
        }
    else:
        outcomes = [int(row["final_hr_outcome"]) for row in valid_settlements]
        probabilities = [float(row["model_probability"]) for row in valid_settlements]
        metrics = {
            "status": "measured",
            "settled_count": len(valid_settlements),
            "hit_rate": sum(outcomes) / len(outcomes),
            "log_loss": -sum(
                outcome * math.log(min(max(probability, 1e-15), 1 - 1e-15))
                + (1 - outcome)
                * math.log1p(-min(max(probability, 1e-15), 1 - 1e-15))
                for outcome, probability in zip(outcomes, probabilities, strict=True)
            )
            / len(outcomes),
            "brier_score": sum(
                (probability - outcome) ** 2
                for outcome, probability in zip(outcomes, probabilities, strict=True)
            )
            / len(outcomes),
            "calibration_error": calibration_error,
            "flat_one_unit_profit_loss": sum(pnl_values),
        }
    volume = {
        "prediction_dates": len({row["operating_date"] for row in predictions}),
        "completed_games": len(
            {row["event_id"] for row in valid_settlements}
        ),
        "eligible_predictions": len(predictions),
        "unique_players": len(
            {row["normalized_player_name"] for row in predictions}
        ),
        "positive_hr_outcomes": sum(
            row["final_hr_outcome"] == "1" for row in valid_settlements
        ),
    }
    gates = {
        "prospective_prediction_dates": _minimum_gate(
            volume["prediction_dates"], 30
        ),
        "completed_games": _minimum_gate(volume["completed_games"], 100),
        "eligible_predictions": _minimum_gate(
            volume["eligible_predictions"], 1000
        ),
        "unique_players": _minimum_gate(volume["unique_players"], 100),
        "positive_hr_outcomes": _minimum_gate(
            volume["positive_hr_outcomes"], 50
        ),
        "missing_data_rate": _maximum_gate(missing_rate, 0.20),
        "identity_match_rate": _minimum_gate(identity_rate, 0.95),
        "calibration_error": _maximum_gate(calibration_error, 0.075),
        "closing_line_coverage": _minimum_gate(closing_coverage, 0.80),
        "unresolved_leakage_findings": {
            "required_value": 0,
            "current_value": 0,
            "remaining_gap": 0,
            "status": "pass",
        },
        "artifact_mutation_findings": {
            "required_value": 0,
            "current_value": len(artifact_findings),
            "remaining_gap": len(artifact_findings),
            "status": "pass" if not artifact_findings else "fail",
        },
    }
    if ledger_path.exists() and _file_sha256(
        ledger_path, "prospective ledger"
    ) != ledger_before:
        raise MLBHRProspectiveTrialError(
            "prospective ledger changed during read-only reporting"
        )
    if closing_path.exists() and _file_sha256(
        closing_path, "closing-line evidence"
    ) != closing_before:
        raise MLBHRProspectiveTrialError(
            "closing-line evidence changed during read-only reporting"
        )
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "control": {
            "control_id": control["control_id"],
            "control_manifest_digest": control_digest,
            "validation_status": "valid",
            "model_id": control["identity_material"]["model_id"],  # type: ignore[index]
            "model_version": control["identity_material"]["model_version"],  # type: ignore[index]
        },
        "counts": {
            "prospective_operating_dates": volume["prediction_dates"],
            "committed_predictions": len(predictions),
            "settled_predictions": len(valid_settlements),
            "pending_predictions": len(pending),
            "void_predictions": void_count,
            "manual_review_predictions": manual_review_count,
            "conflict_count": 0,
            "unique_games": len({row["event_id"] for row in predictions}),
            "unique_players": volume["unique_players"],
            "positive_hr_outcomes": volume["positive_hr_outcomes"],
        },
        "identity_status_coverage": dict(sorted(identity_counts.items())),
        "closing_line_coverage": {
            "captured": captured_closing,
            "prediction_count": len(predictions),
            "coverage_rate": closing_coverage,
            "missing": max(len(predictions) - len(closing_by_id), 0),
        },
        "metrics": metrics,
        "gate_progress": gates,
        "artifact_integrity": {
            "status": "valid" if not artifact_findings else "findings",
            "finding_count": len(artifact_findings),
            "findings": artifact_findings,
        },
        "evidence_separation": {
            "prospective_trial_predictions": len(predictions),
            "historical_training_rows_imported": 0,
            "rehearsal_rows_imported": 0,
            "lifecycle_diagnostic_rows_imported": 0,
            "grade_derivative_rows_imported": 0,
        },
        "automatic_promotion_enabled": False,
        **RESEARCH_BOUNDARY,
    }


PROSPECTIVE_COMMANDS: Final = frozenset(
    {
        "activate-prospective-control",
        "run-prospective-paper-day",
        "capture-prospective-closing",
        "settle-prospective-paper-day",
        "report-prospective-status",
    }
)


def configure_prospective_cli(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    activate = subparsers.add_parser("activate-prospective-control")
    activate.add_argument("--model-dir", type=Path, required=True)
    activate.add_argument("--trial-root", type=Path, required=True)
    activate.add_argument("--repository-root", type=Path, required=True)

    predict = subparsers.add_parser("run-prospective-paper-day")
    predict.add_argument("--date", dest="target_date", required=True)
    predict.add_argument("--control-dir", type=Path, required=True)
    predict.add_argument("--odds-csv", type=Path, required=True)
    predict.add_argument("--trial-root", type=Path, required=True)
    predict.add_argument("--repository-root", type=Path, required=True)
    predict.add_argument("--identity-cache-csv", type=Path)
    predict.add_argument("--dry-run", action="store_true")

    closing = subparsers.add_parser("capture-prospective-closing")
    closing.add_argument("--control-dir", type=Path, required=True)
    closing.add_argument("--predictions-csv", type=Path, required=True)
    closing.add_argument("--odds-csv", type=Path, required=True)
    closing.add_argument("--trial-root", type=Path, required=True)

    settle = subparsers.add_parser("settle-prospective-paper-day")
    settle.add_argument("--control-dir", type=Path, required=True)
    settle.add_argument("--results-csv", type=Path, required=True)
    settle.add_argument("--trial-root", type=Path, required=True)

    report = subparsers.add_parser("report-prospective-status")
    report.add_argument("--control-dir", type=Path, required=True)
    report.add_argument("--trial-root", type=Path, required=True)


def _relative_control_path(control_id: str) -> str:
    return f"controls/{control_id}"


def execute_prospective_cli(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "activate-prospective-control":
        result = activate_prospective_control(
            model_dir=args.model_dir,
            trial_root=args.trial_root,
            repository_root=args.repository_root,
        )
        return {
            "success": True,
            "control_id": result.control_id,
            "control_manifest_digest": result.control_manifest_digest,
            "model_id": result.model_id,
            "model_version": result.model_version,
            "model_bundle_manifest_digest": (
                result.model_bundle_manifest_digest
            ),
            "control_path": _relative_control_path(result.control_id),
            "replayed_existing_control": result.replayed_existing_control,
            **RESEARCH_BOUNDARY,
        }
    if args.command == "run-prospective-paper-day":
        result = run_prospective_paper_day(
            target_date=args.target_date,
            control_dir=args.control_dir,
            odds_csv=args.odds_csv,
            trial_root=args.trial_root,
            repository_root=args.repository_root,
            identity_cache_csv=args.identity_cache_csv,
            dry_run=args.dry_run,
        )
        run_path = (
            f"{_relative_control_path(result.control_id)}/dates/"
            f"{result.operating_date}/{result.prediction_run_id}"
            if result.run_dir is not None
            else ""
        )
        return {
            "success": True,
            "status": result.status,
            "control_id": result.control_id,
            "prediction_run_id": result.prediction_run_id,
            "operating_date": result.operating_date,
            "prediction_count": result.prediction_count,
            "exclusion_count": result.exclusion_count,
            "prediction_manifest_digest": result.prediction_manifest_digest,
            "run_path": run_path,
            "ledger_rows_appended": result.ledger_rows_appended,
            "replayed_existing_run": result.replayed_existing_run,
            **RESEARCH_BOUNDARY,
        }
    if args.command == "capture-prospective-closing":
        return capture_prospective_closing(
            control_dir=args.control_dir,
            predictions_csv=args.predictions_csv,
            odds_csv=args.odds_csv,
            trial_root=args.trial_root,
        )
    if args.command == "settle-prospective-paper-day":
        return settle_prospective_paper_day(
            control_dir=args.control_dir,
            results_csv=args.results_csv,
            trial_root=args.trial_root,
        )
    if args.command == "report-prospective-status":
        return report_prospective_status(
            control_dir=args.control_dir,
            trial_root=args.trial_root,
        )
    raise MLBHRProspectiveTrialError("unsupported prospective command")


__all__ = [
    "CLOSING_COLUMNS",
    "CLOSING_SCHEMA_VERSION",
    "CONTROL_SCHEMA_VERSION",
    "ControlActivationResult",
    "EXCLUDED_COLUMNS",
    "FROZEN_POLICIES",
    "LEDGER_COLUMNS",
    "LEDGER_SCHEMA_VERSION",
    "MLBHRProspectiveTrialBusyError",
    "MLBHRProspectiveTrialConflictError",
    "MLBHRProspectiveTrialError",
    "MLBHRProspectiveTrialLockError",
    "PREDICTION_COLUMNS",
    "PREDICTION_MANIFEST_SCHEMA_VERSION",
    "PREDICTION_SCHEMA_VERSION",
    "PROSPECTIVE_COMMANDS",
    "ProspectivePaperRunResult",
    "activate_prospective_control",
    "capture_prospective_closing",
    "configure_prospective_cli",
    "execute_prospective_cli",
    "report_prospective_status",
    "run_prospective_paper_day",
    "settle_prospective_paper_day",
    "validate_complete_model_bundle",
]
