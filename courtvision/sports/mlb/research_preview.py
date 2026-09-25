"""Offline, unapproved display contracts for the existing MLB research models."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from enum import Enum
import json
import math
from typing import Mapping
from zoneinfo import ZoneInfo

from courtvision.core.candidates import CandidateProvenance
from courtvision.sports.mlb.ab_projection import (
    AB_PROJECTION_V2_METHOD, AB_PROJECTION_V2_VERSION, assemble_sovereign_batter_hits_features,
)
from courtvision.sports.mlb.batter_hits import (
    MODEL_ID, MODEL_VERSION, assemble_batter_hits_candidate,
    batter_hits_source_evidence_from_market_record, compute_batter_hits_probability,
)
from courtvision.sports.mlb.hits_acquisition import AcquiredBatterHitsEvidence, SovereignBatterHitsEvidence
from courtvision.sports.mlb.hits_season_ledger import CANONICAL_HITS_SEASON_SOURCE, HitsLedgerError
from courtvision.sports.mlb.fact_ledger import _unique_object
from courtvision.sports.mlb.game_facts import _hash, _id
from courtvision.sports.mlb.market_adapter import adapt_mlb_hr_prediction
from courtvision.sports.mlb.market_data import MLBPlayerPropSourceRecord

LEGACY_PREVIEW_SCHEMA_VERSION = "mlb-research-preview-v1"
PREVIEW_SCHEMA_VERSION = "mlb-research-preview-v2"
LEGACY_HITS_PROVENANCE_UNQUALIFIED = "LEGACY_HITS_PROVENANCE_UNQUALIFIED"
OPERATING_TIMEZONE = ZoneInfo("America/Toronto")
HITS_LIMITATION = "NAIVE_UNCALIBRATED_BASELINE"
HR_LIMITATION = "LEGACY_MARKET_CONTAMINATED"
HR_WARNING = (
    "CURRENT HR MODEL IS RESEARCH-ONLY AND MARKET-CONTAMINATED. "
    "ITS PROBABILITY IS DISPLAYED FOR INSPECTION, NOT AS A SOVEREIGN COURTVISION "
    "PROBABILITY. Its feature lineage contains sportsbook-derived information."
)


class PredictionStatus(str, Enum):
    QUALIFIED_RESEARCH = "QUALIFIED_RESEARCH"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"
    LEGACY_RESEARCH = "LEGACY_RESEARCH"


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class MLBResearchPreviewRow:
    operating_date: str
    market_type: str
    prediction_status: str
    preview_schema_version: str = PREVIEW_SCHEMA_VERSION
    sport: str = "MLB"
    market_family: str = "player_prop"
    market_display: str = ""
    provider_event_id: str | None = None
    canonical_event_id: str | None = None
    commence_time_utc: str | None = None
    player_id: str | None = None
    player_name: str | None = None
    team: str | None = None
    opponent: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    sportsbook: str | None = None
    line: float | None = None
    american_odds: int | None = None
    market_implied_probability: float | None = None
    market_timestamp_utc: str | None = None
    model_probability: float | None = None
    model_id: str | None = None
    model_version: str | None = None
    prediction_timestamp_utc: str | None = None
    block_reason: str | None = None
    block_detail: str | None = None
    limitation_status: str = ""
    probability_market_independence: str = "NOT_TESTED"
    research_only: bool = True
    approval_status: str = "not_approved"
    eligible_for_betting: bool = False
    kelly_eligible: bool = False
    evidence_cutoff: str | None = None
    source_refs: tuple[str, ...] = ()
    identity_status: str = "unresolved"
    event_status: str = "unresolved"
    lineup_status: str = "unavailable"
    season_hits: int | None = None
    season_at_bats: int | None = None
    projected_at_bats: float | None = None
    row_kind: str = "PLAYER"
    season_source: str | None = None
    season_aggregate_hash: str | None = None
    distinct_batting_games: int | None = None
    ab_projection_version: str | None = None

    def __post_init__(self) -> None:
        date.fromisoformat(self.operating_date)
        status = PredictionStatus(self.prediction_status)
        if self.preview_schema_version not in {PREVIEW_SCHEMA_VERSION, LEGACY_PREVIEW_SCHEMA_VERSION} or self.sport != "MLB":
            raise ValueError("unsupported preview schema or sport")
        if self.market_type not in {"batter_hits", "batter_home_runs"}:
            raise ValueError("unsupported preview market")
        if (self.research_only is not True or self.eligible_for_betting is not False
                or self.kelly_eligible is not False or self.approval_status != "not_approved"):
            raise ValueError("preview must remain unapproved research")
        for value in (self.model_probability, self.market_implied_probability):
            if value is not None and (isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1):
                raise ValueError("invalid preview probability")
        if status in {PredictionStatus.BLOCKED, PredictionStatus.UNAVAILABLE}:
            if self.model_probability is not None or not self.block_reason:
                raise ValueError("blocked/unavailable rows require a reason and null probability")
        elif self.model_probability is None or not self.model_id or not self.model_version:
            raise ValueError("usable probability requires model identity")
        if self.market_type == "batter_home_runs":
            if (status == PredictionStatus.QUALIFIED_RESEARCH
                    or self.probability_market_independence != "NO"
                    or self.limitation_status != HR_LIMITATION):
                raise ValueError("HR must disclose legacy market contamination")
        elif self.limitation_status != HITS_LIMITATION or status == PredictionStatus.LEGACY_RESEARCH:
            raise ValueError("Hits must disclose the naive uncalibrated baseline")
        if status == PredictionStatus.QUALIFIED_RESEARCH:
            if (self.probability_market_independence != "YES" or not self.player_id
                    or not self.canonical_event_id or self.identity_status != "resolved"
                    or self.event_status != "resolved" or self.lineup_status != "statsapi_batting_order_present"
                    or self.market_implied_probability is None or self.projected_at_bats is None):
                raise ValueError("qualified Hits requires the complete evidence chain")
        if self.row_kind not in {"PLAYER", "SOURCE_STATUS"}:
            raise ValueError("invalid row kind")
        if self.row_kind == "SOURCE_STATUS" and (self.player_name or status != PredictionStatus.UNAVAILABLE):
            raise ValueError("source status must not invent a player")
        if not isinstance(self.source_refs, tuple) or any(not isinstance(ref, str) for ref in self.source_refs):
            raise ValueError("source refs must be immutable text")
        if status == PredictionStatus.QUALIFIED_RESEARCH:
            self._validate_sovereign_hits()

    def _validate_sovereign_hits(self) -> None:
        """Recheck persisted v2 lineage; never infer it from code or file paths.

        These are the existing ledger content identities and AB v2 provenance,
        not an assertion that external fact files were reacquired or audited.
        """
        if (self.preview_schema_version != PREVIEW_SCHEMA_VERSION
                or self.season_source != CANONICAL_HITS_SEASON_SOURCE
                or self.ab_projection_version != AB_PROJECTION_V2_VERSION):
            raise ValueError("qualified Hits requires v2 ledger provenance and AB v2")
        _id(self.player_id, "player_id")
        _id(self.canonical_event_id, "canonical_event_id")
        _hash(self.season_aggregate_hash, "season_aggregate_hash")
        if (type(self.season_at_bats) is not int or self.season_at_bats <= 0
                or type(self.season_hits) is not int or not 0 <= self.season_hits <= self.season_at_bats
                or type(self.distinct_batting_games) is not int or self.distinct_batting_games <= 0
                or type(self.projected_at_bats) not in {int, float}
                or not math.isfinite(self.projected_at_bats) or self.projected_at_bats <= 0):
            raise ValueError("qualified Hits requires valid season counts and projected AB")

        def reference(prefix: str) -> str:
            values = {ref[len(prefix):] for ref in self.source_refs if ref.startswith(prefix)}
            if len(values) != 1:
                raise ValueError(f"qualified Hits requires one unambiguous {prefix} reference")
            return values.pop()

        if reference("cv-ledger-season:sha256:") != self.season_aggregate_hash:
            raise ValueError("season aggregate reference differs from row identity")
        _hash(reference("cv-ledger-coverage:sha256:"), "ledger coverage reference")
        projection = json.loads(reference("cv_ab_projection_provenance="), object_pairs_hook=_unique_object)
        expected = {
            "model_version": self.ab_projection_version, "projection_method": AB_PROJECTION_V2_METHOD,
            "season_source": self.season_source, "season_aggregate_hash": self.season_aggregate_hash,
            "season": date.fromisoformat(self.operating_date).year, "season_at_bats": self.season_at_bats,
            "distinct_batting_games": self.distinct_batting_games, "game_id": self.canonical_event_id,
            "player_id": self.player_id, "lineup_status": self.lineup_status,
        }
        if (not isinstance(projection, dict)
                or any(type(projection.get(key)) is not type(value) or projection[key] != value
                       for key, value in expected.items())
                or type(projection.get("batting_order_position")) is not int
                or not 1 <= projection["batting_order_position"] <= 9):
            raise ValueError("AB v2 provenance differs from qualified Hits identity/counts/lineup")
        features = json.loads(reference("hits_feature_provenance="), object_pairs_hook=_unique_object)
        expected_features = {
            "game_id": self.canonical_event_id, "player_id": self.player_id,
            "season_source": self.season_source, "season_games_played": None,
            "season": str(expected["season"]), "projection_model_version": self.ab_projection_version,
            "projection_method": AB_PROJECTION_V2_METHOD, "projection_source": "courtvision",
            "batting_order_position": projection["batting_order_position"],
        }
        if (not isinstance(features, dict)
                or any(key not in features or type(features[key]) is not type(value) or features[key] != value
                       for key, value in expected_features.items())):
            raise ValueError("feature provenance conflicts with sovereign Hits lineage")
        for name in ("season_hits", "season_at_bats", "projected_at_bats"):
            if reference(f"features.{name}=") != str(getattr(self, name)):
                raise ValueError(f"persisted {name} differs from feature provenance")
        try:
            expected_ab = self.season_at_bats / self.distinct_batting_games
        except OverflowError as exc:
            raise ValueError("invalid AB v2 opportunity") from exc
        if self.projected_at_bats != expected_ab:
            raise ValueError("projected AB differs from AB v2 season opportunity")

    def to_dict(self) -> dict:
        return asdict(self)


def unavailable_row(day: str, market: str, reason: str, *, refs: tuple[str, ...] = ()) -> MLBResearchPreviewRow:
    hits = market == "batter_hits"
    return MLBResearchPreviewRow(
        operating_date=day, market_type=market, market_display="1+ Hits" if hits else "1+ Home Runs",
        prediction_status="UNAVAILABLE", block_reason=reason, row_kind="SOURCE_STATUS",
        limitation_status=HITS_LIMITATION if hits else HR_LIMITATION,
        probability_market_independence="NOT_TESTED" if hits else "NO", source_refs=refs,
        season_source=CANONICAL_HITS_SEASON_SOURCE if hits else None,
    )


def hits_source_row(source: MLBPlayerPropSourceRecord) -> MLBResearchPreviewRow:
    quote = source.to_normalized_quote()
    return MLBResearchPreviewRow(
        operating_date=quote.event_date.isoformat(), market_type="batter_hits", market_display="1+ Hits",
        provider_event_id=source.provider_event_id, commence_time_utc=source.commence_time.isoformat(),
        player_name=source.participant_name, home_team=source.home_team, away_team=source.away_team,
        sportsbook=source.bookmaker_name, line=source.line, american_odds=quote.american_odds,
        market_implied_probability=quote.implied_probability,
        market_timestamp_utc=source.market_updated_at.isoformat(), source_refs=source.source_refs,
        prediction_status="BLOCKED", block_reason="EVENT_IDENTITY_UNRESOLVED",
        limitation_status=HITS_LIMITATION, model_id=MODEL_ID, model_version=MODEL_VERSION,
        season_source=CANONICAL_HITS_SEASON_SOURCE,
    )


def hits_failure_reason(detail: str, default: str) -> str:
    if "split" in detail and ("exactly one" in detail or "unambiguous" in detail):
        return "AMBIGUOUS_SEASON_SPLITS"
    if any(text in detail for text in ("pregame", "before both game starts", "before game start", "cutoff", "game start")):
        return "PREGAME_CUTOFF_FAILED"
    if "batting-order presence" in detail:
        return "LINEUP_UNAVAILABLE"
    return default


def preview_hits_evidence(
    source: MLBPlayerPropSourceRecord, acquired: AcquiredBatterHitsEvidence, *, generated_at: datetime,
) -> MLBResearchPreviewRow:
    """Canonical preview requires ledger season evidence; never falls back to v1."""
    base = hits_source_row(source)
    player = acquired.player_binding
    event = player.event_binding
    side = player.team_side
    base = replace(
        base, canonical_event_id=event.mlbam_game_id, player_id=player.mlbam_player_id,
        identity_status=player.participant_identity.identity_status.value, event_status=event.identity_status.value,
        lineup_status=acquired.lineup_evidence.lineup_status, evidence_cutoff=acquired.evidence_cutoff.isoformat(),
        team=source.home_team if side == "home" else source.away_team if side == "away" else None,
        opponent=source.away_team if side == "home" else source.home_team if side == "away" else None,
    )
    if type(acquired) is not SovereignBatterHitsEvidence:
        return replace(base, block_reason="COURTVISION_LEDGER_MISSING",
                       block_detail="CourtVision season ledger is required; provider season evidence is diagnostic only")
    season = acquired.season_evidence
    base = replace(base, season_hits=season.hits, season_at_bats=season.at_bats,
        season_aggregate_hash=season.season_aggregate_hash, distinct_batting_games=season.distinct_batting_games,
        ab_projection_version=AB_PROJECTION_V2_VERSION,
        source_refs=tuple(dict.fromkeys((*source.source_refs, *event.source_refs, *player.source_refs,
                                      *acquired.lineup_evidence.source_refs, *acquired.season_evidence.source_refs))),
    )
    stage = "AB_PROJECTION_UNAVAILABLE"
    try:
        features = assemble_sovereign_batter_hits_features(acquired, generated_at=generated_at)
        stage = "CANDIDATE_EVIDENCE_INVALID"
        probability = compute_batter_hits_probability(features, generated_at=generated_at)
        candidate = assemble_batter_hits_candidate(
            candidate_id=f"preview:{source.provider_event_id}:{player.mlbam_player_id}:{source.bookmaker_key}",
            quote=source.to_normalized_quote(), source_evidence=batter_hits_source_evidence_from_market_record(source),
            features=features, probability=probability, participant_identity=player.participant_identity,
            event_identity=event.event_identity,
            provenance=CandidateProvenance(source="mlb_research_preview", source_refs=base.source_refs),
        )
    except HitsLedgerError as exc:
        return replace(base, block_reason=exc.state, block_detail=str(exc))
    except ValueError as exc:
        return replace(base, block_reason=hits_failure_reason(str(exc), stage), block_detail=str(exc))
    return replace(
        base, prediction_status="QUALIFIED_RESEARCH", block_reason=None,
        model_probability=candidate.probability.model_probability, model_id=candidate.probability.model_id,
        model_version=candidate.probability.model_version, probability_market_independence="YES",
        projected_at_bats=features.projected_at_bats, evidence_cutoff=features.evidence_cutoff.isoformat(),
        prediction_timestamp_utc=generated_at.isoformat(), source_refs=candidate.provenance.source_refs,
    )


def preview_hr_prediction(prediction: Mapping[str, str], *, day: str, source_ref: str) -> MLBResearchPreviewRow:
    """Translate the two existing HR CSV contracts through the current HR adapter.

    A legacy source cutoff is not established by its snapshot timestamp. The
    existing adapter is used with the prediction-time upper bound, explicitly
    marked here; the preview leaves the actual evidence cutoff unavailable.
    """
    from courtvision.sports.mlb.market_data import MLBMarketVariant, MLBPropSide

    row = dict(prediction)
    base = replace(unavailable_row(day, "batter_home_runs", "HR_PREDICTION_INVALID", refs=(source_ref,)),
                   row_kind="PLAYER", player_name=row.get("player_name") or None,
                   provider_event_id=row.get("event_id") or None,
                   model_id=row.get("model_id") or None, model_version=row.get("model_version") or None)
    try:
        schema = row.get("prediction_schema_version")
        for key in ("eligible_for_betting", "eligible_for_official_pick", "kelly_eligible"):
            if row.get(key) and row[key].lower() != "false":
                raise ValueError("HR source safety flags invalid")
        if row.get("approval_status") and row["approval_status"] != "not_approved":
            raise ValueError("HR source approval status invalid")
        if schema == "mlb-hr-prospective-prediction-v1":
            if (row.get("research_only") != "true" or row.get("approval_status") != "not_approved"
                    or row.get("eligible_for_betting") != "false"
                    or row.get("eligible_for_official_pick") != "false"):
                raise ValueError("HR source safety flags invalid")
            row.update(
                prediction_schema_version="mlb-hr-research-predictions-v2",
                research_label="RESEARCH ONLY - NOT A VALIDATED BETTING PICK",
                game_date=row["operating_date"], commence_time=row["commence_time_utc"],
                american_odds=row["prediction_time_price"], snapshot_time=row["selected_snapshot_timestamp_utc"],
                prediction_timestamp=row["prediction_timestamp_utc"],
            )
        elif schema != "mlb-hr-research-predictions-v2":
            raise ValueError("unsupported HR prediction schema")
        if row["game_date"] != day:
            raise ValueError("HR source operating date mismatch")
        source = MLBPlayerPropSourceRecord(
            provider="the_odds_api", provider_sport_key="baseball_mlb", provider_event_id=row["event_id"],
            home_team=row["home_team"], away_team=row["away_team"], commence_time=timestamp(row["commence_time"]),
            bookmaker_key=row["sportsbook"], bookmaker_name=row["sportsbook"],
            provider_market_key=row["market_key"], canonical_market_type="batter_home_runs",
            market_variant=MLBMarketVariant.ALTERNATE if row["market_key"].endswith("alternate") else MLBMarketVariant.MAIN,
            participant_name=row["player_name"], side=MLBPropSide(row["side"].upper()), line=float(row["point"]),
            american_odds=row["american_odds"], market_updated_at=timestamp(row["snapshot_time"]),
            collected_at=timestamp(row["prediction_timestamp"]), source_refs=(source_ref,),
        )
        candidate = adapt_mlb_hr_prediction(row, source.to_normalized_quote(),
                                            evidence_cutoff=timestamp(row["prediction_timestamp"]))
        quote = candidate.quote
        return replace(
            base, prediction_status="LEGACY_RESEARCH", block_reason=None,
            model_probability=candidate.probability.model_probability,
            commence_time_utc=source.commence_time.isoformat(), home_team=source.home_team, away_team=source.away_team,
            team=row.get("team") or None, opponent=row.get("opponent") or None,
            sportsbook=source.bookmaker_name, line=source.line, american_odds=quote.american_odds,
            market_implied_probability=quote.implied_probability, market_timestamp_utc=source.market_updated_at.isoformat(),
            prediction_timestamp_utc=row["prediction_timestamp"], identity_status="name_only_research",
            source_refs=(source_ref, *candidate.provenance.source_refs,
                         "evidence_cutoff=unavailable; prediction_timestamp is only an upper bound"),
        )
    except (ValueError, KeyError, TypeError) as exc:
        return replace(base, block_detail=str(exc))


def sort_preview_rows(rows: list[MLBResearchPreviewRow]) -> list[MLBResearchPreviewRow]:
    return sorted(rows, key=lambda row: (
        row.operating_date, row.commence_time_utc or "", row.canonical_event_id or row.provider_event_id or "",
        (row.player_name or "").casefold(), row.player_id or "", row.market_type, row.sportsbook or "",
        row.line if row.line is not None else -1, row.prediction_status, row.source_refs,
        json.dumps(row.to_dict(), sort_keys=True),
    ))


def preview_availability(rows: list[MLBResearchPreviewRow]) -> dict:
    """Separate source availability from real player rows without changing either."""
    markets = {}
    for market, label in (("batter_hits", "Hits"), ("batter_home_runs", "Home Runs")):
        market_rows = [row for row in rows if row.market_type == market]
        players = [row for row in market_rows if row.row_kind == "PLAYER"]
        qualified = sum(row.prediction_status == "QUALIFIED_RESEARCH" for row in players)
        legacy = sum(row.prediction_status == "LEGACY_RESEARCH" for row in players)
        blocked = sum(row.prediction_status == "BLOCKED" for row in players)
        unavailable = sum(row.prediction_status == "UNAVAILABLE" for row in players)
        reasons = sorted({row.block_reason for row in market_rows if row.row_kind == "SOURCE_STATUS"})
        loaded = []
        if qualified:
            loaded.append(f"{qualified} QUALIFIED RESEARCH ROWS LOADED")
        if legacy:
            loaded.append(f"{legacy} LEGACY RESEARCH ROWS LOADED")
        if blocked:
            loaded.append(f"{blocked} BLOCKED PLAYER ROWS")
        if unavailable:
            loaded.append(f"{unavailable} UNAVAILABLE PLAYER ROWS")
        if reasons or not market_rows:
            loaded.append("SOURCE UNAVAILABLE")
        if market == "batter_hits" and any(r.block_reason == "COURTVISION_LEDGER_MISSING" for r in market_rows):
            loaded.append("SEASON_LEDGER_NOT_POPULATED")
        markets[market] = {
            "label": label, "status": " · ".join(loaded), "prediction_rows": len(players),
            "usable_rows": qualified + legacy, "blocked_rows": blocked,
            "unavailable_player_rows": unavailable, "source_reasons": reasons,
        }
    usable = sum(market["usable_rows"] for market in markets.values())
    incomplete = any(not market["usable_rows"] or market["blocked_rows"]
                     or market["unavailable_player_rows"] or market["source_reasons"] for market in markets.values())
    return {
        "availability_schema_version": "mlb-preview-availability-v1",
        "market_status": markets,
        "prediction_rows": sum(market["prediction_rows"] for market in markets.values()),
        "usable_prediction_rows": usable,
        "status": "MLB_PREVIEW_SOURCE_DATA_UNAVAILABLE" if not usable else
                  "MLB_PREVIEW_PARTIAL_AVAILABILITY" if incomplete else "MLB_PREVIEW_RESEARCH_ONLY",
    }


def preview_summary(rows: list[MLBResearchPreviewRow], day: str) -> dict:
    if any(row.operating_date != day for row in rows):
        raise ValueError("cannot combine different operating dates")
    hits = [row for row in rows if row.market_type == "batter_hits"]
    hr = [row for row in rows if row.market_type == "batter_home_runs"]
    players = [row for row in rows if row.row_kind == "PLAYER"]
    return {
        "preview_schema_version": PREVIEW_SCHEMA_VERSION, "operating_date": day,
        "games_seen": len({(r.away_team, r.home_team, r.commence_time_utc) if r.home_team and r.away_team
                           else (r.canonical_event_id or r.provider_event_id,) for r in players
                           if r.canonical_event_id or r.provider_event_id}),
        "players_seen": len({r.player_name.casefold() for r in players if r.player_name}),
        "hits_rows": len(hits), "hits_qualified": sum(r.prediction_status == "QUALIFIED_RESEARCH" for r in hits),
        "hits_blocked": sum(r.prediction_status == "BLOCKED" for r in hits),
        "hits_unavailable": sum(r.prediction_status == "UNAVAILABLE" for r in hits),
        "hr_rows": len(hr), "hr_market_contaminated": sum(r.prediction_status == "LEGACY_RESEARCH" for r in hr),
        "hr_unavailable": sum(r.prediction_status == "UNAVAILABLE" for r in hr),
        "total_rows": len(rows), "source_status_rows": sum(r.row_kind == "SOURCE_STATUS" for r in rows),
        "research_only": True, "eligible_for_betting": False, "kelly_eligible": False,
        "approval_status": "not_approved", "hr_warning": HR_WARNING,
        **preview_availability(rows),
        "missing_sources": sorted({f"{r.market_type}: {r.block_reason}" for r in rows if r.prediction_status == "UNAVAILABLE"}),
    }
