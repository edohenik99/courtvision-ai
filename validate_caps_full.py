#!/usr/bin/env python
"""Full validation of elite board caps and directional integrity."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

DATE = "2026-04-23"  # Using available date
BASE = Path("outputs") / "runtime" / "operator"

elite_csv = BASE / f"elite_board_{DATE}.csv"
summary_json = BASE / f"elite_pipeline_audit_summary_{DATE}.json"

if not elite_csv.exists():
    raise FileNotFoundError(f"Missing elite board: {elite_csv}")

df = pd.read_csv(elite_csv)

print(f"\n=== Elite Board Validation: {elite_csv.name} ===")
print(f"Rows: {len(df)}")
print(f"Columns: {list(df.columns)}")

# Normalize likely column names
game_col = "game_id" if "game_id" in df.columns else None
team_col = "team_abbr" if "team_abbr" in df.columns else ("team" if "team" in df.columns else None)
edge_col = "edge" if "edge" in df.columns else ("edge_pct" if "edge_pct" in df.columns else None)
sel_col = "selection" if "selection" in df.columns else None

if team_col:
    team_counts = df.groupby(team_col).size().sort_values(ascending=False)
    print("\nTeam exposure:")
    print(team_counts.to_string())
    print(f"max_team_exposure = {int(team_counts.max())}")
else:
    print("\nNo team column found.")

if game_col:
    game_counts = df.groupby(game_col).size().sort_values(ascending=False)
    print("\nGame exposure:")
    print(game_counts.to_string())
    print(f"max_game_exposure = {int(game_counts.max())}")
else:
    print("\nNo game_id column found.")

if sel_col:
    side_counts = df.groupby(sel_col).size().sort_values(ascending=False)
    print("\nSelection mix:")
    print(side_counts.to_string())

if edge_col:
    print("\nEdge stats:")
    print(f"avg_edge     = {df[edge_col].mean():.4f}")
    print(f"avg_abs_edge = {df[edge_col].abs().mean():.4f}")

# Directional integrity check
violations = []
if sel_col and edge_col:
    for _, row in df.iterrows():
        selection = str(row[sel_col]).strip().lower()
        edge = float(row[edge_col])
        player = row.get("player_name", row.get("player", "unknown"))
        if selection == "over" and edge <= 0:
            violations.append(f"{player}: over with edge {edge}")
        elif selection == "under" and edge >= 0:
            violations.append(f"{player}: under with edge {edge}")

    print("\nDirectional validation:")
    if violations:
        print(f"FAILED: {len(violations)} violations")
        for v in violations[:10]:
            print(f"  - {v}")
    else:
        print("PASSED")

# Compare against summary artifact if present
if summary_json.exists():
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    summary = payload.get("summary", {})
    board_analytics = summary.get("board_analytics", {})

    print(f"\n=== Summary Cross-Check: {summary_json.name} ===")
    print("summary keys:", list(summary.keys()))
    print("board_analytics:", board_analytics)

    def show_compare(name: str, actual):
        reported = board_analytics.get(name, summary.get(name))
        print(f"{name}: actual={actual} reported={reported}")

    if game_col:
        show_compare("max_game_exposure", int(df.groupby(game_col).size().max()))
        show_compare("unique_games", int(df[game_col].nunique()))
    if team_col:
        show_compare("max_team_exposure", int(df.groupby(team_col).size().max()))
        show_compare("unique_teams", int(df[team_col].nunique()))
    if sel_col:
        show_compare("overs_count", int((df[sel_col].astype(str).str.lower() == "over").sum()))
        show_compare("unders_count", int((df[sel_col].astype(str).str.lower() == "under").sum()))
    show_compare("elite_count", int(len(df)))
else:
    print(f"\nNo summary JSON found: {summary_json}")
