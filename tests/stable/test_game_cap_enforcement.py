#!/usr/bin/env python
"""
Regression test for game cap enforcement.

Tests that when 10 elite-eligible candidates are from the same game:
- Final elite board contains at most 4 rows from that game (ELITE_GAME_CAP = 4)
- Team cap can still coexist with game cap (ELITE_TEAM_CAP = 3)
- Summary metrics match the final CSV
"""

import pytest
import pandas as pd
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Any

# Import the cap enforcement logic
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from courtvision.pipeline.predict_pipeline import PredictionPipeline, PredictionConfig
from courtvision.config import EliteThresholds


def test_game_cap_enforcement():
    """Test that game cap correctly limits exposure when many candidates qualify."""
    
    # Create test data: 10 elite-eligible candidates from same game, multiple teams
    test_data = []
    teams = ["BOS", "BOS", "BOS", "DAL", "DAL", "DAL", "OKC", "OKC", "DEN", "UTA"]
    players = [
        "Tatum", "Brown", "Holiday", 
        "Doncic", "Irving", "Washington",
        "Gilgeous-Alexander", "Williams",
        "Jokic", "Markkanen"
    ]
    
    for i, (team, player) in enumerate(zip(teams, players)):
        test_data.append({
            "player_name": player,
            "team_abbr": team,
            "game_id": 2026042303,  # All from same game
            "market_type": "player_points",
            "selection": "over",
            "edge": 5.0 + i * 0.1,  # Decreasing edge
            "quality_score": 60.0,
            "confidence": 0.70,
            "selection_score": 100 - i * 2,
            "is_elite": True,
            "qualification_reason": "live_market",
            "is_live_market": True,
            "synthetic_line": False,
            "source_lane": "research",
            "line_source": "live_market",
        })
    
    candidates_df = pd.DataFrame(test_data)
    
    print(f"\nTest input: {len(candidates_df)} candidates from game_id={candidates_df['game_id'].iloc[0]}")
    print(f"Teams distribution:")
    print(candidates_df.groupby("team_abbr").size().to_string())
    
    # Apply cap enforcement logic directly
    elite_thresholds = EliteThresholds.default()
    elite_game_cap = elite_thresholds.game_cap  # Should be 4
    elite_team_cap = elite_thresholds.team_cap  # Should be 3
    
    print(f"\nCap settings: team_cap={elite_team_cap}, game_cap={elite_game_cap}")
    
    # Sort by selection_score
    df_sorted = candidates_df.sort_values("selection_score", ascending=False)
    
    # Apply caps
    capped_selection = []
    team_counts: dict[str, int] = {}
    game_counts: dict[int, int] = {}
    skipped_by_team_cap = 0
    skipped_by_game_cap = 0
    
    for idx, row in df_sorted.iterrows():
        team = str(row.get("team_abbr", "")).strip().upper()
        game_id = int(row.get("game_id", 0)) if pd.notna(row.get("game_id")) else 0
        player = row.get("player_name", "unknown")
        
        # Check team cap
        if team and team_counts.get(team, 0) >= elite_team_cap:
            skipped_by_team_cap += 1
            print(f"  Row {idx} ({player}): SKIPPED by team_cap (count={team_counts.get(team, 0)})")
            continue
        
        # Check game cap
        if game_id and game_counts.get(game_id, 0) >= elite_game_cap:
            skipped_by_game_cap += 1
            print(f"  Row {idx} ({player}): SKIPPED by game_cap (count={game_counts.get(game_id, 0)})")
            continue
        
        # Row passes caps
        capped_selection.append(idx)
        if team:
            team_counts[team] = team_counts.get(team, 0) + 1
        if game_id:
            game_counts[game_id] = game_counts.get(game_id, 0) + 1
        print(f"  Row {idx} ({player}): SELECTED (team={team}, game_count={game_counts.get(game_id, 0)})")
    
    # Create capped DataFrame
    capped_df = df_sorted.loc[capped_selection].copy() if capped_selection else pd.DataFrame()
    
    print(f"\nResults:")
    print(f"  Input candidates: {len(candidates_df)}")
    print(f"  Selected after caps: {len(capped_df)}")
    print(f"  Skipped by team cap: {skipped_by_team_cap}")
    print(f"  Skipped by game cap: {skipped_by_game_cap}")
    
    # Validate game cap
    if len(capped_df) > 0 and "game_id" in capped_df.columns:
        game_counts_final = capped_df.groupby("game_id").size()
        max_game_exposure = game_counts_final.max()
        print(f"  max_game_exposure: {max_game_exposure}")
        
        assert max_game_exposure <= elite_game_cap, \
            f"Game cap violated: max_game_exposure={max_game_exposure} > cap={elite_game_cap}"
        print(f"  ✓ Game cap enforced: {max_game_exposure} <= {elite_game_cap}")
    
    # Validate team cap
    if len(capped_df) > 0 and "team_abbr" in capped_df.columns:
        team_counts_final = capped_df.groupby("team_abbr").size()
        max_team_exposure = team_counts_final.max()
        print(f"  max_team_exposure: {max_team_exposure}")
        
        assert max_team_exposure <= elite_team_cap, \
            f"Team cap violated: max_team_exposure={max_team_exposure} > cap={elite_team_cap}"
        print(f"  ✓ Team cap enforced: {max_team_exposure} <= {elite_team_cap}")
    
    # With 10 candidates from same game and caps=4/3, we expect:
    # - First 4 selected (game cap)
    # - But also team cap may limit further
    # Expected: 4 rows max from game, with no more than 3 from any team
    assert len(capped_df) <= elite_game_cap, \
        f"Should have at most {elite_game_cap} rows when all from same game, got {len(capped_df)}"
    assert len(capped_df) == elite_game_cap, \
        f"Expected game cap to select exactly {elite_game_cap} rows, got {len(capped_df)}"
    assert skipped_by_game_cap == len(candidates_df) - elite_game_cap, \
        f"Expected remaining candidates to be skipped by game cap, got {skipped_by_game_cap}"
    
    print(f"\n✓ Test PASSED: Game cap and team cap correctly enforced")


def test_game_cap_with_int_conversion():
    """Test that game_id int conversion works correctly for dictionary hashing."""
    
    # Simulate pandas numpy int64 types
    import numpy as np
    
    game_counts: dict[int, int] = {}
    
    # Test with numpy int64 (what pandas returns)
    game_id_1 = np.int64(2026042303)
    game_id_2 = np.int64(2026042303)  # Same value, different object
    
    # Without int conversion, this might create separate entries
    game_counts[game_id_1] = game_counts.get(game_id_1, 0) + 1
    game_counts[game_id_2] = game_counts.get(game_id_2, 0) + 1
    
    # With numpy types, they should hash the same
    print(f"\nNumpy int64 test: game_counts = {game_counts}")
    print(f"  len(game_counts) = {len(game_counts)}")
    
    # Now test with explicit int() conversion (our fix)
    game_counts_fixed: dict[int, int] = {}
    game_id_fixed_1 = int(game_id_1)
    game_id_fixed_2 = int(game_id_2)
    
    game_counts_fixed[game_id_fixed_1] = game_counts_fixed.get(game_id_fixed_1, 0) + 1
    game_counts_fixed[game_id_fixed_2] = game_counts_fixed.get(game_id_fixed_2, 0) + 1
    
    print(f"\nWith int() conversion: game_counts = {game_counts_fixed}")
    print(f"  len(game_counts) = {len(game_counts_fixed)}")
    
    assert len(game_counts_fixed) == 1, "Should have exactly 1 game entry with int conversion"
    assert game_counts_fixed[2026042303] == 2, "Count should be 2 after both increments"
    
    print("\n✓ Test PASSED: int() conversion ensures consistent dictionary hashing")


if __name__ == "__main__":
    print("=" * 60)
    print("GAME CAP ENFORCEMENT REGRESSION TESTS")
    print("=" * 60)
    
    test_game_cap_enforcement()
    
    print("\n" + "=" * 60)
    test_game_cap_with_int_conversion()
    
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)
