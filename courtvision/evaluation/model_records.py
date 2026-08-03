"""Normalized records for the Phase 1 model-evaluation dashboard.

The contract in this module is intentionally observational.  It contains no
wager sizing, bankroll, publication, execution, or model-running fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
import hashlib
import json
import math
from typing import Any


SCHEMA_VERSION = "1.0"
EVALUATION_POPULATION = "nba_elite_legacy"
EVIDENCE_CLASS = "legacy_observational"
SPORT = "NBA"
LEAGUE = "NBA"
CONFIDENCE_SEMANTICS = "legacy_score_not_probability"
EDGE_TYPE = "stat_units"


class Outcome(str, Enum):
    """Supported normalized outcome states for Phase 1."""

    WIN = "WIN"
    LOSS = "LOSS"
    PUSH = "PUSH"
    VOID = "VOID"
    PENDING = "PENDING"
    UNSUPPORTED = "UNSUPPORTED"


_OUTCOME_ALIASES = {
    "hit": Outcome.WIN,
    "miss": Outcome.LOSS,
    "push": Outcome.PUSH,
    "void": Outcome.VOID,
    "pending": Outcome.PENDING,
}


def normalize_outcome(value: Any) -> Outcome:
    """Map the legacy result vocabulary to the closed Phase 1 outcome enum."""

    normalized = "" if value is None else str(value).strip().casefold()
    return _OUTCOME_ALIASES.get(normalized, Outcome.UNSUPPORTED)


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_american_odds(value: Any) -> int | None:
    """Return valid integer American odds, or ``None`` without substitution."""

    parsed = _finite_float(value)
    if parsed is None or not parsed.is_integer() or abs(parsed) < 100:
        return None
    return int(parsed)


def american_to_decimal(value: Any) -> float | None:
    """Convert valid American odds to decimal odds without applying defaults."""

    american = parse_american_odds(value)
    if american is None:
        return None
    if american > 0:
        return 1.0 + (american / 100.0)
    return 1.0 + (100.0 / abs(american))


def deterministic_record_id(
    *,
    source_name: str,
    prediction_date: date,
    participant_id: str | None,
    participant_name: str,
    team: str | None,
    opponent: str | None,
    event_id: str | None,
    market: str,
    selection: str,
    line: float | None,
) -> str:
    """Build a stable identifier from source identity fields, not row position."""

    identity = {
        "source_name": source_name.casefold(),
        "prediction_date": prediction_date.isoformat(),
        "participant": (participant_id or participant_name).strip().casefold(),
        "team": (team or "").strip().casefold(),
        "opponent": (opponent or "").strip().casefold(),
        "event_id": (event_id or "").strip().casefold(),
        "market": market.strip().casefold(),
        "selection": selection.strip().casefold(),
        "line": line,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"cv1_{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class ModelEvaluationRecord:
    """Immutable normalized record used by every Phase 1 evaluation metric."""

    schema_version: str
    record_id: str
    source_name: str
    source_path: str
    source_row_number: int
    evaluation_population: str
    evidence_class: str
    sport: str
    league: str
    prediction_date: date
    participant_id: str | None
    participant_name: str
    team: str | None
    opponent: str | None
    event_id: str | None
    market: str
    selection: str
    line: float | None
    prediction_value: float | None
    odds_american: int | None
    odds_decimal: float | None
    odds_valid: bool
    confidence: float | None
    confidence_semantics: str
    edge_value: float | None
    edge_type: str
    outcome: Outcome
    actual_value: float | None
    hit_rate_eligible: bool
    roi_eligible: bool
    exclusion_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        fixed_values = {
            "schema_version": (self.schema_version, SCHEMA_VERSION),
            "evaluation_population": (
                self.evaluation_population,
                EVALUATION_POPULATION,
            ),
            "evidence_class": (self.evidence_class, EVIDENCE_CLASS),
            "sport": (self.sport, SPORT),
            "league": (self.league, LEAGUE),
            "confidence_semantics": (
                self.confidence_semantics,
                CONFIDENCE_SEMANTICS,
            ),
            "edge_type": (self.edge_type, EDGE_TYPE),
        }
        for field_name, (actual, expected) in fixed_values.items():
            if actual != expected:
                raise ValueError(f"{field_name} must be {expected!r}")

        if self.source_row_number < 2:
            raise ValueError("source_row_number must identify a CSV data row")
        if not self.record_id:
            raise ValueError("record_id is required")
        if not self.source_name or not self.source_path:
            raise ValueError("source provenance is required")
        if not self.participant_name.strip():
            raise ValueError("participant_name is required")
        if not self.market.strip() or not self.selection.strip():
            raise ValueError("market and selection are required")

        expected_hit_rate = self.outcome in {Outcome.WIN, Outcome.LOSS}
        expected_roi = self.odds_valid and self.outcome in {
            Outcome.WIN,
            Outcome.LOSS,
            Outcome.PUSH,
        }
        if self.hit_rate_eligible != expected_hit_rate:
            raise ValueError("hit_rate_eligible is inconsistent with outcome")
        if self.roi_eligible != expected_roi:
            raise ValueError("roi_eligible is inconsistent with outcome and odds")

        expected_decimal = american_to_decimal(self.odds_american)
        if self.odds_valid != (expected_decimal is not None):
            raise ValueError("odds_valid is inconsistent with odds_american")
        if self.odds_decimal != expected_decimal:
            raise ValueError("odds_decimal is inconsistent with odds_american")
        if not isinstance(self.exclusion_reasons, tuple):
            raise TypeError("exclusion_reasons must be an immutable tuple")


def create_model_evaluation_record(
    *,
    source_name: str,
    source_path: str,
    source_row_number: int,
    prediction_date: date,
    participant_id: str | None,
    participant_name: str,
    team: str | None,
    opponent: str | None,
    event_id: str | None,
    market: str,
    selection: str,
    line: float | None,
    prediction_value: float | None,
    raw_odds: Any,
    confidence: float | None,
    edge_value: float | None,
    raw_outcome: Any,
    actual_value: float | None,
) -> ModelEvaluationRecord:
    """Create a validated record while deriving odds and eligibility fields."""

    outcome = normalize_outcome(raw_outcome)
    odds_american = parse_american_odds(raw_odds)
    odds_decimal = american_to_decimal(odds_american)
    odds_valid = odds_decimal is not None
    hit_rate_eligible = outcome in {Outcome.WIN, Outcome.LOSS}
    roi_eligible = odds_valid and outcome in {
        Outcome.WIN,
        Outcome.LOSS,
        Outcome.PUSH,
    }

    exclusions: list[str] = []
    if not participant_id:
        exclusions.append("missing_participant_id")
    if not event_id:
        exclusions.append("missing_event_id")
    if raw_odds is None or not str(raw_odds).strip():
        exclusions.append("missing_odds")
    elif not odds_valid:
        exclusions.append("invalid_odds")
    if outcome is Outcome.UNSUPPORTED:
        exclusions.append("unsupported_outcome")
    if not hit_rate_eligible:
        exclusions.append("hit_rate_ineligible")
    if not roi_eligible:
        exclusions.append("roi_ineligible")

    record_id = deterministic_record_id(
        source_name=source_name,
        prediction_date=prediction_date,
        participant_id=participant_id,
        participant_name=participant_name,
        team=team,
        opponent=opponent,
        event_id=event_id,
        market=market,
        selection=selection,
        line=line,
    )
    return ModelEvaluationRecord(
        schema_version=SCHEMA_VERSION,
        record_id=record_id,
        source_name=source_name,
        source_path=source_path,
        source_row_number=source_row_number,
        evaluation_population=EVALUATION_POPULATION,
        evidence_class=EVIDENCE_CLASS,
        sport=SPORT,
        league=LEAGUE,
        prediction_date=prediction_date,
        participant_id=participant_id,
        participant_name=participant_name,
        team=team,
        opponent=opponent,
        event_id=event_id,
        market=market,
        selection=selection,
        line=line,
        prediction_value=prediction_value,
        odds_american=odds_american,
        odds_decimal=odds_decimal,
        odds_valid=odds_valid,
        confidence=confidence,
        confidence_semantics=CONFIDENCE_SEMANTICS,
        edge_value=edge_value,
        edge_type=EDGE_TYPE,
        outcome=outcome,
        actual_value=actual_value,
        hit_rate_eligible=hit_rate_eligible,
        roi_eligible=roi_eligible,
        exclusion_reasons=tuple(exclusions),
    )


__all__ = [
    "CONFIDENCE_SEMANTICS",
    "EDGE_TYPE",
    "EVALUATION_POPULATION",
    "EVIDENCE_CLASS",
    "LEAGUE",
    "ModelEvaluationRecord",
    "Outcome",
    "SCHEMA_VERSION",
    "SPORT",
    "american_to_decimal",
    "create_model_evaluation_record",
    "deterministic_record_id",
    "normalize_outcome",
    "parse_american_odds",
]
