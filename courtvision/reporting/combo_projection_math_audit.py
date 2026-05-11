"""
Phase 13A: Combo Projection Math Audit.

Analytics-only audit of why high-edge combo OVER projections fail.
Decomposes combo projection errors into component contributions
(points / rebounds / assists) using shadow history records.

Data sources:
  - data/history/market_shadow_history.csv  (combo + component rows)
  - outputs/runtime/operator/edge_containment_review_flags_*.csv  (held count)

This module DOES NOT:
  - modify projections, edge, confidence, or Kelly sizing
  - change elite gates or selection thresholds
  - alter grading logic or model formulas
  - write to any source data file
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Policy constants (mirror Phase 12G — audit reads same lane)
# ---------------------------------------------------------------------------

EDGE_THRESHOLD: float = 4.0   # absolute edge units (same as Phase 12G)

_COMBO_MARKETS: frozenset[str] = frozenset({
    "player_points_assists",
    "player_points_rebounds",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
})

# Components required for each combo market
_COMBO_COMPONENTS: dict[str, list[str]] = {
    "player_points_assists":          ["player_points", "player_assists"],
    "player_points_rebounds":         ["player_points", "player_rebounds"],
    "player_rebounds_assists":        ["player_rebounds", "player_assists"],
    "player_points_rebounds_assists": ["player_points", "player_rebounds", "player_assists"],
}

_COMPONENT_MARKETS: frozenset[str] = frozenset({
    "player_points", "player_rebounds", "player_assists",
})

_GRADED_STATUSES: frozenset[str] = frozenset({"hit", "miss", "push"})

# Root cause labels
RC_POINTS_INFLATION      = "POINTS_INFLATION"
RC_REBOUNDS_INFLATION    = "REBOUNDS_INFLATION"
RC_ASSISTS_INFLATION     = "ASSISTS_INFLATION"
RC_MULTI_INFLATION       = "MULTI_COMPONENT_INFLATION"
RC_LINE_TOO_HIGH         = "LINE_TOO_HIGH_NO_COMPONENT_SPIKE"
RC_INSUFFICIENT          = "INSUFFICIENT_COMPONENT_DATA"

# Threshold: component error fraction of combo error to claim "dominant"
_DOMINANCE_THRESHOLD = 0.60

# Probable DNP threshold: actual <= this value treated as possible non-participation
_PROBABLE_DNP_ACTUAL_THRESHOLD = 1.0

DEFAULT_HISTORY_CSV    = "data/history/market_shadow_history.csv"
DEFAULT_OPERATOR_DIR   = "outputs/runtime/operator"
DEFAULT_RUNTIME_ROOT   = "outputs/runtime"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def combo_audit_json_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"combo_projection_math_audit_{date}.json"


def combo_audit_txt_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "operator" / f"combo_projection_math_audit_{date}.txt"


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _load_shadow_history(history_csv: str | Path | pd.DataFrame) -> pd.DataFrame:
    if isinstance(history_csv, pd.DataFrame):
        return history_csv.copy()
    p = Path(history_csv)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def _load_review_flags(operator_dir: str | Path) -> pd.DataFrame:
    pattern = str(Path(operator_dir) / "edge_containment_review_flags_*.csv")
    import glob
    files = sorted(glob.glob(pattern))
    frames: list[pd.DataFrame] = []
    for f in files:
        try:
            df = pd.read_csv(f)
            if not df.empty:
                frames.append(df)
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ---------------------------------------------------------------------------
# Filtering and normalisation
# ---------------------------------------------------------------------------

def _normalise_player(name: Any) -> str:
    return str(name).lower().strip() if pd.notna(name) else ""


def _filter_high_edge_combo_overs(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    edge_n = pd.to_numeric(df.get("edge", pd.Series(dtype=float)), errors="coerce")
    sel = df.get("selection", pd.Series(dtype=str)).astype(str).str.lower().str.strip()
    mt  = df.get("market_type", pd.Series(dtype=str)).astype(str).str.strip()
    mask = (edge_n >= EDGE_THRESHOLD) & (sel == "over") & (mt.isin(_COMBO_MARKETS))
    return df[mask].copy()


def _filter_graded(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    rs = df.get("result_status", pd.Series(dtype=str)).astype(str).str.lower().str.strip()
    return df[rs.isin(_GRADED_STATUSES)].copy()


def _count_held(review_flags_df: pd.DataFrame) -> int:
    if review_flags_df.empty:
        return 0
    col = "edge_containment_stake_policy"
    if col not in review_flags_df.columns:
        col = "edge_containment_verdict"
    if col not in review_flags_df.columns:
        return 0
    return int(
        review_flags_df[col].astype(str).str.strip()
        .isin({"HOLD_FOR_REVIEW", "HOLD"}).sum()
    )


# ---------------------------------------------------------------------------
# Component lookup
# ---------------------------------------------------------------------------

def _build_component_lookup(
    sh: pd.DataFrame,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """(date, player_name_lower, market_type) -> {projection, actual}."""
    comp = sh[sh.get("market_type", pd.Series(dtype=str)).isin(_COMPONENT_MARKETS)]
    lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for _, row in comp.iterrows():
        key = (
            str(row.get("prediction_date", "")),
            _normalise_player(row.get("player_name")),
            str(row.get("market_type", "")),
        )
        lookup[key] = {
            "projection": row.get("model_projection"),
            "actual":     row.get("actual_value"),
        }
    return lookup


# ---------------------------------------------------------------------------
# Row decomposition
# ---------------------------------------------------------------------------

def _decompose_row(
    row: pd.Series,
    lookup: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    """Return component-level decomposition for a single combo OVER row."""
    date   = str(row.get("prediction_date", ""))
    player = _normalise_player(row.get("player_name"))
    mt     = str(row.get("market_type", ""))

    combo_proj   = _to_float(row.get("model_projection"))
    combo_actual = _to_float(row.get("actual_value"))
    combo_error  = (combo_proj - combo_actual
                    if combo_proj is not None and combo_actual is not None
                    else None)

    components = _COMBO_COMPONENTS.get(mt, [])
    comp_data: dict[str, dict[str, Any] | None] = {}
    for c in components:
        v = lookup.get((date, player, c))
        comp_data[c] = v  # None if not found

    all_proj_found = all(comp_data.get(c) is not None for c in components)
    partial_proj   = (not all_proj_found and
                      any(comp_data.get(c) is not None for c in components))

    # Compute per-component errors where actuals are available
    comp_errors: dict[str, float | None] = {}
    for c in components:
        v = comp_data.get(c)
        if v is None:
            comp_errors[c] = None
            continue
        cp = _to_float(v.get("projection"))
        ca = _to_float(v.get("actual"))
        comp_errors[c] = (cp - ca) if cp is not None and ca is not None else None

    return {
        "date":                date,
        "player_name":         str(row.get("player_name", "")),
        "market_type":         mt,
        "combo_proj":          combo_proj,
        "combo_actual":        combo_actual,
        "combo_error":         combo_error,
        "result_status":       str(row.get("result_status", "")).lower().strip(),
        "edge":                _to_float(row.get("edge")),
        "confidence":          _to_float(row.get("confidence")),
        "components":          components,
        "comp_data":           comp_data,
        "comp_errors":         comp_errors,
        "all_proj_found":      all_proj_found,
        "partial_proj_found":  partial_proj,
    }


# ---------------------------------------------------------------------------
# Root cause classification
# ---------------------------------------------------------------------------

def _classify_root_cause(decomp: dict[str, Any]) -> str:
    """Classify the primary inflation source for a single failed combo OVER."""
    result  = decomp.get("result_status", "")
    actual  = decomp.get("combo_actual")
    error   = decomp.get("combo_error")
    comp_errors = decomp.get("comp_errors", {})

    # Hits are not "failures" — don't classify
    if result == "hit":
        return "HIT_NOT_FAILURE"

    # Probable DNP: actual at or near zero
    if actual is not None and actual <= _PROBABLE_DNP_ACTUAL_THRESHOLD:
        return RC_INSUFFICIENT  # actual=0 likely DNP; can't decompose model error

    if error is None:
        return RC_INSUFFICIENT

    # Compute known component fractions
    named_errors: dict[str, float] = {
        c: v for c, v in comp_errors.items() if v is not None
    }

    if not named_errors:
        return RC_INSUFFICIENT

    total_named_error = sum(named_errors.values())
    pts_err = named_errors.get("player_points")
    reb_err = named_errors.get("player_rebounds")
    ast_err = named_errors.get("player_assists")

    # Primary classification: which component accounts for >= dominance_threshold of combo error
    if error != 0:
        pts_frac = (pts_err / error) if pts_err is not None else None
        reb_frac = (reb_err / error) if reb_err is not None else None
        ast_frac = (ast_err / error) if ast_err is not None else None

        if pts_frac is not None and pts_frac >= _DOMINANCE_THRESHOLD:
            return RC_POINTS_INFLATION
        if reb_frac is not None and reb_frac >= _DOMINANCE_THRESHOLD:
            return RC_REBOUNDS_INFLATION
        if ast_frac is not None and ast_frac >= _DOMINANCE_THRESHOLD:
            return RC_ASSISTS_INFLATION

    # Multiple components each contributed
    non_null = [v for v in [pts_err, reb_err, ast_err] if v is not None]
    if len(non_null) >= 2 and all(v > 0 for v in non_null):
        return RC_MULTI_INFLATION

    # Single component error but below dominance threshold — may be line noise
    if len(non_null) == 1:
        if non_null[0] > 0 and error is not None and error > 0 and (non_null[0] / error) < _DOMINANCE_THRESHOLD:
            return RC_LINE_TOO_HIGH
        return RC_POINTS_INFLATION if pts_err is not None else RC_INSUFFICIENT

    return RC_INSUFFICIENT


# ---------------------------------------------------------------------------
# Component projection / actual status flags
# ---------------------------------------------------------------------------

def _compute_component_status(
    decomp_list: list[dict[str, Any]],
) -> tuple[str, str]:
    """Return (component_projection_status, component_actual_status)."""
    if not decomp_list:
        return "no_graded_rows", "no_graded_rows"

    n = len(decomp_list)
    all_proj = sum(1 for d in decomp_list if d["all_proj_found"])
    any_proj = sum(1 for d in decomp_list if d["all_proj_found"] or d["partial_proj_found"])

    if all_proj == n:
        proj_status = "all_component_projection_fields_available"
    elif any_proj >= n * 0.5:
        proj_status = "partial_component_projection_fields"
    else:
        proj_status = "insufficient_component_projection_fields"

    # Actual status: check rebounds and assists (points often available)
    has_pts_actual  = sum(1 for d in decomp_list
                         if d["comp_errors"].get("player_points") is not None)
    has_reb_actual  = sum(1 for d in decomp_list
                         if d["comp_errors"].get("player_rebounds") is not None)
    has_ast_actual  = sum(1 for d in decomp_list
                         if d["comp_errors"].get("player_assists") is not None)

    if has_pts_actual >= n * 0.5:
        act_status = "partial_points_only_insufficient_rebounds_assists"
    elif has_reb_actual == 0 and has_ast_actual == 0:
        act_status = "insufficient_component_actual_fields"
    else:
        act_status = "insufficient_component_actual_fields"

    return proj_status, act_status


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------

def _to_float(v: Any) -> float | None:
    try:
        f = float(v)
        import math
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _safe_mean(vals: list[float]) -> float | None:
    clean = [v for v in vals if v is not None]
    return round(sum(clean) / len(clean), 4) if clean else None


def _safe_median(vals: list[float]) -> float | None:
    clean = sorted(v for v in vals if v is not None)
    return round(statistics.median(clean), 4) if clean else None


def _over_projection_rate(errors: list[float | None]) -> float | None:
    clean = [e for e in errors if e is not None]
    if not clean:
        return None
    return round(sum(1 for e in clean if e > 0) / len(clean), 4)


def _compute_hit_rate(decomps: list[dict[str, Any]]) -> float | None:
    graded = [d for d in decomps if d["result_status"] in ("hit", "miss", "push")]
    hm = [d for d in graded if d["result_status"] in ("hit", "miss")]
    if not hm:
        return None
    return round(sum(1 for d in hm if d["result_status"] == "hit") / len(hm), 4)


def _compute_roi(decomps: list[dict[str, Any]], sh: pd.DataFrame) -> float | None:
    """Compute avg shadow_roi for graded combo OVERs from shadow_history."""
    if sh.empty or "shadow_roi" not in sh.columns:
        return None
    # Use the shadow_history rows directly
    combo_mask = sh["market_type"].isin(_COMBO_MARKETS) & (
        sh["selection"].astype(str).str.lower().str.strip() == "over"
    )
    he_mask = combo_mask & (pd.to_numeric(sh.get("edge", pd.Series(dtype=float)),
                                          errors="coerce") >= EDGE_THRESHOLD)
    graded_mask = he_mask & sh["result_status"].isin(_GRADED_STATUSES)
    sub = sh[graded_mask]
    if sub.empty:
        return None
    rois = pd.to_numeric(sub["shadow_roi"], errors="coerce").dropna()
    return round(float(rois.mean()), 4) if len(rois) else None


# ---------------------------------------------------------------------------
# Breakdown builders
# ---------------------------------------------------------------------------

def _breakdown_by_market_type(decomps: list[dict[str, Any]]) -> dict[str, Any]:
    from collections import defaultdict
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in decomps:
        buckets[d["market_type"]].append(d)

    result = {}
    for mt, rows in sorted(buckets.items()):
        graded = [r for r in rows if r["result_status"] in ("hit", "miss", "push")]
        hm     = [r for r in graded if r["result_status"] in ("hit", "miss")]
        hr     = round(sum(1 for r in hm if r["result_status"] == "hit") / len(hm), 4) if hm else None
        errors = [r["combo_error"] for r in graded]

        pts_errs = [r["comp_errors"].get("player_points") for r in graded]
        reb_errs = [r["comp_errors"].get("player_rebounds") for r in graded]
        ast_errs = [r["comp_errors"].get("player_assists") for r in graded]

        named = {
            "player_points":   _safe_mean([e for e in pts_errs if e is not None]),
            "player_rebounds": _safe_mean([e for e in reb_errs if e is not None]),
            "player_assists":  _safe_mean([e for e in ast_errs if e is not None]),
        }
        dominant = max(
            {k: v for k, v in named.items() if v is not None},
            key=lambda k: named[k],
            default=None,
        )

        result[mt] = {
            "sample_size":                 len(rows),
            "graded_count":                len(graded),
            "hit_rate":                    hr,
            "avg_combo_projection_error":  _safe_mean([e for e in errors if e is not None]),
            "avg_points_error":            named["player_points"],
            "avg_rebounds_error":          named["player_rebounds"],
            "avg_assists_error":           named["player_assists"],
            "dominant_error_component":    dominant,
        }
    return result


def _breakdown_by_player(decomps: list[dict[str, Any]]) -> dict[str, Any]:
    from collections import defaultdict, Counter
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in decomps:
        buckets[d["player_name"]].append(d)

    result = {}
    for player, rows in sorted(buckets.items()):
        graded = [r for r in rows if r["result_status"] in ("hit", "miss", "push")]
        errors = [r["combo_error"] for r in graded if r["combo_error"] is not None]

        comp_errors_map: dict[str, list[float]] = {
            "player_points": [], "player_rebounds": [], "player_assists": [],
        }
        for r in graded:
            for c in comp_errors_map:
                v = r["comp_errors"].get(c)
                if v is not None:
                    comp_errors_map[c].append(v)

        named = {k: _safe_mean(v) for k, v in comp_errors_map.items() if v}
        most_inflated = max(named, key=lambda k: named[k], default=None) if named else None

        result[player] = {
            "sample_size":                 len(rows),
            "graded_count":                len(graded),
            "repeat_failure_count":        sum(1 for r in graded if r["result_status"] == "miss"),
            "avg_combo_projection_error":  _safe_mean(errors),
            "most_inflated_component":     most_inflated,
        }
    return result


def _edge_bucket(edge: float | None) -> str:
    if edge is None:
        return "unknown"
    if edge < 5.0:
        return "4_to_5pct"
    if edge < 7.0:
        return "5_to_7pct"
    return "7pct_plus"


def _confidence_bucket(conf: float | None) -> str:
    if conf is None:
        return "unknown"
    if conf < 0.70:
        return "below_0.70"
    if conf < 0.80:
        return "0.70_to_0.80"
    return "0.80_plus"


def _breakdown_by_bucket(
    decomps: list[dict[str, Any]],
    bucket_fn: Any,
    bucket_key: str,
) -> dict[str, Any]:
    from collections import defaultdict
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in decomps:
        buckets[bucket_fn(d.get(bucket_key))].append(d)

    result = {}
    for bkt, rows in sorted(buckets.items()):
        graded = [r for r in rows if r["result_status"] in ("hit", "miss", "push")]
        hm     = [r for r in graded if r["result_status"] in ("hit", "miss")]
        hr     = round(sum(1 for r in hm if r["result_status"] == "hit") / len(hm), 4) if hm else None
        errors = [r["combo_error"] for r in graded if r["combo_error"] is not None]
        result[bkt] = {
            "sample_size":                len(rows),
            "graded_count":               len(graded),
            "hit_rate":                   hr,
            "avg_combo_projection_error": _safe_mean(errors),
        }
    return result


# ---------------------------------------------------------------------------
# Root cause summary
# ---------------------------------------------------------------------------

def _summarize_root_causes(
    decomps: list[dict[str, Any]],
) -> dict[str, Any]:
    from collections import Counter
    failed = [d for d in decomps
              if d["result_status"] == "miss" and d.get("combo_actual", 1) is not None]
    if not failed:
        return {
            "total_failures_analyzed": 0,
            "root_cause_counts":       {},
            "pct_points_inflation":    None,
            "pct_rebounds_inflation":  None,
            "pct_assists_inflation":   None,
            "pct_multi_inflation":     None,
            "pct_insufficient":        None,
        }

    causes = [_classify_root_cause(d) for d in failed]
    counts = dict(Counter(causes))
    n = len(causes)

    def _pct(label: str) -> float | None:
        return round(counts.get(label, 0) / n, 4) if n else None

    return {
        "total_failures_analyzed": n,
        "root_cause_counts":       counts,
        "pct_points_inflation":    _pct(RC_POINTS_INFLATION),
        "pct_rebounds_inflation":  _pct(RC_REBOUNDS_INFLATION),
        "pct_assists_inflation":   _pct(RC_ASSISTS_INFLATION),
        "pct_multi_inflation":     _pct(RC_MULTI_INFLATION),
        "pct_line_too_high":       _pct(RC_LINE_TOO_HIGH),
        "pct_insufficient":        _pct(RC_INSUFFICIENT),
    }


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_combo_projection_math_audit(
    prediction_date: str,
    operator_dir:   str | Path = DEFAULT_OPERATOR_DIR,
    history_csv:    str | Path | pd.DataFrame = DEFAULT_HISTORY_CSV,
) -> dict[str, Any]:
    """Run the full combo projection math audit.

    Args:
        prediction_date: Slate date string (YYYY-MM-DD).
        operator_dir:    Directory containing edge_containment_review_flags_*.csv files.
        history_csv:     Path to market_shadow_history.csv.

    Returns:
        Payload dict with all audit metrics and status fields.
    """
    sh = _load_shadow_history(history_csv)

    # Column presence audit (printed for operator visibility, stored in payload)
    col_audit = _audit_column_presence(sh)

    if sh.empty:
        return _empty_payload(prediction_date, col_audit)

    # --- Combo OVERs ---
    combo_mask = sh["market_type"].isin(_COMBO_MARKETS) & (
        sh["selection"].astype(str).str.lower().str.strip() == "over"
    )
    all_combo_overs = sh[combo_mask].copy()
    high_edge_overs = _filter_high_edge_combo_overs(sh)
    graded_high     = _filter_graded(high_edge_overs)

    # Held count from review_flags
    rf_df = _load_review_flags(operator_dir)
    held_count = _count_held(rf_df)

    # Build component lookup from shadow_history
    comp_lookup = _build_component_lookup(sh)

    # Decompose every graded high-edge combo OVER
    decomp_list = [_decompose_row(row, comp_lookup) for _, row in graded_high.iterrows()]

    # Overall combo error metrics
    all_errors = [d["combo_error"] for d in decomp_list if d["combo_error"] is not None]
    all_proj   = [d["combo_proj"]  for d in decomp_list if d["combo_proj"]  is not None]
    all_actual = [d["combo_actual"] for d in decomp_list if d["combo_actual"] is not None]

    # Matched / unmatched component projection counts
    matched_comp   = sum(1 for d in decomp_list if d["all_proj_found"] or d["partial_proj_found"])
    unmatched_comp = sum(1 for d in decomp_list if not d["all_proj_found"] and not d["partial_proj_found"])

    proj_status, act_status = _compute_component_status(decomp_list)
    root_cause_summary = _summarize_root_causes(decomp_list)

    by_market   = _breakdown_by_market_type(decomp_list)
    by_player   = _breakdown_by_player(decomp_list)
    by_edge_bkt = _breakdown_by_bucket(decomp_list, _edge_bucket, "edge")
    by_conf_bkt = _breakdown_by_bucket(decomp_list, _confidence_bucket, "confidence")

    # Dominant error component across all graded rows
    pts_errs = [d["comp_errors"].get("player_points") for d in decomp_list
                if d["comp_errors"].get("player_points") is not None]
    reb_errs = [d["comp_errors"].get("player_rebounds") for d in decomp_list
                if d["comp_errors"].get("player_rebounds") is not None]
    ast_errs = [d["comp_errors"].get("player_assists") for d in decomp_list
                if d["comp_errors"].get("player_assists") is not None]

    comp_means = {
        "player_points":   _safe_mean(pts_errs),
        "player_rebounds": _safe_mean(reb_errs),
        "player_assists":  _safe_mean(ast_errs),
    }
    dominant_error = max(
        {k: v for k, v in comp_means.items() if v is not None},
        key=lambda k: comp_means[k],
        default="unknown",
    )

    hit_rate = _compute_hit_rate(decomp_list)
    roi      = _compute_roi(decomp_list, sh)

    return {
        "prediction_date":                prediction_date,
        "note":                           "audit_only_no_model_change",
        # Counts
        "total_combo_over_rows":          int(len(all_combo_overs)),
        "high_edge_combo_over_rows":      int(len(high_edge_overs)),
        "graded_combo_over_rows":         int(len(graded_high)),
        "held_combo_over_rows":           int(held_count),
        "matched_component_rows":         int(matched_comp),
        "unmatched_component_rows":       int(unmatched_comp),
        # Overall metrics
        "avg_combo_projection_error":     _safe_mean(all_errors),
        "median_combo_projection_error":  _safe_median(all_errors),
        "over_projection_rate":           _over_projection_rate(all_errors),
        "hit_rate":                       hit_rate,
        "roi":                            roi,
        # Component analysis
        "component_projection_status":    proj_status,
        "component_actual_status":        act_status,
        "avg_points_error":               _safe_mean(pts_errs),
        "avg_rebounds_error":             _safe_mean(reb_errs),
        "avg_assists_error":              _safe_mean(ast_errs),
        "dominant_error_component":       dominant_error,
        # Root causes
        "root_cause_summary":             root_cause_summary,
        # Breakdowns
        "by_market_type":                 by_market,
        "by_player":                      by_player,
        "by_edge_bucket":                 by_edge_bkt,
        "by_confidence_bucket":           by_conf_bkt,
        # Column audit
        "column_audit":                   col_audit,
    }


def _audit_column_presence(sh: pd.DataFrame) -> dict[str, Any]:
    """Report which data fields are available for the audit."""
    if sh.empty:
        return {
            "combo_projected_totals": "not_available",
            "component_projections":  "not_available",
            "actual_points":          "not_available",
            "actual_rebounds":        "not_available",
            "actual_assists":         "not_available",
        }

    combo = sh[sh["market_type"].isin(_COMBO_MARKETS)]
    comp  = sh[sh["market_type"].isin(_COMPONENT_MARKETS)]

    def _pres(df: pd.DataFrame, col: str) -> str:
        if col not in df.columns:
            return "column_absent"
        has = df[col].notna().sum()
        return f"present_{has}_non_null" if has else "column_all_null"

    pts_rows = sh[sh["market_type"] == "player_points"]
    reb_rows = sh[sh["market_type"] == "player_rebounds"]
    ast_rows = sh[sh["market_type"] == "player_assists"]

    def _actual_status(sub: pd.DataFrame) -> str:
        if sub.empty:
            return "no_rows"
        has_actual = int(sub["actual_value"].notna().sum()) if "actual_value" in sub.columns else 0
        total = len(sub)
        if has_actual == 0:
            return "no_actual_values"
        return f"available_{has_actual}_of_{total}_rows"

    return {
        "combo_projected_totals":   _pres(combo, "model_projection"),
        "combo_actual_values":      _pres(combo, "actual_value"),
        "component_projections":    _pres(comp, "model_projection"),
        "actual_points":            _actual_status(pts_rows),
        "actual_rebounds":          _actual_status(reb_rows),
        "actual_assists":           _actual_status(ast_rows),
        "files_with_component_data": ["data/history/market_shadow_history.csv"],
        "component_projection_source": "shadow_history_component_market_rows",
        "component_actual_source":     "shadow_history_component_market_rows",
    }


def _empty_payload(
    prediction_date: str,
    col_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "prediction_date":               prediction_date,
        "note":                          "audit_only_no_model_change",
        "total_combo_over_rows":         0,
        "high_edge_combo_over_rows":     0,
        "graded_combo_over_rows":        0,
        "held_combo_over_rows":          0,
        "matched_component_rows":        0,
        "unmatched_component_rows":      0,
        "avg_combo_projection_error":    None,
        "median_combo_projection_error": None,
        "over_projection_rate":          None,
        "hit_rate":                      None,
        "roi":                           None,
        "component_projection_status":   "insufficient_component_projection_fields",
        "component_actual_status":       "insufficient_component_actual_fields",
        "avg_points_error":              None,
        "avg_rebounds_error":            None,
        "avg_assists_error":             None,
        "dominant_error_component":      "unknown",
        "root_cause_summary":            {},
        "by_market_type":                {},
        "by_player":                     {},
        "by_edge_bucket":                {},
        "by_confidence_bucket":          {},
        "column_audit":                  col_audit or {},
    }


# ---------------------------------------------------------------------------
# Text formatter
# ---------------------------------------------------------------------------

def _format_txt(payload: dict[str, Any], date: str) -> str:
    sep  = "=" * 78
    sep2 = "-" * 78
    lines: list[str] = [
        f"{sep}\n",
        f"COMBO PROJECTION MATH AUDIT  (Phase 13A -- AUDIT ONLY)\n",
        f"date: {date}    note: {payload.get('note', '')}\n",
        f"{sep}\n",
        "\n",
        "OVERVIEW\n",
        f"{sep2}\n",
        f"  total_combo_over_rows       : {payload.get('total_combo_over_rows', 0)}\n",
        f"  high_edge_combo_over_rows   : {payload.get('high_edge_combo_over_rows', 0)}\n",
        f"  graded_combo_over_rows      : {payload.get('graded_combo_over_rows', 0)}\n",
        f"  held_combo_over_rows        : {payload.get('held_combo_over_rows', 0)}\n",
        f"  matched_component_rows      : {payload.get('matched_component_rows', 0)}\n",
        f"  unmatched_component_rows    : {payload.get('unmatched_component_rows', 0)}\n",
        f"\n",
        "OVERALL METRICS (graded high-edge combo OVERs)\n",
        f"{sep2}\n",
        f"  hit_rate                    : {payload.get('hit_rate')}\n",
        f"  roi                         : {payload.get('roi')}\n",
        f"  avg_combo_projection_error  : {payload.get('avg_combo_projection_error')}\n",
        f"  median_combo_projection_error: {payload.get('median_combo_projection_error')}\n",
        f"  over_projection_rate        : {payload.get('over_projection_rate')}\n",
        f"\n",
        "COMPONENT ERROR ANALYSIS\n",
        f"{sep2}\n",
        f"  component_projection_status : {payload.get('component_projection_status')}\n",
        f"  component_actual_status     : {payload.get('component_actual_status')}\n",
        f"  avg_points_error            : {payload.get('avg_points_error')}\n",
        f"  avg_rebounds_error          : {payload.get('avg_rebounds_error')}\n",
        f"  avg_assists_error           : {payload.get('avg_assists_error')}\n",
        f"  dominant_error_component    : {payload.get('dominant_error_component')}\n",
        f"\n",
    ]

    # Root cause summary
    rc = payload.get("root_cause_summary", {})
    if rc:
        lines += [
            "ROOT CAUSE CLASSIFICATION\n",
            f"{sep2}\n",
            f"  total_failures_analyzed : {rc.get('total_failures_analyzed', 0)}\n",
        ]
        for label, count in sorted((rc.get("root_cause_counts") or {}).items()):
            lines.append(f"  {label:<42}: {count}\n")
        lines += [
            f"  pct_points_inflation    : {rc.get('pct_points_inflation')}\n",
            f"  pct_rebounds_inflation  : {rc.get('pct_rebounds_inflation')}\n",
            f"  pct_assists_inflation   : {rc.get('pct_assists_inflation')}\n",
            f"  pct_multi_inflation     : {rc.get('pct_multi_inflation')}\n",
            f"  pct_insufficient        : {rc.get('pct_insufficient')}\n",
            "\n",
        ]

    # By market type
    by_mt = payload.get("by_market_type", {})
    if by_mt:
        lines += ["BY MARKET TYPE\n", f"{sep2}\n"]
        for mt, row in sorted(by_mt.items()):
            lines.append(f"  {mt}\n")
            lines.append(f"    sample={row.get('sample_size')} graded={row.get('graded_count')} "
                         f"hit_rate={row.get('hit_rate')} "
                         f"avg_error={row.get('avg_combo_projection_error')}\n")
            lines.append(f"    pts_err={row.get('avg_points_error')} "
                         f"reb_err={row.get('avg_rebounds_error')} "
                         f"ast_err={row.get('avg_assists_error')} "
                         f"dominant={row.get('dominant_error_component')}\n")
        lines.append("\n")

    # By player
    by_pl = payload.get("by_player", {})
    if by_pl:
        lines += ["BY PLAYER (top by avg error)\n", f"{sep2}\n"]
        sorted_players = sorted(
            by_pl.items(),
            key=lambda kv: (kv[1].get("avg_combo_projection_error") or 0),
            reverse=True,
        )
        for player, row in sorted_players[:10]:
            lines.append(
                f"  {player}: sample={row.get('sample_size')} "
                f"failures={row.get('repeat_failure_count')} "
                f"avg_error={row.get('avg_combo_projection_error')} "
                f"dominant={row.get('most_inflated_component')}\n"
            )
        lines.append("\n")

    # By edge bucket
    by_eb = payload.get("by_edge_bucket", {})
    if by_eb:
        lines += ["BY EDGE BUCKET\n", f"{sep2}\n"]
        for bkt, row in sorted(by_eb.items()):
            lines.append(f"  {bkt}: sample={row.get('sample_size')} "
                         f"graded={row.get('graded_count')} "
                         f"hit_rate={row.get('hit_rate')} "
                         f"avg_error={row.get('avg_combo_projection_error')}\n")
        lines.append("\n")

    # By confidence bucket
    by_cb = payload.get("by_confidence_bucket", {})
    if by_cb:
        lines += ["BY CONFIDENCE BUCKET\n", f"{sep2}\n"]
        for bkt, row in sorted(by_cb.items()):
            lines.append(f"  {bkt}: sample={row.get('sample_size')} "
                         f"graded={row.get('graded_count')} "
                         f"hit_rate={row.get('hit_rate')} "
                         f"avg_error={row.get('avg_combo_projection_error')}\n")
        lines.append("\n")

    col = payload.get("column_audit", {})
    if col:
        lines += ["COLUMN / DATA AVAILABILITY AUDIT\n", f"{sep2}\n"]
        for k, v in sorted(col.items()):
            if not isinstance(v, list):
                lines.append(f"  {k:<40}: {v}\n")
        lines.append(f"{sep}\n")

    return "".join(lines)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def write_combo_projection_math_audit(
    prediction_date: str,
    runtime_root:    str | Path = DEFAULT_RUNTIME_ROOT,
    history_csv:     str | Path | pd.DataFrame = DEFAULT_HISTORY_CSV,
    operator_dir:    str | Path | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Write combo projection math audit artifacts.

    The history_csv and all source DataFrames are read-only.
    Returns (json_path, txt_path, payload).
    """
    runtime_root = Path(runtime_root)
    if operator_dir is None:
        operator_dir = runtime_root / "operator"

    payload = build_combo_projection_math_audit(
        prediction_date=prediction_date,
        operator_dir=operator_dir,
        history_csv=history_csv,
    )

    json_path = combo_audit_json_path_for_date(prediction_date, runtime_root)
    txt_path  = combo_audit_txt_path_for_date(prediction_date, runtime_root)

    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)

    # Write JSON (omit non-serialisable objects — there are none here)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Write TXT
    txt_path.write_text(_format_txt(payload, prediction_date), encoding="utf-8")

    return json_path, txt_path, payload


__all__ = [
    "EDGE_THRESHOLD",
    "build_combo_projection_math_audit",
    "combo_audit_json_path_for_date",
    "combo_audit_txt_path_for_date",
    "write_combo_projection_math_audit",
]
