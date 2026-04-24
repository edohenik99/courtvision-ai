"""Integration hooks for Phase 11 market intelligence.

Provides integration with prediction pipeline for:
- Line movement analysis
- CLV tracking
- Market bias detection
- Adaptive strategy adjustments

Phase 11: Market Adaptation and Opponent Modeling
"""

from __future__ import annotations

from typing import Any

from courtvision.market_intelligence.adaptive_strategy import AdaptiveStrategy
from courtvision.market_intelligence.bias_detection import BiasDetector
from courtvision.market_intelligence.clv_tracker import CLVTracker
from courtvision.market_intelligence.line_movement import LineMovementAnalyzer
from courtvision.market_intelligence.reaction_modeling import ReactionDetector


class MarketIntelligenceHooks:
    """Integration hooks for market intelligence layer.

    Provides unified interface for all Phase 11 components:
    - Line movement tracking
    - CLV tracking
    - Bias detection
    - Reaction modeling
    - Adaptive strategy

    Usage:
        hooks = MarketIntelligenceHooks()

        # Record line changes
        hooks.record_line(play_id, line_value, odds)

        # Track pick result for CLV
        hooks.record_clv(play_id, our_line, closing_line, actual_result)

        # Get adaptive thresholds
        thresholds = hooks.get_adaptive_thresholds()
    """

    def __init__(
        self,
        enable_line_tracking: bool = True,
        enable_clv: bool = True,
        enable_bias: bool = True,
        enable_reactions: bool = True,
        enable_adaptive: bool = True,
        base_edge: float = 0.05,
        base_confidence: float = 0.65,
    ) -> None:
        """Initialize market intelligence hooks.

        Args:
            enable_line_tracking: Enable line movement tracking
            enable_clv: Enable CLV tracking
            enable_bias: Enable bias detection
            enable_reactions: Enable reaction modeling
            enable_adaptive: Enable adaptive strategy
            base_edge: Base edge threshold
            base_confidence: Base confidence threshold
        """
        self.enable_line_tracking = enable_line_tracking
        self.enable_clv = enable_clv
        self.enable_bias = enable_bias
        self.enable_reactions = enable_reactions
        self.enable_adaptive = enable_adaptive

        # Initialize components
        self.line_analyzer = LineMovementAnalyzer() if enable_line_tracking else None
        self.clv_tracker = CLVTracker() if enable_clv else None
        self.bias_detector = BiasDetector() if enable_bias else None
        self.reaction_detector = ReactionDetector() if enable_reactions else None
        self.adaptive_strategy = (
            AdaptiveStrategy(base_edge=base_edge, base_confidence=base_confidence)
            if enable_adaptive else None
        )

    def record_line(
        self,
        play_id: str,
        line_value: float,
        odds: int = -110,
        timestamp: Any = None,
        source: str = "consensus",
        volume_indicator: str = "",
    ) -> None:
        """Record a line snapshot.

        Args:
            play_id: Play identifier
            line_value: Line value
            odds: American odds
            timestamp: Timestamp (default now)
            source: Line source
            volume_indicator: Volume context
        """
        if self.line_analyzer:
            self.line_analyzer.record_line(
                play_id=play_id,
                line_value=line_value,
                odds=odds,
                timestamp=timestamp,
                source=source,
                volume_indicator=volume_indicator,
            )

    def record_line_movement_analysis(
        self,
        play_id: str,
        player_name: str,
        stat_type: str,
    ) -> dict[str, Any] | None:
        """Analyze line movement for a play.

        Args:
            play_id: Play identifier
            player_name: Player name
            stat_type: Stat type

        Returns:
            Analysis dict or None if insufficient data
        """
        if not self.line_analyzer:
            return None

        analysis = self.line_analyzer.analyze_movement(play_id, player_name, stat_type)
        return analysis.to_dict() if analysis else None

    def record_pick_result(
        self,
        play_id: str,
        player_name: str,
        stat_type: str,
        prediction_date: str,
        over_under: str,
        hit: bool,
        line_value: float,
        actual_value: float,
    ) -> None:
        """Record pick result for bias detection.

        Args:
            play_id: Play identifier
            player_name: Player name
            stat_type: Stat type
            prediction_date: Date of prediction
            over_under: "over" or "under"
            hit: Whether pick hit
            line_value: Betting line
            actual_value: Actual result
        """
        if self.bias_detector:
            self.bias_detector.add_result(
                stat_type=stat_type,
                over_under=over_under,
                hit=hit,
                line_value=line_value,
                actual_value=actual_value,
            )

    def record_clv(
        self,
        play_id: str,
        player_name: str,
        stat_type: str,
        prediction_date: str,
        our_line: float,
        closing_line: float,
        pick_direction: str,
        actual_result: float,
        pick_result: str,
    ) -> dict[str, Any]:
        """Record CLV for a pick.

        Returns:
            CLV record dict
        """
        if not self.clv_tracker:
            return {}

        record = self.clv_tracker.record_clv(
            play_id=play_id,
            player_name=player_name,
            stat_type=stat_type,
            prediction_date=prediction_date,
            our_line=our_line,
            closing_line=closing_line,
            pick_direction=pick_direction,
            actual_result=actual_result,
            pick_result=pick_result,
        )

        return record.to_dict()

    def analyze_injury_reaction(
        self,
        play_id: str,
        player_name: str,
        pre_injury_line: float,
        post_injury_line: float,
        injury_status: str,
        key_player: bool,
        minutes_impact: float,
    ) -> dict[str, Any] | None:
        """Analyze market reaction to injury.

        Returns:
            Reaction analysis dict
        """
        if not self.reaction_detector:
            return None

        reaction = self.reaction_detector.analyze_injury_reaction(
            play_id=play_id,
            player_name=player_name,
            pre_injury_line=pre_injury_line,
            post_injury_line=post_injury_line,
            injury_status=injury_status,
            key_player=key_player,
            minutes_impact=minutes_impact,
        )

        return reaction.to_dict()

    def get_adaptive_thresholds(self) -> dict[str, float]:
        """Get current adaptive thresholds.

        Returns:
            Threshold dict with edge, confidence, ev
        """
        if not self.adaptive_strategy:
            return {"edge": 0.05, "confidence": 0.65, "ev": 0.03}

        # Generate fresh recommendation
        self.adaptive_strategy.generate_recommendation(
            clv_tracker=self.clv_tracker,
            bias_detector=self.bias_detector,
            reaction_detector=self.reaction_detector,
            line_analyzer=self.line_analyzer,
        )

        return self.adaptive_strategy.get_current_thresholds()

    def get_strategy_recommendation(self) -> dict[str, Any] | None:
        """Get strategy recommendation.

        Returns:
            Recommendation dict
        """
        if not self.adaptive_strategy:
            return None

        rec = self.adaptive_strategy.generate_recommendation(
            clv_tracker=self.clv_tracker,
            bias_detector=self.bias_detector,
            reaction_detector=self.reaction_detector,
            line_analyzer=self.line_analyzer,
        )

        return rec.to_dict()

    def get_sharp_opportunities(self) -> list[dict[str, Any]]:
        """Get plays with sharp movement.

        Returns:
            List of sharp opportunity dicts
        """
        if not self.line_analyzer:
            return []

        sharp = self.line_analyzer.get_sharp_plays(min_confidence=0.6)
        return [s.to_dict() for s in sharp]

    def get_fade_opportunities(self) -> list[dict[str, Any]]:
        """Get fade opportunities from overreactions.

        Returns:
            List of fade opportunity dicts
        """
        fades = []

        if self.reaction_detector:
            reactions = self.reaction_detector.get_fade_opportunities()
            fades.extend([r.to_dict() for r in reactions])

        if self.line_analyzer:
            contrarian = self.line_analyzer.get_contrarian_opportunities()
            fades.extend([c.to_dict() for c in contrarian])

        return fades

    def get_bias_report(self) -> dict[str, Any]:
        """Get market bias detection report.

        Returns:
            Bias report dict
        """
        if not self.bias_detector:
            return {"enabled": False}

        return self.bias_detector.export_bias_report()

    def get_clv_report(self) -> dict[str, Any]:
        """Get CLV performance report.

        Returns:
            CLV report dict
        """
        if not self.clv_tracker:
            return {"enabled": False}

        return self.clv_tracker.export_clv_report()

    def get_market_summary(self) -> dict[str, Any]:
        """Get complete market intelligence summary."""
        return {
            "enabled_modules": {
                "line_tracking": self.enable_line_tracking,
                "clv": self.enable_clv,
                "bias": self.enable_bias,
                "reactions": self.enable_reactions,
                "adaptive": self.enable_adaptive,
            },
            "adaptive_thresholds": self.get_adaptive_thresholds(),
            "strategy_recommendation": self.get_strategy_recommendation(),
            "opportunities": {
                "sharp": len(self.get_sharp_opportunities()),
                "fades": len(self.get_fade_opportunities()),
            },
            "reports": {
                "bias": self.get_bias_report() if self.enable_bias else None,
                "clv": self.get_clv_report() if self.enable_clv else None,
            },
        }

    def export_all_data(self) -> dict[str, Any]:
        """Export all market intelligence data."""
        return {
            "line_analyses": (
                self.line_analyzer.export_analysis() if self.line_analyzer else None
            ),
            "clv_report": (
                self.clv_tracker.export_clv_report() if self.clv_tracker else None
            ),
            "bias_report": (
                self.bias_detector.export_bias_report() if self.bias_detector else None
            ),
            "reaction_report": (
                self.reaction_detector.generate_reaction_report() if self.reaction_detector else None
            ),
            "strategy_state": (
                self.adaptive_strategy.export_strategy_state() if self.adaptive_strategy else None
            ),
        }


def create_market_aware_predictor(
    base_edge: float = 0.05,
    adapt_thresholds: bool = True,
) -> MarketIntelligenceHooks:
    """Factory function to create market-aware prediction system.

    Args:
        base_edge: Base edge threshold
        adapt_thresholds: Enable adaptive threshold adjustment

    Returns:
        Configured MarketIntelligenceHooks instance
    """
    return MarketIntelligenceHooks(
        enable_line_tracking=True,
        enable_clv=True,
        enable_bias=True,
        enable_reactions=True,
        enable_adaptive=adapt_thresholds,
        base_edge=base_edge,
    )
