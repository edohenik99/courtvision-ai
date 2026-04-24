"""Pick-level attribution tracking for causal analysis.

Tracks individual contribution components for each pick to enable
understanding of WHY predictions succeed or fail.

Phase 8: Causal Attribution and Mistake Analysis
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class PickAttribution:
    """Complete attribution breakdown for a single pick.

    Captures all signal contributions at prediction time
    for post-hoc causal analysis.
    """

    # Identifiers
    pick_id: str
    prediction_date: str
    player_id: int
    player_name: str
    stat_type: str
    market_type: str

    # Projection contribution
    projection_value: float
    line_value: float
    projection_edge: float  # (projection - line) / line

    # Confidence components
    base_confidence: float  # From scoring
    final_confidence: float  # After penalties
    confidence_bucket: str  # low, mid, high, elite

    # Edge analysis
    edge_raw: float  # Before adjustments
    edge_final: float  # After dampening
    edge_bucket: str

    # Penalty impact breakdown
    penalty_impacts: dict[str, float] = field(default_factory=dict)
    # e.g., {"market_quality": -0.05, "injury_volatility": -0.03, ...}

    total_penalty_impact: float = 0.0  # Sum of all penalties

    # Injury context
    injury_boost: float = 0.0  # Positive if key players out
    injury_volatility_penalty: float = 0.0
    injury_context_score: float = 0.0  # Net injury impact

    # Market context
    market_quality_score: float = 0.0  # Market efficiency rating
    market_line_aggressiveness: float = 0.0  # How aggressive vs historical

    # Recent form
    recent_form_ratio: float = 1.0  # Current vs historical performance
    recent_form_confidence_impact: float = 0.0

    # Signal flags
    signals_present: list[str] = field(default_factory=list)
    # e.g., ["high_edge", "strong_projection", "injury_boost", "good_recent_form"]

    def compute_signal_strength(self) -> dict[str, float]:
        """Compute relative strength of each signal category."""
        return {
            "projection": abs(self.projection_edge),
            "confidence_delta": self.final_confidence - self.base_confidence,
            "injury_net": self.injury_boost + self.injury_volatility_penalty,
            "penalty_severity": abs(self.total_penalty_impact),
            "market_quality": self.market_quality_score,
            "recent_form": self.recent_form_ratio - 1.0,
        }

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "pick_id": self.pick_id,
            "prediction_date": self.prediction_date,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "stat_type": self.stat_type,
            "market_type": self.market_type,
            "projection": {
                "value": self.projection_value,
                "line": self.line_value,
                "edge": self.projection_edge,
            },
            "confidence": {
                "base": self.base_confidence,
                "final": self.final_confidence,
                "bucket": self.confidence_bucket,
            },
            "edge": {
                "raw": self.edge_raw,
                "final": self.edge_final,
                "bucket": self.edge_bucket,
            },
            "penalties": {
                "breakdown": self.penalty_impacts,
                "total_impact": self.total_penalty_impact,
            },
            "injury": {
                "boost": self.injury_boost,
                "volatility_penalty": self.injury_volatility_penalty,
                "net_impact": self.injury_context_score,
            },
            "market": {
                "quality_score": self.market_quality_score,
                "line_aggressiveness": self.market_line_aggressiveness,
            },
            "recent_form": {
                "ratio": self.recent_form_ratio,
                "confidence_impact": self.recent_form_confidence_impact,
            },
            "signals_present": self.signals_present,
        }


class AttributionTracker:
    """Tracks attribution for all picks enabling causal analysis.

    Maintains a registry of pick attributions that can be analyzed
    post-grading to understand which components drove success/failure.
    """

    def __init__(self) -> None:
        """Initialize attribution tracker."""
        self._attributions: dict[str, PickAttribution] = {}
        self._by_player: dict[int, list[str]] = {}
        self._by_date: dict[str, list[str]] = {}
        self._by_market: dict[str, list[str]] = {}

    def record_attribution(self, attribution: PickAttribution) -> None:
        """Record attribution for a pick.

        Args:
            attribution: Complete attribution breakdown
        """
        self._attributions[attribution.pick_id] = attribution

        # Index by player
        if attribution.player_id not in self._by_player:
            self._by_player[attribution.player_id] = []
        self._by_player[attribution.player_id].append(attribution.pick_id)

        # Index by date
        if attribution.prediction_date not in self._by_date:
            self._by_date[attribution.prediction_date] = []
        self._by_date[attribution.prediction_date].append(attribution.pick_id)

        # Index by market type
        if attribution.market_type not in self._by_market:
            self._by_market[attribution.market_type] = []
        self._by_market[attribution.market_type].append(attribution.pick_id)

    def get_attribution(self, pick_id: str) -> PickAttribution | None:
        """Get attribution for a specific pick."""
        return self._attributions.get(pick_id)

    def get_player_attributions(self, player_id: int) -> list[PickAttribution]:
        """Get all attributions for a player."""
        pick_ids = self._by_player.get(player_id, [])
        return [self._attributions[pid] for pid in pick_ids if pid in self._attributions]

    def get_date_attributions(self, date: str) -> list[PickAttribution]:
        """Get all attributions for a specific date."""
        pick_ids = self._by_date.get(date, [])
        return [self._attributions[pid] for pid in pick_ids if pid in self._attributions]

    def analyze_projection_impact(
        self,
        result: str,  # "hit" or "miss"
        min_samples: int = 10,
    ) -> dict[str, Any]:
        """Analyze how projection strength relates to outcomes.

        Returns statistics on projection accuracy by edge size.
        """
        matching = [
            attr for attr in self._attributions.values()
        ]

        if len(matching) < min_samples:
            return {"error": f"Insufficient samples: {len(matching)} < {min_samples}"}

        # Group by projection edge buckets
        edge_buckets: dict[str, list[PickAttribution]] = {
            "weak": [],      # < 2% edge
            "moderate": [],  # 2-5% edge
            "strong": [],    # 5-10% edge
            "extreme": [],   # > 10% edge
        }

        for attr in matching:
            edge = attr.projection_edge
            if edge < 0.02:
                edge_buckets["weak"].append(attr)
            elif edge < 0.05:
                edge_buckets["moderate"].append(attr)
            elif edge < 0.10:
                edge_buckets["strong"].append(attr)
            else:
                edge_buckets["extreme"].append(attr)

        return {
            "total_samples": len(matching),
            "by_edge_bucket": {
                bucket: {
                    "count": len(attrs),
                    "avg_projection_edge": sum(a.projection_edge for a in attrs) / len(attrs) if attrs else 0,
                    "avg_confidence": sum(a.final_confidence for a in attrs) / len(attrs) if attrs else 0,
                }
                for bucket, attrs in edge_buckets.items()
            },
        }

    def analyze_penalty_effectiveness(
        self,
        result: str,
        min_samples: int = 10,
    ) -> dict[str, Any]:
        """Analyze which penalties are most predictive of outcomes.

        Returns effectiveness score for each penalty type.
        """
        matching = list(self._attributions.values())

        if len(matching) < min_samples:
            return {"error": f"Insufficient samples: {len(matching)} < {min_samples}"}

        # Aggregate penalty impacts
        penalty_stats: dict[str, dict[str, Any]] = {}

        for attr in matching:
            for penalty_type, impact in attr.penalty_impacts.items():
                if penalty_type not in penalty_stats:
                    penalty_stats[penalty_type] = {
                        "total_applications": 0,
                        "total_impact": 0.0,
                        "avg_impact": 0.0,
                    }
                penalty_stats[penalty_type]["total_applications"] += 1
                penalty_stats[penalty_type]["total_impact"] += impact

        # Compute averages
        for stats in penalty_stats.values():
            if stats["total_applications"] > 0:
                stats["avg_impact"] = stats["total_impact"] / stats["total_applications"]

        return {
            "total_samples": len(matching),
            "penalty_effectiveness": penalty_stats,
            "most_impactful": sorted(
                penalty_stats.items(),
                key=lambda x: abs(x[1]["avg_impact"]),
                reverse=True,
            )[:3],
        }

    def analyze_injury_context(
        self,
        result: str,
        min_samples: int = 10,
    ) -> dict[str, Any]:
        """Analyze how injury context affects outcomes."""
        matching = list(self._attributions.values())

        if len(matching) < min_samples:
            return {"error": f"Insufficient samples: {len(matching)} < {min_samples}"}

        # Group by injury context
        injury_categories: dict[str, list[PickAttribution]] = {
            "strong_boost": [],    # injury_boost > 0.1
            "moderate_boost": [],  # 0 < injury_boost <= 0.1
            "neutral": [],         # injury_boost = 0
            "volatility_hit": [],  # volatility penalty > 0.05
        }

        for attr in matching:
            if attr.injury_boost > 0.1:
                injury_categories["strong_boost"].append(attr)
            elif attr.injury_boost > 0:
                injury_categories["moderate_boost"].append(attr)
            elif attr.injury_volatility_penalty > 0.05:
                injury_categories["volatility_hit"].append(attr)
            else:
                injury_categories["neutral"].append(attr)

        return {
            "total_samples": len(matching),
            "by_injury_context": {
                category: {
                    "count": len(attrs),
                    "avg_boost": sum(a.injury_boost for a in attrs) / len(attrs) if attrs else 0,
                    "avg_volatility_penalty": sum(a.injury_volatility_penalty for a in attrs) / len(attrs) if attrs else 0,
                    "avg_confidence": sum(a.final_confidence for a in attrs) / len(attrs) if attrs else 0,
                }
                for category, attrs in injury_categories.items()
            },
        }

    def get_signal_correlation(
        self,
        signal: str,
        min_samples: int = 10,
    ) -> dict[str, Any]:
        """Analyze correlation between a signal and pick outcomes.

        Args:
            signal: Signal name to analyze (e.g., "injury_boost", "high_edge")
            min_samples: Minimum samples required

        Returns:
            Correlation statistics between signal presence and outcomes
        """
        with_signal = [
            attr for attr in self._attributions.values()
            if signal in attr.signals_present
        ]
        without_signal = [
            attr for attr in self._attributions.values()
            if signal not in attr.signals_present
        ]

        if len(with_signal) < min_samples:
            return {
                "signal": signal,
                "error": f"Insufficient samples with signal: {len(with_signal)} < {min_samples}",
            }

        return {
            "signal": signal,
            "with_signal_count": len(with_signal),
            "without_signal_count": len(without_signal),
            "with_signal_avg_confidence": sum(a.final_confidence for a in with_signal) / len(with_signal),
            "without_signal_avg_confidence": sum(a.final_confidence for a in without_signal) / len(without_signal) if without_signal else 0,
            "interpretation": "strong" if len(with_signal) > len(without_signal) * 0.5 else "weak",
        }

    def export_attribution_report(self) -> dict[str, Any]:
        """Export complete attribution analysis report."""
        return {
            "summary": {
                "total_picks": len(self._attributions),
                "unique_players": len(self._by_player),
                "unique_dates": len(self._by_date),
                "market_types": list(self._by_market.keys()),
            },
            "projection_analysis": self.analyze_projection_impact("hit"),
            "penalty_analysis": self.analyze_penalty_effectiveness("hit"),
            "injury_analysis": self.analyze_injury_context("hit"),
            "signal_correlations": {
                signal: self.get_signal_correlation(signal)
                for signal in ["injury_boost", "high_edge", "strong_projection"]
            },
        }
