"""Closing Line Value (CLV) tracking system.

Tracks whether picks beat the closing line and uses CLV as a performance signal.

Phase 11: Market Adaptation and Opponent Modeling
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class CLVRecord:
    """Record of CLV for a single pick."""

    play_id: str
    player_name: str
    stat_type: str
    prediction_date: str

    # Lines
    our_line: float  # Line when we made pick
    closing_line: float  # Final closing line

    # CLV calculation
    clv_points: float  # Points of CLV
    clv_percentage: float  # CLV as percentage

    # Direction
    pick_direction: str  # "over" or "under"
    line_movement: str  # "toward_us" or "away_from_us"

    # Result
    actual_result: float  # Actual player performance
    pick_result: str  # "hit", "miss", or "push"

    # CLV quality
    clv_grade: str  # "A", "B", "C", "D", "F"
    was_profitable: bool  # Whether CLV translated to win

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "play": {
                "id": self.play_id,
                "player": self.player_name,
                "stat": self.stat_type,
                "date": self.prediction_date,
            },
            "lines": {
                "our_line": self.our_line,
                "closing_line": self.closing_line,
            },
            "clv": {
                "points": round(self.clv_points, 2),
                "percentage": round(self.clv_percentage, 3),
                "grade": self.clv_grade,
            },
            "direction": {
                "pick": self.pick_direction,
                "movement": self.line_movement,
            },
            "result": {
                "actual": self.actual_result,
                "pick": self.pick_result,
                "profitable": self.was_profitable,
            },
        }


@dataclass
class CLVPerformance:
    """CLV performance metrics over time."""

    total_picks: int = 0
    picks_with_clv: int = 0

    # CLV statistics
    avg_clv_points: float = 0.0
    avg_clv_percentage: float = 0.0
    total_clv_points: float = 0.0

    # Grade distribution
    grade_distribution: dict[str, int] = field(default_factory=dict)

    # Conversion
    clv_to_win_rate: float = 0.0  # % of positive CLV picks that won

    # By direction
    toward_us_clv: float = 0.0  # Avg CLV when market agrees
    away_from_us_clv: float = 0.0  # Avg CLV when market disagrees

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "totals": {
                "picks": self.total_picks,
                "with_clv": self.picks_with_clv,
            },
            "clv_stats": {
                "avg_points": round(self.avg_clv_points, 3),
                "avg_pct": round(self.avg_clv_percentage, 3),
                "total_points": round(self.total_clv_points, 2),
            },
            "grades": self.grade_distribution,
            "conversion": {
                "clv_to_win_rate": round(self.clv_to_win_rate, 3),
            },
            "by_direction": {
                "toward_us": round(self.toward_us_clv, 3),
                "away_from_us": round(self.away_from_us_clv, 3),
            },
        }


class CLVTracker:
    """Track Closing Line Value as a performance signal.

    CLV measures whether we beat the closing line - a strong indicator
    of long-term profitability even before results are known.
    """

    # CLV grade thresholds
    GRADE_THRESHOLDS = {
        "A": 0.03,  # 3%+ CLV
        "B": 0.015,  # 1.5-3% CLV
        "C": 0.005,  # 0.5-1.5% CLV
        "D": 0.0,  # 0-0.5% CLV
        "F": -1.0,  # Negative CLV
    }

    def __init__(self) -> None:
        """Initialize CLV tracker."""
        self.records: list[CLVRecord] = []
        self.by_player: dict[str, list[CLVRecord]] = {}
        self.by_stat: dict[str, list[CLVRecord]] = {}

    def record_clv(
        self,
        play_id: str,
        player_name: str,
        stat_type: str,
        prediction_date: str,
        our_line: float,
        closing_line: float,
        pick_direction: str,  # "over" or "under"
        actual_result: float,
        pick_result: str,
    ) -> CLVRecord:
        """Record CLV for a pick.

        Args:
            play_id: Play identifier
            player_name: Player name
            stat_type: Stat type
            prediction_date: Date of prediction
            our_line: Line when we made the pick
            closing_line: Final closing line
            pick_direction: "over" or "under"
            actual_result: Actual player performance
            pick_result: "hit", "miss", or "push"

        Returns:
            CLVRecord
        """
        # Calculate CLV
        if pick_direction == "over":
            # We took the over
            # CLV = closing_line - our_line (higher line = better for over)
            clv_points = closing_line - our_line
            line_movement = "toward_us" if closing_line > our_line else "away_from_us"
        else:
            # We took the under
            # CLV = our_line - closing_line (lower line = better for under)
            clv_points = our_line - closing_line
            line_movement = "toward_us" if closing_line < our_line else "away_from_us"

        # CLV percentage
        clv_percentage = clv_points / our_line if our_line != 0 else 0

        # Grade CLV
        clv_grade = self._grade_clv(clv_percentage)

        # Was it profitable?
        was_profitable = pick_result == "hit"

        record = CLVRecord(
            play_id=play_id,
            player_name=player_name,
            stat_type=stat_type,
            prediction_date=prediction_date,
            our_line=our_line,
            closing_line=closing_line,
            clv_points=clv_points,
            clv_percentage=clv_percentage,
            pick_direction=pick_direction,
            line_movement=line_movement,
            actual_result=actual_result,
            pick_result=pick_result,
            clv_grade=clv_grade,
            was_profitable=was_profitable,
        )

        # Store record
        self.records.append(record)

        if player_name not in self.by_player:
            self.by_player[player_name] = []
        self.by_player[player_name].append(record)

        if stat_type not in self.by_stat:
            self.by_stat[stat_type] = []
        self.by_stat[stat_type].append(record)

        return record

    def _grade_clv(self, clv_percentage: float) -> str:
        """Grade CLV based on percentage."""
        for grade, threshold in sorted(self.GRADE_THRESHOLDS.items(), key=lambda x: -x[1]):
            if clv_percentage >= threshold:
                return grade
        return "F"

    def calculate_performance(
        self,
        records: list[CLVRecord] | None = None,
    ) -> CLVPerformance:
        """Calculate CLV performance metrics.

        Args:
            records: Records to analyze (all if None)

        Returns:
            CLVPerformance metrics
        """
        if records is None:
            records = self.records

        if not records:
            return CLVPerformance()

        # Filter records with valid CLV
        valid_records = [r for r in records if r.closing_line != 0]

        if not valid_records:
            return CLVPerformance(total_picks=len(records))

        # Calculate statistics
        total_clv_points = sum(r.clv_points for r in valid_records)
        avg_clv_points = total_clv_points / len(valid_records)
        avg_clv_pct = sum(r.clv_percentage for r in valid_records) / len(valid_records)

        # Grade distribution
        grade_dist: dict[str, int] = {}
        for r in valid_records:
            grade_dist[r.clv_grade] = grade_dist.get(r.clv_grade, 0) + 1

        # CLV to win conversion
        positive_clv_picks = [r for r in valid_records if r.clv_percentage > 0]
        if positive_clv_picks:
            wins = sum(1 for r in positive_clv_picks if r.was_profitable)
            clv_to_win = wins / len(positive_clv_picks)
        else:
            clv_to_win = 0.0

        # By direction
        toward_records = [r for r in valid_records if r.line_movement == "toward_us"]
        away_records = [r for r in valid_records if r.line_movement == "away_from_us"]

        toward_clv = sum(r.clv_percentage for r in toward_records) / len(toward_records) if toward_records else 0
        away_clv = sum(r.clv_percentage for r in away_records) / len(away_records) if away_records else 0

        return CLVPerformance(
            total_picks=len(records),
            picks_with_clv=len(valid_records),
            avg_clv_points=avg_clv_points,
            avg_clv_percentage=avg_clv_pct,
            total_clv_points=total_clv_points,
            grade_distribution=grade_dist,
            clv_to_win_rate=clv_to_win,
            toward_us_clv=toward_clv,
            away_from_us_clv=away_clv,
        )

    def get_player_clv(self, player_name: str) -> CLVPerformance:
        """Get CLV performance for a specific player."""
        records = self.by_player.get(player_name, [])
        return self.calculate_performance(records)

    def get_stat_clv(self, stat_type: str) -> CLVPerformance:
        """Get CLV performance for a specific stat type."""
        records = self.by_stat.get(stat_type, [])
        return self.calculate_performance(records)

    def get_recent_clv(
        self,
        window: int = 30,
    ) -> CLVPerformance:
        """Get CLV for recent picks.

        Args:
            window: Number of most recent picks to analyze

        Returns:
            CLVPerformance for recent window
        """
        recent = self.records[-window:] if len(self.records) > window else self.records
        return self.calculate_performance(recent)

    def identify_clv_patterns(self) -> dict[str, Any]:
        """Identify patterns in CLV performance."""
        if len(self.records) < 20:
            return {"error": "Insufficient data for pattern analysis"}

        # Compare first half vs second half
        mid = len(self.records) // 2
        first_half = self.calculate_performance(self.records[:mid])
        second_half = self.calculate_performance(self.records[mid:])

        # Trend analysis
        trend = "improving" if second_half.avg_clv_percentage > first_half.avg_clv_percentage else "declining"

        # Best performing stat types
        stat_performance = {
            stat: self.get_stat_clv(stat).avg_clv_percentage
            for stat in self.by_stat.keys()
        }
        best_stats = sorted(stat_performance.items(), key=lambda x: x[1], reverse=True)[:3]

        # CLV consistency
        clv_values = [r.clv_percentage for r in self.records if r.closing_line != 0]
        if clv_values:
            import statistics
            clv_std = statistics.stdev(clv_values) if len(clv_values) > 1 else 0
            consistency = "consistent" if clv_std < 0.02 else "volatile"
        else:
            consistency = "unknown"

        return {
            "trend": trend,
            "first_half_clv": round(first_half.avg_clv_percentage, 3),
            "second_half_clv": round(second_half.avg_clv_percentage, 3),
            "best_stat_types": [s[0] for s in best_stats],
            "consistency": consistency,
            "recommendation": self._generate_recommendation(trend, consistency, second_half),
        }

    def _generate_recommendation(
        self,
        trend: str,
        consistency: str,
        recent_performance: CLVPerformance,
    ) -> str:
        """Generate recommendation based on CLV patterns."""
        if trend == "improving" and recent_performance.avg_clv_percentage > 0.02:
            return "Strong performance - maintain current edge detection"
        elif trend == "declining" and recent_performance.avg_clv_percentage < 0.01:
            return "Consider reviewing line timing and source selection"
        elif consistency == "volatile":
            return "Inconsistent CLV - may need more stable line sources"
        else:
            return "Performance stable - no changes needed"

    def export_clv_report(self) -> dict[str, Any]:
        """Export complete CLV report."""
        overall = self.calculate_performance()
        recent = self.get_recent_clv(window=30)
        patterns = self.identify_clv_patterns()

        return {
            "overall": overall.to_dict(),
            "recent_30": recent.to_dict(),
            "patterns": patterns,
            "by_player": {
                player: perf.to_dict()
                for player, perf in [
                    (p, self.get_player_clv(p))
                    for p in self.by_player.keys()
                ]
            },
            "by_stat": {
                stat: perf.to_dict()
                for stat, perf in [
                    (s, self.get_stat_clv(s))
                    for s in self.by_stat.keys()
                ]
            },
        }
