"""Shadow run runner - orchestrates paper trading mode.

Runs full system, records all decisions and context, without execution.

VALIDATE + CALIBRATE mode - Measurement and validation only.

Task C: Add "paper trading / shadow run" mode
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from courtvision.shadow_run.artifact import ShadowRunArtifact, ShadowRunEntry


class ShadowRunRunner:
    """Runner for shadow (paper trading) mode.

    Records all system decisions and context without assuming execution.
    Enables later comparison against closing lines and actual results.

    Usage:
        runner = ShadowRunRunner(config={
            "thresholds": {"edge": 0.05, "confidence": 0.65},
            "market_regime": "neutral",
        })

        for candidate in candidates:
            entry = runner.evaluate_candidate(candidate, context)
            runner.add_entry(entry)

        artifact = runner.finalize()
        artifact.save("shadow_run_2024-01-01.json")
    """

    def __init__(
        self,
        prediction_date: str,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize shadow run runner.

        Args:
            prediction_date: Date of predictions
            config: System configuration used for this run
        """
        self.prediction_date = prediction_date
        self.config = config or {}

        # Create artifact
        self.artifact = ShadowRunArtifact(
            artifact_id=f"shadow_{prediction_date}_{uuid.uuid4().hex[:8]}",
            created_at=datetime.now().isoformat(),
            prediction_date=prediction_date,
            mode="shadow",
            config=config or {},
        )

        # Current thresholds and conditions
        self.current_thresholds: dict[str, float] = {}
        self.current_market_regime: str = "neutral"
        self.current_market_conditions: dict[str, Any] = {}

    def set_context(
        self,
        thresholds: dict[str, float] | None = None,
        market_regime: str = "",
        market_conditions: dict[str, Any] | None = None,
    ) -> None:
        """Set current context for subsequent entries.

        Args:
            thresholds: Current thresholds used
            market_regime: Current market regime
            market_conditions: Additional market conditions
        """
        if thresholds:
            self.current_thresholds = thresholds
        if market_regime:
            self.current_market_regime = market_regime
        if market_conditions:
            self.current_market_conditions = market_conditions

    def evaluate_candidate(
        self,
        candidate: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> ShadowRunEntry:
        """Evaluate a candidate and create shadow run entry.

        Args:
            candidate: Candidate play dict with keys:
                - player_name, stat_type, over_under
                - line_value, odds, projected_value
                - confidence, edge, ev
            context: Additional context

        Returns:
            ShadowRunEntry with decision
        """
        # Apply thresholds to determine recommendation
        thresholds = self.current_thresholds or self.config.get("thresholds", {})

        edge_threshold = thresholds.get("edge", 0.05)
        confidence_threshold = thresholds.get("confidence", 0.65)
        ev_threshold = thresholds.get("ev", 0.03)

        edge = candidate.get("edge", 0)
        confidence = candidate.get("confidence", 0)
        ev = candidate.get("ev", 0)

        # Decision logic
        recommended = (
            edge >= edge_threshold and
            confidence >= confidence_threshold and
            ev >= ev_threshold
        )

        rejection_reason = ""
        if not recommended:
            reasons = []
            if edge < edge_threshold:
                reasons.append(f"edge {edge:.3f} < {edge_threshold:.3f}")
            if confidence < confidence_threshold:
                reasons.append(f"confidence {confidence:.3f} < {confidence_threshold:.3f}")
            if ev < ev_threshold:
                reasons.append(f"ev {ev:.3f} < {ev_threshold:.3f}")
            rejection_reason = "; ".join(reasons)

        # Portfolio inclusion (more selective)
        portfolio_included = recommended and edge >= edge_threshold * 1.2

        # Create entry
        entry = ShadowRunEntry(
            entry_id=f"entry_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.now().isoformat(),
            prediction_date=self.prediction_date,
            player_name=candidate.get("player_name", ""),
            stat_type=candidate.get("stat_type", ""),
            over_under=candidate.get("over_under", ""),
            line_value=candidate.get("line_value", 0),
            odds=candidate.get("odds", -110),
            projected_value=candidate.get("projected_value", 0),
            confidence=confidence,
            edge=edge,
            ev=ev,
            recommended=recommended,
            portfolio_included=portfolio_included,
            rejection_reason=rejection_reason,
            thresholds_used=self.current_thresholds.copy(),
            market_regime=self.current_market_regime,
            market_conditions=self.current_market_conditions.copy(),
        )

        return entry

    def add_entry(self, entry: ShadowRunEntry) -> None:
        """Add entry to artifact."""
        self.artifact.add_entry(entry)

    def add_batch(self, candidates: list[dict[str, Any]]) -> list[ShadowRunEntry]:
        """Process batch of candidates.

        Args:
            candidates: List of candidate dicts

        Returns:
            List of created entries
        """
        entries = []
        for candidate in candidates:
            entry = self.evaluate_candidate(candidate)
            self.add_entry(entry)
            entries.append(entry)
        return entries

    def finalize(self) -> ShadowRunArtifact:
        """Finalize shadow run and return artifact."""
        self.artifact.finalize()
        return self.artifact

    def get_summary(self) -> dict[str, Any]:
        """Get current summary of shadow run."""
        return {
            "prediction_date": self.prediction_date,
            "entries_recorded": self.artifact.total_candidates,
            "portfolio_size": self.artifact.portfolio_size,
            "selection_rate": (
                self.artifact.portfolio_size / self.artifact.total_candidates
                if self.artifact.total_candidates > 0 else 0
            ),
            "current_thresholds": self.current_thresholds,
            "current_regime": self.current_market_regime,
        }


def create_shadow_run_from_pipeline(
    prediction_date: str,
    pipeline_result: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> ShadowRunArtifact:
    """Create shadow run artifact from pipeline result.

    Convenience function to convert pipeline output to shadow run format.

    Args:
        prediction_date: Date of predictions
        pipeline_result: Result from prediction pipeline
        config: System configuration

    Returns:
        ShadowRunArtifact
    """
    runner = ShadowRunRunner(prediction_date, config)

    # Extract candidates from pipeline result
    # Adjust keys based on actual pipeline output structure
    candidates = pipeline_result.get("candidates", [])

    # Set context from pipeline
    runner.set_context(
        thresholds=config.get("thresholds", {}) if config else {},
        market_regime=pipeline_result.get("market_regime", "neutral"),
    )

    # Process all candidates
    for candidate in candidates:
        entry = runner.evaluate_candidate(candidate)
        runner.add_entry(entry)

    return runner.finalize()
