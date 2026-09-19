"""Immutable analytical candidates; no publication or promotion behavior.

Identity follows the existing NBA distinction between observed names, canonical
identifiers, resolution status, and resolution method.  These small generic
views do not resolve identities or migrate the existing sport-specific records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math

from courtvision.core.market_taxonomy import (
    MarketApplicability,
    MarketTaxonomy,
    ParticipantScope,
    StatisticDomain,
)
from courtvision.core.odds import NormalizedOddsQuote
from courtvision.core.probability import ProbabilityOutput, ThresholdDirection
from courtvision.core.reasoning import EvidenceAvailability, ReasoningAssessment


class IdentityStatus(str, Enum):
    """Resolution states, including honest name-only research and no subject."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    CONFLICTING = "conflicting"
    QUARANTINED = "quarantined"
    NAME_ONLY_RESEARCH = "name_only_research"
    NOT_APPLICABLE = "not_applicable"


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _canonical_identifier(value: object, name: str) -> str | None:
    result = _optional_text(value, name)
    if result is not None and result.casefold() in {
        "-", "<na>", "missing", "n/a", "na", "nan", "none", "not applicable",
        "not_available", "null", "tbd", "unk", "unknown", "unresolved",
    }:
        raise ValueError(f"{name} must be a resolved identifier, not a placeholder")
    return result


@dataclass(frozen=True, slots=True)
class EventIdentity:
    """The quote's event reference and any independently resolved identity."""

    event_id: str
    event_identity_method: str
    identity_status: IdentityStatus = IdentityStatus.UNRESOLVED
    canonical_event_id: str | None = None

    def __post_init__(self) -> None:
        status = IdentityStatus(self.identity_status)
        if status in {IdentityStatus.NAME_ONLY_RESEARCH, IdentityStatus.NOT_APPLICABLE}:
            raise ValueError("event identity requires an event resolution status")
        canonical_id = _canonical_identifier(self.canonical_event_id, "canonical_event_id")
        if (status is IdentityStatus.RESOLVED) != (canonical_id is not None):
            raise ValueError("canonical_event_id requires resolved event identity")
        object.__setattr__(self, "event_id", _text(self.event_id, "event_id"))
        object.__setattr__(
            self, "event_identity_method", _text(self.event_identity_method, "event_identity_method")
        )
        object.__setattr__(self, "identity_status", status)
        object.__setattr__(self, "canonical_event_id", canonical_id)

    @property
    def event_identity_status(self) -> str:
        return self.identity_status.value


@dataclass(frozen=True, slots=True)
class ParticipantIdentity:
    """Observed player/team identity without inventing a canonical identifier.

The player properties expose familiar NBA names for future player adapters.
Game-scoped markets use NOT_APPLICABLE and no participant fields.
"""

    participant_name: str | None
    identity_method: str
    identity_status: IdentityStatus = IdentityStatus.UNRESOLVED
    canonical_participant_id: str | None = None
    canonical_participant_name: str | None = None

    def __post_init__(self) -> None:
        status = IdentityStatus(self.identity_status)
        name = _optional_text(self.participant_name, "participant_name")
        canonical_id = _canonical_identifier(
            self.canonical_participant_id, "canonical_participant_id"
        )
        canonical_name = _optional_text(self.canonical_participant_name, "canonical_participant_name")
        if status is IdentityStatus.NOT_APPLICABLE:
            if any(value is not None for value in (name, canonical_id, canonical_name)):
                raise ValueError("not-applicable identity cannot contain a participant")
        elif name is None:
            raise ValueError("participant_name is required for participant identity")
        if status is IdentityStatus.RESOLVED:
            if canonical_id is None or canonical_name is None:
                raise ValueError("resolved participant identity requires canonical id and name")
        elif canonical_id is not None or canonical_name is not None:
            raise ValueError("unresolved participant identity cannot claim canonical identity")
        object.__setattr__(self, "participant_name", name)
        object.__setattr__(self, "identity_method", _text(self.identity_method, "identity_method"))
        object.__setattr__(self, "identity_status", status)
        object.__setattr__(self, "canonical_participant_id", canonical_id)
        object.__setattr__(self, "canonical_participant_name", canonical_name)

    @property
    def canonical_player_id(self) -> str | None:
        return self.canonical_participant_id

    @property
    def canonical_player_name(self) -> str | None:
        return self.canonical_participant_name

    @property
    def player_identity_status(self) -> str:
        return self.identity_status.value

    @property
    def player_identity_method(self) -> str:
        return self.identity_method


@dataclass(frozen=True, slots=True)
class CandidateProvenance:
    """Immutable source references supplied by the translating adapter.

References retain the source's values (for example a manifest reference or a
prediction run id).  The contract neither reads nor authenticates those sources.
Quote and model provenance remain attached to their existing typed objects.
"""

    source: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _text(self.source, "source"))
        if not isinstance(self.source_refs, tuple) or not self.source_refs:
            raise ValueError("source_refs must be a non-empty immutable tuple")
        for reference in self.source_refs:
            _text(reference, "source reference")


@dataclass(frozen=True, slots=True)
class MarketCandidate:
    """A bound analytical view, always research-only and never an approval.

The exact supplied quote, probability, and reasoning instances are retained.
No lifecycle writer, official-pick service, provider, or sizing API is imported.
"""

    candidate_id: str
    quote: NormalizedOddsQuote
    taxonomy: MarketTaxonomy
    probability: ProbabilityOutput
    reasoning: ReasoningAssessment
    event_identity: EventIdentity
    participant_identity: ParticipantIdentity
    provenance: CandidateProvenance
    record_kind: str = field(default="MODEL_CANDIDATE", init=False)
    research_only: bool = field(default=True, init=False)
    eligible_for_betting: bool = field(default=False, init=False)
    eligible_for_official_pick: bool = field(default=False, init=False)
    approval_status: str = field(default="not_approved", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _text(self.candidate_id, "candidate_id"))
        for name, expected_type in (
            ("quote", NormalizedOddsQuote),
            ("taxonomy", MarketTaxonomy),
            ("probability", ProbabilityOutput),
            ("reasoning", ReasoningAssessment),
            ("event_identity", EventIdentity),
            ("participant_identity", ParticipantIdentity),
            ("provenance", CandidateProvenance),
        ):
            if not isinstance(getattr(self, name), expected_type):
                raise TypeError(f"{name} must be a {expected_type.__name__}")
        if self.taxonomy.sport != self.quote.sport:
            raise ValueError("taxonomy sport does not match quote")
        if self.taxonomy.market_type != self.quote.market_type:
            raise ValueError("taxonomy market_type does not match quote")
        if self.event_identity.event_id != self.quote.event_id:
            raise ValueError("event identity does not match quote")
        if self.probability.target_statistic != self.taxonomy.statistic:
            raise ValueError("probability target_statistic does not match taxonomy")
        self._validate_participant()
        self._validate_threshold()
        for name, expected in (
            ("model_probability", self.probability.model_probability),
            ("market_implied_probability", self.quote.implied_probability),
        ):
            dimension = getattr(self.reasoning, name)
            if dimension.availability is not EvidenceAvailability.AVAILABLE:
                raise ValueError(f"reasoning {name} must be available for a bound candidate")
            if dimension.value != expected:
                raise ValueError(f"reasoning {name} does not match its bound source")
        edge = self.reasoning.edge
        if edge.availability is EvidenceAvailability.AVAILABLE:
            expected_edge = self.probability.model_probability - self.quote.implied_probability
            if edge.value != expected_edge:
                raise ValueError("reasoning edge does not match bound probabilities")

    def _validate_participant(self) -> None:
        identity = self.participant_identity
        if self.taxonomy.participant_scope is ParticipantScope.GAME:
            if identity.identity_status is not IdentityStatus.NOT_APPLICABLE:
                raise ValueError("game-scoped markets require not-applicable participant identity")
            return
        if identity.identity_status is IdentityStatus.NOT_APPLICABLE:
            raise ValueError("participant-scoped market requires participant identity")
        # Named selections can be checked without resolving a player.  Side-only
        # selections carry no participant name; their sport adapter must bind it.
        selection_name = " ".join(self.quote.selection_name.casefold().split())
        if selection_name not in {"over", "under", "yes", "no", "home", "away", "draw"}:
            names = {
                " ".join(name.casefold().split())
                for name in (identity.participant_name, identity.canonical_participant_name)
                if name is not None
            }
            if selection_name not in names:
                raise ValueError("participant identity does not match named quote selection")

    def _validate_threshold(self) -> None:
        line = self.quote.line
        applicability = self.taxonomy.line_applicability
        if applicability is MarketApplicability.REQUIRED and line is None:
            raise ValueError("market requires a quote line")
        if applicability is MarketApplicability.NOT_APPLICABLE and line is not None:
            raise ValueError("market does not permit a quote line")
        if applicability is MarketApplicability.NOT_APPLICABLE and self.probability.threshold is not None:
            raise ValueError("market does not permit a probability threshold")
        if line is None:
            return
        threshold = self.probability.threshold
        direction = self.probability.direction
        if threshold is None or direction is ThresholdDirection.NOT_APPLICABLE:
            raise ValueError("a quoted line requires a probability threshold and direction")
        discrete_target = self.taxonomy.statistic_domain in {
            StatisticDomain.DISCRETE_COUNT, StatisticDomain.DISCRETE_INTEGER
        }
        side = self.quote.selection_name.casefold()
        if side in {"over", "under"}:
            strict_direction = (
                ThresholdDirection.GREATER_THAN if side == "over"
                else ThresholdDirection.LESS_THAN
            )
            inclusive_direction = (
                ThresholdDirection.AT_LEAST if side == "over"
                else ThresholdDirection.AT_MOST
            )
            inclusive_threshold = math.floor(line) + 1 if side == "over" else math.ceil(line) - 1
            if direction is strict_direction and threshold == line:
                return
            if discrete_target and direction is inclusive_direction and threshold == inclusive_threshold:
                return
            raise ValueError("probability direction and threshold do not match the quoted side and line")
        if threshold == line:
            return
        # A discrete count greater than 0.5 is at least 1; this is a
        # mathematical target conversion, never a probability calculation.
        # Named-player quotes have no explicit side.  Their adapters must verify
        # side semantics against original evidence before constructing a view.
        if discrete_target:
            if direction is ThresholdDirection.AT_LEAST and threshold == math.floor(line) + 1:
                return
            if direction is ThresholdDirection.AT_MOST and threshold == math.ceil(line) - 1:
                return
        raise ValueError("probability threshold does not match the quoted line")


__all__ = [
    "CandidateProvenance",
    "EventIdentity",
    "IdentityStatus",
    "MarketCandidate",
    "ParticipantIdentity",
]
