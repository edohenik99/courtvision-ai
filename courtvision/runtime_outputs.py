from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class OutputLayoutConfig:
    verbose_outputs: bool = False


class OutputLayoutPolicy:
    def __init__(self, root_dir: Path, config: OutputLayoutConfig | None = None) -> None:
        self.root_dir = Path(root_dir)
        self.config = config or OutputLayoutConfig()

    def ensure_directories(self) -> None:
        for lane in self.active_lanes():
            (self.root_dir / lane).mkdir(parents=True, exist_ok=True)

    def active_lanes(self) -> tuple[str, ...]:
        lanes = ["operator", "diagnostics", "research"]
        if self.config.verbose_outputs:
            lanes.append("optional")
        return tuple(lanes)

    def prediction_paths(self, prediction_date: str) -> dict[str, Path]:
        self.ensure_directories()
        paths = {
            "player_predictions": self._lane_file("research", f"player_predictions_{prediction_date}.csv"),
            "game_predictions": self._lane_file("research", f"game_predictions_{prediction_date}.csv"),
            "player_edges": self._lane_file("research", f"player_edges_{prediction_date}.csv"),
            "game_edges": self._lane_file("research", f"game_edges_{prediction_date}.csv"),
            "model_metrics": self._lane_file("research", f"model_metrics_{prediction_date}.json"),
            "top_plays_report": self._lane_file("operator", f"top_plays_report_{prediction_date}.txt"),
            "elite_decision_report": self._lane_file("operator", f"elite_decision_report_{prediction_date}.txt"),
            "elite_board": self._lane_file("operator", f"elite_board_{prediction_date}.csv"),
            "full_market_board": self._lane_file("operator", f"full_market_board_{prediction_date}.csv"),
            "near_elite_review": self._lane_file("operator", f"near_elite_review_{prediction_date}.csv"),
            "incubator_board": self._lane_file("operator", f"incubator_board_{prediction_date}.csv"),
            "sgp_board": self._lane_file("operator", f"sgp_board_{prediction_date}.csv"),
            "board_diagnostics_json": self._lane_file("diagnostics", f"board_diagnostics_{prediction_date}.json"),
            "player_points_elite_admission_csv": self._lane_file(
                "diagnostics",
                f"player_points_elite_admission_{prediction_date}.csv",
            ),
            "player_points_elite_admission_json": self._lane_file(
                "diagnostics",
                f"player_points_elite_admission_{prediction_date}.json",
            ),
        }

        if self.config.verbose_outputs:
            paths.update(
                {
                    "top_player_edges": self._lane_file("optional", f"top_player_edges_{prediction_date}.csv"),
                    "top_game_edges": self._lane_file("optional", f"top_game_edges_{prediction_date}.csv"),
                    "stat_only_board": self._lane_file("optional", f"stat_only_board_{prediction_date}.csv"),
                    "strike_board": self._lane_file("optional", f"strike_board_{prediction_date}.csv"),
                    "predictive_lines_board": self._lane_file("optional", f"predictive_lines_board_{prediction_date}.csv"),
                    "team_board": self._lane_file("optional", f"team_board_{prediction_date}.csv"),
                    "near_miss_board": self._lane_file("optional", f"near_miss_board_{prediction_date}.csv"),
                    "board_diagnostics_csv": self._lane_file("optional", f"board_diagnostics_{prediction_date}.csv"),
                }
            )

        return paths

    def grading_paths(self, prediction_date: str) -> dict[str, Path]:
        self.ensure_directories()
        paths = {
            "grading_results": self._lane_file("research", f"grading_results_{prediction_date}.csv"),
            "grading_summary_json": self._lane_file("diagnostics", f"grading_summary_{prediction_date}.json"),
            "player_points_calibration_json": self._lane_file("diagnostics", f"player_points_calibration_{prediction_date}.json"),
        }
        if self.config.verbose_outputs:
            paths["grading_summary_csv"] = self._lane_file("optional", f"grading_summary_{prediction_date}.csv")
        return paths

    def _lane_file(self, lane: str, filename: str) -> Path:
        return self.root_dir / lane / filename


__all__ = ["OutputLayoutConfig", "OutputLayoutPolicy"]
