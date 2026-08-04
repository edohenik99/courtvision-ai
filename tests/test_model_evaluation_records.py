from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import date

import pytest

from courtvision.evaluation.model_records import (
    CONFIDENCE_SEMANTICS,
    EDGE_TYPE,
    ELITE_EVALUATION_POPULATION,
    EVALUATION_POPULATION,
    EVALUATION_POPULATIONS,
    EVIDENCE_CLASS,
    FEEDBACK_EVALUATION_POPULATION,
    ModelEvaluationRecord,
    Outcome,
    american_to_decimal,
    create_model_evaluation_record,
    normalize_outcome,
    parse_american_odds,
)


def _record(
    *,
    outcome: str = "hit",
    odds: object = "-110",
    evaluation_population: str = EVALUATION_POPULATION,
    source_name: str = "pick_history.csv",
    source_path: str = "C:/research/pick_history.csv",
    source_row_number: int = 2,
    source_identity: str | None = None,
) -> ModelEvaluationRecord:
    return create_model_evaluation_record(
        source_name=source_name,
        source_path=source_path,
        source_row_number=source_row_number,
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
        evaluation_population=evaluation_population,
        source_identity=source_identity,
    )


def test_record_is_immutable_and_has_fixed_observational_contract() -> None:
    record = _record()

    assert record.evaluation_population == EVALUATION_POPULATION
    assert record.evaluation_population == ELITE_EVALUATION_POPULATION
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
        ("WIN", Outcome.WIN),
        ("Miss", Outcome.LOSS),
        ("loss", Outcome.LOSS),
        ("pUsH", Outcome.PUSH),
        ("VOID", Outcome.VOID),
        ("pending", Outcome.PENDING),
        ("unresolved", Outcome.PENDING),
        ("ungraded", Outcome.UNSUPPORTED),
        ("unsupported", Outcome.UNSUPPORTED),
        ("unknown", Outcome.UNSUPPORTED),
        ("", Outcome.UNSUPPORTED),
        ("cancelled", Outcome.UNSUPPORTED),
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


@pytest.mark.parametrize("population", sorted(EVALUATION_POPULATIONS))
def test_exactly_two_evaluation_populations_are_permitted(population: str) -> None:
    assert EVALUATION_POPULATIONS == {
        ELITE_EVALUATION_POPULATION,
        FEEDBACK_EVALUATION_POPULATION,
    }
    record = _record(evaluation_population=population)
    assert record.evaluation_population == population


@pytest.mark.parametrize("population", ["", "nba", "all", "nba_shadow_legacy"])
def test_every_other_evaluation_population_is_rejected(population: str) -> None:
    with pytest.raises(ValueError, match="evaluation_population must be one of"):
        _record(evaluation_population=population)


def test_feedback_source_identity_id_is_stable_and_source_namespaced() -> None:
    grade_key = "2026-05-01|player_points|Sample Player|over|20.5"
    left = _record(
        outcome="win",
        evaluation_population=FEEDBACK_EVALUATION_POPULATION,
        source_name="result_feedback.csv",
        source_path="C:/left/result_feedback.csv",
        source_row_number=2,
        source_identity=grade_key,
    )
    right = _record(
        outcome="win",
        evaluation_population=FEEDBACK_EVALUATION_POPULATION,
        source_name="result_feedback.csv",
        source_path="D:/right/result_feedback.csv",
        source_row_number=99,
        source_identity=grade_key,
    )
    elite = _record()

    assert left.record_id == right.record_id
    assert left.record_id != elite.record_id
    assert left.source_row_number == 2
    assert right.source_row_number == 99


@pytest.mark.parametrize(
    ("outcome", "expected", "hit_rate_eligible", "roi_eligible"),
    [
        ("win", Outcome.WIN, True, True),
        ("loss", Outcome.LOSS, True, True),
        ("push", Outcome.PUSH, False, True),
        ("unresolved", Outcome.PENDING, False, False),
        ("ungraded", Outcome.UNSUPPORTED, False, False),
    ],
)
def test_feedback_outcome_eligibility(
    outcome: str,
    expected: Outcome,
    hit_rate_eligible: bool,
    roi_eligible: bool,
) -> None:
    record = _record(
        outcome=outcome,
        evaluation_population=FEEDBACK_EVALUATION_POPULATION,
        source_name="result_feedback.csv",
        source_identity=f"key-{outcome}",
    )

    assert record.outcome is expected
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
