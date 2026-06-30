"""Immutable temporal-split artifacts for MLB HR historical research.

The writer binds one firewall-valid feature pack to its sealed label-custody
artifact, derives a whole-date 60/20/20 split, and publishes one JSON artifact
to an isolated staging directory.  It never opens labels or enables execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Final, Mapping
from uuid import uuid4

from courtvision.sports.mlb.data.historical_backtest_readiness import (
    HistoricalBacktestReadinessVerdict,
)
from courtvision.sports.mlb.data.historical_feature_pack import (
    HistoricalFeaturePackBuildError,
    feature_pack_from_payload,
)
from courtvision.sports.mlb.data.historical_temporal_backtest import (
    MIN_TEST_UNIQUE_DATES,
    MIN_TRAIN_UNIQUE_DATES,
    MIN_VALIDATION_UNIQUE_DATES,
    SPLIT_DATE_DENOMINATOR,
    TRAIN_DATE_NUMERATOR,
    VALIDATION_DATE_NUMERATOR,
    TemporalBacktestPlanningError,
    TemporalSplitPlan,
    plan_temporal_date_splits,
)
from courtvision.sports.mlb.training.hr_label_custody import (
    MLBHRLabelCustodyError,
    validate_mlb_hr_label_custody,
)
from courtvision.sports.mlb.training.hr_preprocessing_plan import (
    TEMPORAL_SPLIT_ARTIFACT_VERSION,
)


TEMPORAL_SPLIT_ARTIFACT_FILENAME: Final = "mlb_hr_temporal_split_plan.json"
TEMPORAL_SPLIT_ARTIFACT_TYPE: Final = "mlb_hr_temporal_split_plan"
TEMPORAL_SPLIT_WRITER_CODE_VERSION: Final = "mlb-hr-temporal-split-writer-v1"
SPLIT_METHOD: Final = "whole_unique_game_dates_60_20_20"

_GATE_NAMES: Final = (
    "model_training_enabled",
    "backtesting_enabled",
    "predictions_enabled",
    "eligible_for_betting",
    "ev_enabled",
    "kelly_eligible",
    "elite_enabled",
    "staking_enabled",
)
_FORBIDDEN_PATH_COMPONENTS: Final = frozenset(
    {
        "cache",
        "caches",
        "history",
        "manual",
        "manualdata",
        "operational",
        "operations",
        "output",
        "outputs",
        "pytestcache",
        "pycache",
        "runtime",
        "testoutputs",
    }
)
_SPLIT_RULES: Final[Mapping[str, object]] = {
    "assignment_unit": "whole_unique_game_date",
    "date_order": "strict_train_before_validation_before_test",
    "date_overlap_allowed": False,
    "row_assignment": "every_feature_row_exactly_once_by_game_date",
    "source": "validated_feature_pack_rows",
    "train_date_numerator": TRAIN_DATE_NUMERATOR,
    "validation_date_numerator": VALIDATION_DATE_NUMERATOR,
    "split_date_denominator": SPLIT_DATE_DENOMINATOR,
}
_SPLIT_THRESHOLDS: Final[Mapping[str, object]] = {
    "minimum_total_unique_dates": (
        MIN_TRAIN_UNIQUE_DATES
        + MIN_VALIDATION_UNIQUE_DATES
        + MIN_TEST_UNIQUE_DATES
    ),
    "minimum_train_unique_dates": MIN_TRAIN_UNIQUE_DATES,
    "minimum_validation_unique_dates": MIN_VALIDATION_UNIQUE_DATES,
    "minimum_test_unique_dates": MIN_TEST_UNIQUE_DATES,
    "required_readiness_verdict": (
        HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
    ),
}


class MLBHRTemporalSplitArtifactError(ValueError):
    """Raised when an immutable temporal-split artifact cannot be produced."""


@dataclass(frozen=True, slots=True)
class MLBHRTemporalSplitArtifact:
    """Validated binding for one persisted temporal split."""

    path: Path
    feature_pack_sha256: str
    label_custody_sha256: str
    row_identity_sha256: str
    artifact_sha256: str
    plan: TemporalSplitPlan

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", self.path.resolve())


@dataclass(frozen=True, slots=True)
class MLBHRTemporalSplitWriteResult:
    """Result of one create-once temporal-split publication."""

    output_dir: Path
    artifact_path: Path
    artifact: MLBHRTemporalSplitArtifact

    def __post_init__(self) -> None:
        object.__setattr__(self, "output_dir", self.output_dir.resolve())
        object.__setattr__(self, "artifact_path", self.artifact_path.resolve())


def _read_json_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MLBHRTemporalSplitArtifactError(
            f"could not read {label} {path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise MLBHRTemporalSplitArtifactError(f"{label} must contain a JSON object")
    return payload


def _file_sha256(path: Path, label: str) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise MLBHRTemporalSplitArtifactError(
            f"could not hash {label} {path}: {exc}"
        ) from exc
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _artifact_sha256(payload: Mapping[str, object]) -> str:
    return _canonical_sha256(
        {name: value for name, value in payload.items() if name != "artifact_sha256"}
    )


def _validate_output_directory(path: str | Path) -> tuple[Path, bool]:
    output_dir = Path(path).expanduser().resolve()
    components = {
        re.sub(r"[^a-z0-9]+", "", part.casefold()) for part in output_dir.parts
    }
    forbidden = sorted(components & _FORBIDDEN_PATH_COMPONENTS)
    if forbidden:
        raise MLBHRTemporalSplitArtifactError(
            "temporal split staging output cannot be inside operational, manual, "
            "cache, output, history, or runtime folders "
            f"({', '.join(forbidden)}): {output_dir}"
        )
    if output_dir.exists():
        if not output_dir.is_dir():
            raise MLBHRTemporalSplitArtifactError(
                f"output staging path is not a directory: {output_dir}"
            )
        if any(output_dir.iterdir()):
            raise MLBHRTemporalSplitArtifactError(
                f"output staging directory must be empty: {output_dir}"
            )
        return output_dir, False
    if not output_dir.parent.is_dir():
        raise MLBHRTemporalSplitArtifactError(
            f"output staging parent directory does not exist: {output_dir.parent}"
        )
    return output_dir, True


def _derive_plan(
    feature_payload: Mapping[str, object], *, feature_pack_path: Path
) -> TemporalSplitPlan:
    try:
        feature_pack = feature_pack_from_payload(feature_payload)
        plan = plan_temporal_date_splits(
            (row.game_date for row in feature_pack.rows),
            pack_dir=feature_pack_path.parent,
        )
    except (HistoricalFeaturePackBuildError, TemporalBacktestPlanningError) as exc:
        raise MLBHRTemporalSplitArtifactError(str(exc)) from exc

    assigned_dates = {
        game_date
        for window in (plan.train, plan.validation, plan.test)
        for game_date in window.game_dates
    }
    feature_dates = {row.game_date for row in feature_pack.rows}
    if assigned_dates != feature_dates:
        raise MLBHRTemporalSplitArtifactError(
            "strict temporal split does not cover every feature-pack game date"
        )
    return plan


def _window_payload(plan: TemporalSplitPlan, name: str) -> dict[str, object]:
    window = getattr(plan, name)
    return {
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "unique_date_count": window.unique_date_count,
        "game_dates": [value.isoformat() for value in window.game_dates],
    }


def _build_payload(
    *,
    plan: TemporalSplitPlan,
    feature_pack_sha256: str,
    label_custody_sha256: str,
    row_identity_sha256: str,
    created_at: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": TEMPORAL_SPLIT_ARTIFACT_VERSION,
        "artifact_type": TEMPORAL_SPLIT_ARTIFACT_TYPE,
        "mode": "historical_research",
        "created_at": created_at,
        "feature_pack_sha256": feature_pack_sha256,
        "label_custody_sha256": label_custody_sha256,
        "feature_pack_row_identity_sha256": row_identity_sha256,
        "code_version": TEMPORAL_SPLIT_WRITER_CODE_VERSION,
        "code_version_sha256": _canonical_sha256(
            TEMPORAL_SPLIT_WRITER_CODE_VERSION
        ),
        "split_rules": dict(_SPLIT_RULES),
        "split_rules_sha256": _canonical_sha256(_SPLIT_RULES),
        "thresholds": dict(_SPLIT_THRESHOLDS),
        "thresholds_sha256": _canonical_sha256(_SPLIT_THRESHOLDS),
        "pack_dir": str(plan.pack_dir),
        "split_method": SPLIT_METHOD,
        "readiness_verdict": plan.readiness_verdict,
        "feature_firewall_valid": True,
        "label_custody_valid": True,
        "labels_opened": False,
        "strict_chronology_valid": True,
        "approval_status": "not_approved",
        **{name: False for name in _GATE_NAMES},
        "train": _window_payload(plan, "train"),
        "validation": _window_payload(plan, "validation"),
        "test": _window_payload(plan, "test"),
    }
    payload["artifact_sha256"] = _artifact_sha256(payload)
    return payload


def _publish_create_once(temporary_path: Path, output_path: Path) -> None:
    """Atomically create ``output_path`` without an overwrite-capable operation."""

    try:
        os.link(temporary_path, output_path)
    except FileExistsError as exc:
        raise MLBHRTemporalSplitArtifactError(
            f"refusing to overwrite temporal split artifact: {output_path}"
        ) from exc
    except OSError as exc:
        raise MLBHRTemporalSplitArtifactError(
            f"could not publish temporal split artifact {output_path}: {exc}"
        ) from exc


def load_mlb_hr_temporal_split_artifact(
    artifact_path: str | Path,
    *,
    feature_pack_path: str | Path,
    label_custody_path: str | Path,
) -> MLBHRTemporalSplitArtifact:
    """Load and revalidate a supported split against its exact sealed inputs."""

    source = Path(artifact_path).expanduser().resolve()
    feature_source = Path(feature_pack_path).expanduser().resolve()
    custody_source = Path(label_custody_path).expanduser().resolve()
    payload = _read_json_object(source, "temporal split artifact")
    feature_payload = _read_json_object(feature_source, "feature-pack artifact")
    try:
        binding = validate_mlb_hr_label_custody(
            feature_pack_path=feature_source,
            label_custody_path=custody_source,
        )
    except MLBHRLabelCustodyError as exc:
        raise MLBHRTemporalSplitArtifactError(str(exc)) from exc
    plan = _derive_plan(feature_payload, feature_pack_path=feature_source)

    expected_values: Mapping[str, object] = {
        "schema_version": TEMPORAL_SPLIT_ARTIFACT_VERSION,
        "artifact_type": TEMPORAL_SPLIT_ARTIFACT_TYPE,
        "mode": "historical_research",
        "feature_pack_sha256": binding.feature_pack_sha256,
        "label_custody_sha256": binding.label_custody_sha256,
        "feature_pack_row_identity_sha256": binding.row_identity_sha256,
        "code_version": TEMPORAL_SPLIT_WRITER_CODE_VERSION,
        "code_version_sha256": _canonical_sha256(
            TEMPORAL_SPLIT_WRITER_CODE_VERSION
        ),
        "split_rules": dict(_SPLIT_RULES),
        "split_rules_sha256": _canonical_sha256(_SPLIT_RULES),
        "thresholds": dict(_SPLIT_THRESHOLDS),
        "thresholds_sha256": _canonical_sha256(_SPLIT_THRESHOLDS),
        "pack_dir": str(plan.pack_dir),
        "split_method": SPLIT_METHOD,
        "readiness_verdict": plan.readiness_verdict,
        "feature_firewall_valid": True,
        "label_custody_valid": True,
        "labels_opened": False,
        "strict_chronology_valid": True,
        "approval_status": "not_approved",
        **{name: False for name in _GATE_NAMES},
        "train": _window_payload(plan, "train"),
        "validation": _window_payload(plan, "validation"),
        "test": _window_payload(plan, "test"),
    }
    expected_fields = set(expected_values) | {"created_at", "artifact_sha256"}
    missing = sorted(expected_fields - payload.keys())
    unknown = sorted(payload.keys() - expected_fields)
    if missing or unknown:
        details = []
        if missing:
            details.append("missing=" + ", ".join(missing))
        if unknown:
            details.append("unsupported=" + ", ".join(unknown))
        raise MLBHRTemporalSplitArtifactError(
            "temporal split artifact schema mismatch: " + "; ".join(details)
        )
    created_at = payload.get("created_at")
    if not isinstance(created_at, str):
        raise MLBHRTemporalSplitArtifactError(
            "temporal split artifact created_at must be timezone-aware ISO-8601 text"
        )
    try:
        parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MLBHRTemporalSplitArtifactError(
            "temporal split artifact created_at must be timezone-aware ISO-8601 text"
        ) from exc
    if parsed_created_at.tzinfo is None or parsed_created_at.utcoffset() is None:
        raise MLBHRTemporalSplitArtifactError(
            "temporal split artifact created_at must be timezone-aware ISO-8601 text"
        )
    mismatches = [
        name
        for name, expected in expected_values.items()
        if payload.get(name) != expected
    ]
    if mismatches:
        raise MLBHRTemporalSplitArtifactError(
            "temporal split artifact binding/hash mismatch: "
            + ", ".join(mismatches)
        )
    artifact_hash = payload.get("artifact_sha256")
    if not isinstance(artifact_hash, str) or re.fullmatch(
        r"[0-9a-f]{64}", artifact_hash
    ) is None:
        raise MLBHRTemporalSplitArtifactError(
            "temporal split artifact artifact_sha256 must be lowercase SHA-256"
        )
    if artifact_hash != _artifact_sha256(payload):
        raise MLBHRTemporalSplitArtifactError(
            "temporal split artifact content hash mismatch"
        )
    if (
        _file_sha256(feature_source, "feature pack") != binding.feature_pack_sha256
        or _file_sha256(custody_source, "label-custody artifact")
        != binding.label_custody_sha256
    ):
        raise MLBHRTemporalSplitArtifactError(
            "feature pack or label-custody artifact changed during split validation"
        )
    return MLBHRTemporalSplitArtifact(
        path=source,
        feature_pack_sha256=binding.feature_pack_sha256,
        label_custody_sha256=binding.label_custody_sha256,
        row_identity_sha256=binding.row_identity_sha256,
        artifact_sha256=str(artifact_hash),
        plan=plan,
    )


def write_mlb_hr_temporal_split_artifact(
    *,
    feature_pack_path: str | Path,
    label_custody_path: str | Path,
    output_staging_dir: str | Path,
) -> MLBHRTemporalSplitWriteResult:
    """Derive and create one sealed temporal-split artifact in staging."""

    output_dir, create_output_dir = _validate_output_directory(output_staging_dir)
    feature_source = Path(feature_pack_path).expanduser().resolve()
    custody_source = Path(label_custody_path).expanduser().resolve()
    if feature_source.parent == output_dir or custody_source.parent == output_dir:
        raise MLBHRTemporalSplitArtifactError(
            "temporal split output directory must be isolated from input artifacts"
        )

    feature_payload = _read_json_object(feature_source, "feature-pack artifact")
    try:
        binding = validate_mlb_hr_label_custody(
            feature_pack_path=feature_source,
            label_custody_path=custody_source,
        )
    except MLBHRLabelCustodyError as exc:
        raise MLBHRTemporalSplitArtifactError(str(exc)) from exc
    plan = _derive_plan(feature_payload, feature_pack_path=feature_source)
    payload = _build_payload(
        plan=plan,
        feature_pack_sha256=binding.feature_pack_sha256,
        label_custody_sha256=binding.label_custody_sha256,
        row_identity_sha256=binding.row_identity_sha256,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    output_path = output_dir / TEMPORAL_SPLIT_ARTIFACT_FILENAME
    temporary_path: Path | None = None
    created_output_dir = False
    published = False
    try:
        if create_output_dir:
            output_dir.mkdir()
            created_output_dir = True
        temporary_path = output_dir / f".courtvision-temporal-split-{uuid4().hex}.json"
        temporary_path.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        _publish_create_once(temporary_path, output_path)
        published = True
        temporary_path.unlink()
        temporary_path = None
        artifact = load_mlb_hr_temporal_split_artifact(
            output_path,
            feature_pack_path=feature_source,
            label_custody_path=custody_source,
        )
        return MLBHRTemporalSplitWriteResult(
            output_dir=output_dir,
            artifact_path=output_path,
            artifact=artifact,
        )
    except Exception:
        if published and output_path.exists():
            output_path.unlink()
        raise
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
        if created_output_dir and output_dir.exists() and not any(output_dir.iterdir()):
            output_dir.rmdir()


__all__ = [
    "SPLIT_METHOD",
    "TEMPORAL_SPLIT_ARTIFACT_FILENAME",
    "TEMPORAL_SPLIT_ARTIFACT_TYPE",
    "TEMPORAL_SPLIT_WRITER_CODE_VERSION",
    "MLBHRTemporalSplitArtifact",
    "MLBHRTemporalSplitArtifactError",
    "MLBHRTemporalSplitWriteResult",
    "load_mlb_hr_temporal_split_artifact",
    "write_mlb_hr_temporal_split_artifact",
]
