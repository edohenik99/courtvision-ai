from pathlib import Path

from courtvision.runtime_outputs import OutputLayoutConfig, OutputLayoutPolicy


def test_stat_only_board_uses_optional_lane_when_verbose(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"

    paths = OutputLayoutPolicy(
        runtime_root,
        OutputLayoutConfig(verbose_outputs=True),
    ).prediction_paths("2026-04-10")

    assert paths["elite_board"] == runtime_root / "operator" / "elite_board_2026-04-10.csv"
    assert paths["full_market_board"] == runtime_root / "operator" / "full_market_board_2026-04-10.csv"
    assert paths["near_elite_review"] == runtime_root / "operator" / "near_elite_review_2026-04-10.csv"
    assert paths["sgp_board"] == runtime_root / "operator" / "sgp_board_2026-04-10.csv"
    assert paths["stat_only_board"] == runtime_root / "optional" / "stat_only_board_2026-04-10.csv"
    assert paths["stat_only_board"] != runtime_root / "operator" / "stat_only_board_2026-04-10.csv"


def test_stat_only_board_is_absent_without_verbose_outputs(tmp_path: Path) -> None:
    paths = OutputLayoutPolicy(
        tmp_path / "runtime",
        OutputLayoutConfig(verbose_outputs=False),
    ).prediction_paths("2026-04-10")

    assert "stat_only_board" not in paths
