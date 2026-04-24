"""Correlation detection for portfolio plays.

Detects correlations between plays:
- Same player multiple props
- Same game props
- Stat dependencies (points vs assists, PRA combinations)

Assigns correlation scores for portfolio optimization.

Phase 10: Correlation and Portfolio Optimization
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np


class CorrelationType(str, Enum):
    """Types of correlations between plays."""

    SAME_PLAYER = "same_player"  # Same player, different stats
    SAME_GAME = "same_game"  # Different players, same game
    STAT_DEPENDENCY = "stat_dependency"  # Stat types that move together
    PRA_COMBINATION = "pra_combination"  # Points+Rebounds+Assists
    OPPOSING_TEAM = "opposing_team"  # Head-to-head correlation
    NONE = "none"


@dataclass
class CorrelationScore:
    """Correlation score between two plays."""

    play_1_id: str
    play_2_id: str
    correlation_type: CorrelationType
    correlation_score: float  # -1 to 1 (0 = independent)
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "play_1": self.play_1_id,
            "play_2": self.play_2_id,
            "type": self.correlation_type.value,
            "score": round(self.correlation_score, 3),
            "explanation": self.explanation,
        }


@dataclass
class PlayIdentity:
    """Identity information for a play."""

    play_id: str
    player_name: str
    player_id: int | None = None
    stat_type: str = ""
    game_id: str = ""
    team: str = ""
    opponent: str = ""
    line_value: float = 0.0
    projection: float = 0.0


class CorrelationMatrix:
    """Matrix of correlations between all plays in portfolio."""

    def __init__(self, play_ids: list[str]) -> None:
        """Initialize correlation matrix.

        Args:
            play_ids: List of play identifiers
        """
        self.play_ids = play_ids
        self.n = len(play_ids)
        self.play_index = {pid: i for i, pid in enumerate(play_ids)}

        # Initialize identity matrix (diagonal = 1, off-diagonal = 0)
        self.matrix = np.eye(self.n)

        # Store correlation details
        self.correlations: dict[tuple[str, str], CorrelationScore] = {}

    def set_correlation(
        self,
        play_1_id: str,
        play_2_id: str,
        score: float,
        correlation_type: CorrelationType,
        explanation: str = "",
    ) -> None:
        """Set correlation between two plays.

        Args:
            play_1_id: First play ID
            play_2_id: Second play ID
            score: Correlation score (-1 to 1)
            correlation_type: Type of correlation
            explanation: Human-readable explanation
        """
        if play_1_id not in self.play_index or play_2_id not in self.play_index:
            return

        i = self.play_index[play_1_id]
        j = self.play_index[play_2_id]

        # Set symmetric correlation
        self.matrix[i, j] = score
        self.matrix[j, i] = score

        # Store details (only store once)
        key = tuple(sorted([play_1_id, play_2_id]))
        self.correlations[key] = CorrelationScore(
            play_1_id=play_1_id,
            play_2_id=play_2_id,
            correlation_type=correlation_type,
            correlation_score=score,
            explanation=explanation,
        )

    def get_correlation(self, play_1_id: str, play_2_id: str) -> float:
        """Get correlation score between two plays."""
        if play_1_id not in self.play_index or play_2_id not in self.play_index:
            return 0.0

        i = self.play_index[play_1_id]
        j = self.play_index[play_2_id]
        return float(self.matrix[i, j])

    def get_correlation_details(
        self,
        play_1_id: str,
        play_2_id: str,
    ) -> CorrelationScore | None:
        """Get detailed correlation information."""
        key = tuple(sorted([play_1_id, play_2_id]))
        return self.correlations.get(key)

    def get_high_correlations(self, threshold: float = 0.3) -> list[CorrelationScore]:
        """Get all correlations above threshold.

        Args:
            threshold: Minimum correlation score (absolute value)

        Returns:
            List of high correlations
        """
        return [
            score for score in self.correlations.values()
            if abs(score.correlation_score) >= threshold
        ]

    def get_portfolio_variance_multiplier(self) -> float:
        """Calculate variance multiplier for entire portfolio.

        If all plays independent: multiplier = 1.0
        If positive correlations: multiplier > 1.0 (higher risk)
        If negative correlations: multiplier < 1.0 (lower risk)

        Returns:
            Variance multiplier
        """
        if self.n <= 1:
            return 1.0

        # Sum all correlations (excluding diagonal)
        total_correlation = np.sum(self.matrix) - self.n  # Subtract diagonal (all 1s)
        num_pairs = self.n * (self.n - 1)

        if num_pairs == 0:
            return 1.0

        avg_correlation = total_correlation / num_pairs

        # Variance multiplier formula
        # If avg correlation = 0: multiplier = 1
        # If avg correlation > 0: multiplier > 1 (increased variance)
        # If avg correlation < 0: multiplier < 1 (decreased variance)
        return 1.0 + avg_correlation

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "plays": self.play_ids,
            "matrix": self.matrix.tolist(),
            "high_correlations": [
                c.to_dict() for c in self.get_high_correlations()
            ],
            "variance_multiplier": round(self.get_portfolio_variance_multiplier(), 3),
        }


class CorrelationDetector:
    """Detect correlations between plays in portfolio.

    Analyzes play identities to determine correlation types and scores.
    """

    # Stat dependencies (stats that tend to move together)
    STAT_DEPENDENCIES: dict[str, list[str]] = {
        "points": ["pra", "threes", "assists"],  # High pts often means high pra/assists
        "pra": ["points", "rebounds", "assists"],  # PRA components
        "assists": ["points", "pra"],  # High assists often means high pts/PRA
        "rebounds": ["pra"],
        "threes": ["points"],
    }

    # Correlation scores by type
    CORRELATION_SCORES = {
        CorrelationType.SAME_PLAYER: 0.6,  # Same player, different stats
        CorrelationType.SAME_GAME: 0.3,   # Same game, different players
        CorrelationType.STAT_DEPENDENCY: 0.4,  # Stat types that correlate
        CorrelationType.PRA_COMBINATION: 0.8,  # PRA + component stats (very correlated)
        CorrelationType.OPPOSING_TEAM: -0.2,  # Head-to-head (slightly negative)
        CorrelationType.NONE: 0.0,
    }

    def __init__(self) -> None:
        """Initialize correlation detector."""
        self.plays: dict[str, PlayIdentity] = {}

    def add_play(self, identity: PlayIdentity) -> None:
        """Add a play to analyze.

        Args:
            identity: Play identity information
        """
        self.plays[identity.play_id] = identity

    def detect_correlation(
        self,
        play_1_id: str,
        play_2_id: str,
    ) -> CorrelationScore:
        """Detect correlation between two plays.

        Args:
            play_1_id: First play ID
            play_2_id: Second play ID

        Returns:
            CorrelationScore with type and score
        """
        play_1 = self.plays.get(play_1_id)
        play_2 = self.plays.get(play_2_id)

        if not play_1 or not play_2:
            return CorrelationScore(
                play_1_id=play_1_id,
                play_2_id=play_2_id,
                correlation_type=CorrelationType.NONE,
                correlation_score=0.0,
                explanation="Play not found",
            )

        # Check for same player
        if play_1.player_name == play_2.player_name:
            return self._score_same_player(play_1, play_2)

        # Check for same game
        if play_1.game_id == play_2.game_id:
            return self._score_same_game(play_1, play_2)

        # Check for opposing teams (head-to-head)
        if play_1.team == play_2.opponent and play_1.opponent == play_2.team:
            return self._score_opposing(play_1, play_2)

        # Default: no correlation
        return CorrelationScore(
            play_1_id=play_1_id,
            play_2_id=play_2_id,
            correlation_type=CorrelationType.NONE,
            correlation_score=0.0,
            explanation="No significant correlation detected",
        )

    def _score_same_player(
        self,
        play_1: PlayIdentity,
        play_2: PlayIdentity,
    ) -> CorrelationScore:
        """Score correlation for same player plays."""
        base_score = self.CORRELATION_SCORES[CorrelationType.SAME_PLAYER]

        # Check for PRA combination
        stat_types = {play_1.stat_type, play_2.stat_type}
        if "pra" in stat_types:
            component = stat_types - {"pra"}
            if component:
                comp = component.pop()
                if comp in ["points", "rebounds", "assists"]:
                    return CorrelationScore(
                        play_1_id=play_1.play_id,
                        play_2_id=play_2.play_id,
                        correlation_type=CorrelationType.PRA_COMBINATION,
                        correlation_score=self.CORRELATION_SCORES[CorrelationType.PRA_COMBINATION],
                        explanation=f"PRA and {comp} are highly correlated (PRA includes {comp})",
                    )

        # Check for stat dependencies
        if play_2.stat_type in self.STAT_DEPENDENCIES.get(play_1.stat_type, []):
            dep_score = self.CORRELATION_SCORES[CorrelationType.STAT_DEPENDENCY]
            return CorrelationScore(
                play_1_id=play_1.play_id,
                play_2_id=play_2.play_id,
                correlation_type=CorrelationType.STAT_DEPENDENCY,
                correlation_score=dep_score,
                explanation=f"{play_1.stat_type} and {play_2.stat_type} tend to move together",
            )

        # Default same-player correlation
        return CorrelationScore(
            play_1_id=play_1.play_id,
            play_2_id=play_2.play_id,
            correlation_type=CorrelationType.SAME_PLAYER,
            correlation_score=base_score,
            explanation=f"Same player ({play_1.player_name}), different stats",
        )

    def _score_same_game(
        self,
        play_1: PlayIdentity,
        play_2: PlayIdentity,
    ) -> CorrelationScore:
        """Score correlation for same game plays."""
        base_score = self.CORRELATION_SCORES[CorrelationType.SAME_GAME]

        # Same team correlation is higher
        if play_1.team == play_2.team:
            score = base_score * 1.3
            explanation = f"Same game, same team ({play_1.team})"
        else:
            score = base_score
            explanation = f"Same game, different teams ({play_1.team} vs {play_2.team})"

        return CorrelationScore(
            play_1_id=play_1.play_id,
            play_2_id=play_2.play_id,
            correlation_type=CorrelationType.SAME_GAME,
            correlation_score=min(score, 0.5),  # Cap at 0.5
            explanation=explanation,
        )

    def _score_opposing(
        self,
        play_1: PlayIdentity,
        play_2: PlayIdentity,
    ) -> CorrelationScore:
        """Score correlation for opposing team plays."""
        score = self.CORRELATION_SCORES[CorrelationType.OPPOSING_TEAM]

        return CorrelationScore(
            play_1_id=play_1.play_id,
            play_2_id=play_2.play_id,
            correlation_type=CorrelationType.OPPOSING_TEAM,
            correlation_score=score,
            explanation=f"Head-to-head: {play_1.team} vs {play_2.team} (slightly negative correlation)",
        )

    def build_correlation_matrix(self, play_ids: list[str] | None = None) -> CorrelationMatrix:
        """Build full correlation matrix for all plays.

        Args:
            play_ids: Specific play IDs to include (all if None)

        Returns:
            CorrelationMatrix with all pairwise correlations
        """
        if play_ids is None:
            play_ids = list(self.plays.keys())

        matrix = CorrelationMatrix(play_ids)

        # Compute all pairwise correlations
        for i, play_1_id in enumerate(play_ids):
            for play_2_id in play_ids[i + 1:]:
                correlation = self.detect_correlation(play_1_id, play_2_id)
                matrix.set_correlation(
                    play_1_id=play_1_id,
                    play_2_id=play_2_id,
                    score=correlation.correlation_score,
                    correlation_type=correlation.correlation_type,
                    explanation=correlation.explanation,
                )

        return matrix

    def find_correlated_groups(
        self,
        threshold: float = 0.5,
    ) -> list[list[str]]:
        """Find groups of plays that are highly correlated.

        Args:
            threshold: Minimum correlation to be considered related

        Returns:
            List of play ID groups
        """
        matrix = self.build_correlation_matrix()
        play_ids = matrix.play_ids

        # Build groups using union-find approach
        groups: list[set[str]] = []

        for i, play_1_id in enumerate(play_ids):
            # Find which group this play belongs to
            assigned_group = None
            for group in groups:
                if play_1_id in group:
                    assigned_group = group
                    break

            if assigned_group is None:
                assigned_group = {play_1_id}
                groups.append(assigned_group)

            # Check correlations with other plays
            for play_2_id in play_ids[i + 1:]:
                corr = matrix.get_correlation(play_1_id, play_2_id)
                if abs(corr) >= threshold:
                    assigned_group.add(play_2_id)

        return [sorted(list(g)) for g in groups]

    def get_risk_concentration(self) -> dict[str, Any]:
        """Analyze risk concentration in portfolio.

        Returns:
            Risk concentration metrics
        """
        # Count plays per player
        player_counts: dict[str, int] = {}
        game_counts: dict[str, int] = {}
        stat_counts: dict[str, int] = {}

        for play in self.plays.values():
            player_counts[play.player_name] = player_counts.get(play.player_name, 0) + 1
            game_counts[play.game_id] = game_counts.get(play.game_id, 0) + 1
            stat_counts[play.stat_type] = stat_counts.get(play.stat_type, 0) + 1

        # Find concentrations
        max_player = max(player_counts.items(), key=lambda x: x[1]) if player_counts else ("", 0)
        max_game = max(game_counts.items(), key=lambda x: x[1]) if game_counts else ("", 0)
        max_stat = max(stat_counts.items(), key=lambda x: x[1]) if stat_counts else ("", 0)

        return {
            "player_concentration": {
                "max_exposure": max_player[1],
                "max_exposure_player": max_player[0],
                "multi_prop_players": [p for p, c in player_counts.items() if c > 1],
            },
            "game_concentration": {
                "max_exposure": max_game[1],
                "max_exposure_game": max_game[0],
                "total_games": len(game_counts),
            },
            "stat_concentration": {
                "max_exposure": max_stat[1],
                "max_exposure_stat": max_stat[0],
            },
        }
