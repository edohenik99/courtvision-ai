"""
Phase 12B: Projection Bias Attribution Layer.

Shadow-only investigative analytics — identifies WHERE and WHY projections
are systematically inflated.

Phase 12 discovered:
  calibration_gap ≈ -0.307
  mean_projection_error ≈ +2.45
  OVER hit_rate ≈ 38%, UNDER hit_rate ≈ 61%

This module attributes that inflation to specific segments without
modifying any projection, score, gate, or runtime behavior.

Schema constraints (confirmed by Phase 12 audit on 375 graded rows):
  100% available: model_projection, actual_value, confidence, edge,
                  shadow_roi, selection, market_type, quality_score,
                  selection_score, same_opponent_under_warning, team_abbr
   55% available: context_pick_alignment, context_caution_level
   59% available: opponent
    5% available: final_elite_rejection_reason (use all rows for board tier)
    0% available: fragility_bucket, minutes, venue, is_playoff
   ABSENT        : opponent_strength, pace, player_tier
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPORT_VERSION = "1.0"
_GRADED = frozenset({"hit", "miss"})
_MIN_SEGMENT = 10

# Confidence bucket definitions
_CONF_EDGES  = [0.0, 0.55, 0.60, 0.70, 0.80, 1.01]
_CONF_LABELS = ["<0.55", "0.55-0.60", "0.60-0.70", "0.70-0.80", "0.80+"]

# Edge bucket definitions
_EDGE_EDGES  = [0.0, 2.0, 4.0, 6.0, float("inf")]
_EDGE_LABELS = ["0-2%", "2-4%", "4-6%", "6%+"]

# Quality score tier definitions (based on observed distribution: min≈31, max≈282, mean≈96)
_QUAL_EDGES  = [0.0, 75.0, 90.0, 110.0, float("inf")]
_QUAL_LABELS = ["<75 (low)", "75-90 (moderate)", "90-110 (standard)", "110+ (high)"]

# Selection score tier definitions (observed: min≈26, max≈61, mean≈37)
_SEL_EDGES   = [0.0, 33.0, 38.0, 44.0, float("inf")]
_SEL_LABELS  = ["<33 (weak)", "33-38 (moderate)", "38-44 (strong)", "44+ (elite)"]

# Inflation classification:
#   Based on mean_projection_error (positive = over-predicted) and calibration_gap
#   WELL_CALIBRATED:    |mean_error| < 0.5  AND |calibration_gap| < 0.05
#   MILD_INFLATION:     mean_error in [0.5, 1.5)   OR calibration_gap in (-0.10, -0.05)
#   MODERATE_INFLATION: mean_error in [1.5, 3.0)   OR calibration_gap in (-0.20, -0.10)
#   SEVERE_INFLATION:   mean_error in [3.0, 5.0)   OR calibration_gap in (-0.30, -0.20)
#   EXTREME_INFLATION:  mean_error >= 5.0           OR calibration_gap <= -0.30
#   UNDER_CORRECTION:   mean_error <= -0.5 (model systematically under-predicts)
_INFLATION_THRESHOLDS = [
    ("EXTREME_INFLATION",   5.0,  float("inf"), -float("inf"),  -0.30),
    ("SEVERE_INFLATION",    3.0,  5.0,           -0.30,         -0.20),
    ("MODERATE_INFLATION",  1.5,  3.0,           -0.20,         -0.10),
    ("MILD_INFLATION",      0.5,  1.5,           -0.10,         -0.05),
    ("WELL_CALIBRATED",    -0.5,  0.5,           -0.05,          0.05),
    ("UNDER_CORRECTION",  -float("inf"), -0.5,   -float("inf"), float("inf")),
]

_COMBO_MARKETS = frozenset({
    "player_points_assists",
    "player_points_rebounds",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
})


# ---------------------------------------------------------------------------
# Low-level helpers (self-contained — no imports from other reporting modules)
# ---------------------------------------------------------------------------

def _safe_mean(series: pd.Series) -> float | None:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    return round(float(vals.mean()), 4) if not vals.empty else None


def _safe_median(series: pd.Series) -> float | None:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    return round(float(vals.median()), 4) if not vals.empty else None


def _hit_rate(df: pd.DataFrame) -> float | None:
    if df.empty or "result_status" not in df.columns:
        return None
    s = df["result_status"].astype(str).str.lower()
    denom = int(s.isin(["hit", "miss"]).sum())
    return round(int((s == "hit").sum()) / denom, 4) if denom else None


def _roi(df: pd.DataFrame) -> float | None:
    if "shadow_roi" not in df.columns:
        return None
    vals = pd.to_numeric(df["shadow_roi"], errors="coerce").dropna()
    return round(float(vals.mean()), 4) if not vals.empty else None


def _projection_error_series(df: pd.DataFrame) -> pd.Series:
    proj   = pd.to_numeric(df.get("model_projection", pd.Series(dtype=float)), errors="coerce")
    actual = pd.to_numeric(df.get("actual_value",    pd.Series(dtype=float)), errors="coerce")
    return proj - actual


def _classify_inflation(
    mean_error: float | None,
    calibration_gap: float | None,
    n: int,
) -> str:
    if n < _MIN_SEGMENT:
        return "INSUFFICIENT_SAMPLE"
    if mean_error is None and calibration_gap is None:
        return "INSUFFICIENT_SAMPLE"
    me = mean_error if mean_error is not None else 0.0
    cg = calibration_gap if calibration_gap is not None else 0.0

    if me >= 5.0 or cg <= -0.30:
        return "EXTREME_INFLATION"
    if me >= 3.0 or cg <= -0.20:
        return "SEVERE_INFLATION"
    if me >= 1.5 or cg <= -0.10:
        return "MODERATE_INFLATION"
    if me >= 0.5 or cg <= -0.05:
        return "MILD_INFLATION"
    if me <= -0.5:
        return "UNDER_CORRECTION"
    return "WELL_CALIBRATED"


# ---------------------------------------------------------------------------
# Segment attribution (core unit)
# ---------------------------------------------------------------------------

def _segment_stats(df: pd.DataFrame, label: str) -> dict[str, Any]:
    n = int(len(df))
    if n == 0:
        return {
            "label": label,
            "sample_size": 0,
            "inflation_severity": "INSUFFICIENT_SAMPLE",
            "note": "empty_segment",
        }

    errors = _projection_error_series(df)
    valid  = errors.dropna()
    n_err  = int(len(valid))

    mean_err   = round(float(valid.mean()),   4) if n_err else None
    median_err = round(float(valid.median()), 4) if n_err else None
    over_pred_rate  = round(float((valid > 0).sum() / n_err), 4) if n_err else None
    under_pred_rate = round(float((valid < 0).sum() / n_err), 4) if n_err else None

    hr       = _hit_rate(df)
    avg_conf = _safe_mean(df.get("confidence", pd.Series(dtype=float)))
    gap      = round(hr - avg_conf, 4) if (hr is not None and avg_conf is not None) else None

    severity = _classify_inflation(mean_err, gap, n)

    result: dict[str, Any] = {
        "label": label,
        "sample_size": n,
        "mean_projection_error": mean_err,
        "median_projection_error": median_err,
        "hit_rate": hr,
        "roi": _roi(df),
        "avg_edge": _safe_mean(df.get("edge", pd.Series(dtype=float))),
        "avg_confidence": avg_conf,
        "calibration_gap": gap,
        "over_prediction_rate": over_pred_rate,
        "under_prediction_rate": under_pred_rate,
        "inflation_severity": severity,
    }

    if n < _MIN_SEGMENT:
        result["caution"] = f"sparse_sample (n={n} < {_MIN_SEGMENT})"

    return result


# ---------------------------------------------------------------------------
# Dimension 1: Market type
# ---------------------------------------------------------------------------

def _market_attribution(df: pd.DataFrame) -> dict[str, Any]:
    col = "market_type"
    if col not in df.columns:
        return {"status": "insufficient_data", "note": "column_absent"}
    mt = df[col].astype(str).str.lower()
    buckets = {
        "player_points": df[mt == "player_points"],
        "player_rebounds": df[mt == "player_rebounds"],
        "player_assists": df[mt == "player_assists"],
        "combos": df[mt.isin({m.lower() for m in _COMBO_MARKETS})],
        "other": df[~mt.isin({"player_points", "player_rebounds", "player_assists"}
                               | {m.lower() for m in _COMBO_MARKETS})],
    }
    result: dict[str, Any] = {}
    for name, seg in buckets.items():
        result[name] = _segment_stats(seg, name)

    # Per-combo breakdown
    combo_breakdown: dict[str, Any] = {}
    for combo in sorted(_COMBO_MARKETS):
        seg = df[mt == combo.lower()]
        combo_breakdown[combo] = _segment_stats(seg, combo)
    result["combo_breakdown"] = combo_breakdown
    return result


# ---------------------------------------------------------------------------
# Dimension 2: Directional (OVER vs UNDER)
# ---------------------------------------------------------------------------

def _directional_attribution(df: pd.DataFrame) -> dict[str, Any]:
    col = "selection"
    if col not in df.columns:
        return {"status": "insufficient_data", "note": "column_absent"}
    sel = df[col].astype(str).str.lower()
    overs  = df[sel == "over"]
    unders = df[sel == "under"]
    return {
        "over": _segment_stats(overs, "over"),
        "under": _segment_stats(unders, "under"),
        "error_delta_over_minus_under": (
            round(
                (_safe_mean(_projection_error_series(overs)) or 0)
                - (_safe_mean(_projection_error_series(unders)) or 0),
                4,
            )
            if (not overs.empty and not unders.empty)
            else None
        ),
    }


# ---------------------------------------------------------------------------
# Dimension 3: Confidence buckets
# ---------------------------------------------------------------------------

def _confidence_attribution(df: pd.DataFrame) -> list[dict[str, Any]]:
    if "confidence" not in df.columns:
        return []
    vals = pd.to_numeric(df["confidence"], errors="coerce")
    results = []
    for label, lo, hi in zip(_CONF_LABELS, _CONF_EDGES[:-1], _CONF_EDGES[1:]):
        seg = df[(vals >= lo) & (vals < hi)]
        results.append(_segment_stats(seg, label))
    return results


# ---------------------------------------------------------------------------
# Dimension 4: Edge buckets
# ---------------------------------------------------------------------------

def _edge_attribution(df: pd.DataFrame) -> list[dict[str, Any]]:
    if "edge" not in df.columns:
        return []
    vals = pd.to_numeric(df["edge"], errors="coerce")
    results = []
    for label, lo, hi in zip(_EDGE_LABELS, _EDGE_EDGES[:-1], _EDGE_EDGES[1:]):
        seg = df[(vals >= lo) & (vals < hi)]
        results.append(_segment_stats(seg, label))
    return results


# ---------------------------------------------------------------------------
# Dimension 5: Fragility buckets (0% populated → insufficient_data)
# ---------------------------------------------------------------------------

def _fragility_attribution(df: pd.DataFrame) -> dict[str, Any]:
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
    fb = df[col].astype(str).str.upper()
    return {
        "status": "ok",
        "coverage_pct": round(coverage / len(df) * 100, 1),
        "LOW": _segment_stats(df[fb == "LOW"], "LOW"),
        "MEDIUM": _segment_stats(df[fb == "MEDIUM"], "MEDIUM"),
        "HIGH": _segment_stats(df[fb == "HIGH"], "HIGH"),
    }


# ---------------------------------------------------------------------------
# Dimension 6: Context alignment (55% coverage)
# ---------------------------------------------------------------------------

def _context_attribution(df: pd.DataFrame) -> dict[str, Any]:
    col = "context_pick_alignment"
    if col not in df.columns:
        return {"status": "insufficient_data", "note": "column_absent"}
    coverage = int(df[col].notna().sum())
    total = len(df)
    cov_pct = round(coverage / total * 100, 1) if total else 0.0
    ctx = df[col].astype(str).str.lower()
    result: dict[str, Any] = {
        "coverage_pct": cov_pct,
        "coverage_caution": cov_pct < 80,
    }
    for label in ("aligned", "neutral", "conflicted"):
        result[label] = _segment_stats(df[ctx == label], label)
    return result


# ---------------------------------------------------------------------------
# Dimension 7: Board tier (elite vs full_market)
#   Uses all_rows_df since final_elite_rejection_reason is only 5% in graded
# ---------------------------------------------------------------------------

def _board_tier_attribution(all_rows_df: pd.DataFrame, graded_df: pd.DataFrame) -> dict[str, Any]:
    col = "final_elite_rejection_reason"
    if col not in all_rows_df.columns:
        return {"status": "insufficient_data", "note": "column_absent"}

    # Tag graded rows using the all_rows index
    rej_all = all_rows_df[col].fillna("").astype(str).str.strip()
    is_elite_all = rej_all == ""

    # Intersect with graded rows
    graded_idx = set(graded_df.index)
    elite_graded = graded_df[graded_df.index.isin(all_rows_df.index[is_elite_all])]
    full_market_graded = graded_df[~graded_df.index.isin(all_rows_df.index[is_elite_all])]

    rej_col_in_graded = col in graded_df.columns
    if rej_col_in_graded:
        rej_g = graded_df[col].fillna("").astype(str).str.strip()
        elite_graded = graded_df[rej_g == ""]
        full_market_graded = graded_df[rej_g != ""]

    coverage = int(graded_df[col].notna().sum()) if rej_col_in_graded else 0
    cov_pct = round(coverage / len(graded_df) * 100, 1) if len(graded_df) else 0.0

    return {
        "coverage_pct": cov_pct,
        "coverage_caution": cov_pct < 20,
        "note": "empty_rejection_reason_treated_as_elite",
        "elite": _segment_stats(elite_graded, "elite"),
        "full_market": _segment_stats(full_market_graded, "full_market"),
    }


# ---------------------------------------------------------------------------
# Dimension 8: Quality score tiers
# ---------------------------------------------------------------------------

def _quality_score_attribution(df: pd.DataFrame) -> list[dict[str, Any]]:
    if "quality_score" not in df.columns:
        return []
    vals = pd.to_numeric(df["quality_score"], errors="coerce")
    results = []
    for label, lo, hi in zip(_QUAL_LABELS, _QUAL_EDGES[:-1], _QUAL_EDGES[1:]):
        seg = df[(vals >= lo) & (vals < hi)]
        results.append(_segment_stats(seg, label))
    return results


# ---------------------------------------------------------------------------
# Dimension 9: Caution level (55% coverage)
# ---------------------------------------------------------------------------

def _caution_level_attribution(df: pd.DataFrame) -> dict[str, Any]:
    col = "context_caution_level"
    if col not in df.columns:
        return {"status": "insufficient_data", "note": "column_absent"}
    coverage = int(df[col].notna().sum())
    total = len(df)
    cov_pct = round(coverage / total * 100, 1) if total else 0.0
    lvl = df[col].astype(str).str.lower()
    result: dict[str, Any] = {
        "coverage_pct": cov_pct,
        "coverage_caution": cov_pct < 80,
    }
    for label in ("low", "medium", "high"):
        result[label] = _segment_stats(df[lvl == label], label)
    return result


# ---------------------------------------------------------------------------
# Dimension 10: Same-opponent rematches (2% coverage)
# ---------------------------------------------------------------------------

def _rematch_attribution(df: pd.DataFrame) -> dict[str, Any]:
    col = "same_opponent_under_warning"
    if col not in df.columns:
        return {"status": "insufficient_data", "note": "column_absent"}
    flag = df[col].astype(str).str.lower().isin({"true", "1", "yes"})
    rematch = df[flag]
    non_rematch = df[~flag]
    coverage = int(flag.sum())
    cov_pct = round(coverage / len(df) * 100, 1) if len(df) else 0.0
    result: dict[str, Any] = {
        "coverage_pct": cov_pct,
        "rematch_count": coverage,
        "non_rematch": _segment_stats(non_rematch, "non_rematch"),
    }
    if coverage >= _MIN_SEGMENT:
        result["rematch"] = _segment_stats(rematch, "rematch")
    else:
        result["rematch"] = {
            "status": "insufficient_data",
            "n": coverage,
            "note": f"only {coverage} rematch rows (min={_MIN_SEGMENT})",
        }
    return result


# ---------------------------------------------------------------------------
# Dimension 11: Season context (playoffs vs regular)
#   Inferred from prediction_date: NBA playoffs ≈ April 19 – June 22
# ---------------------------------------------------------------------------

def _season_context_attribution(df: pd.DataFrame) -> dict[str, Any]:
    if "prediction_date" not in df.columns:
        return {"status": "insufficient_data", "note": "column_absent"}
    try:
        dates = pd.to_datetime(df["prediction_date"], errors="coerce")
    except Exception:
        return {"status": "insufficient_data", "note": "parse_error"}
    # Infer playoffs: month=4 (Apr ≥ day 19) or month 5-6
    is_playoff = (
        ((dates.dt.month == 4) & (dates.dt.day >= 19))
        | (dates.dt.month == 5)
        | (dates.dt.month == 6)
    )
    playoff_df = df[is_playoff]
    regular_df = df[~is_playoff]
    cov_pct = round(is_playoff.notna().sum() / len(df) * 100, 1) if len(df) else 0.0
    return {
        "inference_method": "date_heuristic_nba_playoffs_apr19_jun22",
        "playoff_rows": int(is_playoff.sum()),
        "regular_rows": int((~is_playoff).sum()),
        "playoffs": _segment_stats(playoff_df, "playoffs"),
        "regular_season": _segment_stats(regular_df, "regular_season"),
    }


# ---------------------------------------------------------------------------
# Dimension 12: Team (top inflated teams)
# ---------------------------------------------------------------------------

def _team_attribution(df: pd.DataFrame, top_n: int = 10) -> dict[str, Any]:
    col = "team_abbr"
    if col not in df.columns:
        return {"status": "insufficient_data", "note": "column_absent"}
    teams = df[col].dropna().unique()
    team_stats = []
    for team in teams:
        seg = df[df[col] == team]
        if len(seg) < 3:
            continue
        s = _segment_stats(seg, team)
        team_stats.append(s)

    # Sort by mean_projection_error descending (most inflated first)
    team_stats.sort(key=lambda x: x.get("mean_projection_error") or 0, reverse=True)
    return {
        "total_teams": len(team_stats),
        "top_inflated": team_stats[:top_n],
        "top_calibrated": sorted(
            team_stats, key=lambda x: abs(x.get("mean_projection_error") or 99)
        )[:top_n],
    }


# ---------------------------------------------------------------------------
# Dimension 13: Player (top inflated players)
# ---------------------------------------------------------------------------

def _player_attribution(df: pd.DataFrame, top_n: int = 15) -> dict[str, Any]:
    col = "player_name"
    if col not in df.columns:
        return {"status": "insufficient_data", "note": "column_absent"}
    players = df[col].dropna().unique()
    player_stats = []
    for player in players:
        seg = df[df[col] == player]
        if len(seg) < 3:
            continue
        s = _segment_stats(seg, str(player))
        player_stats.append(s)

    player_stats.sort(key=lambda x: x.get("mean_projection_error") or 0, reverse=True)
    return {
        "total_players_analyzed": len(player_stats),
        "top_inflated": player_stats[:top_n],
        "top_calibrated": sorted(
            player_stats, key=lambda x: abs(x.get("mean_projection_error") or 99)
        )[:top_n],
    }


# ---------------------------------------------------------------------------
# Root-cause diagnostics
# ---------------------------------------------------------------------------

def _root_cause_diagnostics(
    graded_df: pd.DataFrame,
    market: dict[str, Any],
    directional: dict[str, Any],
    conf_buckets: list[dict[str, Any]],
    edge_buckets: list[dict[str, Any]],
    context: dict[str, Any],
    caution: dict[str, Any],
) -> dict[str, Any]:
    """Explicit attribution checks for 9 root-cause hypotheses."""

    diag: dict[str, Any] = {}

    # 1. OVER inflation concentration
    over = directional.get("over", {})
    under = directional.get("under", {})
    over_me = over.get("mean_projection_error")
    under_me = under.get("mean_projection_error")
    diag["over_inflation_concentration"] = {
        "finding": "OVER picks drive primary inflation",
        "over_mean_error": over_me,
        "under_mean_error": under_me,
        "error_delta": round(over_me - under_me, 4) if (over_me is not None and under_me is not None) else None,
        "severity": over.get("inflation_severity"),
        "supported": (over_me is not None and over_me > 2.0),
    }

    # 2. High-confidence inflation
    high_conf = next((b for b in conf_buckets if "0.80" in b.get("label", "")), {})
    diag["high_confidence_inflation"] = {
        "finding": "0.80+ confidence picks may be most overconfident",
        "bucket": "0.80+",
        "mean_error": high_conf.get("mean_projection_error"),
        "calibration_gap": high_conf.get("calibration_gap"),
        "severity": high_conf.get("inflation_severity"),
        "supported": (
            high_conf.get("calibration_gap") is not None
            and high_conf.get("calibration_gap") < -0.15
        ),
    }

    # 3. High-edge inflation
    high_edge = next((b for b in edge_buckets if "6%" in b.get("label", "")), {})
    diag["high_edge_inflation"] = {
        "finding": "High-edge (6%+) picks may reflect edge inflation not real value",
        "bucket": "6%+",
        "mean_error": high_edge.get("mean_projection_error"),
        "roi": high_edge.get("roi"),
        "severity": high_edge.get("inflation_severity"),
        "supported": (
            high_edge.get("roi") is not None and high_edge.get("roi") < -0.30
        ),
    }

    # 4. Fragile OVER inflation (fragility_bucket 0% → cannot confirm)
    diag["fragile_over_inflation"] = {
        "finding": "Cannot assess — fragility_bucket 0% populated in graded rows",
        "status": "insufficient_data",
        "recommendation": "Populate fragility_bucket in market_shadow_history to enable this analysis",
    }

    # 5. Context-conflicted inflation
    conflicted = context.get("conflicted", {}) if isinstance(context, dict) else {}
    diag["context_conflicted_inflation"] = {
        "finding": "Conflicted context picks may show worse calibration",
        "mean_error": conflicted.get("mean_projection_error"),
        "calibration_gap": conflicted.get("calibration_gap"),
        "severity": conflicted.get("inflation_severity"),
        "coverage_caveat": context.get("coverage_caution", True),
        "supported": (
            conflicted.get("mean_projection_error") is not None
            and conflicted.get("mean_projection_error") > 2.5
        ),
    }

    # 6. Elite-board inflation (final_elite_rejection_reason 5% → caveat)
    diag["elite_board_inflation"] = {
        "finding": "Elite vs full-market inflation comparison limited by sparse rejection column",
        "note": "final_elite_rejection_reason only 5% populated in graded rows",
        "recommendation": "Use all-rows data from directional_bias diagnostics for acceptance-rate analysis",
    }

    # 7. Combo inflation
    combo = market.get("combos", {})
    diag["combo_inflation"] = {
        "finding": "Combo markets (multi-stat) show highest mean projection error",
        "mean_error": combo.get("mean_projection_error"),
        "sample_size": combo.get("sample_size"),
        "severity": combo.get("inflation_severity"),
        "supported": (
            combo.get("mean_projection_error") is not None
            and combo.get("mean_projection_error") > 2.5
        ),
        "combo_breakdown": market.get("combo_breakdown", {}),
    }

    # 8. Minutes optimism (ABSENT column)
    diag["minutes_optimism"] = {
        "finding": "Cannot assess — 'minutes' column absent from market_shadow_history",
        "status": "insufficient_data",
        "recommendation": "Add minutes_projected to market_shadow_history schema",
    }

    # 9. Caution-level inflation
    high_caution = caution.get("high", {}) if isinstance(caution, dict) else {}
    diag["high_caution_inflation"] = {
        "finding": "High-caution context picks may show elevated inflation",
        "mean_error": high_caution.get("mean_projection_error"),
        "calibration_gap": high_caution.get("calibration_gap"),
        "severity": high_caution.get("inflation_severity"),
        "coverage_caveat": caution.get("coverage_caution", True),
        "supported": (
            high_caution.get("mean_projection_error") is not None
            and high_caution.get("mean_projection_error") > 2.5
        ),
    }

    return diag


# ---------------------------------------------------------------------------
# Top findings / rankings
# ---------------------------------------------------------------------------

_RANKABLE_INFLATION = {"EXTREME_INFLATION", "SEVERE_INFLATION", "MODERATE_INFLATION"}
_RANKABLE_CALIBRATED = {"WELL_CALIBRATED", "MILD_INFLATION", "UNDER_CORRECTION"}


def _rank_segments(
    segments: list[dict[str, Any]],
    n: int = 5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (worst, best) segments ranked by mean_projection_error magnitude."""
    valid = [s for s in segments if s.get("mean_projection_error") is not None and s.get("sample_size", 0) >= _MIN_SEGMENT]
    worst = sorted(valid, key=lambda x: x["mean_projection_error"], reverse=True)[:n]
    best  = sorted(valid, key=lambda x: abs(x["mean_projection_error"]))[:n]
    return worst, best


def _build_top_findings(
    overall: dict[str, Any],
    market: dict[str, Any],
    directional: dict[str, Any],
    conf_buckets: list[dict[str, Any]],
    edge_buckets: list[dict[str, Any]],
    context: dict[str, Any],
    caution: dict[str, Any],
    quality: list[dict[str, Any]],
    season: dict[str, Any],
) -> dict[str, Any]:
    # Collect all named segments
    all_segments: list[dict[str, Any]] = []

    for name in ("player_points", "player_rebounds", "player_assists", "combos", "other"):
        s = market.get(name, {})
        if isinstance(s, dict) and s.get("mean_projection_error") is not None:
            all_segments.append({**s, "dimension": "market_type"})

    for side in ("over", "under"):
        s = directional.get(side, {})
        if isinstance(s, dict) and s.get("mean_projection_error") is not None:
            all_segments.append({**s, "dimension": "directional"})

    for b in conf_buckets + edge_buckets + quality:
        if isinstance(b, dict) and b.get("mean_projection_error") is not None:
            all_segments.append({**b, "dimension": "bucket"})

    for label in ("aligned", "neutral", "conflicted"):
        s = context.get(label, {}) if isinstance(context, dict) else {}
        if isinstance(s, dict) and s.get("mean_projection_error") is not None:
            all_segments.append({**s, "dimension": "context"})

    for label in ("low", "medium", "high"):
        s = caution.get(label, {}) if isinstance(caution, dict) else {}
        if isinstance(s, dict) and s.get("mean_projection_error") is not None:
            all_segments.append({**s, "dimension": "caution_level"})

    for key in ("playoffs", "regular_season"):
        s = season.get(key, {}) if isinstance(season, dict) else {}
        if isinstance(s, dict) and s.get("mean_projection_error") is not None:
            all_segments.append({**s, "dimension": "season_context"})

    worst, best = _rank_segments(all_segments)

    def _summary(s: dict[str, Any]) -> dict[str, Any]:
        return {
            "label": s.get("label"),
            "dimension": s.get("dimension"),
            "sample_size": s.get("sample_size"),
            "mean_projection_error": s.get("mean_projection_error"),
            "hit_rate": s.get("hit_rate"),
            "roi": s.get("roi"),
            "calibration_gap": s.get("calibration_gap"),
            "inflation_severity": s.get("inflation_severity"),
        }

    # Most/least inflated in key dimensions
    def _most_inflated(segs: list[dict[str, Any]]) -> dict[str, Any] | None:
        valid = [s for s in segs if s.get("mean_projection_error") is not None and s.get("sample_size", 0) >= _MIN_SEGMENT]
        return _summary(max(valid, key=lambda x: x["mean_projection_error"])) if valid else None

    def _best_calibrated(segs: list[dict[str, Any]]) -> dict[str, Any] | None:
        valid = [s for s in segs if s.get("mean_projection_error") is not None and s.get("sample_size", 0) >= _MIN_SEGMENT]
        return _summary(min(valid, key=lambda x: abs(x["mean_projection_error"]))) if valid else None

    market_segs  = [market.get(k, {}) for k in ("player_points", "player_rebounds", "player_assists", "combos")]
    dir_segs     = [directional.get(k, {}) for k in ("over", "under")]
    strong_under = [s for s in dir_segs if s.get("label") == "under" and s.get("roi") is not None]
    strong_over  = [s for s in dir_segs if s.get("label") == "over"  and s.get("roi") is not None]

    return {
        "worst_calibrated_segments": [_summary(s) for s in worst],
        "best_calibrated_segments": [_summary(s) for s in best],
        "most_inflated_market": _most_inflated(market_segs),
        "best_calibrated_market": _best_calibrated(market_segs),
        "most_inflated_confidence_bucket": _most_inflated(conf_buckets),
        "best_calibrated_confidence_bucket": _best_calibrated(conf_buckets),
        "most_inflated_edge_bucket": _most_inflated(edge_buckets),
        "best_calibrated_edge_bucket": _best_calibrated(edge_buckets),
        "most_inflated_directional": _most_inflated(dir_segs),
        "best_calibrated_directional": _best_calibrated(dir_segs),
        "strongest_under_environment": _summary(strong_under[0]) if strong_under else None,
        "strongest_over_environment": _summary(strong_over[0]) if strong_over else None,
    }


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------

def build_projection_bias_attribution(
    shadow_history_df: pd.DataFrame,
    prediction_date: str,
    pick_history_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build Phase 12B projection bias attribution report.

    Shadow analytics only — nothing in this function alters any projection,
    score, gate, Kelly stake, or runtime output.
    """
    if shadow_history_df.empty:
        return _empty_report(prediction_date)

    # Never mutate caller's dataframe
    shadow_history_df = shadow_history_df.copy()

    graded = shadow_history_df[
        shadow_history_df.get("result_status", pd.Series(dtype=str))
        .astype(str).str.lower().isin(_GRADED)
    ]

    if graded.empty:
        return _empty_report(prediction_date)

    graded = graded.copy()

    # Compute overall
    errors = _projection_error_series(graded).dropna()
    n = int(len(graded))
    hr = _hit_rate(graded)
    avg_conf = _safe_mean(graded.get("confidence", pd.Series(dtype=float)))
    gap = round(hr - avg_conf, 4) if (hr is not None and avg_conf is not None) else None
    me = round(float(errors.mean()), 4) if not errors.empty else None

    overall: dict[str, Any] = {
        "total_graded_rows": n,
        "mean_projection_error": me,
        "median_projection_error": round(float(errors.median()), 4) if not errors.empty else None,
        "over_prediction_rate": round(float((errors > 0).sum() / len(errors)), 4) if not errors.empty else None,
        "under_prediction_rate": round(float((errors < 0).sum() / len(errors)), 4) if not errors.empty else None,
        "hit_rate": hr,
        "roi": _roi(graded),
        "avg_edge": _safe_mean(graded.get("edge", pd.Series(dtype=float))),
        "avg_confidence": avg_conf,
        "calibration_gap": gap,
        "inflation_severity": _classify_inflation(me, gap, n),
    }

    # Attribution dimensions
    market     = _market_attribution(graded)
    directional = _directional_attribution(graded)
    conf_buckets = _confidence_attribution(graded)
    edge_buckets = _edge_attribution(graded)
    fragility  = _fragility_attribution(graded)
    context    = _context_attribution(graded)
    board_tier = _board_tier_attribution(shadow_history_df, graded)
    quality    = _quality_score_attribution(graded)
    caution    = _caution_level_attribution(graded)
    rematch    = _rematch_attribution(graded)
    season     = _season_context_attribution(graded)
    teams      = _team_attribution(graded)
    players    = _player_attribution(graded)

    root_cause = _root_cause_diagnostics(
        graded, market, directional, conf_buckets, edge_buckets, context, caution
    )
    top_findings = _build_top_findings(
        overall, market, directional, conf_buckets, edge_buckets,
        context, caution, quality, season
    )

    return {
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "notes": [
            "shadow_analytics_only",
            "no_live_logic_changed",
            "no_projections_altered",
            "no_gates_modified",
            "no_kelly_sizing_changed",
            "investigative_bias_attribution_only",
        ],
        "overall": overall,
        "attribution": {
            "by_market": market,
            "by_direction": directional,
            "by_confidence_bucket": conf_buckets,
            "by_edge_bucket": edge_buckets,
            "by_fragility": fragility,
            "by_context": context,
            "by_board_tier": board_tier,
            "by_quality_score": quality,
            "by_caution_level": caution,
            "by_rematch": rematch,
            "by_season_context": season,
            "by_team": teams,
            "by_player": players,
        },
        "root_cause_diagnostics": root_cause,
        "top_findings": top_findings,
    }


def _empty_report(prediction_date: str) -> dict[str, Any]:
    return {
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "overall": {
            "total_graded_rows": 0,
            "inflation_severity": "INSUFFICIENT_SAMPLE",
        },
        "attribution": {},
        "root_cause_diagnostics": {},
        "top_findings": {},
        "notes": ["history_empty"],
    }


# ---------------------------------------------------------------------------
# Operator text renderer
# ---------------------------------------------------------------------------

def _fmt_f(v: float | None, prec: int = 2) -> str:
    return f"{v:.{prec}f}" if v is not None else "n/a"


def _fmt_pct(v: float | None) -> str:
    return f"{v:.1%}" if v is not None else "n/a"


def _severity_badge(s: str | None) -> str:
    badges = {
        "EXTREME_INFLATION": "★★★★★",
        "SEVERE_INFLATION":  "★★★★",
        "MODERATE_INFLATION":"★★★",
        "MILD_INFLATION":    "★★",
        "WELL_CALIBRATED":   "✓",
        "UNDER_CORRECTION":  "▼",
        "INSUFFICIENT_SAMPLE": "–",
    }
    return badges.get(s or "", "?")


def _render_segment_line(s: dict[str, Any], indent: str = "  ") -> str:
    return (
        f"{indent}{s.get('label','?'):<40}  "
        f"n={s.get('sample_size',0):>4}  "
        f"err={_fmt_f(s.get('mean_projection_error'),2):>6}  "
        f"hit={_fmt_pct(s.get('hit_rate')):>7}  "
        f"roi={_fmt_pct(s.get('roi')):>8}  "
        f"gap={_fmt_f(s.get('calibration_gap'),4):>8}  "
        f"{_severity_badge(s.get('inflation_severity'))}"
    )


def _render_operator_report(payload: dict[str, Any]) -> str:
    date = payload.get("prediction_date", "unknown")
    ov = payload.get("overall", {})
    lines = [
        "Projection Bias Attribution Report (Phase 12B — shadow analytics only)",
        f"prediction_date : {date}",
        "=" * 78,
        f"inflation_severity : {ov.get('inflation_severity', 'n/a')}",
        f"graded_rows        : {ov.get('total_graded_rows', 0)}",
        f"mean_proj_error    : {_fmt_f(ov.get('mean_projection_error'), 2)}  "
        f"(positive = model over-predicted)",
        f"median_proj_error  : {_fmt_f(ov.get('median_projection_error'), 2)}",
        f"over_pred_rate     : {_fmt_pct(ov.get('over_prediction_rate'))}",
        f"hit_rate           : {_fmt_pct(ov.get('hit_rate'))}",
        f"roi                : {_fmt_pct(ov.get('roi'))}",
        f"avg_confidence     : {_fmt_pct(ov.get('avg_confidence'))}",
        f"calibration_gap    : {_fmt_f(ov.get('calibration_gap'), 4)}  "
        f"(actual_hit_rate − mean_confidence; negative = overconfident)",
        "",
    ]

    attr = payload.get("attribution", {})
    header = "  {:<40}  {:>4}  {:>8}  {:>7}  {:>8}  {:>8}".format(
        "segment", "n", "err", "hit", "roi", "gap"
    )
    sep = "  " + "-" * 75

    # By market
    lines += ["Market Attribution", "-" * 78, header, sep]
    mkt = attr.get("by_market", {})
    for k in ("player_points", "player_rebounds", "player_assists", "combos", "other"):
        s = mkt.get(k, {})
        if isinstance(s, dict) and "sample_size" in s:
            lines.append(_render_segment_line(s))
    # Combo breakdown
    lines += ["", "  Combo Breakdown:"]
    for combo, s in mkt.get("combo_breakdown", {}).items():
        if isinstance(s, dict) and "sample_size" in s:
            lines.append(_render_segment_line(s, "    "))
    lines.append("")

    # By direction
    lines += ["Directional Attribution", "-" * 78, header, sep]
    d = attr.get("by_direction", {})
    for side in ("over", "under"):
        s = d.get(side, {})
        if isinstance(s, dict) and "sample_size" in s:
            lines.append(_render_segment_line(s))
    lines.append(f"  error_delta_over_minus_under : {_fmt_f(d.get('error_delta_over_minus_under'), 4)}")
    lines.append("")

    # Confidence buckets
    lines += ["Confidence Bucket Attribution", "-" * 78, header, sep]
    for b in attr.get("by_confidence_bucket", []):
        if isinstance(b, dict):
            lines.append(_render_segment_line(b))
    lines.append("")

    # Edge buckets
    lines += ["Edge Bucket Attribution", "-" * 78, header, sep]
    for b in attr.get("by_edge_bucket", []):
        if isinstance(b, dict):
            lines.append(_render_segment_line(b))
    lines.append("")

    # Context
    lines += ["Context Alignment Attribution", "-" * 78]
    ctx = attr.get("by_context", {})
    if isinstance(ctx, dict) and ctx.get("status") != "insufficient_data":
        if ctx.get("coverage_caution"):
            lines.append(f"  CAUTION: context coverage = {ctx.get('coverage_pct','?')}% (< 80%)")
        lines.append(header)
        lines.append(sep)
        for label in ("aligned", "neutral", "conflicted"):
            s = ctx.get(label, {})
            if isinstance(s, dict) and "sample_size" in s:
                lines.append(_render_segment_line(s))
    else:
        lines.append(f"  status: {ctx.get('status','insufficient_data')}")
    lines.append("")

    # Caution level
    lines += ["Caution Level Attribution", "-" * 78]
    cl = attr.get("by_caution_level", {})
    if isinstance(cl, dict) and cl.get("status") != "insufficient_data":
        if cl.get("coverage_caution"):
            lines.append(f"  CAUTION: caution_level coverage = {cl.get('coverage_pct','?')}% (< 80%)")
        lines.append(header)
        lines.append(sep)
        for label in ("low", "medium", "high"):
            s = cl.get(label, {})
            if isinstance(s, dict) and "sample_size" in s:
                lines.append(_render_segment_line(s))
    else:
        lines.append(f"  status: {cl.get('status','insufficient_data')}")
    lines.append("")

    # Season context
    lines += ["Season Context Attribution", "-" * 78]
    sc = attr.get("by_season_context", {})
    if isinstance(sc, dict) and sc.get("status") != "insufficient_data":
        lines.append(f"  inference: {sc.get('inference_method','?')}")
        lines.append(f"  playoff_rows={sc.get('playoff_rows','?')}  regular_rows={sc.get('regular_rows','?')}")
        lines.append(header)
        lines.append(sep)
        for key in ("playoffs", "regular_season"):
            s = sc.get(key, {})
            if isinstance(s, dict) and "sample_size" in s:
                lines.append(_render_segment_line(s))
    else:
        lines.append(f"  status: {sc.get('status','insufficient_data')}")
    lines.append("")

    # Root-cause diagnostics
    lines += ["Root-Cause Diagnostics", "=" * 78]
    for key, diag in payload.get("root_cause_diagnostics", {}).items():
        if not isinstance(diag, dict):
            continue
        supported = diag.get("supported", None)
        badge = "CONFIRMED" if supported else ("UNCONFIRMED" if supported is False else "N/A")
        lines.append(f"  [{badge}] {key}")
        lines.append(f"           {diag.get('finding','')}")
        me = diag.get("mean_error")
        if me is not None:
            lines.append(f"           mean_error={_fmt_f(me,2)}  severity={diag.get('severity','?')}")
        lines.append("")

    # Top findings
    lines += ["Top Findings Summary", "=" * 78]
    tf = payload.get("top_findings", {})
    lines.append("Worst Calibrated Segments:")
    for s in tf.get("worst_calibrated_segments", []):
        if s:
            lines.append(f"  [{s.get('inflation_severity','?')}]  {s.get('label','?'):<28}  "
                         f"n={s.get('sample_size',0):>4}  err={_fmt_f(s.get('mean_projection_error'),2):>6}  "
                         f"hit={_fmt_pct(s.get('hit_rate')):>7}")
    lines.append("")
    lines.append("Best Calibrated Segments:")
    for s in tf.get("best_calibrated_segments", []):
        if s:
            lines.append(f"  [{s.get('inflation_severity','?')}]  {s.get('label','?'):<28}  "
                         f"n={s.get('sample_size',0):>4}  err={_fmt_f(s.get('mean_projection_error'),2):>6}  "
                         f"hit={_fmt_pct(s.get('hit_rate')):>7}")
    lines.append("")
    lines.append("Most Inflated per Dimension:")
    for key in ("most_inflated_market", "most_inflated_confidence_bucket",
                "most_inflated_edge_bucket", "most_inflated_directional"):
        s = tf.get(key)
        if s:
            lines.append(f"  {key.replace('most_inflated_',''):<20}  "
                         f"{s.get('label','?'):<24}  err={_fmt_f(s.get('mean_projection_error'),2)}")
    lines.append("")
    lines.append(
        "NOTE: Shadow analytics only — no projections, gates, scores, or Kelly stakes changed."
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def attribution_json_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return (
        Path(runtime_root)
        / "diagnostics"
        / f"projection_bias_attribution_{date}.json"
    )


def attribution_txt_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return (
        Path(runtime_root)
        / "operator"
        / f"projection_bias_attribution_{date}.txt"
    )


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def write_projection_bias_attribution(
    shadow_history_df: pd.DataFrame,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    pick_history_df: pd.DataFrame | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Write attribution JSON + operator TXT. Returns (json_path, txt_path, payload).

    Shadow analytics only — no live logic altered.
    """
    payload  = build_projection_bias_attribution(
        shadow_history_df, prediction_date, pick_history_df=pick_history_df
    )
    json_path = attribution_json_path_for_date(prediction_date, runtime_root)
    txt_path  = attribution_txt_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    txt_path.write_text(_render_operator_report(payload), encoding="utf-8")
    return json_path, txt_path, payload


__all__ = [
    "REPORT_VERSION",
    "build_projection_bias_attribution",
    "attribution_json_path_for_date",
    "attribution_txt_path_for_date",
    "write_projection_bias_attribution",
]
