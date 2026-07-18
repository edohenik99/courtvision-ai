from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import courtvision.sports.nba.player_points_research_runner as runner_module
from courtvision.sports.nba.player_points_research_runner import (
    EXIT_CODES,
    MANUAL_RUN_BUNDLE_SCHEMA_VERSION,
    NBAPlayerPointsApprovalDigestMismatchError,
    NBAPlayerPointsBundleError,
    NBAPlayerPointsIntegrityError,
    NBAPlayerPointsManualRunnerError,
    NBAPlayerPointsPathSecurityError,
    NBAPlayerPointsPlanNotPublishableError,
    NBAPlayerPointsPrerequisiteEvidenceError,
    NBAPlayerPointsPublicationConflictError,
    SUPPORTED_OPERATIONS,
    bundle_schema_definition,
    run_manual_bundle,
)
from courtvision.sports.nba.player_points_settlement_closing_binding import (
    NBA_PLAYER_POINTS_MANUAL_PLAN_V2_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_CONTRACT_VERSION,
    NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "nba"
PROVIDER_SHAPES_FIXTURE = (
    FIXTURE_ROOT / "player_points_source_adapters" / "provider_shapes.json"
)
RUNNER_MODULE = (
    PROJECT_ROOT
    / "courtvision"
    / "sports"
    / "nba"
    / "player_points_research_runner.py"
)
CLI = PROJECT_ROOT / "tools" / "run_nba_player_points_research.py"


def _current_sha() -> str:
    return subprocess.check_output(
        [
            "git",
            "-c",
            f"safe.directory={PROJECT_ROOT.as_posix()}",
            "rev-parse",
            "HEAD",
        ],
        cwd=PROJECT_ROOT,
        text=True,
    ).strip()


def _current_branch() -> str:
    return subprocess.check_output(
        [
            "git",
            "-c",
            f"safe.directory={PROJECT_ROOT.as_posix()}",
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
        ],
        cwd=PROJECT_ROOT,
        text=True,
    ).strip()


def _clean_git_output(args: object, repository_root: Path) -> str:
    command = tuple(args)
    if command == ("rev-parse", "HEAD"):
        return _current_sha() + "\n"
    if command == ("rev-parse", "--abbrev-ref", "HEAD"):
        return _current_branch() + "\n"
    if command in {
        ("diff", "--cached", "--name-only"),
        ("diff", "--name-only"),
        ("ls-files", "-u"),
        ("ls-files", "--others", "--exclude-standard"),
    }:
        return ""
    raise AssertionError(f"unexpected git command: {command}")


@pytest.fixture(autouse=True)
def _default_clean_repository_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_module, "_run_git_readonly", _clean_git_output)


def _load_fixture() -> dict[str, object]:
    return json.loads(PROVIDER_SHAPES_FIXTURE.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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


def _paths(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "input_root": tmp_path / "inputs",
        "preview_root": tmp_path / "previews",
        "evidence_root": tmp_path / "evidence",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _write_base_inputs(
    tmp_path: Path,
    *,
    projection_overrides: dict[str, object] | None = None,
    final_stats_payload: dict[str, object] | None = None,
    closing_payload: dict[str, object] | None = None,
    duplicate_projection: bool = False,
) -> dict[str, Path]:
    fixture = _load_fixture()
    paths = _paths(tmp_path)
    inputs = paths["input_root"]
    minutes = deepcopy(fixture["minutes_inputs"])
    minutes["repository_commit_sha"] = _current_sha()
    projection = deepcopy(fixture["projection_fixture"])
    if projection_overrides:
        projection.update(projection_overrides)
    _write_json(inputs / "pregame_odds.json", fixture["pregame_odds"])
    _write_json(inputs / "schedule_identity.json", fixture["schedule_identity"])
    _write_json(inputs / "minutes_inputs.json", minutes)
    _write_json(inputs / "projection_evidence.json", projection)
    if duplicate_projection:
        _write_json(inputs / "projection_evidence_b.json", projection)
    _write_json(inputs / "closing_odds.json", closing_payload or fixture["closing_odds"])
    _write_json(inputs / "final_stats.json", final_stats_payload or fixture["final_stats"])
    return paths


def _pregame_bundle(
    tmp_path: Path,
    *,
    run_id: str = "run-nba-points-manual-test",
    operation_stage: str = "pregame-plan",
    projection_overrides: dict[str, object] | None = None,
    duplicate_projection: bool = False,
    reverse_projection_order: bool = False,
    policy_version: str = "1.0",
    repository_commit_sha: str | None = None,
    repository_branch: str | None = None,
) -> tuple[Path, dict[str, object], dict[str, Path]]:
    paths = _write_base_inputs(
        tmp_path,
        projection_overrides=projection_overrides,
        duplicate_projection=duplicate_projection,
    )
    projection_refs = ["projection_evidence.json"]
    if duplicate_projection:
        projection_refs = ["projection_evidence.json", "projection_evidence_b.json"]
        if reverse_projection_order:
            projection_refs = list(reversed(projection_refs))
    bundle: dict[str, object] = {
        "bundle_schema_version": MANUAL_RUN_BUNDLE_SCHEMA_VERSION,
        "operation_stage": operation_stage,
        "operating_date": "2026-06-05",
        "repository_commit_sha": repository_commit_sha or _current_sha(),
        "research_label": "research_only_not_for_betting",
        "input_root": str(paths["input_root"]),
        "preview_root": str(paths["preview_root"]),
        "evidence_root": str(paths["evidence_root"]),
        "operator_id": "manual-research-operator",
        "requested_at_utc": "2026-06-05T18:09:00Z",
        "pregame": {
            "prediction_run_id": run_id,
            "pregame_odds_payloads": ["pregame_odds.json"],
            "schedule_identity_payloads": ["schedule_identity.json"],
            "minutes_payloads": ["minutes_inputs.json"],
            "projection_payloads": projection_refs,
            "feature_cutoff_timestamp_utc": "2026-06-05T18:30:00Z",
            "prediction_timestamp_utc": "2026-06-05T18:08:00Z",
            "model_id": "nba-player-points-manual-model-v1",
            "pregame_policy_id": "nba-player-points-manual-pregame-v1",
            "pregame_policy_version": policy_version,
        },
    }
    if repository_branch is not None:
        bundle["repository_branch"] = repository_branch
    bundle_path = tmp_path / f"{run_id}.json"
    _write_json(bundle_path, bundle)
    return bundle_path, bundle, paths


def _publish_pregame(tmp_path: Path, *, run_id: str = "run-nba-points-manual-pub"):
    bundle_path, bundle, paths = _pregame_bundle(tmp_path, run_id=run_id)
    plan = run_manual_bundle(bundle_path)
    receipt = run_manual_bundle(
        bundle_path,
        publish=True,
        approval_digest=plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-05T18:12:00Z",
    )
    return bundle_path, bundle, paths, plan, receipt


def _ledger_rows(evidence_root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in (evidence_root / "nba_player_points_evidence" / "ledgers" / "segments").glob(
        "*/*/prediction_ledger.jsonl"
    ):
        rows.extend(_read_jsonl(path))
    return rows


def _draftkings_prediction(evidence_root: Path) -> dict[str, object]:
    return next(row for row in _ledger_rows(evidence_root) if row["sportsbook"] == "DraftKings")


def _closing_bundle(
    tmp_path: Path,
    paths: dict[str, Path],
    prediction_id: str,
    *,
    closing_payload: dict[str, object] | None = None,
) -> Path:
    if closing_payload is not None:
        _write_json(paths["input_root"] / "closing_odds.json", closing_payload)
    bundle = {
        "bundle_schema_version": MANUAL_RUN_BUNDLE_SCHEMA_VERSION,
        "operation_stage": "closing-plan",
        "operating_date": "2026-06-05",
        "repository_commit_sha": _current_sha(),
        "research_label": "research_only_not_for_betting",
        "input_root": str(paths["input_root"]),
        "preview_root": str(tmp_path / "closing_previews"),
        "evidence_root": str(paths["evidence_root"]),
        "operator_id": "manual-research-operator",
        "requested_at_utc": "2026-06-06T00:39:30Z",
        "closing": {
            "closing_batch_id": "manual-closing-batch-001",
            "selection_batch_id": "manual-selection-batch-001",
            "prediction_references": {
                "sga_draftkings": {
                    "prediction_id": prediction_id,
                }
            },
            "closing_policy_id": "nba-player-points-same-book-latest-pre-tip-v1",
            "closing_policy_version": "1.0",
            "closing_odds_payloads": ["closing_odds.json"],
            "collection_timestamp_utc": "2026-06-06T00:39:30Z",
        },
    }
    path = tmp_path / "closing_bundle.json"
    _write_json(path, bundle)
    return path


def _single_final_stats(prediction_row: dict[str, object], source_row_id: str = "final-sga-valid") -> dict[str, object]:
    fixture = _load_fixture()
    payload = deepcopy(fixture["final_stats"])
    player = next(
        deepcopy(row)
        for row in payload["games"][0]["players"]
        if row["source_row_id"] == source_row_id
    )
    player["prediction_id"] = prediction_row["prediction_id"]
    player["player_id"] = prediction_row["player_id"]
    player["canonical_event_id"] = prediction_row["canonical_event_id"]
    payload["games"][0]["players"] = [player]
    return payload


def _settlement_bundle(
    tmp_path: Path,
    paths: dict[str, Path],
    prediction_row: dict[str, object],
    *,
    final_stats_payload: dict[str, object],
) -> Path:
    _write_json(paths["input_root"] / "final_stats.json", final_stats_payload)
    bundle = {
        "bundle_schema_version": MANUAL_RUN_BUNDLE_SCHEMA_VERSION,
        "operation_stage": "settlement-plan",
        "operating_date": "2026-06-05",
        "repository_commit_sha": _current_sha(),
        "research_label": "research_only_not_for_betting",
        "input_root": str(paths["input_root"]),
        "preview_root": str(tmp_path / "settlement_previews"),
        "evidence_root": str(paths["evidence_root"]),
        "operator_id": "manual-research-operator",
        "requested_at_utc": "2026-06-06T04:00:00Z",
        "settlement": {
            "settlement_batch_id": "manual-settlement-batch-001",
            "prediction_ids": [prediction_row["prediction_id"]],
            "settlement_policy_id": "nba-player-points-offline-final-stats-settlement-v1",
            "settlement_policy_version": "1.0",
            "final_stat_payloads": ["final_stats.json"],
            "settlement_timestamp_utc": "2026-06-06T04:00:00Z",
        },
    }
    path = tmp_path / "settlement_bundle.json"
    _write_json(path, bundle)
    return path


def _settlement_v2_bundle(
    tmp_path: Path,
    paths: dict[str, Path],
    prediction_row: dict[str, object],
    *,
    final_stats_payload: dict[str, object],
    logical_settlement_batch_id: str = "manual-settlement-v2-001",
) -> Path:
    _write_json(paths["input_root"] / "final_stats.json", final_stats_payload)
    evidence_root = paths["evidence_root"] / "nba_player_points_evidence"
    observation_manifests = sorted(
        evidence_root.glob("closing/observations/segments/*/*/closing_manifest.json")
    )
    selection_manifests = sorted(
        evidence_root.glob("closing/selections/segments/*/*/selection_manifest.json")
    )
    assert observation_manifests and len(selection_manifests) == 1
    observation_batch_ids = [
        _read_json(path)["closing_batch_id"] for path in observation_manifests
    ]
    selection_manifest = _read_json(selection_manifests[0])
    bundle = {
        "bundle_schema_version": NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION,
        "operation_stage": "settlement-plan",
        "operating_date": "2026-06-05",
        "repository_commit_sha": _current_sha(),
        "research_label": "research_only_not_for_betting",
        "input_root": str(paths["input_root"]),
        "preview_root": str(tmp_path / "settlement_v2_previews"),
        "evidence_root": str(paths["evidence_root"]),
        "operator_id": "manual-research-operator",
        "requested_at_utc": "2026-06-06T04:00:00Z",
        "settlement": {
            "logical_settlement_batch_id": logical_settlement_batch_id,
            "physical_closing_selection_batch_id": selection_manifest[
                "selection_batch_id"
            ],
            "expected_physical_observation_batch_ids": observation_batch_ids,
            "expected_closing_policy_id": (
                "nba-player-points-same-book-latest-pre-tip-v1"
            ),
            "expected_closing_policy_version": "1.0",
            "requested_prediction_ids": [prediction_row["prediction_id"]],
            "settlement_policy_id": (
                "nba-player-points-offline-final-stats-settlement-v1"
            ),
            "settlement_policy_version": "1.0",
            "final_stat_input_files": ["final_stats.json"],
            "settlement_timestamp_utc": "2026-06-06T04:00:00Z",
        },
    }
    path = tmp_path / "settlement_v2_bundle.json"
    _write_json(path, bundle)
    return path


def test_bundle_schema_documents_operations_and_exit_codes() -> None:
    schema = bundle_schema_definition()
    assert schema["bundle_schema_version"] == MANUAL_RUN_BUNDLE_SCHEMA_VERSION
    assert set(schema["supported_operations"]) == set(SUPPORTED_OPERATIONS)
    assert EXIT_CODES["success"] == 0
    assert EXIT_CODES["approval_digest_mismatch"] == 4
    assert "evidence_root" in schema["required_common_fields"]


def test_dry_run_defaults_to_plan_and_leaves_evidence_root_unchanged(tmp_path: Path) -> None:
    bundle_path, _, paths = _pregame_bundle(tmp_path)
    before = _snapshot(paths["evidence_root"])
    result = run_manual_bundle(bundle_path)
    after = _snapshot(paths["evidence_root"])

    assert result["exit_code"] == 0
    assert result["publication_attempted"] is False
    assert result["plan"]["publishability"]["allowed"] is True
    assert before == after == ()
    assert {path.name for path in paths["preview_root"].iterdir()} == {
        "plan.json",
        "source_summary.json",
        "normalized_preview.json",
        "eligibility_summary.json",
        "conflict_report.json",
        "integrity_preview.json",
        "approval_request.json",
    }


def test_approval_digest_is_deterministic_and_bundle_bytes_are_bound(tmp_path: Path) -> None:
    first_path, bundle, _ = _pregame_bundle(tmp_path, duplicate_projection=True)
    first = run_manual_bundle(first_path)["plan"]["approval_digest"]
    bundle["pregame"]["projection_payloads"] = list(
        reversed(bundle["pregame"]["projection_payloads"])
    )
    second_path = tmp_path / "reversed_projection_order.json"
    _write_json(second_path, bundle)
    second = run_manual_bundle(second_path)["plan"]["approval_digest"]

    assert first == run_manual_bundle(first_path)["plan"]["approval_digest"]
    assert first != second


@pytest.mark.parametrize(
    ("mutator", "expected_change"),
    [
        (lambda path, bundle: _write_json(path.parent / "inputs" / "projection_evidence.json", {**_load_fixture()["projection_fixture"], "projected_points": 33.7}), "source"),
        (lambda path, bundle: bundle["pregame"].update({"pregame_policy_version": "1.1"}), "policy"),
        (lambda path, bundle: bundle.update({"repository_commit_sha": "0" * 40}), "repository"),
    ],
)
def test_material_plan_changes_alter_digest(tmp_path: Path, mutator, expected_change: str) -> None:
    bundle_path, bundle, _ = _pregame_bundle(tmp_path)
    original = run_manual_bundle(bundle_path)["plan"]["approval_digest"]
    mutator(bundle_path, bundle)
    if expected_change != "source":
        _write_json(bundle_path, bundle)
    changed = run_manual_bundle(bundle_path)["plan"]["approval_digest"]
    assert changed != original


def test_publish_requires_exact_digest_timestamp_and_operator(tmp_path: Path) -> None:
    bundle_path, bundle, _ = _pregame_bundle(tmp_path)
    plan = run_manual_bundle(bundle_path)

    with pytest.raises(NBAPlayerPointsApprovalDigestMismatchError):
        run_manual_bundle(bundle_path, publish=True)
    with pytest.raises(NBAPlayerPointsApprovalDigestMismatchError):
        run_manual_bundle(
            bundle_path,
            publish=True,
            approval_digest="0" * 64,
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-05T18:12:00Z",
        )
    with pytest.raises(NBAPlayerPointsBundleError):
        run_manual_bundle(
            bundle_path,
            publish=True,
            approval_digest=plan["plan"]["approval_digest"],
            approval_timestamp_utc="2026-06-05T18:12:00Z",
        )
    bundle.pop("operator_id")
    _write_json(bundle_path, bundle)
    with pytest.raises(NBAPlayerPointsBundleError):
        run_manual_bundle(
            bundle_path,
            publish=True,
            approval_digest=plan["plan"]["approval_digest"],
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-05T18:12:00Z",
        )


def test_correct_approval_publishes_prediction_evidence_idempotently(tmp_path: Path) -> None:
    bundle_path, _, paths = _pregame_bundle(tmp_path)
    input_before = _snapshot(paths["input_root"])
    plan = run_manual_bundle(bundle_path)
    first = run_manual_bundle(
        bundle_path,
        publish=True,
        approval_digest=plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-05T18:12:00Z",
    )
    evidence_after_first = _snapshot(paths["evidence_root"])
    second = run_manual_bundle(
        bundle_path,
        publish=True,
        approval_digest=plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-05T18:20:00Z",
    )

    assert first["receipt"]["publication_result"]["completion_status"] == "complete"
    assert second["receipt"]["publication_result"]["completion_status"] == "already_complete"
    assert first["receipt"]["operator_id"] == "manual-research-operator"
    assert second["receipt"]["publication_semantics"] == "idempotent_replay"
    run_manifest = Path(first["receipt"]["evidence_segment_references"]["run_directory"]) / "run_manifest.json"
    assert first["receipt"]["evidence_manifest_hashes"]["run_manifest.json"] == _sha256(run_manifest)
    assert _snapshot(paths["evidence_root"]) == evidence_after_first
    assert _snapshot(paths["input_root"]) == input_before
    assert not (paths["evidence_root"] / "nba_player_points_evidence" / "closing").exists()
    assert not (paths["evidence_root"] / "nba_player_points_evidence" / "settlement").exists()
    assert (paths["preview_root"] / "approval_receipt.json").exists()


def test_same_prediction_run_id_with_changed_content_conflicts(tmp_path: Path) -> None:
    bundle_path, _, paths = _pregame_bundle(
        tmp_path.parent / "same",
        run_id="r-same",
    )
    plan = run_manual_bundle(bundle_path)
    run_manual_bundle(
        bundle_path,
        publish=True,
        approval_digest=plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-05T18:12:00Z",
    )
    projection = deepcopy(_load_fixture()["projection_fixture"])
    projection["projected_points"] = 34.2
    _write_json(paths["input_root"] / "projection_evidence.json", projection)
    changed_plan = run_manual_bundle(bundle_path)

    with pytest.raises(NBAPlayerPointsPublicationConflictError):
        run_manual_bundle(
            bundle_path,
            publish=True,
            approval_digest=changed_plan["plan"]["approval_digest"],
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-05T18:21:00Z",
        )


def test_failed_publications_do_not_write_success_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_path, bundle, paths = _pregame_bundle(tmp_path)
    plan = run_manual_bundle(bundle_path)
    projection = deepcopy(_load_fixture()["projection_fixture"])
    projection["projected_points"] = 33.0
    _write_json(paths["input_root"] / "projection_evidence.json", projection)
    with pytest.raises(NBAPlayerPointsApprovalDigestMismatchError):
        run_manual_bundle(
            bundle_path,
            publish=True,
            approval_digest=plan["plan"]["approval_digest"],
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-05T18:12:00Z",
        )
    assert not (paths["preview_root"] / "approval_receipt.json").exists()

    conflict_path, conflict_bundle, conflict_paths = _pregame_bundle(
        tmp_path / "c",
        run_id="run-conflict-r",
    )
    conflict_plan = run_manual_bundle(conflict_path)
    run_manual_bundle(
        conflict_path,
        publish=True,
        approval_digest=conflict_plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-05T18:12:00Z",
    )
    changed_projection = deepcopy(_load_fixture()["projection_fixture"])
    changed_projection["projected_points"] = 34.0
    _write_json(conflict_paths["input_root"] / "projection_evidence.json", changed_projection)
    conflict_bundle["preview_root"] = str(tmp_path / "conflict_failure_preview")
    _write_json(conflict_path, conflict_bundle)
    changed_plan = run_manual_bundle(conflict_path)
    with pytest.raises(NBAPlayerPointsPublicationConflictError):
        run_manual_bundle(
            conflict_path,
            publish=True,
            approval_digest=changed_plan["plan"]["approval_digest"],
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-05T18:21:00Z",
        )
    assert not (tmp_path / "conflict_failure_preview" / "approval_receipt.json").exists()

    class FailedVerifier:
        ok = False
        violations = ("forced verifier failure",)

        def to_dict(self) -> dict[str, object]:
            return {"ok": False, "violations": list(self.violations)}

    verify_path, _, verify_paths = _pregame_bundle(
        tmp_path.parent / "vf",
        run_id="r-vf",
    )
    verify_plan = run_manual_bundle(verify_path)
    monkeypatch.setattr(
        runner_module,
        "verify_nba_player_points_evidence",
        lambda *args, **kwargs: FailedVerifier(),
    )
    with pytest.raises(NBAPlayerPointsIntegrityError):
        run_manual_bundle(
            verify_path,
            publish=True,
            approval_digest=verify_plan["plan"]["approval_digest"],
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-05T18:12:00Z",
        )
    assert not (verify_paths["preview_root"] / "approval_receipt.json").exists()


def test_changed_source_policy_or_repository_after_planning_causes_approval_mismatch(tmp_path: Path) -> None:
    bundle_path, bundle, paths = _pregame_bundle(tmp_path)
    digest = run_manual_bundle(bundle_path)["plan"]["approval_digest"]
    projection = deepcopy(_load_fixture()["projection_fixture"])
    projection["projected_points"] = 33.0
    _write_json(paths["input_root"] / "projection_evidence.json", projection)
    with pytest.raises(NBAPlayerPointsApprovalDigestMismatchError):
        run_manual_bundle(
            bundle_path,
            publish=True,
            approval_digest=digest,
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-05T18:12:00Z",
        )

    bundle["pregame"]["pregame_policy_version"] = "1.1"
    _write_json(bundle_path, bundle)
    new_digest = run_manual_bundle(bundle_path)["plan"]["approval_digest"]
    bundle["repository_commit_sha"] = "1" * 40
    _write_json(bundle_path, bundle)
    with pytest.raises(NBAPlayerPointsApprovalDigestMismatchError):
        run_manual_bundle(
            bundle_path,
            publish=True,
            approval_digest=new_digest,
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-05T18:12:00Z",
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda bundle_path, bundle, paths: _write_json(
            paths["input_root"] / "projection_evidence.json",
            {**_load_fixture()["projection_fixture"], "projected_points": 33.0},
        ),
        lambda bundle_path, bundle, paths: (
            bundle.update({"review_only_comment": "bundle bytes changed"}),
            _write_json(bundle_path, bundle),
        ),
        lambda bundle_path, bundle, paths: _write_json(
            paths["input_root"] / "schedule_identity.json",
            {
                **_load_fixture()["schedule_identity"],
                "reviewed_mappings": {
                    **_load_fixture()["schedule_identity"]["reviewed_mappings"],
                    "mapping_version": "nba-source-adapter-map-fixture-v2",
                },
            },
        ),
    ],
)
def test_mutable_bundle_source_and_mapping_changes_before_publish_mismatch(
    tmp_path: Path,
    mutator,
) -> None:
    bundle_path, bundle, paths = _pregame_bundle(tmp_path)
    digest = run_manual_bundle(bundle_path)["plan"]["approval_digest"]
    mutator(bundle_path, bundle, paths)

    with pytest.raises(NBAPlayerPointsApprovalDigestMismatchError):
        run_manual_bundle(
            bundle_path,
            publish=True,
            approval_digest=digest,
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-05T18:12:00Z",
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda path: _write_json(
            path,
            {**_load_fixture()["projection_fixture"], "projected_points": 40.0},
        ),
        lambda path: path.unlink(),
        lambda path: (
            _write_json(
                path.with_name("replacement_projection.json"),
                {**_load_fixture()["projection_fixture"], "projected_points": 41.0},
            ),
            path.with_name("replacement_projection.json").replace(path),
        ),
    ],
)
def test_source_mutation_after_digest_compare_does_not_change_published_snapshot(
    tmp_path: Path,
    mutator,
) -> None:
    short_suffix = tmp_path.name[-1]
    bundle_path, _, paths = _pregame_bundle(
        tmp_path.parent / f"sm{short_suffix}",
        run_id=f"r-sm{short_suffix}",
    )
    plan = run_manual_bundle(bundle_path)
    assert plan["plan"]["approval_digest"]
    original_projected_points = _load_fixture()["projection_fixture"]["projected_points"]
    source_path = paths["input_root"] / "projection_evidence.json"

    result = run_manual_bundle(
        bundle_path,
        publish=True,
        approval_digest=plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-05T18:12:00Z",
        after_approval_verified=lambda: mutator(source_path),
    )

    published = _draftkings_prediction(paths["evidence_root"])
    assert result["exit_code"] == 0
    assert published["projected_points"] == original_projected_points


def test_input_symlink_is_rejected_before_target_replacement(tmp_path: Path) -> None:
    bundle_path, bundle, paths = _pregame_bundle(tmp_path)
    target = paths["input_root"] / "projection_target.json"
    target.write_bytes((paths["input_root"] / "projection_evidence.json").read_bytes())
    link = paths["input_root"] / "projection_link.json"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this environment")
    bundle["pregame"]["projection_payloads"] = ["projection_link.json"]
    _write_json(bundle_path, bundle)

    with pytest.raises(NBAPlayerPointsPathSecurityError):
        run_manual_bundle(bundle_path)


def test_closing_plan_publish_and_post_tip_selection_behavior(tmp_path: Path) -> None:
    root = tmp_path.parent / "cl"
    _, _, paths, _, _ = _publish_pregame(root, run_id="r-cl")
    prediction = _draftkings_prediction(paths["evidence_root"])
    closing_path = _closing_bundle(root, paths, prediction["prediction_id"])
    plan = run_manual_bundle(closing_path)
    receipt = run_manual_bundle(
        closing_path,
        publish=True,
        approval_digest=plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-06T00:40:30Z",
    )

    assert plan["exit_code"] == 0
    assert receipt["receipt"]["publication_result"]["completion_status"] == "complete"
    assert receipt["receipt"]["verifier_result"]["ok"] is True
    assert not (paths["evidence_root"] / "nba_player_points_evidence" / "settlement").exists()

    fixture = _load_fixture()
    post_tip = deepcopy(fixture["closing_odds"])
    post_tip["events"][0]["bookmakers"][0]["markets"][0][
        "observation_timestamp_utc"
    ] = "2026-06-06T00:40:01Z"
    post_tip_path = _closing_bundle(
        root / "pt",
        paths,
        prediction["prediction_id"],
        closing_payload=post_tip,
    )
    post_tip_plan = run_manual_bundle(post_tip_path)
    assert post_tip_plan["plan"]["eligibility_summary"]["post_tip_observations"] == 1
    assert post_tip_plan["plan"]["normalized_preview"]["effective_selection_preview"][0][
        "selection_status"
    ] == "no_eligible_observation"


def test_closing_requires_completed_prediction_evidence(tmp_path: Path) -> None:
    _, _, paths = _pregame_bundle(tmp_path)
    closing_path = _closing_bundle(tmp_path, paths, "missing-prediction-id")
    result = run_manual_bundle(closing_path)
    assert result["exit_code"] == EXIT_CODES["plan_not_publishable"]
    assert any(
        "corrupt prerequisite prediction evidence" in reason
        for reason in result["plan"]["publishability"]["blocked_reasons"]
    )


def test_settlement_publish_and_minutes_participation_distinctions(tmp_path: Path) -> None:
    _, _, paths, _, _ = _publish_pregame(tmp_path)
    prediction = _draftkings_prediction(paths["evidence_root"])
    closing_path = _closing_bundle(tmp_path, paths, prediction["prediction_id"])
    closing_plan = run_manual_bundle(closing_path)
    run_manual_bundle(
        closing_path,
        publish=True,
        approval_digest=closing_plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-06T00:40:30Z",
    )
    settlement_path = _settlement_v2_bundle(
        tmp_path,
        paths,
        prediction,
        final_stats_payload=_single_final_stats(prediction),
    )
    plan = run_manual_bundle(settlement_path)
    receipt = run_manual_bundle(
        settlement_path,
        publish=True,
        approval_digest=plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-06T04:01:00Z",
    )
    assert plan["plan"]["plan_schema_version"] == NBA_PLAYER_POINTS_MANUAL_PLAN_V2_SCHEMA_VERSION
    assert plan["plan"]["approval_contract_version"] == (
        NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_CONTRACT_VERSION
    )
    assert plan["plan"]["binding_status"] == "closing-bound"
    assert plan["plan"]["per_prediction_closing_binding_summary"][0][
        "closing_line"
    ] == "32.5"
    assert plan["plan"]["eligibility_summary"]["settled_rows"] == 1
    assert receipt["receipt"]["publication_result"]["completion_status"] == "complete"
    assert receipt["binding_status"] == "closing-bound"
    assert receipt["evidence_schema_version"] == (
        NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION
    )
    assert receipt["approval_receipt_sha256"]

    segment = Path(receipt["receipt"]["settlement_segment_path"])
    envelope = _read_json(segment / "approval_envelope.json")
    manifest = _read_json(segment / "settlement_manifest.json")
    assert "physical_settlement_batch_id" not in envelope
    assert "manifest_file_sha256" not in envelope
    assert "approval_envelope_sha256" not in envelope
    assert "approval_receipt.json" not in manifest["file_inventory"]
    assert manifest["binding_status"] == "closing-bound"
    verify_report = receipt["receipt"]["full_verifier_result"]
    assert verify_report["binding_status_counts"]["closing-bound"] == 1
    replay = run_manual_bundle(
        settlement_path,
        publish=True,
        approval_digest=plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-06T04:01:00Z",
    )
    assert replay["plan"]["approval_digest"] == plan["plan"]["approval_digest"]
    assert replay["receipt"]["publication_result"]["completion_status"] == (
        "already_complete"
    )

    missing_path = _settlement_v2_bundle(
        tmp_path / "missing",
        paths,
        prediction,
        final_stats_payload=_single_final_stats(prediction, "final-sga-missing-minutes"),
    )
    missing = run_manual_bundle(missing_path)
    assert missing["plan"]["eligibility_summary"]["missing_actual_minutes_rows"] == 1
    assert missing["plan"]["eligibility_summary"]["manual_review_rows"] == 1

    zero_path = _settlement_v2_bundle(
        tmp_path / "zero",
        paths,
        prediction,
        final_stats_payload=_single_final_stats(prediction, "final-sga-zero-minutes"),
    )
    zero = run_manual_bundle(zero_path)
    assert zero["plan"]["eligibility_summary"]["zero_minute_rows"] == 1
    assert zero["plan"]["eligibility_summary"]["dnp_rows"] == 0


def test_new_v1_settlement_publication_is_blocked_without_fallback(tmp_path: Path) -> None:
    _, _, paths, _, _ = _publish_pregame(tmp_path)
    prediction = _draftkings_prediction(paths["evidence_root"])
    settlement_path = _settlement_bundle(
        tmp_path,
        paths,
        prediction,
        final_stats_payload=_single_final_stats(prediction),
    )

    plan = run_manual_bundle(settlement_path)

    assert plan["exit_code"] == EXIT_CODES["plan_not_publishable"]
    assert "closing_bound_settlement_v2_required" in plan["plan"]["publishability"][
        "blocked_reasons"
    ]
    with pytest.raises(
        NBAPlayerPointsPlanNotPublishableError,
        match="closing_bound_settlement_v2_required",
    ):
        run_manual_bundle(
            settlement_path,
            publish=True,
            approval_digest=plan["plan"]["approval_digest"],
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-06T04:01:00Z",
        )
    assert not (
        paths["evidence_root"]
        / "nba_player_points_evidence"
        / "settlement"
    ).exists()


def test_stage_publications_and_read_only_operations_do_not_mutate_other_evidence(tmp_path: Path) -> None:
    root = tmp_path.parent / "stg"
    _, _, paths, _, _ = _publish_pregame(root, run_id="r-stage")
    prediction = _draftkings_prediction(paths["evidence_root"])
    evidence_root = paths["evidence_root"] / "nba_player_points_evidence"
    prediction_before_closing = (
        _snapshot(evidence_root / "runs"),
        _snapshot(evidence_root / "ledgers"),
    )

    closing_path = _closing_bundle(root, paths, prediction["prediction_id"])
    closing_plan = run_manual_bundle(closing_path)
    run_manual_bundle(
        closing_path,
        publish=True,
        approval_digest=closing_plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-06T00:40:30Z",
    )
    assert (_snapshot(evidence_root / "runs"), _snapshot(evidence_root / "ledgers")) == prediction_before_closing

    closing_before_settlement = _snapshot(evidence_root / "closing")
    prediction_before_settlement = (
        _snapshot(evidence_root / "runs"),
        _snapshot(evidence_root / "ledgers"),
    )
    settlement_path = _settlement_v2_bundle(
        root,
        paths,
        prediction,
        final_stats_payload=_single_final_stats(prediction),
    )
    settlement_plan = run_manual_bundle(settlement_path)
    run_manual_bundle(
        settlement_path,
        publish=True,
        approval_digest=settlement_plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-06T04:01:00Z",
    )
    assert (_snapshot(evidence_root / "runs"), _snapshot(evidence_root / "ledgers")) == prediction_before_settlement
    assert _snapshot(evidence_root / "closing") == closing_before_settlement

    evidence_before_read_only = _snapshot(paths["evidence_root"])
    for operation in ("verify", "status"):
        bundle = {
            "bundle_schema_version": MANUAL_RUN_BUNDLE_SCHEMA_VERSION,
            "operation_stage": operation,
            "operating_date": "2026-06-05",
            "repository_commit_sha": _current_sha(),
            "research_label": "research_only_not_for_betting",
            "input_root": str(paths["input_root"]),
            "preview_root": str(root / f"{operation}_previews"),
            "evidence_root": str(paths["evidence_root"]),
            "operator_id": "manual-research-operator",
            "requested_at_utc": "2026-06-06T05:00:00Z",
        }
        path = root / f"{operation}.json"
        _write_json(path, bundle)
        result = run_manual_bundle(path)
        assert result["publication_attempted"] is False
    assert _snapshot(paths["evidence_root"]) == evidence_before_read_only


def test_settlement_requires_prediction_evidence_and_terminal_conflict_fails_closed(tmp_path: Path) -> None:
    _, _, paths = _pregame_bundle(tmp_path)
    fake_prediction = {
        "prediction_id": "missing-prediction-id",
        "player_id": "nba-player-1628983",
        "canonical_event_id": "nba-2026-06-05-okc-ind",
    }
    missing_path = _settlement_bundle(
        tmp_path,
        paths,
        fake_prediction,
        final_stats_payload=_single_final_stats(fake_prediction),
    )
    missing = run_manual_bundle(missing_path)
    assert missing["exit_code"] == EXIT_CODES["plan_not_publishable"]

    _, _, paths, _, _ = _publish_pregame(
        tmp_path,
        run_id="run-nba-points-manual-conflict",
    )
    prediction = _draftkings_prediction(paths["evidence_root"])
    conflict_payload = _single_final_stats(prediction)
    player = deepcopy(conflict_payload["games"][0]["players"][0])
    player["source_row_id"] = "final-sga-conflict"
    player["final_points"] = 99
    conflict_payload["games"][0]["players"].append(player)
    conflict_path = _settlement_bundle(
        tmp_path / "settlement_conflict",
        paths,
        prediction,
        final_stats_payload=conflict_payload,
    )
    conflict = run_manual_bundle(conflict_path)
    assert conflict["exit_code"] == EXIT_CODES["plan_not_publishable"]
    assert "terminal settlement conflict" in conflict["plan"]["publishability"]["blocked_reasons"]
    with pytest.raises(NBAPlayerPointsPlanNotPublishableError):
        run_manual_bundle(
            conflict_path,
            publish=True,
            approval_digest=conflict["plan"]["approval_digest"],
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-06T04:01:00Z",
        )


def test_cross_stage_and_cross_run_digest_reuse_is_rejected(tmp_path: Path) -> None:
    pregame_path, _, paths, pregame_plan, _ = _publish_pregame(tmp_path)
    prediction = _draftkings_prediction(paths["evidence_root"])
    closing_path = _closing_bundle(tmp_path, paths, prediction["prediction_id"])
    closing_plan = run_manual_bundle(closing_path)
    settlement_path = _settlement_bundle(
        tmp_path,
        paths,
        prediction,
        final_stats_payload=_single_final_stats(prediction),
    )
    settlement_plan = run_manual_bundle(settlement_path)

    with pytest.raises(NBAPlayerPointsApprovalDigestMismatchError):
        run_manual_bundle(
            closing_path,
            publish=True,
            approval_digest=pregame_plan["plan"]["approval_digest"],
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-06T00:40:30Z",
        )
    with pytest.raises(NBAPlayerPointsApprovalDigestMismatchError):
        run_manual_bundle(
            settlement_path,
            publish=True,
            approval_digest=closing_plan["plan"]["approval_digest"],
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-06T04:01:00Z",
        )
    with pytest.raises(NBAPlayerPointsApprovalDigestMismatchError):
        run_manual_bundle(
            closing_path,
            publish=True,
            approval_digest=settlement_plan["plan"]["approval_digest"],
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-06T00:40:30Z",
        )

    other_path, _, _ = _pregame_bundle(
        tmp_path / "other_run",
        run_id="run-nba-points-manual-other",
    )
    with pytest.raises(NBAPlayerPointsApprovalDigestMismatchError):
        run_manual_bundle(
            other_path,
            publish=True,
            approval_digest=pregame_plan["plan"]["approval_digest"],
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-05T18:12:00Z",
        )

    assert run_manual_bundle(pregame_path)["plan"]["approval_digest"] == pregame_plan["plan"]["approval_digest"]


def test_new_prerequisite_closing_evidence_after_planning_changes_digest(tmp_path: Path) -> None:
    root = tmp_path.parent / "preq"
    _, _, paths, _, _ = _publish_pregame(root, run_id="r-pr")
    prediction = _draftkings_prediction(paths["evidence_root"])
    closing_path = _closing_bundle(root, paths, prediction["prediction_id"])
    stale_plan = run_manual_bundle(closing_path)

    other_path = _closing_bundle(root / "o", paths, prediction["prediction_id"])
    other_bundle = _read_json(other_path)
    other_bundle["closing"]["closing_batch_id"] = "manual-closing-batch-002"
    other_bundle["closing"]["selection_batch_id"] = "manual-selection-batch-002"
    _write_json(other_path, other_bundle)
    other_plan = run_manual_bundle(other_path)
    run_manual_bundle(
        other_path,
        publish=True,
        approval_digest=other_plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-06T00:40:30Z",
    )

    with pytest.raises(NBAPlayerPointsApprovalDigestMismatchError):
        run_manual_bundle(
            closing_path,
            publish=True,
            approval_digest=stale_plan["plan"]["approval_digest"],
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-06T00:41:00Z",
        )


def test_invalid_prerequisite_evidence_publish_uses_prerequisite_exit_class(tmp_path: Path) -> None:
    _, _, paths = _pregame_bundle(tmp_path)
    closing_path = _closing_bundle(tmp_path, paths, "missing-prediction-id")
    plan = run_manual_bundle(closing_path)

    with pytest.raises(NBAPlayerPointsPrerequisiteEvidenceError):
        run_manual_bundle(
            closing_path,
            publish=True,
            approval_digest=plan["plan"]["approval_digest"],
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-06T00:40:30Z",
        )


def test_verify_operation_is_read_only(tmp_path: Path) -> None:
    _, _, paths, _, _ = _publish_pregame(tmp_path.parent / "vr", run_id="r-v")
    bundle = {
        "bundle_schema_version": MANUAL_RUN_BUNDLE_SCHEMA_VERSION,
        "operation_stage": "verify",
        "operating_date": "2026-06-05",
        "repository_commit_sha": _current_sha(),
        "research_label": "research_only_not_for_betting",
        "input_root": str(paths["input_root"]),
        "preview_root": str(tmp_path / "verify_previews"),
        "evidence_root": str(paths["evidence_root"]),
        "operator_id": "manual-research-operator",
        "requested_at_utc": "2026-06-06T05:00:00Z",
    }
    path = tmp_path / "verify.json"
    _write_json(path, bundle)
    before = _snapshot(paths["evidence_root"])
    result = run_manual_bundle(path)
    after = _snapshot(paths["evidence_root"])
    assert result["publication_attempted"] is False
    assert result["read_only"] is True
    assert before == after


def test_invalid_path_symlink_escape_and_known_production_path_are_blocked(tmp_path: Path) -> None:
    bundle_path, bundle, paths = _pregame_bundle(tmp_path)
    bundle["pregame"]["projection_payloads"] = ["../projection_evidence.json"]
    _write_json(bundle_path, bundle)
    with pytest.raises(NBAPlayerPointsPathSecurityError):
        run_manual_bundle(bundle_path)

    bundle_path, bundle, paths = _pregame_bundle(tmp_path / "prod")
    bundle["evidence_root"] = str(tmp_path / "outputs")
    _write_json(bundle_path, bundle)
    with pytest.raises(NBAPlayerPointsPathSecurityError):
        run_manual_bundle(bundle_path)

    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "input_link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this environment")
    bundle_path, bundle, _ = _pregame_bundle(tmp_path / "symlink")
    bundle["input_root"] = str(link)
    _write_json(bundle_path, bundle)
    with pytest.raises(NBAPlayerPointsPathSecurityError):
        run_manual_bundle(bundle_path)


@pytest.mark.parametrize(
    "configure",
    [
        lambda bundle, paths: bundle.update({"preview_root": str(paths["input_root"])}),
        lambda bundle, paths: bundle.update({"preview_root": str(paths["evidence_root"] / "preview")}),
        lambda bundle, paths: bundle.update({"evidence_root": str(paths["preview_root"] / "evidence")}),
        lambda bundle, paths: (
            (paths["preview_root"] / "inputs").mkdir(parents=True, exist_ok=True),
            bundle.update({"input_root": str(paths["preview_root"] / "inputs")}),
        ),
        lambda bundle, paths: bundle.update({"preview_root": str(paths["input_root"] / "preview")}),
        lambda bundle, paths: (
            (paths["evidence_root"] / "inputs").mkdir(parents=True, exist_ok=True),
            bundle.update({"input_root": str(paths["evidence_root"] / "inputs")}),
        ),
        lambda bundle, paths: bundle.update({"evidence_root": str(paths["input_root"] / "evidence")}),
    ],
)
def test_pairwise_root_overlap_classes_are_blocked(tmp_path: Path, configure) -> None:
    bundle_path, bundle, paths = _pregame_bundle(tmp_path)
    configure(bundle, paths)
    _write_json(bundle_path, bundle)

    with pytest.raises(NBAPlayerPointsPathSecurityError):
        run_manual_bundle(bundle_path)


def test_case_normalized_root_overlap_is_blocked_on_windows(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("case-normalized path overlap is Windows-specific")
    bundle_path, bundle, paths = _pregame_bundle(tmp_path)
    bundle["preview_root"] = str(paths["input_root"]).upper()
    _write_json(bundle_path, bundle)

    with pytest.raises(NBAPlayerPointsPathSecurityError):
        run_manual_bundle(bundle_path)


def test_symlink_based_root_overlap_is_blocked(tmp_path: Path) -> None:
    bundle_path, bundle, paths = _pregame_bundle(tmp_path)
    link = tmp_path / "preview_link"
    try:
        os.symlink(paths["evidence_root"], link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this environment")
    bundle["preview_root"] = str(link)
    _write_json(bundle_path, bundle)

    with pytest.raises(NBAPlayerPointsPathSecurityError):
        run_manual_bundle(bundle_path)


def test_dry_run_does_not_create_missing_evidence_root(tmp_path: Path) -> None:
    bundle_path, _, paths = _pregame_bundle(tmp_path)
    paths["evidence_root"].rmdir()

    result = run_manual_bundle(bundle_path)

    assert result["exit_code"] == 0
    assert not paths["evidence_root"].exists()


def _fake_git_with(overrides: dict[tuple[str, ...], object]):
    def fake(args: object, repository_root: Path) -> str:
        command = tuple(args)
        value = overrides.get(command)
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return str(value)
        return _clean_git_output(args, repository_root)

    return fake


@pytest.mark.parametrize(
    ("overrides", "bundle_kwargs", "allowed", "reason_fragment"),
    [
        (
            {("rev-parse", "HEAD"): "1" * 40 + "\n"},
            {},
            False,
            "repository SHA mismatch",
        ),
        (
            {("diff", "--cached", "--name-only"): "courtvision/sports/nba/example.py\n"},
            {},
            False,
            "staged changes exist",
        ),
        (
            {("diff", "--name-only"): "courtvision/sports/nba/example.py\n"},
            {},
            False,
            "tracked working-tree changes exist",
        ),
        (
            {("ls-files", "-u"): "100644 " + "a" * 40 + " 1\tconflicted.py\n"},
            {},
            False,
            "unmerged paths exist",
        ),
        (
            {
                ("ls-files", "--others", "--exclude-standard"):
                "docs/audits/courtvision_full_system_audit/00_executive_summary.md\n"
            },
            {},
            True,
            "",
        ),
        (
            {
                ("ls-files", "--others", "--exclude-standard"):
                "docs/audits/courtvision_full_system_audit/00_executive_summary.md\nnew_source.py\n"
            },
            {},
            False,
            "unexpected untracked repository paths exist",
        ),
        (
            {
                ("ls-files", "--others", "--exclude-standard"):
                "docs/audits/courtvision_full_system_audit_extra/report.md\n"
            },
            {},
            False,
            "unexpected untracked repository paths exist",
        ),
        (
            {("diff", "--name-only"): NBAPlayerPointsBundleError("simulated git failure")},
            {},
            False,
            "simulated git failure",
        ),
        (
            {("rev-parse", "--abbrev-ref", "HEAD"): "main\n"},
            {"repository_branch": "feat/nba-prospective-research"},
            False,
            "repository branch mismatch",
        ),
    ],
)
def test_repository_state_verification_blocks_structural_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[tuple[str, ...], object],
    bundle_kwargs: dict[str, object],
    allowed: bool,
    reason_fragment: str,
) -> None:
    monkeypatch.setattr(runner_module, "_run_git_readonly", _fake_git_with(overrides))
    bundle_path, _, _ = _pregame_bundle(tmp_path, **bundle_kwargs)

    result = run_manual_bundle(bundle_path)

    assert result["plan"]["publishability"]["allowed"] is allowed
    if allowed:
        assert result["exit_code"] == 0
        assert result["plan"]["repository_state"]["unexpected_untracked_paths"] == []
        assert result["plan"]["repository_state"]["protected_untracked_paths"]
    else:
        assert result["exit_code"] == EXIT_CODES["plan_not_publishable"]
        assert any(
            reason_fragment in reason
            for reason in result["plan"]["publishability"]["blocked_reasons"]
        )
        with pytest.raises(NBAPlayerPointsPlanNotPublishableError):
            run_manual_bundle(
                bundle_path,
                publish=True,
                approval_digest=result["plan"]["approval_digest"],
                approval_operator_id="manual-research-operator",
                approval_timestamp_utc="2026-06-05T18:12:00Z",
            )


def _init_clean_git_repo(path: Path) -> tuple[Path, str]:
    path.mkdir(parents=True, exist_ok=True)
    (path / "README.md").write_text("clean test repository\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test User",
            "commit",
            "-m",
            "initial",
        ],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
    return path, sha


def _run_cli(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_exit_codes_and_dry_run_behavior(tmp_path: Path) -> None:
    repo_root, repo_sha = _init_clean_git_repo(tmp_path / "repo")
    bundle_path, _, paths = _pregame_bundle(tmp_path / "cli", repository_commit_sha=repo_sha)
    completed = _run_cli(["--bundle", str(bundle_path), "--repository-root", str(repo_root)])
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["publication_attempted"] is False
    assert _snapshot(paths["evidence_root"]) == ()

    missing_bundle = _run_cli([])
    assert missing_bundle.returncode == EXIT_CODES["invalid_command_or_bundle"]

    missing_digest = _run_cli(
        [
            "--bundle",
            str(bundle_path),
            "--repository-root",
            str(repo_root),
            "--publish",
            "--approval-operator-id",
            "manual-research-operator",
            "--approval-timestamp-utc",
            "2026-06-05T18:12:00Z",
        ]
    )
    assert missing_digest.returncode == EXIT_CODES["approval_digest_mismatch"]

    wrong_digest = _run_cli(
        [
            "--bundle",
            str(bundle_path),
            "--repository-root",
            str(repo_root),
            "--publish",
            "--approval-digest",
            "0" * 64,
            "--approval-operator-id",
            "manual-research-operator",
            "--approval-timestamp-utc",
            "2026-06-05T18:12:00Z",
        ]
    )
    assert wrong_digest.returncode == EXIT_CODES["approval_digest_mismatch"]

    mismatch_path, _, _ = _pregame_bundle(
        tmp_path / "cli_nonpublishable",
        repository_commit_sha="1" * 40,
    )
    nonpublishable = _run_cli(
        ["--bundle", str(mismatch_path), "--repository-root", str(repo_root)]
    )
    assert nonpublishable.returncode == EXIT_CODES["plan_not_publishable"]

    publish_plan = json.loads(completed.stdout)
    successful_publish = _run_cli(
        [
            "--bundle",
            str(bundle_path),
            "--repository-root",
            str(repo_root),
            "--publish",
            "--approval-digest",
            publish_plan["plan"]["approval_digest"],
            "--approval-operator-id",
            "manual-research-operator",
            "--approval-timestamp-utc",
            "2026-06-05T18:12:00Z",
        ]
    )
    assert successful_publish.returncode == EXIT_CODES["success"]

    projection = deepcopy(_load_fixture()["projection_fixture"])
    projection["projected_points"] = 44.0
    _write_json(paths["input_root"] / "projection_evidence.json", projection)
    conflict_plan = _run_cli(["--bundle", str(bundle_path), "--repository-root", str(repo_root)])
    conflict_digest = json.loads(conflict_plan.stdout)["plan"]["approval_digest"]
    conflict = _run_cli(
        [
            "--bundle",
            str(bundle_path),
            "--repository-root",
            str(repo_root),
            "--publish",
            "--approval-digest",
            conflict_digest,
            "--approval-operator-id",
            "manual-research-operator",
            "--approval-timestamp-utc",
            "2026-06-05T18:21:00Z",
        ]
    )
    assert conflict.returncode == EXIT_CODES["publication_conflict"]

    _, _, missing_paths = _pregame_bundle(
        tmp_path / "cli_invalid_prereq",
        repository_commit_sha=repo_sha,
    )
    closing_path = _closing_bundle(
        tmp_path / "cli_invalid_prereq",
        missing_paths,
        "missing-prediction-id",
    )
    closing_bundle = _read_json(closing_path)
    closing_bundle["repository_commit_sha"] = repo_sha
    _write_json(closing_path, closing_bundle)
    closing_plan = _run_cli(["--bundle", str(closing_path), "--repository-root", str(repo_root)])
    invalid_prereq = _run_cli(
        [
            "--bundle",
            str(closing_path),
            "--repository-root",
            str(repo_root),
            "--publish",
            "--approval-digest",
            json.loads(closing_plan.stdout)["plan"]["approval_digest"],
            "--approval-operator-id",
            "manual-research-operator",
            "--approval-timestamp-utc",
            "2026-06-06T00:40:30Z",
        ]
    )
    assert invalid_prereq.returncode == EXIT_CODES["prerequisite_evidence_invalid"]

    verify_bundle = {
        "bundle_schema_version": MANUAL_RUN_BUNDLE_SCHEMA_VERSION,
        "operation_stage": "verify",
        "operating_date": "2026-06-05",
        "repository_commit_sha": repo_sha,
        "research_label": "research_only_not_for_betting",
        "input_root": str(paths["input_root"]),
        "preview_root": str(tmp_path / "cli_verify_previews"),
        "evidence_root": str(paths["evidence_root"]),
        "operator_id": "manual-research-operator",
        "requested_at_utc": "2026-06-06T05:00:00Z",
    }
    ledger_path = next(
        (paths["evidence_root"] / "nba_player_points_evidence" / "ledgers" / "segments").glob(
            "*/*/prediction_ledger.jsonl"
        )
    )
    ledger_path.write_text(ledger_path.read_text(encoding="utf-8") + "\n{}\n", encoding="utf-8")
    verify_path = tmp_path / "cli_verify.json"
    _write_json(verify_path, verify_bundle)
    integrity = _run_cli(["--bundle", str(verify_path), "--repository-root", str(repo_root)])
    assert integrity.returncode == EXIT_CODES["integrity_verification_failed"]

    path_error_bundle = deepcopy(verify_bundle)
    path_error_bundle["operation_stage"] = "pregame-plan"
    path_error_bundle["preview_root"] = str(paths["input_root"])
    path_error_bundle["pregame"] = _read_json(bundle_path)["pregame"]
    path_error_path = tmp_path / "cli_path_error.json"
    _write_json(path_error_path, path_error_bundle)
    path_error = _run_cli(["--bundle", str(path_error_path), "--repository-root", str(repo_root)])
    assert path_error.returncode == EXIT_CODES["path_or_security_validation_failed"]


def test_architectural_boundary_has_no_live_calls_credentials_scheduler_or_betting_paths() -> None:
    source = RUNNER_MODULE.read_text(encoding="utf-8")
    lowered = source.casefold()
    forbidden_fragments = [
        "requests.",
        "httpx",
        "api_nba_client",
        "run_today.bat",
        "run_today.ps1",
        "courtvision_ai.py",
        "subprocess.run([\"git\", \"checkout\"",
        "subprocess.run([\"git\", \"reset\"",
        "subprocess.run([\"git\", \"clean\"",
        "os.environ",
        "scheduledtask",
        "windows task",
        "calculate_kelly",
        "kelly_size",
        "bankroll_manager",
        "grade_live",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in lowered


def test_unsupported_bundle_schema_and_publish_stage_without_flag_fail_closed(tmp_path: Path) -> None:
    bundle_path, bundle, _ = _pregame_bundle(tmp_path)
    bundle["bundle_schema_version"] = "nba-player-points-manual-run-v2"
    _write_json(bundle_path, bundle)
    with pytest.raises(NBAPlayerPointsBundleError):
        run_manual_bundle(bundle_path)

    bundle_path, bundle, _ = _pregame_bundle(tmp_path / "publish-stage")
    bundle["operation_stage"] = "pregame-publish"
    _write_json(bundle_path, bundle)
    with pytest.raises(NBAPlayerPointsBundleError):
        run_manual_bundle(bundle_path)


def test_runner_errors_expose_stable_exit_codes() -> None:
    assert issubclass(NBAPlayerPointsManualRunnerError, RuntimeError)
    assert NBAPlayerPointsApprovalDigestMismatchError.exit_code == EXIT_CODES["approval_digest_mismatch"]
    assert NBAPlayerPointsPublicationConflictError.exit_code == EXIT_CODES["publication_conflict"]
