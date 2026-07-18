"""Manual NBA player-points research orchestration runner.

This module coordinates committed offline NBA player-points research contracts
and append-only evidence writers. It performs no provider I/O, reads no
credentials, schedules nothing, calculates no bets, and does not touch
production scoring, grading, Kelly, bankroll, dashboard, or runtime entrypoints.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from types import MappingProxyType
from typing import Any, Final

from courtvision.sports.nba.player_minutes_research import (
    map_minutes_feature_case_fixture,
    validate_feature_rows,
)
from courtvision.sports.nba.player_points_assembly import (
    NBAPlayerPointsAssemblyBatchResult,
    assemble_nba_player_points_batch,
    build_projection_evidence,
)
from courtvision.sports.nba.player_points_closing import (
    NBAPlayerPointsClosingPolicy,
    NBAPlayerPointsClosingWriterConfig,
    verify_nba_player_points_closing_evidence,
    write_nba_player_points_closing_evidence,
)
from courtvision.sports.nba.player_points_crosswalk import (
    join_nba_player_points_crosswalk,
)
from courtvision.sports.nba.player_points_evidence import (
    NBA_PLAYER_POINTS_EVIDENCE_DIR_NAME,
    NBAPlayerPointsEvidenceWriterConfig,
    verify_nba_player_points_evidence,
    write_nba_player_points_evidence,
)
from courtvision.sports.nba.player_points_research import (
    NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL,
)
from courtvision.sports.nba.player_points_settlement import (
    settle_nba_player_points_predictions,
)
from courtvision.sports.nba.player_points_settlement_evidence import (
    NBAPlayerPointsSettlementEvidenceWriterConfig,
    NBAPlayerPointsSettlementPolicy,
    verify_nba_player_points_settlement_evidence,
    write_nba_player_points_settlement_evidence,
)
from courtvision.sports.nba.player_points_settlement_closing_binding import (
    NBA_PLAYER_POINTS_MANUAL_PLAN_V2_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_CONTRACT_VERSION,
    NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_RECEIPT_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_REQUEST_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION,
    ClosingEvidenceSnapshot,
    NBAPlayerPointsClosingBindingError,
    build_nba_player_points_closing_prerequisite,
    build_nba_player_points_settlement_approval_envelope,
    canonical_sha256 as _v2_canonical_sha256,
    preview_nba_player_points_settlement_evidence_v2_records,
    write_nba_player_points_settlement_evidence_v2,
)
from courtvision.sports.nba.player_points_source_adapters import (
    normalize_closing_player_points_odds,
    normalize_final_stat_sources,
    normalize_minutes_feature_inputs,
    normalize_pregame_player_points_odds,
    normalize_schedule_identity_sources,
    source_fixture_hash,
)


MANUAL_RUN_BUNDLE_SCHEMA_VERSION: Final = "nba-player-points-manual-run-v1"
MANUAL_RUN_PLAN_SCHEMA_VERSION: Final = "nba-player-points-manual-plan-v1"
APPROVAL_RECEIPT_SCHEMA_VERSION: Final = "nba-player-points-approval-receipt-v1"

SUPPORTED_OPERATIONS: Final = (
    "pregame-plan",
    "pregame-publish",
    "closing-plan",
    "closing-publish",
    "settlement-plan",
    "settlement-publish",
    "verify",
    "status",
)

EXIT_CODES: Final = MappingProxyType(
    {
        "success": 0,
        "invalid_command_or_bundle": 2,
        "plan_not_publishable": 3,
        "approval_digest_mismatch": 4,
        "prerequisite_evidence_invalid": 5,
        "publication_conflict": 6,
        "integrity_verification_failed": 7,
        "path_or_security_validation_failed": 8,
    }
)

APPROVAL_DIGEST_INPUTS: Final = (
    "bundle_schema_version",
    "publish_operation_stage",
    "operating_date",
    "repository_commit_sha",
    "current_repository_commit_sha",
    "repository branch and read-only repository state",
    "research_label",
    "bundle SHA-256 hash",
    "input source identities and SHA-256 file hashes",
    "reviewed mapping hashes where applicable",
    "projection evidence hashes where applicable",
    "prerequisite evidence verifier state and hashes",
    "policy IDs and versions",
    "run and batch IDs",
    "canonical planned rows and row hashes",
    "eligibility, exclusion, quarantine, conflict, and duplicate counts",
    "evidence_root identity",
)

PUBLISHABILITY_BLOCKS: Final = (
    "invalid bundle schema",
    "missing required input",
    "unsupported schema or policy version",
    "repository SHA mismatch",
    "source-hash mismatch",
    "repository branch or working tree mismatch",
    "target-game leakage",
    "naive timestamps",
    "path or symlink violation",
    "corrupt prerequisite evidence",
    "approval-digest mismatch",
    "conflicting identity that invalidates the stage",
    "writer/verifier precondition failure",
)

_UTC: Final = timezone.utc
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_SAFE_PATH_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_COMMIT_SHA_RE: Final = re.compile(r"^[0-9a-f]{7,40}$")
_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PROTECTED_AUDIT_DIR: Final = "docs/audits/courtvision_full_system_audit"
_PREVIEW_FILES: Final = (
    "plan.json",
    "source_summary.json",
    "normalized_preview.json",
    "eligibility_summary.json",
    "conflict_report.json",
    "integrity_preview.json",
    "approval_request.json",
)
_PRODUCTION_PATH_PARTS: Final = frozenset(
    {
        ".env",
        ".pytest_cache",
        "bankroll",
        "dashboard",
        "dashboards",
        "grading",
        "histories",
        "history",
        "kelly",
        "logs",
        "operator-board",
        "operator_board",
        "outputs",
        "production",
        "run_history",
        "runtime",
        "scheduled_tasks",
        "stake",
        "test_outputs",
        "wager",
        "wagers",
        "windows_tasks",
    }
)
_CREDENTIAL_PATH_MARKERS: Final = (
    ".env",
    "api_key",
    "apikey",
    "credential",
    "secret",
    "token",
)


class NBAPlayerPointsManualRunnerError(RuntimeError):
    """Base runner error carrying a stable CLI exit code."""

    exit_code = EXIT_CODES["invalid_command_or_bundle"]


class NBAPlayerPointsBundleError(NBAPlayerPointsManualRunnerError):
    """Raised when the manual-run bundle or command is invalid."""

    exit_code = EXIT_CODES["invalid_command_or_bundle"]


class NBAPlayerPointsPlanNotPublishableError(NBAPlayerPointsManualRunnerError):
    """Raised when a publish command is structurally blocked."""

    exit_code = EXIT_CODES["plan_not_publishable"]


class NBAPlayerPointsApprovalDigestMismatchError(NBAPlayerPointsManualRunnerError):
    """Raised when explicit approval is missing or does not match the plan."""

    exit_code = EXIT_CODES["approval_digest_mismatch"]


class NBAPlayerPointsPrerequisiteEvidenceError(NBAPlayerPointsManualRunnerError):
    """Raised when prerequisite prediction evidence is invalid."""

    exit_code = EXIT_CODES["prerequisite_evidence_invalid"]


class NBAPlayerPointsPublicationConflictError(NBAPlayerPointsManualRunnerError):
    """Raised when an append-only writer fails closed on a publication conflict."""

    exit_code = EXIT_CODES["publication_conflict"]


class NBAPlayerPointsIntegrityError(NBAPlayerPointsManualRunnerError):
    """Raised when post-publication verification fails."""

    exit_code = EXIT_CODES["integrity_verification_failed"]


class NBAPlayerPointsPathSecurityError(NBAPlayerPointsManualRunnerError):
    """Raised when a configured path is unsafe."""

    exit_code = EXIT_CODES["path_or_security_validation_failed"]


@dataclass(frozen=True, slots=True)
class _JsonSnapshot:
    path: Path
    sha256: str
    size_bytes: int
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _InputFile:
    field_name: str
    configured_path: str
    path: Path
    sha256: str
    size_bytes: int
    payload: Mapping[str, object]

    def summary(self, root: Path) -> dict[str, object]:
        return {
            "field_name": self.field_name,
            "configured_path": self.configured_path,
            "path": str(self.path),
            "path_relative_to_input_root": self.path.relative_to(root).as_posix(),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class _RepositoryState:
    repository_root: Path
    expected_branch: str | None
    current_branch: str
    current_head: str
    staged_changes: tuple[str, ...]
    tracked_worktree_changes: tuple[str, ...]
    unmerged_paths: tuple[str, ...]
    untracked_paths: tuple[str, ...]
    protected_untracked_paths: tuple[str, ...]
    unexpected_untracked_paths: tuple[str, ...]
    git_command_failures: tuple[str, ...]
    violations: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "repository_root": str(self.repository_root),
            "expected_branch": self.expected_branch,
            "current_branch": self.current_branch,
            "current_head": self.current_head,
            "staged_changes": list(self.staged_changes),
            "tracked_worktree_changes": list(self.tracked_worktree_changes),
            "unmerged_paths": list(self.unmerged_paths),
            "untracked_paths": list(self.untracked_paths),
            "protected_untracked_paths": list(self.protected_untracked_paths),
            "unexpected_untracked_paths": list(self.unexpected_untracked_paths),
            "git_command_failures": list(self.git_command_failures),
            "violations": list(self.violations),
            "protected_untracked_directory": _PROTECTED_AUDIT_DIR,
        }


@dataclass(frozen=True, slots=True)
class _BundleContext:
    bundle_path: Path
    bundle_hash: str
    bundle_size_bytes: int
    bundle: Mapping[str, object]
    bundle_schema_version: str
    repository_root: Path
    current_repository_commit_sha: str
    current_repository_branch: str
    expected_repository_branch: str | None
    repository_state: Mapping[str, object]
    operation_stage: str
    operating_date: str
    repository_commit_sha: str
    research_label: str
    input_root: Path
    preview_root: Path
    evidence_root: Path
    operator_id: str
    requested_at_utc: str


@dataclass(frozen=True, slots=True)
class _PlanBuild:
    plan: Mapping[str, object]
    source_summary: Mapping[str, object]
    normalized_preview: Mapping[str, object]
    eligibility_summary: Mapping[str, object]
    conflict_report: Mapping[str, object]
    integrity_preview: Mapping[str, object]
    approval_request: Mapping[str, object]
    digest_payload: Mapping[str, object]
    assembly_result: NBAPlayerPointsAssemblyBatchResult | None = None
    closing_observations: tuple[Mapping[str, object], ...] = ()
    settlement_rows: tuple[object, ...] = ()
    closing_config: NBAPlayerPointsClosingWriterConfig | None = None
    settlement_config: NBAPlayerPointsSettlementEvidenceWriterConfig | None = None
    closing_snapshot: ClosingEvidenceSnapshot | None = None
    logical_settlement_batch_id: str | None = None
    settlement_policy_v2: Mapping[str, object] | None = None


def run_manual_bundle(
    bundle_path: str | Path,
    *,
    publish: bool = False,
    approval_digest: str | None = None,
    approval_operator_id: str | None = None,
    approval_timestamp_utc: str | None = None,
    approval_note: str | None = None,
    repository_root: str | Path | None = None,
    after_approval_verified: Callable[[], None] | None = None,
) -> Mapping[str, object]:
    """Run one explicit manual bundle operation.

    Planning is the default. Publication requires ``publish=True`` plus an exact
    approval digest and explicit approval timestamp.
    """

    context = _load_bundle_context(bundle_path, repository_root=repository_root)
    effective_operation = _effective_operation(context.operation_stage, publish=publish)

    if effective_operation == "status":
        return _status_result(context)
    if effective_operation == "verify":
        return _verify_result(context)

    if effective_operation.endswith("-plan"):
        plan_build = _build_plan(context, effective_operation)
        _write_preview_files(context, plan_build)
        return {
            "exit_code": (
                EXIT_CODES["success"]
                if plan_build.plan["publishability"]["allowed"]
                else EXIT_CODES["plan_not_publishable"]
            ),
            "operation_stage": effective_operation,
            "publication_attempted": False,
            "plan": _json_ready(plan_build.plan),
        }

    if effective_operation.endswith("-publish"):
        if not publish:
            raise NBAPlayerPointsBundleError(
                "publish operations require the explicit --publish flag"
            )
        if not approval_digest:
            raise NBAPlayerPointsApprovalDigestMismatchError(
                "publication requires --approval-digest"
            )
        approval_operator = _require_path_id(
            approval_operator_id,
            "approval_operator_id",
        )
        approval_time = _require_utc_timestamp(
            approval_timestamp_utc,
            "approval_timestamp_utc",
        )
        plan_operation = effective_operation.replace("-publish", "-plan")
        plan_build = _build_plan(context, plan_operation)
        _write_preview_files(context, plan_build)
        computed = str(plan_build.plan["approval_digest"])
        if computed != approval_digest:
            raise NBAPlayerPointsApprovalDigestMismatchError(
                "approval digest mismatch"
            )
        if not plan_build.plan["publishability"]["allowed"]:
            if _has_prerequisite_evidence_block(plan_build.plan["publishability"]["blocked_reasons"]):
                raise NBAPlayerPointsPrerequisiteEvidenceError(
                    "invalid prerequisite evidence: "
                    + "; ".join(plan_build.plan["publishability"]["blocked_reasons"])
                )
            raise NBAPlayerPointsPlanNotPublishableError(
                "plan is not publishable: "
                + "; ".join(plan_build.plan["publishability"]["blocked_reasons"])
            )
        if after_approval_verified is not None:
            after_approval_verified()
        receipt = _publish_plan(
            context,
            plan_build,
            operation_stage=effective_operation,
            approval_digest=approval_digest,
            approval_operator_id=approval_operator,
            approval_timestamp_utc=approval_time,
            approval_note=approval_note,
        )
        response = {
            "exit_code": EXIT_CODES["success"],
            "operation_stage": effective_operation,
            "publication_attempted": True,
            "plan": _json_ready(plan_build.plan),
            "receipt": _json_ready(receipt),
        }
        if context.bundle_schema_version == NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION:
            receipt_path = context.preview_root / "approval_receipt.json"
            response["bundle_schema_version"] = context.bundle_schema_version
            response["plan_schema_version"] = NBA_PLAYER_POINTS_MANUAL_PLAN_V2_SCHEMA_VERSION
            response["approval_contract_version"] = (
                NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_CONTRACT_VERSION
            )
            response["evidence_schema_version"] = (
                NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION
            )
            response["binding_status"] = "closing-bound"
            response["approval_receipt_sha256"] = (
                _sha256_bytes(receipt_path.read_bytes()) if receipt_path.exists() else None
            )
        return response

    raise NBAPlayerPointsBundleError(f"unsupported operation: {effective_operation}")


def exit_code_meanings() -> Mapping[str, int]:
    """Return stable CLI exit code meanings."""

    return EXIT_CODES


def bundle_schema_definition() -> Mapping[str, object]:
    """Return the manual-run bundle schema summary."""

    return {
        "bundle_schema_version": MANUAL_RUN_BUNDLE_SCHEMA_VERSION,
        "supported_bundle_schema_versions": [
            MANUAL_RUN_BUNDLE_SCHEMA_VERSION,
            NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION,
        ],
        "supported_operations": list(SUPPORTED_OPERATIONS),
        "required_common_fields": [
            "bundle_schema_version",
            "operation_stage",
            "operating_date",
            "repository_commit_sha",
            "research_label",
            "input_root",
            "preview_root",
            "evidence_root",
            "operator_id",
            "requested_at_utc",
        ],
        "required_stage_sections": {
            "pregame": [
                "prediction_run_id",
                "pregame_odds_payloads",
                "schedule_identity_payloads",
                "minutes_payloads",
                "projection_payloads",
                "feature_cutoff_timestamp_utc",
                "prediction_timestamp_utc",
                "model_id",
            ],
            "closing": [
                "closing_batch_id",
                "selection_batch_id",
                "prediction_references",
                "closing_policy_id",
                "closing_policy_version",
                "closing_odds_payloads",
                "collection_timestamp_utc",
            ],
            "settlement": [
                "settlement_batch_id",
                "prediction_ids",
                "settlement_policy_id",
                "settlement_policy_version",
                "final_stat_payloads",
                "settlement_timestamp_utc",
            ],
            "settlement_v2": [
                "logical_settlement_batch_id",
                "physical_closing_selection_batch_id",
                "expected_physical_observation_batch_ids",
                "expected_closing_policy_id",
                "expected_closing_policy_version",
                "requested_prediction_ids",
                "settlement_policy_id",
                "settlement_policy_version",
                "final_stat_input_files",
                "settlement_timestamp_utc",
            ],
        },
        "path_rules": [
            "all roots are explicit",
            "input_root, preview_root, and evidence_root must be pairwise disjoint after resolution",
            "referenced input files must remain under input_root",
            "preview artifacts remain under preview_root",
            "evidence publication remains under evidence_root",
            "path traversal, blank IDs, separators in path IDs, and symlink escapes are rejected",
            "known production output, history, operator-board, Kelly, bankroll, runtime, log, and dashboard paths are rejected",
            "no production evidence root default is provided",
        ],
        "repository_state_rules": [
            "current HEAD must match repository_commit_sha",
            "repository_branch is enforced when configured",
            "staged, tracked working-tree, and unmerged changes block publication",
            "unexpected untracked paths block publication",
            f"only {_PROTECTED_AUDIT_DIR}/ is allowed as an untracked path prefix",
        ],
        "approval_digest_inputs": list(APPROVAL_DIGEST_INPUTS),
        "publishability_blocks": list(PUBLISHABILITY_BLOCKS),
    }


def _load_bundle_context(
    bundle_path: str | Path,
    *,
    repository_root: str | Path | None,
) -> _BundleContext:
    configured_bundle_path = Path(bundle_path)
    if _path_has_parent_traversal(configured_bundle_path):
        raise NBAPlayerPointsPathSecurityError("bundle path must not contain '..'")
    absolute_bundle_path = configured_bundle_path.expanduser().resolve(strict=True)
    _reject_credential_path(absolute_bundle_path, "bundle")
    bundle_snapshot = _read_json_snapshot(absolute_bundle_path)
    bundle_payload = bundle_snapshot.payload
    if bundle_payload.get("approval_digest") not in (None, ""):
        raise NBAPlayerPointsBundleError(
            "approval_digest must be supplied by the operator command, not trusted from the bundle"
        )
    bundle_schema_version = bundle_payload.get("bundle_schema_version")
    if bundle_schema_version not in {
        MANUAL_RUN_BUNDLE_SCHEMA_VERSION,
        NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION,
    }:
        raise NBAPlayerPointsBundleError(
            "unsupported or missing bundle_schema_version"
        )
    operation_stage = _require_text(
        bundle_payload.get("operation_stage"),
        "operation_stage",
    )
    if operation_stage not in SUPPORTED_OPERATIONS:
        raise NBAPlayerPointsBundleError(
            f"unsupported operation_stage: {operation_stage!r}"
        )
    if (
        bundle_schema_version == NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION
        and operation_stage not in {"settlement-plan", "settlement-publish"}
    ):
        raise NBAPlayerPointsBundleError(
            "nba-player-points-manual-run-v2 supports only settlement-plan "
            "and settlement-publish"
        )
    operating_date = _require_operating_date(
        bundle_payload.get("operating_date"),
        "operating_date",
    )
    repository_commit_sha = _require_commit_sha(
        bundle_payload.get("repository_commit_sha"),
        "repository_commit_sha",
    )
    research_label = _require_text(bundle_payload.get("research_label"), "research_label")
    if research_label != NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL:
        raise NBAPlayerPointsBundleError("research_label is unsupported")
    operator_id = _require_path_id(bundle_payload.get("operator_id"), "operator_id")
    requested_at_utc = _require_utc_timestamp(
        bundle_payload.get("requested_at_utc"),
        "requested_at_utc",
    )
    expected_repository_branch = _optional_repository_branch(
        bundle_payload.get("repository_branch"),
    )
    repo_root = (
        Path(repository_root).expanduser().resolve()
        if repository_root is not None
        else Path.cwd().resolve()
    )
    repository_state = _inspect_repository_state(
        repo_root,
        expected_branch=expected_repository_branch,
    )
    current_sha = repository_state.current_head
    input_root = _resolve_root(bundle_payload.get("input_root"), "input_root", must_exist=True)
    preview_root = _resolve_root(bundle_payload.get("preview_root"), "preview_root", must_exist=False)
    evidence_root = _resolve_root(bundle_payload.get("evidence_root"), "evidence_root", must_exist=False)
    _validate_disjoint_roots(
        input_root=input_root,
        preview_root=preview_root,
        evidence_root=evidence_root,
    )

    return _BundleContext(
        bundle_path=absolute_bundle_path,
        bundle_hash=bundle_snapshot.sha256,
        bundle_size_bytes=bundle_snapshot.size_bytes,
        bundle=MappingProxyType(_json_clone_mapping(bundle_payload)),
        bundle_schema_version=str(bundle_schema_version),
        repository_root=repo_root,
        current_repository_commit_sha=current_sha,
        current_repository_branch=repository_state.current_branch,
        expected_repository_branch=expected_repository_branch,
        repository_state=MappingProxyType(repository_state.to_dict()),
        operation_stage=operation_stage,
        operating_date=operating_date,
        repository_commit_sha=repository_commit_sha,
        research_label=research_label,
        input_root=input_root,
        preview_root=preview_root,
        evidence_root=evidence_root,
        operator_id=operator_id,
        requested_at_utc=requested_at_utc,
    )


def _effective_operation(operation_stage: str, *, publish: bool) -> str:
    if operation_stage in {"verify", "status"}:
        if publish:
            raise NBAPlayerPointsBundleError(
                f"{operation_stage} does not support publication"
            )
        return operation_stage
    if operation_stage.endswith("-publish"):
        if not publish:
            raise NBAPlayerPointsBundleError(
                "bundle requests a publish operation but --publish was not supplied"
            )
        return operation_stage
    if operation_stage.endswith("-plan"):
        return operation_stage.replace("-plan", "-publish") if publish else operation_stage
    raise NBAPlayerPointsBundleError(f"unsupported operation_stage: {operation_stage}")


def _build_plan(context: _BundleContext, operation_stage: str) -> _PlanBuild:
    if operation_stage == "pregame-plan":
        return _build_pregame_plan(context)
    if operation_stage == "closing-plan":
        return _build_closing_plan(context)
    if operation_stage == "settlement-plan":
        if context.bundle_schema_version == NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION:
            return _build_settlement_plan_v2(context)
        return _build_settlement_plan(context)
    raise NBAPlayerPointsBundleError(f"unsupported plan operation: {operation_stage}")


def _build_pregame_plan(context: _BundleContext) -> _PlanBuild:
    section = _stage_section(context.bundle, "pregame")
    prediction_run_id = _require_path_id(section.get("prediction_run_id"), "pregame.prediction_run_id")
    model_id = _require_path_id(section.get("model_id"), "pregame.model_id")
    feature_cutoff = _require_utc_timestamp(
        section.get("feature_cutoff_timestamp_utc"),
        "pregame.feature_cutoff_timestamp_utc",
    )
    prediction_timestamp = _require_utc_timestamp(
        section.get("prediction_timestamp_utc"),
        "pregame.prediction_timestamp_utc",
    )
    pregame_policy = {
        "pregame_policy_id": _require_path_id(
            section.get("pregame_policy_id", "nba-player-points-manual-pregame-v1"),
            "pregame.pregame_policy_id",
        ),
        "pregame_policy_version": _require_text(
            section.get("pregame_policy_version", "1.0"),
            "pregame.pregame_policy_version",
        ),
    }
    source_files: list[_InputFile] = []
    pregame_payloads = _load_input_payloads(context, section, "pregame_odds_payloads", source_files)
    identity_payloads = _load_input_payloads(context, section, "schedule_identity_payloads", source_files)
    minutes_payloads = _load_input_payloads(context, section, "minutes_payloads", source_files)
    projection_payloads = _load_input_payloads(context, section, "projection_payloads", source_files)
    probability_payloads = _load_input_payloads(
        context,
        section,
        "probability_payloads",
        source_files,
        required=False,
    )

    structural_errors: list[str] = []
    adapter_details: list[Mapping[str, object]] = []
    market_records: list[Mapping[str, object]] = []
    schedule_rows: list[Mapping[str, object]] = []
    player_rows: list[Mapping[str, object]] = []
    mapping_records: list[Mapping[str, object]] = []
    minutes_feature_rows: list[Mapping[str, object]] = []

    for payload in pregame_payloads:
        result = normalize_pregame_player_points_odds(payload)
        adapter_details.append(_adapter_summary("pregame_player_points_odds", result))
        market_records.extend(result.normalized_records)
    for payload in identity_payloads:
        result = normalize_schedule_identity_sources(payload)
        adapter_details.append(_adapter_summary("schedule_identity", result))
        schedule_rows.extend(result.records_by_type("schedule_event"))
        player_rows.extend(result.records_by_type("roster_player"))
        mapping_records.extend(result.records_by_type("reviewed_mapping_artifact"))
    for payload in minutes_payloads:
        result = normalize_minutes_feature_inputs(
            payload,
            feature_cutoff_timestamp_utc=feature_cutoff,
        )
        adapter_details.append(_adapter_summary("minutes_feature_input", result))
        for record in result.normalized_records:
            try:
                minutes_row = map_minutes_feature_case_fixture(record)
                minutes_feature_rows.append(_minutes_payload_for_assembly(minutes_row.to_dict()))
            except Exception as exc:
                structural_errors.append(f"minutes feature validation failed: {exc}")

    try:
        validate_feature_rows(
            tuple(map_minutes_feature_case_fixture(record) for payload in minutes_payloads for record in normalize_minutes_feature_inputs(payload, feature_cutoff_timestamp_utc=feature_cutoff).normalized_records)
        )
    except Exception as exc:
        structural_errors.append(f"minutes feature row collection invalid: {exc}")

    mapping_artifact = (
        dict(mapping_records[0]["mapping_artifact"])
        if mapping_records and isinstance(mapping_records[0].get("mapping_artifact"), Mapping)
        else None
    )
    try:
        crosswalk = join_nba_player_points_crosswalk(
            market_records,
            schedule_rows,
            player_rows,
            reviewed_event_mapping=mapping_artifact,
            reviewed_player_mapping=mapping_artifact,
        )
        crosswalk_rows = tuple(row.to_dict() for row in crosswalk.rows)
    except Exception as exc:
        crosswalk_rows = ()
        structural_errors.append(f"crosswalk join failed: {exc}")

    projection_candidates = _payload_list(projection_payloads)
    probability_candidates = _payload_list(probability_payloads)
    assembly_records: list[Mapping[str, object]] = []
    projection_hashes: list[Mapping[str, object]] = []
    for index, row in enumerate(crosswalk_rows):
        market = _require_mapping(row.get("original_odds_row"), "crosswalk.original_odds_row")
        try:
            projection = _projection_for_market(projection_candidates, market, index)
            build_projection_evidence(projection, commence_time_utc=market.get("commence_time_utc"))
            projection_hashes.append(
                {
                    "projection_source_id": str(projection.get("projection_source_id", "")),
                    "projection_source_hash": str(projection.get("projection_source_hash", "")),
                    "canonical_hash": source_fixture_hash(projection),
                }
            )
            minutes = _find_minutes_for_market(minutes_feature_rows, row, market)
            if minutes is None:
                structural_errors.append(
                    "missing projected-minutes evidence for "
                    + str(market.get("provider_event_id"))
                    + "/"
                    + str(row.get("canonical_player_id"))
                )
                continue
            probability = _probability_for_market(probability_candidates, market, index)
            assembly_records.append(
                {
                    "market": market,
                    "crosswalk": _assembly_crosswalk_payload(
                        row,
                        market,
                        schedule_rows,
                        player_rows,
                        mapping_records,
                    ),
                    "minutes": minutes,
                    "projection": projection,
                    "probability": probability,
                    "provenance": {
                        "prediction_run_id": prediction_run_id,
                        "prediction_timestamp_utc": prediction_timestamp,
                        "model_id": model_id,
                        "repository_commit_sha": context.repository_commit_sha,
                        "source_manifest_id": _first_text(
                            section.get("source_manifest_id"),
                            minutes.get("source_manifest_id"),
                            "manual-pregame-source-manifest",
                        ),
                        "research_label": context.research_label,
                    },
                }
            )
        except Exception as exc:
            structural_errors.append(f"assembly input preparation failed: {exc}")

    assembly_result: NBAPlayerPointsAssemblyBatchResult | None = None
    if assembly_records:
        try:
            assembly_result = assemble_nba_player_points_batch(
                assembly_records,
                manifest_created_at_utc=section.get("manifest_created_at_utc", prediction_timestamp),
            )
        except Exception as exc:
            structural_errors.append(f"pregame assembly failed: {exc}")
    else:
        structural_errors.append("no assembly records were prepared")

    source_summary = {
        "source_files": _source_file_summaries(source_files, context.input_root),
        "adapter_results": adapter_details,
        "projection_hashes": _canonical_list(projection_hashes),
    }
    normalized_preview = {
        "market_records": _canonical_list(market_records),
        "schedule_rows": _canonical_list(schedule_rows),
        "player_rows": _canonical_list(player_rows),
        "reviewed_mapping_records": _canonical_list(mapping_records),
        "crosswalk_rows": _canonical_list(crosswalk_rows),
        "minutes_feature_rows": _canonical_list(minutes_feature_rows),
        "assembly": assembly_result.to_dict() if assembly_result else {},
    }
    eligibility_summary = _pregame_eligibility_summary(assembly_result, crosswalk_rows)
    conflict_report = {
        "structural_errors": structural_errors,
        "adapter_conflicts": [
            detail for detail in adapter_details if int(detail["counts"]["conflicting_records"]) > 0
        ],
        "duplicate_diagnostics": (
            assembly_result.to_dict().get("duplicate_diagnostics", [])
            if assembly_result
            else []
        ),
        "conflicting_rows": (
            [row.to_dict() for row in assembly_result.conflicting_rows]
            if assembly_result
            else []
        ),
    }
    blocked = _common_publish_blocks(context, structural_errors)
    if not assembly_result or not assembly_result.eligible_projection_rows:
        blocked.append("no eligible pregame projection rows")
    if assembly_result and assembly_result.conflicting_rows:
        blocked.append("pregame assembly produced conflicting rows")
    if _adapter_blocks(adapter_details):
        blocked.append("source adapters produced invalid, quarantined, or conflicting records")

    return _finalize_plan(
        context,
        operation_stage="pregame-plan",
        publish_operation_stage="pregame-publish",
        run_or_batch_ids={
            "prediction_run_id": prediction_run_id,
        },
        policies=pregame_policy,
        source_summary=source_summary,
        normalized_preview=normalized_preview,
        eligibility_summary=eligibility_summary,
        conflict_report=conflict_report,
        integrity_preview={"prediction_evidence_verifier": "not_run_for_pregame_plan"},
        blocked_reasons=blocked,
        assembly_result=assembly_result,
    )


def _build_closing_plan(context: _BundleContext) -> _PlanBuild:
    section = _stage_section(context.bundle, "closing")
    closing_batch_id = _require_path_id(section.get("closing_batch_id"), "closing.closing_batch_id")
    selection_batch_id = _require_path_id(section.get("selection_batch_id"), "closing.selection_batch_id")
    collection_timestamp = _require_utc_timestamp(
        section.get("collection_timestamp_utc"),
        "closing.collection_timestamp_utc",
    )
    policy = NBAPlayerPointsClosingPolicy(
        closing_policy_id=_require_path_id(
            section.get("closing_policy_id"),
            "closing.closing_policy_id",
        ),
        closing_policy_version=_require_text(
            section.get("closing_policy_version"),
            "closing.closing_policy_version",
        ),
        closing_window_start_seconds=int(section.get("closing_window_start_seconds", 30 * 60)),
        closing_window_end_seconds=int(section.get("closing_window_end_seconds", 0)),
        same_book_required=bool(section.get("same_book_required", True)),
        same_market_required=bool(section.get("same_market_required", True)),
    )
    config = NBAPlayerPointsClosingWriterConfig(policy=policy)
    source_files: list[_InputFile] = []
    closing_payloads = _load_input_payloads(context, section, "closing_odds_payloads", source_files)
    prediction_verify = verify_nba_player_points_evidence(
        context.evidence_root,
        NBAPlayerPointsEvidenceWriterConfig(),
    )
    structural_errors: list[str] = []
    if not prediction_verify.ok:
        structural_errors.append(
            "prediction evidence invalid: " + "; ".join(prediction_verify.violations)
        )
    references = _prediction_references_from_section(
        context,
        section.get("prediction_references"),
        prediction_verify_ok=prediction_verify.ok,
    )
    adapter_details: list[Mapping[str, object]] = []
    observations: list[Mapping[str, object]] = []
    for payload in closing_payloads:
        result = normalize_closing_player_points_odds(
            payload,
            prediction_references=references,
        )
        adapter_details.append(_adapter_summary("closing_player_points_odds", result))
        observations.extend(result.normalized_records)

    source_summary = {
        "source_files": _source_file_summaries(source_files, context.input_root),
        "adapter_results": adapter_details,
        "prediction_reference_count": len(references),
    }
    normalized_preview = {
        "closing_observations": _canonical_list(observations),
        "effective_selection_preview": _closing_selection_preview(observations, config),
    }
    eligibility_summary = _closing_eligibility_summary(observations, config)
    conflict_report = {
        "structural_errors": structural_errors,
        "adapter_conflicts": [
            detail for detail in adapter_details if int(detail["counts"]["conflicting_records"]) > 0
        ],
        "post_tip_observations": [
            row for row in observations if not _closing_observation_is_pre_tip(row)
        ],
    }
    blocked = _common_publish_blocks(context, structural_errors)
    if not prediction_verify.ok:
        blocked.append("corrupt prerequisite prediction evidence")
    if not observations:
        blocked.append("no closing observations were prepared")
    if _adapter_blocks(adapter_details):
        blocked.append("source adapters produced invalid, quarantined, or conflicting records")

    return _finalize_plan(
        context,
        operation_stage="closing-plan",
        publish_operation_stage="closing-publish",
        run_or_batch_ids={
            "closing_batch_id": closing_batch_id,
            "selection_batch_id": selection_batch_id,
        },
        policies=policy.to_dict(),
        source_summary=source_summary,
        normalized_preview=normalized_preview,
        eligibility_summary=eligibility_summary,
        conflict_report=conflict_report,
        integrity_preview={
            "prediction_evidence": prediction_verify.to_dict(),
            "closing_evidence": verify_nba_player_points_closing_evidence(
                context.evidence_root,
                config,
            ).to_dict(),
        },
        blocked_reasons=blocked,
        closing_observations=tuple(observations),
        closing_config=config,
    )


def _build_settlement_plan(context: _BundleContext) -> _PlanBuild:
    section = _stage_section(context.bundle, "settlement")
    settlement_batch_id = _require_path_id(
        section.get("settlement_batch_id"),
        "settlement.settlement_batch_id",
    )
    settlement_timestamp = _require_utc_timestamp(
        section.get("settlement_timestamp_utc"),
        "settlement.settlement_timestamp_utc",
    )
    policy = NBAPlayerPointsSettlementPolicy(
        settlement_policy_id=_require_path_id(
            section.get("settlement_policy_id"),
            "settlement.settlement_policy_id",
        ),
        settlement_policy_version=_require_text(
            section.get("settlement_policy_version"),
            "settlement.settlement_policy_version",
        ),
    )
    config = NBAPlayerPointsSettlementEvidenceWriterConfig(policy=policy)
    prediction_ids = tuple(
        _require_path_id(item, "settlement.prediction_ids[]")
        for item in _require_sequence(section.get("prediction_ids"), "settlement.prediction_ids")
    )
    source_files: list[_InputFile] = []
    final_stat_payloads = _load_input_payloads(context, section, "final_stat_payloads", source_files)
    prediction_verify = verify_nba_player_points_evidence(
        context.evidence_root,
        NBAPlayerPointsEvidenceWriterConfig(),
    )
    structural_errors: list[str] = []
    if not prediction_verify.ok:
        structural_errors.append(
            "prediction evidence invalid: " + "; ".join(prediction_verify.violations)
        )
    prediction_rows = _prediction_rows_from_evidence(context, prediction_ids)
    if len(prediction_rows) != len(prediction_ids):
        structural_errors.append("one or more requested prediction_ids were not found")
    final_records: list[Mapping[str, object]] = []
    adapter_details: list[Mapping[str, object]] = []
    for payload in final_stat_payloads:
        result = normalize_final_stat_sources(payload)
        adapter_details.append(_adapter_summary("final_stat", result))
        final_records.extend(result.normalized_records)

    settlement_result = None
    if prediction_rows and final_records:
        try:
            settlement_result = settle_nba_player_points_predictions(
                prediction_rows,
                [_crosswalk_row_from_prediction(row) for row in prediction_rows],
                final_records,
                settlement_timestamp_utc=settlement_timestamp,
                repository_commit_sha=context.repository_commit_sha,
                research_label=context.research_label,
            )
        except Exception as exc:
            structural_errors.append(f"settlement contract failed: {exc}")
    else:
        structural_errors.append("settlement requires prediction rows and final-stat rows")

    settlement_rows = tuple(settlement_result.rows) if settlement_result else ()
    source_summary = {
        "source_files": _source_file_summaries(source_files, context.input_root),
        "adapter_results": adapter_details,
        "prediction_reference_count": len(prediction_rows),
    }
    normalized_preview = {
        "prediction_rows": _canonical_list(prediction_rows),
        "final_stat_rows": _canonical_list(final_records),
        "settlement_rows": [row.to_dict() for row in settlement_rows],
        "settlement_diagnostics": (
            _json_ready(settlement_result.diagnostics) if settlement_result else {}
        ),
    }
    eligibility_summary = _settlement_eligibility_summary(settlement_rows)
    conflict_report = {
        "structural_errors": structural_errors,
        "adapter_conflicts": [
            detail for detail in adapter_details if int(detail["counts"]["conflicting_records"]) > 0
        ],
        "terminal_conflicts": [
            row.to_dict()
            for row in settlement_rows
            if getattr(row, "settlement_status", None) == "conflicting"
        ],
        "diagnostics": _json_ready(settlement_result.diagnostics) if settlement_result else {},
    }
    blocked = _common_publish_blocks(context, structural_errors)
    blocked.append("closing_bound_settlement_v2_required")
    if not prediction_verify.ok:
        blocked.append("corrupt prerequisite prediction evidence")
    if not settlement_rows:
        blocked.append("no settlement rows were prepared")
    if any(getattr(row, "settlement_status", None) == "conflicting" for row in settlement_rows):
        blocked.append("terminal settlement conflict")
    if _adapter_blocks(adapter_details):
        blocked.append("source adapters produced invalid, quarantined, or conflicting records")

    return _finalize_plan(
        context,
        operation_stage="settlement-plan",
        publish_operation_stage="settlement-publish",
        run_or_batch_ids={"settlement_batch_id": settlement_batch_id},
        policies=policy.to_dict(),
        source_summary=source_summary,
        normalized_preview=normalized_preview,
        eligibility_summary=eligibility_summary,
        conflict_report=conflict_report,
        integrity_preview={
            "prediction_evidence": prediction_verify.to_dict(),
            "settlement_evidence": verify_nba_player_points_settlement_evidence(
                context.evidence_root,
                config,
            ).to_dict(),
        },
        blocked_reasons=blocked,
        settlement_rows=settlement_rows,
        settlement_config=config,
    )


def _build_settlement_plan_v2(context: _BundleContext) -> _PlanBuild:
    section = _stage_section(context.bundle, "settlement")
    logical_batch_id = _require_path_id(
        section.get("logical_settlement_batch_id"),
        "settlement.logical_settlement_batch_id",
    )
    selection_batch_id = _require_path_id(
        section.get("physical_closing_selection_batch_id"),
        "settlement.physical_closing_selection_batch_id",
    )
    observation_batch_ids = tuple(
        _require_path_id(item, "settlement.expected_physical_observation_batch_ids[]")
        for item in _require_sequence(
            section.get("expected_physical_observation_batch_ids"),
            "settlement.expected_physical_observation_batch_ids",
        )
    )
    if len(set(observation_batch_ids)) != len(observation_batch_ids):
        raise NBAPlayerPointsBundleError(
            "expected_physical_observation_batch_ids must be unique"
        )
    policy_id = _require_path_id(
        section.get("expected_closing_policy_id"),
        "settlement.expected_closing_policy_id",
    )
    policy_version = _require_text(
        section.get("expected_closing_policy_version"),
        "settlement.expected_closing_policy_version",
    )
    logical_closing_id = (
        _require_path_id(section.get("logical_closing_id"), "settlement.logical_closing_id")
        if section.get("logical_closing_id") not in (None, "")
        else None
    )
    prediction_ids = tuple(
        _require_path_id(item, "settlement.requested_prediction_ids[]")
        for item in _require_sequence(
            section.get("requested_prediction_ids"),
            "settlement.requested_prediction_ids",
        )
    )
    if len(set(prediction_ids)) != len(prediction_ids):
        raise NBAPlayerPointsBundleError("requested_prediction_ids must be unique")
    settlement_timestamp = _require_utc_timestamp(
        section.get("settlement_timestamp_utc"),
        "settlement.settlement_timestamp_utc",
    )
    settlement_policy = NBAPlayerPointsSettlementPolicy(
        settlement_policy_id=_require_path_id(
            section.get("settlement_policy_id"),
            "settlement.settlement_policy_id",
        ),
        settlement_policy_version=_require_text(
            section.get("settlement_policy_version"),
            "settlement.settlement_policy_version",
        ),
    ).to_dict()

    source_files: list[_InputFile] = []
    final_stat_payloads = _load_input_payloads(
        context,
        section,
        "final_stat_input_files",
        source_files,
    )
    prediction_verify = verify_nba_player_points_evidence(
        context.evidence_root,
        NBAPlayerPointsEvidenceWriterConfig(),
    )
    structural_errors: list[str] = []
    if not prediction_verify.ok:
        structural_errors.append(
            "prediction evidence invalid: " + "; ".join(prediction_verify.violations)
        )
    prediction_rows = _prediction_rows_from_evidence(context, prediction_ids)
    if len(prediction_rows) != len(prediction_ids):
        structural_errors.append("one or more requested prediction_ids were not found")

    final_records: list[Mapping[str, object]] = []
    adapter_details: list[Mapping[str, object]] = []
    for payload in final_stat_payloads:
        result = normalize_final_stat_sources(payload)
        adapter_details.append(_adapter_summary("final_stat", result))
        final_records.extend(result.normalized_records)

    settlement_result = None
    if prediction_rows and final_records:
        try:
            settlement_result = settle_nba_player_points_predictions(
                prediction_rows,
                [_crosswalk_row_from_prediction(row) for row in prediction_rows],
                final_records,
                settlement_timestamp_utc=settlement_timestamp,
                repository_commit_sha=context.repository_commit_sha,
                research_label=context.research_label,
            )
        except Exception as exc:
            structural_errors.append(f"settlement contract failed: {exc}")
    else:
        structural_errors.append("settlement requires prediction rows and final-stat rows")
    settlement_rows = tuple(settlement_result.rows) if settlement_result else ()

    closing_snapshot: ClosingEvidenceSnapshot | None = None
    closing_prerequisite: Mapping[str, object] | None = None
    v2_records: tuple[Mapping[str, object], ...] = ()
    try:
        closing_snapshot = build_nba_player_points_closing_prerequisite(
            context.evidence_root,
            operating_date=context.operating_date,
            physical_observation_batch_ids=observation_batch_ids,
            physical_selection_batch_id=selection_batch_id,
            prediction_ids=prediction_ids,
            expected_closing_policy_id=policy_id,
            expected_closing_policy_version=policy_version,
            logical_closing_id=logical_closing_id,
        )
        closing_prerequisite = closing_snapshot.prerequisite.to_dict()
        if settlement_rows:
            v2_records = preview_nba_player_points_settlement_evidence_v2_records(
                settlement_rows,
                closing_snapshot.prerequisite,
            )
    except NBAPlayerPointsClosingBindingError as exc:
        structural_errors.append(f"closing prerequisite invalid: {exc}")

    prediction_prerequisites = [
        {
            "prediction_id": row.get("prediction_id"),
            "prediction_run_id": row.get("prediction_run_id"),
            "prediction_ledger_record_hash": row.get("ledger_record_hash"),
            "prediction_assembled_record_hash": row.get("assembled_record_hash"),
            "prediction_evidence_segment": row.get("prediction_evidence_segment"),
        }
        for row in prediction_rows
    ]
    source_summary = {
        "source_files": _source_file_summaries(source_files, context.input_root),
        "adapter_results": adapter_details,
        "prediction_prerequisites": prediction_prerequisites,
        "prediction_reference_count": len(prediction_rows),
    }
    normalized_preview = {
        "prediction_rows": _canonical_list(prediction_rows),
        "final_stat_rows": _canonical_list(final_records),
        "settlement_rows": [row.to_dict() for row in settlement_rows],
        "v2_settlement_evidence_envelope_previews": [
            _json_ready(row) for row in v2_records
        ],
        "settlement_diagnostics": (
            _json_ready(settlement_result.diagnostics) if settlement_result else {}
        ),
    }
    eligibility_summary = _settlement_eligibility_summary(settlement_rows)
    conflict_report = {
        "structural_errors": structural_errors,
        "adapter_conflicts": [
            detail
            for detail in adapter_details
            if int(detail["counts"]["conflicting_records"]) > 0
        ],
        "terminal_conflicts": [
            row.to_dict()
            for row in settlement_rows
            if getattr(row, "settlement_status", None) == "conflicting"
        ],
        "diagnostics": _json_ready(settlement_result.diagnostics) if settlement_result else {},
    }
    blocked = _common_publish_blocks(context, structural_errors)
    if not prediction_verify.ok:
        blocked.append("corrupt prerequisite prediction evidence")
    if closing_snapshot is None:
        blocked.append("closing prerequisite evidence invalid")
    if not settlement_rows:
        blocked.append("no settlement rows were prepared")
    if any(getattr(row, "settlement_status", None) == "conflicting" for row in settlement_rows):
        blocked.append("terminal settlement conflict")
    if _adapter_blocks(adapter_details):
        blocked.append("source adapters produced invalid, quarantined, or conflicting records")

    integrity_preview = {
        "prediction_evidence": prediction_verify.to_dict(),
        "settlement_evidence": verify_nba_player_points_settlement_evidence(
            context.evidence_root,
            NBAPlayerPointsSettlementEvidenceWriterConfig(),
        ).to_dict(),
        "closing_prerequisite_validation": {
            "ok": closing_snapshot is not None,
            "binding_status": "closing-bound" if closing_snapshot is not None else "invalid",
            "closing_prerequisite_sha256": (
                closing_snapshot.prerequisite.closing_prerequisite_sha256
                if closing_snapshot is not None
                else None
            ),
        },
    }
    return _finalize_settlement_plan_v2(
        context,
        logical_settlement_batch_id=logical_batch_id,
        settlement_policy=settlement_policy,
        source_summary=source_summary,
        normalized_preview=normalized_preview,
        eligibility_summary=eligibility_summary,
        conflict_report=conflict_report,
        integrity_preview=integrity_preview,
        blocked_reasons=blocked,
        closing_prerequisite=closing_prerequisite,
        closing_snapshot=closing_snapshot,
        settlement_rows=settlement_rows,
    )


def _finalize_settlement_plan_v2(
    context: _BundleContext,
    *,
    logical_settlement_batch_id: str,
    settlement_policy: Mapping[str, object],
    source_summary: Mapping[str, object],
    normalized_preview: Mapping[str, object],
    eligibility_summary: Mapping[str, object],
    conflict_report: Mapping[str, object],
    integrity_preview: Mapping[str, object],
    blocked_reasons: Sequence[str],
    closing_prerequisite: Mapping[str, object] | None,
    closing_snapshot: ClosingEvidenceSnapshot | None,
    settlement_rows: tuple[object, ...],
) -> _PlanBuild:
    blocked = tuple(dict.fromkeys(str(reason) for reason in blocked_reasons if reason))
    prerequisite_hash = (
        closing_prerequisite.get("closing_prerequisite_sha256")
        if isinstance(closing_prerequisite, Mapping)
        else None
    )
    digest_payload = {
        "approval_contract_version": NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_CONTRACT_VERSION,
        "bundle_schema_version": NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION,
        "bundle_sha256": context.bundle_hash,
        "bundle_size_bytes": context.bundle_size_bytes,
        "operation_stage": "settlement-publish",
        "operating_date": context.operating_date,
        "repository_commit_sha": context.repository_commit_sha,
        "current_repository_commit_sha": context.current_repository_commit_sha,
        "expected_repository_branch": context.expected_repository_branch,
        "current_repository_branch": context.current_repository_branch,
        "repository_state": context.repository_state,
        "research_label": context.research_label,
        "final_stat_source_summaries": source_summary,
        "prediction_prerequisite_identities": source_summary.get(
            "prediction_prerequisites",
            [],
        ),
        "closing_prerequisite": closing_prerequisite,
        "closing_prerequisite_sha256": prerequisite_hash,
        "settlement_policy": settlement_policy,
        "logical_settlement_batch_id": logical_settlement_batch_id,
        "normalized_final_stat_rows": normalized_preview.get("final_stat_rows", []),
        "v1_settlement_calculation_rows": normalized_preview.get("settlement_rows", []),
        "v2_settlement_evidence_envelope_previews": normalized_preview.get(
            "v2_settlement_evidence_envelope_previews",
            [],
        ),
        "eligibility_summary": eligibility_summary,
        "conflict_diagnostics": conflict_report,
        "structural_diagnostics": {
            "prediction_evidence": integrity_preview.get("prediction_evidence"),
            "closing_prerequisite_validation": integrity_preview.get(
                "closing_prerequisite_validation"
            ),
        },
    }
    approval_digest = _v2_canonical_sha256(digest_payload)
    approval_request = {
        "approval_request_schema_version": NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_REQUEST_SCHEMA_VERSION,
        "approval_contract_version": NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_CONTRACT_VERSION,
        "operation_stage": "settlement-publish",
        "operator_id": context.operator_id,
        "approval_digest": approval_digest,
        "approval_digest_algorithm": "sha256-canonical-json-v2",
        "publishable": not blocked,
        "blocked_reasons": list(blocked),
        "bundle_sha256": context.bundle_hash,
        "repository_commit_sha": context.repository_commit_sha,
        "logical_settlement_batch_id": logical_settlement_batch_id,
        "closing_prerequisite_sha256": prerequisite_hash,
    }
    prerequisite = closing_snapshot.prerequisite if closing_snapshot is not None else None
    plan = {
        "plan_schema_version": NBA_PLAYER_POINTS_MANUAL_PLAN_V2_SCHEMA_VERSION,
        "bundle_schema_version": NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION,
        "approval_contract_version": NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_CONTRACT_VERSION,
        "evidence_schema_version": NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_V2_SCHEMA_VERSION,
        "binding_status": "closing-bound" if closing_snapshot is not None else "invalid",
        "operation_stage": "settlement-plan",
        "publish_operation_stage": "settlement-publish",
        "operating_date": context.operating_date,
        "repository_commit_sha": context.repository_commit_sha,
        "current_repository_commit_sha": context.current_repository_commit_sha,
        "expected_repository_branch": context.expected_repository_branch,
        "current_repository_branch": context.current_repository_branch,
        "repository_state": _json_ready(context.repository_state),
        "research_label": context.research_label,
        "input_root": str(context.input_root),
        "preview_root": str(context.preview_root),
        "evidence_root": str(context.evidence_root),
        "logical_settlement_batch_id": logical_settlement_batch_id,
        "logical_closing_id": prerequisite.logical_closing_id if prerequisite else None,
        "physical_observation_batch_ids": (
            [item.physical_observation_batch_id for item in prerequisite.observation_batches]
            if prerequisite
            else []
        ),
        "physical_selection_batch_id": (
            prerequisite.selection_batch.physical_selection_batch_id if prerequisite else None
        ),
        "observation_batch_snapshots": (
            [item.to_dict() for item in prerequisite.observation_batches] if prerequisite else []
        ),
        "selection_batch_snapshot": prerequisite.selection_batch.to_dict() if prerequisite else None,
        "prediction_mapping_count": len(prerequisite.prediction_mappings) if prerequisite else 0,
        "mapping_aggregate_sha256": prerequisite.mapping_aggregate_sha256 if prerequisite else None,
        "per_prediction_closing_binding_summary": (
            [item.to_dict() for item in prerequisite.prediction_mappings] if prerequisite else []
        ),
        "closing_prerequisite": closing_prerequisite,
        "closing_prerequisite_sha256": prerequisite_hash,
        "settlement_policy": _json_ready(settlement_policy),
        "source_summary": _json_ready(source_summary),
        "normalized_preview": _json_ready(normalized_preview),
        "eligibility_summary": _json_ready(eligibility_summary),
        "conflict_report": _json_ready(conflict_report),
        "integrity_preview": _json_ready(integrity_preview),
        "publishability": {
            "allowed": not blocked,
            "blocked_reasons": list(blocked),
            "documented_blocking_conditions": list(PUBLISHABILITY_BLOCKS),
        },
        "approval_digest": approval_digest,
        "approval_digest_algorithm": "sha256-canonical-json-v2",
    }
    return _PlanBuild(
        plan=MappingProxyType(plan),
        source_summary=MappingProxyType(dict(source_summary)),
        normalized_preview=MappingProxyType(dict(normalized_preview)),
        eligibility_summary=MappingProxyType(dict(eligibility_summary)),
        conflict_report=MappingProxyType(dict(conflict_report)),
        integrity_preview=MappingProxyType(dict(integrity_preview)),
        approval_request=MappingProxyType(approval_request),
        digest_payload=MappingProxyType(digest_payload),
        settlement_rows=settlement_rows,
        closing_snapshot=closing_snapshot,
        logical_settlement_batch_id=logical_settlement_batch_id,
        settlement_policy_v2=MappingProxyType(dict(settlement_policy)),
    )


def _finalize_plan(
    context: _BundleContext,
    *,
    operation_stage: str,
    publish_operation_stage: str,
    run_or_batch_ids: Mapping[str, object],
    policies: Mapping[str, object],
    source_summary: Mapping[str, object],
    normalized_preview: Mapping[str, object],
    eligibility_summary: Mapping[str, object],
    conflict_report: Mapping[str, object],
    integrity_preview: Mapping[str, object],
    blocked_reasons: Sequence[str],
    assembly_result: NBAPlayerPointsAssemblyBatchResult | None = None,
    closing_observations: tuple[Mapping[str, object], ...] = (),
    settlement_rows: tuple[object, ...] = (),
    closing_config: NBAPlayerPointsClosingWriterConfig | None = None,
    settlement_config: NBAPlayerPointsSettlementEvidenceWriterConfig | None = None,
) -> _PlanBuild:
    blocked = tuple(dict.fromkeys(str(reason) for reason in blocked_reasons if reason))
    digest_payload = {
        "bundle_schema_version": MANUAL_RUN_BUNDLE_SCHEMA_VERSION,
        "bundle_hash": context.bundle_hash,
        "bundle_size_bytes": context.bundle_size_bytes,
        "operation_stage": publish_operation_stage,
        "operating_date": context.operating_date,
        "repository_commit_sha": context.repository_commit_sha,
        "current_repository_commit_sha": context.current_repository_commit_sha,
        "expected_repository_branch": context.expected_repository_branch,
        "current_repository_branch": context.current_repository_branch,
        "repository_state": context.repository_state,
        "research_label": context.research_label,
        "source_summary": source_summary,
        "integrity_preview": integrity_preview,
        "policies": policies,
        "run_or_batch_ids": dict(run_or_batch_ids),
        "planned_rows": _planned_rows_for_digest(normalized_preview),
        "eligibility_summary": eligibility_summary,
        "conflict_counts": _conflict_counts(conflict_report),
        "evidence_root_identity": str(context.evidence_root),
    }
    approval_digest = _canonical_sha256(digest_payload)
    approval_request = {
        "approval_request_schema_version": "nba-player-points-approval-request-v1",
        "operation_stage": publish_operation_stage,
        "operator_id": context.operator_id,
        "approval_digest": approval_digest,
        "approval_digest_algorithm": "sha256-canonical-json-v1",
        "approval_digest_inputs": list(APPROVAL_DIGEST_INPUTS),
        "publishable": not blocked,
        "blocked_reasons": list(blocked),
        "bundle_hash": context.bundle_hash,
        "repository_commit_sha": context.repository_commit_sha,
        "current_repository_commit_sha": context.current_repository_commit_sha,
        "operating_date": context.operating_date,
        "run_or_batch_ids": dict(run_or_batch_ids),
        "evidence_root": str(context.evidence_root),
        "approval_command_requirements": [
            "--publish",
            "--approval-digest <EXACT_DIGEST>",
            "--approval-operator-id <OPERATOR_ID>",
            "--approval-timestamp-utc <UTC_TIMESTAMP>",
        ],
    }
    plan = {
        "plan_schema_version": MANUAL_RUN_PLAN_SCHEMA_VERSION,
        "bundle_schema_version": MANUAL_RUN_BUNDLE_SCHEMA_VERSION,
        "operation_stage": operation_stage,
        "publish_operation_stage": publish_operation_stage,
        "operating_date": context.operating_date,
        "repository_commit_sha": context.repository_commit_sha,
        "current_repository_commit_sha": context.current_repository_commit_sha,
        "expected_repository_branch": context.expected_repository_branch,
        "current_repository_branch": context.current_repository_branch,
        "repository_state": _json_ready(context.repository_state),
        "research_label": context.research_label,
        "input_root": str(context.input_root),
        "preview_root": str(context.preview_root),
        "evidence_root": str(context.evidence_root),
        "operator_id": context.operator_id,
        "requested_at_utc": context.requested_at_utc,
        "run_or_batch_ids": dict(run_or_batch_ids),
        "policies": _json_ready(policies),
        "source_summary": _json_ready(source_summary),
        "normalized_preview": _json_ready(normalized_preview),
        "eligibility_summary": _json_ready(eligibility_summary),
        "conflict_report": _json_ready(conflict_report),
        "integrity_preview": _json_ready(integrity_preview),
        "publishability": {
            "allowed": not blocked,
            "blocked_reasons": list(blocked),
            "documented_blocking_conditions": list(PUBLISHABILITY_BLOCKS),
        },
        "approval_digest": approval_digest,
        "approval_digest_algorithm": "sha256-canonical-json-v1",
        "approval_digest_inputs": list(APPROVAL_DIGEST_INPUTS),
    }
    return _PlanBuild(
        plan=MappingProxyType(plan),
        source_summary=MappingProxyType(dict(source_summary)),
        normalized_preview=MappingProxyType(dict(normalized_preview)),
        eligibility_summary=MappingProxyType(dict(eligibility_summary)),
        conflict_report=MappingProxyType(dict(conflict_report)),
        integrity_preview=MappingProxyType(dict(integrity_preview)),
        approval_request=MappingProxyType(approval_request),
        digest_payload=MappingProxyType(digest_payload),
        assembly_result=assembly_result,
        closing_observations=closing_observations,
        settlement_rows=settlement_rows,
        closing_config=closing_config,
        settlement_config=settlement_config,
    )


def _publish_plan(
    context: _BundleContext,
    plan_build: _PlanBuild,
    *,
    operation_stage: str,
    approval_digest: str,
    approval_operator_id: str,
    approval_timestamp_utc: str,
    approval_note: str | None,
) -> Mapping[str, object]:
    if operation_stage == "pregame-publish":
        if plan_build.assembly_result is None:
            raise NBAPlayerPointsPlanNotPublishableError("pregame assembly result missing")
        config = NBAPlayerPointsEvidenceWriterConfig()
        try:
            result = write_nba_player_points_evidence(
                plan_build.assembly_result,
                plan_build.assembly_result.source_manifest_preview,
                context.evidence_root,
                config,
                repository_commit_sha=context.repository_commit_sha,
                writer_timestamp_utc=approval_timestamp_utc,
            )
        except Exception as exc:
            raise NBAPlayerPointsPublicationConflictError(str(exc)) from exc
        verifier = verify_nba_player_points_evidence(context.evidence_root, config)
        if not verifier.ok:
            raise NBAPlayerPointsIntegrityError(
                "prediction evidence verification failed: "
                + "; ".join(verifier.violations)
            )
        receipt = _receipt(
            context,
            operation_stage=operation_stage,
            approval_digest=approval_digest,
            approval_operator_id=approval_operator_id,
            approval_timestamp_utc=approval_timestamp_utc,
            approval_note=approval_note,
            publication_result=result.to_dict(),
            verifier_result=verifier.to_dict(),
            segment_references={
                "run_directory": str(result.run_directory),
                "ledger_path": str(result.ledger_path),
            },
            manifest_hashes=_manifest_hashes([result.run_directory / "run_manifest.json"]),
        )
    elif operation_stage == "closing-publish":
        if plan_build.closing_config is None:
            raise NBAPlayerPointsPlanNotPublishableError("closing config missing")
        try:
            result = write_nba_player_points_closing_evidence(
                context.evidence_root,
                plan_build.closing_observations,
                plan_build.closing_config,
                collection_timestamp_utc=_stage_section(context.bundle, "closing")[
                    "collection_timestamp_utc"
                ],
                repository_commit_sha=context.repository_commit_sha,
                writer_timestamp_utc=approval_timestamp_utc,
            )
        except Exception as exc:
            raise NBAPlayerPointsPublicationConflictError(str(exc)) from exc
        verifier = verify_nba_player_points_closing_evidence(
            context.evidence_root,
            plan_build.closing_config,
        )
        if not verifier.ok:
            raise NBAPlayerPointsIntegrityError(
                "closing evidence verification failed: "
                + "; ".join(verifier.violations)
            )
        receipt = _receipt(
            context,
            operation_stage=operation_stage,
            approval_digest=approval_digest,
            approval_operator_id=approval_operator_id,
            approval_timestamp_utc=approval_timestamp_utc,
            approval_note=approval_note,
            publication_result=result.to_dict(),
            verifier_result=verifier.to_dict(),
            segment_references={
                "observation_segment_directory": (
                    str(result.observation_segment_directory)
                    if result.observation_segment_directory
                    else None
                ),
                "selection_segment_directory": (
                    str(result.selection_segment_directory)
                    if result.selection_segment_directory
                    else None
                ),
            },
            manifest_hashes=_manifest_hashes(
                [
                    path / "closing_manifest.json"
                    for path in [result.observation_segment_directory]
                    if path is not None
                ]
                + [
                    path / "selection_manifest.json"
                    for path in [result.selection_segment_directory]
                    if path is not None
                ]
            ),
        )
    elif (
        operation_stage == "settlement-publish"
        and context.bundle_schema_version == NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION
    ):
        if (
            plan_build.closing_snapshot is None
            or plan_build.logical_settlement_batch_id is None
            or plan_build.settlement_policy_v2 is None
        ):
            raise NBAPlayerPointsPrerequisiteEvidenceError(
                "closing-bound settlement plan is missing prerequisite state"
            )
        try:
            plan_build.closing_snapshot.assert_unchanged()
        except NBAPlayerPointsClosingBindingError as exc:
            if "reparse" in str(exc).casefold() or "path" in str(exc).casefold():
                raise NBAPlayerPointsPathSecurityError(str(exc)) from exc
            raise NBAPlayerPointsPrerequisiteEvidenceError(str(exc)) from exc
        envelope = build_nba_player_points_settlement_approval_envelope(
            approval_digest=approval_digest,
            operator_id=approval_operator_id,
            approval_timestamp_utc=approval_timestamp_utc,
            bundle_sha256=context.bundle_hash,
            repository_commit_sha=context.repository_commit_sha,
            logical_settlement_batch_id=plan_build.logical_settlement_batch_id,
            closing_prerequisite_sha256=(
                plan_build.closing_snapshot.prerequisite.closing_prerequisite_sha256
            ),
            prediction_ids=[
                item.prediction_id
                for item in plan_build.closing_snapshot.prerequisite.prediction_mappings
            ],
        )
        try:
            result = write_nba_player_points_settlement_evidence_v2(
                context.evidence_root,
                plan_build.settlement_rows,
                plan_build.closing_snapshot,
                envelope,
                logical_settlement_batch_id=plan_build.logical_settlement_batch_id,
                settlement_policy=plan_build.settlement_policy_v2,
                collection_timestamp_utc=_stage_section(context.bundle, "settlement").get(
                    "collection_timestamp_utc",
                    _stage_section(context.bundle, "settlement")["settlement_timestamp_utc"],
                ),
                repository_commit_sha=context.repository_commit_sha,
                writer_timestamp_utc=approval_timestamp_utc,
            )
        except NBAPlayerPointsClosingBindingError as exc:
            message = str(exc)
            lowered = message.casefold()
            if "changed after planning" in lowered or "prerequisite" in lowered:
                raise NBAPlayerPointsPrerequisiteEvidenceError(message) from exc
            if "reparse" in lowered or "path traversal" in lowered or "symlink" in lowered:
                raise NBAPlayerPointsPathSecurityError(message) from exc
            raise NBAPlayerPointsPublicationConflictError(message) from exc
        verifier = verify_nba_player_points_settlement_evidence(
            context.evidence_root,
            NBAPlayerPointsSettlementEvidenceWriterConfig(),
        )
        if not verifier.ok:
            raise NBAPlayerPointsIntegrityError(
                "closing-bound settlement evidence verification failed: "
                + "; ".join(verifier.violations)
            )
        manifest_path = result.settlement_segment_directory / "settlement_manifest.json"
        manifest_file_hash = _sha256_bytes(manifest_path.read_bytes())
        receipt = {
            "receipt_schema_version": (
                NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_RECEIPT_SCHEMA_VERSION
            ),
            "approval_contract_version": (
                NBA_PLAYER_POINTS_SETTLEMENT_APPROVAL_CONTRACT_VERSION
            ),
            "approval_envelope_identity": {
                "file": "approval_envelope.json",
                "sha256": result.settlement_manifest["approval_envelope_sha256"],
                "approval_digest": approval_digest,
            },
            "physical_settlement_batch_id": result.physical_settlement_batch_id,
            "logical_settlement_batch_id": result.logical_settlement_batch_id,
            "settlement_segment_path": str(result.settlement_segment_directory),
            "settlement_rows_sha256": result.settlement_manifest[
                "settlement_rows_sha256"
            ],
            "manifest_internal_hash": result.settlement_manifest[
                "settlement_manifest_hash"
            ],
            "manifest_file_sha256": manifest_file_hash,
            "closing_prerequisite_sha256": result.settlement_manifest[
                "closing_prerequisite_sha256"
            ],
            "writer_verifier_result": _json_ready(result.writer_verifier_result),
            "full_verifier_result": verifier.to_dict(),
            "publication_result": result.to_dict(),
            "publication_semantics": (
                "idempotent_replay"
                if result.completion_status == "already_complete"
                else "new_publication"
            ),
            "completion_timestamp_utc": approval_timestamp_utc,
            "operator_id": approval_operator_id,
            "operator_identity_disclaimer": (
                "Operator metadata is supplied by the offline caller and is not "
                "cryptographic proof of the operator's real-world identity."
            ),
            "evidence_trust_statement": (
                "This external receipt is not part of the immutable settlement "
                "manifest and is not the evidence trust root."
            ),
        }
    elif operation_stage == "settlement-publish":
        if plan_build.settlement_config is None:
            raise NBAPlayerPointsPlanNotPublishableError("settlement config missing")
        try:
            result = write_nba_player_points_settlement_evidence(
                context.evidence_root,
                plan_build.settlement_rows,
                plan_build.settlement_config,
                collection_timestamp_utc=_stage_section(context.bundle, "settlement").get(
                    "collection_timestamp_utc",
                    _stage_section(context.bundle, "settlement")["settlement_timestamp_utc"],
                ),
                repository_commit_sha=context.repository_commit_sha,
                writer_timestamp_utc=approval_timestamp_utc,
            )
        except Exception as exc:
            raise NBAPlayerPointsPublicationConflictError(str(exc)) from exc
        verifier = verify_nba_player_points_settlement_evidence(
            context.evidence_root,
            plan_build.settlement_config,
        )
        if not verifier.ok:
            raise NBAPlayerPointsIntegrityError(
                "settlement evidence verification failed: "
                + "; ".join(verifier.violations)
            )
        receipt = _receipt(
            context,
            operation_stage=operation_stage,
            approval_digest=approval_digest,
            approval_operator_id=approval_operator_id,
            approval_timestamp_utc=approval_timestamp_utc,
            approval_note=approval_note,
            publication_result=result.to_dict(),
            verifier_result=verifier.to_dict(),
            segment_references={
                "settlement_segment_directory": str(result.settlement_segment_directory),
            },
            manifest_hashes=_manifest_hashes(
                [result.settlement_segment_directory / "settlement_manifest.json"]
            ),
        )
    else:
        raise NBAPlayerPointsBundleError(f"unsupported publish operation: {operation_stage}")

    if (
        operation_stage == "settlement-publish"
        and context.bundle_schema_version == NBA_PLAYER_POINTS_MANUAL_RUN_V2_SCHEMA_VERSION
    ):
        try:
            _write_json_artifact(context.preview_root, "approval_receipt.json", receipt)
        except Exception as exc:
            receipt = {
                **dict(receipt),
                "external_receipt_write_status": "failed",
                "external_receipt_write_error": str(exc),
                "evidence_remains_verified": True,
            }
    else:
        _write_json_artifact(context.preview_root, "approval_receipt.json", receipt)
    return receipt


def _receipt(
    context: _BundleContext,
    *,
    operation_stage: str,
    approval_digest: str,
    approval_operator_id: str,
    approval_timestamp_utc: str,
    approval_note: str | None,
    publication_result: Mapping[str, object],
    verifier_result: Mapping[str, object],
    segment_references: Mapping[str, object],
    manifest_hashes: Mapping[str, object],
) -> Mapping[str, object]:
    return {
        "receipt_schema_version": APPROVAL_RECEIPT_SCHEMA_VERSION,
        "operation_stage": operation_stage,
        "operator_id": approval_operator_id,
        "bundle_operator_id": context.operator_id,
        "approval_timestamp_utc": approval_timestamp_utc,
        "approval_note": approval_note,
        "approval_digest": approval_digest,
        "bundle_hash": context.bundle_hash,
        "repository_commit_sha": context.repository_commit_sha,
        "current_repository_commit_sha": context.current_repository_commit_sha,
        "operating_date": context.operating_date,
        "run_or_batch_ids": dict(_require_mapping(context.bundle.get(operation_stage.split("-")[0], {}), "stage")),
        "evidence_segment_references": _json_ready(segment_references),
        "evidence_manifest_hashes": _json_ready(manifest_hashes),
        "publication_result": _json_ready(publication_result),
        "publication_semantics": (
            "idempotent_replay"
            if publication_result.get("completion_status") == "already_complete"
            else "new_publication"
        ),
        "verifier_result": _json_ready(verifier_result),
        "completed_at_utc": approval_timestamp_utc,
        "identity_statement": (
            "This receipt records operator-supplied approval metadata for an "
            "offline research workflow; it is not cryptographic proof of the "
            "human operator's real-world identity."
        ),
    }


def _verify_result(context: _BundleContext) -> Mapping[str, object]:
    prediction_config = NBAPlayerPointsEvidenceWriterConfig()
    closing_config = NBAPlayerPointsClosingWriterConfig()
    settlement_config = NBAPlayerPointsSettlementEvidenceWriterConfig()
    prediction_report = verify_nba_player_points_evidence(context.evidence_root, prediction_config)
    closing_report = verify_nba_player_points_closing_evidence(context.evidence_root, closing_config)
    settlement_report = verify_nba_player_points_settlement_evidence(
        context.evidence_root,
        settlement_config,
    )
    result = {
        "exit_code": (
            EXIT_CODES["success"]
            if prediction_report.ok and closing_report.ok and settlement_report.ok
            else EXIT_CODES["integrity_verification_failed"]
        ),
        "operation_stage": "verify",
        "publication_attempted": False,
        "prediction_evidence": prediction_report.to_dict(),
        "closing_evidence": closing_report.to_dict(),
        "settlement_evidence": settlement_report.to_dict(),
        "settlement_binding_status_counts": dict(
            settlement_report.binding_status_counts
        ),
        "settlement_schema_versions": list(settlement_report.schema_versions),
        "settlement_approval_contract_versions": list(
            settlement_report.approval_contract_versions
        ),
        "closing_prerequisite_hashes": list(
            settlement_report.closing_prerequisite_hashes
        ),
        "logical_settlement_batch_ids": list(
            settlement_report.logical_settlement_batch_ids
        ),
        "physical_settlement_batch_ids": list(
            settlement_report.physical_settlement_batch_ids
        ),
        "legacy_unbound_warning_count": len(settlement_report.warnings),
        "invalid_evidence_violation_count": len(
            settlement_report.invalid_evidence_violations
        ),
        "read_only": True,
        "repairs_attempted": False,
    }
    _write_json_artifact(context.preview_root, "verify_report.json", result)
    return result


def _status_result(context: _BundleContext) -> Mapping[str, object]:
    actual_evidence_root = _actual_evidence_root(context.evidence_root)
    preview_files = sorted(
        path.relative_to(context.preview_root).as_posix()
        for path in context.preview_root.rglob("*")
        if path.is_file()
    ) if context.preview_root.exists() else []
    evidence_files = sorted(
        path.relative_to(actual_evidence_root).as_posix()
        for path in actual_evidence_root.rglob("*")
        if path.is_file()
    ) if actual_evidence_root.exists() else []
    return {
        "exit_code": EXIT_CODES["success"],
        "operation_stage": "status",
        "publication_attempted": False,
        "repository_commit_sha": context.repository_commit_sha,
        "current_repository_commit_sha": context.current_repository_commit_sha,
        "input_root": str(context.input_root),
        "preview_root": str(context.preview_root),
        "evidence_root": str(context.evidence_root),
        "actual_evidence_root": str(actual_evidence_root),
        "preview_file_count": len(preview_files),
        "evidence_file_count": len(evidence_files),
        "read_only": True,
    }


def _write_preview_files(context: _BundleContext, plan_build: _PlanBuild) -> None:
    preview_payloads = {
        "plan.json": plan_build.plan,
        "source_summary.json": plan_build.source_summary,
        "normalized_preview.json": plan_build.normalized_preview,
        "eligibility_summary.json": plan_build.eligibility_summary,
        "conflict_report.json": plan_build.conflict_report,
        "integrity_preview.json": plan_build.integrity_preview,
        "approval_request.json": plan_build.approval_request,
    }
    for file_name in _PREVIEW_FILES:
        _write_json_artifact(context.preview_root, file_name, preview_payloads[file_name])


def _stage_section(bundle: Mapping[str, object], stage: str) -> Mapping[str, object]:
    section = bundle.get(stage)
    if not isinstance(section, Mapping):
        raise NBAPlayerPointsBundleError(f"{stage} section is required")
    return MappingProxyType(_json_clone_mapping(section))


def _load_input_payloads(
    context: _BundleContext,
    section: Mapping[str, object],
    field_name: str,
    source_files: list[_InputFile],
    *,
    required: bool = True,
) -> tuple[Mapping[str, object], ...]:
    value = section.get(field_name)
    if value in (None, "", []):
        if required:
            raise NBAPlayerPointsBundleError(f"{field_name} is required")
        return ()
    refs = _path_refs(value, field_name)
    payloads: list[Mapping[str, object]] = []
    for ref in refs:
        input_file = _load_input_file(context, f"{field_name}[]", ref)
        payloads.append(input_file.payload)
        source_files.append(input_file)
    return tuple(payloads)


def _path_refs(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return (_require_text(value, field_name),)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        refs = tuple(_require_text(item, field_name) for item in value)
        if not refs:
            raise NBAPlayerPointsBundleError(f"{field_name} must not be empty")
        return refs
    raise NBAPlayerPointsBundleError(f"{field_name} must be a path string or list")


def _load_input_file(context: _BundleContext, field_name: str, configured: str) -> _InputFile:
    configured_path = Path(configured)
    if _path_has_parent_traversal(configured_path):
        raise NBAPlayerPointsPathSecurityError(f"{field_name} must not contain '..'")
    _reject_credential_path(configured_path, field_name)
    candidate = configured_path if configured_path.is_absolute() else context.input_root / configured_path
    _assert_no_symlink_components(candidate.expanduser(), field_name)
    resolved = candidate.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise NBAPlayerPointsBundleError(f"{field_name} is not a file: {resolved}")
    if not _is_relative_to(resolved, context.input_root):
        raise NBAPlayerPointsPathSecurityError(
            f"{field_name} escapes input_root: {resolved}"
        )
    _assert_no_symlink_components(resolved, field_name)
    snapshot = _read_json_snapshot(resolved)
    return _InputFile(
        field_name=field_name,
        configured_path=configured,
        path=resolved,
        sha256=snapshot.sha256,
        size_bytes=snapshot.size_bytes,
        payload=snapshot.payload,
    )


def _source_file_summaries(
    files: Sequence[_InputFile],
    input_root: Path,
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        sorted(
            (file.summary(input_root) for file in files),
            key=lambda item: (str(item["field_name"]), str(item["path_relative_to_input_root"])),
        )
    )


def _adapter_summary(source_type: str, result: object) -> Mapping[str, object]:
    to_dict = result.to_dict()
    return {
        "source_type": source_type,
        "counts": {
            "normalized_records": len(to_dict["normalized_records"]),
            "invalid_records": len(to_dict["invalid_records"]),
            "unresolved_records": len(to_dict["unresolved_records"]),
            "ambiguous_records": len(to_dict["ambiguous_records"]),
            "quarantined_records": len(to_dict["quarantined_records"]),
            "conflicting_records": len(to_dict["conflicting_records"]),
            "duplicate_diagnostics": len(to_dict["duplicate_diagnostics"]),
        },
        "source_summary": to_dict["source_summary"],
        "invalid_records": to_dict["invalid_records"],
        "unresolved_records": to_dict["unresolved_records"],
        "ambiguous_records": to_dict["ambiguous_records"],
        "quarantined_records": to_dict["quarantined_records"],
        "conflicting_records": to_dict["conflicting_records"],
        "duplicate_diagnostics": to_dict["duplicate_diagnostics"],
    }


def _adapter_blocks(adapter_details: Sequence[Mapping[str, object]]) -> bool:
    for detail in adapter_details:
        counts = _require_mapping(detail.get("counts"), "adapter.counts")
        if (
            int(counts.get("invalid_records", 0))
            or int(counts.get("quarantined_records", 0))
            or int(counts.get("conflicting_records", 0))
        ):
            return True
    return False


def _minutes_payload_for_assembly(payload: Mapping[str, object]) -> Mapping[str, object]:
    result = dict(payload)
    if "minutes_source_hashes" not in result and isinstance(result.get("source_hashes"), Mapping):
        result["minutes_source_hashes"] = dict(result["source_hashes"])
    return MappingProxyType(result)


def _payload_list(payloads: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    expanded: list[Mapping[str, object]] = []
    for payload in payloads:
        if isinstance(payload.get("projections"), list):
            expanded.extend(
                item for item in payload["projections"] if isinstance(item, Mapping)
            )
        elif isinstance(payload.get("probabilities"), list):
            expanded.extend(
                item for item in payload["probabilities"] if isinstance(item, Mapping)
            )
        else:
            expanded.append(payload)
    return tuple(MappingProxyType(_json_clone_mapping(item)) for item in expanded)


def _projection_for_market(
    projections: Sequence[Mapping[str, object]],
    market: Mapping[str, object],
    index: int,
) -> Mapping[str, object]:
    if not projections:
        raise NBAPlayerPointsBundleError("projection_payloads must contain projection evidence")
    for projection in projections:
        if projection.get("provider_event_id") in (None, market.get("provider_event_id")) and projection.get("player_id") in (
            None,
            market.get("player_id"),
        ):
            return projection
    return projections[min(index, len(projections) - 1)]


def _probability_for_market(
    probabilities: Sequence[Mapping[str, object]],
    market: Mapping[str, object],
    index: int,
) -> Mapping[str, object] | None:
    if not probabilities:
        return None
    for probability in probabilities:
        if probability.get("provider_event_id") in (None, market.get("provider_event_id")):
            return probability
    return probabilities[min(index, len(probabilities) - 1)]


def _find_minutes_for_market(
    minutes_rows: Sequence[Mapping[str, object]],
    crosswalk_row: Mapping[str, object],
    market: Mapping[str, object],
) -> Mapping[str, object] | None:
    canonical_event_id = crosswalk_row.get("canonical_event_id")
    player_id = crosswalk_row.get("canonical_player_id")
    for row in minutes_rows:
        if row.get("canonical_event_id") == canonical_event_id and row.get("player_id") == player_id:
            return row
    for row in minutes_rows:
        if row.get("provider_event_id") == market.get("provider_event_id"):
            return row
    return None


def _assembly_crosswalk_payload(
    row: Mapping[str, object],
    market: Mapping[str, object],
    schedule_rows: Sequence[Mapping[str, object]],
    player_rows: Sequence[Mapping[str, object]],
    mapping_records: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    event_identity = _require_mapping(row.get("event_identity"), "event_identity")
    player_identity = _require_mapping(row.get("player_identity"), "player_identity")
    schedule_hash = _source_hash_for_schedule(schedule_rows, event_identity, market)
    player_hash = _source_hash_for_player(player_rows, player_identity, market)
    mapping_hash = str(mapping_records[0]["source_hash"]) if mapping_records else "0" * 64
    return {
        "canonical_event_id": row.get("canonical_event_id"),
        "player_id": row.get("canonical_player_id"),
        "canonical_player_name": player_identity.get("canonical_player_name")
        or market.get("provider_player_name"),
        "team": player_identity.get("canonical_team") or player_identity.get("team") or market.get("team"),
        "opponent": market.get("opponent"),
        "commence_time_utc": event_identity.get("commence_time_utc"),
        "operating_date": event_identity.get("operating_date"),
        "event_identity_status": event_identity.get("event_identity_status"),
        "player_identity_status": player_identity.get("player_identity_status"),
        "event_identity_method": event_identity.get("event_identity_method"),
        "player_identity_method": player_identity.get("player_identity_method"),
        "mapping_version": _require_mapping(row.get("resolution_provenance"), "resolution_provenance").get(
            "event_mapping_version",
            "unmapped",
        ),
        "crosswalk_source_hashes": {
            "schedule_event": schedule_hash,
            "roster_player": player_hash,
            "reviewed_mapping_artifact": mapping_hash,
        },
    }


def _source_hash_for_schedule(
    rows: Sequence[Mapping[str, object]],
    event_identity: Mapping[str, object],
    market: Mapping[str, object],
) -> str:
    for row in rows:
        if row.get("canonical_event_id") == event_identity.get("canonical_event_id"):
            return str(row["source_hash"])
        if row.get("provider_event_id") == market.get("provider_event_id"):
            return str(row["source_hash"])
    return "0" * 64


def _source_hash_for_player(
    rows: Sequence[Mapping[str, object]],
    player_identity: Mapping[str, object],
    market: Mapping[str, object],
) -> str:
    for row in rows:
        if row.get("player_id") == player_identity.get("player_id"):
            return str(row["source_hash"])
        if row.get("provider_player_id") == market.get("provider_player_id"):
            return str(row["source_hash"])
    return "0" * 64


def _pregame_eligibility_summary(
    assembly_result: NBAPlayerPointsAssemblyBatchResult | None,
    crosswalk_rows: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    if assembly_result is None:
        return {
            "total_input_rows": len(crosswalk_rows),
            "eligible_projection_rows": 0,
            "eligible_probability_rows": 0,
            "excluded_rows": 0,
            "quarantined_rows": 0,
            "conflicting_rows": 0,
        }
    return {
        "total_input_rows": len(assembly_result.rows),
        "eligible_projection_rows": len(assembly_result.eligible_projection_rows),
        "eligible_probability_rows": len(assembly_result.eligible_probability_rows),
        "excluded_rows": len(assembly_result.excluded_rows),
        "quarantined_rows": len(assembly_result.quarantined_rows),
        "conflicting_rows": len(assembly_result.conflicting_rows),
        "crosswalk_excluded_rows": len(
            [row for row in crosswalk_rows if row.get("eligibility_status") == "excluded"]
        ),
    }


def _closing_eligibility_summary(
    observations: Sequence[Mapping[str, object]],
    config: NBAPlayerPointsClosingWriterConfig,
) -> Mapping[str, object]:
    eligible = [row for row in observations if _closing_observation_is_policy_eligible(row, config)]
    return {
        "total_observations": len(observations),
        "eligible_observations": len(eligible),
        "post_tip_observations": len(
            [row for row in observations if not _closing_observation_is_pre_tip(row)]
        ),
        "same_book_required": config.policy.same_book_required,
        "same_market_required": config.policy.same_market_required,
        "closing_policy_id": config.policy.closing_policy_id,
        "closing_policy_version": config.policy.closing_policy_version,
    }


def _closing_selection_preview(
    observations: Sequence[Mapping[str, object]],
    config: NBAPlayerPointsClosingWriterConfig,
) -> tuple[Mapping[str, object], ...]:
    by_prediction: dict[str, list[Mapping[str, object]]] = {}
    for row in observations:
        by_prediction.setdefault(str(row.get("prediction_id") or row.get("prediction_reference", {}).get("prediction_id")), []).append(row)
    previews: list[Mapping[str, object]] = []
    for prediction_id, rows in sorted(by_prediction.items()):
        eligible = [row for row in rows if _closing_observation_is_policy_eligible(row, config)]
        selected = sorted(eligible, key=lambda item: str(item.get("observation_timestamp_utc", "")))[-1] if eligible else None
        previews.append(
            {
                "prediction_id": prediction_id,
                "selection_status": "selected" if selected else "no_eligible_observation",
                "selected_closing_source_id": selected.get("closing_source_id") if selected else None,
                "eligible_observation_count": len(eligible),
                "observation_count": len(rows),
            }
        )
    return tuple(previews)


def _closing_observation_is_pre_tip(row: Mapping[str, object]) -> bool:
    return _parse_utc(row.get("observation_timestamp_utc"), "observation_timestamp_utc") < _parse_utc(
        row.get("commence_time_utc"),
        "commence_time_utc",
    )


def _closing_observation_is_policy_eligible(
    row: Mapping[str, object],
    config: NBAPlayerPointsClosingWriterConfig,
) -> bool:
    observation_time = _parse_utc(row.get("observation_timestamp_utc"), "observation_timestamp_utc")
    commence_time = _parse_utc(row.get("commence_time_utc"), "commence_time_utc")
    seconds_before = (commence_time - observation_time).total_seconds()
    return (
        seconds_before > config.policy.closing_window_end_seconds
        and seconds_before <= config.policy.closing_window_start_seconds
        and row.get("closing_line") is not None
        and row.get("closing_american_odds") is not None
    )


def _settlement_eligibility_summary(rows: Sequence[object]) -> Mapping[str, object]:
    payloads = [row.to_dict() for row in rows if hasattr(row, "to_dict")]
    statuses: dict[str, int] = {}
    for row in payloads:
        status = str(row.get("settlement_status"))
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "total_settlement_rows": len(payloads),
        "status_counts": statuses,
        "settled_rows": statuses.get("settled", 0),
        "pending_rows": statuses.get("pending", 0),
        "manual_review_rows": statuses.get("manual_review_required", 0),
        "conflicting_rows": statuses.get("conflicting", 0),
        "missing_actual_minutes_rows": len(
            [row for row in payloads if row.get("actual_minutes") is None]
        ),
        "zero_minute_rows": len(
            [row for row in payloads if row.get("actual_minutes") == 0.0]
        ),
        "dnp_rows": len(
            [row for row in payloads if row.get("participation_status") == "did_not_participate"]
        ),
    }


def _common_publish_blocks(
    context: _BundleContext,
    structural_errors: Sequence[str],
) -> list[str]:
    blocked: list[str] = []
    if context.repository_commit_sha != context.current_repository_commit_sha:
        blocked.append("repository SHA mismatch")
    repository_violations = context.repository_state.get("violations")
    if isinstance(repository_violations, Sequence) and not isinstance(
        repository_violations,
        (str, bytes, bytearray),
    ):
        blocked.extend(f"repository state violation: {item}" for item in repository_violations)
    if structural_errors:
        blocked.extend(f"structural error: {item}" for item in structural_errors)
    return blocked


def _has_prerequisite_evidence_block(reasons: object) -> bool:
    if not isinstance(reasons, Sequence) or isinstance(reasons, (str, bytes, bytearray)):
        return False
    markers = (
        "corrupt prerequisite",
        "prediction evidence invalid",
        "closing evidence invalid",
        "closing prerequisite",
        "settlement evidence invalid",
    )
    return any(
        any(marker in str(reason).casefold() for marker in markers)
        for reason in reasons
    )


def _planned_rows_for_digest(normalized_preview: Mapping[str, object]) -> Mapping[str, object]:
    payload: dict[str, object] = {}
    for key in (
        "assembly",
        "closing_observations",
        "effective_selection_preview",
        "settlement_rows",
        "final_stat_rows",
        "prediction_rows",
    ):
        if key in normalized_preview:
            payload[key] = normalized_preview[key]
    return payload


def _conflict_counts(conflict_report: Mapping[str, object]) -> Mapping[str, int]:
    counts: dict[str, int] = {}
    for key, value in conflict_report.items():
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            counts[key] = len(value)
        elif isinstance(value, Mapping):
            counts[key] = len(value)
    return counts


def _prediction_references_from_section(
    context: _BundleContext,
    value: object,
    *,
    prediction_verify_ok: bool,
) -> Mapping[str, Mapping[str, object]]:
    if not prediction_verify_ok:
        return MappingProxyType({})
    references = _require_mapping(value, "closing.prediction_references")
    ledger_rows = _ledger_rows(context)
    by_prediction_id = {str(row["prediction_id"]): row for row in ledger_rows}
    result: dict[str, Mapping[str, object]] = {}
    for key, spec in references.items():
        reference_key = _require_path_id(key, "closing.prediction_references key")
        if isinstance(spec, str):
            prediction_id = _require_path_id(spec, "closing.prediction_references prediction_id")
        else:
            prediction_id = _require_path_id(
                _require_mapping(spec, "closing.prediction_references value").get("prediction_id"),
                "closing.prediction_references prediction_id",
            )
        row = by_prediction_id.get(prediction_id)
        if row is None:
            continue
        result[reference_key] = _prediction_reference_payload(row)
    return MappingProxyType(result)


def _prediction_reference_payload(row: Mapping[str, object]) -> Mapping[str, object]:
    return {
        "prediction_reference": {
            "prediction_id": row["prediction_id"],
            "prediction_run_id": row["prediction_run_id"],
            "prediction_evidence_segment": row["prediction_evidence_segment"],
            "prediction_record_hash": row["ledger_record_hash"],
        },
        "canonical_event_id": row["canonical_event_id"],
        "player_id": row["player_id"],
        "operating_date": row["operating_date"],
        "commence_time_utc": row["commence_time_utc"],
    }


def _prediction_rows_from_evidence(
    context: _BundleContext,
    prediction_ids: Sequence[str],
) -> tuple[Mapping[str, object], ...]:
    wanted = set(prediction_ids)
    rows = []
    for row in _ledger_rows(context):
        if row.get("prediction_id") in wanted:
            payload = dict(row)
            payload["artifact_hash"] = row["assembled_record_hash"]
            rows.append(MappingProxyType(payload))
    return tuple(_canonical_list(rows))


def _ledger_rows(context: _BundleContext) -> tuple[Mapping[str, object], ...]:
    root = _actual_evidence_root(context.evidence_root)
    rows: list[Mapping[str, object]] = []
    ledgers_root = root / "ledgers" / "segments"
    if not ledgers_root.exists():
        return ()
    for path in sorted(ledgers_root.glob("*/*/prediction_ledger.jsonl")):
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            payload = _require_mapping(json.loads(line), "prediction ledger row")
            row = dict(payload)
            row["prediction_evidence_segment"] = relative
            row["prediction_evidence_line_number"] = line_number
            rows.append(MappingProxyType(row))
    return tuple(_canonical_list(rows))


def _crosswalk_row_from_prediction(row: Mapping[str, object]) -> Mapping[str, object]:
    return {
        "original_odds_row": {
            "provider_event_id": row.get("provider_event_id"),
        },
        "canonical_event_id": row.get("canonical_event_id"),
        "canonical_player_id": row.get("player_id"),
        "event_identity": {
            "event_identity_status": row.get("event_identity_status", "resolved"),
            "canonical_home_team": row.get("team"),
            "canonical_away_team": row.get("opponent"),
            "provider_event_id": row.get("provider_event_id"),
        },
        "player_identity": {
            "player_identity_status": row.get("player_identity_status", "resolved"),
        },
    }


def _actual_evidence_root(root: Path) -> Path:
    return root if root.name == NBA_PLAYER_POINTS_EVIDENCE_DIR_NAME else root / NBA_PLAYER_POINTS_EVIDENCE_DIR_NAME


def _write_json_artifact(root: Path, file_name: str, payload: Mapping[str, object]) -> None:
    if "/" in file_name or "\\" in file_name or file_name in {"", ".", ".."}:
        raise NBAPlayerPointsPathSecurityError("preview artifact file name is unsafe")
    safe_root = _resolve_root(str(root), "preview_root", must_exist=False)
    safe_root.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_components(safe_root, "preview_root")
    target = safe_root / file_name
    if not _is_relative_to(target.resolve(strict=False), safe_root.resolve(strict=True)):
        raise NBAPlayerPointsPathSecurityError("preview artifact escapes preview_root")
    target.write_bytes(_json_bytes(payload))


def _manifest_hashes(paths: Sequence[Path]) -> Mapping[str, object]:
    hashes: dict[str, object] = {}
    for path in paths:
        if path.exists():
            hashes[path.name] = _sha256_bytes(path.read_bytes())
    return hashes


def _resolve_root(value: object, field_name: str, *, must_exist: bool) -> Path:
    text = _require_text(value, field_name)
    path = Path(text).expanduser()
    if _path_has_parent_traversal(path):
        raise NBAPlayerPointsPathSecurityError(f"{field_name} must not contain '..'")
    _reject_credential_path(path, field_name)
    _reject_known_production_path(path, field_name)
    unresolved_absolute = path if path.is_absolute() else Path.cwd() / path
    _assert_no_symlink_components(unresolved_absolute, field_name)
    if must_exist:
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise NBAPlayerPointsBundleError(f"{field_name} must be a directory")
        _assert_no_symlink_components(resolved, field_name)
        return resolved
    parent = path.parent if path.parent != Path("") else Path(".")
    existing_parent = parent.resolve(strict=True)
    _assert_no_symlink_components(existing_parent, f"{field_name} parent")
    resolved = path.resolve(strict=False)
    _reject_known_production_path(resolved, field_name)
    return resolved


def _validate_disjoint_roots(
    *,
    input_root: Path,
    preview_root: Path,
    evidence_root: Path,
) -> None:
    roots = {
        "input_root": input_root,
        "preview_root": preview_root,
        "evidence_root": evidence_root,
    }
    names = tuple(roots)
    for left_index, left_name in enumerate(names):
        for right_name in names[left_index + 1:]:
            left = roots[left_name]
            right = roots[right_name]
            if _roots_overlap(left, right):
                raise NBAPlayerPointsPathSecurityError(
                    f"{left_name} and {right_name} must be pairwise disjoint"
                )


def _roots_overlap(left: Path, right: Path) -> bool:
    left_parts = _normalized_root_parts(left)
    right_parts = _normalized_root_parts(right)
    return (
        left_parts == right_parts
        or left_parts[: len(right_parts)] == right_parts
        or right_parts[: len(left_parts)] == left_parts
    )


def _normalized_root_parts(path: Path) -> tuple[str, ...]:
    normalized = os.path.normcase(str(path.resolve(strict=False))).casefold()
    return Path(normalized).parts


def _reject_known_production_path(path: Path, field_name: str) -> None:
    parts = {part.casefold() for part in path.parts}
    if parts.intersection(_PRODUCTION_PATH_PARTS):
        raise NBAPlayerPointsPathSecurityError(
            f"{field_name} points at a prohibited production/runtime path"
        )


def _reject_credential_path(path: Path, field_name: str) -> None:
    text = path.as_posix().casefold()
    if any(marker in text for marker in _CREDENTIAL_PATH_MARKERS):
        raise NBAPlayerPointsPathSecurityError(f"{field_name} appears to reference credentials")


def _assert_no_symlink_components(path: Path, field_name: str) -> None:
    probes: list[Path] = []
    current = path
    while True:
        probes.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for probe in reversed(probes):
        if probe.exists() and probe.is_symlink():
            raise NBAPlayerPointsPathSecurityError(
                f"{field_name} contains a symlink component: {probe}"
            )


def _path_has_parent_traversal(path: Path) -> bool:
    return any(part == ".." for part in path.parts)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _inspect_repository_state(
    repository_root: Path,
    *,
    expected_branch: str | None,
) -> _RepositoryState:
    failures: list[str] = []

    def git_lines(args: Sequence[str]) -> tuple[str, ...]:
        try:
            return tuple(
                line.strip()
                for line in _run_git_readonly(args, repository_root).splitlines()
                if line.strip()
            )
        except NBAPlayerPointsBundleError as exc:
            failures.append("git " + " ".join(args) + ": " + str(exc))
            return ()

    head_lines = git_lines(("rev-parse", "HEAD"))
    current_head = (
        head_lines[0].casefold()
        if head_lines and _COMMIT_SHA_RE.fullmatch(head_lines[0].casefold())
        else "0" * 40
    )
    branch_lines = git_lines(("rev-parse", "--abbrev-ref", "HEAD"))
    current_branch = branch_lines[0] if branch_lines else ""
    staged_changes = tuple(sorted(git_lines(("diff", "--cached", "--name-only"))))
    tracked_changes = tuple(sorted(git_lines(("diff", "--name-only"))))
    unmerged_raw = git_lines(("ls-files", "-u"))
    unmerged_paths = tuple(
        sorted(
            {
                line.split("\t", 1)[1] if "\t" in line else line
                for line in unmerged_raw
            }
        )
    )
    untracked_paths = tuple(sorted(git_lines(("ls-files", "--others", "--exclude-standard"))))
    protected_untracked = tuple(
        path for path in untracked_paths if _is_protected_audit_untracked_path(path)
    )
    unexpected_untracked = tuple(
        path for path in untracked_paths if path not in protected_untracked
    )

    violations: list[str] = []
    if failures:
        violations.extend(failures)
    if current_head == "0" * 40:
        violations.append("unable to determine current repository HEAD")
    if expected_branch is not None and current_branch != expected_branch:
        violations.append(
            f"repository branch mismatch: expected {expected_branch}, found {current_branch or '<unknown>'}"
        )
    if staged_changes:
        violations.append("staged changes exist: " + ", ".join(staged_changes))
    if tracked_changes:
        violations.append("tracked working-tree changes exist: " + ", ".join(tracked_changes))
    if unmerged_paths:
        violations.append("unmerged paths exist: " + ", ".join(unmerged_paths))
    if unexpected_untracked:
        violations.append(
            "unexpected untracked repository paths exist: "
            + ", ".join(unexpected_untracked)
        )

    return _RepositoryState(
        repository_root=repository_root,
        expected_branch=expected_branch,
        current_branch=current_branch,
        current_head=current_head,
        staged_changes=staged_changes,
        tracked_worktree_changes=tracked_changes,
        unmerged_paths=unmerged_paths,
        untracked_paths=untracked_paths,
        protected_untracked_paths=protected_untracked,
        unexpected_untracked_paths=unexpected_untracked,
        git_command_failures=tuple(failures),
        violations=tuple(dict.fromkeys(violations)),
    )


def _run_git_readonly(args: Sequence[str], repository_root: Path) -> str:
    command = tuple(args)
    if not command:
        raise NBAPlayerPointsBundleError("git command is empty")
    allowed = {
        ("rev-parse", "HEAD"),
        ("rev-parse", "--abbrev-ref", "HEAD"),
        ("diff", "--cached", "--name-only"),
        ("diff", "--name-only"),
        ("ls-files", "-u"),
        ("ls-files", "--others", "--exclude-standard"),
    }
    if command not in allowed:
        raise NBAPlayerPointsBundleError(
            "disallowed git inspection command: " + " ".join(command)
        )
    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={repository_root.as_posix()}",
                *command,
            ],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise NBAPlayerPointsBundleError("unable to inspect repository state") from exc
    return completed.stdout


def _is_protected_audit_untracked_path(path: str) -> bool:
    normalized = path.replace("\\", "/").strip("/")
    protected = _PROTECTED_AUDIT_DIR
    return normalized == protected or normalized.startswith(protected + "/")


def _optional_repository_branch(value: object) -> str | None:
    if value in (None, ""):
        return None
    return _require_text(value, "repository_branch")


def _read_json_snapshot(path: Path) -> _JsonSnapshot:
    try:
        data = path.read_bytes()
        payload = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise NBAPlayerPointsBundleError(f"JSON file must be UTF-8: {path}") from exc
    except json.JSONDecodeError as exc:
        raise NBAPlayerPointsBundleError(f"invalid JSON file: {path}") from exc
    return _JsonSnapshot(
        path=path,
        sha256=_sha256_bytes(data),
        size_bytes=len(data),
        payload=_require_mapping(payload, str(path)),
    )


def _require_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise NBAPlayerPointsBundleError(f"{field_name} must be an object")
    return MappingProxyType(_json_clone_mapping(value))


def _require_sequence(value: object, field_name: str) -> tuple[object, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        values = tuple(value)
        if values:
            return values
    raise NBAPlayerPointsBundleError(f"{field_name} must be a non-empty list")


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NBAPlayerPointsBundleError(f"{field_name} is required")
    return value.strip()


def _first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise NBAPlayerPointsBundleError("expected at least one text value")


def _require_path_id(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if _SAFE_PATH_ID_RE.fullmatch(text) is None:
        raise NBAPlayerPointsPathSecurityError(
            f"{field_name} must be a safe path component"
        )
    return text


def _require_commit_sha(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold()
    if _COMMIT_SHA_RE.fullmatch(text) is None:
        raise NBAPlayerPointsBundleError(f"{field_name} must be a git commit SHA")
    return text


def _require_operating_date(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if _DATE_RE.fullmatch(text) is None:
        raise NBAPlayerPointsBundleError(f"{field_name} must be YYYY-MM-DD")
    return text


def _require_utc_timestamp(value: object, field_name: str) -> str:
    parsed = _parse_utc(value, field_name)
    return _format_utc(parsed)


def _parse_utc(value: object, field_name: str) -> datetime:
    text = _require_text(value, field_name)
    raw = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise NBAPlayerPointsBundleError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NBAPlayerPointsBundleError(f"{field_name} must be timezone-aware UTC")
    utc_value = parsed.astimezone(_UTC)
    if utc_value.utcoffset().total_seconds() != 0:
        raise NBAPlayerPointsBundleError(f"{field_name} must be UTC")
    return utc_value


def _format_utc(value: datetime) -> str:
    return value.astimezone(_UTC).isoformat().replace("+00:00", "Z")


def _canonical_list(values: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    return tuple(
        sorted(
            (MappingProxyType(_json_clone_mapping(item)) for item in values),
            key=lambda item: _canonical_json_text(item),
        )
    )


def _canonical_sha256(payload: object) -> str:
    return _sha256_bytes(_json_bytes(payload))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(payload: object) -> bytes:
    return (_canonical_json_text(payload) + "\n").encode("utf-8")


def _canonical_json_text(payload: object) -> str:
    return json.dumps(
        _json_ready(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _json_ready(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return _format_utc(value)
    if hasattr(value, "to_dict"):
        return _json_ready(value.to_dict())
    return value


def _json_clone_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return json.loads(json.dumps(_json_ready(value), sort_keys=True, allow_nan=False))
