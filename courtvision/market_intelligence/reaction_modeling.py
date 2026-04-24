"""Market reaction modeling for detecting overreactions.

Detects when markets overreact to:
- Injuries
- Recent form
- Narratives

Phase 11: Market Adaptation and Opponent Modeling
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ReactionType(str, Enum):
    """Types of market reactions."""

    INJURY_OVERREACTION = "injury_overreaction"
    INJURY_UNDERREACTION = "injury_underreaction"
    RECENT_FORM_OVERREACTION = "recent_form_overreaction"
    NARRATIVE_OVERREACTION = "narrative_overreaction"
    PROBABLE_RETURN = "probable_return"  # Market likely correct
    NO_REACTION = "no_reaction"


@dataclass
class MarketReaction:
    """Detected market reaction."""

    play_id: str
    player_name: str
    reaction_type: ReactionType

    # Trigger
    trigger_event: str  # e.g., "injury_out", "hot_streak", "media_hype"
    trigger_severity: str  # "high", "medium", "low"

    # Market response
    line_movement: float  # Points moved
    movement_pct: float
    market_sentiment: str  # "panic", "excited", "cautious"

    # Assessment
    is_overreaction: bool
    correction_expected: bool
    fade_opportunity: bool  # Should we fade the market?

    # Confidence
    confidence: float  # 0-1
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "play": {
                "id": self.play_id,
                "player": self.player_name,
            },
            "reaction": {
                "type": self.reaction_type.value,
                "trigger": self.trigger_event,
                "severity": self.trigger_severity,
            },
            "market_response": {
                "line_movement": round(self.line_movement, 2),
                "movement_pct": round(self.movement_pct, 3),
                "sentiment": self.market_sentiment,
            },
            "assessment": {
                "is_overreaction": self.is_overreaction,
                "correction_expected": self.correction_expected,
                "fade_opportunity": self.fade_opportunity,
            },
            "confidence": round(self.confidence, 3),
            "explanation": self.explanation,
        }


@dataclass
class ReactionContext:
    """Context for detecting reactions."""

    player_name: str
    baseline_line: float  # Line before event
    current_line: float  # Line after event
    event_type: str  # "injury", "recent_form", "narrative"
    event_details: dict[str, Any] = field(default_factory=dict)


class ReactionDetector:
    """Detect market overreactions to events.

    Helps identify fade opportunities when market overreacts to:
    - Injury news
    - Recent hot/cold streaks
    - Media narratives
    """

    # Overreaction thresholds
    INJURY_MOVE_THRESHOLD = 1.0  # 1+ point move on injury is significant
    FORM_MOVE_THRESHOLD = 0.8  # 0.8+ point move on recent form
    NARRATIVE_MOVE_THRESHOLD = 0.5  # 0.5+ point move on narrative

    # Expected vs actual movement ratios
    OVERREACTION_RATIO = 2.0  # If actual > 2x expected, it's an overreaction

    def __init__(self) -> None:
        """Initialize reaction detector."""
        self.reactions: list[MarketReaction] = []
        self.recent_contexts: list[ReactionContext] = []

    def analyze_injury_reaction(
        self,
        play_id: str,
        player_name: str,
        pre_injury_line: float,
        post_injury_line: float,
        injury_status: str,  # "out", "doubtful", "questionable", "probable"
        key_player: bool,  # Is injured player key to this player's performance?
        minutes_impact: float,  # Expected minutes change
    ) -> MarketReaction:
        """Analyze market reaction to injury news.

        Args:
            play_id: Play identifier
            player_name: Player whose line we're tracking
            pre_injury_line: Line before injury news
            post_injury_line: Line after injury news
            injury_status: Status of injured player
            key_player: Whether injured player is key to performance
            minutes_impact: Expected minutes change for tracked player

        Returns:
            MarketReaction analysis
        """
        line_movement = post_injury_line - pre_injury_line
        movement_pct = line_movement / pre_injury_line if pre_injury_line != 0 else 0

        # Calculate expected movement based on injury impact
        if key_player:
            if injury_status == "out":
                expected_movement = minutes_impact * 0.3  # 30% of minutes = stat impact
            elif injury_status in ["doubtful", "questionable"]:
                expected_movement = minutes_impact * 0.15
            else:  # probable
                expected_movement = minutes_impact * 0.05
        else:
            expected_movement = minutes_impact * 0.1

        # Determine if overreaction
        abs_movement = abs(line_movement)
        is_overreaction = False
        correction_expected = False
        fade_opportunity = False
        reaction_type = ReactionType.NO_REACTION
        confidence = 0.5
        explanation = ""

        if abs_movement > self.INJURY_MOVE_THRESHOLD:
            if abs_movement > expected_movement * self.OVERREACTION_RATIO:
                is_overreaction = True
                correction_expected = True

                # Determine direction
                if line_movement > 0:
                    # Line moved up
                    reaction_type = ReactionType.INJURY_OVERREACTION
                    explanation = f"Market overreacted to injury, line moved {line_movement:+.1f} (expected {expected_movement:+.1f})"
                else:
                    reaction_type = ReactionType.INJURY_UNDERREACTION
                    explanation = f"Market underreacted to injury, line moved {line_movement:+.1f} (expected {expected_movement:+.1f})"

                confidence = min(0.9, abs_movement / (expected_movement * 2))

                # Fade opportunity if market moved opposite to logic
                if key_player and injury_status == "out":
                    if line_movement < 0:  # Line went down when it should go up
                        fade_opportunity = True
                        explanation += " - FADE: Market moved wrong direction"

            else:
                reaction_type = ReactionType.PROBABLE_RETURN
                explanation = "Market reaction proportionate to injury impact"
                confidence = 0.7

        market_sentiment = self._classify_sentiment(abs_movement, expected_movement)

        reaction = MarketReaction(
            play_id=play_id,
            player_name=player_name,
            reaction_type=reaction_type,
            trigger_event=f"injury_{injury_status}",
            trigger_severity="high" if injury_status == "out" else "medium",
            line_movement=line_movement,
            movement_pct=movement_pct,
            market_sentiment=market_sentiment,
            is_overreaction=is_overreaction,
            correction_expected=correction_expected,
            fade_opportunity=fade_opportunity,
            confidence=confidence,
            explanation=explanation,
        )

        self.reactions.append(reaction)
        return reaction

    def analyze_form_reaction(
        self,
        play_id: str,
        player_name: str,
        pre_form_line: float,
        post_form_line: float,
        recent_games: int,
        recent_avg: float,
        season_avg: float,
        narrative: str,  # "hot", "cold", "trending"
    ) -> MarketReaction:
        """Analyze market reaction to recent form.

        Args:
            play_id: Play identifier
            player_name: Player name
            pre_form_line: Line before recent form considered
            post_form_line: Line after recent form adjustment
            recent_games: Number of games in sample
            recent_avg: Recent average performance
            season_avg: Season average performance
            narrative: "hot", "cold", "trending"

        Returns:
            MarketReaction analysis
        """
        line_movement = post_form_line - pre_form_line
        movement_pct = line_movement / pre_form_line if pre_form_line != 0 else 0

        # Expected movement based on form difference
        form_diff = (recent_avg - season_avg) / season_avg if season_avg != 0 else 0
        expected_movement = form_diff * pre_form_line * (recent_games / 10)  # Weight by sample size

        abs_movement = abs(line_movement)

        # Small sample overreaction detection
        is_overreaction = False
        correction_expected = False
        fade_opportunity = False
        reaction_type = ReactionType.NO_REACTION
        confidence = 0.5
        explanation = ""

        if recent_games <= 3 and abs_movement > self.FORM_MOVE_THRESHOLD:
            # Small sample, large move = likely overreaction
            is_overreaction = True
            correction_expected = True
            reaction_type = ReactionType.RECENT_FORM_OVERREACTION
            confidence = 0.75
            explanation = f"Market overreacted to {recent_games}-game sample, line moved {line_movement:+.1f}"

            # Fade opportunity when market chases hot/cold streaks
            if narrative == "hot" and line_movement > 0:
                fade_opportunity = True
                explanation += " - FADE: Market chasing hot streak"
            elif narrative == "cold" and line_movement < 0:
                fade_opportunity = True
                explanation += " - FADE: Market over-penalizing cold streak"

        elif abs_movement > expected_movement * self.OVERREACTION_RATIO:
            is_overreaction = True
            correction_expected = True
            reaction_type = ReactionType.RECENT_FORM_OVERREACTION
            confidence = 0.6
            explanation = f"Form-based line move ({line_movement:+.1f}) exceeds expected ({expected_movement:+.1f})"

        market_sentiment = self._classify_sentiment(abs_movement, expected_movement)

        reaction = MarketReaction(
            play_id=play_id,
            player_name=player_name,
            reaction_type=reaction_type,
            trigger_event=f"recent_form_{narrative}",
            trigger_severity="medium" if recent_games <= 5 else "low",
            line_movement=line_movement,
            movement_pct=movement_pct,
            market_sentiment=market_sentiment,
            is_overreaction=is_overreaction,
            correction_expected=correction_expected,
            fade_opportunity=fade_opportunity,
            confidence=confidence,
            explanation=explanation,
        )

        self.reactions.append(reaction)
        return reaction

    def analyze_narrative_reaction(
        self,
        play_id: str,
        player_name: str,
        pre_narrative_line: float,
        post_narrative_line: float,
        narrative_type: str,  # "media_hype", "prime_time", "rivalry", "revenge"
        narrative_strength: str,  # "high", "medium", "low"
        media_mentions: int = 0,  # Number of media mentions
    ) -> MarketReaction:
        """Analyze market reaction to media narratives.

        Args:
            play_id: Play identifier
            player_name: Player name
            pre_narrative_line: Line before narrative impact
            post_narrative_line: Line after narrative impact
            narrative_type: Type of narrative
            narrative_strength: Strength of narrative
            media_mentions: Media mention count

        Returns:
            MarketReaction analysis
        """
        line_movement = post_narrative_line - pre_narrative_line
        movement_pct = line_movement / pre_narrative_line if pre_narrative_line != 0 else 0

        abs_movement = abs(line_movement)

        # Narrative overreaction detection
        is_overreaction = False
        correction_expected = False
        fade_opportunity = False
        reaction_type = ReactionType.NO_REACTION
        confidence = 0.5
        explanation = ""

        # High media attention often leads to overreaction
        if media_mentions > 50 and abs_movement > self.NARRATIVE_MOVE_THRESHOLD:
            is_overreaction = True
            correction_expected = True
            reaction_type = ReactionType.NARRATIVE_OVERREACTION
            confidence = min(0.8, media_mentions / 100)
            explanation = f"Market overreacting to media narrative ({media_mentions} mentions), line moved {line_movement:+.1f}"
            fade_opportunity = True
            explanation += " - FADE: Media hype often overpriced"

        elif narrative_strength == "high" and abs_movement > self.NARRATIVE_MOVE_THRESHOLD * 1.5:
            is_overreaction = True
            reaction_type = ReactionType.NARRATIVE_OVERREACTION
            confidence = 0.65
            explanation = f"Strong narrative driving excessive line movement ({line_movement:+.1f})"

        market_sentiment = self._classify_sentiment(abs_movement, 0.2)  # 0.2 as baseline narrative impact

        reaction = MarketReaction(
            play_id=play_id,
            player_name=player_name,
            reaction_type=reaction_type,
            trigger_event=f"narrative_{narrative_type}",
            trigger_severity=narrative_strength,
            line_movement=line_movement,
            movement_pct=movement_pct,
            market_sentiment=market_sentiment,
            is_overreaction=is_overreaction,
            correction_expected=correction_expected,
            fade_opportunity=fade_opportunity,
            confidence=confidence,
            explanation=explanation,
        )

        self.reactions.append(reaction)
        return reaction

    def _classify_sentiment(self, actual_movement: float, expected_movement: float) -> str:
        """Classify market sentiment based on movement."""
        ratio = actual_movement / expected_movement if expected_movement > 0 else actual_movement

        if ratio > 3:
            return "panic"
        elif ratio > 2:
            return "excited"
        elif ratio > 1:
            return "concerned"
        elif ratio > 0.5:
            return "cautious"
        else:
            return "neutral"

    def get_overreactions(
        self,
        min_confidence: float = 0.6,
        fade_only: bool = False,
    ) -> list[MarketReaction]:
        """Get detected overreactions.

        Args:
            min_confidence: Minimum confidence threshold
            fade_only: Only return fade opportunities

        Returns:
            List of overreactions
        """
        overreactions = [
            r for r in self.reactions
            if r.is_overreaction and r.confidence >= min_confidence
        ]

        if fade_only:
            overreactions = [r for r in overreactions if r.fade_opportunity]

        return sorted(overreactions, key=lambda x: x.confidence, reverse=True)

    def get_fade_opportunities(self) -> list[MarketReaction]:
        """Get all fade opportunities."""
        return [r for r in self.reactions if r.fade_opportunity]

    def generate_reaction_report(self) -> dict[str, Any]:
        """Generate complete reaction analysis report."""
        overreactions = self.get_overreactions(min_confidence=0.5)
        fades = self.get_fade_opportunities()

        by_type: dict[str, list[MarketReaction]] = {}
        for r in self.reactions:
            rt = r.reaction_type.value
            if rt not in by_type:
                by_type[rt] = []
            by_type[rt].append(r)

        return {
            "summary": {
                "total_analyzed": len(self.reactions),
                "overreactions": len(overreactions),
                "fade_opportunities": len(fades),
            },
            "by_type": {
                rt: len(reactions) for rt, reactions in by_type.items()
            },
            "overreactions": [r.to_dict() for r in overreactions[:10]],
            "fade_opportunities": [r.to_dict() for r in fades],
            "recommendations": self._generate_recommendations(overreactions),
        }

    def _generate_recommendations(self, overreactions: list[MarketReaction]) -> list[str]:
        """Generate trading recommendations based on reactions."""
        if not overreactions:
            return ["No significant overreactions detected"]

        recs = []

        # Check for patterns
        injury_overreactions = [r for r in overreactions if "injury" in r.reaction_type.value]
        form_overreactions = [r for r in overreactions if "form" in r.reaction_type.value]
        narrative_overreactions = [r for r in overreactions if "narrative" in r.reaction_type.value]

        if len(injury_overreactions) > 3:
            recs.append("Multiple injury overreactions detected - consider fading injury news")

        if len(form_overreactions) > 3:
            recs.append("Market consistently overreacts to recent form - fade recency bias")

        if len(narrative_overreactions) > 2:
            recs.append("Media narratives causing overreactions - fade hype plays")

        # General recommendation
        fade_count = len([r for r in overreactions if r.fade_opportunity])
        if fade_count > 5:
            recs.append(f"{fade_count} fade opportunities available - market appears overly emotional")

        return recs if recs else ["Market reactions appear rational - no major fade opportunities"]

    def export_reactions(self) -> list[dict[str, Any]]:
        """Export all reactions."""
        return [r.to_dict() for r in self.reactions]
