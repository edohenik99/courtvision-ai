"""Market package for market-quality evaluation.

This package provides modular market logic:
- quality: Market quality scoring and threshold evaluation
- evaluator: Market context evaluation and filtering
"""

from __future__ import annotations

from courtvision.market.evaluator import (
    MarketContext,
    MarketEvaluator,
    evaluate_market_context,
    score_market_quality,
)
from courtvision.market.quality import (
    MarketQualityConfig,
    MarketQualityScorer,
    filter_player_markets,
    normalize_market_alias,
)

__all__ = [
    # evaluator
    "MarketContext",
    "MarketEvaluator",
    "evaluate_market_context",
    "score_market_quality",
    # quality
    "MarketQualityConfig",
    "MarketQualityScorer",
    "filter_player_markets",
    "normalize_market_alias",
]
