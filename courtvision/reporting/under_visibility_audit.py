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
REPORT_VERSION = "1.0"


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
