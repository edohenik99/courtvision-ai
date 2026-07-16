from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from unittest.mock import patch

import pytest

from courtvision.sports.nba.player_points_crosswalk import (
    NBA_PLAYER_POINTS_DEFAULT_EVENT_TIME_TOLERANCE,
    join_nba_player_points_crosswalk,
)
from courtvision.sports.nba.player_points_research import (
    build_research_prediction_row,
    map_final_stats_provider_fixture,
    map_the_odds_api_player_points_fixture,
)
from courtvision.sports.nba.player_points_settlement import (
    NBA_PLAYER_POINTS_PARTICIPATION_STATUSES,
    NBA_PLAYER_POINTS_SETTLEMENT_ROW_FIELDS,
    NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_SETTLEMENT_STATUSES,
    NBAPlayerPointsSettlementRow,
    NBAPlayerPointsSettlementSchemaError,
    map_api_nba_final_stats_fixture,
    map_balldontlie_final_stats_fixture,
    map_sportsdataio_final_stats_fixture,
    settle_nba_player_points_predictions,
    settlement_provider_capability_matrix,
    settlement_schema_definition,
    source_fixture_hash,
    validate_settlement_prediction_link,
    validate_settlement_rows,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "nba" / "player_points"
ODDS_FIXTURE = FIXTURE_ROOT / "the_odds_api_event_odds.json"
FINAL_STATS_FIXTURE = FIXTURE_ROOT / "final_stats.json"
SETTLEMENT_FIXTURE = FIXTURE_ROOT / "settlement_cases.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SETTLEMENT_TS = "2026-06-06T04:00:00Z"


def _load_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _source_hashes() -> dict[str, str]:
    return {
        "the_odds_api_event_odds": _sha256(ODDS_FIXTURE),
        "final_stats": _sha256(FINAL_STATS_FIXTURE),
        "settlement_cases": _sha256(SETTLEMENT_FIXTURE),
    }


def _feature_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "projected_points": 32.1,
        "projected_minutes": 36.5,
        "recent_minutes": 37.2,
        "season_minutes": 35.8,
        "points_per_minute": 0.879,
        "lineup_status": "confirmed",
        "injury_status": "active",
        "feature_timestamp_utc": "2026-06-05T18:00:00Z",
        "feature_source": "offline_feature_fixture",
    }
    payload.update(overrides)
    return payload


def _output_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "model_over_probability": 0.58,
        "model_under_probability": 0.42,
        "selected_side": "Over",
        "model_edge": 0.05619,
        "eligibility_status": "research_eligible",
        "exclusion_reason": "none",
    }
    payload.update(overrides)
    return payload


def _provenance_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "prediction_timestamp_utc": "2026-06-05T18:05:00Z",
        "feature_schema_version": "nba-player-points-feature-fixture-v1",
        "repository_commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "source_manifest_id": "nba-player-points-fixture-manifest-v1",
        "source_hashes": _source_hashes(),
    }
    payload.update(overrides)
    return payload


def _market_rows():
    return map_the_odds_api_player_points_fixture(_load_fixture(ODDS_FIXTURE)).rows


def _identity_final_rows():
    return map_final_stats_provider_fixture(_load_fixture(FINAL_STATS_FIXTURE)).rows


def _build_prediction(**overrides: object):
    params: dict[str, object] = {
        "prediction_id": "pred-nba-points-001",
        "prediction_run_id": "run-nba-points-20260605-fixture",
        "model_id": "nba-player-points-research-model-v1",
        "market": next(row for row in _market_rows() if row.side == "over"),
        "final_stats": _identity_final_rows(),
        "features": _feature_payload(),
        "outputs": _output_payload(),
        "provenance": _provenance_payload(),
    }
    params.update(overrides)
    return build_research_prediction_row(**params)


def _resolved_crosswalk_rows():
    final_stat_rows = _identity_final_rows()
    schedule_rows = [
        {
            "canonical_event_id": final_stat_rows[0].canonical_event_id,
            "operating_date": final_stat_rows[0].operating_date.isoformat(),
            "commence_time_utc": final_stat_rows[0].commence_time_utc.isoformat(),
            "home_team": final_stat_rows[0].team,
            "away_team": final_stat_rows[0].opponent,
        }
    ]
    return join_nba_player_points_crosswalk(
        _market_rows(),
        schedule_rows,
        final_stat_rows,
        event_time_tolerance=NBA_PLAYER_POINTS_DEFAULT_EVENT_TIME_TOLERANCE,
    ).rows


def _settlement_payload() -> dict[str, Any]:
    return _load_fixture(SETTLEMENT_FIXTURE)


def _mapped_rows(provider: str = "balldontlie"):
    payload = _settlement_payload()[provider]
    mapper = {
        "balldontlie": map_balldontlie_final_stats_fixture,
        "api_nba": map_api_nba_final_stats_fixture,
        "sportsdataio": map_sportsdataio_final_stats_fixture,
    }[provider]
    return mapper(payload).rows


def _case_rows(*case_ids: str, provider: str = "balldontlie"):
    rows = _mapped_rows(provider)
    wanted = set(case_ids)
    return tuple(row for row in rows if row.raw_evidence.get("case_id") in wanted)


def _settle_final_rows(final_rows, *, prediction=None):
    prediction = prediction or _build_prediction(prediction_id="pred-settlement-case")
    return settle_nba_player_points_predictions(
        [prediction],
        _resolved_crosswalk_rows(),
        final_rows,
        settlement_timestamp_utc=SETTLEMENT_TS,
    )


def _settle_cases(*case_ids: str, provider: str = "balldontlie", prediction=None):
    return _settle_final_rows(_case_rows(*case_ids, provider=provider), prediction=prediction)


def _synthetic_source_hash(source_row_id: str) -> str:
    return hashlib.sha256(source_row_id.encode("utf-8")).hexdigest()


def _exact_prediction_id_final_row(prediction, *, source_row_id: str, **overrides: object):
    prediction_payload = prediction.to_dict()
    values: dict[str, object] = {
        "prediction_id": prediction_payload["prediction_id"],
        "canonical_event_id": prediction_payload["canonical_event_id"],
        "player_id": prediction_payload["player_id"],
        "source_row_id": source_row_id,
        "source_hash": _synthetic_source_hash(source_row_id),
    }
    values.update(overrides)
    return replace(_case_rows("exact_prediction_id_match")[0], **values)


def _only_row(result):
    assert len(result.rows) == 1
    return result.rows[0]


def test_settlement_schema_validation_and_required_fields() -> None:
    schema = settlement_schema_definition()

    assert schema["schema_version"] == NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION
    assert schema["required_fields"] == list(NBA_PLAYER_POINTS_SETTLEMENT_ROW_FIELDS)
    assert "settled" in NBA_PLAYER_POINTS_SETTLEMENT_STATUSES
    assert "manual_review_required" in schema["settlement_statuses"]
    assert "zero_minutes" in NBA_PLAYER_POINTS_PARTICIPATION_STATUSES
    assert schema["matching_priority"] == [
        "prediction_id_exact",
        "canonical_event_id_plus_player_id",
        "approved_provider_event_id_mapping_plus_player_id",
        "unresolved",
    ]

    row = _only_row(_settle_cases("valid_final_points_and_minutes"))
    with pytest.raises(NBAPlayerPointsSettlementSchemaError, match="settlement_id is required"):
        replace(row, settlement_id="")


def test_provider_capability_matrix_reports_explicit_gaps() -> None:
    matrix = settlement_provider_capability_matrix()

    assert set(matrix) == {"balldontlie", "api_nba", "sportsdataio"}
    assert matrix["balldontlie"]["supports_live_calls"] is False
    assert "provider_event_id" in matrix["balldontlie"]["available_fields"]
    assert "final_points" in matrix["api_nba"]["available_fields"]
    assert "actual_minutes" in matrix["sportsdataio"]["available_fields"]
    assert "canonical_event_id" in matrix["api_nba"]["unsupported_fields"]
    assert "player_participation" in matrix["api_nba"]["unsupported_fields"]


def test_final_stat_provider_adapters_are_offline_and_preserve_missing_values() -> None:
    bdl = map_balldontlie_final_stats_fixture(_settlement_payload()["balldontlie"])
    api = map_api_nba_final_stats_fixture(_settlement_payload()["api_nba"])
    sdio = map_sportsdataio_final_stats_fixture(_settlement_payload()["sportsdataio"])

    assert len(bdl.rows) == 22
    assert len(api.rows) == 2
    assert len(sdio.rows) == 2
    valid = next(row for row in bdl.rows if row.raw_evidence["case_id"] == "valid_final_points_and_minutes")
    missing_minutes = next(row for row in bdl.rows if row.raw_evidence["case_id"] == "missing_actual_minutes")
    zero_minutes = next(row for row in bdl.rows if row.raw_evidence["case_id"] == "participated_zero_minutes")
    assert valid.actual_minutes == pytest.approx(38.75)
    assert missing_minutes.actual_minutes is None
    assert zero_minutes.actual_minutes == 0.0
    assert zero_minutes.participation_status == "zero_minutes"
    assert SHA256_RE.fullmatch(valid.source_hash)


def test_exact_prediction_id_settlement_takes_priority() -> None:
    prediction = _build_prediction(prediction_id="pred-nba-points-001")
    evidence = _case_rows("exact_prediction_id_match")[0]
    result = _settle_cases("exact_prediction_id_match", prediction=prediction)
    row = _only_row(result)

    assert evidence.player_id == prediction.to_dict()["player_id"]
    assert evidence.canonical_event_id == prediction.to_dict()["canonical_event_id"]
    assert row.settlement_status == "settled"
    assert row.final_points == 35.0
    assert row.actual_minutes == 39.0
    assert row.settlement_source_id == "bdl-exact-prediction-001"
    assert validate_settlement_prediction_link(row, prediction) is row


def test_exact_prediction_id_with_conflicting_player_id_fails_closed() -> None:
    prediction = _build_prediction(prediction_id="pred-exact-player-conflict")
    before = prediction.to_dict()
    final_row = _exact_prediction_id_final_row(
        prediction,
        source_row_id="bdl-exact-conflict-player",
        player_id="nba-player-conflicting",
    )
    result = _settle_final_rows([final_row], prediction=prediction)
    row = _only_row(result)
    diagnostic = result.diagnostics["conflicting"][0]

    assert row.settlement_status == "conflicting"
    assert row.exclusion_reason == "prediction_id_identity_conflict"
    assert row.manual_review_status == "quarantined"
    assert row.final_points is None
    assert row.actual_minutes is None
    assert row.participation_status == "unknown"
    assert result.settled_rows == ()
    assert result.quarantined_rows == (row,)
    assert row.settlement_source_id == "conflict:bdl-exact-conflict-player"
    assert diagnostic["matching_method"] == "prediction_id_identity_conflict"
    assert diagnostic["conflict_reason"] == "prediction_id_identity_conflict"
    assert diagnostic["source_row_ids"] == ["bdl-exact-conflict-player"]
    assert diagnostic["source_hashes"] == [final_row.source_hash]
    assert prediction.to_dict() == before
    assert validate_settlement_prediction_link(row, prediction) is row


def test_exact_prediction_id_with_conflicting_canonical_event_id_fails_closed() -> None:
    prediction = _build_prediction(prediction_id="pred-exact-event-conflict")
    final_row = _exact_prediction_id_final_row(
        prediction,
        source_row_id="bdl-exact-conflict-event",
        canonical_event_id="nba-2026-06-05-bos-lal",
    )
    result = _settle_final_rows([final_row], prediction=prediction)
    row = _only_row(result)
    diagnostic = result.diagnostics["conflicting"][0]

    assert row.settlement_status == "conflicting"
    assert row.exclusion_reason == "prediction_id_identity_conflict"
    assert row.final_points is None
    assert row.actual_minutes is None
    assert row.participation_status == "unknown"
    assert result.settled_rows == ()
    assert diagnostic["source_row_ids"] == ["bdl-exact-conflict-event"]
    assert diagnostic["source_hashes"] == [final_row.source_hash]


def test_exact_prediction_id_identical_replay_remains_idempotent() -> None:
    prediction = _build_prediction(prediction_id="pred-exact-idempotent")
    final_row = _exact_prediction_id_final_row(
        prediction,
        source_row_id="bdl-exact-idempotent",
    )
    result = _settle_final_rows([final_row, final_row], prediction=prediction)
    replay = _settle_final_rows([final_row, final_row], prediction=prediction)
    row = _only_row(result)

    assert row.settlement_status == "settled"
    assert row.final_points == 35.0
    assert row.actual_minutes == 39.0
    assert row.settlement_source_id == "bdl-exact-idempotent"
    assert result.to_dicts() == replay.to_dicts()
    assert result.diagnostics["duplicate_identical_replay"][0]["matching_method"] == "prediction_id_exact"


def test_canonical_event_and_player_fallback_settlement() -> None:
    prediction = _build_prediction(prediction_id="pred-canonical-fallback")
    row = _only_row(_settle_cases("canonical_event_player_fallback", prediction=prediction))

    assert row.settlement_status == "settled"
    assert row.final_points == 36.0
    assert row.actual_minutes == 37.5
    assert row.exclusion_reason == "none"


def test_approved_provider_event_mapping_fallback_settlement() -> None:
    prediction = _build_prediction(prediction_id="pred-provider-map")
    row = _only_row(
        _settle_cases(
            "provider_mapped_event_player_match",
            provider="api_nba",
            prediction=prediction,
        )
    )

    assert row.settlement_status == "settled"
    assert row.settlement_provider == "api_nba"
    assert row.final_points == 33.0
    assert row.actual_minutes == 36.5


def test_final_game_requirement_and_pending_game_handling() -> None:
    row = _only_row(_settle_cases("game_not_final"))

    assert row.settlement_status == "pending"
    assert row.game_status == "in_progress"
    assert row.game_final is False
    assert row.exclusion_reason == "game_not_final"
    assert row.manual_review_status == "required"


def test_postponed_and_cancelled_games_are_void_not_settled() -> None:
    postponed = _only_row(_settle_cases("game_postponed"))
    cancelled = _only_row(
        _settle_cases(
            "game_cancelled",
            provider="sportsdataio",
            prediction=_build_prediction(prediction_id="pred-cancelled"),
        )
    )

    assert postponed.settlement_status == "void"
    assert postponed.exclusion_reason == "game_postponed"
    assert cancelled.settlement_status == "void"
    assert cancelled.exclusion_reason == "game_cancelled"


def test_missing_actual_minutes_requires_manual_review_without_zero_fill() -> None:
    row = _only_row(_settle_cases("missing_actual_minutes"))

    assert row.settlement_status == "manual_review_required"
    assert row.exclusion_reason == "missing_actual_minutes"
    assert row.actual_minutes is None
    assert row.manual_review_status == "required"


def test_missing_final_points_requires_manual_review() -> None:
    row = _only_row(_settle_cases("missing_final_points"))

    assert row.settlement_status == "manual_review_required"
    assert row.exclusion_reason == "missing_final_points"
    assert row.final_points is None


def test_zero_minutes_and_zero_points_are_not_missing_values() -> None:
    zero_minutes = _only_row(_settle_cases("participated_zero_minutes"))
    zero_points = _only_row(_settle_cases("zero_player_points"))

    assert zero_minutes.settlement_status == "settled"
    assert zero_minutes.actual_minutes == 0.0
    assert zero_minutes.participation_status == "zero_minutes"
    assert zero_points.settlement_status == "settled"
    assert zero_points.final_points == 0.0
    assert zero_points.actual_minutes == 31.5


def test_did_not_participate_and_inactive_are_explicit_voids() -> None:
    dnp = _only_row(_settle_cases("did_not_participate"))
    inactive = _only_row(_settle_cases("inactive"))

    assert dnp.settlement_status == "void"
    assert dnp.participation_status == "did_not_participate"
    assert dnp.exclusion_reason == "did_not_participate"
    assert inactive.settlement_status == "void"
    assert inactive.participation_status == "did_not_participate"


def test_missing_player_or_event_identity_never_uses_name_only_matching() -> None:
    missing_player = _only_row(_settle_cases("missing_player_id"))
    missing_event = _only_row(_settle_cases("missing_event_id"))

    assert missing_player.settlement_status == "unresolved"
    assert missing_player.exclusion_reason == "missing_player_id"
    assert missing_event.settlement_status == "unresolved"
    assert missing_event.exclusion_reason == "missing_event_id"


def test_duplicate_identical_replay_is_idempotent() -> None:
    result = _settle_cases("duplicate_identical_settlement")
    row = _only_row(result)

    assert row.settlement_status == "settled"
    assert row.settlement_source_id == "bdl-duplicate-identical"
    assert "duplicate_identical_replay" in result.diagnostics


def test_conflicting_replay_quarantines_evidence_without_overwrite() -> None:
    result = _settle_cases("conflicting_final_points_a", "conflicting_final_points_b")
    row = _only_row(result)

    assert row.settlement_status == "conflicting"
    assert row.exclusion_reason == "conflicting_final_points"
    assert row.manual_review_status == "quarantined"
    assert row.final_points is None
    assert result.diagnostics["conflicting"][0]["source_row_ids"] == [
        "bdl-conflict-points-a",
        "bdl-conflict-points-b",
    ]


def test_conflicting_actual_minutes_quarantines_evidence() -> None:
    result = _settle_cases("conflicting_actual_minutes_a", "conflicting_actual_minutes_b")
    row = _only_row(result)

    assert row.settlement_status == "conflicting"
    assert row.exclusion_reason == "conflicting_actual_minutes"
    assert "bdl-conflict-minutes-a" in result.diagnostics["conflicting"][0]["source_row_ids"]


def test_multiple_final_stat_candidates_are_ambiguous() -> None:
    result = _settle_cases("multiple_final_stat_candidates_a", "multiple_final_stat_candidates_b")
    row = _only_row(result)

    assert row.settlement_status == "ambiguous"
    assert row.exclusion_reason == "multiple_final_stat_candidates"
    assert row.manual_review_status == "quarantined"
    assert result.diagnostics["ambiguous"][0]["source_row_ids"] == [
        "bdl-multiple-a",
        "bdl-multiple-b",
    ]


def test_ambiguous_crosswalk_identity_blocks_settlement() -> None:
    prediction = _build_prediction(prediction_id="pred-ambiguous-crosswalk")
    ambiguous_crosswalk = deepcopy(_resolved_crosswalk_rows()[0].to_dict())
    ambiguous_crosswalk["event_identity"]["event_identity_status"] = "ambiguous"
    ambiguous_crosswalk["player_identity"]["player_identity_status"] = "ambiguous"

    result = settle_nba_player_points_predictions(
        [prediction],
        [ambiguous_crosswalk],
        _case_rows("valid_final_points_and_minutes"),
        settlement_timestamp_utc=SETTLEMENT_TS,
    )
    row = _only_row(result)

    assert row.settlement_status == "unresolved"
    assert row.event_identity_status == "ambiguous"
    assert row.player_identity_status == "ambiguous"
    assert row.exclusion_reason == "identity_unresolved"


def test_source_hash_generation_and_mismatch_detection() -> None:
    payload = deepcopy(_settlement_payload()["balldontlie"])
    row_payload = payload["rows"][0]
    expected = source_fixture_hash(row_payload)
    mapped = map_balldontlie_final_stats_fixture({"provider_name": "balldontlie", "source_timestamp_utc": payload["source_timestamp_utc"], "rows": [row_payload]}).rows[0]

    assert mapped.source_hash == expected
    row_payload["source_hash"] = "0" * 64
    with pytest.raises(NBAPlayerPointsSettlementSchemaError, match="source_hash mismatch"):
        map_balldontlie_final_stats_fixture(payload)


def test_settlement_record_hash_and_collection_integrity_detection() -> None:
    row = _only_row(_settle_cases("valid_final_points_and_minutes"))

    assert SHA256_RE.fullmatch(row.settlement_record_hash)
    with pytest.raises(NBAPlayerPointsSettlementSchemaError, match="duplicate settlement_id"):
        validate_settlement_rows([row, row])
    different_settlement = replace(row, settlement_id="manual-different-settlement-id")
    with pytest.raises(NBAPlayerPointsSettlementSchemaError, match="multiple settlements"):
        validate_settlement_rows([row, different_settlement])


def test_utc_timestamp_enforcement_and_timezone_naive_rejection() -> None:
    payload = deepcopy(_settlement_payload()["balldontlie"])
    payload["rows"][0]["commence_time_utc"] = "2026-06-06T00:40:00"

    with pytest.raises(NBAPlayerPointsSettlementSchemaError, match="timezone-aware"):
        map_balldontlie_final_stats_fixture(payload)

    row = _only_row(_settle_cases("valid_final_points_and_minutes"))
    with pytest.raises(NBAPlayerPointsSettlementSchemaError, match="timezone-aware"):
        replace(row, settlement_timestamp_utc=datetime(2026, 6, 6, 4, 0))


def test_toronto_operating_date_validation() -> None:
    payload = deepcopy(_settlement_payload()["balldontlie"])
    payload["rows"][0]["operating_date"] = "2026-06-06"

    with pytest.raises(
        NBAPlayerPointsSettlementSchemaError,
        match="America/Toronto date",
    ):
        map_balldontlie_final_stats_fixture(payload)

    row = _only_row(_settle_cases("valid_final_points_and_minutes"))
    assert row.operating_date == date(2026, 6, 5)


def test_prediction_immutability_and_prediction_hash_mismatch_detection() -> None:
    prediction = _build_prediction(prediction_id="pred-immutable")
    before = prediction.to_dict()
    row = _only_row(_settle_cases("valid_final_points_and_minutes", prediction=prediction))

    assert prediction.to_dict() == before
    assert validate_settlement_prediction_link(row, prediction) is row
    mismatched = replace(row, prediction_artifact_hash="0" * 64)
    with pytest.raises(NBAPlayerPointsSettlementSchemaError, match="prediction_artifact_hash mismatch"):
        validate_settlement_prediction_link(mismatched, prediction)


def test_no_source_fixture_mutation() -> None:
    before = {path: _sha256(path) for path in (ODDS_FIXTURE, FINAL_STATS_FIXTURE, SETTLEMENT_FIXTURE)}

    _settle_cases("source_fixture_immutability")
    map_api_nba_final_stats_fixture(_settlement_payload()["api_nba"])
    map_sportsdataio_final_stats_fixture(_settlement_payload()["sportsdataio"])

    after = {path: _sha256(path) for path in (ODDS_FIXTURE, FINAL_STATS_FIXTURE, SETTLEMENT_FIXTURE)}
    assert after == before


def test_no_live_provider_calls() -> None:
    with patch("requests.Session.get", side_effect=AssertionError("live call attempted")) as mock_get:
        _settle_cases("valid_final_points_and_minutes")
        map_api_nba_final_stats_fixture(_settlement_payload()["api_nba"])

    assert mock_get.call_count == 0


def test_no_production_output_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    before = _snapshot(tmp_path)

    _settle_cases("valid_final_points_and_minutes")

    assert _snapshot(tmp_path) == before
    assert not (tmp_path / "outputs").exists()
    assert not (tmp_path / "test_outputs").exists()
    assert not (tmp_path / "data" / "history").exists()


def test_backward_compatibility_with_research_and_crosswalk_contracts() -> None:
    prediction = _build_prediction(prediction_id="pred-backward-compatible")
    crosswalk = _resolved_crosswalk_rows()
    result = settle_nba_player_points_predictions(
        [prediction],
        crosswalk,
        _case_rows("sportsdataio_valid", provider="sportsdataio"),
        settlement_timestamp_utc=SETTLEMENT_TS,
    )
    row = _only_row(result)

    assert row.settlement_status == "settled"
    assert row.canonical_event_id == "nba-2026-06-05-okc-ind"
    assert row.player_id == "nba-player-1628983"
    assert row.research_label == "research_only_not_for_betting"
