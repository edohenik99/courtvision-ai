"""Offline acceptance and adversarial custody/freeze tests; no live qualification."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import json
import socket

import pytest

from courtvision.sports.nba.prospective_evidence import (
    EVIDENCE_SCHEMA, ProspectiveEvidenceError, canonical_bytes, capture_response,
    digest, normalized_request, source_manifest, verify_capture,
)
from courtvision.sports.nba.prospective_freeze import (
    FREEZE_SCHEMA, MARKET_SCHEMA, PreseasonMeasurement, bind_market_observation,
    _STATE_FIELDS, freeze_models, model_snapshot, verify_model_freeze,
)

SHA = "ef5ab02ad3d28f6298c87a23a853e1289750a427"
REQUESTED = "2026-10-07T19:00:00Z"
RESPONDED = "2026-10-07T19:00:01Z"
CREATED = datetime(2026, 10, 7, 20, tzinfo=timezone.utc)
DURABLE = datetime(2026, 10, 7, 20, 0, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("provider/network access is forbidden in foundation tests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def request(request_id="stats-1", player="player-1", **overrides):
    values = dict(request_id=request_id, provider="api_nba", source_role="factual", endpoint="/players/statistics",
        parameters={"player_id": player, "season": "2025"}, repository_commit_sha=SHA,
        operating_date="2026-10-07", canonical_event_id="event-1", provider_event_id="provider-event-1")
    values.update(overrides)
    return normalized_request(**values)


def capture(tmp_path, req=None, **overrides):
    values = dict(request=req or request(), requested_at_utc=REQUESTED, responded_at_utc=RESPONDED,
        http_status=200, response_metadata={"content-type": "application/json"},
        raw_body=b'{"response":[{"player_id":"player-1","season":"2025","points":12}]}')
    values.update(overrides)
    return capture_response(tmp_path / "journal", **values)


def metadata(**overrides):
    values = dict(operating_date="2026-10-07", prediction_run_id="synthetic-run-1", repository_commit_sha=SHA)
    values.update(overrides)
    return PreseasonMeasurement(**values)


def snapshot(tmp_path, meta=None, player="player-1", capture_id="stats-1", **overrides):
    sources = source_manifest(tmp_path / "journal", [capture_id])
    values = dict(canonical_event_id="event-1", provider_event_ids={"api_nba": "provider-event-1"},
        player_id=player, canonical_player_name="Synthetic Player " + player, team="BOS", opponent="TOR",
        commence_time_utc="2026-10-07T23:00:00Z", model_id="synthetic-model", model_version="fixture-v1",
        projected_minutes=18, minutes_evidence_ref={"request_id": capture_id, "raw_body_sha256": sources[capture_id]["raw_body_sha256"]},
        projected_points=12, projection_method="synthetic-fixture", projection_evidence_ref={"request_id": capture_id,
            "raw_body_sha256": sources[capture_id]["raw_body_sha256"]},
        projection_cutoff_utc="2026-10-07T19:01:00Z", projection_timestamp_utc="2026-10-07T19:02:00Z")
    values.update(overrides)
    return model_snapshot(meta or metadata(), source_manifest_sha256=digest(sources), **values)


def freeze(tmp_path, rows=None, meta=None, request_ids=None, **overrides):
    if rows is None:
        capture(tmp_path)
        rows = [snapshot(tmp_path)]
    clock_values = iter([CREATED, DURABLE])
    values = dict(metadata=meta or metadata(), rows=rows, evidence_root=tmp_path / "journal",
        request_ids=request_ids if request_ids is not None else ["stats-1"], exclusions=[], clock=lambda: next(clock_values))
    values.update(overrides)
    return freeze_models(tmp_path / "articles", **values)


def verify(tmp_path, root):
    return verify_model_freeze(root, expected_repository_sha=SHA, evidence_root=tmp_path / "journal")


def mutate_json(path, mutate):
    value = json.loads(path.read_bytes())
    mutate(value)
    path.write_bytes(canonical_bytes(value) + b"\n")


def test_request_identity_deterministic_secret_independent(tmp_path):
    a = request(parameters={"season": "2025", "player_id": "player-1", "API_KEY": "hidden-a",
        "nested": {"Authorization": "Bearer hidden-b", "query": "safe"}})
    b = request(parameters={"nested": {"query": "safe", "apiKey": "another-key"}, "player_id": "player-1", "season": "2025"})
    assert a == b and digest(a) == digest(b)
    saved = capture(tmp_path, a)
    raw = (tmp_path / "journal" / EVIDENCE_SCHEMA / "stats-1" / "manifest.json").read_bytes()
    assert b"hidden" not in raw and b"another-key" not in raw
    assert saved.manifest["request_identity_sha256"] == digest(a)


CREDENTIAL_HEADERS = ["Authorization", "authorization", "Proxy-Authorization", "X-API-Key", "API-Key",
    "X-Api-Key", "x-apisports-key", "X-APISPORTS-KEY", "Cookie", "Set-Cookie", "X-Session-Token", "Client-Secret"]


def header_parameters(name, secret, form, nested=False):
    pairs = [("Accept", "application/json"), (name, secret), ("X-Trace-ID", "trace-1")]
    if form == "mapping":
        headers = dict(pairs)
    elif form == "pairs":
        headers = [list(pair) for pair in pairs]
    elif form == "tuples":
        headers = tuple(pairs)
    else:
        headers = [{form: key, "value": value} for key, value in pairs]
    params = {"requestHeaders": headers}
    return {"context": [{"transport": params}]} if nested else params


@pytest.mark.parametrize("name", CREDENTIAL_HEADERS)
@pytest.mark.parametrize("form", ["mapping", "pairs", "tuples", "name", "key", "header"])
@pytest.mark.parametrize("nested", [False, True])
def test_header_credentials_never_enter_request_or_capture_identity(tmp_path, name, form, nested):
    first = request(parameters=header_parameters(name, "hidden-a", form, nested))
    rotated = request(parameters=header_parameters(name, "hidden-b", form, nested))
    assert first == rotated and digest(first) == digest(rotated)
    assert b"hidden" not in canonical_bytes(first)
    assert b"hidden" not in repr(first).encode()
    saved = capture(tmp_path, first)
    path = tmp_path / "journal" / EVIDENCE_SCHEMA / "stats-1" / "manifest.json"
    before = path.read_bytes()
    assert b"hidden" not in before and b"hidden" not in repr(saved).encode()
    assert capture(tmp_path, rotated) == saved == verify_capture(tmp_path / "journal", "stats-1")
    assert path.read_bytes() == before
    assert saved.manifest["request_identity_sha256"] == digest(rotated)
    assert saved.manifest["raw_body_sha256"] == digest_body(saved.raw_body)


def digest_body(raw):
    import hashlib
    return hashlib.sha256(raw).hexdigest()


def test_header_forms_normalize_to_same_nonsecret_identity():
    forms = [request(parameters=header_parameters("X-API-Key", "hidden", form))
        for form in ("mapping", "pairs", "tuples", "name", "key", "header")]
    assert all(item == forms[0] and digest(item) == digest(forms[0]) for item in forms)
    assert forms[0]["parameters"] == {"requestHeaders": {"accept": "application/json", "x-trace-id": "trace-1"}}
    changed = request(parameters={"requestHeaders": {"X-Trace-ID": "trace-2", "Accept": "application/json"}})
    assert digest(changed) != digest(forms[0])
    same = request(parameters={"requestHeaders": {"x-trace-id": "trace-1", "ACCEPT": "application/json"}})
    assert same == forms[0]


@pytest.mark.parametrize("headers", [["X-API-Key", "hidden"], ("X-API-Key", "hidden"),
    {"name": "X-API-Key", "value": "hidden"}, {"KEY": "X-API-Key", "VALUE": "hidden"}])
def test_single_header_pairs_and_records_are_sanitized(headers):
    assert request(parameters={"headers": headers})["parameters"] == {"headers": {}}


@pytest.mark.parametrize("headers", [None, "X-API-Key: hidden", [["X-API-Key"]],
    [["X-API-Key", "hidden", "extra"]], [[42, "hidden"]], [["Accept", None]],
    [["Accept", "safe\r\ninjected"]], [["", "hidden"]], [["X API Key", "hidden"]],
    [["Accept", "safe"], "ambiguous"], [["Accept", "safe"], ["accept", "other"]],
    {"Accept": ["safe"]}, {"nested": {"X-API-Key": "hidden"}}, [[["X-API-Key", "hidden"]]],
    [{"name": "X-API-Key", "value": "hidden", "extra": "ambiguous"}],
    [{"name": "X-API-Key", "Name": "Accept", "value": "hidden"}],
    [{"header_name": "X-API-Key", "header_value": "hidden"}], [{"X-API-Key": "hidden"}],
    [["Authorization", None]], [["X-API-Key", {"ambiguous": "hidden"}]]])
def test_malformed_or_ambiguous_header_structures_fail_closed(headers):
    with pytest.raises(ProspectiveEvidenceError) as caught:
        request(parameters={"nested": {"headers": headers}})
    assert "hidden" not in str(caught.value)


@pytest.mark.parametrize("params", [{"metadata": [["Authorization", "hidden"]]},
    {"metadata": ("X-API-Key", "hidden")}, {"metadata": {"name": "Cookie", "value": "hidden"}},
    {"metadata": {"header": "x-apisports-key", "value": "hidden"}},
    {"metadata": [{"NAME": "X-Session-Token", "VALUE": "hidden"}]},
    {"metadata": {"header_name": "Authorization", "header_value": "hidden"}},
    {"metadata": [{"headerName": "X-API-Key", "headerValue": "hidden"}]}])
def test_credential_pairs_outside_header_containers_fail_closed(params):
    with pytest.raises(ProspectiveEvidenceError, match="credential"):
        request(parameters=params)


@pytest.mark.parametrize("name", CREDENTIAL_HEADERS)
@pytest.mark.parametrize("form", ["mapping", "pairs", "name"])
def test_verifier_rejects_resigned_credential_header_metadata(tmp_path, name, form):
    capture(tmp_path)
    params = header_parameters(name, "hidden", form, nested=True)
    req = request()
    req["parameters"] = params
    def inject(manifest):
        manifest["parameters"] = params
        manifest["request_identity_sha256"] = digest(req)
        manifest["capture_sha256"] = digest({key: value for key, value in manifest.items() if key != "capture_sha256"})
    mutate_json(tmp_path / "journal" / EVIDENCE_SCHEMA / "stats-1" / "manifest.json", inject)
    with pytest.raises(ProspectiveEvidenceError, match="credential") as caught:
        verify_capture(tmp_path / "journal", "stats-1")
    assert "hidden" not in str(caught.value)
    with pytest.raises(ProspectiveEvidenceError, match="credential"):
        source_manifest(tmp_path / "journal", ["stats-1"])


@pytest.mark.parametrize("headers", [{"Authorization": "hidden"}, [["X-API-Key", "hidden"]],
    [{"name": "Cookie", "value": "hidden"}], [["Accept", "safe", "ambiguous"]]])
def test_capture_rejects_unsanitized_header_metadata_before_writes(tmp_path, headers):
    req = request()
    req["parameters"] = {"headers": headers}
    with pytest.raises(ProspectiveEvidenceError) as caught:
        capture(tmp_path, req)
    assert "hidden" not in str(caught.value)
    assert not (tmp_path / "journal").exists()


def test_request_header_sanitization_does_not_rewrite_response_body(tmp_path):
    body = b'{ "headers": [["Accept", "application/json"]], "response": [] }\n'
    saved = capture(tmp_path, request(parameters={"headers": [["X-API-Key", "hidden"]]}), raw_body=body)
    assert saved.raw_body == body
    assert (tmp_path / "journal" / EVIDENCE_SCHEMA / "stats-1" / "body.bin").read_bytes() == body
    assert saved.manifest["raw_body_sha256"] == digest_body(body)


def test_raw_capture_exact_replay_and_immutable_nested_metadata(tmp_path):
    saved = capture(tmp_path)
    replay = verify_capture(tmp_path / "journal", "stats-1")
    assert saved == replay == capture(tmp_path)
    assert json.loads(replay.raw_body)["response"][0]["points"] == 12
    with pytest.raises(TypeError):
        replay.manifest["parameters"]["season"] = "2026"


@pytest.mark.parametrize("overrides", [dict(raw_body=b'{"different":true}'), dict(responded_at_utc="2026-10-07T19:00:02Z"),
    dict(request=request(parameters={"player_id": "other-player"}))])
def test_conflicting_capture_identity_never_overwrites(tmp_path, overrides):
    before = capture(tmp_path)
    with pytest.raises(ProspectiveEvidenceError):
        capture(tmp_path, **overrides)
    assert verify_capture(tmp_path / "journal", "stats-1") == before


@pytest.mark.parametrize("body", [b'{"api_key":"hidden"}', b'{"nested":{"Authorization":"hidden"}}',
    b'{"cookies":null}', b'Bearer private-token', b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff'])
def test_unsafe_raw_body_rejected_before_writes(tmp_path, body):
    with pytest.raises(ProspectiveEvidenceError):
        capture(tmp_path, raw_body=body)
    assert not (tmp_path / "journal").exists()


@pytest.mark.parametrize("overrides", [dict(responded_at_utc="2026-10-07T18:00:00Z"),
    dict(requested_at_utc="2026-10-07T19:00:00"), dict(responded_at_utc="2026-10-07T19:00:01-04:00"),
    dict(http_status=True), dict(response_metadata={"set-cookie": "private"})])
def test_capture_invalid_clocks_status_metadata(tmp_path, overrides):
    with pytest.raises(ProspectiveEvidenceError):
        capture(tmp_path, **overrides)
    assert not (tmp_path / "journal").exists()


def test_raw_body_one_byte_tamper(tmp_path):
    capture(tmp_path)
    path = tmp_path / "journal" / EVIDENCE_SCHEMA / "stats-1" / "body.bin"
    before = path.read_bytes()
    path.write_bytes(before[:-1] + b" ")
    with pytest.raises(ProspectiveEvidenceError, match="hash/length"):
        verify_capture(tmp_path / "journal", "stats-1")


@pytest.mark.parametrize("change", [lambda m: m.pop("endpoint"), lambda m: m.update(schema_version="unsupported"),
    lambda m: m.update(request_identity_sha256="0" * 64), lambda m: m.update(request_id="another-request"),
    lambda m: m.update(raw_body_byte_length=-1)])
def test_capture_manifest_malformed_or_unsupported(tmp_path, change):
    capture(tmp_path)
    mutate_json(tmp_path / "journal" / EVIDENCE_SCHEMA / "stats-1" / "manifest.json", change)
    with pytest.raises(ProspectiveEvidenceError):
        verify_capture(tmp_path / "journal", "stats-1")


def test_cache_cannot_substitute_for_verified_evidence(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "response.json").write_text('{"response":[]}', encoding="utf-8")
    with pytest.raises(ProspectiveEvidenceError):
        verify_capture(cache, "stats-1")
    with pytest.raises(ProspectiveEvidenceError):
        source_manifest(cache, ["stats-1"])


@pytest.mark.parametrize("override", [dict(season_phase="REGULAR_SEASON"), dict(regular_season_model_evidence=True),
    dict(research_only=False), dict(eligible_for_betting=True), dict(eligible_for_betting=0), dict(kelly_eligible=True),
    dict(eligible_for_official_pick=True), dict(target_market="player_rebounds"), dict(schema_version="legacy"),
    dict(operating_date="2026-10-7")])
def test_preseason_policy_fails_closed(override):
    with pytest.raises(ProspectiveEvidenceError):
        metadata(**override)


def test_preseason_metadata_immutable():
    m = metadata()
    assert m.measurement_class == "PRESEASON_REHEARSAL" and m.season_phase == "PRESEASON"
    with pytest.raises(FrozenInstanceError):
        m.season_phase = "REGULAR_SEASON"


PROHIBITED = """sportsbook bookmaker vendor line american_odds decimal_odds implied_probability market_timestamp_utc
selected_side edge model_edge probability_based_edge closing_line closing_odds CLV stake kelly bankroll
result settlement actual_points actual_minutes final_points final_stats box_score model_over_probability model_under_probability""".split()


@pytest.mark.parametrize("field", PROHIBITED + ["ActualPoints", "target_game_actual_minutes", "bookmakerName",
    "AMERICANODDS", "ACTUALPOINTS", "decimalodds"])
@pytest.mark.parametrize("nested", [False, True])
def test_model_rejects_market_and_outcome_fields_even_null(tmp_path, field, nested):
    capture(tmp_path)
    with pytest.raises(ProspectiveEvidenceError):
        snapshot(tmp_path, **({"projection_inputs": {"nested": [{field: None}]}} if nested else {field: None}))


def test_model_hash_inputs_metadata_and_versions(tmp_path):
    capture(tmp_path)
    row = snapshot(tmp_path)
    assert snapshot(tmp_path) == row
    for update in (dict(projected_minutes=19), dict(projected_points=13), dict(model_version="fixture-v2"),
                   dict(projection_inputs={"recent_games": 4})):
        changed = snapshot(tmp_path, **update)
        assert changed["model_snapshot_id"] != row["model_snapshot_id"]
        assert changed["row_sha256"] != row["row_sha256"]
    assert snapshot(tmp_path, meta=replace(metadata(), prediction_run_id="synthetic-run-2"))["row_sha256"] != row["row_sha256"]


def test_positive_multirow_freeze_and_exact_retry(tmp_path):
    capture(tmp_path)
    capture(tmp_path, request("stats-2", "player-2"))
    sources = source_manifest(tmp_path / "journal", ["stats-2", "stats-1"])
    rows = []
    for pid, cid in [("player-2", "stats-2"), ("player-1", "stats-1")]:
        row = snapshot(tmp_path, player=pid, capture_id=cid)
        # Construct against the shared, verified cohort source manifest.
        state = {k: v for k, v in row.items() if k in _STATE_FIELDS}
        rows.append(model_snapshot(metadata(), source_manifest_sha256=digest(sources), **state))
    root = freeze(tmp_path, rows=rows, request_ids=["stats-1", "stats-2"])
    saved = verify(tmp_path, root)
    assert saved.manifest["row_count"] == 2 and len(saved.rows) == 2
    assert [r["player_id"] for r in saved.rows] == ["player-1", "player-2"]
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    assert freeze(tmp_path, rows=list(reversed(rows)), request_ids=["stats-2", "stats-1"]) == root
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}
    with pytest.raises(TypeError):
        saved.rows[0]["measurement_metadata"]["research_only"] = False


def test_zero_row_freeze_with_source_and_exclusion_evidence(tmp_path):
    capture(tmp_path)
    root = freeze(tmp_path, rows=[], exclusions=[{"canonical_event_id": "event-1", "reason": "MODEL_STATE_INCOMPLETE"}])
    saved = verify(tmp_path, root)
    assert saved.manifest["row_count"] == 0 and saved.rows == ()
    assert json.loads((root / "exclusions.json").read_bytes())["rows"][0]["reason"] == "MODEL_STATE_INCOMPLETE"
    assert saved.manifest["measurement_metadata"]["research_only"] is True


def test_unavailable_future_model_values_remain_incomplete(tmp_path):
    capture(tmp_path)
    row = snapshot(tmp_path, projected_minutes=None, minutes_evidence_ref=None, projected_points=None,
        projection_evidence_ref=None, projection_method=None, projection_cutoff_utc=None, projection_timestamp_utc=None)
    assert row["model_state_status"] == "MODEL_STATE_INCOMPLETE"
    assert row["distribution_parameters"] is None
    assert verify(tmp_path, freeze(tmp_path, rows=[row])).rows[0]["projected_points"] is None


@pytest.mark.parametrize("name", ["model_snapshots.jsonl", "sources.json", "exclusions.json", "manifest.json", "freeze.json"])
def test_each_freeze_artifact_tamper_fails(tmp_path, name):
    root = freeze(tmp_path)
    path = root / name
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ProspectiveEvidenceError):
        verify(tmp_path, root)


def test_same_run_conflict_missing_receipt_and_declared_repo(tmp_path):
    root = freeze(tmp_path)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    with pytest.raises(ProspectiveEvidenceError, match="conflicting"):
        freeze(tmp_path, rows=[snapshot(tmp_path, projected_points=13)])
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}
    with pytest.raises(ProspectiveEvidenceError, match="repository"):
        verify_model_freeze(root, expected_repository_sha="a" * 40, evidence_root=tmp_path / "journal")
    (root / "freeze.json").unlink()
    with pytest.raises(ProspectiveEvidenceError):
        freeze(tmp_path, rows=[snapshot(tmp_path)])


@pytest.mark.parametrize("clock_values", [[CREATED, datetime(2026, 10, 7, 23, tzinfo=timezone.utc)],
    [datetime(2026, 10, 7, 23, tzinfo=timezone.utc)], [CREATED, datetime(2026, 10, 7, 19, tzinfo=timezone.utc)]])
def test_freeze_clock_guards(tmp_path, clock_values):
    capture(tmp_path)
    clocks = iter(clock_values)
    with pytest.raises(ProspectiveEvidenceError):
        freeze(tmp_path, rows=[snapshot(tmp_path)], clock=lambda: next(clocks))
    assert not (tmp_path / "articles" / FREEZE_SCHEMA / metadata().prediction_run_id / "freeze.json").exists()


def market(tmp_path, root):
    saved = verify(tmp_path, root)
    record = capture(tmp_path, request("market-1", source_role="market", provider="synthetic_market", endpoint="/market"),
        requested_at_utc="2026-10-07T20:01:00Z", responded_at_utc="2026-10-07T20:01:01Z",
        raw_body=b'{"bookmaker":"synthetic-book","line":12.5,"decimal_odds":1.9}')
    return dict(schema_version=MARKET_SCHEMA, freeze_manifest_sha256=saved.manifest["manifest_sha256"],
        model_snapshot_id=saved.rows[0]["model_snapshot_id"], canonical_event_id="event-1", player_id="player-1",
        bookmaker="synthetic-book", line=12.5, decimal_odds=1.9, observed_at_utc="2026-10-07T20:01:02Z",
        raw_market_evidence_ref={"request_id": "market-1", "raw_body_sha256": record.manifest["raw_body_sha256"]})


def bind(tmp_path, root, observation):
    return bind_market_observation(root, expected_repository_sha=SHA, evidence_root=tmp_path / "journal", observation=observation)


def test_market_changes_assessment_only_without_mutating_freeze(tmp_path):
    root = freeze(tmp_path)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    observation = market(tmp_path, root)
    first = bind(tmp_path, root, observation)
    second = bind(tmp_path, root, {**observation, "line":13.5})
    assert first["assessment_id"] != second["assessment_id"]
    for key in ("model_snapshot_id", "row_sha256", "freeze_manifest_sha256"):
        assert first[key] == second[key]
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}


@pytest.mark.parametrize("update", [dict(observed_at_utc="2026-10-07T20:00:01Z"),
    dict(observed_at_utc="2026-10-07T23:00:00Z"), dict(player_id="other"), dict(line=float("nan")),
    dict(decimal_odds=1), dict(freeze_manifest_sha256="0" * 64), dict(model_snapshot_id="0" * 64)])
def test_market_contract_time_identity_and_price_guards(tmp_path, update):
    root = freeze(tmp_path)
    with pytest.raises(ProspectiveEvidenceError):
        bind(tmp_path, root, {**market(tmp_path, root), **update})


def test_pre_freeze_market_capture_and_market_as_model_source_rejected(tmp_path):
    root = freeze(tmp_path)
    early = capture(tmp_path, request("early-market", source_role="market"))
    observation = market(tmp_path, root)
    observation["raw_market_evidence_ref"] = {"request_id": "early-market", "raw_body_sha256": early.manifest["raw_body_sha256"]}
    with pytest.raises(ProspectiveEvidenceError):
        bind(tmp_path, root, observation)
    with pytest.raises(ProspectiveEvidenceError, match="market evidence"):
        source_manifest(tmp_path / "journal", ["early-market"])


def test_freeze_without_credentials_and_no_environment_reads(tmp_path, monkeypatch):
    for name in ("THE_ODDS_API_KEY", "BALLDONTLIE_API_KEY", "API_NBA_KEY"):
        monkeypatch.delenv(name, raising=False)
    import os
    def forbidden(*args, **kwargs):
        raise AssertionError("freeze may not read environment/credentials")
    capture(tmp_path)
    row = snapshot(tmp_path)
    monkeypatch.setattr(os, "getenv", forbidden)
    original = type(os.environ).__getitem__
    def guarded(self, key):
        if any(part in key.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")):
            raise AssertionError("credential read is forbidden")
        return original(self, key)
    monkeypatch.setattr(type(os.environ), "__getitem__", guarded)
    assert verify(tmp_path, freeze(tmp_path, rows=[row])).manifest["row_count"] == 1


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_real_symlink_paths_fail_closed(tmp_path, kind):
    capture(tmp_path)
    if kind == "file":
        original = tmp_path / "journal" / EVIDENCE_SCHEMA / "stats-1" / "body.bin"
        other = tmp_path / "body-copy.bin"
        other.write_bytes(original.read_bytes())
        original.unlink()
        original.symlink_to(other)
        with pytest.raises(ProspectiveEvidenceError, match="link/reparse"):
            verify_capture(tmp_path / "journal", "stats-1")
    else:
        alias = tmp_path / "journal-alias"
        alias.symlink_to(tmp_path / "journal", target_is_directory=True)
        with pytest.raises(ProspectiveEvidenceError, match="link/reparse"):
            verify_capture(alias, "stats-1")


def test_interrupted_capture_and_writer_failure_retained(tmp_path, monkeypatch):
    import courtvision.sports.nba.prospective_evidence as evidence
    real_write = evidence.write_once
    def interrupted(path, raw):
        if path.name == "manifest.json":
            raise OSError("simulated manifest failure")
        return real_write(path, raw)
    monkeypatch.setattr(evidence, "write_once", interrupted)
    with pytest.raises(OSError):
        capture(tmp_path)
    assert (tmp_path / "journal" / EVIDENCE_SCHEMA / "stats-1" / "body.bin").exists()
    monkeypatch.setattr(evidence, "write_once", real_write)
    with pytest.raises(ProspectiveEvidenceError):
        capture(tmp_path)


def test_duplicate_rows_and_bad_evidence_ref_before_publication(tmp_path):
    capture(tmp_path)
    row = snapshot(tmp_path)
    with pytest.raises(ProspectiveEvidenceError, match="duplicate"):
        freeze(tmp_path, rows=[row, row])
    wrong = snapshot(tmp_path, player="other-player")
    with pytest.raises(ProspectiveEvidenceError, match="canonical player/event"):
        freeze(tmp_path, rows=[wrong])
    assert not (tmp_path / "articles").exists()


@pytest.mark.parametrize("params", [{"query": '{"api_key":"hidden"}'}, {"query": "api_key=hidden"},
    {"headers": {"Authorization": "Bearer hidden"}, "query": "Bearer hidden"}])
def test_secret_values_cannot_be_hidden_in_parameter_text(params):
    with pytest.raises(ProspectiveEvidenceError):
        request(parameters=params)


@pytest.mark.parametrize("endpoint", ["/games?api_key=hidden", "/games/../key/hidden", "/api_key/hidden",
    "https://user:secret@example.invalid/games", "/games%3Fapi_key%3Dhidden"])
def test_endpoint_credentials_and_queries_rejected(endpoint):
    with pytest.raises(ProspectiveEvidenceError):
        request(endpoint=endpoint)


@pytest.mark.parametrize("field", ["line", "actual_minutes", "model_over_probability"])
def test_factual_capture_metadata_cannot_smuggle_market_outcomes_into_sources(tmp_path, field):
    capture(tmp_path, request(parameters={"player_id": "player-1", field: None}))
    row = snapshot(tmp_path)
    with pytest.raises(ProspectiveEvidenceError):
        freeze(tmp_path, rows=[row])
    assert not (tmp_path / "articles").exists()


@pytest.mark.parametrize("field,value", [("points", 12), ("minutes", 18), ("kelly_eligible", True),
    ("eligible_for_betting", True), ("eligible_for_official_pick", True)])
def test_unscoped_actual_aliases_and_nested_economic_flags_rejected(tmp_path, field, value):
    capture(tmp_path)
    with pytest.raises(ProspectiveEvidenceError):
        snapshot(tmp_path, projection_inputs={field: value})


def test_disk_verifier_rejects_resigned_policy_promotion(tmp_path):
    root = freeze(tmp_path)
    def promote(manifest):
        manifest["measurement_metadata"]["season_phase"] = "REGULAR_SEASON"
        manifest["manifest_sha256"] = digest({k: v for k, v in manifest.items() if k != "manifest_sha256"})
    mutate_json(root / "manifest.json", promote)
    with pytest.raises(ProspectiveEvidenceError, match="promoted"):
        verify(tmp_path, root)


def test_freeze_source_raw_tamper_and_journal_alias_fail_verification(tmp_path):
    root = freeze(tmp_path)
    body = tmp_path / "journal" / EVIDENCE_SCHEMA / "stats-1" / "body.bin"
    body.write_bytes(body.read_bytes().replace(b'12', b'13'))
    with pytest.raises(ProspectiveEvidenceError):
        verify(tmp_path, root)


def test_directory_claim_race_cannot_overwrite(tmp_path, monkeypatch):
    from pathlib import Path
    capture(tmp_path)
    row = snapshot(tmp_path)
    real_mkdir = Path.mkdir
    def rival_claim(self, *args, **kwargs):
        if self.name == metadata().prediction_run_id:
            real_mkdir(self, *args, **kwargs)
            (self / "rival.txt").write_bytes(b'preserve')
            raise FileExistsError("simulated concurrent claim")
        return real_mkdir(self, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", rival_claim)
    with pytest.raises(ProspectiveEvidenceError, match="concurrently"):
        freeze(tmp_path, rows=[row])
    root = tmp_path / "articles" / FREEZE_SCHEMA / metadata().prediction_run_id
    assert {p.name for p in root.iterdir()} == {"rival.txt"}
    assert (root / "rival.txt").read_bytes() == b'preserve'


def test_fsync_failure_never_publishes_verified_capture(tmp_path, monkeypatch):
    import os
    def fail(_):
        raise OSError("simulated durability failure")
    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="durability"):
        capture(tmp_path)
    with pytest.raises(ProspectiveEvidenceError):
        verify_capture(tmp_path / "journal", "stats-1")


def test_source_manifest_uses_verified_metadata_without_second_read(tmp_path, monkeypatch):
    import courtvision.sports.nba.prospective_evidence as evidence
    capture(tmp_path)
    original = evidence.read_document
    reads = []
    def single_read(path):
        reads.append(path)
        if len(reads) > 1:
            raise AssertionError("unverified reread")
        return original(path)
    monkeypatch.setattr(evidence, "read_document", single_read)
    sources = evidence.source_manifest(tmp_path / "journal", ["stats-1"])
    assert len(reads) == 1 and sources["stats-1"]["request_id"] == "stats-1"


def test_source_evidence_change_changes_model_identity(tmp_path):
    capture(tmp_path)
    original = snapshot(tmp_path)
    capture(tmp_path, request("stats-new"), raw_body=b'{"response":[{"historical_points":13}]}')
    sources = source_manifest(tmp_path / "journal", ["stats-1", "stats-new"])
    state = {k: v for k, v in original.items() if k in _STATE_FIELDS}
    changed = model_snapshot(metadata(), source_manifest_sha256=digest(sources), **state)
    assert changed["model_snapshot_id"] != original["model_snapshot_id"]
    assert changed["row_sha256"] != original["row_sha256"]
