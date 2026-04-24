"""Daily run summary artifact generator.

Creates compact summary after each shadow run with:
- date, mode, thresholds, candidate/play counts
- averages (confidence, EV, hit probability, robustness)
- concentration metrics, market regime
- top plays and rejection reasons

Produces structured JSON + readable TXT/MD.

OPERATIONS + VALIDATION mode - Daily artifact quality only.

Task 2: Create daily run summary artifact
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from courtvision.shadow_run.artifact import ShadowRunArtifact, ShadowRunEntry


@dataclass
class DailySummary:
    """Daily run summary artifact."""

    # Identification
    summary_id: str
    prediction_date: str
    created_at: str

    # Operator context
    operator_mode: str
    thresholds_used: dict[str, float]

    # Volume metrics
    raw_candidates: int
    validated_plays: int
    final_portfolio_plays: int

    # Quality averages
    avg_confidence: float
    avg_ev: float
    avg_hit_probability: float
    avg_robustness: float | None

    # Concentration metrics
    games_covered: int
    max_plays_per_game: int
    unique_players: int
    unique_stat_types: int

    # Market context
    market_regime: str

    # Top plays (max 5)
    top_plays: list[dict[str, Any]]

    # Rejection analysis
    total_rejected: int
    top_rejection_reasons: list[tuple[str, int]]

    # Health flags
    health_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "metadata": {
                "summary_id": self.summary_id,
                "prediction_date": self.prediction_date,
                "created_at": self.created_at,
                "version": "1.0",
            },
            "operator_context": {
                "mode": self.operator_mode,
                "thresholds": self.thresholds_used,
            },
            "volume_metrics": {
                "raw_candidates": self.raw_candidates,
                "validated_plays": self.validated_plays,
                "final_portfolio_plays": self.final_portfolio_plays,
                "selection_rate": round(
                    self.final_portfolio_plays / max(1, self.raw_candidates), 3
                ),
            },
            "quality_averages": {
                "avg_confidence": round(self.avg_confidence, 3),
                "avg_ev": round(self.avg_ev, 4),
                "avg_hit_probability": round(self.avg_hit_probability, 3),
                "avg_robustness": round(self.avg_robustness, 3) if self.avg_robustness else None,
            },
            "concentration_metrics": {
                "games_covered": self.games_covered,
                "max_plays_per_game": self.max_plays_per_game,
                "unique_players": self.unique_players,
                "unique_stat_types": self.unique_stat_types,
            },
            "market_context": {
                "market_regime": self.market_regime,
            },
            "top_plays": self.top_plays,
            "rejection_analysis": {
                "total_rejected": self.total_rejected,
                "top_reasons": [
                    {"reason": reason, "count": count}
                    for reason, count in self.top_rejection_reasons
                ],
            },
            "health_flags": self.health_flags,
        }

    def to_markdown(self) -> str:
        """Generate human-readable markdown summary."""
        lines = [
            f"# Daily Run Summary: {self.prediction_date}",
            "",
            f"**Summary ID**: {self.summary_id}  ",
            f"**Generated**: {self.created_at}  ",
            f"**Operator Mode**: {self.operator_mode}",
            "",
            "## Volume Metrics",
            f"- Raw candidates: {self.raw_candidates}",
            f"- Validated plays: {self.validated_plays}",
            f"- Final portfolio: {self.final_portfolio_plays}",
            f"- Selection rate: {self.final_portfolio_plays / max(1, self.raw_candidates):.1%}",
            "",
            "## Quality Averages",
            f"- Average confidence: {self.avg_confidence:.1%}",
            f"- Average EV: {self.avg_ev:.2%}",
            f"- Average hit probability: {self.avg_hit_probability:.1%}",
        ]

        if self.avg_robustness:
            lines.append(f"- Average robustness: {self.avg_robustness:.3f}")

        lines.extend([
            "",
            "## Concentration Metrics",
            f"- Games covered: {self.games_covered}",
            f"- Max plays per game: {self.max_plays_per_game}",
            f"- Unique players: {self.unique_players}",
            f"- Unique stat types: {self.unique_stat_types}",
            "",
            f"## Market Context",
            f"- Market regime: {self.market_regime}",
            "",
        ])

        lines.extend([
            "## Top Plays",
            "",
        ])

        if self.top_plays:
            for i, play in enumerate(self.top_plays, 1):
                lines.extend([
                    f"{i}. **{play['player']} - {play['stat']} {play['over_under']} {play['line']}**",
                    f"   - Confidence: {play['confidence']:.1%}",
                    f"   - EV: {play['ev']:.2%}",
                    f"   - Edge: {play['edge']:.2%}",
                    f"   - Reason: {play['reason']}",
                    "",
                ])
        else:
            lines.append("_No plays selected_")
            lines.append("")

        lines.extend([
            "## Rejection Analysis",
            f"- Total rejected: {self.total_rejected}",
        ])

        if self.top_rejection_reasons:
            lines.append("- Top reasons:")
            for reason, count in self.top_rejection_reasons:
                lines.append(f"  - {reason}: {count}")
        else:
            lines.append("_No rejections recorded_")

        lines.append("")

        if self.health_flags:
            lines.extend([
                "## Health Flags ⚠️",
                "",
            ])
            for flag in self.health_flags:
                lines.append(f"- {flag}")
            lines.append("")

        lines.extend([
            "---",
            f"*Generated by CourtVision Shadow Run System*",
        ])

        return "\n".join(lines)

    def save(self, json_path: str, md_path: str | None = None) -> None:
        """Save summary to JSON and optional Markdown."""
        # JSON
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

        # Markdown
        if md_path:
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(self.to_markdown())


class DailySummaryGenerator:
    """Generate daily summary from shadow run artifact."""

    def __init__(self, artifact: ShadowRunArtifact) -> None:
        """Initialize with shadow run artifact.

        Args:
            artifact: Completed shadow run artifact
        """
        self.artifact = artifact

    def generate(self) -> DailySummary:
        """Generate daily summary from artifact."""
        # Get portfolio entries (plays that made it to portfolio)
        portfolio_entries = self.artifact.get_portfolio_entries()
        all_entries = self.artifact.entries

        # Calculate averages from portfolio plays
        avg_confidence = self._avg([e.confidence for e in portfolio_entries])
        avg_ev = self._avg([e.ev for e in portfolio_entries])
        avg_hit_prob = self._avg([e.confidence for e in portfolio_entries])  # Proxy

        # Robustness not directly in artifact, could be added if simulation ran
        avg_robustness = None

        # Concentration metrics
        games = set()
        players = set()
        stat_types = set()
        plays_per_game: dict[str, int] = {}

        for e in portfolio_entries:
            # Extract game_id from context if available
            game_id = e.market_conditions.get("game_id", "unknown")
            games.add(game_id)
            players.add(e.player_name)
            stat_types.add(e.stat_type)
            plays_per_game[game_id] = plays_per_game.get(game_id, 0) + 1

        max_plays_per_game = max(plays_per_game.values()) if plays_per_game else 0

        # Top plays (top 5 by EV)
        sorted_plays = sorted(
            portfolio_entries,
            key=lambda e: e.ev,
            reverse=True,
        )[:5]

        top_plays = [
            {
                "player": e.player_name,
                "stat": e.stat_type,
                "over_under": e.over_under,
                "line": e.line_value,
                "confidence": e.confidence,
                "ev": e.ev,
                "edge": e.edge,
                "reason": f"High EV ({e.ev:.2%}) with {e.confidence:.0%} confidence",
            }
            for e in sorted_plays
        ]

        # Rejection analysis
        rejected_entries = [e for e in all_entries if not e.recommended]
        total_rejected = len(rejected_entries)

        # Count rejection reasons
        reason_counts: dict[str, int] = {}
        for e in rejected_entries:
            reason = e.rejection_reason or "unknown"
            # Simplify: extract first part before semicolon
            simple_reason = reason.split(";")[0].strip()
            reason_counts[simple_reason] = reason_counts.get(simple_reason, 0) + 1

        # Top 3 reasons
        top_reasons = sorted(
            reason_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:3]

        # Health flags
        health_flags = self._detect_health_flags(
            all_entries, portfolio_entries, total_rejected
        )

        # Create summary
        summary = DailySummary(
            summary_id=f"daily_{self.artifact.prediction_date}",
            prediction_date=self.artifact.prediction_date,
            created_at=self.artifact.created_at,
            operator_mode=self.artifact.mode,
            thresholds_used=self._get_thresholds_from_entries(all_entries),
            raw_candidates=self.artifact.total_candidates,
            validated_plays=len([e for e in all_entries if e.recommended]),
            final_portfolio_plays=len(portfolio_entries),
            avg_confidence=avg_confidence,
            avg_ev=avg_ev,
            avg_hit_probability=avg_hit_prob,
            avg_robustness=avg_robustness,
            games_covered=len(games),
            max_plays_per_game=max_plays_per_game,
            unique_players=len(players),
            unique_stat_types=len(stat_types),
            market_regime=self._get_market_regime(all_entries),
            top_plays=top_plays,
            total_rejected=total_rejected,
            top_rejection_reasons=top_reasons,
            health_flags=health_flags,
        )

        return summary

    def _avg(self, values: list[float]) -> float:
        """Calculate average with safe handling of empty lists."""
        if not values:
            return 0.0
        return sum(values) / len(values)

    def _get_thresholds_from_entries(
        self, entries: list[ShadowRunEntry]
    ) -> dict[str, float]:
        """Extract thresholds used from first entry with thresholds."""
        for e in entries:
            if e.thresholds_used:
                return e.thresholds_used.copy()
        return {}

    def _get_market_regime(self, entries: list[ShadowRunEntry]) -> str:
        """Extract market regime from entries."""
        for e in entries:
            if e.market_regime:
                return e.market_regime
        return "unknown"

    def _detect_health_flags(
        self,
        all_entries: list[ShadowRunEntry],
        portfolio_entries: list[ShadowRunEntry],
        total_rejected: int,
    ) -> list[str]:
        """Detect health issues for operator attention."""
        flags = []

        # Zero candidates
        if len(all_entries) == 0:
            flags.append("CRITICAL: Zero candidates produced")

        # Zero portfolio plays
        if len(all_entries) > 0 and len(portfolio_entries) == 0:
            flags.append("WARNING: No plays made it to portfolio")

        # Excessive candidates
        if len(all_entries) > 500:
            flags.append(f"WARNING: Excessive candidates ({len(all_entries)})")

        # High rejection rate
        if len(all_entries) > 0:
            rejection_rate = total_rejected / len(all_entries)
            if rejection_rate > 0.95:
                flags.append(f"WARNING: Very high rejection rate ({rejection_rate:.1%})")

        # Low selection rate
        if len(all_entries) > 0:
            selection_rate = len(portfolio_entries) / len(all_entries)
            if selection_rate < 0.01:
                flags.append(f"WARNING: Very low selection rate ({selection_rate:.1%})")

        # Missing market regime
        if not any(e.market_regime for e in all_entries):
            flags.append("WARNING: No market regime recorded")

        return flags


def generate_daily_summary(
    artifact: ShadowRunArtifact,
    output_dir: str = "./shadow_runs",
) -> DailySummary:
    """Convenience function to generate and save daily summary.

    Args:
        artifact: Completed shadow run artifact
        output_dir: Directory for output files

    Returns:
        Generated DailySummary
    """
    import os

    generator = DailySummaryGenerator(artifact)
    summary = generator.generate()

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Generate file paths
    date_str = artifact.prediction_date
    json_path = os.path.join(output_dir, f"daily_summary_{date_str}.json")
    md_path = os.path.join(output_dir, f"daily_summary_{date_str}.md")

    # Save
    summary.save(json_path, md_path)

    return summary
