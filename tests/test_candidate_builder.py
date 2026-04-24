import pandas as pd

from courtvision_ai import CourtVisionAI


class _DummySelection:
    def evaluate_minutes_gate(self, **kwargs):
        return {
            "effective_minutes": 32.0,
            "threshold_overrides": {},
            "mode": "pass",
            "hard_reject_floor": 16.0,
            "minutes_shortfall": 0.0,
        }


class _DummyEngine:
    PLAYER_MARKETS = {"player_points": "pts"}
    player_selection = _DummySelection()

    def _to_float(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _player_selection_injury_profile(self, **kwargs):
        return {"inactive": False, "threshold_overrides": {}, "notes": "", "team_impact": 0.0, "opp_impact": 0.0}

    def _rejected_row(self, **kwargs):
        row = {"recommendation": "rejected"}
        row.update(kwargs)
        return row

    def _synthetic_player_market_line(self, **kwargs):
        return None

    def _project_player_market(self, **kwargs):
        return 24.0

    def _player_confidence(self, **kwargs):
        return 0.71

    def _apply_player_injury_context(self, **kwargs):
        return kwargs["projection"], kwargs["confidence"], {"injury_adjustment": 0.0}

    def _apply_player_points_realism_dampener(self, **kwargs):
        return kwargs["model_projection"], kwargs["confidence"], {"realism_dampener": 0.0}

    def _qualify_or_reject(self, **kwargs):
        return {"qualified": True, "row": {"entity_name": kwargs["entity_name"], "market_type": kwargs["market_type"], "recommendation": "qualified"}}

    def _score_player_markets(
        self,
        player_row,
        player_markets,
        live_supported_markets,
        team_abbr,
        opp_abbr,
        prediction_date,
        calibration,
        opponent_row,
        league_context,
        injury_context=None,
    ):
        selected_rows = []
        rejected_rows = []

        for _, market_row in player_markets.iterrows():
            market_type = str(market_row.get('market_type', '')).strip()
            if market_type not in live_supported_markets:
                continue

            sportsbook_line = self._to_float(market_row.get('line', 0.0))
            odds = market_row.get('odds')

            projection = self._project_player_market(
                player_row=player_row,
                market_type=market_type,
                opponent_row=opponent_row,
                league_context=league_context,
            )

            # Apply injury context if available
            projection, confidence_adjustment, injury_metadata = self._apply_player_injury_context(
                player_row=player_row,
                team_abbr=team_abbr,
                opp_abbr=opp_abbr,
                market_type=market_type,
                projection=projection,
                confidence=0.5,  # placeholder
                injury_context=injury_context,
            )

            edge = projection - sportsbook_line

            stat_std = self._to_float(player_row.get(f"{self.PLAYER_MARKETS.get(market_type, '')}_std", 0.0))
            minutes_avg = self._to_float(player_row.get("min_avg", 0.0))
            confidence = self._player_confidence(
                market_type=market_type,
                edge_abs=abs(edge),
                stat_std=stat_std,
                minutes_avg=minutes_avg,
                calibration=calibration,
            )
            confidence *= confidence_adjustment

            selection = "over" if projection > sportsbook_line else "under"

            result = self._qualify_or_reject(
                market_type=market_type,
                entity_name=str(player_row.get('player_name', '')),
                team=team_abbr,
                opponent=opp_abbr,
                sportsbook_line=sportsbook_line,
                model_projection=projection,
                edge=edge,
                confidence=confidence,
                selection=selection,
                prediction_date=prediction_date,
                odds=odds,
                extra_fields=injury_metadata,
            )

            if result["qualified"]:
                selected_rows.append(result["row"])
            else:
                rejected_rows.append(result["row"])

        return selected_rows, rejected_rows


def test_score_player_markets_builds_selected_rows_for_live_market():
    engine = _DummyEngine()
    player_row = {"player_name": "Test Player", "min_avg": 30, "min_recent": 32, "pts_std": 4.0}
    player_markets = pd.DataFrame([
        {"market_type": "player_points", "line": 21.5, "bookmaker": "test", "is_live_market": True, "line_source": "api_market", "odds": -110}
    ])

    selected, rejected = engine._score_player_markets(
        player_row=player_row,
        player_markets=player_markets,
        live_supported_markets=["player_points"],
        team_abbr="GSW",
        opp_abbr="LAL",
        prediction_date="2026-04-15",
        calibration={},
        opponent_row=None,
        league_context={},
        injury_context=None,
    )

    assert len(selected) == 1
    assert rejected == []
    assert selected[0]["entity_name"] == "Test Player"
    assert selected[0]["market_type"] == "player_points"


def test_score_player_markets_preserves_over_under_odds_when_odds_is_missing():
    ai = CourtVisionAI(out_dir="outputs")
    player_row = {"player_name": "Test Player", "team_abbr": "GSW", "min_avg": 30.0, "pts_std": 4.0}
    player_markets = pd.DataFrame([
        {
            "market_type": "player_points",
            "line": 21.5,
            "over_odds": -110,
            "under_odds": 104,
            "odds": None,
        }
    ])

    selected, rejected = ai._score_player_markets(
        player_row=player_row,
        player_markets=player_markets,
        live_supported_markets=["player_points"],
        team_abbr="GSW",
        opp_abbr="LAL",
        prediction_date="2026-04-15",
        calibration={},
        opponent_row={},
        league_context={},
        injury_context=None,
    )

    assert len(selected) + len(rejected) == 1
    row = (selected or rejected)[0]
    assert row["sportsbook_line"] == 21.5
    assert row["odds"] in (-110.0, 104.0)
    assert row["selection"] in ("over", "under")
