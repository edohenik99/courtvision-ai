"""Placeholder adapter for future sportsbook HR prop odds feeds."""

from __future__ import annotations

from datetime import date
from typing import Sequence

from courtvision.sports.mlb.adapters.base import OddsQuote


SUPPORTED_SPORTSBOOKS = (
    "DraftKings",
    "FanDuel",
    "BetMGM",
    "Caesars",
    "Pinnacle",
)


class SportsbookOddsProvider:
    """Provider shell; live fetching and authentication are intentionally absent."""

    supported_sportsbooks = SUPPORTED_SPORTSBOOKS

    def get_odds(self, report_date: date) -> Sequence[OddsQuote]:
        raise NotImplementedError("Live MLB sportsbook odds are not configured")


__all__ = ["SUPPORTED_SPORTSBOOKS", "SportsbookOddsProvider"]
