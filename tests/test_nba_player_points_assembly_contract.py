from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from unittest.mock import patch

import pytest

from courtvision.sports.nba.player_points_assembly import (
    NBA_PLAYER_POINTS_ASSEMBLY_ROW_FIELDS,
    NBA_PLAYER_POINTS_ASSEMBLY_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_ASSEMBLY_STATUSES,
    NBA_PLAYER_POINTS_PROBABILITY_SUM_TOLERANCE,
    NBA_PLAYER_POINTS_PROJECTION_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_SOURCE_MANIFEST_SCHEMA_VERSION,
    NBAPlayerPointsAssemblyContractError,
    assemble_nba_player_points_batch,
    assembly_schema_definition,
    build_projection_evidence,
    build_source_manifest_preview,
    projection_evidence_schema,
    validate_assembled_rows,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "nba" / "player_points"
ASSEMBLY_CASES_FIXTURE = FIXTURE_ROOT / "assembly_cases.json"
ASSEMBLY_MODULE = (
    Path(__file__).resolve().parents[1]
    / "courtvision"
    / "sports"
    / "nba"
    / "player_points_assembly.py"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

REQUIRED_CASE_IDS = {
    "valid_projection_no_probabilities",
    "valid_probability_research",
    "missing_projected_minutes",
    "inactive_minutes_status",
    "lineup_unconfirmed_minutes_status",
    "unresolved_event_identity",
    "unresolved_player_identity",
    "market_timestamp_after_cutoff",
    "projection_timestamp_after_cutoff",
    "minutes_timestamp_after_cutoff",
    "conflicting_player_id",
    "conflicting_event_id",
    "team_mismatch",
    "opponent_mismatch",
    "operating_date_mismatch",
    "commence_time_mismatch",
    "missing_sportsbook",
    "missing_line",
    "missing_odds",
    "missing_market_source_hash",
    "missing_projection_source_hash",
    "missing_minutes_source_hash",
    "malformed_over_probability",
    "malformed_under_probability",
    "probabilities_not_summing_to_one",
    "probability_fields_without_source_version",
    "multiple_sportsbook_rows",
    "identical_duplicate_rows",
    "conflicting_duplicate_rows",
    "target_game_actual_points_leak",
    "target_game_actual_minutes_leak",
    "source_fixture_immutability",
    "deterministic_repeated_assembly",
    "input_order_independence",
    "manifest_hash_change_after_source_change",
}


def _load_fixture() -> dict[str, object]:
    return json.loads(ASSEMBLY_CASES_FIXTURE.read_text(encoding="utf-8"))


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


def _deep_merge(base: Mapping[str, object], overrides: Mapping[str, object]) -> dict[str, object]:
    merged = deepcopy(dict(base))
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _case_payload(
    case_id: str,
    extra_overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    fixture = _load_fixture()
    base = fixture["base_case"]
    cases = {case["case_id"]: case for case in fixture["cases"]}
    payload = _deep_merge(base, cases[case_id].get("overrides", {}))
    if extra_overrides:
        payload = _deep_merge(payload, extra_overrides)
    return payload


def _assemble(case_id: str, extra_overrides: Mapping[str, object] | None = None):
    result = assemble_nba_player_points_batch(
        [_case_payload(case_id, extra_overrides)],
        manifest_created_at_utc="2026-06-05T18:06:00Z",
    )
    return result.rows[0]


def _batch(*case_ids: str):
    return assemble_nba_player_points_batch(
        [_case_payload(case_id) for case_id in case_ids],
        manifest_created_at_utc="2026-06-05T18:06:00Z",
    )


def test_fixture_catalog_contains_all_required_offline_cases() -> None:
    fixture = _load_fixture()
    case_ids = {case["case_id"] for case in fixture["cases"]}
    fixture_text = ASSEMBLY_CASES_FIXTURE.read_text(encoding="utf-8").casefold()

    assert case_ids == REQUIRED_CASE_IDS
    assert len(case_ids) == 35
    assert "credential" not in fixture_text
    assert "secret" not in fixture_text
    assert "api_key" not in fixture_text


def test_schema_definitions_document_offline_contracts() -> None:
    projection_definition = projection_evidence_schema()
    assembly_definition = assembly_schema_definition()

    assert projection_definition["projection_schema_version"] == NBA_PLAYER_POINTS_PROJECTION_SCHEMA_VERSION
    assert "target-game actual points and actual minutes rejected" in projection_definition["constraints"]
    assert assembly_definition["assembly_schema_version"] == NBA_PLAYER_POINTS_ASSEMBLY_SCHEMA_VERSION
    assert set(assembly_definition["assembly_statuses"]) == set(NBA_PLAYER_POINTS_ASSEMBLY_STATUSES)
    assert assembly_definition["required_fields"] == list(NBA_PLAYER_POINTS_ASSEMBLY_ROW_FIELDS)
    assert assembly_definition["probability_sum_tolerance"] == NBA_PLAYER_POINTS_PROBABILITY_SUM_TOLERANCE
    assert assembly_definition["prediction_id_canonical_inputs"] == [
        "prediction_run_id",
        "canonical_event_id",
        "player_id",
        "sportsbook",
        "market",
        "line",
        "american_odds",
        "prediction_timestamp_utc",
        "model_id",
    ]
    assert assembly_definition["directional_diagnostic_label"] == (
        "non_probabilistic_projection_line_difference"
    )


def test_projection_evidence_schema_timestamp_cutoff_and_leakage_validation() -> None:
    payload = _case_payload("valid_projection_no_probabilities")
    projection = build_projection_evidence(
        payload["projection"],
        commence_time_utc=payload["crosswalk"]["commence_time_utc"],
    )

    assert projection.projected_points == 32.1
    assert projection.projection_schema_version == NBA_PLAYER_POINTS_PROJECTION_SCHEMA_VERSION

    with pytest.raises(NBAPlayerPointsAssemblyContractError, match="non-negative"):
        build_projection_evidence(
            {**payload["projection"], "projected_points": -0.1},
            commence_time_utc=payload["crosswalk"]["commence_time_utc"],
        )
    with pytest.raises(NBAPlayerPointsAssemblyContractError, match="timezone-aware"):
        build_projection_evidence(
            {**payload["projection"], "projection_timestamp_utc": "2026-06-05T18:05:00"},
            commence_time_utc=payload["crosswalk"]["commence_time_utc"],
        )
    with pytest.raises(NBAPlayerPointsAssemblyContractError, match="projection cutoff"):
        build_projection_evidence(
            {**payload["projection"], "projection_timestamp_utc": "2026-06-05T18:45:00Z"},
            commence_time_utc=payload["crosswalk"]["commence_time_utc"],
        )
    with pytest.raises(NBAPlayerPointsAssemblyContractError, match="target-game actual"):
        build_projection_evidence(
            {**payload["projection"], "target_game_actual_points": 33},
            commence_time_utc=payload["crosswalk"]["commence_time_utc"],
        )


def test_projection_research_row_does_not_fabricate_probabilities_or_edge() -> None:
    row = _assemble("valid_projection_no_probabilities")
    payload = row.to_dict()

    assert row.assembly_status == "eligible_projection_research"
    assert row.projection_research_eligible is True
    assert row.probability_research_eligible is False
    assert row.probability_status == "unavailable"
    assert row.model_over_probability is None
    assert row.model_under_probability is None
    assert row.probability_model_id is None
    assert row.probability_source_hash is None
    assert row.probability_based_edge is None
    assert row.model_edge is None
    assert row.selected_side is None
    assert row.projection_line_difference == pytest.approx(0.6)
    assert row.projected_points_above_line is True
    assert payload["directional_diagnostic_label"] == "non_probabilistic_projection_line_difference"
    assert "probability" not in payload["directional_diagnostic_label"].replace("non_probabilistic", "")
    assert SHA256_RE.fullmatch(row.assembled_record_hash)


def test_probability_research_requires_validated_probability_source_metadata() -> None:
    row = _assemble("valid_probability_research")

    assert row.assembly_status == "eligible_probability_research"
    assert row.projection_research_eligible is True
    assert row.probability_research_eligible is True
    assert row.probability_status == "valid"
    assert row.model_over_probability == pytest.approx(0.56)
    assert row.model_under_probability == pytest.approx(0.44)
    assert row.probability_source_id == "validated-prob-fixture-sga-001"
    assert row.probability_model_id == "nba-probability-validation-fixture-v1"
    assert SHA256_RE.fullmatch(str(row.probability_source_hash))
    assert row.probability_timestamp_utc < row.commence_time_utc


@pytest.mark.parametrize(
    ("case_id", "expected_status", "reason_fragment"),
    [
        ("missing_projected_minutes", "excluded", "projected_minutes is required"),
        ("inactive_minutes_status", "excluded", "minutes_projection_status=inactive"),
        ("lineup_unconfirmed_minutes_status", "excluded", "diagnostic-only"),
        ("unresolved_event_identity", "excluded", "resolved event identity is required"),
        ("unresolved_player_identity", "excluded", "resolved player identity is required"),
        ("missing_sportsbook", "excluded", "sportsbook is required"),
        ("missing_line", "excluded", "line is required"),
        ("missing_odds", "excluded", "american_odds is required"),
    ],
)
def test_excluded_rows_are_preserved_with_explicit_reasons(
    case_id: str,
    expected_status: str,
    reason_fragment: str,
) -> None:
    row = _assemble(case_id)

    assert row.assembly_status == expected_status
    assert row.projection_research_eligible is False
    assert reason_fragment in row.assembly_exclusion_reason
    assert row.to_dict()["prediction_id"].startswith("nba-pp-preview-")


@pytest.mark.parametrize(
    ("case_id", "reason_fragment"),
    [
        ("market_timestamp_after_cutoff", "market timestamp is after cutoff"),
        ("projection_timestamp_after_cutoff", "projection_timestamp_utc must be at or before projection cutoff"),
        ("minutes_timestamp_after_cutoff", "minutes timestamp is after cutoff"),
        ("conflicting_player_id", "player IDs disagree"),
        ("conflicting_event_id", "canonical event IDs disagree"),
        ("team_mismatch", "teams disagree"),
        ("opponent_mismatch", "opponents disagree"),
        ("operating_date_mismatch", "operating dates disagree"),
        ("commence_time_mismatch", "commence times disagree beyond approved tolerance"),
        ("missing_market_source_hash", "market_source_hash is required"),
        ("missing_projection_source_hash", "projection_source_hash is required"),
        ("missing_minutes_source_hash", "minutes_source_hashes.baseline is required"),
    ],
)
def test_conflicting_rows_fail_closed_but_remain_visible(
    case_id: str,
    reason_fragment: str,
) -> None:
    row = _assemble(case_id)

    assert row.assembly_status == "conflicting"
    assert row.projection_research_eligible is False
    assert reason_fragment in row.assembly_exclusion_reason


def test_unsupported_schema_versions_fail_closed() -> None:
    row = _assemble(
        "valid_projection_no_probabilities",
        {
            "market": {"market_schema_version": "nba-player-points-market-v2"},
            "minutes": {"feature_schema_version": "nba-player-minutes-feature-v2"},
        },
    )

    assert row.assembly_status == "conflicting"
    assert "unsupported market_schema_version" in row.assembly_exclusion_reason
    assert "unsupported feature_schema_version" in row.assembly_exclusion_reason


@pytest.mark.parametrize(
    ("case_id", "probability_status"),
    [
        ("malformed_over_probability", "malformed"),
        ("malformed_under_probability", "malformed"),
        ("probabilities_not_summing_to_one", "malformed"),
        ("probability_fields_without_source_version", "incomplete"),
    ],
)
def test_malformed_probability_evidence_does_not_block_projection_research(
    case_id: str,
    probability_status: str,
) -> None:
    row = _assemble(case_id)

    assert row.assembly_status == "eligible_projection_research"
    assert row.projection_research_eligible is True
    assert row.probability_research_eligible is False
    assert row.probability_status == probability_status
    assert row.probability_based_edge is None


def test_malformed_probability_claiming_probability_eligibility_conflicts() -> None:
    row = _assemble(
        "malformed_over_probability",
        {"probability": {"claims_probability_eligibility": True}},
    )

    assert row.assembly_status == "conflicting"
    assert row.probability_research_eligible is False
    assert "claimed probability eligibility" in row.assembly_exclusion_reason


def test_leakage_rows_are_quarantined() -> None:
    points = _assemble("target_game_actual_points_leak")
    minutes = _assemble("target_game_actual_minutes_leak")

    assert points.assembly_status == "quarantined"
    assert minutes.assembly_status == "quarantined"
    assert "target-game actual" in points.assembly_exclusion_reason
    assert "target-game actual" in minutes.assembly_exclusion_reason


def test_prediction_ids_and_hashes_are_deterministic_and_market_sensitive() -> None:
    first = _assemble("deterministic_repeated_assembly")
    second = _assemble("deterministic_repeated_assembly")
    alternate_market = _assemble("multiple_sportsbook_rows")

    assert first.prediction_id == second.prediction_id
    assert first.assembled_record_hash == second.assembled_record_hash
    assert first.prediction_id != alternate_market.prediction_id
    assert first.assembled_record_hash != alternate_market.assembled_record_hash
    assert "settlement" not in json.dumps(first.to_dict(), sort_keys=True)
    assert "closing" not in json.dumps(first.to_dict(), sort_keys=True)


def test_duplicate_idempotency_and_conflicting_duplicate_handling() -> None:
    identical = _batch("identical_duplicate_rows", "identical_duplicate_rows")
    conflicting = _batch("identical_duplicate_rows", "conflicting_duplicate_rows")

    assert len(identical.rows) == 1
    assert identical.duplicate_diagnostics[0].duplicate_status == "identical_collapsed"
    assert identical.rows[0].assembly_status == "eligible_projection_research"

    assert len(conflicting.rows) == 2
    assert {row.assembly_status for row in conflicting.rows} == {"conflicting"}
    assert conflicting.duplicate_diagnostics[0].duplicate_status == "conflicting"
    assert conflicting.batch_summary_counts["conflicting_rows"] == 2


def test_source_manifest_preview_is_stable_sensitive_and_contains_no_raw_secret_material() -> None:
    base = _case_payload("valid_projection_no_probabilities")
    changed = _case_payload("manifest_hash_change_after_source_change")
    first = assemble_nba_player_points_batch([base], manifest_created_at_utc="2026-06-05T18:06:00Z")
    second = assemble_nba_player_points_batch([base], manifest_created_at_utc="2026-06-05T18:06:00Z")
    changed_result = assemble_nba_player_points_batch([changed], manifest_created_at_utc="2026-06-05T18:06:00Z")

    manifest = first.source_manifest_preview.to_dict()
    assert manifest["manifest_schema_version"] == NBA_PLAYER_POINTS_SOURCE_MANIFEST_SCHEMA_VERSION
    assert first.source_manifest_preview.manifest_hash == second.source_manifest_preview.manifest_hash
    assert first.source_manifest_preview.manifest_hash != changed_result.source_manifest_preview.manifest_hash
    assert SHA256_RE.fullmatch(first.source_manifest_preview.manifest_hash)
    assert all(record["source_hash"] for record in manifest["source_records"])
    assert "secret" not in json.dumps(manifest, sort_keys=True).casefold()

    direct_manifest = build_source_manifest_preview(
        [base],
        prediction_run_id=base["provenance"]["prediction_run_id"],
        operating_date=base["crosswalk"]["operating_date"],
        created_at_utc="2026-06-05T18:06:00Z",
        repository_commit_sha=base["provenance"]["repository_commit_sha"],
    )
    assert direct_manifest.manifest_hash == first.source_manifest_preview.manifest_hash


def test_batch_summary_counts_excluded_rows_and_canonical_ordering() -> None:
    result = assemble_nba_player_points_batch(
        [
            _case_payload("multiple_sportsbook_rows"),
            _case_payload("valid_probability_research"),
            _case_payload(
                "missing_projected_minutes",
                {
                    "market": {
                        "sportsbook": "Caesars",
                        "market_source_hash": "7777777777777777777777777777777777777777777777777777777777777777",
                    }
                },
            ),
            _case_payload(
                "target_game_actual_points_leak",
                {
                    "market": {
                        "sportsbook": "BetRivers",
                        "market_source_hash": "8888888888888888888888888888888888888888888888888888888888888888",
                    }
                },
            ),
            _case_payload(
                "team_mismatch",
                {
                    "market": {
                        "sportsbook": "PointsBet",
                        "market_source_hash": "9999999999999999999999999999999999999999999999999999999999999999",
                    }
                },
            ),
        ],
        manifest_created_at_utc="2026-06-05T18:06:00Z",
    )

    assert result.batch_summary_counts == {
        "total_rows": 5,
        "eligible_projection_rows": 2,
        "eligible_probability_rows": 1,
        "excluded_rows": 1,
        "quarantined_rows": 1,
        "conflicting_rows": 1,
        "duplicate_diagnostics": 0,
    }
    assert len(result.eligible_projection_rows) == 2
    assert len(result.eligible_probability_rows) == 1
    assert len(result.excluded_rows) == 1
    assert len(result.quarantined_rows) == 1
    assert len(result.conflicting_rows) == 1
    assert [row.assembly_status for row in result.rows] == [
        "eligible_probability_research",
        "eligible_projection_research",
        "excluded",
        "quarantined",
        "conflicting",
    ]


def test_input_order_independence_for_ids_hashes_manifest_and_output_order() -> None:
    forward = _batch(
        "valid_projection_no_probabilities",
        "input_order_independence",
        "valid_probability_research",
    )
    reverse = assemble_nba_player_points_batch(
        [
            _case_payload("valid_probability_research"),
            _case_payload("input_order_independence"),
            _case_payload("valid_projection_no_probabilities"),
        ],
        manifest_created_at_utc="2026-06-05T18:06:00Z",
    )

    assert [row.prediction_id for row in forward.rows] == [row.prediction_id for row in reverse.rows]
    assert [row.assembled_record_hash for row in forward.rows] == [
        row.assembled_record_hash for row in reverse.rows
    ]
    assert forward.source_manifest_preview.manifest_hash == reverse.source_manifest_preview.manifest_hash


def test_validate_assembled_rows_rejects_conflicting_duplicate_ids() -> None:
    result = _batch("valid_projection_no_probabilities", "multiple_sportsbook_rows")
    assert validate_assembled_rows(result.rows) == result.rows

    conflicting = _batch("identical_duplicate_rows", "conflicting_duplicate_rows")
    with pytest.raises(NBAPlayerPointsAssemblyContractError, match="conflicting duplicate"):
        validate_assembled_rows(conflicting.rows)


def test_source_fixture_immutability_no_live_calls_no_environment_reads_and_no_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before_fixture_hash = _sha256(ASSEMBLY_CASES_FIXTURE)
    payload = _case_payload("source_fixture_immutability")
    monkeypatch.chdir(tmp_path)
    before = _snapshot(tmp_path)

    with (
        patch("requests.Session.get", side_effect=AssertionError("live call attempted")) as mock_get,
        patch("os.getenv", side_effect=AssertionError("environment read attempted")) as mock_getenv,
    ):
        result = assemble_nba_player_points_batch(
            [payload],
            manifest_created_at_utc="2026-06-05T18:06:00Z",
        )

    assert result.rows[0].assembly_status == "eligible_projection_research"
    assert mock_get.call_count == 0
    assert mock_getenv.call_count == 0
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / "outputs").exists()
    assert not (tmp_path / "test_outputs").exists()
    assert _sha256(ASSEMBLY_CASES_FIXTURE) == before_fixture_hash


def test_architectural_boundary_and_backward_compatibility_imports() -> None:
    source_text = ASSEMBLY_MODULE.read_text(encoding="utf-8")

    assert "courtvision_ai" not in source_text
    assert "run_today" not in source_text
    assert "kelly_" not in source_text.casefold()
    assert "bankroll_" not in source_text.casefold()
    assert "import kelly" not in source_text.casefold()
    assert "import bankroll" not in source_text.casefold()
    assert "requests" not in source_text
    assert "os.environ" not in source_text
    assert "sports.mlb" not in source_text

    from courtvision.sports.nba import player_minutes_research, player_points_crosswalk
    from courtvision.sports.nba import player_points_research, player_points_settlement

    assert player_points_research.NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION == "nba-player-points-research-v1"
    assert player_points_crosswalk.NBA_PLAYER_POINTS_CROSSWALK_SCHEMA_VERSION == "nba-player-points-crosswalk-v1"
    assert player_points_settlement.NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION == "nba-player-points-settlement-v1"
    assert player_minutes_research.NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION == "nba-player-minutes-feature-v1"


def test_prediction_timestamps_are_utc_and_before_tipoff() -> None:
    row = _assemble("valid_projection_no_probabilities")

    assert row.prediction_timestamp_utc.tzinfo == timezone.utc
    assert row.market_timestamp_utc.tzinfo == timezone.utc
    assert row.feature_timestamp_utc.tzinfo == timezone.utc
    assert row.prediction_timestamp_utc < row.commence_time_utc
    assert row.market_timestamp_utc < row.commence_time_utc
    assert row.feature_timestamp_utc < row.commence_time_utc
