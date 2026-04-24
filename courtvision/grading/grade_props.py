from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from courtvision.clients.balldontlie_client import BalldontlieClient
from courtvision.calibration.buckets import player_profile_bucket
from courtvision.config import Settings
from courtvision.models import GradedPick, PlayerGameStats


class PickGrader:
    _PROP_TO_STAT: dict[str, str] = {
        "points": "points",
        "rebounds": "rebounds",
        "assists": "assists",
        "threes": "threes",
        "steals": "steals",
        "blocks": "blocks",
    }
    _MARKET_TO_PROP: dict[str, str] = {
        "player_points": "points",
        "player_rebounds": "rebounds",
        "player_assists": "assists",
        "player_3pt_made": "threes",
        "player_steals": "steals",
        "player_blocks": "blocks",
    }
    _CONFIDENCE_BAND_LABELS: dict[str, str] = {
        "low": "Low",
        "mid": "Medium",
        "high": "High",
        "elite": "Elite",
    }
    _PICK_HISTORY_FIELDNAMES: list[str] = [
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
    ]

    def __init__(self, settings: Settings, output_dir: str = "outputs") -> None:
        self.settings = settings
        self.client = BalldontlieClient(settings)
        self.output_dir = Path(output_dir)
        self.runtime_dir = self.output_dir / "runtime"
        self.history_dir = self.runtime_dir / "history"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def grade_date(self, prediction_date: str) -> tuple[list[GradedPick], list[dict[str, str]]]:
        picks_path = self._ensure_pick_history(prediction_date)

        picks = self._read_pick_history(picks_path)
        if not picks:
            return [], self._build_summary([])

        player_ids = sorted({int(row["player_id"]) for row in picks if str(row.get("player_id", "")).strip()})
        season = self._season_for_prediction_date(prediction_date)
        player_game_stats = self.client.get_stats_for_player_ids_on_date(player_ids, prediction_date, season)

        actual_map: dict[tuple[int, int], PlayerGameStats] = {}
        actual_by_player: dict[int, PlayerGameStats] = {}
        for row in player_game_stats:
            actual_map[(row.player_id, row.game_id)] = row
            actual_by_player[row.player_id] = row

        graded: list[GradedPick] = []
        for row in picks:
            player_id = int(row["player_id"])
            game_id = int(row["game_id"])
            prop_type = str(row["prop_type"]).strip().lower()
            stat_key = self._PROP_TO_STAT.get(prop_type)

            actual_row = actual_map.get((player_id, game_id)) or actual_by_player.get(player_id)
            actual_value: float | None = None
            result = "pending"

            if actual_row is not None and stat_key is not None:
                actual_value = float(getattr(actual_row, stat_key))
                result = self._grade_pick(
                    side=str(row["side"]).strip().lower(),
                    line_value=float(row["line_value"]),
                    actual_value=actual_value,
                )

            notes = []
            if row.get("notes"):
                notes = [part.strip() for part in str(row["notes"]).split(";") if part.strip()]

            graded.append(
                GradedPick(
                    prediction_date=prediction_date,
                    game_id=game_id,
                    player_id=player_id,
                    player_name=str(row["player_name"]),
                    team_abbreviation=str(row["team_abbreviation"]),
                    opponent_abbreviation=str(row["opponent_abbreviation"]),
                    vendor=str(row["vendor"]),
                    prop_type=prop_type,
                    side=str(row["side"]).strip().lower(),
                    line_value=float(row["line_value"]),
                    projection=float(row["projection"]),
                    actual_value=actual_value,
                    result=result,
                    edge=float(row["edge"]),
                    confidence=str(row["confidence"]),
                    exposure_score=float(row["exposure_score"]),
                    fair_probability=float(row["fair_probability"]),
                    offered_odds=self._safe_int(row.get("offered_odds")),
                    kelly_fraction=float(row["kelly_fraction"]),
                    score=float(row["score"]),
                    notes=notes,
                )
            )

        summary = self._build_summary(graded)
        return graded, summary

    @staticmethod
    def _grade_pick(side: str, line_value: float, actual_value: float | None) -> str:
        if actual_value is None:
            return "pending"
        if abs(actual_value - line_value) < 1e-9:
            return "push"
        if side == "over":
            return "win" if actual_value > line_value else "loss"
        if side == "under":
            return "win" if actual_value < line_value else "loss"
        return "pending"

    @staticmethod
    def _safe_int(value: str | None) -> int | None:
        if value is None:
            return None
        text = str(value).strip()
        if text == "":
            return None
        try:
            return int(float(text))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _season_for_prediction_date(prediction_date: str) -> int:
        dt = datetime.strptime(prediction_date, "%Y-%m-%d")
        return dt.year if dt.month >= 10 else dt.year - 1

    @staticmethod
    def _read_pick_history(path: Path) -> list[dict[str, str]]:
        with path.open("r", newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def _ensure_pick_history(self, prediction_date: str) -> Path:
        picks_path = self.history_dir / f"picks_{prediction_date}.csv"
        if picks_path.exists():
            return picks_path
        legacy_picks = self.output_dir / "history" / f"picks_{prediction_date}.csv"
        if legacy_picks.exists():
            return legacy_picks
        if self._rebuild_pick_history_from_canonical_outputs(prediction_date, picks_path):
            return picks_path

        operator_path = self.runtime_dir / "operator" / f"elite_board_{prediction_date}.csv"
        legacy_operator_path = self.output_dir / "operator" / f"elite_board_{prediction_date}.csv"
        legacy_path = self.output_dir / f"elite_board_{prediction_date}.csv"
        raise FileNotFoundError(
            f"Missing pick history file: {picks_path}\n"
            f"Also looked for legacy pick history: {legacy_picks}\n"
            f"Also looked for canonical operator board: {operator_path}\n"
            f"Also looked for legacy operator board: {legacy_operator_path}\n"
            f"Also looked for legacy elite board: {legacy_path}"
        )

    def _rebuild_pick_history_from_canonical_outputs(self, prediction_date: str, picks_path: Path) -> bool:
        board_path = self._first_existing_path(
            [
                self.runtime_dir / "operator" / f"elite_board_{prediction_date}.csv",
                self.output_dir / "operator" / f"elite_board_{prediction_date}.csv",
                self.output_dir / f"elite_board_{prediction_date}.csv",
            ]
        )
        if board_path is None:
            return False

        board_rows = self._read_pick_history(board_path)
        player_lookup = self._load_player_id_lookup()
        legacy_rows: list[dict[str, object]] = []
        missing_players: list[str] = []

        for rank, row in enumerate(board_rows, start=1):
            market_type = str(row.get("market_type", "")).strip().lower()
            prop_type = self._MARKET_TO_PROP.get(market_type)
            if not prop_type:
                continue
            if row.get("is_live_market") and not self._safe_bool(row.get("is_live_market")):
                continue

            player_name = str(row.get("entity_name", "")).strip()
            team_abbreviation = str(row.get("team", "")).strip().upper()
            opponent_abbreviation = str(row.get("opponent", "")).strip().upper()
            player_id = self._safe_int(row.get("player_id"))
            if player_id is None:
                player_id = player_lookup.get(self._player_key(player_name, team_abbreviation))
            if player_id is None:
                missing_players.append(f"{player_name} ({team_abbreviation})")
                continue

            legacy_rows.append(
                {
                    "prediction_date": prediction_date,
                    "rank": rank,
                    "game_id": self._safe_int(row.get("game_id")) or 0,
                    "player_id": player_id,
                    "player_name": player_name,
                    "team_abbreviation": team_abbreviation,
                    "opponent_abbreviation": opponent_abbreviation,
                    "vendor": str(row.get("bookmaker") or row.get("vendor") or "unknown").strip(),
                    "prop_type": prop_type,
                    "side": str(row.get("selection", "")).strip().lower(),
                    "line_value": f"{self._safe_float(row.get('sportsbook_line')):.1f}",
                    "projection": f"{self._safe_float(row.get('model_projection')):.4f}",
                    "edge": f"{self._safe_float(row.get('edge_abs') or row.get('edge')):.4f}",
                    "confidence": self._legacy_confidence_label(row),
                    "exposure_score": f"{self._safe_float(row.get('market_trust_weight') or row.get('confidence')):.4f}",
                    "fair_probability": f"{self._safe_float(row.get('confidence')):.4f}",
                    "offered_odds": self._safe_int(row.get("odds")),
                    "kelly_fraction": "0.0000",
                    "score": f"{self._safe_float(row.get('quality_score')):.4f}",
                    "notes": self._legacy_notes(row),
                }
            )

        if missing_players:
            sample = ", ".join(sorted(dict.fromkeys(missing_players))[:5])
            raise FileNotFoundError(
                f"Unable to rebuild {picks_path.name} from {board_path} because player ids were missing for: {sample}"
            )

        self._write_pick_history_rows(picks_path, legacy_rows)
        return True

    def _load_player_id_lookup(self) -> dict[str, int]:
        baselines_path = self.output_dir / "model" / "player_baselines.csv"
        if not baselines_path.exists():
            return {}

        lookup: dict[str, int] = {}
        for row in self._read_pick_history(baselines_path):
            player_id = self._safe_int(row.get("player_id"))
            player_name = str(row.get("player_name", "")).strip()
            team_abbreviation = str(row.get("team_abbr", "")).strip().upper()
            if player_id is not None and player_name and team_abbreviation:
                lookup[self._player_key(player_name, team_abbreviation)] = player_id
            player_key = str(row.get("player_key", "")).strip().lower()
            if player_id is not None and player_key:
                lookup[player_key] = player_id
        return lookup

    @classmethod
    def _legacy_confidence_label(cls, row: dict[str, str]) -> str:
        confidence_band = str(row.get("confidence_band", "")).strip().lower()
        if confidence_band in cls._CONFIDENCE_BAND_LABELS:
            return cls._CONFIDENCE_BAND_LABELS[confidence_band]

        confidence = cls._safe_float(row.get("confidence"))
        if confidence >= 0.75:
            return "High"
        if confidence >= 0.62:
            return "Medium"
        return "Low"

    @staticmethod
    def _legacy_notes(row: dict[str, str]) -> str:
        notes: list[str] = []
        for key in ("qualification_reason", "line_source", "selection_injury_notes", "reason"):
            value = str(row.get(key, "")).strip()
            if value:
                notes.append(value)
        return "; ".join(dict.fromkeys(notes))

    @classmethod
    def _write_pick_history_rows(cls, path: Path, rows: list[dict[str, object]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=cls._PICK_HISTORY_FIELDNAMES)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    @staticmethod
    def _first_existing_path(paths: list[Path]) -> Path | None:
        for path in paths:
            if path.exists():
                return path
        return None

    @staticmethod
    def _player_key(player_name: str, team_abbreviation: str) -> str:
        return f"{player_name.strip().lower()}__{team_abbreviation.strip().upper()}"

    @staticmethod
    def _safe_float(value: object, default: float = 0.0) -> float:
        if value is None:
            return default
        text = str(value).strip()
        if not text:
            return default
        try:
            return float(text)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y"}

    def _build_summary(self, graded: list[GradedPick]) -> list[dict[str, str]]:
        summary_rows: list[dict[str, str]] = []

        overall = self._aggregate_bucket(graded)
        summary_rows.append(self._format_summary_row("overall", "all", overall))

        by_prop: defaultdict[str, list[GradedPick]] = defaultdict(list)
        by_confidence: defaultdict[str, list[GradedPick]] = defaultdict(list)
        by_side: defaultdict[str, list[GradedPick]] = defaultdict(list)
        by_selection_lane: defaultdict[str, list[GradedPick]] = defaultdict(list)
        by_profile_bucket: defaultdict[str, list[GradedPick]] = defaultdict(list)
        by_vendor: defaultdict[str, list[GradedPick]] = defaultdict(list)

        for pick in graded:
            by_prop[pick.prop_type].append(pick)
            by_confidence[pick.confidence].append(pick)
            by_side[pick.side].append(pick)
            by_selection_lane[self._selection_lane(pick)].append(pick)
            by_profile_bucket[self._profile_bucket(pick)].append(pick)
            by_vendor[pick.vendor].append(pick)

        for key, items in sorted(by_prop.items()):
            summary_rows.append(self._format_summary_row("prop_type", key, self._aggregate_bucket(items)))

        for key, items in sorted(by_confidence.items()):
            summary_rows.append(self._format_summary_row("confidence", key, self._aggregate_bucket(items)))

        for key, items in sorted(by_side.items()):
            summary_rows.append(self._format_summary_row("side", key, self._aggregate_bucket(items)))

        for key, items in sorted(by_selection_lane.items()):
            summary_rows.append(self._format_summary_row("selection_lane", key, self._aggregate_bucket(items)))

        for key, items in sorted(by_profile_bucket.items()):
            summary_rows.append(self._format_summary_row("player_profile_bucket", key, self._aggregate_bucket(items)))

        for key, items in sorted(by_vendor.items()):
            summary_rows.append(self._format_summary_row("vendor", key, self._aggregate_bucket(items)))

        return summary_rows

    @staticmethod
    def _selection_lane(pick: GradedPick) -> str:
        note_text = "; ".join(pick.notes)
        if "live_quality_rescue_pass" in note_text:
            return "live_quality_rescue_pass"
        return "core_pass"

    @staticmethod
    def _profile_bucket(pick: GradedPick) -> str:
        market_type = {
            "points": "player_points",
            "rebounds": "player_rebounds",
            "assists": "player_assists",
            "threes": "player_3pt_made",
            "steals": "player_steals",
            "blocks": "player_blocks",
        }.get(pick.prop_type, pick.prop_type)
        return player_profile_bucket(
            {
                "market_type": market_type,
                "line_value": pick.line_value,
            }
        )

    @staticmethod
    def _aggregate_bucket(items: list[GradedPick]) -> dict[str, int]:
        wins = sum(1 for item in items if item.result == "win")
        losses = sum(1 for item in items if item.result == "loss")
        pushes = sum(1 for item in items if item.result == "push")
        pending = sum(1 for item in items if item.result == "pending")
        total = len(items)
        return {
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "pending": pending,
            "total": total,
        }

    @staticmethod
    def _format_summary_row(bucket: str, key: str, stats: dict[str, int]) -> dict[str, str]:
        graded_total = stats["wins"] + stats["losses"]
        win_rate_text = f"{(stats['wins'] / graded_total):.4f}" if graded_total > 0 else "0.0000"

        return {
            "bucket": bucket,
            "key": key,
            "wins": str(stats["wins"]),
            "losses": str(stats["losses"]),
            "pushes": str(stats["pushes"]),
            "pending": str(stats["pending"]),
            "total": str(stats["total"]),
            "win_rate_ex_push": win_rate_text,
        }
