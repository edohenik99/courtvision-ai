"""Transactional publication primitives for model-derived artifacts."""

from __future__ import annotations

import contextlib
import contextvars
import csv
from dataclasses import dataclass, field
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import uuid4

import pandas as pd

from courtvision.artifact_guard import (
    guard_no_existing_artifact,
    guard_prediction_artifact_date,
    log_prediction_artifact_write,
)
from courtvision.prediction.contracts import EnginePrediction, PredictionRequest


class PredictionPublicationError(RuntimeError):
    """Raised when canonical prediction publication cannot complete safely."""


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    label: str
    path: str
    row_count: int | None
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "path": self.path,
            "row_count": self.row_count,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(slots=True)
class PublicationOutcome:
    artifact_paths: Mapping[str, str] = field(default_factory=dict)
    artifact_metadata: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )
    primary_artifact_label: str | None = None


@dataclass(slots=True)
class _StagedArtifact:
    label: str
    final_path: Path
    staged_path: Path
    row_count: int | None
    backup_path: Path | None = None
    committed: bool = False


class PredictionPublicationTransaction:
    """Stage prediction files beside their targets and commit via ``os.replace``."""

    def __init__(
        self,
        *,
        prediction_date: str,
        run_id: str,
        caller: str,
    ) -> None:
        self.prediction_date = str(prediction_date)
        self.run_id = str(run_id)
        self.caller = str(caller)
        self._artifacts: list[_StagedArtifact] = []
        self._created_directories: list[Path] = []
        self._finalized = False

    def stage_bytes(
        self,
        path: str | Path,
        payload: bytes,
        *,
        label: str,
        row_count: int | None = None,
        protect_existing: bool = False,
        force_overwrite: bool = False,
        validate_prediction_date: bool = True,
    ) -> Path:
        if self._finalized:
            raise PredictionPublicationError(
                "cannot stage artifacts after transaction finalization"
            )
        final_path = Path(path)
        if validate_prediction_date:
            guard_prediction_artifact_date(
                requested_prediction_date=self.prediction_date,
                output_path=final_path,
                caller=self.caller,
            )
        if protect_existing:
            guard_no_existing_artifact(
                output_path=final_path,
                force=force_overwrite,
                caller=self.caller,
                artifact_label=label,
            )
        missing_directories: list[Path] = []
        candidate = final_path.parent
        while not candidate.exists():
            missing_directories.append(candidate)
            if candidate.parent == candidate:
                break
            candidate = candidate.parent
        final_path.parent.mkdir(parents=True, exist_ok=True)
        self._created_directories.extend(
            directory
            for directory in reversed(missing_directories)
            if directory not in self._created_directories
        )
        # Keep the temporary basename short so deeply nested Windows output
        # roots remain below the legacy MAX_PATH boundary.
        staged_path = final_path.parent / f".cvp-{uuid4().hex[:12]}.stage"
        try:
            with staged_path.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise PredictionPublicationError(
                f"could not stage prediction artifact {final_path}: {exc}"
            ) from exc
        self._artifacts.append(
            _StagedArtifact(
                label=str(label),
                final_path=final_path,
                staged_path=staged_path,
                row_count=row_count,
            )
        )
        return final_path

    def commit(self) -> None:
        """Commit every currently staged artifact while retaining rollback data."""

        for artifact in self._artifacts:
            if artifact.committed:
                continue
            try:
                if artifact.final_path.exists():
                    backup_path = artifact.final_path.parent / (
                        f".cvp-{uuid4().hex[:12]}.backup"
                    )
                    shutil.copy2(artifact.final_path, backup_path)
                    artifact.backup_path = backup_path
                os.replace(artifact.staged_path, artifact.final_path)
                artifact.committed = True
            except OSError as exc:
                self.rollback()
                raise PredictionPublicationError(
                    f"could not commit prediction artifact "
                    f"{artifact.final_path}: {exc}"
                ) from exc

    def rollback(self) -> None:
        """Remove staged files and restore any targets changed by this run."""

        for artifact in reversed(self._artifacts):
            try:
                if artifact.committed:
                    if artifact.backup_path and artifact.backup_path.exists():
                        os.replace(artifact.backup_path, artifact.final_path)
                    elif artifact.final_path.exists():
                        artifact.final_path.unlink()
                elif artifact.staged_path.exists():
                    artifact.staged_path.unlink()
            except OSError:
                # Best effort only; the original publication error remains primary.
                pass
            if artifact.backup_path and artifact.backup_path.exists():
                try:
                    artifact.backup_path.unlink()
                except OSError:
                    pass
        for directory in reversed(self._created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass

    def finalize(self) -> None:
        for artifact in self._artifacts:
            if artifact.staged_path.exists():
                artifact.staged_path.unlink()
            if artifact.backup_path and artifact.backup_path.exists():
                artifact.backup_path.unlink()
        self._finalized = True

    def metadata(self) -> dict[str, ArtifactMetadata]:
        output: dict[str, ArtifactMetadata] = {}
        for artifact in self._artifacts:
            source = (
                artifact.final_path
                if artifact.committed
                else artifact.staged_path
            )
            if not source.is_file():
                continue
            payload = source.read_bytes()
            output[artifact.label] = ArtifactMetadata(
                label=artifact.label,
                path=str(artifact.final_path),
                row_count=artifact.row_count,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            )
        return output


_ACTIVE_TRANSACTION: contextvars.ContextVar[
    PredictionPublicationTransaction | None
] = contextvars.ContextVar("courtvision_prediction_publication", default=None)


@contextlib.contextmanager
def activate_publication_transaction(
    transaction: PredictionPublicationTransaction,
):
    token = _ACTIVE_TRANSACTION.set(transaction)
    try:
        yield transaction
    finally:
        _ACTIVE_TRANSACTION.reset(token)


def _transaction_or_immediate(
    *,
    prediction_date: str,
    caller: str,
) -> tuple[PredictionPublicationTransaction, bool]:
    active = _ACTIVE_TRANSACTION.get()
    if active is not None:
        return active, False
    return (
        PredictionPublicationTransaction(
            prediction_date=prediction_date,
            run_id=f"direct-{uuid4().hex}",
            caller=caller,
        ),
        True,
    )


def current_publication_metadata() -> dict[str, ArtifactMetadata]:
    """Return metadata for files staged in the active publication callback."""

    transaction = _ACTIVE_TRANSACTION.get()
    return transaction.metadata() if transaction is not None else {}


def publish_dataframe(
    path: str | Path,
    dataframe: pd.DataFrame,
    *,
    prediction_date: str,
    caller: str,
    artifact_label: str,
    protect_existing: bool = False,
    force_overwrite: bool = False,
) -> Path:
    log_prediction_artifact_write(
        requested_prediction_date=prediction_date,
        output_path=Path(path),
        caller=caller,
        artifact_label=artifact_label,
    )
    buffer = io.StringIO(newline="")
    dataframe.to_csv(buffer, index=False)
    transaction, immediate = _transaction_or_immediate(
        prediction_date=prediction_date,
        caller=caller,
    )
    final_path = transaction.stage_bytes(
        path,
        buffer.getvalue().encode("utf-8"),
        label=artifact_label,
        row_count=int(len(dataframe)),
        protect_existing=protect_existing,
        force_overwrite=force_overwrite,
    )
    if immediate:
        transaction.commit()
        transaction.finalize()
    return final_path


def publish_json(
    path: str | Path,
    payload: Any,
    *,
    prediction_date: str,
    caller: str,
    artifact_label: str,
    create_once: bool = False,
    force_overwrite: bool = False,
    trailing_newline: bool = False,
    sort_keys: bool = False,
) -> Path:
    log_prediction_artifact_write(
        requested_prediction_date=prediction_date,
        output_path=Path(path),
        caller=caller,
        artifact_label=artifact_label,
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=sort_keys)
    if trailing_newline:
        text += "\n"
    transaction, immediate = _transaction_or_immediate(
        prediction_date=prediction_date,
        caller=caller,
    )
    final_path = transaction.stage_bytes(
        path,
        text.encode("utf-8"),
        label=artifact_label,
        protect_existing=create_once,
        force_overwrite=force_overwrite,
    )
    if immediate:
        transaction.commit()
        transaction.finalize()
    return final_path


def publish_text(
    path: str | Path,
    payload: str,
    *,
    prediction_date: str,
    caller: str,
    artifact_label: str,
    create_once: bool = False,
    force_overwrite: bool = False,
) -> Path:
    log_prediction_artifact_write(
        requested_prediction_date=prediction_date,
        output_path=Path(path),
        caller=caller,
        artifact_label=artifact_label,
    )
    transaction, immediate = _transaction_or_immediate(
        prediction_date=prediction_date,
        caller=caller,
    )
    final_path = transaction.stage_bytes(
        path,
        payload.encode("utf-8"),
        label=artifact_label,
        protect_existing=create_once,
        force_overwrite=force_overwrite,
    )
    if immediate:
        transaction.commit()
        transaction.finalize()
    return final_path


def publish_csv_rows(
    path: str | Path,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, object]],
    *,
    prediction_date: str,
    caller: str,
    artifact_label: str,
    create_once: bool = True,
    force_overwrite: bool = False,
) -> Path:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(columns))
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in columns})
    transaction, immediate = _transaction_or_immediate(
        prediction_date=prediction_date,
        caller=caller,
    )
    final_path = transaction.stage_bytes(
        path,
        buffer.getvalue().encode("utf-8"),
        label=artifact_label,
        row_count=len(rows),
        protect_existing=create_once,
        force_overwrite=force_overwrite,
    )
    if immediate:
        transaction.commit()
        transaction.finalize()
    return final_path


class PredictionPublisher(Protocol):
    def stage(
        self,
        request: PredictionRequest,
        run_id: str,
        engine_prediction: EnginePrediction,
    ) -> tuple[PredictionPublicationTransaction, PublicationOutcome]:
        """Stage artifacts without making them visible."""


class NoArtifactPublisher:
    """Publisher used by compatibility calls that historically returned data only."""

    def stage(
        self,
        request: PredictionRequest,
        run_id: str,
        engine_prediction: EnginePrediction,
    ) -> tuple[PredictionPublicationTransaction, PublicationOutcome]:
        transaction = PredictionPublicationTransaction(
            prediction_date=request.prediction_date,
            run_id=run_id,
            caller=str(
                request.metadata.get("entrypoint", "prediction_application")
            ),
        )
        return transaction, PublicationOutcome()


PublicationCallback = Callable[
    [PredictionRequest, str, EnginePrediction],
    Mapping[str, str | Path],
]


class CallbackPredictionPublisher:
    """Adapter for existing writers while enforcing one transaction."""

    def __init__(
        self,
        callback: PublicationCallback,
        *,
        primary_artifact_label: str | None = None,
    ) -> None:
        self._callback = callback
        self._primary_artifact_label = primary_artifact_label

    def stage(
        self,
        request: PredictionRequest,
        run_id: str,
        engine_prediction: EnginePrediction,
    ) -> tuple[PredictionPublicationTransaction, PublicationOutcome]:
        transaction = PredictionPublicationTransaction(
            prediction_date=request.prediction_date,
            run_id=run_id,
            caller=str(
                request.metadata.get("entrypoint", "prediction_application")
            ),
        )
        try:
            with activate_publication_transaction(transaction):
                paths = self._callback(request, run_id, engine_prediction)
        except Exception:
            transaction.rollback()
            raise
        metadata = {
            label: item.to_dict()
            for label, item in transaction.metadata().items()
        }
        normalized_paths = {
            str(label): str(path) for label, path in dict(paths).items()
        }
        return transaction, PublicationOutcome(
            artifact_paths=normalized_paths,
            artifact_metadata=metadata,
            primary_artifact_label=self._primary_artifact_label,
        )


def stage_application_manifest(
    transaction: PredictionPublicationTransaction,
    *,
    path: str | Path,
    payload: Mapping[str, Any],
    label: str = "prediction_application_manifest",
) -> Path:
    return transaction.stage_bytes(
        path,
        (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=str,
            )
            + "\n"
        ).encode("utf-8"),
        label=label,
        validate_prediction_date=False,
    )


__all__ = [
    "ArtifactMetadata",
    "CallbackPredictionPublisher",
    "NoArtifactPublisher",
    "PredictionPublicationError",
    "PredictionPublicationTransaction",
    "PredictionPublisher",
    "PublicationOutcome",
    "activate_publication_transaction",
    "current_publication_metadata",
    "publish_csv_rows",
    "publish_dataframe",
    "publish_json",
    "publish_text",
    "stage_application_manifest",
]
