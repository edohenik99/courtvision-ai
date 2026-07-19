from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

import courtvision.sports.nba.player_points_research_runner as runner_module
from courtvision.sports.nba.player_points_research_runner import (
    NBAPlayerPointsPathSecurityError,
    NBAPlayerPointsPrerequisiteEvidenceError,
    run_manual_bundle,
)
from courtvision.sports.nba.player_points_closing import (
    NBAPlayerPointsClosingWriterConfig,
    write_nba_player_points_closing_evidence,
)
from courtvision.sports.nba.player_points_settlement_closing_binding import (
    NBA_PLAYER_POINTS_CLOSING_PREREQUISITE_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION,
    NBAPlayerPointsClosingBindingError,
    build_nba_player_points_closing_prerequisite,
    build_nba_player_points_settlement_approval_envelope,
    canonical_closing_line,
    canonical_json_bytes,
    canonical_sha256,
    validate_nba_player_points_closing_prerequisite,
    verify_nba_player_points_settlement_evidence_v2,
    write_nba_player_points_settlement_evidence_v2,
)
from courtvision.sports.nba.player_points_settlement_evidence import (
    NBAPlayerPointsSettlementEvidenceWriterConfig,
    verify_nba_player_points_settlement_evidence,
)
from tests.test_nba_player_points_research_runner import (
    _clean_git_output,
    _closing_bundle,
    _draftkings_prediction,
    _ledger_rows,
    _publish_pregame,
    _read_json,
    _settlement_v2_bundle,
    _single_final_stats,
)


VERSION_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "nba"
    / "player_points"
    / "settlement_evidence_versions.json"
)
INVALID_FIXTURE = VERSION_FIXTURE.with_name("settlement_evidence_invalid_case.json")


@pytest.fixture(autouse=True)
def _clean_repository_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_module, "_run_git_readonly", _clean_git_output)


def _prepare_v2_plan(tmp_path: Path):
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
    bundle_path = _settlement_v2_bundle(
        tmp_path,
        paths,
        prediction,
        final_stats_payload=_single_final_stats(prediction),
    )
    context = runner_module._load_bundle_context(bundle_path, repository_root=None)
    plan_build = runner_module._build_plan(context, "settlement-plan")
    assert plan_build.closing_snapshot is not None
    assert plan_build.logical_settlement_batch_id is not None
    assert plan_build.settlement_policy_v2 is not None
    return bundle_path, paths, prediction, context, plan_build


def _approval_envelope(context, plan_build, *, timestamp: str = "2026-06-06T04:01:00Z"):
    prerequisite = plan_build.closing_snapshot.prerequisite
    return build_nba_player_points_settlement_approval_envelope(
        approval_digest=plan_build.plan["approval_digest"],
        operator_id="manual-research-operator",
        approval_timestamp_utc=timestamp,
        bundle_sha256=context.bundle_hash,
        repository_commit_sha=context.repository_commit_sha,
        logical_settlement_batch_id=plan_build.logical_settlement_batch_id,
        closing_prerequisite_sha256=prerequisite.closing_prerequisite_sha256,
        prediction_ids=[item.prediction_id for item in prerequisite.prediction_mappings],
    )


def _write_v2(context, plan_build, envelope, *, failure_hook=None):
    return write_nba_player_points_settlement_evidence_v2(
        context.evidence_root,
        plan_build.settlement_rows,
        plan_build.closing_snapshot,
        envelope,
        logical_settlement_batch_id=plan_build.logical_settlement_batch_id,
        settlement_policy=plan_build.settlement_policy_v2,
        collection_timestamp_utc="2026-06-06T04:00:00Z",
        repository_commit_sha=context.repository_commit_sha,
        writer_timestamp_utc=envelope["approval_timestamp_utc"],
        failure_hook=failure_hook,
    )


def _materialize_frozen_case(tmp_path: Path, case_name: str) -> Path:
    fixture = json.loads(VERSION_FIXTURE.read_text(encoding="utf-8"))
    files = fixture["cases"][case_name]["files"]
    root = tmp_path / case_name / "nba_player_points_evidence"
    for relative, encoded in files.items():
        relative_path = Path(relative)
        assert not relative_path.is_absolute()
        assert ".." not in relative_path.parts
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(encoded))
    return root


def _file_state(root: Path) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        sorted(
            (
                path.relative_to(root).as_posix(),
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_mtime_ns,
            )
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def test_canonical_decimal_and_canonical_json_rules() -> None:
    assert canonical_closing_line(31.5000) == "31.5"
    assert canonical_closing_line("33.000") == "33"
    assert canonical_closing_line(0) == "0"
    assert canonical_json_bytes({"b": 1, "a": "é"}) == b'{"a":"\\u00e9","b":1}'
    assert not canonical_json_bytes({"a": 1}).endswith(b"\n")
    with pytest.raises(NBAPlayerPointsClosingBindingError):
        canonical_closing_line(float("nan"))


def test_frozen_v1_v2_and_invalid_compatibility_fixtures_are_read_only(
    tmp_path: Path,
) -> None:
    v1_root = _materialize_frozen_case(tmp_path, "v1")
    v1_before = _file_state(v1_root)
    v1_report = verify_nba_player_points_settlement_evidence(
        v1_root,
        NBAPlayerPointsSettlementEvidenceWriterConfig(),
    )
    assert v1_report.ok is True
    assert v1_report.binding_status_counts["legacy-unbound"] == 1
    assert v1_report.binding_status_counts["invalid"] == 0
    assert _file_state(v1_root) == v1_before

    v2_root = _materialize_frozen_case(tmp_path, "v2")
    v2_before = _file_state(v2_root)
    v2_report = verify_nba_player_points_settlement_evidence(
        v2_root,
        NBAPlayerPointsSettlementEvidenceWriterConfig(),
    )
    assert v2_report.ok is True
    assert v2_report.binding_status_counts["closing-bound"] == 1
    assert v2_report.binding_status_counts["invalid"] == 0
    assert _file_state(v2_root) == v2_before

    invalid_directive = json.loads(INVALID_FIXTURE.read_text(encoding="utf-8"))
    invalid_root = _materialize_frozen_case(tmp_path, invalid_directive["base_case"])
    target = next(invalid_root.glob(invalid_directive["mutation"]["path_glob"]))
    target.write_bytes(target.read_bytes() + b" ")
    invalid_report = verify_nba_player_points_settlement_evidence(
        invalid_root,
        NBAPlayerPointsSettlementEvidenceWriterConfig(),
    )
    assert invalid_report.ok is False
    assert invalid_report.binding_status_counts["invalid"] == 1
    assert invalid_report.binding_status_counts["legacy-unbound"] == 0


def test_prerequisite_is_deterministic_relocatable_and_root_append_independent(
    tmp_path: Path,
) -> None:
    _, paths, _, _, plan_build = _prepare_v2_plan(tmp_path)
    prerequisite = plan_build.closing_snapshot.prerequisite
    observation_ids = [
        item.physical_observation_batch_id for item in prerequisite.observation_batches
    ]
    kwargs = {
        "operating_date": prerequisite.operating_date,
        "physical_observation_batch_ids": list(reversed(observation_ids)),
        "physical_selection_batch_id": prerequisite.selection_batch.physical_selection_batch_id,
        "prediction_ids": [item.prediction_id for item in reversed(prerequisite.prediction_mappings)],
        "expected_closing_policy_id": prerequisite.closing_policy["closing_policy_id"],
        "expected_closing_policy_version": prerequisite.closing_policy[
            "closing_policy_version"
        ],
    }

    rebuilt = build_nba_player_points_closing_prerequisite(paths["evidence_root"], **kwargs)
    assert rebuilt.prerequisite.to_dict() == prerequisite.to_dict()
    assert prerequisite.schema_version == NBA_PLAYER_POINTS_CLOSING_PREREQUISITE_SCHEMA_VERSION
    assert prerequisite.prediction_mappings[0].closing_line == "32.5"

    relocated = tmp_path / "relocated_evidence"
    shutil.copytree(paths["evidence_root"], relocated)
    relocated_snapshot = build_nba_player_points_closing_prerequisite(relocated, **kwargs)
    assert relocated_snapshot.prerequisite.to_dict() == prerequisite.to_dict()

    unrelated = (
        relocated
        / "nba_player_points_evidence"
        / "closing"
        / "observations"
        / "segments"
        / "2026-06-05"
        / "unrelated-future-batch"
    )
    unrelated.mkdir(parents=True)
    (unrelated / "UNRELATED").write_text("future batch locator\n", encoding="utf-8")
    after_append = build_nba_player_points_closing_prerequisite(relocated, **kwargs)
    assert after_append.prerequisite.closing_prerequisite_sha256 == (
        prerequisite.closing_prerequisite_sha256
    )


def test_multiple_sportsbooks_and_observation_batches_bind_to_one_selection_batch(
    tmp_path: Path,
) -> None:
    _, _, paths, _, _ = _publish_pregame(tmp_path)
    predictions = {
        str(row["sportsbook"]): row for row in _ledger_rows(paths["evidence_root"])
    }
    assert {"DraftKings", "FanDuel"}.issubset(predictions)
    evidence_root = paths["evidence_root"] / "nba_player_points_evidence"
    ledger_path = next(
        evidence_root.glob("ledgers/segments/*/*/prediction_ledger.jsonl")
    )
    ledger_locator = ledger_path.relative_to(evidence_root).as_posix()

    def observation(sportsbook: str, source_suffix: str) -> dict[str, object]:
        row = predictions[sportsbook]
        return {
            "prediction_reference": {
                "prediction_id": row["prediction_id"],
                "prediction_run_id": row["prediction_run_id"],
                "prediction_evidence_segment": ledger_locator,
                "prediction_record_hash": row["ledger_record_hash"],
            },
            "canonical_event_id": row["canonical_event_id"],
            "provider_event_id": row["provider_event_id"],
            "player_id": row["player_id"],
            "sportsbook": row["sportsbook"],
            "market": row["market"],
            "operating_date": row["operating_date"],
            "commence_time_utc": row["commence_time_utc"],
            "closing_line": 32.5 if sportsbook == "DraftKings" else 33,
            "closing_american_odds": -115 if sportsbook == "DraftKings" else -118,
            "closing_market_status": "open",
            "observation_timestamp_utc": "2026-06-06T00:39:00Z",
            "source_market_update_timestamp_utc": "2026-06-06T00:39:00Z",
            "closing_provider": "offline_closing_fixture",
            "closing_source_id": f"closing-source-{source_suffix}",
            "closing_source_hash": ("a" if sportsbook == "DraftKings" else "b") * 64,
        }

    draftkings = observation("DraftKings", "dk")
    fanduel = observation("FanDuel", "fd")
    draftkings_too_early = dict(draftkings)
    draftkings_too_early.update(
        {
            "observation_timestamp_utc": "2026-06-06T00:00:00Z",
            "source_market_update_timestamp_utc": "2026-06-06T00:00:00Z",
            "closing_source_id": "closing-source-dk-too-early",
            "closing_source_hash": "c" * 64,
        }
    )
    config = NBAPlayerPointsClosingWriterConfig()
    first = write_nba_player_points_closing_evidence(
        paths["evidence_root"],
        [draftkings],
        config,
        collection_timestamp_utc="2026-06-06T00:39:30Z",
        repository_commit_sha=runner_module._run_git_readonly(
            ("rev-parse", "HEAD"),
            Path.cwd(),
        ).strip(),
        writer_timestamp_utc="2026-06-06T00:39:30Z",
    )
    second = write_nba_player_points_closing_evidence(
        paths["evidence_root"],
        [draftkings_too_early, fanduel],
        config,
        collection_timestamp_utc="2026-06-06T00:39:31Z",
        repository_commit_sha=runner_module._run_git_readonly(
            ("rev-parse", "HEAD"),
            Path.cwd(),
        ).strip(),
        writer_timestamp_utc="2026-06-06T00:39:31Z",
    )
    snapshot = build_nba_player_points_closing_prerequisite(
        paths["evidence_root"],
        operating_date="2026-06-05",
        physical_observation_batch_ids=[
            first.closing_batch_id,
            second.closing_batch_id,
        ],
        physical_selection_batch_id=second.selection_batch_id,
        prediction_ids=[
            predictions["DraftKings"]["prediction_id"],
            predictions["FanDuel"]["prediction_id"],
        ],
        expected_closing_policy_id=(
            "nba-player-points-same-book-latest-pre-tip-v1"
        ),
        expected_closing_policy_version="1.0",
    )

    assert len(snapshot.prerequisite.observation_batches) == 2
    assert len(snapshot.prerequisite.prediction_mappings) == 2
    assert {item.sportsbook for item in snapshot.prerequisite.prediction_mappings} == {
        "DraftKings",
        "FanDuel",
    }
    assert {item.closing_line for item in snapshot.prerequisite.prediction_mappings} == {
        "32.5",
        "33",
    }


@pytest.mark.parametrize(
    "relative_glob",
    [
        "closing/observations/segments/*/*/closing_observations.jsonl",
        "closing/observations/segments/*/*/closing_manifest.json",
        "closing/observations/segments/*/*/COMPLETE",
        "closing/selections/segments/*/*/selected_closing_rows.jsonl",
        "closing/selections/segments/*/*/selection_manifest.json",
        "closing/selections/segments/*/*/COMPLETE",
    ],
)
def test_any_referenced_closing_file_mutation_blocks_planning(
    tmp_path: Path,
    relative_glob: str,
) -> None:
    bundle_path, paths, _, _, plan_build = _prepare_v2_plan(tmp_path)
    evidence_root = paths["evidence_root"] / "nba_player_points_evidence"
    target = next(evidence_root.glob(relative_glob))
    target.write_bytes(target.read_bytes() + b" ")

    result = run_manual_bundle(bundle_path)

    if result["plan"]["binding_status"] == "closing-bound":
        assert result["plan"]["approval_digest"] != plan_build.plan["approval_digest"]
        assert result["plan"]["closing_prerequisite_sha256"] != (
            plan_build.plan["closing_prerequisite_sha256"]
        )
    else:
        assert result["plan"]["binding_status"] == "invalid"
        assert result["plan"]["publishability"]["allowed"] is False
        assert any(
            "closing prerequisite invalid" in reason
            for reason in result["plan"]["publishability"]["blocked_reasons"]
        )


def test_plan_publish_detects_closing_change_after_approval(tmp_path: Path) -> None:
    bundle_path, paths, _, _, _ = _prepare_v2_plan(tmp_path)
    plan = run_manual_bundle(bundle_path)
    selection_path = next(
        (
            paths["evidence_root"]
            / "nba_player_points_evidence"
            / "closing"
            / "selections"
            / "segments"
        ).glob("*/*/selected_closing_rows.jsonl")
    )

    def mutate_after_approval() -> None:
        selection_path.write_bytes(selection_path.read_bytes() + b" ")

    with pytest.raises(
        NBAPlayerPointsPrerequisiteEvidenceError,
        match="changed after planning",
    ):
        run_manual_bundle(
            bundle_path,
            publish=True,
            approval_digest=plan["plan"]["approval_digest"],
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-06T04:01:00Z",
            after_approval_verified=mutate_after_approval,
        )
    assert not list(
        (
            paths["evidence_root"]
            / "nba_player_points_evidence"
            / "settlement"
            / "segments"
        ).glob("*/*/COMPLETE")
    )


def test_plan_publish_detects_byte_identical_path_replacement(tmp_path: Path) -> None:
    bundle_path, paths, _, _, _ = _prepare_v2_plan(tmp_path)
    plan = run_manual_bundle(bundle_path)
    selection_path = next(
        (
            paths["evidence_root"]
            / "nba_player_points_evidence"
            / "closing"
            / "selections"
            / "segments"
        ).glob("*/*/selected_closing_rows.jsonl")
    )

    def replace_after_approval() -> None:
        data = selection_path.read_bytes()
        replaced = selection_path.with_suffix(".replaced")
        selection_path.replace(replaced)
        selection_path.write_bytes(data)

    with pytest.raises(
        NBAPlayerPointsPrerequisiteEvidenceError,
        match="changed after planning",
    ):
        run_manual_bundle(
            bundle_path,
            publish=True,
            approval_digest=plan["plan"]["approval_digest"],
            approval_operator_id="manual-research-operator",
            approval_timestamp_utc="2026-06-06T04:01:00Z",
            after_approval_verified=replace_after_approval,
        )


@pytest.mark.parametrize(
    "stage",
    [
        "before_settlement_rows_write",
        "before_approval_envelope_write",
        "before_manifest_write",
        "before_complete_write",
        "before_atomic_rename",
    ],
)
def test_v2_atomic_interruptions_leave_no_completed_segment_and_release_lock(
    tmp_path: Path,
    stage: str,
) -> None:
    _, paths, _, context, plan_build = _prepare_v2_plan(tmp_path)
    envelope = _approval_envelope(context, plan_build)

    def fail_at(current: str) -> None:
        if current == stage:
            raise RuntimeError(stage)

    with pytest.raises(RuntimeError, match=stage):
        _write_v2(context, plan_build, envelope, failure_hook=fail_at)

    settlement_root = (
        paths["evidence_root"] / "nba_player_points_evidence" / "settlement"
    )
    assert not list(settlement_root.glob("segments/*/*/COMPLETE"))
    assert not (
        paths["evidence_root"]
        / "nba_player_points_evidence"
        / ".settlement-writer.lock"
    ).exists()
    recovered = _write_v2(context, plan_build, envelope)
    assert recovered.completion_status == "complete"


def test_concurrent_identical_and_conflicting_v2_publication(tmp_path: Path) -> None:
    _, _, _, context, plan_build = _prepare_v2_plan(tmp_path)
    envelope = _approval_envelope(context, plan_build)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda _: _write_v2(context, plan_build, envelope),
                range(2),
            )
        )
    assert sorted(item.completion_status for item in outcomes) == [
        "already_complete",
        "complete",
    ]

    changed_envelope = _approval_envelope(
        context,
        plan_build,
        timestamp="2026-06-06T04:02:00Z",
    )
    with pytest.raises(NBAPlayerPointsClosingBindingError, match="conflicting"):
        _write_v2(context, plan_build, changed_envelope)


def test_v2_verification_and_v1_legacy_classification(tmp_path: Path) -> None:
    bundle_path, paths, _, _, _ = _prepare_v2_plan(tmp_path)
    plan = run_manual_bundle(bundle_path)
    run_manual_bundle(
        bundle_path,
        publish=True,
        approval_digest=plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-06T04:01:00Z",
    )

    v2_report = verify_nba_player_points_settlement_evidence_v2(
        paths["evidence_root"]
    )
    dual_report = verify_nba_player_points_settlement_evidence(
        paths["evidence_root"],
        NBAPlayerPointsSettlementEvidenceWriterConfig(),
    )
    assert v2_report.ok is True
    assert v2_report.binding_status_counts["closing-bound"] == 1
    assert dual_report.ok is True
    assert dual_report.binding_status_counts["closing-bound"] == 1
    assert dual_report.binding_status_counts["invalid"] == 0


def test_v2_record_corruption_is_invalid_not_legacy(tmp_path: Path) -> None:
    bundle_path, paths, _, _, _ = _prepare_v2_plan(tmp_path)
    plan = run_manual_bundle(bundle_path)
    result = run_manual_bundle(
        bundle_path,
        publish=True,
        approval_digest=plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-06T04:01:00Z",
    )
    segment = Path(result["receipt"]["settlement_segment_path"])
    rows_path = segment / "settlement_rows.jsonl"
    rows_path.write_bytes(rows_path.read_bytes() + b" ")

    report = verify_nba_player_points_settlement_evidence(
        paths["evidence_root"],
        NBAPlayerPointsSettlementEvidenceWriterConfig(),
    )
    assert report.ok is False
    assert report.binding_status_counts["invalid"] == 1
    assert report.binding_status_counts["legacy-unbound"] == 0
    assert report.invalid_evidence_violations


def test_failed_external_receipt_write_does_not_invalidate_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_path, paths, _, _, _ = _prepare_v2_plan(tmp_path)
    plan = run_manual_bundle(bundle_path)
    original = runner_module._write_json_artifact

    def fail_receipt(root: Path, file_name: str, payload) -> None:
        if file_name == "approval_receipt.json":
            raise OSError("simulated external receipt failure")
        original(root, file_name, payload)

    monkeypatch.setattr(runner_module, "_write_json_artifact", fail_receipt)
    result = run_manual_bundle(
        bundle_path,
        publish=True,
        approval_digest=plan["plan"]["approval_digest"],
        approval_operator_id="manual-research-operator",
        approval_timestamp_utc="2026-06-06T04:01:00Z",
    )

    assert result["exit_code"] == 0
    assert result["approval_receipt_sha256"] is None
    assert result["receipt"]["external_receipt_write_status"] == "failed"
    assert result["receipt"]["evidence_remains_verified"] is True
    report = verify_nba_player_points_settlement_evidence(
        paths["evidence_root"],
        NBAPlayerPointsSettlementEvidenceWriterConfig(),
    )
    assert report.ok is True
    assert report.binding_status_counts["closing-bound"] == 1


def test_prerequisite_payload_rejects_noncanonical_line_and_extra_mapping(
    tmp_path: Path,
) -> None:
    _, paths, _, _, plan_build = _prepare_v2_plan(tmp_path)
    payload = plan_build.closing_snapshot.prerequisite.to_dict()
    payload["prediction_mappings"][0]["closing_line"] = "32.50"
    unsigned = {key: value for key, value in payload.items() if key != "closing_prerequisite_sha256"}
    payload["closing_prerequisite_sha256"] = canonical_sha256(unsigned)

    result = validate_nba_player_points_closing_prerequisite(
        paths["evidence_root"],
        payload,
    )
    assert result.ok is False
    assert result.binding_status == "invalid"
    assert "canonical decimal string" in result.violations[0]

    payload = plan_build.closing_snapshot.prerequisite.to_dict()
    payload["prediction_mappings"].append(dict(payload["prediction_mappings"][0]))
    unsigned = {
        key: value
        for key, value in payload.items()
        if key != "closing_prerequisite_sha256"
    }
    payload["closing_prerequisite_sha256"] = canonical_sha256(unsigned)
    result = validate_nba_player_points_closing_prerequisite(
        paths["evidence_root"],
        payload,
    )
    assert result.ok is False


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("sportsbook", "OtherBook"),
        ("market", "player_rebounds"),
        ("player_id", "other-player"),
        ("canonical_event_id", "other-event"),
        ("operating_date", "2026-06-04"),
        ("closing_line", "31.5"),
        ("closing_american_odds", -120),
        ("selection_record_hash", "0" * 64),
        ("source_observation_record_hash", "1" * 64),
        ("source_observation_id", "unknown-observation"),
        ("prediction_id", "unknown-prediction"),
    ],
)
def test_mismatched_mapping_identity_is_invalid(
    tmp_path: Path,
    target: str,
    value: object,
) -> None:
    _, paths, _, _, plan_build = _prepare_v2_plan(tmp_path)
    payload = plan_build.closing_snapshot.prerequisite.to_dict()
    payload["prediction_mappings"][0][target] = value
    payload["mapping_aggregate_sha256"] = canonical_sha256(
        payload["prediction_mappings"]
    )
    unsigned = {
        key: item
        for key, item in payload.items()
        if key != "closing_prerequisite_sha256"
    }
    payload["closing_prerequisite_sha256"] = canonical_sha256(unsigned)

    result = validate_nba_player_points_closing_prerequisite(
        paths["evidence_root"],
        payload,
    )

    assert result.ok is False
    assert result.binding_status == "invalid"


@pytest.mark.parametrize(
    ("policy_field", "value"),
    [
        ("closing_policy_id", "other-policy"),
        ("closing_policy_version", "2.0"),
        ("closing_window_start_seconds", 1200),
        ("same_book_required", False),
    ],
)
def test_mismatched_policy_identity_or_parameter_is_invalid(
    tmp_path: Path,
    policy_field: str,
    value: object,
) -> None:
    _, paths, _, _, plan_build = _prepare_v2_plan(tmp_path)
    payload = plan_build.closing_snapshot.prerequisite.to_dict()
    payload["closing_policy"][policy_field] = value
    unsigned = {
        key: item
        for key, item in payload.items()
        if key != "closing_prerequisite_sha256"
    }
    payload["closing_prerequisite_sha256"] = canonical_sha256(unsigned)

    result = validate_nba_player_points_closing_prerequisite(
        paths["evidence_root"],
        payload,
    )
    assert result.ok is False
    assert result.binding_status == "invalid"


def test_file_and_directory_symlinks_are_rejected(tmp_path: Path) -> None:
    _, paths, _, _, plan_build = _prepare_v2_plan(tmp_path)
    prerequisite = plan_build.closing_snapshot.prerequisite
    source_root = paths["evidence_root"]
    linked_root = tmp_path / "linked_evidence"
    try:
        linked_root.symlink_to(source_root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(NBAPlayerPointsClosingBindingError, match="symlink|reparse"):
        build_nba_player_points_closing_prerequisite(
            linked_root,
            operating_date=prerequisite.operating_date,
            physical_observation_batch_ids=[
                item.physical_observation_batch_id
                for item in prerequisite.observation_batches
            ],
            physical_selection_batch_id=(
                prerequisite.selection_batch.physical_selection_batch_id
            ),
            prediction_ids=[item.prediction_id for item in prerequisite.prediction_mappings],
            expected_closing_policy_id=prerequisite.closing_policy["closing_policy_id"],
            expected_closing_policy_version=prerequisite.closing_policy[
                "closing_policy_version"
            ],
        )

    copied_root = tmp_path / "file_link_evidence"
    shutil.copytree(source_root, copied_root)
    copied_actual = copied_root / "nba_player_points_evidence"
    selection_file = next(
        copied_actual.glob(
            "closing/selections/segments/*/*/selected_closing_rows.jsonl"
        )
    )
    outside_file = tmp_path / "outside_selection.jsonl"
    outside_file.write_bytes(selection_file.read_bytes())
    selection_file.unlink()
    selection_file.symlink_to(outside_file)
    with pytest.raises(NBAPlayerPointsClosingBindingError, match="symlink|reparse"):
        build_nba_player_points_closing_prerequisite(
            copied_root,
            operating_date=prerequisite.operating_date,
            physical_observation_batch_ids=[
                item.physical_observation_batch_id
                for item in prerequisite.observation_batches
            ],
            physical_selection_batch_id=(
                prerequisite.selection_batch.physical_selection_batch_id
            ),
            prediction_ids=[item.prediction_id for item in prerequisite.prediction_mappings],
            expected_closing_policy_id=prerequisite.closing_policy[
                "closing_policy_id"
            ],
            expected_closing_policy_version=prerequisite.closing_policy[
                "closing_policy_version"
            ],
        )


def test_specific_batch_path_traversal_is_rejected_by_runner(tmp_path: Path) -> None:
    bundle_path, _, _, _, _ = _prepare_v2_plan(tmp_path)
    bundle = _read_json(bundle_path)
    bundle["settlement"]["physical_closing_selection_batch_id"] = "../escape"
    bundle_path.write_text(json.dumps(bundle, sort_keys=True), encoding="utf-8")

    with pytest.raises(NBAPlayerPointsPathSecurityError):
        run_manual_bundle(bundle_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
def test_windows_junction_is_rejected_using_native_reparse_attributes(
    tmp_path: Path,
) -> None:
    _, paths, _, _, plan_build = _prepare_v2_plan(tmp_path)
    prerequisite = plan_build.closing_snapshot.prerequisite
    junction = tmp_path / "junction_evidence"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(paths["evidence_root"])],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.skip("junction creation unavailable")
    try:
        with pytest.raises(NBAPlayerPointsClosingBindingError, match="reparse"):
            build_nba_player_points_closing_prerequisite(
                junction,
                operating_date=prerequisite.operating_date,
                physical_observation_batch_ids=[
                    item.physical_observation_batch_id
                    for item in prerequisite.observation_batches
                ],
                physical_selection_batch_id=(
                    prerequisite.selection_batch.physical_selection_batch_id
                ),
                prediction_ids=[
                    item.prediction_id for item in prerequisite.prediction_mappings
                ],
                expected_closing_policy_id=prerequisite.closing_policy[
                    "closing_policy_id"
                ],
                expected_closing_policy_version=prerequisite.closing_policy[
                    "closing_policy_version"
                ],
            )
    finally:
        junction.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows long-path contract")
def test_supported_long_windows_paths(tmp_path: Path) -> None:
    nested = tmp_path
    for index in range(6):
        nested = nested / (f"long-{index}-" + "x" * 28)
    extended_nested = Path("\\\\?\\" + str(nested))
    _, _, _, _, plan_build = _prepare_v2_plan(extended_nested)
    assert plan_build.closing_snapshot is not None
    assert len(str(plan_build.closing_snapshot.evidence_root)) > 260
