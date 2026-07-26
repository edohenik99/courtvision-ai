from __future__ import annotations

import logging
import statistics
import warnings
from collections import defaultdict
from datetime import datetime

from courtvision.clients.provider_manager import ProviderManager
from courtvision.config import Settings
from courtvision.market.value_engine import ValueEngine
from courtvision.models import Game, Injury, PlayerGameStats, PlayerProjection
from courtvision.reporting.text_report import TextReportWriter

PREDICTION_ENGINE_STATUS = "legacy-unused"


class CourtVisionPro:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # Use ProviderManager for priority-based provider selection with fallback
        self.client = ProviderManager(settings)
        self.value_engine = ValueEngine()
        self.report_writer = TextReportWriter(output_dir="outputs")
        self.logger = logging.getLogger("courtvision")
        self.logger.setLevel(logging.INFO)

    def predict(self, prediction_date: str) -> dict[str, str]:
        warnings.warn(
            "CourtVisionPro.predict is legacy-unused and is not an approved "
            "live entrypoint. Use PredictionApplicationService.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.logger.info("starting prediction run for %s", prediction_date)

        # Log provider configuration at start of run
        provider_status = self.client.get_run_status()
        self.logger.info(
            "provider_configuration attempted=%s priority=%s",
            provider_status.provider_attempted,
            self.client.provider_priority,
        )

        games = self.client.get_games_by_date(prediction_date)
        if not games:
            raise RuntimeError(f"No games found for {prediction_date}")

        team_ids = self._team_ids_from_games(games)
        team_abbrev_by_id = self._team_abbrev_by_id(games)

        self.logger.info("found %s games and %s active teams", len(games), len(team_ids))

        active_players = self.client.get_active_players_for_team_ids(team_ids)
        if not active_players:
            raise RuntimeError("No active players found for today's teams.")

        player_ids = [player.id for player in active_players]
        season = self._season_for_prediction_date(prediction_date)
        stats = self.client.get_stats_for_player_ids(player_ids, [season])
        self.logger.info("pulled %s player stat rows", len(stats))

        try:
            injuries = self.client.get_team_injuries()
        except Exception as exc:
            self.logger.warning("injury pull failed: %s", exc)
            injuries = []

        projections = self._build_projections(
            games=games,
            stats=stats,
            injuries=injuries,
            team_abbrev_by_id=team_abbrev_by_id,
        )

        market_props = []
        all_odds = []
        for game in games:
            try:
                data = self.client.get_player_props_for_game(
                    game.id,
                    vendors=["draftkings", "fanduel", "caesars", "betmgm"],
                    prop_types=["points", "rebounds", "assists", "threes", "steals", "blocks"],
                )
                all_odds.extend(data)
            except Exception as exc:
                self.logger.warning("market pull failed for game %s: %s", game.id, exc)

        if not all_odds:
            self.logger.warning("no_odds_data game_id=%s", game.id)
            return pd.DataFrame()

        # Extract player_name from nested player object if needed
        for row in all_odds:
            if not row.get("player_name") and row.get("player"):
                player = row.get("player")
                if isinstance(player, dict):
                    first_name = str(player.get("first_name", "")).strip()
                    last_name = str(player.get("last_name", "")).strip()
                    row["player_name"] = f"{first_name} {last_name}".strip()

        df = pd.DataFrame(all_odds)

        # Diagnostic logging for player_name
        if "player_name" in df.columns:
            null_count = df["player_name"].isna().sum() + (df["player_name"] == "").sum()
            sample_names = df["player_name"].dropna().head(5).tolist()
            self.logger.info(
                "odds_dataframe_created total_rows=%d null_player_names=%d sample_names=%s",
                len(df),
                null_count,
                sample_names,
            )

        self.logger.info("pulled %s market props", len(market_props))

        elite_plays = self.value_engine.rank_plays(projections, market_props, top_n=20)
        self.logger.info("built %s elite plays", len(elite_plays))

        projection_report = self.report_writer.write_projection_report(prediction_date, projections[:40])
        elite_txt, elite_csv = self.report_writer.write_elite_board(prediction_date, elite_plays)
        picks_history = self.report_writer.write_pick_history(prediction_date, elite_plays)

        # Log provider usage summary at end of run
        self.client.log_provider_summary()

        return {
            "projection_report": str(projection_report),
            "elite_board_txt": str(elite_txt),
            "elite_board_csv": str(elite_csv),
            "pick_history_csv": str(picks_history),
        }

    def _build_projections(
        self,
        games: list[Game],
        stats: list[PlayerGameStats],
        injuries: list[Injury],
        team_abbrev_by_id: dict[int, str],
    ) -> list[PlayerProjection]:
        stats_by_player: defaultdict[int, list[PlayerGameStats]] = defaultdict(list)
        for row in stats:
            stats_by_player[row.player_id].append(row)

        opponent_map: dict[int, str] = {}
        game_map: dict[int, int] = {}
        for game in games:
            opponent_map[game.home_team.id] = game.visitor_team.abbreviation
            opponent_map[game.visitor_team.id] = game.home_team.abbreviation
            game_map[game.home_team.id] = game.id
            game_map[game.visitor_team.id] = game.id

        injury_count_by_team: defaultdict[int, int] = defaultdict(int)
        notable_statuses = {"out", "doubtful", "inactive"}
        for injury in injuries:
            if injury.status.strip().lower() in notable_statuses:
                injury_count_by_team[injury.team_id] += 1

        projections: list[PlayerProjection] = []

        for player_id, rows in stats_by_player.items():
            if not rows:
                continue

            rows_sorted = sorted(rows, key=lambda x: x.game_date, reverse=True)
            recent10 = rows_sorted[:10]
            recent5 = rows_sorted[:5] if len(rows_sorted) >= 5 else rows_sorted

            team_id = recent10[0].team_id
            if team_id not in opponent_map:
                continue

            expected_minutes = self._weighted_recent_stat(recent5, recent10, "minutes")
            if expected_minutes < 16:
                continue

            exposure = self._exposure_score([r.minutes for r in recent10], expected_minutes)
            confidence = self._confidence_label(expected_minutes, exposure)

            injury_boost = min(injury_count_by_team.get(team_id, 0) * 0.03, 0.12)

            points = self._weighted_recent_stat(recent5, recent10, "points") * (1.0 + injury_boost)
            rebounds = self._weighted_recent_stat(recent5, recent10, "rebounds") * (1.0 + injury_boost * 0.35)
            assists = self._weighted_recent_stat(recent5, recent10, "assists") * (1.0 + injury_boost * 0.75)
            threes = self._weighted_recent_stat(recent5, recent10, "threes") * (1.0 + injury_boost * 0.50)
            steals = self._weighted_recent_stat(recent5, recent10, "steals")
            blocks = self._weighted_recent_stat(recent5, recent10, "blocks")

            notes = [
                f"minute stability={exposure:.2f}",
                f"expected minutes={expected_minutes:.1f}",
            ]
            if injury_boost > 0:
                notes.append(f"injury boost={injury_boost:.2%}")
            else:
                notes.append("no injury boost applied")

            projections.append(
                PlayerProjection(
                    player_id=player_id,
                    player_name=recent10[0].player_name,
                    team_id=team_id,
                    team_abbreviation=team_abbrev_by_id.get(team_id, recent10[0].team_abbreviation),
                    game_id=game_map[team_id],
                    opponent_abbreviation=opponent_map[team_id],
                    expected_minutes=expected_minutes,
                    exposure_score=exposure,
                    confidence=confidence,
                    stat_projections={
                        "points": points,
                        "rebounds": rebounds,
                        "assists": assists,
                        "threes": threes,
                        "steals": steals,
                        "blocks": blocks,
                    },
                    notes=notes,
                )
            )

        projections.sort(
            key=lambda p: (
                {"Strong": 3, "Medium": 2, "Low": 1}.get(p.confidence, 0),
                p.exposure_score,
                p.expected_minutes,
                p.stat_projections.get("points", 0.0) + p.stat_projections.get("assists", 0.0),
            ),
            reverse=True,
        )
        return projections

    @staticmethod
    def _team_ids_from_games(games: list[Game]) -> set[int]:
        ids: set[int] = set()
        for game in games:
            ids.add(game.home_team.id)
            ids.add(game.visitor_team.id)
        return ids

    @staticmethod
    def _team_abbrev_by_id(games: list[Game]) -> dict[int, str]:
        mapping: dict[int, str] = {}
        for game in games:
            mapping[game.home_team.id] = game.home_team.abbreviation
            mapping[game.visitor_team.id] = game.visitor_team.abbreviation
        return mapping

    @staticmethod
    def _season_for_prediction_date(prediction_date: str) -> int:
        dt = datetime.strptime(prediction_date, "%Y-%m-%d")
        return dt.year if dt.month >= 10 else dt.year - 1

    @staticmethod
    def _weighted_recent_stat(
        recent5: list[PlayerGameStats],
        recent10: list[PlayerGameStats],
        stat_name: str,
    ) -> float:
        values5 = [float(getattr(r, stat_name)) for r in recent5]
        values10 = [float(getattr(r, stat_name)) for r in recent10]
        avg5 = sum(values5) / len(values5) if values5 else 0.0
        avg10 = sum(values10) / len(values10) if values10 else 0.0
        return 0.65 * avg5 + 0.35 * avg10

    @staticmethod
    def _exposure_score(minutes_history: list[float], expected_minutes: float) -> float:
        if not minutes_history:
            return 0.5

        if len(minutes_history) == 1:
            volatility_penalty = 0.0
        else:
            volatility_penalty = min(statistics.pstdev(minutes_history) / 12.0, 0.35)

        minutes_strength = min(expected_minutes / 36.0, 1.0)
        score = (0.35 + 0.65 * minutes_strength) - volatility_penalty
        return max(0.15, min(score, 0.98))

    @staticmethod
    def _confidence_label(expected_minutes: float, exposure_score: float) -> str:
        if expected_minutes >= 30 and exposure_score >= 0.78:
            return "Strong"
        if expected_minutes >= 24 and exposure_score >= 0.60:
            return "Medium"
        return "Low"
