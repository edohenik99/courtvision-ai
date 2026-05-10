"""
Phase 12: Projection Calibration Shadow Layer.

Shadow-only analytics — evaluates whether CourtVision projections are
statistically well-calibrated over time.

This module reads from data/history and produces calibration diagnostics
WITHOUT modifying any live logic, scores, gates, Kelly sizing, or outputs.

Primary source: market_shadow_history.csv (model_projection, edge, confidence,
                shadow_roi, actual_value all 100% available in graded rows)
Supplementary:  pick_history.csv (elite/Kelly-level projection error)

Schema constraints (confirmed by Phase 12 audit):
  - fragility_bucket    : 0% populated in graded market_shadow rows → insufficient_data
  - survivability_bucket: 0% populated in graded market_shadow rows → insufficient_data
  - context_pick_alignment : 55% populated in graded rows → computed with coverage caveat
  - final_elite_rejection_reason: 5% in graded rows → board_bucket insufficient_data
  - projection error    : computable — model_projection and actual_value both 100%
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPORT_VERSION = "1.0"
_GRADED = frozenset({"hit", "miss"})

# Readiness thresholds (documented):
#   INSUFFICIENT_SAMPLE : n < 30 — too few graded rows to draw any conclusions
#   UNSTABLE            : n ≥ 30, calibration_gap > 0.15 — badly miscalibrated
#   NEUTRAL             : calibration_gap in (−0.05, +0.05) but n < 100
#   PROMISING           : calibration_gap in (−0.05, +0.05) and n ≥ 100
#   STRONGLY_CALIBRATED : calibration_gap in (−0.03, +0.03) and n ≥ 200
_MIN_SAMPLE        = 30
_MIN_PROMISING     = 100
_MIN_STRONG        = 200
_GAP_UNSTABLE      = 0.15
_GAP_PROMISING     = 0.05
_GAP_STRONG        = 0.03

# Confidence bucket edges
_CONF_EDGES = [0.0, 0.55, 0.60, 0.70, 0.80, 1.01]
_CONF_LABELS = ["<0.55", "0.55-0.60", "0.60-0.70", "0.70-0.80", "0.80+"]

# Edge bucket edges (abs_edge / edge %)
_EDGE_EDGES  = [0.0, 2.0, 4.0, 6.0, float("inf")]
_EDGE_LABELS = ["0-2%", "2-4%", "4-6%", "6%+"]

# Combo markets
_COMBO_MARKETS = frozenset({
    "player_points_assists",
    "player_points_rebounds",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
})


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _safe_mean(series: pd.Series) -> float | None:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    return round(float(vals.mean()), 4) if not vals.empty else None


def _safe_median(series: pd.Series) -> float | None:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    return round(float(vals.median()), 4) if not vals.empty else None


def _safe_std(series: pd.Series) -> float | None:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    return round(float(vals.std()), 4) if len(vals) >= 2 else None


def _hit_rate(df: pd.DataFrame) -> float | None:
    if df.empty or "result_status" not in df.columns:
        return None
    status = df["result_status"].astype(str).str.lower()
    wins = int((status == "hit").sum())
    denom = int(status.isin(["hit", "miss"]).sum())
    return round(wins / denom, 4) if denom else None


def _roi(df: pd.DataFrame) -> float | None:
    if "shadow_roi" in df.columns:
        vals = pd.to_numeric(df["shadow_roi"], errors="coerce").dropna()
        if not vals.empty:
            return round(float(vals.mean()), 4)
    return None


def _classify_readiness(n: int, calibration_gap: float | None) -> str:
    if n < _MIN_SAMPLE:
        return "INSUFFICIENT_SAMPLE"
    if calibration_gap is None:
        return "INSUFFICIENT_SAMPLE"
    abs_gap = abs(calibration_gap)
    if abs_gap > _GAP_UNSTABLE:
        return "UNSTABLE"
    if n >= _MIN_STRONG and abs_gap <= _GAP_STRONG:
        return "STRONGLY_CALIBRATED"
    if n >= _MIN_PROMISING and abs_gap <= _GAP_PROMISING:
        return "PROMISING"
    return "NEUTRAL"


def _calibration_gap(df: pd.DataFrame) -> float | None:
    """actual_hit_rate − mean(confidence).

    Negative → model overconfident; positive → model underconfident.
    """
    hr = _hit_rate(df)
    avg_conf = _safe_mean(df.get("confidence", pd.Series(dtype=float)))
    if hr is None or avg_conf is None:
        return None
    return round(hr - avg_conf, 4)


def _projection_error_stats(df: pd.DataFrame) -> dict[str, Any]:
    """model_projection − actual_value (positive = over-predicted)."""
    if "model_projection" not in df.columns or "actual_value" not in df.columns:
        return {"status": "insufficient_projection_fields"}
    proj = pd.to_numeric(df["model_projection"], errors="coerce")
    actual = pd.to_numeric(df["actual_value"], errors="coerce")
    valid = proj.notna() & actual.notna()
    if valid.sum() < 1:
        return {"status": "insufficient_projection_fields"}
    errors = proj[valid] - actual[valid]
    return {
        "status": "ok",
        "n": int(valid.sum()),
        "mean_error": round(float(errors.mean()), 4),
        "median_error": round(float(errors.median()), 4),
        "std_error": round(float(errors.std()), 4) if len(errors) >= 2 else None,
        "pct_over_predicted": round(float((errors > 0).sum() / len(errors)), 4),
        "pct_under_predicted": round(float((errors < 0).sum() / len(errors)), 4),
    }


# ---------------------------------------------------------------------------
# Segment analytics
# ---------------------------------------------------------------------------

def _segment_stats(seg: pd.DataFrame, label: str = "") -> dict[str, Any]:
    n = int(len(seg))
    hr = _hit_rate(seg)
    avg_conf = _safe_mean(seg.get("confidence", pd.Series(dtype=float)))
    gap = round(hr - avg_conf, 4) if (hr is not None and avg_conf is not None) else None
    return {
        "label": label,
        "sample_size": n,
        "hit_rate": hr,
        "roi": _roi(seg),
        "avg_edge": _safe_mean(seg.get("edge", pd.Series(dtype=float))),
        "avg_confidence": avg_conf,
        "avg_profit_loss": _safe_mean(seg.get("shadow_roi", pd.Series(dtype=float))),
        "expected_win_rate": avg_conf,
        "calibration_gap": gap,
        "readiness": _classify_readiness(n, gap),
    }


def _directional_stats(df: pd.DataFrame) -> dict[str, Any]:
    if "selection" not in df.columns:
        return {"status": "insufficient_data"}
    sel = df["selection"].astype(str).str.lower()
    overs  = df[sel == "over"]
    unders = df[sel == "under"]
    return {
        "over": _segment_stats(overs, "over"),
        "under": _segment_stats(unders, "under"),
        "over_vs_under_hit_rate_delta": (
            round((_hit_rate(overs) or 0) - (_hit_rate(unders) or 0), 4)
            if (_hit_rate(overs) is not None and _hit_rate(unders) is not None)
            else None
        ),
        "over_vs_under_roi_delta": (
            round((_roi(overs) or 0) - (_roi(unders) or 0), 4)
            if (_roi(overs) is not None and _roi(unders) is not None)
            else None
        ),
    }


def _bucket_stats(
    df: pd.DataFrame,
    col: str,
    edges: list[float],
    labels: list[str],
) -> list[dict[str, Any]]:
    if col not in df.columns:
        return []
    vals = pd.to_numeric(df[col], errors="coerce")
    results = []
    for label, lo, hi in zip(labels, edges[:-1], edges[1:]):
        mask = (vals >= lo) & (vals < hi)
        seg = df[mask]
        stats = _segment_stats(seg, label)
        results.append(stats)
    return results


def _market_bucket_stats(df: pd.DataFrame) -> dict[str, Any]:
    col = "market_type"
    if col not in df.columns:
        return {"status": "insufficient_data"}
    mt = df[col].astype(str).str.lower()
    buckets: dict[str, pd.DataFrame] = {
        "player_points": df[mt == "player_points"],
        "player_rebounds": df[mt == "player_rebounds"],
        "player_assists": df[mt == "player_assists"],
        "combos": df[mt.isin({m.lower() for m in _COMBO_MARKETS})],
        "other": df[~mt.isin({"player_points", "player_rebounds", "player_assists"}
                               | {m.lower() for m in _COMBO_MARKETS})],
    }
    return {name: _segment_stats(seg, name) for name, seg in buckets.items()}


def _context_bucket_stats(df: pd.DataFrame) -> dict[str, Any]:
    col = "context_pick_alignment"
    if col not in df.columns:
        return {"status": "insufficient_data"}
    coverage = int(df[col].notna().sum())
    total = len(df)
    cov_pct = round(coverage / total * 100, 1) if total else 0
    ctx = df[col].astype(str).str.lower()
    result: dict[str, Any] = {
        "coverage_pct": cov_pct,
        "coverage_caution": cov_pct < 80,
    }
    for label in ("aligned", "neutral", "conflicted"):
        result[label] = _segment_stats(df[ctx == label], label)
    return result


def _fragility_bucket_stats(df: pd.DataFrame) -> dict[str, Any]:
    col = "fragility_bucket"
    if col not in df.columns:
        return {"status": "insufficient_data", "note": "column_absent"}
    coverage = int(df[col].notna().sum())
    if coverage == 0:
        return {
            "status": "insufficient_data",
            "note": "fragility_bucket_not_populated_in_graded_rows",
            "coverage_pct": 0.0,
        }
    total = len(df)
    result: dict[str, Any] = {
        "status": "ok",
        "coverage_pct": round(coverage / total * 100, 1),
    }
    fb = df[col].astype(str).str.upper()
    for label in ("LOW", "MEDIUM", "HIGH"):
        result[label.lower()] = _segment_stats(df[fb == label], label)
    return result


def _board_bucket_stats(df: pd.DataFrame) -> dict[str, Any]:
    """Elite vs full-market split requires final_elite_rejection_reason (<5% populated)."""
    col = "final_elite_rejection_reason"
    if col not in df.columns:
        return {"status": "insufficient_data", "note": "column_absent"}
    coverage = int(df[col].notna().sum())
    total = len(df)
    cov_pct = round(coverage / total * 100, 1) if total else 0
    if cov_pct < 20:
        return {
            "status": "insufficient_data",
            "note": "final_elite_rejection_reason_sparse",
            "coverage_pct": cov_pct,
        }
    rej = df[col].astype(str)
    elite = df[rej.str.strip() == ""]
    full_market = df[rej.str.strip() != ""]
    return {
        "status": "ok",
        "coverage_pct": cov_pct,
        "elite_board": _segment_stats(elite, "elite_board"),
        "full_market": _segment_stats(full_market, "full_market"),
    }


# ---------------------------------------------------------------------------
# Directional bias diagnostics
# ---------------------------------------------------------------------------

def _top_rejection_reasons(df: pd.DataFrame) -> list[dict[str, Any]]:
    col = "final_elite_rejection_reason"
    if df.empty or col not in df.columns:
        return []
    counts = df[col].dropna().astype(str).value_counts()
    total = int(counts.sum())
    return [
        {"reason": str(reason), "count": int(cnt), "pct": round(cnt / total * 100, 1)}
        for reason, cnt in counts.head(10).items()
    ]


def _directional_bias_diagnostics(
    all_rows_df: pd.DataFrame,
    graded_df: pd.DataFrame,
) -> dict[str, Any]:
    """Board-level OVER vs UNDER directional bias diagnostics.

    Measures whether UNDER dominance is justified by historical performance or
    caused by overly harsh OVER gates. Shadow analytics only — no gates changed.

    all_rows_df: all rows in market_shadow_history (for acceptance-rate counts).
    graded_df  : rows with result_status hit/miss (for hit-rate and ROI).
    """
    if "selection" not in all_rows_df.columns:
        return {"status": "insufficient_data", "note": "selection_column_absent"}

    sel_all = all_rows_df["selection"].astype(str).str.lower()
    all_overs = all_rows_df[sel_all == "over"]
    all_unders = all_rows_df[sel_all == "under"]
    total_all = len(all_rows_df)

    result: dict[str, Any] = {
        "full_market_over_count": int(len(all_overs)),
        "full_market_under_count": int(len(all_unders)),
        "full_market_over_pct": round(len(all_overs) / total_all * 100, 1) if total_all else None,
        "full_market_under_pct": round(len(all_unders) / total_all * 100, 1) if total_all else None,
    }

    # Elite board counts: empty final_elite_rejection_reason → elite accepted
    if "final_elite_rejection_reason" in all_rows_df.columns:
        rej = all_rows_df["final_elite_rejection_reason"].fillna("").astype(str).str.strip()
        is_elite = rej == ""
        elite_df = all_rows_df[is_elite]
        sel_elite = elite_df["selection"].astype(str).str.lower()
        elite_over_count = int((sel_elite == "over").sum())
        elite_under_count = int((sel_elite == "under").sum())

        n_overs = len(all_overs)
        n_unders = len(all_unders)
        over_acc = round(elite_over_count / n_overs, 4) if n_overs else None
        under_acc = round(elite_under_count / n_unders, 4) if n_unders else None

        result["elite_over_count"] = elite_over_count
        result["elite_under_count"] = elite_under_count
        result["over_acceptance_rate"] = over_acc
        result["under_acceptance_rate"] = under_acc
        result["acceptance_rate_delta_over_minus_under"] = (
            round(over_acc - under_acc, 4)
            if (over_acc is not None and under_acc is not None)
            else None
        )

        # Rejection reasons broken out by direction
        rejected_df = all_rows_df[~is_elite]
        sel_rej = rejected_df["selection"].astype(str).str.lower()
        result["over_rejection_reasons"] = _top_rejection_reasons(rejected_df[sel_rej == "over"])
        result["under_rejection_reasons"] = _top_rejection_reasons(rejected_df[sel_rej == "under"])

        result["elite_board_coverage"] = {
            "total_rows": total_all,
            "elite_rows": int(is_elite.sum()),
            "rejected_rows": int((~is_elite).sum()),
            "elite_pct": round(is_elite.sum() / total_all * 100, 1) if total_all else 0,
            "note": "empty_final_elite_rejection_reason_treated_as_elite",
        }
    else:
        result["elite_over_count"] = None
        result["elite_under_count"] = None
        result["over_acceptance_rate"] = None
        result["under_acceptance_rate"] = None
        result["acceptance_rate_delta_over_minus_under"] = None
        result["over_rejection_reasons"] = []
        result["under_rejection_reasons"] = []
        result["elite_board_coverage"] = {"note": "final_elite_rejection_reason_column_absent"}

    # Hit rate and ROI by direction from graded rows
    if not graded_df.empty and "selection" in graded_df.columns:
        sel_g = graded_df["selection"].astype(str).str.lower()
        g_overs = graded_df[sel_g == "over"]
        g_unders = graded_df[sel_g == "under"]

        over_hr = _hit_rate(g_overs)
        under_hr = _hit_rate(g_unders)
        over_roi = _roi(g_overs)
        under_roi = _roi(g_unders)

        result["graded_over_count"] = int(len(g_overs))
        result["graded_under_count"] = int(len(g_unders))
        result["over_hit_rate"] = over_hr
        result["under_hit_rate"] = under_hr
        result["over_roi"] = over_roi
        result["under_roi"] = under_roi
        result["hit_rate_delta_over_minus_under"] = (
            round(over_hr - under_hr, 4)
            if (over_hr is not None and under_hr is not None)
            else None
        )
        result["roi_delta_over_minus_under"] = (
            round(over_roi - under_roi, 4)
            if (over_roi is not None and under_roi is not None)
            else None
        )

        # Context-aligned over vs under performance
        if "context_pick_alignment" in graded_df.columns:
            ctx = graded_df["context_pick_alignment"].astype(str).str.lower()
            aligned = graded_df[ctx == "aligned"]
            if not aligned.empty:
                sel_aligned = aligned["selection"].astype(str).str.lower()
                result["context_aligned_over"] = _segment_stats(aligned[sel_aligned == "over"], "context_aligned_over")
                result["context_aligned_under"] = _segment_stats(aligned[sel_aligned == "under"], "context_aligned_under")
            else:
                result["context_aligned_over"] = {"status": "insufficient_data"}
                result["context_aligned_under"] = {"status": "insufficient_data"}
        else:
            result["context_aligned_over"] = {"status": "insufficient_data", "note": "context_pick_alignment_absent"}
            result["context_aligned_under"] = {"status": "insufficient_data", "note": "context_pick_alignment_absent"}

        # High-caution OVER suppression: check known caution flag columns
        caution_col: str | None = None
        for col in ("caution_flag", "high_caution", "caution_level", "is_high_caution"):
            if col in graded_df.columns:
                caution_col = col
                break
        if caution_col is not None:
            caution_vals = graded_df[caution_col].astype(str).str.lower()
            caution_mask = caution_vals.isin({"true", "1", "yes", "high"})
            caution_overs = graded_df[caution_mask & (sel_g == "over")]
            c_hr = _hit_rate(caution_overs)
            result["high_caution_over_suppression"] = {
                "n": int(len(caution_overs)),
                "hit_rate": c_hr,
                "roi": _roi(caution_overs),
                "caution_source": caution_col,
                "verdict": (
                    "suppression_justified"
                    if (c_hr is not None and c_hr < 0.45)
                    else "suppression_may_be_overly_harsh"
                    if (c_hr is not None and c_hr >= 0.50)
                    else "inconclusive"
                ),
            }
        else:
            result["high_caution_over_suppression"] = {
                "status": "insufficient_data",
                "note": "no_caution_flag_column_found",
            }
    else:
        result["graded_over_count"] = 0
        result["graded_under_count"] = 0
        result["over_hit_rate"] = None
        result["under_hit_rate"] = None
        result["over_roi"] = None
        result["under_roi"] = None
        result["hit_rate_delta_over_minus_under"] = None
        result["roi_delta_over_minus_under"] = None
        result["context_aligned_over"] = {"status": "insufficient_data"}
        result["context_aligned_under"] = {"status": "insufficient_data"}
        result["high_caution_over_suppression"] = {"status": "insufficient_data"}

    # Bias verdict: is UNDER dominance justified by performance or by harsh gates?
    over_acc_v = result.get("over_acceptance_rate")
    under_acc_v = result.get("under_acceptance_rate")
    over_hr_v = result.get("over_hit_rate")
    under_hr_v = result.get("under_hit_rate")

    if over_acc_v is not None and under_acc_v is not None and over_hr_v is not None and under_hr_v is not None:
        if under_acc_v > over_acc_v and under_hr_v > over_hr_v:
            verdict = "under_dominance_justified_by_performance"
        elif under_acc_v > over_acc_v and under_hr_v <= over_hr_v:
            verdict = "under_dominance_not_justified_performance_similar_or_worse"
        elif over_acc_v > under_acc_v:
            verdict = "over_dominant_selection"
        else:
            verdict = "balanced_selection"
    else:
        verdict = "inconclusive_insufficient_data"
    result["directional_bias_verdict"] = verdict

    return result


# ---------------------------------------------------------------------------
# Rolling trends
# ---------------------------------------------------------------------------

def _rolling_trends(df: pd.DataFrame) -> dict[str, Any]:
    if "prediction_date" not in df.columns or df.empty:
        return {"status": "insufficient_data"}
    try:
        df = df.copy()
        df["_date"] = pd.to_datetime(df["prediction_date"], errors="coerce")
        df = df[df["_date"].notna()].sort_values("_date")
        dates = sorted(df["_date"].dt.date.unique())
    except Exception:
        return {"status": "insufficient_data"}
    if len(dates) < 2:
        return {"status": "insufficient_data", "unique_dates": len(dates)}

    def _window(n_slates: int) -> dict[str, Any]:
        if len(dates) < n_slates:
            return {"status": "insufficient_sample", "slates_available": len(dates)}
        cutoff = pd.Timestamp(dates[-n_slates])
        seg = df[df["_date"] >= cutoff]
        hr = _hit_rate(seg)
        avg_conf = _safe_mean(seg.get("confidence", pd.Series(dtype=float)))
        gap = round(hr - avg_conf, 4) if (hr is not None and avg_conf is not None) else None
        return {
            "slates": n_slates,
            "rows": int(len(seg)),
            "hit_rate": hr,
            "roi": _roi(seg),
            "avg_edge": _safe_mean(seg.get("edge", pd.Series(dtype=float))),
            "calibration_gap": gap,
            "readiness": _classify_readiness(len(seg), gap),
        }

    return {
        "unique_dates": len(dates),
        "last_7_slates": _window(7),
        "last_14_slates": _window(14),
        "last_30_slates": _window(30),
    }


# ---------------------------------------------------------------------------
# Overall calibration summary
# ---------------------------------------------------------------------------

def _overall_stats(df: pd.DataFrame) -> dict[str, Any]:
    n = len(df)
    hr = _hit_rate(df)
    avg_conf = _safe_mean(df.get("confidence", pd.Series(dtype=float)))
    gap = round(hr - avg_conf, 4) if (hr is not None and avg_conf is not None) else None
    return {
        "total_graded_rows": n,
        "hit_rate": hr,
        "roi": _roi(df),
        "avg_edge": _safe_mean(df.get("edge", pd.Series(dtype=float))),
        "avg_confidence": avg_conf,
        "calibration_gap": gap,
        "readiness": _classify_readiness(n, gap),
        "projection_error": _projection_error_stats(df),
    }


# ---------------------------------------------------------------------------
# Caution warnings
# ---------------------------------------------------------------------------

def _build_caution_warnings(
    overall: dict[str, Any],
    conf_buckets: list[dict[str, Any]],
    edge_buckets: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    n = overall.get("total_graded_rows", 0)
    if n < _MIN_SAMPLE:
        warnings.append(f"CAUTION: Only {n} graded rows — all metrics are preliminary.")
    gap = overall.get("calibration_gap")
    if gap is not None and abs(gap) > _GAP_UNSTABLE:
        direction = "overconfident" if gap < 0 else "underconfident"
        warnings.append(
            f"CAUTION: Model appears {direction} — calibration_gap={gap:+.3f} "
            f"(threshold={_GAP_UNSTABLE:+.2f})."
        )
    # Confidence inflation: if high-confidence buckets underperform
    for b in conf_buckets:
        if b.get("label", "").startswith("0.80") and b.get("sample_size", 0) >= 10:
            bg = b.get("calibration_gap")
            if bg is not None and bg < -0.10:
                warnings.append(
                    f"CAUTION: 0.80+ confidence bucket hit_rate underperforms confidence "
                    f"by {abs(bg):.1%} — possible confidence inflation."
                )
    # Edge inflation: if high-edge buckets underperform baseline
    baseline_roi = overall.get("roi")
    for b in edge_buckets:
        if b.get("label", "") == "6%+" and b.get("sample_size", 0) >= 10:
            br = b.get("roi")
            if br is not None and baseline_roi is not None and br < baseline_roi - 0.10:
                warnings.append(
                    "CAUTION: High-edge (6%+) picks underperform overall ROI — "
                    "possible edge inflation in model."
                )
    return warnings


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------

def build_projection_calibration_report(
    shadow_history_df: pd.DataFrame,
    prediction_date: str,
    pick_history_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build the Phase 12 projection calibration shadow report.

    Simulation/analytics only — no scoring, selection, or staking altered.
    """
    # Filter to graded rows, respecting calibration_eligible when present
    if shadow_history_df.empty:
        return _empty_report(prediction_date)

    graded = shadow_history_df[
        shadow_history_df.get("result_status", pd.Series(dtype=str))
        .astype(str).str.lower().isin(_GRADED)
    ].copy()

    # Honour calibration_eligible flag when populated
    if "calibration_eligible" in graded.columns:
        ce = graded["calibration_eligible"]
        populated = ce.notna() & (ce.astype(str).str.lower() != "nan")
        if populated.any():
            eligible = ce[populated].astype(str).str.lower().isin({"true", "1", "yes"})
            graded = graded[~populated | eligible]

    if graded.empty:
        return _empty_report(prediction_date)

    overall = _overall_stats(graded)
    directional = _directional_stats(graded)
    directional_bias = _directional_bias_diagnostics(shadow_history_df, graded)
    conf_buckets = _bucket_stats(graded, "confidence", _CONF_EDGES, _CONF_LABELS)
    edge_buckets = _bucket_stats(graded, "edge", _EDGE_EDGES, _EDGE_LABELS)
    market_buckets = _market_bucket_stats(graded)
    context_buckets = _context_bucket_stats(graded)
    fragility_buckets = _fragility_bucket_stats(graded)
    board_buckets = _board_bucket_stats(graded)
    rolling = _rolling_trends(graded)

    # Elite-level projection error from pick_history if available
    elite_projection_error: dict[str, Any] = {"status": "insufficient_data"}
    if pick_history_df is not None and not pick_history_df.empty:
        ph_graded = pick_history_df[
            pick_history_df.get("result_status", pd.Series(dtype=str))
            .astype(str).str.lower().isin(_GRADED)
        ].copy()
        # pick_history uses 'market' not 'market_type', 'projection' not 'model_projection'
        if "projection" in ph_graded.columns and "actual_value" in ph_graded.columns:
            ph_graded = ph_graded.rename(columns={"projection": "model_projection"})
            elite_projection_error = _projection_error_stats(ph_graded)
            elite_projection_error["source"] = "pick_history"

    caution_warnings = _build_caution_warnings(overall, conf_buckets, edge_buckets)

    return {
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "notes": [
            "shadow_analytics_only",
            "no_live_logic_changed",
            "no_scoring_thresholds_changed",
            "no_kelly_sizing_changed",
        ],
        "overall": overall,
        "directional": directional,
        "directional_bias": directional_bias,
        "confidence_buckets": conf_buckets,
        "edge_buckets": edge_buckets,
        "market_buckets": market_buckets,
        "context_buckets": context_buckets,
        "fragility_buckets": fragility_buckets,
        "board_buckets": board_buckets,
        "rolling_trends": rolling,
        "elite_projection_error": elite_projection_error,
        "caution_warnings": caution_warnings,
    }


def _empty_report(prediction_date: str) -> dict[str, Any]:
    return {
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "overall": {"total_graded_rows": 0, "readiness": "INSUFFICIENT_SAMPLE"},
        "directional": {"status": "insufficient_data"},
        "directional_bias": {"status": "insufficient_data"},
        "confidence_buckets": [],
        "edge_buckets": [],
        "market_buckets": {"status": "insufficient_data"},
        "context_buckets": {"status": "insufficient_data"},
        "fragility_buckets": {"status": "insufficient_data"},
        "board_buckets": {"status": "insufficient_data"},
        "rolling_trends": {"status": "insufficient_data"},
        "elite_projection_error": {"status": "insufficient_data"},
        "caution_warnings": ["No graded history available."],
        "notes": ["history_empty"],
    }


# ---------------------------------------------------------------------------
# Operator text renderer
# ---------------------------------------------------------------------------

def _fmt_pct(val: float | None) -> str:
    return f"{val:.1%}" if val is not None else "n/a"


def _fmt_f(val: float | None, prec: int = 4) -> str:
    return f"{val:.{prec}f}" if val is not None else "n/a"


def _render_operator_report(payload: dict[str, Any]) -> str:
    date = payload.get("prediction_date", "unknown")
    ov = payload.get("overall", {})
    lines = [
        f"Projection Calibration Shadow (Phase 12 — analytics only)",
        f"prediction_date: {date}",
        "=" * 72,
        f"readiness : {ov.get('readiness', 'n/a')}",
        f"graded_rows: {ov.get('total_graded_rows', 0)}",
        f"hit_rate   : {_fmt_pct(ov.get('hit_rate'))}",
        f"roi        : {_fmt_pct(ov.get('roi'))}",
        f"avg_edge   : {_fmt_f(ov.get('avg_edge'), 2)}",
        f"avg_conf   : {_fmt_pct(ov.get('avg_confidence'))}",
        f"calib_gap  : {_fmt_f(ov.get('calibration_gap'), 4)}  "
        f"(actual_hit_rate − mean_confidence; negative = overconfident)",
        "",
    ]

    # Projection error
    pe = ov.get("projection_error", {})
    lines.append("Projection Error (model_projection − actual_value)")
    lines.append("-" * 72)
    if pe.get("status") == "ok":
        lines += [
            f"  n              : {pe.get('n')}",
            f"  mean_error     : {_fmt_f(pe.get('mean_error'), 2)}  (positive = over-predicted)",
            f"  median_error   : {_fmt_f(pe.get('median_error'), 2)}",
            f"  std_error      : {_fmt_f(pe.get('std_error'), 2)}",
            f"  pct_over_pred  : {_fmt_pct(pe.get('pct_over_predicted'))}",
            f"  pct_under_pred : {_fmt_pct(pe.get('pct_under_predicted'))}",
        ]
    else:
        lines.append(f"  status: {pe.get('status', 'insufficient_data')}")
    lines.append("")

    # Directional
    d = payload.get("directional", {})
    lines.append("Directional (OVER vs UNDER)")
    lines.append("-" * 72)
    if "over" in d and "under" in d:
        for side in ("over", "under"):
            s = d[side]
            lines.append(
                f"  {side:<6}  n={s.get('sample_size',0):>3}  "
                f"hit={_fmt_pct(s.get('hit_rate'))}  roi={_fmt_pct(s.get('roi'))}  "
                f"conf={_fmt_pct(s.get('avg_confidence'))}  gap={_fmt_f(s.get('calibration_gap'),4)}"
            )
        lines.append(f"  over_vs_under_hit_delta : {_fmt_f(d.get('over_vs_under_hit_rate_delta'), 4)}")
        lines.append(f"  over_vs_under_roi_delta : {_fmt_f(d.get('over_vs_under_roi_delta'), 4)}")
    else:
        lines.append("  status: insufficient_data")
    lines.append("")

    # Directional bias diagnostics
    db = payload.get("directional_bias", {})
    lines.append("Directional Bias Diagnostics (OVER/UNDER gate analysis)")
    lines.append("-" * 72)
    if db.get("status") == "insufficient_data":
        lines.append(f"  status: {db.get('note', 'insufficient_data')}")
    else:
        lines.append(f"  verdict: {db.get('directional_bias_verdict', 'n/a')}")
        lines.append(f"  full_market  over={db.get('full_market_over_count','n/a')}  "
                     f"({db.get('full_market_over_pct','n/a')}%)  "
                     f"under={db.get('full_market_under_count','n/a')}  "
                     f"({db.get('full_market_under_pct','n/a')}%)")
        lines.append(f"  elite_board  over={db.get('elite_over_count','n/a')}  "
                     f"under={db.get('elite_under_count','n/a')}")
        lines.append(f"  acceptance   over={_fmt_pct(db.get('over_acceptance_rate'))}  "
                     f"under={_fmt_pct(db.get('under_acceptance_rate'))}  "
                     f"delta={_fmt_f(db.get('acceptance_rate_delta_over_minus_under'), 4)}")
        lines.append(f"  graded perf  over_hit={_fmt_pct(db.get('over_hit_rate'))} "
                     f"under_hit={_fmt_pct(db.get('under_hit_rate'))}  "
                     f"hit_delta={_fmt_f(db.get('hit_rate_delta_over_minus_under'), 4)}")
        lines.append(f"               over_roi={_fmt_pct(db.get('over_roi'))} "
                     f"under_roi={_fmt_pct(db.get('under_roi'))}  "
                     f"roi_delta={_fmt_f(db.get('roi_delta_over_minus_under'), 4)}")
        ca_over = db.get("context_aligned_over", {})
        ca_under = db.get("context_aligned_under", {})
        if isinstance(ca_over, dict) and "hit_rate" in ca_over:
            lines.append(f"  ctx_aligned  over_hit={_fmt_pct(ca_over.get('hit_rate'))} "
                         f"(n={ca_over.get('sample_size',0)})  "
                         f"under_hit={_fmt_pct(ca_under.get('hit_rate') if isinstance(ca_under, dict) else None)} "
                         f"(n={ca_under.get('sample_size',0) if isinstance(ca_under, dict) else 0})")
        hco = db.get("high_caution_over_suppression", {})
        if isinstance(hco, dict) and hco.get("status") != "insufficient_data":
            lines.append(f"  hi_caut_over n={hco.get('n',0)}  "
                         f"hit={_fmt_pct(hco.get('hit_rate'))}  "
                         f"roi={_fmt_pct(hco.get('roi'))}  "
                         f"verdict={hco.get('verdict','n/a')}")
        top_over_rej = db.get("over_rejection_reasons", [])
        if top_over_rej:
            lines.append("  top_over_rejection_reasons:")
            for r in top_over_rej[:5]:
                lines.append(f"    {r.get('reason','?')[:55]:<55} {r.get('count','?'):>3}  ({r.get('pct','?')}%)")
    lines.append("")

    # Confidence buckets
    lines.append("Confidence Buckets")
    lines.append("-" * 72)
    for b in payload.get("confidence_buckets", []):
        lines.append(
            f"  {b.get('label','?'):<12}  n={b.get('sample_size',0):>3}  "
            f"hit={_fmt_pct(b.get('hit_rate'))}  expected={_fmt_pct(b.get('expected_win_rate'))}  "
            f"gap={_fmt_f(b.get('calibration_gap'),4)}  verdict={b.get('readiness','?')}"
        )
    lines.append("")

    # Edge buckets
    lines.append("Edge Buckets")
    lines.append("-" * 72)
    for b in payload.get("edge_buckets", []):
        lines.append(
            f"  {b.get('label','?'):<8}  n={b.get('sample_size',0):>3}  "
            f"hit={_fmt_pct(b.get('hit_rate'))}  roi={_fmt_pct(b.get('roi'))}  "
            f"avg_edge={_fmt_f(b.get('avg_edge'),2)}"
        )
    lines.append("")

    # Market buckets
    lines.append("Market Buckets")
    lines.append("-" * 72)
    mb = payload.get("market_buckets", {})
    if isinstance(mb, dict) and "status" not in mb:
        for name, s in mb.items():
            if isinstance(s, dict):
                lines.append(
                    f"  {name:<38}  n={s.get('sample_size',0):>3}  "
                    f"hit={_fmt_pct(s.get('hit_rate'))}  roi={_fmt_pct(s.get('roi'))}"
                )
    else:
        lines.append(f"  status: {mb.get('status','insufficient_data')}")
    lines.append("")

    # Context buckets
    lines.append("Context Alignment Buckets")
    lines.append("-" * 72)
    cb = payload.get("context_buckets", {})
    if isinstance(cb, dict) and "status" not in cb:
        if cb.get("coverage_caution"):
            lines.append(f"  CAUTION: context coverage = {cb.get('coverage_pct','?')}% (< 80%)")
        for label in ("aligned", "neutral", "conflicted"):
            s = cb.get(label, {})
            if s:
                lines.append(
                    f"  {label:<12}  n={s.get('sample_size',0):>3}  "
                    f"hit={_fmt_pct(s.get('hit_rate'))}  roi={_fmt_pct(s.get('roi'))}  "
                    f"gap={_fmt_f(s.get('calibration_gap'),4)}"
                )
    else:
        lines.append(f"  status: {cb.get('status','insufficient_data')}")
    lines.append("")

    # Fragility
    lines.append("Fragility Buckets")
    lines.append("-" * 72)
    fb = payload.get("fragility_buckets", {})
    if isinstance(fb, dict):
        lines.append(f"  status: {fb.get('status','n/a')}  note: {fb.get('note','')}")
    lines.append("")

    # Rolling trends
    lines.append("Rolling Trends")
    lines.append("-" * 72)
    rt = payload.get("rolling_trends", {})
    if isinstance(rt, dict) and rt.get("status") != "insufficient_data":
        lines.append(f"  unique_dates: {rt.get('unique_dates','?')}")
        for window in ("last_7_slates", "last_14_slates", "last_30_slates"):
            w = rt.get(window, {})
            if w.get("status") == "insufficient_sample":
                lines.append(
                    f"  {window:<18}  insufficient_sample "
                    f"(available={w.get('slates_available','?')})"
                )
            else:
                lines.append(
                    f"  {window:<18}  n={w.get('rows',0):>3}  "
                    f"hit={_fmt_pct(w.get('hit_rate'))}  roi={_fmt_pct(w.get('roi'))}  "
                    f"gap={_fmt_f(w.get('calibration_gap'),4)}  {w.get('readiness','?')}"
                )
    else:
        lines.append(f"  status: {rt.get('status','insufficient_data')}")
    lines.append("")

    # Caution warnings
    warnings = payload.get("caution_warnings", [])
    if warnings:
        lines.append("Caution Warnings")
        lines.append("-" * 72)
        for w in warnings:
            lines.append(f"  {w}")
        lines.append("")

    lines.append(
        "NOTE: Shadow analytics only — no scoring, selection, Kelly sizing, "
        "or production outputs changed."
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def calibration_json_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return (
        Path(runtime_root)
        / "diagnostics"
        / f"projection_calibration_shadow_{date}.json"
    )


def calibration_txt_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return (
        Path(runtime_root)
        / "operator"
        / f"projection_calibration_shadow_{date}.txt"
    )


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def write_projection_calibration_report(
    shadow_history_df: pd.DataFrame,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    pick_history_df: pd.DataFrame | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Write calibration JSON + operator TXT. Returns (json_path, txt_path, payload).

    Shadow analytics only — no live logic altered.
    """
    payload = build_projection_calibration_report(
        shadow_history_df, prediction_date, pick_history_df=pick_history_df
    )
    json_path = calibration_json_path_for_date(prediction_date, runtime_root)
    txt_path = calibration_txt_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    txt_path.write_text(_render_operator_report(payload), encoding="utf-8")
    return json_path, txt_path, payload


__all__ = [
    "REPORT_VERSION",
    "build_projection_calibration_report",
    "calibration_json_path_for_date",
    "calibration_txt_path_for_date",
    "write_projection_calibration_report",
]
