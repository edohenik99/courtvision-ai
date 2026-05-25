"""Meta-Label Rules Performance Tracking diagnostics and reports.

Shadow-only performance analytics comparing Phase 4B rules buckets against
historical outcomes where data exists.
This module does not change projections, selection logic, Elite gates, Kelly
sizing, or final decisions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from courtvision.context.meta_label_promotion import (
    DIAGNOSTIC_ONLY_NOTE as META_LABEL_DIAGNOSTIC_ONLY_NOTE,
    apply_meta_label_promotion,
)

REPORT_VERSION = "1.0"
DIAGNOSTIC_ONLY_NOTE = (
    "Meta-Label Rules Performance is shadow-only and is not an Elite/Kelly input."
)

CSV_FIELDS = (
    "group_type",
    "group_name",
    "total_rows",
    "graded_rows",
    "hits",
    "misses",
    "pushes",
    "voids",
    "pending",
    "hit_rate",
    "sample_status",
)


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
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


def _aggregate_outcomes(df: pd.DataFrame, group_col: str | None = None, filter_fn: Any | None = None) -> dict[str, Any]:
    """Helper to aggregate hits, misses, hit rates for a given sub-segment."""
    working = df.copy()
    if filter_fn is not None:
        working = working[working.apply(filter_fn, axis=1)]

    total_rows = len(working)
    if total_rows == 0:
        return {
            "total_rows": 0,
            "graded_rows": 0,
            "hits": 0,
            "misses": 0,
            "pushes": 0,
            "voids": 0,
            "pending": 0,
            "hit_rate": None,
            "sample_status": "insufficient",
        }

    statuses = working["result_status"].fillna("").astype(str).str.lower().str.strip()
    
    hits = int(statuses.eq("hit").sum())
    misses = int(statuses.eq("miss").sum())
    pushes = int(statuses.eq("push").sum())
    voids = int(statuses.eq("void").sum())
    pending = int(statuses.eq("pending").sum())
    
    graded_rows = hits + misses
    hit_rate = float(hits) / graded_rows if graded_rows > 0 else None
    sample_status = "sufficient" if graded_rows >= 30 else "insufficient"

    return {
        "total_rows": total_rows,
        "graded_rows": graded_rows,
        "hits": hits,
        "misses": misses,
        "pushes": pushes,
        "voids": voids,
        "pending": pending,
        "hit_rate": hit_rate,
        "sample_status": sample_status,
    }


def build_rules_performance_report(
    prediction_date: str,
    shadow_history_df: pd.DataFrame | None = None,
    pick_history_df: pd.DataFrame | None = None,
    current_meta_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build rules baseline performance analytics against historical outcomes."""
    if shadow_history_df is None or shadow_history_df.empty:
        return {
            "report_version": REPORT_VERSION,
            "prediction_date": prediction_date,
            "scope": "meta_label_rules_performance",
            "notes": [
                "diagnostic_report_only",
                "no_prediction_logic_changed",
                "no_elite_gates_changed",
                "no_kelly_sizing_changed",
                "no_final_decision_changed",
                DIAGNOSTIC_ONLY_NOTE,
            ],
            "data_readiness": {
                "completed_slate_count": 0,
                "graded_hit_miss_rows": 0,
                "minimum_sample_threshold_status": "insufficient",
                "missing_role_stability_rate": 0.0,
                "missing_fragility_rate": 0.0,
                "verdict": "WAIT_MORE_DATA",
            },
            "buckets": {},
            "score_bands": {},
            "markets": {},
            "sides": {},
        }

    # Enrich shadow history DataFrame using Phase 4B scoring
    enriched = apply_meta_label_promotion(shadow_history_df)

    # 1. Bucket outcome summaries
    buckets_to_track = [
        "shadow_strong_review_candidate",
        "shadow_watch_candidate",
        "shadow_neutral",
        "shadow_weak",
        "shadow_avoid_review",
    ]
    bucket_outcomes = {}
    for bucket in buckets_to_track:
        bucket_outcomes[bucket] = _aggregate_outcomes(
            enriched,
            filter_fn=lambda row: row.get("meta_label_bucket") == bucket
        )

    # 2. Score band outcome summaries
    score_bands = {
        "80-100": lambda row: _safe_float(row.get("meta_label_rules_score")) is not None and _safe_float(row.get("meta_label_rules_score")) >= 80.0,
        "65-79": lambda row: _safe_float(row.get("meta_label_rules_score")) is not None and 65.0 <= _safe_float(row.get("meta_label_rules_score")) < 80.0,
        "50-64": lambda row: _safe_float(row.get("meta_label_rules_score")) is not None and 50.0 <= _safe_float(row.get("meta_label_rules_score")) < 65.0,
        "35-49": lambda row: _safe_float(row.get("meta_label_rules_score")) is not None and 35.0 <= _safe_float(row.get("meta_label_rules_score")) < 50.0,
        "0-34": lambda row: _safe_float(row.get("meta_label_rules_score")) is not None and _safe_float(row.get("meta_label_rules_score")) < 35.0,
    }
    band_outcomes = {}
    for band_name, filter_fn in score_bands.items():
        band_outcomes[band_name] = _aggregate_outcomes(enriched, filter_fn=filter_fn)

    # 3. Market breakdowns
    markets_to_track = {
        "player_points": lambda row: _safe_text(row.get("market_type")) == "player_points",
        "player_rebounds": lambda row: _safe_text(row.get("market_type")) == "player_rebounds",
        "player_assists": lambda row: _safe_text(row.get("market_type")) == "player_assists",
        "combo_markets": lambda row: "_" in _safe_text(row.get("market_type")) and _safe_text(row.get("market_type")) != "player_points",
    }
    market_outcomes = {}
    for market_name, filter_fn in markets_to_track.items():
        market_outcomes[market_name] = _aggregate_outcomes(enriched, filter_fn=filter_fn)

    # 4. Side breakdowns
    sides_to_track = {
        "over": lambda row: _safe_text(row.get("selection")).lower() == "over",
        "under": lambda row: _safe_text(row.get("selection")).lower() == "under",
    }
    side_outcomes = {}
    for side_name, filter_fn in sides_to_track.items():
        side_outcomes[side_name] = _aggregate_outcomes(enriched, filter_fn=filter_fn)

    # 5. Data Readiness Calculations
    completed_slate_count = int(shadow_history_df["prediction_date"].fillna("").astype(str).str.strip().nunique())
    
    # Calculate graded hits and misses in shadow history
    graded_hist = shadow_history_df[
        shadow_history_df["result_status"].fillna("").astype(str).str.lower().str.strip().isin({"hit", "miss"})
    ]
    graded_hit_miss_rows = int(len(graded_hist))
    min_sample_status = "satisfied" if graded_hit_miss_rows >= 1000 else "insufficient"

    # Calculate missing feature rates
    # missing role stability: count rows in enriched where role stability bucket is unknown or missing warnings
    missing_role_stability_count = int(
        enriched["role_stability_bucket"].fillna("").astype(str).str.lower().eq("unknown").sum()
    )
    missing_role_stability_rate = (
        float(missing_role_stability_count) / len(enriched) if len(enriched) > 0 else 0.0
    )

    # missing fragility: count rows in enriched where fragility_bucket is empty, NaN, or not LOW/MEDIUM/HIGH
    missing_fragility_count = int(
        enriched["fragility_bucket"].fillna("").astype(str).str.strip().eq("").sum()
    )
    missing_fragility_rate = (
        float(missing_fragility_count) / len(enriched) if len(enriched) > 0 else 0.0
    )

    # 6. Recommendation Verdict logic
    # - WAIT_MORE_DATA: graded < 1000
    # - NEED_FEATURE_BACKFILL: graded >= 1000 but missing rates >= 30%
    # - RULES_BASELINE_UNPROVEN: strong bucket hit rate <= avoid bucket hit rate (with sufficient sample)
    # - READY_FOR_PHASE_4C: otherwise
    strong_rate = bucket_outcomes["shadow_strong_review_candidate"]["hit_rate"]
    avoid_rate = bucket_outcomes["shadow_avoid_review"]["hit_rate"]
    
    if graded_hit_miss_rows < 1000:
        verdict = "WAIT_MORE_DATA"
    elif missing_role_stability_rate >= 0.30 or missing_fragility_rate >= 0.30:
        verdict = "NEED_FEATURE_BACKFILL"
    elif (
        strong_rate is not None
        and avoid_rate is not None
        and strong_rate <= avoid_rate
        and bucket_outcomes["shadow_strong_review_candidate"]["graded_rows"] >= 10
    ):
        verdict = "RULES_BASELINE_UNPROVEN"
    else:
        verdict = "READY_FOR_PHASE_4C"

    data_readiness = {
        "completed_slate_count": completed_slate_count,
        "graded_hit_miss_rows": graded_hit_miss_rows,
        "minimum_sample_threshold_status": min_sample_status,
        "missing_role_stability_rate": round(missing_role_stability_rate, 4),
        "missing_fragility_rate": round(missing_fragility_rate, 4),
        "verdict": verdict,
    }

    return {
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "scope": "meta_label_rules_performance",
        "notes": [
            "diagnostic_report_only",
            "no_prediction_logic_changed",
            "no_elite_gates_changed",
            "no_kelly_sizing_changed",
            "no_final_decision_changed",
            DIAGNOSTIC_ONLY_NOTE,
        ],
        "data_readiness": data_readiness,
        "buckets": bucket_outcomes,
        "score_bands": band_outcomes,
        "markets": market_outcomes,
        "sides": side_outcomes,
    }


def performance_json_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"meta_label_rules_performance_{date}.json"


def performance_txt_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"meta_label_rules_performance_{date}.txt"


def performance_csv_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"meta_label_rules_performance_{date}.csv"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def render_rules_performance_report(payload: dict[str, Any]) -> str:
    readiness = payload.get("data_readiness", {})
    buckets = payload.get("buckets", {})
    score_bands = payload.get("score_bands", {})
    markets = payload.get("markets", {})
    sides = payload.get("sides", {})

    lines = [
        "Meta-Label Rules Performance - Shadow Only",
        f"prediction_date: {payload.get('prediction_date', '')}",
        "=" * 72,
        "Data Readiness Metrics",
        "-" * 72,
        f"- completed slate count: {readiness.get('completed_slate_count', 0)}",
        f"- graded hit/miss rows: {readiness.get('graded_hit_miss_rows', 0)}",
        f"- minimum sample status: {readiness.get('minimum_sample_threshold_status', 'insufficient')}",
        f"- missing role stability rate: {_fmt_pct(readiness.get('missing_role_stability_rate'))}",
        f"- missing fragility/survivability rate: {_fmt_pct(readiness.get('missing_fragility_rate'))}",
        f"- Phase 4C readiness verdict: {readiness.get('verdict', 'WAIT_MORE_DATA')}",
        "",
        "Bucket Outcome Summary",
        "-" * 72,
        "bucket | total_rows | graded | hits | misses | pushes | voids | hit_rate | status"
    ]

    for bname, stats in sorted(buckets.items()):
        lines.append(
            " | ".join(
                [
                    bname,
                    str(stats.get("total_rows", 0)),
                    str(stats.get("graded_rows", 0)),
                    str(stats.get("hits", 0)),
                    str(stats.get("misses", 0)),
                    str(stats.get("pushes", 0)),
                    str(stats.get("voids", 0)),
                    _fmt_pct(stats.get("hit_rate")),
                    str(stats.get("sample_status", "insufficient")),
                ]
            )
        )

    lines.extend(
        [
            "",
            "Score-Band Outcome Summary",
            "-" * 72,
            "band | total_rows | graded | hits | misses | pushes | voids | hit_rate | status"
        ]
    )

    for band, stats in sorted(score_bands.items(), reverse=True):
        lines.append(
            " | ".join(
                [
                    band,
                    str(stats.get("total_rows", 0)),
                    str(stats.get("graded_rows", 0)),
                    str(stats.get("hits", 0)),
                    str(stats.get("misses", 0)),
                    str(stats.get("pushes", 0)),
                    str(stats.get("voids", 0)),
                    _fmt_pct(stats.get("hit_rate")),
                    str(stats.get("sample_status", "insufficient")),
                ]
            )
        )

    lines.extend(
        [
            "",
            "Market Breakdown",
            "-" * 72,
            "market | total_rows | graded | hits | misses | pushes | voids | hit_rate | status"
        ]
    )

    for mkt, stats in sorted(markets.items()):
        lines.append(
            " | ".join(
                [
                    mkt,
                    str(stats.get("total_rows", 0)),
                    str(stats.get("graded_rows", 0)),
                    str(stats.get("hits", 0)),
                    str(stats.get("misses", 0)),
                    str(stats.get("pushes", 0)),
                    str(stats.get("voids", 0)),
                    _fmt_pct(stats.get("hit_rate")),
                    str(stats.get("sample_status", "insufficient")),
                ]
            )
        )

    lines.extend(
        [
            "",
            "Side Breakdown",
            "-" * 72,
            "side | total_rows | graded | hits | misses | pushes | voids | hit_rate | status"
        ]
    )

    for side, stats in sorted(sides.items()):
        lines.append(
            " | ".join(
                [
                    side,
                    str(stats.get("total_rows", 0)),
                    str(stats.get("graded_rows", 0)),
                    str(stats.get("hits", 0)),
                    str(stats.get("misses", 0)),
                    str(stats.get("pushes", 0)),
                    str(stats.get("voids", 0)),
                    _fmt_pct(stats.get("hit_rate")),
                    str(stats.get("sample_status", "insufficient")),
                ]
            )
        )

    lines.extend(["", DIAGNOSTIC_ONLY_NOTE, ""])
    return "\n".join(lines) + "\n"


def write_rules_performance_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    shadow_history_df: pd.DataFrame | None = None,
    pick_history_df: pd.DataFrame | None = None,
    current_meta_df: pd.DataFrame | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    """Write rules performanceJSON, operator TXT, and operator CSV."""
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)

    if shadow_history_df is None:
        shadow_history_df = _read_csv(history_root / "market_shadow_history.csv")

    if pick_history_df is None:
        pick_history_df = _read_csv(history_root / "pick_history.csv")

    payload = build_rules_performance_report(
        prediction_date=prediction_date,
        shadow_history_df=shadow_history_df,
        pick_history_df=pick_history_df,
        current_meta_df=current_meta_df,
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
    txt_path.write_text(render_rules_performance_report(payload), encoding="utf-8")

    # Generate CSV rows
    csv_rows = []
    
    # helper to append group records to csv rows list
    def append_group(gtype: str, gdict: dict[str, dict[str, Any]]):
        for name, stats in sorted(gdict.items()):
            csv_rows.append(
                {
                    "group_type": gtype,
                    "group_name": name,
                    "total_rows": stats.get("total_rows", 0),
                    "graded_rows": stats.get("graded_rows", 0),
                    "hits": stats.get("hits", 0),
                    "misses": stats.get("misses", 0),
                    "pushes": stats.get("pushes", 0),
                    "voids": stats.get("voids", 0),
                    "pending": stats.get("pending", 0),
                    "hit_rate": stats.get("hit_rate"),
                    "sample_status": stats.get("sample_status", "insufficient"),
                }
            )

    append_group("bucket", payload.get("buckets", {}))
    append_group("score_band", payload.get("score_bands", {}))
    append_group("market", payload.get("markets", {}))
    append_group("side", payload.get("sides", {}))

    pd.DataFrame(csv_rows, columns=list(CSV_FIELDS)).to_csv(csv_path, index=False)

    return json_path, txt_path, csv_path, payload
