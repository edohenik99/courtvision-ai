from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping
from unittest.mock import patch

import pytest

from courtvision.sports.nba.player_minutes_research import (
    NBA_PLAYER_MINUTES_FEATURE_FIELDS,
    NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION,
    NBA_PLAYER_MINUTES_PROJECTION_STATUSES,
    NBA_PLAYER_MINUTES_RESEARCH_LABEL,
    NBAPlayerMinutesFeatureSchemaError,
    map_minutes_feature_case_fixture,
    map_minutes_feature_cases_fixture,
    map_player_baseline_fixture,
    provider_capability_matrix,
    schema_definition,
    validate_feature_rows,
    validate_schema_version,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "nba" / "player_minutes"
MINUTES_CASES_FIXTURE = FIXTURE_ROOT / "projected_minutes_cases.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

REQUIRED_CASE_IDS = {
    "stable_high_minute_starter",
    "stable_low_minute_starter",
    "stable_bench_player",
    "recent_role_increase",
    "recent_role_decrease",
    "insufficient_recent_sample",
    "missing_season_minutes",
    "missing_recent_minutes",
    "missing_both_minutes",
    "high_recent_volatility",
    "confirmed_starter",
    "confirmed_bench_role",
    "lineup_unconfirmed",
    "questionable_player",
    "inactive_player",
    "player_did_not_dress",
    "returning_from_injury",
    "confirmed_minutes_restriction",
    "verified_teammate_absence",
    "back_to_back_game",
    "condensed_schedule",
    "observation_exactly_at_cutoff",
    "observation_after_cutoff",
    "target_game_actual_minutes_supplied",
    "target_game_final_stat_row_supplied",
    "timezone_naive_timestamp",
    "unresolved_player_identity",
    "unresolved_event_identity",
    "conflicting_status_evidence",
    "source_fixture_immutability",
}


def _load_fixture() -> dict[str, Any]:
    return json.loads(MINUTES_CASES_FIXTURE.read_text(encoding="utf-8"))


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


def _deep_merge(base: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(dict(base))
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _case_payload(case_id: str, extra_overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    fixture = _load_fixture()
    base = fixture["base_case"]
    cases = {case["case_id"]: case for case in fixture["cases"]}
    case = cases[case_id]
    payload = _deep_merge(base, case.get("overrides", {}))
    payload["case_id"] = case_id
    if extra_overrides:
        payload = _deep_merge(payload, extra_overrides)
    return payload


def _feature(case_id: str, extra_overrides: Mapping[str, Any] | None = None):
    return map_minutes_feature_case_fixture(_case_payload(case_id, extra_overrides))


def _adjustment_names(row) -> set[str]:
    return {adjustment["adjustment_name"] for adjustment in row.to_dict()["applied_adjustments"]}


def test_fixture_catalog_contains_all_required_offline_cases() -> None:
    fixture = _load_fixture()
    case_ids = {case["case_id"] for case in fixture["cases"]}

    assert case_ids == REQUIRED_CASE_IDS
    assert len(case_ids) == 30
    assert "credentials" not in MINUTES_CASES_FIXTURE.read_text(encoding="utf-8").casefold()


def test_schema_version_required_fields_statuses_and_bounds() -> None:
    definition = schema_definition()

    assert definition["schema_version"] == NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION
    assert definition["required_fields"] == list(NBA_PLAYER_MINUTES_FEATURE_FIELDS)
    assert set(definition["projection_statuses"]) == set(NBA_PLAYER_MINUTES_PROJECTION_STATUSES)
    assert definition["hard_minutes_bounds"] == {"minimum": 0.0, "maximum": 48.0}
    assert definition["research_label"] == NBA_PLAYER_MINUTES_RESEARCH_LABEL
    assert "commence_time_utc" in definition["utc_timestamp_fields"]

    assert validate_schema_version(NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION)
    with pytest.raises(NBAPlayerMinutesFeatureSchemaError, match="unsupported feature_schema_version"):
        validate_schema_version("nba-player-minutes-feature-v2")


def test_provider_capability_matrix_is_offline_and_explicit() -> None:
    matrix = provider_capability_matrix()

    assert set(matrix) == {
        "nba_player_baseline_fixture",
        "nba_lineup_status_fixture",
        "nba_injury_availability_fixture",
        "nba_schedule_rest_fixture",
        "nba_role_context_fixture",
    }
    for capability in matrix.values():
        assert capability["mode"] == "offline"
        assert capability["supports_live_calls"] is False
    assert "min_avg" in matrix["nba_player_baseline_fixture"]["available_fields"]
    assert "min_recent" in matrix["nba_player_baseline_fixture"]["available_fields"]
    assert "projected_minutes" in matrix["nba_player_baseline_fixture"]["unsupported_fields"]
    assert "actual_minutes" in matrix["nba_player_baseline_fixture"]["unsupported_fields"]
    assert "final_points" in matrix["nba_player_baseline_fixture"]["unsupported_fields"]


def test_complete_projected_row_weighted_basis_and_required_fields() -> None:
    row = _feature("stable_high_minute_starter")
    payload = row.to_dict()
    basis = payload["unadjusted_minutes_basis"]

    assert set(payload) == set(NBA_PLAYER_MINUTES_FEATURE_FIELDS)
    assert payload["feature_schema_version"] == NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION
    assert payload["provider_event_id"] == "odds_evt_20260605_okc_ind"
    assert payload["canonical_event_id"] == "nba-2026-06-05-okc-ind"
    assert payload["operating_date"] == "2026-06-05"
    assert payload["commence_time_utc"] == "2026-06-06T00:40:00Z"
    assert payload["minutes_projection_status"] == "projected"
    assert payload["minutes_confidence"] == "high"
    assert payload["minutes_projection_method"] == "weighted_season_recent"
    assert payload["projected_minutes"] == pytest.approx(36.99)
    assert payload["projected_minutes_low"] == pytest.approx(33.86)
    assert payload["projected_minutes_high"] == pytest.approx(40.12)
    assert basis["season_weight"] == pytest.approx(0.55)
    assert basis["recent_weight"] == pytest.approx(0.45)
    assert basis["basis_minutes"] == pytest.approx(35.74)
    assert basis["total_adjustment_clamped"] == pytest.approx(1.25)
    assert basis["last_game_minutes_context"]["used_as_weighted_input"] is False
    assert _adjustment_names(row) == {"confirmed_starter"}
    assert all(SHA256_RE.fullmatch(value) for value in payload["source_hashes"].values())


def test_first_class_projection_rejects_source_aliases_for_min_avg_and_min_recent() -> None:
    payload = _case_payload("stable_high_minute_starter")
    row = map_minutes_feature_case_fixture(payload)

    assert row.projected_minutes != payload["baseline"]["min_avg"]
    assert row.projected_minutes != payload["baseline"]["min_recent"]

    payload["baseline"]["projected_minutes"] = payload["baseline"]["min_avg"]
    with pytest.raises(NBAPlayerMinutesFeatureSchemaError, match="projected_minutes is calculated"):
        map_minutes_feature_case_fixture(payload)


def test_stable_starter_and_bench_results_are_deterministic() -> None:
    low_starter = _feature("stable_low_minute_starter")
    bench = _feature("stable_bench_player")

    assert low_starter.minutes_projection_status == "projected"
    assert low_starter.projected_minutes == pytest.approx(26.41)
    assert low_starter.minutes_confidence == "high"
    assert bench.minutes_projection_status == "projected"
    assert bench.projected_minutes == pytest.approx(16.29)
    assert _adjustment_names(bench) == {"confirmed_bench_role"}


def test_recent_role_increase_and_decrease_are_explicit_adjustments() -> None:
    increased = _feature("recent_role_increase")
    decreased = _feature("recent_role_decrease")

    assert increased.projected_minutes == pytest.approx(32.05)
    assert increased.minutes_confidence == "medium"
    assert "verified_role_increase" in _adjustment_names(increased)
    assert decreased.projected_minutes == pytest.approx(26.45)
    assert decreased.minutes_confidence == "medium"
    assert "verified_role_decrease" in _adjustment_names(decreased)


def test_missing_minutes_inputs_and_insufficient_data_statuses() -> None:
    missing_season = _feature("missing_season_minutes")
    missing_recent = _feature("missing_recent_minutes")
    insufficient_recent = _feature("insufficient_recent_sample")
    missing_both = _feature("missing_both_minutes")

    assert missing_season.minutes_projection_status == "projected"
    assert missing_season.minutes_projection_method == "recent_with_research_prior"
    assert missing_season.projected_minutes == pytest.approx(28.85)
    assert missing_recent.minutes_projection_status == "projected"
    assert missing_recent.minutes_projection_method == "season_with_research_prior"
    assert missing_recent.projected_minutes == pytest.approx(30.0)
    assert insufficient_recent.minutes_projection_status == "insufficient_data"
    assert insufficient_recent.projected_minutes is None
    assert "sample is below minimum" in insufficient_recent.minutes_exclusion_reason
    assert missing_both.minutes_projection_status == "insufficient_data"
    assert missing_both.projected_minutes is None


def test_high_recent_volatility_reduces_recent_weight_and_widens_bounds() -> None:
    stable = _feature("stable_high_minute_starter")
    volatile = _feature("high_recent_volatility")
    stable_basis = stable.to_dict()["unadjusted_minutes_basis"]
    volatile_basis = volatile.to_dict()["unadjusted_minutes_basis"]

    assert volatile_basis["recent_weight"] == pytest.approx(0.35)
    assert volatile_basis["volatility_penalty"] == "high"
    assert volatile.minutes_confidence == "medium"
    assert volatile.projected_minutes == pytest.approx(33.0)
    stable_width = stable.projected_minutes_high - stable.projected_minutes
    volatile_width = volatile.projected_minutes_high - volatile.projected_minutes
    assert volatile_width > stable_width


def test_confidence_and_uncertainty_for_questionable_and_lineup_unconfirmed() -> None:
    questionable = _feature("questionable_player")
    lineup_unconfirmed = _feature("lineup_unconfirmed")
    stable = _feature("stable_high_minute_starter")

    assert questionable.minutes_projection_status == "projected"
    assert questionable.minutes_confidence == "low"
    assert "questionable_status" in _adjustment_names(questionable)
    assert (questionable.projected_minutes_high - questionable.projected_minutes_low) > (
        stable.projected_minutes_high - stable.projected_minutes_low
    )
    assert lineup_unconfirmed.minutes_projection_status == "lineup_unconfirmed"
    assert lineup_unconfirmed.projected_minutes is not None
    assert lineup_unconfirmed.minutes_confidence == "low"
    assert "diagnostic" in lineup_unconfirmed.minutes_exclusion_reason


def test_adjustment_recording_and_global_positive_clamping() -> None:
    row = _feature(
        "recent_role_increase",
        {
            "role_context": {
                "teammate_absence_context": {
                    "verified": True,
                    "absent_teammates": ["Primary Creator"],
                    "role_impact_minutes": 3.0,
                    "review_note": "stacked adjustment clamp fixture",
                }
            }
        },
    )
    payload = row.to_dict()
    adjustments = payload["applied_adjustments"]

    assert {item["adjustment_name"] for item in adjustments} == {
        "confirmed_starter",
        "verified_role_increase",
        "confirmed_teammate_absence",
    }
    for adjustment in adjustments:
        assert set(adjustment) == {
            "adjustment_name",
            "input_evidence",
            "numeric_value",
            "maximum_allowed_magnitude",
            "source_timestamp_utc",
            "source_reference",
            "reason",
        }
    assert payload["unadjusted_minutes_basis"]["total_adjustment_raw"] == pytest.approx(7.25)
    assert payload["unadjusted_minutes_basis"]["total_adjustment_clamped"] == pytest.approx(6.0)
    assert payload["projected_minutes"] == pytest.approx(33.8)


def test_global_minutes_bounds_and_negative_minutes_validation() -> None:
    high_payload = _case_payload(
        "stable_high_minute_starter",
        {"baseline": {"min_avg": 60.0, "min_recent": 60.0}},
    )
    high_row = map_minutes_feature_case_fixture(high_payload)
    assert high_row.projected_minutes == pytest.approx(48.0)
    assert high_row.projected_minutes_high <= 48.0
    assert high_row.projected_minutes_low <= high_row.projected_minutes <= high_row.projected_minutes_high

    negative_payload = _case_payload("stable_high_minute_starter", {"baseline": {"min_avg": -1.0}})
    with pytest.raises(NBAPlayerMinutesFeatureSchemaError, match="season_minutes must be non-negative"):
        map_minutes_feature_case_fixture(negative_payload)


def test_inactive_dnp_minutes_restriction_teammate_and_schedule_behaviors() -> None:
    inactive = _feature("inactive_player")
    dnp = _feature("player_did_not_dress")
    restriction = _feature("confirmed_minutes_restriction")
    teammate = _feature("verified_teammate_absence")
    back_to_back = _feature("back_to_back_game")
    condensed = _feature("condensed_schedule")
    returning = _feature("returning_from_injury")

    assert inactive.minutes_projection_status == "inactive"
    assert inactive.projected_minutes is None
    assert inactive.minutes_confidence == "unavailable"
    assert dnp.minutes_projection_status == "did_not_dress"
    assert dnp.projected_minutes is None
    assert restriction.projected_minutes == pytest.approx(24.0)
    assert restriction.projected_minutes_high <= 24.0
    assert "confirmed_minutes_restriction" in _adjustment_names(restriction)
    assert "confirmed_teammate_absence" in _adjustment_names(teammate)
    assert "back_to_back_game" in _adjustment_names(back_to_back)
    assert "condensed_schedule" in _adjustment_names(condensed)
    assert "return_from_injury" in _adjustment_names(returning)
    assert returning.minutes_confidence == "medium"


def test_cutoff_boundary_timezone_and_target_game_leakage_protection() -> None:
    at_cutoff = _feature("observation_exactly_at_cutoff")
    assert at_cutoff.minutes_projection_status == "projected"

    for case_id, message in (
        ("observation_after_cutoff", "feature cutoff"),
        ("target_game_actual_minutes_supplied", "target-game actual minutes"),
        ("target_game_final_stat_row_supplied", "target-event final statistics"),
        ("timezone_naive_timestamp", "timezone-aware"),
    ):
        with pytest.raises(NBAPlayerMinutesFeatureSchemaError, match=message):
            _feature(case_id)

    post_tip_cutoff = _case_payload(
        "stable_high_minute_starter",
        {"feature_cutoff_timestamp_utc": "2026-06-06T00:41:00Z"},
    )
    with pytest.raises(
        NBAPlayerMinutesFeatureSchemaError,
        match="feature_timestamp_utc <= feature_cutoff_timestamp_utc < commence_time_utc",
    ):
        map_minutes_feature_case_fixture(post_tip_cutoff)


def test_unresolved_identity_and_conflicting_evidence_fail_closed_with_preserved_rows() -> None:
    unresolved_player = _feature("unresolved_player_identity")
    unresolved_event = _feature("unresolved_event_identity")
    conflicting = _feature("conflicting_status_evidence")

    assert unresolved_player.minutes_projection_status == "identity_unresolved"
    assert unresolved_player.player_id is None
    assert unresolved_player.projected_minutes is None
    assert unresolved_event.minutes_projection_status == "event_unresolved"
    assert unresolved_event.canonical_event_id is None
    assert unresolved_event.projected_minutes is None
    assert conflicting.minutes_projection_status == "conflicting"
    assert conflicting.projected_minutes is None
    assert conflicting.minutes_confidence == "unavailable"


def test_deterministic_repeated_output_source_hash_stability_and_fixture_immutability() -> None:
    before = _sha256(MINUTES_CASES_FIXTURE)
    first = _feature("source_fixture_immutability").to_dict()
    second = _feature("source_fixture_immutability").to_dict()
    after = _sha256(MINUTES_CASES_FIXTURE)

    assert first == second
    assert first["source_hashes"] == second["source_hashes"]
    assert after == before


def test_case_collection_validation_rejects_duplicates() -> None:
    first = _feature("stable_high_minute_starter")
    duplicate = _feature("stable_high_minute_starter")

    with pytest.raises(NBAPlayerMinutesFeatureSchemaError, match="duplicate projected-minutes feature identity"):
        validate_feature_rows([first, duplicate])

    distinct = _feature("stable_low_minute_starter")
    assert validate_feature_rows([first, distinct]) == (first, distinct)


def test_no_live_calls_and_no_production_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    before = _snapshot(tmp_path)

    with patch("requests.Session.get", side_effect=AssertionError("live call attempted")) as mock_get:
        row = _feature("stable_high_minute_starter")

    assert row.minutes_projection_status == "projected"
    assert mock_get.call_count == 0
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / "outputs").exists()
    assert not (tmp_path / "test_outputs").exists()
    assert not (tmp_path / "data" / "history").exists()


def test_individual_baseline_adapter_preserves_raw_source_row_and_cutoff_check() -> None:
    payload = _case_payload("stable_high_minute_starter")
    cutoff = datetime(2026, 6, 5, 18, 30, tzinfo=timezone.utc)
    baseline = map_player_baseline_fixture(payload["baseline"], feature_cutoff_timestamp_utc=cutoff)

    assert baseline.raw_source_row["min_avg"] == payload["baseline"]["min_avg"]
    assert baseline.raw_source_row["min_recent"] == payload["baseline"]["min_recent"]
    assert baseline.source_timestamp_utc <= cutoff
    assert SHA256_RE.fullmatch(baseline.source_hash)


def test_bulk_fixture_mapping_and_backward_compatibility_imports() -> None:
    rows = map_minutes_feature_cases_fixture(
        [_case_payload("stable_high_minute_starter"), _case_payload("stable_low_minute_starter")]
    )
    assert len(rows) == 2

    from courtvision.sports.nba import player_points_crosswalk, player_points_research, player_points_settlement

    assert player_points_research.NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION == "nba-player-points-research-v1"
    assert player_points_crosswalk.NBA_PLAYER_POINTS_CROSSWALK_SCHEMA_VERSION == "nba-player-points-crosswalk-v1"
    assert player_points_settlement.NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION == "nba-player-points-settlement-v1"
