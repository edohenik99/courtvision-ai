"""Unit tests for Kelly Criterion bet sizing module.

Tests compute_kelly_fraction and compute_recommended_bet functions
to ensure proper conservative stake sizing based on edge, odds, and confidence.
"""

import csv

import pytest
from courtvision.betting.kelly import compute_kelly_fraction, compute_recommended_bet, MAX_STAKE_FRACTION, MIN_CONFIDENCE_THRESHOLD
from scripts.run_kelly_stakes import _apply_stake_dampeners, _build_stake_row, _validate_columns, main


def _kelly_row(
    *,
    player_name: str = "Kelly Player",
    selection: str = "over",
    context_caution_level: str = "low",
    context_pick_alignment: str = "aligned",
    edge: str = "0.10",
    confidence: str = "0.75",
    odds: str = "-110",
) -> dict[str, str]:
    return {
        "player_name": player_name,
        "market_type": "player_points",
        "selection": selection,
        "line": "20.5",
        "odds": odds,
        "confidence": confidence,
        "edge_pct": edge,
        "side_edge_pct": edge,
        "context_caution_level": context_caution_level,
        "context_pick_alignment": context_pick_alignment,
    }


class TestConservativeKellyLogic:
    """Test suite for conservative Kelly stake sizing."""

    def test_negative_edge_returns_zero(self):
        """Test that negative edge returns 0 (no bet)."""
        edge = -0.05  # -5% expected value
        odds = 1.91
        confidence = 0.90
        
        result = compute_kelly_fraction(edge, odds, confidence)
        
        assert result == 0.0, "Negative edge should return 0"

    def test_zero_edge_returns_zero(self):
        """Test that zero edge returns 0 (no bet)."""
        edge = 0.0
        odds = 1.91
        confidence = 0.80
        
        result = compute_kelly_fraction(edge, odds, confidence)
        
        assert result == 0.0, "Zero edge should return 0"

    def test_confidence_below_threshold_returns_zero(self):
        """Test that confidence < 0.55 returns 0."""
        edge = 0.10  # 10% edge
        odds = 1.91
        confidence = 0.50  # Below 0.55 threshold
        
        result = compute_kelly_fraction(edge, odds, confidence)
        
        assert result == 0.0, f"Confidence below {MIN_CONFIDENCE_THRESHOLD} should return 0"

    def test_confidence_at_threshold_returns_positive(self):
        """Test that confidence >= 0.55 allows betting."""
        edge = 0.08
        odds = 1.91
        confidence = 0.55  # At threshold
        
        result = compute_kelly_fraction(edge, odds, confidence)
        
        assert result > 0, "Confidence at threshold should allow betting"

    def test_stake_capped_at_0_02(self):
        """Test that stake is capped at 0.02 (2%)."""
        edge = 0.20  # Very high 20% edge
        odds = 1.50  # Short odds
        confidence = 1.0  # Full confidence
        
        result = compute_kelly_fraction(edge, odds, confidence)
        
        assert result <= MAX_STAKE_FRACTION, f"Stake should be capped at {MAX_STAKE_FRACTION}"
        assert result == MAX_STAKE_FRACTION, f"Expected {MAX_STAKE_FRACTION} cap, got {result}"

    def test_quarter_kelly_reduces_stake(self):
        """Test that quarter-Kelly reduces stake by 75%."""
        edge = 0.08  # 8% edge
        odds = 2.0   # Even money (b=1)
        confidence = 1.0
        
        result = compute_kelly_fraction(edge, odds, confidence)
        
        # Raw Kelly would be 0.08 / 1 = 0.08
        # Quarter-Kelly should be 0.08 * 0.25 = 0.02
        # Then capped at 0.02
        expected = min(edge / (odds - 1) * 0.25, MAX_STAKE_FRACTION)
        assert result == round(expected, 4), f"Quarter-Kelly should reduce stake, got {result}"

    def test_valid_positive_edge_returns_small_stake(self):
        """Test that valid positive edge returns small nonzero stake."""
        edge = 0.05  # 5% edge
        odds = 1.91  # -110 odds
        confidence = 0.70  # 70% confidence
        
        result = compute_kelly_fraction(edge, odds, confidence)
        
        # b = 0.91, raw = 0.05 / 0.91 = 0.0549
        # adjusted = 0.0549 * 0.70 = 0.0384
        # quarter = 0.0384 * 0.25 = 0.0096
        assert result > 0, "Valid edge should return positive stake"
        assert result < 0.02, "Stake should be small (conservative)"

    def test_invalid_odds_return_zero(self):
        """Test that invalid odds (<= 1.0) return 0."""
        edge = 0.08
        confidence = 0.80
        
        # Odds of 1.0 or less are invalid
        result_one = compute_kelly_fraction(edge, 1.0, confidence)
        result_zero = compute_kelly_fraction(edge, 0.5, confidence)
        result_negative = compute_kelly_fraction(edge, -1.0, confidence)
        
        assert result_one == 0.0, "Odds of 1.0 should return 0"
        assert result_zero == 0.0, "Odds < 1.0 should return 0"
        assert result_negative == 0.0, "Negative odds should return 0"

    def test_none_inputs_return_zero(self):
        """Test that None inputs return 0 gracefully."""
        result_none_edge = compute_kelly_fraction(None, 1.91, 0.80)
        result_none_odds = compute_kelly_fraction(0.08, None, 0.80)
        result_none_confidence = compute_kelly_fraction(0.08, 1.91, None)
        
        assert result_none_edge == 0.0, "None edge should return 0"
        assert result_none_odds == 0.0, "None odds should return 0"
        assert result_none_confidence == 0.0, "None confidence should return 0"

    def test_confidence_scaling(self):
        """Test that confidence scales stake proportionally."""
        edge = 0.08
        odds = 1.91
        
        result_low = compute_kelly_fraction(edge, odds, 0.60)  # Just above threshold
        result_mid = compute_kelly_fraction(edge, odds, 0.75)
        result_high = compute_kelly_fraction(edge, odds, 0.90)
        
        assert result_low < result_mid < result_high, "Higher confidence should increase stake"

    def test_result_rounded_to_4_decimals(self):
        """Test that result is rounded to 4 decimal places."""
        edge = 0.07777
        odds = 1.91
        confidence = 0.8888
        
        result = compute_kelly_fraction(edge, odds, confidence)
        
        # Check that result has at most 4 decimal places
        result_str = str(result)
        if '.' in result_str:
            decimal_places = len(result_str.split('.')[1])
            assert decimal_places <= 4, f"Result should have at most 4 decimal places: {result}"

    def test_kelly_stake_runner_accepts_valid_under_side_edge(self):
        fieldnames = ["player_name", "market_type", "selection", "odds", "confidence", "edge_pct", "side_edge_pct"]
        edge_col = _validate_columns(fieldnames)
        row = {
            "player_name": "Under Player",
            "market_type": "player_points",
            "selection": "under",
            "line": "24.5",
            "odds": "-110",
            "confidence": "0.75",
            "edge_pct": "-0.10",
            "side_edge_pct": "0.10",
            "context_caution_level": "high",
            "context_pick_alignment": "aligned",
        }

        stake = _build_stake_row(row, edge_col, bankroll=1000.0)

        assert edge_col == "side_edge_pct"
        assert stake.eligible is True
        assert stake.edge_pct == 0.10
        assert stake.side_edge_pct == 0.10
        assert stake.context_caution_level == "high"
        assert stake.context_pick_alignment == "aligned"
        assert stake.stake_amount > 0

    def test_high_caution_over_is_skipped_by_kelly(self):
        fieldnames = ["player_name", "market_type", "selection", "odds", "confidence", "edge_pct", "side_edge_pct", "context_caution_level"]
        edge_col = _validate_columns(fieldnames)

        stake = _build_stake_row(_kelly_row(selection="over", context_caution_level="high"), edge_col, bankroll=1000.0)

        assert stake.eligible is False
        assert stake.skip_reason == "context_high_caution_over"
        assert stake.stake_amount == 0
        assert stake.stake_fraction == 0
        assert stake.stake_dampener_reason == ""
        assert stake.stake_dampener_factor == 1.0

    def test_medium_neutral_over_is_eligible_and_dampened(self):
        fieldnames = ["player_name", "market_type", "selection", "odds", "confidence", "edge_pct", "side_edge_pct", "context_caution_level", "context_pick_alignment"]
        edge_col = _validate_columns(fieldnames)

        stake = _build_stake_row(
            _kelly_row(selection="over", context_caution_level="medium", context_pick_alignment="neutral", edge="0.50", confidence="1.0"),
            edge_col,
            bankroll=1000.0,
        )
        normal_stake_amount = stake.stake_amount
        normal_stake_fraction = stake.stake_fraction

        _apply_stake_dampeners([stake])

        assert stake.eligible is True
        assert stake.skip_reason == ""
        assert stake.stake_dampener_reason == "medium_neutral_over_dampener"
        assert stake.stake_dampener_factor == 0.5
        assert stake.stake_amount == round(normal_stake_amount * 0.5, 2)
        assert stake.stake_fraction == round(normal_stake_fraction * 0.5, 6)

    def test_medium_aligned_over_is_not_dampened(self):
        fieldnames = ["player_name", "market_type", "selection", "odds", "confidence", "edge_pct", "side_edge_pct", "context_caution_level", "context_pick_alignment"]
        edge_col = _validate_columns(fieldnames)

        stake = _build_stake_row(
            _kelly_row(selection="over", context_caution_level="medium", context_pick_alignment="aligned", edge="0.50", confidence="1.0"),
            edge_col,
            bankroll=1000.0,
        )
        normal_stake_amount = stake.stake_amount

        _apply_stake_dampeners([stake])

        assert stake.eligible is True
        assert stake.stake_dampener_reason == ""
        assert stake.stake_dampener_factor == 1.0
        assert stake.stake_amount == normal_stake_amount

    def test_under_pick_is_not_dampened_by_medium_neutral_rule(self):
        fieldnames = ["player_name", "market_type", "selection", "odds", "confidence", "edge_pct", "side_edge_pct", "context_caution_level", "context_pick_alignment"]
        edge_col = _validate_columns(fieldnames)

        stake = _build_stake_row(
            _kelly_row(selection="under", context_caution_level="medium", context_pick_alignment="neutral", edge="0.50", confidence="1.0"),
            edge_col,
            bankroll=1000.0,
        )
        normal_stake_amount = stake.stake_amount

        _apply_stake_dampeners([stake])

        assert stake.eligible is True
        assert stake.stake_dampener_reason == ""
        assert stake.stake_dampener_factor == 1.0
        assert stake.stake_amount == normal_stake_amount

    def test_high_caution_under_is_not_skipped_by_context_over_rule(self):
        fieldnames = ["player_name", "market_type", "selection", "odds", "confidence", "edge_pct", "side_edge_pct", "context_caution_level"]
        edge_col = _validate_columns(fieldnames)

        stake = _build_stake_row(_kelly_row(selection="under", context_caution_level="high"), edge_col, bankroll=1000.0)

        assert stake.eligible is True
        assert stake.skip_reason == ""
        assert stake.stake_amount > 0

    @pytest.mark.parametrize("level", ["medium", "low"])
    def test_medium_low_caution_over_remains_eligible(self, level):
        fieldnames = ["player_name", "market_type", "selection", "odds", "confidence", "edge_pct", "side_edge_pct", "context_caution_level"]
        edge_col = _validate_columns(fieldnames)

        stake = _build_stake_row(_kelly_row(selection="over", context_caution_level=level), edge_col, bankroll=1000.0)

        assert stake.eligible is True
        assert stake.skip_reason == ""
        assert stake.stake_amount > 0

    def test_non_player_points_market_remains_non_kelly_eligible(self):
        fieldnames = ["player_name", "market_type", "selection", "odds", "confidence", "edge_pct", "side_edge_pct", "context_caution_level"]
        edge_col = _validate_columns(fieldnames)
        row = _kelly_row(
            player_name="Shadow Rebounds",
            selection="over",
            context_caution_level="low",
            edge="0.50",
            confidence="1.0",
        )
        row["market_type"] = "player_rebounds"

        stake = _build_stake_row(row, edge_col, bankroll=1000.0)

        assert stake.eligible is False
        assert stake.skip_reason == "kelly_points_only_market_lock"
        assert stake.stake_amount == 0
        assert stake.stake_fraction == 0

    def test_existing_exposure_cap_still_works(self, tmp_path):
        input_path = tmp_path / "elite_board.csv"
        output_path = tmp_path / "kelly_stakes.csv"
        rows = [
            _kelly_row(player_name="A", selection="under", context_caution_level="high", edge="0.50", confidence="1.0", odds="-110"),
            _kelly_row(player_name="B", selection="over", context_caution_level="low", edge="0.50", confidence="1.0", odds="-110"),
            _kelly_row(player_name="C", selection="over", context_caution_level="high", edge="0.50", confidence="1.0", odds="-110"),
        ]
        with input_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        rc = main(
            [
                "--prediction-date",
                "2026-04-29",
                "--bankroll",
                "1000",
                "--input-csv",
                str(input_path),
                "--output-csv",
                str(output_path),
                "--max-daily-exposure",
                "0.01",
            ]
        )

        assert rc == 0
        out_rows = list(csv.DictReader(output_path.open("r", encoding="utf-8", newline="")))
        assert len(out_rows) == 3
        eligible_stakes = [float(row["stake_amount"]) for row in out_rows if row["eligible"] == "True"]
        assert round(sum(eligible_stakes), 2) <= 10.0
        skipped = {row["player_name"]: row for row in out_rows if row["eligible"] == "False"}
        assert all(row["kelly_eligible"] == row["eligible"] for row in out_rows)
        assert all(row["context_pick_alignment"] == "aligned" for row in out_rows)
        assert all("stake_dampener_reason" in row for row in out_rows)
        assert all("stake_dampener_factor" in row for row in out_rows)
        assert all(row["stake_dampener_factor"] == "1.0" for row in out_rows)
        assert skipped["C"]["skip_reason"] == "context_high_caution_over"
        assert float(skipped["C"]["stake_amount"]) == 0.0

    def test_output_includes_medium_neutral_over_dampener_metadata(self, tmp_path):
        input_path = tmp_path / "elite_board.csv"
        output_path = tmp_path / "kelly_stakes.csv"
        row = _kelly_row(
            player_name="Dampened Over",
            selection="over",
            context_caution_level="medium",
            context_pick_alignment="neutral",
            edge="0.50",
            confidence="1.0",
            odds="-110",
        )
        with input_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
            writer.writeheader()
            writer.writerow(row)

        rc = main(
            [
                "--prediction-date",
                "2026-04-29",
                "--bankroll",
                "1000",
                "--input-csv",
                str(input_path),
                "--output-csv",
                str(output_path),
                "--max-daily-exposure",
                "1.0",
            ]
        )

        assert rc == 0
        out_rows = list(csv.DictReader(output_path.open("r", encoding="utf-8", newline="")))
        assert len(out_rows) == 1
        out = out_rows[0]
        assert out["eligible"] == "True"
        assert out["kelly_eligible"] == "True"
        assert out["skip_reason"] == ""
        assert out["stake_dampener_reason"] == "medium_neutral_over_dampener"
        assert out["stake_dampener_factor"] == "0.5"
        assert float(out["stake_amount"]) == 10.0
        assert float(out["stake_fraction"]) == 0.01

    def test_output_carries_manual_review_action_metadata(self, tmp_path, capsys):
        input_path = tmp_path / "elite_board.csv"
        output_path = tmp_path / "kelly_stakes.csv"
        rows = [
            {
                **_kelly_row(
                    player_name="Review Under",
                    selection="under",
                    edge="0.50",
                    confidence="1.0",
                    odds="-110",
                ),
                "same_opponent_under_warning": "True",
                "same_opponent_warning_reason": "last_same_opponent_actual_exceeded_current_under_line",
                "manual_review_required": "True",
                "manual_review_reason": "same_opponent_under_warning",
            },
            {
                **_kelly_row(
                    player_name="Clean Under",
                    selection="under",
                    edge="0.50",
                    confidence="1.0",
                    odds="-110",
                ),
                "same_opponent_under_warning": "False",
                "same_opponent_warning_reason": "",
                "manual_review_required": "False",
                "manual_review_reason": "",
            },
        ]
        with input_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        rc = main(
            [
                "--prediction-date",
                "2026-05-07",
                "--bankroll",
                "1000",
                "--input-csv",
                str(input_path),
                "--output-csv",
                str(output_path),
                "--max-daily-exposure",
                "1.0",
            ]
        )

        assert rc == 0
        out_rows = {
            row["player_name"]: row
            for row in csv.DictReader(output_path.open("r", encoding="utf-8", newline=""))
        }
        review = out_rows["Review Under"]
        clean = out_rows["Clean Under"]
        assert review["same_opponent_under_warning"] == "True"
        assert review["same_opponent_warning_reason"] == "last_same_opponent_actual_exceeded_current_under_line"
        assert review["manual_review_required"] == "True"
        assert review["manual_review_reason"] == "same_opponent_under_warning"
        assert review["recommended_action"] == "REVIEW_BEFORE_BET"
        assert clean["same_opponent_under_warning"] == "False"
        assert clean["manual_review_required"] == "False"
        assert clean["recommended_action"] == "OK_TO_CONSIDER"
        log = capsys.readouterr().out
        assert "manual_review_required_count=1" in log
        assert "review_before_bet_count=1" in log


class TestConfigConstants:
    """Test configuration constants."""

    def test_max_stake_fraction_is_0_02(self):
        """Test that MAX_STAKE_FRACTION is 0.02 (2%)."""
        assert MAX_STAKE_FRACTION == 0.02, "Max stake should be 2%"

    def test_min_confidence_threshold_is_0_55(self):
        """Test that MIN_CONFIDENCE_THRESHOLD is 0.55."""
        assert MIN_CONFIDENCE_THRESHOLD == 0.55, "Min confidence should be 0.55"


class TestComputeRecommendedBet:
    """Test suite for compute_recommended_bet function."""

    def test_recommended_bet_calculation(self):
        """Test that recommended bet equals bankroll times stake fraction."""
        edge = 0.08
        odds = 1.91
        confidence = 0.80
        bankroll = 1000.0
        
        result = compute_recommended_bet(edge, odds, confidence, bankroll)
        
        assert "stake_fraction" in result
        assert "recommended_bet" in result
        
        # Verify calculation: recommended_bet = bankroll * stake_fraction
        expected_bet = round(bankroll * result["stake_fraction"], 2)
        assert result["recommended_bet"] == expected_bet, \
            f"Expected {expected_bet}, got {result['recommended_bet']}"

    def test_recommended_bet_respects_max_stake(self):
        """Test that recommended bet respects 2% max stake cap."""
        from courtvision.config import DEFAULT_BANKROLL
        
        edge = 0.20  # Very high edge
        odds = 1.50  # Short odds
        confidence = 1.0
        
        result = compute_recommended_bet(edge, odds, confidence, DEFAULT_BANKROLL)
        
        max_bet = DEFAULT_BANKROLL * MAX_STAKE_FRACTION
        assert result["recommended_bet"] <= max_bet, \
            f"Recommended bet should not exceed {max_bet}"
        assert result["stake_fraction"] <= MAX_STAKE_FRACTION, \
            f"Stake fraction should not exceed {MAX_STAKE_FRACTION}"

    def test_zero_stake_produces_zero_bet(self):
        """Test that zero stake fraction produces zero recommended bet."""
        edge = -0.05  # Negative edge
        odds = 1.91
        confidence = 0.90
        bankroll = 1000.0
        
        result = compute_recommended_bet(edge, odds, confidence, bankroll)
        
        assert result["stake_fraction"] == 0.0
        assert result["recommended_bet"] == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
