from __future__ import annotations

import builtins
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import fields, is_dataclass, replace
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import socket

import pytest

from courtvision.core.candidates import IdentityStatus
from courtvision.core.probability import DistributionType, ThresholdDirection
from courtvision.core.reasoning import EvidenceAvailability
from courtvision.sports.mlb.hr_pipeline import run_mlb_hr_research_pipeline
from courtvision.sports.mlb.hr_report import build_hr_report
from courtvision.sports.mlb.market_adapter import adapt_mlb_hr_prediction
from courtvision.sports.mlb.training.hr_research_baseline import (
    FEATURE_SCHEMA_VERSION,
    MODEL_REQUIRED_INPUT_COLUMNS,
    NUMERIC_MODEL_FEATURES,
    PREDICTION_SCHEMA_VERSION,
    RESEARCH_ONLY_LABEL,
    ModelBundle,
    predict_model_probability,
)


RUN_DATE = date(2026, 6, 19)
GENERATED_AT = datetime(2026, 6, 19, 16, 30, tzinfo=timezone.utc)


@pytest.fixture
def prediction_quote():
    # Reuse the existing offline pipeline fixture quote and existing specialist
    # predictor, with deterministic in-memory model parameters and no training.
    quote = run_mlb_hr_research_pipeline(
        RUN_DATE, provider="fixture", generated_at=GENERATED_AT,
    ).normalized_odds_quotes[0]
    model = ModelBundle(
        bundle_dir=Path("fixture-model-not-loaded"),
        metadata={"model_id": "fixture-logistic", "model_version": "fixture-v1"},
        model={
            "preprocessing": {
                "numeric": {key: {"mean": 0.0, "stdev": 1.0} for key in NUMERIC_MODEL_FEATURES},
                "categorical_levels": {},
            },
            "weights": [-1.23456789] + [0.0] * (2 * len(NUMERIC_MODEL_FEATURES)),
        },
    )
    probability = predict_model_probability(
        {key: "0" for key in MODEL_REQUIRED_INPUT_COLUMNS}, model,
    )
    row = {
        "prediction_id": "fixture-prediction",
        "prediction_run_id": "fixture-run",
        "prediction_schema_version": PREDICTION_SCHEMA_VERSION,
        "research_label": RESEARCH_ONLY_LABEL,
        "model_id": model.model_id,
        "model_version": model.model_version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "event_id": quote.market_identity.event_id,
        "game_date": RUN_DATE.isoformat(),
        "home_team": quote.market_identity.home_team,
        "away_team": quote.market_identity.away_team,
        "commence_time": quote.event_start_time.isoformat(),
        "player_name": quote.selection.selection_name,
        "player_id": "legacy-source-id-is-not-canonical",
        "sportsbook": quote.source_metadata.sportsbook,
        "american_odds": str(quote.american_odds),
        "implied_probability": f"{quote.implied_probability:.12g}",
        "model_probability": probability,
        "prediction_timestamp": GENERATED_AT.isoformat(),
        "snapshot_time": GENERATED_AT.isoformat(),
        "market_key": "batter_home_runs_alternate",
        "side": "Over",
        "point": "0.5",
        "source_manifest_reference": "fixture-manifest",
        "source_odds_sha256": "a" * 64,
        "eligibility_status": "eligible_for_prediction",
        "probability_edge": "legacy-rounded-edge",
    }
    return row, quote


def test_exact_probability_quote_provenance_and_research_boundaries(prediction_quote):
    row, quote = prediction_quote
    original = deepcopy(row)
    candidate = adapt_mlb_hr_prediction(row, quote, evidence_cutoff=GENERATED_AT)
    assert row == original
    assert candidate.quote is quote
    assert candidate.quote.source_metadata is quote.source_metadata
    assert candidate.taxonomy.market_type == "batter_home_runs"
    assert candidate.probability.model_probability == row["model_probability"]
    assert candidate.reasoning.model_probability.value == row["model_probability"]
    assert candidate.reasoning.market_implied_probability.value == quote.implied_probability
    assert candidate.probability.target_statistic == "home_runs"
    assert candidate.probability.threshold == 1
    assert candidate.probability.direction is ThresholdDirection.AT_LEAST
    assert candidate.probability.distribution_type is DistributionType.BERNOULLI_EVENT
    assert candidate.probability.uncertainty.availability is EvidenceAvailability.UNAVAILABLE
    assert candidate.probability.calibration_provenance.availability is EvidenceAvailability.UNAVAILABLE
    assert candidate.probability.evidence_cutoff == GENERATED_AT
    assert candidate.reasoning.edge.availability is EvidenceAvailability.UNAVAILABLE
    assert candidate.reasoning.threshold_cushion.availability is EvidenceAvailability.UNAVAILABLE
    assert candidate.participant_identity.identity_status is IdentityStatus.NAME_ONLY_RESEARCH
    assert candidate.participant_identity.canonical_player_id is None
    assert candidate.participant_identity.canonical_player_name is None
    assert candidate.research_only is True
    assert candidate.eligible_for_betting is False
    assert candidate.eligible_for_official_pick is False
    assert candidate.approval_status == "not_approved"
    assert "source_manifest_reference=fixture-manifest" in candidate.provenance.source_refs
    assert "player_id=legacy-source-id-is-not-canonical" in candidate.provenance.source_refs
    row["source_manifest_reference"] = "changed-after-translation"
    assert "source_manifest_reference=fixture-manifest" in candidate.provenance.source_refs


def test_serialized_probability_is_not_reestimated_or_rounded(prediction_quote):
    row, quote = prediction_quote
    row["model_probability"] = "0.12345678912345678"
    candidate = adapt_mlb_hr_prediction(row, quote, evidence_cutoff=GENERATED_AT)
    assert candidate.probability.model_probability == float(row["model_probability"])


def test_name_alias_keeps_observed_quote_name_and_original_source_reference(prediction_quote):
    row, quote = prediction_quote
    row["player_name"] = "Ronald Acuña Jr."
    quote = replace(quote, selection=replace(quote.selection, selection_name="Ronald Acuna"))
    candidate = adapt_mlb_hr_prediction(row, quote, evidence_cutoff=GENERATED_AT)
    assert candidate.participant_identity.participant_name == "Ronald Acuna"
    assert "player_name=Ronald Acuña Jr." in candidate.provenance.source_refs
    assert candidate.participant_identity.canonical_player_id is None


def test_implied_probability_mismatch_beyond_legacy_precision_is_rejected(prediction_quote):
    row, quote = prediction_quote
    row["implied_probability"] = quote.implied_probability + 1e-10
    with pytest.raises(ValueError, match="implied_probability"):
        adapt_mlb_hr_prediction(row, quote, evidence_cutoff=GENERATED_AT)


def test_adapter_performs_no_io(prediction_quote, monkeypatch):
    row, quote = prediction_quote
    def forbidden(*args, **kwargs):
        raise AssertionError("adapter attempted I/O")
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        for name in ("open", "write_text", "write_bytes", "mkdir", "unlink"):
            patch.setattr(Path, name, forbidden)
        patch.setattr(socket, "socket", forbidden)
        patch.setattr(socket, "create_connection", forbidden)
        candidate = adapt_mlb_hr_prediction(row, quote, evidence_cutoff=GENERATED_AT)
    assert candidate.quote is quote


@pytest.mark.parametrize("key,value", [
    ("model_probability", None), ("model_probability", True),
    ("model_probability", float("nan")), ("model_probability", float("inf")),
    ("model_probability", 24.5), ("model_id", ""),
    ("model_version", ""), ("feature_schema_version", ""),
    ("event_id", "other-event"), ("game_date", "2026-06-20"),
    ("home_team", "Other Home Team"), ("away_team", "Other Away Team"),
    ("commence_time", "2026-06-19T16:29:00Z"),
    ("commence_time", "2026-06-20T23:00:00Z"),
    ("player_name", "Other Batter"), ("sportsbook", "Other Book"),
    ("american_odds", "400"), ("implied_probability", "0.9"),
    ("market_key", "batter_hits"), ("point", "1.5"), ("side", "Under"),
    ("research_label", "approved"), ("prediction_schema_version", "unknown"),
    ("snapshot_time", "2026-06-20T16:30:00Z"),
    ("prediction_timestamp", "2026-06-19T16:30:00"),
])
def test_conflicting_or_unproven_inputs_fail_closed(prediction_quote, key, value):
    row, quote = prediction_quote
    with pytest.raises(ValueError):
        adapt_mlb_hr_prediction({**row, key: value}, quote, evidence_cutoff=GENERATED_AT)


def test_ranking_score_cannot_substitute_for_probability(prediction_quote):
    row, quote = prediction_quote
    row.pop("model_probability")
    row["research_score"] = 85
    with pytest.raises(ValueError, match="model_probability"):
        adapt_mlb_hr_prediction(row, quote, evidence_cutoff=GENERATED_AT)


def test_quote_time_line_and_cutoff_must_bind(prediction_quote):
    row, quote = prediction_quote
    wrong_line = replace(quote, selection=replace(quote.selection, line=1.5))
    with pytest.raises(ValueError, match="Over 0.5"):
        adapt_mlb_hr_prediction(row, wrong_line, evidence_cutoff=GENERATED_AT)
    with pytest.raises(ValueError, match="snapshot_time"):
        adapt_mlb_hr_prediction(row, replace(quote, quote_timestamp=GENERATED_AT.replace(hour=15)), evidence_cutoff=GENERATED_AT)
    with pytest.raises(ValueError, match="evidence_cutoff"):
        adapt_mlb_hr_prediction(row, quote, evidence_cutoff=GENERATED_AT.replace(tzinfo=None))


def test_preimplementation_pipeline_fixture_parity(tmp_path):
    # Captured at base 4870ab70 before contract implementation. Covers all
    # sample/fixture rows, quotes, context, warnings, eligibility, artifact JSON,
    # report rows and relative output paths. Only the disposable root is masked.
    def plain(value):
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Path):
            return "<SCRATCH>/" + value.relative_to(tmp_path).as_posix()
        if isinstance(value, Enum):
            return value.value
        if is_dataclass(value):
            return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
        if isinstance(value, Mapping):
            return {key: plain(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [plain(item) for item in value]
        return value
    payload = {}
    for provider in ("sample", "fixture"):
        result = run_mlb_hr_research_pipeline(
            RUN_DATE, provider=provider, generated_at=GENERATED_AT,
            artifact_path=tmp_path / provider / "artifact.json",
        )
        payload[provider] = {
            "result": plain(result),
            "written_artifact": json.loads(result.artifact_path.read_text(encoding="utf-8")),
        }
    payload["report_rows"] = [row.to_dict() for row in build_hr_report(RUN_DATE)]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=plain)
    assert hashlib.sha256(encoded.encode()).hexdigest() == "a9b4cc2eae942a4302dbffbb024fd0192a96adc35a5e8306533da198a1624e91"
