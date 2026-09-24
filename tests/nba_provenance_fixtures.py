"""Explicit synthetic provenance for new tests; frozen historical JSON is untouched."""
from copy import deepcopy

from courtvision.sports.nba.player_minutes_research import minutes_evidence_identity
from courtvision.sports.nba.player_points_assembly import build_probability_identity


def bind_probability(record):
    probability = record["probability"]
    target = record["crosswalk"]
    for key in ("canonical_event_id", "player_id", "operating_date", "commence_time_utc"):
        probability[key] = target[key]
    probability.update(
        provider_event_id=record["market"]["provider_event_id"], market="player_points",
        line=record["market"]["line"], probability_model_version="1.0",
        projection_source_hash=record["projection"]["projection_source_hash"],
        minutes_source_hash=minutes_evidence_identity(record["minutes"])["minutes_source_hash"],
    )
    probability["probability_identity"] = build_probability_identity(
        market_evidence=record["market"], crosswalk_evidence=target,
        minutes_evidence=record["minutes"], projection_evidence=record["projection"],
        provenance=record["provenance"], probability_evidence=probability,
    )
    return record


def assembly_fixture(payload):
    """Explicitly author the next fixture version in memory for regression tests."""
    fixture = deepcopy(payload)
    base = fixture["base_case"]
    provider_id = base["market"]["provider_event_id"]
    base["crosswalk"]["provider_event_id"] = provider_id
    base["minutes"].update(provider_event_id=provider_id,
                           source_manifest_id="synthetic-minutes-manifest-v1")
    base["projection"].update(provider_event_id=provider_id,
                              canonical_event_id=base["crosswalk"]["canonical_event_id"],
                              player_id=base["crosswalk"]["player_id"])
    for case in fixture["cases"]:
        probability = case.get("overrides", {}).get("probability")
        if probability and case["case_id"] == "valid_probability_research":
            record = deepcopy(base)
            record["probability"] = probability
            bind_probability(record)
    return fixture


def provider_fixture(payload):
    fixture = deepcopy(payload)
    fixture["projection_fixture"].update(
        provider_event_id="odds_evt_20260605_okc_ind",
        canonical_event_id="nba-2026-06-05-okc-ind", player_id="nba-player-1628983",
    )
    return fixture
