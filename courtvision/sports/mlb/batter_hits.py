"""Pure, uncalibrated research baseline and adapter for MLB batter 1+ hits.

The naive independent-at-bat baseline assumes a constant per-at-bat hit
probability equal to season hits / season at-bats and independent at-bats.
Projected at-bats must be supplied externally; a fractional projection is used
as an exponent, not asserted to be a full distribution of playing opportunity.
There is no pitcher, park/weather, or recent-form adjustment and no calibrated
uncertainty. Lineup status is supplied evidence, never inferred confirmation.
This module neither fetches evidence nor authenticates the caller's sources.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
import math

from courtvision.core.candidates import (
    CandidateProvenance,
    EventIdentity,
    IdentityStatus,
    MarketCandidate,
    ParticipantIdentity,
)
from courtvision.core.market_taxonomy import resolve_market_taxonomy
from courtvision.core.odds import NormalizedOddsQuote
from courtvision.core.probability import (
    CalibrationProvenance,
    DistributionType,
    ProbabilityOutput,
    ProbabilityUncertainty,
    ThresholdDirection,
)
from courtvision.core.reasoning import (
    EvidenceAvailability,
    EvidenceKind,
    ReasoningAssessment,
    ReasoningDimension,
)
from courtvision.sports.mlb.player_name_normalization import normalize_mlb_player_name


MODEL_ID = "mlb-batter-hits-independent-at-bat-baseline"
MODEL_VERSION = "research-v1"
FEATURE_SCHEMA_VERSION = "mlb-batter-hits-baseline-features-v1"


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _aware(value: object, name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")


def _refs(value: object) -> None:
    if not isinstance(value, tuple) or not value:
        raise ValueError("source_refs must be a non-empty immutable tuple")
    for reference in value:
        _text(reference, "source reference")


def _name(value: object) -> str:
    _text(value, "batter name")
    normalized = normalize_mlb_player_name(value)
    if not normalized:
        raise ValueError("batter name must have a non-empty normalized name")
    return normalized


@dataclass(frozen=True, slots=True)
class BatterHitsBaselineFeatures:
    """Point-in-time baseball evidence, with no price or market probability.

    The caller attests every source was available by evidence_cutoff, including
    the season totals and external opportunity projection. Batter and event
    references prevent accidentally attaching another subject's feature set.
    The cutoff describes only evidence used by the specialist model, independent
    of quote receipt or candidate assembly. Source references are retained;
    they are not opened or resolved here.
    """

    season_hits: int
    season_at_bats: int
    projected_at_bats: float
    lineup_status: str
    evidence_cutoff: datetime
    source_refs: tuple[str, ...]
    batter_name: str
    event_id: str

    def __post_init__(self) -> None:
        for name in ("season_hits", "season_at_bats"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.season_at_bats == 0 or self.season_hits > self.season_at_bats:
            raise ValueError("season_at_bats must be positive and at least season_hits")
        projected = self.projected_at_bats
        if isinstance(projected, bool) or not isinstance(projected, (int, float)):
            raise ValueError("projected_at_bats must be finite and positive")
        try:
            valid = math.isfinite(projected) and projected > 0
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("projected_at_bats must be finite and positive")
        _text(self.lineup_status, "lineup_status")
        _text(self.event_id, "event_id")
        _name(self.batter_name)
        _aware(self.evidence_cutoff, "evidence_cutoff")
        _refs(self.source_refs)


@dataclass(frozen=True, slots=True)
class BatterHitsSourceEvidence:
    """Explicit source semantics supplementing, never replacing, the quote.

    Event, provider, book and snapshot bind the side evidence to its quote.
    Only the supplied Over 0.5 batter_hits observation is supported.
    """

    market: str
    side: str
    point: float
    batter_name: str
    snapshot_timestamp: datetime
    event_id: str
    provider: str
    sportsbook: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.market != "batter_hits":
            raise ValueError("source market must be batter_hits")
        if not isinstance(self.side, str) or self.side.strip().casefold() != "over":
            raise ValueError("source side must explicitly be Over")
        if isinstance(self.point, bool) or not isinstance(self.point, (int, float)) or self.point != 0.5:
            raise ValueError("only explicit Over 0.5 hits is supported")
        _name(self.batter_name)
        for name in ("event_id", "provider", "sportsbook"):
            _text(getattr(self, name), name)
        _aware(self.snapshot_timestamp, "snapshot_timestamp")
        _refs(self.source_refs)


def compute_batter_hits_probability(
    features: BatterHitsBaselineFeatures, *, generated_at: datetime,
) -> ProbabilityOutput:
    """Return 1 - (1 - season_hits / season_at_bats) ** projected_at_bats.

    No quote, odds, inferred opportunity, confidence weight or fitted calibration
    enters this function. The caller supplies the generation time explicitly.
    """
    if not isinstance(features, BatterHitsBaselineFeatures):
        raise TypeError("features must be BatterHitsBaselineFeatures")
    per_at_bat_hit_rate = features.season_hits / features.season_at_bats
    return ProbabilityOutput(
        model_probability=1 - (1 - per_at_bat_hit_rate) ** features.projected_at_bats,
        target_statistic="hits",
        threshold=1,
        direction=ThresholdDirection.AT_LEAST,
        distribution_type=DistributionType.BERNOULLI_EVENT,
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        calibration_provenance=CalibrationProvenance(
            EvidenceAvailability.UNAVAILABLE,
            reason="This naive independent-at-bat research baseline has not been calibrated.",
        ),
        uncertainty=ProbabilityUncertainty(
            EvidenceAvailability.UNAVAILABLE,
            reason="No evidence-based uncertainty model is available for this baseline.",
        ),
        generated_at=generated_at,
        evidence_cutoff=features.evidence_cutoff,
    )


def assemble_batter_hits_candidate(
    *,
    candidate_id: str,
    quote: NormalizedOddsQuote,
    source_evidence: BatterHitsSourceEvidence,
    features: BatterHitsBaselineFeatures,
    probability: ProbabilityOutput,
    participant_identity: ParticipantIdentity,
    event_identity: EventIdentity,
    provenance: CandidateProvenance,
) -> MarketCandidate:
    """Bind supplied pregame evidence into an unapproved analytical candidate.

    A resolved MLB player ID must be explicitly supplied as a positive decimal
    identifier with a compatible canonical name. This pure adapter cannot
    authenticate an identity registry; the identity method/provenance records
    the caller's resolution. Name-only research never acquires a canonical ID.
    Keep participant_name in the quote's observed spelling; source, feature and
    canonical names may use the shared MLB normalization's compatible aliases.
    Model evidence and quote observation have independent pregame timelines.
    """
    for name, value, expected in (
        ("quote", quote, NormalizedOddsQuote),
        ("source_evidence", source_evidence, BatterHitsSourceEvidence),
        ("features", features, BatterHitsBaselineFeatures),
        ("probability", probability, ProbabilityOutput),
        ("participant_identity", participant_identity, ParticipantIdentity),
        ("event_identity", event_identity, EventIdentity),
        ("provenance", provenance, CandidateProvenance),
    ):
        if not isinstance(value, expected):
            raise TypeError(f"{name} must be {expected.__name__}")
    if quote.sport != "MLB" or quote.league != "MLB" or quote.market_type != "batter_hits":
        raise ValueError("quote must identify MLB batter_hits")
    if quote.line != 0.5:
        raise ValueError("quote must have line 0.5 for 1+ hits")
    if quote.raw_provider_market_id is not None and quote.raw_provider_market_id != source_evidence.market:
        raise ValueError("raw provider market must match the explicit source market")
    if quote.eligible_for_betting or quote.kelly_eligible or quote.approval_status != "not_approved":
        raise ValueError("quote must be ineligible and not_approved")
    if not features.event_id == source_evidence.event_id == event_identity.event_id == quote.event_id:
        raise ValueError("feature, source, event identity and quote event_id must match")
    if event_identity.identity_status not in {IdentityStatus.UNRESOLVED, IdentityStatus.RESOLVED}:
        raise ValueError("conflicting or ambiguous event identity is unsupported")
    if source_evidence.provider != quote.provider or source_evidence.sportsbook != quote.sportsbook:
        raise ValueError("source provider and sportsbook must match quote")
    identity = participant_identity
    if identity.identity_status not in {IdentityStatus.NAME_ONLY_RESEARCH, IdentityStatus.RESOLVED}:
        raise ValueError("participant identity must be name_only_research or resolved")
    if identity.identity_status is IdentityStatus.RESOLVED:
        player_id = identity.canonical_player_id
        if not player_id.isascii() or not player_id.isdecimal() or player_id.startswith("0"):
            raise ValueError("resolved canonical MLB player ID must be a positive decimal identifier")
    names = [source_evidence.batter_name, features.batter_name, identity.participant_name]
    if identity.canonical_player_name is not None:
        names.append(identity.canonical_player_name)
    # This slice requires the normalized quote to retain the batter's name.
    # A side-only quote cannot prove which batter its price belongs to.
    if quote.selection_name.casefold() in {"over", "under", "yes", "no"}:
        raise ValueError("quote must retain its batter name, not a side-only selection")
    names.append(quote.selection_name)
    if len({_name(name) for name in names}) != 1:
        raise ValueError("conflicting batter names across feature, source, identity or quote")
    for name in ("quote_timestamp", "collected_at", "event_start_time"):
        _aware(getattr(quote, name), name)
    if source_evidence.snapshot_timestamp != quote.quote_timestamp:
        raise ValueError("source snapshot_timestamp must match quote_timestamp")
    if quote.is_live is True or not (
        quote.quote_timestamp <= quote.collected_at < quote.event_start_time
        and features.evidence_cutoff <= probability.generated_at < quote.event_start_time
    ):
        raise ValueError("quote, evidence_cutoff and generation must be consistent and pregame")
    expected_probability = compute_batter_hits_probability(features, generated_at=probability.generated_at)
    if probability != expected_probability:
        raise ValueError("probability must match the specialist baseline and feature provenance exactly")

    fact = EvidenceKind.FACTUAL_EVIDENCE
    model = EvidenceKind.MODEL_OUTPUT
    derived = EvidenceKind.DERIVED_REASONING_FEATURE
    reasoning = ReasoningAssessment(
        model_probability=ReasoningDimension.available(probability.model_probability, model),
        market_implied_probability=ReasoningDimension.available(quote.implied_probability, derived),
        edge=ReasoningDimension.available(probability.model_probability - quote.implied_probability, derived),
        opportunity=ReasoningDimension.available(
            features.projected_at_bats, derived, reason="Externally supplied projected at-bats, not realized at-bats.",
        ),
        identity_quality=ReasoningDimension.available(identity.identity_status.value, fact),
        threshold_cushion=ReasoningDimension.unavailable(derived, "No main-versus-alternate comparison supplied."),
        projection_confidence=ReasoningDimension.unavailable(model, "No evidence-based confidence model supplied."),
        matchup=ReasoningDimension.unavailable(fact, "No pitcher matchup evidence supplied."),
        environment=ReasoningDimension.unavailable(fact, "No park or weather evidence supplied."),
        volatility=ReasoningDimension.unavailable(model, "No volatility model supplied."),
        market_movement=ReasoningDimension.unavailable(derived, "No multiple time-bound market observations supplied."),
    )
    # The generic candidate has no market-specific feature slots. Retain every
    # supplied feature/source value as immutable provenance, including refs.
    references = list(provenance.source_refs)
    for prefix, evidence in (("features", features), ("source", source_evidence)):
        for descriptor in fields(evidence):
            value = getattr(evidence, descriptor.name)
            if descriptor.name == "source_refs":
                references.extend(value)
            else:
                text = value.isoformat() if isinstance(value, datetime) else str(value)
                references.append(f"{prefix}.{descriptor.name}={text}")
    return MarketCandidate(
        candidate_id=candidate_id,
        quote=quote,
        taxonomy=resolve_market_taxonomy("MLB", "batter_hits"),
        probability=probability,
        reasoning=reasoning,
        event_identity=event_identity,
        participant_identity=participant_identity,
        provenance=CandidateProvenance(source=provenance.source, source_refs=tuple(references)),
    )


__all__ = [
    "BatterHitsBaselineFeatures",
    "BatterHitsSourceEvidence",
    "assemble_batter_hits_candidate",
    "compute_batter_hits_probability",
]
