from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


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


def _safe_int(value: Any, default: int = 0) -> int:
    number = _safe_float(value)
    return default if number is None else int(number)


def _format_num(value: Any, digits: int = 2, *, trim: bool = False) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    text = f"{number:.{digits}f}"
    return text.rstrip("0").rstrip(".") if trim else text


def _format_rate(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    if abs(number) <= 1:
        number *= 100
    return f"{number:.1f}%"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False)
    except Exception:
        return pd.DataFrame()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _count_under_rows(df: pd.DataFrame) -> int:
    if df.empty or "selection" not in df.columns:
        return 0
    return int((df["selection"].astype(str).str.lower() == "under").sum())


def _sort_candidates(df: pd.DataFrame, limit: int) -> pd.DataFrame:
    if df.empty:
        return df
    working = df.copy()
    sort_columns: list[str] = []
    for column in ("quality_score", "confidence"):
        if column in working.columns:
            working[column] = pd.to_numeric(working[column], errors="coerce")
            sort_columns.append(column)
    if "edge" in working.columns:
        working["_abs_edge_for_display"] = pd.to_numeric(working["edge"], errors="coerce").abs()
        sort_columns.append("_abs_edge_for_display")
    if sort_columns:
        working = working.sort_values(sort_columns, ascending=[False] * len(sort_columns), na_position="last")
    return working.head(limit)


def _line_value(row: pd.Series) -> Any:
    for column in ("sportsbook_line", "line"):
        if column in row.index and _safe_text(row.get(column)):
            return row.get(column)
    return None


def _edge_value(row: pd.Series) -> Any:
    for column in ("directional_edge", "edge"):
        if column in row.index and _safe_text(row.get(column)):
            return row.get(column)
    return None


def get_under_research_snapshot_text(
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
) -> str:
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)

    operator_dir = runtime_root / "operator"
    diagnostics_dir = runtime_root / "diagnostics"

    audit_json_path = diagnostics_dir / f"under_visibility_audit_{prediction_date}.json"
    shadow_lane_path = operator_dir / f"shadow_candidate_lane_{prediction_date}.csv"

    full_market_path = operator_dir / f"full_market_board_{prediction_date}.csv"
    near_elite_path = operator_dir / f"near_elite_review_{prediction_date}.csv"
    incubator_path = operator_dir / f"incubator_board_{prediction_date}.csv"
    shadow_history_path = history_root / "market_shadow_history.csv"

    # 1. Load under_visibility_audit json if available
    audit_payload = _read_json(audit_json_path)

    # 2. Extract pipeline stage counts
    # Fallback to direct calculation if JSON not available
    full_market_df = _read_csv(full_market_path)
    near_elite_df = _read_csv(near_elite_path)
    incubator_df = _read_csv(incubator_path)
    shadow_lane_df = _read_csv(shadow_lane_path)

    if audit_payload and "funnel_stages" in audit_payload:
        funnel = audit_payload["funnel_stages"]
        
        fm_under = funnel.get("full_market", {}).get("under", 0)
        fm_total = funnel.get("full_market", {}).get("total", 0)
        
        ne_under = funnel.get("near_elite", {}).get("under", 0)
        
        inc_under = funnel.get("incubator", {}).get("under", 0)
        
        sl_under = funnel.get("shadow_candidate_lane", {}).get("under", 0)
        sl_total = funnel.get("shadow_candidate_lane", {}).get("total", 0)
    else:
        # Compute dynamically
        fm_under = _count_under_rows(full_market_df)
        fm_total = len(full_market_df)
        
        ne_under = _count_under_rows(near_elite_df)
        
        inc_under = _count_under_rows(incubator_df)
        
        sl_under = _count_under_rows(shadow_lane_df)
        sl_total = len(shadow_lane_df)

    fm_pct = (fm_under / fm_total * 100) if fm_total > 0 else 0.0
    sl_pct = (sl_under / sl_total * 100) if sl_total > 0 else 0.0

    # 3. Get historical shadow OVER vs UNDER comparison
    hist_comp = audit_payload.get("historical_comparison") if audit_payload else None
    
    if hist_comp:
        under_hr = _format_rate(hist_comp.get("under", {}).get("hit_rate"))
        under_roi = _format_rate(hist_comp.get("under", {}).get("roi"))
        under_n = str(hist_comp.get("under", {}).get("count", 0))
        
        over_hr = _format_rate(hist_comp.get("over", {}).get("hit_rate"))
        over_roi = _format_rate(hist_comp.get("over", {}).get("roi"))
        over_n = str(hist_comp.get("over", {}).get("count", 0))
    elif shadow_history_path.exists():
        # Compute dynamically from shadow history CSV
        shadow_hist_df = _read_csv(shadow_history_path)
        if not shadow_hist_df.empty and "selection" in shadow_hist_df.columns and "result_status" in shadow_hist_df.columns:
            hist_over_count = 0
            hist_over_hits = 0
            hist_over_roi_sum = 0.0
            hist_over_roi_count = 0
            
            hist_under_count = 0
            hist_under_hits = 0
            hist_under_roi_sum = 0.0
            hist_under_roi_count = 0
            
            for sel, group in shadow_hist_df.groupby("selection"):
                hits = int((group["result_status"] == "hit").sum())
                misses = int((group["result_status"] == "miss").sum())
                pushes = int((group["result_status"] == "push").sum())
                total = hits + misses + pushes
                
                # Check for ROI column
                roi_col = None
                for col in ("shadow_roi", "roi"):
                    if col in group.columns:
                        roi_col = col
                        break
                
                avg_roi = 0.0
                if roi_col:
                    roi_vals = pd.to_numeric(group[roi_col], errors="coerce").dropna()
                    if len(roi_vals) > 0:
                        avg_roi = float(roi_vals.mean())
                
                if sel == "over":
                    hist_over_count = total
                    hist_over_hits = hits
                    hist_over_roi_sum = avg_roi
                elif sel == "under":
                    hist_under_count = total
                    hist_under_hits = hits
                    hist_under_roi_sum = avg_roi
            
            under_hr = _format_rate(hist_under_hits / (hist_under_hits + (hist_under_count - hist_under_hits - int((shadow_hist_df[(shadow_hist_df["selection"] == "under") & (shadow_hist_df["result_status"] == "push")].shape[0])))) if (hist_under_count - int((shadow_hist_df[(shadow_hist_df["selection"] == "under") & (shadow_hist_df["result_status"] == "push")].shape[0]))) > 0 else None)
            under_roi = _format_rate(hist_under_roi_sum)
            under_n = str(hist_under_count)
            
            over_hr = _format_rate(hist_over_hits / (hist_over_hits + (hist_over_count - hist_over_hits - int((shadow_hist_df[(shadow_hist_df["selection"] == "over") & (shadow_hist_df["result_status"] == "push")].shape[0])))) if (hist_over_count - int((shadow_hist_df[(shadow_hist_df["selection"] == "over") & (shadow_hist_df["result_status"] == "push")].shape[0]))) > 0 else None)
            over_roi = _format_rate(hist_over_roi_sum)
            over_n = str(hist_over_count)
        else:
            under_hr, under_roi, under_n = "n/a", "n/a", "n/a"
            over_hr, over_roi, over_n = "n/a", "n/a", "n/a"
    else:
        # Return n/a instead of stale hardcoded values
        under_hr, under_roi, under_n = "n/a", "n/a", "n/a"
        over_hr, over_roi, over_n = "n/a", "n/a", "n/a"

    # 4. Top Current UNDER candidates
    top_under_lines = []
    # If the JSON list is available, prefer it
    if audit_payload and "current_slate_candidates" in audit_payload:
        candidates = audit_payload["current_slate_candidates"]
        sorted_c = sorted(
            [c for c in candidates if c.get("selection") == "under"],
            key=lambda c: (
                _safe_float(c.get("quality_score")) or 0.0,
                _safe_float(c.get("confidence")) or 0.0,
                abs(_safe_float(c.get("edge")) or 0.0)
            ),
            reverse=True
        )[:5]
        for c in sorted_c:
            player = c.get("player_name") or "Unknown"
            market = c.get("market_type") or "unknown"
            selection = c.get("selection") or "under"
            line = _format_num(c.get("line"), 1, trim=True)
            proj = _format_num(c.get("model_projection"), 2)
            edge = _format_num(c.get("edge"), 2)
            conf = _format_num(c.get("confidence"), 2)
            qual = _format_num(c.get("quality_score"), 1)
            reason = _safe_text(c.get("rejection_reason"))
            reason_suffix = f", reason={reason}" if reason and reason.lower() not in {"none", "n/a", "na"} else ""
            top_under_lines.append(
                f"- {player}: {market} {selection} {line}, proj={proj}, edge={edge}, conf={conf}, quality={qual}{reason_suffix}"
            )
    else:
        # Load from full_market_df directly
        if not full_market_df.empty and "selection" in full_market_df.columns:
            under_candidates = full_market_df[full_market_df["selection"].astype(str).str.lower() == "under"].copy()
            sorted_fm = _sort_candidates(under_candidates, 5)
            for _, row in sorted_fm.iterrows():
                player = _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name")) or "Unknown"
                market = _safe_text(row.get("market_type")) or "unknown"
                selection = _safe_text(row.get("selection")) or "under"
                line = _format_num(_line_value(row), 1, trim=True)
                proj = _format_num(row.get("model_projection"), 2)
                edge = _format_num(_edge_value(row), 2)
                conf = _format_num(row.get("confidence"), 2)
                qual = _format_num(row.get("quality_score"), 1)
                
                reason = ""
                for col in ("source_rejection_reason", "final_elite_rejection_reason", "rejection_reason"):
                    val = _safe_text(row.get(col))
                    if val and val.lower() not in {"none", "n/a", "na"}:
                        reason = val
                        break
                reason_suffix = f", reason={reason}" if reason else ""
                top_under_lines.append(
                    f"- {player}: {market} {selection} {line}, proj={proj}, edge={edge}, conf={conf}, quality={qual}{reason_suffix}"
                )

    if not top_under_lines:
        top_under_lines.append("  - None")

    # 5. Top UNDER_ALIGNED_RESEARCH Candidates
    top_aligned_lines = []
    if shadow_lane_path.exists():
        shadow_lane_df = _read_csv(shadow_lane_path)
        if not shadow_lane_df.empty and "research_lane" in shadow_lane_df.columns:
            # Filter for UNDER_ALIGNED_RESEARCH research_lane
            aligned_under = shadow_lane_df[shadow_lane_df["research_lane"] == "UNDER_ALIGNED_RESEARCH"].head(5)
            for _, row in aligned_under.iterrows():
                player = _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name")) or "Unknown"
                market = _safe_text(row.get("market_type")) or "unknown"
                selection = _safe_text(row.get("selection")) or "under"
                line = _format_num(_line_value(row), 1, trim=True)
                n = _safe_int(row.get("historical_graded_rows"), 0)
                
                hit_val = _safe_float(row.get("historical_hit_rate"))
                hit_str = _format_rate(hit_val) if hit_val is not None else "n/a"
                
                roi_val = _safe_float(row.get("historical_roi"))
                roi_str = _format_rate(roi_val) if roi_val is not None else "n/a"
                
                top_aligned_lines.append(
                    f"- {player}: {market} {selection} {line}, hist_n={n}, hit={hit_str}, ROI={roi_str}"
                )

    if not top_aligned_lines:
        top_aligned_lines.append("  - None")

    # Build report block
    lines = [
        "UNDER Research Snapshot - Shadow Only",
        "----------------------------------------",
        f"- full-market UNDER count: {fm_under} / {fm_total} ({fm_pct:.1f}%)" if fm_total > 0 else f"- full-market UNDER count: {fm_under}",
        f"- near-elite UNDER count: {ne_under}",
        f"- incubator UNDER count: {inc_under}",
    ]
    
    if shadow_lane_path.exists():
        lines.append(f"- shadow-lane UNDER count: {sl_under} / {sl_total} ({sl_pct:.1f}%)" if sl_total > 0 else f"- shadow-lane UNDER count: {sl_under}")
    else:
        lines.append("- shadow-lane UNDER count: n/a (shadow_candidate_lane file not found)")

    lines.extend([
        f"- historical shadow UNDER: {under_hr} hit, {under_roi} ROI, n={under_n}",
        f"- historical shadow OVER: {over_hr} hit, {over_roi} ROI, n={over_n}",
        "- recommended action: improve visibility / continue shadow tracking / no staking",
        "",
        "Top Current UNDER Candidates:",
    ])
    lines.extend(top_under_lines)
    lines.extend([
        "",
        "Top UNDER_ALIGNED_RESEARCH Candidates:",
    ])
    lines.extend(top_aligned_lines)
    lines.extend([
        "",
        "Disclaimer:",
        "- UNDER Research Snapshot is shadow-only.",
        "- It is not an Elite board.",
        "- It is not a Kelly input.",
        "- It is not a betting recommendation.",
        "- No real-money promotion is allowed."
    ])

    return "\n".join(lines)
