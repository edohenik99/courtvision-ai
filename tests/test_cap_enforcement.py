"""Regression test for game and team cap enforcement."""

import sys
sys.path.insert(0, r"c:\dev\Sport_Project1")

import pandas as pd
import pytest
from datetime import date
from courtvision.pipeline.predict_pipeline import _normalize_game_key


def test_game_cap_enforcement():
    """Test that final elite board respects game cap of 4."""
    # Create 8 eligible rows all from same game but DIFFERENT teams
    # Using distinct teams ensures team cap (3) doesn't interfere with game cap (4) test
    rows = []
    teams = ["LAL", "LAL", "GSW", "GSW", "BOS", "BOS", "MIA", "MIA"]  # 4 distinct teams, 2 each
    for i in range(8):
        rows.append({
            "player_id": i + 1,
            "player_name": f"Player{i+1}",
            "game_id": 11018,  # Same game for all rows
            "team": teams[i],
            "team_abbr": teams[i],
            "market_type": "player_points",
            "selection_score": 0.9 - (i * 0.01),  # Descending scores
            "quality_score": 0.85,
        })
    
    df = pd.DataFrame(rows)
    
    # Simulate cap enforcement
    elite_game_cap = 4
    elite_team_cap = 3
    game_counts = {}
    team_counts = {}
    capped_selection = []
    skipped_by_team_cap = 0
    skipped_by_game_cap = 0
    
    # Sort by selection_score
    df_sorted = df.sort_values("selection_score", ascending=False)
    
    for idx, row in df_sorted.iterrows():
        team = row.get("team", "")
        raw_game_id = row.get("game_id", 0)
        game_id = int(raw_game_id) if pd.notna(raw_game_id) and raw_game_id != 0 else 0
        
        # Check team cap
        if team and team_counts.get(team, 0) >= elite_team_cap:
            skipped_by_team_cap += 1
            continue
        # Check game cap
        if game_id and game_counts.get(game_id, 0) >= elite_game_cap:
            skipped_by_game_cap += 1
            continue
        
        # Add to selection
        capped_selection.append(idx)
        if team:
            team_counts[team] = team_counts.get(team, 0) + 1
        if game_id:
            game_counts[game_id] = game_counts.get(game_id, 0) + 1
    
    capped_df = df.loc[capped_selection]
    elite_size = 10
    selected_df = capped_df.head(elite_size)
    
    # Debug output
    print(f"[DEBUG] capped_selection={capped_selection}")
    print(f"[DEBUG] capped_df length={len(capped_df)}")
    print(f"[DEBUG] selected_df length={len(selected_df)}")
    print(f"[DEBUG] team_counts={team_counts}")
    print(f"[DEBUG] game_counts={game_counts}")
    print(f"[DEBUG] skipped_by_team_cap={skipped_by_team_cap}")
    print(f"[DEBUG] skipped_by_game_cap={skipped_by_game_cap}")
    
    # Recalculate max exposure from actual final selection
    final_game_counts = {}
    for _, row in selected_df.iterrows():
        raw_gid = row.get("game_id", 0)
        gid = int(raw_gid) if pd.notna(raw_gid) and raw_gid != 0 else 0
        if gid:
            final_game_counts[gid] = final_game_counts.get(gid, 0) + 1
    
    final_max_game = max(final_game_counts.values()) if final_game_counts else 0
    
    # Assertions
    assert len(selected_df) == 4, f"Expected 4 rows, got {len(selected_df)}"
    assert final_max_game <= 4, f"Game cap violated: {final_max_game} > 4"
    assert skipped_by_game_cap == 4, f"Expected 4 skipped by game cap, got {skipped_by_game_cap}"
    assert skipped_by_team_cap == 0, f"Expected 0 skipped by team cap, got {skipped_by_team_cap}"
    
    # Verify all 4 rows are from the same game (the first 4 that fit in cap)
    assert final_game_counts[11018] == 4
    
    print(f"[CAP_ENFORCE] before_rows=8 after_rows={len(selected_df)} skipped_team_cap={skipped_by_team_cap} skipped_game_cap={skipped_by_game_cap} final_max_game={final_max_game}")
    print("✅ Game cap enforcement test PASSED")


def test_team_cap_enforcement():
    """Test that final elite board respects team cap of 3."""
    # Create test data where team cap (3) is the limiting factor, not game cap
    # Use DIFFERENT game_ids so game cap doesn't interfere
    data = {
        "player_name": ["Player1", "Player2", "Player3", "Player4", "Player5", "Player6", "Player7", "Player8"],
        "game_id": [11018, 11019, 11020, 11021, 11022, 11023, 11024, 11025],  # Each player different game
        "team": ["LAL", "LAL", "LAL", "LAL", "GSW", "GSW", "BOS", "BOS"],  # 3 LAL players to hit team cap
        "opponent": ["OPP"] * 8,
        "market_type": ["player_points"] * 8,
        "selection_score": [0.9, 0.89, 0.88, 0.87, 0.86, 0.85, 0.84, 0.83],
        "is_elite": [True] * 8,
    }
    df = pd.DataFrame(data)
    
    elite_game_cap = 4
    elite_team_cap = 3
    game_counts = {}
    team_counts = {}
    capped_selection = []
    skipped_by_team_cap = 0
    skipped_by_game_cap = 0
    
    df_sorted = df.sort_values("selection_score", ascending=False)
    
    for idx, row in df_sorted.iterrows():
        team = row.get("team", "")
        raw_game_id = row.get("game_id", 0)
        game_id = int(raw_game_id) if pd.notna(raw_game_id) and raw_game_id != 0 else 0
        
        if team and team_counts.get(team, 0) >= elite_team_cap:
            skipped_by_team_cap += 1
            continue
        if game_id and game_counts.get(game_id, 0) >= elite_game_cap:
            skipped_by_game_cap += 1
            continue
        
        capped_selection.append(idx)
        if team:
            team_counts[team] = team_counts.get(team, 0) + 1
        if game_id:
            game_counts[game_id] = game_counts.get(game_id, 0) + 1
    
    capped_df = df.loc[capped_selection]
    selected_df = capped_df.head(10)
    
    final_team_counts = {}
    for _, row in selected_df.iterrows():
        team_abbr = str(row.get("team_abbr", row.get("team", ""))).strip().upper()
        if team_abbr:
            final_team_counts[team_abbr] = final_team_counts.get(team_abbr, 0) + 1
    
    final_max_team = max(final_team_counts.values()) if final_team_counts else 0
    
    # With 3 LAL players and team cap of 3:
    # - First 3 LAL players selected (team cap hit)
    # - 4th LAL player skipped by team cap
    # - Remaining non-LAL players selected (up to elite_size)
    # Total: 3 LAL + 4 others = 7 rows
    assert len(selected_df) == 7, f"Expected 7 rows, got {len(selected_df)}"
    assert final_max_team <= 3, f"Team cap violated: {final_max_team} > 3"
    assert skipped_by_team_cap == 1, f"Expected 1 skipped by team cap, got {skipped_by_team_cap}"
    assert skipped_by_game_cap == 0, f"Expected 0 skipped by game cap, got {skipped_by_game_cap}"
    
    print(f"[CAP_ENFORCE] before_rows=8 after_rows={len(selected_df)} skipped_team_cap={skipped_by_team_cap} skipped_game_cap={skipped_by_game_cap} final_max_team={final_max_team}")
    print("✅ Team cap enforcement test PASSED")


def test_board_analytics_game_key_normalization():
    """Test that _compute_board_analytics uses _normalize_game_key correctly.
    
    When game_id is missing, it should fallback to team@opponent key,
    matching the behavior of cap enforcement logic.
    """
    
    # Create data WITHOUT game_id column - simulates the real scenario where
    # elite board CSV doesn't have game_id but has team/opponent
    data = {
        "team": ["LAL", "LAL", "LAL", "GSW", "GSW", "GSW", "BOS", "BOS"],
        "team_abbr": ["LAL", "LAL", "LAL", "GSW", "GSW", "GSW", "BOS", "BOS"],
        "opponent": ["GSW", "GSW", "GSW", "LAL", "LAL", "LAL", "MIA", "MIA"],
        # NO game_id column - forces fallback to team@opponent
    }
    df = pd.DataFrame(data)
    
    # Calculate game exposure using _normalize_game_key (same as cap enforcement)
    game_keys = [_normalize_game_key(row) for _, row in df.iterrows()]
    game_counts = pd.Series(game_keys).value_counts()
    max_game_exposure = int(game_counts.max()) if not game_counts.empty else 0
    unique_games = int(len(game_counts))
    
    # With team@opponent normalization:
    # - LAL@GSW game: 6 rows (3 LAL vs GSW + 3 GSW vs LAL, but sorted gives LAL@GSW for all)
    # - BOS@MIA game: 2 rows
    # So max_game_exposure should be 6 (all LAL-GSW rows map to same key)
    
    print(f"[ANALYTICS_TEST] game_keys={game_keys}")
    print(f"[ANALYTICS_TEST] game_counts={dict(game_counts)}")
    print(f"[ANALYTICS_TEST] max_game_exposure={max_game_exposure}, unique_games={unique_games}")
    
    # Verify normalization produces consistent keys
    # All LAL vs GSW rows should produce the same key (GSW@LAL since GSW < LAL alphabetically)
    lal_rows = [game_keys[i] for i in range(6)]  # First 6 rows are LAL-GSW matchup
    assert all(k == "GSW@LAL" for k in lal_rows), f"Expected all LAL-GSW rows to have key GSW@LAL, got {lal_rows}"
    
    # All BOS vs MIA rows should produce BOS@MIA (BOS < MIA alphabetically)
    bos_rows = [game_keys[i] for i in range(6, 8)]  # Last 2 rows are BOS-MIA matchup
    assert all(k == "BOS@MIA" for k in bos_rows), f"Expected all BOS-MIA rows to have key BOS@MIA, got {bos_rows}"
    
    # max_game_exposure should be 6 (the LAL@GSW game has 6 rows)
    assert max_game_exposure == 6, f"Expected max_game_exposure=6, got {max_game_exposure}"
    assert unique_games == 2, f"Expected unique_games=2, got {unique_games}"
    
    print("✅ Board analytics game key normalization test PASSED")


def test_board_analytics_with_game_id():
    """Test that _normalize_game_key correctly uses game_id when available."""
    
    # Create data WITH game_id column
    data = {
        "game_id": [101, 101, 101, 102, 102, 102, 103, 103],
        "team": ["LAL", "LAL", "LAL", "GSW", "GSW", "GSW", "BOS", "BOS"],
        "team_abbr": ["LAL", "LAL", "LAL", "GSW", "GSW", "GSW", "BOS", "BOS"],
        "opponent": ["GSW", "GSW", "GSW", "LAL", "LAL", "LAL", "MIA", "MIA"],
    }
    df = pd.DataFrame(data)
    
    # Calculate game exposure using _normalize_game_key
    game_keys = [_normalize_game_key(row) for _, row in df.iterrows()]
    
    # With game_id normalization, each game_id is its own key
    assert game_keys == [101, 101, 101, 102, 102, 102, 103, 103]
    
    game_counts = pd.Series(game_keys).value_counts()
    max_game_exposure = int(game_counts.max()) if not game_counts.empty else 0
    unique_games = int(len(game_counts))
    
    # Each game_id appears 2-3 times
    assert max_game_exposure == 3, f"Expected max_game_exposure=3, got {max_game_exposure}"
    assert unique_games == 3, f"Expected unique_games=3, got {unique_games}"
    
    print("✅ Board analytics with game_id test PASSED")


def test_candidate_scoring_config_matches_elite_thresholds():
    """Test that CandidateScoringConfig defaults mirror EliteThresholds.
    
    This ensures legacy CandidateScoringConfig does not drift from the canonical
    EliteThresholds source, preventing future confusion.
    """
    from courtvision.scoring.candidate_scoring import CandidateScoringConfig
    from courtvision.config import EliteThresholds
    
    config = CandidateScoringConfig()
    elite = EliteThresholds.default()
    
    # Verify all threshold fields match
    assert config.elite_min_confidence == elite.confidence, \
        f"confidence mismatch: config={config.elite_min_confidence}, elite={elite.confidence}"
    assert config.elite_min_quality_score == elite.quality_score, \
        f"quality_score mismatch: config={config.elite_min_quality_score}, elite={elite.quality_score}"
    assert config.elite_min_player_minutes == elite.player_minutes, \
        f"player_minutes mismatch: config={config.elite_min_player_minutes}, elite={elite.player_minutes}"
    assert config.elite_min_player_edge == elite.player_edge, \
        f"player_edge mismatch: config={config.elite_min_player_edge}, elite={elite.player_edge}"
    assert config.elite_min_player_confidence == elite.player_confidence, \
        f"player_confidence mismatch: config={config.elite_min_player_confidence}, elite={elite.player_confidence}"
    assert config.elite_min_moneyline_edge == elite.moneyline_edge, \
        f"moneyline_edge mismatch: config={config.elite_min_moneyline_edge}, elite={elite.moneyline_edge}"
    assert config.elite_min_moneyline_confidence == elite.moneyline_confidence, \
        f"moneyline_confidence mismatch: config={config.elite_min_moneyline_confidence}, elite={elite.moneyline_confidence}"
    assert config.elite_max_plus_moneyline_odds == elite.max_plus_moneyline_odds, \
        f"max_plus_moneyline_odds mismatch: config={config.elite_max_plus_moneyline_odds}, elite={elite.max_plus_moneyline_odds}"
    
    print("✅ CandidateScoringConfig matches EliteThresholds test PASSED")


if __name__ == "__main__":
    test_game_cap_enforcement()
    test_team_cap_enforcement()
    test_board_analytics_game_key_normalization()
    test_board_analytics_with_game_id()
    test_candidate_scoring_config_matches_elite_thresholds()
    print("\n✅ All cap enforcement tests PASSED")
