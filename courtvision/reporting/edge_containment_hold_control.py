"""
Phase 12G: Edge Containment HOLD Risk Control.

Promotes the Phase 12E/12F combo-OVER warning into an active bankroll-protection
HOLD. Flagged rows receive HOLD_FOR_REVIEW metadata; the Kelly runner reads this
to force stake_amount = 0.

Phase 12F forward-tracking evidence that triggered this promotion
(evidence_snapshot is hardcoded per spec — reflects graded data as of 2026-05-10):
  graded_flags=13, hit_rate=0.0769, avg_roi=-0.8579, avg_projection_error=+10.8002,
  n_hit=1, n_miss=12, n_push=0

This module DOES NOT:
  - remove rows from any board CSV
  - change projections, edge, confidence, or Kelly math for normal rows
  - modify grading, history, or model formulas
  - silently suppress rows without diagnostics
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

HOLD_POLICY_NAME:   str   = "suppress_high_edge_combo_over"
HOLD_VERDICT:       str   = "HOLD_FOR_REVIEW"
HOLD_POLICY_MODE:   str   = "HOLD_FOR_REVIEW"
HOLD_POLICY_REASON: str   = "high_edge_combo_over_forward_tracking_failure"
EDGE_THRESHOLD:     float = 4.0   # absolute edge units (model_projection - line)

_COMBO_MARKETS: frozenset[str] = frozenset({
    "player_points_assists",
    "player_points_rebounds",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
})

# Evidence snapshot from Phase 12F (hardcoded per spec — reflects 2026-05-10 analysis)
EVIDENCE_SNAPSHOT: dict[str, Any] = {
    "graded_flags":          13,
    "hit_rate":              0.0769,
    "avg_roi":               -0.8579,
    "avg_projection_error":  10.8002,
    "n_hit":                 1,
    "n_miss":                12,
    "n_push":                0,
}

# ---------------------------------------------------------------------------
# Metadata dicts
# ---------------------------------------------------------------------------

_HOLD_META: dict[str, Any] = {
    "edge_containment_review_required":    True,
    "edge_containment_policy":             HOLD_POLICY_NAME,
    "edge_containment_reason":             HOLD_POLICY_REASON,
    "edge_containment_verdict":            HOLD_VERDICT,
    "edge_containment_recommended_action": "DO_NOT_BET_UNTIL_REVIEWED",
    "edge_containment_stake_policy":       "HOLD_FOR_REVIEW",
    "edge_containment_note":               "risk_control_no_model_change",
    "risk_control_active":                 True,
    "would_block_kelly_stake":             True,
}

_NOT_HELD_META: dict[str, Any] = {
    "edge_containment_review_required":    False,
    "edge_containment_policy":             "",
    "edge_containment_reason":             "",
    "edge_containment_verdict":            "",
    "edge_containment_recommended_action": "OK_TO_CONSIDER",
    "edge_containment_stake_policy":       "NORMAL",
    "edge_containment_note":               "",
    "risk_control_active":                 False,
    "would_block_kelly_stake":             False,
}

# Columns written to the review-flags CSV artifact
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
    "edge_containment_stake_policy",
    "edge_containment_note",
    "risk_control_active",
    "would_block_kelly_stake",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _build_hold_mask(df: pd.DataFrame) -> pd.Series:
    """Boolean Series: True for rows that match the HOLD policy."""
    idx = df.index
    edge_n = (
        pd.to_numeric(df["edge"], errors="coerce")
        if "edge" in df.columns
        else pd.Series(float("nan"), index=idx)
    )
    sel = (
        df["selection"].astype(str).str.strip().str.lower()
        if "selection" in df.columns
        else pd.Series("", index=idx)
    )
    mt = (
        df["market_type"].astype(str).str.strip().str.lower()
        if "market_type" in df.columns
        else pd.Series("", index=idx)
    )
    return (
        (edge_n >= EDGE_THRESHOLD)
        & (sel == "over")
        & (mt.isin(_COMBO_MARKETS))
    ).fillna(False)


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build_hold_control_flags(
    full_market_df: pd.DataFrame,
    prediction_date: str,
    kelly_hold_count: int = 0,
    kelly_stake_blocked_count: int = 0,
) -> dict[str, Any]:
    """Apply HOLD_FOR_REVIEW metadata to board rows matching the policy.

    The original full_market_df is NEVER modified. Returns a payload dict
    containing the annotated DataFrame copy and all diagnostic counts.

    Args:
        full_market_df: Current full-market board (read-only).
        prediction_date: Slate date string (YYYY-MM-DD).
        kelly_hold_count: Number of Kelly rows held (supplied by caller).
        kelly_stake_blocked_count: Number of Kelly stakes zeroed (supplied by caller).

    Returns:
        Payload dict with keys: flags_df, hold_required_count, elite_hold_count,
        full_market_hold_count, kelly_hold_count, kelly_stake_blocked_count,
        blocked_players, blocked_markets, evidence_snapshot, ...
    """
    if full_market_df.empty:
        return _empty_payload(
            prediction_date, kelly_hold_count, kelly_stake_blocked_count
        )

    df = full_market_df.copy()
    mask = _build_hold_mask(df)

    # Stamp NOT-HELD metadata on all rows first
    for col, default in _NOT_HELD_META.items():
        df[col] = default

    # Overwrite held rows with HOLD metadata
    for col, val in _HOLD_META.items():
        df.loc[mask, col] = val

    # Ensure prediction_date column present
    if "prediction_date" not in df.columns:
        df.insert(0, "prediction_date", prediction_date)

    held = df[mask]
    n_total  = int(len(df))
    n_held   = int(mask.sum())

    # Elite split
    if "is_elite" in held.columns and not held.empty:
        n_elite_held = int(held["is_elite"].map(_is_truthy).sum())
    else:
        n_elite_held = 0

    # Build CSV-ready artifact
    out_cols = [c for c in _OUTPUT_COLUMNS if c in df.columns]
    flags_df = df[out_cols].copy()

    blocked_players = (
        sorted(held["player_name"].dropna().astype(str).unique().tolist())
        if "player_name" in held.columns else []
    )
    blocked_markets = (
        sorted(held["market_type"].dropna().astype(str).unique().tolist())
        if "market_type" in held.columns else []
    )

    return {
        "prediction_date":             prediction_date,
        "policy_name":                 HOLD_POLICY_NAME,
        "policy_mode":                 HOLD_POLICY_MODE,
        "policy_reason":               HOLD_POLICY_REASON,
        "total_rows_checked":          n_total,
        "hold_required_count":         n_held,
        "elite_hold_count":            n_elite_held,
        "full_market_hold_count":      n_held,
        "kelly_hold_count":            kelly_hold_count,
        "kelly_stake_blocked_count":   kelly_stake_blocked_count,
        "blocked_players":             blocked_players,
        "blocked_markets":             blocked_markets,
        "evidence_snapshot":           EVIDENCE_SNAPSHOT,
        "flags_df":                    flags_df,
        "note":                        "risk_control_only_no_model_change",
    }


def _empty_payload(
    prediction_date: str,
    kelly_hold_count: int = 0,
    kelly_stake_blocked_count: int = 0,
) -> dict[str, Any]:
    return {
        "prediction_date":             prediction_date,
        "policy_name":                 HOLD_POLICY_NAME,
        "policy_mode":                 HOLD_POLICY_MODE,
        "policy_reason":               HOLD_POLICY_REASON,
        "total_rows_checked":          0,
        "hold_required_count":         0,
        "elite_hold_count":            0,
        "full_market_hold_count":      0,
        "kelly_hold_count":            kelly_hold_count,
        "kelly_stake_blocked_count":   kelly_stake_blocked_count,
        "blocked_players":             [],
        "blocked_markets":             [],
        "evidence_snapshot":           EVIDENCE_SNAPSHOT,
        "flags_df":                    pd.DataFrame(),
        "note":                        "risk_control_only_no_model_change",
    }


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def hold_control_json_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return (
        Path(runtime_root) / "diagnostics"
        / f"edge_containment_hold_control_{date}.json"
    )


def hold_control_review_flags_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    """Same path as Phase 12E review_flags — Phase 12G overwrites with HOLD metadata."""
    return (
        Path(runtime_root) / "operator"
        / f"edge_containment_review_flags_{date}.csv"
    )


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def write_hold_control_artifacts(
    full_market_df: pd.DataFrame,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    kelly_hold_count: int = 0,
    kelly_stake_blocked_count: int = 0,
) -> tuple[Path, Path, dict[str, Any]]:
    """Write the hold-control review CSV + diagnostics JSON.

    The full_market_df is NEVER modified. Returns (review_csv_path, hold_json_path, payload).

    The review CSV overwrites the Phase 12E output with updated HOLD_FOR_REVIEW
    metadata so downstream operators see the active risk-control verdict.
    """
    runtime_root = Path(runtime_root)
    payload = build_hold_control_flags(
        full_market_df, prediction_date,
        kelly_hold_count=kelly_hold_count,
        kelly_stake_blocked_count=kelly_stake_blocked_count,
    )

    review_csv = hold_control_review_flags_path_for_date(prediction_date, runtime_root)
    hold_json  = hold_control_json_path_for_date(prediction_date, runtime_root)
    review_csv.parent.mkdir(parents=True, exist_ok=True)
    hold_json.parent.mkdir(parents=True, exist_ok=True)

    # Write / overwrite review flags CSV (HOLD metadata supersedes Phase 12E)
    flags_df: pd.DataFrame = payload["flags_df"]
    if not flags_df.empty:
        flags_df.to_csv(review_csv, index=False)
    else:
        review_csv.write_text(
            ",".join(_OUTPUT_COLUMNS) + "\n",
            encoding="utf-8",
        )

    # Write diagnostics JSON (no DataFrame key)
    json_payload = {k: v for k, v in payload.items() if k != "flags_df"}
    hold_json.write_text(json.dumps(json_payload, indent=2), encoding="utf-8")

    return review_csv, hold_json, payload


__all__ = [
    "EDGE_THRESHOLD",
    "EVIDENCE_SNAPSHOT",
    "HOLD_POLICY_MODE",
    "HOLD_POLICY_NAME",
    "HOLD_POLICY_REASON",
    "HOLD_VERDICT",
    "build_hold_control_flags",
    "hold_control_json_path_for_date",
    "hold_control_review_flags_path_for_date",
    "write_hold_control_artifacts",
]
