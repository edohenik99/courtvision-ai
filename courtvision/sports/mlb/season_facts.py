"""Pure distinct-game aggregation over supplied canonical facts, never team splits.

Summaries describe only their supplied set; they do not attest full-season
coverage or regular/postseason selection. Season here is the official game-date
year. Missing counts poison the corresponding total, not silently become zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from courtvision.sports.mlb.game_facts import (
    MLBBatterGameFact, MLBPitcherGameFact, _aware, _id,
    canonical_json, fact_from_payload, fact_payload,
)
from courtvision.sports.mlb.hits_features import BatterSeasonHittingEvidence


@dataclass(frozen=True, slots=True, kw_only=True)
class _SeasonFacts:
    season: int
    mlbam_player_id: str
    facts: tuple[MLBBatterGameFact | MLBPitcherGameFact, ...]
    research_only: bool = field(default=True, init=False)
    eligible_for_betting: bool = field(default=False, init=False)
    kelly_eligible: bool = field(default=False, init=False)
    eligible_for_official_pick: bool = field(default=False, init=False)
    approval_status: str = field(default="not_approved", init=False)
    fact_type: ClassVar[type]

    def __post_init__(self) -> None:
        if type(self.season) is not int or not 1 <= self.season <= 9999:
            raise ValueError("season must be an explicit calendar year")
        _id(self.mlbam_player_id, "mlbam_player_id")
        if not isinstance(self.facts, tuple) or not self.facts:
            raise ValueError("season aggregation requires a non-empty immutable fact tuple")
        unique = {}
        for fact in self.facts:
            if type(fact) is not self.fact_type:
                raise TypeError("season facts must have the expected player role")
            fact_from_payload(fact_payload(fact))
            if fact.mlbam_player_id != self.mlbam_player_id:
                raise ValueError("wrong player ID in season facts")
            if fact.season != self.season:
                raise ValueError("wrong season in supplied facts")
            previous = unique.get(fact.mlbam_game_id)
            if previous is not None and previous != fact:
                raise ValueError("conflicting duplicate player-game fact")
            unique[fact.mlbam_game_id] = fact
        object.__setattr__(self, "facts", tuple(unique[key] for key in sorted(unique)))

    def _total(self, field: str) -> int | None:
        values = tuple(getattr(fact, field) for fact in self.facts)
        return None if any(value is None for value in values) else sum(values)

    @property
    def game_ids(self) -> tuple[str, ...]:
        return tuple(fact.mlbam_game_id for fact in self.facts)

    @property
    def observed_at(self) -> datetime:
        return max(fact.observed_at for fact in self.facts)

    @property
    def source_refs(self) -> tuple[str, ...]:
        return tuple(sorted({ref for fact in self.facts for ref in fact.source_refs}))

    @property
    def factual_record_hashes(self) -> tuple[str, ...]:
        return tuple(fact.factual_record_hash for fact in self.facts)


@dataclass(frozen=True, slots=True, kw_only=True)
class MLBBatterSeasonFacts(_SeasonFacts):
    fact_type: ClassVar[type] = MLBBatterGameFact

    @property
    def distinct_batting_games(self) -> int:
        return len(self.facts)

    @property
    def qualified_ab_h(self) -> bool:
        return self.at_bats is not None and self.hits is not None

    @property
    def at_bats(self) -> int | None:
        return self._total("at_bats")

    @property
    def hits(self) -> int | None:
        return self._total("hits")

    @property
    def plate_appearances(self) -> int | None:
        return self._total("plate_appearances")

    @property
    def singles(self) -> int | None:
        return self._total("singles")

    @property
    def doubles(self) -> int | None:
        return self._total("doubles")

    @property
    def triples(self) -> int | None:
        return self._total("triples")

    @property
    def home_runs(self) -> int | None:
        return self._total("home_runs")

    @property
    def walks(self) -> int | None:
        return self._total("walks")

    @property
    def strikeouts(self) -> int | None:
        return self._total("strikeouts")

    @property
    def runs(self) -> int | None:
        return self._total("runs")

    @property
    def rbi(self) -> int | None:
        return self._total("rbi")

    @property
    def total_bases(self) -> int | None:
        return self._total("total_bases")


@dataclass(frozen=True, slots=True, kw_only=True)
class MLBPitcherSeasonFacts(_SeasonFacts):
    fact_type: ClassVar[type] = MLBPitcherGameFact

    @property
    def distinct_pitching_games(self) -> int:
        return len(self.facts)

    @property
    def outs_recorded(self) -> int | None:
        return self._total("outs_recorded")

    @property
    def strikeouts(self) -> int | None:
        return self._total("strikeouts")

    @property
    def walks(self) -> int | None:
        return self._total("walks")

    @property
    def hits_allowed(self) -> int | None:
        return self._total("hits_allowed")

    @property
    def batters_faced(self) -> int | None:
        return self._total("batters_faced")

    @property
    def home_runs_allowed(self) -> int | None:
        return self._total("home_runs_allowed")

    @property
    def pitches(self) -> int | None:
        return self._total("pitches")

    @property
    def strikes(self) -> int | None:
        return self._total("strikes")


def bridge_batter_season_hitting_evidence(
    summary: MLBBatterSeasonFacts, *, player_name: str, evidence_cutoff: datetime,
) -> BatterSeasonHittingEvidence:
    """AB/H compatibility only; do not populate the provider games_played field.

    The distinct opportunity metric and complete per-record lineage travel in
    provenance. The current live AB projection still requires games_played and
    is deliberately not cut over by this adapter. A future explicit policy must
    select coverage/season type and adopt distinct_batting_games as its input.
    """
    if type(summary) is not MLBBatterSeasonFacts:
        raise TypeError("summary must be MLBBatterSeasonFacts")
    # Reconstruct to validate even an object tampered with outside frozen APIs.
    summary = MLBBatterSeasonFacts(season=summary.season,
        mlbam_player_id=summary.mlbam_player_id, facts=summary.facts)
    if not summary.qualified_ab_h:
        raise ValueError("missing required AB/H prevents qualified season evidence")
    cutoff = _aware(evidence_cutoff)
    if cutoff < summary.observed_at:
        raise ValueError("evidence_cutoff precedes supplied final facts")
    provenance = {
        "source": "courtvision_game_ledger", "season": summary.season,
        "mlbam_player_id": summary.mlbam_player_id,
        "opportunity_metric": "distinct_batting_games",
        "distinct_batting_games": summary.distinct_batting_games,
        "coverage": "supplied_facts_only", "provider_gamesPlayed": None,
        "records": [{"game_id": f.mlbam_game_id, "game_date": f.game_date.isoformat(),
                     "observed_at": f.observed_at.isoformat(), "source_hash": f.source_hash,
                     "game_fact_hash": f.game_fact_hash,
                     "factual_record_hash": f.factual_record_hash} for f in summary.facts],
    }
    return BatterSeasonHittingEvidence(
        season=summary.season, mlbam_player_id=summary.mlbam_player_id,
        player_name=player_name, hits=summary.hits, at_bats=summary.at_bats,
        observed_at=summary.observed_at, evidence_cutoff=cutoff,
        source="courtvision_game_ledger", games_played=None,
        source_refs=(*summary.source_refs,
                     "mlb_ledger_provenance=" + canonical_json(provenance).decode("utf-8")),
    )
