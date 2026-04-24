#!/usr/bin/env python
"""Validate game/team cap enforcement in elite board."""
import pandas as pd
import json
from pathlib import Path

# Load elite board
elite_file = Path("outputs/runtime/operator/elite_board_2026-04-23.csv")
elite_df = pd.read_csv(elite_file)

print("=" * 60)
print("ELITE BOARD CAP VALIDATION")
print("=" * 60)

# Filter to only elite rows
elite_only = elite_df[elite_df["is_elite"] == True]
print(f"\n1. ELITE COUNT: {len(elite_only)}")
print(f"   Total rows in file: {len(elite_df)}")

if len(elite_only) == 0:
    print("   WARNING: No elite rows found (all is_elite=False)")
    print("   Using all rows for analysis...")
    elite_only = elite_df

# Group by game_id
print(f"\n2. GAME EXPOSURE ANALYSIS:")
game_counts = elite_only.groupby("game_id").size().reset_index(name="player_count")
print(f"   Unique games: {len(game_counts)}")
print(f"   Game exposure distribution:")
for _, row in game_counts.iterrows():
    print(f"     Game {row['game_id']}: {row['player_count']} players")

max_game_exposure = game_counts["player_count"].max()
print(f"\n   MAX GAME EXPOSURE: {max_game_exposure}")
print(f"   ELITE_GAME_CAP: 4")
print(f"   STATUS: {'PASS' if max_game_exposure <= 4 else 'FAIL - CAP EXCEEDED'}")

# Group by team
print(f"\n3. TEAM EXPOSURE ANALYSIS:")
team_counts = elite_only.groupby("team_abbr").size().reset_index(name="player_count")
print(f"   Unique teams: {len(team_counts)}")
print(f"   Team exposure distribution:")
for _, row in team_counts.iterrows():
    print(f"     Team {row['team_abbr']}: {row['player_count']} players")

max_team_exposure = team_counts["player_count"].max()
print(f"\n   MAX TEAM EXPOSURE: {max_team_exposure}")
print(f"   ELITE_TEAM_CAP: 3")
print(f"   STATUS: {'PASS' if max_team_exposure <= 3 else 'FAIL - CAP EXCEEDED'}")

# Load audit summary
print(f"\n4. AUDIT SUMMARY COMPARISON:")
audit_file = Path("outputs/runtime/operator/elite_pipeline_audit_summary_2026-04-23.json")
with open(audit_file) as f:
    audit = json.load(f)

summary = audit.get("summary", {})
board_analytics = summary.get("board_analytics", {})

print(f"   Audit elite_count: {summary.get('elite_count', 'N/A')}")
print(f"   Audit max_game_exposure: {board_analytics.get('max_game_exposure', 'N/A')}")
print(f"   Audit unique_games: {board_analytics.get('unique_games', 'N/A')}")
print(f"   Audit max_team_exposure: {board_analytics.get('max_team_exposure', 'N/A')}")
print(f"   Audit unique_teams: {board_analytics.get('unique_teams', 'N/A')}")

print(f"\n5. CONSISTENCY CHECK:")
csv_max_game = max_game_exposure
csv_unique_games = len(game_counts)
audit_max_game = board_analytics.get('max_game_exposure', 0)
audit_unique_games = board_analytics.get('unique_games', 0)

print(f"   CSV max_game_exposure ({csv_max_game}) vs Audit ({audit_max_game}): {'MATCH' if csv_max_game == audit_max_game else 'MISMATCH'}")
print(f"   CSV unique_games ({csv_unique_games}) vs Audit ({audit_unique_games}): {'MATCH' if csv_unique_games == audit_unique_games else 'MISMATCH'}")

print(f"\n6. SUMMARY:")
print(f"   Game cap enforcement: {'PASS' if max_game_exposure <= 4 else 'FAIL'}")
print(f"   Team cap enforcement: {'PASS' if max_team_exposure <= 3 else 'FAIL'}")
print(f"   CSV vs Audit consistency: {'PASS' if csv_max_game == audit_max_game and csv_unique_games == audit_unique_games else 'FAIL'}")

print("\n" + "=" * 60)
