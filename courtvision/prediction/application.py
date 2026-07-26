"""Canonical prediction application service."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
from datetime import date, datetime, timezone
import errno
import json
import os
from pathlib import Path
import socket
import sys
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from courtvision.prediction.contracts import (
    EnginePrediction,
    PredictionRequest,
    PredictionResult,
)
from courtvision.prediction.publication import (
    NoArtifactPublisher,
    PredictionPublisher,
    PublicationOutcome,
    stage_application_manifest,
)
from courtvision.prediction.registry import PredictionEngineRegistry


class PredictionRequestError(ValueError):
    """Raised before engine execution when a request is invalid."""


class PredictionRunConflictError(RuntimeError):
    """Raised when another process owns the same sport/date run lock."""


PREDICTION_LOCK_STALE_SECONDS_ENV = (
    "COURTVISION_PREDICTION_LOCK_STALE_SECONDS"
)
DEFAULT_PREDICTION_LOCK_STALE_SECONDS = 6 * 60 * 60


@dataclass(slots=True)
class LifecycleRunState:
    status: str
    context: Any = None
    hooks: Any = None
    error: str | None = None
    observation_state: dict[str, Any] | None = None


class PredictionLifecycle(Protocol):
    def begin(
        self,
        request: PredictionRequest,
        run_id: str,
        engine: Any,
    ) -> LifecycleRunState:
        """Initialize lifecycle state before prediction execution."""

    def complete(
        self,
        state: LifecycleRunState,
        request: PredictionRequest,
        publication: PublicationOutcome,
    ) -> str:
        """Commit the terminal lifecycle result."""

    def fail(
        self,
        state: LifecycleRunState,
        error: BaseException,
    ) -> str:
        """Record an engine or publication failure."""


class DisabledPredictionLifecycle:
    def begin(
        self,
        request: PredictionRequest,
        run_id: str,
        engine: Any,
    ) -> LifecycleRunState:
        return LifecycleRunState(status="DISABLED")

    def complete(
        self,
        state: LifecycleRunState,
        request: PredictionRequest,
        publication: PublicationOutcome,
    ) -> str:
        return state.status

    def fail(
        self,
        state: LifecycleRunState,
        error: BaseException,
    ) -> str:
        return state.status


class ShadowPredictionLifecycle:
    """Adapter over the opt-in immutable lifecycle ledger."""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        hooks_loader: Callable[[], Any] | None = None,
        lifecycle_root: str | Path | None = None,
    ) -> None:
        self.repository_root = Path(repository_root)
        self._hooks_loader = hooks_loader
        self.lifecycle_root = Path(lifecycle_root) if lifecycle_root else None

    def _load_hooks(self) -> Any:
        if self._hooks_loader is not None:
            return self._hooks_loader()
        from courtvision.shadow_lifecycle import load_shadow_lifecycle_hooks

        return load_shadow_lifecycle_hooks()

    def begin(
        self,
        request: PredictionRequest,
        run_id: str,
        engine: Any,
    ) -> LifecycleRunState:
        try:
            hooks = self._load_hooks()
            if hooks is None:
                return LifecycleRunState(status="DISABLED")
            runtime = getattr(engine, "runtime", engine)
            entrypoint = str(
                request.metadata.get("entrypoint", "prediction_application")
            )
            command = str(
                request.metadata.get(
                    "command",
                    f"{entrypoint} predict --sport {request.sport} "
                    f"--mode {request.mode}",
                )
            )
            context = hooks.begin_shadow_run(
                runtime,
                repository_root=self.repository_root,
                prediction_date=request.prediction_date,
                verbose_outputs=bool(
                    request.metadata.get("verbose_outputs", False)
                ),
                force_output_overwrite=request.force_overwrite,
                lifecycle_root=self.lifecycle_root,
                run_id=run_id,
                entrypoint=entrypoint,
                sport=request.sport,
                mode=request.mode,
                actor_id="prediction_application",
                command=command,
                request_metadata=dict(request.metadata),
            )
            if context is None:
                return LifecycleRunState(status="DISABLED", hooks=hooks)
            observation_state: dict[str, Any] = {
                "enabled": bool(
                    getattr(hooks, "observations_enabled", False)
                    and request.sport == "nba"
                ),
                "batch": None,
                "error": getattr(
                    hooks, "observation_initialization_error", None
                ),
            }
            prepare = getattr(hooks, "prepare_observation_batch", None)
            if observation_state["enabled"] and callable(prepare):
                def capture(**source_values: Any) -> None:
                    try:
                        observation_state["batch"] = prepare(
                            prediction_run_id=run_id,
                            clock=context.clock,
                            **source_values,
                        )
                    except Exception as exc:
                        observation_state["error"] = (
                            f"{type(exc).__name__}: {str(exc)[:500]}"
                        )

                setattr(runtime, "_shadow_lifecycle_observer", capture)
            print(
                f"[PREDICTION_LIFECYCLE] status=STARTED "
                f"run_id={run_id} sport={request.sport} mode={request.mode}",
                flush=True,
            )
            return LifecycleRunState(
                status="STARTED",
                context=context,
                hooks=hooks,
                observation_state=observation_state,
            )
        except Exception as exc:
            classification = getattr(
                exc,
                "classification",
                "LIFECYCLE_INITIALIZATION_FAILURE",
            )
            error_type = getattr(exc, "cause_type", type(exc).__name__)
            print(
                "[PREDICTION_LIFECYCLE] status=DEGRADED "
                f"stage=INITIALIZATION classification={classification} "
                f"error_type={error_type}",
                file=sys.stderr,
                flush=True,
            )
            return LifecycleRunState(
                status="DEGRADED",
                error=f"{type(exc).__name__}: {exc}",
            )

    def complete(
        self,
        state: LifecycleRunState,
        request: PredictionRequest,
        publication: PublicationOutcome,
    ) -> str:
        if state.context is None or state.hooks is None:
            return state.status
        primary_label = publication.primary_artifact_label
        primary_path = (
            publication.artifact_paths.get(primary_label)
            if primary_label
            else None
        )
        if not primary_path:
            return "DEGRADED"
        try:
            observation = state.observation_state or {}
            kwargs: dict[str, Any] = {"board_path": primary_path}
            if observation.get("enabled"):
                kwargs.update(
                    {
                        "observations_enabled": True,
                        "observation_batch": observation.get("batch"),
                        "observation_capture_error": observation.get("error"),
                    }
                )
            result = state.hooks.publish_shadow_after_board(
                state.context,
                **kwargs,
            )
            return str(result.status)
        except Exception as exc:
            state.error = f"{type(exc).__name__}: {exc}"
            return "DEGRADED"

    def fail(
        self,
        state: LifecycleRunState,
        error: BaseException,
    ) -> str:
        if state.context is None or state.hooks is None:
            return state.status
        try:
            state.hooks.record_failed_shadow_run(state.context, error)
            return "FAILED_RECORDED"
        except Exception as exc:
            state.error = f"{type(exc).__name__}: {exc}"
            return "DEGRADED"


class PredictionRunLock:
    """Create-exclusive lock with conservative, claimed stale recovery.

    Same-host locks are reclaimed only when their recorded PID is
    demonstrably dead. Corrupt metadata must exceed
    ``COURTVISION_PREDICTION_LOCK_STALE_SECONDS`` (six hours by default).
    Valid cross-host locks require operator recovery because their process
    liveness cannot be established locally.
    """

    def __init__(
        self,
        path: Path,
        *,
        run_id: str,
        sport: str = "unknown",
        mode: str = "unknown",
        prediction_date: str = "unknown",
        stale_after_seconds: float | None = None,
        hostname: str | None = None,
    ) -> None:
        self.path = path
        self.run_id = run_id
        self.sport = str(sport)
        self.mode = str(mode)
        self.prediction_date = str(prediction_date)
        self.hostname = str(hostname or socket.gethostname())
        self.stale_after_seconds = (
            float(stale_after_seconds)
            if stale_after_seconds is not None
            else self._configured_stale_seconds()
        )
        if self.stale_after_seconds <= 0:
            raise ValueError("prediction lock stale threshold must be positive")
        self._owner_token = uuid4().hex
        self._fd: int | None = None

    @staticmethod
    def _configured_stale_seconds() -> float:
        raw = os.environ.get(PREDICTION_LOCK_STALE_SECONDS_ENV, "").strip()
        if not raw:
            return float(DEFAULT_PREDICTION_LOCK_STALE_SECONDS)
        try:
            value = float(raw)
        except ValueError:
            return float(DEFAULT_PREDICTION_LOCK_STALE_SECONDS)
        return (
            value
            if value > 0
            else float(DEFAULT_PREDICTION_LOCK_STALE_SECONDS)
        )

    @property
    def _reclamation_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.reclaim")

    def _metadata(self, *, owner_token: str, role: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "role": role,
            "owner_token": owner_token,
            "run_id": self.run_id,
            "pid": os.getpid(),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "hostname": self.hostname,
            "sport": self.sport,
            "mode": self.mode,
            "prediction_date": self.prediction_date,
        }

    def _create_owned_file(
        self,
        path: Path,
        *,
        owner_token: str,
        role: str,
    ) -> int:
        fd = os.open(
            str(path),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
        payload = (
            json.dumps(
                self._metadata(owner_token=owner_token, role=role),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        try:
            written = 0
            while written < len(payload):
                count = os.write(fd, payload[written:])
                if count <= 0:
                    raise OSError("short write while creating prediction lock")
                written += count
            os.fsync(fd)
        except Exception:
            os.close(fd)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise
        return fd

    @staticmethod
    def _read_metadata(
        path: Path,
    ) -> tuple[dict[str, Any] | None, os.stat_result | None]:
        try:
            snapshot = path.stat()
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            try:
                return None, path.stat()
            except OSError:
                return None, None
        return (
            payload if isinstance(payload, dict) else None,
            snapshot,
        )

    @staticmethod
    def _structured_owner(
        metadata: Mapping[str, Any] | None,
    ) -> tuple[int, str] | None:
        if metadata is None:
            return None
        pid = metadata.get("pid")
        hostname = metadata.get("hostname")
        required_text = (
            metadata.get("run_id"),
            metadata.get("created_at_utc"),
            hostname,
            metadata.get("sport"),
            metadata.get("mode"),
            metadata.get("prediction_date"),
            metadata.get("owner_token"),
        )
        if (
            isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid <= 0
            or any(not isinstance(value, str) or not value.strip()
                   for value in required_text)
        ):
            return None
        try:
            created = datetime.fromisoformat(
                str(metadata["created_at_utc"])
            )
            date.fromisoformat(str(metadata["prediction_date"]))
        except ValueError:
            return None
        if created.tzinfo is None:
            return None
        return pid, str(hostname)

    @staticmethod
    def _process_is_alive(pid: int) -> bool | None:
        if pid == os.getpid():
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError as exc:
            if exc.errno in {errno.ESRCH, errno.EINVAL}:
                return False
            if getattr(exc, "winerror", None) in {87, 1168}:
                return False
            if exc.errno == errno.EPERM:
                return True
            return None
        return True

    def _stale_snapshot(self, path: Path) -> os.stat_result | None:
        metadata, snapshot = self._read_metadata(path)
        if snapshot is None:
            return None
        owner = self._structured_owner(metadata)
        if owner is not None:
            pid, hostname = owner
            if hostname.casefold() != self.hostname.casefold():
                return None
            return (
                snapshot
                if self._process_is_alive(pid) is False
                else None
            )
        age_seconds = max(
            0.0,
            datetime.now(timezone.utc).timestamp() - snapshot.st_mtime,
        )
        return (
            snapshot
            if age_seconds >= self.stale_after_seconds
            else None
        )

    @staticmethod
    def _same_snapshot(
        left: os.stat_result,
        right: os.stat_result,
    ) -> bool:
        return (
            left.st_dev,
            left.st_ino,
            left.st_size,
            left.st_mtime_ns,
        ) == (
            right.st_dev,
            right.st_ino,
            right.st_size,
            right.st_mtime_ns,
        )

    def _unlink_if_unchanged(
        self,
        path: Path,
        snapshot: os.stat_result,
    ) -> bool:
        try:
            current = path.stat()
            if not self._same_snapshot(snapshot, current):
                return False
            path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def _remove_if_owned(self, path: Path, owner_token: str) -> None:
        metadata, snapshot = self._read_metadata(path)
        if (
            snapshot is None
            or metadata is None
            or metadata.get("owner_token") != owner_token
        ):
            return
        self._unlink_if_unchanged(path, snapshot)

    def _clear_stale_reclamation_claim(self) -> bool:
        if not self._reclamation_path.exists():
            return True
        snapshot = self._stale_snapshot(self._reclamation_path)
        if snapshot is None:
            return False
        return self._unlink_if_unchanged(
            self._reclamation_path,
            snapshot,
        )

    def _reclaim_and_acquire(self) -> bool:
        stale_lock = self._stale_snapshot(self.path)
        if stale_lock is None:
            return False

        claim_token = uuid4().hex
        claim_fd: int | None = None
        for _attempt in range(2):
            if not self._clear_stale_reclamation_claim():
                return False
            try:
                claim_fd = self._create_owned_file(
                    self._reclamation_path,
                    owner_token=claim_token,
                    role="reclamation",
                )
                break
            except FileExistsError:
                continue
        if claim_fd is None:
            return False
        os.close(claim_fd)

        try:
            stale_lock = self._stale_snapshot(self.path)
            if stale_lock is not None:
                if not self._unlink_if_unchanged(self.path, stale_lock):
                    return False
            elif self.path.exists():
                return False
            try:
                self._fd = self._create_owned_file(
                    self.path,
                    owner_token=self._owner_token,
                    role="prediction",
                )
            except FileExistsError:
                return False
            return True
        finally:
            self._remove_if_owned(
                self._reclamation_path,
                claim_token,
            )

    def __enter__(self) -> "PredictionRunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self._clear_stale_reclamation_claim():
            raise PredictionRunConflictError(
                f"prediction run lock reclamation already active for "
                f"{self.path}"
            )
        try:
            self._fd = self._create_owned_file(
                self.path,
                owner_token=self._owner_token,
                role="prediction",
            )
        except FileExistsError as exc:
            if not self._reclaim_and_acquire():
                raise PredictionRunConflictError(
                    f"prediction run already active for lock {self.path}"
                ) from exc
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._remove_if_owned(self.path, self._owner_token)


class PredictionApplicationService:
    """The single application boundary for prediction-producing workflows."""

    def __init__(
        self,
        *,
        registry: PredictionEngineRegistry,
        publisher: PredictionPublisher | None = None,
        lifecycle: PredictionLifecycle | None = None,
    ) -> None:
        self.registry = registry
        self.publisher = publisher or NoArtifactPublisher()
        self.lifecycle = lifecycle or DisabledPredictionLifecycle()

    @staticmethod
    def _validated(request: PredictionRequest) -> tuple[str, str, str]:
        sport = str(request.sport).strip().lower()
        mode = str(request.mode).strip().lower()
        try:
            prediction_date = date.fromisoformat(
                str(request.prediction_date)
            ).isoformat()
        except (TypeError, ValueError) as exc:
            raise PredictionRequestError(
                "prediction_date must be YYYY-MM-DD"
            ) from exc
        if not sport or not mode:
            raise PredictionRequestError("sport and mode are required")
        if request.dry_run and request.force_overwrite:
            raise PredictionRequestError(
                "dry_run and force_overwrite cannot both be enabled"
            )
        return sport, mode, prediction_date

    @staticmethod
    def _run_id(request: PredictionRequest, sport: str, mode: str) -> str:
        if request.run_id is not None:
            value = str(request.run_id).strip()
            if not value:
                raise PredictionRequestError("run_id cannot be blank")
            return value
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        prefix = str(
            request.metadata.get("run_id_prefix", f"{sport}-{mode}")
        ).strip()
        return f"{prefix}-{timestamp}-{uuid4().hex[:12]}"

    @staticmethod
    def _manifest_path(
        request: PredictionRequest,
        *,
        run_id: str,
        sport: str,
        mode: str,
        publication: PublicationOutcome,
    ) -> Path:
        override = request.metadata.get("application_manifest_path")
        if override:
            return Path(str(override))
        out_dir = Path(request.out_dir or "outputs")
        return (
            out_dir
            / "runtime"
            / "manifests"
            / sport
            / mode
            / f"prediction_{sport}_{request.prediction_date}_{run_id}.json"
        )

    @staticmethod
    def _manifest_payload(
        *,
        request: PredictionRequest,
        run_id: str,
        status: str,
        lifecycle_status: str,
        engine_prediction: EnginePrediction,
        publication: PublicationOutcome,
        failure_classification: str | None = None,
    ) -> dict[str, Any]:
        summary = engine_prediction.outputs.get("summary")
        return {
            "manifest_schema_version": 1,
            "run_id": run_id,
            "sport": request.sport,
            "mode": request.mode,
            "prediction_date": request.prediction_date,
            "status": status,
            "dry_run": request.dry_run,
            "entrypoint": request.metadata.get(
                "entrypoint", "prediction_application"
            ),
            "actor_id": "prediction_application",
            "command": request.metadata.get("command", ""),
            "lifecycle_status": lifecycle_status,
            "failure_classification": failure_classification,
            "engine_status": engine_prediction.status,
            "result_summary": (
                dict(summary) if isinstance(summary, Mapping) else {}
            ),
            "model_version": engine_prediction.model_version,
            "provider_provenance": dict(
                engine_prediction.provider_provenance
            ),
            "artifacts": dict(publication.artifact_metadata),
            "artifact_paths": dict(publication.artifact_paths),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }

    def run(self, request: PredictionRequest) -> PredictionResult:
        sport, mode, prediction_date = self._validated(request)
        normalized_request = PredictionRequest(
            sport=sport,
            prediction_date=prediction_date,
            mode=mode,
            run_id=request.run_id,
            out_dir=request.out_dir,
            dry_run=request.dry_run,
            force_overwrite=request.force_overwrite,
            metadata=dict(request.metadata),
        )
        run_id = self._run_id(normalized_request, sport, mode)
        engine = self.registry.resolve(sport, mode)
        out_dir = Path(normalized_request.out_dir or "outputs")
        lock_override = normalized_request.metadata.get("lock_path")
        lock_path = (
            Path(str(lock_override))
            if lock_override
            else (
                out_dir
                / "runtime"
                / "locks"
                / f"prediction_{sport}_{mode}_{prediction_date}.lock"
            )
        )
        lifecycle_state = LifecycleRunState(status="NOT_STARTED")
        transaction = None
        engine_prediction = EnginePrediction(outputs={})
        publication = PublicationOutcome()
        manifest_path: Path | None = None
        lock_context = (
            PredictionRunLock(
                lock_path,
                run_id=run_id,
                sport=sport,
                mode=mode,
                prediction_date=prediction_date,
            )
            if bool(
                normalized_request.metadata.get(
                    "lock_enabled",
                    not normalized_request.dry_run,
                )
            )
            else nullcontext()
        )
        with lock_context:
            try:
                lifecycle_state = self.lifecycle.begin(
                    normalized_request,
                    run_id,
                    engine,
                )
                engine_prediction = engine.execute(
                    PredictionRequest(
                        sport=sport,
                        prediction_date=prediction_date,
                        mode=mode,
                        run_id=run_id,
                        out_dir=normalized_request.out_dir,
                        dry_run=normalized_request.dry_run,
                        force_overwrite=normalized_request.force_overwrite,
                        metadata=normalized_request.metadata,
                    )
                )
                transaction, publication = self.publisher.stage(
                    normalized_request,
                    run_id,
                    engine_prediction,
                )
                write_manifest = bool(
                    normalized_request.metadata.get(
                        "write_application_manifest",
                        not normalized_request.dry_run,
                    )
                )
                if write_manifest:
                    manifest_path = self._manifest_path(
                        normalized_request,
                        run_id=run_id,
                        sport=sport,
                        mode=mode,
                        publication=publication,
                    )
                    initial_manifest = self._manifest_payload(
                        request=normalized_request,
                        run_id=run_id,
                        status="PUBLICATION_STAGED",
                        lifecycle_status=lifecycle_state.status,
                        engine_prediction=engine_prediction,
                        publication=publication,
                    )
                    stage_application_manifest(
                        transaction,
                        path=manifest_path,
                        payload=initial_manifest,
                    )
                transaction.commit()
                lifecycle_status = self.lifecycle.complete(
                    lifecycle_state,
                    normalized_request,
                    publication,
                )
                if lifecycle_status in {"PASS", "DISABLED"}:
                    final_status = (
                        str(engine_prediction.status)
                        if engine_prediction.status
                        else "SUCCESS"
                    )
                else:
                    final_status = "DEGRADED"
                if manifest_path is not None:
                    final_manifest = self._manifest_payload(
                        request=normalized_request,
                        run_id=run_id,
                        status=final_status,
                        lifecycle_status=lifecycle_status,
                        engine_prediction=engine_prediction,
                        publication=publication,
                    )
                    stage_application_manifest(
                        transaction,
                        path=manifest_path,
                        payload=final_manifest,
                    )
                    transaction.commit()
                transaction.finalize()
                artifact_paths = dict(publication.artifact_paths)
                if manifest_path is not None:
                    artifact_paths["application_manifest"] = str(
                        manifest_path
                    )
                return PredictionResult(
                    sport=sport,
                    prediction_date=prediction_date,
                    run_id=run_id,
                    status=final_status,
                    outputs=engine_prediction.outputs,
                    artifact_paths=artifact_paths,
                    provider_provenance=engine_prediction.provider_provenance,
                    lifecycle_status=lifecycle_status,
                    manifest_path=(
                        str(manifest_path)
                        if manifest_path is not None
                        else None
                    ),
                )
            except Exception as exc:
                if transaction is not None:
                    transaction.rollback()
                lifecycle_status = self.lifecycle.fail(
                    lifecycle_state,
                    exc,
                )
                setattr(exc, "prediction_run_id", run_id)
                setattr(exc, "lifecycle_status", lifecycle_status)
                raise


__all__ = [
    "DEFAULT_PREDICTION_LOCK_STALE_SECONDS",
    "DisabledPredictionLifecycle",
    "LifecycleRunState",
    "PredictionApplicationService",
    "PredictionLifecycle",
    "PredictionRequestError",
    "PredictionRunConflictError",
    "PredictionRunLock",
    "PREDICTION_LOCK_STALE_SECONDS_ENV",
    "ShadowPredictionLifecycle",
]
