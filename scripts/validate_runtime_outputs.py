#!/usr/bin/env python3
"""Validate runtime operator outputs for a slate date (caps, directional edges, preview).

Used by run_today.ps1 instead of fragile inline Python.

Usage:
    python scripts/validate_runtime_outputs.py <YYYY-MM-DD>
    python scripts/validate_runtime_outputs.py <YYYY-MM-DD> --grading-summary
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


def _operator_dir() -> Path:
    return Path("outputs") / "runtime" / "operator"


def _history_dir() -> Path:
    return Path("outputs") / "runtime" / "history"


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


def directional_violations(elite_df: pd.DataFrame) -> list[str]:
    """Over must have positive edge; under must have negative edge (same rules as legacy run_today.ps1)."""
    violations: list[str] = []
    for _, row in elite_df.iterrows():
        selection = str(row.get("selection", "")).strip().lower()
        if selection not in ("over", "under"):
            continue
        edge = _row_edge(row)
        name = _display_name(row)
        if selection == "over" and edge <= 0:
            violations.append(f"{name}: over with edge {edge}")
        elif selection == "under" and edge >= 0:
            violations.append(f"{name}: under with edge {edge}")
    return violations


def load_audit_summary(prediction_date: str) -> dict:
    path = _operator_dir() / f"elite_pipeline_audit_summary_{prediction_date}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Audit summary not found: {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def print_cap_enforcement(doc: dict) -> tuple[int, int, bool]:
    inner = doc.get("summary") or {}
    ba = inner.get("board_analytics") or {}
    max_team = ba.get("max_team_exposure", inner.get("elite_max_team_exposure"))
    max_game = ba.get("max_game_exposure", inner.get("elite_max_game_exposure"))
    if max_team is None:
        max_team = 0
    if max_game is None:
        max_game = 0
    max_team = int(max_team)
    max_game = int(max_game)

    ok_team = max_team <= 3
    ok_game = max_game <= 4
    print("\n  Cap Enforcement:")
    if ok_team:
        print(f"    [OK] max_team_exposure = {max_team} (cap: 3)")
    else:
        print(f"    [FAIL] max_team_exposure = {max_team} (cap: 3) VIOLATION")
    if ok_game:
        print(f"    [OK] max_game_exposure = {max_game} (cap: 4)")
    else:
        print(f"    [FAIL] max_game_exposure = {max_game} (cap: 4) VIOLATION")

    return max_team, max_game, ok_team and ok_game


def print_directional(elite_path: Path) -> bool:
    elite_df = pd.read_csv(elite_path)
    print("\n  Directional Validation:")
    violations = directional_violations(elite_df)
    if violations:
        print(f"    [FAIL] {len(violations)} directional violation(s)")
        for v in violations:
            print(f"      - {v}")
        return False
    print(f"    [OK] All picks pass directional validation ({len(elite_df)} rows)")
    return True


def print_preview(elite_path: Path) -> None:
    df = pd.read_csv(elite_path)
    cols = [c for c in ("player_name", "market_type", "selection", "edge") if c in df.columns]
    if not cols:
        print("\n  Top Picks: (no expected columns for preview)")
        return
    print("\n  Top Picks:")
    preview = df[cols].head().to_string(index=False)
    for line in preview.splitlines():
        print(f"    {line}")


def print_final_summary(doc: dict, max_team: int, max_game: int) -> None:
    inner = doc.get("summary") or {}
    totals = doc.get("totals") or {}
    ba = inner.get("board_analytics") or {}

    provider = inner.get("provider_used") or doc.get("provider_used") or "n/a"
    elite_count = inner.get("elite_count", ba.get("elite_count", "n/a"))
    total_candidates = totals.get("total_candidates", inner.get("candidate_count", "n/a"))
    total_rejections = totals.get("total_rejections", "n/a")

    print("\n========================================")
    print("FINAL SUMMARY")
    print("========================================")
    print(f"  Provider Used:     {provider}")
    print(f"  Elite Count:       {elite_count}")
    print(f"  Total Candidates:  {total_candidates}")
    print(f"  Total Rejected:    {total_rejections}")
    print(f"  Max Team Exposure: {max_team} (cap: 3)")
    print(f"  Max Game Exposure: {max_game} (cap: 4)")


def grading_summary(prediction_date: str) -> int:
    graded = _history_dir() / f"graded_picks_{prediction_date}.csv"
    if not graded.is_file():
        return 0
    df = pd.read_csv(graded)
    if df.empty or "letter_grade" not in df.columns:
        print("    [INFO] Graded file present but no letter_grade column or empty")
        return 0
    hit_rate = (df["letter_grade"].isin(["A", "B"])).mean() * 100
    print(f"    Hit Rate: {hit_rate:.1f}%")
    print(f"    Graded: {len(df)} picks")
    return 0


def validate_outputs(prediction_date: str) -> int:
    elite_path = _operator_dir() / f"elite_board_{prediction_date}.csv"
    if not elite_path.is_file():
        print(f"[ERROR] Elite board not found: {elite_path}", file=sys.stderr)
        return 1

    doc = load_audit_summary(prediction_date)
    max_team, max_game, caps_ok = print_cap_enforcement(doc)
    directional_ok = print_directional(elite_path)
    print_final_summary(doc, max_team, max_game)
    print_preview(elite_path)

    return 0 if caps_ok and directional_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate CourtVision runtime outputs for a date.")
    parser.add_argument("date", help="Prediction date YYYY-MM-DD")
    parser.add_argument(
        "--grading-summary",
        action="store_true",
        help="Print grading hit-rate summary if graded_picks CSV exists.",
    )
    args = parser.parse_args()

    if args.grading_summary:
        return grading_summary(args.date)

    return validate_outputs(args.date)


if __name__ == "__main__":
    raise SystemExit(main())
