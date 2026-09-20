from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from courtvision.core.odds import NormalizedOddsQuote
from courtvision.sports.mlb.market_data import (
    MLBMarketDataBatch,
    MLBMarketDataDiagnostic,
    MLBMarketVariant,
    MLBPlayerPropSourceRecord,
    MLBPropSide,
)


START = datetime(2026, 9, 21, 2, 10, tzinfo=timezone.utc)
UPDATED = datetime(2026, 9, 20, 19, 0, tzinfo=timezone.utc)
COLLECTED = UPDATED + timedelta(minutes=1)


def _record(**changes: object) -> MLBPlayerPropSourceRecord:
    values: dict[str, object] = {
        "provider": "the_odds_api",
        "provider_sport_key": "baseball_mlb",
        "provider_sport_title": "MLB",
        "provider_event_id": "provider-event-1",
        "home_team": "Toronto Blue Jays",
        "away_team": "New York Yankees",
        "commence_time": START,
        "bookmaker_key": "examplebook",
        "bookmaker_name": "Example Book",
        "provider_market_key": "batter_hits",
        "canonical_market_type": "batter_hits",
        "market_variant": MLBMarketVariant.MAIN,
        "participant_name": "Example Batter",
        "side": MLBPropSide.OVER,
        "line": 0.5,
        "american_odds": -120,
        "market_updated_at": UPDATED,
        "collected_at": COLLECTED,
        "source_refs": ("fixture:mlb-multi-market/event-1",),
    }
    values.update(changes)
    return MLBPlayerPropSourceRecord(**values)  # type: ignore[arg-type]


def test_source_record_preserves_evidence_and_is_immutable() -> None:
    record = _record(
        provider_outcome_id="outcome-1",
        provider_market_id="market-1",
        provider_bookmaker_id="book-1",
    )
    assert record.source_type == record.to_normalized_quote().source_type == "manual"
    assert record.provider_event_id == "provider-event-1"
    assert record.provider_sport_key == "baseball_mlb"
    assert record.provider_sport_title == "MLB"
    assert record.provider_market_key == record.canonical_market_type == "batter_hits"
    assert record.market_variant is MLBMarketVariant.MAIN
    assert record.side is MLBPropSide.OVER
    assert record.participant_name == "Example Batter"
    assert record.bookmaker_key == "examplebook"
    assert record.bookmaker_name == "Example Book"
    assert record.commence_time is START
    assert record.market_updated_at is UPDATED
    assert record.collected_at is COLLECTED
    assert record.source_refs == ("fixture:mlb-multi-market/event-1",)
    assert record.provider_outcome_id == "outcome-1"
    assert record.provider_market_id == "market-1"
    assert record.provider_bookmaker_id == "book-1"
    with pytest.raises(FrozenInstanceError):
        record.side = MLBPropSide.UNDER  # type: ignore[misc]
    with pytest.raises(TypeError):
        record.source_refs[0] = "changed"  # type: ignore[index]
    assert not hasattr(record, "__dict__")


@pytest.mark.parametrize("source_type", ["manual", "live"])
def test_acquisition_type_is_immutable_and_independent_of_inplay(source_type: str) -> None:
    record = _record(source_type=source_type)
    quote = record.to_normalized_quote()
    assert record.source_type == quote.source_type == source_type
    assert quote.mode == "research"
    assert quote.is_live is False
    assert quote.eligible_for_betting is False
    assert quote.kelly_eligible is False
    assert quote.approval_status == "not_approved"
    with pytest.raises(FrozenInstanceError):
        record.source_type = "manual"  # type: ignore[misc]


@pytest.mark.parametrize("invalid", ["", " ", "unknown", "LIVE", " live ", "mock", "historical", "sample", None, True, 1, [], {}])
def test_source_acquisition_type_fails_closed(invalid: object) -> None:
    with pytest.raises(ValueError, match="source_type"):
        _record(source_type=invalid)


@pytest.mark.parametrize("side, expected", [(" Over ", MLBPropSide.OVER), ("under", MLBPropSide.UNDER)])
@pytest.mark.parametrize("variant, expected_variant", [("main", MLBMarketVariant.MAIN), (" alternate ", MLBMarketVariant.ALTERNATE)])
def test_side_and_variant_normalize_deterministically(
    side: str, expected: MLBPropSide, variant: str, expected_variant: MLBMarketVariant,
) -> None:
    record = _record(side=side, market_variant=variant)
    assert record.side is expected
    assert record.market_variant is expected_variant


@pytest.mark.parametrize("field_name", [
    "provider", "provider_sport_key", "provider_event_id", "home_team", "away_team",
    "bookmaker_key", "bookmaker_name", "provider_market_key", "canonical_market_type",
    "participant_name",
])
@pytest.mark.parametrize("invalid", ["", " ", None, 123])
def test_required_source_text_is_not_inferred(field_name: str, invalid: object) -> None:
    with pytest.raises(ValueError, match=field_name):
        _record(**{field_name: invalid})


@pytest.mark.parametrize("field_name", [
    "provider", "provider_event_id", "provider_market_key", "participant_name",
    "bookmaker_name", "provider_sport_title", "provider_outcome_id",
])
def test_padded_source_text_is_rejected_without_rewriting_evidence(field_name: str) -> None:
    with pytest.raises(ValueError, match="surrounding whitespace"):
        _record(**{field_name: " source text "})


@pytest.mark.parametrize("field_name", [
    "provider_outcome_id", "provider_market_id", "provider_bookmaker_id",
])
@pytest.mark.parametrize("invalid", ["", " ", True, 123.5, [], {}])
def test_optional_provider_ids_require_explicit_strings_or_integers(
    field_name: str, invalid: object,
) -> None:
    with pytest.raises(ValueError, match=field_name):
        _record(**{field_name: invalid})


@pytest.mark.parametrize("field_name", ["commence_time", "market_updated_at", "collected_at"])
@pytest.mark.parametrize("invalid", [datetime(2026, 9, 20, 12), None, "2026-09-20T12:00:00Z"])
def test_timestamps_require_aware_datetime_values(field_name: str, invalid: object) -> None:
    with pytest.raises(ValueError, match=field_name):
        _record(**{field_name: invalid})


@pytest.mark.parametrize("changes", [
    {"collected_at": START},
    {"collected_at": START + timedelta(seconds=1)},
    {"market_updated_at": COLLECTED + timedelta(seconds=1)},
])
def test_pregame_and_source_availability_ordering_is_strict(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _record(**changes)


def test_equal_update_and_collection_times_are_allowed_and_offsets_are_retained() -> None:
    offset = timezone(timedelta(hours=-4))
    local_updated = UPDATED.astimezone(offset)
    record = _record(market_updated_at=local_updated, collected_at=UPDATED)
    assert record.market_updated_at is local_updated
    assert record.to_normalized_quote().quote_timestamp is local_updated
    assert record.to_normalized_quote().collected_at is UPDATED


@pytest.mark.parametrize("invalid", ["Yes", "No", "", "either", None, True])
def test_side_must_explicitly_be_over_or_under(invalid: object) -> None:
    with pytest.raises(ValueError, match="side"):
        _record(side=invalid)


@pytest.mark.parametrize("invalid", ["other", "", None, True])
def test_variant_must_explicitly_be_main_or_alternate(invalid: object) -> None:
    with pytest.raises(ValueError, match="market_variant"):
        _record(market_variant=invalid)


@pytest.mark.parametrize("invalid", [0, 99, -99, True, 100.0, float("nan"), float("inf"), "even", "", None, 10**400, -(10**50)])
def test_invalid_prices_are_rejected(invalid: object) -> None:
    with pytest.raises(ValueError):
        _record(american_odds=invalid)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf"), True, "0.5", None, 10**400])
def test_line_requires_a_finite_number(invalid: object) -> None:
    with pytest.raises(ValueError, match="line"):
        _record(line=invalid)


@pytest.mark.parametrize("invalid", [(), [], ["fixture"], ("",), (" ",), (None,), (["fixture"],)])
def test_source_references_must_be_nonempty_immutable_text(invalid: object) -> None:
    with pytest.raises(ValueError):
        _record(source_refs=invalid)


def test_team_identity_cannot_be_equal_ignoring_case() -> None:
    with pytest.raises(ValueError, match="different teams"):
        _record(away_team="TORONTO BLUE JAYS")


@pytest.mark.parametrize("market", [
    "batter_hits", "batter_total_bases", "batter_runs", "batter_rbis", "batter_walks",
    "batter_strikeouts", "batter_stolen_bases", "pitcher_strikeouts",
    "pitcher_outs_recorded", "pitcher_hits_allowed", "pitcher_earned_runs",
    "pitcher_walks_allowed",
])
def test_existing_mlb_player_prop_taxonomy_is_reused(market: str) -> None:
    record = _record(canonical_market_type=market)
    assert record.to_normalized_quote().market_type == market


@pytest.mark.parametrize("market", [
    "batter_runs_scored", "pitcher_outs", "batter_hits_alternate", "batter_singles",
    "player_points", "moneyline", "game_total", "Batter Hits",
])
def test_unknown_noncanonical_and_non_player_markets_are_rejected(market: str) -> None:
    with pytest.raises(ValueError):
        _record(canonical_market_type=market)


def test_normalized_quote_binds_source_facts_and_keeps_research_safety() -> None:
    record = _record(american_odds="+150", line=1, side="Under")
    quote = record.to_normalized_quote()
    assert isinstance(quote, NormalizedOddsQuote)
    assert quote.sport == quote.league == "MLB"
    assert quote.event_id == quote.raw_event_id == record.provider_event_id
    assert quote.home_team == record.home_team
    assert quote.away_team == record.away_team
    assert quote.market_type == record.canonical_market_type
    assert quote.raw_provider_market_id == record.provider_market_key
    assert quote.selection_name == record.participant_name
    assert quote.line == record.line == 1.0
    assert quote.american_odds == record.american_odds == 150
    assert quote.quote_timestamp is record.market_updated_at
    assert quote.collected_at is record.collected_at
    assert quote.event_start_time is record.commence_time
    assert quote.provider == record.provider
    assert quote.sportsbook == record.bookmaker_name
    assert quote.mode == "research"
    assert quote.source_type == "manual"
    assert quote.is_live is False
    assert quote.eligible_for_betting is False
    assert quote.kelly_eligible is False
    assert quote.approval_status == "not_approved"
    assert record.side is MLBPropSide.UNDER
    assert "Under" not in quote.selection_name


def test_evening_mlb_event_uses_toronto_operating_date() -> None:
    quote = _record().to_normalized_quote()
    assert START.date() == date(2026, 9, 21)
    assert quote.event_date == date(2026, 9, 20)


def test_alternate_raw_key_and_variant_are_retained_with_canonical_statistic() -> None:
    record = _record(
        provider_market_key="batter_hits_alternate",
        market_variant=MLBMarketVariant.ALTERNATE,
        line=1.5,
    )
    quote = record.to_normalized_quote()
    assert record.market_variant is MLBMarketVariant.ALTERNATE
    assert quote.market_type == "batter_hits"
    assert quote.raw_provider_market_id == "batter_hits_alternate"
    assert quote.line == 1.5
    assert record.side is MLBPropSide.OVER


def test_no_canonical_player_or_statsapi_identity_is_invented() -> None:
    missing = _record()
    assert missing.provider_outcome_id is None
    assert missing.provider_market_id is None
    assert missing.provider_bookmaker_id is None
    supplied = replace(missing, provider_outcome_id="observed-outcome-id")
    for record in (missing, supplied):
        quote = record.to_normalized_quote()
        assert quote.selection_id is None
        assert quote.event_id == "provider-event-1"
        for value in (record, quote):
            field_names = {field.name for field in fields(value)}
            assert not field_names & {"canonical_player_id", "gamePk", "game_pk", "identity_status"}


def test_batch_is_immutable_and_quotes_cannot_drift_from_records() -> None:
    record = _record()
    diagnostic = MLBMarketDataDiagnostic("BAD_PRICE", "price", (("provider_event_id", "event-2"),))
    batch = MLBMarketDataBatch((record,), (diagnostic,))
    assert batch.records == (record,)
    assert batch.diagnostics == (diagnostic,)
    assert batch.quotes == (record.to_normalized_quote(),)
    with pytest.raises(FrozenInstanceError):
        batch.records = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        diagnostic.category = "INVALID"  # type: ignore[misc]
    with pytest.raises(TypeError):
        diagnostic.context[0] = ("provider_event_id", "changed")  # type: ignore[index]
    assert MLBMarketDataBatch().quotes == ()


@pytest.mark.parametrize("changes", [
    {"records": []}, {"records": ({},)}, {"records": ("record",)},
    {"diagnostics": []}, {"diagnostics": ({},)},
])
def test_batch_requires_typed_immutable_tuples(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        MLBMarketDataBatch(**changes)  # type: ignore[arg-type]


@pytest.mark.parametrize("context", [
    [], [("field", "value")], (["field", "value"],), (("field", []),),
    (("", "value"),), (("field", "one"), ("field", "two")), (("field",),),
])
def test_diagnostic_context_cannot_retain_mutable_or_malformed_payloads(context: object) -> None:
    with pytest.raises(ValueError):
        MLBMarketDataDiagnostic("INVALID", "outcome", context)  # type: ignore[arg-type]


def test_diagnostic_can_preserve_explicit_blank_source_value() -> None:
    diagnostic = MLBMarketDataDiagnostic("MISSING_FIELD", "id", (("provider_event_id", ""),))
    assert diagnostic.context == (("provider_event_id", ""),)


@pytest.mark.parametrize("category", ["UNKNOWN", "", None])
def test_diagnostic_category_is_explicit(category: object) -> None:
    with pytest.raises(ValueError):
        MLBMarketDataDiagnostic(category, "outcome", ())  # type: ignore[arg-type]


def test_market_records_have_no_cross_market_mutable_state() -> None:
    hits = _record()
    bases = _record(provider_market_key="batter_total_bases", canonical_market_type="batter_total_bases", line=1.5)
    original_hits_quote = hits.to_normalized_quote()
    original_bases_quote = bases.to_normalized_quote()
    changed_bases = replace(bases, american_odds=120)
    assert changed_bases != bases
    assert hits.to_normalized_quote() == original_hits_quote
    changed_hits = replace(hits, american_odds=-150)
    assert changed_hits != hits
    assert bases.to_normalized_quote() == original_bases_quote

@pytest.mark.parametrize("field_name", ["provider_outcome_id", "provider_market_id", "provider_bookmaker_id"])
def test_explicit_integer_provider_ids_are_retained_without_canonical_identity(field_name: str) -> None:
    record = _record(**{field_name: 123})
    assert getattr(record, field_name) == 123
    assert type(getattr(record, field_name)) is int
    assert record.to_normalized_quote().selection_id is None


@pytest.mark.parametrize("invalid", ["", " ", 123, True, [], {}])
def test_optional_sport_title_requires_explicit_nonblank_text(invalid: object) -> None:
    with pytest.raises(ValueError, match="provider_sport_title"):
        _record(provider_sport_title=invalid)


@pytest.mark.parametrize("field_name,value", [
    ("market_updated_at", datetime.min.replace(tzinfo=timezone(timedelta(hours=14)))),
    ("collected_at", datetime.min.replace(tzinfo=timezone(timedelta(hours=14)))),
    ("commence_time", datetime.max.replace(tzinfo=timezone(timedelta(hours=-14)))),
])
def test_utc_conversion_overflow_is_a_source_validation_error(field_name, value) -> None:
    with pytest.raises(ValueError, match="representable"):
        _record(**{field_name: value})


def test_unrepresentable_operating_date_is_rejected_before_record_can_enter_batch() -> None:
    with pytest.raises(ValueError, match="America/Toronto"):
        _record(
            market_updated_at=datetime(1, 1, 1, 1, tzinfo=timezone.utc),
            collected_at=datetime(1, 1, 1, 2, tzinfo=timezone.utc),
            commence_time=datetime(1, 1, 1, 3, tzinfo=timezone.utc),
        )


def test_valid_dst_fold_timestamps_preserve_sources_and_compare_absolute_instants() -> None:
    toronto = ZoneInfo("America/Toronto")
    updated = datetime(2026, 11, 1, 1, 45, tzinfo=toronto, fold=0)
    collected = datetime(2026, 11, 1, 1, 15, tzinfo=toronto, fold=1)
    start = datetime(2026, 11, 1, 2, 10, tzinfo=toronto)
    record = _record(market_updated_at=updated, collected_at=collected, commence_time=start)
    quote, = MLBMarketDataBatch(records=(record,)).quotes
    assert updated.astimezone(timezone.utc) < collected.astimezone(timezone.utc)
    assert quote.quote_timestamp is updated
    assert quote.collected_at is collected
    assert quote.event_start_time is start
    assert quote.event_date == date(2026, 11, 1)


def test_dst_fold_cannot_hide_a_provider_update_after_collection() -> None:
    toronto = ZoneInfo("America/Toronto")
    with pytest.raises(ValueError, match="market_updated_at"):
        _record(
            market_updated_at=datetime(2026, 11, 1, 1, 15, tzinfo=toronto, fold=1),
            collected_at=datetime(2026, 11, 1, 1, 45, tzinfo=toronto, fold=0),
            commence_time=datetime(2026, 11, 1, 2, 10, tzinfo=toronto),
        )
