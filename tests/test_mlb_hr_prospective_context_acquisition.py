from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from courtvision.sports.mlb.data import prospective_context_acquisition as acquisition


UTC = timezone.utc
OPERATING_DATE = date(2026, 8, 15)
START = datetime(2026, 8, 15, 20, 0, tzinfo=UTC)
OBSERVED = datetime(2026, 8, 15, 19, 0, tzinfo=UTC)
CUTOFF = datetime(2026, 8, 15, 19, 15, tzinfo=UTC)
COMMIT = "4f48dc8d20c33177ee00dbe629da359e9ae35bd3"


def _event(**updates: object) -> acquisition.ScheduledEvent:
    values: dict[str, object] = {
        "event_id": "823184",
        "operating_date": OPERATING_DATE,
        "scheduled_start_utc": START,
        "away_team_id": "115",
        "away_team": "Colorado Rockies",
        "home_team_id": "137",
        "home_team": "San Francisco Giants",
        "venue_id": "2395",
        "venue_name": "Oracle Park",
        "status": "Pre-Game",
        "away_probable_pitcher_id": "547179",
        "home_probable_pitcher_id": "657277",
    }
    values.update(updates)
    return acquisition.ScheduledEvent(**values)  # type: ignore[arg-type]


def _cluster(event: acquisition.ScheduledEvent | None = None) -> acquisition.EventCluster:
    return acquisition.build_event_clusters((event or _event(),))[0]


def _feed(
    event: acquisition.ScheduledEvent | None = None,
    *,
    game_pk: str | None = None,
    away_pitcher: str | None = None,
    home_pitcher: str | None = None,
    away_lineup: int = 9,
    home_lineup: int = 9,
) -> bytes:
    event = event or _event()
    payload = {
        "gamePk": int(game_pk or event.event_id),
        "gameData": {
            "datetime": {"dateTime": acquisition.utc_text(event.scheduled_start_utc)},
            "teams": {
                "away": {"id": int(event.away_team_id)},
                "home": {"id": int(event.home_team_id)},
            },
            "venue": {"id": int(event.venue_id)},
            "probablePitchers": {
                "away": {
                    "id": int(away_pitcher or event.away_probable_pitcher_id or "547179")
                },
                "home": {
                    "id": int(home_pitcher or event.home_probable_pitcher_id or "657277")
                },
            },
        },
        "liveData": {
            "boxscore": {
                "teams": {
                    "away": {
                        "battingOrder": [600000 + index for index in range(1, away_lineup + 1)]
                    },
                    "home": {
                        "battingOrder": [700000 + index for index in range(1, home_lineup + 1)]
                    },
                }
            }
        },
    }
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def _request(event: acquisition.ScheduledEvent | None = None) -> acquisition.EvidenceRequest:
    event = event or _event()
    return acquisition.EvidenceRequest(
        request_id=f"feed-{event.event_id}",
        evidence_class="volatile_pregame",
        source_name="mlb_statsapi_game_feed",
        provider="mlb_statsapi",
        url=f"mock://feed/{event.event_id}",
        event_id=event.event_id,
    )


def _response(
    body: bytes | None = None,
    *,
    status_code: int = 200,
    first_observed: datetime = OBSERVED,
    captured: datetime = OBSERVED + timedelta(seconds=1),
    published: datetime | None = None,
) -> acquisition.ProviderResponse:
    return acquisition.ProviderResponse(
        body=body or _feed(),
        status_code=status_code,
        headers=MappingProxyType({"Content-Type": "application/json"}),
        first_observed_at_utc=first_observed,
        captured_at_utc=captured,
        provider_published_at_utc=published,
    )


class MockProvider:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def fetch(
        self, request: acquisition.EvidenceRequest
    ) -> acquisition.ProviderResponse:
        self.calls.append(request.request_id)
        result = self.responses[request.request_id]
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, acquisition.ProviderResponse)
        return result


def _acquire(
    tmp_path: Path,
    provider: MockProvider,
    *,
    cluster: acquisition.EventCluster | None = None,
    request: acquisition.EvidenceRequest | None = None,
    observed: datetime = OBSERVED,
    cutoff: datetime = CUTOFF,
) -> acquisition.AcquisitionResult:
    selected = cluster or _cluster()
    evidence_request = request or _request(selected.events[0])
    return acquisition.acquire_event_cluster(
        selected,
        requested_as_of_utc=cutoff,
        observed_at_utc=observed,
        requests=(evidence_request,),
        provider=provider,
        acquisition_root=tmp_path / "prospective-acquisition",
        git_commit=COMMIT,
    )


def _manifest(result: acquisition.AcquisitionResult) -> dict[str, object]:
    return json.loads(result.manifest_path.read_text(encoding="utf-8"))


def test_clusters_are_deterministic_and_use_shared_event_windows() -> None:
    second = _event(
        event_id="823588",
        scheduled_start_utc=START + timedelta(minutes=5),
        away_team_id="120",
        away_team="Washington Nationals",
        home_team_id="121",
        home_team="New York Mets",
        venue_id="3289",
        venue_name="Citi Field",
        away_probable_pitcher_id="695418",
        home_probable_pitcher_id="640455",
    )
    first = acquisition.build_event_clusters((_event(), second))
    repeated = acquisition.build_event_clusters((second, _event()))

    assert first == repeated
    assert len(first) == 1
    assert first[0].window_opens_at_utc == datetime(2026, 8, 15, 18, 35, tzinfo=UTC)
    assert first[0].window_closes_at_utc == datetime(2026, 8, 15, 19, 15, tzinfo=UTC)


def test_cluster_identity_ignores_volatile_status_and_probable_pitcher_changes() -> None:
    scheduled = _event(status="Scheduled")
    updated = _event(
        status="Pre-Game",
        away_probable_pitcher_id="547180",
        home_probable_pitcher_id="657278",
    )

    assert _cluster(scheduled).cluster_id == _cluster(updated).cluster_id


def test_custom_timing_policy_controls_response_admissibility(tmp_path: Path) -> None:
    event = _event()
    cluster = acquisition.build_event_clusters(
        (event,),
        policy=acquisition.AcquisitionPolicy(
            target_lead_minutes=60,
            minimum_lead_minutes=30,
            maximum_lead_minutes=120,
            cluster_window_minutes=10,
        ),
    )[0]
    observed = START - timedelta(minutes=100)
    provider = MockProvider(
        {
            "feed-823184": _response(
                first_observed=observed,
                captured=observed + timedelta(seconds=1),
            )
        }
    )

    result = _acquire(
        tmp_path,
        provider,
        cluster=cluster,
        observed=observed,
        cutoff=START - timedelta(minutes=30),
    )

    assert result.capture_state == "completed"


def test_http_provider_first_observation_is_response_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"{}"

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: FakeResponse(),
    )
    provider = acquisition.HttpEvidenceProvider()
    request = acquisition.EvidenceRequest(
        request_id="clock-check",
        evidence_class="stable_history",
        source_name="mlb_schedule",
        provider="mlb_statsapi",
        url="https://statsapi.mlb.com/mock",
    )

    response = provider.fetch(request)

    assert response.first_observed_at_utc == response.captured_at_utc


def test_post_cutoff_evidence_is_preserved_but_rejected(tmp_path: Path) -> None:
    provider = MockProvider(
        {
            "feed-823184": _response(
                first_observed=CUTOFF + timedelta(seconds=1),
                captured=CUTOFF + timedelta(seconds=2),
            )
        }
    )

    result = _acquire(tmp_path, provider)
    source = _manifest(result)["sources"][0]

    assert result.capture_state == "rejected"
    assert source["availability_status"] == "rejected"
    assert source["body_path"]


def test_response_body_write_failure_is_visible_in_acquisition_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_body_write(path: Path, payload: bytes) -> None:
        if path.suffix == ".body":
            raise PermissionError("denied")
        path.write_bytes(payload)

    monkeypatch.setattr(acquisition, "_write_raw_file", fail_body_write)
    result = _acquire(tmp_path, MockProvider({"feed-823184": _response()}))
    source = _manifest(result)["sources"][0]

    assert result.capture_state == "unavailable"
    assert source["request_result"] == "response_persistence_failed"
    assert source["raw_persistence_status"] == "failed"
    assert "persistence failure" in source["availability_note"]
    assert source["sha256"]
    assert source["body_path"] is None


def test_game_already_started_makes_no_provider_call(tmp_path: Path) -> None:
    provider = MockProvider({"feed-823184": _response()})

    with pytest.raises(acquisition.ProspectiveAcquisitionError, match="already started"):
        _acquire(tmp_path, provider, observed=START)

    assert provider.calls == []


def test_missed_acquisition_makes_no_provider_call(tmp_path: Path) -> None:
    provider = MockProvider({"feed-823184": _response()})

    with pytest.raises(acquisition.ProspectiveAcquisitionError, match="missed"):
        _acquire(tmp_path, provider, observed=CUTOFF + timedelta(seconds=1), cutoff=START)

    assert provider.calls == []


def test_conflicting_game_identity_fails_closed_and_retains_raw(tmp_path: Path) -> None:
    provider = MockProvider({"feed-823184": _response(_feed(game_pk="823999"))})

    result = _acquire(tmp_path, provider)
    source = _manifest(result)["sources"][0]

    assert result.capture_state == "rejected"
    assert "conflicting game identity" in source["availability_note"]
    assert (result.capture_dir / source["body_path"]).is_file()


def test_conflicting_pitcher_identity_fails_closed(tmp_path: Path) -> None:
    provider = MockProvider(
        {"feed-823184": _response(_feed(away_pitcher="547180"))}
    )

    result = _acquire(tmp_path, provider)

    assert result.capture_state == "rejected"
    assert "conflicting probable pitcher" in _manifest(result)["sources"][0][
        "availability_note"
    ]


def test_late_lineup_response_is_rejected_not_backfilled(tmp_path: Path) -> None:
    event = _event(scheduled_start_utc=START)
    provider = MockProvider(
        {
            "feed-823184": _response(
                _feed(event),
                first_observed=START - timedelta(minutes=44),
                captured=START - timedelta(minutes=44) + timedelta(seconds=1),
            )
        }
    )

    result = _acquire(tmp_path, provider, cutoff=START - timedelta(minutes=40))

    assert result.capture_state == "rejected"
    assert "outside declared admissible window" in _manifest(result)["sources"][0][
        "availability_note"
    ]


def test_future_statcast_game_leakage_is_rejected() -> None:
    row = {
        "game_pk": "823184",
        "game_date": "2026-08-15",
        "at_bat_number": "1",
        "pitch_number": "1",
    }

    with pytest.raises(acquisition.ProspectiveAcquisitionError, match="future Statcast"):
        acquisition.validate_historical_statcast_rows(
            (row,),
            completed_game_ids={"823184"},
            requested_as_of_utc=CUTOFF,
            first_observed_at_utc=OBSERVED,
            captured_at_utc=OBSERVED,
        )


def test_historical_statcast_captured_after_cutoff_is_rejected() -> None:
    row = {
        "game_pk": "823184",
        "game_date": "2026-08-14",
        "at_bat_number": "1",
        "pitch_number": "1",
    }

    with pytest.raises(acquisition.ProspectiveAcquisitionError, match="captured after cutoff"):
        acquisition.validate_historical_statcast_rows(
            (row,),
            completed_game_ids={"823184"},
            requested_as_of_utc=CUTOFF,
            first_observed_at_utc=CUTOFF + timedelta(seconds=1),
            captured_at_utc=CUTOFF + timedelta(seconds=2),
        )


def test_incorrect_park_effective_date_is_rejected() -> None:
    observation = {
        "venue_id": "2395",
        "effective_from_date": "2026-08-16",
        "effective_to_date": "",
        "published_or_available_at_utc": acquisition.utc_text(OBSERVED),
        "captured_at_utc": acquisition.utc_text(OBSERVED),
        "version": "park-factor-2026-v1",
    }

    with pytest.raises(acquisition.ProspectiveAcquisitionError, match="effective date"):
        acquisition.validate_park_observation(
            observation, event=_event(), requested_as_of_utc=CUTOFF
        )


def test_weather_valid_for_wrong_time_is_rejected() -> None:
    observation = {
        "event_id": "823184",
        "venue_id": "2395",
        "issued_at_utc": acquisition.utc_text(OBSERVED - timedelta(minutes=5)),
        "valid_for_utc": acquisition.utc_text(START + timedelta(hours=2)),
        "first_observed_at_utc": acquisition.utc_text(OBSERVED),
        "captured_at_utc": acquisition.utc_text(OBSERVED + timedelta(seconds=1)),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
    }

    with pytest.raises(acquisition.ProspectiveAcquisitionError, match="wrong time"):
        acquisition.validate_weather_observation(
            observation, event=_event(), requested_as_of_utc=CUTOFF
        )


def test_nws_hourly_forecast_preserves_issuance_validity_units_and_humidity() -> None:
    payload = {
        "type": "Feature",
        "properties": {
            "generatedAt": "2026-08-15T18:00:00Z",
            "periods": [
                {
                    "number": 3,
                    "startTime": "2026-08-15T20:00:00Z",
                    "endTime": "2026-08-15T21:00:00Z",
                    "temperature": 64,
                    "temperatureUnit": "F",
                    "windSpeed": "8 mph",
                    "windDirection": "W",
                    "relativeHumidity": {"value": 71},
                }
            ],
        },
    }

    observation = acquisition.normalize_nws_hourly_forecast(
        json.dumps(payload).encode("utf-8"),
        event=_event(),
        first_observed_at_utc="2026-08-15T18:50:00Z",
        captured_at_utc="2026-08-15T18:50:01Z",
        requested_as_of_utc=CUTOFF,
    )

    assert observation["issued_at_utc"] == "2026-08-15T18:00:00Z"
    assert observation["valid_for_utc"] == "2026-08-15T20:00:00Z"
    assert observation["temperature_unit"] == "fahrenheit"
    assert observation["wind_speed"] == "8"
    assert observation["wind_speed_unit"] == "mph"
    assert observation["humidity"] == 71


def test_duplicate_immutable_capture_is_no_op_without_provider_call(tmp_path: Path) -> None:
    provider = MockProvider({"feed-823184": _response()})

    first = _acquire(tmp_path, provider)
    second = _acquire(tmp_path, provider)

    assert first.no_op is False
    assert second.no_op is True
    assert second.provider_call_count == 0
    assert provider.calls == ["feed-823184"]


def test_completed_capture_is_no_op_after_volatile_schedule_metadata_changes(
    tmp_path: Path,
) -> None:
    initial_event = _event(status="Scheduled")
    updated_event = _event(
        status="Pre-Game",
        away_probable_pitcher_id="547180",
    )
    provider = MockProvider({"feed-823184": _response(_feed(initial_event))})

    first = _acquire(
        tmp_path,
        provider,
        cluster=_cluster(initial_event),
        request=_request(initial_event),
    )
    second = _acquire(
        tmp_path,
        provider,
        cluster=_cluster(updated_event),
        request=_request(updated_event),
    )

    assert first.capture_id == second.capture_id
    assert second.no_op is True
    assert provider.calls == ["feed-823184"]


def test_conflicting_existing_capture_fails_closed(tmp_path: Path) -> None:
    provider = MockProvider({"feed-823184": _response()})
    first = _acquire(tmp_path, provider)
    source = _manifest(first)["sources"][0]
    (first.capture_dir / source["body_path"]).write_bytes(b"mutated")

    with pytest.raises(
        acquisition.ImmutableCaptureConflictError, match="raw response digest"
    ):
        _acquire(tmp_path, provider)


def test_partial_provider_response_is_explicit(tmp_path: Path) -> None:
    provider = MockProvider({"feed-823184": _response(status_code=206)})

    result = _acquire(tmp_path, provider)

    assert result.capture_state == "partial"
    assert _manifest(result)["sources"][0]["availability_status"] == "partial"


def test_network_provider_failure_is_explicit_and_terminal(tmp_path: Path) -> None:
    provider = MockProvider({"feed-823184": TimeoutError("provider timeout")})

    result = _acquire(tmp_path, provider)
    source = _manifest(result)["sources"][0]

    assert result.capture_state == "unavailable"
    assert source["availability_status"] == "unavailable"
    assert source["availability_note"] == "provider failure: TimeoutError"


def test_partial_lineup_coverage_is_preserved(tmp_path: Path) -> None:
    provider = MockProvider(
        {"feed-823184": _response(_feed(away_lineup=9, home_lineup=4))}
    )

    result = _acquire(tmp_path, provider)

    assert _manifest(result)["sources"][0]["lineup_slots_by_side"] == {
        "away": 9,
        "home": 4,
    }


def test_full_slate_history_plan_deduplicates_teams_and_statcast() -> None:
    second = _event(
        event_id="823588",
        away_team_id="120",
        away_team="Washington Nationals",
        home_team_id="121",
        home_team="New York Mets",
        venue_id="3289",
        venue_name="Citi Field",
    )

    plan = acquisition.build_daily_history_request_plan(
        (_event(), second),
        operating_date=OPERATING_DATE,
        season_start_date=date(2026, 3, 25),
    )

    assert len([item for item in plan if item.source_name == "mlb_active_roster"]) == 4
    assert len([item for item in plan if item.source_name == "statcast_pitch_history"]) == 1
    assert len([item for item in plan if item.source_name == "mlb_schedule"]) == 1


def _schedule_body(event: acquisition.ScheduledEvent) -> bytes:
    payload = {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": int(event.event_id),
                        "officialDate": event.operating_date.isoformat(),
                        "gameDate": acquisition.utc_text(event.scheduled_start_utc),
                        "teams": {
                            "away": {
                                "team": {
                                    "id": int(event.away_team_id),
                                    "name": event.away_team,
                                },
                                "probablePitcher": {
                                    "id": int(event.away_probable_pitcher_id or "547179")
                                },
                            },
                            "home": {
                                "team": {
                                    "id": int(event.home_team_id),
                                    "name": event.home_team,
                                },
                                "probablePitcher": {
                                    "id": int(event.home_probable_pitcher_id or "657277")
                                },
                            },
                        },
                        "venue": {"id": int(event.venue_id), "name": event.venue_name},
                        "status": {"detailedState": "Pre-Game"},
                    }
                ]
            }
        ]
    }
    return json.dumps(payload).encode("utf-8")


def test_daily_history_is_content_addressed_and_reused_without_redownload(
    tmp_path: Path,
) -> None:
    event = _event()
    requests = (
        acquisition.EvidenceRequest(
            request_id="schedule",
            evidence_class="stable_history",
            source_name="mlb_schedule",
            provider="mlb_statsapi",
            url="mock://schedule",
        ),
        acquisition.EvidenceRequest(
            request_id="roster-115",
            evidence_class="stable_history",
            source_name="mlb_active_roster",
            provider="mlb_statsapi",
            url="mock://roster/115",
        ),
        acquisition.EvidenceRequest(
            request_id="completed",
            evidence_class="stable_history",
            source_name="mlb_completed_game_schedule",
            provider="mlb_statsapi",
            url="mock://completed",
        ),
        acquisition.EvidenceRequest(
            request_id="statcast",
            evidence_class="stable_history",
            source_name="statcast_pitch_history",
            provider="baseball_savant",
            url="mock://statcast",
        ),
    )
    completed = json.dumps(
        {
            "dates": [
                {
                    "games": [
                        {
                            "gamePk": 823100,
                            "gameDate": "2026-08-14T20:00:00Z",
                            "status": {"abstractGameState": "Final"},
                        }
                    ]
                }
            ]
        }
    ).encode("utf-8")
    statcast = (
        b"game_pk,game_date,at_bat_number,pitch_number,batter,pitcher,events\n"
        b"823100,2026-08-14,1,1,600001,500001,\n"
    )
    provider = MockProvider(
        {
            "schedule": _response(_schedule_body(event)),
            "roster-115": _response(b'{"roster":[{"person":{"id":600001}}]}'),
            "completed": _response(completed),
            "statcast": _response(statcast),
        }
    )

    first = acquisition.acquire_daily_history(
        (event,),
        requested_as_of_utc=CUTOFF,
        requests=requests,
        provider=provider,
        acquisition_root=tmp_path / "history",
        git_commit=COMMIT,
    )
    second = acquisition.acquire_daily_history(
        (event,),
        requested_as_of_utc=START - timedelta(minutes=30),
        requests=requests,
        provider=provider,
        acquisition_root=tmp_path / "history",
        git_commit=COMMIT,
    )

    assert first.snapshot_state == "completed"
    assert first.no_op is False
    assert second.no_op is True
    assert second.provider_call_count == 0
    assert provider.calls == ["schedule", "roster-115", "completed", "statcast"]


def test_daily_history_rejects_nonfinal_statcast_game(tmp_path: Path) -> None:
    event = _event()
    requests = (
        acquisition.EvidenceRequest(
            request_id="schedule",
            evidence_class="stable_history",
            source_name="mlb_schedule",
            provider="mlb_statsapi",
            url="mock://schedule",
        ),
        acquisition.EvidenceRequest(
            request_id="completed",
            evidence_class="stable_history",
            source_name="mlb_completed_game_schedule",
            provider="mlb_statsapi",
            url="mock://completed",
        ),
        acquisition.EvidenceRequest(
            request_id="statcast",
            evidence_class="stable_history",
            source_name="statcast_pitch_history",
            provider="baseball_savant",
            url="mock://statcast",
        ),
    )
    provider = MockProvider(
        {
            "schedule": _response(_schedule_body(event)),
            "completed": _response(b'{"dates":[{"games":[]}]}'),
            "statcast": _response(
                b"game_pk,game_date,at_bat_number,pitch_number\n"
                b"823100,2026-08-14,1,1\n"
            ),
        }
    )

    result = acquisition.acquire_daily_history(
        (event,),
        requested_as_of_utc=CUTOFF,
        requests=requests,
        provider=provider,
        acquisition_root=tmp_path / "history",
        git_commit=COMMIT,
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    statcast = next(
        item for item in manifest["sources"] if item["request_id"] == "statcast"
    )

    assert result.snapshot_state == "rejected"
    assert statcast["availability_status"] == "rejected"
    assert "future Statcast" in statcast["availability_note"]


def test_statcast_accepts_only_unique_pitches_from_completed_prior_games() -> None:
    rows = (
        {
            "game_pk": "823100",
            "game_date": "2026-08-14",
            "at_bat_number": "1",
            "pitch_number": "1",
        },
        {
            "game_pk": "823100",
            "game_date": "2026-08-14",
            "at_bat_number": "1",
            "pitch_number": "2",
        },
    )

    acquisition.validate_historical_statcast_rows(
        rows,
        completed_game_ids={"823100"},
        requested_as_of_utc=CUTOFF,
        first_observed_at_utc=OBSERVED,
        captured_at_utc=OBSERVED + timedelta(seconds=1),
    )


def test_module_has_no_training_or_operational_imports() -> None:
    source = Path(acquisition.__file__).read_text(encoding="utf-8")

    for prohibited in (
        "official_pick",
        "kelly",
        "bankroll",
        "settlement",
        "run_today",
        "predictions.json",
    ):
        assert prohibited not in source.casefold()
