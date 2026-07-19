from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from courtvision.sports.nba.player_points_crosswalk import (
    NBA_PLAYER_POINTS_CROSSWALK_MAPPING_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_DEFAULT_EVENT_TIME_TOLERANCE,
    NBA_PLAYER_POINTS_MAX_EVENT_TIME_TOLERANCE,
    NBAPlayerPointsEventIdentity,
    NBAPlayerPointsPlayerIdentity,
    event_identity_schema,
    join_nba_player_points_crosswalk,
    load_reviewed_identity_mapping_artifact,
    mapping_artifact_schema,
    normalize_nba_team,
    player_identity_schema,
    resolve_nba_event_identity,
    resolve_nba_player_identity,
    validate_nba_team_normalization_table,
)
from courtvision.sports.nba.player_points_research import (
    NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
    NBAPlayerPointsResearchSchemaError,
    map_final_stats_provider_fixture,
    map_the_odds_api_player_points_fixture,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "nba" / "player_points"
CROSSWALK_FIXTURE = FIXTURE_ROOT / "crosswalk_cases.json"
ODDS_FIXTURE = FIXTURE_ROOT / "the_odds_api_event_odds.json"
FINAL_STATS_FIXTURE = FIXTURE_ROOT / "final_stats.json"


def _load_fixture(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _crosswalk() -> dict[str, object]:
    return _load_fixture(CROSSWALK_FIXTURE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(root: Path) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        sorted(
            (str(path.relative_to(root)), _sha256(path), path.stat().st_size)
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def _odds(key: str) -> dict[str, object]:
    return deepcopy(_crosswalk()["odds_rows"][key])


def _schedule(*keys: str) -> list[dict[str, object]]:
    rows = _crosswalk()["schedule_rows"]
    return [deepcopy(rows[key]) for key in keys]


def _players(*keys: str) -> list[dict[str, object]]:
    rows = _crosswalk()["canonical_player_rows"]
    return [deepcopy(rows[key]) for key in keys]


def _reviewed_mapping() -> dict[str, object]:
    return deepcopy(_crosswalk()["reviewed_mapping"])


def test_event_contract_validation() -> None:
    schema = event_identity_schema()

    assert "provider_event_id" in schema["fields"]
    assert "canonical_event_id" in schema["fields"]
    assert "quarantined" in schema["statuses"]
    assert schema["default_event_time_tolerance_seconds"] == 900
    assert schema["max_event_time_tolerance_seconds"] == 1800

    with pytest.raises(
        NBAPlayerPointsResearchSchemaError,
        match="event_status and event_identity_status must match",
    ):
        NBAPlayerPointsEventIdentity(
            provider_event_id="odds_evt",
            canonical_event_id=None,
            provider_name="the_odds_api_nba",
            operating_date=date(2026, 6, 5),
            commence_time_utc=datetime(2026, 6, 6, 0, 40, tzinfo=timezone.utc),
            home_team="OKC",
            away_team="IND",
            canonical_home_team="OKC",
            canonical_away_team="IND",
            event_status="resolved",
            event_identity_status="unresolved",
            event_identity_method="contract_test",
            event_identity_confidence=1.0,
            event_conflict_reason="none",
            source_timestamp_utc=datetime(2026, 6, 5, 18, 2, tzinfo=timezone.utc),
            mapping_version="fixture",
        )


def test_player_contract_validation() -> None:
    schema = player_identity_schema()

    assert "provider_player_name" in schema["fields"]
    assert "player_id" in schema["fields"]
    assert schema["matching_priority"][0] == "provider_player_id_exact"

    with pytest.raises(NBAPlayerPointsResearchSchemaError, match="unsupported player_identity_status"):
        NBAPlayerPointsPlayerIdentity(
            provider_player_name="Shai Gilgeous-Alexander",
            normalized_player_name="shai gilgeous alexander",
            provider_player_id=None,
            player_id="nba-player-1628983",
            canonical_player_name="Shai Gilgeous-Alexander",
            team="OKC",
            canonical_team="OKC",
            player_identity_status="guessed",
            player_identity_method="contract_test",
            player_identity_confidence=1.0,
            player_conflict_reason="none",
            identity_source="fixture",
            mapping_version="fixture",
        )


def test_team_normalization_is_explicit() -> None:
    table = validate_nba_team_normalization_table()

    assert table[("generic", "okc")] == "OKC"
    assert normalize_nba_team("Oklahoma City Thunder").canonical_team == "OKC"
    assert normalize_nba_team("PHO").canonical_team == "PHX"
    unknown = normalize_nba_team("Oklahoma City")
    assert unknown.team_identity_status == "unresolved"
    assert unknown.team_conflict_reason == "unknown_team_alias"


def test_exact_canonical_event_id_match() -> None:
    identity = resolve_nba_event_identity(_odds("canonical_event_id"), _schedule("okc_ind"))

    assert identity.event_identity_status == "resolved"
    assert identity.event_identity_method == "canonical_event_id_exact"
    assert identity.canonical_event_id == "nba-2026-06-05-okc-ind"


def test_exact_event_team_date_time_match() -> None:
    identity = resolve_nba_event_identity(_odds("exact"), _schedule("okc_ind"))

    assert identity.event_identity_status == "resolved"
    assert identity.event_identity_method == "team_date_time"
    assert identity.canonical_home_team == "OKC"
    assert identity.canonical_away_team == "IND"


def test_event_tolerance_boundary_and_configurable_limit() -> None:
    boundary = resolve_nba_event_identity(_odds("boundary_tolerance"), _schedule("okc_ind"))
    outside = resolve_nba_event_identity(_odds("outside_tolerance"), _schedule("okc_ind"))
    custom = resolve_nba_event_identity(
        _odds("within_tolerance"),
        _schedule("okc_ind"),
        event_time_tolerance=timedelta(minutes=5),
    )

    assert boundary.event_identity_status == "resolved"
    assert outside.event_identity_status == "conflicting"
    assert outside.event_conflict_reason == "commence_time_mismatch"
    assert custom.event_identity_status == "conflicting"

    with pytest.raises(
        NBAPlayerPointsResearchSchemaError,
        match="event_time_tolerance exceeds",
    ):
        resolve_nba_event_identity(
            _odds("exact"),
            _schedule("okc_ind"),
            event_time_tolerance=NBA_PLAYER_POINTS_MAX_EVENT_TIME_TOLERANCE + timedelta(seconds=1),
        )


def test_ambiguous_event_handling_never_selects_closest() -> None:
    identity = resolve_nba_event_identity(
        _odds("ambiguous_event"),
        _schedule("bos_lal_a", "bos_lal_b"),
    )

    assert identity.event_identity_status == "ambiguous"
    assert identity.event_conflict_reason == "multiple_candidate_events"
    assert identity.canonical_event_id is None


def test_reversed_event_teams_are_quarantined_without_reviewed_rule() -> None:
    identity = resolve_nba_event_identity(_odds("reversed_teams"), _schedule("okc_ind"))

    assert identity.event_identity_status == "quarantined"
    assert identity.event_conflict_reason == "reversed_teams"


def test_unknown_team_alias_keeps_event_unresolved() -> None:
    identity = resolve_nba_event_identity(_odds("unknown_team_alias"), _schedule("okc_ind"))

    assert identity.event_identity_status == "unresolved"
    assert identity.event_conflict_reason == "unknown_team_alias"


def test_reviewed_event_mapping_and_reversed_rule() -> None:
    reviewed = resolve_nba_event_identity(
        _odds("reviewed_event"),
        _schedule("okc_ind"),
        reviewed_mapping=_reviewed_mapping(),
    )
    reversed_reviewed = resolve_nba_event_identity(
        _odds("reviewed_reversed_event"),
        _schedule("okc_ind"),
        reviewed_mapping=_reviewed_mapping(),
    )

    assert reviewed.event_identity_status == "resolved"
    assert reviewed.event_identity_method == "reviewed_event_mapping"
    assert reversed_reviewed.event_identity_status == "resolved"
    assert reversed_reviewed.event_identity_method == "reviewed_event_mapping_reversed"


def test_timezone_naive_event_commence_time_is_rejected() -> None:
    row = _odds("exact")
    row["commence_time_utc"] = "2026-06-06T00:40:00"

    with pytest.raises(NBAPlayerPointsResearchSchemaError, match="timezone-aware"):
        resolve_nba_event_identity(row, _schedule("okc_ind"))


def test_exact_player_id_matching() -> None:
    identity = resolve_nba_player_identity(_odds("exact"), _players("sga"))

    assert identity.player_identity_status == "resolved"
    assert identity.player_identity_method == "provider_player_id_exact"
    assert identity.player_id == "nba-player-1628983"


def test_exact_normalized_player_name_plus_team_matching() -> None:
    identity = resolve_nba_player_identity(_odds("name_team_match"), _players("sga"))

    assert identity.player_identity_status == "resolved"
    assert identity.player_identity_method == "normalized_full_name_plus_team"
    assert identity.normalized_player_name == "shai gilgeous alexander"


def test_player_team_compatibility_is_required() -> None:
    identity = resolve_nba_player_identity(_odds("incompatible_team"), _players("sga"))

    assert identity.player_identity_status == "conflicting"
    assert identity.player_conflict_reason == "team_mismatch"


def test_approved_alias_mapping_is_auditable() -> None:
    identity = resolve_nba_player_identity(
        _odds("alias_player"),
        _players("sga"),
        reviewed_mapping=_reviewed_mapping(),
    )

    assert identity.player_identity_status == "resolved"
    assert identity.player_identity_method == "reviewed_alias_mapping"
    assert identity.identity_source == "fixture_review"
    assert identity.player_id == "nba-player-1628983"


def test_ambiguous_duplicate_player_name() -> None:
    identity = resolve_nba_player_identity(
        _odds("ambiguous_player"),
        _players("alex_smith_one", "alex_smith_two"),
    )

    assert identity.player_identity_status == "ambiguous"
    assert identity.player_conflict_reason == "multiple_candidate_players"


def test_missing_player_id_and_unresolved_name() -> None:
    unresolved = resolve_nba_player_identity(_odds("unresolved_player"), _players("sga"))
    missing = resolve_nba_player_identity(_odds("missing_player_name"), _players("sga"))

    assert unresolved.player_identity_status == "unresolved"
    assert unresolved.player_conflict_reason == "no_matching_player"
    assert missing.player_identity_status == "unresolved"
    assert missing.player_conflict_reason == "missing_required_identity_field"


def test_mapping_artifact_version_and_review_metadata() -> None:
    schema = mapping_artifact_schema()
    artifact = load_reviewed_identity_mapping_artifact(_reviewed_mapping())

    assert schema["schema_version"] == NBA_PLAYER_POINTS_CROSSWALK_MAPPING_SCHEMA_VERSION
    assert artifact.mapping_version == "nba-crosswalk-map-fixture-v1"
    assert artifact.event_mappings[0].mapping_source == "fixture_review"
    assert artifact.event_mappings[0].review_status == "approved"
    assert artifact.event_mappings[0].reviewer == "research-fixture"
    assert artifact.player_mappings[0].review_reference == "fixture:player:id"

    invalid = _reviewed_mapping()
    invalid["schema_version"] = "nba-player-points-crosswalk-mapping-v2"
    with pytest.raises(NBAPlayerPointsResearchSchemaError, match="unsupported mapping schema_version"):
        load_reviewed_identity_mapping_artifact(invalid)


def test_duplicate_mapping_detection_and_no_silent_overwrite() -> None:
    fixture = _crosswalk()

    with pytest.raises(NBAPlayerPointsResearchSchemaError, match="duplicate player mapping key"):
        load_reviewed_identity_mapping_artifact(fixture["duplicate_player_mapping"])


def test_conflicting_reviewed_mapping_detection() -> None:
    fixture = _crosswalk()

    with pytest.raises(
        NBAPlayerPointsResearchSchemaError,
        match="one provider player identity maps to multiple canonical player identities",
    ):
        load_reviewed_identity_mapping_artifact(fixture["conflicting_player_mapping"])

    with pytest.raises(
        NBAPlayerPointsResearchSchemaError,
        match="one provider event identity maps to multiple canonical event identities",
    ):
        load_reviewed_identity_mapping_artifact(fixture["conflicting_event_mapping"])


def test_incompatible_provider_identities_to_one_canonical_identity_are_rejected() -> None:
    payload = _reviewed_mapping()
    payload["player_mappings"] = [
        {
            "provider_name": "the_odds_api_nba",
            "provider_player_id": "toa-one",
            "player_id": "nba-player-1628983",
            "canonical_player_name": "Shai Gilgeous-Alexander",
            "canonical_team": "OKC",
            "mapping_type": "identity",
            "mapping_source": "fixture_review",
            "reviewed_at": "2026-06-05T19:30:00Z",
            "review_status": "approved"
        },
        {
            "provider_name": "the_odds_api_nba",
            "provider_player_id": "toa-two",
            "player_id": "nba-player-1628983",
            "canonical_player_name": "Different Name",
            "canonical_team": "OKC",
            "mapping_type": "identity",
            "mapping_source": "fixture_review",
            "reviewed_at": "2026-06-05T19:31:00Z",
            "review_status": "approved"
        }
    ]

    with pytest.raises(
        NBAPlayerPointsResearchSchemaError,
        match="incompatible provider players map to one canonical player identity",
    ):
        load_reviewed_identity_mapping_artifact(payload)


def test_eligible_row_creation_and_multiple_sportsbook_rows() -> None:
    result = join_nba_player_points_crosswalk(
        [_odds("exact"), _odds("multi_sportsbook_over"), _odds("multi_sportsbook_under")],
        _schedule("okc_ind"),
        _players("sga"),
    )

    assert len(result.rows) == 3
    assert len(result.eligible_rows) == 3
    assert {row.original_odds_row["sportsbook"] for row in result.rows} == {"DraftKings", "FanDuel"}
    assert {row.canonical_event_id for row in result.rows} == {"nba-2026-06-05-okc-ind"}
    assert {row.canonical_player_id for row in result.rows} == {"nba-player-1628983"}


def test_excluded_rows_are_preserved_with_reasons() -> None:
    result = join_nba_player_points_crosswalk(
        [
            _odds("exact"),
            _odds("outside_tolerance"),
            _odds("ambiguous_player"),
            _odds("missing_player_name"),
        ],
        _schedule("okc_ind"),
        _players("sga", "alex_smith_one", "alex_smith_two"),
    )

    assert len(result.rows) == 4
    assert len(result.eligible_rows) == 1
    assert len(result.excluded_rows) == 3
    assert [row.exclusion_reason for row in result.rows] == [
        "none",
        "commence_time_mismatch",
        "player_ambiguous",
        "missing_required_identity_field",
    ]
    assert result.rows[1].canonical_event_id is None
    assert result.rows[2].canonical_player_id is None


def test_deterministic_repeated_join_results() -> None:
    rows = [_odds("exact"), _odds("alias_player"), _odds("unresolved_player")]
    first = join_nba_player_points_crosswalk(
        rows,
        _schedule("okc_ind"),
        _players("sga"),
        reviewed_player_mapping=_reviewed_mapping(),
    )
    second = join_nba_player_points_crosswalk(
        rows,
        _schedule("okc_ind"),
        _players("sga"),
        reviewed_player_mapping=_reviewed_mapping(),
    )

    assert first.to_dicts() == second.to_dicts()


def test_no_source_fixture_mutation() -> None:
    before = {path: _sha256(path) for path in (CROSSWALK_FIXTURE, ODDS_FIXTURE, FINAL_STATS_FIXTURE)}

    join_nba_player_points_crosswalk(
        [_odds("exact"), _odds("alias_player")],
        _schedule("okc_ind"),
        _players("sga"),
        reviewed_player_mapping=_reviewed_mapping(),
    )
    load_reviewed_identity_mapping_artifact(_reviewed_mapping())

    after = {path: _sha256(path) for path in (CROSSWALK_FIXTURE, ODDS_FIXTURE, FINAL_STATS_FIXTURE)}
    assert after == before


def test_no_live_provider_calls() -> None:
    with patch("requests.Session.get", side_effect=AssertionError("live call attempted")) as mock_get:
        join_nba_player_points_crosswalk(
            [_odds("exact")],
            _schedule("okc_ind"),
            _players("sga"),
        )

    assert mock_get.call_count == 0


def test_no_production_output_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    before = _snapshot(tmp_path)

    join_nba_player_points_crosswalk(
        [_odds("exact"), _odds("outside_tolerance")],
        _schedule("okc_ind"),
        _players("sga"),
    )

    assert _snapshot(tmp_path) == before
    assert not (tmp_path / "outputs").exists()
    assert not (tmp_path / "test_outputs").exists()
    assert not (tmp_path / "data" / "history").exists()


def test_backward_compatibility_with_existing_nba_research_row_contract() -> None:
    market_rows = map_the_odds_api_player_points_fixture(_load_fixture(ODDS_FIXTURE)).rows
    final_stat_rows = map_final_stats_provider_fixture(_load_fixture(FINAL_STATS_FIXTURE)).rows
    schedule_rows = [
        {
            "canonical_event_id": final_stat_rows[0].canonical_event_id,
            "operating_date": final_stat_rows[0].operating_date.isoformat(),
            "commence_time_utc": final_stat_rows[0].commence_time_utc.isoformat(),
            "home_team": final_stat_rows[0].team,
            "away_team": final_stat_rows[0].opponent,
        }
    ]

    result = join_nba_player_points_crosswalk(
        market_rows,
        schedule_rows,
        final_stat_rows,
        event_time_tolerance=NBA_PLAYER_POINTS_DEFAULT_EVENT_TIME_TOLERANCE,
    )

    assert len(result.rows) == len(market_rows)
    assert len(result.eligible_rows) == len(market_rows)
    assert {row.canonical_event_id for row in result.rows} == {"nba-2026-06-05-okc-ind"}
    assert {row.canonical_player_id for row in result.rows} == {"nba-player-1628983"}
    assert all(
        row.event_identity.operating_date.isoformat() == "2026-06-05"
        for row in result.rows
    )
    assert all(
        row.event_identity.to_dict()["provider_name"] == "the_odds_api_nba"
        for row in result.rows
    )
    assert NBA_PLAYER_POINTS_OPERATING_TIMEZONE == "America/Toronto"
