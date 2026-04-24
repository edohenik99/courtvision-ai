"""Quick test of grading logic."""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.grading.grade_props import PickGrader


def test_grade_pick_over_win():
    """over bet wins when actual > line."""
    result = PickGrader._grade_pick("over", 24.5, 26.0)
    assert result == "win", f"Expected win, got {result}"
    print("✓ over win passed")


def test_grade_pick_over_loss():
    """over bet loses when actual < line."""
    result = PickGrader._grade_pick("over", 24.5, 20.0)
    assert result == "loss", f"Expected loss, got {result}"
    print("✓ over loss passed")


def test_grade_pick_under_win():
    """under bet wins when actual < line."""
    result = PickGrader._grade_pick("under", 24.5, 20.0)
    assert result == "win", f"Expected win, got {result}"
    print("✓ under win passed")


def test_grade_pick_under_loss():
    """under bet loses when actual > line."""
    result = PickGrader._grade_pick("under", 24.5, 30.0)
    assert result == "loss", f"Expected loss, got {result}"
    print("✓ under loss passed")


def test_grade_pick_push():
    """Push when actual == line."""
    result = PickGrader._grade_pick("over", 24.5, 24.5)
    assert result == "push", f"Expected push, got {result}"
    print("✓ push passed")


def test_grade_pick_pending():
    """Pending when actual is None."""
    result = PickGrader._grade_pick("over", 24.5, None)
    assert result == "pending", f"Expected pending, got {result}"
    print("✓ pending passed")


def test_safe_int():
    """_safe_int converts strings to int."""
    assert PickGrader._safe_int("42") == 42
    assert PickGrader._safe_int(None) is None
    assert PickGrader._safe_int("") is None
    assert PickGrader._safe_int("invalid") is None
    print("✓ _safe_int passed")


def test_safe_float():
    """_safe_float converts strings to float."""
    assert PickGrader._safe_float("24.5") == 24.5
    assert PickGrader._safe_float(None, 0.0) == 0.0
    assert PickGrader._safe_float("", 99.0) == 99.0
    assert PickGrader._safe_float("invalid", -1.0) == -1.0
    print("✓ _safe_float passed")


if __name__ == "__main__":
    print("\n=== Testing Grading Logic ===\n")
    
    test_grade_pick_over_win()
    test_grade_pick_over_loss()
    test_grade_pick_under_win()
    test_grade_pick_under_loss()
    test_grade_pick_push()
    test_grade_pick_pending()
    test_safe_int()
    test_safe_float()
    
    print("\n=== All Grading Tests Passed ===\n")
