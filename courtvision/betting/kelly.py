"""Kelly Criterion bet sizing module for CourtVision AI.

Provides stake fraction calculations based on edge, odds, and confidence.
Uses conservative quarter-Kelly sizing with strict confidence minimums.
"""

from __future__ import annotations

# Maximum stake fraction (2% of bankroll for conservative sizing)
MAX_STAKE_FRACTION = 0.02

# Minimum confidence threshold for betting
MIN_CONFIDENCE_THRESHOLD = 0.55


def compute_kelly_fraction(edge: float, odds: float, confidence: float) -> float:
    """Compute conservative Kelly stake fraction for a bet.

    Uses quarter-Kelly sizing with strict minimum confidence threshold.
    Edge represents model advantage / expected ROI signal.

    Args:
        edge: Expected value as decimal (e.g., 0.08 for 8% edge)
        odds: Decimal odds (e.g., 1.91 for -110 American odds)
        confidence: Confidence multiplier 0 to 1 (must be >= 0.55)

    Returns:
        Fraction of bankroll to bet, capped at MAX_STAKE_FRACTION (2%)
        Returns 0 for insufficient edge, confidence, or invalid inputs

    Example:
        >>> compute_kelly_fraction(0.08, 1.91, 0.85)
        0.0177  # Quarter-Kelly capped at 0.02
        >>> compute_kelly_fraction(-0.05, 1.91, 0.90)
        0.0  # Negative edge, no bet
        >>> compute_kelly_fraction(0.08, 1.91, 0.50)
        0.0  # Confidence too low, no bet
    """
    # Validate inputs
    if edge is None or odds is None or confidence is None:
        return 0.0
    
    # Edge must be positive for betting
    if edge <= 0:
        return 0.0
    
    # Invalid odds (must be > 1.0 for betting)
    if odds <= 1.0:
        return 0.0
    
    # Minimum confidence threshold (no bet if confidence too low)
    if confidence < MIN_CONFIDENCE_THRESHOLD:
        return 0.0
    
    # Convert decimal odds to b (net odds received on win)
    # For decimal odds 1.91: you risk 1 to win 0.91
    b = odds - 1.0
    
    if b <= 0:
        return 0.0
    
    # Raw Kelly fraction: edge / b
    raw_fraction = edge / b
    
    # Adjust by confidence (partial Kelly)
    confidence_adjusted = raw_fraction * confidence
    
    # Apply quarter-Kelly conservatism
    quarter_kelly = confidence_adjusted * 0.25
    
    # Cap at maximum stake fraction (2%)
    final_stake = min(quarter_kelly, MAX_STAKE_FRACTION)
    
    # Ensure non-negative
    if final_stake < 0:
        return 0.0
    
    # Round to 4 decimal places
    return round(final_stake, 4)


def compute_recommended_bet(
    edge: float,
    odds: float,
    confidence: float,
    bankroll: float,
) -> dict[str, float]:
    """Compute complete bet sizing recommendation.

    Args:
        edge: Expected value as decimal
        odds: Decimal odds
        confidence: Confidence multiplier 0 to 1
        bankroll: Current bankroll amount

    Returns:
        Dict with stake_fraction and recommended_bet_amount
    """
    stake_fraction = compute_kelly_fraction(edge, odds, confidence)
    recommended_bet = round(bankroll * stake_fraction, 2)
    
    return {
        "stake_fraction": stake_fraction,
        "recommended_bet": recommended_bet,
    }
