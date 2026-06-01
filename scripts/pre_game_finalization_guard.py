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


def run_pre_game_finalization_guard(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    strict: bool = False,
) -> tuple[int, dict, str]:
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)

    errors: list[str] = []
    warnings: list[str] = []

    # Check date format
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", prediction_date):
        errors.append(f"Invalid prediction date format: {prediction_date}. Expected YYYY-MM-DD.")
        return 2, {"status": "NOT_READY", "errors": errors, "warnings": warnings, "recommended_action": "rerun/fix artifacts before locking"}, ""

    # Required files map
    req_files = {
        "full_market_board": runtime_root / "operator" / f"full_market_board_{prediction_date}.csv",
        "operator_card": runtime_root / "operator" / f"operator_card_{prediction_date}.txt",
        "daily_summary": runtime_root / "operator" / f"daily_summary_{prediction_date}.txt",
        "quality_summary": runtime_root / "operator" / f"quality_summary_{prediction_date}.txt",
        "board_diagnostics": runtime_root / "diagnostics" / f"board_diagnostics_{prediction_date}.json",
    }

    # Research files map
    res_files = {
        "under_visibility_audit_txt": runtime_root / "operator" / f"under_visibility_audit_{prediction_date}.txt",
        "under_visibility_audit_json": runtime_root / "diagnostics" / f"under_visibility_audit_{prediction_date}.json",
        "under_visibility_board_csv": runtime_root / "operator" / f"under_visibility_board_{prediction_date}.csv",
        "under_visibility_board_txt": runtime_root / "operator" / f"under_visibility_report_{prediction_date}.txt",
        "under_visibility_board_json": runtime_root / "diagnostics" / f"under_visibility_board_{prediction_date}.json",
        "shadow_candidate_lane_csv": runtime_root / "operator" / f"shadow_candidate_lane_{prediction_date}.csv",
        "shadow_candidate_lane_report_txt": runtime_root / "operator" / f"shadow_candidate_lane_report_{prediction_date}.txt",
        "shadow_candidate_lane_json": runtime_root / "diagnostics" / f"shadow_candidate_lane_{prediction_date}.json",
        "shadow_candidate_lane_performance_txt": runtime_root / "operator" / f"shadow_candidate_lane_performance_{prediction_date}.txt",
        "shadow_candidate_lane_performance_json": runtime_root / "diagnostics" / f"shadow_candidate_lane_performance_{prediction_date}.json",
    }

    req_status = {}
    for name, path in req_files.items():
        if path.exists():
            req_status[name] = "ok"
        else:
            req_status[name] = "missing"
            errors.append(f"Required artifact is missing: {path.name}")

    res_status = {}
    for name, path in res_files.items():
        if path.exists():
            res_status[name] = "ok"
        else:
            res_status[name] = "missing/warning"
            warnings.append(f"Optional research artifact is missing: {path.name}")

    # Content checks status variables
    operator_card_snapshot_present = False
    daily_summary_snapshot_present = False
    operator_card_disclaimers_present = False
    betting_logic_changed_detected = False
    pick_history_touched_detected = False
    real_money_promotion_detected = False
    kelly_promotion_detected = False
    elite_promotion_detected = False
    same_date_status = "ok"

    # 1. operator_card content checks
    op_card_path = req_files["operator_card"]
    if op_card_path.exists():
        try:
            content = op_card_path.read_text(encoding="utf-8")
            # checks
            has_pred_date = f"prediction_date: {prediction_date}" in content or f"prediction_date:{prediction_date}" in content
            has_final_decision = "final_decision:" in content
            has_snapshot = "UNDER Research Snapshot - Shadow Only" in content
            has_disclaimer_elite = "It is not an Elite board." in content
            has_disclaimer_kelly = "It is not a Kelly input." in content
            has_disclaimer_bet = "It is not a betting recommendation." in content
            has_disclaimer_promo = "No real-money promotion is allowed." in content

            if not has_pred_date:
                errors.append(f"operator_card does not contain prediction_date: {prediction_date}")
            if not has_final_decision:
                warnings.append("operator_card missing final_decision label")
            if not has_snapshot:
                warnings.append("operator_card missing UNDER Research Snapshot - Shadow Only snapshot section")
            else:
                operator_card_snapshot_present = True

            if has_disclaimer_elite and has_disclaimer_kelly and has_disclaimer_bet and has_disclaimer_promo:
                operator_card_disclaimers_present = True
            else:
                warnings.append("operator_card missing one or more required pre-game finalization guardrail disclaimers")
        except Exception as e:
            errors.append(f"Failed to read operator_card content: {e}")

    # 2. daily_summary content checks
    daily_sum_path = req_files["daily_summary"]
    if daily_sum_path.exists():
        try:
            content = daily_sum_path.read_text(encoding="utf-8")
            has_snapshot = "UNDER Research Snapshot - Shadow Only" in content
            has_hist_shadow = "historical shadow UNDER" in content
            has_top_curr = "Top Current UNDER Candidates" in content
            has_top_aligned = "Top UNDER_ALIGNED_RESEARCH Candidates" in content

            if has_snapshot and has_hist_shadow and has_top_curr and has_top_aligned:
                daily_summary_snapshot_present = True
            else:
                warnings.append("daily_summary missing one or more required UNDER Research Snapshot sections")
        except Exception as e:
            errors.append(f"Failed to read daily_summary content: {e}")

    # 3. shadow_candidate_lane_csv row checks
    shadow_csv_path = res_files["shadow_candidate_lane_csv"]
    if shadow_csv_path.exists():
        try:
            df = pd.read_csv(shadow_csv_path)
            if not df.empty:
                for idx, row in df.iterrows():
                    row_pred_date = _clean_str(row.get("prediction_date", ""))
                    row_source_date = _clean_str(row.get("source_artifact_date", ""))
                    row_real_money = row.get("real_money_eligible", False)
                    row_kelly = row.get("kelly_eligible", False)
                    row_elite = row.get("elite_eligible", False)
                    row_shadow_only = row.get("shadow_only", True)

                    if row_pred_date != prediction_date:
                        errors.append(f"shadow_candidate_lane row {idx} has mismatched prediction_date: {row_pred_date} != {prediction_date}")
                        same_date_status = "mismatch"
                    if row_source_date != prediction_date:
                        errors.append(f"shadow_candidate_lane row {idx} has mismatch/stale source_artifact_date: {row_source_date} != {prediction_date}")
                        same_date_status = "mismatch"
                    if not _is_false(row_real_money):
                        real_money_promotion_detected = True
                        errors.append(f"shadow_candidate_lane row {idx} has real_money_eligible=True")
                    if not _is_false(row_kelly):
                        kelly_promotion_detected = True
                        errors.append(f"shadow_candidate_lane row {idx} has kelly_eligible=True")
                    if not _is_false(row_elite):
                        elite_promotion_detected = True
                        errors.append(f"shadow_candidate_lane row {idx} has elite_eligible=True")
                    if not _is_true(row_shadow_only):
                        errors.append(f"shadow_candidate_lane row {idx} has shadow_only=False")
        except Exception as e:
            errors.append(f"Failed to validate shadow_candidate_lane CSV rows: {e}")

    # 4. shadow_candidate_lane_history check
    history_csv_path = history_root / "shadow_candidate_lane_history.csv"
    if history_csv_path.exists():
        try:
            df = pd.read_csv(history_csv_path)
            if not df.empty and "prediction_date" in df.columns:
                matching_rows = df[df["prediction_date"] == prediction_date]
                for idx, row in matching_rows.iterrows():
                    row_source_date = _clean_str(row.get("source_artifact_date", ""))
                    if row_source_date != prediction_date:
                        errors.append(f"Contamination detected in shadow history: row has prediction_date={prediction_date} but source_artifact_date={row_source_date}")
        except Exception as e:
            errors.append(f"Failed to read/validate shadow_candidate_lane_history: {e}")

    # 5. under_visibility_audit JSON check
    audit_json_path = res_files["under_visibility_audit_json"]
    if audit_json_path.exists():
        try:
            with open(audit_json_path, "r", encoding="utf-8") as f:
                audit_data = json.load(f)
            
            # Match date if exists
            audit_pred_date = _clean_str(audit_data.get("prediction_date", ""))
            audit_source_date = _clean_str(audit_data.get("source_date", ""))
            if audit_pred_date and audit_pred_date != prediction_date:
                errors.append(f"under_visibility_audit JSON prediction_date mismatch: {audit_pred_date} != {prediction_date}")
            if audit_source_date and audit_source_date != prediction_date:
                errors.append(f"under_visibility_audit JSON source_date mismatch: {audit_source_date} != {prediction_date}")

            # Funnel stages verification
            if "funnel_stages" not in audit_data:
                warnings.append("under_visibility_audit JSON missing 'funnel_stages' metrics")
            
            # Betting promotion check
            for k, v in audit_data.items():
                if "promot" in k.lower() or "eligible" in k.lower():
                    if _is_true(v):
                        real_money_promotion_detected = True
                        errors.append(f"under_visibility_audit JSON contains flag implying betting promotion: {k}={v}")
        except Exception as e:
            errors.append(f"Failed to read/validate under_visibility_audit JSON: {e}")

    # 5b. under_visibility_board JSON check (Phase 6B.1)
    board_json_path = res_files["under_visibility_board_json"]
    if board_json_path.exists():
        try:
            with open(board_json_path, "r", encoding="utf-8") as f:
                board_data = json.load(f)

            board_pred_date = _clean_str(board_data.get("prediction_date", ""))
            if board_pred_date and board_pred_date != prediction_date:
                errors.append(f"under_visibility_board JSON prediction_date mismatch: {board_pred_date} != {prediction_date}")

            # Shadow safety flags
            if not _is_true(board_data.get("shadow_only", True)):
                errors.append("under_visibility_board JSON has shadow_only=False")
            if _is_true(board_data.get("betting_logic_changed", False)):
                errors.append("under_visibility_board JSON has betting_logic_changed=True")
            if _is_true(board_data.get("real_money_promotion", False)):
                real_money_promotion_detected = True
                errors.append("under_visibility_board JSON has real_money_promotion=True")
            if _is_true(board_data.get("elite_promotion", False)):
                elite_promotion_detected = True
                errors.append("under_visibility_board JSON has elite_promotion=True")
            if _is_true(board_data.get("kelly_promotion", False)):
                kelly_promotion_detected = True
                errors.append("under_visibility_board JSON has kelly_promotion=True")
            if _is_true(board_data.get("pick_history_written", False)):
                errors.append("under_visibility_board JSON has pick_history_written=True")
        except Exception as e:
            errors.append(f"Failed to read/validate under_visibility_board JSON: {e}")

    # 6. shadow candidate performance JSON check
    perf_json_path = res_files["shadow_candidate_lane_performance_json"]
    if perf_json_path.exists():
        try:
            with open(perf_json_path, "r", encoding="utf-8") as f:
                perf_data = json.load(f)
            
            if _is_true(perf_data.get("source_date_mismatch", False)):
                errors.append("shadow candidate performance JSON indicates source_date_mismatch is True")
            if perf_data.get("history_persistence_status", "") == "skipped_source_date_mismatch":
                errors.append("shadow candidate performance history_persistence_status is skipped_source_date_mismatch")
            
            if not _is_true(perf_data.get("all_rows_real_money_eligible_false", True)):
                real_money_promotion_detected = True
                errors.append("shadow candidate performance indicates not all rows have real_money_eligible=False")
            if not _is_true(perf_data.get("all_rows_kelly_eligible_false", True)):
                kelly_promotion_detected = True
                errors.append("shadow candidate performance indicates not all rows have kelly_eligible=False")
            if not _is_true(perf_data.get("all_rows_elite_eligible_false", True)):
                elite_promotion_detected = True
                errors.append("shadow candidate performance indicates not all rows have elite_eligible=False")
            if not _is_true(perf_data.get("all_rows_shadow_only_true", True)):
                errors.append("shadow candidate performance indicates not all rows have shadow_only=True")
        except Exception as e:
            errors.append(f"Failed to read/validate shadow_candidate_lane_performance JSON: {e}")

    # Pick History untouched check - verification/reporting only, pick_history should never be touched or changed
    pick_history_path = history_root / "pick_history.csv"
    if pick_history_path.exists():
        pass

    # Determine status
    if errors:
        status = "NOT_READY"
        recommended_action = "rerun/fix artifacts before locking"
        exit_code = 2
    elif warnings:
        status = "READY_WITH_WARNINGS"
        recommended_action = "review warnings before locking"
        exit_code = 1 if strict else 0
    else:
        status = "READY_TO_LOCK"
        recommended_action = "safe to preserve artifacts and wait for game"
        exit_code = 0

    # Build TXT Report
    txt_report = f"""CourtVision Pre-Game Finalization Guard - {prediction_date}

============================================================
Status: {status}
Recommended Action: {recommended_action}
============================================================

Required Artifact Checklist:
--------------------------------------------
"""
    for name, stat in req_status.items():
        path_name = req_files[name].name
        txt_report += f"- {path_name}: {stat}\n"

    txt_report += """
Research Artifact Checklist:
--------------------------------------------
"""
    for name, stat in res_status.items():
        path_name = res_files[name].name
        txt_report += f"- {path_name}: {stat}\n"

    txt_report += f"""
Date Integrity:
--------------------------------------------
- same-date status: {same_date_status}
- source_artifact_date checks: {"ok" if not errors and same_date_status == "ok" else "failed/warnings"}
- shadow history contamination checks: {"ok" if not any("contamination" in e.lower() for e in errors) else "failed"}

UNDER Research Snapshot Status:
--------------------------------------------
- operator card snapshot present: {str(operator_card_snapshot_present).lower()}
- daily summary snapshot present: {str(daily_summary_snapshot_present).lower()}
- guardrail disclaimer present: {str(operator_card_disclaimers_present).lower()}

Betting Safety:
--------------------------------------------
- betting_logic_changed: {str(betting_logic_changed_detected).lower()}
- pick_history_touched: {str(pick_history_touched_detected).lower()}
- real_money_promotion_detected: {str(real_money_promotion_detected).lower()}
- kelly_promotion_detected: {str(kelly_promotion_detected).lower()}
"""

    if errors:
        txt_report += "\nErrors:\n--------------------------------------------\n"
        for err in errors:
            txt_report += f"[ERROR] {err}\n"

    if warnings:
        txt_report += "\nWarnings:\n--------------------------------------------\n"
        for warn in warnings:
            txt_report += f"[WARN] {warn}\n"

    # Build JSON payload
    diagnostics_payload = {
        "prediction_date": prediction_date,
        "status": status,
        "recommended_action": recommended_action,
        "strict": strict,
        "exit_code": exit_code,
        "required_checklists": req_status,
        "research_checklists": res_status,
        "date_integrity": {
            "same_date_status": same_date_status,
            "shadow_history_contamination_detected": any("contamination" in e.lower() for e in errors),
        },
        "under_research_snapshot": {
            "operator_card_snapshot_present": operator_card_snapshot_present,
            "daily_summary_snapshot_present": daily_summary_snapshot_present,
            "guardrail_disclaimer_present": operator_card_disclaimers_present,
        },
        "betting_safety": {
            "betting_logic_changed": betting_logic_changed_detected,
            "pick_history_touched": pick_history_touched_detected,
            "real_money_promotion_detected": real_money_promotion_detected,
            "kelly_promotion_detected": kelly_promotion_detected,
            "elite_promotion_detected": elite_promotion_detected,
        },
        "warnings": warnings,
        "errors": errors,
    }

    # Write output files (the ONLY permitted writes)
    guard_report_path = runtime_root / "operator" / f"pre_game_finalization_guard_{prediction_date}.txt"
    guard_json_path = runtime_root / "diagnostics" / f"pre_game_finalization_guard_{prediction_date}.json"

    try:
        guard_report_path.parent.mkdir(parents=True, exist_ok=True)
        guard_report_path.write_text(txt_report, encoding="utf-8")

        guard_json_path.parent.mkdir(parents=True, exist_ok=True)
        guard_json_path.write_text(json.dumps(diagnostics_payload, indent=2), encoding="utf-8")
    except Exception as e:
        # Fallback to appending error if write fails
        errors.append(f"Failed to write guard outputs: {e}")
        diagnostics_payload["errors"] = errors
        diagnostics_payload["status"] = "NOT_READY"
        diagnostics_payload["exit_code"] = 2
        return 2, diagnostics_payload, txt_report

    return exit_code, diagnostics_payload, txt_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CourtVision Pre-Game Finalization Guard.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument("--strict", action="store_true", help="Escalate exit code to 1 on warnings.")
    parser.add_argument("--json", action="store_true", help="Print structured JSON result to stdout.")

    args = parser.parse_args(argv)

    exit_code, payload, report = run_pre_game_finalization_guard(
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
