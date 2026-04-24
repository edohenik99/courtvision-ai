"""
Regression tests for projection market support gating.

Ensures unsupported/under-modeled markets are rejected from live boards.
Tests validate behavior through score_player_markets integration.
"""

import pandas as pd
import pytest
from typing import Any


class TestProjectionMarketSupport:
    """Test that only fully modeled markets enter live boards via integration."""

    def test_player_points_survives_with_real_projection(self):
        """player_points with real projection survives and has modeled status."""
        from courtvision.data.candidates import score_player_markets

        # Setup: player_points with valid odds
        players_df = pd.DataFrame({
            "player_name": ["James Harden"],
            "team_abbr": ["CLE"],
            "player_id": [123],
        })

        odds_df = pd.DataFrame({
            "player_name": ["James Harden"],
            "market": ["player_points"],
            "team": ["CLE"],
            "line": [24.5],
            "over_odds": [-110],
            "under_odds": [-110],
        })

        # Mock build_candidate_row to return valid candidate with real projection
        def mock_build_candidate_row(**kwargs) -> dict[str, Any]:
            return {
                "player_name": "James Harden",
                "team_abbr": "CLE",
                "market_type": "player_points",
                "model_projection": 26.5,  # Real projection
                "projection_support_status": "modeled",
                "edge": 2.0,
                "edge_pct": 8.0,
                "confidence": 0.75,
                "quality_score": 85.0,
                "selection_score": 12.5,
            }

        def mock_reject_candidate(**kwargs) -> dict[str, Any]:
            return {
                "player_name": kwargs.get("player_row", {}).get("player_name", ""),
                "market_type": kwargs.get("market", ""),
                "rejection_reason": kwargs.get("reason", ""),
                "projection_support_status": kwargs.get("projection_support_status", ""),
                "team": kwargs.get("team", ""),
            }

        def mock_score_candidate(**kwargs) -> dict[str, Any] | None:
            # Return the candidate as accepted
            candidate = kwargs.get("candidate_row", {})
            candidate["scored"] = True
            return candidate

        accepted, rejected = score_player_markets(
            players_df=players_df,
            odds_df=odds_df,
            is_player_inactive=lambda x: False,
            build_candidate_row=mock_build_candidate_row,
            score_candidate_fn=mock_score_candidate,
            reject_candidate_fn=mock_reject_candidate,
            allow_partial_fill=False,
        )

        # Assert: should be accepted (not rejected)
        assert len(accepted) == 1, "player_points with real projection should survive"
        assert len(rejected) == 0, "No rejections expected"
        assert accepted[0]["projection_support_status"] == "modeled"
        assert accepted[0]["model_projection"] == 26.5

    def test_unsupported_markets_rejected_via_none_candidate(self):
        """Unsupported markets return None from build_candidate_row and are rejected."""
        from courtvision.data.candidates import score_player_markets

        # Setup: unsupported market
        players_df = pd.DataFrame({
            "player_name": ["Player A"],
            "team_abbr": ["LAL"],
        })

        odds_df = pd.DataFrame({
            "player_name": ["Player A"],
            "market": ["player_3pt_made"],
            "team": ["LAL"],
            "line": [3.5],
            "over_odds": [-110],
            "under_odds": [-110],
        })

        # Mock build_candidate_row returns None (unsupported market)
        def mock_build_candidate_row(**kwargs) -> dict[str, Any] | None:
            return None

        def mock_reject_candidate(**kwargs) -> dict[str, Any]:
            return {
                "player_name": kwargs.get("player_row", {}).get("player_name", ""),
                "market_type": kwargs.get("market", ""),
                "rejection_reason": kwargs.get("reason", ""),
                "projection_support_status": kwargs.get("projection_support_status", ""),
                "team": kwargs.get("team", ""),
            }

        def mock_score_candidate(**kwargs) -> dict[str, Any] | None:
            return None

        accepted, rejected = score_player_markets(
            players_df=players_df,
            odds_df=odds_df,
            is_player_inactive=lambda x: False,
            build_candidate_row=mock_build_candidate_row,
            score_candidate_fn=mock_score_candidate,
            reject_candidate_fn=mock_reject_candidate,
            allow_partial_fill=False,
        )

        # Assert: should be rejected with explicit reason
        assert len(accepted) == 0, "Unsupported market should not be accepted"
        assert len(rejected) == 1, "Should have one rejection"
        assert rejected[0]["rejection_reason"] == "unsupported_projection_market"
        assert rejected[0]["projection_support_status"] == "unsupported_market"

    def test_rejection_reasons_explicit_for_all_unsupported(self):
        """Each unsupported market type gets explicit rejection reason."""
        from courtvision.data.candidates import score_player_markets

        unsupported_markets = [
            "player_rebounds",
            "player_assists",
            "player_3pt_made",
            "player_steals",
            "player_blocks",
        ]

        for market in unsupported_markets:
            players_df = pd.DataFrame({
                "player_name": ["Player A"],
                "team_abbr": ["LAL"],
            })

            odds_df = pd.DataFrame({
                "player_name": ["Player A"],
                "market": [market],
                "team": ["LAL"],
                "line": [5.5],
            })

            def mock_build_candidate_row(**kwargs) -> dict[str, Any] | None:
                return None

            def mock_reject_candidate(**kwargs) -> dict[str, Any]:
                return {
                    "player_name": kwargs.get("player_row", {}).get("player_name", ""),
                    "market_type": kwargs.get("market", ""),
                    "rejection_reason": kwargs.get("reason", ""),
                    "projection_support_status": kwargs.get("projection_support_status", ""),
                    "team": kwargs.get("team", ""),
                }

            def mock_score_candidate(**kwargs) -> dict[str, Any] | None:
                return None

            accepted, rejected = score_player_markets(
                players_df=players_df,
                odds_df=odds_df,
                is_player_inactive=lambda x: False,
                build_candidate_row=mock_build_candidate_row,
                score_candidate_fn=mock_score_candidate,
                reject_candidate_fn=mock_reject_candidate,
                allow_partial_fill=False,
            )

            assert len(accepted) == 0, f"{market} should not be accepted"
            assert len(rejected) == 1, f"{market} should be rejected"
            assert rejected[0]["rejection_reason"] in [
                "unsupported_projection_market",
                "unsupported_market_type",
                "invalid_projection_output",
            ], f"{market} should have explicit rejection reason"

    def test_zero_projection_placeholder_rejected(self):
        """player_points with zero projection is rejected as placeholder."""
        from courtvision.data.candidates import score_player_markets

        players_df = pd.DataFrame({
            "player_name": ["Player A"],
            "team_abbr": ["LAL"],
        })

        odds_df = pd.DataFrame({
            "player_name": ["Player A"],
            "market": ["player_points"],
            "team": ["LAL"],
            "line": [24.5],
        })

        # build_candidate_row returns None for zero projection case
        def mock_build_candidate_row(**kwargs) -> dict[str, Any] | None:
            return None

        def mock_reject_candidate(**kwargs) -> dict[str, Any]:
            return {
                "player_name": kwargs.get("player_row", {}).get("player_name", ""),
                "market_type": kwargs.get("market", ""),
                "rejection_reason": kwargs.get("reason", ""),
                "projection_support_status": kwargs.get("projection_support_status", ""),
                "team": kwargs.get("team", ""),
            }

        def mock_score_candidate(**kwargs) -> dict[str, Any] | None:
            return None

        accepted, rejected = score_player_markets(
            players_df=players_df,
            odds_df=odds_df,
            is_player_inactive=lambda x: False,
            build_candidate_row=mock_build_candidate_row,
            score_candidate_fn=mock_score_candidate,
            reject_candidate_fn=mock_reject_candidate,
            allow_partial_fill=False,
        )

        # Assert: player_points with zero/None projection should be rejected
        assert len(accepted) == 0
        assert len(rejected) == 1
        assert rejected[0]["rejection_reason"] == "invalid_projection_output"

    def test_board_outputs_exclude_zero_projection_fake_edges(self):
        """Board outputs should only contain valid projections."""
        from courtvision.data.candidates import score_player_markets

        # Mix of supported and unsupported
        players_df = pd.DataFrame({
            "player_name": ["Player A", "Player B"],
            "team_abbr": ["LAL", "GSW"],
        })

        odds_df = pd.DataFrame({
            "player_name": ["Player A", "Player B"],
            "market": ["player_points", "player_3pt_made"],
            "team": ["LAL", "GSW"],
            "line": [25.5, 3.5],
        })

        call_count = [0]

        def mock_build_candidate_row(**kwargs) -> dict[str, Any] | None:
            market = kwargs.get("market", "")
            call_count[0] += 1
            if market == "player_points":
                return {
                    "player_name": kwargs.get("player_row", {}).get("player_name", ""),
                    "market_type": market,
                    "model_projection": 27.0,
                    "projection_support_status": "modeled",
                    "edge": 1.5,
                }
            return None  # Unsupported markets return None

        def mock_reject_candidate(**kwargs) -> dict[str, Any]:
            return {
                "player_name": kwargs.get("player_row", {}).get("player_name", ""),
                "market_type": kwargs.get("market", ""),
                "rejection_reason": kwargs.get("reason", ""),
                "projection_support_status": kwargs.get("projection_support_status", ""),
            }

        def mock_score_candidate(**kwargs) -> dict[str, Any] | None:
            candidate = kwargs.get("candidate_row")
            if candidate:
                candidate["scored"] = True
                return candidate
            return None

        accepted, rejected = score_player_markets(
            players_df=players_df,
            odds_df=odds_df,
            is_player_inactive=lambda x: False,
            build_candidate_row=mock_build_candidate_row,
            score_candidate_fn=mock_score_candidate,
            reject_candidate_fn=mock_reject_candidate,
            allow_partial_fill=False,
        )

        # Assert: Only player_points accepted, 3pt_made rejected
        assert len(accepted) == 1
        assert accepted[0]["market_type"] == "player_points"
        assert accepted[0]["model_projection"] > 0

        assert len(rejected) == 1
        assert rejected[0]["market_type"] == "player_3pt_made"

    def test_rejection_counts_tracked_in_diagnostics(self):
        """score_player_markets tracks rejection reasons in diagnostics."""
        from courtvision.data.candidates import score_player_markets
        import logging

        players_df = pd.DataFrame({
            "player_name": ["Player A", "Player B"],
            "team_abbr": ["LAL", "LAL"],
        })

        odds_df = pd.DataFrame({
            "player_name": ["Player A", "Player B"],
            "market": ["player_3pt_made", "player_rebounds"],
            "team": ["LAL", "LAL"],
            "line": [3.5, 8.5],
        })

        def mock_build_candidate_row(**kwargs) -> dict[str, Any] | None:
            return None

        def mock_reject_candidate(**kwargs) -> dict[str, Any]:
            return {
                "player_name": kwargs.get("player_row", {}).get("player_name", ""),
                "market_type": kwargs.get("market", ""),
                "rejection_reason": kwargs.get("reason", ""),
                "projection_support_status": kwargs.get("projection_support_status", ""),
            }

        def mock_score_candidate(**kwargs) -> dict[str, Any] | None:
            return None

        accepted, rejected = score_player_markets(
            players_df=players_df,
            odds_df=odds_df,
            is_player_inactive=lambda x: False,
            build_candidate_row=mock_build_candidate_row,
            score_candidate_fn=mock_score_candidate,
            reject_candidate_fn=mock_reject_candidate,
            allow_partial_fill=False,
        )

        # Assert: Both rejected with correct reasons
        assert len(rejected) == 2
        reasons = [r["rejection_reason"] for r in rejected]
        assert all(r in ["unsupported_projection_market", "unsupported_market_type"] for r in reasons)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
