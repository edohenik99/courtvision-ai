from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from unittest.mock import patch

import pytest

from courtvision.sports.nba import player_points_source_adapters as adapters
from courtvision.sports.nba.player_minutes_research import (
    map_minutes_feature_case_fixture,
)
from courtvision.sports.nba.player_points_assembly import (
    assemble_nba_player_points_batch,
)
from courtvision.sports.nba.player_points_closing import (
    NBAPlayerPointsClosingWriterConfig,
    verify_nba_player_points_closing_evidence,
    write_nba_player_points_closing_evidence,
)
from courtvision.sports.nba.player_points_crosswalk import (
    join_nba_player_points_crosswalk,
)
from courtvision.sports.nba.player_points_evidence import (
    NBAPlayerPointsEvidenceWriterConfig,
    verify_nba_player_points_evidence,
    write_nba_player_points_evidence,
)
from courtvision.sports.nba.player_points_settlement import (
    settle_nba_player_points_predictions,
)
from courtvision.sports.nba.player_points_settlement_evidence import (
    NBAPlayerPointsSettlementEvidenceWriterConfig,
    verify_nba_player_points_settlement_evidence,
    write_nba_player_points_settlement_evidence,
)
from courtvision.sports.nba.player_points_source_adapters import (
    NBA_PLAYER_POINTS_CLOSING_ODDS_FIXTURE_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_FINAL_STATS_FIXTURE_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_MINUTES_INPUT_FIXTURE_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_PREGAME_ODDS_FIXTURE_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_SCHEDULE_IDENTITY_FIXTURE_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_SOURCE_ADAPTER_SCHEMA_VERSION,
    NBAPlayerPointsSourceAdapterError,
    normalize_closing_player_points_odds,
    normalize_final_stat_sources,
    normalize_minutes_feature_inputs,
    normalize_pregame_player_points_odds,
    normalize_schedule_identity_sources,
    source_fixture_hash,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "nba" / "player_points_source_adapters"
PROVIDER_SHAPES_FIXTURE = FIXTURE_ROOT / "provider_shapes.json"
SOURCE_ADAPTER_MODULE = (
    Path(__file__).resolve().parents[1]
    / "courtvision"
    / "sports"
    / "nba"
    / "player_points_source_adapters.py"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"


def _load_fixture() -> dict[str, Any]:
    return json.loads(PROVIDER_SHAPES_FIXTURE.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(root: Path) -> tuple[tuple[str, str, int], ...]:
    if not root.exists():
        return ()
    return tuple(
        sorted(
            (str(path.relative_to(root)), _sha256(path), path.stat().st_size)
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def _source_record(
    *,
    provider: str,
    source_type: str,
    source_id: str,
    source_schema_version: str = "fixture-schema-v1",
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "provider": provider,
        "source_type": source_type,
        "source_id": source_id,
        "source_schema_version": source_schema_version,
        "source_hash": source_fixture_hash(payload or {"source_id": source_id}),
    }


def _represented_count(result) -> int:
    return (
        len(result.normalized_records)
        + len(result.invalid_records)
        + len(result.unresolved_records)
        + len(result.ambiguous_records)
        + len(result.quarantined_records)
        + len(result.conflicting_records)
        + len(result.duplicate_diagnostics)
    )


def _basic_prediction_references() -> dict[str, dict[str, object]]:
    return {
        "sga_draftkings": {
            "prediction_reference": {
                "prediction_id": "pred-adapter-fixture-sga",
                "prediction_run_id": "run-adapter-fixture-sga",
                "prediction_evidence_segment": "ledgers/segments/2026-06-05/run/prediction_ledger.jsonl",
                "prediction_record_hash": "0" * 64,
            },
            "canonical_event_id": "nba-2026-06-05-okc-ind",
            "player_id": "nba-player-1628983",
            "operating_date": "2026-06-05",
            "commence_time_utc": "2026-06-06T00:40:00Z",
        }
    }


def _one_pregame_outcome(payload: dict[str, Any]) -> dict[str, Any]:
    return payload["events"][0]["bookmakers"][0]["markets"][0]["outcomes"][0]


def _one_pregame_market(payload: dict[str, Any]) -> dict[str, Any]:
    return payload["events"][0]["bookmakers"][0]["markets"][0]


def _build_adapter_chain() -> dict[str, Any]:
    fixture = _load_fixture()
    pregame = normalize_pregame_player_points_odds(fixture["pregame_odds"])
    identity = normalize_schedule_identity_sources(fixture["schedule_identity"])
    minutes = normalize_minutes_feature_inputs(fixture["minutes_inputs"])

    market = next(record for record in pregame.normalized_records if record["sportsbook"] == "DraftKings")
    schedule_rows = list(identity.records_by_type("schedule_event"))
    player_rows = list(identity.records_by_type("roster_player"))
    mapping_record = identity.records_by_type("reviewed_mapping_artifact")[0]
    mapping_artifact = dict(mapping_record["mapping_artifact"])
    crosswalk = join_nba_player_points_crosswalk(
        [market],
        schedule_rows,
        player_rows,
        reviewed_event_mapping=mapping_artifact,
        reviewed_player_mapping=mapping_artifact,
    )
    crosswalk_row = crosswalk.rows[0].to_dict()

    minutes_feature = map_minutes_feature_case_fixture(minutes.normalized_records[0]).to_dict()
    minutes_feature["minutes_source_hashes"] = minutes_feature["source_hashes"]
    assembly_record = {
        "market": market,
        "crosswalk": {
            "canonical_event_id": crosswalk_row["canonical_event_id"],
            "player_id": crosswalk_row["canonical_player_id"],
            "canonical_player_name": crosswalk_row["player_identity"]["canonical_player_name"],
            "team": crosswalk_row["player_identity"]["team"],
            "opponent": market["opponent"],
            "commence_time_utc": crosswalk_row["event_identity"]["commence_time_utc"],
            "operating_date": crosswalk_row["event_identity"]["operating_date"],
            "event_identity_status": crosswalk_row["event_identity"]["event_identity_status"],
            "player_identity_status": crosswalk_row["player_identity"]["player_identity_status"],
            "event_identity_method": crosswalk_row["event_identity"]["event_identity_method"],
            "player_identity_method": crosswalk_row["player_identity"]["player_identity_method"],
            "mapping_version": crosswalk_row["resolution_provenance"]["event_mapping_version"],
            "crosswalk_source_hashes": {
                "schedule_event": schedule_rows[0]["source_hash"],
                "roster_player": player_rows[0]["source_hash"],
                "reviewed_mapping_artifact": mapping_record["source_hash"],
            },
        },
        "minutes": minutes_feature,
        "projection": deepcopy(fixture["projection_fixture"]),
        "provenance": {
            "prediction_run_id": "run-nba-points-adapter-fixture",
            "prediction_timestamp_utc": "2026-06-05T18:08:00Z",
            "model_id": "nba-player-points-source-adapter-fixture-model",
            "repository_commit_sha": REPOSITORY_COMMIT_SHA,
            "source_manifest_id": fixture["minutes_inputs"]["source_manifest_id"],
            "research_label": "research_only_not_for_betting",
        },
    }
    return {
        "fixture": fixture,
        "pregame": pregame,
        "identity": identity,
        "minutes": minutes,
        "market": market,
        "schedule_rows": schedule_rows,
        "player_rows": player_rows,
        "mapping_record": mapping_record,
        "crosswalk": crosswalk,
        "crosswalk_row": crosswalk_row,
        "assembly_record": assembly_record,
    }


def _prediction_reference_from_ledger(
    *,
    evidence_root: Path,
    ledger_path: Path,
    prediction_row: dict[str, object],
) -> dict[str, dict[str, object]]:
    return {
        "sga_draftkings": {
            "prediction_reference": {
                "prediction_id": prediction_row["prediction_id"],
                "prediction_run_id": prediction_row["prediction_run_id"],
                "prediction_evidence_segment": ledger_path.relative_to(evidence_root).as_posix(),
                "prediction_record_hash": prediction_row["ledger_record_hash"],
            },
            "canonical_event_id": prediction_row["canonical_event_id"],
            "player_id": prediction_row["player_id"],
            "operating_date": prediction_row["operating_date"],
            "commence_time_utc": prediction_row["commence_time_utc"],
        }
    }


def _prediction_for_settlement(prediction_row: dict[str, object]) -> dict[str, object]:
    payload = dict(prediction_row)
    payload["artifact_hash"] = prediction_row["assembled_record_hash"]
    return payload


def test_fixture_catalog_and_architectural_boundary() -> None:
    fixture = _load_fixture()
    fixture_text = PROVIDER_SHAPES_FIXTURE.read_text(encoding="utf-8").casefold()
    source_text = SOURCE_ADAPTER_MODULE.read_text(encoding="utf-8").casefold()

    assert set(fixture) == {
        "pregame_odds",
        "schedule_identity",
        "minutes_inputs",
        "projection_fixture",
        "closing_odds",
        "final_stats",
    }
    assert fixture["pregame_odds"]["schema_version"] == NBA_PLAYER_POINTS_PREGAME_ODDS_FIXTURE_SCHEMA_VERSION
    assert fixture["closing_odds"]["schema_version"] == NBA_PLAYER_POINTS_CLOSING_ODDS_FIXTURE_SCHEMA_VERSION
    assert fixture["schedule_identity"]["schema_version"] == NBA_PLAYER_POINTS_SCHEDULE_IDENTITY_FIXTURE_SCHEMA_VERSION
    assert fixture["minutes_inputs"]["schema_version"] == NBA_PLAYER_POINTS_MINUTES_INPUT_FIXTURE_SCHEMA_VERSION
    assert fixture["final_stats"]["schema_version"] == NBA_PLAYER_POINTS_FINAL_STATS_FIXTURE_SCHEMA_VERSION
    assert "authorization" not in fixture_text
    assert "credential" not in fixture_text
    assert "secret" not in fixture_text
    assert "api_key" not in fixture_text
    assert "request_headers" not in fixture_text

    assert "requests" not in source_text
    assert "os.getenv" not in source_text
    assert "os.environ" not in source_text
    assert "courtvision_ai" not in source_text
    assert "run_today" not in source_text
    assert "sports.mlb" not in source_text
    assert "assemble_nba_player_points_batch" not in source_text
    assert "write_nba_player_points_evidence" not in source_text
    assert "settle_nba_player_points_predictions" not in source_text
    assert "kelly_" not in source_text
    assert "bankroll_" not in source_text
    assert "import grading" not in source_text
    assert "grading_" not in source_text


def test_pregame_odds_normalization_preserves_market_and_provenance() -> None:
    result = normalize_pregame_player_points_odds(_load_fixture()["pregame_odds"])
    records = list(result.normalized_records)
    draftkings = next(record for record in records if record["sportsbook"] == "DraftKings")

    assert len(records) == 2
    assert result.invalid_records == ()
    assert result.ok is True
    assert draftkings["provider_event_id"] == "odds_evt_20260605_okc_ind"
    assert draftkings["provider_player_name"] == "Shai Gilgeous-Alexander"
    assert draftkings["provider_player_id"] == "toa-player-sga"
    assert draftkings["market"] == "player_points"
    assert draftkings["side"] == "over"
    assert draftkings["line"] == 31.5
    assert draftkings["american_odds"] == -110
    assert draftkings["decimal_odds"] == pytest.approx(1.909091)
    assert draftkings["implied_probability"] == pytest.approx(0.52381)
    assert draftkings["market_timestamp_utc"] == "2026-06-05T18:02:00Z"
    assert draftkings["commence_time_utc"] == "2026-06-06T00:40:00Z"
    assert draftkings["operating_date"] == "2026-06-05"
    assert draftkings["market_source_id"] == "fixture-pregame:sga:draftkings:points"
    assert draftkings["source_id"] == draftkings["market_source_id"]
    assert draftkings["source_hash"] == draftkings["market_source_hash"]
    assert SHA256_RE.fullmatch(str(draftkings["source_hash"]))
    assert draftkings["adapter_schema_version"] == NBA_PLAYER_POINTS_SOURCE_ADAPTER_SCHEMA_VERSION
    assert draftkings["provider_capability"]["supports_live_calls"] is False
    assert draftkings["provider_capability"]["reads_credentials"] is False
    assert draftkings["provider_capability"]["writes_files"] is False
    assert "model_over_probability" not in draftkings
    assert "selected_side" not in draftkings
    assert "model_edge" not in draftkings


def test_market_implied_probability_cannot_satisfy_model_probability_eligibility() -> None:
    chain = _build_adapter_chain()
    assembly_record = dict(chain["assembly_record"])
    assembly_record["probability"] = dict(chain["market"])

    assembled = assemble_nba_player_points_batch(
        [assembly_record],
        manifest_created_at_utc="2026-06-05T18:09:00Z",
    )
    row = assembled.rows[0]

    assert chain["market"]["implied_probability"] == pytest.approx(0.52381)
    assert row.assembly_status == "eligible_projection_research"
    assert row.probability_research_eligible is False
    assert row.probability_status == "unavailable"
    assert row.model_over_probability is None
    assert row.model_under_probability is None
    assert row.probability_model_id is None
    assert row.probability_based_edge is None
    assert row.selected_side is None
    assert row.model_edge is None


@pytest.mark.parametrize(
    ("label", "mutate", "reason_fragment"),
    [
        ("unsupported market", lambda payload: _one_pregame_market(payload).update({"key": "player_rebounds"}), "unsupported market"),
        ("missing event ID", lambda payload: payload["events"][0].update({"provider_event_id": ""}), "provider_event_id is required"),
        ("missing sportsbook", lambda payload: payload["events"][0]["bookmakers"][0].update({"key": "", "title": ""}), "sportsbook is required"),
        ("missing line", lambda payload: _one_pregame_outcome(payload).update({"point": None, "line": None}), "line"),
        ("missing price", lambda payload: _one_pregame_outcome(payload).update({"price": None, "american_odds": None}), "american_odds"),
        ("missing player", lambda payload: _one_pregame_outcome(payload).update({"description": "", "player_name": "", "name": ""}), "provider_player_name"),
        ("timezone naive", lambda payload: _one_pregame_market(payload).update({"last_update": "2026-06-05T18:02:00"}), "timezone-aware"),
        ("NaN line", lambda payload: _one_pregame_outcome(payload).update({"point": float("nan")}), "NaN or infinity"),
    ],
)
def test_pregame_fail_closed_for_unsupported_and_missing_fields(
    label: str,
    mutate,
    reason_fragment: str,
) -> None:
    payload = deepcopy(_load_fixture()["pregame_odds"])
    payload["events"][0]["bookmakers"] = [payload["events"][0]["bookmakers"][0]]
    mutate(payload)

    result = normalize_pregame_player_points_odds(payload)

    assert result.normalized_records == (), label
    assert len(result.invalid_records) == 1, label
    assert reason_fragment in result.invalid_records[0].reason


@pytest.mark.parametrize(
    ("section", "adapter", "kwargs"),
    [
        ("pregame_odds", normalize_pregame_player_points_odds, {}),
        ("schedule_identity", normalize_schedule_identity_sources, {}),
        ("minutes_inputs", normalize_minutes_feature_inputs, {}),
        ("closing_odds", normalize_closing_player_points_odds, {"prediction_references": _basic_prediction_references()}),
        ("final_stats", normalize_final_stat_sources, {}),
    ],
)
def test_unsupported_schema_rejection_by_each_adapter(section: str, adapter, kwargs: dict[str, object]) -> None:
    payload = deepcopy(_load_fixture()[section])
    payload["schema_version"] = "unsupported-fixture-schema-v2"

    result = adapter(payload, **kwargs)

    assert result.normalized_records == ()
    assert len(result.invalid_records) == 1
    assert "unsupported provider schema" in result.invalid_records[0].reason


def test_source_id_hash_determinism_sensitivity_and_input_order_independence() -> None:
    fixture = _load_fixture()
    payload = deepcopy(fixture["pregame_odds"])
    bytes_payload = json.dumps(payload, sort_keys=True).encode("utf-8")

    first = normalize_pregame_player_points_odds(bytes_payload)
    second = normalize_pregame_player_points_odds(bytes_payload)
    mapping_result = normalize_pregame_player_points_odds(payload)
    assert first.to_dict()["normalized_records"] == second.to_dict()["normalized_records"]
    assert first.to_dict()["normalized_records"] == mapping_result.to_dict()["normalized_records"]

    reordered = deepcopy(payload)
    reordered["events"][0]["bookmakers"] = list(reversed(reordered["events"][0]["bookmakers"]))
    assert normalize_pregame_player_points_odds(reordered).to_dict()["normalized_records"] == mapping_result.to_dict()["normalized_records"]

    outcome = _one_pregame_outcome(payload)
    outcome_reordered = dict(reversed(list(outcome.items())))
    payload_reordered_fields = deepcopy(payload)
    payload_reordered_fields["events"][0]["bookmakers"][0]["markets"][0]["outcomes"][0] = outcome_reordered
    assert normalize_pregame_player_points_odds(payload_reordered_fields).normalized_records[0]["source_hash"] == mapping_result.normalized_records[0]["source_hash"]

    changed = deepcopy(payload)
    _one_pregame_outcome(changed)["price"] = -125
    assert normalize_pregame_player_points_odds(changed).normalized_records[0]["source_hash"] != mapping_result.normalized_records[0]["source_hash"]

    raw_source_change = deepcopy(payload)
    _one_pregame_outcome(raw_source_change)["provider_trace_id"] = "trace-material-change"
    assert (
        normalize_pregame_player_points_odds(raw_source_change).normalized_records[0]["source_hash"]
        != mapping_result.normalized_records[0]["source_hash"]
    )

    generated = deepcopy(payload)
    _one_pregame_outcome(generated).pop("source_id")
    generated_id = normalize_pregame_player_points_odds(generated).normalized_records[0]["source_id"]
    assert generated_id.startswith("fixture-pregame-odds-provider:market:")
    assert "draftkings" in generated_id
    assert "player-points" in generated_id

    assert source_fixture_hash({"b": 2, "a": 1}) == source_fixture_hash({"a": 1, "b": 2})
    assert source_fixture_hash({"a": 1}) != source_fixture_hash({"a": 2})
    assert source_fixture_hash({"a": 1, "source_hash": "0" * 64}) == source_fixture_hash({"a": 1})
    with pytest.raises(NBAPlayerPointsSourceAdapterError, match="NaN|finite|infinity"):
        source_fixture_hash({"a": float("inf")})


def test_duplicate_idempotency_and_conflicting_duplicate_behavior() -> None:
    fixture = _load_fixture()
    identical = deepcopy(fixture["pregame_odds"])
    market = _one_pregame_market(identical)
    market["outcomes"].append(deepcopy(market["outcomes"][0]))

    identical_result = normalize_pregame_player_points_odds(identical)

    assert len(identical_result.normalized_records) == 2
    assert len(identical_result.duplicate_diagnostics) == 1
    assert identical_result.duplicate_diagnostics[0].reason == "identical scoped source identity replay collapsed"

    conflicting = deepcopy(fixture["pregame_odds"])
    market = _one_pregame_market(conflicting)
    changed = deepcopy(market["outcomes"][0])
    changed["price"] = -120
    market["outcomes"].append(changed)

    conflict_result = normalize_pregame_player_points_odds(conflicting)

    assert len(conflict_result.conflicting_records) == 2
    assert conflict_result.ok is False
    assert all(diag.source_id == "fixture-pregame:sga:draftkings:points" for diag in conflict_result.conflicting_records)
    assert {record["sportsbook"] for record in conflict_result.normalized_records} == {"FanDuel"}


def test_source_identity_scope_is_not_raw_source_id_only() -> None:
    provider_a = _source_record(
        provider="provider_a",
        source_type="pregame_player_points_odds",
        source_id="shared-provider-id",
        payload={"provider": "provider_a", "price": -110},
    )
    provider_b = _source_record(
        provider="provider_b",
        source_type="pregame_player_points_odds",
        source_id="shared-provider-id",
        payload={"provider": "provider_b", "price": -115},
    )
    normalized, duplicates, conflicts = adapters._dedupe_records([provider_a, provider_b])
    assert len(normalized) == 2
    assert duplicates == ()
    assert conflicts == ()

    pregame = _source_record(
        provider="provider_a",
        source_type="pregame_player_points_odds",
        source_id="shared-type-id",
        payload={"type": "pregame", "price": -110},
    )
    closing = _source_record(
        provider="provider_a",
        source_type="closing_player_points_odds",
        source_id="shared-type-id",
        payload={"type": "closing", "price": -110},
    )
    normalized, duplicates, conflicts = adapters._dedupe_records([pregame, closing])
    assert len(normalized) == 2
    assert duplicates == ()
    assert conflicts == ()


def test_scoped_source_identity_idempotent_and_conflicting_content() -> None:
    first = _source_record(
        provider="provider_a",
        source_type="pregame_player_points_odds",
        source_id="same-scoped-id",
        payload={"line": 31.5, "price": -110},
    )
    identical = _source_record(
        provider="provider_a",
        source_type="pregame_player_points_odds",
        source_id="same-scoped-id",
        payload={"price": -110, "line": 31.5},
    )
    normalized, duplicates, conflicts = adapters._dedupe_records([first, identical])
    assert len(normalized) == 1
    assert len(duplicates) == 1
    assert conflicts == ()

    changed = _source_record(
        provider="provider_a",
        source_type="pregame_player_points_odds",
        source_id="same-scoped-id",
        payload={"line": 31.5, "price": -120},
    )
    normalized, duplicates, conflicts = adapters._dedupe_records([first, changed])
    assert normalized == ()
    assert duplicates == ()
    assert len(conflicts) == 2
    assert {diag.reason for diag in conflicts} == {"same scoped source identity has different canonical content"}


def test_same_raw_event_id_in_schedule_and_market_evidence_does_not_collide() -> None:
    raw_event_id = "odds_evt_20260605_okc_ind"
    schedule = _source_record(
        provider="fixture_provider",
        source_type="schedule_event",
        source_id=raw_event_id,
        payload={"provider_event_id": raw_event_id, "home_team": "OKC"},
    )
    market = _source_record(
        provider="fixture_provider",
        source_type="pregame_player_points_odds",
        source_id=raw_event_id,
        payload={"provider_event_id": raw_event_id, "line": 31.5, "price": -110},
    )
    normalized, duplicates, conflicts = adapters._dedupe_records([schedule, market])
    assert len(normalized) == 2
    assert duplicates == ()
    assert conflicts == ()


def test_schedule_identity_and_crosswalk_contract_normalization() -> None:
    chain = _build_adapter_chain()
    identity = chain["identity"]
    schedule = chain["schedule_rows"][0]
    player = chain["player_rows"][0]
    mapping = chain["mapping_record"]["mapping_artifact"]
    crosswalk_row = chain["crosswalk_row"]

    assert {record["source_type"] for record in identity.normalized_records} == {
        "schedule_event",
        "roster_player",
        "reviewed_mapping_artifact",
    }
    assert len(identity.ambiguous_records) == 2
    assert len(identity.unresolved_records) == 1
    assert schedule["provider_event_id"] == "odds_evt_20260605_okc_ind"
    assert schedule["canonical_event_id"] == "nba-2026-06-05-okc-ind"
    assert schedule["canonical_candidates"][0]["canonical_event_id"] == "nba-2026-06-05-okc-ind"
    assert schedule["commence_time_utc"] == "2026-06-06T00:40:00Z"
    assert schedule["operating_date"] == "2026-06-05"
    assert player["provider_player_id"] == "toa-player-sga"
    assert player["player_id"] == "nba-player-1628983"
    assert mapping["schema_version"] == "nba-player-points-crosswalk-mapping-v1"
    assert crosswalk_row["eligibility_status"] == "eligible"
    assert crosswalk_row["event_identity"]["event_identity_status"] == "resolved"
    assert crosswalk_row["player_identity"]["player_identity_status"] == "resolved"
    assert any("multiple_canonical_candidates" in diag.reason for diag in identity.ambiguous_records)
    assert any("missing_canonical_candidate" in diag.reason for diag in identity.unresolved_records)


def test_reviewed_mapping_duplicate_diagnostics_are_preserved() -> None:
    fixture = _load_fixture()
    payload = deepcopy(fixture["schedule_identity"])
    mappings = payload["reviewed_mappings"]["event_mappings"]
    mappings.append(deepcopy(mappings[0]))

    result = normalize_schedule_identity_sources(payload)

    assert len(result.duplicate_diagnostics) == 1
    assert result.duplicate_diagnostics[0].reason == "identical reviewed mapping replay collapsed"
    assert result.source_summary["duplicate_diagnostics"] == 1


def test_minutes_inputs_feed_projected_minutes_contract_and_reject_leakage() -> None:
    fixture = _load_fixture()
    result = normalize_minutes_feature_inputs(fixture["minutes_inputs"])
    record = result.normalized_records[0]
    projected = map_minutes_feature_case_fixture(record).to_dict()

    assert len(result.normalized_records) == 1
    assert result.invalid_records == ()
    assert record["source_type"] == "minutes_feature_input"
    assert "projected_minutes" not in record
    assert record["baseline"]["season_minutes"] == 35.2
    assert record["lineup"]["starter_status"] == "confirmed_starter"
    assert record["injury_availability"]["availability_status"] == "available"
    assert record["schedule"]["days_rest"] == 2
    assert record["role_context"]["teammate_absence_context"]["verified"] is False
    assert projected["projected_minutes"] == pytest.approx(36.99)
    assert projected["minutes_projection_status"] == "projected"
    assert all(SHA256_RE.fullmatch(value) for value in projected["source_hashes"].values())

    post_cutoff = deepcopy(fixture["minutes_inputs"])
    post_cutoff["players"][0]["lineup"]["source_timestamp_utc"] = "2026-06-05T18:31:00Z"
    post_cutoff_result = normalize_minutes_feature_inputs(post_cutoff)
    assert post_cutoff_result.normalized_records == ()
    assert "feature cutoff" in post_cutoff_result.invalid_records[0].reason

    leakage = deepcopy(fixture["minutes_inputs"])
    leakage["players"][0]["actual_minutes"] = 38.75
    leakage_result = normalize_minutes_feature_inputs(leakage)
    assert leakage_result.normalized_records == ()
    assert len(leakage_result.quarantined_records) == 1
    assert "leakage" in leakage_result.quarantined_records[0].reason

    final_stat_shaped = deepcopy(fixture["minutes_inputs"])
    final_stat_shaped["players"][0] = deepcopy(fixture["final_stats"]["games"][0]["players"][0])
    final_stat_shaped_result = normalize_minutes_feature_inputs(final_stat_shaped)
    assert final_stat_shaped_result.normalized_records == ()
    assert len(final_stat_shaped_result.quarantined_records) == 1
    assert "leakage" in final_stat_shaped_result.quarantined_records[0].reason


def test_closing_adapter_preserves_market_update_and_represents_post_tip_observations() -> None:
    fixture = _load_fixture()
    result = normalize_closing_player_points_odds(
        fixture["closing_odds"],
        prediction_references=_basic_prediction_references(),
    )
    record = result.normalized_records[0]

    assert len(result.normalized_records) == 1
    assert result.invalid_records == ()
    assert record["sportsbook"] == "DraftKings"
    assert record["market"] == "player_points"
    assert record["closing_line"] == 32.5
    assert record["closing_american_odds"] == -115
    assert record["observation_timestamp_utc"] == "2026-06-06T00:39:00Z"
    assert record["source_market_update_timestamp_utc"] == "2026-06-06T00:38:55Z"
    assert record["closing_source_id"] == "fixture-closing:sga:draftkings:points"
    assert record["closing_source_hash"] == record["source_hash"]

    post_tip = deepcopy(fixture["closing_odds"])
    post_tip["events"][0]["bookmakers"][0]["markets"][0]["observation_timestamp_utc"] = "2026-06-06T00:40:01Z"
    post_tip_result = normalize_closing_player_points_odds(
        post_tip,
        prediction_references=_basic_prediction_references(),
    )
    assert post_tip_result.normalized_records[0]["observation_timestamp_utc"] == "2026-06-06T00:40:01Z"

    missing_price = deepcopy(fixture["closing_odds"])
    missing_price["events"][0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = None
    missing_price_result = normalize_closing_player_points_odds(
        missing_price,
        prediction_references=_basic_prediction_references(),
    )
    assert missing_price_result.normalized_records == ()
    assert "closing_american_odds" in missing_price_result.invalid_records[0].reason


def test_final_stat_normalization_preserves_minutes_participation_and_game_status() -> None:
    fixture = _load_fixture()
    result = normalize_final_stat_sources(fixture["final_stats"])
    by_source = {record["source_row_id"]: record for record in result.normalized_records}

    assert len(result.normalized_records) == 6
    assert result.invalid_records == ()
    assert by_source["final-sga-valid"]["final_points"] == 35.0
    assert by_source["final-sga-valid"]["actual_minutes"] == 38.75
    assert by_source["final-sga-missing-minutes"]["actual_minutes"] is None
    assert by_source["final-sga-zero-minutes"]["actual_minutes"] == 0.0
    assert by_source["final-sga-zero-minutes"]["participation_status"] == "zero_minutes"
    assert by_source["final-sga-dnp"]["final_points"] is None
    assert by_source["final-sga-dnp"]["participation_status"] == "did_not_participate"
    assert by_source["final-sga-postponed"]["game_status"] == "postponed"
    assert by_source["final-sga-postponed"]["game_final"] is False
    assert by_source["final-sga-cancelled"]["game_status"] == "cancelled"
    assert by_source["final-sga-cancelled"]["game_final"] is False
    assert all(SHA256_RE.fullmatch(str(record["source_hash"])) for record in result.normalized_records)

    explicit_dnp_zero_minutes = deepcopy(fixture["final_stats"])
    explicit_dnp_zero_minutes["games"] = [explicit_dnp_zero_minutes["games"][0]]
    explicit_dnp_zero_minutes["games"][0]["players"] = [
        {
            "source_row_id": "explicit-dnp-zero-minutes",
            "player_id": "nba-player-1628983",
            "canonical_player_name": "Shai Gilgeous-Alexander",
            "team": "OKC",
            "opponent": "IND",
            "final_points": 0,
            "actual_minutes": 0,
            "participation_status": "did_not_participate",
        }
    ]
    explicit_dnp = normalize_final_stat_sources(explicit_dnp_zero_minutes).normalized_records[0]
    assert explicit_dnp["actual_minutes"] == 0.0
    assert explicit_dnp["participation_status"] == "did_not_participate"

    unsupported = deepcopy(fixture["final_stats"])
    unsupported["games"] = [unsupported["games"][0]]
    unsupported["games"][0]["game_status"] = "weather_delay"
    unsupported["games"][0]["game_final"] = False
    unsupported_result = normalize_final_stat_sources(unsupported)
    assert unsupported_result.normalized_records == ()
    assert "unsupported game_status" in unsupported_result.invalid_records[0].reason

    conflict = deepcopy(fixture["final_stats"])
    changed_player = deepcopy(conflict["games"][0]["players"][0])
    changed_player["final_points"] = 36
    conflict["games"][0]["players"].append(changed_player)
    conflict_result = normalize_final_stat_sources(conflict)
    assert len(conflict_result.conflicting_records) == 2
    assert "final-sga-valid" not in {record["source_row_id"] for record in conflict_result.normalized_records}


def test_batch_output_and_source_provenance_shape() -> None:
    fixture = _load_fixture()
    results = [
        (2, normalize_pregame_player_points_odds(fixture["pregame_odds"])),
        (6, normalize_schedule_identity_sources(fixture["schedule_identity"])),
        (1, normalize_minutes_feature_inputs(fixture["minutes_inputs"])),
        (
            1,
            normalize_closing_player_points_odds(
                fixture["closing_odds"],
                prediction_references=_basic_prediction_references(),
            ),
        ),
        (6, normalize_final_stat_sources(fixture["final_stats"])),
    ]

    for expected_input_count, result in results:
        assert set(result.to_dict()) == {
            "normalized_records",
            "invalid_records",
            "unresolved_records",
            "ambiguous_records",
            "quarantined_records",
            "conflicting_records",
            "duplicate_diagnostics",
            "source_summary",
            "capability_summary",
        }
        assert result.source_summary["normalized_records"] == len(result.normalized_records)
        assert _represented_count(result) == expected_input_count
        assert result.source_summary["represented_records"] == expected_input_count
        assert result.capability_summary["mode"] == "offline"
        assert result.capability_summary["supports_live_calls"] is False
        assert result.capability_summary["reads_credentials"] is False
        assert result.capability_summary["writes_files"] is False
        for record in result.normalized_records:
            assert record["provider"]
            assert record["source_type"]
            assert record["source_id"]
            assert record["source_timestamp_utc"].endswith("Z")
            assert record["source_schema_version"]
            assert SHA256_RE.fullmatch(str(record["source_hash"]))
            assert record["adapter_schema_version"] == NBA_PLAYER_POINTS_SOURCE_ADAPTER_SCHEMA_VERSION
            assert record["adapter_version"]
            assert record["provider_capability"]["mode"] == "offline"


def test_fixture_immutability_across_adapter_calls() -> None:
    before = _sha256(PROVIDER_SHAPES_FIXTURE)
    fixture = _load_fixture()

    normalize_pregame_player_points_odds(fixture["pregame_odds"])
    normalize_schedule_identity_sources(fixture["schedule_identity"])
    normalize_minutes_feature_inputs(fixture["minutes_inputs"])
    normalize_closing_player_points_odds(
        fixture["closing_odds"],
        prediction_references=_basic_prediction_references(),
    )
    normalize_final_stat_sources(fixture["final_stats"])

    assert _sha256(PROVIDER_SHAPES_FIXTURE) == before


def test_full_adapter_to_evidence_integration_no_live_calls_credentials_or_production_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before_fixture_hash = _sha256(PROVIDER_SHAPES_FIXTURE)
    monkeypatch.chdir(tmp_path)
    before_production_paths = _snapshot(tmp_path / "outputs") + _snapshot(tmp_path / "test_outputs") + _snapshot(tmp_path / "data")

    with (
        patch("requests.Session.get", side_effect=AssertionError("live call attempted")) as mock_get,
        patch("os.getenv", side_effect=AssertionError("credential read attempted")) as mock_getenv,
    ):
        first_chain = _build_adapter_chain()
        second_chain = _build_adapter_chain()
        assert first_chain["pregame"].to_dict() == second_chain["pregame"].to_dict()
        assert first_chain["identity"].to_dict() == second_chain["identity"].to_dict()
        assert first_chain["minutes"].to_dict() == second_chain["minutes"].to_dict()

        assembly = assemble_nba_player_points_batch(
            [first_chain["assembly_record"]],
            manifest_created_at_utc="2026-06-05T18:09:00Z",
        )
        assert assembly.rows[0].assembly_status == "eligible_projection_research"
        assert assembly.rows[0].probability_research_eligible is False
        assert assembly.rows[0].model_over_probability is None
        assert assembly.rows[0].selected_side is None

        prediction_result = write_nba_player_points_evidence(
            assembly,
            assembly.source_manifest_preview,
            tmp_path,
            NBAPlayerPointsEvidenceWriterConfig(),
            repository_commit_sha=REPOSITORY_COMMIT_SHA,
            writer_timestamp_utc="2026-06-05T18:10:00Z",
        )
        assert verify_nba_player_points_evidence(tmp_path, NBAPlayerPointsEvidenceWriterConfig()).ok is True

        prediction_row = json.loads(prediction_result.ledger_path.read_text(encoding="utf-8").splitlines()[0])
        evidence_root = tmp_path / "nba_player_points_evidence"
        references = _prediction_reference_from_ledger(
            evidence_root=evidence_root,
            ledger_path=prediction_result.ledger_path,
            prediction_row=prediction_row,
        )
        closing = normalize_closing_player_points_odds(
            first_chain["fixture"]["closing_odds"],
            prediction_references=references,
        )
        assert len(closing.normalized_records) == 1
        write_nba_player_points_closing_evidence(
            tmp_path,
            closing.normalized_records,
            NBAPlayerPointsClosingWriterConfig(),
            collection_timestamp_utc="2026-06-06T00:39:30Z",
            repository_commit_sha=REPOSITORY_COMMIT_SHA,
            writer_timestamp_utc="2026-06-06T00:39:30Z",
        )
        assert verify_nba_player_points_closing_evidence(tmp_path, NBAPlayerPointsClosingWriterConfig()).ok is True

        final_stats = normalize_final_stat_sources(first_chain["fixture"]["final_stats"])
        final_row = next(record for record in final_stats.normalized_records if record["source_row_id"] == "final-sga-valid")
        settlement = settle_nba_player_points_predictions(
            [_prediction_for_settlement(prediction_row)],
            [first_chain["crosswalk_row"]],
            [final_row],
            settlement_timestamp_utc="2026-06-06T03:45:00Z",
            repository_commit_sha=REPOSITORY_COMMIT_SHA,
        )
        assert [row.settlement_status for row in settlement.rows] == ["settled"]
        write_nba_player_points_settlement_evidence(
            tmp_path,
            settlement.rows,
            NBAPlayerPointsSettlementEvidenceWriterConfig(),
            collection_timestamp_utc="2026-06-06T03:45:00Z",
            repository_commit_sha=REPOSITORY_COMMIT_SHA,
            writer_timestamp_utc="2026-06-06T03:46:00Z",
        )
        assert verify_nba_player_points_settlement_evidence(
            tmp_path,
            NBAPlayerPointsSettlementEvidenceWriterConfig(),
        ).ok is True

    assert mock_get.call_count == 0
    assert mock_getenv.call_count == 0
    assert _sha256(PROVIDER_SHAPES_FIXTURE) == before_fixture_hash
    assert before_production_paths == ()
    assert not (tmp_path / "outputs").exists()
    assert not (tmp_path / "test_outputs").exists()
    assert not (tmp_path / "data" / "history").exists()
    assert (tmp_path / "nba_player_points_evidence" / "runs").exists()
    assert (tmp_path / "nba_player_points_evidence" / "closing").exists()
    assert (tmp_path / "nba_player_points_evidence" / "settlement").exists()
