"""
Phase 12F: Edge Containment Forward Review Outcome Tracker.

Reads all edge_containment_review_flags_*.csv artifacts (Phase 12E outputs)
and joins them against market_shadow_history.csv to measure whether flagged
high-edge combo OVERs predict poor outcomes after grading.

This module is read-only analytics. It DOES NOT:
  - change any projections, edge, confidence, or Kelly math
  - alter elite gates or selection thresholds
  - suppress, remove, or modify any board row
  - write to any board CSV
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TRACKER_POLICY_NAME: str = "suppress_high_edge_combo_over"
_SHADOW_HISTORY_DEFAULT: str = "data/history/market_shadow_history.csv"

_JOIN_KEYS: list[str] = [
    "prediction_date",
    "player_name",
    "market_type",
    "selection",
    "line",
]

_COMBO_MARKETS: frozenset[str] = frozenset({
    "player_points_assists",
    "player_points_rebounds",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
})

_GRADED_STATUSES: frozenset[str] = frozenset({"hit", "miss", "push"})
_HIT_RATE_STATUSES: frozenset[str] = frozenset({"hit", "miss"})  # push excluded per pipeline convention

# Readiness thresholds
_MIN_FOR_TRACKING:    int = 10
_MIN_FOR_WARNING:     int = 30
_MIN_FOR_HOLD:        int = 50
_MIN_FOR_SUPPRESSION: int = 100

_WARN_HIT_RATE:        float = 0.45
_STRONG_WARN_HIT_RATE: float = 0.35
_HOLD_HIT_RATE:        float = 0.40
_HOLD_ROI:             float = -0.10
_SUPP_HIT_RATE:        float = 0.38
_SUPP_ROI:             float = -0.15


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def forward_tracker_json_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return (
        Path(runtime_root) / "diagnostics"
        / f"edge_containment_forward_tracker_{date}.json"
    )


def forward_tracker_txt_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return (
        Path(runtime_root) / "operator"
        / f"edge_containment_forward_tracker_{date}.txt"
    )


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_all_review_flags(operator_dir: Path) -> pd.DataFrame:
    """Concatenate all edge_containment_review_flags_*.csv files."""
    paths = sorted(operator_dir.glob("edge_containment_review_flags_*.csv"))
    if not paths:
        return pd.DataFrame()
    frames = []
    for p in paths:
        try:
            df = pd.read_csv(p, low_memory=False)
            frames.append(df)
        except Exception:
            pass
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _filter_flagged(all_flags_df: pd.DataFrame) -> pd.DataFrame:
    """Return only rows where edge_containment_review_required is truthy."""
    if all_flags_df.empty:
        return pd.DataFrame()
    if "edge_containment_review_required" not in all_flags_df.columns:
        return pd.DataFrame()
    mask = all_flags_df["edge_containment_review_required"].map(_is_truthy)
    return all_flags_df[mask].copy()


# ---------------------------------------------------------------------------
# Join logic
# ---------------------------------------------------------------------------

def _normalise_join_keys(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with join key columns normalised for reliable matching."""
    df = df.copy()
    for col in ("player_name", "market_type", "selection", "prediction_date"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.lower()
    if "line" in df.columns:
        df["line"] = pd.to_numeric(df["line"], errors="coerce").round(2)
    return df


def _join_flags_to_history(
    flagged_df: pd.DataFrame,
    history_df: pd.DataFrame,
) -> pd.DataFrame:
    """Left-join flagged rows onto shadow history using stable join keys.

    Returns a DataFrame with all flagged rows; history columns populated where
    a match was found, NaN otherwise.
    """
    if flagged_df.empty:
        return pd.DataFrame()

    left = _normalise_join_keys(flagged_df)
    right = _normalise_join_keys(history_df)

    # Avoid duplicate columns from both sides (prefer history for result cols)
    right_cols = _JOIN_KEYS + [
        c for c in right.columns
        if c not in _JOIN_KEYS and c not in left.columns
    ]
    right_slim = right[right_cols].drop_duplicates(subset=_JOIN_KEYS, keep="last")

    merged = left.merge(right_slim, on=_JOIN_KEYS, how="left")
    return merged


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------

def _safe_mean(series: pd.Series) -> float | None:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    return float(vals.mean()) if len(vals) > 0 else None


def _compute_outcome_metrics(df: pd.DataFrame) -> dict[str, Any]:
    """Compute hit_rate, roi, projection_error, avg_edge, avg_confidence for df."""
    n_total = int(len(df))
    if n_total == 0:
        return {
            "n_rows": 0,
            "n_graded": 0,
            "n_hit": 0,
            "n_miss": 0,
            "n_push": 0,
            "hit_rate": None,
            "avg_roi": None,
            "avg_projection_error": None,
            "avg_edge": None,
            "avg_confidence": None,
        }

    rs = df["result_status"].astype(str).str.strip().str.lower() if "result_status" in df.columns else pd.Series("", index=df.index)
    graded_mask   = rs.isin(_GRADED_STATUSES)
    hit_rate_mask = rs.isin(_HIT_RATE_STATUSES)
    hit_mask      = rs == "hit"
    miss_mask     = rs == "miss"
    push_mask     = rs == "push"

    n_graded = int(graded_mask.sum())
    n_hit    = int(hit_mask.sum())
    n_miss   = int(miss_mask.sum())
    n_push   = int(push_mask.sum())
    n_hr_denom = int(hit_rate_mask.sum())

    hit_rate = float(n_hit / n_hr_denom) if n_hr_denom > 0 else None

    graded_df = df[graded_mask]
    avg_roi = _safe_mean(graded_df["shadow_roi"]) if "shadow_roi" in graded_df.columns else None

    # Projection error: |model_projection - actual_value| for graded rows
    if "model_projection" in graded_df.columns and "actual_value" in graded_df.columns:
        proj = pd.to_numeric(graded_df["model_projection"], errors="coerce")
        act  = pd.to_numeric(graded_df["actual_value"],    errors="coerce")
        err  = (proj - act).abs().dropna()
        avg_projection_error = float(err.mean()) if len(err) > 0 else None
    else:
        avg_projection_error = None

    avg_edge       = _safe_mean(df["edge"])       if "edge"       in df.columns else None
    avg_confidence = _safe_mean(df["confidence"]) if "confidence" in df.columns else None

    return {
        "n_rows":                n_total,
        "n_graded":              n_graded,
        "n_hit":                 n_hit,
        "n_miss":                n_miss,
        "n_push":                n_push,
        "hit_rate":              round(hit_rate, 4) if hit_rate is not None else None,
        "avg_roi":               round(avg_roi, 4) if avg_roi is not None else None,
        "avg_projection_error":  round(avg_projection_error, 4) if avg_projection_error is not None else None,
        "avg_edge":              round(avg_edge, 4) if avg_edge is not None else None,
        "avg_confidence":        round(avg_confidence, 4) if avg_confidence is not None else None,
    }


# ---------------------------------------------------------------------------
# Readiness classification
# ---------------------------------------------------------------------------

def _classify_readiness(
    n_graded: int,
    hit_rate: float | None,
    avg_roi: float | None,
    baseline_hit_rate: float | None,
) -> str:
    if n_graded < _MIN_FOR_TRACKING:
        return "INSUFFICIENT_FORWARD_SAMPLE"
    if n_graded < _MIN_FOR_WARNING or hit_rate is None:
        return "TRACKING"

    if (
        n_graded >= _MIN_FOR_SUPPRESSION
        and hit_rate < _SUPP_HIT_RATE
        and avg_roi is not None
        and avg_roi < _SUPP_ROI
    ):
        return "READY_FOR_SUPPRESSION_REVIEW_LATER"

    if (
        n_graded >= _MIN_FOR_HOLD
        and hit_rate < _HOLD_HIT_RATE
        and avg_roi is not None
        and avg_roi < _HOLD_ROI
    ):
        return "READY_FOR_HOLD_REVIEW_CONSIDERATION"

    if hit_rate < _STRONG_WARN_HIT_RATE:
        return "STRONG_WARNING_SIGNAL"

    if hit_rate < _WARN_HIT_RATE:
        return "WARNING_SIGNAL"

    return "TRACKING"


# ---------------------------------------------------------------------------
# Breakdown builders
# ---------------------------------------------------------------------------

def _build_market_type_breakdown(joined_df: pd.DataFrame) -> dict[str, dict]:
    if joined_df.empty or "market_type" not in joined_df.columns:
        return {}
    result = {}
    for mt, group in joined_df.groupby("market_type"):
        result[str(mt)] = _compute_outcome_metrics(group)
    return result


def _build_context_breakdown(joined_df: pd.DataFrame) -> dict[str, dict]:
    if joined_df.empty or "context_pick_alignment" not in joined_df.columns:
        return {}
    result = {}
    for ctx, group in joined_df.groupby("context_pick_alignment"):
        result[str(ctx)] = _compute_outcome_metrics(group)
    return result


def _edge_bucket(edge_val: float) -> str:
    if edge_val < 5.0:
        return "4.0-5.0"
    if edge_val < 7.0:
        return "5.0-7.0"
    return "7.0+"


def _build_edge_bucket_breakdown(joined_df: pd.DataFrame) -> dict[str, dict]:
    if joined_df.empty or "edge" not in joined_df.columns:
        return {}
    df = joined_df.copy()
    df["_edge_bucket"] = pd.to_numeric(df["edge"], errors="coerce").map(
        lambda x: _edge_bucket(x) if pd.notna(x) else "unknown"
    )
    result = {}
    for bucket, group in df.groupby("_edge_bucket"):
        result[str(bucket)] = _compute_outcome_metrics(group)
    return result


def _confidence_bucket(conf_val: float) -> str:
    if conf_val < 0.70:
        return "low(<0.70)"
    if conf_val < 0.80:
        return "medium(0.70-0.79)"
    return "high(>=0.80)"


def _build_confidence_bucket_breakdown(joined_df: pd.DataFrame) -> dict[str, dict]:
    if joined_df.empty or "confidence" not in joined_df.columns:
        return {}
    df = joined_df.copy()
    df["_conf_bucket"] = pd.to_numeric(df["confidence"], errors="coerce").map(
        lambda x: _confidence_bucket(x) if pd.notna(x) else "unknown"
    )
    result = {}
    for bucket, group in df.groupby("_conf_bucket"):
        result[str(bucket)] = _compute_outcome_metrics(group)
    return result


def _build_date_breakdown(joined_df: pd.DataFrame) -> dict[str, dict]:
    if joined_df.empty or "prediction_date" not in joined_df.columns:
        return {}
    result = {}
    for dt, group in joined_df.groupby("prediction_date"):
        result[str(dt)] = _compute_outcome_metrics(group)
    return result


def _build_rolling_breakdown(joined_df: pd.DataFrame) -> dict[str, Any]:
    """Compute rolling 7- and 14-slate cumulative metrics (by slate count)."""
    if joined_df.empty or "prediction_date" not in joined_df.columns:
        return {"last_7_slates": None, "last_14_slates": None}

    dates = sorted(joined_df["prediction_date"].dropna().unique())
    last_7_dates  = dates[-7:]  if len(dates) >= 1 else dates
    last_14_dates = dates[-14:] if len(dates) >= 1 else dates

    sub7  = joined_df[joined_df["prediction_date"].isin(last_7_dates)]
    sub14 = joined_df[joined_df["prediction_date"].isin(last_14_dates)]

    return {
        "last_7_slates":  _compute_outcome_metrics(sub7),
        "last_14_slates": _compute_outcome_metrics(sub14),
    }


# ---------------------------------------------------------------------------
# Comparison group builder
# ---------------------------------------------------------------------------

def _build_comparison_groups(
    history_df: pd.DataFrame,
    flagged_joined_df: pd.DataFrame,
) -> dict[str, Any]:
    """Build comparison metrics: non-flagged combo, all combo, all graded."""
    if history_df.empty:
        return {}

    h = _normalise_join_keys(history_df)

    combo_mask = (
        h["market_type"].isin(_COMBO_MARKETS)
        if "market_type" in h.columns
        else pd.Series(False, index=h.index)
    )
    over_mask = (
        h["selection"] == "over"
        if "selection" in h.columns
        else pd.Series(False, index=h.index)
    )
    edge_n = pd.to_numeric(h["edge"], errors="coerce") if "edge" in h.columns else pd.Series(float("nan"), index=h.index)
    high_edge_mask = edge_n >= 4.0

    # Non-flagged combo OVERs (same market class but edge < 4 OR different selection)
    non_flagged_combo_mask = combo_mask & ~(high_edge_mask & over_mask)
    flagged_combo_mask     = combo_mask & high_edge_mask & over_mask

    groups: dict[str, Any] = {
        "all_graded_market_shadow":       _compute_outcome_metrics(h),
        "all_combo_over_graded":          _compute_outcome_metrics(h[combo_mask & over_mask]),
        "non_flagged_combo_over_graded":  _compute_outcome_metrics(h[non_flagged_combo_mask]),
        "flagged_combo_over_shadow_history": _compute_outcome_metrics(h[flagged_combo_mask]),
    }
    return groups


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build_edge_containment_forward_tracker(
    prediction_date: str,
    operator_dir: str | Path = "outputs/runtime/operator",
    history_csv: str | Path = _SHADOW_HISTORY_DEFAULT,
) -> dict[str, Any]:
    """Join Phase 12E review flags to graded shadow history and compute metrics.

    Args:
        prediction_date: Slate date string (YYYY-MM-DD); used for artifact naming.
        operator_dir: Directory containing edge_containment_review_flags_*.csv files.
        history_csv: Path to market_shadow_history.csv.

    Returns:
        Payload dict with all forward-tracking metrics. Never mutates inputs.
    """
    operator_dir = Path(operator_dir)
    history_csv  = Path(history_csv)

    # --- Load all review flag CSVs ---
    all_flags_df = _load_all_review_flags(operator_dir)
    if all_flags_df.empty:
        return _empty_payload(prediction_date, "no_review_flag_files_found")

    flagged_df = _filter_flagged(all_flags_df)
    total_review_flags = int(len(flagged_df))

    # --- Load shadow history ---
    if not history_csv.exists():
        return _empty_payload(prediction_date, "market_shadow_history_not_found")

    try:
        history_df = pd.read_csv(history_csv, low_memory=False)
    except Exception as exc:
        return _empty_payload(prediction_date, f"history_load_error:{exc}")

    # --- Join ---
    if flagged_df.empty:
        joined_df = pd.DataFrame()
    else:
        joined_df = _join_flags_to_history(flagged_df, history_df)

    # --- Classify match status ---
    if joined_df.empty:
        n_matched   = 0
        n_unmatched = total_review_flags
        graded_df   = pd.DataFrame()
        pending_df  = pd.DataFrame()
        void_df     = pd.DataFrame()
    else:
        rs = (
            joined_df["result_status"].astype(str).str.strip().str.lower()
            if "result_status" in joined_df.columns
            else pd.Series("", index=joined_df.index)
        )
        matched_mask   = rs.notna() & (rs != "nan") & (rs != "")
        graded_mask    = rs.isin(_GRADED_STATUSES)
        pending_mask   = rs == "pending"
        void_mask      = rs == "void"
        unmatched_mask = ~matched_mask | rs.isna()

        n_matched   = int(matched_mask.sum())
        n_unmatched = int(unmatched_mask.sum())

        graded_df  = joined_df[graded_mask].copy()
        pending_df = joined_df[pending_mask].copy()
        void_df    = joined_df[void_mask].copy()

    n_graded  = int(len(graded_df))
    n_pending = int(len(pending_df))
    n_void    = int(len(void_df))

    # --- Flagged metrics ---
    flagged_metrics = _compute_outcome_metrics(joined_df) if not joined_df.empty else _compute_outcome_metrics(pd.DataFrame())

    flagged_hit_rate = flagged_metrics["hit_rate"]
    flagged_avg_roi  = flagged_metrics["avg_roi"]

    # --- Comparison groups ---
    comparison_groups = _build_comparison_groups(history_df, joined_df)
    non_flagged_hr = comparison_groups.get("non_flagged_combo_over_graded", {}).get("hit_rate")

    # --- Readiness ---
    readiness = _classify_readiness(n_graded, flagged_hit_rate, flagged_avg_roi, non_flagged_hr)

    # Recommendation string
    recommendation_map = {
        "INSUFFICIENT_FORWARD_SAMPLE":        "accumulate_more_graded_flags_before_drawing_conclusions",
        "TRACKING":                           "continue_monitoring_no_action_needed",
        "WARNING_SIGNAL":                     "operator_should_note_below_average_performance_on_flagged_combos",
        "STRONG_WARNING_SIGNAL":              "consider_tightening_review_on_flagged_combo_overs",
        "READY_FOR_HOLD_REVIEW_CONSIDERATION":"evaluate_whether_to_withhold_flagged_combo_overs_pending_review",
        "READY_FOR_SUPPRESSION_REVIEW_LATER": "evaluate_promote_suppress_high_edge_combo_over_to_active_policy",
    }
    recommendation = recommendation_map.get(readiness, "continue_monitoring")

    # --- Breakdowns ---
    breakdowns = {
        "by_market_type":         _build_market_type_breakdown(joined_df) if not joined_df.empty else {},
        "by_context_alignment":   _build_context_breakdown(joined_df)     if not joined_df.empty else {},
        "by_edge_bucket":         _build_edge_bucket_breakdown(joined_df) if not joined_df.empty else {},
        "by_confidence_bucket":   _build_confidence_bucket_breakdown(joined_df) if not joined_df.empty else {},
        "by_prediction_date":     _build_date_breakdown(joined_df)        if not joined_df.empty else {},
        "rolling":                _build_rolling_breakdown(joined_df)     if not joined_df.empty else {
            "last_7_slates": None, "last_14_slates": None
        },
    }

    return {
        "prediction_date":             prediction_date,
        "policy_name":                 TRACKER_POLICY_NAME,
        "total_review_flags":          total_review_flags,
        "matched_to_history":          n_matched,
        "unmatched_review_flags":      n_unmatched,
        "graded_review_flags":         n_graded,
        "pending_review_flags":        n_pending,
        "void_review_flags":           n_void,
        "flagged_hit_rate":            flagged_hit_rate,
        "flagged_avg_roi":             flagged_avg_roi,
        "flagged_avg_projection_error": flagged_metrics["avg_projection_error"],
        "flagged_avg_edge":            flagged_metrics["avg_edge"],
        "flagged_avg_confidence":      flagged_metrics["avg_confidence"],
        "n_hit":                       flagged_metrics["n_hit"],
        "n_miss":                      flagged_metrics["n_miss"],
        "n_push":                      flagged_metrics["n_push"],
        "readiness":                   readiness,
        "recommendation":              recommendation,
        "comparison_groups":           comparison_groups,
        "breakdowns":                  breakdowns,
        "note":                        "forward_tracking_only_no_live_logic_changed",
    }


def _empty_payload(prediction_date: str, reason: str) -> dict[str, Any]:
    return {
        "prediction_date":              prediction_date,
        "policy_name":                  TRACKER_POLICY_NAME,
        "total_review_flags":           0,
        "matched_to_history":           0,
        "unmatched_review_flags":       0,
        "graded_review_flags":          0,
        "pending_review_flags":         0,
        "void_review_flags":            0,
        "flagged_hit_rate":             None,
        "flagged_avg_roi":              None,
        "flagged_avg_projection_error": None,
        "flagged_avg_edge":             None,
        "flagged_avg_confidence":       None,
        "n_hit":                        0,
        "n_miss":                       0,
        "n_push":                       0,
        "readiness":                    "INSUFFICIENT_FORWARD_SAMPLE",
        "recommendation":               "accumulate_more_graded_flags_before_drawing_conclusions",
        "comparison_groups":            {},
        "breakdowns":                   {},
        "note":                         f"forward_tracking_only_no_live_logic_changed — {reason}",
    }


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def write_edge_containment_forward_tracker(
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_csv: str | Path = _SHADOW_HISTORY_DEFAULT,
) -> tuple[Path, Path, dict[str, Any]]:
    """Build tracker, write JSON + TXT artifacts, return (json_path, txt_path, payload).

    Never modifies any input CSV, board, or live data.
    """
    runtime_root = Path(runtime_root)
    operator_dir = runtime_root / "operator"

    payload = build_edge_containment_forward_tracker(
        prediction_date,
        operator_dir=operator_dir,
        history_csv=history_csv,
    )

    json_path = forward_tracker_json_path_for_date(prediction_date, runtime_root)
    txt_path  = forward_tracker_txt_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)

    # Strip breakdowns from JSON if very large (keep summary + comparison groups)
    json_payload = {k: v for k, v in payload.items() if k != "breakdowns"}
    json_payload["breakdowns_summary"] = {
        grp: {k: v for k, v in metrics.items() if k in ("n_rows", "n_graded", "hit_rate", "avg_roi")}
        for grp, metrics in payload.get("breakdowns", {}).get("by_market_type", {}).items()
    }
    json_path.write_text(json.dumps(json_payload, indent=2), encoding="utf-8")

    txt_path.write_text(_render_text_section(payload), encoding="utf-8")

    return json_path, txt_path, payload


# ---------------------------------------------------------------------------
# Text renderer
# ---------------------------------------------------------------------------

def _render_text_section(payload: dict[str, Any]) -> str:
    sep = "-" * 78
    lines = [
        sep,
        "EDGE CONTAINMENT FORWARD REVIEW TRACKER (Phase 12F -- READ ONLY)",
        sep,
        f"  policy             : {payload.get('policy_name', TRACKER_POLICY_NAME)}",
        f"  prediction_date    : {payload.get('prediction_date', '')}",
        f"  total_review_flags : {payload.get('total_review_flags', 0)}",
        f"  graded_flags       : {payload.get('graded_review_flags', 0)}",
        f"  pending_flags      : {payload.get('pending_review_flags', 0)}",
        f"  void_flags         : {payload.get('void_review_flags', 0)}",
        f"  unmatched_flags    : {payload.get('unmatched_review_flags', 0)}",
        "",
        "  Outcome Metrics (flagged rows):",
        f"    hit_rate         : {payload.get('flagged_hit_rate')}",
        f"    avg_roi          : {payload.get('flagged_avg_roi')}",
        f"    avg_proj_error   : {payload.get('flagged_avg_projection_error')}",
        f"    avg_edge         : {payload.get('flagged_avg_edge')}",
        f"    avg_confidence   : {payload.get('flagged_avg_confidence')}",
        f"    n_hit/miss/push  : {payload.get('n_hit')}/{payload.get('n_miss')}/{payload.get('n_push')}",
        "",
        f"  readiness          : {payload.get('readiness')}",
        f"  recommendation     : {payload.get('recommendation')}",
    ]

    # Comparison groups
    cg = payload.get("comparison_groups", {})
    if cg:
        lines += ["", "  Comparison Groups:"]
        for grp_name, metrics in cg.items():
            hr  = metrics.get("hit_rate")
            roi = metrics.get("avg_roi")
            n   = metrics.get("n_graded", 0)
            lines.append(
                f"    {grp_name:<42} n_graded={n}, hit_rate={hr}, roi={roi}"
            )

    # Market-type breakdown
    by_mt = payload.get("breakdowns", {}).get("by_market_type", {})
    if by_mt:
        lines += ["", "  By Market Type (flagged):"]
        for mt, metrics in sorted(by_mt.items()):
            lines.append(
                f"    {mt:<44} n={metrics.get('n_rows',0)}, "
                f"graded={metrics.get('n_graded',0)}, "
                f"hit_rate={metrics.get('hit_rate')}"
            )

    # Rolling breakdown
    rolling = payload.get("breakdowns", {}).get("rolling", {})
    if rolling:
        lines += ["", "  Rolling Slate Windows:"]
        for window, metrics in rolling.items():
            if metrics:
                lines.append(
                    f"    {window:<22} n_graded={metrics.get('n_graded',0)}, "
                    f"hit_rate={metrics.get('hit_rate')}, "
                    f"roi={metrics.get('avg_roi')}"
                )

    lines += [
        "",
        f"  note               : {payload.get('note', '')}",
        sep,
    ]
    return "\n".join(lines) + "\n"


__all__ = [
    "TRACKER_POLICY_NAME",
    "build_edge_containment_forward_tracker",
    "forward_tracker_json_path_for_date",
    "forward_tracker_txt_path_for_date",
    "write_edge_containment_forward_tracker",
]
