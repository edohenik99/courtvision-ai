"""Canonical JSON v1 and SHA-256 helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Mapping


CANONICALIZATION_VERSION = "canonical_json_v1"
HASH_ALGORITHM = "SHA-256"


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented deterministically."""


def format_utc_datetime(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise TypeError("value must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise CanonicalizationError("naive datetimes are forbidden")
    normalized = value.astimezone(UTC)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise CanonicalizationError("naive datetimes are forbidden")
        return value.astimezone(UTC)
    if not isinstance(value, str) or not value.strip():
        raise CanonicalizationError("UTC datetime must be a non-empty ISO-8601 string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CanonicalizationError(f"invalid ISO-8601 datetime: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CanonicalizationError("naive datetimes are forbidden")
    return parsed.astimezone(UTC)


def _canonical_value(value: Any, path: str = "$") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise CanonicalizationError(f"{path}: non-finite floats are forbidden")
        return value
    if isinstance(value, datetime):
        return format_utc_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"{path}: object keys must be strings")
            output[key] = _canonical_value(item, f"{path}.{key}")
        return output
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise CanonicalizationError(
        f"{path}: unsupported canonical JSON type {type(value).__name__}"
    )


def canonical_json_v1(value: Any) -> str:
    """Serialize *value* using the frozen canonical JSON v1 algorithm."""

    normalized = _canonical_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_payload_bytes(value: Any) -> bytes:
    return canonical_json_v1(value).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise TypeError("sha256_bytes requires bytes")
    return hashlib.sha256(value).hexdigest()


def payload_sha256(value: Any) -> str:
    return sha256_bytes(canonical_payload_bytes(value))


def file_sha256(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def deterministic_id(prefix: str, namespace: str, value: Any) -> str:
    clean_prefix = str(prefix).strip()
    clean_namespace = str(namespace).strip()
    if not clean_prefix or not clean_namespace:
        raise ValueError("prefix and namespace are required")
    digest = payload_sha256(
        {
            "canonicalization_version": CANONICALIZATION_VERSION,
            "namespace": clean_namespace,
            "value": value,
        }
    )
    return f"{clean_prefix}_{digest}"


__all__ = [
    "CANONICALIZATION_VERSION",
    "CanonicalizationError",
    "HASH_ALGORITHM",
    "canonical_json_v1",
    "canonical_payload_bytes",
    "deterministic_id",
    "file_sha256",
    "format_utc_datetime",
    "parse_utc_datetime",
    "payload_sha256",
    "sha256_bytes",
]
