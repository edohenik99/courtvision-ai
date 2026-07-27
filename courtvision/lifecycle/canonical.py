"""Canonical JSON v1 and SHA-256 helpers."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any


CANONICALIZATION_VERSION = "canonical_json_v1"
HASH_ALGORITHM = "SHA-256"


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented deterministically."""


class FrozenJSONDict(  # pyright: ignore[reportGeneralTypeIssues]
    bytes,
    Mapping[str, Any],
):
    """Immutable JSON mapping with no writable per-instance storage.

    Canonical JSON bytes are the complete instance value. There is no
    instance dictionary, writable slot, or mutable backing container that can
    be replaced through ``object.__setattr__``. Nested containers are frozen
    again when read. The public mapping remains intentionally unhashable;
    stable digests use the explicit canonical payload/equality helpers.

    ``to_dict()`` and deep-copy return detached JSON-compatible containers.
    Direct ``json.dumps(FrozenJSONDict(...))`` is intentionally unsupported
    and raises ``TypeError`` instead of silently encoding a JSON string.
    """

    __slots__ = ()

    def __new__(
        cls,
        values: Mapping[str, Any] | Iterable[tuple[str, Any]],
    ) -> "FrozenJSONDict":
        raw = dict(values)
        if any(not isinstance(key, str) for key in raw):
            raise CanonicalizationError("$: object keys must be strings")
        normalized = {
            key: canonical_json_value(raw[key], f"$.{key}")
            for key in sorted(raw)
        }
        data = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return bytes.__new__(cls, data)

    def __getitem__(self, key: object) -> Any:
        if not isinstance(key, str):
            raise TypeError("FrozenJSONDict keys must be strings")
        raw = self.to_dict()
        if key not in raw:
            raise KeyError(key)
        return freeze_json_value(raw[key], f"$.{key}")

    def __iter__(self) -> Iterator[Any]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self.to_dict()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return False
        try:
            return canonical_equal(self.to_dict(), other)
        except CanonicalizationError:
            return False

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    __hash__ = None  # pyright: ignore[reportAssignmentType]

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.to_dict()!r})"

    def __copy__(self) -> "FrozenJSONDict":
        return type(self)(self.to_dict())

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        copied = deepcopy(self.to_dict(), memo)
        memo[id(self)] = copied
        return copied

    def to_dict(self) -> dict[str, Any]:
        """Return detached, mutable, JSON-compatible containers."""

        value = json.loads(bytes.decode(self, "utf-8"))
        if not isinstance(value, dict):
            raise CanonicalizationError("frozen JSON mapping storage is malformed")
        return value


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


def canonical_json_value(value: Any, path: str = "$") -> Any:
    """Return one recursively normalized, JSON-compatible canonical value."""

    if isinstance(value, FrozenJSONDict):
        return value.to_dict()
    if isinstance(value, Enum):
        return canonical_json_value(value.value, path)
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
        keys = tuple(value)
        if any(not isinstance(key, str) for key in keys):
            raise CanonicalizationError(f"{path}: object keys must be strings")
        for key in sorted(keys):
            output[key] = canonical_json_value(value[key], f"{path}.{key}")
        return output
    if isinstance(value, (list, tuple)):
        return [
            canonical_json_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise CanonicalizationError(
        f"{path}: unsupported canonical JSON type {type(value).__name__}"
    )


def freeze_json_value(value: Any, path: str = "$") -> Any:
    """Detach and deeply freeze a value without changing its canonical JSON."""

    normalized = canonical_json_value(value, path)
    if isinstance(normalized, dict):
        return FrozenJSONDict(normalized)
    if isinstance(normalized, list):
        return tuple(
            freeze_json_value(item, f"{path}[{index}]")
            for index, item in enumerate(normalized)
        )
    return normalized


def thaw_json_value(value: Any, path: str = "$") -> Any:
    """Return mutable canonical JSON containers for serialization."""

    return canonical_json_value(value, path)


def canonical_json_v1(value: Any) -> str:
    """Serialize *value* using the frozen canonical JSON v1 algorithm."""

    normalized = canonical_json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _type_preserving_value(value: Any, path: str = "$") -> Any:
    """Return the tagged canonical representation used for exact equality."""

    if isinstance(value, FrozenJSONDict):
        value = value.to_dict()
    if isinstance(value, Enum):
        enum_type = type(value)
        return {
            "type": "enum",
            "enum_type": f"{enum_type.__module__}.{enum_type.__qualname__}",
            "value": _type_preserving_value(value.value, path),
        }
    if value is None:
        return {"type": "null"}
    if type(value) is bool:
        return {"type": "boolean", "value": value}
    if type(value) is int:
        return {"type": "integer", "value": str(value)}
    if type(value) is float:
        if not math.isfinite(value):
            raise CanonicalizationError(f"{path}: non-finite float")
        return {"type": "float", "value": value.hex()}
    if type(value) is str:
        return {"type": "string", "value": value}
    if isinstance(value, datetime):
        return {
            "type": "datetime",
            "value": format_utc_datetime(value),
        }
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, Mapping):
        keys = tuple(value)
        if any(not isinstance(key, str) for key in keys):
            raise CanonicalizationError(f"{path}: object keys must be strings")
        return {
            "type": "object",
            "value": [
                [
                    key,
                    _type_preserving_value(value[key], f"{path}.{key}"),
                ]
                for key in sorted(keys)
            ],
        }
    if isinstance(value, (list, tuple)):
        return {
            "type": "array",
            "value": [
                _type_preserving_value(item, f"{path}[{index}]")
                for index, item in enumerate(value)
            ],
        }
    raise CanonicalizationError(
        f"{path}: unsupported canonical equality type {type(value).__name__}"
    )


def canonical_equality_bytes(value: Any) -> bytes:
    """Serialize exact typed values for authorization and identity equality.

    Numeric types never compare across type boundaries. In particular, integer
    ``1``, float ``1.0``, boolean ``True``, and string ``"1"`` are distinct.
    Lists and tuples intentionally share the canonical array representation.
    """

    return json.dumps(
        _type_preserving_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_equality_sha256(value: Any) -> str:
    return sha256_bytes(canonical_equality_bytes(value))


def canonical_equal(left: Any, right: Any) -> bool:
    return canonical_equality_bytes(left) == canonical_equality_bytes(right)


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
    "FrozenJSONDict",
    "HASH_ALGORITHM",
    "canonical_equal",
    "canonical_equality_bytes",
    "canonical_equality_sha256",
    "canonical_json_v1",
    "canonical_json_value",
    "canonical_payload_bytes",
    "deterministic_id",
    "file_sha256",
    "format_utc_datetime",
    "freeze_json_value",
    "parse_utc_datetime",
    "payload_sha256",
    "sha256_bytes",
    "thaw_json_value",
]
