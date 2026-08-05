"""Narrow NBA training adapter for immutable verified baseline builds.

The adapter normalizes already-fetched BallDontLie rows, binds the material CLI
configuration, and delegates all identity, serialization, replay, locking, and
publication behavior to the existing prospective model-build core.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from importlib import metadata
import json
import os
import platform
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pandas as pd

from courtvision.data.normalization import normalize_stats_frame
from courtvision.prospective.contracts import (
    ConfigurationProvenanceV1,
    GitProvenanceV1,
)
from courtvision.prospective.model_build import (
    TRAINING_ROW_COLUMNS,
    build_feature_schema_evidence,
    build_training_input_evidence,
    create_verified_model_build,
    derive_verified_model_version,
)
from courtvision.prospective.model_manifest_io import (
    VerifiedModelBuild,
    load_verified_model_build,
)
from courtvision.prospective.provenance import capture_configuration_provenance


NBA_VERIFIED_BUILD_CLI_VERSION = "courtvision-nba-verified-build-cli-v1"
BALLDONTLIE_PROVIDER_NAME = "balldontlie"
BALLDONTLIE_STATS_ENDPOINT_VERSION = "nba-v1-stats"
COURTVISION_SOURCE_VERSION = "0.1.0"


class VerifiedNBATrainingDataError(ValueError):
    """Raised when fetched NBA training rows are empty or unusable."""


def _plain_scalar(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    item = getattr(value, "item", None)
    return item() if callable(item) else value


def normalize_nba_verified_training_rows(
    raw_stats: pd.DataFrame,
) -> list[dict[str, object]]:
    """Apply the production NBA normalizer and return the strict v1 row shape."""

    if not isinstance(raw_stats, pd.DataFrame) or raw_stats.empty:
        raise VerifiedNBATrainingDataError(
            "BallDontLie returned no NBA training rows"
        )
    try:
        normalized = normalize_stats_frame(raw_stats)
    except Exception as exc:
        raise VerifiedNBATrainingDataError(
            "BallDontLie NBA training rows could not be normalized"
        ) from exc
    if normalized.empty:
        raise VerifiedNBATrainingDataError(
            "BallDontLie NBA training rows were unusable after normalization"
        )
    if len(normalized) != len(raw_stats):
        raise VerifiedNBATrainingDataError(
            "BallDontLie NBA training rows lost required identities during normalization"
        )

    missing = set(TRAINING_ROW_COLUMNS) - set(normalized.columns)
    if missing:
        raise VerifiedNBATrainingDataError(
            "normalized NBA training rows do not match the verified-build schema"
        )

    rows: list[dict[str, object]] = []
    for row_index, row in normalized.iterrows():
        game_date = row["game_date"]
        if pd.isna(game_date):
            raise VerifiedNBATrainingDataError(
                f"normalized NBA training row {row_index} has no valid game date"
            )
        try:
            game_date_text = pd.Timestamp(game_date).date().isoformat()
        except (TypeError, ValueError, OverflowError) as exc:
            raise VerifiedNBATrainingDataError(
                f"normalized NBA training row {row_index} has an invalid game date"
            ) from exc

        normalized_row: dict[str, object] = {
            column: _plain_scalar(row[column]) for column in TRAINING_ROW_COLUMNS
        }
        normalized_row["game_date"] = game_date_text
        rows.append(normalized_row)
    return rows


def build_nba_verified_configuration(
    *,
    requested_start_date: date,
    requested_end_date: date,
    provider_base_url: str,
    request_timeout_seconds: int,
    retry_total: int,
    retry_backoff_seconds: float,
) -> ConfigurationProvenanceV1:
    """Construct credential-free canonical configuration for the build."""

    parsed_url = urlsplit(provider_base_url)
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.netloc
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ValueError("verified NBA provider endpoint configuration is invalid")

    configuration = {
        "schema_version": 1,
        "command_contract": NBA_VERIFIED_BUILD_CLI_VERSION,
        "sport": "nba",
        "mode": "production",
        "training_interval": {
            "start_date": requested_start_date.isoformat(),
            "end_date": requested_end_date.isoformat(),
            "inclusive": True,
        },
        "provider": {
            "name": BALLDONTLIE_PROVIDER_NAME,
            "base_url": provider_base_url,
            "stats_endpoint": "/stats",
            "endpoint_version": BALLDONTLIE_STATS_ENDPOINT_VERSION,
            "page_size": 100,
            "request_timeout_seconds": request_timeout_seconds,
            "retry_total": retry_total,
            "retry_backoff_seconds": retry_backoff_seconds,
            "retry_status_codes": [429, 500, 502, 503, 504],
            "retry_allowed_methods": ["GET", "POST"],
        },
        "normalization": {
            "implementation": (
                "courtvision.data.normalization.normalize_stats_frame"
            ),
            "invalid_identity_rows": "fail_closed",
        },
        "player_baseline_policy": {
            "minimum_minutes_inclusive": 8.0,
            "recency_half_life_days": 18.0,
            "recent_window_observations": 5,
            "minute_multiplier": "clip(clip(min,10,40)/24,0.70,1.20)",
        },
        "team_baseline_policy": {
            "game_team_aggregation": True,
            "opponent_self_join": "game_id",
            "recency_half_life_days": 20.0,
            "recent_window_observations": 5,
        },
        "serialization": {
            "csv_encoding": "utf-8",
            "csv_newlines": "lf",
            "csv_index": False,
            "float_format": ".17g",
            "json": "canonical-sorted-key-utf-8",
        },
        "calibration_policy": "identity_no_calibration",
        "publication": {
            "layout_under_output_root": "model/verified_builds/<model_version>",
            "immutable": True,
            "force_overwrite": False,
        },
    }
    return capture_configuration_provenance(configuration)


def build_nba_verified_tool_version() -> str:
    """Return stable toolchain identity without Git or environment content."""

    try:
        courtvision_version = metadata.version("courtvision")
    except metadata.PackageNotFoundError:
        courtvision_version = COURTVISION_SOURCE_VERSION
    return json.dumps(
        {
            "verified_builder_schema": NBA_VERIFIED_BUILD_CLI_VERSION,
            "courtvision": courtvision_version,
            "python": platform.python_version(),
            "pandas": pd.__version__,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _verified_destination(
    *,
    repository_root: Path,
    output_root: str | Path,
    model_version: str,
) -> Path | None:
    supplied_output = Path(output_root)
    candidate = (
        supplied_output
        if supplied_output.is_absolute()
        else repository_root / supplied_output
    )
    try:
        resolved_output = candidate.resolve(strict=False)
        resolved_output.relative_to(repository_root)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved_output / "model" / "verified_builds" / model_version


def _replay_clock(existing: VerifiedModelBuild) -> Callable[[], datetime]:
    completed = existing.manifest.training.training_completed_at_utc
    created = existing.manifest.created_at_utc
    values = iter((completed, created))

    def clock() -> datetime:
        return next(values, created)

    return clock


def create_nba_verified_model_build(
    *,
    raw_stats: pd.DataFrame,
    requested_start_date: date,
    requested_end_date: date,
    build_configuration: ConfigurationProvenanceV1,
    build_git_provenance: GitProvenanceV1,
    model_build_tool_version: str,
    player_baseline_builder: Callable[[pd.DataFrame], pd.DataFrame],
    team_baseline_builder: Callable[[pd.DataFrame], pd.DataFrame],
    repository_root: str | Path,
    output_root: str | Path,
    clock: Callable[[], datetime] | None = None,
    training_run_id: str | None = None,
) -> VerifiedModelBuild:
    """Normalize fetched rows and delegate the complete build to the core."""

    normalized_rows = normalize_nba_verified_training_rows(raw_stats)
    training_evidence = build_training_input_evidence(
        normalized_rows,
        provider_name=BALLDONTLIE_PROVIDER_NAME,
        provider_endpoint_version=BALLDONTLIE_STATS_ENDPOINT_VERSION,
        requested_start_date=requested_start_date,
        requested_end_date=requested_end_date,
        selection_policy={
            "normalizer": "courtvision.data.normalization.normalize_stats_frame",
            "invalid_identity_rows": "fail_closed",
        },
        manual_context_policy={"source": "none"},
    )
    feature_evidence = build_feature_schema_evidence()
    model_version = derive_verified_model_version(
        training_start_date=requested_start_date,
        training_end_date=requested_end_date,
        training_data_digest=training_evidence["training_data_digest"],
        feature_schema_version=feature_evidence["feature_schema_version"],
        feature_schema_digest=feature_evidence["feature_schema_digest"],
        model_build_tool_version=model_build_tool_version,
        build_git_provenance=build_git_provenance,
        build_configuration_provenance=build_configuration,
    )

    root = Path(repository_root).resolve(strict=True)
    destination = _verified_destination(
        repository_root=root,
        output_root=output_root,
        model_version=model_version,
    )
    build_clock = clock or (lambda: datetime.now(UTC))
    run_id = training_run_id
    if destination is not None and os.path.lexists(destination):
        existing = load_verified_model_build(destination, repository_root=root)
        build_clock = _replay_clock(existing)
        run_id = existing.manifest.training.training_run_id

    return create_verified_model_build(
        normalized_rows=normalized_rows,
        provider_name=BALLDONTLIE_PROVIDER_NAME,
        provider_endpoint_version=BALLDONTLIE_STATS_ENDPOINT_VERSION,
        requested_start_date=requested_start_date,
        requested_end_date=requested_end_date,
        build_configuration=build_configuration,
        build_git_provenance=build_git_provenance,
        model_build_tool_version=model_build_tool_version,
        player_baseline_builder=player_baseline_builder,
        team_baseline_builder=team_baseline_builder,
        repository_root=root,
        output_root=output_root,
        training_run_id=run_id,
        clock=build_clock,
        selection_policy={
            "normalizer": "courtvision.data.normalization.normalize_stats_frame",
            "invalid_identity_rows": "fail_closed",
        },
        manual_context_policy={"source": "none"},
    )


__all__ = [
    "BALLDONTLIE_PROVIDER_NAME",
    "BALLDONTLIE_STATS_ENDPOINT_VERSION",
    "NBA_VERIFIED_BUILD_CLI_VERSION",
    "VerifiedNBATrainingDataError",
    "build_nba_verified_configuration",
    "build_nba_verified_tool_version",
    "create_nba_verified_model_build",
    "normalize_nba_verified_training_rows",
]
