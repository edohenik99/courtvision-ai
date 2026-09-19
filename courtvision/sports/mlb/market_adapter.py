"""Pure view of existing MLB HR research predictions as market candidates.

Accepts an already materialized research-baseline prediction row and its quote.
It neither runs a model nor loads artifacts. Watchlist ranking scores are not
probabilities and are deliberately not accepted. A caller must supply the
actual evidence cutoff; a quote timestamp alone cannot prove that cutoff.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
import math

from courtvision.core.candidates import (
    CandidateProvenance,
    EventIdentity,
    IdentityStatus,
    MarketCandidate,
    ParticipantIdentity,
)
from courtvision.core.market_taxonomy import resolve_market_taxonomy
from courtvision.core.odds import NormalizedOddsQuote, validate_american_odds
from courtvision.core.probability import (
    CalibrationProvenance,
    DistributionType,
    ProbabilityOutput,
    ThresholdDirection,
)
from courtvision.core.reasoning import (
    EvidenceAvailability,
    EvidenceKind,
    ReasoningAssessment,
    ReasoningDimension,
)
from courtvision.sports.mlb.player_name_normalization import normalize_mlb_player_name


def _text(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required in the research prediction")
    return value.strip()


def _number(row: Mapping[str, object], key: str) -> float:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{key} must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{key} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{key} must be a finite number")
    return result


def _timestamp(row: Mapping[str, object], key: str) -> datetime:
    result = datetime.fromisoformat(_text(row, key).replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{key} must have a timezone")
    return result


def adapt_mlb_hr_prediction(
    prediction: Mapping[str, object],
    quote: NormalizedOddsQuote,
    *,
    evidence_cutoff: datetime,
) -> MarketCandidate:
    """Translate an explicit Over 0.5 HR prediction without changing its value.

    ``model_probability`` is copied numerically, without rounding or inference.
    The quote object (including its exact implied probability) is retained.
    Legacy decimal text for the price probability is checked at its existing
    twelve-significant-digit precision, never used to replace quote values.
    Legacy player identifiers remain source references, not canonical identity.
    """
    if not isinstance(prediction, Mapping):
        raise TypeError("prediction must be an existing research prediction mapping")
    if not isinstance(quote, NormalizedOddsQuote):
        raise TypeError("quote must be a NormalizedOddsQuote")
    if (
        quote.market_identity.sport != "MLB"
        or quote.market_identity.league != "MLB"
        or quote.market_identity.market_type != "batter_home_runs"
    ):
        raise ValueError("quote must identify MLB batter_home_runs")
    if _text(prediction, "prediction_schema_version") != "mlb-hr-research-predictions-v2":
        raise ValueError("unsupported research prediction schema")
    if _text(prediction, "research_label") != "RESEARCH ONLY - NOT A VALIDATED BETTING PICK":
        raise ValueError("prediction must retain its research-only label")
    if _text(prediction, "market_key") not in {"batter_home_runs", "batter_home_runs_alternate"}:
        raise ValueError("prediction must identify the HR market")
    if (
        _text(prediction, "side").casefold() != "over"
        or _number(prediction, "point") != 0.5
        or quote.selection.line != 0.5
    ):
        raise ValueError("only explicit Over 0.5 HR has proven home_runs >= 1 semantics")
    if _text(prediction, "event_id") != quote.market_identity.event_id:
        raise ValueError("prediction event_id does not match quote")
    if date.fromisoformat(_text(prediction, "game_date")) != quote.market_identity.event_date:
        raise ValueError("prediction game_date does not match quote")
    for key in ("home_team", "away_team"):
        if _text(prediction, key).casefold() != getattr(quote.market_identity, key).casefold():
            raise ValueError(f"prediction {key} does not match quote")
    player_name = _text(prediction, "player_name")
    if normalize_mlb_player_name(player_name) != normalize_mlb_player_name(quote.selection.selection_name):
        raise ValueError("prediction player_name does not match quote")
    if _text(prediction, "sportsbook").casefold() != quote.source_metadata.sportsbook.casefold():
        raise ValueError("prediction sportsbook does not match quote")
    if validate_american_odds(prediction.get("american_odds")) != quote.american_odds:
        raise ValueError("prediction american_odds does not match quote")
    if not math.isclose(_number(prediction, "implied_probability"), quote.implied_probability, rel_tol=5e-12, abs_tol=0.0):
        raise ValueError("prediction implied_probability does not match quote")
    generated_at = _timestamp(prediction, "prediction_timestamp")
    snapshot_time = _timestamp(prediction, "snapshot_time")
    commence_time = _timestamp(prediction, "commence_time")
    if generated_at >= commence_time:
        raise ValueError("prediction_timestamp must precede commence_time")
    if quote.event_start_time is not None and quote.event_start_time != commence_time:
        raise ValueError("prediction commence_time does not match quote")
    if not isinstance(evidence_cutoff, datetime) or evidence_cutoff.utcoffset() is None:
        raise ValueError("evidence_cutoff must be a timezone-aware datetime")
    if not snapshot_time <= evidence_cutoff <= generated_at:
        raise ValueError("snapshot/evidence cutoff/prediction timestamps are inconsistent")
    if quote.quote_timestamp is not None and quote.quote_timestamp != snapshot_time:
        raise ValueError("prediction snapshot_time does not match quote")
    probability = ProbabilityOutput(
        model_probability=_number(prediction, "model_probability"),
        target_statistic="home_runs",
        threshold=1,
        direction=ThresholdDirection.AT_LEAST,
        distribution_type=DistributionType.BERNOULLI_EVENT,
        model_id=_text(prediction, "model_id"),
        model_version=_text(prediction, "model_version"),
        feature_schema_version=_text(prediction, "feature_schema_version"),
        calibration_provenance=CalibrationProvenance(
            availability=EvidenceAvailability.UNAVAILABLE,
            reason="The prediction row does not include fitted calibration provenance.",
        ),
        generated_at=generated_at,
        evidence_cutoff=evidence_cutoff,
    )
    reasoning = ReasoningAssessment(
        model_probability=ReasoningDimension.available(probability.model_probability, EvidenceKind.MODEL_OUTPUT),
        market_implied_probability=ReasoningDimension.available(quote.implied_probability, EvidenceKind.DERIVED_REASONING_FEATURE),
        identity_quality=ReasoningDimension.available("name_only_research", EvidenceKind.FACTUAL_EVIDENCE),
        threshold_cushion=ReasoningDimension.unavailable(EvidenceKind.DERIVED_REASONING_FEATURE, "No main-versus-alternate comparison is supplied."),
    )
    reference_keys = (
        "prediction_id", "prediction_run_id", "prediction_schema_version",
        "research_label", "source_manifest_reference", "source_odds_sha256",
        "repository_commit_sha", "model_bundle_path", "player_id", "player_name",
        "market_key", "side", "point", "snapshot_time", "eligibility_status",
        "exclusion_reason", "probability_edge",
    )
    references = tuple(
        f"{key}={prediction[key]}" for key in reference_keys
        if isinstance(prediction.get(key), str) and prediction[key]
    )
    return MarketCandidate(
        candidate_id=_text(prediction, "prediction_id"),
        quote=quote,
        taxonomy=resolve_market_taxonomy("MLB", "batter_home_runs"),
        probability=probability,
        reasoning=reasoning,
        event_identity=EventIdentity(
            event_id=quote.market_identity.event_id,
            event_identity_method="source_event_id",
        ),
        participant_identity=ParticipantIdentity(
            participant_name=quote.selection.selection_name,
            identity_method="normalized_name_only",
            identity_status=IdentityStatus.NAME_ONLY_RESEARCH,
        ),
        provenance=CandidateProvenance(source="mlb_hr_research_prediction", source_refs=references),
    )


__all__ = ["adapt_mlb_hr_prediction"]
