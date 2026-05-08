"""CourtVision Power Rating Shadow Analysis.

Joins graded pick history with per-date Power Rating context diagnostics to
measure whether Power Rating signals (blowout_risk, expected_competitiveness,
rating-difference buckets) correlate with observed hit rates.

Observation-only.  Never modifies pick selection, candidate admission, edge,
confidence, quality_score, Kelly sizing, or stake calculation.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

OBSERVATION_ONLY = True

PICK_HISTORY_REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {"prediction_date", "player_name", "team", "market", "selection", "result_status"}
)

_CONTEXT_POWER_COLUMNS: tuple[str, ...] = (
    "home_team_power_rating",
    "away_team_power_rating",
    "power_rating_diff",
    "home_adjusted_power_rating_diff",
    "home_win_probability",
    "away_win_probability",
    "expected_competitiveness",
    "blowout_risk",
    "team_power_context_applied",
    "team_power_context_missing_reason",
)


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------

def _safe_str(val: Any) -> str:
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    return str(val).strip()


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _unit_return(result_status: str, odds: Any) -> float | None:
    """Return per-unit profit/loss for a single pick given American odds."""
    rs = _safe_str(result_status).lower()
    if rs == "hit":
        o = _safe_float(odds)
        if o is None or o == 0:
            return None
        return 100.0 / abs(o) if o < 0 else o / 100.0
    if rs == "miss":
        return -1.0
    return None


def _rating_diff_bucket(diff: Any) -> str:
    """Bucket home_adjusted_power_rating_diff into 5 coarse zones."""
    v = _safe_float(diff)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "UNKNOWN"
    if v >= 120:
        return "LARGE_HOME_ADV"
    if v >= 60:
        return "MOD_HOME_ADV"
    if v > -60:
        return "COMPETITIVE"
    if v > -120:
        return "MOD_AWAY_ADV"
    return "LARGE_AWAY_ADV"


def _normalize_selection(val: Any) -> str:
    return _safe_str(val).lower()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_pick_history(
    path: str | Path | None = None,
    *,
    dedup_latest_run: bool = True,
) -> pd.DataFrame:
    """Load pick history CSV.

    De-duplicates to the latest run_timestamp per
    (prediction_date, player_name, market, selection) when dedup_latest_run=True
    so that re-runs on the same day don't inflate pick counts.

    Returns an empty DataFrame (with PICK_HISTORY_REQUIRED_COLUMNS) on failure.
    Never raises.
    """
    p = Path(path) if path is not None else Path("data/history/pick_history.csv")
    try:
        if not p.exists():
            return pd.DataFrame(columns=list(PICK_HISTORY_REQUIRED_COLUMNS))
        df = pd.read_csv(p, dtype={"game_id": str})
        if not PICK_HISTORY_REQUIRED_COLUMNS.issubset(set(df.columns)):
            return pd.DataFrame(columns=list(PICK_HISTORY_REQUIRED_COLUMNS))
        if dedup_latest_run and "run_timestamp" in df.columns:
            df = (
                df.sort_values("run_timestamp", ascending=False)
                .drop_duplicates(
                    subset=["prediction_date", "player_name", "market", "selection"],
                    keep="first",
                )
            )
        return df.reset_index(drop=True)
    except Exception:
        return pd.DataFrame(columns=list(PICK_HISTORY_REQUIRED_COLUMNS))


def load_power_rating_context(
    diagnostics_dir: str | Path,
    prediction_date: str,
) -> pd.DataFrame | None:
    """Load power_rating_context_{prediction_date}.csv from diagnostics_dir.

    Returns None if the file does not exist.  Returns an empty DataFrame on
    read errors.  Never raises.
    """
    p = Path(diagnostics_dir) / f"power_rating_context_{prediction_date}.csv"
    if not p.exists():
        return None
    try:
        return pd.read_csv(p, dtype={"game_id": str})
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------

def join_picks_with_context(
    picks_df: pd.DataFrame,
    context_df: pd.DataFrame,
) -> pd.DataFrame:
    """Left-join picks with Power Rating context.

    Join keys (all normalised to lowercase strings):
        player_name  +  team / team_abbr  +  market / market_type  +
        selection / side

    All _CONTEXT_POWER_COLUMNS are added to the result; rows with no match get
    NaN for those columns.  A boolean 'context_available' column is added.
    Never raises.
    """
    result = picks_df.copy()
    for col in _CONTEXT_POWER_COLUMNS:
        result[col] = None
    result["context_available"] = False

    if context_df is None or context_df.empty:
        return result

    if not {"player_name", "team_abbr", "market_type", "side"}.issubset(context_df.columns):
        return result

    try:
        left = result.copy()
        left["_pname"] = left["player_name"].fillna("").astype(str).str.strip()
        left["_team"] = left["team"].fillna("").astype(str).str.strip()
        left["_market"] = left["market"].fillna("").astype(str).str.strip()
        left["_side"] = left["selection"].fillna("").astype(str).str.strip().str.lower()

        ctx = context_df.copy()
        ctx["_pname"] = ctx["player_name"].fillna("").astype(str).str.strip()
        ctx["_team"] = ctx["team_abbr"].fillna("").astype(str).str.strip()
        ctx["_market"] = ctx["market_type"].fillna("").astype(str).str.strip()
        ctx["_side"] = ctx["side"].fillna("").astype(str).str.strip().str.lower()

        ctx_cols = list(_CONTEXT_POWER_COLUMNS)
        ctx_slim = ctx[["_pname", "_team", "_market", "_side"] + [c for c in ctx_cols if c in ctx.columns]].drop_duplicates(
            subset=["_pname", "_team", "_market", "_side"], keep="first"
        )

        merged = left.merge(
            ctx_slim,
            on=["_pname", "_team", "_market", "_side"],
            how="left",
            suffixes=("", "_ctx"),
        )

        for col in _CONTEXT_POWER_COLUMNS:
            ctx_col = f"{col}_ctx" if f"{col}_ctx" in merged.columns else col
            if ctx_col in merged.columns:
                result[col] = merged[ctx_col].values
            elif col in merged.columns:
                result[col] = merged[col].values

        result["context_available"] = merged["team_power_context_applied_ctx" if "team_power_context_applied_ctx" in merged.columns else "team_power_context_applied"].notna().values

    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _group_metrics(df: pd.DataFrame) -> dict[str, Any]:
    """Compute performance metrics for a group of picks."""
    total = len(df)
    if total == 0 or "result_status" not in df.columns:
        return {
            "total_picks": 0, "graded_picks": 0, "hit_count": 0, "miss_count": 0,
            "void_count": 0, "pending_count": 0, "unsupported_count": 0,
            "hit_rate": None, "unit_roi_per_pick": None,
            "avg_edge": None, "avg_confidence": None, "avg_quality_score": None,
        }
    graded = df[df["result_status"].isin(["hit", "miss"])]
    n_graded = len(graded)
    n_hit = int((graded["result_status"] == "hit").sum())
    n_miss = int((graded["result_status"] == "miss").sum())
    n_void = int((df["result_status"] == "void").sum()) if "result_status" in df.columns else 0
    n_pending = int((df["result_status"] == "pending").sum()) if "result_status" in df.columns else 0
    n_unsupported = total - n_graded - n_void - n_pending

    hit_rate = round(n_hit / n_graded, 4) if n_graded > 0 else None

    unit_roi = None
    if "odds" in graded.columns and n_graded > 0:
        returns = [_unit_return(row["result_status"], row.get("odds")) for _, row in graded.iterrows()]
        valid = [r for r in returns if r is not None]
        if valid:
            unit_roi = round(sum(valid) / len(valid), 4)

    avg_edge = None
    avg_conf = None
    avg_qs = None
    if n_graded > 0:
        if "edge" in graded.columns:
            avg_edge = _safe_float(graded["edge"].mean())
            avg_edge = round(avg_edge, 4) if avg_edge is not None else None
        if "confidence" in graded.columns:
            avg_conf = _safe_float(graded["confidence"].mean())
            avg_conf = round(avg_conf, 4) if avg_conf is not None else None
        if "quality_score" in graded.columns:
            avg_qs = _safe_float(graded["quality_score"].mean())
            avg_qs = round(avg_qs, 4) if avg_qs is not None else None

    return {
        "total_picks": total,
        "graded_picks": n_graded,
        "hit_count": n_hit,
        "miss_count": n_miss,
        "void_count": n_void,
        "pending_count": n_pending,
        "unsupported_count": max(n_unsupported, 0),
        "hit_rate": hit_rate,
        "unit_roi_per_pick": unit_roi,
        "avg_edge": avg_edge,
        "avg_confidence": avg_conf,
        "avg_quality_score": avg_qs,
    }


def _metrics_by_group(df: pd.DataFrame, col: str) -> dict[str, dict[str, Any]]:
    """Return _group_metrics for each unique value of col."""
    if col not in df.columns:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for val, group in df.groupby(col, dropna=False):
        key = _safe_str(val) or "UNKNOWN"
        result[key] = _group_metrics(group)
    return result


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def build_shadow_analysis(
    prediction_dates: list[str] | None,
    pick_history_path: str | Path | None,
    diagnostics_dir: str | Path,
) -> dict[str, Any]:
    """Join graded picks with Power Rating context and report performance by dimension.

    Args:
        prediction_dates: Dates to include.  None means all dates in pick_history.
        pick_history_path: Path to pick_history.csv.
        diagnostics_dir: Directory containing power_rating_context_YYYY-MM-DD.csv files.

    Returns:
        Analysis dict with overall metrics and breakdowns by blowout_risk,
        expected_competitiveness, rating-diff bucket, market, and side.
        Always includes observation_only=True.
    """
    picks = load_pick_history(pick_history_path)
    empty_result: dict[str, Any] = {
        "observation_only": OBSERVATION_ONLY,
        "note": "",
        "overall": _group_metrics(pd.DataFrame(columns=list(PICK_HISTORY_REQUIRED_COLUMNS))),
    }

    if picks.empty:
        empty_result["note"] = "No pick history available"
        return empty_result

    if prediction_dates:
        picks = picks[picks["prediction_date"].isin(prediction_dates)].copy()

    if picks.empty:
        empty_result["note"] = "No picks for the requested dates"
        return empty_result

    dates_in_picks = sorted(picks["prediction_date"].dropna().unique().tolist())
    diag_dir = Path(diagnostics_dir)

    joined_frames: list[pd.DataFrame] = []
    dates_with_context: list[str] = []
    dates_without_context: list[str] = []

    for date in dates_in_picks:
        day_picks = picks[picks["prediction_date"] == date].copy()
        ctx = load_power_rating_context(diag_dir, date)
        if ctx is None:
            for col in _CONTEXT_POWER_COLUMNS:
                day_picks[col] = None
            day_picks["context_available"] = False
            dates_without_context.append(date)
        else:
            day_picks = join_picks_with_context(day_picks, ctx)
            dates_with_context.append(date)
        joined_frames.append(day_picks)

    non_empty = [f for f in joined_frames if not f.empty]
    combined = pd.concat(non_empty, ignore_index=True) if non_empty else pd.DataFrame()

    combined["_adj_diff_bucket"] = combined["home_adjusted_power_rating_diff"].map(_rating_diff_bucket)
    combined["_raw_diff_bucket"] = combined["power_rating_diff"].map(_rating_diff_bucket)

    context_joined = int(combined["context_available"].sum()) if "context_available" in combined.columns else 0
    context_missing = len(combined) - context_joined

    return {
        "observation_only": OBSERVATION_ONLY,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prediction_dates_analyzed": dates_in_picks,
        "dates_with_context": dates_with_context,
        "dates_without_context": dates_without_context,
        "context_joined_count": context_joined,
        "context_missing_count": context_missing,
        "overall": _group_metrics(combined),
        "by_blowout_risk": _metrics_by_group(combined, "blowout_risk"),
        "by_expected_competitiveness": _metrics_by_group(combined, "expected_competitiveness"),
        "by_home_adjusted_diff_bucket": _metrics_by_group(combined, "_adj_diff_bucket"),
        "by_raw_diff_bucket": _metrics_by_group(combined, "_raw_diff_bucket"),
        "by_market_type": _metrics_by_group(combined, "market"),
        "by_side": _metrics_by_group(combined, "selection"),
    }, combined


def write_shadow_outputs(
    prediction_dates: list[str] | None,
    pick_history_path: str | Path | None,
    diagnostics_dir: str | Path,
    analysis_date: str,
) -> dict[str, Any]:
    """Run shadow analysis and write row-level CSV + summary JSON to diagnostics_dir.

    Returns a summary dict with paths and key metrics.  Never raises.
    """
    diag_dir = Path(diagnostics_dir)
    csv_path = diag_dir / f"power_rating_shadow_{analysis_date}.csv"
    json_path = diag_dir / f"power_rating_shadow_summary_{analysis_date}.json"

    try:
        result = build_shadow_analysis(prediction_dates, pick_history_path, diagnostics_dir)
        if isinstance(result, tuple):
            analysis, combined = result
        else:
            analysis = result
            combined = pd.DataFrame()

        diag_dir.mkdir(parents=True, exist_ok=True)

        if not combined.empty:
            drop_cols = [c for c in combined.columns if c.startswith("_")]
            combined.drop(columns=drop_cols, errors="ignore").to_csv(csv_path, index=False)

        import json
        json_path.write_text(json.dumps(analysis, indent=2, default=str), encoding="utf-8")

        overall = analysis.get("overall", {})
        return {
            "csv_path": str(csv_path) if not combined.empty else None,
            "json_path": str(json_path),
            "total_picks": overall.get("total_picks", 0),
            "graded_picks": overall.get("graded_picks", 0),
            "hit_rate": overall.get("hit_rate"),
            "context_joined_count": analysis.get("context_joined_count", 0),
            "dates_analyzed": len(analysis.get("prediction_dates_analyzed", [])),
            "observation_only": OBSERVATION_ONLY,
        }
    except Exception as exc:
        return {
            "csv_path": None,
            "json_path": None,
            "error": str(exc),
            "observation_only": OBSERVATION_ONLY,
        }


__all__ = [
    "OBSERVATION_ONLY",
    "PICK_HISTORY_REQUIRED_COLUMNS",
    "build_shadow_analysis",
    "join_picks_with_context",
    "load_pick_history",
    "load_power_rating_context",
    "write_shadow_outputs",
]
