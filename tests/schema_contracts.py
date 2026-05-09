"""Minimum schema contracts for runtime artifacts.

These contracts are append-only by design: tests require only a minimum field set.
Adding new diagnostics/columns is allowed without test updates.
Removing or renaming required fields must be an intentional migration.
"""

FULL_MARKET_REQUIRED_COLUMNS = {
    # identity
    "prediction_date", "player_name", "team", "opponent", "game_id",
    # market
    "market_type", "selection", "line", "odds", "line_source",
    # model/scoring
    "model_projection", "edge", "confidence", "quality_score", "selection_score",
    # gates/context/manual
    "is_live_market", "context_caution_level", "context_pick_alignment",
    "same_opponent_under_warning", "manual_review_required",
    "fragility_score", "fragility_bucket", "survivability_score", "survivability_bucket",
}

ELITE_REQUIRED_COLUMNS = FULL_MARKET_REQUIRED_COLUMNS

KELLY_REQUIRED_COLUMNS = {
    "prediction_date", "player_name", "market_type", "selection", "line", "american_odds",
    "edge_pct", "confidence", "stake_fraction", "stake_amount", "kelly_eligible", "skip_reason",
    "manual_review_required", "recommended_action", "stake_policy",
}

QUALITY_SUMMARY_REQUIRED_KEYS = {
    "run_identity", "run_health_status", "candidate_funnel", "kelly_safety_summary", "warnings", "power_rating_context",
    "fragility_survivability_summary",
}

QUALITY_SUMMARY_CANDIDATE_FUNNEL_REQUIRED_KEYS = {
    "elite_board_count", "full_market_board_count", "kelly_rows_count",
}

BOARD_DIAGNOSTICS_REQUIRED_KEYS = {
    "prediction_date", "board_counts", "rejected_by_reason", "full_market_board", "final_board_construction",
}
