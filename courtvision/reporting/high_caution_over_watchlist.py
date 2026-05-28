from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from courtvision.context.player_identity import SOURCE_IDENTITY_CONFLICT_COLUMNS

WATCHLIST_FILE_PREFIX = "high_caution_over_watchlist"
KELLY_PROJECTED_SKIP_REASON = "context_high_caution_over"
FINAL_ELITE_REJECTION_REASON = "elite_reject_context_high_caution_over"
OBSERVATION_ONLY_NOTE = (
    "These candidates are excluded from Elite/Kelly and are for paper tracking only."
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
    "kelly_projected_skip_reason",
    "final_elite_rejection_reason",
    "identity_resolution_category",
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


def _reason_mask(df: pd.DataFrame) -> pd.Series:
    mask = pd.Series(False, index=df.index)
    if "kelly_projected_skip_reason" in df.columns:
        mask = mask | (
            df["kelly_projected_skip_reason"]
            .fillna("")
            .astype(str)
            .str.strip()
            .eq(KELLY_PROJECTED_SKIP_REASON)
        )
    if "final_elite_rejection_reason" in df.columns:
        mask = mask | (
            df["final_elite_rejection_reason"]
            .fillna("")
            .astype(str)
            .str.strip()
            .eq(FINAL_ELITE_REJECTION_REASON)
        )
    return mask


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


def build_high_caution_over_watchlist(full_market_df: pd.DataFrame) -> pd.DataFrame:
    """Build an observation-only watchlist from an already-produced full market board."""

    if not isinstance(full_market_df, pd.DataFrame) or full_market_df.empty:
        return pd.DataFrame(columns=WATCHLIST_COLUMNS)

    prepared = _prepared_watchlist_frame(full_market_df)
    watchlist = prepared.loc[_reason_mask(prepared), list(WATCHLIST_COLUMNS)].copy()
    if "edge" in watchlist.columns:
        watchlist["_sort_edge"] = pd.to_numeric(watchlist["edge"], errors="coerce")
        watchlist = watchlist.sort_values(
            "_sort_edge",
            ascending=False,
            na_position="last",
            kind="mergesort",
        ).drop(columns=["_sort_edge"])
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


def write_high_caution_over_watchlist(
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
    watchlist = build_high_caution_over_watchlist(source_df)
    output_path = watchlist_path_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    watchlist.to_csv(output_path, index=False)
    return output_path, watchlist


def watchlist_row_line(row: pd.Series) -> str:
    player = _safe_text(row.get("player_name")) or "Unknown"
    market = _safe_text(row.get("market_type")) or "unknown"
    selection = _safe_text(row.get("selection")) or "n/a"
    line = _safe_text(row.get("line")) or "n/a"
    edge = _safe_text(row.get("edge")) or "n/a"
    confidence = _safe_text(row.get("confidence")) or "n/a"
    reason = (
        _safe_text(row.get("kelly_projected_skip_reason"))
        or _safe_text(row.get("final_elite_rejection_reason"))
        or "n/a"
    )
    return (
        f"{player}: {market} {selection} {line} "
        f"(edge={edge}, confidence={confidence}, reason={reason})"
    )


__all__ = [
    "FINAL_ELITE_REJECTION_REASON",
    "KELLY_PROJECTED_SKIP_REASON",
    "OBSERVATION_ONLY_NOTE",
    "WATCHLIST_COLUMNS",
    "build_high_caution_over_watchlist",
    "watchlist_path_for_date",
    "watchlist_row_line",
    "write_high_caution_over_watchlist",
]
