"""Main candidate scoring orchestration module.

This module extracts the main scoring logic from runtime_scoring.py including:
- Player tier weight calculation
- Quality score computation
- Elite candidate qualification
- Complete scoring metadata application
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from courtvision.config import EliteThresholds
from courtvision.scoring import confidence
from courtvision.scoring.confidence import (
    compute_confidence,
    historical_confidence_multiplier,
)
from courtvision.scoring.edge import compute_edge, favorite_bias_factor
from courtvision.scoring.penalties import compute_penalties, to_float


def player_tier_weight(market_type: str, minutes_projection: float) -> float:
    """Compute tier weight based on player minutes projection.

    Higher weights for players with more minutes (starters vs bench).
    Non-player markets return neutral weight of 1.0.
    """
    if not str(market_type).startswith("player_"):
        return 1.0
    if minutes_projection >= 34.0:
        return 1.15
    if minutes_projection >= 28.0:
        return 1.05
    if minutes_projection >= 20.0:
        return 0.95
    return 0.75


def compute_quality_score(
    row: Mapping[str, Any],
    confidence: float,
    adjusted_edge_abs: float,
    edge_pct: float,
    tier_weight: float,
    historical_multiplier: float,
    total_penalty: float,
    under_bias_multiplier: float,
    market_type: str,
) -> float:
    """Compute the quality score for a candidate.

    Quality score combines confidence, edge, and percentage terms with
    various multipliers and penalties applied.
    """
    if market_type == "moneyline":
        quality_score = (confidence * 100.0) + (adjusted_edge_abs * 115.0)
        quality_score -= longshot_penalty_points(row.get("odds"))
    else:
        pct_term = min(edge_pct, 24.0) * 0.35
        quality_score = ((confidence * 100.0) + (adjusted_edge_abs * 8.0) + pct_term) * tier_weight

    quality_score = ((quality_score * historical_multiplier) - total_penalty) * under_bias_multiplier
    return max(0.0, quality_score)


def compute_selection_score(row: Mapping[str, Any]) -> dict[str, Any]:
    """Compute all scoring metrics for a candidate.

    This is the main entry point for scoring a single candidate row.
    Returns a dict with all computed scores, penalties, and metadata.

    Keys in returned dict:
    - edge_pct: Edge as percentage
    - player_tier_weight: Tier weight from minutes
    - favorite_bias_factor: Bias factor applied
    - historical_confidence_multiplier: Historical multiplier
    - volatility_penalty: Volatility-based penalty
    - projection_realism_penalty: Edge/confidence mismatch penalty
    - under_bias_multiplier: Under selection bias
    - elite_points_ranking_penalty: Elite ranking penalty
    - elite_points_ranking_reason: Reason for ranking penalty
    - points_recent_form_ratio: Recent form ratio
    - elite_rank_score: Elite-adjusted score
    - quality_score: Final quality score
    """
    # Lazy import to avoid circular dependency
    from courtvision.runtime_selection import elite_points_ranking_pressure

    enriched = dict(row)
    market_type = str(enriched.get("market_type", ""))
    selection = str(enriched.get("selection", "")).strip().lower()
    sportsbook_line = to_float(enriched.get("sportsbook_line")) or 0.0
    edge_abs = to_float(enriched.get("edge_abs")) or 0.0
    side_edge_pct_raw = to_float(enriched.get("side_edge_pct"))
    if side_edge_pct_raw is None:
        side_edge = to_float(enriched.get("side_edge"))
        side_edge_pct_raw = (side_edge / sportsbook_line) if side_edge is not None and sportsbook_line else None

    # Compute edge metrics
    edge_result = compute_edge(market_type, edge_abs, sportsbook_line, enriched.get("odds"))
    adjusted_edge_abs = edge_result["adjusted_edge_abs"]
    edge_pct = edge_result["edge_pct"]
    bias_factor = edge_result["bias_factor"]

    # Clamp edge to avoid extreme values affecting ranking
    edge_pct = max(min(float(edge_pct), 15.0), -5.0)

    # Compute confidence adjustments
    minutes_avg = to_float(enriched.get("minutes_avg")) or 0.0
    minutes_recent = to_float(enriched.get("minutes_recent")) or minutes_avg
    minutes_projection = max(minutes_avg, minutes_recent)
    confidence_result = compute_confidence(enriched, edge_abs, market_type, minutes_projection)
    adjusted_confidence = confidence_result["adjusted_confidence"]

    # Compute tier weight and historical multiplier
    tier_weight = player_tier_weight(market_type, minutes_projection)
    historical_multiplier = historical_confidence_multiplier(enriched)

    # Compute penalties
    penalties = compute_penalties(enriched, adjusted_edge_abs, adjusted_confidence)

    # Under bias multiplier
    under_bias_multiplier = 0.95 if selection == "under" and market_type != "moneyline" else 1.0

    # Compute quality score
    quality_score = compute_quality_score(
        enriched,
        adjusted_confidence,
        adjusted_edge_abs,
        edge_pct,
        tier_weight,
        historical_multiplier,
        penalties["total_penalty"],
        under_bias_multiplier,
        market_type,
    )

    # Elite ranking pressure
    ranking_pressure = elite_points_ranking_pressure(enriched)
    elite_rank_penalty = min(12.0, max(0.0, to_float(ranking_pressure.get("penalty")) or 0.0))
    elite_rank_score = max(0.0, quality_score - elite_rank_penalty)

    # Compute selection_score for ranking (edge + confidence + quality)
    # Compute selection_score for ranking (edge + confidence + quality)
    side_edge_component = side_edge_pct_raw if side_edge_pct_raw is not None else (float(edge_pct) / 100.0)
    if abs(float(side_edge_component)) <= 1.0:
        side_edge_component *= 100.0
    side_edge_component = max(min(float(side_edge_component), 15.0), -5.0)
    edge_component = max(float(side_edge_component), 0.0)  # Only reward positive side edge
    confidence_value = float(confidence_result["adjusted_confidence"])
    confidence_component = confidence_value * 100.0 # Scale to 0-100
    quality_component = float(quality_score)
    selection_score = (edge_component * 0.6) + (confidence_component * 0.3) + (quality_component * 0.1)

    return {
        "edge_pct": round(float(edge_pct), 4),
        "side_edge_pct": round(float(side_edge_pct_raw), 6) if side_edge_pct_raw is not None else None,
        "player_tier_weight": round(float(tier_weight), 4),
        "favorite_bias_factor": round(float(bias_factor), 4),
        "historical_confidence_multiplier": round(float(historical_multiplier), 4),
        "volatility_penalty": round(float(penalties["volatility_penalty"]), 4),
        "projection_realism_penalty": round(float(penalties["realism_penalty"]), 4),
        "under_bias_multiplier": round(float(under_bias_multiplier), 4),
        "elite_points_ranking_penalty": round(float(elite_rank_penalty), 4),
        "elite_points_ranking_reason": str(ranking_pressure.get("reason", "") or ""),
        "points_recent_form_ratio": round(float(ranking_pressure.get("recent_form_ratio", 1.0) or 1.0), 4),
        "elite_rank_score": round(float(elite_rank_score), 4),
        "quality_score": round(float(quality_score), 4),
        "selection_score": round(float(selection_score), 4),
    }


@dataclass(frozen=True, slots=True)
class CandidateScoringConfig:
    """LEGACY: Configuration thresholds for candidate scoring - runtime thresholds come from EliteThresholds.

    This dataclass exists for backward compatibility only. Runtime elite board filtering
    uses EliteThresholds from courtvision.config as the canonical source. These defaults
    are synchronized to EliteThresholds.default() to prevent confusion.
    """

    elite_min_confidence: float = field(default_factory=lambda: EliteThresholds.default().confidence)
    elite_min_quality_score: float = field(default_factory=lambda: EliteThresholds.default().quality_score)
    elite_min_player_minutes: float = field(default_factory=lambda: EliteThresholds.default().player_minutes)
    elite_min_player_edge: float = field(default_factory=lambda: EliteThresholds.default().player_edge)
    elite_min_player_confidence: float = field(default_factory=lambda: EliteThresholds.default().player_confidence)
    elite_min_moneyline_edge: float = field(default_factory=lambda: EliteThresholds.default().moneyline_edge)
    elite_min_moneyline_confidence: float = field(default_factory=lambda: EliteThresholds.default().moneyline_confidence)
    elite_max_plus_moneyline_odds: int = field(default_factory=lambda: EliteThresholds.default().max_plus_moneyline_odds)


class CandidateScoringPolicy:
    """Policy class for candidate scoring operations.

    Maintains the same interface as BoardScoringPolicy from runtime_scoring.py
    for backward compatibility while delegating to new modular functions.
    """

    def __init__(self, config: CandidateScoringConfig | None = None) -> None:
        self.config = config or CandidateScoringConfig()

    def player_tier_weight(self, market_type: str, minutes_projection: float) -> float:
        """Delegate to standalone function."""
        return player_tier_weight(market_type, minutes_projection)

    def favorite_bias_factor(self, market_type: str, sportsbook_line: float, odds: Any) -> float:
        """Delegate to edge module."""
        return favorite_bias_factor(market_type, sportsbook_line, odds)

    def edge_pct_denominator(self, market_type: str, sportsbook_line: float) -> float:
        """Delegate to edge module."""
        from courtvision.scoring.edge import edge_pct_denominator

        return edge_pct_denominator(market_type, sportsbook_line)

    def longshot_penalty_points(self, odds: Any) -> float:
        """Delegate to penalties module."""
        from courtvision.scoring.penalties import longshot_penalty_points

        return longshot_penalty_points(odds)

    def volatility_penalty_points(self, row: Mapping[str, Any]) -> float:
        """Delegate to penalties module."""
        from courtvision.scoring.penalties import volatility_penalty_points

        return volatility_penalty_points(row)

    def projection_realism_penalty_points(self, row: Mapping[str, Any], edge_abs: float, confidence: float) -> float:
        """Delegate to penalties module."""
        from courtvision.scoring.penalties import projection_realism_penalty_points

        return projection_realism_penalty_points(row, edge_abs, confidence)

    def historical_confidence_multiplier(self, row: Mapping[str, Any]) -> float:
        """Delegate to confidence module."""
        return historical_confidence_multiplier(row)

    def edge_pct_value(self, market_type: str, adjusted_edge_abs: float, sportsbook_line: float) -> float:
        """Delegate to edge module."""
        from courtvision.scoring.edge import edge_pct_value

        return edge_pct_value(market_type, adjusted_edge_abs, sportsbook_line)

    def apply_scoring_metadata(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """Apply complete scoring metadata to a candidate row.

        This is the main entry point that replaces the monolithic
        apply_scoring_metadata from runtime_scoring.py.
        """
        enriched = dict(row)

        # Ensure confidence field exists for compute_selection_score
        base_confidence = to_float(enriched.get("confidence")) or 0.0

        # Compute all scores via the new modular function
        scores = compute_selection_score(enriched)

        # Update enriched with all computed scores
        enriched.update(scores)

        # Update confidence to adjusted value
        market_type = str(enriched.get("market_type", ""))
        minutes_avg = to_float(enriched.get("minutes_avg")) or 0.0
        minutes_recent = to_float(enriched.get("minutes_recent")) or minutes_avg
        minutes_projection = max(minutes_avg, minutes_recent)
        edge_abs = to_float(enriched.get("edge_abs")) or 0.0

        confidence_result = compute_confidence(enriched, edge_abs, market_type, minutes_projection)
        enriched["confidence"] = confidence_result["adjusted_confidence"]

        return enriched

    def is_elite_candidate(self, row: Mapping[str, Any]) -> bool:
        """Determine if a candidate qualifies for elite board.

        Applies thresholds from config including special handling for:
        - Player markets (minutes, edge, confidence thresholds)
        - Moneyline markets (odds limits, edge/confidence thresholds)
        - Elite points risk guard for player_points
        """
        # Lazy import to avoid circular dependency
        from courtvision.runtime_selection import (
            passes_elite_points_risk_guard,
            passes_player_points_strong_over_calibration,
        )

        market_type = str(row.get("market_type", ""))
        confidence = to_float(row.get("confidence")) or 0.0
        quality_score = to_float(row.get("quality_score")) or 0.0
        edge_abs = to_float(row.get("edge_abs"))
        if edge_abs is None:
            edge_abs = abs(to_float(row.get("edge")) or 0.0)

        # Reject synthetic lines
        if self.to_bool(row.get("synthetic_line")):
            return False
        live_market = row.get("is_live_market")
        if live_market is not None and not self.to_bool(live_market):
            return False

        # Base thresholds
        if confidence < self.config.elite_min_confidence or quality_score < self.config.elite_min_quality_score:
            return False

        # Elite points risk guard
        if not passes_elite_points_risk_guard(row):
            return False

        # Player_points strong-OVER calibration guard (historical 41.6% hit rate
        # at edge>=+3 — block from elite/Kelly until projection recalibrated)
        if not passes_player_points_strong_over_calibration(row):
            return False

        # Player market thresholds
        if market_type.startswith("player_"):
            minutes_avg = to_float(row.get("minutes_avg")) or 0.0
            minutes_recent = to_float(row.get("minutes_recent")) or minutes_avg
            if max(minutes_avg, minutes_recent) < self.config.elite_min_player_minutes:
                return False
            if edge_abs < self.config.elite_min_player_edge:
                return False
            if confidence < self.config.elite_min_player_confidence:
                return False

            # Higher thresholds for steals/blocks
            if market_type in {"player_steals", "player_blocks"}:
                if edge_abs < max(self.config.elite_min_player_edge, 0.75):
                    return False
                if confidence < max(self.config.elite_min_player_confidence, 0.72):
                    return False

        # Moneyline thresholds
        if market_type == "moneyline":
            odds_value = to_float(row.get("odds"))
            if edge_abs < self.config.elite_min_moneyline_edge:
                return False
            if confidence < self.config.elite_min_moneyline_confidence:
                return False
            if odds_value is not None and odds_value > self.config.elite_max_plus_moneyline_odds:
                return False

        return True

    @staticmethod
    def to_bool(value: Any) -> bool:
        """Safely convert a value to bool."""
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return value != 0
        return str(value).strip().lower() in {"1", "true", "yes", "y"}


def longshot_penalty_points(odds: Any) -> float:
    """Import from penalties module for local use."""
    from courtvision.scoring.penalties import longshot_penalty_points

    return longshot_penalty_points(odds)
