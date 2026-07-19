from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import threading
from unittest.mock import patch

import pytest

from courtvision.sports.nba.player_points_assembly import (
    NBAPlayerPointsAssemblyBatchResult,
    assemble_nba_player_points_batch,
)
from courtvision.sports.nba.player_points_closing import (
    NBA_PLAYER_POINTS_CLOSING_SCHEMA_VERSION,
    NBA_PLAYER_POINTS_CLOSING_SELECTION_SCHEMA_VERSION,
    NBAPlayerPointsClosingError,
    NBAPlayerPointsClosingPolicy,
    NBAPlayerPointsClosingWriterConfig,
    closing_observation_schema_definition,
    closing_selection_schema_definition,
    default_closing_policy,
    resolve_nba_player_points_effective_closing_selection,
    verify_nba_player_points_closing_evidence,
    write_nba_player_points_closing_evidence,
)
from courtvision.sports.nba.player_points_evidence import (
    NBAPlayerPointsEvidenceWriterConfig,
    write_nba_player_points_evidence,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "nba" / "player_points"
ASSEMBLY_CASES_FIXTURE = FIXTURE_ROOT / "assembly_cases.json"
WRITER_TIMESTAMP = "2026-06-06T00:39:30Z"
COLLECTION_TIMESTAMP = "2026-06-06T00:39:30Z"
CONFIG = NBAPlayerPointsClosingWriterConfig()
EVIDENCE_CONFIG = NBAPlayerPointsEvidenceWriterConfig()


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
    case_id: str,
    extra_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    fixture = _load_fixture()
    base = fixture["base_case"]
    cases = {case["case_id"]: case for case in fixture["cases"]}
    payload = _deep_merge(base, cases[case_id].get("overrides", {}))
    if extra_overrides:
        payload = _deep_merge(payload, extra_overrides)
    return payload


def _batch_payloads(*payloads: dict[str, object]) -> NBAPlayerPointsAssemblyBatchResult:
    return assemble_nba_player_points_batch(
        payloads,
        manifest_created_at_utc="2026-06-05T18:06:00Z",
    )


def _batch(case_id: str = "valid_projection_no_probabilities") -> NBAPlayerPointsAssemblyBatchResult:
    return _batch_payloads(_case_payload(case_id))


def _write_prediction(tmp_path: Path, result: NBAPlayerPointsAssemblyBatchResult | None = None):
    result = result or _batch()
    return write_nba_player_points_evidence(
        result,
        result.source_manifest_preview,
        tmp_path,
        EVIDENCE_CONFIG,
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


def _stable_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _json_file_bytes(payload: object) -> bytes:
    return _stable_json_bytes(payload) + b"\n"


def _jsonl_bytes(rows: list[dict[str, object]]) -> bytes:
    return b"".join(_stable_json_bytes(row) + b"\n" for row in rows)


def _record_hash(payload: dict[str, object], hash_field: str) -> str:
    return hashlib.sha256(
        _stable_json_bytes({key: value for key, value in payload.items() if key != hash_field})
    ).hexdigest()


def _selection_id(payload: dict[str, object]) -> str:
    values = {
        key: payload.get(key)
        for key in (
            "schema_version",
            "prediction_id",
            "prediction_run_id",
            "selected_observation_id",
            "selected_observation_hash",
            "closing_policy_id",
            "closing_policy_version",
            "selected_at_utc",
            "selection_status",
            "selection_exclusion_reason",
        )
    }
    return "nba-close-selection-" + hashlib.sha256(_stable_json_bytes(values)).hexdigest()[:32]


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_bytes(_json_file_bytes(payload))


def _rewrite_selection_segment(selection_path: Path, rows: list[dict[str, object]]) -> None:
    segment = selection_path.parent
    selection_path.write_bytes(_jsonl_bytes(rows))
    integrity_path = segment / "integrity_report.json"
    integrity = _read_json(integrity_path)
    integrity["selection_hash"] = _sha256(selection_path)
    integrity["selection_count"] = len(rows)
    _write_json(integrity_path, integrity)
    manifest_path = segment / "selection_manifest.json"
    manifest = _read_json(manifest_path)
    manifest["selection_file_hash"] = _sha256(selection_path)
    manifest["integrity_report_hash"] = _sha256(integrity_path)
    manifest["selection_count"] = len(rows)
    manifest["selection_manifest_hash"] = _record_hash(manifest, "selection_manifest_hash")
    _write_json(manifest_path, manifest)
    marker_path = segment / "COMPLETE"
    marker = _read_json(marker_path)
    marker["manifest_hash"] = _sha256(manifest_path)
    _write_json(marker_path, marker)


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


def _prediction_row(tmp_path: Path) -> dict[str, object]:
    ledger_paths = sorted(_evidence_root(tmp_path).glob("ledgers/segments/*/*/prediction_ledger.jsonl"))
    assert len(ledger_paths) == 1
    return _read_jsonl(ledger_paths[0])[0]


def _prediction_reference(tmp_path: Path) -> dict[str, object]:
    ledger_paths = sorted(_evidence_root(tmp_path).glob("ledgers/segments/*/*/prediction_ledger.jsonl"))
    assert len(ledger_paths) == 1
    row = _read_jsonl(ledger_paths[0])[0]
    return {
        "prediction_id": row["prediction_id"],
        "prediction_run_id": row["prediction_run_id"],
        "prediction_evidence_segment": ledger_paths[0].relative_to(_evidence_root(tmp_path)).as_posix(),
        "prediction_record_hash": row["ledger_record_hash"],
    }


def _observation(
    tmp_path: Path,
    *,
    suffix: str = "base",
    observation_timestamp_utc: str = "2026-06-06T00:39:00Z",
    source_market_update_timestamp_utc: str | None = None,
    closing_line: float | None = 32.5,
    closing_american_odds: int | None = -115,
    sportsbook: str | None = None,
    market: str | None = None,
    canonical_event_id: str | None = None,
    player_id: str | None = None,
    provider_event_id: str | None = None,
    closing_source_hash: str = "9999999999999999999999999999999999999999999999999999999999999999",
) -> dict[str, object]:
    row = _prediction_row(tmp_path)
    return {
        "prediction_reference": _prediction_reference(tmp_path),
        "canonical_event_id": canonical_event_id or row["canonical_event_id"],
        "provider_event_id": provider_event_id or row["provider_event_id"],
        "player_id": player_id or row["player_id"],
        "sportsbook": sportsbook or row["sportsbook"],
        "market": market or row["market"],
        "operating_date": row["operating_date"],
        "commence_time_utc": row["commence_time_utc"],
        "closing_line": closing_line,
        "closing_american_odds": closing_american_odds,
        "closing_market_status": "open",
        "observation_timestamp_utc": observation_timestamp_utc,
        "source_market_update_timestamp_utc": (
            source_market_update_timestamp_utc or observation_timestamp_utc
        ),
        "closing_provider": "offline_closing_fixture",
        "closing_source_id": f"closing-source-{suffix}",
        "closing_source_hash": closing_source_hash,
    }


def _write_closing(
    tmp_path: Path,
    *observations: dict[str, object],
    config: NBAPlayerPointsClosingWriterConfig = CONFIG,
    collection_timestamp_utc: str = COLLECTION_TIMESTAMP,
    writer_timestamp_utc: str = WRITER_TIMESTAMP,
    failure_hook=None,
):
    return write_nba_player_points_closing_evidence(
        tmp_path,
        observations,
        config,
        collection_timestamp_utc=collection_timestamp_utc,
        repository_commit_sha="f6b52cb9caf195346d4100b37add5396e45688b2",
        writer_timestamp_utc=writer_timestamp_utc,
        failure_hook=failure_hook,
    )


def _observation_rows(tmp_path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(_evidence_root(tmp_path).glob("closing/observations/segments/*/*/closing_observations.jsonl")):
        rows.extend(_read_jsonl(path))
    return rows


def _conflict_rows(tmp_path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(_evidence_root(tmp_path).glob("closing/observations/segments/*/*/closing_conflicts.jsonl")):
        rows.extend(_read_jsonl(path))
    return rows


def _selection_rows(tmp_path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(_evidence_root(tmp_path).glob("closing/selections/segments/*/*/selected_closing_rows.jsonl")):
        rows.extend(_read_jsonl(path))
    return rows


def _effective_selection(
    tmp_path: Path,
    config: NBAPlayerPointsClosingWriterConfig = CONFIG,
) -> dict[str, object]:
    return dict(
        resolve_nba_player_points_effective_closing_selection(
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


def test_schema_definitions_document_closing_contract() -> None:
    observation_schema = closing_observation_schema_definition()
    selection_schema = closing_selection_schema_definition()
    policy = default_closing_policy()

    assert observation_schema["schema_version"] == NBA_PLAYER_POINTS_CLOSING_SCHEMA_VERSION
    assert selection_schema["schema_version"] == NBA_PLAYER_POINTS_CLOSING_SELECTION_SCHEMA_VERSION
    assert "closing_observation_id" in observation_schema["required_fields"]
    assert "closing_selection_id" in selection_schema["required_fields"]
    assert "eligible" in observation_schema["statuses"]
    assert policy.closing_window_start_seconds == 1800
    assert policy.closing_window_end_seconds == 0
    assert policy.same_book_required is True
    assert policy.same_market_required is True


def test_valid_same_book_close_creates_append_only_layout_and_selection(tmp_path: Path) -> None:
    _write_prediction(tmp_path)

    result = _write_closing(tmp_path, _observation(tmp_path))

    assert result.completion_status == "complete"
    assert result.observations_written == 1
    assert result.selections_written == 1
    assert (result.observation_segment_directory / "closing_manifest.json").exists()
    assert (result.observation_segment_directory / "closing_observations.jsonl").exists()
    assert (result.observation_segment_directory / "closing_conflicts.jsonl").exists()
    assert (result.observation_segment_directory / "COMPLETE").exists()
    assert (result.selection_segment_directory / "selection_manifest.json").exists()
    row = _observation_rows(tmp_path)[0]
    selected = _selection_rows(tmp_path)[0]
    assert row["observation_eligibility_status"] == "eligible"
    assert row["seconds_before_tipoff"] == 60
    assert selected["selection_status"] == "selected"
    assert selected["selected_observation_id"] == row["closing_observation_id"]
    assert selected["line_movement"] == pytest.approx(1.0)
    assert selected["price_movement"] == -5
    assert verify_nba_player_points_closing_evidence(tmp_path, CONFIG).ok is True


def test_multiple_eligible_observations_latest_wins_and_all_are_preserved(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    early = _observation(
        tmp_path,
        suffix="early",
        observation_timestamp_utc="2026-06-06T00:20:00Z",
        closing_line=31.5,
        closing_american_odds=-110,
    )
    late = _observation(
        tmp_path,
        suffix="late",
        observation_timestamp_utc="2026-06-06T00:39:59Z",
        closing_line=33.5,
        closing_american_odds=-120,
    )

    _write_closing(tmp_path, early, late)

    observations = _observation_rows(tmp_path)
    selections = _selection_rows(tmp_path)
    assert len(observations) == 2
    assert {row["observation_eligibility_status"] for row in observations} == {"eligible"}
    assert selections[-1]["closing_line"] == 33.5
    assert selections[-1]["selected_observation_id"] == next(
        row["closing_observation_id"] for row in observations if row["closing_line"] == 33.5
    )


@pytest.mark.parametrize(
    ("timestamp", "expected_status", "seconds"),
    [
        ("2026-06-06T00:09:59Z", "too_early", 1801),
        ("2026-06-06T00:10:00Z", "eligible", 1800),
        ("2026-06-06T00:39:59Z", "eligible", 1),
        ("2026-06-06T00:40:00Z", "after_tipoff", 0),
        ("2026-06-06T00:40:01Z", "after_tipoff", -1),
    ],
)
def test_close_window_boundaries(tmp_path: Path, timestamp: str, expected_status: str, seconds: int) -> None:
    _write_prediction(tmp_path)

    _write_closing(tmp_path, _observation(tmp_path, observation_timestamp_utc=timestamp))

    row = _observation_rows(tmp_path)[0]
    assert row["observation_eligibility_status"] == expected_status
    assert row["seconds_before_tipoff"] == seconds
    if expected_status == "after_tipoff":
        assert _selection_rows(tmp_path)[0]["selection_status"] == "no_eligible_observation"


@pytest.mark.parametrize(
    ("overrides", "expected_status", "reason"),
    [
        ({"sportsbook": "FanDuel"}, "wrong_book", "same_book_required"),
        ({"market": "player_rebounds"}, "wrong_market", "same_market_required"),
        ({"player_id": "nba-player-other"}, "manual_review_required", "wrong_player"),
        ({"canonical_event_id": "nba-other-event"}, "manual_review_required", "wrong_event"),
        ({"closing_line": None}, "missing_line", "missing_closing_line"),
        ({"closing_american_odds": None}, "missing_price", "missing_closing_american_odds"),
    ],
)
def test_ineligible_observation_statuses_are_preserved_but_not_selected(
    tmp_path: Path,
    overrides: dict[str, object],
    expected_status: str,
    reason: str,
) -> None:
    _write_prediction(tmp_path)

    _write_closing(tmp_path, _observation(tmp_path, **overrides))

    row = _observation_rows(tmp_path)[0]
    assert row["observation_eligibility_status"] == expected_status
    assert row["exclusion_reason"] == reason
    assert _selection_rows(tmp_path)[0]["selection_status"] == "no_eligible_observation"


def test_missing_source_hash_and_timezone_naive_inputs_fail_before_write(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    before = _snapshot(_evidence_root(tmp_path))

    with pytest.raises(NBAPlayerPointsClosingError, match="closing_source_hash"):
        _write_closing(
            tmp_path,
            _observation(tmp_path, closing_source_hash=""),
        )
    with pytest.raises(NBAPlayerPointsClosingError, match="timezone-aware"):
        _write_closing(
            tmp_path,
            _observation(tmp_path, observation_timestamp_utc="2026-06-06T00:39:00"),
        )

    assert _snapshot(_evidence_root(tmp_path)) == before


def test_source_market_update_cannot_be_after_observation(tmp_path: Path) -> None:
    _write_prediction(tmp_path)

    with pytest.raises(NBAPlayerPointsClosingError, match="source_market_update"):
        _write_closing(
            tmp_path,
            _observation(
                tmp_path,
                source_market_update_timestamp_utc="2026-06-06T00:39:01Z",
            ),
        )


def test_prediction_reference_validation_missing_incomplete_and_corrupt_runs(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    missing_ref = _observation(tmp_path)
    missing_ref["prediction_reference"] = {
        **missing_ref["prediction_reference"],
        "prediction_id": "missing-prediction-id",
    }

    _write_closing(tmp_path, missing_ref)
    assert _observation_rows(tmp_path)[0]["observation_eligibility_status"] == "prediction_not_found"

    incomplete_dir = tmp_path.parent / f"i_{tmp_path.name[-6:]}"
    shutil.rmtree(incomplete_dir, ignore_errors=True)
    result = _batch()

    def fail_after_ledger(stage: str) -> None:
        if stage == "after_ledger_append_before_run_publication":
            raise RuntimeError(stage)

    with pytest.raises(RuntimeError):
        write_nba_player_points_evidence(
            result,
            result.source_manifest_preview,
            incomplete_dir,
            EVIDENCE_CONFIG,
            repository_commit_sha=result.source_manifest_preview.repository_commit_sha,
            writer_timestamp_utc="2026-06-05T18:10:00Z",
            failure_hook=fail_after_ledger,
        )
    row = _read_jsonl(next((incomplete_dir / "nba_player_points_evidence").glob("ledgers/segments/*/*/prediction_ledger.jsonl")))[0]
    obs = {
        **_observation(tmp_path),
        "prediction_reference": {
            "prediction_id": row["prediction_id"],
            "prediction_run_id": row["prediction_run_id"],
            "prediction_evidence_segment": next(
                (incomplete_dir / "nba_player_points_evidence").glob("ledgers/segments/*/*/prediction_ledger.jsonl")
            ).relative_to(incomplete_dir / "nba_player_points_evidence").as_posix(),
            "prediction_record_hash": row["ledger_record_hash"],
        },
    }
    for key in (
        "canonical_event_id",
        "provider_event_id",
        "player_id",
        "sportsbook",
        "market",
        "operating_date",
        "commence_time_utc",
    ):
        obs[key] = row[key]
    _write_closing(incomplete_dir, obs)
    assert _observation_rows(incomplete_dir)[0]["observation_eligibility_status"] == "prediction_not_complete"

    corrupt_dir = tmp_path / "corrupt"
    write_result = _write_prediction(corrupt_dir)
    prediction_file = write_result.run_directory / "prediction_rows.jsonl"
    prediction_file.write_text("{bad\n", encoding="utf-8")
    with pytest.raises(NBAPlayerPointsClosingError, match="prediction evidence failed verification"):
        _write_closing(corrupt_dir, _observation(corrupt_dir))


def test_identical_duplicate_observation_replay_is_idempotent(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    observation = _observation(tmp_path)
    first = _write_closing(tmp_path, observation)
    snapshot = _snapshot(_evidence_root(tmp_path) / "closing")

    second = _write_closing(tmp_path, observation)

    assert first.closing_batch_id == second.closing_batch_id
    assert second.completion_status == "already_complete"
    assert _snapshot(_evidence_root(tmp_path) / "closing") == snapshot
    assert len(_observation_rows(tmp_path)) == 1


def test_within_batch_identical_duplicate_collapses(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    observation = _observation(tmp_path)

    _write_closing(tmp_path, observation, observation)

    assert len(_observation_rows(tmp_path)) == 1
    assert _conflict_rows(tmp_path) == []


@pytest.mark.parametrize(
    "changed",
    [
        {"closing_line": 33.5},
        {"closing_american_odds": -125},
    ],
)
def test_same_timestamp_conflicting_content_is_preserved_as_conflict(
    tmp_path: Path,
    changed: dict[str, object],
) -> None:
    _write_prediction(tmp_path)
    base = _observation(tmp_path, suffix="same-source")
    conflict = {**base, **changed}

    result = _write_closing(tmp_path, base, conflict)

    assert result.completion_status == "conflicting"
    assert _observation_rows(tmp_path) == []
    assert len(_conflict_rows(tmp_path)) >= 1
    assert _selection_rows(tmp_path)[0]["selection_status"] == "no_eligible_observation"


def test_multiple_sportsbooks_and_lines_are_deterministic_and_policy_filtered(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    valid = _observation(tmp_path, suffix="dk", closing_line=32.5)
    wrong_book = _observation(
        tmp_path,
        suffix="fd",
        sportsbook="FanDuel",
        closing_line=30.5,
        closing_american_odds=-105,
    )
    alt_line_later = _observation(
        tmp_path,
        suffix="dk-alt",
        observation_timestamp_utc="2026-06-06T00:39:30Z",
        closing_line=33.5,
    )

    forward_dir = tmp_path.parent / f"f_{tmp_path.name[-6:]}"
    reverse_dir = tmp_path.parent / f"r_{tmp_path.name[-6:]}"
    shutil.rmtree(forward_dir, ignore_errors=True)
    shutil.rmtree(reverse_dir, ignore_errors=True)
    _write_prediction(forward_dir)
    f_valid = _observation(forward_dir, suffix="dk", closing_line=32.5)
    f_wrong_book = _observation(
        forward_dir,
        suffix="fd",
        sportsbook="FanDuel",
        closing_line=30.5,
        closing_american_odds=-105,
    )
    f_alt_line_later = _observation(
        forward_dir,
        suffix="dk-alt",
        observation_timestamp_utc="2026-06-06T00:39:30Z",
        closing_line=33.5,
    )
    forward = _write_closing(forward_dir, f_valid, f_wrong_book, f_alt_line_later)
    _write_prediction(reverse_dir)
    r_valid = _observation(reverse_dir, suffix="dk", closing_line=32.5)
    r_wrong_book = _observation(
        reverse_dir,
        suffix="fd",
        sportsbook="FanDuel",
        closing_line=30.5,
        closing_american_odds=-105,
    )
    r_alt_line_later = _observation(
        reverse_dir,
        suffix="dk-alt",
        observation_timestamp_utc="2026-06-06T00:39:30Z",
        closing_line=33.5,
    )
    reverse = _write_closing(reverse_dir, r_alt_line_later, r_wrong_book, r_valid)

    assert forward.closing_batch_id == reverse.closing_batch_id
    assert _selection_rows(forward_dir)[-1]["closing_line"] == 33.5
    assert {
        row["observation_eligibility_status"] for row in _observation_rows(forward_dir)
    } == {"eligible", "wrong_book"}


def test_later_pre_tip_observation_creates_new_selection_without_mutating_old_segments(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    first = _write_closing(
        tmp_path,
        _observation(tmp_path, suffix="first", observation_timestamp_utc="2026-06-06T00:38:00Z"),
        collection_timestamp_utc="2026-06-06T00:38:10Z",
        writer_timestamp_utc="2026-06-06T00:38:10Z",
    )
    first_segment_snapshot = _snapshot(first.observation_segment_directory)

    second = _write_closing(
        tmp_path,
        _observation(
            tmp_path,
            suffix="second",
            observation_timestamp_utc="2026-06-06T00:39:50Z",
            closing_line=34.5,
        ),
        collection_timestamp_utc="2026-06-06T00:39:55Z",
        writer_timestamp_utc="2026-06-06T00:39:55Z",
    )

    assert _snapshot(first.observation_segment_directory) == first_segment_snapshot
    assert second.selection_batch_id != first.selection_batch_id
    assert len(_observation_rows(tmp_path)) == 2
    assert _selection_rows(tmp_path)[-1]["closing_line"] == 34.5


def test_cross_batch_effective_selection_evolves_append_only_and_replay_safe(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    batch_a = _observation(
        tmp_path,
        suffix="batch-a",
        observation_timestamp_utc="2026-06-06T00:20:00Z",
        closing_line=31.5,
        closing_american_odds=-110,
    )
    first = _write_closing(
        tmp_path,
        batch_a,
        collection_timestamp_utc="2026-06-06T00:20:05Z",
        writer_timestamp_utc="2026-06-06T00:20:05Z",
    )
    first_observation_segment = _snapshot(first.observation_segment_directory)
    first_selection_segment = _snapshot(first.selection_segment_directory)
    assert _effective_selection(tmp_path)["selected_observation"]["closing_line"] == 31.5

    batch_b = _observation(
        tmp_path,
        suffix="batch-b",
        observation_timestamp_utc="2026-06-06T00:38:00Z",
        closing_line=34.5,
        closing_american_odds=-120,
    )
    second = _write_closing(
        tmp_path,
        batch_b,
        collection_timestamp_utc="2026-06-06T00:38:05Z",
        writer_timestamp_utc="2026-06-06T00:38:05Z",
    )

    assert _snapshot(first.observation_segment_directory) == first_observation_segment
    assert _snapshot(first.selection_segment_directory) == first_selection_segment
    assert second.observation_segment_directory != first.observation_segment_directory
    assert second.selection_segment_directory != first.selection_segment_directory
    assert len(_observation_rows(tmp_path)) == 2
    assert len(_selection_rows(tmp_path)) == 2

    effective = _effective_selection(tmp_path)
    assert effective["selection_status"] == "selected"
    assert effective["selected_observation"]["closing_line"] == 34.5
    assert effective["historical_selection_count"] == 2
    assert len(effective["evidence_lineage"]) == 2
    report = verify_nba_player_points_closing_evidence(tmp_path, CONFIG)
    assert report.ok is True
    assert len(report.to_dict()["effective_selections"]) == 1

    closing_snapshot = _snapshot(_evidence_root(tmp_path) / "closing")
    replay_b = _write_closing(
        tmp_path,
        batch_b,
        collection_timestamp_utc="2026-06-06T00:38:05Z",
        writer_timestamp_utc="2026-06-06T00:38:05Z",
    )
    assert replay_b.completion_status == "already_complete"
    assert _snapshot(_evidence_root(tmp_path) / "closing") == closing_snapshot

    replay_a = _write_closing(
        tmp_path,
        batch_a,
        collection_timestamp_utc="2026-06-06T00:20:05Z",
        writer_timestamp_utc="2026-06-06T00:20:05Z",
    )
    assert replay_a.completion_status == "already_complete"
    assert replay_a.selection_batch_id == first.selection_batch_id
    assert _snapshot(_evidence_root(tmp_path) / "closing") == closing_snapshot
    assert _effective_selection(tmp_path)["selected_observation"]["closing_line"] == 34.5


def test_older_outside_window_and_post_tip_batches_do_not_displace_effective_close(
    tmp_path: Path,
) -> None:
    _write_prediction(tmp_path)
    _write_closing(
        tmp_path,
        _observation(
            tmp_path,
            suffix="effective",
            observation_timestamp_utc="2026-06-06T00:38:00Z",
            closing_line=34.5,
        ),
        collection_timestamp_utc="2026-06-06T00:38:05Z",
        writer_timestamp_utc="2026-06-06T00:38:05Z",
    )
    effective_id = _effective_selection(tmp_path)["selected_observation_id"]

    _write_closing(
        tmp_path,
        _observation(
            tmp_path,
            suffix="older-later-batch",
            observation_timestamp_utc="2026-06-06T00:25:00Z",
            closing_line=30.5,
        ),
        collection_timestamp_utc="2026-06-06T00:39:00Z",
        writer_timestamp_utc="2026-06-06T00:39:00Z",
    )
    _write_closing(
        tmp_path,
        _observation(
            tmp_path,
            suffix="too-early-later-batch",
            observation_timestamp_utc="2026-06-06T00:09:59Z",
            closing_line=35.5,
        ),
        collection_timestamp_utc="2026-06-06T00:39:10Z",
        writer_timestamp_utc="2026-06-06T00:39:10Z",
    )
    _write_closing(
        tmp_path,
        _observation(
            tmp_path,
            suffix="post-tip-later-batch",
            observation_timestamp_utc="2026-06-06T00:40:00Z",
            closing_line=36.5,
        ),
        collection_timestamp_utc="2026-06-06T00:40:10Z",
        writer_timestamp_utc="2026-06-06T00:40:10Z",
    )

    effective = _effective_selection(tmp_path)
    assert effective["selected_observation_id"] == effective_id
    assert effective["selected_observation"]["closing_line"] == 34.5
    assert verify_nba_player_points_closing_evidence(tmp_path, CONFIG).ok is True


def test_policy_version_isolation_keeps_effective_selections_partitioned(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    v2_config = NBAPlayerPointsClosingWriterConfig(
        policy=NBAPlayerPointsClosingPolicy(closing_policy_version="2.0")
    )

    _write_closing(
        tmp_path,
        _observation(
            tmp_path,
            suffix="policy-v1",
            observation_timestamp_utc="2026-06-06T00:20:00Z",
            closing_line=31.5,
        ),
        collection_timestamp_utc="2026-06-06T00:20:05Z",
        writer_timestamp_utc="2026-06-06T00:20:05Z",
    )
    _write_closing(
        tmp_path,
        _observation(
            tmp_path,
            suffix="policy-v2",
            observation_timestamp_utc="2026-06-06T00:38:00Z",
            closing_line=34.5,
        ),
        config=v2_config,
        collection_timestamp_utc="2026-06-06T00:38:05Z",
        writer_timestamp_utc="2026-06-06T00:38:05Z",
    )

    v1_effective = _effective_selection(tmp_path, CONFIG)
    v2_effective = _effective_selection(tmp_path, v2_config)
    assert v1_effective["closing_policy_version"] == "1.0"
    assert v1_effective["selected_observation"]["closing_line"] == 31.5
    assert v2_effective["closing_policy_version"] == "2.0"
    assert v2_effective["selected_observation"]["closing_line"] == 34.5

    report = verify_nba_player_points_closing_evidence(tmp_path, CONFIG)
    effective_versions = {
        row["closing_policy_version"] for row in report.to_dict()["effective_selections"]
    }
    assert effective_versions == {"1.0", "2.0"}
    with pytest.raises(NBAPlayerPointsClosingError, match="exactly one effective selection"):
        _effective_selection(
            tmp_path,
            NBAPlayerPointsClosingWriterConfig(
                policy=NBAPlayerPointsClosingPolicy(closing_policy_version="3.0")
            ),
        )


@pytest.mark.parametrize(
    "changed",
    [
        {"closing_line": 35.5},
        {"closing_american_odds": -125},
    ],
)
def test_same_timestamp_conflicting_eligible_candidates_fail_closed(
    tmp_path: Path,
    changed: dict[str, object],
) -> None:
    _write_prediction(tmp_path)
    first = _observation(
        tmp_path,
        suffix="same-time-a",
        observation_timestamp_utc="2026-06-06T00:38:00Z",
        closing_line=34.5,
        closing_american_odds=-115,
    )
    second = {
        **_observation(
            tmp_path,
            suffix="same-time-b",
            observation_timestamp_utc="2026-06-06T00:38:00Z",
            closing_line=34.5,
            closing_american_odds=-115,
        ),
        **changed,
    }

    _write_closing(tmp_path, first, second)

    selection = _selection_rows(tmp_path)[0]
    assert selection["selection_status"] == "conflicting"
    assert selection["selected_observation_id"] is None
    effective = _effective_selection(tmp_path)
    assert effective["selection_status"] == "conflicting"
    assert effective["selected_observation_id"] is None
    assert verify_nba_player_points_closing_evidence(tmp_path, CONFIG).ok is True


def test_concurrent_later_batches_resolve_to_deterministic_latest_observation(
    tmp_path: Path,
) -> None:
    _write_prediction(tmp_path)

    outcomes = _run_closing_concurrently(
        tmp_path,
        _observation(
            tmp_path,
            suffix="concurrent-earlier",
            observation_timestamp_utc="2026-06-06T00:39:20Z",
            closing_line=34.5,
        ),
        _observation(
            tmp_path,
            suffix="concurrent-later",
            observation_timestamp_utc="2026-06-06T00:39:30Z",
            closing_line=35.5,
        ),
    )

    assert all(not isinstance(outcome, Exception) for outcome in outcomes)
    effective = _effective_selection(tmp_path)
    assert effective["selected_observation"]["observation_timestamp_utc"] == "2026-06-06T00:39:30Z"
    assert effective["selected_observation"]["closing_line"] == 35.5
    assert verify_nba_player_points_closing_evidence(tmp_path, CONFIG).ok is True


def test_failure_recovery_for_observation_and_selection_publication(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    observation = _observation(tmp_path)

    with pytest.raises(RuntimeError, match="before_observation_segment_publication"):
        _write_closing(
            tmp_path,
            observation,
            failure_hook=_fail_at("before_observation_segment_publication"),
        )
    assert list(_evidence_root(tmp_path).glob("closing/observations/segments/*/*/COMPLETE")) == []

    with pytest.raises(RuntimeError, match="before_selection_segment_publication"):
        _write_closing(
            tmp_path,
            observation,
            failure_hook=_fail_at("before_selection_segment_publication"),
        )
    assert len(_observation_rows(tmp_path)) == 1
    assert _selection_rows(tmp_path) == []

    recovered = _write_closing(tmp_path, observation)
    assert recovered.completion_status == "already_complete"
    assert len(_selection_rows(tmp_path)) == 1
    assert verify_nba_player_points_closing_evidence(tmp_path, CONFIG).ok is True


def _run_closing_concurrently(tmp_path: Path, *observations: dict[str, object]) -> list[object]:
    barrier = threading.Barrier(len(observations))

    def worker(observation: dict[str, object]) -> object:
        barrier.wait(timeout=5)
        return _write_closing(tmp_path, observation)

    outcomes: list[object] = []
    with ThreadPoolExecutor(max_workers=len(observations)) as executor:
        futures = [executor.submit(worker, observation) for observation in observations]
        for future in as_completed(futures):
            try:
                outcomes.append(future.result())
            except Exception as exc:
                outcomes.append(exc)
    return outcomes


def test_concurrent_identical_conflicting_and_different_batches(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    observation = _observation(tmp_path)

    identical = _run_closing_concurrently(tmp_path, observation, observation)

    assert all(not isinstance(outcome, Exception) for outcome in identical)
    assert sorted(outcome.completion_status for outcome in identical) == ["already_complete", "complete"]
    assert len(_observation_rows(tmp_path)) == 1

    conflict = _run_closing_concurrently(
        tmp_path,
        _observation(tmp_path, suffix="same-source", observation_timestamp_utc="2026-06-06T00:39:10Z"),
        _observation(
            tmp_path,
            suffix="same-source",
            observation_timestamp_utc="2026-06-06T00:39:10Z",
            closing_line=35.5,
        ),
    )

    assert all(not isinstance(outcome, Exception) for outcome in conflict)
    assert any(outcome.completion_status == "conflicting" for outcome in conflict)
    assert _conflict_rows(tmp_path)

    different = _run_closing_concurrently(
        tmp_path,
        _observation(tmp_path, suffix="different-1", observation_timestamp_utc="2026-06-06T00:39:20Z"),
        _observation(tmp_path, suffix="different-2", observation_timestamp_utc="2026-06-06T00:39:30Z"),
    )
    assert all(not isinstance(outcome, Exception) for outcome in different)
    assert verify_nba_player_points_closing_evidence(tmp_path, CONFIG).ok is True


def test_lock_releases_after_exception(tmp_path: Path) -> None:
    _write_prediction(tmp_path)

    with pytest.raises(RuntimeError, match="after_observation_temp_dir_created"):
        _write_closing(
            tmp_path,
            _observation(tmp_path),
            failure_hook=_fail_at("after_observation_temp_dir_created"),
        )

    assert not (_evidence_root(tmp_path) / ".closing-writer.lock").exists()
    assert _write_closing(tmp_path, _observation(tmp_path)).completion_status == "complete"


def test_integrity_verifier_detects_observation_and_selection_corruption(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    _write_closing(tmp_path, _observation(tmp_path))
    observation_path = next(_evidence_root(tmp_path).glob("closing/observations/segments/*/*/closing_observations.jsonl"))
    row = _read_jsonl(observation_path)[0]
    row["closing_line"] = 99.5
    observation_path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    report = verify_nba_player_points_closing_evidence(tmp_path, CONFIG)
    assert report.ok is False
    assert any("hash mismatch" in violation for violation in report.violations)

    clean_dir = tmp_path.parent / f"s_{tmp_path.name[-6:]}"
    shutil.rmtree(clean_dir, ignore_errors=True)
    _write_prediction(clean_dir)
    _write_closing(clean_dir, _observation(clean_dir))
    selection_path = next(_evidence_root(clean_dir).glob("closing/selections/segments/*/*/selected_closing_rows.jsonl"))
    selection = _read_jsonl(selection_path)[0]
    selection["selected_observation_hash"] = "0" * 64
    selection_path.write_text(json.dumps(selection, sort_keys=True) + "\n", encoding="utf-8")

    report = verify_nba_player_points_closing_evidence(clean_dir, CONFIG)
    assert report.ok is False
    assert any("selected_closing_rows.jsonl hash mismatch" in violation for violation in report.violations)


@pytest.mark.parametrize(
    ("case", "expected_violation"),
    [
        ("missing_reference", "selected observation missing"),
        ("wrong_prediction", "selected observation wrong prediction"),
        ("wrong_policy_version", "selected observation wrong policy version"),
    ],
)
def test_selection_reference_semantic_conflicts_are_reported_after_rehash(
    tmp_path: Path,
    case: str,
    expected_violation: str,
) -> None:
    _write_prediction(tmp_path)
    _write_closing(tmp_path, _observation(tmp_path))
    selection_path = next(
        _evidence_root(tmp_path).glob(
            "closing/selections/segments/*/*/selected_closing_rows.jsonl"
        )
    )
    row = _read_jsonl(selection_path)[0]
    if case == "missing_reference":
        row["selected_observation_id"] = "nba-close-obs-missing"
        row["selected_observation_hash"] = "0" * 64
    elif case == "wrong_prediction":
        row["prediction_id"] = "wrong-prediction-id"
    elif case == "wrong_policy_version":
        row["closing_policy_version"] = "2.0"
    row["closing_selection_id"] = _selection_id(row)
    row["selection_record_hash"] = _record_hash(row, "selection_record_hash")
    _rewrite_selection_segment(selection_path, [row])

    report = verify_nba_player_points_closing_evidence(tmp_path, CONFIG)
    assert report.ok is False
    assert any(expected_violation in violation for violation in report.violations)


def test_path_and_symlink_safety(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    bad = _observation(tmp_path)
    bad["prediction_reference"] = {
        **bad["prediction_reference"],
        "prediction_evidence_segment": "../evil",
    }

    with pytest.raises(NBAPlayerPointsClosingError, match="traverse"):
        _write_closing(tmp_path, bad)

    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "linked-root"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(NBAPlayerPointsClosingError, match="symlink"):
        write_nba_player_points_closing_evidence(
            link,
            [_observation(tmp_path)],
            CONFIG,
            collection_timestamp_utc=COLLECTION_TIMESTAMP,
            repository_commit_sha="f6b52cb9caf195346d4100b37add5396e45688b2",
            writer_timestamp_utc=WRITER_TIMESTAMP,
        )


def test_prediction_evidence_is_not_mutated_and_no_production_writes_or_live_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_prediction(tmp_path)
    prediction_snapshot = _snapshot(_evidence_root(tmp_path) / "runs") + _snapshot(
        _evidence_root(tmp_path) / "ledgers"
    )
    monkeypatch.chdir(tmp_path)

    with (
        patch("requests.Session.get", side_effect=AssertionError("live call attempted")) as mock_get,
        patch("os.getenv", side_effect=AssertionError("credential read attempted")) as mock_getenv,
    ):
        _write_closing(tmp_path, _observation(tmp_path))

    assert mock_get.call_count == 0
    assert mock_getenv.call_count == 0
    assert _snapshot(_evidence_root(tmp_path) / "runs") + _snapshot(
        _evidence_root(tmp_path) / "ledgers"
    ) == prediction_snapshot
    assert not (tmp_path / "outputs").exists()
    assert not (tmp_path / "test_outputs").exists()
    assert not (tmp_path / "data" / "history").exists()
    for row in [*_observation_rows(tmp_path), *_selection_rows(tmp_path)]:
        lowered = json.dumps(row, sort_keys=True).casefold()
        assert "settlement" not in lowered
        assert "kelly" not in lowered
        assert "bankroll" not in lowered
        assert "roi" not in lowered


def test_no_completed_segment_is_rewritten_on_replay(tmp_path: Path) -> None:
    _write_prediction(tmp_path)
    observation = _observation(tmp_path)
    result = _write_closing(tmp_path, observation)
    observation_segment = _snapshot(result.observation_segment_directory)
    selection_segment = _snapshot(result.selection_segment_directory)

    _write_closing(tmp_path, observation)

    assert _snapshot(result.observation_segment_directory) == observation_segment
    assert _snapshot(result.selection_segment_directory) == selection_segment


def test_fixture_file_is_not_mutated(tmp_path: Path) -> None:
    before = _sha256(ASSEMBLY_CASES_FIXTURE)

    _write_prediction(tmp_path)
    _write_closing(tmp_path, _observation(tmp_path))

    assert _sha256(ASSEMBLY_CASES_FIXTURE) == before


def test_no_mlb_or_production_imports_in_closing_module() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "courtvision"
        / "sports"
        / "nba"
        / "player_points_closing.py"
    ).read_text(encoding="utf-8").casefold()

    assert "sports.mlb" not in source
    assert "courtvision_ai" not in source
    assert "run_today" not in source
    assert "requests" not in source
    assert "os.getenv" not in source
    assert "os.environ" not in source
    assert "import kelly" not in source
    assert "kelly_" not in source
    assert "import bankroll" not in source
    assert "bankroll_" not in source
    assert "settle_nba_player_points_predictions" not in source
