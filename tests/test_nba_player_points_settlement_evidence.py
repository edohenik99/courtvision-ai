from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import shutil
import threading
from unittest.mock import patch

import pytest

from courtvision.sports.nba.player_points_assembly import (
    assemble_nba_player_points_batch,
)
from courtvision.sports.nba.player_points_closing import (
    NBAPlayerPointsClosingWriterConfig,
    write_nba_player_points_closing_evidence,
)
from courtvision.sports.nba.player_points_evidence import (
    NBAPlayerPointsEvidenceWriterConfig,
    write_nba_player_points_evidence,
)
from courtvision.sports.nba.player_points_settlement import (
    NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION,
    NBAPlayerPointsSettlementRow,
)
from courtvision.sports.nba.player_points_settlement_evidence import (
    NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_SCHEMA_VERSION,
    NBAPlayerPointsSettlementEvidenceError,
    NBAPlayerPointsSettlementEvidenceWriterConfig,
    NBAPlayerPointsSettlementPolicy,
    resolve_nba_player_points_effective_settlement,
    settlement_evidence_schema_definition,
    verify_nba_player_points_settlement_evidence,
    write_nba_player_points_settlement_evidence,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "nba" / "player_points"
ASSEMBLY_CASES_FIXTURE = FIXTURE_ROOT / "assembly_cases.json"
SETTLEMENT_MODULE = (
    Path(__file__).resolve().parents[1]
    / "courtvision"
    / "sports"
    / "nba"
    / "player_points_settlement_evidence.py"
)
PREDICTION_CONFIG = NBAPlayerPointsEvidenceWriterConfig()
SETTLEMENT_CONFIG = NBAPlayerPointsSettlementEvidenceWriterConfig()
WRITER_TIMESTAMP = "2026-06-06T04:15:00Z"
COLLECTION_TIMESTAMP = "2026-06-06T04:15:00Z"


def _load_fixture() -> dict[str, object]:
    return json.loads(ASSEMBLY_CASES_FIXTURE.read_text(encoding="utf-8"))


def _deep_merge(base: object, overrides: object) -> object:
    if isinstance(base, dict) and isinstance(overrides, dict):
        merged = deepcopy(base)
        for key, value in overrides.items():
            merged[key] = _deep_merge(merged.get(key), value)
        return merged
    return deepcopy(overrides)


def _case_payload(
    case_id: str = "valid_projection_no_probabilities",
    extra_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    fixture = _load_fixture()
    base = fixture["base_case"]
    cases = {case["case_id"]: case for case in fixture["cases"]}
    payload = _deep_merge(base, cases[case_id].get("overrides", {}))
    if extra_overrides:
        payload = _deep_merge(payload, extra_overrides)
    return payload


def _batch(*payloads: dict[str, object]):
    return assemble_nba_player_points_batch(
        payloads or (_case_payload(),),
        manifest_created_at_utc="2026-06-05T18:06:00Z",
    )


def _write_prediction(tmp_path: Path, result=None):
    result = result or _batch(_case_payload())
    return write_nba_player_points_evidence(
        result,
        result.source_manifest_preview,
        tmp_path,
        PREDICTION_CONFIG,
        repository_commit_sha=result.source_manifest_preview.repository_commit_sha,
        writer_timestamp_utc="2026-06-05T18:10:00Z",
    )


def _evidence_root(tmp_path: Path) -> Path:
    return tmp_path / "nba_player_points_evidence"


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


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


def _prediction_ledger_path(tmp_path: Path) -> Path:
    paths = sorted(_evidence_root(tmp_path).glob("ledgers/segments/*/*/prediction_ledger.jsonl"))
    assert len(paths) == 1
    return paths[0]


def _prediction_row(tmp_path: Path) -> dict[str, object]:
    return _read_jsonl(_prediction_ledger_path(tmp_path))[0]


def _settlement_rows(tmp_path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(_evidence_root(tmp_path).glob("settlement/segments/*/*/settlement_rows.jsonl")):
        rows.extend(_read_jsonl(path))
    return rows


def _settlement_conflicts(tmp_path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(_evidence_root(tmp_path).glob("settlement/segments/*/*/settlement_conflicts.jsonl")):
        rows.extend(_read_jsonl(path))
    return rows


def _settlement_segment_dirs(tmp_path: Path) -> list[Path]:
    return sorted(_evidence_root(tmp_path).glob("settlement/segments/*/*"))


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _source_hash(suffix: str) -> str:
    return hashlib.sha256(suffix.encode("utf-8")).hexdigest()


def _settlement_row(
    tmp_path: Path,
    *,
    suffix: str = "settled",
    settlement_status: str = "settled",
    final_points: float | None = 35.0,
    actual_minutes: float | None = 38.75,
    participation_status: str = "participated",
    player_identity_status: str = "resolved",
    event_identity_status: str = "resolved",
    game_status: str = "final",
    game_final: bool = True,
    exclusion_reason: str = "none",
    manual_review_status: str = "not_required",
    settlement_source_timestamp_utc: str = "2026-06-06T03:55:00Z",
    settlement_timestamp_utc: str = "2026-06-06T04:00:00Z",
    prediction_artifact_hash: str | None = None,
) -> NBAPlayerPointsSettlementRow:
    prediction = _prediction_row(tmp_path)
    return NBAPlayerPointsSettlementRow(
        settlement_id=f"settlement-{suffix}",
        prediction_id=str(prediction["prediction_id"]),
        prediction_run_id=str(prediction["prediction_run_id"]),
        model_id=str(prediction["model_id"]),
        canonical_event_id=str(prediction["canonical_event_id"]),
        provider_event_id=str(prediction["provider_event_id"]),
        provider_name="the_odds_api_nba",
        operating_date=date.fromisoformat(str(prediction["operating_date"])),
        commence_time_utc=_dt(str(prediction["commence_time_utc"])),
        home_team=str(prediction["team"]),
        away_team=str(prediction["opponent"]),
        player_id=str(prediction["player_id"]),
        canonical_player_name=str(prediction["canonical_player_name"]),
        team=str(prediction["team"]),
        opponent=str(prediction["opponent"]),
        player_identity_status=player_identity_status,
        event_identity_status=event_identity_status,
        game_status=game_status,
        game_final=game_final,
        final_points=final_points,
        actual_minutes=actual_minutes,
        participation_status=participation_status,
        settlement_status=settlement_status,
        exclusion_reason=exclusion_reason,
        manual_review_status=manual_review_status,
        settlement_timestamp_utc=_dt(settlement_timestamp_utc),
        settlement_provider="offline_final_stats_fixture",
        settlement_source_id=f"settlement-source-{suffix}",
        settlement_source_timestamp_utc=_dt(settlement_source_timestamp_utc),
        settlement_source_hash=_source_hash(suffix),
        settlement_schema_version=NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION,
        prediction_artifact_hash=prediction_artifact_hash
        or str(prediction["assembled_record_hash"]),
        repository_commit_sha=str(prediction["repository_commit_sha"]),
    )


def _pending_row(tmp_path: Path, *, suffix: str = "pending") -> NBAPlayerPointsSettlementRow:
    return _settlement_row(
        tmp_path,
        suffix=suffix,
        settlement_status="pending",
        final_points=None,
        actual_minutes=None,
        participation_status="unknown",
        game_status="in_progress",
        game_final=False,
        exclusion_reason="game_not_final",
        manual_review_status="required",
        settlement_source_timestamp_utc="2026-06-06T00:50:00Z",
        settlement_timestamp_utc="2026-06-06T00:51:00Z",
    )


def _manual_review_row(
    tmp_path: Path,
    *,
    suffix: str = "manual",
    final_points: float | None = 35.0,
    actual_minutes: float | None = None,
    participation_status: str = "participated",
    exclusion_reason: str = "missing_actual_minutes",
    settlement_source_timestamp_utc: str = "2026-06-06T03:10:00Z",
    settlement_timestamp_utc: str = "2026-06-06T03:11:00Z",
) -> NBAPlayerPointsSettlementRow:
    return _settlement_row(
        tmp_path,
        suffix=suffix,
        settlement_status="manual_review_required",
        final_points=final_points,
        actual_minutes=actual_minutes,
        participation_status=participation_status,
        exclusion_reason=exclusion_reason,
        manual_review_status="required",
        settlement_source_timestamp_utc=settlement_source_timestamp_utc,
        settlement_timestamp_utc=settlement_timestamp_utc,
    )


def _unresolved_row(
    tmp_path: Path,
    *,
    suffix: str = "unresolved",
) -> NBAPlayerPointsSettlementRow:
    return _settlement_row(
        tmp_path,
        suffix=suffix,
        settlement_status="unresolved",
        final_points=None,
        actual_minutes=None,
        participation_status="unknown",
        player_identity_status="unresolved",
        event_identity_status="unresolved",
        exclusion_reason="identity_unresolved",
        settlement_source_timestamp_utc="2026-06-06T02:00:00Z",
        settlement_timestamp_utc="2026-06-06T02:01:00Z",
    )


def _void_dnp_row(tmp_path: Path, *, suffix: str = "dnp") -> NBAPlayerPointsSettlementRow:
    return _settlement_row(
        tmp_path,
        suffix=suffix,
        settlement_status="void",
        final_points=None,
        actual_minutes=None,
        participation_status="did_not_participate",
        exclusion_reason="did_not_participate",
        settlement_source_timestamp_utc="2026-06-06T04:20:00Z",
        settlement_timestamp_utc="2026-06-06T04:21:00Z",
    )


def _zero_minutes_row(tmp_path: Path, *, suffix: str = "zero") -> NBAPlayerPointsSettlementRow:
    return _settlement_row(
        tmp_path,
        suffix=suffix,
        final_points=0.0,
        actual_minutes=0.0,
        participation_status="zero_minutes",
        settlement_source_timestamp_utc="2026-06-06T04:10:00Z",
        settlement_timestamp_utc="2026-06-06T04:11:00Z",
    )


def _write_settlement(
    tmp_path: Path,
    *rows: NBAPlayerPointsSettlementRow,
    config: NBAPlayerPointsSettlementEvidenceWriterConfig = SETTLEMENT_CONFIG,
    collection_timestamp_utc: str = COLLECTION_TIMESTAMP,
    writer_timestamp_utc: str = WRITER_TIMESTAMP,
    failure_hook=None,
):
    return write_nba_player_points_settlement_evidence(
        tmp_path,
        rows,
        config,
        collection_timestamp_utc=collection_timestamp_utc,
        repository_commit_sha="f6b52cb9caf195346d4100b37add5396e45688b2",
        writer_timestamp_utc=writer_timestamp_utc,
        failure_hook=failure_hook,
    )


def _effective(tmp_path: Path, config: NBAPlayerPointsSettlementEvidenceWriterConfig = SETTLEMENT_CONFIG) -> dict[str, object]:
    return dict(
        resolve_nba_player_points_effective_settlement(
            tmp_path,
            str(_prediction_row(tmp_path)["prediction_id"]),
            config,
        )
    )


def _fail_at(stage_name: str):
    def hook(stage: str) -> None:
        if stage == stage_name:
            raise RuntimeError(stage_name)

    return hook


def test_schema_definition_documents_append_only_settlement_contract() -> None:
    schema = settlement_evidence_schema_definition()

    assert schema["schema_version"] == NBA_PLAYER_POINTS_SETTLEMENT_EVIDENCE_SCHEMA_VERSION
    assert schema["settlement_contract_schema_version"] == NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION
    assert "already_complete" in schema["completion_statuses"]
    assert "settlement_evidence_record_hash" in schema["required_record_fields"]
    assert "null actual_minutes and zero actual_minutes are distinct" in schema["compatible_enrichment_rule"]


def test_valid_terminal_settlement_creates_immutable_segment_and_effective_result(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    row = _settlement_row(tmp_path)

    result = _write_settlement(tmp_path, row)

    assert result.completion_status == "complete"
    assert result.settlement_rows_written == 1
    assert result.conflicts_written == 0
    assert (result.settlement_segment_directory / "settlement_manifest.json").exists()
    assert (result.settlement_segment_directory / "settlement_rows.jsonl").exists()
    assert (result.settlement_segment_directory / "settlement_conflicts.jsonl").exists()
    assert (result.settlement_segment_directory / "COMPLETE").exists()
    stored = _settlement_rows(tmp_path)[0]
    prediction = _prediction_row(tmp_path)
    assert stored["settlement_record_hash"] == row.settlement_record_hash
    assert stored["prediction_record_hash"] == prediction["ledger_record_hash"]
    assert stored["prediction_assembled_record_hash"] == prediction["assembled_record_hash"]
    effective = _effective(tmp_path)
    assert effective["effective_status"] == "settled"
    assert effective["selected_settlement_record"]["final_points"] == 35.0
    assert effective["historical_settlement_count"] == 1
    report = verify_nba_player_points_settlement_evidence(tmp_path, SETTLEMENT_CONFIG)
    assert report.ok is True
    assert report.binding_status_counts["legacy-unbound"] == 1
    assert report.binding_status_counts["closing-bound"] == 0
    assert report.binding_status_counts["invalid"] == 0
    assert report.warnings[0]["code"] == "legacy_unbound_settlement_evidence"
    assert report.settlement_segments[0]["binding_status"] == "legacy-unbound"


def test_pending_to_terminal_revision_preserves_first_segment(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    pending = _pending_row(tmp_path)
    terminal = _settlement_row(
        tmp_path,
        suffix="terminal",
        settlement_source_timestamp_utc="2026-06-06T04:30:00Z",
        settlement_timestamp_utc="2026-06-06T04:31:00Z",
    )

    first = _write_settlement(tmp_path, pending)
    first_snapshot = _snapshot(first.settlement_segment_directory)
    assert _effective(tmp_path)["effective_status"] == "pending"

    second = _write_settlement(tmp_path, terminal)

    assert _snapshot(first.settlement_segment_directory) == first_snapshot
    assert second.settlement_segment_directory != first.settlement_segment_directory
    effective = _effective(tmp_path)
    assert effective["effective_status"] == "settled"
    assert effective["historical_settlement_count"] == 2
    assert effective["terminal_revision_count"] == 1
    assert verify_nba_player_points_settlement_evidence(tmp_path, SETTLEMENT_CONFIG).ok is True


def test_compatible_terminal_enrichment_uses_latest_without_rewriting(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    first = _settlement_row(tmp_path, suffix="terminal-a")
    enriched = _settlement_row(
        tmp_path,
        suffix="terminal-b",
        settlement_source_timestamp_utc="2026-06-06T04:30:00Z",
        settlement_timestamp_utc="2026-06-06T04:31:00Z",
    )

    first_result = _write_settlement(tmp_path, first)
    first_snapshot = _snapshot(first_result.settlement_segment_directory)
    second_result = _write_settlement(tmp_path, enriched)

    assert _snapshot(first_result.settlement_segment_directory) == first_snapshot
    assert second_result.completion_status == "complete"
    effective = _effective(tmp_path)
    assert effective["selected_settlement_id"] == "settlement-terminal-b"
    assert effective["compatible_terminal_revision_count"] == 2
    assert effective["conflict_reason"] == "none"


def test_manual_review_missing_minutes_enriches_to_settled_without_rewriting(
    tmp_path: Path,
) -> None:
    _write_prediction(tmp_path)
    manual = _manual_review_row(tmp_path, suffix="manual-missing-minutes")
    settled = _settlement_row(
        tmp_path,
        suffix="manual-minutes-arrived",
        actual_minutes=38.75,
        settlement_source_timestamp_utc="2026-06-06T04:30:00Z",
        settlement_timestamp_utc="2026-06-06T04:31:00Z",
    )

    first = _write_settlement(tmp_path, manual)
    first_snapshot = _snapshot(first.settlement_segment_directory)
    rows_path = first.settlement_segment_directory / "settlement_rows.jsonl"
    manual_bytes = rows_path.read_bytes()
    manual_record = _read_jsonl(rows_path)[0]

    second = _write_settlement(tmp_path, settled)

    assert second.completion_status == "complete"
    assert _snapshot(first.settlement_segment_directory) == first_snapshot
    assert rows_path.read_bytes() == manual_bytes
    assert _read_jsonl(rows_path)[0] == manual_record
    effective = _effective(tmp_path)
    assert effective["effective_status"] == "settled"
    assert effective["selected_settlement_id"] == "settlement-manual-minutes-arrived"
    assert effective["selected_settlement_record"]["actual_minutes"] == 38.75
    assert effective["historical_settlement_count"] == 2
    assert [item["settlement_id"] for item in effective["evidence_lineage"]] == [
        "settlement-manual-missing-minutes",
        "settlement-manual-minutes-arrived",
    ]


def test_missing_minutes_to_zero_minutes_is_enrichment_but_not_dnp(
    tmp_path: Path,
) -> None:
    _write_prediction(tmp_path)
    manual = _manual_review_row(
        tmp_path,
        suffix="manual-null-to-zero",
        final_points=None,
        actual_minutes=None,
        participation_status="unknown",
        exclusion_reason="unknown_participation",
    )

    _write_settlement(tmp_path, manual)
    zero_result = _write_settlement(tmp_path, _zero_minutes_row(tmp_path))

    assert zero_result.completion_status == "complete"
    effective = _effective(tmp_path)
    assert effective["effective_status"] == "settled"
    assert effective["selected_settlement_record"]["actual_minutes"] == 0.0
    assert effective["selected_settlement_record"]["participation_status"] == "zero_minutes"

    dnp_result = _write_settlement(tmp_path, _void_dnp_row(tmp_path))

    assert dnp_result.completion_status == "conflicting"
    assert _effective(tmp_path)["conflict_reason"] == "conflicting_terminal_outcome"


def test_unknown_participation_may_become_participated(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    manual = _manual_review_row(
        tmp_path,
        suffix="manual-unknown-participation",
        actual_minutes=38.75,
        participation_status="unknown",
        exclusion_reason="unknown_participation",
    )
    settled = _settlement_row(
        tmp_path,
        suffix="unknown-became-participated",
        settlement_source_timestamp_utc="2026-06-06T04:35:00Z",
        settlement_timestamp_utc="2026-06-06T04:36:00Z",
    )

    _write_settlement(tmp_path, manual)
    result = _write_settlement(tmp_path, settled)

    assert result.completion_status == "complete"
    effective = _effective(tmp_path)
    assert effective["effective_status"] == "settled"
    assert effective["selected_settlement_record"]["participation_status"] == "participated"


def test_unknown_participation_may_become_dnp_void(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    manual = _manual_review_row(
        tmp_path,
        suffix="manual-unknown-to-dnp",
        final_points=None,
        actual_minutes=None,
        participation_status="unknown",
        exclusion_reason="unknown_participation",
    )

    _write_settlement(tmp_path, manual)
    result = _write_settlement(tmp_path, _void_dnp_row(tmp_path))

    assert result.completion_status == "complete"
    effective = _effective(tmp_path)
    assert effective["effective_status"] == "void"
    assert effective["selected_settlement_record"]["participation_status"] == "did_not_participate"


def test_unresolved_evidence_may_resolve_to_settled(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    unresolved = _unresolved_row(tmp_path)
    settled = _settlement_row(
        tmp_path,
        suffix="unresolved-became-settled",
        settlement_source_timestamp_utc="2026-06-06T04:35:00Z",
        settlement_timestamp_utc="2026-06-06T04:36:00Z",
    )

    _write_settlement(tmp_path, unresolved)
    result = _write_settlement(tmp_path, settled)

    assert result.completion_status == "complete"
    effective = _effective(tmp_path)
    assert effective["effective_status"] == "settled"
    assert effective["selected_settlement_id"] == "settlement-unresolved-became-settled"


def test_missing_final_points_may_become_finite(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    manual = _manual_review_row(
        tmp_path,
        suffix="manual-missing-final-points",
        final_points=None,
        actual_minutes=38.75,
        participation_status="participated",
        exclusion_reason="missing_final_points",
    )
    settled = _settlement_row(
        tmp_path,
        suffix="final-points-arrived",
        final_points=35.0,
        actual_minutes=38.75,
        settlement_source_timestamp_utc="2026-06-06T04:35:00Z",
        settlement_timestamp_utc="2026-06-06T04:36:00Z",
    )

    _write_settlement(tmp_path, manual)
    result = _write_settlement(tmp_path, settled)

    assert result.completion_status == "complete"
    effective = _effective(tmp_path)
    assert effective["effective_status"] == "settled"
    assert effective["selected_settlement_record"]["final_points"] == 35.0


def test_later_null_regression_from_authoritative_settlement_fails_closed(
    tmp_path: Path,
) -> None:
    _write_prediction(tmp_path)
    settled = _settlement_row(tmp_path, suffix="known-authoritative")
    regression = _manual_review_row(
        tmp_path,
        suffix="later-null-regression",
        final_points=None,
        actual_minutes=None,
        participation_status="unknown",
        exclusion_reason="missing_final_points",
        settlement_source_timestamp_utc="2026-06-06T04:50:00Z",
        settlement_timestamp_utc="2026-06-06T04:51:00Z",
    )

    _write_settlement(tmp_path, settled)
    result = _write_settlement(tmp_path, regression)

    assert result.completion_status == "conflicting"
    effective = _effective(tmp_path)
    assert effective["effective_status"] == "conflicting"
    assert effective["selected_settlement_record"] is None
    assert effective["conflict_reason"] == "known_authoritative_information_regressed"


def test_participated_versus_dnp_fails_closed(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    _write_settlement(tmp_path, _settlement_row(tmp_path, suffix="participated-final"))

    result = _write_settlement(tmp_path, _void_dnp_row(tmp_path))

    assert result.completion_status == "conflicting"
    assert _effective(tmp_path)["conflict_reason"] == "conflicting_terminal_outcome"


def test_zero_minutes_versus_dnp_fails_closed(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    _write_settlement(tmp_path, _zero_minutes_row(tmp_path))

    result = _write_settlement(tmp_path, _void_dnp_row(tmp_path))

    assert result.completion_status == "conflicting"
    assert _effective(tmp_path)["conflict_reason"] == "conflicting_terminal_outcome"


def test_pending_may_become_void(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    _write_settlement(tmp_path, _pending_row(tmp_path))

    result = _write_settlement(tmp_path, _void_dnp_row(tmp_path))

    assert result.completion_status == "complete"
    effective = _effective(tmp_path)
    assert effective["effective_status"] == "void"
    assert effective["selected_settlement_id"] == "settlement-dnp"


def test_final_settled_versus_final_void_fails_closed(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    _write_settlement(tmp_path, _settlement_row(tmp_path, suffix="settled-final"))
    void = _settlement_row(
        tmp_path,
        suffix="cancelled-void",
        settlement_status="void",
        final_points=None,
        actual_minutes=None,
        participation_status="unknown",
        game_status="cancelled",
        game_final=False,
        exclusion_reason="game_cancelled",
        settlement_source_timestamp_utc="2026-06-06T04:40:00Z",
        settlement_timestamp_utc="2026-06-06T04:41:00Z",
    )

    result = _write_settlement(tmp_path, void)

    assert result.completion_status == "conflicting"
    assert _effective(tmp_path)["conflict_reason"] == "conflicting_terminal_outcome"


def test_same_source_timestamp_with_different_settlement_values_conflicts(
    tmp_path: Path,
) -> None:
    _write_prediction(tmp_path)
    first = _settlement_row(
        tmp_path,
        suffix="same-source-a",
        settlement_source_timestamp_utc="2026-06-06T04:30:00Z",
        settlement_timestamp_utc="2026-06-06T04:31:00Z",
    )
    second = _settlement_row(
        tmp_path,
        suffix="same-source-a",
        final_points=36.0,
        settlement_source_timestamp_utc="2026-06-06T04:30:00Z",
        settlement_timestamp_utc="2026-06-06T04:32:00Z",
    )

    _write_settlement(tmp_path, first)
    result = _write_settlement(tmp_path, second)

    assert result.completion_status == "conflicting"
    assert _settlement_conflicts(tmp_path)[0]["conflict_reason"] == (
        "same_source_timestamp_conflicting_settlement"
    )


def test_repeated_compatible_enrichment_resolves_deterministically(
    tmp_path: Path,
) -> None:
    _write_prediction(tmp_path)
    _write_settlement(tmp_path, _pending_row(tmp_path))
    _write_settlement(
        tmp_path,
        _manual_review_row(
            tmp_path,
            suffix="deterministic-manual",
            final_points=None,
            actual_minutes=38.75,
            participation_status="participated",
            exclusion_reason="missing_final_points",
            settlement_source_timestamp_utc="2026-06-06T03:30:00Z",
            settlement_timestamp_utc="2026-06-06T03:31:00Z",
        ),
    )
    _write_settlement(
        tmp_path,
        _settlement_row(
            tmp_path,
            suffix="deterministic-settled-a",
            settlement_source_timestamp_utc="2026-06-06T04:20:00Z",
            settlement_timestamp_utc="2026-06-06T04:21:00Z",
        ),
    )
    _write_settlement(
        tmp_path,
        _settlement_row(
            tmp_path,
            suffix="deterministic-settled-b",
            settlement_source_timestamp_utc="2026-06-06T04:40:00Z",
            settlement_timestamp_utc="2026-06-06T04:41:00Z",
        ),
    )

    first_effective = _effective(tmp_path)
    second_effective = _effective(tmp_path)

    assert first_effective["conflict_reason"] == "none"
    assert first_effective["selected_settlement_id"] == "settlement-deterministic-settled-b"
    assert second_effective["selected_settlement_hash"] == first_effective[
        "selected_settlement_hash"
    ]


def test_write_order_does_not_change_effective_result(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    settled = _settlement_row(
        tmp_path,
        suffix="written-first-terminal",
        settlement_source_timestamp_utc="2026-06-06T04:20:00Z",
        settlement_timestamp_utc="2026-06-06T04:21:00Z",
    )
    pending = _pending_row(tmp_path, suffix="written-second-earlier-pending")

    _write_settlement(tmp_path, settled)
    result = _write_settlement(tmp_path, pending)

    assert result.completion_status == "complete"
    effective = _effective(tmp_path)
    assert effective["effective_status"] == "settled"
    assert effective["selected_settlement_id"] == "settlement-written-first-terminal"


def test_all_historical_settlement_segments_remain_immutable(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    first = _write_settlement(tmp_path, _pending_row(tmp_path, suffix="immutable-pending"))
    first_snapshot = _snapshot(first.settlement_segment_directory)
    second = _write_settlement(
        tmp_path,
        _manual_review_row(
            tmp_path,
            suffix="immutable-manual",
            final_points=None,
            actual_minutes=38.75,
            participation_status="participated",
            exclusion_reason="missing_final_points",
            settlement_source_timestamp_utc="2026-06-06T03:30:00Z",
            settlement_timestamp_utc="2026-06-06T03:31:00Z",
        ),
    )
    second_snapshot = _snapshot(second.settlement_segment_directory)

    _write_settlement(
        tmp_path,
        _settlement_row(
            tmp_path,
            suffix="immutable-settled",
            settlement_source_timestamp_utc="2026-06-06T04:35:00Z",
            settlement_timestamp_utc="2026-06-06T04:36:00Z",
        ),
    )

    assert _snapshot(first.settlement_segment_directory) == first_snapshot
    assert _snapshot(second.settlement_segment_directory) == second_snapshot
    effective = _effective(tmp_path)
    assert effective["historical_settlement_count"] == 3
    assert [row["settlement_id"] for row in effective["historical_settlement_records"]] == [
        "settlement-immutable-pending",
        "settlement-immutable-manual",
        "settlement-immutable-settled",
    ]


@pytest.mark.parametrize(
    "changed",
    [
        {"suffix": "points-conflict", "final_points": 36.0},
        {"suffix": "minutes-conflict", "actual_minutes": 39.25},
        {"suffix": "participation-conflict", "actual_minutes": 0.0, "participation_status": "zero_minutes"},
    ],
)
def test_conflicting_terminal_points_minutes_or_participation_fail_closed(
    tmp_path: Path,
    changed: dict[str, object],
) -> None:
    _write_prediction(tmp_path)
    _write_settlement(tmp_path, _settlement_row(tmp_path, suffix="terminal-a"))
    conflict = _settlement_row(
        tmp_path,
        settlement_source_timestamp_utc="2026-06-06T04:35:00Z",
        settlement_timestamp_utc="2026-06-06T04:36:00Z",
        **changed,
    )

    result = _write_settlement(tmp_path, conflict)

    assert result.completion_status == "conflicting"
    assert result.conflicts_written == 1
    assert _settlement_conflicts(tmp_path)[0]["conflict_reason"] == "conflicting_terminal_outcome"
    effective = _effective(tmp_path)
    assert effective["effective_status"] == "conflicting"
    assert effective["selected_settlement_record"] is None
    assert effective["conflict_reason"] == "conflicting_terminal_outcome"
    assert verify_nba_player_points_settlement_evidence(tmp_path, SETTLEMENT_CONFIG).ok is True


def test_null_minutes_zero_minutes_and_dnp_remain_distinct(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    pending = _pending_row(tmp_path, suffix="pending-null-minutes")
    zero = _zero_minutes_row(tmp_path)
    dnp = _void_dnp_row(tmp_path)

    _write_settlement(tmp_path, pending)
    _write_settlement(tmp_path, zero)
    result = _write_settlement(tmp_path, dnp)

    rows = _settlement_rows(tmp_path)
    by_id = {row["settlement_id"]: row for row in rows}
    assert by_id["settlement-pending-null-minutes"]["actual_minutes"] is None
    assert by_id["settlement-zero"]["actual_minutes"] == 0.0
    assert by_id["settlement-zero"]["participation_status"] == "zero_minutes"
    assert by_id["settlement-dnp"]["actual_minutes"] is None
    assert by_id["settlement-dnp"]["participation_status"] == "did_not_participate"
    assert result.completion_status == "conflicting"
    assert _effective(tmp_path)["conflict_reason"] == "conflicting_terminal_outcome"


def test_policy_versions_are_isolated_for_effective_settlement(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    v2_config = NBAPlayerPointsSettlementEvidenceWriterConfig(
        policy=NBAPlayerPointsSettlementPolicy(settlement_policy_version="2.0")
    )
    _write_settlement(tmp_path, _settlement_row(tmp_path, suffix="policy-v1", final_points=35.0))
    _write_settlement(
        tmp_path,
        _settlement_row(
            tmp_path,
            suffix="policy-v2",
            final_points=40.0,
            actual_minutes=39.0,
            settlement_source_timestamp_utc="2026-06-06T04:40:00Z",
            settlement_timestamp_utc="2026-06-06T04:41:00Z",
        ),
        config=v2_config,
    )

    v1 = _effective(tmp_path, SETTLEMENT_CONFIG)
    v2 = _effective(tmp_path, v2_config)

    assert v1["settlement_policy_version"] == "1.0"
    assert v1["selected_settlement_record"]["final_points"] == 35.0
    assert v2["settlement_policy_version"] == "2.0"
    assert v2["selected_settlement_record"]["final_points"] == 40.0
    report = verify_nba_player_points_settlement_evidence(tmp_path, SETTLEMENT_CONFIG)
    assert {
        row["settlement_policy_version"] for row in report.to_dict()["effective_settlements"]
    } == {"1.0", "2.0"}


def test_prediction_reference_mismatch_missing_and_incomplete_runs_fail_before_write(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    before = _snapshot(_evidence_root(tmp_path))
    bad_hash = replace(_settlement_row(tmp_path), prediction_artifact_hash="0" * 64)

    with pytest.raises(NBAPlayerPointsSettlementEvidenceError, match="prediction_artifact_hash"):
        _write_settlement(tmp_path, bad_hash)
    assert _snapshot(_evidence_root(tmp_path)) == before

    row = _settlement_row(tmp_path, suffix="incomplete-reference")
    shutil.rmtree(next(_evidence_root(tmp_path).glob("runs/*/*")))

    with pytest.raises(NBAPlayerPointsSettlementEvidenceError, match="not complete"):
        _write_settlement(tmp_path, row)


def test_identical_replay_is_already_complete_and_does_not_rewrite_bytes(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    row = _settlement_row(tmp_path)
    first = _write_settlement(tmp_path, row)
    snapshot = _snapshot(_evidence_root(tmp_path) / "settlement")

    second = _write_settlement(tmp_path, row)

    assert second.completion_status == "already_complete"
    assert second.settlement_rows_written == 0
    assert second.settlement_batch_id == first.settlement_batch_id
    assert _snapshot(_evidence_root(tmp_path) / "settlement") == snapshot


def test_corrupted_completed_segment_is_detected_and_not_replayed(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    row = _settlement_row(tmp_path)
    result = _write_settlement(tmp_path, row)
    rows_path = result.settlement_segment_directory / "settlement_rows.jsonl"
    stored = _read_jsonl(rows_path)[0]
    stored["final_points"] = 99.0
    rows_path.write_text(json.dumps(stored, sort_keys=True) + "\n", encoding="utf-8")

    report = verify_nba_player_points_settlement_evidence(tmp_path, SETTLEMENT_CONFIG)
    assert report.ok is False
    assert any("settlement_rows.jsonl hash mismatch" in violation for violation in report.violations)
    with pytest.raises(NBAPlayerPointsSettlementEvidenceError, match="failed verification"):
        _write_settlement(tmp_path, row)


def test_missing_final_newline_is_precise_corruption(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    result = _write_settlement(tmp_path, _settlement_row(tmp_path))
    rows_path = result.settlement_segment_directory / "settlement_rows.jsonl"
    rows_path.write_bytes(rows_path.read_bytes().rstrip(b"\n"))

    report = verify_nba_player_points_settlement_evidence(tmp_path, SETTLEMENT_CONFIG)

    assert report.ok is False
    assert any("JSONL frame missing final newline" in violation for violation in report.violations)


def test_crash_recovery_before_publication_and_lock_release(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    row = _settlement_row(tmp_path)

    with pytest.raises(RuntimeError, match="before_settlement_segment_publication"):
        _write_settlement(
            tmp_path,
            row,
            failure_hook=_fail_at("before_settlement_segment_publication"),
        )

    assert list(_evidence_root(tmp_path).glob("settlement/segments/*/*/COMPLETE")) == []
    assert not (_evidence_root(tmp_path) / ".settlement-writer.lock").exists()
    recovered = _write_settlement(tmp_path, row)
    assert recovered.completion_status == "complete"
    assert verify_nba_player_points_settlement_evidence(tmp_path, SETTLEMENT_CONFIG).ok is True


def test_short_write_leaves_no_completed_segment_and_retry_succeeds(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    row = _settlement_row(tmp_path)

    def short_write(path: Path, data: bytes) -> None:
        path.write_bytes(data[:9])
        raise RuntimeError("short write")

    with patch(
        "courtvision.sports.nba.player_points_settlement_evidence._write_bytes_verified",
        side_effect=short_write,
    ):
        with pytest.raises(RuntimeError, match="short write"):
            _write_settlement(tmp_path, row)

    assert _settlement_segment_dirs(tmp_path) == []
    recovered = _write_settlement(tmp_path, row)
    assert recovered.completion_status == "complete"
    assert len(_settlement_rows(tmp_path)) == 1


def _run_settlements_concurrently(
    tmp_path: Path,
    *rows: NBAPlayerPointsSettlementRow,
) -> list[object]:
    barrier = threading.Barrier(len(rows))

    def worker(row: NBAPlayerPointsSettlementRow) -> object:
        barrier.wait(timeout=5)
        return _write_settlement(tmp_path, row)

    outcomes: list[object] = []
    with ThreadPoolExecutor(max_workers=len(rows)) as executor:
        futures = [executor.submit(worker, row) for row in rows]
        for future in as_completed(futures):
            try:
                outcomes.append(future.result())
            except Exception as exc:
                outcomes.append(exc)
    return outcomes


def test_concurrent_identical_replay_does_not_duplicate_segments(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    row = _settlement_row(tmp_path)

    outcomes = _run_settlements_concurrently(tmp_path, row, row)

    assert all(not isinstance(outcome, Exception) for outcome in outcomes)
    assert sorted(outcome.completion_status for outcome in outcomes) == ["already_complete", "complete"]
    assert len(_settlement_rows(tmp_path)) == 1
    assert verify_nba_player_points_settlement_evidence(tmp_path, SETTLEMENT_CONFIG).ok is True


def test_concurrent_conflicting_terminal_batches_resolve_to_conflict(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    first = _settlement_row(tmp_path, suffix="concurrent-a", final_points=35.0)
    second = _settlement_row(
        tmp_path,
        suffix="concurrent-b",
        final_points=37.0,
        settlement_source_timestamp_utc="2026-06-06T04:35:00Z",
        settlement_timestamp_utc="2026-06-06T04:36:00Z",
    )

    outcomes = _run_settlements_concurrently(tmp_path, first, second)

    assert all(not isinstance(outcome, Exception) for outcome in outcomes)
    assert sorted(outcome.completion_status for outcome in outcomes) == ["complete", "conflicting"]
    assert _effective(tmp_path)["effective_status"] == "conflicting"
    assert verify_nba_player_points_settlement_evidence(tmp_path, SETTLEMENT_CONFIG).ok is True


def test_path_and_symlink_safety(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    with pytest.raises(NBAPlayerPointsSettlementEvidenceError, match="\\.\\.|path separators"):
        NBAPlayerPointsSettlementEvidenceWriterConfig(settlement_dir_name="../evil")

    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "linked-root"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(NBAPlayerPointsSettlementEvidenceError, match="symlink"):
        _write_settlement(link, _settlement_row(tmp_path))


def _closing_observation(tmp_path: Path) -> dict[str, object]:
    prediction = _prediction_row(tmp_path)
    ledger_path = _prediction_ledger_path(tmp_path)
    return {
        "prediction_reference": {
            "prediction_id": prediction["prediction_id"],
            "prediction_run_id": prediction["prediction_run_id"],
            "prediction_evidence_segment": ledger_path.relative_to(_evidence_root(tmp_path)).as_posix(),
            "prediction_record_hash": prediction["ledger_record_hash"],
        },
        "canonical_event_id": prediction["canonical_event_id"],
        "provider_event_id": prediction["provider_event_id"],
        "player_id": prediction["player_id"],
        "sportsbook": prediction["sportsbook"],
        "market": prediction["market"],
        "operating_date": prediction["operating_date"],
        "commence_time_utc": prediction["commence_time_utc"],
        "closing_line": 32.5,
        "closing_american_odds": -115,
        "closing_market_status": "open",
        "observation_timestamp_utc": "2026-06-06T00:39:00Z",
        "source_market_update_timestamp_utc": "2026-06-06T00:39:00Z",
        "closing_provider": "offline_closing_fixture",
        "closing_source_id": "closing-source-settlement-immutability",
        "closing_source_hash": "9" * 64,
    }


def test_prediction_and_closing_evidence_are_immutable_and_no_live_calls_or_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_prediction(tmp_path)
    write_nba_player_points_closing_evidence(
        tmp_path,
        [_closing_observation(tmp_path)],
        NBAPlayerPointsClosingWriterConfig(),
        collection_timestamp_utc="2026-06-06T00:39:30Z",
        repository_commit_sha="f6b52cb9caf195346d4100b37add5396e45688b2",
        writer_timestamp_utc="2026-06-06T00:39:30Z",
    )
    prediction_snapshot = _snapshot(_evidence_root(tmp_path) / "runs") + _snapshot(
        _evidence_root(tmp_path) / "ledgers"
    )
    closing_snapshot = _snapshot(_evidence_root(tmp_path) / "closing")
    monkeypatch.chdir(tmp_path)

    with (
        patch("requests.Session.get", side_effect=AssertionError("live call attempted")) as mock_get,
        patch("os.getenv", side_effect=AssertionError("credential read attempted")) as mock_getenv,
    ):
        _write_settlement(tmp_path, _settlement_row(tmp_path))

    assert mock_get.call_count == 0
    assert mock_getenv.call_count == 0
    assert _snapshot(_evidence_root(tmp_path) / "runs") + _snapshot(
        _evidence_root(tmp_path) / "ledgers"
    ) == prediction_snapshot
    assert _snapshot(_evidence_root(tmp_path) / "closing") == closing_snapshot
    assert not (tmp_path / "outputs").exists()
    assert not (tmp_path / "test_outputs").exists()
    assert not (tmp_path / "data" / "history").exists()


def test_no_forbidden_runtime_or_bankroll_imports_in_settlement_evidence_module() -> None:
    source = SETTLEMENT_MODULE.read_text(encoding="utf-8").casefold()

    assert "sports.mlb" not in source
    assert "courtvision_ai" not in source
    assert "run_today" not in source
    assert "requests" not in source
    assert "os.getenv" not in source
    assert "os.environ" not in source
    assert "player_points_closing" not in source
    assert "settle_nba_player_points_predictions" not in source
    assert "import kelly" not in source
    assert "kelly_" not in source
    assert "import bankroll" not in source
    assert "bankroll_" not in source
