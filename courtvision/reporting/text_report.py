from __future__ import annotations

import csv
import json
import logging
from collections import Counter
from pathlib import Path

from courtvision.models import GradedPick, PlayerProjection, RankedPlay

# ELITE_GAME_CAP must match the value in selection/operator_boards.py
ELITE_GAME_CAP = 4
logger = logging.getLogger("courtvision.reporting")


class TextReportWriter:
    def __init__(self, output_dir: str = "outputs") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir = self.output_dir / "runtime"
        self.history_dir = self.runtime_dir / "history"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def write_projection_report(self, prediction_date: str, projections: list[PlayerProjection]) -> Path:
        path = self.output_dir / f"projection_report_{prediction_date}.txt"
        with path.open("w", encoding="utf-8") as handle:
            handle.write(f"CourtVision Projection Report - {prediction_date}\n")
            handle.write("=" * 60 + "\n\n")
            for proj in projections:
                stats = proj.stat_projections
                handle.write(
                    f"{proj.player_name} | {proj.team_abbreviation} vs {proj.opponent_abbreviation} | "
                    f"PTS {stats.get('points', 0):.1f} REB {stats.get('rebounds', 0):.1f} "
                    f"AST {stats.get('assists', 0):.1f} 3PM {stats.get('threes', 0):.1f} | "
                    f"Min {proj.expected_minutes:.1f} | Exposure {proj.exposure_score:.2f} | {proj.confidence}\n"
                )
                if proj.notes:
                    handle.write(f"   Notes: {'; '.join(proj.notes)}\n")
        return path

    def write_elite_board(self, prediction_date: str, plays: list[RankedPlay]) -> tuple[Path, Path]:
        txt_path = self.output_dir / f"elite_board_{prediction_date}.txt"
        csv_path = self.output_dir / f"elite_board_{prediction_date}.csv"

        # Calculate game exposure for validation
        game_counts = Counter()
        market_counts = Counter()
        for play in plays:
            game_id = getattr(play, 'game_id', None)
            if game_id:
                game_counts[int(game_id)] += 1
            market_type = getattr(play, 'market_type', 'unknown')
            market_counts[market_type] += 1
        
        max_game_exposure = max(game_counts.values()) if game_counts else 0
        rows = len(plays)
        
        # [FINAL_ELITE_WRITER] runtime marker
        print(f"[FINAL_ELITE_WRITER] function=write_elite_board rows={rows} max_game_exposure={max_game_exposure} cap={ELITE_GAME_CAP}")
        
        # [FINAL_ELITE_MARKETS] distribution trace
        market_dist = dict(market_counts)
        print(f"[FINAL_ELITE_MARKETS] {market_dist}")
        
        # Hard validation: game cap must be enforced
        if max_game_exposure > ELITE_GAME_CAP:
            raise RuntimeError(
                f"[CAP_VIOLATION] Game cap violated in final elite write: "
                f"max_game_exposure={max_game_exposure} > cap={ELITE_GAME_CAP}. "
                f"Game distribution: {dict(game_counts)}"
            )
        
        # Persist market coverage diagnostics
        diagnostics_path = self.runtime_dir / "diagnostics" / f"market_coverage_{prediction_date}.json"
        diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
        coverage_data = {
            "prediction_date": prediction_date,
            "elite_count": rows,
            "max_game_exposure": max_game_exposure,
            "game_cap": ELITE_GAME_CAP,
            "market_distribution": market_dist,
            "game_distribution": dict(game_counts)
        }
        with diagnostics_path.open("w", encoding="utf-8") as f:
            json.dump(coverage_data, f, indent=2)
        logger.info(f"[MARKET_COVERAGE] persisted to {diagnostics_path}")

        with txt_path.open("w", encoding="utf-8") as handle:
            handle.write(f"CourtVision Elite Board - {prediction_date}\n")
            handle.write("=" * 36 + "\n\n")
            for play in plays:
                handle.write(
                    f"{play.rank}. {play.player_name} | {play.team_abbreviation} vs {play.opponent_abbreviation} | "
                    f"{play.side.upper()} {play.prop_type.upper()} {play.line_value:.1f} @ {play.vendor} | "
                    f"Proj {play.projection:.2f} | Edge {play.edge:.2f} | "
                    f"Exposure {play.exposure_score:.2f} | {play.confidence}\n"
                )
                if play.notes:
                    handle.write(f"   Notes: {'; '.join(play.notes)}\n")

        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "rank",
                    "player_name",
                    "team_abbreviation",
                    "opponent_abbreviation",
                    "vendor",
                    "prop_type",
                    "side",
                    "line_value",
                    "projection",
                    "edge",
                    "confidence",
                    "exposure_score",
                    "fair_probability",
                    "offered_odds",
                    "kelly_fraction",
                    "score",
                    "notes",
                ],
            )
            writer.writeheader()
            for play in plays:
                writer.writerow(
                    {
                        "rank": play.rank,
                        "player_name": play.player_name,
                        "team_abbreviation": play.team_abbreviation,
                        "opponent_abbreviation": play.opponent_abbreviation,
                        "vendor": play.vendor,
                        "prop_type": play.prop_type,
                        "side": play.side,
                        "line_value": f"{play.line_value:.1f}",
                        "projection": f"{play.projection:.2f}",
                        "edge": f"{play.edge:.2f}",
                        "confidence": play.confidence,
                        "exposure_score": f"{play.exposure_score:.2f}",
                        "fair_probability": f"{play.fair_probability:.4f}",
                        "offered_odds": play.offered_odds,
                        "kelly_fraction": f"{play.kelly_fraction:.4f}",
                        "score": f"{play.score:.4f}",
                        "notes": "; ".join(play.notes),
                    }
                )

        return txt_path, csv_path

    def write_pick_history(self, prediction_date: str, plays: list[RankedPlay]) -> Path:
        path = self.history_dir / f"picks_{prediction_date}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "prediction_date",
                    "rank",
                    "game_id",
                    "player_id",
                    "player_name",
                    "team_abbreviation",
                    "opponent_abbreviation",
                    "vendor",
                    "prop_type",
                    "side",
                    "line_value",
                    "projection",
                    "edge",
                    "confidence",
                    "exposure_score",
                    "fair_probability",
                    "offered_odds",
                    "kelly_fraction",
                    "score",
                    "notes",
                ],
            )
            writer.writeheader()
            for play in plays:
                writer.writerow(
                    {
                        "prediction_date": prediction_date,
                        "rank": play.rank,
                        "game_id": play.game_id,
                        "player_id": play.player_id,
                        "player_name": play.player_name,
                        "team_abbreviation": play.team_abbreviation,
                        "opponent_abbreviation": play.opponent_abbreviation,
                        "vendor": play.vendor,
                        "prop_type": play.prop_type,
                        "side": play.side,
                        "line_value": f"{play.line_value:.1f}",
                        "projection": f"{play.projection:.2f}",
                        "edge": f"{play.edge:.2f}",
                        "confidence": play.confidence,
                        "exposure_score": f"{play.exposure_score:.2f}",
                        "fair_probability": f"{play.fair_probability:.4f}",
                        "offered_odds": play.offered_odds,
                        "kelly_fraction": f"{play.kelly_fraction:.4f}",
                        "score": f"{play.score:.4f}",
                        "notes": "; ".join(play.notes),
                    }
                )
        return path

    def write_graded_picks(self, prediction_date: str, graded: list[GradedPick]) -> tuple[Path, Path]:
        txt_path = self.history_dir / f"graded_picks_{prediction_date}.txt"
        csv_path = self.history_dir / f"graded_picks_{prediction_date}.csv"

        with txt_path.open("w", encoding="utf-8") as handle:
            handle.write(f"CourtVision Graded Picks - {prediction_date}\n")
            handle.write("=" * 40 + "\n\n")
            for pick in graded:
                actual_text = "N/A" if pick.actual_value is None else f"{pick.actual_value:.2f}"
                handle.write(
                    f"{pick.player_name} | {pick.team_abbreviation} vs {pick.opponent_abbreviation} | "
                    f"{pick.side.upper()} {pick.prop_type.upper()} {pick.line_value:.1f} @ {pick.vendor} | "
                    f"Actual {actual_text} | Result {pick.result}\n"
                )
                if pick.notes:
                    handle.write(f"   Notes: {'; '.join(pick.notes)}\n")

        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            if graded:
                fieldnames = list(graded[0].as_dict().keys())
            else:
                fieldnames = [
                    "prediction_date",
                    "game_id",
                    "player_id",
                    "player_name",
                    "team_abbreviation",
                    "opponent_abbreviation",
                    "vendor",
                    "prop_type",
                    "side",
                    "line_value",
                    "projection",
                    "actual_value",
                    "result",
                    "edge",
                    "confidence",
                    "exposure_score",
                    "fair_probability",
                    "offered_odds",
                    "kelly_fraction",
                    "score",
                    "notes",
                ]
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for pick in graded:
                writer.writerow(pick.as_dict())

        return txt_path, csv_path

    def write_grading_summary(self, prediction_date: str, summary_rows: list[dict[str, str]]) -> Path:
        path = self.history_dir / f"grading_summary_{prediction_date}.csv"
        fieldnames = [
            "bucket",
            "key",
            "wins",
            "losses",
            "pushes",
            "pending",
            "total",
            "win_rate_ex_push",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in summary_rows:
                writer.writerow(row)
        return path