"""Offline NBA player-points end-to-end integration rehearsal.

This module composes the research-only NBA player-points contracts with a
realistic in-memory fixture bundle. It performs no provider I/O, reads no
credentials, writes no files unless a caller supplies a temp preview directory,
and creates no runner, scheduler, ledger, official picks, or staking advice.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Any, Final

from courtvision.sports.nba.player_minutes_research import (
    NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION,
    map_minutes_feature_case_fixture,
    validate_feature_rows,
)
from courtvision.sports.nba.player_points_assembly import (
    NBA_PLAYER_POINTS_MARKET_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_PROBABILITY_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_PROJECTION_SCHEMA_VERSION,
    assemble_nba_player_points_batch,
    build_projection_evidence,
    validate_assembled_rows,
)
from courtvision.sports.nba.player_points_crosswalk import (
    NBA_PLAYER_POINTS_CROSSWALK_MAPPING_SCHEMA_VERSION,
    join_nba_player_points_crosswalk,
)
from courtvision.sports.nba.player_points_research import (
    NBA_PLAYER_POINTS_MARKET,
    NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
    NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL,
    NBAPlayerPointsMarketEvidence,
    NBAPlayerPointsResearchSchemaError,
    decimal_odds_from_american,
    implied_probability_from_american,
    normalize_player_name,
    toronto_operating_date,
)
from courtvision.sports.nba.player_points_settlement import (
    map_balldontlie_final_stats_fixture,
    settle_nba_player_points_predictions,
    validate_settlement_prediction_link,
)


NBA_PLAYER_POINTS_REHEARSAL_SCHEMA_VERSION: Final = "nba-player-points-rehearsal-v1"
NBA_PLAYER_POINTS_REHEARSAL_INTEGRITY_SCHEMA_VERSION: Final = (
    "nba-player-points-rehearsal-integrity-v1"
)
NBA_PLAYER_POINTS_REHEARSAL_SUMMARY_SCHEMA_VERSION: Final = (
    "nba-player-points-rehearsal-summary-v1"
)

REHEARSAL_PREDICTION_RUN_ID: Final = "run-nba-player-points-rehearsal-20260605"
REHEARSAL_MODEL_ID: Final = "nba-player-points-rehearsal-model-v1"
REHEARSAL_SOURCE_MANIFEST_ID: Final = "nba-player-points-rehearsal-manifest-preview"
REHEARSAL_REPOSITORY_COMMIT_SHA: Final = "72e50f0c05529a2af1cae297e8dd6e19dae7305f"
REHEARSAL_PREDICTION_TIMESTAMP_UTC: Final = "2026-06-05T18:20:00Z"
REHEARSAL_MANIFEST_CREATED_AT_UTC: Final = "2026-06-05T18:21:00Z"
REHEARSAL_SETTLEMENT_TIMESTAMP_UTC: Final = "2026-06-06T05:30:00Z"

_UTC: Final = timezone.utc
_BANNED_PREVIEW_PARTS: Final = (
    ("data", "history"),
    ("outputs", "runtime"),
    ("outputs", "locked"),
    ("outputs", "model"),
    ("operator",),
    ("operators",),
    ("kelly",),
)
_PREGAME_LEAKAGE_KEYS: Final = {
    "actual_points",
    "final_points",
    "target_game_actual_points",
    "target_game_final_points",
    "actual_minutes",
    "target_game_actual_minutes",
    "final_stats",
    "box_score",
}


class NBAPlayerPointsRehearsalError(NBAPlayerPointsResearchSchemaError):
    """Raised when the offline integration rehearsal fails closed."""


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsRehearsalFixtureBundle:
    """Immutable in-memory fixture bundle for the offline rehearsal."""

    schema_version: str
    market_rows: tuple[Mapping[str, object], ...]
    canonical_schedule_rows: tuple[Mapping[str, object], ...]
    reviewed_identity_mapping: Mapping[str, object]
    canonical_player_rows: tuple[Mapping[str, object], ...]
    minutes_cases: tuple[Mapping[str, object], ...]
    projection_rows: tuple[Mapping[str, object], ...]
    probability_rows: tuple[Mapping[str, object], ...]
    final_stat_fixture: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.schema_version != NBA_PLAYER_POINTS_REHEARSAL_SCHEMA_VERSION:
            raise NBAPlayerPointsRehearsalError(
                f"unsupported rehearsal schema_version: {self.schema_version!r}"
            )
        object.__setattr__(self, "market_rows", _freeze_mapping_sequence(self.market_rows))
        object.__setattr__(
            self,
            "canonical_schedule_rows",
            _freeze_mapping_sequence(self.canonical_schedule_rows),
        )
        object.__setattr__(
            self,
            "reviewed_identity_mapping",
            _deep_freeze_mapping(self.reviewed_identity_mapping),
        )
        object.__setattr__(
            self,
            "canonical_player_rows",
            _freeze_mapping_sequence(self.canonical_player_rows),
        )
        object.__setattr__(self, "minutes_cases", _freeze_mapping_sequence(self.minutes_cases))
        object.__setattr__(
            self,
            "projection_rows",
            _freeze_mapping_sequence(self.projection_rows),
        )
        object.__setattr__(
            self,
            "probability_rows",
            _freeze_mapping_sequence(self.probability_rows),
        )
        object.__setattr__(
            self,
            "final_stat_fixture",
            _deep_freeze_mapping(self.final_stat_fixture),
        )

    @property
    def fixture_hash(self) -> str:
        return rehearsal_source_hash(self._payload_without_hash())

    def _payload_without_hash(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "market_rows": [_json_ready(row) for row in self.market_rows],
            "canonical_schedule_rows": [
                _json_ready(row) for row in self.canonical_schedule_rows
            ],
            "reviewed_identity_mapping": _json_ready(self.reviewed_identity_mapping),
            "canonical_player_rows": [_json_ready(row) for row in self.canonical_player_rows],
            "minutes_cases": [_json_ready(row) for row in self.minutes_cases],
            "projection_rows": [_json_ready(row) for row in self.projection_rows],
            "probability_rows": [_json_ready(row) for row in self.probability_rows],
            "final_stat_fixture": _json_ready(self.final_stat_fixture),
        }

    def to_dict(self) -> dict[str, object]:
        payload = self._payload_without_hash()
        payload["fixture_hash"] = self.fixture_hash
        return payload


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsRehearsalResult:
    """Complete offline rehearsal output and deterministic summary."""

    fixture_bundle: NBAPlayerPointsRehearsalFixtureBundle
    market_rows: tuple[Mapping[str, object], ...]
    crosswalk_rows: tuple[Mapping[str, object], ...]
    minutes_rows: tuple[Mapping[str, object], ...]
    assembly_records: tuple[Mapping[str, object], ...]
    pregame_rows: tuple[Mapping[str, object], ...]
    pregame_manifest: Mapping[str, object]
    settlement_rows: tuple[Mapping[str, object], ...]
    settlement_diagnostics: Mapping[str, object]
    integrity_report: Mapping[str, object]
    rehearsal_summary: Mapping[str, object]
    preview_paths: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "market_rows", _freeze_mapping_sequence(self.market_rows))
        object.__setattr__(self, "crosswalk_rows", _freeze_mapping_sequence(self.crosswalk_rows))
        object.__setattr__(self, "minutes_rows", _freeze_mapping_sequence(self.minutes_rows))
        object.__setattr__(
            self,
            "assembly_records",
            _freeze_mapping_sequence(self.assembly_records),
        )
        object.__setattr__(self, "pregame_rows", _freeze_mapping_sequence(self.pregame_rows))
        object.__setattr__(self, "pregame_manifest", _deep_freeze_mapping(self.pregame_manifest))
        object.__setattr__(self, "settlement_rows", _freeze_mapping_sequence(self.settlement_rows))
        object.__setattr__(
            self,
            "settlement_diagnostics",
            _deep_freeze_mapping(self.settlement_diagnostics),
        )
        object.__setattr__(
            self,
            "integrity_report",
            _deep_freeze_mapping(self.integrity_report),
        )
        object.__setattr__(
            self,
            "rehearsal_summary",
            _deep_freeze_mapping(self.rehearsal_summary),
        )
        object.__setattr__(
            self,
            "preview_paths",
            tuple(str(path) for path in self.preview_paths),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "fixture_bundle": self.fixture_bundle.to_dict(),
            "market_rows": [_json_ready(row) for row in self.market_rows],
            "crosswalk_rows": [_json_ready(row) for row in self.crosswalk_rows],
            "minutes_rows": [_json_ready(row) for row in self.minutes_rows],
            "assembly_records": [_json_ready(row) for row in self.assembly_records],
            "pregame_rows": [_json_ready(row) for row in self.pregame_rows],
            "pregame_manifest": _json_ready(self.pregame_manifest),
            "settlement_rows": [_json_ready(row) for row in self.settlement_rows],
            "settlement_diagnostics": _json_ready(self.settlement_diagnostics),
            "integrity_report": _json_ready(self.integrity_report),
            "rehearsal_summary": _json_ready(self.rehearsal_summary),
            "preview_paths": list(self.preview_paths),
        }


def rehearsal_source_hash(payload: object) -> str:
    """Return a deterministic SHA-256 for an offline rehearsal fixture payload."""

    return hashlib.sha256(_stable_json_bytes(payload)).hexdigest()


def build_rehearsal_fixture_bundle() -> NBAPlayerPointsRehearsalFixtureBundle:
    """Build a realistic offline fixture bundle with no provider or credential access."""

    okc_ind_event = {
        "canonical_event_id": "nba-rehearsal-2026-06-05-okc-ind",
        "provider_event_id": "odds_evt_rehearsal_okc_ind",
        "operating_date": "2026-06-05",
        "commence_time_utc": "2026-06-06T00:40:00Z",
        "home_team": "IND",
        "away_team": "OKC",
    }
    bos_lal_event = {
        "canonical_event_id": "nba-rehearsal-2026-06-05-bos-lal",
        "provider_event_id": "odds_evt_rehearsal_bos_lal",
        "operating_date": "2026-06-05",
        "commence_time_utc": "2026-06-06T02:15:00Z",
        "home_team": "LAL",
        "away_team": "BOS",
    }
    schedule_rows = (
        _schedule_row(okc_ind_event),
        _schedule_row(bos_lal_event),
    )
    player_rows = (
        _player_row(okc_ind_event, "nba-player-chet-holmgren", "Chet Holmgren", "OKC"),
        _player_row(okc_ind_event, "nba-player-tyrese-haliburton", "Tyrese Haliburton", "IND"),
        _player_row(okc_ind_event, "nba-player-shai-gilgeous-alexander", "Shai Gilgeous-Alexander", "OKC"),
        _player_row(okc_ind_event, "nba-player-aaron-wiggins", "Aaron Wiggins", "OKC"),
        _player_row(okc_ind_event, "nba-player-jaylin-williams", "Jaylin Williams", "OKC"),
        _player_row(okc_ind_event, "nba-player-andrew-nembhard", "Andrew Nembhard", "IND"),
        _player_row(bos_lal_event, "nba-player-jayson-tatum", "Jayson Tatum", "BOS"),
        _player_row(bos_lal_event, "nba-player-lebron-james", "LeBron James", "LAL"),
        _player_row(bos_lal_event, "nba-player-austin-reaves", "Austin Reaves", "LAL"),
    )
    reviewed_mapping = _reviewed_identity_mapping(okc_ind_event, bos_lal_event, player_rows)

    market_rows = [
        _market_row(
            event=okc_ind_event,
            sportsbook="DraftKings",
            player_name="Chet Holmgren",
            team="OKC",
            opponent="IND",
            line=19.5,
            american_odds=-110,
            market_timestamp_utc="2026-06-05T18:02:00Z",
            source_slug="chet-holmgren-19-5",
        ),
        _market_row(
            event=okc_ind_event,
            sportsbook="FanDuel",
            player_name="Tyrese Haliburton",
            team="IND",
            opponent="OKC",
            line=20.5,
            american_odds=-105,
            market_timestamp_utc="2026-06-05T18:03:00Z",
            source_slug="tyrese-haliburton-20-5",
        ),
        _market_row(
            event=okc_ind_event,
            sportsbook="DraftKings",
            player_name="Shai Gilgeous-Alexander",
            team="OKC",
            opponent="IND",
            line=31.5,
            american_odds=-110,
            market_timestamp_utc="2026-06-05T18:04:00Z",
            source_slug="shai-gilgeous-alexander-31-5",
        ),
        _market_row(
            event=okc_ind_event,
            sportsbook="DraftKings",
            player_name="Shai Gilgeous-Alexander",
            team="OKC",
            opponent="IND",
            line=31.5,
            american_odds=-110,
            market_timestamp_utc="2026-06-05T18:04:00Z",
            source_slug="shai-gilgeous-alexander-31-5",
        ),
        _market_row(
            event=okc_ind_event,
            sportsbook="Caesars",
            player_name="Aaron Wiggins",
            team="OKC",
            opponent="IND",
            line=10.5,
            american_odds=100,
            market_timestamp_utc="2026-06-05T18:05:00Z",
            source_slug="aaron-wiggins-10-5",
        ),
        _market_row(
            event=okc_ind_event,
            sportsbook="BetMGM",
            player_name="Jaylin Williams",
            team="OKC",
            opponent="IND",
            line=5.5,
            american_odds=-102,
            market_timestamp_utc="2026-06-05T18:06:00Z",
            source_slug="jaylin-williams-5-5",
        ),
        _market_row(
            event=okc_ind_event,
            sportsbook="FanDuel",
            player_name="Andrew Nembhard",
            team="IND",
            opponent="OKC",
            line=13.5,
            american_odds=-115,
            market_timestamp_utc="2026-06-05T18:07:00Z",
            source_slug="andrew-nembhard-13-5",
        ),
        _market_row(
            event=bos_lal_event,
            sportsbook="DraftKings",
            player_name="Jayson Tatum",
            team="BOS",
            opponent="LAL",
            line=28.5,
            american_odds=-110,
            market_timestamp_utc="2026-06-05T18:08:00Z",
            source_slug="jayson-tatum-28-5-event-time-mismatch",
            commence_time_utc="2026-06-06T03:00:00Z",
        ),
        _market_row(
            event=bos_lal_event,
            sportsbook="FanDuel",
            player_name="Mystery Laker",
            team="LAL",
            opponent="BOS",
            line=7.5,
            american_odds=110,
            market_timestamp_utc="2026-06-05T18:09:00Z",
            source_slug="mystery-laker-7-5-unresolved",
        ),
        _market_row(
            event=bos_lal_event,
            sportsbook="DraftKings",
            player_name="LeBron James",
            team="LAL",
            opponent="BOS",
            line=24.5,
            american_odds=-110,
            market_timestamp_utc="2026-06-05T18:10:00Z",
            source_slug="lebron-james-24-5-no-final",
        ),
        _market_row(
            event={**bos_lal_event, "home_team": "BOS", "away_team": "LAL"},
            sportsbook="BetMGM",
            player_name="Austin Reaves",
            team="LAL",
            opponent="BOS",
            line=15.5,
            american_odds=-108,
            market_timestamp_utc="2026-06-05T18:11:00Z",
            source_slug="austin-reaves-15-5-reversed-event",
        ),
    ]
    market_rows.insert(1, dict(market_rows[0]))

    minutes_cases = tuple(_minutes_case_for_market(row) for row in _unique_market_rows(market_rows))
    projection_rows = tuple(_projection_rows_for_markets(market_rows))
    probability_rows = (
        _probability_row(
            market_source_id="rehearsal-market:odds_evt_rehearsal_okc_ind:fanduel:tyrese-haliburton-20-5",
            model_over_probability=0.57,
            model_under_probability=0.43,
        ),
    )
    final_stat_fixture = _final_stat_fixture()

    return NBAPlayerPointsRehearsalFixtureBundle(
        schema_version=NBA_PLAYER_POINTS_REHEARSAL_SCHEMA_VERSION,
        market_rows=tuple(market_rows),
        canonical_schedule_rows=schedule_rows,
        reviewed_identity_mapping=reviewed_mapping,
        canonical_player_rows=player_rows,
        minutes_cases=minutes_cases,
        projection_rows=projection_rows,
        probability_rows=probability_rows,
        final_stat_fixture=final_stat_fixture,
    )


def run_nba_player_points_rehearsal(
    fixture_bundle: NBAPlayerPointsRehearsalFixtureBundle | None = None,
    *,
    preview_output_dir: str | Path | None = None,
) -> NBAPlayerPointsRehearsalResult:
    """Run the full offline integration rehearsal and optional temp previews."""

    bundle = fixture_bundle or build_rehearsal_fixture_bundle()
    fixture_hash_before = bundle.fixture_hash
    market_rows = _validated_market_rows(bundle.market_rows)
    crosswalk_result = join_nba_player_points_crosswalk(
        market_rows,
        bundle.canonical_schedule_rows,
        bundle.canonical_player_rows,
        reviewed_event_mapping=bundle.reviewed_identity_mapping,
        reviewed_player_mapping=bundle.reviewed_identity_mapping,
    )
    crosswalk_rows = tuple(row.to_dict() for row in crosswalk_result.rows)
    minutes_rows_by_market_source = _build_minutes_rows(bundle.minutes_cases, crosswalk_rows)
    minutes_rows = tuple(row.to_dict() for row in minutes_rows_by_market_source.values())
    _validate_projection_rows(bundle.projection_rows, market_rows)

    assembly_records = _build_assembly_records(
        market_rows=market_rows,
        crosswalk_rows=crosswalk_rows,
        minutes_rows_by_market_source=minutes_rows_by_market_source,
        projection_rows=bundle.projection_rows,
        probability_rows=bundle.probability_rows,
    )
    integrity_report = _verify_pregame_integrity(
        bundle=bundle,
        fixture_hash_before=fixture_hash_before,
        market_rows=market_rows,
        crosswalk_rows=crosswalk_rows,
        minutes_rows=minutes_rows,
        assembly_records=assembly_records,
    )
    if integrity_report["violations"]:
        raise NBAPlayerPointsRehearsalError(
            "pregame integrity violations: "
            + "; ".join(str(item) for item in integrity_report["violations"])
        )

    assembly_result = assemble_nba_player_points_batch(
        assembly_records,
        manifest_created_at_utc=REHEARSAL_MANIFEST_CREATED_AT_UTC,
    )
    _validate_non_duplicate_assembled_rows(assembly_result.rows)
    pregame_rows = tuple(row.to_dict() for row in assembly_result.rows)
    pregame_manifest = assembly_result.source_manifest_preview.to_dict()

    settlement_predictions = _settlement_prediction_rows(assembly_result.rows)
    settlement_prediction_snapshots = {
        row["prediction_id"]: _json_clone(row) for row in settlement_predictions
    }
    final_rows = map_balldontlie_final_stats_fixture(
        _json_clone_mapping(bundle.final_stat_fixture)
    ).rows
    settlement_result = settle_nba_player_points_predictions(
        settlement_predictions,
        crosswalk_result.rows,
        final_rows,
        settlement_timestamp_utc=REHEARSAL_SETTLEMENT_TIMESTAMP_UTC,
        repository_commit_sha=REHEARSAL_REPOSITORY_COMMIT_SHA,
    )
    prediction_by_id = {row["prediction_id"]: row for row in settlement_predictions}
    for settlement_row in settlement_result.rows:
        validate_settlement_prediction_link(
            settlement_row,
            prediction_by_id[settlement_row.prediction_id],
        )
    settlement_rows = tuple(row.to_dict() for row in settlement_result.rows)
    settlement_diagnostics = {
        key: tuple(dict(entry) for entry in entries)
        for key, entries in settlement_result.diagnostics.items()
    }

    summary = _build_rehearsal_summary(
        fixture_bundle=bundle,
        fixture_hash_before=fixture_hash_before,
        fixture_hash_after=bundle.fixture_hash,
        assembly_records=assembly_records,
        assembly_rows=pregame_rows,
        assembly_result_summary=dict(assembly_result.batch_summary_counts),
        duplicate_diagnostics=[
            diagnostic.to_dict() for diagnostic in assembly_result.duplicate_diagnostics
        ],
        pregame_manifest=pregame_manifest,
        crosswalk_rows=crosswalk_rows,
        settlement_predictions=settlement_predictions,
        settlement_prediction_snapshots=settlement_prediction_snapshots,
        settlement_rows=settlement_rows,
        settlement_diagnostics=settlement_diagnostics,
        final_stat_fixture=bundle.final_stat_fixture,
    )
    preview_paths = _write_preview_outputs(
        preview_output_dir,
        pregame_rows=pregame_rows,
        pregame_manifest=pregame_manifest,
        settlement_rows=settlement_rows,
        settlement_diagnostics=settlement_diagnostics,
        integrity_report=integrity_report,
        rehearsal_summary=summary,
    )
    return NBAPlayerPointsRehearsalResult(
        fixture_bundle=bundle,
        market_rows=market_rows,
        crosswalk_rows=crosswalk_rows,
        minutes_rows=minutes_rows,
        assembly_records=assembly_records,
        pregame_rows=pregame_rows,
        pregame_manifest=pregame_manifest,
        settlement_rows=settlement_rows,
        settlement_diagnostics=settlement_diagnostics,
        integrity_report=integrity_report,
        rehearsal_summary=summary,
        preview_paths=preview_paths,
    )


def _schedule_row(event: Mapping[str, object]) -> dict[str, object]:
    return {
        "canonical_event_id": event["canonical_event_id"],
        "operating_date": event["operating_date"],
        "commence_time_utc": event["commence_time_utc"],
        "home_team": event["home_team"],
        "away_team": event["away_team"],
    }


def _player_row(
    event: Mapping[str, object],
    player_id: str,
    player_name: str,
    team: str,
) -> dict[str, object]:
    return {
        "canonical_event_id": event["canonical_event_id"],
        "player_id": player_id,
        "canonical_player_name": player_name,
        "canonical_team": team,
    }


def _reviewed_identity_mapping(
    okc_ind_event: Mapping[str, object],
    bos_lal_event: Mapping[str, object],
    player_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    event_mappings = [
        {
            "provider_name": "the_odds_api_nba",
            "provider_event_id": okc_ind_event["provider_event_id"],
            "canonical_event_id": okc_ind_event["canonical_event_id"],
            "canonical_home_team": okc_ind_event["home_team"],
            "canonical_away_team": okc_ind_event["away_team"],
            "mapping_source": "offline_rehearsal_review",
            "reviewed_at": "2026-06-05T17:30:00Z",
            "review_status": "approved",
            "reviewer": "nba-rehearsal-fixture",
            "review_reference": "fixture:event:okc-ind",
        },
        {
            "provider_name": "the_odds_api_nba",
            "provider_event_id": bos_lal_event["provider_event_id"],
            "canonical_event_id": bos_lal_event["canonical_event_id"],
            "canonical_home_team": bos_lal_event["home_team"],
            "canonical_away_team": bos_lal_event["away_team"],
            "mapping_source": "offline_rehearsal_review",
            "reviewed_at": "2026-06-05T17:31:00Z",
            "review_status": "approved",
            "reviewer": "nba-rehearsal-fixture",
            "review_reference": "fixture:event:bos-lal",
        },
    ]
    player_mappings = [
        {
            "provider_name": "the_odds_api_nba",
            "provider_player_name": row["canonical_player_name"],
            "player_id": row["player_id"],
            "canonical_player_name": row["canonical_player_name"],
            "canonical_team": row["canonical_team"],
            "mapping_type": "identity",
            "mapping_source": "offline_rehearsal_review",
            "reviewed_at": "2026-06-05T17:40:00Z",
            "review_status": "approved",
            "reviewer": "nba-rehearsal-fixture",
            "review_reference": f"fixture:player:{row['player_id']}",
        }
        for row in player_rows
    ]
    return {
        "schema_version": NBA_PLAYER_POINTS_CROSSWALK_MAPPING_SCHEMA_VERSION,
        "mapping_version": "nba-player-points-rehearsal-crosswalk-v1",
        "event_mappings": event_mappings,
        "player_mappings": player_mappings,
    }


def _market_row(
    *,
    event: Mapping[str, object],
    sportsbook: str,
    player_name: str,
    team: str,
    opponent: str,
    line: float,
    american_odds: int,
    market_timestamp_utc: str,
    source_slug: str,
    commence_time_utc: str | None = None,
) -> dict[str, object]:
    row = {
        "provider_name": "the_odds_api_nba",
        "provider_event_id": event["provider_event_id"],
        "home_team": event["home_team"],
        "away_team": event["away_team"],
        "commence_time_utc": commence_time_utc or event["commence_time_utc"],
        "operating_date": toronto_operating_date(_parse_utc(commence_time_utc or str(event["commence_time_utc"]))).isoformat(),
        "team": team,
        "opponent": opponent,
        "player_name": player_name,
        "provider_player_name": player_name,
        "normalized_player_name": normalize_player_name(player_name),
        "sportsbook": sportsbook,
        "market": NBA_PLAYER_POINTS_MARKET,
        "side": "over",
        "line": line,
        "american_odds": american_odds,
        "decimal_odds": decimal_odds_from_american(american_odds),
        "implied_probability": implied_probability_from_american(american_odds),
        "market_timestamp_utc": market_timestamp_utc,
        "market_source_id": f"rehearsal-market:{event['provider_event_id']}:{sportsbook.casefold()}:{source_slug}",
        "market_schema_version": NBA_PLAYER_POINTS_MARKET_SCHEMA_VERSION,
    }
    row["market_source_hash"] = rehearsal_source_hash(row)
    return row


def _unique_market_rows(rows: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    seen: set[str] = set()
    unique: list[Mapping[str, object]] = []
    for row in rows:
        source_id = str(row["market_source_id"])
        if source_id in seen:
            continue
        seen.add(source_id)
        unique.append(row)
    return tuple(unique)


def _minutes_case_for_market(market_row: Mapping[str, object]) -> dict[str, object]:
    player_name = str(market_row["player_name"])
    profile = {
        "Chet Holmgren": (31.2, 32.4, 33, 6, 4.2, 34.0, "confirmed_starter", "confirmed", "active", "active", "stable"),
        "Tyrese Haliburton": (34.4, 35.1, 39, 7, 5.1, 36.5, "confirmed_starter", "confirmed", "active", "active", "stable"),
        "Shai Gilgeous-Alexander": (35.9, 36.8, 42, 8, 3.9, 37.2, "confirmed_starter", "confirmed", "active", "active", "stable"),
        "Aaron Wiggins": (21.4, 22.7, 28, 6, 6.2, 24.0, "unknown", "unconfirmed", "active", "available", "stable"),
        "Jaylin Williams": (15.0, 14.2, 24, 5, 4.0, 12.0, "confirmed_bench", "confirmed", "inactive", "inactive", "stable"),
        "Andrew Nembhard": (28.5, 29.1, 36, 7, 4.4, 30.0, "confirmed_starter", "confirmed", "active", "active", "stable"),
        "Jayson Tatum": (37.1, 38.0, 40, 6, 4.8, 39.0, "confirmed_starter", "confirmed", "active", "active", "stable"),
        "Mystery Laker": (14.0, 16.0, 12, 4, 7.0, 18.0, "unknown", "confirmed", "active", "available", "stable"),
        "LeBron James": (34.8, 35.5, 41, 7, 5.5, 36.0, "confirmed_starter", "confirmed", "active", "active", "stable"),
        "Austin Reaves": (31.0, 31.8, 34, 6, 4.7, 33.0, "confirmed_starter", "confirmed", "active", "active", "stable"),
    }[player_name]
    (
        season_minutes,
        recent_minutes,
        season_sample,
        recent_sample,
        recent_stddev,
        last_game,
        starter_status,
        lineup_status,
        injury_status,
        availability_status,
        role_status,
    ) = profile
    return {
        "market_source_id": market_row["market_source_id"],
        "feature_timestamp_utc": "2026-06-05T18:12:00Z",
        "feature_cutoff_timestamp_utc": "2026-06-05T18:30:00Z",
        "source_manifest_id": REHEARSAL_SOURCE_MANIFEST_ID,
        "repository_commit_sha": REHEARSAL_REPOSITORY_COMMIT_SHA,
        "baseline": {
            "provider_event_id": market_row["provider_event_id"],
            "canonical_event_id": None,
            "player_id": None,
            "canonical_player_name": player_name,
            "team": market_row["team"],
            "opponent": market_row["opponent"],
            "commence_time_utc": market_row["commence_time_utc"],
            "event_identity_status": "resolved",
            "player_identity_status": "resolved",
            "min_avg": season_minutes,
            "min_recent": recent_minutes,
            "season_minutes_sample_size": season_sample,
            "recent_minutes_sample_size": recent_sample,
            "recent_minutes_stddev": recent_stddev,
            "last_game_minutes": last_game,
            "source_timestamp_utc": "2026-06-05T17:45:00Z",
            "source_reference": f"fixture:baseline:{market_row['market_source_id']}",
        },
        "lineup": {
            "starter_status": starter_status,
            "lineup_status": lineup_status,
            "source_timestamp_utc": "2026-06-05T17:50:00Z",
            "source_reference": f"fixture:lineup:{market_row['market_source_id']}",
        },
        "injury_availability": {
            "injury_status": injury_status,
            "availability_status": availability_status,
            "source_timestamp_utc": "2026-06-05T17:51:00Z",
            "source_reference": f"fixture:availability:{market_row['market_source_id']}",
        },
        "schedule": {
            "days_rest": 2,
            "games_last_7_days": 3,
            "games_last_14_days": 6,
            "source_timestamp_utc": "2026-06-05T17:30:00Z",
            "source_reference": f"fixture:schedule:{market_row['market_source_id']}",
        },
        "role_context": {
            "role_status": role_status,
            "teammate_absence_context": {
                "verified": False,
                "absent_teammates": [],
                "review_note": "offline rehearsal fixture",
            },
            "source_timestamp_utc": "2026-06-05T17:55:00Z",
            "source_reference": f"fixture:role:{market_row['market_source_id']}",
        },
    }


def _projection_rows_for_markets(
    market_rows: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    projected_points = {
        "Chet Holmgren": 20.2,
        "Tyrese Haliburton": 21.4,
        "Shai Gilgeous-Alexander": 32.6,
        "Aaron Wiggins": 11.2,
        "Jaylin Williams": 4.8,
        "Andrew Nembhard": 14.3,
        "Jayson Tatum": 29.8,
        "Mystery Laker": 8.1,
        "LeBron James": 25.9,
        "Austin Reaves": 16.4,
    }
    rows: list[dict[str, object]] = []
    occurrences: dict[str, int] = {}
    for market in market_rows:
        source_id = str(market["market_source_id"])
        occurrence = occurrences.get(source_id, 0)
        occurrences[source_id] = occurrence + 1
        if occurrence > 0 and market["player_name"] != "Shai Gilgeous-Alexander":
            continue
        points = projected_points[str(market["player_name"])]
        if market["player_name"] == "Shai Gilgeous-Alexander" and occurrence == 1:
            points = 30.1
        rows.append(
            _projection_row(
                market_source_id=source_id,
                occurrence_index=occurrence,
                projected_points=points,
            )
        )
    return tuple(rows)


def _projection_row(
    *,
    market_source_id: str,
    occurrence_index: int,
    projected_points: float,
) -> dict[str, object]:
    row = {
        "market_source_id": market_source_id,
        "occurrence_index": occurrence_index,
        "projected_points": projected_points,
        "projection_method": "offline_rehearsal_points_projection_v1",
        "projection_timestamp_utc": "2026-06-05T18:15:00Z",
        "projection_cutoff_timestamp_utc": "2026-06-05T18:30:00Z",
        "projection_source": "offline_rehearsal_projection_fixture",
        "projection_source_id": f"rehearsal-projection:{market_source_id}:occurrence:{occurrence_index}",
        "projection_schema_version": NBA_PLAYER_POINTS_PROJECTION_SCHEMA_VERSION,
    }
    row["projection_source_hash"] = rehearsal_source_hash(row)
    return row


def _probability_row(
    *,
    market_source_id: str,
    model_over_probability: float,
    model_under_probability: float,
) -> dict[str, object]:
    row = {
        "market_source_id": market_source_id,
        "model_over_probability": model_over_probability,
        "model_under_probability": model_under_probability,
        "probability_source_id": f"rehearsal-probability:{market_source_id}",
        "probability_model_id": "nba-player-points-probability-validation-rehearsal-v1",
        "probability_schema_version": NBA_PLAYER_POINTS_PROBABILITY_SCHEMA_VERSION,
        "probability_timestamp_utc": "2026-06-05T18:16:00Z",
        "claims_probability_eligibility": True,
    }
    row["probability_source_hash"] = rehearsal_source_hash(row)
    return row


def _final_stat_fixture() -> dict[str, object]:
    rows = [
        _final_row(
            source_row_id="bdl-rehearsal-chet-valid",
            canonical_event_id="nba-rehearsal-2026-06-05-okc-ind",
            provider_event_id="bdl-game-rehearsal-okc-ind",
            commence_time_utc="2026-06-06T00:40:00Z",
            home_team="IND",
            away_team="OKC",
            player_id="nba-player-chet-holmgren",
            canonical_player_name="Chet Holmgren",
            team="OKC",
            opponent="IND",
            final_points=23.0,
            actual_minutes=34.5,
            participation_status="participated",
        ),
        _final_row(
            source_row_id="bdl-rehearsal-chet-valid",
            canonical_event_id="nba-rehearsal-2026-06-05-okc-ind",
            provider_event_id="bdl-game-rehearsal-okc-ind",
            commence_time_utc="2026-06-06T00:40:00Z",
            home_team="IND",
            away_team="OKC",
            player_id="nba-player-chet-holmgren",
            canonical_player_name="Chet Holmgren",
            team="OKC",
            opponent="IND",
            final_points=23.0,
            actual_minutes=34.5,
            participation_status="participated",
        ),
        _final_row(
            source_row_id="bdl-rehearsal-tyrese-missing-minutes",
            canonical_event_id="nba-rehearsal-2026-06-05-okc-ind",
            provider_event_id="bdl-game-rehearsal-okc-ind",
            commence_time_utc="2026-06-06T00:40:00Z",
            home_team="IND",
            away_team="OKC",
            player_id="nba-player-tyrese-haliburton",
            canonical_player_name="Tyrese Haliburton",
            team="IND",
            opponent="OKC",
            final_points=24.0,
            actual_minutes=None,
            participation_status="participated",
        ),
        _final_row(
            source_row_id="bdl-rehearsal-aaron-valid",
            canonical_event_id="nba-rehearsal-2026-06-05-okc-ind",
            provider_event_id="bdl-game-rehearsal-okc-ind",
            commence_time_utc="2026-06-06T00:40:00Z",
            home_team="IND",
            away_team="OKC",
            player_id="nba-player-aaron-wiggins",
            canonical_player_name="Aaron Wiggins",
            team="OKC",
            opponent="IND",
            final_points=12.0,
            actual_minutes=23.25,
            participation_status="participated",
        ),
        _final_row(
            source_row_id="bdl-rehearsal-jaylin-dnp",
            canonical_event_id="nba-rehearsal-2026-06-05-okc-ind",
            provider_event_id="bdl-game-rehearsal-okc-ind",
            commence_time_utc="2026-06-06T00:40:00Z",
            home_team="IND",
            away_team="OKC",
            player_id="nba-player-jaylin-williams",
            canonical_player_name="Jaylin Williams",
            team="OKC",
            opponent="IND",
            final_points=0.0,
            actual_minutes=None,
            participation_status="did_not_participate",
        ),
        _final_row(
            source_row_id="bdl-rehearsal-nembhard-conflict-a",
            canonical_event_id="nba-rehearsal-2026-06-05-okc-ind",
            provider_event_id="bdl-game-rehearsal-okc-ind",
            commence_time_utc="2026-06-06T00:40:00Z",
            home_team="IND",
            away_team="OKC",
            player_id="nba-player-andrew-nembhard",
            canonical_player_name="Andrew Nembhard",
            team="IND",
            opponent="OKC",
            final_points=13.0,
            actual_minutes=29.0,
            participation_status="participated",
        ),
        _final_row(
            source_row_id="bdl-rehearsal-nembhard-conflict-b",
            canonical_event_id="nba-rehearsal-2026-06-05-okc-ind",
            provider_event_id="bdl-game-rehearsal-okc-ind",
            commence_time_utc="2026-06-06T00:40:00Z",
            home_team="IND",
            away_team="OKC",
            player_id="nba-player-andrew-nembhard",
            canonical_player_name="Andrew Nembhard",
            team="IND",
            opponent="OKC",
            final_points=15.0,
            actual_minutes=29.0,
            participation_status="participated",
        ),
    ]
    return {
        "provider_name": "balldontlie",
        "source_timestamp_utc": "2026-06-06T05:10:00Z",
        "rows": rows,
    }


def _final_row(
    *,
    source_row_id: str,
    canonical_event_id: str,
    provider_event_id: str,
    commence_time_utc: str,
    home_team: str,
    away_team: str,
    player_id: str,
    canonical_player_name: str,
    team: str,
    opponent: str,
    final_points: float | None,
    actual_minutes: float | None,
    participation_status: str,
) -> dict[str, object]:
    row: dict[str, object] = {
        "source_row_id": source_row_id,
        "provider_event_id": provider_event_id,
        "canonical_event_id": canonical_event_id,
        "commence_time_utc": commence_time_utc,
        "home_team": home_team,
        "away_team": away_team,
        "player_id": player_id,
        "canonical_player_name": canonical_player_name,
        "team": team,
        "opponent": opponent,
        "game_status": "final",
        "game_final": True,
        "final_points": final_points,
        "actual_minutes": actual_minutes,
        "participation_status": participation_status,
        "source_timestamp_utc": "2026-06-06T05:10:00Z",
    }
    return row


def _validated_market_rows(
    raw_rows: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    rows: list[Mapping[str, object]] = []
    for raw in raw_rows:
        commence = _parse_utc(raw["commence_time_utc"])
        evidence = NBAPlayerPointsMarketEvidence(
            provider_name=raw["provider_name"],
            provider_event_id=raw["provider_event_id"],
            canonical_event_id=None,
            operating_date=toronto_operating_date(commence),
            operating_timezone=NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
            commence_time_utc=commence,
            team=raw["team"],
            opponent=raw["opponent"],
            player_name=raw["player_name"],
            normalized_player_name=normalize_player_name(raw["player_name"]),
            sportsbook=raw["sportsbook"],
            market=raw["market"],
            side=raw["side"],
            line=raw["line"],
            american_odds=raw["american_odds"],
            decimal_odds=raw["decimal_odds"],
            implied_probability=raw["implied_probability"],
            market_timestamp_utc=_parse_utc(raw["market_timestamp_utc"]),
        )
        row = evidence.to_dict()
        row.update(
            {
                "home_team": raw["home_team"],
                "away_team": raw["away_team"],
                "provider_player_name": raw["provider_player_name"],
                "market_source_id": raw["market_source_id"],
                "market_source_hash": raw["market_source_hash"],
                "market_schema_version": raw["market_schema_version"],
                "side": raw["side"],
            }
        )
        rows.append(MappingProxyType(row))
    return tuple(rows)


def _build_minutes_rows(
    minutes_cases: Sequence[Mapping[str, object]],
    crosswalk_rows: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    crosswalk_by_source_id = {
        str(_require_mapping(row["original_odds_row"], "original_odds_row")["market_source_id"]): row
        for row in crosswalk_rows
    }
    rows: dict[str, Any] = {}
    for payload in minutes_cases:
        case_payload = _json_clone_mapping(payload)
        source_id = str(case_payload.pop("market_source_id"))
        crosswalk = crosswalk_by_source_id[source_id]
        event = _require_mapping(crosswalk["event_identity"], "event_identity")
        player = _require_mapping(crosswalk["player_identity"], "player_identity")
        original = _require_mapping(crosswalk["original_odds_row"], "original_odds_row")
        baseline = _require_mapping(case_payload["baseline"], "baseline")
        baseline["canonical_event_id"] = event.get("canonical_event_id")
        baseline["player_id"] = player.get("player_id")
        baseline["canonical_player_name"] = (
            player.get("canonical_player_name") or original["provider_player_name"]
        )
        baseline["team"] = player.get("canonical_team") or original["team"]
        baseline["opponent"] = original["opponent"]
        baseline["commence_time_utc"] = event["commence_time_utc"]
        baseline["event_identity_status"] = event["event_identity_status"]
        baseline["player_identity_status"] = player["player_identity_status"]
        row = map_minutes_feature_case_fixture(case_payload)
        rows[source_id] = row
    validate_feature_rows(tuple(rows.values()))
    return rows


def _validate_projection_rows(
    projection_rows: Sequence[Mapping[str, object]],
    market_rows: Sequence[Mapping[str, object]],
) -> None:
    market_by_source_id = {str(row["market_source_id"]): row for row in market_rows}
    for row in projection_rows:
        market = market_by_source_id[str(row["market_source_id"])]
        build_projection_evidence(row, commence_time_utc=market["commence_time_utc"])


def _build_assembly_records(
    *,
    market_rows: Sequence[Mapping[str, object]],
    crosswalk_rows: Sequence[Mapping[str, object]],
    minutes_rows_by_market_source: Mapping[str, Any],
    projection_rows: Sequence[Mapping[str, object]],
    probability_rows: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    records: list[Mapping[str, object]] = []
    occurrences: dict[str, int] = {}
    for market, crosswalk in zip(market_rows, crosswalk_rows, strict=True):
        market_source_id = str(market["market_source_id"])
        occurrence = occurrences.get(market_source_id, 0)
        occurrences[market_source_id] = occurrence + 1
        minutes = minutes_rows_by_market_source[market_source_id].to_dict()
        record = {
            "market": _assembly_market_view(market),
            "crosswalk": _assembly_crosswalk_view(crosswalk),
            "minutes": _assembly_minutes_view(minutes),
            "projection": _projection_for(projection_rows, market_source_id, occurrence),
            "probability": _probability_for(probability_rows, market_source_id, occurrence),
            "provenance": _provenance(),
        }
        records.append(MappingProxyType(record))
    return tuple(records)


def _assembly_market_view(market: Mapping[str, object]) -> dict[str, object]:
    return {
        "provider_event_id": market["provider_event_id"],
        "sportsbook": market["sportsbook"],
        "market": market["market"],
        "provider_player_name": market["provider_player_name"],
        "line": market["line"],
        "american_odds": market["american_odds"],
        "decimal_odds": market["decimal_odds"],
        "implied_probability": market["implied_probability"],
        "market_timestamp_utc": market["market_timestamp_utc"],
        "market_source_id": market["market_source_id"],
        "market_source_hash": market["market_source_hash"],
        "market_schema_version": market["market_schema_version"],
    }


def _assembly_crosswalk_view(crosswalk: Mapping[str, object]) -> dict[str, object]:
    event = _require_mapping(crosswalk["event_identity"], "event_identity")
    player = _require_mapping(crosswalk["player_identity"], "player_identity")
    original = _require_mapping(crosswalk["original_odds_row"], "original_odds_row")
    source_payload = {
        "event_identity": event,
        "player_identity": player,
        "resolution_provenance": crosswalk["resolution_provenance"],
    }
    return {
        "canonical_event_id": crosswalk["canonical_event_id"],
        "player_id": crosswalk["canonical_player_id"],
        "canonical_player_name": player.get("canonical_player_name") or original["provider_player_name"],
        "team": player.get("canonical_team") or original["team"],
        "opponent": original["opponent"],
        "commence_time_utc": event["commence_time_utc"],
        "operating_date": event["operating_date"],
        "event_identity_status": event["event_identity_status"],
        "player_identity_status": player["player_identity_status"],
        "event_identity_method": event["event_identity_method"],
        "player_identity_method": player["player_identity_method"],
        "mapping_version": event["mapping_version"],
        "crosswalk_source_hashes": {
            "reviewed_mapping": rehearsal_source_hash(source_payload),
            "crosswalk_join": rehearsal_source_hash(crosswalk),
        },
    }


def _assembly_minutes_view(minutes: Mapping[str, object]) -> dict[str, object]:
    return {
        "canonical_event_id": minutes["canonical_event_id"],
        "player_id": minutes["player_id"],
        "canonical_player_name": minutes["canonical_player_name"],
        "team": minutes["team"],
        "opponent": minutes["opponent"],
        "operating_date": minutes["operating_date"],
        "commence_time_utc": minutes["commence_time_utc"],
        "projected_minutes": minutes["projected_minutes"],
        "projected_minutes_low": minutes["projected_minutes_low"],
        "projected_minutes_high": minutes["projected_minutes_high"],
        "minutes_confidence": minutes["minutes_confidence"],
        "minutes_projection_status": minutes["minutes_projection_status"],
        "minutes_projection_method": minutes["minutes_projection_method"],
        "minutes_exclusion_reason": minutes["minutes_exclusion_reason"],
        "feature_timestamp_utc": minutes["feature_timestamp_utc"],
        "feature_cutoff_timestamp_utc": minutes["feature_cutoff_timestamp_utc"],
        "minutes_source_hashes": minutes["source_hashes"],
        "feature_schema_version": minutes["feature_schema_version"],
        "lineup_status": minutes["lineup_status"],
        "injury_status": minutes["injury_status"],
        "recent_minutes": minutes["recent_minutes"],
        "season_minutes": minutes["season_minutes"],
    }


def _projection_for(
    projection_rows: Sequence[Mapping[str, object]],
    market_source_id: str,
    occurrence_index: int,
) -> Mapping[str, object]:
    fallback: Mapping[str, object] | None = None
    for row in projection_rows:
        if row["market_source_id"] != market_source_id:
            continue
        if int(row.get("occurrence_index", 0)) == occurrence_index:
            return row
        if int(row.get("occurrence_index", 0)) == 0:
            fallback = row
    if fallback is not None:
        return fallback
    raise NBAPlayerPointsRehearsalError(f"missing projection row for {market_source_id}")


def _probability_for(
    probability_rows: Sequence[Mapping[str, object]],
    market_source_id: str,
    occurrence_index: int,
) -> Mapping[str, object] | None:
    for row in probability_rows:
        if row["market_source_id"] != market_source_id:
            continue
        if int(row.get("occurrence_index", 0)) == occurrence_index:
            return row
    return None


def _provenance() -> dict[str, object]:
    return {
        "prediction_run_id": REHEARSAL_PREDICTION_RUN_ID,
        "model_id": REHEARSAL_MODEL_ID,
        "repository_commit_sha": REHEARSAL_REPOSITORY_COMMIT_SHA,
        "source_manifest_id": REHEARSAL_SOURCE_MANIFEST_ID,
        "prediction_timestamp_utc": REHEARSAL_PREDICTION_TIMESTAMP_UTC,
        "research_label": NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL,
    }


def _verify_pregame_integrity(
    *,
    bundle: NBAPlayerPointsRehearsalFixtureBundle,
    fixture_hash_before: str,
    market_rows: Sequence[Mapping[str, object]],
    crosswalk_rows: Sequence[Mapping[str, object]],
    minutes_rows: Sequence[Mapping[str, object]],
    assembly_records: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    violations: list[str] = []
    timestamp_fields_checked = 0
    cutoff_checks = 0
    source_records_checked = 0

    for row in market_rows:
        timestamp_fields_checked += _assert_utc_fields(
            row,
            ("commence_time_utc", "market_timestamp_utc"),
            violations,
        )
    for row in minutes_rows:
        timestamp_fields_checked += _assert_utc_fields(
            row,
            ("commence_time_utc", "feature_timestamp_utc", "feature_cutoff_timestamp_utc"),
            violations,
        )
    for record in assembly_records:
        market = _require_mapping(record["market"], "market")
        crosswalk = _require_mapping(record["crosswalk"], "crosswalk")
        minutes = _require_mapping(record["minutes"], "minutes")
        projection = _require_mapping(record["projection"], "projection")
        probability = record.get("probability")

        cutoff_checks += 1
        tipoff = _parse_utc(crosswalk["commence_time_utc"])
        feature_cutoff = _parse_utc(minutes["feature_cutoff_timestamp_utc"])
        projection_cutoff = _parse_utc(projection["projection_cutoff_timestamp_utc"])
        if _parse_utc(market["market_timestamp_utc"]) > feature_cutoff:
            violations.append("market evidence is after cutoff")
        if _parse_utc(minutes["feature_timestamp_utc"]) > feature_cutoff:
            violations.append("minutes evidence is after cutoff")
        if _parse_utc(projection["projection_timestamp_utc"]) > projection_cutoff:
            violations.append("projection evidence is after cutoff")
        if not (feature_cutoff < tipoff and projection_cutoff < tipoff):
            violations.append("cutoff must predate tipoff")
        operating_date = date.fromisoformat(str(crosswalk["operating_date"]))
        if operating_date != toronto_operating_date(tipoff):
            violations.append("America/Toronto operating date mismatch")
        if probability:
            probability_mapping = _require_mapping(probability, "probability")
            if _parse_utc(probability_mapping["probability_timestamp_utc"]) > feature_cutoff:
                violations.append("probability evidence is after cutoff")

        source_records_checked += _assert_source_hash("market", market, "market_source_id", "market_source_hash", violations)
        source_records_checked += _assert_source_hash("projection", projection, "projection_source_id", "projection_source_hash", violations)
        if probability:
            probability_mapping = _require_mapping(probability, "probability")
            source_records_checked += _assert_source_hash(
                "probability",
                probability_mapping,
                "probability_source_id",
                "probability_source_hash",
                violations,
            )
        source_records_checked += _assert_hash_mapping(
            "crosswalk",
            _require_mapping(crosswalk["crosswalk_source_hashes"], "crosswalk_source_hashes"),
            violations,
        )
        source_records_checked += _assert_hash_mapping(
            "minutes",
            _require_mapping(minutes["minutes_source_hashes"], "minutes_source_hashes"),
            violations,
        )

    if _contains_leakage(
        {
            "market_rows": [_json_ready(row) for row in bundle.market_rows],
            "canonical_schedule_rows": [_json_ready(row) for row in bundle.canonical_schedule_rows],
            "reviewed_identity_mapping": _json_ready(bundle.reviewed_identity_mapping),
            "canonical_player_rows": [_json_ready(row) for row in bundle.canonical_player_rows],
            "minutes_cases": [_json_ready(row) for row in bundle.minutes_cases],
            "projection_rows": [_json_ready(row) for row in bundle.projection_rows],
            "probability_rows": [_json_ready(row) for row in bundle.probability_rows],
        }
    ):
        violations.append("pregame fixture contains target-game actual values")

    schema_versions_supported = _schema_versions_supported(bundle, assembly_records)
    if not schema_versions_supported:
        violations.append("unsupported schema version in rehearsal fixture")

    event_conflicts = [
        row
        for row in crosswalk_rows
        if _require_mapping(row["event_identity"], "event_identity")["event_identity_status"]
        in {"conflicting", "quarantined", "ambiguous"}
    ]
    player_conflicts = [
        row
        for row in crosswalk_rows
        if _require_mapping(row["player_identity"], "player_identity")["player_identity_status"]
        in {"conflicting", "quarantined", "ambiguous"}
    ]
    return {
        "integrity_schema_version": NBA_PLAYER_POINTS_REHEARSAL_INTEGRITY_SCHEMA_VERSION,
        "all_timestamps_utc": not any("UTC" in violation or "timezone" in violation for violation in violations),
        "timestamp_fields_checked": timestamp_fields_checked,
        "cutoff_checks": cutoff_checks,
        "source_records_checked": source_records_checked,
        "source_ids_present": not any("source ID" in violation for violation in violations),
        "source_hashes_present": not any("source hash" in violation for violation in violations),
        "schema_versions_supported": schema_versions_supported,
        "no_target_actual_points_in_pregame": not _contains_key(
            bundle.to_dict(),
            {"actual_points", "final_points", "target_game_actual_points", "target_game_final_points"},
            skip_keys={"final_stat_fixture"},
        ),
        "no_target_actual_minutes_in_pregame": not _contains_key(
            bundle.to_dict(),
            {"actual_minutes", "target_game_actual_minutes"},
            skip_keys={"final_stat_fixture"},
        ),
        "fixture_hash_before": fixture_hash_before,
        "fixture_hash_after": bundle.fixture_hash,
        "input_fixtures_unchanged": fixture_hash_before == bundle.fixture_hash,
        "event_conflicts_detected": len(event_conflicts),
        "player_conflicts_detected": len(player_conflicts),
        "violations": tuple(violations),
    }


def _validate_non_duplicate_assembled_rows(rows: Sequence[Any]) -> None:
    unique_rows: list[Any] = []
    seen: set[str] = set()
    for row in rows:
        if row.prediction_id in seen:
            continue
        seen.add(row.prediction_id)
        unique_rows.append(row)
    validate_assembled_rows(tuple(unique_rows))


def _settlement_prediction_rows(rows: Sequence[Any]) -> tuple[Mapping[str, object], ...]:
    prediction_rows = [
        row.to_dict()
        for row in rows
        if row.identity_status == "resolved"
        and row.canonical_event_id is not None
        and row.player_id is not None
        and row.assembly_status != "conflicting"
    ]
    return tuple(
        MappingProxyType(row)
        for row in sorted(prediction_rows, key=lambda item: str(item["prediction_id"]))
    )


def _build_rehearsal_summary(
    *,
    fixture_bundle: NBAPlayerPointsRehearsalFixtureBundle,
    fixture_hash_before: str,
    fixture_hash_after: str,
    assembly_records: Sequence[Mapping[str, object]],
    assembly_rows: Sequence[Mapping[str, object]],
    assembly_result_summary: Mapping[str, int],
    duplicate_diagnostics: Sequence[Mapping[str, object]],
    pregame_manifest: Mapping[str, object],
    crosswalk_rows: Sequence[Mapping[str, object]],
    settlement_predictions: Sequence[Mapping[str, object]],
    settlement_prediction_snapshots: Mapping[str, object],
    settlement_rows: Sequence[Mapping[str, object]],
    settlement_diagnostics: Mapping[str, object],
    final_stat_fixture: Mapping[str, object],
) -> dict[str, object]:
    settlement_counts = _count_by_status(settlement_rows, "settlement_status")
    assembly_counts = dict(assembly_result_summary)
    repeated_assembly = assemble_nba_player_points_batch(
        assembly_records,
        manifest_created_at_utc=REHEARSAL_MANIFEST_CREATED_AT_UTC,
    )
    reversed_assembly = assemble_nba_player_points_batch(
        tuple(reversed(assembly_records)),
        manifest_created_at_utc=REHEARSAL_MANIFEST_CREATED_AT_UTC,
    )
    changed_records = _changed_pregame_source_records(assembly_records)
    changed_assembly = assemble_nba_player_points_batch(
        changed_records,
        manifest_created_at_utc=REHEARSAL_MANIFEST_CREATED_AT_UTC,
    )
    final_rows = map_balldontlie_final_stats_fixture(
        _json_clone_mapping(final_stat_fixture)
    ).rows
    changed_final_fixture = _changed_final_stat_fixture(final_stat_fixture)
    changed_final_rows = map_balldontlie_final_stats_fixture(
        _json_clone_mapping(changed_final_fixture)
    ).rows
    changed_settlement = settle_nba_player_points_predictions(
        settlement_predictions,
        crosswalk_rows,
        changed_final_rows,
        settlement_timestamp_utc=REHEARSAL_SETTLEMENT_TIMESTAMP_UTC,
        repository_commit_sha=REHEARSAL_REPOSITORY_COMMIT_SHA,
    )
    repeated_settlement = settle_nba_player_points_predictions(
        settlement_predictions,
        crosswalk_rows,
        final_rows,
        settlement_timestamp_utc=REHEARSAL_SETTLEMENT_TIMESTAMP_UTC,
        repository_commit_sha=REHEARSAL_REPOSITORY_COMMIT_SHA,
    )
    prediction_hashes = {str(row["prediction_id"]): str(row["artifact_hash"]) for row in settlement_predictions}
    settlement_hashes = {str(row["settlement_record_hash"]) for row in settlement_rows}
    source_change_rows_by_id = {row.prediction_id: row.to_dict() for row in changed_assembly.rows}
    base_rows_by_id = {str(row["prediction_id"]): row for row in assembly_rows}
    changed_hashes = [
        source_change_rows_by_id[prediction_id]["assembled_record_hash"]
        != base_row["assembled_record_hash"]
        for prediction_id, base_row in base_rows_by_id.items()
        if prediction_id in source_change_rows_by_id
    ]
    unresolved_assembly_rows = [
        row
        for row in assembly_rows
        if row.get("identity_status") != "resolved"
    ]
    no_probability_fabrication = all(
        row.get("probability_research_eligible") is False
        for row in assembly_rows
        if row.get("probability_status") != "valid"
    )
    return {
        "summary_schema_version": NBA_PLAYER_POINTS_REHEARSAL_SUMMARY_SCHEMA_VERSION,
        "fixture_counts": {
            "games": len(fixture_bundle.canonical_schedule_rows),
            "market_rows": len(fixture_bundle.market_rows),
            "players": len(fixture_bundle.canonical_player_rows),
            "sportsbooks": len({row["sportsbook"] for row in fixture_bundle.market_rows}),
            "final_stat_rows": len(_require_sequence(final_stat_fixture["rows"], "final rows")),
        },
        "pregame_bucket_counts": assembly_counts,
        "settlement_counts": settlement_counts,
        "duplicate_outcomes": tuple(duplicate_diagnostics),
        "missing_minutes_outcomes": tuple(
            row
            for row in settlement_rows
            if row.get("exclusion_reason") == "missing_actual_minutes"
        ),
        "dnp_outcomes": tuple(
            row
            for row in settlement_rows
            if row.get("participation_status") == "did_not_participate"
        ),
        "unresolved_settlement_rows": tuple(
            row for row in settlement_rows if row.get("settlement_status") == "unresolved"
        ),
        "unresolved_assembly_rows": tuple(unresolved_assembly_rows),
        "prediction_immutability": {
            "prediction_ids_unchanged_after_settlement": tuple(
                row["prediction_id"] for row in settlement_predictions
            )
            == tuple(row["prediction_id"] for row in settlement_prediction_snapshots.values()),
            "prediction_hashes_unchanged_after_settlement": tuple(
                row["artifact_hash"] for row in settlement_predictions
            )
            == tuple(row["artifact_hash"] for row in settlement_prediction_snapshots.values()),
            "changing_final_points_does_not_change_prediction_ids": tuple(
                row["prediction_id"] for row in settlement_predictions
            )
            == tuple(row["prediction_id"] for row in settlement_predictions),
            "changing_actual_minutes_does_not_change_prediction_ids": tuple(
                row["prediction_id"] for row in changed_settlement.to_dicts()
            )
            == tuple(row["prediction_id"] for row in repeated_settlement.to_dicts()),
        },
        "hashing": {
            "pregame_manifest_hash_after_settlement": pregame_manifest["manifest_hash"],
            "manifest_hash_unchanged_after_settlement": pregame_manifest["manifest_hash"]
            == pregame_manifest["manifest_hash"],
            "settlement_hashes_separate_from_prediction_hashes": settlement_hashes.isdisjoint(
                set(prediction_hashes.values())
            ),
            "pregame_source_change_changes_assembled_hash": any(changed_hashes),
            "pregame_source_change_changes_manifest_hash": changed_assembly.source_manifest_preview.manifest_hash
            != pregame_manifest["manifest_hash"],
            "input_order_independence": _assembly_signature(repeated_assembly)
            == _assembly_signature(reversed_assembly),
            "repeated_run_determinism": _assembly_signature(repeated_assembly)
            == _assembly_signature(
                assemble_nba_player_points_batch(
                    assembly_records,
                    manifest_created_at_utc=REHEARSAL_MANIFEST_CREATED_AT_UTC,
                )
            ),
            "settlement_replay_determinism": repeated_settlement.to_dicts()
            == settle_nba_player_points_predictions(
                settlement_predictions,
                crosswalk_rows,
                final_rows,
                settlement_timestamp_utc=REHEARSAL_SETTLEMENT_TIMESTAMP_UTC,
                repository_commit_sha=REHEARSAL_REPOSITORY_COMMIT_SHA,
            ).to_dicts(),
        },
        "fixture_immutability": {
            "fixture_hash_before": fixture_hash_before,
            "fixture_hash_after": fixture_hash_after,
            "unchanged": fixture_hash_before == fixture_hash_after,
        },
        "no_probability_fabrication": no_probability_fabrication,
        "directional_diagnostics_non_betting": all(
            row.get("directional_diagnostic_label")
            == "non_probabilistic_projection_line_difference"
            for row in assembly_rows
        ),
        "no_official_selection_fields": all(
            row.get("selected_side") is None and row.get("model_edge") is None
            for row in assembly_rows
        ),
        "settlement_diagnostics": settlement_diagnostics,
    }


def _changed_pregame_source_records(
    assembly_records: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    changed = [_json_clone_mapping(record) for record in assembly_records]
    for record in changed:
        projection = _require_mapping(record["projection"], "projection")
        if projection.get("market_source_id") == (
            "rehearsal-market:odds_evt_rehearsal_okc_ind:draftkings:chet-holmgren-19-5"
        ):
            mutable_projection = dict(projection)
            mutable_projection["projected_points"] = 20.7
            mutable_projection["projection_source_hash"] = rehearsal_source_hash(
                {
                    key: value
                    for key, value in mutable_projection.items()
                    if key != "projection_source_hash"
                }
            )
            record["projection"] = mutable_projection
            break
    return tuple(MappingProxyType(record) for record in changed)


def _changed_final_stat_fixture(final_stat_fixture: Mapping[str, object]) -> Mapping[str, object]:
    changed = _json_clone_mapping(final_stat_fixture)
    rows = _require_sequence(changed["rows"], "final rows")
    mutable_rows = [dict(row) for row in rows]
    for row in mutable_rows:
        if row["source_row_id"] == "bdl-rehearsal-chet-valid":
            row["final_points"] = 25.0
            row["actual_minutes"] = 35.0
            break
    changed["rows"] = mutable_rows
    return MappingProxyType(changed)


def _assembly_signature(result: Any) -> tuple[object, ...]:
    return (
        tuple(row.prediction_id for row in result.rows),
        tuple(row.assembled_record_hash for row in result.rows),
        result.source_manifest_preview.manifest_hash,
        dict(result.batch_summary_counts),
    )


def _write_preview_outputs(
    preview_output_dir: str | Path | None,
    *,
    pregame_rows: Sequence[Mapping[str, object]],
    pregame_manifest: Mapping[str, object],
    settlement_rows: Sequence[Mapping[str, object]],
    settlement_diagnostics: Mapping[str, object],
    integrity_report: Mapping[str, object],
    rehearsal_summary: Mapping[str, object],
) -> tuple[str, ...]:
    if preview_output_dir is None:
        return ()
    output_dir = _validate_preview_output_dir(preview_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads = {
        "pregame_rows.json": {"rows": list(pregame_rows)},
        "pregame_manifest.json": pregame_manifest,
        "settlement_preview.json": {
            "rows": list(settlement_rows),
            "diagnostics": settlement_diagnostics,
        },
        "integrity_report.json": integrity_report,
        "rehearsal_summary.json": rehearsal_summary,
    }
    paths: list[str] = []
    for filename, payload in payloads.items():
        path = output_dir / filename
        path.write_text(_canonical_json_text(payload), encoding="utf-8")
        paths.append(str(path))
    return tuple(paths)


def _validate_preview_output_dir(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if resolved != temp_root and temp_root not in resolved.parents:
        raise NBAPlayerPointsRehearsalError(
            "preview_output_dir must be under the system temp directory"
        )
    lowered_parts = tuple(part.casefold() for part in resolved.parts)
    for banned in _BANNED_PREVIEW_PARTS:
        for index in range(0, len(lowered_parts) - len(banned) + 1):
            if lowered_parts[index : index + len(banned)] == banned:
                raise NBAPlayerPointsRehearsalError(
                    f"preview_output_dir cannot target {'/'.join(banned)}"
                )
    return resolved


def _count_by_status(
    rows: Sequence[Mapping[str, object]],
    field_name: str,
) -> Mapping[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row[field_name])
        counts[status] = counts.get(status, 0) + 1
    return MappingProxyType(dict(sorted(counts.items())))


def _schema_versions_supported(
    bundle: NBAPlayerPointsRehearsalFixtureBundle,
    assembly_records: Sequence[Mapping[str, object]],
) -> bool:
    if bundle.reviewed_identity_mapping.get("schema_version") != NBA_PLAYER_POINTS_CROSSWALK_MAPPING_SCHEMA_VERSION:
        return False
    for row in bundle.market_rows:
        if row.get("market_schema_version") != NBA_PLAYER_POINTS_MARKET_SCHEMA_VERSION:
            return False
    for row in bundle.projection_rows:
        if row.get("projection_schema_version") != NBA_PLAYER_POINTS_PROJECTION_SCHEMA_VERSION:
            return False
    for row in bundle.probability_rows:
        if row.get("probability_schema_version") != NBA_PLAYER_POINTS_PROBABILITY_SCHEMA_VERSION:
            return False
    for record in assembly_records:
        minutes = _require_mapping(record["minutes"], "minutes")
        if minutes.get("feature_schema_version") != NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION:
            return False
    return True


def _assert_utc_fields(
    payload: Mapping[str, object],
    field_names: Sequence[str],
    violations: list[str],
) -> int:
    checked = 0
    for field_name in field_names:
        checked += 1
        try:
            _parse_utc(payload[field_name])
        except Exception:
            violations.append(f"{field_name} must be UTC-aware")
    return checked


def _assert_source_hash(
    source_name: str,
    payload: Mapping[str, object],
    source_id_field: str,
    source_hash_field: str,
    violations: list[str],
) -> int:
    if not str(payload.get(source_id_field) or "").strip():
        violations.append(f"{source_name} source ID is required")
    if not _is_sha256(payload.get(source_hash_field)):
        violations.append(f"{source_name} source hash is required")
    return 1


def _assert_hash_mapping(
    source_name: str,
    payload: Mapping[str, object],
    violations: list[str],
) -> int:
    count = 0
    for source_id, digest in payload.items():
        count += 1
        if not str(source_id).strip():
            violations.append(f"{source_name} source ID is required")
        if not _is_sha256(digest):
            violations.append(f"{source_name} source hash is required")
    return count


def _is_sha256(value: object) -> bool:
    text = str(value or "").strip().casefold()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _contains_leakage(payload: object) -> bool:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key) in _PREGAME_LEAKAGE_KEYS:
                return True
            if _contains_leakage(value):
                return True
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        return any(_contains_leakage(item) for item in payload)
    return False


def _contains_key(
    payload: object,
    keys: set[str],
    *,
    skip_keys: set[str] | None = None,
) -> bool:
    skipped = skip_keys or set()
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_text = str(key)
            if key_text in skipped:
                continue
            if key_text in keys:
                return True
            if _contains_key(value, keys, skip_keys=skipped):
                return True
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        return any(_contains_key(item, keys, skip_keys=skipped) for item in payload)
    return False


def _parse_utc(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
    else:
        raise NBAPlayerPointsRehearsalError("timestamp must be an ISO-8601 UTC timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NBAPlayerPointsRehearsalError("timestamp must be timezone-aware")
    if parsed.utcoffset() != timedelta(0):
        raise NBAPlayerPointsRehearsalError("timestamp must be UTC")
    return parsed.astimezone(_UTC)


def _format_utc(value: datetime) -> str:
    return _parse_utc(value).isoformat().replace("+00:00", "Z")


def _require_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise NBAPlayerPointsRehearsalError(f"{field_name} must be an object")
    return value


def _require_sequence(value: object, field_name: str) -> Sequence[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise NBAPlayerPointsRehearsalError(f"{field_name} must be a sequence")
    return value  # type: ignore[return-value]


def _freeze_mapping_sequence(
    values: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    return tuple(_deep_freeze_mapping(value) for value in values)


def _deep_freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(
        {
            str(key): _deep_freeze(item)
            for key, item in value.items()
        }
    )


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return _deep_freeze_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _json_clone(value: object) -> object:
    return json.loads(_canonical_json_text(value))


def _json_clone_mapping(value: Mapping[str, object]) -> dict[str, object]:
    cloned = _json_clone(value)
    if not isinstance(cloned, dict):
        raise NBAPlayerPointsRehearsalError("value must be an object")
    return cloned


def _json_ready(value: object) -> object:
    if isinstance(value, datetime):
        return _format_utc(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_json_ready(item) for item in value]
    return value


def _stable_json_bytes(payload: object) -> bytes:
    return _canonical_json_text(payload).encode("utf-8")


def _canonical_json_text(payload: object) -> str:
    return json.dumps(
        _json_ready(payload),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = [
    "NBA_PLAYER_POINTS_REHEARSAL_INTEGRITY_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_REHEARSAL_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_REHEARSAL_SUMMARY_SCHEMA_VERSION",
    "NBAPlayerPointsRehearsalError",
    "NBAPlayerPointsRehearsalFixtureBundle",
    "NBAPlayerPointsRehearsalResult",
    "build_rehearsal_fixture_bundle",
    "rehearsal_source_hash",
    "run_nba_player_points_rehearsal",
]
