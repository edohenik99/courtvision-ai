from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone

import pytest

from courtvision.core.odds import (
    NormalizedOddsQuote,
    OddsMarketIdentity,
    OddsSelection,
    OddsSourceMetadata,
    american_to_decimal,
    american_to_implied_probability,
    decimal_to_implied_probability,
    normalize_market_type,
    quote_is_fresh,
    validate_american_odds,
)
from courtvision.sports.mlb.adapters.odds_api_provider import OddsAPIProvider
from courtvision.sports.mlb.hr_report import main


def _quote(
    *,
    mode: str = "production",
    source_type: str = "live",
    quote_timestamp: datetime | None = None,
    eligible_for_betting: bool = False,
    kelly_eligible: bool = False,
    approval_status: str = "not_approved",
) -> NormalizedOddsQuote:
    return NormalizedOddsQuote(
        market_identity=OddsMarketIdentity(
            sport="NBA",
            league="NBA",
            event_id="nba-event-1",
            event_date=date(2026, 6, 19),
            home_team="Toronto Raptors",
            away_team="Boston Celtics",
            market_type="Player Points",
        ),
        selection=OddsSelection(
            selection_name="Example Player Over",
            selection_id="player-1-over",
            line=20.5,
        ),
        source_metadata=OddsSourceMetadata(
            sportsbook="Example Book",
            provider="mocked_provider",
            mode=mode,
            source_type=source_type,
            region="CA",
            raw_provider_market_id="player_points",
            raw_event_id="provider-event-1",
            data_quality="complete",
        ),
        american_odds=-110,
        quote_timestamp=quote_timestamp,
        eligible_for_betting=eligible_for_betting,
        kelly_eligible=kelly_eligible,
        approval_status=approval_status,
    )


def _mlb_payload() -> list[dict[str, object]]:
    return [
        {
            "id": "mlb-game-1",
            "commence_time": "2026-06-20T00:10:00Z",
            "home_team": "New York Yankees",
            "away_team": "Boston Red Sox",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "last_update": "2026-06-19T16:00:00Z",
                    "markets": [
                        {
                            "key": "batter_home_runs",
                            "outcomes": [
                                {
                                    "name": "Over",
                                    "description": "Aaron Judge",
                                    "team": "New York Yankees",
                                    "price": 330,
                                    "point": 0.5,
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    ]


def test_positive_american_odds_conversion() -> None:
    assert validate_american_odds("+150") == 150
    assert american_to_decimal(150) == pytest.approx(2.5)
    assert american_to_implied_probability(150) == pytest.approx(0.4)


def test_negative_american_odds_conversion() -> None:
    assert validate_american_odds("-200") == -200
    assert american_to_decimal(-200) == pytest.approx(1.5)
    assert american_to_implied_probability(-200) == pytest.approx(2 / 3)


def test_zero_and_invalid_american_odds_are_rejected() -> None:
    with pytest.raises(ValueError, match="zero"):
        validate_american_odds(0)
    for invalid in ("even", "", 99, -99, 110.0, True, None):
        with pytest.raises(ValueError):
            validate_american_odds(invalid)  # type: ignore[arg-type]


def test_decimal_implied_probability_is_correct() -> None:
    assert decimal_to_implied_probability(4.0) == pytest.approx(0.25)
    with pytest.raises(ValueError, match="greater than 1.0"):
        decimal_to_implied_probability(1.0)


def test_quote_is_immutable_normalized_and_not_approved_by_default() -> None:
    quote = _quote()

    assert quote.market_type == "player_points"
    assert quote.decimal_odds == pytest.approx(1.0 + 100.0 / 110.0)
    assert quote.implied_probability == pytest.approx(110.0 / 210.0)
    assert quote.eligible_for_betting is False
    assert quote.kelly_eligible is False
    assert quote.approval_status == "not_approved"
    with pytest.raises(FrozenInstanceError):
        quote.eligible_for_betting = True  # type: ignore[misc]


def test_research_and_sample_quotes_cannot_be_approved() -> None:
    research = _quote(mode="research")
    sample = _quote(mode="sample", source_type="sample")

    assert research.kelly_eligible is False
    assert sample.approval_status == "not_approved"
    with pytest.raises(ValueError, match="must remain ineligible"):
        _quote(
            mode="research",
            eligible_for_betting=True,
            kelly_eligible=True,
            approval_status="approved",
        )


def test_missing_timestamp_is_stale_and_fresh_timestamp_passes() -> None:
    now = datetime(2026, 6, 19, 16, 0, tzinfo=timezone.utc)
    assert quote_is_fresh(_quote(), now=now) is False
    assert (
        quote_is_fresh(
            _quote(quote_timestamp=now - timedelta(minutes=2)),
            now=now,
        )
        is True
    )
    assert (
        quote_is_fresh(
            _quote(quote_timestamp=now - timedelta(minutes=10)),
            now=now,
            max_age=timedelta(minutes=15),
        )
        is True
    )


def test_market_identity_and_market_type_fail_clearly_when_malformed() -> None:
    assert normalize_market_type(" Batter Home Runs ") == "batter_home_runs"
    with pytest.raises(ValueError, match="different teams"):
        OddsMarketIdentity(
            sport="MLB",
            league="MLB",
            event_id="event-1",
            event_date=date(2026, 6, 19),
            home_team="Same Team",
            away_team="same team",
            market_type="batter_home_runs",
        )
    with pytest.raises(ValueError, match="event_id"):
        OddsMarketIdentity(
            sport="MLB",
            league="MLB",
            event_id="",
            event_date=date(2026, 6, 19),
            home_team="Home",
            away_team="Away",
            market_type="batter_home_runs",
        )


def test_mlb_mocked_hr_odds_map_to_research_only_normalized_quotes() -> None:
    provider = OddsAPIProvider(api_key="test-key", region="us")

    quotes = provider.normalize_quotes(_mlb_payload(), source_type="mock")

    assert len(quotes) == 1
    quote = quotes[0]
    assert quote.sport == "MLB"
    assert quote.league == "MLB"
    assert quote.event_id == "mlb-game-1"
    assert quote.home_team == "New York Yankees"
    assert quote.away_team == "Boston Red Sox"
    assert quote.market_type == "batter_home_runs"
    assert quote.selection_name == "Aaron Judge"
    assert quote.line == 0.5
    assert quote.american_odds == 330
    assert quote.provider == "odds_api"
    assert quote.source_type == "mock"
    assert quote.mode == "research"
    assert quote.eligible_for_betting is False
    assert quote.kelly_eligible is False
    assert quote.approval_status == "not_approved"


def test_existing_mlb_sample_cli_has_no_forbidden_presentation_terms(capsys) -> None:
    assert main(["--date", "2026-06-19", "--provider", "sample"]) == 0
    output = capsys.readouterr().out.casefold()

    for forbidden in (
        "bet",
        "elite",
        "strong",
        "wager",
        "unit",
        "kelly",
        "staking",
        "fair probability",
        "estimated fair",
        "betting edge",
        "bankroll",
    ):
        assert forbidden not in output
