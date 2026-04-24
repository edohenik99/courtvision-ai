"""
Live verification script for elite board directional consistency.

Usage:
    python scripts/verify_elite_output.py <elite_board_csv>
    
Example:
    python scripts/verify_elite_output.py outputs/boards/2026-04-19/elite.csv
"""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd


def _display_name(row: pd.Series) -> str:
    for key in ("player_name", "entity_name", "player"):
        v = row.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return "unknown"


def _row_edge(row: pd.Series) -> float:
    raw = row.get("edge")
    if raw is not None and str(raw).strip() != "" and str(raw).lower() != "nan":
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    raw_pct = row.get("edge_pct")
    if raw_pct is not None and str(raw_pct).strip() != "":
        try:
            return float(raw_pct)
        except (TypeError, ValueError):
            pass
    return 0.0


def verify_elite_directional_consistency(elite_df: pd.DataFrame) -> bool:
    """
    Over bets must have positive edge; under bets must have negative edge
    (same rules as scripts/validate_runtime_outputs.py and legacy run_today.ps1).
    """
    violations: list[str] = []

    for _, row in elite_df.iterrows():
        selection = str(row.get("selection", "")).strip().lower()
        if selection not in ("over", "under"):
            continue
        edge = _row_edge(row)
        player = _display_name(row)

        if selection == "over" and edge <= 0:
            violations.append(f"{player}: over with edge {edge}")
        elif selection == "under" and edge >= 0:
            violations.append(f"{player}: under with edge {edge}")

    if violations:
        print(f"[FAIL] DIRECTIONAL VIOLATIONS FOUND: {len(violations)}")
        for v in violations:
            print(f"  - {v}")
        return False

    print(f"[OK] All {len(elite_df)} rows directionally valid")
    return True


def verify_telemetry_files(slate_date: str, operator_dir: str = "outputs/runtime/operator") -> bool:
    """
    Verify that telemetry files exist and contain expected data.
    
    Returns True if valid, False otherwise.
    """
    op_dir = Path(operator_dir)
    csv_path = op_dir / f"elite_pipeline_audit_{slate_date}.csv"
    json_path = op_dir / f"elite_pipeline_audit_summary_{slate_date}.json"
    
    ok = True
    
    if not csv_path.exists():
        print(f"[FAIL] Telemetry CSV not found: {csv_path}")
        ok = False
    else:
        print(f"[OK] Telemetry CSV found: {csv_path}")
        # Check it has content
        df = pd.read_csv(csv_path)
        if len(df) == 0:
            print("[WARN] Telemetry CSV is empty")
        else:
            print(f"[OK] Telemetry CSV has {len(df)} rows")
    
    if not json_path.exists():
        print(f"[FAIL] Telemetry JSON not found: {json_path}")
        ok = False
    else:
        print(f"[OK] Telemetry JSON found: {json_path}")
    
    return ok


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/verify_elite_output.py <elite_board_csv> [slate_date]")
        print("  elite_board_csv: Path to elite board CSV file")
        print("  slate_date: Optional slate date (e.g., 2026-04-19) for telemetry verification")
        return 1

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"File not found: {csv_path}")
        return 1

    print(f"\n=== Verifying Elite Board: {csv_path} ===\n")
    
    elite_df = pd.read_csv(csv_path)
    ok = verify_elite_directional_consistency(elite_df)
    
    # Also verify telemetry if slate date provided
    if len(sys.argv) >= 3:
        slate_date = sys.argv[2]
        print(f"\n=== Verifying Telemetry for {slate_date} ===\n")
        ok = verify_telemetry_files(slate_date) and ok
    
    print()
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
