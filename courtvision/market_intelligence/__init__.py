"""Phase 11: Market Adaptation and Opponent Modeling.

Adjust strategy based on how sportsbooks and markets behave over time.

Components:
- line_movement: Track opening vs closing lines, detect sharp vs public movement
- clv_tracker: Closing Line Value tracking and performance signal
- bias_detection: Identify over/under and stat-type market inefficiencies
- reaction_modeling: Detect market overreactions to injuries/form/narratives
- adaptive_strategy: Dynamic threshold and edge requirement adjustment
- hooks: Integration with prediction pipeline

Phase 11 adds external market intelligence layer for strategy adaptation.
"""

from __future__ import annotations

from courtvision.market_intelligence.line_movement import (
    LineMovementAnalyzer,
    LineMovementType,
    LineSnapshot,
)
from courtvision.market_intelligence.clv_tracker import (
    CLVRecord,
    CLVTracker,
)
from courtvision.market_intelligence.market_snapshots import (
    MarketSnapshotIdentity,
    market_snapshot_key,
)
from courtvision.market_intelligence.bias_detection import (
    BiasDetector,
    MarketBias,
)
from courtvision.market_intelligence.reaction_modeling import (
    MarketReaction,
    ReactionDetector,
)
from courtvision.market_intelligence.adaptive_strategy import (
    AdaptiveConfig,
    AdaptiveStrategy,
)
from courtvision.market_intelligence.hooks import MarketIntelligenceHooks

__all__ = [
    # Line Movement
    "LineMovementAnalyzer",
    "LineMovementType",
    "LineSnapshot",
    # CLV
    "CLVRecord",
    "CLVTracker",
    "MarketSnapshotIdentity",
    "market_snapshot_key",
    # Bias Detection
    "BiasDetector",
    "MarketBias",
    # Reaction Modeling
    "MarketReaction",
    "ReactionDetector",
    # Adaptive Strategy
    "AdaptiveConfig",
    "AdaptiveStrategy",
    # Integration
    "MarketIntelligenceHooks",
]
