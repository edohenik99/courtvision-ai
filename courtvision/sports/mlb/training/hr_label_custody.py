"""Physical label custody for MLB HR research feature packs.

The model-visible feature pack and the outcome labels are separate immutable
artifacts.  This module validates their identity binding without returning
label values, then exposes one split only when the caller supplies evidence of
the required frozen-prediction/approval boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Final, Mapping, Sequence

from courtvision.sports.mlb.training.hr_feature_allowlist import (
    MLBHRFeatureFieldClass,
    classify_mlb_hr_research_field,
)


LABEL_CUSTODY_SCHEMA_VERSION: Final = "mlb-hr-label-custody-v1"
LABEL_CUSTODY_ARTIFACT_TYPE: Final = "mlb_hr_label_custody"
LABEL_CUSTODY_FILENAME: Final = "mlb_hr_label_custody.json"
LABEL_COLUMN: Final = "is_home_run"
SPLIT_NAMES: Final = ("train", "validation", "test")

_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY_FIELDS: Final = ("row_id", "game_date", "game_id", "player_id")
_LABEL_ROW_FIELDS: Final = frozenset({"row_id", LABEL_COLUMN})


class MLBHRLabelCustodyError(ValueError):
    """Raised when label custody or a requested label opening fails closed."""


@dataclass(frozen=True, slots=True)
class MLBHRLabelCustodyBinding:
    """Label-free proof that the custody rows match one exact feature pack."""

    feature_pack_path: Path
    label_custody_path: Path
    feature_pack_sha256: str
    label_custody_sha256: str
    row_identity_sha256: str
    row_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "feature_pack_path", self.feature_pack_path.resolve())
        object.__setattr__(self, "label_custody_path", self.label_custody_path.resolve())
        if (
            self.row_count <= 0
            or _SHA256_PATTERN.fullmatch(self.feature_pack_sha256) is None
            or _SHA256_PATTERN.fullmatch(self.label_custody_sha256) is None
            or _SHA256_PATTERN.fullmatch(self.row_identity_sha256) is None
        ):
            raise MLBHRLabelCustodyError("invalid label-custody binding proof")


@dataclass(frozen=True, slots=True)
class MLBHRLabelOpeningAuthorization:
    """Evidence needed to open exactly one custody split."""

    split: str
    reason: str
    expected_row_ids: tuple[str, ...]
    frozen_prediction_artifact_sha256: str | None = None
    approval_receipt_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected_row_ids", tuple(self.expected_row_ids))
        if (
            self.split not in SPLIT_NAMES
            or not self.expected_row_ids
            or len(set(self.expected_row_ids)) != len(self.expected_row_ids)
        ):
            raise MLBHRLabelCustodyError("invalid label-opening authorization")
        if self.split == "train":
            valid = (
                self.reason == "train_fitting"
                and self.frozen_prediction_artifact_sha256 is None
                and self.approval_receipt_sha256 is None
            )
        elif self.split == "validation":
            valid = (
                self.reason == "frozen_prediction_validation"
                and _is_sha256(self.frozen_prediction_artifact_sha256)
                and self.approval_receipt_sha256 is None
            )
        else:
            valid = (
                self.reason == "approved_one_shot_test_handoff"
                and _is_sha256(self.frozen_prediction_artifact_sha256)
                and _is_sha256(self.approval_receipt_sha256)
            )
        if not valid:
            raise MLBHRLabelCustodyError(
                f"{self.split} labels do not have the required opening authority"
            )


@dataclass(frozen=True, slots=True)
class MLBHROpenedLabel:
    """One authorized row label joined back to its feature identity."""

    row_id: str
    game_date: date
    game_id: str
    player_id: str
    is_home_run: bool


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _file_sha256(path: Path, label: str) -> str:
    if not path.is_file():
        raise MLBHRLabelCustodyError(
            f"{label} must be an existing local file: {path}"
        )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise MLBHRLabelCustodyError(f"could not hash {label} {path}: {exc}") from exc
    return digest.hexdigest()


def _reject_nonstandard_number(value: str) -> object:
    raise ValueError(f"non-standard JSON number is prohibited: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is prohibited: {key}")
        result[key] = value
    return result


def _read_json_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_nonstandard_number,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise MLBHRLabelCustodyError(f"could not read {label} {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise MLBHRLabelCustodyError(f"{label} must contain a JSON object")
    return payload


def resolve_label_custody_path(
    feature_pack_path: str | Path,
    label_custody_path: str | Path | None = None,
) -> Path:
    """Resolve an explicit custody path or the fixed sibling artifact name."""

    feature_source = Path(feature_pack_path).expanduser().resolve()
    if label_custody_path is None:
        return feature_source.with_name(LABEL_CUSTODY_FILENAME)
    return Path(label_custody_path).expanduser().resolve()


def _required_identity_text(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MLBHRLabelCustodyError(f"{location} must be non-empty text")
    return value


def _feature_identities(
    feature_payload: Mapping[str, object],
) -> tuple[tuple[str, str, str, str], ...]:
    raw_rows = feature_payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise MLBHRLabelCustodyError("feature-pack rows must be a non-empty list")
    identities: list[tuple[str, str, str, str]] = []
    row_ids: set[str] = set()
    for index, raw_row in enumerate(raw_rows):
        location = f"feature-pack rows[{index}]"
        if not isinstance(raw_row, Mapping):
            raise MLBHRLabelCustodyError(f"{location} must be an object")
        identity = tuple(
            _required_identity_text(raw_row.get(field), f"{location}.{field}")
            for field in _IDENTITY_FIELDS
        )
        row_id = identity[0]
        if row_id in row_ids:
            raise MLBHRLabelCustodyError(f"duplicate feature-pack row_id: {row_id}")
        try:
            date.fromisoformat(identity[1])
        except ValueError as exc:
            raise MLBHRLabelCustodyError(
                f"{location}.game_date must be an ISO-8601 date"
            ) from exc
        row_ids.add(row_id)
        identities.append(identity)
    return tuple(identities)


def feature_row_identity_sha256(feature_payload: Mapping[str, object]) -> str:
    """Hash the canonical feature-row identity set, independent of row order."""

    identities = sorted(_feature_identities(feature_payload))
    encoded = json.dumps(
        identities,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _label_fields(value: object, *, location: str = "feature pack") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            field_name = str(key)
            if (
                field_name == LABEL_COLUMN
                or classify_mlb_hr_research_field(field_name)
                is MLBHRFeatureFieldClass.LABEL_OUTCOME
            ):
                found.append(f"{location}.{field_name}")
            found.extend(_label_fields(child, location=f"{location}.{field_name}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_label_fields(child, location=f"{location}[{index}]"))
    return found


def assert_model_visible_feature_pack_label_free(
    feature_payload: Mapping[str, object],
) -> None:
    """Fail if any outcome-label key appears anywhere in a feature artifact."""

    # Population-accounting metadata legitimately uses names such as
    # ``target_population_count``; it contains counts/definitions, never
    # model-row values.  Outcome-field detection remains strict everywhere
    # model-visible row or schema data can live.
    model_visible_payload = {
        key: value for key, value in feature_payload.items() if key != "population"
    }
    leaked = _label_fields(model_visible_payload)
    raw_names = feature_payload.get("feature_names")
    if isinstance(raw_names, list):
        leaked.extend(
            f"feature pack.feature_names[{index}]={name}"
            for index, name in enumerate(raw_names)
            if isinstance(name, str)
            and classify_mlb_hr_research_field(name)
            is MLBHRFeatureFieldClass.LABEL_OUTCOME
        )
    if leaked:
        raise MLBHRLabelCustodyError(
            "model-visible feature pack contains outcome labels: "
            + ", ".join(leaked[:10])
        )


def build_label_custody_payload(
    *,
    feature_payload: Mapping[str, object],
    feature_pack_sha256: str,
    labels: Sequence[Mapping[str, object]],
    created_at: str,
) -> dict[str, object]:
    """Build a custody artifact after the model-visible artifact is frozen."""

    assert_model_visible_feature_pack_label_free(feature_payload)
    if not _is_sha256(feature_pack_sha256):
        raise MLBHRLabelCustodyError("feature_pack_sha256 must be lowercase SHA-256")
    identities = _feature_identities(feature_payload)
    expected_row_ids = tuple(identity[0] for identity in identities)
    label_rows: list[dict[str, object]] = []
    observed_row_ids: list[str] = []
    for index, raw_row in enumerate(labels):
        if not isinstance(raw_row, Mapping):
            raise MLBHRLabelCustodyError(f"labels[{index}] must be an object")
        row_id = _required_identity_text(raw_row.get("row_id"), f"labels[{index}].row_id")
        label = raw_row.get(LABEL_COLUMN)
        if type(label) is not bool:
            raise MLBHRLabelCustodyError(
                f"labels[{index}].{LABEL_COLUMN} must be boolean true/false"
            )
        observed_row_ids.append(row_id)
        label_rows.append({"row_id": row_id, LABEL_COLUMN: label})
    if tuple(observed_row_ids) != expected_row_ids:
        raise MLBHRLabelCustodyError(
            "label rows must exactly match feature-pack row identities"
        )
    return {
        "schema_version": LABEL_CUSTODY_SCHEMA_VERSION,
        "artifact_type": LABEL_CUSTODY_ARTIFACT_TYPE,
        "mode": "historical_research",
        "created_at": created_at,
        "feature_pack_sha256": feature_pack_sha256,
        "feature_pack_row_identity_sha256": feature_row_identity_sha256(
            feature_payload
        ),
        "row_count": len(label_rows),
        "label_column": LABEL_COLUMN,
        "rows": label_rows,
        "custody_policy": {
            "train": "fitting_only",
            "validation": "open_after_frozen_prediction_validation",
            "test": "open_after_explicit_one_shot_approval",
        },
        "research_only": True,
        "approval_status": "not_approved",
        "model_training_enabled": False,
        "prediction_generation_enabled": False,
        "live_fetching_enabled": False,
        "eligible_for_betting": False,
        "ev_enabled": False,
        "kelly_eligible": False,
        "elite_enabled": False,
        "staking_enabled": False,
        "production_approved": False,
    }


def validate_mlb_hr_label_custody(
    *,
    feature_pack_path: str | Path,
    label_custody_path: str | Path | None = None,
) -> MLBHRLabelCustodyBinding:
    """Validate identity/hash binding without returning or summarizing labels."""

    feature_source = Path(feature_pack_path).expanduser().resolve()
    custody_source = resolve_label_custody_path(feature_source, label_custody_path)
    feature_hash = _file_sha256(feature_source, "feature pack")
    custody_hash = _file_sha256(custody_source, "label-custody artifact")
    feature_payload = _read_json_object(feature_source, "feature pack")
    custody_payload = _read_json_object(custody_source, "label-custody artifact")
    assert_model_visible_feature_pack_label_free(feature_payload)

    expected_fields: Mapping[str, object] = {
        "schema_version": LABEL_CUSTODY_SCHEMA_VERSION,
        "artifact_type": LABEL_CUSTODY_ARTIFACT_TYPE,
        "mode": "historical_research",
        "label_column": LABEL_COLUMN,
        "research_only": True,
        "approval_status": "not_approved",
    }
    invalid = [
        name for name, expected in expected_fields.items()
        if custody_payload.get(name) != expected
    ]
    if invalid:
        raise MLBHRLabelCustodyError(
            "label-custody artifact has invalid contract fields: "
            + ", ".join(invalid)
        )
    if custody_payload.get("feature_pack_sha256") != feature_hash:
        raise MLBHRLabelCustodyError(
            "label-custody feature_pack_sha256 does not match feature pack"
        )
    identity_hash = feature_row_identity_sha256(feature_payload)
    if custody_payload.get("feature_pack_row_identity_sha256") != identity_hash:
        raise MLBHRLabelCustodyError(
            "label-custody row identity hash does not match feature pack"
        )

    identities = _feature_identities(feature_payload)
    expected_row_ids = tuple(identity[0] for identity in identities)
    raw_label_rows = custody_payload.get("rows")
    if not isinstance(raw_label_rows, list):
        raise MLBHRLabelCustodyError("label-custody rows must be a list")
    observed_row_ids: list[str] = []
    for index, raw_row in enumerate(raw_label_rows):
        location = f"label-custody rows[{index}]"
        if not isinstance(raw_row, Mapping):
            raise MLBHRLabelCustodyError(f"{location} must be an object")
        if frozenset(raw_row) != _LABEL_ROW_FIELDS:
            raise MLBHRLabelCustodyError(
                f"{location} must contain only row_id and {LABEL_COLUMN}"
            )
        observed_row_ids.append(
            _required_identity_text(raw_row.get("row_id"), f"{location}.row_id")
        )
    if len(set(observed_row_ids)) != len(observed_row_ids):
        raise MLBHRLabelCustodyError("label-custody rows contain duplicate row_id")
    if tuple(observed_row_ids) != expected_row_ids:
        missing = sorted(set(expected_row_ids) - set(observed_row_ids))
        extra = sorted(set(observed_row_ids) - set(expected_row_ids))
        details: list[str] = []
        if missing:
            details.append(f"missing label rows={len(missing)}")
        if extra:
            details.append(f"extra label rows={len(extra)}")
        if not missing and not extra:
            details.append("label row order does not match feature rows")
        raise MLBHRLabelCustodyError(
            "label-custody rows do not exactly match feature rows: "
            + "; ".join(details)
        )
    if custody_payload.get("row_count") != len(expected_row_ids):
        raise MLBHRLabelCustodyError(
            "label-custody row_count does not match feature rows"
        )
    if (
        _file_sha256(feature_source, "feature pack") != feature_hash
        or _file_sha256(custody_source, "label-custody artifact") != custody_hash
    ):
        raise MLBHRLabelCustodyError(
            "feature pack or label-custody artifact changed during validation"
        )
    return MLBHRLabelCustodyBinding(
        feature_pack_path=feature_source,
        label_custody_path=custody_source,
        feature_pack_sha256=feature_hash,
        label_custody_sha256=custody_hash,
        row_identity_sha256=identity_hash,
        row_count=len(expected_row_ids),
    )


def _split_dates(split_payload: Mapping[str, object], split: str) -> frozenset[str]:
    raw_window = split_payload.get(split)
    if not isinstance(raw_window, Mapping):
        raise MLBHRLabelCustodyError(f"temporal split plan has no {split!r} window")
    raw_dates = raw_window.get("game_dates")
    if not isinstance(raw_dates, list) or not raw_dates:
        raise MLBHRLabelCustodyError(
            f"temporal split plan {split}.game_dates must be non-empty"
        )
    dates: list[str] = []
    for index, raw_date in enumerate(raw_dates):
        if not isinstance(raw_date, str):
            raise MLBHRLabelCustodyError(
                f"temporal split plan {split}.game_dates[{index}] is invalid"
            )
        try:
            parsed = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise MLBHRLabelCustodyError(
                f"temporal split plan {split}.game_dates[{index}] is invalid"
            ) from exc
        dates.append(parsed.isoformat())
    if dates != sorted(set(dates)):
        raise MLBHRLabelCustodyError(
            f"temporal split plan {split}.game_dates must be unique and ordered"
        )
    return frozenset(dates)


def open_mlb_hr_label_custody_split(
    *,
    feature_pack_path: str | Path,
    label_custody_path: str | Path | None,
    temporal_split_plan_path: str | Path,
    authorization: MLBHRLabelOpeningAuthorization | None,
) -> tuple[MLBHROpenedLabel, ...]:
    """Open one exact split after the required authority has been established."""

    if not isinstance(authorization, MLBHRLabelOpeningAuthorization):
        raise MLBHRLabelCustodyError(
            "labels cannot open without a validated opening authorization"
        )
    binding = validate_mlb_hr_label_custody(
        feature_pack_path=feature_pack_path,
        label_custody_path=label_custody_path,
    )
    split_source = Path(temporal_split_plan_path).expanduser().resolve()
    split_hash = _file_sha256(split_source, "temporal split plan")
    feature_payload = _read_json_object(binding.feature_pack_path, "feature pack")
    custody_payload = _read_json_object(
        binding.label_custody_path, "label-custody artifact"
    )
    split_payload = _read_json_object(split_source, "temporal split plan")
    split_dates = _split_dates(split_payload, authorization.split)
    identities = {
        identity[0]: identity
        for identity in _feature_identities(feature_payload)
        if identity[1] in split_dates
    }
    expected_row_ids = tuple(sorted(identities))
    if tuple(sorted(authorization.expected_row_ids)) != expected_row_ids:
        raise MLBHRLabelCustodyError(
            "opening authority does not cover the exact feature rows in the split"
        )

    raw_rows = custody_payload.get("rows")
    if not isinstance(raw_rows, list):
        raise MLBHRLabelCustodyError("label-custody rows must be a list")
    labels_by_row_id: dict[str, bool] = {}
    for index, raw_row in enumerate(raw_rows):
        if not isinstance(raw_row, Mapping):
            raise MLBHRLabelCustodyError(
                f"label-custody rows[{index}] must be an object"
            )
        row_id = raw_row.get("row_id")
        if row_id not in identities:
            continue
        label = raw_row.get(LABEL_COLUMN)
        if type(label) is not bool:
            raise MLBHRLabelCustodyError(
                f"label for row_id={row_id!r} must be boolean true/false"
            )
        labels_by_row_id[str(row_id)] = label
    if set(labels_by_row_id) != set(identities):
        raise MLBHRLabelCustodyError(
            "opened label population does not match the authorized split"
        )
    opened = tuple(
        MLBHROpenedLabel(
            row_id=row_id,
            game_date=date.fromisoformat(identities[row_id][1]),
            game_id=identities[row_id][2],
            player_id=identities[row_id][3],
            is_home_run=labels_by_row_id[row_id],
        )
        for row_id in authorization.expected_row_ids
    )
    if (
        _file_sha256(binding.feature_pack_path, "feature pack")
        != binding.feature_pack_sha256
        or _file_sha256(binding.label_custody_path, "label-custody artifact")
        != binding.label_custody_sha256
        or _file_sha256(split_source, "temporal split plan") != split_hash
    ):
        raise MLBHRLabelCustodyError(
            "an input artifact changed while opening label custody"
        )
    return opened


__all__ = [
    "LABEL_COLUMN",
    "LABEL_CUSTODY_ARTIFACT_TYPE",
    "LABEL_CUSTODY_FILENAME",
    "LABEL_CUSTODY_SCHEMA_VERSION",
    "MLBHRLabelCustodyBinding",
    "MLBHRLabelCustodyError",
    "MLBHRLabelOpeningAuthorization",
    "MLBHROpenedLabel",
    "assert_model_visible_feature_pack_label_free",
    "build_label_custody_payload",
    "feature_row_identity_sha256",
    "open_mlb_hr_label_custody_split",
    "resolve_label_custody_path",
    "validate_mlb_hr_label_custody",
]
