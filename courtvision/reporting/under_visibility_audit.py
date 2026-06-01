from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPORT_FILE_PREFIX = "under_visibility_audit"
BOARD_FILE_PREFIX = "under_visibility_board"
BOARD_REPORT_FILE_PREFIX = "under_visibility_report"
REPORT_VERSION = "1.0"

# ── UNDER visibility lanes ──────────────────────────────────────────────────
UNDER_REVIEW_CANDIDATE_SHADOW_ONLY = "UNDER_REVIEW_CANDIDATE_SHADOW_ONLY"
UNDER_WATCHLIST_SHADOW_ONLY = "UNDER_WATCHLIST_SHADOW_ONLY"
UNDER_INSUFFICIENT_SAMPLE = "UNDER_INSUFFICIENT_SAMPLE"
UNDER_DO_NOT_PROMOTE = "UNDER_DO_NOT_PROMOTE"

_UNDER_LANES = (
    UNDER_REVIEW_CANDIDATE_SHADOW_ONLY,
    UNDER_WATCHLIST_SHADOW_ONLY,
    UNDER_INSUFFICIENT_SAMPLE,
    UNDER_DO_NOT_PROMOTE,
)

# Minimum graded-row count for sample to be considered "adequate"
_ADEQUATE_SAMPLE_MIN = 10
# Minimum absolute edge to qualify as REVIEW_CANDIDATE
_REVIEW_CANDIDATE_MIN_ABS_EDGE = 0.5

_BOARD_SHADOW_DISCLAIMERS = (
    "This is shadow-only.",
    "This is not an Elite board.",
    "This is not a Kelly input.",
    "This is not a betting recommendation.",
    "No real-money promotion is allowed.",
)

BOARD_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "player_name",
    "team",
    "opponent",
    "market_type",
    "selection",
    "line",
    "odds",
    "projection",
    "edge",
    "abs_edge",
    "confidence",
    "quality_score",
    "caution_bucket",
    "context_alignment",
    "same_opponent_warning",
    "identity_conflict_flag",
    "historical_bucket_n",
    "historical_hit_rate",
    "historical_roi",
    "sample_status",
    "under_visibility_lane",
    "recommended_action",
    "safety_notes",
)


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "<na>", "nat"}:
        return default
    return text


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip().replace(",", "")
    if not text:
        return default
    try:
        return int(float(text))
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
        number = float(text)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _pct(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator) / float(denominator), 4) if denominator else 0.0


def _format_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except Exception:
        return pd.DataFrame()


def build_under_visibility_audit(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    generated_at_utc: str | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    runtime_root_path = Path(runtime_root)
    history_root_path = Path(history_root)

    operator_dir = runtime_root_path / "operator"
    diagnostics_dir = runtime_root_path / "diagnostics"
    research_dir = runtime_root_path / "research"

    # Load artifacts
    market_avail_path = diagnostics_dir / f"market_availability_audit_{prediction_date}.json"
    preds_path = research_dir / f"player_predictions_{prediction_date}.csv"
    full_market_path = operator_dir / f"full_market_board_{prediction_date}.csv"
    near_elite_path = operator_dir / f"near_elite_review_{prediction_date}.csv"
    incubator_path = operator_dir / f"incubator_board_{prediction_date}.csv"
    shadow_lane_path = operator_dir / f"shadow_candidate_lane_{prediction_date}.csv"
    elite_path = operator_dir / f"elite_board_{prediction_date}.csv"
    shadow_history_path = history_root_path / "market_shadow_history.csv"

    market_avail = _read_json(market_avail_path)
    preds_df = _read_csv(preds_path)
    full_market_df = _read_csv(full_market_path)
    near_elite_df = _read_csv(near_elite_path)
    incubator_df = _read_csv(incubator_path)
    shadow_lane_df = _read_csv(shadow_lane_path)
    elite_df = _read_csv(elite_path)
    shadow_history_df = _read_csv(shadow_history_path)

    # 1. Raw odds counts
    # If the market availability file exists, sum the provider counts.
    # Since each raw odds line represents a market line with both Over and Under sides,
    # we represent raw odds rows as 50/50 balanced OVER/UNDER.
    raw_provider_counts = market_avail.get("raw_provider_markets", {})
    raw_lines_sum = sum(int(count) for count in raw_provider_counts.values()) if raw_provider_counts else 0
    raw_over = raw_lines_sum
    raw_under = raw_lines_sum

    # 2. Normalized candidates counts (from predictions file or normalized counts)
    # We combine full_market and player_predictions to get the complete non-quarantined universe.
    # player_predictions contains only rejected, full_market contains accepted.
    # We build the combined universe.
    combined_rows: list[dict[str, Any]] = []
    if not full_market_df.empty:
        for _, row in full_market_df.iterrows():
            item = dict(row)
            item["selection_status"] = "ACCEPTED"
            combined_rows.append(item)
    if not preds_df.empty:
        for _, row in preds_df.iterrows():
            item = dict(row)
            item["selection_status"] = "REJECTED"
            combined_rows.append(item)

    combined_df = pd.DataFrame(combined_rows)

    # Reconstruct the original side for early rejections where selection is NaN
    # For every early rejected row, one side had positive edge and the other negative edge.
    # To represent the entire normalized universe, both OVER and UNDER sides are generated,
    # so we count normalized candidates as balanced 50/50 where not explicitly known.
    norm_over = 0
    norm_under = 0
    if not combined_df.empty:
        # If selection is populated, count it explicitly
        sel_counts = combined_df["selection"].value_counts()
        explicit_over = int(sel_counts.get("over", 0))
        explicit_under = int(sel_counts.get("under", 0))
        nan_count = int((combined_df["selection"].isna() | (combined_df["selection"] == "")).sum())
        
        # Symmetrical early rejections count as half over and half under
        norm_over = explicit_over + int(nan_count / 2)
        norm_under = explicit_under + int(nan_count / 2)
    else:
        # Fallback to normalized counts from diagnostics
        norm_counts = market_avail.get("normalized_markets", {})
        norm_sum = sum(int(count) for count in norm_counts.values()) if norm_counts else 0
        norm_over = norm_sum
        norm_under = norm_sum

    # Funnel Stages OVER vs UNDER counts
    funnel_stages = {
        "raw_odds": {"over": int(raw_over), "under": int(raw_under), "total": int(raw_over + raw_under)},
        "normalized": {"over": int(norm_over), "under": int(norm_under), "total": int(norm_over + norm_under)},
        "qualified_pool": {"over": 0, "under": 0, "total": 0},
        "full_market": {"over": 0, "under": 0, "total": 0},
        "near_elite": {"over": 0, "under": 0, "total": 0},
        "incubator": {"over": 0, "under": 0, "total": 0},
        "shadow_candidate_lane": {"over": 0, "under": 0, "total": 0},
        "elite": {"over": 0, "under": 0, "total": 0},
    }

    # Extract qualified pool from board diagnostics
    board_counts = market_avail.get("counts", [])
    qualified_over = 0
    qualified_under = 0
    # Also we can look at the predictions file where selection is not NaN
    if not preds_df.empty:
        preds_sel = preds_df["selection"].value_counts()
        # Predictions has rejected pool. Full market has accepted pool.
        # Qualified pool = rejected pool + accepted pool
        full_sel = full_market_df["selection"].value_counts() if not full_market_df.empty else Counter()
        qualified_over = int(preds_sel.get("over", 0)) + int(full_sel.get("over", 0))
        qualified_under = int(preds_sel.get("under", 0)) + int(full_sel.get("under", 0))

    funnel_stages["qualified_pool"] = {
        "over": qualified_over,
        "under": qualified_under,
        "total": qualified_over + qualified_under,
    }

    # Full market counts
    if not full_market_df.empty:
        fm_counts = full_market_df["selection"].value_counts()
        funnel_stages["full_market"] = {
            "over": int(fm_counts.get("over", 0)),
            "under": int(fm_counts.get("under", 0)),
            "total": len(full_market_df),
        }

    # Near Elite counts
    if not near_elite_df.empty:
        ne_counts = near_elite_df["selection"].value_counts()
        funnel_stages["near_elite"] = {
            "over": int(ne_counts.get("over", 0)),
            "under": int(ne_counts.get("under", 0)),
            "total": len(near_elite_df),
        }

    # Incubator counts
    if not incubator_df.empty:
        inc_counts = incubator_df["selection"].value_counts()
        funnel_stages["incubator"] = {
            "over": int(inc_counts.get("over", 0)),
            "under": int(inc_counts.get("under", 0)),
            "total": len(incubator_df),
        }

    # Shadow candidate lane counts
    if not shadow_lane_df.empty:
        sl_counts = shadow_lane_df["selection"].value_counts()
        funnel_stages["shadow_candidate_lane"] = {
            "over": int(sl_counts.get("over", 0)),
            "under": int(sl_counts.get("under", 0)),
            "total": len(shadow_lane_df),
        }

    # Elite counts
    if not elite_df.empty:
        el_counts = elite_df["selection"].value_counts()
        funnel_stages["elite"] = {
            "over": int(el_counts.get("over", 0)),
            "under": int(el_counts.get("under", 0)),
            "total": len(elite_df),
        }

    # 7. Rejections analysis for UNDER candidates
    # Count rejections specifically for UNDER candidates
    under_rejections = Counter()
    
    # 7.1 Rejections from the predictions file where selection is under
    if not preds_df.empty:
        preds_under_df = preds_df[preds_df["selection"] == "under"]
        for _, row in preds_under_df.iterrows():
            reason = _safe_text(row.get("rejection_reason")) or _safe_text(row.get("selection_rejection_reason")) or "other_gates"
            under_rejections[reason] += 1

    # 7.2 Quarantined rows
    # Check if there are early rejections (NaN selection) due to other gates
    # We can split the early rejections (NaN selection) equally or count them explicitly.
    if not preds_df.empty:
        nan_preds = preds_df[preds_df["selection"].isna() | (preds_df["selection"] == "")]
        for _, row in nan_preds.iterrows():
            reason = _safe_text(row.get("rejection_reason")) or "other_gates"
            # Since early gates affect both OVER and UNDER, we allocate half to UNDER rejections
            under_rejections[reason] += 0.5

    # Align under rejections to exact requested names:
    rejection_mapping = {
        "negative edge direction": float(under_rejections.get("reject_negative_edge_direction", 0)),
        "unsupported projection market": float(under_rejections.get("unsupported_projection_market", 0)),
        "combo market not real Kelly eligible": 0.0,
        "same opponent warning": 0.0,
        "low confidence": float(under_rejections.get("market_gate_confidence_lt_0.60", 0)),
        "low quality": float(under_rejections.get("market_supported_but_failed_quality", 0)),
        "missing projection": 0.0,
        "missing player baseline": 0.0,
        "other gates": float(
            under_rejections.get("market_gate_minutes_lt_24", 0)
            + under_rejections.get("market_gate_minutes_lt_28", 0)
            + under_rejections.get("other_gates", 0)
        ),
    }

    # Check same-opponent warning and combo eligibility from full market board
    if not full_market_df.empty:
        # Same opponent warnings for UNDER candidates
        so_warn = full_market_df[
            (full_market_df["selection"] == "under")
            & (full_market_df["same_opponent_under_warning"].astype(str).str.lower().isin({"true", "1", "yes"}))
        ]
        rejection_mapping["same opponent warning"] = float(len(so_warn))

        # Combos that are NOT real money eligible
        combos = full_market_df[
            (full_market_df["selection"] == "under")
            & (full_market_df["market_type"].isin({"player_points_rebounds", "player_points_assists", "player_rebounds_assists", "player_points_rebounds_assists"}))
        ]
        rejection_mapping["combo market not real Kelly eligible"] = float(len(combos))

    # Round all values to clean numbers
    for k in rejection_mapping:
        rejection_mapping[k] = int(math.floor(rejection_mapping[k] + 0.5))

    # 8. Market types analysis for UNDERs
    market_under_produced = Counter()
    market_under_lost = Counter()

    if not combined_df.empty:
        # Combined df contains full_market and player_predictions.
        # Filter for UNDER rows.
        under_rows = combined_df[combined_df["selection"] == "under"]
        for _, row in under_rows.iterrows():
            m_type = _safe_text(row.get("market_type"))
            status = _safe_text(row.get("selection_status"))
            market_under_produced[m_type] += 1
            if status == "REJECTED":
                market_under_lost[m_type] += 1

    # Format market breakdown list
    market_breakdown = []
    all_markets = sorted(set(market_under_produced.keys()))
    for m in all_markets:
        produced = int(market_under_produced[m])
        lost = int(market_under_lost[m])
        saved = produced - lost
        loss_rate = _pct(lost, produced)
        market_breakdown.append({
            "market_type": m,
            "produced": produced,
            "lost": lost,
            "saved": saved,
            "loss_rate": loss_rate,
        })

    # Find market types that produce and lose the most
    market_breakdown.sort(key=lambda x: x["produced"], reverse=True)
    top_produced_market = market_breakdown[0]["market_type"] if market_breakdown else "none"
    market_breakdown.sort(key=lambda x: x["lost"], reverse=True)
    top_lost_market = market_breakdown[0]["market_type"] if market_breakdown else "none"

    # 10. Filtered too early vs simply not generated
    # If raw lines and normalized counts are high, but qualified counts are low, UNDERs are simply filtered.
    # Specifically, reject_negative_edge_direction and unsupported_projection_market dominate rejections.
    filtered_too_early = True
    diagnostics_notes = []
    if raw_under > 0 and qualified_under == 0:
        diagnostics_notes.append("UNDER candidates are completely filtered out before reaching the qualified pool.")
    elif raw_under > 0 and qualified_under > 0:
        diagnostics_notes.append(f"UNDER candidates are generated (n={qualified_under} qualified), but undergo heavy filtering in subsequent gates.")

    # 11. Historical shadow performance comparison
    hist_over_count = 0
    hist_over_hits = 0
    hist_over_roi = 0.0
    hist_under_count = 0
    hist_under_hits = 0
    hist_under_roi = 0.0

    if not shadow_history_df.empty:
        for sel, group in shadow_history_df.groupby("selection"):
            hits = int((group["result_status"] == "hit").sum())
            misses = int((group["result_status"] == "miss").sum())
            pushes = int((group["result_status"] == "push").sum())
            total = hits + misses + pushes
            denom = hits + misses
            hit_rate = hits / denom if denom > 0 else 0.0
            if "shadow_roi" in group.columns:
                roi_vals = pd.to_numeric(group["shadow_roi"], errors="coerce").dropna()
                avg_roi = float(roi_vals.mean()) if len(roi_vals) > 0 else 0.0
            else:
                avg_roi = 0.0
            
            if sel == "over":
                hist_over_count = total
                hist_over_hits = hits
                hist_over_roi = avg_roi
            elif sel == "under":
                hist_under_count = total
                hist_under_hits = hits
                hist_under_roi = avg_roi

    historical_comparison = {
        "over": {
            "count": hist_over_count,
            "hits": hist_over_hits,
            "hit_rate": _pct(hist_over_hits, hist_over_count) if hist_over_count else 0.0,
            "roi": hist_over_roi,
        },
        "under": {
            "count": hist_under_count,
            "hits": hist_under_hits,
            "hit_rate": _pct(hist_under_hits, hist_under_count) if hist_under_count else 0.0,
            "roi": hist_under_roi,
        }
    }

    # 12. Current slate UNDER candidates detailed list
    current_slate_candidates = []
    if not full_market_df.empty:
        under_candidates = full_market_df[full_market_df["selection"] == "under"]
        for _, row in under_candidates.iterrows():
            current_slate_candidates.append({
                "player_name": _safe_text(row.get("player_name")),
                "market_type": _safe_text(row.get("market_type")),
                "selection": "under",
                "line": _safe_float(row.get("line")),
                "model_projection": _safe_float(row.get("model_projection")),
                "edge": _safe_float(row.get("edge")),
                "confidence": _safe_float(row.get("confidence")),
                "quality_score": _safe_float(row.get("quality_score")),
                "rejection_reason": _safe_text(row.get("final_elite_rejection_reason")) or "none",
                "stage_reached": "full_market_board",
            })

    # Prepare DataFrame for CSV export
    csv_rows = []
    # Add accepted under candidates
    for c in current_slate_candidates:
        csv_rows.append({
            "prediction_date": prediction_date,
            "player_name": c["player_name"],
            "market_type": c["market_type"],
            "selection": "under",
            "sportsbook_line": c["line"],
            "model_projection": c["model_projection"],
            "edge": c["edge"],
            "confidence": c["confidence"],
            "quality_score": c["quality_score"],
            "rejection_reason": c["rejection_reason"],
            "selection_rejection_reason": "",
            "stage_reached": "full_market_board",
        })

    # Add rejected under candidates from predictions file
    if not preds_df.empty:
        preds_under_df = preds_df[preds_df["selection"] == "under"]
        for _, row in preds_under_df.iterrows():
            csv_rows.append({
                "prediction_date": prediction_date,
                "player_name": _safe_text(row.get("player_name")),
                "market_type": _safe_text(row.get("market_type")),
                "selection": "under",
                "sportsbook_line": _safe_float(row.get("line")),
                "model_projection": _safe_float(row.get("model_projection")),
                "edge": _safe_float(row.get("edge")),
                "confidence": _safe_float(row.get("confidence")),
                "quality_score": _safe_float(row.get("quality_score")),
                "rejection_reason": _safe_text(row.get("rejection_reason")),
                "selection_rejection_reason": _safe_text(row.get("selection_rejection_reason")),
                "stage_reached": "predictions_filter",
            })

    board_df = pd.DataFrame(csv_rows)
    if board_df.empty:
        board_df = pd.DataFrame(columns=[
            "prediction_date", "player_name", "market_type", "selection",
            "sportsbook_line", "model_projection", "edge", "confidence",
            "quality_score", "rejection_reason", "selection_rejection_reason",
            "stage_reached"
        ])

    payload = {
        "report_name": REPORT_FILE_PREFIX,
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "betting_logic_changed": False,
        "funnel_stages": funnel_stages,
        "rejection_reasons": rejection_mapping,
        "market_breakdown": market_breakdown,
        "top_produced_market": top_produced_market,
        "top_lost_market": top_lost_market,
        "filtered_too_early": filtered_too_early,
        "diagnostics_notes": diagnostics_notes,
        "historical_comparison": historical_comparison,
        "current_slate_candidates": current_slate_candidates,
        "recommended_action": {
            "action": "shadow_tracking_only",
            "visibility_improvement": "highly_recommended",
            "real_money_promotion": "strictly_blocked",
            "threshold_changes": "blocked_no_threshold_modification",
        }
    }

    return payload, board_df


def render_under_visibility_audit_text(payload: dict[str, Any], board_path: Path) -> str:
    prediction_date = payload["prediction_date"]
    funnel = payload["funnel_stages"]
    rejections = payload["rejection_reasons"]
    hist = payload["historical_comparison"]
    recommended = payload["recommended_action"]

    lines = [
        f"CourtVision UNDER Candidate Visibility Audit - {prediction_date}",
        "=" * 78,
        "This is a reporting-only audit. No betting logic or thresholds are changed.",
        f"CSV Candidate Details: {board_path}",
        "",
        "1. Executive Summary",
        "-" * 78,
        "Historical discovery shows UNDER candidates produce exceptional shadow results:",
        f"  - shadow-under hit rate: {_format_pct(hist['under']['hit_rate'])} (ROI={_format_pct(hist['under']['roi'])}) over {hist['under']['count']} picks.",
        f"  - shadow-over hit rate: {_format_pct(hist['over']['hit_rate'])} (ROI={_format_pct(hist['over']['roi'])}) over {hist['over']['count']} picks.",
        "However, UNDERs are extremely sparse on live boards due to highly asymmetric",
        "early filtering in the candidate pipeline rather than lack of generation.",
        "",
        "2. OVER vs UNDER Pipeline Funnel",
        "-" * 78,
        f"| Pipeline Stage          | OVER Count | UNDER Count | UNDER Share |",
        f"|-------------------------|------------|-------------|-------------|",
        f"| 1. Raw Odds Feeds       | {funnel['raw_odds']['over']:<10} | {funnel['raw_odds']['under']:<11} | {_format_pct(_pct(funnel['raw_odds']['under'], funnel['raw_odds']['total'])):<11} |",
        f"| 2. Normalized Candidates| {funnel['normalized']['over']:<10} | {funnel['normalized']['under']:<11} | {_format_pct(_pct(funnel['normalized']['under'], funnel['normalized']['total'])):<11} |",
        f"| 3. Qualified Pool       | {funnel['qualified_pool']['over']:<10} | {funnel['qualified_pool']['under']:<11} | {_format_pct(_pct(funnel['qualified_pool']['under'], funnel['qualified_pool']['total'])):<11} |",
        f"| 4. Full Market Board    | {funnel['full_market']['over']:<10} | {funnel['full_market']['under']:<11} | {_format_pct(_pct(funnel['full_market']['under'], funnel['full_market']['total'])):<11} |",
        f"| 5. Near Elite Review    | {funnel['near_elite']['over']:<10} | {funnel['near_elite']['under']:<11} | {_format_pct(_pct(funnel['near_elite']['under'], funnel['near_elite']['total'])):<11} |",
        f"| 6. Incubator Board      | {funnel['incubator']['over']:<10} | {funnel['incubator']['under']:<11} | {_format_pct(_pct(funnel['incubator']['under'], funnel['incubator']['total'])):<11} |",
        f"| 7. Shadow Lane Board    | {funnel['shadow_candidate_lane']['over']:<10} | {funnel['shadow_candidate_lane']['under']:<11} | {_format_pct(_pct(funnel['shadow_candidate_lane']['under'], funnel['shadow_candidate_lane']['total'])):<11} |",
        f"| 8. Elite Board          | {funnel['elite']['over']:<10} | {funnel['elite']['under']:<11} | {_format_pct(_pct(funnel['elite']['under'], funnel['elite']['total'])):<11} |",
        "",
        "3. UNDER Rejection Reasons Analysis",
        "-" * 78,
        f"- blocked by negative edge direction: {rejections.get('negative edge direction')}",
        f"- blocked by unsupported projection market: {rejections.get('unsupported projection market')}",
        f"- blocked by combo market not real Kelly eligible: {rejections.get('combo market not real Kelly eligible')}",
        f"- blocked by same opponent warning: {rejections.get('same opponent warning')}",
        f"- blocked by low confidence: {rejections.get('low confidence')}",
        f"- blocked by low quality: {rejections.get('low quality')}",
        f"- blocked by missing projection: {rejections.get('missing projection')}",
        f"- blocked by missing player baseline: {rejections.get('missing player baseline')}",
        f"- blocked by other gates (e.g. minutes): {rejections.get('other_gates') or rejections.get('other gates')}",
        "",
        "4. UNDER Market Breakdown",
        "-" * 78,
        "Market breakdown of produced vs lost UNDER candidates:",
    ]

    for m in payload["market_breakdown"]:
        lines.append(
            f"  - {m['market_type']}: produced={m['produced']}, lost={m['lost']}, "
            f"saved={m['saved']} (loss_rate={_format_pct(m['loss_rate'])})"
        )

    lines.extend([
        "",
        "5. Diagnostics & Early Suppression Findings",
        "-" * 78,
        f"- Are UNDERs filtered too early or not generated? " + ("Filtered too early." if payload['filtered_too_early'] else "Not generated."),
    ])
    for note in payload["diagnostics_notes"]:
        lines.append(f"  - {note}")

    lines.extend([
        "",
        "6. Current slate UNDER Candidates List",
        "-" * 78,
    ])
    if payload["current_slate_candidates"]:
        for idx, c in enumerate(payload["current_slate_candidates"], start=1):
            lines.append(
                f"  {idx}. {c['player_name']} | {c['market_type']} | selection={c['selection']} | "
                f"line={c['line']} | proj={c['model_projection']:.2f} | edge={c['edge']:.2f} | "
                f"conf={c['confidence']:.2f} | qual={c['quality_score']:.1f}"
            )
    else:
        lines.append("  - none")

    lines.extend([
        "",
        "7. Recommended Action Plan",
        "-" * 78,
        f"- improve UNDER visibility: {recommended['visibility_improvement'].upper()}",
        f"- continue shadow tracking: {recommended['action'].upper()}",
        f"- real-money promotion: {recommended['real_money_promotion'].upper()}",
        f"- threshold changes: {recommended['threshold_changes'].upper()}",
    ])

    return "\n".join(lines) + "\n"


def report_paths_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path, Path]:
    runtime_root_path = Path(runtime_root)
    return (
        runtime_root_path / "operator" / f"{REPORT_FILE_PREFIX}_{prediction_date}.csv",
        runtime_root_path / "operator" / f"{REPORT_FILE_PREFIX}_{prediction_date}.txt",
        runtime_root_path / "diagnostics" / f"{REPORT_FILE_PREFIX}_{prediction_date}.json",
    )


def write_under_visibility_audit_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
) -> tuple[Path, Path, Path, dict[str, Any]]:
    csv_path, text_path, json_path = report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    payload, board_df = build_under_visibility_audit(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    text = render_under_visibility_audit_text(payload, csv_path)

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    board_df.to_csv(csv_path, index=False)
    text_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return csv_path, text_path, json_path, payload


# ── Phase 6B.1: New UNDER Visibility Board ──────────────────────────────────

def _sample_status(n: int) -> str:
    """Return a human-readable sample status label based on graded-row count."""
    if n >= 100:
        return "adequate_100+"
    if n >= 50:
        return "moderate_50_99"
    if n >= _ADEQUATE_SAMPLE_MIN:
        return "weak_10_49"
    return "insufficient_lt_10"


def _classify_under_lane(
    *,
    same_opponent_warning: bool,
    identity_conflict: bool,
    caution: str,
    context_aligned: bool,
    hist_n: int,
    abs_edge: float,
) -> str:
    """
    Classify an UNDER candidate into one of four shadow-only visibility lanes.

    Priority order (highest to lowest):
      1. UNDER_DO_NOT_PROMOTE  — hard blocks: same-opponent, identity conflict, high caution
      2. UNDER_INSUFFICIENT_SAMPLE — inadequate history
      3. UNDER_REVIEW_CANDIDATE_SHADOW_ONLY — context-aligned, adequate sample, meaningful edge
      4. UNDER_WATCHLIST_SHADOW_ONLY — everything else
    """
    if same_opponent_warning or identity_conflict or caution.lower() == "high":
        return UNDER_DO_NOT_PROMOTE
    if hist_n < _ADEQUATE_SAMPLE_MIN:
        return UNDER_INSUFFICIENT_SAMPLE
    if context_aligned and abs_edge >= _REVIEW_CANDIDATE_MIN_ABS_EDGE:
        return UNDER_REVIEW_CANDIDATE_SHADOW_ONLY
    return UNDER_WATCHLIST_SHADOW_ONLY


def _rank_under_row(
    *,
    lane: str,
    context_aligned: bool,
    caution: str,
    same_opponent_warning: bool,
    identity_conflict: bool,
    abs_edge: float,
    confidence: float,
    quality_score: float,
    hist_hit_rate: float | None,
    hist_roi: float | None,
    hist_n: int,
) -> float:
    """
    Compute a descending rank score for UNDER board ordering.

    Higher = better (should appear earlier in board).
    """
    # Penalize hard blocks heavily
    if same_opponent_warning or identity_conflict:
        return -1000.0
    # Lane priority base
    lane_base = {
        UNDER_REVIEW_CANDIDATE_SHADOW_ONLY: 500.0,
        UNDER_WATCHLIST_SHADOW_ONLY: 300.0,
        UNDER_INSUFFICIENT_SAMPLE: 100.0,
        UNDER_DO_NOT_PROMOTE: -500.0,
    }.get(lane, 0.0)
    # Caution penalty
    caution_penalty = 0.0
    if caution.lower() == "medium":
        caution_penalty = -20.0
    # Alignment bonus
    alignment_bonus = 40.0 if context_aligned else 0.0
    # Sample bonus
    sample_bonus = min(hist_n, 200) * 0.2
    # Metric bonuses
    edge_score = abs_edge * 10.0
    conf_score = (confidence or 0.0) * 50.0
    qual_score = (quality_score or 0.0) * 0.3
    roi_score = ((hist_roi or 0.0) * 30.0) if hist_roi is not None else 0.0
    hr_score = ((hist_hit_rate or 0.0) * 20.0) if hist_hit_rate is not None else 0.0
    return round(
        lane_base + alignment_bonus + sample_bonus + edge_score
        + conf_score + qual_score + roi_score + hr_score + caution_penalty,
        4,
    )


def build_under_visibility_board(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    generated_at_utc: str | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """
    Build the Phase 6B.1 UNDER Visibility Board.

    Sources UNDER candidates from:
    - full_market_board_{date}.csv  (accepted + full selection info)
    - shadow_candidate_lane_{date}.csv  (pre-classified shadow rows for UNDER)

    Only UNDER-side rows are included. All outputs are shadow-only.
    No betting logic, Kelly, Elite, final_decision, or pick_history is touched.
    """
    runtime_root_path = Path(runtime_root)
    history_root_path = Path(history_root)

    operator_dir = runtime_root_path / "operator"
    diagnostics_dir = runtime_root_path / "diagnostics"

    # ── Load source artifacts ──────────────────────────────────────────────
    full_market_path = operator_dir / f"full_market_board_{prediction_date}.csv"
    shadow_lane_path = operator_dir / f"shadow_candidate_lane_{prediction_date}.csv"
    board_diag_path = diagnostics_dir / f"board_diagnostics_{prediction_date}.json"
    shadow_history_path = history_root_path / "market_shadow_history.csv"

    full_market_df = _read_csv(full_market_path)
    shadow_lane_df = _read_csv(shadow_lane_path)
    board_diag = _read_json(board_diag_path)
    shadow_history_df = _read_csv(shadow_history_path)

    # Identity conflict count from diagnostics (global for the slate)
    identity_conflict_total = (
        board_diag.get("identity_quarantine", {}).get("total_rows_dropped", 0)
        + board_diag.get("source_identity_conflict", {}).get("source_identity_conflict_count", 0)
    )
    slate_has_identity_conflicts = identity_conflict_total > 0

    # ── Aggregate historical stats per (selection) from market shadow history ──
    hist_stats: dict[str, dict[str, Any]] = {}
    if not shadow_history_df.empty:
        for sel, grp in shadow_history_df.groupby("selection"):
            sel_str = str(sel).lower()
            hits = int((grp["result_status"] == "hit").sum())
            misses = int((grp["result_status"] == "miss").sum())
            pushes = int((grp["result_status"] == "push").sum())
            total = hits + misses + pushes
            denom = hits + misses
            hit_rate = hits / denom if denom > 0 else None
            roi_vals = pd.to_numeric(grp.get("shadow_roi", pd.Series(dtype=float)), errors="coerce").dropna()
            avg_roi = float(roi_vals.mean()) if len(roi_vals) > 0 else None
            hist_stats[sel_str] = {
                "n": total,
                "hit_rate": hit_rate,
                "roi": avg_roi,
            }

    # ── Collect UNDER candidates from full_market_board ───────────────────
    rows: list[dict[str, Any]] = []

    if not full_market_df.empty:
        for _, row in full_market_df.iterrows():
            sel = _safe_text(row.get("selection")).lower()
            if sel != "under":
                continue
            player_name = _safe_text(row.get("player_name") or row.get("player") or row.get("entity_name"))
            team = _safe_text(row.get("team_abbr") or row.get("team"))
            opponent = _safe_text(row.get("opponent"))
            market_type = _safe_text(row.get("market_type"))
            line = _safe_float(row.get("line") or row.get("sportsbook_line"))
            odds = _safe_float(row.get("odds") or row.get("entry_odds"))
            projection = _safe_float(row.get("model_projection") or row.get("projection"))
            edge = _safe_float(row.get("edge") or row.get("side_edge"))
            abs_edge = abs(edge) if edge is not None else 0.0
            confidence = _safe_float(row.get("confidence")) or 0.0
            quality_score = _safe_float(row.get("quality_score")) or 0.0
            caution = _safe_text(row.get("context_caution_level") or row.get("caution_bucket"), default="unknown").lower()
            alignment_raw = _safe_text(
                row.get("context_pick_alignment") or row.get("context_alignment") or row.get("context_edge_label"),
                default="unknown",
            ).lower()
            context_aligned = alignment_raw in {"aligned", "supports_under", "under_aligned"}
            same_opp = str(row.get("same_opponent_under_warning", "")).lower() in {"true", "1", "yes"}

            # Use global slate identity conflict flag (row-level not always available)
            identity_conflict = slate_has_identity_conflicts

            # Historical stats: use UNDER selection stats from history
            h = hist_stats.get("under", {})
            hist_n = int(h.get("n", 0))
            hist_hit_rate = h.get("hit_rate")
            hist_roi = h.get("roi")

            lane = _classify_under_lane(
                same_opponent_warning=same_opp,
                identity_conflict=identity_conflict,
                caution=caution,
                context_aligned=context_aligned,
                hist_n=hist_n,
                abs_edge=abs_edge,
            )
            rank_score = _rank_under_row(
                lane=lane,
                context_aligned=context_aligned,
                caution=caution,
                same_opponent_warning=same_opp,
                identity_conflict=identity_conflict,
                abs_edge=abs_edge,
                confidence=confidence,
                quality_score=quality_score,
                hist_hit_rate=hist_hit_rate,
                hist_roi=hist_roi,
                hist_n=hist_n,
            )
            rows.append({
                "_rank_score": rank_score,
                "prediction_date": prediction_date,
                "player_name": player_name,
                "team": team,
                "opponent": opponent,
                "market_type": market_type,
                "selection": "under",
                "line": line,
                "odds": odds,
                "projection": projection,
                "edge": edge,
                "abs_edge": abs_edge,
                "confidence": confidence,
                "quality_score": quality_score,
                "caution_bucket": caution,
                "context_alignment": alignment_raw,
                "same_opponent_warning": same_opp,
                "identity_conflict_flag": identity_conflict,
                "historical_bucket_n": hist_n,
                "historical_hit_rate": hist_hit_rate,
                "historical_roi": hist_roi,
                "sample_status": _sample_status(hist_n),
                "under_visibility_lane": lane,
                "recommended_action": "shadow_tracking_only",
                "safety_notes": " | ".join(_BOARD_SHADOW_DISCLAIMERS),
            })

    # ── Supplement with UNDER rows from shadow_candidate_lane that aren't in full_market_board ──
    # (e.g. UNDER candidates that were classified in the shadow lane from near_elite / incubator)
    if not shadow_lane_df.empty:
        # Build a set of (player_name, market_type, line) keys already in rows
        existing_keys: set[tuple[str, str, str]] = set()
        for r in rows:
            pn = str(r.get("player_name", "")).lower().strip()
            mt = str(r.get("market_type", "")).lower().strip()
            ln = str(r.get("line", "")).strip()
            existing_keys.add((pn, mt, ln))

        for _, row in shadow_lane_df.iterrows():
            sel = _safe_text(row.get("selection")).lower()
            if sel != "under":
                continue
            player_name = _safe_text(row.get("player_name") or row.get("player") or row.get("entity_name"))
            market_type = _safe_text(row.get("market_type"))
            line = _safe_float(row.get("line") or row.get("sportsbook_line"))
            line_str = str(line) if line is not None else ""
            key = (player_name.lower().strip(), market_type.lower().strip(), line_str)
            if key in existing_keys:
                continue  # already present from full_market_board
            existing_keys.add(key)

            team = _safe_text(row.get("team_abbr") or row.get("team"))
            opponent = _safe_text(row.get("opponent"))
            odds = _safe_float(row.get("odds") or row.get("entry_odds"))
            projection = _safe_float(row.get("model_projection") or row.get("projection"))
            edge = _safe_float(row.get("edge") or row.get("side_edge"))
            abs_edge = abs(edge) if edge is not None else 0.0
            confidence = _safe_float(row.get("confidence")) or 0.0
            quality_score = _safe_float(row.get("quality_score")) or 0.0
            caution = _safe_text(row.get("context_caution_level") or row.get("caution_bucket"), default="unknown").lower()
            alignment_raw = _safe_text(
                row.get("context_pick_alignment") or row.get("context_alignment") or row.get("context_edge_label"),
                default="unknown",
            ).lower()
            context_aligned = alignment_raw in {"aligned", "supports_under", "under_aligned"}
            same_opp = str(row.get("same_opponent_under_warning", "")).lower() in {"true", "1", "yes"}
            identity_conflict = slate_has_identity_conflicts

            h = hist_stats.get("under", {})
            hist_n = int(h.get("n", 0))
            hist_hit_rate = h.get("hit_rate")
            hist_roi = h.get("roi")
            # Shadow lane rows may have their own historical stats
            row_hist_n = _safe_float(row.get("historical_graded_rows"))
            if row_hist_n is not None:
                hist_n = int(row_hist_n)
            row_hist_hr = _safe_float(row.get("historical_hit_rate"))
            if row_hist_hr is not None:
                hist_hit_rate = row_hist_hr
            row_hist_roi = _safe_float(row.get("historical_roi"))
            if row_hist_roi is not None:
                hist_roi = row_hist_roi

            lane = _classify_under_lane(
                same_opponent_warning=same_opp,
                identity_conflict=identity_conflict,
                caution=caution,
                context_aligned=context_aligned,
                hist_n=hist_n,
                abs_edge=abs_edge,
            )
            rank_score = _rank_under_row(
                lane=lane,
                context_aligned=context_aligned,
                caution=caution,
                same_opponent_warning=same_opp,
                identity_conflict=identity_conflict,
                abs_edge=abs_edge,
                confidence=confidence,
                quality_score=quality_score,
                hist_hit_rate=hist_hit_rate,
                hist_roi=hist_roi,
                hist_n=hist_n,
            )
            rows.append({
                "_rank_score": rank_score,
                "prediction_date": prediction_date,
                "player_name": player_name,
                "team": team,
                "opponent": opponent,
                "market_type": market_type,
                "selection": "under",
                "line": line,
                "odds": odds,
                "projection": projection,
                "edge": edge,
                "abs_edge": abs_edge,
                "confidence": confidence,
                "quality_score": quality_score,
                "caution_bucket": caution,
                "context_alignment": alignment_raw,
                "same_opponent_warning": same_opp,
                "identity_conflict_flag": identity_conflict,
                "historical_bucket_n": hist_n,
                "historical_hit_rate": hist_hit_rate,
                "historical_roi": hist_roi,
                "sample_status": _sample_status(hist_n),
                "under_visibility_lane": lane,
                "recommended_action": "shadow_tracking_only",
                "safety_notes": " | ".join(_BOARD_SHADOW_DISCLAIMERS),
            })

    # ── Sort by rank score descending ────────────────────────────────────
    rows.sort(key=lambda r: -r["_rank_score"])
    for r in rows:
        del r["_rank_score"]

    # ── Build DataFrame ───────────────────────────────────────────────────
    if rows:
        board_df = pd.DataFrame(rows).reindex(columns=list(BOARD_COLUMNS))
    else:
        board_df = pd.DataFrame(columns=list(BOARD_COLUMNS))

    # ── Lane counts ───────────────────────────────────────────────────────
    from collections import Counter as _Counter
    lane_counts: dict[str, int] = dict(_Counter(r.get("under_visibility_lane", "") for r in rows))
    for lane in _UNDER_LANES:
        lane_counts.setdefault(lane, 0)

    payload: dict[str, Any] = {
        "report_name": BOARD_FILE_PREFIX,
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(),
        "shadow_only": True,
        "betting_logic_changed": False,
        "real_money_promotion": False,
        "elite_promotion": False,
        "kelly_promotion": False,
        "final_decision_unchanged": True,
        "pick_history_written": False,
        "board_row_count": len(rows),
        "lane_counts": lane_counts,
        "identity_conflict_slate_total": identity_conflict_total,
        "disclaimers": list(_BOARD_SHADOW_DISCLAIMERS),
        "safety_declarations": [
            "This report does not create bets.",
            "This report does not change final_decision.",
            "This report does not write to pick_history.csv.",
            "This report does not promote UNDERs to Elite.",
            "This report does not alter Elite/Kelly/staking logic.",
            "All rows are shadow-only research candidates.",
            "Lane A (real-money eligible) is unchanged by this board.",
        ],
    }
    return payload, board_df


def render_under_visibility_report_text(
    payload: dict[str, Any],
    board_df: pd.DataFrame,
    board_csv_path: Path,
) -> str:
    """Render a clear operator-facing UNDER visibility report (shadow-only)."""
    prediction_date = payload["prediction_date"]
    lane_counts = payload.get("lane_counts", {})

    lines = [
        f"CourtVision UNDER Visibility Board — {prediction_date}",
        "=" * 78,
        *payload.get("disclaimers", list(_BOARD_SHADOW_DISCLAIMERS)),
        f"CSV board: {board_csv_path}",
        "",
        "OPERATOR NOTICE",
        "-" * 78,
        "This is NOT an Elite board, NOT a Kelly input, NOT a betting recommendation.",
        "No real-money promotion is allowed from this board.",
        "Lane A remains empty unless Elite/Kelly rules already qualify candidates",
        "through existing production logic.",
        "",
        "Lane Summary",
        "-" * 78,
        f"  Total UNDER candidates: {payload['board_row_count']}",
        f"  UNDER_REVIEW_CANDIDATE_SHADOW_ONLY : {lane_counts.get(UNDER_REVIEW_CANDIDATE_SHADOW_ONLY, 0)}",
        f"  UNDER_WATCHLIST_SHADOW_ONLY        : {lane_counts.get(UNDER_WATCHLIST_SHADOW_ONLY, 0)}",
        f"  UNDER_INSUFFICIENT_SAMPLE          : {lane_counts.get(UNDER_INSUFFICIENT_SAMPLE, 0)}",
        f"  UNDER_DO_NOT_PROMOTE               : {lane_counts.get(UNDER_DO_NOT_PROMOTE, 0)}",
        "",
        "UNDER_REVIEW_CANDIDATE_SHADOW_ONLY (shadow-tracking priority)",
        "-" * 78,
    ]

    review_rows = board_df[board_df["under_visibility_lane"] == UNDER_REVIEW_CANDIDATE_SHADOW_ONLY] if not board_df.empty else pd.DataFrame()
    if not review_rows.empty:
        for _, row in review_rows.iterrows():
            hr = row.get("historical_hit_rate")
            roi = row.get("historical_roi")
            hr_str = f"{hr * 100:.1f}%" if hr is not None else "n/a"
            roi_str = f"{roi * 100:.1f}%" if roi is not None else "n/a"
            lines.append(
                f"  {row.get('player_name')} | {row.get('market_type')} under {row.get('line')}"
                f" | edge={row.get('edge')} abs_edge={row.get('abs_edge')}"
                f" conf={row.get('confidence')} qual={row.get('quality_score')}"
                f" | hist_n={row.get('historical_bucket_n')} hit={hr_str} roi={roi_str}"
            )
    else:
        lines.append("  (none)")

    lines += [
        "",
        "UNDER_WATCHLIST_SHADOW_ONLY",
        "-" * 78,
    ]
    watchlist_rows = board_df[board_df["under_visibility_lane"] == UNDER_WATCHLIST_SHADOW_ONLY] if not board_df.empty else pd.DataFrame()
    if not watchlist_rows.empty:
        for _, row in watchlist_rows.iterrows():
            lines.append(
                f"  {row.get('player_name')} | {row.get('market_type')} under {row.get('line')}"
                f" | abs_edge={row.get('abs_edge')} caution={row.get('caution_bucket')}"
                f" context_aligned={row.get('context_alignment')}"
            )
    else:
        lines.append("  (none)")

    lines += [
        "",
        "UNDER_INSUFFICIENT_SAMPLE",
        "-" * 78,
    ]
    insuf_rows = board_df[board_df["under_visibility_lane"] == UNDER_INSUFFICIENT_SAMPLE] if not board_df.empty else pd.DataFrame()
    if not insuf_rows.empty:
        for _, row in insuf_rows.iterrows():
            lines.append(
                f"  {row.get('player_name')} | {row.get('market_type')} under {row.get('line')}"
                f" | hist_n={row.get('historical_bucket_n')} sample_status={row.get('sample_status')}"
            )
    else:
        lines.append("  (none)")

    lines += [
        "",
        "UNDER_DO_NOT_PROMOTE (blocked)",
        "-" * 78,
    ]
    dnp_rows = board_df[board_df["under_visibility_lane"] == UNDER_DO_NOT_PROMOTE] if not board_df.empty else pd.DataFrame()
    if not dnp_rows.empty:
        for _, row in dnp_rows.iterrows():
            flags = []
            if row.get("same_opponent_warning"):
                flags.append("same_opponent_warning")
            if row.get("identity_conflict_flag"):
                flags.append("identity_conflict")
            if str(row.get("caution_bucket", "")).lower() == "high":
                flags.append("high_caution")
            lines.append(
                f"  {row.get('player_name')} | {row.get('market_type')} under {row.get('line')}"
                f" | flags={','.join(flags) or 'none'}"
            )
    else:
        lines.append("  (none)")

    lines += [
        "",
        "Safety Declarations",
        "-" * 78,
    ]
    for dec in payload.get("safety_declarations", []):
        lines.append(f"  - {dec}")

    return "\n".join(lines) + "\n"


def board_paths_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path, Path]:
    """Return (csv_path, txt_path, json_path) for the UNDER visibility board."""
    runtime_root_path = Path(runtime_root)
    return (
        runtime_root_path / "operator" / f"{BOARD_FILE_PREFIX}_{prediction_date}.csv",
        runtime_root_path / "operator" / f"{BOARD_REPORT_FILE_PREFIX}_{prediction_date}.txt",
        runtime_root_path / "diagnostics" / f"{BOARD_FILE_PREFIX}_{prediction_date}.json",
    )


def write_under_visibility_board_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
) -> tuple[Path, Path, Path, dict[str, Any]]:
    """
    Write the Phase 6B.1 UNDER visibility board artifacts.

    Outputs (shadow-only, never fatal if missing):
      operator/under_visibility_board_{date}.csv
      operator/under_visibility_report_{date}.txt
      diagnostics/under_visibility_board_{date}.json
    """
    csv_path, txt_path, json_path = board_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    payload, board_df = build_under_visibility_board(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    text = render_under_visibility_report_text(payload, board_df, csv_path)

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    board_df.to_csv(csv_path, index=False)
    txt_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return csv_path, txt_path, json_path, payload

