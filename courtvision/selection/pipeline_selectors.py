from __future__ import annotations

import pandas as pd


def select_top_per_market(candidates: pd.DataFrame, per_market_limit: int = 20) -> pd.DataFrame:
    """Select the top full-market candidates per market using current pipeline ranking."""
    if candidates.empty:
        return candidates

    sort_column = "selection_score" if "selection_score" in candidates.columns else "quality_score"
    selected_groups = []
    for _, group in candidates.groupby("market_type", sort=False):
        selected_groups.append(group.sort_values(sort_column, ascending=False).head(per_market_limit))
    selected_df = pd.concat(selected_groups).copy() if selected_groups else pd.DataFrame()

    if "selection_rejection_reason" in selected_df.columns:
        selected_df["selection_rejection_reason"] = ""
    return selected_df


__all__ = ["select_top_per_market"]
