"""Shadow run artifact format for replayable storage.

Structured format for paper trading mode that records:
- Recommended plays and portfolio decisions
- Market conditions and thresholds used
- Full context for later comparison

VALIDATE + CALIBRATE mode - Measurement and validation only.

Task C: Add "paper trading / shadow run" mode
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ShadowRunEntry:
    """Single entry in shadow run - one recommended play."""

    # Identifiers
    entry_id: str
    timestamp: str
    prediction_date: str

    # Play details
    player_name: str
    stat_type: str
    over_under: str
    line_value: float
    odds: int

    # Predictions
    projected_value: float
    confidence: float
    edge: float
    ev: float

    # Decision
    recommended: bool
    portfolio_included: bool
    rejection_reason: str = ""

    # Context at time of prediction
    thresholds_used: dict[str, float] = field(default_factory=dict)
    market_regime: str = ""
    market_conditions: dict[str, Any] = field(default_factory=dict)

    # For later comparison
    closing_line: float | None = None
    closing_odds: int | None = None
    actual_result: float | None = None
    hit: bool | None = None
    clv: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp,
            "prediction_date": self.prediction_date,
            "play": {
                "player": self.player_name,
                "stat": self.stat_type,
                "over_under": self.over_under,
                "line": self.line_value,
                "odds": self.odds,
            },
            "predictions": {
                "projected": round(self.projected_value, 2),
                "confidence": round(self.confidence, 3),
                "edge": round(self.edge, 3),
                "ev": round(self.ev, 3),
            },
            "decision": {
                "recommended": self.recommended,
                "portfolio_included": self.portfolio_included,
                "rejection_reason": self.rejection_reason if self.rejection_reason else None,
            },
            "context": {
                "thresholds": self.thresholds_used,
                "market_regime": self.market_regime,
                "market_conditions": self.market_conditions,
            },
            "results": {
                "closing_line": self.closing_line,
                "closing_odds": self.closing_odds,
                "actual_result": self.actual_result,
                "hit": self.hit,
                "clv": self.clv,
            } if self.closing_line is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShadowRunEntry:
        """Create from dictionary."""
        play = data.get("play", {})
        preds = data.get("predictions", {})
        decision = data.get("decision", {})
        context = data.get("context", {})
        results = data.get("results")

        entry = cls(
            entry_id=data["entry_id"],
            timestamp=data["timestamp"],
            prediction_date=data["prediction_date"],
            player_name=play.get("player", ""),
            stat_type=play.get("stat", ""),
            over_under=play.get("over_under", ""),
            line_value=play.get("line", 0),
            odds=play.get("odds", -110),
            projected_value=preds.get("projected", 0),
            confidence=preds.get("confidence", 0),
            edge=preds.get("edge", 0),
            ev=preds.get("ev", 0),
            recommended=decision.get("recommended", False),
            portfolio_included=decision.get("portfolio_included", False),
            rejection_reason=decision.get("rejection_reason", ""),
            thresholds_used=context.get("thresholds", {}),
            market_regime=context.get("market_regime", ""),
            market_conditions=context.get("market_conditions", {}),
        )

        if results:
            entry.closing_line = results.get("closing_line")
            entry.closing_odds = results.get("closing_odds")
            entry.actual_result = results.get("actual_result")
            entry.hit = results.get("hit")
            entry.clv = results.get("clv")

        return entry


@dataclass
class ShadowRunArtifact:
    """Complete shadow run artifact for a prediction session."""

    # Metadata
    artifact_id: str
    created_at: str
    prediction_date: str
    mode: str = "shadow"  # "shadow" or "live"

    # System configuration at run time
    config: dict[str, Any] = field(default_factory=dict)

    # All entries
    entries: list[ShadowRunEntry] = field(default_factory=list)

    # Portfolio summary
    portfolio_size: int = 0
    total_candidates: int = 0
    selection_rate: float = 0.0

    def add_entry(self, entry: ShadowRunEntry) -> None:
        """Add entry to artifact."""
        self.entries.append(entry)

        # Update counts
        self.total_candidates += 1
        if entry.portfolio_included:
            self.portfolio_size += 1

    def finalize(self) -> None:
        """Finalize artifact after all entries added."""
        if self.total_candidates > 0:
            self.selection_rate = self.portfolio_size / self.total_candidates

    def update_results(
        self,
        entry_id: str,
        closing_line: float | None = None,
        closing_odds: int | None = None,
        actual_result: float | None = None,
        hit: bool | None = None,
    ) -> bool:
        """Update entry with results for later comparison.

        Args:
            entry_id: Entry to update
            closing_line: Closing line
            closing_odds: Closing odds
            actual_result: Actual player performance
            hit: Whether pick hit

        Returns:
            True if entry found and updated
        """
        for entry in self.entries:
            if entry.entry_id == entry_id:
                entry.closing_line = closing_line
                entry.closing_odds = closing_odds
                entry.actual_result = actual_result
                entry.hit = hit

                # Calculate CLV if we have closing line
                if closing_line is not None and entry.line_value != 0:
                    if entry.over_under == "over":
                        entry.clv = (closing_line - entry.line_value) / entry.line_value
                    else:
                        entry.clv = (entry.line_value - closing_line) / entry.line_value

                return True
        return False

    def get_recommended_entries(self) -> list[ShadowRunEntry]:
        """Get entries that were recommended."""
        return [e for e in self.entries if e.recommended]

    def get_portfolio_entries(self) -> list[ShadowRunEntry]:
        """Get entries included in portfolio."""
        return [e for e in self.entries if e.portfolio_included]

    def get_rejected_entries(self) -> list[ShadowRunEntry]:
        """Get entries that were rejected."""
        return [e for e in self.entries if not e.recommended and e.rejection_reason]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "metadata": {
                "artifact_id": self.artifact_id,
                "created_at": self.created_at,
                "prediction_date": self.prediction_date,
                "mode": self.mode,
            },
            "config": self.config,
            "summary": {
                "total_candidates": self.total_candidates,
                "portfolio_size": self.portfolio_size,
                "selection_rate": round(self.selection_rate, 3),
            },
            "entries": [e.to_dict() for e in self.entries],
        }

    def save(self, filepath: str) -> None:
        """Save artifact to JSON file."""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, filepath: str) -> ShadowRunArtifact:
        """Load artifact from JSON file."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        meta = data.get("metadata", {})

        artifact = cls(
            artifact_id=meta.get("artifact_id", ""),
            created_at=meta.get("created_at", ""),
            prediction_date=meta.get("prediction_date", ""),
            mode=meta.get("mode", "shadow"),
            config=data.get("config", {}),
        )

        for entry_data in data.get("entries", []):
            entry = ShadowRunEntry.from_dict(entry_data)
            artifact.entries.append(entry)
            artifact.total_candidates += 1
            if entry.portfolio_included:
                artifact.portfolio_size += 1

        if artifact.total_candidates > 0:
            artifact.selection_rate = artifact.portfolio_size / artifact.total_candidates

        return artifact

    def export_for_comparison(self, filepath: str) -> None:
        """Export simplified format for easy result comparison.

        Creates a CSV-like format for easy comparison with actual results.
        """
        lines = []
        lines.append(
            "entry_id,prediction_date,player,stat,over_under,line,confidence,"
            "recommended,portfolio_included,closing_line,actual_result,hit,clv"
        )

        for e in self.entries:
            if not e.recommended:
                continue  # Only include recommended plays for comparison

            lines.append(
                f"{e.entry_id},{e.prediction_date},{e.player_name},{e.stat_type},"
                f"{e.over_under},{e.line_value},{e.confidence},"
                f"{e.recommended},{e.portfolio_included},"
                f"{e.closing_line if e.closing_line else ''},"
                f"{e.actual_result if e.actual_result else ''},"
                f"{e.hit if e.hit is not None else ''},"
                f"{e.clv if e.clv is not None else ''}"
            )

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
