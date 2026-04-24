"""Adaptive strategy for dynamic threshold and edge requirement adjustment.

Adjusts strategy based on market conditions:
- Aggressiveness
- Thresholds
- Edge requirements

Phase 11: Market Adaptation and Opponent Modeling
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from courtvision.market_intelligence.bias_detection import BiasDetector
from courtvision.market_intelligence.clv_tracker import CLVTracker
from courtvision.market_intelligence.line_movement import LineMovementAnalyzer
from courtvision.market_intelligence.reaction_modeling import ReactionDetector


@dataclass
class AdaptiveConfig:
    """Adaptive configuration for strategy adjustment."""

    # Base thresholds
    base_edge_threshold: float = 0.05
    base_confidence_threshold: float = 0.65
    base_ev_threshold: float = 0.03

    # Aggressiveness (0 = conservative, 1 = aggressive)
    aggressiveness: float = 0.5

    # Adjusted thresholds (computed)
    current_edge_threshold: float = field(default=0.05)
    current_confidence_threshold: float = field(default=0.65)
    current_ev_threshold: float = field(default=0.03)

    # Market condition adjustments
    bias_adjustment: float = 0.0
    clv_adjustment: float = 0.0
    reaction_adjustment: float = 0.0

    def __post_init__(self) -> None:
        """Compute current thresholds."""
        self.current_edge_threshold = self._compute_adjusted(
            self.base_edge_threshold,
            [self.bias_adjustment, self.clv_adjustment, self.reaction_adjustment]
        )
        self.current_confidence_threshold = self._compute_adjusted(
            self.base_confidence_threshold,
            [self.bias_adjustment * 0.5, self.clv_adjustment * 0.5]
        )
        self.current_ev_threshold = self._compute_adjusted(
            self.base_ev_threshold,
            [self.bias_adjustment * 0.8, self.clv_adjustment * 0.8]
        )

    def _compute_adjusted(
        self,
        base: float,
        adjustments: list[float],
    ) -> float:
        """Compute adjusted threshold."""
        total_adjustment = sum(adjustments)
        # Aggressiveness affects direction
        adjusted = base * (1 - total_adjustment * self.aggressiveness)
        return max(0.01, adjusted)  # Minimum threshold

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "base_thresholds": {
                "edge": self.base_edge_threshold,
                "confidence": self.base_confidence_threshold,
                "ev": self.base_ev_threshold,
            },
            "aggressiveness": self.aggressiveness,
            "adjustments": {
                "bias": round(self.bias_adjustment, 3),
                "clv": round(self.clv_adjustment, 3),
                "reaction": round(self.reaction_adjustment, 3),
            },
            "current_thresholds": {
                "edge": round(self.current_edge_threshold, 3),
                "confidence": round(self.current_confidence_threshold, 3),
                "ev": round(self.current_ev_threshold, 3),
            },
        }


@dataclass
class StrategyRecommendation:
    """Strategy recommendation based on market conditions."""

    timestamp: str
    market_condition: str  # "soft", "efficient", "overreactive", "neutral"

    # Recommended adjustments
    edge_adjustment: float
    confidence_adjustment: float
    volume_adjustment: float  # How many plays to target

    # Rationale
    rationale: str
    supporting_evidence: list[str] = field(default_factory=list)

    # Risk warning
    risk_level: str = "normal"  # "low", "normal", "elevated", "high"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp,
            "market_condition": self.market_condition,
            "adjustments": {
                "edge": round(self.edge_adjustment, 3),
                "confidence": round(self.confidence_adjustment, 3),
                "volume": round(self.volume_adjustment, 2),
            },
            "rationale": self.rationale,
            "evidence": self.supporting_evidence,
            "risk_level": self.risk_level,
        }


class AdaptiveStrategy:
    """Adaptive strategy that adjusts to market conditions.

    Monitors:
    - CLV performance (are we beating the close?)
    - Market biases (over/under inefficiencies)
    - Market reactions (overreactions to events)
    - Line movement patterns

    Adjusts:
    - Edge thresholds
    - Confidence thresholds
    - Number of plays
    - Aggressiveness
    """

    def __init__(
        self,
        base_edge: float = 0.05,
        base_confidence: float = 0.65,
        base_ev: float = 0.03,
    ) -> None:
        """Initialize adaptive strategy.

        Args:
            base_edge: Base edge threshold
            base_confidence: Base confidence threshold
            base_ev: Base EV threshold
        """
        self.config = AdaptiveConfig(
            base_edge_threshold=base_edge,
            base_confidence_threshold=base_confidence,
            base_ev_threshold=base_ev,
        )

        self.recommendations: list[StrategyRecommendation] = []

    def analyze_market_conditions(
        self,
        clv_tracker: CLVTracker | None = None,
        bias_detector: BiasDetector | None = None,
        reaction_detector: ReactionDetector | None = None,
        line_analyzer: LineMovementAnalyzer | None = None,
    ) -> dict[str, Any]:
        """Analyze current market conditions.

        Args:
            clv_tracker: CLV tracker for performance analysis
            bias_detector: Bias detector for inefficiencies
            reaction_detector: Reaction detector for overreactions
            line_analyzer: Line movement analyzer

        Returns:
            Market condition assessment
        """
        conditions = {
            "clv_performance": self._assess_clv(clv_tracker),
            "market_bias": self._assess_bias(bias_detector),
            "market_reactions": self._assess_reactions(reaction_detector),
            "line_patterns": self._assess_lines(line_analyzer),
        }

        # Overall market condition
        scores = [
            conditions["clv_performance"]["score"],
            conditions["market_bias"]["score"],
            conditions["market_reactions"]["score"],
        ]
        avg_score = sum(scores) / len(scores) if scores else 0.5

        if avg_score > 0.7:
            market_condition = "soft"
        elif avg_score > 0.5:
            market_condition = "neutral"
        elif avg_score > 0.3:
            market_condition = "efficient"
        else:
            market_condition = "overreactive"

        conditions["overall_condition"] = market_condition
        conditions["overall_score"] = avg_score

        return conditions

    def _assess_clv(self, clv_tracker: CLVTracker | None) -> dict[str, Any]:
        """Assess CLV performance."""
        if not clv_tracker:
            return {"score": 0.5, "status": "unknown", "adjustment": 0}

        recent = clv_tracker.get_recent_clv(window=30)

        if recent.picks_with_clv < 10:
            return {"score": 0.5, "status": "insufficient_data", "adjustment": 0}

        avg_clv = recent.avg_clv_percentage

        if avg_clv > 0.02:
            # Beating close significantly - market is soft
            return {
                "score": 0.9,
                "status": "beating_close",
                "adjustment": -0.02,  # Lower thresholds
                "avg_clv": round(avg_clv, 3),
            }
        elif avg_clv > 0.01:
            # Beating close moderately
            return {
                "score": 0.7,
                "status": "slight_edge",
                "adjustment": -0.01,
                "avg_clv": round(avg_clv, 3),
            }
        elif avg_clv > 0:
            # Barely beating close
            return {
                "score": 0.5,
                "status": "break_even",
                "adjustment": 0,
                "avg_clv": round(avg_clv, 3),
            }
        else:
            # Not beating close - market is efficient or we're off
            return {
                "score": 0.3,
                "status": "inefficient",
                "adjustment": 0.02,  # Raise thresholds
                "avg_clv": round(avg_clv, 3),
            }

    def _assess_bias(self, bias_detector: BiasDetector | None) -> dict[str, Any]:
        """Assess market bias opportunities."""
        if not bias_detector:
            return {"score": 0.5, "status": "unknown", "adjustment": 0}

        report = bias_detector.generate_full_report()

        significant_biases = [
            b for b in report.detected_biases
            if abs(b.edge_opportunity) > 0.03 and b.confidence > 0.6
        ]

        if len(significant_biases) >= 2:
            # Multiple exploitable biases
            return {
                "score": 0.85,
                "status": "strong_biases",
                "adjustment": -0.02,
                "num_biases": len(significant_biases),
            }
        elif len(significant_biases) == 1:
            return {
                "score": 0.7,
                "status": "moderate_bias",
                "adjustment": -0.01,
                "num_biases": 1,
            }
        else:
            return {
                "score": 0.5,
                "status": "minimal_bias",
                "adjustment": 0,
                "num_biases": 0,
            }

    def _assess_reactions(self, reaction_detector: ReactionDetector | None) -> dict[str, Any]:
        """Assess market reaction patterns."""
        if not reaction_detector:
            return {"score": 0.5, "status": "unknown", "adjustment": 0}

        overreactions = reaction_detector.get_overreactions(min_confidence=0.6)
        fades = reaction_detector.get_fade_opportunities()

        if len(fades) > 5:
            # Many fade opportunities - market is emotional
            return {
                "score": 0.9,
                "status": "overreactive",
                "adjustment": -0.015,
                "fade_opportunities": len(fades),
            }
        elif len(overreactions) > 3:
            return {
                "score": 0.75,
                "status": "reactive",
                "adjustment": -0.01,
                "overreactions": len(overreactions),
            }
        else:
            return {
                "score": 0.5,
                "status": "rational",
                "adjustment": 0,
                "overreactions": len(overreactions),
            }

    def _assess_lines(self, line_analyzer: LineMovementAnalyzer | None) -> dict[str, Any]:
        """Assess line movement patterns."""
        if not line_analyzer:
            return {"score": 0.5, "status": "unknown"}

        sharp_plays = line_analyzer.get_sharp_plays(min_confidence=0.6)
        contrarian = line_analyzer.get_contrarian_opportunities()

        return {
            "score": 0.6 if len(sharp_plays) > 2 else 0.5,
            "status": "opportunities_available" if len(contrarian) > 2 else "stable",
            "sharp_opportunities": len(sharp_plays),
            "contrarian_opportunities": len(contrarian),
        }

    def generate_recommendation(
        self,
        clv_tracker: CLVTracker | None = None,
        bias_detector: BiasDetector | None = None,
        reaction_detector: ReactionDetector | None = None,
        line_analyzer: LineMovementAnalyzer | None = None,
    ) -> StrategyRecommendation:
        """Generate strategy recommendation based on market conditions.

        Returns:
            StrategyRecommendation with adjustments
        """
        conditions = self.analyze_market_conditions(
            clv_tracker, bias_detector, reaction_detector, line_analyzer
        )

        market_condition = conditions["overall_condition"]
        clv_adj = conditions["clv_performance"].get("adjustment", 0)
        bias_adj = conditions["market_bias"].get("adjustment", 0)
        reaction_adj = conditions["market_reactions"].get("adjustment", 0)

        # Update config adjustments
        self.config.clv_adjustment = clv_adj
        self.config.bias_adjustment = bias_adj
        self.config.reaction_adjustment = reaction_adj

        # Recalculate thresholds
        self.config.__post_init__()

        # Generate recommendation
        evidence = []
        if clv_tracker and conditions["clv_performance"].get("status"):
            evidence.append(f"CLV: {conditions['clv_performance']['status']}")
        if bias_detector and conditions["market_bias"].get("num_biases"):
            evidence.append(f"Biases: {conditions['market_bias']['num_biases']} detected")
        if reaction_detector and conditions["market_reactions"].get("fade_opportunities"):
            evidence.append(f"Fades: {conditions['market_reactions']['fade_opportunities']} opportunities")

        # Determine risk level
        if market_condition == "overreactive":
            risk_level = "elevated"
        elif market_condition == "soft":
            risk_level = "low"
        else:
            risk_level = "normal"

        # Volume adjustment
        if market_condition == "soft":
            volume_adj = 1.2  # Increase volume
        elif market_condition == "overreactive":
            volume_adj = 0.9  # Slight decrease
        else:
            volume_adj = 1.0

        from datetime import datetime
        recommendation = StrategyRecommendation(
            timestamp=datetime.now().isoformat(),
            market_condition=market_condition,
            edge_adjustment=clv_adj + bias_adj + reaction_adj,
            confidence_adjustment=(clv_adj + bias_adj) * 0.5,
            volume_adjustment=volume_adj,
            rationale=f"Market is {market_condition} - adjusting thresholds accordingly",
            supporting_evidence=evidence,
            risk_level=risk_level,
        )

        self.recommendations.append(recommendation)
        return recommendation

    def get_current_thresholds(self) -> dict[str, float]:
        """Get current adjusted thresholds."""
        return {
            "edge": self.config.current_edge_threshold,
            "confidence": self.config.current_confidence_threshold,
            "ev": self.config.current_ev_threshold,
        }

    def should_adjust_for_play(
        self,
        player_name: str,
        stat_type: str,
        over_under: str,
        bias_detector: BiasDetector | None = None,
    ) -> dict[str, Any]:
        """Determine if specific adjustments needed for a play."""
        adjustments = {
            "edge_modifier": 0.0,
            "confidence_modifier": 0.0,
            "reason": "",
        }

        if not bias_detector:
            return adjustments

        # Check for stat-specific bias
        report = bias_detector.generate_full_report()
        for bias in report.detected_biases:
            if bias.entity == stat_type:
                if bias.edge_opportunity > 0 and over_under == "over":
                    adjustments["edge_modifier"] = -0.01
                    adjustments["reason"] = f"Favorable over bias in {stat_type}"
                elif bias.edge_opportunity > 0 and over_under == "under":
                    adjustments["edge_modifier"] = 0.01
                    adjustments["reason"] = f"Unfavorable over bias in {stat_type}"
                elif bias.edge_opportunity < 0 and over_under == "under":
                    adjustments["edge_modifier"] = -0.01
                    adjustments["reason"] = f"Favorable under bias in {stat_type}"

        return adjustments

    def export_strategy_state(self) -> dict[str, Any]:
        """Export current strategy state."""
        return {
            "config": self.config.to_dict(),
            "current_thresholds": self.get_current_thresholds(),
            "recent_recommendations": [
                r.to_dict() for r in self.recommendations[-5:]
            ],
        }
