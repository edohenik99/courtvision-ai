from __future__ import annotations

import math
from collections import defaultdict

from courtvision.models import MarketProp, PlayerProjection, RankedPlay


class ValueEngine:
    _SIGMA_BY_PROP: dict[str, float] = {
        "points": 6.5,
        "rebounds": 3.5,
        "assists": 3.2,
        "threes": 1.8,
        "steals": 1.1,
        "blocks": 1.1,
    }

    _PROP_TO_KEY: dict[str, str] = {
        "points": "points",
        "rebounds": "rebounds",
        "assists": "assists",
        "threes": "threes",
        "steals": "steals",
        "blocks": "blocks",
    }

    def rank_plays(
        self,
        projections: list[PlayerProjection],
        market_props: list[MarketProp],
        top_n: int = 20,
    ) -> list[RankedPlay]:
        projection_map = {p.player_id: p for p in projections}
        deduped: dict[tuple[int, str, str], RankedPlay] = {}

        for prop in market_props:
            projection = projection_map.get(prop.player_id)
            if projection is None:
                continue

            stat_key = self._PROP_TO_KEY.get(prop.prop_type)
            if stat_key is None:
                continue

            projected_value = projection.stat_projections.get(stat_key)
            if projected_value is None:
                continue

            over_play = self._build_play(projection, prop, "over", projected_value)
            under_play = self._build_play(projection, prop, "under", projected_value)

            for play in (over_play, under_play):
                if play is None:
                    continue
                key = (play.player_id, play.prop_type, play.side)
                current = deduped.get(key)
                if current is None or play.score > current.score:
                    deduped[key] = play

        plays = sorted(
            deduped.values(),
            key=lambda x: (x.score, x.edge, x.fair_probability),
            reverse=True,
        )

        elite = self._apply_board_rules(plays, top_n=top_n)

        for index, play in enumerate(elite, start=1):
            play.rank = index
        return elite

    def _apply_board_rules(self, plays: list[RankedPlay], top_n: int) -> list[RankedPlay]:
        selected: list[RankedPlay] = []
        by_player: defaultdict[int, int] = defaultdict(int)
        by_game: defaultdict[int, int] = defaultdict(int)

        for play in plays:
            if by_player[play.player_id] >= 2:
                continue
            if by_game[play.game_id] >= 4:
                continue
            if play.edge < 0.75:
                continue
            if play.confidence == "Low":
                continue

            selected.append(play)
            by_player[play.player_id] += 1
            by_game[play.game_id] += 1

            if len(selected) >= top_n:
                break

        return selected

    def _build_play(
        self,
        projection: PlayerProjection,
        prop: MarketProp,
        side: str,
        projected_value: float,
    ) -> RankedPlay | None:
        if side == "over" and prop.over_odds is None:
            return None
        if side == "under" and prop.under_odds is None:
            return None

        edge = projected_value - prop.line_value
        if side == "under":
            edge = -edge

        if edge <= 0:
            return None

        sigma = self._SIGMA_BY_PROP.get(prop.prop_type, 4.0)
        fair_probability = self._logistic(edge / sigma)

        exposure_multiplier = projection.exposure_score
        confidence_multiplier = {"Strong": 1.0, "Medium": 0.8, "Low": 0.6}.get(
            projection.confidence, 0.7
        )

        offered_odds = prop.over_odds if side == "over" else prop.under_odds
        kelly_fraction = self._kelly_fraction(fair_probability, offered_odds)
        score = edge * exposure_multiplier * confidence_multiplier * (0.75 + fair_probability)

        notes = list(projection.notes)
        notes.append(f"market={prop.vendor}")
        notes.append(f"proj={projected_value:.2f} vs line={prop.line_value:.1f}")
        notes.append(f"fair_prob={fair_probability:.3f}")

        return RankedPlay(
            rank=0,
            game_id=projection.game_id,
            player_id=projection.player_id,
            player_name=projection.player_name,
            team_abbreviation=projection.team_abbreviation,
            opponent_abbreviation=projection.opponent_abbreviation,
            vendor=prop.vendor,
            prop_type=prop.prop_type,
            side=side,
            line_value=prop.line_value,
            projection=projected_value,
            edge=edge,
            confidence=projection.confidence,
            exposure_score=projection.exposure_score,
            fair_probability=fair_probability,
            offered_odds=offered_odds,
            kelly_fraction=kelly_fraction,
            score=score,
            notes=notes,
        )

    @staticmethod
    def _logistic(x: float) -> float:
        return 1.0 / (1.0 + math.exp(-x))

    @staticmethod
    def _american_to_decimal(american_odds: int | None) -> float | None:
        if american_odds is None or american_odds == 0:
            return None
        if american_odds > 0:
            return 1.0 + (american_odds / 100.0)
        return 1.0 + (100.0 / abs(american_odds))

    def _kelly_fraction(self, fair_probability: float, american_odds: int | None) -> float:
        decimal_odds = self._american_to_decimal(american_odds)
        if decimal_odds is None:
            return 0.0

        b = decimal_odds - 1.0
        p = fair_probability
        q = 1.0 - p
        if b <= 0:
            return 0.0

        fraction = (b * p - q) / b
        return max(0.0, min(fraction, 0.05))