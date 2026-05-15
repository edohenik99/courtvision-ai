"""Selection and lane assignment package.

This package contains:
- boards.py: Legacy Projection-based board builders (to be deprecated)
- operator_boards.py: New DataFrame-based board construction
- lanes.py: Lane assignment and board classification
"""

from .lanes import BoardLane, classify_candidate_lane, classify_candidates_batch
from .operator_boards import (
    ACTIVE_OPERATOR_MARKETS,
    DUPLICATE_BETTING_IDENTITY_REASON,
    UNSUPPORTED_ACTIVE_OPERATOR_MARKET_REASON,
    assign_candidate_lanes,
    build_operator_boards,
    compute_board_diversity_metrics,
    duplicate_betting_identity_drop_summary,
    format_unsupported_active_operator_market_drop_line,
    apply_diversity_penalty,
    unsupported_active_operator_market_drop_summary,
)

__all__ = [
    "BoardLane",
    "ACTIVE_OPERATOR_MARKETS",
    "DUPLICATE_BETTING_IDENTITY_REASON",
    "UNSUPPORTED_ACTIVE_OPERATOR_MARKET_REASON",
    "classify_candidate_lane",
    "classify_candidates_batch",
    "assign_candidate_lanes",
    "build_operator_boards",
    "compute_board_diversity_metrics",
    "duplicate_betting_identity_drop_summary",
    "format_unsupported_active_operator_market_drop_line",
    "apply_diversity_penalty",
    "unsupported_active_operator_market_drop_summary",
]
