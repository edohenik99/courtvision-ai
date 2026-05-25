"""Feature Completeness Tracker diagnostics and reports.

Tracks whether new forward slates are collecting enough point-in-time features
for future Phase 4C, and estimates when Phase 4C will become ready.
This module does not change prediction logic, Elite gates, Kelly staking,
bankroll sizing, or final decisions.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

REPORT_VERSION = "1.0"
DIAGNOSTIC_ONLY_NOTE = (
    "Feature Completeness Tracker is shadow-only and is not an Elite/Kelly input."
)
FORWARD_FEATURE_START_DATE = "2026-05-24"

CSV_FIELDS = (
    "metric_group",
    "metric_name",
    "value",
    "target_threshold",
    "status",
)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return default if text.lower() in {"nan", "none", "null", "<na>"} else text


def _is_valid_role_stability(value: Any) -> bool:
    val = _safe_text(value).lower().strip()
    return val in {"stable", "mostly_stable", "mixed", "volatile", "highly_volatile"}


def _is_feature_complete(row: pd.Series | dict[str, Any]) -> bool:
    # 1. Context alignment and caution
    align = _safe_text(row.get("context_pick_alignment"))
    caution = _safe_text(row.get("context_caution_level"))
    if not align or not caution:
        return False

    # 2. Fragility/survivability
    frag = _safe_text(row.get("fragility_bucket"))
    surv = _safe_text(row.get("survivability_bucket"))
    if not frag or not surv:
        return False

    # 3. Role stability
    role = _safe_text(row.get("role_stability_bucket"))
    if not _is_valid_role_stability(role):
        return False

    # 4. Meta label rules score
    score = _safe_float(row.get("meta_label_rules_score"))
    if score is None:
        return False

    return True


def _normalize_name(name: Any) -> str:
    val = _safe_text(name).lower().strip()
    # basic normalizations (optional)
    return val


def _join_meta_label_rules_score(
    history_df: pd.DataFrame,
    runtime_root: Path,
) -> pd.DataFrame:
    """Safely joins meta_label_rules_score from prediction-time shadow CSVs for forward dates."""
    working = history_df.copy()
    if working.empty:
        working["meta_label_rules_score"] = pd.Series(dtype=float)
        return working

    # Group by prediction date to load the files efficiently
    forward_mask = working["prediction_date"].fillna("").astype(str) >= FORWARD_FEATURE_START_DATE
    forward_df = working[forward_mask].copy()
    
    if forward_df.empty:
        working["meta_label_rules_score"] = None
        return working

    joined_scores = []
    
    # Pre-load files per date
    promo_cache: dict[str, pd.DataFrame] = {}
    
    for idx, row in working.iterrows():
        dt = _safe_text(row.get("prediction_date"))
        if dt < FORWARD_FEATURE_START_DATE:
            joined_scores.append(None)
            continue
            
        if dt not in promo_cache:
            promo_path = runtime_root / "operator" / f"meta_label_promotion_shadow_{dt}.csv"
            if promo_path.exists():
                try:
                    df_promo = pd.read_csv(promo_path, keep_default_na=False, low_memory=False)
                    # normalize columns in df_promo to look for both sportsbook_line and line
                    if "sportsbook_line" in df_promo.columns and "line" not in df_promo.columns:
                        df_promo["line"] = df_promo["sportsbook_line"]
                    promo_cache[dt] = df_promo
                except Exception:
                    promo_cache[dt] = pd.DataFrame()
            else:
                promo_cache[dt] = pd.DataFrame()
                
        promo_df = promo_cache[dt]
        if promo_df.empty:
            joined_scores.append(None)
            continue
            
        # Extract row join keys
        pid = _safe_text(row.get("player_id"))
        pname = _normalize_name(row.get("player_name"))
        team = _safe_text(row.get("team")).upper()
        market = _safe_text(row.get("market_type"))
        sel = _safe_text(row.get("selection")).lower()
        
        # Get line (handle both sportsbook_line and line keys)
        line = _safe_float(row.get("sportsbook_line"))
        if line is None:
            line = _safe_float(row.get("line"))
            
        # Try joining by: prediction_date, player_id, market_type, selection, line
        # First filter by player_id
        matched = promo_df[
            (promo_df["player_id"].fillna("").astype(str) == pid)
            & (promo_df["market_type"].fillna("").astype(str) == market)
            & (promo_df["selection"].fillna("").astype(str).str.lower() == sel)
        ]
        
        if line is not None:
            # Check line match
            matched_line = matched[
                matched["line"].map(_safe_float) == line
            ]
            if not matched_line.empty:
                val = _safe_float(matched_line.iloc[0].get("meta_label_rules_score"))
                joined_scores.append(val)
                continue
                
        # Try fallback join: normalized player_name, team, market_type, selection, line
        fallback = promo_df[
            (promo_df["player_name"].map(_normalize_name) == pname)
            & (promo_df["team"].fillna("").astype(str).str.upper() == team)
            & (promo_df["market_type"].fillna("").astype(str) == market)
            & (promo_df["selection"].fillna("").astype(str).str.lower() == sel)
        ]
        
        if line is not None:
            fallback_line = fallback[
                fallback["line"].map(_safe_float) == line
            ]
            if not fallback_line.empty:
                val = _safe_float(fallback_line.iloc[0].get("meta_label_rules_score"))
                joined_scores.append(val)
                continue
                
        # If line was None or lines didn't match, fall back to matching without line
        if not matched.empty:
            val = _safe_float(matched.iloc[0].get("meta_label_rules_score"))
            joined_scores.append(val)
        elif not fallback.empty:
            val = _safe_float(fallback.iloc[0].get("meta_label_rules_score"))
            joined_scores.append(val)
        else:
            joined_scores.append(None)
            
    working["meta_label_rules_score"] = joined_scores
    return working


def build_feature_completeness_report(
    prediction_date: str,
    shadow_history_df: pd.DataFrame | None = None,
    full_market_df: pd.DataFrame | None = None,
    meta_promo_df: pd.DataFrame | None = None,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
) -> dict[str, Any]:
    """Build shadow-only forward feature completeness diagnostics and projections."""
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)

    # 1. Current-Date Feature Coverage
    current_metrics = {
        "full_market_rows": 0,
        "rows_with_context_alignment": 0,
        "rows_with_context_caution": 0,
        "rows_with_fragility": 0,
        "rows_with_survivability": 0,
        "rows_with_role_stability": 0,
        "rows_with_meta_label_rules_score": 0,
        "feature_complete_current_rows": 0,
    }

    if full_market_df is not None and not full_market_df.empty:
        working_fm = full_market_df.copy()
        
        # If we have meta_promo_df, join meta_label_rules_score into working_fm
        if meta_promo_df is not None and not meta_promo_df.empty:
            # Let's perform a merge on player_id, market_type, selection, sportsbook_line/line
            # Normalize column names in both
            fm_merge = working_fm.copy()
            promo_merge = meta_promo_df.copy()
            if "sportsbook_line" in fm_merge.columns and "line" not in fm_merge.columns:
                fm_merge["line"] = fm_merge["sportsbook_line"].map(_safe_float)
            if "sportsbook_line" in promo_merge.columns and "line" not in promo_merge.columns:
                promo_merge["line"] = promo_merge["sportsbook_line"].map(_safe_float)
                
            fm_merge["line_flt"] = fm_merge["line"].map(_safe_float)
            promo_merge["line_flt"] = promo_merge["line"].map(_safe_float)
            
            # Keep only the score column
            promo_sub = promo_merge[
                ["player_id", "market_type", "selection", "line_flt", "meta_label_rules_score"]
            ].drop_duplicates(subset=["player_id", "market_type", "selection", "line_flt"])
            
            merged = fm_merge.merge(
                promo_sub,
                on=["player_id", "market_type", "selection", "line_flt"],
                how="left",
            )
            working_fm["meta_label_rules_score"] = merged["meta_label_rules_score"].values

        total_fm = len(working_fm)
        current_metrics["full_market_rows"] = total_fm
        
        if total_fm > 0:
            align_cnt = int(working_fm["context_pick_alignment"].fillna("").astype(str).str.strip().ne("").sum())
            caution_cnt = int(working_fm["context_caution_level"].fillna("").astype(str).str.strip().ne("").sum())
            frag_cnt = int(working_fm["fragility_bucket"].fillna("").astype(str).str.strip().ne("").sum())
            surv_cnt = int(working_fm["survivability_bucket"].fillna("").astype(str).str.strip().ne("").sum())
            
            role_cnt = int(working_fm["role_stability_bucket"].fillna("").map(_is_valid_role_stability).sum())
            score_cnt = int(working_fm.get("meta_label_rules_score", pd.Series([None]*total_fm)).map(_safe_float).notna().sum())
            
            complete_cnt = 0
            for _, row in working_fm.iterrows():
                if _is_feature_complete(row):
                    complete_cnt += 1
                    
            current_metrics.update(
                {
                    "rows_with_context_alignment": align_cnt,
                    "rows_with_context_caution": caution_cnt,
                    "rows_with_fragility": frag_cnt,
                    "rows_with_survivability": surv_cnt,
                    "rows_with_role_stability": role_cnt,
                    "rows_with_meta_label_rules_score": score_cnt,
                    "feature_complete_current_rows": complete_cnt,
                }
            )

    # 2. Historical Forward-Ready Coverage
    if shadow_history_df is None:
        shadow_history_df = pd.DataFrame()
        
    hist_metrics = {
        "completed_slate_count": 0,
        "graded_hit_miss_rows": 0,
        "feature_complete_graded_rows": 0,
        "role_stability_missing_rate": 1.0,
        "fragility_missing_rate": 1.0,
        "survivability_missing_rate": 1.0,
        "context_missing_rate": 1.0,
        "meta_label_rules_score_missing_rate": 1.0,
    }

    if not shadow_history_df.empty:
        # Join rules score from promo reports
        enriched_hist = _join_meta_label_rules_score(shadow_history_df, runtime_root)
        
        # Filter for forward-feature start date
        forward_mask = enriched_hist["prediction_date"].fillna("").astype(str) >= FORWARD_FEATURE_START_DATE
        forward_df = enriched_hist[forward_mask].copy()
        
        # Filter for graded rows only (hit or miss)
        graded_mask = forward_df["result_status"].fillna("").astype(str).str.lower().str.strip().isin({"hit", "miss"})
        graded_df = forward_df[graded_mask].copy()
        
        total_graded = len(graded_df)
        hist_metrics["graded_hit_miss_rows"] = total_graded
        
        if total_graded > 0:
            hist_metrics["completed_slate_count"] = int(graded_df["prediction_date"].nunique())
            
            # calculate missing feature counts on forward graded rows
            missing_role_cnt = int(
                (~graded_df["role_stability_bucket"].fillna("").map(_is_valid_role_stability)).sum()
            )
            missing_frag_cnt = int(
                graded_df["fragility_bucket"].fillna("").astype(str).str.strip().eq("").sum()
            )
            missing_surv_cnt = int(
                graded_df["survivability_bucket"].fillna("").astype(str).str.strip().eq("").sum()
            )
            
            missing_ctx_cnt = int(
                (
                    graded_df["context_pick_alignment"].fillna("").astype(str).str.strip().eq("")
                    | graded_df["context_caution_level"].fillna("").astype(str).str.strip().eq("")
                ).sum()
            )
            
            missing_score_cnt = int(
                graded_df["meta_label_rules_score"].map(_safe_float).isna().sum()
            )
            
            # Feature complete count
            complete_graded = 0
            for _, row in graded_df.iterrows():
                if _is_feature_complete(row):
                    complete_graded += 1
                    
            hist_metrics.update(
                {
                    "feature_complete_graded_rows": complete_graded,
                    "role_stability_missing_rate": round(float(missing_role_cnt) / total_graded, 4),
                    "fragility_missing_rate": round(float(missing_frag_cnt) / total_graded, 4),
                    "survivability_missing_rate": round(float(missing_surv_cnt) / total_graded, 4),
                    "context_missing_rate": round(float(missing_ctx_cnt) / total_graded, 4),
                    "meta_label_rules_score_missing_rate": round(float(missing_score_cnt) / total_graded, 4),
                }
            )

    # 3. Phase 4C Readiness Verdict
    # Thresholds
    min_graded = 1000
    min_complete = 1000
    min_slates = 30
    max_missing = 0.10  # 10%
    
    graded_cnt = hist_metrics["graded_hit_miss_rows"]
    complete_cnt = hist_metrics["feature_complete_graded_rows"]
    slates_cnt = hist_metrics["completed_slate_count"]
    
    rs_miss = hist_metrics["role_stability_missing_rate"]
    frag_miss = hist_metrics["fragility_missing_rate"]
    surv_miss = hist_metrics["survivability_missing_rate"]
    ctx_miss = hist_metrics["context_missing_rate"]
    score_miss = hist_metrics["meta_label_rules_score_missing_rate"]
    
    any_high_missingness = (
        rs_miss >= max_missing
        or frag_miss >= max_missing
        or surv_miss >= max_missing
        or ctx_miss >= max_missing
        or score_miss >= max_missing
    )

    # Verdict priority order:
    # - READY_FOR_PHASE_4C_DESIGN_RECHECK
    # - FEATURE_COLLECTION_HEALTHY_BUT_SAMPLE_SMALL
    # - NEED_FEATURE_BACKFILL_REVIEW
    # - WAIT_MORE_FORWARD_DATA
    if (
        graded_cnt >= min_graded
        and complete_cnt >= min_complete
        and slates_cnt >= min_slates
        and not any_high_missingness
    ):
        verdict = "READY_FOR_PHASE_4C_DESIGN_RECHECK"
    elif not any_high_missingness:
        verdict = "FEATURE_COLLECTION_HEALTHY_BUT_SAMPLE_SMALL"
    elif any_high_missingness and graded_cnt > 0:
        verdict = "NEED_FEATURE_BACKFILL_REVIEW"
    else:
        verdict = "WAIT_MORE_FORWARD_DATA"

    # 4. Projection
    avg_graded_per_slate = (
        float(graded_cnt) / slates_cnt if slates_cnt > 0 else 0.0
    )
    
    if avg_graded_per_slate > 0.0:
        graded_needed = max(0, min_graded - graded_cnt)
        complete_needed = max(0, min_complete - complete_cnt)
        slates_needed_graded = float(graded_needed) / avg_graded_per_slate
        slates_needed_complete = float(complete_needed) / avg_graded_per_slate
        est_slates_needed = int(
            math.ceil(max(slates_needed_graded, slates_needed_complete))
        )
    else:
        est_slates_needed = 999

    readiness = {
        "minimum_required_graded_rows": min_graded,
        "minimum_required_feature_complete_rows": min_complete,
        "minimum_required_completed_slates": min_slates,
        "maximum_allowed_missing_role_stability_rate": max_missing,
        "verdict": verdict,
        "recent_average_graded_rows_per_completed_slate": round(avg_graded_per_slate, 2),
        "estimated_additional_slates_needed": est_slates_needed,
    }

    return {
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "scope": "feature_completeness_tracker",
        "notes": [
            "diagnostic_report_only",
            "no_prediction_logic_changed",
            "no_elite_gates_changed",
            "no_kelly_sizing_changed",
            "no_final_decision_changed",
            DIAGNOSTIC_ONLY_NOTE,
        ],
        "current_coverage": current_metrics,
        "historical_coverage": hist_metrics,
        "readiness": readiness,
    }


def performance_json_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"feature_completeness_tracker_{date}.json"


def performance_txt_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"feature_completeness_tracker_{date}.txt"


def performance_csv_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"feature_completeness_tracker_{date}.csv"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def render_feature_completeness_report(payload: dict[str, Any]) -> str:
    current = payload.get("current_coverage", {})
    hist = payload.get("historical_coverage", {})
    readiness = payload.get("readiness", {})

    lines = [
        "Feature Completeness Tracker - Shadow Only",
        f"prediction_date: {payload.get('prediction_date', '')}",
        "=" * 72,
        "Current Slate Coverage Metrics",
        "-" * 72,
        f"- full market rows: {current.get('full_market_rows', 0)}",
        f"- rows with context alignment: {current.get('rows_with_context_alignment', 0)}",
        f"- rows with context caution: {current.get('rows_with_context_caution', 0)}",
        f"- rows with fragility: {current.get('rows_with_fragility', 0)}",
        f"- rows with survivability: {current.get('rows_with_survivability', 0)}",
        f"- rows with role stability: {current.get('rows_with_role_stability', 0)}",
        f"- rows with meta label rules score: {current.get('rows_with_meta_label_rules_score', 0)}",
        f"- feature-complete current rows: {current.get('feature_complete_current_rows', 0)}",
        "",
        "Historical Forward-Ready Coverage (Since May 24)",
        "-" * 72,
        f"- completed slate count: {hist.get('completed_slate_count', 0)}",
        f"- graded hit/miss rows: {hist.get('graded_hit_miss_rows', 0)}",
        f"- feature-complete graded rows: {hist.get('feature_complete_graded_rows', 0)}",
        f"- role stability missing rate: {_fmt_pct(hist.get('role_stability_missing_rate'))}",
        f"- fragility missing rate: {_fmt_pct(hist.get('fragility_missing_rate'))}",
        f"- survivability missing rate: {_fmt_pct(hist.get('survivability_missing_rate'))}",
        f"- context missing rate: {_fmt_pct(hist.get('context_missing_rate'))}",
        f"- meta label rules score missing rate: {_fmt_pct(hist.get('meta_label_rules_score_missing_rate'))}",
        "",
        "Phase 4C Readiness Status",
        "-" * 72,
        f"- minimum required graded rows: {readiness.get('minimum_required_graded_rows', 1000)}",
        f"- minimum required feature complete rows: {readiness.get('minimum_required_feature_complete_rows', 1000)}",
        f"- minimum required completed slates: {readiness.get('minimum_required_completed_slates', 30)}",
        f"- maximum allowed missing rate: {_fmt_pct(readiness.get('maximum_allowed_missing_role_stability_rate'))}",
        f"- recent avg graded rows per slate: {readiness.get('recent_average_graded_rows_per_completed_slate', 0.0)}",
        f"- estimated additional slates needed: {readiness.get('estimated_additional_slates_needed', 999)}",
        f"- Phase 4C readiness verdict: {readiness.get('verdict', 'WAIT_MORE_FORWARD_DATA')}",
        "",
        DIAGNOSTIC_ONLY_NOTE,
        "",
    ]
    return "\n".join(lines)


def write_feature_completeness_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    shadow_history_df: pd.DataFrame | None = None,
    full_market_df: pd.DataFrame | None = None,
    meta_promo_df: pd.DataFrame | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    """Write forward feature completeness JSON, operator TXT, and operator CSV."""
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)

    if shadow_history_df is None:
        shadow_history_df = pd.read_csv(
            history_root / "market_shadow_history.csv", keep_default_na=False, low_memory=False
        )

    if full_market_df is None:
        fm_path = runtime_root / "operator" / f"full_market_board_{prediction_date}.csv"
        if fm_path.exists():
            full_market_df = pd.read_csv(fm_path, keep_default_na=False, low_memory=False)
        else:
            full_market_df = pd.DataFrame()

    if meta_promo_df is None:
        promo_path = runtime_root / "operator" / f"meta_label_promotion_shadow_{prediction_date}.csv"
        if promo_path.exists():
            meta_promo_df = pd.read_csv(promo_path, keep_default_na=False, low_memory=False)
        else:
            meta_promo_df = pd.DataFrame()

    payload = build_feature_completeness_report(
        prediction_date=prediction_date,
        shadow_history_df=shadow_history_df,
        full_market_df=full_market_df,
        meta_promo_df=meta_promo_df,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    json_path = performance_json_path_for_date(prediction_date, runtime_root)
    txt_path = performance_txt_path_for_date(prediction_date, runtime_root)
    csv_path = performance_csv_path_for_date(prediction_date, runtime_root)

    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    # Write JSON
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )

    # Write TXT
    txt_path.write_text(render_feature_completeness_report(payload), encoding="utf-8")

    # Generate CSV
    csv_rows = []
    
    current = payload.get("current_coverage", {})
    for k, v in sorted(current.items()):
        csv_rows.append(
            {
                "metric_group": "current_coverage",
                "metric_name": k,
                "value": str(v),
                "target_threshold": "n/a",
                "status": "shadow_only",
            }
        )

    hist = payload.get("historical_coverage", {})
    for k, v in sorted(hist.items()):
        csv_rows.append(
            {
                "metric_group": "historical_coverage",
                "metric_name": k,
                "value": str(v),
                "target_threshold": "n/a",
                "status": "shadow_only",
            }
        )

    readiness = payload.get("readiness", {})
    for k, v in sorted(readiness.items()):
        # Try finding threshold
        threshold = "n/a"
        if "graded_rows" in k:
            threshold = "1000"
        elif "feature_complete" in k:
            threshold = "1000"
        elif "completed_slates" in k:
            threshold = "30"
        elif "allowed_missing" in k:
            threshold = "0.10"
            
        csv_rows.append(
            {
                "metric_group": "readiness",
                "metric_name": k,
                "value": str(v),
                "target_threshold": threshold,
                "status": readiness.get("verdict", "WAIT_MORE_FORWARD_DATA"),
            }
        )

    pd.DataFrame(csv_rows, columns=list(CSV_FIELDS)).to_csv(csv_path, index=False)

    return json_path, txt_path, csv_path, payload
