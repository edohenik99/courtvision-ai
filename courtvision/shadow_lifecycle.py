"""Feature-flag boundary for optional lifecycle shadow publication."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
import os
from typing import Any, Callable, Mapping


LIFECYCLE_SHADOW_ENV = "COURTVISION_LIFECYCLE_SHADOW"
LIFECYCLE_OBSERVATIONS_ENV = "COURTVISION_LIFECYCLE_OBSERVATIONS"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class ShadowLifecycleInitializationError(RuntimeError):
    """Classified failure to load enabled lifecycle shadow infrastructure."""

    status = "DEGRADED"
    classification = "LIFECYCLE_IMPORT_FAILURE"

    def __init__(self, cause: BaseException) -> None:
        self.cause_type = type(cause).__name__
        super().__init__(
            "enabled lifecycle shadow publication could not be imported "
            f"({self.cause_type})"
        )


@dataclass(frozen=True, slots=True)
class ShadowLifecycleHooks:
    """Callable publication surface loaded only for enabled shadow mode."""

    begin_shadow_run: Callable[..., Any]
    publish_shadow_after_board: Callable[..., Any]
    record_failed_shadow_run: Callable[..., Any]
    observations_enabled: bool = False
    prepare_observation_batch: Callable[..., Any] | None = None
    observation_initialization_error: str | None = None


def lifecycle_shadow_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether the opt-in lifecycle shadow feature is enabled."""

    source = os.environ if environ is None else environ
    return (
        str(source.get(LIFECYCLE_SHADOW_ENV, "")).strip().lower()
        in _TRUE_VALUES
    )


def lifecycle_observations_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether the separately gated Phase 3 observer is enabled."""

    source = os.environ if environ is None else environ
    return (
        str(source.get(LIFECYCLE_OBSERVATIONS_ENV, "")).strip().lower()
        in _TRUE_VALUES
    )


def load_shadow_lifecycle_hooks(
    environ: Mapping[str, str] | None = None,
) -> ShadowLifecycleHooks | None:
    """Load lifecycle publication only after the feature flag evaluates true."""

    if not lifecycle_shadow_enabled(environ):
        return None
    try:
        publication = import_module("courtvision.lifecycle.publication")
        observations_enabled = lifecycle_observations_enabled(environ)
        prepare_observation_batch = None
        observation_initialization_error = None
        if observations_enabled:
            try:
                observations = import_module("courtvision.lifecycle.observations")
                prepare_observation_batch = observations.prepare_observation_batch
            except Exception as observation_exc:
                observation_initialization_error = (
                    "enabled lifecycle observations could not be imported "
                    f"({type(observation_exc).__name__})"
                )
        return ShadowLifecycleHooks(
            begin_shadow_run=publication.begin_shadow_run,
            publish_shadow_after_board=publication.publish_shadow_after_board,
            record_failed_shadow_run=publication.record_failed_shadow_run,
            observations_enabled=observations_enabled,
            prepare_observation_batch=prepare_observation_batch,
            observation_initialization_error=observation_initialization_error,
        )
    except Exception as exc:
        raise ShadowLifecycleInitializationError(exc) from exc


__all__ = [
    "LIFECYCLE_OBSERVATIONS_ENV",
    "LIFECYCLE_SHADOW_ENV",
    "ShadowLifecycleHooks",
    "ShadowLifecycleInitializationError",
    "lifecycle_observations_enabled",
    "lifecycle_shadow_enabled",
    "load_shadow_lifecycle_hooks",
]
