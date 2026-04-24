"""Runtime scoring module (legacy compatibility layer).

This module now delegates to the courtvision.scoring package.
The BoardScoringConfig and BoardScoringPolicy classes are preserved
for backward compatibility.

Migration Phase 2 Note:
- New code should import directly from courtvision.scoring
- This module is kept for existing callers during transition
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

# runtime_selection imports are done lazily in methods to avoid circular imports
from courtvision.scoring.candidate_scoring import (
    CandidateScoringConfig,
    CandidateScoringPolicy,
    compute_selection_score,
    player_tier_weight,
)
from courtvision.scoring.confidence import (
    compute_confidence,
    historical_confidence_multiplier,
    player_points_scoring_stability,
)
from courtvision.scoring.edge import (
    compute_edge,
    edge_pct_denominator,
    edge_pct_value,
    favorite_bias_factor,
)
from courtvision.scoring.penalties import (
    compute_penalties,
    longshot_penalty_points,
    projection_realism_penalty_points,
    to_float,
    volatility_penalty_points,
)

# Re-export for backward compatibility
__all__ = ["BoardScoringConfig", "BoardScoringPolicy"]


@dataclass(frozen=True, slots=True)
class BoardScoringConfig:
    """Configuration thresholds for board scoring (legacy alias for CandidateScoringConfig).

    Maintains exact same interface as before for backward compatibility.
    """

    elite_min_confidence: float = 0.65
    elite_min_quality_score: float = 82.0
    elite_min_player_minutes: float = 24.0
    elite_min_player_edge: float = 1.5
    elite_min_player_confidence: float = 0.65
    elite_min_moneyline_edge: float = 0.06
    elite_min_moneyline_confidence: float = 0.70
    elite_max_plus_moneyline_odds: int = 300


class BoardScoringPolicy:
    """Policy for board scoring operations (legacy compatibility wrapper).

    This class delegates to the new courtvision.scoring modules while
    maintaining the exact same public interface for backward compatibility.
    """

    def __init__(self, config: BoardScoringConfig | None = None) -> None:
        # Convert legacy config to new config format
        legacy_config = config or BoardScoringConfig()
        self.config = legacy_config
        # Create underlying new policy for delegation where appropriate
        new_config = CandidateScoringConfig(
            elite_min_confidence=legacy_config.elite_min_confidence,
            elite_min_quality_score=legacy_config.elite_min_quality_score,
            elite_min_player_minutes=legacy_config.elite_min_player_minutes,
            elite_min_player_edge=legacy_config.elite_min_player_edge,
            elite_min_player_confidence=legacy_config.elite_min_player_confidence,
            elite_min_moneyline_edge=legacy_config.elite_min_moneyline_edge,
            elite_min_moneyline_confidence=legacy_config.elite_min_moneyline_confidence,
            elite_max_plus_moneyline_odds=legacy_config.elite_max_plus_moneyline_odds,
        )
        self._new_policy = CandidateScoringPolicy(config=new_config)

    def player_tier_weight(self, market_type: str, minutes_projection: float) -> float:
        """Delegate to scoring module."""
        return player_tier_weight(market_type, minutes_projection)

    def favorite_bias_factor(self, market_type: str, sportsbook_line: float, odds: Any) -> float:
        """Delegate to edge module."""
        return favorite_bias_factor(market_type, sportsbook_line, odds)

    def edge_pct_denominator(self, market_type: str, sportsbook_line: float) -> float:
        """Delegate to edge module."""
        return edge_pct_denominator(market_type, sportsbook_line)

    def longshot_penalty_points(self, odds: Any) -> float:
        """Delegate to penalties module."""
        return longshot_penalty_points(odds)

    def volatility_penalty_points(self, row: Mapping[str, Any]) -> float:
        """Delegate to penalties module."""
        return volatility_penalty_points(row)

    def projection_realism_penalty_points(self, row: Mapping[str, Any], edge_abs: float, confidence: float) -> float:
        """Delegate to penalties module."""
        return projection_realism_penalty_points(row, edge_abs, confidence)

    def historical_confidence_multiplier(self, row: Mapping[str, Any]) -> float:
        """Delegate to confidence module."""
        return historical_confidence_multiplier(row)

    def player_points_scoring_stability(self, row: Mapping[str, Any], sportsbook_line: float) -> float:
        """Delegate to confidence module."""
        return player_points_scoring_stability(row, sportsbook_line)

    def edge_pct_value(self, market_type: str, adjusted_edge_abs: float, sportsbook_line: float) -> float:
        """Delegate to edge module."""
        return edge_pct_value(market_type, adjusted_edge_abs, sportsbook_line)

    def apply_scoring_metadata(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """Apply complete scoring metadata to a candidate row.

        Delegates to compute_selection_score and integrates with the legacy format.
        """
        # Lazy import to avoid circular dependency
        from courtvision.runtime_selection import elite_points_ranking_pressure

        enriched = dict(row)
        market_type = str(enriched.get("market_type", ""))
        selection = str(enriched.get("selection", "")).strip().lower()
        sportsbook_line = to_float(enriched.get("sportsbook_line")) or 0.0
        edge_abs = to_float(enriched.get("edge_abs")) or 0.0
        confidence = to_float(enriched.get("confidence")) or 0.0

        # Edge-based confidence adjustments (mirrors original logic)
        if edge_abs is not None:
            if edge_abs > 6.0:
                confidence += 0.04
            elif edge_abs > 4.5:
                confidence += 0.02
        confidence = min(confidence, 0.99)

        minutes_avg = to_float(enriched.get("minutes_avg")) or 0.0
        minutes_recent = to_float(enriched.get("minutes_recent")) or minutes_avg
        minutes_projection = max(minutes_avg, minutes_recent)

        # Minutes-based floor for player props
        if market_type.startswith("player_"):
            if minutes_projection >= 28.0:
                confidence = max(confidence, 0.58)

        # Compute via new modular functions for exact compatibility
        tier_weight = player_tier_weight(market_type, minutes_projection)
        bias_factor = favorite_bias_factor(market_type, sportsbook_line, enriched.get("odds"))
        adjusted_edge_abs = edge_abs * bias_factor
        edge_pct = edge_pct_value(market_type, adjusted_edge_abs, sportsbook_line)
        historical_multiplier = historical_confidence_multiplier(enriched)
        volatility_penalty = volatility_penalty_points(enriched)
        realism_penalty = projection_realism_penalty_points(enriched, adjusted_edge_abs, confidence)
        under_bias_multiplier = 0.95 if selection == "under" and market_type != "moneyline" else 1.0

        # Quality score calculation (exact copy of original logic)
        if market_type == "moneyline":
            quality_score = (confidence * 100.0) + (adjusted_edge_abs * 115.0)
            quality_score -= longshot_penalty_points(enriched.get("odds"))
        else:
            pct_term = min(edge_pct, 24.0) * 0.35
            quality_score = ((confidence * 100.0) + (adjusted_edge_abs * 8.0) + pct_term) * tier_weight

        total_penalty = volatility_penalty + realism_penalty
        total_penalty = min(total_penalty, 12.0)  # prevent over-penalization

        quality_score = ((quality_score * historical_multiplier) - total_penalty) * under_bias_multiplier
        ranking_pressure = elite_points_ranking_pressure(enriched)
        elite_rank_penalty = min(12.0, max(0.0, to_float(ranking_pressure.get("penalty")) or 0.0))
        elite_rank_score = max(0.0, quality_score - elite_rank_penalty)

        enriched["edge_pct"] = round(float(edge_pct), 4)
        enriched["player_tier_weight"] = round(float(tier_weight), 4)
        enriched["favorite_bias_factor"] = round(float(bias_factor), 4)
        enriched["historical_confidence_multiplier"] = round(float(historical_multiplier), 4)
        enriched["volatility_penalty"] = round(float(volatility_penalty), 4)
        enriched["projection_realism_penalty"] = round(float(realism_penalty), 4)
        enriched["under_bias_multiplier"] = round(float(under_bias_multiplier), 4)
        enriched["elite_points_ranking_penalty"] = round(float(elite_rank_penalty), 4)
        enriched["elite_points_ranking_reason"] = str(ranking_pressure.get("reason", "") or "")
        enriched["points_recent_form_ratio"] = round(float(ranking_pressure.get("recent_form_ratio", 1.0) or 1.0), 4)
        enriched["elite_rank_score"] = round(float(elite_rank_score), 4)
        enriched["quality_score"] = round(float(max(0.0, quality_score)), 4)
        return enriched

    def is_elite_candidate(self, row: Mapping[str, Any]) -> bool:
        """Determine if a candidate qualifies for elite board.

        Maintains exact same logic as original for backward compatibility.
        """
        # Lazy import to avoid circular dependency
        from courtvision.runtime_selection import passes_elite_points_risk_guard

        market_type = str(row.get("market_type", ""))
        confidence = to_float(row.get("confidence")) or 0.0
        quality_score = to_float(row.get("quality_score")) or 0.0
        edge_abs = to_float(row.get("edge_abs"))
        if edge_abs is None:
            edge_abs = abs(to_float(row.get("edge")) or 0.0)

        if self.to_bool(row.get("synthetic_line")):
            return False
        live_market = row.get("is_live_market")
        if live_market is not None and not self.to_bool(live_market):
            return False

        if confidence < self.config.elite_min_confidence or quality_score < self.config.elite_min_quality_score:
            return False

        if not passes_elite_points_risk_guard(row):
            return False

        if market_type.startswith("player_"):
            minutes_avg = to_float(row.get("minutes_avg")) or 0.0
            minutes_recent = to_float(row.get("minutes_recent")) or minutes_avg
            if max(minutes_avg, minutes_recent) < self.config.elite_min_player_minutes:
                return False
            if edge_abs < self.config.elite_min_player_edge:
                return False
            if confidence < self.config.elite_min_player_confidence:
                return False
            if market_type in {"player_steals", "player_blocks"}:
                if edge_abs < max(self.config.elite_min_player_edge, 0.75):
                    return False
                if confidence < max(self.config.elite_min_player_confidence, 0.72):
                    return False

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
    def to_float(value: Any) -> float | None:
        """Safely convert a value to float."""
        return to_float(value)

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
