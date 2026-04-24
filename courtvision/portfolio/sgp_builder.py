"""SGP (Same Game Parlay) builder with intentional correlation.

Builds correlated parlays intentionally and avoids accidental correlation.

Phase 10: Correlation and Portfolio Optimization
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

from courtvision.portfolio.correlation import CorrelationDetector, CorrelationMatrix, CorrelationType


class SGPType(str, Enum):
    """Types of Same Game Parlays."""

    PLAYER_MULTI_STAT = "player_multi_stat"  # Same player, different stats
    TEAM_MULTI_PLAYER = "team_multi_player"  # Same team, different players
    GAME_MULTI_PLAYER = "game_multi_player"  # Any players in same game
    CROSS_STAT_CORRELATED = "cross_stat_correlated"  # Points + Assists, etc.
    CROSS_STAT_UNCORRELATED = "cross_stat_uncorrelated"  # Points + Rebounds


@dataclass
class SGPParlay:
    """A Same Game Parlay."""

    parlay_id: str
    legs: list[str]  # Play IDs
    game_id: str
    sgp_type: SGPType

    # Calculated metrics
    combined_odds: float  # Decimal odds
    combined_implied_prob: float
    combined_hit_prob: float  # Adjusted for correlation
    ev: float

    # Correlation info
    avg_correlation: float
    correlation_explanation: str = ""

    # Status
    is_viable: bool = True
    rejection_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "parlay_id": self.parlay_id,
            "game_id": self.game_id,
            "type": self.sgp_type.value,
            "legs": self.legs,
            "num_legs": len(self.legs),
            "odds": {
                "combined": round(self.combined_odds, 2),
                "implied_prob": round(self.combined_implied_prob, 3),
            },
            "probability": {
                "uncorrelated": round(
                    np.prod([1 / self.combined_odds] * len(self.legs)), 3
                ) if self.combined_odds > 0 else 0,
                "correlation_adjusted": round(self.combined_hit_prob, 3),
            },
            "ev": round(self.ev, 3),
            "correlation": {
                "avg": round(self.avg_correlation, 3),
                "explanation": self.correlation_explanation,
            },
            "status": {
                "viable": self.is_viable,
                "rejection": self.rejection_reason if not self.is_viable else None,
            },
        }


class SGPBuilder:
    """Build Same Game Parlays with correlation awareness.

    SGPs can be profitable when:
    1. Legs are positively correlated (one hitting increases odds of other)
    2. Correlation is not fully priced into odds

    SGPs are dangerous when:
    1. Legs are negatively correlated
    2. Correlation is overpriced
    """

    # Maximum legs per SGP
    MAX_LEGS = 4

    # Minimum correlation for "intentional" correlation
    MIN_POSITIVE_CORRELATION = 0.3

    # Maximum correlation for "safe" diversification
    MAX_NEGATIVE_CORRELATION = -0.1

    # Vig estimate for SGPs (higher than single bets)
    SGP_VIG = 0.08  # 8% vig on SGPs vs 4% on singles

    def __init__(self, correlation_detector: CorrelationDetector | None = None) -> None:
        """Initialize SGP builder.

        Args:
            correlation_detector: Correlation detector for leg analysis
        """
        self.correlation_detector = correlation_detector or CorrelationDetector()
        self.parlays: list[SGPParlay] = []

    def build_player_multi_stat_parlay(
        self,
        player_name: str,
        game_id: str,
        stat_legs: list[tuple[str, float, float]],  # (stat_type, line, odds)
        max_legs: int = 3,
    ) -> SGPParlay | None:
        """Build parlay with multiple stats from same player.

        Args:
            player_name: Player name
            game_id: Game ID
            stat_legs: List of (stat_type, line, odds) tuples
            max_legs: Maximum legs to include

        Returns:
            SGPParlay or None if not viable
        """
        if len(stat_legs) < 2:
            return None

        # Take top legs up to max
        selected_legs = stat_legs[:max_legs]

        # Generate leg IDs
        legs = [f"{player_name}_{stat}_{line}" for stat, line, _ in selected_legs]

        # Combine odds
        combined_odds = np.prod([odds for _, _, odds in selected_legs])

        # Calculate correlation - same player stats are highly correlated
        avg_correlation = 0.6  # Strong positive for same player

        # Adjust hit probability for correlation
        uncorrelated_prob = 1 / combined_odds
        correlated_prob = uncorrelated_prob * (1 + avg_correlation * 0.5)

        # Simple EV calculation
        ev = correlated_prob * combined_odds - 1

        return SGPParlay(
            parlay_id=f"SGP_{player_name}_multi_{game_id}",
            legs=legs,
            game_id=game_id,
            sgp_type=SGPType.PLAYER_MULTI_STAT,
            combined_odds=combined_odds,
            combined_implied_prob=1 / combined_odds,
            combined_hit_prob=correlated_prob,
            ev=ev,
            avg_correlation=avg_correlation,
            correlation_explanation=f"Same player ({player_name}) multiple stats - highly correlated",
            is_viable=ev > 0 and avg_correlation > 0,
        )

    def evaluate_sgp(
        self,
        legs: list[str],
        game_id: str,
        individual_odds: list[float],
        correlation_matrix: CorrelationMatrix | None = None,
    ) -> SGPParlay:
        """Evaluate a potential SGP.

        Args:
            legs: List of play IDs
            game_id: Game ID
            individual_odds: Odds for each leg (decimal)
            correlation_matrix: Correlation matrix for legs

        Returns:
            SGPParlay with evaluation
        """
        num_legs = len(legs)

        if num_legs < 2:
            return SGPParlay(
                parlay_id="invalid",
                legs=legs,
                game_id=game_id,
                sgp_type=SGPType.GAME_MULTI_PLAYER,
                combined_odds=0,
                combined_implied_prob=0,
                combined_hit_prob=0,
                ev=0,
                avg_correlation=0,
                is_viable=False,
                rejection_reason="Need at least 2 legs for SGP",
            )

        if num_legs > self.MAX_LEGS:
            return SGPParlay(
                parlay_id="invalid",
                legs=legs,
                game_id=game_id,
                sgp_type=SGPType.GAME_MULTI_PLAYER,
                combined_odds=0,
                combined_implied_prob=0,
                combined_hit_prob=0,
                ev=0,
                avg_correlation=0,
                is_viable=False,
                rejection_reason=f"Too many legs (max {self.MAX_LEGS})",
            )

        # Combine odds
        combined_odds = np.prod(individual_odds)
        implied_prob = 1 / combined_odds

        # Calculate average correlation
        avg_correlation = 0.0
        correlations_found = 0

        if correlation_matrix:
            for i, leg_1 in enumerate(legs):
                for leg_2 in legs[i + 1:]:
                    corr = correlation_matrix.get_correlation(leg_1, leg_2)
                    avg_correlation += corr
                    correlations_found += 1

        if correlations_found > 0:
            avg_correlation /= correlations_found

        # Adjust hit probability based on correlation
        if avg_correlation > 0:
            # Positive correlation increases joint probability
            adjusted_prob = implied_prob * (1 + avg_correlation * 0.4)
        elif avg_correlation < 0:
            # Negative correlation decreases joint probability
            adjusted_prob = implied_prob * (1 + avg_correlation * 0.3)
        else:
            adjusted_prob = implied_prob

        # Apply SGP vig
        vig_adjusted_prob = adjusted_prob * (1 - self.SGP_VIG)

        # Calculate EV
        ev = vig_adjusted_prob * combined_odds - 1

        # Determine viability
        is_viable = ev > -0.1  # Allow slightly negative for correlation plays

        rejection_reason = ""
        if avg_correlation < self.MAX_NEGATIVE_CORRELATION:
            is_viable = False
            rejection_reason = f"Negative correlation ({avg_correlation:.2f}) reduces parlay value"
        elif ev < -0.2:
            is_viable = False
            rejection_reason = f"EV too negative ({ev:.2f})"

        # Determine SGP type
        if avg_correlation > self.MIN_POSITIVE_CORRELATION:
            sgp_type = SGPType.CROSS_STAT_CORRELATED
        else:
            sgp_type = SGPType.CROSS_STAT_UNCORRELATED

        return SGPParlay(
            parlay_id=f"SGP_{game_id}_{'_'.join(legs[:2])}",
            legs=legs,
            game_id=game_id,
            sgp_type=sgp_type,
            combined_odds=combined_odds,
            combined_implied_prob=implied_prob,
            combined_hit_prob=vig_adjusted_prob,
            ev=ev,
            avg_correlation=avg_correlation,
            correlation_explanation=self._explain_correlation(avg_correlation),
            is_viable=is_viable,
            rejection_reason=rejection_reason,
        )

    def find_best_sgps(
        self,
        plays: list[dict],
        correlation_matrix: CorrelationMatrix | None = None,
        max_sgps: int = 5,
        min_ev: float = -0.05,
    ) -> list[SGPParlay]:
        """Find best SGP opportunities from available plays.

        Args:
            plays: List of play dicts with keys: play_id, game_id, odds, stat_type, player_name
            correlation_matrix: Correlation matrix
            max_sgps: Maximum number of SGPs to return
            min_ev: Minimum EV threshold

        Returns:
            List of viable SGPs
        """
        viable_sgps: list[SGPParlay] = []

        # Group plays by game
        games: dict[str, list[dict]] = {}
        for play in plays:
            game_id = play.get("game_id", "")
            if game_id not in games:
                games[game_id] = []
            games[game_id].append(play)

        # Look for SGP opportunities in each game
        for game_id, game_plays in games.items():
            if len(game_plays) < 2:
                continue

            # Try 2-leg combinations
            for i, play_1 in enumerate(game_plays):
                for play_2 in game_plays[i + 1:]:
                    legs = [play_1["play_id"], play_2["play_id"]]
                    odds = [play_1["odds"], play_2["odds"]]

                    sgp = self.evaluate_sgp(legs, game_id, odds, correlation_matrix)

                    if sgp.is_viable and sgp.ev >= min_ev:
                        viable_sgps.append(sgp)

            # Try 3-leg combinations if enough plays
            if len(game_plays) >= 3:
                for i, play_1 in enumerate(game_plays):
                    for j, play_2 in enumerate(game_plays[i + 1:], i + 1):
                        for play_3 in game_plays[j + 1:]:
                            legs = [play_1["play_id"], play_2["play_id"], play_3["play_id"]]
                            odds = [play_1["odds"], play_2["odds"], play_3["odds"]]

                            sgp = self.evaluate_sgp(legs, game_id, odds, correlation_matrix)

                            if sgp.is_viable and sgp.ev >= min_ev:
                                viable_sgps.append(sgp)

        # Sort by EV
        viable_sgps.sort(key=lambda s: s.ev, reverse=True)

        return viable_sgps[:max_sgps]

    def _explain_correlation(self, correlation: float) -> str:
        """Generate human-readable correlation explanation."""
        if correlation > 0.6:
            return f"Very strong positive correlation ({correlation:.2f}) - legs highly likely to hit together"
        elif correlation > 0.3:
            return f"Moderate positive correlation ({correlation:.2f}) - legs somewhat likely to hit together"
        elif correlation > 0.1:
            return f"Weak positive correlation ({correlation:.2f})"
        elif correlation > -0.1:
            return f"Near independent ({correlation:.2f})"
        elif correlation > -0.3:
            return f"Weak negative correlation ({correlation:.2f})"
        else:
            return f"Negative correlation ({correlation:.2f}) - legs tend to offset"

    def avoid_accidental_correlation(
        self,
        selected_legs: list[str],
        available_legs: list[str],
        correlation_matrix: CorrelationMatrix,
        max_correlation: float = 0.4,
    ) -> list[str]:
        """Filter legs to avoid accidental high correlation.

        Args:
            selected_legs: Already selected legs
            available_legs: Available legs to consider
            correlation_matrix: Correlation matrix
            max_correlation: Maximum allowed correlation with selected legs

        Returns:
            Filtered list of legs that don't create accidental correlation
        """
        safe_legs = []

        for leg in available_legs:
            # Check correlation with all selected legs
            max_corr = max(
                correlation_matrix.get_correlation(leg, selected)
                for selected in selected_legs
            )

            if max_corr <= max_correlation:
                safe_legs.append(leg)

        return safe_legs

    def get_sgp_summary(self, parlays: list[SGPParlay]) -> dict[str, Any]:
        """Get summary of SGP opportunities."""
        if not parlays:
            return {"message": "No viable SGPs found"}

        viable = [p for p in parlays if p.is_viable]
        avg_ev = sum(p.ev for p in viable) / len(viable) if viable else 0

        return {
            "total_evaluated": len(parlays),
            "viable": len(viable),
            "avg_ev": round(avg_ev, 3),
            "best_sgp": max(viable, key=lambda p: p.ev).to_dict() if viable else None,
            "by_type": {
                sgp_type.value: len([p for p in viable if p.sgp_type == sgp_type])
                for sgp_type in SGPType
            },
        }
