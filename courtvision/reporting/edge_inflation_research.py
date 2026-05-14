"""
Phase 12C: Edge Inflation Containment Research.

Shadow-only investigative analytics. Investigates WHERE synthetic edge inflation
originates and proposes containment policies for future shadow-mode testing.

Phase 12B confirmed (375 graded rows, April-May 2026 playoffs):
  overall mean_projection_error ≈ +2.45
  corr(edge, projection_error)  ≈ +0.43  — the larger the edge, the more inflated
  OVER 6%+ error ≈ +10.17;  ROI ≈ -48.6%
  combo 6%+ error ≈ +26.38; ROI ≈ -100%
  context-conflicted 6%+ error ≈ +17.26

This module DOES NOT modify:
  projections, edge calculations, confidence, Kelly sizing, elite gates,
  selection thresholds, board generation, grading logic, fragility scoring.

Schema: market_shadow_history.csv (375 graded rows confirmed available):
  100%: model_projection, actual_value, edge, confidence, shadow_roi,
        selection, market_type, result_status, prediction_date
   55%: context_pick_alignment, context_caution_level
    5%: final_elite_rejection_reason
    0%: fragility_bucket
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPORT_VERSION = "1.0"
_GRADED = frozenset({"hit", "miss"})
_MIN_SEGMENT = 10

_EDGE_EDGES  = [0.0, 2.0, 4.0, 6.0, float("inf")]
_EDGE_LABELS = ["0-2%", "2-4%", "4-6%", "6%+"]

_CONF_EDGES  = [0.0, 0.55, 0.60, 0.70, 0.80, 1.01]
_CONF_LABELS = ["<0.55", "0.55-0.60", "0.60-0.70", "0.70-0.80", "0.80+"]

_COMBO_MARKETS = frozenset({
    "player_points_assists",
    "player_points_rebounds",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
})

# Edge risk thresholds (documented):
#   CATASTROPHIC  : mean_error > 8.0  OR  (hit_rate < 0.30 AND roi < -0.50)
#   SEVERE        : mean_error > 5.0  OR  (hit_rate < 0.40 AND roi < -0.35)
#   HIGH          : mean_error > 3.0  OR   hit_rate < 0.45  OR  roi < -0.20
#   MODERATE      : mean_error > 1.5  OR   hit_rate < 0.50  OR  roi < -0.10
#   ACCEPTABLE    : otherwise
#   INSUFFICIENT_SAMPLE : n < _MIN_SEGMENT
_RISK_THRESHOLDS = [
    ("CATASTROPHIC", 8.0, 0.30, -0.50),
    ("SEVERE",       5.0, 0.40, -0.35),
    ("HIGH",         3.0, 0.45, -0.20),
    ("MODERATE",     1.5, 0.50, -0.10),
]

# Policy simulation verdict thresholds:
#   READY_FOR_SHADOW_TEST : hit_rate < 0.35 AND roi < -0.35
#   PROMISING             : hit_rate < 0.43 OR  roi < -0.30
#   MONITOR               : hit_rate < 0.50 AND roi < -0.10
#   REJECT_POLICY         : flagged rows perform AT or ABOVE baseline
#   INSUFFICIENT_SAMPLE   : n_flagged < 20


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _safe_mean(s: pd.Series) -> float | None:
    v = pd.to_numeric(s, errors="coerce").dropna()
    return round(float(v.mean()), 4) if not v.empty else None


def _safe_correlation(left: pd.Series, right: pd.Series) -> float | None:
    pairs = pd.concat(
        [
            pd.to_numeric(left, errors="coerce").rename("left"),
            pd.to_numeric(right, errors="coerce").rename("right"),
        ],
        axis=1,
    ).dropna()
    if len(pairs) < 2:
        return None
    finite = pairs.apply(lambda col: col.map(math.isfinite))
    if not bool(finite.all().all()):
        return None
    left_values = pairs["left"]
    right_values = pairs["right"]
    left_std = float(left_values.std(ddof=0))
    right_std = float(right_values.std(ddof=0))
    if not math.isfinite(left_std) or not math.isfinite(right_std):
        return None
    if left_std == 0.0 or right_std == 0.0:
        return None
    corr = left_values.corr(right_values)
    if corr is None:
        return None
    corr_value = float(corr)
    if not math.isfinite(corr_value):
        return None
    return round(corr_value, 4)


def _hit_rate(df: pd.DataFrame) -> float | None:
    if df.empty or "result_status" not in df.columns:
        return None
    s = df["result_status"].astype(str).str.lower()
    denom = int(s.isin(["hit", "miss"]).sum())
    return round(int((s == "hit").sum()) / denom, 4) if denom else None


def _roi(df: pd.DataFrame) -> float | None:
    if "shadow_roi" not in df.columns:
        return None
    v = pd.to_numeric(df["shadow_roi"], errors="coerce").dropna()
    return round(float(v.mean()), 4) if not v.empty else None


def _projection_error_series(df: pd.DataFrame) -> pd.Series:
    proj   = pd.to_numeric(df.get("model_projection", pd.Series(dtype=float)), errors="coerce")
    actual = pd.to_numeric(df.get("actual_value",    pd.Series(dtype=float)), errors="coerce")
    return (proj - actual).rename("proj_error")


def _classify_risk(
    mean_error: float | None,
    hit_rate: float | None,
    roi: float | None,
    n: int,
) -> str:
    if n < _MIN_SEGMENT:
        return "INSUFFICIENT_SAMPLE"
    me = mean_error or 0.0
    hr = hit_rate  or 1.0
    r  = roi       or 0.0
    if me > 8.0 or (hr < 0.30 and r < -0.50):
        return "CATASTROPHIC"
    if me > 5.0 or (hr < 0.40 and r < -0.35):
        return "SEVERE"
    if me > 3.0 or hr < 0.45 or r < -0.20:
        return "HIGH"
    if me > 1.5 or hr < 0.50 or r < -0.10:
        return "MODERATE"
    return "ACCEPTABLE"


# ---------------------------------------------------------------------------
# Core segment stats
# ---------------------------------------------------------------------------

def _seg(df: pd.DataFrame, label: str) -> dict[str, Any]:
    n = int(len(df))
    if n == 0:
        return {"label": label, "sample_size": 0, "risk_flag": "INSUFFICIENT_SAMPLE"}

    errors = _projection_error_series(df).dropna()
    n_err  = int(len(errors))
    me = round(float(errors.mean()),   4) if n_err else None
    md = round(float(errors.median()), 4) if n_err else None
    ov = round(float((errors > 0).sum() / n_err), 4) if n_err else None

    hr       = _hit_rate(df)
    r        = _roi(df)
    avg_conf = _safe_mean(df.get("confidence", pd.Series(dtype=float)))
    avg_edge = _safe_mean(df.get("edge",       pd.Series(dtype=float)))
    gap      = round(hr - avg_conf, 4) if (hr is not None and avg_conf is not None) else None
    risk     = _classify_risk(me, hr, r, n)

    result: dict[str, Any] = {
        "label": label,
        "sample_size": n,
        "hit_rate": hr,
        "roi": r,
        "avg_edge": avg_edge,
        "avg_confidence": avg_conf,
        "avg_projection_error": me,
        "median_projection_error": md,
        "calibration_gap": gap,
        "over_prediction_rate": ov,
        "risk_flag": risk,
    }
    if n < _MIN_SEGMENT:
        result["caution"] = f"sparse_sample (n={n})"
    return result


# ---------------------------------------------------------------------------
# Analysis: edge buckets
# ---------------------------------------------------------------------------

def _edge_bucket_analysis(df: pd.DataFrame) -> list[dict[str, Any]]:
    if "edge" not in df.columns:
        return []
    vals = pd.to_numeric(df["edge"], errors="coerce")
    results = []
    for label, lo, hi in zip(_EDGE_LABELS, _EDGE_EDGES[:-1], _EDGE_EDGES[1:]):
        seg = df[(vals >= lo) & (vals < hi)]
        results.append(_seg(seg, label))
    return results


# ---------------------------------------------------------------------------
# Analysis: edge × direction cross-table
# ---------------------------------------------------------------------------

def _edge_by_direction(df: pd.DataFrame) -> dict[str, Any]:
    if "edge" not in df.columns or "selection" not in df.columns:
        return {"status": "insufficient_data"}
    vals = pd.to_numeric(df["edge"], errors="coerce")
    sel  = df["selection"].astype(str).str.lower()
    result: dict[str, Any] = {}
    for eb_label, lo, hi in zip(_EDGE_LABELS, _EDGE_EDGES[:-1], _EDGE_EDGES[1:]):
        bucket_df = df[(vals >= lo) & (vals < hi)]
        result[eb_label] = {
            "over":  _seg(bucket_df[sel[bucket_df.index] == "over"],  f"{eb_label}_over"),
            "under": _seg(bucket_df[sel[bucket_df.index] == "under"], f"{eb_label}_under"),
        }
    return result


# ---------------------------------------------------------------------------
# Analysis: edge × market type
# ---------------------------------------------------------------------------

def _edge_by_market(df: pd.DataFrame) -> dict[str, Any]:
    if "edge" not in df.columns or "market_type" not in df.columns:
        return {"status": "insufficient_data"}
    vals = pd.to_numeric(df["edge"], errors="coerce")
    mt   = df["market_type"].astype(str).str.lower()
    is_combo = mt.isin({m.lower() for m in _COMBO_MARKETS})
    result: dict[str, Any] = {}
    for eb_label, lo, hi in zip(_EDGE_LABELS, _EDGE_EDGES[:-1], _EDGE_EDGES[1:]):
        bucket_df = df[(vals >= lo) & (vals < hi)]
        bmt       = mt[bucket_df.index]
        bcombo    = is_combo[bucket_df.index]
        result[eb_label] = {
            "player_points":  _seg(bucket_df[bmt == "player_points"],  f"{eb_label}_pts"),
            "player_rebounds": _seg(bucket_df[bmt == "player_rebounds"], f"{eb_label}_reb"),
            "player_assists":  _seg(bucket_df[bmt == "player_assists"],  f"{eb_label}_ast"),
            "combo":           _seg(bucket_df[bcombo],                   f"{eb_label}_combo"),
            "single_stat":     _seg(bucket_df[~bcombo],                  f"{eb_label}_single"),
        }
    return result


# ---------------------------------------------------------------------------
# Analysis: high-edge combo focus
# ---------------------------------------------------------------------------

def _high_edge_combo_analysis(df: pd.DataFrame) -> dict[str, Any]:
    if "edge" not in df.columns or "market_type" not in df.columns:
        return {"status": "insufficient_data"}
    vals     = pd.to_numeric(df["edge"], errors="coerce")
    mt       = df["market_type"].astype(str).str.lower()
    high_edge = (vals >= 4.0)
    is_combo  = mt.isin({m.lower() for m in _COMBO_MARKETS})
    combos = {
        "all_combos_high_edge": _seg(df[high_edge & is_combo], "all_combos_4pct_plus"),
    }
    for combo in sorted(_COMBO_MARKETS):
        seg = df[high_edge & (mt == combo.lower())]
        combos[combo] = _seg(seg, combo)
    combos["single_stat_high_edge"] = _seg(df[high_edge & ~is_combo], "single_stat_4pct_plus")
    return combos


# ---------------------------------------------------------------------------
# Analysis: high-edge × context
# ---------------------------------------------------------------------------

def _high_edge_context_analysis(df: pd.DataFrame) -> dict[str, Any]:
    col = "context_pick_alignment"
    if "edge" not in df.columns or col not in df.columns:
        return {"status": "insufficient_data"}
    vals = pd.to_numeric(df["edge"], errors="coerce")
    ctx  = df[col].astype(str).str.lower()
    coverage = int(df[col].notna().sum())
    cov_pct  = round(coverage / len(df) * 100, 1) if len(df) else 0.0
    result: dict[str, Any] = {"coverage_pct": cov_pct, "coverage_caution": cov_pct < 80}
    for eb_label, lo, hi in zip(_EDGE_LABELS, _EDGE_EDGES[:-1], _EDGE_EDGES[1:]):
        bucket_df = df[(vals >= lo) & (vals < hi)]
        bctx      = ctx[bucket_df.index]
        result[eb_label] = {
            label: _seg(bucket_df[bctx == label], f"{eb_label}_{label}")
            for label in ("aligned", "neutral", "conflicted")
        }
    return result


# ---------------------------------------------------------------------------
# Analysis: high-edge × confidence
# ---------------------------------------------------------------------------

def _high_edge_confidence_analysis(df: pd.DataFrame) -> dict[str, Any]:
    if "edge" not in df.columns or "confidence" not in df.columns:
        return {"status": "insufficient_data"}
    vals = pd.to_numeric(df["edge"], errors="coerce")
    conf = pd.to_numeric(df["confidence"], errors="coerce")
    result: dict[str, Any] = {}
    for eb_label, lo, hi in zip(_EDGE_LABELS, _EDGE_EDGES[:-1], _EDGE_EDGES[1:]):
        bucket_df = df[(vals >= lo) & (vals < hi)]
        bconf     = conf[bucket_df.index]
        result[eb_label] = {
            conf_label: _seg(
                bucket_df[(bconf >= clo) & (bconf < chi)],
                f"{eb_label}_{conf_label}",
            )
            for conf_label, clo, chi in zip(_CONF_LABELS, _CONF_EDGES[:-1], _CONF_EDGES[1:])
        }
    return result


# ---------------------------------------------------------------------------
# Analysis: edge–error correlation
# ---------------------------------------------------------------------------

def _edge_error_correlation(df: pd.DataFrame) -> dict[str, Any]:
    if "edge" not in df.columns:
        return {"status": "insufficient_data"}
    errors = _projection_error_series(df)
    edge_n = pd.to_numeric(df["edge"], errors="coerce")
    valid  = errors.notna() & edge_n.notna()
    n_valid = int(valid.sum())
    if n_valid < 10:
        return {"status": "insufficient_data", "n": n_valid}

    e_vals  = edge_n[valid]
    er_vals = errors[valid]

    pearson = _safe_correlation(e_vals, er_vals)
    hit_bin = df.loc[valid, "result_status"].astype(str).str.lower().map({"hit": 1, "miss": 0})
    edge_hit_corr = _safe_correlation(e_vals, hit_bin) if hit_bin.notna().sum() >= 10 else None
    if pearson is None:
        return {
            "status": "insufficient_data",
            "n": n_valid,
            "pearson_edge_vs_projection_error": None,
            "pearson_edge_vs_hit_rate": edge_hit_corr,
        }

    # Monotonicity: does hit_rate decrease as edge increases?
    buckets = _edge_bucket_analysis(df)
    hrs = [b["hit_rate"] for b in buckets if b.get("hit_rate") is not None and b.get("sample_size", 0) >= _MIN_SEGMENT]
    monotone_decreasing = all(hrs[i] >= hrs[i + 1] for i in range(len(hrs) - 1)) if len(hrs) >= 2 else None

    return {
        "n": n_valid,
        "pearson_edge_vs_projection_error": pearson,
        "pearson_edge_vs_hit_rate": edge_hit_corr,
        "interpretation": (
            "strong_positive: larger_edge_correlates_with_larger_inflation"
            if pearson > 0.30
            else "moderate_positive" if pearson > 0.10
            else "weak_or_no_correlation"
        ),
        "hit_rate_monotone_decreasing_with_edge": monotone_decreasing,
        "finding": (
            "CONFIRMED: edge size is positively correlated with projection error"
            if pearson > 0.30
            else "WEAK: limited evidence of edge-error relationship"
        ),
    }


# ---------------------------------------------------------------------------
# Policy simulation helpers
# ---------------------------------------------------------------------------

def _policy_verdict(
    n_flagged: int,
    flagged_hr: float | None,
    flagged_roi: float | None,
    baseline_hr: float | None,
    baseline_roi: float | None,
) -> str:
    if n_flagged < 20:
        return "INSUFFICIENT_SAMPLE"
    if flagged_hr is None or flagged_roi is None:
        return "INSUFFICIENT_SAMPLE"
    # If flagged rows perform AT or BETTER than baseline → suppressing them would hurt
    if baseline_hr is not None and flagged_hr >= baseline_hr and flagged_roi is not None and flagged_roi >= (baseline_roi or 0):
        return "REJECT_POLICY"
    if flagged_hr < 0.35 and flagged_roi < -0.35:
        return "READY_FOR_SHADOW_TEST"
    if flagged_hr < 0.43 or flagged_roi < -0.30:
        return "PROMISING"
    if flagged_hr < 0.50 and flagged_roi < -0.10:
        return "MONITOR"
    return "REJECT_POLICY"


def _simulate_policy(
    df: pd.DataFrame,
    policy_name: str,
    description: str,
    flag_mask: pd.Series,
    baseline_hr: float | None,
    baseline_roi: float | None,
) -> dict[str, Any]:
    """Simulate suppressing rows matching flag_mask; compute counterfactual P&L."""
    flagged     = df[flag_mask]
    not_flagged = df[~flag_mask]
    n_flagged   = int(flag_mask.sum())

    flagged_hr  = _hit_rate(flagged)
    flagged_roi = _roi(flagged)

    # Counterfactual: sum of saved losses minus missed profits
    avoided_loss = 0.0
    lost_profit  = 0.0
    if "shadow_roi" in flagged.columns:
        roi_vals = pd.to_numeric(flagged["shadow_roi"], errors="coerce").dropna()
        avoided_loss = round(float(roi_vals[roi_vals < 0].abs().sum()), 4)
        lost_profit  = round(float(roi_vals[roi_vals > 0].sum()), 4)
    net_effect = round(avoided_loss - lost_profit, 4)

    return {
        "policy_name": policy_name,
        "description": description,
        "rows_flagged": n_flagged,
        "sample_size": n_flagged,
        "hit_rate": flagged_hr,
        "roi": flagged_roi,
        "avoided_loss_units": avoided_loss,
        "lost_profit_units": lost_profit,
        "net_units_saved": net_effect,
        "verdict": _policy_verdict(n_flagged, flagged_hr, flagged_roi, baseline_hr, baseline_roi),
        "note": "shadow_simulation_only_no_live_logic_changed",
    }


def _run_policy_simulations(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"status": "insufficient_data"}

    idx    = df.index
    # Build all masks against df.index so boolean alignment is guaranteed
    edge_n  = pd.to_numeric(df["edge"],                 errors="coerce") if "edge"                 in df.columns else pd.Series(float("nan"), index=idx)
    sel     = df["selection"].astype(str).str.lower()                      if "selection"            in df.columns else pd.Series("",          index=idx)
    mt      = df["market_type"].astype(str).str.lower()                    if "market_type"          in df.columns else pd.Series("",          index=idx)
    ctx     = df["context_pick_alignment"].fillna("").astype(str).str.lower() if "context_pick_alignment" in df.columns else pd.Series("", index=idx)
    is_combo = mt.isin({m.lower() for m in _COMBO_MARKETS})

    baseline_hr  = _hit_rate(df)
    baseline_roi = _roi(df)

    policies: dict[str, Any] = {}

    # P1: Cap effective edge at 4% — suppress all picks with edge >= 4%
    policies["cap_effective_edge_at_4_percent"] = _simulate_policy(
        df,
        "cap_effective_edge_at_4_percent",
        "Suppress any pick with edge >= 4%. Treats reported 4%+ edge as unreliable.",
        edge_n >= 4.0,
        baseline_hr, baseline_roi,
    )

    # P2: Require context-aligned for 6%+ edge
    policies["require_context_aligned_for_6plus_edge"] = _simulate_policy(
        df,
        "require_context_aligned_for_6plus_edge",
        "Suppress 6%+ edge picks that are not context-aligned.",
        (edge_n >= 6.0) & (ctx != "aligned"),
        baseline_hr, baseline_roi,
    )

    # P3: Suppress high-edge context-conflicted OVERs
    policies["suppress_high_edge_context_conflicted_over"] = _simulate_policy(
        df,
        "suppress_high_edge_context_conflicted_over",
        "Suppress OVER picks with edge >= 4% AND context = conflicted.",
        (edge_n >= 4.0) & (sel == "over") & (ctx == "conflicted"),
        baseline_hr, baseline_roi,
    )

    # P4: Suppress high-edge combo OVERs (edge >= 4% AND combo AND over)
    policies["suppress_high_edge_combo_over"] = _simulate_policy(
        df,
        "suppress_high_edge_combo_over",
        "Suppress OVER picks on combo markets with edge >= 4%.",
        (edge_n >= 4.0) & is_combo & (sel == "over"),
        baseline_hr, baseline_roi,
    )

    # P5: Downgrade confidence when edge >= 6% and projection error risk
    #     Proxy: suppress 6%+ edge OVER picks (confidence recalibration candidates)
    policies["downgrade_confidence_when_edge_6plus_over"] = _simulate_policy(
        df,
        "downgrade_confidence_when_edge_6plus_over",
        "Flag 6%+ edge OVER picks for confidence downgrade review. "
        "Simulated as full suppression for loss-avoided estimate.",
        (edge_n >= 6.0) & (sel == "over"),
        baseline_hr, baseline_roi,
    )

    # P6: Manual review for 6%+ edge any direction
    policies["manual_review_for_6plus_edge_any_direction"] = _simulate_policy(
        df,
        "manual_review_for_6plus_edge_any_direction",
        "Flag any pick with edge >= 6% for manual review before accepting.",
        edge_n >= 6.0,
        baseline_hr, baseline_roi,
    )

    # Summary: rank by net_units_saved descending
    ranked = sorted(
        [v for v in policies.values() if isinstance(v, dict)],
        key=lambda p: p.get("net_units_saved", 0),
        reverse=True,
    )

    return {
        "baseline_hit_rate": baseline_hr,
        "baseline_roi": baseline_roi,
        "policies": policies,
        "ranked_by_net_saved": [
            {
                "policy": p["policy_name"],
                "verdict": p["verdict"],
                "n_flagged": p["rows_flagged"],
                "hit_rate": p["hit_rate"],
                "roi": p["roi"],
                "net_units_saved": p["net_units_saved"],
            }
            for p in ranked
        ],
    }


# ---------------------------------------------------------------------------
# Top findings
# ---------------------------------------------------------------------------

def _build_top_findings(
    edge_buckets: list[dict[str, Any]],
    edge_dir: dict[str, Any],
    edge_mkt: dict[str, Any],
    edge_ctx: dict[str, Any],
    correlation: dict[str, Any],
) -> dict[str, Any]:
    valid_buckets = [b for b in edge_buckets if b.get("risk_flag") not in (None, "INSUFFICIENT_SAMPLE")]

    worst = sorted(
        [b for b in valid_buckets if b.get("avg_projection_error") is not None],
        key=lambda x: x["avg_projection_error"],
        reverse=True,
    )[:3]

    safest = sorted(
        [b for b in valid_buckets if b.get("avg_projection_error") is not None],
        key=lambda x: abs(x["avg_projection_error"]),
    )[:3]

    def _quick(s: dict[str, Any]) -> dict[str, Any]:
        return {
            "label": s.get("label"),
            "n": s.get("sample_size"),
            "error": s.get("avg_projection_error"),
            "hit_rate": s.get("hit_rate"),
            "roi": s.get("roi"),
            "risk_flag": s.get("risk_flag"),
        }

    # High-edge OVER
    high_over = edge_dir.get("6%+", {}).get("over", {}) if isinstance(edge_dir, dict) else {}
    # High-edge UNDER
    high_under = edge_dir.get("6%+", {}).get("under", {}) if isinstance(edge_dir, dict) else {}
    # High-edge combo
    high_combo = {}
    if isinstance(edge_mkt, dict):
        he_mkt = edge_mkt.get("6%+", {})
        if isinstance(he_mkt, dict):
            high_combo = he_mkt.get("combo", {})

    return {
        "worst_edge_buckets": [_quick(b) for b in worst],
        "safest_edge_buckets": [_quick(b) for b in safest],
        "high_edge_over_6pct":  _quick(high_over)  if high_over  else None,
        "high_edge_under_6pct": _quick(high_under) if high_under else None,
        "high_edge_combo_6pct": _quick(high_combo) if high_combo else None,
        "edge_error_correlation": correlation.get("pearson_edge_vs_projection_error"),
        "edge_correlation_finding": correlation.get("finding"),
        "monotone_hit_decrease": correlation.get("hit_rate_monotone_decreasing_with_edge"),
    }


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------

def build_edge_inflation_research(
    shadow_history_df: pd.DataFrame,
    prediction_date: str,
    pick_history_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build Phase 12C edge inflation research report.

    Shadow analytics and research only.
    No live projections, edges, gates, Kelly stakes, or selection changed.
    """
    if shadow_history_df.empty:
        return _empty_report(prediction_date)

    shadow_history_df = shadow_history_df.copy()

    graded = shadow_history_df[
        shadow_history_df.get("result_status", pd.Series(dtype=str))
        .astype(str).str.lower().isin(_GRADED)
    ].copy()

    if graded.empty:
        return _empty_report(prediction_date)

    # Overall stats
    n = int(len(graded))
    errors = _projection_error_series(graded).dropna()
    overall_hr  = _hit_rate(graded)
    overall_roi = _roi(graded)
    overall_me  = round(float(errors.mean()), 4) if not errors.empty else None
    avg_conf    = _safe_mean(graded.get("confidence", pd.Series(dtype=float)))
    avg_edge    = _safe_mean(graded.get("edge",       pd.Series(dtype=float)))
    gap         = round(overall_hr - avg_conf, 4) if (overall_hr is not None and avg_conf is not None) else None

    overall = {
        "total_graded_rows": n,
        "overall_hit_rate": overall_hr,
        "overall_roi": overall_roi,
        "avg_edge": avg_edge,
        "avg_confidence": avg_conf,
        "avg_projection_error": overall_me,
        "calibration_gap": gap,
        "overall_risk_flag": _classify_risk(overall_me, overall_hr, overall_roi, n),
    }

    # Attribution analyses
    edge_buckets = _edge_bucket_analysis(graded)
    edge_dir     = _edge_by_direction(graded)
    edge_mkt     = _edge_by_market(graded)
    edge_ctx     = _high_edge_context_analysis(graded)
    edge_conf    = _high_edge_confidence_analysis(graded)
    he_combo     = _high_edge_combo_analysis(graded)
    correlation  = _edge_error_correlation(graded)

    # Policy simulations
    simulations  = _run_policy_simulations(graded)

    # Pick-history validation (supplemental)
    pick_validation: dict[str, Any] = {"status": "not_provided"}
    if pick_history_df is not None and not pick_history_df.empty:
        ph = pick_history_df.copy()
        ph_graded = ph[ph.get("result_status", pd.Series(dtype=str))
                       .astype(str).str.lower().isin(_GRADED)].copy()
        # pick_history uses 'projection' not 'model_projection'
        if "projection" in ph_graded.columns:
            ph_graded = ph_graded.rename(columns={"projection": "model_projection"})
        ph_graded["shadow_roi"] = ph_graded.get("shadow_roi", pd.Series(dtype=float))
        ph_buckets = _edge_bucket_analysis(ph_graded)
        ph_corr    = _edge_error_correlation(ph_graded)
        pick_validation = {
            "status": "ok",
            "source": "pick_history",
            "graded_rows": int(len(ph_graded)),
            "edge_buckets": ph_buckets,
            "correlation": ph_corr,
            "note": "pick_history_shadow_roi_absent_roi_metrics_unavailable",
        }

    top_findings = _build_top_findings(edge_buckets, edge_dir, edge_mkt, edge_ctx, correlation)

    return {
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "notes": [
            "shadow_analytics_and_research_only",
            "no_live_logic_changed",
            "no_projections_altered",
            "no_edges_modified",
            "no_gates_changed",
            "no_kelly_sizing_changed",
            "policy_simulations_are_research_only",
        ],
        "overall": overall,
        "edge_bucket_analysis": edge_buckets,
        "edge_by_direction": edge_dir,
        "edge_by_market": edge_mkt,
        "high_edge_context_analysis": edge_ctx,
        "high_edge_confidence_analysis": edge_conf,
        "high_edge_combo_analysis": he_combo,
        "edge_error_correlation": correlation,
        "policy_simulations": simulations,
        "pick_history_validation": pick_validation,
        "top_findings": top_findings,
    }


def _empty_report(prediction_date: str) -> dict[str, Any]:
    return {
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "overall": {"total_graded_rows": 0, "overall_risk_flag": "INSUFFICIENT_SAMPLE"},
        "edge_bucket_analysis": [],
        "edge_by_direction": {"status": "insufficient_data"},
        "edge_by_market": {"status": "insufficient_data"},
        "high_edge_context_analysis": {"status": "insufficient_data"},
        "high_edge_confidence_analysis": {"status": "insufficient_data"},
        "high_edge_combo_analysis": {"status": "insufficient_data"},
        "edge_error_correlation": {"status": "insufficient_data"},
        "policy_simulations": {"status": "insufficient_data"},
        "pick_history_validation": {"status": "not_provided"},
        "top_findings": {},
        "notes": ["history_empty"],
    }


# ---------------------------------------------------------------------------
# Operator text renderer
# ---------------------------------------------------------------------------

def _fp(v: float | None, p: int = 2) -> str:
    return f"{v:.{p}f}" if v is not None else "n/a"


def _fpc(v: float | None) -> str:
    return f"{v:.1%}" if v is not None else "n/a"


_RISK_ICON = {
    "CATASTROPHIC":       "💀",
    "SEVERE":             "🔴",
    "HIGH":               "🟠",
    "MODERATE":           "🟡",
    "ACCEPTABLE":         "✅",
    "INSUFFICIENT_SAMPLE":"—",
}
# Plain-text fallback (no emoji in operator files)
_RISK_TAG = {
    "CATASTROPHIC":       "[CATASTROPHIC]",
    "SEVERE":             "[SEVERE]",
    "HIGH":               "[HIGH]",
    "MODERATE":           "[MODERATE]",
    "ACCEPTABLE":         "[ACCEPTABLE]",
    "INSUFFICIENT_SAMPLE":"[N/A]",
}

_VERDICT_TAG = {
    "READY_FOR_SHADOW_TEST": "READY_SHADOW",
    "PROMISING":             "PROMISING",
    "MONITOR":               "MONITOR",
    "REJECT_POLICY":         "REJECT",
    "INSUFFICIENT_SAMPLE":   "N/A",
}


def _seg_line(s: dict[str, Any], indent: str = "  ") -> str:
    risk = _RISK_TAG.get(s.get("risk_flag", ""), "?")
    return (
        f"{indent}{s.get('label','?'):<44}  "
        f"n={s.get('sample_size',0):>4}  "
        f"err={_fp(s.get('avg_projection_error'),2):>6}  "
        f"hit={_fpc(s.get('hit_rate')):>7}  "
        f"roi={_fpc(s.get('roi')):>8}  "
        f"{risk}"
    )


def _render_operator_report(payload: dict[str, Any]) -> str:
    date = payload.get("prediction_date", "unknown")
    ov   = payload.get("overall", {})
    lines = [
        "Edge Inflation Containment Research (Phase 12C — shadow analytics only)",
        f"prediction_date   : {date}",
        "=" * 78,
        f"overall_risk_flag  : {ov.get('overall_risk_flag', 'n/a')}",
        f"graded_rows        : {ov.get('total_graded_rows', 0)}",
        f"avg_edge           : {_fp(ov.get('avg_edge'), 2)}",
        f"avg_projection_err : {_fp(ov.get('avg_projection_error'), 2)}  (positive = over-predicted)",
        f"hit_rate           : {_fpc(ov.get('overall_hit_rate'))}",
        f"roi                : {_fpc(ov.get('overall_roi'))}",
        f"calibration_gap    : {_fp(ov.get('calibration_gap'), 4)}",
        "",
    ]

    hdr = "  {:<44}  {:>4}  {:>8}  {:>7}  {:>8}".format("segment", "n", "err", "hit", "roi")
    sep = "  " + "-" * 74

    # Edge bucket analysis
    lines += ["Edge Bucket Analysis", "-" * 78, hdr, sep]
    for b in payload.get("edge_bucket_analysis", []):
        lines.append(_seg_line(b))
    lines.append("")

    # Edge correlation
    corr = payload.get("edge_error_correlation", {})
    lines += ["Edge-Error Correlation", "-" * 78]
    if corr.get("status") != "insufficient_data":
        lines.append(f"  pearson(edge, projection_error) = {_fp(corr.get('pearson_edge_vs_projection_error'),4)}")
        lines.append(f"  pearson(edge, hit_rate)         = {_fp(corr.get('pearson_edge_vs_hit_rate'),4)}")
        lines.append(f"  interpretation: {corr.get('interpretation','n/a')}")
        lines.append(f"  finding: {corr.get('finding','n/a')}")
        lines.append(f"  hit_rate_monotone_decreasing_with_edge: {corr.get('hit_rate_monotone_decreasing_with_edge','n/a')}")
    else:
        lines.append(f"  status: insufficient_data")
    lines.append("")

    # Edge × direction
    lines += ["Edge × Direction", "-" * 78, hdr, sep]
    ed = payload.get("edge_by_direction", {})
    if isinstance(ed, dict) and "status" not in ed:
        for eb in _EDGE_LABELS:
            bucket = ed.get(eb, {})
            for side in ("over", "under"):
                s = bucket.get(side, {})
                if isinstance(s, dict) and "sample_size" in s and s["sample_size"] > 0:
                    lines.append(_seg_line(s))
    lines.append("")

    # High-edge combo
    lines += ["High-Edge Combo Analysis (edge >= 4%)", "-" * 78, hdr, sep]
    hc = payload.get("high_edge_combo_analysis", {})
    if isinstance(hc, dict) and "status" not in hc:
        for key, s in hc.items():
            if isinstance(s, dict) and "sample_size" in s and s["sample_size"] > 0:
                lines.append(_seg_line(s))
    lines.append("")

    # High-edge context
    lines += ["High-Edge Context Analysis", "-" * 78]
    ctx_a = payload.get("high_edge_context_analysis", {})
    if isinstance(ctx_a, dict) and "status" not in ctx_a:
        if ctx_a.get("coverage_caution"):
            lines.append(f"  CAUTION: context coverage = {ctx_a.get('coverage_pct','?')}% (< 80%)")
        lines.append(hdr)
        lines.append(sep)
        for eb in ("4-6%", "6%+"):
            eb_data = ctx_a.get(eb, {})
            if isinstance(eb_data, dict):
                for ctx_label in ("aligned", "neutral", "conflicted"):
                    s = eb_data.get(ctx_label, {})
                    if isinstance(s, dict) and s.get("sample_size", 0) > 0:
                        lines.append(_seg_line(s))
    else:
        lines.append("  status: insufficient_data")
    lines.append("")

    # Policy simulations
    lines += ["Policy Simulation Results (research only — NOT applied)", "=" * 78]
    sims = payload.get("policy_simulations", {})
    if isinstance(sims, dict) and "policies" in sims:
        lines.append(f"  baseline_hit_rate : {_fpc(sims.get('baseline_hit_rate'))}")
        lines.append(f"  baseline_roi      : {_fpc(sims.get('baseline_roi'))}")
        lines.append("")
        hdr_pol = "  {:<46}  {:>4}  {:>7}  {:>8}  {:>9}  {:>14}".format(
            "policy", "n", "hit", "roi", "net_saved", "verdict"
        )
        lines.append(hdr_pol)
        lines.append("  " + "-" * 95)
        for p in sims.get("ranked_by_net_saved", []):
            lines.append(
                f"  {p.get('policy','?')[:46]:<46}  "
                f"{p.get('n_flagged',0):>4}  "
                f"{_fpc(p.get('hit_rate')):>7}  "
                f"{_fpc(p.get('roi')):>8}  "
                f"{_fp(p.get('net_units_saved'),2):>9}  "
                f"{_VERDICT_TAG.get(p.get('verdict',''), '?'):>14}"
            )
        lines.append("")
        lines.append("  Detailed policy verdicts:")
        for name, p in sims.get("policies", {}).items():
            if not isinstance(p, dict):
                continue
            lines.append(f"    [{_VERDICT_TAG.get(p.get('verdict',''),'?')}]  {p.get('policy_name','?')}")
            lines.append(f"           {p.get('description','')}")
            lines.append(f"           n={p.get('rows_flagged',0)}  hit={_fpc(p.get('hit_rate'))}  "
                         f"roi={_fpc(p.get('roi'))}  "
                         f"avoided={_fp(p.get('avoided_loss_units'),2)}  "
                         f"lost_profit={_fp(p.get('lost_profit_units'),2)}  "
                         f"net={_fp(p.get('net_units_saved'),2)}")
            lines.append("")
    else:
        lines.append(f"  status: {sims.get('status','insufficient_data')}")
    lines.append("")

    # Top findings
    lines += ["Top Findings", "=" * 78]
    tf = payload.get("top_findings", {})
    lines.append("Worst edge buckets (by mean projection error):")
    for s in tf.get("worst_edge_buckets", []):
        if s:
            lines.append(f"  [{s.get('risk_flag','?')}]  {s.get('label','?'):<12}  "
                         f"n={s.get('n',0):>4}  err={_fp(s.get('error'),2):>6}  "
                         f"hit={_fpc(s.get('hit_rate')):>7}  roi={_fpc(s.get('roi'))}")
    lines.append("")
    lines.append("Safest edge buckets:")
    for s in tf.get("safest_edge_buckets", []):
        if s:
            lines.append(f"  [{s.get('risk_flag','?')}]  {s.get('label','?'):<12}  "
                         f"n={s.get('n',0):>4}  err={_fp(s.get('error'),2):>6}  "
                         f"hit={_fpc(s.get('hit_rate')):>7}  roi={_fpc(s.get('roi'))}")
    lines.append("")
    he_over  = tf.get("high_edge_over_6pct")
    he_under = tf.get("high_edge_under_6pct")
    if he_over:
        lines.append(f"6%+ OVER  : n={he_over.get('n',0)}  err={_fp(he_over.get('error'),2)}  "
                     f"hit={_fpc(he_over.get('hit_rate'))}  roi={_fpc(he_over.get('roi'))}")
    if he_under:
        lines.append(f"6%+ UNDER : n={he_under.get('n',0)}  err={_fp(he_under.get('error'),2)}  "
                     f"hit={_fpc(he_under.get('hit_rate'))}  roi={_fpc(he_under.get('roi'))}")
    he_combo = tf.get("high_edge_combo_6pct")
    if he_combo:
        lines.append(f"6%+ COMBO : n={he_combo.get('n',0)}  err={_fp(he_combo.get('error'),2)}  "
                     f"hit={_fpc(he_combo.get('hit_rate'))}  roi={_fpc(he_combo.get('roi'))}")
    lines.append(f"\nedge_error_correlation: {_fp(tf.get('edge_error_correlation'),4)}")
    lines.append(f"finding: {tf.get('edge_correlation_finding','n/a')}")
    lines.append("")
    lines.append(
        "NOTE: Shadow analytics only — no projections, edges, gates, or Kelly stakes changed."
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def edge_research_json_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return (
        Path(runtime_root)
        / "diagnostics"
        / f"edge_inflation_research_{date}.json"
    )


def edge_research_txt_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return (
        Path(runtime_root)
        / "operator"
        / f"edge_inflation_research_{date}.txt"
    )


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def write_edge_inflation_research(
    shadow_history_df: pd.DataFrame,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    pick_history_df: pd.DataFrame | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Write edge inflation research JSON + operator TXT.

    Returns (json_path, txt_path, payload). Shadow analytics only.
    """
    payload   = build_edge_inflation_research(
        shadow_history_df, prediction_date, pick_history_df=pick_history_df
    )
    json_path = edge_research_json_path_for_date(prediction_date, runtime_root)
    txt_path  = edge_research_txt_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    txt_path.write_text(_render_operator_report(payload), encoding="utf-8")
    return json_path, txt_path, payload


__all__ = [
    "REPORT_VERSION",
    "build_edge_inflation_research",
    "edge_research_json_path_for_date",
    "edge_research_txt_path_for_date",
    "write_edge_inflation_research",
]
