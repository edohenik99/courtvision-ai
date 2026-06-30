"""Source-aware, raw-only data collection contracts."""

from courtvision.data_collection.core import (
    CollectionError,
    CollectionRequest,
    CollectionResult,
    collect_sources,
)
from courtvision.data_collection.registry import get_collection_adapter

__all__ = [
    "CollectionError",
    "CollectionRequest",
    "CollectionResult",
    "collect_sources",
    "get_collection_adapter",
]
