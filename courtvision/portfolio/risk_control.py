"""Portfolio risk control with exposure limits.

Limits:
- Total exposure per game
- Total exposure per player
- Total exposure per stat type

Phase 10: Correlation and Portfolio Optimization
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExposureLimits:
    """Exposure limits configuration."""

    # Per-game limits
    max_plays_per_game: int = 3
    max_exposure_pct_per_game: float = 0.15  # 15% of portfolio in one game

    # Per-player limits
    max_props_per_player: int = 2
    max_exposure_pct_per_player: float = 0.10  # 10% of portfolio on one player

    # Per-stat limits
    max_plays_per_stat: int = 5
    max_exposure_pct_per_stat: float = 0.25  # 25% of portfolio on one stat type

    # Portfolio limits
    max_total_plays: int = 20
    max_same_game_parlay_legs: int = 2  # SGP max legs from same game

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "per_game": {
                "max_plays": self.max_plays_per_game,
                "max_exposure_pct": self.max_exposure_pct_per_game,
            },
            "per_player": {
                "max_props": self.max_props_per_player,
                "max_exposure_pct": self.max_exposure_pct_per_player,
            },
            "per_stat": {
                "max_plays": self.max_plays_per_stat,
                "max_exposure_pct": self.max_exposure_pct_per_stat,
            },
            "portfolio": {
                "max_total_plays": self.max_total_plays,
                "max_sgp_legs_same_game": self.max_same_game_parlay_legs,
            },
        }


@dataclass
class ExposureStatus:
    """Current exposure status for a portfolio."""

    total_plays: int = 0
    total_exposure: float = 0.0  # Sum of all bet sizes

    # Per-game exposure
    game_exposures: dict[str, float] = field(default_factory=dict)
    game_counts: dict[str, int] = field(default_factory=dict)

    # Per-player exposure
    player_exposures: dict[str, float] = field(default_factory=dict)
    player_counts: dict[str, int] = field(default_factory=dict)

    # Per-stat exposure
    stat_exposures: dict[str, float] = field(default_factory=dict)
    stat_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total": {
                "plays": self.total_plays,
                "exposure": round(self.total_exposure, 2),
            },
            "by_game": {
                game: {
                    "exposure": round(exp, 2),
                    "count": self.game_counts.get(game, 0),
                }
                for game, exp in self.game_exposures.items()
            },
            "by_player": {
                player: {
                    "exposure": round(exp, 2),
                    "count": self.player_counts.get(player, 0),
                }
                for player, exp in self.player_exposures.items()
            },
            "by_stat": {
                stat: {
                    "exposure": round(exp, 2),
                    "count": self.stat_counts.get(stat, 0),
                }
                for stat, exp in self.stat_exposures.items()
            },
        }


@dataclass
class RiskViolation:
    """A risk limit violation."""

    violation_type: str  # game, player, stat, total
    entity: str  # game_id, player_name, stat_type
    current_value: float
    limit_value: float
    severity: str  # warning, critical
    message: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "type": self.violation_type,
            "entity": self.entity,
            "current": self.current_value,
            "limit": self.limit_value,
            "severity": self.severity,
            "message": self.message,
        }


class RiskController:
    """Control portfolio risk through exposure limits.

    Tracks and enforces limits on:
    - Exposure per game
    - Exposure per player
    - Exposure per stat type
    - Total portfolio size
    """

    def __init__(self, limits: ExposureLimits | None = None) -> None:
        """Initialize risk controller.

        Args:
            limits: Exposure limits configuration
        """
        self.limits = limits or ExposureLimits()
        self.status = ExposureStatus()
        self.violations: list[RiskViolation] = []

    def reset(self) -> None:
        """Reset exposure tracking."""
        self.status = ExposureStatus()
        self.violations = []

    def add_play(
        self,
        play_id: str,
        player_name: str,
        game_id: str,
        stat_type: str,
        bet_size: float = 1.0,
    ) -> bool:
        """Add a play to the portfolio.

        Args:
            play_id: Unique play identifier
            player_name: Player name
            game_id: Game identifier
            stat_type: Stat type
            bet_size: Bet size (for exposure calculation)

        Returns:
            True if added successfully, False if rejected
        """
        # Check total plays limit
        if self.status.total_plays >= self.limits.max_total_plays:
            self.violations.append(RiskViolation(
                violation_type="total",
                entity="portfolio",
                current_value=self.status.total_plays,
                limit_value=self.limits.max_total_plays,
                severity="critical",
                message=f"Maximum total plays ({self.limits.max_total_plays}) reached",
            ))
            return False

        # Check per-game limit
        current_game_count = self.status.game_counts.get(game_id, 0)
        if current_game_count >= self.limits.max_plays_per_game:
            self.violations.append(RiskViolation(
                violation_type="game",
                entity=game_id,
                current_value=current_game_count,
                limit_value=self.limits.max_plays_per_game,
                severity="critical",
                message=f"Maximum plays per game ({self.limits.max_plays_per_game}) reached for {game_id}",
            ))
            return False

        # Check per-player limit
        current_player_count = self.status.player_counts.get(player_name, 0)
        if current_player_count >= self.limits.max_props_per_player:
            self.violations.append(RiskViolation(
                violation_type="player",
                entity=player_name,
                current_value=current_player_count,
                limit_value=self.limits.max_props_per_player,
                severity="critical",
                message=f"Maximum props per player ({self.limits.max_props_per_player}) reached for {player_name}",
            ))
            return False

        # Check per-stat limit
        current_stat_count = self.status.stat_counts.get(stat_type, 0)
        if current_stat_count >= self.limits.max_plays_per_stat:
            self.violations.append(RiskViolation(
                violation_type="stat",
                entity=stat_type,
                current_value=current_stat_count,
                limit_value=self.limits.max_plays_per_stat,
                severity="critical",
                message=f"Maximum plays per stat ({self.limits.max_plays_per_stat}) reached for {stat_type}",
            ))
            return False

        # Check exposure percentages (if total exposure > 0)
        if self.status.total_exposure > 0:
            # Game exposure
            game_exposure = self.status.game_exposures.get(game_id, 0)
            new_game_pct = (game_exposure + bet_size) / (self.status.total_exposure + bet_size)
            if new_game_pct > self.limits.max_exposure_pct_per_game:
                self.violations.append(RiskViolation(
                    violation_type="game_exposure",
                    entity=game_id,
                    current_value=round(new_game_pct, 3),
                    limit_value=self.limits.max_exposure_pct_per_game,
                    severity="warning",
                    message=f"Game {game_id} exposure would exceed {self.limits.max_exposure_pct_per_game:.1%}",
                ))
                # Still allow but warn

            # Player exposure
            player_exposure = self.status.player_exposures.get(player_name, 0)
            new_player_pct = (player_exposure + bet_size) / (self.status.total_exposure + bet_size)
            if new_player_pct > self.limits.max_exposure_pct_per_player:
                self.violations.append(RiskViolation(
                    violation_type="player_exposure",
                    entity=player_name,
                    current_value=round(new_player_pct, 3),
                    limit_value=self.limits.max_exposure_pct_per_player,
                    severity="warning",
                    message=f"Player {player_name} exposure would exceed {self.limits.max_exposure_pct_per_player:.1%}",
                ))

            # Stat exposure
            stat_exposure = self.status.stat_exposures.get(stat_type, 0)
            new_stat_pct = (stat_exposure + bet_size) / (self.status.total_exposure + bet_size)
            if new_stat_pct > self.limits.max_exposure_pct_per_stat:
                self.violations.append(RiskViolation(
                    violation_type="stat_exposure",
                    entity=stat_type,
                    current_value=round(new_stat_pct, 3),
                    limit_value=self.limits.max_exposure_pct_per_stat,
                    severity="warning",
                    message=f"Stat {stat_type} exposure would exceed {self.limits.max_exposure_pct_per_stat:.1%}",
                ))

        # Add the play
        self.status.total_plays += 1
        self.status.total_exposure += bet_size

        self.status.game_counts[game_id] = self.status.game_counts.get(game_id, 0) + 1
        self.status.game_exposures[game_id] = self.status.game_exposures.get(game_id, 0) + bet_size

        self.status.player_counts[player_name] = self.status.player_counts.get(player_name, 0) + 1
        self.status.player_exposures[player_name] = self.status.player_exposures.get(player_name, 0) + bet_size

        self.status.stat_counts[stat_type] = self.status.stat_counts.get(stat_type, 0) + 1
        self.status.stat_exposures[stat_type] = self.status.stat_exposures.get(stat_type, 0) + bet_size

        return True

    def remove_play(
        self,
        play_id: str,
        player_name: str,
        game_id: str,
        stat_type: str,
        bet_size: float = 1.0,
    ) -> None:
        """Remove a play from the portfolio."""
        self.status.total_plays = max(0, self.status.total_plays - 1)
        self.status.total_exposure = max(0.0, self.status.total_exposure - bet_size)

        self.status.game_counts[game_id] = max(0, self.status.game_counts.get(game_id, 0) - 1)
        self.status.game_exposures[game_id] = max(0.0, self.status.game_exposures.get(game_id, 0) - bet_size)

        self.status.player_counts[player_name] = max(0, self.status.player_counts.get(player_name, 0) - 1)
        self.status.player_exposures[player_name] = max(0.0, self.status.player_exposures.get(player_name, 0) - bet_size)

        self.status.stat_counts[stat_type] = max(0, self.status.stat_counts.get(stat_type, 0) - 1)
        self.status.stat_exposures[stat_type] = max(0.0, self.status.stat_exposures.get(stat_type, 0) - bet_size)

    def check_limits(self) -> list[RiskViolation]:
        """Check current portfolio against limits.

        Returns:
            List of current violations
        """
        violations = []

        # Check game exposures
        for game_id, exposure in self.status.game_exposures.items():
            pct = exposure / self.status.total_exposure if self.status.total_exposure > 0 else 0
            if pct > self.limits.max_exposure_pct_per_game:
                violations.append(RiskViolation(
                    violation_type="game_exposure",
                    entity=game_id,
                    current_value=round(pct, 3),
                    limit_value=self.limits.max_exposure_pct_per_game,
                    severity="warning",
                    message=f"Game {game_id} exposure at {pct:.1%}",
                ))

        # Check player exposures
        for player_name, exposure in self.status.player_exposures.items():
            pct = exposure / self.status.total_exposure if self.status.total_exposure > 0 else 0
            if pct > self.limits.max_exposure_pct_per_player:
                violations.append(RiskViolation(
                    violation_type="player_exposure",
                    entity=player_name,
                    current_value=round(pct, 3),
                    limit_value=self.limits.max_exposure_pct_per_player,
                    severity="warning",
                    message=f"Player {player_name} exposure at {pct:.1%}",
                ))

        # Check stat exposures
        for stat_type, exposure in self.status.stat_exposures.items():
            pct = exposure / self.status.total_exposure if self.status.total_exposure > 0 else 0
            if pct > self.limits.max_exposure_pct_per_stat:
                violations.append(RiskViolation(
                    violation_type="stat_exposure",
                    entity=stat_type,
                    current_value=round(pct, 3),
                    limit_value=self.limits.max_exposure_pct_per_stat,
                    severity="warning",
                    message=f"Stat {stat_type} exposure at {pct:.1%}",
                ))

        return violations

    def get_status(self) -> dict[str, Any]:
        """Get current risk status."""
        current_violations = self.check_limits()

        return {
            "exposure": self.status.to_dict(),
            "violations": [v.to_dict() for v in current_violations],
            "is_compliant": len(current_violations) == 0,
            "risk_level": (
                "low" if len(current_violations) == 0
                else "medium" if len([v for v in current_violations if v.severity == "warning"]) <= 2
                else "high"
            ),
        }

    def can_add_play(
        self,
        player_name: str,
        game_id: str,
        stat_type: str,
    ) -> tuple[bool, str]:
        """Check if a play can be added without violating limits.

        Args:
            player_name: Player name
            game_id: Game ID
            stat_type: Stat type

        Returns:
            (can_add, reason) tuple
        """
        # Check total plays
        if self.status.total_plays >= self.limits.max_total_plays:
            return False, f"Maximum total plays ({self.limits.max_total_plays}) reached"

        # Check game limit
        if self.status.game_counts.get(game_id, 0) >= self.limits.max_plays_per_game:
            return False, f"Maximum plays per game ({self.limits.max_plays_per_game}) reached"

        # Check player limit
        if self.status.player_counts.get(player_name, 0) >= self.limits.max_props_per_player:
            return False, f"Maximum props per player ({self.limits.max_props_per_player}) reached"

        # Check stat limit
        if self.status.stat_counts.get(stat_type, 0) >= self.limits.max_plays_per_stat:
            return False, f"Maximum plays per stat ({self.limits.max_plays_per_stat}) reached"

        return True, "OK"
