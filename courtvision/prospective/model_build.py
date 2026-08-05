"""Deterministic NBA baseline-build evidence and orchestration.

This module owns only the reusable, caller-driven build boundary.  It does not
fetch provider data, fit ``CourtVisionAI``, generate predictions, or touch any
operational history.  Publication and strict on-disk validation live in
``model_manifest_io``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime
import csv
import io
import math
import numbers
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pandas as pd

from courtvision.prospective.contracts import (
    ConfigurationProvenanceV1,
    FrozenJSONMapping,
    GitProvenanceV1,
    ProspectiveContractError,
    ProspectiveDirtyTreeError,
    ProspectiveProvenanceError,
    canonical_json_bytes,
    canonical_sha256,
)

if TYPE_CHECKING:
    from courtvision.prospective.model_manifest_io import VerifiedModelBuild


MODEL_ID = "courtvision-nba-baselines"
FEATURE_SCHEMA_VERSION = "courtvision-nba-baselines-v1"
TRAINING_INPUT_SCHEMA_VERSION = 1

TRAINING_ROW_COLUMNS = (
    "game_id",
    "game_date",
    "player_id",
    "player_name",
    "team_abbr",
    "min",
    "pts",
    "reb",
    "ast",
    "stl",
    "blk",
    "fg3m",
)

PLAYER_BASELINE_COLUMNS = (
    "player_id",
    "player_name",
    "team_abbr",
    "games",
    "min_avg",
    "min_recent",
    "pts_avg",
    "pts_recent",
    "pts_std",
    "reb_avg",
    "reb_recent",
    "reb_std",
    "ast_avg",
    "ast_recent",
    "ast_std",
    "stl_avg",
    "stl_recent",
    "stl_std",
    "blk_avg",
    "blk_recent",
    "blk_std",
    "fg3m_avg",
    "fg3m_recent",
    "fg3m_std",
    "player_key",
)

TEAM_BASELINE_COLUMNS = (
    "team_abbr",
    "games",
    "team_pts_avg",
    "team_pts_recent",
    "team_reb_avg",
    "team_reb_recent",
    "team_ast_avg",
    "team_ast_recent",
    "team_stl_avg",
    "team_stl_recent",
    "team_blk_avg",
    "team_blk_recent",
    "team_fg3m_avg",
    "team_fg3m_recent",
    "opp_pts_allowed_avg",
    "opp_pts_allowed_recent",
    "opp_reb_allowed_avg",
    "opp_reb_allowed_recent",
    "opp_ast_allowed_avg",
    "opp_ast_allowed_recent",
    "opp_stl_allowed_avg",
    "opp_stl_allowed_recent",
    "opp_blk_allowed_avg",
    "opp_blk_allowed_recent",
    "opp_fg3m_allowed_avg",
    "opp_fg3m_allowed_recent",
)

_TRAINING_TEXT_COLUMNS = TRAINING_ROW_COLUMNS[:5]
_TRAINING_NUMERIC_COLUMNS = TRAINING_ROW_COLUMNS[5:]
_TEAM_INPUT_COLUMNS = (
    "game_id",
    "game_date",
    "team_abbr",
    "min",
    "pts",
    "reb",
    "ast",
    "stl",
    "blk",
    "fg3m",
)
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"[A-Za-z]:[\\/]")


class VerifiedModelBuildError(ProspectiveProvenanceError):
    """Base failure for verified model-build construction or publication."""


class ModelBuildBuilderError(VerifiedModelBuildError):
    """Raised when a caller-supplied baseline builder fails."""


class ModelBuildSerializationError(VerifiedModelBuildError):
    """Raised when a baseline frame cannot be serialized canonically."""


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProspectiveContractError(f"{field_name} is required")
    normalized = value.strip()
    if "\r" in normalized or "\n" in normalized:
        raise ProspectiveContractError(f"{field_name} may not contain newlines")
    return normalized


def _required_date(value: date | str, field_name: str) -> date:
    if isinstance(value, datetime):
        raise ProspectiveContractError(f"{field_name} must be a date, not datetime")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ProspectiveContractError(
                f"{field_name} must be an ISO YYYY-MM-DD date"
            ) from exc
        if value != parsed.isoformat():
            raise ProspectiveContractError(
                f"{field_name} must be an ISO YYYY-MM-DD date"
            )
        return parsed
    raise ProspectiveContractError(f"{field_name} must be a date")


def _utc_clock_value(clock: Callable[[], datetime], field_name: str) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ProspectiveContractError(f"{field_name} clock must return timezone-aware UTC")
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError) as exc:
        raise ProspectiveContractError(f"{field_name} clock returned invalid UTC time") from exc
    if offset is None or offset.total_seconds() != 0:
        raise ProspectiveContractError(f"{field_name} clock must return UTC")
    return value.astimezone(UTC)


def _canonical_policy(value: Mapping[str, Any] | None, field_name: str) -> dict[str, Any]:
    supplied: Mapping[str, Any] = {} if value is None else value
    if not isinstance(supplied, Mapping):
        raise ProspectiveContractError(f"{field_name} must be a mapping")
    validated = ConfigurationProvenanceV1.from_configuration(
        {field_name: supplied}
    ).canonical_configuration.to_dict()
    policy = validated[field_name]
    if not isinstance(policy, dict):
        raise ProspectiveContractError(f"{field_name} must be a mapping")
    _reject_absolute_paths(policy, field_name)
    return policy


def _reject_absolute_paths(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_absolute_paths(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_absolute_paths(item, f"{path}[{index}]")
    elif isinstance(value, str) and (
        value.startswith(("/", "\\\\"))
        or _WINDOWS_ABSOLUTE_PATH.match(value) is not None
    ):
        raise ProspectiveContractError(
            f"{path} may not contain an absolute filesystem path"
        )


def _canonical_identifier(value: object, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, numbers.Integral)):
        raise ProspectiveContractError(f"{field_name} must be a string or integer identifier")
    return _required_text(str(value), field_name)


def _canonical_number(value: object, field_name: str) -> float | None:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ProspectiveContractError(f"{field_name} must be a finite number or null")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ProspectiveContractError(f"{field_name} must be finite")
    return 0.0 if normalized == 0.0 else normalized


def _canonical_training_row(
    value: Mapping[str, Any],
    *,
    row_index: int,
    requested_start_date: date,
    requested_end_date: date,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProspectiveContractError(f"normalized_rows[{row_index}] must be a mapping")
    supplied = set(value)
    expected = set(TRAINING_ROW_COLUMNS)
    if supplied != expected:
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected, key=str)
        raise ProspectiveContractError(
            f"normalized_rows[{row_index}] fields do not match the v1 schema; "
            f"missing={missing}, extra={extra}"
        )
    game_date = _required_date(value["game_date"], f"normalized_rows[{row_index}].game_date")
    if not requested_start_date <= game_date <= requested_end_date:
        raise ProspectiveContractError(
            f"normalized_rows[{row_index}].game_date falls outside the requested interval"
        )
    player_name = _required_text(
        value["player_name"], f"normalized_rows[{row_index}].player_name"
    )
    team_abbr = _required_text(
        value["team_abbr"], f"normalized_rows[{row_index}].team_abbr"
    )
    if team_abbr != team_abbr.upper():
        raise ProspectiveContractError(
            f"normalized_rows[{row_index}].team_abbr must be uppercase normalized text"
        )
    row: dict[str, Any] = {
        "game_id": _canonical_identifier(
            value["game_id"], f"normalized_rows[{row_index}].game_id"
        ),
        "game_date": game_date.isoformat(),
        "player_id": _canonical_identifier(
            value["player_id"], f"normalized_rows[{row_index}].player_id"
        ),
        "player_name": player_name,
        "team_abbr": team_abbr,
    }
    for column in _TRAINING_NUMERIC_COLUMNS:
        row[column] = _canonical_number(
            value[column], f"normalized_rows[{row_index}].{column}"
        )
    return row


def _typed_sort_value(value: Any) -> tuple[int, Any]:
    return (0, "") if value is None else (1, value)


def _training_row_sort_key(row: Mapping[str, Any]) -> tuple[tuple[int, Any], ...]:
    return tuple(_typed_sort_value(row[column]) for column in TRAINING_ROW_COLUMNS)


def _normalized_row_schema() -> dict[str, Any]:
    fields = [
        {"name": "game_id", "logical_type": "non_empty_identifier_string", "nullable": False},
        {"name": "game_date", "logical_type": "iso_date_yyyy_mm_dd", "nullable": False},
        {"name": "player_id", "logical_type": "non_empty_identifier_string", "nullable": False},
        {"name": "player_name", "logical_type": "trimmed_utf8_string", "nullable": False},
        {"name": "team_abbr", "logical_type": "uppercase_utf8_string", "nullable": False},
    ]
    fields.extend(
        {"name": column, "logical_type": "finite_float64", "nullable": True}
        for column in _TRAINING_NUMERIC_COLUMNS
    )
    return {
        "field_order": list(TRAINING_ROW_COLUMNS),
        "fields": fields,
        "null_representation": "json_null",
    }


_TRAINING_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version",
        "sport",
        "league",
        "provider_name",
        "provider_endpoint_version",
        "requested_start_date",
        "requested_end_date",
        "normalized_row_schema",
        "normalized_rows",
        "row_count",
        "player_input_digest",
        "team_input_digest",
        "training_data_digest",
        "selection_policy",
        "manual_context_policy",
        "exclusion_policy",
    }
)


def _training_digests(content: Mapping[str, Any]) -> tuple[str, str, str]:
    rows = content["normalized_rows"]
    if not isinstance(rows, list):
        raise ProspectiveContractError("normalized_rows must be a list")
    player_digest = canonical_sha256(
        {
            "digest_scope": "player_baseline_input_v1",
            "normalized_row_schema": content["normalized_row_schema"],
            "rows": rows,
        }
    )
    team_rows = [
        {column: row[column] for column in _TEAM_INPUT_COLUMNS}
        for row in rows
    ]
    team_digest = canonical_sha256(
        {
            "digest_scope": "team_baseline_input_v1",
            "field_order": list(_TEAM_INPUT_COLUMNS),
            "rows": team_rows,
        }
    )
    overall = dict(content)
    overall["player_input_digest"] = player_digest
    overall["team_input_digest"] = team_digest
    overall.pop("training_data_digest", None)
    training_digest = canonical_sha256(overall)
    return player_digest, team_digest, training_digest


def validate_training_input_evidence(
    evidence: Mapping[str, Any],
) -> FrozenJSONMapping:
    """Strictly reconstruct and verify canonical training-input evidence."""

    if not isinstance(evidence, Mapping) or set(evidence) != _TRAINING_EVIDENCE_FIELDS:
        raise ProspectiveContractError("training-input evidence fields do not match v1")
    raw = FrozenJSONMapping(evidence).to_dict()
    if raw["schema_version"] != TRAINING_INPUT_SCHEMA_VERSION:
        raise ProspectiveContractError("training-input schema_version must be 1")
    if raw["sport"] != "NBA" or raw["league"] != "NBA":
        raise ProspectiveContractError("training-input sport and league must be NBA")
    if _required_text(raw["provider_name"], "provider_name") != raw["provider_name"]:
        raise ProspectiveContractError("provider_name must be canonical trimmed text")
    if (
        _required_text(raw["provider_endpoint_version"], "provider_endpoint_version")
        != raw["provider_endpoint_version"]
    ):
        raise ProspectiveContractError(
            "provider_endpoint_version must be canonical trimmed text"
        )
    _reject_absolute_paths(raw["provider_name"], "provider_name")
    _reject_absolute_paths(
        raw["provider_endpoint_version"], "provider_endpoint_version"
    )
    start = _required_date(raw["requested_start_date"], "requested_start_date")
    end = _required_date(raw["requested_end_date"], "requested_end_date")
    if end < start:
        raise ProspectiveContractError("requested_end_date may not precede requested_start_date")
    if raw["normalized_row_schema"] != _normalized_row_schema():
        raise ProspectiveContractError("normalized_row_schema does not match v1")
    rows_value = raw["normalized_rows"]
    if not isinstance(rows_value, list):
        raise ProspectiveContractError("normalized_rows must be a list")
    rows = [
        _canonical_training_row(
            row,
            row_index=index,
            requested_start_date=start,
            requested_end_date=end,
        )
        for index, row in enumerate(rows_value)
    ]
    if rows != sorted(rows, key=_training_row_sort_key):
        raise ProspectiveContractError("normalized_rows are not in canonical typed-field order")
    if type(raw["row_count"]) is not int or raw["row_count"] != len(rows):
        raise ProspectiveContractError("row_count does not match normalized_rows")
    selection = raw["selection_policy"]
    manual = raw["manual_context_policy"]
    exclusion = raw["exclusion_policy"]
    for field_name, policy in (
        ("selection_policy", selection),
        ("manual_context_policy", manual),
        ("exclusion_policy", exclusion),
    ):
        _canonical_policy(policy, field_name)
    if not isinstance(selection, dict) or selection.get("requested_interval") != "inclusive":
        raise ProspectiveContractError("selection_policy must bind the inclusive interval")
    if selection.get("duplicates") != "preserved" or selection.get("input_order") != "non_material":
        raise ProspectiveContractError("selection_policy must preserve duplicates and ignore input order")
    if not isinstance(manual, dict) or manual.get("participates_in_fitting") is not False:
        raise ProspectiveContractError("manual context may not participate in v1 fitting")
    if not isinstance(exclusion, dict) or type(exclusion.get("participates_in_fitting")) is not bool:
        raise ProspectiveContractError("exclusion_policy participation must be explicit")
    player_digest, team_digest, training_digest = _training_digests(raw)
    if raw["player_input_digest"] != player_digest:
        raise ProspectiveContractError("player_input_digest does not match training rows")
    if raw["team_input_digest"] != team_digest:
        raise ProspectiveContractError("team_input_digest does not match training rows")
    if raw["training_data_digest"] != training_digest:
        raise ProspectiveContractError("training_data_digest does not match canonical evidence")
    return FrozenJSONMapping(raw)


def build_training_input_evidence(
    normalized_rows: Iterable[Mapping[str, Any]],
    *,
    provider_name: str,
    provider_endpoint_version: str,
    requested_start_date: date | str,
    requested_end_date: date | str,
    selection_policy: Mapping[str, Any] | None = None,
    manual_context_policy: Mapping[str, Any] | None = None,
    exclusion_policy: Mapping[str, Any] | None = None,
) -> FrozenJSONMapping:
    """Return immutable, order-independent evidence for caller-supplied rows."""

    start = _required_date(requested_start_date, "requested_start_date")
    end = _required_date(requested_end_date, "requested_end_date")
    if end < start:
        raise ProspectiveContractError("requested_end_date may not precede requested_start_date")
    if isinstance(normalized_rows, (str, bytes, Mapping)):
        raise ProspectiveContractError("normalized_rows must be an iterable of row mappings")
    try:
        supplied_rows = list(normalized_rows)
    except TypeError as exc:
        raise ProspectiveContractError("normalized_rows must be iterable") from exc
    rows = [
        _canonical_training_row(
            row,
            row_index=index,
            requested_start_date=start,
            requested_end_date=end,
        )
        for index, row in enumerate(supplied_rows)
    ]
    rows.sort(key=_training_row_sort_key)
    supplied_manual_policy = _canonical_policy(
        manual_context_policy, "manual_context_policy"
    )
    if supplied_manual_policy.get("participates_in_fitting") not in (None, False):
        raise ProspectiveContractError("manual context may not participate in v1 fitting")
    content: dict[str, Any] = {
        "schema_version": TRAINING_INPUT_SCHEMA_VERSION,
        "sport": "NBA",
        "league": "NBA",
        "provider_name": _required_text(provider_name, "provider_name"),
        "provider_endpoint_version": _required_text(
            provider_endpoint_version, "provider_endpoint_version"
        ),
        "requested_start_date": start.isoformat(),
        "requested_end_date": end.isoformat(),
        "normalized_row_schema": _normalized_row_schema(),
        "normalized_rows": rows,
        "row_count": len(rows),
        "selection_policy": {
            "row_source": "caller_supplied_normalized_rows",
            "requested_interval": "inclusive",
            "duplicates": "preserved",
            "input_order": "non_material",
            "caller_supplied": _canonical_policy(selection_policy, "selection_policy"),
        },
        "manual_context_policy": {
            "participates_in_fitting": False,
            "caller_supplied": supplied_manual_policy,
        },
        "exclusion_policy": {
            "participates_in_fitting": exclusion_policy is not None,
            "caller_supplied": _canonical_policy(exclusion_policy, "exclusion_policy"),
        },
        "player_input_digest": "",
        "team_input_digest": "",
        "training_data_digest": "",
    }
    player_digest, team_digest, training_digest = _training_digests(content)
    content["player_input_digest"] = player_digest
    content["team_input_digest"] = team_digest
    content["training_data_digest"] = training_digest
    return validate_training_input_evidence(content)


def _feature_schema_content() -> dict[str, Any]:
    return {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "normalized_training_rows": {
            **_normalized_row_schema(),
            "canonical_sort": list(TRAINING_ROW_COLUMNS),
            "duplicate_policy": "preserve every duplicate row",
        },
        "player_baseline": {
            "output_columns": list(PLAYER_BASELINE_COLUMNS),
            "identity_columns": ["player_id", "player_name", "team_abbr"],
            "unique_columns": [["player_id", "player_name", "team_abbr"], ["player_key"]],
            "transformations": {
                "minimum_minutes_inclusive": 8.0,
                "group_by": ["player_id", "player_name", "team_abbr"],
                "observation_sort": ["game_date", "game_id"],
                "recency_half_life_days": 18.0,
                "recency_weight_clip": [0.15, 1.0],
                "recent_window_observations": 5,
                "minute_multiplier": "clip(clip(min,10,40)/24,0.70,1.20)",
                "average": "positive-weight weighted arithmetic mean",
                "standard_deviation": "positive-weight population standard deviation",
                "missing_numeric_result": 0.0,
                "player_key": "strip(player_name).lower() + '__' + strip(team_abbr).upper()",
            },
        },
        "team_baseline": {
            "output_columns": list(TEAM_BASELINE_COLUMNS),
            "identity_columns": ["team_abbr"],
            "unique_columns": [["team_abbr"]],
            "transformations": {
                "game_team_aggregation": {
                    "group_by": ["game_id", "team_abbr"],
                    "game_date": "max",
                    "min": "sum",
                    "stats": {name: "sum" for name in ("pts", "reb", "ast", "stl", "blk", "fg3m")},
                },
                "opponent_allowances": "inner self-join on game_id and exclude equal team_abbr pairs",
                "group_by": ["team_abbr"],
                "observation_sort": ["game_date", "game_id"],
                "recency_half_life_days": 20.0,
                "recency_weight_clip": [0.15, 1.0],
                "recent_window_games": 5,
                "average": "positive-weight weighted arithmetic mean",
                "missing_numeric_result": 0.0,
            },
        },
        "calibration_policy": {
            "mode": "identity_no_calibration",
            "legacy_calibration_inherited": False,
            "calibration_artifact_present": False,
            "future_recalibration": "requires_separately_authorized_design",
        },
        "serialization_policy": {
            "text_encoding": "utf-8",
            "byte_order_mark": False,
            "newline": "LF",
            "csv_dialect": "RFC4180-compatible minimal quoting with doubled quotes",
            "csv_column_order": "exact output_columns order",
            "csv_row_order": "lexicographic canonical serialized identity columns",
            "csv_index": False,
            "csv_null": "empty field",
            "csv_float": "finite IEEE-754 value formatted with .17g; negative zero normalized to zero",
            "json": "sorted keys, UTF-8, no whitespace, JSON primitives, allow_nan=false",
        },
    }


def build_feature_schema_evidence() -> FrozenJSONMapping:
    """Return the immutable combined NBA baseline feature schema and digest."""

    content = _feature_schema_content()
    content["feature_schema_digest"] = canonical_sha256(content)
    return FrozenJSONMapping(content)


def validate_feature_schema_evidence(
    evidence: Mapping[str, Any],
) -> FrozenJSONMapping:
    """Verify a feature-schema evidence object against the authoritative v1 schema."""

    if not isinstance(evidence, Mapping):
        raise ProspectiveContractError("feature-schema evidence must be a mapping")
    raw = FrozenJSONMapping(evidence).to_dict()
    if set(raw) != set(_feature_schema_content()) | {"feature_schema_digest"}:
        raise ProspectiveContractError("feature-schema evidence fields do not match v1")
    claimed = raw.pop("feature_schema_digest")
    if raw != _feature_schema_content():
        raise ProspectiveContractError("feature-schema evidence does not match the authoritative v1 schema")
    expected = canonical_sha256(raw)
    if claimed != expected:
        raise ProspectiveContractError("feature_schema_digest does not match canonical schema")
    raw["feature_schema_digest"] = claimed
    return FrozenJSONMapping(raw)


def derive_verified_model_version(
    *,
    training_start_date: date | str,
    training_end_date: date | str,
    training_data_digest: str,
    feature_schema_version: str,
    feature_schema_digest: str,
    model_build_tool_version: str,
    build_git_provenance: GitProvenanceV1,
    build_configuration_provenance: ConfigurationProvenanceV1,
    model_id: str = MODEL_ID,
) -> str:
    """Derive a deterministic model version from every material build input."""

    start = _required_date(training_start_date, "training_start_date")
    end = _required_date(training_end_date, "training_end_date")
    if end < start:
        raise ProspectiveContractError("training_end_date may not precede training_start_date")
    if model_id != MODEL_ID:
        raise ProspectiveContractError(f"model_id must be {MODEL_ID}")
    if not isinstance(build_git_provenance, GitProvenanceV1):
        raise ProspectiveContractError("build_git_provenance must be GitProvenanceV1")
    if build_git_provenance.dirty:
        raise ProspectiveDirtyTreeError("dirty Git state blocks a verified model build")
    if not isinstance(build_configuration_provenance, ConfigurationProvenanceV1):
        raise ProspectiveContractError(
            "build_configuration_provenance must be ConfigurationProvenanceV1"
        )
    for value, field_name in (
        (training_data_digest, "training_data_digest"),
        (feature_schema_digest, "feature_schema_digest"),
    ):
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ProspectiveContractError(
                f"{field_name} must be 64 lowercase hexadecimal characters"
            )
    if feature_schema_version != FEATURE_SCHEMA_VERSION:
        raise ProspectiveContractError(
            f"feature_schema_version must be {FEATURE_SCHEMA_VERSION}"
        )
    build_key = canonical_sha256(
        {
            "model_id": model_id,
            "training_start_date": start.isoformat(),
            "training_end_date": end.isoformat(),
            "training_data_digest": training_data_digest,
            "feature_schema_version": feature_schema_version,
            "feature_schema_digest": feature_schema_digest,
            "model_build_tool_version": _required_text(
                model_build_tool_version, "model_build_tool_version"
            ),
            "build_git_provenance": build_git_provenance.to_dict(),
            "build_configuration_provenance": build_configuration_provenance.to_dict(),
        }
    )
    return (
        f"nba-baselines-v1-{start:%Y%m%d}-{end:%Y%m%d}-{build_key[:20]}"
    )


def _is_null_output(value: object) -> bool:
    return value is None or value is pd.NA


def _serialize_float(value: numbers.Real, field_name: str) -> str:
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ModelBuildSerializationError(f"{field_name} contains a non-finite value")
    if normalized == 0.0:
        return "0"
    return format(normalized, ".17g")


def _serialize_output_cell(value: object, *, column: str, row_index: int) -> str:
    field_name = f"{column} at output row {row_index}"
    if _is_null_output(value):
        return ""
    if isinstance(value, bool):
        raise ModelBuildSerializationError(f"{field_name} may not be boolean")
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        return _serialize_float(value, field_name)
    if isinstance(value, str):
        if "\r" in value or "\n" in value:
            raise ModelBuildSerializationError(f"{field_name} may not contain newlines")
        return value
    raise ModelBuildSerializationError(
        f"{field_name} has unsupported type {type(value).__name__}"
    )


def _canonical_csv_bytes(columns: tuple[str, ...], rows: list[list[str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(
        output,
        delimiter=",",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
        doublequote=True,
    )
    writer.writerow(columns)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def serialize_baseline_frame(frame: pd.DataFrame, *, kind: str) -> bytes:
    """Validate and serialize a player or team baseline frame deterministically."""

    if not isinstance(frame, pd.DataFrame):
        raise ModelBuildSerializationError(f"{kind} builder must return a pandas DataFrame")
    if kind == "player":
        columns = PLAYER_BASELINE_COLUMNS
        identity_indexes = tuple(columns.index(name) for name in ("player_id", "player_name", "team_abbr"))
        player_key_index = columns.index("player_key")
    elif kind == "team":
        columns = TEAM_BASELINE_COLUMNS
        identity_indexes = (0,)
        player_key_index = None
    else:
        raise ModelBuildSerializationError("baseline kind must be player or team")
    actual_columns = tuple(str(column) for column in frame.columns)
    if actual_columns != columns:
        raise ModelBuildSerializationError(
            f"{kind} baseline columns must exactly match the required order"
        )
    rows: list[list[str]] = []
    identities: set[tuple[str, ...]] = set()
    player_keys: set[str] = set()
    games_index = columns.index("games")
    for row_index, values in enumerate(frame.itertuples(index=False, name=None)):
        for column, value in zip(columns, values, strict=True):
            if column == "games":
                if isinstance(value, bool) or not isinstance(value, numbers.Integral):
                    raise ModelBuildSerializationError(
                        f"{kind} baseline games must be a non-negative integer"
                    )
            elif column in {"player_name", "team_abbr", "player_key"}:
                if not isinstance(value, str):
                    raise ModelBuildSerializationError(
                        f"{kind} baseline {column} must be text"
                    )
                if value != value.strip():
                    raise ModelBuildSerializationError(
                        f"{kind} baseline {column} must be trimmed text"
                    )
                if column == "team_abbr" and value != value.upper():
                    raise ModelBuildSerializationError(
                        f"{kind} baseline team_abbr must be uppercase"
                    )
            elif column == "player_id":
                if isinstance(value, bool) or not isinstance(
                    value, (str, numbers.Integral)
                ):
                    raise ModelBuildSerializationError(
                        "player baseline player_id must be a string or integer identifier"
                    )
            elif not _is_null_output(value) and (
                isinstance(value, bool) or not isinstance(value, numbers.Real)
            ):
                raise ModelBuildSerializationError(
                    f"{kind} baseline {column} must be numeric or null"
                )
        serialized = [
            _serialize_output_cell(value, column=column, row_index=row_index)
            for column, value in zip(columns, values, strict=True)
        ]
        identity = tuple(serialized[index] for index in identity_indexes)
        if any(not value for value in identity):
            raise ModelBuildSerializationError(f"{kind} baseline identity fields may not be null or empty")
        if identity in identities:
            raise ModelBuildSerializationError(f"{kind} baseline contains a duplicate identity row")
        identities.add(identity)
        games = serialized[games_index]
        if not games or re.fullmatch(r"0|[1-9][0-9]*", games) is None:
            raise ModelBuildSerializationError(f"{kind} baseline games must be a non-negative integer")
        for index, column in enumerate(columns):
            if index in identity_indexes or column == "player_key" or column == "games":
                continue
            value = serialized[index]
            if value:
                try:
                    numeric = float(value)
                except ValueError as exc:
                    raise ModelBuildSerializationError(
                        f"{kind} baseline {column} must be numeric or null"
                    ) from exc
                if not math.isfinite(numeric):
                    raise ModelBuildSerializationError(
                        f"{kind} baseline {column} contains a non-finite value"
                    )
        if player_key_index is not None:
            player_key = serialized[player_key_index]
            expected_key = f"{identity[1].strip().lower()}__{identity[2].strip().upper()}"
            if player_key != expected_key:
                raise ModelBuildSerializationError("player_key does not match the canonical v1 rule")
            if player_key in player_keys:
                raise ModelBuildSerializationError("player baseline contains a duplicate player_key")
            player_keys.add(player_key)
        rows.append(serialized)
    rows.sort(key=lambda row: tuple(row[index] for index in identity_indexes))
    return _canonical_csv_bytes(columns, rows)


def validate_baseline_csv_bytes(data: bytes, *, kind: str) -> bytes:
    """Strictly parse and canonicalize persisted baseline CSV bytes."""

    if not isinstance(data, bytes):
        raise ModelBuildSerializationError("baseline CSV content must be bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ModelBuildSerializationError("baseline CSV must be valid UTF-8") from exc
    if "\r" in text or not text.endswith("\n"):
        raise ModelBuildSerializationError("baseline CSV must use LF newlines and end with LF")
    try:
        parsed = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise ModelBuildSerializationError("baseline CSV is malformed") from exc
    columns = PLAYER_BASELINE_COLUMNS if kind == "player" else TEAM_BASELINE_COLUMNS
    if not parsed or tuple(parsed[0]) != columns:
        raise ModelBuildSerializationError(f"{kind} baseline header is invalid")
    typed_rows: list[dict[str, object]] = []
    identity_columns = (
        {"player_id", "player_name", "team_abbr", "player_key"}
        if kind == "player"
        else {"team_abbr"}
    )
    for row_index, values in enumerate(parsed[1:]):
        if len(values) != len(columns):
            raise ModelBuildSerializationError(f"{kind} baseline row width is invalid")
        row: dict[str, object] = {}
        for column, value in zip(columns, values, strict=True):
            if column in identity_columns:
                row[column] = value
            elif column == "games":
                if re.fullmatch(r"0|[1-9][0-9]*", value) is None:
                    raise ModelBuildSerializationError(f"{kind} baseline games is invalid")
                row[column] = int(value)
            elif value == "":
                row[column] = None
            else:
                try:
                    numeric = float(value)
                except ValueError as exc:
                    raise ModelBuildSerializationError(
                        f"{kind} baseline numeric value is invalid at row {row_index}"
                    ) from exc
                if not math.isfinite(numeric):
                    raise ModelBuildSerializationError(
                        f"{kind} baseline numeric value is non-finite at row {row_index}"
                    )
                row[column] = numeric
        typed_rows.append(row)
    canonical = serialize_baseline_frame(pd.DataFrame(typed_rows, columns=columns), kind=kind)
    if canonical != data:
        raise ModelBuildSerializationError(f"{kind} baseline CSV is not canonical")
    return data


def _configuration_provenance(
    value: Mapping[str, Any] | ConfigurationProvenanceV1,
) -> ConfigurationProvenanceV1:
    if isinstance(value, ConfigurationProvenanceV1):
        return value
    if not isinstance(value, Mapping):
        raise ProspectiveContractError("build_configuration must be a mapping")
    return ConfigurationProvenanceV1.from_configuration(value)


def _training_run_id(
    supplied: str | None,
    factory: Callable[[], str] | None,
) -> str:
    if supplied is not None and factory is not None:
        raise ProspectiveContractError(
            "supply either training_run_id or training_run_id_factory, not both"
        )
    value = supplied if supplied is not None else (factory or (lambda: uuid4().hex))()
    if not isinstance(value, str) or _SAFE_RUN_ID.fullmatch(value) is None:
        raise ProspectiveContractError(
            "training_run_id must be a filesystem-safe 1-128 character token"
        )
    return value


def create_verified_model_build(
    *,
    normalized_rows: Iterable[Mapping[str, Any]],
    provider_name: str,
    provider_endpoint_version: str,
    requested_start_date: date | str,
    requested_end_date: date | str,
    build_configuration: Mapping[str, Any] | ConfigurationProvenanceV1,
    build_git_provenance: GitProvenanceV1,
    model_build_tool_version: str,
    player_baseline_builder: Callable[[pd.DataFrame], pd.DataFrame],
    team_baseline_builder: Callable[[pd.DataFrame], pd.DataFrame],
    repository_root: str | Path,
    output_root: str | Path,
    training_run_id: str | None = None,
    training_run_id_factory: Callable[[], str] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    selection_policy: Mapping[str, Any] | None = None,
    manual_context_policy: Mapping[str, Any] | None = None,
    exclusion_policy: Mapping[str, Any] | None = None,
    force_overwrite: bool = False,
) -> VerifiedModelBuild:
    """Build and create-once publish an immutable verified NBA baseline build."""

    if force_overwrite is not False:
        raise ProspectiveContractError("force overwrite is forbidden for verified model builds")
    if not callable(player_baseline_builder) or not callable(team_baseline_builder):
        raise ProspectiveContractError("player and team baseline builders must be callable")
    if not callable(clock):
        raise ProspectiveContractError("clock must be callable")
    configuration = _configuration_provenance(build_configuration)
    evidence = build_training_input_evidence(
        normalized_rows,
        provider_name=provider_name,
        provider_endpoint_version=provider_endpoint_version,
        requested_start_date=requested_start_date,
        requested_end_date=requested_end_date,
        selection_policy=selection_policy,
        manual_context_policy=manual_context_policy,
        exclusion_policy=exclusion_policy,
    )
    feature_evidence = build_feature_schema_evidence()
    start = _required_date(requested_start_date, "requested_start_date")
    end = _required_date(requested_end_date, "requested_end_date")
    model_version = derive_verified_model_version(
        training_start_date=start,
        training_end_date=end,
        training_data_digest=evidence["training_data_digest"],
        feature_schema_version=feature_evidence["feature_schema_version"],
        feature_schema_digest=feature_evidence["feature_schema_digest"],
        model_build_tool_version=model_build_tool_version,
        build_git_provenance=build_git_provenance,
        build_configuration_provenance=configuration,
    )
    run_id = _training_run_id(training_run_id, training_run_id_factory)
    rows_frame = pd.DataFrame(
        evidence.to_dict()["normalized_rows"], columns=TRAINING_ROW_COLUMNS
    )
    try:
        player_frame = player_baseline_builder(rows_frame.copy(deep=True))
    except Exception as exc:
        raise ModelBuildBuilderError("player baseline builder failed") from exc
    try:
        team_frame = team_baseline_builder(rows_frame.copy(deep=True))
    except Exception as exc:
        raise ModelBuildBuilderError("team baseline builder failed") from exc
    player_bytes = serialize_baseline_frame(player_frame, kind="player")
    team_bytes = serialize_baseline_frame(team_frame, kind="team")

    from courtvision.prospective.model_manifest_io import publish_verified_model_build

    return publish_verified_model_build(
        repository_root=repository_root,
        output_root=output_root,
        model_version=model_version,
        training_run_id=run_id,
        training_start_date=start,
        training_end_date=end,
        training_input_evidence=evidence,
        feature_schema_evidence=feature_evidence,
        player_baseline_bytes=player_bytes,
        team_baseline_bytes=team_bytes,
        build_git_provenance=build_git_provenance,
        build_configuration_provenance=configuration,
        model_build_tool_version=_required_text(
            model_build_tool_version, "model_build_tool_version"
        ),
        clock=clock,
    )


__all__ = [
    "FEATURE_SCHEMA_VERSION",
    "MODEL_ID",
    "PLAYER_BASELINE_COLUMNS",
    "TEAM_BASELINE_COLUMNS",
    "TRAINING_INPUT_SCHEMA_VERSION",
    "TRAINING_ROW_COLUMNS",
    "ModelBuildBuilderError",
    "ModelBuildSerializationError",
    "VerifiedModelBuildError",
    "build_feature_schema_evidence",
    "build_training_input_evidence",
    "create_verified_model_build",
    "derive_verified_model_version",
]
