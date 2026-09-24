from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest

from nba_provenance_fixtures import assembly_fixture, bind_probability
from courtvision.sports.nba.artifact_domains import (
    NBA_OUTCOME_EVIDENCE, NBA_PROSPECTIVE_EVIDENCE, NBA_STAT_ARTIFACT_SCHEMA,
    contains_target_game_outcome, require_artifact_path, stat_artifact_path,
)
from courtvision.sports.nba.player_points_assembly import (
    assemble_nba_player_points_batch, validate_probability_identity_payload,
)
from courtvision.sports.nba.player_points_evidence import (
    NBAPlayerPointsEvidenceWriterConfig, write_nba_player_points_evidence,
)
from courtvision.sports.nba.player_points_research_runner import (
    NBAPlayerPointsBundleError,
    _projection_for_market, _probability_for_market, _find_minutes_for_market,
)
from scripts.run_market_projection_join import _load_projection_source
from scripts.run_research_mode import _write_stat_projection_csv
from scripts.build_stat_projection_source import build_stat_projection_source
from courtvision.sports.nba.player_points_closing import NBAPlayerPointsClosingWriterConfig
from courtvision.sports.nba.player_points_settlement_evidence import NBAPlayerPointsSettlementEvidenceWriterConfig


def record():
    fixture = assembly_fixture(json.loads((Path(__file__).parent / "fixtures/nba/player_points/assembly_cases.json").read_text()))
    result = fixture["base_case"]
    result["probability"] = next(case for case in fixture["cases"] if case["case_id"] == "valid_probability_research")["overrides"]["probability"]
    return result


def assemble(payload):
    return assemble_nba_player_points_batch([payload], manifest_created_at_utc="2026-06-05T18:30:00Z")


@pytest.mark.parametrize("section", ["market", "crosswalk", "minutes", "projection", "probability", "provenance"])
@pytest.mark.parametrize("field", ["actual_points", "actual_minutes", "final_points", "final_stats", "box_score", "settlement", "result", "grade"])
def test_all_prospective_boundaries_reject_nested_outcomes(section, field):
    payload = record()
    payload[section]["nested"] = [{field: None}]
    row = assemble(payload).rows[0]
    assert not row.projection_research_eligible
    assert not row.probability_research_eligible
    assert row.probability_status != "valid"
    assert row.assembly_status == "quarantined"


@pytest.mark.parametrize("section", ["projection", "minutes", "probability", "crosswalk"])
@pytest.mark.parametrize("field", ["canonical_event_id", "player_id"])
@pytest.mark.parametrize("value", [None, "different-canonical-id"])
def test_canonical_identity_required_and_names_cannot_override(section, field, value):
    payload = record()
    payload[section][field] = value
    row = assemble(payload).rows[0]
    assert row.probability_status != "valid"
    assert not row.probability_research_eligible


@pytest.mark.parametrize("section,field", [
    ("projection", "projection_timestamp_utc"), ("projection", "projection_cutoff_timestamp_utc"),
    ("minutes", "feature_timestamp_utc"), ("minutes", "feature_cutoff_timestamp_utc"),
    ("probability", "probability_timestamp_utc"), ("market", "market_timestamp_utc"),
])
def test_post_tip_clocks_cannot_qualify(section, field):
    payload = record()
    payload[section][field] = "2026-06-06T01:00:00Z"
    row = assemble(payload).rows[0]
    assert row.probability_status != "valid"
    assert not row.probability_research_eligible


def test_probability_hashes_are_deterministic_and_economically_quarantined(tmp_path):
    payload = record()
    first, second = assemble(payload), assemble(deepcopy(payload))
    row = first.rows[0]
    assert row.probability_status == "valid"
    assert row.probability_identity_hash == second.rows[0].probability_identity_hash
    assert row.probability_identity_hash
    assert row.research_only is True
    assert row.selected_side is None and row.model_edge is None and row.probability_based_edge is None
    assert not {"stake", "recommended_bet", "kelly_eligible", "official_pick"}.intersection(row.to_dict())
    written = write_nba_player_points_evidence(first, first.source_manifest_preview, tmp_path,
        NBAPlayerPointsEvidenceWriterConfig(), repository_commit_sha=payload["provenance"]["repository_commit_sha"],
        writer_timestamp_utc="2026-06-05T18:30:00Z")
    predictions = list(tmp_path.rglob("prediction_rows.jsonl"))
    assert predictions and written.completion_status == "complete"
    stored = json.loads(predictions[0].read_text().splitlines()[0])
    assert not contains_target_game_outcome(stored)
    assert stored["probability_identity_hash"] == row.probability_identity_hash
    validate_probability_identity_payload(stored)
    ledger = json.loads(next(tmp_path.rglob("prediction_ledger.jsonl")).read_text().splitlines()[0])
    validate_probability_identity_payload(ledger)
    for field in ("player_id", "line", "probability_identity_hash"):
        corrupt = deepcopy(stored)
        corrupt[field] = "tampered"
        with pytest.raises(ValueError):
            validate_probability_identity_payload(corrupt)


@pytest.mark.parametrize("change", ["projection_hash", "minutes_hash", "model_id", "model_version", "event", "commit"])
def test_provenance_changes_model_identity(change):
    payload = record()
    before = assemble(payload).rows[0].probability_identity_hash
    if change == "projection_hash":
        payload["projection"]["projection_source_hash"] = "9" * 64
    elif change == "minutes_hash":
        payload["minutes"]["minutes_source_hashes"]["baseline"] = "9" * 64
    elif change == "model_id":
        payload["probability"]["probability_model_id"] = "different-model"
    elif change == "model_version":
        payload["probability"]["probability_model_version"] = "2.0"
    elif change == "event":
        for section in ("crosswalk", "minutes", "projection"):
            payload[section]["canonical_event_id"] = "different-canonical-event"
    else:
        payload["provenance"]["repository_commit_sha"] = "9" * 40
    # Explicit producer rebinding, never done by the admission code.
    old_version = payload["probability"]["probability_model_version"]
    bind_probability(payload)
    if change == "model_version":
        from courtvision.sports.nba.player_points_assembly import build_probability_identity
        payload["probability"]["probability_model_version"] = old_version
        payload["probability"]["probability_identity"] = build_probability_identity(
            market_evidence=payload["market"], crosswalk_evidence=payload["crosswalk"],
            minutes_evidence=payload["minutes"], projection_evidence=payload["projection"],
            provenance=payload["provenance"], probability_evidence=payload["probability"])
    row = assemble(payload).rows[0]
    assert row.probability_status == "valid"
    assert row.probability_identity_hash != before


def test_price_and_line_are_separate_from_model_identity():
    payload = record()
    original = assemble(payload).rows[0]
    payload["market"].update(line=32.5, american_odds=-125, sportsbook="Other book", market_source_hash="8" * 64)
    assert assemble(payload).rows[0].probability_status != "valid"
    payload["probability"]["line"] = 32.5
    changed = assemble(payload).rows[0]
    assert changed.probability_status == "valid"
    assert changed.probability_identity_hash == original.probability_identity_hash
    assert changed.probability_assessment_hash != original.probability_assessment_hash


def test_legacy_probability_metadata_does_not_qualify():
    payload = record()
    del payload["probability"]["probability_identity"]
    assert assemble(payload).rows[0].probability_status != "valid"
    payload = record()
    del payload["projection"]["player_id"]
    assert not assemble(payload).rows[0].projection_research_eligible


def test_valid_research_probability_does_not_restore_existing_kelly_eligibility():
    from scripts.run_kelly_stakes import _build_stake_row

    row = assemble(record()).rows[0].to_dict()
    assert row["probability_status"] == "valid"
    candidate = {**row, "market_type": "player_points", "selection": "over",
                 "odds": "-110", "confidence": "0.99", "edge_pct": "0.30",
                 "side_edge_pct": "0.30"}
    stake = _build_stake_row(candidate, "edge_pct", bankroll=1000)
    assert stake.eligible is False
    assert stake.stake_fraction == stake.stake_amount == 0
    assert stake.expected_value is None
    assert stake.economic_ineligibility_reason == "economic_probability_provenance_unqualified"


def test_selectors_require_unique_exact_ids_not_position():
    payload = record()
    target = {**payload["market"], **{key: payload["crosswalk"][key] for key in ("player_id", "canonical_event_id")}}
    for selector, evidence in ((_projection_for_market, payload["projection"]), (_probability_for_market, payload["probability"])):
        assert selector([evidence], target, 999) == evidence
        wrong = {**evidence, "player_id": "other"}
        with pytest.raises(NBAPlayerPointsBundleError):
            selector([wrong], target, 0)
        with pytest.raises(NBAPlayerPointsBundleError):
            selector([evidence, evidence], target, 0)
        with pytest.raises(NBAPlayerPointsBundleError):
            selector([{}], target, 0)
    crosswalk = {"canonical_event_id": target["canonical_event_id"], "canonical_player_id": target["player_id"]}
    assert _find_minutes_for_market([{**payload["minutes"], "player_id": "other"}], crosswalk, target) is None


def test_outcome_writer_cannot_use_prospective_namespace(tmp_path):
    path = stat_artifact_path(tmp_path, NBA_OUTCOME_EVIDENCE, "2026-06-05")
    _write_stat_projection_csv(path, [{"points": 25, "minutes": 30, "artifact_domain": NBA_OUTCOME_EVIDENCE}])
    assert pd.read_csv(path).iloc[0]["points"] == 25
    with pytest.raises(ValueError):
        _write_stat_projection_csv(tmp_path / "nba/prospective/actuals.csv", [])
    with pytest.raises(FileExistsError):
        _write_stat_projection_csv(path, [])
    assert pd.read_csv(path).iloc[0]["points"] == 25
    with pytest.raises(ValueError):
        require_artifact_path(path, NBA_PROSPECTIVE_EVIDENCE)


@pytest.mark.parametrize("config,kwargs", [
    (NBAPlayerPointsEvidenceWriterConfig, {"runs_dir_name": "outcomes"}),
    (NBAPlayerPointsEvidenceWriterConfig, {"ledgers_dir_name": "settlement"}),
    (NBAPlayerPointsClosingWriterConfig, {"closing_dir_name": "outcomes"}),
    (NBAPlayerPointsSettlementEvidenceWriterConfig, {"settlement_dir_name": "prospective"}),
    (NBAPlayerPointsSettlementEvidenceWriterConfig, {"segments_dir_name": "runs"}),
])
def test_configurable_paths_cannot_cross_domains(config, kwargs):
    with pytest.raises(ValueError):
        config(**kwargs)


def test_builder_preserves_legacy_and_never_backdates_new_projection(tmp_path):
    legacy = tmp_path / "stat_projection_source_2026-06-05.csv"
    legacy.write_text("points,minutes\n31,36\n")
    original = legacy.read_bytes()
    source = tmp_path / "context.csv"
    pd.DataFrame([{"player_name": "Fixture", "pts_recent": 25, "pts_avg": 25,
        "source_timestamp_utc": "2026-06-05T18:00:00Z",
        "evidence_cutoff_timestamp_utc": "2026-06-05T18:30:00Z",
        "commence_time_utc": "2026-06-06T00:40:00Z"}]).to_csv(source, index=False)
    result = build_stat_projection_source(target_date="2026-06-05", cleaned_context=source,
        output_dir=tmp_path, diagnostics_dir=tmp_path / "diagnostics")
    row = pd.read_csv(result.output_path).iloc[0].to_dict()
    assert row["prospective_status"] == "unqualified"
    assert row["projection_timestamp_utc"] > row["evidence_cutoff_timestamp_utc"]
    assert not contains_target_game_outcome(row)
    assert legacy.read_bytes() == original
    with pytest.raises(FileExistsError):
        build_stat_projection_source(target_date="2026-06-05", cleaned_context=source,
            output_dir=tmp_path, diagnostics_dir=tmp_path / "diagnostics")


@pytest.mark.parametrize("kind", ["outcome", "legacy", "mixed", "nested", "post_tip"])
def test_prospective_csv_reader_rejects_outcome_and_legacy(tmp_path, kind):
    row = {"artifact_domain": NBA_PROSPECTIVE_EVIDENCE, "artifact_schema_version": NBA_STAT_ARTIFACT_SCHEMA,
           "operating_date": "2026-06-05", "source_timestamp_utc": "2026-06-05T18:00:00Z",
           "projection_timestamp_utc": "2026-06-05T18:01:00Z", "evidence_cutoff_timestamp_utc": "2026-06-05T18:30:00Z",
           "commence_time_utc": "2026-06-06T00:40:00Z", "projected_points": 25, "player_name": "Fixture"}
    path = tmp_path / "nba/prospective/projections.csv"
    if kind == "outcome":
        path = tmp_path / "nba/outcomes/actuals.csv"
    elif kind == "legacy":
        path = tmp_path / "stat_projection_source_2026-06-05.csv"
    elif kind == "mixed":
        row["actual_points"] = 20
    elif kind == "nested":
        row["box_score"] = '{"points":20}'
    else:
        row["projection_timestamp_utc"] = "2026-06-06T02:00:00Z"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_csv(path, index=False)
    before = path.read_bytes()
    warnings = []
    _, frame, available, _ = _load_projection_source(target_date="2026-06-05", output_dir=tmp_path,
        projection_source=path, warnings=warnings)
    assert not available and frame.empty and warnings
    assert path.read_bytes() == before
