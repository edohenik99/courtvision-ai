"""Content-addressed, sanitized lifecycle evidence objects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable, Mapping
from uuid import uuid4

from courtvision.lifecycle.canonical import canonical_payload_bytes, sha256_bytes


EVIDENCE_SCHEMA_VERSION = 1
REDACTED_VALUE = "[REDACTED]"
_SENSITIVE_KEY_SUFFIXES = (
    "authorization",
    "apikey",
    "token",
    "accesstoken",
    "secret",
    "password",
    "cookie",
    "session",
)


class EvidenceIntegrityError(RuntimeError):
    """Raised when immutable evidence cannot be safely committed or verified."""


@dataclass(frozen=True, slots=True)
class PreparedEvidenceObject:
    category: str
    sha256: str
    data: bytes

    @property
    def relative_path(self) -> str:
        return f"evidence/objects/{self.sha256[:2]}/{self.sha256}.json"


def is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", str(key).strip().lower())
    return any(normalized == item or normalized.endswith(item) for item in _SENSITIVE_KEY_SUFFIXES)


def sanitize_evidence(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                REDACTED_VALUE
                if is_sensitive_key(str(key))
                else sanitize_evidence(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_evidence(item) for item in value]
    return value


def prepare_evidence_object(
    category: str,
    payload: Mapping[str, Any],
    *,
    digest_func: Callable[[bytes], str] = sha256_bytes,
) -> PreparedEvidenceObject:
    clean_category = str(category).strip().lower()
    if not clean_category or not re.fullmatch(r"[a-z0-9_]+", clean_category):
        raise ValueError("evidence category must contain only lowercase letters, digits, and underscores")
    sanitized = sanitize_evidence(dict(payload))
    data = canonical_payload_bytes(
        {
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "category": clean_category,
            "payload": sanitized,
        }
    )
    digest = digest_func(data)
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise EvidenceIntegrityError("evidence digest must be a lowercase SHA-256 hex string")
    return PreparedEvidenceObject(clean_category, digest, data)


def evidence_path(root: Path, evidence: PreparedEvidenceObject) -> Path:
    return root / evidence.relative_path


def commit_prepared_evidence(root: Path, evidence: PreparedEvidenceObject) -> Path:
    path = evidence_path(root, evidence)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != evidence.data:
            raise EvidenceIntegrityError(
                f"evidence hash path exists with different content: {path}"
            )
        return path
    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(evidence.data)
            handle.flush()
            import os

            os.fsync(handle.fileno())
        temporary.rename(path)
    except FileExistsError:
        if not path.exists() or path.read_bytes() != evidence.data:
            raise EvidenceIntegrityError(
                f"evidence object publication conflict: {path}"
            )
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def verify_evidence(root: Path, sha256: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{64}", str(sha256)):
        return False
    path = root / "evidence" / "objects" / sha256[:2] / f"{sha256}.json"
    if not path.is_file() or path.is_symlink():
        return False
    return sha256_bytes(path.read_bytes()) == sha256


__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "EvidenceIntegrityError",
    "PreparedEvidenceObject",
    "commit_prepared_evidence",
    "evidence_path",
    "is_sensitive_key",
    "prepare_evidence_object",
    "sanitize_evidence",
    "verify_evidence",
]
