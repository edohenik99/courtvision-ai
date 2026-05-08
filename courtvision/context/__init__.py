"""Context-layer helpers for passive operator diagnostics."""

from .manual_player_context import (
    MANUAL_CONTEXT_COLUMNS,
    MANUAL_CONTEXT_OUTPUT_COLUMNS,
    apply_manual_player_context,
    load_manual_player_context,
    write_manual_context_diagnostics,
)
from .game_context import (
    GAME_CONTEXT_COLUMNS,
    apply_game_context,
    write_game_context_outputs,
)
from .game_strength import (
    POWER_RATING_CONTEXT_COLUMNS,
    apply_power_rating_context_to_df,
    get_matchup_context,
    get_matchup_context_batch,
)

__all__ = [
    "MANUAL_CONTEXT_COLUMNS",
    "MANUAL_CONTEXT_OUTPUT_COLUMNS",
    "GAME_CONTEXT_COLUMNS",
    "POWER_RATING_CONTEXT_COLUMNS",
    "apply_manual_player_context",
    "apply_game_context",
    "apply_power_rating_context_to_df",
    "get_matchup_context",
    "get_matchup_context_batch",
    "load_manual_player_context",
    "write_manual_context_diagnostics",
    "write_game_context_outputs",
]
