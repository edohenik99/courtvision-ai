from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _clean_str(val) -> str:
    if pd.isna(val):
        return ""
    return str(val).strip()


def _is_false(val) -> bool:
    if isinstance(val, bool):
        return not val
    s = _clean_str(val).lower()
    return s in ("false", "f", "0", "0.0")


def _is_true(val) -> bool:
    if isinstance(val, bool):
        return val
    s = _clean_str(val).lower()
    return s in ("true", "t", "1", "1.0")


def _is_truthy(val) -> bool:
    if isinstance(val, bool):
        return val
    s = _clean_str(val).lower()
    return s in ("true", "1", "yes", "y")


def _safe_float(val) -> float:
    try:
        if pd.isna(val):
            return 0.0
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _read_json(path: Path) -> dict:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def run_bet_readiness_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    strict: bool = False,
) -> tuple[int, dict, str]:
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)

    # 1. Map paths
    operator_dir = runtime_root / "operator"
    diagnostics_dir = runtime_root / "diagnostics"

    required_paths = {
        "elite_board": operator_dir / f"elite_board_{prediction_date}.csv",
        "full_market_board": operator_dir / f"full_market_board_{prediction_date}.csv",
        "operator_card": operator_dir / f"operator_card_{prediction_date}.txt",
        "daily_summary": operator_dir / f"daily_summary_{prediction_date}.txt",
        "pre_game_finalization_guard": diagnostics_dir / f"pre_game_finalization_guard_{prediction_date}.json",
        "board_diagnostics": diagnostics_dir / f"board_diagnostics_{prediction_date}.json",
    }

    optional_paths = {
        "sgp_board": operator_dir / f"sgp_board_{prediction_date}.csv",
        "near_elite_review": operator_dir / f"near_elite_review_{prediction_date}.csv",
        "incubator_board": operator_dir / f"incubator_board_{prediction_date}.csv",
        "shadow_candidate_lane": operator_dir / f"shadow_candidate_lane_{prediction_date}.csv",
        "kelly_stakes": operator_dir / f"kelly_stakes_{prediction_date}.csv",
        "quality_summary": operator_dir / f"quality_summary_{prediction_date}.json",
        # Phase 6B.1 — UNDER visibility board (shadow-only; never affects betability)
        "under_visibility_board": diagnostics_dir / f"under_visibility_board_{prediction_date}.json",
    }

    # 2. Check required files existence
    missing_required = []
    for name, path in required_paths.items():
        if not path.exists():
            missing_required.append(path.name)

    # 3. Read files
    elite_df = _read_csv(required_paths["elite_board"])
    full_market_df = _read_csv(required_paths["full_market_board"])
    sgp_df = _read_csv(optional_paths["sgp_board"])
    near_elite_df = _read_csv(optional_paths["near_elite_review"])
    incubator_df = _read_csv(optional_paths["incubator_board"])
    shadow_lane_df = _read_csv(optional_paths["shadow_candidate_lane"])
    kelly_df = _read_csv(optional_paths["kelly_stakes"])

    # Load diagnostic JSONs
    guard_data = _read_json(required_paths["pre_game_finalization_guard"])
    board_diag_data = _read_json(required_paths["board_diagnostics"])
    quality_data = _read_json(optional_paths["quality_summary"])
    # Phase 6B.1 — UNDER visibility board (shadow-only; does NOT affect betability)
    under_vis_board_data = _read_json(optional_paths["under_visibility_board"])

    # 4. Check date integrity
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", prediction_date):
        missing_required.append(f"Invalid date format: {prediction_date}")

    # Determine checkers / status
    has_missing_artifacts = len(missing_required) > 0

    # Read guard status
    guard_status = _clean_str(guard_data.get("status", "NOT_READY"))
    is_guard_ready = guard_status in ("READY_TO_LOCK", "READY_WITH_WARNINGS")

    # Read operator card decision
    op_card_content = ""
    op_card_decision = "UNKNOWN"
    if required_paths["operator_card"].exists():
        try:
            op_card_content = required_paths["operator_card"].read_text(encoding="utf-8")
            match = re.search(r"final_decision:\s*(\w+(?:\s+\w+)?)", op_card_content)
            if match:
                op_card_decision = match.group(1).strip()
        except Exception:
            pass

    daily_sum_content = ""
    if required_paths["daily_summary"].exists():
        try:
            daily_sum_content = required_paths["daily_summary"].read_text(encoding="utf-8")
        except Exception:
            pass

    # Identify Kelly eligible/stakeable rows
    # Strongest evidence: kelly_stakes_YYYY-MM-DD.csv is present and has kelly_eligible rows
    kelly_stakeable = []
    if not kelly_df.empty:
        col = "kelly_eligible" if "kelly_eligible" in kelly_df.columns else ("eligible" if "eligible" in kelly_df.columns else None)
        if col:
            for _, row in kelly_df.iterrows():
                if _is_truthy(row.get(col)):
                    kelly_stakeable.append(row)
    else:
        # Fallback to elite_board
        if not elite_df.empty:
            col = "kelly_eligible" if "kelly_eligible" in elite_df.columns else ("eligible" if "eligible" in elite_df.columns else None)
            if col:
                for _, row in elite_df.iterrows():
                    if _is_truthy(row.get(col)):
                        kelly_stakeable.append(row)

    has_elite_rows = len(elite_df) > 0
    has_kelly_rows = len(kelly_stakeable) > 0

    # Identity conflict check
    # Check diagnostics
    identity_conflicts = 0
    if board_diag_data:
        identity_conflicts += board_diag_data.get("identity_quarantine", {}).get("total_rows_dropped", 0)
        identity_conflicts += board_diag_data.get("source_identity_conflict", {}).get("source_identity_conflict_count", 0)
    if quality_data:
        identity_conflicts += quality_data.get("source_identity_conflict", {}).get("source_identity_conflict_count", 0)

    # Stale / Date Mismatch check
    source_date_mismatch = False
    if not shadow_lane_df.empty:
        for _, row in shadow_lane_df.iterrows():
            row_pred_date = _clean_str(row.get("prediction_date", ""))
            row_source_date = _clean_str(row.get("source_artifact_date", ""))
            if row_pred_date != prediction_date or row_source_date != prediction_date:
                source_date_mismatch = True

    if guard_data:
        date_integrity = guard_data.get("date_integrity", {})
        if date_integrity.get("same_date_status") == "mismatch":
            source_date_mismatch = True

    # Check freshness of operator card and daily summary
    is_fresh = False
    if required_paths["operator_card"].exists() and required_paths["daily_summary"].exists():
        has_card_date = f"prediction_date: {prediction_date}" in op_card_content or f"prediction_date:{prediction_date}" in op_card_content
        has_sum_snapshot = "UNDER Research Snapshot - Shadow Only" in daily_sum_content or "historical shadow UNDER" in daily_sum_content
        if has_card_date and has_sum_snapshot:
            is_fresh = True

    # Define Manual Review Hold Precisely
    manual_review_required = False
    if op_card_decision == "REVIEW REQUIRED":
        manual_review_required = True

    # Inspect stakeable/Elite/Kelly candidates for review flags
    # We inspect elite_df and kelly_df
    candidate_dfs = [elite_df]
    if not kelly_df.empty:
        candidate_dfs.append(kelly_df)

    for df in candidate_dfs:
        if not df.empty:
            for _, row in df.iterrows():
                # Only check stakeable/Elite/Kelly rows
                # Any row in elite_board or kelly stakes is considered a candidate
                for col in ("manual_review_required", "manual_review_hold", "review_flag", "review_required"):
                    if col in row.index and _is_truthy(row.get(col)):
                        manual_review_required = True
                for col in ("operator_action", "recommended_action"):
                    if col in row.index:
                        action_str = _clean_str(row.get(col)).upper()
                        if "REVIEW" in action_str or action_str == "DO_NOT_BET_UNTIL_REVIEWED":
                            manual_review_required = True

    # Identify shadow-only restriction
    has_only_research_shadow = False
    # If Elite and Kelly stakes are empty (or only have non-eligible rows)
    # and SGP, near_elite, incubator, or shadow lane has rows
    if not has_elite_rows and not has_kelly_rows:
        has_only_research_shadow = (len(near_elite_df) > 0 or len(incubator_df) > 0 or len(shadow_lane_df) > 0 or len(sgp_df) > 0)

    # 5. Compute Betability Score (0 to 100)
    score = 0
    if not has_missing_artifacts:
        # Addition points
        if is_guard_ready:
            score += 25
        if has_elite_rows:
            score += 25
        if has_kelly_rows:
            score += 20
        if not source_date_mismatch:
            score += 10
        if identity_conflicts == 0:
            score += 10
        if is_fresh:
            score += 10

        # Apply Caps
        if not has_kelly_rows:
            score = min(score, 49)
        if has_only_research_shadow:
            score = min(score, 59)
        if manual_review_required:
            score = min(score, 69)
    else:
        score = 0

    # 6. Status decision ladder
    if has_missing_artifacts:
        status = "NOT_BETTABLE"
    elif guard_status == "NOT_READY":
        status = "NOT_BETTABLE"
    elif is_guard_ready and not has_elite_rows and not has_kelly_rows:
        status = "RESEARCH_ONLY"
    elif has_elite_rows and not has_kelly_rows:
        status = "REVIEW_ONLY"
    elif has_kelly_rows and manual_review_required:
        status = "REVIEW_ONLY"
    elif has_kelly_rows and is_guard_ready and not manual_review_required:
        status = "BETTABLE"
    else:
        status = "NOT_BETTABLE"

    # Recommended action mapping
    if status == "BETTABLE":
        recommended_action = "user may manually review Elite/Kelly output only"
    elif status == "REVIEW_ONLY":
        recommended_action = "inspect but do not auto-stake"
    elif status == "RESEARCH_ONLY":
        recommended_action = "keep paper tracking"
    else:
        recommended_action = "no bet"

    # Blockers breakdown
    blockers = []
    if has_missing_artifacts:
        blockers.append("missing artifacts")
    if not has_elite_rows:
        blockers.append("no Elite rows")
    if not has_kelly_rows:
        blockers.append("no Kelly eligible rows")
    if source_date_mismatch:
        blockers.append("stale/date mismatch")
    if identity_conflicts > 0:
        blockers.append("identity conflicts")
    if manual_review_required:
        blockers.append("manual review required")
    if not is_guard_ready:
        blockers.append("finalisation guard not ready")

    # Check high-caution OVER gate
    # Scan full_market_board or elite_board for Over + High Caution
    has_high_caution_over = False
    for df in (full_market_df, elite_df):
        if not df.empty and "selection" in df.columns and "context_caution_level" in df.columns:
            for _, row in df.iterrows():
                if _clean_str(row.get("selection")).lower() == "over" and _clean_str(row.get("context_caution_level")).lower() == "high":
                    has_high_caution_over = True
    if has_high_caution_over:
        blockers.append("high-caution OVER gate")

    # Scan for low quality / low confidence
    has_low_quality = False
    has_low_confidence = False
    for df in (elite_df, kelly_df):
        if not df.empty:
            for _, row in df.iterrows():
                quality = _safe_float(row.get("quality_score"))
                confidence = _safe_float(row.get("confidence"))
                if quality > 0 and quality < 0.4:
                    has_low_quality = True
                if confidence > 0 and confidence < 0.4:
                    has_low_confidence = True
    if has_low_quality:
        blockers.append("low quality")
    if has_low_confidence:
        blockers.append("low confidence")

    # Same-opponent warnings
    has_same_opponent_warning = False
    for df in (full_market_df, elite_df):
        if not df.empty and "same_opponent_under_warning" in df.columns:
            for _, row in df.iterrows():
                if _is_truthy(row.get("same_opponent_under_warning")):
                    has_same_opponent_warning = True
    if has_same_opponent_warning:
        blockers.append("same-opponent warnings")

    # Separate candidates into lanes
    unknown_players_list = []

    def _get_display_name(row) -> str:
        for field in ["player", "player_name", "athlete", "participant", "name"]:
            val = row.get(field)
            if val is not None and not pd.isna(val) and str(val).strip() != "" and str(val).strip().lower() != "nan":
                return str(val).strip()
        
        desc = row.get("description")
        if desc is not None and not pd.isna(desc) and str(desc).strip() != "":
            return str(desc).strip()
            
        return "Unknown"

    # Helper function to format candidate row
    def _format_candidate(row) -> str:
        player = _get_display_name(row)
        if player == "Unknown":
            mkt = row.get("market_type", "unknown")
            sel = row.get("selection", "unknown")
            line = row.get("line", "n/a")
            unknown_players_list.append(f"{mkt} {sel} {line}")

        mkt = row.get("market_type", "unknown")
        sel = row.get("selection", "unknown")
        line = row.get("line", "n/a")
        edge = row.get("edge", "n/a")
        conf = row.get("confidence", "n/a")
        return f"{player}: {mkt} {sel} {line} (edge={edge}, confidence={conf})"

    # Helper to construct raw candidate entries before formatting/deduping
    def _create_candidate_entry(row, category_prefix: str = "") -> dict:
        player = _get_display_name(row)
        mkt = row.get("market_type", "unknown")
        sel = row.get("selection", "unknown")
        line = row.get("line", "n/a")
        formatted = _format_candidate(row)
        
        # Prepend category prefix if any
        full_formatted = f"{category_prefix}{formatted}" if category_prefix else formatted
        
        return {
            "category": category_prefix,
            "player": player,
            "market_type": mkt,
            "selection": sel,
            "line": line,
            "formatted": full_formatted
        }

    def _get_dedupe_key(entry: dict) -> tuple[str, str, str, str, str]:
        category = entry.get("category", "")
        player = entry.get("player", "")
        market_type = entry.get("market_type", "")
        selection = entry.get("selection", "")
        line = entry.get("line", "")

        # Normalize
        norm_category = str(category).lower().strip()
        norm_player = str(player).lower().strip()
        norm_market = str(market_type).lower().strip()
        norm_selection = str(selection).lower().strip()
        
        try:
            norm_line = str(float(line))
        except (ValueError, TypeError):
            norm_line = str(line).lower().strip()

        return (
            norm_category,
            norm_player,
            norm_market,
            norm_selection,
            norm_line
        )

    def _deduplicate_lane_entries(entries: list[dict]) -> tuple[list[dict], int, list[str]]:
        seen_keys = set()
        deduped = []
        num_removed = 0
        removed_examples = []
        
        for entry in entries:
            key = _get_dedupe_key(entry)
            if key in seen_keys:
                num_removed += 1
                ex = entry["formatted"]
                if ex not in removed_examples:
                    removed_examples.append(ex)
            else:
                seen_keys.add(key)
                deduped.append(entry)
                
        return deduped, num_removed, removed_examples

    lane_a_entries = []
    lane_b_entries = []
    lane_c_entries = []
    do_not_promote_entries = []

    # A. Real-money eligible
    for row in kelly_stakeable:
        lane_a_entries.append(_create_candidate_entry(row))

    # B. Manual review candidates
    # Near elite
    if not near_elite_df.empty:
        for _, row in near_elite_df.iterrows():
            lane_b_entries.append(_create_candidate_entry(row, "[Near-Elite] "))
    # Same-opponent warnings
    for df in (full_market_df, elite_df):
        if not df.empty and "same_opponent_under_warning" in df.columns:
            for _, row in df.iterrows():
                if _is_truthy(row.get("same_opponent_under_warning")):
                    lane_b_entries.append(_create_candidate_entry(row, "[Same-Opponent] "))

    # C. Research-only candidates
    if not shadow_lane_df.empty:
        for _, row in shadow_lane_df.iterrows():
            lane = _clean_str(row.get("research_lane"))
            if lane in ("UNDER_ALIGNED_RESEARCH", "COMBO_OVER_WEAK_POSITIVE_RESEARCH", "INCUBATOR_RESEARCH", "HIGH_CAUTION_OVER_DO_NOT_PROMOTE"):
                lane_c_entries.append(_create_candidate_entry(row, f"[{lane}] "))
    if not incubator_df.empty:
        for _, row in incubator_df.iterrows():
            lane_c_entries.append(_create_candidate_entry(row, "[INCUBATOR_RESEARCH] "))

    # Do Not Promote Section
    # High-caution OVERs
    for df in (full_market_df, elite_df):
        if not df.empty and "selection" in df.columns and "context_caution_level" in df.columns:
            for _, row in df.iterrows():
                if _clean_str(row.get("selection")).lower() == "over" and _clean_str(row.get("context_caution_level")).lower() == "high":
                    do_not_promote_entries.append(_create_candidate_entry(row, "High-caution OVER: "))

    # Shadow-only rows
    if not shadow_lane_df.empty:
        for _, row in shadow_lane_df.iterrows():
            if _is_true(row.get("shadow_only", True)):
                do_not_promote_entries.append(_create_candidate_entry(row, "Shadow-only row: "))

    # Source date mismatch rows
    if not shadow_lane_df.empty:
        for _, row in shadow_lane_df.iterrows():
            row_pred_date = _clean_str(row.get("prediction_date", ""))
            row_source_date = _clean_str(row.get("source_artifact_date", ""))
            if row_pred_date != prediction_date or row_source_date != prediction_date:
                do_not_promote_entries.append(_create_candidate_entry(row, f"Date Mismatch row (pred={row_pred_date}, source={row_source_date}): "))

    # Candidates with real_money_eligible=False, kelly_eligible=False, elite_eligible=False
    if not shadow_lane_df.empty:
        for _, row in shadow_lane_df.iterrows():
            real_money = row.get("real_money_eligible", False)
            kelly = row.get("kelly_eligible", False)
            elite = row.get("elite_eligible", False)
            if _is_false(real_money) and _is_false(kelly) and _is_false(elite):
                do_not_promote_entries.append(_create_candidate_entry(row, "Research-only Flags: "))

    # Candidates from paper-only histories (e.g. from incubator_board / shadow candidate lane)
    if not incubator_df.empty:
        for _, row in incubator_df.iterrows():
            do_not_promote_entries.append(_create_candidate_entry(row, "Paper-only Incubator Candidate: "))

    # Deduplicate within each lane
    lane_counts_before_dedupe = {
        "real_money_eligible": len(lane_a_entries),
        "manual_review_candidates": len(lane_b_entries),
        "research_only_candidates": len(lane_c_entries),
        "do_not_promote": len(do_not_promote_entries),
    }

    deduped_a, removed_a, examples_a = _deduplicate_lane_entries(lane_a_entries)
    deduped_b, removed_b, examples_b = _deduplicate_lane_entries(lane_b_entries)
    deduped_c, removed_c, examples_c = _deduplicate_lane_entries(lane_c_entries)
    deduped_dnp, removed_dnp, examples_dnp = _deduplicate_lane_entries(do_not_promote_entries)

    lane_counts_after_dedupe = {
        "real_money_eligible": len(deduped_a),
        "manual_review_candidates": len(deduped_b),
        "research_only_candidates": len(deduped_c),
        "do_not_promote": len(deduped_dnp),
    }

    total_removed = removed_a + removed_b + removed_c + removed_dnp
    
    duplicate_candidate_examples = []
    for ex in (examples_a + examples_b + examples_c + examples_dnp):
        if ex not in duplicate_candidate_examples:
            duplicate_candidate_examples.append(ex)

    # Format final lists of strings
    lanes = {
        "real_money_eligible": [e["formatted"] for e in deduped_a],
        "manual_review_candidates": [e["formatted"] for e in deduped_b],
        "research_only_candidates": [e["formatted"] for e in deduped_c],
    }
    do_not_promote = [e["formatted"] for e in deduped_dnp]

    # Strict Safety Declarations
    safety_declarations = [
        "This report does not create bets.",
        "This report does not change final_decision.",
        "This report does not write to pick_history.csv.",
        "This report does not promote UNDERs.",
        "This report does not alter Elite/Kelly logic.",
        "UNDER visibility board is shadow-only; it does not increase betability.",
    ]

    # Phase 6B.1 — UNDER visibility board context (research-only, never affects betability)
    under_vis_board_row_count = int(under_vis_board_data.get("board_row_count", 0)) if under_vis_board_data else 0
    under_vis_board_present = bool(under_vis_board_data)

    # Build TXT report content
    txt_report = f"""CourtVision Bet Readiness Report - {prediction_date}

============================================================
Status: {status}
Betability Score: {score}/100
Recommended Action: {recommended_action}
============================================================

Required Artifacts:
--------------------------------------------
"""
    for name, path in required_paths.items():
        exists_str = "OK" if path.exists() else "MISSING"
        txt_report += f"- {path.name}: {exists_str}\n"

    txt_report += """
Blockers Checklist:
--------------------------------------------
"""
    if blockers:
        for b in blockers:
            txt_report += f"- [BLOCKED] {b}\n"
    else:
        txt_report += "- None\n"

    txt_report += """
Candidates Lanes:
--------------------------------------------
"""
    txt_report += "\nLane A: Real-money Eligible:\n"
    if lanes["real_money_eligible"]:
        for item in lanes["real_money_eligible"]:
            txt_report += f"  {item}\n"
    else:
        txt_report += "  None\n"

    txt_report += "\nLane B: Manual Review Candidates:\n"
    if lanes["manual_review_candidates"]:
        for item in lanes["manual_review_candidates"]:
            txt_report += f"  {item}\n"
    else:
        txt_report += "  None\n"

    txt_report += "\nLane C: Research-only Candidates:\n"
    if lanes["research_only_candidates"]:
        for item in lanes["research_only_candidates"]:
            txt_report += f"  {item}\n"
    else:
        txt_report += "  None\n"

    txt_report += """
Do Not Promote Section:
--------------------------------------------
"""
    if do_not_promote:
        for item in do_not_promote[:20]:
            txt_report += f"- {item}\n"
    else:
        txt_report += "- None\n"

    txt_report += """
Strict Safety Declarations:
--------------------------------------------
"""
    for dec in safety_declarations:
        txt_report += f"- {dec}\n"

    # Phase 6B.1 — UNDER Visibility Board context (shadow-only; does not change betability)
    txt_report += """
UNDER Visibility Board (Shadow-Only Research):
--------------------------------------------
"""
    if under_vis_board_present:
        lane_counts = under_vis_board_data.get("lane_counts", {})
        txt_report += f"- UNDER board present: {under_vis_board_row_count} candidate(s)\n"
        txt_report += f"  shadow_only: {under_vis_board_data.get('shadow_only', True)}\n"
        txt_report += f"  betting_logic_changed: {under_vis_board_data.get('betting_logic_changed', False)}\n"
        txt_report += f"  real_money_promotion: {under_vis_board_data.get('real_money_promotion', False)}\n"
        for lane_key, lane_n in lane_counts.items():
            txt_report += f"  {lane_key}: {lane_n}\n"
    else:
        txt_report += "- UNDER visibility board not present (shadow-only research context only).\n"
    txt_report += "- UNDER visibility CANNOT increase Lane A or betability score.\n"

    unknown_display_name_examples = list(dict.fromkeys(unknown_players_list))
    unknown_display_name_count = len(unknown_display_name_examples)

    # Build JSON diagnostics payload
    diagnostics_payload = {
        "prediction_date": prediction_date,
        "status": status,
        "score": score,
        "recommended_action": recommended_action,
        "blockers": blockers,
        "lanes": lanes,
        "do_not_promote": do_not_promote,
        "safety_declarations": safety_declarations,
        "unknown_display_name_count": unknown_display_name_count,
        "unknown_display_name_examples": unknown_display_name_examples,
        "duplicate_candidate_rows_removed": total_removed,
        "duplicate_candidate_examples": duplicate_candidate_examples,
        "lane_counts_before_dedupe": lane_counts_before_dedupe,
        "lane_counts_after_dedupe": lane_counts_after_dedupe,
        "metrics": {
            "has_elite_rows": has_elite_rows,
            "has_kelly_rows": has_kelly_rows,
            "manual_review_required": manual_review_required,
            "source_date_mismatch": source_date_mismatch,
            "identity_conflicts": identity_conflicts,
            "is_fresh": is_fresh,
            "is_guard_ready": is_guard_ready,
        },
        # Phase 6B.1 — UNDER visibility board context (shadow-only; does not change betability)
        "under_visibility_board_context": {
            "present": under_vis_board_present,
            "board_row_count": under_vis_board_row_count,
            "shadow_only": True,
            "affects_betability": False,
            "lane_counts": under_vis_board_data.get("lane_counts", {}) if under_vis_board_data else {},
        },
    }

    # Write output files (reporting-only, the ONLY permitted writes)
    report_txt_path = operator_dir / f"bet_readiness_report_{prediction_date}.txt"
    report_json_path = diagnostics_dir / f"bet_readiness_report_{prediction_date}.json"

    try:
        report_txt_path.parent.mkdir(parents=True, exist_ok=True)
        report_txt_path.write_text(txt_report, encoding="utf-8")

        report_json_path.parent.mkdir(parents=True, exist_ok=True)
        report_json_path.write_text(json.dumps(diagnostics_payload, indent=2), encoding="utf-8")
    except Exception as e:
        return 2, {"error": f"Failed to write outputs: {e}"}, ""

    exit_code = 0
    if strict and status != "BETTABLE":
        exit_code = 1

    return exit_code, diagnostics_payload, txt_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CourtVision Bet Readiness Report.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument("--strict", action="store_true", help="Escalate exit code if status is not BETTABLE.")
    parser.add_argument("--json", action="store_true", help="Print structured JSON result to stdout.")

    args = parser.parse_args(argv)

    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
        strict=args.strict,
    )

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(report)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
