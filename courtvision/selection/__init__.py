"""Selection and lane assignment package.

This package contains:
- boards.py: Legacy Projection-based board builders (to be deprecated)
- operator_boards.py: New DataFrame-based board construction
- lanes.py: Lane assignment and board classification
"""

from .lanes import BoardLane, classify_candidate_lane, classify_candidates_batch
from .operator_boards import (
    assign_candidate_lanes,
    build_operator_boards,
    compute_board_diversity_metrics,
    apply_diversity_penalty,
)

__all__ = [
    "BoardLane",
    "classify_candidate_lane",
    "classify_candidates_batch",
    "assign_candidate_lanes",
    "build_operator_boards",
    "compute_board_diversity_metrics",
    "apply_diversity_penalty",
]
