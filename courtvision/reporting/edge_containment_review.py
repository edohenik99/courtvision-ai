"""
Phase 12E: Edge Containment Live Review Overlay.

Read-only operator review overlay for risky high-edge combo OVERs, implementing
the suppress_high_edge_combo_over policy (Phase 12D verdict: READY_FOR_FORWARD_SHADOW)
as a REVIEW-ONLY flag.

Flagged rows receive review metadata so the operator can decide before betting.
Nothing is suppressed, removed, or altered.

This module DOES NOT:
  - remove rows from elite_board or full_market_board
  - change projections, edge, confidence, Kelly math
  - change selection thresholds or elite gates
  - change grading or history
  - modify any existing board CSV file
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Policy constants (Phase 12D: suppress_high_edge_combo_over)
# ---------------------------------------------------------------------------

REVIEW_POLICY_NAME:    str   = "suppress_high_edge_combo_over"
REVIEW_POLICY_VERDICT: str   = "READY_FOR_FORWARD_SHADOW"
EDGE_THRESHOLD:        float = 4.0

_COMBO_MARKETS: frozenset[str] = frozenset({
    "player_points_assists",
    "player_points_rebounds",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
})

# Review metadata values
_REVIEW_REQUIRED_META = {
    "edge_containment_review_required":    True,
    "edge_containment_policy":             REVIEW_POLICY_NAME,
    "edge_containment_reason":             "high_edge_combo_over_historical_inflation",
    "edge_containment_verdict":            REVIEW_POLICY_VERDICT,
    "edge_containment_recommended_action": "REVIEW_BEFORE_BET",
    "edge_containment_note":               "review_only_no_live_suppression",
}

_NOT_REQUIRED_META = {
    "edge_containment_review_required":    False,
    "edge_containment_policy":             "",
    "edge_containment_reason":             "",
    "edge_containment_verdict":            "",
    "edge_containment_recommended_action": "OK_TO_CONSIDER",
    "edge_containment_note":               "",
}

# Columns included in the review CSV artifact
_OUTPUT_COLUMNS: list[str] = [
    "prediction_date",
    "player_name",
    "market_type",
    "selection",
    "line",
    "edge",
    "confidence",
    "context_pick_alignment",
    "is_elite",
    "edge_containment_review_required",
    "edge_containment_policy",
    "edge_containment_reason",
    "edge_containment_recommended_action",
    "edge_containment_verdict",
    "edge_containment_note",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _flag_row(row: pd.Series) -> bool:
    """Return True if this row matches the suppress_high_edge_combo_over policy."""
    edge = pd.to_numeric(row.get("edge", None), errors="coerce")
    if edge is None or pd.isna(edge):
        return False
    selection = str(row.get("selection", "")).strip().lower()
    market_type = str(row.get("market_type", "")).strip().lower()
    return (
        edge >= EDGE_THRESHOLD
        and selection == "over"
        and market_type in _COMBO_MARKETS
    )


def _build_review_mask(df: pd.DataFrame) -> pd.Series:
    """Boolean Series aligned to df.index: True for rows matching review policy."""
    idx = df.index
    edge_n = pd.to_numeric(df["edge"], errors="coerce") if "edge" in df.columns else pd.Series(float("nan"), index=idx)
    sel = df["selection"].astype(str).str.strip().str.lower() if "selection" in df.columns else pd.Series("", index=idx)
    mt  = df["market_type"].astype(str).str.strip().str.lower() if "market_type" in df.columns else pd.Series("", index=idx)
    return (
        (edge_n >= EDGE_THRESHOLD)
        & (sel == "over")
        & (mt.isin(_COMBO_MARKETS))
    ).fillna(False)


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build_edge_containment_review_flags(
    full_market_df: pd.DataFrame,
    prediction_date: str,
) -> dict[str, Any]:
    """Apply review-only flag to full_market board rows matching the policy.

    Returns a payload dict including the annotated DataFrame copy and summary
    counts.  The original full_market_df is NEVER modified.

    Args:
        full_market_df: Current full-market board (read-only).
        prediction_date: Slate date string (YYYY-MM-DD).

    Returns:
        dict with keys: flags_df, total_rows_checked, review_required_count,
        high_edge_combo_over_review_count, elite_review_required_count,
        full_market_review_required_count, review_note, policy_name,
        edge_threshold, flagged_players, flagged_markets, flagged_games.
    """
    if full_market_df.empty:
        return _empty_payload(prediction_date)

    # Work on a copy — original is never mutated
    df = full_market_df.copy()

    mask = _build_review_mask(df)

    # Add metadata columns to the copy
    for col, default in _NOT_REQUIRED_META.items():
        df[col] = default

    # Overwrite flagged rows with review metadata
    for col, val in _REVIEW_REQUIRED_META.items():
        df.loc[mask, col] = val

    # Ensure prediction_date column is present
    if "prediction_date" not in df.columns:
        df.insert(0, "prediction_date", prediction_date)

    flagged = df[mask]
    n_total    = int(len(df))
    n_flagged  = int(mask.sum())

    # Elite split (uses is_elite column from full_market_board)
    if "is_elite" in flagged.columns and not flagged.empty:
        elite_mask = flagged["is_elite"].map(_is_truthy)
        n_elite_flagged = int(elite_mask.sum())
    else:
        n_elite_flagged = 0

    n_full_market_flagged = n_flagged  # full_market includes everything

    # Build the CSV-ready subset
    out_cols = [c for c in _OUTPUT_COLUMNS if c in df.columns]
    flags_df = df[out_cols].copy()

    # Collect identifiers for the operator
    flagged_players = sorted(
        flagged["player_name"].dropna().astype(str).unique().tolist()
    ) if "player_name" in flagged.columns else []
    flagged_markets = sorted(
        flagged["market_type"].dropna().astype(str).unique().tolist()
    ) if "market_type" in flagged.columns else []
    flagged_games = sorted(
        flagged["game_id"].dropna().astype(str).unique().tolist()
    ) if "game_id" in flagged.columns else []

    return {
        "prediction_date":                prediction_date,
        "policy_name":                    REVIEW_POLICY_NAME,
        "policy_verdict":                 REVIEW_POLICY_VERDICT,
        "edge_threshold":                 EDGE_THRESHOLD,
        "total_rows_checked":             n_total,
        "review_required_count":          n_flagged,
        "high_edge_combo_over_review_count": n_flagged,
        "elite_review_required_count":    n_elite_flagged,
        "full_market_review_required_count": n_full_market_flagged,
        "flagged_players":                flagged_players,
        "flagged_markets":                flagged_markets,
        "flagged_games":                  flagged_games,
        "flags_df":                       flags_df,
        "review_note": (
            "REVIEW_ONLY: Flagged rows are NOT suppressed. "
            "Operator should review before placing bet. "
            "No live logic changed."
        ),
    }


def _empty_payload(prediction_date: str) -> dict[str, Any]:
    return {
        "prediction_date":                    prediction_date,
        "policy_name":                        REVIEW_POLICY_NAME,
        "policy_verdict":                     REVIEW_POLICY_VERDICT,
        "edge_threshold":                     EDGE_THRESHOLD,
        "total_rows_checked":                 0,
        "review_required_count":              0,
        "high_edge_combo_over_review_count":  0,
        "elite_review_required_count":        0,
        "full_market_review_required_count":  0,
        "flagged_players":                    [],
        "flagged_markets":                    [],
        "flagged_games":                      [],
        "flags_df":                           pd.DataFrame(),
        "review_note":                        "board_not_available",
    }


# ---------------------------------------------------------------------------
# Path helper
# ---------------------------------------------------------------------------

def review_flags_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return (
        Path(runtime_root) / "operator" / f"edge_containment_review_flags_{date}.csv"
    )


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def write_edge_containment_review_flags(
    full_market_df: pd.DataFrame,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, dict[str, Any]]:
    """Write review flags CSV artifact and return (csv_path, payload).

    The full_market_df is NEVER modified. The CSV is a separate new artifact.
    """
    payload = build_edge_containment_review_flags(full_market_df, prediction_date)
    csv_path = review_flags_path_for_date(prediction_date, runtime_root)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    flags_df: pd.DataFrame = payload["flags_df"]
    if not flags_df.empty:
        flags_df.to_csv(csv_path, index=False)
    else:
        # Write header-only CSV so the artifact always exists
        csv_path.write_text(
            ",".join([
                "prediction_date", "player_name", "market_type", "selection",
                "line", "edge", "confidence", "context_pick_alignment",
                "is_elite", "edge_containment_review_required",
                "edge_containment_policy", "edge_containment_reason",
                "edge_containment_recommended_action",
                "edge_containment_verdict", "edge_containment_note",
            ]) + "\n",
            encoding="utf-8",
        )

    # Remove the DataFrame from the serialisable payload copy
    summary = {k: v for k, v in payload.items() if k != "flags_df"}
    return csv_path, summary


# ---------------------------------------------------------------------------
# Operator text renderer (for quality_summary.txt section)
# ---------------------------------------------------------------------------

def _render_review_section(payload: dict[str, Any]) -> str:
    n    = payload.get("review_required_count", 0)
    tot  = payload.get("total_rows_checked", 0)
    elite = payload.get("elite_review_required_count", 0)
    players = payload.get("flagged_players", [])
    markets = payload.get("flagged_markets", [])
    lines = [
        "Edge Containment Review Overlay (Phase 12E — review only)",
        f"  policy           : {payload.get('policy_name')}",
        f"  verdict          : {payload.get('policy_verdict')}",
        f"  edge threshold   : {EDGE_THRESHOLD}% (combo OVERs only)",
        f"  rows checked     : {tot}",
        f"  review_required  : {n}",
        f"  elite flagged    : {elite}",
    ]
    if players:
        lines.append(f"  flagged players  : {', '.join(players[:6])}" + (" ..." if len(players) > 6 else ""))
    if markets:
        lines.append(f"  flagged markets  : {', '.join(markets)}")
    lines.append(f"  note             : {payload.get('review_note', '')}")
    lines.append("  NO ROWS SUPPRESSED. REVIEW ONLY.")
    return "\n".join(lines)


__all__ = [
    "EDGE_THRESHOLD",
    "REVIEW_POLICY_NAME",
    "REVIEW_POLICY_VERDICT",
    "build_edge_containment_review_flags",
    "review_flags_path_for_date",
    "write_edge_containment_review_flags",
]
