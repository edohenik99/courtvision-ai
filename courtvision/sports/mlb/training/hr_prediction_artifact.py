"""Read-only validation for frozen MLB HR research prediction artifacts.

The loader binds existing prediction bytes to sealed research inputs.  It has
no model, transformer, evaluator, label reader, fetcher, writer, or wagering
operation.  In particular, it hashes the feature pack without opening its
row-level labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Final, Mapping


FROZEN_PREDICTION_ARTIFACT_SCHEMA_VERSION: Final = (
    "mlb-hr-frozen-research-predictions-v1"
)
FROZEN_PREDICTION_ARTIFACT_TYPE: Final = "mlb_hr_frozen_research_predictions"
MODEL_SPECIFICATION_ID: Final = "mlb-hr-first-research-model-v1"
ROW_IDENTITY_KEYS: Final = ("row_id", "game_date", "game_id", "player_id")
PROBABILITY_FIELDS: Final = (
    "raw_home_run_probability",
    "calibrated_home_run_probability",
)
IMMUTABLE_WRITE_POLICY: Final = "create_once_atomic_no_overwrite"
ALLOWED_SPLIT_IDS: Final = frozenset({"validation", "test"})

_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[4]
DEFAULT_MODEL_SPECIFICATION_PATH: Final = (
    _REPOSITORY_ROOT
    / "docs"
    / "COURTVISION_MLB_HR_MODEL_SPECIFICATION_AND_LABEL_HANDOFF.md"
)
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_ROOT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "artifact_type",
        "mode",
        "research_only",
        "approval_status",
        "production_approved",
        "operational_use_enabled",
        "model_training_enabled",
        "prediction_generation_enabled",
        "evaluation_enabled",
        "live_fetching_enabled",
        "evaluation_data_sealed",
        "immutable",
        "write_policy",
        "feature_pack_sha256",
        "temporal_split_plan_sha256",
        "fitted_preprocessing_artifact_sha256",
        "model_specification_id",
        "model_specification_sha256",
        "code_version",
        "code_version_sha256",
        "split_id",
        "window_id",
        "prediction_timestamp",
        "row_identity_keys",
        "probability_fields",
        "probability_minimum",
        "probability_maximum",
        "rows",
        "artifact_sha256",
    }
)
_ROW_FIELDS: Final = frozenset({*ROW_IDENTITY_KEYS, *PROBABILITY_FIELDS})
_FALSE_SAFETY_FIELDS: Final = (
    "production_approved",
    "operational_use_enabled",
    "model_training_enabled",
    "prediction_generation_enabled",
    "evaluation_enabled",
    "live_fetching_enabled",
)
_LABEL_FIELD_TOKENS: Final = frozenset(
    {
        "actual",
        "final",
        "label",
        "labels",
        "outcome",
        "outcomes",
        "result",
        "results",
        "score",
        "scores",
        "target",
        "targets",
    }
)
_BETTING_FIELD_TOKENS: Final = frozenset(
    {
        "bankroll",
        "bet",
        "bets",
        "betting",
        "edge",
        "elite",
        "ev",
        "kelly",
        "line",
        "odds",
        "payout",
        "profit",
        "price",
        "roi",
        "sportsbook",
        "stake",
        "stakes",
        "staking",
        "wager",
        "wagers",
        "wagering",
    }
)


class MLBHRFrozenPredictionArtifactError(ValueError):
    """Raised when a frozen prediction artifact must fail closed."""


@dataclass(frozen=True, slots=True)
class MLBHRFrozenPredictionRow:
    """One validated feature-row prediction with no outcome data."""

    row_id: str
    game_date: date
    game_id: str
    player_id: str
    raw_home_run_probability: float
    calibrated_home_run_probability: float


@dataclass(frozen=True, slots=True)
class MLBHRFrozenPredictionArtifact:
    """Immutable in-memory view of one validated prediction artifact."""

    path: Path
    feature_pack_sha256: str
    temporal_split_plan_sha256: str
    fitted_preprocessing_artifact_sha256: str
    model_specification_sha256: str
    code_version: str
    code_version_sha256: str
    split_id: str
    window_id: str
    prediction_timestamp: datetime
    rows: tuple[MLBHRFrozenPredictionRow, ...]
    artifact_sha256: str
    schema_version: str = FROZEN_PREDICTION_ARTIFACT_SCHEMA_VERSION
    artifact_type: str = FROZEN_PREDICTION_ARTIFACT_TYPE
    model_specification_id: str = MODEL_SPECIFICATION_ID
    row_identity_keys: tuple[str, ...] = ROW_IDENTITY_KEYS
    probability_fields: tuple[str, ...] = PROBABILITY_FIELDS
    probability_minimum: float = 0.0
    probability_maximum: float = 1.0
    mode: str = "historical_research"
    research_only: bool = True
    approval_status: str = "not_approved"
    production_approved: bool = False
    operational_use_enabled: bool = False
    model_training_enabled: bool = False
    prediction_generation_enabled: bool = False
    evaluation_enabled: bool = False
    live_fetching_enabled: bool = False
    evaluation_data_sealed: bool = True
    immutable: bool = True
    write_policy: str = IMMUTABLE_WRITE_POLICY

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", self.path.resolve())
        object.__setattr__(self, "rows", tuple(self.rows))


def _file_sha256(path: str | Path, label: str) -> str:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise MLBHRFrozenPredictionArtifactError(
            f"{label} must be an existing local file: {source}"
        )
    digest = hashlib.sha256()
    try:
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise MLBHRFrozenPredictionArtifactError(
            f"could not hash {label} {source}: {exc}"
        ) from exc
    return digest.hexdigest()


def _reject_nonstandard_number(value: str) -> object:
    raise ValueError(f"non-standard JSON number is prohibited: {value}")


def _read_json_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_nonstandard_number,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise MLBHRFrozenPredictionArtifactError(
            f"could not read {label} {path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise MLBHRFrozenPredictionArtifactError(
            f"{label} must contain a JSON object"
        )
    return payload


def _canonical_payload_sha256(payload: Mapping[str, object]) -> str:
    content = dict(payload)
    content.pop("artifact_sha256", None)
    try:
        encoded = json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MLBHRFrozenPredictionArtifactError(
            f"prediction artifact cannot be canonicalized: {exc}"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _field_tokens(field_name: str) -> frozenset[str]:
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", field_name)
    return frozenset(
        token
        for token in re.split(r"[^a-z0-9]+", snake.casefold())
        if token
    )


def _unsupported_fields(
    payload: Mapping[str, object],
    allowed: frozenset[str],
    location: str,
) -> None:
    extras = sorted(str(name) for name in payload if name not in allowed)
    if not extras:
        return
    label_fields: list[str] = []
    betting_fields: list[str] = []
    for name in extras:
        tokens = _field_tokens(name)
        if name.casefold() == "is_home_run" or tokens & _LABEL_FIELD_TOKENS:
            label_fields.append(name)
        elif {"home", "run"}.issubset(tokens):
            label_fields.append(name)
        if (
            tokens & _BETTING_FIELD_TOKENS
            or {"expected", "value"}.issubset(tokens)
        ):
            betting_fields.append(name)
    if label_fields:
        raise MLBHRFrozenPredictionArtifactError(
            f"{location} contains prohibited label/outcome/final-score fields: "
            + ", ".join(label_fields)
        )
    if betting_fields:
        raise MLBHRFrozenPredictionArtifactError(
            f"{location} contains prohibited EV/Kelly/staking/betting fields: "
            + ", ".join(betting_fields)
        )
    raise MLBHRFrozenPredictionArtifactError(
        f"{location} contains unsupported fields: " + ", ".join(extras)
    )


def _require_exact_fields(
    payload: Mapping[str, object],
    required: frozenset[str],
    location: str,
) -> None:
    missing = sorted(required - payload.keys())
    if missing:
        raise MLBHRFrozenPredictionArtifactError(
            f"{location} is missing required fields: " + ", ".join(missing)
        )
    _unsupported_fields(payload, required, location)


def _require_sha256(payload: Mapping[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise MLBHRFrozenPredictionArtifactError(
            f"prediction artifact {field_name} must be lowercase SHA-256"
        )
    return value


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MLBHRFrozenPredictionArtifactError(
            "prediction_timestamp must be an ISO-8601 timezone-aware datetime"
        )
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise MLBHRFrozenPredictionArtifactError(
            "prediction_timestamp must be an ISO-8601 timezone-aware datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MLBHRFrozenPredictionArtifactError(
            "prediction_timestamp must be timezone-aware"
        )
    return parsed


def _split_window(
    temporal_split_payload: Mapping[str, object], split_id: str
) -> tuple[str, frozenset[date]]:
    raw_window = temporal_split_payload.get(split_id)
    if not isinstance(raw_window, Mapping):
        raise MLBHRFrozenPredictionArtifactError(
            f"temporal split plan has no {split_id!r} window"
        )
    raw_dates = raw_window.get("game_dates")
    if not isinstance(raw_dates, list) or not raw_dates:
        raise MLBHRFrozenPredictionArtifactError(
            f"temporal split plan {split_id}.game_dates must be non-empty"
        )
    dates: list[date] = []
    for index, raw_date in enumerate(raw_dates):
        if not isinstance(raw_date, str):
            raise MLBHRFrozenPredictionArtifactError(
                f"temporal split plan {split_id}.game_dates[{index}] is invalid"
            )
        try:
            dates.append(date.fromisoformat(raw_date))
        except ValueError as exc:
            raise MLBHRFrozenPredictionArtifactError(
                f"temporal split plan {split_id}.game_dates[{index}] is invalid"
            ) from exc
    if dates != sorted(set(dates)):
        raise MLBHRFrozenPredictionArtifactError(
            f"temporal split plan {split_id}.game_dates must be unique and ordered"
        )
    window_id = f"{split_id}:{dates[0].isoformat()}:{dates[-1].isoformat()}"
    return window_id, frozenset(dates)


def _require_probability(value: object, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MLBHRFrozenPredictionArtifactError(
            f"{location} must be numeric and within [0, 1]"
        )
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise MLBHRFrozenPredictionArtifactError(
            f"{location} must be numeric and within [0, 1]"
        )
    return parsed


def _parse_rows(
    payload: Mapping[str, object], split_dates: frozenset[date]
) -> tuple[MLBHRFrozenPredictionRow, ...]:
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise MLBHRFrozenPredictionArtifactError(
            "prediction artifact rows must be a non-empty list"
        )
    rows: list[MLBHRFrozenPredictionRow] = []
    row_ids: set[str] = set()
    identities: set[tuple[str, str, str, str]] = set()
    for index, raw_row in enumerate(raw_rows):
        location = f"prediction artifact rows[{index}]"
        if not isinstance(raw_row, Mapping):
            raise MLBHRFrozenPredictionArtifactError(
                f"{location} must be an object"
            )
        _require_exact_fields(raw_row, _ROW_FIELDS, location)
        text_values: dict[str, str] = {}
        for field_name in ("row_id", "game_id", "player_id"):
            value = raw_row.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise MLBHRFrozenPredictionArtifactError(
                    f"{location}.{field_name} must be non-empty text"
                )
            text_values[field_name] = value
        raw_game_date = raw_row.get("game_date")
        if not isinstance(raw_game_date, str):
            raise MLBHRFrozenPredictionArtifactError(
                f"{location}.game_date must be an ISO-8601 date"
            )
        try:
            game_date = date.fromisoformat(raw_game_date)
        except ValueError as exc:
            raise MLBHRFrozenPredictionArtifactError(
                f"{location}.game_date must be an ISO-8601 date"
            ) from exc
        if game_date not in split_dates:
            raise MLBHRFrozenPredictionArtifactError(
                f"{location}.game_date is outside the declared split window"
            )
        identity = (
            text_values["row_id"],
            raw_game_date,
            text_values["game_id"],
            text_values["player_id"],
        )
        if text_values["row_id"] in row_ids or identity in identities:
            raise MLBHRFrozenPredictionArtifactError(
                f"{location} duplicates a row identity"
            )
        row_ids.add(text_values["row_id"])
        identities.add(identity)
        rows.append(
            MLBHRFrozenPredictionRow(
                row_id=text_values["row_id"],
                game_date=game_date,
                game_id=text_values["game_id"],
                player_id=text_values["player_id"],
                raw_home_run_probability=_require_probability(
                    raw_row.get("raw_home_run_probability"),
                    f"{location}.raw_home_run_probability",
                ),
                calibrated_home_run_probability=_require_probability(
                    raw_row.get("calibrated_home_run_probability"),
                    f"{location}.calibrated_home_run_probability",
                ),
            )
        )
    return tuple(rows)


def load_frozen_prediction_artifact(
    prediction_artifact_path: str | Path,
    *,
    feature_pack_path: str | Path,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
    model_specification_path: str | Path = DEFAULT_MODEL_SPECIFICATION_PATH,
) -> MLBHRFrozenPredictionArtifact:
    """Validate one existing artifact without opening labels or writing files."""

    prediction_source = Path(prediction_artifact_path).expanduser().resolve()
    feature_source = Path(feature_pack_path).expanduser().resolve()
    split_source = Path(temporal_split_plan_path).expanduser().resolve()
    preprocessing_source = (
        Path(fitted_preprocessing_artifact_path).expanduser().resolve()
    )
    model_spec_source = Path(model_specification_path).expanduser().resolve()
    sources = (
        (feature_source, "feature pack"),
        (split_source, "temporal split plan"),
        (preprocessing_source, "fitted preprocessing artifact"),
        (model_spec_source, "model specification"),
        (prediction_source, "prediction artifact"),
    )
    initial_hashes = tuple(_file_sha256(path, label) for path, label in sources)
    payload = _read_json_object(prediction_source, "prediction artifact")
    _require_exact_fields(payload, _ROOT_FIELDS, "prediction artifact")

    if payload.get("schema_version") != FROZEN_PREDICTION_ARTIFACT_SCHEMA_VERSION:
        raise MLBHRFrozenPredictionArtifactError(
            "prediction artifact schema_version is unsupported"
        )
    fixed_values = {
        "artifact_type": FROZEN_PREDICTION_ARTIFACT_TYPE,
        "mode": "historical_research",
        "approval_status": "not_approved",
        "write_policy": IMMUTABLE_WRITE_POLICY,
        "model_specification_id": MODEL_SPECIFICATION_ID,
        "row_identity_keys": list(ROW_IDENTITY_KEYS),
        "probability_fields": list(PROBABILITY_FIELDS),
    }
    invalid_fixed = [
        field_name
        for field_name, expected in fixed_values.items()
        if payload.get(field_name) != expected
    ]
    if invalid_fixed:
        raise MLBHRFrozenPredictionArtifactError(
            "prediction artifact cannot relax its research-only/immutable "
            "contract fields: " + ", ".join(invalid_fixed)
        )
    required_true = (
        "research_only",
        "evaluation_data_sealed",
        "immutable",
    )
    invalid_true = [
        field_name
        for field_name in required_true
        if payload.get(field_name) is not True
    ]
    if invalid_true:
        raise MLBHRFrozenPredictionArtifactError(
            "prediction artifact cannot relax its research-only/immutable "
            "contract fields: " + ", ".join(invalid_true)
        )
    probability_bounds = (
        payload.get("probability_minimum"),
        payload.get("probability_maximum"),
    )
    if (
        any(isinstance(value, bool) for value in probability_bounds)
        or probability_bounds != (0.0, 1.0)
    ):
        raise MLBHRFrozenPredictionArtifactError(
            "prediction artifact probability bounds must remain numeric [0, 1]"
        )
    enabled = [
        field_name
        for field_name in _FALSE_SAFETY_FIELDS
        if payload.get(field_name) is not False
    ]
    if enabled:
        raise MLBHRFrozenPredictionArtifactError(
            "prediction artifact cannot enable production or execution gates: "
            + ", ".join(enabled)
        )

    feature_hash = _require_sha256(payload, "feature_pack_sha256")
    split_hash = _require_sha256(payload, "temporal_split_plan_sha256")
    preprocessing_hash = _require_sha256(
        payload, "fitted_preprocessing_artifact_sha256"
    )
    model_spec_hash = _require_sha256(payload, "model_specification_sha256")
    code_version_hash = _require_sha256(payload, "code_version_sha256")
    artifact_hash = _require_sha256(payload, "artifact_sha256")
    expected_input_hashes = initial_hashes[:4]
    recorded_input_hashes = (
        feature_hash,
        split_hash,
        preprocessing_hash,
        model_spec_hash,
    )
    if recorded_input_hashes != expected_input_hashes:
        names = (
            "feature_pack_sha256",
            "temporal_split_plan_sha256",
            "fitted_preprocessing_artifact_sha256",
            "model_specification_sha256",
        )
        mismatches = [
            name
            for name, recorded, expected in zip(
                names, recorded_input_hashes, expected_input_hashes, strict=True
            )
            if recorded != expected
        ]
        raise MLBHRFrozenPredictionArtifactError(
            "prediction artifact input hash mismatch: " + ", ".join(mismatches)
        )

    code_version = payload.get("code_version")
    if not isinstance(code_version, str) or not code_version.strip():
        raise MLBHRFrozenPredictionArtifactError(
            "prediction artifact code_version must be non-empty text"
        )
    expected_code_hash = hashlib.sha256(code_version.encode("utf-8")).hexdigest()
    if code_version_hash != expected_code_hash:
        raise MLBHRFrozenPredictionArtifactError(
            "prediction artifact code_version_sha256 does not match code_version"
        )
    if artifact_hash != _canonical_payload_sha256(payload):
        raise MLBHRFrozenPredictionArtifactError(
            "prediction artifact content SHA-256 does not match"
        )

    split_id = payload.get("split_id")
    if split_id not in ALLOWED_SPLIT_IDS:
        raise MLBHRFrozenPredictionArtifactError(
            "prediction artifact split_id must be validation or test"
        )
    temporal_split_payload = _read_json_object(split_source, "temporal split plan")
    expected_window_id, split_dates = _split_window(
        temporal_split_payload, str(split_id)
    )
    window_id = payload.get("window_id")
    if window_id != expected_window_id:
        raise MLBHRFrozenPredictionArtifactError(
            f"prediction artifact window_id must be {expected_window_id!r}"
        )
    prediction_timestamp = _parse_timestamp(payload.get("prediction_timestamp"))
    rows = _parse_rows(payload, split_dates)

    final_hashes = tuple(_file_sha256(path, label) for path, label in sources)
    if final_hashes != initial_hashes:
        raise MLBHRFrozenPredictionArtifactError(
            "an input or prediction artifact changed during validation"
        )

    return MLBHRFrozenPredictionArtifact(
        path=prediction_source,
        feature_pack_sha256=feature_hash,
        temporal_split_plan_sha256=split_hash,
        fitted_preprocessing_artifact_sha256=preprocessing_hash,
        model_specification_sha256=model_spec_hash,
        code_version=code_version,
        code_version_sha256=code_version_hash,
        split_id=str(split_id),
        window_id=str(window_id),
        prediction_timestamp=prediction_timestamp,
        rows=rows,
        artifact_sha256=artifact_hash,
    )


def validate_frozen_prediction_artifact(
    prediction_artifact_path: str | Path,
    *,
    feature_pack_path: str | Path,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
    model_specification_path: str | Path = DEFAULT_MODEL_SPECIFICATION_PATH,
) -> MLBHRFrozenPredictionArtifact:
    """Validate and return the same immutable view as the read-only loader."""

    return load_frozen_prediction_artifact(
        prediction_artifact_path,
        feature_pack_path=feature_pack_path,
        temporal_split_plan_path=temporal_split_plan_path,
        fitted_preprocessing_artifact_path=fitted_preprocessing_artifact_path,
        model_specification_path=model_specification_path,
    )


__all__ = [
    "ALLOWED_SPLIT_IDS",
    "DEFAULT_MODEL_SPECIFICATION_PATH",
    "FROZEN_PREDICTION_ARTIFACT_SCHEMA_VERSION",
    "FROZEN_PREDICTION_ARTIFACT_TYPE",
    "IMMUTABLE_WRITE_POLICY",
    "MLBHRFrozenPredictionArtifact",
    "MLBHRFrozenPredictionArtifactError",
    "MLBHRFrozenPredictionRow",
    "MODEL_SPECIFICATION_ID",
    "PROBABILITY_FIELDS",
    "ROW_IDENTITY_KEYS",
    "load_frozen_prediction_artifact",
    "validate_frozen_prediction_artifact",
]
