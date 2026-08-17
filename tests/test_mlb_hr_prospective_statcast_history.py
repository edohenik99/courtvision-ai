from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from courtvision.sports.mlb.data import prospective_materialization_authority as authority
from courtvision.sports.mlb.data import prospective_statcast_history as history
from courtvision.sports.mlb.data.context_source_pack import collect_statcast_snapshot
from courtvision.sports.mlb.data.prospective_context_acquisition import (
    EvidenceRequest,
    ImmutableCaptureConflictError,
    ProspectiveAcquisitionError,
    ProviderResponse,
)


UTC = timezone.utc
OPERATING_DATE = date(2026, 8, 15)
SEASON_START = date(2026, 3, 25)
CUTOFF = datetime(2026, 8, 15, 19, 15, tzinfo=UTC)
OBSERVED = datetime(2026, 8, 15, 18, 30, tzinfo=UTC)
TARGETS = ("823184", "823588")
HITTERS = ("600001", "600002")
PITCHERS = ("500001", "500002")
HISTORICAL_GAME = "823100"
COMMIT = "4f48dc8d20c33177ee00dbe629da359e9ae35bd3"


class MockProvider:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def fetch(self, request: EvidenceRequest) -> ProviderResponse:
        self.calls.append(request.request_id)
        result = self.responses[request.request_id]
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, ProviderResponse)
        return result


def _response(
    body: bytes,
    *,
    status_code: int = 200,
    observed: datetime = OBSERVED,
    captured: datetime | None = None,
) -> ProviderResponse:
    return ProviderResponse(
        body=body,
        status_code=status_code,
        headers=MappingProxyType({"Content-Type": "application/octet-stream"}),
        first_observed_at_utc=observed,
        captured_at_utc=captured or observed + timedelta(seconds=1),
    )


def _schedule(*games: tuple[str, str, str]) -> bytes:
    payload = {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": int(game_id),
                        "gameDate": start,
                        "officialDate": start[:10],
                        "gameType": "R",
                        "season": 2026,
                        "status": {
                            "abstractGameState": state,
                            "detailedState": state,
                        },
                        "teams": {
                            "away": {"team": {"id": 115, "name": "Colorado Rockies"}},
                            "home": {
                                "team": {"id": 137, "name": "San Francisco Giants"}
                            },
                        },
                        "venue": {"id": 2395, "name": "Oracle Park"},
                    }
                    for game_id, start, state in games
                ]
            }
        ]
    }
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def _schedule_game(
    game_id: str,
    start: str,
    detailed_state: str,
    *,
    abstract_state: str | None = None,
    official_date: str | None = None,
    away_team_id: int = 115,
    home_team_id: int = 137,
    venue_id: int = 2395,
) -> dict[str, object]:
    is_final = detailed_state.casefold() in {"final", "game over", "completed early"}
    return {
        "gamePk": int(game_id),
        "gameGuid": f"guid-{game_id}",
        "gameDate": start,
        "officialDate": official_date or start[:10],
        "gameType": "R",
        "season": 2026,
        "status": {
            "abstractGameState": abstract_state or ("Final" if is_final else "Preview"),
            "detailedState": detailed_state,
            "codedGameState": "F" if is_final else "D",
            "statusCode": "F" if is_final else "DI",
        },
        "teams": {
            "away": {"team": {"id": away_team_id, "name": "Away Team"}},
            "home": {"team": {"id": home_team_id, "name": "Home Team"}},
        },
        "venue": {"id": venue_id, "name": "Test Park"},
    }


def _schedule_games(*games: dict[str, object]) -> bytes:
    return json.dumps(
        {"dates": [{"date": "2026-08-14", "games": list(games)}]},
        sort_keys=True,
    ).encode("utf-8")


def _resolved_schedule(
    *bodies: bytes,
) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    base = history.build_historical_statcast_requests(
        operating_date=OPERATING_DATE,
        season_start_date=SEASON_START,
        initial_chunk_days=365,
    )[0]
    sources = []
    for index, body in enumerate(bodies):
        request = EvidenceRequest(
            request_id=f"schedule-page-{index}",
            evidence_class=base.evidence_class,
            source_name=base.source_name,
            provider=base.provider,
            url=base.url,
        )
        digest = hashlib.sha256(body).hexdigest()
        record = {
            "sha256": digest,
            "captured_at_utc": "2026-08-15T18:30:00Z",
            "body_path": f"raw/{digest[:24]}.body",
        }
        sources.append((request, record, _response(body)))
    return history._resolve_schedule_responses(sources)


STATCAST_COLUMNS = (
    "game_pk",
    "game_date",
    "game_type",
    "at_bat_number",
    "pitch_number",
    "batter",
    "pitcher",
    "stand",
    "p_throws",
    "home_team",
    "away_team",
    "inning",
    "inning_topbot",
    "events",
    "description",
    "pitch_type",
    "release_speed",
    "launch_speed",
    "launch_angle",
    "bb_type",
    "barrel",
    "estimated_woba_using_speedangle",
    "estimated_slg_using_speedangle",
    "sv_id",
)


def _row(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "game_pk": HISTORICAL_GAME,
        "game_date": "2026-08-14",
        "game_type": "R",
        "at_bat_number": "1",
        "pitch_number": "1",
        "batter": HITTERS[0],
        "pitcher": PITCHERS[0],
        "stand": "R",
        "p_throws": "R",
        "home_team": "SF",
        "away_team": "COL",
        "inning": "1",
        "inning_topbot": "Top",
        "events": "home_run",
        "description": "hit_into_play",
        "pitch_type": "FF",
        "release_speed": "95.1",
        "launch_speed": "104.2",
        "launch_angle": "27",
        "bb_type": "fly_ball",
        "barrel": "1",
        "estimated_woba_using_speedangle": "",
        "estimated_slg_using_speedangle": "",
        "sv_id": "pitch-1",
    }
    values.update(updates)
    return values


def _statcast(*rows: dict[str, object]) -> bytes:
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=STATCAST_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows or (_row(),):
        writer.writerow({column: row.get(column, "") for column in STATCAST_COLUMNS})
    return handle.getvalue().encode("utf-8")


def _play_by_play(game_id: str, completion: str = "2026-08-14T23:00:00Z") -> bytes:
    return json.dumps(
        {
            "gamePk": int(game_id),
            "allPlays": [{"about": {"endTime": completion}}],
        },
        sort_keys=True,
    ).encode("utf-8")


def _base_request_ids() -> tuple[str, str]:
    requests = history.build_historical_statcast_requests(
        operating_date=OPERATING_DATE,
        season_start_date=SEASON_START,
        initial_chunk_days=365,
    )
    return requests[0].request_id, requests[1].request_id


def _provider(
    *,
    schedule: bytes | None = None,
    statcast: bytes | None = None,
    statcast_status: int = 200,
    statcast_observed: datetime = OBSERVED,
    statcast_captured: datetime | None = None,
    play_by_play: dict[str, object] | None = None,
) -> MockProvider:
    schedule_id, statcast_id = _base_request_ids()
    responses: dict[str, object] = {
        schedule_id: _response(
            schedule
            or _schedule((HISTORICAL_GAME, "2026-08-14T20:00:00Z", "Final"))
        ),
        statcast_id: _response(
            statcast or _statcast(_row()),
            status_code=statcast_status,
            observed=statcast_observed,
            captured=statcast_captured,
        ),
        f"statsapi-playbyplay-{HISTORICAL_GAME}": _response(
            _play_by_play(HISTORICAL_GAME)
        ),
    }
    responses.update(play_by_play or {})
    return MockProvider(responses)


def _acquire(
    tmp_path: Path,
    provider: MockProvider,
    **updates: object,
) -> history.HistoricalStatcastResult:
    arguments: dict[str, object] = {
        "operating_date": OPERATING_DATE,
        "season_start_date": SEASON_START,
        "requested_as_of_utc": CUTOFF,
        "target_game_ids": TARGETS,
        "eligible_hitter_ids": HITTERS,
        "probable_pitcher_ids": PITCHERS,
        "provider": provider,
        "acquisition_root": tmp_path / "acquisition",
        "git_commit": COMMIT,
        "initial_chunk_days": 365,
    }
    arguments.update(updates)
    return history.acquire_historical_statcast_snapshot(**arguments)  # type: ignore[arg-type]


def _manifest(result: history.HistoricalStatcastResult) -> dict[str, object]:
    return json.loads(result.manifest_path.read_text(encoding="utf-8"))


def test_postponed_then_final_same_game_pk_reconciles_with_full_provenance() -> None:
    postponed = _schedule_game(
        HISTORICAL_GAME,
        "2026-08-13T20:00:00Z",
        "Postponed",
        abstract_state="Final",
        official_date="2026-08-14",
    )
    final = _schedule_game(
        HISTORICAL_GAME,
        "2026-08-14T20:00:00Z",
        "Final",
        official_date="2026-08-14",
    )

    resolved, summary = _resolved_schedule(_schedule_games(postponed, final))
    game = resolved[HISTORICAL_GAME]

    assert game["observation_count"] == 2
    assert game["distinct_mutable_state_count"] == 2
    assert game["selected_canonical_state"]["detailed_state"] == "Final"
    assert game["selected_canonical_state"]["is_final"] is True
    assert all(item["source_response_digest"] for item in game["observed_states"])
    assert all(item["captured_at_utc"] for item in game["observed_states"])
    assert summary["reconciled_game_count"] == 1
    assert summary["identity_conflict_count"] == 0


def test_postponed_rescheduled_start_then_final_selects_final_revision() -> None:
    observations = (
        _schedule_game(
            HISTORICAL_GAME,
            "2026-08-13T20:00:00Z",
            "Postponed",
            abstract_state="Final",
            official_date="2026-08-14",
        ),
        _schedule_game(
            HISTORICAL_GAME,
            "2026-08-14T19:00:00Z",
            "Scheduled",
            official_date="2026-08-14",
        ),
        _schedule_game(
            HISTORICAL_GAME,
            "2026-08-14T19:00:00Z",
            "Final",
            official_date="2026-08-14",
        ),
    )

    resolved, summary = _resolved_schedule(_schedule_games(*observations))
    selected = resolved[HISTORICAL_GAME]["selected_canonical_state"]

    assert selected["detailed_state"] == "Final"
    assert selected["scheduled_start_utc"] == "2026-08-14T19:00:00Z"
    assert summary["revision_count"] == 2


def test_duplicate_identical_schedule_rows_are_preserved_without_revision() -> None:
    final = _schedule_game(HISTORICAL_GAME, "2026-08-14T20:00:00Z", "Final")

    resolved, summary = _resolved_schedule(_schedule_games(final, final))

    assert resolved[HISTORICAL_GAME]["observation_count"] == 2
    assert resolved[HISTORICAL_GAME]["distinct_mutable_state_count"] == 1
    assert summary["duplicate_observation_count"] == 1
    assert summary["revision_count"] == 0


@pytest.mark.parametrize(
    ("updated", "field"),
    (
        ({"away_team_id": 116}, "away_team_id"),
        ({"venue_id": 1}, "venue_id"),
    ),
)
def test_conflicting_schedule_identity_fails_closed(
    updated: dict[str, int], field: str
) -> None:
    first = _schedule_game(HISTORICAL_GAME, "2026-08-14T20:00:00Z", "Final")
    second = _schedule_game(
        HISTORICAL_GAME,
        "2026-08-14T20:00:00Z",
        "Final",
        **updated,
    )

    resolved, summary = _resolved_schedule(_schedule_games(first, second))

    assert HISTORICAL_GAME not in resolved
    assert summary["identity_conflict_count"] == 1
    assert field in summary["identity_conflicts"][0]["conflicting_fields"]


def test_final_row_wins_over_stale_postponed_row_across_schedule_responses() -> None:
    final = _schedule_games(
        _schedule_game(HISTORICAL_GAME, "2026-08-14T20:00:00Z", "Final")
    )
    stale = _schedule_games(
        _schedule_game(
            HISTORICAL_GAME,
            "2026-08-13T20:00:00Z",
            "Postponed",
            abstract_state="Final",
            official_date="2026-08-14",
        )
    )

    first, first_summary = _resolved_schedule(final, stale)
    second, second_summary = _resolved_schedule(stale, final)

    first_selected = first[HISTORICAL_GAME]["selected_canonical_state"]
    second_selected = second[HISTORICAL_GAME]["selected_canonical_state"]
    assert first_selected["detailed_state"] == second_selected["detailed_state"] == "Final"
    assert first_selected["scheduled_start_utc"] == second_selected[
        "scheduled_start_utc"
    ]
    assert first_summary["revision_count"] == second_summary["revision_count"] == 1
    assert len(first[HISTORICAL_GAME]["observed_states"]) == 2


def test_statcast_chunk_plan_has_exact_inclusive_boundary_coverage() -> None:
    chunks = history.plan_statcast_date_chunks(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 10),
        initial_chunk_days=3,
    )

    assert chunks == (
        (date(2026, 8, 1), date(2026, 8, 3)),
        (date(2026, 8, 4), date(2026, 8, 6)),
        (date(2026, 8, 7), date(2026, 8, 9)),
        (date(2026, 8, 10), date(2026, 8, 10)),
    )


def _two_day_chunk_provider(*, parent_status: int = 200, omit_second: bool = False) -> MockProvider:
    short_start = date(2026, 8, 13)
    requests = history.build_historical_statcast_requests(
        operating_date=OPERATING_DATE,
        season_start_date=short_start,
        initial_chunk_days=2,
    )
    parent = requests[1]
    first_child = history._statcast_request(short_start, short_start)
    second_child = history._statcast_request(date(2026, 8, 14), date(2026, 8, 14))
    first_game = HISTORICAL_GAME
    second_game = "823101"
    first_row = _row(
        game_pk=first_game,
        game_date="2026-08-13",
        sv_id="pitch-first",
    )
    second_row = _row(
        game_pk=second_game,
        game_date="2026-08-14",
        sv_id="pitch-second",
    )
    parent_rows = (first_row,) if omit_second else (first_row, second_row)
    return MockProvider(
        {
            requests[0].request_id: _response(
                _schedule_games(
                    _schedule_game(
                        first_game,
                        "2026-08-13T20:00:00Z",
                        "Final",
                        official_date="2026-08-13",
                    ),
                    _schedule_game(
                        second_game,
                        "2026-08-14T20:00:00Z",
                        "Final",
                        official_date="2026-08-14",
                    ),
                )
            ),
            parent.request_id: _response(
                _statcast(*parent_rows), status_code=parent_status
            ),
            first_child.request_id: _response(_statcast(first_row)),
            second_child.request_id: _response(_statcast(second_row)),
            f"statsapi-playbyplay-{first_game}": _response(
                _play_by_play(first_game, "2026-08-13T23:00:00Z")
            ),
            f"statsapi-playbyplay-{second_game}": _response(
                _play_by_play(second_game, "2026-08-14T23:00:00Z")
            ),
        }
    )


@pytest.mark.parametrize(
    ("parent_status", "omit_second", "reason"),
    (
        (206, False, "provider_partial_response"),
        (200, True, "missing_completed_schedule_games"),
    ),
)
def test_suspicious_statcast_chunk_splits_and_only_complete_children_merge(
    tmp_path: Path,
    parent_status: int,
    omit_second: bool,
    reason: str,
) -> None:
    provider = _two_day_chunk_provider(
        parent_status=parent_status, omit_second=omit_second
    )

    result = _acquire(
        tmp_path,
        provider,
        season_start_date=date(2026, 8, 13),
        initial_chunk_days=2,
    )
    manifest = _manifest(result)

    assert result.snapshot_state == "completed"
    assert result.pitch_count == 2
    assert manifest["coverage"]["provider_statcast_request_count"] == 3
    assert manifest["coverage"]["accepted_statcast_chunk_count"] == 2
    assert manifest["coverage"]["split_statcast_chunk_count"] == 1
    assert manifest["coverage"]["requested_date_coverage_gaps"] == []
    split = next(
        item for item in manifest["statcast_requested_ranges"] if item["chunk_status"] == "split"
    )
    split_manifest = next(
        item
        for item in manifest["statcast_chunk_manifests"]
        if item["request_id"] == split["request_id"]
    )
    payload = json.loads(
        (result.snapshot_dir / split_manifest["path"]).read_text(encoding="utf-8")
    )
    assert reason in payload["suspicious_reasons"]


def _overlapping_requests() -> tuple[EvidenceRequest, EvidenceRequest, EvidenceRequest]:
    schedule = history.build_historical_statcast_requests(
        operating_date=OPERATING_DATE,
        season_start_date=date(2026, 8, 13),
        initial_chunk_days=2,
    )[0]
    return (
        schedule,
        history._statcast_request(date(2026, 8, 13), date(2026, 8, 14)),
        history._statcast_request(date(2026, 8, 14), date(2026, 8, 14)),
    )


def _overlap_provider(
    requests: tuple[EvidenceRequest, EvidenceRequest, EvidenceRequest],
    *,
    conflicting_second: bool = False,
) -> MockProvider:
    row = _row(game_date="2026-08-14", sv_id="stable-overlap")
    second = dict(row)
    if conflicting_second:
        second["release_speed"] = "99.9"
    return MockProvider(
        {
            requests[0].request_id: _response(
                _schedule_games(
                    _schedule_game(
                        HISTORICAL_GAME,
                        "2026-08-14T20:00:00Z",
                        "Final",
                    )
                )
            ),
            requests[1].request_id: _response(_statcast(row)),
            requests[2].request_id: _response(_statcast(second)),
            f"statsapi-playbyplay-{HISTORICAL_GAME}": _response(
                _play_by_play(HISTORICAL_GAME)
            ),
        }
    )


def test_overlapping_chunks_deduplicate_identical_stable_pitch_identity(
    tmp_path: Path,
) -> None:
    requests = _overlapping_requests()
    result = _acquire(
        tmp_path,
        _overlap_provider(requests),
        season_start_date=date(2026, 8, 13),
        base_requests=requests,
    )
    manifest = _manifest(result)

    assert result.snapshot_state == "completed"
    assert result.pitch_count == 1
    assert manifest["coverage"]["raw_row_count_before_dedupe"] == 2
    assert manifest["coverage"]["row_count_after_dedupe"] == 1
    assert manifest["coverage"]["duplicate_row_count"] == 1
    assert manifest["coverage"]["overlap_day_count"] == 1


def test_conflicting_duplicate_pitch_identity_rejects_before_play_by_play(
    tmp_path: Path,
) -> None:
    requests = _overlapping_requests()
    provider = _overlap_provider(requests, conflicting_second=True)

    result = _acquire(
        tmp_path,
        provider,
        season_start_date=date(2026, 8, 13),
        base_requests=requests,
    )

    assert result.snapshot_state == "rejected"
    assert "conflicting duplicate pitch identity" in " ".join(
        _manifest(result)["integrity_errors"]
    )
    assert not any(call.startswith("statsapi-playbyplay") for call in provider.calls)


def test_missing_chunk_provider_failure_is_accounted_and_blocks_merge(
    tmp_path: Path,
) -> None:
    short_start = date(2026, 8, 13)
    requests = history.build_historical_statcast_requests(
        operating_date=OPERATING_DATE,
        season_start_date=short_start,
        initial_chunk_days=1,
    )
    row = _row(game_date="2026-08-14")
    provider = MockProvider(
        {
            requests[0].request_id: _response(
                _schedule_games(
                    _schedule_game(
                        HISTORICAL_GAME,
                        "2026-08-14T20:00:00Z",
                        "Final",
                    )
                )
            ),
            requests[1].request_id: TimeoutError("missing first chunk"),
            requests[2].request_id: _response(_statcast(row)),
        }
    )

    result = _acquire(
        tmp_path,
        provider,
        season_start_date=short_start,
        initial_chunk_days=1,
    )
    manifest = _manifest(result)

    assert result.snapshot_state == "partial"
    assert result.pitch_count == 0
    assert manifest["coverage"]["failed_statcast_leaf_count"] == 1
    assert manifest["coverage"]["requested_date_coverage_gaps"] == [
        {"start_date": "2026-08-13", "end_date": "2026-08-13"}
    ]
    assert manifest["every_provider_request_accounted"] is True
    assert not any(call.startswith("statsapi-playbyplay") for call in provider.calls)


def test_one_day_partial_chunk_is_preserved_but_never_merged(tmp_path: Path) -> None:
    short_start = date(2026, 8, 14)
    requests = history.build_historical_statcast_requests(
        operating_date=OPERATING_DATE,
        season_start_date=short_start,
        initial_chunk_days=1,
    )
    provider = MockProvider(
        {
            requests[0].request_id: _response(
                _schedule_games(
                    _schedule_game(
                        HISTORICAL_GAME,
                        "2026-08-14T20:00:00Z",
                        "Final",
                    )
                )
            ),
            requests[1].request_id: _response(_statcast(_row()), status_code=206),
        }
    )

    result = _acquire(
        tmp_path,
        provider,
        season_start_date=short_start,
        initial_chunk_days=1,
    )
    manifest = _manifest(result)

    assert result.snapshot_state == "partial"
    assert manifest["coverage"]["accepted_statcast_chunk_count"] == 0
    chunk = next(
        item
        for item in manifest["provider_requests"]
        if item["endpoint_class"] == "statcast_pitch_history"
    )
    assert chunk["availability_status"] == "partial"
    assert (result.snapshot_dir / chunk["body_path"]).is_file()


def test_chunk_response_date_outside_request_is_rejected(tmp_path: Path) -> None:
    short_start = date(2026, 8, 14)
    requests = history.build_historical_statcast_requests(
        operating_date=OPERATING_DATE,
        season_start_date=short_start,
        initial_chunk_days=1,
    )
    provider = MockProvider(
        {
            requests[0].request_id: _response(
                _schedule_games(
                    _schedule_game(
                        HISTORICAL_GAME,
                        "2026-08-14T20:00:00Z",
                        "Final",
                    )
                )
            ),
            requests[1].request_id: _response(
                _statcast(_row(game_date="2026-08-13"))
            ),
        }
    )

    result = _acquire(
        tmp_path,
        provider,
        season_start_date=short_start,
        initial_chunk_days=1,
    )

    assert result.snapshot_state == "rejected"
    assert "outside 2026-08-14..2026-08-14" in " ".join(
        _manifest(result)["integrity_errors"]
    )


def test_rejected_snapshot_is_immutable_and_not_reused_by_retry(tmp_path: Path) -> None:
    requests = _overlapping_requests()
    rejected = _acquire(
        tmp_path,
        _overlap_provider(requests, conflicting_second=True),
        season_start_date=date(2026, 8, 13),
        base_requests=requests,
    )
    rejected_bytes = rejected.manifest_path.read_bytes()
    retry_provider = _overlap_provider(requests)

    completed = _acquire(
        tmp_path,
        retry_provider,
        season_start_date=date(2026, 8, 13),
        base_requests=requests,
    )

    assert rejected.snapshot_state == "rejected"
    assert completed.snapshot_state == "completed"
    assert completed.snapshot_id != rejected.snapshot_id
    assert completed.no_op is False
    assert retry_provider.calls
    assert rejected.manifest_path.read_bytes() == rejected_bytes


def test_daily_statcast_snapshot_is_content_addressed_complete_and_accounted(
    tmp_path: Path,
) -> None:
    provider = _provider()

    result = _acquire(tmp_path, provider)
    manifest = _manifest(result)

    assert result.snapshot_state == "completed"
    assert result.game_count == 1
    assert result.pitch_count == 1
    assert result.plate_appearance_count == 1
    assert result.snapshot_id == "statcast-history-" + manifest["content_digest"]
    assert manifest["raw_artifact_digest"]
    assert manifest["manifest_digest"] == result.manifest_digest
    assert manifest["included_game_ids"] == [HISTORICAL_GAME]
    assert manifest["included_hitter_ids"] == [HITTERS[0]]
    assert manifest["included_pitcher_ids"] == [PITCHERS[0]]
    assert manifest["eligible_hitter_ids_without_history"] == [HITTERS[1]]
    assert manifest["probable_pitcher_ids_without_history"] == [PITCHERS[1]]
    assert manifest["every_provider_request_accounted"] is True
    assert manifest["provider_call_count"] == 2
    assert f"statsapi-playbyplay-{HISTORICAL_GAME}" not in provider.calls
    assert manifest["coverage"]["completion_witness_counts_by_source_type"] == {
        "schedule_final_observation": 1
    }
    assert result.completion_witness_counts_by_source_type == {
        "schedule_final_observation": 1
    }
    for record in manifest["provider_requests"]:
        assert record["endpoint_class"]
        assert record["request_started_at_utc"]
        assert record["request_completed_at_utc"]
        assert record["request_result"] == "response_preserved"
        assert record["raw_persistence_status"] == "completed"
        assert (result.snapshot_dir / record["body_path"]).is_file()


def test_current_target_game_is_excluded_without_completion_request(tmp_path: Path) -> None:
    target_row = _row(
        game_pk=TARGETS[0], game_date="2026-08-14", sv_id="target"
    )
    provider = _provider(
        schedule=_schedule(
            (HISTORICAL_GAME, "2026-08-14T20:00:00Z", "Final"),
            (TARGETS[0], "2026-08-15T20:05:00Z", "Preview"),
        ),
        statcast=_statcast(_row(), target_row),
    )

    result = _acquire(tmp_path, provider)

    assert _manifest(result)["evidence_rejection_counts"]["target_game"] == 1
    assert f"statsapi-playbyplay-{TARGETS[0]}" not in provider.calls
    assert result.pitch_count == 1


def test_future_game_is_excluded_without_completion_request(tmp_path: Path) -> None:
    future_id = "823900"
    provider = _provider(
        schedule=_schedule(
            (future_id, "2026-08-15T21:00:00Z", "Preview"),
        ),
        statcast=_statcast(
            _row(game_pk=future_id, game_date="2026-08-14", sv_id="future")
        ),
    )

    result = _acquire(tmp_path, provider)

    assert _manifest(result)["evidence_rejection_counts"]["future_game"] == 1
    assert f"statsapi-playbyplay-{future_id}" not in provider.calls
    assert result.pitch_count == 0


def test_in_progress_game_is_excluded_without_completion_request(tmp_path: Path) -> None:
    live_id = "823901"
    provider = _provider(
        schedule=_schedule((live_id, "2026-08-15T17:00:00Z", "Live")),
        statcast=_statcast(
            _row(game_pk=live_id, game_date="2026-08-14", sv_id="live")
        ),
    )

    result = _acquire(tmp_path, provider)

    assert _manifest(result)["evidence_rejection_counts"]["in_progress_game"] == 1
    assert f"statsapi-playbyplay-{live_id}" not in provider.calls
    assert result.pitch_count == 0


def test_ambiguous_historical_game_with_pbp_completion_after_cutoff_is_excluded(
    tmp_path: Path,
) -> None:
    provider = _provider(
        schedule=_schedule(
            (
                HISTORICAL_GAME,
                "2026-08-14T20:00:00Z",
                "Suspended",
            )
        ),
        play_by_play={
            f"statsapi-playbyplay-{HISTORICAL_GAME}": _response(
                _play_by_play(
                    HISTORICAL_GAME,
                    "2026-08-15T19:16:00Z",
                )
            )
        },
    )

    result = _acquire(tmp_path, provider)
    manifest = _manifest(result)

    assert manifest["evidence_rejection_counts"]["completed_after_cutoff"] == 1
    assert result.pitch_count == 0

    completion_request = next(
        record
        for record in manifest["provider_requests"]
        if record["endpoint_class"] == "mlb_game_play_by_play"
    )

    assert completion_request["evidence_admissibility"] == "rejected"
    assert (
        f"statsapi-playbyplay-{HISTORICAL_GAME}"
        in provider.calls
    )

def test_historical_snapshot_captured_after_cutoff_is_not_usable(tmp_path: Path) -> None:
    late = CUTOFF + timedelta(seconds=1)
    provider = _provider(statcast_observed=late, statcast_captured=late)

    result = _acquire(tmp_path, provider)
    manifest = _manifest(result)

    assert result.snapshot_state == "partial"
    statcast = next(
        item
        for item in manifest["provider_requests"]
        if item["endpoint_class"] == "statcast_pitch_history"
    )
    assert statcast["availability_status"] == "rejected"
    with pytest.raises(ProspectiveAcquisitionError, match="not complete"):
        history.load_historical_statcast_snapshot(
            result.manifest_path,
            cutoff_utc=CUTOFF,
            target_game_ids=TARGETS,
            eligible_hitter_ids=HITTERS,
            probable_pitcher_ids=PITCHERS,
        )


@pytest.mark.parametrize(
    ("field", "message"),
    (("batter", "hitter identity mismatch"), ("pitcher", "pitcher identity mismatch")),
)
def test_player_identity_mismatch_rejects_snapshot(
    tmp_path: Path, field: str, message: str
) -> None:
    provider = _provider(statcast=_statcast(_row(**{field: "not-an-id"})))

    result = _acquire(tmp_path, provider)

    assert result.snapshot_state == "rejected"
    assert message in " ".join(_manifest(result)["integrity_errors"])


def test_probable_pitcher_with_no_history_is_explicit(tmp_path: Path) -> None:
    provider = _provider(statcast=_statcast(_row(pitcher="500099")))

    result = _acquire(tmp_path, provider)
    manifest = _manifest(result)

    assert result.snapshot_state == "completed"
    assert manifest["included_pitcher_ids"] == []
    assert manifest["probable_pitcher_ids_without_history"] == list(PITCHERS)


def test_duplicate_request_reuses_snapshot_without_provider_calls(tmp_path: Path) -> None:
    first_provider = _provider()
    first = _acquire(tmp_path, first_provider)
    second_provider = _provider()

    second = _acquire(tmp_path, second_provider)

    assert second.snapshot_id == first.snapshot_id
    assert second.no_op is True
    assert second.provider_call_count == 0
    assert second_provider.calls == []


def test_conflicting_or_tampered_immutable_snapshot_fails_closed(tmp_path: Path) -> None:
    result = _acquire(tmp_path, _provider())
    payload = _manifest(result)
    payload["coverage"]["pitch_count"] = 999
    result.manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ImmutableCaptureConflictError, match="digest mismatch"):
        _acquire(tmp_path, _provider())


def test_partial_statcast_response_is_preserved_and_not_materializable(
    tmp_path: Path,
) -> None:
    result = _acquire(tmp_path, _provider(statcast_status=206))
    manifest = _manifest(result)

    assert result.snapshot_state == "partial"
    statcast = next(
        item
        for item in manifest["provider_requests"]
        if item["endpoint_class"] == "statcast_pitch_history"
    )
    assert statcast["availability_status"] == "partial"
    assert (result.snapshot_dir / statcast["body_path"]).is_file()


def test_provider_timeout_is_explicitly_accounted(tmp_path: Path) -> None:
    schedule_id, statcast_id = _base_request_ids()
    provider = MockProvider(
        {
            schedule_id: _response(
                _schedule((HISTORICAL_GAME, "2026-08-14T20:00:00Z", "Final"))
            ),
            statcast_id: TimeoutError("timeout"),
        }
    )

    result = _acquire(tmp_path, provider)
    manifest = _manifest(result)
    failed = next(item for item in manifest["provider_requests"] if item["status_code"] is None)

    assert result.snapshot_state == "partial"
    assert failed["request_result"] == "provider_error"
    assert failed["error_type"] == "TimeoutError"
    assert manifest["every_provider_request_accounted"] is True


def test_control_request_accounting_binds_status_reason_and_evidence_manifest(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text('{"capture":"complete"}\n', encoding="utf-8")
    record = {
        "request_id": "schedule-preflight",
        "endpoint_class": "mlb_schedule",
        "provider": "mlb_statsapi",
        "url": "https://statsapi.mlb.com/api/v1/schedule",
        "started_at_utc": "2026-08-15T18:00:00Z",
        "completed_at_utc": "2026-08-15T18:00:01Z",
        "status": "completed",
        "status_code": 200,
        "reason": "selected cluster timing",
        "admissibility": "non_evidentiary_control",
    }

    path = history.persist_request_accounting_manifest(
        operating_date=OPERATING_DATE,
        records=(record,),
        outcome="stopped_before_evidence_materialization",
        reason="cluster was early",
        acquisition_root=tmp_path / "acquisition",
        git_commit=COMMIT,
        evidence_manifest_paths=(evidence,),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["every_provider_request_accounted"] is True
    assert payload["requests"] == [record]
    assert payload["evidence_manifests"] == [
        {
            "path": str(evidence.resolve()),
            "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            "byte_size": evidence.stat().st_size,
        }
    ]


def test_response_body_write_failure_is_surfaced_in_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_body_write(path: Path, payload: bytes) -> None:
        if path.suffix == ".body":
            raise PermissionError("denied")
        path.write_bytes(payload)

    monkeypatch.setattr(history, "_write_raw_file", fail_body_write)

    result = _acquire(tmp_path, _provider())
    manifest = _manifest(result)

    assert result.snapshot_state == "unavailable"
    assert all(
        item["request_result"] == "response_persistence_failed"
        for item in manifest["provider_requests"]
    )
    assert all(
        "persistence failure" in item["availability_note"]
        for item in manifest["provider_requests"]
    )


def test_same_daily_snapshot_can_be_loaded_for_multiple_target_games(
    tmp_path: Path,
) -> None:
    result = _acquire(tmp_path, _provider())

    first = history.load_historical_statcast_snapshot(
        result.manifest_path,
        cutoff_utc=CUTOFF,
        target_game_ids=(TARGETS[0],),
        eligible_hitter_ids=(HITTERS[0],),
        probable_pitcher_ids=(PITCHERS[0],),
    )
    second = history.load_historical_statcast_snapshot(
        result.manifest_path,
        cutoff_utc=CUTOFF,
        target_game_ids=(TARGETS[1],),
        eligible_hitter_ids=(HITTERS[1],),
        probable_pitcher_ids=(PITCHERS[1],),
    )

    assert first.manifest_path == second.manifest_path == result.manifest_path
    assert first.manifest["manifest_digest"] == second.manifest["manifest_digest"]


def test_no_derived_feature_evidence_timestamp_exceeds_cutoff(tmp_path: Path) -> None:
    result = _acquire(tmp_path, _provider())
    loaded = history.load_historical_statcast_snapshot(
        result.manifest_path,
        cutoff_utc=CUTOFF,
        target_game_ids=TARGETS,
        eligible_hitter_ids=HITTERS,
        probable_pitcher_ids=PITCHERS,
    )
    with loaded.game_clock_csv_path.open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))

    assert rows
    assert all(
        datetime.fromisoformat(row["captured_at_utc"].replace("Z", "+00:00"))
        <= CUTOFF
        for row in rows
    )
    assert all(row["game_id"] not in TARGETS for row in rows)


def test_historical_artifact_collects_through_existing_source_pack_adapter(
    tmp_path: Path,
) -> None:
    result = _acquire(tmp_path, _provider())
    loaded = history.load_historical_statcast_snapshot(
        result.manifest_path,
        cutoff_utc=CUTOFF,
        target_game_ids=TARGETS,
        eligible_hitter_ids=HITTERS,
        probable_pitcher_ids=PITCHERS,
    )

    snapshot = collect_statcast_snapshot(
        loaded.statcast_csv_path,
        loaded.game_clock_csv_path,
        operating_date=OPERATING_DATE,
        cutoff_utc=CUTOFF,
        captured_at_utc=loaded.manifest["captured_at_utc"],
        git_commit=COMMIT,
        research_root=tmp_path / "source-packs",
        additional_raw_inputs={"historical_statcast_manifest": loaded.manifest_path},
    )
    with snapshot.data_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert snapshot.row_count == 1
    assert rows[0]["batter_id"] == HITTERS[0]
    assert rows[0]["pitcher_id"] == PITCHERS[0]
    assert rows[0]["game_id"] == HISTORICAL_GAME
    assert rows[0]["captured_at_utc"] == "2026-08-15T18:30:01Z"
    assert rows[0]["game_completed_at_utc"] == ""
    assert rows[0]["completion_evidence_type"] == "schedule_final_observation"
    assert rows[0]["completion_witnessed_at_utc"]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _materialization(
    root: Path, materialization_id: str, event_ids: tuple[str, ...] = TARGETS
) -> Path:
    directory = root / materialization_id.removeprefix("materialization-")
    directory.mkdir(parents=True)
    schedule = directory / "schedule.csv"
    with schedule.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("event_id",), lineterminator="\n")
        writer.writeheader()
        for event_id in event_ids:
            writer.writerow({"event_id": event_id})
    payload: dict[str, object] = {
        "schema_version": "mlb-hr-context-materialization-v6",
        "materialization_id": materialization_id,
        "operating_date": OPERATING_DATE.isoformat(),
    }
    payload["manifest_digest"] = hashlib.sha256(_canonical(payload)).hexdigest()
    manifest = directory / "materialization_manifest_v1.json"
    manifest.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


def test_authority_record_identifies_authoritative_and_superseded_artifacts(
    tmp_path: Path,
) -> None:
    authoritative = _materialization(tmp_path / "mats", "materialization-authoritative")
    superseded = _materialization(tmp_path / "mats", "materialization-superseded")

    published = authority.publish_materialization_authority(
        operating_date=OPERATING_DATE,
        event_ids=TARGETS,
        authoritative_manifest_path=authoritative,
        superseded_manifest_paths=(superseded,),
        authority_root=tmp_path / "authority",
    )
    resolved = authority.resolve_materialization_authority(
        operating_date=OPERATING_DATE,
        event_ids=reversed(TARGETS),
        authority_root=tmp_path / "authority",
    )

    assert resolved.authoritative_materialization_id == "materialization-authoritative"
    assert resolved.superseded_materialization_ids == ("materialization-superseded",)
    assert published.authority_path.read_bytes() == resolved.authority_path.read_bytes()


def test_conflicting_authority_record_fails_closed(tmp_path: Path) -> None:
    first = _materialization(tmp_path / "mats", "materialization-first")
    second = _materialization(tmp_path / "mats", "materialization-second")
    authority.publish_materialization_authority(
        operating_date=OPERATING_DATE,
        event_ids=TARGETS,
        authoritative_manifest_path=first,
        authority_root=tmp_path / "authority",
    )

    with pytest.raises(ImmutableCaptureConflictError, match="conflicting authority"):
        authority.publish_materialization_authority(
            operating_date=OPERATING_DATE,
            event_ids=TARGETS,
            authoritative_manifest_path=second,
            authority_root=tmp_path / "authority",
        )

# ============================================================================
# Resumable immutable historical-Statcast recovery tests.
#
# These tests are deliberately provider-mocked and offline. They prove that
# previously accepted leaves remain reusable immutable evidence and that only
# declared failed/missing Statcast ranges can reach the underlying provider.
# ============================================================================

RECOVERY_SEASON_START = date(2026, 8, 13)
RECOVERY_GAME_A = "823099"
RECOVERY_GAME_B = HISTORICAL_GAME


def _recovery_requests() -> tuple[EvidenceRequest, EvidenceRequest, EvidenceRequest]:
    requests = history.build_historical_statcast_requests(
        operating_date=OPERATING_DATE,
        season_start_date=RECOVERY_SEASON_START,
        initial_chunk_days=1,
    )

    assert len(requests) == 3

    schedule_request, first_leaf, second_leaf = requests

    assert schedule_request.source_name == "mlb_completed_game_schedule"
    assert first_leaf.request_id == (
        "baseball-savant-statcast-2026-08-13-2026-08-13"
    )
    assert second_leaf.request_id == (
        "baseball-savant-statcast-2026-08-14-2026-08-14"
    )

    return schedule_request, first_leaf, second_leaf


def _empty_statcast() -> bytes:
    buffer = io.StringIO()

    writer = csv.DictWriter(
        buffer,
        fieldnames=STATCAST_COLUMNS,
        lineterminator="\n",
    )
    writer.writeheader()

    return buffer.getvalue().encode("utf-8")


def _make_recovery_partial(
    tmp_path: Path,
) -> tuple[
    history.HistoricalStatcastResult,
    tuple[EvidenceRequest, EvidenceRequest, EvidenceRequest],
    MockProvider,
]:
    schedule_request, first_leaf, second_leaf = _recovery_requests()

    provider = MockProvider(
        {
            schedule_request.request_id: _response(
                _schedule(
                    (
                        RECOVERY_GAME_A,
                        "2026-08-13T20:00:00Z",
                        "Final",
                    ),
                    (
                        RECOVERY_GAME_B,
                        "2026-08-14T20:00:00Z",
                        "Final",
                    ),
                )
            ),
            first_leaf.request_id: _response(
                _statcast(
                    _row(
                        game_pk=RECOVERY_GAME_A,
                        game_date="2026-08-13",
                        sv_id="reused-leaf",
                    )
                )
            ),
            second_leaf.request_id: TimeoutError(
                "synthetic failed Statcast leaf"
            ),
        }
    )

    result = _acquire(
        tmp_path,
        provider,
        season_start_date=RECOVERY_SEASON_START,
        initial_chunk_days=1,
    )

    assert result.snapshot_state == "partial"

    manifest = _manifest(result)

    accepted = {
        str(item["request_id"])
        for item in manifest["accepted_statcast_chunk_manifests"]
    }

    assert accepted == {first_leaf.request_id}

    ranges = {
        str(item["request_id"]): str(item["chunk_status"])
        for item in manifest["statcast_requested_ranges"]
        if str(item.get("chunk_status")) != "split"
    }

    assert ranges[first_leaf.request_id] == "accepted"
    assert ranges[second_leaf.request_id] != "accepted"

    return (
        result,
        (schedule_request, first_leaf, second_leaf),
        provider,
    )


def _recovery_observation_time() -> datetime:
    return CUTOFF + timedelta(minutes=1)


def _recovery_cutoff() -> datetime:
    return CUTOFF + timedelta(minutes=5)


def _utc_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def test_recovery_completed_snapshot_with_later_cutoff_is_no_op(
    tmp_path: Path,
) -> None:
    completed = _acquire(tmp_path, _provider())

    assert completed.snapshot_state == "completed"

    original_manifest = completed.manifest_path.read_bytes()

    provider = MockProvider({})

    recovered = _acquire(
        tmp_path,
        provider,
        requested_as_of_utc=_recovery_cutoff(),
        resume_from_snapshot=completed.snapshot_dir,
    )

    assert recovered.no_op is True
    assert recovered.snapshot_state == "completed"
    assert recovered.snapshot_id == completed.snapshot_id
    assert recovered.manifest_path == completed.manifest_path
    assert recovered.manifest_digest == completed.manifest_digest
    assert recovered.provider_call_count == 0
    assert recovered.reused_chunk_count == 0
    assert recovered.recovered_chunk_count == 0
    assert provider.calls == []
    assert completed.manifest_path.read_bytes() == original_manifest


def test_recovery_completed_snapshot_rejects_non_time_identity_change_before_provider(
    tmp_path: Path,
) -> None:
    completed = _acquire(tmp_path, _provider())

    provider = MockProvider({})

    with pytest.raises(
        ImmutableCaptureConflictError,
        match="request identity conflicts with recovery",
    ):
        _acquire(
            tmp_path,
            provider,
            requested_as_of_utc=_recovery_cutoff(),
            eligible_hitter_ids=(HITTERS[0], "600999"),
            resume_from_snapshot=completed.snapshot_dir,
        )

    assert provider.calls == []


def test_recovery_completed_snapshot_rejects_earlier_cutoff_before_provider(
    tmp_path: Path,
) -> None:
    completed = _acquire(tmp_path, _provider())

    provider = MockProvider({})

    with pytest.raises(
        ImmutableCaptureConflictError,
        match="cannot precede the prior snapshot cutoff",
    ):
        _acquire(
            tmp_path,
            provider,
            requested_as_of_utc=CUTOFF - timedelta(seconds=1),
            resume_from_snapshot=completed.snapshot_dir,
        )

    assert provider.calls == []


def test_recovery_rejected_state_refuses_before_any_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A separate existing test already proves that a genuinely rejected
    # snapshot is immutable and is not reused as a normal completed result.
    # This isolates the resume gate itself and proves it fails before provider
    # access or request-identity parsing.
    provider = MockProvider({})
    rejected_path = tmp_path / "synthetic-rejected-snapshot"

    def validate_as_rejected(path: Path) -> dict[str, object]:
        assert Path(path).resolve() == rejected_path.resolve()
        return {"snapshot_state": "rejected"}

    monkeypatch.setattr(
        history,
        "_validate_snapshot_dir",
        validate_as_rejected,
    )

    with pytest.raises(
        ProspectiveAcquisitionError,
        match="prior rejected Statcast snapshot cannot be resumed",
    ):
        history._prepare_recovery(
            rejected_path,
            request_identity={},
            request_identity_digest="",
            requested_as_of=CUTOFF,
            provider=provider,
        )

    assert provider.calls == []


def test_recovery_reuses_successful_leaf_and_calls_only_failed_leaf(
    tmp_path: Path,
) -> None:
    partial, requests, initial_provider = _make_recovery_partial(
        tmp_path
    )

    schedule_request, first_leaf, failed_leaf = requests

    prior_manifest_bytes = partial.manifest_path.read_bytes()
    prior_manifest = _manifest(partial)

    assert schedule_request.request_id in initial_provider.calls
    assert first_leaf.request_id in initial_provider.calls
    assert failed_leaf.request_id in initial_provider.calls

    observed = _recovery_observation_time()

    recovery_provider = MockProvider(
        {
            failed_leaf.request_id: _response(
                _statcast(
                    _row(
                        game_pk=RECOVERY_GAME_B,
                        game_date="2026-08-14",
                        sv_id="recovered-leaf",
                    )
                ),
                observed=observed,
                captured=observed + timedelta(seconds=1),
            ),
        }
    )

    result = _acquire(
        tmp_path,
        recovery_provider,
        season_start_date=RECOVERY_SEASON_START,
        initial_chunk_days=1,
        requested_as_of_utc=_recovery_cutoff(),
        resume_from_snapshot=partial.snapshot_dir,
    )

    assert result.snapshot_state == "completed"
    assert result.no_op is False

    # This is the central safety property:
    # the underlying provider receives ONLY the failed leaf.
    assert recovery_provider.calls == [failed_leaf.request_id]

    assert schedule_request.request_id not in recovery_provider.calls
    assert first_leaf.request_id not in recovery_provider.calls

    assert result.provider_call_count == 1
    assert result.reused_chunk_count == 1
    assert result.recovered_chunk_count == 1

    manifest = _manifest(result)

    assert manifest["provider_call_count"] == 1
    assert manifest["reused_prior_response_count"] == 2
    assert manifest["every_provider_request_accounted"] is True

    execution_by_request = {
        str(item["request_id"]): str(item["request_execution"])
        for item in manifest["provider_requests"]
    }

    assert execution_by_request[schedule_request.request_id] == (
        "reused_prior_response"
    )
    assert execution_by_request[first_leaf.request_id] == (
        "reused_prior_response"
    )
    assert execution_by_request[failed_leaf.request_id] == (
        "provider_call"
    )

    provenance = manifest["recovery_provenance"]

    assert provenance["schema_version"] == (
        "mlb-hr-statcast-recovery-v1"
    )
    assert provenance["prior_partial_snapshot_id"] == (
        prior_manifest["snapshot_id"]
    )
    assert provenance["prior_partial_manifest_digest"] == (
        prior_manifest["manifest_digest"]
    )
    assert provenance["reused_successful_leaf_count"] == 1

    assert provenance["failed_or_missing_leaf_ranges"] == [
        {
            "start_date": "2026-08-14",
            "end_date": "2026-08-14",
            "request_id": failed_leaf.request_id,
        }
    ]

    # Recovery must never mutate the prior partial evidence.
    assert partial.manifest_path.read_bytes() == prior_manifest_bytes


def test_recovery_zero_row_retry_remains_partial_and_reuses_good_leaf(
    tmp_path: Path,
) -> None:
    partial, requests, _ = _make_recovery_partial(tmp_path)

    schedule_request, first_leaf, failed_leaf = requests

    prior_manifest_bytes = partial.manifest_path.read_bytes()

    observed = _recovery_observation_time()

    recovery_provider = MockProvider(
        {
            failed_leaf.request_id: _response(
                _empty_statcast(),
                observed=observed,
                captured=observed + timedelta(seconds=1),
            ),
        }
    )

    result = _acquire(
        tmp_path,
        recovery_provider,
        season_start_date=RECOVERY_SEASON_START,
        initial_chunk_days=1,
        requested_as_of_utc=_recovery_cutoff(),
        resume_from_snapshot=partial.snapshot_dir,
    )

    assert result.snapshot_state == "partial"
    assert result.no_op is False

    assert recovery_provider.calls == [failed_leaf.request_id]
    assert schedule_request.request_id not in recovery_provider.calls
    assert first_leaf.request_id not in recovery_provider.calls

    assert result.provider_call_count == 1
    assert result.reused_chunk_count == 1
    assert result.recovered_chunk_count == 0

    manifest = _manifest(result)

    assert manifest["provider_call_count"] == 1
    assert manifest["reused_prior_response_count"] == 2
    assert "recovery_provenance" in manifest

    provenance = manifest["recovery_provenance"]

    assert provenance["reused_successful_leaf_count"] == 1
    assert provenance["failed_or_missing_leaf_ranges"] == [
        {
            "start_date": "2026-08-14",
            "end_date": "2026-08-14",
            "request_id": failed_leaf.request_id,
        }
    ]

    assert partial.manifest_path.read_bytes() == prior_manifest_bytes


def test_recovery_provider_reuses_accepted_leaf_and_blocks_undeclared_statcast(
    tmp_path: Path,
) -> None:
    partial, requests, _ = _make_recovery_partial(tmp_path)

    schedule_request, accepted_leaf, failed_leaf = requests

    prior_manifest = _manifest(partial)
    content = prior_manifest["content_address_payload"]

    assert isinstance(content, dict)

    prior_identity = content["request_identity"]

    assert isinstance(prior_identity, dict)

    current_identity = dict(prior_identity)
    current_identity["requested_as_of_utc"] = _utc_z(
        _recovery_cutoff()
    )

    provider = MockProvider({})

    (
        prior_path,
        validated_manifest,
        recovery_provider,
        provenance,
    ) = history._prepare_recovery(
        partial.snapshot_dir,
        request_identity=current_identity,
        request_identity_digest=history._value_digest(
            current_identity
        ),
        requested_as_of=_recovery_cutoff(),
        provider=provider,
    )

    assert prior_path == partial.snapshot_dir
    assert validated_manifest["snapshot_id"] == partial.snapshot_id
    assert recovery_provider is not None
    assert provenance is not None

    # Schedule and the successful Statcast leaf are served entirely
    # from immutable prior evidence.
    schedule_response = recovery_provider.fetch(schedule_request)
    accepted_response = recovery_provider.fetch(accepted_leaf)

    assert isinstance(schedule_response, ProviderResponse)
    assert isinstance(accepted_response, ProviderResponse)

    assert recovery_provider.request_execution(
        schedule_request
    ) == "reused_prior_response"

    assert recovery_provider.request_execution(
        accepted_leaf
    ) == "reused_prior_response"

    assert provider.calls == []

    # The failed leaf is the sole declared Statcast retry range.
    assert provenance["failed_or_missing_leaf_ranges"] == [
        {
            "start_date": "2026-08-14",
            "end_date": "2026-08-14",
            "request_id": failed_leaf.request_id,
        }
    ]

    # A different Statcast range must fail closed instead of reaching
    # the underlying provider.
    undeclared = history._statcast_request(
        date(2026, 8, 12),
        date(2026, 8, 12),
    )

    with pytest.raises(
        ProspectiveAcquisitionError,
        match="recovery attempted undeclared provider request",
    ):
        recovery_provider.fetch(undeclared)

    assert provider.calls == []



# ============================================================
# Checkpoint 4A: regular-season Statcast admission contract
# ============================================================


def _statcast_without_game_type(
    *rows: dict[str, object],
) -> bytes:
    columns = tuple(
        column
        for column in STATCAST_COLUMNS
        if column != "game_type"
    )
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(
        handle,
        fieldnames=columns,
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows or (_row(),):
        writer.writerow(
            {
                column: row.get(column, "")
                for column in columns
            }
        )
    return handle.getvalue().encode("utf-8")


def test_regular_season_game_type_is_admitted_and_accounted(
    tmp_path: Path,
) -> None:
    result = _acquire(
        tmp_path,
        _provider(),
    )
    manifest = _manifest(result)

    assert result.snapshot_state == "completed"
    assert result.pitch_count == 1

    assert (
        manifest["statcast_admission_schema_version"]
        == "mlb-hr-statcast-admission-v1"
    )
    assert (
        manifest["historical_game_universe"]
        == "regular_season"
    )
    assert manifest["admitted_game_types"] == ["R"]

    assert manifest[
        "accepted_provider_row_count_total"
    ] == 1

    assert manifest[
        "admitted_regular_season_row_count_before_dedupe"
    ] == 1

    assert manifest[
        "excluded_non_regular_row_count"
    ] == 0

    assert manifest[
        "excluded_non_regular_game_count"
    ] == 0

    assert manifest[
        "excluded_non_regular_game_ids"
    ] == []

    assert manifest[
        "excluded_non_regular_game_types"
    ] == []

    assert (
        manifest["admission_partition_balance_ok"]
        is True
    )


def test_mixed_regular_and_nonregular_chunk_preserves_raw_and_excludes_nonregular(
    tmp_path: Path,
) -> None:
    nonregular_game = "823999"

    raw = _statcast(
        _row(),
        _row(
            game_pk=nonregular_game,
            game_type="A",
            at_bat_number="2",
            pitch_number="1",
            sv_id="all-star-evidence",
        ),
    )

    result = _acquire(
        tmp_path,
        _provider(statcast=raw),
    )

    manifest = _manifest(result)

    assert result.snapshot_state == "completed"
    assert result.pitch_count == 1
    assert result.game_count == 1

    assert manifest[
        "accepted_provider_row_count_total"
    ] == 2

    assert manifest[
        "admitted_regular_season_row_count_before_dedupe"
    ] == 1

    assert manifest[
        "excluded_non_regular_row_count"
    ] == 1

    assert manifest[
        "excluded_non_regular_game_count"
    ] == 1

    assert manifest[
        "excluded_non_regular_game_ids"
    ] == [nonregular_game]

    assert manifest[
        "excluded_non_regular_game_types"
    ] == ["A"]

    assert (
        "Statcast game identity conflicts with schedule"
        not in " ".join(manifest["integrity_errors"])
    )

    assert (
        manifest["coverage"][
            "accepted_statcast_chunk_count"
        ]
        == 1
    )

    statcast_records = [
        record
        for record in manifest["provider_requests"]
        if record.get("endpoint_class")
        == "statcast_pitch_history"
    ]

    assert len(statcast_records) == 1

    record = statcast_records[0]

    raw_path = (
        result.snapshot_dir
        / str(record["body_path"])
    )

    assert raw_path.read_bytes() == raw

    assert (
        record["sha256"]
        == hashlib.sha256(raw).hexdigest()
    )


def test_regular_season_game_missing_from_schedule_still_rejects(
    tmp_path: Path,
) -> None:
    missing_regular_game = "823101"

    raw = _statcast(
        _row(),
        _row(
            game_pk=missing_regular_game,
            game_type="R",
            at_bat_number="2",
            pitch_number="1",
            sv_id="missing-regular-game",
        ),
    )

    result = _acquire(
        tmp_path,
        _provider(statcast=raw),
    )

    manifest = _manifest(result)

    assert result.snapshot_state == "rejected"

    assert (
        f"Statcast game identity conflicts with schedule: "
        f"{missing_regular_game}"
        in " ".join(manifest["integrity_errors"])
    )


def test_missing_statcast_game_type_column_fails_closed(
    tmp_path: Path,
) -> None:
    raw = _statcast_without_game_type(
        _row()
    )

    result = _acquire(
        tmp_path,
        _provider(statcast=raw),
    )

    manifest = _manifest(result)

    assert result.snapshot_state == "rejected"

    assert (
        "Statcast history lacks required columns: game_type"
        in " ".join(manifest["integrity_errors"])
    )


@pytest.mark.parametrize(
    "game_type",
    (
        "",
        "r",
        "UNKNOWN",
    ),
)
def test_blank_or_unknown_statcast_game_type_fails_closed(
    tmp_path: Path,
    game_type: str,
) -> None:
    raw = _statcast(
        _row(
            game_type=game_type,
        )
    )

    result = _acquire(
        tmp_path,
        _provider(statcast=raw),
    )

    manifest = _manifest(result)

    assert result.snapshot_state == "rejected"

    errors = " ".join(
        manifest["integrity_errors"]
    )

    assert (
        "missing game_type" in errors
        or "invalid game_type" in errors
    )


def test_nonregular_exclusion_accounting_is_deterministic_and_digest_bound(
    tmp_path: Path,
) -> None:
    game_a = "823999"
    game_s = "823998"

    raw = _statcast(
        _row(),
        _row(
            game_pk=game_a,
            game_type="A",
            at_bat_number="1",
            pitch_number="1",
            sv_id="a-1",
        ),
        _row(
            game_pk=game_a,
            game_type="A",
            at_bat_number="1",
            pitch_number="2",
            sv_id="a-2",
        ),
        _row(
            game_pk=game_s,
            game_type="S",
            at_bat_number="1",
            pitch_number="1",
            sv_id="s-1",
        ),
    )

    result = _acquire(
        tmp_path,
        _provider(statcast=raw),
    )

    manifest = _manifest(result)

    assert result.snapshot_state == "completed"
    assert result.pitch_count == 1

    assert manifest[
        "accepted_provider_row_count_total"
    ] == 4

    assert manifest[
        "admitted_regular_season_row_count_before_dedupe"
    ] == 1

    assert manifest[
        "excluded_non_regular_row_count"
    ] == 3

    assert manifest[
        "excluded_non_regular_game_count"
    ] == 2

    assert manifest[
        "excluded_non_regular_game_ids"
    ] == [
        game_s,
        game_a,
    ]

    assert manifest[
        "excluded_non_regular_game_types"
    ] == [
        "A",
        "S",
    ]

    assert (
        manifest["accepted_provider_row_count_total"]
        ==
        manifest[
            "admitted_regular_season_row_count_before_dedupe"
        ]
        + manifest[
            "excluded_non_regular_row_count"
        ]
    )

    payload = manifest[
        "content_address_payload"
    ]

    assert (
        history._value_digest(payload)
        == manifest["content_digest"]
    )

    mutated = json.loads(
        json.dumps(payload)
    )

    mutated[
        "excluded_non_regular_row_count"
    ] += 1

    assert (
        history._value_digest(mutated)
        != manifest["content_digest"]
    )



# ============================================================
# Checkpoint 4B1: immutable offline replay
# ============================================================


def _snapshot_hashes(
    root: Path,
) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix():
        hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(
            item
            for item in root.rglob("*")
            if item.is_file()
        )
    }


def test_offline_replay_of_completed_snapshot_is_zero_call_and_idempotent(
    tmp_path: Path,
) -> None:
    source_provider = _provider()

    source = _acquire(
        tmp_path / "source",
        source_provider,
    )

    assert source.snapshot_state == "completed"

    source_hashes = _snapshot_hashes(
        source.snapshot_dir
    )

    replay = history.replay_historical_statcast_snapshot(
        source_snapshot=source.snapshot_dir,
        acquisition_root=tmp_path / "replay",
        git_commit=COMMIT,
    )

    assert replay.snapshot_state == "completed"
    assert replay.no_op is False
    assert replay.provider_call_count == 0
    assert replay.reused_chunk_count == 0
    assert replay.recovered_chunk_count == 0

    manifest = _manifest(replay)

    assert (
        manifest["coverage"]["provider_call_count"]
        == 0
    )

    assert (
        manifest["coverage"][
            "replayed_immutable_response_count"
        ]
        == len(manifest["provider_requests"])
    )

    assert (
        manifest["coverage"][
            "replayed_statcast_chunk_count"
        ]
        == manifest["coverage"][
            "accepted_statcast_chunk_count"
        ]
    )

    assert all(
        record["request_execution"]
        == "replayed_immutable_response"
        for record in manifest["provider_requests"]
    )

    provenance = (
        manifest["content_address_payload"][
            "replay_provenance"
        ]
    )

    assert (
        provenance["source_snapshot_id"]
        == source.snapshot_id
    )

    assert (
        provenance["source_manifest_digest"]
        == source.manifest_digest
    )

    assert (
        provenance["replay_network_access"]
        is False
    )

    assert (
        provenance[
            "replay_underlying_provider_call_count"
        ]
        == 0
    )

    assert (
        provenance[
            "statcast_admission_schema_version"
        ]
        == "mlb-hr-statcast-admission-v1"
    )

    # Source evidence is read-only.
    assert (
        _snapshot_hashes(source.snapshot_dir)
        == source_hashes
    )

    second = history.replay_historical_statcast_snapshot(
        source_snapshot=source.snapshot_dir,
        acquisition_root=tmp_path / "replay",
        git_commit=COMMIT,
    )

    assert second.no_op is True
    assert second.provider_call_count == 0
    assert second.snapshot_id == replay.snapshot_id
    assert second.manifest_digest == replay.manifest_digest

    assert (
        _snapshot_hashes(source.snapshot_dir)
        == source_hashes
    )


def test_offline_replay_accepts_rejected_terminal_source_but_preserves_real_integrity_failure(
    tmp_path: Path,
) -> None:
    missing_regular_game = "823101"

    source = _acquire(
        tmp_path / "source",
        _provider(
            statcast=_statcast(
                _row(),
                _row(
                    game_pk=missing_regular_game,
                    game_type="R",
                    at_bat_number="2",
                    pitch_number="1",
                    sv_id="replay-missing-r-game",
                ),
            )
        ),
    )

    assert source.snapshot_state == "rejected"

    source_hashes = _snapshot_hashes(
        source.snapshot_dir
    )

    replay = history.replay_historical_statcast_snapshot(
        source_snapshot=source.snapshot_dir,
        acquisition_root=tmp_path / "replay",
        git_commit=COMMIT,
    )

    assert replay.snapshot_state == "rejected"
    assert replay.provider_call_count == 0

    manifest = _manifest(replay)

    assert (
        f"Statcast game identity conflicts with schedule: "
        f"{missing_regular_game}"
        in " ".join(manifest["integrity_errors"])
    )

    assert all(
        record["request_execution"]
        == "replayed_immutable_response"
        for record in manifest["provider_requests"]
    )

    assert (
        _snapshot_hashes(source.snapshot_dir)
        == source_hashes
    )

    # Recovery semantics remain separate and unchanged:
    # rejected snapshots still cannot be resumed.
    with pytest.raises(
        history.ProspectiveAcquisitionError,
        match="prior rejected Statcast snapshot cannot be resumed",
    ):
        _acquire(
            tmp_path / "recovery",
            MockProvider({}),
            resume_from_snapshot=source.snapshot_dir,
        )


def test_offline_replay_refuses_partial_source(
    tmp_path: Path,
) -> None:
    partial, _, _ = _make_recovery_partial(
        tmp_path / "partial-source"
    )

    assert partial.snapshot_state == "partial"

    source_hashes = _snapshot_hashes(
        partial.snapshot_dir
    )

    with pytest.raises(
        history.ProspectiveAcquisitionError,
        match="offline replay requires an immutable terminal",
    ):
        history.replay_historical_statcast_snapshot(
            source_snapshot=partial.snapshot_dir,
            acquisition_root=tmp_path / "replay",
            git_commit=COMMIT,
        )

    assert (
        _snapshot_hashes(partial.snapshot_dir)
        == source_hashes
    )


def test_offline_replay_fails_closed_on_mutated_source_raw_bytes(
    tmp_path: Path,
) -> None:
    source = _acquire(
        tmp_path / "source",
        _provider(),
    )

    manifest = _manifest(source)

    statcast_record = next(
        record
        for record in manifest["provider_requests"]
        if record["endpoint_class"]
        == "statcast_pitch_history"
    )

    body_path = (
        source.snapshot_dir
        / str(statcast_record["body_path"])
    )

    body_path.write_bytes(
        body_path.read_bytes()
        + b"\nMUTATED"
    )

    with pytest.raises(
        history.ImmutableCaptureConflictError,
    ):
        history.replay_historical_statcast_snapshot(
            source_snapshot=source.snapshot_dir,
            acquisition_root=tmp_path / "replay",
            git_commit=COMMIT,
        )
