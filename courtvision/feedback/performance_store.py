"""Performance tracking store with rolling windows.

Maintains historical performance data with configurable rolling windows
for self-correction and feedback loop functionality.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass
class PerformanceRecord:
    """Single prediction performance record."""

    prediction_date: str
    player_id: int
    player_name: str
    stat_type: str
    market_type: str
    edge: float
    confidence: float
    confidence_bucket: str
    edge_bucket: str
    result: str  # "hit", "miss", "push"
    actual_value: float
    line_value: float
    projection: float
    margin: float | None = None  # How much they beat/missed by


@dataclass
class WindowMetrics:
    """Metrics for a specific rolling window."""

    window_days: int
    total_picks: int = 0
    hits: int = 0
    misses: int = 0
    pushes: int = 0
    hit_rate: float = 0.0
    avg_edge: float = 0.0
    avg_confidence: float = 0.0
    by_confidence_bucket: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_edge_bucket: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_player: dict[int, dict[str, Any]] = field(default_factory=dict)
    by_stat_type: dict[str, dict[str, Any]] = field(default_factory=dict)

    def compute_hit_rate(self) -> float:
        """Compute hit rate excluding pushes."""
        decisions = self.hits + self.misses
        return self.hits / decisions if decisions > 0 else 0.0


class PerformanceStore:
    """Store and analyze prediction performance with rolling windows.

    Maintains performance records and computes metrics for configurable
    rolling windows (7, 14, 30 days) to enable dynamic adjustments.
    """

    CONFIDENCE_BUCKETS = ["low", "mid", "high", "elite"]
    EDGE_BUCKETS = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 10.0), (10.0, float("inf"))]
    DEFAULT_WINDOWS = [7, 14, 30]

    def __init__(
        self,
        storage_path: str | Path | None = None,
        windows: list[int] | None = None,
    ) -> None:
        """Initialize performance store.

        Args:
            storage_path: Path to persist performance data
            windows: List of window sizes in days (default: [7, 14, 30])
        """
        self.storage_path = Path(storage_path) if storage_path else None
        self.windows = windows or self.DEFAULT_WINDOWS
        self.records: list[PerformanceRecord] = []
        self._by_date: dict[str, list[PerformanceRecord]] = defaultdict(list)

        if self.storage_path and self.storage_path.exists():
            self._load()

    def add_record(self, record: PerformanceRecord) -> None:
        """Add a performance record to the store."""
        self.records.append(record)
        self._by_date[record.prediction_date].append(record)

        # Persist if path configured
        if self.storage_path:
            self._save()

    def add_graded_picks(self, graded_picks: list[Any], prediction_date: str) -> None:
        """Add graded picks from PickGrader."""
        for pick in graded_picks:
            # Map confidence to bucket
            conf = pick.confidence if hasattr(pick, "confidence") else 0.0
            conf_bucket = self._confidence_to_bucket(conf)

            # Map edge to bucket
            edge = pick.edge if hasattr(pick, "edge") else 0.0
            edge_bucket = self._edge_to_bucket(edge)

            # Determine result
            result = self._determine_result(pick)

            record = PerformanceRecord(
                prediction_date=prediction_date,
                player_id=pick.player_id if hasattr(pick, "player_id") else 0,
                player_name=pick.player_name if hasattr(pick, "player_name") else "",
                stat_type=self._extract_stat_type(pick),
                market_type=pick.prop_type if hasattr(pick, "prop_type") else "",
                edge=edge,
                confidence=conf,
                confidence_bucket=conf_bucket,
                edge_bucket=edge_bucket,
                result=result,
                actual_value=pick.actual_stat if hasattr(pick, "actual_stat") else 0.0,
                line_value=pick.line_value if hasattr(pick, "line_value") else 0.0,
                projection=pick.projection if hasattr(pick, "projection") else 0.0,
            )
            self.add_record(record)

    def get_window_metrics(self, window_days: int, end_date: str | None = None) -> WindowMetrics:
        """Compute metrics for a specific rolling window."""
        end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.now()
        start = end - timedelta(days=window_days)

        # Filter records in window
        window_records = [
            r for r in self.records
            if start <= datetime.strptime(r.prediction_date, "%Y-%m-%d") <= end
        ]

        metrics = WindowMetrics(window_days=window_days)
        metrics.total_picks = len(window_records)

        if not window_records:
            return metrics

        # Count results
        for r in window_records:
            if r.result == "hit":
                metrics.hits += 1
            elif r.result == "miss":
                metrics.misses += 1
            else:
                metrics.pushes += 1

        metrics.hit_rate = metrics.compute_hit_rate()
        metrics.avg_edge = sum(r.edge for r in window_records) / len(window_records)
        metrics.avg_confidence = sum(r.confidence for r in window_records) / len(window_records)

        # Compute by bucket/player/stat
        metrics.by_confidence_bucket = self._aggregate_by_confidence(window_records)
        metrics.by_edge_bucket = self._aggregate_by_edge(window_records)
        metrics.by_player = self._aggregate_by_player(window_records)
        metrics.by_stat_type = self._aggregate_by_stat_type(window_records)

        return metrics

    def get_all_windows(self, end_date: str | None = None) -> dict[int, WindowMetrics]:
        """Get metrics for all configured windows."""
        return {w: self.get_window_metrics(w, end_date) for w in self.windows}

    def get_player_performance(self, player_id: int, window_days: int = 30) -> dict[str, Any]:
        """Get performance metrics for a specific player."""
        window = self.get_window_metrics(window_days)
        return window.by_player.get(player_id, {"total": 0, "hits": 0, "hit_rate": 0.0})

    def get_confidence_calibration(self, window_days: int = 30) -> dict[str, dict[str, Any]]:
        """Get hit rates by confidence bucket for calibration analysis."""
        window = self.get_window_metrics(window_days)
        return window.by_confidence_bucket

    def get_edge_reliability(self, window_days: int = 30) -> dict[str, dict[str, Any]]:
        """Get hit rates by edge bucket for edge reliability analysis."""
        window = self.get_window_metrics(window_days)
        return window.by_edge_bucket

    def _confidence_to_bucket(self, confidence: float) -> str:
        """Map confidence value to bucket label."""
        if confidence >= 0.80:
            return "elite"
        elif confidence >= 0.70:
            return "high"
        elif confidence >= 0.60:
            return "mid"
        return "low"

    def _edge_to_bucket(self, edge: float) -> str:
        """Map edge value to bucket label."""
        for low, high in self.EDGE_BUCKETS:
            if low <= edge < high:
                return f"{low:.1f}-{high:.1f}"
        return "10.0+"

    def _determine_result(self, pick: Any) -> str:
        """Determine if a pick was hit, miss, or push."""
        if not hasattr(pick, "actual_stat") or not hasattr(pick, "line_value"):
            return "push"

        actual = pick.actual_stat
        line = pick.line_value
        side = pick.side if hasattr(pick, "side") else "over"

        # Push if exactly on line
        if abs(actual - line) < 0.01:
            return "push"

        if side == "over":
            return "hit" if actual > line else "miss"
        else:  # under
            return "hit" if actual < line else "miss"

    def _extract_stat_type(self, pick: Any) -> str:
        """Extract stat type from pick."""
        prop_type = pick.prop_type if hasattr(pick, "prop_type") else ""
        # Map prop_type to stat type
        mapping = {
            "player_points": "points",
            "player_rebounds": "rebounds",
            "player_assists": "assists",
            "player_3pt_made": "threes",
            "player_steals": "steals",
            "player_blocks": "blocks",
        }
        return mapping.get(prop_type, prop_type)

    def _aggregate_by_confidence(self, records: list[PerformanceRecord]) -> dict[str, dict[str, Any]]:
        """Aggregate records by confidence bucket."""
        buckets = {b: {"total": 0, "hits": 0, "misses": 0} for b in self.CONFIDENCE_BUCKETS}

        for r in records:
            bucket = r.confidence_bucket
            if bucket in buckets:
                buckets[bucket]["total"] += 1
                if r.result == "hit":
                    buckets[bucket]["hits"] += 1
                elif r.result == "miss":
                    buckets[bucket]["misses"] += 1

        # Compute hit rates
        for b in buckets:
            total = buckets[b]["hits"] + buckets[b]["misses"]
            buckets[b]["hit_rate"] = buckets[b]["hits"] / total if total > 0 else 0.0

        return buckets

    def _aggregate_by_edge(self, records: list[PerformanceRecord]) -> dict[str, dict[str, Any]]:
        """Aggregate records by edge bucket."""
        buckets: dict[str, dict[str, Any]] = {}
        for low, high in self.EDGE_BUCKETS:
            label = f"{low:.1f}-{high:.1f}" if high != float("inf") else "10.0+"
            buckets[label] = {"total": 0, "hits": 0, "misses": 0}

        for r in records:
            bucket = r.edge_bucket
            if bucket in buckets:
                buckets[bucket]["total"] += 1
                if r.result == "hit":
                    buckets[bucket]["hits"] += 1
                elif r.result == "miss":
                    buckets[bucket]["misses"] += 1

        # Compute hit rates
        for b in buckets:
            total = buckets[b]["hits"] + buckets[b]["misses"]
            buckets[b]["hit_rate"] = buckets[b]["hits"] / total if total > 0 else 0.0

        return buckets

    def _aggregate_by_player(self, records: list[PerformanceRecord]) -> dict[int, dict[str, Any]]:
        """Aggregate records by player."""
        players: dict[int, dict[str, Any]] = defaultdict(lambda: {"total": 0, "hits": 0, "misses": 0})

        for r in records:
            players[r.player_id]["total"] += 1
            if r.result == "hit":
                players[r.player_id]["hits"] += 1
            elif r.result == "miss":
                players[r.player_id]["misses"] += 1

        # Compute hit rates
        for pid in players:
            total = players[pid]["hits"] + players[pid]["misses"]
            players[pid]["hit_rate"] = players[pid]["hits"] / total if total > 0 else 0.0
            players[pid]["player_name"] = next(
                (r.player_name for r in records if r.player_id == pid), ""
            )

        return dict(players)

    def _aggregate_by_stat_type(self, records: list[PerformanceRecord]) -> dict[str, dict[str, Any]]:
        """Aggregate records by stat type."""
        stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "hits": 0, "misses": 0})

        for r in records:
            stats[r.stat_type]["total"] += 1
            if r.result == "hit":
                stats[r.stat_type]["hits"] += 1
            elif r.result == "miss":
                stats[r.stat_type]["misses"] += 1

        # Compute hit rates
        for st in stats:
            total = stats[st]["hits"] + stats[st]["misses"]
            stats[st]["hit_rate"] = stats[st]["hits"] / total if total > 0 else 0.0

        return dict(stats)

    def _save(self) -> None:
        """Persist records to storage."""
        if not self.storage_path:
            return

        data = [
            {
                "prediction_date": r.prediction_date,
                "player_id": r.player_id,
                "player_name": r.player_name,
                "stat_type": r.stat_type,
                "market_type": r.market_type,
                "edge": r.edge,
                "confidence": r.confidence,
                "confidence_bucket": r.confidence_bucket,
                "edge_bucket": r.edge_bucket,
                "result": r.result,
                "actual_value": r.actual_value,
                "line_value": r.line_value,
                "projection": r.projection,
            }
            for r in self.records
        ]

        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.storage_path, "w") as f:
            json.dump(data, f, indent=2)

    def _load(self) -> None:
        """Load records from storage."""
        if not self.storage_path or not self.storage_path.exists():
            return

        try:
            with open(self.storage_path) as f:
                data = json.load(f)

            self.records = [PerformanceRecord(**item) for item in data]
            self._by_date.clear()
            for r in self.records:
                self._by_date[r.prediction_date].append(r)
        except (json.JSONDecodeError, TypeError):
            # If loading fails, start fresh
            self.records = []
            self._by_date.clear()
