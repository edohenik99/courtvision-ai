"""Qualified, auditable Hits season evidence from the CORE-01 factual ledger.

Coverage is an explicit caller attestation, never inferred from directory
contents. Its expected game IDs/hashes make withheld files detectable. No
manifest builder scans available facts and declares them a complete season.
Same-day games are conservatively excluded: CORE-01 has no game-end clock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import hashlib
import json
from typing import Mapping

from courtvision.sports.mlb.fact_ledger import MLBFactStore, _unique_object
from courtvision.sports.mlb.game_facts import (
    FACT_SCHEMA_VERSION, MLBBatterGameFact, _aware, _hash, _id, _refs, canonical_json,
)
from courtvision.sports.mlb.hits_features import BatterSeasonHittingEvidence
from courtvision.sports.mlb.season_facts import (
    MLBBatterSeasonFacts, bridge_batter_season_hitting_evidence,
)

CANONICAL_HITS_SEASON_SOURCE = "COURTVISION_GAME_FACT_LEDGER"
LEGACY_HITS_SEASON_SOURCE = "MLB_STATSAPI_SEASON_SPLITS"
LEDGER_SEASON_SCHEMA = "cv_hits_ledger_season_v1"
LEDGER_COVERAGE_SCHEMA = "cv_hits_ledger_coverage_v1"


class HitsLedgerError(ValueError):
    def __init__(self, state: str, detail: str):
        self.state = state
        super().__init__(detail)


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class BatterFactReference:
    mlbam_game_id: str
    factual_record_hash: str

    def __post_init__(self) -> None:
        _id(self.mlbam_game_id, "mlbam_game_id")
        _hash(self.factual_record_hash, "factual_record_hash")


@dataclass(frozen=True, slots=True, kw_only=True)
class BatterLedgerCoverage:
    """Declared full season-to-prior-date inventory for one player and target.

    source_refs must identify the independent coverage evidence supplied by the
    caller. The code verifies consistency/completeness against this inventory;
    it cannot authenticate the caller's assertion about historical coverage.
    """
    season: int
    mlbam_player_id: str
    target_game_id: str
    target_game_date: date
    coverage_through: date
    aggregation_cutoff: datetime
    observed_at: datetime
    expected_records: tuple[BatterFactReference, ...]
    complete: bool
    source_refs: tuple[str, ...]
    schema_version: str = field(default=LEDGER_COVERAGE_SCHEMA, init=False)

    def __post_init__(self) -> None:
        if type(self.season) is not int or not 1 <= self.season <= 9999:
            raise ValueError("coverage requires an explicit season")
        for label in ("mlbam_player_id", "target_game_id"):
            _id(getattr(self, label), label)
        if type(self.target_game_date) is not date or type(self.coverage_through) is not date:
            raise ValueError("coverage requires explicit dates")
        if self.target_game_date.year != self.season:
            raise ValueError("coverage season differs from target game date")
        if type(self.complete) is not bool:
            raise ValueError("coverage completeness must be an explicit bool")
        object.__setattr__(self, "aggregation_cutoff", _aware(self.aggregation_cutoff))
        object.__setattr__(self, "observed_at", _aware(self.observed_at))
        if self.observed_at > self.aggregation_cutoff:
            raise ValueError("coverage observation is after aggregation cutoff")
        object.__setattr__(self, "source_refs", _refs(self.source_refs))
        if not isinstance(self.expected_records, tuple):
            raise ValueError("coverage references must be an immutable tuple")
        records = {}
        for ref in self.expected_records:
            if type(ref) is not BatterFactReference:
                raise TypeError("coverage requires BatterFactReference")
            previous = records.get(ref.mlbam_game_id)
            if previous is not None and previous != ref:
                raise HitsLedgerError("COURTVISION_LEDGER_CONFLICT", "conflicting coverage game hashes")
            records[ref.mlbam_game_id] = ref
        object.__setattr__(self, "expected_records", tuple(records[key] for key in sorted(records)))

    def to_payload(self) -> dict:
        return {
            "schema_version": self.schema_version, "season": self.season,
            "mlbam_player_id": self.mlbam_player_id, "target_game_id": self.target_game_id,
            "target_game_date": self.target_game_date.isoformat(),
            "coverage_through": self.coverage_through.isoformat(),
            "aggregation_cutoff": self.aggregation_cutoff.isoformat(),
            "observed_at": self.observed_at.isoformat(), "complete": self.complete,
            "source_refs": list(self.source_refs),
            "expected_records": [{"mlbam_game_id": ref.mlbam_game_id,
                                  "factual_record_hash": ref.factual_record_hash}
                                 for ref in self.expected_records],
        }

    @property
    def manifest_hash(self) -> str:
        return _digest(self.to_payload())

    def to_envelope(self) -> dict:
        return {"coverage": self.to_payload(), "manifest_hash": self.manifest_hash}


def coverage_from_payload(envelope: Mapping) -> BatterLedgerCoverage:
    """Decode a supplied manifest with strict schema and normalized hash binding."""
    try:
        if set(envelope) != {"coverage", "manifest_hash"}:
            raise ValueError("invalid coverage envelope")
        raw = dict(envelope["coverage"])
        version = raw.pop("schema_version")
        if version != LEDGER_COVERAGE_SCHEMA:
            raise ValueError("unsupported coverage schema")
        for label in ("target_game_date", "coverage_through"):
            raw[label] = date.fromisoformat(raw[label])
        for label in ("aggregation_cutoff", "observed_at"):
            raw[label] = datetime.fromisoformat(raw[label])
        if not isinstance(raw["expected_records"], list) or not isinstance(raw["source_refs"], list):
            raise ValueError("coverage records and refs must be JSON arrays")
        raw["expected_records"] = tuple(BatterFactReference(**ref) for ref in raw["expected_records"])
        raw["source_refs"] = tuple(raw["source_refs"])
        coverage = BatterLedgerCoverage(**raw)
        if coverage.manifest_hash != envelope["manifest_hash"]:
            raise ValueError("coverage manifest hash mismatch")
        return coverage
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise HitsLedgerError("COURTVISION_LEDGER_CONFLICT", "invalid coverage manifest") from exc


def coverage_from_bytes(raw: bytes) -> BatterLedgerCoverage:
    try:
        return coverage_from_payload(json.loads(raw, object_pairs_hook=_unique_object))
    except (ValueError, TypeError) as exc:
        raise HitsLedgerError("COURTVISION_LEDGER_CONFLICT", "invalid coverage manifest JSON") from exc


def _qualified_summary(coverage: BatterLedgerCoverage, facts: tuple[MLBBatterGameFact, ...]) -> MLBBatterSeasonFacts:
    if type(coverage) is not BatterLedgerCoverage:
        raise HitsLedgerError("COURTVISION_LEDGER_MISSING", "CourtVision season ledger coverage is not populated")
    if (not coverage.complete or coverage.coverage_through != coverage.target_game_date - timedelta(days=1)
            or not coverage.expected_records or not facts):
        raise HitsLedgerError("COURTVISION_LEDGER_INCOMPLETE", "complete prior-date season inventory and positive games are required")
    try:
        summary = MLBBatterSeasonFacts(season=coverage.season, mlbam_player_id=coverage.mlbam_player_id, facts=facts)
    except (ValueError, TypeError) as exc:
        raise HitsLedgerError("COURTVISION_LEDGER_CONFLICT", str(exc)) from exc
    actual = {fact.mlbam_game_id: fact.factual_record_hash for fact in summary.facts}
    expected = {ref.mlbam_game_id: ref.factual_record_hash for ref in coverage.expected_records}
    if set(actual) != set(expected):
        raise HitsLedgerError("COURTVISION_LEDGER_INCOMPLETE", "facts differ from required coverage game inventory")
    if actual != expected:
        raise HitsLedgerError("COURTVISION_LEDGER_CONFLICT", "contributing fact differs from coverage hash")
    if any(f.game_date >= coverage.target_game_date or f.mlbam_game_id == coverage.target_game_id
           or f.observed_at > coverage.aggregation_cutoff or f.observed_at > coverage.observed_at
           for f in summary.facts):
        raise HitsLedgerError("COURTVISION_LEDGER_INCOMPLETE", "ledger facts must precede target date, coverage observation and aggregation cutoff")
    if not summary.qualified_ab_h or summary.at_bats <= 0:
        raise HitsLedgerError("COURTVISION_LEDGER_INCOMPLETE", "complete positive season AB and valid H are required")
    return summary


def _aggregate_manifest(coverage: BatterLedgerCoverage, summary: MLBBatterSeasonFacts) -> dict:
    return {
        "schema_version": LEDGER_SEASON_SCHEMA, "source_schema_version": FACT_SCHEMA_VERSION,
        "source": CANONICAL_HITS_SEASON_SOURCE, "coverage": coverage.to_envelope(),
        "season": summary.season, "mlbam_player_id": summary.mlbam_player_id,
        "aggregation_cutoff": coverage.aggregation_cutoff.isoformat(),
        "distinct_batting_games": summary.distinct_batting_games,
        "at_bats": summary.at_bats, "hits": summary.hits,
        "records": [{"logical_identity": list(f.logical_identity),
                     "factual_record_hash": f.factual_record_hash,
                     "source_refs": list(f.source_refs)} for f in summary.facts],
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class LedgerBatterSeasonEvidence(BatterSeasonHittingEvidence):
    coverage: BatterLedgerCoverage
    summary: MLBBatterSeasonFacts

    def __post_init__(self) -> None:
        BatterSeasonHittingEvidence.__post_init__(self)
        summary = _qualified_summary(self.coverage, self.summary.facts)
        if (self.season != summary.season or self.mlbam_player_id != summary.mlbam_player_id
                or self.at_bats != summary.at_bats or self.hits != summary.hits
                or self.games_played is not None or self.source != CANONICAL_HITS_SEASON_SOURCE
                or self.observed_at != max(summary.observed_at, self.coverage.observed_at)
                or self.evidence_cutoff != self.coverage.aggregation_cutoff):
            raise HitsLedgerError("COURTVISION_LEDGER_CONFLICT", "season evidence differs from canonical facts/coverage")
        object.__setattr__(self, "summary", summary)
        if self.source_refs != self.compact_source_refs:
            raise HitsLedgerError("COURTVISION_LEDGER_CONFLICT", "season provenance differs from aggregate identity")

    @property
    def distinct_batting_games(self) -> int:
        return self.summary.distinct_batting_games

    @property
    def qualification_state(self) -> str:
        return "COURTVISION_LEDGER_QUALIFIED"

    def aggregate_manifest(self) -> dict:
        """Auditable underlying evidence; UI rows use only its compact hash ref."""
        return _aggregate_manifest(self.coverage, self.summary)

    @property
    def season_aggregate_hash(self) -> str:
        return _digest(self.aggregate_manifest())

    @property
    def compact_source_refs(self) -> tuple[str, ...]:
        return (f"cv-ledger-coverage:sha256:{self.coverage.manifest_hash}",
                f"cv-ledger-season:sha256:{self.season_aggregate_hash}")


def qualify_ledger_season(
    coverage: BatterLedgerCoverage | None, facts: tuple[MLBBatterGameFact, ...], *, player_name: str,
) -> LedgerBatterSeasonEvidence:
    summary = _qualified_summary(coverage, facts)
    # Reuse CORE-01's bridge for the existing AB/H evidence contract, then bind
    # the explicit sovereign coverage/target and replace its long refs by hashes.
    base = bridge_batter_season_hitting_evidence(summary, player_name=player_name,
                                               evidence_cutoff=coverage.aggregation_cutoff)
    manifest = _aggregate_manifest(coverage, summary)
    return LedgerBatterSeasonEvidence(
        season=base.season, mlbam_player_id=base.mlbam_player_id, player_name=base.player_name,
        hits=base.hits, at_bats=base.at_bats, observed_at=max(base.observed_at, coverage.observed_at),
        evidence_cutoff=base.evidence_cutoff, source=CANONICAL_HITS_SEASON_SOURCE,
        source_refs=(f"cv-ledger-coverage:sha256:{coverage.manifest_hash}", f"cv-ledger-season:sha256:{_digest(manifest)}"),
        games_played=None, coverage=coverage, summary=summary,
    )


def load_ledger_season(
    store: MLBFactStore, coverage: BatterLedgerCoverage | None, *, player_name: str,
) -> LedgerBatterSeasonEvidence:
    """Read exactly the attested inventory through CORE-01 integrity checks."""
    if coverage is None:
        raise HitsLedgerError("COURTVISION_LEDGER_MISSING", "CourtVision season ledger coverage is not populated")
    try:
        facts = tuple(store.read("BATTER", ref.mlbam_game_id, coverage.mlbam_player_id)
                      for ref in coverage.expected_records)
    except FileNotFoundError as exc:
        raise HitsLedgerError("COURTVISION_LEDGER_INCOMPLETE", "required immutable batter-game record is missing") from exc
    except (OSError, ValueError, TypeError) as exc:
        raise HitsLedgerError("COURTVISION_LEDGER_CONFLICT", "immutable batter-game record failed validation") from exc
    return qualify_ledger_season(coverage, facts, player_name=player_name)
