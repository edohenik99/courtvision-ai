"""Market context evaluation.

This module provides comprehensive market context evaluation
for assessing market quality and suitability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from courtvision.calibration.buckets import to_float
from courtvision.market.quality import MarketQualityConfig, MarketQualityScorer


@dataclass(frozen=True, slots=True)
class MarketContext:
    """Context for market evaluation."""

    market_type: str
    is_live: bool
    has_odds: bool
    sportsbook_line: float | None
    odds: float | None
    edge: float | None
    confidence: float | None
    quality_score: float | None
    minutes_projection: float | None
    injury_impact: float | None


def score_market_quality(
    candidate: Mapping[str, Any],
    config: MarketQualityConfig | None = None,
) -> dict[str, Any]:
    """Compute comprehensive market quality score.

    Returns dict with quality metrics:
    - base_score: Raw quality score
    - weighted_score: Market-type weighted score
    - quality_band: Elite/high/mid/low classification
    - passes_thresholds: Boolean pass/fail
    """
    cfg = config or MarketQualityConfig()
    scorer = MarketQualityScorer(cfg)

    market_type = str(candidate.get("market_type", ""))

    # Extract values
    edge = to_float(candidate.get("edge")) or to_float(candidate.get("edge_abs")) or 0.0
    confidence = to_float(candidate.get("confidence")) or 0.0
    quality_score = to_float(candidate.get("quality_score"))

    # If quality_score not pre-computed, calculate base
    if quality_score is None:
        quality_score = (confidence * 100.0) + (edge * 8.0)

    # Apply market type weight
    weight = scorer.market_type_weight(market_type)
    weighted_score = quality_score * weight

    # Determine band
    band = scorer.quality_band(weighted_score)

    # Check thresholds
    passes = scorer.passes_minimum_thresholds(edge, confidence, weighted_score)

    return {
        "market_type": market_type,
        "base_score": round(float(quality_score), 4),
        "weighted_score": round(float(weighted_score), 4),
        "quality_band": band,
        "market_type_weight": round(float(weight), 4),
        "passes_thresholds": passes,
        "edge": round(float(edge), 4),
        "confidence": round(float(confidence), 4),
    }


def evaluate_market_context(
    candidate: Mapping[str, Any],
    injury_context: Mapping[str, Any] | None = None,
    config: MarketQualityConfig | None = None,
) -> dict[str, Any]:
    """Evaluate complete market context for a candidate.

    Combines quality scoring with injury and live market context.

    Returns comprehensive evaluation dict with:
    - quality: Quality scoring results
    - context: Market context flags
    - eligible: Overall eligibility determination
    """
    cfg = config or MarketQualityConfig()

    # Base quality score
    quality_result = score_market_quality(candidate, cfg)

    # Build context flags
    market_type = str(candidate.get("market_type", ""))
    is_live = bool(candidate.get("is_live_market", False))
    has_odds = to_float(candidate.get("odds")) is not None
    synthetic = bool(candidate.get("synthetic_line", False))

    sportsbook_line = to_float(candidate.get("sportsbook_line"))
    minutes = to_float(candidate.get("minutes_projection")) or to_float(candidate.get("minutes_avg"))

    # Injury impact from candidate or context
    injury_impact = to_float(candidate.get("injury_impact_score"))
    if injury_impact is None and injury_context:
        team = str(candidate.get("team", "")).upper()
        teams_ctx = injury_context.get("teams", {})
        if isinstance(teams_ctx, Mapping) and team in teams_ctx:
            injury_impact = to_float(teams_ctx[team].get("impact_score"))

    # Eligibility determination
    eligibility_reasons: list[str] = []
    ineligible_reasons: list[str] = []

    if quality_result["passes_thresholds"]:
        eligibility_reasons.append("quality_pass")
    else:
        ineligible_reasons.append("quality_below_threshold")

    if is_live:
        eligibility_reasons.append("live_market")
    else:
        ineligible_reasons.append("not_live_market")

    if not synthetic:
        eligibility_reasons.append("not_synthetic")
    else:
        ineligible_reasons.append("synthetic_line")

    if has_odds:
        eligibility_reasons.append("has_odds")

    # Player-specific checks
    if market_type.startswith("player_"):
        if minutes is not None and minutes >= cfg.min_confidence * 50:  # rough minutes proxy
            eligibility_reasons.append("minutes_ok")
        elif minutes is not None:
            ineligible_reasons.append("low_minutes")

        if injury_impact is not None and injury_impact > 0.5:
            ineligible_reasons.append("high_injury_impact")
        elif injury_impact is not None and injury_impact > 0.25:
            eligibility_reasons.append("moderate_injury_ok")

    eligible = len(ineligible_reasons) == 0 and quality_result["passes_thresholds"]

    return {
        "quality": quality_result,
        "context": {
            "market_type": market_type,
            "is_live": is_live,
            "has_odds": has_odds,
            "synthetic_line": synthetic,
            "sportsbook_line": sportsbook_line,
            "minutes_projection": minutes,
            "injury_impact": injury_impact,
        },
        "eligibility": {
            "eligible": eligible,
            "reasons": eligibility_reasons,
            "disqualifiers": ineligible_reasons,
        },
    }


class MarketEvaluator:
    """Evaluator for comprehensive market analysis."""

    def __init__(self, config: MarketQualityConfig | None = None) -> None:
        self.config = config or MarketQualityConfig()
        self.scorer = MarketQualityScorer(self.config)

    def evaluate(
        self,
        candidate: Mapping[str, Any],
        injury_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate market context comprehensively."""
        return evaluate_market_context(candidate, injury_context, self.config)

    def score_quality(self, candidate: Mapping[str, Any]) -> dict[str, Any]:
        """Score market quality only."""
        return score_market_quality(candidate, self.config)

    def passes_thresholds(self, edge: float, confidence: float, quality_score: float) -> bool:
        """Check if values pass minimum thresholds."""
        return self.scorer.passes_minimum_thresholds(edge, confidence, quality_score)

    def market_weight(self, market_type: str) -> float:
        """Get weight for market type."""
        return self.scorer.market_type_weight(market_type)
