"""Expected value (EV) calculation based on odds and hit probability.

Ranks plays by EV rather than just edge, providing a more complete
picture of expected return.

Phase 9: Scenario Simulation and Forward Validation
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EVResult:
    """Expected value calculation result."""

    player_name: str
    stat_type: str
    line_value: float
    projection: float
    edge: float

    # Odds
    american_odds: int
    decimal_odds: float
    implied_probability: float

    # Simulation results
    hit_probability: float
    simulated_edge: float  # Edge from simulation vs implied prob

    # EV calculation
    expected_value: float  # EV as percentage
    ev_rating: str  # positive, negative, neutral

    # Kelly criterion
    kelly_fraction: float  # Optimal bet size as % of bankroll
    half_kelly: float  # Conservative bet size

    # Comparison
    model_vs_market: str  # whether model beats market
    confidence_score: float  # 0-1 confidence in EV estimate

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "player": self.player_name,
            "stat": self.stat_type,
            "line": self.line_value,
            "projection": round(self.projection, 1),
            "edge": round(self.edge, 3),
            "odds": {
                "american": self.american_odds,
                "decimal": round(self.decimal_odds, 2),
                "implied_prob": round(self.implied_probability, 3),
            },
            "simulation": {
                "hit_prob": round(self.hit_probability, 3),
                "simulated_edge": round(self.simulated_edge, 3),
            },
            "ev": {
                "value": round(self.expected_value, 3),
                "rating": self.ev_rating,
            },
            "kelly": {
                "full": round(self.kelly_fraction, 3),
                "half": round(self.half_kelly, 3),
            },
            "confidence": round(self.confidence_score, 3),
        }


class OddsConverter:
    """Convert between different odds formats."""

    @staticmethod
    def american_to_decimal(american_odds: int) -> float:
        """Convert American odds to decimal odds.

        Args:
            american_odds: American odds (e.g., -110, +150)

        Returns:
            Decimal odds (e.g., 1.91, 2.50)
        """
        if american_odds > 0:
            return (american_odds / 100) + 1
        else:
            return (100 / abs(american_odds)) + 1

    @staticmethod
    def decimal_to_american(decimal_odds: float) -> int:
        """Convert decimal odds to American odds.

        Args:
            decimal_odds: Decimal odds (e.g., 1.91, 2.50)

        Returns:
            American odds (e.g., -110, +150)
        """
        if decimal_odds >= 2.0:
            return int((decimal_odds - 1) * 100)
        else:
            return int(-100 / (decimal_odds - 1))

    @staticmethod
    def american_to_implied_probability(american_odds: int) -> float:
        """Convert American odds to implied probability.

        Args:
            american_odds: American odds

        Returns:
            Implied probability (0-1)
        """
        if american_odds > 0:
            return 100 / (american_odds + 100)
        else:
            return abs(american_odds) / (abs(american_odds) + 100)

    @staticmethod
    def decimal_to_implied_probability(decimal_odds: float) -> float:
        """Convert decimal odds to implied probability.

        Args:
            decimal_odds: Decimal odds

        Returns:
            Implied probability (0-1)
        """
        return 1 / decimal_odds


class EVCalculator:
    """Calculate expected value for betting plays.

    Uses simulation-based hit probability and market odds to compute
    expected return and optimal bet sizing.
    """

    # EV thresholds for rating
    POSITIVE_EV_THRESHOLD = 0.05  # 5% expected return
    STRONG_EV_THRESHOLD = 0.10    # 10% expected return

    def __init__(self, vig_adjustment: float = 0.025) -> None:
        """Initialize EV calculator.

        Args:
            vig_adjustment: Vig adjustment factor (default 2.5%)
        """
        self.vig_adjustment = vig_adjustment
        self.converter = OddsConverter()

    def calculate_ev(
        self,
        player_name: str,
        stat_type: str,
        line_value: float,
        projection: float,
        edge: float,
        hit_probability: float,
        american_odds: int,
        confidence_in_hit_prob: float = 0.8,
    ) -> EVResult:
        """Calculate expected value for a play.

        Args:
            player_name: Player name
            stat_type: Type of stat
            line_value: Betting line
            projection: Model projection
            edge: Model edge vs line
            hit_probability: Simulated hit probability (0-1)
            american_odds: American odds for the play
            confidence_in_hit_prob: Confidence in hit probability estimate (0-1)

        Returns:
            EVResult with all calculations
        """
        # Convert odds
        decimal_odds = self.converter.american_to_decimal(american_odds)
        implied_probability = self.converter.american_to_implied_probability(american_odds)

        # Adjust implied probability for vig
        adjusted_implied = implied_probability * (1 - self.vig_adjustment)

        # Calculate simulated edge
        simulated_edge = hit_probability - adjusted_implied

        # Calculate EV
        # EV = (Probability of Win * Amount Won) - (Probability of Loss * Amount Lost)
        # For a $1 bet at decimal odds:
        # Amount Won = decimal_odds - 1
        # Amount Lost = 1
        amount_won = decimal_odds - 1
        amount_lost = 1.0

        ev = (hit_probability * amount_won) - ((1 - hit_probability) * amount_lost)
        ev_percentage = ev  # Already as percentage of bet

        # Determine EV rating
        if ev_percentage >= self.STRONG_EV_THRESHOLD:
            ev_rating = "strong_positive"
        elif ev_percentage >= self.POSITIVE_EV_THRESHOLD:
            ev_rating = "positive"
        elif ev_percentage > -self.POSITIVE_EV_THRESHOLD:
            ev_rating = "neutral"
        else:
            ev_rating = "negative"

        # Calculate Kelly criterion
        # Kelly = (bp - q) / b
        # where b = odds received (decimal - 1), p = prob of win, q = prob of loss
        b = decimal_odds - 1
        p = hit_probability
        q = 1 - p

        kelly = (b * p - q) / b if b > 0 else 0
        kelly = max(0, kelly)  # Don't suggest negative bets

        # Model vs market assessment
        if simulated_edge > 0.10:
            model_vs_market = "strong_edge"
        elif simulated_edge > 0.05:
            model_vs_market = "moderate_edge"
        elif simulated_edge > 0:
            model_vs_market = "slight_edge"
        else:
            model_vs_market = "no_edge"

        # Confidence score combines hit prob confidence and edge magnitude
        confidence_score = confidence_in_hit_prob * min(1.0, abs(simulated_edge) * 5)

        return EVResult(
            player_name=player_name,
            stat_type=stat_type,
            line_value=line_value,
            projection=projection,
            edge=edge,
            american_odds=american_odds,
            decimal_odds=decimal_odds,
            implied_probability=implied_probability,
            hit_probability=hit_probability,
            simulated_edge=simulated_edge,
            expected_value=ev_percentage,
            ev_rating=ev_rating,
            kelly_fraction=kelly,
            half_kelly=kelly / 2,
            model_vs_market=model_vs_market,
            confidence_score=confidence_score,
        )

    def rank_by_ev(
        self,
        ev_results: list[EVResult],
        min_ev: float = 0.0,
        min_confidence: float = 0.5,
    ) -> list[EVResult]:
        """Rank plays by expected value.

        Args:
            ev_results: List of EV results
            min_ev: Minimum EV threshold
            min_confidence: Minimum confidence threshold

        Returns:
            Sorted list of EV results (highest EV first)
        """
        # Filter by thresholds
        filtered = [
            r for r in ev_results
            if r.expected_value >= min_ev and r.confidence_score >= min_confidence
        ]

        # Sort by EV (descending)
        sorted_results = sorted(filtered, key=lambda r: r.expected_value, reverse=True)

        return sorted_results

    def find_best_bets(
        self,
        ev_results: list[EVResult],
        top_n: int = 10,
        min_ev: float = 0.05,
    ) -> list[EVResult]:
        """Find the best betting opportunities.

        Args:
            ev_results: List of EV results
            top_n: Number of top bets to return
            min_ev: Minimum EV threshold

        Returns:
            List of top EV opportunities
        """
        # Filter for positive EV
        positive_ev = [r for r in ev_results if r.expected_value >= min_ev]

        # Sort by EV, then by confidence
        sorted_results = sorted(
            positive_ev,
            key=lambda r: (r.expected_value, r.confidence_score),
            reverse=True,
        )

        return sorted_results[:top_n]

    def calculate_portfolio_ev(
        self,
        ev_results: list[EVResult],
        bet_sizes: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Calculate portfolio-level expected value.

        Args:
            ev_results: List of EV results for portfolio
            bet_sizes: Dict mapping player_name to bet size (fraction of bankroll)

        Returns:
            Portfolio metrics
        """
        if bet_sizes is None:
            # Use half-Kelly for all
            bet_sizes = {r.player_name: r.half_kelly for r in ev_results}

        total_ev = 0.0
        total_risk = 0.0
        weighted_confidence = 0.0

        for result in ev_results:
            size = bet_sizes.get(result.player_name, 0)
            total_ev += result.expected_value * size
            total_risk += size
            weighted_confidence += result.confidence_score * size

        avg_ev = total_ev / total_risk if total_risk > 0 else 0
        avg_confidence = weighted_confidence / total_risk if total_risk > 0 else 0

        return {
            "total_plays": len(ev_results),
            "total_risk": round(total_risk, 3),
            "portfolio_ev": round(avg_ev, 4),
            "avg_confidence": round(avg_confidence, 3),
            "expected_return": round(avg_ev * 100, 2),  # As percentage
            "assessment": (
                "profitable" if avg_ev > 0.05
                else "marginal" if avg_ev > 0
                else "unprofitable"
            ),
        }

    def get_ev_summary(
        self,
        ev_results: list[EVResult],
    ) -> dict[str, Any]:
        """Get summary statistics for a set of EV calculations."""
        if not ev_results:
            return {"error": "No EV results provided"}

        evs = [r.expected_value for r in ev_results]
        confidences = [r.confidence_score for r in ev_results]

        positive_ev = sum(1 for e in evs if e > 0)
        strong_ev = sum(1 for e in evs if e > self.STRONG_EV_THRESHOLD)

        return {
            "total_plays": len(ev_results),
            "positive_ev_count": positive_ev,
            "strong_ev_count": strong_ev,
            "positive_ev_rate": round(positive_ev / len(ev_results), 3),
            "ev_statistics": {
                "mean": round(sum(evs) / len(evs), 4),
                "min": round(min(evs), 4),
                "max": round(max(evs), 4),
                "median": round(sorted(evs)[len(evs) // 2], 4),
            },
            "confidence_statistics": {
                "mean": round(sum(confidences) / len(confidences), 3),
                "min": round(min(confidences), 3),
                "max": round(max(confidences), 3),
            },
            "top_ev_play": max(ev_results, key=lambda r: r.expected_value).to_dict(),
        }
