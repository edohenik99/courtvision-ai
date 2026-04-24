"""Line movement tracking and analysis.

Tracks opening vs closing lines and detects sharp vs public movement.

Phase 11: Market Adaptation and Opponent Modeling
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class LineMovementType(str, Enum):
    """Types of line movement."""

    SHARP = "sharp"  # Movement toward sharp side (likely correct)
    PUBLIC = "public"  # Movement toward public side (often wrong)
    INJURY = "injury"  # Movement due to injury news
    WEATHER = "weather"  # Movement due to external factors
    STEAM = "steam"  # Rapid movement (money pouring in)
    REVERSE = "reverse"  # Line moves opposite to money
    STABLE = "stable"  # Minimal movement


@dataclass
class LineSnapshot:
    """Line at a point in time."""

    timestamp: datetime
    line_value: float
    odds: int  # American odds (e.g., -110)
    source: str  # sportsbook or "consensus"

    # Optional context
    volume_indicator: str = ""  # "high", "medium", "low"
    movement_from_open: float = 0.0  # Points moved from open

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "line": self.line_value,
            "odds": self.odds,
            "source": self.source,
            "volume": self.volume_indicator,
            "movement_from_open": round(self.movement_from_open, 2),
        }


@dataclass
class LineMovementAnalysis:
    """Analysis of line movement from open to close."""

    play_id: str
    player_name: str
    stat_type: str

    # Line values
    opening_line: float
    closing_line: float
    current_line: float

    # Movement metrics
    total_movement: float
    movement_pct: float
    movement_direction: str  # "up", "down", "stable"

    # Classification
    movement_type: LineMovementType
    confidence: float  # 0-1 confidence in classification

    # Analysis
    sharp_side: str  # "over", "under", or "neutral"
    public_side: str  # "over", "under", or "neutral"

    # Snapshots
    snapshots: list[LineSnapshot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "play": {
                "id": self.play_id,
                "player": self.player_name,
                "stat": self.stat_type,
            },
            "lines": {
                "opening": self.opening_line,
                "closing": self.closing_line,
                "current": self.current_line,
            },
            "movement": {
                "total": round(self.total_movement, 2),
                "pct": round(self.movement_pct, 3),
                "direction": self.movement_direction,
            },
            "classification": {
                "type": self.movement_type.value,
                "confidence": round(self.confidence, 3),
                "sharp_side": self.sharp_side,
                "public_side": self.public_side,
            },
            "snapshots": [s.to_dict() for s in self.snapshots],
        }


class LineMovementAnalyzer:
    """Analyze line movements to detect sharp vs public action.

    Identifies:
    - Sharp movement (smart money)
    - Public movement (recreational money)
    - Steam moves (sudden large shifts)
    - Reverse line moves (contrarian signals)
    """

    # Thresholds for movement classification
    SIGNIFICANT_MOVE_THRESHOLD = 0.5  # 0.5 points is significant
    LARGE_MOVE_THRESHOLD = 1.0  # 1.0 point is large
    STEAM_MOVE_THRESHOLD = 0.3  # 0.3 points in short window is steam

    def __init__(self) -> None:
        """Initialize line movement analyzer."""
        self.line_histories: dict[str, list[LineSnapshot]] = {}
        self.analyses: dict[str, LineMovementAnalysis] = {}

    def record_line(
        self,
        play_id: str,
        line_value: float,
        odds: int,
        timestamp: datetime | None = None,
        source: str = "consensus",
        volume_indicator: str = "",
    ) -> None:
        """Record a line snapshot.

        Args:
            play_id: Unique play identifier
            line_value: Line value
            odds: American odds
            timestamp: Time of snapshot (default now)
            source: Source of line
            volume_indicator: Volume context
        """
        if timestamp is None:
            timestamp = datetime.now()

        if play_id not in self.line_histories:
            self.line_histories[play_id] = []

        # Calculate movement from open
        movement = 0.0
        if self.line_histories[play_id]:
            opening_line = self.line_histories[play_id][0].line_value
            movement = line_value - opening_line

        snapshot = LineSnapshot(
            timestamp=timestamp,
            line_value=line_value,
            odds=odds,
            source=source,
            volume_indicator=volume_indicator,
            movement_from_open=movement,
        )

        self.line_histories[play_id].append(snapshot)

    def analyze_movement(
        self,
        play_id: str,
        player_name: str,
        stat_type: str,
    ) -> LineMovementAnalysis | None:
        """Analyze line movement for a play.

        Args:
            play_id: Play identifier
            player_name: Player name
            stat_type: Stat type

        Returns:
            LineMovementAnalysis or None if insufficient data
        """
        history = self.line_histories.get(play_id, [])
        if len(history) < 2:
            return None

        opening = history[0]
        closing = history[-1]

        total_movement = closing.line_value - opening.line_value
        movement_pct = total_movement / opening.line_value if opening.line_value != 0 else 0

        # Determine direction
        if abs(total_movement) < 0.1:
            direction = "stable"
        elif total_movement > 0:
            direction = "up"
        else:
            direction = "down"

        # Classify movement type
        movement_type, confidence, sharp_side, public_side = self._classify_movement(
            history, total_movement
        )

        analysis = LineMovementAnalysis(
            play_id=play_id,
            player_name=player_name,
            stat_type=stat_type,
            opening_line=opening.line_value,
            closing_line=closing.line_value,
            current_line=closing.line_value,
            total_movement=total_movement,
            movement_pct=movement_pct,
            movement_direction=direction,
            movement_type=movement_type,
            confidence=confidence,
            sharp_side=sharp_side,
            public_side=public_side,
            snapshots=history,
        )

        self.analyses[play_id] = analysis
        return analysis

    def _classify_movement(
        self,
        history: list[LineSnapshot],
        total_movement: float,
    ) -> tuple[LineMovementType, float, str, str]:
        """Classify the type of line movement.

        Returns:
            (movement_type, confidence, sharp_side, public_side)
        """
        abs_movement = abs(total_movement)

        # Check for steam move (rapid movement)
        if len(history) >= 3:
            recent_moves = [
                history[i].line_value - history[i - 1].line_value
                for i in range(1, len(history))
            ]
            max_recent = max(abs(m) for m in recent_moves[-3:])

            if max_recent > self.STEAM_MOVE_THRESHOLD:
                # Rapid movement detected
                if history[-1].volume_indicator == "high":
                    return LineMovementType.STEAM, 0.8, "unknown", "unknown"

        # Check for significant move
        if abs_movement < self.SIGNIFICANT_MOVE_THRESHOLD:
            return LineMovementType.STABLE, 0.9, "neutral", "neutral"

        # Determine sharp vs public
        # Heuristic: Large moves with low volume often sharp
        # Large moves with high volume often public

        last_volume = history[-1].volume_indicator if history else ""

        if abs_movement > self.LARGE_MOVE_THRESHOLD:
            if last_volume == "low":
                # Large move on low volume = likely sharp
                sharp_side = "over" if total_movement > 0 else "under"
                public_side = "under" if total_movement > 0 else "over"
                return LineMovementType.SHARP, 0.7, sharp_side, public_side
            else:
                # Large move on volume = likely public
                sharp_side = "under" if total_movement > 0 else "over"
                public_side = "over" if total_movement > 0 else "under"
                return LineMovementType.PUBLIC, 0.6, sharp_side, public_side

        # Moderate move
        if last_volume == "low":
            sharp_side = "over" if total_movement > 0 else "under"
            public_side = "neutral"
            return LineMovementType.SHARP, 0.5, sharp_side, public_side
        else:
            sharp_side = "neutral"
            public_side = "over" if total_movement > 0 else "under"
            return LineMovementType.PUBLIC, 0.5, sharp_side, public_side

    def get_sharp_plays(
        self,
        min_confidence: float = 0.6,
    ) -> list[LineMovementAnalysis]:
        """Get plays showing sharp movement.

        Args:
            min_confidence: Minimum confidence threshold

        Returns:
            List of analyses with sharp movement
        """
        return [
            analysis for analysis in self.analyses.values()
            if analysis.movement_type == LineMovementType.SHARP
            and analysis.confidence >= min_confidence
        ]

    def get_public_plays(
        self,
        min_confidence: float = 0.5,
    ) -> list[LineMovementAnalysis]:
        """Get plays showing public movement (potential fade)."""
        return [
            analysis for analysis in self.analyses.values()
            if analysis.movement_type == LineMovementType.PUBLIC
            and analysis.confidence >= min_confidence
        ]

    def get_steam_moves(self) -> list[LineMovementAnalysis]:
        """Get steam moves (rapid line changes)."""
        return [
            analysis for analysis in self.analyses.values()
            if analysis.movement_type == LineMovementType.STEAM
        ]

    def get_contrarian_opportunities(
        self,
        min_movement: float = 0.5,
    ) -> list[LineMovementAnalysis]:
        """Find contrarian opportunities (fade public overreaction)."""
        public_plays = self.get_public_plays()

        return [
            analysis for analysis in public_plays
            if abs(analysis.total_movement) >= min_movement
        ]

    def get_line_stability_score(self, play_id: str) -> float:
        """Get line stability score (0-1, higher = more stable)."""
        history = self.line_histories.get(play_id, [])
        if len(history) < 2:
            return 1.0  # No data = assume stable

        # Calculate coefficient of variation of line
        lines = [s.line_value for s in history]
        mean_line = sum(lines) / len(lines)
        variance = sum((l - mean_line) ** 2 for l in lines) / len(lines)
        std = variance ** 0.5

        cv = std / mean_line if mean_line > 0 else 0

        # Convert to stability score
        stability = max(0, 1 - cv * 10)
        return min(1.0, stability)

    def export_analysis(self) -> dict[str, Any]:
        """Export all line movement analyses."""
        return {
            "plays_analyzed": len(self.analyses),
            "by_type": {
                mtype.value: len([
                    a for a in self.analyses.values()
                    if a.movement_type == mtype
                ])
                for mtype in LineMovementType
            },
            "sharp_opportunities": len(self.get_sharp_plays()),
            "contrarian_opportunities": len(self.get_contrarian_opportunities()),
            "analyses": [a.to_dict() for a in self.analyses.values()],
        }
