"""
Phase 9: Fragility/Survivability Shadow Evaluation Report.

Reporting and analysis only — no scoring, selection, or staking changes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


SHADOW_EVAL_REPORT_VERSION = "1.0"
_GRADED_STATUSES = {"hit", "miss", "push"}

_NOT_READY_THRESHOLD = 100
_PROMISING_THRESHOLD = 300
_MEANINGFUL_LIFT_MIN = 0.02


def _safe_mean(col: str, seg: pd.DataFrame) -> float | None:
    if col not in seg.columns:
        return None
    vals = pd.to_numeric(seg[col], errors="coerce").dropna()
    if vals.empty:
        return None
    return round(float(vals.mean()), 4)


def _hit_rate(seg: pd.DataFrame) -> float:
    if seg.empty:
        return 0.0
    return float((seg["result_status"].astype(str).str.lower() == "hit").mean())


def _bucket_stats(
    graded: pd.DataFrame,
    bucket_col: str,
    baseline_hit_rate: float,
) -> list[dict[str, Any]]:
    if bucket_col not in graded.columns:
        return []
    out = []
    for bucket, seg in graded.groupby(graded[bucket_col].astype(str).str.upper()):
        if not bucket or bucket in {"NAN", "NONE"}:
            continue
        hr = _hit_rate(seg)
        row: dict[str, Any] = {
            "bucket": bucket,
            "rows": int(len(seg)),
            "hit_rate": round(hr, 4),
            "hit_rate_vs_baseline": round(hr - baseline_hit_rate, 4),
            "avg_edge": _safe_mean("edge", seg),
            "avg_confidence": _safe_mean("confidence", seg),
            "avg_quality_score": _safe_mean("quality_score", seg),
        }
        if "profit_loss" in seg.columns:
            pl = pd.to_numeric(seg["profit_loss"], errors="coerce").dropna()
            if not pl.empty:
                row["total_profit_loss"] = round(float(pl.sum()), 4)
                row["avg_profit_loss"] = round(float(pl.mean()), 4)
                if "stake_amount" in seg.columns:
                    stakes = pd.to_numeric(seg["stake_amount"], errors="coerce").dropna()
                    total_stake = float(stakes.sum())
                    if total_stake > 0:
                        row["roi"] = round(float(pl.sum()) / total_stake, 4)
        out.append(row)
    return out


def _comparison_bucket_stats(
    graded: pd.DataFrame,
    bucket_col: str,
    baseline_hit_rate: float,
) -> list[dict[str, Any]]:
    if bucket_col not in graded.columns:
        return []
    out = []
    for bucket, seg in graded.groupby(graded[bucket_col].astype(str)):
        if not bucket or bucket.lower() in {"nan", "none", ""}:
            continue
        hr = _hit_rate(seg)
        out.append({
            "bucket": str(bucket),
            "rows": int(len(seg)),
            "hit_rate": round(hr, 4),
            "hit_rate_vs_baseline": round(hr - baseline_hit_rate, 4),
            "avg_confidence": _safe_mean("confidence", seg),
            "avg_quality_score": _safe_mean("quality_score", seg),
        })
    return out


def _add_comparison_buckets(graded: pd.DataFrame) -> pd.DataFrame:
    if "confidence" in graded.columns:
        conf = pd.to_numeric(graded["confidence"], errors="coerce")
        graded["_conf_bucket"] = pd.cut(
            conf,
            bins=[0.0, 0.55, 0.65, 0.75, 1.01],
            labels=["<0.55", "0.55-0.65", "0.65-0.75", "0.75+"],
            right=False,
        ).astype(str)
    if "quality_score" in graded.columns:
        qs = pd.to_numeric(graded["quality_score"], errors="coerce")
        graded["_qual_bucket"] = pd.cut(
            qs,
            bins=[0.0, 55.0, 65.0, 75.0, 101.0],
            labels=["<55", "55-65", "65-75", "75+"],
            right=False,
        ).astype(str)
    if "edge" in graded.columns:
        edge = pd.to_numeric(graded["edge"], errors="coerce")
        graded["_edge_bucket"] = pd.cut(
            edge,
            bins=[-1e9, -1.0, 0.0, 1.0, 1e9],
            labels=["<-1", "-1to0", "0to1", "1+"],
        ).astype(str)
    return graded


def _compute_predictive_lift(graded: pd.DataFrame, baseline_hit_rate: float) -> dict[str, Any]:
    lift: dict[str, Any] = {}
    if "fragility_bucket" in graded.columns:
        for label in ("HIGH", "LOW", "MEDIUM"):
            seg = graded[graded["fragility_bucket"].astype(str).str.upper() == label]
            if not seg.empty:
                hr = _hit_rate(seg)
                lift[f"{label.lower()}_fragility_hit_rate"] = round(hr, 4)
                lift[f"{label.lower()}_fragility_vs_baseline"] = round(hr - baseline_hit_rate, 4)
                lift[f"{label.lower()}_fragility_rows"] = int(len(seg))
    if "survivability_bucket" in graded.columns:
        for label in ("HIGH", "LOW", "MEDIUM"):
            seg = graded[graded["survivability_bucket"].astype(str).str.upper() == label]
            if not seg.empty:
                hr = _hit_rate(seg)
                lift[f"{label.lower()}_survivability_hit_rate"] = round(hr, 4)
                lift[f"{label.lower()}_survivability_vs_baseline"] = round(hr - baseline_hit_rate, 4)
                lift[f"{label.lower()}_survivability_rows"] = int(len(seg))
    return lift


def _has_directional_lift(lift: dict[str, Any]) -> bool:
    hf_vs = lift.get("high_fragility_vs_baseline")
    lf_vs = lift.get("low_fragility_vs_baseline")
    hs_vs = lift.get("high_survivability_vs_baseline")
    ls_vs = lift.get("low_survivability_vs_baseline")
    return (
        (hf_vs is not None and hf_vs < -_MEANINGFUL_LIFT_MIN)
        or (lf_vs is not None and lf_vs > _MEANINGFUL_LIFT_MIN)
        or (hs_vs is not None and hs_vs > _MEANINGFUL_LIFT_MIN)
        or (ls_vs is not None and ls_vs < -_MEANINGFUL_LIFT_MIN)
    )


def _has_consistent_meaningful_lift(lift: dict[str, Any]) -> bool:
    frag_signal = (
        (lift.get("high_fragility_vs_baseline") or 0.0) < -_MEANINGFUL_LIFT_MIN
        and (lift.get("low_fragility_vs_baseline") or 0.0) > _MEANINGFUL_LIFT_MIN
    )
    surv_signal = (
        (lift.get("high_survivability_vs_baseline") or 0.0) > _MEANINGFUL_LIFT_MIN
        and (lift.get("low_survivability_vs_baseline") or 0.0) < -_MEANINGFUL_LIFT_MIN
    )
    return frag_signal or surv_signal


def _compute_conflict_analysis(graded: pd.DataFrame) -> dict[str, Any]:
    conflicts: dict[str, Any] = {}
    has_frag = "fragility_bucket" in graded.columns
    has_surv = "survivability_bucket" in graded.columns

    frag_high = (
        graded["fragility_bucket"].astype(str).str.upper() == "HIGH"
        if has_frag else pd.Series(False, index=graded.index)
    )
    surv_high = (
        graded["survivability_bucket"].astype(str).str.upper() == "HIGH"
        if has_surv else pd.Series(False, index=graded.index)
    )
    is_hit = graded["result_status"].astype(str).str.lower() == "hit"
    is_miss = graded["result_status"].astype(str).str.lower() == "miss"

    if "confidence" in graded.columns:
        high_conf = pd.to_numeric(graded["confidence"], errors="coerce") >= 0.7
        conflicts["high_confidence_high_fragility_rows"] = int((high_conf & frag_high).sum())
        conflicts["high_confidence_high_fragility_hits"] = int((high_conf & frag_high & is_hit).sum())
        conflicts["high_confidence_high_fragility_misses"] = int((high_conf & frag_high & is_miss).sum())

    if "quality_score" in graded.columns:
        high_qual = pd.to_numeric(graded["quality_score"], errors="coerce") >= 70
        conflicts["high_quality_high_fragility_rows"] = int((high_qual & frag_high).sum())
        conflicts["high_quality_high_fragility_hits"] = int((high_qual & frag_high & is_hit).sum())
        conflicts["high_quality_high_fragility_misses"] = int((high_qual & frag_high & is_miss).sum())

    if "edge" in graded.columns:
        strong_edge = pd.to_numeric(graded["edge"], errors="coerce") > 1.0
        conflicts["strong_edge_high_fragility_rows"] = int((strong_edge & frag_high).sum())
        conflicts["strong_edge_high_fragility_hits"] = int((strong_edge & frag_high & is_hit).sum())
        conflicts["strong_edge_high_fragility_misses"] = int((strong_edge & frag_high & is_miss).sum())

    if has_surv:
        conflicts["high_survivability_miss_rows"] = int((surv_high & is_miss).sum())

    if has_frag:
        conflicts["high_fragility_win_rows"] = int((frag_high & is_hit).sum())

    return conflicts


def _compute_readiness_verdict(graded_with_diag: int, lift: dict[str, Any]) -> str:
    if graded_with_diag < _NOT_READY_THRESHOLD:
        return "not_ready_insufficient_sample"
    if graded_with_diag < _PROMISING_THRESHOLD:
        if _has_directional_lift(lift):
            return "promising_needs_more_sample"
        return "reject_signal_no_lift"
    if _has_consistent_meaningful_lift(lift):
        return "ready_for_shadow_policy_design"
    return "reject_signal_no_lift"


def build_shadow_eval_report(history_df: pd.DataFrame, prediction_date: str) -> dict[str, Any]:
    """Build the Phase 9 shadow evaluation report from history DataFrame.

    Reporting only — no scoring, selection, or staking values are altered.
    """
    total_historical = int(len(history_df))

    if history_df.empty:
        return {
            "report_version": SHADOW_EVAL_REPORT_VERSION,
            "prediction_date": prediction_date,
            "diagnostic_coverage": {
                "total_historical_rows": 0,
                "total_graded_rows": 0,
                "rows_with_fragility_diagnostics": 0,
                "rows_with_survivability_diagnostics": 0,
                "graded_rows_with_diagnostics": 0,
                "diagnostics_coverage_pct": 0.0,
            },
            "readiness_verdict": "not_ready_insufficient_sample",
            "notes": ["history_empty"],
        }

    status_col = (
        history_df.get("result_status", pd.Series("", index=history_df.index))
        .astype(str)
        .str.lower()
    )
    graded = history_df[status_col.isin(_GRADED_STATUSES)].copy()

    _valid_buckets = {"HIGH", "MEDIUM", "LOW"}

    def _count_valid_buckets(df: pd.DataFrame, col: str) -> int:
        if col not in df.columns:
            return 0
        return int(df[col].astype(str).str.strip().str.upper().isin(_valid_buckets).sum())

    rows_with_frag = _count_valid_buckets(history_df, "fragility_bucket")
    rows_with_surv = _count_valid_buckets(history_df, "survivability_bucket")
    total_graded = int(len(graded))
    graded_with_diag = _count_valid_buckets(graded, "fragility_bucket") if not graded.empty else 0
    coverage_pct = round(graded_with_diag / total_graded * 100, 2) if total_graded > 0 else 0.0

    diagnostic_coverage: dict[str, Any] = {
        "total_historical_rows": total_historical,
        "total_graded_rows": total_graded,
        "rows_with_fragility_diagnostics": rows_with_frag,
        "rows_with_survivability_diagnostics": rows_with_surv,
        "graded_rows_with_diagnostics": graded_with_diag,
        "diagnostics_coverage_pct": coverage_pct,
    }

    if graded.empty:
        return {
            "report_version": SHADOW_EVAL_REPORT_VERSION,
            "prediction_date": prediction_date,
            "diagnostic_coverage": diagnostic_coverage,
            "readiness_verdict": "not_ready_insufficient_sample",
            "notes": ["no_graded_rows"],
        }

    baseline_hit_rate = round(_hit_rate(graded), 4)

    # 2. Bucket outcome analysis
    bucket_outcome_analysis: dict[str, Any] = {
        "by_fragility_bucket": _bucket_stats(graded, "fragility_bucket", baseline_hit_rate),
        "by_survivability_bucket": _bucket_stats(graded, "survivability_bucket", baseline_hit_rate),
    }

    # 3. Baseline comparison — add temp columns to graded copy only
    graded = _add_comparison_buckets(graded)

    selection_buckets: list[dict[str, Any]] = []
    if "selection" in graded.columns:
        for sel, seg in graded.groupby(graded["selection"].astype(str).str.upper()):
            if not sel or sel in {"NAN", "NONE"}:
                continue
            hr = _hit_rate(seg)
            selection_buckets.append({
                "selection": sel,
                "rows": int(len(seg)),
                "hit_rate": round(hr, 4),
                "hit_rate_vs_baseline": round(hr - baseline_hit_rate, 4),
            })

    elite_comparison: list[dict[str, Any]] = []
    if "is_elite" in graded.columns:
        elite_mask = graded["is_elite"].astype(str).str.lower().isin({"true", "1", "yes", "y"})
        for label, mask in [("elite", elite_mask), ("non_elite", ~elite_mask)]:
            seg = graded[mask]
            if not seg.empty:
                hr = _hit_rate(seg)
                elite_comparison.append({
                    "group": label,
                    "rows": int(len(seg)),
                    "hit_rate": round(hr, 4),
                    "hit_rate_vs_baseline": round(hr - baseline_hit_rate, 4),
                })

    baseline_comparison: dict[str, Any] = {
        "global_baseline_hit_rate": baseline_hit_rate,
        "global_graded_rows": total_graded,
        "by_confidence_bucket": _comparison_bucket_stats(graded, "_conf_bucket", baseline_hit_rate),
        "by_quality_bucket": _comparison_bucket_stats(graded, "_qual_bucket", baseline_hit_rate),
        "by_edge_bucket": _comparison_bucket_stats(graded, "_edge_bucket", baseline_hit_rate),
        "by_market_type": _comparison_bucket_stats(graded, "market_type", baseline_hit_rate),
        "by_selection": selection_buckets,
        "by_elite_vs_full_market": elite_comparison,
    }

    # 4. Predictive lift
    lift = _compute_predictive_lift(graded, baseline_hit_rate)

    # 5. Conflict analysis
    conflict_analysis = _compute_conflict_analysis(graded)

    # 6. Readiness verdict
    readiness_verdict = _compute_readiness_verdict(graded_with_diag, lift)

    return {
        "report_version": SHADOW_EVAL_REPORT_VERSION,
        "prediction_date": prediction_date,
        "diagnostic_coverage": diagnostic_coverage,
        "bucket_outcome_analysis": bucket_outcome_analysis,
        "baseline_comparison": baseline_comparison,
        "predictive_lift": lift,
        "conflict_analysis": conflict_analysis,
        "readiness_verdict": readiness_verdict,
        "notes": [
            "diagnostics_non_operational",
            "reporting_analysis_only",
            f"graded_with_diag={graded_with_diag}",
        ],
    }


def shadow_eval_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"fragility_survivability_shadow_eval_{date}.json"


def write_shadow_eval_report(
    history_df: pd.DataFrame,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, dict[str, Any]]:
    """Write shadow eval JSON and operator TXT. Returns (json_path, payload).

    Reporting only — no scoring, selection, or staking values are altered.
    """
    payload = build_shadow_eval_report(history_df, prediction_date)
    json_path = shadow_eval_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    txt_path = (
        Path(runtime_root) / "operator"
        / f"fragility_survivability_shadow_eval_{prediction_date}.txt"
    )
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    cov = payload.get("diagnostic_coverage", {})
    txt_path.write_text(
        "Fragility/Survivability Shadow Evaluation (Phase 9 — diagnostics only)\n"
        f"prediction_date: {prediction_date}\n"
        f"readiness_verdict: {payload.get('readiness_verdict', 'not_ready_insufficient_sample')}\n"
        f"graded_rows_with_diagnostics: {cov.get('graded_rows_with_diagnostics', 0)}\n"
        f"coverage_pct: {cov.get('diagnostics_coverage_pct', 0.0)}%\n"
        "NOTE: Diagnostics are non-operational. No scoring or selection changes.\n",
        encoding="utf-8",
    )

    return json_path, payload


__all__ = [
    "SHADOW_EVAL_REPORT_VERSION",
    "build_shadow_eval_report",
    "shadow_eval_path_for_date",
    "write_shadow_eval_report",
]
