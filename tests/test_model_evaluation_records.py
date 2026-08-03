from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import date

import pytest

from courtvision.evaluation.model_records import (
    CONFIDENCE_SEMANTICS,
    EDGE_TYPE,
    EVALUATION_POPULATION,
    EVIDENCE_CLASS,
    ModelEvaluationRecord,
    Outcome,
    american_to_decimal,
    create_model_evaluation_record,
    normalize_outcome,
    parse_american_odds,
)


def _record(*, outcome: str = "hit", odds: object = "-110") -> ModelEvaluationRecord:
    return create_model_evaluation_record(
        source_name="pick_history.csv",
        source_path="C:/research/pick_history.csv",
        source_row_number=2,
        prediction_date=date(2026, 5, 1),
        participant_id="player-1",
        participant_name="Sample Player",
        team="TOR",
        opponent="BOS",
        event_id="game-1",
        market="player_points",
        selection="over",
        line=20.5,
        prediction_value=23.0,
        raw_odds=odds,
        confidence=0.76,
        edge_value=2.5,
        raw_outcome=outcome,
        actual_value=25.0,
    )


def test_record_is_immutable_and_has_fixed_observational_contract() -> None:
    record = _record()

    assert record.evaluation_population == EVALUATION_POPULATION
    assert record.evidence_class == EVIDENCE_CLASS
    assert record.sport == "NBA"
    assert record.league == "NBA"
    assert record.confidence_semantics == CONFIDENCE_SEMANTICS
    assert record.edge_type == EDGE_TYPE
    assert isinstance(record.exclusion_reasons, tuple)
    with pytest.raises(FrozenInstanceError):
        record.market = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" HIT ", Outcome.WIN),
        ("Miss", Outcome.LOSS),
        ("pUsH", Outcome.PUSH),
        ("VOID", Outcome.VOID),
        ("pending", Outcome.PENDING),
        ("cancelled", Outcome.UNSUPPORTED),
        ("unresolved", Outcome.UNSUPPORTED),
        ("unexpected", Outcome.UNSUPPORTED),
        (None, Outcome.UNSUPPORTED),
    ],
)
def test_outcome_normalization_is_case_insensitive(
    raw: object, expected: Outcome
) -> None:
    assert normalize_outcome(raw) is expected


def test_american_to_decimal_positive_and_negative() -> None:
    assert american_to_decimal(+150) == pytest.approx(2.5)
    assert american_to_decimal(-200) == pytest.approx(1.5)
    assert parse_american_odds("+110") == 110


@pytest.mark.parametrize("raw", [None, "", "bad", 0, 99, -99, 110.5, float("nan")])
def test_invalid_or_missing_odds_are_never_defaulted(raw: object) -> None:
    assert parse_american_odds(raw) is None
    assert american_to_decimal(raw) is None
    record = _record(odds=raw)
    assert record.odds_american is None
    assert record.odds_decimal is None
    assert record.odds_valid is False
    assert record.roi_eligible is False


@pytest.mark.parametrize(
    ("outcome", "hit_rate_eligible", "roi_eligible"),
    [
        ("hit", True, True),
        ("miss", True, True),
        ("push", False, True),
        ("void", False, False),
        ("pending", False, False),
        ("other", False, False),
    ],
)
def test_eligibility_is_derived_from_outcome_and_real_odds(
    outcome: str, hit_rate_eligible: bool, roi_eligible: bool
) -> None:
    record = _record(outcome=outcome)
    assert record.hit_rate_eligible is hit_rate_eligible
    assert record.roi_eligible is roi_eligible


def test_normalized_contract_has_no_operational_or_sizing_fields() -> None:
    names = {field.name.casefold() for field in fields(ModelEvaluationRecord)}
    forbidden_fragments = (
        "stake",
        "bankroll",
        "wager",
        "kelly",
        "promotion",
        "execution",
    )
    assert not {
        name
        for name in names
        if any(fragment in name for fragment in forbidden_fragments)
    }
