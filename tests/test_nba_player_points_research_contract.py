from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from unittest.mock import patch

import pytest

from courtvision.sports.nba.player_points_research import (
    NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL,
    NBA_PLAYER_POINTS_RESEARCH_ROW_FIELDS,
    NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION,
    NBAPlayerPointsResearchSchemaError,
    build_prediction_features,
    build_research_prediction_row,
    map_final_stats_provider_fixture,
    map_the_odds_api_player_points_fixture,
    provider_capability_matrix,
    resolve_final_stat_for_market,
    schema_definition,
    validate_prediction_rows,
    validate_schema_version,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "nba" / "player_points"
ODDS_FIXTURE = FIXTURE_ROOT / "the_odds_api_event_odds.json"
FINAL_STATS_FIXTURE = FIXTURE_ROOT / "final_stats.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _load_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_hashes() -> dict[str, str]:
    return {
        "the_odds_api_event_odds": _sha256(ODDS_FIXTURE),
        "final_stats": _sha256(FINAL_STATS_FIXTURE),
    }


def _market_rows():
    return map_the_odds_api_player_points_fixture(_load_fixture(ODDS_FIXTURE)).rows


def _final_stat_rows():
    return map_final_stats_provider_fixture(_load_fixture(FINAL_STATS_FIXTURE)).rows


def _over_market():
    return next(row for row in _market_rows() if row.side == "over")


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


def _build_valid_row(**overrides: object):
    params: dict[str, object] = {
        "prediction_id": "pred-nba-points-001",
        "prediction_run_id": "run-nba-points-20260605-fixture",
        "model_id": "nba-player-points-research-model-v1",
        "market": _over_market(),
        "final_stats": _final_stat_rows(),
        "features": _feature_payload(),
        "outputs": _output_payload(),
        "provenance": _provenance_payload(),
    }
    params.update(overrides)
    return build_research_prediction_row(**params)


def _snapshot(root: Path) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        sorted(
            (str(path.relative_to(root)), _sha256(path), path.stat().st_size)
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def test_complete_valid_row_construction() -> None:
    row = _build_valid_row()
    payload = row.to_dict()

    assert set(payload) == set(NBA_PLAYER_POINTS_RESEARCH_ROW_FIELDS)
    assert payload["schema_version"] == NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION
    assert payload["prediction_id"] == "pred-nba-points-001"
    assert payload["provider_event_id"] == "odds_evt_20260605_okc_ind"
    assert payload["canonical_event_id"] == "nba-2026-06-05-okc-ind"
    assert payload["operating_date"] == "2026-06-05"
    assert payload["operating_timezone"] == "America/Toronto"
    assert payload["commence_time_utc"] == "2026-06-06T00:40:00Z"
    assert payload["player_id"] == "nba-player-1628983"
    assert payload["normalized_player_name"] == "shai gilgeous alexander"
    assert payload["decimal_odds"] == pytest.approx(1.909091)
    assert payload["implied_probability"] == pytest.approx(0.52381)
    assert payload["market_timestamp_utc"] == "2026-06-05T18:02:00Z"
    assert payload["feature_timestamp_utc"] == "2026-06-05T18:00:00Z"
    assert payload["prediction_timestamp_utc"] == "2026-06-05T18:05:00Z"
    assert payload["selected_side"] == "over"
    assert payload["research_only_label"] == NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL
    assert payload["research_only"] is True
    assert SHA256_RE.fullmatch(str(payload["artifact_hash"]))
    assert validate_prediction_rows([row]) == (row,)


def test_missing_player_id_is_rejected() -> None:
    payload = _load_fixture(FINAL_STATS_FIXTURE)
    payload["games"][0]["players"][0]["player_id"] = ""

    with pytest.raises(NBAPlayerPointsResearchSchemaError, match="player_id is required"):
        map_final_stats_provider_fixture(payload)


def test_missing_event_id_is_rejected() -> None:
    payload = _load_fixture(ODDS_FIXTURE)
    payload.pop("id")

    with pytest.raises(NBAPlayerPointsResearchSchemaError, match="provider_event_id is required"):
        map_the_odds_api_player_points_fixture(payload)


def test_ambiguous_identity_is_rejected() -> None:
    final_stats_payload = _load_fixture(FINAL_STATS_FIXTURE)
    duplicate = deepcopy(final_stats_payload["games"][0]["players"][0])
    duplicate["player_id"] = "nba-player-ambiguous"
    final_stats_payload["games"][0]["players"].append(duplicate)
    stats_rows = map_final_stats_provider_fixture(final_stats_payload).rows

    with pytest.raises(NBAPlayerPointsResearchSchemaError, match="ambiguous identity"):
        resolve_final_stat_for_market(_over_market(), stats_rows)


def test_invalid_timestamp_is_rejected() -> None:
    payload = _load_fixture(ODDS_FIXTURE)
    payload["commence_time"] = "not-a-timestamp"

    with pytest.raises(
        NBAPlayerPointsResearchSchemaError,
        match="commence_time_utc must be an ISO-8601 UTC timestamp",
    ):
        map_the_odds_api_player_points_fixture(payload)


def test_timezone_naive_timestamp_is_rejected() -> None:
    payload = _load_fixture(ODDS_FIXTURE)
    payload["commence_time"] = "2026-06-06T00:40:00"

    with pytest.raises(
        NBAPlayerPointsResearchSchemaError,
        match="commence_time_utc must be timezone-aware",
    ):
        map_the_odds_api_player_points_fixture(payload)


def test_toronto_operating_date_conversion() -> None:
    market = _over_market()

    assert market.commence_time_utc.isoformat() == "2026-06-06T00:40:00+00:00"
    assert market.operating_date == date(2026, 6, 5)
    assert market.to_dict()["operating_date"] == "2026-06-05"


def test_missing_projected_minutes_is_rejected_without_min_avg_alias() -> None:
    payload = _feature_payload(min_avg=36.5)
    payload.pop("projected_minutes")

    with pytest.raises(
        NBAPlayerPointsResearchSchemaError,
        match="min_avg is not accepted as projected_minutes",
    ):
        build_prediction_features(payload)


def test_provider_capability_reporting() -> None:
    matrix = provider_capability_matrix()

    odds = matrix["the_odds_api_nba"]
    assert odds["supports_live_calls"] is False
    assert "line" in odds["available_fields"]
    assert "american_odds" in odds["available_fields"]
    assert "player_id" in odds["unsupported_fields"]
    assert "final_points" in odds["unsupported_fields"]
    assert "actual_minutes" in odds["unsupported_fields"]

    final_stats = matrix["nba_final_stats_fixture"]
    assert final_stats["supports_live_calls"] is False
    assert "player_id" in final_stats["available_fields"]
    assert "final_points" in final_stats["available_fields"]
    assert "actual_minutes" in final_stats["available_fields"]
    assert "sportsbook" in final_stats["unsupported_fields"]
    assert "line" in final_stats["unsupported_fields"]


def test_the_odds_api_fixture_mapping_exposes_market_data_and_explicit_gaps() -> None:
    result = map_the_odds_api_player_points_fixture(_load_fixture(ODDS_FIXTURE))

    assert result.provider.provider_name == "the_odds_api_nba"
    assert result.warnings == ()
    assert len(result.rows) == 2
    row = result.rows[0]
    row_payload = row.to_dict()
    assert row_payload["provider_event_id"] == "odds_evt_20260605_okc_ind"
    assert row_payload["sportsbook"] == "DraftKings"
    assert row_payload["market"] == "player_points"
    assert row_payload["side"] == "over"
    assert row_payload["line"] == 31.5
    assert row_payload["american_odds"] == -110
    assert row_payload["decimal_odds"] == pytest.approx(1.909091)
    assert row_payload["implied_probability"] == pytest.approx(0.52381)
    assert row_payload["market_timestamp_utc"] == "2026-06-05T18:02:00Z"
    assert row_payload["unsupported_fields"]["player_id"]
    assert row_payload["unsupported_fields"]["final_points"]
    assert row_payload["unsupported_fields"]["actual_minutes"]


def test_final_stat_provider_mapping_exposes_results_and_explicit_market_gaps() -> None:
    result = map_final_stats_provider_fixture(_load_fixture(FINAL_STATS_FIXTURE))

    assert result.provider.provider_name == "nba_final_stats_fixture"
    assert result.warnings == ()
    assert len(result.rows) == 1
    row_payload = result.rows[0].to_dict()
    assert row_payload["provider_event_id"] == "stats_game_10403"
    assert row_payload["canonical_event_id"] == "nba-2026-06-05-okc-ind"
    assert row_payload["player_id"] == "nba-player-1628983"
    assert row_payload["final_points"] == 34.0
    assert row_payload["actual_minutes"] == 38.75
    assert row_payload["unsupported_fields"]["sportsbook"]
    assert row_payload["unsupported_fields"]["line"]
    assert row_payload["unsupported_fields"]["market_timestamp_utc"]


def test_field_normalization() -> None:
    payload = _load_fixture(ODDS_FIXTURE)
    market = payload["bookmakers"][0]["markets"][0]
    market["key"] = " Player Points "
    outcome = market["outcomes"][0]
    outcome["name"] = " OVER "
    outcome["description"] = "  Shai   Gilgeous-Alexander  "
    outcome["team"] = " okc "
    outcome["opponent"] = " ind "

    mapped = map_the_odds_api_player_points_fixture(payload).rows[0]
    row = build_research_prediction_row(
        prediction_id="pred-normalized-001",
        prediction_run_id="run-normalized-001",
        model_id="nba-player-points-research-model-v1",
        market=mapped,
        final_stats=_final_stat_rows(),
        features=_feature_payload(),
        outputs=_output_payload(selected_side=" UNDER "),
        provenance=_provenance_payload(),
    )

    assert mapped.market == "player_points"
    assert mapped.side == "over"
    assert mapped.team == "OKC"
    assert mapped.opponent == "IND"
    assert mapped.normalized_player_name == "shai gilgeous alexander"
    assert row.selected_side == "under"


def test_schema_version_validation() -> None:
    definition = schema_definition()
    assert definition["schema_version"] == NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION
    assert "prediction_id" in definition["required_fields"]
    assert "commence_time_utc" in definition["utc_timestamp_fields"]

    with pytest.raises(NBAPlayerPointsResearchSchemaError, match="unsupported schema_version"):
        validate_schema_version("nba-player-points-research-v2")

    with pytest.raises(NBAPlayerPointsResearchSchemaError, match="unsupported schema_version"):
        _build_valid_row(schema_version="nba-player-points-research-v2")


def test_duplicate_prediction_identity_is_rejected() -> None:
    first = _build_valid_row()
    second = _build_valid_row()

    with pytest.raises(NBAPlayerPointsResearchSchemaError, match="duplicate prediction identity"):
        validate_prediction_rows([first, second])


def test_no_production_output_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    before = _snapshot(tmp_path)

    _build_valid_row()

    assert _snapshot(tmp_path) == before
    assert not (tmp_path / "outputs").exists()
    assert not (tmp_path / "test_outputs").exists()
    assert not (tmp_path / "data" / "history").exists()


def test_no_live_provider_calls() -> None:
    with patch("requests.Session.get", side_effect=AssertionError("live call attempted")) as mock_get:
        _build_valid_row()

    assert mock_get.call_count == 0


def test_no_mutation_of_source_fixtures() -> None:
    before = {path: _sha256(path) for path in (ODDS_FIXTURE, FINAL_STATS_FIXTURE)}

    map_the_odds_api_player_points_fixture(_load_fixture(ODDS_FIXTURE))
    map_final_stats_provider_fixture(_load_fixture(FINAL_STATS_FIXTURE))
    _build_valid_row()

    after = {path: _sha256(path) for path in (ODDS_FIXTURE, FINAL_STATS_FIXTURE)}
    assert after == before
