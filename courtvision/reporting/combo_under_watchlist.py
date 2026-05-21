from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from courtvision.context.player_identity import SOURCE_IDENTITY_CONFLICT_COLUMNS

WATCHLIST_FILE_PREFIX = "combo_under_watchlist"
OBSERVATION_ONLY_NOTE = "These combo UNDER candidates are not Elite/Kelly eligible yet."
COMBO_UNDER_MARKETS: frozenset[str] = frozenset(
    {
        "player_points_assists",
        "player_points_rebounds",
        "player_points_rebounds_assists",
    }
)

WATCHLIST_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "player_id",
    "player_name",
    "team_abbr",
    "opponent",
    "market_type",
    "selection",
    "line",
    "model_projection",
    "edge",
    "confidence",
    "quality_score",
    "selection_score",
    "odds",
    "context_pick_alignment",
    "context_caution_level",
    "context_conflict_cause",
    "final_elite_rejection_reason",
    "result_status",
    "actual_value",
    *SOURCE_IDENTITY_CONFLICT_COLUMNS,
)


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _norm_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series("", index=df.index)
    return df[column].fillna("").astype(str).str.strip().str.lower()


def _prepared_watchlist_frame(source_df: pd.DataFrame) -> pd.DataFrame:
    prepared = source_df.copy()
    fallback_columns = {
        "team_abbr": "team",
        "line": "sportsbook_line",
        "model_projection": "projection",
    }
    for column, fallback in fallback_columns.items():
        if column not in prepared.columns and fallback in prepared.columns:
            prepared[column] = prepared[fallback]
    for column in WATCHLIST_COLUMNS:
        if column not in prepared.columns:
            prepared[column] = ""
    return prepared


def build_combo_under_watchlist(full_market_df: pd.DataFrame) -> pd.DataFrame:
    """Build a reporting-only combo UNDER watchlist from the current full market board."""

    if not isinstance(full_market_df, pd.DataFrame) or full_market_df.empty:
        return pd.DataFrame(columns=WATCHLIST_COLUMNS)

    prepared = _prepared_watchlist_frame(full_market_df)
    market = _norm_series(prepared, "market_type")
    selection = _norm_series(prepared, "selection")
    alignment = _norm_series(prepared, "context_pick_alignment")
    caution = _norm_series(prepared, "context_caution_level")
    mask = (
        market.isin(COMBO_UNDER_MARKETS)
        & selection.eq("under")
        & alignment.eq("aligned")
        & caution.eq("low")
    )
    watchlist = prepared.loc[mask, list(WATCHLIST_COLUMNS)].copy()
    if watchlist.empty:
        return pd.DataFrame(columns=WATCHLIST_COLUMNS)

    watchlist["_abs_edge_sort"] = pd.to_numeric(watchlist["edge"], errors="coerce").abs()
    watchlist = watchlist.sort_values(
        "_abs_edge_sort",
        ascending=False,
        na_position="last",
        kind="mergesort",
    ).drop(columns=["_abs_edge_sort"])
    return watchlist.reset_index(drop=True)


def read_full_market_board(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=WATCHLIST_COLUMNS)
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=WATCHLIST_COLUMNS)


def watchlist_path_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"{WATCHLIST_FILE_PREFIX}_{prediction_date}.csv"


def write_combo_under_watchlist(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    full_market_df: pd.DataFrame | None = None,
) -> tuple[Path, pd.DataFrame]:
    runtime_root = Path(runtime_root)
    operator_dir = runtime_root / "operator"
    source_df = (
        full_market_df
        if isinstance(full_market_df, pd.DataFrame)
        else read_full_market_board(operator_dir / f"full_market_board_{prediction_date}.csv")
    )
    watchlist = build_combo_under_watchlist(source_df)
    output_path = watchlist_path_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    watchlist.to_csv(output_path, index=False)
    return output_path, watchlist


def watchlist_row_line(row: pd.Series) -> str:
    player = _safe_text(row.get("player_name")) or "Unknown"
    team = _safe_text(row.get("team_abbr")) or "n/a"
    opponent = _safe_text(row.get("opponent")) or "n/a"
    market = _safe_text(row.get("market_type")) or "unknown"
    line = _safe_text(row.get("line")) or "n/a"
    edge = _safe_text(row.get("edge")) or "n/a"
    confidence = _safe_text(row.get("confidence")) or "n/a"
    reason = _safe_text(row.get("final_elite_rejection_reason")) or "n/a"
    return (
        f"{player} ({team} vs {opponent}): {market} under {line} "
        f"(edge={edge}, confidence={confidence}, final_elite_rejection_reason={reason})"
    )


__all__ = [
    "COMBO_UNDER_MARKETS",
    "OBSERVATION_ONLY_NOTE",
    "WATCHLIST_COLUMNS",
    "build_combo_under_watchlist",
    "watchlist_path_for_date",
    "watchlist_row_line",
    "write_combo_under_watchlist",
]
